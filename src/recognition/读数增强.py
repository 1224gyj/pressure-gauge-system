# -*- coding: utf-8 -*-
"""
读数增强层 —— 专治「摄像头实时抓图」这种小画面/低分辨率场景。

为什么需要它（2026-10-12 实测得出，全程有调试图作证）：

  原工具 `读数_0到16kPa.py` 是在「2734x2734 方图、表盘占满整幅」的照片集上
  训练与调参的。整套阈值/扫描半径都是**绝对像素值**，一旦换成
  640x360 的实时抓图（表盘只占画面中间一块、半径仅约 145px），三处同时失配：

  ① FALLBACK_D = 700px 的「淡针」判据恒定触发
     参考照片针长 1028px → 不触发；实时帧针长 164px → 必然触发。
  ② 兜底通道的扫描半径写死 r∈[200,1000]、精修 r∈[250,1050]
     实时帧表盘半径才 145px —— 整个扫描环落在表盘**外面**，
     于是它把右侧的暗背景（键盘/机箱）当成针，方向错到横向。
     实测：真针约 299°，兜底给出 10° → 读数 14.13 MPa（真值约 10.0）。
  ③ 模型给的「轴心」在小画面下误差 40~65px（= 表盘半径的 30~45%）
     参考照片上这点偏移可忽略，实时帧上直接把针角带偏十几度。

  本层的对策（每条都对应上面一条）：
  ① 判据改成**尺度自适应**：针长 < 0.55 × 表盘半径 才算淡针
  ② 需要兜底时，扫描半径按表盘半径换算；针角另用「自圆心出发的最长连续暗段」
     独立测量（该法尺度无关，实测与肉眼一致，误差 1~2°）
  ③ 圆心改用「筷子法(0线×16线交点)」与「Hough 圆」交叉验证，两者一致才采信，
     模型轴心仅作最后兜底
  ④ 送模型前先把表盘裁成方形、放大回训练时的尺度（表盘占满输入画面）

  标定沿用原工具的 CAL_A/CAL_B —— 未改动，故与原工具同源。

  ★★ 2026-10-13 晚间新增：**多帧锁定圆心**（read_one_multi）
     单帧各自检圆心，帧间会抖 ±3~5px；而针角 = ang_of(圆心, 针尖)，
     圆心一抖角就跟着抖 —— 子码流半径只有 87~146px，3px 圆心偏移 ≈ 2.0°，
     正好是当前读数抖动的量级（1σ≈0.13MPa ≈ 2.1°）。
     表静止时圆心物理上不动，所以连拍 N 帧、取圆心**中位数**钉死，
     再让每帧在这个共同圆心下各自量针角、读数取中位数 —— 圆心抖动项被消掉。
     分工见 _prepare()（每帧各自定圆心）与 _measure()（按给定圆心量读数）。
"""
import importlib.util
import math
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent


def _load_original():
    """按路径加载原工具（原文件一字未改），只为复用它的角度/交点函数与标定常数。"""
    spec = importlib.util.spec_from_file_location("rd_orig", str(HERE / "读数_0到16kPa.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["rd_orig"] = m
    spec.loader.exec_module(m)
    return m


RD = _load_original()
ang_of = RD.ang_of
line_intersection = RD.line_intersection
CAL_A, CAL_B = RD.CAL_A, RD.CAL_B
TIP_R = RD.TIP_R

# ---------------------------------------------------------------- 方案3：真值标定层
# 读数 = CAL_A × (针角 − a0)/span × 16 + CAL_B          ← 现有（沿用原工具标定）
#      ↓ 再套一层（由 标定.py 用"照片 + 人眼真值"配对回归得出）
# 读数 = CAL2_A × 上式 + CAL2_B                        ← 本层
# 默认 CAL2_A=1, CAL2_B=0 → 行为与从前逐位一致。
# 标定后由 标定.py 自动改写这两个数（它会把新值写回本文件）。
CAL2_A, CAL2_B = 1.0, 0.0


def _apply_cal(v):
    """把标定层套到原始读数上（v 是 CAL_A/CAL_B 处理过的读数）。"""
    return CAL2_A * v + CAL2_B

FAINT_REL = 0.55          # 针长 / 表盘半径 < 此值 → 判定淡针
SCAN_REL = (0.22, 0.96)   # 扫描的相对半径区间（自圆心向外）
MIN_RUN_REL = 0.45        # 细暗段 / 可用扫描长度 > 此值 → 采信图像法量出的针角
STRONG_EVID = 0.60        # 图像法证据强于此值 → 不再因"模型针尖不一致"要求复核
CROP_RATIO = 2.4          # 裁切方形边长 = 表盘半径 × 此值
TARGET = 640              # 裁切后放大到该边长
THIN_W = 6                # ★ 横向暗宽 ≤ 此值才算"细"（针身）；指针尾部的配重块更粗，会被排除
SCAN_REFERENCE_R = 150.0  # 大画面的横向针宽按表盘半径换算；原小画面参数保持不变
SCAN_NATIVE_MAX_R = 200.0
GAP_TOL = 3               # 细暗线上允许的断续采样点数（抗 JPEG 噪点 / 反光）。
                          #   ★★ 2026-10-13 下午第二起真机故障：由 9 降到 3。
                          #   现象：同一轮 5 帧读数在 10.2 与 8.0 MPa 之间两簇跳（极差 2.27），
                          #         另一轮在 6.6~8.3 间连续跳（极差 1.71）。
                          #   根因（沿射线打灰度剖面查实）：真针方向是一条**从圆心连续
                          #         贯通到针尖**的暗线（104px 无断口）；而假候选方向（此处
                          #         是表盘中央的黑色商标/文字）在 r=51~59px 处**断开**，
                          #         两段各 ~20px / ~73px。GAP_TOL=9 恰好把那 8px 的断口
                          #         桥接起来，拼成 67px 的长"暗段"，反超真针的 64px →
                          #         argmax 选错 → 读数跳到量程反向的另一侧。
                          #   实测：降到 1~3 后，该轮判角由 {264,265} 收敛到 {302,303}，
                          #         峰值/次峰比由 1.015 抬到 1.06~1.22。
                          #   取舍：1 过严（另一轮开始抖），3 在上午/下午共 6 轮上均稳定。
MULTI_SPREAD_FLAG = 0.8   # 多帧读数极差 > 此值(MPa) → 认定为"帧间不一致"，标需复核
ANG_TOL = 12.0            # 帧间"自检针角"分歧 ≤ 此值(°) 才敢锁针角；超过就认为表真的动了
                          #   ★ 2026-10-13 实测标定：假翻转（表静止、argmax 翻错）实测 7~9°；
                          #     真位移（轮E：表从 9.5 跳到 11.6 MPa）实测 33°。12° 落在中间。
                          #     换算：span≈261° 对应 16 MPa → 12° ≈ 0.74 MPa，
                          #     与 MULTI_SPREAD_FLAG=0.8 同量级，两条判据互相自洽。
ANG_DISAGREE = 10.0       # ★ 2026-10-13 晚（用户报"读数 7.9、肉眼 9.35"）：图像法角与模型
                          #   针尖角分歧超过此值(°)时，就在备注里把两个值都写下来，
                          #   不再"悄悄忽略模型"。**只记录、不报警** —— 实测轮A（真值 13.4）
                          #   模型针尖系统性偏 27°，图像法（证据 98%）才是对的；拿分歧
                          #   去报警会在整列制造误报。真正要拦的"图像法锁错目标"由
                          #   read_one_multi 里的 evid 证据闸门负责。

# ---------------------------------------------------------------- 刻度基准（方案1）
# ★ 2026-10-13 深夜：用户指出"标注图里 16 的刻度线并没有对准"——查证属实。
#   根因：读数 = (针角 − a0)/span × 16，其中 a0/span **全部来自模型的 4 个刻度关键点**
#   （k[0..3] 两两取中点）。实测这 4 个点落在 0/16 的**数字**上，而数字相对刻度线
#   有 3~7° 的固定角偏移 → 基准整体偏了，且偏差按比例放大（读数越大错越多）。
#   证据：1.jpg 用"径向最长暗段"实测 16 刻度线在 47.75°，模型给 41.99°，差 +5.76°；
#         而该角度与人工逐像素确认的值（47.74°）吻合到 0.01°。
#   对策：改由**图像**直接找 0/16 两条长刻度线当基准；找不到/不自洽就回退模型点。
SCALE_FROM_IMAGE = True   # 总开关：True=优先用图像法刻度线，False=退回旧的模型点基准
SCALE_CONTRAST = 25       # 暗判据：该处比本角度亮底暗 ≥ 此灰阶值才算"刻度线"。
                          #   ★ 实测（2026-10-13）：原定 45 在**子码流小图**上过严 ——
                          #     JPEG 压缩把刻度线不少像素"抹淡"，45 只取到最暗的核心片段，
                          #     暗段被切碎（10~11px）；降到 20~25 后连成整条（28~30px，2.8 倍）。
                          #     大图（手机 12MP）本就清晰，25 与 45 结果几乎一致。
SCALE_WIN = 30.0          # 在模型基准角 ±此角度内寻找刻度线（窄窗口避开其它刻度）
SCALE_MIN_OUT = 0.86      # 真刻度线的**外端**半径须 ≥ 此值 × R：刻度线伸到近外圈，
                          #   而表盘上的厂商文字（"青岛科奥仪表…"）够不到这么外。
SCALE_MIN_LEN = 0.12      # 暗段长度须 ≥ 此值 × R（短于此的多为数字笔画/杂点）
SCALE_MIN_IN = 0.60       # 暗段**内端**半径须 ≥ 此值 × R：真刻度线不会深入圆心区
                          #   （实测大图 0 侧内端 0.72~0.76R、16 侧 0.74~0.78R；
                          #    而抓到圆心附近的暗物时内端会掉到 0.58~0.66R）
SCALE_SPAN_LO, SCALE_SPAN_HI = 250.0, 294.0   # 采信时允许的刻度跨度区间（°）
SCALE_MAX_DEV = 20.0      # 图像法跨度与模型跨度偏差 > 此值(°) → 视为检测失败，回退模型
SCALE_MAX_SHIFT = 15.0    # 单侧基准修正量 |Δ| > 此值(°) → 视为抓错目标，整对作废。
                          #   实测（大图三张）真实修正量：0 侧 +3.0~+4.7°、16 侧 +5.3~+7.1°；
                          #   而抓错目标时会出现 -25°（如 H 轮 16 侧抓到表盘另一侧的暗物）。


def _polar_runs(gray, cx, cy, R, nang=1440, n=260, r0_rel=0.45, r1_rel=1.00,
                contrast=SCALE_CONTRAST):
    """把表盘沿圆周展开成 (角度 × 半径) 采样矩阵，求**每个角度的最长连续暗段**。

    这是方案1的底层：刻度线在极坐标下就是"一根从外圈向内的暗柱"，
    展开后长度远大于数字笔画，可稳定区分（见文件头讨论）。

    ★ r1_rel 取 1.00（扫描止于表盘边缘）：实测真刻度线外端 ≤0.96R，
      而表盘**外面**是暗背景（桌面/机箱），若允许扫到 1.08R，
      降低对比度后那条外圈暗物会反超真刻度线（1.jpg 实测：135px vs 107px）。

    返回 dict：
      per      每格角度（°）
      rr       半径采样（像素，长度 n）
      best     每角度最长暗段长度（采样点数）
      bsi/bei  该暗段内端/外端在 rr 中的下标（-1 = 该角度无暗段）
      R        表盘半径（换算相对半径用）
    """
    th = np.radians(np.arange(nang) * (360.0 / nang))
    rr = np.linspace(r0_rel * R, r1_rel * R, n)
    X = np.rint(cx + np.cos(th)[:, None] * rr[None, :]).astype(np.int32)
    Y = np.rint(cy + np.sin(th)[:, None] * rr[None, :]).astype(np.int32)
    np.clip(X, 0, gray.shape[1] - 1, out=X)
    np.clip(Y, 0, gray.shape[0] - 1, out=Y)
    M = gray[Y, X].astype(np.float32)
    bg = np.percentile(M[:, ::5], 88, axis=1)      # 每个角度自己的亮底 → 抗反光/阴影
    dark = M < (bg[:, None] - contrast)            # 该处比本角度亮底暗 contrast 灰阶以上

    # 每行最长连续 True 段（差分 → 起止 → 取最长）
    pad = np.zeros((nang, 1), bool)
    d = np.diff(np.concatenate([pad, dark, pad], axis=1).astype(np.int8), axis=1)
    sp = np.argwhere(d == 1)
    ep = np.argwhere(d == -1)
    lens = ep[:, 1] - sp[:, 1]
    best = np.zeros(nang, np.int32)
    np.maximum.at(best, sp[:, 0], lens)
    sel = lens == best[sp[:, 0]]
    bsi = np.full(nang, -1, np.int32)
    bei = np.full(nang, -1, np.int32)
    bsi[sp[sel, 0]] = sp[sel, 1]
    bei[sp[sel, 0]] = ep[sel, 1] - 1
    return {"per": 360.0 / nang, "rr": rr, "best": best, "bsi": bsi, "bei": bei, "R": R}


def _pick_tick(pr, hint, window=SCALE_WIN):
    """在 hint ±window 里挑"最像刻度线"的一条。返回 (角度°, 长度px, 内端R, 外端R) 或 None。"""
    rr, best, bsi, bei = pr["rr"], pr["best"], pr["bsi"], pr["bei"]
    per, Rs, nang = pr["per"], pr["R"], len(best)
    step = rr[1] - rr[0]
    lo, hi = int((hint - window) / per), int((hint + window) / per)
    idx = np.arange(lo, hi + 1) % nang
    L = best[idx] * step
    out_rel = np.array([rr[bei[i]] if bei[i] >= 0 else 0.0 for i in idx]) / Rs
    in_rel = np.array([rr[bsi[i]] if bsi[i] >= 0 else 0.0 for i in idx]) / Rs
    for min_out, min_len in ((SCALE_MIN_OUT, SCALE_MIN_LEN),
                             (SCALE_MIN_OUT - 0.02, SCALE_MIN_LEN)):
        ok = (out_rel >= min_out) & (in_rel >= SCALE_MIN_IN) & (L >= min_len * Rs)
        if ok.any():
            j = int(np.argmax(np.where(ok, L, -1)))
            return (float(idx[j] * per % 360), float(L[j]), float(in_rel[j]), float(out_rel[j]))
    return None


def find_scale_ticks(gray, cx, cy, R, a0_hint, a25_hint):
    """图像法找 0 与 16 两条长刻度线 → 更准的刻度基准。

    返回 (a0, a25, info字符串)；任一环节不自洽就返回 None（调用方回退模型点）。
    自洽要求：两侧都找到、跨度落在 [SCALE_SPAN_LO, SCALE_SPAN_HI]、
    且与模型跨度偏差 ≤ SCALE_MAX_DEV（防止窗口内抓错目标导致基准跳到别的刻度）。
    """
    pr = _polar_runs(gray, cx, cy, R)
    t0 = _pick_tick(pr, a0_hint)
    t16 = _pick_tick(pr, a25_hint)
    if t0 is None or t16 is None:
        return None
    a0, a25 = t0[0], t16[0]
    span = (a25 - a0) % 360
    span_model = (a25_hint - a0_hint) % 360
    if not (SCALE_SPAN_LO <= span <= SCALE_SPAN_HI):
        return None
    if abs(span - span_model) > SCALE_MAX_DEV:
        return None
    d0 = ((a0 - a0_hint + 180) % 360) - 180      # 单侧修正量：抓错目标时会出现 ±25° 以上的大跳
    d16 = ((a25 - a25_hint + 180) % 360) - 180
    if abs(d0) > SCALE_MAX_SHIFT or abs(d16) > SCALE_MAX_SHIFT:
        return None
    info = ("图像法刻度线(0:%+.1f° 16:%+.1f° span%.1f° 长%.0f/%.0fpx)" % (
        d0, d16, span, t0[1], t16[1]))
    return a0, a25, info


def _circ_spread(angles):
    """一组角度（度）的最大两两圆周距离 —— 衡量"它们一致吗"。"""
    n = len(angles)
    if n < 2:
        return 0.0
    return max(abs((angles[i] - angles[j] + 180) % 360 - 180)
               for i in range(n) for j in range(i + 1, n))


def _ang_median(angles):
    """角度的圆周中位数（先把各角归一到相对首角的 ±180° 内，再取中位数）。"""
    ref = float(angles[0])
    d = [((float(a) - ref + 180) % 360) - 180 for a in angles]
    return (ref + float(np.median(d))) % 360


def _longest_runs(line, gap):
    """逐行（逐角度）求"最长连续 True 段"的长度，允许 gap 个采样点的断续。"""
    k = np.ones(gap, dtype=np.int8)
    closed = np.apply_along_axis(
        lambda row: np.convolve(row.astype(np.int8), k, mode="same") > 0, 1, line)
    out = np.zeros(line.shape[0], dtype=np.int32)
    for i in range(line.shape[0]):
        row = closed[i]
        if not row.any():
            continue
        idx = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
        out[i] = (idx[1::2] - idx[0::2]).max()
    return out


def imread_zh(path):
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def imwrite_zh(path, img):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", img)[1].tofile(str(path))


def find_circle(gray, cx0, cy0, r0):
    """在 (cx0,cy0) 附近、半径约 r0 处找表盘圆。返回 (cx,cy,r) 或 None。
    为速度起见，先在缩小的图上跑 Hough（圆检测尺度无关），再换算回原尺寸。"""
    if r0 is None or r0 < 20:
        return None
    H, W = gray.shape[:2]
    sc = min(1.0, 640.0 / max(H, W))
    small = gray if sc >= 0.999 else cv2.resize(gray, None, fx=sc, fy=sc,
                                                interpolation=cv2.INTER_AREA)
    cx1, cy1, r1 = cx0 * sc, cy0 * sc, r0 * sc
    lo, hi = int(max(15, r1 * 0.6)), int(r1 * 1.45)
    bl = cv2.GaussianBlur(small, (5, 5), 1.2)
    best = None
    for p2 in (45, 38, 30, 24):
        c = cv2.HoughCircles(bl, cv2.HOUGH_GRADIENT, dp=1.2,
                             minDist=max(40, int(r1)), param1=120, param2=p2,
                             minRadius=lo, maxRadius=hi)
        if c is None:
            continue
        for x, y, r in c[0]:
            score = math.hypot(x - cx1, y - cy1) / max(r1, 1)   # 离已知圆心越近越好
            if best is None or score < best[0]:
                best = (score, float(x), float(y), float(r))
        if best is not None and best[0] < 0.35:
            break
    if best is None:
        return None
    return best[1] / sc, best[2] / sc, best[3] / sc


def _scan_masks(gray, cx, cy, R, bg=None, max_steps=200):
    """逐角度扫横向灰度剖面 → 两张布尔掩膜（形状都是 (360, n)）：

      thin[a,i] ：在角度 a、半径 i 处，横向暗宽 ∈ [1, thin_w] —— "又细又暗" = 针的特征
      dark[a,i] ：横向暗宽 ≥ 1 —— 任意暗物（针、配重、表盘文字、暗背景都算）

    step 固定由 R 与 max_steps 决定，所以**同一 R 下不同帧的掩膜逐格对齐**，
    可以直接帧间相加做表决（见 _runs_multi）。这是多帧表决能成立的前提。
    """
    H, W = gray.shape[:2]
    thin_w = (max(THIN_W, int(round(THIN_W * R / SCAN_REFERENCE_R)))
              if R > SCAN_NATIVE_MAX_R else THIN_W)
    if bg is None:
        yy, xx = np.mgrid[0:H, 0:W]
        d = np.hypot(xx - cx, yy - cy)
        m = (d > 0.55 * R) & (d < 0.82 * R)
        bg = float(np.percentile(gray[m], 80)) if m.any() else float(np.median(gray))
    thr = bg - 45
    r_lo, r_hi = SCAN_REL[0] * R, SCAN_REL[1] * R
    step = max(1.0, (r_hi - r_lo) / float(max_steps))
    n = max(2, int((r_hi - r_lo) / step))
    rr = r_lo + np.arange(n) * step
    th = np.radians(np.arange(360.0))
    ux, uy = np.cos(th)[:, None], np.sin(th)[:, None]
    offs = np.arange(-thin_w, thin_w + 1)
    ux3, uy3 = ux[:, :, None], uy[:, :, None]          # (360,1,1)
    rr3 = rr[None, :, None]                            # (1,n,1)
    of3 = offs[None, None, :]                          # (1,1,2*thin_w+1)
    X = np.rint(cx + ux3 * rr3 - uy3 * of3).astype(np.int32)
    Y = np.rint(cy + uy3 * rr3 + ux3 * of3).astype(np.int32)
    np.clip(X, 0, W - 1, out=X)
    np.clip(Y, 0, H - 1, out=Y)
    dark = gray[Y, X] < thr                     # (360, n, 2*thin_w+1)
    wid = dark.sum(axis=2)                      # (360, n) 该处横向暗宽
    return (wid >= 1) & (wid <= thin_w), (wid >= 1), step, bg


def _runs_from(thin, dark, step):
    """掩膜 → 每个角度上「最长连续段」的长度（采样点数）。
    细判据整体失效（画面过暗 / 过糊 / 针真的断了）→ 退回"任意暗段"的老逻辑。"""
    runs = _longest_runs(thin, GAP_TOL)
    if runs.max() * step < 8.0:
        runs = _longest_runs(dark, GAP_TOL)
    return runs


def _scan_runs(gray, cx, cy, R, bg=None, max_steps=200):
    """单帧：逐角度扫描，返回 (runs, step, bg)。"""
    thin, dark, step, bg = _scan_masks(gray, cx, cy, R, bg, max_steps)
    return _runs_from(thin, dark, step), step, bg


def _runs_multi(masks, step):
    """多帧掩膜 → **逐格多数表决**后再取最长连续段。返回 (runs, agree)。

    ★★ 2026-10-13 晚间：只锁圆心不够，必须连"证据"一起表决。
      实测（轮F，`112551`~`112631`，表静止）：把圆心换成 5 帧中位数之后，
      5 帧读数反而从 9.72/10.03/10.23/10.20/10.29 裂成 10.28/9.67/10.31/9.75/9.73
      —— 分成了两簇。
      根因：`scan_needle` 的判据是"哪条细暗线最长"，而真针与次强候选（表盘中央的
      黑色商标/文字）只差 1~3 个采样点，属于**双稳态**：帧间 JPEG 噪点一变，
      argmax 就在两个峰之间来回翻。圆心换不换都救不了这个。
      对策：表静止 → 真针在**每一帧里都是暗的**（持续存在），而噪点/反光造成
      的假候选**帧帧不同**。所以把每帧的 thin 掩膜逐格相加、过半才保留，
      假候选被票掉、真针留下，再取最长连续段 —— argmax 自然就稳了。
      实测（同 6 轮）：轮F 极差 0.57→0.02，轮A 0.64→0.11，轮D 0.34→0.05；
      而"表真的动了"的轮E 不受影响（该轮的帧间差异在圆心/刻度上，不在针的暗线上）。
    """
    n = len(masks)
    need = n // 2 + 1                      # 过半：N=5 → 3，N=3 → 2，N=2 → 2
    thin = np.sum([m[0] for m in masks], axis=0) >= need
    dark = np.sum([m[1] for m in masks], axis=0) >= need
    runs = _runs_from(thin, dark, step)
    a_best = int(np.argmax(runs))
    return runs, a_best



def scan_needle(gray, cx, cy, R, bg=None, max_steps=200):
    """自圆心出发，逐角度找**最长的细暗段** —— 针是唯一"又细又长"且贯穿圆心的暗物。
    返回 (角度, 暗段长度 px, 表盘底色)。

    ★★ 2026-10-13 真机故障与两次修正（同一批 5 帧、压力表根本没动，读数却在
      3.6 与 13.2 MPa 之间乱跳）：

      根因：原判据是"逐射线找最长连续暗段"，它分不清【细长的针】与【指针尾部的
      黑色配重锥】。配重短而粗、但暗得更"实"，在小画面下（表盘半径仅 ~90px）
      常赢过被 JPEG 抹淡的细针 → 针角被判反 180° → 读数正好落到刻度盘的反向
      对称位置。验证方法：把圆心钉死重测，5 帧针角立刻收敛到 354~357°（极差 2°），
      证明是判据错、不是表在动。

      修正：只统计**横向宽度 ≤ THIN_W 像素**的暗像素（细 = 针，粗 = 配重），
      再用这条细暗线的最长连续长度定针向。
      实测：轮3 五帧读数极差 10.07 → 0.64 MPa；轮1/轮2 不受影响。

      试过但**更差**的两条路（留档，别再走）：
        · 把扫描区外移到 r∈[0.45R,1.0R] → 干净图上直接锁到「0 刻度线」。
        · 改成统计环带内「暗点密度」argmax → 轮3 极差反而 9.83，轮1 也崩。
      结论：**"细"这个物理属性才是把针和配重分开的钥匙**，单纯比"暗得多"没用。
    """
    runs, step, bg = _scan_runs(gray, cx, cy, R, bg, max_steps)
    a_best = int(np.argmax(runs))
    return a_best, int(round(runs[a_best] * step)), bg


def _predict(model, src):
    return model.predict(src, conf=0.25, imgsz=640, verbose=False)[0]


# ---------------------------------------------------------------- 第一步：备料
def _prepare(mk, mp, src, allow_legacy=True):
    """一帧 → 测量素材（含**本帧自己**检出的圆心/半径）。

    src 可以是图片路径，也可以是 BGR numpy 数组（连拍时直接用内存里的帧，不必落盘）。

    返回 dict：
      · 带 "err" 键        → 这一帧废了（图坏 / 关键点没检出）
      · delegate=True      → 大图，本层不适用，应交给原工具
      · 否则含 center/Rad/ctag 以及 k/p2/gray/raw_d/tip_conf，供 _measure() 使用
    """
    img = src if isinstance(src, np.ndarray) else imread_zh(src)
    if img is None:
        return {"err": "图片读不出来"}
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape

    rk = _predict(mk, src)
    rp = _predict(mp, src)
    if rk.keypoints is None or len(rk.keypoints) == 0 or \
       rp.keypoints is None or len(rp.keypoints) == 0:
        return {"err": "关键点没检出来"}
    k = rk.keypoints.xy[0].cpu().numpy()
    p2 = rp.keypoints.xy[0].cpu().numpy()
    tip_conf = float(rp.keypoints.conf[0][1])
    ax0 = (float(p2[0][0]), float(p2[0][1]))
    raw_d = math.hypot(p2[1][0] - ax0[0], p2[1][1] - ax0[1])

    prep = {"img": img, "gray": gray, "H": H, "W": W, "k": k, "p2": p2,
            "tip_conf": tip_conf, "raw_d": raw_d, "delegate": False}

    # 高清视频中 raw_d>=700 只说明像素更多，不能证明轴心正确或已经裁好。
    # 摄像头调用显式关闭旧大图路径，始终校正圆心并裁切；保留已有离线
    # 照片的默认行为，避免靠宽高比猜测图片来源或改变旧标定集的输出。
    if allow_legacy and raw_d >= RD.FALLBACK_D:
        prep["delegate"] = True
        return prep

    # ---------- 圆心 / 半径：筷子法 与 Hough 交叉验证 ----------
    def fuse(center_hint, r_hint):
        inter = line_intersection(*k[0:4]) if len(k) >= 4 else None
        pos = []
        if inter is not None and all(abs(v) < 1e6 for v in inter):
            pos.append(("筷子", (float(inter[0]), float(inter[1]))))
        cir = find_circle(gray, center_hint[0], center_hint[1], r_hint)
        if cir is not None:
            pos.append(("Hough", (cir[0], cir[1])))
        if not pos:
            return center_hint, r_hint, "模型轴心"
        if len(pos) == 2:
            d = math.hypot(pos[0][1][0] - pos[1][1][0], pos[0][1][1] - pos[1][1][1])
            if d <= 0.30 * r_hint:                      # 两者一致 → 取中点，最稳
                c = ((pos[0][1][0] + pos[1][1][0]) / 2, (pos[0][1][1] + pos[1][1][1]) / 2)
                tag = "筷子+Hough"
            else:                                        # 不一致 → 筷子法优先（用交点直接算角度）
                c, tag = pos[0][1], "筷子(与Hough分歧%.0fpx)" % d
        else:
            c, tag = pos[0][1], pos[0][0]
        m0 = ((k[0][0] + k[1][0]) / 2, (k[0][1] + k[1][1]) / 2)
        m25 = ((k[2][0] + k[3][0]) / 2, (k[2][1] + k[3][1]) / 2)
        r = max(math.hypot(m0[0] - c[0], m0[1] - c[1]),
                math.hypot(m25[0] - c[0], m25[1] - c[1])) / 0.90
        return c, r, tag

    center, Rad, ctag = fuse(ax0, max(raw_d, 1.0))

    # ---------- 裁表盘 → 放大回训练尺度 → 第二遍 ----------
    if Rad > 30:
        half = CROP_RATIO * Rad / 2.0
        x0, y0 = int(center[0] - half), int(center[1] - half)
        x1, y1 = int(center[0] + half), int(center[1] + half)
        x0c, y0c, x1c, y1c = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1c - x0c > 40 and y1c - y0c > 40:
            pad = 30
            crop = cv2.copyMakeBorder(img[y0c:y1c, x0c:x1c], pad, pad, pad, pad,
                                      cv2.BORDER_REPLICATE)
            x0c -= pad
            y0c -= pad
            s = TARGET / float(max(crop.shape[0], 1))
            big = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
            if big.shape[0] < TARGET:
                big = cv2.copyMakeBorder(big, 0, TARGET - big.shape[0], 0, 0, cv2.BORDER_REPLICATE)
            if big.shape[1] < TARGET:
                big = cv2.copyMakeBorder(big, 0, 0, 0, TARGET - big.shape[1], cv2.BORDER_REPLICATE)
            rk2, rp2 = _predict(mk, big), _predict(mp, big)
            if rk2.keypoints is not None and len(rk2.keypoints) and \
               rp2.keypoints is not None and len(rp2.keypoints):
                k2 = rk2.keypoints.xy[0].cpu().numpy()
                q2 = rp2.keypoints.xy[0].cpu().numpy()
                # 映射回原图坐标，后续一律用原图坐标系
                def back(pt):
                    return ((pt[0]) / s + x0c, (pt[1]) / s + y0c)
                k = np.array([back(pt) for pt in k2])
                p2 = np.array([back(pt) for pt in q2])
                prep["k"], prep["p2"] = k, p2
                tip_conf = float(rp2.keypoints.conf[0][1])
                ax0 = (float(p2[0][0]), float(p2[0][1]))
                raw_d = math.hypot(p2[1][0] - ax0[0], p2[1][1] - ax0[1])
                prep["tip_conf"], prep["raw_d"] = tip_conf, raw_d
                center, Rad, ctag = fuse(ax0, max(raw_d, 1.0))
                prep["used_crop"] = True

    prep.update({"center": center, "Rad": Rad, "ctag": ctag})
    return prep


# ---------------------------------------------------------------- 第二步：量读数
def _measure(prep, center, Rad, needle=None, scale=None):
    """在**给定的**圆心/半径下量这一帧的读数。

    ★ 这是多帧锁定圆心的关键切分点：圆心由调用方给（单帧模式给本帧自己的，
      多帧模式给 N 帧圆心的中位数），本函数只用它算 a0/span/针角。
      圆心一变，a0、a25、针角全都要跟着重算 —— 所以不能缓存。

    needle：可选，(a_needle, run_len, evid, source_tag)。
      多帧模式把"N 帧掩膜多数表决"得到的针角从这里传进来 —— 表静止时，
      这个共同针角比任何单帧自己 argmax 出来的都稳（见 _runs_multi）。
    scale：可选，(a0, a25, info)。
      多帧模式把"N 帧共同检出的刻度基准"从这里传进来，保证整组用**同一对**
      基准线；不传则本帧自己检一次（单帧路径）。

    返回 dict；带 "err" 键表示这一帧在给定圆心上算不出合法读数。
    """
    if center is None or Rad is None or Rad <= 0:
        return {"err": "圆心或半径无效"}
    k, p2, gray = prep["k"], prep["p2"], prep["gray"]
    m0 = ((k[0][0] + k[1][0]) / 2, (k[0][1] + k[1][1]) / 2)
    m25 = ((k[2][0] + k[3][0]) / 2, (k[2][1] + k[3][1]) / 2)
    a0_model = ang_of(center[0], center[1], m0[0], m0[1])
    a25_model = ang_of(center[0], center[1], m25[0], m25[1])

    # ---------- 刻度基准：优先"图像法刻度线"（方案1），失败则回退模型关键点 ----------
    # 模型的 4 个刻度点实际落在 0/16 的**数字**上（实测偏 3~7°），图像法直接找
    # 长刻度线可消掉这条系统性基准误差。find_scale_ticks 自带自洽校验，不可信时返回 None。
    a0, a25, scale_src = a0_model, a25_model, "模型刻度点"
    if scale is not None:
        a0, a25, scale_src = scale          # 多帧：整组共用同一对基准线
    elif SCALE_FROM_IMAGE:
        st = find_scale_ticks(gray, center[0], center[1], Rad, a0_model, a25_model)
        if st is not None:
            a0, a25, scale_src = st         # 单帧：本帧自己检

    span = (a25 - a0) % 360
    if not (200 <= span <= 320):
        return {"err": "跨度异常 %.0f°" % span, "span": span, "center": center,
                "radius": round(Rad, 1)}

    # ---------- 针角：图像法为主，模型针尖为辅 ----------
    a_model = ang_of(center[0], center[1], p2[1][0], p2[1][1])
    bg = None
    if needle is not None:
        a_needle, run_len, evid, src_tag = needle
        a_scan = a_needle
    else:
        a_scan, run_len, bg = scan_needle(gray, center[0], center[1], Rad)
        scan_px = (SCAN_REL[1] - SCAN_REL[0]) * Rad
        evid = run_len / max(scan_px, 1e-6)  # 图像法证据强度：细暗段占可用扫描长度的比例
        src_tag = "-"
        if evid >= MIN_RUN_REL:
            a_needle, src_tag = a_scan, "图像法"
        else:
            a_needle, src_tag = a_model, "模型针尖"
    diff = abs((a_needle - a_model + 180) % 360 - 180)
    raw_d, tip_conf = prep["raw_d"], prep["tip_conf"]
    if needle is None and src_tag == "模型针尖" and raw_d < FAINT_REL * Rad and diff > 25:
        a_needle, src_tag = a_scan, "图像法(兜底)"

    v_raw = ((a_needle - a0) % 360) / span * 16
    value = _apply_cal(CAL_A * v_raw + CAL_B)

    flag = None
    note = None
    v_scan = _apply_cal(CAL_A * ((a_scan - a0) % 360) / span * 16 + CAL_B)
    v_model = _apply_cal(CAL_A * ((a_model - a0) % 360) / span * 16 + CAL_B)
    if evid < 0.35 and tip_conf < RD.FLAG_CONF:
        flag = "需人工复核（针看不清）"
    elif diff > 30 and evid < STRONG_EVID:
        # 只有图像法证据不足时，"与模型针尖不一致"才值得人工复核。
        # 小画面（表盘半径 ≲200px）下模型关键点本就不可靠（见文件头 ②③），
        # 证据充分时以图像法为准，仅留一条备注，避免整列误报把真问题淹掉。
        # ★ 2026-10-13 晚：试过再引入一条"分歧>10° 且 tip_conf<0.85 → 标复核"，
        #   结果在轮A 上大面积误报（真值 13.4、图像法 13.44 完全正确）。
        #   根因：tip_conf 与表盘大小强相关（R=92→0.07，R=147→0.59，R=169→0.88），
        #   它是"模型对这点有多少把握"，不是"针清不清楚"。已撤销，勿再走。
        flag = "需人工复核（图像法与模型差 %.0f°）" % diff
    elif not (215 <= span <= 305):
        flag = "需人工复核（跨度 %.0f° 偏离常态）" % span
    if diff > ANG_DISAGREE and flag is None:
        note = "图像法 %.2f 与模型针尖 %.2f 差 %.0f°（图像法证据 %.0f%%，以图像法为准）" % (
            v_scan, v_model, diff, evid * 100)

    return {"value": value, "a0": a0, "a25": a25, "span": span, "m0": m0, "m25": m25,
            "a_scan": a_scan, "a_model": a_model, "a_needle": a_needle,
            "run_len": run_len, "evid": evid, "bg": bg, "source": src_tag,
            "flag": flag, "note": note, "tip_conf": tip_conf, "raw_d": raw_d,
            "scale_src": scale_src, "a0_model": a0_model, "a25_model": a25_model}


# ---------------------------------------------------------------- 第三步：成行
def _result(m, center, Rad, ctag, label):
    """把一次成功测量整理成对外结果（字段与原工具 read_one 兼容 + 诊断字段）。"""
    return {"ok": True, "value": round(m["value"], 2),
            "tip_conf": round(m["tip_conf"], 3), "raw_d": int(m["raw_d"]),
            "flag": m["flag"], "note": m["note"],
            "fallback": (m["source"] != "模型针尖"),
            "image": label,
            "center": (round(center[0], 1), round(center[1], 1)),
            "radius": round(Rad, 1), "span": round(m["span"], 1),
            "needle_deg": round(m["a_needle"], 1), "model_deg": round(m["a_model"], 1),
            "run_len": m["run_len"], "center_tag": ctag,
            "a0_deg": round(m["a0"], 1), "a16_deg": round(m["a25"], 1),
            "evid": round(m["evid"], 3), "source": m["source"],
            "scale_src": m.get("scale_src", "模型刻度点"),
            "a0_model_deg": round(m.get("a0_model", m["a0"]), 1),
            "a16_model_deg": round(m.get("a25_model", m["a25"]), 1)}


def _draw(prep, m, center, Rad, label, debug_dir, stem):
    """把锁定圆心、两条刻度基准线、图像法针角（红）、模型针角（黄）画回原图。

    基准线画**两套**，方便人工核对"到底对准刻度线没有"：
      · 品红（粗）＝ 图像法找的长刻度线 —— 本次实际参与读数计算的基准
      · 蓝（细）  ＝ 模型给的刻度关键点 —— 旧基准，实测偏在 0/16 的"数字"上
    """
    vis = prep["img"].copy()
    cv2.circle(vis, (int(center[0]), int(center[1])), int(Rad), (0, 200, 0), 1)
    cv2.circle(vis, (int(center[0]), int(center[1])), 3, (0, 200, 0), -1)
    for pt in (m["m0"], m["m25"]):                       # 模型刻度点（旧基准）
        cv2.line(vis, (int(center[0]), int(center[1])), (int(pt[0]), int(pt[1])),
                 (255, 140, 0), 1)
    for ang in (m["a0"], m["a25"]):                      # 图像法刻度线（实际基准）
        t = math.radians(ang)
        cv2.line(vis, (int(center[0]), int(center[1])),
                 (int(center[0] + math.cos(t) * Rad * 1.02),
                  int(center[1] + math.sin(t) * Rad * 1.02)), (255, 0, 255), 2)
    for ang, col, w in ((m["a_needle"], (0, 0, 255), 2), (m["a_model"], (0, 255, 255), 1)):
        t = math.radians(ang)
        cv2.line(vis, (int(center[0]), int(center[1])),
                 (int(center[0] + math.cos(t) * Rad * 1.1),
                  int(center[1] + math.sin(t) * Rad * 1.1)), col, w)
    cv2.putText(vis, label, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
    tag = "基准:%s" % str(m.get("scale_src", "模型刻度点")).split("(")[0]
    cv2.putText(vis, tag, (6, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)
    imwrite_zh(Path(debug_dir) / ("%s.jpg" % stem), vis)


def read_one(mk, mp, image_path, debug_dir=None, allow_legacy=True):
    """单帧：照片 → 读数(MPa)。圆心用**本帧自己**检出的那个。"""
    src = image_path if isinstance(image_path, np.ndarray) else Path(image_path)
    label = "<内存帧>" if isinstance(src, np.ndarray) else str(src)
    debug_dir = Path(debug_dir) if debug_dir else None
    prep = _prepare(mk, mp, src, allow_legacy=allow_legacy)
    if "err" in prep:
        return {"ok": False, "error": prep["err"]}
    if prep.get("delegate"):
        # 大图（表盘占满整幅）走原工具路径，读数由原工具给出。
        # ★ 标定层必须同样套上 —— 否则方案3（真值标定）对这类照片完全无效，
        #   而它们恰恰是画面最清晰、最该被标定受益的一类。默认 (1,0) 时逐位不变。
        if isinstance(src, np.ndarray):
            # 原工具按文件读取；PNG 保留内存帧像素，临时文件在读取结束后自动清理。
            with tempfile.TemporaryDirectory(prefix="gauge_frame_") as tmp:
                temp_image = Path(tmp) / "memory_frame.png"
                cv2.imencode(".png", src)[1].tofile(str(temp_image))
                r = RD.read_one(mk, mp, temp_image, debug_dir=debug_dir)
            r["image"] = label
        else:
            r = RD.read_one(mk, mp, src, debug_dir=debug_dir)
        if (CAL2_A != 1.0 or CAL2_B != 0.0) and r.get("value") is not None:
            r["value"] = round(_apply_cal(float(r["value"])), 2)
            r["note"] = ((r.get("note") + "；") if r.get("note") else "") + \
                        "已套真值标定（×%.4f %+.4f）" % (CAL2_A, CAL2_B)
        return r

    center, Rad, ctag = prep["center"], prep["Rad"], prep["ctag"]
    m = _measure(prep, center, Rad)
    if "err" in m:
        return {"ok": False, "error": m["err"], "center": center,
                "radius": round(Rad, 1), "center_tag": ctag}

    if debug_dir:
        _draw(prep, m, center, Rad, "%.2f MPa  [%s]" % (m["value"], m["source"]),
              debug_dir, "memory_frame" if isinstance(src, np.ndarray) else src.stem)
    return _result(m, center, Rad, ctag, label)


def read_one_multi(mk, mp, srcs, debug_dir=None, min_frames=2, lock_angle=True, allow_legacy=True):
    """多帧锁定圆心：N 帧 → 一个读数(MPa)。**表静止时用**。

    原理：读数 = f(圆心, 针角, 刻度跨度)，这三样每帧都被单独检一次，各自都会抖：

      ① 圆心：帧间抖 ±3~5px。子码流半径只有 87~146px，3px ≈ 2.0°，
         换算成读数约 0.12 MPa —— 正好是当前 1σ 抖动的量级。
         对策：取 N 帧圆心的**逐坐标中位数**钉死（中位数天然抗个别帧跑偏）。
      ② 针角：`scan_needle` 判"哪条细暗线最长"，而真针与次强候选（表盘中央的
         黑色商标/文字）只差 1~3 个采样点 → **双稳态**，帧间噪点一变 argmax 就翻。
         对策（lock_angle=True）：表静止 → 真针在每帧里都是暗的、假候选帧帧不同，
         于是把 N 帧的细暗线掩膜**逐格过半表决**再取最长段（见 _runs_multi）。
      ③ 刻度跨度：来自模型关键点，同样取中位数。

    三步都做完，帧间差异只剩"表真的动了"那部分 —— 所以**读数极差本身就是
    "表有没有动"的判据**：极差大 → 不走平均、直接标"需人工复核"。

    srcs：路径列表或 BGR 数组列表（连拍时直接喂内存帧，不必落盘）。
    返回字段与 read_one 兼容，另加 frames / values / spread / center_jitter / agree / best_index。
    """
    srcs = list(srcs)
    label = "<内存连拍>" if srcs and isinstance(srcs[0], np.ndarray) else (str(srcs[0]) if srcs else "")
    preps, errs = [], []
    for source_index, s in enumerate(srcs):
        p = _prepare(mk, mp, s, allow_legacy=allow_legacy)
        if "err" in p:
            errs.append(p["err"])
            continue
        if not p.get("delegate") and "center" not in p:
            errs.append("圆心没定住")
            continue
        p["source_index"] = source_index
        preps.append(p)

    if any(p.get("delegate") for p in preps):
        # 大图/混合尺寸帧各自测量，仍保留多帧中位数、离散度和真实代表帧序号。
        singles = [(p["source_index"], read_one(mk, mp, srcs[p["source_index"]], allow_legacy=allow_legacy))
                   for p in preps]
        singles = [(i, r) for i, r in singles if r.get("ok") and r.get("value") is not None]
        if not singles:
            return {"ok": False, "error": "多帧全部读不出来", "frames": 0}
        values = [r["value"] for _, r in singles]
        median = float(np.median(values))
        best_index, best = min(singles, key=lambda item: abs(item[1]["value"] - median))
        result = dict(best)
        spread = float(max(values) - min(values))
        result.update({"image": label, "value": round(median, 2), "frames": len(singles),
                       "values": values, "spread": round(spread, 3),
                       "angle_locked": False, "best_index": best_index})
        if spread > MULTI_SPREAD_FLAG:
            result["flag"] = "需人工复核（%d帧读数差 %.2f MPa，表可能动了）" % (len(singles), spread)
        result["note"] = ((result.get("note") + "；") if result.get("note") else "") + \
                         "%d帧独立测量，中位读数，极差 %.2f MPa" % (len(singles), spread)
        if debug_dir:
            read_one(mk, mp, srcs[best_index], debug_dir=debug_dir, allow_legacy=allow_legacy)
        return result

    if preps and any(p["gray"].shape != preps[0]["gray"].shape for p in preps):
        return {"ok": False, "error": "连拍帧尺寸不一致，不能共用圆心", "frames": len(preps)}

    # 有效帧不够 → 退回单帧，绝不因为凑不齐就整张失败
    if len(preps) < max(2, int(min_frames)):
        if not preps:
            return {"ok": False, "error": errs[0] if errs else "多帧全部读不出来",
                    "frames": 0}
        p0 = preps[0]
        m0 = _measure(p0, p0["center"], p0["Rad"])
        if "err" in m0:
            return {"ok": False, "error": m0["err"], "frames": 1}
        r = _result(m0, p0["center"], p0["Rad"], p0["ctag"], label)
        r.update({"frames": 1, "values": [round(m0["value"], 2)],
                  "spread": 0.0, "center_jitter": 0.0, "agree": 1,
                  "best_index": p0["source_index"]})
        r["note"] = ((r["note"] + "；") if r["note"] else "") + \
                    "有效帧仅 1 帧，未能锁定圆心（%s）" % (errs[0] if errs else "其余帧无效")
        return r

    # ---------- ① 锁定圆心 / 半径：逐坐标中位数 ----------
    xs = [p["center"][0] for p in preps]
    ys = [p["center"][1] for p in preps]
    Rs = [p["Rad"] for p in preps]
    center = (float(np.median(xs)), float(np.median(ys)))
    Rad = float(np.median(Rs))
    jitter = max(math.hypot(x - center[0], y - center[1]) for x, y in zip(xs, ys))

    # ---------- ② 针角：先验"帧间一致吗"，一致才敢锁 ----------
    #    表静止 → 真针在每帧里都指向同一角度（差别只有噪点级别）；
    #    表动了   → 各帧针角本身就分得开。所以反过来用"帧间自检针角的分歧"
    #              当闸门：分歧小 = 静止，可以放心把证据合起来表决去噪；
    #                      分歧大 = 表动了，绝不合并（合并会把位移抹掉）。
    needle, agree, ang_spread, angle_locked = None, 0, 0.0, False
    evid_shared = 0.0
    if lock_angle and len(preps) > 1:
        masks, step = [], None
        for p in preps:
            thin, dark, st, _bg = _scan_masks(p["gray"], center[0], center[1], Rad)
            masks.append((thin, dark))
            step = st
        a_owns = [int(np.argmax(_runs_from(t, d, step))) for (t, d) in masks]
        ang_spread = _circ_spread(a_owns)
        if ang_spread <= ANG_TOL:
            runs, a_shared = _runs_multi(masks, step)
            run_len = float(runs[a_shared] * step)
            evid_shared = run_len / max((SCAN_REL[1] - SCAN_REL[0]) * Rad, 1e-6)
            agree = sum(1 for a in a_owns if a == a_shared)
            # ★ 2026-10-13 晚：表决角也要过"证据闸门" —— 表决只能"去噪"，不能"造证据"。
            #   实测（用户报"读数 7.9、肉眼 9.35"那轮）：5 帧一致指向 263°，看着 5/5
            #   铁证，其实是表盘中央商标的假细线（39px），真针只有 36px、差 2px；
            #   表决把这个假共识固化了，读数被拉到 7.93（真值 9.35，错 1.4）。
            #   证据强度 evid < MIN_RUN_REL 说明这条"暗线"根本没贯穿扫描区（像商标笔画，
            #   不是从圆心伸到盘边的针）→ 不锁针角，退回"各帧自己判"（会走到模型针尖兜底）。
            if evid_shared >= MIN_RUN_REL:
                needle = (float(a_shared), run_len, evid_shared, "图像法·多帧表决")
                angle_locked = True

    # ---------- ②b 刻度基准：N 帧各检一次，过半成功才统一采信 ----------
    #    和圆心一样，基准线也是"每帧各检一次会抖"的量；表静止时物理上只有一对，
    #    所以取 N 帧成功检出的**圆周中位数**当整组共用的基准，避免帧间基准不一致
    #    把读数撕成两簇。成功帧数不过半 → 整组回退模型点（保守，不赌单帧）。
    scale, scale_frame_note = None, None
    if SCALE_FROM_IMAGE and len(preps) > 1:
        cands = []
        for p in preps:
            kk = p["k"]
            mm0 = ((kk[0][0] + kk[1][0]) / 2, (kk[0][1] + kk[1][1]) / 2)
            mm25 = ((kk[2][0] + kk[3][0]) / 2, (kk[2][1] + kk[3][1]) / 2)
            h0 = ang_of(center[0], center[1], mm0[0], mm0[1])
            h25 = ang_of(center[0], center[1], mm25[0], mm25[1])
            st = find_scale_ticks(p["gray"], center[0], center[1], Rad, h0, h25)
            if st is not None:
                cands.append(st)
        if len(cands) >= max(2, len(preps) // 2 + 1):
            a0m = _ang_median([c[0] for c in cands])
            a25m = _ang_median([c[1] for c in cands])
            sp = (a25m - a0m) % 360
            if SCALE_SPAN_LO <= sp <= SCALE_SPAN_HI:
                scale = (a0m, a25m, "图像法刻度线·%d/%d帧中位(span%.1f°)" % (
                    len(cands), len(preps), sp))

    # ---------- ③ 每帧在共同圆心（+共同针角/基准）下重量 ----------
    ms = []
    for p in preps:
        m = _measure(p, center, Rad, needle=needle, scale=scale)
        if "err" not in m:
            ms.append((p, m))
    if not ms:
        return {"ok": False, "error": "多帧在锁定圆心上都算不出读数",
                "center": (round(center[0], 1), round(center[1], 1)),
                "radius": round(Rad, 1), "center_tag": "多帧锁定", "frames": len(preps)}
    vals = [m["value"] for _, m in ms]
    value = float(np.median(vals))
    spread = float(max(vals) - min(vals))

    # 代表帧：自身圆心离锁定圆心最近的那一帧（最"典型"，用于落盘图 / 摘出模型类字段）
    best_i = min(range(len(ms)),
                 key=lambda i: math.hypot(ms[i][0]["center"][0] - center[0],
                                          ms[i][0]["center"][1] - center[1]))
    p_best, m_best = ms[best_i]

    tag = "多帧锁定×%d" % len(ms)
    r = _result(m_best, center, Rad, tag, label)
    r["value"] = round(value, 2)                     # ★ 用中位读数覆盖单帧读数
    r.update({"frames": len(ms), "values": [round(v, 2) for v in vals],
              "spread": round(spread, 3), "center_jitter": round(jitter, 1),
              "agree": agree, "ang_spread": round(ang_spread, 1),
              "angle_locked": angle_locked, "best_index": p_best["source_index"]})

    # ---------- ④ 复核判据 ----------
    #    ★ 单帧的 flag 会被"个别帧模型关键点跑偏"带偏：实测轮A（真值 13.4）5 帧里
    #      只有 1 帧模型针尖偏 27°，却差点让整组标上"两来源均不可信"。
    #      改成**全组多数帧**都这么判，才认这个标记。
    votes = {}
    for _p, mm in ms:
        if mm.get("flag"):
            votes.setdefault(mm["flag"].split("（")[0], []).append(mm["flag"])
    r["flag"] = None
    if votes:
        top = max(votes.values(), key=len)
        if len(top) >= max(2, len(ms) // 2 + 1):
            r["flag"] = top[0]
    if spread > MULTI_SPREAD_FLAG:
        r["flag"] = "需人工复核（%d帧读数差 %.2f MPa，表可能动了）" % (len(ms), spread)
    if angle_locked:
        tail = "%d帧锁圆心+针角（圆心抖动≤%.1fpx，半径%.0fpx，针角分歧%.0f°，%d/%d帧一致），读数极差 %.2f MPa" \
               % (len(ms), jitter, Rad, ang_spread, agree, len(ms), spread)
    elif ang_spread > ANG_TOL:
        tail = "%d帧只锁圆心（针角分歧%.0f° > %.0f° → 未锁针角），读数极差 %.2f MPa" \
               % (len(ms), ang_spread, ANG_TOL, spread)
    else:
        tail = "%d帧只锁圆心（图像法证据仅 %.0f%% < %.0f%% → 未锁针角，退回模型针尖），读数极差 %.2f MPa" \
               % (len(ms), evid_shared * 100, MIN_RUN_REL * 100, spread)
    tail += "；基准:%s" % str(m_best.get("scale_src", "模型刻度点")).split("(")[0]
    r["note"] = ((r["note"] + "；") if r["note"] else "") + tail

    if debug_dir:
        _draw(p_best, m_best, center, Rad,
              "%.2f MPa  [%s]" % (value, tag), debug_dir,
              Path(str(srcs[0])).stem if not isinstance(srcs[0], np.ndarray)
              else "multi")
    return r
