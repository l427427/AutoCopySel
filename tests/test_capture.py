#!/usr/bin/env python3
"""取词逻辑单元测试（不依赖 GUI、不抢焦点、**不碰真实剪贴板**）。

重点验证「静默复制」的两个硬要求：
  1. AX 能直接读到时，绝不发送任何按键、绝不惊动源应用；
  2. 黑名单应用绝不发按键。

剪贴板在这里被整个替换成内存假实现（FakeClipboard），跑完会核对真实剪贴板
有没有被动过——测试绝不能污染用户正在用的剪贴板内容。

运行：python tests/test_capture.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import autocopysel.clipboard as real_clipboard
from autocopysel.capture import capture_once
from autocopysel.config import Config

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


class FakeClipboard:
    """内存剪贴板，接口与 autocopysel.clipboard 一致。"""

    def __init__(self, initial: str = "") -> None:
        self._text = initial
        self._mark = 0
        self._saved: dict[str, object] = {}

    # --- 与真实模块同名的方法 ---
    def get_text(self) -> str:
        return self._text

    def set_text(self, text: str) -> bool:
        self._text = text or ""
        self._mark += 1
        return True

    def mark(self) -> int:
        return self._mark

    def has_content(self) -> bool:
        return bool(self._text)

    # --- 安装 / 还原 ---
    def install(self) -> None:
        for name in ("get_text", "set_text", "mark", "has_content"):
            self._saved[name] = getattr(real_clipboard, name)
            setattr(real_clipboard, name, getattr(self, name))

    def uninstall(self) -> None:
        for name, fn in self._saved.items():
            setattr(real_clipboard, name, fn)
        self._saved.clear()


CLIP = FakeClipboard()


class FakeBackend:
    """可控后端：能指定 AX 返回值、前台应用、并统计按键次数。"""

    name = "fake"

    def __init__(self, ax_text=None, app=("com.example.app", "Example"), copy_payload="FAKE-COPY"):
        self.ax_text = ax_text
        self.app = app
        self.copy_payload = copy_payload
        self.copy_calls = 0

    def permission_ok(self):
        return True, ""

    def frontmost_app(self):
        return self.app

    def read_selection_ax(self):
        return self.ax_text

    def send_copy(self):
        self.copy_calls += 1
        CLIP.set_text(self.copy_payload)

    def modifiers_down(self):
        return False


def new_cfg(**over) -> Config:
    cfg = Config({"app_blacklist": ["com.apple.terminal"]}, Path(tempfile.mkdtemp()) / "c.json")
    cfg.data["capture_mode"] = "auto"
    cfg.data.update(over)
    return cfg


def main() -> int:
    # 记住真实剪贴板，跑完核对
    try:
        real_before = real_clipboard.get_text()
    except Exception:
        real_before = None

    CLIP.install()
    try:
        print("[1] AX 直接命中时必须是静默的（不发按键）")
        cfg = new_cfg()
        b = FakeBackend(ax_text="直接读到的文本")
        r = capture_once(cfg, b)
        check("走 AX 通道", r.ok and r.source == "ax", f"source={r.source}")
        check("未发送任何按键", b.copy_calls == 0, f"send_copy 调用 {b.copy_calls} 次")
        check("写入的是假剪贴板", CLIP.get_text() == "直接读到的文本", repr(CLIP.get_text()[:20]))

        print("\n[2] AX 读不到时退回按键")
        b2 = FakeBackend(ax_text=None, copy_payload="按键拿到的文本")
        r2 = capture_once(cfg, b2)
        check("走了按键通道", r2.ok and r2.source == "key", f"source={r2.source}")
        check("按键只发一次", b2.copy_calls == 1, f"{b2.copy_calls} 次")

        print("\n[3] 黑名单应用绝不发按键")
        b3 = FakeBackend(ax_text=None, app=("com.apple.Terminal", "Terminal"))
        r3 = capture_once(cfg, b3)
        check("黑名单被拦住", (not r3.ok) and b3.copy_calls == 0, f"note={r3.note!r}")
        check("提示里说明了黑名单", "黑名单" in (r3.note or ""), r3.note or "")

        print("\n[4] trim 去首尾空白与换行")
        b4 = FakeBackend(ax_text="  hello world \n\n")
        r4 = capture_once(new_cfg(trim=True), b4)
        check("文本被 trim", r4.ok and r4.text == "hello world", repr(r4.text))
        check("剪贴板也是 trim 后的", CLIP.get_text() == "hello world", repr(CLIP.get_text()))

        print("\n[5] 关闭 trim 时原样保留")
        b5 = FakeBackend(ax_text="  keep \n")
        r5 = capture_once(new_cfg(trim=False), b5)
        check("保留原始空白", r5.ok and r5.text == "  keep \n", repr(r5.text))

        print("\n[6] 长度限制")
        b6 = FakeBackend(ax_text="0123456789")
        r6 = capture_once(new_cfg(max_length=5), b6)
        check("超长截断到 5", r6.ok and r6.text == "01234", repr(r6.text))
        b6b = FakeBackend(ax_text="ab")
        r6b = capture_once(new_cfg(min_length=5), b6b)
        check("过短被拒", not r6b.ok, f"note={r6b.note!r}")

        print("\n[7] 纯空白 / 空选区不写剪贴板")
        CLIP.set_text("KEEP")
        b7 = FakeBackend(ax_text="   \n\t ")
        r7 = capture_once(new_cfg(), b7)
        check("空白选区被拒", not r7.ok, f"note={r7.note!r}")
        check("剪贴板未被污染", CLIP.get_text() == "KEEP", repr(CLIP.get_text()[:16]))

        print("\n[8] 非文本选区（图片/文件）必须还原剪贴板")
        CLIP.set_text("ORIGINAL-KEEP-ME")
        b8 = FakeBackend(ax_text=None, copy_payload="")  # 模拟复制到的是非文本内容
        r8 = capture_once(new_cfg(capture_mode="key"), b8)
        check(
            "非文本复制后剪贴板被还原",
            (not r8.ok) and CLIP.get_text() == "ORIGINAL-KEEP-ME",
            f"note={r8.note!r} 剪贴板={CLIP.get_text()!r}",
        )
        CLIP.set_text("ORIGINAL-KEEP-ME")
        b8b = FakeBackend(ax_text=None, copy_payload="正常文本")
        r8b = capture_once(new_cfg(capture_mode="key"), b8b)
        check(
            "文本复制正常采纳",
            r8b.ok and CLIP.get_text() == "正常文本",
            f"text={r8b.text!r}",
        )

        print("\n[9] 单实例句柄复用")
        # 用独立 AUTOCOPYSEL_HOME 起子进程测，避免和本机正在运行的实例抢锁
        import os
        import subprocess

        root = str(Path(__file__).resolve().parents[1])
        env = dict(os.environ, AUTOCOPYSEL_HOME=tempfile.mkdtemp())
        probe = (
            "from autocopysel.app import acquire_single_instance as a;"
            "h1=a(); h2=a();"
            "print('OK' if (h1 is not None and h1 is h2) else 'NO')"
        )
        rp = subprocess.run(
            [sys.executable, "-c", probe], cwd=root, env=env, capture_output=True, timeout=30
        )
        out = (rp.stdout or b"").decode("utf-8", "replace").strip()
        check("同进程复用同一句柄", "OK" in out, f"probe={out!r}")
    finally:
        CLIP.uninstall()

    # 关键自证：真实剪贴板一个字都没动
    try:
        real_after = real_clipboard.get_text()
    except Exception:
        real_after = None
    check(
        "全程未触碰真实剪贴板",
        real_after == real_before,
        f"测试前 {len(real_before or '')} 字 / 测试后 {len(real_after or '')} 字",
    )

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 46}\n{passed}/{len(results)} 通过")
    for name, ok, detail in results:
        if not ok:
            print(f"  失败：{name} {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
