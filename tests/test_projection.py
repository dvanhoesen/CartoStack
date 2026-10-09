"""NumPy Lambert Conformal Conic against published values, an inverse, and PROJ."""

from __future__ import annotations

import numpy as np
import pytest
from helpers import lcc_inverse, small_georeferenced_geometry

from cartostack import ProjectionError
from cartostack.geometry import Projection
from cartostack.projection import forward, to_pixels

QPF = Projection.create(
    "lcc",
    {"a": 6378137.0, "f": 0.0033528106647474805, "lat_0": 42.95, "lon_0": -75.85,
     "lat_1": 33.0, "lat_2": 45.0, "x_0": 0.0, "y_0": 0.0},
)  # fmt: skip


def lcc(**kw: float) -> Projection:
    params = {"a": 6378137.0, "f": 1 / 298.257223563, "lat_0": 0.0, "lon_0": 0.0,
              "lat_1": 33.0, "lat_2": 45.0, "x_0": 0.0, "y_0": 0.0}  # fmt: skip
    return Projection.create("lcc", {**params, **kw})


def test_snyder_ellipsoidal_example() -> None:
    """Snyder (1987) p. 296: Clarke 1866, standard parallels 33° and 45°, origin 23°N 96°W."""
    a, b = 6378206.4, 6356583.8
    p = lcc(a=a, f=(a - b) / a, lat_0=23.0, lon_0=-96.0)
    x, y = forward(p, -75.0, 35.0)
    assert x == pytest.approx(1894410.9, abs=0.15)
    assert y == pytest.approx(1564649.5, abs=0.15)


def test_snyder_spherical_example() -> None:
    """Snyder (1987) p. 295: unit sphere, same parallels and origin."""
    x, y = forward(lcc(a=1.0, f=0.0, lat_0=23.0, lon_0=-96.0), -75.0, 35.0)
    assert x == pytest.approx(0.2966785, abs=1e-7)
    assert y == pytest.approx(0.2462112, abs=1e-7)


def test_origin_and_false_origin() -> None:
    x, y = forward(QPF, -75.85, 42.95)
    assert abs(x) < 1e-6
    assert abs(y) < 1e-6
    x, y = forward(lcc(lat_0=40.0, lon_0=-100.0, x_0=500.0, y_0=-200.0), -100.0, 40.0)
    assert x == pytest.approx(500.0, abs=1e-6)
    assert y == pytest.approx(-200.0, abs=1e-6)


@pytest.mark.parametrize(
    "p",
    [
        QPF,
        lcc(lat_1=40.5, lat_2=44.5, lat_0=42.5, lon_0=-76.0),
        lcc(lat_1=45.0, lat_2=45.0, lat_0=45.0, lon_0=10.0),  # tangent cone
        lcc(lat_1=-30.0, lat_2=-45.0, lat_0=-35.0, lon_0=145.0),  # southern hemisphere
        lcc(f=0.0, a=6371000.0),  # sphere
    ],
)
def test_round_trip_through_independent_inverse(p: Projection) -> None:
    q = p.as_dict()
    rng = np.random.default_rng(0)
    lon = q["lon_0"] + rng.uniform(-40, 40, 500)
    lat = np.clip(q["lat_0"] + rng.uniform(-25, 25, 500), -85, 85)
    x, y = forward(p, lon, lat)
    lon2, lat2 = lcc_inverse(p, x, y)
    assert np.abs(lat2 - lat).max() < 1e-9
    assert np.abs(((lon2 - lon + 180) % 360) - 180).max() < 1e-9


def test_longitudes_wrap_around_lon_0() -> None:
    lon = np.array([-80.0, 10.0, 170.0])
    lat = np.array([40.0, 50.0, 30.0])
    a = forward(QPF, lon, lat)
    b = forward(QPF, lon + 360.0, lat)
    c = forward(QPF, lon - 720.0, lat)
    np.testing.assert_allclose(a, b, atol=1e-6)
    np.testing.assert_allclose(a, c, atol=1e-6)


def test_apex_pole_is_finite_and_opposite_pole_is_refused() -> None:
    x, y = forward(QPF, 0.0, 90.0)  # northern cone: the apex
    assert np.isfinite(x)
    assert np.isfinite(y)
    with pytest.raises(ProjectionError, match="opposite"):
        forward(QPF, [0.0, 1.0], [10.0, -90.0])


@pytest.mark.parametrize(
    ("lon", "lat", "match"),
    [([0.0, np.nan], [0.0, 0.0], "finite"), ([0.0], [np.inf], "finite"), ([0.0], [90.5], "90")],
)
def test_invalid_coordinates(lon: list[float], lat: list[float], match: str) -> None:
    with pytest.raises(ProjectionError, match=match):
        forward(QPF, lon, lat)


@pytest.mark.parametrize(
    ("p", "match"),
    [
        (Projection.create("merc", {"a": 6378137.0}), "not supported"),
        (lcc(lat_1=30.0, lat_2=-30.0), "symmetric"),
        (lcc(lat_1=90.0), "symmetric"),
        (lcc(a=-1.0), "a > 0"),
        (lcc(lat_0=95.0), "lat_0"),
        (lcc(lat_0=-90.0), "opposite"),
        (Projection.create("lcc", {"a": 6378137.0, "f": 0.0}), "missing"),
    ],
)
def test_invalid_projections(p: Projection, match: str) -> None:
    with pytest.raises(ProjectionError, match=match):
        forward(p, [0.0], [10.0])


def test_empty_input() -> None:
    x, y = forward(QPF, [], [])
    assert x.shape == (0,)
    assert y.shape == (0,)


def test_to_pixels_applies_world_to_pixel() -> None:
    geo = small_georeferenced_geometry().georeference
    assert geo is not None
    lon = np.array([-80.0, -75.85, -72.0])
    lat = np.array([41.0, 42.95, 45.0])
    col, row = to_pixels(geo, lon, lat)
    x, y = forward(geo.projection, lon, lat)
    for i in range(3):
        assert (col[i], row[i]) == pytest.approx(geo.to_pixel(x[i], y[i]), abs=1e-9)


@pytest.mark.build
@pytest.mark.parametrize(
    "p",
    [
        QPF,
        lcc(lat_1=40.5, lat_2=44.5, lat_0=42.5, lon_0=-76.0),
        lcc(lat_1=45.0, lat_2=45.0, lat_0=45.0, lon_0=10.0),
        lcc(lat_1=-30.0, lat_2=-45.0, lat_0=-35.0, lon_0=145.0, x_0=1e6, y_0=2e6),
    ],
)
def test_matches_pyproj_to_a_millimetre(p: Projection) -> None:
    pyproj = pytest.importorskip("pyproj")
    q = p.as_dict()
    crs = pyproj.CRS.from_dict(
        {"proj": "lcc", "a": q["a"], "rf": 1 / q["f"], "lat_0": q["lat_0"], "lon_0": q["lon_0"],
         "lat_1": q["lat_1"], "lat_2": q["lat_2"], "x_0": q["x_0"], "y_0": q["y_0"]}
    )  # fmt: skip
    tr = pyproj.Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True)
    lon, lat = np.meshgrid(
        np.linspace(q["lon_0"] - 60, q["lon_0"] + 60, 61),
        np.clip(np.linspace(q["lat_0"] - 35, q["lat_0"] + 35, 41), -85, 85),
    )
    want_x, want_y = tr.transform(lon.ravel(), lat.ravel())
    x, y = forward(p, lon.ravel(), lat.ravel())
    err = np.hypot(x - want_x, y - want_y).max()
    assert err < 1e-3, f"max difference from PROJ: {err:.3g} m"
