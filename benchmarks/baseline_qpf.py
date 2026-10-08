# /// script
# requires-python = "==3.13.*"
# dependencies = [
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
# ///
"""Session 01b: Cartopy baseline for the WPC QPF workload (resources/slow_example.py).

The drawing code is ported from ``resources/slow_example.py`` without changing
what it draws. Downloading and extracting the WPC tarballs is replaced by
reading the snapshot in ``benchmarks/data/qpf/`` (``fetch_fixtures.py``), and the
SWRCC GeoPackages and logo come from ``benchmarks/data/swrcc/``. Network access
is blocked in the render processes.

Measured (fresh processes; results in ``benchmarks/results/qpf-<UTC timestamp>/``):

* product: one cold product per process (cron job rendering a single map);
* loop: every snapshot product in one process, as the example runs today —
  imports and static GeoPackages once, then per product: QPF read, clip,
  artist build, draw by group, and ``savefig``.

    uv run benchmarks/baseline_qpf.py
    uv run benchmarks/baseline_qpf.py --child product --day 1-3 --out x.png

Constants and ``render_product()`` are importable for Session 02.
"""

import time

T0_PERF = time.perf_counter()
T0_WALL = time.time()

import argparse
import hashlib
import io
import json
import shutil
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from baseline_cartopy import DrawTimer, block_network, peak_rss_bytes, summarize

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmarks" / "data"
RESULTS = ROOT / "benchmarks" / "results"
QPF_DIR = DATA / "qpf"
GPKG_DIR = DATA / "swrcc" / "prepared_gpkg"
LOGO = DATA / "swrcc" / "logos" / "SWRCC_Logo_Transparent.png"
PRODUCTION_IMAGES = Path("/Users/danielvanhoesen/swrcc/internal/qpf/images")

# --- From resources/slow_example.py -----------------------------------------

MYREGION = "NYS"
GEOBOUNDDICT = {"NYS": [40.3, -80.0, 45.6, -71.7]}
LAT_MIN, LON_MIN, LAT_MAX, LON_MAX = GEOBOUNDDICT[MYREGION]
CENTER_LAT = (LAT_MIN + LAT_MAX) / 2.0
CENTER_LON = (LON_MIN + LON_MAX) / 2.0

CITYLABELXOFFSET = {
    "NYS": {"Manhattan": -0.86, "Buffalo": -0.62, "Default": 0.07},
}

# (lower, upper, colour, zorder)
QPF_RANGES = [
    (0.00, 0.01, "white", 10),
    (0.01, 0.10, "#7fff00", 11),
    (0.10, 0.25, "#00cd00", 12),
    (0.25, 0.50, "#008b00", 13),
    (0.50, 0.75, "#104e8b", 14),
    (0.75, 1.00, "#1e90ff", 15),
    (1.00, 1.25, "#00b2ee", 16),
    (1.25, 1.50, "#00eeee", 17),
    (1.50, 1.75, "#8968cd", 18),
    (1.75, 2.00, "#912cee", 19),
    (2.00, 2.50, "#8b008b", 20),
    (2.50, 3.00, "#8b0000", 21),
    (3.00, 4.00, "#cd0000", 22),
    (4.00, 5.00, "#ee4000", 23),
    (5.00, 7.00, "#ff7f00", 24),
    (7.00, 10.0, "#cd8500", 25),
    (10.0, 15.0, "#ffd700", 26),
]
TITLE = "Expected Liquid-Equivalent Precipitation"
FIGSIZE = (10, 8)
SAVE_DPI = 300
LEGEND_MAX_UPPER = 15.0

# Product keys in the example's order.
DAYS = [
    "1", "2", "3", "4", "5", "6", "7", "1-2", "1-3", "1-5", "4-5", "6-7", "1-7",
    "1_6hr_f00-f06", "1_6hr_f06-f12", "1_6hr_f12-f18", "1_6hr_f18-f24", "1_6hr_f24-f30",
]
STATIC_GPKG = ["counties", "lakes", "cities", "nymask", "states", "coastlines"]

# Draw groups: what Session 02 stores as layers.
GROUPS = ("below", "data", "above", "decorations", "subtitle")
PNG_VARIANTS = (("rgba", 6), ("rgb", 6), ("rgba", 1), ("rgb", 1))


def import_stack() -> dict[str, float]:
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
    import matplotlib.patches
    import matplotlib.patheffects
    import matplotlib.pyplot
    from matplotlib.offsetbox import AnnotationBbox, OffsetImage  # noqa: F401

    times["matplotlib"] = time.perf_counter() - t
    t = time.perf_counter()
    import geopandas  # noqa: F401
    import pyogrio  # noqa: F401
    import shapely.geometry  # noqa: F401

    times["geopandas"] = time.perf_counter() - t
    t = time.perf_counter()
    import cartopy.crs  # noqa: F401

    times["cartopy"] = time.perf_counter() - t
    t = time.perf_counter()
    import pytz  # noqa: F401

    times["pytz"] = time.perf_counter() - t
    return times


def load_static() -> tuple[dict, dict[str, float]]:
    """Load logos and GeoPackages once, as the example does before its loop."""
    import geopandas as gpd
    from PIL import Image

    times = {}
    static = {}
    t = time.perf_counter()
    static["logo"] = Image.open(LOGO)
    times["logo"] = time.perf_counter() - t
    for name in STATIC_GPKG:
        t = time.perf_counter()
        static[name] = gpd.read_file(GPKG_DIR / f"{name}_epsg4326.gpkg")
        times[name] = time.perf_counter() - t
    states = static["states"]
    static["state_limit"] = states[states["NAME"] != "New York"]
    return static, times


def product_shapefile(day: str) -> Path:
    shapefiles = sorted((QPF_DIR / f"day_{day}").glob("*.shp"))
    if not shapefiles:
        sys.exit(f"no QPF shapefile for day {day}; run fetch_fixtures.py")
    return shapefiles[0]


def parse_valid_time(side):
    from datetime import datetime as dt_cls

    import pytz

    side = side.strip()
    hour_str, date_str = side.split()  # '12Z', '12/10/25'
    z_hour = int(hour_str[:-1])
    dt = dt_cls.strptime(date_str, "%m/%d/%y").replace(hour=z_hour)  # noqa: DTZ007 (as example)
    return pytz.utc.localize(dt).astimezone(pytz.timezone("America/New_York"))


def read_product(day: str) -> dict:
    """Read one QPF product and derive its subtitle (example lines 226-251)."""
    from datetime import datetime as dt_cls

    import geopandas as gpd
    import pytz

    eastern, utc = pytz.timezone("America/New_York"), pytz.utc
    qpf_data = gpd.read_file(product_shapefile(day)).to_crs(epsg=4326)
    first_row = qpf_data.iloc[0]
    product = first_row.get("PRODUCT", "Unknown Product")
    valid_time = first_row.get("VALID_TIME", "")
    issue_time = first_row.get("ISSUE_TIME", "")
    issue_dt = utc.localize(dt_cls.strptime(issue_time, "%Y-%m-%d %H:%M:%S"))  # noqa: DTZ007
    issue_str = issue_dt.astimezone(eastern).strftime("%-I:%M %p %b %-d")
    left_str, right_str = valid_time.split(" - ")
    start_est = parse_valid_time(left_str)
    end_est = parse_valid_time(right_str)
    subtitle = (
        f"{start_est.strftime('%-I:%M %p %b %-d')} - "
        f"{end_est.strftime('%-I:%M %p %b %-d')} (Issued: {issue_str})"
    )
    qpf_data = qpf_data.sort_values("QPF", ascending=True, kind="mergesort")
    return {
        "day": day,
        "data": qpf_data,
        "product": product,
        "valid_time": valid_time,
        "issue_time": issue_time,
        "subtitle": subtitle,
    }


def render_product(static: dict, prod: dict) -> dict:
    """Draw and save one product exactly as the example does (lines 253-411).

    Returns phase timings, the PNG bytes from ``savefig``, and geometry.
    """
    import cartopy.crs as ccrs
    import matplotlib.patches as mpatches
    import matplotlib.patheffects as path_effects
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.offsetbox import AnnotationBbox, OffsetImage
    from shapely.geometry import box

    timer = DrawTimer(GROUPS)
    phases: dict[str, float] = {}
    extent_poly = box(LON_MIN - 5, LAT_MIN - 5, LON_MAX + 5, LAT_MAX + 5)

    def clip_geom(g):
        if g is None or g.is_empty:
            return None
        g2 = g.intersection(extent_poly)
        return None if g2.is_empty else g2

    qpf_data = prod["data"].copy()

    t = time.perf_counter()
    fig = plt.figure(figsize=FIGSIZE)
    ax = plt.subplot(
        111, projection=ccrs.LambertConformal(central_longitude=CENTER_LON,
                                              central_latitude=CENTER_LAT)
    )
    ax.set_extent((LON_MIN, LON_MAX, LAT_MIN, LAT_MAX), crs=ccrs.PlateCarree())
    phases["setup"] = time.perf_counter() - t

    t = time.perf_counter()
    qpf_data["geometry"] = qpf_data.geometry.apply(clip_geom)
    qpf_data = qpf_data[qpf_data.geometry.notna()]
    phases["clip"] = time.perf_counter() - t

    t = time.perf_counter()
    n_polygons = n_artists = 0
    for _, row in qpf_data.iterrows():
        geom = row.geometry
        qpf_value = row.get("QPF", None)
        if qpf_value is None:
            continue
        qpf_value = round(qpf_value, 2)
        color = "gray"
        layer_zorder = 10
        for lower, upper, col, zorder in QPF_RANGES:
            if lower <= qpf_value < upper:
                color = col
                layer_zorder = zorder
                break
        if geom.geom_type == "Polygon":
            geoms = [geom]
        elif geom.geom_type == "MultiPolygon":
            geoms = list(geom.geoms)
        else:
            continue
        artist = ax.add_geometries(geoms, crs=ccrs.PlateCarree(), facecolor=color,
                                   edgecolor="none", linewidth=0.5, zorder=layer_zorder)
        timer.wrap(artist, "data")
        n_polygons += len(geoms)
        n_artists += 1
    phases["build_data"] = time.perf_counter() - t

    t = time.perf_counter()
    pc = ccrs.PlateCarree()
    a = ax.add_geometries(static["coastlines"]["geometry"], crs=pc, facecolor="none",
                          edgecolor="white", linewidth=1, zorder=5)
    timer.wrap(a, "below")
    for gdf, kw in (
        (static["state_limit"], {"facecolor": "none", "edgecolor": "black", "linewidth": 1,
                                 "zorder": 60}),
        (static["counties"], {"facecolor": "none", "edgecolor": "black", "linewidth": 0.5,
                              "zorder": 62}),
        (static["lakes"], {"facecolor": "none", "edgecolor": "black", "linewidth": 1,
                           "zorder": 60}),
        (static["nymask"], {"facecolor": (1.0, 1.0, 1.0, 0.75), "edgecolor": (0.0, 0.0, 0.0, 1.0),
                            "linewidth": 1.5, "zorder": 75}),
    ):
        timer.wrap(ax.add_geometries(gdf["geometry"], crs=pc, **kw), "above")

    cities = static["cities"]
    for x, y, label in zip(cities.geometry.x, cities.geometry.y, cities["City"], strict=True):
        timer.wrap(ax.scatter(x, y, s=14, facecolor="white", edgecolor="black",
                              transform=ccrs.PlateCarree(), alpha=0.75, zorder=90), "above")
        if label in CITYLABELXOFFSET[MYREGION]:
            city_offset = x + CITYLABELXOFFSET[MYREGION][label]
        else:
            city_offset = x + CITYLABELXOFFSET[MYREGION]["Default"]
        citytext = ax.text(city_offset, y, label, color="black", fontsize=8, fontweight="bold",
                           ha="left", va="center", transform=ccrs.PlateCarree(), clip_on=True,
                           zorder=91)
        citytext.set_path_effects([path_effects.Stroke(linewidth=2, foreground="white"),
                                   path_effects.Normal()])
        timer.wrap(citytext, "above")
    phases["build_static"] = time.perf_counter() - t

    t = time.perf_counter()
    rect = plt.Rectangle((0.0, 0.9), 1.0, 0.1, facecolor="#d9d2e9", edgecolor="none",
                         transform=ax.transAxes, zorder=995)
    ax.add_patch(rect)
    timer.wrap(rect, "decorations")
    title_artist = ax.set_title(TITLE, fontweight="bold", fontsize=16, y=1.0, pad=-19,
                                zorder=1000)
    timer.wrap(title_artist, "decorations")
    subtitle_attrib = ax.text(0.5, 0.915, prod["subtitle"], fontsize=11, style="italic",
                              transform=ax.transAxes, horizontalalignment="center",
                              verticalalignment="bottom")
    subtitle_attrib.set_zorder(1000)
    timer.wrap(subtitle_attrib, "subtitle")

    legend_ranges = [
        (lower, upper, color)
        for (lower, upper, color, zorder) in QPF_RANGES
        if not (np.isclose(lower, 0.00) and np.isclose(upper, 0.01))
        and upper <= LEGEND_MAX_UPPER + 1e-5
    ]
    legend_ranges = sorted(legend_ranges, key=lambda x: x[0], reverse=True)
    legend_patches = [
        mpatches.Patch(
            facecolor=color,
            label=(f"{lower:.1f}" if lower >= 10 else f"{lower:.2f}")
            + " - "
            + (f"{upper:.1f}" if upper >= 10 else f"{upper:.2f}"),
        )
        for (lower, upper, color) in legend_ranges
    ]
    legend = ax.legend(handles=legend_patches, title="Inches\n ", loc="upper left",
                       bbox_to_anchor=(0.825, 0.77), borderpad=0.3, labelspacing=0.0,
                       handlelength=1.6, handleheight=1.5, handletextpad=0.4)
    legend_title = legend.get_title()
    legend_title.set_fontweight("bold")
    legend_title.set_ha("center")
    legend_title.set_linespacing(0.1)
    for text in legend.get_texts():
        text.set_fontsize(8)
    frame = legend.get_frame()
    frame.set_facecolor("white")
    frame.set_edgecolor("gray")
    frame.set_alpha(1.0)
    legend.set_zorder(1000)
    timer.wrap(legend, "decorations")

    logo = AnnotationBbox(OffsetImage(np.array(static["logo"]), zoom=0.025), (0.01, 0.89),
                          frameon=False, xycoords="axes fraction", box_alignment=(0, 1),
                          zorder=95)
    ax.add_artist(logo)
    timer.wrap(logo, "above")

    attrib = ax.text(0.02, 0.02, "Data Source: NWS WPC Quantitative Precipitation Forecast",
                     fontsize=8, transform=ax.transAxes, horizontalalignment="left",
                     verticalalignment="bottom",
                     bbox=dict(boxstyle="square,pad=0.4", fc="w", ec="0.7", alpha=1, lw=0.5))  # noqa: C408
    attrib.set_zorder(1000)
    timer.wrap(attrib, "decorations")
    phases["build_decorations"] = time.perf_counter() - t

    # savefig(dpi=300, bbox_inches="tight") draws twice: once with drawing
    # disabled to find the tight box, then for real at 300 DPI. Group times
    # sum both passes; the remainder is tight-box/layout work and PNG encoding.
    buf = io.BytesIO()
    t = time.perf_counter()
    plt.savefig(buf, format="png", dpi=SAVE_DPI, bbox_inches="tight", pad_inches=0)
    phases["savefig_total"] = time.perf_counter() - t
    for group in GROUPS:
        phases[f"draw_{group}"] = timer.draw[group]
    phases["savefig_other"] = phases["savefig_total"] - sum(timer.draw.values())

    geometry = output_geometry(fig, ax, legend, attrib)
    # The example never closes its figures; neither does this port.
    return {
        "phases_s": phases,
        "png": buf.getvalue(),
        "geometry": geometry,
        "n_rows": len(qpf_data),
        "n_polygons": n_polygons,
        "n_data_artists": n_artists,
        "n_open_figures": len(plt.get_fignums()),
    }


def output_geometry(fig, ax, legend, attrib) -> dict:
    """Tight crop and artist boxes in output pixels (top-left origin)."""
    renderer = fig.canvas.get_renderer()
    tight = fig.get_tightbbox(renderer)  # inches
    height_px = tight.height * SAVE_DPI

    def out_px(artist):
        bb = artist.get_window_extent(renderer)
        left = (bb.x0 / fig.dpi - tight.x0) * SAVE_DPI
        top = height_px - (bb.y1 / fig.dpi - tight.y0) * SAVE_DPI
        return {"left": left, "top": top,
                "width": bb.width / fig.dpi * SAVE_DPI, "height": bb.height / fig.dpi * SAVE_DPI}

    return {
        "figsize_in": list(FIGSIZE),
        "figure_dpi": fig.dpi,
        "save_dpi": SAVE_DPI,
        "tight_bbox_in": list(tight.bounds),
        "axes_position_fraction": list(ax.get_position().bounds),
        "axes_output_px": out_px(ax),
        "legend_output_px": out_px(legend),
        "attribution_output_px": out_px(attrib),
        "projection_proj4": ax.projection.proj4_init,
        "projected_extent_xxyy": list(ax.get_extent()),
    }


def analyse_png(png: bytes) -> dict:
    """Decode the saved PNG and time matched Pillow encodes (not part of the run)."""
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(png))
    rgba = np.asarray(img.convert("RGBA"))
    encodes = {}
    for mode, level in PNG_VARIANTS:
        t = time.perf_counter()
        im = Image.fromarray(rgba, "RGBA")
        if mode == "rgb":
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="PNG", compress_level=level)
        encodes[f"{mode}_cl{level}"] = {"s": time.perf_counter() - t, "bytes": out.tell()}
    return {
        "size": list(img.size),
        "mode": img.mode,
        "png_bytes": len(png),
        "png_sha256": hashlib.sha256(png).hexdigest(),
        "pixel_sha256": hashlib.sha256(rgba.tobytes()).hexdigest(),
        "alpha_min": int(rgba[..., 3].min()),
        "pillow_encode": encodes,
    }


def versions() -> dict:
    import platform

    import cartopy
    import geopandas
    import matplotlib
    import numpy
    import pandas
    import PIL
    import pyogrio
    import pyproj
    import pytz
    import shapely
    from matplotlib import font_manager

    fonts = {}
    for style, weight in (("normal", "bold"), ("italic", "normal"), ("normal", "normal")):
        prop = font_manager.FontProperties(family="DejaVu Sans", style=style, weight=weight)
        path = Path(font_manager.findfont(prop, fallback_to_default=False))
        fonts[f"{style}-{weight}"] = {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": numpy.__version__,
        "matplotlib": matplotlib.__version__,
        "cartopy": cartopy.__version__,
        "pillow": PIL.__version__,
        "geopandas": geopandas.__version__,
        "pandas": pandas.__version__,
        "pyogrio": pyogrio.__version__,
        "gdal": pyogrio.__gdal_version_string__,
        "shapely": shapely.__version__,
        "geos": shapely.geos_version_string,
        "pyproj": pyproj.__version__,
        "proj": pyproj.proj_version_str,
        "pytz": pytz.__version__,
        "fonts": fonts,
    }


# --- Child processes ---------------------------------------------------------


def run_product(static, day: str, out: Path | None) -> dict:
    t = time.perf_counter()
    prod = read_product(day)
    read_s = time.perf_counter() - t
    result = render_product(static, prod)
    result["phases_s"] = {"read": read_s, **result["phases_s"]}
    if out is not None:
        t = time.perf_counter()
        out.write_bytes(result["png"])
        result["phases_s"]["write_file"] = time.perf_counter() - t
    # draw_* and savefig_other are components of savefig_total; don't add them twice.
    result["phases_s"]["product_total"] = sum(
        v for k, v in result["phases_s"].items()
        if not k.startswith("draw_") and k != "savefig_other"
    )
    result["analysis"] = analyse_png(result.pop("png"))
    result.update(day=day, subtitle=prod["subtitle"], product=prod["product"],
                  valid_time=prod["valid_time"], issue_time=prod["issue_time"])
    return result


def child_product(args) -> dict:
    t_main = time.perf_counter()
    block_network()
    imports = import_stack()
    t = time.perf_counter()
    static, static_times = load_static()
    static_s = time.perf_counter() - t
    t = time.perf_counter()
    result = run_product(static, args.day, Path(args.out) if args.out else None)
    run_s = time.perf_counter() - t
    analysis_s = run_s - result["phases_s"]["product_total"]
    return {
        **result,
        "imports_s": imports,
        "load_static_s": static_s,
        "load_static_detail_s": static_times,
        "pre_main_s": t_main - T0_PERF,
        "script_start_wall": T0_WALL,
        # End to end excludes the post-run PNG analysis.
        "end_to_end_in_process_s": time.perf_counter() - T0_PERF - analysis_s,
        "peak_rss_bytes": peak_rss_bytes(),
    }


def child_loop(args) -> dict:
    """The example's real shape: one process, every product in order."""
    block_network()
    imports = import_stack()
    t = time.perf_counter()
    static, static_times = load_static()
    static_s = time.perf_counter() - t
    outdir = Path(args.outdir) if args.outdir else None
    products = []
    for day in DAYS:
        out = outdir / f"qpf_plot_day_{day}.png" if outdir else None
        products.append(run_product(static, day, out))
    return {
        "imports_s": imports,
        "load_static_s": static_s,
        "load_static_detail_s": static_times,
        "products": products,
        "loop_render_s": sum(p["phases_s"]["product_total"] for p in products),
        "end_to_end_in_process_s": time.perf_counter() - T0_PERF
        - sum(sum(e["s"] for e in p["analysis"]["pillow_encode"].values()) for p in products),
        "script_start_wall": T0_WALL,
        "peak_rss_bytes": peak_rss_bytes(),
        "versions": versions(),
    }


def run_child(argv: list[str]) -> dict:
    t_spawn = time.time()
    t = time.perf_counter()
    proc = subprocess.run([sys.executable, __file__, *argv], capture_output=True, text=True,
                          check=False)
    wall = time.perf_counter() - t
    if proc.returncode != 0:
        sys.exit(f"child {argv} failed:\n{proc.stderr}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    result["interpreter_start_s"] = result["script_start_wall"] - t_spawn
    result["process_wall_s"] = wall
    return result


# --- Orchestrator ------------------------------------------------------------


def check_fixtures() -> dict:
    manifest_path = DATA / "fixtures.json"
    if not manifest_path.exists():
        sys.exit("benchmarks/data/fixtures.json missing; run: uv run benchmarks/fetch_fixtures.py")
    manifest = json.loads(manifest_path.read_text())
    if "qpf" not in manifest:
        sys.exit("no QPF snapshot in fixtures.json; re-run fetch_fixtures.py")
    for entry in manifest["files"]:
        path = DATA / entry["path"]
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            sys.exit(f"fixture missing or changed: {path}; re-run fetch_fixtures.py")
    return manifest


def compare_production(png_path: Path, day: str, geometry: dict) -> dict:
    """Compare static regions (legend, attribution box) with today's production
    image for the same product key. The data and subtitle differ (different
    forecast), so only opaque static boxes are compared."""
    import numpy as np
    from PIL import Image

    prod_path = PRODUCTION_IMAGES / f"qpf_plot_day_{day}.png"
    if not prod_path.exists():
        return {"available": False, "path": str(prod_path)}
    ours = np.asarray(Image.open(png_path).convert("RGBA")).astype(np.int16)
    theirs_img = Image.open(prod_path)
    theirs = np.asarray(theirs_img.convert("RGBA")).astype(np.int16)
    out = {"available": True, "path": str(prod_path), "production_size": list(theirs_img.size),
           "baseline_size": [ours.shape[1], ours.shape[0]],
           "production_mtime_utc": datetime.fromtimestamp(prod_path.stat().st_mtime, UTC)
           .isoformat(timespec="seconds")}
    if ours.shape != theirs.shape:
        out["regions"] = "sizes differ; regions not compared"
        return out
    regions = {}
    for name in ("legend_output_px", "attribution_output_px"):
        b = geometry[name]
        x0, y0 = int(np.ceil(b["left"])) + 2, int(np.ceil(b["top"])) + 2
        x1, y1 = int(b["left"] + b["width"]) - 2, int(b["top"] + b["height"]) - 2
        diff = np.abs(ours[y0:y1, x0:x1] - theirs[y0:y1, x0:x1])
        regions[name] = {
            "box_xyxy": [x0, y0, x1, y1],
            "max_abs_diff": int(diff.max()),
            "fraction_pixels_differing": float((diff.max(axis=-1) > 0).mean()),
        }
    diff_all = np.abs(ours - theirs).max(axis=-1)
    out["regions"] = regions
    out["whole_image_fraction_differing"] = float((diff_all > 0).mean())
    return out


def orchestrate(args) -> None:
    fixtures = check_fixtures()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    outdir = RESULTS / f"qpf-{stamp}"
    outdir.mkdir(parents=True)

    bare = []
    for _ in range(args.cold_runs):
        t = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        bare.append(time.perf_counter() - t)

    print("priming run (bytecode and OS file caches; not recorded)")
    run_child(["--child", "product", "--day", args.day])

    single = []
    for i in range(args.cold_runs):
        out = outdir / f"single_{args.day}_{i}.png"
        r = run_child(["--child", "product", "--day", args.day, "--out", str(out)])
        single.append(r)
        if i:
            out.unlink()
        print(f"single product {args.day} run {i}: {r['process_wall_s']:.2f} s wall, "
              f"savefig {r['phases_s']['savefig_total']:.2f} s")

    loops = []
    for i in range(args.loop_runs):
        # Every loop writes its PNGs, as the example does; only the first set is kept.
        loop_dir = outdir / ("products" if i == 0 else f"products_run{i}")
        loop_dir.mkdir()
        r = run_child(["--child", "loop", "--outdir", str(loop_dir)])
        loops.append(r)
        if i:
            shutil.rmtree(loop_dir)
        print(f"loop run {i}: {len(r['products'])} products, {r['process_wall_s']:.2f} s wall")

    # Reproducibility: same product → same pixels, across runs and modes.
    per_day: dict[str, set[str]] = {}
    for loop in loops:
        for p in loop["products"]:
            per_day.setdefault(p["day"], set()).add(p["analysis"]["pixel_sha256"])
    single_hashes = {r["analysis"]["pixel_sha256"] for r in single}
    per_day.setdefault(args.day, set()).update(single_hashes)
    sizes = {tuple(p["analysis"]["size"]) for loop in loops for p in loop["products"]}
    sizes |= {tuple(r["analysis"]["size"]) for r in single}
    crops = {json.dumps(p["geometry"]["tight_bbox_in"]) for loop in loops for p in loop["products"]}
    reproducible = all(len(h) == 1 for h in per_day.values())

    first_loop = loops[0]
    days = [p["day"] for p in first_loop["products"]]

    def per_product(key: str) -> dict:
        return {d: statistics.median(
            [lp["products"][i]["phases_s"][key] for lp in loops]) for i, d in enumerate(days)}

    phase_keys = first_loop["products"][0]["phases_s"].keys()
    results = {
        "created_utc": stamp,
        "versions": first_loop["versions"],
        "uv_version": subprocess.run(["uv", "--version"], capture_output=True, text=True,
                                     check=True).stdout.strip(),
        "fixtures_prepared_utc": fixtures["prepared_utc"],
        "qpf_snapshot": fixtures["qpf"],
        "caches": {
            "os_file_cache": "warm (priming run; no purge)",
            "bytecode": "warm (priming run)",
            "network": "blocked in render processes",
        },
        "reproducibility": {
            "identical_per_product": reproducible,
            "distinct_hashes_per_product": {d: sorted(h) for d, h in per_day.items()},
            "distinct_sizes": [list(s) for s in sorted(sizes)],
            "distinct_tight_crops": len(crops),
        },
        "geometry": single[0]["geometry"],
        "single_product": {
            "day": args.day,
            "runs": len(single),
            "bare_interpreter_s": summarize(bare),
            "process_wall_s": summarize([r["process_wall_s"] for r in single]),
            "interpreter_start_s": summarize([r["interpreter_start_s"] for r in single]),
            "end_to_end_in_process_s": summarize([r["end_to_end_in_process_s"] for r in single]),
            "imports_s": {k: summarize([r["imports_s"][k] for r in single])
                          for k in single[0]["imports_s"]},
            "load_static_s": summarize([r["load_static_s"] for r in single]),
            "load_static_detail_s": {k: summarize([r["load_static_detail_s"][k] for r in single])
                                     for k in single[0]["load_static_detail_s"]},
            "phases_s": {k: summarize([r["phases_s"][k] for r in single])
                         for k in single[0]["phases_s"]},
            "peak_rss_bytes": summarize([r["peak_rss_bytes"] for r in single]),
            "analysis": single[0]["analysis"],
            "counts": {k: single[0][k] for k in ("n_rows", "n_polygons", "n_data_artists")},
            "runs_detail": single,
        },
        "loop": {
            "runs": len(loops),
            "products": days,
            "process_wall_s": summarize([lp["process_wall_s"] for lp in loops]),
            "imports_total_s": summarize([sum(lp["imports_s"].values()) for lp in loops]),
            "load_static_s": summarize([lp["load_static_s"] for lp in loops]),
            "loop_render_s": summarize([lp["loop_render_s"] for lp in loops]),
            "peak_rss_bytes": summarize([lp["peak_rss_bytes"] for lp in loops]),
            "per_product_median_s": {k: per_product(k) for k in phase_keys},
            "per_product_counts": {p["day"]: {k: p[k] for k in
                                              ("n_rows", "n_polygons", "n_data_artists")}
                                   for p in first_loop["products"]},
            "per_product_png": {p["day"]: p["analysis"] for p in first_loop["products"]},
            "subtitles": {p["day"]: p["subtitle"] for p in first_loop["products"]},
            "runs_detail": loops,
        },
    }
    results["production_comparison"] = compare_production(
        outdir / f"single_{args.day}_0.png", args.day, single[0]["geometry"])
    (outdir / "results.json").write_text(json.dumps(results, indent=2, default=list) + "\n")
    print_summary(results, outdir)


def print_summary(r: dict, outdir: Path) -> None:
    def ms(s: float) -> str:
        return f"{s * 1000:9.1f} ms"

    s, lp = r["single_product"], r["loop"]
    print(f"\nresults: {outdir.relative_to(ROOT)}")
    print(f"reproducible per product: {r['reproducibility']['identical_per_product']}; "
          f"sizes {r['reproducibility']['distinct_sizes']}; "
          f"tight crops {r['reproducibility']['distinct_tight_crops']}")
    print(f"single product {s['day']} (median of {s['runs']} cold runs):")
    print(f"  process wall           {ms(s['process_wall_s']['median'])}")
    print(f"  interpreter start      {ms(s['interpreter_start_s']['median'])}")
    for k, v in s["imports_s"].items():
        print(f"  import {k:<15} {ms(v['median'])}")
    print(f"  load static gpkg/logo  {ms(s['load_static_s']['median'])}")
    for k, v in s["phases_s"].items():
        print(f"  {k:<22} {ms(v['median'])}")
    for k, v in s["analysis"]["pillow_encode"].items():
        print(f"  pillow encode {k:<8} {ms(v['s'])}  ({v['bytes']:,} B)")
    print(f"  peak RSS               {s['peak_rss_bytes']['median'] / 2**20:9.1f} MiB")
    print(f"loop ({len(lp['products'])} products, median of {lp['runs']} runs):")
    print(f"  process wall           {ms(lp['process_wall_s']['median'])}")
    print(f"  imports                {ms(lp['imports_total_s']['median'])}")
    print(f"  load static            {ms(lp['load_static_s']['median'])}")
    print(f"  render all products    {ms(lp['loop_render_s']['median'])}")
    print(f"  peak RSS               {lp['peak_rss_bytes']['median'] / 2**20:9.1f} MiB")
    tot = lp["per_product_median_s"]["product_total"]
    print("  per product total:", ", ".join(f"{d} {v:.2f}s" for d, v in tot.items()))
    print(f"production comparison: {json.dumps(r['production_comparison'])[:400]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child", choices=["product", "loop"], help=argparse.SUPPRESS)
    parser.add_argument("--day", default="1-3", choices=DAYS, help="product for single runs")
    parser.add_argument("--out", help="PNG path for --child product")
    parser.add_argument("--outdir", help=argparse.SUPPRESS)
    parser.add_argument("--cold-runs", type=int, default=5)
    parser.add_argument("--loop-runs", type=int, default=2)
    args = parser.parse_args()
    if args.child == "product":
        print(json.dumps(child_product(args), default=list))
    elif args.child == "loop":
        print(json.dumps(child_loop(args), default=list))
    else:
        orchestrate(args)


if __name__ == "__main__":
    main()
