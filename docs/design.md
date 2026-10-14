# How CartoStack works

This page explains the ideas behind CartoStack. The [API reference](api.md) documents the
calls, and the [format specification](format.md) defines the `.cstack` file exactly.

## The problem

Operational maps, such as a precipitation forecast or a temperature analysis, are made on a
schedule. From one product to the next most of the map stays the same: the projection and
extent, the basemap, coastlines, borders, lakes, city labels, logo, layout and legend. Only a
few parts change: the data, a subtitle or valid time, and sometimes a colorbar.

A typical Matplotlib/Cartopy script still rebuilds everything on every run. It imports large
libraries, reads shapefiles and Natural Earth data, projects geometries, builds artists and
rasterizes the same borders again. That work usually costs far more than drawing the new data.

## The idea

CartoStack splits map making into two stages.

**Build, once per map template**, on a machine with Matplotlib and Cartopy:

```text
existing Matplotlib/Cartopy figure
        ↓  SceneBuilder assigns each artist to a layer
RGBA layers + grid index maps + color tables + text settings + fonts + georeference
        ↓
map.cstack
```

**Runtime, every product**, with only NumPy and Pillow:

```text
map.cstack ──► load and verify
new grid values ──► index-map lookup → normalize → color table ──► grid layer
new polygons    ──► project (NumPy) → fill by value bin           ──► polygon layer
new text        ──► Pillow with the embedded font                 ──► text layer
                              ↓
               stack the layers (alpha compositing)
                              ↓
                             PNG
```

Building may be slow; it happens once. Making a product needs only array operations and
image stacking. The runtime never imports Matplotlib, Cartopy, pyproj or shapely, and it never
reads the original geospatial files.

## One shared canvas

Every layer in a file uses the same canvas: image size, DPI, map-axes rectangle, projection,
pixel transform, extent and clipping. Pixel `(x, y)` is the same place on Earth in every
layer, so stacking layers is enough to make the map. Anything that would break this, such as
a layer of the wrong size or a change to the geometry, is refused instead of producing a
misaligned map.

## Layers

A file holds an ordered stack of layers. Static layers are plain pixels. *Slots* are layers
whose pixels the runtime regenerates from new data.

| Layer | Stored in the file | Runtime work |
| --- | --- | --- |
| Static raster | RGBA pixels (basemap, borders, labels, logo, legend) | None; reused as is |
| Grid slot | Which grid cell each pixel shows, the color range and a color table | Look up each pixel's value, normalize it and color it |
| Polygon slot | Value bins with colors, and the map projection | Project lon/lat rings with NumPy and fill them by bin |
| Text slot | Font, size, color, anchor, and the font file | Lay out one line as Matplotlib would and draw it with Pillow |
| Colorbar | Its pixels plus the parts needed to redraw it | Redraw ticks and colors when a grid's range or colors change |

**Grid slots** avoid projecting anything at runtime. While building, CartoStack draws the
grid once with Matplotlib and records which cell lands in each pixel (an *index map*).
Rendering new values is then one array lookup followed by the same normalization and color
rounding Matplotlib uses, so the colors match the full render.

**Polygon slots** handle classified shapes such as WPC precipitation contours. The runtime
projects their lon/lat rings with a NumPy implementation of the Lambert Conformal Conic
projection (no pyproj), classifies each polygon into a value bin, and fills the polygons in
bin order with the even-odd rule, using several samples per pixel to smooth edges.

**Text slots** store the font file in the map file, so a title or valid time is drawn the same
way on any machine, with Matplotlib 3.11's placement rules.

## Stacking and reuse

Layers are stacked bottom to top with standard alpha compositing. Runs of static layers next
to each other are combined once and reused for as long as they stay unchanged, so a new
product redraws only the slot that changed plus a few compositing steps. Layers can also be
added, removed, hidden, moved or reordered at runtime.

## Why it is fast

The speed comes from work the runtime skips: no heavy imports, no reading of geospatial
sources, no projecting of static geometry, no artist construction and no redrawing of
unchanged layers.

![A scheduled job making 32 maps from data on disk, average of 10 runs: Matplotlib/Cartopy 33.3 s, CartoStack 1.9 s (18× faster)](images/scheduled_job.png)

| Scheduled job from the examples (data on disk, fresh process per job, Apple M3 Pro, average of 10 runs) | Matplotlib/Cartopy | CartoStack | Speed-up |
| --- | --- | --- | --- |
| Hazard maps: 7 synthetic daily GeoTIFFs × 2 regions = 14 maps | 11.3 s | 0.65 s | 17× |
| WPC QPF maps: 18 products | 22.0 s | 1.24 s | 18× |
| Both jobs, back to back (32 maps) | 33.3 s | 1.89 s | 18× |
| CPU time (user + system), both jobs | 39.6 s | 1.9 s | 21× less |
| Peak memory per job (hazard / QPF) | 460 / 505 MB | 154 / 257 MB | 2–3× less |

What remains for each map is process start-up, loading the file, rendering the changed
layers, compositing and PNG encoding. PNG encoding is now one of the largest costs. These
times exclude building the `.cstack` files, which is done once per map layout (about 9 s
for the two hazard scenes and 7 s for QPF, including the original script's own drawing).
`benchmarks/cron_jobs.py` regenerates the figure and numbers.

## Reliability

- Files are ZIP archives of JSON, PNG and raw arrays, so they can be inspected with ordinary
  tools. They contain no pickled objects or code.
- Every member is checked against a SHA-256 digest when a file is loaded.
- Saves are atomic (write a temporary file, then rename) and deterministic: saving the same
  scene gives the same bytes.
- A loaded scene keeps no file open, so the file can be moved, replaced or deleted while the
  scene is in use.
- The format is versioned; readers reject unknown major versions instead of guessing.

## Non-goals

CartoStack is for fixed-layout production maps. It does not try to:

- serialize arbitrary live Matplotlib figures or keep every artist editable;
- replace Matplotlib or Cartopy, or be a general plotting library;
- resize or reproject a file after it is built (build a new one instead);
- support interactive pan and zoom.

## Future directions

Possible later work, each only if profiling shows it is worth it:

- more polygon projections and more grid types (log or boundary color ranges, interpolation);
- contour, point and vector slots, such as stations or warning polygons;
- lazy loading, faster PNG encoding and long-lived worker processes;
- native kernels for compositing or grid rendering, tiling, and GPU backends.
