"""全局鼠标监听：识别「按住拖选」和「双击/三击选词」。

注意：pynput 在 macOS 上如果拿不到辅助功能权限，CGEventTapCreate 会返回 None，
监听线程会**静默退出**（pynput 源码里直接 return，不抛异常、不打日志）。所以这里
必须显式检查启动结果，并由 health.Guardian 负责在权限到位后重建监听器。
"""
from __future__ import annotations

import math
import threading
import time
from typing import Callable

MULTI_CLICK_WINDOW = 0.55  # 秒
MULTI_CLICK_DIST = 8  # 像素


class MouseSelectionWatcher:
    def __init__(self, on_select: Callable[[int, int, str], None], cfg, backend):
        self._on_select = on_select
        self.cfg = cfg
        self.backend = backend

        self._listener = None
        self._press: tuple[float, float, float] | None = None
        self._last_release: float = 0.0
        self._last_pos: tuple[float, float] = (0.0, 0.0)
        self._click_count = 0
        self._lock = threading.Lock()
        self.dead_reason = ""

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

        if button != mouse.Button.left:
            return True

        now = time.monotonic()

        if pressed:
            with self._lock:
                self._press = (float(x), float(y), now)
            return True

        with self._lock:
            press = self._press
            self._press = None
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

        if self.cfg.get("ignore_with_modifiers"):
            try:
                if self.backend.modifiers_down():
                    return True
            except Exception:
                pass

        try:
            self._on_select(int(x), int(y), kind)
        except Exception:
            pass
        return True
