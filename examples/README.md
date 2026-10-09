# Examples

Two maps, each in two steps: **author** the `.cstack` file once with the `build` extra,
then **update** it on every run using only NumPy and Pillow.

| Script | Needs | Does |
| --- | --- | --- |
| [`author_grid.py`](author_grid.py) | `build` extra; Natural Earth shapefiles | Draws a temperature map of New York State and writes `grid.cstack` (5 layers: below, a `temperature` grid slot, above, a redrawable `colorbar`, a `subtitle` text slot), plus 4 sample inputs (`.npy`). |
| [`update_grid.py`](update_grid.py) | runtime only | Writes one PNG per `.npy` input. Optional `--vmin`/`--vmax` change the colour range, and the colorbar is redrawn to match. |
| [`author_qpf.py`](author_qpf.py) | `build` extra + GeoPandas (PEP 723 header); QPF fixtures | Wraps the unchanged WPC QPF figure from `benchmarks/baseline_qpf.py` (a port of the production script) and writes `qpf.cstack` (below, a `qpf` polygon slot, above, a `subtitle` text slot). |
| [`update_qpf.py`](update_qpf.py) | runtime only | Renders every WPC QPF product directory given, in one process. |
| [`wpc_qpf.py`](wpc_qpf.py) | runtime only | A small reader for WPC's QPF shapefiles that uses only the standard library and NumPy (no GeoPandas). |

## Running them

The fixtures (Natural Earth, county and state GeoPackages, logo, and the 18 WPC QPF
products) come from `scripts/fetch_fixtures.py`, which is one-time and needs the
network. Nothing below downloads anything.

```bash
uv run scripts/fetch_fixtures.py                     # once

# Author (build environment)
uv run --extra build python examples/author_grid.py out/grid.cstack --inputs out/grid-inputs
uv run examples/author_qpf.py out/qpf.cstack         # PEP 723: brings GeoPandas

# Update (any environment with cartostack, NumPy and Pillow)
python examples/update_grid.py out/grid.cstack out/grid-inputs/*.npy --out out/png
python examples/update_grid.py out/grid.cstack out/grid-inputs/t2m_18z.npy --out out/png \
    --vmin 0 --vmax 40 --subtitle "Valid 2026-10-09 18:00 UTC"
python examples/update_qpf.py out/qpf.cstack benchmarks/data/qpf/day_* --out out/png
```

The update scripts are shaped like a cron job: load the file once, then loop over the
inputs. Only the slot that changes is re-rendered; static layers are reused as stored.
To run the updates on a runtime-only machine, copy the `.cstack` files and the inputs
there and `pip install cartostack`. The original shapefiles and Natural Earth data are
not needed.
