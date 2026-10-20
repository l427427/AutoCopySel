"""健康守护：盯住权限和监听器状态，权限一到位就自动把监听器拉起来。

这一层是「授权了却没反应」这个问题的根治点：
1. 用户在应用启动之后才授权 → 监听器早就死了，必须自动重建；
2. 事件 tap 被系统禁用 / 线程异常退出 → 必须能发现并重启。
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("autocopysel")

POLL_SECONDS = 1.2


class Guardian(threading.Thread):
    def __init__(self, service, on_event):
        super().__init__(name="guardian", daemon=True)
        self.service = service
        self.on_event = on_event
        self._stop_flag = False
        self._state = ""  # 'ok' | 'no-permission' | 'dead'
        self._restart_failures = 0

    def stop(self) -> None:
        self._stop_flag = True

    def run(self) -> None:
        while not self._stop_flag:
            try:
                self._tick()
            except Exception:
                log.debug("guardian tick failed", exc_info=True)
            for _ in range(int(POLL_SECONDS * 10)):
                if self._stop_flag:
                    return
                time.sleep(0.1)

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        service = self.service
        trusted = service.permission_ok()
        alive = service.watcher_alive()

        if not trusted:
            if self._state != "no-permission":
                self._state = "no-permission"
                log.warning("guardian: 缺少辅助功能权限，监听器无法工作")
                self._emit("need_permission")
            return

        if not alive:
            if self._state != "dead":
                self._state = "dead"
                log.warning("guardian: 权限正常但监听器未运行，正在重建")
            if service.restart_watcher():
                self._restart_failures = 0
                self._state = "ok"
                log.info("guardian: 监听器已重建，划词复制恢复")
                self._emit("restored")
            else:
                self._restart_failures += 1
                log.error(
                    "guardian: 监听器重建失败（第 %d 次）：%s",
                    self._restart_failures,
                    service.watcher_dead_reason(),
                )
                if self._restart_failures >= 3:
                    self._state = "dead"
                    self._emit("restart_failed")
                    # 退避，避免疯狂重试
                    for _ in range(30):
                        if self._stop_flag:
                            return
                        time.sleep(0.1)
                    self._restart_failures = 0
            return

        if self._state != "ok":
            log.info("guardian: 状态正常")
            self._state = "ok"

    def _emit(self, event: str) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass
