"""``manifest.json`` of a ``.cstack`` file: version rules, records, and validation.

Plain Python only (no NumPy); see ``docs/format.md`` for the specification and
``cartostack/schemas/manifest-1.schema.json`` for the structural JSON Schema.
:func:`parse_manifest` raises with every problem found; :func:`validate_manifest`
returns them instead.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from types import MappingProxyType
from typing import Any, Final

from . import _validate as v
from .errors import FormatError, GeometryError, UnsupportedVersionError
from .geometry import CanvasGeometry

FORMAT_NAME: Final = "cartostack"
FORMAT_VERSION: Final = (1, 0)
MANIFEST_NAME: Final = "manifest.json"
MIMETYPE_NAME: Final = "mimetype"
MIMETYPE: Final = "application/x-cartostack"

LAYER_KINDS: Final = ("raster", "colorbar", "grid", "polygon", "text")
RASTER_ENCODINGS: Final = ("rgba8+zlib", "rgba8", "png")
INDEX_ENCODINGS: Final = ("i32le+zlib", "i32le")
LUT_ENCODINGS: Final = ("rgba8+zlib", "rgba8")
TEXT_HA: Final = ("left", "center", "right")
TEXT_VA: Final = ("top", "center", "baseline", "bottom", "center_baseline")

_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_MEMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")


# --- Records -------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Placement:
    """Integer canvas offset and size of a layer's pixels (may extend past the canvas)."""

    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class Layer:
    """Fields common to every layer kind. ``index`` is the position in ``layers``."""

    id: str
    kind: str
    order: float
    index: int
    placement: Placement
    visible: bool
    opacity: float
    member: str | None  # current pixels; required for raster/colorbar
    encoding: str | None


@dataclass(frozen=True)
class RasterLayer(Layer):
    pass


@dataclass(frozen=True)
class ColorbarRedrawRecord:
    """``colorbar.redraw``: the parts needed to redraw a colorbar (``docs/format.md`` §7)."""

    orientation: str
    box: tuple[float, float, float, float]
    rows_member: str
    rows_encoding: str
    n_colors: int
    under_member: str | None
    under_encoding: str | None
    over_member: str | None
    over_encoding: str | None
    label_member: str | None
    label_encoding: str | None
    label_edge: float
    locator: str
    nbins: int
    steps: tuple[float, ...]
    values: tuple[float, ...]
    side: str
    direction: str
    tick_length: float
    tick_width: float
    tick_color: tuple[int, int, int, int]
    font: str
    size_px: float
    label_color: tuple[int, int, int, int]
    pad: float
    minus: str


@dataclass(frozen=True)
class ColorbarLayer(Layer):
    slot: str  # the grid slot whose normalisation and colormap it shows
    redraw: ColorbarRedrawRecord | None = None


@dataclass(frozen=True)
class GridSlot(Layer):
    shape: tuple[int, int]  # (ny, nx); cell index = j * nx + i
    cells: str  # "centers" | "edges" (how the index map was authored)
    index_member: str
    index_encoding: str
    value_dtype: str
    norm: str
    vmin: float
    vmax: float
    lut_member: str
    lut_encoding: str
    n_colors: int  # LUT rows: n_colors entries, then under, over, bad
    extend: str
    alpha: float | None


@dataclass(frozen=True)
class Bin:
    lower: float
    upper: float
    color: tuple[int, int, int, int]
    order: int


@dataclass(frozen=True)
class PolygonSlot(Layer):
    bins: tuple[Bin, ...]
    interval: str
    round_decimals: int | None
    fallback: Bin | None  # lower/upper unused
    fill_rule: str
    supersample: int


@dataclass(frozen=True)
class TextSlot(Layer):
    value: str
    font: str
    size_px: float
    color: tuple[int, int, int, int]
    x: float
    y: float
    ha: str
    va: str
    layout: str
    snap: str | None
    offset: tuple[float, float]
    rotation: float


@dataclass(frozen=True)
class Manifest:
    version: tuple[int, int]
    geometry: CanvasGeometry
    fingerprint: str
    members: Mapping[str, Member]
    layers: tuple[Layer, ...]
    provenance: Mapping[str, Any] | None

    def layer(self, layer_id: str) -> Layer:
        for layer in self.layers:
            if layer.id == layer_id:
                return layer
        raise KeyError(layer_id)

    def draw_order(self) -> tuple[Layer, ...]:
        """Bottom to top: ascending ``order``; ties keep their ``layers`` array order."""
        return tuple(sorted(self.layers, key=lambda layer: (layer.order, layer.index)))


# --- Entry points --------------------------------------------------------------------


def parse_version(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise FormatError(f"format_version: must be 'MAJOR.MINOR', got {value!r}")
    major, minor = value.split(".")
    return int(major), int(minor)


def parse_manifest(data: object) -> Manifest:
    """Validate a decoded ``manifest.json`` and return typed records.

    Raises :class:`UnsupportedVersionError` for another major version (checked
    first, before anything else is interpreted) and :class:`FormatError` listing
    every other problem.
    """
    p = v.Problems()
    m = v.obj(p, "", data)
    if m is None:
        raise FormatError(p.items)
    if m.get("format") != FORMAT_NAME:
        raise FormatError(f"format: must be {FORMAT_NAME!r}, got {m.get('format')!r}")
    version = parse_version(m.get("format_version"))
    if version[0] != FORMAT_VERSION[0]:
        raise UnsupportedVersionError(
            f"format_version {version[0]}.{version[1]} is not supported; this reader "
            f"implements {FORMAT_VERSION[0]}.x (up to {FORMAT_VERSION[0]}.{FORMAT_VERSION[1]})"
        )

    geometry = None
    geo_raw = v.get(p, m, "geometry", "")
    try:
        geometry = CanvasGeometry.from_dict(geo_raw)
    except GeometryError as exc:
        p.items.extend(exc.problems)
    fingerprint = v.string(
        p, "geometry_fingerprint", v.get(p, m, "geometry_fingerprint", ""), pattern=_FINGERPRINT
    )
    if geometry is not None and fingerprint is not None and fingerprint != geometry.fingerprint():
        p.add(
            "geometry_fingerprint",
            f"does not match the geometry ({geometry.fingerprint()}); the geometry was edited "
            "or the fingerprint is stale",
        )
    members = _members(p, v.get(p, m, "members", ""))
    layers = _layers(p, v.get(p, m, "layers", ""), geometry, members)
    prov = v.get(p, m, "provenance", "", None)
    provenance = None if prov is None else v.obj(p, "provenance", prov)

    if p or geometry is None or fingerprint is None or members is None or layers is None:
        raise FormatError(p.items)
    return Manifest(
        version=version,
        geometry=geometry,
        fingerprint=fingerprint,
        members=MappingProxyType(members),
        layers=layers,
        provenance=None if provenance is None else MappingProxyType(dict(provenance)),
    )


def validate_manifest(data: object) -> list[str]:
    """Every problem in ``data`` as a list of strings (empty when valid)."""
    try:
        parse_manifest(data)
    except FormatError as exc:
        return list(exc.problems)
    return []


def load_schema() -> dict[str, Any]:
    """The structural JSON Schema (draft 2020-12) for format version 1."""
    text = resources.files("cartostack.schemas").joinpath("manifest-1.schema.json").read_text()
    schema: dict[str, Any] = json.loads(text)
    return schema


# --- Members -------------------------------------------------------------------------


def _members(p: v.Problems, data: object) -> dict[str, Member] | None:
    table = v.obj(p, "members", data)
    if table is None:
        return None
    out: dict[str, Member] = {}
    for name, info in table.items():
        path = f"members[{name!r}]"
        if name in (MANIFEST_NAME, MIMETYPE_NAME):
            p.add(path, "is reserved and must not be listed")
            continue
        if len(name) > 255 or not _MEMBER.fullmatch(name) or ".." in name.split("/"):
            p.add(path, "is not a valid member name (relative, '/'-separated, [A-Za-z0-9._-])")
            continue
        rec = v.obj(p, path, info)
        if rec is None:
            continue
        size = v.integer(p, v.join(path, "bytes"), v.get(p, rec, "bytes", path), lo=0)
        digest = v.string(p, v.join(path, "sha256"), v.get(p, rec, "sha256", path), pattern=_SHA256)
        if size is not None and digest is not None:
            out[name] = Member(name, size, digest)
    return out


# --- Layers --------------------------------------------------------------------------


def _layers(
    p: v.Problems,
    data: object,
    geometry: CanvasGeometry | None,
    members: Mapping[str, Member] | None,
) -> tuple[Layer, ...] | None:
    items = v.array(p, "layers", data)
    if items is None:
        return None
    if not items:
        p.add("layers", "must contain at least one layer")
    out: list[Layer] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(items):
        # Duplicates are found from the raw ids, so an otherwise invalid layer still counts.
        rid = raw.get("id") if isinstance(raw, Mapping) else None
        if isinstance(rid, str):
            if rid in seen:
                p.add(f"layers[{i}].id", f"duplicates layers[{seen[rid]}].id {rid!r}")
            else:
                seen[rid] = i
        layer = _layer(p, f"layers[{i}]", i, raw, geometry, members)
        if layer is not None:
            out.append(layer)
    grids = {layer.id for layer in out if isinstance(layer, GridSlot)}
    for layer in out:
        if isinstance(layer, ColorbarLayer) and layer.slot not in grids:
            p.add(f"layers[{layer.index}].colorbar.slot", f"{layer.slot!r} is not a grid layer")
    return tuple(out)


def _ref(
    p: v.Problems, path: str, name: object, members: Mapping[str, Member] | None
) -> str | None:
    member = v.string(p, path, name)
    if member is not None and members is not None and member not in members:
        p.add(path, f"member {member!r} is not listed in members")
        return None
    return member


def _layer(
    p: v.Problems,
    path: str,
    index: int,
    data: object,
    geometry: CanvasGeometry | None,
    members: Mapping[str, Member] | None,
) -> Layer | None:
    r = v.obj(p, path, data)
    if r is None:
        return None
    n = len(p.items)
    lid = v.string(p, v.join(path, "id"), v.get(p, r, "id", path), pattern=_ID)
    kind = v.string(p, v.join(path, "kind"), v.get(p, r, "kind", path), choices=LAYER_KINDS)
    order = v.number(p, v.join(path, "order"), v.get(p, r, "order", path))
    left = v.integer(p, v.join(path, "left"), v.get(p, r, "left", path, 0))
    top = v.integer(p, v.join(path, "top"), v.get(p, r, "top", path, 0))
    # Size defaults to the canvas; with an invalid geometry there is nothing to check it against.
    size: dict[str, int | None] = {}
    for key, canvas_size in (
        ("width", geometry.width if geometry else None),
        ("height", geometry.height if geometry else None),
    ):
        raw = v.get(p, r, key, path, canvas_size)
        size[key] = None if raw is None else v.integer(p, v.join(path, key), raw, lo=1)
    width, height = size["width"], size["height"]
    visible = v.boolean(p, v.join(path, "visible"), v.get(p, r, "visible", path, True))
    opacity = v.number(p, v.join(path, "opacity"), v.get(p, r, "opacity", path, 1.0), lo=0, hi=1)

    member_raw = v.get(p, r, "member", path, None)
    needs_pixels = kind in ("raster", "colorbar")
    if member_raw is None and needs_pixels:
        p.add(v.join(path, "member"), f"is required for kind {kind!r}")
    member = None if member_raw is None else _ref(p, v.join(path, "member"), member_raw, members)
    enc_raw = v.get(p, r, "encoding", path, None)
    encoding = None
    if member_raw is not None:
        encoding = v.string(
            p, v.join(path, "encoding"), v.get(p, r, "encoding", path), choices=RASTER_ENCODINGS
        )
    elif enc_raw is not None:
        p.add(v.join(path, "encoding"), "is only allowed together with member")
    placed = None not in (left, top, width, height) and geometry is not None
    if placed and geometry is not None and left is not None and top is not None:
        assert width is not None
        assert height is not None
        outside = (
            left >= geometry.width
            or top >= geometry.height
            or left + width <= 0
            or top + height <= 0
        )
        if outside:
            p.add(path, "placement does not intersect the canvas")

    if len(p.items) > n or None in (lid, kind, order, left, top, width, height, visible, opacity):
        return None
    assert lid is not None
    assert kind is not None
    assert order is not None
    assert left is not None
    assert top is not None
    assert width is not None
    assert height is not None
    assert visible is not None
    assert opacity is not None
    common: dict[str, Any] = {
        "id": lid,
        "kind": kind,
        "order": order,
        "index": index,
        "placement": Placement(left, top, width, height),
        "visible": visible,
        "opacity": opacity,
        "member": member,
        "encoding": encoding,
    }
    if kind == "raster":
        return RasterLayer(**common)
    if kind == "colorbar":
        return _colorbar(p, path, r, common, members)
    if kind == "grid":
        return _grid(p, path, r, common, members)
    if kind == "polygon":
        return _polygon(p, path, r, common, geometry)
    return _text(p, path, r, common, members)


def _section(p: v.Problems, path: str, r: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    return v.obj(p, v.join(path, key), v.get(p, r, key, path))


def _colorbar(
    p: v.Problems,
    path: str,
    r: Mapping[str, Any],
    common: Mapping[str, Any],
    members: Mapping[str, Member] | None,
) -> ColorbarLayer | None:
    s = _section(p, path, r, "colorbar")
    if s is None:
        return None
    sp = v.join(path, "colorbar")
    slot = v.string(p, v.join(sp, "slot"), v.get(p, s, "slot", sp), pattern=_ID)
    raw = v.get(p, s, "redraw", sp, None)
    n = len(p.items)
    redraw = None if raw is None else _redraw(p, v.join(sp, "redraw"), raw, members)
    if slot is None or len(p.items) > n:
        return None
    return ColorbarLayer(**common, slot=slot, redraw=redraw)


def _member_ref(
    p: v.Problems, path: str, data: object, members: Mapping[str, Member] | None,
    encodings: tuple[str, ...],
) -> tuple[str | None, str | None]:  # fmt: skip
    ref = v.obj(p, path, data)
    if ref is None:
        return None, None
    member = _ref(p, v.join(path, "member"), v.get(p, ref, "member", path), members)
    encoding = v.string(
        p, v.join(path, "encoding"), v.get(p, ref, "encoding", path), choices=encodings
    )
    return member, encoding


def _redraw(
    p: v.Problems, path: str, data: object, members: Mapping[str, Member] | None
) -> ColorbarRedrawRecord | None:
    d = v.obj(p, path, data)
    if d is None:
        return None
    n = len(p.items)
    orientation = v.string(p, v.join(path, "orientation"), v.get(p, d, "orientation", path),
                           choices=("horizontal", "vertical"))  # fmt: skip
    box = v.numbers(p, v.join(path, "box"), v.get(p, d, "box", path), 4)
    if box is not None and not (box[2] > 0 and box[3] > 0):
        p.add(v.join(path, "box"), "needs a positive width and height")
    rp = v.join(path, "rows")
    rows = v.obj(p, rp, v.get(p, d, "rows", path))
    rows_member = rows_encoding = n_colors = None
    if rows is not None:
        rows_member, rows_encoding = _member_ref(p, rp, rows, members, INDEX_ENCODINGS)
        n_colors = v.integer(
            p, v.join(rp, "n_colors"), v.get(p, rows, "n_colors", rp), lo=1, hi=65536
        )
    under: tuple[str | None, str | None] = (None, None)
    over: tuple[str | None, str | None] = (None, None)
    label: tuple[str | None, str | None] = (None, None)
    if v.get(p, d, "under", path, None) is not None:
        under = _member_ref(p, v.join(path, "under"), d["under"], members, RASTER_ENCODINGS)
    if v.get(p, d, "over", path, None) is not None:
        over = _member_ref(p, v.join(path, "over"), d["over"], members, RASTER_ENCODINGS)
    if v.get(p, d, "label", path, None) is not None:
        label = _member_ref(p, v.join(path, "label"), d["label"], members, RASTER_ENCODINGS)
    label_edge = v.number(p, v.join(path, "label_edge"), v.get(p, d, "label_edge", path, 0.0))
    tp = v.join(path, "ticks")
    t = v.obj(p, tp, v.get(p, d, "ticks", path)) or {}
    locator = v.string(
        p, v.join(tp, "locator"), v.get(p, t, "locator", tp, "auto"), choices=("auto", "fixed")
    )
    nbins = v.integer(p, v.join(tp, "nbins"), v.get(p, t, "nbins", tp, 9), lo=1)
    steps_raw = v.array(p, v.join(tp, "steps"), v.get(p, t, "steps", tp, [1, 2, 2.5, 5, 10]))
    steps = (
        None if steps_raw is None else v.numbers(p, v.join(tp, "steps"), steps_raw, len(steps_raw))
    )
    if steps is not None and (len(steps) < 2 or steps[0] != 1 or steps[-1] != 10):
        p.add(v.join(tp, "steps"), "must start at 1 and end at 10")
    values_raw = v.array(p, v.join(tp, "values"), v.get(p, t, "values", tp, []))
    values = (
        None
        if values_raw is None
        else v.numbers(p, v.join(tp, "values"), values_raw, len(values_raw))
    )
    side = v.string(p, v.join(tp, "side"), v.get(p, t, "side", tp, "bottom"),
                    choices=("bottom", "top", "left", "right"))  # fmt: skip
    direction = v.string(p, v.join(tp, "direction"), v.get(p, t, "direction", tp, "out"),
                         choices=("out", "in", "inout"))  # fmt: skip
    length = v.number(p, v.join(tp, "length"), v.get(p, t, "length", tp), lo=0)
    width = v.number(p, v.join(tp, "width"), v.get(p, t, "width", tp), lo=0)
    tick_color = v.color(p, v.join(tp, "color"), v.get(p, t, "color", tp, [0, 0, 0, 255]))
    lp = v.join(path, "labels")
    lab = v.obj(p, lp, v.get(p, d, "labels", path)) or {}
    font = _ref(p, v.join(lp, "font"), v.get(p, lab, "font", lp), members)
    size_px = v.number(p, v.join(lp, "size_px"), v.get(p, lab, "size_px", lp), lo=0, lo_open=True)
    label_color = v.color(p, v.join(lp, "color"), v.get(p, lab, "color", lp, [0, 0, 0, 255]))
    pad = v.number(p, v.join(lp, "pad"), v.get(p, lab, "pad", lp, 0.0), lo=0)
    minus = v.string(p, v.join(lp, "minus"), v.get(p, lab, "minus", lp, "\u2212"))
    if len(p.items) > n:
        return None
    required = (orientation, box, rows_member, rows_encoding, n_colors, locator, nbins, steps,
                values, side, direction, length, width, tick_color, font, size_px, label_color,
                pad, minus)  # fmt: skip
    if any(x is None for x in required):
        return None
    assert orientation is not None
    assert box is not None
    assert rows_member is not None
    assert rows_encoding is not None
    assert n_colors is not None
    assert locator is not None
    assert nbins is not None
    assert steps is not None
    assert values is not None
    assert side is not None
    assert direction is not None
    assert length is not None
    assert width is not None
    assert tick_color is not None
    assert font is not None
    assert size_px is not None
    assert label_color is not None
    assert pad is not None
    assert minus is not None
    assert label_edge is not None
    return ColorbarRedrawRecord(
        orientation, (box[0], box[1], box[2], box[3]), rows_member, rows_encoding, n_colors,
        under[0], under[1], over[0], over[1], label[0], label[1], label_edge, locator, nbins,
        steps, values, side, direction, length, width, tick_color, font, size_px, label_color,
        pad, minus,
    )  # fmt: skip


def _grid(
    p: v.Problems,
    path: str,
    r: Mapping[str, Any],
    common: Mapping[str, Any],
    members: Mapping[str, Member] | None,
) -> GridSlot | None:
    s = _section(p, path, r, "grid")
    if s is None:
        return None
    n = len(p.items)
    gp = v.join(path, "grid")
    shape_raw = v.array(p, v.join(gp, "shape"), v.get(p, s, "shape", gp), length=2)
    shape = None
    if shape_raw is not None:
        dims = [v.integer(p, v.join(gp, f"shape[{i}]"), d, lo=1) for i, d in enumerate(shape_raw)]
        if dims[0] is not None and dims[1] is not None:
            shape = (dims[0], dims[1])
            if dims[0] * dims[1] > 2**31 - 1:
                p.add(v.join(gp, "shape"), "has more cells than an int32 index can address")
    cells = v.string(
        p, v.join(gp, "cells"), v.get(p, s, "cells", gp, "centers"), choices=("centers", "edges")
    )
    im = v.obj(p, v.join(gp, "index_map"), v.get(p, s, "index_map", gp))
    index_member = index_encoding = None
    if im is not None:
        ip = v.join(gp, "index_map")
        index_member = _ref(p, v.join(ip, "member"), v.get(p, im, "member", ip), members)
        index_encoding = v.string(
            p, v.join(ip, "encoding"), v.get(p, im, "encoding", ip), choices=INDEX_ENCODINGS
        )
    dtype = v.string(
        p,
        v.join(gp, "value_dtype"),
        v.get(p, s, "value_dtype", gp, "float32"),
        choices=("float32", "float64"),
    )
    norm = v.obj(p, v.join(gp, "norm"), v.get(p, s, "norm", gp))
    kind = vmin = vmax = None
    if norm is not None:
        np_ = v.join(gp, "norm")
        kind = v.string(
            p, v.join(np_, "kind"), v.get(p, norm, "kind", np_, "linear"), choices=("linear",)
        )
        vmin = v.number(p, v.join(np_, "vmin"), v.get(p, norm, "vmin", np_))
        vmax = v.number(p, v.join(np_, "vmax"), v.get(p, norm, "vmax", np_))
        if vmin is not None and vmax is not None and not vmin < vmax:
            p.add(np_, "requires vmin < vmax")
    cm = v.obj(p, v.join(gp, "colormap"), v.get(p, s, "colormap", gp))
    lut_member = lut_encoding = n_colors = extend = None
    if cm is not None:
        cp = v.join(gp, "colormap")
        lut_member = _ref(p, v.join(cp, "member"), v.get(p, cm, "member", cp), members)
        lut_encoding = v.string(
            p, v.join(cp, "encoding"), v.get(p, cm, "encoding", cp), choices=LUT_ENCODINGS
        )
        n_colors = v.integer(
            p, v.join(cp, "n_colors"), v.get(p, cm, "n_colors", cp), lo=1, hi=65536
        )
        extend = v.string(
            p,
            v.join(cp, "extend"),
            v.get(p, cm, "extend", cp, "neither"),
            choices=("neither", "min", "max", "both"),
        )
    alpha_raw = v.get(p, s, "alpha", gp, None)
    alpha = None if alpha_raw is None else v.number(p, v.join(gp, "alpha"), alpha_raw, lo=0, hi=1)
    if len(p.items) > n:
        return None
    assert shape is not None
    assert cells is not None
    assert index_member is not None
    assert index_encoding is not None
    assert dtype is not None
    assert kind is not None
    assert vmin is not None
    assert vmax is not None
    assert lut_member is not None
    assert lut_encoding is not None
    assert n_colors is not None
    assert extend is not None
    return GridSlot(
        **common,
        shape=shape,
        cells=cells,
        index_member=index_member,
        index_encoding=index_encoding,
        value_dtype=dtype,
        norm=kind,
        vmin=vmin,
        vmax=vmax,
        lut_member=lut_member,
        lut_encoding=lut_encoding,
        n_colors=n_colors,
        extend=extend,
        alpha=alpha,
    )


def _bin(p: v.Problems, path: str, data: object, *, bounds: bool) -> Bin | None:
    b = v.obj(p, path, data)
    if b is None:
        return None
    lower = upper = 0.0
    if bounds:
        lo = v.number(p, v.join(path, "lower"), v.get(p, b, "lower", path))
        hi = v.number(p, v.join(path, "upper"), v.get(p, b, "upper", path))
        if lo is None or hi is None:
            return None
        if not lo < hi:
            p.add(path, "requires lower < upper")
            return None
        lower, upper = lo, hi
    col = v.color(p, v.join(path, "color"), v.get(p, b, "color", path))
    order = v.integer(p, v.join(path, "order"), v.get(p, b, "order", path))
    if col is None or order is None:
        return None
    return Bin(lower, upper, col, order)


def _polygon(
    p: v.Problems,
    path: str,
    r: Mapping[str, Any],
    common: Mapping[str, Any],
    geometry: CanvasGeometry | None,
) -> PolygonSlot | None:
    s = _section(p, path, r, "polygon")
    if s is None:
        return None
    n = len(p.items)
    pp = v.join(path, "polygon")
    bins_raw = v.array(p, v.join(pp, "bins"), v.get(p, s, "bins", pp))
    bins: list[Bin] = []
    if bins_raw is not None:
        if not bins_raw:
            p.add(v.join(pp, "bins"), "must contain at least one bin")
        for i, raw in enumerate(bins_raw):
            b = _bin(p, f"{pp}.bins[{i}]", raw, bounds=True)
            if b is not None:
                bins.append(b)
    interval = v.string(
        p,
        v.join(pp, "interval"),
        v.get(p, s, "interval", pp, "closed-open"),
        choices=("closed-open",),
    )
    rd_raw = v.get(p, s, "round_decimals", pp, None)
    round_decimals = (
        None if rd_raw is None else v.integer(p, v.join(pp, "round_decimals"), rd_raw, lo=0, hi=15)
    )
    fb_raw = v.get(p, s, "fallback", pp, None)
    fallback = None if fb_raw is None else _bin(p, v.join(pp, "fallback"), fb_raw, bounds=False)
    fill_rule = v.string(
        p, v.join(pp, "fill_rule"), v.get(p, s, "fill_rule", pp, "evenodd"), choices=("evenodd",)
    )
    supersample = v.integer(
        p, v.join(pp, "supersample"), v.get(p, s, "supersample", pp, 1), lo=1, hi=8
    )
    if geometry is not None:
        geo = geometry.georeference
        if geo is None:
            p.add(pp, "polygon slots need geometry.georeference")
        elif not geo.projection.supported:
            p.add(
                pp,
                f"projection {geo.projection.name!r} is not supported by the runtime; "
                "polygon slots are unusable in this file",
            )
    if len(p.items) > n:
        return None
    assert interval is not None
    assert fill_rule is not None
    assert supersample is not None
    return PolygonSlot(
        **common,
        bins=tuple(bins),
        interval=interval,
        round_decimals=round_decimals,
        fallback=fallback,
        fill_rule=fill_rule,
        supersample=supersample,
    )


def _text(
    p: v.Problems,
    path: str,
    r: Mapping[str, Any],
    common: Mapping[str, Any],
    members: Mapping[str, Member] | None,
) -> TextSlot | None:
    s = _section(p, path, r, "text")
    if s is None:
        return None
    n = len(p.items)
    tp = v.join(path, "text")
    value = v.string(p, v.join(tp, "value"), v.get(p, s, "value", tp))
    font = _ref(p, v.join(tp, "font"), v.get(p, s, "font", tp), members)
    size = v.number(p, v.join(tp, "size_px"), v.get(p, s, "size_px", tp), lo=0, lo_open=True)
    col = v.color(p, v.join(tp, "color"), v.get(p, s, "color", tp))
    x = v.number(p, v.join(tp, "x"), v.get(p, s, "x", tp))
    y = v.number(p, v.join(tp, "y"), v.get(p, s, "y", tp))
    ha = v.string(p, v.join(tp, "ha"), v.get(p, s, "ha", tp, "left"), choices=TEXT_HA)
    va = v.string(p, v.join(tp, "va"), v.get(p, s, "va", tp, "baseline"), choices=TEXT_VA)
    layout = v.string(
        p, v.join(tp, "layout"), v.get(p, s, "layout", tp, "matplotlib"), choices=("matplotlib",)
    )
    snap_raw = v.get(p, s, "snap", tp, None)
    snap = (
        None if snap_raw is None else v.string(p, v.join(tp, "snap"), snap_raw, choices=("round",))
    )
    offset = v.numbers(p, v.join(tp, "offset"), v.get(p, s, "offset", tp, [0.0, 0.0]), 2)
    rotation = v.number(p, v.join(tp, "rotation"), v.get(p, s, "rotation", tp, 0.0))
    if rotation is not None and rotation != 0:
        p.add(v.join(tp, "rotation"), "must be 0 in format version 1.0")
    if len(p.items) > n:
        return None
    assert value is not None
    assert font is not None
    assert size is not None
    assert col is not None
    assert x is not None
    assert y is not None
    assert ha is not None
    assert va is not None
    assert layout is not None
    assert offset is not None
    assert rotation is not None
    return TextSlot(
        **common,
        value=value,
        font=font,
        size_px=size,
        color=col,
        x=x,
        y=y,
        ha=ha,
        va=va,
        layout=layout,
        snap=snap,
        offset=(offset[0], offset[1]),
        rotation=rotation,
    )
