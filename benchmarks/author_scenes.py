# /// script
# requires-python = "==3.13.*"
# dependencies = [
#     "cartostack[build]",
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
# [tool.uv.sources]
# cartostack = { path = "..", editable = true }
# ///
"""Session 10: author the QPF and grid scenes with ``cartostack.build.SceneBuilder``.

The figures come from the baseline scripts unchanged (``baseline_qpf.build_figure`` for
Day 1-3, the port of ``resources/slow_example.py``; ``baseline_cartopy.build_scene``
for variant 0); only the layer assignment is new:

* QPF: ``below`` (zorder < 10, with the white background), the ``qpf`` polygon slot (the
  data polygons; WPC bin table, fallback gray, values rounded to 2 decimals, 4×
  supersampling), ``above`` (everything else: borders, mask, cities, logo, title bar,
  legend, attribution), and the ``subtitle`` text slot; output 300 dpi with the tight
  crop resolved once.
* Grid: ``below`` (zorder < 2), the ``temperature`` grid slot (500 × 500 cell centres,
  coolwarm, -10..35, alpha 0.8), ``above`` (counties, borders, cities, title, colorbar
  axes), and the ``subtitle`` text slot.

Fixtures come from ``benchmarks/data`` only (network blocked). Writes
``benchmarks/results/authored-<UTC stamp>/`` with ``qpf.cstack``, ``grid.cstack``, and
``build.json`` (timings, layers, warnings, and each file's difference from the full
render of the same figure).

    uv run benchmarks/author_scenes.py
"""

from __future__ import annotations

import io
import json
import math
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
sys.path.insert(0, str(Path(__file__).parent))

import baseline_cartopy as bc
import baseline_qpf as bq


def full_render(fig, dpi: float, crop):
    import numpy as np
    from matplotlib.transforms import Bbox
    from PIL import Image

    buf = io.BytesIO()
    kwargs = {"bbox_inches": Bbox.from_bounds(*crop), "pad_inches": 0} if crop else {}
    fig.savefig(buf, format="png", dpi=dpi, **kwargs)
    return np.asarray(Image.open(buf).convert("RGBA")).astype(int)


def summary(scene, path: Path, ref, t_build: float, caught) -> dict:
    import numpy as np

    out = scene.render().astype(int)
    d = np.abs(out - ref).max(-1)
    return {
        "file": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "build_s": t_build,
        "canvas": [scene.geometry.width, scene.geometry.height],
        "axes_px": scene.geometry.axes.to_dict(),
        "layers": [{"id": la.id, "kind": la.kind, "order": la.order, "left": la.left, "top": la.top,
                    "size": None if la.pixels is None else list(la.pixels.shape[:2])}
                   for la in scene.draw_order()],
        "warnings": [str(w.message) for w in caught],
        "vs_full_render": {"max": int(d.max()), "frac_gt8": float((d > 8).mean()),
                           "frac_gt32": float((d > 32).mean())},
    }


def author_qpf(out: Path) -> dict:
    from cartostack.build import SceneBuilder

    bq.import_stack()
    static, _ = bq.load_static()
    prod = bq.read_product("1-3")
    sc = bq.build_figure(static, prod)
    fig, ax = sc["fig"], sc["ax"]
    t = time.perf_counter()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        b = SceneBuilder(fig, ax, dpi=bq.SAVE_DPI, crop="tight")
        b.add_static("below", order=0, background=True, zorder=(-math.inf, 10))
        b.add_polygon_slot(
            "qpf", order=10, bins=[(lo, up, color, z) for lo, up, color, z in bq.QPF_RANGES],
            fallback=("gray", 10), round_decimals=2, artists=sc["data_artists"],
        )  # fmt: skip
        b.add_static("above", order=20, zorder=(10, math.inf))
        b.add_text_slot("subtitle", sc["subtitle"], order=30)
        scene = b.build()
    t_build = time.perf_counter() - t
    path = out / "qpf.cstack"
    scene.save(path)
    crop = scene.geometry.figure.crop_in if scene.geometry.figure else None
    ref = full_render(fig, bq.SAVE_DPI, crop)
    # The data polygons are a slot without initial pixels, so compare without them.
    for a in sc["data_artists"]:
        a.set_visible(False)
    ref_no_data = full_render(fig, bq.SAVE_DPI, crop)
    del ref
    return summary(scene, path, ref_no_data, t_build, caught)


def author_grid(out: Path) -> dict:
    from cartostack.build import SceneBuilder

    bc.import_stack()
    sources = bc.load_sources()
    sc = bc.build_scene(sources, 0)
    fig, ax, mesh = sc["fig"], sc["ax"], sc["mesh"]
    lon, lat, values = bc.make_field(0)
    t = time.perf_counter()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        b = SceneBuilder(fig, ax, dpi=bc.DPI)
        b.add_static("below", order=0, background=True, zorder=(-math.inf, bc.DATA_ZORDER))
        b.add_grid_slot(
            "temperature", order=10, lon=lon, lat=lat, cmap=mesh.cmap, norm=mesh.norm,
            alpha=mesh.get_alpha(), extend="both", values=values, artists=[mesh],
        )  # fmt: skip
        b.add_static("above", order=20, zorder=(bc.DATA_ZORDER, math.inf), artists=[sc["title"]])
        b.add_colorbar("colorbar", sc["cax"]._colorbar, slot="temperature", order=25)
        b.add_text_slot("subtitle", sc["subtitle"], order=30)
        scene = b.build()
    t_build = time.perf_counter() - t
    path = out / "grid.cstack"
    scene.save(path)
    ref = full_render(fig, bc.DPI, None)
    out = summary(scene, path, ref, t_build, caught)
    # Colorbar redraw cost: replace values with and without a new normalisation.
    import numpy as np

    times = {"same_norm": [], "new_norm": []}
    for i in range(10):
        t = time.perf_counter()
        scene.replace_grid("temperature", values)
        times["same_norm"].append(time.perf_counter() - t)
        t = time.perf_counter()
        scene.replace_grid("temperature", values, vmin=-15.0 - i, vmax=40.0)
        times["new_norm"].append(time.perf_counter() - t)
    out["replace_grid_ms"] = {k: float(np.median(v) * 1000) for k, v in times.items()}
    out["stale_after_norm_change"] = list(scene.stale_colorbars)
    return out


def main() -> None:
    bc.block_network()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"authored-{stamp}"
    out.mkdir(parents=True)
    report = {"created_utc": stamp, "qpf": author_qpf(out), "grid": author_grid(out)}
    (out / "build.json").write_text(json.dumps(report, indent=1))
    for name in ("qpf", "grid"):
        r = report[name]
        v = r["vs_full_render"]
        print(f"{name}: {r['canvas'][0]}x{r['canvas'][1]}, {len(r['layers'])} layers, {r['bytes'] / 1e6:.2f} MB, "
              f"built in {r['build_s']:.1f} s; vs full render >8 {v['frac_gt8'] * 100:.3f} %, "
              f">32 {v['frac_gt32'] * 100:.3f} %, max {v['max']}; warnings {r['warnings']}")
    print(f"grid replace_grid: {report['grid']['replace_grid_ms']} ms, stale {report['grid']['stale_after_norm_change']}")
    print(f"results → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
