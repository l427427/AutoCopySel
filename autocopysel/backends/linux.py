"""Linux 后端（附带支持，便于开发调试）。X11 用 PRIMARY 选区，Wayland 走 Ctrl+C。"""
from __future__ import annotations

import os
import shutil
import subprocess

from .base import Backend


class LinuxBackend(Backend):
    name = "linux"

    def __init__(self) -> None:
        self.wayland = bool(os.environ.get("WAYLAND_DISPLAY"))

    def permission_ok(self) -> tuple[bool, str]:
        return True, "需要 xclip / wl-clipboard 工具"

    def frontmost_app(self) -> tuple[str | None, str | None]:
        try:
            out = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowclassname"],
                capture_output=True,
                timeout=1,
            ).stdout.decode("utf-8", "replace").strip()
            return (out.lower() or None), (out or None)
        except Exception:
            return None, None

    def read_selection_ax(self) -> str | None:
        # X11 直接读 PRIMARY 选区，就是「划词即复制」的原生语义
        if self.wayland:
            return None
        if not shutil.which("xclip"):
            return None
        try:
            out = subprocess.run(
                ["xclip", "-selection", "primary", "-o"],
                capture_output=True,
                timeout=1,
            ).stdout.decode("utf-8", "replace")
            return out or None
        except Exception:
            return None

    def send_copy(self) -> None:
        from pynput.keyboard import Controller, Key

        kb = Controller()
        with kb.pressed(Key.ctrl):
            kb.press("c")
            kb.release("c")

    def modifiers_down(self) -> bool:
        return False
