"""macOS 后端：Accessibility API 读选区 + Cmd+C 兜底。"""
from __future__ import annotations

import subprocess
import time

from .base import Backend

_MAX_DEPTH = 7
_MAX_NODES = 160
_SEARCH_BUDGET = 0.18  # 秒；BFS 找选区的时间上限，超了就退回按键模拟
_AX_TIMEOUT = 0.25  # 秒；避免个别应用把 AX 查询卡死


class DarwinBackend(Backend):
    name = "macos"

    def __init__(self) -> None:
        import ApplicationServices as AS

        self.AS = AS
        try:
            AS.AXUIElementSetMessagingTimeout(AS.AXUIElementCreateSystemWide(), _AX_TIMEOUT)
        except Exception:
            pass

    # ---------------- 权限 ----------------
    # macOS 上这个程序需要两个权限，缺一个都不工作：
    #   辅助功能 Accessibility  → 读选区（AXSelectedText）
    #   输入监控 Input Monitoring → 建 CGEventTap 监听全局鼠标
    # 早期版本只检查了前者，导致「勾了辅助功能却还是没反应」。
    def listen_event_ok(self) -> bool | None:
        """输入监控权限。None 表示当前系统取不到该 API（按通过处理，不误报）。"""
        try:
            import Quartz

            fn = getattr(Quartz, "CGPreflightListenEventAccess", None)
            if fn is None:
                return None
            return bool(fn())
        except Exception:
            return None

    def permission_state(self) -> dict[str, object]:
        try:
            ax = bool(self.AS.AXIsProcessTrusted())
        except Exception:
            ax = None
        return {"accessibility": ax, "listen_event": self.listen_event_ok()}

    def permission_ok(self) -> tuple[bool, str]:
        try:
            ax_ok = bool(self.AS.AXIsProcessTrusted())
        except Exception:
            return False, "无法检测无障碍权限"

        li_ok = self.listen_event_ok()

        if ax_ok and li_ok is not False:
            return True, "辅助功能已授权"
        if not ax_ok and li_ok is False:
            return False, "缺少「辅助功能」和「输入监控」两个权限"
        if not ax_ok:
            return False, "缺少「辅助功能（Accessibility）」权限（读选区要用）"
        return False, "缺少「输入监控（Input Monitoring）」权限（监听鼠标要用）"

    def prompt_permission(self) -> None:
        """同时触发两个权限的系统请求弹窗，缺哪个都会弹。"""
        try:
            self.AS.AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
        except Exception:
            pass
        try:
            import Quartz

            fn = getattr(Quartz, "CGRequestListenEventAccess", None)
            if fn is not None and self.listen_event_ok() is False:
                fn()
        except Exception:
            pass

    def open_permission_settings(self, which: str = "accessibility") -> None:
        pane = "Privacy_ListenEvent" if which == "listen_event" else "Privacy_Accessibility"
        url = f"x-apple.systempreferences:com.apple.preference.security?{pane}"
        try:
            import shutil

            subprocess.Popen([shutil.which("open") or "/usr/bin/open", url])
        except Exception:
            pass

    # ---------------- 前台应用 ----------------
    def frontmost_app(self) -> tuple[str | None, str | None]:
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None, None
            return (
                str(app.bundleIdentifier() or ""),
                str(app.localizedName() or ""),
            )
        except Exception:
            return None, None

    # ---------------- AX 读选区 ----------------
    def _attr(self, el, name: str):
        try:
            err, value = self.AS.AXUIElementCopyAttributeValue(el, name, None)
            if err == 0 and value is not None:
                return value
        except Exception:
            pass
        return None

    def read_selection_ax(self) -> str | None:
        for root, depth, budget in self._candidate_roots():
            text = self._text_of(root)
            if text:
                return text
            text = self._search_children(root, depth, budget)
            if text:
                return text
        return None

    def _candidate_roots(self) -> list[tuple]:
        """(根元素, 搜索深度, 时间预算) 候选列表。

        system-wide 元素的 AXFocusedApplication 在部分机器/应用上直接返回
        kAXErrorCannotComplete(-25204)，所以必须再用「前台 PID → 应用元素」兜一层。
        """
        roots: list[tuple] = []
        try:
            system = self.AS.AXUIElementCreateSystemWide()
            focused_app = self._attr(system, "AXFocusedApplication")
            if focused_app is not None:
                self._set_timeout(focused_app)
                el = self._attr(focused_app, "AXFocusedUIElement")
                if el is not None:
                    self._set_timeout(el)
                    roots.append((el, _MAX_DEPTH, _SEARCH_BUDGET))
                roots.append((focused_app, 3, 0.08))
        except Exception:
            pass

        try:
            pid = self._frontmost_pid()
            if pid:
                app_el = self.AS.AXUIElementCreateApplication(pid)
                self._set_timeout(app_el)
                el = self._attr(app_el, "AXFocusedUIElement")
                if el is not None:
                    self._set_timeout(el)
                    roots.append((el, _MAX_DEPTH, _SEARCH_BUDGET))
                roots.append((app_el, 4, 0.08))
        except Exception:
            pass
        return roots

    def _frontmost_pid(self) -> int:
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return int(app.processIdentifier()) if app is not None else 0
        except Exception:
            return 0

    def _set_timeout(self, el) -> None:
        try:
            self.AS.AXUIElementSetMessagingTimeout(el, _AX_TIMEOUT)
        except Exception:
            pass

    def _text_of(self, el) -> str | None:
        val = self._attr(el, "AXSelectedText")
        if isinstance(val, str) and val.strip():
            return val
        return None

    def _search_children(self, root, max_depth: int = _MAX_DEPTH, budget: float = _SEARCH_BUDGET):
        """广度优先找带选区的子元素（浏览器 / WPS / Electron 的文本视图常在深层）。"""
        deadline = time.monotonic() + budget
        queue = [(root, 0)]
        seen = 0
        while queue and seen < _MAX_NODES:
            if time.monotonic() > deadline:
                break
            el, depth = queue.pop(0)
            seen += 1
            if depth >= max_depth:
                continue
            children = self._attr(el, "AXChildren")
            if not children:
                continue
            try:
                items = list(children)
            except Exception:
                continue
            for child in items:
                found = self._text_of(child)
                if found:
                    return found
                if len(queue) < _MAX_NODES:
                    queue.append((child, depth + 1))
        return None

    # ---------------- 模拟复制 ----------------
    def send_copy(self) -> None:
        from pynput.keyboard import Controller, Key

        kb = Controller()
        try:
            with kb.pressed(Key.cmd):
                kb.press("c")
                kb.release("c")
        except Exception:
            # 某些环境 pressed() 上下文行为异常，退回手动按下/抬起
            try:
                kb.press(Key.cmd)
                time.sleep(0.01)
                kb.press("c")
                kb.release("c")
                kb.release(Key.cmd)
            except Exception:
                pass

    # ---------------- 修饰键状态 ----------------
    def modifiers_down(self) -> bool:
        """是否按住了 Cmd / Ctrl / Option。

        三个来源取或：会话状态、HID 状态、AppKit 实时状态。任何一个报有就算有，
        避免某个来源在某些时机读到 0 导致「按住 Cmd 拖动」被误判成普通划选。
        """
        try:
            import Quartz

            mask = (
                Quartz.kCGEventFlagMaskCommand
                | Quartz.kCGEventFlagMaskControl
                | Quartz.kCGEventFlagMaskAlternate
            )
            for state in (
                Quartz.kCGEventSourceStateCombinedSessionState,
                Quartz.kCGEventSourceStateHIDSystemState,
            ):
                try:
                    if Quartz.CGEventSourceFlagsState(state) & mask:
                        return True
                except Exception:
                    continue
            try:
                from AppKit import NSEvent

                if int(NSEvent.modifierFlags()) & mask:
                    return True
            except Exception:
                pass
        except Exception:
            pass
        return False
