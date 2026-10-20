"""平台后端公共接口。"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass


@dataclass
class CaptureResult:
    ok: bool = False
    text: str = ""
    source: str = ""  # 'ax' = 无障碍接口读取；'key' = 模拟复制快捷键
    app: str = ""  # 取词时前台应用的标识
    note: str = ""

    def __bool__(self) -> bool:
        return self.ok


class Backend:
    name = "base"

    # ---- 权限 ----
    def permission_ok(self) -> tuple[bool, str]:
        return True, ""

    def prompt_permission(self) -> None:
        pass

    def open_permission_settings(self) -> None:
        pass

    # ---- 前台应用 ----
    def frontmost_app(self) -> tuple[str | None, str | None]:
        return None, None

    # ---- 取词 ----
    def read_selection_ax(self) -> str | None:
        """用系统无障碍接口直接读出当前选中文本。不支持时返回 None。"""
        return None

    # ---- 模拟快捷键 ----
    def _modifier_key(self):
        from pynput.keyboard import Key

        return Key.cmd if sys.platform == "darwin" else Key.ctrl

    def _send_hotkey(self, char: str) -> bool:
        """按住平台修饰键敲一下 char。失败时退回手动按下/抬起。"""
        try:
            from pynput.keyboard import Controller

            kb = Controller()
        except Exception:
            return False

        mod = self._modifier_key()
        try:
            with kb.pressed(mod):
                kb.press(char)
                kb.release(char)
            return True
        except Exception:
            pass
        try:
            kb.press(mod)
            time.sleep(0.01)
            kb.press(char)
            kb.release(char)
            return True
        except Exception:
            return False
        finally:
            try:
                kb.release(mod)
            except Exception:
                pass

    def send_copy(self) -> None:
        """模拟系统复制快捷键。"""
        self._send_hotkey("c")

    def send_paste(self) -> None:
        """模拟系统粘贴快捷键。"""
        self._send_hotkey("v")

    # ---- 状态 ----
    def modifiers_down(self) -> bool:
        return False
