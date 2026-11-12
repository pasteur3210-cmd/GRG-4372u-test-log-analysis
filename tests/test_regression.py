import zipfile,datetime as dt,json
from pathlib import Path
from analyzer import load_events,prepare_tables
from xlsx_template_writer import sheet_paths

def fixture_zip(tmp_path,additional=None):
    p=tmp_path/'sample.zip'
    with zipfile.ZipFile(p,'w') as z:
      for name,txt in (additional or []):z.writestr(name,txt)
    return p

def test_mes_excluded_from_functional_fail(tmp_path):
    # Identical MAC, first MES block and then valid PASS; no functional fail.
    records=[('BOB調試/Fail/344763060001-fail12026-11-10T100000.txt','不允许重复过站\n测试结果 FAIL\n'),
      ('BOB調試/Success/344763060001-pass12026-11-10T100100.txt','校准完成 接收光功率 保存FLASH\n')]
    table=prepare_tables(load_events(fixture_zip(tmp_path,records),{}))
    summary=table['01_各站統計'][0]
    assert summary[2]==2 and summary[3]==1 and summary[5]==0 and summary[11]==1
    assert summary[12]==0 and summary[15]==0
    assert 'MES異常' in table['03_BOB調試'][0][12]

def test_rf_id_not_overwritten_by_measure_group(tmp_path):
    log='''[10:00:00.000]Load driver (MeasureSuite.ScanData.dll)
扫描DUT条形码 Start
[10:00:01.000][2G TX测试][2412.00][11AX_HE20][MCS0][ANT1]
MeasurePower :[18.0]<[19.0]<[20.0]dBm PASS
TotalTime:2.000 s\nTotal Result: PASS'''
    path='RF/PASS/MO26110261-00001_20261108_100000_DUT1_PASS.LOG'
    tb=prepare_tables(load_events(fixture_zip(tmp_path,[(path,log)]),{}))
    assert tb['05_RF板測'][0][1]=='MO26110261-00001'

def test_open_fail_before_retests_before_first_pass(tmp_path):
    files=[
      ('BOB調試/Success/344763060001-pass12026-11-10T100000.txt','校准完成 接收光功率 保存FLASH'),
      ('BOB調試/Fail/344763060002-fail12026-11-10T100000.txt','校准完成 接收光功率 保存FLASH'),
      ('BOB調試/Fail/344763060003-fail12026-11-10T100000.txt','校准完成 接收光功率 保存FLASH'),
      ('BOB調試/Success/344763060003-pass12026-11-10T100100.txt','校准完成 接收光功率 保存FLASH')]
    tb=prepare_tables(load_events(fixture_zip(tmp_path,files),{}))
    ids=[r[1] for r in tb['03_BOB調試']]
    assert ids==['344763060002','344763060003','344763060001']
    st=tb['01_各站統計'][0]
    assert st[3]==3 and st[7]==2 and st[10]==1 and st[14]==1
    assert abs(st[15]-33.33)<.01

def test_output_structure(tmp_path):
    # Small fixture verifies actual 32-sheet Excel output, not only table dicts.
    from analyzer import analyze
    file=fixture_zip(tmp_path,[('BOB調試/Success/344763060001-pass12026-11-10T100000.txt','校准完成 接收光功率 保存FLASH')])
    result=tmp_path/'output.xlsx';analyze(file,result)
    with zipfile.ZipFile(result) as z:
       assert z.testzip() is None
       assert len(sheet_paths(z))==32
       assert b'veryHidden' in z.read('xl/workbook.xml')
       assert b'344763060001' in z.read('xl/worksheets/sheet4.xml')
