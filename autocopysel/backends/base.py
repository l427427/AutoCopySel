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

    def permission_state(self) -> dict[str, object]:
        """分别报告每个权限的状态，供界面生成「缺哪个」的提示。

        None = 该平台不适用 / 无法检测。
        """
        return {"accessibility": True, "listen_event": None}

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

    def send_copy(self) -> None:
        """模拟系统复制快捷键。"""
        raise NotImplementedError

    def send_delete(self) -> bool:
        """发送删除键：macOS 的 Delete（退格）= pynput 的 Key.backspace。

        有选区时删除选区，没有选区时删光标前一个字符——所以调用方必须先
        确认真的存在选中内容，绝不能无条件发送。
        """
        try:
            from pynput.keyboard import Controller, Key

            kb = Controller()
            kb.press(Key.backspace)
            kb.release(Key.backspace)
            return True
        except Exception:
            return False

    def send_paste(self) -> None:
        """模拟系统粘贴快捷键（Cmd+V / Ctrl+V）。

        这里给通用实现，各平台不覆盖——粘贴就是一对快捷键，没有平台差异。
        """
        from pynput.keyboard import Controller, Key

        mod = Key.cmd if sys.platform == "darwin" else Key.ctrl
        kb = Controller()
        try:
            with kb.pressed(mod):
                kb.press("v")
                kb.release("v")
            return
        except Exception:
            pass
        # pressed() 上下文在个别环境异常时，退回手动按下/抬起
        try:
            kb.press(mod)
            time.sleep(0.01)
            kb.press("v")
            kb.release("v")
        except Exception:
            pass
        finally:
            try:
                kb.release(mod)
            except Exception:
                pass

    # ---- 状态 ----
    def modifiers_down(self) -> bool:
        """Cmd / Ctrl / Option(Win) 是否被按住。"""
        return False

    def shift_down(self) -> bool:
        """Shift 是否被按住。

        Shift + 点击 / Shift + 拖动 = 用键盘辅助扩展选区，
        不属于「纯鼠标选择」，默认模式（mouse_only_selection）下不触发复制。
        """
        return False
