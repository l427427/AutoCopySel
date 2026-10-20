"""按平台选择后端。"""
from __future__ import annotations

import sys

from .base import Backend, CaptureResult

_BACKEND: Backend | None = None


def get_backend() -> Backend:
    global _BACKEND
    if _BACKEND is None:
        if sys.platform == "darwin":
            from .darwin import DarwinBackend

            _BACKEND = DarwinBackend()
        elif sys.platform == "win32":
            from .win32 import Win32Backend

            _BACKEND = Win32Backend()
        else:
            from .linux import LinuxBackend

            _BACKEND = LinuxBackend()
    return _BACKEND


__all__ = ["Backend", "CaptureResult", "get_backend"]
