"""Author the hazard maps from an existing plotting script (build extra).

You don't rewrite your plotting script; you wrap the figure it already makes.
``hazard_map.py`` is an ordinary Matplotlib/Cartopy script. Here its ``draw`` function
builds each region's figure, unchanged, and ``SceneBuilder`` only declares the layers:

* ``below``: the figure and map background;
* ``hazard``: a grid slot for the regridded raster. The script draws it with Cartopy
  ``imshow``, so the slot is declared with ``image_extent``: the index map is made through
  the same ``imshow`` call, including Cartopy's regridding to the map projection;
* ``above``: water, state lines, the veil over other states, legend box and names, note;
* ``all_clear``: the "no elevated hazard" note, a static layer the runtime shows or hides;
* text slots ``title``, ``subtitle`` and one ``pct_<level>`` per legend row.

The script also redoes fixed work on every run: it regrids the Mercator raster to the
lon/lat grid and finds which grid cells lie in each region's states. Neither depends on
the day's data, so both are computed once here and saved next to the scenes
(``hazard_aux.npz``): the nearest source pixel of every grid cell, and per region the
area weight of every source pixel inside its states. The runtime then needs one gather
and one weighted ``bincount`` per map.

    uv run --extra build python examples/author_hazard.py out/hazard --inputs out/hazard-inputs
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hazard_data as hd
import hazard_map  # the existing figure code
import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="directory for the scenes and hazard_aux.npz")
    parser.add_argument(
        "--inputs",
        type=Path,
        required=True,
        help="directory for the synthetic GeoTIFFs (written if missing)",
    )
    parser.add_argument("--day", type=int, default=4, help="the day drawn while authoring")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tif = args.inputs / f"hazard_day{args.day}.tif"
    if not tif.exists():
        hd.make_inputs(args.inputs)

    from cartostack.build import SceneBuilder

    src = hd.read_geotiff(tif)
    index = hd.reprojection_index(src, hazard_map.LON, hazard_map.LAT)
    grid = hd.regrid(src, index)
    aux: dict[str, np.ndarray] = {
        "index": index,
        "signature": np.array(src.signature, float),
        "regions": np.array(list(hazard_map.REGIONS)),
    }

    for region in hazard_map.REGIONS:
        # The per-region zone weights the script recomputes each run, made once.
        cells, weights = hazard_map.zone_cells(region)
        pixels = index.ravel()[cells]
        keep = pixels >= 0
        per_pixel = np.bincount(pixels[keep], weights=weights[keep], minlength=src.values.size)
        aux[f"{region}_pixels"] = np.flatnonzero(per_pixel).astype(np.int32)
        aux[f"{region}_weights"] = per_pixel[aux[f"{region}_pixels"]]

        pct = hd.area_shares(src, aux[f"{region}_pixels"], aux[f"{region}_weights"])
        figure = hazard_map.draw(region, grid, pct, *hd.titles(args.day))  # the unchanged script
        figure["all_clear"].set_visible(True)  # drawn into its own layer; the runtime toggles it

        b = SceneBuilder(figure["fig"], figure["ax"], dpi=hazard_map.DPI)
        raster = figure["raster"]
        b.add_static("below", order=0, background=True, zorder=(-math.inf, 10))
        b.add_grid_slot(
            "hazard",
            order=10,
            image_extent=hazard_map.GRID_EXTENT,
            origin="upper",
            cmap=raster.cmap,
            norm=raster.norm,
            values=grid,
            artists=[raster],
        )
        b.add_static("all_clear", order=30, artists=[figure["all_clear"]])
        b.add_static(
            "above", order=20, zorder=(10, math.inf), artists=[*figure["legend"], figure["note"]]
        )
        b.add_text_slot("title", figure["title"], order=40)
        b.add_text_slot("subtitle", figure["subtitle"], order=40)
        for name, text in figure["pct"].items():
            b.add_text_slot(f"pct_{name.lower()}", text, order=40)
        scene = b.build()
        scene.hide("all_clear")
        path = args.output / f"hazard_{region}.cstack"
        scene.save(path)
        print(f"wrote {path}: {scene.shape[1]}x{scene.shape[0]} px, {len(scene)} layers")

    np.savez_compressed(args.output / "hazard_aux.npz", **aux)
    print(f"wrote {args.output / 'hazard_aux.npz'}")


if __name__ == "__main__":
    main()
