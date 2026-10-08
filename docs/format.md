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
`(left, top)`; parts outside the canvas are discarded. The reference operator is Pillow's
`Image.alpha_composite`. Implementations MAY differ from it and from a single-canvas
Matplotlib render by rounding. Session 02 measured at most 3–5 levels per channel when
stacking Agg-rendered layers.

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
adds `"colorbar": {"slot": "<grid layer id>"}`; the slot MUST name a grid layer in the same
file. In 1.0 it is rendered exactly like a raster. A later minor version adds the
parameters needed to redraw it when the slot's normalisation or colormap changes
(Session 11).

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
  evaluated in `value_dtype` (default `float32`):
  1. `x = (value − vmin) / (vmax − vmin)`; then `x = x · n_colors`;
     where `x == n_colors`, set `x = n_colors − 1`.
  2. The row is `under` (`n_colors`) if `x < 0`, `over` (`n_colors + 1`) if
     `x ≥ n_colors`, `bad` (`n_colors + 2`) if the value is NaN or masked, otherwise
     `trunc(x)`.
  3. The colour is LUT row `row`. If `alpha` is not `null`, replace the alpha channel by
     `floor(alpha · 255 + 0.5)`.
- `colormap`: the LUT member decodes (`rgba8` or `rgba8+zlib`) to `n_colors + 3` RGBA rows,
  i.e. `(n_colors + 3) × 4` bytes: `n_colors` colours followed by the `under`, `over`, and
  `bad` colours. `extend` (`neither|min|max|both`, default
  `neither`) only affects colorbars; `under` and `over` colours are always applied.
- Pixels with index `−1` are transparent.
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
  continuous pixel coordinates and filled with the **even-odd rule** over all of the record's
  rings. Holes and islands inside holes follow from the rule, and no ring nesting
  information is needed.
- **Edges.** With `supersample = k` (1–8; default 1), a pixel's coverage is the fraction of
  its `k × k` sub-pixels whose centres are inside the filled region. The bin colour is then
  composited source-over with that coverage onto the slot's earlier records. `k = 1` gives
  hard edges. Session 02 chose 2 as the default trade-off: k = 1, 2 and 4 cost 48, 81 and
  113 ms (median per QPF product); they differ from Agg's anti-aliased edges in 0.43, 0.23
  and 0.05 % of clear-map pixels by more than 32 levels.
- **Clipping.** The result is clipped to the clip region (§5.3) and to the layer's placed
  box.
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
          "snap": "round", "offset": [0.0, -1.0], "rotation": 0.0}}
```

- `value`: the current text (one line).
- `font`: a member holding a TrueType/OpenType font. Readers MUST fail clearly if it is
  missing or unusable, and MUST NOT fall back to another font.
- `size_px`: the font size in pixels (`points × dpi / 72`).
- `color`: RGBA.
- `x`, `y`: the anchor in canvas pixels.
- `ha` ∈ `left|center|right` (default `left`) and `va` ∈ `top|center|baseline|bottom`
  (default `baseline`): the alignment of the text box to the anchor.
- **`layout: "matplotlib"`** (the only value in 1.0). Measure the ink box of `value`
  relative to the baseline origin, `(l, t, r, b)` with `t < 0` above the baseline, and the
  ink box of `"lp"`. Let `w = r − l`, `height = max(b − t, b_lp − t_lp)`, and
  `descent = max(b, b_lp)`. The box's left edge is `x − {0, w/2, w}[ha]`. The baseline is at
  `y − descent` (bottom), `y + height − descent` (top), `y` (baseline), or
  `y + height/2 − descent` (center). The glyph origin is `(box_left − l, baseline)`.
- `snap`: `"round"` rounds the glyph origin to whole pixels, as Matplotlib's Agg backend
  does; `null` keeps it fractional.
- `offset`: `[dx, dy]` pixels added after snapping. Agg draws text one pixel higher, so
  files authored from Agg use `[0, −1]`.
- `rotation`: MUST be 0 in 1.0.
- `member` (optional): the rendered pixels of the current `value`, placed at
  `left`/`top`.

Measured in Session 02: at 45.8 px, Pillow text matches Agg with no pixel off by more than
32 levels. At 16.7 px, the glyphs differ visibly in about 27 % of text pixels, because
Matplotlib forces FreeType auto-hinting and Pillow does not expose it. Session 09 decides
how to handle small text.

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

- colorbar redraw parameters (Session 11);
- markers for flattened static runs and lazy-loading hints (Session 12);
- `zstd` encodings, revisited when Python 3.14 is the minimum (stdlib `compression.zstd`);
- text rotation;
- further projections for polygon slots.
