"""An ordinary Matplotlib/Cartopy script: WPC precipitation forecasts over the Northeast US.

This is the kind of script CartoStack starts from. ``draw(product)`` builds one figure;
run as a script, it writes one PNG per product with ``savefig``, the way a scheduled job
would. ``author_qpf.py`` wraps the figure ``draw`` returns. Nothing here knows about
CartoStack.

The map: Lambert Conformal over the Northeast, Natural Earth land, water, coastline,
state and county lines and cities (``scripts/fetch_fixtures.py``), the forecast polygons
coloured by amount, a title and valid-time subtitle, and a stepped legend.

    uv run --extra build python examples/qpf_map.py benchmarks/data/qpf/day_* --out out/qpf-mpl
"""

from __future__ import annotations

import argparse
import sys
from functools import cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # wpc_qpf.py, also under python -I

import numpy as np
from wpc_qpf import Product, read_product

ROOT = Path(__file__).resolve().parent.parent
NATURAL_EARTH = ROOT / "benchmarks" / "data" / "shapefiles" / "natural_earth"
EXTENT = (-82.0, -66.5, 37.5, 47.6)  # lon0, lon1, lat0, lat1
DPI = 150
TITLE = "Forecast precipitation (WPC)"

# (lower, upper, colour, draw order): amounts in inches, light to heavy.
BINS = [
    (0.01, 0.10, "#d6ecd2", 10),
    (0.10, 0.25, "#a6d8a3", 11),
    (0.25, 0.50, "#64bc78", 12),
    (0.50, 0.75, "#2e9c74", 13),
    (0.75, 1.00, "#1d7f86", 14),
    (1.00, 1.50, "#2a5f9e", 15),
    (1.50, 2.00, "#3f4aa3", 16),
    (2.00, 3.00, "#6a3d9a", 17),
    (3.00, 5.00, "#9b2f8f", 18),
    (5.00, 10.0, "#c62f6c", 19),
    (10.0, 99.0, "#e8603c", 20),
]
OTHER = ("#bdbdbd", 10)  # amounts outside every bin


def map_area():
    """The map extent plus a margin, in lon/lat degrees."""
    from shapely.geometry import box

    lon0, lon1, lat0, lat1 = EXTENT
    return box(lon0 - 3, lat0 - 3, lon1 + 3, lat1 + 3)


@cache
def shapes(ne: Path, group: str, name: str) -> list:
    """A Natural Earth layer, clipped to the map area (plus a margin); read once per process."""
    from cartopy.io import shapereader

    area = map_area()
    out = []
    for r in shapereader.Reader(str(ne / group / f"ne_10m_{name}.shp")).records():
        if r.geometry is not None and r.geometry.intersects(area):
            out.append(r.geometry.intersection(area))
    return out


@cache
def cities(ne: Path) -> list[tuple[float, float, str]]:
    """Larger Natural Earth places inside the map (with room for their labels)."""
    from cartopy.io import shapereader

    lon0, lon1, lat0, lat1 = EXTENT
    out = []
    for r in shapereader.Reader(str(ne / "cultural" / "ne_10m_populated_places.shp")).records():
        a = r.attributes
        if (
            a["SCALERANK"] <= 3
            and lon0 + 0.3 < a["LONGITUDE"] < lon1 - 1.5
            and lat0 + 0.3 < a["LATITUDE"] < lat1 - 0.3
        ):
            out.append((float(a["LONGITUDE"]), float(a["LATITUDE"]), a["NAME"]))
    return sorted(out)


def polygon(rings) -> object:
    """One shapefile record as a (multi)polygon: clockwise rings are outer boundaries,
    counter-clockwise rings are holes inside them (the ESRI shapefile rule)."""
    from shapely import STRtree, make_valid
    from shapely.geometry import MultiPolygon, Point, Polygon

    def signed_area(r):
        x, y = r[:, 0], r[:, 1]
        return 0.5 * float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))

    rings = [r for r in rings if len(r) >= 4]
    shells = [r for r in rings if signed_area(r) < 0]
    holes = [r for r in rings if signed_area(r) >= 0]
    if not shells:
        return None
    outer = [Polygon(r) for r in shells]
    inner: list[list] = [[] for _ in shells]
    tree = STRtree(outer)
    for h in holes:
        point = Point(h[0])
        for i in tree.query(point):
            if outer[i].contains(point):
                inner[i].append(h)
                break
    return make_valid(MultiPolygon([Polygon(s, inner[i]) for i, s in enumerate(shells)]))


def bin_of(value: float) -> tuple[str, int]:
    value = round(value, 2)
    for lower, upper, colour, order in BINS:
        if lower <= value < upper:
            return colour, order
    return OTHER


def draw(product: Product, ne: Path = NATURAL_EARTH) -> dict:
    """The figure for one product, and the artists a caller may want to refer to."""
    import cartopy.crs as ccrs
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    pc = ccrs.PlateCarree()
    proj = ccrs.LambertConformal(
        central_longitude=-74.5, central_latitude=42.5, standard_parallels=(39.0, 45.0)
    )
    fig = plt.figure(figsize=(9, 7.6), dpi=DPI)
    ax = fig.add_axes((0.02, 0.12, 0.96, 0.76), projection=proj)
    ax.set_extent(EXTENT, crs=pc)
    ax.spines["geo"].set_edgecolor("#555555")

    ax.add_geometries(
        shapes(ne, "physical", "ocean"), pc, facecolor="#dbe7f0", edgecolor="none", zorder=0
    )
    ax.add_geometries(
        shapes(ne, "physical", "land"), pc, facecolor="#f7f5f0", edgecolor="none", zorder=1
    )
    ax.add_geometries(
        shapes(ne, "physical", "lakes"), pc, facecolor="#dbe7f0", edgecolor="none", zorder=2
    )

    data = []  # the forecast polygons, by bin, clipped to the map area
    area = map_area()
    for rings, value in zip(product.rings, product.values, strict=True):
        geom = polygon(rings)
        geom = None if geom is None else geom.intersection(area)
        if geom is None or geom.is_empty:
            continue
        colour, order = bin_of(value)
        data.append(ax.add_geometries([geom], pc, facecolor=colour, edgecolor="none", zorder=order))

    lines = dict(facecolor="none", zorder=30)
    ax.add_geometries(
        shapes(ne, "cultural", "admin_2_counties"), pc, edgecolor="#9a9a9a", linewidth=0.25, **lines
    )
    ax.add_geometries(
        shapes(ne, "physical", "lakes"), pc, edgecolor="#6f8ea3", linewidth=0.5, **lines
    )
    ax.add_geometries(
        shapes(ne, "physical", "coastline"), pc, edgecolor="#6f8ea3", linewidth=0.6, **lines
    )
    ax.add_geometries(
        shapes(ne, "cultural", "admin_1_states_provinces_lakes"),
        pc,
        edgecolor="#3c3c3c",
        linewidth=0.8,
        **lines,
    )

    halo = [pe.withStroke(linewidth=2.2, foreground="white")]
    for x, y, name in cities(ne):
        ax.plot(x, y, "o", ms=3, mfc="white", mec="#222222", mew=0.8, transform=pc, zorder=40)
        ax.text(
            x + 0.12,
            y,
            name,
            fontsize=7.5,
            va="center",
            transform=pc,
            zorder=41,
            path_effects=halo,
            clip_on=True,
        )

    title = fig.text(0.03, 0.945, TITLE, fontsize=15, weight="bold", color="#1f1e1c")
    subtitle = fig.text(0.03, 0.905, product.subtitle, fontsize=10, color="#3c3c3c")
    note = fig.text(
        0.97,
        0.008,
        "Data: NOAA/NWS Weather Prediction Center · Natural Earth",
        fontsize=7,
        ha="right",
        color="#6b6a66",
    )

    # Stepped legend: one box per bin, edges labelled in inches.
    lax = fig.add_axes((0.12, 0.062, 0.76, 0.03))
    for i, (lower, _, colour, _) in enumerate(BINS):
        lax.add_patch(Rectangle((i, 0), 1, 1, facecolor=colour, edgecolor="white", linewidth=1))
        lax.text(i, -0.35, f"{lower:g}", ha="center", va="top", fontsize=7.5, color="#3c3c3c")
    lax.text(len(BINS) + 0.15, 0.5, "inches", va="center", fontsize=8, color="#3c3c3c")
    lax.set_xlim(0, len(BINS))
    lax.set_ylim(0, 1)
    lax.axis("off")
    return {
        "fig": fig,
        "ax": ax,
        "data": data,
        "title": title,
        "subtitle": subtitle,
        "note": note,
        "legend": lax,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("products", type=Path, nargs="+", help="WPC QPF product directories")
    parser.add_argument("--out", type=Path, default=Path("."), help="output directory")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for directory in args.products:
        figure = draw(read_product(directory))
        figure["fig"].savefig(args.out / f"qpf_{directory.name}.png", dpi=DPI)
        plt.close(figure["fig"])
        print(args.out / f"qpf_{directory.name}.png")


if __name__ == "__main__":
    main()
