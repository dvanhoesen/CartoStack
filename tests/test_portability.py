"""Source independence and no network on the runtime path, in a fresh process (Session 13).

The full procedure (wheel in a core-only venv, fixtures removed) is
``scripts/check_portability.py``; this test keeps the same guarantees checked in every
test run: loading and updating a saved scene opens no file outside the scene, the output
and the Python installation, and attempts no network access.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from helpers import full_scene

SCRIPT = r"""
import json, os, sys
scene_path, out = sys.argv[1], sys.argv[2]
control = sys.argv[3] if len(sys.argv) > 3 else None
opened, network = set(), []

def audit(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        opened.add(os.path.realpath(os.fsdecode(args[0])))
    elif event.startswith(("socket.", "urllib.Request")) and event != "socket.__new__":
        network.append(event)
        raise OSError("network blocked")

sys.addaudithook(audit)
import numpy as np
from cartostack import Scene
if control:
    open(control).read(1)
    import socket
    try:
        socket.create_connection(("example.com", 80), timeout=1)
    except OSError:
        pass
with Scene.load(scene_path) as s:
    s.replace_grid("temperature", np.linspace(-15, 40, 108, dtype=np.float32).reshape(12, 9))
    ring = [(-79.0, 41.0), (-73.0, 41.0), (-73.0, 44.5), (-79.0, 44.5)]
    s.replace_polygons("qpf", [[ring]], [0.05])
    s.add_layer("logo", np.full((4, 4, 4), 255, np.uint8), left=1, top=1)
    s.save_png(out)
    s.save(out + ".cstack")
roots = (sys.prefix, sys.base_prefix, os.path.dirname(scene_path), os.path.dirname(out))
allowed = [os.path.realpath(p) for p in roots]
allowed += [os.path.realpath(p) for p in sys.path if p and os.path.isdir(p)]
outside = sorted(
    p for p in opened if not any(p.startswith(a) for a in allowed) and not p.startswith("/dev/")
)
print(json.dumps({"outside": outside, "network": network}))
"""


def run(tmp_path: Path, control: str | None = None) -> dict[str, list[str]]:
    scene = full_scene()
    path = tmp_path / "scene" / "s.cstack"
    path.parent.mkdir()
    scene.save(path)
    out = tmp_path / "out"
    out.mkdir()
    args = [sys.executable, "-c", SCRIPT, str(path), str(out / "o.png")] + (
        [control] if control else []
    )
    res = subprocess.run(args, capture_output=True, text=True, check=True, cwd=tmp_path)
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_runtime_reads_nothing_but_the_scene_and_never_uses_the_network(tmp_path: Path) -> None:
    log = run(tmp_path)
    assert log == {"outside": [], "network": []}
    assert (tmp_path / "out" / "o.png").stat().st_size > 0


def test_the_check_detects_outside_reads_and_network_use(tmp_path: Path) -> None:
    """Negative control: the audit hook really sees a stray read and a connection attempt."""
    stray = Path(__file__).resolve().parent.parent / "README.md"
    log = run(tmp_path, str(stray))
    assert log["outside"] == [str(stray)]
    assert log["network"]
    assert np.isfinite(0.0)
