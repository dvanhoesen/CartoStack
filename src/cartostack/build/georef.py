"""Canvas geometry and georeferencing of a Matplotlib/Cartopy figure (``docs/format.md`` §5)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from ..geometry import CanvasGeometry, FigureCrop, Georeference, Projection, Rect

Crop = tuple[float, float, float, float]  # x0, y0, width, height in inches, bottom-left origin


def resolve_crop(fig: Any, crop: str | Crop | None) -> Crop | None:
    """``"tight"`` → the figure's tight bounding box (resolved once, padding 0); else as given.

    Saving with the resolved box is byte-identical to ``bbox_inches="tight"``
    (Session 02), so the output size never drifts between builds.
    """
    if crop is None:
        return None
    if crop == "tight":
        fig.canvas.draw()
        bounds = fig.get_tightbbox(fig.canvas.get_renderer()).bounds
        return (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
    if isinstance(crop, str):
        raise ValueError(
            f"crop must be None, 'tight', or (x0, y0, width, height) inches; got {crop!r}"
        )
    x0, y0, w, h = (float(v) for v in crop)
    return (x0, y0, w, h)


def canvas_size(fig: Any, dpi: float, crop: Crop | None) -> tuple[int, int]:
    """``(width, height)`` of ``savefig(dpi=dpi, bbox_inches=crop)``, truncated as Agg does."""
    w_in, h_in = (crop[2], crop[3]) if crop else tuple(fig.get_size_inches())
    # Matplotlib truncates, except within 1e-8 px of the next integer (FigureCanvasBase).
    return int(w_in * dpi + 1e-8), int(h_in * dpi + 1e-8)


def display_to_canvas(
    fig: Any, dpi: float, crop: Crop | None
) -> Callable[[np.ndarray], np.ndarray]:
    """Map Matplotlib display coordinates (figure dpi, bottom-left) to canvas pixels (top-left)."""
    x0, y0, _, h_in = crop if crop else (0.0, 0.0, *fig.get_size_inches())
    fig_dpi = fig.dpi

    def to_canvas(xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        inches = xy / fig_dpi
        return np.column_stack([(inches[:, 0] - x0) * dpi, (y0 + h_in - inches[:, 1]) * dpi])

    return to_canvas


def axes_rect(ax: Any, to_canvas: Callable[[np.ndarray], np.ndarray]) -> Rect:
    """The axes' **resolved** rectangle (after Cartopy's aspect adjustment) in canvas pixels."""
    ax.apply_aspect()
    box = ax.get_window_extent()
    (left, top), (right, bottom) = to_canvas(np.array([[box.x0, box.y1], [box.x1, box.y0]]))
    return Rect(float(left), float(top), float(right - left), float(bottom - top))


def _projection(ax: Any) -> tuple[Projection, str, str | None]:
    import pyproj

    crs = ax.projection
    params = dict(crs.proj4_params)
    name = str(params.pop("proj"))
    ell = pyproj.CRS(crs.proj4_init).ellipsoid
    if ell is None:
        raise ValueError(f"cannot determine the ellipsoid of {crs.proj4_init!r}")
    inv_f = ell.inverse_flattening
    numeric: dict[str, float] = {
        "a": float(ell.semi_major_metre),
        "f": 0.0 if not inv_f else 1.0 / inv_f,
    }
    for key, value in params.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            numeric[key] = float(value)
    if name == "lcc":
        numeric.setdefault("lat_2", numeric["lat_1"])
        numeric.setdefault("x_0", 0.0)
        numeric.setdefault("y_0", 0.0)
        numeric.setdefault("lat_0", 0.0)
        numeric.setdefault("lon_0", 0.0)
        numeric = {
            k: numeric[k] for k in ("a", "f", "lat_0", "lon_0", "lat_1", "lat_2", "x_0", "y_0")
        }
    try:
        wkt: str | None = pyproj.CRS(crs.proj4_init).to_wkt()
    except pyproj.exceptions.CRSError:
        wkt = None
    return Projection.create(name, numeric), crs.proj4_init, wkt


def georeference(ax: Any, to_canvas: Callable[[np.ndarray], np.ndarray]) -> Georeference:
    """CRS, projected extent and the projected-metres → canvas-pixel affine of a GeoAxes."""
    projection, proj4, wkt = _projection(ax)
    xmin, xmax, ymin, ymax = (float(v) for v in ax.get_extent())
    span = max(xmax - xmin, ymax - ymin)
    pts = np.array([[xmin, ymin], [xmin + span, ymin], [xmin, ymin + span]])
    px = to_canvas(ax.transData.transform(pts))
    a, d = (px[1] - px[0]) / span
    b, e = (px[2] - px[0]) / span
    c = px[0, 0] - a * pts[0, 0] - b * pts[0, 1]
    f = px[0, 1] - d * pts[0, 0] - e * pts[0, 1]
    probe = np.array([[xmin + 0.37 * (xmax - xmin), ymin + 0.61 * (ymax - ymin)]])
    want = to_canvas(ax.transData.transform(probe))[0]
    got = (a * probe[0, 0] + b * probe[0, 1] + c, d * probe[0, 0] + e * probe[0, 1] + f)
    if max(abs(got[0] - want[0]), abs(got[1] - want[1])) > 1e-6:
        raise ValueError("the axes' data transform is not affine; cannot georeference this map")
    return Georeference(
        proj4,
        wkt,
        projection,
        (xmin, xmax, ymin, ymax),
        (float(a), float(b), float(c), float(d), float(e), float(f)),
    )


def canvas_geometry(fig: Any, ax: Any, *, dpi: float, crop: Crop | None) -> CanvasGeometry:
    """The ``.cstack`` geometry of ``fig`` saved at ``dpi`` with output crop ``crop``."""
    fig.canvas.draw()
    to_canvas = display_to_canvas(fig, dpi, crop)
    width, height = canvas_size(fig, dpi, crop)
    w_in, h_in = fig.get_size_inches()
    figure = FigureCrop(float(w_in), float(h_in), float(fig.dpi), crop)
    geo = georeference(ax, to_canvas) if hasattr(ax, "projection") else None
    return CanvasGeometry(
        width=width,
        height=height,
        dpi=float(dpi),
        axes=axes_rect(ax, to_canvas),
        figure=figure,
        georeference=geo,
    )
