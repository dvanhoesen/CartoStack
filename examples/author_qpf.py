# /// script
# requires-python = "==3.13.*"
# dependencies = [
#     "cartostack[build]",
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
# [tool.uv.sources]
# cartostack = { path = "..", editable = true }
# ///
"""Author the WPC QPF map from an existing Matplotlib/Cartopy script (build extra).

The figure is the one ``benchmarks/baseline_qpf.py`` draws: a port of the production
script ``resources/slow_example.py`` (GeoPackage borders and counties, cities, logo,
legend, title bar), here for the Day 1-3 product. The script is not changed: the builder
wraps its finished figure and only declares the layers:

* ``below``: everything under the precipitation (zorder < 10), with the background;
* ``qpf``: the polygon slot, with WPC's bin table (lower, upper, colour, draw order),
  a gray fallback, values rounded to 2 decimals, 4x supersampled edges;
* ``above``: everything else (borders, cities, logo, title bar, legend);
* ``subtitle``: the valid-time line, as a text slot.

Fixtures come from ``benchmarks/data`` (``scripts/fetch_fixtures.py``); the figure is
cropped like ``savefig(bbox_inches="tight")``, resolved once here.

    uv run examples/author_qpf.py out/qpf.cstack
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

import baseline_qpf as bq  # the existing figure code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="the .cstack file to write")
    parser.add_argument("--day", default="1-3", help="the product drawn while authoring")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    from cartostack.build import SceneBuilder

    static, _ = bq.load_static()
    figure = bq.build_figure(static, bq.read_product(args.day))  # the unchanged script

    builder = SceneBuilder(figure["fig"], figure["ax"], dpi=bq.SAVE_DPI, crop="tight")
    builder.add_static("below", order=0, background=True, zorder=(-math.inf, 10))
    builder.add_polygon_slot(
        "qpf",
        order=10,
        bins=bq.QPF_RANGES,  # (lower, upper, colour, zorder) rows
        fallback=("gray", 10),
        round_decimals=2,
        artists=figure["data_artists"],  # the drawn polygons become the slot
    )
    builder.add_static("above", order=20, zorder=(10, math.inf))
    builder.add_text_slot("subtitle", figure["subtitle"], order=30)
    scene = builder.save(args.output)
    print(f"wrote {args.output}: {scene.shape[1]}x{scene.shape[0]} px, "
          f"layers {[layer.id for layer in scene.draw_order()]}")  # fmt: skip


if __name__ == "__main__":
    main()
