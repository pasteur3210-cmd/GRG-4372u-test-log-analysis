"""GRG-4372u offline production LOG audit. Python 3.11+; stdlib only.
Usage: python main.py --input production.zip --output report.xlsx [--rules rules.json]
GUI: python main.py
"""
from __future__ import annotations
import argparse, collections, datetime, json, os, re, sys, traceback, zipfile
from pathlib import Path
from xml.sax.saxutils import escape

VERSION='1.0.7'
MAC_RE=re.compile(r'(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])|(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])')
PCB_RE=re.compile(r'MO\d{8}-\d{5}',re.I)
SECTION=re.compile(r'\[(?P<band>[25])G\s+(?P<stage>[^\]]+)\]\[(?P<mhz>\d+(?:\.\d+)?)\]\[(?P<phy>[^\]]+)\]\[(?P<rate>[^\]]+)\](?:\[(?P<rx_input>-?\d+(?:\.\d+)?)\])?\[(?P<ant>ANT\d+)\]')
METRIC=re.compile(r'\]\s*(?P<name>MeasurePower|FreqError|EVM|Mask|PER|PER_Packet|PERRate)\s*:\s*\[\s*(?P<low>-?\d+(?:\.\d+)?)\s*\]\s*<\s*\[\s*(?P<value>-?\d+(?:\.\d+)?)\s*\]\s*<\s*\[\s*(?P<high>-?\d+(?:\.\d+)?)\s*\]\s*(?P<unit>[%a-zA-Z]*)\s*(?P<result>PASS|FAIL)',re.I)
OTHER_METRIC=re.compile(r'\]\s*(?P<name>[^:\[\]]{2,45})\s*:\s*\[\s*(?P<low>-?\d+(?:\.\d+)?)\s*\]\s*<\s*\[\s*(?P<value>-?\d+(?:\.\d+)?)\s*\]\s*<\s*\[\s*(?P<high>-?\d+(?:\.\d+)?)\s*\]\s*(?P<unit>[%a-zA-Z]*)\s*(?P<result>PASS|FAIL)',re.I)
TIME_RE=re.compile(r'^\[(\d\d:\d\d:\d\d\.\d{3})\]')
PASS_RE=re.compile(r'(?i)(?<![A-Z])PASS(?![A-Z])')
FAIL_RE=re.compile(r'(?i)(?<![A-Z])FAIL(?![A-Z])')

def decode(data, name=''):
    if name.lower().endswith('.log'):
        try: return data.decode('utf-8-sig')
        except UnicodeDecodeError: pass
    for enc in ('utf-8-sig','gb18030','cp950'):
        try:return data.decode(enc)
        except UnicodeDecodeError:continue
    return data.decode('utf-8','replace')

def mac_norm(s):
    m=MAC_RE.search(s or '')
    return re.sub('[^0-9A-F]','',m.group(0).upper()) if m else ''

def stage_for(path, content=None):
    """Identify the station primarily by distinct LOG signatures.

    Folder names are a fallback only, to support RF/板測/RF測試 renaming.
    Ambiguous content is not used to override a recognizable folder.
    """
    if content:
        sample=content[:400_000]
        lo=sample.lower()
        # RF bench test: native MeasureSuite .LOG, or measurement CSV.
        if ('measuresuite.' in lo and ('扫描dut条形码' in sample or '掃描dut條形碼' in sample or 'iqxel' in lo)) or (SECTION.search(sample) and METRIC.search(sample)) or ('lowerlimit' in lo and 'upperlimit' in lo and 'testvalue' in lo):
            return 'RF板測'
        # Ethernet traffic tester.
        if 'teleatt' in lo and ('total result:' in lo or 'total time' in lo):
            return '流量測試'
        # BOB finished product uses eye-diagram measurement; adjustment does not.
        if ('crossing' in lo and ('margin' in lo or '消光比' in sample)) or ('眼图模板测试' in sample or '眼圖模板測試' in sample):
            return 'BOB測試'
        if ('校准完成' in sample or '校準完成' in sample) and ('接收光功率' in sample or '保存flash' in lo):
            return 'BOB調試'
        # Manufacturing station command reports.
        if ('[写入个性化数据]' in sample or '[寫入個性化數據]' in sample or ('nvram set SerialNum' in sample and 'nvram set xPONSN' in sample)):
            return '資訊寫入'
        if ('[检查个性化数据]' in sample or '[檢查個性化數據]' in sample or "MacAddr='" in sample and "xPONSN='" in sample):
            return '資訊核對'
        if ('2.4G_CableLoss' in sample and 'PowerMeterPort' in sample) or ('实际功率' in sample and '天线' in sample and '17.5' in sample and '25.5' in sample):
            return '耦合'
    parts=path.replace('\\','/').split('/')
    for part in parts:
        p=part.lower().replace(' ','')
        if '板测' in p or '板測' in p or p in ('rf','rf測試','rf测试','rftest','rfboardtest'):
            return 'RF板測'
        if 'bob测试' in p or 'bob測試' in p:return 'BOB測試'
        if 'bob调试' in p or 'bob調試' in p:return 'BOB調試'
        if '信息写入' in p or '信息寫入' in p or '資訊寫入' in p:return '資訊寫入'
        if '信息核对' in p or '信息核對' in p or '資訊核對' in p:return '資訊核對'
        if '耦合' in p:return '耦合'
        if '流量' in p:return '流量測試'
    return '其他'

def rf_mode(phy,rate):
    p=phy.upper();r=rate.upper()
    if '11BE' in p or 'EHT' in p: family='11BE'
    elif '11AX' in p or 'HE' in p: family='11AX'
    elif '11G' in p:family='11G'
    elif '11A' in p:family='11A'
    else:family=p
    bw=re.search(r'(?:HE|EHT|BE)(20|40|80|160)',p)
    return family+(bw.group(1) if bw else '')

def rf_rules(rules): return rules.get('rf_rules',[])

def pdf_match(measure, rules):
    """Only exact, explicit condition mapping; no guessing from last TX stage."""
    if measure['metric']=='PER':return None,'待確認：RX Pin、封包數／PER定義未完整對應'
    if '校准' in measure['stage_raw'] or '校準' in measure['stage_raw']:
        return None,'校準站：不得使用成品TX規格'
    if 'TX测试' not in measure['stage_raw'] and 'TX測試' not in measure['stage_raw']:
        return None,'非成品TX：未配置正式規格'
    metric={'MeasurePower':'TX Power','FreqError':'Frequency','EVM':'EVM','Mask':'Spectrum Mask'}.get(measure['metric'])
    if not metric:return None,'此測項未設定正式規格'
    matches=[]
    for r in rf_rules(rules):
        if r.get('band')!=measure['band'] or r.get('mode')!=measure['mode'] or r.get('metric')!=metric:continue
        if r.get('rate') and r['rate'].upper()!=measure['rate'].upper():continue
        if r.get('mhz') and float(r['mhz'])!=float(measure['mhz']):continue
        matches.append(r)
    if len(matches)!=1:return None,'規格對應不唯一或缺少精確頻率／Rate條件'
    return matches[0],''

def judge(value, low, high, strict_low=False, strict_high=False):
    if low is not None and (value<=low if strict_low else value<low):return 'FAIL'
    if high is not None and (value>=high if strict_high else value>high):return 'FAIL'
    return 'PASS'

def parse_rf(content, filename, rules):
    pcb=PCB_RE.search(filename)
    if not pcb:pcb=PCB_RE.search(content[:7000])
    pcb=pcb.group(0).upper() if pcb else ''
    current={};rows=[]
    for line_idx,line in enumerate(content.splitlines(),1):
        sect=SECTION.search(line)
        if sect:
            current={'band':sect['band']+'G','stage_raw':sect['stage'].strip(),'mhz':float(sect['mhz']), 'phy':sect['phy'],'rate':sect['rate'],'ant':sect['ant'],'rx_input':float(sect['rx_input']) if sect['rx_input'] else None}
        m=METRIC.search(line)
        if not m:continue
        if not current:continue
        name=m['name'];name='PER' if name.upper().startswith('PER') else name
        d=dict(current,pcb=pcb,source=filename,line=line_idx, time=TIME_RE.search(line).group(1) if TIME_RE.search(line) else '',metric=name,low=float(m['low']),value=float(m['value']),high=float(m['high']),unit=m['unit'],log_result=m['result'].upper())
        d['mode']=rf_mode(d['phy'],d['rate'])
        rule,reason=pdf_match(d,rules)
        if rule:
            d['pdf_low']=rule.get('low');d['pdf_high']=rule.get('high');d['pdf_ref']=rule.get('source','');d['pdf_status']=judge(d['value'],d['pdf_low'],d['pdf_high'],rule.get('strict_low',False),rule.get('strict_high',False))
            d['limits_match']='一致' if d['low']==d['pdf_low'] and d['high']==d['pdf_high'] else '不一致'
            d['match_note']='初步數值比對；條件與正式PDF仍需受控複核'
        else:d.update(pdf_low=None,pdf_high=None,pdf_ref='',pdf_status='待確認',limits_match='待確認',match_note=reason)
        rows.append(d)
    # Per test condition, a FAIL followed by PASS is a retest: preserve both records.
    grouped=collections.defaultdict(list)
    for i,d in enumerate(rows):
        key=(d['pcb'],d['band'],d['stage_raw'],d['mhz'],d['phy'],d['rate'],d['ant'],d['metric'],d['low'],d['high'])
        grouped[key].append(i)
    for inds in grouped.values():
        for pos,i in enumerate(inds):
            d=rows[i]
            if d['log_result']=='FAIL':
                following=[j for j in inds[pos+1:] if rows[j]['log_result']=='PASS']
                if following:
                    j=following[0];d['retest']='初測FAIL→同條件複測PASS';d['final_status']='PASS（覆測）';d['retest_line']=rows[j]['line']
                    rows[j]['retest']='覆測PASS';rows[j]['final_status']='PASS';rows[j]['retest_line']=d['line']
                else:d['retest']='未找到後續同條件PASS';d['final_status']='FAIL/待追蹤';d['retest_line']=''
            else:
                d.setdefault('retest','');d.setdefault('final_status','PASS');d.setdefault('retest_line','')
    return rows

def extract_bob(txt,filename,stage,rules):
    mac=mac_norm(filename) or mac_norm(re.search(r'(?m)^MAC\s*=\s*([^\s]+)',txt).group(1)) if re.search(r'(?m)^MAC\s*=\s*([^\s]+)',txt) else mac_norm(filename)
    # Measurement eligibility is station-specific. Adjustment logs may contain
    # ER calibration/readback values, but these are NOT the final BOB ER eye test.
    # Never turn absence of final-test metrics at BOB adjustment into failure.
    patterns={
     '發射實測光功率':r'发射实测光功率\s*[：:]\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)',
    }
    if stage=='BOB測試':
        patterns.update({
         '消光比 ER':r'消光比测试值为\s*[:：]\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)|消光比\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*[,，]符合',
         'Eye Margin':r'\bmargin\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)',
         '眼圖 Crossing':r'交叉点Crossing\s*[:：]\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)',
         '總抖動':r'总抖动为\s*[:：]\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)',
         '偏置電流':r'Bias DDM\s*[:：]\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)',
         '測試電流(MOD)':r'发射MOD电流\s*[:：]\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)',
        })
    out=[]
    for label,pat in patterns.items():
        ms=list(re.finditer(pat,txt,re.I));
        if not ms:continue
        m=ms[-1]; raw=next((g for g in m.groups() if g is not None),None)
        rule=(rules.get('mp_bob_rules',{}) if stage=='BOB測試' else rules.get('bob_rules',{})).get(label,{})
        low=rule.get('low');high=rule.get('high');value=float(raw);valid=abs(value)<1e12 and __import__('math').isfinite(value)
        # BOB adjustment is NOT automatically a production final-test failure.
        is_adj=stage=='BOB調試'
        out.append(dict(stage=stage,mac=mac,source=filename,metric=label,value=value,pdf_low=low,pdf_high=high,unit=rule.get('unit',''),pdf_page=rule.get('page',''),pdf_status=('無效值' if not valid else '調試監控/不作成品判定') if is_adj else (('無效值' if not valid else judge(value,low,high,rule.get('strict_low',False),rule.get('strict_high',False))) if rule else '待確認'),note='BOB調試站：TX值僅供校準追溯；ER校準值不當成成品ER；無Margin/Crossing/總抖動驗收要求' if is_adj else 'BOB成品測試；依MP受控限值重判'))
    return out

# Minimal deterministic Office Open XML writer, no external dependencies.
def colname(i):
    r='';i+=1
    while i:i,k=divmod(i-1,26);r=chr(65+k)+r
    return r

def xml_cell(v,ref,style=0):
    a=f' r="{ref}" s="{style}"'
    if isinstance(v,str) and v.startswith('__XLFORMULA__:'):
        a=f' r="{ref}" s="2"'
        return f'<c{a}><f>{escape(v.split(":",1)[1])}</f></c>'
    if v is None:return f'<c{a}/>'
    if isinstance(v,bool):return f'<c{a} t="b"><v>{int(v)}</v></c>'
    if isinstance(v,(int,float)) and not isinstance(v,bool):return f'<c{a}><v>{v}</v></c>'
    v=str(v)
    if len(v)>32767:v=v[:32760]+'[截斷]'
    # prevent Excel formula injection
    if v.startswith(('=','+','-','@')):v="'"+v
    return f'<c{a} t="inlineStr"><is><t xml:space="preserve">{escape(v)}</t></is></c>'

def write_xlsx(target,sheets):
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        z.writestr('[Content_Types].xml','<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))+'</Types>')
        z.writestr('_rels/.rels','<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,_) in enumerate(sheets,1))+'</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))+'<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml','<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="10"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Aptos"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF17365D"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/><xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs></styleSheet>')
        for no,(name,data) in enumerate(sheets,1):
            w=max((len(row) for row in data),default=1);last=colname(w-1)
            contents=['<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',f'<dimension ref="A1:{last}{max(len(data),1)}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>']
            contents += [f'<col min="{i+1}" max="{i+1}" width="{min(47, max(14, 26 if i<3 else 18))}" customWidth="1"/>' for i in range(w)]
            contents.append('</cols><sheetData>')
            for i,row in enumerate(data,1):
                contents.append(f'<row r="{i}">')
                for j,v in enumerate(row):contents.append(xml_cell(v,f'{colname(j)}{i}',1 if i==1 else 0))
                contents.append('</row>')
            contents.append('</sheetData>')
            if len(data)>1:contents.append(f'<autoFilter ref="A1:{last}{len(data)}"/>')
            contents.append('</worksheet>');z.writestr(f'xl/worksheets/sheet{no}.xml',''.join(contents))

def filename_event_time(name):
    # 34476306B0D4-fail72026-11-10T050609.txt
    m=re.search(r'(20\d{2}-\d{2}-\d{2})T(\d{6})', name)
    if m:
        try:return datetime.datetime.strptime(m.group(1)+m.group(2),'%Y-%m-%d%H%M%S')
        except ValueError:pass
    return None

def explicit_file_status(path,content):
    path=path.replace('\\','/')
    if re.search(r'/(Fail|FAIL|失败|失敗)/',path):return 'FAIL'
    if re.search(r'/(Success|SUCCESS|成功)/',path):return 'PASS' if '测试完成' in content or '測試完成' in content else 'PASS(檔案標示)'
    if re.search(r'/(Abort|ABORT)/',path):return 'ABORT'
    name=Path(path).name
    if re.search(r'(?i)(?:^|[-_])abort',name):return 'ABORT'
    if re.search(r'(?i)(?:^|[-_])fail',name):return 'FAIL'
    if re.search(r'(?i)(?:^|[-_])pass',name):return 'PASS'
    return 'UNKNOWN'

def link_bob_retests(entries, rules):
    """Only same MAC + same BOB station + strictly later PASS, with valid final values."""
    grouped=collections.defaultdict(list)
    for r in entries:
        if r['station'] in ('BOB測試','BOB調試') and r['mac']:
            grouped[(r['mac'],r['station'])].append(r)
    out=[]
    for (mac,station), seq in grouped.items():
        seq.sort(key=lambda r:(r['time'] is None,r['time'] or datetime.datetime.max,r['path']))
        for pos,fail in enumerate(seq):
            if fail['status']!='FAIL':continue
            after=[x for x in seq[pos+1:] if x['status']=='PASS' and fail['time'] is not None and x['time'] is not None and x['time']>fail['time']]
            passed=None; notes=[]
            for x in after:
                if station=='BOB測試':
                    rr=extract_bob(x['text'],x['name'],station,rules)
                    targets={v['metric']:v for v in rr if v['metric'] in ('發射實測光功率','消光比 ER')}
                    if not all(y in targets and targets[y]['pdf_status']=='PASS' for y in ('發射實測光功率','消光比 ER')):
                        notes.append('後續PASS檔內TX/ER數值不足或超限：'+x['name']);continue
                if station=='BOB調試':
                    # A subsequent PASS file proves station retest status only, not ER/Margin eye-test acceptance.
                    pass
                passed=x;break
            values=extract_bob(fail['text'],fail['name'],station,rules)
            failing=[v for v in values if v['pdf_status'] in ('FAIL','無效值')]
            if not failing:failing=[dict(metric='LOG檔案FAIL',value='',pdf_status='FAIL')]
            later=passed or (after[0] if after else None)
            final='已確認同站複測PASS' if passed else ('找到後續PASS檔，但數值未完成驗證' if after else '未找到後續同站PASS')
            good={x['metric']:x for x in extract_bob(passed['text'],passed['name'],station,rules)} if passed else {}
            interval=round((later['time']-fail['time']).total_seconds(),1) if later and fail['time'] else ''
            for v in failing:
                metric=v['metric'];pv=good.get(metric,{}).get('value','')
                out.append([station,mac,metric,v['value'],v['pdf_status'],fail['time'].isoformat(' ') if fail['time'] else '',fail['name'],later['time'].isoformat(' ') if later and later['time'] else '',later['name'] if later else '',interval,pv,good.get(metric,{}).get('pdf_status',''),final,'；'.join(notes)])
    return out


STATION_ORDER=['BOB調試','BOB測試','RF板測','資訊寫入','資訊核對','耦合','流量測試']

def identify_key(station, pcb, mac):
    """Never silently equate a GPON-SN-derived 12-hex substring with MAC."""
    if station=='RF板測':
        return ('PCB Seq',pcb) if pcb else ('','')
    if not mac:return ('','')
    if station=='資訊寫入' and mac.startswith('434D5444'):
        return ('GPON SN/待映射',mac)
    return ('MAC',mac)


def any_event_time(name):
    t=filename_event_time(name)
    if t is not None:return t
    m=re.search(r'(20\d{2})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})',name)
    if m:
        try:return datetime.datetime(*(int(x) for x in m.groups()))
        except ValueError:return None
    return None


def station_unit_results(index):
    """Device-key + station chronology; no inferred MES clearance."""
    all_groups=collections.defaultdict(list)
    no_key=[]
    for r in index:
        st,pcb,mac,status,name,_,_,path=r
        kind,key=identify_key(st,pcb,mac)
        event=dict(st=st,kind=kind,key=key,status=status,name=name,path=path,time=any_event_time(name))
        if not key:no_key.append(event)
        else:all_groups[(st,kind,key)].append(event)
    result=[]
    for (st,kind,key),events in sorted(all_groups.items()):
        reliable=all(x['time'] is not None for x in events)
        # All timestamps must be available to make a confident first/last/redo judgment.
        seq=sorted(events,key=lambda x:(x['time'] or datetime.datetime.min,x['name'])) if reliable else events
        first,last=seq[0],seq[-1]
        normal=lambda x: x['status'] in ('PASS','PASS(檔案標示)')
        bad=lambda x: x['status'] in ('FAIL','ABORT')
        has_bad=any(bad(x) for x in seq)
        has_abort=any(x['status']=='ABORT' for x in seq)
        fail_count=sum(x['status']=='FAIL' for x in seq)
        abort_count=sum(x['status']=='ABORT' for x in seq)
        abnormal_count=fail_count+abort_count
        attempt_count=len(seq)
        repeat_count=max(0,attempt_count-1)
        # Count every FAIL->PASS recovery transition separately. A device can recover more than once.
        recovery_count=sum(bad(seq[i-1]) and normal(seq[i]) for i in range(1,len(seq))) if reliable else 0
        abnormal_before_last=sum(bad(x) for x in seq[:-1]) if len(seq)>1 else 0
        retest_count=abnormal_count
        if not reliable:final='待確認：時間資訊不足';first_class='待確認';retest_pass=0
        elif any(x['status']=='UNKNOWN' for x in seq):final='待確認：含UNKNOWN';first_class='待確認';retest_pass=0
        elif normal(last):
            retest_pass=int(has_bad and any(bad(x) for x in seq[:-1]))
            final='覆測PASS（LOG）' if retest_pass else '最後PASS（LOG）'
            first_class='首測PASS' if normal(first) else '首測異常'
        elif last['status']=='ABORT':final='最後ABORT／待結案';first_class='首測異常' if bad(first) else '首測PASS';retest_pass=0
        else:final='最後FAIL／待結案';first_class='首測異常' if bad(first) else '首測PASS';retest_pass=0
        result.append(dict(station=st,kind=kind,key=key,events=seq,first=first,last=last,first_class=first_class,final=final,retest_pass=retest_pass,retest_count=retest_count,attempt_count=attempt_count,repeat_count=repeat_count,fail_count=fail_count,abort_count=abort_count,abnormal_count=abnormal_count,recovery_count=recovery_count,abnormal_before_last=abnormal_before_last,abort=has_abort,reliable=reliable))
    return result,no_key


def build_spec_comparison(old, rules):
    """Audit four prioritized stations, explicitly distinguishing NO LOG from PASS.
    RF numerical mappings remain provisional until condition and approval are reviewed.
    """
    index=old['06_檔案索引'][1:]
    have=collections.Counter(r[0] for r in index)
    rf=old['02_RF逐筆'][1:]
    bob=old['04_BOB規格比對'][1:]
    result=[['測試站','測試模式/條件','測試項目','核准Low','核准High','單位','LOG Low','LOG High','規格一致性','有效量測筆數','LOG FAIL筆數','重判超限筆數','待確認筆數','規格來源','證據/限制']]
    # Values below are from the approved MP amendment, NOT original pre-MP PDF limits.
    bob_specs=[('發射實測光功率',1.5,3.5,'dBm','MP規格更新 2026-11-11'),('消光比 ER',11.5,13.5,'dB','MP規格更新 2026-11-11'),('Eye Margin',30,None,'%','PDF p5 / 版本記錄'),('眼圖 Crossing',45,55,'%','PDF p5'),('總抖動',None,300,'ps','PDF p5')]
    for metric,low,high,unit,src in bob_specs:
        rows=[r for r in bob if r[0]=='BOB測試' and r[3]==metric]
        if not have['BOB測試']:
            result.append(['BOB測試','成品測試',metric,low,high,unit,'','','NO LOG',0,0,0,0,src,'本批未提供BOB測試LOG'])
            continue
        fail=sum(r[9]=='FAIL' for r in rows)
        invalid=sum(r[9]=='無效值' for r in rows)
        review=sum(r[9] not in ('PASS','FAIL','無效值') for r in rows)
        # Program historically extracted measurements, not the literal limits for each BOB LOG.
        result.append(['BOB測試','成品測試',metric,low,high,unit,'未逐筆擷取','未逐筆擷取','LOG限值待查',len(rows),None,fail,invalid+review,src,'MP標準已套用數值重判；LOG內限值尚未逐筆解析；超限為事件非最終不良'])
    rf_groups=collections.defaultdict(list)
    for r in rf:
        key=(r[5],r[9],r[8],r[6],r[12],r[4])
        rf_groups[key].append(r)
    for key,rows in sorted(rf_groups.items(),key=lambda x:tuple(map(str,x[0]))):
        band,mode,rate,mhz,metric,stage=key
        vals=lambda j: sorted({str(r[j]) for r in rows if r[j] is not None and r[j]!=''})
        lo=vals(18);hi=vals(19);llo=vals(15);lhi=vals(16)
        comparison=collections.Counter(r[21] for r in rows)
        if comparison['不一致']:state='MISMATCH（暫定）'
        elif comparison['一致'] and not comparison['待確認']:state='MATCH（暫定）'
        else:state='條件待確認'
        result.append(['RF板測',f'{band} {mode} {rate} {mhz}MHz {stage}',metric,', '.join(lo),', '.join(hi),rows[0][14],', '.join(llo),', '.join(lhi),state,len(rows),sum(r[17]=='FAIL' for r in rows),sum(r[20]=='FAIL' for r in rows),sum(r[20]=='待確認' for r in rows),'PDF p2–p4 / rules.json (暫定)','須核對Channel/Rate/RX Pin/PER封包數；校準站不套成品TX規格'])
    if not have['RF板測']:
        # Show every configured RF rule even without logs, making test coverage visible.
        for r in rules.get('rf_rules',[]):
            result.append(['RF板測',f"{r.get('band','')} {r.get('mode','')} {r.get('rate','')} {r.get('mhz','')}MHz",r.get('metric',''),r.get('low'),r.get('high'),'依規格','','','NO LOG',0,0,0,0,r.get('source',''),'本批未提供RF板測LOG；不得判PASS/FAIL'])
        result.append(['RF板測','RX PER','PER / RX Pin / 封包數','依模式','依模式','%','','','NO LOG',0,0,0,0,'PDF p2–p4','正式條件須包括RX Pin、900/1000封包及測試Rate'])
    # Coupling NFT per PDF p4 (not the Wi-Fi TX final test limits).
    for band,mode,ch in [('2.4G','11G 54M','CH3'),('5G','11A 54M','CH40')]:
        result.append(['耦合',f'{band} {mode} {ch}','NFT TX Power',17.5,25.5,'dBm','','','NO LOG' if not have['耦合'] else '待確認：尚無NFT量測欄位解析',0,None,0,0,'PDF p4','需要區分耦合LOG內儀器指令、輸出值與實際NFT功率；無法僅從檔名判規格'])
    result.append(['流量測試','4 LAN 1G + 1 LAN 2.5G','吞吐量 Low',800,None,'PDF未明確列單位','','','NO LOG' if not have['流量測試'] else '待確認：尚無可比對吞吐量實測值',0,None,0,0,'PDF p2','MP有TeleATT流量LOG，但Port/速率/封包統計尚未與PDF吞吐量800建立受控量測映射'])
    return result

def station_report_layout(original, rules):
    """Stable seven-station workbook with event and unique-device status separated."""
    old=dict(original)
    index=old['06_檔案索引'][1:]
    bob=old['04_BOB規格比對'][1:]
    rf=old['02_RF逐筆'][1:]
    ret=old['10_BOB覆測追蹤'][1:]
    coverage=old['09_MAC跨站覆蓋']
    by_st=collections.defaultdict(list)
    for r in index:by_st[r[0]].append(r)
    units,unknown_ids=station_unit_results(index)
    by_unit=collections.defaultdict(list)
    for u in units:by_unit[u['station']].append(u)
    by_measure=collections.defaultdict(dict)
    for r in bob:by_measure[(r[0],r[2])][r[3]]=r
    ret_by_file=collections.defaultdict(list)
    for r in ret:ret_by_file[(r[0],r[1],r[6])].append(r)
    station_rows=[]
    rate_rows=[]
    for st in STATION_ORDER:
        events=by_st[st];cnt=collections.Counter(r[3] for r in events)
        uu=by_unit[st]
        firstpass=sum(x['first_class']=='首測PASS' for x in uu)
        retpass=sum(x['retest_pass'] for x in uu)
        lastpass=sum('PASS（LOG）' in x['final'] for x in uu)
        failpending=sum(x['final']=='最後FAIL／待結案' for x in uu)
        abortpending=sum(x['final']=='最後ABORT／待結案' for x in uu)
        review=sum(x['final'].startswith('待確認') for x in uu)
        keyless=sum(x['st']==st for x in unknown_ids)
        eligible=[x for x in uu if x['reliable'] and not any(e['status']=='UNKNOWN' for e in x['events'])]
        # Rate denominators are only keys that have reliable chronology and a known outcome.
        eligible_n=len(eligible)
        repeated=sum(x['repeat_count']>0 for x in eligible)
        total_repeats=sum(x['repeat_count'] for x in eligible)
        first_fail=sum(x['first']['status']=='FAIL' for x in eligible)
        first_abort=sum(x['first']['status']=='ABORT' for x in eligible)
        first_abnormal=sum(x['first']['status'] in ('FAIL','ABORT') for x in eligible)
        ever_fail=sum(x['fail_count']>0 for x in eligible)
        ever_bad=sum(x['abnormal_count']>0 for x in eligible)
        first_abnormal_retested=sum(x['first']['status'] in ('FAIL','ABORT') and x['repeat_count']>0 for x in eligible)
        resolved_from_first=sum(x['first']['status'] in ('FAIL','ABORT') and x['retest_pass'] for x in eligible)
        all_fail_events=sum(x['fail_count'] for x in uu)
        all_abort_events=sum(x['abort_count'] for x in uu)
        total_recovery=sum(x['recovery_count'] for x in eligible)
        units_pending=sum(x['final'] in ('最後FAIL／待結案','最後ABORT／待結案') for x in eligible)
        def ratio(n,d):return (n/d) if d else None
        rate_rows.append([st,'已提供' if events else 'NO LOG',len(events),len(uu),eligible_n,keyless,first_fail,first_abort,first_abnormal,ever_fail,all_fail_events,all_abort_events,repeated,total_repeats,first_abnormal_retested,resolved_from_first,retpass,total_recovery,failpending,abortpending,review,ratio(first_abnormal,eligible_n),ratio(repeated,eligible_n),ratio(first_abnormal_retested,first_abnormal),ratio(resolved_from_first,first_abnormal_retested),ratio(units_pending,eligible_n),'率值以可排序且結果有效的唯一站別識別碼為分母；尚不能當整批不良率' if events else 'NO LOG: 不計算'])
        station_rows.append([st,'已提供' if events else 'NO LOG',len(events),len(uu),cnt['PASS']+cnt['PASS(檔案標示)'],cnt['FAIL'],cnt['ABORT'],firstpass,retpass,lastpass,failpending,abortpending,review,keyless,'LOG層級結案；MES/主鍵映射待核' if events else '未提供LOG，不等於PASS或FAIL'])
    total=lambda idx:sum(row[idx] for row in station_rows)
    summary=[['項目','結果','說明'],
       ['程式版本',VERSION,'GRG4372 Log Audit'],
       ['分析範圍','單一匯入ZIP','MP正式產品報告不混入40pcs回歸資料'],
       ['LOG總數',len(index),'事件數，非獨立台數'],
       ['已提供測站數',sum(bool(by_st[x]) for x in STATION_ORDER),'固定7站'],
       ['NO LOG測站數',sum(not by_st[x] for x in STATION_ORDER),'不得宣稱通過'],
       ['原始PASS事件',total(4),'同台重複測試仍計事件'],
       ['原始FAIL事件',total(5),'保留初測異常'],
       ['原始ABORT事件',total(6),'中斷，不等於產品最終不良'],
       ['首測PASS識別碼-站別組數',total(7),'逐站計數，同一台跨站會重複；需可靠時間'],
       ['FAIL/ABORT後覆測PASS組數',total(8),'同主鍵同站、時間可排序、最後PASS的組數'],
       ['最後PASS識別碼-站別組數',total(9),'LOG狀態；不能稱全流程良率'],
       ['最後FAIL待結案組數',total(10),'未找到後續PASS；不等於MES最終FAIL'],
       ['最後ABORT待結案組數',total(11),'無後續PASS之LOG識別碼組數'],
       ['狀態待確認組數',total(12),'時間不足或UNKNOWN'],
       ['缺主鍵LOG事件',total(13),'無法將其可靠歸至單台'],
       ['RF量測紀錄',len(rf),'未提供RF站時顯示0'],
       ['PER量測紀錄',sum(r[12]=='PER' for r in rf),'不得誤歸前一個TX測項'],
       ['BOB測試TX MP規格','1.5～3.5 dBm','調試站僅校準監控'],
       ['BOB測試ER MP規格','11.5～13.5 dB','調試站ER不作成品驗收'],
       ['規格核對覆蓋','BOB測試、RF板測、耦合、流量測試','詳07A_測試規格比對；NO LOG不代表符合規格'],
       ['整機放行結論','資料不完整／不可判定全流程PASS' if any(not by_st[x] for x in STATION_ORDER) else '待MES與跨站完整性查核','跨站MAC／GPON SN／PCB關聯不得臆測']]
    summary.extend([[],['各站統計（事件與唯一識別碼分開）'],['測試站','提供狀態','LOG數','唯一識別碼數','PASS事件','FAIL事件','ABORT事件','首測PASS','覆測PASS','最後PASS','最後FAIL待結案','最後ABORT待結案','待確認','缺主鍵LOG','說明']]+station_rows)
    rate_header=['測試站','資料狀態','LOG總數','唯一識別碼數','可判定主鍵數(分母)','缺主鍵LOG','首測FAIL台數','首測ABORT台數','首測異常台數','曾FAIL台數','FAIL事件次數','ABORT事件次數','有覆測台數','覆測總次數','首測異常且有覆測台數','首測異常覆測PASS台數','歷程有異常且最後PASS台數','FAIL→PASS轉折次數','最後FAIL待結案台數','最後ABORT待結案台數','待確認主鍵數','首測異常率','重複測試紀錄率（非異常覆測率）','首測異常後覆測率','覆測成功率','待結案率','定義與限制']
    rate_sheet=[rate_header]+[r.copy() for r in rate_rows]
    for line_no,rr in enumerate(rate_sheet[1:],2):
        # Formula-driven rate audit in a stable, independently recalculable worksheet.
        for column,numerator,denominator in [('V','I','E'),('W','M','E'),('X','O','I'),('Y','P','O')]:
            rr[ord(column)-65]=f'__XLFORMULA__:IF({denominator}{line_no}=0,"",{numerator}{line_no}/{denominator}{line_no})'
        rr[25]=f'__XLFORMULA__:IF(E{line_no}=0,"",(S{line_no}+T{line_no})/E{line_no})'

    # In the first sheet, append all numerator/denominator fields and explanatory rate definitions.
    summary_rows=[r[:21]+[(f'{v:.2%}' if isinstance(v,(float,int)) else '') for v in r[21:26]]+[r[26]] for r in rate_rows]
    summary.extend([[],['覆測次數、首測異常與待結案（依站別）'],rate_header]+summary_rows)
    summary.extend([[],['率值定義','分子／分母'],['重複測試紀錄率（非異常覆測率）','有覆測台數 ÷ 可判定主鍵數'],['首測異常率（首測不良率代理）','首測 FAIL 或 ABORT 主鍵數 ÷ 可判定主鍵數；FAIL 與 ABORT 另列'],['首測異常後覆測率','首測異常且有覆測台數 ÷ 首測異常台數'],['覆測成功率','首測異常且最終 PASS 台數 ÷ 首測異常且有覆測台數'],['待結案率（非產品最終不良率）','最後 FAIL 或 ABORT 待結案主鍵數 ÷ 可判定主鍵數'],['注意','每台跨站不合併；同一主鍵如有不同設備或缺時間，不能用於正式 FPY/DPPM']])
    result=[('00_Overall Summary',summary),('00C_覆測與不良率',rate_sheet),('00A_測站與最終狀態',[
      ['測試站','提供狀態','LOG數','唯一識別碼數','PASS事件','FAIL事件','ABORT事件','首測PASS組數','異常後覆測PASS組數','最後PASS組數','最後FAIL待結案','最後ABORT待結案','時間/結果待確認','缺主鍵事件','限制']]+station_rows)]
    unit_sheet=[['站別','主鍵種類','設備識別碼','測試LOG次數','覆測次數','FAIL次數','ABORT次數','FAIL→PASS轉折次數','首測狀態','首測時間','最後LOG狀態','最後測試時間','異常後覆測PASS','LOG層級最終分類','首測LOG','最後LOG','時間完整','說明']]
    for x in units:
        first,last=x['first'],x['last']
        unit_sheet.append([x['station'],x['kind'],x['key'],x['attempt_count'],x['repeat_count'],x['fail_count'],x['abort_count'],x['recovery_count'],first['status'],first['time'].isoformat(' ') if first['time'] else '',last['status'],last['time'].isoformat(' ') if last['time'] else '','是' if x['retest_pass'] else '否',x['final'],first['name'],last['name'],'是' if x['reliable'] else '否','GPON SN不能自動當MAC' if x['kind']=='GPON SN/待映射' else '依LOG狀態，非MES過站證明'])
    result.append(('00B_逐台最終結果',unit_sheet))
    detailed=[['測試站','識別碼類型','設備識別碼','第幾次測試','本次是否覆測','測試時間','LOG結果','上次LOG結果','是否FAIL→PASS轉折','LOG檔案名稱','完整路徑','最終狀態(同站)','備註']]
    for x in units:
        for i,e in enumerate(x['events']):
            prev=x['events'][i-1] if i else None
            detailed.append([x['station'],x['kind'],x['key'],i+1,'是' if i else '否',e['time'].isoformat(' ') if e['time'] else '',e['status'],prev['status'] if prev else '', '是' if prev and prev['status'] in ('FAIL','ABORT') and e['status'] in ('PASS','PASS(檔案標示)') else '否',e['name'],e['path'],x['final'],'排序時間不完整，順序待確認' if not x['reliable'] else ''])
    for e in unknown_ids:
        detailed.append([e['st'],'缺主鍵','','','',e['time'].isoformat(' ') if e['time'] else '',e['status'],'','','',e['path'],'無法歸屬設備','主鍵缺失'])
    result.append(('00D_逐次測試歷程',detailed))
    for idx,st in enumerate(STATION_ORDER,1):
        events=by_st[st]
        if not events:
            result.append((f'{idx:02d}_{st}',[['檢查項目','狀態','說明'],['測站LOG','NO LOG','本次ZIP沒有此測試站LOG'],['最終判定','UNKNOWN','缺少資料不可判PASS／FAIL'],['待補','測站LOG/MES','取得後重新分析']]));continue
        if st in ('BOB測試','BOB調試'):
            header=['MAC','原始LOG判定','TX Power(dBm)','TX判定','ER(dB)','ER判定','Margin(%)','Crossing(%)','總抖動(ps)','覆測追蹤','檔名','ZIP路徑']
            data=[]
            for r in events:
                mm=by_measure.get((st,r[4]),{})
                def met(key,col):return mm[key][col] if key in mm else ''
                linked=ret_by_file.get((st,r[2],r[4]),[])
                follow='；'.join(sorted({a[12] for a in linked})) if linked else ('初次FAIL，詳逐台最終結果' if r[3]=='FAIL' else '')
                data.append([r[2],r[3],met('發射實測光功率',4),met('發射實測光功率',9),met('消光比 ER',4) if st=='BOB測試' else 'N/A',met('消光比 ER',9) if st=='BOB測試' else 'N/A',met('Eye Margin',4) if st=='BOB測試' else 'N/A',met('眼圖 Crossing',4) if st=='BOB測試' else 'N/A',met('總抖動',4) if st=='BOB測試' else 'N/A',follow,r[4],r[7]])
            result.append((f'{idx:02d}_{st}',[header]+data))
        else:result.append((f'{idx:02d}_{st}',[['識別碼(MAC/PCB)','PCB Seq','LOG判定','檔名','ZIP路徑','說明']]+[[r[2],r[1],r[3],r[4],r[7],'事件；最終分類見00B'] for r in events]))
    result.append(('07A_測試規格比對',build_spec_comparison(old,rules)))
    result.extend([
      ('08_異常覆測追蹤',old['10_BOB覆測追蹤']),
      ('09_MAC跨站追溯',coverage),
      ('10_BOB量測明細',old['04_BOB規格比對']),
      ('11_LOG檔案索引',old['06_檔案索引']),
      ('12_RF逐筆',old['02_RF逐筆']),
      ('13_RF規格統計',old['01_RF規格統計']),
      ('14_RF覆測',old['03_RF覆測與FAIL']),
      ('15_資料問題',old['07_資料問題']),
      ('16_規格與限制',old['00A_BOB站別規則']),
      ('17_分析規則',old['08_分析規則']),
      ('18_主鍵重複',old['05_跨站與重複']),
    ])
    return result

def analyze(zip_path,output,rules_path=None):
    here=(Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent)
    rules=json.loads(Path(rules_path or here/'rules.json').read_text(encoding='utf-8'))
    rf=[];bob=[];file_rows=[];stations=collections.defaultdict(list);error_rows=[];bob_events=[]
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            if member.is_dir() or Path(member.filename).suffix.lower() not in ('.log','.txt'):continue
            if member.file_size>15_000_000:
                error_rows.append([member.filename,'超過單一LOG 15MB安全上限']);continue
            try:
                content=decode(archive.read(member),member.filename);name=Path(member.filename).name
                station=stage_for(member.filename,content)
                pcb=(PCB_RE.search(name).group(0).upper() if PCB_RE.search(name) else '')
                mac=mac_norm(name)
                if not mac:
                    match=re.search(r'(?im)^\s*(?:MAC|MacAddr)\s*[:=]\s*([^\s]+)',content)
                    if match:mac=mac_norm(match.group(1))
                if station=='RF板測':
                    rr=parse_rf(content,name,rules);rf.extend(rr)
                    pcb=pcb or (rr[0]['pcb'] if rr else '')
                if station.startswith('BOB'):
                    bb=extract_bob(content,name,station,rules);bob.extend(bb)
                    bob_events.append(dict(station=station,mac=mac_norm(name),status=explicit_file_status(member.filename,content),time=filename_event_time(name),name=name,path=member.filename,text=content))
                final=explicit_file_status(member.filename,content)
                file_rows.append([station,pcb,mac,final,name,len(content),len(rr) if station=='RF板測' else '',member.filename]);stations[station].append((pcb,mac,name))
            except Exception as e:error_rows.append([member.filename,str(e)])
    bob_retests=link_bob_retests(bob_events,rules)
    # RF condition statistics
    group=collections.defaultdict(list)
    for r in rf:group[(r['band'],r['mode'],r['metric'],r['stage_raw'])].append(r)
    rf_summary=[]
    for key,rs in sorted(group.items()):
        rf_summary.append(list(key)+[len(rs),len(set(r['pcb'] for r in rs)),sum(r['log_result']=='FAIL' for r in rs),sum(r['pdf_status']=='FAIL' for r in rs),sum(r['pdf_status']=='待確認' for r in rs),sum(r['limits_match']=='不一致' for r in rs),sum(bool(r.get('retest')) and '初測FAIL→' in r['retest'] for r in rs)])
    retests=[[r['pcb'],r['stage_raw'],r['band'],r['mode'],r['mhz'],r['rate'],r['ant'],r['metric'],r['value'],r['log_result'],r['retest'],r['final_status'],r['source'],r['line'],r['retest_line']] for r in rf if r['log_result']=='FAIL']
    # Only MAC-stations are cross-linked; unknown PCB/MAC mapping is not assumed
    cross=[]
    for st,entries in sorted(stations.items()):
        ids=[p if st=='RF板測' else m for p,m,_ in entries if (p if st=='RF板測' else m)]
        freq=collections.Counter(ids)
        cross.append([st,len(entries),len(set(ids)),sum(n-1 for n in freq.values() if n>1),', '.join(k for k,v in freq.items() if v>1)[:300],len(entries)-len(ids)])
    # Cross-station MAC comparison only, RF PCB cannot be joined without audited mapping.
    mac_stations=['BOB調試','BOB測試','資訊寫入','資訊核對','耦合','流量測試']
    all_macs=sorted(set(m for st in mac_stations for p,m,n in stations.get(st,[]) if m))
    station_mac_sets={st:set(m for p,m,n in stations.get(st,[]) if m) for st in mac_stations}
    mac_coverage=[[m]+['有' if m in station_mac_sets[st] else '缺少' for st in mac_stations]+[sum(m not in station_mac_sets[st] for st in mac_stations)] for m in all_macs]
    count_files=collections.Counter(row[0] for row in file_rows)
    total_pdf_fail=sum(x['pdf_status']=='FAIL' for x in rf)
    total_pdf_review=sum(x['pdf_status']=='待確認' for x in rf)
    sumrows=[['項目','結果','備註'],['工具版本',VERSION,'標準庫／離線執行'],['分析ZIP',Path(zip_path).name,'僅分析本次選取ZIP'],['RF板測LOG',count_files['RF板測'],'單次測試檔，不等於設備唯一數'],['RF量測紀錄',len(rf),'筆數不等於台數'],['RX PER紀錄',sum(x['metric']=='PER' for x in rf),'依RX測站段落，不得沿用TX'],['RF LOG原始FAIL事件',len(retests),'包含可覆測PASS紀錄'],['確認同條件覆測PASS',sum('初測FAIL→' in r['retest'] for r in rf),'保留初測不良歷程'],['RF PDF暫定數值FAIL',total_pdf_fail,'須確認測試條件／受控規格'],['RF PDF待確認',total_pdf_review,'不列為FAIL'],['BOB量測紀錄',len(bob),'BOB調試僅有TX監控；ER/Margin/Crossing/Jitter不適用'],['BOB FAIL事件逐項覆測關聯',len(bob_retests),'一個FAIL LOG可有多項超限；詳覆測分頁'],['BOB覆測核實PASS明細',sum(r[12]=='已確認同站複測PASS' for r in bob_retests),'逐測項數量不等於不良台數'],['BOB測試MP數值超限',sum(x['stage']=='BOB測試' and x['pdf_status']=='FAIL' for x in bob),'僅BOB成品測試站'],['資料問題',len(error_rows),'詳資料異常'],['正式規格來源','GRG-4372u 測試規格.pdf','rules.json需由QA批准後才能作正式放行'],['跨站唯一設備數','未判定','缺少全站PCB Seq↔MAC可靠主鍵，不能直接宣稱完整追溯']]
    station_matrix=[['測試項目','BOB調試','BOB測試','判定說明'],['TX Power','校準監控／記錄','MP 1.5~3.5 dBm','調試站不作成品最終判定'],['ER','不適用（校準數值不等於測試項）','MP 11.5~13.5 dB','僅BOB測試站重判'],['Eye Margin','不適用','≥30%','僅BOB測試站'],['Crossing','不適用','45~55%','僅BOB測試站'],['發射端總抖動','不適用','≤300 ps（請核對嚴格符號）','僅BOB測試站'],['缺項處理','不因不存在ER/Margin/Crossing/Jitter判FAIL','測試提前結束應保留未執行原因','不將未測誤當PASS']];sheets=[('00_品質總覽',sumrows),('00A_BOB站別規則',station_matrix),('01_RF規格統計',[['Band','模式','測項','LOG站別','量測筆數','PCB數','LOG FAIL','PDF暫定FAIL','PDF待確認','上下限不一致','同條件覆測PASS']]+rf_summary),('02_RF逐筆',[['PCB Seq','LOG來源','LOG行號','時間','站別','Band','MHz','PHY','Rate','模式','ANT','RX Pin (dBm)','測項','實測值','單位','LOG Low','LOG High','LOG原判','PDF Low','PDF High','PDF暫判','上下限比較','資料限制','覆測狀態','最終狀態','相關行號','PDF來源']]+[[r.get(k,'') for k in ('pcb','source','line','time','stage_raw','band','mhz','phy','rate','mode','ant','rx_input','metric','value','unit','low','high','log_result','pdf_low','pdf_high','pdf_status','limits_match','match_note','retest','final_status','retest_line','pdf_ref')] for r in rf]),('03_RF覆測與FAIL',[['PCB Seq','測站','Band','Mode','MHz','Rate','ANT','測項','原始值','原始判定','覆測狀態','最終狀態','LOG檔','原始行','覆測行']]+retests),('04_BOB規格比對',[['站別','MAC','原始LOG','測項','實測值','單位','PDF Low','PDF High','PDF頁','PDF數值判定','備註']]+[[r.get(k,'') for k in ('stage','mac','source','metric','value','unit','pdf_low','pdf_high','pdf_page','pdf_status','note')] for r in bob]),('05_跨站與重複',[['測站','LOG數','唯一主鍵數','站內重複數','重複主鍵','缺少主鍵LOG數']]+cross),('06_檔案索引',[['測站','PCB Seq','MAC','檔名標示','檔名','字元數','RF量測筆數','ZIP完整路徑']]+file_rows),('07_資料問題',[['來源LOG','問題']]+error_rows),('09_MAC跨站覆蓋',[['MAC']+mac_stations+['缺站數']]+mac_coverage),('10_BOB覆測追蹤',[['站別','MAC','初測異常測項','初測實測','初測判定','初測時間','FAIL LOG','後續時間','PASS LOG','間隔秒','覆測實測','覆測數值判定','最終狀態','備註']]+bob_retests),('08_分析規則',[['規則ID','Band','Mode','Rate','MHz','測項','Low','High','PDF頁','狀態']]+[[r.get(k,'') for k in ('id','band','mode','rate','mhz','metric','low','high','source','status')] for r in rules.get('rf_rules',[])])]
    sheets=station_report_layout(sheets,rules)
    write_xlsx(output,sheets)
    log_dir=Path(output).parent/'Records';log_dir.mkdir(parents=True,exist_ok=True)
    timestamp=datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    with (log_dir/'execution.log').open('a',encoding='utf-8') as fh:
        fh.write(f'{timestamp} version={VERSION} input={Path(zip_path).name} output={Path(output).name} files={len(file_rows)} rf={len(rf)} per={sum(x["metric"]=="PER" for x in rf)} errors={len(error_rows)}\n')
    with (log_dir/'test.log').open('a',encoding='utf-8') as fh:
        fh.write(f'{timestamp} rf_count={len(rf)} per_count={sum(x["metric"]=="PER" for x in rf)} retest_events={len(retests)} retest_pass={sum("初測FAIL→" in r["retest"] for r in rf)} pdf_review={total_pdf_review} pdf_numeric_fail={total_pdf_fail}\n')
    with (log_dir/'debug.log').open('a',encoding='utf-8') as fh:
        for path,msg in error_rows:fh.write(f'{timestamp} {path}: {msg}\n')
    return {'rf':len(rf),'per':sum(x['metric']=='PER' for x in rf),'files':len(file_rows),'retest_pass':sum('初測FAIL→' in r['retest'] for r in rf),'errors':len(error_rows),'bob_retest_rows':len(bob_retests),'bob_retest_pass':sum(r[12]=='已確認同站複測PASS' for r in bob_retests),'output':str(output)}

def gui():
    import tkinter as tk
    from tkinter import filedialog,messagebox,ttk
    root=tk.Tk();root.title('GRG-4372u LOG Audit Tool V'+VERSION);root.geometry('780x285')
    input_path=tk.StringVar();output_path=tk.StringVar();rules_path=tk.StringVar();status=tk.StringVar(value='選擇 ZIP 檔案後執行，正式規格規則可自行載入 JSON。')
    def browse(var,save=False,types=None):
        p=filedialog.asksaveasfilename(defaultextension='.xlsx',filetypes=[('Excel','*.xlsx')]) if save else filedialog.askopenfilename(filetypes=types or [('ZIP','*.zip')])
        if p:var.set(p)
    for row,(caption,var,cmd) in enumerate([('測試 LOG ZIP',input_path,lambda:browse(input_path)),('輸出 Excel',output_path,lambda:browse(output_path,True)),('規格 JSON（可留空）',rules_path,lambda:browse(rules_path,types=[('JSON','*.json')]))]):
        ttk.Label(root,text=caption).grid(row=row,column=0,padx=12,pady=13,sticky='w');ttk.Entry(root,textvariable=var,width=65).grid(row=row,column=1,sticky='ew');ttk.Button(root,text='瀏覽',command=cmd).grid(row=row,column=2,padx=12)
    def run():
        if not input_path.get():messagebox.showerror('缺少檔案','請選擇測試 LOG ZIP');return
        if not output_path.get():output_path.set(str(Path(input_path.get()).with_suffix(''))+'_GRG4372_Audit.xlsx')
        try:
            root.config(cursor='watch');root.update_idletasks();r=analyze(input_path.get(),output_path.get(),rules_path.get() or None)
            status.set(f"完成：{r['files']}份LOG、{r['rf']}筆RF、{r['per']}筆PER、{r['retest_pass']}筆同條件覆測PASS；資料問題{r['errors']}筆。")
            messagebox.showinfo('報告完成',status.get()+'\n'+output_path.get())
        except Exception as e:messagebox.showerror('分析失敗',str(e)+'\n詳見console')
        finally:root.config(cursor='')
    ttk.Button(root,text='開始分析並產出 Excel',command=run).grid(row=3,column=1,pady=14)
    ttk.Label(root,textvariable=status,wraplength=720).grid(row=4,column=0,columnspan=3,sticky='w',padx=12)
    root.grid_columnconfigure(1,weight=1);root.mainloop()

def main():
    p=argparse.ArgumentParser();p.add_argument('--input');p.add_argument('--output');p.add_argument('--rules');p.add_argument('--version',action='store_true');a=p.parse_args()
    if a.version:print(VERSION);return
    if a.input:
        out=a.output or str(Path(a.input).with_suffix(''))+'_Audit.xlsx'
        print(json.dumps(analyze(a.input,out,a.rules),ensure_ascii=False,indent=2))
    else:gui()
if __name__=='__main__':main()
