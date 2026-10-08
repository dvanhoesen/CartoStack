# /// script
# requires-python = "==3.13.*"
# dependencies = [
#     "cartopy==0.26.0",
#     "geopandas==1.2.0",
#     "matplotlib==3.11.2",
#     "numpy==2.5.3",
#     "pillow==12.3.0",
#     "pyogrio==0.13.0",
#     "pytz==2026.5",
# ]
# [tool.uv]
# exclude-newer = "2026-10-08T00:00:00Z"
# ///
"""Session 02: compare prototype runtime outputs with full Cartopy renders.

Every runtime output is compared with the full render of the *same* inputs:
QPF products with the Session 01b baseline products, grid variants with
``baseline_cartopy.render(variant)``. Differences are reported per region,
using the build's layers as masks:

* ``static_opaque``: the static-above layer is opaque (title bar, legend, logo, labels...);
* ``under_mask``: the static-above layer is partly transparent (QPF's 75 % white mask, AA edges);
* ``map_clear``: nothing static above — data shows directly;
* ``text``: the subtitle's box (dilated by 4 px).

A layering-only floor is measured by stacking the build's Agg-rendered data
and subtitle layers for the authoring inputs (QPF Day 1-3, grid variant 0).

    uv run benchmarks/prototype_compare.py      # latest prototype dir and its latest runtime run
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import baseline_cartopy as bc
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
THRESHOLDS = (0, 8, 32, 64)


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB")).astype(np.int16)


def region_masks(layers_dir: Path, scene: str) -> dict[str, np.ndarray]:
    above = np.asarray(Image.open(layers_dir / f"{scene}_above.png"))[..., 3]
    sub = np.asarray(Image.open(layers_dir / f"{scene}_subtitle.png"))[..., 3]
    ys, xs = np.nonzero(sub)
    text = np.zeros_like(above, bool)
    text[max(ys.min() - 4, 0):ys.max() + 5, max(xs.min() - 4, 0):xs.max() + 5] = True
    return {
        "static_opaque": (above == 255) & ~text,
        "under_mask": (above > 0) & (above < 255) & ~text,
        "map_clear": (above == 0) & ~text,
        "text": text,
    }


def diff_stats(a: np.ndarray, b: np.ndarray, masks: dict) -> dict:
    d = np.abs(a - b).max(-1)
    out = {"all": stats(d, np.ones_like(d, bool))}
    for name, m in masks.items():
        out[name] = stats(d, m)
    return out


def stats(d: np.ndarray, m: np.ndarray) -> dict:
    v = d[m]
    res = {"pixels": int(m.sum()), "max": int(v.max()) if v.size else 0,
           "mean": float(v.mean()) if v.size else 0.0}
    for t in THRESHOLDS:
        res[f"frac_gt{t}"] = float((v > t).mean()) if v.size else 0.0
    return res


def diff_image(a, b, path: Path, gain: int = 8) -> None:
    d = (np.abs(a - b).max(-1) * gain).clip(0, 255).astype(np.uint8)
    Image.fromarray(255 - d).save(path, compress_level=1)


def sheet(ref: np.ndarray, out: np.ndarray, box, path: Path, gain: int = 8) -> None:
    """Side-by-side crop: reference | runtime | amplified difference."""
    x0, y0, x1, y1 = box
    r, o = ref[y0:y1, x0:x1], out[y0:y1, x0:x1]
    d = 255 - (np.abs(r - o).max(-1, keepdims=True) * gain).clip(0, 255)
    row = np.concatenate([r, o, np.repeat(d, 3, -1)], 1).astype(np.uint8)
    Image.fromarray(row).save(path, compress_level=1)


def layering_floor(proto: Path, scene: str, ref: np.ndarray, masks: dict) -> dict:
    layers = proto / "layers"
    img = Image.open(layers / f"{scene}_below.png").convert("RGBA")
    for name in ("data", "above", "subtitle"):
        img.alpha_composite(Image.open(layers / f"{scene}_{name}.png").convert("RGBA"))
    return diff_stats(np.asarray(img.convert("RGB")).astype(np.int16), ref, masks)


def aggregate(per_product: dict) -> dict:
    """Median and worst over products, per region and metric."""
    regions = next(iter(per_product.values())).keys()
    out = {}
    for reg in regions:
        metrics = next(iter(per_product.values()))[reg].keys()
        out[reg] = {}
        for met in metrics:
            vals = [p[reg][met] for p in per_product.values()]
            out[reg][met] = {"median": float(np.median(vals)), "worst": float(max(vals))}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", help="prototype build directory (default: latest)")
    parser.add_argument("--runtime", help="runtime run directory (default: latest in --dir)")
    args = parser.parse_args()
    proto = Path(args.dir) if args.dir else max(RESULTS.glob("prototype-*"))
    runtime = Path(args.runtime) if args.runtime else max(proto.glob("runtime-*"))
    qpf_ref_dir = max(RESULTS.glob("qpf-*")) / "products"
    outdir = runtime / "compare"
    outdir.mkdir(exist_ok=True)
    report = {"created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
              "prototype_dir": str(proto.relative_to(ROOT)),
              "runtime_dir": str(runtime.relative_to(ROOT)),
              "qpf_reference_dir": str(qpf_ref_dir.relative_to(ROOT)), "configs": {}}

    masks = {"qpf": region_masks(proto / "layers", "qpf"),
             "grid": region_masks(proto / "layers", "grid")}
    report["region_pixels"] = {s: {k: int(v.sum()) for k, v in m.items()} for s, m in masks.items()}

    # Grid references for the variants the runtime rendered (full renders, this env).
    grid_refs = {}
    variants = sorted({p.stem for d in runtime.glob("grid-*") for p in d.glob("variant_*.png")})
    if variants:
        bc.import_stack()
        sources = bc.load_sources()
        for name in variants:
            ref_path = outdir / f"ref_grid_{name}.png"
            if not ref_path.exists():
                bc.render(sources, int(name.split("_")[1]), ref_path)
            grid_refs[name] = load_rgb(ref_path)

    for cfg in sorted(d for d in runtime.iterdir() if d.is_dir() and d.name != "compare"):
        scene = "qpf" if cfg.name.startswith("qpf") else "grid"
        per = {}
        for png in sorted(cfg.glob("*.png")):
            ref = load_rgb(qpf_ref_dir / png.name) if scene == "qpf" else grid_refs[png.stem]
            out = load_rgb(png)
            if out.shape != ref.shape:
                sys.exit(f"size mismatch {png}: {out.shape} vs {ref.shape}")
            per[png.stem] = diff_stats(out, ref, masks[scene])
            if png.stem in ("qpf_plot_day_1-3", "qpf_plot_day_1-7", "variant_1"):
                diff_image(out, ref, outdir / f"diff_{cfg.name}_{png.stem}.png")
                box = (1380, 1280, 2010, 1780) if scene == "qpf" else (300, 30, 900, 330)
                sheet(ref, out, box, outdir / f"sheet_{cfg.name}_{png.stem}.png")
        report["configs"][cfg.name] = {"scene": scene, "products": len(per),
                                       "aggregate": aggregate(per), "per_product": per}
        a = report["configs"][cfg.name]["aggregate"]
        print(f"{cfg.name:24} n={len(per):2}  all>0 {a['all']['frac_gt0']['median']:.4f}  "
              f"all>8 {a['all']['frac_gt8']['median']:.5f}  "
              f"clear>8 {a['map_clear']['frac_gt8']['median']:.5f}  "
              f"text>32 {a['text']['frac_gt32']['median']:.5f}  "
              f"static>0 {a['static_opaque']['frac_gt0']['median']:.5f}  "
              f"max {a['all']['max']['worst']:.0f}")

    report["layering_floor"] = {
        "qpf_day_1-3": layering_floor(proto, "qpf", load_rgb(qpf_ref_dir / "qpf_plot_day_1-3.png"),
                                      masks["qpf"]),
        "grid_variant_0": layering_floor(proto, "grid",
                                         grid_refs.get("variant_0", load_rgb(max(RESULTS.glob("2026*"))
                                                                             / "baseline.png")),
                                         masks["grid"]),
    }
    for k, v in report["layering_floor"].items():
        print(f"layering floor {k:14} all>0 {v['all']['frac_gt0']:.4f} "
              f"all>8 {v['all']['frac_gt8']:.5f} max {v['all']['max']}")
    (outdir / "compare.json").write_text(json.dumps(report, indent=1) + "\n")
    print(f"wrote {outdir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
