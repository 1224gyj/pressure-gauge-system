"""实时监测集成验证：原子发布、SSE、手动参考值、故障及持久化，不连接摄像头。"""
import copy
import datetime as dt
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,str(ROOT/'src/pipeline'))
from live_store import LiveStore, atomic_json

passed=[]
def check(label, result):
    assert result,label
    passed.append(label)
    print('[通过] '+label,flush=True)

with tempfile.TemporaryDirectory(prefix='live_test_',dir=ROOT/'tests/fixtures') as temp:
    directory=Path(temp)
    directory.resolve().relative_to((ROOT/'tests/fixtures').resolve())
    config=json.loads((ROOT/'config/live.json').read_text(encoding='utf-8'))
    config['researchControlRoot']=str(directory)
    config['transmitterConfigFile']=None  # 独立测试不读取正式云端配置或密钥。
    config['comparisonMode']='time'  # 本文件覆盖旧手动参考值接口，次数配对由单独测试覆盖。
    for key,name in [('readingsFile','readings.json'),('statusFile','status.json'),('comparisonsFile','comparisons.json')]:
        config[key]=str(directory/name)
    config['watchIntervalMs']=100
    config_path=directory/'config.json'
    atomic_json(config_path,config)
    store=LiveStore(ROOT,config_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    env=dict(os.environ,LIVE_CONFIG_PATH=str(config_path))
    log=(directory/'server.log').open('w',encoding='utf-8')
    def start():
        process=subprocess.Popen([str(ROOT/'runtime/node/node.exe'),str(ROOT/'src/data_sync/server.js'),str(port)],
                    cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(60):
            try:
                request('/api/live'); return process
            except OSError:
                if process.poll() is not None: raise RuntimeError('实时服务启动失败')
                time.sleep(.05)
        process.terminate();process.wait();raise RuntimeError('服务启动超时')
    def request(relative, body=None):
        data=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request('http://127.0.0.1:%d%s'%(port,relative),data=data,
                                   headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5) as res:return json.load(res)
    def row(value=7.76,status='ok'):
        return {'t_cap':dt.datetime.now(dt.timezone.utc).isoformat(),'value':value,'status':status,'note':None,'source':'模型识别验证'}
    def sse_sample(stream,identifier):
        for _ in range(30):
            line=stream.readline().decode('utf-8')
            if line.startswith('data:'):
                data=json.loads(line[5:])
                if data.get('latest',{} ) and data['latest']['id']==identifier:return data
        raise AssertionError('SSE 未推送目标采集记录')
    process=start()
    try:
        check('初始化仅有一块表，等待真实数据', request('/api/live')['latest'] is None)
        stream=urllib.request.urlopen('http://127.0.0.1:%d/api/live/stream'%port,timeout=5)
        first=store.publish(row(),ROOT/'data/input/1.jpg',origin='offline_test')
        event=sse_sample(stream,first['id'])
        check('Python 发布后 SSE 自动推送，保留小数和采集时刻',event['latest']['valueKpa']==7.76 and event['latest']['capturedAt']==first['capturedAt'])
        check('没有变送器读数时保持待填写，不虚构零值',event['latest']['state']=='pending' and event['latest']['deltaKpa'] is None)
        with urllib.request.urlopen('http://127.0.0.1:%d/api/live/image?sampleId=%s'%(port,first['id'])) as image:
            check('照片与采集记录对应',image.read()==(ROOT/'data/input/1.jpg').read_bytes())
        saved=request('/api/live/transmitter',{'sampleId':first['id'],'valueKpa':7.66})
        check('手动参考读数保存并正确比较',saved['sample']['state']=='normal' and abs(saved['sample']['deltaKpa']-.10)<1e-9)
        saved=request('/api/live/transmitter',{'sampleId':first['id'],'valueKpa':7.50})
        check('引用误差计算保留正确的小数精度',saved['sample']['percentFS']==1.625)
        saved=request('/api/live/transmitter',{'sampleId':first['id'],'valueKpa':7.56})
        check('0.20 kPa 预警边界正确',saved['sample']['state']=='warning')
        saved=request('/api/live/transmitter',{'sampleId':first['id'],'valueKpa':7.36})
        check('0.40 kPa 报警边界正确',saved['sample']['state']=='alarm')
        saved=request('/api/live/transmitter',{'sampleId':first['id'],'valueKpa':0})
        check('零值变送器读数合法',saved['sample']['transmitterKpa']==0)
        second=store.publish(row(8.25),ROOT/'data/input/2.jpg',origin='offline_test')
        event=sse_sample(stream,second['id'])
        check('新抓拍自动更新，上一参考读数不会串到新照片',event['latest']['valueKpa']==8.25 and event['latest']['transmitterKpa'] is None)
        request('/api/live/transmitter',{'sampleId':first['id'],'valueKpa':7.50})
        latest=request('/api/live')
        check('填写过程中到达新照片，旧输入仍绑定旧采集',latest['latest']['transmitterKpa'] is None and latest['readings'][0]['transmitterKpa']==7.50)
        for body in [{'sampleId':second['id'],'valueKpa':None},{'sampleId':second['id'],'valueKpa':17},{'sampleId':'missing','valueKpa':7}]:
            try:request('/api/live/transmitter',body);raise AssertionError('无效输入被接受')
            except urllib.error.HTTPError as error:check('拒绝无效参考输入 '+str(body['valueKpa']),error.code in [400,409])
        failed=store.publish(row(None,'关键点没检出来'))
        check('识别失败明确展示，不保留假成功值',request('/api/live')['latest']['state']=='failed')
        review=store.publish(row(8.2,'需人工复核（针看不清）'))
        request('/api/live/transmitter',{'sampleId':review['id'],'valueKpa':8.2})
        check('低质量识别不能被参考读数掩盖成正常',request('/api/live')['latest']['state']=='review')
        stable=request('/api/live')['latest']['id']
        original=Path(config['readingsFile']).read_text(encoding='utf-8')
        Path(config['readingsFile']).write_text('{broken',encoding='utf-8')
        broken=request('/api/live')
        check('记录暂时损坏时保留上一有效数据并报告错误',broken['latest']['id']==stable and bool(broken['problem']))
        Path(config['readingsFile']).write_text(original,encoding='utf-8')
        check('记录恢复后自动恢复',request('/api/live')['problem'] is None)
        store.set_status('capture_error','未取得摄像头画面')
        check('抓图中断状态被推送',request('/api/live')['captureStatus']['state']=='capture_error')
        stream.close()
        process.terminate();process.wait(timeout=5)
        process=start()
        persisted=request('/api/live')
        check('重启服务后保留采集和手动输入',persisted['readings'][0]['transmitterKpa']==7.50 and len(persisted['readings'])==4)

        # 使用真实 mode_live 流程和模拟摄像头，Excel 写入故意失败。
        spec=importlib.util.spec_from_file_location('live_pipeline_test',ROOT/'src/pipeline/流水线.py')
        pipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(pipe)
        import cv2
        import types
        jpg=cv2.imencode('.jpg',cv2.imdecode(__import__('numpy').fromfile(str(ROOT/'data/input/1.jpg'),dtype='uint8'),cv2.IMREAD_COLOR))[1].tobytes()
        class Stream:
            stopped=False
            def __init__(self,*a):pass
            def start(self):return self
            def grab(self,**kw):return jpg,0
            def stop(self):self.stopped=True
        class Schedule:
            def describe(self):return '离线验证'
            def next_after(self,t):return None
        rc=types.SimpleNamespace(build_schedule_from_args=lambda a:(Schedule(),None),mask_uri=lambda *a:'offline-test',
            find_stream_holders=lambda *a:[],RtspFrameStream=Stream,_STREAMS=[])
        cfg=copy.deepcopy(pipe.DEFAULT_CONFIG)
        cfg['output'].update({'image_dir':str(directory/'captures'),'excel':str(directory/'blocked.xlsx'),'excel_mode':'new'})
        cfg['live']={'enabled':True,'config':str(config_path)}
        cfg['schedule']['first_shot_now']=True
        args=types.SimpleNamespace(burst=1,count=1,password='',excel=str(directory/'blocked.xlsx'))
        pipe.READER['mod']=types.SimpleNamespace()
        pipe.read_and_record=lambda _g,_k,_m,p,t,s,d:{'seq':s,'t_cap':t,'name':p.name,'value':7.76,'status':'ok','source':'offline-test','cost':0,'note':None}
        def blocked_excel(*a,**kw):raise PermissionError('模拟 Excel 无法写入')
        pipe.write_excel=blocked_excel
        try:
            pipe.mode_live(rc,None,None,None,args,cfg,'offline-test',None)
            raise AssertionError('模拟写入应失败')
        except PermissionError:pass
        final=request('/api/live')
        check('Excel 保存失败之前读数已经发布到前端',len(final['readings'])==5 and final['latest']['valueKpa']==7.76)
        check('异常时摄像头连接仍被清理',not rc._STREAMS)
        check('异常状态如实显示',final['captureStatus']['state']=='error')
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=5)
        log.close()
print('实时监测验证完成：%d 项通过'%len(passed))
