# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fresh-environment, source-independence and no-network check of the runtime.

Stages (each in its own process; this orchestrator needs only the standard library and
``uv``):

1. **Author** the example scenes with the ``build`` extra, into a sandbox outside the
   repository: ``examples/author_qpf.py``, ``author_grid.py`` (with its sample inputs) and
   ``author_hazard.py`` (with its synthetic GeoTIFFs). The WPC QPF product shapefiles are
   copied in as runtime inputs, with the runtime examples (``update_qpf.py``,
   ``wpc_qpf.py``, ``update_hazard.py``, ``hazard_data.py``).
2. **Core-only environment**: build the wheel and install it into a new venv inside the
   sandbox with its dependencies only (NumPy, Pillow), then confirm that Matplotlib,
   Cartopy, pyproj, shapely, GeoPandas and pyogrio are not installed there.
3. **Sources removed**: the repository's fixture directory (``benchmarks/data``: Natural
   Earth and the WPC snapshot) is renamed away for the runtime stage and always restored.
   The runtime runs with ``HOME`` set inside the sandbox, so the user's Cartopy Natural
   Earth cache (``~/.local/share/cartopy``) is not reachable either; it is not touched.
4. **Runtime** in the core venv, in isolated mode (``python -I``), from the sandbox: an
   audit hook records every file opened and refuses every network call (``socket.*``,
   ``urllib.Request``). It runs the QPF and hazard update examples (18 + 14 maps), renders
   the grid's sample inputs, changes the grid's colour range (colorbar redraw), adds a
   layer, then saves, reloads and re-renders. It then checks ``sys.modules`` and the opened
   files: anything outside the sandbox, the venv and the Python installation fails.
5. **Compare** (development environment): the same updates rendered by the development
   environment must be pixel-identical.

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
EXAMPLES = ROOT / "examples"
RUNTIME_EXAMPLES = ("update_qpf.py", "wpc_qpf.py", "update_hazard.py", "hazard_data.py")
FORBIDDEN = ("matplotlib", "cartopy", "pyproj", "shapely", "geopandas", "pyogrio", "pandas")

RUNTIME = r"""
import os, sys, json, glob, runpy, contextlib, io
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
log = {}

def example(script, *argv):
    sys.argv = [script, *argv]
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        runpy.run_path(os.path.join(SANDBOX, "examples", script), run_name="__main__")
    return printed.getvalue().strip().splitlines()[-1]

qpf_days = sorted(glob.glob(os.path.join(inputs, "qpf", "day_*")))
log["qpf"] = example("update_qpf.py", os.path.join(SANDBOX, "qpf.cstack"), *qpf_days,
                     "--out", os.path.join(OUT, "qpf"))
tifs = sorted(glob.glob(os.path.join(inputs, "hazard", "*.tif")))
log["hazard"] = example("update_hazard.py", os.path.join(SANDBOX, "hazard"), *tifs,
                        "--out", os.path.join(OUT, "hazard"))

with Scene.load(os.path.join(SANDBOX, "grid.cstack")) as s:
    for path in sorted(glob.glob(os.path.join(inputs, "grid", "*.npy"))):
        name = os.path.basename(path)[:-4]
        s.replace_grid("temperature", np.load(path))
        s.text["subtitle"] = f"Sample input {name}"
        s.save_png(os.path.join(OUT, f"grid_{name}.png"), mode="RGB")
    s.replace_grid("temperature", np.load(path), vmin=-15.0, vmax=40.0)
    log["stale_colorbars_after_norm_change"] = list(s.stale_colorbars)
    s.save_png(os.path.join(OUT, "grid_new_norm.png"), mode="RGB")
    yy, xx = np.mgrid[0:48, 0:48]
    badge = np.zeros((48, 48, 4), np.uint8)
    badge[..., 0], badge[..., 2] = 200, 60
    badge[..., 3] = np.where((yy - 24) ** 2 + (xx - 24) ** 2 < 400, 255, 0)
    s.add_layer("badge", badge, left=1140, top=12)
    s.save_png(os.path.join(OUT, "grid_with_badge.png"), mode="RGB")
    saved = os.path.join(OUT, "grid_updated.cstack")
    s.save(saved)
    again = Scene.load(saved)
    log["reload_identical"] = bool(np.array_equal(again.render(), s.render()))

prefixes = [SANDBOX, os.path.realpath(SANDBOX), sys.prefix, sys.base_prefix,
            os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix), "/dev/",
            # operating-system data, not map sources: the time-zone database (zoneinfo,
            # for the QPF valid times) and the macOS version file (platform.mac_ver)
            "/usr/share/zoneinfo/", "/System/Library/CoreServices/SystemVersion.plist"]
log["pngs"] = len(glob.glob(os.path.join(OUT, "**", "*.png"), recursive=True))
log["files_opened"] = len(opened)
log["outside_allowed"] = sorted(p for p in opened if not any(p.startswith(x) for x in prefixes))
log["network_attempts"] = network
log["forbidden_modules"] = sorted(m for m in sys.modules if m.split(".")[0] in __FORBIDDEN__)
log["python"] = sys.version.split()[0]
import PIL
log["versions"] = {"numpy": np.__version__, "pillow": PIL.__version__,
                   "cartostack": __import__("cartostack").__version__}
log["cartostack_file"] = __import__("cartostack").__file__
print(json.dumps(log))
""".replace("__FORBIDDEN__", repr(FORBIDDEN))

COMPARE = r"""
import json, sys
from pathlib import Path
import numpy as np
from PIL import Image

core, dev, out = (Path(a) for a in sys.argv[1:4])
identical = {}
for png in sorted(core.rglob("*.png")):
    a, b = np.asarray(Image.open(png)), np.asarray(Image.open(dev / png.relative_to(core)))
    identical[str(png.relative_to(core))] = bool(a.shape == b.shape and np.array_equal(a, b))
same = bool(identical) and all(identical.values())
summary = {"pngs": len(identical), "identical_to_dev_env": same}
report = {"identical_to_dev_env": identical, **summary}
(out / "compare.json").write_text(json.dumps(report, indent=1))
print(json.dumps(summary))
"""


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    shown = " ".join(str(c) if len(str(c)) < 120 else "<inline script>" for c in cmd)
    print("$", shown, flush=True)
    return subprocess.run(cmd, text=True, capture_output=True, check=True, **kw)  # type: ignore[call-overload]


def author(sandbox: Path) -> None:
    """Stage 1: the example scenes and their runtime inputs, in the sandbox."""
    build = ["uv", "run", "--extra", "build", "python"]
    inputs = sandbox / "inputs"
    run([*build, "examples/author_qpf.py", str(sandbox / "qpf.cstack")], cwd=ROOT)
    run([*build, "examples/author_grid.py", str(sandbox / "grid.cstack"),
         "--inputs", str(inputs / "grid")], cwd=ROOT)  # fmt: skip
    run([*build, "examples/author_hazard.py", str(sandbox / "hazard"),
         "--inputs", str(inputs / "hazard")], cwd=ROOT)  # fmt: skip
    for day in sorted((DATA / "qpf").glob("day_*")):
        shutil.copytree(day, inputs / "qpf" / day.name, dirs_exist_ok=True)
    (sandbox / "examples").mkdir(exist_ok=True)
    for name in RUNTIME_EXAMPLES:
        shutil.copy2(EXAMPLES / name, sandbox / "examples" / name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sandbox", type=Path, help="working directory outside the repository")
    parser.add_argument("--python", default="3.13", help="Python for the core-only venv")
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sandbox = (args.sandbox or Path(tempfile.mkdtemp(prefix="cartostack-portability-"))).resolve()
    if ROOT in sandbox.parents or sandbox == ROOT:
        sys.exit("the sandbox must be outside the repository")
    sandbox.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"portability-{stamp}"
    out.mkdir(parents=True)
    report: dict[str, object] = {"created_utc": stamp, "sandbox": str(sandbox)}

    # 1. Author the examples and gather their inputs (build extra, fixtures present).
    author(sandbox)

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
        "no files opened outside sandbox/venv/python/OS data": not core_log["outside_allowed"],
        "no network attempts": not core_log["network_attempts"],
        "cartostack imported from the venv": str(venv) in core_log["cartostack_file"],
        "all example maps written (18 QPF + 14 hazard)": core_log["qpf"].startswith("18 products")
        and core_log["hazard"].startswith("14 maps"),
        "reload renders identically": core_log["reload_identical"],
        "colorbar redrawn (none stale)": core_log["stale_colorbars_after_norm_change"] == [],
        "pixel-identical to the dev environment": report["compare"]["identical_to_dev_env"],
    }
    report["checks"] = checks
    (out / "report.json").write_text(json.dumps(report, indent=1))
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(
        f"core env: Python {core_log['python']}, {core_log['versions']}, "
        f"packages {report['core_env_packages']}; {core_log['pngs']} PNGs"
    )
    print(f"results → {out.relative_to(ROOT)}")
    if not all(checks.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
