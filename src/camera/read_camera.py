#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
read_camera.py —— 通过 ONVIF 读取摄像头信息 + 抓取任意一帧图片

【核心概念，先理解再动手】
  ONVIF 不传视频！它只是"信令"协议：
      WS-Discovery 发现设备 → SOAP 鉴权 → GetDeviceInformation 拿设备指纹
                                        → GetProfiles / GetStreamUri 拿到 rtsp:// 地址
  真正的视频流由 RTSP 协议传输，用 ffmpeg / OpenCV / VLC 去拉。

  所以本脚本是两段式：
      第 1 段（ONVIF）：拿设备信息 + 问出 RTSP 地址
      第 2 段（RTSP） ：用 ffmpeg 拉流，只取 1 帧存成 jpg

【本机实测的坑，已经写进代码里了】
  * 这台 TP-Link TL-IPC342-4 的 ONVIF device_service 在 **2020** 端口，不是 80。
    别的牌子（海康/大华）才是 80。端口不用猜：WS-Discovery 应答里的 XAddrs 会直接告诉你。
  * device_service **会校验账号**：未鉴权调用返回 400 + "Authority failure"。
    （2026-09-30 时它还不校验 —— 说明鉴权策略会随设备状态变化，别把一次探测当永久结论。）
  * ONVIF 的 Media 服务**是好的**，登录后能正常返回 profile 和 GetStreamUri。
    不过代码仍保留 fallback：问不到就按厂商约定拼固定 RTSP 地址。
  * **RTSP 没有独立账号**（2026-10-02 从设备网页固件的配置模型里确认）：
    配置文件 `cet` 的 `server.rtsp` 段只有 `port` 一个字段，没有用户名/密码项；
    `multicast`（组播）也只有 开关/地址/端口。
    网页里同样没有 RTSP 账号页 —— `网络设置→高级配置` 下是 FTP/MQTT/邮箱/IP权限/RTMP，
    RTSP 只以"端口号"出现在 `网络设置→端口`。
    → 结论：RTSP 复用的是**设备自己的用户账号**。
  * **用户名就是 `admin`，没有第二个账号**（2026-10-02 用户打开网页
    `系统设置 → 用户管理` 实测：列表里只有一行 `admin` / 管理员组）。
    配置模型里虽然定义了 root/guest/customer 等组，但这台没有其它用户。
    → 所以 RTSP 凭据 = `admin` + 能登进网页的那个设备密码，别再去猜别的用户名。
  * **ONVIF 的 GetStreamUri 返回的地址不带凭据**（`rtsp://IP:554/stream1`）——
    这是协议规范如此，客户端必须自己把 `user:pass@` 补进去。
    **2026-10-02 实测踩坑：直接把这条裸地址交给 ffmpeg，会以匿名身份去请求 → 秒回 401，
    看起来像"密码错"，其实是"根本没带密码"。** 已由 `inject_credentials()` 修好。
  * **密码进 URL 必须做百分号编码**：ONVIF 把密码放在 HTTP 头里传，URL 里没有它；
    RTSP 则必须把密码塞进 URL。密码里若有 `@ : / ? # & %`、空格或中文，不编码就会被
    ffmpeg 解析歪 —— 症状同样像"密码明明对，RTSP 却 401"。已自动编码。
  * **★★ 这台设备同时只服务【一路】RTSP 客户端（2026-10-02 实测）**：
    12:08 抓帧成功后，12:13 用同一个正确密码再跑就被 401。查本机连接发现
    **`iexplore.exe` 有一条 ESTABLISHED 连接占着 192.168.1.60:554** ——
    是网页「预览」页的插件（application/x-tp-camera-h264）在拉流。
    而 ONVIF 侧同时用同一套密码校验是 **OK** 的 → 说明 401 不是密码问题，
    而是**流被别人占着**。这台设备对"第二路客户端"回的是 401，
    **极易被误读成"密码错"**，比回 503 更难排查。
    → 所以本脚本第 3 步前会先扫本机连接、列出占用进程（`find_stream_holders`）。
    → **跑脚本前请关掉网页预览页 / ODM 的 Live video / VLC。**
  * **★★★ 更深一层：本机无占用时，正确密码也会被秒拒（2026-10-02 12:18 实测）**：
    12:08 抓帧成功 → 之后所有尝试全部 401，且 12:18 那次**本机已无任何占用**、
    ONVIF 侧同一密码校验 OK，RTSP 仍是 **0.0 秒秒拒**。
    这台设备**只有一路稳定的 RTSP 会话，且用完释放得很不干脆** ——
    上一位客户端的会话没清干净时，后面**谁来都被回 401（它不说"忙"，只说"没授权"）**。
    → **所以：① 别把脚本反复跑**（每跑一次都是一次"开会话+关会话"），
      **要多张图就用 `--frames N` 在同一个会话里抓完**；
      **② 一旦进入这种"谁都 401"的状态，只能给设备断电 30 秒重启**。

用法：
    python read_camera.py --password 你的密码
    python read_camera.py --password 你的密码 --sub          # 取子码流（更快、更省带宽）
    python read_camera.py --password 你的密码 --sub --frames 6 --interval 10
        # ★ 强烈推荐要"多张"时用这种：一个会话里每 10 秒抓一帧、共 6 帧
        #   （别把脚本反复跑 —— 每次跑都要新建一次 RTSP 会话，这台设备会被耗到回 401）
    python read_camera.py --ip 192.168.1.60 --password xxx --out my.jpg

【定时 / 定间隔抓图 —— 想让它自己按节奏拍】
    ★ 最省事：改本文件顶部的【PLAN 配置区】，然后**直接运行脚本**就行：
    python read_camera.py                              # 自动按 PLAN 抓图（F5 / 双击 也一样）
    python read_camera.py --dry-run                    # 先按 PLAN 预演（不联网、不问密码、不写文件）
    ★ 脚本【默认】就按 PLAN 跑，**不用加 --plan**，也不用挑 VSCode 配置。
    想临时改一次而不动代码，就直接写命令行参数（命令行优先于 PLAN）：
    python read_camera.py --every 30 --count 20        # 每 30 秒一张，抓满 20 张就停
    python read_camera.py --every 5m                   # 每 5 分钟一张，一直跑到你按 Ctrl+C
    python read_camera.py --sub --every 15m --between 09:00-17:00 --count 32
                                                       # 只在 9:00~17:00 之间，每 15 分钟一张，共 32 张
    python read_camera.py --at 08:00,12:00,18:00       # 每天 8 点 / 12 点 / 18 点各一张，一直跑
    python read_camera.py --at 08:00 --count 1         # 抓满 1 张就退出
    python read_camera.py --at "2026-10-03 09:00:00"   # 只在那个绝对时刻抓一次
    python read_camera.py --every 1h --dry-run         # 只算时间、打印计划（不联网、不问密码，断网也能跑）

  间隔怎么写：30 = 30 秒、90s、5m（分）、2h（时）、1d（天），也认 分/时/天。
  时刻怎么写：08:00、8:00:30（每天都重复）；"2026-10-03 09:00:00" 或 "10-03 09:00"（绝对时刻，只抓一次）。
  图片按 `<前缀>_年月日_时分秒.jpg` 命名，**绝不覆盖旧图**；输出目录用 --out-dir 指定。

  ★ 为什么定时模式必须走【长连接】：
    这台设备只有一个 RTSP 会话槽，而且**用完释放得很不干脆**（见下面「本机实测的坑」第 3 条）。
    "定时抓图"如果理解成"到点就新建一次连接去抓"，跑不了几轮就会被回 401。
    本脚本的做法：开**一个常驻 ffmpeg，只连一次**，让它按 --stream-fps 持续吐 JPEG 帧；
    Python 只负责在到点时把"最近这一帧"落盘。整个进程生命周期里 RTSP 连接恒等于 1 条。
    代价：跑着的时候一直在拉流，**建议配 --sub 走子码流**（640×360，几百 kbps）。
    若你更在意带宽、并愿意承担重连风险，可加 --lazy 改成"抓拍时才临时连一次"。

【为什么在终端能跑、在 VSCode 里跑不了】
  这不是摄像头的问题，是【解释器选错】。本脚本依赖 onvif-zeep + imageio-ffmpeg，
  它们只装在 C:\Users\28902\.workbuddy\binaries\python\envs\default 这个 venv 里；
  系统 Python（G:\develop\Python\python.exe）没装。
  VSCode 的 ▶ / F5 默认可能挑到系统 Python → 报 ModuleNotFoundError 或"找不到 ffmpeg"。
  → 已经随脚本放了 .vscode/settings.json + launch.json，并按 F5 可选三种跑法；
    也可以在 VSCode 里 Ctrl+Shift+P → Python: Select Interpreter → 选：
    C:\Users\28902\.workbuddy\binaries\python\envs\default\Scripts\python.exe
  脚本开头的 check_env() 会在每次运行时把"当前解释器 + 依赖是否齐全"打出来，方便自证。
"""

import argparse
import atexit
import datetime
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import quote

# Windows 控制台中文输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ==============================================================================
#  ★★★  想改抓图时间？只改下面这一个方块，其余一个字都不用动。  ★★★
#
#  改完保存（Ctrl+S），然后【直接运行本脚本】即可 —— F5、双击、终端
#  python read_camera.py，都一样。**不用挑 VSCode 配置，也不用加 --plan**：
#  脚本默认就按下面这个方块跑。
#  想先看看计划对不对（不联网、不问密码），运行时加一个 --dry-run 就行。
# ==============================================================================
PLAN = {
    # ⓪ 总开关：True = 按下面的设置【定时抓图】；False = 只抓一张（忽略下面的节奏）
    "enable":  True,

    # ① 每隔多久抓一张  ——  "30"=30秒  "90s"  "5m"=5分钟  "2h"=2小时  "1d"=1天
    "every":   "10",

    # ② 或者：固定时间点抓（每天重复）—— "08:00,12:00,18:00"；某个绝对时刻写 "2026-10-03 09:00"
    #    也认省略年份的 "10-03 09:00"（今年已过就按明年算）。
    #    ★ every 和 at 只能填一个，另一个保持空字符串 ""。
    "at":     "",
    # ③ 只在某个时段内抓（配合 every 用）："09:00-17:00"；不需要就留空 ""
    "between": "",

    # ④ 抓满多少张就停：0 = 一直抓（按 Ctrl+C 停）
    "count":   5,


    # ⑤ 子码流（小、快、稳，推荐）True；想要高清大图改 False（每张约 1MB）
    "sub":     True,

    # ⑥ 图片存到哪个文件夹（相对本脚本所在目录）。留空 "" = 就存脚本旁边
    "out_dir": "data/captures/camera",
}


# ---------------------------------------------------------------- ONVIF 段
def read_onvif_info(ip, port, user, password, sub=False):
    """用 ONVIF 读设备信息，并尽量问出 RTSP 地址。

    返回 (设备信息 dict, rtsp uri 或 None)
    """
    from onvif import ONVIFCamera

    # 构造 ONVIFCamera 本身不联网，只是准备好各服务的 wsdl
    cam = ONVIFCamera(ip, port, user, password)

    info = {}
    try:
        d = cam.devicemgmt.GetDeviceInformation()   # 顺便验证鉴权是否真的生效
        info = {
            "厂商":   d.Manufacturer,
            "型号":   d.Model,
            "固件":   d.FirmwareVersion,
            "序列号": d.SerialNumber,
            "硬件":   d.HardwareId,
        }
    except Exception as e:
        print("  [!] GetDeviceInformation 失败: %s" % e)
        if "Authority failure" in str(e) or "NotAuthorized" in str(e):
            print("      → 这是「鉴权失败」：用户名或密码不对。")
            print("        （该设备现在会校验账号，所以这一步就是免费的密码验证器）")
            print("        换用户名/密码重试；真实用户名可在网页「系统设置 → 用户管理」查看。")

    # 设备时钟：ONVIF 用 WS-Security 摘要鉴权，时钟偏差过大会直接鉴权失败
    try:
        t = cam.devicemgmt.GetSystemDateAndTime()
        lt = t.LocalDateTime
        info["设备时间"] = "%04d-%02d-%02d %02d:%02d:%02d" % (
            lt.Date.Year, lt.Date.Month, lt.Date.Day,
            lt.Time.Hour, lt.Time.Minute, lt.Time.Second)
    except Exception:
        pass

    uri = None
    try:
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        print("  [OK] Media 服务可用，profile 数 = %d" % len(profiles))

        def _res(p):
            v = getattr(p, "VideoEncoderConfiguration", None)
            r = getattr(v, "Resolution", None) if v is not None else None
            if r is None:
                return None
            return int(getattr(r, "Width", 0)), int(getattr(r, "Height", 0))

        def _area(p):
            wh = _res(p)
            return 0 if wh is None else wh[0] * wh[1]

        # 打印所有 profile，让用户一眼看到主/子码流
        for p in profiles:
            wh = _res(p)
            v = getattr(p, "VideoEncoderConfiguration", None)
            print("       token=%-16s name=%-16s %s  %s" % (
                p.token, (getattr(p, "Name", "") or "")[:16],
                ("%sx%s" % wh) if wh else "?x?",
                getattr(v, "Encoding", "") if v is not None else ""))

        # 选 profile：--sub 取分辨率最小的（子码流省带宽），否则取最大的（主码流清晰）
        ranked = sorted(profiles, key=_area)
        target = ranked[0] if sub else ranked[-1]

        req = media.create_type("GetStreamUri")
        req.ProfileToken = target.token
        req.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
        uri = media.GetStreamUri(req).Uri
        print("  [OK] GetStreamUri -> %s   (%s)"
              % (uri, "子码流" if sub else "主码流"))
    except Exception as e:
        print("  [!] Media 服务不可用（%s），改用固定地址拼接" % type(e).__name__)

    return info, uri


def inject_credentials(uri, user, password):
    """把账号密码注入 ONVIF 返回的裸 RTSP 地址。

    ONVIF 的 GetStreamUri 按规范**不返回凭据**，只给 `rtsp://IP:554/stream1`。
    直接交给 ffmpeg 会以匿名身份请求 → 秒回 401（看起来像密码错，其实根本没带密码）。
    这里在 `rtsp://` 之后插入 `user:pass@`，并对特殊字符做百分号编码。
    若地址里已经带了凭据，则替换掉旧的。
    """
    import re
    m = re.match(r"^(rtsps?://)(.*)$", uri, re.I)
    if not m:
        return uri
    scheme, rest = m.group(1), m.group(2)
    head = rest.split("/", 1)[0]
    if "@" in head:                      # 已有旧凭据，丢掉
        rest = rest.split("@", 1)[1]
    return "%s%s:%s@%s" % (scheme, quote(user, safe=""), quote(password, safe=""), rest)


def guess_rtsp_url(ip, user, password, sub=False):
    """当 ONVIF 问不出流地址时，按厂商约定拼 RTSP 地址。

    本机实测（TL-IPC342-4）：rtsp://user:pass@IP:554/stream1  （主码流）
                              rtsp://user:pass@IP:554/stream2  （子码流）
    其他厂商见下方注释，换成对应格式即可。
    """
    stream = "stream2" if sub else "stream1"
    # 凭据必须做百分号编码：密码里的 @ : / ? # & % 空格 中文 等
    # 不编码会让 ffmpeg 解析错地址，症状是"密码对却 401"或 "Port missing in uri"。
    return "rtsp://%s:%s@%s:554/%s" % (
        quote(user, safe=""), quote(password, safe=""), ip, stream)
    # 海康: rtsp://u:p@IP:554/Streaming/Channels/101   (102 = 通道1子码流)
    # 大华: rtsp://u:p@IP:554/cam/realmonitor?channel=1&subtype=0


def mask_uri(uri, password):
    """打印用：把地址里的密码换成 ***，避免截图 / 录屏泄露。

    密码可能在地址里以两种形态出现：原文、或百分号编码后的样子，都要盖掉。
    """
    if not uri or not password:
        return uri
    return uri.replace(password, "***").replace(quote(password, safe=""), "***")


# ---------------------------------------------------------------- RTSP 段
# 装了依赖的那个解释器（本机唯一可用的那个）。系统 Python 是 G:\develop\Python\python.exe，
# 它没装 onvif-zeep / imageio-ffmpeg —— VSCode 很容易自动挑到它，然后脚本就跑不起来。
VENV_PYTHON = r"C:\Users\28902\.workbuddy\binaries\python\envs\default\Scripts\python.exe"


def check_env():
    """跑之前先体检：是哪个 Python 在跑、依赖齐不齐。

    ★ 为什么要这一步：脚本可以在任何终端跑，但【必须用装了依赖的解释器】跑。
      VSCode 的 ▶ / F5 默认可能挑到系统 Python，此时会报 ModuleNotFoundError
      或"找不到 ffmpeg"，看着像脚本坏了，其实只是解释器选错。
      这里直接把答案打出来，省得去猜。
    """
    print("[i] 当前解释器：%s" % sys.executable)
    if sys.version_info < (3, 8):
        print("[!] Python 版本偏低（%d.%d），建议 3.8+" % sys.version_info[:2])

    missing = []
    for mod, role in (("onvif", "ONVIF 信令，第 1 步读设备信息"),
                      ("imageio_ffmpeg", "自带 ffmpeg，第 3 步拉流抓帧")):
        try:
            __import__(mod)
        except Exception:
            missing.append((mod, role))

    if missing:
        print("[!] 缺依赖，这次大概率跑不起来：")
        for mod, role in missing:
            print("      · %-16s —— %s" % (mod, role))
        print("    原因：你现在用的解释器不是装了依赖的那个。")
        same = os.path.normcase(os.path.abspath(sys.executable)) == \
            os.path.normcase(os.path.abspath(VENV_PYTHON))
        if os.path.exists(VENV_PYTHON) and not same:
            print("    两条修法（任选）：")
            print("      A) 换解释器 —— 用这个跑：")
            print("         %s" % VENV_PYTHON)
            print("         （VSCode：Ctrl+Shift+P → Python: Select Interpreter → 选上面这个路径）")
            print("      B) 给当前解释器补装依赖：")
            print('         "%s" -m pip install onvif-zeep imageio-ffmpeg' % sys.executable)
        else:
            print('    修法：python -m pip install onvif-zeep imageio-ffmpeg')
        print()
        if any(m == "imageio_ffmpeg" for m, _ in missing):
            print("[!] 且没找到可用的 ffmpeg —— 第 3 步取流会失败。\n")
    else:
        print("[i] 依赖齐全（onvif-zeep + imageio-ffmpeg）")
    print()


def get_ffmpeg():
    """ffmpeg 不在 PATH，用 imageio-ffmpeg 自带的静态版本。"""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return "ffmpeg"          # 兜底：指望系统 PATH 里有（一般没有）


def _run_text(cmd, timeout=15):
    """跑系统命令并稳妥地拿到文本输出。

    ★ 坑：中文 Windows 下 netstat / tasklist 输出是 **GBK(CP936)**，
      用 subprocess(text=True) 会按 UTF-8 解码报 UnicodeDecodeError，
      stdout 直接变成 None（而且异常发生在读取线程里，不会抛给我们）。
      → 所以这里拿 bytes 自己解码。
    """
    import locale
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return ""
    enc = locale.getpreferredencoding(False) or "utf-8"
    for e in (enc, "gbk", "utf-8"):
        try:
            return (p.stdout or b"").decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
    return (p.stdout or b"").decode("utf-8", "replace")


def find_stream_holders(ip, port=554):
    """找出本机上已经占着 <ip>:<port> 视频流的进程。

    ★ 2026-10-02 实测发现：这台 TL-IPC342-4 **同一时刻只服务一路 RTSP 客户端**。
      当网页预览插件（IE/Edge IE 模式的 application/x-tp-camera-h264）或 ODM 的
      Live video 已经在看时，我们再拉流会被回一个 **401**——而这个 401 会严重误导人，
      让人以为"密码错了"。实际是"流被别人占着"。
    返回 [进程名, ...]（去重）。
    """
    import csv
    import io
    import re

    if os.name != "nt":
        return []

    out = _run_text(["netstat", "-ano"])
    target = "%s:%d" % (ip, port)
    pids = set()
    for line in out.splitlines():
        if target in line and "ESTABLISHED" in line.upper():
            m = re.search(r"\s(\d+)\s*$", line)
            if m:
                pids.add(m.group(1))
    if not pids:
        return []

    names = []
    for row in csv.reader(io.StringIO(_run_text(["tasklist", "/FO", "CSV", "/NH"]))):
        if len(row) >= 2 and row[1].strip() in pids:
            n = row[0].strip()
            if n and n not in names:
                names.append(n)
    return names or ["PID " + ",".join(sorted(pids))]


def grab_frames(rtsp_url, out_path, frames=1, interval=5, timeout_s=20):
    """用 ffmpeg 从 RTSP 拉流抓帧存图。

    ★ 关键设计（2026-10-02 实测沉淀）：这台设备**只稳定服务一路 RTSP 会话**，
      而且会话释放得很不干脆 —— 每抓一帧就"开一次会话、关一次会话"的用法，
      很快就会把设备耗到"谁来都回 401"的状态（要断电重启才恢复）。
      → 所以**要多张图时，请在【同一个会话里】连续抓**（frames>1），
        而不是把脚本反复跑很多次。

    -rtsp_transport tcp  : 必须加！UDP 在这类机器上常花屏/断流
    -frames:v N          : 只取 N 帧
    -vf fps=1/interval   : 每 interval 秒留一帧（单会话内的间隔抓拍）
    -y                   : 覆盖已存在文件
    """
    ff = get_ffmpeg()
    cmd = [ff, "-hide_banner", "-loglevel", "warning",
           "-rtsp_transport", "tcp",                 # 关键参数
           "-timeout", str(timeout_s * 1000000)]     # 微秒，socket 读超时

    if frames > 1:
        # 输出名变成 a_001.jpg / a_002.jpg ...，交给 ffmpeg 的序号模板
        root, ext = os.path.splitext(out_path)
        if ext.lower() not in (".jpg", ".jpeg", ".png"):
            ext = ".jpg"
        pattern = "%s_%%03d%s" % (root, ext)
        cmd += ["-i", rtsp_url,
                "-vf", "fps=1/%d" % max(interval, 1),
                "-frames:v", str(frames),
                "-q:v", "2", "-y", pattern]
        wait_s = interval * frames + 25
        outputs = [pattern % i for i in range(1, frames + 1)]
    else:
        cmd += ["-i", rtsp_url, "-frames:v", "1",
                "-q:v", "2", "-y", out_path]
        wait_s = timeout_s + 15
        outputs = [out_path]

    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=wait_s,
                           errors="replace")
    except FileNotFoundError:
        return False, [], 0, time.time() - t0, (
            "找不到 ffmpeg 可执行文件（%s）。\n"
            "  → 说明当前解释器里没有 imageio-ffmpeg。\n"
            "  → 改用这个解释器跑：%s\n"
            "    或给它装：\"%s\" -m pip install imageio-ffmpeg"
            % (ff, VENV_PYTHON, sys.executable))
    except subprocess.TimeoutExpired:
        return False, [], 0, time.time() - t0, (
            "ffmpeg 超时（%d 秒内没拿到画面）。可能是设备在忙 / 被占用 / 网络不通。"
            % wait_s)
    err = (p.stderr or "").strip()

    made = [f for f in outputs if os.path.exists(f) and os.path.getsize(f) > 0]
    total = sum(os.path.getsize(f) for f in made)
    if made:
        return True, made, total, time.time() - t0, err
    return False, [], 0, time.time() - t0, err


def grab_one_frame(rtsp_url, out_path, timeout_s=20):
    """单帧的兼容包装（保留旧名字，内部走 grab_frames）。"""
    ok, made, size, cost, err = grab_frames(rtsp_url, out_path, frames=1,
                                            timeout_s=timeout_s)
    return ok, (made[0] if made else None), size, cost, err


# ================================================================ 定时调度段
# 2026-10-02 新增：让脚本能"自己按节奏拍"。
#     --every 30            每 30 秒一张
#     --at 08:00,12:00      每天这两个点各一张
#     --between 09:00-17:00 只在时段内拍
#
# ★ 这一整段的设计都围绕一条铁律：**这台设备只有一个 RTSP 会话槽、用完释放不干脆**
#   （见文件头「本机实测的坑」第 3 条）。所以定时抓图绝不能"到点就新建连接去抓"，
#   只能是【一次连接、长跑】：ffmpeg 常驻拉流 -> 持续吐 JPEG -> Python 到点落盘最新帧。

_STREAMS = []          # 所有活着的取流进程，用于异常退出时兜底清理（别让它占住会话槽）


def _cleanup_streams():
    """进程退出前把所有取流子进程收干净。

    ★ 为什么必须做：ffmpeg 要是残留下来，它会一直占着那唯一一路 RTSP 会话，
      下次再跑脚本就是 401 —— 正是之前 iexplore 占用流那个坑的翻版。
    """
    for s in list(_STREAMS):
        try:
            s.stop()
        except Exception:
            pass


atexit.register(_cleanup_streams)


_DUR_UNITS = {
    "s": 1, "秒": 1,
    "m": 60, "分": 60, "分钟": 60,
    "h": 3600, "时": 3600, "小时": 3600,
    "d": 86400, "天": 86400,
}


def human_duration(sec):
    """把秒数说成人话：3600 -> '1 小时'，41103 -> '11 小时 25 分'。

    以前的版本对"非整单位"的秒数会原样吐成 '41103.3 秒'，倒计时看着很丑，故改写。
    """
    sec = float(sec)
    if sec < 0:
        sec = 0.0
    n = int(round(sec))
    if n < 60:
        return "%s 秒" % (("%.1f" % sec).rstrip("0").rstrip("."))
    if n < 3600:
        m, s = divmod(n, 60)
        return ("%d 分 %d 秒" % (m, s)) if s else ("%d 分钟" % m)
    if n < 86400:
        h, rem = divmod(n, 3600)
        m = rem // 60
        return ("%d 小时 %d 分" % (h, m)) if m else ("%d 小时" % h)
    d, rem = divmod(n, 86400)
    h = rem // 3600
    return ("%d 天 %d 小时" % (d, h)) if h else ("%d 天" % d)


def parse_duration(text):
    """'30' / '30s' / '5m' / '2h' / '1d' / '90秒' -> 秒数(float)。"""
    s = str(text).strip().lower().replace(" ", "")
    if not s:
        raise ValueError("时间间隔是空的")
    m = re.match(r"^(\d+(?:\.\d+)?)([a-z\u4e00-\u9fff]*)$", s)
    if not m:
        raise ValueError("看不懂的时间间隔：%r（示例：30 = 30 秒 / 5m = 5 分钟 / 2h = 2 小时）"
                         % text)
    value = float(m.group(1))
    unit = m.group(2) or "s"
    if unit not in _DUR_UNITS:
        raise ValueError("不认识的时间单位：%r（可用 s/秒、m/分、h/时、d/天）" % unit)
    sec = value * _DUR_UNITS[unit]
    if sec <= 0:
        raise ValueError("时间间隔必须大于 0")
    return sec


def parse_at(text):
    """解析一个"时刻"。返回 (datetime, 是否绝对时刻)。

      '08:00' / '8:00:30'                  -> 每天的钟点（今天已过就顺延到明天）
      '2026-10-03 09:00:00' / '...T09:00'  -> 绝对时刻，只抓这一次
    """
    s = str(text).strip().replace("T", " ").replace("t", " ").replace("/", "-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$", s)
    if m:
        y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
        se = int(m.group(6) or 0)
        try:
            return datetime.datetime(y, mo, d, h, mi, se), True
        except ValueError as e:
            raise ValueError("不是有效日期：%r（%s）" % (text, e))
    # '10-03 09:00' / '10-03 09:00:30' -> 月-日 时:分（省略年份；今年已过就按明年算）
    m = re.match(r"^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$", s)
    if m:
        mo, d, h, mi = (int(m.group(i)) for i in range(1, 5))
        se = int(m.group(5) or 0)
        now = datetime.datetime.now()
        try:
            cand = datetime.datetime(now.year, mo, d, h, mi, se)
        except ValueError as e:
            raise ValueError("不是有效日期：%r（%s）" % (text, e))
        if cand <= now:
            try:
                cand = cand.replace(year=now.year + 1)
            except ValueError:            # 2/29 撞上非闰年
                raise ValueError("不是有效日期：%r（%d 年没有 2 月 29 日）"
                                 % (text, now.year + 1))
        return cand, True

    m = re.match(r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$", s)
    if m:
        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if not (0 <= h <= 23 and 0 <= mi <= 59 and 0 <= se <= 59):
            raise ValueError("时刻超出范围：%r" % text)
        return datetime.datetime.combine(
            datetime.date.today(), datetime.time(h, mi, se)), False
    raise ValueError("看不懂的时刻：%r（示例：08:00 / 8:00:30 / \"2026-10-03 09:00:00\"）"
                     % text)


def parse_between(text):
    """'09:00-17:00' -> (time(9,0), time(17,0))。不支持跨零点。"""
    parts = [p for p in re.split(r"\s*(?:-|~|至|到)\s*", str(text).strip()) if p]
    if len(parts) != 2:
        raise ValueError("时段要写成 09:00-17:00 这种两段式的，收到：%r" % text)
    out = []
    for p in parts:
        m = re.match(r"^(\d{1,2}):(\d{1,2})$", p)
        if not m:
            raise ValueError("时段里的时刻要写成 HH:MM，收到：%r" % p)
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            raise ValueError("时段里的时刻超出范围：%r" % p)
        out.append(datetime.time(h, mi))
    if out[1] <= out[0]:
        raise ValueError("时段的结束要晚于开始（跨零点不支持），收到：%r" % text)
    return out[0], out[1]


class Schedule:
    """算出"下一次该抓拍的时刻"。

    ★ 只算时间，不抓图。间隔模式以【传入的 now】为基准往后推，所以主循环用上一轮的
      nxt（而不是"当前时间"）当基准，节拍才不会越跑越漂。
    """

    def __init__(self, every=None, at_list=None, between=None):
        self.every = every
        self.at = list(at_list or [])          # [(datetime, is_absolute), ...]
        self.between = between

    def _in_window(self, tt):
        if not self.between:
            return True
        return self.between[0] <= tt <= self.between[1]

    def next_after(self, now):
        """返回 now 之后第一个该抓的时刻；没有则返回 None。"""
        if self.every:
            nxt = now + datetime.timedelta(seconds=self.every)
            if self.between:
                # 落在时段外就跳到下一个时段起点（最多往后找 8 天，防死循环）
                for _ in range(8):
                    tt = nxt.time()
                    if self._in_window(tt):
                        break
                    if tt < self.between[0]:
                        nxt = datetime.datetime.combine(nxt.date(), self.between[0])
                    else:
                        nxt = datetime.datetime.combine(
                            nxt.date() + datetime.timedelta(days=1), self.between[0])
            return nxt

        best = None
        # 1) 绝对时刻（只抓一次的那种）
        for dt, is_abs in self.at:
            if is_abs and dt > now and (best is None or dt < best):
                best = dt
        # 2) 每天的钟点，最多往后找 8 天（每天都凑不出可用时刻就认输）
        daily = sorted({dt.time() for dt, is_abs in self.at if not is_abs})
        if daily:
            for i in range(9):
                day = now.date() + datetime.timedelta(days=i)
                for tt in daily:
                    if not self._in_window(tt):
                        continue
                    cand = datetime.datetime.combine(day, tt)
                    if cand > now and (best is None or cand < best):
                        best = cand
                        break
                if best is not None:
                    break
        return best

    def describe(self):
        bits = []
        if self.every:
            bits.append("每隔 %s" % human_duration(self.every))
        if self.at:
            dails = [dt.strftime("%H:%M:%S") for dt, a in self.at if not a]
            abss = [dt.strftime("%Y-%m-%d %H:%M:%S") for dt, a in self.at if a]
            if dails:
                bits.append("每天 %s" % "、".join(dails))
            if abss:
                bits.append("绝对时刻 %s" % "、".join(abss))
        if self.between:
            bits.append("仅在 %s~%s 之间" % (
                self.between[0].strftime("%H:%M"), self.between[1].strftime("%H:%M")))
        return "；".join(bits) if bits else "（没有设置）"


class RtspFrameStream:
    """常驻 RTSP 取流：**只建立一次会话**，持续吐 JPEG 帧。

    ★ 这是"定时抓图"能在本机跑起来的关键。设备只服务一路 RTSP 会话，每抓一张就
      新建/关闭一次连接会把会话槽占死（之后谁来都回 401）。所以这里让 **一个 ffmpeg
      常驻**（`-f image2pipe -c:v mjpeg`），后台线程把这串 JPEG 切出来存成"最新帧"，
      抓拍时直接取用 —— 整个生命周期里 RTSP 连接始终只有一条。

    帧怎么切：JPEG 以 FFD8 开头、FFD9 结尾。JPEG 熵编码段里出现的 0xFF 都会被转义
    成 FF00（restart marker 是 FFD0~FFD7，不冲突），所以直接扫 FFD9 找帧尾是安全的，
    不需要完整解析 JPEG 结构。
    """

    def __init__(self, rtsp_url, stream_fps=2, quality=3):
        self.rtsp_url = rtsp_url
        self.stream_fps = max(1, int(stream_fps))
        self.quality = int(quality)
        self.proc = None
        self._buf = bytearray()
        self._frame = None            # 最近一帧 JPEG 字节
        self._frame_at = 0.0          # 收到它的本地时刻
        self._frame_no = 0            # 已收到的帧数（用来判断"有没有新帧"）
        self._alive = False
        self._err = []
        self._cond = threading.Condition()
        self._t_out = None
        self._t_err = None
        self.started_at = 0.0
        self._history = None
        self._history_bytes = 0

    def enable_history(self, seconds=120, max_bytes=64*1024*1024):
        from collections import deque
        self._history = deque()
        self._history_seconds = seconds
        self._history_max_bytes = max_bytes
        return self

    def historical_frame(self, target, tolerance=10):
        with self._cond:
            if not self._history:
                return None
            stamp, jpg = min(self._history, key=lambda item: abs(item[0]-target))
            return (jpg, stamp) if abs(stamp-target) <= tolerance else None

    def history_status(self):
        with self._cond:
            return {'frameCount':len(self._history or []),'bytes':self._history_bytes,
                    'oldestFrameAt':self._history[0][0] if self._history else None,
                    'newestFrameAt':self._history[-1][0] if self._history else None}

    # ---------------- 生命周期 ----------------
    def start(self):
        ff = get_ffmpeg()
        cmd = [ff, "-hide_banner", "-loglevel", "warning",
               "-rtsp_transport", "tcp",                  # 必须加，UDP 会花屏/断流
               "-i", self.rtsp_url,
               "-vf", "fps=%d" % self.stream_fps,          # 每秒吐几帧，决定抓拍时刻精度
               "-q:v", str(self.quality),
               "-f", "image2pipe", "-c:v", "mjpeg", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, bufsize=0)
        self.started_at = time.time()
        self._alive = True
        self._t_out = threading.Thread(target=self._loop_out, daemon=True)
        self._t_out.start()
        self._t_err = threading.Thread(target=self._loop_err, daemon=True)
        self._t_err.start()
        return self

    def stop(self):
        """关掉 ffmpeg。幂等，可以重复调用。"""
        self._alive = False
        p = self.proc
        self.proc = None
        if p is None:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
                    try:
                        p.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
        except Exception:
            pass
        for f in (p.stdout, p.stderr):
            try:
                if f:
                    f.close()
            except Exception:
                pass

    @property
    def alive(self):
        return self._alive

    def tail(self, n=10):
        """最近 n 行 ffmpeg 输出（排错用）。"""
        return list(self._err[-max(1, int(n)):])

    # ---------------- 后台读 ----------------
    def _loop_err(self):
        """必须有人读 stderr，否则缓冲区满了会把 ffmpeg 卡死。"""
        try:
            for raw in iter(self.proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._err.append(line)
                    del self._err[:-40]
        except Exception:
            pass

    def _loop_out(self):
        try:
            while True:
                chunk = self.proc.stdout.read(8192)
                if not chunk:
                    break
                self._feed(chunk)
        except Exception as e:
            self._err.append("读取取流管道出错: %s" % e)
        finally:
            with self._cond:
                self._alive = False
                self._cond.notify_all()

    def _feed(self, chunk):
        """把字节流里完整的 JPEG 切出来发布。"""
        buf = self._buf
        buf += chunk
        while True:
            s = buf.find(b"\xff\xd8")
            if s < 0:
                break
            e = buf.find(b"\xff\xd9", s + 2)
            if e < 0:
                if s:
                    del buf[:s]              # 丢掉帧头之前的垃圾
                break
            self._publish(bytes(buf[s:e + 2]))
            del buf[:e + 2]
        if len(buf) > 4 * 1024 * 1024:        # 兜底：万一这根本不是 JPEG 流
            del buf[:-4]

    def _publish(self, jpg):
        with self._cond:
            self._frame = jpg
            self._frame_at = time.time()
            self._frame_no += 1
            if self._history is not None:
                self._history.append((self._frame_at, jpg))
                self._history_bytes += len(jpg)
                while self._history and (self._frame_at-self._history[0][0] > self._history_seconds or self._history_bytes > self._history_max_bytes):
                    self._history_bytes -= len(self._history.popleft()[1])
            self._cond.notify_all()

    # ---------------- 取帧 ----------------
    def grab(self, timeout=15.0):
        """取一帧"新鲜"的画面。返回 (jpeg字节, 收到时刻)；拿不到就返回 (None, 0.0)。

        fresh 的取值跟随帧率：帧率越低，容忍的"帧龄"越长（否则永远嫌它旧）。
        """
        fresh = max(0.3, 1.2 / float(self.stream_fps))
        deadline = time.time() + max(0.5, float(timeout))
        with self._cond:
            if not self._alive:
                # 流已停/已断：一张都不给。否则调用方会把"上一次会话的旧画面"
                # 当成这一次抓拍成功，那才是最坏的情况（静默拿到过期图）。
                return None, 0.0
            while True:
                if self._frame is not None and (time.time() - self._frame_at) <= fresh:
                    return self._frame, self._frame_at
                if not self._alive:
                    break
                if time.time() >= deadline:
                    break
                self._cond.wait(0.3)
            # 没等到"新鲜帧"：只有在流仍然活着时才退让，用 5 秒内的旧帧兜底（弱网场景）。
            # 流已停/已断就直接认失败 —— 让上层走"断开重连"，而不是拿一张过期画面假装成功。
            if self._alive and self._frame is not None \
                    and (time.time() - self._frame_at) <= 5.0:
                return self._frame, self._frame_at
            return None, 0.0


def _wait_until(target, label="下一次抓拍"):
    """等到 target 时刻。终端里就地刷倒计时；输出被重定向时只打一行。"""
    try:
        tty = bool(sys.stdout.isatty())
    except Exception:
        tty = False
    if not tty:
        print("  %s：%s" % (label, target.strftime("%Y-%m-%d %H:%M:%S")))
        while True:
            left = (target - datetime.datetime.now()).total_seconds()
            if left <= 0:
                return
            time.sleep(min(left, 1.0))
    while True:
        left = (target - datetime.datetime.now()).total_seconds()
        if left <= 0:
            break
        if left >= 3600:
            tip = "还有 %d 小时 %02d 分" % (int(left // 3600), int((left % 3600) // 60))
        elif left >= 60:
            tip = "还有 %d 分 %02d 秒" % (int(left // 60), int(left % 60))
        else:
            tip = "还有 %4.1f 秒" % left
        sys.stdout.write("\r  %s %s  %s   "
                         % (label, target.strftime("%m-%d %H:%M:%S"), tip))
        sys.stdout.flush()
        time.sleep(0.1 if left < 2 else (0.5 if left < 10 else 1.0))
    sys.stdout.write("\r" + " " * 64 + "\r")
    sys.stdout.flush()


def _report_stream_problem(stream, shown_uri):
    """取流失败时，把 ffmpeg 的原话 + 本机的排查顺序打出来。"""
    tail = stream.tail(12) if stream is not None else []
    text = "\n".join(tail)
    low = text.lower()
    if "401" in text or "unauthorized" in low:
        print("      -> 摄像头回了 401（拒绝了这次 RTSP 请求）。这台设备这条最常见，按顺序查：")
        print("        1) 有没有别的程序在看？网页「预览」页 / ODM 的 Live video / 播放器 / 另一个 ffmpeg")
        print("           查：netstat -ano | findstr \":554\"  —— 有 ESTABLISHED 就是它占着。")
        print("        2) 是不是短时间里反复连过很多次（反复跑脚本、反复试密码）？")
        print("           设备的会话槽 / 失败计数可能已卡住 -> 给摄像头断电 30 秒重启，之后正确密码只跑一次。")
        print("        3) 密码对不对？第 1 步 ONVIF 能读出厂商/型号，就说明密码是对的，别再改密码。")
    elif any(k in low for k in ("connection", "timed out", "timeout", "refused",
                                "unreachable", "no route")):
        print("      -> 连不上摄像头：确认它还在线、IP 没变、电脑和它同网段。")
    elif "invalid data" in low or "could not find codec" in low:
        print("      -> 连上了但读不出视频：确认地址路径对不对（TP-Link 是 /stream1 或 /stream2）。")
    if tail:
        print("      —— ffmpeg 原话（最后几行）——")
        for line in tail[-6:]:
            print("        %s" % line)
    else:
        print("      （ffmpeg 没有输出任何错误信息）")


def build_schedule_from_args(args):
    """把 --every / --at / --between 解析成 Schedule。

    返回 (sched, None)；参数不合法时返回 (None, "人话错误提示")。
    ★ 预演（--dry-run）和正式跑共用这一套解析 —— 保证"预演看到的 = 实际跑的"。
    """
    every = at_list = between = None
    if args.every:
        try:
            every = parse_duration(args.every)
        except ValueError as e:
            return None, str(e)
    if args.at:
        at_list = []
        for part in str(args.at).replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                at_list.append(parse_at(part))
            except ValueError as e:
                return None, str(e)
        if not at_list:
            return None, "--at 里没写时刻。示例：--at 08:00,12:00,18:00"
    if args.between:
        try:
            between = parse_between(args.between)
        except ValueError as e:
            return None, str(e)
    return Schedule(every=every, at_list=at_list, between=between), None


def _plan_out_dir_prefix(args):
    """从 --out-dir / --out-prefix / --out 推出「(输出目录, 文件名前缀)」。"""
    out_dir = args.out_dir or "."
    prefix = args.out_prefix or "snapshot"
    if args.out:
        d, f = os.path.split(args.out)
        base = os.path.splitext(f)[0]
        if d:
            out_dir = d
        if base:
            prefix = base
    return out_dir, prefix


def dry_run_plan(args, preview_n=10):
    """★ 离线预演：只算时间、只打印计划。

    **不联网、不问密码、不写任何文件** —— 断网、摄像头没插、密码忘了，都能跑。
    用途：设置"任意时间间隔 / 任意时间点"后，先确认时刻算得对不对，再正式跑。
    """
    if not (args.every or args.at):
        if args.frames > 1:
            print("[i] --frames %d 是「立刻连抓 %d 张」，没有「预演」这一步 —— "
                  "去掉 --dry-run 直接跑就行。" % (args.frames, args.frames))
            return 2
        print("[!] 现在没有「定时计划」可以预演，二选一：")
        print("    --every 5m                     每 5 分钟一张（间隔模式）")
        print("    --at 08:00,12:00,18:00         每天这三个点各一张（时刻模式）")
        print('    --at "2026-10-03 09:00:00"     某个绝对时刻，只抓一次')
        print('    --at "10-03 09:00"             省略年份，今年已过就按明年算')
        print()
        print("    若你本意是预演脚本顶部【PLAN 配置区】的计划，请检查：")
        print("      ① read_camera.py 顶部 PLAN 里的 every 或 at 填了内容、enable 是 True；")
        print("      ② 运行时别加 --no-plan。")
        return 2

    sched, err = build_schedule_from_args(args)
    if err:
        print("[!] %s" % err)
        return 2

    out_dir, prefix = _plan_out_dir_prefix(args)
    limit = min(args.count, preview_n) if args.count else preview_n

    print("=" * 62)
    print("预演（--dry-run）：只打印计划 —— 不联网、不问密码、不抓图")
    print("=" * 62)
    print("  抓拍节奏 ：%s" % sched.describe())
    print("  输出目录 ：%s" % os.path.abspath(out_dir))
    print("  文件名   ：%s_年月日_时分秒.jpg" % prefix)
    print("  数量上限 ：%s" % ("%d 张，抓满自动退出" % args.count if args.count
                              else "不限，按 Ctrl+C 停止"))
    print("  现在时间 ：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print()

    preview, tt = [], datetime.datetime.now()
    for _ in range(limit):
        n = sched.next_after(tt)
        if n is None:
            break
        preview.append(n)
        tt = n
    if not preview:
        print("  [!] 按当前设置算不出任何未来的抓拍时刻。")
        print("      常见原因：--at 写的都是已经过去的绝对时刻；或 --between 时段把钟点全裁掉了。")
        return 2
    print("  接下来 %d 次抓拍：" % len(preview))
    for i, n in enumerate(preview, 1):
        delta = (n - datetime.datetime.now()).total_seconds()
        print("    %2d) %s   （%s后）"
              % (i, n.strftime("%Y-%m-%d %H:%M:%S"), human_duration(delta)))
    print()
    print("  [i] 计划没问题，就去掉 --dry-run 正式跑。")
    return 0


def run_schedule(args, uri, password):
    """定时 / 定间隔抓图的主流程（长连接版本）。"""
    # ---- 输出目录与文件名前缀（--out 也可以拿来定这两个）----
    out_dir = args.out_dir or "."
    prefix = args.out_prefix or "snapshot"
    if args.out:
        d, f = os.path.split(args.out)
        base = os.path.splitext(f)[0]
        if d:
            out_dir = d
        if base:
            prefix = base
    try:
        if out_dir and out_dir not in (".", ""):
            os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        print("[!] 建不了输出目录 %s：%s" % (out_dir, e))
        return 2

    def mask(u):
        return mask_uri(u, password)

    # ---- 组装调度器：与 --dry-run 预演共用同一套解析，保证"预演=实跑" ----
    sched, err = build_schedule_from_args(args)
    if err:
        print("[!] %s" % err)
        return 2

    print("=" * 62)
    print("定时抓图模式")
    print("=" * 62)
    print("  取流地址 ：%s" % mask(uri))
    print("  输出目录 ：%s" % os.path.abspath(out_dir))
    print("  文件名   ：%s_年月日_时分秒.jpg" % prefix)
    print("  抓拍节奏 ：%s" % sched.describe())
    print("  数量上限 ：%s" % ("%d 张，抓满自动退出" % args.count if args.count
                              else "不限，按 Ctrl+C 停止"))
    print("  会话策略 ：%s" % ("懒连接（每次抓拍才临时连一次；省带宽，但本机型有被回 401 的风险）"
                              if args.lazy else "长连接（全程只连这一次，推荐）"))
    if not args.sub:
        print("  建议     ：长跑时加 --sub 走子码流，带宽小、更稳。")
    print()

    # ---- 先把计划亮出来，让用户一眼确认 ----
    preview, tt = [], datetime.datetime.now()
    for _ in range(5 if not args.count else min(args.count, 5)):
        n = sched.next_after(tt)
        if n is None:
            break
        preview.append(n)
        tt = n
    if not preview:
        print("  [!] 按当前设置算不出任何未来的抓拍时刻，请检查 --every / --at / --between。")
        return 2
    print("  接下来 %d 次抓拍：" % len(preview))
    for i, n in enumerate(preview, 1):
        print("    %d) %s" % (i, n.strftime("%Y-%m-%d %H:%M:%S")))
    print()

    if args.dry_run:
        print("  [i] 这是 --dry-run，只预演、不抓图。计划没问题就去掉 --dry-run 正式跑。")
        return 0

    # ---- 占用体检：这台设备只有一路会话 ----
    holders = find_stream_holders(args.ip, 554)
    if holders:
        print("  [!] 本机已有程序占着该摄像头的视频流：%s" % "、".join(holders))
        print("      这台设备同时只允许一路 RTSP 客户端，那样我们会一直拿不到画面（401）。")
        print("      建议先关掉：网页「预览」页 / ODM 的 Live video / 播放器。")
        print()

    stream = None
    ok_n = fail_n = 0
    t = datetime.datetime.now()
    try:
        if not args.lazy:
            print("  正在建立长连接（本次运行只连这一次）……")
            stream = RtspFrameStream(uri, args.stream_fps).start()
            _STREAMS.append(stream)
            jpg, _ts = stream.grab(timeout=20)
            if jpg is None:
                print("  [失败] 长连接没拿到画面。")
                _report_stream_problem(stream, mask(uri))
                return 1
            print("  [OK] 取流已建立，首帧 %.1f KB。" % (len(jpg) / 1024.0))
            print()

        while True:
            now = datetime.datetime.now()
            nxt = sched.next_after(t)
            if nxt is None:
                print("  [i] 没有更多抓拍时刻了（绝对时刻已全部过去），收工。")
                break
            if nxt < now:                    # 上一轮抓拍拖太久，节拍落后了，拉回现在
                nxt = now
            _wait_until(nxt)
            t = nxt

            # ---- 取帧：长连接直接取；懒连接临时开一条 ----
            if args.lazy:
                stream = RtspFrameStream(uri, args.stream_fps).start()
                _STREAMS.append(stream)
                jpg, _ts = stream.grab(timeout=20)
                stream.stop()
                try:
                    _STREAMS.remove(stream)
                except ValueError:
                    pass
                err_ref = stream
            else:
                if stream is None or not stream.alive:
                    print("  [!] 取流断了，重新连一次……")
                    if stream is not None:
                        stream.stop()
                        try:
                            _STREAMS.remove(stream)
                        except ValueError:
                            pass
                    stream = RtspFrameStream(uri, args.stream_fps).start()
                    _STREAMS.append(stream)
                jpg, _ts = stream.grab(timeout=20)
                err_ref = stream

            if jpg is None:
                fail_n += 1
                print("  [失败] %s 这一张没拿到画面（连续第 %d 次失败）"
                      % (t.strftime("%H:%M:%S"), fail_n))
                _report_stream_problem(err_ref, mask(uri))
                if fail_n >= 5:
                    print()
                    print("  连续 5 次没拿到画面，先停下来。多半是：")
                    print("    · 有别的客户端占着唯一那路流（关掉网页预览 / ODM Live video）")
                    print("    · 设备的会话槽卡住了 -> 给摄像头断电 30 秒重启，再跑一次")
                    break
            else:
                fail_n = 0
                name = "%s_%s.jpg" % (prefix, datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
                path = os.path.join(out_dir, name)
                try:
                    with open(path, "wb") as fh:
                        fh.write(jpg)
                except Exception as e:
                    print("  [失败] 写文件失败：%s（%s）" % (path, e))
                    break
                ok_n += 1
                print("  [成功] %s  ->  %s  (%.1f KB)"
                      % (t.strftime("%H:%M:%S"), os.path.abspath(path), len(jpg) / 1024.0))

            if args.count and ok_n >= args.count:
                print()
                print("  已抓满 %d 张，收工。" % ok_n)
                break
    except KeyboardInterrupt:
        print()
        print("  收到 Ctrl+C，停止。本次共抓到 %d 张。" % ok_n)
    finally:
        for s in list(_STREAMS):
            try:
                s.stop()
            except Exception:
                pass
        try:
            _STREAMS.clear()
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="ONVIF 读信息 + 抓一帧")
    ap.add_argument("--ip", default="192.168.1.60")
    ap.add_argument("--onvif-port", type=int, default=2020,
                    help="ONVIF 端口。TP-Link=2020，海康/大华=80")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default=None,
                    help="RTSP/设备密码。不填会交互式询问（推荐，密码不留在命令历史里）")
    ap.add_argument("--out", default=None, help="输出图片路径，默认 snapshot_时间戳.jpg")
    ap.add_argument("--sub", action="store_true", help="取子码流（stream2）")
    ap.add_argument("--frames", type=int, default=1,
                    help="抓几帧（默认 1）。>1 时在【同一个 RTSP 会话】内按 --interval 间隔连续抓，"
                         "文件名自动加序号。强烈建议需要多张时用这个，别反复跑脚本")
    ap.add_argument("--interval", type=int, default=5,
                    help="--frames>1 时每帧间隔秒数（默认 5）")

    # ---- 定时 / 定间隔抓图（2026-10-02 新增；一旦用它就进入"定时模式"）----
    g = ap.add_argument_group("定时抓图（--every 与 --at 二选一，给了就进入定时模式）")
    g.add_argument("--plan", action="store_true",
                   help="按【文件顶部 PLAN 配置区】跑（其实不写这个也是默认按 PLAN 跑，留着兼容）")
    g.add_argument("--no-plan", action="store_true",
                   help="忽略顶部 PLAN，只抓一张（临时抓单张时用）")
    g.add_argument("--every", default=None, metavar="间隔",
                   help="每隔多久抓一张：30（秒）/ 90s / 5m / 2h / 1d，也认 分/时/天")
    g.add_argument("--at", default=None, metavar="时刻[,时刻...]",
                   help="在固定时刻抓：每天重复写 08:00,12:00,18:00；"
                        "绝对时刻写 \"2026-10-03 09:00:00\"（只抓一次）")
    g.add_argument("--between", default=None, metavar="起-止",
                   help="配合 --every：只在这个时段内抓，如 09:00-17:00")
    g.add_argument("--count", type=int, default=0, metavar="N",
                   help="抓满 N 张就退出（默认 0 = 一直跑，按 Ctrl+C 停）")
    g.add_argument("--out-dir", default=None, metavar="目录",
                   help="图片输出目录（默认当前目录）")
    g.add_argument("--out-prefix", default="snapshot", metavar="前缀",
                   help="文件名前缀，实际文件名 = 前缀_年月日_时分秒.jpg（默认 snapshot）")
    g.add_argument("--stream-fps", type=int, default=2, metavar="FPS",
                   help="长连接每秒解码几帧，决定抓拍时刻的精度（默认 2）")
    g.add_argument("--lazy", action="store_true",
                   help="改成「抓拍时才临时连一次」（省带宽，但本机型可能因此回 401，默认不用）")
    g.add_argument("--dry-run", action="store_true",
                   help="只打印抓拍计划：不联网、不问密码、不抓图（先确认时刻算得对不对）")

    args = ap.parse_args()

    # ---- 顶部 PLAN 配置区：**默认就按它跑**，不需要 --plan ----
    # 你只要改本文件顶部那几行，然后【直接运行本脚本】即可（F5 / 双击 / 终端都行）。
    # 什么时候套用 PLAN：
    #   裸跑，或只加 --dry-run / --sub 这类"不指定节奏"的参数 → 自动套用 PLAN
    #   显式给了 --every / --at                               → 以命令行为准（临时改一次，不动代码）
    #   显式给了 --frames N（N>1）                            → 那是"立刻连抓 N 张"，不套用 PLAN
    #   --no-plan                                             → 明确忽略 PLAN，只抓一张
    explicit_rhythm = bool(args.every or args.at)
    explicit_multi = args.frames > 1
    want_plan = args.plan or (not explicit_rhythm and not explicit_multi)
    use_plan = want_plan and not args.no_plan and bool(PLAN.get("enable", True))

    if use_plan:
        if PLAN.get("every") and PLAN.get("at"):
            print("[!] PLAN 里 every 和 at 同时填了，只能留一个：")
            print("    「每隔多久抓」→ 填 every、把 at 清空；「固定时间点抓」→ 反过来。")
            return 2
        args.every = args.every or (str(PLAN.get("every") or "") or None)
        args.at = args.at or (str(PLAN.get("at") or "") or None)
        args.between = args.between or (str(PLAN.get("between") or "") or None)
        if not args.count:
            try:
                args.count = int(PLAN.get("count") or 0)
            except (TypeError, ValueError):
                print("[!] PLAN 里的 count 要填整数（0 = 一直抓）。收到：%r" % (PLAN.get("count"),))
                return 2
        if not (args.every or args.at):
            if args.plan:
                print("[!] PLAN 里的 every 和 at 都是空的，不知道该什么时候抓。")
                print("    请到 read_camera.py 顶部的【PLAN 配置区】，把 every 或 at 填上一个。")
                return 2
            # 裸跑、且 PLAN 里没设节奏 → 不硬报错，退回"只抓一张"
            use_plan = False
        else:
            if not args.sub:
                args.sub = bool(PLAN.get("sub"))
            if not args.out_dir and PLAN.get("out_dir"):
                # 相对路径一律按【脚本所在目录】算，这样从哪儿跑，图片都进同一个文件夹
                base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
                args.out_dir = os.path.join(base, str(PLAN["out_dir"]))

    # ---- 定时模式参数体检：早失败早省事，别等输完密码才报错 ----
    if args.every and args.at:
        print("[!] --every 和 --at 只能选一个（它们都是「什么时候抓」的设定）：")
        print("    --every 5m                每 5 分钟一张")
        print("    --at 08:00,12:00,18:00    每天这三个点各一张")
        return 2
    if args.between and not args.every:
        print("[!] --between 只配合 --every 用（例：--every 15m --between 09:00-17:00）。")
        return 2
    if (args.every or args.at) and args.frames > 1:
        print("[!] --frames 是「现在立刻连抓几张」，跟定时模式（--every/--at）不是一回事：")
        print("    要「每 N 秒一张」，请用：--every N --count 张数")
        return 2

    # 环境体检：是哪个解释器在跑、依赖齐不齐（VSCode 里最容易在这栽跟头）
    check_env()

    # ---- 预演模式（--dry-run）----
    # 特意放在这里：**不问密码、不连 ONVIF、不碰摄像头、不写任何文件**。
    # 目的是"断网、摄像头没插、密码忘了"也能先把抓拍计划算出来看一眼，
    # 确认"任意时间间隔 / 任意时间点"设置得对不对，再去正式跑。
    if args.dry_run:
        return dry_run_plan(args)

    # 密码：命令行没给就交互式问，避免密码出现在命令历史/屏幕录制里
    password = args.password
    if password is None:
        # 用 input() 而不是 getpass()：任何终端都能用，不会出现"卡住没反应"。
        # 密码只是显示在屏幕上，不会进入命令历史，比写在命令行里安全。
        try:
            password = input("请输入摄像头密码，然后按回车: ").strip()
        except EOFError:
            password = ""
        if not password:
            print('[!] 没输入密码。如果摄像头确实允许空密码，请用 --password "" 明确传入。')
            return 2
    args.password = password

    # 提前体检：密码里的特殊字符只影响 RTSP（ONVIF 走 HTTP 头不受影响）。
    # 典型症状就是"密码明明对，RTSP 却 401"。本脚本已自动做百分号编码，这里只提示。
    risky = [c for c in "@:/?#&% " if c in password]
    if any(ord(c) > 127 for c in password):
        risky.append("非ASCII字符")
    if risky:
        print("[i] 密码里含特殊字符：%s"
              % " ".join(repr(c) if c != "非ASCII字符" else c for c in risky))
        print("    不编码会让 RTSP 地址被解析歪，表现成 401。脚本已自动做百分号编码，继续。\n")

    def _mask(u):
        return mask_uri(u, args.password)

    out = args.out or ("snapshot_%s.jpg" % time.strftime("%Y%m%d_%H%M%S"))

    print("=" * 62)
    print("第 1 步 / ONVIF 信令：读取设备信息")
    print("=" * 62)
    info, uri = {}, None
    try:
        info, uri = read_onvif_info(args.ip, args.onvif_port, args.user,
                                    args.password, sub=args.sub)
        for k, v in info.items():
            print("  %-8s %s" % (k + "：", v))
        if info.get("厂商"):
            print("  → 结论：用户名【%s】+ 你输的密码 **通过了 ONVIF 鉴权**，凭据是对的。" % args.user)
            print("    如果下面第 3 步仍然报 401，那就不是密码本身的问题。")
    except Exception as e:
        print("  [!] ONVIF 整体失败: %s" % e)
        print("      检查：ONVIF 是否开启 / 端口对不对 / 防火墙")

    print()
    print("=" * 62)
    print("第 2 步 / 确定 RTSP 取流地址")
    print("=" * 62)
    if not uri:
        uri = guess_rtsp_url(args.ip, args.user, args.password, sub=args.sub)
        print("  使用固定拼接地址：%s" % _mask(uri))
    else:
        raw = uri
        uri = inject_credentials(uri, args.user, args.password)
        print("  ONVIF 返回地址：%s" % raw)
        print("  补上凭据后  ：%s" % _mask(uri))
        print("  （ONVIF 规范里 GetStreamUri 不带凭据，必须自己补，否则匿名请求必 401）")

    # ---- 定时 / 定间隔模式：整条取流路子都不一样（常驻单会话），在这里分出去 ----
    if args.every or args.at:
        print()
        return run_schedule(args, uri, args.password)

    print()
    print("=" * 62)
    print("第 3 步 / RTSP 拉流抓帧")
    print("=" * 62)

    if args.frames > 1:
        print("  模式：单会话连续抓 %d 帧，每 %d 秒一帧（约需 %d 秒）"
              % (args.frames, args.interval, args.frames * args.interval))
        print("  （这条路子专治本机'反复开会话把设备耗死'的毛病）")

    # 体检：这台设备只有一路稳定的 RTSP 会话。
    # 若有别的程序（网页预览插件 / ODM Live video / VLC）正看着，我们必被 401。
    holders = find_stream_holders(args.ip, 554)
    if holders:
        print("  [!] 检测到本机已有程序正在占用摄像头视频流：%s" % "、".join(holders))
        print("      这台设备同时只允许【一路】RTSP 客户端。若它占着流，我们拉流会被回 401,")
        print("      而这个 401 很容易被误读成\"密码错\"。")
        print("      → 建议先关掉网页预览页 / ODM 的 Live video / 播放器，再跑本脚本。")
        print("      （仍继续尝试，也许它只是挂着空连接。）")
        print()
    else:
        print("  [i] 本机没有其它程序占用该摄像头的视频流（这一项排除）。")
        print()

    ok, made, size, cost, err = grab_frames(uri, out, frames=args.frames,
                                            interval=args.interval)
    if ok:
        print("  成功！%.1f 秒拿到 %d 帧" % (cost, len(made)))
        for f in made:
            print("  文件：%s  (%.1f KB)" % (os.path.abspath(f), os.path.getsize(f) / 1024.0))
    else:
        print("  失败（耗时 %.1f 秒）" % cost)
        if "401" in err:
            print("  → 401 Unauthorized：摄像头拒绝了这次 RTSP 请求")
            print("     实际请求的地址：%s" % _mask(uri))
            print("     排查顺序（本机 2026-10-02 实测沉淀，按概率）：")
            print("     1) ★ 本机无占用、密码也对（第 1 步 ONVIF 已通过），却仍秒回 401")
            print("        → 最可能是【设备的 RTSP 侧处于'卡住'状态】：")
            print("          · 它只有一路稳定会话，且用完不干脆释放；")
            print("          · 上一位客户端的会话没清干净，后面谁来都被回 401（而它不说\"忙\"）。")
            print("        ★ 解法：把摄像头【断电 30 秒】再上电，等 2 分钟起完，")
            print("          然后用正确密码【只跑一次】。若要多张图，用 --frames 在一个会话里抓完。")
            print("     2) 视频流被别的客户端占用（网页预览页 / ODM Live video / 播放器）")
            print("        → 脚本开头那张体检表会自动列出占用进程，关掉它们再跑。")
            print("     3) 地址里到底有没有凭据 —— 漏了 user:pass@ 就是匿名请求，必然 401。")
            print("        ONVIF 的 GetStreamUri 天生不带凭据，本脚本已自动补。")
            print("     4) 失败次数累计触发了临时锁定（固件写明错 20 次锁 30 分钟）。")
            print("        → 同上：断电重启最干脆，且之后别连试。")
        elif err:
            for line in err.splitlines()[:6]:
                print("  ffmpeg: %s" % line)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
