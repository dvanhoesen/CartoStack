"""In-memory layers of a scene.

Layers are immutable. Arrays passed in are copied into owned, read-only,
C-contiguous buffers, so mutating the caller's array afterwards cannot change a
layer; buffers already owned by a layer are shared, not copied, when a layer is
re-created with other fields changed. Scenes are edited by swapping in new layer
objects (``Scene.update_layer``, ``replace_layer``, ...). Field meanings follow
``docs/format.md`` §6-§10.
"""

from __future__ import annotations

import dataclasses
import math
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Final

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .errors import CartoStackError
from .manifest import (
    INDEX_ENCODINGS,
    LUT_ENCODINGS,
    RASTER_ENCODINGS,
    TEXT_HA,
    TEXT_VA,
    Bin,
)

DEFAULT_ENCODING: Final = "rgba8+zlib"
DEFAULT_INDEX_ENCODING: Final = "i32le+zlib"
DEFAULT_LUT_ENCODING: Final = "rgba8"

EDITABLE_FIELDS: Final = frozenset({"order", "left", "top", "visible", "opacity", "encoding"})
"""Fields any layer can change without new data (``Scene.update_layer``)."""

RGBA = NDArray[np.uint8]


class LayerError(CartoStackError, ValueError):
    """A layer's data or parameters are invalid."""


class ImmutableLayerError(CartoStackError, dataclasses.FrozenInstanceError):
    """An attempt to change a layer in place; edits go through ``Scene``."""


def edit_refusal(layer: Layer, name: str) -> str:
    """Why ``name`` of ``layer`` cannot be changed directly, and what to do instead."""
    what = f"{layer.kind} layer {layer.id!r}"
    if name in EDITABLE_FIELDS:
        return (
            f"layers are immutable: change {name!r} of {what} with "
            f"scene.update_layer({layer.id!r}, {name}=...)"
        )
    if name in ("pixels", "width", "height"):
        return (
            f"layers are immutable: replace the pixels of {what} with "
            f"scene.replace_layer({layer.id!r}, rgba)"
        )
    if name in {f.name for f in dataclasses.fields(layer)}:
        return (
            f"{name!r} of {what} cannot be edited in place; replace the whole layer with "
            f"scene.replace_layer({layer.id!r}, new_layer)"
        )
    stored = "pre-rendered pixels" if layer.kind in ("raster", "colorbar") else "its slot data"
    return (
        f"{what} has no {name!r}: the file stores only {stored}, not the styling it was "
        "drawn with, so it cannot be restyled at runtime. Re-author the layer on a build "
        f"machine, or replace its pixels with scene.replace_layer({layer.id!r}, rgba)"
    )


# Arrays created by ``_readonly``: safe to share between layer objects without copying.
_OWNED: weakref.WeakValueDictionary[int, NDArray[Any]] = weakref.WeakValueDictionary()


def _owned(arr: NDArray[Any]) -> bool:
    return _OWNED.get(id(arr)) is arr


def owned_rgba(pixels: ArrayLike, what: str = "pixels") -> RGBA:
    """Snapshot ``pixels`` as an owned, read-only, C-contiguous ``(h, w, 4)`` uint8 array."""
    arr = np.asarray(pixels)
    if arr.dtype != np.uint8:
        raise LayerError(f"{what} must have dtype uint8, got {arr.dtype}")
    if arr.ndim != 3 or arr.shape[2] != 4 or arr.shape[0] < 1 or arr.shape[1] < 1:
        raise LayerError(f"{what} must have shape (height, width, 4), got {arr.shape}")
    return arr if _owned(arr) else _readonly(arr)


def owned_index_map(index_map: ArrayLike, n_cells: int) -> NDArray[np.int32]:
    arr = np.asarray(index_map)
    if arr.dtype != np.int32:
        raise LayerError(f"index_map must have dtype int32, got {arr.dtype}")
    if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] < 1:
        raise LayerError(f"index_map must have shape (height, width), got {arr.shape}")
    lo, hi = int(arr.min()), int(arr.max())
    if lo < -1 or hi >= n_cells:
        raise LayerError(
            f"index_map values must be -1 or a cell index in 0..{n_cells - 1}, "
            f"found range {lo}..{hi}"
        )
    return arr if _owned(arr) else _readonly(arr)


def owned_lut(lut: ArrayLike) -> RGBA:
    arr = np.asarray(lut)
    if arr.dtype != np.uint8 or arr.ndim != 2 or arr.shape[1] != 4 or arr.shape[0] < 4:
        raise LayerError(
            "lut must be uint8 with shape (n_colors + 3, 4): colours, then under, over, bad; "
            f"got {arr.dtype} {arr.shape}"
        )
    return arr if _owned(arr) else _readonly(arr)


def _readonly(arr: NDArray[Any]) -> NDArray[Any]:
    out = np.array(arr, copy=True, order="C")
    out.flags.writeable = False
    _OWNED[id(out)] = out
    return out


def _floats(obj: object, *names: str) -> None:
    for name in names:
        value = getattr(obj, name)
        if value is not None:
            object.__setattr__(obj, name, float(value))


def _bin(b: Bin) -> Bin:
    return Bin(float(b.lower), float(b.upper), tuple(int(c) for c in b.color), int(b.order))  # type: ignore[arg-type]


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise LayerError(message)


@dataclass(frozen=True, eq=False, kw_only=True)
class Layer:
    """Common fields. ``width``/``height`` of ``None`` mean "from pixels, else the canvas"."""

    kind: ClassVar[str] = ""

    id: str
    order: float
    left: int = 0
    top: int = 0
    width: int | None = None
    height: int | None = None
    visible: bool = True
    opacity: float = 1.0
    pixels: RGBA | None = None
    encoding: str = DEFAULT_ENCODING
    extra: Mapping[str, Any] = field(default_factory=dict)
    # Encoded bytes this layer was read from, by role; reused on save when unchanged.
    _encoded: Mapping[str, tuple[str, bytes]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _check(isinstance(self.id, str) and bool(self.id), "layer id must be a non-empty string")
        _check(
            isinstance(self.order, int | float) and math.isfinite(self.order),
            f"{self.id}: order must be a finite number",
        )
        _check(0.0 <= self.opacity <= 1.0, f"{self.id}: opacity must be within 0..1")
        # Numbers are normalised so equal scenes serialise identically (12 vs 12.0).
        _floats(self, "order", "opacity")
        for name in ("left", "top"):
            value = getattr(self, name)
            _check(
                isinstance(value, int) and not isinstance(value, bool),
                f"{self.id}: {name} must be an integer",
            )
        _check(self.encoding in RASTER_ENCODINGS, f"{self.id}: unknown encoding {self.encoding!r}")
        if self.pixels is not None:
            px = owned_rgba(self.pixels, f"{self.id}: pixels")
            object.__setattr__(self, "pixels", px)
            for name, size in (("height", px.shape[0]), ("width", px.shape[1])):
                given = getattr(self, name)
                _check(
                    given in (None, size),
                    f"{self.id}: {name}={given} does not match pixels {name} {size}",
                )
                object.__setattr__(self, name, size)
        for name in ("width", "height"):
            value = getattr(self, name)
            _check(value is None or value >= 1, f"{self.id}: {name} must be >= 1")
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))
        object.__setattr__(self, "_encoded", MappingProxyType(dict(self._encoded)))

    def size(self, canvas: tuple[int, int]) -> tuple[int, int]:
        """``(height, width)`` of this layer on a canvas of ``(height, width)``."""
        return (self.height or canvas[0], self.width or canvas[1])


@dataclass(frozen=True, eq=False, kw_only=True)
class RasterLayer(Layer):
    kind: ClassVar[str] = "raster"

    def __post_init__(self) -> None:
        super().__post_init__()
        _check(self.pixels is not None, f"{self.id}: a {self.kind} layer needs pixels")


TICK_SIDES: Final = ("bottom", "top", "left", "right")
TICK_DIRECTIONS: Final = ("out", "in", "inout")


@dataclass(frozen=True, eq=False, kw_only=True)
class ColorbarRedraw:
    """What a runtime needs to redraw a colorbar for a new normalisation or colormap.

    Coordinates are pixels in the colorbar layer's own frame. ``box`` is the colour
    strip from ``vmin`` to ``vmax`` along the long axis. ``rows`` maps each pixel of the
    strip to the LUT row it shows for ``n_colors`` colours: ``0..n_colors-1``, then
    ``n_colors`` (under) and ``n_colors + 1`` (over) for the extension triangles, and
    ``-1`` elsewhere. ``under``/``over`` are the colorbar's pixels drawn before and after
    the strip (axes background; outline and label), without ticks or tick labels.
    """

    orientation: str  # "horizontal" | "vertical"
    box: tuple[float, float, float, float]  # left, top, width, height
    rows: NDArray[np.int32]
    n_colors: int
    under: RGBA | None = None
    over: RGBA | None = None
    label: RGBA | None = None  # the axis label, moved with the tick labels' outer edge
    label_edge: float = 0.0  # outer edge of the authored tick labels (runtime layout)
    locator: str = "auto"  # "auto" (Matplotlib's MaxNLocator) | "fixed"
    nbins: int = 9
    steps: tuple[float, ...] = (1.0, 2.0, 2.5, 5.0, 10.0)
    values: tuple[float, ...] = ()
    side: str = "bottom"
    direction: str = "out"
    tick_length: float = 0.0  # pixels
    tick_width: float = 0.0  # pixels
    tick_color: tuple[int, int, int, int] = (0, 0, 0, 255)
    font: str = ""
    size_px: float = 10.0
    label_color: tuple[int, int, int, int] = (0, 0, 0, 255)
    pad: float = 0.0  # pixels between tick and label
    minus: str = "\u2212"

    def __post_init__(self) -> None:
        _check(self.orientation in ("horizontal", "vertical"), "colorbar orientation is invalid")
        box = tuple(float(v) for v in self.box)
        _check(len(box) == 4 and box[2] > 0 and box[3] > 0, "colorbar box needs a positive size")
        object.__setattr__(self, "box", box)
        rows = np.asarray(self.rows)
        _check(rows.dtype == np.int32 and rows.ndim == 2, "colorbar rows must be a 2-D int32 array")
        object.__setattr__(self, "rows", rows if _owned(rows) else _readonly(rows))
        _check(self.n_colors >= 1, "colorbar n_colors must be >= 1")
        for name in ("under", "over", "label"):
            px = getattr(self, name)
            if px is not None:
                px = owned_rgba(px, f"colorbar {name}")
                _check(px.shape[:2] == rows.shape, f"colorbar {name} must have the rows' size")
                object.__setattr__(self, name, px)
        _check(self.locator in ("auto", "fixed"), "colorbar locator must be 'auto' or 'fixed'")
        _check(self.nbins >= 1, "colorbar nbins must be >= 1")
        object.__setattr__(self, "steps", tuple(float(v) for v in self.steps))
        object.__setattr__(self, "values", tuple(float(v) for v in self.values))
        _check(self.side in TICK_SIDES, "colorbar tick side is invalid")
        _check(self.direction in TICK_DIRECTIONS, "colorbar tick direction is invalid")
        _floats(self, "tick_length", "tick_width", "size_px", "pad", "label_edge")
        _check(self.size_px > 0, "colorbar label size_px must be positive")
        for name in ("tick_color", "label_color"):
            color = tuple(int(c) for c in getattr(self, name))
            _check(len(color) == 4 and all(0 <= c <= 255 for c in color), f"{name} must be RGBA")
            object.__setattr__(self, name, color)


@dataclass(frozen=True, eq=False, kw_only=True)
class ColorbarLayer(RasterLayer):
    kind: ClassVar[str] = "colorbar"
    slot: str
    redraw: ColorbarRedraw | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.redraw is not None:
            size = self.size((0, 0))
            _check(
                self.redraw.rows.shape == size,
                f"{self.id}: colorbar redraw data is {self.redraw.rows.shape}, the layer {size}",
            )


@dataclass(frozen=True, eq=False, kw_only=True)
class GridSlot(Layer):
    """Values of a fixed ``shape`` grid → pixels through ``index_map`` and ``lut``."""

    kind: ClassVar[str] = "grid"
    shape: tuple[int, int]
    index_map: NDArray[np.int32]
    lut: RGBA
    vmin: float
    vmax: float
    alpha: float | None = None
    extend: str = "neither"
    value_dtype: str = "float32"
    cells: str = "centers"
    index_encoding: str = DEFAULT_INDEX_ENCODING
    lut_encoding: str = DEFAULT_LUT_ENCODING

    def __post_init__(self) -> None:
        super().__post_init__()
        ny, nx = self.shape
        _check(ny >= 1 and nx >= 1, f"{self.id}: grid shape must be positive")
        _check(ny * nx <= 2**31 - 1, f"{self.id}: grid has more cells than int32 can index")
        imap = owned_index_map(self.index_map, ny * nx)
        object.__setattr__(self, "index_map", imap)
        object.__setattr__(self, "lut", owned_lut(self.lut))
        for name, size in (("height", imap.shape[0]), ("width", imap.shape[1])):
            given = getattr(self, name)
            _check(
                given in (None, size),
                f"{self.id}: {name}={given} does not match index_map {name} {size}",
            )
            object.__setattr__(self, name, size)
        _floats(self, "vmin", "vmax", "alpha")
        object.__setattr__(self, "shape", (int(ny), int(nx)))
        _check(
            math.isfinite(self.vmin) and math.isfinite(self.vmax) and self.vmin < self.vmax,
            f"{self.id}: requires finite vmin < vmax",
        )
        _check(
            self.alpha is None or 0.0 <= self.alpha <= 1.0, f"{self.id}: alpha must be within 0..1"
        )
        _check(self.extend in ("neither", "min", "max", "both"), f"{self.id}: invalid extend")
        _check(self.value_dtype in ("float32", "float64"), f"{self.id}: invalid value_dtype")
        _check(self.cells in ("centers", "edges"), f"{self.id}: invalid cells")
        _check(self.index_encoding in INDEX_ENCODINGS, f"{self.id}: invalid index encoding")
        _check(self.lut_encoding in LUT_ENCODINGS, f"{self.id}: invalid lut encoding")

    @property
    def n_colors(self) -> int:
        return int(self.lut.shape[0]) - 3


@dataclass(frozen=True, eq=False, kw_only=True)
class PolygonSlot(Layer):
    """Classified lon/lat polygons → pixels (bins, even-odd fill, supersampled coverage)."""

    kind: ClassVar[str] = "polygon"
    bins: tuple[Bin, ...]
    fallback: Bin | None = None
    round_decimals: int | None = None
    supersample: int = 1
    interval: str = "closed-open"
    fill_rule: str = "evenodd"

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "bins", tuple(_bin(b) for b in self.bins))
        if self.fallback is not None:
            object.__setattr__(self, "fallback", _bin(self.fallback))
        _check(len(self.bins) >= 1, f"{self.id}: needs at least one bin")
        for b in self.bins:
            _check(b.lower < b.upper, f"{self.id}: bin requires lower < upper")
        _check(1 <= self.supersample <= 8, f"{self.id}: supersample must be within 1..8")
        _check(self.interval == "closed-open", f"{self.id}: interval must be 'closed-open'")
        _check(self.fill_rule == "evenodd", f"{self.id}: fill_rule must be 'evenodd'")


@dataclass(frozen=True, eq=False, kw_only=True)
class TextSlot(Layer):
    """One line of text rendered with Pillow from the embedded ``font`` member."""

    kind: ClassVar[str] = "text"
    value: str
    font: str
    size_px: float
    color: tuple[int, int, int, int] = (0, 0, 0, 255)
    x: float = 0.0
    y: float = 0.0
    ha: str = "left"
    va: str = "baseline"
    layout: str = "matplotlib"
    snap: str | None = None
    offset: tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        _floats(self, "size_px", "x", "y", "rotation")
        object.__setattr__(self, "offset", (float(self.offset[0]), float(self.offset[1])))
        object.__setattr__(self, "color", tuple(int(c) for c in self.color))
        _check(self.size_px > 0, f"{self.id}: size_px must be positive")
        _check(self.ha in TEXT_HA, f"{self.id}: invalid ha {self.ha!r}")
        _check(self.va in TEXT_VA, f"{self.id}: invalid va {self.va!r}")
        _check(self.rotation == 0, f"{self.id}: rotation must be 0 in format 1.0")
        _check(
            len(self.color) == 4 and all(0 <= c <= 255 for c in self.color),
            f"{self.id}: color must be 4 values in 0..255",
        )


LAYER_CLASSES: Final[Mapping[str, type[Layer]]] = MappingProxyType(
    {cls.kind: cls for cls in (RasterLayer, ColorbarLayer, GridSlot, PolygonSlot, TextSlot)}
)


def _refuse_setattr(self: Layer, name: str, value: object) -> None:
    raise ImmutableLayerError(edit_refusal(self, name))


def _refuse_delattr(self: Layer, name: str) -> None:
    raise ImmutableLayerError(edit_refusal(self, name))


# Frozen dataclasses raise a bare "cannot assign to field"; say what to do instead.
for _cls in (Layer, *LAYER_CLASSES.values()):
    _cls.__setattr__ = _refuse_setattr  # type: ignore[method-assign, assignment]
    _cls.__delattr__ = _refuse_delattr  # type: ignore[method-assign, assignment]


#: Buffer fields whose encoded bytes (kept from loading, by role) a change invalidates.
ENCODED_ROLES: Final = ("pixels", "index_map", "lut")


def evolve(layer: Layer, **changes: Any) -> Layer:
    """A copy of ``layer`` with ``changes``, validated, sharing unchanged buffers.

    A new ``pixels``, ``index_map`` or ``lut`` drops that buffer's encoded bytes kept
    from loading, so saving re-encodes it; everything else keeps its encoded bytes.
    """
    dropped = [role for role in ENCODED_ROLES if role in changes]
    if dropped:
        encoded = {k: v for k, v in layer._encoded.items() if k not in dropped}
        changes = {**changes, "_encoded": encoded}
    if "pixels" in changes:
        changes = {"width": None, "height": None, **changes}
    return dataclasses.replace(layer, **changes)
