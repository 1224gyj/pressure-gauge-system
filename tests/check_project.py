"""检查整理后的模块、配置和资源路径，不联网、不修改实际数据。"""
import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding='utf-8')
errors = []

def verify(label, condition):
    print(('[通过] ' if condition else '[失败] ') + label)
    if not condition:
        errors.append(label)

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def main():
    for relative in ['src/camera/read_camera.py','src/pipeline/流水线.py',
                     'src/recognition/读数增强.py','src/recognition/读数_0到16kPa.py',
                     'src/recognition/标定.py','src/data_sync/填整点数据.py']:
        p = ROOT / relative
        ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
        verify(relative + ' 语法与文件位置', True)
    pipe = load('organized_pipeline', 'src/pipeline/流水线.py')
    camera, original, enhanced = pipe.load_parts()
    verify('流水线能加载摄像头、原读数器和增强读数器', all(x is not None for x in [camera,original,enhanced]))
    verify('模型权重路径', all(Path(p).is_file() for p in [original.MK_PT,original.MP_PT]))
    cfg = pipe.load_config()
    verify('摄像头输出目录', pipe.resolve(cfg['output']['image_dir']) == ROOT / 'data/captures')
    verify('读数记录路径', pipe.resolve(cfg['output']['excel']) == ROOT / 'data/readings/读数记录.xlsx')
    calibration = load('organized_calibration','src/recognition/标定.py')
    verify('标定图片及增强脚本路径', calibration.PICS == ROOT / 'data/input' and calibration.ENH.is_file())
    bridge = load('organized_bridge','src/data_sync/填整点数据.py')
    verify('变送器对比表和识别器路径', bridge.DEFAULT_XLSX.is_file() and (bridge.DEFAULT_PIPE/'读数增强.py').is_file())
    sync = json.loads((ROOT/'config/sync.json').read_text(encoding='utf-8'))
    verify('前端监听文件与台账', (ROOT/sync['source']).is_file() and (ROOT/sync['seedGauges']).is_file())
    verify('前端与便携运行工具', all((ROOT/p).is_file() for p in ['web/index.html','web/js/xlsx.full.min.js','runtime/node/node.exe','runtime/camera_python/python.exe']))
    print('检查完成，失败项：%d' % len(errors))
    return 1 if errors else 0

if __name__ == '__main__':
    raise SystemExit(main())
