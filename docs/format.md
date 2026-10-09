# The `.cstack` file format, version 1.0

Status: **1.0**, specified in Session 04 (2026-10-08). Reference implementation of the
validation rules: `cartostack.geometry` and `cartostack.manifest`
(`parse_manifest` / `validate_manifest`). Structural JSON Schema:
`src/cartostack/schemas/manifest-1.schema.json` (`cartostack.manifest.load_schema()`).
Examples: `docs/examples/*.manifest.json`.

A `.cstack` file is a layered map. It holds a stack of RGBA layers that share one canvas
geometry, plus *slots*: layers whose pixels are regenerated at runtime from new data
(grid values, classified polygons, text) using only NumPy and Pillow. The file never
contains pickles, live figures, or serialized callbacks. It renders without the
geospatial sources it was authored from.

The key words MUST, MUST NOT, SHOULD, and MAY are used as in RFC 2119.

## 1. Conventions

- **Pixel coordinates** are canvas pixels with the origin at the **top-left corner** of the
  top-left pixel and `y` growing downwards. Pixel `(col, row)` covers
  `[col, col+1) × [row, row+1)`, and its centre is `(col + 0.5, row + 0.5)`. Every layer of a
  file uses the same pixel grid, so pixel `(x, y)` is the same place in every layer.
- **Colours** are 8-bit RGBA `[r, g, b, a]` with **straight (non-premultiplied) alpha**.
- **Raster arrays** are `(height, width, 4)` `uint8`, row-major, top row first.
- **Numbers** are JSON numbers. Integers MUST be written without a fraction or exponent
  where this document says *integer*. NaN and infinities are not allowed anywhere.

## 2. Container

A `.cstack` file is a ZIP archive (APPNOTE 6.3, no encryption, no multi-disk):

| Member | Requirement |
| --- | --- |
| `mimetype` | MUST be the first member, stored, with exactly the ASCII bytes `application/x-cartostack` (no newline). Readers SHOULD check it. |
| `manifest.json` | MUST exist: the UTF-8 JSON manifest described below. |
| everything else | Data members, each listed in the manifest's `members` table. |

- All members MUST be stored without ZIP compression (`ZIP_STORED`). Compression is part of
  each member's *encoding* (§6.3), so decode cost is decided by the encoding alone, and a
  member can be read with one seek.
- **Member names** are relative, `/`-separated paths matching
  `[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*`, at most 255 characters, with
  no `..` segment. Names are case-sensitive and unique. `mimetype` and `manifest.json` are
  reserved.
- **Recommended layout** (not required): `layers/<id>.<ext>` for layer pixels,
  `slots/<id>/…` for slot data, `fonts/<file>` for fonts. Extensions: `.png`, `.rgba8`,
  `.rgba8.zz`, `.i32`, `.i32.zz`.
- **Members table.** `members` maps every data member name to
  `{"bytes": <integer>, "sha256": "<64 lowercase hex>"}`, computed over the member's stored
  bytes. Readers MUST verify the size and digest of a member before decoding it. Members not
  referenced by any record MAY exist; readers ignore them.
- Writers SHOULD write atomically (temporary file, then rename), so a failed save never
  corrupts an existing file.
- The reference writer (`Scene.save`, Session 05) is also **deterministic**:
  - members go in a fixed order (`mimetype`, `manifest.json`, then the others sorted by
    name) with timestamp 1980-01-01 00:00, Unix attributes `0644`, and manifest JSON
    written with a 2-space indent;
  - members read from a file and still unchanged are written back byte for byte.

  Saving the same scene twice, or loading and re-saving a file it wrote, gives identical
  bytes. Newly encoded members are compressed by the zlib that Python and Pillow link, so
  their bytes (never their decoded pixels) can differ between zlib versions. Session 15
  found this between macOS (zlib 1.2.12) and Linux (1.3.1). It preserves unknown fields at the manifest's top level, in `geometry`, in layer
  records, and in their kind sections.
- **Lifetime (reference runtime).** `Scene.load` reads, verifies and decodes every member,
  then closes the file before returning. A loaded scene holds no open file. The file may be
  moved, deleted, or replaced, including by saving over it, and `Scene.close()` releases
  nothing. A save re-encodes only pixels that are new since they were loaded or last saved.
  Lazy decoding was measured and not adopted (Session 12): load and decode take at most
  5.6 % of a one-product run (7.4 of 133 ms for the grid scene, 13.1 of 318 ms for QPF),
  below the 10 % threshold.

## 3. Versions and compatibility

`format` MUST be `"cartostack"`. `format_version` is `"MAJOR.MINOR"`; this document
specifies `1.0`.

- A reader MUST reject a file whose MAJOR version differs from the one it implements,
  *before* interpreting anything else (`UnsupportedVersionError`).
- A reader of `1.x` MUST read any `1.y`. **Minor versions only add optional fields**, with
  defaults that preserve 1.0 behaviour. Readers MUST ignore unknown fields and SHOULD
  preserve them when rewriting a file.
- Changing the meaning of an existing field, adding a required field, or adding a layer kind
  or encoding requires a new MAJOR version. A 1.x reader therefore never meets a layer kind
  or encoding it does not know, and treats one as invalid.
- Writers write the version whose features they use (`1.0` for this document).

## 4. Manifest

```json
{
  "format": "cartostack",
  "format_version": "1.0",
  "geometry": { ... },                       // §5
  "geometry_fingerprint": "sha256:<hex>",    // §5.6
  "members": { "<name>": {"bytes": 0, "sha256": "<hex>"} },
  "layers": [ ... ],                         // §6–§10, at least one
  "provenance": { ... }                      // §11, optional
}
```

## 5. Geometry

`geometry` defines the canvas every layer shares. It is immutable for the life of the file:
an operation that would change it MUST fail instead of misaligning layers.

```json
"geometry": {
  "canvas": {"width": 2210, "height": 1848, "dpi": 300.0},
  "axes": {"left": 0.0, "top": 0.0, "width": 2210.9002, "height": 1848.0},
  "clip": "axes",
  "figure": {"width_in": 10.0, "height_in": 8.0, "dpi": 100.0,
             "crop_in": [1.440166, 0.88, 7.369667, 6.16]},
  "georeference": { ... }
}
```

### 5.1 Canvas

`width` and `height` are integers in 1–100 000: the size of the output image. `dpi` is a
positive number giving the canvas's pixels per inch. It is used to convert authored point
sizes (text) and to relate the canvas to the authoring figure (§5.4).

### 5.2 Axes

`axes` is the map area in canvas pixels: `left`, `top`, `width`, `height` as numbers, with
width and height positive. For Cartopy maps this is the **resolved** axes rectangle after
the aspect adjustment, not the rectangle requested when the figure was built. It MAY extend
past the canvas by a fraction of a pixel (a tight crop truncates the output size), but it
MUST intersect the canvas.

### 5.3 Clip

`clip` (default `"axes"`, the only value in 1.0) is the region that map content regenerated
at runtime (grid and polygon slots) is clipped to: the axes rectangle intersected with the
canvas. Pre-rendered raster layers already contain their own clipping.

### 5.4 Figure and output crop

`figure` (optional; `null` or absent for files not authored from a figure) records how the
canvas relates to the authoring figure. This makes a fixed output crop representable without
moving any pixel:

- `width_in`, `height_in`, `dpi`: the figure's size and DPI when it was built.
- `crop_in`: `[x0, y0, width, height]` in figure inches with Matplotlib's bottom-left origin,
  e.g. the box `savefig(bbox_inches="tight")` resolved once at build time; `null` means the
  canvas is the whole figure.

The canvas *is* the cropped output. Canvas pixel `(col, row)` is figure point
`(x0 + col / canvas.dpi, y0 + height − row / canvas.dpi)` inches. The crop's origin may be
fractional in pixels (it was 432.05 px for the QPF scene); that is why the crop is recorded
rather than applied to integer-placed layers. Consistency: `|canvas.width − width ×
canvas.dpi| < 1`, and likewise for the height. Without `crop_in`, the whole figure at the
canvas DPI must match the canvas size in the same way.

### 5.5 Georeference

`georeference` (optional; `null` or absent for non-map canvases; REQUIRED when the file has
polygon slots):

```json
"georeference": {
  "crs": {"proj4": "+ellps=WGS84 +proj=lcc +lon_0=-75.85 ...", "wkt": null},
  "projection": {"name": "lcc", "a": 6378137.0, "f": 0.0033528106647474805,
                 "lat_0": 42.95, "lon_0": -75.85, "lat_1": 33.0, "lat_2": 45.0,
                 "x_0": 0.0, "y_0": 0.0},
  "projected_extent": {"xmin": -350859.05, "xmax": 350859.05,
                       "ymin": -285022.09, "ymax": 301515.04},
  "world_to_pixel": [0.00315069569, 0.0, 1105.45009869, 0.0, -0.00315069569, 949.982132]
}
```

- `crs.proj4` (required) and `crs.wkt` (optional) identify the projected CRS for external
  tools. The runtime does not parse them.
- `projection` restates the projection as plain numbers, using PROJ parameter names, so the
  runtime can evaluate it with NumPy. `name` is required. **1.0 runtime support: `lcc`**
  (ellipsoidal Lambert Conformal Conic with two standard parallels; Snyder 1987, eq.
  15-1…15-10). It requires `a` (semi-major axis, m, > 0), `f` (flattening, 0 ≤ f < 1;
  0 for a sphere), `lat_0`, `lon_0`, `lat_1`, `lat_2` (degrees; standard parallels in
  (−90, 90) and not symmetric about the equator), `x_0`, `y_0` (m). Other names are allowed
  and describe the CRS, but polygon slots in such a file are invalid ("unusable"), because
  the runtime cannot project lon/lat for them.
- Input longitudes and latitudes for runtime projection are geodetic degrees **on the
  projection's ellipsoid**, with no datum shift. This matches Cartopy's `PlateCarree()` →
  projection pipeline. Data on another datum must be converted beforehand if the difference
  matters at the canvas resolution.
- `projected_extent` is the axes' extent in projected metres (`xmin < xmax`, `ymin < ymax`),
  as from Cartopy's `ax.get_extent()`.
- `world_to_pixel` `[a, b, c, d, e, f]` maps projected metres to continuous canvas pixels:
  `col = a·x + b·y + c`, `row = d·x + e·y + f`. It MUST be invertible. (GDAL's
  geotransform is the inverse direction: pixel → world.)
- **Consistency:** `(xmin, ymax)` MUST map to the axes' top-left corner and `(xmax, ymin)`
  to its bottom-right, each within 0.01 px.

### 5.6 Geometry fingerprint

`geometry_fingerprint` is `"sha256:"` followed by the SHA-256 of the **canonical JSON** of
the normalised geometry. Readers recompute it and MUST reject a mismatch, which catches a
geometry edited without re-authoring the layers. Two files can share or exchange layers
only if their fingerprints are equal.

Normalised geometry: an object with exactly these keys, defaults filled in:
`canvas` {`width`, `height` as integers; `dpi` as a float}, `axes` {`left`, `top`,
`width`, `height` as floats}, `clip`, `figure` (`null` or {`width_in`, `height_in`, `dpi`
as floats; `crop_in` as `null` or 4 floats}), and `georeference` (`null` or {`crs`
{`proj4`, `wkt`}, `projection` {`name` plus every numeric parameter as a float},
`projected_extent` {4 floats}, `world_to_pixel` [6 floats]}).

Canonical JSON: keys sorted, separators `,` and `:` with no whitespace, ASCII output with
`\uXXXX` escapes, integers without a fraction, and floats in their shortest round-trip
decimal form (Python `repr`, ECMAScript `Number.prototype.toString`), always with a `.0` or
an exponent. This is Python's `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.

## 6. Layers

`layers` is an array of at least one record. Common fields:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `id` | string `[a-z0-9][a-z0-9_-]{0,63}` | required | Unique within the file. |
| `kind` | `raster` \| `colorbar` \| `grid` \| `polygon` \| `text` | required | §7–§10 |
| `order` | number | required | Stacking position. |
| `left`, `top` | integer | 0 | Canvas offset of the layer's pixels; may be negative. |
| `width`, `height` | integer ≥ 1 | canvas size | Size of the layer's pixel array; the placed box must intersect the canvas. |
| `visible` | boolean | `true` | Hidden layers are skipped. |
| `opacity` | number 0–1 | 1 | Multiplies the layer's alpha. |
| `member` | member name | see kinds | The layer's **current pixels**, `(height, width, 4)`. |
| `encoding` | §6.3 | with `member` | Encoding of `member`. |

### 6.1 Stacking order

Layers are drawn bottom to top in ascending `order`. Ties keep their order in the `layers`
array. Writers SHOULD write `layers` already sorted.

### 6.2 Compositing

Start from a fully transparent canvas. For each visible layer in stacking order, multiply
its alpha by `opacity` and composite it source-over (Porter–Duff, straight alpha) at
`(left, top)`; parts outside the canvas are discarded. The scaled alpha is
`floor(alpha · opacity + 0.5)`; colour channels are unchanged. The reference operator is
Pillow's `Image.alpha_composite`. Implementations MAY differ from it and from a
single-canvas Matplotlib render by rounding. Session 02 measured at most 3–5 levels per
channel when stacking Agg-rendered layers.

Placement clipping to the canvas is separate from map clipping (§5.3): raster and colorbar
pixels are composited wherever they fall on the canvas, inside or outside the axes.

The reference runtime (`cartostack.compositor`) is exact against layer-by-layer Pillow
stacking except in one case. It flattens a contiguous run of two or more raster or colorbar
layers that lies above a slot into one image and reuses it across renders, which can differ
by rounding. In a stress test with random partial alpha (200 runs of 2–4 layers), no value
differed by more than 3 levels and under 0.2 % by 2 or more. Layers below the first slot,
single layers, and slots are always composited exactly. Writers SHOULD store static runs
pre-flattened (§7), which avoids the case.

The output image has the canvas size. Saved as RGB, it is first composited over an opaque
background colour (the reference runtime defaults to white); an opaque canvas is
unchanged by that.

### 6.3 Encodings

| Encoding | Payload | Used for |
| --- | --- | --- |
| `rgba8+zlib` | zlib stream (RFC 1950) of the raw array | raster pixels (**default**), colormap LUTs |
| `rgba8` | raw `uint8` RGBA array, `height × width × 4` bytes | raster pixels, LUTs |
| `png` | 8-bit RGBA PNG of `width × height` | raster pixels (inspectable) |
| `i32le+zlib` | zlib stream of raw little-endian `int32` array | grid index maps (**default**) |
| `i32le` | raw little-endian `int32`, `height × width × 4` bytes | grid index maps |

The decoded shape MUST match the record (`width`, `height`). Writers SHOULD use the
defaults. In Session 02, `rgba8+zlib` gave the smallest files and decoded in about 4 ms per
4-megapixel layer.

## 7. Raster and colorbar layers

`raster`: pre-rendered pixels. `member` and `encoding` are REQUIRED. Contiguous runs of
static content SHOULD be stored pre-flattened into one raster layer (Session 02: half the
stacking and load time, and closer to the single-canvas render). Store them separately only
when they must be shown, hidden, or reordered independently.

`colorbar`: a raster layer (same requirements) that shows a grid slot's colour scale. It
adds `"colorbar": {"slot": "<grid layer id>", "redraw": {...}}`; the slot MUST name a grid
layer in the same file. The layer's pixels are shown as they are until the slot's `vmin`,
`vmax`, or LUT changes. A runtime then redraws the colorbar from `redraw` if it is present;
without it, the colorbar is stale (it no longer matches the slot) until it is replaced.

```json
"redraw": {
  "orientation": "horizontal",
  "box": [67.36, 50.8, 327.27, 12.6],
  "rows": {"member": "colorbars/cbar/rows.i32.zz", "encoding": "i32le+zlib", "n_colors": 256},
  "under": null,
  "over": {"member": "colorbars/cbar/over.rgba8.zz", "encoding": "rgba8+zlib"},
  "label": {"member": "colorbars/cbar/label.rgba8.zz", "encoding": "rgba8+zlib"},
  "label_edge": 79.6,
  "ticks": {"locator": "auto", "nbins": 8, "steps": [1, 2, 2.5, 5, 10], "values": [],
            "side": "bottom", "direction": "out", "length": 4.86, "width": 1.11,
            "color": [0, 0, 0, 255]},
  "labels": {"font": "fonts/DejaVuSans.ttf", "size_px": 12.5, "color": [0, 0, 0, 255],
             "pad": 4.86, "minus": "\u2212"}}
```

All coordinates are pixels in the layer's own frame, and every raster has the layer's size.
A redraw composites, in order:

1. `under`: what is drawn before the colour strip (typically nothing, as colorbar axes draw
   no background).
2. The strip: `rows` (int32, `i32le` or `i32le+zlib`) gives each pixel the LUT row it
   shows: `0…n_colors−1`, then `n_colors` (under) and `n_colors + 1` (over) for the
   extension triangles, and `−1` elsewhere. Pixels take the slot's LUT row with the slot's
   alpha rule (§8, step 4). If the slot's LUT has another number of colours `N`, a strip
   pixel at fraction `t` along `box`, from `vmin` to `vmax`, takes row `floor(t · N)`.
3. `over`: the outline and anything else drawn after the strip, except ticks, tick labels,
   and the axis label.
4. Tick marks at `box` positions for the tick values (below), on `side`: `length` and
   `width` pixels, pointing `out`, `in`, or both (`inout`), in `color`, with the mark
   centred on the pixel centre at or left of (below) the exact position.
5. Tick labels: one-line text (§10, `layout: "matplotlib"`, from the `font` member)
   anchored `length` (for `out`; half for `inout`; 0 for `in`) plus `pad` pixels outward
   from the box edge. Horizontal colorbars use `ha: center` with `va: top` (bottom side)
   or `va: bottom` (top side); vertical ones use `va: center_baseline` with `ha: left`
   (right side) or `ha: right` (left side).
6. `label`: the axis label, moved perpendicular to the box by the whole-pixel change in the
   tick labels' outer edge, measured as `label_edge` (the authored edge) is: the extreme of
   the labels' layout boxes on that side.

**Tick values and labels** reproduce Matplotlib's defaults exactly. `locator: "auto"` is
`MaxNLocator(nbins, steps)` (Matplotlib's `AutoLocator` with its resolved `nbins`) over
`[vmin, vmax]`; `"fixed"` uses `values`. Only ticks within `[vmin, vmax]` are drawn. Labels
are `ScalarFormatter`'s: `%1.Nf` with the fewest decimals that keep the labels exact to
1/1000 of the range, with `minus` replacing `-`. When `ScalarFormatter` would show an
offset or a power-of-ten multiplier (for example `1e6` or `+1000`), the runtime MUST NOT
redraw. The colorbar stays stale instead. Session 11 checked the locator and formatter
against Matplotlib 3.11.2 on 1,500 random ranges; every tick and label was identical.

## 8. Grid slots

A grid slot renders new values of a fixed grid without projecting anything. A stored index
map says which grid cell each pixel shows.

```json
{"id": "temperature", "kind": "grid", "order": 10,
 "grid": {
   "shape": [500, 500],
   "cells": "centers",
   "index_map": {"member": "slots/temperature/index.i32.zz", "encoding": "i32le+zlib"},
   "value_dtype": "float32",
   "norm": {"kind": "linear", "vmin": -10.0, "vmax": 35.0},
   "colormap": {"member": "slots/temperature/lut.rgba8", "encoding": "rgba8",
                "n_colors": 256, "extend": "both"},
   "alpha": 0.8}}
```

- `shape` `[ny, nx]`: the grid the values must have. Cell index `k = j·nx + i`, row-major;
  `ny·nx` must fit in an `int32`.
- `index_map`: an `int32` array of the layer's `height × width`. The value is the cell index
  shown at that pixel, or `−1` for no data. Pixels outside the clip region (§5.3) MUST be
  `−1`. `cells` (`centers` | `edges`, default `centers`) records whether the authoring
  coordinates were cell centres or edges; it is informational, since the mapping is fully in
  the index map. Index maps are produced at build time by the index-image method: draw the
  mesh with per-cell colours encoding `k`, with anti-aliasing off.
- **Values → colours.** These are Matplotlib's `Normalize` and `Colormap.__call__` rules,
  reproduced exactly. Values are an array of shape `shape` with a real numeric type,
  converted to `value_dtype` (default `float32`); NaN and masked values are *bad*.
  1. `x = value − vmin`, then `x = x / (vmax − vmin)`. Each step is computed in float64
     and rounded to `value_dtype`, as Matplotlib's in-place NumPy arithmetic does.
     Computing in float32 throughout gives different results for many ranges (Session 08
     measured 55–100 % of values differing for ranges such as 0.1–0.7 or 250.15–310.7).
  2. `x = x · n_colors` in `value_dtype`; where `x == n_colors`, set `x = n_colors − 1`.
  3. The row is `under` (`n_colors`) if `x < 0`, `over` (`n_colors + 1`) if
     `x ≥ n_colors`, `bad` (`n_colors + 2`) if the value is bad, otherwise `trunc(x)`.
     A bad value takes `bad` even when it is also out of range.
  4. The colour is LUT row `row`. If `alpha` is not `null`, replace the alpha channel by
     `floor(alpha · 255 + 0.5)`, except that a `bad` colour whose four channels are all
     zero stays `(0, 0, 0, 0)` (Matplotlib ignores alpha for it).
- `colormap`: the LUT member decodes (`rgba8` or `rgba8+zlib`) to `n_colors + 3` RGBA rows,
  i.e. `(n_colors + 3) × 4` bytes: `n_colors` colours followed by the `under`, `over`, and
  `bad` colours. `extend` (`neither|min|max|both`, default
  `neither`) only affects colorbars; `under` and `over` colours are always applied.
  Writers authoring from Matplotlib SHOULD store `round(c · 255)` per channel, as Agg
  rounds colours when it draws a mesh, rather than `cmap(..., bytes=True)`, which
  truncates (Session 10: truncation put most pixels one level off the rendered map).
- Pixels with index `−1` are transparent.
- **Runtime changes.** A runtime MAY change `vmin`, `vmax`, and the LUT (including
  `n_colors`) when it supplies new values; a saved file then records the new `norm` and
  colormap. Colorbar layers drawn from the slot no longer match it until they are redrawn
  or replaced.
- `member` (optional) holds the slot's current pixels, so the file renders before any new
  values are supplied. Without it, the slot is empty until values are given.

## 9. Polygon slots

A polygon slot renders classified polygons supplied at runtime as lon/lat rings, for
example WPC QPF contours. It requires `geometry.georeference` with a supported projection.

```json
{"id": "qpf", "kind": "polygon", "order": 10,
 "polygon": {
   "bins": [{"lower": 0.0, "upper": 0.01, "color": [255,255,255,255], "order": 10},
            {"lower": 0.01, "upper": 0.1, "color": [127,255,0,255], "order": 11}],
   "interval": "closed-open",
   "round_decimals": 2,
   "fallback": {"color": [128,128,128,255], "order": 10},
   "fill_rule": "evenodd",
   "supersample": 2}}
```

- **Input.** One or more *records*, each a value and any number of rings. A ring is a
  sequence of `(lon, lat)` degrees (§5.5). Rings are implicitly closed, and their
  orientation does not matter.
- **Classification.** If `round_decimals` is not `null`, the value is first rounded to that
  many decimals (Python `round(value, n)`). The record takes the **first** bin in list order
  with `lower ≤ value < upper` (`interval: "closed-open"`, the only value in 1.0). If none
  matches, it takes `fallback` (`color`, `order`; `lower` and `upper` are ignored). If there
  is no fallback, the input is rejected.
- **Draw order.** Records are drawn by ascending bin `order`. Ties keep their input order.
- **Filling.** Each record's rings are projected (`projection`, then `world_to_pixel`) to
  continuous canvas pixels, then shifted by the layer's `(left, top)`. Straight edges join
  consecutive projected vertices, and the last vertex joins the first. The record is filled
  with the **even-odd rule** over all of its rings, so holes and islands inside holes follow
  from the rule and no ring nesting information is needed.
- **Samples.** With `supersample = k` (1–8; default 1), every pixel holds `k × k` samples
  at the centres of a `k`-times finer grid: sample `(i, j)` is at
  `((i + 0.5) / k, (j + 0.5) / k)` pixels. A sample is **inside** a record when an odd
  number of the record's edges cross the sample's horizontal line at or to the left of the
  sample. An edge from `(x0, y0)` to `(x1, y1)` crosses that line when the sample's `y` lies
  in `[min(y0, y1), max(y0, y1))`, at `x = x0 + (y − y0)·(x1 − x0)/(y1 − y0)`. This
  half-open rule counts a shared vertex once and ignores horizontal edges. A sample exactly
  on a left edge is inside and one exactly on a right edge is outside, so polygons that
  share an edge do not overlap.
- **Clipping.** A sample is never inside when its centre lies outside the clip region
  (§5.3) or outside the layer's placed box: it must satisfy `left ≤ x < right` and
  `top ≤ y < bottom`.
- **Compositing.** A pixel's coverage is its count `c` of inside samples out of `k²`. The
  record contributes the bin colour with alpha `floor(a · c / k² + 0.5)`, where `a` is the
  colour's alpha. That contribution is composited source-over onto the slot's earlier
  records with the §6.2 operator (Pillow's integer `alpha_composite`), starting from a
  fully transparent layer. `k = 1` gives hard edges.
- **Choosing `k`.** Writers SHOULD use `supersample: 4` for maps compared with
  Matplotlib/Cartopy output. The reference runtime's exact fill, on all 18 WPC QPF
  products of Session 01b (2210 × 1848, measured in Session 08b on an Apple M3 Pro), gives
  these results. Fill times are medians over products 2–18 of one process, plus the heavy
  Day 1-7 product. The accuracy column is the share of clear-map pixels that differ from
  Cartopy's anti-aliased render by more than 32 levels.

  | `k` | Fill, median product | Fill, Day 1-7 | Pixels off by > 32 levels (median; range) |
  | --- | --- | --- | --- |
  | 1 | 40 ms | 118 ms | 0.386 %; 0.045–0.728 % |
  | 2 | 42 ms | 125 ms | 0.057 %; 0.012–0.217 % |
  | 4 | 45 ms | 148 ms | 0.000 %; 0–0.001 % |

  Edge accuracy improves much faster than cost grows, because most of the cost is work at
  pixel resolution. The Session 02 prototype's Pillow fill truncated vertices and filled
  every pixel an edge touched, so it differed from this rule along every edge. Its 2×
  default left 0.23 % of clear-map pixels more than 32 levels off.
- `member` (optional): current pixels, as for grids.

## 10. Text slots

A text slot renders one line of text with Pillow/FreeType from an embedded font, placed by
Matplotlib's layout rule so it lines up with text authored in Matplotlib.

```json
{"id": "subtitle", "kind": "text", "order": 30,
 "text": {"value": "8:00 AM Oct 8 - 8:00 AM Oct 11 (Issued: 6:19 AM Oct 8)",
          "font": "fonts/DejaVuSans-Oblique.ttf", "size_px": 45.833333,
          "color": [0, 0, 0, 255], "x": 1105.4501, "y": 157.08,
          "ha": "center", "va": "bottom", "layout": "matplotlib",
          "snap": null, "offset": [0.0, 0.0], "rotation": 0.0}}
```

- `value`: the current text (one line).
- `font`: a member holding a TrueType/OpenType font. Readers MUST fail clearly if it is
  missing or unusable, and MUST NOT fall back to another font.
- `size_px`: the font size in pixels (`points × dpi / 72`).
- `color`: RGBA. The layer's alpha is `round(coverage · a)`.
- `x`, `y`: the anchor in canvas pixels.
- `ha` ∈ `left|center|right` (default `left`) and
  `va` ∈ `top|center|baseline|bottom|center_baseline` (default `baseline`): the alignment
  of the text box to the anchor.
- **`layout: "matplotlib"`** (the only value in 1.0) is Matplotlib 3.11's single-line
  layout (`Text._get_layout`, HarfBuzz shaping, Agg glyph drawing):
  1. *Shaping.* Apply the font's ligatures (GSUB `rlig`, `liga`, `clig` for the
     `DFLT`/`latn` default language systems). Pen positions are FreeType's hinted
     advances at `size_px` plus the font's pair kerning (GPOS `kern`, else a format 0
     `kern` table) scaled by `size_px / unitsPerEm`, each rounded to 1/64 pixel.
  2. *Box.* Union the glyphs' ink boxes at their pen positions, relative to the origin:
     `(l, t, r, b)` with `t < 0` above the baseline. The width is `w = r − max(l, 0)`.
     The ascent is `a = max(−t, A)` and the descent `d = max(b, D)`, where `A` and `D`
     are the font's OS/2 `sTypoAscender` and `−sTypoDescender` (else `hhea` ascent and
     `−descent`) scaled by `size_px / unitsPerEm`.
  3. *Origin.* `x − {0, w/2, w}[ha]`; the baseline is at `y` (baseline), `y − d` (bottom),
     `y + a` (top), `y + (a − d)/2` (center), or `y + a/2` (center_baseline, Matplotlib's
     vertical-axis tick labels).
  4. *Glyphs* are drawn at their pen positions to 1/64 pixel, without snapping to whole
     pixels; coverage accumulates source-over from glyph to glyph.
- `snap`: `null` (Matplotlib ≥ 3.11) keeps the origin fractional; `"round"`, `"floor"`
  and `"ceil"` snap it to whole pixels (Matplotlib ≤ 3.10's Agg did `"round"`).
- `offset`: `[dx, dy]` pixels added after snapping. Matplotlib ≤ 3.10's Agg drew text one
  pixel higher (`[0, −1]`); files authored from Matplotlib 3.11 use `[0, 0]`.
- `rotation`: MUST be 0 in 1.0.
- `member` (optional): the rendered pixels of the current `value`, placed at
  `left`/`top`.

**Measured against Matplotlib 3.11.2 (Session 09).** DejaVu Sans, Oblique and Bold at
11–58 px, all four `va` values with `ha` left, center and right, fractional anchors,
plain, kerned (`AVATAR To Ty Wo …`) and ligature (`office flight affine`) strings.
Placement, measured as the shift of the ink's centre of mass, is within 0.02 px
horizontally (one 0.22 px case among the kerned strings) and 0.006 px vertically. The
remaining differences are anti-aliasing. The reference runtime renders each glyph with
FreeType at a whole pixel and shifts it by the sub-pixel remainder with bilinear
interpolation, because Pillow cannot render at fractional positions, whereas FreeType in
Matplotlib rasterises the outline at the fractional position. Of the pixels either
render inks, 11–14 % (median) differ by more than 32 levels at 30–58 px and 21–27 % at
11–17 px. The worst single pixel is about 80–95 levels, and the ink box is 1 px wider.
Not reproduced: contextual alternates, mark positioning, and ligature glyphs without a
Unicode code point (such ligatures are drawn as their separate characters).

## 11. Provenance

`provenance` (optional) is a free-form object. Readers MUST NOT require it. Recommended
keys:

- `cartostack`: the writer's version;
- `created_utc`;
- `authoring`: library versions, e.g. Matplotlib and Cartopy;
- `source`: a short description;
- `sources`: a list of `{"name", "sha256"}` for the geospatial inputs.

## 12. Validation

`cartostack.manifest.parse_manifest(data)` returns typed, immutable records. It raises
`UnsupportedVersionError` for another MAJOR version, and `FormatError` (or `GeometryError`)
listing **every** problem with a JSON path. It needs only the Python standard library. It
checks everything in this document that can be checked from the manifest alone:

- types, ranges, and enumerations;
- geometry consistency (§5.4, §5.5) and the fingerprint;
- unique ids;
- every referenced member present in `members`;
- colorbar slots naming grid layers;
- polygon slots having a supported projection;
- placements intersecting the canvas.

Member sizes and digests, and decoded shapes, are checked when reading the archive
(Session 05). The JSON Schema checks structure only, so it accepts some manifests that
`parse_manifest` rejects; the reverse should never happen.

## 13. Review against the Session 02 prototype

| Prototype manifest (`benchmarks/prototype_build.py`) | Format 1.0 |
| --- | --- |
| `format: "cartostack-prototype"`, `version: "0.0-session02"` | `format`, `format_version: "1.0"`, plus the `mimetype` member |
| `canvas.width/height`, `save_dpi`, `figsize_in`, `crop_bbox_in` | `geometry.canvas` (`dpi` = save DPI), `geometry.figure` (`crop_in`) |
| (resolved axes only in benchmark results) | `geometry.axes`, checked against the georeference |
| `georef.lcc` (+ `proj4`), `georef.affine` | `georeference.projection` (`name: "lcc"`), `crs.proj4`, `world_to_pixel`, plus `projected_extent` |
| `stack: [...]` with `slot:`/`text:` prefixes | `layers[]` with `kind` and `order` |
| raster `encoding: zlib/raw/png`, per-layer `sha256` | `rgba8+zlib`/`rgba8`/`png`; digests in `members` |
| `slots.qpf`: `ranges`, `colors`, `zorder`, `default`, `rounding`, `fill_rule`, `supersample` | `polygon.bins[]` (`lower`, `upper`, `color`, `order`), `fallback`, `round_decimals`, `fill_rule`, `supersample` |
| `slots.grid`: `grid_shape`, `vmin`, `vmax`, `n_colors`, `lut_rows`, `alpha_u8`, `dtype`, `index_map`, `lut` | `grid.shape`, `norm`, `colormap` (`n_colors`, fixed under/over/bad rows), `alpha` (float; 8-bit rule in §8), `value_dtype`, `index_map` (`i32le+zlib`) |
| `text_slots.subtitle`: `text`, `font`, `size_px`, `x`, `y`, `ha`, `va`, `color`, `snap`, `dy` | `text.value`, `font`, `size_px`, `x`, `y`, `ha`, `va`, `color`, `snap`, `offset: [0, dy]`, `layout`, `rotation` |

`docs/examples/qpf.manifest.json` and `grid.manifest.json` are these two prototype files
translated to 1.0. Their member sizes and digests are those of the prototype's real members
under the new names. `minimal.manifest.json` is an illustrative non-georeferenced file
whose `layers/background.png` digest is a placeholder.

Deliberately left to later minor versions:

- markers for flattened static runs and lazy-loading hints (Session 12);
- `zstd` encodings, revisited when Python 3.14 is the minimum (stdlib `compression.zstd`);
- text rotation;
- further projections for polygon slots.
