# /// script
# requires-python = "==3.13.*"
# dependencies = [
#     "matplotlib==3.11.2",
#     "numpy==2.5.3",
# ]
# [tool.uv]
# exclude-newer = "2026-10-08T00:00:00Z"
# ///
"""Session 07b: figures comparing CartoStack with the standard Matplotlib/Cartopy render.

Reads recorded benchmark results, converts them to one common schema
(``cartostack-bench/1``), and writes figures, ``report.json`` and ``index.md``
to ``benchmarks/results/report-<UTC stamp>/``.

Inputs (each defaults to the latest matching directory under ``benchmarks/results``):

* ``--grid-baseline``     Session 01 ``baseline_cartopy.py`` result (latest with >= 5 cold runs;
                          shorter runs such as ``--cold-runs 3`` are treated as smoke runs);
* ``--qpf-baseline``      Session 01b ``baseline_qpf.py`` result (``qpf-*``, >= 5 cold runs);
* ``--prototype-runtime`` Session 02 ``prototype_runtime.py`` run (``prototype-*/runtime-*``),
                          with its ``compare/compare.json`` from ``prototype_compare.py``;
* ``--compositor``        Session 06 ``compositor_timing.py`` result (``compositor-*``);
* ``--bench``             native ``bench.json`` files already in the common schema (repeatable).
                          Every ``benchmarks/results/**/bench.json`` is also read.

Common schema. A ``bench.json`` holds ``{"schema": "cartostack-bench/1", "records": [...]}``;
each record is::

    {"series": "cartopy-baseline" | "prototype" | "package",
     "workload": "qpf" | "grid",
     "mode": "cold-process" | "loop" | "warm" | "components",
     "config": "zlib",                       # encoding/variant label within the series
     "source": "benchmarks/results/...",     # file the numbers came from
     "runs": [{"phases_s": {phase: s},       # once per process: startup, load, other
               "products": [{"name": "1-3", "n_polygons": 312, "phases_s": {phase: s}}]}],
     "accuracy": {"frac_gt8": f, "frac_gt32": f, "max": m, "region": "all", "products": n},  # optional
     "notes": ["..."]}

Phases are ``PHASES`` below. A run's total is the sum of its phases plus its products'
phases. Totals exclude measurement-only work (extra PNG encodes, output hashing) and
process exit. ``cold-process`` runs render one product in a fresh process; ``loop`` runs
render several products in one process (products after the first are the warm
per-product cost); ``warm`` runs are in-process repetitions after a warm-up;
``components`` records hold medians of individual operations.

    uv run benchmarks/report.py
    uv run benchmarks/report.py --qpf-baseline benchmarks/results/qpf-20261008T152044Z
"""

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
SCHEMA = "cartostack-bench/1"
MIN_COLD_RUNS = 5

PHASES = ("startup", "load", "read_input", "data", "static", "text", "composite", "encode", "other")
PHASE_LABELS = {
    "startup": "Start-up (interpreter, imports)",
    "load": "Load (static sources / .cstack)",
    "read_input": "Read product input",
    "data": "Render data",
    "static": "Draw static content",
    "text": "Text and decorations",
    "composite": "Composite layers",
    "encode": "PNG encode (Cartopy: savefig layout + encode)",
    "other": "Other",
}
# Reference palette (dataviz skill, light mode), categorical slots in validated order;
# "other" is a neutral gray, not a categorical slot.
SLOTS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
PHASE_COLORS = dict(zip(PHASES, (*SLOTS, "#b5b3ad"), strict=True))
SERIES = ("cartopy-baseline", "prototype", "package")
SERIES_LABELS = {"cartopy-baseline": "Matplotlib/Cartopy", "prototype": "CartoStack prototype",
                 "package": "CartoStack package"}
SERIES_COLORS = dict(zip(SERIES, SLOTS[:3], strict=True))
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
NEUTRAL_BAR = "#8a8984"

# Default prototype configurations (Session 02 decisions: zlib layers, 2x polygons, Pillow stacking).
DEFAULT_CONFIG = {("qpf", "cold-process"): "zlib", ("qpf", "loop"): "zlib",
                  ("grid", "cold-process"): "zlib", ("grid", "loop"): "png"}


# --------------------------------------------------------------------------- input selection

def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def latest(candidates: list[Path], what: str) -> Path:
    if not candidates:
        sys.exit(f"no {what} found under {rel(RESULTS)}; pass it explicitly")
    return sorted(candidates, key=lambda p: p.name)[-1]


def cold_runs(path: Path, key: str) -> int:
    data = json.loads((path / "results.json").read_text())
    return len(data[key]["runs_detail" if key == "single_product" else "runs"])


def select_inputs(args) -> dict[str, Path]:
    grid = args.grid_baseline or latest(
        [p for p in RESULTS.glob("2*Z") if (p / "results.json").exists()
         and cold_runs(p, "cold") >= MIN_COLD_RUNS], "grid baseline")
    qpf = args.qpf_baseline or latest(
        [p for p in RESULTS.glob("qpf-*") if (p / "results.json").exists()
         and cold_runs(p, "single_product") >= MIN_COLD_RUNS], "QPF baseline")
    proto = args.prototype_runtime or latest(
        [p for p in RESULTS.glob("prototype-*/runtime-*") if (p / "runtime.json").exists()],
        "prototype runtime run")
    comp = args.compositor or latest(
        [p for p in RESULTS.glob("compositor-*") if (p / "results.json").exists()],
        "compositor timing")
    return {"grid_baseline": Path(grid), "qpf_baseline": Path(qpf),
            "prototype_runtime": Path(proto), "compositor": Path(comp)}


# --------------------------------------------------------------------------- adapters

def record(series, workload, mode, config, source, runs, notes=(), accuracy=None) -> dict:
    rec = {"series": series, "workload": workload, "mode": mode, "config": config,
           "source": rel(source), "runs": runs, "notes": list(notes)}
    if accuracy is not None:
        rec["accuracy"] = accuracy
    return rec


def accounted(run: dict) -> float:
    return sum(run["phases_s"].values()) + sum(product_total(p) for p in run["products"])


def product_total(product: dict) -> float:
    return sum(product["phases_s"].values())


def qpf_baseline_product(p: dict) -> dict:
    ph = p["phases_s"]
    draws = sum(v for k, v in ph.items() if k.startswith("draw_"))
    phases = {
        "read_input": ph["read"] + ph["clip"],
        "data": ph["draw_data"] + ph["build_data"],
        "static": ph["draw_below"] + ph["draw_above"] + ph["build_static"],
        "text": ph["draw_decorations"] + ph["draw_subtitle"] + ph["build_decorations"],
        "encode": ph["savefig_other"],
        # savefig_total is draw_* + savefig_other; keep any remainder rather than dropping it.
        "other": ph["setup"] + ph.get("write_file", 0.0)
        + (ph["savefig_total"] - draws - ph["savefig_other"]),
    }
    return {"name": p["day"], "n_polygons": p["n_polygons"], "phases_s": phases}


def adapt_qpf_baseline(path: Path) -> list[dict]:
    data = json.loads((path / "results.json").read_text())
    src = path / "results.json"
    single = []
    for r in data["single_product"]["runs_detail"]:
        run = {"phases_s": {"startup": r["interpreter_start_s"] + r["pre_main_s"]
                            + sum(r["imports_s"].values()),
                            "load": r["load_static_s"]},
               "products": [qpf_baseline_product(r)]}
        # end_to_end_in_process_s already excludes the post-run PNG analysis.
        run["phases_s"]["other"] = r["interpreter_start_s"] + r["end_to_end_in_process_s"] - accounted(run)
        single.append(run)
    loop = []
    for r in data["loop"]["runs_detail"]:
        loop.append({"phases_s": {"startup": r["interpreter_start_s"] + sum(r["imports_s"].values()),
                                  "load": r["load_static_s"]},
                     "products": [qpf_baseline_product(p) for p in r["products"]]})
    note = ("Cartopy's encode phase is savefig's remainder after drawing: tight bounding box, "
            "layout and PNG encoding together.")
    return [
        record("cartopy-baseline", "qpf", "cold-process", "default", src, single, [note]),
        record("cartopy-baseline", "qpf", "loop", "default", src, loop, [
            note, "Loop totals are the sum of start-up, load and per-product phases; the recorded "
            "in-process end-to-end also contains the per-product PNG analysis (decode and hashing) "
            "and is not used."]),
    ]


def grid_baseline_product(ph: dict, name: str) -> dict:
    return {"name": name, "n_polygons": None, "phases_s": {
        "read_input": ph["field"],
        "data": ph["draw_data"] + ph["build_data"],
        "static": ph["draw_below"] + ph["draw_above"] + ph["build_below"] + ph["build_above"],
        "text": ph["draw_text_colorbar"] + ph["build_text_colorbar"],
        "encode": ph["encode_rgb_cl6"],
        "other": ph["setup"] + ph["draw_overhead"] + ph["buffer_copy"]
        + ph.get("write_file", 0.0) + ph["close"],
    }}


def adapt_grid_baseline(path: Path) -> list[dict]:
    data = json.loads((path / "results.json").read_text())
    src = path / "results.json"
    variant = f"variant_{data['variant']}"
    extra = ("encode_rgba_cl6", "encode_rgb_cl1", "encode_rgba_cl1")  # measurement-only encodes
    cold = []
    for r in data["cold"]["runs"]:
        run = {"phases_s": {"startup": r["interpreter_start_s"] + r["pre_main_s"]
                            + sum(r["imports_s"].values()),
                            "load": r["load_sources_s"]},
               "products": [grid_baseline_product(r["phases_s"], variant)]}
        in_process = r["end_to_end_in_process_s"] - sum(r["phases_s"][k] for k in extra)
        run["phases_s"]["other"] = r["interpreter_start_s"] + in_process - accounted(run)
        cold.append(run)
    warm = [{"phases_s": {}, "products": [grid_baseline_product(r["phases_s"], variant)
                                          for r in data["warm"]["runs"] if not r.get("warmup")]}]
    note = ("Natural Earth shapefiles are read lazily while drawing, so their read time "
            "(~1.9 s cold) is part of 'Draw static content'.")
    return [
        record("cartopy-baseline", "grid", "cold-process", "default", src, cold, [
            note, "The reference PNG is RGB level 6; the three other encodes are measurement-only "
            "and excluded."]),
        record("cartopy-baseline", "grid", "warm", "default", src, warm, [
            note, f"{data['warm']['reps']} in-process repetitions after one warm-up; Cartopy keeps "
            "geometry caches between them."]),
    ]


def adapt_prototype(path: Path) -> list[dict]:
    data = json.loads((path / "runtime.json").read_text())
    src = path / "runtime.json"
    compare_path = path / "compare" / "compare.json"
    compare = json.loads(compare_path.read_text())["configs"] if compare_path.exists() else {}
    records = []
    for key, runs in data["runs"].items():
        workload, kind, *rest = key.split("-")
        config = "-".join(rest)
        mode = "cold-process" if kind == "single" else "loop"
        out = []
        for r in runs:
            products = []
            for p in r["products"]:
                ph = p["phases_s"]
                encode = sum(v for k, v in ph.items() if k.startswith("encode_"))
                phases = {"data": ph["data"], "text": ph["text"], "composite": ph["composite"],
                          "encode": encode}
                # render_total excludes read_input; its remainder is write_file and bookkeeping.
                phases["other"] = ph["render_total"] - sum(phases.values())
                phases["read_input"] = ph["read_input"]
                name = p["name"].removeprefix("qpf_plot_day_")
                products.append({"name": name, "n_polygons": None, "phases_s": phases})
            # NumPy is imported at module level, so its time is already inside pre_main_s.
            run = {"phases_s": {"startup": r["interpreter_start_s"] + r["pre_main_s"]
                                + sum(v for k, v in r["imports_s"].items() if k != "numpy"),
                                "load": r["load_s"]},
                   "products": products}
            # end_to_end_in_process_s already excludes the measurement-only extra encodes.
            run["phases_s"]["other"] = r["interpreter_start_s"] + r["end_to_end_in_process_s"] - accounted(run)
            out.append(run)
        accuracy = None
        if key in compare:
            agg = compare[key]["aggregate"]["all"]
            accuracy = {"region": "all", "products": compare[key]["products"],
                        "frac_gt8": agg["frac_gt8"]["median"], "frac_gt32": agg["frac_gt32"]["median"],
                        "max": agg["max"]["median"]}
        records.append(record("prototype", workload, mode, config, src, out, accuracy=accuracy, notes=[
            "Session 02 prototype runtime (NumPy and Pillow only), not the cartostack package."]))
    return records


def adapt_compositor(path: Path) -> list[dict]:
    data = json.loads((path / "results.json").read_text())
    src = path / "results.json"
    records = []
    for workload, encode_key in (("qpf", "encode_RGBA_6"), ("grid", "encode_RGB_6")):
        s = data["scenes"][workload]
        run = {"phases_s": {"load": s["load"]["median_ms"] / 1000,
                            "composite": s["render_warm"]["median_ms"] / 1000,
                            "encode": s[encode_key]["median_ms"] / 1000},
               "products": []}
        records.append(record("package", workload, "components", encode_key.removeprefix("encode_"),
                              src, [run], [f"Session 06 medians over {s['load']['reps']} repetitions "
                                           "(load, warm render, encode); no slot rendering yet."]))
    return records


def read_native(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if data.get("schema") != SCHEMA:
        sys.exit(f"{rel(path)}: expected schema {SCHEMA!r}, found {data.get('schema')!r}")
    return data["records"]


def check_phases(records: list[dict]) -> list[str]:
    """Negative phases mean an adapter double-counts; report them instead of plotting them."""
    problems = []
    for rec in records:
        for i, run in enumerate(rec["runs"]):
            items = [("run", run["phases_s"])] + [(p["name"], p["phases_s"]) for p in run["products"]]
            for where, phases in items:
                for ph, v in phases.items():
                    if ph not in PHASES:
                        problems.append(f"{rec['source']} {rec['series']}/{rec['config']}: unknown phase {ph!r}")
                    elif v < -1e-3:
                        problems.append(f"{rec['source']} {rec['series']}/{rec['workload']}/{rec['mode']}/"
                                        f"{rec['config']} run {i} {where}: {ph} = {v * 1000:.1f} ms")
    return problems


def fill_polygon_counts(records: list[dict]) -> None:
    counts = {}
    for rec in records:
        if rec["workload"] == "qpf":
            for run in rec["runs"]:
                for p in run["products"]:
                    if p.get("n_polygons") is not None:
                        counts[p["name"]] = p["n_polygons"]
    for rec in records:
        if rec["workload"] == "qpf":
            for run in rec["runs"]:
                for p in run["products"]:
                    if p.get("n_polygons") is None:
                        p["n_polygons"] = counts.get(p["name"])


# --------------------------------------------------------------------------- summaries

def stat(values) -> dict:
    values = list(values)
    return {"median": statistics.median(values), "min": min(values), "max": max(values), "n": len(values)}


def find(records, series, workload, mode, config=None) -> dict | None:
    for rec in records:
        if (rec["series"], rec["workload"], rec["mode"]) == (series, workload, mode) and (
                config is None or rec["config"] == config):
            return rec
    return None


def pick(records, series, workload, mode) -> dict | None:
    """The record a figure uses: the default configuration for the prototype, else the first."""
    if series == "prototype":
        return find(records, series, workload, mode, DEFAULT_CONFIG.get((workload, mode)))
    return find(records, series, workload, mode)


def run_totals(rec) -> dict:
    return stat(accounted(r) for r in rec["runs"])


def warm_products(rec) -> list[dict]:
    """Per-product costs after the first product in each process (all products for warm reps)."""
    skip = 0 if rec["mode"] == "warm" else 1
    return [p for r in rec["runs"] for p in r["products"][skip:]]


def per_product_medians(rec) -> dict[str, float]:
    """Each warm product's median total over runs, so every product counts once."""
    by_name: dict[str, list[float]] = {}
    for p in warm_products(rec):
        by_name.setdefault(p["name"], []).append(product_total(p))
    return {name: statistics.median(v) for name, v in by_name.items()}


def warm_totals(rec) -> dict:
    """Median over products of each product's median; min/max over those product medians."""
    return stat(per_product_medians(rec).values())


def median_phases(rec, products_only=False) -> dict:
    out = {}
    for ph in PHASES:
        if products_only:
            vals = [p["phases_s"].get(ph, 0.0) for p in warm_products(rec)]
        else:
            vals = [r["phases_s"].get(ph, 0.0) + sum(p["phases_s"].get(ph, 0.0) for p in r["products"])
                    for r in rec["runs"]]
        out[ph] = statistics.median(vals)
    return out


def speedup(base: dict, new: dict) -> dict:
    return {"median": base["median"] / new["median"], "min": base["min"] / new["max"],
            "max": base["max"] / new["min"]}


def warm_speedup(base_rec, new_rec) -> dict:
    """Ratio of medians; the range is over products both series rendered (same inputs)."""
    b, n = per_product_medians(base_rec), per_product_medians(new_rec)
    out = speedup(warm_totals(base_rec), warm_totals(new_rec))
    ratios = [b[k] / n[k] for k in b.keys() & n.keys()]
    if ratios:
        out.update(min=min(ratios), max=max(ratios), range_basis="same product")
    else:
        out["range_basis"] = "slowest/fastest products (no product in common)"
    return out


SKIPPED: list[str] = []


def product_names(rec) -> set[tuple[str, ...]]:
    return {tuple(p["name"] for p in r["products"]) for r in rec["runs"]}


def comparisons(records) -> list[dict]:
    """Speed-ups of each CartoStack series against the baseline, on the common basis."""
    rows = []
    specs = (
        ("qpf", "QPF Day 1-3, one product per fresh process", "cold-process", "cold-process", run_totals),
        ("qpf", "QPF, 18 products in one process", "loop", "loop", run_totals),
        ("qpf", "QPF, per product after the first", "loop", "loop", warm_totals),
        ("grid", "Grid, one product per fresh process", "cold-process", "cold-process", run_totals),
        ("grid", "Grid, per product (warm)", "warm", "loop", warm_totals),
    )
    for workload, label, base_mode, new_mode, measure in specs:
        base = pick(records, "cartopy-baseline", workload, base_mode)
        if base is None:
            continue
        b = measure(base)
        for series in SERIES[1:]:
            rec = pick(records, series, workload, new_mode)
            if rec is None:
                continue
            if base_mode == "loop" and measure is run_totals and product_names(base) != product_names(rec):
                SKIPPED.append(f"{label}: {SERIES_LABELS[series]} rendered a different product set "
                               f"({len(rec['runs'][0]['products'])} vs {len(base['runs'][0]['products'])} products); "
                               "loop totals not compared.")
                continue
            n = measure(rec)
            ratio = warm_speedup(base, rec) if measure is warm_totals else speedup(b, n)
            rows.append({"workload": workload, "label": label, "series": series, "config": rec["config"],
                         "baseline_s": b, "cartostack_s": n, "speedup": ratio,
                         "kind": "warm" if measure is warm_totals else base_mode})
    return rows


# --------------------------------------------------------------------------- log reconciliation

def reconcile(inputs, records) -> list[dict]:
    """Recompute the numbers quoted in SESSIONS.md on their original basis and on the common basis."""
    qb = json.loads((inputs["qpf_baseline"] / "results.json").read_text())
    gb = json.loads((inputs["grid_baseline"] / "results.json").read_text())
    pr = json.loads((inputs["prototype_runtime"] / "runtime.json").read_text())["summary"]
    comp = json.loads((inputs["compositor"] / "results.json").read_text())["scenes"]
    cmp = {(c["workload"], c["label"], c["series"]): c for c in comparisons(records)}

    def e2e(s):
        return s["interpreter_start_s"]["median"] + s["end_to_end_in_process_s"]["median"]

    def common(workload, label, which):
        c = cmp.get((workload, label, "prototype"))
        return None if c is None else c[which]["median"] if which != "speedup" else c["speedup"]["median"]

    loop_rest = [v for k, v in qb["loop"]["per_product_median_s"]["product_total"].items() if k != "1"]
    proto_runs = json.loads((inputs["prototype_runtime"] / "runtime.json").read_text())["runs"]["qpf-loop-zlib"]
    proto_loop = [statistics.median(r["products"][i]["phases_s"]["render_total"] for r in proto_runs)
                  for i in range(1, len(proto_runs[0]["products"]))]
    rows = [
        # (claim, value in the log, recomputed on the log's basis, basis, value on the common basis)
        ("QPF baseline, cold product (s)", 12.23, qb["single_product"]["process_wall_s"]["median"],
         "process wall (includes post-run PNG analysis and exit)",
         common("qpf", "QPF Day 1-3, one product per fresh process", "baseline_s")),
        ("QPF prototype zlib, cold product (s)", 0.33, e2e(pr["qpf-single-zlib"]),
         "interpreter start + in-process end-to-end",
         common("qpf", "QPF Day 1-3, one product per fresh process", "cartostack_s")),
        ("QPF cold speed-up (x)", 37, qb["single_product"]["process_wall_s"]["median"] / e2e(pr["qpf-single-zlib"]),
         "baseline process wall / prototype in-process",
         common("qpf", "QPF Day 1-3, one product per fresh process", "speedup")),
        ("QPF baseline, 18-product loop (s)", 32.1, qb["loop"]["process_wall_s"]["median"],
         "process wall (includes measurement encodes, analysis and exit)",
         common("qpf", "QPF, 18 products in one process", "baseline_s")),
        ("QPF prototype zlib, 18-product loop (s)", 3.64, e2e(pr["qpf-loop-zlib"]),
         "interpreter start + in-process end-to-end",
         common("qpf", "QPF, 18 products in one process", "cartostack_s")),
        ("QPF loop speed-up (x)", 8.8, qb["loop"]["process_wall_s"]["median"] / e2e(pr["qpf-loop-zlib"]),
         "baseline process wall / prototype in-process",
         common("qpf", "QPF, 18 products in one process", "speedup")),
        ("QPF baseline, per product after the first (s)", 0.92, statistics.median(loop_rest),
         "median over products 2-18 of each product's median over both loop runs",
         common("qpf", "QPF, per product after the first", "baseline_s")),
        ("QPF prototype, per product (s)", 0.156, statistics.median(proto_loop),
         "median over products 2-18 of each product's median render_total",
         common("qpf", "QPF, per product after the first", "cartostack_s")),
        ("QPF per-product speed-up (x)", 5.9, statistics.median(loop_rest) / statistics.median(proto_loop),
         "ratio of the two medians above", common("qpf", "QPF, per product after the first", "speedup")),
        ("Grid baseline, cold product (s)", 16.20, gb["cold"]["process_wall_s"]["median"],
         "process wall (includes three measurement-only encodes, hashing and exit)",
         common("grid", "Grid, one product per fresh process", "baseline_s")),
        ("Grid prototype zlib, cold product (s)", 0.12, e2e(pr["grid-single-zlib"]),
         "interpreter start + in-process end-to-end",
         common("grid", "Grid, one product per fresh process", "cartostack_s")),
        ("Grid cold speed-up (x)", 130, gb["cold"]["process_wall_s"]["median"] / e2e(pr["grid-single-zlib"]),
         "baseline process wall / prototype in-process",
         common("grid", "Grid, one product per fresh process", "speedup")),
        ("Grid baseline, warm repetition (s)", 0.32, gb["warm"]["total_s"]["median"],
         "warm total_s (includes four encodes, hashing and figure close)",
         common("grid", "Grid, per product (warm)", "baseline_s")),
        ("Grid prototype, per product (s)", 0.030, pr["grid-variants-png"]["per_product_phases_s"]["render_total"]["median"],
         "median render_total over all 8 products",
         common("grid", "Grid, per product (warm)", "cartostack_s")),
        ("Grid warm speed-up (x)", 10.5, gb["warm"]["total_s"]["median"]
         / pr["grid-variants-png"]["per_product_phases_s"]["render_total"]["median"],
         "ratio of the two values above", common("grid", "Grid, per product (warm)", "speedup")),
        ("QPF prototype 2x, pixels differing > 8 (%)", 0.33, 100 * find(records, "prototype", "qpf", "loop", "png")["accuracy"]["frac_gt8"],
         "compare.json, region all, median over 18 products", None),
        ("Package QPF load (ms)", 12.0, comp["qpf"]["load"]["median_ms"], "Session 06 median", None),
        ("Package QPF warm render (ms)", 15.3, comp["qpf"]["render_warm"]["median_ms"], "Session 06 median", None),
        ("Package grid warm render (ms)", 2.1, comp["grid"]["render_warm"]["median_ms"], "Session 06 median", None),
    ]
    out = []
    for claim, logged, recomputed, basis, common_value in rows:
        # The log quotes 2-3 significant figures; a recomputed value matches when it rounds to it.
        digits = max(0, len(str(logged).split(".")[1]) if "." in str(logged) else 0)
        tolerance = 0.5 * 10 ** -digits if digits else 0.5 if logged < 100 else 5
        out.append({"claim": claim, "log": logged, "recomputed_log_basis": recomputed,
                    "matches_log": abs(recomputed - logged) <= tolerance + 1e-9, "log_basis": basis,
                    "common_basis": common_value})
    return out


# --------------------------------------------------------------------------- figures

def style() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 9, "xtick.color": INK_2, "ytick.color": INK_2,
        "xtick.labelsize": 8.5, "ytick.labelsize": 8.5, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
        "legend.frameon": False, "legend.fontsize": 8.5, "font.size": 9, "text.color": INK,
        "lines.linewidth": 2, "lines.solid_capstyle": "round",
    })


def footer(fig, sources, stamp) -> None:
    text = "Sources: " + "; ".join(sorted(set(sources))) + f"\nGenerated {stamp} by benchmarks/report.py"
    fig.text(0.01, 0.005, text, fontsize=6.5, color=INK_2, va="bottom", ha="left", wrap=True)


def fmt_s(seconds: float) -> str:
    return f"{seconds:.2f} s" if seconds >= 1 else f"{seconds * 1000:.0f} ms"


def fig_cold_phases(records, out: Path, stamp: str) -> list[str]:
    """Total time per series (linear) next to where that time goes (share of total)."""
    workloads = [w for w in ("qpf", "grid") if pick(records, "cartopy-baseline", w, "cold-process")]
    fig, axes = plt.subplots(len(workloads), 2, figsize=(12, 2.6 * len(workloads) + 1.4),
                             gridspec_kw={"width_ratios": [1, 1.25]}, squeeze=False)
    titles = {"qpf": "QPF Day 1-3 (2210 × 1848)", "grid": "Synthetic grid (1200 × 800)"}
    sources = []
    for row, workload in enumerate(workloads):
        recs = [r for s in SERIES if (r := pick(records, s, workload, "cold-process"))]
        sources += [r["source"] for r in recs]
        labels = [SERIES_LABELS[r["series"]] + ("" if r["series"] == "cartopy-baseline" else f" ({r['config']})")
                  for r in recs]
        totals = [run_totals(r) for r in recs]
        y = np.arange(len(recs))[::-1]
        ax = axes[row][0]
        med = [t["median"] for t in totals]
        ax.barh(y, med, height=0.5, color=NEUTRAL_BAR)
        ax.errorbar(med, y, xerr=[[m - t["min"] for m, t in zip(med, totals, strict=True)],
                                   [t["max"] - m for m, t in zip(med, totals, strict=True)]],
                    fmt="none", ecolor=INK, elinewidth=1, capsize=3)
        base = totals[0]["median"]
        for yi, t in zip(y, totals, strict=True):
            extra = "" if t is totals[0] else f"  ({base / t['median']:.0f}× faster)"
            ax.text(t["max"] + base * 0.015, yi, fmt_s(t["median"]) + extra, va="center", fontsize=8.5, color=INK)
        ax.set_yticks(y, labels)
        ax.set_xlim(0, base * 1.35)
        ax.set_xlabel("Seconds, fresh process (median; whiskers min–max)")
        ax.grid(axis="y", visible=False)
        ax.set_title(f"{titles[workload]}: time to one PNG")
        ax2 = axes[row][1]
        for yi, rec in zip(y, recs, strict=True):
            phases = median_phases(rec)
            total = sum(phases.values())
            left = 0.0
            for ph in PHASES:
                share = phases[ph] / total
                if share <= 0:
                    continue
                ax2.barh(yi, share * 100, left=left, height=0.5, color=PHASE_COLORS[ph],
                         edgecolor=SURFACE, linewidth=1.5)
                if share >= 0.07:
                    ax2.text(left + share * 50, yi, f"{share * 100:.0f}%", ha="center", va="center",
                             fontsize=8, color="white" if ph in ("startup", "static", "text", "composite", "encode", "load") else INK)
                left += share * 100
        ax2.set_yticks(y, [""] * len(y))
        ax2.set_xlim(0, 100)
        ax2.set_xlabel("Share of that series' own total (%)")
        ax2.grid(axis="y", visible=False)
        ax2.set_title("Where the time goes", loc="right")
    handles = [Patch(color=PHASE_COLORS[ph], label=PHASE_LABELS[ph]) for ph in PHASES]
    fig.legend(handles=handles, loc="lower center", ncol=3, bbox_to_anchor=(0.5, 0.045))
    fig.suptitle("Cold start: Matplotlib/Cartopy versus CartoStack, one product per process",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0.17, 1, 0.97))
    footer(fig, sources, stamp)
    fig.savefig(out / "cold_phases.png", dpi=150)
    plt.close(fig)
    return sources


def fig_loop_cumulative(records, out: Path, stamp: str) -> list[str]:
    fig, ax = plt.subplots(figsize=(11, 5.6))
    sources = []
    for series in SERIES:
        rec = pick(records, series, "qpf", "loop")
        if rec is None:
            continue
        sources.append(rec["source"])
        for i, run in enumerate(rec["runs"]):
            t = sum(run["phases_s"].get(k, 0.0) for k in ("startup", "load"))
            ys = [t]
            for p in run["products"]:
                t += product_total(p)
                ys.append(t)
            xs = np.arange(len(ys))
            label = SERIES_LABELS[series] + ("" if series == "cartopy-baseline" else f" ({rec['config']})")
            ax.plot(xs, ys, color=SERIES_COLORS[series], label=label if i == 0 else None,
                    marker="o", markersize=4, markeredgecolor=SURFACE, markeredgewidth=1, alpha=0.9)
        ends = [accounted(r) for r in rec["runs"]]
        ax.annotate(f"{SERIES_LABELS[series]}: {fmt_s(statistics.median(ends))}",
                    (len(rec["runs"][0]["products"]), statistics.median(ends)),
                    xytext=(8, 0), textcoords="offset points", va="center", fontsize=9, color=INK)
    base = pick(records, "cartopy-baseline", "qpf", "loop")
    if base:
        first = statistics.median(product_total(r["products"][0]) for r in base["runs"])
        ax.annotate(f"First Cartopy product: {fmt_s(first)}\n(projecting and clipping static geometry)",
                    (1, statistics.median(r["phases_s"]["startup"] + r["phases_s"]["load"]
                                          + product_total(r["products"][0]) for r in base["runs"])),
                    xytext=(30, -30), textcoords="offset points", fontsize=8.5, color=INK_2,
                    arrowprops={"arrowstyle": "-", "color": INK_2, "linewidth": 0.8})
    ax.set_xlim(0, 21.5)
    ax.set_ylim(bottom=0)
    ax.set_xticks(range(0, 19, 3))
    ax.set_xlabel("Products finished (0 = after start-up and loading)")
    ax.set_ylabel("Seconds since process start")
    ax.set_title("QPF: 18 products rendered in one process, every run shown", fontsize=13)
    ax.legend(loc="upper left")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    footer(fig, sources, stamp)
    fig.savefig(out / "qpf_loop_cumulative.png", dpi=150)
    plt.close(fig)
    return sources


def fig_polygons(records, out: Path, stamp: str) -> list[str]:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharex=True)
    sources = []
    for series in SERIES:
        rec = pick(records, series, "qpf", "loop")
        if rec is None:
            continue
        sources.append(rec["source"])
        by_name: dict[str, list[dict]] = {}
        for p in warm_products(rec):
            by_name.setdefault(p["name"], []).append(p)
        xs = [ps[0]["n_polygons"] for ps in by_name.values()]
        data = [statistics.median(p["phases_s"].get("data", 0.0) for p in ps) * 1000 for ps in by_name.values()]
        total = [statistics.median(product_total(p) for p in ps) * 1000 for ps in by_name.values()]
        label = SERIES_LABELS[series] + ("" if series == "cartopy-baseline" else f" ({rec['config']})")
        for ax, ys in zip(axes, (data, total), strict=True):
            ax.scatter(xs, ys, s=36, color=SERIES_COLORS[series], edgecolor=SURFACE, linewidth=1.2,
                       label=label, zorder=3)
    axes[0].set_title("Data polygons only")
    axes[1].set_title("Whole product")
    for ax in axes:
        ax.set_xlabel("Polygons in the product (after clipping)")
        ax.set_ylabel("Milliseconds per product (median over loop runs)")
        ax.set_ylim(bottom=0)
    axes[0].legend(loc="upper left")
    fig.suptitle("QPF: cost per product against polygon count (products after the first in a process)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))
    footer(fig, sources, stamp)
    fig.savefig(out / "qpf_time_vs_polygons.png", dpi=150)
    plt.close(fig)
    return sources


CONFIG_LABELS = {("qpf", "png"): "2× polygons (default)", ("qpf", "png-k1"): "1× polygons",
                 ("qpf", "png-k4"): "4× polygons", ("qpf", "png-agg"): "Agg-exact stacking",
                 ("qpf", "png-split"): "static layers split", ("grid", "png"): "Pillow stacking (default)",
                 ("grid", "png-agg"): "Agg-exact stacking",
                 ("qpf", "zlib-k1"): "exact 1×", ("qpf", "zlib-k2"): "exact 2×",
                 ("qpf", "zlib-k4"): "exact 4× (default)", ("grid", "zlib"): "index map + LUT (default)"}
SHORT_SERIES = {"prototype": "Prototype", "package": "Package"}


def fig_accuracy(records, out: Path, stamp: str) -> list[str]:
    """One row per configuration: time per product (bars) next to pixel differences (dots)."""
    panels = []
    for workload in ("qpf", "grid"):
        base = pick(records, "cartopy-baseline", workload, "warm" if workload == "grid" else "loop")
        recs = [r for r in records if r["series"] != "cartopy-baseline" and r["workload"] == workload
                and "accuracy" in r]
        if base is not None and recs:
            recs.sort(key=lambda r: (SERIES.index(r["series"]), r["config"]))
            panels.append((workload, base, recs))
    if not panels:
        return []
    heights = [len(recs) + 1 for _, _, recs in panels]
    fig, axes = plt.subplots(len(panels), 2, figsize=(12, 0.42 * sum(heights) + 2.6), squeeze=False,
                             gridspec_kw={"height_ratios": heights, "width_ratios": [1, 1]})
    titles = {"qpf": "QPF, 18 products", "grid": "Grid, 4 variants"}
    sources = []
    for (workload, base, recs), (ax_t, ax_a) in zip(panels, axes, strict=True):
        sources += [base["source"], *[r["source"] for r in recs]]
        base_ms = warm_totals(base)["median"] * 1000
        labels = ["Matplotlib/Cartopy (reference)"] + [
            f"{SHORT_SERIES[r['series']]}: {CONFIG_LABELS.get((workload, r['config']), r['config'])}" for r in recs]
        y = np.arange(len(labels))[::-1]
        ms = [base_ms] + [warm_totals(r)["median"] * 1000 for r in recs]
        colors = [SERIES_COLORS["cartopy-baseline"]] + [SERIES_COLORS[r["series"]] for r in recs]
        ax_t.barh(y, ms, height=0.6, color=colors)
        for yi, m in zip(y, ms, strict=True):
            text = f"{m:.0f} ms" + ("" if m == base_ms else f"  ({base_ms / m:.1f}× faster)")
            ax_t.text(m + base_ms * 0.015, yi, text, va="center", fontsize=8, color=INK)
        ax_t.set_xlim(0, base_ms * 1.45)
        ax_t.set_yticks(y, labels)
        ax_t.grid(axis="y", visible=False)
        ax_t.set_title(f"{titles[workload]}: milliseconds per product")
        for yi, rec in zip(y[1:], recs, strict=True):
            acc = rec["accuracy"]
            color = SERIES_COLORS[rec["series"]]
            ax_a.plot([acc["frac_gt32"] * 100, acc["frac_gt8"] * 100], [yi, yi], color=color, linewidth=1.5, alpha=0.5)
            ax_a.scatter([acc["frac_gt8"] * 100], [yi], s=46, color=color, edgecolor=SURFACE, linewidth=1.2, zorder=3)
            ax_a.scatter([acc["frac_gt32"] * 100], [yi], s=42, facecolor=SURFACE, edgecolor=color, linewidth=1.6,
                         zorder=3)
            ax_a.text(acc["frac_gt8"] * 100, yi + 0.28,
                      f"{acc['frac_gt8'] * 100:.2f} % / {acc['frac_gt32'] * 100:.3f} %",
                      fontsize=7.5, color=INK_2, ha="center", va="bottom")
        ax_a.text(0, y[0], "  the reference itself", va="center", fontsize=8, color=INK_2)
        ax_a.set_ylim(axes_ylim := (y.min() - 0.6, y.max() + 0.7))
        ax_t.set_ylim(axes_ylim)
        ax_a.set_xlim(0, max(r["accuracy"]["frac_gt8"] for r in recs) * 100 * 1.2)
        ax_a.set_yticks(y, [""] * len(y))
        ax_a.grid(axis="y", visible=False)
        ax_a.set_title("Pixels differing from the Cartopy render (%)")
    axes[-1][0].set_xlabel("Median per product, after the first in a process")
    axes[-1][1].set_xlabel("Share of all pixels (median over products)")
    handles = [Line2D([], [], marker="o", linestyle="", color=INK_2, markersize=7, label="differ by more than 8 levels"),
               Line2D([], [], marker="o", linestyle="", markerfacecolor=SURFACE, markeredgecolor=INK_2,
                      markersize=7, label="differ by more than 32 levels")]
    axes[0][1].legend(handles=handles, loc="lower right")
    fig.suptitle("Speed and accuracy of each configuration against the full Cartopy render",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0.05, 1, 0.96))
    footer(fig, sources, stamp)
    fig.savefig(out / "accuracy_vs_speed.png", dpi=150)
    plt.close(fig)
    return sources


def fig_speedups(rows, out: Path, stamp: str, sources: list[str]) -> None:
    labels = list(dict.fromkeys(r["label"] for r in rows))
    fig, ax = plt.subplots(figsize=(11, 0.75 * len(labels) + 1.8))
    offsets = {"prototype": 0.12, "package": -0.12}
    for row in rows:
        y = len(labels) - 1 - labels.index(row["label"]) + offsets.get(row["series"], 0)
        s = row["speedup"]
        color = SERIES_COLORS[row["series"]]
        ax.plot([s["min"], s["max"]], [y, y], color=color, linewidth=2, alpha=0.5)
        ax.scatter([s["median"]], [y], s=60, color=color, edgecolor=SURFACE, linewidth=1.5, zorder=3)
        ax.annotate(f"{s['median']:.1f}×  ({fmt_s(row['baseline_s']['median'])} → {fmt_s(row['cartostack_s']['median'])})",
                    (s["max"], y), xytext=(8, 0), textcoords="offset points", va="center", fontsize=8.5, color=INK)
    ax.set_xscale("log")
    ax.set_xlim(1, 2000)
    ax.axvline(1, color=INK_2, linewidth=1)
    ax.set_yticks(range(len(labels)), labels[::-1])
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Times faster than Matplotlib/Cartopy (log scale; dot = ratio of medians, line = min–max)")
    present = list(dict.fromkeys(r["series"] for r in rows))
    ax.legend(handles=[Line2D([], [], marker="o", linestyle="", color=SERIES_COLORS[s], markersize=8,
                              label=SERIES_LABELS[s]) for s in present], loc="lower right")
    ax.set_title("Speed-up by workload and mode: cold, loop and warm kept separate", fontsize=13)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    footer(fig, sources, stamp)
    fig.savefig(out / "speedup_summary.png", dpi=150)
    plt.close(fig)


def fig_components(records, out: Path, stamp: str) -> list[str]:
    comps = [r for r in records if r["mode"] == "components"]
    if not comps:
        return []
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    sources = []
    names = ("load", "composite", "encode")
    labels = {"load": "Load file", "composite": "Composite (warm)", "encode": "PNG encode"}
    for ax, workload in zip(axes, ("qpf", "grid"), strict=True):
        pkg = next((r for r in comps if r["workload"] == workload), None)
        proto = pick(records, "prototype", workload, "loop")
        # Session 06 scenes use zlib layers; compare load with the prototype's zlib cold runs.
        proto_load = find(records, "prototype", workload, "cold-process", "zlib")
        if pkg is None or proto is None or proto_load is None:
            ax.set_visible(False)
            continue
        sources += [pkg["source"], proto["source"]]
        proto_vals = {"load": statistics.median(r["phases_s"]["load"] for r in proto_load["runs"]),
                      "composite": median_phases(proto, products_only=True)["composite"],
                      "encode": median_phases(proto, products_only=True)["encode"]}
        pkg_vals = pkg["runs"][0]["phases_s"]
        x = np.arange(len(names))
        for dx, series, vals in ((-0.17, "prototype", proto_vals), (0.17, "package", pkg_vals)):
            heights = [vals[n] * 1000 for n in names]
            ax.bar(x + dx, heights, width=0.32, color=SERIES_COLORS[series], label=SERIES_LABELS[series])
            for xi, h in zip(x + dx, heights, strict=True):
                ax.text(xi, h, f"{h:.1f}", ha="center", va="bottom", fontsize=8, color=INK)
        ax.set_xticks(x, [labels[n] for n in names])
        ax.set_ylim(0, max(max(proto_vals.values()), max(pkg_vals.values())) * 1000 * 1.15)
        ax.grid(axis="x", visible=False)
        ax.set_ylabel("Milliseconds (median)")
        ax.set_title({"qpf": "QPF (2210 × 1848)", "grid": "Grid (1200 × 800)"}[workload])
    axes[0].legend(loc="upper left")
    fig.suptitle("Package components measured so far (Session 06) against the prototype",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    footer(fig, sources, stamp)
    fig.savefig(out / "package_components.png", dpi=150)
    plt.close(fig)
    return sources


# --------------------------------------------------------------------------- index

def write_index(out: Path, stamp: str, inputs: dict, records, rows, checks) -> None:
    lines = [f"# CartoStack benchmark report ({stamp})", "",
             "Generated by `uv run benchmarks/report.py`. All times exclude measurement-only work "
             "(extra PNG encodes, output hashing) and process exit, for every series alike.", "",
             "## Figures", "",
             "- `speedup_summary.png`: speed-up by workload and mode (cold, loop, warm kept separate).",
             "- `cold_phases.png`: one product per fresh process; total time and where it goes.",
             "- `qpf_loop_cumulative.png`: 18 QPF products in one process, cumulative time.",
             "- `qpf_time_vs_polygons.png`: per-product cost against polygon count.",
             "- `accuracy_vs_speed.png`: time per product and pixel differences for each configuration.",
             "- `package_components.png`: package load, compositing and encoding against the prototype.", "",
             "## Speed-ups (common basis)", "",
             "Cold and loop ranges pair the slowest and fastest runs; per-product ranges are the "
             "spread of same-product ratios (or slowest/fastest products when none are shared).", "",
             "| Comparison | Series | Matplotlib/Cartopy | CartoStack | Speed-up (median) | Range |",
             "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        s = r["speedup"]
        lines.append(f"| {r['label']} | {SERIES_LABELS[r['series']]} ({r['config']}) | "
                     f"{fmt_s(r['baseline_s']['median'])} | {fmt_s(r['cartostack_s']['median'])} | "
                     f"{s['median']:.1f}× | {s['min']:.1f}–{s['max']:.1f}× |")
    if SKIPPED:
        lines += ["", "Not compared:", ""] + [f"- {m}" for m in sorted(set(SKIPPED))]
    lines += ["", "## Cold product phases (median ms)", "",
              "| Workload | Series | " + " | ".join(PHASE_LABELS[p] for p in PHASES) + " | Total |",
              "| --- | --- | " + " | ".join("---" for _ in PHASES) + " | --- |"]
    for workload in ("qpf", "grid"):
        for series in SERIES:
            rec = pick(records, series, workload, "cold-process")
            if rec is None:
                continue
            ph = median_phases(rec)
            lines.append(f"| {workload} | {SERIES_LABELS[series]} | "
                         + " | ".join(f"{ph[p] * 1000:.1f}" for p in PHASES)
                         + f" | {run_totals(rec)['median'] * 1000:.0f} |")
    lines += ["", "## Reconciliation with SESSIONS.md", "",
              "`Recomputed` uses the basis the log used; `Common basis` is what the figures plot.", "",
              "| Claim | Log | Recomputed | Matches | Log basis | Common basis |",
              "| --- | --- | --- | --- | --- | --- |"]
    for c in checks:
        cb = "" if c["common_basis"] is None else f"{c['common_basis']:.4g}"
        lines.append(f"| {c['claim']} | {c['log']} | {c['recomputed_log_basis']:.4g} | "
                     f"{'yes' if c['matches_log'] else '**no**'} | {c['log_basis']} | {cb} |")
    lines += ["", "## Inputs", ""] + [f"- {k}: `{rel(v)}`" for k, v in inputs.items()]
    notes = sorted({n for r in records for n in r["notes"]})
    lines += ["", "## Notes", ""] + [f"- {n}" for n in notes] + [""]
    (out / "index.md").write_text("\n".join(lines))


# --------------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--grid-baseline", type=Path)
    parser.add_argument("--qpf-baseline", type=Path)
    parser.add_argument("--prototype-runtime", type=Path)
    parser.add_argument("--compositor", type=Path)
    parser.add_argument("--bench", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    inputs = select_inputs(args)
    records = [*adapt_grid_baseline(inputs["grid_baseline"]), *adapt_qpf_baseline(inputs["qpf_baseline"]),
               *adapt_prototype(inputs["prototype_runtime"]), *adapt_compositor(inputs["compositor"])]
    # Native results, oldest first by directory name (UTC stamps); a newer record replaces an
    # older one with the same series, workload, mode and config, so re-runs supersede.
    native = sorted({*RESULTS.glob("**/bench.json"), *args.bench}, key=lambda p: (p.parent.name, str(p)))
    latest: dict[tuple[str, str, str, str], dict] = {}
    for path in native:
        for rec in read_native(path):
            latest[(rec["series"], rec["workload"], rec["mode"], rec["config"])] = rec
    used = {rec["source"] for rec in latest.values()}
    for path in native:
        if any(src.startswith(rel(path.parent)) for src in used):
            inputs[f"bench:{path.parent.name}"] = path
    records += list(latest.values())
    fill_polygon_counts(records)
    problems = check_phases(records)
    if problems:
        sys.exit("inconsistent phases (an adapter double-counts or misses time):\n  " + "\n  ".join(problems))

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or RESULTS / f"report-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    style()
    rows = comparisons(records)
    checks = reconcile(inputs, records)
    sources = fig_cold_phases(records, out, stamp)
    sources += fig_loop_cumulative(records, out, stamp)
    fig_polygons(records, out, stamp)
    fig_accuracy(records, out, stamp)
    fig_speedups(rows, out, stamp, sources)
    fig_components(records, out, stamp)
    write_index(out, stamp, inputs, records, rows, checks)
    (out / "report.json").write_text(json.dumps({
        "schema": SCHEMA, "created_utc": stamp, "inputs": {k: rel(v) for k, v in inputs.items()},
        "comparisons": rows, "reconciliation": checks, "records": records}, indent=1))

    print(f"report → {rel(out)}")
    for r in rows:
        print(f"  {r['label']:45} {r['series']:10} {r['speedup']['median']:7.1f}× "
              f"({fmt_s(r['baseline_s']['median'])} → {fmt_s(r['cartostack_s']['median'])})")
    for m in sorted(set(SKIPPED)):
        print(f"  not compared: {m}")
    bad = [c for c in checks if not c["matches_log"]]
    print(f"  reconciliation: {len(checks) - len(bad)}/{len(checks)} logged values recomputed exactly")
    for c in bad:
        print(f"    MISMATCH {c['claim']}: log {c['log']}, recomputed {c['recomputed_log_basis']:.4g}")


if __name__ == "__main__":
    main()
