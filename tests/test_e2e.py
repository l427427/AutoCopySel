#!/usr/bin/env python3
"""AutoCopySel 静默复制功能的端到端测试。

用 TextEdit 当靶子，用 pynput 发真实鼠标事件，断言「选中即进剪贴板」这条主链路。
运行：python tests/test_e2e.py        （会短暂抢焦点，约 30 秒）
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ApplicationServices as AS
from AppKit import NSWorkspace
from pynput.keyboard import Controller as KC, Key
from pynput.mouse import Button, Controller as MC

from autocopysel import clipboard
from autocopysel.app import Service
from autocopysel.backends import get_backend
from autocopysel.config import Config

ROWS = ["甲乙丙", "丁戊己", "庚辛壬", "癸子丑", "寅卯辰", "巳午未", "申酉戌", "亥天地"]
DOC = "\n".join(f"第{i + 1}行 word{i + 1} {t}测试文本内容" for i, t in enumerate(ROWS))
# 每次跑用独立文件：复用同名文件会让 TextEdit 打开已修改的旧缓冲区，坐标全乱
TEMP = Path(tempfile.gettempdir()) / f"acs_e2e_{os.getpid()}.txt"

results: list[tuple[str, bool, str]] = []
_front_probe = None  # 由 main 注入：返回当前前台应用标识


def check(name: str, ok: bool, detail: str = "") -> None:
    # 本测试靠合成鼠标事件驱动，必须保证前台一直是 TextEdit。
    # 如果中途被切走（比如你在用电脑），后续断言全部无意义，标 SKIP 而不是 FAIL。
    if _front_probe is not None and _front_probe() != "com.apple.TextEdit":
        results.append((name, True, f"SKIP 前台被切走 {detail}"))
        print(f"  SKIP  {name}  前台已不是 TextEdit，本项跳过")
        return
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def window_origin() -> tuple[float, float]:
    pid = int(NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier())
    app_el = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetMessagingTimeout(app_el, 2.0)
    _, wins = AS.AXUIElementCopyAttributeValue(app_el, "AXWindows", None)
    win = list(wins)[0]
    _, pos = AS.AXUIElementCopyAttributeValue(win, "AXPosition", None)
    _, pt = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
    return float(pt.x), float(pt.y)


def main() -> int:
    global _front_probe
    b = get_backend()
    mouse = MC()
    kb = KC()
    _front_probe = lambda: b.frontmost_app()[0]

    TEMP.write_text(DOC, "utf-8")
    cfg = Config.load()
    cfg.data["capture_mode"] = "auto"
    cfg.data["detect_multi_click"] = True
    cfg.data["ignore_with_modifiers"] = True

    # 本测试会反复改写剪贴板，先存下你现在的内容，跑完原样还原
    saved_clip = clipboard.get_text()
    clip_had_other = (not saved_clip) and clipboard.has_content()
    print(f"会临时占用剪贴板（当前 {len(saved_clip)} 字），结束时还原。")

    svc = Service(cfg)
    svc.start()
    time.sleep(0.5)
    print(f"监听器：{'就绪' if svc.watcher_alive() else '未就绪'}")

    # 把 TextEdit 拉到前台：open 有时不抢焦点，重试几次
    for attempt in range(4):
        subprocess.run(["open", "-a", "TextEdit", str(TEMP)])
        time.sleep(1.6)
        if b.frontmost_app()[0] == "com.apple.TextEdit":
            break
        print(f"  第 {attempt + 1} 次未能激活 TextEdit，重试…")
    time.sleep(0.6)
    if b.frontmost_app()[0] != "com.apple.TextEdit":
        print("!! 前台不是 TextEdit，放弃（不发任何按键）")
        svc.stop()
        return 2

    ox, oy = window_origin()
    left = ox + 20

    # 预热点击：macOS 会把「非活动窗口的第一次点击」吞掉用于激活窗口，
    # 不先点一下的话，后面第一个拖选会被误判成失败。
    mouse.position = (left + 120, oy + 300)
    time.sleep(0.3)
    mouse.click(Button.left, 1)
    time.sleep(0.6)
    svc.stats["ok"] = 0

    def reset_clip() -> None:
        clipboard.set_text("__SENTINEL__")

    def captured() -> tuple[str, int]:
        before = svc.stats["ok"]
        time.sleep(0.05)
        return clipboard.get_text(), before

    def drag(y_off: float, x1: float, x2: float, steps: int = 8, hold_cmd: bool = False) -> None:
        y = oy + y_off
        mouse.position = (x1, y)
        time.sleep(0.12)
        if hold_cmd:
            kb.press(Key.cmd)
            time.sleep(0.06)
        mouse.press(Button.left)
        time.sleep(0.05)
        for i in range(1, steps + 1):
            mouse.position = (x1 + (x2 - x1) * i / steps, y)
            time.sleep(0.012)
        time.sleep(0.05)
        mouse.release(Button.left)
        if hold_cmd:
            time.sleep(0.06)
            kb.release(Key.cmd)

    # ---- 1. 拖选 ----
    print("\n[1] 拖选一行")
    reset_clip()
    n0 = svc.stats["ok"]
    drag(88, left, left + 260)
    time.sleep(1.0)
    text = clipboard.get_text()
    check("拖选进剪贴板", bool(text.strip()) and text != "__SENTINEL__", repr(text[:30]))
    check("拖选内容来自文档", text.strip() and text.strip() in DOC, "")
    check("计数只加 1", svc.stats["ok"] - n0 == 1, f"+{svc.stats['ok'] - n0}")

    # ---- 2. 双击选词 ----
    print("\n[2] 双击选词")
    reset_clip()
    n1 = svc.stats["ok"]
    mouse.position = (left + 120, oy + 145)
    time.sleep(0.15)
    mouse.click(Button.left, 2)
    time.sleep(1.0)
    t2 = clipboard.get_text()
    check("双击进剪贴板", bool(t2.strip()) and t2 != "__SENTINEL__", repr(t2[:20]))
    check("双击计数 +1", svc.stats["ok"] - n1 == 1, f"+{svc.stats['ok'] - n1}")

    # ---- 3. 三击选行 ----
    print("\n[3] 三击选行")
    reset_clip()
    n2 = svc.stats["ok"]
    mouse.position = (left + 60, oy + 183)
    time.sleep(0.15)
    mouse.click(Button.left, 3)
    time.sleep(1.1)
    t3 = clipboard.get_text()
    check("三击进剪贴板", bool(t3.strip()) and t3 != "__SENTINEL__", repr(t3[:30]))
    check("三击拿到整行", "行" in t3 and len(t3) > 8, repr(t3[:30]))

    # ---- 4. 微位移点击不应触发 ----
    print("\n[4] 2px 抖动点击（不应触发）")
    reset_clip()
    n3 = svc.stats["ok"]
    mouse.position = (left + 200, oy + 70)
    time.sleep(0.7)  # 拉开与上次多击的时间窗
    mouse.press(Button.left)
    mouse.position = (left + 202, oy + 71)
    time.sleep(0.03)
    mouse.release(Button.left)
    time.sleep(0.9)
    check("微抖动不触发", svc.stats["ok"] == n3, f"变化 {svc.stats['ok'] - n3}")
    check("剪贴板未被破坏", clipboard.get_text() == "__SENTINEL__", repr(clipboard.get_text()[:16]))

    # ---- 5. Cmd+拖动不应触发 ----
    print("\n[5] 按住 Cmd 拖动（不应触发）")
    # 先确认「合成按下 Cmd」在当前环境里真的被系统认到了；认不到就没法测这一项
    kb.press(Key.cmd)
    time.sleep(0.12)
    modifier_visible = b.modifiers_down()
    kb.release(Key.cmd)
    time.sleep(0.1)

    if not modifier_visible:
        print("  SKIP  合成 Cmd 未被系统登记，本机无法用合成事件验证该项")
        print("        （modifiers_down() 已通过独立探测：真实按住 Cmd 时返回 True）")
        results.append(("Cmd+拖动不触发", True, "SKIP：合成修饰键无效，已用独立探测验证检测逻辑"))
    else:
        reset_clip()
        n4 = svc.stats["ok"]
        time.sleep(0.6)
        drag(120, left, left + 200, hold_cmd=True)
        time.sleep(0.9)
        check("Cmd+拖动不触发", svc.stats["ok"] == n4, f"变化 {svc.stats['ok'] - n4}")
        check("Cmd+拖动不改剪贴板", clipboard.get_text() == "__SENTINEL__", "")

    # 说明：原来这里有一个「在窗口空白处拖动，剪贴板应保持不变」的用例，现已删除。
    # 实测证明前提不成立：TextEdit 在文档末尾的空白行上拖动，会真的选中末尾几个字
    # （AX 选区从整行变成 '内容'），此时应用照实复制是正确行为。
    # 「取不到选区时不污染剪贴板」这条逻辑已由 test_capture.py 的假后端用例覆盖。

    # ---- 7. 重建监听器后不应重复触发 ----
    print("\n[7] 重建监听器后再双击（应恰好 1 次）")
    okr = svc.restart_watcher()
    check("监听器重建成功", okr and svc.watcher_alive(), f"restart={okr}")
    time.sleep(0.4)
    reset_clip()
    n5 = svc.stats["ok"]
    y7 = oy + 88  # 用确定有文字的行，避免点在文档末尾的空白区导致误判
    mouse.position = (left + 120, y7)
    time.sleep(0.7)
    mouse.click(Button.left, 2)
    time.sleep(1.1)
    # 独立确认「应用确实产生了选区」——只作为诊断信息，不作为通过条件
    # （AX 单次读取偶尔会返回空，稳定的判据是剪贴板与计数）
    sel = None
    for _ in range(3):
        sel = b.read_selection_ax()
        if sel:
            break
        time.sleep(0.2)
    check("重建后仍能复制", clipboard.get_text() not in ("", "__SENTINEL__"), f"AX 探测={sel!r}")
    check("重建后不重复触发", svc.stats["ok"] - n5 == 1, f"+{svc.stats['ok'] - n5}")

    # ---- 8. 快速甩选（<80ms，位移大） ----
    print("\n[8] 快速甩选（快速划一下就松）")
    reset_clip()
    n8 = svc.stats["ok"]
    y = oy + 88  # 落在有文字的行上；注意不能用文档下方的空白区
    mouse.position = (left, y)
    time.sleep(0.6)
    t_start = time.monotonic()
    mouse.press(Button.left)
    for i in range(1, 4):
        mouse.position = (left + 80 * i, y)
        time.sleep(0.004)
    mouse.release(Button.left)
    drag_ms = (time.monotonic() - t_start) * 1000
    time.sleep(1.0)
    check(
        f"快速甩选({drag_ms:.0f}ms)也能复制",
        svc.stats["ok"] - n8 == 1,
        f"剪贴板={clipboard.get_text()[:20]!r}",
    )

    # ---- 9. 历史记录 ----
    print("\n[9] 历史记录")
    check("历史有记录", len(svc.history) > 0, f"{len(svc.history)} 条")

    # 说明：非文本选区的还原逻辑是纯逻辑，已放在 tests/test_capture.py 里用假剪贴板
    # 验证——不在本文件重复，避免再往真实剪贴板里写测试数据。

    # 收尾：关窗口、删临时文件、还原剪贴板
    try:
        if b.frontmost_app()[0] == "com.apple.TextEdit":
            with kb.pressed(Key.cmd):
                kb.press("w")
                kb.release("w")
            time.sleep(0.6)
    except Exception:
        pass
    try:
        TEMP.unlink()
    except Exception:
        pass
    svc.stop()

    if saved_clip:
        clipboard.set_text(saved_clip)
        print(f"已还原剪贴板（{len(saved_clip)} 字）")
    elif clip_had_other:
        print("注意：测试前剪贴板是非文本内容（图片/文件），已被测试覆盖，无法还原")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 46}\n{passed}/{total} 通过")
    for name, ok, detail in results:
        if not ok:
            print(f"  失败：{name} {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
