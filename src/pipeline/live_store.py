"""实时读数独立落盘：原子 JSON 文件，供前端服务读取，不依赖 Excel 写入成功。"""
import datetime as dt
import json
import math
import os
import tempfile
import time
import uuid
from pathlib import Path

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.05)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

class LiveStore:
    def __init__(self, root, config='config/live.json'):
        self.root = Path(root).resolve()
        config_path = Path(config)
        if not config_path.is_absolute():
            config_path = self.root / config_path
        self.config = json.loads(config_path.read_text(encoding='utf-8'))
        self.readings_path = self.root / self.config['readingsFile']
        self.status_path = self.root / self.config['statusFile']
        self.limit = max(1, int(self.config.get('historyLimit',1000)))

    def set_status(self, state, message, **details):
        atomic_json(self.status_path, {'state':state,'message':message,
                    'updatedAt':dt.datetime.now(dt.timezone.utc).isoformat(), **details})

    def publish(self, row, image_path=None, origin='camera'):
        control_file = Path(self.config.get('researchControlRoot', self.root)) / 'data/research/control.json'
        if control_file.exists():
            control = json.loads(control_file.read_text(encoding='utf-8'))
            if control.get('paused', False):
                return None
        records = []
        if self.readings_path.exists():
            document = json.loads(self.readings_path.read_text(encoding='utf-8'))
            records = document['readings']
            if not isinstance(records,list):
                raise ValueError('实时记录文件格式不正确，未覆盖已有数据')
        value = row.get('value')
        if value is not None:
            value = float(value)
            if not math.isfinite(value):
                value = None
        captured = dt.datetime.fromisoformat(row['t_cap'])
        if captured.tzinfo is None:
            captured = captured.astimezone()
        relative_image = None
        if image_path is not None:
            try:
                candidate = Path(image_path).resolve()
                candidate.relative_to(self.root / 'data')
                relative_image = candidate.relative_to(self.root).as_posix()
            except ValueError:
                pass
        sample = {'id':uuid.uuid4().hex,'meterId':self.config['meter']['id'],
                  'capturedAt':captured.isoformat(),'receivedAt':dt.datetime.now(dt.timezone.utc).isoformat(),
                  'valueKpa':value,'status':row.get('status','ok'),
                  'note':row.get('note'),'source':row.get('source'),
                  'image':relative_image,'origin':origin}
        if row.get('transmitterStamp'):
            sample.update({key:row.get(key) for key in ['transmitterStamp','triggerReceivedAt','triggerStatus','frameTimeDiffSeconds']})
            sample['id'] = __import__('hashlib').md5((sample['meterId']+':'+str(row['transmitterStamp'])).encode()).hexdigest()
            if any(r.get('id') == sample['id'] for r in records):
                return next(r for r in records if r.get('id') == sample['id'])
        records.append(sample)
        atomic_json(self.readings_path, {'readings':records[-self.limit:]})
        self.set_status('running','正在采集和识别')
        return sample
