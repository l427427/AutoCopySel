"""托盘菜单。"""
from __future__ import annotations

import ctypes
import subprocess
import sys

import pystray
from pystray import Menu, MenuItem

from . import APP_NAME, __version__
from .config import autostart_enabled


def native_prompt(
    title: str, message: str, buttons: tuple[str, ...] = ("好",), wait: bool = True
) -> str:
    """系统原生弹窗。返回被点击的按钮文字；wait=False 时直接返回第一个按钮。"""
    buttons = tuple(buttons) or ("好",)
    try:
        if sys.platform == "darwin":
            btns = "{" + ", ".join(f'"{b}"' for b in buttons) + "}"
            script = (
                "on run argv\n"
                f"display dialog (item 2 of argv as text) buttons {btns} "
                "default button 1 with title (item 1 of argv as text)\n"
                "end run"
            )
            args = ["osascript", "-e", script, title, message]
            if not wait:
                subprocess.Popen(
                    args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                return buttons[0]
            proc = subprocess.run(args, capture_output=True, timeout=900)
            out = (proc.stdout or b"").decode("utf-8", "replace")
            if "button returned:" in out:
                return out.split("button returned:", 1)[1].strip()
            return buttons[0]

        if sys.platform == "win32":
            flags = 0x40 | (0x4 if len(buttons) > 1 else 0x0)  # ICONINFO | YESNO
            ret = ctypes.windll.user32.MessageBoxW(None, message, title, flags)
            if len(buttons) > 1:
                return buttons[0] if ret == 6 else buttons[1]
            return buttons[0]

        print(f"{title}\n{message}")
        return buttons[0]
    except Exception:
        return buttons[0]


def native_alert(title: str, message: str) -> None:
    native_prompt(title, message, wait=False)


def _preview(text: str, width: int = 42) -> str:
    line = text.replace("\r", " ").replace("\n", " ").strip()
    if len(line) > width:
        line = line[:width] + "…"
    return line or "(空)"


def _history_menu(app):
    def generate():
        items = list(app.service.history)
        if not items:
            return [MenuItem("（暂无记录）", None, enabled=False)]
        out = []
        for text, _ts in items[:10]:
            out.append(
                MenuItem(
                    f"复制：{_preview(text)}",
                    _make_paste(app, text),
                )
            )
        return out

    return Menu(generate)


def _make_paste(app, text: str):
    def handler(icon, item):
        app.paste_text(text)

    return handler


def build_menu(app) -> Menu:
    cfg = app.cfg
    service = app.service

    def status_label(item):
        ax_ok, li_ok = app.permission_flags()
        if ax_ok is False and li_ok is False:
            return "⚠ 缺「辅助功能」和「输入监控」— 点此去补"
        if ax_ok is False:
            return "⚠ 缺「辅助功能」权限 — 点此去补"
        if li_ok is False:
            return "⚠ 缺「输入监控」权限 — 点此去补"
        if not service.enabled:
            return "○ 已暂停 — 点击启用"
        if not service.watcher_alive():
            return "⚠ 事件监听未运行 — 点此看诊断"
        return "● 运行中 — 点击暂停"

    def status_action(icon, item):
        ax_ok, li_ok = app.permission_flags()
        if ax_ok is False or li_ok is False:
            # 缺权限时直接跳到缺的那个面板（不再默认去重置，少一步）
            app.open_permission()
        elif service.enabled and not service.watcher_alive():
            app.run_diagnostics()
        else:
            app.toggle()

    mode_menu = Menu(
        MenuItem(
            "自动：接口优先，失败再模拟按键",
            lambda i, it: app.set_mode("auto"),
            checked=lambda it: cfg.get("capture_mode") == "auto",
            radio=True,
        ),
        MenuItem(
            "仅无障碍接口（不动剪贴板之外的任何东西）",
            lambda i, it: app.set_mode("ax"),
            checked=lambda it: cfg.get("capture_mode") == "ax",
            radio=True,
        ),
        MenuItem(
            "仅模拟复制键（兼容性最好）",
            lambda i, it: app.set_mode("key"),
            checked=lambda it: cfg.get("capture_mode") == "key",
            radio=True,
        ),
    )

    items = [
        MenuItem(status_label, status_action, default=True),
        Menu.SEPARATOR,
        MenuItem("立即复制当前选中", lambda i, it: app.copy_now()),
        MenuItem("最近复制", _history_menu(app)),
        Menu.SEPARATOR,
        MenuItem("取词方式", mode_menu),
        MenuItem(
            "左键按住 + 右键单击 → 粘贴",
            lambda i, it: app.toggle_paste_gesture(),
            checked=lambda it: bool(cfg.get("paste_gesture")),
        ),
        MenuItem(
            "提示音",
            lambda i, it: app.toggle_sound(),
            checked=lambda it: bool(cfg.get("sound")),
        ),
        MenuItem(
            "开机自启",
            lambda i, it: app.toggle_autostart(),
            checked=lambda it: autostart_enabled(),
        ),
        Menu.SEPARATOR,
    ]

    if sys.platform == "darwin":
        items += [
            MenuItem(
                "打开「辅助功能」设置",
                lambda i, it: app.open_permission("accessibility"),
            ),
            MenuItem(
                "打开「输入监控」设置",
                lambda i, it: app.open_permission("listen_event"),
            ),
            MenuItem("修复权限（重置并重新授权）", lambda i, it: app.fix_permission()),
        ]

    items += [
        MenuItem("运行诊断", lambda i, it: app.run_diagnostics()),
        MenuItem("打开配置目录", lambda i, it: app.open_config()),
        MenuItem(
            f"关于 {APP_NAME} v{__version__}",
            lambda i, it: native_alert(APP_NAME, app.about()),
        ),
        MenuItem("退出", lambda i, it: app.quit()),
    ]

    return Menu(*items)
