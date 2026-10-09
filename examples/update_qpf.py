"""Render every WPC QPF product from one authored ``.cstack`` file (NumPy and Pillow only).

The cron-style workload of ``resources/slow_example.py``: load the file once, then for
each product swap in the new precipitation polygons and the subtitle, and write a PNG.
Background, state and county borders, cities, logo, legend and title are reused as
stored; only the polygons are projected (NumPy Lambert Conformal) and filled.

Each input is a directory holding one WPC QPF shapefile (``.shp`` + ``.dbf``), e.g. the
snapshots ``scripts/fetch_fixtures.py`` keeps in ``benchmarks/data/qpf/day_*``.

    python examples/update_qpf.py out/qpf.cstack benchmarks/data/qpf/day_* --out out/png
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # wpc_qpf.py, also under python -I

from wpc_qpf import read_product

from cartostack import Scene


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scene", type=Path, help="an authored .cstack file")
    parser.add_argument("products", type=Path, nargs="+", help="WPC QPF product directories")
    parser.add_argument("--out", type=Path, default=Path("."), help="output directory")
    parser.add_argument("--slot", default="qpf", help="the polygon slot to update")
    parser.add_argument("--text", default="subtitle", help="the text slot to update")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with Scene.load(args.scene) as scene:
        for directory in args.products:
            t = time.perf_counter()
            product = read_product(directory)
            scene.replace_polygons(args.slot, product.rings, product.values)
            scene.text[args.text] = product.subtitle
            png = args.out / f"qpf_{directory.name}.png"
            scene.save_png(png)
            print(
                f"{png}  {len(product.values)} polygons  {(time.perf_counter() - t) * 1000:.0f} ms"
            )
    print(f"{len(args.products)} products in {time.perf_counter() - start:.2f} s")


if __name__ == "__main__":
    main()
