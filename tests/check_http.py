"""使用临时空闲端口验证前端和只读 API，完成后停止本脚本创建的服务。"""
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding='utf-8')
config = json.loads((ROOT/'config/sync.json').read_text(encoding='utf-8'))
source = ROOT/config['source']
original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
log = ROOT/'tests/http-check.log'
with log.open('w',encoding='utf-8') as output:
    service = subprocess.Popen([str(ROOT/'runtime/node/node.exe'),str(ROOT/'src/data_sync/server.js'),str(port)],
                              cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        base = 'http://127.0.0.1:%d' % port
        for attempt in range(40):
            try:
                with urllib.request.urlopen(base+'/api/status',timeout=1) as res:
                    status=json.load(res)
                break
            except OSError:
                if service.poll() is not None:
                    raise RuntimeError('服务启动失败：'+log.read_text(encoding='utf-8'))
                time.sleep(.1)
        else:
            raise RuntimeError('服务未及时启动')
        assert status['exists'] and not status['lastError'],status
        for relative in ['/','/css/style.css','/js/core.js','/js/sync.js','/js/xlsx.full.min.js']:
            with urllib.request.urlopen(base+relative,timeout=3) as res:
                assert res.status==200 and len(res.read())>0,relative
            print('[通过] HTTP 静态资源 '+relative)
        with urllib.request.urlopen(base+'/api/data',timeout=3) as res:
            data=json.load(res)
        assert data['version']>0 and data['gauges'],data
        allowed=config.get('allowedGaugeIds')
        if allowed is not None:
            assert {g['id'] for g in data['gauges']}==set(allowed),data['gauges']
            assert all(r['gaugeId'] in allowed for r in data['readings']),data['readings']
            print('[通过] 台账及历史同步只保留指定测试表 '+','.join(allowed))
        else:
            assert data['readings'],data
        assert Path(data['source']).resolve()==source.resolve()
        print('[通过] 实际对比表读取、台账及 API 数据，共 %d 条读数' % len(data['readings']))
        with urllib.request.urlopen(base+'/api/stream',timeout=3) as res:
            assert 'text/event-stream' in res.headers['Content-Type']
            first_lines=[]
            for _ in range(6):
                first_lines.append(res.readline().decode('utf-8'))
                if first_lines[-1].startswith('data:'):
                    break
            assert any('data:' in line for line in first_lines),first_lines
        print('[通过] SSE 实时推送接口')
        assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
        print('[通过] 对比表内容未被验证修改')
    finally:
        service.terminate()
        service.wait(timeout=5)
