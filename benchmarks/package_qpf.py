"""Session 08b: the QPF workload on the ``cartostack`` package, in fresh processes.

Builds format 1.0 QPF scenes from the latest Session 02 prototype build (its static
layers, the WPC bin table, and the embedded font), one per polygon supersampling
factor, and saves them as ``.cstack``. Fresh child processes then load a scene and,
per product, read the WPC shapefile (the prototype's NumPy reader, timed as input),
call ``Scene.replace_polygons``, set the subtitle (``scene.text``; Matplotlib 3.11
layout, ``snap: null``, ``offset: [0, 0]``), composite, and encode
an RGBA PNG at level 6, as the Cartopy baseline writes it.

* cold: Day 1-3 alone, one product per process (``--cold-runs``, default 5);
* loop: all 18 products in one process (``--loop-runs`` for the default factor, one run
  for the others), PNGs kept and compared with each product's own Cartopy render
  (Session 01b) using the Session 02 comparison's regions.

Children assert that matplotlib, cartopy, pyproj, shapely, geopandas, pyogrio and
pandas are never imported. Runs in the project environment:

    uv run python benchmarks/package_qpf.py [--supersample 4 --compare 1,2]
    <venv>/bin/python benchmarks/package_qpf.py --scene ...   # an installed wheel (Session 15)

Writes ``benchmarks/results/package-qpf-<UTC stamp>/``: ``bench.json`` (schema
``cartostack-bench/1``, read by ``benchmarks/report.py``), ``results.json`` (every run),
the scenes, and the loop PNGs.
"""

from __future__ import annotations

import time

T0_PERF = time.perf_counter()
T0_WALL = time.time()

import argparse
import json
import resource
import statistics
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
EXAMPLES = ROOT / "docs" / "examples"
FORBIDDEN = ("matplotlib", "cartopy", "pyproj", "shapely", "geopandas", "pyogrio", "pandas")
SCHEMA = "cartostack-bench/1"


# --- Child: one process rendering products -------------------------------------------


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
    import prototype_runtime as proto  # NumPy shapefile reader (input, harness)

    t = time.perf_counter()
    scene = Scene.load(args.file)
    load_s = time.perf_counter() - t
    outdir = Path(args.outdir) if args.outdir else None
    products = []
    for day in args.items:
        ph = {}
        t = time.perf_counter()
        q = proto.read_qpf(proto.DATA / "qpf" / f"day_{day}")
        ph["read_input"] = time.perf_counter() - t
        t = time.perf_counter()
        scene.replace_polygons("qpf", q["rings"], q["values"])
        ph["data"] = time.perf_counter() - t
        t = time.perf_counter()
        scene.text["subtitle"] = q["subtitle"]
        ph["text"] = time.perf_counter() - t
        t = time.perf_counter()
        image = scene._render_image(flatten=True)  # what save_png encodes, without the copy
        ph["composite"] = time.perf_counter() - t
        t = time.perf_counter()
        png = encode_png(image, mode="RGBA", compress_level=6)
        ph["encode"] = time.perf_counter() - t
        t = time.perf_counter()
        if outdir:
            (outdir / f"qpf_plot_day_{day}.png").write_bytes(png)
        ph["other"] = time.perf_counter() - t
        products.append({"name": day, "phases_s": ph, "png_bytes": len(png)})
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


def run_child(argv: list[str], script: str = __file__) -> dict:
    t_spawn = time.time()
    t = time.perf_counter()
    proc = subprocess.run([sys.executable, script, "--child", *argv], capture_output=True,
                          text=True, check=False)  # fmt: skip
    wall = time.perf_counter() - t
    if proc.returncode != 0:
        sys.exit(f"child {argv} failed:\n{proc.stderr}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    if result["forbidden_modules_loaded"]:
        sys.exit(f"child loaded forbidden modules: {result['forbidden_modules_loaded']}")
    result["interpreter_start_s"] = result["script_start_wall"] - t_spawn
    result["process_wall_s"] = wall
    return result


# --- Orchestrator --------------------------------------------------------------------


def build_scenes(build: Path, out: Path, factors: list[int]) -> dict[int, Path]:
    import numpy as np
    from compositor_timing import geometry, png

    from cartostack import PolygonSlot, RasterLayer, Scene, TextSlot
    from cartostack.manifest import Bin

    zf = zipfile.ZipFile(build / "qpf-zlib.cstack")
    m = json.loads(zf.read("manifest.json"))
    s = m["slots"]["qpf"]
    bins = tuple(Bin(lo, hi, tuple(c), z)
                 for (lo, hi), c, z in zip(s["ranges"], s["colors"], s["zorder"], strict=False))  # fmt: skip
    fallback = Bin(0.0, 0.0, tuple(s["colors"][-1]), s["zorder"][-1])
    spec = m["text_slots"]["subtitle"]
    layers = build / "layers"
    sub = png(layers / "qpf_subtitle.png")
    ys, xs = np.nonzero(sub[..., 3])
    scenes = {}
    for k in factors:
        scene = Scene(
            geometry("qpf"),
            [
                RasterLayer(id="below", order=0, pixels=png(layers / "qpf_below.png")),
                PolygonSlot(id="qpf", order=10, bins=bins, fallback=fallback,
                            round_decimals=s["rounding"], supersample=k),
                RasterLayer(id="above", order=20, pixels=png(layers / "qpf_above.png")),
                TextSlot(id="subtitle", order=30, value=spec["text"], font=spec["font"],
                         size_px=spec["size_px"], color=tuple(spec["color"]), x=spec["x"],
                         y=spec["y"], ha=spec["ha"], va=spec["va"], snap=None,
                         offset=(0.0, 0.0), left=int(xs.min()), top=int(ys.min()),
                         pixels=sub[ys.min():ys.max() + 1, xs.min():xs.max() + 1]),
            ],
            assets={spec["font"]: zf.read(spec["font"])},
        )  # fmt: skip
        scenes[k] = out / f"qpf-k{k}.cstack"
        scene.save(scenes[k])
    return scenes


def accuracy(build: Path, outdir: Path, reference: Path, days: list[str]) -> dict:
    import numpy as np
    from PIL import Image
    from prototype_compare import diff_stats, region_masks

    masks = region_masks(build / "layers", "qpf")
    per = {}
    for day in days:
        name = f"qpf_plot_day_{day}.png"
        out = np.asarray(Image.open(outdir / name).convert("RGB")).astype(np.int16)
        ref = np.asarray(Image.open(reference / name).convert("RGB")).astype(np.int16)
        per[day] = diff_stats(out, ref, masks)
    agg = {}
    for region in ("all", "map_clear", "text"):
        agg[region] = {m: statistics.median(p[region][m] for p in per.values())
                       for m in ("frac_gt8", "frac_gt32", "max")}  # fmt: skip
        agg[region]["worst_frac_gt32"] = max(p[region]["frac_gt32"] for p in per.values())
    return {"aggregate": agg, "per_product": per}


def package_origin() -> dict:
    """Which cartostack ran: the source tree (``uv run``) or an installed wheel (Session 15)."""
    import cartostack
    path = Path(cartostack.__file__).resolve()
    where = "source tree" if (ROOT / "src") in path.parents else "installed package"
    return {"version": cartostack.__version__, "file": str(path), "where": where,
            "executable": sys.executable}


def to_record(mode: str, config: str, runs: list[dict], source: Path, acc: dict | None,
              workload: str = "qpf", note: str | None = None) -> dict:
    out = []
    for r in runs:
        run = {"phases_s": {"startup": r["interpreter_start_s"] + r["pre_main_s"]
                            + sum(r["imports_s"].values()),
                            "load": r["load_s"]},
               "products": [{"name": p["name"], "n_polygons": None, "phases_s": p["phases_s"]}
                            for p in r["products"]]}  # fmt: skip
        accounted = sum(run["phases_s"].values()) + sum(
            sum(p["phases_s"].values()) for p in run["products"])
        run["phases_s"]["other"] = r["interpreter_start_s"] + r["end_to_end_in_process_s"] - accounted
        out.append(run)
    rec = {"series": "package", "workload": workload, "mode": mode, "config": config,
           "source": str(source.relative_to(ROOT)), "runs": out,
           "notes": [note or "cartostack package: Scene.load, replace_polygons (exact NumPy fill), "
                     "subtitle via scene.text, compositor, PNG RGBA 6.",
                     "cartostack {version} from the {where} ({executable})".format(**package_origin())]}  # fmt: skip
    if acc is not None:
        a = acc["aggregate"]["all"]
        rec["accuracy"] = {"region": "all", "products": len(acc["per_product"]),
                           "frac_gt8": a["frac_gt8"], "frac_gt32": a["frac_gt32"], "max": a["max"]}
    return rec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--file", help=argparse.SUPPRESS)
    parser.add_argument("--items", nargs="*", help=argparse.SUPPRESS)
    parser.add_argument("--outdir", help=argparse.SUPPRESS)
    parser.add_argument("--build", type=Path, help="prototype build directory (default: latest)")
    parser.add_argument("--scene", type=Path,
                        help="an authored .cstack (author_scenes.py) instead of scenes from the prototype layers")
    parser.add_argument("--reference", type=Path,
                        help="Session 01b products directory (default: latest qpf-*/products)")
    parser.add_argument("--supersample", type=int, default=4, help="default polygon supersampling")
    parser.add_argument("--compare", default="1,2", help="other factors, one loop run each")
    parser.add_argument("--cold-runs", type=int, default=5)
    parser.add_argument("--loop-runs", type=int, default=2)
    args = parser.parse_args()
    if args.child:
        print(json.dumps(child(args)))
        return

    sys.path.insert(0, str(Path(__file__).parent))
    from prototype_runtime import QPF_DAYS

    build = args.build or max(p for p in RESULTS.glob("prototype-*") if (p / "layers").is_dir())
    reference = args.reference or max(RESULTS.glob("qpf-*/products"))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"package-qpf-{stamp}"
    out.mkdir(parents=True)
    if args.scene:
        from cartostack import Scene

        args.supersample = Scene.load(args.scene)["qpf"].supersample
        factors = [args.supersample]
        scenes = {args.supersample: args.scene.resolve()}
    else:
        factors = [args.supersample] + [int(k) for k in args.compare.split(",") if k and int(k) != args.supersample]
        scenes = build_scenes(build, out, factors)

    def argv(k: int, items: list[str], outdir: Path | None = None) -> list[str]:
        a = ["--file", str(scenes[k]), "--items", *items]
        return a + (["--outdir", str(outdir)] if outdir else [])

    run_child(argv(args.supersample, ["1-3"]))  # priming (bytecode, OS cache); not recorded
    results: dict = {"build": str(build.relative_to(ROOT)), "reference": str(reference.relative_to(ROOT)),
                     "created_utc": stamp, "python": sys.version.split()[0], "cartostack": package_origin(), "cold": [], "loops": {}}  # fmt: skip
    for i in range(args.cold_runs):
        r = run_child(argv(args.supersample, ["1-3"]))
        results["cold"].append(r)
        print(f"cold k={args.supersample} run {i}: {r['interpreter_start_s'] + r['end_to_end_in_process_s']:.3f} s "
              f"(polygons {r['products'][0]['phases_s']['data'] * 1000:.0f} ms)")
    records = [to_record("cold-process", f"zlib-k{args.supersample}", results["cold"], out / "results.json", None)]
    for k in factors:
        runs = []
        for i in range(args.loop_runs if k == args.supersample else 1):
            d = out / f"products-k{k}"
            d.mkdir(exist_ok=True)
            runs.append(run_child(argv(k, QPF_DAYS, d if i == 0 else None)))
            total = runs[-1]["interpreter_start_s"] + runs[-1]["end_to_end_in_process_s"]
            data = statistics.median(p["phases_s"]["data"] for p in runs[-1]["products"])
            print(f"loop k={k} run {i}: {total:.2f} s, polygons median {data * 1000:.0f} ms")
        acc = accuracy(build, out / f"products-k{k}", reference, QPF_DAYS)
        a = acc["aggregate"]
        print(f"  accuracy k={k}: all >8 {a['all']['frac_gt8'] * 100:.3f} %, >32 {a['all']['frac_gt32'] * 100:.3f} %;"
              f" map_clear >32 {a['map_clear']['frac_gt32'] * 100:.3f} % (worst {a['map_clear']['worst_frac_gt32'] * 100:.3f} %)")
        print(f"  text region k={k}: >8 {a['text']['frac_gt8'] * 100:.2f} %, >32 {a['text']['frac_gt32'] * 100:.3f} %, max {a['text']['max']:.0f}")
        results["loops"][str(k)] = {"runs": runs, "accuracy": acc}
        records.append(to_record("loop", f"zlib-k{k}", runs, out / "results.json", acc))
    (out / "results.json").write_text(json.dumps(results, indent=1))
    (out / "bench.json").write_text(json.dumps({"schema": SCHEMA, "records": records}, indent=1))
    print(f"results → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
