"""``Scene``: a ``.cstack`` file in memory: load, edit, render, and save.

Layers are immutable; every edit builds the new layer list, validates it against
the scene's geometry, and only then swaps it in, so a failed edit leaves the scene
unchanged. Untouched layer objects are kept as they are (no decode, copy, or
re-encode), which is also how the compositor recognises unchanged runs.
"""

from __future__ import annotations

import functools
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

import numpy as np
from numpy.typing import ArrayLike, NDArray

from . import __version__, grids
from . import colorbars as cbars
from . import io as cio
from . import manifest as mf
from . import polygons as poly
from . import text as ctext
from .errors import CartoStackError, FormatError
from .geometry import CanvasGeometry
from .layers import (
    DEFAULT_ENCODING,
    EDITABLE_FIELDS,
    ColorbarLayer,
    ColorbarRedraw,
    GridSlot,
    Layer,
    LayerError,
    PolygonSlot,
    RasterLayer,
    TextSlot,
    edit_refusal,
    evolve,
)

if TYPE_CHECKING:
    from PIL import Image

    from .compositor import Compositor

_EXT: Final = {
    "rgba8": "rgba8",
    "rgba8+zlib": "rgba8.zz",
    "png": "png",
    "i32le": "i32",
    "i32le+zlib": "i32.zz",
}
_TOP_KEYS: Final = frozenset(
    {
        "format",
        "format_version",
        "geometry",
        "geometry_fingerprint",
        "members",
        "layers",
        "provenance",
    }
)
_GEOMETRY_KEYS: Final = frozenset({"canvas", "axes", "clip", "figure", "georeference"})
_LAYER_KEYS: Final = frozenset(
    {
        "id",
        "kind",
        "order",
        "left",
        "top",
        "width",
        "height",
        "visible",
        "opacity",
        "member",
        "encoding",
    }
)
_SECTION_KEYS: Final[Mapping[str, frozenset[str]]] = {
    "colorbar": frozenset({"slot", "redraw"}),
    "grid": frozenset({"shape", "cells", "index_map", "value_dtype", "norm", "colormap", "alpha"}),
    "polygon": frozenset(
        {"bins", "interval", "round_decimals", "fallback", "fill_rule", "supersample"}
    ),
    "text": frozenset(
        {
            "value",
            "font",
            "size_px",
            "color",
            "x",
            "y",
            "ha",
            "va",
            "layout",
            "snap",
            "offset",
            "rotation",
        }
    ),
}


class SceneError(CartoStackError, ValueError):
    """Layers are inconsistent with each other or with the scene's geometry."""


class TextValues(Mapping[str, str]):
    """``scene.text``: the current value of each text slot, by id; assignment re-renders it."""

    def __init__(self, scene: Scene) -> None:
        self._scene = scene

    def _slots(self) -> list[TextSlot]:
        return [la for la in self._scene.draw_order() if isinstance(la, TextSlot)]

    def __getitem__(self, layer_id: str) -> str:
        layer = self._scene[layer_id]
        if not isinstance(layer, TextSlot):
            raise KeyError(f"{layer_id!r} is a {layer.kind} layer, not a text slot")
        return layer.value

    def __setitem__(self, layer_id: str, value: str) -> None:
        self._scene.replace_text(layer_id, value)

    def __delitem__(self, layer_id: str) -> None:
        raise TypeError(
            "text slots cannot be deleted through scene.text; set an empty string to clear one, "
            f"or use scene.remove_layer({layer_id!r})"
        )

    def __iter__(self) -> Iterator[str]:
        return iter([la.id for la in self._slots()])

    def __len__(self) -> int:
        return len(self._slots())

    def __repr__(self) -> str:
        return f"TextValues({dict(self)!r})"


class Scene:
    """A canvas geometry and its stack of layers, plus embedded assets (fonts, ...).

    Construction validates that every layer fits the geometry. The geometry is
    fixed; layers are edited with ``replace_layer`` (or ``scene[id] = ...``),
    ``add_layer``, ``remove_layer`` (or ``del scene[id]``), ``update_layer``,
    ``show`` and ``hide``. ``Scene.save`` writes atomically and deterministically.

    Lifetime: a scene is plain in-memory data. ``Scene.load`` decodes everything eagerly
    and keeps no file open; ``close()`` (or leaving ``with Scene.load(...)``) releases
    nothing and the scene stays usable. Saving re-encodes only pixels that are new since
    they were loaded or last saved.
    """

    def __init__(
        self,
        geometry: CanvasGeometry,
        layers: Iterable[Layer] = (),
        *,
        assets: Mapping[str, bytes] | None = None,
        provenance: Mapping[str, Any] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self._geometry = geometry
        self._layers = tuple(layers)
        self._assets: Mapping[str, bytes] = MappingProxyType(dict(assets or {}))
        prov = {"cartostack": __version__} if provenance is None else dict(provenance)
        self._provenance: Mapping[str, Any] = MappingProxyType(prov)
        self._extra: Mapping[str, Any] = MappingProxyType(dict(extra or {}))
        self._compositor: Compositor | None = None
        self._stale_colorbars: set[str] = set()
        problems = self._problems(self._layers)
        if problems:
            raise SceneError("; ".join(problems))

    # --- Access ----------------------------------------------------------------------

    @property
    def geometry(self) -> CanvasGeometry:
        return self._geometry

    @property
    def layers(self) -> tuple[Layer, ...]:
        return self._layers

    @property
    def assets(self) -> Mapping[str, bytes]:
        return self._assets

    @property
    def provenance(self) -> Mapping[str, Any]:
        return self._provenance

    @property
    def shape(self) -> tuple[int, int]:
        """Canvas ``(height, width)``."""
        return (self._geometry.height, self._geometry.width)

    def __getitem__(self, layer_id: str) -> Layer:
        return self._layers[self._index(layer_id)]

    def __setitem__(self, layer_id: str, new: Layer | ArrayLike) -> None:
        self.replace_layer(layer_id, new)

    def __delitem__(self, layer_id: str) -> None:
        self.remove_layer(layer_id)

    def _index(self, layer_id: str) -> int:
        for i, layer in enumerate(self._layers):
            if layer.id == layer_id:
                return i
        raise KeyError(f"no layer {layer_id!r}; layers are {[la.id for la in self._layers]}")

    def __contains__(self, layer_id: object) -> bool:
        return any(layer.id == layer_id for layer in self._layers)

    def __iter__(self) -> Iterator[Layer]:
        return iter(self._layers)

    def __len__(self) -> int:
        return len(self._layers)

    def __enter__(self) -> Scene:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the scene's resources. There are none to release: see "Lifetime" above.

        ``Scene.load`` reads, verifies and decodes the whole archive and closes the file
        before it returns, so a scene never holds a file open and stays fully usable
        after ``close()`` (and after its file is moved, deleted, or overwritten, including
        by ``save`` to the same path). ``close`` and ``with Scene.load(...)`` exist so code
        written today keeps working if a later version loads lazily.
        """

    def draw_order(self) -> tuple[Layer, ...]:
        """Bottom to top: ascending ``order``, ties in list order."""
        ranked = sorted(enumerate(self._layers), key=lambda item: (item[1].order, item[0]))
        return tuple(layer for _, layer in ranked)

    def __repr__(self) -> str:
        ids = ", ".join(f"{layer.id}:{layer.kind}" for layer in self.draw_order())
        return f"Scene({self._geometry.width}x{self._geometry.height}, [{ids}])"

    # --- Editing ---------------------------------------------------------------------

    def replace_layer(self, layer_id: str, new: Layer | ArrayLike) -> Layer:
        """Replace layer ``layer_id`` and return the new layer object.

        ``new`` is either a layer with the same id (any kind, size, and placement), or
        an RGBA array for a raster or colorbar layer, which keeps every other field and
        must have the layer's ``(height, width)``. Slots get new data through their
        own methods, not raw pixels.
        """
        i = self._index(layer_id)
        old = self._layers[i]
        if isinstance(new, Layer):
            if new.id != layer_id:
                raise SceneError(
                    f"replacement layer id {new.id!r} does not match {layer_id!r}; "
                    "use remove_layer and add_layer to rename"
                )
            layer = new
        else:
            if not isinstance(old, RasterLayer):
                raise SceneError(
                    f"{old.kind} layer {layer_id!r} is a slot: its pixels are rendered from "
                    "its data, so supply new data (or a whole new layer), not raw pixels"
                )
            pixels = np.asarray(new)
            size = old.size(self.shape)
            if pixels.shape[:2] != size:
                raise SceneError(
                    f"{layer_id}: new pixels are {pixels.shape[0]}x{pixels.shape[1]} "
                    f"(height x width), the layer is {size[0]}x{size[1]}; pass a new layer "
                    "object to change its size or placement"
                )
            layer = evolve(old, pixels=pixels)
        self._commit((*self._layers[:i], layer, *self._layers[i + 1 :]))
        self._stale_colorbars.discard(layer_id)
        return layer

    def add_layer(
        self,
        layer: Layer | str,
        pixels: ArrayLike | None = None,
        *,
        order: float | None = None,
        left: int = 0,
        top: int = 0,
        visible: bool = True,
        opacity: float = 1.0,
        encoding: str = DEFAULT_ENCODING,
    ) -> Layer:
        """Add a layer and return it.

        Either pass a layer object, or an id and RGBA ``pixels`` (any size, placed at
        ``left``/``top`` and intersecting the canvas) to add a raster layer. ``order``
        defaults to above every existing layer; a tie draws after existing layers.
        """
        if isinstance(layer, Layer):
            if pixels is not None or order is not None:
                raise TypeError("pass either a layer object, or an id with pixels and options")
            new = layer
        else:
            if pixels is None:
                raise TypeError(f"add_layer({layer!r}, ...) needs RGBA pixels")
            if order is None:
                order = max((la.order for la in self._layers), default=-1.0) + 1.0
            new = RasterLayer(
                id=layer,
                order=order,
                pixels=np.asarray(pixels),
                left=left,
                top=top,
                visible=visible,
                opacity=opacity,
                encoding=encoding,
            )
        self._commit((*self._layers, new))
        return new

    def remove_layer(self, layer_id: str) -> Layer:
        """Remove layer ``layer_id`` and return it. Nothing is rendered."""
        i = self._index(layer_id)
        removed = self._layers[i]
        self._commit(self._layers[:i] + self._layers[i + 1 :])
        self._stale_colorbars.discard(layer_id)
        return removed

    def update_layer(self, layer_id: str, **changes: Any) -> Layer:
        """Change stacking or placement fields and return the new layer object.

        Accepts ``order``, ``left``, ``top``, ``visible``, ``opacity`` and
        ``encoding`` (the storage encoding of its pixels). Pixels are shared with the
        previous object, not copied or re-encoded. Anything else is refused with an
        explanation, for example styling that the file does not store.
        """
        i = self._index(layer_id)
        old = self._layers[i]
        refused = [edit_refusal(old, name) for name in changes if name not in EDITABLE_FIELDS]
        if refused:
            raise SceneError("; ".join(refused))
        layer = evolve(old, **changes)
        self._commit((*self._layers[:i], layer, *self._layers[i + 1 :]))
        return layer

    def show(self, layer_id: str) -> Layer:
        return self.update_layer(layer_id, visible=True)

    def hide(self, layer_id: str) -> Layer:
        """Hide a layer; its pixels stay in the scene and in saved files."""
        return self.update_layer(layer_id, visible=False)

    # --- Slot data -------------------------------------------------------------------

    def replace_grid(
        self,
        layer_id: str,
        values: ArrayLike,
        *,
        vmin: float | None = None,
        vmax: float | None = None,
        lut: ArrayLike | None = None,
    ) -> GridSlot:
        """Render new values into grid slot ``layer_id``; return the new slot.

        ``values`` must have the slot's ``shape`` ``(ny, nx)`` and a real numeric dtype;
        NaN and masked values take the ``bad`` colour (``docs/format.md`` §8). ``vmin``,
        ``vmax`` and ``lut`` (``(n_colors + 3, 4)`` uint8: colours, then under, over,
        bad) change the normalisation or colormap for this and later renders; colorbars
        drawn from this slot are redrawn when the file holds their redraw data, and are
        otherwise listed in ``stale_colorbars`` until they are replaced. Only this slot
        (and such colorbars) is re-rendered; on any error (``GridMismatchError``,
        ``LayerError``) the scene is unchanged.
        """
        i = self._index(layer_id)
        old = self._layers[i]
        if not isinstance(old, GridSlot):
            raise SceneError(f"{old.kind} layer {layer_id!r} is not a grid slot")
        changes: dict[str, Any] = {}
        if vmin is not None:
            changes["vmin"] = vmin
        if vmax is not None:
            changes["vmax"] = vmax
        if lut is not None:
            changes["lut"] = np.asarray(lut)
        styled = evolve(old, **changes) if changes else old
        assert isinstance(styled, GridSlot)
        layer = evolve(styled, pixels=grids.render(styled, values))
        assert isinstance(layer, GridSlot)
        restyled = (
            styled.vmin != old.vmin
            or styled.vmax != old.vmax
            or not np.array_equal(styled.lut, old.lut)
        )
        new_layers = [*self._layers[:i], layer, *self._layers[i + 1 :]]
        redrawn: set[str] = set()
        stale: set[str] = set()
        if restyled:
            for k, la in enumerate(new_layers):
                if not isinstance(la, ColorbarLayer) or la.slot != layer_id:
                    continue
                try:
                    pixels = self._colorbar_pixels(la, layer)
                except cbars.ColorbarRedrawError:
                    stale.add(la.id)
                    continue
                new_layers[k] = evolve(la, pixels=pixels)
                redrawn.add(la.id)
        self._commit(tuple(new_layers))
        self._stale_colorbars = (self._stale_colorbars - redrawn) | stale
        return layer

    def _colorbar_pixels(self, layer: ColorbarLayer, slot: GridSlot) -> NDArray[np.uint8]:
        if layer.redraw is None:
            raise cbars.ColorbarRedrawError(f"{layer.id}: the file holds no redraw data for it")
        return cbars.render(layer, slot, self._assets.get(layer.redraw.font))

    def redraw_colorbar(self, layer_id: str) -> ColorbarLayer:
        """Redraw colorbar ``layer_id`` for its grid slot's current normalisation and colormap.

        ``replace_grid`` does this automatically when it changes ``vmin``/``vmax`` or the
        LUT. Raises ``ColorbarRedrawError`` when the file holds no redraw data for the
        colorbar or the range needs an offset/scientific label (``docs/format.md`` §7).
        """
        i = self._index(layer_id)
        old = self._layers[i]
        if not isinstance(old, ColorbarLayer):
            raise SceneError(f"{old.kind} layer {layer_id!r} is not a colorbar")
        slot = self[old.slot]
        assert isinstance(slot, GridSlot)
        layer = evolve(old, pixels=self._colorbar_pixels(old, slot))
        assert isinstance(layer, ColorbarLayer)
        self._commit((*self._layers[:i], layer, *self._layers[i + 1 :]))
        self._stale_colorbars.discard(layer_id)
        return layer

    @property
    def stale_colorbars(self) -> tuple[str, ...]:
        """Colorbars whose grid slot's normalisation or colormap changed after they were drawn.

        Their pixels no longer match the data; replace them (``replace_layer``) or hide
        them. Runtime colorbar redrawing is Session 11.
        """
        return tuple(sorted(self._stale_colorbars & {la.id for la in self._layers}))

    def replace_text(self, layer_id: str, value: str) -> TextSlot:
        """Render ``value`` into text slot ``layer_id`` with its embedded font; return the new slot.

        The anchor, alignment, size, and colour stay as stored (``docs/format.md`` §10);
        only the text and its pixels (cropped, at a new ``left``/``top``) change. Raises
        ``TextFontError`` when the slot's font is missing or unusable (there is no
        fallback font), ``TypeError``/``ValueError`` for anything but one line of text.
        ``scene.text[layer_id] = value`` does the same.
        """
        i = self._index(layer_id)
        old = self._layers[i]
        if not isinstance(old, TextSlot):
            raise SceneError(f"{old.kind} layer {layer_id!r} is not a text slot")
        if not isinstance(value, str):
            raise TypeError(f"{layer_id}: text must be a str, got {type(value).__name__}")
        left, top, pixels = ctext.render(old, self._assets.get(old.font), self.shape, value)
        layer = evolve(old, value=value, left=left, top=top, pixels=pixels)
        assert isinstance(layer, TextSlot)
        self._commit((*self._layers[:i], layer, *self._layers[i + 1 :]))
        return layer

    @property
    def text(self) -> TextValues:
        """The text slots' values: read ``scene.text[id]``, replace with ``scene.text[id] = s``."""
        return TextValues(self)

    def replace_polygons(
        self, layer_id: str, records: Iterable[object], values: ArrayLike
    ) -> PolygonSlot:
        """Render new classified polygons into polygon slot ``layer_id``; return the new slot.

        ``records`` holds one entry per polygon: its rings of ``(lon, lat)`` degrees
        (each an ``(n, 2)`` array-like; a single ring may be passed on its own), and
        ``values`` one number per record, classified by the slot's bins
        (``docs/format.md`` §9). ``cartostack.polygons.records_from_features`` converts
        GeoJSON-like features. Only this slot is re-rendered; on any error
        (``PolygonDataError``, ``ProjectionError``) the scene is unchanged.
        """
        i = self._index(layer_id)
        old = self._layers[i]
        if not isinstance(old, PolygonSlot):
            raise SceneError(f"{old.kind} layer {layer_id!r} is not a polygon slot")
        layer = evolve(old, pixels=poly.render(old, self._geometry, records, values))
        assert isinstance(layer, PolygonSlot)
        self._commit((*self._layers[:i], layer, *self._layers[i + 1 :]))
        return layer

    def _commit(self, layers: tuple[Layer, ...]) -> None:
        problems = self._problems(layers)
        if problems:
            raise SceneError("; ".join(problems))
        self._layers = layers

    # --- Validation ------------------------------------------------------------------

    def _problems(self, layers: tuple[Layer, ...]) -> list[str]:
        problems: list[str] = []
        seen: set[str] = set()
        grids = {layer.id for layer in layers if isinstance(layer, GridSlot)}
        g = self._geometry
        for layer in layers:
            if not isinstance(layer, Layer) or not layer.kind:
                problems.append(f"{layer!r} is not a layer")
                continue
            if layer.id in seen:
                problems.append(f"duplicate layer id {layer.id!r}")
            seen.add(layer.id)
            height, width = layer.size(self.shape)
            if (
                layer.left >= g.width
                or layer.top >= g.height
                or layer.left + width <= 0
                or layer.top + height <= 0
            ):
                problems.append(f"{layer.id}: placement does not intersect the canvas")
            if isinstance(layer, ColorbarLayer) and layer.slot not in grids:
                problems.append(f"{layer.id}: colorbar slot {layer.slot!r} is not a grid layer")
            if isinstance(layer, TextSlot) and layer.font not in self._assets:
                problems.append(f"{layer.id}: font {layer.font!r} is not among the scene assets")
            if isinstance(layer, PolygonSlot):
                geo = g.georeference
                if geo is None or not geo.projection.supported:
                    problems.append(
                        f"{layer.id}: polygon slots need a georeference with a supported projection"
                    )
        for name, data in self._assets.items():
            if not isinstance(data, bytes):
                problems.append(f"asset {name!r} must be bytes")
        return problems

    # --- Output ----------------------------------------------------------------------

    def render(self, *, flatten: bool = True) -> NDArray[np.uint8]:
        """Composite the visible layers into a new ``(height, width, 4)`` uint8 RGBA array.

        Layers stack source-over in draw order (``docs/format.md`` §6.2). With
        ``flatten`` (default), contiguous runs of static layers are flattened once and
        reused by later renders; ``flatten=False`` stacks every layer individually.
        Slots without stored pixels draw nothing until new data is supplied.
        """
        return np.array(self._render_image(flatten=flatten), dtype=np.uint8)

    def save_png(
        self,
        path: cio.PathLike,
        *,
        mode: str = "RGBA",
        compress_level: int = 6,
        background: tuple[int, int, int] = (255, 255, 255),
        flatten: bool = True,
    ) -> None:
        """Render and write a PNG of the canvas size atomically.

        ``mode`` is ``"RGBA"`` or ``"RGB"``; RGB composites the image over the opaque
        ``background`` first. ``compress_level`` is zlib's 0 (fastest) to 9 (smallest).
        """
        from .compositor import encode_png

        image = self._render_image(flatten=flatten)
        data = encode_png(image, mode=mode, compress_level=compress_level, background=background)
        with cio.atomic_file(path) as fh:
            fh.write(data)

    def _render_image(self, *, flatten: bool) -> Image.Image:
        from .compositor import Compositor

        if self._compositor is None:
            self._compositor = Compositor(self.shape)
        return self._compositor.render(self.draw_order(), flatten=flatten)

    # --- Persistence -----------------------------------------------------------------

    @classmethod
    def load(cls, path: cio.PathLike) -> Scene:
        """Eagerly read, verify, and decode a ``.cstack`` file.

        Raises ``UnsupportedVersionError`` for another major version and
        ``FormatError`` (listing problems) for invalid, corrupt, or modified files.
        """
        raw, members = cio.read_archive(path)
        try:
            man = mf.parse_manifest(raw)
        except FormatError as exc:
            raise type(exc)([f"{path}: {p}" for p in exc.problems]) from None
        used: set[str] = set()
        layers = []
        for raw_layer, rec in zip(raw["layers"], man.layers, strict=True):
            try:
                layers.append(_layer_from_record(rec, raw_layer, members, used, man))
            except LayerError as exc:
                raise FormatError(f"{path}: layer {rec.id!r}: {exc}") from exc
        assets = {name: data for name, data in members.items() if name not in used}
        extra = {k: val for k, val in raw.items() if k not in _TOP_KEYS}
        geometry_extra = {k: val for k, val in raw["geometry"].items() if k not in _GEOMETRY_KEYS}
        if geometry_extra:
            extra["geometry"] = geometry_extra
        try:
            return cls(
                man.geometry, layers, assets=assets, provenance=man.provenance or {}, extra=extra
            )
        except SceneError as exc:
            raise FormatError(f"{path}: {exc}") from exc

    def save(self, path: cio.PathLike, *, encoding: str | None = None) -> None:
        """Write the scene atomically.

        ``encoding`` re-encodes every pixel member (``rgba8+zlib``, ``rgba8``,
        ``png``); by default each layer keeps its own encoding, and members read
        from a file are written back unchanged.
        """
        manifest, members = self.to_manifest(encoding=encoding)
        cio.write_archive(path, cio.dumps_manifest(manifest), members)

    def to_manifest(
        self, *, encoding: str | None = None
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        """The manifest dict and member bytes that ``save`` would write (validated)."""
        if encoding is not None and encoding not in mf.RASTER_ENCODINGS:
            raise ValueError(f"unknown encoding {encoding!r}")
        members: dict[str, bytes] = dict(self._assets)
        records = []
        for layer in self._layers:
            records.append(_record(layer, encoding, members, self.shape, set(self._assets)))
        geometry = self._geometry.to_dict()
        geometry.update(self._extra.get("geometry", {}))
        manifest: dict[str, Any] = {
            "format": mf.FORMAT_NAME,
            "format_version": f"{mf.FORMAT_VERSION[0]}.{mf.FORMAT_VERSION[1]}",
            "geometry": geometry,
            "geometry_fingerprint": self._geometry.fingerprint(),
            "members": {
                name: {"bytes": len(members[name]), "sha256": cio.sha256(members[name])}
                for name in sorted(members)
            },
            "layers": records,
            "provenance": dict(self._provenance),
        }
        for key in sorted(self._extra):
            if key != "geometry":
                manifest[key] = self._extra[key]
        mf.parse_manifest(manifest)  # never write a file this library could not read
        return manifest, members


# --- Records ↔ layers ----------------------------------------------------------------


def _member(
    members: dict[str, bytes], assets: set[str], name: str, data: bytes, layer_id: str
) -> str:
    if name in assets:
        raise SceneError(f"{layer_id}: member name {name!r} collides with an asset")
    members[name] = data
    return name


# Encoded bytes of read-only (owned) buffers, by identity, while the buffer is alive. Owned
# buffers never change, so an edited scene re-encodes only buffers that are new since the
# last save (Session 12). Entries go away with their buffer.
_ENCODE_CACHE: dict[int, tuple[weakref.ref[Any], str, bytes]] = {}


def _cached_encode(buffer: NDArray[Any], encoding: str, encode: Callable[[], bytes]) -> bytes:
    key = id(buffer)
    hit = _ENCODE_CACHE.get(key)
    if hit is not None and hit[0]() is buffer and hit[1] == encoding:
        return hit[2]
    data = encode()
    if not buffer.flags.writeable:

        def forget(ref: weakref.ref[Any], key: int = key) -> None:
            entry = _ENCODE_CACHE.get(key)
            if entry is not None and entry[0] is ref:
                del _ENCODE_CACHE[key]

        _ENCODE_CACHE[key] = (weakref.ref(buffer, forget), encoding, data)
    return data


def _encoded(
    layer: Layer, role: str, encoding: str, buffer: NDArray[Any], encode: Callable[[], bytes]
) -> bytes:
    """Bytes kept from loading, else cached from an earlier save, else freshly encoded."""
    cached = layer._encoded.get(role)
    if cached is not None and cached[0] == encoding:
        return cached[1]
    return _cached_encode(buffer, encoding, encode)


def _record(
    layer: Layer,
    encoding: str | None,
    members: dict[str, bytes],
    canvas: tuple[int, int],
    assets: set[str],
) -> dict[str, Any]:
    height, width = layer.size(canvas)
    rec: dict[str, Any] = {
        "id": layer.id,
        "kind": layer.kind,
        "order": layer.order,
        "left": layer.left,
        "top": layer.top,
        "width": width,
        "height": height,
        "visible": layer.visible,
        "opacity": layer.opacity,
    }
    if layer.pixels is not None:
        enc = encoding or layer.encoding
        pixels = layer.pixels
        data = _encoded(layer, "pixels", enc, pixels, lambda: cio.encode_rgba(pixels, enc))
        rec["member"] = _member(members, assets, f"layers/{layer.id}.{_EXT[enc]}", data, layer.id)
        rec["encoding"] = enc
    section = _section(layer, members, assets)
    if section is not None:
        section.update({k: val for k, val in sorted(layer.extra.get(layer.kind, {}).items())})
        rec[layer.kind] = section
    for key in sorted(layer.extra):
        if key != layer.kind:
            rec[key] = layer.extra[key]
    return rec


def _section(layer: Layer, members: dict[str, bytes], assets: set[str]) -> dict[str, Any] | None:
    if isinstance(layer, ColorbarLayer):
        out: dict[str, Any] = {"slot": layer.slot}
        if layer.redraw is not None:
            out["redraw"] = _redraw_section(layer.id, layer.redraw, members, assets)
        return out
    if isinstance(layer, GridSlot):
        g = layer
        idx = _encoded(
            g,
            "index_map",
            g.index_encoding,
            g.index_map,
            lambda: cio.encode_index(g.index_map, g.index_encoding),
        )
        lut = _encoded(
            g,
            "lut",
            g.lut_encoding,
            g.lut,
            lambda: cio.encode_rgba(g.lut.reshape(1, -1, 4), g.lut_encoding),
        )
        base = f"slots/{g.id}"
        return {
            "shape": list(g.shape),
            "cells": g.cells,
            "index_map": {
                "member": _member(
                    members, assets, f"{base}/index.{_EXT[g.index_encoding]}", idx, g.id
                ),
                "encoding": g.index_encoding,
            },
            "value_dtype": g.value_dtype,
            "norm": {"kind": "linear", "vmin": g.vmin, "vmax": g.vmax},
            "colormap": {
                "member": _member(members, assets, f"{base}/lut.{_EXT[g.lut_encoding]}", lut, g.id),
                "encoding": g.lut_encoding,
                "n_colors": g.n_colors,
                "extend": g.extend,
            },
            "alpha": g.alpha,
        }
    if isinstance(layer, PolygonSlot):

        def bin_dict(b: mf.Bin, bounds: bool) -> dict[str, Any]:
            out: dict[str, Any] = {"lower": b.lower, "upper": b.upper} if bounds else {}
            out.update(color=list(b.color), order=b.order)
            return out

        return {
            "bins": [bin_dict(b, True) for b in layer.bins],
            "interval": layer.interval,
            "round_decimals": layer.round_decimals,
            "fallback": None if layer.fallback is None else bin_dict(layer.fallback, False),
            "fill_rule": layer.fill_rule,
            "supersample": layer.supersample,
        }
    if isinstance(layer, TextSlot):
        return {
            "value": layer.value,
            "font": layer.font,
            "size_px": layer.size_px,
            "color": list(layer.color),
            "x": layer.x,
            "y": layer.y,
            "ha": layer.ha,
            "va": layer.va,
            "layout": layer.layout,
            "snap": layer.snap,
            "offset": list(layer.offset),
            "rotation": layer.rotation,
        }
    return None


def _layer_from_record(
    rec: mf.Layer,
    raw: Mapping[str, Any],
    members: Mapping[str, bytes],
    used: set[str],
    man: mf.Manifest,
) -> Layer:
    p = rec.placement
    extra: dict[str, Any] = {
        k: val for k, val in raw.items() if k not in _LAYER_KEYS and k != rec.kind
    }
    section_raw = raw.get(rec.kind)
    if isinstance(section_raw, Mapping):
        unknown = {k: val for k, val in section_raw.items() if k not in _SECTION_KEYS[rec.kind]}
        if unknown:
            extra[rec.kind] = unknown
    common: dict[str, Any] = {
        "id": rec.id,
        "order": rec.order,
        "left": p.left,
        "top": p.top,
        "width": p.width,
        "height": p.height,
        "visible": rec.visible,
        "opacity": rec.opacity,
        "encoding": rec.encoding or DEFAULT_ENCODING,
        "extra": extra,
    }
    encoded: dict[str, tuple[str, bytes]] = {}
    if rec.member is not None and rec.encoding is not None:
        data = members[rec.member]
        common["pixels"] = cio.decode_rgba(data, rec.encoding, p.height, p.width, rec.member)
        encoded["pixels"] = (rec.encoding, data)
        used.add(rec.member)
    common["_encoded"] = encoded

    if isinstance(rec, mf.ColorbarLayer):
        return ColorbarLayer(**common, slot=rec.slot, redraw=_redraw(rec, members, used))
    if isinstance(rec, mf.GridSlot):
        idx_data = members[rec.index_member]
        lut_data = members[rec.lut_member]
        index_map = cio.decode_index(
            idx_data, rec.index_encoding, p.height, p.width, rec.index_member
        )
        lut = cio.decode_rgba(lut_data, rec.lut_encoding, 1, rec.n_colors + 3, rec.lut_member)
        used.update((rec.index_member, rec.lut_member))
        encoded["index_map"] = (rec.index_encoding, idx_data)
        encoded["lut"] = (rec.lut_encoding, lut_data)
        return GridSlot(
            **common,
            shape=rec.shape,
            index_map=index_map,
            lut=np.asarray(lut).reshape(-1, 4),
            vmin=rec.vmin,
            vmax=rec.vmax,
            alpha=rec.alpha,
            extend=rec.extend,
            value_dtype=rec.value_dtype,
            cells=rec.cells,
            index_encoding=rec.index_encoding,
            lut_encoding=rec.lut_encoding,
        )
    if isinstance(rec, mf.PolygonSlot):
        return PolygonSlot(
            **common,
            bins=rec.bins,
            fallback=rec.fallback,
            round_decimals=rec.round_decimals,
            supersample=rec.supersample,
            interval=rec.interval,
            fill_rule=rec.fill_rule,
        )
    if isinstance(rec, mf.TextSlot):
        return TextSlot(
            **common,
            value=rec.value,
            font=rec.font,
            size_px=rec.size_px,
            color=rec.color,
            x=rec.x,
            y=rec.y,
            ha=rec.ha,
            va=rec.va,
            layout=rec.layout,
            snap=rec.snap,
            offset=rec.offset,
            rotation=rec.rotation,
        )
    assert isinstance(rec, mf.RasterLayer)
    return RasterLayer(**common)


def _redraw(
    rec: mf.ColorbarLayer, members: Mapping[str, bytes], used: set[str]
) -> ColorbarRedraw | None:
    r = rec.redraw
    if r is None:
        return None
    h, w = rec.placement.height, rec.placement.width
    rows = cio.decode_index(members[r.rows_member], r.rows_encoding, h, w, r.rows_member)
    used.add(r.rows_member)
    parts: dict[str, NDArray[np.uint8]] = {}
    for name, member, enc in (
        ("under", r.under_member, r.under_encoding),
        ("over", r.over_member, r.over_encoding),
        ("label", r.label_member, r.label_encoding),
    ):
        if member is not None and enc is not None:
            parts[name] = cio.decode_rgba(members[member], enc, h, w, member)
            used.add(member)
    return ColorbarRedraw(
        orientation=r.orientation,
        box=r.box,
        rows=rows,
        n_colors=r.n_colors,
        under=parts.get("under"),
        over=parts.get("over"),
        label=parts.get("label"),
        label_edge=r.label_edge,
        locator=r.locator,
        nbins=r.nbins,
        steps=r.steps,
        values=r.values,
        side=r.side,
        direction=r.direction,
        tick_length=r.tick_length,
        tick_width=r.tick_width,
        tick_color=r.tick_color,
        font=r.font,
        size_px=r.size_px,
        label_color=r.label_color,
        pad=r.pad,
        minus=r.minus,
    )


def _redraw_section(
    layer_id: str, r: ColorbarRedraw, members: dict[str, bytes], assets: set[str]
) -> dict[str, Any]:
    base = f"colorbars/{layer_id}"
    rows = _member(
        members,
        assets,
        f"{base}/rows.i32.zz",
        _cached_encode(r.rows, "i32le+zlib", lambda: cio.encode_index(r.rows, "i32le+zlib")),
        layer_id,
    )
    parts: dict[str, Any] = {}
    for name in ("under", "over", "label"):
        px = getattr(r, name)
        if px is None:
            parts[name] = None
            continue
        data = _cached_encode(
            px, "rgba8+zlib", functools.partial(cio.encode_rgba, px, "rgba8+zlib")
        )
        member = _member(members, assets, f"{base}/{name}.rgba8.zz", data, layer_id)
        parts[name] = {"member": member, "encoding": "rgba8+zlib"}
    return {
        "orientation": r.orientation,
        "box": list(r.box),
        "rows": {"member": rows, "encoding": "i32le+zlib", "n_colors": r.n_colors},
        **parts,
        "label_edge": r.label_edge,
        "ticks": {
            "locator": r.locator,
            "nbins": r.nbins,
            "steps": list(r.steps),
            "values": list(r.values),
            "side": r.side,
            "direction": r.direction,
            "length": r.tick_length,
            "width": r.tick_width,
            "color": list(r.tick_color),
        },
        "labels": {
            "font": r.font,
            "size_px": r.size_px,
            "color": list(r.label_color),
            "pad": r.pad,
            "minus": r.minus,
        },
    }
