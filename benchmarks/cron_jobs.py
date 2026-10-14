"""Benchmark a scheduled job: the example maps with Matplotlib/Cartopy vs CartoStack.

A scheduled job typically starts a fresh Python process for each product family and
draws every map from scratch. This benchmark simulates one run of such a job with the
repository's examples, both ways, from data already on disk:

* **Matplotlib/Cartopy**: ``examples/hazard_map.py`` (7 synthetic days x 2 regions =
  14 maps), then ``examples/qpf_map.py`` (18 WPC QPF products), each a fresh process in
  the development environment (``build`` extra).
* **CartoStack**: ``examples/update_hazard.py`` and ``examples/update_qpf.py`` on scenes
  authored once beforehand, each a fresh process in a core-only environment (cartostack,
  NumPy, Pillow), the way a slim job image would run them.

Each recorded run executes both jobs back to back; the two methods alternate which goes
first, after one unrecorded warm-up run. Per process: wall time (launch to exit),
user + system CPU, peak memory (``os.wait4``). Authoring time is reported separately; it
happens once per map layout, not per run.

    uv run --extra build python benchmarks/cron_jobs.py [--runs 10] [--readme]

Writes ``benchmarks/results/cron-<UTC stamp>/``: ``timings.json``, ``stats.json``, and
the figures; ``--readme`` also copies the summary figure to ``docs/images/``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics as st
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
QPF_DAYS = sorted((ROOT / "benchmarks" / "data" / "qpf").glob("day_*"))
MODES = ("matplotlib", "cartostack")
LABEL = {"matplotlib": "Matplotlib/Cartopy", "cartostack": "CartoStack"}
COLOR = {"matplotlib": "#2a78d6", "cartostack": "#1baf7a"}  # dataviz reference palette
JOBS = ("hazard", "qpf")
JOB_LABEL = {"hazard": "Hazard\n(14 maps)", "qpf": "QPF\n(18 maps)", "job": "Both\n(32 maps)"}
INK, MUTED, GRID = "#1f1e1c", "#6b6a66", "#e6e4df"


def run_process(argv: list[str], env: dict[str, str]) -> dict:
    t0 = time.perf_counter()
    proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.read() if proc.stdout else b""
    _, status, usage = os.wait4(proc.pid, 0)
    wall = time.perf_counter() - t0
    if os.waitstatus_to_exitcode(status) != 0:
        raise RuntimeError(f"{argv} failed:\n{out.decode(errors='replace')[-2000:]}")
    rss = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
    return {"wall_s": wall, "cpu_s": usage.ru_utime + usage.ru_stime, "max_rss_mb": rss / 2**20}


def commands(mode: str, work: Path, core_python: Path, out: Path) -> dict[str, list[str]]:
    tifs = [str(p) for p in sorted((work / "inputs").glob("*.tif"))]
    days = [str(p) for p in QPF_DAYS]
    if mode == "matplotlib":
        py = sys.executable
        return {
            "hazard": [py, str(EXAMPLES / "hazard_map.py"), *tifs, "--out", str(out / "hazard")],
            "qpf": [py, str(EXAMPLES / "qpf_map.py"), *days, "--out", str(out / "qpf")],
        }
    py = str(core_python)
    return {
        "hazard": [py, "-I", str(EXAMPLES / "update_hazard.py"), str(work / "hazard"), *tifs,
                   "--out", str(out / "hazard")],
        "qpf": [py, "-I", str(EXAMPLES / "update_qpf.py"), str(work / "qpf.cstack"), *days,
                "--out", str(out / "qpf")],
    }  # fmt: skip


def setup(work: Path) -> tuple[Path, dict[str, float]]:
    """Author the scenes once and make a core-only venv; returns its python and timings."""
    authoring = {}
    t = time.perf_counter()
    subprocess.run([sys.executable, str(EXAMPLES / "author_hazard.py"), str(work / "hazard"),
                    "--inputs", str(work / "inputs")], check=True, capture_output=True)  # fmt: skip
    authoring["hazard"] = time.perf_counter() - t
    t = time.perf_counter()
    subprocess.run([sys.executable, str(EXAMPLES / "author_qpf.py"), str(work / "qpf.cstack")],
                   check=True, capture_output=True)  # fmt: skip
    authoring["qpf"] = time.perf_counter() - t
    venv = work / "venv-core"
    subprocess.run(["uv", "venv", "-q", "--python", platform.python_version(), str(venv)],
                   check=True)  # fmt: skip
    subprocess.run(["uv", "pip", "install", "-q", "--python", str(venv / "bin" / "python"),
                    str(ROOT)], check=True)  # fmt: skip
    return venv / "bin" / "python", authoring


def summarize(values: list[float]) -> dict[str, float]:
    return {"mean": st.mean(values), "median": st.median(values), "min": min(values),
            "max": max(values), "stdev": st.stdev(values) if len(values) > 1 else 0.0}  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--readme", action="store_true", help="copy the figure to docs/images/")
    args = parser.parse_args()
    if len(QPF_DAYS) != 18:
        sys.exit("needs the 18 WPC QPF fixtures: uv run scripts/fetch_fixtures.py")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results = ROOT / "benchmarks" / "results" / f"cron-{stamp}"
    results.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix="cartostack-cron-"))
    try:
        core_python, authoring = setup(work)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        env["MPLBACKEND"] = "Agg"
        runs = []
        for i in range(args.warmup + args.runs):
            order = MODES if i % 2 == 0 else MODES[::-1]
            record: dict = {"order": list(order)}
            for mode in order:
                out = work / "png" / mode
                shutil.rmtree(out, ignore_errors=True)
                t0 = time.perf_counter()
                cmds = commands(mode, work, core_python, out)
                record[mode] = {job: run_process(cmds[job], env) for job in JOBS}
                record[mode]["job_wall_s"] = time.perf_counter() - t0
                maps = len(list(out.rglob("*.png")))
                if maps != 32:
                    raise RuntimeError(f"{mode} wrote {maps} maps, expected 32")
            label = "warm-up" if i < args.warmup else f"run {i - args.warmup + 1:2d}"
            print(f"[{label}] " + "  ".join(
                f"{LABEL[m]} {record[m]['job_wall_s']:5.1f} s" for m in MODES), flush=True)  # fmt: skip
            if i >= args.warmup:
                runs.append(record)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    stats = statistics(runs)
    meta = {"stamp": stamp, "runs": args.runs, "warmup": args.warmup, "authoring_s": authoring,
            "machine": platform.platform(), "python": platform.python_version(),
            "qpf_products": [p.name for p in QPF_DAYS]}  # fmt: skip
    (results / "timings.json").write_text(json.dumps({**meta, "iterations": runs}, indent=1))
    (results / "stats.json").write_text(json.dumps({**meta, **stats}, indent=1))
    figures(results, runs, stats)
    if args.readme:
        shutil.copy2(results / "scheduled_job.png", ROOT / "docs" / "images" / "scheduled_job.png")
    for job in (*JOBS, "job"):
        m, c = stats["wall_s"][f"matplotlib/{job}"]["mean"], stats["wall_s"][f"cartostack/{job}"]["mean"]
        print(f"{job:7s} {m:6.2f} s -> {c:5.2f} s  ({m / c:.0f}x faster, average of {args.runs})")
    print(f"results -> {results.relative_to(ROOT)}")


def wall(run: dict, mode: str, job: str) -> float:
    return run[mode]["job_wall_s"] if job == "job" else run[mode][job]["wall_s"]


def statistics(runs: list[dict]) -> dict:
    out: dict = {"wall_s": {}, "cpu_s": {}, "max_rss_mb": {}, "speedup": {}}
    for mode in MODES:
        for job in (*JOBS, "job"):
            out["wall_s"][f"{mode}/{job}"] = summarize([wall(r, mode, job) for r in runs])
        for job in JOBS:
            for key in ("cpu_s", "max_rss_mb"):
                out[key][f"{mode}/{job}"] = summarize([r[mode][job][key] for r in runs])
    for job in (*JOBS, "job"):
        m, c = out["wall_s"][f"matplotlib/{job}"]["mean"], out["wall_s"][f"cartostack/{job}"]["mean"]
        out["speedup"][job] = m / c
    return out


def figures(results: Path, runs: list[dict], stats: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update({
        "font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
        "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
        "figure.facecolor": "white", "axes.titleweight": "bold", "axes.titlesize": 11,
    })  # fmt: skip

    # Wall time per job: average bars, one dot per run, speed-up per row.
    rows = (*JOBS, "job")
    fig, ax = plt.subplots(figsize=(9, 4.6))
    y = np.arange(len(rows))[::-1] * 1.0
    h = 0.34
    top = max(wall(r, "matplotlib", "job") for r in runs)
    for mode in MODES:
        off = h / 2 + 0.02 if mode == "matplotlib" else -h / 2 - 0.02
        for yi, job in zip(y, rows, strict=True):
            mean = stats["wall_s"][f"{mode}/{job}"]["mean"]
            ax.barh(yi + off, mean, height=h, color=COLOR[mode],
                    label=LABEL[mode] if job == rows[0] else None)  # fmt: skip
            pts = [wall(r, mode, job) for r in runs]
            ax.scatter(pts, np.full(len(pts), yi + off), s=10, color="white", edgecolor=INK,
                       linewidth=0.6, zorder=3)  # fmt: skip
            ax.text(max(pts) + top * 0.015, yi + off, f"{mean:.1f} s", va="center", fontsize=9,
                    color=INK)  # fmt: skip
    for yi, job in zip(y, rows, strict=True):
        ax.text(1.0, yi, f"{stats['speedup'][job]:.0f}× faster", transform=ax.get_yaxis_transform(),
                ha="right", va="center", fontsize=10, color=INK, fontweight="bold")  # fmt: skip
    ax.set_yticks(y, [JOB_LABEL[j] for j in rows])
    ax.set_xlabel(f"Time (average of {len(runs)} runs)")
    ax.set_xlim(0, top * 1.35)
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, frameon=False)
    ax.set_title("A scheduled job: fresh process per run, data on disk", loc="center", pad=26)
    fig.tight_layout()
    fig.savefig(results / "scheduled_job.png", dpi=160)
    plt.close(fig)

    # CPU time and peak memory per job (two charts, one measure each).
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    for ax, key, unit, title in ((axes[0], "cpu_s", "Time (s)", "CPU Time"),
                                 (axes[1], "max_rss_mb", "Memory (MB)", "Peak Memory")):  # fmt: skip
        x = np.arange(len(JOBS))
        for mode, dx in (("matplotlib", -0.2), ("cartostack", 0.2)):
            v = [stats[key][f"{mode}/{job}"]["mean"] for job in JOBS]
            ax.bar(x + dx, v, width=0.38, color=COLOR[mode], label=LABEL[mode])
            for xi, vi in zip(x + dx, v, strict=True):
                ax.text(xi, vi, f"{vi:.0f}" if vi >= 10 else f"{vi:.1f}", ha="center", va="bottom",
                        fontsize=9, color=INK)  # fmt: skip
        ax.set_xticks(x, ["Hazard", "QPF"])
        ax.set_ylabel(unit)
        ax.set_title(title, loc="center")
        ax.grid(axis="x", visible=False)
        ax.margins(y=0.15)
    axes[0].legend(frameon=False, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(results / "cpu_memory.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
