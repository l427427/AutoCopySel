"""自检与诊断：把「为什么没反应」变成一条条可验证的结论。"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from . import APP_ID, __version__, clipboard

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"


# ---------------------------------------------------------------- 单项检查
def bundle_root() -> Path | None:
    """如果是打包运行的 .app，返回 .app 路径。"""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def signing_info() -> tuple[str, str]:
    """返回 (cdhash, 校验结论)。"""
    if sys.platform != "darwin":
        return "", "非 macOS"
    root = bundle_root()
    if root is None:
        return "", "源码运行，签名信息不适用（授权对象是启动它的终端程序）"
    target = root
    try:
        out = subprocess.run(
            ["codesign", "-d", "-vv", str(target)],
            capture_output=True,
            timeout=10,
        )
        text = (out.stderr or b"").decode("utf-8", "replace")
        cdhash = ""
        for line in text.splitlines():
            if "CandidateCDHash sha256" in line:
                cdhash = line.split("=")[-1].strip()
            elif line.startswith("CDHash=") and not cdhash:
                cdhash = line.split("=", 1)[1].strip()
        ver = subprocess.run(
            ["codesign", "--verify", "--strict", str(target)],
            capture_output=True,
            timeout=10,
        )
        return cdhash, ("签名有效" if ver.returncode == 0 else "签名校验失败")
    except Exception as exc:
        return "", f"读取失败: {exc}"


def event_tap_ok() -> tuple[str, str]:
    """决定性检查：能不能真的建立一个只监听的事件 tap。

    AXIsProcessTrusted() 为 True 但这里失败，说明授权没落到当前这个二进制上。
    """
    if sys.platform != "darwin":
        return OK, "非 macOS，无需事件 tap"
    try:
        import Quartz

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown) | Quartz.CGEventMaskBit(
            Quartz.kCGEventLeftMouseUp
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            lambda proxy, etype, event, refcon: event,
            None,
        )
        if tap is None:
            return FAIL, "无法建立事件 tap → 辅助功能授权对当前程序未生效"
        del tap
        return OK, "事件 tap 可建立"
    except Exception as exc:
        return FAIL, f"事件 tap 检查异常: {exc}"


def ax_trusted() -> tuple[str, str]:
    if sys.platform != "darwin":
        return OK, "非 macOS"
    try:
        import ApplicationServices as AS

        if AS.AXIsProcessTrusted():
            return OK, "已授权（读选区用）"
        return FAIL, "未授权 → 系统设置 → 隐私与安全性 → 辅助功能 里勾选本程序"
    except Exception as exc:
        return FAIL, f"检测失败: {exc}"


def listen_event_trusted() -> tuple[str, str]:
    """输入监控权限：建 CGEventTap 监听鼠标必需，缺它选区复制和粘贴都不工作。"""
    if sys.platform != "darwin":
        return OK, "非 macOS"
    try:
        import Quartz

        fn = getattr(Quartz, "CGPreflightListenEventAccess", None)
        if fn is None:
            return WARN, "当前系统取不到该权限的检测接口"
        if fn():
            return OK, "已授权（监听鼠标用）"
        return FAIL, "未授权 → 系统设置 → 隐私与安全性 → 输入监控 里勾选本程序"
    except Exception as exc:
        return FAIL, f"检测失败: {exc}"


def clipboard_ok() -> tuple[str, str]:
    """检查剪贴板读写，并保证不破坏用户原有的剪贴板内容。"""
    saved = clipboard.get_text()

    # 剪贴板里如果是图片/文件（没有文本），写入测试会把它们冲掉且无法还原，
    # 这种时候只做读检查，不动用户的内容。
    if not saved and clipboard.has_content():
        return OK, "读取正常；剪贴板当前是非文本内容，已跳过写入测试以免破坏它"

    try:
        sample = "__acs_probe__"
        if not clipboard.set_text(sample):
            return FAIL, "写入失败"
        got = clipboard.get_text()
        if got != sample:
            return WARN, f"写入后读回不一致（{got[:20]!r}）"
        return OK, "读写正常"
    except Exception as exc:
        return FAIL, f"异常: {exc}"
    finally:
        if saved:
            clipboard.set_text(saved)


def autostart_state() -> str:
    try:
        from .config import autostart_enabled

        return "已开启" if autostart_enabled() else "未开启"
    except Exception:
        return "未知"


def launch_context() -> tuple[str, str]:
    """判断本进程是不是「被别人启动的」。

    macOS 的 TCC 是按「负责进程」判定的：从终端里跑本程序，权限会被算到终端头上，
    于是 AXIsProcessTrusted() 会说 True，但双击启动时其实是 False。
    这个坑必须显式提示，否则诊断结果会误导人。
    """
    if sys.platform != "darwin":
        return OK, "非 macOS"
    try:
        ppid = os.getppid()
    except Exception:
        return OK, "无法判断"
    if ppid <= 1:
        return OK, "由系统直接启动，权限按本程序计算（结果可信）"
    name = ""
    try:
        out = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(ppid)], capture_output=True, timeout=3
        )
        name = (out.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        pass
    name = name.rsplit("/", 1)[-1] or f"pid {ppid}"
    return (
        WARN,
        f"由「{name}」启动 —— 权限可能被算在父进程身上，本页的授权/tap 结果仅供参照；"
        "要看真实结果，请从访达双击 AutoCopySel.app 启动后再诊断",
    )


# ---------------------------------------------------------------- 汇总
def collect(cfg=None) -> list[tuple[str, str, str]]:
    """返回 [(检查项, 状态, 说明)]。"""
    rows: list[tuple[str, str, str]] = []

    chip = platform.machine().lower()
    chip_name = {"arm64": "Apple Silicon (M 系列)", "x86_64": "Intel"}.get(
        chip, chip
    )
    rows.append(("系统", OK, f"macOS {platform.mac_ver()[0]} / {chip_name}"))
    rows.append(("程序版本", OK, f"AutoCopySel {__version__}"))

    root = bundle_root()
    if root:
        where = str(root)
        state = OK if str(root).startswith("/Applications") else WARN
        rows.append(("运行方式", state, f"打包运行：{where}"))
    else:
        rows.append(
            ("运行方式", WARN, f"源码运行：{Path(sys.argv[0]).resolve()}（授权对象是调用它的终端）")
        )

    if sys.platform == "darwin":
        rows.append(("启动方式",) + launch_context())

    if sys.platform == "darwin":
        cdhash, verdict = signing_info()
        if cdhash:
            rows.append(
                (
                    "代码签名",
                    OK if verdict == "签名有效" else WARN,
                    f"{verdict}；cdhash={cdhash[:16]}…（重建会改变 cdhash，授权需重新勾一次）",
                )
            )
        else:
            rows.append(("代码签名", WARN, verdict))
        rows.append(("辅助功能授权",) + ax_trusted())
        rows.append(("输入监控授权",) + listen_event_trusted())
        rows.append(("事件监听自检",) + event_tap_ok())

    rows.append(("剪贴板",) + clipboard_ok())

    try:
        from .backends import get_backend

        b = get_backend()
        key, name = b.frontmost_app()
        rows.append(("前台应用", OK, f"{name or '未知'}（{key or '?'}）"))
        if cfg is not None and cfg.is_blacklisted(key):
            rows.append(
                ("黑名单", WARN, "当前前台应用在 app_blacklist 内，按键模拟会被跳过")
            )
    except Exception:
        pass

    if cfg is not None:
        rows.append(
            (
                "当前配置",
                OK,
                f"启用={cfg.get('enabled')}；取词方式={cfg.get('capture_mode')}；延迟={cfg.get('delay_ms')}ms",
            )
        )
        rows.append(("开机自启", OK, autostart_state()))
        rows.append(("配置文件", OK, str(cfg.path)))

    return rows


def format_report(rows: list[tuple[str, str, str]]) -> str:
    icon = {OK: "✓", WARN: "!", FAIL: "✗"}
    width = max((len(r[0]) for r in rows), default=4)
    lines = []
    for name, state, detail in rows:
        lines.append(f"{icon.get(state, '?')} {name.ljust(width)}  {detail}")
    fails = [r for r in rows if r[1] == FAIL]
    if fails:
        lines.append("")
        lines.append("有 " + str(len(fails)) + " 项失败，最常见的原因是辅助功能授权没有落到当前这份程序上。")
    return "\n".join(lines)


def advice(rows: list[tuple[str, str, str]]) -> str:
    failed = {r[0] for r in rows if r[1] == FAIL}
    if "事件监听自检" in failed or "辅助功能授权" in failed or "输入监控授权" in failed:
        return (
            "修复步骤：\n"
            "1. 点菜单里的「修复权限」，它会清掉辅助功能和输入监控两个旧记录；\n"
            "2. 两个面板都要勾：辅助功能（读选区）+ 输入监控（监听鼠标）；\n"
            "3. 如果列表里已有同名的旧条目，先按 − 删掉，再用 + 手动添加本程序；\n"
            "4. 回到程序，最多 2 秒会自动生效，不用重启。"
        )
    return ""


# ---------------------------------------------------------------- 修复
def reset_permission() -> tuple[bool, str]:
    """清掉本程序的辅助功能授权记录（TCC），让授权重新走一遍。"""
    if sys.platform != "darwin":
        return False, "仅 macOS 支持"
    import shutil

    # 打包成 .app 后由访达启动时 PATH 可能不完整，用绝对路径更稳
    tccutil = shutil.which("tccutil") or "/usr/bin/tccutil"
    try:
        # 两个权限都要清：只清辅助功能的话，输入监控里的旧记录（对应旧 cdhash）
        # 会让系统设置显示「已勾选」但实际被拒——这正是「勾了却没反应」的根源。
        messages = []
        ok_all = True
        for service in ("Accessibility", "ListenEvent"):
            proc = subprocess.run(
                [tccutil, "reset", service, APP_ID],
                capture_output=True,
                timeout=15,
            )
            out = (proc.stdout or b"").decode("utf-8", "replace").strip()
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            messages.append(f"{service}: {out or err or ('exit=' + str(proc.returncode))}")
            if proc.returncode != 0:
                ok_all = False
        msg = " | ".join(messages)
        opener = shutil.which("open") or "/usr/bin/open"
        try:
            subprocess.Popen(
                [
                    opener,
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
                ]
            )
        except Exception:
            pass
        return ok_all, msg
    except Exception as exc:
        return False, str(exc)


def tail_log(lines: int = 12) -> str:
    from .config import LOG_PATH

    try:
        text = LOG_PATH.read_text("utf-8", errors="replace").splitlines()
        return "\n".join(text[-lines:])
    except Exception:
        return "（无日志）"


def env_note() -> str:
    return f"pid={os.getpid()}  python={sys.version.split()[0]}"
