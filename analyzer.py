"""GRG-4372u MP full-log analyzer. All report numbers originate in supplied ZIP.

The approved V2.6.1 workbook defines the 32-sheet reporting schema, not a data
source. Sections that cannot be proven from the current LOG are marked REVIEW.
"""
from __future__ import annotations
import collections, csv, datetime as dt, hashlib, io, json, math, os, re, statistics, sys, zipfile
from pathlib import Path
from legacy_audit import stage_for, decode, parse_rf, extract_bob, PCB_RE, mac_norm, explicit_file_status

VERSION='1.2.0'
STATIONS=['BOB調試','BOB測試','RF板測','資訊寫入','資訊核對','耦合','流量測試']
RF_TOTAL=re.compile(r'TotalTime\s*:\s*(\d+(?:\.\d+)?)\s*s',re.I)
OTHER_TOTAL=re.compile(r'(?:Total\s*Time|總測試時間|总测试时间)\s*[:：]\s*(\d+(?:\.\d+)?)\s*s',re.I)
DATE_PATTERNS=[(re.compile(r'(20\d{6})(\d{6})(?=[^0-9]|$)'),'%Y%m%d%H%M%S'),(re.compile(r'(20\d{6})[_T](\d{6})'),'%Y%m%d%H%M%S'),(re.compile(r'(20\d{2}-\d{2}-\d{2})T(\d{6})'),'%Y-%m-%d%H%M%S')]
COUPLE=re.compile(r'天线\s*[-－]?\s*(\d).*?线损补偿\s*[=:：]\s*([-+\d.]+)\s*dBm.*?实际功率\s*[=:：]\s*([-+\d.]+)\s*dBm.*?标准\s*\(\s*([-+\d.]+)\s*dBm\s*[~～]\s*([-+\d.]+)\s*dBm',re.I)
SENSITIVE=re.compile(r'(?i)(password\s*[:=]|passwd|wifipwd|rootpwd|userpwd|cookie|token\s*[:=])')

def timestamp(name):
    for pat,fmt in DATE_PATTERNS:
        m=pat.search(name)
        if m:
            try:return dt.datetime.strptime(''.join(m.groups()),fmt)
            except ValueError:pass
    return None

def result(path,text):
    # Explicit test-result field inside LOG takes precedence over folder/filename.
    m=re.search(r'Total Result\s*:\s*(PASS|FAIL)',text,re.I)
    if m:return m.group(1).upper()
    lo=Path(path).name.lower()
    if '/fail/' in path.lower() or re.search(r'(^|[_-])fail([_-]|\d)',lo):return 'FAIL'
    if '/pass/' in path.lower() or '/success/' in path.lower() or re.search(r'(^|[_-])pass([_-]|\d)',lo):return 'PASS'
    return 'UNKNOWN'

def id_from_name(name,stage):
    if stage=='RF板測':
        m=PCB_RE.search(name);return m.group().upper() if m else ''
    # Standalone MAC, including 434D5444 GPON prefix which must not be treated as MAC.
    m=re.search(r'(?i)(?:^|[_/])([0-9a-f]{12})(?:[_./-]|$)',name)
    return m.group(1).upper() if m else mac_norm(name)

def rf_csv_rows(raw,filename,acc,problems):
    """Read CSV limits from current MP file, never from old PDF/rules.json."""
    try:
        stream=io.StringIO(decode(raw,filename));reader=csv.DictReader(stream)
        if not {'TestName','LowerLimit','UpperLimit','TestValue','TestStatus'}.issubset(reader.fieldnames or []):
            problems.append([filename,'RF CSV 欄位不完整']);return 0
        seen=collections.Counter(); count=0
        for r in reader:
            count+=1
            label=r['TestName'].strip();band=(r.get('Band') or '').strip();freq=(r.get('FreqMHz') or '').strip();ant=(r.get('Antenna') or '').strip();unit=(r.get('Unit') or '').strip()
            base=(band,label,freq,ant,unit);seen[base]+=1; key=base+(seen[base],)
            try:low=float(r['LowerLimit']);high=float(r['UpperLimit']);value=float(r['TestValue']);valid=all(map(math.isfinite,[low,high,value])) and low<=high
            except (ValueError,TypeError):low=high=value=None;valid=False
            status=(r.get('TestStatus') or '').strip().upper();numeric=('PASS' if low<=value<=high else 'FAIL') if valid else '無效/缺限值'
            bucket=acc.setdefault(key,dict(n=0,passed=0,failed=0,mismatch=0,invalid=0,values=[],limits=collections.Counter(),source=filename))
            bucket['n']+=1;bucket['passed']+=status=='PASS';bucket['failed']+=status=='FAIL';bucket['invalid']+=not valid;bucket['mismatch']+=valid and status in ('PASS','FAIL') and numeric!=status
            if valid:
                bucket['values'].append(value);bucket['limits'][(low,high)]+=1
        return count
    except Exception as e:problems.append([filename,'CSV讀取失敗 '+str(e)]);return 0

def load_events(zip_path, rules):
    events=[];rf_measures=[];bob_measures=[];rf_acc={};csv_acc={};problems=[];coupling=[];traffic=[];raw_csv_count=0;csv_rows=0
    seen_files=set(); zip_entries=0; paired=collections.Counter();
    with zipfile.ZipFile(zip_path) as z:
      for item in z.infolist():
        if item.is_dir():continue
        zip_entries+=1;name=item.filename.replace('\\','/');lower=name.lower();ext=Path(name).suffix.lower()
        if ext not in ('.txt','.log','.csv'):continue
        if item.file_size>20_000_000:problems.append([name,'單檔超過20MB安全上限']);continue
        if name in seen_files:problems.append([name,'ZIP內重複路徑']);continue
        seen_files.add(name)
        try:
            raw=z.read(item)
            if ext=='.csv':
                raw_csv_count+=1
                csv_rows+=rf_csv_rows(raw,name,csv_acc,problems)
                continue
            # CommandInfo vs DebugInfo are one test; exclude debugging text as event.
            if re.search(r'(?i)_(?:debuginfo|comma(?:nd)?detail)\.txt$',name):
                paired['debug_info']+=1;continue
            t=decode(raw,name)
            station=stage_for(name,t)
            if station=='其他':problems.append([name,'未辨識測試站，未依資料夾強行假定']);continue
            if ext=='.log' and station!='RF板測' and 'TeleATT' not in t and 'Total Result' not in t:
                problems.append([name,'無法判定的LOG內容']);continue
            basename=Path(name).name
            key=id_from_name(basename,station)
            if not key:
                p=PCB_RE.search(t[:12000]) if station=='RF板測' else None
                key=p.group().upper() if p else (mac_norm(t[:6000]) if station!='RF板測' else '')
            status=result(name,t)
            time=timestamp(basename)
            if status=='UNKNOWN':problems.append([name,'缺少可靠 PASS/FAIL']);
            if not key:problems.append([name,'缺少設備識別碼（不計入唯一機台分母）']);
            total_match=(RF_TOTAL.search(t) if station=='RF板測' else OTHER_TOTAL.search(t))
            duration=float(total_match.group(1)) if total_match else None
            if station=='RF板測':
                data=parse_rf(t,basename,rules);rf_measures+=data
                for r in data:
                    if not all(math.isfinite(r[n]) for n in ('low','high','value')) or r['low']>r['high']:
                        problems.append([name,f'RF LOG invalid limits line {r["line"]}']);continue
                    rf_key=(r['band'],r['stage_raw']+' / '+r['metric']+' / '+r['phy']+' / '+r['rate'],str(r['mhz']),'',r['ant'],r['unit'])
                    bucket=rf_acc.setdefault(rf_key,dict(n=0,passed=0,failed=0,mismatch=0,invalid=0,values=[],limits=collections.Counter(),source=name))
                    bucket['n']+=1;bucket['passed']+=r['log_result']=='PASS';bucket['failed']+=r['log_result']=='FAIL'
                    bucket['values'].append(r['value']);bucket['limits'][(r['low'],r['high'])]+=1
                    numeric='PASS' if r['low']<=r['value']<=r['high'] else 'FAIL'
                    bucket['mismatch']+=numeric!=r['log_result']
            if station.startswith('BOB'):
                bob_measures.extend(extract_bob(t,basename,station,rules))
            if station=='耦合':
                # Band contextual detection from nearby command is not always available.
                for lineno,line in enumerate(t.splitlines(),1):
                    m=COUPLE.search(line)
                    if not m:continue
                    ant,loss,power,low,high=m.groups();v=float(power);lo=float(low);hi=float(high)
                    coupling.append(dict(key=key,source=name,status=status,ant='ANT'+ant,loss=float(loss),power=v,low=lo,high=hi,judge='PASS' if lo<=v<=hi else 'FAIL',lineno=lineno,band='待依上下文確認'))
            if station=='流量測試':
                # Per-port rows are parsed only if explicit tabular headers are recognized.
                for line in t.splitlines():
                    m=re.match(r'\s*(\d+\([^\r\n)]*\))\s+(Speed_[\w]+)\s+(Full|Half)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)',line,re.I)
                    if m:traffic.append(dict(key=key,status=status,port=m[1],speed=m[2],duplex=m[3],frame=m[4],rate=m[5],tx=m[6],rx=m[7],lost=m[8],fcs=m[9],source=name))
            # Identify MES duplicate blocking separately from true test failure.
            mes=bool(re.search(r'不允许重复过站|不允許重複過站|重复过站|重複過站|(?<![A-Za-z])MES(?![A-Za-z])[^\r\n]{0,80}(?:FAIL|ERROR|NG|拒绝|拒絕)|(?:FAIL|ERROR|NG)[^\r\n]{0,80}(?<![A-Za-z])MES(?![A-Za-z])',t,re.I))
            events.append(dict(station=station,key=key,status=status,time=time,name=basename,path=name,duration=duration,mes=mes))
        except Exception as e:problems.append([name,'LOG處理錯誤: '+str(e)[:160]])
    return locals()

def prepare_tables(data):
    events=data['events'];problems=data['problems'];rf=data['rf_measures'];bob=data['bob_measures'];rf_acc=data['rf_acc'];coupling=data['coupling'];traffic=data['traffic']
    tables=collections.defaultdict(list)
    by_station=collections.defaultdict(list);by_key=collections.defaultdict(list)
    for e in events:
        by_station[e['station']].append(e)
        if e['key']:by_key[(e['station'],e['key'])].append(e)
    # Keep all LOG events for audit, but never classify an MES-only block as
    # a DUT functional failure. The function-test sequence is MES-filtered.
    units={}
    for k,ee in by_key.items():
        ee.sort(key=lambda e:(e['time'] is None,e['time'] or dt.datetime.max,e['name']))
        functional=[e for e in ee if not e['mes'] and e['status'] in ('PASS','FAIL','ABORT')]
        first= functional[0] if functional else None
        last= functional[-1] if functional else None
        firstbad=bool(first and first['status'] in ('FAIL','ABORT'))
        lastbad=bool(last and last['status'] in ('FAIL','ABORT'))
        recovered=bool(firstbad and last and last['status']=='PASS')
        units[k]=dict(ee=ee,functional=functional,first=first,last=last,recovered=recovered,
            fail=sum(e['status']=='FAIL' for e in functional),abort=sum(e['status']=='ABORT' for e in functional),
            mes=sum(e['mes'] for e in ee),firstbad=firstbad,lastbad=lastbad,
            retest=max(0,len(functional)-1),firstfailed_retested=bool(firstbad and len(functional)>1))

    def priority(x):
        seq=x['functional']
        if x['lastbad']:return 0  # open failure / PASS->FAIL
        if x['recovered']:return 1 # FAIL->PASS
        if len(seq)>1:return 2     # PASS->PASS
        if x['mes']:return 3       # MES-only/blocked visibility
        return 4                  # first-pass good

    for st in STATIONS:
        ev=by_station[st]
        uu=sorted([(key,x) for (station,key),x in units.items() if station==st],key=lambda pair:(priority(pair[1]),pair[0]))
        valid=[x for _,x in uu if x['functional']]
        denominator=len(valid)
        firstbad=sum(x['firstbad'] for x in valid)
        finalbad=sum(x['lastbad'] for x in valid)
        failed_retested=sum(x['firstfailed_retested'] for x in valid)
        recovered=sum(x['recovered'] for x in valid)
        retested=sum(x['retest']>0 for x in valid)
        functional_fail=sum(x['fail'] for x in valid)
        functional_abort=sum(x['abort'] for x in valid)
        functional_pass=sum(sum(e['status']=='PASS' for e in x['functional']) for x in valid)
        pct=lambda n,d:round(n/d*100,2) if d else None
        tables['01_各站統計'].append([st,'有 LOG' if ev else 'NO LOG',len(ev),denominator,
            functional_pass,functional_fail,functional_abort,firstbad,failed_retested,recovered,finalbad,
            sum(x['mes'] for _,x in uu),pct(firstbad,denominator),pct(finalbad,denominator),
            retested,pct(retested,denominator),pct(recovered,failed_retested),
            sum(x['retest'] for x in valid),sum(1 for _,x in uu if x['mes']),
            sum(1 for _,x in uu if not x['functional'])])
        for key,x in uu:
            ee=x['ee'];f=x['first'];l=x['last'];first_label=f['status'] if f else 'MES ONLY/UNKNOWN';last_label=l['status'] if l else 'MES ONLY/UNKNOWN'
            if x['lastbad']:kind='最終FAIL／待結案'
            elif x['recovered']:kind='FAIL→PASS'
            elif x['retest']:kind='PASS→PASS／其他重測'
            elif x['mes']:kind='MES異常（不計功能FAIL）'
            elif f:kind='首測PASS'
            else:kind='無有效功能判定'
            first_time=ee[0]['time'] if ee else None;last_time=ee[-1]['time'] if ee else None
            row=[st,key,len(ee),first_label,last_label,x['fail'],x['abort'],x['retest'],
                '是' if x['recovered'] else '否',x['mes'],str(first_time) if first_time else '',
                str(last_time) if last_time else '',kind,'；'.join(e['name'] for e in ee)[:1200],
                priority(x),len(x['functional'])]
            tables['02_逐台最終狀態'].append(row)
            sn=STATIONS.index(st)+3;tables[f'{sn:02d}_{st}'].append(row)
            if len(ee)>1 or x['fail'] or x['abort'] or x['mes']:
                for j,e in enumerate(ee,1):
                    tables['14_測試歷程與重測'].append([st,key,j,str(e['time']) if e['time'] else '',e['status'],e['name'],e['path'],
                        'MES擋站／排除功能FAIL' if e['mes'] else '功能測試'])
        # All unkeyed events remain visible in source and data-quality sheets.
        for e in ev:
            if e['mes']:
                tables['18_資料品質問題'].append([e['path'],'MES擋站獨立事件；不列入功能不良率'])
    # Sort all-machine overview by status priority, then station/key.
    tables['02_逐台最終狀態'].sort(key=lambda r:(r[14], STATIONS.index(r[0]),r[1]))
    # Drop internal helper priority columns; report remains backward compatible.
    for n in ['02_逐台最終狀態']+[f'{i+3:02d}_{st}' for i,st in enumerate(STATIONS)]:
        tables[n]=[r[:14] for r in tables[n]]
    for e in events:tables['19_來源LOG索引'].append([e['station'],e['key'],str(e['time']) if e['time'] else '',e['status'],e['name'],e['path']])
    # LOG is the sole authoritative value/limit/status source; CSV is auxiliary.
    rf_roll=collections.defaultdict(lambda:collections.Counter())
    special=[];limitdiff=0;csv_mismatch=0
    csv_ref_count=sum(v['n'] for v in data['csv_acc'].values())
    for key,stat in rf_acc.items():
        band,label,freq,occ,ant,unit=key;limits=stat['limits'];n=stat['n'];lim=limits.most_common(1)[0][0] if limits else (None,None)
        if len(limits)>1:special.append([band,label,freq,ant,occ,str(list(limits.items())[:8]),stat['source']]);limitdiff+=1
        csv_mismatch+=stat['mismatch'];family=re.match(r'([^\[]+)',label).group(1) if label else ''
        stage='RX' if family.upper().startswith('PER') else 'TX/Calibration'
        group=(band,stage,family)
        rf_roll[group]['n']+=n;rf_roll[group]['pass']+=stat['passed'];rf_roll[group]['fail']+=stat['failed']
        tables['21_RF_LOG規格主表'].append([band,label,freq,ant,unit,occ,lim[0],lim[1],n,stat['passed'],stat['failed'],stat['mismatch'],stat['invalid'],round(min(stat['values']),6) if stat['values'] else None,round(max(stat['values']),6) if stat['values'] else None,stat['source'],'規格不一致／待確認' if len(limits)>1 else 'MP LOG 規格已列示'])
    for (band,stage,metric),v in sorted(rf_roll.items()):
        tables['11_RF測項統計'].append([band,stage,'LOG實際測項',metric,v['n'],v['pass'],v['fail'],round(v['fail']/v['n'],7) if v['n'] else None,'當批MP RF LOG量測行上下限'])
    tables['23_RF特殊規格與判定']=special or [['無','同測項同序號未發現多個限值']]
    rf_inner=collections.defaultdict(list)
    for r in rf:
        if r['log_result']=='FAIL':
            tables['12_RF_FAIL逐項'].append([r['source'],r['band'],r['mhz'],r['ant'],r['stage_raw']+' / '+r['metric']+' / '+r['rate'],r['low'],r['high'],r['value'],r['unit'],'FAIL (LOG)'])
        if r['log_result']=='FAIL' and r['source'].upper().endswith('PASS.LOG'):rf_inner[(r['pcb'],r['source'])].append(r)
    for (pcb,source),rows in rf_inner.items():tables['17_RF檔案內FAIL'].append([pcb,source,len(rows),sum('初測FAIL→' in (x.get('retest') or '') for x in rows)])
    for b in bob:
        tables['13_BOB量測重判'].append([b['stage'],b['mac'],b['source'],b['metric'],b['value'],b['unit'],b['pdf_low'],b['pdf_high'],b['pdf_status']])
    for x in coupling:
        tables['25_耦合逐筆重判'].append([x['key'],x['status'],x['band'],x['ant'],x['loss'],x['power'],x['low'],x['high'],x['judge'],'',x['lineno'],x['source']])
    couple_group=collections.defaultdict(list)
    for x in coupling:couple_group[(x['ant'],x['low'],x['high'])].append(x)
    for (ant,low,high),rows in couple_group.items():tables['24_耦合LOG規格統計'].append(['耦合',ant+' Power',low,high,'dBm',len(rows),sum(r['judge']=='PASS' for r in rows),sum(r['judge']=='FAIL' for r in rows),1,'一致','本次MP CommandInfo 標準(...)'])
    for t in traffic:tables['28_流量逐埠逐流'].append([t['key'],t['status'],'Port',t['port'],t['speed'],t['duplex'],t['frame'],t['rate'],t['tx'],t['rx'],t['lost'],t['fcs'],'','','','','','待確認',t['source']])
    for p in problems:tables['18_資料品質問題'].append(p)
    # Real total times; BOB inter-PASS intervals are takt proxy, never single DUT duration.
    durations={}
    for st in STATIONS:
        ee=[e for e in by_station[st] if e['status']=='PASS'];real=[e['duration'] for e in ee if e['duration'] is not None and 0<e['duration']<100000]
        if real:values=real;method='LOG 原始 TotalTime／Total Time（單次實測）'
        elif st.startswith('BOB'):
            times=sorted(e['time'] for e in ee if e['time']);values=[(b-a).total_seconds() for a,b in zip(times,times[1:]) if 10<=(b-a).total_seconds()<=300];method='相鄰PASS完成時間差（推估節拍，非單台工時）'
        else:values=[];method='NO LOG／無可靠單次工時，不以檔案間隔冒充'
        durations[st]=values
        tables['15_工時_PASS統計'].append([st,len(values),round(min(values),3) if values else None,round(max(values),3) if values else None,round(statistics.mean(values),3) if values else None,round(statistics.median(values),3) if values else None,method])
        if real:
            median=statistics.median(values)
            for e in ee:
                if e['duration'] is not None and median and e['duration']>2*median:
                    multiple=e['duration']/median
                    tables['16_工時偏長追蹤'].append([st,e['key'],e['name'],round(e['duration'],3),round(median,3),round(multiple,2),'高優先 >3×中位數' if multiple>3 else '偏長 >2×中位數',e['path']])
    tables['10_測試規格比對']=[['BOB測試','TX Power',1.5,3.5,'dBm','MP 已確認設定','待核對規格文件'],['BOB測試','ER',11.5,13.5,'dB','MP 已確認設定','待核對規格文件'],['RF板測','所有LOG測項','','','原檔單位','本批MP LOG量測上下限','LOG限值作為首要判定依據'],['耦合','Power','','','dBm','本批MP CommandInfo 標準(...)','逐項依LOG數值判定']]
    tables['22_RF_LOG判定查核']=[['正式規格來源','本次 MP RF LOG','LOG原始量測行[Low]<[Value]<[High]，不受CSV覆蓋'],['RF CSV 參考檔案數',data['raw_csv_count'],'CSV非事件且不覆蓋LOG規格'],['CSV參考測項總數',data['csv_rows'],'只供輔助稽核'],['LOG規格組數',len(rf_acc),'依Band/Stage/PHY/Rate/Freq/ANT/Metric分組'],['同測項不同限值',limitdiff,'須人工覆核，不能任意取首筆'],['LOG數值判定與原Status不一致',csv_mismatch,'限值兩端含邊界'],['RF LOG量測筆數',len(rf),'正式測試值及限值均由LOG解析']]
    tables['20_統計定義']=[['測試事件','RF一份LOG為1事件；CSV不重複計數','CommandInfo/DebugInfo不重複'],['唯一識別碼','RF採PCB Seq；其他站採MAC','跨站PCB→MAC不得猜測'],['功能重測次數','同站同識別碼非MES有效功能 LOG 數 - 1','PASS→PASS納入重測率，但不納入不良率'],['首測功能不良率','首筆有效功能FAIL/ABORT台數／有效功能測試唯一識別碼數','MES擋站不計入分子分母'],['最後功能不良率','末筆有效功能FAIL/ABORT台數／有效功能測試唯一識別碼數','MES事件不作功能末測'],['功能重測率','同站功能有效測試次數>1之識別碼數／有效功能測試識別碼數','MES-only不納入功能重測'],['MES擋站','獨立統計MES LOG事件及機台','不得計入功能FAIL'],['MP RF規格','本批LOG Low/High/Value正式判定；CSV只供稽核','不能用CSV或過期PDF覆蓋LOG規格'],['工時來源','RF優先 TotalTime；BOB以完成間隔推估節拍','推估時間不可寫為單次實測']]
    tables['30_規格稽核與判定說明']=[['MP RF Calibration／Verification','本批LOG提取',f'{len(rf)}筆LOG量測／{len(rf_acc)}個規格組','CSV僅輔助交叉核對'],['RF LOG限值一致性','需覆核' if limitdiff else '相同條件限值一致',f'{limitdiff}組出現不同上下限','同一測項按第N筆分組'],['RF LOG數值查核','需覆核' if csv_mismatch else '數值判定一致',f'{csv_mismatch}筆與LOG狀態不同','邊界含等號'],['耦合原始功率','本批LOG解析',f'{len(coupling)}筆','不同頻段需人工核對上下文'],['流量逐埠明細','部分支援' if traffic else '待確認',f'{len(traffic)}筆成功解析','未有明確規格不可臆造限值'],['資料品質異常','待追蹤',f'{len(problems)}筆','詳18_資料品質問題']]
    summary=[['分析版本',VERSION,'動態重算；非複製核准Excel'],['分析檔案',Path(data['zip_path']).name,'只分析本次輸入ZIP'],['ZIP原始檔案數',data['zip_entries'],'包含輔助CSV/DebugInfo'],['有效測試事件數',len(events),'RF LOG一份為一次測試'],['RF CSV輔助檔數',data['raw_csv_count'],'不計事件、不參與主判定'],['RF CSV測項列數',data['csv_rows'],'僅供交叉核對，非正式量測統計'],['RF LOG量測筆數',len(rf),'CSV與LOG分開'],['RF LOG不同限值組',limitdiff,'同條件規格差異需人工覆核'],['RF LOG數值判定差異',csv_mismatch,'比對Low/High與TestStatus'],['BOB量測筆數',len(bob),'區分調試/成品測試'],['耦合量測筆數',len(coupling),'規格取當批CommandInfo'],['可辨識流量測項筆數',len(traffic),'LOG格式支援限制見稽核頁'],['未辨識/異常',len(problems),'不能默認PASS'],['跨站可放行結論','待MES關聯確認','RF PCB Seq與其他站MAC跨站未證明完整映射']]
    for row in tables['01_各站統計']:summary.append(['測站 '+row[0],f'LOG {row[2]}／功能PASS {row[4]}／功能FAIL {row[5]}',f'有效功能台數 {row[3]}；末次待結案 {row[10]}；重測率 {row[15]}%；MES {row[11]}'])
    tables['00_整體品質總覽']=summary
    # Explanatory rows for sections requiring distinct source parsers; never fabricate data.
    missing={'16A_測試前區間明細':'需DebugInfo內明確起始/測項時間格式，不以檔名時間推算', '26_耦合站內覆測':'須相同MAC、Band、ANT測序與後續PASS完整對應','27_流量LOG規格欄位':'LOG不提供上下限時，記錄為未提供，不自行定義標準','29_流量異常與缺測':'缺測需規範預期埠清單才可正式判定'}
    for name,note in missing.items():
        if not tables[name]:tables[name].append(['待確認',note])
    return tables

def hide_internal_sheet(path):
    """Set internal sheet 00 to veryHidden in OOXML; preserve all other workbook parts."""
    import tempfile
    source=Path(path)
    with tempfile.NamedTemporaryFile(dir=source.parent,suffix='.xlsx',delete=False) as f:
        temp=Path(f.name)
    try:
        with zipfile.ZipFile(source,'r') as inp,zipfile.ZipFile(temp,'w') as out:
            for item in inp.infolist():
                raw=inp.read(item.filename)
                if item.filename=='xl/workbook.xml':
                    xml=raw.decode('utf-8')
                    pat=r'(<(?:[a-zA-Z0-9_]+:)?sheet\s+name="00_整體品質總覽"[^>]*?)(\s*/>)'
                    xml,num=re.subn(pat,lambda m: re.sub(r'\sstate="[^"]*"','',m.group(1))+' state="veryHidden"'+m.group(2),xml,count=1)
                    if num!=1:raise ValueError('Cannot locate internal sheet 00 for hiding')
                    raw=xml.encode('utf-8')
                out.writestr(item,raw)
        temp.replace(source)
    finally:
        temp.unlink(missing_ok=True)

def create_report(data,output):
    # Rebuild all 32 sheets using the approved design template. The template is
    # never used as a data source; every data row is recalculated from ZIP LOG.
    from xlsx_template_writer import write
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    schema=json.loads((root/'schema.json').read_text(encoding='utf-8'))
    template=root/'templates'/'GRG4372_MP_Approved_V2.6.1.xlsx'
    rows=prepare_tables(data)
    write(template,output,rows,schema)
    return {'sheet_count':len(schema),'event_count':len(data['events']),
      'csv_count':data['raw_csv_count'],'csv_rows':data['csv_rows'],
      'rf_rows':len(data['rf_measures']),'problems':len(data['problems'])}

def analyze(input_zip,output_xlsx):
    if not zipfile.is_zipfile(input_zip):raise ValueError('輸入不是有效的ZIP')
    rules=json.loads((Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'rules.json').read_text(encoding='utf-8'))
    data=load_events(str(input_zip),rules)
    data['zip_path']=str(input_zip)
    out=Path(output_xlsx);out.parent.mkdir(parents=True,exist_ok=True)
    result=create_report(data,out)
    records=out.parent/'Records';records.mkdir(exist_ok=True)
    stamp=dt.datetime.now().isoformat(timespec='seconds')
    with (records/'execution.log').open('a',encoding='utf-8') as f:f.write(f'{stamp} input={Path(input_zip).name} output={out.name} events={len(data["events"])} csv={data["raw_csv_count"]}\n')
    with (records/'test.log').open('a',encoding='utf-8') as f:f.write(f'{stamp} {json.dumps(result,ensure_ascii=False)}\n')
    with (records/'debug.log').open('a',encoding='utf-8') as f:
        for source,error in data['problems']:f.write(f'{stamp} {source} : {error}\n')
    return result
