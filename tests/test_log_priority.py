import zipfile, json
from pathlib import Path
from analyzer import load_events, prepare_tables, hide_internal_sheet

def test_log_is_primary_even_if_csv_conflicts(tmp_path):
    log='''[10:00:00.000]Load driver (MeasureSuite.ScanData.dll)
[10:00:00.000]扫描DUT条形码 Start
[10:00:01.000][2G TX测试][2412.00][11AX_HE20][MCS0][ANT1]
[10:00:01.001]MeasurePower :[ 18.0000 ]<[ 19.0000 ]<[ 20.0000 ]dBm PASS
[10:00:02.000]TotalTime:2.000 s
Total Result: PASS
'''
    csv='TestName,Band,FreqMHz,Antenna,LowerLimit,UpperLimit,TestValue,Unit,TestStatus\nMeasurePower,2G,2412,ANT1,25,30,19,dBm,FAIL\n'
    src=tmp_path/'fixture.zip'
    with zipfile.ZipFile(src,'w') as z:
        z.writestr('other/MO26110261-00001_20261108_100000_DUT1_PASS.LOG',log)
        z.writestr('RF/PASS/MO26110261-00001_20261108_100000_DUT1_PASS.csv',csv)
    data=load_events(src,{})
    assert len(data['events'])==1
    assert len(data['rf_measures'])==1
    assert data['rf_measures'][0]['low']==18
    assert data['rf_measures'][0]['high']==20
    assert data['rf_measures'][0]['value']==19
    assert data['raw_csv_count']==1
    stats=prepare_tables(data)
    assert len(stats['21_RF_LOG規格主表'])==1
    assert stats['21_RF_LOG規格主表'][0][6:8]==[18,20]

from analyzer import result
def test_explicit_log_status_overrides_folder():
    assert result('RF/PASS/example_PASS.LOG','Total Result: FAIL')=='FAIL'
