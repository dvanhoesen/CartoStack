# CartoStack API reference

CartoStack has two halves:

- **The runtime**, `import cartostack`. It needs only NumPy and Pillow. It loads a `.cstack`
  file, swaps in new data, stacks the layers and writes PNG.
- **Authoring**, `cartostack.build`. This needs the `build` extra (Matplotlib ≥ 3.11.1 and
  Cartopy). It turns a Matplotlib/Cartopy figure into a `.cstack` file once.

`import cartostack` never imports Matplotlib, Cartopy, pyproj or shapely. The file
format is specified in [format.md](format.md). Runnable examples are in
[`examples/`](../examples/README.md).

## Installation

```bash
pip install cartostack            # runtime: NumPy ≥ 2.0, Pillow ≥ 10.1, Python ≥ 3.12
pip install "cartostack[build]"   # authoring: adds Matplotlib ≥ 3.11.1 and Cartopy ≥ 0.26 (with pyproj)
```

Runtime machines need only the first.

## Runtime: `cartostack.Scene`

```python
from cartostack import Scene

with Scene.load("qpf.cstack") as scene:          # read once
    for product in products:                     # then one PNG per product
        scene.replace_polygons("qpf", product.rings, product.values)
        scene.text["subtitle"] = product.subtitle
        scene.save_png(f"qpf_{product.name}.png")
```

A scene is a fixed canvas geometry plus an ordered stack of layers and embedded assets
(fonts). Layers are immutable objects. Every edit swaps in a new, validated layer and
returns it. An edit that fails raises an error and leaves the scene unchanged.

### Loading, saving, lifetime

| Call | Does |
| --- | --- |
| `Scene.load(path)` | Reads, verifies (SHA-256 of every member, the manifest schema, geometry) and decodes the whole file, then closes it. |
| `scene.save(path, *, encoding=None)` | Writes atomically and deterministically: the same scene gives the same bytes. Only pixels that changed since loading or the last save are re-encoded. `encoding` overrides the layer encodings (`"rgba8+zlib"` or `"png"`). |
| `scene.close()`, `with Scene.load(...) as scene:` | Releases nothing; the scene stays usable (see below). |
| `scene.to_manifest()` | The manifest dict and member bytes `save` would write. |

**Lifetime.** A loaded scene is plain in-memory data and holds no open file. Its file
may be moved, deleted or overwritten, including by saving the scene back to the same
path. `close()` and the context manager exist so that code keeps working if a later
version loads lazily.

### Updating data slots

| Call | Slot kind | Notes |
| --- | --- | --- |
| `scene.replace_grid(id, values, *, vmin=None, vmax=None, lut=None)` | grid | `values` has the slot's `(ny, nx)` shape; NaN and masked values take the `bad` color. `vmin`/`vmax`/`lut` change the color range or colormap from now on; colorbars drawn from the slot are redrawn. Raises `GridMismatchError`. |
| `scene.replace_polygons(id, records, values)` | polygon | One entry per polygon: its rings of `(lon, lat)` degrees, plus one value per polygon, classified by the slot's bins. Raises `PolygonDataError`, `ProjectionError`. |
| `scene.text[id] = "…"`, or `scene.replace_text(id, value)` | text | One line, rendered with the embedded font and laid out the way Matplotlib 3.11 lays it out. Reading `scene.text[id]` returns the current string. Raises `TextFontError`. |
| `scene.redraw_colorbar(id)`, `scene.stale_colorbars` | colorbar | Redraws happen automatically; `stale_colorbars` lists colorbars that could not be redrawn, either because the file holds no redraw data or because the new range needs an offset or scientific notation. |

`cartostack.polygons.records_from_features(features, value)` turns GeoJSON-like
features (a `FeatureCollection`, anything with `__geo_interface__` such as a GeoPandas
`GeoDataFrame`, or an iterable of features) into `(records, values)`. `value` names the
property to use, or is a function of each feature.

Only the slot you update is re-rendered. Static layers and runs of static layers between
slots are composited once and reused.

### Editing layers

| Call | Does |
| --- | --- |
| `scene[id]`, `id in scene`, `len(scene)`, `iter(scene)` | Look up layers (in list order). |
| `scene.draw_order()` | Layers bottom to top: ascending `order`, ties in list order. |
| `scene.add_layer(id, pixels, *, order=None, left=0, top=0, visible=True, opacity=1.0)` | Adds an RGBA raster layer of any size, placed at `left`/`top` canvas pixels. `order` defaults to the top of the stack. Also accepts a layer object. |
| `scene.replace_layer(id, new)`, `scene[id] = new` | Replaces a layer's pixels (an array) or swaps in a whole layer object. |
| `scene.remove_layer(id)`, `del scene[id]` | Removes a layer. |
| `scene.update_layer(id, **changes)` | Changes `order`, `left`, `top`, `visible`, `opacity` or `encoding`. Styling the file does not store is refused with an explanation. |
| `scene.show(id)`, `scene.hide(id)` | Visibility. Hidden layers stay in the file. |

The geometry (canvas size, DPI, axes, projection, extent, clip) is fixed. Layers that do
not fit it are refused (`SceneError`, `LayerError`) rather than misaligned.

### Output

| Call | Does |
| --- | --- |
| `scene.render(*, flatten=True)` | A new `(height, width, 4)` uint8 RGBA array of the visible layers. |
| `scene.save_png(path, *, mode="RGBA", compress_level=6, background=(255, 255, 255))` | Renders and writes a PNG atomically. `mode="RGB"` composites over `background` first. `compress_level` runs from 0 (fastest) to 9 (smallest). |
| `scene.shape`, `scene.geometry`, `scene.layers`, `scene.assets`, `scene.provenance` | Read-only views. |

### Layer types

`RasterLayer` (static pixels), `GridSlot`, `PolygonSlot`, `TextSlot` and `ColorbarLayer`
are frozen dataclasses sharing `id`, `order`, `left`, `top`, `visible`, `opacity`,
`pixels` and `encoding`. Their arrays are read-only. Constructing one directly is
possible but rarely needed: files come from `SceneBuilder`, and edits go through the
`Scene` methods.

### Errors

Every runtime error is a `cartostack.CartoStackError`, and most are also `ValueError`s:

- **Reading files:** `FormatError` (an invalid or corrupt file), `UnsupportedVersionError`, `GeometryError`.
- **Editing scenes:** `SceneError`, `LayerError`.
- **Updating slots:** `GridMismatchError`, `PolygonDataError`, `ProjectionError`, `TextFontError`, `ColorbarRedrawError`.

## Authoring: `cartostack.build.SceneBuilder`

You don't rewrite your plotting script; you wrap the figure it already makes. The usual
path takes the figure an existing script draws, unchanged, and assigns each part to a
layer:

```python
import math
from cartostack.build import SceneBuilder

fig, ax = draw_my_map(first_product)             # unchanged Matplotlib/Cartopy code

b = SceneBuilder(fig, ax, dpi=300, crop="tight")
b.add_static("below", order=0, background=True, zorder=(-math.inf, 10))
b.add_polygon_slot("qpf", order=10, bins=QPF_BINS, fallback=("gray", 10),
                   round_decimals=2, artists=data_polygons)
b.add_static("above", order=20, zorder=(10, math.inf))
b.add_text_slot("subtitle", subtitle_artist, order=30)
b.save("qpf.cstack")
```

`SceneBuilder.new(projection, extent, *, width, height, dpi=100, axes=(0, 0, 1, 1),
frame=False, crop=None)` creates a figure with one map axes instead.

| Method | Declares |
| --- | --- |
| `add_static(id, *, order, draw=None, artists=(), zorder=None, background=False, opacity=1.0)` | A raster layer of artists: those `draw(ax)` creates, those listed in `artists`, and/or the map-axes artists with `lo <= zorder < hi`. One static layer may take the figure and axes background. |
| `add_grid_slot(id, *, order, lon, lat, cmap, vmin/vmax or norm, alpha=None, extend="neither", cells="centers", values=None, artists=())` | A grid slot. The pixel→cell index map is made by drawing the grid once with Matplotlib; the colormap LUT is rounded the same way Agg rounds colors. `norm` must be a plain linear `Normalize` without `clip`. |
| `add_grid_slot(id, *, order, image_extent, image_shape=None, origin="upper", regrid_shape=750, cmap, ...)` | A grid slot for an image drawn with `ax.imshow(..., transform=ccrs.PlateCarree(), interpolation="none")`: pass the same extent (`left, right, bottom, top` in degrees), `origin` and `regrid_shape` instead of `lon`/`lat`. The index map is made through the same `imshow` call, including Cartopy's regridding to the map projection (which needs SciPy or pykdtree, as `imshow` itself does). The shape comes from `image_shape` or `values`. |
| `add_polygon_slot(id, *, order, bins, fallback=None, round_decimals=None, supersample=4, records=None, values=None, artists=())` | A polygon slot. `bins` are `(lower, upper, color, draw order)` rows. |
| `add_text_slot(id, artist=None, *, order, **text)` | A text slot from a `Text` artist (font, size, color, anchor) or from `fig.text` keywords. Its font file is embedded. |
| `add_colorbar(id, colorbar, *, slot, order)` | A colorbar layer that the runtime can redraw when `slot`'s range or colormap changes. |
| `build(*, rest=None, encoding="rgba8+zlib", provenance=None)` / `save(path, **kw)` | Renders every layer and returns, or also saves, the `Scene`. |

Layer ids are 1–64 lowercase letters, digits, `_` or `-`, starting with a letter or digit;
an invalid id is refused when the layer is declared.

Every visible artist must belong to exactly one layer. An unassigned artist is an error
unless `rest="<static id>"` names a layer to collect them. The builder warns when the
layer order would draw overlapping artists in a different order than Matplotlib.

**Embedded fonts.** Each text slot embeds the exact font file Matplotlib resolved for
its artist (DejaVu Sans by default). Make sure the font's license allows embedding
before using another font.

## Tips for scheduled jobs

- **Load once, loop.** Load each `.cstack` file once per process and update it for every
  product; static layers are composited once and reused.
- **PNG encoding is usually the largest remaining cost.** `save_png(compress_level=1)` is
  about a quarter faster than the default level 6 on the example maps, for files about a
  third larger.
- **Precompute fixed work.** If a script regrids rasters on a fixed grid or computes
  statistics over fixed zones every run, compute the index or the weights once while
  authoring and save them next to the scene (`examples/author_hazard.py`).
- **Conditional annotations.** Author notes that appear only sometimes as their own static
  layer and toggle them with `scene.show` / `scene.hide`.
- **Changing layout.** Text slots keep their anchor. A layout that moves with the data
  (for example a box sized to its widest label) can be authored once per variant, as
  separate layers, with the matching one shown.

## Limitations (format 1.0)

- **Polygon slots** support only the Lambert Conformal Conic projection; the runtime
  projects with NumPy instead of pyproj. Static layers and grid slots work with any
  projection Cartopy draws.
- **Grid slots** support only a linear `vmin`/`vmax` range (no log, boundary or
  two-slope norms) on a fixed grid. Integer categories drawn with a `BoundaryNorm` of
  unit-wide bins map exactly onto a linear range from `-0.5` to `n - 0.5`. The values' shape must match the shape the slot
  was authored with.
- **Text slots** hold one unrotated line, with no mathtext or usetex. Shaping covers
  ligatures and pair kerning only.
- **Colorbar redraw** needs Matplotlib's default locator (or fixed ticks) and a
  `ScalarFormatter`. Ranges that would need an offset or scientific notation leave the
  colorbar in `stale_colorbars`.
- **Not yet supported:** contour, point or vector slots; tiling; native or GPU
  backends. See [design.md](design.md) → "Future directions".
