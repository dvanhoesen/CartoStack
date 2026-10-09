"""Longitude/latitude → projected metres → canvas pixels, with NumPy only.

The runtime projects polygon-slot input itself instead of depending on pyproj
(``docs/format.md`` §5.5). Format 1.0 supports one projection, ``lcc``: the
ellipsoidal Lambert Conformal Conic with two standard parallels (Snyder 1987,
"Map Projections: A Working Manual", eq. 15-1 to 15-10; one standard parallel
when ``lat_1 == lat_2``). Results match PROJ's ``+proj=lcc`` to well under a
millimetre (``tests/test_projection.py``).

Input longitudes and latitudes are geodetic degrees on the projection's
ellipsoid, with no datum shift. Longitudes are reduced to within 180° of
``lon_0``, as PROJ does, so a ring that crosses the meridian opposite ``lon_0``
is split across the map rather than drawn continuously.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .errors import CartoStackError
from .geometry import SUPPORTED_PROJECTIONS, Georeference, Projection

Float64 = NDArray[np.float64]


class ProjectionError(CartoStackError, ValueError):
    """Coordinates cannot be projected (unsupported projection, invalid input, or a pole)."""


@dataclass(frozen=True)
class _LCC:
    e: float  # first eccentricity
    n: float  # cone constant
    af: float  # a * F
    rho0: float
    lam0: float  # radians
    x0: float
    y0: float


def _lcc_t(phi: float, e: float) -> float:
    es = e * math.sin(phi)
    return float(math.tan(math.pi / 4 - phi / 2) / ((1 - es) / (1 + es)) ** (e / 2))


def _lcc_m(phi: float, e: float) -> float:
    return math.cos(phi) / math.sqrt(1 - (e * math.sin(phi)) ** 2)


@lru_cache(maxsize=16)
def _lcc(projection: Projection) -> _LCC:
    p = projection.as_dict()
    missing = [k for k in SUPPORTED_PROJECTIONS["lcc"] if k not in p]
    if missing:
        raise ProjectionError(f"lcc projection is missing {', '.join(missing)}")
    a, f = p["a"], p["f"]
    if not (a > 0 and 0 <= f < 1):
        raise ProjectionError("lcc requires a > 0 and 0 <= f < 1")
    if any(abs(p[k]) >= 90 for k in ("lat_1", "lat_2")) or math.isclose(p["lat_1"], -p["lat_2"]):
        raise ProjectionError(
            "lcc standard parallels must lie in (-90, 90) and not be symmetric about the equator"
        )
    if abs(p["lat_0"]) > 90:
        raise ProjectionError("lcc lat_0 must lie within [-90, 90]")
    e = math.sqrt(f * (2 - f))
    phi1, phi2, phi0 = (math.radians(p[k]) for k in ("lat_1", "lat_2", "lat_0"))
    m1, t1 = _lcc_m(phi1, e), _lcc_t(phi1, e)
    if math.isclose(phi1, phi2):
        n = math.sin(phi1)
    else:
        m2, t2 = _lcc_m(phi2, e), _lcc_t(phi2, e)
        n = (math.log(m1) - math.log(m2)) / (math.log(t1) - math.log(t2))
    af = a * m1 / (n * t1**n)
    rho0 = af * _lcc_t(phi0, e) ** n if abs(p["lat_0"]) < 90 else 0.0
    if not math.isfinite(rho0) or (abs(p["lat_0"]) >= 90 and p["lat_0"] * n < 0):
        raise ProjectionError("lcc lat_0 lies at the pole opposite the cone's apex")
    return _LCC(e, n, af, rho0, math.radians(p["lon_0"]), p["x_0"], p["y_0"])


def forward(projection: Projection, lon: ArrayLike, lat: ArrayLike) -> tuple[Float64, Float64]:
    """Project geodetic degrees to projected metres ``(x, y)``.

    Raises ``ProjectionError`` for an unsupported projection, non-finite input,
    latitudes outside [-90, 90], or points at the pole opposite the cone's apex
    (which LCC maps to infinity).
    """
    if projection.name != "lcc":
        raise ProjectionError(
            f"projection {projection.name!r} is not supported by the runtime "
            f"(supported: {', '.join(SUPPORTED_PROJECTIONS)})"
        )
    c = _lcc(projection)
    lon_a = np.asarray(lon, np.float64)
    lat_a = np.asarray(lat, np.float64)
    if lon_a.size and not (np.isfinite(lon_a).all() and np.isfinite(lat_a).all()):
        raise ProjectionError("longitudes and latitudes must be finite")
    if lat_a.size and np.abs(lat_a).max() > 90:
        raise ProjectionError("latitudes must lie within [-90, 90]")
    if lat_a.size and (lat_a * math.copysign(1.0, c.n)).min() <= -90:
        raise ProjectionError(
            "points at the pole opposite the cone's apex cannot be projected with lcc"
        )
    phi = np.radians(lat_a)
    lam = (np.radians(lon_a) - c.lam0 + np.pi) % (2 * np.pi) - np.pi
    es = c.e * np.sin(phi)
    with np.errstate(divide="ignore", over="ignore"):
        t = np.tan(np.pi / 4 - phi / 2) / ((1 - es) / (1 + es)) ** (c.e / 2)
        rho = c.af * np.power(t, c.n)
    if not np.isfinite(rho).all():
        raise ProjectionError(
            "points at the pole opposite the cone's apex cannot be projected with lcc"
        )
    theta = c.n * lam
    return rho * np.sin(theta) + c.x0, c.rho0 - rho * np.cos(theta) + c.y0


def to_pixels(georef: Georeference, lon: ArrayLike, lat: ArrayLike) -> tuple[Float64, Float64]:
    """Project geodetic degrees to continuous canvas pixels ``(col, row)``.

    Pixel edges lie at integers and centres at half-integers (``docs/format.md`` §5.5).
    """
    x, y = forward(georef.projection, lon, lat)
    a, b, c, d, e, f = georef.world_to_pixel
    return a * x + b * y + c, d * x + e * y + f
