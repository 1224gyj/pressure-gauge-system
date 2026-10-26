"""One vendor report -> one recognition from timestamped JPEG history."""
import datetime as dt
import json
import time
from pathlib import Path
from live_store import LiveStore, atomic_json


class RetryReadError(Exception):
    pass


def read_json(file, fallback):
    for attempt in range(8):
        try:
            return json.loads(Path(file).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return fallback
        except (PermissionError, json.JSONDecodeError) as exc:
            if attempt == 7:
                raise RetryReadError('数据文件暂时被占用或正在更新：'+Path(file).name) from exc
            time.sleep(.05)


def pending_reports(records, done, boundary):
    return sorted((r for r in records if r['vendorRecvAtUs'] not in done
                   and int(r['vendorRecvAtUs'])/1e6 > boundary), key=lambda r: int(r['vendorRecvAtUs']))


def stream_needs_reconnect(stream, max_frame_age=15):
    if stream is None or not stream.alive:
        return True
    if not hasattr(stream, 'history_status'):
        return False
    newest = stream.history_status().get('newestFrameAt')
    return newest is None or time.time()-newest > max_frame_age


def run_triggered(pipe, rc, gr, mk, mp, args, cfg, uri, debug_dir):
    root = pipe.PROJECT_ROOT
    live = LiveStore(root, cfg.get('live', {}).get('config', 'config/live.json'))
    tx = read_json(root/'config/transmitter.json', {})
    cache = root/tx.get('vendorCacheFile', 'data/transmitter/vendor-http.json')
    control_file = root/'data/research/control.json'
    ledger_file = root/'data/research/trigger-ledger.json'
    ledger = read_json(ledger_file, {'done': {}})
    done = ledger['done']
    activation = dt.datetime.fromisoformat(cfg['read']['trigger_activated_at']).timestamp()
    seconds = int(cfg['read'].get('frame_cache_seconds', 120))
    max_bytes = int(cfg['read'].get('frame_cache_max_mb', 64))*1024*1024
    fps = int(cfg['read'].get('frame_cache_fps', 2))
    output = pipe.resolve(cfg['output']['image_dir'])
    output.mkdir(parents=True, exist_ok=True)
    excel = pipe.excel_path_of(cfg, args)
    history = pipe.load_excel_rows(excel) if cfg['output'].get('excel_mode') == 'append' else []
    rows = []
    stream = None
    live.set_status('starting', '正在建立历史画面缓存')
    print('报文触发识别：%d 秒画面缓存，%d 帧/秒，最多 %d MB；不再独立定时识别。' % (seconds, fps, max_bytes//1024//1024), flush=True)
    try:
        stream = rc.RtspFrameStream(uri, fps).enable_history(seconds, max_bytes).start()
        rc._STREAMS.append(stream)
        if stream.grab(timeout=20)[0] is None:
            raise RuntimeError('摄像头未取得画面，无法建立历史缓存')
        live.set_status('running', '正在缓存画面，等待新的变送器报文')
        last_heartbeat = 0
        reconnect_at = 0
        while True:
            try:
                stop = root/'data/research/stop-capture.flag'
                if stop.exists():
                    stop.unlink()
                    break
                control = read_json(control_file, {})
                # FFmpeg can remain alive after a camera/network interruption
                # without producing JPEGs. Process liveness alone is insufficient.
                if stream_needs_reconnect(stream):
                    if time.monotonic() < reconnect_at:
                        time.sleep(.3)
                        continue
                    live.set_status('capture_error', '摄像头画面停止更新，正在自动重连；等待新画面建立缓存')
                    print('摄像头画面停止更新，正在自动重连', flush=True)
                    if stream:
                        stream.stop()
                        if stream in rc._STREAMS:
                            rc._STREAMS.remove(stream)
                    stream = None
                    try:
                        stream = rc.RtspFrameStream(uri, fps).enable_history(seconds, max_bytes).start()
                        rc._STREAMS.append(stream)
                        if stream.grab(timeout=10)[0] is None:
                            raise RuntimeError('重连后未收到画面')
                        print('摄像头取流已恢复，继续缓存画面', flush=True)
                        last_heartbeat = 0
                    except Exception:
                        if stream:
                            stream.stop()
                            if stream in rc._STREAMS:
                                rc._STREAMS.remove(stream)
                        stream = None
                        reconnect_at = time.monotonic()+5
                        live.set_status('capture_error', '摄像头重连尚未取得画面，5 秒后自动重试；请检查供电和网线')
                        continue
                if time.monotonic()-last_heartbeat > 10:
                    live.set_status('paused' if control.get('paused') else 'running',
                                    '缓存画面中；识别已暂停' if control.get('paused') else '缓存画面中，等待新报文触发识别',
                                    frameCache=stream.history_status() if hasattr(stream,'history_status') else {})
                    last_heartbeat = time.monotonic()
                if control.get('paused'):
                    time.sleep(.3)
                    continue
                boundary = max(activation, dt.datetime.fromisoformat(control['since']).timestamp() if control.get('since') else 0)
                document = read_json(cache, {'records': []})
                if document.get('identity') and not document['identity'].startswith(str(tx.get('vendorBaseUrl', '')).rstrip('/')+'|'+str(tx.get('projectId'))+'|'+str(tx.get('deviceId'))+'|'):
                    raise ValueError('厂家缓存与当前设备配置不一致')
                for report in pending_reports(document.get('records', []), done, boundary):
                    control = read_json(control_file, {})
                    if control.get('paused'):
                        break
                    if control.get('since') and int(report['vendorRecvAtUs'])/1e6 <= dt.datetime.fromisoformat(control['since']).timestamp():
                        continue
                    stamp = report['vendorRecvAtUs']
                    # Recover after a crash between publishing a result and marking its trigger.
                    published = read_json(live.readings_path, {'readings': []})['readings']
                    if any(r.get('transmitterStamp') == stamp for r in published):
                        done[stamp] = 'already_published'
                        atomic_json(ledger_file, ledger)
                        continue
                    settings = read_json(root/'config/live.json', {})
                    tolerance = float(settings.get('pairingToleranceSeconds', 10))
                    target = int(stamp)/1e6
                    selected = stream.historical_frame(target, tolerance)
                    if selected is None:
                        row = {'seq':len(rows)+1,'t_cap':report['vendorReceivedAt'],'name':'report_'+stamp,
                               'value':None,'conf':None,'raw_d':None,'status':'缺少对应历史画面','cost':0,'source':'报文触发',
                               'note':'缓存尚未覆盖、画面已过期或时间差超出容差；未使用当前照片替代。'}
                        image = None
                        row['triggerStatus'] = 'missing_frame'
                        row['frameTimeDiffSeconds'] = None
                    else:
                        jpg, captured = selected
                        image = output/('report_'+stamp+'.jpg')
                        image.write_bytes(jpg)
                        captured_at = dt.datetime.fromtimestamp(captured, dt.timezone.utc).isoformat(timespec='milliseconds')
                        row = pipe.read_and_record(gr, mk, mp, image, captured_at, len(rows)+1, debug_dir, camera_frame=True)
                        row['triggerStatus'] = 'recognized' if row['value'] is not None else 'recognition_failed'
                        row['frameTimeDiffSeconds'] = abs(captured-target)
                    row['transmitterStamp'] = stamp
                    row['triggerReceivedAt'] = report.get('serverReceivedAt')
                    result = live.publish(row, image)
                    if result is None:  # Paused while inference was running: retry on resume.
                        break
                    done[stamp] = row['triggerStatus']
                    # Cache is bounded to 1000 reports; retain a larger deduplication ledger.
                    ledger['done'] = done = dict(sorted(done.items(), key=lambda item:int(item[0]))[-5000:])
                    atomic_json(ledger_file, ledger)
                    rows.append(row)
                    print('报文 %s -> %s，视觉值 %s，画面时间差 %s 秒' % (stamp,row['triggerStatus'],row['value'],row['frameTimeDiffSeconds']), flush=True)
                    try:
                        book = pipe.merge_rows(history, rows, 'image')
                        pipe.write_excel(excel, book, {'span':book[0]['t_cap']+' ~ '+book[-1]['t_cap']})
                    except Exception as exc:
                        print('Excel 保存失败，识别已发布：%s' % exc, flush=True)
                    if not cfg['output'].get('keep_images', True) and image:
                        image.unlink(missing_ok=True)
                    if args.count and len(rows) >= args.count:
                        return 0
                time.sleep(.3)
            except (RetryReadError, PermissionError, json.JSONDecodeError) as exc:
                try:
                    live.set_status('capture_error', '数据文件暂时不可用；保留视频缓存，自动重试：'+str(exc))
                except PermissionError:
                    pass
                last_heartbeat = 0
                time.sleep(.5)
                continue
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        live.set_status('error', '报文触发采集异常：'+str(exc))
        raise
    finally:
        if stream:
            stream.stop()
        rc._STREAMS.clear()
    live.set_status('stopped', '报文触发采集已停止')
    return 0
