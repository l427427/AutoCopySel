#!/usr/bin/env python3
"""「反向拖选 + 右键 = 删除」的判定测试（纯逻辑，不碰真实鼠标/剪贴板）。

要证明：
  1. 左→右拖选 + 右键 → 粘贴，不删除；
  2. 右→左拖选 + 右键 → 删除，不粘贴；
  3. 拖动距离不足阈值 → 按正向（粘贴）处理；
  4. 关闭 reverse_drag_delete 后反向也不再删除；
  5. 没有选中内容时**绝不发送删除键**（否则会删掉光标前一个字符）。

运行：python tests/test_reverse_delete.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pynput import mouse

import autocopysel.clipboard as real_clipboard
from autocopysel.config import Config
from autocopysel.hook import MouseSelectionWatcher

BTN_L = mouse.Button.left
BTN_R = mouse.Button.right

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


class FakeBackend:
    def __init__(self, selection: str | None = None) -> None:
        self.selection = selection
        self.delete_calls = 0

    def modifiers_down(self) -> bool:
        return False

    def shift_down(self) -> bool:
        return False

    def read_selection_ax(self):
        return self.selection

    def send_delete(self) -> bool:
        self.delete_calls += 1
        return True


def new_cfg(**over) -> Config:
    cfg = Config({}, Path(tempfile.mkdtemp()) / "c.json")
    cfg.data.update(
        {
            "detect_multi_click": True,
            "ignore_with_modifiers": True,
            "mouse_only_selection": True,
            "paste_gesture": True,
            "paste_debounce_ms": 250,
            "reverse_drag_delete": True,
            "drag_threshold_px": 6,
            "press_min_ms": 80,
        }
    )
    cfg.data.update(over)
    return cfg


def make(cfg, backend=None):
    counters = {"paste": 0, "delete": 0, "select": []}
    w = MouseSelectionWatcher(
        lambda x, y, kind: counters["select"].append(kind),
        cfg,
        backend or FakeBackend(),
        on_paste=lambda: counters.__setitem__("paste", counters["paste"] + 1),
        on_delete=lambda: counters.__setitem__("delete", counters["delete"] + 1),
    )
    return w, counters


def reverse_drag_then_right_click(w, cfg):
    """右→左拖选，保持左键按下，然后点一下右键。

    方向判定用的是「右键按下位置的 x」减去「左键按下位置的 x」，
    所以这里只需要：左键在右边按下 → 右键在左边按下/释放。
    """
    w._on_click(240, 100, BTN_L, True)   # 左键起点在右：240
    w._on_click(100, 100, BTN_R, True)   # 右键在左：100 → dx = -140，反向
    w._on_click(100, 100, BTN_R, False)


def forward_drag_then_right_click(w):
    w._on_click(100, 100, BTN_L, True)
    w._on_click(240, 100, BTN_R, True)
    w._on_click(240, 100, BTN_R, False)


def main() -> int:
    print("[1] 左→右拖选 + 右键 → 粘贴")
    w, c = make(new_cfg())
    forward_drag_then_right_click(w)
    check("触发粘贴", c["paste"] == 1, f"paste={c['paste']}")
    check("没有删除", c["delete"] == 0, f"delete={c['delete']}")

    print("\n[2] 右→左拖选 + 右键 → 删除")
    w, c = make(new_cfg())
    reverse_drag_then_right_click(w, None)
    check("触发删除", c["delete"] == 1, f"delete={c['delete']}")
    check("没有粘贴", c["paste"] == 0, f"paste={c['paste']}")

    print("\n[3] 位移不足阈值（5px）→ 按正向处理，走粘贴")
    w, c = make(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    w._on_click(95, 100, BTN_R, True)
    w._on_click(95, 100, BTN_R, False)
    check("仍走粘贴", c["paste"] == 1 and c["delete"] == 0, f"paste={c['paste']} delete={c['delete']}")

    print("\n[4] 关闭 reverse_drag_delete → 反向也不删除")
    w, c = make(new_cfg(reverse_drag_delete=False))
    reverse_drag_then_right_click(w, None)
    check("不删除", c["delete"] == 0, f"delete={c['delete']}")
    check("回落到粘贴", c["paste"] == 1, f"paste={c['paste']}")

    print("\n[5] 手势发生后松手，不再重复自动复制")
    w, c = make(new_cfg())
    reverse_drag_then_right_click(w, None)
    w._on_click(100, 100, BTN_L, False)
    check("已消费的周期不复制", c["select"] == [], f"select={c['select']}")

    print("\n[6] Service 层：没有选区时绝不发送删除键")
    from autocopysel.app import Service

    class NullClipboard:
        def __init__(self) -> None:
            self._saved = {}

        def get_text(self):
            return ""

        def set_text(self, t):
            return True

        def mark(self):
            return 0

        def has_content(self):
            return False

        def install(self):
            for n in ("get_text", "set_text", "mark", "has_content"):
                self._saved[n] = getattr(real_clipboard, n)
                setattr(real_clipboard, n, getattr(self, n))

        def uninstall(self):
            for n, fn in self._saved.items():
                setattr(real_clipboard, n, fn)

    clip = NullClipboard()
    clip.install()
    try:
        svc = Service(new_cfg())
        be = FakeBackend(selection=None)  # AX 读不到选区
        svc.backend = be
        svc._delete_gesture()
        check("无选区不发删除键", be.delete_calls == 0, f"send_delete={be.delete_calls}")

        be.selection = "   "  # 只有空白也算没有内容
        svc._delete_gesture()
        check("空白选区也不删", be.delete_calls == 0, f"send_delete={be.delete_calls}")

        be.selection = "一段真的文字"
        svc._delete_gesture()
        check("有选区才删", be.delete_calls == 1, f"send_delete={be.delete_calls}")
        check("统计已累加", svc.stats["delete"] == 1, f"delete={svc.stats['delete']}")

        svc.enabled = False
        svc._delete_gesture()
        check("暂停时不删", be.delete_calls == 1, f"send_delete={be.delete_calls}")
    finally:
        clip.uninstall()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 46}\n{passed}/{len(results)} 通过")
    for name, ok, detail in results:
        if not ok:
            print(f"  失败：{name} {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
