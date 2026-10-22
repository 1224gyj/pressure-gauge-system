# -*- coding: utf-8 -*-
"""把相机识别读数按「整点数据.xlsx」的格式填进去。

两个子命令
----------
1) scan  —— 扫抓图目录，按「整点」分组，用读数增强层识别每帧，
            取该整点内所有帧的**中位数**当这个整点的识别读数，
            并自动换算 kPa → MPa（×0.001）。
            结果写成 CSV 草稿（变送器列留空，等你填）。

2) apply —— 读填好的 CSV，校验后**追加**到 整点数据.xlsx。
            写入前自动备份；同一「时间+表号」已存在则**覆盖该行**（不堆重复）。

格式约定（与现有 81 行完全一致）
-------------------------------
  A 时间        文本  'YYYY-MM-DD HH:MM'
  B 表号        文本  'YB-106'
  C 变送器(MPa)  数字
  D 识别读数(MPa) 数字
  E 量程(MPa)    数字
  F 精度等级     数字

用法
----
  # ① 生成草稿（默认读 一体化\\流水线\\输出\\captures）
  python 填整点数据.py scan --gauge YB-106 --range 0.016 --acc 2.5

  # ② 打开 data-source/_待填入.csv 把「变送器(MPa)」列填上，保存

  # ③ 写入
  python 填整点数据.py apply --csv data-source/_待填入.csv

  # 只看不写
  python 填整点数据.py apply --csv data-source/_待填入.csv --dry-run
"""
import argparse
import csv
import datetime as dt
import importlib.util
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------- 路径与常量
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DEFAULT_XLSX = PROJECT_ROOT / "data/comparison/整点数据.xlsx"
DEFAULT_CSV = PROJECT_ROOT / "data/comparison/_待填入.csv"
DEFAULT_PIPE = PROJECT_ROOT / "src/recognition"
COLS = ["时间", "表号", "变送器(MPa)", "识别读数(MPa)", "量程(MPa)", "精度等级"]
KPA_PER_MPA = 1000.0


def log(*a):
    print(*a)
    sys.stdout.flush()


# ---------------------------------------------------------------- Excel 读写
def read_grid(xlsx):
    """读成二维列表（纯值）。"""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb[wb.sheetnames[0]]
    return [list(r) for r in ws.iter_rows(values_only=True)], ws.title


def backup(xlsx):
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = xlsx.with_name("%s.bak-%s%s" % (xlsx.stem, stamp, xlsx.suffix))
    shutil.copy2(xlsx, bak)
    return bak


def apply_rows(xlsx, rows, dry_run=False):
    """rows: list[dict]，键为 COLS。同「时间+表号」覆盖，否则追加。返回统计。"""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx)          # 保留原格式
    ws = wb[wb.sheetnames[0]]
    grid, _ = read_grid(xlsx)
    index = {}
    for i, r in enumerate(grid[1:], start=2):   # 数据从第 2 行起
        if r and r[0] is not None and len(r) > 1 and r[1] is not None:
            index[(str(r[0]).strip(), str(r[1]).strip())] = i

    added, replaced = 0, 0
    for rec in rows:
        key = (rec["时间"], rec["表号"])
        vals = [rec[c] for c in COLS]
        if key in index:
            for c, v in enumerate(vals, start=1):
                ws.cell(index[key], c).value = v
            replaced += 1
        else:
            ws.append(vals)
            index[key] = ws.max_row
            added += 1

    if dry_run:
        return {"added": added, "replaced": replaced, "saved": False}
    wb.save(xlsx)
    return {"added": added, "replaced": replaced, "saved": True}


# ---------------------------------------------------------------- 识别侧
def load_reader(pipe_dir):
    """按路径加载 一体化\\流水线\\读数增强.py（原工具一字未改地复用）。"""
    pipe_dir = Path(pipe_dir)
    spec = importlib.util.spec_from_file_location("rd", str(pipe_dir / "读数增强.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rd"] = mod
    spec.loader.exec_module(mod)
    from ultralytics import YOLO
    mk = YOLO(str(PROJECT_ROOT / "models" / "刻度_0到16.pt"))
    mp = YOLO(str(PROJECT_ROOT / "models" / "指针_0到16.pt"))
    return mod, mk, mp


def stamp_of(name):
    """从 snapshot_20261014_123359.jpg 取出 (整点字符串, datetime)。"""
    base = Path(name).stem
    parts = base.split("_")
    d, t = parts[-2], parts[-1]                 # snapshot_20261014_123359 → 20261014 / 123359
    when = dt.datetime.strptime(d + t, "%Y%m%d%H%M%S")
    hour = when.replace(minute=0, second=0)
    return hour.strftime("%Y-%m-%d %H:%M"), when


def cmd_scan(args):
    import numpy as np
    src = Path(args.dir)
    files = sorted(p for p in src.glob("*.jpg"))
    if args.since:
        files = [p for p in files if stamp_of(p.name)[1] >= dt.datetime.fromisoformat(args.since)]
    if not files:
        log("[!] %s 下没有符合条件的抓图" % src)
        return 2

    log("加载读数增强层：%s" % args.pipe_dir)
    RD, mk, mp = load_reader(args.pipe_dir)

    groups = {}
    for p in files:
        key, _when = stamp_of(p.name)
        groups.setdefault(key, []).append(p)

    log("共 %d 张抓图，落在 %d 个整点上：" % (len(files), len(groups)))
    rows = []
    for key in sorted(groups):
        vals = []
        for p in groups[key]:
            r = RD.read_one(mk, mp, p)
            if r.get("value") is not None:
                vals.append((r["value"], p.name))
        if not vals:
            log("  %s  %d 帧 → 全部读取失败，跳过" % (key, len(groups[key])))
            continue
        med_kpa = float(np.median([v for v, _ in vals]))
        med_mpa = round(med_kpa * 0.001, 6)
        rows.append({
            "时间": key, "表号": args.gauge,
            "变送器(MPa)": "",                       # ← 留空，等人工填
            "识别读数(MPa)": med_mpa,
            "量程(MPa)": args.range, "精度等级": args.acc,
        })
        log("  %s  %d 帧  中位 %.2f kPa → %.4f MPa   (范围 %.2f~%.2f kPa)" % (
            key, len(vals), med_kpa, med_mpa,
            min(v for v, _ in vals), max(v for v, _ in vals)))

    out = Path(args.csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    log("\n草稿已写：%s" % out)
    log("→ 请把「变送器(MPa)」列填上（每行一个数字），保存后跑：")
    log("   python 填整点数据.py apply --csv \"%s\"" % out)
    return 0


def cmd_apply(args):
    xlsx, csvf = Path(args.xlsx), Path(args.csv)
    if not xlsx.exists():
        log("[!] 找不到 %s" % xlsx)
        return 2
    if not csvf.exists():
        log("[!] 找不到 %s（先跑 scan 生成草稿）" % csvf)
        return 2

    with open(csvf, "r", encoding="utf-8-sig", newline="") as fh:
        raw = list(csv.DictReader(fh))
    if not raw:
        log("[!] CSV 是空的")
        return 2

    rows, bad = [], []
    for i, r in enumerate(raw, start=2):
        try:
            rec = {
                "时间": str(r["时间"]).strip(),
                "表号": str(r["表号"]).strip(),
                "变送器(MPa)": float(str(r["变送器(MPa)"]).strip()),
                "识别读数(MPa)": float(str(r["识别读数(MPa)"]).strip()),
                "量程(MPa)": float(str(r["量程(MPa)"]).strip()),
                "精度等级": float(str(r["精度等级"]).strip()),
            }
        except (KeyError, ValueError, TypeError) as e:
            bad.append("第 %d 行：%s（%s）" % (i, r, e))
            continue
        if not rec["时间"] or not rec["表号"]:
            bad.append("第 %d 行：时间/表号为空" % i)
            continue
        rows.append(rec)

    if bad:
        log("[×] 有 %d 行不合法，先修好再写：" % len(bad))
        for b in bad[:10]:
            log("   " + b)
        return 2
    if not rows:
        log("[!] 没有可写入的行（多半是「变送器(MPa)」列还空着）")
        return 2

    log("待写入 %d 行：" % len(rows))
    for r in rows:
        log("   %s | %s | 变送器 %.4f | 识别 %.4f | 量程 %s | 精度 %s" % (
            r["时间"], r["表号"], r["变送器(MPa)"], r["识别读数(MPa)"],
            r["量程(MPa)"], r["精度等级"]))
        delta = r["识别读数(MPa)"] - r["变送器(MPa)"]
        dlt = delta / r["量程(MPa)"] * 100.0
        log("        Δ=%+.4f MPa  δ=%+.2f%%FS  %s" % (
            delta, dlt, "← 超差明显，确认无误？" if abs(dlt) > r["精度等级"] else ""))

    grid, sheet = read_grid(xlsx)
    log("\n现有：%s 共 %d 行（含表头）" % (sheet, len(grid)))
    if not args.dry_run:
        bak = backup(xlsx)
        log("已备份：%s" % bak.name)
    st = apply_rows(xlsx, rows, dry_run=args.dry_run)
    if st["saved"]:
        log("✔ 已写入：新增 %d 行，覆盖 %d 行" % (st["added"], st["replaced"]))
    else:
        log("（--dry-run：没有写文件）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="把识别读数按整点数据格式写入 Excel")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="扫抓图→按整点汇总→生成 CSV 草稿")
    s.add_argument("--dir", default=str(PROJECT_ROOT / "data/captures"))
    s.add_argument("--pipe-dir", default=str(DEFAULT_PIPE))
    s.add_argument("--gauge", required=True, help="表号，如 YB-106")
    s.add_argument("--range", required=True, type=float, dest="range", help="量程(MPa)，如 0.016")
    s.add_argument("--acc", required=True, type=float, help="精度等级，如 2.5")
    s.add_argument("--since", default=None, help="只取该时刻之后的抓图，如 2026-10-14")
    s.add_argument("--csv", default=str(DEFAULT_CSV))
    s.set_defaults(func=cmd_scan)

    a = sub.add_parser("apply", help="把填好的 CSV 写进 Excel（自动备份）")
    a.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    a.add_argument("--csv", default=str(DEFAULT_CSV))
    a.add_argument("--dry-run", action="store_true", help="只看不写")
    a.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
