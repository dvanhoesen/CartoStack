"""Author a grid map: a temperature field over New York State (build extra required).

Draws an ordinary Cartopy figure, then tells ``SceneBuilder`` which parts become which
layers:

* ``below``: background, ocean, land and lakes (everything under the data);
* ``temperature``: a grid slot; the runtime colours new values with the stored index map,
  normalisation and colormap;
* ``above``: coastline, borders and the title;
* ``colorbar``: redrawn by the runtime when the colour range changes;
* ``subtitle``: a text slot, re-rendered with the embedded font.

It also writes a few sample inputs (``.npy``) for ``update_grid.py``. Natural Earth
comes from local shapefiles (``--natural-earth``; ``scripts/fetch_fixtures.py``
prepares them); nothing is downloaded.

    uv run --extra build python examples/author_grid.py out/grid.cstack --inputs out/grid-inputs
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
NATURAL_EARTH = ROOT / "benchmarks" / "data" / "shapefiles" / "natural_earth"
EXTENT = (-80.5, -71.5, 40.2, 45.3)  # lon0, lon1, lat0, lat1
SHAPE = (300, 300)  # (ny, nx) cell centres
LON = np.linspace(EXTENT[0] - 0.5, EXTENT[1] + 0.5, SHAPE[1])
LAT = np.linspace(EXTENT[2] - 0.5, EXTENT[3] + 0.5, SHAPE[0])


def sample_field(hour: int) -> np.ndarray:
    """A smooth temperature-like field (°C) that warms through the day."""
    lon, lat = np.meshgrid(LON, LAT)
    warm = 8.0 * np.exp(-((lon + 74.0 + hour / 12) ** 2 + (lat - 41.0) ** 2) / 4.0)
    waves = 3.0 * np.sin(lon * 1.3 + hour / 4) * np.cos(lat * 1.7)
    return (12.0 - 1.6 * (lat - 40.0) + warm + waves + hour / 3).astype(np.float32)


def subtitle(hour: int) -> str:
    return f"Valid 2026-10-09 {hour:02d}:00 UTC"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="the .cstack file to write")
    parser.add_argument("--inputs", type=Path, help="also write sample inputs (.npy) here")
    parser.add_argument("--natural-earth", type=Path, default=NATURAL_EARTH)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    import cartopy.crs as ccrs
    from cartopy.io import shapereader

    from cartostack.build import SceneBuilder

    ne = args.natural_earth
    proj = ccrs.LambertConformal(central_longitude=-76.0, central_latitude=42.75,
                                 standard_parallels=(41.0, 44.5))  # fmt: skip
    builder = SceneBuilder.new(
        proj, EXTENT, width=1200, height=900, dpi=150, axes=(0.03, 0.15, 0.94, 0.75)
    )
    fig, ax = builder.fig, builder.ax
    pc = ccrs.PlateCarree()

    def shapes(name: str) -> list[object]:
        group = "physical" if name in ("ocean", "land", "lakes", "coastline") else "cultural"
        return list(shapereader.Reader(str(ne / group / f"ne_10m_{name}.shp")).geometries())

    # The figure, exactly as one would draw it for savefig.
    ax.add_geometries(shapes("ocean"), pc, facecolor="#cfe3f3", edgecolor="none", zorder=0)
    ax.add_geometries(shapes("land"), pc, facecolor="#f2efe6", edgecolor="none", zorder=0.5)
    ax.add_geometries(shapes("lakes"), pc, facecolor="#cfe3f3", edgecolor="none", zorder=1)
    mesh = ax.pcolormesh(LON, LAT, sample_field(12), transform=pc, shading="nearest",
                         cmap="coolwarm", vmin=-5, vmax=30, alpha=0.8, zorder=2)  # fmt: skip
    ax.add_geometries(shapes("coastline"), pc, facecolor="none", edgecolor="#333", lw=0.6, zorder=3)
    ax.add_geometries(shapes("admin_1_states_provinces_lakes"), pc, facecolor="none",
                      edgecolor="#555", lw=0.5, zorder=3)  # fmt: skip
    cax = fig.add_axes((0.2, 0.09, 0.6, 0.025))
    cbar = fig.colorbar(mesh, cax=cax, orientation="horizontal", extend="both", label="°C")
    title = fig.text(0.03, 0.955, "2-m temperature (sample field)", fontsize=14, weight="bold")
    sub = fig.text(0.03, 0.915, subtitle(12), fontsize=10)

    # The layer assignment: draw-order bands on the map axes plus explicit artists.
    builder.add_static("below", order=0, background=True, zorder=(-math.inf, 2))
    builder.add_grid_slot("temperature", order=10, lon=LON, lat=LAT, cmap=mesh.cmap,
                          norm=mesh.norm, alpha=0.8, extend="both", values=mesh.get_array(),
                          artists=[mesh])  # fmt: skip
    builder.add_static("above", order=20, zorder=(2, math.inf), artists=[title])
    builder.add_colorbar("colorbar", cbar, slot="temperature", order=25)
    builder.add_text_slot("subtitle", sub, order=30)
    scene = builder.save(args.output)
    print(f"wrote {args.output}: {scene.shape[1]}x{scene.shape[0]} px, "
          f"layers {[layer.id for layer in scene.draw_order()]}")  # fmt: skip

    if args.inputs:
        args.inputs.mkdir(parents=True, exist_ok=True)
        for hour in (0, 6, 12, 18):
            np.save(args.inputs / f"t2m_{hour:02d}z.npy", sample_field(hour))
        print(f"wrote 4 sample inputs to {args.inputs}")


if __name__ == "__main__":
    main()
