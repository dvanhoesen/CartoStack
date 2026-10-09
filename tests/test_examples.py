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
