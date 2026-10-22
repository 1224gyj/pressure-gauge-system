import datetime as dt
import importlib.util
import json
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src/pipeline'))
from triggered_capture import run_triggered, pending_reports
import triggered_capture
from live_store import LiveStore

spec = importlib.util.spec_from_file_location('camera_history_test', ROOT/'src/camera/read_camera.py')
camera = importlib.util.module_from_spec(spec)
spec.loader.exec_module(camera)
stream = camera.RtspFrameStream('unused').enable_history(120, 100)
with patch.object(camera.time, 'time', side_effect=[100., 101., 102., 230.]):
    for value in [b'a', b'b', b'c', b'd']:
        stream._publish(value)
assert stream.historical_frame(100, 1) is None
assert stream.historical_frame(230.1, 1) == (b'd', 230.)
assert stream.historical_frame(240, 1) is None
tiny = camera.RtspFrameStream('unused').enable_history(120, 2)
tiny._publish(b'aa'); tiny._publish(b'bb')
assert tiny._history_bytes == 2 and len(tiny._history) == 1

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    def write(name, data):
        p = root/name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding='utf-8')
    now = time.time()
    stamps = [str(int((now-10)*1e6)), str(int((now-5)*1e6))]
    reports = [{'vendorRecvAtUs':s,'vendorReceivedAt':dt.datetime.fromtimestamp(int(s)/1e6,dt.timezone.utc).isoformat(), 'serverReceivedAt':dt.datetime.now(dt.timezone.utc).isoformat()} for s in stamps]
    write('config/transmitter.json', {'vendorCacheFile':'data/vendor.json'})
    write('data/vendor.json', {'records':reports})
    write('config/live.json', {'meter':{'id':'YB-106'},'readingsFile':'data/live/readings.json','statusFile':'data/live/status.json','pairingToleranceSeconds':1})
    cfg = {'read':{'trigger_activated_at':dt.datetime.fromtimestamp(now-20,dt.timezone.utc).isoformat()},'output':{'image_dir':'data/captures','keep_images':True}}
    class Stream:
        alive=True
        created=0
        def __init__(self,*args): Stream.created+=1
        def enable_history(self,*args): return self
        def start(self): return self
        def grab(self,**kwargs): return b'jpeg',now
        def historical_frame(self,target,tolerance): return (b'jpeg',target+.2) if abs(target-int(stamps[0])/1e6)<.01 else None
        def stop(self): self.alive=False
    calls=[]
    def recognize(gr,mk,mp,image,captured,seq,debug):
        calls.append(str(image))
        return {'seq':seq,'t_cap':captured,'name':image.name,'value':8.7,'conf':.9,'raw_d':10,'source':'test','status':'ok','cost':0}
    pipe=types.SimpleNamespace(PROJECT_ROOT=root,resolve=lambda name:root/name,excel_path_of=lambda *args:root/'out.xlsx',read_and_record=recognize,merge_rows=lambda old,new,*args:new,write_excel=lambda *args:None)
    rc=types.SimpleNamespace(RtspFrameStream=Stream,_STREAMS=[])
    original_read=triggered_capture.read_json
    locked_once=[False]
    def intermittent_read(file, fallback):
        if str(file).endswith('data\\vendor.json') or str(file).endswith('data/vendor.json'):
            if not locked_once[0]:
                locked_once[0]=True
                raise triggered_capture.RetryReadError('simulated Windows file lock')
        return original_read(file,fallback)
    with patch.object(triggered_capture,'read_json',side_effect=intermittent_read):
        assert run_triggered(pipe,rc,None,None,None,types.SimpleNamespace(count=2),cfg,'unused',None) == 0
    assert locked_once[0] and Stream.created==1, 'file lock must not kill or reconnect the stream'
    result=json.loads((root/'data/live/readings.json').read_text())['readings']
    assert len(calls)==1 and len(result)==2
    assert result[0]['transmitterStamp']==stamps[0] and abs(result[0]['frameTimeDiffSeconds']-.2)<.001
    assert result[1]['triggerStatus']=='missing_frame' and result[1]['valueKpa'] is None and result[1]['image'] is None
    ledger=json.loads((root/'data/research/trigger-ledger.json').read_text())['done']
    assert not pending_reports(reports,ledger,now-20)
    assert not pending_reports(reports,{},now)
    store=LiveStore(root)
    row={'t_cap':reports[0]['vendorReceivedAt'],'value':8.7,'status':'ok','transmitterStamp':stamps[0]}
    store.publish(row)
    assert len(json.loads(store.readings_path.read_text())['readings'])==2
print('PASS: bounded history, nearest frame, stale/missing frame, one inference/report, persistent deduplication, activation boundary and cleanup')
with patch.object(Path,'read_text',side_effect=[PermissionError('busy'),'{"ok":true}']):
    assert triggered_capture.read_json('unused',{}) == {'ok':True}
print('PASS: transient file locks retry, sustained lock preserves stream and resumes triggering')
