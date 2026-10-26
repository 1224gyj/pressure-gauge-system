#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
流水线.py —— 一条命令跑通「摄像头抓图 → 压力表读数 → Excel」

把两个原本互不相干的工具接成一条链：
    camera-grab（ONVIF 抓图）        →  0-16MPa读数工具（照片 → MPa）
                     ↓  合体成一个进程  ↓
              到点取帧 → 立刻读数 → 结果落 Excel（带时间戳 + 趋势图）

【为什么必须是「一个进程、一条长连接」】
  这台 TP-Link TL-IPC342-4 只有一个 RTSP 会话槽，而且用完释放得很不干脆
  （详见 lib/read_camera.py 文件头的实测记录）。所以正确形态不是
  "抓一批图 → 再读一批图"，而是：
      开一条常驻 ffmpeg 长连接  →  到点从流里取最新帧  →  立刻读数
  整个进程生命周期里 RTSP 连接恒等于 1 条，不会被设备回 401。
  代价是运行期间一直在拉流，默认走子码流（640×360，几百 kbps）。

【怎么用】
  1) 先看计划，不联网、不问密码：
         python 流水线.py --dry-run
  2) 正式跑（按 config.json 的设置）：
         python 流水线.py
  3) 只对已有照片批量读数（跳过抓图，离线补跑）：
         python 流水线.py --from-dir D:\照片
  4) 单张试一下读数：
         python 流水线.py --image 样例照片.jpg

  ★ 抓图节奏只改 config.json 里的 schedule 段，不用动代码。
  ★ 命令行参数优先于 config.json，用来临时改一次。

【跑在哪个 Python 里】
  用装了 torch/ultralytics/opencv 的那个环境（本机是 conda env `ultralytics`）。
  抓图侧的 onvif-zeep / imageio-ffmpeg 已随本流水线放在 _deps/ 里，启动时自动加载，
  不需要额外安装、也不动你的环境。
"""

import argparse
import datetime
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- 路径与依赖
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
LIB = PROJECT_ROOT / "src/camera"
RECOGNITION = PROJECT_ROOT / "src/recognition"
DEPS = PROJECT_ROOT / "runtime/camera_deps"
WEIGHTS = PROJECT_ROOT / "models"
CONFIG = PROJECT_ROOT / "config/pipeline.json"

# 优先使用当前 Python 环境的依赖；随附依赖仅作后备。
# 随附 lxml 等二进制扩展可能属于其他 Python 版本，不能覆盖环境中的版本。
if DEPS.is_dir():
    sys.path.append(str(DEPS))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_module(name, path):
    """按文件路径加载模块（两个模块的文件名都带中文，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_parts():
    """加载两个原版模块 —— 原样复用，不改它们一个字节。
    另外加载可选的读数增强层（只包一层，原工具不动）。"""
    missing = [p for p in (LIB / "read_camera.py", RECOGNITION / "读数_0到16kPa.py")
               if not p.exists()]
    if missing:
        print("[!] 缺少模块文件：")
        for p in missing:
            print("      %s" % p)
        return None, None, None
    rc = load_module("_pipeline_camera", LIB / "read_camera.py")
    gr = load_module("_pipeline_gauge", RECOGNITION / "读数_0到16kPa.py")
    enh = None
    p_enh = RECOGNITION / "读数增强.py"
    if p_enh.exists():
        try:
            enh = load_module("_pipeline_gauge_enh", p_enh)
        except Exception as e:
            print("[!] 读数增强层加载失败（%s），本次退回原读数。" % e)
    return rc, gr, enh


# 当前生效的读数实现（原工具 / 增强层）—— 在 main 里按配置选定
READER = {"mod": None, "tag": "原工具"}


def use_reader(gr, enh, choice):
    """选读数实现。增强层缺失时自动退回原工具，不报错。"""
    if choice == "enhanced":
        if enh is not None:
            READER["mod"], READER["tag"] = enh, "增强层"
        else:
            READER["mod"], READER["tag"] = gr, "原工具(增强层缺失)"
    else:
        READER["mod"], READER["tag"] = gr, "原工具"
    return READER["tag"]


# ---------------------------------------------------------------- 配置
DEFAULT_CONFIG = {
    "camera": {
        "ip": "192.168.1.60",
        "onvif_port": 2020,
        "user": "admin",
        "password": "",
        "sub": True,
        "stream_fps": 2,
    },
    "schedule": {
        "every": "10",
        "at": "",
        "between": "",
        "count": 5,
        "first_shot_now": True,
    },
    "output": {
        "image_dir": "data/captures",
        "prefix": "snapshot",
        "excel": "data/readings/读数记录.xlsx",  # ★ 固定文件名：每次跑都往这个表里续写
        "excel_mode": "append",        # append=累积更新同一个表（默认）；new=每次新建带时间戳的表
        "dedup": "image",              # 同一张图重跑 → 覆盖那一行（按图片名去重）；none=无条件追加
        "debug_dir": "",
        "keep_images": True,
    },
    "read": {
        "reader": "enhanced",     # enhanced=读数增强层（推荐，见 读数增强.py 开头说明）
                                  # original=原工具原样调用（其调参针对 2734px 满幅照片，
                                  #          实时小图会误触发"淡针兜底"，读数偏差可达数 MPa）
        "burst": 5,               # ★ 每次抓拍连拍几帧，用多帧锁定圆心/针角（详见 流水线.py 的
                                  #   _grab_new 与 读数增强.py 的 read_one_multi）
                                  #   1 = 关，退回单帧读数；实测 3~7 效果最好；连拍只占同一条
                                  #   RTSP 长连接，不会多开一路会话。
    },
}


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=CONFIG):
    """读 config.json；不存在就用默认值（并顺手写一份出来）。"""
    cfg = DEFAULT_CONFIG
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cfg = deep_merge(DEFAULT_CONFIG, raw)
        except Exception as e:
            print("[!] config.json 读不动（%s），这次用默认配置。" % e)
    else:
        try:
            path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            print("[i] 已生成默认配置：%s" % path)
        except Exception:
            pass
    return cfg


def resolve(p):
    """相对路径一律按项目根目录算，从哪儿运行都落到同一个地方。"""
    if not p:
        return p
    pp = Path(p)
    return pp if pp.is_absolute() else (PROJECT_ROOT / pp)


# ------------------------------------------------- Excel 累积（并进同一个表）
# 目的：结果表固定一个文件名，每次跑完往里【续写/更新】，而不是另存一份新的。
# 读取时按【表头名字】认列、不按列位置 —— 早先生成、列数不同的旧表也能读回来续写。
COL_OF_HEADER = {
    "读数(kPa)": ("value", float),  # 兼容旧表头，历史识别数值保持不变
    "序号":        ("seq",    int),
    "抓图时刻":    ("t_cap",  str),
    "图片":        ("name",   str),
    "读数(MPa)":   ("value",  float),
    "针尖置信度":  ("conf",   float),
    "原始针长":    ("raw_d",  int),
    "来源":        ("source", str),
    "状态":        ("status", str),
    "备注":        ("note",   str),
    "读数耗时(s)": ("cost",   float),
}
ROW_FIELDS = ["seq", "t_cap", "name", "value", "conf", "raw_d",
              "source", "status", "note", "cost"]
APPEND_DEFAULT = "data/readings/读数记录.xlsx"
PENDING_GLOB = "_待合并_*.xlsx"


def _num(v, cast):
    """Excel 单元格 → 目标类型；空值 / 占位符（— 等）一律回 None。
    字符串字段（图片名、时刻、状态…）原样保留，不做数值转换。"""
    if v is None:
        return None
    if cast is str:
        s = str(v).strip()
        return s if s not in ("", "—", "-", "None", "nan") else None
    if isinstance(v, str):
        s = v.strip()
        if s in ("", "—", "-", "None", "nan"):
            return None
        try:
            v = float(s)
        except Exception:
            return None
    try:
        return cast(v)
    except Exception:
        return None


def load_excel_rows(path):
    """读回已有 Excel 的「明细」表。读不出（文件不在 / 表头不认识 / 正被占用）
    就返回空表 —— 绝不因为读不动而让整轮抓图失败。"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        from openpyxl import load_workbook
        wb = load_workbook(str(p), read_only=True, data_only=True)
    except Exception as e:
        print("  [i] 已有结果表读不动（%s），这次按空表开始。" % e)
        return []
    try:
        if "明细" not in wb.sheetnames:
            return []
        it = wb["明细"].iter_rows(values_only=True)
        try:
            head = next(it)
        except StopIteration:
            return []
        cols = []
        for i, h in enumerate(head or []):
            key = COL_OF_HEADER.get(str(h).strip()) if h is not None else None
            cols.append((i,) + key if key else None)
        if not any(cols):
            print("  [i] 已有结果表的表头认不出来，这次按空表开始。")
            return []
        out = []
        for raw in it:
            if raw is None or all(v is None for v in raw):
                continue
            rec = {k: None for k in ROW_FIELDS}
            rec["fallback"] = False
            for c in cols:
                if c and c[0] < len(raw):
                    rec[c[1]] = _num(raw[c[0]], c[2])
            if rec.get("name"):
                out.append(rec)
        return out
    except Exception as e:
        print("  [i] 已有结果表解析出错（%s），这次按空表开始。" % e)
        return []
    finally:
        try:
            wb.close()
        except Exception:
            pass


def merge_rows(history, new_rows, dedup="image"):
    """把本次结果并进历史，返回全量行。

    dedup='image'（默认）：以「图片名」为唯一键 ——
        同名（同一张图重跑）→ 用新结果**覆盖**那一行，表里的数跟着算法更新；
        新名              → **追加**一行。
    dedup='none'：无条件追加（不推荐，重跑会出重复行）。

    最后按（抓图时刻, 图片名）排序并重编序号 —— 明细表按时间读起来才顺。
    """
    out = list(history)
    idx = {}
    if dedup == "image":
        for i, r in enumerate(out):
            k = r.get("name")
            if k and k not in idx:
                idx[k] = i
    for r in new_rows:
        k = r.get("name") if dedup == "image" else None
        if k and k in idx:
            out[idx[k]] = r                 # 覆盖：同名图有了更新的读数
        else:
            out.append(r)
            if k:
                idx[k] = len(out) - 1
    out.sort(key=lambda r: (str(r.get("t_cap") or ""), str(r.get("name") or "")))
    for i, r in enumerate(out, 1):
        r["seq"] = i
    return out


def excel_path_of(cfg, args=None):
    """定下结果表的路径。
    append 模式 + 没指定路径 → 固定的「data/readings/读数记录.xlsx」（同一个文件一直沿用）；
    new    模式 + 没指定路径 → 老的「data/readings/读数_年月日_时分.xlsx」。"""
    outp = cfg["output"]
    p = (getattr(args, "excel", None) if args else None) or outp.get("excel") or ""
    if p:
        return resolve(p)
    if outp.get("excel_mode", "append") == "append":
        return resolve(APPEND_DEFAULT)
    return resolve("data/readings/读数_%s.xlsx" % datetime.datetime.now().strftime("%Y%m%d_%H%M"))


def absorb_pending(out_path):
    """上次因为目标表正被 Excel 打开、没能写进去而落成的「_待合并_*.xlsx」，
    这次开跑时先并回主表（成功即删掉那些临时表）。返回并入的行数。"""
    out = Path(out_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    pend = sorted(out.parent.glob(PENDING_GLOB))
    if not pend:
        return 0
    book = load_excel_rows(out)
    n = 0
    for p in pend:
        rows = load_excel_rows(p)
        if not rows:
            continue
        book = merge_rows(book, rows)
        n += len(rows)
        try:                                # 本机 safe-delete 垫片可能抛异常
            p.unlink()
        except Exception:
            pass
    if n:
        span = "—"
        if book:
            span = "%s ~ %s" % (book[0].get("t_cap"), book[-1].get("t_cap"))
        write_excel(out, book, {"span": span})
        print("  [i] 已把上次没写进去的 %d 行并回 %s" % (n, out.name))
    return n


# ---------------------------------------------------------------- Excel 输出
HEADER = ["序号", "抓图时刻", "图片", "读数(MPa)", "针尖置信度",
          "原始针长", "来源", "状态", "备注", "读数耗时(s)"]


def write_excel(out_path, rows, meta):
    """写明细 + 汇总（含趋势图）。每写完一次就落盘，长跑中途崩了也不丢数据。"""
    from openpyxl import Workbook
    from openpyxl.chart import LineChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "明细"
    ws.append(HEADER)
    for r in rows:
        ws.append([
            r["seq"], r["t_cap"], r["name"],
            r["value"], r["conf"], r["raw_d"], r["source"], r["status"],
            r.get("note"), r["cost"],
        ])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color="1F3864")
        c.alignment = Alignment(horizontal="center")
    for i, w in enumerate([6, 20, 30, 11, 12, 10, 10, 30, 34, 11], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions

    vals = [r["value"] for r in rows if r["value"] is not None]
    fails = [r for r in rows if r["value"] is None]
    fb_n = sum(1 for r in rows if r.get("fallback"))
    flag_n = sum(1 for r in rows if r["status"] not in ("ok",) and r["value"] is not None)
    costs = [r["cost"] for r in rows if r.get("cost")]

    pairs = [("生成时间", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
             ("抓图起止", meta.get("span", "—")),
             ("图片总数", len(rows)),
             ("成功读数", len(vals)),
             ("失败/未检出", len(fails)),
             ("兜底/图像法", fb_n),
             ("需人工复核", flag_n),
             ("成功率", "%.1f%%" % (len(vals) / len(rows) * 100) if rows else "—")]
    if vals:
        pairs += [("读数最小", "%.2f MPa" % min(vals)),
                  ("读数平均", "%.2f MPa" % (sum(vals) / len(vals))),
                  ("读数最大", "%.2f MPa" % max(vals)),
                  ("读数极差", "%.2f MPa" % (max(vals) - min(vals)))]
    if costs:
        pairs.append(("平均读数耗时", "%.3f s/张" % (sum(costs) / len(costs))))

    ws2 = wb.create_sheet("汇总")
    for k, v in pairs:
        ws2.append([k, v])
    for i in range(1, len(pairs) + 1):
        ws2.cell(row=i, column=1).font = Font(bold=True, color="1F3864")
    ws2.column_dimensions["A"].width = 16
    ws2.column_dimensions["B"].width = 26

    if fails:
        start = len(pairs) + 2
        ws2.cell(row=start, column=1, value="失败清单").font = Font(bold=True, color="C00000")
        ws2.cell(row=start, column=2, value="原因").font = Font(bold=True, color="C00000")
        for j, r in enumerate(fails, 1):
            ws2.cell(row=start + j, column=1, value=r["name"])
            ws2.cell(row=start + j, column=2, value=r["status"])

    # 趋势图：读数随时间怎么变 —— 监测压力表才真正要看的东西
    if len(vals) >= 2:
        ch = LineChart()
        ch.title = "压力表读数趋势"
        ch.y_axis.title = "读数 (MPa)"
        ch.x_axis.title = "抓图时刻"
        ch.height = 8.5
        ch.width = 20
        data = Reference(ws, min_col=4, min_row=1, max_row=ws.max_row)
        cats = Reference(ws, min_col=2, min_row=2, max_row=ws.max_row)
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(cats)
        ch.series[0].smooth = False
        # 累积模式下明细则越积越长，x 轴标签隔开显示，免得挤成一团
        ch.x_axis.tickLblSkip = max(1, len(vals) // 15)
        ws2.add_chart(ch, "D2")

    return _save_wb(wb, out)


def _save_wb(wb, out, tries=3):
    """保存工作簿。目标正被 Excel 占用时先重试几次（用户来得及关掉）；
    仍失败则写成同目录的「_待合并_*.xlsx」，下次运行会自动并回主表，数据不丢。"""
    out = Path(out)
    for k in range(tries):
        try:
            wb.save(str(out))
            return str(out)
        except PermissionError:
            if k < tries - 1:
                time.sleep(1.5)
    alt = out.with_name("_待合并_%s%s"
                        % (datetime.datetime.now().strftime("%Y%m%d_%H%M%S"), out.suffix))
    wb.save(str(alt))
    print("  [提示] %s 正被占用（Excel 里开着它？），本轮结果先存到 %s" % (out.name, alt.name))
    print("         下次运行会自动把它并回主表；用完记得关掉 Excel。")
    return str(alt)


# ---------------------------------------------------------------- 读数
def read_and_record(gr, mk, mp, img_path, t_cap, seq, debug_dir=None, camera_frame=False):
    """读一张图，返回一行记录。读数实现由 READER 决定（原工具 / 增强层）。"""
    t0 = time.time()
    options = {'allow_legacy': not camera_frame} if READER['tag'] == '增强层' else {}
    r = READER["mod"].read_one(mk, mp, Path(img_path), debug_dir=debug_dir, **options)
    cost = time.time() - t0
    if r.get("ok"):
        src = r.get("source")
        if src:                                  # 增强层会给出具体来源（图像法/模型针尖…）
            src = READER["tag"] + "·" + src
        else:
            src = "淡针兜底" if r.get("fallback") else "模型直读"
        return {
            "seq": seq, "t_cap": t_cap, "name": Path(img_path).name,
            "value": r["value"], "conf": r["tip_conf"], "raw_d": r["raw_d"],
            "source": src,
            "fallback": bool(r.get("fallback")),
            "status": r["flag"] if r.get("flag") else "ok",
            "note": r.get("note"),
            "cost": round(cost, 3),
        }
    return {
        "seq": seq, "t_cap": t_cap, "name": Path(img_path).name,
        "value": None, "conf": None, "raw_d": None, "source": "—", "fallback": False,
        "status": r.get("error", "读数失败"), "note": None, "cost": round(cost, 3),
    }


def _grab_new(stream, last_no=-1, timeout=8.0):
    """从长连接里取一个**新**帧（帧序号 > last_no）。返回 (jpeg字节, 帧序号)。

    为什么不直接用 lib 的 stream.grab()：那个函数在"新鲜窗口"（1.2/帧率 秒）内会
    **反复返回同一帧**，连拍时只会拿到同一张画面、多帧锁定就退化成了单帧。
    所以这里按帧序号等新帧 —— 序号由取流线程在收到新帧时自增并广播（见
    lib/read_camera.py 的 _reader），所以等的一定是真正解出来的新画面。

    last_no=-1 是个特殊值：第一次调用会立刻返回当前缓存帧（不用等），
    之后每次都必须比上一次更新 —— 正好是"首帧即时、后续逐帧等"的连拍语义。
    """
    deadline = time.time() + max(0.5, float(timeout))
    with stream._cond:
        while True:
            if stream._frame is not None and stream._frame_no > last_no:
                return stream._frame, stream._frame_no
            if not stream._alive or time.time() >= deadline:
                return None, last_no
            stream._cond.wait(0.2)


def read_and_record_multi(mk, mp, frames, t_cap, seq, name, debug_dir=None):
    """多帧（**内存里的** BGR 数组列表）→ 一行记录。返回 (row, 代表帧下标, 摘要)。

    连拍帧不落盘：读数在内存里完成，最后只把"最典型"的那一帧写成 jpg。
    这样磁盘上仍然是一次抓拍一张图，主表仍旧一图一行，不产生一堆中间文件。
    """
    t0 = time.time()
    r = READER["mod"].read_one_multi(mk, mp, frames, debug_dir=debug_dir, allow_legacy=False)
    cost = time.time() - t0
    brief = ""
    if r.get("ok"):
        src = r.get("source")
        src = (READER["tag"] + "·" + src) if src else READER["tag"]
        if r.get("frames"):
            brief = "%d帧|读数极差%.2f|圆心抖%.1fpx%s" % (
                r["frames"], r.get("spread", 0), r.get("center_jitter", 0),
                "|锁针角" if r.get("angle_locked") else "|未锁针角(表可能动了)")
        return {
            "seq": seq, "t_cap": t_cap, "name": name,
            "value": r["value"], "conf": r["tip_conf"], "raw_d": r["raw_d"],
            "source": src, "fallback": bool(r.get("fallback")),
            "status": r["flag"] if r.get("flag") else "ok",
            "note": r.get("note"), "cost": round(cost, 3),
        }, int(r.get("best_index", 0)), brief
    return {
        "seq": seq, "t_cap": t_cap, "name": name,
        "value": None, "conf": None, "raw_d": None, "source": "—", "fallback": False,
        "status": r.get("error", "读数失败"), "note": None, "cost": round(cost, 3),
    }, 0, ""


def stamp_from_name(name, fallback_ts):
    """从 snapshot_20261012_210530.jpg 里还原抓图时刻；认不出就用文件时间。"""
    import re
    m = re.search(r"(\d{8})_(\d{6})", name)
    if m:
        try:
            return datetime.datetime.strptime(m.group(1) + m.group(2),
                                              "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return datetime.datetime.fromtimestamp(fallback_ts).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 环境自检
def env_report(cfg, gr):
    print("[i] 解释器  ：%s" % sys.executable)
    print("[i] 版本    ：Python %d.%d.%d" % sys.version_info[:3])
    ok = True
    for mod, role in (("onvif", "ONVIF 信令（抓图）"),
                      ("imageio_ffmpeg", "自带 ffmpeg（抓图）"),
                      ("cv2", "图像处理（读数）"),
                      ("numpy", "数值计算（读数）"),
                      ("ultralytics", "YOLO 模型（读数）")):
        try:
            __import__(mod)
        except Exception as exc:
            print("[!] 依赖加载失败：%-16s %s（%s）" % (mod, role, exc))
            ok = False
    for label, p in (("刻度模型", WEIGHTS / "刻度_0到16.pt"),
                     ("指针模型", WEIGHTS / "指针_0到16.pt"),
                     ("读数脚本", RECOGNITION / "读数_0到16kPa.py"),
                     ("抓图模块", LIB / "read_camera.py")):
        if not p.exists():
            print("[!] 缺文件：%s（%s）" % (p, label))
            ok = False
    print("[i] 读数实现：%s" % READER["tag"])
    print("[i] 依赖与文件：%s" % ("齐全" if ok else "有缺失，见上"))
    print()
    return ok


def load_models(gr):
    from ultralytics import YOLO
    t0 = time.time()
    mk = YOLO(gr.MK_PT)
    mp = YOLO(gr.MP_PT)
    print("[i] 两个关键点模型已加载（%.1f 秒）" % (time.time() - t0))
    print("      刻度：%s" % gr.MK_PT)
    print("      指针：%s" % gr.MP_PT)
    return mk, mp


# ---------------------------------------------------------------- 三种模式
def mode_single(rc, gr, mk, mp, args, debug_dir):
    r = read_and_record(gr, mk, mp, args.image, "—", 1, debug_dir)
    print("=" * 62)
    print("单张读数：%s" % args.image)
    print("=" * 62)
    for k, v in r.items():
        if v is not None:
            print("  %-10s %s" % (k, v))
    return 0 if r["value"] is not None else 1


def mode_from_dir(rc, gr, mk, mp, args, cfg, debug_dir):
    """离线补跑：对已有照片批量读数，不抓图、不连摄像头。

    --from-dir 既可以是**目录**（递归读里面所有图片），也可以是**单张图片**
    （方便把一张图直接拖到 读图片.bat 上）。
    """
    d = Path(args.from_dir)
    if d.is_file():
        if d.suffix.lower() not in gr.IMG_EXT:
            print("[!] 不是图片文件：%s（支持 %s）" % (d, "/".join(gr.IMG_EXT)))
            return 2
        files, what = [d], "文件"
    elif d.is_dir():
        files = sorted(p for p in d.rglob("*")
                       if p.is_file() and p.suffix.lower() in gr.IMG_EXT
                       and not p.name.startswith("_"))
        what = "目录"
    else:
        print("[!] 路径不存在：%s" % d)
        return 2
    if not files:
        print("[!] %s 下没找到图片。" % d)
        return 2
    if args.count:
        files = files[:args.count]

    print("=" * 62)
    print("离线批量读数（不连摄像头）")
    print("=" * 62)
    print("  %s：%s" % (what, d))
    print("  数量：%d 张" % len(files))
    print()

    rows, t0 = [], time.time()
    for i, p in enumerate(files, 1):
        t_cap = stamp_from_name(p.name, p.stat().st_mtime)
        r = read_and_record(gr, mk, mp, p, t_cap, i, debug_dir)
        rows.append(r)
        tag = ("%.2f MPa" % r["value"]) if r["value"] is not None else r["status"]
        print("  [%3d/%d] %-34s %s" % (i, len(files), p.name[:34], tag))

    out = excel_path_of(cfg, args)
    append = cfg["output"].get("excel_mode", "append") == "append"
    dedup = cfg["output"].get("dedup", "image")
    history = []
    if append:
        absorb_pending(out)
        history = load_excel_rows(out)
    book = merge_rows(history, rows, dedup if append else "none")
    meta = {"span": ("%s ~ %s" % (book[0]["t_cap"], book[-1]["t_cap"])) if book else "—"}
    saved = write_excel(out, book, meta)
    vals = [r["value"] for r in rows if r["value"] is not None]
    print()
    print("  成功 %d / %d，耗时 %.0f 秒" % (len(vals), len(rows), time.time() - t0))
    if vals:
        print("  读数：最小 %.2f　平均 %.2f　最大 %.2f MPa"
              % (min(vals), sum(vals) / len(vals), max(vals)))
    if append and history:
        print("  结果已并入：%s（原有 %d 行 → 现共 %d 行）" % (saved, len(history), len(book)))
    else:
        print("  结果已写入：%s（共 %d 行）" % (saved, len(book)))
    return 0


def mode_live(rc, gr, mk, mp, args, cfg, uri, debug_dir):
    if cfg.get('read', {}).get('trigger_mode') == 'transmitter':
        from triggered_capture import run_triggered
        return run_triggered(sys.modules[__name__], rc, gr, mk, mp, args, cfg, uri, debug_dir)
    """正式模式：一条长连接 → 到点取帧 → 立刻读数 → 落 Excel。"""
    cam, sch, outp = cfg["camera"], cfg["schedule"], cfg["output"]
    live_store = None
    if cfg.get("live", {}).get("enabled", True):
        try:
            live_module = load_module("_pipeline_live_store", HERE / "live_store.py")
            live_store = live_module.LiveStore(PROJECT_ROOT, cfg.get("live", {}).get("config", "config/live.json"))
            live_store.set_status("starting", "正在连接摄像头")
        except Exception as e:
            print("  [实时推送] 初始化失败：%s" % e)
    live_failed = False
    def live_status(state, message):
        if live_store:
            try:
                live_store.set_status(state, message)
            except Exception as e:
                print("  [实时推送] 状态保存失败：%s" % e)
    out_dir = resolve(outp["image_dir"] or ".")
    prefix = outp["prefix"] or "snapshot"
    keep_images = bool(outp.get("keep_images", True))
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 连拍（多帧锁定圆心）：只占同一条长连接，不会多开 RTSP 会话 ----
    burst = int(getattr(args, "burst", 0) or cfg["read"].get("burst", 1) or 1)
    use_multi = burst > 1 and hasattr(READER["mod"], "read_one_multi")
    if burst > 1 and not use_multi:
        print("  [i] 读数实现「%s」不支持多帧锁定，burst=%d 已忽略（仍按单帧读）。"
              % (READER["tag"], burst))
        burst = 1
    # 取流帧率至少等于连拍张数，否则一张要等 1/帧率 秒，连拍会拖很久
    fps = int(cam["stream_fps"])
    if use_multi and fps < burst:
        fps = burst

    import cv2 as _cv2
    import numpy as _np

    sched, err = rc.build_schedule_from_args(args)
    if err:
        print("[!] %s" % err)
        return 2

    excel = excel_path_of(cfg, args)
    append = outp.get("excel_mode", "append") == "append"
    dedup = outp.get("dedup", "image")
    history = []
    if append:
        absorb_pending(excel)
        history = load_excel_rows(excel)

    print("=" * 62)
    print("流水线：抓图 → 读数 → Excel")
    print("=" * 62)
    print("  取流地址 ：%s" % rc.mask_uri(uri, args.password))
    print("  图片目录 ：%s" % out_dir)
    print("  结果文件 ：%s" % excel)
    if append:
        print("  写入方式 ：累积更新同一份表（原有 %d 行；同张图重跑则覆盖那一行）" % len(history))
    print("  抓拍节奏 ：%s" % sched.describe())
    print("  数量上限 ：%s" % ("%d 张，抓满自动退出" % args.count if args.count
                              else "不限，按 Ctrl+C 停止"))
    print("  首张     ：%s" % ("启动后立刻抓一张，之后按节奏"
                              if sch.get("first_shot_now", True) else "等到第一个计划时刻"))
    print("  会话策略 ：长连接（全程只连这一次）")
    if use_multi:
        print("  连拍     ：每次抓 %d 帧 → 多帧锁定圆心+针角（表静止时用；取流帧率提到 %d）"
              % (burst, fps))
    print()

    # 计划预演：先让用户看一眼，别蒙着跑
    now_first = bool(sch.get("first_shot_now", True))
    cap = 5 if not args.count else max(0, min(args.count - (1 if now_first else 0), 5))
    preview, tt = [], datetime.datetime.now()
    for _ in range(cap):
        if sch.get("align_to_clock", False):
            from capture_schedule import next_capture
            n = datetime.datetime.fromtimestamp(next_capture(CONFIG, tt.timestamp(), now=tt.timestamp()))
        else:
            n = sched.next_after(tt)
        if n is None:
            break
        preview.append(n)
        tt = n
    if preview or now_first:
        print("  接下来 %d 次抓拍：" % (len(preview) + (1 if now_first else 0)))
        i = 0
        if now_first:
            i = 1
            print("    %d) %s   （启动即刻）"
                  % (i, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        for n in preview:
            i += 1
            print("    %d) %s" % (i, n.strftime("%Y-%m-%d %H:%M:%S")))
        print()
    elif not now_first:
        print("  [!] 按当前设置算不出未来的抓拍时刻，请检查 every / at / between。")
        return 2

    # 占用体检：这台设备只有一路 RTSP 会话槽
    holders = rc.find_stream_holders(cam["ip"], 554)
    if holders:
        print("  [!] 本机已有程序占着该摄像头的视频流：%s" % "、".join(holders))
        print("      这台设备同时只允许一路 RTSP 客户端，那样我们会一直拿到 401。")
        print("      → 建议先关掉：网页「预览」页 / ODM 的 Live video / 播放器。")
        print()

    stream = None
    rows = []
    ok_n = fail_n = 0
    t0 = time.time()

    def flush():
        book = merge_rows(history, rows, dedup if append else "none")
        vals = [r["value"] for r in rows if r["value"] is not None]
        meta = {"span": ("%s ~ %s" % (book[0]["t_cap"], book[-1]["t_cap"])) if book else "—"}
        saved = write_excel(excel, book, meta)
        return vals, saved

    try:
        print("  正在建立长连接（本次运行只连这一次）……")
        stream = rc.RtspFrameStream(uri, fps).start()
        rc._STREAMS.append(stream)
        jpg, _ts = stream.grab(timeout=20)
        if jpg is None:
            live_failed = True
            live_status("error", "摄像头连接后未取得画面")
            print("  [失败] 长连接没拿到画面。")
            rc._report_stream_problem(stream, rc.mask_uri(uri, args.password))
            return 1
        print("  [OK] 取流已建立，首帧 %.1f KB。" % (len(jpg) / 1024.0))
        print()

        first = True
        t = datetime.datetime.now()
        while True:
            if first and sch.get("first_shot_now", True):
                nxt = datetime.datetime.now()
            elif sch.get("align_to_clock", False):
                from capture_schedule import wait_capture
                nxt = wait_capture(CONFIG, t.timestamp())
            else:
                nxt = sched.next_after(t)
                if nxt is None:
                    print("  [i] 没有更多抓拍时刻了（绝对时刻已全部过去），收工。")
                    break
                if nxt < datetime.datetime.now():
                    nxt = datetime.datetime.now()
                rc._wait_until(nxt, label="下一次抓拍")
            first = False
            t = nxt

            jpgs, fmats, frame_times = [], [], []
            if use_multi:
                # 在同一条长连接上连拍 N 帧（不额外开 RTSP 会话）—— 首帧即时，其余逐帧等新帧
                last_no = -1
                for _k in range(burst):
                    jb, last_no = _grab_new(stream, last_no, timeout=10.0)
                    if jb is None:
                        break
                    arr = _cv2.imdecode(_np.frombuffer(jb, dtype=_np.uint8),
                                        _cv2.IMREAD_COLOR)
                    if arr is None:
                        continue
                    jpgs.append(jb)
                    frame_times.append(stream._frame_at if stream._frame_at > 0 else time.time())
                    fmats.append(arr)
            else:
                jb, _frm_at = stream.grab(timeout=20)
                if jb is not None:
                    jpgs.append(jb)
                    frame_times.append(_frm_at if _frm_at > 0 else time.time())

            if not jpgs:
                fail_n += 1
                live_status("capture_error", "未取得摄像头画面，正在重试")
                print("  [失败] %s 这一张没拿到画面（连续第 %d 次）"
                      % (t.strftime("%H:%M:%S"), fail_n))
                rc._report_stream_problem(stream, rc.mask_uri(uri, args.password))
                if fail_n >= 5:
                    live_failed = True
                    live_status("error", "连续 5 次未取得摄像头画面，采集已停止")
                    print()
                    print("  连续 5 次没拿到画面，先停下来。多半是：")
                    print("    · 有别的客户端占着唯一那路流（关掉网页预览 / ODM Live video）")
                    print("    · 设备的会话槽卡住了 → 给摄像头断电 30 秒重启，再跑一次")
                    break
                continue
            fail_n = 0
            cap_at = datetime.datetime.fromtimestamp(sorted(frame_times)[len(frame_times)//2]) if frame_times else datetime.datetime.now()
            t_cap = cap_at.isoformat(sep=" ", timespec="milliseconds")
            name = "%s_%s.jpg" % (prefix, cap_at.strftime("%Y%m%d_%H%M%S"))
            path = out_dir / name

            # ---- 立刻读数（模型常驻内存，不再重复加载）----
            #    多帧模式：N 帧在内存里一起读（圆心/针角取中位数钉死），
            #    只把"最典型"的那一帧写盘 —— 磁盘上仍然是一图一行。
            seq = len(rows) + 1
            brief = ""
            if use_multi and len(fmats) > 1:
                r, best_i, brief = read_and_record_multi(mk, mp, fmats, t_cap, seq,
                                                         name, debug_dir)
                jpg = jpgs[min(best_i, len(jpgs) - 1)]
            else:
                # 单帧（含连拍不足两帧的降级）必须先保存，读数器才能按路径读取。
                try:
                    path.write_bytes(jpgs[0])
                except Exception as e:
                    print("  [失败] 写文件失败：%s（%s）" % (path, e))
                    live_failed = True
                    live_status("error", "抓拍照片保存失败：%s" % e)
                    break
                r = read_and_record(gr, mk, mp, path, t_cap, seq, debug_dir, camera_frame=True)
                jpg = jpgs[0]
            rows.append(r)
            ok_n += 1

            try:
                with open(path, "wb") as fh:
                    fh.write(jpg)
            except Exception as e:
                print("  [失败] 写文件失败：%s（%s）" % (path, e))
                live_failed = True
                live_status("error", "抓拍照片保存失败：%s" % e)
                break

            # 实时记录先于 Excel 写入，Excel 占用不会阻塞这次识别结果的展示。
            if live_store:
                try:
                    live_store.publish(r, path if keep_images else None)
                except Exception as e:
                    print("  [实时推送] 本次结果保存失败：%s" % e)

            if r["value"] is not None:
                mark = "" if r["status"].startswith("ok") else "  ←%s" % r["status"]
                print("  [%d] %s  %s  →  %.2f MPa   (%.0f ms, %s)%s"
                      % (seq, cap_at.strftime("%H:%M:%S"), name[-13:], r["value"],
                         r["cost"] * 1000, r["source"], mark))
                if brief:
                    print("        %s" % brief)
            else:
                print("  [%d] %s  %s  →  读数失败：%s"
                      % (seq, cap_at.strftime("%H:%M:%S"), name[-13:], r["status"]))

            if not keep_images:
                try:
                    path.unlink()
                except Exception:
                    pass

            # 每张都落盘一次：长跑中途断电/Ctrl+C 也不丢前面结果
            _vals, saved = flush()
            if seq == 1 or seq % 10 == 0:
                print("      （已写入 %s）" % saved)

            if args.count and ok_n >= args.count:
                break

    except KeyboardInterrupt:
        print()
        print("  收到 Ctrl+C，停止抓图。")
    except Exception as e:
        live_failed = True
        live_status("error", "采集异常：%s" % e)
        raise
    finally:
        if live_store and not live_failed:
            live_status("stopped", "摄像头采集已结束")
        for s in list(rc._STREAMS):
            try:
                s.stop()
            except Exception:
                pass
        try:
            rc._STREAMS.clear()
        except Exception:
            pass

    # ---- 收尾 ----
    print()
    print("=" * 62)
    if not rows:
        print("  一张都没抓到，不生成 Excel。")
        return 1
    vals, saved = flush()
    book_n = len(history) + len(rows) if append else len(rows)
    print("  本次抓拍 %d 张，成功读数 %d 张，耗时 %.0f 秒"
          % (len(rows), len(vals), time.time() - t0))
    if vals:
        print("  读数：最小 %.2f　平均 %.2f　最大 %.2f MPa"
              % (min(vals), sum(vals) / len(vals), max(vals)))
    fb = sum(1 for r in rows if "淡针兜底" in str(r.get("source") or ""))
    fl = [r for r in rows if r["value"] is not None and not r["status"].startswith("ok")]
    print("  淡针兜底 %d 张，需人工复核 %d 张，失败 %d 张"
          % (fb, len(fl), len(rows) - len(vals)))
    for r in fl:
        print("    复核 %s：%s" % (r["name"], r["status"]))
    print()
    print("  结果表    ：%s" % saved)
    if append:
        print("              （累积更新，表内累计约 %d 行）" % book_n)
    print("  图片目录  ：%s" % out_dir)
    return 0


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="摄像头抓图 → 压力表读数 → Excel 一体化流水线")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印抓拍计划：不联网、不问密码、不抓图、不写文件")
    ap.add_argument("--from-dir", default=None, metavar="目录",
                    help="跳过抓图，对已有照片批量读数（离线补跑）")
    ap.add_argument("--image", default=None, metavar="文件", help="只读单张图片")
    ap.add_argument("--ip", default=None, help="摄像头 IP")
    ap.add_argument("--onvif-port", type=int, default=None, help="ONVIF 端口（TP-Link=2020）")
    ap.add_argument("--user", default=None, help="设备账号")
    ap.add_argument("--password", default=None, help="设备密码；不填会交互式询问")
    ap.add_argument("--sub", action="store_true", help="取子码流（默认就取子码流）")
    ap.add_argument("--hd", action="store_true", help="改用高清主码流（约 1MB/张）")
    ap.add_argument("--every", default=None, metavar="间隔",
                    help="每隔多久抓一张：30 / 90s / 5m / 2h / 1d")
    ap.add_argument("--at", default=None, metavar="时刻[,...]",
                    help='固定时刻抓：08:00,12:00,18:00；绝对时刻 "2026-10-13 09:00:00"')
    ap.add_argument("--between", default=None, metavar="起-止", help="只在该时段内抓，如 09:00-17:00")
    ap.add_argument("--count", type=int, default=None, metavar="N", help="抓满 N 张就停；0 = 不限")
    ap.add_argument("--stream-fps", type=int, default=None, help="长连接每秒解码几帧（默认 2）")
    ap.add_argument("--out-dir", default=None, help="图片输出目录")
    ap.add_argument("--excel", default=None, help="Excel 结果路径（默认并进 data/readings/读数记录.xlsx）")
    ap.add_argument("--new-book", action="store_true",
                    help="另存为新表（带时间戳），不改动主记录表")
    ap.add_argument("--debug-dir", default=None, help="调试图输出目录")
    ap.add_argument("--reader", default=None, choices=["enhanced", "original"],
                    help="读数实现：enhanced=增强层（默认，适合实时小图）；original=原工具原样")
    ap.add_argument("--burst", type=int, default=None, metavar="N",
                    help="每次抓拍连拍 N 帧 → 多帧锁定圆心+针角（表静止时显著更稳）；1=关。"
                         "默认取 config 的 read.burst")
    args = ap.parse_args()

    cfg = load_config()

    if not (args.dry_run or args.image or args.from_dir):
        from capture_process import register
        if not register(PROJECT_ROOT):
            print('[i] 摄像头采集已运行，请使用页面启动/暂停按钮，不要重复启动。')
            return 0

    if args.reader:
        cfg["read"]["reader"] = args.reader
    if args.burst:
        cfg["read"]["burst"] = args.burst

    # ---- 单张模式：只读一张图，完全不碰摄像头 ----
    if args.image:
        rc_, gr_, enh_ = load_parts()
        if rc_ is None:
            return 2
        use_reader(gr_, enh_, cfg["read"]["reader"])
        env_report(cfg, gr_)
        mk, mp = load_models(gr_)
        dbg = resolve(args.debug_dir) if args.debug_dir else None
        if dbg:
            dbg.mkdir(parents=True, exist_ok=True)
        return mode_single(rc_, gr_, mk, mp, args, dbg)

    # ---- 参数覆盖 config ----
    if args.ip:
        cfg["camera"]["ip"] = args.ip
    if args.onvif_port:
        cfg["camera"]["onvif_port"] = args.onvif_port
    if args.user:
        cfg["camera"]["user"] = args.user
    if args.sub:
        cfg["camera"]["sub"] = True
    if args.hd:
        cfg["camera"]["sub"] = False
    if args.stream_fps:
        cfg["camera"]["stream_fps"] = args.stream_fps
    if args.out_dir:
        cfg["output"]["image_dir"] = args.out_dir
    if args.excel:
        cfg["output"]["excel"] = args.excel
    if args.new_book:
        cfg["output"]["excel_mode"] = "new"
        if not args.excel:
            cfg["output"]["excel"] = ""      # 走带时间戳的自动命名
    if args.debug_dir:
        cfg["output"]["debug_dir"] = args.debug_dir

    for key in ("every", "at", "between"):
        v = getattr(args, key)
        if v is not None:
            cfg["schedule"][key] = v
    if args.count is not None:
        cfg["schedule"]["count"] = args.count

    cam = cfg["camera"]

    # ---- 组装一份"像 argparse 结果"的对象，喂给原版的调度器 ----
    class _NS:
        pass

    ns = _NS()
    ns.every = cfg["schedule"]["every"] or None
    ns.at = cfg["schedule"]["at"] or None
    ns.between = cfg["schedule"]["between"] or None
    ns.count = int(cfg["schedule"]["count"] or 0)
    ns.frames = 1
    ns.plan = False
    ns.no_plan = True

    if ns.every and ns.at:
        print("[!] config.json 里 every 和 at 同时填了，只能留一个：")
        print("    「每隔多久抓」→ 填 every、把 at 清空；「固定时间点抓」→ 反过来。")
        return 2
    if ns.between and not ns.every:
        print("[!] between 只配合 every 用（例：every=15m + between=09:00-17:00）。")
        return 2

    rc, gr, enh = load_parts()
    if rc is None:
        return 2
    reader_tag = use_reader(gr, enh, cfg["read"]["reader"])

    # 把「最终生效」的节奏写回 args —— 后面两个模式都从这里取，避免再读一遍 config
    args.every, args.at, args.between, args.count = ns.every, ns.at, ns.between, ns.count

    build_args = _NS()
    build_args.every = ns.every
    build_args.at = ns.at
    build_args.between = ns.between
    build_args.count = ns.count

    # ---- 预演：走原版同一套时间解析，保证"预演 = 实跑"----
    if args.dry_run and cfg.get('read', {}).get('trigger_mode') == 'transmitter':
        print('报文触发识别：摄像头持续缓存 %s 秒画面，收到新报文后按平台接收时间选帧；不独立定时识别。' % cfg['read'].get('frame_cache_seconds', 120))
        print('相同报文只识别一次；缺失或超出时间容差的画面明确标记，不用当前图替代。')
        return 0

    if args.dry_run or not (ns.every or ns.at):
        sched, err = rc.build_schedule_from_args(build_args)
        if err:
            print("[!] %s" % err)
            return 2
        print("=" * 62)
        print("预演：只打印计划 —— 不联网、不问密码、不抓图、不写文件")
        print("=" * 62)
        print("  抓拍节奏 ：%s" % sched.describe())
        print("  首张     ：%s" % ("启动后立刻抓一张" if cfg["schedule"].get("first_shot_now", True)
                                  else "等到第一个计划时刻"))
        print("  图片目录 ：%s" % resolve(cfg["output"]["image_dir"]))
        xls = excel_path_of(cfg)
        print("  结果文件 ：%s" % xls)
        if cfg["output"].get("excel_mode", "append") == "append":
            print("  写入方式 ：累积更新同一份表（现有 %d 行；新读数并入，同张图重跑则覆盖）"
                  % len(load_excel_rows(xls)))
        else:
            print("  写入方式 ：每次另存为新表（带时间戳）")
        print("  图片尺寸 ：%s" % ("子码流（小、快、稳）" if cam["sub"] else "主码流（高清大图）"))
        print("  读数实现 ：%s" % reader_tag)
        _b = int(cfg["read"].get("burst", 1) or 1)
        print("  连拍     ：%s" % ("每次抓 %d 帧 → 多帧锁定圆心+针角（表静止时显著更稳）" % _b
                                  if _b > 1 else "关（每次抓 1 帧，单帧读数）"))
        print("  数量上限 ：%s" % ("%d 张，抓满自动退出" % ns.count if ns.count
                                  else "不限，按 Ctrl+C 停止"))
        print("  现在时间 ：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print()
        now_first = cfg["schedule"].get("first_shot_now", True)
        # 首张若为"启动即刻"，它本身也算在 count 里，预演就别多算一行
        cap = 10 if not ns.count else max(0, min(ns.count - (1 if now_first else 0), 10))
        preview, tt = [], datetime.datetime.now()
        for _ in range(cap):
            if cfg["schedule"].get("align_to_clock", False):
                from capture_schedule import next_capture
                n = datetime.datetime.fromtimestamp(next_capture(CONFIG, tt.timestamp(), now=tt.timestamp()))
            else:
                n = sched.next_after(tt)
            if n is None:
                break
            preview.append(n)
            tt = n
        if preview or now_first:
            total = len(preview) + (1 if now_first else 0)
            print("  接下来 %d 次抓拍：" % total)
            i = 0
            if now_first:
                i = 1
                print("    %2d) %s   （启动即刻）"
                      % (i, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            for n in preview:
                i += 1
                left = (n - datetime.datetime.now()).total_seconds()
                print("    %2d) %s   （%s后）"
                      % (i, n.strftime("%Y-%m-%d %H:%M:%S"), rc.human_duration(left)))
        else:
            print("  [!] 算不出未来的抓拍时刻，检查 every / at / between。")
        print()
        print("  [i] 计划没问题就去掉 --dry-run 正式跑。")
        if args.dry_run:
            return 0

    if args.from_dir:
        env_report(cfg, gr)
        mk, mp = load_models(gr)
        dbg = resolve(cfg["output"]["debug_dir"]) if cfg["output"]["debug_dir"] else None
        if dbg:
            dbg.mkdir(parents=True, exist_ok=True)
        return mode_from_dir(rc, gr, mk, mp, args, cfg, dbg)

    # ---- 正式模式：先体检，再拿密码，再连 ----
    if not env_report(cfg, gr):
        print("[!] 环境不齐，先补齐上面列出的东西再跑。")
        return 2

    password = args.password
    if password is None:
        password = str(cam.get("password") or "")
    if not password:
        try:
            password = input("请输入摄像头密码，然后按回车: ").strip()
        except EOFError:
            password = ""
        if not password:
            print('[!] 没输入密码。若设备确实允许空密码，用 --password "" 明确传入。')
            return 2
    args.password = password

    risky = [c for c in "@:/?#&% " if c in password]
    if risky or any(ord(c) > 127 for c in password):
        print("[i] 密码含特殊字符，脚本已自动做百分号编码（RTSP 侧必需），继续。\n")

    print("=" * 62)
    print("第 1 步 / ONVIF 信令：读设备信息 + 问出 RTSP 地址")
    print("=" * 62)
    info, uri = {}, None
    try:
        info, uri = rc.read_onvif_info(cam["ip"], cam["onvif_port"], cam["user"],
                                       password, sub=cam["sub"])
        for k, v in info.items():
            print("  %-8s %s" % (k + "：", v))
        if info.get("厂商"):
            print("  → 用户名【%s】+ 密码 通过了 ONVIF 鉴权，凭据是对的。" % cam["user"])
    except Exception as e:
        print("  [!] ONVIF 失败: %s" % e)
        print("      检查：ONVIF 是否开启 / 端口 / 防火墙 / 网络")

    if not uri:
        uri = rc.guess_rtsp_url(cam["ip"], cam["user"], password, sub=cam["sub"])
        print("  使用固定拼接地址：%s" % rc.mask_uri(uri, password))
    else:
        print("  ONVIF 返回：%s" % uri)
        uri = rc.inject_credentials(uri, cam["user"], password)
        print("  补上凭据后：%s" % rc.mask_uri(uri, password))
    print()

    mk, mp = load_models(gr)
    dbg = resolve(cfg["output"]["debug_dir"]) if cfg["output"]["debug_dir"] else None
    if dbg:
        dbg.mkdir(parents=True, exist_ok=True)

    return mode_live(rc, gr, mk, mp, args, cfg, uri, dbg)


if __name__ == "__main__":
    sys.exit(main())
