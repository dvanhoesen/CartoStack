"""Text slots: one line of text rendered with Pillow/FreeType (``docs/format.md`` §10).

Text is laid out the way Matplotlib 3.11 lays out a single line, so it lands where text
authored in Matplotlib does:

* **Shaping.** The font's ligatures (GSUB ``rlig``/``liga``/``clig``) are applied when
  the ligature glyph has a Unicode code point (e.g. U+FB01 "ﬁ"). Pen positions are
  FreeType's hinted advances (Pillow's, identical to HarfBuzz's) plus the font's pair
  kerning (GPOS ``kern``, else a ``kern`` table), each rounded to 1/64 pixel. Other
  shaping (contextual alternates, mark positioning) is not applied.
* **Box.** The ink box is the union of the glyphs' ink boxes at their pen positions;
  its width aligns the pen origin by ``ha``. Vertically, the ascent is the larger of the
  ink ascent and the font's OS/2 typographic ascender, the descent the larger of the
  ink descent and the typographic descender, and ``va`` aligns that box.
* **Glyphs** are rendered by FreeType at whole pixels and shifted by the remaining
  sub-pixel fraction with bilinear interpolation (Pillow places glyphs only at whole
  pixels; Matplotlib renders them at 1/64-pixel positions). Coverage combines across
  glyphs as source-over, as Agg draws each glyph in turn.

The layer holds only the text's pixels, cropped to the ink and to the canvas, in
straight alpha: the colour with alpha ``round(coverage · a)``.

Fonts come only from the scene's embedded assets. A missing or unreadable font is an
error (``TextFontError``); there is never a fallback to another font.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

import numpy as np
from numpy.typing import NDArray
from PIL import ImageFont

from . import _opentype
from .errors import CartoStackError
from .layers import RGBA, TextSlot

#: Pen positions are quantised like HarfBuzz/FreeType 26.6 fixed point.
SUBPIXEL: Final = 64


class TextFontError(CartoStackError, ValueError):
    """A text slot's font is missing from the scene or cannot be used."""


@dataclass(frozen=True, eq=False)  # hashed by identity: a cache key
class Font:
    face: ImageFont.FreeTypeFont
    info: _opentype.FontInfo
    size_px: float

    def scale(self, units: int) -> float:
        return units * self.size_px / self.info.units_per_em


@lru_cache(maxsize=16)
def _info(data: bytes) -> _opentype.FontInfo:
    return _opentype.read(data)


@lru_cache(maxsize=32)
def _font(data: bytes, size_px: float) -> Font:
    face = ImageFont.truetype(io.BytesIO(data), size_px, layout_engine=ImageFont.Layout.BASIC)
    return Font(face, _info(data), size_px)


def load_font(slot: TextSlot, data: bytes | None) -> Font:
    """The slot's embedded font at ``size_px``; ``TextFontError`` if missing or unusable."""
    if data is None:
        raise TextFontError(f"{slot.id}: font {slot.font!r} is not among the scene's assets")
    try:
        return _font(data, slot.size_px)
    except (OSError, ValueError) as exc:
        raise TextFontError(
            f"{slot.id}: font {slot.font!r} cannot be used ({exc}); embed a TrueType/OpenType "
            "font. CartoStack never substitutes another font."
        ) from exc


Box = tuple[float, float, float, float]


@lru_cache(maxsize=4096)
def _glyph(font: Font, char: str) -> tuple[NDArray[np.float64], int, int, Box]:
    """Coverage (0..1), its offset from the pen origin, and the glyph's ink box."""
    mask, (dx, dy) = font.face.getmask2(char, mode="L", anchor="ls")
    width, height = mask.size
    coverage = np.asarray(mask, np.float64).reshape(height, width) / 255.0
    return coverage, dx, dy, font.face.getbbox(char, anchor="ls")


@dataclass(frozen=True)
class Layout:
    """Pen positions relative to the origin, the origin on the canvas, and the ink box."""

    pens: tuple[float, ...]
    origin: tuple[float, float]
    ink: tuple[float, float, float, float]  # left, top, right, bottom relative to the origin


_SNAP: Final[dict[str, Callable[[float], int]]] = {
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
}


def _quantise(v: float) -> float:
    return round(v * SUBPIXEL) / SUBPIXEL


def pen_positions(font: Font, value: str) -> list[float]:
    """Each character's pen x (pixels from the origin): hinted advances plus kerning.

    Like HarfBuzz, every kerning value is rounded to 1/64 pixel before it is added.
    """
    pens, x = [], 0.0
    for i, char in enumerate(value):
        placement = 0.0
        if i:
            adj = font.info.kerning(value[i - 1], char)
            x += _quantise(font.scale(adj.first_advance))
            placement = _quantise(font.scale(adj.second_placement))
        pens.append(x + placement)
        x += font.face.getlength(char)
        if i:
            x += _quantise(font.scale(adj.second_advance))
    return pens


def layout(slot: TextSlot, font: Font, value: str) -> Layout:
    """Where ``value`` goes: Matplotlib 3.11's single-line rule (``docs/format.md`` §10).

    ``value`` is the shaped text (``Font.info.ligate`` already applied).
    """
    pens = pen_positions(font, value)
    boxes: list[Box] = []
    for pen, char in zip(pens, value, strict=True):
        x0, y0, x1, y1 = _glyph(font, char)[3]
        boxes.append((pen + x0, y0, pen + x1, y1))
    inked = [b for b, c in zip(boxes, value, strict=True) if not c.isspace()] or boxes
    inked = inked or [(0.0, 0.0, 0.0, 0.0)]
    left = min(b[0] for b in inked)
    top = min(b[1] for b in inked)
    right = max(b[2] for b in inked)
    bottom = max(b[3] for b in inked)
    ascent = max(-top, font.scale(font.info.typo_ascender))
    descent = max(bottom, -font.scale(font.info.typo_descender))
    # Matplotlib measures the width from the pen origin when ink starts left of it
    # (e.g. a slanted "A"), so ink left of the origin does not widen the box.
    width = right - max(left, 0.0)
    ox = slot.x - {"left": 0.0, "center": width / 2, "right": width}[slot.ha]
    oy = {
        "baseline": slot.y,
        "bottom": slot.y - descent,
        "top": slot.y + ascent,
        "center": slot.y + (ascent - descent) / 2,
        "center_baseline": slot.y + ascent / 2,  # Matplotlib's y tick labels
    }[slot.va]
    if slot.snap is not None:
        snap = _SNAP[slot.snap]
        ox, oy = float(snap(ox)), float(snap(oy))
    ox, oy = ox + slot.offset[0], oy + slot.offset[1]
    return Layout(tuple(pens), (_quantise(ox), _quantise(oy)), (left, top, right, bottom))


def render(
    slot: TextSlot, font_data: bytes | None, canvas: tuple[int, int], value: str | None = None
) -> tuple[int, int, RGBA | None]:
    """Render ``value`` (default: the slot's) → ``(left, top, pixels)`` on a ``(h, w)`` canvas.

    ``pixels`` is ``None`` when nothing would be drawn on the canvas (empty or blank
    text, or text placed entirely off the canvas).
    """
    text = slot.value if value is None else value
    if "\n" in text or "\r" in text:
        raise ValueError(f"{slot.id}: text slots hold one line; got a line break")
    font = load_font(slot, font_data)
    if not text.strip():
        return 0, 0, None
    text = font.info.ligate(text)
    lay = layout(slot, font, text)
    ox, oy = lay.origin
    left, top, right, bottom = lay.ink
    x0 = max(math.floor(ox + left) - 1, 0)
    y0 = max(math.floor(oy + top) - 1, 0)
    x1 = min(math.ceil(ox + right) + 2, canvas[1])
    y1 = min(math.ceil(oy + bottom) + 2, canvas[0])
    if x1 <= x0 or y1 <= y0:
        return 0, 0, None
    cov = np.zeros((y1 - y0 + 2, x1 - x0 + 2))
    for char, pen in zip(text, lay.pens, strict=True):
        glyph, dx, dy, _ = _glyph(font, char)
        if glyph.size == 0:
            continue
        gx, gy = ox + pen + dx - x0, oy + dy - y0
        ix, iy = math.floor(gx), math.floor(gy)
        fx, fy = gx - ix, gy - iy
        h, w = glyph.shape
        shifted = np.zeros((h + 1, w + 1))
        shifted[:h, :w] += (1 - fx) * (1 - fy) * glyph
        shifted[:h, 1:] += fx * (1 - fy) * glyph
        shifted[1:, :w] += (1 - fx) * fy * glyph
        shifted[1:, 1:] += fx * fy * glyph
        # Clip the glyph to the buffer (text partly off the canvas).
        sy0, sx0 = max(-iy, 0), max(-ix, 0)
        by0, bx0 = iy + sy0, ix + sx0
        hh = min(h + 1 - sy0, cov.shape[0] - by0)
        ww = min(w + 1 - sx0, cov.shape[1] - bx0)
        if hh <= 0 or ww <= 0:
            continue
        part = shifted[sy0 : sy0 + hh, sx0 : sx0 + ww]
        region = cov[by0 : by0 + hh, bx0 : bx0 + ww]
        region[:] = part + region * (1 - part)
    cov = cov[: y1 - y0, : x1 - x0]
    alpha = np.rint(np.clip(cov, 0.0, 1.0) * slot.color[3]).astype(np.uint8)
    rows, cols = np.nonzero(alpha)
    if rows.size == 0:
        return 0, 0, None
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    out = np.empty((r1 - r0, c1 - c0, 4), np.uint8)
    out[..., :3] = slot.color[:3]
    out[..., 3] = alpha[r0:r1, c0:c1]
    return x0 + int(c0), y0 + int(r0), out
