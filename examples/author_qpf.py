"""Author the WPC QPF map from an existing plotting script (build extra).

You don't rewrite your plotting script; you wrap the figure it already makes.
``qpf_map.py`` is an ordinary Matplotlib/Cartopy script. Here its ``draw`` function
builds the figure for one product, unchanged, and ``SceneBuilder`` only declares which
parts become which layers:

* ``below``: background, ocean, land and lakes (everything under the forecast);
* ``qpf``: a polygon slot, with the script's own bin table (lower, upper, colour, draw
  order), its fallback colour, values rounded to 2 decimals and 4x supersampled edges;
* ``above``: county, state and coast lines, cities, title, legend and attribution;
* ``subtitle``: the valid-time line, as a text slot.

Fixtures come from ``benchmarks/data`` (``scripts/fetch_fixtures.py``); nothing is
downloaded.

    uv run --extra build python examples/author_qpf.py out/qpf.cstack
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qpf_map  # the existing figure code
from wpc_qpf import read_product

ROOT = Path(__file__).resolve().parent.parent
QPF_DIR = ROOT / "benchmarks" / "data" / "qpf"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="the .cstack file to write")
    parser.add_argument(
        "--product",
        type=Path,
        default=QPF_DIR / "day_1-3",
        help="the product drawn while authoring",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    from cartostack.build import SceneBuilder

    figure = qpf_map.draw(read_product(args.product))  # the unchanged script

    builder = SceneBuilder(figure["fig"], figure["ax"], dpi=qpf_map.DPI)
    builder.add_static("below", order=0, background=True, zorder=(-math.inf, 10))
    builder.add_polygon_slot(
        "qpf",
        order=10,
        bins=qpf_map.BINS,
        fallback=qpf_map.OTHER,
        round_decimals=2,
        artists=figure["data"],  # the drawn forecast polygons become the slot
    )
    builder.add_static(
        "above",
        order=20,
        zorder=(10, math.inf),
        artists=[figure["title"], figure["note"], figure["legend"]],
    )
    builder.add_text_slot("subtitle", figure["subtitle"], order=30)
    scene = builder.save(args.output)
    print(
        f"wrote {args.output}: {scene.shape[1]}x{scene.shape[0]} px, "
        f"layers {[layer.id for layer in scene.draw_order()]}"
    )


if __name__ == "__main__":
    main()
