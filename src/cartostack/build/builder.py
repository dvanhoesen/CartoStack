"""``SceneBuilder``: split a Matplotlib/Cartopy figure into ``.cstack`` layers and slots.

Every visible *unit* of the figure is assigned to exactly one layer:

* the map axes' children (features, collections, texts, the outline spine, ...);
* every other axes as a whole (e.g. a colorbar axes);
* figure-level artists (``fig.text``, figure images, legends, patches).

The figure and map-axes background patches belong to the static layer marked
``background=True`` (or nowhere: transparent). Units are assigned explicitly
(``artists=``), by the artists a ``draw`` callback creates, or by a zorder band
(``zorder=(lo, hi)``); slot artists (a preview of the data, the text artist) are hidden
from every static layer. Unassigned visible units are an error unless ``build(rest=...)``
names a layer for them, so nothing is lost or duplicated silently.

Each static layer is rendered alone with ``savefig`` at the output DPI and output crop,
then cropped to its ink. Layers composite back to the full render as long as their
stacking order follows Matplotlib's draw order; the builder warns when it does not.
"""

from __future__ import annotations

import dataclasses
import io
import itertools
import math
import re
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .. import __version__
from .. import colorbars as cbars
from .. import grids as cgrids
from .. import polygons as cpolygons
from .. import text as ctext
from ..layers import (
    DEFAULT_ENCODING,
    ColorbarLayer,
    ColorbarRedraw,
    GridSlot,
    Layer,
    PolygonSlot,
    RasterLayer,
    TextSlot,
    evolve,
)
from ..manifest import Bin
from ..scene import Scene
from .georef import Crop, canvas_geometry, display_to_canvas, resolve_crop
from .index_map import grid_shape
from .index_map import index_map as make_index_map

# 3.11.1: text slots reproduce 3.11 text layout, and colorbar redraw matches 3.11.1+ (Session 15)
MIN_MATPLOTLIB = (3, 11, 1)


class BuildError(ValueError):
    """The figure cannot be turned into a ``.cstack`` file as specified."""


@dataclass
class _Static:
    id: str
    order: float
    units: list[Any]
    zorder: tuple[float, float] | None
    background: bool
    opacity: float


@dataclass
class _Grid:
    id: str
    order: float
    lon: Any
    lat: Any
    cells: str
    cmap: Any
    vmin: float
    vmax: float
    alpha: float | None
    extend: str
    value_dtype: str
    values: Any
    units: list[Any] = field(default_factory=list)


@dataclass
class _Polygons:
    id: str
    order: float
    bins: tuple[Bin, ...]
    fallback: Bin | None
    round_decimals: int | None
    supersample: int
    records: Any
    values: Any
    units: list[Any] = field(default_factory=list)


@dataclass
class _Text:
    id: str
    order: float
    artist: Any


def _rgba(color: Any) -> tuple[int, int, int, int]:
    from matplotlib import colors as mcolors

    if isinstance(color, Sequence) and len(color) == 4 and all(isinstance(c, int) for c in color):
        return (int(color[0]), int(color[1]), int(color[2]), int(color[3]))
    r, g, b, a = mcolors.to_rgba(color)
    return (round(r * 255), round(g * 255), round(b * 255), round(a * 255))


def _bin(b: Any, *, bounds: bool = True) -> Bin:
    if isinstance(b, Bin):
        return b
    if isinstance(b, dict):
        lower, upper = (b.get("lower", 0.0), b.get("upper", 0.0)) if bounds else (0.0, 0.0)
        return Bin(float(lower), float(upper), _rgba(b["color"]), int(b["order"]))
    if bounds:
        lower, upper, color, order = b
        return Bin(float(lower), float(upper), _rgba(color), int(order))
    color, order = b
    return Bin(0.0, 0.0, _rgba(color), int(order))


def lut_from_cmap(cmap: Any) -> NDArray[np.uint8]:
    """``(N + 3, 4)`` uint8 LUT: the colours, then under, over, bad.

    Channels are rounded to 8 bits as Agg rounds them when it draws (``pcolormesh``),
    not truncated as ``cmap(..., bytes=True)`` does, so runtime grids match rendered maps.
    """
    import matplotlib as mpl

    if isinstance(cmap, str):
        cmap = mpl.colormaps[cmap]
    n = cmap.N
    rows = [
        cmap(np.arange(n)),
        cmap(np.array([-1.0])),
        cmap(np.array([2.0])),
        cmap(np.ma.masked_invalid(np.array([np.nan]))),
    ]
    return np.ascontiguousarray(np.rint(np.vstack(rows) * 255), dtype=np.uint8)


@dataclass
class _Colorbar:
    id: str
    order: float
    cbar: Any
    slot: str


_Spec = _Static | _Grid | _Polygons | _Text | _Colorbar


class SceneBuilder:
    """Author a ``.cstack`` scene from a Matplotlib figure with a (Cartopy) map axes.

    ``fig``/``ax`` are an existing figure and its map axes, e.g. from a script that
    already draws the map; ``SceneBuilder.new`` creates them. ``dpi`` is the output DPI
    (default: the figure's), ``crop`` the output crop: ``None`` (whole figure),
    ``"tight"`` (``bbox_inches="tight"``, resolved once), or ``(x0, y0, w, h)`` inches.
    """

    def __init__(
        self,
        fig: Any,
        ax: Any,
        *,
        dpi: float | None = None,
        crop: str | Crop | None = None,
        close: bool = False,
    ) -> None:
        import matplotlib

        version = tuple(
            int(re.match(r"\d*", p).group() or 0)  # type: ignore[union-attr]
            for p in matplotlib.__version__.split(".")[:3]
        )
        if version < MIN_MATPLOTLIB:
            raise BuildError(
                f"cartostack.build needs Matplotlib >= 3.11.1 (text slots reproduce its layout); "
                f"found {matplotlib.__version__}"
            )
        self.fig = fig
        self.ax = ax
        self.dpi = float(dpi if dpi is not None else fig.dpi)
        self._crop_spec = crop
        self._close = close
        self._statics: list[_Static] = []
        self._grids: list[_Grid] = []
        self._polygons: list[_Polygons] = []
        self._texts: list[_Text] = []
        self._colorbars: list[_Colorbar] = []
        self._fonts: dict[str, bytes] = {}

    @classmethod
    def new(
        cls,
        projection: Any,
        extent: Sequence[float],
        *,
        width: int,
        height: int,
        dpi: float = 100.0,
        axes: Sequence[float] = (0.0, 0.0, 1.0, 1.0),
        extent_crs: Any = None,
        facecolor: Any = "white",
        frame: bool = False,
        crop: str | Crop | None = None,
    ) -> SceneBuilder:
        """A builder with a new ``width`` by ``height`` px figure and one map axes.

        ``axes`` is the requested rectangle in figure fractions (Cartopy may shrink it to
        keep the map's aspect; the resolved rectangle is what the file records).
        ``frame=False`` hides the map outline; with ``True`` assign ``ax.spines["geo"]``.
        """
        import cartopy.crs as ccrs
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
        fig.patch.set_facecolor(facecolor)
        left, bottom, w, h = (float(v) for v in axes)
        ax: Any = fig.add_axes((left, bottom, w, h), projection=projection)
        ax.set_extent(list(extent), crs=extent_crs or ccrs.PlateCarree())
        if not frame:
            for spine in ax.spines.values():
                spine.set_visible(False)
        return cls(fig, ax, dpi=dpi, crop=crop, close=True)

    # --- Declaring layers --------------------------------------------------------------

    def _specs(self) -> list[_Spec]:
        return [*self._statics, *self._grids, *self._polygons, *self._texts, *self._colorbars]

    def _check_id(self, layer_id: str) -> None:
        taken = {la.id for la in self._specs()}
        if layer_id in taken:
            raise BuildError(f"layer id {layer_id!r} is already used")

    def add_static(
        self,
        layer_id: str,
        *,
        order: float,
        draw: Callable[[Any], Any] | None = None,
        artists: Iterable[Any] = (),
        zorder: tuple[float, float] | None = None,
        background: bool = False,
        opacity: float = 1.0,
    ) -> SceneBuilder:
        """A static raster layer: the artists ``draw(ax)`` creates, ``artists``, and/or the
        map-axes units with ``lo <= zorder < hi``. ``background=True`` adds the figure and
        map-axes background patches (one layer at most)."""
        self._check_id(layer_id)
        if background and any(s.background for s in self._statics):
            raise BuildError("only one static layer can hold the background")
        units = list(artists)
        if draw is not None:
            before = set(map(id, self._units()))
            draw(self.ax)
            units += [u for u in self._units() if id(u) not in before]
        self._statics.append(_Static(layer_id, float(order), units, zorder, background, opacity))
        return self

    def add_grid_slot(
        self,
        layer_id: str,
        *,
        order: float,
        lon: ArrayLike,
        lat: ArrayLike,
        cmap: Any,
        vmin: float | None = None,
        vmax: float | None = None,
        norm: Any = None,
        alpha: float | None = None,
        extend: str = "neither",
        cells: str = "centers",
        value_dtype: str = "float32",
        values: ArrayLike | None = None,
        artists: Iterable[Any] = (),
    ) -> SceneBuilder:
        """A grid slot for values on ``lon``/``lat`` (degrees; cell ``centers`` or ``edges``).

        The index map is made with the index-image method; the LUT is ``cmap``'s, with
        ``vmin``/``vmax`` (or a linear ``norm``). ``values`` renders initial pixels;
        ``artists`` (e.g. a preview ``pcolormesh``) are hidden from static layers.
        """
        self._check_id(layer_id)
        if norm is not None:
            from matplotlib.colors import Normalize

            # Format 1.0 grid slots store a linear range only (docs/format.md §8).
            if type(norm) is not Normalize or norm.clip:
                raise BuildError(
                    f"{layer_id}: only a linear Normalize without clip is supported, "
                    f"got {type(norm).__name__}{' with clip' if norm.clip else ''}"
                )
            vmin, vmax = norm.vmin, norm.vmax
        if vmin is None or vmax is None:
            raise BuildError(f"{layer_id}: give vmin and vmax (or a norm)")
        grid_shape(lon, lat, cells)  # validates the coordinates early
        self._grids.append(
            _Grid(
                layer_id,
                float(order),
                np.asarray(lon),
                np.asarray(lat),
                cells,
                cmap,
                float(vmin),
                float(vmax),
                alpha,
                extend,
                value_dtype,
                values,
                list(artists),
            )
        )
        return self

    def add_polygon_slot(
        self,
        layer_id: str,
        *,
        order: float,
        bins: Iterable[Any],
        fallback: Any = None,
        round_decimals: int | None = None,
        supersample: int = 4,
        records: Iterable[Any] | None = None,
        values: ArrayLike | None = None,
        artists: Iterable[Any] = (),
    ) -> SceneBuilder:
        """A polygon slot. ``bins`` are ``(lower, upper, color, order)`` (or ``Bin``); the
        ``fallback`` is ``(color, order)``. ``records``/``values`` render initial pixels;
        ``artists`` (the drawn data polygons) are hidden from static layers."""
        self._check_id(layer_id)
        fb = None if fallback is None else _bin(fallback, bounds=False)
        self._polygons.append(
            _Polygons(
                layer_id,
                float(order),
                tuple(_bin(b) for b in bins),
                fb,
                round_decimals,
                supersample,
                records,
                values,
                list(artists),
            )
        )
        return self

    def add_text_slot(
        self, layer_id: str, artist: Any = None, *, order: float, **text: Any
    ) -> SceneBuilder:
        """A text slot from a Matplotlib ``Text`` artist (its font, size, colour, anchor,
        alignment, and current string), or a new one: ``fig.text(**text)`` with ``x``/``y``
        in figure fractions and the other keywords as for ``Figure.text``."""
        self._check_id(layer_id)
        if artist is None:
            x, y = text.pop("x"), text.pop("y")
            artist = self.fig.text(x, y, text.pop("s", ""), **text)
        self._texts.append(_Text(layer_id, float(order), artist))
        return self

    # --- Units and assignment ------------------------------------------------------------

    def add_colorbar(
        self, layer_id: str, colorbar: Any, *, slot: str, order: float
    ) -> SceneBuilder:
        """A colorbar layer for grid slot ``slot`` from a Matplotlib ``Colorbar``.

        Its axes become the layer, plus what the runtime needs to redraw it when the
        slot's ``vmin``/``vmax`` or colormap change (``docs/format.md`` §7): the strip's
        LUT rows, the parts drawn under and over the strip, and the tick and label style.
        Redrawing needs Matplotlib's default ``AutoLocator``/``MaxNLocator`` or fixed
        ticks and a ``ScalarFormatter``; otherwise the colorbar is kept as drawn.
        """
        self._check_id(layer_id)
        if colorbar.ax is self.ax:
            raise BuildError(f"{layer_id}: the colorbar must have its own axes")
        self._colorbars.append(_Colorbar(layer_id, float(order), colorbar, slot))
        return self

    def _units(self) -> list[Any]:
        """Every unit that can be drawn: map-axes children, other axes, figure-level artists."""
        units = [c for c in self.ax.get_children() if c is not self.ax.patch]
        units += [a for a in self.fig.axes if a is not self.ax]
        for group in (
            self.fig.texts,
            self.fig.images,
            self.fig.lines,
            self.fig.patches,
            self.fig.artists,
            self.fig.legends,
        ):
            units += list(group)
        return units

    @staticmethod
    def _draws(unit: Any) -> bool:
        if not unit.get_visible():
            return False
        get_text = getattr(unit, "get_text", None)
        return not (callable(get_text) and isinstance(get_text(), str) and not get_text())

    def _rank(self, unit: Any) -> tuple[float, int, float, int]:
        """Matplotlib's draw order: figure children by (zorder, insertion), then axes children."""
        children = [c for c in self.fig.get_children() if c is not self.fig.patch]
        position = {id(c): (c.get_zorder(), i) for i, c in enumerate(children)}
        if any(unit is c for c in self.ax.get_children()):
            z, i = position[id(self.ax)]
            siblings = self.ax.get_children()
            index = next(k for k, c in enumerate(siblings) if c is unit)
            return (z, i, unit.get_zorder(), index)
        z, i = position.get(id(unit), (unit.get_zorder(), len(children)))
        return (z, i, 0.0, 0)

    def _assign(self, rest: str | None) -> dict[str, list[Any]]:
        statics = {s.id: s for s in self._statics}
        if rest is not None and rest not in statics:
            raise BuildError(f"rest={rest!r} is not a static layer")
        owner: dict[int, str] = {}

        def claim(unit: Any, layer_id: str) -> None:
            prev = owner.get(id(unit))
            if prev is not None and prev != layer_id:
                raise BuildError(f"{unit!r} is assigned to both {prev!r} and {layer_id!r}")
            owner[id(unit)] = layer_id

        slots: list[_Grid | _Polygons] = [*self._grids, *self._polygons]
        for slot in slots:
            for u in slot.units:
                claim(u, slot.id)
        for t in self._texts:
            claim(t.artist, t.id)
        for c in self._colorbars:
            claim(c.cbar.ax, c.id)
        for s in self._statics:
            for u in s.units:
                claim(u, s.id)
        drawn = [u for u in self._units() if self._draws(u)]
        for s in self._statics:
            if s.zorder is not None:
                lo, hi = s.zorder
                for u in drawn:
                    if id(u) not in owner and lo <= u.get_zorder() < hi:
                        owner[id(u)] = s.id
        missing = [u for u in drawn if id(u) not in owner]
        if missing and rest is None:
            names = ", ".join(f"{type(u).__name__}(zorder={u.get_zorder()})" for u in missing[:8])
            raise BuildError(
                f"{len(missing)} visible artist(s) are not assigned to a layer: {names}. "
                "Assign them with add_static(artists=..., draw=..., zorder=...), or pass "
                "build(rest=<static layer id>)."
            )
        for u in missing:
            owner[id(u)] = rest  # type: ignore[assignment]
        out: dict[str, list[Any]] = {}
        for u in self._units():
            if id(u) in owner:
                out.setdefault(owner[id(u)], []).append(u)
        return out

    def _check_order(self, groups: dict[str, list[Any]]) -> list[tuple[str, str, list[Any]]]:
        """Layers stacked against Matplotlib's draw order: ``(lower, upper, late units)``.

        The late units are the lower layer's artists that Matplotlib draws after the upper
        layer's first artist; only they can be misdrawn by stacking.
        """
        orders = {la.id: la.order for la in self._specs()}
        spans = []
        for layer_id, units in groups.items():
            ranks = [
                self._rank(u)
                for u in units
                if self._draws(u) or layer_id in {t.id for t in self._texts}
            ]
            if ranks:
                spans.append((orders[layer_id], layer_id, min(ranks), max(ranks)))
        spans.sort()
        problems = []
        for (_, a, _, a_max), (_, b, b_min, _) in itertools.pairwise(spans):
            if a_max > b_min:
                late = [u for u in groups[a] if self._draws(u) and self._rank(u) > b_min]
                problems.append((a, b, late))
        return problems

    @staticmethod
    def _overlap(a: Layer, b: Layer) -> bool:
        """Whether two placed layers have ink on a common pixel (unknown counts as overlap)."""
        if a.pixels is None or b.pixels is None:
            return a.kind in ("grid", "polygon") or b.kind in ("grid", "polygon")
        x0, y0 = max(a.left, b.left), max(a.top, b.top)
        x1 = min(a.left + a.pixels.shape[1], b.left + b.pixels.shape[1])
        y1 = min(a.top + a.pixels.shape[0], b.top + b.pixels.shape[0])
        if x1 <= x0 or y1 <= y0:
            return False
        ma = a.pixels[y0 - a.top : y1 - a.top, x0 - a.left : x1 - a.left, 3] > 0
        mb = b.pixels[y0 - b.top : y1 - b.top, x0 - b.left : x1 - b.left, 3] > 0
        return bool((ma & mb).any())

    # --- Rendering -----------------------------------------------------------------------

    def _render(
        self, show: Iterable[Any], background: bool, crop: Crop | None
    ) -> NDArray[np.uint8]:
        import matplotlib.pyplot as plt
        from matplotlib.transforms import Bbox
        from PIL import Image

        shown = set(map(id, show))
        units = self._units()
        original = {id(u): u.get_visible() for u in units}
        patches = {id(p): p.get_visible() for p in (self.fig.patch, self.ax.patch)}
        try:
            for u in units:
                u.set_visible(original[id(u)] and id(u) in shown)
            self.fig.patch.set_visible(background and patches[id(self.fig.patch)])
            self.ax.patch.set_visible(background and patches[id(self.ax.patch)])
            buf = io.BytesIO()
            kwargs = {"bbox_inches": Bbox.from_bounds(*crop), "pad_inches": 0} if crop else {}
            plt.figure(self.fig.number)
            self.fig.savefig(buf, format="png", dpi=self.dpi, transparent=not background, **kwargs)
        finally:
            for u in units:
                u.set_visible(original[id(u)])
            self.fig.patch.set_visible(patches[id(self.fig.patch)])
            self.ax.patch.set_visible(patches[id(self.ax.patch)])
        rgba = np.array(Image.open(buf).convert("RGBA"))
        rgba[rgba[..., 3] == 0] = 0  # canonical transparent pixels
        return rgba

    def _font(self, artist: Any) -> tuple[str, bytes]:
        from matplotlib import font_manager

        path = Path(font_manager.findfont(artist.get_fontproperties(), fallback_to_default=False))
        data = path.read_bytes()
        name = f"fonts/{path.name}"
        if name in self._fonts and self._fonts[name] != data:
            raise BuildError(f"two different fonts are named {path.name!r}")
        self._fonts[name] = data
        return name, data

    def _text_slot(self, t: _Text, to_canvas: Callable[[np.ndarray], np.ndarray]) -> TextSlot:
        from matplotlib import colors as mcolors

        artist = t.artist
        value = artist.get_text()
        if "\n" in value:
            raise BuildError(f"{t.id}: text slots hold one line")
        if artist.get_rotation() % 360:
            raise BuildError(f"{t.id}: rotated text is not supported in format 1.0")
        if artist.get_usetex() or (value.count("$") >= 2 and artist._parse_math):
            raise BuildError(
                f"{t.id}: mathtext/usetex cannot be rendered at runtime; set parse_math=False"
            )
        font, _ = self._font(artist)
        x, y = to_canvas(artist.get_transform().transform(artist.get_position()))[0]
        r, g, b, a = mcolors.to_rgba(artist.get_color(), artist.get_alpha())
        return TextSlot(
            id=t.id,
            order=t.order,
            value=value,
            font=font,
            size_px=artist.get_fontsize() * self.dpi / 72,
            color=(round(r * 255), round(g * 255), round(b * 255), round(a * 255)),
            x=float(x),
            y=float(y),
            ha=artist.get_horizontalalignment(),
            va=artist.get_verticalalignment(),
            snap=None,
            offset=(0.0, 0.0),
        )

    # --- Colorbars ------------------------------------------------------------------------

    def _render_cax(self, cax: Any, crop: Crop | None, *, patch: bool) -> NDArray[np.uint8]:
        """Render only ``cax`` (whatever of it is visible now), with or without its background."""
        visible = cax.patch.get_visible()
        try:
            cax.patch.set_visible(patch and visible)
            return self._render([cax], False, crop)
        finally:
            cax.patch.set_visible(visible)

    def _colorbar_layer(
        self,
        c: _Colorbar,
        crop: Crop | None,
        to_canvas: Callable[[np.ndarray], np.ndarray],
        canvas: tuple[int, int],
        encoding: str,
    ) -> ColorbarLayer:
        import matplotlib as mpl
        import matplotlib.ticker as mticker
        from matplotlib import colors as mcolors

        cbar, cax = c.cbar, c.cbar.ax
        full = self._render_cax(cax, crop, patch=True)
        ys, xs = np.nonzero(full[..., 3])
        if ys.size == 0:
            raise BuildError(f"{c.id}: the colorbar draws nothing")
        axis = cbar.long_axis
        tick = axis.get_major_ticks()[0]
        size_px = tick.label1.get_fontsize() * self.dpi / 72
        # Room for longer labels after a range change.
        margin = math.ceil(4 * size_px)
        top, left = max(int(ys.min()) - margin, 0), max(int(xs.min()) - margin, 0)
        bottom = min(int(ys.max()) + 1 + margin, canvas[0])
        right = min(int(xs.max()) + 1 + margin, canvas[1])

        def crop_box(a: NDArray[Any]) -> NDArray[Any]:
            return np.ascontiguousarray(a[top:bottom, left:right])

        pixels = crop_box(full)
        locator, formatter = axis.get_major_locator(), axis.get_major_formatter()
        auto = isinstance(locator, mticker.MaxNLocator)
        fixed = isinstance(locator, mticker.FixedLocator)
        if not (auto or fixed) or type(formatter) is not mticker.ScalarFormatter:
            warnings.warn(
                f"{c.id}: ticks cannot be recomputed at runtime; the colorbar is kept as drawn",
                stacklevel=3,
            )
            return ColorbarLayer(
                id=c.id,
                order=c.order,
                slot=c.slot,
                pixels=pixels,
                left=left,
                top=top,
                encoding=encoding,
            )
        # Under the strip: the axes background only.
        children = cax.get_children()
        shown = {id(ch): ch.get_visible() for ch in children}
        try:
            for ch in children:
                ch.set_visible(False)
            under = crop_box(self._render_cax(cax, crop, patch=True))
        finally:
            for ch in children:
                ch.set_visible(shown[id(ch)])
        # Over the strip: outline, dividers, label; no strip, ticks, or tick labels.
        strip = [cbar.solids, *cbar._extend_patches]
        # Ticks and labels stay laid out (the axis label keeps its place) but transparent.
        tick_color = tick.tick1line.get_color()
        label_color = tick.label1.get_color()
        axis_label = axis.label
        label_visible = axis_label.get_visible()
        try:
            for a in strip:
                a.set_visible(False)
            axis.set_tick_params(which="both", color=(0, 0, 0, 0), labelcolor=(0, 0, 0, 0))
            axis_label.set_visible(False)
            over = crop_box(self._render_cax(cax, crop, patch=False))
            # The axis label alone: the runtime moves it with the tick labels' outer edge.
            axis_label.set_visible(label_visible)
            for ch in cax.get_children():
                if ch is not axis.axes.xaxis and ch is not axis.axes.yaxis:
                    ch.set_visible(False)
            other = cax.yaxis if axis is cax.xaxis else cax.xaxis
            other_visible = other.get_visible()
            other.set_visible(False)
            label_px = crop_box(self._render_cax(cax, crop, patch=False))
            other.set_visible(other_visible)
        finally:
            for ch in children:
                ch.set_visible(shown[id(ch)])
            for a in strip:
                a.set_visible(True)
            axis_label.set_visible(label_visible)
            axis.set_tick_params(which="both", color=tick_color, labelcolor=label_color)
        rows = crop_box(self._strip_rows(c, cax, crop))
        box = cax.get_window_extent()
        (bx0, by0), (bx1, by1) = to_canvas(np.array([[box.x0, box.y1], [box.x1, box.y0]]))
        font, _ = self._font(tick.label1)
        dpi = self.dpi
        if auto:
            nbins = locator._nbins
            if nbins == "auto":
                nbins = int(np.clip(axis.get_tick_space(), max(1, locator._min_n_ticks - 1), 9))
            values: tuple[float, ...] = ()
            steps = tuple(float(v) for v in locator._steps)
        else:
            nbins, steps = 9, (1.0, 2.0, 2.5, 5.0, 10.0)
            values = tuple(float(v) for v in locator.locs)
        if cbar.orientation == "horizontal":
            side = axis.get_ticks_position()
            side = "top" if side == "top" else "bottom"
        else:
            side = "left" if axis.get_ticks_position() == "left" else "right"
        tick_rgba = mcolors.to_rgba(tick.tick1line.get_color())
        label_rgba = mcolors.to_rgba(tick.label1.get_color())
        redraw = ColorbarRedraw(
            orientation=cbar.orientation,
            box=(bx0 - left, by0 - top, bx1 - bx0, by1 - by0),
            rows=rows,
            n_colors=cbar.cmap.N,
            under=under,
            over=over,
            locator="auto" if auto else "fixed",
            nbins=int(nbins),
            steps=steps,
            values=values,
            side=side,
            direction=tick._tickdir,
            tick_length=tick.tick1line.get_markersize() * dpi / 72,
            tick_width=tick.tick1line.get_markeredgewidth() * dpi / 72,
            tick_color=_rgba(tick_rgba),
            font=font,
            size_px=size_px,
            label_color=_rgba(label_rgba),
            pad=tick.get_pad() * dpi / 72,
            minus="\u2212" if mpl.rcParams["axes.unicode_minus"] else "-",
        )
        if label_px[..., 3].any():
            grid_spec = next(g for g in self._grids if g.id == c.slot)
            locs, labels = cbars.visible_ticks(redraw, grid_spec.vmin, grid_spec.vmax)
            left_b, top_b, width_b, height_b = redraw.box
            span = grid_spec.vmax - grid_spec.vmin
            if redraw.orientation == "horizontal":
                positions = left_b + (locs - grid_spec.vmin) / span * width_b
            else:
                positions = top_b + height_b - (locs - grid_spec.vmin) / span * height_b
            edge = cbars.label_edge(redraw, positions, labels, self._fonts[font])
            redraw = dataclasses.replace(redraw, label=label_px, label_edge=edge)
        return ColorbarLayer(
            id=c.id,
            order=c.order,
            slot=c.slot,
            pixels=pixels,
            left=left,
            top=top,
            encoding=encoding,
            redraw=redraw,
        )

    def _strip_rows(self, c: _Colorbar, cax: Any, crop: Crop | None) -> NDArray[np.int32]:
        """Index image of the strip: each band's LUT row, under/over for the triangles."""
        from matplotlib.collections import QuadMesh

        cbar = c.cbar
        grid = next(g for g in self._grids if g.id == c.slot)
        n = cbar.cmap.N
        values = np.asarray(cbar._values, np.float64)[cbar._inside]
        probe = GridSlot(
            id="probe",
            order=0,
            shape=(1, max(values.size, 1)),
            index_map=np.zeros((1, 1), np.int32),
            lut=lut_from_cmap(cbar.cmap),
            vmin=grid.vmin,
            vmax=grid.vmax,
            value_dtype="float64",
        )
        band_rows = cgrids.lut_rows(probe, values.reshape(1, -1))
        codes = [(r + 1) for r in band_rows]  # 0 means "nothing drawn"
        solids = cbar.solids
        saved = (
            solids.get_facecolor(),
            solids.get_alpha(),
            solids.get_antialiased(),
            solids.get_array(),
        )
        patches = list(cbar._extend_patches)
        saved_p = [
            (p.get_facecolor(), p.get_alpha(), p.get_antialiased(), p.get_edgecolor())
            for p in patches
        ]
        outline_visible = cbar.outline.get_visible()
        restore_hidden: list[Any] = []

        def rgba(code: int) -> tuple[float, float, float, float]:
            return ((code >> 16 & 255) / 255, (code >> 8 & 255) / 255, (code & 255) / 255, 1.0)

        try:
            QuadMesh.set_array(solids, None)
            solids.set_alpha(None)
            solids.set_antialiased(False)
            solids.set_facecolor([rgba(code) for code in codes])
            lower, upper = cbar._extend_lower(), cbar._extend_upper()
            ext_codes = ([n + 1] if lower else []) + ([n + 2] if upper else [])
            for p, code in zip(patches, ext_codes, strict=True):
                p.set_alpha(None)
                p.set_antialiased(False)
                p.set_facecolor(rgba(code))
                p.set_edgecolor("none")
            cbar.outline.set_visible(False)
            for ch in cax.get_children():
                if ch is not solids and ch not in patches and ch.get_visible():
                    ch.set_visible(False)
                    restore_hidden.append(ch)
            img = self._render_cax(cax, crop, patch=False).astype(np.int32)
        finally:
            for ch in restore_hidden:
                ch.set_visible(True)
            QuadMesh.set_array(solids, saved[3])
            solids.set_facecolor(saved[0])
            solids.set_alpha(saved[1])
            solids.set_antialiased(saved[2])
            for p, (fc, al, aa, ec) in zip(patches, saved_p, strict=True):
                p.set_facecolor(fc)
                p.set_alpha(al)
                p.set_antialiased(aa)
                p.set_edgecolor(ec)
            cbar.outline.set_visible(outline_visible)
        code = (img[..., 0] << 16) | (img[..., 1] << 8) | img[..., 2]
        rows = np.where(img[..., 3] == 255, code - 1, -1).astype(np.int32)
        rows[(rows < -1) | (rows > n + 1)] = -1
        return rows

    # --- Output --------------------------------------------------------------------------

    def build(
        self,
        *,
        rest: str | None = None,
        encoding: str = DEFAULT_ENCODING,
        provenance: dict[str, Any] | None = None,
    ) -> Scene:
        """Render every layer and return the ``Scene`` (geometry resolved once, here)."""
        import cartopy
        import matplotlib

        fig = self.fig
        crop = resolve_crop(fig, self._crop_spec)
        geometry = canvas_geometry(fig, self.ax, dpi=self.dpi, crop=crop)
        to_canvas = display_to_canvas(fig, self.dpi, crop)
        groups = self._assign(rest)
        misordered = self._check_order(groups)
        layers: list[Layer] = []
        canvas = (geometry.height, geometry.width)
        for s in self._statics:
            rgba = self._render(groups.get(s.id, []), s.background, crop)
            if rgba.shape[:2] != canvas:
                raise BuildError(
                    f"rendered {rgba.shape[1]}x{rgba.shape[0]} px, expected "
                    f"{geometry.width}x{geometry.height}"
                )
            ys, xs = np.nonzero(rgba[..., 3])
            if ys.size == 0:
                warnings.warn(f"static layer {s.id!r} draws nothing", stacklevel=2)
                left = top = 0
                pixels = np.zeros((1, 1, 4), np.uint8)
            else:
                top, left = int(ys.min()), int(xs.min())
                pixels = rgba[top : ys.max() + 1, left : xs.max() + 1]
            layers.append(
                RasterLayer(
                    id=s.id,
                    order=s.order,
                    pixels=pixels,
                    left=left,
                    top=top,
                    opacity=s.opacity,
                    encoding=encoding,
                )
            )
        ax = self.ax
        g = geometry.axes
        clip = (
            max(g.left, 0.0),
            max(g.top, 0.0),
            min(g.right, geometry.width),
            min(g.bottom, geometry.height),
        )
        for grid in self._grids:
            imap, partial = make_index_map(
                fig,
                ax,
                grid.lon,
                grid.lat,
                cells=grid.cells,
                dpi=self.dpi,
                crop=crop,
                hide=self._units(),
                clip=clip,
            )
            if partial:
                warnings.warn(
                    f"{grid.id}: {partial} partly covered pixels left as no data", stacklevel=2
                )
            slot = GridSlot(
                id=grid.id,
                order=grid.order,
                shape=grid_shape(grid.lon, grid.lat, grid.cells),
                index_map=imap,
                lut=lut_from_cmap(grid.cmap),
                vmin=grid.vmin,
                vmax=grid.vmax,
                alpha=grid.alpha,
                extend=grid.extend,
                value_dtype=grid.value_dtype,
                cells=grid.cells,
                encoding=encoding,
            )
            if grid.values is not None:
                slot = evolve(slot, pixels=cgrids.render(slot, grid.values))  # type: ignore[assignment]
            layers.append(slot)
        for poly in self._polygons:
            slot_p = PolygonSlot(
                id=poly.id,
                order=poly.order,
                bins=poly.bins,
                fallback=poly.fallback,
                round_decimals=poly.round_decimals,
                supersample=poly.supersample,
                encoding=encoding,
            )
            if poly.records is not None:
                px = cpolygons.render(slot_p, geometry, poly.records, poly.values)
                slot_p = evolve(slot_p, pixels=px)  # type: ignore[assignment]
            layers.append(slot_p)
        grid_ids = {g.id: g for g in self._grids}
        for c in self._colorbars:
            if c.slot not in grid_ids:
                raise BuildError(f"{c.id}: slot {c.slot!r} is not a grid slot of this builder")
            layers.append(self._colorbar_layer(c, crop, to_canvas, canvas, encoding))
        for t in self._texts:
            slot_t = self._text_slot(t, to_canvas)
            left, top, px_t = ctext.render(slot_t, self._fonts[slot_t.font], canvas)
            if px_t is not None:
                slot_t = evolve(slot_t, left=left, top=top, pixels=px_t)  # type: ignore[assignment]
            layers.append(slot_t)
        prov = {
            "cartostack": __version__,
            "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "authoring": {
                "tool": "cartostack.build.SceneBuilder",
                "matplotlib": matplotlib.__version__,
                "cartopy": cartopy.__version__,
            },
            **(provenance or {}),
        }
        by_id = {la.id: la for la in layers}
        for lower, upper, late in misordered:
            rgba = self._render(late, False, crop)
            ys, xs = np.nonzero(rgba[..., 3])
            if ys.size == 0:
                continue
            late_layer = RasterLayer(
                id="late",
                order=0,
                left=int(xs.min()),
                top=int(ys.min()),
                pixels=rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1],
            )
            if self._overlap(late_layer, by_id[upper]):
                warnings.warn(
                    "layer order differs from Matplotlib's draw order where they overlap: "
                    f"{lower!r} stacks below {upper!r} but has artists Matplotlib draws after it",
                    stacklevel=2,
                )
        scene = Scene(geometry, layers, assets=dict(self._fonts), provenance=prov)
        if self._close:
            import matplotlib.pyplot as plt

            plt.close(fig)
        return scene

    def save(self, path: str | Path, **kwargs: Any) -> Scene:
        """``build(**kwargs)`` and save the scene to ``path``; returns the scene."""
        scene = self.build(**kwargs)
        scene.save(path)
        return scene


__all__ = ["BuildError", "SceneBuilder", "lut_from_cmap"]
