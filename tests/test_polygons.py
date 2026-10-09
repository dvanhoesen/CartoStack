"""Polygon slots: classification, exact even-odd coverage, clipping, blending, and Scene use."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from helpers import full_scene, gradient, map_geometry, pixels_to_lonlat
from PIL import Image

from cartostack import (
    PolygonDataError,
    PolygonSlot,
    ProjectionError,
    RasterLayer,
    Scene,
    SceneError,
)
from cartostack import compositor as comp
from cartostack import polygons as poly
from cartostack.geometry import CanvasGeometry
from cartostack.manifest import Bin

RED = (255, 0, 0, 255)
GREEN = (0, 200, 0, 255)
BLUE = (0, 0, 255, 255)
BINS = (Bin(0.0, 1.0, RED, 10), Bin(1.0, 2.0, GREEN, 11), Bin(2.0, 3.0, BLUE, 12))


def slot(**kw: Any) -> PolygonSlot:
    return PolygonSlot(id="qpf", order=10, **{"bins": BINS, "supersample": 1, **kw})


def box(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def render(
    g: CanvasGeometry, s: PolygonSlot, records: Sequence[Any], values: Sequence[float]
) -> np.ndarray:
    """Render records given as rings in *pixel* coordinates (converted to lon/lat)."""
    lonlat = [[pixels_to_lonlat(g, ring) for ring in rec] for rec in records]
    return poly.render(s, g, lonlat, values)


def filled(height: int, width: int, boxes: dict[tuple[int, int, int, int], Any]) -> np.ndarray:
    out = np.zeros((height, width, 4), np.uint8)
    for (x0, y0, x1, y1), color in boxes.items():
        out[y0:y1, x0:x1] = color
    return out


# --- Rasterisation against an independent brute-force rule ------------------------------


def brute_counts(
    rings: list[np.ndarray], k: int, bounds: tuple[int, int, int, int], shape: tuple[int, int]
) -> np.ndarray:
    """Even-odd test of every sample centre against every edge (docs/format.md §9)."""
    c0, r0, c1, r1 = bounds
    h, w = shape
    ys = (np.arange(h * k) + 0.5)[:, None]
    xs = (np.arange(w * k) + 0.5)[None, :]
    inside = np.zeros((h * k, w * k), bool)
    for ring in rings:
        x, y = ring[:, 0] * k, ring[:, 1] * k
        for a, b, c, d in zip(x, y, np.roll(x, -1), np.roll(y, -1), strict=True):
            if b == d:
                continue
            crosses = (ys >= min(b, d)) & (ys < max(b, d))
            inside ^= crosses & (a + (ys - b) * (c - a) / (d - b) <= xs)
    jj, ii = np.arange(h * k)[:, None], np.arange(w * k)[None, :]
    inside &= (jj >= r0) & (jj < r1) & (ii >= c0) & (ii < c1)
    return inside.reshape(h, k, w, k).sum(axis=(1, 3))


@pytest.mark.parametrize("seed", range(6))
def test_coverage_equals_brute_force(seed: int) -> None:
    rng = np.random.default_rng(seed)
    h, w = 9, 11
    for trial in range(60):
        k = int(rng.integers(1, 6))
        rings = [
            rng.uniform(-3, 14, (int(rng.integers(3, 9)), 2)) for _ in range(rng.integers(1, 4))
        ]
        if trial % 3 == 0:  # vertices on the sample grid: exercises exact ties
            rings = [np.round(r * 2 * k) / (2 * k) for r in rings]
        clip = (
            max(rng.uniform(-1, 3), 0.0),
            max(rng.uniform(-1, 3), 0.0),
            min(rng.uniform(6, 12), w),
            min(rng.uniform(5, 10), h),
        )
        bounds = poly.sample_bounds(clip, k)
        pts = np.concatenate(rings)
        found = poly.coverage(pts[:, 0], pts[:, 1], np.cumsum([len(r) for r in rings]), k, bounds)
        got = np.zeros((h, w), int)
        if found is not None:
            (top, left), counts = found
            got[top : top + counts.shape[0], left : left + counts.shape[1]] = counts
        np.testing.assert_array_equal(got, brute_counts(rings, k, bounds, (h, w)))


def test_coverage_of_far_away_vertices() -> None:
    """Vertices far outside the clip still decide parity; only the clip is filled."""
    ring = np.array(box(-1e7, -1e7, 1e7, 1e7))
    found = poly.coverage(ring[:, 0], ring[:, 1], np.array([4]), 2, (0, 0, 10, 8))
    assert found is not None
    (top, left), counts = found
    assert (top, left) == (0, 0)
    np.testing.assert_array_equal(counts, np.full((4, 5), 4))


# --- Hand-computed pixels through projection and render ---------------------------------


def test_square_fills_exact_pixels() -> None:
    g = map_geometry()
    out = render(g, slot(), [[box(2, 3, 5, 6)]], [0.5])
    np.testing.assert_array_equal(out, filled(16, 20, {(2, 3, 5, 6): RED}))


def test_hole_and_island_follow_even_odd() -> None:
    g = map_geometry()
    rings = [box(1, 1, 9, 9), box(3, 3, 7, 7), box(4, 4, 6, 6)]  # outer, hole, island
    out = render(g, slot(), [rings], [0.5])
    want = filled(16, 20, {(1, 1, 9, 9): RED, (3, 3, 7, 7): (0, 0, 0, 0), (4, 4, 6, 6): RED})
    np.testing.assert_array_equal(out, want)


def test_ring_orientation_does_not_matter() -> None:
    g = map_geometry()
    a = render(g, slot(), [[box(1, 1, 9, 9), box(3, 3, 7, 7)]], [0.5])
    b = render(g, slot(), [[box(1, 1, 9, 9)[::-1], box(3, 3, 7, 7)]], [0.5])
    np.testing.assert_array_equal(a, b)


def test_supersampled_edges_blend_by_sample_count() -> None:
    """k = 2: samples at x = 0.25, 0.75, ...; [1.5, 3.5) covers 1, 2 and 1 sample columns."""
    g = map_geometry()
    out = render(g, slot(supersample=2), [[box(1.5, 1, 3.5, 2)]], [0.5])
    want = np.zeros((16, 20, 4), np.uint8)
    want[1, 1] = (255, 0, 0, 128)  # 2 of 4 samples: floor(255 * 2 / 4 + 0.5) = 128
    want[1, 2] = RED
    want[1, 3] = (255, 0, 0, 128)
    np.testing.assert_array_equal(out, want)


def test_partial_coverage_composites_over_earlier_records_like_pillow() -> None:
    g = map_geometry()
    out = render(g, slot(supersample=2), [[box(0, 0, 4, 4)], [box(1.5, 1, 3.5, 2)]], [0.5, 1.5])
    src = Image.new("RGBA", (1, 1), (0, 200, 0, 128))
    expect = Image.new("RGBA", (1, 1), RED)
    expect.alpha_composite(src)
    assert tuple(out[1, 1]) == expect.getpixel((0, 0))
    assert tuple(out[1, 2]) == GREEN
    assert tuple(out[0, 0]) == RED


def test_translucent_bin_colour_composites_even_when_fully_covered() -> None:
    g = map_geometry()
    bins = (Bin(0.0, 1.0, RED, 10), Bin(1.0, 2.0, (0, 0, 255, 100), 11))
    out = render(g, slot(bins=bins), [[box(0, 0, 4, 4)], [box(2, 0, 6, 4)]], [0.5, 1.5])
    expect = Image.new("RGBA", (1, 1), RED)
    expect.alpha_composite(Image.new("RGBA", (1, 1), (0, 0, 255, 100)))
    assert tuple(out[0, 3]) == expect.getpixel((0, 0))
    assert tuple(out[0, 5]) == (0, 0, 255, 100)  # over transparent: the colour itself


def test_draw_order_follows_bin_order_and_ties_keep_input_order() -> None:
    g = map_geometry()
    bins = (Bin(0.0, 1.0, RED, 12), Bin(1.0, 2.0, GREEN, 11), Bin(2.0, 3.0, BLUE, 11))
    records = [[box(0, 0, 6, 6)], [box(2, 2, 8, 8)], [box(4, 4, 10, 10)]]
    out = render(g, slot(bins=bins), records, [0.5, 1.5, 2.5])
    assert tuple(out[5, 5]) == RED  # order 12 is drawn last, on top
    assert tuple(out[7, 7]) == BLUE  # green and blue tie at 11: input order, blue later
    out = render(g, slot(bins=bins), records[::-1], [2.5, 1.5, 0.5])
    assert tuple(out[7, 7]) == GREEN


def test_clipped_to_fractional_axes() -> None:
    """Axes [2.3, 17.3) x [1.6, 13.6): samples outside are never inside."""
    g = map_geometry(axes=(2.3, 1.6, 15.0, 12.0))
    out = render(g, slot(supersample=2), [[box(-50, -50, 70, 70)]], [0.5])
    cov = np.rint(out[..., 3].astype(float) / 255 * 4).astype(int)
    # Columns: sample x = 2.25 is outside, 2.75 inside -> pixel 2 half; 17.25 inside,
    # 17.75 outside -> pixel 17 half. Rows: y = 1.25 out, 1.75 in -> row 1 half; row 13
    # samples 13.25 in, 13.75 out -> half.
    assert cov[5, 2] == 2
    assert cov[5, 3] == 4
    assert cov[5, 17] == 2
    assert cov[5, 18] == 0
    assert cov[1, 5] == 2
    assert cov[2, 5] == 4
    assert cov[13, 5] == 2
    assert cov[14, 5] == 0
    assert cov[1, 2] == 1
    assert cov[13, 17] == 1
    assert cov[:, :2].sum() == 0
    assert cov[:1].sum() == 0


def test_placed_layer_box_is_local_and_clipped() -> None:
    g = map_geometry()
    s = slot(left=3, top=2, width=6, height=5)
    out = render(g, s, [[box(0, 0, 20, 16)]], [0.5])
    np.testing.assert_array_equal(out, np.broadcast_to(np.array(RED, np.uint8), (5, 6, 4)))
    out = render(g, s, [[box(4, 3, 6, 4)]], [0.5])  # canvas pixels (4..5, 3) -> local (1..2, 1)
    np.testing.assert_array_equal(out, filled(5, 6, {(1, 1, 3, 2): RED}))


def test_polygon_outside_the_map_and_empty_records_draw_nothing() -> None:
    g = map_geometry()
    out = render(g, slot(), [[box(30, 30, 40, 40)], [], [box(-9, 2, -1, 9)]], [0.5, 0.5, 0.5])
    assert not out.any()
    assert not poly.render(slot(), g, [], []).any()


# --- Classification ---------------------------------------------------------------------


QPF_BINS = (Bin(0.01, 0.1, RED, 11), Bin(0.1, 0.25, GREEN, 12))
FALLBACK = Bin(0.0, 0.0, (128, 128, 128, 255), 10)


@pytest.mark.parametrize(
    ("value", "decimals", "color"),
    [
        (0.0949, 2, RED),  # rounds to 0.09
        (0.0951, 2, GREEN),  # rounds to 0.10: closed-open, so the second bin
        (0.1, None, GREEN),
        (0.0999, None, RED),
        (0.25, 2, FALLBACK.color),  # upper bound is open
        (float("nan"), 2, FALLBACK.color),
        (-1.0, None, FALLBACK.color),
    ],
)
def test_classification(value: float, decimals: int | None, color: tuple[int, ...]) -> None:
    s = slot(bins=QPF_BINS, fallback=FALLBACK, round_decimals=decimals)
    colors, _ = poly.classify(s, [value])
    assert tuple(colors[0]) == color


def test_first_matching_bin_in_list_order_wins() -> None:
    s = slot(bins=(Bin(0.0, 10.0, RED, 1), Bin(0.0, 1.0, GREEN, 2)))
    colors, orders = poly.classify(s, [0.5])
    assert tuple(colors[0]) == RED
    assert orders[0] == 1


def test_value_without_bin_or_fallback_is_rejected() -> None:
    with pytest.raises(PolygonDataError, match=r"no fallback \(first: record 1, value 7\.0\)"):
        poly.render(slot(), map_geometry(), [[], []], [0.5, 7.0])


# --- Input validation -------------------------------------------------------------------


def test_input_forms() -> None:
    g = map_geometry()
    ring = pixels_to_lonlat(g, box(2, 3, 5, 6))
    hole = pixels_to_lonlat(g, box(3, 4, 4, 5))
    want = poly.render(slot(), g, [[ring]], [0.5])
    np.testing.assert_array_equal(poly.render(slot(), g, [ring], [0.5]), want)  # bare ring
    np.testing.assert_array_equal(poly.render(slot(), g, [ring.tolist()], [0.5]), want)
    np.testing.assert_array_equal(poly.render(slot(), g, (r for r in [[ring]]), [0.5]), want)
    ragged = poly.render(slot(), g, [[ring, np.vstack([hole, hole[:1]])]], [0.5])  # 4 + 5 points
    assert tuple(ragged[4, 3]) == (0, 0, 0, 0)
    assert tuple(ragged[3, 2]) == RED
    np.testing.assert_array_equal(poly.render(slot(), g, [None], [0.5]), want * 0)
    np.testing.assert_array_equal(poly.render(slot(), g, [[ring]], np.array([0.5])), want)


@pytest.mark.parametrize(
    ("records", "values", "match"),
    [
        ([[[(0, 0), (1, 1), (1, 0)]]], [1, 2], "1 record"),
        ([[]], [[1.0]], "one-dimensional"),
        ([[]], [True], "real numbers"),
        ([[]], ["0.5"], "real numbers"),
        ([[[(0, 0, 0), (1, 1, 1), (1, 0, 0)]]], [0.5], r"shape \(n, 2\)"),
        ([[[(0, 0), (1, np.nan), (1, 0)]]], [0.5], "finite"),
        ([[["a", "b"]]], [0.5], "pairs"),
        (np.zeros((4, 2)), [0.5, 0.5, 0.5, 0.5], "wrap a single ring"),
        ([5.0], [0.5], "sequence of rings"),
    ],
)
def test_invalid_input(records: Any, values: Any, match: str) -> None:
    with pytest.raises(PolygonDataError, match=match):
        poly.render(slot(), map_geometry(), records, values)


def test_unprojectable_coordinates() -> None:
    with pytest.raises(ProjectionError, match="90"):
        poly.render(slot(), map_geometry(), [[[(-75, 40), (-74, 95), (-73, 40)]]], [0.5])


# --- GeoJSON-like input -----------------------------------------------------------------


class GeoThing:
    def __init__(self, data: dict[str, Any]) -> None:
        self.__geo_interface__ = data


def square(lon: float, lat: float, d: float = 1.0) -> list[list[float]]:
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]


def test_records_from_feature_collection() -> None:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"QPF": 0.5},
             "geometry": {"type": "Polygon",
                          "coordinates": [square(-76, 42, 2), square(-75.5, 42.5)]}},
            {"type": "Feature", "properties": {"QPF": 1.5},
             "geometry": {"type": "MultiPolygon",
                          "coordinates": [[square(-80, 40)],
                                          [[[*p, 7.0] for p in square(-70, 44)]]]}},
            {"type": "Feature", "properties": {"QPF": 2.5}, "geometry": None},
        ],
    }  # fmt: skip
    records, values = poly.records_from_features(fc, "QPF")
    assert values == [0.5, 1.5, 2.5]
    assert [len(r) for r in records] == [2, 2, 0]
    assert all(ring.shape == (5, 2) for rec in records for ring in rec)
    np.testing.assert_array_equal(records[1][1][0], [-70.0, 44.0])  # z dropped
    same = poly.records_from_features(GeoThing(fc), lambda f: f["properties"]["QPF"])
    assert same[1] == values
    feats = [GeoThing(f) for f in fc["features"]]
    assert poly.records_from_features(feats, "QPF")[1] == values
    g = map_geometry()
    a = poly.render(slot(), g, *poly.records_from_features(fc, "QPF"))
    b = poly.render(slot(), g, records, values)
    np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize(
    ("features", "match"),
    [
        ({"type": "Feature"}, "FeatureCollection"),
        ([{"type": "Point"}], "'Feature'"),
        ([{"type": "Feature", "properties": {}, "geometry": None}], "no property 'QPF'"),
        ([{"type": "Feature", "properties": {"QPF": 1},
           "geometry": {"type": "GeometryCollection", "geometries": []}}],
         "Polygon or MultiPolygon"),
        ([42], "GeoJSON-like"),
    ],
)  # fmt: skip
def test_invalid_features(features: Any, match: str) -> None:
    with pytest.raises(PolygonDataError, match=match):
        poly.records_from_features(features, "QPF")


def test_geometry_rings_accepts_geo_interface_objects() -> None:
    rings = poly.geometry_rings(GeoThing({"type": "Polygon", "coordinates": [square(-76, 42)]}))
    assert len(rings) == 1
    assert rings[0].shape == (5, 2)
    assert poly.geometry_rings(None) == []


# --- Scene -------------------------------------------------------------------------------


def map_scene() -> Scene:
    g = map_geometry()
    return Scene(
        g,
        [
            RasterLayer(id="below", order=0, pixels=gradient(16, 20)),
            slot(supersample=2),
            RasterLayer(id="a1", order=20, pixels=gradient(16, 20, 1) // 2),
            RasterLayer(id="a2", order=21, pixels=gradient(4, 5, 2), left=3, top=3),
        ],
    )


def qpf_input(g: CanvasGeometry) -> tuple[list[list[np.ndarray]], list[float]]:
    return (
        [[pixels_to_lonlat(g, box(1.3, 1.7, 9.2, 8.4)), pixels_to_lonlat(g, box(3, 3, 5, 5))],
         [pixels_to_lonlat(g, box(6.6, 5.1, 18.5, 14.9))]],
        [0.5, 1.5],
    )  # fmt: skip


def test_replace_polygons_swaps_in_a_new_slot() -> None:
    scene = map_scene()
    before = list(scene.layers)
    records, values = qpf_input(scene.geometry)
    new = scene.replace_polygons("qpf", records, values)
    assert scene["qpf"] is new
    assert new is not before[1]
    assert all(a is b for a, b in zip(scene.layers, before, strict=True) if a.id != "qpf")
    assert new.bins == before[1].bins
    assert new.supersample == 2
    np.testing.assert_array_equal(new.pixels, poly.render(new, scene.geometry, records, values))
    assert new.pixels is not None
    assert not new.pixels.flags.writeable


def test_failed_replacement_leaves_scene_unchanged() -> None:
    scene = map_scene()
    records, values = qpf_input(scene.geometry)
    scene.replace_polygons("qpf", records, values)
    before, image = list(scene.layers), scene.render()
    for bad in ([records, [0.5]], [records, [0.5, 9.0]], [[[[(0, 0), (1, 95), (2, 0)]]], [0.5]]):
        with pytest.raises((PolygonDataError, ProjectionError)):
            scene.replace_polygons("qpf", *bad)
        assert all(a is b for a, b in zip(scene.layers, before, strict=True))
        np.testing.assert_array_equal(scene.render(), image)


def test_replace_polygons_refuses_other_layers() -> None:
    scene = map_scene()
    with pytest.raises(SceneError, match="raster layer 'below' is not a polygon slot"):
        scene.replace_polygons("below", [], [])
    with pytest.raises(KeyError, match="nope"):
        scene.replace_polygons("nope", [], [])


def test_only_the_slot_is_recomposited(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = map_scene()
    records, values = qpf_input(scene.geometry)
    scene.replace_polygons("qpf", records, values)
    scene.render()
    calls: list[str] = []
    original = comp._over

    def spy(canvas: Image.Image, layer: Any, origin: tuple[int, int] = (0, 0)) -> None:
        calls.append(layer.id)
        original(canvas, layer, origin)

    monkeypatch.setattr(comp, "_over", spy)
    scene.replace_polygons("qpf", records[::-1], values[::-1])
    image = scene.render()
    assert calls == ["qpf"], calls  # below and the a1+a2 run are reused
    monkeypatch.undo()
    # The a1+a2 run above the slot is flattened first: within the documented 3 levels.
    diff = np.abs(image.astype(int) - scene.render(flatten=False).astype(int))
    assert diff.max() <= 3


def test_save_load_round_trip(tmp_path: Path) -> None:
    scene = map_scene()
    records, values = qpf_input(scene.geometry)
    scene.replace_polygons("qpf", records, values)
    path = tmp_path / "qpf.cstack"
    scene.save(path)
    loaded = Scene.load(path)
    np.testing.assert_array_equal(loaded["qpf"].pixels, scene["qpf"].pixels)
    np.testing.assert_array_equal(loaded.render(), scene.render())
    loaded.replace_polygons("qpf", records, values)
    np.testing.assert_array_equal(loaded.render(), scene.render())


def test_full_scene_polygon_slot_renders() -> None:
    """The shared test scene's slot (QPF geometry, scaled) takes real-looking lon/lat."""
    scene = full_scene()
    ring = [(-79.0, 41.0), (-73.0, 41.0), (-73.0, 44.5), (-79.0, 44.5)]
    new = scene.replace_polygons("qpf", [[ring]], [0.05])
    assert new.pixels is not None
    assert (new.pixels[..., 3] == 255).sum() > 0.25 * new.pixels.shape[0] * new.pixels.shape[1]
    assert tuple(new.pixels[new.pixels.shape[0] // 2, new.pixels.shape[1] // 2]) == (
        127,
        255,
        0,
        255,
    )


def test_runtime_path_loads_no_geospatial_libraries(tmp_path: Path) -> None:
    """Load, replace polygons, render and write PNG in a fresh process: NumPy and Pillow only."""
    scene = map_scene()
    path = tmp_path / "qpf.cstack"
    scene.save(path)
    records, values = qpf_input(scene.geometry)
    data = tmp_path / "input.json"
    data.write_text(json.dumps({"records": [[r.tolist() for r in rec] for rec in records],
                                "values": values}))  # fmt: skip
    code = (
        "import json, sys\n"
        "from cartostack import Scene\n"
        f"d = json.load(open({str(data)!r}))\n"
        f"s = Scene.load({str(path)!r})\n"
        "s.replace_polygons('qpf', d['records'], d['values'])\n"
        f"s.save_png({str(tmp_path / 'out.png')!r})\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = {name.split(".")[0] for name in json.loads(out.stdout)}
    forbidden = {"matplotlib", "cartopy", "pyproj", "shapely", "geopandas", "pyogrio"}
    assert loaded.isdisjoint(forbidden), sorted(loaded & forbidden)
    assert (tmp_path / "out.png").stat().st_size > 0
