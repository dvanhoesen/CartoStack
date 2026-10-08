"""Canvas geometry and georeferencing shared by every layer of a ``.cstack`` file.

Everything here is immutable and plain Python (no NumPy): a manifest's geometry
can be validated, compared, and fingerprinted without any extra installed.
See ``docs/format.md`` → "Geometry".
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from . import _validate as v
from .errors import GeometryError

MAX_CANVAS_PX: Final = 100_000
#: Corners of ``projected_extent`` must land on the axes corners within this many pixels.
EXTENT_TOLERANCE_PX: Final = 0.01
#: Projections the runtime can evaluate with NumPy, and their required parameters.
SUPPORTED_PROJECTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    "lcc": ("a", "f", "lat_0", "lon_0", "lat_1", "lat_2", "x_0", "y_0"),
}
CLIP_MODES: Final = ("axes",)


@dataclass(frozen=True)
class Rect:
    """A rectangle in canvas pixels; top-left origin, y down, pixel edges at integers."""

    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    def to_dict(self) -> dict[str, float]:
        return {
            "left": float(self.left),
            "top": float(self.top),
            "width": float(self.width),
            "height": float(self.height),
        }


@dataclass(frozen=True)
class FigureCrop:
    """How canvas pixels relate to the authoring figure (provenance of the geometry).

    ``crop_in`` is ``(x0, y0, width, height)`` in figure inches with Matplotlib's
    bottom-left origin, e.g. a resolved ``bbox_inches="tight"`` box; ``None``
    means the canvas is the whole figure. Canvas pixel ``(col, row)`` is figure
    point ``(x0 + col / dpi, y0 + height - row / dpi)`` inches at the canvas DPI.
    """

    width_in: float
    height_in: float
    dpi: float
    crop_in: tuple[float, float, float, float] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "width_in": float(self.width_in),
            "height_in": float(self.height_in),
            "dpi": float(self.dpi),
            "crop_in": None if self.crop_in is None else [float(c) for c in self.crop_in],
        }


@dataclass(frozen=True)
class Projection:
    """A named projection with plain numeric parameters (PROJ names: ``lat_0``, ``a``, ...)."""

    name: str
    params: tuple[tuple[str, float], ...]  # sorted by key

    @classmethod
    def create(cls, name: str, params: Mapping[str, float]) -> Projection:
        return cls(name, tuple(sorted((k, float(val)) for k, val in params.items())))

    def as_dict(self) -> dict[str, float]:
        return dict(self.params)

    @property
    def supported(self) -> bool:
        """True when the runtime can project lon/lat for this projection."""
        return self.name in SUPPORTED_PROJECTIONS

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, **self.as_dict()}


@dataclass(frozen=True)
class Georeference:
    """CRS, projected extent, and the affine from projected metres to canvas pixels.

    ``world_to_pixel`` is ``(a, b, c, d, e, f)`` with ``col = a*x + b*y + c`` and
    ``row = d*x + e*y + f``; pixel centres are at half-integers.
    """

    proj4: str
    wkt: str | None
    projection: Projection
    extent: tuple[float, float, float, float]  # xmin, xmax, ymin, ymax
    world_to_pixel: tuple[float, float, float, float, float, float]

    def to_pixel(self, x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.world_to_pixel
        return a * x + b * y + c, d * x + e * y + f

    def to_world(self, col: float, row: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.world_to_pixel
        det = a * e - b * d
        u, w = col - c, row - f
        return (e * u - b * w) / det, (a * w - d * u) / det

    def to_dict(self) -> dict[str, Any]:
        xmin, xmax, ymin, ymax = self.extent
        return {
            "crs": {"proj4": self.proj4, "wkt": self.wkt},
            "projection": self.projection.to_dict(),
            "projected_extent": {
                "xmin": float(xmin),
                "xmax": float(xmax),
                "ymin": float(ymin),
                "ymax": float(ymax),
            },
            "world_to_pixel": [float(t) for t in self.world_to_pixel],
        }


@dataclass(frozen=True)
class CanvasGeometry:
    """The pixel grid every layer shares; immutable and validated on construction."""

    width: int
    height: int
    dpi: float
    axes: Rect
    clip: str = "axes"
    figure: FigureCrop | None = None
    georeference: Georeference | None = None

    def __post_init__(self) -> None:
        problems = consistency_problems(self)
        if problems:
            raise GeometryError(problems)

    @classmethod
    def from_dict(cls, data: object, path: str = "geometry") -> CanvasGeometry:
        """Parse and validate the manifest's ``geometry`` object (raises ``GeometryError``)."""
        p = v.Problems()
        geometry = _parse(p, path, data)
        if p or geometry is None:
            raise GeometryError(p.items)
        return geometry

    def to_dict(self) -> dict[str, Any]:
        return {
            "canvas": {"width": self.width, "height": self.height, "dpi": float(self.dpi)},
            "axes": self.axes.to_dict(),
            "clip": self.clip,
            "figure": None if self.figure is None else self.figure.to_dict(),
            "georeference": None if self.georeference is None else self.georeference.to_dict(),
        }

    def fingerprint(self) -> str:
        """``sha256:<hex>`` of the canonical JSON of :meth:`to_dict` (see docs/format.md)."""
        return fingerprint_of(self.to_dict())

    def compatible_with(self, other: CanvasGeometry) -> bool:
        """Pixel ``(x, y)`` means the same place in both: identical fingerprints."""
        return self.fingerprint() == other.fingerprint()


def canonical_json(data: object) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def fingerprint_of(geometry_dict: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(geometry_dict)).hexdigest()


# --- Parsing -------------------------------------------------------------------------


def _parse(p: v.Problems, path: str, data: object) -> CanvasGeometry | None:
    g = v.obj(p, path, data)
    if g is None:
        return None
    canvas = v.obj(p, v.join(path, "canvas"), v.get(p, g, "canvas", path))
    width = height = None
    dpi = None
    if canvas is not None:
        cp = v.join(path, "canvas")
        width = v.integer(
            p, v.join(cp, "width"), v.get(p, canvas, "width", cp), lo=1, hi=MAX_CANVAS_PX
        )
        height = v.integer(
            p, v.join(cp, "height"), v.get(p, canvas, "height", cp), lo=1, hi=MAX_CANVAS_PX
        )
        dpi = v.number(p, v.join(cp, "dpi"), v.get(p, canvas, "dpi", cp), lo=0, lo_open=True)
    axes = _rect(p, v.join(path, "axes"), v.get(p, g, "axes", path))
    clip = v.string(p, v.join(path, "clip"), v.get(p, g, "clip", path, "axes"), choices=CLIP_MODES)
    fig_raw = v.get(p, g, "figure", path, None)
    figure = None if fig_raw is None else _figure(p, v.join(path, "figure"), fig_raw)
    geo_raw = v.get(p, g, "georeference", path, None)
    georef = None if geo_raw is None else _georeference(p, v.join(path, "georeference"), geo_raw)
    if p or width is None or height is None or dpi is None or axes is None or clip is None:
        return None
    if (fig_raw is not None and figure is None) or (geo_raw is not None and georef is None):
        return None
    try:
        return CanvasGeometry(width, height, dpi, axes, clip, figure, georef)
    except GeometryError as exc:
        for problem in exc.problems:
            p.add(path, problem)
        return None


def _rect(p: v.Problems, path: str, data: object) -> Rect | None:
    r = v.obj(p, path, data)
    if r is None:
        return None
    left = v.number(p, v.join(path, "left"), v.get(p, r, "left", path))
    top = v.number(p, v.join(path, "top"), v.get(p, r, "top", path))
    width = v.number(p, v.join(path, "width"), v.get(p, r, "width", path), lo=0, lo_open=True)
    height = v.number(p, v.join(path, "height"), v.get(p, r, "height", path), lo=0, lo_open=True)
    if left is None or top is None or width is None or height is None:
        return None
    return Rect(left, top, width, height)


def _figure(p: v.Problems, path: str, data: object) -> FigureCrop | None:
    f = v.obj(p, path, data)
    if f is None:
        return None
    w = v.number(p, v.join(path, "width_in"), v.get(p, f, "width_in", path), lo=0, lo_open=True)
    h = v.number(p, v.join(path, "height_in"), v.get(p, f, "height_in", path), lo=0, lo_open=True)
    dpi = v.number(p, v.join(path, "dpi"), v.get(p, f, "dpi", path), lo=0, lo_open=True)
    crop_raw = v.get(p, f, "crop_in", path, None)
    crop = None if crop_raw is None else v.numbers(p, v.join(path, "crop_in"), crop_raw, 4)
    if w is None or h is None or dpi is None or (crop_raw is not None and crop is None):
        return None
    c4 = None if crop is None else (crop[0], crop[1], crop[2], crop[3])
    return FigureCrop(w, h, dpi, c4)


def _georeference(p: v.Problems, path: str, data: object) -> Georeference | None:
    g = v.obj(p, path, data)
    if g is None:
        return None
    crs = v.obj(p, v.join(path, "crs"), v.get(p, g, "crs", path))
    proj4 = wkt = None
    if crs is not None:
        cp = v.join(path, "crs")
        proj4 = v.string(p, v.join(cp, "proj4"), v.get(p, crs, "proj4", cp))
        wkt_raw = v.get(p, crs, "wkt", cp, None)
        wkt = None if wkt_raw is None else v.string(p, v.join(cp, "wkt"), wkt_raw)
    projection = _projection(p, v.join(path, "projection"), v.get(p, g, "projection", path))
    ext = v.obj(p, v.join(path, "projected_extent"), v.get(p, g, "projected_extent", path))
    extent = None
    if ext is not None:
        ep = v.join(path, "projected_extent")
        vals = [
            v.number(p, v.join(ep, k), v.get(p, ext, k, ep))
            for k in ("xmin", "xmax", "ymin", "ymax")
        ]
        if None not in vals:
            xmin, xmax, ymin, ymax = (float(x) for x in vals if x is not None)
            if xmin >= xmax or ymin >= ymax:
                p.add(ep, "requires xmin < xmax and ymin < ymax")
            else:
                extent = (xmin, xmax, ymin, ymax)
    affine = v.numbers(p, v.join(path, "world_to_pixel"), v.get(p, g, "world_to_pixel", path), 6)
    if proj4 is None or projection is None or extent is None or affine is None:
        return None
    a, b, c, d, e, f = affine
    return Georeference(proj4, wkt, projection, extent, (a, b, c, d, e, f))


def _projection(p: v.Problems, path: str, data: object) -> Projection | None:
    pr = v.obj(p, path, data)
    if pr is None:
        return None
    name = v.string(p, v.join(path, "name"), v.get(p, pr, "name", path))
    if name is None:
        return None
    params: dict[str, float] = {}
    for key, value in pr.items():
        if key == "name":
            continue
        num = v.number(p, v.join(path, key), value)
        if num is not None:
            params[key] = num
    for key in SUPPORTED_PROJECTIONS.get(name, ()):
        if key not in pr:
            p.add(v.join(path, key), f"is required for projection {name!r}")
    if name == "lcc" and all(k in params for k in SUPPORTED_PROJECTIONS["lcc"]):
        if not (params["a"] > 0 and 0 <= params["f"] < 1):
            p.add(path, "lcc requires a > 0 and 0 <= f < 1")
        if any(abs(params[k]) >= 90 for k in ("lat_1", "lat_2")) or math.isclose(
            params["lat_1"], -params["lat_2"]
        ):
            p.add(
                path,
                "lcc standard parallels must lie in (-90, 90) and not be symmetric about "
                "the equator",
            )
    return Projection.create(name, params)


# --- Consistency ---------------------------------------------------------------------


def consistency_problems(g: CanvasGeometry) -> list[str]:
    """Cross-field checks between canvas, axes, figure crop, and georeference."""
    out: list[str] = []
    if not (isinstance(g.width, int) and 1 <= g.width <= MAX_CANVAS_PX):
        out.append(f"canvas width must be an integer in 1..{MAX_CANVAS_PX}")
    if not (isinstance(g.height, int) and 1 <= g.height <= MAX_CANVAS_PX):
        out.append(f"canvas height must be an integer in 1..{MAX_CANVAS_PX}")
    if not (math.isfinite(g.dpi) and g.dpi > 0):
        out.append("canvas dpi must be a positive number")
    if g.clip not in CLIP_MODES:
        out.append(f"clip must be one of {CLIP_MODES}")
    ax = g.axes
    if not all(math.isfinite(t) for t in (ax.left, ax.top, ax.width, ax.height)):
        out.append("axes must be finite")
    elif ax.width <= 0 or ax.height <= 0:
        out.append("axes width and height must be positive")
    elif ax.right <= 0 or ax.bottom <= 0 or ax.left >= g.width or ax.top >= g.height:
        out.append("axes do not intersect the canvas")
    if g.figure is not None and g.figure.crop_in is not None:
        _, _, cw, ch = g.figure.crop_in
        if cw <= 0 or ch <= 0:
            out.append("figure.crop_in width and height must be positive")
        else:
            for name, px, inches in (("width", g.width, cw), ("height", g.height, ch)):
                if abs(px - inches * g.dpi) >= 1:
                    out.append(
                        f"canvas {name} {px} does not match figure.crop_in {inches:g} in "
                        f"at {g.dpi:g} dpi ({inches * g.dpi:.3f} px)"
                    )
    elif g.figure is not None:
        for name, px, inches in (
            ("width", g.width, g.figure.width_in),
            ("height", g.height, g.figure.height_in),
        ):
            if abs(px - inches * g.dpi) >= 1:
                out.append(f"canvas {name} {px} does not match figure {name} at canvas dpi")
    geo = g.georeference
    if geo is not None:
        a, b, _, d, e, _ = geo.world_to_pixel
        if not math.isfinite(a * e - b * d) or abs(a * e - b * d) < 1e-300:
            out.append("world_to_pixel is not invertible")
        else:
            xmin, xmax, ymin, ymax = geo.extent
            corners = {
                "(xmin, ymax) -> axes top-left": (geo.to_pixel(xmin, ymax), (ax.left, ax.top)),
                "(xmax, ymin) -> axes bottom-right": (
                    geo.to_pixel(xmax, ymin),
                    (ax.right, ax.bottom),
                ),
            }
            for label, (got, want) in corners.items():
                err = max(abs(got[0] - want[0]), abs(got[1] - want[1]))
                if err > EXTENT_TOLERANCE_PX:
                    out.append(
                        f"georeference inconsistent with axes: {label} is off by {err:.4g} px"
                    )
    return out
