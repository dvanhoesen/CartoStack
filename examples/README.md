# Examples

**You don't rewrite your plotting script; you wrap the figure it already makes.** Each
example below starts from an ordinary Matplotlib/Cartopy script, then works in two steps:
**author** a `.cstack` file once from that script's figure (`build` extra), then
**update** it on every run with only NumPy and Pillow.

| Script | Needs | Does |
| --- | --- | --- |
| **Temperature grid** | | |
| [`author_grid.py`](author_grid.py) | `build` extra; Natural Earth | Draws a temperature map of New York State and writes `grid.cstack` (below, a `temperature` grid slot, above, a redrawable `colorbar`, a `subtitle` text slot), plus 4 sample inputs (`.npy`). |
| [`update_grid.py`](update_grid.py) | runtime only | One PNG per `.npy` input. `--vmin`/`--vmax` change the color range, and the colorbar is redrawn to match. |
| **Precipitation forecast polygons (WPC QPF)** | | |
| [`qpf_map.py`](qpf_map.py) | `build` extra; Natural Earth, QPF fixtures | The existing script: a Northeast US forecast map per product, written with `savefig`. Knows nothing about CartoStack. |
| [`author_qpf.py`](author_qpf.py) | `build` extra | Wraps the figure `qpf_map.draw` returns and writes `qpf.cstack` (below, a `qpf` polygon slot with the script's bin table, above, a `subtitle` text slot). |
| [`wrap_script.py`](wrap_script.py) | `build` extra | The same scene from a script you can't edit: runs `qpf_map.py` unchanged with its own command line, intercepts its first `savefig`, and builds from that figure. |
| [`update_qpf.py`](update_qpf.py) | runtime only | Renders every WPC QPF product directory given, in one process. |
| [`wpc_qpf.py`](wpc_qpf.py) | runtime only | Reads WPC's QPF shapefiles with the standard library and NumPy (no GeoPandas). |
| **Daily categorical hazard raster (synthetic GeoTIFFs)** | | |
| [`hazard_data.py`](hazard_data.py) | runtime only | Makes a synthetic week of Web Mercator GeoTIFFs; reads them with Pillow; regrids with a stored index; area shares per level from per-pixel weights. |
| [`hazard_map.py`](hazard_map.py) | `build` extra; Natural Earth | The existing script: two regional maps per day drawn with `imshow`, a legend listing each level's share of area, and an "all clear" note when nothing is elevated. |
| [`author_hazard.py`](author_hazard.py) | `build` extra | Wraps its figures into `hazard_northeast.cstack` and `hazard_new_england.cstack`: an image grid slot (`image_extent`), a toggled `all_clear` layer, text slots for the title and the five shares. Saves the fixed regridding and zone weights the script recomputed every run to `hazard_aux.npz`. |
| [`update_hazard.py`](update_hazard.py) | runtime only | For each day: read, check and regrid the GeoTIFF, compute the shares, update both regions, write 14 PNGs. |

## What the examples show

- **Wrapping, not rewriting.** `author_qpf.py` and `author_hazard.py` call the existing
  script's drawing function; `wrap_script.py` runs a script as-is and catches its figure at
  `savefig`. Layers are picked by z-order bands and by the artists the script returns.
- **Replaceable data.** Grid values (`replace_grid`), polygons (`replace_polygons`) and text
  (`scene.text[...]`) change per product; everything else is reused as stored.
- **Images drawn with `imshow`.** A grid slot declared with `image_extent` reproduces
  Cartopy's regridding of an image to the map projection.
- **Fixed work done once.** A script that regrids the same raster grid or finds the same
  zones on every run can do that once at authoring time and save the result next to the
  scene. The runtime then needs a gather and a weighted `bincount`.
- **Several maps from one input.** One GeoTIFF updates two regional scenes.
- **Showing and hiding layers.** The "all clear" note is a static layer the runtime shows
  only when the data calls for it (`scene.show` / `scene.hide`).

## Running them

The fixtures (Natural Earth and the 18 WPC QPF products) come from
`scripts/fetch_fixtures.py`, which is one-time and needs the network. The hazard inputs
are synthetic and are written by `author_hazard.py`. Nothing below downloads anything.

```bash
uv run scripts/fetch_fixtures.py                     # once

# The existing scripts, as they would run without CartoStack (build environment)
uv run --extra build python examples/qpf_map.py benchmarks/data/qpf/day_* --out out/qpf-mpl

# Author (build environment)
uv run --extra build python examples/author_grid.py out/grid.cstack --inputs out/grid-inputs
uv run --extra build python examples/author_qpf.py out/qpf.cstack
uv run --extra build python examples/wrap_script.py out/wrapped.cstack examples/qpf_map.py \
    benchmarks/data/qpf/day_1-3
uv run --extra build python examples/author_hazard.py out/hazard --inputs out/hazard-inputs

# Update (any environment with cartostack, NumPy and Pillow)
python examples/update_grid.py out/grid.cstack out/grid-inputs/*.npy --out out/png
python examples/update_grid.py out/grid.cstack out/grid-inputs/t2m_18z.npy --out out/png \
    --vmin 0 --vmax 40 --subtitle "Valid 2026-10-09 18:00 UTC"
python examples/update_qpf.py out/qpf.cstack benchmarks/data/qpf/day_* --out out/png
python examples/update_hazard.py out/hazard out/hazard-inputs/*.tif --out out/png
```

The update scripts are shaped like a scheduled job: load the files once, then loop over
the inputs. Only the slots that change are re-rendered; static layers are reused as stored.

## Running on a schedule

Author on a machine with the `build` extra, whenever the map layout changes. Copy the
`.cstack` files (and any saved precomputation, such as `hazard_aux.npz`) to wherever the
job runs. That machine, or a slim container image, needs only:

```bash
pip install cartostack        # NumPy and Pillow; no Matplotlib, Cartopy or shapefiles
```

A cron entry then runs the update scripts directly, for example every 30 minutes:

```cron
*/30 * * * *  python /opt/maps/update_hazard.py /opt/maps/hazard /data/hazard/*.tif --out /srv/maps
*/30 * * * *  python /opt/maps/update_qpf.py /opt/maps/qpf.cstack /data/qpf/day_* --out /srv/maps
```

`benchmarks/cron_jobs.py` times this pattern against the original scripts.
