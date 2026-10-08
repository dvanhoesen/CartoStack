# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Prepare the local fixtures used by the Session 01/01b/02 benchmarks.

One-time and network-allowed. Copies the Natural Earth shapefiles already in
the Cartopy cache and the New York county shapefile from ``resources/``,
downloads ``ne_10m_populated_places`` (the only file not cached locally),
copies the SWRCC prepared GeoPackages and logo used by the QPF example
(``resources/slow_example.py``; source directory overridable with
``SWRCC_RESOURCES``), and snapshots every WPC QPF product the example renders.
Everything goes to the gitignored ``benchmarks/data/`` with a
``fixtures.json`` record of sources, sizes, and SHA-256 digests.

The benchmarks read only ``benchmarks/data/`` and never download. Re-running
this script is idempotent: existing files are kept when their digest matches,
and the QPF snapshot is kept once taken (delete ``benchmarks/data/qpf/`` to
take a new one).

    uv run benchmarks/fetch_fixtures.py
"""

from __future__ import annotations

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
DATA = ROOT / "benchmarks" / "data"
NE_DIR = DATA / "shapefiles" / "natural_earth"  # Cartopy's data_dir layout
COUNTY_SRC = ROOT / "resources" / "NYS_Shoreline_Counties"
COUNTY_DST = DATA / "NYS_Shoreline_Counties"

SWRCC_SRC = Path(os.environ.get("SWRCC_RESOURCES", "/Users/danielvanhoesen/swrcc/resources"))
SWRCC_DST = DATA / "swrcc"
SWRCC_FILES = [
    "prepared_gpkg/counties_epsg4326.gpkg",
    "prepared_gpkg/lakes_epsg4326.gpkg",
    "prepared_gpkg/cities_epsg4326.gpkg",
    "prepared_gpkg/nymask_epsg4326.gpkg",
    "prepared_gpkg/states_epsg4326.gpkg",
    "prepared_gpkg/coastlines_epsg4326.gpkg",
    "logos/SWRCC_Logo_Transparent.png",
]
QPF_DIR = DATA / "qpf"

SHAPEFILE_EXTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")

# WPC QPF products in the order resources/slow_example.py renders them.
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

# (category, name) of Natural Earth 10m layers copied from the Cartopy cache.
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
NE_DOWNLOADED = [("cultural", "populated_places")]
NE_URLS = [
    "https://naciscdn.org/naturalearth/10m/{category}/ne_10m_{name}.zip",
    "https://naturalearth.s3.amazonaws.com/10m_{category}/ne_10m_{name}.zip",
]

# SHA-256 recorded in SESSIONS.md → Environment for the user-supplied fixture.
COUNTY_SHA256 = {
    "NYS Counties.shp": "c3fdde4d0c7f6ef3194f6e92940ff82b93c219393e28f25c4b4cdf7c8ae2ed24",
    "NYS Counties.shx": "41d740903605db3223085e6ea0844e301f0f81ff2878acc3d90c8802ddf1e362",
    "NYS Counties.dbf": "f5bdbc93a2f0f64f889cae66e31634b12067dcb5cc41949566cedadd016952b8",
    "NYS Counties.prj": "a02a27b1d1982c8516d83398e85a3c8b1aef1713c13ef4d84d7bde17430c07c4",
    "NYS Counties.cpg": "3ad3031f5503a4404af825262ee8232cc04d4ea6683d42c5dd0a2f2a27ac9824",
    "NYS Counties.qmd": "b29add816be3a45c6f58c18c5f757177b2a96b8e29ae205c0922e5c60e8bf519",
}


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


def copy_counties(entries: list[dict[str, object]]) -> None:
    for fname, expected in COUNTY_SHA256.items():
        src = COUNTY_SRC / fname
        if not src.exists():
            sys.exit(f"missing county fixture: {src}")
        actual = sha256(src)
        if actual != expected:
            sys.exit(f"county fixture hash mismatch for {src}: {actual} != {expected}")
        dst = COUNTY_DST / fname
        copy_file(src, dst)
        entries.append(record(dst, f"resources: {src.relative_to(ROOT)}"))


def copy_swrcc(entries: list[dict[str, object]]) -> None:
    for rel in SWRCC_FILES:
        src = SWRCC_SRC / rel
        if not src.exists():
            sys.exit(f"missing SWRCC resource: {src} (set SWRCC_RESOURCES)")
        dst = SWRCC_DST / rel
        copy_file(src, dst)
        entries.append(record(dst, f"swrcc: {src}"))


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


def main() -> None:
    entries: list[dict[str, object]] = []
    copy_cached_natural_earth(entries)
    copy_counties(entries)
    download_natural_earth(entries)
    copy_swrcc(entries)
    snapshot_qpf(entries)
    manifest = {
        "prepared_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_dir": str(DATA.relative_to(ROOT)),
        "downloads": DOWNLOADS,
        "qpf": QPF,
        "files": entries,
    }
    (DATA / "fixtures.json").write_text(json.dumps(manifest, indent=2) + "\n")
    total = sum(int(e["bytes"]) for e in entries)
    print(f"{len(entries)} fixture files, {total / 1e6:.1f} MB, in {DATA}")


if __name__ == "__main__":
    main()
