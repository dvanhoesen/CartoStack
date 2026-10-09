"""Session 08: the synthetic grid workload on the ``cartostack`` package, in fresh processes.

Builds the Session 01 grid scene as a format 1.0 ``.cstack`` from the latest Session 02
prototype build (``compositor_timing.grid_scene``: static layers, the 500 × 500 grid's
index map and coolwarm LUT, the subtitle text slot and its font). Fresh child processes
load it and, per variant, generate the analytic field (``baseline_cartopy.make_field``,
timed as input), call ``Scene.replace_grid``, set the subtitle (``scene.text``;
Matplotlib 3.11 layout, ``snap: null``, ``offset: [0, 0]``), composite, and encode an
RGB PNG at level 6, as the Cartopy baseline writes it.

* cold: variant 0 alone, one product per process (``--cold-runs``, default 5);
* loop: variants 0-3 in one process (``--loop-runs``, default 2), PNGs compared with the
  full Cartopy renders of the same variants (Session 02's ``ref_grid_variant_*.png``)
  using the Session 02 comparison's regions;
* render timing, in this process: ``grids.render`` and ``Scene.replace_grid`` for the
  500 × 500 grid and an HRRR-sized 1799 × 1059 grid shown about one cell per pixel.

Runs in the project environment:

    uv run python benchmarks/package_grid.py
    <venv>/bin/python benchmarks/package_grid.py --scene ...   # an installed wheel (Session 15)

Writes ``benchmarks/results/package-grid-<UTC stamp>/``: ``bench.json`` (schema
``cartostack-bench/1``), ``results.json``, the scene, and the loop PNGs.
"""

from __future__ import annotations

import time

T0_PERF = time.perf_counter()
T0_WALL = time.time()

import argparse
import json
import resource
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
FORBIDDEN = ("matplotlib", "cartopy", "pyproj", "shapely", "geopandas", "pyogrio", "pandas")
SCHEMA = "cartostack-bench/1"
VARIANTS = ["0", "1", "2", "3"]


# --- Child ---------------------------------------------------------------------------


def child(args) -> dict:
    t_main = time.perf_counter()
    imports = {}
    t = time.perf_counter()
    import numpy  # noqa: F401

    imports["numpy"] = time.perf_counter() - t
    t = time.perf_counter()
    import PIL.Image  # noqa: F401

    imports["PIL"] = time.perf_counter() - t
    t = time.perf_counter()
    from cartostack import Scene
    from cartostack.compositor import encode_png

    imports["cartostack"] = time.perf_counter() - t

    sys.path.insert(0, str(Path(__file__).parent))
    from baseline_cartopy import make_field, title_text  # stdlib-only module top level

    t = time.perf_counter()
    scene = Scene.load(args.file)
    load_s = time.perf_counter() - t
    outdir = Path(args.outdir) if args.outdir else None
    products = []
    for v in args.items:
        ph = {}
        t = time.perf_counter()
        _, _, values = make_field(int(v))
        ph["read_input"] = time.perf_counter() - t
        t = time.perf_counter()
        scene.replace_grid("temperature", values)
        ph["data"] = time.perf_counter() - t
        t = time.perf_counter()
        text = title_text(int(v))[1]
        scene.text["subtitle"] = text
        ph["text"] = time.perf_counter() - t
        t = time.perf_counter()
        image = scene._render_image(flatten=True)  # what save_png encodes, without the copy
        ph["composite"] = time.perf_counter() - t
        t = time.perf_counter()
        png = encode_png(image, mode="RGB", compress_level=6)
        ph["encode"] = time.perf_counter() - t
        t = time.perf_counter()
        if outdir:
            (outdir / f"variant_{v}.png").write_bytes(png)
        ph["other"] = time.perf_counter() - t
        products.append({"name": f"variant_{v}", "phases_s": ph, "png_bytes": len(png)})
    end = time.perf_counter()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "pre_main_s": t_main - T0_PERF,
        "imports_s": imports,
        "load_s": load_s,
        "products": products,
        "end_to_end_in_process_s": end - T0_PERF,
        "script_start_wall": T0_WALL,
        "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "forbidden_modules_loaded": sorted(m for m in sys.modules if m.split(".")[0] in FORBIDDEN),
        "modules_loaded": len(sys.modules),
    }


# --- Orchestrator --------------------------------------------------------------------


def accuracy(build: Path, outdir: Path, references: Path) -> dict:
    import numpy as np
    from PIL import Image
    from prototype_compare import diff_stats, region_masks

    masks = region_masks(build / "layers", "grid")
    per = {}
    for v in VARIANTS:
        out = np.asarray(Image.open(outdir / f"variant_{v}.png").convert("RGB")).astype(np.int16)
        ref = np.asarray(Image.open(references / f"ref_grid_variant_{v}.png").convert("RGB")).astype(np.int16)
        per[f"variant_{v}"] = diff_stats(out, ref, masks)
    agg = {}
    for region in ("all", "map_clear", "under_mask", "text"):
        agg[region] = {m: statistics.median(p[region][m] for p in per.values())
                       for m in ("frac_gt8", "frac_gt32", "max")}  # fmt: skip
    return {"aggregate": agg, "per_product": per}


def timed(fn, reps: int) -> dict:
    times = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    return {"median_ms": statistics.median(times) * 1e3, "min_ms": min(times) * 1e3, "reps": reps}


def render_timing(scene, reps: int) -> dict:
    """``grids.render`` and ``replace_grid`` for the scene's grid and an HRRR-sized grid."""
    import numpy as np
    from baseline_cartopy import make_field

    from cartostack import CanvasGeometry, GridSlot, Scene, grids
    from cartostack.geometry import Rect

    out = {}
    slot = scene["temperature"]
    _, _, values = make_field(1)
    out["500x500"] = {
        "canvas": list(scene.shape),
        "valid_pixels": int((slot.index_map >= 0).sum()),
        "render": timed(lambda: grids.render(slot, values), reps),
        "replace_grid": timed(lambda: scene.replace_grid("temperature", values), reps),
    }
    ny, nx = 1059, 1799  # HRRR CONUS
    h, w = 1100, 1840  # about one cell per pixel, with a no-data border
    rows = np.clip(((np.arange(h) - 20) * ny / (h - 40)).astype(np.int64), -1, ny)
    cols = np.clip(((np.arange(w) - 20) * nx / (w - 40)).astype(np.int64), -1, nx)
    imap = (rows[:, None] * nx + cols[None, :]).astype(np.int32)
    imap[(rows[:, None] < 0) | (rows[:, None] >= ny) | (cols[None, :] < 0) | (cols[None, :] >= nx)] = -1
    hrrr = GridSlot(id="hrrr", order=0, shape=(ny, nx), index_map=imap, lut=slot.lut,
                    vmin=slot.vmin, vmax=slot.vmax, alpha=slot.alpha)  # fmt: skip
    rng = np.random.default_rng(0)
    vals = (rng.standard_normal((ny, nx)) * 10 + 12).astype(np.float32)
    vals[:50, :50] = np.nan
    hscene = Scene(CanvasGeometry(width=w, height=h, dpi=100.0, axes=Rect(0, 0, w, h)), [hrrr])
    out["1799x1059"] = {
        "canvas": [h, w],
        "valid_pixels": int((imap >= 0).sum()),
        "render": timed(lambda: grids.render(hrrr, vals), reps),
        "replace_grid": timed(lambda: hscene.replace_grid("hrrr", vals), reps),
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--file", help=argparse.SUPPRESS)
    parser.add_argument("--items", nargs="*", help=argparse.SUPPRESS)
    parser.add_argument("--outdir", help=argparse.SUPPRESS)
    parser.add_argument("--build", type=Path, help="prototype build directory (default: latest)")
    parser.add_argument("--scene", type=Path,
                        help="an authored .cstack (author_scenes.py) instead of a scene from the prototype layers")
    parser.add_argument("--references", type=Path,
                        help="directory with ref_grid_variant_*.png (default: latest prototype compare)")
    parser.add_argument("--cold-runs", type=int, default=5)
    parser.add_argument("--loop-runs", type=int, default=2)
    parser.add_argument("--reps", type=int, default=20, help="in-process render timing repetitions")
    args = parser.parse_args()
    if args.child:
        print(json.dumps(child(args)))
        return

    sys.path.insert(0, str(Path(__file__).parent))
    from compositor_timing import grid_scene
    from package_qpf import package_origin, run_child, to_record

    from cartostack.layers import evolve

    build = args.build or max(p for p in RESULTS.glob("prototype-*") if (p / "layers").is_dir())
    references = args.references or max(build.glob("runtime-*/compare"))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"package-grid-{stamp}"
    out.mkdir(parents=True)
    if args.scene:
        from cartostack import Scene

        scene = Scene.load(args.scene)
    else:
        scene = grid_scene(build)
    # Matplotlib 3.11 places text at fractional positions without Agg's old 1 px lift.
    if not args.scene:
        sub = scene["subtitle"]
        scene.replace_layer("subtitle", evolve(sub, snap=None, offset=(0.0, 0.0)))
        scene.text["subtitle"] = sub.value
    path = out / "grid.cstack"
    scene.save(path)

    def argv(items: list[str], outdir: Path | None = None) -> list[str]:
        a = ["--file", str(path), "--items", *items]
        return a + (["--outdir", str(outdir)] if outdir else [])

    run_child(argv(["0"]), __file__)  # priming (bytecode, OS cache); not recorded
    results: dict = {"build": str(build.relative_to(ROOT)), "references": str(references.relative_to(ROOT)),
                     "created_utc": stamp, "python": sys.version.split()[0], "cartostack": package_origin(), "cold": [], "loop": []}  # fmt: skip
    for i in range(args.cold_runs):
        r = run_child(argv(["0"]), __file__)
        results["cold"].append(r)
        print(f"cold run {i}: {r['interpreter_start_s'] + r['end_to_end_in_process_s']:.3f} s "
              f"(grid {r['products'][0]['phases_s']['data'] * 1000:.1f} ms)")
    for i in range(args.loop_runs):
        d = out / "products"
        d.mkdir(exist_ok=True)
        r = run_child(argv(VARIANTS, d if i == 0 else None), __file__)
        results["loop"].append(r)
        data = statistics.median(p["phases_s"]["data"] for p in r["products"])
        print(f"loop run {i}: {r['interpreter_start_s'] + r['end_to_end_in_process_s']:.3f} s, "
              f"grid median {data * 1000:.1f} ms")
    acc = accuracy(build, out / "products", references)
    a = acc["aggregate"]
    print(f"accuracy: all >8 {a['all']['frac_gt8'] * 100:.2f} %, >32 {a['all']['frac_gt32'] * 100:.3f} %; "
          f"map_clear >8 {a['map_clear']['frac_gt8'] * 100:.2f} %, max {a['map_clear']['max']:.0f}")
    print(f"text region: >8 {a['text']['frac_gt8'] * 100:.2f} %, >32 {a['text']['frac_gt32'] * 100:.3f} %, max {a['text']['max']:.0f}")
    results["accuracy"] = acc
    results["render_timing"] = render_timing(scene, args.reps)
    for name, rt in results["render_timing"].items():
        print(f"render {name}: grids.render {rt['render']['median_ms']:.1f} ms, "
              f"replace_grid {rt['replace_grid']['median_ms']:.1f} ms ({rt['valid_pixels']} pixels)")
    note = ("cartostack package: Scene.load, replace_grid (index map + LUT), subtitle via "
            "scene.text, compositor, PNG RGB 6.")
    records = [
        to_record("cold-process", "zlib", results["cold"], out / "results.json", None, "grid", note),
        to_record("loop", "zlib", results["loop"], out / "results.json", acc, "grid", note),
    ]
    (out / "results.json").write_text(json.dumps(results, indent=1))
    (out / "bench.json").write_text(json.dumps({"schema": SCHEMA, "records": records}, indent=1))
    print(f"results → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
