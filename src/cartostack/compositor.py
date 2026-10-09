"""Stacking layers into the output image (``docs/format.md`` §6.1-§6.2).

Layers are composited source-over in straight alpha with Pillow's
``Image.alpha_composite``, the format's reference operator, starting from a fully
transparent canvas. A layer's ``opacity`` scales its alpha by
``floor(alpha · opacity + 0.5)``. Layers are placed at their integer
``(left, top)``; the parts outside the canvas are discarded. Map clipping
(``geometry.clip``) is a separate rule that only slot rendering applies.

``Compositor`` flattens each contiguous run of static layers (``raster`` and
``colorbar``) once and reuses it while the run's layer objects stay the same.
Slots (``grid``, ``polygon``, ``text``) are expected to change between renders
and are composited one by one. The bottom run is composited exactly as
layer-by-layer stacking would; a run of two or more static layers above a slot
is flattened on its own first, which may differ from layer-by-layer stacking
by rounding: in a stress test of random partial-alpha runs of 2-4 layers, 0.05 %
of channel values differed by 2 levels and none by more than 3
(``tests/test_compositor.py``). ``Compositor.render(..., flatten=False)`` stacks
every layer individually.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from PIL import Image

from .layers import RGBA, ColorbarLayer, Layer, RasterLayer

PNG_MODES: Final = ("RGBA", "RGB")
STATIC_KINDS: Final = frozenset({RasterLayer.kind, ColorbarLayer.kind})


@dataclass(frozen=True)
class Window:
    """The visible part of a placed layer: ``src`` box in the layer, ``dest`` corner on the canvas.

    Boxes are ``(x0, y0, x1, y1)`` in pixels, exclusive at the far edge.
    """

    src: tuple[int, int, int, int]
    dest: tuple[int, int]

    @property
    def box(self) -> tuple[int, int, int, int]:
        """The covered canvas box."""
        x0, y0, x1, y1 = self.src
        return (self.dest[0], self.dest[1], self.dest[0] + x1 - x0, self.dest[1] + y1 - y0)


def window(left: int, top: int, height: int, width: int, canvas: tuple[int, int]) -> Window | None:
    """Clip a ``(height, width)`` array placed at ``(left, top)`` to a ``(height, width)`` canvas.

    Returns ``None`` when nothing of it is on the canvas.
    """
    x0, y0 = max(0, -left), max(0, -top)
    x1, y1 = min(width, canvas[1] - left), min(height, canvas[0] - top)
    if x0 >= x1 or y0 >= y1:
        return None
    return Window((x0, y0, x1, y1), (left + x0, top + y0))


def apply_opacity(pixels: RGBA, opacity: float) -> RGBA:
    """Scale alpha by ``opacity`` with ``floor(alpha · opacity + 0.5)``; RGB is unchanged."""
    if opacity >= 1.0:
        return pixels
    alpha = np.floor(np.arange(256, dtype=np.float64) * opacity + 0.5).astype(np.uint8)
    out = np.array(pixels, copy=True)
    out[..., 3] = alpha[pixels[..., 3]]
    return out


def drawn(layers: Iterable[Layer]) -> list[Layer]:
    """The layers that contribute pixels: visible, with pixels, and opacity above 0."""
    return [
        layer
        for layer in layers
        if layer.visible and layer.pixels is not None and layer.opacity > 0.0
    ]


def _over(canvas: Image.Image, layer: Layer, origin: tuple[int, int] = (0, 0)) -> None:
    """Composite ``layer`` onto ``canvas``, whose top-left sits at canvas pixel ``origin``."""
    assert layer.pixels is not None
    height, width = layer.pixels.shape[:2]
    left, top = layer.left - origin[0], layer.top - origin[1]
    win = window(left, top, height, width, (canvas.height, canvas.width))
    if win is None:
        return
    x0, y0, x1, y1 = win.src
    src = apply_opacity(layer.pixels[y0:y1, x0:x1], layer.opacity)
    canvas.alpha_composite(Image.fromarray(src, "RGBA"), dest=win.dest)


def transparent(shape: tuple[int, int]) -> Image.Image:
    return Image.new("RGBA", (shape[1], shape[0]), (0, 0, 0, 0))


def composite(layers: Iterable[Layer], shape: tuple[int, int]) -> Image.Image:
    """Layer-by-layer stacking (the reference): ``layers`` bottom to top on a transparent canvas.

    When the bottom layer covers the whole canvas at full opacity, stacking it onto the
    transparent canvas yields its own pixels with fully transparent ones zeroed (Pillow's
    ``alpha_composite``), so the canvas starts from that copy instead (exact, and about
    10 ms faster for a 4-megapixel first render).
    """
    todo = drawn(layers)
    if todo and _covers(todo[0], shape):
        canvas = _start(todo[0])
        todo = todo[1:]
    else:
        canvas = transparent(shape)
    for layer in todo:
        _over(canvas, layer)
    return canvas


def _covers(layer: Layer, shape: tuple[int, int]) -> bool:
    assert layer.pixels is not None
    return (
        layer.left == 0
        and layer.top == 0
        and layer.pixels.shape[:2] == shape
        and layer.opacity >= 1.0
    )


def _start(layer: Layer) -> Image.Image:
    """``layer`` composited onto a transparent canvas: its pixels, transparent ones zeroed."""
    assert layer.pixels is not None
    pixels = layer.pixels
    clear = pixels[..., 3] == 0
    if clear.any():
        pixels = pixels.copy()
        pixels[clear] = 0
    return Image.fromarray(pixels, "RGBA").copy()


@dataclass(frozen=True, eq=False)
class _Flat:
    """A flattened static run: its layers (kept alive for identity checks) and pixels."""

    layers: tuple[Layer, ...]
    image: Image.Image
    origin: tuple[int, int]

    def matches(self, run: Sequence[Layer]) -> bool:
        return len(run) == len(self.layers) and all(
            a is b for a, b in zip(run, self.layers, strict=True)
        )


class Compositor:
    """Stacks a scene's layers, reusing flattened static runs across renders.

    Layers are immutable, so a run is unchanged exactly while it consists of the
    same layer objects. Only runs used by the latest render stay cached.
    """

    def __init__(self, shape: tuple[int, int]) -> None:
        self.shape = shape
        self._cache: dict[int, _Flat] = {}

    def render(self, layers: Sequence[Layer], *, flatten: bool = True) -> Image.Image:
        """Composite ``layers`` (bottom to top) into a new RGBA image of the canvas size."""
        if not flatten:
            return composite(layers, self.shape)
        cache: dict[int, _Flat] = {}
        canvas: Image.Image | None = None
        for index, run, static in _runs(drawn(layers)):
            if not static or (canvas is not None and len(run) == 1):
                canvas = canvas or transparent(self.shape)
                for layer in run:
                    _over(canvas, layer)
                continue
            flat = self._cache.get(index)
            if flat is None or not flat.matches(run):
                flat = self._flatten(run, bottom=canvas is None)
            cache[index] = flat
            if canvas is None:
                canvas = flat.image.copy()
            else:
                canvas.alpha_composite(flat.image, dest=flat.origin)
        self._cache = cache
        return canvas or transparent(self.shape)

    def _flatten(self, run: Sequence[Layer], *, bottom: bool) -> _Flat:
        if bottom:
            return _Flat(tuple(run), composite(run, self.shape), (0, 0))
        boxes = []
        for layer in run:
            assert layer.pixels is not None
            height, width = layer.pixels.shape[:2]
            win = window(layer.left, layer.top, height, width, self.shape)
            assert win is not None  # scenes only hold layers that intersect the canvas
            boxes.append(win.box)
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        image = transparent((y1 - y0, x1 - x0))
        for layer in run:
            _over(image, layer, (x0, y0))
        return _Flat(tuple(run), image, (x0, y0))


def _runs(layers: Sequence[Layer]) -> list[tuple[int, list[Layer], bool]]:
    """Split into maximal runs of static or slot layers: ``(start index, run, static)``."""
    runs: list[tuple[int, list[Layer], bool]] = []
    for i, layer in enumerate(layers):
        static = layer.kind in STATIC_KINDS
        if runs and runs[-1][2] == static:
            runs[-1][1].append(layer)
        else:
            runs.append((i, [layer], static))
    return runs


def encode_png(
    image: Image.Image,
    *,
    mode: str = "RGBA",
    compress_level: int = 6,
    background: tuple[int, int, int] = (255, 255, 255),
) -> bytes:
    """Encode an RGBA image as PNG without changing its size.

    ``mode="RGB"`` composites the image over the opaque ``background`` colour
    first (default white, Matplotlib's default figure colour); an opaque image
    is unchanged by that.
    """
    if mode not in PNG_MODES:
        raise ValueError(f"mode must be one of {PNG_MODES}, got {mode!r}")
    if not (isinstance(compress_level, int) and 0 <= compress_level <= 9):
        raise ValueError(f"compress_level must be an integer 0..9, got {compress_level!r}")
    if len(background) != 3 or not all(0 <= int(c) <= 255 for c in background):
        raise ValueError(f"background must be 3 values in 0..255, got {background!r}")
    if mode == "RGB":
        if image.getchannel("A").getextrema() != (255, 255):
            base = Image.new("RGBA", image.size, (*(int(c) for c in background), 255))
            base.alpha_composite(image)
            image = base
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG", compress_level=compress_level)
    return buf.getvalue()
