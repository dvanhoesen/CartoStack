# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Prepare and verify the local fixtures shared by the benchmarks and the test suite.

Preparing is one-time and network-allowed, and uses public sources only. Copies the
Natural Earth shapefiles already in the Cartopy cache (downloading any that are missing),
downloads ``ne_10m_populated_places`` and ``ne_10m_admin_2_counties``, and snapshots
every WPC QPF product the QPF example renders. Everything goes to the gitignored fixture
directory (``benchmarks/data/``, or ``$CARTOSTACK_FIXTURE_DIR``) with a ``fixtures.json``
record of sources, sizes, and SHA-256 digests.

Benchmarks and tests read only that directory and never download; tests skip
fixture-dependent cases when it is absent (``tests/conftest.py`` imports
``fixture_dir`` and ``verify`` from this file). Re-running is idempotent:
existing files are kept when their digest matches, and the QPF snapshot is kept
once taken (delete ``<fixture dir>/qpf/`` to take a new one).

    uv run scripts/fetch_fixtures.py           # prepare (network)
    uv run scripts/fetch_fixtures.py --check   # verify every file against fixtures.json (offline)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fixture_dir() -> Path:
    """The shared fixture directory: ``$CARTOSTACK_FIXTURE_DIR`` or ``benchmarks/data``."""
    return Path(os.environ.get("CARTOSTACK_FIXTURE_DIR", ROOT / "benchmarks" / "data"))


DATA = fixture_dir()
NE_DIR = DATA / "shapefiles" / "natural_earth"  # Cartopy's data_dir layout
QPF_DIR = DATA / "qpf"

SHAPEFILE_EXTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")

# WPC QPF products, in the order the QPF example renders them.
QPF_URLS = [
    "https://ftp-wpc.ncep.noaa.gov/shapefiles/qpf/{}",
    "https://ftp.wpc.ncep.noaa.gov/shapefiles/qpf/{}",
]
QPF_PRODUCTS = {
    "1": "day1/QPF24hr_Day1_latest.tar",
    "2": "day2/QPF24hr_Day2_latest.tar",
    "3": "day3/QPF24hr_Day3_latest.tar",
    "4": "day47_24hr/QPF24hr_Day4_latest.tar",
    "5": "day47_24hr/QPF24hr_Day5_latest.tar",
    "6": "day47_24hr/QPF24hr_Day6_latest.tar",
    "7": "day47_24hr/QPF24hr_Day7_latest.tar",
    "1-2": "day12/QPF48hr_Day1-2_latest.tar",
    "1-3": "day13/QPF72hr_Day1-3_latest.tar",
    "1-5": "5day/QPF120hr_Day1-5_latest.tar",
    "4-5": "day45/QPF48hr_Day4-5_latest.tar",
    "6-7": "day67/QPF48hr_Day6-7_latest.tar",
    "1-7": "7day/QPF168hr_Day1-7_latest.tar",
    "1_6hr_f00-f06": "day1/QPF6hr_f00-f06_latest.tar",
    "1_6hr_f06-f12": "day1/QPF6hr_f06-f12_latest.tar",
    "1_6hr_f12-f18": "day1/QPF6hr_f12-f18_latest.tar",
    "1_6hr_f18-f24": "day1/QPF6hr_f18-f24_latest.tar",
    "1_6hr_f24-f30": "day1/QPF6hr_f24-f30_latest.tar",
}

# (category, name) of Natural Earth 10m layers copied from the Cartopy cache (or downloaded).
NE_CACHED = [
    ("physical", "ocean"),
    ("physical", "land"),
    ("physical", "lakes"),
    ("physical", "coastline"),
    ("cultural", "admin_1_states_provinces_lakes"),
    ("cultural", "admin_0_boundary_lines_land"),
]

# Natural Earth layers downloaded once. naciscdn is the official CDN; the S3
# bucket is the one Cartopy itself downloads from.
NE_DOWNLOADED = [("cultural", "populated_places"), ("cultural", "admin_2_counties")]
NE_URLS = [
    "https://naciscdn.org/naturalearth/10m/{category}/ne_10m_{name}.zip",
    "https://naturalearth.s3.amazonaws.com/10m_{category}/ne_10m_{name}.zip",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cartopy_cache_dir() -> Path:
    # Mirrors cartopy.config['data_dir'] without importing Cartopy.
    base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / "cartopy" / "shapefiles" / "natural_earth"


def record(path: Path, source: str) -> dict[str, object]:
    return {
        "path": str(path.relative_to(DATA)),
        "source": source,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def copy_file(src: Path, dst: Path) -> None:
    if dst.exists() and sha256(dst) == sha256(src):
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_cached_natural_earth(entries: list[dict[str, object]]) -> None:
    cache = cartopy_cache_dir()
    for category, name in NE_CACHED:
        if not (cache / category / f"ne_10m_{name}.shp").exists():
            NE_DOWNLOADED.append((category, name))  # not cached here: download it instead
            continue
        for ext in SHAPEFILE_EXTS:
            src = cache / category / f"ne_10m_{name}{ext}"
            if not src.exists():
                if ext == ".cpg":
                    continue  # optional member
                sys.exit(f"missing cached Natural Earth file: {src}")
            dst = NE_DIR / category / src.name
            copy_file(src, dst)
            entries.append(record(dst, f"cartopy cache: {src}"))


def download_natural_earth(entries: list[dict[str, object]]) -> None:
    previous = load_previous().get("downloads", {})
    for category, name in NE_DOWNLOADED:
        dst_dir = NE_DIR / category
        stem = f"ne_10m_{name}"
        if (dst_dir / f"{stem}.shp").exists() and stem in previous:
            DOWNLOADS[stem] = previous[stem]
        else:
            DOWNLOADS[stem] = fetch_zip(category, name, dst_dir)
        for ext in SHAPEFILE_EXTS:
            path = dst_dir / f"{stem}{ext}"
            if path.exists():
                entries.append(record(path, f"download: {DOWNLOADS[stem]['url']}"))


def fetch_zip(category: str, name: str, dst_dir: Path) -> dict[str, object]:
    for template in NE_URLS:
        url = template.format(category=category, name=name)
        print(f"downloading {url}")
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                payload = resp.read()
        except OSError as exc:
            print(f"  failed: {exc}")
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for member in zf.namelist():
                if Path(member).suffix.lower() in SHAPEFILE_EXTS:
                    (dst_dir / Path(member).name).write_bytes(zf.read(member))
        return {
            "url": url,
            "zip_bytes": len(payload),
            "zip_sha256": hashlib.sha256(payload).hexdigest(),
            "downloaded_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    sys.exit(f"could not download ne_10m_{name}")


def snapshot_qpf(entries: list[dict[str, object]]) -> None:
    """Download each QPF tarball once and keep its shapefile members."""
    previous = load_previous().get("qpf", {})
    for day, rel in QPF_PRODUCTS.items():
        dst_dir = QPF_DIR / f"day_{day}"
        if any(dst_dir.glob("*.shp")) and day in previous:
            QPF[day] = previous[day]
        else:
            QPF[day] = fetch_tar(rel, dst_dir)
        for path in sorted(dst_dir.iterdir()):
            entries.append(record(path, f"download: {QPF[day]['url']}"))


def fetch_tar(rel: str, dst_dir: Path) -> dict[str, object]:
    for template in QPF_URLS:
        url = template.format(rel)
        print(f"downloading {url}")
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                payload = resp.read()
                last_modified = resp.headers.get("Last-Modified")
        except OSError as exc:
            print(f"  failed: {exc}")
            continue
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        dst_dir.mkdir(parents=True)
        members = []
        with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
            for member in tar.getmembers():
                if member.isfile() and Path(member.name).suffix.lower() in SHAPEFILE_EXTS:
                    data = tar.extractfile(member).read()
                    (dst_dir / Path(member.name).name).write_bytes(data)
                    members.append(Path(member.name).name)
        if not any(m.endswith(".shp") for m in members):
            sys.exit(f"no shapefile in {url}")
        return {
            "url": url,
            "last_modified": last_modified,
            "tar_bytes": len(payload),
            "tar_sha256": hashlib.sha256(payload).hexdigest(),
            "downloaded_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "members": members,
        }
    sys.exit(f"could not download {rel}")


def load_previous() -> dict:
    manifest = DATA / "fixtures.json"
    return json.loads(manifest.read_text()) if manifest.exists() else {}


DOWNLOADS: dict[str, dict[str, object]] = {}
QPF: dict[str, dict[str, object]] = {}


def verify(data_dir: Path | None = None) -> list[str]:
    """Offline check of every file in ``fixtures.json``; returns a list of problems."""
    data_dir = data_dir or fixture_dir()
    manifest_path = data_dir / "fixtures.json"
    if not manifest_path.exists():
        return [f"{manifest_path} missing; run: uv run scripts/fetch_fixtures.py"]
    problems = []
    for entry in json.loads(manifest_path.read_text())["files"]:
        path = data_dir / entry["path"]
        if not path.exists():
            problems.append(f"missing: {path}")
        elif sha256(path) != entry["sha256"]:
            problems.append(f"changed: {path}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify only; never download")
    if parser.parse_args().check:
        problems = verify()
        print("\n".join(problems) or f"all fixtures present and unchanged in {DATA}")
        sys.exit(1 if problems else 0)
    entries: list[dict[str, object]] = []
    copy_cached_natural_earth(entries)
    download_natural_earth(entries)
    snapshot_qpf(entries)
    manifest = {
        "prepared_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_dir": str(DATA.relative_to(ROOT) if DATA.is_relative_to(ROOT) else DATA),
        "downloads": DOWNLOADS,
        "qpf": QPF,
        "files": entries,
    }
    (DATA / "fixtures.json").write_text(json.dumps(manifest, indent=2) + "\n")
    total = sum(int(e["bytes"]) for e in entries)
    print(f"{len(entries)} fixture files, {total / 1e6:.1f} MB, in {DATA}")


if __name__ == "__main__":
    main()
