"""隔离验证：商家 POST → 云端保存 → 本机同步 → SSE/时间匹配；不连接实物。"""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
NODE=ROOT/'runtime/node/node.exe'
FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)
passed=[]
def check(label,result):
    assert result,label
    passed.append(label)
    print('[通过] '+label,flush=True)
def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
def write(file,doc):
    file.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
def request(base,relative,body=None,expected=200,token=None):
    data=None if body is None else json.dumps(body).encode()
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    req=urllib.request.Request(base+relative,data=data,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=4) as response:
            assert response.status==expected
            return json.load(response)
    except urllib.error.HTTPError as error:
        assert error.code==expected,(error.code,expected,error.read())
        return json.load(error)
def wait(fn):
    for _ in range(100):
        try:
            result=fn()
            if result:return result
        except (OSError,KeyError,TypeError):pass
        time.sleep(.06)
    raise AssertionError('等待接口状态超时')

with tempfile.TemporaryDirectory(prefix='transmitter_',dir=ROOT/'tests/fixtures') as temp:
    folder=Path(temp)
    cloud_port,local_port=port(),port()
    cloud='http://127.0.0.1:'+str(cloud_port)
    local='http://127.0.0.1:'+str(local_port)
    cloud_cfg=folder/'cloud.json'
    write(cloud_cfg,{'host':'127.0.0.1','port':cloud_port,'dataDir':'cloud-data','devices':{'20101':'YB-106'}})
    (folder/'read.key').write_text('isolated-read-key',encoding='utf-8')
    sync_cfg=folder/'transmitter.json'
    settings={'enabled':True,'serverUrl':cloud,'deviceId':'20101','readKeyFile':str(folder/'read.key'),
        'cacheFile':str(folder/'cache.json'),'pollIntervalMs':500,'timeoutMs':800,'staleAfterSeconds':60,'maxTimeDiffSeconds':15}
    write(sync_cfg,settings)
    cfg=json.loads((ROOT/'config/live.json').read_text(encoding='utf-8'))
    cfg['researchControlRoot']=str(folder)
    cfg['recognitionMode']='scheduled'
    cfg['comparisonMode']='time'  # 保留时间配对路径回归；本文件末尾再验证次数配对。
    cfg['meter']['unit']='kPa'  # 该隔离测试的历史夹具按 kPa 编写。
    cfg['pairingToleranceSeconds']=15
    cfg.update({'readingsFile':str(folder/'readings.json'),'statusFile':str(folder/'status.json'),
        'comparisonsFile':str(folder/'comparisons.json'),'transmitterConfigFile':str(sync_cfg),'watchIntervalMs':100})
    write(folder/'live.json',cfg)
    stamp=time.time_ns()//1000
    def iso(us):return dt.datetime.fromtimestamp(us/1e6,dt.timezone.utc).isoformat(timespec='milliseconds')
    sample={'id':'a'*32,'meterId':'YB-106','capturedAt':iso(stamp),'valueKpa':7.76,'status':'ok','origin':'camera'}
    write(folder/'readings.json',{'readings':[sample]})
    logs=[]
    def spawn(args,env):
        log=(folder/('log'+str(len(logs))+'.txt')).open('w',encoding='utf-8');logs.append(log)
        return subprocess.Popen([str(NODE)]+list(map(str,args)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=FLAGS)
    cloud_process=spawn([ROOT/'src/transmitter/receiver.js',cloud_cfg],dict(os.environ,PUSH_KEY='isolated-push-key',READ_KEY='isolated-read-key'))
    local_process=None
    def live():return request(local,'/api/live')
    def packet(us=stamp,value=750,dot=5,unit=1,device=20101):
        p={'devID':device,'recvAt':us,'value':value,'dot':dot,'unit':unit}
        p['auth']=hashlib.md5((str(us)+'isolated-push-key').encode()).hexdigest();return p
    def push(p,expected=200):return request(cloud,'/api/transmitter/push',p,expected)
    try:
        wait(lambda:request(cloud,'/health'))
        request(cloud,'/api/transmitter/readings?deviceId=20101',expected=401)
        check('云端只读接口拒绝无密钥访问',True)
        push(dict(packet(),auth='0'*32),401)
        push(packet(device=99999),403)
        check('推送校验商家 MD5 和设备 ID',True)
        local_process=spawn([ROOT/'src/data_sync/server.js',local_port],dict(os.environ,LIVE_CONFIG_PATH=str(folder/'live.json')))
        wait(lambda:live()['transmitter']['status']=='connected')
        check('未收到设备数据时不虚构读数或偏差',live()['transmitter']['latest'] is None and live()['latest']['deltaKpa'] is None)
        stream=urllib.request.urlopen(local+'/api/live/stream',timeout=5)
        push(packet())
        event=None
        for _ in range(50):
            line=stream.readline().decode('utf-8')
            if line.startswith('data:'):
                doc=json.loads(line[5:])
                if doc.get('transmitter',{}).get('latest'):event=doc;break
        stream.close()
        check('设备推送经过本机同步后通过 SSE 更新',event is not None and event['transmitter']['latest']['valueKpa']==7.5)
        check('MPa、小数位、微秒时间戳换算正确',event['latest']['transmitterKpa']==7.5 and event['latest']['transmitterRecordedAt']==iso(stamp).replace('+00:00','Z'))
        check('偏差与引用误差使用对应时段的变送器值',event['latest']['deltaKpa']==.26 and event['latest']['percentFS']==1.625)
        check('实时接口独立公开已校验历史及准确条数',event['transmitter']['recordCount']==1 and len(event['transmitter']['readings'])==1 and 'raw' not in event['transmitter']['readings'][0])
        write(folder/'readings.json',{'readings':[]})
        standalone=live()
        check('没有摄像头记录时仍提供变送器读数与历史',standalone['latest'] is None and standalone['transmitter']['latest']['valueKpa']==7.5 and standalone['transmitter']['recordCount']==1)
        write(folder/'readings.json',{'readings':[sample]})
        duplicate=push(packet(value=999))
        check('重复推送不重复入库，也不覆盖原始读数',duplicate['duplicate'] and len(list((folder/'cloud-data/history/20101').glob('*.json')))==1)
        push(packet(stamp+40_000_000,value=800))
        event=wait(lambda:(d if d['transmitter']['latest']['valueKpa']==8 else None) if (d:=live()) else None)
        check('识别停止时变送器仍独立更新，偏差保留正确匹配值',event['latest']['id']==sample['id'] and event['latest']['transmitterKpa']==7.5 and event['transmitter']['latest']['valueKpa']==8)
        late=stamp-20_000_000
        push(packet(late,value=700))
        wait(lambda:len(json.loads((folder/'cache.json').read_text(encoding='utf-8'))['readings'])==3)
        check('乱序上报保留历史且不倒退最新读数',live()['transmitter']['latest']['valueKpa']==8)
        rows=[]
        for index,offset in enumerate([15,15.001,100]):
            rows.append(dict(sample,id=hex(index+1)[2:]*32,capturedAt=iso(stamp+int(offset*1e6))))
        write(folder/'readings.json',{'readings':rows})
        by_id={r['id']:r for r in live()['readings']}
        check('15 秒边界可以匹配，超过边界不比较',by_id['1'*32]['transmitterKpa']==7.5 and by_id['2'*32]['transmitterKpa'] is None and by_id['3'*32]['deltaKpa'] is None)
        rows.append(dict(sample,id='b'*32,status='需人工复核'))
        rows.append(dict(sample,id='c'*32,valueKpa=None,status='failed'))
        write(folder/'readings.json',{'readings':rows})
        by_id={r['id']:r for r in live()['readings']}
        check('识别需复核或失败时不会显示为正常',by_id['b'*32]['state']=='review' and by_id['c'*32]['state']=='failed')
        push(packet(stamp+50_000_000,value=0))
        check('合法零值可实时显示',wait(lambda:live()['transmitter']['latest']['valueKpa']==0))
        push(packet(stamp+60_000_000,unit=14))
        check('未知压力单位保留原始数据，不伪造 kPa',wait(lambda:(d:=live())['transmitter']['latest']['unitCode']==14 and d['transmitter']['latest']['valueKpa'] is None))
        old=live()['transmitter']['latest']
        cloud_process.terminate();cloud_process.wait(timeout=5)
        check('服务器中断明确报错并保留历史数据',wait(lambda:(d:=live())['transmitter']['status']=='error' and d['transmitter']['latest']==old))
        local_process.terminate();local_process.wait(timeout=5)
        local_process=spawn([ROOT/'src/data_sync/server.js',local_port],dict(os.environ,LIVE_CONFIG_PATH=str(folder/'live.json')))
        check('本机重启后仍能读取缓存且不冒充连接成功',wait(lambda:(d:=live())['transmitter']['latest']==old and d['transmitter']['status']!='connected'))
        serialized=json.dumps(live())
        check('前端数据不含推送/只读密钥和原始鉴权字段','isolated-read-key' not in serialized and 'isolated-push-key' not in serialized and '"auth"' not in serialized)
        cloud_process=spawn([ROOT/'src/transmitter/receiver.js',cloud_cfg],dict(os.environ,PUSH_KEY='isolated-push-key',READ_KEY='isolated-read-key'))
        check('云端恢复后自动重新连接',wait(lambda:live()['transmitter']['status']=='connected'))
        # 切换为本次用户要求的按次数配对，仍只使用隔离端口与模拟数据。
        cfg['comparisonMode']='sequence'
        write(folder/'live.json',cfg)
        local_process.terminate();local_process.wait(timeout=5)
        local_process=spawn([ROOT/'src/data_sync/server.js',local_port],dict(os.environ,LIVE_CONFIG_PATH=str(folder/'live.json')))
        wait(lambda:live()['transmitter']['status']=='connected')
        indexed=live()
        check('按次数模式对外提供统一对比行',indexed['comparisonMode']=='sequence' and len(indexed['comparisonRows'])==max(len(indexed['readings']),len(indexed['transmitter']['readings'])))
        for index,pair in enumerate(indexed['comparisonRows']):
            assert pair['sequence']==index+1
            assert pair['recognitionId']==indexed['readings'][index]['id']
            assert pair['transmitterKpa']==indexed['transmitter']['readings'][index]['valueKpa']
        check('失败和未知单位保留次数，不压缩错配',indexed['pairedCount']==3 and indexed['comparisonRows'][1]['state']=='failed' and indexed['comparisonRows'][4]['deltaKpa'] is None)
        extra=dict(sample,id='d'*32,capturedAt=iso(stamp+101_000_000),valueKpa=9.1)
        write(folder/'readings.json',{'readings':rows+[extra]})
        pending=live()
        check('单侧先到时保留缺项，偏差卡片仍指向最近有效配对',pending['latest']['transmitterKpa'] is None and pending['comparisonRows'][-1]['pairingStatus']=='waiting_transmitter' and pending['latestComparison']['sequence']==4)
        push(packet(stamp+102_000_000,value=900,dot=2,unit=2))
        paired=wait(lambda:(d if d['latestComparison'] and d['latestComparison']['sequence']==6 else None) if (d:=live()) else None)
        check('另一侧到达后自动计算该次偏差',paired['latestComparison']['deltaKpa']==.1 and paired['latest']['deltaKpa']==.1 and paired['latest']['transmitterTimeDiffSeconds'] is None)
        prior=[(r['recognitionId'],r['transmitterStamp']) for r in paired['comparisonRows']]
        removed=request(local,'/api/live/delete',{'sampleIds':[paired['readings'][0]['id']]})
        check('删除识别记录后次数与配对重新计算',removed['snapshot']['comparisonRows'][-1]['pairingStatus']=='waiting_recognition' and len(removed['snapshot']['readings'])==5)
        restored=request(local,'/api/live/restore',{'batchId':removed['snapshot']['lastDeletion']['id']})['snapshot']
        check('撤销删除恢复原来的次数配对',[(r['recognitionId'],r['transmitterStamp']) for r in restored['comparisonRows']]==prior)
    finally:
        for process in [local_process,cloud_process]:
            if process and process.poll() is None:process.terminate();process.wait(timeout=5)
        for log in logs:log.close()
print('变送器全链路隔离验证：%d 项通过'%len(passed))
