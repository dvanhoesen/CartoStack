"""Grid slots: Matplotlib's normalisation and colormap rules, index-map gathering, and Scene use."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from helpers import full_scene, gradient, map_geometry, unpack
from PIL import Image

from cartostack import (
    ColorbarLayer,
    GridMismatchError,
    GridSlot,
    LayerError,
    RasterLayer,
    Scene,
    SceneError,
    grids,
)
from cartostack import compositor as comp

# Four colours, then under, over, bad.
LUT = np.array(
    [[10, 0, 0, 255], [20, 0, 0, 255], [30, 0, 0, 255], [40, 0, 0, 255],
     [0, 0, 255, 255], [255, 0, 0, 255], [0, 0, 0, 0]],
    np.uint8,
)  # fmt: skip
UNDER, OVER, BAD = LUT[4], LUT[5], LUT[6]


def slot(**kw: Any) -> GridSlot:
    index_map = np.array([[-1, 0, 1, 2], [3, 4, 5, -1]], np.int32)
    params = {"id": "t", "order": 10, "shape": (2, 3), "index_map": index_map, "lut": LUT,
              "vmin": 0.0, "vmax": 4.0}  # fmt: skip
    return GridSlot(**{**params, **kw})


def colors(s: GridSlot, values: Any) -> np.ndarray:
    return grids.cell_colors(s, values)


# --- Values -> colours --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, LUT[0]),
        (0.999, LUT[0]),
        (1.0, LUT[1]),
        (2.5, LUT[2]),
        (3.999, LUT[3]),
        (4.0, LUT[3]),  # x == n_colors becomes the last colour
        (4.0001, OVER),
        (-1e-6, UNDER),
        (np.inf, OVER),
        (-np.inf, UNDER),
        (np.nan, BAD),
    ],
)
def test_value_to_colour(value: float, expected: np.ndarray) -> None:
    values = np.full((2, 3), value, np.float32)
    np.testing.assert_array_equal(colors(slot(), values), np.broadcast_to(expected, (6, 4)))


def test_masked_values_are_bad_even_out_of_range() -> None:
    values = np.ma.masked_array([[1.0, 50.0, -50.0], [2.0, 3.0, np.nan]],
                                mask=[[True, True, True], [False, False, False]])  # fmt: skip
    got = colors(slot(), values)
    np.testing.assert_array_equal(got, [BAD, BAD, BAD, LUT[2], LUT[3], BAD])


def test_alpha_replaces_alpha_except_for_an_all_zero_bad_colour() -> None:
    values = np.array([[0.5, np.nan, 9.0], [-9.0, 1.5, 2.5]], np.float32)
    got = colors(slot(alpha=0.8), values)
    assert (got[[0, 2, 3, 4, 5], 3] == 204).all()  # floor(0.8 * 255 + 0.5)
    np.testing.assert_array_equal(got[1], [0, 0, 0, 0])
    lut = LUT.copy()
    lut[-1] = (128, 128, 128, 255)  # a visible bad colour takes the alpha
    got = colors(slot(alpha=0.7, lut=lut), values)
    np.testing.assert_array_equal(got[1], [128, 128, 128, 179])  # floor(178.5 + 0.5)


def test_arithmetic_is_float64_rounded_to_value_dtype() -> None:
    """Range 0.1..0.7: float32 arithmetic would give different bins for some values."""
    rng = np.random.default_rng(0)
    values = rng.uniform(0.05, 0.75, (300, 300)).astype(np.float32)
    s = GridSlot(id="t", order=0, shape=(300, 300), index_map=np.zeros((2, 2), np.int32),
                 lut=np.zeros((259, 4), np.uint8), vmin=0.1, vmax=0.7)  # fmt: skip
    x = ((values.astype(np.float64) - 0.1).astype(np.float32).astype(np.float64) / 0.6).astype(
        np.float32
    )
    x *= np.float32(256)
    x[x == 256] = 255
    want = np.where(x < 0, 256, np.where(x >= 256, 257, np.floor(x))).astype(int).ravel()
    np.testing.assert_array_equal(grids.lut_rows(s, values), want)
    f32 = values.copy()
    f32 -= 0.1
    f32 /= 0.6
    assert (f32 != x / np.float32(256)).any()  # the float32 shortcut really differs here


def test_value_dtype_float64() -> None:
    values = np.array([[1.0 - 1e-12, 1.0, 2.0], [3.0, 4.0, 5.0]])
    got = grids.lut_rows(slot(value_dtype="float64"), values)
    np.testing.assert_array_equal(got, [0, 1, 2, 3, 3, 5])
    # In float32, 1 - 1e-12 rounds to 1.0 and lands in the second colour.
    np.testing.assert_array_equal(grids.lut_rows(slot(), values)[:2], [1, 1])


def test_integer_values_are_accepted() -> None:
    got = grids.lut_rows(slot(), np.array([[0, 1, 2], [3, 4, 5]], np.int64))
    np.testing.assert_array_equal(got, [0, 1, 2, 3, 3, 5])


@pytest.mark.parametrize(
    ("values", "match"),
    [
        (np.zeros((3, 2)), r"shape \(3, 2\), the grid is \(2, 3\)"),
        (np.zeros(6), r"shape \(6,\)"),
        (np.zeros((2, 3), bool), "real numbers"),
        (np.zeros((2, 3), complex), "real numbers"),
        (np.array([["a"] * 3] * 2), "real numbers"),
    ],
)
def test_invalid_values(values: np.ndarray, match: str) -> None:
    with pytest.raises(GridMismatchError, match=match):
        grids.render(slot(), values)


# --- Values -> pixels ---------------------------------------------------------------------


def test_index_map_gathers_cells_and_minus_one_is_transparent() -> None:
    values = np.array([[0.5, 1.5, 2.5], [3.5, 9.0, -9.0]], np.float32)
    got = grids.render(slot(), values)
    want = np.array([[[0, 0, 0, 0], LUT[0], LUT[1], LUT[2]],
                     [LUT[3], OVER, UNDER, [0, 0, 0, 0]]], np.uint8)  # fmt: skip
    np.testing.assert_array_equal(got, want)


def test_domain_edges_and_repeated_cells() -> None:
    """A 2x2 grid magnified 3x with a no-data border: each cell fills a 3x3 block."""
    imap = np.full((8, 8), -1, np.int32)
    imap[1:7, 1:7] = np.kron(np.arange(4, dtype=np.int32).reshape(2, 2), np.ones((3, 3), np.int32))
    s = slot(shape=(2, 2), index_map=imap)
    got = grids.render(s, np.array([[0.5, 1.5], [np.nan, 3.5]], np.float32))
    border = np.ones((8, 8), bool)
    border[1:7, 1:7] = False
    assert not got[border].any()
    np.testing.assert_array_equal(got[1:4, 1:4], np.broadcast_to(LUT[0], (3, 3, 4)))
    np.testing.assert_array_equal(got[1:4, 4:7], np.broadcast_to(LUT[1], (3, 3, 4)))
    np.testing.assert_array_equal(got[4:7, 1:4], np.zeros((3, 3, 4)))  # bad colour is transparent
    np.testing.assert_array_equal(got[4:7, 4:7], np.broadcast_to(LUT[3], (3, 3, 4)))


# --- Matplotlib -----------------------------------------------------------------------------


@pytest.mark.build
@pytest.mark.parametrize(
    ("vmin", "vmax"), [(-10.0, 35.0), (0.1, 0.7), (250.15, 310.7), (1e-5, 3e-5)]
)
def test_matches_matplotlib_exactly(vmin: float, vmax: float) -> None:
    import matplotlib as mpl
    from matplotlib.colors import Normalize

    cmap = mpl.colormaps["coolwarm"].with_extremes(under="navy", over="darkred", bad="0.5")
    n = cmap.N
    lut = np.vstack([cmap(np.arange(n), bytes=True), cmap(-1.0, bytes=True), cmap(2.0, bytes=True),
                     cmap(np.ma.masked_invalid([np.nan]), bytes=True)])  # fmt: skip
    rng = np.random.default_rng(1)
    span = vmax - vmin
    values = rng.uniform(vmin - 0.1 * span, vmax + 0.1 * span, (400, 500)).astype(np.float32)
    values[0, :50] = np.nan
    edges = (vmin + span * np.arange(n + 1) / n).astype(np.float32)
    values[1, : n + 1] = edges
    values[2, : n + 1] = np.nextafter(edges, np.float32(-np.inf))
    s = GridSlot(id="t", order=0, shape=values.shape, index_map=np.zeros((2, 2), np.int32),
                 lut=lut, vmin=vmin, vmax=vmax)  # fmt: skip
    want = cmap(Normalize(vmin, vmax)(values), bytes=True).reshape(-1, 4)
    np.testing.assert_array_equal(grids.cell_colors(s, values), want)
    alpha = dataclasses.replace(s, alpha=0.8)
    want = cmap(Normalize(vmin, vmax)(values), alpha=0.8, bytes=True).reshape(-1, 4)
    np.testing.assert_array_equal(grids.cell_colors(alpha, values), want)


# --- Scene ------------------------------------------------------------------------------------


def grid_scene() -> Scene:
    g = map_geometry(width=8, height=6)
    imap = np.full((6, 8), -1, np.int32)
    imap[1:5, 1:7] = np.arange(24, dtype=np.int32).reshape(4, 6)
    return Scene(
        g,
        [
            RasterLayer(id="below", order=0, pixels=gradient(6, 8)),
            GridSlot(
                id="t",
                order=10,
                shape=(4, 6),
                index_map=imap,
                lut=LUT,
                vmin=0.0,
                vmax=4.0,
                alpha=0.8,
            ),
            RasterLayer(id="a1", order=20, pixels=gradient(6, 8, 1) // 2),
            RasterLayer(id="a2", order=21, pixels=gradient(2, 3, 2), left=2, top=2),
            ColorbarLayer(id="cbar", order=30, slot="t", pixels=gradient(1, 8, 3), top=5),
        ],
    )


def values(seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).uniform(-1, 5, (4, 6)).astype(np.float32)


def test_replace_grid_swaps_in_a_new_slot() -> None:
    scene = grid_scene()
    before = list(scene.layers)
    new = scene.replace_grid("t", values())
    assert scene["t"] is new
    assert new is not before[1]
    assert all(a is b for a, b in zip(scene.layers, before, strict=True) if a.id != "t")
    assert new.index_map is before[1].index_map  # shared, not copied
    np.testing.assert_array_equal(new.pixels, grids.render(new, values()))
    assert scene.stale_colorbars == ()


def test_failed_replacement_leaves_scene_unchanged() -> None:
    scene = grid_scene()
    scene.replace_grid("t", values())
    before, image = list(scene.layers), scene.render()
    bad_calls: list[tuple[Any, dict[str, Any]]] = [
        (np.zeros((6, 4)), {}),
        (np.zeros((4, 6), bool), {}),
        (values(), {"vmin": 5.0}),  # vmin >= vmax
        (values(), {"lut": np.zeros((2, 4), np.uint8)}),
        (np.zeros((6, 4)), {"vmax": 9.0}),
    ]
    for vals, kw in bad_calls:
        with pytest.raises((GridMismatchError, LayerError)):
            scene.replace_grid("t", vals, **kw)
        assert all(a is b for a, b in zip(scene.layers, before, strict=True))
        np.testing.assert_array_equal(scene.render(), image)
    assert scene.stale_colorbars == ()


def test_restyling_marks_colorbars_stale_until_replaced_or_removed() -> None:
    scene = grid_scene()
    scene.replace_grid("t", values(), vmin=0.0, vmax=4.0)  # unchanged: not stale
    assert scene.stale_colorbars == ()
    new = scene.replace_grid("t", values(), vmin=-1.0, vmax=5.0)
    assert (new.vmin, new.vmax) == (-1.0, 5.0)
    assert scene.stale_colorbars == ("cbar",)
    scene.replace_layer("cbar", gradient(1, 8, 4))
    assert scene.stale_colorbars == ()
    lut = np.vstack([LUT[:4], LUT[:4], LUT[4:]])  # 8 colours now
    new = scene.replace_grid("t", values(), lut=lut)
    assert new.n_colors == 8
    assert scene.stale_colorbars == ("cbar",)
    scene.remove_layer("cbar")
    assert scene.stale_colorbars == ()


def test_new_norm_and_lut_are_saved(tmp_path: Path) -> None:
    path = tmp_path / "t.cstack"
    grid_scene().save(path)
    scene = Scene.load(path)
    lut = np.vstack([LUT[:4][::-1], LUT[4:]])
    scene.replace_grid("t", values(), vmin=-2.0, vmax=6.0, lut=lut)
    out = tmp_path / "out.cstack"
    scene.save(out)
    manifest, members = unpack(out)
    rec = next(r for r in manifest["layers"] if r["id"] == "t")
    assert rec["grid"]["norm"] == {"kind": "linear", "vmin": -2.0, "vmax": 6.0}
    assert members[rec["grid"]["colormap"]["member"]] == lut.tobytes()
    loaded = Scene.load(out)
    np.testing.assert_array_equal(loaded["t"].lut, lut)
    np.testing.assert_array_equal(loaded.render(), scene.render())
    loaded.replace_grid("t", values(1))
    scene.replace_grid("t", values(1))
    np.testing.assert_array_equal(loaded.render(), scene.render())


def test_only_the_slot_is_recomposited(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = grid_scene()
    scene.replace_grid("t", values())
    scene.render()
    calls: list[str] = []
    original = comp._over

    def spy(canvas: Image.Image, layer: Any, origin: tuple[int, int] = (0, 0)) -> None:
        calls.append(layer.id)
        original(canvas, layer, origin)

    monkeypatch.setattr(comp, "_over", spy)
    scene.replace_grid("t", values(1))
    scene.render()
    assert calls == ["t"], calls  # below and the a1+a2+cbar run are reused


def test_replace_grid_refuses_other_layers() -> None:
    scene = grid_scene()
    with pytest.raises(SceneError, match="raster layer 'below' is not a grid slot"):
        scene.replace_grid("below", values())
    with pytest.raises(KeyError, match="nope"):
        scene.replace_grid("nope", values())


def test_full_scene_grid_slot() -> None:
    scene = full_scene()
    slot_ = scene["temperature"]
    assert isinstance(slot_, GridSlot)
    vals = np.linspace(-15, 40, slot_.shape[0] * slot_.shape[1], dtype=np.float32).reshape(
        slot_.shape
    )
    new = scene.replace_grid("temperature", vals)
    assert new.pixels is not None
    assert not new.pixels[:2].any()  # index -1 rows
    assert (new.pixels[2:, :, 3] == 204).all()  # alpha 0.8; no bad values


def test_runtime_path_loads_no_rendering_libraries(tmp_path: Path) -> None:
    path = tmp_path / "t.cstack"
    grid_scene().save(path)
    np.save(tmp_path / "v.npy", values())
    code = (
        "import json, sys\n"
        "import numpy as np\n"
        "from cartostack import Scene\n"
        f"s = Scene.load({str(path)!r})\n"
        f"s.replace_grid('t', np.load({str(tmp_path / 'v.npy')!r}), vmin=-1.0, vmax=5.0)\n"
        f"s.save_png({str(tmp_path / 'out.png')!r})\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = {name.split(".")[0] for name in json.loads(out.stdout)}
    forbidden = {"matplotlib", "cartopy", "pyproj", "shapely"}
    assert loaded.isdisjoint(forbidden), sorted(loaded & forbidden)
    assert (tmp_path / "out.png").stat().st_size > 0
