# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Session 13: fresh-environment, source-independence and no-network check of the runtime.

Stages (each in its own process; this orchestrator needs only the standard library and
``uv``):

1. **Author** the QPF and grid scenes from the baseline figures with the ``build`` extra
   (``benchmarks/author_scenes.py``), and export runtime inputs as plain NumPy files:
   every WPC QPF product's rings, values, and subtitle, and the grid's analytic fields
   (data acquisition is out of scope, as everywhere in this project).
2. **Core-only environment**: build the wheel and install it into a new venv inside the
   sandbox (outside the repository) with its dependencies only (NumPy, Pillow), then
   confirm that Matplotlib, Cartopy, pyproj, shapely, GeoPandas and pyogrio are not
   installed there.
3. **Sources removed**: the repository's fixture directory (``benchmarks/data``: Natural
   Earth, counties, GeoPackages, WPC shapefiles) is renamed away for the runtime stage and
   always restored. The runtime runs with ``HOME`` set inside the sandbox, so the user's
   Cartopy Natural Earth cache (``~/.local/share/cartopy``) is not reachable either; it is
   not touched.
4. **Runtime** in the core venv, in isolated mode (``python -I``), from the sandbox: an
   audit hook records every file opened and refuses every network call (``socket.*``,
   ``urllib.Request``). It renders all 18 QPF products, the grid's 4 variants, a grid
   normalisation change (colorbar redraw), an added layer, then saves, reloads and
   re-renders. It then checks ``sys.modules`` and the opened files: anything outside the
   sandbox, the venv and the Python installation fails.
5. **Compare** (development environment): the runtime PNGs against the full Cartopy
   renders (QPF: Session 01b products; grid: Session 02 references), and against the
   same updates rendered by the development environment (must be pixel-identical).

    uv run scripts/check_portability.py [--sandbox DIR] [--python 3.13]

Writes ``benchmarks/results/portability-<UTC stamp>/report.json`` and copies the PNGs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
DATA = ROOT / "benchmarks" / "data"
FORBIDDEN = ("matplotlib", "cartopy", "pyproj", "shapely", "geopandas", "pyogrio", "pandas")

EXPORT = r"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, "benchmarks")
import prototype_runtime as proto
from baseline_cartopy import make_field, title_text

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
meta = {"qpf": [], "grid": []}
for day in proto.QPF_DAYS:
    q = proto.read_qpf(proto.DATA / "qpf" / f"day_{day}")
    rings = [r for rec in q["rings"] for r in rec]
    np.savez(out / f"qpf_{day}.npz",
             coords=np.concatenate(rings) if rings else np.zeros((0, 2)),
             ring_sizes=np.array([len(r) for r in rings], np.int64),
             record_sizes=np.array([len(rec) for rec in q["rings"]], np.int64),
             values=np.asarray(q["values"], np.float64))
    meta["qpf"].append({"day": day, "subtitle": q["subtitle"]})
for v in range(4):
    _, _, values = make_field(v)
    np.save(out / f"grid_{v}.npy", values)
    meta["grid"].append({"variant": v, "subtitle": title_text(v)[1]})
(out / "inputs.json").write_text(json.dumps(meta, indent=1))
"""

RUNTIME = r"""  # noqa: UP031 - filled in with % below
import os, sys, json, time
SANDBOX = os.path.abspath(sys.argv[1])
OUT = os.path.join(SANDBOX, sys.argv[2])
opened, network = set(), []

def audit(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        opened.add(os.path.abspath(os.fsdecode(args[0])))
    elif event.startswith(("socket.", "urllib.Request")) and event != "socket.__new__":
        network.append(event)
        raise OSError(f"network access is blocked in this check ({event})")

sys.addaudithook(audit)
import numpy as np
from cartostack import Scene

os.makedirs(OUT, exist_ok=True)
inputs = os.path.join(SANDBOX, "inputs")
meta = json.load(open(os.path.join(inputs, "inputs.json")))
log = {"qpf": [], "grid": []}

def records(day):
    z = np.load(os.path.join(inputs, f"qpf_{day}.npz"))
    coords, ring_sizes, record_sizes = z["coords"], z["ring_sizes"], z["record_sizes"]
    rings = np.split(coords, np.cumsum(ring_sizes)[:-1]) if len(ring_sizes) else []
    out, k = [], 0
    for n in record_sizes:
        out.append(rings[k:k + n]); k += n
    return out, z["values"]

with Scene.load(os.path.join(SANDBOX, "qpf.cstack")) as s:
    for item in meta["qpf"]:
        recs, values = records(item["day"])
        t = time.perf_counter()
        s.replace_polygons("qpf", recs, values)
        s.text["subtitle"] = item["subtitle"]
        s.save_png(os.path.join(OUT, f"qpf_plot_day_{item['day']}.png"), mode="RGBA")
        log["qpf"].append({"day": item["day"], "s": time.perf_counter() - t})

with Scene.load(os.path.join(SANDBOX, "grid.cstack")) as s:
    for item in meta["grid"]:
        values = np.load(os.path.join(inputs, f"grid_{item['variant']}.npy"))
        s.replace_grid("temperature", values)
        s.text["subtitle"] = item["subtitle"]
        s.save_png(os.path.join(OUT, f"variant_{item['variant']}.png"), mode="RGB")
    s.replace_grid("temperature", values, vmin=-15.0, vmax=40.0)
    log["stale_colorbars_after_norm_change"] = list(s.stale_colorbars)
    s.save_png(os.path.join(OUT, "grid_new_norm.png"), mode="RGB")
    yy, xx = np.mgrid[0:48, 0:48]
    logo = np.zeros((48, 48, 4), np.uint8)
    logo[..., 0], logo[..., 2] = 200, 60
    logo[..., 3] = np.where((yy - 24) ** 2 + (xx - 24) ** 2 < 400, 255, 0)
    s.add_layer("logo", logo, left=1140, top=12)
    s.save_png(os.path.join(OUT, "grid_with_logo.png"), mode="RGB")
    saved = os.path.join(OUT, "grid_updated.cstack")
    s.save(saved)
    again = Scene.load(saved)
    log["reload_identical"] = bool(np.array_equal(again.render(), s.render()))

prefixes = [SANDBOX, os.path.realpath(SANDBOX), sys.prefix, sys.base_prefix,
            os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix), "/dev/"]
log["files_opened"] = len(opened)
log["outside_allowed"] = sorted(p for p in opened if not any(p.startswith(x) for x in prefixes))
log["network_attempts"] = network
log["forbidden_modules"] = sorted(m for m in sys.modules if m.split(".")[0] in __FORBIDDEN__)
log["python"] = sys.version.split()[0]
log["executable"] = sys.executable
import PIL
log["versions"] = {"numpy": np.__version__, "pillow": PIL.__version__,
                   "cartostack": __import__("cartostack").__version__}
log["cartostack_file"] = __import__("cartostack").__file__
print(json.dumps(log))
""".replace("__FORBIDDEN__", repr(FORBIDDEN))

COMPARE = r"""
import json, sys, statistics
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, "benchmarks")
from prototype_compare import diff_stats, region_masks

core, dev, out = (Path(a) for a in sys.argv[1:4])
build = max(p for p in Path("benchmarks/results").glob("prototype-*") if (p / "layers").is_dir())
qpf_ref = max(Path("benchmarks/results").glob("qpf-*/products"))
grid_ref = max(build.glob("runtime-*/compare"))
rgb = lambda p: np.asarray(Image.open(p).convert("RGB")).astype(np.int16)
report = {"identical_to_dev_env": {}, "qpf": {}, "grid": {}}
for png in sorted(core.glob("*.png")):
    a, b = np.asarray(Image.open(png)), np.asarray(Image.open(dev / png.name))
    report["identical_to_dev_env"][png.name] = bool(a.shape == b.shape and np.array_equal(a, b))
masks = region_masks(build / "layers", "qpf")
for png in sorted(core.glob("qpf_plot_day_*.png")):
    report["qpf"][png.name] = diff_stats(rgb(png), rgb(qpf_ref / png.name), masks)
gmasks = region_masks(build / "layers", "grid")
for v in range(4):
    report["grid"][f"variant_{v}"] = diff_stats(rgb(core / f"variant_{v}.png"),
                                                rgb(grid_ref / f"ref_grid_variant_{v}.png"), gmasks)
def med(section, region, metric):
    return statistics.median(p[region][metric] for p in report[section].values())
report["summary"] = {
    "qpf_all_gt8": med("qpf", "all", "frac_gt8"), "qpf_all_gt32": med("qpf", "all", "frac_gt32"),
    "grid_all_gt8": med("grid", "all", "frac_gt8"),
    "grid_all_gt32": med("grid", "all", "frac_gt32"),
    "identical_to_dev_env": all(report["identical_to_dev_env"].values()),
    "pngs": len(report["identical_to_dev_env"]),
}
(out / "compare.json").write_text(json.dumps(report, indent=1))
print(json.dumps(report["summary"]))
"""


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    shown = " ".join(str(c) if len(str(c)) < 120 else "<inline script>" for c in cmd)
    print("$", shown, flush=True)
    return subprocess.run(cmd, text=True, capture_output=True, check=True, **kw)  # type: ignore[call-overload]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sandbox", type=Path, help="working directory outside the repository")
    parser.add_argument("--python", default="3.13", help="Python for the core-only venv")
    parser.add_argument("--authored", type=Path, help="reuse an authored-<stamp> directory")
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sandbox = (args.sandbox or Path(tempfile.mkdtemp(prefix="cartostack-portability-"))).resolve()
    if ROOT in sandbox.parents or sandbox == ROOT:
        sys.exit("the sandbox must be outside the repository")
    sandbox.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"portability-{stamp}"
    out.mkdir(parents=True)
    report: dict[str, object] = {"created_utc": stamp, "sandbox": str(sandbox)}

    # 1. Author and export inputs (build-capable environments, with the fixtures present).
    if args.authored:
        authored = args.authored.resolve()
    else:
        run(["uv", "run", "benchmarks/author_scenes.py"], cwd=ROOT)
        authored = max(RESULTS.glob("authored-*"))
    for name in ("qpf.cstack", "grid.cstack"):
        shutil.copy2(authored / name, sandbox / name)
    run(["uv", "run", "python", "-c", EXPORT, str(sandbox / "inputs")], cwd=ROOT)
    report["authored"] = str(authored.relative_to(ROOT))

    # 2. Core-only venv from the wheel, outside the repository.
    dist = sandbox / "dist"
    run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=ROOT)
    wheel = max(dist.glob("cartostack-*.whl"))
    venv = sandbox / "venv"
    run(["uv", "venv", "--python", args.python, str(venv)], cwd=sandbox)
    py = venv / "bin" / "python"
    run(["uv", "pip", "install", "--python", str(py), str(wheel)], cwd=sandbox)
    probe = (
        "import importlib.util as u, json; "
        f"print(json.dumps({{m: u.find_spec(m) is not None for m in {FORBIDDEN!r}}}))"
    )
    installed = json.loads(run([str(py), "-I", "-c", probe], cwd=sandbox).stdout)
    report["core_env_installed"] = installed
    pkgs = run(["uv", "pip", "list", "--python", str(py), "--format", "json"], cwd=sandbox).stdout
    report["core_env_packages"] = sorted(p["name"] for p in json.loads(pkgs))
    if any(installed.values()):
        sys.exit(f"the core venv has build libraries installed: {installed}")

    # 3 + 4. Runtime with the sources renamed away, isolated mode, HOME in the sandbox.
    (sandbox / "runtime.py").write_text(RUNTIME)
    home = sandbox / "home"
    home.mkdir(exist_ok=True)
    hidden = DATA.with_name(f".data-hidden-{stamp}")
    DATA.rename(hidden)
    try:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home)}
        res = run(
            [str(py), "-I", str(sandbox / "runtime.py"), str(sandbox), "out-core"],
            cwd=sandbox,
            env=env,
        )
    finally:
        hidden.rename(DATA)
    core_log = json.loads(res.stdout.strip().splitlines()[-1])
    report["runtime"] = core_log
    report["sources_hidden"] = {"fixtures": str(DATA.relative_to(ROOT)), "home": str(home)}

    # The same updates in the development environment, for an identity check.
    dev = run(
        ["uv", "run", "python", str(sandbox / "runtime.py"), str(sandbox), "out-dev"], cwd=ROOT
    )
    report["dev_runtime_python"] = json.loads(dev.stdout.strip().splitlines()[-1])["python"]

    # 5. Compare.
    cmp = run(
        ["uv", "run", "python", "-c", COMPARE,
         str(sandbox / "out-core"), str(sandbox / "out-dev"), str(out)],
        cwd=ROOT,
    )  # fmt: skip
    report["compare"] = json.loads(cmp.stdout.strip().splitlines()[-1])
    shutil.copytree(sandbox / "out-core", out / "out-core")
    checks = {
        "no build libraries installed": not any(installed.values()),
        "no forbidden modules imported": not core_log["forbidden_modules"],
        "no files opened outside sandbox/venv/python": not core_log["outside_allowed"],
        "no network attempts": not core_log["network_attempts"],
        "cartostack imported from the venv": str(venv) in core_log["cartostack_file"],
        "reload renders identically": core_log["reload_identical"],
        "colorbar redrawn (none stale)": core_log["stale_colorbars_after_norm_change"] == [],
        "pixel-identical to the dev environment": report["compare"]["identical_to_dev_env"],
    }
    report["checks"] = checks
    (out / "report.json").write_text(json.dumps(report, indent=1))
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    c = report["compare"]
    print(
        f"QPF vs Cartopy (median of 18): >8 {c['qpf_all_gt8'] * 100:.3f} %, "
        f">32 {c['qpf_all_gt32'] * 100:.3f} %;"
        f" grid (median of 4): >8 {c['grid_all_gt8'] * 100:.2f} %, "
        f">32 {c['grid_all_gt32'] * 100:.3f} %"
    )
    print(
        f"core env: Python {core_log['python']}, {core_log['versions']}, "
        f"packages {report['core_env_packages']}"
    )
    print(f"results → {out.relative_to(ROOT)}")
    if not all(checks.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
