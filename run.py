#!/usr/bin/env python3
"""AutoCopySel 入口。

用法：
    python run.py                # 托盘后台运行
    python run.py --check        # 环境/权限自检
    python run.py --capture      # 立即试取一次选中文本（打印结果）
    python run.py --no-tray      # 无托盘运行（仅命令行调试）
    python run.py --verbose      # 输出调试日志
    python run.py --reset-config # 恢复默认配置
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="AutoCopySel", description="鼠标划词自动复制（Windows / macOS）"
    )
    parser.add_argument("--check", action="store_true", help="打印环境与权限自检信息")
    parser.add_argument(
        "--doctor", action="store_true", help="完整诊断（含事件监听自检与修复建议）"
    )
    parser.add_argument(
        "--reset-permission",
        action="store_true",
        help="清除本程序的辅助功能授权记录并打开设置页（macOS）",
    )
    parser.add_argument(
        "--capture", action="store_true", help="立即尝试取一次选中文本并打印"
    )
    parser.add_argument("--no-tray", action="store_true", help="不启动托盘图标")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出调试日志")
    parser.add_argument("--reset-config", action="store_true", help="恢复默认配置")
    args = parser.parse_args(argv)

    from autocopysel.app import Application, acquire_single_instance, setup_logging
    from autocopysel.config import CONFIG_PATH, Config

    if args.reset_config:
        try:
            CONFIG_PATH.unlink()
            print(f"已删除配置：{CONFIG_PATH}")
        except FileNotFoundError:
            print("配置文件本来就不存在")
        return 0

    if args.doctor:
        from autocopysel import diagnostics

        cfg = Config.load()
        rows = diagnostics.collect(cfg)
        print(diagnostics.format_report(rows))
        tip = diagnostics.advice(rows)
        if tip:
            print()
            print(tip)
        print()
        print("--- 最近日志 ---")
        print(diagnostics.tail_log(10))
        return 0 if not any(r[1] == diagnostics.FAIL for r in rows) else 1

    if args.reset_permission:
        from autocopysel import diagnostics

        ok, msg = diagnostics.reset_permission()
        print(("已重置：%s" if ok else "重置失败：%s") % msg)
        print("请在打开的设置页里勾选 AutoCopySel，授权后程序会自动生效。")
        return 0 if ok else 1

    if args.check:
        from autocopysel.capture import probe

        cfg = Config.load()
        info = probe()
        info["config_path"] = str(cfg.path)
        info["config"] = cfg.data
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    if args.capture:
        from autocopysel import clipboard
        from autocopysel.capture import capture_once

        cfg = Config.load()
        setup_logging(args.verbose)
        print("3 秒内请用鼠标在其他窗口里选中一段文字……")
        for i in (3, 2, 1):
            print(f"  {i}")
            import time

            time.sleep(1)
        res = capture_once(cfg)
        print("-" * 48)
        print(f"成功: {res.ok}")
        print(f"来源: {res.source or '-'}")
        print(f"应用: {res.app or '-'}")
        print(f"说明: {res.note or '-'}")
        print(f"文本({len(res.text)} 字): {res.text[:200]!r}")
        print(f"剪贴板当前: {clipboard.get()[:200]!r}")
        return 0 if res.ok else 1

    setup_logging(args.verbose)

    if not acquire_single_instance():
        print("AutoCopySel 已经在运行了。")
        return 0

    cfg = Config.load()
    app = Application(cfg)
    try:
        app.run(with_tray=not args.no_tray)
    except KeyboardInterrupt:
        app.service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
