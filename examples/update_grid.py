"""Render one PNG per new grid from an authored ``.cstack`` file (NumPy and Pillow only).

A cron-style job: load the file once, then for each input swap in the values and the
subtitle, and write a PNG. Everything else in the map (background, borders, title,
colorbar) is reused as stored. ``--vmin``/``--vmax`` change the colour range; the
colorbar is redrawn to match.

Inputs are ``.npy`` files holding the grid's values (``(ny, nx)``, the shape the slot was
authored with); the subtitle is ``--subtitle`` with ``{name}`` replaced by each file's
stem.

    python examples/update_grid.py out/grid.cstack out/grid-inputs/*.npy --out out/png
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from cartostack import Scene


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scene", type=Path, help="an authored .cstack file")
    parser.add_argument("inputs", type=Path, nargs="+", help=".npy files of grid values")
    parser.add_argument("--out", type=Path, default=Path("."), help="output directory")
    parser.add_argument("--slot", default="temperature", help="the grid slot to update")
    parser.add_argument("--text", default="subtitle", help="the text slot to update")
    parser.add_argument("--subtitle", default="Valid {name}", help="subtitle template")
    parser.add_argument("--vmin", type=float, help="new colour range minimum")
    parser.add_argument("--vmax", type=float, help="new colour range maximum")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with Scene.load(args.scene) as scene:
        for path in args.inputs:
            t = time.perf_counter()
            values = np.load(path)
            scene.replace_grid(args.slot, values, vmin=args.vmin, vmax=args.vmax)
            if args.text:
                scene.text[args.text] = args.subtitle.format(name=path.stem)
            png = args.out / f"{path.stem}.png"
            scene.save_png(png)
            print(f"{png}  {(time.perf_counter() - t) * 1000:.0f} ms")
    print(f"{len(args.inputs)} products in {time.perf_counter() - start:.2f} s")


if __name__ == "__main__":
    main()
