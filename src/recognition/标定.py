# -*- coding: utf-8 -*-
"""方案3：用「照片 + 人眼真值」配对回归，把系统性偏差一次标定掉。

为什么需要它
------------
读数 = CAL_A×(针角−a0)/span×16 + CAL_B。其中 a0/span 来自模型的 4 个刻度关键点，
实测它们落在 0/16 的**数字**上（偏 3~7°），而且 span 在几张照片之间差 2%
（真实跨度是物理恒定的）。这类**系统性**偏差，多帧锁定治不了 —— 它只治随机抖动。
唯一能吃掉系统偏差的，是**真值**。

本脚本做的事
------------
  1. 对每张照片跑当前算法，得到"算法读数 v"；
  2. 读入你给的人眼真值 t；
  3. 线性回归    t = α·v + β   （α 修比例误差=跨度偏差，β 修零点误差=基准偏差）；
  4. **留一交叉验证（LOOCV）**：抽掉一组、用其余组预测它 —— 这是唯一诚实的泛化检验。
     样本内残差必定变小（几组点拟合 2 个参数 = 过拟合），不能拿它下结论。
  5. 把 (α, β) 写进 读数增强.py 的 CAL2_A / CAL2_B —— 之后**所有**读数自动套用。
     ⚠ 若留一验证净改善 < 0.03 MPa，脚本会**拒绝写入**（样本不足，写回只会过拟合）。

样本量要求
----------
  至少 **10 组**，且**覆盖整个量程**（如 1 / 3 / 5 / 7 / 9 / 11 / 13 / 15 MPa 附近各一张）。
  4~5 组挤在中段，α 的 95% 置信区间宽到包含 1.0，等于什么都证明不了；
  更危险的是它仍会给出一个"好看"的样本内残差。

真值怎么给（二选一）
--------------------
  方式一（推荐，最省事）：在 待读图片\ 里给每张图建一个**同名 txt**，内容就是读数：
      待读图片\1.jpg   →   待读图片\1.txt   内容比如  8.1
  方式二：写一个 CSV（两列：图片,真值），运行时用 --csv 指定。

用法
----
    python 标定.py                 # 只报告（不改任何文件）
    python 标定.py --apply         # 回归并把结果写回 读数增强.py
    python 标定.py --reset         # 把标定层还原成 1.0 / 0.0（撤销标定）
    python 标定.py --force         # 配对少于 3 组时也允许 --apply

说明：脚本内部一律把标定层暂时重置为 (1,0) 再测量，所以**重复运行是幂等的** ——
不会把上一次的标定叠加进去。
"""
import argparse
import importlib.util
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np

HERE = Path(__file__).resolve().parent
ENH = HERE / "读数增强.py"
PROJECT_ROOT = HERE.parent.parent
PICS = PROJECT_ROOT / "data/input"
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def load_enh():
    spec = importlib.util.spec_from_file_location("rd", str(ENH))
    m = importlib.util.module_from_spec(spec)
    sys.modules["rd"] = m
    spec.loader.exec_module(m)
    return m


def parse_truth_file(p):
    """从同名 txt 里取第一个能转成 float 的数（跳过空行与 # 注释）。"""
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for tok in re.split(r"[\s,;=]+", line):
            try:
                return float(tok)
            except ValueError:
                continue
    return None


def collect_pairs(csv_path=None):
    """返回 [(图片路径, 真值)]，只含配对齐全的。"""
    pairs = []
    if csv_path:
        for line in Path(csv_path).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [s for s in re.split(r"[,\t;]+", line) if s.strip()]
            if len(parts) < 2:
                continue
            try:
                t = float(parts[1])
            except ValueError:
                continue
            img = Path(parts[0])
            if not img.is_absolute():
                img = PROJECT_ROOT / img
            if img.exists():
                pairs.append((img, t))
        return pairs
    for f in sorted(PICS.iterdir()):
        if not f.is_file() or f.suffix.lower() not in IMG_EXT:
            continue
        t = parse_truth_file(f.with_suffix(".txt"))
        if t is not None:
            pairs.append((f, t))
    return pairs


def measure(RD, mk, mp, pairs, use_img_scale):
    """在指定基准策略下测所有图（标定层强制为 1/0，保证幂等）。"""
    RD.SCALE_FROM_IMAGE = use_img_scale
    RD.CAL2_A, RD.CAL2_B = 1.0, 0.0
    rows = []
    for p, t in pairs:
        r = RD.read_one(mk, mp, p)
        if not r.get("ok") or r.get("value") is None:
            rows.append((p, t, None, r.get("error") or "读数失败"))
            continue
        rows.append((p, t, r, None))
    return rows


def report(title, rows):
    ok = [(p, t, r) for (p, t, r, e) in rows if r is not None]
    print("=" * 78)
    print(title)
    print("=" * 78)
    if not ok:
        print("  没有可用的配对。\n")
        return None
    print("  %-12s %-9s %-9s %-9s %-9s %-8s %s" %
          ("图片", "算法读数", "人眼真值", "残差", "基准来源", "针角", "基准线 0/16"))
    xs, ys = [], []
    for p, t, r in ok:
        v = r["value"]
        base = str(r.get("scale_src", "模型刻度点")).split("(")[0]
        if "a0_deg" in r:
            extra = "%.1f/%.1f" % (r["a0_deg"], r["a16_deg"])
            nd = "%.1f" % r.get("needle_deg", float("nan"))
        else:
            extra, nd = "原工具路径", "-"
        print("  %-12s %-9.2f %-9.2f %+9.2f %-9s %-8s %s" %
              (p.name, v, t, v - t, base, nd, extra))
        xs.append(v)
        ys.append(t)
    x, y = np.array(xs), np.array(ys)
    if len(x) < 2:
        print("\n  只有 1 组配对，无法回归（至少 2 组，建议 ≥4 组且覆盖不同压力）。\n")
        return None
    a, b = np.polyfit(x, y, 1)
    pred = a * x + b
    rmse = float(np.sqrt(((y - pred) ** 2).mean()))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = float(1 - ((y - pred) ** 2).sum() / ss_tot) if ss_tot > 1e-9 else float("nan")
    print("\n  回归：真值 = %.6f × 算法读数 %+.6f      R²=%.4f  RMSE=%.3f MPa" %
          (a, b, r2, rmse))
    print("  标定后残差：最大 %.3f MPa，平均 %.3f MPa" %
          (float(np.abs(y - pred).max()), float(np.abs(y - pred).mean())))
    print("  当前（未标定）残差：最大 %.3f MPa，平均 %.3f MPa" %
          (float(np.abs(y - x).max()), float(np.abs(y - x).mean())))

    # ★ 留一交叉验证 —— 唯一诚实的泛化检验。
    #   几个点拟合 2 个参数，样本内残差**必定**变小（过拟合），拿它下结论是自欺。
    #   留一是"抽掉这一组、用其余组去预测它"，才是这套标定拿去读新照片时的真实误差。
    gain = 0.0
    if len(x) >= 3:
        ec, eu = [], []
        for i in range(len(x)):
            m = np.ones(len(x), dtype=bool)
            m[i] = False
            ai, bi = np.polyfit(x[m], y[m], 1)
            ec.append(abs(ai * x[i] + bi - y[i]))
            eu.append(abs(x[i] - y[i]))
        gain = float(np.mean(eu) - np.mean(ec))
        print("  ★ 留一验证（真实泛化）：未标定 平均%.3f/最大%.3f  →  标定后 平均%.3f/最大%.3f  "
              "→ 净改善 %+.3f MPa" %
              (float(np.mean(eu)), float(np.max(eu)),
               float(np.mean(ec)), float(np.max(ec)), gain))
        if gain < 0.03:
            print("    ⚠ 净改善 < 0.03 MPa —— 标定**几乎没有泛化收益**，样本量不够，写回只会过拟合。")
    print()
    return a, b, rmse, r2, gain


def write_cal(a, b):
    src = ENH.read_text(encoding="utf-8")
    new = re.sub(r"^CAL2_A, CAL2_B = .*$",
                 "CAL2_A, CAL2_B = %.6f, %.6f" % (a, b), src, count=1, flags=re.M)
    if new == src:
        print("[!] 没找到 CAL2_A, CAL2_B 那一行，未写入。")
        return False
    ENH.write_text(new, encoding="utf-8")
    print("[√] 已写入 读数增强.py ：CAL2_A = %.6f, CAL2_B = %.6f" % (a, b))
    return True


def main():
    ap = argparse.ArgumentParser(description="方案3：真值标定（线性回归）")
    ap.add_argument("--csv", default=None, help="真值表 CSV（两列：图片,真值）")
    ap.add_argument("--apply", action="store_true", help="把回归结果写回 读数增强.py")
    ap.add_argument("--reset", action="store_true", help="把标定层还原为 1.0 / 0.0")
    ap.add_argument("--force", action="store_true", help="配对 <3 组时也允许 --apply")
    ap.add_argument("--model-base", action="store_true",
                    help="额外列出『模型刻度点当基准』的对照回归")
    args = ap.parse_args()

    if args.reset:
        write_cal(1.0, 0.0)
        return 0

    RD = load_enh()
    from ultralytics import YOLO
    mk = YOLO(str(PROJECT_ROOT / "models" / "刻度_0到16.pt"))
    mp = YOLO(str(PROJECT_ROOT / "models" / "指针_0到16.pt"))

    pairs = collect_pairs(args.csv)
    if not pairs:
        print("=" * 78)
        print("还没有可用的「照片 + 真值」配对。")
        print("=" * 78)
        print("请这样做（二选一）：")
        print("  1) 在 %s 里，给每张图建一个同名 txt，内容就是人眼读数：" % PICS)
        for f in sorted(PICS.glob("*")):
            if f.suffix.lower() in IMG_EXT:
                print("       %s  →  %s   内容比如  8.1" % (f.name, f.with_suffix(".txt").name))
        print("  2) 或写一个 CSV（两列：图片,真值），运行时加 --csv 文件名")
        print("\n提示：真值尽量覆盖不同压力（比如 2/5/8/12/15 MPa），")
        print("      只在很窄的区间取点，回归外推到其它读数会不可靠。")
        return 2

    print("配对到的样本：%d 组" % len(pairs))
    for p, t in pairs:
        print("   %-14s 真值 %.2f" % (p.name, t))
    print()

    cur = measure(RD, mk, mp, pairs, use_img_scale=True)
    res_img = report("方案1（图像法刻度线基准）+ 方案3（真值回归）", cur)

    if args.model_base:
        mb = measure(RD, mk, mp, pairs, use_img_scale=False)
        report("对照：只用模型刻度点当基准（旧的）", mb)

    if res_img is None:
        return 2
    a, b, rmse, r2, gain = res_img
    n = len([1 for (_p, _t, r, _e) in cur if r is not None])

    print("-" * 78)
    print("怎么应用：")
    print("  · 若 --apply：会写入  CAL2_A = %.6f, CAL2_B = %.6f" % (a, b))
    print("    含义：读数_新 = %.4f × 读数_旧 %+.4f" % (a, b))
    print("  · 不影响 待读图片 之外的任何既有行为，随时可用 --reset 撤销。")
    if n < 3:
        print("  ⚠ 只有 %d 组配对，回归结果不可靠；--apply 需要再加 --force。" % n)
    if abs(a - 1) > 0.05 or abs(b) > 0.3:
        print("  ⚠ 修正幅度较大（α 偏离 1 达 %.1f%%，β=%.2f），建议先核对标注图确认基准线压准了刻度。" %
              (abs(a - 1) * 100, b))
    if gain < 0.03:
        print("  ✖ 留一验证净改善仅 %+.3f MPa —— **建议不要 --apply**。" % gain)
        print("     原因：样本太少，样本内的\"变准\"是过拟合。继续读新照片不会有这个收益。")
        print("     正确做法：补样本到 ≥10 组、铺满整个量程（含高压段），再回来跑一次。")

    if args.apply:
        if n < 3 and not args.force:
            print("\n[×] 配对不足 3 组，未写入。确认要写就加 --force。")
            return 2
        if gain < 0.03:
            print("\n[×] 留一验证显示没有泛化收益（净改善 %+.3f MPa），已拒绝写入，避免过拟合。" % gain)
            print("    确认要硬写就加 --force（不推荐）。")
            if not args.force:
                return 2
        print()
        write_cal(a, b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
