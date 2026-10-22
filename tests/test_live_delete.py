"""隔离数据验证删除、撤销、并发追加及重启持久化；不修改实际采集记录。"""
import datetime as dt
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,str(ROOT/'src/pipeline'))
from live_store import LiveStore, atomic_json

fixtures=ROOT/'tests/fixtures'
fixtures.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='delete_test_',dir=str(fixtures)) as directory:
    folder=Path(directory).resolve()
    assert folder.parent==fixtures.resolve()
    config=json.loads((ROOT/'config/live.json').read_text(encoding='utf-8'))
    config['researchControlRoot']=str(folder)
    config['transmitterConfigFile']=None
    config['comparisonMode']='time'
    for key,name in [('readingsFile','readings.json'),('statusFile','status.json'),('comparisonsFile','comparisons.json')]:
        config[key]=str(folder/name)
    config['watchIntervalMs']=100
    config_path=folder/'config.json'
    atomic_json(config_path,config)
    store=LiveStore(ROOT,config_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base='http://127.0.0.1:%d'%port
    def request(route,body=None,expected=200):
        req=urllib.request.Request(base+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=4) as response:
                assert response.status==expected
                return json.load(response)
        except urllib.error.HTTPError as error:
            assert error.code==expected,(error.code,error.read())
            return json.load(error)
    def publish(value):
        return store.publish({'t_cap':dt.datetime.now(dt.timezone.utc).isoformat(),'value':value,'status':'ok'},ROOT/'data/input/1.jpg',origin='offline_test')
    def start():
        log=(folder/'server.log').open('a',encoding='utf-8')
        process=subprocess.Popen([str(ROOT/'runtime/node/node.exe'),str(ROOT/'src/data_sync/server.js'),str(port)],cwd=ROOT,
            env=dict(os.environ,LIVE_CONFIG_PATH=str(config_path)),stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        log.close()
        for _ in range(60):
            try:request('/api/live');return process
            except OSError:
                if process.poll() is not None:raise RuntimeError((folder/'server.log').read_text(encoding='utf-8'))
                time.sleep(.05)
        process.terminate();process.wait();raise RuntimeError('启动超时')
    service=start()
    try:
        a,b=publish(7.76),publish(5.32)
        stream=urllib.request.urlopen(base+'/api/live/stream',timeout=4)
        def event_count(count):
            for _ in range(20):
                line=stream.readline().decode('utf-8')
                if line.startswith('data:'):
                    snapshot=json.loads(line[5:])
                    if len(snapshot['readings'])==count:return snapshot
            raise AssertionError('SSE未更新')
        request('/api/live')
        event_count(2)
        deleted=request('/api/live/delete',{'sampleIds':[b['id'],b['id']]})
        assert deleted['count']==1 and deleted['snapshot']['latest']['id']==a['id']
        event_count(1)
        assert len(json.loads((folder/'readings.json').read_text(encoding='utf-8'))['readings'])==2
        assert (ROOT/'data/input/1.jpg').exists()
        print('[通过] 单条删除去重、SSE更新、最新读数回退，原记录和照片保留')
        request('/api/live/delete',{'sampleIds':[a['id'],'f'*32]},409)
        assert len(request('/api/live')['readings'])==1
        request('/api/live/delete',{'sampleIds':['../bad']},400)
        request('/api/live/delete',{'sampleIds':[]},400)
        print('[通过] 无效和过期批量请求不会部分删除')
        c=publish(0)
        assert [p['id'] for p in request('/api/live')['readings']]==[a['id'],c['id']]
        print('[通过] 摄像头继续追加时，已删记录不复现，零读数正常保留')
        # 模拟原采集文件滚动淘汰，仍应能从独立备份撤销。
        atomic_json(folder/'readings.json',{'readings':[c]})
        restored=request('/api/live/restore',{'batchId':deleted['snapshot']['lastDeletion']['id']})
        assert [p['id'] for p in restored['snapshot']['readings']]==[b['id'],c['id']]
        print('[通过] 原文件淘汰后，撤销仍能恢复备份记录')
        removed=request('/api/live/delete',{'sampleIds':[b['id'],c['id']]})
        assert removed['snapshot']['latest'] is None and not removed['snapshot']['readings']
        request('/api/live/restore',{'batchId':removed['snapshot']['lastDeletion']['id']})
        assert len(request('/api/live')['readings'])==2
        print('[通过] 批量删除至空、撤销恢复，最新读数同步更新')
        journal=folder/'deletions.json'
        saved=journal.read_bytes()
        journal.write_text('{broken',encoding='utf-8')
        assert request('/api/live')['problem']
        request('/api/live/delete',{'sampleIds':[c['id']]},409)
        assert journal.read_text(encoding='utf-8')=='{broken'
        journal.write_bytes(saved)
        request('/api/live/delete',{'sampleIds':[c['id']]})
        stream.close();service.terminate();service.wait(timeout=5)
        service=start()
        snapshot=request('/api/live')
        assert [p['id'] for p in snapshot['readings']]==[b['id']]
        assert snapshot['lastDeletion']['count']==1
        print('[通过] 损坏日志不被覆盖，服务重启后删除和撤销信息仍保留')
    finally:
        service.terminate();service.wait(timeout=5)
print('数据删除验证全部通过')
