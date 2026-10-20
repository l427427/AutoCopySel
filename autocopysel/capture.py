"""取词核心：AX 直读 → 失败则模拟复制键（含剪贴板差量判定与回滚）。"""
from __future__ import annotations

import time

from . import clipboard
from .backends import CaptureResult, get_backend
from .config import Config


def capture_once(cfg: Config | None = None, backend=None) -> CaptureResult:
    cfg = cfg or Config.load()
    backend = backend or get_backend()

    app_key, app_name = backend.frontmost_app()
    mode = (cfg.get("capture_mode") or "auto").lower()
    notes: list[str] = []
    text = ""
    source = ""

    # 1) 无障碍接口直读：不改剪贴板，不惊动源应用
    if mode in ("auto", "ax"):
        try:
            got = backend.read_selection_ax()
        except Exception as exc:  # 个别应用会抛异常
            got = None
            notes.append(f"AX 异常: {exc}")
        if got:
            text, source = got, "ax"
        else:
            notes.append("无障碍接口没有读到选区")

    # 2) 兜底：模拟复制快捷键
    if not text and mode in ("auto", "key"):
        if cfg.is_blacklisted(app_key):
            return CaptureResult(
                ok=False,
                app=app_key or "",
                note=f"「{app_name or app_key}」在黑名单中，跳过按键模拟",
            )
        text, source, extra = _copy_via_keystroke(cfg, backend)
        if extra:
            notes.append(extra)

    if not text:
        return CaptureResult(ok=False, app=app_key or "", note="；".join(notes))

    return _finish(cfg, text, source, app_key, notes)


def _copy_via_keystroke(cfg: Config, backend) -> tuple[str, str, str]:
    old_mark = clipboard.mark()
    old_text = clipboard.get_text()

    backend.send_copy()

    deadline = time.monotonic() + max(80, int(cfg.get("timeout_ms") or 450)) / 1000.0
    changed = False
    while time.monotonic() < deadline:
        if clipboard.mark() != old_mark:
            changed = True
            break
        time.sleep(0.008)

    if not changed:
        # 没有新内容进来，把剪贴板还原，避免被清空
        if old_text:
            clipboard.set_text(old_text)
        return "", "", "复制键未产生新内容（可能无选区或在黑名单应用内）"

    new_text = clipboard.get_text()
    if not new_text:
        # 选中的不是文本（图片 / 文件 / 文件夹）。这个非文本剪贴板是**我们自己**
        # 按 Cmd+C 造出来的，属于副作用，必须撤销，把用户原来的内容还回去。
        if old_text:
            clipboard.set_text(old_text)
        return "", "", "选区不是文本，已还原剪贴板"

    return new_text, "key", ""


def _finish(
    cfg: Config, text: str, source: str, app_key: str | None, notes: list[str]
) -> CaptureResult:
    raw = text
    if cfg.get("trim"):
        text = text.strip("\r\n\t ")

    if not text.strip():
        return CaptureResult(ok=False, app=app_key or "", note="选区为空")

    if len(text) < int(cfg.get("min_length") or 1):
        return CaptureResult(
            ok=False, app=app_key or "", note=f"长度 {len(text)} 小于下限"
        )

    max_len = int(cfg.get("max_length") or 0)
    if max_len and len(text) > max_len:
        text = text[:max_len]
        notes.append(f"超长已截断到 {max_len} 字")

    # AX 直读时剪贴板还是旧内容，必须写入；
    # 按键模式下剪贴板已是目标文本，只有 trim/截断改变了内容才回写一次
    if source == "ax" or text != raw:
        clipboard.set_text(text)

    return CaptureResult(
        ok=True, text=text, source=source, app=app_key or "", note="；".join(notes)
    )


def probe() -> dict:
    """自检信息，供命令行 --check 使用。"""
    import sys

    from . import __version__

    backend = get_backend()
    ok, msg = backend.permission_ok()
    app_key, app_name = backend.frontmost_app()
    return {
        "version": __version__,
        "platform": sys.platform,
        "backend": backend.name,
        "permission_ok": ok,
        "permission_message": msg,
        "frontmost_app": app_key,
        "frontmost_name": app_name,
        "clipboard_mark": clipboard.mark(),
        "clipboard_len": len(clipboard.get_text()),
    }
