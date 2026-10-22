"""Minute research schedule; reload settings while waiting without opening extra streams."""
import datetime
import json
import math
import time
from pathlib import Path


def next_capture(config_file, last_timestamp, now=None):
    now = time.time() if now is None else now
    with open(config_file, encoding='utf-8') as handle:
        schedule = json.load(handle)['schedule']
    interval = float(schedule['every'])
    if interval < 60:
        raise ValueError('研究采集周期至少 60 秒')
    phase = float(schedule.get('phase_seconds', 0)) % interval
    # Follow the phase of recent platform receipts, without claiming device-time sync.
    if schedule.get('follow_vendor_phase', False):
        try:
            root = Path(config_file).resolve().parent.parent
            transmitter = json.loads((root / 'config/transmitter.json').read_text(encoding='utf-8'))
            cache = json.loads((root / transmitter.get('vendorCacheFile', 'data/transmitter/vendor-http.json')).read_text(encoding='utf-8'))
            latest = cache['records'][-1]
            received = int(latest['vendorRecvAtUs']) / 1000000
            if 0 <= now-received <= interval*3:
                phase = math.floor(received) % interval
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            pass
    boundary = max(last_timestamp, now if last_timestamp == 0 else last_timestamp)
    target = (math.floor((boundary-phase)/interval)+1)*interval+phase
    if target < now - 1.5:
        target = (math.floor((now-phase)/interval)+1)*interval+phase
    return target


def wait_capture(config_file, last_timestamp):
    last_target = None
    while True:
        stop_file = Path(config_file).resolve().parent.parent / 'data/research/stop-capture.flag'
        if stop_file.exists():
            stop_file.unlink()
            raise KeyboardInterrupt
        control_file = stop_file.parent / 'control.json'
        try:
            control = json.loads(control_file.read_text(encoding='utf-8'))
        except FileNotFoundError:
            control = {}
        if control.get('paused', False):
            last_target = None
            time.sleep(.5)
            continue
        target = next_capture(config_file, last_timestamp)
        # Keep a due target; recomputing against now must not skip it at the boundary.
        if last_target is not None and abs(target-last_target) > .001:
            if time.time() >= last_target and time.time()-last_target < 1.5:
                return datetime.datetime.fromtimestamp(last_target)
        last_target = target
        remaining = target-time.time()
        if remaining <= 0:
            return datetime.datetime.fromtimestamp(target)
        time.sleep(min(remaining, .5))
