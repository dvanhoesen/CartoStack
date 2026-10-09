"""Polygon slots: classified lon/lat polygons → pixels with NumPy (``docs/format.md`` §9).

Input is one *record* per polygon: a value and any number of rings of
``(lon, lat)`` degrees. Each record's value picks a bin (colour and draw order);
records are drawn in bin order, each filled with the even-odd rule over all of
its rings, so holes need no nesting information.

Filling is exact for the format's coverage rule rather than an approximation:
a pixel holds ``k * k`` sub-pixel samples at the centres of a ``k``-times finer
grid, and its coverage is the number of samples inside the record. A sample is
inside when an odd number of ring edges cross its horizontal line at or to its
left, counting an edge when the sample lies in ``[min(y0, y1), max(y0, y1))``.
The implementation never builds the fine grid: it computes every scanline's
crossings, sorts and pairs them into runs, scatters the runs' ends into a
pixel-resolution difference array, and takes one prefix sum per record. Cost
grows with the number of crossings (proportional to ``k``) plus the record's
bounding box at pixel resolution, not with ``k²``.

Coverage becomes alpha ``floor(a · count / k² + 0.5)`` for a bin colour with
alpha ``a``, composited source-over onto the earlier records with the same
integer operator as the compositor (Pillow's ``alpha_composite``). Samples
outside the clip region (the axes intersected with the canvas and the layer's
placed box) are never inside.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .errors import CartoStackError
from .geometry import CanvasGeometry
from .layers import RGBA, PolygonSlot
from .projection import ProjectionError, to_pixels

Ring = NDArray[np.float64]
Record = list[Ring]


class PolygonDataError(CartoStackError, ValueError):
    """Polygon input does not fit the slot (shape, values, or a value with no bin)."""


# --- Input -----------------------------------------------------------------------------


def _ring(data: object, where: str) -> Ring:
    try:
        arr = np.asarray(data, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise PolygonDataError(f"{where}: a ring must be a sequence of (lon, lat) pairs") from exc
    if arr.size == 0:
        return np.empty((0, 2))
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise PolygonDataError(f"{where}: a ring must have shape (n, 2), got {arr.shape}")
    if not np.isfinite(arr).all():
        raise PolygonDataError(f"{where}: ring coordinates must be finite")
    return arr


def _record(data: object, where: str) -> Record:
    """One record's rings. A single ``(n, 2)`` array-like is accepted as one ring."""
    if data is None:
        return []
    try:
        arr = np.asarray(data, dtype=np.float64)
    except (TypeError, ValueError):
        arr = None  # ragged: a sequence of rings with different lengths
    if arr is not None:
        if arr.size == 0:
            return []
        if arr.ndim == 2:
            return [_ring(arr, where)]
        if arr.ndim == 3:
            return [_ring(r, f"{where} ring {j}") for j, r in enumerate(arr)]
        raise PolygonDataError(
            f"{where}: expected a ring of shape (n, 2) or a sequence of rings, "
            f"got shape {arr.shape}"
        )
    if not isinstance(data, Iterable):
        raise PolygonDataError(f"{where}: expected a sequence of rings")
    return [_ring(r, f"{where} ring {j}") for j, r in enumerate(data)]


def normalize(records: Iterable[object], values: ArrayLike) -> tuple[list[Record], list[float]]:
    """Validate polygon input: one record (rings) per value. Returns float64 rings and floats."""
    if isinstance(records, np.ndarray) and records.ndim == 2:
        raise PolygonDataError(
            "records must be a sequence with one entry (a ring or a list of rings) per value; "
            "wrap a single ring as [ring]"
        )
    recs = [_record(r, f"record {i}") for i, r in enumerate(records)]
    vals = np.asarray(values)
    if vals.ndim != 1:
        raise PolygonDataError(f"values must be one-dimensional, got shape {vals.shape}")
    if vals.dtype == bool or not (
        np.issubdtype(vals.dtype, np.integer) or np.issubdtype(vals.dtype, np.floating)
    ):
        raise PolygonDataError(f"values must be real numbers, got dtype {vals.dtype}")
    if len(vals) != len(recs):
        raise PolygonDataError(
            f"got {len(recs)} record(s) but {len(vals)} value(s); supply one value per record"
        )
    return recs, [float(v) for v in vals]


# --- Classification --------------------------------------------------------------------


def classify(
    slot: PolygonSlot, values: Sequence[float]
) -> tuple[NDArray[np.uint8], NDArray[np.int64]]:
    """Each value's bin colour and draw order (``docs/format.md`` §9, "Classification").

    Values are rounded with Python's ``round(value, round_decimals)``; the first bin in
    list order with ``lower <= value < upper`` wins; otherwise the fallback. NaN matches
    no bin. Raises ``PolygonDataError`` when a value has no bin and there is no fallback.
    """
    nd = slot.round_decimals
    vals = np.array([round(v, nd) if nd is not None else v for v in values], np.float64)
    idx = np.full(len(vals), -1, np.int64)
    for i, b in enumerate(slot.bins):
        idx[(idx == -1) & (vals >= b.lower) & (vals < b.upper)] = i
    missing = np.flatnonzero(idx == -1)
    if missing.size and slot.fallback is None:
        first = int(missing[0])
        raise PolygonDataError(
            f"{slot.id}: {missing.size} value(s) fall in no bin and the slot has no fallback "
            f"(first: record {first}, value {values[first]!r})"
        )
    table = list(slot.bins) + ([slot.fallback] if slot.fallback is not None else [])
    colors = np.array([b.color for b in table], np.uint8).reshape(-1, 4)
    orders = np.array([b.order for b in table], np.int64)
    return colors[idx], orders[idx]


# --- Rasterisation ---------------------------------------------------------------------


def clip_bounds(slot: PolygonSlot, geometry: CanvasGeometry) -> tuple[float, float, float, float]:
    """The clip region in the slot's own pixel coordinates: ``(left, top, right, bottom)``.

    The axes intersected with the canvas (``docs/format.md`` §5.3) and the layer's box.
    """
    height, width = slot.size((geometry.height, geometry.width))
    ax = geometry.axes
    left = max(ax.left, 0.0, slot.left) - slot.left
    top = max(ax.top, 0.0, slot.top) - slot.top
    right = min(ax.right, float(geometry.width), slot.left + width) - slot.left
    bottom = min(ax.bottom, float(geometry.height), slot.top + height) - slot.top
    return left, top, right, bottom


def sample_bounds(clip: tuple[float, float, float, float], k: int) -> tuple[int, int, int, int]:
    """Sub-pixel sample indices inside ``clip``: ``(c0, r0, c1, r1)``, end-exclusive.

    Sample ``i`` has its centre at ``(i + 0.5) / k`` pixels; it is inside when
    ``left <= centre < right``.
    """
    left, top, right, bottom = clip
    return (
        math.ceil(left * k - 0.5),
        math.ceil(top * k - 0.5),
        math.ceil(right * k - 0.5),
        math.ceil(bottom * k - 0.5),
    )


def coverage(
    cols: NDArray[np.float64],
    rows: NDArray[np.float64],
    ring_ends: NDArray[np.int64],
    k: int,
    bounds: tuple[int, int, int, int],
) -> tuple[tuple[int, int], NDArray[np.uint8]] | None:
    """Even-odd sample counts (0..k²) for one record, or ``None`` if nothing is inside.

    ``cols``/``rows`` are the record's vertices in continuous pixels (edges at
    integers), rings concatenated; ``ring_ends`` are the cumulative ring lengths.
    ``bounds`` come from ``sample_bounds``. Returns ``((top, left), counts)`` with
    counts covering the pixel box rows ``[top, top + h)`` and columns ``[left, left + w)``.
    """
    c0, r0, c1, r1 = bounds
    if c1 <= c0 or r1 <= r0 or cols.size == 0:
        return None
    x = cols * k
    y = rows * k
    nxt = np.arange(1, x.size + 1)
    nxt[ring_ends - 1] = np.concatenate(([0], ring_ends[:-1]))  # close every ring
    x1, y1 = x[nxt], y[nxt]
    lo, hi = np.minimum(y, y1), np.maximum(y, y1)
    # Sample rows j (centre j + 0.5) each edge crosses: lo <= j + 0.5 < hi, within the clip.
    j0 = np.maximum(np.ceil(lo - 0.5), r0)
    j1 = np.minimum(np.ceil(hi - 0.5), r1)
    n = (j1 - j0).astype(np.int64)
    edges = np.flatnonzero(n > 0)
    if edges.size == 0:
        return None
    n = n[edges]
    j0i = j0[edges].astype(np.int64)
    ex, ey = x[edges], y[edges]
    dx, dy = x1[edges] - ex, y1[edges] - ey
    total = int(n.sum())
    edge = np.repeat(np.arange(edges.size), n)
    j = j0i[edge] + (np.arange(total) - np.repeat(np.cumsum(n) - n, n))
    # Multiply before dividing: exact for crossings that are exactly representable (ties).
    xc = ex[edge] + (j + 0.5 - ey[edge]) * dx[edge] / dy[edge]
    # First sample index at or right of each crossing, clamped to the clip. Clamping keeps
    # the order within a row (ties are interchangeable), so pairing stays correct.
    s = np.clip(np.ceil(xc - 0.5), c0, c1).astype(np.int64)
    span = c1 - c0 + 1
    key = (j - r0) * span + (s - c0)
    if (r1 - r0) * span < 2**31:
        key = key.astype(np.int32)  # sorts about twice as fast as int64
    key.sort()
    key = key.astype(np.int64, copy=False)
    jj = key // span
    ss = key - jj * span + c0
    # Every row has an even number of crossings (closed rings, half-open rows): pair them.
    start, end, row = ss[0::2], ss[1::2], jj[0::2] + r0
    keep = end > start
    if not keep.any():
        return None
    start, end, row = start[keep], end[keep], row[keep] // k
    # Run [start, end) in samples → per-pixel sample counts via a difference array.
    ps, pe = start // k, end // k
    v1, v3 = k - (start - ps * k), end - pe * k
    top, left = int(row.min()), int(ps.min())
    height = int(row.max()) - top + 1
    width = int(((end - 1) // k).max()) - left + 1  # last pixel holding a sample
    stride = width + 2  # room for the pe and pe + 1 entries past the last pixel
    base = (row - top) * stride - left
    diff = np.zeros(height * stride + 1, np.int16)
    np.add.at(diff, np.concatenate((base + ps, base + ps + 1, base + pe, base + pe + 1)),
              np.concatenate((v1, k - v1, v3 - k, -v3)).astype(np.int16))  # fmt: skip
    counts = np.cumsum(diff[:-1].reshape(height, stride), axis=1, dtype=np.int16)[:, :width]
    return (top, left), counts.astype(np.uint8)


def over(dst: NDArray[np.uint8], src: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Straight-alpha source-over with Pillow's integer arithmetic (``alpha_composite``)."""
    d, s = dst.astype(np.int64), src.astype(np.int64)
    sa, da = s[:, 3:], d[:, 3:]
    outa255 = sa * 255 + da * (255 - sa)
    coef1 = sa * 255 * 255 * 128 // np.where(outa255 == 0, 1, outa255)
    coef2 = 255 * 128 - coef1
    rgb = s[:, :3] * coef1 + d[:, :3] * coef2 + (0x80 << 7)
    rgb = (((rgb >> 8) + rgb) >> 8) >> 7
    a = outa255 + 0x80
    a = ((a >> 8) + a) >> 8
    out = np.concatenate([rgb, a], axis=1).astype(np.uint8)
    return np.where(sa == 0, dst, out)


def _blend(
    out: RGBA, origin: tuple[int, int], counts: NDArray[np.uint8], color: NDArray[np.uint8], k: int
) -> None:
    """Composite ``color`` with per-pixel coverage ``counts`` (0..k²) into ``out`` in place."""
    top, left = origin
    h, w = counts.shape
    full_count = k * k
    alpha = int(color[3])
    # One uint32 per pixel: 4x fewer elements to compare and assign than per channel.
    pixels = out.view(np.uint32).reshape(out.shape[:2])
    region = pixels[top : top + h, left : left + w]
    if alpha == 255:
        np.copyto(region, color.view(np.uint32)[0], where=counts == full_count)
        # 0 < count < k² in one pass: count - 1 wraps 0 to 255.
        part = (counts - np.uint8(1)) < np.uint8(full_count - 1)
    else:
        part = counts != 0
    rows, cols = np.nonzero(part)
    if rows.size == 0:
        return
    flat = (rows + top) * out.shape[1] + (cols + left)
    c = counts[rows, cols].astype(np.int64)
    src = np.empty((c.size, 4), np.uint8)
    src[:, :3] = color[:3]
    src[:, 3] = (c * alpha * 2 + full_count) // (2 * full_count)  # floor(a·c/k² + 0.5)
    rgba = out.reshape(-1, 4)
    rgba[flat] = over(rgba[flat], src)


def render(
    slot: PolygonSlot, geometry: CanvasGeometry, records: Iterable[object], values: ArrayLike
) -> RGBA:
    """New pixels for ``slot`` from polygon records and their values.

    Returns a ``(height, width, 4)`` uint8 array of the slot's size. Raises
    ``PolygonDataError`` for invalid input and ``ProjectionError`` when the
    coordinates cannot be projected.
    """
    recs, vals = normalize(records, values)
    colors, orders = classify(slot, vals)
    height, width = slot.size((geometry.height, geometry.width))
    out = np.zeros((height, width, 4), np.uint8)
    geo = geometry.georeference
    if geo is None:
        raise ProjectionError(f"{slot.id}: polygon slots need a georeferenced geometry")
    k = slot.supersample
    bounds = sample_bounds(clip_bounds(slot, geometry), k)
    lengths = [[len(r) for r in rec] for rec in recs]
    points = [r for rec in recs for r in rec if len(r)]
    if not points:
        return out
    pts = np.concatenate(points)
    cols, rows = to_pixels(geo, pts[:, 0], pts[:, 1])
    cols -= slot.left
    rows -= slot.top
    offsets = np.cumsum([0] + [sum(ls) for ls in lengths])
    for i in np.argsort(orders, kind="stable"):
        ring_lengths = [n for n in lengths[i] if n]
        if not ring_lengths:
            continue
        sl = slice(int(offsets[i]), int(offsets[i + 1]))
        found = coverage(cols[sl], rows[sl], np.cumsum(ring_lengths), k, bounds)
        if found is not None:
            _blend(out, found[0], found[1], colors[i], k)
    return out


# --- GeoJSON-like input ----------------------------------------------------------------


def _geo(obj: object) -> Mapping[str, Any]:
    interface = getattr(obj, "__geo_interface__", None)
    data = interface if interface is not None else obj
    if not isinstance(data, Mapping):
        raise PolygonDataError(
            "expected a GeoJSON-like mapping or an object with __geo_interface__, "
            f"got {type(obj).__name__}"
        )
    return data


def geometry_rings(geometry: object) -> Record:
    """The rings of a GeoJSON-like ``Polygon`` or ``MultiPolygon`` (or ``None``: no rings).

    Accepts mappings and objects with ``__geo_interface__`` (shapely geometries, ...)
    without importing their libraries. Extra coordinate dimensions (z) are dropped.
    """
    if geometry is None:
        return []
    g = _geo(geometry)
    kind, coords = g.get("type"), g.get("coordinates")
    if kind == "Polygon":
        polygons = [coords or []]
    elif kind == "MultiPolygon":
        polygons = list(coords or [])
    else:
        raise PolygonDataError(f"expected a Polygon or MultiPolygon geometry, got {kind!r}")
    rings: Record = []
    for polygon in polygons:
        for ring in polygon:
            arr = np.asarray(ring, dtype=np.float64)
            if arr.ndim != 2 or arr.shape[1] < 2:
                raise PolygonDataError(
                    f"ring coordinates must be (lon, lat[, z]) pairs, got {arr.shape}"
                )
            rings.append(_ring(arr[:, :2], "geometry"))
    return rings


def records_from_features(
    features: object, value: str | Callable[[Mapping[str, Any]], float]
) -> tuple[list[Record], list[float]]:
    """``(records, values)`` for ``Scene.replace_polygons`` from GeoJSON-like features.

    ``features`` is a ``FeatureCollection`` mapping, an object with
    ``__geo_interface__`` (a GeoPandas ``GeoDataFrame``, ...), or an iterable of
    features. ``value`` names the property holding each feature's value, or is a
    function of the feature mapping.
    """
    data = getattr(features, "__geo_interface__", features)
    if isinstance(data, Mapping):
        if data.get("type") != "FeatureCollection":
            raise PolygonDataError(f"expected a FeatureCollection, got {data.get('type')!r}")
        items: Iterable[object] = data.get("features") or []
    else:
        items = data  # type: ignore[assignment]
    records: list[Record] = []
    values: list[float] = []
    for i, item in enumerate(items):
        f = _geo(item)
        if f.get("type") != "Feature":
            raise PolygonDataError(f"feature {i}: expected type 'Feature', got {f.get('type')!r}")
        if callable(value):
            v = value(f)
        else:
            props = f.get("properties") or {}
            if value not in props:
                raise PolygonDataError(f"feature {i} has no property {value!r}")
            v = props[value]
        records.append(geometry_rings(f.get("geometry")))
        values.append(v)
    return records, values
