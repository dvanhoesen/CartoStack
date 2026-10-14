"""An ordinary Matplotlib/Cartopy script: a daily categorical hazard map for two regions.

This is the kind of script CartoStack starts from. For each daily GeoTIFF (made by
``hazard_data.make_inputs``) it regrids the Web Mercator raster to a lon/lat grid, works
out the share of the region's land area in each level, and draws one map per region with
``imshow``, a legend that lists those shares, and an "all clear" note when nothing is
elevated. ``author_hazard.py`` wraps the figure ``draw`` returns. Nothing here knows about
CartoStack.

    uv run --extra build python examples/hazard_map.py out/hazard-inputs/*.tif --out out/hazard-mpl
"""

from __future__ import annotations

import argparse
import sys
from functools import cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # hazard_data.py, also under python -I

import numpy as np
from hazard_data import (
    LEVELS,
    NODATA,
    area_shares,
    read_geotiff,
    regrid,
    reprojection_index,
    titles,
)

ROOT = Path(__file__).resolve().parent.parent
NATURAL_EARTH = ROOT / "benchmarks" / "data" / "shapefiles" / "natural_earth"
DPI = 150
COLORS = (
    "#e9f5e1",
    "#f6e58d",
    "#f5a442",
    "#e3493b",
    "#7d1f6e",
    (1.0, 1.0, 1.0, 0.0),
)  # 0..4, no data

# The lon/lat grid the raster is shown on (cell centres, 0.02 degrees).
LON = np.arange(-83.0, -65.5, 0.02) + 0.01
LAT = np.arange(48.6, 36.5, -0.02) - 0.01  # north to south, as imshow(origin="upper")
GRID_EXTENT = (LON[0] - 0.01, LON[-1] + 0.01, LAT[-1] - 0.01, LAT[0] + 0.01)

# Map extent (lon0, lon1, lat0, lat1) and the states whose area the legend summarises.
REGIONS = {
    "northeast": (
        (-81.5, -66.5, 38.5, 47.6),
        ("NY", "PA", "NJ", "CT", "RI", "MA", "VT", "NH", "ME"),
    ),
    "new_england": ((-74.0, -66.8, 40.9, 47.5), ("CT", "RI", "MA", "VT", "NH", "ME")),
}


@cache
def states(ne: Path = NATURAL_EARTH) -> dict[str, object]:
    """US state polygons by postal code (Natural Earth admin-1)."""
    from cartopy.io import shapereader

    path = ne / "cultural" / "ne_10m_admin_1_states_provinces_lakes.shp"
    return {
        r.attributes["postal"]: r.geometry
        for r in shapereader.Reader(str(path)).records()
        if r.attributes["adm0_a3"] == "USA"
    }


@cache
def water(ne: Path = NATURAL_EARTH) -> list:
    """Ocean and lakes, clipped to the grid area; drawn over the raster."""
    from cartopy.io import shapereader
    from shapely.geometry import box

    area = box(GRID_EXTENT[0], GRID_EXTENT[2], GRID_EXTENT[1], GRID_EXTENT[3])
    out = []
    for name in ("ocean", "lakes"):
        for r in shapereader.Reader(str(ne / "physical" / f"ne_10m_{name}.shp")).records():
            if r.geometry is not None and r.geometry.intersects(area):
                out.append(r.geometry.intersection(area))
    return out


@cache
def zone_cells(region: str) -> tuple[np.ndarray, np.ndarray]:
    """``(flat cell indices, area weights)`` of the grid cells inside the region's states."""
    from matplotlib.path import Path as MplPath

    lon, lat = np.meshgrid(LON, LAT)
    points = np.column_stack([lon.ravel(), lat.ravel()])
    inside = np.zeros(points.shape[0], bool)
    for code in REGIONS[region][1]:
        geom = states()[code]
        for poly in getattr(geom, "geoms", [geom]):
            inside |= MplPath(np.asarray(poly.exterior.coords)).contains_points(points)
    cells = np.flatnonzero(inside)
    return cells, np.cos(np.radians(lat.ravel()[cells]))


def shares(src, index: np.ndarray, region: str) -> dict[str, float]:
    """Percent of the region's state area in each level, from the regridded raster."""
    cells, weights = zone_cells(region)
    pixels = index.ravel()[cells]
    keep = pixels >= 0
    return area_shares(src, pixels[keep], weights[keep])


def draw(
    region: str,
    grid: np.ndarray,
    pct: dict[str, float],
    title: str,
    subtitle: str,
    ne: Path = NATURAL_EARTH,
) -> dict:
    """The figure for one region and day, and the artists a caller may want to refer to."""
    import cartopy.crs as ccrs
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Rectangle

    extent, codes = REGIONS[region]
    pc = ccrs.PlateCarree()
    proj = ccrs.LambertConformal(
        central_longitude=(extent[0] + extent[1]) / 2, central_latitude=(extent[2] + extent[3]) / 2
    )
    fig = plt.figure(figsize=(9, 7.6), dpi=DPI)
    ax = fig.add_axes((0.02, 0.04, 0.96, 0.84), projection=proj)
    ax.set_extent(extent, crs=pc)
    ax.spines["geo"].set_edgecolor("#555555")
    ax.set_facecolor("#dbe7f0")

    raster = ax.imshow(
        grid,
        extent=GRID_EXTENT,
        origin="upper",
        transform=pc,
        interpolation="none",
        cmap=ListedColormap(COLORS),
        vmin=-0.5,
        vmax=5.5,
        zorder=10,
    )

    ax.add_geometries(
        water(ne), pc, facecolor="#dbe7f0", edgecolor="#6f8ea3", linewidth=0.5, zorder=20
    )
    lines = dict(facecolor="none", zorder=30)
    all_states = list(states(ne).values())
    focus = [states(ne)[c] for c in codes]
    ax.add_geometries(all_states, pc, edgecolor="#8a8a8a", linewidth=0.5, **lines)
    ax.add_geometries(focus, pc, edgecolor="#2b2b2b", linewidth=1.1, **lines)
    others = [g for c, g in states(ne).items() if c not in codes]
    ax.add_geometries(others, pc, facecolor=(1, 1, 1, 0.55), edgecolor="none", zorder=25)

    title_t = fig.text(0.03, 0.945, title, fontsize=15, weight="bold", color="#1f1e1c")
    subtitle_t = fig.text(0.03, 0.905, subtitle, fontsize=10, color="#3c3c3c")

    # Legend: colour box, level name, share of area (right-aligned).
    x0, y0, row = 0.05, 0.075, 0.032
    box = Rectangle(
        (x0 - 0.012, y0 - 0.02),
        0.25,
        row * len(LEVELS) + 0.085,
        transform=fig.transFigure,
        facecolor="white",
        edgecolor="#9a9a9a",
        linewidth=0.6,
        zorder=2,
    )
    fig.add_artist(box)
    legend = [
        box,
        fig.text(
            x0,
            y0 + row * len(LEVELS) + 0.012,
            "Share of area\nin the bold states",
            fontsize=8.5,
            weight="bold",
            va="bottom",
            zorder=3,
        ),
    ]
    pct_texts = {}
    for i, name in enumerate(reversed(LEVELS)):
        y = y0 + row * i
        swatch = Rectangle(
            (x0, y),
            0.03,
            row * 0.7,
            transform=fig.transFigure,
            facecolor=COLORS[LEVELS.index(name)],
            edgecolor="#9a9a9a",
            lw=0.5,
            zorder=3,
        )
        fig.add_artist(swatch)
        legend += [
            swatch,
            fig.text(x0 + 0.04, y + row * 0.35, name, fontsize=9, va="center", zorder=3),
        ]
        pct_texts[name] = fig.text(
            x0 + 0.225,
            y + row * 0.35,
            f"{pct[name]}%",
            fontsize=9,
            ha="right",
            va="center",
            zorder=3,
        )

    all_clear = ax.text(
        0.5,
        0.5,
        "No elevated hazard in the bold states",
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=13,
        weight="bold",
        color="#2b6a3a",
        zorder=40,
        path_effects=[pe.withStroke(linewidth=3, foreground="white")],
    )
    all_clear.set_visible(pct["None"] == 100.0)
    note = fig.text(
        0.97,
        0.012,
        "Synthetic data · borders: Natural Earth",
        fontsize=7,
        ha="right",
        color="#6b6a66",
    )
    return {
        "fig": fig,
        "ax": ax,
        "raster": raster,
        "title": title_t,
        "subtitle": subtitle_t,
        "pct": pct_texts,
        "legend": legend,
        "all_clear": all_clear,
        "note": note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inputs", type=Path, nargs="+", help="hazard_day<N>.tif files")
    parser.add_argument("--out", type=Path, default=Path("."), help="output directory")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for path in args.inputs:
        day = int(path.stem.removeprefix("hazard_day"))
        src = read_geotiff(path)
        index = reprojection_index(src, LON, LAT)  # recomputed every run
        grid = regrid(src, index)
        for region in REGIONS:
            figure = draw(region, grid, shares(src, index, region), *titles(day))
            png = args.out / f"hazard_{region}_day{day}.png"
            figure["fig"].savefig(png, dpi=DPI)
            plt.close(figure["fig"])
            print(png)


assert len(COLORS) - 1 == NODATA  # the last colour is the transparent no-data colour

if __name__ == "__main__":
    main()
