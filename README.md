# CartoStack

> **Name.** Distribution and import name `cartostack` (`pip install cartostack`, `import cartostack`); scene files use the `.cstack` extension. Chosen 2026-10-08, replacing the working name "GeoScene", which conflicts with Esri China's GIS platform brand. `cartostack` was unregistered on PyPI when chosen.

CartoStack is a proposed high-performance Python package for building and updating production geoscience maps without redrawing the entire figure on every run.

**Implementation tracker:** [SESSIONS.md](SESSIONS.md) breaks the build into individually executable sessions with checklists, verification gates, and a completion log. Update that tracker at every implementation-session handoff.

The primary use case is operational map generation where most of the cartographic scene is unchanged between products:

- projection
- geographic extent
- figure dimensions
- terrain or basemap
- coastlines
- country, state, and county boundaries
- lakes, rivers, and other water bodies
- labels
- logos
- static points and annotations
- layout
- map decorations

while only a small number of components change:

- gridded model output
- radar or satellite data
- contours
- observations
- station values
- title
- subtitle
- valid time
- legend
- colorbar
- other dynamic annotations

Traditional Matplotlib/Cartopy workflows often rebuild the entire map from source data for every product. That means repeatedly loading shapefiles or GeoJSON files, transforming geometries, constructing Matplotlib artists, drawing static text, and rasterizing the same features over and over.

CartoStack takes a different approach:

> **Store the map as a layered image file — N×M RGBA layers plus metadata — and produce each product by loading that file, swapping in only the new data, and stacking the layers. The runtime needs only NumPy and Pillow; it never imports Matplotlib or Cartopy.**

The `.cstack` file is the product. It records the canvas size, the map geometry, how geographic data maps to pixels, and a description of every layer. Matplotlib and Cartopy are used once, on a build machine, to author the file. After that, the original shapefiles, GeoJSON, Natural Earth data, and plotting libraries are no longer needed to make new maps.

Speed comes from what the runtime does *not* do: no heavyweight imports, no source reads, no projection, no artist construction. It does not come from caching inside a Matplotlib workflow.

The public interface will be Python. The internal implementation is not required to be pure Python. Performance-sensitive components may eventually use Rust, C, C++, SIMD, GPU acceleration, memory mapping, or other optimized implementations where they provide meaningful benefits.

---

## Goals

CartoStack should make production geoscience plotting substantially faster by turning the map into a file that is cheap to load and edit.

Primary goals:

1. **Define a self-contained, versioned layered map file (`.cstack`).**
2. **Load, edit, and render it with only NumPy and Pillow.**
3. **Swap in new data — grid values, text, prepared images — without touching other layers.**
4. **Render new grid values in pixel space using mappings stored in the file.**
5. **Treat text and annotations as independently replaceable layers.**
6. **Keep Matplotlib and Cartopy as build-time authoring tools only.**
7. **Support deterministic rendering for cron jobs, containers, and batch pipelines.**
8. **Keep the file inspectable with ordinary tools (ZIP, JSON, PNG).**
9. **Allow optimized native backends without changing the Python API.**
10. **Produce output that matches an equivalent full Cartopy render within documented tolerances.**

---

## Non-goals

At least initially, CartoStack should not attempt to:

- serialize arbitrary live Matplotlib figures
- preserve every possible Matplotlib artist
- behave like a general-purpose plotting library
- replace Cartopy
- replace Matplotlib
- support arbitrary resizing of already-compiled raster layers
- support arbitrary projection changes without recompilation
- support interactive pan/zoom as a primary use case
- reproduce arbitrary vector-editing workflows
- guarantee that compiled scenes remain editable in every possible way

The first target should be **fixed-layout production maps**.

---

## Prior art and alternatives

Layered images and redrawing only what changed are well established. CartoStack's value lies in a portable map file with a tiny runtime for Cartopy-style operational maps, not in a new rendering technique. Related work to borrow from and benchmark against:

- **OpenRaster (`.ora`).** A ZIP of PNG layers plus an XML stack description, used by GIMP and Krita. The `.cstack` container follows the same idea, with JSON metadata and map-specific layer kinds.
- **GeoTIFF.** Established conventions for storing a CRS and an affine transform between projected coordinates and pixels; `.cstack` georeferencing follows them.

- **Matplotlib blitting.** `canvas.copy_from_bbox()` / `restore_region()` plus `ax.draw_artist()` already reuse a rendered background inside one long-lived process. It does not persist across processes and does not handle static layers *above* the data without extra work, but it is the zero-new-code baseline.
- **pyresample / Satpy.** Precomputed resampling information (`get_neighbour_info`, bilinear and gradient-search resamplers) is the same idea as CartoStack's grid slots, already implemented natively for geostationary/polar and model grids.
- **Datashader, rasterio/GDAL warp.** Pixel-space aggregation and reprojection to a fixed target grid.
- **Tile servers and render engines.** Layered rasters, dirty regions, and composite trees are standard in web map tile renderers, browsers, and game engines.

---

# Core concept

CartoStack separates two stages that ordinary plotting scripts mix together.

## Build (once, on a machine with Matplotlib and Cartopy)

```text
shapefiles / Natural Earth / styles / grid coordinates
        ↓
Matplotlib + Cartopy on a fixed canvas
        ↓
RGBA layers + grid index maps + text definitions + georeferencing
        ↓
northeast.cstack
```

Building may be slow. It happens once per map template.

## Runtime (every product, NumPy and Pillow only)

```text
northeast.cstack ──► manifest + needed layers
new grid values ──► index-map gather → normalize → colormap ──► data layer
new text        ──► Pillow/FreeType with the embedded font ──► text layer
                                   ↓
                     stack layers (alpha compositing)
                                   ↓
                                  PNG
```

Each step is a few array operations. Stacking already-rendered layers is far cheaper than rebuilding a map.

The achievable speedup is bounded by what still runs for every product: process start-up and imports, reading the file, rendering the changed layers, stacking, and PNG encoding. Keeping Matplotlib and Cartopy out of the runtime matters as much as the rendering itself, because importing them can cost a substantial fraction of a second in a fresh cron process. PNG encoding is likely to become the largest remaining cost.

---

# Example

A traditional production map may do this on every cron run:

```python
fig = plt.figure(figsize=(12, 8))

ax = plt.axes(
    projection=ccrs.LambertConformal(...)
)

ax.set_extent(...)

ax.add_feature(cfeature.LAND)
ax.add_feature(cfeature.OCEAN)
ax.add_feature(cfeature.LAKES)

ax.add_geometries(load_counties(...))
ax.add_geometries(load_states(...))
ax.add_geometries(load_countries(...))

ax.pcolormesh(
    lon,
    lat,
    temperature,
    transform=ccrs.PlateCarree(),
)

ax.set_title(...)

plt.savefig(...)
```

Even when the counties, states, lakes, projection, and layout are identical to the previous run.

With CartoStack:

```python
from cartostack import Scene

scene = Scene.load("northeast.cstack")

scene.replace_grid(
    "temperature",
    data=t2m,
    grid="hrrr",
    cmap="coolwarm",
    vmin=-30,
    vmax=40,
)

scene.text["title"] = "2-m Temperature"
scene.text["valid_time"] = "Valid 18 UTC"

scene.save_png("temperature.png")
```

This script imports only NumPy and Pillow. The static layers are never reconstructed, and the temperature field is placed using the index map stored in the file.

---

# Scene architecture

A `.cstack` file contains everything required to reproduce and update the map.

```text
Scene (runtime: NumPy + Pillow)
│
├── Canvas geometry
│   ├── width, height, DPI
│   ├── axes rectangle (pixels)
│   └── clipping
│
├── Georeferencing
│   ├── CRS (PROJ/WKT + plain parameters)
│   ├── projected extent
│   └── affine transform: projected coordinates ↔ pixels
│
├── Layers (ordered)
│   ├── RasterLayer      static or replaced RGBA image, full-canvas or cropped
│   ├── GridSlot         index map + normalization + colormap LUT
│   ├── TextSlot         text, style, embedded font, rendered pixels
│   └── ColorbarLayer    tied to a grid slot's normalization
│
├── Compositor
└── Reader / writer

SceneBuilder (build machine: + Matplotlib + Cartopy)
├── fixed-geometry figure construction
├── static layer capture
├── index-map generation
└── georeferencing export
```

---

# Layer model

Everything that can change independently should be represented as an independent layer.

Example:

```text
00 background
05 terrain
10 land/ocean
20 dynamic model field
30 county borders
31 state borders
32 country borders
35 water outlines
40 city markers
41 city labels
50 station observations
80 annotations
90 title
91 subtitle
92 valid time
93 logo
94 legend
95 colorbar
```

If only the model field and valid time change, only those layers need to be redrawn.

---

# Raster layers

The simplest persistent representation is an RGBA image.

```python
RasterLayer(
    id="counties",
    zorder=30,
    image=rgba,
)
```

Advantages:

- no shapefile required at runtime
- no Cartopy required at runtime
- no geometry parsing
- no projection work
- no Matplotlib artist construction
- very fast compositing
- simple serialization

Raster layers should be the default representation for static features.

---

# Sparse raster layers

Not every layer needs to occupy the full canvas.

A title might occupy only:

```text
x = 400
y = 20
width = 800
height = 60
```

Instead of storing a mostly transparent full-resolution image, CartoStack should eventually support cropped layers:

```python
RasterLayer(
    id="title",
    image=title_rgba,
    x=400,
    y=20,
)
```

This can significantly reduce memory usage and serialized scene size.

Useful candidates include:

- titles
- subtitles
- legends
- logos
- labels
- station annotations
- timestamps

---

# Compiled vector layers

Some layers may need to remain editable without requiring their original source data.

For example, users may want to change state-border line width after compilation.

Instead of retaining the original shapefile, CartoStack could optionally persist already-projected drawing primitives.

```text
Shapefile
    ↓
read once
    ↓
project once
    ↓
compiled paths
    ↓
save paths
```

A `CompiledVectorLayer` could store:

- projected path vertices
- polygon rings
- clipping information
- line width
- line style
- fill color
- edge color
- alpha
- z-order

The original shapefile would no longer be required.

This creates two persistent compilation levels:

```text
source geometry
      ↓
CompiledVectorLayer
      ↓
RasterLayer
      ↓
final output
```

The user can choose between maximum speed and greater editability.

---

# Fixed scene geometry

All independently rendered layers must share the exact same geometry.

The scene should own:

```text
output width
output height
DPI
axes location
axes dimensions
projection
geographic extent
projected extent
aspect ratio
clipping region
```

The critical invariant is:

> Pixel `(x, y)` must refer to the same geographic and figure location for every layer.

This guarantees that layers can be rendered independently and composited later.

Changes to fundamental scene geometry should invalidate affected compiled layers.

Examples:

```python
scene.extent = ...
scene.projection = ...
scene.width = ...
```

should normally require recompilation.

The library should fail explicitly rather than silently misalign layers.

---

# Cartopy integration

Cartopy is an **authoring tool**, used only by `SceneBuilder` on the build machine. It is never part of the saved scene and never imported at runtime.

At build time, CartoStack creates a transparent figure with the scene's fixed size, DPI, and resolved axes position, lets the caller draw into it, and captures the result as an RGBA layer:

```python
builder.add_static("states", draw=lambda ax: ax.add_feature(cfeature.STATES, lw=0.6))
```

Internally:

```text
canvas geometry
        ↓
temporary GeoAxes
        ↓
caller draws one layer
        ↓
extract transparent RGBA buffer
        ↓
discard figure
        ↓
store layer in the file
```

The same machinery generates grid-slot index maps and exports the projection and pixel transform. Dynamic content that genuinely needs Matplotlib every product (for example contour lines with labels) can still be drawn on a build-style machine and dropped into the file as a replaced raster layer.

---

# Text as layers

Text is handled like any other layer.

```python
scene.text["title"] = "Surface Temperature"
scene.text["subtitle"] = "HRRR 3-km"
scene.text["valid_time"] = "Valid 2026-10-06 18 UTC"
```

A text slot stores the current string, its style (size, color, anchor, alignment), the font file embedded in the `.cstack`, and the rendered pixels. Changing text renders only that slot, using Pillow/FreeType, so no Matplotlib is needed at runtime. Pillow and Matplotlib text differ slightly even with the same font; those differences are measured and documented. Mathtext is not supported in runtime text slots.

Static labels (city names, state abbreviations, disclaimers, branding) are simply raster layers captured at build time.

---

# Layer groups

Scenes may contain many logical layers.

Grouping can simplify both API design and compositing.

```text
BASE
├── terrain
├── land
└── ocean

DATA
└── temperature

BOUNDARIES
├── counties
├── states
└── countries

ANNOTATIONS
├── city markers
└── city labels

TEXT
├── title
├── subtitle
└── valid time
```

Groups can also support precomposited caches.

If only `DATA` changes:

```text
BASE composite          cached
DATA                    redraw
BOUNDARIES composite    cached
ANNOTATIONS composite   cached
TEXT composite          cached
```

The final image may require only a handful of alpha-compositing operations.

---

# The `.cstack` file format

The file format is the center of the project. It is versioned, documented in `docs/format.md`, and readable with ordinary tools. Python pickle, live Matplotlib figures, and serialized callbacks are never stored.

Version 1 is a ZIP container, in the spirit of OpenRaster:

```text
northeast.cstack
│
├── manifest.json          format version, canvas geometry, georeferencing, layer records
│
├── layers/
│   ├── 010_land_ocean.png
│   ├── 030_borders.png
│   ├── 090_title.png
│   └── 095_colorbar.png
│
├── grids/
│   ├── temperature.index.zst    int32 pixel→cell index map (−1 = no data)
│   └── temperature.lut.png      256-entry colormap lookup table
│
└── fonts/
    └── DejaVuSans.ttf
```

Design rules:

- Members are stored uncompressed in the ZIP (`ZIP_STORED`); each member's own encoding (PNG, zstd-compressed raw array) determines decode cost, and the encoding is recorded per layer so several can coexist.
- Layer encoding defaults are chosen from benchmarks: PNG is compact and inspectable; raw arrays with zstd or LZ4 decode much faster.
- Every layer shares the canvas geometry. Layers may be cropped and placed at an integer pixel offset.
- Readers reject unknown major versions and ignore unknown optional fields within a major version.
- Saves are atomic (write to a temporary file, then rename), so a failed save never corrupts a template.

A later version could move to an indexed binary container if profiling shows ZIP overhead matters.

---

# Scene metadata

Example `manifest.json` (field names are provisional until the format specification session):

```json
{
  "format_version": "1.0",
  "canvas": {
    "width": 1200,
    "height": 800,
    "dpi": 100,
    "axes_bbox_px": [60, 70, 1140, 690]
  },
  "georeference": {
    "crs_wkt": "PROJCRS[\"Lambert Conformal Conic\", ...]",
    "projection": {
      "type": "LambertConformal",
      "central_longitude": -74.0,
      "central_latitude": 42.0,
      "standard_parallels": [39.0, 45.0]
    },
    "geographic_extent": [-82, -66, 37, 48],
    "pixel_transform": [1234.5, 0.0, -650000.0, 0.0, -1234.5, 520000.0]
  },
  "layers": [
    {"id": "land_ocean", "kind": "raster", "order": 10,
     "member": "layers/010_land_ocean.png", "encoding": "png"},
    {"id": "temperature", "kind": "grid", "order": 20, "alpha": 0.8,
     "grid_shape": [500, 500], "index_map": "grids/temperature.index.zst",
     "vmin": -30, "vmax": 40, "lut": "grids/temperature.lut.png"},
    {"id": "borders", "kind": "raster", "order": 30,
     "member": "layers/030_borders.png", "encoding": "png"},
    {"id": "title", "kind": "text", "order": 90, "text": "",
     "font": "fonts/DejaVuSans.ttf", "size": 16, "color": "#000000",
     "anchor": "ms", "position": [600, 40], "member": "layers/090_title.png"}
  ],
  "provenance": {"cartostack": "0.1.0", "matplotlib": "3.11.2", "cartopy": "0.26.0"}
}
```

---

# Lazy loading

Scene loading should support lazy access.

```python
scene = Scene.load(
    "northeast.cstack",
    lazy=True,
)
```

Initially:

```text
metadata             loaded
layer index          loaded
projection metadata  loaded

terrain              not decoded
counties             not decoded
states               not decoded
labels               not decoded
```

If a layer is immediately replaced, the previous layer may never need to be decompressed.

Lazy loading should be a core design goal because scenes may eventually contain dozens or hundreds of layers.

---

# Memory mapping

For large operational products, CartoStack should investigate memory-mapped persistent data.

Potential uses:

- uncompressed or block-compressed RGBA buffers
- projection lookup tables
- precomputed index maps
- compiled path arrays
- large grid caches

This could allow large scenes to be opened without reading all contents into RAM.

---

# Grid slots and georeferencing

A grid slot lets the runtime place new values from a fixed model or observation grid without projecting anything.

At build time, the caller registers the grid's coordinates:

```python
builder.add_grid_slot("temperature", lon=lon, lat=lat, cmap="coolwarm", vmin=-30, vmax=40)
```

CartoStack computes, once, which grid cell covers each canvas pixel and stores that `int32` index map in the file, with the normalization and colormap. This is part of the file's georeferencing, not a cache: it is what makes the file self-sufficient.

Each product then supplies only values:

```python
scene.replace_grid("temperature", t2m)
```

No longitude/latitude transformation, Cartopy, or Matplotlib is involved.

The file also stores the CRS and an affine transform between projected coordinates and pixels. That is enough for a later extension to place new points or polygons at runtime with pyproj and Pillow, without Cartopy.

---

# Pixel-space rendering

The runtime pipeline for a grid slot:

```text
new model values
      ↓
gather through stored index map
      ↓
normalize (vmin / vmax)
      ↓
colormap lookup table
      ↓
apply layer alpha, mask no-data
      ↓
RGBA layer
      ↓
compositor
      ↓
PNG
```

For a 1200 × 800 canvas this is a handful of NumPy operations on about a million elements.

---

# Grid resampling

Different grid types may require different cached mappings.

Examples:

- regular latitude/longitude
- projected rectilinear grids
- curvilinear model grids
- radar polar grids
- satellite swaths
- unstructured grids

Possible strategies include:

- nearest-neighbor lookup tables
- bilinear interpolation weights
- triangle lookup tables
- scanline rasterization
- precomputed sparse interpolation matrices
- texture sampling on the GPU

The appropriate implementation should be chosen based on profiling.

For flat-shaded cell data (what `pcolormesh` draws), a pixel→cell index map can be produced with Matplotlib's own rasterization rules: draw the grid once with `pcolormesh(antialiased=False, linewidth=0)` using per-cell face colors that encode each cell's flat index (24 bits in RGB, alpha marking "no cell"), then decode the image into an `int32` index array. Each update is then `values.ravel()[index]`, normalization, and a colormap lookup table — pure NumPy, no Cartopy, and pixel-equivalent to `pcolormesh` away from anti-aliased domain edges. Interpolating modes (bilinear, gradient search) have different semantics and need their own references; pyresample is the first candidate for those.

Not every dynamic layer fits this model. Contour lines with labels, wind barbs, and station plots depend on the values themselves and still need a Matplotlib render each product; for those, CartoStack saves only the static-layer work.

---

# Native backend strategy

The Python API should not constrain the internal implementation language.

## Python layer

Python should handle:

- user-facing API
- configuration
- object lifecycle
- integration with NumPy
- integration with xarray
- integration with Matplotlib
- integration with Cartopy
- plugin-style renderers
- metadata
- errors and validation

## Performance-critical backend

Candidates include:

### Rust

Strong candidate for:

- alpha compositing
- layer decoding
- binary serialization
- grid index-map remapping
- image processing
- multithreaded rendering
- SIMD
- safe memory management

Python bindings could use PyO3 and maturin.

### C / C++

Possible for:

- low-level rasterization
- image compositing
- existing rendering libraries
- interoperability with GEOS/GDAL/PROJ
- SIMD-heavy operations

### NumPy

Should remain the default baseline implementation where it is already sufficiently fast.

### Numba

Could be useful during early development for:

- grid remapping kernels
- alpha compositing experiments
- interpolation
- prototype rasterizers

### GPU

Optional future backend for very large datasets.

Potential technologies:

- CUDA
- CuPy
- OpenCL
- Vulkan compute
- wgpu
- WebGPU

GPU acceleration should only be added if real workloads justify the additional complexity.

Expectation to test, not assume: for the Small and Medium scenes, NumPy gathers/lookup tables plus Pillow's C compositor and encoder are likely fast enough that PNG encoding and process start-up, not CartoStack's own code, dominate the recurring cost. Native code is most likely to pay off for the Large scenes and for encoding (for example zlib-ng-based or fpng-style encoders) rather than for compositing a handful of buffers.

---

# External native libraries

CartoStack should reuse optimized native libraries where appropriate rather than reimplementing everything.

Possible dependencies include:

- PROJ for coordinate transformations
- GEOS for geometry operations
- GDAL where dataset support is necessary
- libpng
- libwebp
- zstd
- lz4
- image-rs
- resvg
- Skia
- Cairo

Dependencies should be introduced only when benchmarks show clear value.

---

# Compositor

The compositor is central to CartoStack.

At minimum it must support:

- RGBA alpha blending
- layer ordering
- visibility
- opacity
- clipped layers
- cropped sparse layers
- a documented alpha convention

Stored layers use straight (non-premultiplied) `uint8` RGBA, matching Matplotlib Agg's `buffer_rgba()`, PNG, and Pillow's `Image.alpha_composite`. Compositing independently rendered layers is equivalent to drawing them on one canvas up to small rounding differences, which tests should quantify. A native compositor may convert to premultiplied internally.

A simple initial implementation can use NumPy or Pillow. Contiguous runs of unchanged layers should be flattened into cached composites early, so a typical update composites only a few buffers regardless of how many static layers the scene has.

A native implementation should eventually support:

- SIMD
- multithreading
- zero-copy buffers
- premultiplied alpha
- block compositing
- dirty rectangles

---

# Dirty rectangles

When a small layer changes, the package should eventually avoid recompositing the entire output.

Example:

```text
title changed

affected area:
x = 300..1300
y = 10..80
```

Only that rectangle needs to be recomposited.

This is particularly useful for:

- timestamps
- titles
- logos
- station values
- legends
- small annotations

The first version does not need this optimization, but the layer API should not prevent it.

---

# Precomposed layer trees

For scenes with many layers, CartoStack can cache intermediate composites.

Example:

```text
                    final
                   /     \
               lower     upper
              /    \     /   \
           base   data  borders text
```

If only `data` changes, the rest of the tree remains valid.

This resembles a render tree or scene graph used in browser and graphics engines.

A balanced composite tree could make updates scale approximately with the number of modified branches rather than the total number of layers.

---

# Compression

Different layer types may benefit from different storage strategies.

## PNG

Good for:

- transparency
- labels
- line work
- sparse graphics

## WebP lossless

Potentially useful for:

- static raster layers
- smaller scene archives

## Zstandard

Useful for:

- raw arrays
- projection tables
- compiled vector data
- memory blocks

## LZ4

Useful when decompression speed is more important than maximum compression ratio.

Compression choices should be benchmark-driven.

---

# Avoiding unnecessary copies

Large images can easily consume substantial memory.

For example:

```text
4000 × 3000 × 4 bytes ≈ 48 MB
```

Ten full-resolution RGBA layers could therefore require hundreds of megabytes.

The implementation should minimize:

- temporary arrays
- array copies
- Python object overhead
- repeated color conversions
- repeated decompression

Important techniques may include:

- memory views
- zero-copy NumPy integration
- memory mapping
- premultiplied alpha buffers
- sparse layer storage
- tiled layers

---

# Tiled scenes

Large maps may benefit from tile-based storage.

Instead of:

```text
one 8000 × 6000 image
```

store:

```text
256 × 256
or
512 × 512
tiles
```

Benefits:

- load only touched regions
- change only affected tiles
- parallel decode
- parallel compositing
- lower peak memory
- efficient dirty-region updates

Tiling should probably be a later optimization rather than part of the MVP.

---

# Static source compilation

Authoring happens on the build machine (`cartostack[build]`). The builder API may look like:

```python
from cartostack.build import SceneBuilder

builder = SceneBuilder(
    projection=LambertConformalSpec(central_longitude=-74, central_latitude=42),
    extent=(-82, -66, 37, 48),
    size=(1200, 800),
    dpi=100,
)

builder.add_static("land_ocean", draw=draw_land_ocean, order=10)
builder.add_static("lakes", draw=lambda ax: ax.add_feature(cfeature.LAKES), order=15)
builder.add_grid_slot("temperature", lon=lon, lat=lat, order=20,
                      cmap="coolwarm", vmin=-30, vmax=40, alpha=0.8)
builder.add_static("counties", draw=lambda ax: ax.add_geometries(counties, ...), order=30)
builder.add_static("states", draw=draw_states, order=31)
builder.add_text_slot("title", position=(600, 40), font="DejaVuSans.ttf", size=16, order=90)
builder.add_colorbar("cbar", grid="temperature", order=95)

builder.save("northeast.cstack")
```

After building:

```text
counties.shp   no longer required
states.shp     no longer required
GeoJSON files  no longer required
Natural Earth  no longer required
Matplotlib     no longer required
Cartopy        no longer required
```

Source checksums and library versions may be recorded as optional provenance metadata.

---

# Runtime API

The operational API should be much smaller.

```python
from cartostack import Scene

scene = Scene.load("northeast.cstack")

scene.replace_grid(
    "temperature",
    temperature,
    grid="hrrr",
    cmap="coolwarm",
    vmin=-30,
    vmax=40,
)

scene.text["title"] = "2-m Temperature"
scene.text["valid_time"] = valid_time

scene.save_png("temperature.png")
```

Only the replaced layers should be rendered.

---

# Layer replacement

The core operation should be explicit.

```python
scene.replace_layer(
    "temperature",
    new_layer,
)
```

or:

```python
scene["temperature"] = new_layer
```

The package should not silently reconstruct neighboring layers.

---

# Adding layers

New layers can be inserted into an existing scene.

```python
scene.add_layer("logo", logo_rgba, order=93, x=1100, y=740)
scene.add_layer("warnings", warnings_rgba, order=45)
```

The core runtime accepts prepared RGBA images that match the canvas (or a cropped image with an offset). Layers produced from geographic coordinates — station plots or warning polygons — need a renderer: either draw them on a build-style machine, or, in a later extension, project them with pyproj using the file's stored georeferencing and draw them with Pillow.

---

# Removing layers

```python
scene.remove_layer("stations")
```

should require no rendering.

The layer is removed from the stack and the affected composite is rebuilt.

---

# Visibility

```python
scene["counties"].visible = False
```

should not require rerendering the county data.

The cached raster remains available and can be made visible again later.

---

# Style changes

Behavior should depend on representation.

For a `RasterLayer`:

```python
scene["states"].linewidth = 2
```

cannot be applied unless the layer retains compiled vector information.

For a `CompiledVectorLayer`, it can.

The API should make this distinction explicit.

---

# Runtime dependency levels

One distribution with optional extras:

```text
cartostack            (core: NumPy + Pillow)
```

- load and save `.cstack` files
- replace grid values, text, and prepared images
- add, remove, show/hide, and reorder layers
- redraw colorbars when normalization changes
- composite and write PNG

```text
cartostack[build]     (+ Matplotlib + Cartopy)
```

- author `.cstack` files with `SceneBuilder`
- capture static cartography and labels
- generate grid index maps and georeferencing

```text
cartostack[geo]       (+ pyproj, later)
```

- place new points and polygons at runtime from geographic coordinates

Production workers install only the core.

---

# Build-time vs runtime dependencies

One of the project's most useful operational properties could be:

## Build machine

Contains:

- Cartopy
- Shapely
- PyProj
- Fiona
- GeoPandas
- GDAL if needed
- source shapefiles
- GeoJSON
- Natural Earth datasets

## Runtime worker

May only require:

- `cartostack` (core)
- NumPy
- Pillow

for every update the file supports: grid values, text, colorbars, and prepared images.

This can significantly reduce container size and startup complexity.

---

# Compatibility checks

Every file records a deterministic fingerprint of its canvas geometry and georeferencing. Layers, grid slots, and colorbars carry the information needed to check compatibility:

```text
canvas size and DPI
axes rectangle
CRS and pixel transform
grid shape (for grid slots)
normalization and colormap (for colorbars)
font file (for text slots)
CartoStack format version
```

Replacing a layer with incompatible dimensions, supplying grid values of the wrong shape, or editing a file with an unknown format version fails with an explicit error rather than producing a misaligned map.

Changing grid values does not affect any other layer. Changing a grid's `vmin`, `vmax`, or colormap also redraws the colorbar that depends on it.

---

# Scene reproducibility

Compiled scenes should record enough metadata to reproduce their construction.

Examples:

```text
CartoStack version
renderer versions
projection parameters
source checksums
font names
DPI
dimensions
layer order
styles
compression format
```

This does not mean source files must be embedded.

The purpose is debugging and reproducibility.

---

# Threading and multiprocessing

Independent layers are naturally parallel.

During compilation:

```text
terrain   ─┐
counties  ─┼─ parallel render
states    ─┤
labels    ─┘
```

During runtime, multiple dynamic layers may also render simultaneously.

Native components should release the Python GIL where possible.

Matplotlib is not thread-safe, so parallel compilation of Matplotlib/Cartopy layers must use separate processes rather than threads.

The architecture should support:

- multiprocessing
- native thread pools
- async orchestration where useful

without requiring parallelism in the initial implementation.

---

# Cron and batch workflows

CartoStack is primarily intended for repeated automated production.

Example:

```bash
python make_temperature.py
python make_precipitation.py
python make_wind.py
```

Each script can load the same compiled base scene.

```text
northeast.cstack
     │
     ├── temperature product
     ├── precipitation product
     ├── wind product
     └── radar product
```

Static cartographic work is shared.

If many products run on the same schedule, a long-lived worker process that keeps the scene loaded avoids repeating interpreter start-up, imports, and scene decoding for every product. This may matter as much as any rendering optimization.

---

# Potential file workflow

A scene should support immutable templates.

```text
template.cstack
      ↓
load
      ↓
modify in memory
      ↓
output PNG
```

The original scene does not need to change.

Alternatively:

```python
scene.save("latest_temperature.cstack")
```

could persist updated dynamic layers for later reuse.

---

# Benchmark philosophy

Performance must be measured from the beginning.

Optimization should be based on real workloads rather than assumptions.

Every major development phase should include benchmarks.

---

# Initial benchmark suite

Create several realistic scenes.

## Small

```text
1200 × 800
10 layers
regional map
```

## Medium

```text
1920 × 1080
20 layers
state or multi-state map
```

## Large

```text
4000 × 3000
30+ layers
high-resolution production graphic
```

## Dynamic grid tests

Include:

- 500 × 500 grid
- 1000 × 1000 grid
- 3000 × 3000 grid
- realistic HRRR grid
- radar grid
- curvilinear satellite grid

---

# Benchmark operations

Measure independently:

```text
scene load
single-layer decode
single-layer replacement
text replacement
grid index-map generation (build)
grid slot render (runtime)
alpha compositing
PNG encoding
scene serialization
scene deserialization
full traditional Cartopy render
CartoStack runtime update
interpreter start-up and imports (cold process)
```

Report both warm in-process repetitions and cold fresh-process runs. Cartopy caches Natural Earth geometries and projected paths in memory, so warm repetitions of the traditional render understate what a cron job pays.

---

# Key benchmark

The most important comparison should be:

```text
traditional Cartopy script, fresh process
vs
CartoStack runtime: fresh process → load file → swap data and text → stack → PNG
```

for the same final image (within documented tolerances).

Example target:

```text
Traditional render:
2.5 seconds

CartoStack:
load             40 ms
new data layer   70 ms
title update      2 ms
composite        15 ms
PNG encode       80 ms

total           207 ms
```

Exact targets should be determined after prototyping.

---

# Performance metrics

Track:

- wall-clock runtime
- CPU time
- peak RAM
- bytes read from disk
- bytes written
- number of allocations where practical
- layer decode time
- projection time
- rasterization time
- compositing time
- compression time
- image encoding time

---

# Development phases

`SESSIONS.md` holds the detailed, numbered sessions. In outline:

## Phase 1 — Prove it

A standalone benchmark compares a full Cartopy render with a prototype layered file and a NumPy/Pillow-only runtime that swaps grid values and a title.

Success criterion:

> The runtime produces a matching image, in a fresh process, substantially faster than the Cartopy script.

## Phase 2 — File format and core runtime

Specify `.cstack` v1, then implement the reader/writer, compositor, layer editing, grid slots, and text slots with NumPy and Pillow only.

Success criterion:

> A `.cstack` file loads, takes new grid values, new text, and an added image, and writes a PNG without Matplotlib or Cartopy installed.

## Phase 3 — Authoring

`SceneBuilder` produces `.cstack` files from Matplotlib/Cartopy: static layers, grid index maps, text definitions, colorbars, and georeferencing.

Success criterion:

> A file built on one machine is copied elsewhere without its sources, updated, and rendered to match a full Cartopy render of the same inputs.

## Phase 4 — Fast loading and release

Lazy decoding, layer-encoding defaults from benchmarks, portability tests in fresh environments, documentation, and a v0.1 release candidate.

## Later, only when profiling justifies it

- runtime geographic overlays with pyproj (stations, warning polygons)
- more grid types and interpolating display (bilinear, pyresample)
- faster PNG encoding, long-lived worker processes
- compiled vector layers for restyling borders without sources
- native (Rust/PyO3) kernels for compositing or grid rendering
- dirty rectangles, composite trees, tiling
- optional GPU backend

---

# Testing strategy

CartoStack will need unusually strong visual regression testing.

## Unit tests

Test:

- layer ordering
- visibility
- serialization
- metadata
- cache invalidation
- projection definitions
- coordinate conversions
- alpha compositing
- cropping

## Visual tests

Compare final output against reference images.

Measure:

- exact pixel equality where possible
- tolerance-based differences where required

## Round-trip tests

```text
create scene
↓
save
↓
load
↓
render
```

must produce the expected output.

## Source independence tests

A critical test should:

1. compile a scene from shapefiles
2. save the scene
3. delete or move the shapefiles
4. load the scene
5. render the map

The output must remain correct.

---

# Determinism

Production plotting benefits from deterministic output.

CartoStack should attempt to guarantee that identical:

- scene
- inputs
- package versions
- rendering backend

produce identical output.

Potential nondeterminism from threading or native rasterization should be tested.

---

# Error handling

Errors should be explicit.

Examples:

```text
SceneGeometryChangedError
LayerNotFoundError
LayerTypeError
ProjectionMismatchError
SceneVersionError
CorruptSceneError
UnsupportedRendererError
GridMismatchError
```

Silent map misalignment is unacceptable.

---

# Compatibility

Potential integrations:

- NumPy
- xarray
- pandas
- GeoPandas
- Matplotlib
- Cartopy
- Shapely
- PyProj
- Rasterio
- GDAL
- MetPy
- cfgrib
- netCDF4

Not all need to be hard dependencies.

Adapters should be optional where possible.

---

# Proposed package layout

```text
src/cartostack/
│
├── __init__.py          Scene (core only; never imports extras)
├── scene.py             load/save, editing API, render, save_png
├── geometry.py          canvas geometry, georeferencing, fingerprints
├── layers.py            raster, grid, text, colorbar layer kinds
├── grid.py              index-map gather, normalization, LUT
├── text.py              Pillow/FreeType text rendering
├── compositor.py        straight-alpha source-over, cropped placement
├── io/
│   ├── manifest.py      schema and validation
│   ├── archive.py       ZIP container, lazy members
│   └── codecs.py        PNG, zstd/raw arrays
│
└── build/               requires cartostack[build]
    ├── builder.py       SceneBuilder
    ├── figure.py        fixed-geometry Matplotlib/Cartopy figures
    └── index_map.py     index-image generation
```

If a Rust backend is added later:

```text
rust/
├── Cargo.toml
└── src/
    ├── lib.rs
    ├── compositor.rs
    └── remap.rs
```

---

# Minimal internal classes

The initial implementation can remain small.

```python
class Scene:
    geometry: SceneGeometry
    layers: LayerStack

    def add_layer(self, layer): ...
    def remove_layer(self, layer_id): ...
    def replace_layer(self, layer_id, layer): ...
    def composite(self): ...
    def save_png(self, path): ...
```

```python
class SceneGeometry:
    width: int
    height: int
    dpi: float
    projection: ProjectionSpec
    geographic_extent: tuple
    projected_extent: tuple
    axes_bbox: tuple
```

```python
class RasterLayer:
    id: str
    zorder: float
    rgba: object
    visible: bool
    x: int
    y: int
```

Avoid premature abstraction until benchmarks reveal the important boundaries.

---

# Suggested first experiment

Before building a package, construct two standalone benchmark scripts.

**Baseline.** A representative operational map drawn the normal way with Cartopy: land/ocean, lakes, county/state/country boundaries, city labels, a large `pcolormesh`, title, subtitle, and colorbar. Time it in fresh processes and warm in-process.

**Prototype file and runtime.**

1. On the build side, render the static content below and above the data, and the title, into transparent RGBA layers on the identical canvas.
2. Generate the data grid's pixel→cell index map with the index-image method.
3. Write a prototype ZIP: JSON manifest, layers, index map, colormap LUT, font.
4. In a fresh process that imports only NumPy and Pillow, load the file, render new grid values and a new title, stack the layers, and encode the PNG.
5. Compare the image with a full Cartopy render of the same new inputs.

This experiment should answer:

- How much of a cron-style Cartopy run is imports, source reading, projection, drawing, and encoding?
- How fast can a core-only process load the file and produce the image?
- Which layer encoding (PNG vs raw/zstd) loads fastest at an acceptable file size?
- How closely does the runtime output match the Cartopy render, and where does it differ?
- Is the measured gain large enough to justify the package, and is native code needed at all?

---

# Initial milestone

The first meaningful milestone (v0.1):

```python
# runtime worker: NumPy + Pillow only
from cartostack import Scene

with Scene.load("northeast.cstack") as scene:
    scene.replace_grid("temperature", t2m)
    scene.text["title"] = "2-m Temperature"
    scene.add_layer("logo", logo_rgba, order=93, x=1100, y=740)
    scene.save_png("output.png")
```

with the following guarantees:

- Matplotlib and Cartopy are not imported
- no source geospatial file is opened
- no coordinate is projected
- no unchanged layer is re-rendered, and unneeded layers are not decoded
- only the grid slot and the title are rendered
- output matches a full Cartopy render of the same inputs within documented tolerances

If that works reliably, the central project concept has been validated.

---

# Performance-first development principles

1. **Measure before optimizing.**
2. **Never rerender unchanged content.**
3. **Never reproject unchanged coordinates.**
4. **Never reload static source geometry during production rendering.**
5. **Keep the saved scene self-contained.**
6. **Prefer immutable compiled artifacts.**
7. **Avoid full-canvas buffers for small layers when practical.**
8. **Avoid copies of large NumPy arrays.**
9. **Keep expensive operations outside Python loops.**
10. **Use native code only where profiling shows it matters.**
11. **Treat PNG encoding separately from map rendering.**
12. **Keep Python as the API, not necessarily the execution engine.**

---

# Possible project evolution

A realistic progression is:

```text
standalone benchmark (Cartopy vs prototype file)
      ↓
.cstack format v1 specification
      ↓
core reader/writer + compositor (NumPy/Pillow)
      ↓
grid slots and text slots
      ↓
SceneBuilder authoring from Matplotlib/Cartopy
      ↓
lazy loading, portability, v0.1
      ↓
runtime geographic overlays, more grid types
      ↓
native kernels, dirty rectangles, tiling, GPU — only if profiling demands
```

Each step should be benchmarked independently.

---

# Long-term vision

CartoStack should behave less like a traditional plotting wrapper and more like a small **geospatial render engine**.

The build phase performs expensive work:

```text
source datasets
      ↓
geometry processing
      ↓
projection
      ↓
rendering / compilation
      ↓
self-contained scene
```

The production phase should be intentionally minimal:

```text
load scene
      ↓
replace changed data
      ↓
replace changed text
      ↓
composite
      ↓
encode image
```

For fixed operational products, the ideal runtime path should not touch:

- shapefiles
- GeoJSON
- Natural Earth datasets
- GeoPandas
- static Shapely geometries
- static Cartopy features
- unchanged Matplotlib artists

The expensive geographic work should already be represented in the saved scene.

---

# Summary

CartoStack is intended to solve a specific production problem:

> Geoscience maps are often rebuilt from scratch even when almost everything about the map is identical to the previous product.

The package replaces that workflow with a layered map file.

Static cartography is authored once with Matplotlib and Cartopy. Each product loads the file, swaps in new data and text, and stacks the layers using only NumPy and Pillow.

The public interface remains Python, while the implementation is free to evolve toward whichever backend provides the best measured performance.

The first version should remain deliberately simple:

```text
.cstack (ZIP + JSON + layers) → the product
Matplotlib/Cartopy → build-time authoring only
NumPy/Pillow → the entire runtime
Python → public API
```

Only after profiling should the project introduce:

```text
Rust
SIMD
memory mapping
tile rendering
native rasterization
GPU acceleration
```

The core requirement should remain unchanged throughout the project:

> **If a layer did not change, CartoStack should do as little work on that layer as possible.**
