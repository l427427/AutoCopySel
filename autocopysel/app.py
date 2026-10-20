"""应用主体：监听 → 取词 → 写剪贴板 + 托盘控制。"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque

from . import APP_NAME, __version__, clipboard, icons
from .backends import get_backend
from .capture import capture_once
from .config import APP_DIR, LOG_PATH, Config, autostart_enabled, set_autostart
from .hook import MouseSelectionWatcher

log = logging.getLogger("autocopysel")


def setup_logging(verbose: bool = False) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > 2 * 1024 * 1024:
        try:
            LOG_PATH.replace(LOG_PATH.with_suffix(".log.old"))
        except Exception:
            pass
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


class Service:
    """后台取词服务，所有取词在单一线程串行执行。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.backend = get_backend()
        self.enabled = bool(cfg.get("enabled", True))
        self.history: deque[tuple[str, float]] = deque(
            maxlen=max(1, int(cfg.get("history_size") or 20))
        )
        self.stats = {"ok": 0, "skip": 0, "error": 0, "paste": 0}
        self.last_message = ""

        self._q: queue.Queue = queue.Queue(maxsize=16)
        self._last_emit = 0.0
        self._last_text = ""
        self._last_text_at = 0.0
        self._worker: threading.Thread | None = None
        self._running = False
        self._watcher = MouseSelectionWatcher(
            self._submit, cfg, self.backend, on_paste=self._paste_gesture
        )

        # 由 Application 注入的事件回调：on_result(kind, text)
        self.on_result = None
        self.on_error = None

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        self._running = True
        self._worker = threading.Thread(target=self._loop, name="capture-worker", daemon=True)
        self._worker.start()
        started = self._watcher.start()
        log.info(
            "service started (backend=%s, mode=%s, listener=%s)",
            self.backend.name,
            self.cfg.get("capture_mode"),
            "ok" if started else f"FAILED: {self._watcher.dead_reason}",
        )

    def stop(self) -> None:
        self._running = False
        self._watcher.stop()
        try:
            self._q.put_nowait(None)
        except Exception:
            pass
        log.info("service stopped")

    # ---------------- 监听器状态（health.Guardian 依赖这几个） ----------------
    def permission_ok(self) -> bool:
        try:
            return bool(self.backend.permission_ok()[0])
        except Exception:
            return True

    def watcher_alive(self) -> bool:
        if not self.enabled:
            return True  # 暂停状态不算故障
        return self._watcher.is_alive()

    def restart_watcher(self) -> bool:
        return self._watcher.restart()

    def watcher_dead_reason(self) -> str:
        return self._watcher.dead_reason

    def set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)
        self.cfg.set("enabled", self.enabled)
        log.info("enabled=%s", self.enabled)

    # ---------------- 触发 ----------------
    def _submit(self, x: int, y: int, kind: str) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last_emit < float(self.cfg.get("debounce_ms") or 220) / 1000.0:
            return
        self._last_emit = now
        try:
            self._q.put_nowait((x, y, kind))
        except queue.Full:
            log.debug("queue full, drop event")

    def _paste_gesture(self) -> None:
        """左键按住 + 右键单击：把剪贴板内容粘贴到当前应用。

        直接在鼠标线程里执行，不进取词队列——粘贴是即时动作，
        排队会引入延迟，也会被去抖逻辑吞掉。
        """
        if not self.enabled:
            return

        try:
            has_content = clipboard.has_content()
        except Exception:
            has_content = bool(clipboard.get_text())

        if not has_content:
            # 空剪贴板不发按键：有的应用会把「粘贴空内容」当成删除，
            # 或者弹出「剪贴板为空」的提示，反而打扰用户。
            self.last_message = "左键+右键：剪贴板为空，未粘贴"
            log.info("paste gesture: 剪贴板为空，跳过")
            self._emit("paste_empty")
            return

        try:
            self.backend.send_paste()
        except Exception as exc:
            log.warning("paste gesture failed: %s", exc)
            self.last_message = "左键+右键：粘贴失败"
            return

        self.stats["paste"] += 1
        self.last_message = "左键+右键：已粘贴"
        log.info("paste gesture: 已发送粘贴（累计 %d 次）", self.stats["paste"])
        self._emit("paste")

    def _emit(self, kind: str) -> None:
        if self.on_result is None:
            return
        try:
            self.on_result(kind, None)
        except Exception:
            pass

    def trigger_now(self) -> None:
        """手动触发一次（菜单 / 快捷键）。"""
        try:
            self._q.put_nowait((-1, -1, "manual"))
        except queue.Full:
            pass

    def _loop(self) -> None:
        while self._running:
            job = self._q.get()
            if job is None:
                break
            if not self.enabled:
                continue
            _, _, kind = job
            try:
                delay = float(self.cfg.get("delay_ms") or 130) / 1000.0
                if kind == "manual":
                    delay = min(delay, 0.06)
                if delay > 0:
                    time.sleep(delay)
                self._handle()
            except Exception as exc:  # 不让单次失败拖死线程
                self.stats["error"] += 1
                log.exception("capture failed: %s", exc)
                if self.on_error:
                    try:
                        self.on_error(exc)
                    except Exception:
                        pass

    def _handle(self) -> None:
        res = capture_once(self.cfg, self.backend)
        if not res.ok:
            self.stats["skip"] += 1
            self.last_message = res.note or "未取到内容"
            log.debug("skip: %s", res.note)
            if self.on_result:
                try:
                    self.on_result("skip", res)
                except Exception:
                    pass
            return

        now = time.monotonic()
        if res.text == self._last_text and now - self._last_text_at < 1.2:
            log.debug("duplicate text, ignored")
            return

        self._last_text = res.text
        self._last_text_at = now
        self.history.appendleft((res.text, time.time()))
        self.stats["ok"] += 1
        self.last_message = f"已复制 {len(res.text)} 字（{res.source}）"
        log.info("captured %d chars via %s from %s", len(res.text), res.source, res.app)

        if self.cfg.get("sound"):
            self._beep()

        if self.on_result:
            try:
                self.on_result("ok", res)
            except Exception:
                pass

    @staticmethod
    def _beep() -> None:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["afplay", "/System/Library/Sounds/Tink.aiff"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "win32":
                import winsound

                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass


def permission_buttons(ax_ok: bool, li_ok: bool) -> tuple[str, str]:
    """缺哪个权限就把哪个放第一位（macOS 的默认按钮 = 第一个）。"""
    if ax_ok is False:
        return "打开辅助功能", "打开输入监控"
    if li_ok is False:
        return "打开输入监控", "打开辅助功能"
    return "打开辅助功能", "打开输入监控"


class Application:
    """把 Service 和托盘图标拼起来。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.service = Service(cfg)
        self.icon = None
        self._flash_timer: threading.Timer | None = None
        self._alerted_permission = False
        self._guardian = None
        self._main_loop_running = False
        self._dialog_open = False

        self.service.on_result = self._on_result
        self.service.on_error = self._on_error

    # ---------------- 启动 ----------------
    def run(self, with_tray: bool = True) -> None:
        self._set_macos_accessory()
        self.service.start()
        self._check_permission()
        self._start_guardian()

        if not with_tray:
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
            finally:
                self._stop_guardian()
                self.service.stop()
            return

        import pystray

        from .tray import build_menu

        self.icon = pystray.Icon(
            APP_NAME,
            icons.IDLE,
            f"{APP_NAME} · 运行中",
            menu=build_menu(self),
        )
        try:
            self.icon.run(setup=self._on_tray_ready)
        finally:
            log.info("tray loop exited")
            self._stop_guardian()
            self.service.stop()

    # ---------------- 主线程 UI 调度 ----------------
    def _ui_call(self, fn, *args) -> None:
        """macOS 的 AppKit 只能主线程碰 UI，后台线程一律转投过去。"""
        if sys.platform == "darwin" and self._main_loop_running:
            try:
                from PyObjCTools import AppHelper

                AppHelper.callAfter(fn, *args)
                return
            except Exception:
                pass
        try:
            fn(*args)
        except Exception:
            pass

    # ---------------- 守护 ----------------
    def _start_guardian(self) -> None:
        from .health import Guardian

        self._guardian = Guardian(self.service, lambda e: self._ui_call(self._on_guardian, e))
        self._guardian.start()

    def _stop_guardian(self) -> None:
        if self._guardian is not None:
            self._guardian.stop()
            self._guardian = None

    def _on_guardian(self, event: str) -> None:
        log.info("guardian event: %s", event)
        if event == "need_permission":
            self._notify_permission_missing(force=False)
        elif event == "restored":
            self._sync_icon()
            self._refresh_menu()
        elif event in ("restart_failed", "dead"):
            self._sync_icon()
            self._refresh_menu()

    def _refresh_menu(self) -> None:
        if self.icon is None:
            return
        try:
            self.icon.update_menu()
        except Exception:
            pass

    @staticmethod
    def _set_macos_accessory() -> None:
        """让进程变成「仅菜单栏」程序，源码运行时也不占 Dock。"""
        if sys.platform != "darwin":
            return
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory
        except Exception:
            pass

    def _on_tray_ready(self, icon) -> None:
        try:
            icon.visible = True
        except Exception:
            pass
        self._main_loop_running = True
        self._sync_icon()
        log.info("tray ready")
        if not self.permission_ok:
            self._notify_permission_missing(force=True)

    # ---------------- 权限 ----------------
    def _check_permission(self) -> None:
        ok, msg = self.service.backend.permission_ok()
        log.info("permission: %s (%s)", ok, msg)
        if not ok:
            # 系统自带的「是否允许」弹窗（能勾选进列表，但不会自动生效）
            try:
                self.service.backend.prompt_permission()
            except Exception:
                pass

    def permission_flags(self) -> tuple[bool, bool]:
        """返回 (辅助功能是否可用, 输入监控是否可用)。检测不到的一律按通过处理。"""
        try:
            state = self.service.backend.permission_state()
        except Exception:
            return True, True
        ax = state.get("accessibility")
        li = state.get("listen_event")
        return (ax is not False, li is not False)

    def _notify_permission_missing(self, force: bool = False) -> None:
        """缺权限时说清楚：缺哪个、为什么缺它就不工作、点哪里去补。"""
        if self._alerted_permission and not force:
            return
        if self._dialog_open:
            return
        self._alerted_permission = True
        self._dialog_open = True
        self._sync_icon()
        self._refresh_menu()

        def worker() -> None:
            try:
                from .tray import native_prompt

                ax_ok, li_ok = self.permission_flags()
                lines = [
                    "本程序需要两个系统权限，缺一个都不工作：",
                    "",
                    f"{'✓' if ax_ok else '✗'}  辅助功能 —— 读取选中的文字",
                    f"{'✓' if li_ok else '✗'}  输入监控 —— 监听鼠标（划词就靠它）",
                    "",
                ]
                if ax_ok and not li_ok:
                    lines.append("现在只差「输入监控」，补上就能用。")
                    lines.append("")
                elif li_ok and not ax_ok:
                    lines.append("现在只差「辅助功能」，补上就能用。")
                    lines.append("")
                lines.append("点下面的按钮会直接跳到对应面板，勾选后自动生效，不用重启。")
                lines.append("如果列表里已有同名的旧条目，先按 − 删掉，再用 + 添加本程序。")

                first, second = permission_buttons(ax_ok, li_ok)

                choice = native_prompt(
                    f"{APP_NAME} 需要系统权限",
                    "\n".join(lines),
                    buttons=(first, second, "稍后"),
                    wait=True,
                )
                log.info("permission dialog choice: %s", choice)

                backend = self.service.backend
                if choice == "打开辅助功能":
                    backend.open_permission_settings("accessibility")
                elif choice == "打开输入监控":
                    backend.open_permission_settings("listen_event")
            except Exception:
                log.debug("permission dialog failed", exc_info=True)
            finally:
                self._dialog_open = False

        threading.Thread(target=worker, name="perm-dialog", daemon=True).start()

    @property
    def permission_ok(self) -> bool:
        try:
            return bool(self.service.backend.permission_ok()[0])
        except Exception:
            return True

    # ---------------- 界面动作 ----------------
    def toggle(self) -> None:
        self.service.set_enabled(not self.service.enabled)
        self._sync_icon()

    def toggle_sound(self) -> None:
        self.cfg.set("sound", not bool(self.cfg.get("sound")))

    def toggle_paste_gesture(self) -> None:
        self.cfg.set("paste_gesture", not bool(self.cfg.get("paste_gesture")))
        log.info("paste_gesture=%s", self.cfg.get("paste_gesture"))

    def set_mode(self, mode: str) -> None:
        self.cfg.set("capture_mode", mode)
        log.info("capture_mode=%s", mode)

    def toggle_autostart(self) -> None:
        target = not autostart_enabled()
        if set_autostart(target):
            self.cfg.set("autostart", target)

    def copy_now(self) -> None:
        self.service.trigger_now()

    def paste_text(self, text: str) -> None:
        if clipboard.set_text(text):
            self._flash("ok")

    def open_config(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(APP_DIR)])
            elif sys.platform == "win32":
                os.startfile(str(APP_DIR))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(APP_DIR)])
        except Exception:
            pass

    def open_permission(self, which: str | None = None) -> None:
        """打开权限面板。which 为空时自动跳到真正缺的那个。"""
        try:
            backend = self.service.backend
            if which:
                backend.open_permission_settings(which)
                return
            _ax_ok, li_ok = self.permission_flags()
            backend.open_permission_settings(
                "listen_event" if li_ok is False else "accessibility"
            )
        except Exception:
            pass

    def fix_permission(self, show_result: bool = True) -> None:
        """清掉旧授权记录并打开设置页，让授权重新走一遍。

        这是「授权了却没反应」的唯一可靠修法：ad-hoc 签名的 app，TCC 里存的是
        cdhash，重新打包后旧记录必然失配，界面上的勾选还亮着但实际被拒。
        """
        from . import diagnostics

        ok, msg = diagnostics.reset_permission()
        log.info("reset permission: ok=%s msg=%s", ok, msg)

        if not show_result:
            return

        from .tray import native_prompt

        native_prompt(
            "已重置授权记录" if ok else "重置失败",
            (
                "辅助功能 + 输入监控的旧记录都已清除。\n\n"
                "请在这两个面板里都勾选 AutoCopySel：\n"
                "  · 辅助功能（读选区）\n"
                "  · 输入监控（监听鼠标，最容易漏）\n\n"
                "勾完最多 2 秒自动生效。若列表里已有同名旧条目，先按 − 删掉再重新添加。"
            )
            if ok
            else f"tccutil 返回：{msg}\n\n可以手动执行：tccutil reset Accessibility com.liqin.autocopysel",
            buttons=("好",),
        )

    def run_diagnostics(self) -> None:
        from . import diagnostics
        from .tray import native_prompt

        rows = diagnostics.collect(self.cfg)
        report = diagnostics.format_report(rows)
        tip = diagnostics.advice(rows)
        body = report + ("\n\n" + tip if tip else "")
        log.info("diagnostics:\n%s", body)
        native_prompt(f"{APP_NAME} 诊断", body, buttons=("好",))

    def doctor_text(self) -> str:
        from . import diagnostics

        rows = diagnostics.collect(self.cfg)
        return diagnostics.format_report(rows) + "\n\n" + diagnostics.tail_log(8)

    def about(self) -> str:
        perm = "已授权" if self.permission_ok else "未授权（划词不会生效）"
        listener = "运行中" if self.service.watcher_alive() else "未运行（见诊断）"
        return (
            f"{APP_NAME} {__version__}\n"
            f"平台后端：{self.service.backend.name}\n"
            f"辅助功能：{perm}\n"
            f"事件监听：{listener}\n"
            f"取词方式：{self.cfg.get('capture_mode')}\n"
            f"状态：{'运行中' if self.service.enabled else '已暂停'}\n"
            f"累计复制：{self.service.stats['ok']} 次\n"
            f"累计粘贴：{self.service.stats.get('paste', 0)} 次\n"
            f"配置文件：{self.cfg.path}"
        )

    def quit(self) -> None:
        log.info("quit requested")
        try:
            self.service.stop()
        finally:
            if self.icon is not None:
                self.icon.stop()
            else:
                sys.exit(0)

    # ---------------- 图标状态 ----------------
    def _on_result(self, kind: str, res) -> None:
        # 只有成功才闪绿点。失败的拖拽（拖窗口、拖文件、拖滚动条）太常见，
        # 每次都闪一下会变成噪音。
        if kind in ("ok", "paste"):
            self._flash("ok")
        elif kind == "paste_empty":
            # 手势识别到了但剪贴板是空的，闪黄点提示，否则用户以为手势没生效
            self._flash("warn")

    def _on_error(self, exc) -> None:
        self._flash("warn")

    def _flash(self, state: str) -> None:
        if not self.cfg.get("flash_icon"):
            return
        self._ui_call(self._do_flash, state)

    def _do_flash(self, state: str) -> None:
        if self.icon is None:
            return
        try:
            self.icon.icon = icons.COPIED if state == "ok" else icons.WARN
        except Exception:
            return
        if self._flash_timer is not None:
            self._flash_timer.cancel()
        self._flash_timer = threading.Timer(0.45, lambda: self._ui_call(self._sync_icon))
        self._flash_timer.daemon = True
        self._flash_timer.start()

    def _sync_icon(self) -> None:
        if self.icon is None:
            return
        ax_ok, li_ok = self.permission_flags()
        missing = ax_ok is False or li_ok is False
        try:
            if missing:
                if ax_ok is False and li_ok is False:
                    which = "辅助功能和输入监控"
                elif ax_ok is False:
                    which = "辅助功能"
                else:
                    which = "输入监控"
                self.icon.icon = icons.NEED_PERM
                self.icon.title = f"{APP_NAME} · 缺「{which}」权限，划词不会生效"
            elif self.service.enabled:
                self.icon.icon = icons.IDLE
                self.icon.title = f"{APP_NAME} · 运行中"
            else:
                self.icon.icon = icons.PAUSED
                self.icon.title = f"{APP_NAME} · 已暂停"
        except Exception:
            pass


# ---------------- 单实例 ----------------
# 必须持有这个句柄：一旦它被 GC 回收，文件描述符关闭，flock 立刻释放，
# 单实例保护就失效了（会导致同时跑两个进程、每次划词各发一遍 Cmd+C）。
_LOCK_HANDLE = None


def acquire_single_instance():
    """返回一个持有即代表唯一实例的文件句柄；已有实例时返回 None。"""
    global _LOCK_HANDLE

    if _LOCK_HANDLE is not None:
        return _LOCK_HANDLE

    APP_DIR.mkdir(parents=True, exist_ok=True)
    from .config import LOCK_PATH

    fh = open(LOCK_PATH, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        fh.close()
        return None
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except Exception:
        pass
    _LOCK_HANDLE = fh  # 关键：模块级引用，保证进程存活期间不释放
    return fh
