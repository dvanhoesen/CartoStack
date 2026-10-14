"""Render the daily hazard maps from the authored scenes (NumPy and Pillow only).

The scheduled-job workload of ``hazard_map.py``: for each daily GeoTIFF, read it with
Pillow, check that its grid is the one the scenes were authored for, regrid it with the
stored index, and for each region compute the area shares with the stored weights, swap
in the grid values, the title, subtitle and legend percentages, show or hide the
"all clear" note, and write a PNG. Basemap, borders, legend box and note are reused as
stored.

    python examples/update_hazard.py out/hazard out/hazard-inputs/*.tif --out out/png
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # hazard_data.py, also under python -I

import numpy as np
from hazard_data import LEVELS, area_shares, read_geotiff, regrid, titles

from cartostack import Scene


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenes", type=Path, help="directory written by author_hazard.py")
    parser.add_argument("inputs", type=Path, nargs="+", help="hazard_day<N>.tif files")
    parser.add_argument("--out", type=Path, default=Path("."), help="output directory")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    aux = dict(np.load(args.scenes / "hazard_aux.npz"))
    regions = [str(r) for r in aux["regions"]]
    scenes = {r: Scene.load(args.scenes / f"hazard_{r}.cstack") for r in regions}
    for path in args.inputs:
        t = time.perf_counter()
        day = int(path.stem.removeprefix("hazard_day"))
        src = read_geotiff(path)
        if not np.array_equal(np.array(src.signature, float), aux["signature"]):
            raise SystemExit(f"{path}: grid differs from the one the scenes were authored for")
        grid = regrid(src, aux["index"])
        title, subtitle = titles(day)
        for region, scene in scenes.items():
            pct = area_shares(src, aux[f"{region}_pixels"], aux[f"{region}_weights"])
            scene.replace_grid("hazard", grid)
            scene.text["title"] = title
            scene.text["subtitle"] = subtitle
            for name in LEVELS:
                scene.text[f"pct_{name.lower()}"] = f"{pct[name]}%"
            (scene.show if pct["None"] == 100.0 else scene.hide)("all_clear")
            scene.save_png(args.out / f"hazard_{region}_day{day}.png")
        print(f"day {day}: {len(scenes)} maps  {(time.perf_counter() - t) * 1000:.0f} ms")
    print(f"{len(args.inputs) * len(scenes)} maps in {time.perf_counter() - start:.2f} s")


if __name__ == "__main__":
    main()
