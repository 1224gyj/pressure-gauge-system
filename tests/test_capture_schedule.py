import importlib.util
import json
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location('capture_schedule', Path(__file__).resolve().parents[1]/'src/pipeline/capture_schedule.py')
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    config = root/'pipeline.json'
    config.write_text(json.dumps({'schedule': {'every': '60'}}))
    assert schedule.next_capture(config, 120, now=130) == 180
    assert schedule.next_capture(config, 120, now=180.1) == 180
    assert schedule.next_capture(config, 120, now=250) == 300
    config.write_text(json.dumps({'schedule': {'every': '120'}}))
    assert schedule.next_capture(config, 120, now=130) == 240
    config.write_text(json.dumps({'schedule': {'every': '30'}}))
    try:
        schedule.next_capture(config, 120, now=130)
        raise AssertionError('short interval accepted')
    except ValueError:
        pass
print('PASS: clock alignment, due boundary, skipped slots, live period changes and minimum interval')
