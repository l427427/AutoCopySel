"""配置读写 + 开机自启。配置文件默认在 ~/.autocopysel/config.json。"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("AUTOCOPYSEL_HOME") or (Path.home() / ".autocopysel"))
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "app.log"
LOCK_PATH = APP_DIR / "app.lock"

# 终端类应用：在「按键模拟」模式下绝不能给它们发 Cmd+C / Ctrl+C
# （macOS 上是正常复制，Windows 上 Ctrl+C 会变成 SIGINT 打断进程）
BLACKLIST_DARWIN = [
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "net.kovidgoyal.kitty",
    "io.alacritty",
    "com.mitchellh.ghostty",
    "org.wezfurlong.wezterm",
    "dev.warp.Warp-Stable",
    "co.zeit.hyper",
    "com.github.Electron.term",
    "com.apple.ScreenSharing",
]
BLACKLIST_WIN32 = [
    "windowsterminal.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "conhost.exe",
    "mintty.exe",
    "wezterm-gui.exe",
    "alacritty.exe",
    "putty.exe",
    "tabby.exe",
    "hyper.exe",
]

DEFAULTS: dict[str, Any] = {
    # 总开关
    "enabled": True,
    # 鼠标按住拖动超过该像素才算「划词」
    "drag_threshold_px": 6,
    # 按住时间短于该值视为误触（毫秒）
    "press_min_ms": 80,
    # 松开鼠标后等待多少毫秒再取词（等应用把选区定下来）
    "delay_ms": 130,
    # 取词方式：auto = 先用无障碍接口，失败再模拟复制键；ax = 只读接口；key = 只模拟按键
    "capture_mode": "auto",
    # 双击 / 三击选词也触发
    "detect_multi_click": True,
    # 按住 Ctrl / Cmd / Win 时拖动不触发（避免和「Cmd+拖动」类操作冲突）
    "ignore_with_modifiers": True,
    # 只有纯鼠标选择才复制：按住 Shift 的点击/拖动（键盘辅助扩展选区）不触发。
    # 关掉它 = 旧行为，Shift+点击也会复制。键盘选中（Cmd+A 等）无论如何都不触发，
    # 因为本程序只监听鼠标事件，没有键盘监听器。
    "mouse_only_selection": True,
    # 左键按住期间单击右键 → 粘贴；判定见 hook.py 的 _on_right_click
    "paste_gesture": True,
    # 连续贴两次之间至少间隔多少毫秒（过滤右键抖动）
    "paste_debounce_ms": 250,
    # 文本长度过滤
    "min_length": 1,
    "max_length": 100000,
    # 去掉首尾空白
    "trim": True,
    # 复制成功的提示音
    "sound": False,
    # 内部历史记录条数（菜单里可回贴）
    "history_size": 20,
    # 两次取词的最小间隔（毫秒），防止连击重复
    "debounce_ms": 120,
    # 等剪贴板变化的最长时间（毫秒）
    "timeout_ms": 450,
    # 托盘图标闪烁提示
    "flash_icon": True,
    # 应用黑名单（bundle id / 进程名，小写）
    "app_blacklist": [],
    # 开机自启
    "autostart": False,
}


def _platform_blacklist() -> list[str]:
    if sys.platform == "darwin":
        items = BLACKLIST_DARWIN
    elif sys.platform == "win32":
        items = BLACKLIST_WIN32
    else:
        items = []
    return [i.lower() for i in items]


def ensure_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


class Config:
    def __init__(self, data: dict[str, Any] | None = None, path: Path | None = None):
        self.path = path or CONFIG_PATH
        self.data = copy.deepcopy(DEFAULTS)
        if data:
            self.data.update({k: v for k, v in data.items() if v is not None})
        if not self.data.get("app_blacklist"):
            self.data["app_blacklist"] = _platform_blacklist()

    # ---------- 存取 ----------
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        p = path or CONFIG_PATH
        data: dict[str, Any] = {}
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
            except Exception:
                data = {}
        cfg = cls(data, p)
        if not p.exists():
            cfg.save()
        return cfg

    def save(self) -> None:
        ensure_dir()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)

    # ---------- 访问 ----------
    def __getitem__(self, key: str) -> Any:
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self.data[key] = value
        if save:
            self.save()

    def is_blacklisted(self, app_key: str | None) -> bool:
        if not app_key:
            return False
        key = app_key.lower()
        for item in self.data.get("app_blacklist") or []:
            if item and (item == key or item in key):
                return True
        return False


# ---------------- 开机自启 ----------------

def _launch_command() -> list[str]:
    """当前程序的启动命令。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    entry = Path(sys.argv[0]).resolve()
    return [sys.executable, str(entry)]


def autostart_supported() -> bool:
    return sys.platform in ("darwin", "win32")


def autostart_enabled() -> bool:
    if sys.platform == "darwin":
        return _mac_plist_path().exists()
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as k:
                winreg.QueryValueEx(k, "AutoCopySel")
            return True
        except Exception:
            return False
    return False


def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.liqin.autocopysel.plist"


def set_autostart(enable: bool) -> bool:
    try:
        if sys.platform == "darwin":
            return _mac_set_autostart(enable)
        if sys.platform == "win32":
            return _win_set_autostart(enable)
    except Exception:
        return False
    return False


def _mac_set_autostart(enable: bool) -> bool:
    p = _mac_plist_path()
    if not enable:
        if p.exists():
            subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
            p.unlink()
        return True
    cmd = _launch_command()
    args = "".join(f"        <string>{a}</string>\n" for a in cmd)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.liqin.autocopysel</string>
    <key>ProgramArguments</key>
    <array>
{args}    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>{LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>{LOG_PATH}</string>
</dict>
</plist>
"""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(plist, "utf-8")
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    subprocess.run(["launchctl", "load", str(p)], capture_output=True)
    return True


def _win_set_autostart(enable: bool) -> bool:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
        if enable:
            cmd = subprocess.list2cmdline(_launch_command())
            winreg.SetValueEx(k, "AutoCopySel", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "AutoCopySel")
            except FileNotFoundError:
                pass
    return True
