#!/usr/bin/env python3
"""「左键按住 + 右键单击 → 粘贴」手势的判定测试（纯逻辑，不碰真实鼠标/剪贴板）。

判定规则：右键「按下」和「释放」两个时刻，左键都必须处于按下状态。
  - 左键单独释放 → 不粘贴，仍按原逻辑判断要不要自动复制
  - 右键单独点击 → 不粘贴
  - 右键按下后左键先松 → 不粘贴（半程不算）
  - 右键只按下不释放 → 不粘贴

运行：python tests/test_paste_gesture.py
"""
from __future__ import annotations

import sys
import tempfile
import time
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
    def modifiers_down(self):
        return False


def new_cfg(**over) -> Config:
    cfg = Config({}, Path(tempfile.mkdtemp()) / "c.json")
    cfg.data["paste_gesture"] = True
    cfg.data["paste_debounce_ms"] = 250
    cfg.data["detect_multi_click"] = True
    cfg.data["ignore_with_modifiers"] = True
    cfg.data.update(over)
    return cfg


def make_watcher(cfg):
    """返回 (watcher, counters)。"""
    counters = {"paste": 0, "select": []}

    def on_paste():
        counters["paste"] += 1

    def on_select(x, y, kind):
        counters["select"].append(kind)

    w = MouseSelectionWatcher(on_select, cfg, FakeBackend(), on_paste=on_paste)
    return w, counters


def main() -> int:
    print("[1] 左键按住 + 右键完整单击 → 粘贴一次")
    w, c = make_watcher(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_R, True)
    w._on_click(100, 100, BTN_R, False)
    w._on_click(100, 100, BTN_L, False)
    check("触发粘贴", c["paste"] == 1, f"paste={c['paste']}")
    check("松手不再自动复制", c["select"] == [], f"select={c['select']}")

    print("\n[2] 右键单独点击（左键没按）→ 不粘贴")
    w, c = make_watcher(new_cfg())
    w._on_click(100, 100, BTN_R, True)
    w._on_click(100, 100, BTN_R, False)
    check("不触发粘贴", c["paste"] == 0, f"paste={c['paste']}")

    print("\n[3] 左键单独按下再释放 → 不粘贴，仍走自动复制判定")
    w, c = make_watcher(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    w._on_click(180, 100, BTN_L, False)  # 拖动 80px
    check("不触发粘贴", c["paste"] == 0, f"paste={c['paste']}")
    check("正常触发划词复制", c["select"] == ["drag"], f"select={c['select']}")

    print("\n[4] 左键按住 → 右键按下 → 左键先松 → 右键再松 → 不粘贴")
    w, c = make_watcher(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_R, True)
    w._on_click(100, 100, BTN_L, False)  # 左键先松，右键还没释放
    w._on_click(100, 100, BTN_R, False)
    check("不触发粘贴（右键释放时左键已松开）", c["paste"] == 0, f"paste={c['paste']}")

    print("\n[5] 左键按住 → 右键只按下不释放 → 松开左键 → 不粘贴")
    w, c = make_watcher(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_R, True)
    w._on_click(100, 100, BTN_L, False)
    check("不触发粘贴（右键没走完释放）", c["paste"] == 0, f"paste={c['paste']}")

    print("\n[6] 连续两次右键（间隔 > 去抖）→ 粘贴两次")
    w, c = make_watcher(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    for _ in range(2):
        w._on_click(100, 100, BTN_R, True)
        w._on_click(100, 100, BTN_R, False)
        time.sleep(0.30)  # 大于 paste_debounce_ms
    check("连续触发两次", c["paste"] == 2, f"paste={c['paste']}")

    print("\n[7] 连点两次右键（间隔 < 去抖）→ 只粘贴一次")
    w, c = make_watcher(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_R, True)
    w._on_click(100, 100, BTN_R, False)
    w._on_click(100, 100, BTN_R, True)
    w._on_click(100, 100, BTN_R, False)
    check("去抖只放行一次", c["paste"] == 1, f"paste={c['paste']}")

    print("\n[8] 关掉开关 → 不粘贴，回到纯复制行为")
    w, c = make_watcher(new_cfg(paste_gesture=False))
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_R, True)
    w._on_click(100, 100, BTN_R, False)
    check("开关关闭时不粘贴", c["paste"] == 0, f"paste={c['paste']}")
    w._on_click(180, 100, BTN_L, False)
    check("松手仍会自动复制", c["select"] == ["drag"], f"select={c['select']}")

    print("\n[9] 粘贴后左键继续拖一段再松手 → 不重复复制")
    w, c = make_watcher(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_R, True)
    w._on_click(100, 100, BTN_R, False)
    w._on_click(260, 100, BTN_L, False)  # 松手时位移很大，本会判成 drag
    check("已消费的左键周期不复制", c["select"] == [], f"select={c['select']}")

    print("\n[10] 粘贴后再来一次普通拖选 → 正常工作（状态已复位）")
    w, c = make_watcher(new_cfg())
    w._on_click(100, 100, BTN_L, True)
    w._on_click(100, 100, BTN_R, True)
    w._on_click(100, 100, BTN_R, False)
    w._on_click(100, 100, BTN_L, False)
    w._on_click(300, 300, BTN_L, True)
    w._on_click(380, 300, BTN_L, False)
    check("下一次拖选照常复制", c["select"] == ["drag"], f"select={c['select']}")

    # ---- Service 层：空剪贴板 ----
    print("\n[11] 剪贴板为空时手势不发送粘贴按键")
    from autocopysel.app import Service

    class NullClipboard:
        def __init__(self):
            self.text = ""
            self.mark_n = 0
            self._saved = {}

        def get_text(self):
            return self.text

        def set_text(self, t):
            self.text = t or ""
            self.mark_n += 1
            return True

        def mark(self):
            return self.mark_n

        def has_content(self):
            return bool(self.text)

        def install(self):
            for n in ("get_text", "set_text", "mark", "has_content"):
                self._saved[n] = getattr(real_clipboard, n)
                setattr(real_clipboard, n, getattr(self, n))

        def uninstall(self):
            for n, fn in self._saved.items():
                setattr(real_clipboard, n, fn)

    class PasteBackend(FakeBackend):
        name = "fake"

        def __init__(self):
            self.paste_calls = 0

        def send_paste(self):
            self.paste_calls += 1

    clip = NullClipboard()
    clip.install()
    try:
        svc = Service(new_cfg())
        be = PasteBackend()
        svc.backend = be

        clip.text = ""  # 空剪贴板
        svc._paste_gesture()
        check("空剪贴板不发粘贴键", be.paste_calls == 0, f"send_paste={be.paste_calls}")

        clip.text = "有内容"
        svc._paste_gesture()
        check("有内容时发粘贴键", be.paste_calls == 1, f"send_paste={be.paste_calls}")
        check("统计已累加", svc.stats["paste"] == 1, f"paste={svc.stats['paste']}")

        svc.enabled = False
        svc._paste_gesture()
        check("暂停时不粘贴", be.paste_calls == 1, f"send_paste={be.paste_calls}")
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
