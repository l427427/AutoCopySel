"""Windows 后端：前台进程识别 + Ctrl+C 模拟（配合剪贴板差量判定）。"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from .base import Backend

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class Win32Backend(Backend):
    name = "windows"

    def __init__(self) -> None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u, self.k = u, k

        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short

        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]

    # ---------------- 权限 ----------------
    def permission_ok(self) -> tuple[bool, str]:
        return True, "无需额外授权"

    # ---------------- 前台应用 ----------------
    def _foreground_pid(self) -> int:
        hwnd = self.u.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wintypes.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def frontmost_app(self) -> tuple[str | None, str | None]:
        pid = self._foreground_pid()
        if not pid:
            return None, None
        h = self.k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None, None
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not self.k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return None, None
            full = buf.value
            exe = full.rsplit("\\", 1)[-1]
            return exe.lower(), exe
        finally:
            self.k.CloseHandle(h)

    # ---------------- 取词 ----------------
    def read_selection_ax(self) -> str | None:
        # Windows 没有等价的全局「读选区」接口，统一走 Ctrl+C
        return None

    def send_copy(self) -> None:
        from pynput.keyboard import Controller, Key

        kb = Controller()
        try:
            with kb.pressed(Key.ctrl):
                kb.press("c")
                kb.release("c")
        except Exception:
            try:
                kb.press(Key.ctrl)
                time.sleep(0.01)
                kb.press("c")
                kb.release("c")
                kb.release(Key.ctrl)
            except Exception:
                pass

    # ---------------- 修饰键状态 ----------------
    def modifiers_down(self) -> bool:
        VK = (0x11, 0x12, 0x5B, 0x5C)  # CTRL / ALT / LWIN / RWIN
        for vk in VK:
            if self.u.GetAsyncKeyState(vk) & 0x8000:
                return True
        return False
