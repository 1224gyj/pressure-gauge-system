"""使用四张现有真值图检查目录迁移后的识别结果，不写读数 Excel。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,str(ROOT/'src/recognition'))
import 读数增强 as reader
from ultralytics import YOLO

mk = YOLO(reader.RD.MK_PT)
mp = YOLO(reader.RD.MP_PT)
expected = {'1.jpg':7.76,'2.jpg':5.32,'3.jpg':8.72,'4.jpg':3.81}
for name, previous in expected.items():
    path=ROOT/'data/input'/name
    result=reader.read_one(mk,mp,path)
    assert result.get('ok') and result['value']==previous,(name,result)
    print('[通过] %s：%.2f kPa，与整理前一致' % (name,result['value']),flush=True)
