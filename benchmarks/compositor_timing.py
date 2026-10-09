"""Session 06: compositing and PNG-encoding timing for the Session 01 grid and QPF scenes.

Builds format 1.0 scenes from the latest Session 02 prototype build (its layer PNGs,
grid index map, LUT, and fonts), saves them as ``.cstack``, then times in-process:
``Scene.load``, the first render (flattening static runs), warm renders (reusing
them), layer-by-layer renders (``flatten=False``), and PNG encoding. It also checks
renders against the prototype's Pillow stacking of the same arrays.

Runs in the project environment (it imports ``cartostack``):

    uv run python benchmarks/compositor_timing.py [--reps 20]

Writes ``benchmarks/results/compositor-<UTC stamp>/`` (``results.json``, scenes, PNGs).
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

import cartostack
from cartostack import CanvasGeometry, GridSlot, PolygonSlot, RasterLayer, Scene, TextSlot
from cartostack.compositor import encode_png
from cartostack.manifest import Bin

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
EXAMPLES = ROOT / "docs" / "examples"
FORBIDDEN = ("matplotlib", "cartopy", "pyproj", "shapely")


def latest_build() -> Path:
    builds = sorted(p for p in RESULTS.glob("prototype-*") if (p / "layers").is_dir())
    if not builds:
        sys.exit("no prototype build found; run `uv run benchmarks/prototype_build.py` first")
    return builds[-1]


def png(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        return np.asarray(img.convert("RGBA"))


def crop(px: np.ndarray) -> tuple[int, int, np.ndarray]:
    """Crop to the non-transparent bounding box: (left, top, pixels)."""
    ys, xs = np.nonzero(px[..., 3])
    top, left = int(ys.min()), int(xs.min())
    return left, top, px[top : ys.max() + 1, left : xs.max() + 1]


def text_slot(spec: dict, layer_px: np.ndarray, order: float) -> TextSlot:
    left, top, px = crop(layer_px)
    return TextSlot(
        id="subtitle", order=order, value=spec["text"], font=spec["font"], size_px=spec["size_px"],
        color=tuple(spec["color"]), x=spec["x"], y=spec["y"], ha=spec["ha"], va=spec["va"],
        snap=spec["snap"], offset=(0.0, spec["dy"]), left=left, top=top, pixels=px,
    )  # fmt: skip


def geometry(name: str) -> CanvasGeometry:
    return CanvasGeometry.from_dict(json.loads((EXAMPLES / f"{name}.manifest.json").read_text())["geometry"])


def grid_scene(build: Path) -> Scene:
    zf = zipfile.ZipFile(build / "grid-zlib.cstack")
    m = json.loads(zf.read("manifest.json"))
    slot = m["slots"]["grid"]
    index = np.frombuffer(zlib.decompress(zf.read(slot["index_map"]["member"])), "<i4")
    lut = np.frombuffer(zf.read(slot["lut"]["member"]), np.uint8).reshape(-1, 4)
    layers = build / "layers"
    spec = m["text_slots"]["subtitle"]
    return Scene(
        geometry("grid"),
        [
            RasterLayer(id="below", order=0, pixels=png(layers / "grid_below.png")),
            GridSlot(
                id="temperature", order=10, shape=tuple(slot["grid_shape"]),
                index_map=index.reshape(slot["index_map"]["shape"]).astype(np.int32), lut=lut,
                vmin=slot["vmin"], vmax=slot["vmax"], alpha=slot["alpha_u8"] / 255, extend="both",
                pixels=png(layers / "grid_data.png"),
            ),
            RasterLayer(id="above", order=20, pixels=png(layers / "grid_above.png")),
            text_slot(spec, png(layers / "grid_subtitle.png"), 30),
        ],
        assets={spec["font"]: zf.read(spec["font"])},
    )


def qpf_scene(build: Path, split: bool) -> Scene:
    zf = zipfile.ZipFile(build / ("qpf-zlib-split.cstack" if split else "qpf-zlib.cstack"))
    m = json.loads(zf.read("manifest.json"))
    s = m["slots"]["qpf"]
    bins = tuple(
        Bin(lo, hi, tuple(c), z) for (lo, hi), c, z in zip(s["ranges"], s["colors"], s["zorder"])
    )
    fb = Bin(0.0, 0.0, tuple(s["colors"][-1]), s["zorder"][-1])
    layers = build / "layers"
    spec = m["text_slots"]["subtitle"]
    stack = [
        RasterLayer(id="below", order=0, pixels=png(layers / "qpf_below.png")),
        PolygonSlot(
            id="qpf", order=10, bins=bins, fallback=fb, round_decimals=s["rounding"],
            supersample=s["supersample"], pixels=png(layers / "qpf_data.png"),
        ),
    ]
    if split:
        for i, rec in enumerate(r for r in m["layers"] if r["id"] != "below"):
            raw = zlib.decompress(zf.read(rec["member"]))
            px = np.frombuffer(raw, np.uint8).reshape(rec["shape"])
            stack.append(RasterLayer(id=rec["id"], order=20 + i, pixels=px))
    else:
        stack.append(RasterLayer(id="above", order=20, pixels=png(layers / "qpf_above.png")))
    stack.append(text_slot(spec, png(layers / "qpf_subtitle.png"), 30))
    return Scene(geometry("qpf"), stack, assets={spec["font"]: zf.read(spec["font"])})


def prototype_stack(scene: Scene) -> np.ndarray:
    """Session 02's ``composite_pillow``: Pillow alpha_composite of every layer in order."""
    order = [layer for layer in scene.draw_order() if layer.visible and layer.pixels is not None]
    img = Image.fromarray(np.array(order[0].pixels), "RGBA")
    for layer in order[1:]:
        img.alpha_composite(Image.fromarray(layer.pixels, "RGBA"), dest=(layer.left, layer.top))
    return np.asarray(img)


def timed(fn, reps: int) -> dict:
    times = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    return {"median_ms": statistics.median(times) * 1e3, "min_ms": min(times) * 1e3, "reps": reps}


def bench(name: str, scene: Scene, out: Path, reps: int) -> dict:
    path = out / f"{name}.cstack"
    scene.save(path)
    res: dict = {"canvas": [scene.geometry.width, scene.geometry.height], "layers": len(scene)}
    res["load"] = timed(lambda: Scene.load(path), reps)
    loaded = Scene.load(path)
    t = time.perf_counter()
    first = loaded.render()
    res["first_render_ms"] = (time.perf_counter() - t) * 1e3
    res["render_warm"] = timed(loaded.render, reps)
    res["render_layer_by_layer"] = timed(lambda: loaded.render(flatten=False), reps)
    single = loaded.render(flatten=False)
    res["flatten_max_diff"] = int(np.abs(first.astype(int) - single).max())
    res["flatten_differing_values"] = int((first != single).sum())
    res["equals_prototype_stacking"] = bool(np.array_equal(single, prototype_stack(loaded)))
    image = loaded._render_image(flatten=True)
    for mode in ("RGBA", "RGB"):
        for level in (1, 6):
            key = f"encode_{mode}_{level}"
            res[key] = timed(lambda m=mode, lv=level: encode_png(image, mode=m, compress_level=lv), reps)
            res[key]["bytes"] = len(encode_png(image, mode=mode, compress_level=level))
    loaded.save_png(out / f"{name}.png")
    with Image.open(out / f"{name}.png") as img:
        res["png_size"] = list(img.size)
        res["png_roundtrip_exact"] = bool(np.array_equal(np.asarray(img), first))
    res["save_png_rgba6"] = timed(lambda: loaded.save_png(out / f"{name}.png"), reps)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reps", type=int, default=20)
    args = ap.parse_args()
    build = latest_build()
    out = RESULTS / f"compositor-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True)
    results = {
        "build": str(build.relative_to(ROOT)),
        "cartostack": cartostack.__version__,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pillow": Image.__version__,
        "machine": platform.platform(),
        "scenes": {},
    }
    for name, make in [
        ("grid", lambda: grid_scene(build)),
        ("qpf", lambda: qpf_scene(build, split=False)),
        ("qpf-split", lambda: qpf_scene(build, split=True)),
    ]:
        results["scenes"][name] = r = bench(name, make(), out, args.reps)
        print(
            f"{name:10s} load {r['load']['median_ms']:6.1f} ms  first {r['first_render_ms']:6.1f} ms  "
            f"warm {r['render_warm']['median_ms']:6.1f} ms  per-layer {r['render_layer_by_layer']['median_ms']:6.1f} ms  "
            f"RGBA6 {r['encode_RGBA_6']['median_ms']:5.1f} ms  RGB1 {r['encode_RGB_1']['median_ms']:5.1f} ms  "
            f"flatten diff {r['flatten_max_diff']} ({r['flatten_differing_values']})  "
            f"=proto {r['equals_prototype_stacking']}  png exact {r['png_roundtrip_exact']}"
        )
    results["forbidden_modules_loaded"] = sorted(m for m in FORBIDDEN if m in sys.modules)
    (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
