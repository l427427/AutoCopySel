"""全局鼠标监听：识别「按住拖选」和「双击/三击选词」。

注意：pynput 在 macOS 上如果拿不到辅助功能权限，CGEventTapCreate 会返回 None，
监听线程会**静默退出**（pynput 源码里直接 return，不抛异常、不打日志）。所以这里
必须显式检查启动结果，并由 health.Guardian 负责在权限到位后重建监听器。
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable

log = logging.getLogger("autocopysel")

MULTI_CLICK_WINDOW = 0.55  # 秒
MULTI_CLICK_DIST = 8  # 像素


class MouseSelectionWatcher:
    def __init__(
        self,
        on_select: Callable[[int, int, str], None],
        cfg,
        backend,
        on_paste: Callable[[], None] | None = None,
    ):
        self._on_select = on_select
        self.cfg = cfg
        self.backend = backend
        self.on_paste = on_paste

        self._listener = None
        self._press: tuple[float, float, float] | None = None
        self._last_release: float = 0.0
        self._last_pos: tuple[float, float] = (0.0, 0.0)
        self._click_count = 0
        self._lock = threading.Lock()
        self.dead_reason = ""

        # 「左键按住 + 右键单击 → 粘贴」用到的状态
        self._left_down = False  # 左键此刻是否按着
        self._right_armed = False  # 这次右键按下是否发生在左键按着期间
        self._suppress_capture = False  # 本个左键周期已用于粘贴，松手不再自动复制
        self._last_paste_at = 0.0

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        """启动监听。返回 False 表示没真正跑起来（通常是缺权限）。"""
        from pynput import mouse

        self.stop()
        self.dead_reason = ""

        try:
            listener = mouse.Listener(on_click=self._on_click)
            listener.daemon = True
            listener.start()
        except Exception as exc:
            self.dead_reason = f"监听器启动失败：{exc}"
            return False

        self._listener = listener
        # 等 _run 里的事件 tap 建好（pynput 建好后会 _mark_ready）
        try:
            listener.wait(1.5)
        except Exception:
            pass

        # tap 创建失败时线程会在 _mark_ready 后立刻退出。这里给一个约 300ms 的
        # 观察窗口：只要线程持续存活，就说明 tap 真的建起来了。
        # （不能用 IS_TRUSTED 判断：它是类默认值 False，和「还没跑起来」无法区分，
        #   会造成慢启动时的假失败。）
        for _ in range(6):
            if not listener.is_alive():
                break
            time.sleep(0.05)

        if not listener.is_alive():
            if getattr(listener, "IS_TRUSTED", None) is False:
                self.dead_reason = "缺少「辅助功能」权限，事件监听器未建立"
            else:
                self.dead_reason = "监听线程已退出（事件 tap 创建失败）"
            return False
        return True

    def is_alive(self) -> bool:
        return self._listener is not None and self._listener.is_alive()

    def restart(self) -> bool:
        return self.start()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    # ---------- 事件 ----------
    def _on_click(self, x, y, button, pressed):
        from pynput import mouse

        # 右键只用于手势判定，而且整段包死：任何异常都不允许影响左键主路径。
        # 注意这里取 button.name 而不是比较 mouse.Button.right —— 万一某个 pynput
        # 后端没有 right 这个枚举成员，写死比较会让每个事件都抛异常，监听直接全废。
        if getattr(button, "name", "") == "right":
            try:
                return self._on_right_click(pressed)
            except Exception:
                log.exception("右键手势处理异常")
                return True

        if button != mouse.Button.left:
            return True

        now = time.monotonic()

        if pressed:
            with self._lock:
                self._left_down = True
                self._press = (float(x), float(y), now)
                self._right_armed = False
                self._suppress_capture = False
            return True

        with self._lock:
            self._left_down = False
            self._right_armed = False
            press = self._press
            self._press = None

            if self._suppress_capture:
                # 这个左键周期已经用于粘贴，松手不再自动复制；同时清多击计数，
                # 免得下一次单击被误判成双击。
                self._suppress_capture = False
                self._click_count = 0
                return True

            if press is None:
                return True
            px, py, pt = press

            dx, dy = x - px, y - py
            dist = math.hypot(dx, dy)
            duration = now - pt

            close_in_time = now - self._last_release <= MULTI_CLICK_WINDOW
            close_in_space = (
                math.hypot(x - self._last_pos[0], y - self._last_pos[1])
                <= MULTI_CLICK_DIST
            )
            if close_in_time and close_in_space:
                self._click_count += 1
            else:
                self._click_count = 1

            self._last_release = now
            self._last_pos = (float(x), float(y))
            clicks = self._click_count

        kind = None
        threshold = float(self.cfg.get("drag_threshold_px") or 6)
        min_ms = float(self.cfg.get("press_min_ms") or 80) / 1000.0
        # 快速甩选（鼠标划一下就松）常常 <80ms，但位移很大，那也是货真价实的选区，
        # 不能因为按得短就丢掉。所以：位移足够大时忽略时长限制。
        fast_px = max(threshold * 4, 24.0)

        if self.cfg.get("detect_multi_click") and clicks >= 2:
            kind = "multi"
        elif dist >= threshold and (duration >= min_ms or dist >= fast_px):
            kind = "drag"

        if kind is None:
            return True

        # 过滤掉「不是纯鼠标选择」的情况：
        #   · 按住 Cmd / Ctrl / Option(Win)  → 多半是组合操作，不是取词
        #   · 按住 Shift                     → 键盘辅助扩展选区（Shift+点击/拖动）
        # 键盘选中（Cmd+A、Shift+方向键）本来就不会到这里，因为本程序只监听鼠标事件。
        skip_for_modifier = False
        try:
            if self.cfg.get("ignore_with_modifiers") and self.backend.modifiers_down():
                skip_for_modifier = True
            elif self.cfg.get("mouse_only_selection") and self.backend.shift_down():
                skip_for_modifier = True
        except Exception:
            pass
        if skip_for_modifier:
            return True

        try:
            self._on_select(int(x), int(y), kind)
        except Exception:
            pass
        return True

    # ---------- 左键按住 + 右键单击 → 粘贴 ----------
    def _on_right_click(self, pressed: bool) -> bool:
        """只有「左键按住期间完成一次右键单击（按下+释放）」才触发粘贴。

        三项必须同时成立：
        1. 右键按下那一刻左键是按着的（记进 _right_armed）；
        2. 右键释放那一刻左键仍然按着（中途松了左键就不算）；
        3. 右键必须走完按下→释放，只按下半程不算。

        因此：左键单独释放不触发，右键单独点击不触发。
        """
        if pressed:
            with self._lock:
                self._right_armed = self._left_down
            return True

        with self._lock:
            armed = self._right_armed
            self._right_armed = False
            left_still_down = self._left_down

        if armed and left_still_down:
            self._fire_paste()
        return True

    def _fire_paste(self) -> None:
        if self.on_paste is None or not self.cfg.get("paste_gesture"):
            return
        now = time.monotonic()
        debounce = float(self.cfg.get("paste_debounce_ms") or 250) / 1000.0
        if now - self._last_paste_at < debounce:
            return
        self._last_paste_at = now
        with self._lock:
            # 本个左键周期改成「粘贴用途」，松手时不再触发自动复制
            self._suppress_capture = True
        try:
            self.on_paste()
        except Exception:
            log.exception("粘贴手势回调异常")
