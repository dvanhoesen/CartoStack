"""Author a scene from a plotting script you cannot (or would rather not) edit (build extra).

You don't rewrite your plotting script; you wrap the figure it already makes. Many
scheduled-job scripts are one ``main()`` that loops over products and ends each map with
``savefig``; there is no function to import. This example runs such a script as-is
(``qpf_map.py`` here, with its own command line), intercepts its first ``savefig``,
and builds the scene from that exact figure. The script never knows.

Layers are then picked from the captured figure by what the script drew: the forecast
polygons by their draw order (the bin table the script defines at module level), the
subtitle as the second line of figure text, everything else above or below the data by
z-order.

    uv run --extra build python examples/wrap_script.py out/wrapped.cstack -- \\
        examples/qpf_map.py benchmarks/data/qpf/day_1-3
"""

from __future__ import annotations

import argparse
import math
import runpy
import sys
from pathlib import Path


class _Captured(BaseException):  # not an Exception, so the script's own handlers let it pass
    pass


def capture_first_savefig(script: Path, argv: list[str]):
    """Run ``script`` with ``argv`` and return the first figure it saves (nothing is written)."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    captured = []
    original = Figure.savefig

    def intercept(fig, *args, **kwargs):  # plt.savefig ends up here too
        captured.append(fig)
        raise _Captured

    Figure.savefig = intercept
    saved_argv = sys.argv
    sys.argv = [str(script), *argv]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except _Captured:
        pass
    finally:
        Figure.savefig = original
        sys.argv = saved_argv
    if not captured:
        raise SystemExit(f"{script} never called savefig")
    return captured[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="the .cstack file to write")
    parser.add_argument("script", type=Path, help="the plotting script, run unchanged")
    parser.add_argument("script_args", nargs=argparse.REMAINDER, help="its arguments")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    fig = capture_first_savefig(args.script, args.script_args)
    ax = next(a for a in fig.axes if hasattr(a, "projection"))  # the map axes

    from cartostack.build import SceneBuilder

    settings = runpy.run_path(str(args.script))  # its module-level names; main() does not run
    bins, fallback = settings["BINS"], settings["OTHER"]  # the script's own bin table
    orders = {order for *_, order in bins}
    polygons = [a for a in ax.get_children() if a.get_zorder() in orders]
    title, subtitle, note = fig.texts  # the script's three figure texts, in drawing order

    b = SceneBuilder(fig, ax, dpi=fig.dpi)
    b.add_static("below", order=0, background=True, zorder=(-math.inf, 10))
    b.add_polygon_slot(
        "qpf", order=10, bins=bins, fallback=fallback, round_decimals=2, artists=polygons
    )
    b.add_static(
        "above",
        order=20,
        zorder=(10, math.inf),
        artists=[title, note, *(a for a in fig.axes if a is not ax)],
    )
    b.add_text_slot("subtitle", subtitle, order=30)
    scene = b.save(args.output)
    print(
        f"wrote {args.output}: {scene.shape[1]}x{scene.shape[0]} px, "
        f"layers {[layer.id for layer in scene.draw_order()]}"
    )


if __name__ == "__main__":
    main()
