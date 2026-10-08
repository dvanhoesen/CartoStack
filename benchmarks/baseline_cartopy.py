# /// script
# requires-python = "==3.13.*"
# dependencies = [
#     "cartopy==0.26.0",
#     "matplotlib==3.11.2",
#     "numpy==2.5.3",
#     "pillow==12.3.0",
# ]
# [tool.uv]
# exclude-newer = "2026-10-08T00:00:00Z"
# ///
"""Session 01: conventional Cartopy baseline for the CartoStack reference scene.

Draws the reference scene (SESSIONS.md → "Session 01 workload specification")
the normal way with Matplotlib and Cartopy and measures it:

* cold: fresh processes timing interpreter start, per-package imports, source
  reading, artist construction, per-group drawing, and PNG encoding;
* warm: one process, a warm-up render plus timed repetitions;
* memory: a separate cold run under ``tracemalloc`` (kept out of the timed
  runs because it slows allocation-heavy code).

Fixtures come only from ``benchmarks/data/`` (``fetch_fixtures.py``); network
access is blocked inside the render processes, so a missing file fails instead
of downloading. Results go to ``benchmarks/results/<UTC timestamp>/``.

    uv run benchmarks/baseline_cartopy.py                # full benchmark
    uv run benchmarks/baseline_cartopy.py --variant 2    # other data/title inputs
    uv run benchmarks/baseline_cartopy.py --child render --out x.png   # one render

The scene constants and ``render()`` are importable so Session 02 can produce
full Cartopy references for updated inputs.
"""

# Timestamps first, before any import beyond the two needed to take them.
import time

T0_PERF = time.perf_counter()
T0_WALL = time.time()

import argparse
import hashlib
import io
import json
import platform
import resource
import socket
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmarks" / "data"
RESULTS = ROOT / "benchmarks" / "results"
NE = DATA / "shapefiles" / "natural_earth"
COUNTIES = DATA / "NYS_Shoreline_Counties" / "NYS Counties.shp"
PLACES = NE / "cultural" / "ne_10m_populated_places.shp"

# --- Reference scene (Session 01 workload specification) -------------------

WIDTH_PX, HEIGHT_PX, DPI = 1200, 800, 100
FIGSIZE = (WIDTH_PX / DPI, HEIGHT_PX / DPI)
PROJECTION = {
    "name": "LambertConformal",
    "central_longitude": -76.0,
    "central_latitude": 42.5,
    "standard_parallels": (40.5, 44.5),
}
EXTENT = (-80.5, -71.5, 40.2, 45.3)  # PlateCarree lon0, lon1, lat0, lat1
# Requested rectangles in figure fractions; Cartopy's aspect adjustment
# shrinks the map axes, so the resolved rectangle is recorded after drawing.
AXES_RECT = (0.02, 0.13, 0.96, 0.78)
CBAR_RECT = (0.2, 0.06, 0.6, 0.025)
GRID_SHAPE = (500, 500)  # (ny, nx) cell centres
GRID_LON = (-81.0, -71.0)
GRID_LAT = (40.0, 46.0)
CMAP, VMIN, VMAX, DATA_ALPHA = "coolwarm", -10.0, 35.0, 0.8
FONT_FAMILY = "DejaVu Sans"
TITLE_SIZE, SUBTITLE_SIZE = 18, 12
CITY_MAX_SCALERANK = 7

# Natural Earth 10m layers: (group, category, name, style)
NE_LAYERS = [
    ("below", "physical", "ocean", {"facecolor": "#9ec7e6", "edgecolor": "none", "zorder": 1.0}),
    ("below", "physical", "land", {"facecolor": "#efe9dc", "edgecolor": "none", "zorder": 1.1}),
    ("below", "physical", "lakes", {"facecolor": "#9ec7e6", "edgecolor": "none", "zorder": 1.2}),
    ("above", "cultural", "admin_1_states_provinces_lakes",
     {"facecolor": "none", "edgecolor": "#333333", "linewidth": 0.9, "zorder": 3.1}),
    ("above", "cultural", "admin_0_boundary_lines_land",
     {"facecolor": "none", "edgecolor": "#000000", "linewidth": 1.3, "zorder": 3.2}),
    ("above", "physical", "coastline",
     {"facecolor": "none", "edgecolor": "#1f3b57", "linewidth": 0.7, "zorder": 3.3}),
]
COUNTY_STYLE = {"facecolor": "none", "edgecolor": "#6b6b6b", "linewidth": 0.4, "zorder": 3.0}
DATA_ZORDER = 2.0

GROUPS = ("below", "data", "above", "text_colorbar")
PNG_VARIANTS = (("rgb", 6), ("rgba", 6), ("rgb", 1), ("rgba", 1))
REFERENCE_PNG = ("rgb", 6)  # mode and compress_level of the written image


def make_field(variant: int = 0):
    """Deterministic analytic temperature-like field (float32, °C).

    Returns (lon, lat, values) with 1-D cell-centre coordinates and values of
    shape GRID_SHAPE. ``variant`` shifts the waves and the warm anomaly so
    Session 02 can compare several updated inputs.
    """
    import numpy as np

    ny, nx = GRID_SHAPE
    lon = np.linspace(*GRID_LON, nx)
    lat = np.linspace(*GRID_LAT, ny)
    lon2, lat2 = np.meshgrid(lon, lat)
    phase = 0.7 * variant
    values = (
        22.0
        - 4.0 * (lat2 - 40.0)
        + 6.0 * np.sin(np.radians(lon2 * 25.0) + phase) * np.cos(np.radians(lat2 * 30.0))
        + 10.0 * np.exp(-((lon2 + 74.5 - 0.5 * variant) ** 2 + (lat2 - 41.0) ** 2) / 1.5)
    )
    return lon, lat, values.astype(np.float32)


def title_text(variant: int = 0) -> tuple[str, str]:
    hour = (12 + 6 * variant) % 24
    day = 8 + (12 + 6 * variant) // 24
    return (
        "2-m Temperature (synthetic)",
        f"Valid 2026-10-{day:02d} {hour:02d}:00 UTC  ·  CartoStack baseline variant {variant}",
    )


# --- Instrumentation ---------------------------------------------------------


def block_network() -> None:
    """Make any socket connection fail so Cartopy cannot download fixtures."""

    def refuse(*args, **kwargs):
        raise OSError("network access is disabled in the benchmark; run fetch_fixtures.py")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    socket.create_connection = refuse  # type: ignore[assignment]


class DrawTimer:
    """Accumulates draw time per group by wrapping artist instances' ``draw``.

    Matplotlib calls ``artist.draw(renderer)`` on each child, so an instance
    attribute takes precedence over the class method. Natural Earth source
    reads happen lazily inside feature drawing; they are attributed to the
    group being drawn and reported separately as ``source_read``.
    """

    def __init__(self, groups: tuple[str, ...] = GROUPS) -> None:
        self.draw = dict.fromkeys(groups, 0.0)
        self.read = dict.fromkeys(groups, 0.0)
        self.current: str | None = None

    def wrap(self, artist, group: str) -> None:
        inner = artist.draw

        def draw(renderer, *args, **kwargs):
            previous, self.current = self.current, group
            t = time.perf_counter()
            try:
                return inner(renderer, *args, **kwargs)
            finally:
                self.draw[group] += time.perf_counter() - t
                self.current = previous

        artist.draw = draw


TIMER_STATE: dict[str, DrawTimer] = {}


def install_read_hook() -> None:
    """Time NaturalEarthFeature.geometries(), which parses the shapefile on
    first use per process, attributing it to the group being drawn."""
    import cartopy.feature as cfeature

    original = cfeature.NaturalEarthFeature.geometries
    if getattr(original, "_timed", False):
        return

    def geometries(feature_self):
        t = time.perf_counter()
        try:
            return original(feature_self)
        finally:
            timer = TIMER_STATE["timer"]
            group = timer.current or "other"
            timer.read[group] = timer.read.get(group, 0.0) + time.perf_counter() - t

    geometries._timed = True  # type: ignore[attr-defined]
    cfeature.NaturalEarthFeature.geometries = geometries



def import_stack() -> dict[str, float]:
    """Import the rendering stack, timing each package (in this order)."""
    times = {}
    t = time.perf_counter()
    import numpy  # noqa: F401

    times["numpy"] = time.perf_counter() - t
    t = time.perf_counter()
    import PIL.Image  # noqa: F401

    times["PIL"] = time.perf_counter() - t
    t = time.perf_counter()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot

    times["matplotlib"] = time.perf_counter() - t
    t = time.perf_counter()
    import cartopy
    import cartopy.crs
    import cartopy.feature
    import cartopy.io.shapereader

    times["cartopy"] = time.perf_counter() - t

    # Point Cartopy at the prepared fixtures only.
    cartopy.config["data_dir"] = DATA
    cartopy.config["pre_existing_data_dir"] = DATA
    matplotlib.rcParams["font.family"] = FONT_FAMILY
    return times


def load_sources():
    """Read the sources a script loads explicitly (counties, city points).

    Natural Earth features are read lazily by Cartopy during drawing and are
    timed there (``source_read``).
    """
    import cartopy.io.shapereader as shpreader

    counties = tuple(shpreader.Reader(str(COUNTIES)).geometries())
    lon0, lon1, lat0, lat1 = EXTENT
    cities = []
    for rec in shpreader.Reader(str(PLACES)).records():
        a = rec.attributes
        if (
            a["SCALERANK"] <= CITY_MAX_SCALERANK
            and lon0 < a["LONGITUDE"] < lon1
            and lat0 < a["LATITUDE"] < lat1
        ):
            cities.append((a["NAME"], float(a["LONGITUDE"]), float(a["LATITUDE"])))
    cities.sort(key=lambda c: (c[1], c[2]))
    return counties, cities


def render(sources, variant: int = 0, out: Path | None = None) -> dict:
    """Draw the full scene once; return per-phase timings and geometry."""
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib.pyplot as plt
    import numpy as np
    from PIL import Image

    counties, cities = sources
    timer = DrawTimer()
    TIMER_STATE["timer"] = timer
    install_read_hook()
    phases: dict[str, float] = {}
    pc = ccrs.PlateCarree()

    t = time.perf_counter()
    proj = ccrs.LambertConformal(
        central_longitude=PROJECTION["central_longitude"],
        central_latitude=PROJECTION["central_latitude"],
        standard_parallels=PROJECTION["standard_parallels"],
    )
    fig = plt.figure(figsize=FIGSIZE, dpi=DPI, facecolor="white")
    ax = fig.add_axes(AXES_RECT, projection=proj)
    ax.set_extent(EXTENT, crs=pc)
    phases["setup"] = time.perf_counter() - t

    t = time.perf_counter()
    for group, category, name, style in NE_LAYERS:
        if group == "below":
            artist = ax.add_feature(cfeature.NaturalEarthFeature(category, name, "10m"), **style)
            timer.wrap(artist, "below")
    phases["build_below"] = time.perf_counter() - t

    t = time.perf_counter()
    lon, lat, values = make_field(variant)
    phases["field"] = time.perf_counter() - t
    t = time.perf_counter()
    mesh = ax.pcolormesh(
        lon, lat, values, transform=pc, cmap=CMAP, vmin=VMIN, vmax=VMAX,
        alpha=DATA_ALPHA, shading="nearest", zorder=DATA_ZORDER,
    )
    timer.wrap(mesh, "data")
    phases["build_data"] = time.perf_counter() - t

    t = time.perf_counter()
    county_artist = ax.add_feature(cfeature.ShapelyFeature(counties, pc), **COUNTY_STYLE)
    timer.wrap(county_artist, "above")
    for group, category, name, style in NE_LAYERS:
        if group == "above":
            artist = ax.add_feature(cfeature.NaturalEarthFeature(category, name, "10m"), **style)
            timer.wrap(artist, "above")
    _, xs, ys = zip(*cities)
    (markers,) = ax.plot(xs, ys, "o", transform=pc, ms=3.5, mfc="#111111", mec="white",
                         mew=0.6, zorder=4.0, clip_on=True)
    timer.wrap(markers, "above")
    for name, x, y in cities:
        label = ax.text(x + 0.06, y + 0.04, name, transform=pc, fontsize=8, color="#111111",
                        ha="left", va="bottom", zorder=4.1, clip_on=True)
        timer.wrap(label, "above")
    phases["build_above"] = time.perf_counter() - t

    t = time.perf_counter()
    title, subtitle = title_text(variant)
    t1 = fig.text(0.5, 0.975, title, ha="center", va="top", fontsize=TITLE_SIZE)
    t2 = fig.text(0.5, 0.932, subtitle, ha="center", va="top", fontsize=SUBTITLE_SIZE,
                  color="#444444")
    cax = fig.add_axes(CBAR_RECT)
    cbar = fig.colorbar(mesh, cax=cax, orientation="horizontal", extend="both")
    cbar.set_label("°C", fontsize=10)
    cbar.ax.tick_params(labelsize=9)
    for artist in (t1, t2, cax):
        timer.wrap(artist, "text_colorbar")
    phases["build_text_colorbar"] = time.perf_counter() - t

    t = time.perf_counter()
    fig.canvas.draw()
    phases["draw_total"] = time.perf_counter() - t
    for group in GROUPS:
        phases[f"draw_{group}"] = timer.draw[group]
    phases["draw_overhead"] = phases["draw_total"] - sum(timer.draw.values())
    phases["source_read_natural_earth"] = sum(timer.read.values())
    for group, value in timer.read.items():
        if value:
            phases[f"source_read_in_draw_{group}"] = value

    t = time.perf_counter()
    rgba = np.asarray(fig.canvas.buffer_rgba()).copy()
    phases["buffer_copy"] = time.perf_counter() - t

    encoded = {}
    for mode, level in PNG_VARIANTS:
        t = time.perf_counter()
        img = Image.fromarray(rgba, "RGBA")
        if mode == "rgb":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=level)
        key = f"encode_{mode}_cl{level}"
        phases[key] = time.perf_counter() - t
        encoded[key] = buf.getvalue()
    ref_key = "encode_{}_cl{}".format(*REFERENCE_PNG)
    if out is not None:
        t = time.perf_counter()
        out.write_bytes(encoded[ref_key])
        phases["write_file"] = time.perf_counter() - t

    geometry = scene_geometry(fig, ax, cax)
    t = time.perf_counter()
    plt.close(fig)
    phases["close"] = time.perf_counter() - t

    return {
        "phases_s": phases,
        "png_bytes": {k: len(v) for k, v in encoded.items()},
        "pixel_sha256": hashlib.sha256(rgba.tobytes()).hexdigest(),
        "png_sha256": hashlib.sha256(encoded[ref_key]).hexdigest(),
        "rgba_shape": list(rgba.shape),
        "alpha_min": int(rgba[..., 3].min()),
        "geometry": geometry,
        "n_cities": len(cities),
    }


def scene_geometry(fig, ax, cax) -> dict:
    """Resolved geometry after drawing: what Session 02 must reproduce."""
    width, height = fig.canvas.get_width_height()

    def px(bbox):
        # Display coords have a bottom-left origin; record top-left too.
        return {
            "x0": bbox.x0, "x1": bbox.x1, "y0_bottom": bbox.y0, "y1_bottom": bbox.y1,
            "left": bbox.x0, "top": height - bbox.y1,
            "width": bbox.width, "height": bbox.height,
        }

    return {
        "canvas_px": [width, height],
        "dpi": fig.dpi,
        "axes_requested_fraction": list(AXES_RECT),
        "axes_resolved_fraction": list(ax.get_position().bounds),
        "axes_resolved_px": px(ax.bbox),
        "colorbar_resolved_fraction": list(cax.get_position().bounds),
        "colorbar_resolved_px": px(cax.bbox),
        "projection_proj4": ax.projection.proj4_init,
        "projected_extent_xxyy": list(ax.get_extent()),
        "geographic_extent_requested": list(EXTENT),
    }


def versions() -> dict:
    import cartopy
    import matplotlib
    import numpy
    import PIL
    import pyproj
    import shapefile
    import shapely
    from matplotlib import font_manager, ft2font
    from PIL import features

    font_path = Path(font_manager.findfont(FONT_FAMILY, fallback_to_default=False))
    return {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "numpy": numpy.__version__,
        "matplotlib": matplotlib.__version__,
        "cartopy": cartopy.__version__,
        "pillow": PIL.__version__,
        "pyproj": pyproj.__version__,
        "proj": pyproj.proj_version_str,
        "shapely": shapely.__version__,
        "geos": shapely.geos_version_string,
        "pyshp": shapefile.__version__,
        "freetype": ft2font.__freetype_version__,
        "pillow_zlib": features.version("zlib"),
        "font_path": str(font_path),
        "font_sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
    }


def peak_rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss if sys.platform == "darwin" else rss * 1024  # Linux reports KiB


# --- Child processes ---------------------------------------------------------


def child_render(args) -> dict:
    """One cold, cron-style run: imports, sources, render, encode, write."""
    t_main = time.perf_counter()
    block_network()
    if args.tracemalloc:
        import tracemalloc

        tracemalloc.start()
    imports = import_stack()
    t = time.perf_counter()
    sources = load_sources()
    source_s = time.perf_counter() - t
    result = render(sources, args.variant, Path(args.out) if args.out else None)
    result["imports_s"] = imports
    result["load_sources_s"] = source_s
    result["pre_main_s"] = t_main - T0_PERF  # stdlib imports in this script
    result["script_start_wall"] = T0_WALL
    result["end_to_end_in_process_s"] = time.perf_counter() - T0_PERF
    if args.tracemalloc:
        import tracemalloc

        result["tracemalloc_peak_bytes"] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    result["peak_rss_bytes"] = peak_rss_bytes()
    result["modules_loaded"] = len(sys.modules)
    return result


def child_warm(args) -> dict:
    """Warm in-process repetitions: imports and sources paid once."""
    block_network()
    imports = import_stack()
    t = time.perf_counter()
    sources = load_sources()
    source_s = time.perf_counter() - t
    reps = []
    for i in range(args.reps + 1):  # first is the warm-up
        t = time.perf_counter()
        r = render(sources, args.variant)
        r["total_s"] = time.perf_counter() - t
        r["warmup"] = i == 0
        reps.append(r)
    return {
        "imports_s": imports,
        "load_sources_s": source_s,
        "reps": reps,
        "peak_rss_bytes": peak_rss_bytes(),
        "versions": versions(),
    }


def run_child(argv: list[str]) -> tuple[dict, float]:
    t_spawn = time.time()
    t = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, __file__, *argv], capture_output=True, text=True, check=False
    )
    wall = time.perf_counter() - t
    if proc.returncode != 0:
        sys.exit(f"child {argv} failed:\n{proc.stderr}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    if "script_start_wall" in result:
        result["interpreter_start_s"] = result["script_start_wall"] - t_spawn
    result["process_wall_s"] = wall
    return result, wall


# --- Orchestrator ------------------------------------------------------------


def check_fixtures() -> dict:
    manifest_path = DATA / "fixtures.json"
    if not manifest_path.exists():
        sys.exit("benchmarks/data/fixtures.json missing; run: uv run benchmarks/fetch_fixtures.py")
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        path = DATA / entry["path"]
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            sys.exit(f"fixture missing or changed: {path}; re-run fetch_fixtures.py")
    return manifest


def summarize(values: list[float]) -> dict:
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "n": len(values),
    }


def phase_summary(runs: list[dict]) -> dict:
    keys = runs[0]["phases_s"].keys()
    return {k: summarize([r["phases_s"][k] for r in runs]) for k in keys}


def orchestrate(args) -> None:
    fixtures = check_fixtures()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    outdir = RESULTS / stamp
    outdir.mkdir(parents=True)
    variant = ["--variant", str(args.variant)]

    # Interpreter start-up alone.
    bare = []
    for _ in range(args.cold_runs):
        t = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        bare.append(time.perf_counter() - t)

    print("priming run (bytecode and OS file caches; not recorded)")
    run_child(["--child", "render", *variant])

    cold = []
    for i in range(args.cold_runs):
        out = outdir / ("baseline.png" if i == 0 else f"cold_{i}.png")
        result, wall = run_child(["--child", "render", "--out", str(out), *variant])
        cold.append(result)
        print(f"cold {i}: {wall:.3f} s wall, draw {result['phases_s']['draw_total']:.3f} s")
    for i in range(1, args.cold_runs):
        (outdir / f"cold_{i}.png").unlink()

    print("tracemalloc run")
    memory, _ = run_child(["--child", "render", "--tracemalloc", *variant])

    print(f"warm run: 1 warm-up + {args.warm_reps} repetitions")
    warm, _ = run_child(["--child", "warm", "--reps", str(args.warm_reps), *variant])
    timed = [r for r in warm["reps"] if not r["warmup"]]

    all_runs = cold + warm["reps"] + [memory]
    pixel_hashes = sorted({r["pixel_sha256"] for r in all_runs})
    png_hashes = sorted({r["png_sha256"] for r in all_runs})
    shapes = sorted({tuple(r["rgba_shape"]) for r in all_runs})
    geometries = {json.dumps(r["geometry"], sort_keys=True) for r in all_runs}

    results = {
        "created_utc": stamp,
        "variant": args.variant,
        "title": title_text(args.variant),
        "workload": {
            "canvas_px": [WIDTH_PX, HEIGHT_PX],
            "dpi": DPI,
            "projection": PROJECTION,
            "extent": EXTENT,
            "axes_rect_requested": AXES_RECT,
            "colorbar_rect_requested": CBAR_RECT,
            "grid_shape": GRID_SHAPE,
            "grid_lon": GRID_LON,
            "grid_lat": GRID_LAT,
            "cmap": CMAP,
            "vmin": VMIN,
            "vmax": VMAX,
            "data_alpha": DATA_ALPHA,
            "font_family": FONT_FAMILY,
            "natural_earth_layers": [(g, c, n) for g, c, n, _ in NE_LAYERS],
            "counties": str(COUNTIES.relative_to(ROOT)),
            "city_max_scalerank": CITY_MAX_SCALERANK,
            "n_cities": cold[0]["n_cities"],
            "reference_png": {"mode": REFERENCE_PNG[0], "compress_level": REFERENCE_PNG[1]},
        },
        "versions": warm["versions"],
        "uv_version": subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True).stdout.strip(),
        "fixtures_prepared_utc": fixtures["prepared_utc"],
        "caches": {
            "os_file_cache": "warm (priming run read every fixture; no purge between runs)",
            "bytecode": "warm (priming run)",
            "network": "blocked in render processes",
        },
        "resolved_geometry": cold[0]["geometry"],
        "reproducibility": {
            "runs_compared": len(all_runs),
            "distinct_pixel_sha256": pixel_hashes,
            "distinct_png_sha256": png_hashes,
            "distinct_rgba_shapes": [list(s) for s in shapes],
            "distinct_geometries": len(geometries),
            "identical": len(pixel_hashes) == 1 and len(png_hashes) == 1 and len(geometries) == 1,
        },
        "png_bytes": cold[0]["png_bytes"],
        "cold": {
            "bare_interpreter_s": summarize(bare),
            "process_wall_s": summarize([r["process_wall_s"] for r in cold]),
            "interpreter_start_s": summarize([r["interpreter_start_s"] for r in cold]),
            "end_to_end_in_process_s": summarize([r["end_to_end_in_process_s"] for r in cold]),
            "imports_s": {k: summarize([r["imports_s"][k] for r in cold]) for k in cold[0]["imports_s"]},
            "imports_total_s": summarize([sum(r["imports_s"].values()) for r in cold]),
            "load_sources_s": summarize([r["load_sources_s"] for r in cold]),
            "phases_s": phase_summary(cold),
            "peak_rss_bytes": summarize([r["peak_rss_bytes"] for r in cold]),
            "modules_loaded": cold[0]["modules_loaded"],
            "runs": cold,
        },
        "memory": {
            "tracemalloc_peak_bytes": memory["tracemalloc_peak_bytes"],
            "peak_rss_bytes_with_tracemalloc": memory["peak_rss_bytes"],
        },
        "warm": {
            "reps": len(timed),
            "total_s": summarize([r["total_s"] for r in timed]),
            "warmup_total_s": warm["reps"][0]["total_s"],
            "phases_s": phase_summary(timed),
            "peak_rss_bytes": warm["peak_rss_bytes"],
            "cartopy_caches": (
                "Warm repetitions reuse cartopy.feature._NATURAL_EARTH_GEOM_CACHE (parsed "
                "Natural Earth geometries), FeatureArtist's projected-path cache keyed by "
                "geometry id and projection, and the county/city sources loaded once per "
                "process; also Matplotlib font/text layout caches."
            ),
            "runs": warm["reps"],
        },
    }
    (outdir / "results.json").write_text(json.dumps(results, indent=2, default=list) + "\n")
    print_summary(results, outdir)


def print_summary(r: dict, outdir: Path) -> None:
    c, w = r["cold"], r["warm"]
    ms = lambda s: f"{s * 1000:8.1f} ms"
    print(f"\nresults: {outdir.relative_to(ROOT)}")
    print(f"reproducible across {r['reproducibility']['runs_compared']} renders: "
          f"{r['reproducibility']['identical']}")
    g = r["resolved_geometry"]["axes_resolved_px"]
    print(f"canvas {r['resolved_geometry']['canvas_px']}, axes px left={g['left']:.3f} "
          f"top={g['top']:.3f} w={g['width']:.3f} h={g['height']:.3f}")
    print("cold (median):")
    print(f"  process wall          {ms(c['process_wall_s']['median'])}")
    print(f"  bare interpreter      {ms(c['bare_interpreter_s']['median'])}")
    print(f"  interpreter start     {ms(c['interpreter_start_s']['median'])}")
    for k, v in c["imports_s"].items():
        print(f"  import {k:<14} {ms(v['median'])}")
    print(f"  load counties/cities  {ms(c['load_sources_s']['median'])}")
    for k, v in c["phases_s"].items():
        print(f"  {k:<28} {ms(v['median'])}")
    print(f"  peak RSS              {c['peak_rss_bytes']['median'] / 2**20:8.1f} MiB")
    print(f"warm ({w['reps']} reps): median {ms(w['total_s']['median'])}, "
          f"min {ms(w['total_s']['min'])}")
    for k, v in w["phases_s"].items():
        print(f"  {k:<28} {ms(v['median'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child", choices=["render", "warm"], help=argparse.SUPPRESS)
    parser.add_argument("--out", help="PNG path for --child render")
    parser.add_argument("--variant", type=int, default=0, help="data/title input variant")
    parser.add_argument("--tracemalloc", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reps", type=int, default=10, help=argparse.SUPPRESS)
    parser.add_argument("--cold-runs", type=int, default=7)
    parser.add_argument("--warm-reps", type=int, default=10)
    args = parser.parse_args()
    if args.child == "render":
        print(json.dumps(child_render(args), default=list))
    elif args.child == "warm":
        print(json.dumps(child_warm(args), default=list))
    else:
        orchestrate(args)


if __name__ == "__main__":
    main()
