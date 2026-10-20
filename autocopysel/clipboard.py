"""跨平台剪贴板：读写纯文本 + 变化检测。不依赖第三方包。"""
from __future__ import annotations

import subprocess
import sys
import time

_IMPL = None


def _impl():
    global _IMPL
    if _IMPL is None:
        if sys.platform == "darwin":
            _IMPL = _MacClipboard()
        elif sys.platform == "win32":
            _IMPL = _WinClipboard()
        else:
            _IMPL = _XClipClipboard()
    return _IMPL


def get_text() -> str:
    try:
        return _impl().get() or ""
    except Exception:
        return ""


def set_text(text: str) -> bool:
    try:
        return bool(_impl().set(text or ""))
    except Exception:
        return False


def mark() -> int:
    """返回剪贴板版本号，用于判断是否发生变化。"""
    try:
        return int(_impl().mark())
    except Exception:
        return 0


def has_content() -> bool:
    """剪贴板里是否有任何内容（文本、图片、文件都算）。"""
    try:
        return bool(_impl().has_content())
    except Exception:
        return bool(get_text())


# ---------------------------------------------------------------- macOS
class _MacClipboard:
    """优先用 AppKit（能拿 changeCount），失败退回 pbcopy/pbpaste。"""

    def __init__(self):
        self._pb = None
        try:
            from AppKit import NSPasteboard

            self._pb = NSPasteboard.generalPasteboard()
            self._type = "public.utf8-plain-text"
            self._default = "Apple Public UTF8 Plain Text pasteboard type"
        except Exception:
            self._pb = None

    def mark(self) -> int:
        if self._pb is not None:
            try:
                return int(self._pb.changeCount())
            except Exception:
                pass
        return int(time.monotonic() * 1000)

    def has_content(self) -> bool:
        if self._pb is not None:
            try:
                return len(self._pb.types() or []) > 0
            except Exception:
                pass
        return bool(self.get())

    def get(self) -> str:
        if self._pb is not None:
            try:
                val = self._pb.stringForType_(self._type)
                return str(val) if val is not None else ""
            except Exception:
                pass
        try:
            p = subprocess.run(["pbpaste"], capture_output=True, timeout=3)
            return p.stdout.decode("utf-8", "replace")
        except Exception:
            return ""

    def set(self, text: str) -> bool:
        if self._pb is not None:
            try:
                self._pb.clearContents()
                # 同时声明多种纯文本类型：老应用只认 NSStringPboardType，
                # 新应用认 public.utf8-plain-text。只写一种会导致个别程序粘贴不出东西。
                wrote = False
                for ptype in ("public.utf8-plain-text", "NSStringPboardType"):
                    try:
                        if self._pb.setString_forType_(text, ptype):
                            wrote = True
                    except Exception:
                        continue
                if wrote:
                    return True
            except Exception:
                pass
        try:
            p = subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=3)
            return p.returncode == 0
        except Exception:
            return False


# ---------------------------------------------------------------- Windows
class _WinClipboard:
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        u, k = self.user32, self.kernel32
        u.OpenClipboard.argtypes = [wintypes.HWND]
        u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.restype = wintypes.BOOL
        u.GetClipboardData.argtypes = [wintypes.UINT]
        u.GetClipboardData.restype = wintypes.HANDLE
        u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u.SetClipboardData.restype = wintypes.HANDLE
        u.GetClipboardSequenceNumber.restype = wintypes.DWORD
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = wintypes.HGLOBAL
        k.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k.GlobalLock.restype = wintypes.LPVOID
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k.GlobalUnlock.restype = wintypes.BOOL
        k.GlobalFree.argtypes = [wintypes.HGLOBAL]

    def mark(self) -> int:
        return int(self.user32.GetClipboardSequenceNumber())

    def has_content(self) -> bool:
        self.user32.CountClipboardFormats.restype = self.wintypes.INT
        return int(self.user32.CountClipboardFormats()) > 0

    def _open(self, retries: int = 12) -> bool:
        for _ in range(retries):
            if self.user32.OpenClipboard(None):
                return True
            time.sleep(0.015)
        return False

    def get(self) -> str:
        if not self._open():
            return ""
        try:
            h = self.user32.GetClipboardData(self.CF_UNICODETEXT)
            if not h:
                return ""
            ptr = self.kernel32.GlobalLock(h)
            if not ptr:
                return ""
            try:
                return self.ctypes.wstring_at(ptr)
            finally:
                self.kernel32.GlobalUnlock(h)
        finally:
            self.user32.CloseClipboard()

    def set(self, text: str) -> bool:
        data = text.encode("utf-16-le") + b"\x00\x00"
        if not self._open():
            return False
        try:
            self.user32.EmptyClipboard()
            h = self.kernel32.GlobalAlloc(self.GMEM_MOVEABLE, len(data))
            if not h:
                return False
            ptr = self.kernel32.GlobalLock(h)
            if not ptr:
                self.kernel32.GlobalFree(h)
                return False
            try:
                self.ctypes.memmove(ptr, data, len(data))
            finally:
                self.kernel32.GlobalUnlock(h)
            if not self.user32.SetClipboardData(self.CF_UNICODETEXT, h):
                self.kernel32.GlobalFree(h)
                return False
            return True
        finally:
            self.user32.CloseClipboard()


# ---------------------------------------------------------------- Linux（附带支持）
class _XClipClipboard:
    def mark(self) -> int:
        return int(time.monotonic() * 1000)

    def has_content(self) -> bool:
        return bool(self.get())

    def _run(self, cmd: list[str], text: str | None = None) -> str:
        try:
            p = subprocess.run(
                cmd,
                input=text.encode("utf-8") if text is not None else None,
                capture_output=True,
                timeout=3,
            )
            return p.stdout.decode("utf-8", "replace")
        except Exception:
            return ""

    def get(self) -> str:
        import os

        if os.environ.get("WAYLAND_DISPLAY"):
            return self._run(["wl-paste", "--no-newline"])
        return self._run(["xclip", "-selection", "clipboard", "-o"])

    def set(self, text: str) -> bool:
        import os

        if os.environ.get("WAYLAND_DISPLAY"):
            return bool(self._run(["wl-copy"], text))
        self._run(["xclip", "-selection", "clipboard"], text)
        return True
