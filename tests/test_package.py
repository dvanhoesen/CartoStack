"""Package-level guarantees: version, dependency boundary, and import isolation."""

from __future__ import annotations

import importlib.metadata
import json
import socket
import subprocess
import sys

import pytest

import cartostack

EXTRAS = ("matplotlib", "cartopy", "pyproj", "shapely")


def test_version_matches_metadata() -> None:
    assert cartostack.__version__ == importlib.metadata.version("cartostack")


def test_core_requires_only_numpy_and_pillow() -> None:
    requires = importlib.metadata.requires("cartostack") or []
    core = sorted(
        r.split(">")[0].split("=")[0].strip().lower() for r in requires if "extra" not in r
    )
    assert core == ["numpy", "pillow"]


def test_import_does_not_load_rendering_libraries() -> None:
    """``import cartostack`` in a fresh process leaves Matplotlib/Cartopy unimported."""
    code = "import json, sys, cartostack; print(json.dumps(sorted(sys.modules)))"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    loaded = {name.split(".")[0] for name in json.loads(out)}
    assert loaded.isdisjoint(EXTRAS), sorted(loaded & set(EXTRAS))


@pytest.mark.build
def test_build_extra_is_importable_but_not_imported() -> None:
    """With the extra installed, it is importable yet still not pulled in by the runtime."""
    import cartopy
    import matplotlib

    assert matplotlib.__version__
    assert cartopy.__version__
    test_import_does_not_load_rendering_libraries()


def test_network_is_blocked() -> None:
    with pytest.raises(OSError, match="network access is disabled"):
        socket.create_connection(("example.com", 80), timeout=1)


def test_no_package_source_is_gitignored() -> None:
    """Hatchling builds honour .gitignore: an ignored module would be missing from the wheel."""
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    if shutil.which("git") is None or not (root / ".git").exists():
        pytest.skip("not a git checkout")
    files = [str(p.relative_to(root)) for p in (root / "src" / "cartostack").rglob("*")
             if p.is_file() and "__pycache__" not in p.parts]  # fmt: skip
    out = subprocess.run(["git", "check-ignore", "--no-index", *files], cwd=root,
                         capture_output=True, text=True, check=False)  # fmt: skip
    assert out.stdout.strip() == "", f"ignored package files: {out.stdout.split()}"
