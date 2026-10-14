"""The runtime examples run as documented, in a fresh process, without build libraries."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from helpers import full_scene
from PIL import Image, ImageFont

from cartostack import Scene

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
FORBIDDEN = ("matplotlib", "cartopy", "pyproj", "shapely", "geopandas", "pyogrio")

# Runs an example as ``python examples/<script> ...`` would, then reports what it imported.
RUN = """
import json, runpy, sys
script, *argv = sys.argv[1:]
sys.argv = [script, *argv]
sys.path.insert(0, str(__import__("pathlib").Path(script).parent))
runpy.run_path(script, run_name="__main__")
print(json.dumps(sorted(m for m in sys.modules if m.split(".")[0] in %r)))
""" % (FORBIDDEN,)  # noqa: UP031


def example_scene(path: Path) -> Scene:
    """The helpers' scene with a real font (Pillow's bundled one) for its text slot."""
    scene = full_scene()
    font = ImageFont.load_default(10).path
    assert isinstance(font, io.BytesIO)
    scene = Scene(scene.geometry, list(scene.layers), assets={"fonts/Test.ttf": font.getvalue()})
    scene.save(path)
    return scene


def run_example(script: str, *args: object, cwd: Path) -> tuple[str, list[str]]:
    res = subprocess.run(
        [sys.executable, "-c", RUN, str(EXAMPLES / script), *map(str, args)],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    )
    lines = res.stdout.strip().splitlines()
    return "\n".join(lines[:-1]), json.loads(lines[-1])


def test_update_grid_renders_each_input_and_changes_the_range(tmp_path: Path) -> None:
    scene = example_scene(tmp_path / "s.cstack")
    shape = scene["temperature"].shape
    rng = np.random.default_rng(3)
    for name in ("a", "b"):
        np.save(tmp_path / f"{name}.npy", rng.uniform(-10, 35, shape).astype(np.float32))
    out, imported = run_example(
        "update_grid.py", "s.cstack", "a.npy", "b.npy", "--out", "png", "--subtitle", "Run {name}",
        cwd=tmp_path,
    )  # fmt: skip
    assert imported == []
    assert "2 products" in out
    for name in ("a", "b"):
        scene.replace_grid("temperature", np.load(tmp_path / f"{name}.npy"))
        scene.text["subtitle"] = f"Run {name}"
        got = np.asarray(Image.open(tmp_path / "png" / f"{name}.png"))
        np.testing.assert_array_equal(got, scene.render())

    run_example("update_grid.py", "s.cstack", "b.npy", "--out", "png2", "--vmin", "0",
                "--vmax", "20", "--text", "", cwd=tmp_path)  # fmt: skip
    scene = Scene.load(tmp_path / "s.cstack")
    scene.replace_grid("temperature", np.load(tmp_path / "b.npy"), vmin=0, vmax=20)
    np.testing.assert_array_equal(
        np.asarray(Image.open(tmp_path / "png2" / "b.png")), scene.render()
    )


@pytest.mark.fixtures
def test_update_qpf_renders_wpc_products(tmp_path: Path, fixture_dir: Path) -> None:
    sys.path.insert(0, str(EXAMPLES))
    try:
        from wpc_qpf import read_product
    finally:
        sys.path.remove(str(EXAMPLES))
    scene = example_scene(tmp_path / "s.cstack")
    days = [fixture_dir / "qpf" / d for d in ("day_1", "day_1-3")]
    out, imported = run_example("update_qpf.py", "s.cstack", *days, "--out", "png", cwd=tmp_path)
    assert imported == []
    assert "2 products" in out
    for day in days:
        product = read_product(day)
        assert len(product.rings) == len(product.values) > 0
        assert product.subtitle.count("(Issued: ") == 1
        scene.replace_polygons("qpf", product.rings, product.values)
        scene.text["subtitle"] = product.subtitle
        got = np.asarray(Image.open(tmp_path / "png" / f"qpf_{day.name}.png"))
        np.testing.assert_array_equal(got, scene.render())


def _hazard_data():
    sys.path.insert(0, str(EXAMPLES))
    try:
        import hazard_data
    finally:
        sys.path.remove(str(EXAMPLES))
    return hazard_data


def test_hazard_geotiffs_round_trip_through_pillow(tmp_path: Path) -> None:
    hd = _hazard_data()
    paths = hd.make_inputs(tmp_path, days=2)
    src = hd.read_geotiff(paths[0])
    assert src.values.shape == hd.SRC_SHAPE
    assert (src.x0, src.y0, src.res, src.nodata) == (hd.SRC_X0, hd.SRC_Y0, hd.SRC_RES, hd.NODATA)
    assert set(np.unique(src.values)) <= set(range(6))


def test_hazard_reprojection_index_and_area_shares() -> None:
    hd = _hazard_data()
    values = np.zeros((4, 4), np.uint8)
    values[:, 2:] = 3
    src = hd.Raster(values, x0=0.0, y0=4.0, res=1.0, nodata=hd.NODATA)
    index = hd.reprojection_index(src, np.array([1e-6, 3.5 / hd.R * 180 / np.pi]), np.array([0.0]))
    assert index.tolist() == [[-1, -1]]  # y = 0 lies on the bottom edge: outside
    lat = np.degrees(2 * np.arctan(np.exp(np.array([2.5]) / hd.R)) - np.pi / 2)
    lon = np.degrees(np.array([0.5, 3.5]) / hd.R)
    index = hd.reprojection_index(src, lon, lat)
    assert index.tolist() == [[4, 7]]  # row 1, columns 0 and 3
    assert hd.regrid(src, index).tolist() == [[0, 3]]
    shares = hd.area_shares(src, np.array([4, 7, 7]), np.array([1.0, 1.0, 2.0]))
    assert shares == {"None": 25.0, "Low": 0.0, "Moderate": 0.0, "High": 75.0, "Extreme": 0.0}


@pytest.mark.build
@pytest.mark.fixtures
def test_hazard_example_authors_then_updates_without_build_libraries(tmp_path: Path) -> None:
    author = subprocess.run(
        [sys.executable, str(EXAMPLES / "author_hazard.py"), "scenes", "--inputs", "inputs"],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )  # fmt: skip
    assert "hazard_aux.npz" in author.stdout
    tifs = sorted(str(p) for p in (tmp_path / "inputs").glob("*.tif"))
    out, imported = run_example("update_hazard.py", "scenes", *tifs, "--out", "png", cwd=tmp_path)
    assert imported == []
    assert "14 maps" in out
    assert len(list((tmp_path / "png").glob("hazard_*_day*.png"))) == 14
    scene = Scene.load(tmp_path / "scenes" / "hazard_new_england.cstack")
    assert scene["hazard"].kind == "grid"
    assert not scene["all_clear"].visible


@pytest.mark.build
@pytest.mark.fixtures
def test_wrapping_an_unchanged_script_matches_the_authoring_example(
    tmp_path: Path, fixture_dir: Path
) -> None:
    day = str(fixture_dir / "qpf" / "day_1-3")
    for args in (
        ["author_qpf.py", "a.cstack", "--product", day],
        ["wrap_script.py", "b.cstack", str(EXAMPLES / "qpf_map.py"), day],
    ):
        subprocess.run(
            [sys.executable, str(EXAMPLES / args[0]), *args[1:]],
            capture_output=True, text=True, check=True, cwd=tmp_path,
        )  # fmt: skip
    a, b = Scene.load(tmp_path / "a.cstack"), Scene.load(tmp_path / "b.cstack")
    assert [la.id for la in a.draw_order()] == [la.id for la in b.draw_order()]
    np.testing.assert_array_equal(a.render(), b.render())
