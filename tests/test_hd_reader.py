"""高清摄像头整帧必须经过圆心修正，保留旧离线照片的兼容路径。"""
import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src/recognition'))
import 读数增强 as reader


class Points:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=np.float32)

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class Keypoints:
    def __init__(self, values):
        self.xy = [Points(values)]
        self.conf = [[.99, .99, .99, .99]]

    def __len__(self):
        return 1


def prediction(points):
    return types.SimpleNamespace(keypoints=Keypoints(points))


scale = prediction([[900,900],[1000,1000],[1600,900],[1700,1000]])
# High confidence, but wrong axis: the same failure mode as the real HD frame.
pointer = prediction([[1600,800],[800,450]])
empty = types.SimpleNamespace(keypoints=None)
image = np.zeros((1440,2560,3), dtype=np.uint8)
with patch.object(reader,'_predict',side_effect=[scale,pointer,empty,empty]) as predict, \
     patch.object(reader,'line_intersection',return_value=(1280.,700.)), \
     patch.object(reader,'find_circle',return_value=(1280.,700.,500.)):
    prep = reader._prepare(None,None,image,allow_legacy=False)
    assert prep['raw_d'] >= 700
    assert not prep['delegate'], 'HD video must not bypass enhanced localization'
    assert prep['center'] == (1280.,700.), 'the wrong model axis must be corrected'
    assert predict.call_count == 4, 'HD must attempt a second prediction on the dial crop'

with patch.object(reader,'_predict',side_effect=[scale,pointer]) as predict:
    prep = reader._prepare(None,None,np.zeros((2734,2734,3),dtype=np.uint8))
    assert prep['delegate'] and predict.call_count == 2

with patch.object(reader,'_predict',side_effect=[scale,pointer,empty,empty]), \
     patch.object(reader,'line_intersection',return_value=(1280.,700.)), \
     patch.object(reader,'find_circle',return_value=(1280.,700.,500.)):
    prep = reader._prepare(None,None,np.zeros((2734,2734,3),dtype=np.uint8),allow_legacy=False)
    assert not prep['delegate'], 'camera frames must use enhanced localization regardless of aspect ratio'

print('PASS: HD camera frame corrects the model axis and attempts dial crop; old offline images retain their route')
