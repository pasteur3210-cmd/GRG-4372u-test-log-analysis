from analyzer import timestamp,id_from_name,rf_csv_rows,prepare_tables
from legacy_audit import stage_for

def test_station_from_content_ignores_folder():
 s='[写入个性化数据]测试:PASS\\n nvram set SerialNum=123\\n nvram set xPONSN=123'
 assert stage_for('random/test.txt',s)=='資訊寫入'
def test_timestamp():
 assert timestamp('MO26110261-00001_20261108_215221_DUT1_PASS.LOG').year==2026
def test_id():
 assert id_from_name('MO26110261-00001_20261108_215221_DUT1_PASS.LOG','RF板測')=='MO26110261-00001'
def test_mp_csv_thresholds():
 t=b'TestName,Band,FreqMHz,Antenna,LowerLimit,UpperLimit,TestValue,Unit,TestStatus\nEVM[11AX_HE20][MCS0],WIFI2G,F2442,ANT1,-55,-5,-20,dB,PASS\n'
 agg={};issues=[]
 assert rf_csv_rows(t,'RF/PASS/test.csv',agg,issues)==1
 assert list(agg.values())[0]['limits'][(-55.,-5.)]==1
 assert not issues
