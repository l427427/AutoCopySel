"""托盘图标绘制（Pillow）。深/浅菜单栏都能看清：深色描边 + 浅色内芯。"""
from __future__ import annotations

from PIL import Image, ImageDraw

SIZE = 64

_OUTLINE = (28, 28, 30, 255)
_LIGHT = (245, 245, 245, 255)
_DIM = (148, 148, 152, 255)
_GREEN = (52, 199, 89, 255)
_ORANGE = (255, 159, 10, 255)
_RED = (255, 59, 48, 255)


def _body(glyph: tuple[int, int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 板身：先粗深色描边，再细浅色内芯
    for color, w in ((_OUTLINE, 8), (glyph, 4)):
        d.rounded_rectangle([13, 15, 51, 58], radius=10, outline=color, width=w)

    # 内页横线
    for y in (29, 38, 47):
        d.line([23, y, 41, y], fill=glyph, width=4)

    # 顶部夹子：自带填充，盖住板身顶边，避免接缝
    d.rounded_rectangle([23, 4, 41, 20], radius=5, fill=_OUTLINE)
    d.rounded_rectangle([27, 8, 37, 16], radius=3, fill=glyph)
    return img


def state_icon(paused: bool = False, flash: str | None = None) -> Image.Image:
    img = _body(_DIM if paused else _LIGHT)
    if flash:
        d = ImageDraw.Draw(img)
        color = _GREEN if flash == "ok" else (_RED if flash == "danger" else _ORANGE)
        d.ellipse([38, 40, 62, 64], fill=color, outline=_OUTLINE, width=3)
    return img


def permission_icon() -> Image.Image:
    """缺权限：红点 + 白色感叹号。用户一眼能看出「程序在，但不工作」。"""
    img = _body(_DIM)
    d = ImageDraw.Draw(img)
    d.ellipse([34, 36, 62, 64], fill=_RED, outline=_OUTLINE, width=3)
    d.rounded_rectangle([45, 42, 51, 53], radius=2, fill=_LIGHT)
    d.ellipse([45, 55, 51, 61], fill=_LIGHT)
    return img


IDLE = state_icon(False)
PAUSED = state_icon(True)
COPIED = state_icon(False, "ok")
WARN = state_icon(False, "warn")
DELETED = state_icon(False, "danger")
NEED_PERM = permission_icon()
