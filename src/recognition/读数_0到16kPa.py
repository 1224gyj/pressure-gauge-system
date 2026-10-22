# -*- coding: utf-8 -*-
"""
0~16 MPa 压力表读数工具（专用版，含精度优化管线）

管线（基于 65 张人工标注 + 人眼真值迭代得出）：
    ① 刻度关键点模型（imgsz 640）→ 0线/16线方位 + 跨度
    ② 指针关键点模型（imgsz 640）→ 轴心（当圆心）+ 针尖
       （实测圆心用"指针轴心"比"筷子法交点"更准）
    ③ 针尖延长到标准针长 1060 px（模型给的针尖常偏短，延长后角度才稳）
    ③' 淡针兜底：若模型给的针长 < 700 px，说明它没看清那根很淡的细针尖
       → 改用「连续性扫描 + PCA 精修」自己找针：
         针 = 一条连续的淡线；表盘上的文字 = 断开的黑块，用连续性把二者分开
    ④ 读数 = 指针相对角度 ÷ 跨度 × 16
    ⑤ 标定：最终值 = 0.9934 × 读数 + 0.2064（用 65 张人眼真值拟合）
    ⑥ 可靠性标记

实测（65 张）：平均误差 0.28 MPa，中位 0.26 MPa，65/65 全部在 ±1.0 MPa 内。
对比：不加淡针兜底时最大误差 11.5 MPa，加了之后降到 0.93 MPa。

用法（在本文件夹打开终端，先激活装好 ultralytics 的环境）：
    python 读数_0到16kPa.py --image test.jpg
    python 读数_0到16kPa.py --batch 500 --img-dir D:\\照片 --out-csv 读数.xlsx
    python 读数_0到16kPa.py --image 某张.jpg --debug-dir 调试
"""
import argparse
import csv
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent

PROJECT_ROOT = HERE.parent.parent
MK_PT = str(PROJECT_ROOT / "models" / "刻度_0到16.pt")
MP_PT = str(PROJECT_ROOT / "models" / "指针_0到16.pt")
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

TIP_R = 1060.0          # 标准针尖距圆心（像素）
CAL_A = 0.9934          # 标定斜率（混合方案重拟合）
CAL_B = 0.2064          # 标定偏移
FLAG_TIP_D = 250.0      # 原始针尖距离 < 此值 → 方向不可靠
FLAG_CONF = 0.15        # 针尖置信度 < 此值 → 不可靠
FALLBACK_D = 700.0      # 模型针长 < 此值 → 判定"淡针"，改用连续性扫描兜底
SCAN_DROP = 9           # 淡针判定：比底色暗多少算"偏暗"


def list_images(directory):
    d = Path(directory)
    return sorted(p for p in d.rglob("*")
                  if p.is_file() and p.suffix.lower() in IMG_EXT
                  and not p.name.startswith("_"))


def imread_zh(path):
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def imwrite_zh(path, img):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", img)[1].tofile(str(path))


def line_intersection(p, q, u, v):
    d = (p[0] - q[0]) * (u[1] - v[1]) - (p[1] - q[1]) * (u[0] - v[0])
    if abs(d) < 1e-9:
        return None
    a1 = p[0] * q[1] - p[1] * q[0]
    a2 = u[0] * v[1] - u[1] * v[0]
    x = (a1 * (u[0] - v[0]) - (p[0] - q[0]) * a2) / d
    y = (a1 * (u[1] - v[1]) - (p[1] - q[1]) * a2) / d
    return (x, y)


def ang_of(cx, cy, px, py):
    return math.degrees(math.atan2(py - cy, px - cx)) % 360


def coarse_needle_dir(gray, cx, cy, bg, r0=200, r1=1000, step=15, perp=4):
    """淡针粗定位：沿每个方向统计「连续偏暗」比例。
    针是一条连续的淡线，表盘上的文字是断开的黑块 —— 用连续性就能把它们分开。"""
    h, w = gray.shape[:2]
    best = (0.0, 0)
    for a in range(0, 360, 2):
        th = math.radians(a)
        ux, uy = math.cos(th), math.sin(th)
        px_, py_ = -uy, ux
        cnt = tot = 0
        for r in range(r0, r1, step):
            mn = 255
            for o in range(-perp, perp + 1):
                x, y = int(cx + ux * r + px_ * o), int(cy + uy * r + py_ * o)
                if 0 <= x < w and 0 <= y < h:
                    mn = min(mn, int(gray[y, x]))
            tot += 1
            if mn < bg - SCAN_DROP:
                cnt += 1
        frac = cnt / tot if tot else 0.0
        if frac > best[0]:
            best = (frac, a)
    return best[1]


def refine_tip(gray, cx, cy, th0, bg, span=10.0, r_lo=250, r_hi=1050):
    """沿粗略方向逐半径找针脊点，再用 PCA 拟合针轴。
    返回 (针尖坐标, 找到的脊点数) —— 脊点太少说明没找到针，不可信。"""
    h, w = gray.shape[:2]
    pts = []
    for r in range(r_lo, r_hi, 25):
        ba, bv = None, 255
        a = th0 - span
        while a <= th0 + span:
            th = math.radians(a)
            x, y = int(cx + math.cos(th) * r), int(cy + math.sin(th) * r)
            if 0 <= x < w and 0 <= y < h:
                v = int(gray[y, x])
                if v < bv:
                    bv, ba = v, a
            a += 0.4
        if ba is not None and bv < bg - 6:
            pts.append((cx + math.cos(math.radians(ba)) * r,
                        cy + math.sin(math.radians(ba)) * r))
    n = len(pts)
    if n < 4:
        th = math.radians(th0)
        return (cx + math.cos(th) * TIP_R, cy + math.sin(th) * TIP_R), n
    d = np.array(pts).mean(0) - np.array([cx, cy])
    if np.linalg.norm(d) < 1:
        th = math.radians(th0)
        return (cx + math.cos(th) * TIP_R, cy + math.sin(th) * TIP_R), n
    u = d / np.linalg.norm(d)
    return (cx + u[0] * TIP_R, cy + u[1] * TIP_R), n


def read_one(mk, mp, image_path, debug_dir=None):
    """照片 → 读数(MPa)。返回 dict：ok/value/flag 等"""
    rk = mk.predict(str(image_path), conf=0.25, imgsz=640, verbose=False)[0]
    rp = mp.predict(str(image_path), conf=0.25, imgsz=640, verbose=False)[0]
    if (rk.keypoints is None or len(rk.keypoints) == 0
            or rp.keypoints is None or len(rp.keypoints) == 0):
        return {"ok": False, "error": "关键点没检出来"}
    k = rk.keypoints.xy[0].cpu().numpy()
    p2 = rp.keypoints.xy[0].cpu().numpy()
    # ② 圆心优先取「指针轴心」（实测比筷子法交点准：轴心偏移更小）
    cx, cy = float(p2[0][0]), float(p2[0][1])
    h_img, w_img = rk.orig_shape
    if not (-0.2 * w_img <= cx <= 1.2 * w_img and -0.2 * h_img <= cy <= 1.2 * h_img):
        # 轴心离谱 → 退回筷子法交点
        center = line_intersection(k[0], k[1], k[2], k[3])
        if center is None:
            return {"ok": False, "error": "圆心求不出来"}
        cx, cy = center
    m0 = ((k[0][0] + k[1][0]) / 2, (k[0][1] + k[1][1]) / 2)
    m25 = ((k[2][0] + k[3][0]) / 2, (k[2][1] + k[3][1]) / 2)
    a0 = ang_of(cx, cy, m0[0], m0[1])
    a25 = ang_of(cx, cy, m25[0], m25[1])
    span = (a25 - a0) % 360
    if not (200 <= span <= 320):
        return {"ok": False, "error": f"跨度异常 {span:.0f}°"}

    tip_conf = float(rp.keypoints.conf[0][1])
    base, tip = p2[0], p2[1]
    raw_d = math.hypot(tip[0] - base[0], tip[1] - base[1])
    used_fallback = False
    fb_pts = 99
    if raw_d < FALLBACK_D:
        # ③' 淡针兜底：模型没看清细针尖时，改用"连续性扫描 + PCA 精修"
        img_gray = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8),
                                cv2.IMREAD_GRAYSCALE)
        if img_gray is not None:
            bg = float(np.median(img_gray))
            th0 = coarse_needle_dir(img_gray, base[0], base[1], bg)
            tip, fb_pts = refine_tip(img_gray, base[0], base[1], th0, bg)
            used_fallback = True
    # ③ 针尖延长到标准针长（模型给的针尖常偏短，延长后角度才稳）
    d_now = math.hypot(tip[0] - base[0], tip[1] - base[1])
    if d_now >= 1:
        tip = (base[0] + (tip[0] - base[0]) / d_now * TIP_R,
               base[1] + (tip[1] - base[1]) / d_now * TIP_R)
    ap = ang_of(cx, cy, tip[0], tip[1])
    raw_value = ((ap - a0) % 360) / span * 16
    value = CAL_A * raw_value + CAL_B     # ⑤ 标定

    # ⑥ 可靠性标记（兜底成功就不标记，只有兜底也没找到针时才提醒）
    flag = None
    if used_fallback and fb_pts < 4:
        flag = "需人工复核（淡针兜底也没找准，建议看图）"
    elif not used_fallback and tip_conf < FLAG_CONF:
        flag = "需人工复核（针尖置信度低）"

    if debug_dir:
        img = imread_zh(image_path)
        cv2.circle(img, (int(cx), int(cy)), 4, (0, 200, 0), -1)
        for p in [m0, m25]:
            cv2.line(img, (int(cx), int(cy)), (int(p[0]), int(p[1])), (255, 0, 0), 1)
        cv2.line(img, (int(cx), int(cy)), (int(tip[0]), int(tip[1])), (0, 0, 255), 2)
        cv2.putText(img, f"{value:.2f} MPa", (6, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        imwrite_zh(debug_dir / f"{image_path.stem}.jpg", img)

    return {"ok": True, "value": round(value, 2),
            "tip_conf": round(tip_conf, 3), "raw_d": int(raw_d),
            "flag": flag, "fallback": used_fallback, "image": str(image_path)}


def write_batch_results(out_path, header, rows, vals, fails, flag_n):
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "明细"
        ws.append(header)
        for row in rows:
            ws.append(row)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", start_color="1F3864")
            c.alignment = Alignment(horizontal="center")
        for i, w in enumerate([18, 10, 10, 10, 10, 26], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        total = len(rows)
        ok_n = len(vals)
        pairs = [
            ("统计时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("图片总数", total),
            ("成功", ok_n),
            ("失败", len(fails)),
            ("需人工复核", flag_n),
            ("成功率", f"{ok_n / total * 100:.1f}%" if total else "—"),
        ]
        if vals:
            pairs += [
                ("读数最小", f"{min(vals):.2f} MPa"),
                ("读数平均", f"{sum(vals) / len(vals):.2f} MPa"),
                ("读数最大", f"{max(vals):.2f} MPa"),
            ]
        ws2 = wb.create_sheet("汇总")
        for k, v in pairs:
            ws2.append([k, v])
        for i in range(1, len(pairs) + 1):
            ws2.cell(row=i, column=1).font = Font(bold=True, color="1F3864")
        ws2.column_dimensions["A"].width = 14
        ws2.column_dimensions["B"].width = 24
        if fails:
            start = len(pairs) + 2
            ws2.cell(row=start, column=1, value="失败清单").font = Font(bold=True, color="C00000")
            ws2.cell(row=start, column=2, value="原因").font = Font(bold=True, color="C00000")
            for j, (name, err) in enumerate(fails, 1):
                ws2.cell(row=start + j, column=1, value=name)
                ws2.cell(row=start + j, column=2, value=err)
        try:
            wb.save(str(out))
        except PermissionError:
            # 目标文件正被 Excel 打开 → 自动换个带时间戳的名字，别让整批白跑
            alt = out.with_name(f"{out.stem}_{datetime.now():%H%M%S}{out.suffix}")
            wb.save(str(alt))
            print(f"  [提示] {out.name} 正被 Excel 占用，已改存为 {alt.name}")
            out = alt
    else:
        with open(out, "w", newline="", encoding="utf-8-sig") as fh:
            wr = csv.writer(fh)
            wr.writerow(header)
            wr.writerows(rows)
    return str(out)


def main():
    ap = argparse.ArgumentParser(description="0~16 MPa 压力表读数（优化管线 + 标定）")
    ap.add_argument("--image", default=None, help="单张图片路径")
    ap.add_argument("--batch", type=int, default=0, help="批量处理 N 张")
    ap.add_argument("--img-dir", default=None, help="批量模式图片目录（含子文件夹）")
    ap.add_argument("--conf", type=float, default=0.25, help="置信度阈值")
    ap.add_argument("--out-csv", default=None, help="结果路径：.xlsx 或 .csv；不填自动按日期命名")
    ap.add_argument("--debug-dir", default=None, help="调试图目录")
    args = ap.parse_args()

    mk = YOLO(MK_PT)
    mp = YOLO(MP_PT)

    if args.image:
        r = read_one(mk, mp, Path(args.image),
                     debug_dir=Path(args.debug_dir) if args.debug_dir else None)
        for key in ("ok", "value", "tip_conf", "raw_d", "flag", "error"):
            if key in r and r[key] is not None:
                print(f"  {key:<12} {r[key]}")
        return 0 if r["ok"] else 1

    if args.batch:
        img_dir = Path(args.img_dir) if args.img_dir else Path(".")
        files = list_images(img_dir)[:args.batch]
        vals, fails, rows, flag_n, fb_n = [], [], [], 0, 0
        t0 = time.time()
        for p in files:
            r = read_one(mk, mp, p,
                         debug_dir=Path(args.debug_dir) if args.debug_dir else None)
            if r["ok"]:
                vals.append(r["value"])
                if r["flag"]:
                    flag_n += 1
                if r.get("fallback"):
                    fb_n += 1
                rows.append([p.name, r["value"], r["tip_conf"], r["raw_d"],
                             "淡针兜底" if r.get("fallback") else "模型直读",
                             r["flag"] or "ok"])
            else:
                fails.append((p.name, r["error"]))
                rows.append([p.name, "", "", "", "", r["error"]])
        print(f"\n成功 {len(vals)} / {len(files)}，失败 {len(fails)}，"
              f"其中淡针兜底 {fb_n} 张，需人工复核 {flag_n}")
        if vals:
            print(f"读数：最小 {min(vals):.2f}　平均 {sum(vals) / len(vals):.2f}　"
                  f"最大 {max(vals):.2f} MPa")
        print(f"耗时 {time.time() - t0:.0f} 秒")
        for name, err in fails:
            print(f"  失败 {name}：{err}")
        header = ["图片", "读数(MPa)", "针尖置信度", "原始针长", "来源", "状态"]
        if not args.out_csv:
            args.out_csv = str(img_dir / f"读数_{datetime.now():%Y%m%d_%H%M}.xlsx")
        out = write_batch_results(args.out_csv, header, rows, vals, fails, flag_n)
        print(f"结果已写入：{out}")
        return 0

    print("请指定 --image 或 --batch + --img-dir，例如：")
    print("  python 读数_0到16kPa.py --image D:\\照片\\1.jpg")
    print("  python 读数_0到16kPa.py --batch 500 --img-dir D:\\照片")
    return 1


if __name__ == "__main__":
    sys.exit(main())
