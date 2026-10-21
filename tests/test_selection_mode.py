#!/usr/bin/env python3
"""「仅鼠标选中才复制」的判定测试（纯逻辑，不碰真实鼠标/剪贴板）。

要证明两件事：
  1. Shift + 点击/拖动（键盘辅助扩展选区）默认不触发复制；
  2. 键盘选中（Cmd+A / Shift+方向键）压根不会进入这条链路——
     本程序只注册了鼠标监听器，没有任何键盘监听，所以不存在「键盘选中被复制」的路径。

运行：python tests/test_selection_mode.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pynput import mouse

from autocopysel.config import Config
from autocopysel.hook import MouseSelectionWatcher

BTN_L = mouse.Button.left

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


class FakeBackend:
    """modifiers_down / shift_down 完全可控。"""

    def __init__(self, mods: bool = False, shift: bool = False) -> None:
        self.mods = mods
        self.shift = shift

    def modifiers_down(self) -> bool:
        return self.mods

    def shift_down(self) -> bool:
        return self.shift


def new_cfg(**over) -> Config:
    cfg = Config({}, Path(tempfile.mkdtemp()) / "c.json")
    cfg.data.update(
        {
            "detect_multi_click": True,
            "ignore_with_modifiers": True,
            "mouse_only_selection": True,
            "drag_threshold_px": 6,
            "press_min_ms": 80,
        }
    )
    cfg.data.update(over)
    return cfg


def make(cfg, backend=None):
    hits: list[str] = []
    w = MouseSelectionWatcher(
        lambda x, y, kind: hits.append(kind), cfg, backend or FakeBackend()
    )
    return w, hits


def do_drag(w, x1=100, y1=100, x2=220, y2=100) -> None:
    """位移 120px 远大于阈值，无需真的等 80ms。"""
    w._on_click(x1, y1, BTN_L, True)
    w._on_click(x2, y2, BTN_L, False)


def main() -> int:
    print("[1] 纯鼠标拖选（无修饰键）→ 复制")
    w, hits = make(new_cfg())
    do_drag(w)
    check("触发复制", hits == ["drag"], f"hits={hits}")

    print("\n[2] Shift + 拖动（键盘辅助扩展选区）→ 默认不复制")
    w, hits = make(new_cfg(), FakeBackend(shift=True))
    do_drag(w)
    check("被 Shift 拦下", hits == [], f"hits={hits}")

    print("\n[3] Shift + 拖动，但关掉「仅鼠标选中才复制」→ 复制（旧行为）")
    w, hits = make(new_cfg(mouse_only_selection=False), FakeBackend(shift=True))
    do_drag(w)
    check("开关关闭后照常复制", hits == ["drag"], f"hits={hits}")

    print("\n[4] Cmd/Ctrl + 拖动 → 不复制（ignore_with_modifiers）")
    w, hits = make(new_cfg(), FakeBackend(mods=True))
    do_drag(w)
    check("被修饰键拦下", hits == [], f"hits={hits}")

    print("\n[5] Cmd 拖动，但关掉 ignore_with_modifiers → 复制")
    w, hits = make(new_cfg(ignore_with_modifiers=False), FakeBackend(mods=True))
    do_drag(w)
    check("关闭后照常复制", hits == ["drag"], f"hits={hits}")

    print("\n[6] Shift + 双击选词 → 也不复制（多击同样受约束）")
    w, hits = make(new_cfg(), FakeBackend(shift=True))
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_L, False)
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_L, False)
    check("双击被 Shift 拦下", hits == [], f"hits={hits}")

    print("\n[7] 双击（无修饰键）→ 复制，确认没被误伤")
    w, hits = make(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_L, False)
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_L, False)
    check("双击正常复制", hits == ["multi"], f"hits={hits}")

    print("\n[8] 监听器只注册了鼠标回调（键盘选中无路径进入）")
    import inspect

    src = inspect.getsource(MouseSelectionWatcher.start)
    has_mouse = "mouse.Listener" in src
    has_kb = "keyboard.Listener" in src or "keyboard.Listener(" in inspect.getsource(
        MouseSelectionWatcher
    )
    check("注册了 mouse.Listener", has_mouse, "")
    check("没有注册 keyboard.Listener", not has_kb, "键盘事件不会触发任何取词逻辑")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 46}\n{passed}/{len(results)} 通过")
    for name, ok, detail in results:
        if not ok:
            print(f"  失败：{name} {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
