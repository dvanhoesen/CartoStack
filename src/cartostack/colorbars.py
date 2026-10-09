"""Colorbars redrawn for a new normalisation or colormap, with NumPy and Pillow.

A colorbar layer with ``redraw`` data (``docs/format.md`` §7) is redrawn from parts:

1. ``under``: what Matplotlib draws before the colour strip (the axes background);
2. the strip: each pixel's LUT row (``rows``) coloured from the grid slot's current LUT
   and alpha. The bands do not depend on ``vmin``/``vmax``; a LUT with a different
   number of colours re-bands the strip by position along it;
3. ``over``: outline, extension outlines, and the axis label;
4. ticks and tick labels for the new range: Matplotlib's ``MaxNLocator``
   (``AutoLocator``) and ``ScalarFormatter`` reproduced exactly, labels drawn with the
   text-slot renderer.

When the new range would need an offset or scientific-notation label (``1e6``,
``+1000``), redrawing is refused (``ColorbarRedrawError``): the colorbar then stays
listed in ``Scene.stale_colorbars``.
"""

from __future__ import annotations

import itertools
import math
from typing import Final

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from . import polygons
from . import text as ctext
from .errors import CartoStackError
from .layers import RGBA, ColorbarLayer, ColorbarRedraw, GridSlot, TextSlot

# Matplotlib defaults (rcParams): axes.formatter.limits, offset_threshold, useoffset.
POWER_LIMITS: Final = (-5, 6)
OFFSET_THRESHOLD: Final = 4
MIN_N_TICKS: Final = 2


class ColorbarRedrawError(CartoStackError, ValueError):
    """The colorbar cannot be redrawn for this normalisation."""


# --- Ticks: matplotlib.ticker.MaxNLocator ------------------------------------------------


def _nonsingular(
    vmin: float, vmax: float, expander: float = 1e-13, tiny: float = 1e-14
) -> tuple[float, float]:
    if not (math.isfinite(vmin) and math.isfinite(vmax)):
        return -expander, expander
    if vmax < vmin:
        vmin, vmax = vmax, vmin
    maxabs = max(abs(vmin), abs(vmax))
    if maxabs < (1e6 / tiny) * np.finfo(float).tiny:
        return -expander, expander
    if vmax - vmin <= maxabs * tiny:
        if vmax == 0 and vmin == 0:
            return -expander, expander
        return vmin - expander * abs(vmin), vmax + expander * abs(vmax)
    return vmin, vmax


def _scale_range(vmin: float, vmax: float, n: int, threshold: float = 100) -> tuple[float, float]:
    dv = abs(vmax - vmin)
    meanv = (vmax + vmin) / 2
    offset = (
        0.0
        if abs(meanv) / dv < threshold
        else math.copysign(10 ** (math.log10(abs(meanv)) // 1), meanv)
    )
    return 10 ** (math.log10(dv / n) // 1), offset


class _Edge:
    def __init__(self, step: float, offset: float) -> None:
        self.step = step
        self.offset = abs(offset)

    def _close(self, ms: float, edge: float) -> bool:
        if self.offset > 0:
            tol = min(0.4999, max(1e-10, 10 ** (math.log10(self.offset / self.step) - 12)))
        else:
            tol = 1e-10
        return abs(ms - edge) < tol

    def le(self, x: float) -> float:
        d, m = divmod(x, self.step)
        return d + 1 if self._close(m / self.step, 1) else d

    def ge(self, x: float) -> float:
        d, m = divmod(x, self.step)
        return d if self._close(m / self.step, 0) else d + 1


def tick_values(
    vmin: float, vmax: float, nbins: int, steps: tuple[float, ...]
) -> NDArray[np.float64]:
    """``MaxNLocator(nbins, steps).tick_values(vmin, vmax)`` (``integer=False``, no pruning)."""
    vmin, vmax = _nonsingular(vmin, vmax)
    scale, offset = _scale_range(vmin, vmax, nbins)
    lo, hi = vmin - offset, vmax - offset
    base = np.asarray(steps, np.float64)
    if base[0] != 1 or base[-1] != 10:
        raise ColorbarRedrawError("tick steps must start at 1 and end at 10")
    extended = np.concatenate([0.1 * base[:-1], base, [10 * base[1]]])
    candidates = extended * scale
    raw_step = (hi - lo) / nbins
    large = candidates >= raw_step
    istep = int(np.nonzero(large)[0][0]) if large.any() else len(candidates) - 1
    ticks = np.array([0.0])
    for step in candidates[: istep + 1][::-1]:
        best = (lo // step) * step
        edge = _Edge(float(step), offset)
        low, high = edge.le(lo - best), edge.ge(hi - best)
        ticks = np.arange(low, high + 1) * step + best
        if ((ticks <= hi) & (ticks >= lo)).sum() >= MIN_N_TICKS:
            break
    return ticks + offset


# --- Labels: matplotlib.ticker.ScalarFormatter ---------------------------------------------


def _offset(locs: NDArray[np.float64], vmin: float, vmax: float) -> float:
    locs = locs[(vmin <= locs) & (locs <= vmax)]
    if not len(locs):
        return 0.0
    lmin, lmax = float(locs.min()), float(locs.max())
    if lmin == lmax or lmin <= 0 <= lmax:
        return 0.0
    abs_min, abs_max = sorted([abs(lmin), abs(lmax)])
    sign = math.copysign(1, lmin)
    oom_max = math.ceil(math.log10(abs_max))
    oom = 1 + next(o for o in itertools.count(oom_max, -1) if abs_min // 10**o != abs_max // 10**o)
    if (abs_max - abs_min) / 10**oom <= 1e-2:
        oom = 1 + next(
            o for o in itertools.count(oom_max, -1) if abs_max // 10**o - abs_min // 10**o > 1
        )
    n = OFFSET_THRESHOLD - 1
    return float(sign * (abs_max // 10**oom) * 10**oom) if abs_max // 10**oom >= 10**n else 0.0


def _order_of_magnitude(locs: NDArray[np.float64], vmin: float, vmax: float, offset: float) -> int:
    locs = np.abs(locs[(vmin <= locs) & (locs <= vmax)])
    if not len(locs):
        return 0
    if offset:
        oom = math.floor(math.log10(vmax - vmin))
    else:
        val = float(locs.max())
        oom = 0 if val == 0 else math.floor(math.log10(val))
    return oom if oom <= POWER_LIMITS[0] or oom >= POWER_LIMITS[1] else 0


def _decimals(locs: NDArray[np.float64], vmin: float, vmax: float) -> int:
    pts = np.asarray([*locs, vmin, vmax] if len(locs) < 2 else locs, np.float64)
    loc_range = float(np.ptp(pts)) or float(np.max(np.abs(pts))) or 1.0
    if len(locs) < 2:
        pts = pts[:-2]
    oom = math.floor(math.log10(loc_range))
    sigfigs = max(0, 3 - oom)
    thresh = 1e-3 * 10**oom
    while sigfigs >= 0:
        if np.abs(pts - np.round(pts, decimals=sigfigs)).max() < thresh:
            sigfigs -= 1
        else:
            break
    return sigfigs + 1


def tick_labels(
    locs: NDArray[np.float64], vmin: float, vmax: float, minus: str = "\u2212"
) -> list[str]:
    """``ScalarFormatter`` labels for ``locs`` on an axis viewing ``[vmin, vmax]``.

    Raises ``ColorbarRedrawError`` if Matplotlib would show an offset or a
    scientific-notation multiplier, which this runtime does not draw.
    """
    locs = np.asarray(locs, np.float64)
    lo, hi = sorted((vmin, vmax))
    offset = _offset(locs, lo, hi)
    oom = _order_of_magnitude(locs, lo, hi, offset)
    if offset or oom:
        raise ColorbarRedrawError(
            f"ticks for {vmin:g}..{vmax:g} need an offset or scientific notation, "
            "which runtime colorbars do not draw"
        )
    fmt = f"%1.{_decimals(locs, lo, hi)}f"
    out = []
    for x in locs:
        xp = 0.0 if abs(x) < 1e-8 else float(x)
        out.append((fmt % xp).replace("-", minus))
    return out


# --- Drawing -------------------------------------------------------------------------------


def visible_ticks(
    r: ColorbarRedraw, vmin: float, vmax: float
) -> tuple[NDArray[np.float64], list[str]]:
    """The ticks drawn for ``[vmin, vmax]`` and their labels (Matplotlib's rules)."""
    if r.locator == "fixed":
        locs = np.asarray(r.values, np.float64)
    else:
        locs = tick_values(vmin, vmax, r.nbins, r.steps)
    labels = tick_labels(locs, vmin, vmax, r.minus) if len(locs) else []
    keep = [(t, lab) for t, lab in zip(locs, labels, strict=True) if vmin <= t <= vmax]
    return np.array([t for t, _ in keep]), [lab for _, lab in keep]


def _strip(r: ColorbarRedraw, slot: GridSlot) -> RGBA:
    """The colour strip in the slot's current LUT and alpha."""
    n = slot.n_colors
    rows = r.rows
    if n != r.n_colors:  # re-band by position along the strip
        rows = rows.copy()
        inner = (rows >= 0) & (rows < r.n_colors)
        left, top, width, height = r.box
        yy, xx = np.nonzero(inner)
        if r.orientation == "horizontal":
            t = (xx + 0.5 - left) / width
        else:
            t = (top + height - (yy + 0.5)) / height
        rows[yy, xx] = np.clip(np.floor(t * n), 0, n - 1).astype(np.int32)
        rows[rows == r.n_colors] = n
        rows[rows == r.n_colors + 1] = n + 1
    colors = np.array(slot.lut)
    if slot.alpha is not None:
        colors[:, 3] = math.floor(slot.alpha * 255 + 0.5)
    out = np.zeros((*rows.shape, 4), np.uint8)
    mask = rows >= 0
    out[mask] = colors[rows[mask]]
    return out


def _tick_marks(r: ColorbarRedraw, positions: NDArray[np.float64], shape: tuple[int, int]) -> RGBA:
    """Tick marks as filled rectangles, snapped as Agg snaps marker paths."""
    h, w = shape
    left, top, width, height = r.box
    length = r.tick_length
    half = max(r.tick_width, 1.0) / 2
    out = np.zeros((h, w, 4), np.uint8)
    if not len(positions) or length <= 0:
        return out
    start, end = {"out": (0.0, length), "in": (-length, 0.0), "inout": (-length / 2, length / 2)}[
        r.direction
    ]
    rings = []
    for p in positions:
        c = math.floor(p) + 0.5  # odd-width strokes snap to pixel centres
        if r.side == "bottom":
            y0 = top + height
            rings.append(
                [
                    (c - half, y0 + start),
                    (c + half, y0 + start),
                    (c + half, y0 + end),
                    (c - half, y0 + end),
                ]
            )
        elif r.side == "top":
            y0 = top
            rings.append(
                [
                    (c - half, y0 - end),
                    (c + half, y0 - end),
                    (c + half, y0 - start),
                    (c - half, y0 - start),
                ]
            )
        elif r.side == "right":
            x0 = left + width
            rings.append(
                [
                    (x0 + start, c - half),
                    (x0 + end, c - half),
                    (x0 + end, c + half),
                    (x0 + start, c + half),
                ]
            )
        else:
            x0 = left
            rings.append(
                [
                    (x0 - end, c - half),
                    (x0 - start, c - half),
                    (x0 - start, c + half),
                    (x0 - end, c + half),
                ]
            )
    k = 4
    for ring in rings:
        pts = np.asarray(ring, np.float64)
        found = polygons.coverage(pts[:, 0], pts[:, 1], np.array([4]), k, (0, 0, w * k, h * k))
        if found is None:
            continue
        (y0_, x0_), counts = found
        region = out[y0_ : y0_ + counts.shape[0], x0_ : x0_ + counts.shape[1]]
        alpha = (counts.astype(np.int32) * r.tick_color[3] * 2 + k * k) // (2 * k * k)
        src = np.zeros((*counts.shape, 4), np.uint8)
        src[..., :3] = r.tick_color[:3]
        src[..., 3] = alpha
        sel = alpha > 0
        region[sel] = polygons.over(region[sel], src[sel])
    return out


def _label_anchors(
    r: ColorbarRedraw, positions: NDArray[np.float64]
) -> list[tuple[float, float, str, str]]:
    left, top, width, height = r.box
    outward = {"out": r.tick_length, "in": 0.0, "inout": r.tick_length / 2}[r.direction] + r.pad
    out = []
    for p in positions:
        if r.side == "bottom":
            out.append((float(p), top + height + outward, "center", "top"))
        elif r.side == "top":
            out.append((float(p), top - outward, "center", "bottom"))
        elif r.side == "right":
            out.append((left + width + outward, float(p), "left", "center_baseline"))
        else:
            out.append((left - outward, float(p), "right", "center_baseline"))
    return out


def label_edge(
    r: ColorbarRedraw, positions: NDArray[np.float64], labels: list[str], font: bytes | None
) -> float:
    """Outer edge of the tick labels' layout boxes (Matplotlib places the axis label past it)."""
    edges = []
    for (x, y, ha, va), label in zip(_label_anchors(r, positions), labels, strict=True):
        slot = TextSlot(
            id="tick", order=0, value=label, font=r.font, size_px=r.size_px, x=x, y=y, ha=ha, va=va
        )
        f = ctext.load_font(slot, font)
        lay = ctext.layout(slot, f, f.info.ligate(label))
        ox, oy = lay.origin
        ink_left, ink_top, ink_right, ink_bottom = lay.ink
        ascent = max(-ink_top, f.scale(f.info.typo_ascender))
        descent = max(ink_bottom, -f.scale(f.info.typo_descender))
        edges.append(
            {
                "bottom": oy + descent,
                "top": oy - ascent,
                "right": ox + ink_right,
                "left": ox + max(ink_left, 0.0),
            }[r.side]
        )
    if not edges:
        return r.label_edge
    return max(edges) if r.side in ("bottom", "right") else min(edges)


def _labels(
    r: ColorbarRedraw,
    positions: NDArray[np.float64],
    labels: list[str],
    font: bytes | None,
    shape: tuple[int, int],
) -> Image.Image:
    h, w = shape
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for (x, y, ha, va), label in zip(_label_anchors(r, positions), labels, strict=True):
        slot = TextSlot(
            id="tick",
            order=0,
            value=label,
            font=r.font,
            size_px=r.size_px,
            color=r.label_color,
            x=float(x),
            y=float(y),
            ha=ha,
            va=va,
        )
        lx, ly, px = ctext.render(slot, font, (h, w))
        if px is not None:
            out.alpha_composite(Image.fromarray(px, "RGBA"), dest=(lx, ly))
    return out


def render(layer: ColorbarLayer, slot: GridSlot, font: bytes | None) -> RGBA:
    """New pixels for ``layer`` showing ``slot``'s current normalisation and colormap."""
    r = layer.redraw
    if r is None:
        raise ColorbarRedrawError(f"{layer.id}: the file holds no redraw data for this colorbar")
    shape = r.rows.shape
    left, top, width, height = r.box
    locs, labels = visible_ticks(r, slot.vmin, slot.vmax)
    span = slot.vmax - slot.vmin
    if r.orientation == "horizontal":
        positions = left + (locs - slot.vmin) / span * width
    else:
        positions = top + height - (locs - slot.vmin) / span * height
    canvas = Image.new("RGBA", (shape[1], shape[0]), (0, 0, 0, 0))
    for part in (r.under, _strip(r, slot), r.over, _tick_marks(r, positions, shape)):
        if part is not None:
            canvas.alpha_composite(Image.fromarray(np.ascontiguousarray(part), "RGBA"))
    canvas.alpha_composite(_labels(r, positions, labels, font, shape))
    if r.label is not None:
        shift = round(label_edge(r, positions, labels, font) - r.label_edge) if labels else 0
        dx, dy = (0, shift) if r.side in ("bottom", "top") else (shift, 0)
        moved = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        moved.paste(Image.fromarray(np.ascontiguousarray(r.label), "RGBA"), (dx, dy))
        canvas.alpha_composite(moved)
    return np.asarray(canvas, dtype=np.uint8).copy()
