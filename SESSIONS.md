# CartoStack build sessions

This is the implementation checklist and handoff record for building CartoStack one session at a time. `docs/design.md` (the README until Session 14) is the architectural proposal; `README.md`, `docs/api.md` and `docs/format.md` describe what exists; this file records what has actually been built and verified. Proposed paths, APIs, and commands below become authoritative only when their session implements them.

**Objective.** CartoStack is a layered map file (`.cstack`): N×M RGBA layers plus metadata describing each layer, the canvas, and how geographic data maps to pixels. A lightweight runtime that needs only NumPy and Pillow loads the file, swaps in or adds only the new data (grid values, classified polygons, text), stacks the layers, and writes the image, much faster than building the map with Matplotlib and Cartopy. Matplotlib and Cartopy are build-time authoring tools; they are never imported on the runtime path. Performance comes from the file design and the small runtime, not from caching inside a Matplotlib workflow.

## Current handoff

- Last updated: 2026-10-09.
- Active session: none.
- Last completed session: **15 — Distribution validation and v0.1 readiness** (2026-10-09). **v0.1 is release-ready pending maintainer actions** (`CONTRIBUTING.md` → "Releasing"): commit, merge to `main` and push, set the version, publish (TestPyPI first). Publishing needs an explicit request.
- Next implementation session: **16 — Workload profiling and next extension decision**. Sessions are listed in the index in execution order.
- Repository implementation state: installable `cartostack` package skeleton (`pyproject.toml`, `uv.lock`, `src/cartostack/` with `errors.py`, `geometry.py`, `manifest.py` (format 1.0 validation, plain Python), `schemas/manifest-1.schema.json`, `layers.py` (immutable layer objects), `io.py` (encodings, verified and atomic archive I/O), `compositor.py` (source-over stacking, placement, flattened-run reuse, PNG encoding), `projection.py` (NumPy LCC), `polygons.py` (exact even-odd polygon fill, classification, GeoJSON-like input), `grids.py` (Matplotlib-exact normalisation and colormap, index-map gather), `colorbars.py` (colorbar redraw: Matplotlib-exact ticks and labels), `text.py` + `_opentype.py` (Matplotlib 3.11 text layout with GPOS kerning and GSUB ligatures, Pillow rendering) and `scene.py` (`Scene.load`/`save`/`render`/`save_png`, the editing API `replace_layer`, `add_layer`, `remove_layer`, `update_layer`, `show`/`hide`, plus `replace_polygons`, `replace_grid`, `stale_colorbars`, `redraw_colorbar`, `replace_text` and `scene.text[...]`); `cartostack.build` (the `build` extra: `SceneBuilder`, `georef.py`, `index_map.py`); `docs/format.md` and `docs/examples/`, `tests/`, `CONTRIBUTING.md`). All slot kinds render, and scenes are authored from Matplotlib/Cartopy figures. Shared fixture script `scripts/fetch_fixtures.py`. Configured standalone benchmarks (PEP 723, each with a `.lock` file): `baseline_cartopy.py` (synthetic grid), `baseline_qpf.py` (QPF), and the Session 02 prototype `prototype_build.py`, `prototype_runtime.py` (NumPy + Pillow only), `prototype_compare.py`. Reference results (gitignored): `benchmarks/results/20261008T145213Z/` (grid), `qpf-20261008T152044Z/` (QPF), `prototype-20261008T155139Z/` (prototype files, runtime and comparison), `compositor-20261008T181244Z/` (Session 06 compositing/encoding timing).
- Benchmark figures: `uv run benchmarks/report.py` → `benchmarks/results/report-<stamp>/` (`index.md` lists the figures and tables). The current report is `report-20261009T193920Z/`. Its package series come from the **authored** files (QPF: `authored-20261009T174311Z/` → `package-qpf-20261009T193907Z/`; grid with a colorbar layer: `authored-20261009T185718Z/` → `package-grid-20261009T193917Z/`); the QPF 1×/2× comparison records still come from `package-qpf-20261009T165539Z/`, because the report keeps the newest native record per series, workload, mode and config. Speed-ups on the report's common basis (all series exclude measurement-only work and process exit) are smaller than some Session 02 log figures, which compared the baseline's process wall with the prototype's in-process time: QPF cold 35.7× (log ≈ 37×), QPF 18-product loop 7.6× (log ≈ 8.8×), QPF per product 5.8×, grid cold 131.5×, grid warm 8.1× (log ≈ 10.5×). Quote the report's numbers from now on.
- Polygon slots (Session 08b) are exact rather than Pillow-approximated: `cartostack.polygons` fills by the format's sample-centre rule (§9) with a sorted-crossings scanline, and blends with Pillow's integer operator. Writers SHOULD use `supersample: 4` (Session 10's `SceneBuilder` default). Two costs found while benchmarking, left for Session 12/16: (1) a cold process's first `render()` composites the bottom static run onto a transparent canvas and caches it (~10–18 ms at 2210 × 1848; the prototype started from the base image); (2) text slots will carry the subtitle (the benchmark swaps in a `TextSlot` with pixels from the prototype's Pillow code until Session 09).
- Before starting Session 16: the release benchmark is `report-20261009T205609Z/` (installed wheel). Profile from the latest `package-qpf-*`/`package-grid-*` results. There is no CI (removed by user decision 2026-10-08); checks are run locally. `resources/` is gitignored (user decision 2026-10-08) and is never committed.

## How to execute and update a session

1. Read this handoff, the requested session, its prerequisites, and the relevant `docs/design.md` sections. Implement one numbered session per request unless the user explicitly requests more.
2. Before implementation, change that session's index status to `in_progress`, update the active session above, and check that its prerequisites are `completed`. Have at most one active session.
3. Work through its checkboxes. Check a box only when its deliverable or behavior exists and its relevant verification passes. Add meaningful tests alongside implementation; later integration sessions do not postpone earlier testing.
4. Run the session's verification. Record the exact commands and outcomes, including visual artifacts, measurements, or environment prerequisites where relevant. A skipped or failed required check does not count as passing. Once Session 07b is complete, a session that records timings writes them in the report's common results schema, regenerates the benchmark report, inspects the figures, and links them in its log entry.
5. At every session handoff, including partial or blocked work, update this file: checkboxes, index status, current handoff, and a new completion-log entry. Record decisions, deviations, and the next concrete action. Record configured commands in [Development commands](#development-commands) below (this file is version-controlled; `CLAUDE.md` is gitignored and only points here), and update the repository-state bullets in `CLAUDE.md` if they become stale.
6. Mark the session `completed` only after every completion checkbox and required verification passes. If unfinished, keep it `in_progress`, or use `blocked` with a specific blocker and recovery action. Point the next session to unfinished work until it is resolved.
7. If a session proves too large, split its remaining work into a new session with explicit prerequisites; preserve existing IDs and completed records. Do not silently drop acceptance criteria.

Statuses: `planned`, `in_progress`, `blocked`, `completed`. The index is the source of truth for overall session status; checklists show individual deliverables, and the log holds verification evidence. Completing a session does not authorize a Git commit, publication, or deployment.

Reusable request:

> Implement Session NN from SESSIONS.md. Complete its verification, update the session checklist, index, current handoff, and completion log, and stop after that session.

## Session index

| ID | Session | Prerequisite | Status |
| --- | --- | --- | --- |
| [00](#session-00) | Session tracker and handoff workflow | None | completed |
| [01](#session-01) | Standalone benchmark baseline | 00 | completed |
| [01b](#session-01b) | QPF workload baseline | 01 | completed |
| [02](#session-02) | File-based prototype benchmark and feasibility results | 01b | completed |
| [03](#session-03) | Python package, tooling, and initial CI (CI later removed) | 02 | completed |
| [04](#session-04) | `.cstack` format specification v1 | 03 | completed |
| [05](#session-05) | Core reader/writer and layer model | 04 | completed |
| [06](#session-06) | Compositor, cropped placement, and PNG output | 05 | completed |
| [07](#session-07) | Layer editing API | 06 | completed |
| [07b](#session-07b) | Benchmark report and comparison figures | 07 | completed |
| [08b](#session-08b) | Polygon slots: classified polygons to pixels with NumPy/Pillow | 07b | completed |
| [08](#session-08) | Grid slots: values to pixels with NumPy | 07b | completed |
| [09](#session-09) | Text slots with Pillow | 08, 08b | completed |
| [10](#session-10) | Build-side authoring from Matplotlib/Cartopy | 09 | completed |
| [11](#session-11) | Colorbars and legends | 10 | completed |
| [12](#session-12) | Archive lifecycle and storage defaults (lazy decoding only if measured) | 11 | completed |
| [13](#session-13) | Core-only runtime and source-independence verification | 12 | completed |
| [14](#session-14) | End-to-end examples and public API documentation | 13 | completed |
| [15](#session-15) | Distribution validation and v0.1 readiness | 14 | completed |
| [16](#session-16) | Workload profiling and next extension decision | 15 | planned |

Milestones:

- **After 02:** measured proof that a file-based, core-only runtime beats a full Cartopy render by enough to justify the package.
- **After 07b:** speed and accuracy figures comparing the standard Matplotlib/Cartopy render with CartoStack, regenerated by every later session that records timings.
- **After 08b:** the primary workload (QPF loop) runs on the package itself, benchmarked against the Cartopy baseline and the Session 02 prototype.
- **After 09:** working core runtime: load a hand-built `.cstack`, replace grid values, classified polygons, and text, add a layer, composite, and write PNG using only NumPy and Pillow.
- **After 11:** build-to-runtime round trip: a file authored from Matplotlib/Cartopy renders, after updates, to match a full Cartopy render of the same inputs.
- **After 13:** portability verified in fresh environments without source data or rendering libraries.
- **After 15:** v0.1 release candidate.

## Target v0.1 package

Sessions 03–15 produce one installable `cartostack` package. The editing and output names on `Scene` were settled in Session 07; `replace_polygons(id, records, values)` (08b), `replace_grid(id, values, *, vmin=None, vmax=None, lut=None)` (08), `scene.text[id] = value` / `replace_text(id, value)` (09) and `SceneBuilder` (10) are settled, as are `add_colorbar` / `redraw_colorbar` (Session 11).

```python
# Build machine: pip install cartostack[build]  (Matplotlib >= 3.11 + Cartopy); settled in Session 10
from cartostack.build import SceneBuilder

b = SceneBuilder.new(projection, extent, width=1200, height=800, dpi=100, axes=(0.02, 0.13, 0.96, 0.78))
# or wrap an existing figure: SceneBuilder(fig, ax, dpi=300, crop="tight")
b.add_static("below", order=0, background=True, draw=draw_land_ocean)      # or zorder=(lo, hi), artists=[...]
b.add_grid_slot("temperature", order=20, lon=lon, lat=lat, cmap="coolwarm", vmin=-30, vmax=40, alpha=0.8)
b.add_polygon_slot("qpf", order=25, bins=qpf_bins, fallback=("gray", 10), round_decimals=2)  # supersample=4
b.add_static("above", order=30, draw=draw_borders)
b.add_text_slot("title", title_artist, order=90)                           # or x=, y=, s=, fontsize=, ...
b.save("northeast.cstack")                                                  # build(rest=...) sweeps leftovers
b.add_colorbar("cbar", cbar, slot="temperature", order=95)                # redrawn at runtime on a new norm/LUT

# Runtime worker: pip install cartostack  (NumPy + Pillow only)
from cartostack import Scene

with Scene.load("northeast.cstack") as scene:
    scene.replace_grid("temperature", t2m)
    scene.replace_polygons("qpf", rings, values)   # lon/lat rings, one value per polygon
    scene.text["title"] = "2-m Temperature"
    scene.add_layer("logo", logo_rgba, order=93, left=1100, top=740)
    scene.save_png("t2m.png")                      # mode="RGBA"|"RGB", compress_level=0..9

# Settled in Session 07 (layers are immutable; each edit swaps in a validated new layer object):
scene.replace_layer("above", rgba)                 # or scene["above"] = rgba / a Layer with that id
scene.add_layer(layer)                             # or add_layer(id, rgba, order=..., left=, top=, ...)
scene.remove_layer("logo")                         # or del scene["logo"]
scene.update_layer("cbar", order=96, left=10, top=700, opacity=0.8, visible=True)
scene.hide("counties"); scene.show("counties")
rgba = scene.render()                              # (height, width, 4) uint8
```

Guarantees checked by tests: the runtime path imports neither Matplotlib nor Cartopy (checked through `sys.modules` in a fresh process), opens no source geospatial files, decodes no layer it does not need, and produces output matching a full Cartopy render of the same inputs within documented tolerances. Polygon slots project lon/lat with a NumPy implementation of the stored projection (Lambert Conformal Conic first; decided 2026-10-08), so the runtime needs no pyproj. Ad hoc runtime overlays (stations, warning outlines) outside declared slots remain a post-v0.1 extension. A file may declare any number of data slots of either kind, including just one.

## Environment (observed 2026-10-07)

Recorded so sessions do not re-derive it; re-check before relying on it.

- Machine: Apple M3 Pro, macOS (Darwin 25.6). Git remote `origin` is GitHub (`dvanhoesen/CartoStack`). No CI: GitHub Actions was removed on 2026-10-08 by user decision.
- `uv`: installed 2026-10-08 — `uv 0.12.23 (Homebrew 2026-10-03 aarch64-apple-darwin)` at `/opt/homebrew/bin/uv`.
- Existing interpreters: Anaconda Python 3.11.8 (`cartopy 0.25.0`, `matplotlib 3.10.6`, `numpy 2.3.0`, `Pillow 11.3.0`, `pyproj 3.6.1`, `shapely 2.1.1`, `pytest 7.4.0`, `mypy 1.8.0`) and Homebrew Python 3.13. Use uv-managed environments, not the Anaconda base environment, for recorded results.
- Cartopy Natural Earth cache (`~/.local/share/cartopy/shapefiles/natural_earth/`): 10m/50m/110m land, ocean, lakes, coastline, rivers, `admin_1_states_provinces_lakes`, `admin_0_boundary_lines_land`. **Missing:** `ne_10m_populated_places` (city labels).
- County fixture (added by the user 2026-10-08): `resources/NYS_Shoreline_Counties/` — "NYS County Boundaries – Shoreline Version" (NYS ITS GIS Program Office, published January 2024; counties clipped to major shorelines). Polygon shapefile, 62 records, WGS 84 geographic (`.prj`), UTF-8 (`.cpg`), bbox `[-79.762, 40.4961, -71.8561, 45.0129]`, 306,418 vertices (high detail, a realistic stress case). Fields include `NAME`, `ABBREV`, `FIPS_CODE`, `POP2020`. The base name contains a space (`NYS Counties.*`); quote paths. QGIS metadata is in `NYS Counties.qmd`. Licence/redistribution terms not yet recorded. `resources/` is gitignored and must not be committed (user decision 2026-10-08). SHA-256:
  - `NYS Counties.shp` `c3fdde4d0c7f6ef3194f6e92940ff82b93c219393e28f25c4b4cdf7c8ae2ed24` (4,908,948 B)
  - `NYS Counties.shx` `41d740903605db3223085e6ea0844e301f0f81ff2878acc3d90c8802ddf1e362`
  - `NYS Counties.dbf` `f5bdbc93a2f0f64f889cae66e31634b12067dcb5cc41949566cedadd016952b8`
  - `NYS Counties.prj` `a02a27b1d1982c8516d83398e85a3c8b1aef1713c13ef4d84d7bde17430c07c4`
  - `NYS Counties.cpg` `3ad3031f5503a4404af825262ee8232cc04d4ea6683d42c5dd0a2f2a27ac9824`
  - `NYS Counties.qmd` `b29add816be3a45c6f58c18c5f757177b2a96b8e29ae205c0922e5c60e8bf519`
- Font: Matplotlib's bundled DejaVu Sans is available and is the deterministic default for text. The same TTF can be embedded in `.cstack` files for Pillow text rendering.
- PyPI (checked 2026-10-08): `cartostack` (chosen name) is unregistered. Latest releases: NumPy 2.5.3 (`requires_python >=3.12`), Matplotlib 3.11.2 (`>=3.11`), Cartopy 0.26.0 (`>=3.11`), Pillow 12.3.0 (`>=3.10`). Name decided 2026-10-08: distribution/import `cartostack`, file extension `.cstack` (the working name "GeoScene" conflicts with Esri China's GIS platform brand).

## Session 01 workload specification

Proposed concrete values for the benchmark. Session 01 may change them, but must record the final values in its log entry, and later sessions reuse them as the reference scene.

| Item | Value |
| --- | --- |
| Canvas | 1200 × 800 px at 100 DPI (`figsize=(12, 8)`), the README "Small" scene |
| Projection | `LambertConformal(central_longitude=-76, central_latitude=42.5, standard_parallels=(40.5, 44.5))`, default globe (New York–centred, revised 2026-10-08 to match the county fixture) |
| Extent | `[-80.5, -71.5, 40.2, 45.3]` in PlateCarree (New York State plus margins; previously the wider Northeast `[-82, -66, 37, 48]`) |
| Axes | Fixed `fig.add_axes` rectangle leaving room for title above and a horizontal colorbar below; record the **resolved** position after Cartopy's aspect adjustment (`ax.get_position()` after a draw), because that, not the requested rectangle, is the scene geometry |
| Below data | ocean, land, lakes (Natural Earth 10m) |
| Data | Deterministic synthetic 500 × 500 regular lon/lat grid (cell centres) over `[-81, -71] × [40, 46]` (covers the extent so most cells are visible), analytic `float32` field (`make_field(variant)`), `cmap="coolwarm"`, `vmin=-10`, `vmax=35`, `pcolormesh(..., shading="nearest", alpha=0.8)` so compositing over the static layers below is exercised |
| Above data | New York counties (`resources/NYS_Shoreline_Counties/NYS Counties.shp`, see [Environment](#environment-observed-2026-10-07)), states, country borders, coastline, city markers and labels (`ne_10m_populated_places`, filtered to the extent and `SCALERANK <= 7`: 34 cities) |
| Text/decorations | title (18 pt), subtitle/valid time (12 pt), horizontal colorbar (`extend="both"`, label `°C`); DejaVu Sans regular |
| Fixtures | `scripts/fetch_fixtures.py` copies cached Natural Earth files and the county shapefile from `resources/`, downloads only `ne_10m_populated_places`, and writes them to gitignored `benchmarks/data/`, recording sources and SHA-256 (verifying the county hashes recorded under Environment); the benchmark reads only those local paths and fails rather than downloading |
| Timing | Two modes, both recorded. **Cold process (primary):** ≥5 fresh `subprocess` runs measuring interpreter start, imports (`numpy`, `PIL`, `matplotlib`, `cartopy` separately), and end-to-end wall time, because each cron product pays these costs. **Warm in-process:** 1 warm-up plus ≥10 timed repetitions; median and min; `time.perf_counter` per phase. Cartopy keeps in-process geometry caches (Natural Earth geometries and projected paths), so warm repetitions understate a cron job's cost. Memory: `tracemalloc` and peak RSS |
| PNG encoding | Encode baseline and file-based outputs with identical Pillow settings (record `compress_level`, mode RGB vs RGBA); also time `compress_level=1`, since encoding is likely to become the dominant recurring cost |
| Outputs | gitignored `benchmarks/results/<UTC timestamp>/` (PNG plus `results.json`); summarize the numbers in the completion log |

## QPF workload specification

The primary real-world workload, from `resources/slow_example.py` (user-supplied 2026-10-08): NOAA WPC Quantitative Precipitation Forecast maps masked to New York State, about 18 products per run in one process. Session 01b confirms these values and records final ones in its log.

| Item | Value (from the example) |
| --- | --- |
| What changes per product | Only (1) the QPF polygons (`ax.add_geometries(geoms, ...)` per polygon, 17 discrete colour bins, `zorder` 10–26, drawn in ascending QPF order) and (2) the italic subtitle `subtitle_attrib` (valid/issue times). Everything else is static. |
| Out of scope for timing | Downloading/extracting the WPC tarballs. Measurement starts from QPF shapefiles on disk; reading them into lon/lat polygons is timed separately from rendering. |
| Canvas | `figsize=(10, 8)`, `savefig(dpi=300, bbox_inches="tight", pad_inches=0)` → observed 2210 × 1848 RGBA. The tight crop is resolved once and fixed; the runtime rejects changes that would alter it (the subtitle lies inside the axes). |
| Projection / extent | `LambertConformal(central_longitude=-75.85, central_latitude=42.95)` (centre of the bounds; Cartopy's default standard parallels 33° and 45°, globe WGS84), extent lon `[-80.0, -71.7]`, lat `[40.3, 45.6]`; polygons pre-clipped to the bounds ±5° |
| Below data | white background, coastline (`zorder` 5) |
| Above data (static, flattenable) | states except NY and lakes (60), counties (62), NY mask white α 0.75 (75), city markers/bold labels with white stroke (90–91), logo (95), title bar (995), bold title, legend, attribution (1000) |
| Text slot | subtitle: DejaVu Sans Oblique 11 pt, centred at axes (0.5, 0.915), bottom-aligned, above the title bar |
| Fixtures | Copies of `/Users/danielvanhoesen/swrcc/resources/prepared_gpkg/*.gpkg` and the SWRCC logo, plus a one-time snapshot of several WPC QPF products (e.g. Day 1, Day 1-3, Day 1-7, a 6-hour product), all in gitignored `benchmarks/data/` with SHA-256; never re-downloaded by the benchmark |
| Timing | Cold process for one product; and the realistic loop: one process rendering every snapshot product, reporting imports, static-source load, and per-product read/build/draw/encode. Per-product draw split into data polygons, static above, text/decorations. PNG encode at the full 2210 × 1848 size. |

## Proposed defaults and design constraints

Validate these defaults while implementing the relevant session, and record any change in the completion log:

- One `cartostack` distribution using `src/cartostack/`. Core dependencies are NumPy and Pillow only; core covers loading, editing, grid and text slots, compositing, saving, and PNG output. A `build` extra adds Matplotlib and Cartopy for authoring (`cartostack.build`). A later `geo` extra may add pyproj for runtime coordinate overlays. Importing `cartostack` must never import an extra.
- Use `uv`, Hatchling, pytest, Ruff, and mypy. Python 3.12+ is the candidate minimum: current NumPy requires 3.12, and SPEC 0 (followed by NumPy/Matplotlib/Cartopy) has already dropped 3.11. Confirm against actual dependency support before declaring it.
- The `.cstack` container is a ZIP with a JSON manifest, borrowing from OpenRaster (`.ora`: ZIP of PNG layers plus a stack description) and GeoTIFF conventions (CRS plus an affine pixel transform). Members are stored uncompressed in the ZIP (`ZIP_STORED`) so the layer encoding alone determines decode cost. Decided in Session 02: default layer encoding is zlib-compressed raw RGBA (stdlib, smallest file, ~4–5 ms per 4 MP layer decode); PNG remains supported as a viewable encoding, raw for debugging. The format records the encoding per layer. zstd is reconsidered when the minimum Python is 3.14 (stdlib `compression.zstd`).
- The format is versioned and specified in `docs/format.md` before code depends on it. Readers reject unknown major versions and ignore unknown optional fields within a major version. No pickle, live figures, or serialized callbacks.
- Raster layers use `(height, width, 4)` `uint8` RGBA with a top-left origin and straight (non-premultiplied) alpha, matching Matplotlib Agg's `buffer_rgba()`, PNG, and Pillow's `Image.alpha_composite`. Measured in Session 02: Pillow stacking of Agg-rendered layers stays within 3–5 levels of the single-canvas render (17–45 % of pixels differ by 1–3). Matplotlib's own integer blend (`fixed_blender_rgba_plain`) for partly transparent pixels makes it exact on all but 1.4 %, but costs ~290 ms per 4 MP frame in NumPy; it is an option, not the default.
- Every layer shares the file's canvas geometry: pixel size, DPI, axes rectangle, projection, extent, and clipping. Pixel `(x, y)` means the same location in every layer. Layers may be cropped and placed at an integer offset. Geometry is immutable; operations that would change it fail explicitly.
- Grid slots store a pixel→cell `int32` index map (−1 = no data) plus normalization and a colormap lookup table, so new values render with a NumPy gather and lookup. Produce index maps at build time with the index-image method (Session 10). This is georeferencing data in the file, not a cache.
- Polygon slots store a bin table (value ranges → colours and draw order) and use the file's georeferencing. The runtime projects lon/lat rings with NumPy (Lambert Conformal Conic forward formula, verified against pyproj in build-extra tests), maps them to pixels with the stored affine transform, fills them in draw order (holes included) into a `uint8` bin-index image, clips to the map area, and colours it through the bin LUT. Pillow polygon fill is not anti-aliased; supersampling (2–4×) then downsampling is the dependency-free way to approach Agg's edges — measure the difference and cost in Session 02.
- Text slots store the text style, anchor, embedded font file, and the rendered pixels. The runtime renders replaced text with Pillow/FreeType; differences from Matplotlib text are measured and documented.
- Static layers may sit both below and above data. Preserve global ordering; contiguous runs of unchanged layers may be flattened into one composite for speed, with individual layers kept for visibility/order changes.
- Saved files must render without original geospatial sources.
- Define visual tolerances from inspected differences; never use a permissive tolerance to hide geometry shifts.
- Matplotlib is not thread-safe: parallel layer authoring uses processes, not threads.
- Native code, dirty rectangles, composite trees, tiling, and GPU work remain profiling-driven extensions after v0.1. Reuse existing native libraries (NumPy, Pillow, a faster PNG encoder, pyresample) before writing Rust/C.

## Session checklists

### Session 00

**Goal:** Make incremental implementation and progress updates durable across future sessions.

**Deliverables:** `SESSIONS.md`, an entry point in `README.md`, and session-update instructions in `AGENTS.md`.

- [x] Convert the implementation plan into ordered, independently scoped sessions with prerequisites and verification gates.
- [x] Define partial-work, blocked-work, completion, and handoff update rules.
- [x] Link the tracker from the README and add the update protocol to repository instructions.
- [x] Review the document links, session ordering, initial statuses, and whitespace; log the actual results.

**Verification:** Documentation review and file/diff checks only. Do not report Python tests or benchmarks as run before they exist.

### Session 01

**Goal:** Produce a reproducible conventional Cartopy baseline without scaffolding the library.

**Deliverables:** `scripts/fetch_fixtures.py`, `benchmarks/baseline_cartopy.py`, `.gitignore` entries for `benchmarks/data/` and `benchmarks/results/`, and baseline artifacts.

- [x] Record the chosen workload, projection, extent, dimensions, DPI, grid shape, font, and dependency versions (start from the [workload specification](#session-01-workload-specification)); record `uv --version`.
- [x] Prepare fixtures with `scripts/fetch_fixtures.py` (one-time, network allowed), recording URLs, file sizes, and SHA-256.
- [x] Create a standalone benchmark using pinned PEP 723 inline script metadata, run with `uv run benchmarks/baseline_cartopy.py`.
- [x] Draw representative land/water, boundaries, labels, data, title/subtitle, and colorbar in their correct drawing order.
- [x] Keep source acquisition outside measured operations; document how prepared fixtures are reused without network access.
- [x] Record cold-process timings (interpreter start, per-package import time, end-to-end) and warm in-process timings; note which Cartopy in-memory caches the warm runs benefit from.
- [x] Break the full render into per-group draw time (static below data, data, static above data, text/colorbar) and PNG encoding.
- [x] Record the resolved axes rectangle and output pixel size; Session 02 must reproduce them exactly. Save the baseline image.

**Verification:** Run the baseline repeatedly with identical inputs. Confirm fixed pixel dimensions and reproducible output in the recorded environment. State whether on-disk source caches were warm or cold.

### Session 01b

**Goal:** A reproducible Cartopy baseline for the real QPF workload, excluding data acquisition.

**Deliverables:** QPF and SWRCC fixtures added to `scripts/fetch_fixtures.py`, `benchmarks/baseline_qpf.py` (PEP 723, pinned, locked), and baseline artifacts.

- [x] Extend `fetch_fixtures.py`: copy the prepared GeoPackages and logo with SHA-256, and snapshot several WPC QPF products once (record URLs, issue times, sizes, SHA-256).
- [x] Port the example's drawing code faithfully into `benchmarks/baseline_qpf.py`, reading only fixtures; record deviations (e.g. pyshp/shapely instead of GeoPandas if chosen, with its effect on read time).
- [x] Time a single cold product and the realistic multi-product loop in one process (imports, static-source load once, then per product: QPF read, clip, artist build, draw split into data/static-above/text, PNG encode at full size with matched Pillow settings, and Matplotlib `savefig` as used today).
- [x] Record the resolved tight-crop box, the axes rectangle within it, output size, projection, and fonts (bold, oblique); Session 02 must reproduce them.
- [x] Save baseline images per product and compare one against the existing production output for the same product if available.

**Verification:** Repeat runs give identical pixels and size; record cache state and the per-product numbers in the log.

### Session 02

**Goal:** Prove that a layered file plus a NumPy/Pillow-only runtime produces the same image much faster than the Cartopy baselines (QPF primary, synthetic grid secondary), and choose the layer storage encoding.

**Deliverables:** `benchmarks/prototype_build.py` (writes a prototype layered file from the Session 01 scene), `benchmarks/prototype_runtime.py` (core-only runtime), reference/runtime/difference images, and machine-readable measurements.

- [x] Build step: render each static group (below data, above data) and the title into transparent RGBA images from figures with identical size, DPI, and resolved axes position; never use `bbox_inches="tight"`.
- [x] Build step: produce the data grid's pixel→cell index map with the index-image method (draw `pcolormesh(antialiased=False, linewidth=0)` with per-cell face colors encoding the flat cell index; decode to `int32`, −1 where empty). Store it with the colormap LUT and `vmin`/`vmax`.
- [x] QPF: store the static-below and flattened static-above layers, the bin table, and the projection parameters; at runtime project polygon rings with a NumPy LCC implementation (check against pyproj at build time), fill them in QPF order into a bin-index image (with and without supersampling), and render the subtitle. Measure the per-product loop with the file loaded once.
- [x] Write a prototype ZIP (JSON manifest plus layer members) in two layer encodings: PNG, and raw arrays compressed with zstd (or `.npy` if zstd is unavailable). Record file sizes.
- [x] Runtime step, importing only NumPy and Pillow (assert via `sys.modules`): load the file, render new grid values via gather → normalize → LUT → apply alpha, render new QPF polygons, render a new title with Pillow and the same TTF, composite in order, encode PNG.
- [x] Compare the runtime output with a full Cartopy render of the same updated inputs; record pixel differences separately for the data, static, and text regions and inspect them. Text is expected to differ slightly (Pillow vs Matplotlib).
- [x] Measure cold-process time for the runtime split into interpreter start, imports, manifest read, per-layer decode (each encoding), grid render, text render, compositing, and PNG encoding (`compress_level` 6 and 1), plus peak memory.
- [x] Report speedup versus the Session 01 cold and warm baselines, and the authoring time and break-even reuse count.
- [x] Record decisions: default layer encoding, whether contiguous static runs should be stored pre-flattened, and any feasibility blocker to resolve before library scaffolding.

**Verification:** Repeat runtime updates with several different data/title inputs, each compared against its own full render. Store visual evidence and measurements outside source files, with paths and results recorded in the log.

### Session 03

**Goal:** Establish an installable Python package and repeatable developer workflow.

**Deliverables:** `pyproject.toml`, `uv.lock`, `src/cartostack/`, `tests/`, contributor setup instructions, and initial CI.

- [x] Re-verify that `cartostack` is still available on PyPI (and unclaimed on conda-forge) before creating `src/cartostack/`; if taken, stop and ask for a new name.
- [x] Configure the Python range, Hatchling, NumPy/Pillow core dependencies, the `build` extra (Matplotlib, Cartopy), and the development dependency group.
- [x] Configure Ruff, mypy, and pytest, creating modules only as they are needed.
- [x] Add GitHub Actions CI with a core-only job (no extras installed) and a `build`-extra job from the start, plus a test asserting `import cartostack` imports neither Matplotlib nor Cartopy.
- [x] Move reusable fixture preparation from `benchmarks/` into a documented script the test suite can share; keep network access out of test runs.
- [x] Document exact setup/check commands and update stale repository-state guidance in `CLAUDE.md`.

**Verification:** Fresh-environment installation, import smoke test, configured format/lint/type/test checks, and distribution build. Record which environments have which extras installed.

### Session 04

**Goal:** Specify `.cstack` format version 1 before code depends on it.

**Deliverables:** `docs/format.md`, a machine-checkable manifest schema, `cartostack/geometry.py`, and example manifests.

- [x] Container: ZIP layout, member naming, `ZIP_STORED` members, manifest location, and format version with major/minor compatibility rules.
- [x] Canvas geometry: pixel width/height, DPI, axes rectangle in pixels (top-left origin), clipping, and a deterministic geometry fingerprint.
- [x] Georeferencing: CRS as a PROJ/WKT string plus the projection parameters as plain data sufficient for the runtime's NumPy forward projection (LCC in v1; unknown projections make polygon slots unusable with a clear error), projected extent, and a GeoTIFF-style affine transform from projected coordinates to canvas pixels.
- [x] Layer records: id, kind (`raster`, `grid`, `text`, `colorbar`), order and tie-breaking, placement offset and size, visibility, opacity, encoding, member path, and checksum.
- [x] Grid slot records: grid shape, index-map member, cell-center/edge convention, normalization (`vmin`/`vmax`, clipping/extend), colormap LUT member, masked/NaN color, and layer alpha.
- [x] Polygon slot records: bin table (ranges, colours, draw order, inclusive/exclusive edges), out-of-range colour, fill rule and hole handling, anti-aliasing/supersampling factor, clipping region.
- [x] Output crop: record a fixed output crop (e.g. a resolved `bbox_inches="tight"` box) separately from the canvas so pixel `(x, y)` stays consistent.
- [x] Text slot records: current text, embedded font member, size, color, anchor/alignment, position, rotation (if supported), and the rendered-pixel member.
- [x] Provenance metadata (CartoStack version, authoring library versions, source checksums) as optional, non-required fields.
- [x] Implement immutable geometry and manifest validation in plain Python; test invalid dimensions, inconsistent records, unknown versions, and fingerprints without importing extras.

**Verification:** Schema validation of example manifests, geometry/validation unit tests, and a review of `docs/format.md` against the Session 02 prototype's needs.

### Session 05

**Goal:** Read and write `.cstack` files and hold layers in memory safely.

**Deliverables:** `cartostack/io.py` (or `serialization/`), raster layer model, `Scene.load()` (eager), `Scene.save()`, and round-trip tests.

- [x] Implement layer objects for the v1 kinds with owned `uint8` RGBA buffers; snapshot caller arrays so later mutation cannot alter a layer.
- [x] Validate dtype, shape, placement, and geometry compatibility on creation and load.
- [x] Implement eager load and save for each supported layer encoding, writing atomically (temporary file then rename) so a failed save never corrupts an existing file.
- [x] Return actionable errors for missing members, checksum mismatches, unsupported versions, and corrupt data.
- [x] Test save → load → save byte-stable manifests and pixel-identical layers.

**Verification:** Round-trip tests, corrupted/invalid-file tests, and a check that load and save run with only core dependencies installed.

### Session 06

**Goal:** Combine layers into the final image correctly and quickly.

**Deliverables:** `cartostack/compositor.py`, cropped-layer placement, `Scene.render()` returning RGBA, and `Scene.save_png()`.

- [x] Implement source-over composition in the documented straight-alpha convention (Pillow `alpha_composite` as the reference), honouring order, visibility, and uniform opacity.
- [x] Place cropped layers at integer offsets, including layers partially outside the canvas; keep map clipping distinct from canvas placement clipping.
- [x] Flatten contiguous runs of unchanged layers and reuse them across renders; verify output equals per-layer composition.
- [x] Encode PNG (RGB or RGBA, configurable `compress_level`) without changing canvas dimensions.
- [x] Test transparent/partial-alpha/overlapping fixtures against independently computed expected pixels, and cropped versus full-canvas equivalence.

**Verification:** Exact or explicitly bounded numeric comparisons, PNG decode comparisons, and composite timing for the Session 01 scene.

### Session 07

**Goal:** Edit a loaded scene: replace, add, remove, show/hide, reorder, and save.

**Deliverables:** Public editing API on `Scene`, error types, and lifecycle tests.

- [x] Implement `replace_layer`/`scene[id] = rgba`, `add_layer`, `remove_layer`, `visible`, `opacity`, and order changes, validating against the file's geometry.
- [x] Make replacements atomic so a failed replacement leaves the previous layer usable.
- [x] Ensure edits never decode, modify, or rewrite untouched layers; saving a modified scene to a new path leaves the source file (the template) unchanged.
- [x] Reject operations that need information the file does not hold (for example restyling a raster's line width) with a clear error.
- [x] Settle public method names and record them in the [target v0.1 package](#target-v01-package) example.

**Verification:** Lifecycle tests with decode/encode spies, atomic-failure tests, and template-immutability tests.

### Session 07b

**Goal:** Show, as figures, how CartoStack's speed and accuracy compare with the standard Matplotlib/Cartopy render, and keep those figures current as the package grows.

**Deliverables:** `benchmarks/report.py` (PEP 723 with lockfile; Matplotlib is allowed here because it is a benchmark, never on the runtime path), a common timing-results schema with adapters for the existing results files, and a generated report directory `benchmarks/results/report-<stamp>/` (figures as PNG, `report.json` recording every input file and value plotted, and a short `index.md` listing the figures).

- [x] Define the common schema: series (`cartopy-baseline`, `prototype`, `package`), workload (`qpf`, `grid`), mode (`cold-process`, `warm`, `loop`), per-product records, and per-phase seconds (interpreter, imports, load/decode, data render, static content, text, compositing, PNG encode, other). Write adapters for the Session 01, 01b, 02 and 06 results; later benchmarks write the schema directly.
- [x] Select inputs explicitly (paths) or as the latest result of each kind, and record the chosen paths in `report.json` and in each figure's footer.
- [x] Figure: per-phase stacked bars for one cold product, Cartopy versus CartoStack, for QPF and grid (log or broken axis so CartoStack's phases stay readable).
- [x] Figure: cumulative wall time over the 18-product QPF loop, one line per series (shows Cartopy's first-product cost against CartoStack's flat per-product cost).
- [x] Figure: per-product time against polygon count, data-render phase and total, per series.
- [x] Figure: speed-up against accuracy (share of pixels differing by more than 8 and by more than 32 levels) for each Session 02 configuration (1×/2×/4× supersampling, Agg-exact stacking, split static layers).
- [x] Figure: speed-up summary with cold, warm and loop speed-ups shown separately, never as one headline number; show medians with min–max ranges.
- [x] Follow the `dataviz` skill for chart form and colours; one consistent colour per series across all figures.

**Verification:** Run the report on the current results; check that every plotted number equals the value recorded in the Session 01b, 02 and 06 completion-log entries; inspect each figure visually; `uvx ruff check --line-length 120 benchmarks/` passes.

### Session 08b

**Goal:** Render new classified polygons (e.g. WPC QPF) with NumPy and Pillow only.

**Deliverables:** `cartostack/projection.py` (NumPy LCC forward), polygon slot implementation, `replace_polygons()`, tests, and a package-based QPF benchmark (`benchmarks/package_qpf.py`, project environment) writing the 07b schema.

- [x] Implement ellipsoidal Lambert Conformal Conic forward projection in NumPy; test against pyproj to ≤ 1 mm in the `build`-extra environment.
- [x] Map projected rings to pixels via the stored affine transform; fill in draw order with holes into a bin-index image; clip to the map area; colour via the bin LUT.
- [x] Accept rings plus one value per polygon (and a helper for shapely/GeoJSON-like input without importing shapely); validate inputs and reject values with no bin unless an out-of-range colour is defined.
- [x] Choose the default edge treatment (none vs supersampling) from Session 02 measurements; quantify differences against Cartopy renders of the same polygons.
- [x] Test analytic polygons (squares, holes, overlaps, polygons crossing the map edge) with hand-computed expected pixels.
- [x] Benchmark the package on the QPF workload (cold single product and the 18-product loop, scenes built from the Session 02 prototype layers as in `compositor_timing.py`; subtitle as a pre-rendered raster until Session 09) and compare with both the Cartopy baseline and the Session 02 prototype. Investigate any per-product regression of more than 10 % against the prototype before completing.
- [x] Regenerate the 07b report with the `package` series.

**Verification:** Projection accuracy tests, analytic rasterization tests, QPF comparison against the Session 01b baseline, per-product timing at 2210 × 1848, and the regenerated report figures.

### Session 08

**Goal:** Render new values for a grid slot with NumPy only.

**Deliverables:** Grid slot implementation, `replace_grid()`, colormap LUT handling, correctness tests, and a package grid benchmark writing the 07b schema.

- [x] Render values via index-map gather → normalize → LUT → layer alpha, with NaN/masked values and out-of-range values following the slot's documented rules.
- [x] Validate value shape and dtype against the slot; raise a grid-mismatch error otherwise.
- [x] Allow changing `vmin`/`vmax` and colormap at runtime (LUT regenerated from a stored colormap definition or supplied by the caller) and mark dependent colorbars stale for Session 11.
- [x] Test against analytic fixtures with hand-computed expected pixels, including masks and domain edges.
- [x] Compare against the Session 02 `pcolormesh` reference and record the difference (expected: equal away from anti-aliased domain edges).
- [x] Regenerate the 07b report with the package grid series.

**Verification:** Analytic and masked-grid tests, reference comparison, timing of the grid render for 500 × 500 and 1799 × 1059 (HRRR-sized) grids, and the regenerated report figures.

### Session 09

**Goal:** Replace text with Pillow so runtime text needs no Matplotlib.

**Deliverables:** Text slot implementation, `scene.text[...]`, embedded-font handling, and text tests.

- [x] Render text with Pillow/FreeType from the embedded font at the stored size, color, anchor, and alignment; place it as a cropped layer.
- [x] Replace one text slot without touching other layers or moving any geometry.
- [x] Error clearly when a text slot has no usable font; never fall back silently to a different font.
- [x] Compare Pillow output with Matplotlib text for the same font/size/anchor; document the offset/weight differences and adjust anchor conversion to minimize them.
- [x] Verify the core-runtime milestone: a hand-built `.cstack` loads, takes new grid values, new polygons, new text, and an added layer, and writes PNG with only NumPy and Pillow installed.
- [x] Switch the package QPF benchmark's subtitle to the text slot and regenerate the 07b report, so the QPF figures show the complete per-product runtime.

**Verification:** Text placement tests, Matplotlib comparison images (in the `build`-extra environment), the core-only milestone test run locally in a core-only environment (`uv sync --group dev`), and the regenerated report figures.

### Session 10

**Goal:** Author `.cstack` files from Matplotlib/Cartopy on a build machine.

**Deliverables:** `cartostack.build.SceneBuilder`, fixed-geometry figure construction, static-layer capture, index-map generation, georeferencing export, and an authored example file.

- [x] Build figures from the canvas geometry with transparent backgrounds and fixed axes placement; resolve Cartopy's aspect adjustment once and record the resolved axes rectangle in the file.
- [x] Capture static layers via user draw callbacks into owned RGBA buffers, avoiding duplicated frames/backgrounds across layers; release figures afterwards.
- [x] Author polygon slots from a bin table (as in the QPF example), defaulting to `supersample: 4` (Session 08b measurements, `docs/format.md` §9), and resolve a `bbox_inches="tight"` output crop once at build time.
- [x] Generate grid-slot index maps with the index-image method from caller-supplied lon/lat (centers or edges, documented).
- [x] Capture text-slot definitions (font file, size, color, anchor) from the intended style and embed the font; for Matplotlib ≥ 3.11 use `snap: null` and `offset: [0, 0]` (Session 09).
- [x] Export the CRS and affine pixel transform; test that control-point lon/lat positions map to the same pixels via the stored transform (using pyproj in the test) as Cartopy draws them.
- [x] Compare a freshly authored and updated file's output with a full Cartopy render of the same inputs.

**Verification:** Authoring tests in the `build`-extra environment, control-point alignment tests, and authored-versus-baseline image comparison.

### Session 11

**Goal:** Keep colorbars and legends consistent with grid normalization without Matplotlib at runtime.

**Deliverables:** Colorbar layer kind, its dependency on a grid slot, and tests.

- [x] Author colorbars at build time; store the colorbar's geometry (gradient box, tick positions, label text/font) so the runtime can redraw it with NumPy/Pillow.
- [x] Reuse the stored colorbar when only values change; redraw it when `vmin`/`vmax` or the colormap change.
- [x] Support a static legend image (raster layer) for non-grid content.
- [x] Verify the build-to-runtime milestone: authored file → updated data, normalization, and title → output matches a full Cartopy render within documented tolerances.

**Verification:** Fixed/changed-normalization tests, colorbar comparison against Matplotlib's colorbar, and the end-to-end milestone comparison.

### Session 12

**Goal:** Settle archive lifetime and the storage defaults chosen in Session 02; add lazy decoding only if measurements show it pays.

**Deliverables:** `Scene.close()`/context-manager semantics, pre-flattened static runs in the file, cached encoded bytes for unchanged layers at save, and a measured lazy-decoding decision.

Scope reduced in the 2026-10-09 plan revision: Session 06 measured a full load of the 2210 × 1848 QPF file at 12 ms (about 4 % of a cold product), so lazy decoding is not assumed to be worth its complexity.

- [x] Define archive lifetime and close behavior; document it in `docs/format.md`/the API docs and test it.
- [x] Optionally store pre-flattened static runs in the file and use them when the layers in a run are unchanged.
- [x] Reuse a layer's encoded bytes at save until its pixels change (deferred from Session 07: an edited layer's pixels are re-encoded at every save until the scene is reloaded).
- [x] Measure load plus decode as a share of cold-product time from the 07b report for QPF and grid. If it exceeds 10 % for either workload, implement lazy decoding (manifest first, decode on first use, replace without decoding the previous pixels, error clearly when a closed scene needs undecoded data, lazy/eager equivalence tests). Otherwise record a no-change decision with the numbers and leave lazy decoding in the backlog.
- [x] Regenerate the 07b report.

**Verification:** Lifecycle tests, encode-call instrumentation for repeated saves, cold-process load timings versus Session 02 and the measured lazy-decoding decision (plus decoder-call instrumentation and exact lazy/eager comparisons if lazy decoding is implemented).

### Session 13

**Goal:** Verify portability and dependency boundaries independently of the build environment.

**Deliverables:** Source-independence tests and a documented manual fresh-environment check procedure (`CONTRIBUTING.md` → "Environment checks"), with recorded results.

- [x] Author and save a file, remove the source fixtures and Natural Earth cache, then load, update, and render it in a fresh process.
- [x] In an environment with only core dependencies, verify load, grid, polygon, and text replacement (no pyproj or shapely installed), layer addition, compositing, save, and PNG output, and assert Matplotlib/Cartopy are absent from `sys.modules`.
- [x] Disable network access during runtime tests and verify no downloads or source lookups are attempted.
- [x] ~~If Session 12 implemented lazy decoding, confirm lazy and eager loading meet the same guarantees.~~ Not applicable: Session 12 measured load at ≤ 5.6 % of a cold product and kept eager loading. Check instead that loaded scenes keep no file open (`tests/test_lifecycle.py`).

**Verification:** Manual fresh-environment, fresh-process checks (the procedure in `CONTRIBUTING.md`); image comparisons; explicitly removed sources and blocked network.

### Session 14

**Goal:** Make the build and runtime workflows usable from documented examples.

**Deliverables:** Authoring and runtime examples, API and format documentation, and updated repository instructions.

- [x] Add executable examples: author the reference scenes, then cron-style runtime scripts that update grid and text, and QPF polygons and subtitle for all products in one process, and write PNGs.
- [x] Document installation (core vs `build` extra), embedded fonts, archive lifecycle, the format reference, and limitations.
- [x] Separate the README quickstart and supported features from the longer design roadmap without losing the architectural reference.
- [x] Document focused test/benchmark commands and reconcile `CLAUDE.md` with the implemented repository.

**Verification:** Run examples from an installed package with prepared fixtures, including runtime use in a core-only environment.

### Session 15

**Goal:** Validate distribution artifacts and record whether v0.1 is ready for release.

**Deliverables:** Wheel/sdist validation, supported-environment checks, benchmark summary, and a release-readiness record.

- [x] Build wheel and sdist; inspect metadata, extras, and included assets.
- [x] Install the artifacts into fresh environments and verify imports/examples outside the repository source directory.
- [x] Run core-only and `build`-extra tests across the declared Python range, with small cross-platform smoke tests where available.
- [x] Re-run the package QPF and grid benchmarks (Sessions 08b/08) from the installed wheel, record them alongside the baseline and the Session 02 prototype, and regenerate the 07b report as the release benchmark summary.
- [x] Check distribution-name availability and document remaining release actions; publishing requires a separate user request.
- [x] Confirm Sessions 01–14 are completed or resolve their blockers before declaring v0.1 readiness.

**Verification:** Full configured checks, fresh artifact installation, runnable example smoke tests, and benchmark evidence. Record unsupported/unverified environments explicitly.

### Session 16

**Goal:** Choose the next extension from measured needs rather than implementing the speculative roadmap.

**Deliverables:** Small/medium/large workload report and explicitly scoped follow-up sessions.

- [ ] Profile cold start, manifest read, layer decode, grid render, polygon render, text render, compositing, PNG encoding, and peak memory separately for each workload size.
- [ ] Compare with the equivalent full Cartopy baseline, cold and warm, as 07b report figures. Priority candidates from earlier measurements: PNG encoding (~40 % of a QPF product in Session 02) and polygon fill on complex products (up to 321 ms).
- [ ] Identify the dominant measured cost and the expected benefit of a specific change.
- [ ] Add the next justified extension as new numbered sessions with deliverables, prerequisites, and verification gates, or record a no-change decision.

**Verification:** Reproducible profiling commands, workload details, output-correctness comparisons, and a written decision linked to the measurements.

## Conditional extension backlog

Candidates for sessions added after Session 16, not requirements for v0.1:

| Need demonstrated by profiling or user workflow | Candidate extension |
| --- | --- |
| Runtime overlays outside declared slots (stations, warning outlines), or projections without a NumPy formula | Reuse the polygon-slot projection for points/lines; `geo` extra (pyproj) for arbitrary CRSs |
| New grid types (curvilinear, radar polar, satellite swath) or interpolating display | Additional grid-slot mappings (bilinear weights, pyresample neighbour info) with method-matched references |
| Value-dependent artists (contour lines, barbs) | Build-machine-style rendering via the `build` extra into a replaceable layer, or a native contouring path |
| PNG encoding dominates recurring runtime | Lower `compress_level`, RGB output, or a faster encoder (zlib-ng-based Pillow builds, fpng-style encoders) |
| Process start-up/imports dominate cron runs | Long-lived worker that keeps scenes loaded and renders products on request |
| Static border restyling without sources | Compiled vector layers (projected paths) as a new layer kind |
| Compositing or grid rendering dominates after NumPy/Pillow tuning | Rust/PyO3 kernels behind the same API |
| Small edits on large canvases | Dirty rectangles or a composite tree |
| Load and decode become a significant share of a product (Session 12 gate: > 10 %; measured 4.1–5.6 % cold, < 1 % in loops), or files hold many layers a render does not use | Lazy layer decoding (manifest first, decode on first use) |
| Scene size exceeds practical memory | Tiled layers or an indexed/raw container; do not assume ZIP members can be memory-mapped |
| CPU paths insufficient for very large workloads | Optional GPU backend with equivalent CPU behavior |

## Development commands

This section is the version-controlled source of truth for commands; mark each as **configured** once a session verifies it.

Configured (Session 01, verified 2026-10-08):

```bash
uv run scripts/fetch_fixtures.py       # one-time, network; idempotent; writes benchmarks/data/ + fixtures.json
uv run benchmarks/baseline_cartopy.py     # offline Cartopy baseline (7 cold runs, tracemalloc run, 1+10 warm); ~3.5 min
uv run benchmarks/baseline_cartopy.py --cold-runs 3 --warm-reps 3              # shorter run
uv run benchmarks/baseline_cartopy.py --child render --variant N --out x.png   # one full render of input variant N
uv lock --script benchmarks/baseline_cartopy.py   # refresh benchmarks/baseline_cartopy.py.lock after editing pins
```

Configured (Session 01b, verified 2026-10-08):

```bash
uv run scripts/fetch_fixtures.py       # also copies SWRCC GeoPackages/logo (SWRCC_RESOURCES overrides the source) and snapshots 18 WPC QPF products once
uv run benchmarks/baseline_qpf.py         # 5 cold single-product runs + 2 cold 18-product loops; ~4 min
uv run benchmarks/baseline_qpf.py --day 1-7 --cold-runs 3 --loop-runs 1
uv run benchmarks/baseline_qpf.py --child product --day 1-3 --out x.png   # one product, as the example renders it
uv lock --script benchmarks/baseline_qpf.py
```

Configured (Session 02, verified 2026-10-08):

```bash
uv run benchmarks/prototype_build.py      # author QPF and grid prototype files (png/raw/zlib, plus QPF split variants) → results/prototype-<stamp>/; ~45 s
uv run benchmarks/prototype_runtime.py    # NumPy/Pillow-only runtime benchmark on the latest build → <build>/runtime-<stamp>/; ~1.5 min
uv run benchmarks/prototype_compare.py    # compare the latest runtime outputs with full Cartopy renders → <runtime>/compare/
uv lock --script benchmarks/prototype_build.py   # likewise for prototype_runtime.py and prototype_compare.py
```

Configured (Session 03, verified 2026-10-08; see also `CONTRIBUTING.md`):

```bash
uv sync --group dev                          # core-only dev env (NumPy + Pillow); exact, removes extras
uv sync --extra build --group dev            # plus Matplotlib + Cartopy (build extra)
uv run ruff format --check .                 # package, tests, scripts (benchmarks and *.md excluded)
uv run ruff check .                          # whole repo; benchmarks/ has relaxed per-file rules
uv run mypy                                  # strict, src/cartostack
uv run pytest                                # build-extra and fixture tests skip when unavailable
uv run pytest tests/test_package.py::test_import_does_not_load_rendering_libraries   # single test
uv run scripts/fetch_fixtures.py [--check]   # prepare fixtures (network) / verify offline
uv build                                     # sdist + wheel
uv lock --check                              # uv.lock up to date
```

Focused (Session 04):

```bash
uv run pytest tests/test_geometry.py tests/test_manifest.py   # format 1.0 validation, examples, schema agreement
```

Focused (Session 05):

```bash
uv run pytest tests/test_io.py tests/test_layers.py   # round trips, byte stability, atomic saves, corrupt files, layer ownership
```

Configured (Session 06, verified 2026-10-08):

```bash
uv run pytest tests/test_compositor.py                # compositing vs independent references, placement, flattening, PNG
uv run python benchmarks/compositor_timing.py         # needs a Session 02 prototype build; → results/compositor-<stamp>/; ~10 s
uv run python benchmarks/compositor_timing.py --reps 5
```

Focused (Session 07):

```bash
uv run pytest tests/test_editing.py                   # edits, atomic failures, decode/encode spies, template immutability
```

Configured (Session 07b, verified 2026-10-09):

```bash
uv run benchmarks/report.py                       # figures + index.md + report.json from the latest results of each kind → results/report-<stamp>/; ~10 s
uv run benchmarks/report.py --qpf-baseline benchmarks/results/qpf-<stamp>   # pin an input (also --grid-baseline, --prototype-runtime, --compositor)
uv run benchmarks/report.py --bench path/to/bench.json --out <dir>         # extra native results; every results/**/bench.json is read anyway
uv lock --script benchmarks/report.py
```

Configured (Session 08b, verified 2026-10-09):

```bash
uv run pytest tests/test_polygons.py tests/test_projection.py   # exact fill vs brute force, hand-computed pixels, LCC (pyproj tests need --extra build)
uv run python benchmarks/package_qpf.py           # package QPF: 5 cold Day 1-3 runs + 18-product loops at 4x (2 runs), 1x and 2x (1 each); accuracy vs Session 01b; writes bench.json; ~1 min
uv run python benchmarks/package_qpf.py --supersample 2 --compare 4 --cold-runs 3 --loop-runs 1
uv run benchmarks/report.py                       # then regenerate the figures
```

Configured (Session 08, verified 2026-10-09):

```bash
uv run pytest tests/test_grids.py                 # rows at every boundary, masks, alpha, index map, Scene; Matplotlib-exact tests need --extra build
uv run python benchmarks/package_grid.py          # package grid: 5 cold variant-0 runs + 2 loops of variants 0-3, accuracy vs pcolormesh, 500x500 and HRRR render timing; writes bench.json; ~15 s
```

Configured (Session 09, verified 2026-10-09):

```bash
uv run pytest tests/test_text.py                  # fonts, layout rules, rendering, scene.text, core-runtime milestone; Matplotlib comparisons need --extra build
uv run pytest tests/test_text.py::test_core_runtime_milestone   # the milestone: run it in the core-only env (uv sync --group dev)
```

Configured (Session 10, verified 2026-10-09):

```bash
uv sync --extra build --group dev && uv run pytest tests/test_build.py && uv sync --group dev   # authoring tests (Matplotlib >= 3.11, Cartopy, pyproj)
uv run benchmarks/author_scenes.py               # author qpf.cstack and grid.cstack from the baseline figures → results/authored-<stamp>/; ~40 s
uv run python benchmarks/package_qpf.py --scene benchmarks/results/authored-<stamp>/qpf.cstack    # benchmark an authored file
uv run python benchmarks/package_grid.py --scene benchmarks/results/authored-<stamp>/grid.cstack
uv lock --script benchmarks/author_scenes.py
```

Configured (Session 11, verified 2026-10-09):

```bash
uv run pytest tests/test_colorbars.py             # tick/label ports, redraw parts, Scene behaviour; Matplotlib comparisons need --extra build
uv sync --extra build --group dev && uv run pytest tests/test_build.py -k milestone && uv sync --group dev   # build-to-runtime milestone
```

Focused (Session 12):

```bash
uv run pytest tests/test_lifecycle.py tests/test_compositor.py   # no open files, close(), encode-once saves, cache cleanup, bottom-layer fast path
```

Focused (Session 13):

```bash
uv run pytest tests/test_portability.py   # fresh process: no files outside the scene/output/Python, no network; negative control
uv sync --extra build --group dev && uv run scripts/check_portability.py && uv sync --group dev   # full procedure (CONTRIBUTING.md → "Environment checks")
```

Examples (Session 14; `examples/README.md`):

```bash
uv run --extra build python examples/author_grid.py out/grid.cstack --inputs out/grid-inputs   # author (build extra, Natural Earth fixtures)
uv run examples/author_qpf.py out/qpf.cstack                                                   # author (PEP 723: adds GeoPandas)
uv run python examples/update_grid.py out/grid.cstack out/grid-inputs/*.npy --out out/png       # runtime only
uv run python examples/update_qpf.py out/qpf.cstack benchmarks/data/qpf/day_* --out out/png     # runtime only, all 18 products
uv run pytest tests/test_examples.py                                                           # runtime examples in a fresh process, no build imports
uv run --extra build mypy --strict examples --ignore-missing-imports                           # examples type-check (not in the configured mypy files)
```

The fixture script moved from `benchmarks/fetch_fixtures.py` to `scripts/fetch_fixtures.py` in Session 03; earlier log entries keep the old path.

Each session must also record its focused verification command once the relevant tests or scripts exist. Session 15 must validate built artifacts independently of the editable development installation.

## Completion log

Append one entry at every session handoff; keep previous entries. Use actual outcomes, not intended work. Multiple entries for a partially completed session are expected.

```text
YYYY-MM-DD — Session NN — completed / partial / blocked
Work: implemented deliverables and affected paths.
Verification: exact commands or manual checks, environment, and results.
Evidence: artifact paths, measured results, or explicit "not applicable".
Decisions/deviations: design choices and any changed scope or defaults.
Remaining/blockers: unfinished checks and recovery action, or "none".
Next: session ID and the first concrete action.
```

### 2026-10-06 — Session 00 — completed

- **Work:** Created `SESSIONS.md` with Sessions 00–20, prerequisite/status index, detailed checklists, milestone gates, current handoff, and append-only completion records. Linked it from `README.md` and added the mandatory session-update workflow to `AGENTS.md`.
- **Verification:** Manually reviewed all 21 session headings against their index links, checked prerequisite ordering, and confirmed only tracker setup is completed. Reviewed the README entry point and AGENTS instructions. The following whitespace checks passed:
  - `git diff --no-index --check -- /dev/null SESSIONS.md`
  - `git diff --no-index --check -- /dev/null README.md`
  - `git diff --no-index --check -- /dev/null AGENTS.md`
- **Evidence:** Documentation/check output only; Python tests and benchmark artifacts are not applicable to tracker setup.
- **Decisions/deviations:** Split the larger implementation plan into 20 implementation sessions plus Session 00 for tracking setup. v0.1 readiness is Session 17; later grid generalization and profiling are Sessions 18–20. Proposed package/tooling commands are explicitly distinguished from configured commands.
- **Remaining/blockers:** None for Session 00. Sessions 01–20 remain planned.
- **Next:** Session 01 — record the benchmark workload and experiment dependencies, then implement and measure the conventional Cartopy baseline.

### 2026-10-07 — Session 00 — plan revision

- **Work:** Revised `SESSIONS.md` for package implementation readiness: added the target v0.1 package surface, observed development environment, and a concrete Session 01 workload specification (canvas, projection, extent, layers, grid, fixtures, timing, output paths). Added fixture-preparation, `uv` prerequisite, and resolved-axes items to Session 01; fixed-geometry rule to Session 02; core-only CI and shared fixture preparation to Session 03. Made this file the version-controlled home for commands, since `AGENTS.md` and `CLAUDE.md` are gitignored. Later the same day, merged `AGENTS.md` into `CLAUDE.md` and deleted `AGENTS.md`; `CLAUDE.md` is now the single agent-instruction file.
- **Verification:** Inspected the local environment (`which uv` → not found; Anaconda Python 3.11.8 package versions; Natural Earth cache listing; `git remote -v`). Reviewed edited sections and anchors manually. Whitespace check: `git diff --no-index --check -- /dev/null SESSIONS.md`.
- **Evidence:** Not applicable (documentation only).
- **Decisions/deviations:** Session IDs and ordering unchanged. Workload values are proposals to be confirmed and recorded by Session 01. Counties come from US Census cartographic boundaries because Natural Earth has no US counties.
- **Remaining/blockers:** Install `uv`; one-time network download of county and populated-places fixtures.
- **Next:** Session 01 — install/verify `uv`, write `benchmarks/fetch_fixtures.py`, then the baseline benchmark.

### 2026-10-08 — Session 00 — plan revision (feasibility review)

- **Work:** Reviewed `README.md` and `SESSIONS.md` for feasibility. Added cold-process/import timing, Cartopy in-process cache caveat, matched PNG encoder settings, and per-group draw breakdown to Session 01. Added Matplotlib blitting, plain-`Axes` projected-coordinate, NumPy pixel-space prototype, flattened-static-run measurements, and a sequencing decision for Sessions 18–19 to Session 02. Added name confirmation (Session 03), flattened static-run caching (Session 07), render-target reuse measurement (Session 08), text-backend decision (Session 10), and the index-image lookup method (Session 19). Raised the candidate Python minimum to 3.12, fixed the alpha convention to straight `uint8`, and extended the backlog with PNG-encoding and long-lived-worker entries. Added matching notes to `README.md` (prior art, Amdahl limits, startup cost, index-image lookup, thread safety, alpha convention).
- **Verification:** PyPI JSON API queried for `geoscene`/`geo-scene` (404, unregistered) and for current NumPy/Matplotlib/Cartopy/Pillow `requires_python`. `git diff --no-index --check -- /dev/null SESSIONS.md` and the same for `README.md`.
- **Evidence:** Not applicable (documentation only). Performance expectations in the README are labelled estimates pending Sessions 01–02.
- **Decisions/deviations:** Session IDs and ordering unchanged; Session 02 now records whether to pull 18–19 ahead of persistence.
- **Remaining/blockers:** Install `uv`; one-time fixture download; decide the package name.
- **Next:** Session 01 — install/verify `uv`, write `benchmarks/fetch_fixtures.py`, then the baseline benchmark with warm and cold timing.

### 2026-10-08 — Session 00 — plan restructure (file-format objective)

- **Work:** Restructured Sessions 01–20 into Sessions 01–16 around the user's clarified objective: a layered `.gsmap` file (RGBA layers plus metadata) edited by a NumPy/Pillow-only runtime, with Matplotlib/Cartopy confined to build-time authoring. Caching is no longer the organizing theme. Format specification moved from old Session 13 to Session 04; grid slots (old 18–19) moved into v0.1 as Session 08; runtime text uses Pillow (Session 09); Cartopy work became build-side authoring (Session 10). Dropped as separate sessions: render invalidation/revision tracking (old 07), Cartopy runtime adapter (old 09), and generalized projection caches (old 18). Session 02 now benchmarks a prototype file plus a core-only runtime. Rewrote README framing (goals, architecture, format, grid slots, dependency levels, phases, first experiment, milestone). Updated `CLAUDE.md` design constraints to match.
- **Verification:** `git diff --no-index --check -- /dev/null SESSIONS.md` and the same for `README.md`; manual check that index links resolve to session headings 00–16.
- **Evidence:** Not applicable (documentation only).
- **Decisions/deviations:** Session 00 ID and records preserved; all other sessions were `planned`, so renumbering loses no history. Session numbers cited in the earlier 2026-10-08 feasibility entry refer to the previous numbering. Runtime geographic overlays (pyproj) deferred to the backlog; the v1 format stores the georeferencing they need.
- **Remaining/blockers:** One-time fixture download; decide the package name before Session 03.
- **Next:** Session 01 — write `benchmarks/fetch_fixtures.py`, then `benchmarks/baseline_cartopy.py` with cold and warm timing.

### 2026-10-08 — Session 00 — package name decision

- **Work:** Renamed the project from the working name GeoScene to **CartoStack**: distribution/import name `cartostack`, file extension `.cstack`, build extra `cartostack[build]`. Updated `README.md`, the planning sections of this file, and `CLAUDE.md`. Earlier completion-log entries keep the names they were written with.
- **Verification:** PyPI JSON API returned 404 (unregistered) for `cartostack` on 2026-10-08; `gsmap` is registered on PyPI, which also motivated changing the extension. Grep confirmed no remaining `geoscene`/`gsmap` references outside historical log entries and the GitHub repository name. Whitespace checks as before.
- **Evidence:** Not applicable (documentation only).
- **Decisions/deviations:** The GitHub repository and local directory remain `GeoScene`; renaming them is a separate user decision. Session 03 re-verifies name availability before scaffolding.
- **Remaining/blockers:** One-time fixture download.
- **Next:** Session 01 — write `benchmarks/fetch_fixtures.py`, then `benchmarks/baseline_cartopy.py` with cold and warm timing.

### 2026-10-08 — Session 00 — county fixture and workload recentre

- **Work:** Recorded the user-supplied New York county shapefile (`resources/NYS_Shoreline_Counties/`) with provenance, attributes, and SHA-256 under Environment. Because it covers only New York, recentred the Session 01 workload: Lambert Conformal centred at −76°/42.5° with standard parallels 40.5°/44.5°, extent `[-80.5, -71.5, 40.2, 45.3]`, synthetic grid over `[-81, -71] × [40, 46]`. The fixture script now copies the county files instead of downloading US Census counties.
- **Verification:** Read the shapefile with pyshp (Anaconda Python 3.11.8): 62 polygon records, bbox and vertex count as recorded; `.prj` is WGS 84 geographic; hashes computed with `hashlib.sha256`.
- **Evidence:** Not applicable (documentation only).
- **Decisions/deviations:** Workload values remain proposals that Session 01 confirms. Whether to commit `resources/` (≈4.9 MB) is undecided pending licence/redistribution terms.
- **Remaining/blockers:** One-time `ne_10m_populated_places` download; decide whether `resources/` is tracked.
- **Next:** Session 01 — write `benchmarks/fetch_fixtures.py`, then `benchmarks/baseline_cartopy.py` with cold and warm timing.

### 2026-10-08 — Session 01 — completed

- **Work:** Added `benchmarks/fetch_fixtures.py` (stdlib-only PEP 723 script: copies six Natural Earth 10m layers from the Cartopy cache and the county shapefile from `resources/` after verifying the recorded county SHA-256, downloads `ne_10m_populated_places`, writes `benchmarks/data/fixtures.json`) and `benchmarks/baseline_cartopy.py` (PEP 723, pinned `cartopy==0.26.0`, `matplotlib==3.11.2`, `numpy==2.5.3`, `pillow==12.3.0`, Python 3.13, `exclude-newer = 2026-10-08`; lockfile `benchmarks/baseline_cartopy.py.lock`). Added `benchmarks/data/` and `benchmarks/results/` to `.gitignore`. The baseline script is a stdlib-only orchestrator spawning fresh child processes; scene constants, `make_field(variant)`, `title_text(variant)`, and `render()` are importable for Session 02.
- **Workload (final):** as in the [workload specification](#session-01-workload-specification), with these concrete choices: requested axes rectangle `(0.02, 0.13, 0.96, 0.78)`, colorbar `(0.2, 0.06, 0.6, 0.025)` (figure fractions); `coolwarm`, `vmin=-10`, `vmax=35`, `alpha=0.8`, `shading="nearest"`; zorders ocean 1.0, land 1.1, lakes 1.2, data 2.0, counties 3.0, states 3.1, country borders 3.2, coastline 3.3, city markers 4.0, labels 4.1; 34 cities (`SCALERANK <= 7`); figure facecolor white (output alpha is 255 everywhere). Reference PNG: Pillow, mode RGB, `compress_level=6`.
- **Resolved geometry (Session 02 must reproduce):** canvas 1200 × 800 px at 100 DPI. Map axes resolved to fraction `(0.147838, 0.13, 0.704324, 0.78)` = pixels left 177.4057, top 72.0, width 845.1885, height 624.0 (top-left origin; x0 177.4057–x1 1022.5943, bottom-origin y 104–728). Colorbar axes pixels left 272.7273, top 732, width 654.5455, height 20. Projection `+ellps=WGS84 +proj=lcc +lon_0=-76.0 +lat_0=42.5 +x_0=0.0 +y_0=0.0 +lat_1=40.5 +lat_2=44.5 +no_defs`; projected extent x `[-383043.176, 383043.176]`, y `[-245186.461, 320412.666]` m.
- **Environment:** `uv 0.12.23`; Python 3.13 (uv-managed env); cartopy 0.26.0, matplotlib 3.11.2, numpy 2.5.3, Pillow 12.3.0, pyproj 3.8.0, shapely 2.2.0, pyshp 3.1.6 (other versions incl. PROJ, GEOS, FreeType, zlib and the DejaVu Sans path/SHA-256 are in `results.json` → `versions`). Apple M3 Pro.
- **Fixtures:** 41 files, 126.6 MB in `benchmarks/data/`. `ne_10m_populated_places.zip` from `https://naciscdn.org/naturalearth/10m/cultural/ne_10m_populated_places.zip`, 2,811,199 B, SHA-256 `cd149186f03d2603e0410da399b980a4357d0ac32d3a2305a49ed3dffcc41d7b`. Per-file sizes/digests in `benchmarks/data/fixtures.json`. A second run of `fetch_fixtures.py` made no download and reproduced the manifest. Offline reuse: the benchmark verifies every fixture digest before running, points `cartopy.config["data_dir"]` at `benchmarks/data/`, and replaces `socket.connect`/`create_connection` in render processes so any download attempt fails.
- **Verification:** `uv run benchmarks/baseline_cartopy.py` (7 cold + 1 tracemalloc + 1 warm-up + 10 warm = 19 renders) → all 19 identical: pixel SHA-256 `47d6948772a11049220fde515fb9511d6310d539205e9c9a77a4b65a3ae6a06e`, PNG SHA-256 `43222cab199ba3500da5f16ad30497a27d3c97feb5f3ed2d4e80ada9179cb36f`, shape 800 × 1200 × 4, one resolved geometry. A second invocation `uv run benchmarks/baseline_cartopy.py --cold-runs 3 --warm-reps 3` (8 renders) and an earlier standalone `--child render` gave the same hashes and geometry. `--child render --variant 1` gave different pixels (`12eab40d…`) with identical geometry. Image inspected visually: land/water, lakes, counties, states, borders, coastline, city markers/labels, data at α 0.8, title, subtitle, colorbar all present and in order. `uvx ruff check --line-length 100 benchmarks/` passes (ad hoc; Ruff is configured in Session 03); after the lint fixes a `--child render` still produced pixel SHA-256 `47d69487…`. Caches: OS file cache and bytecode warm (an unrecorded priming run precedes measurements; no `purge`); first-ever run in a fresh env was slower (imports ~5.6 s while compiling bytecode).
- **Evidence:** `benchmarks/results/20261008T145213Z/` (`baseline.png`, `results.json` with every run) and `benchmarks/results/20261008T145633Z/` (repeat). Medians, 7 cold runs:
  - Process wall **16.20 s** (range 15.95–16.37). Bare interpreter 16.8 ms; script interpreter start 21.9 ms; imports numpy 28.1, PIL 7.1, matplotlib+pyplot 142.4, cartopy 48.9 ms (sequential, ≈ 0.23 s total).
  - Explicit source load (counties 306k vertices + populated places) 840.6 ms.
  - Artist construction: setup 13.9, field 3.6, `pcolormesh` build (mesh projection) 52.5, other builds < 4 ms each.
  - Draw 14.83 s: below data (ocean/land/lakes) **11.01 s**, of which Natural Earth shapefile parsing 0.93 s and the rest Cartopy clipping/projecting the 10m polygons; data 77.5 ms; above data **3.70 s** (Natural Earth parsing 0.93 s; counties, states, borders, coastline, cities); text + colorbar 19.2 ms; other axes overhead 1.0 ms.
  - PNG encode 1200 × 800: RGB cl6 22.2 ms (322,236 B), RGBA cl6 23.8 ms (348,251 B), RGB cl1 10.6 ms (464,367 B), RGBA cl1 13.3 ms (533,350 B).
  - Peak RSS 270 MiB; tracemalloc peak 117 MB (separate run).
  - Warm (10 reps after warm-up, sources loaded once): total median **319.5 ms** (min 312.3); draw 184 ms (below 19.3, data 77.9, above 73.6, text/colorbar 12.0), `pcolormesh` build 53.4, encode RGB cl6 19.9 ms. Warm-up render 15.1 s. Warm runs benefit from `cartopy.feature._NATURAL_EARTH_GEOM_CACHE` (parsed geometries) and `FeatureArtist`'s projected-path cache (keyed by geometry id and projection), plus Matplotlib text caches — so warm understates cron cost by ~50×.
- **Decisions/deviations:** Python pinned to 3.13 for the benchmark env. Counties and city points are read explicitly once per process (`load_sources`) rather than via Cartopy features; Natural Earth layers use `NaturalEarthFeature` (the conventional path), whose lazy reads are timed inside drawing. Per-group draw time is measured by wrapping each artist instance's `draw`; Natural Earth parse time is attributed to the group being drawn. Memory is measured in a separate `tracemalloc` run so timed runs are not slowed. Observation for Session 02: the `pcolormesh` with `alpha=0.8` shows faint cell-seam artifacts (Agg, non-antialiased QuadMesh); expect the index-map render to differ there.
- **Remaining/blockers:** None for Session 01. Open (user decision): whether `resources/` and the new `benchmarks/*.py`/lockfile are committed; nothing has been committed.
- **Next:** Session 02 — write `benchmarks/prototype_build.py` that imports the scene from `baseline_cartopy.py` and renders the below/above/title layers on figures reproducing the resolved axes rectangle above.

### 2026-10-08 — Plan revision — QPF workload (between Sessions 01 and 01b)

- **Work:** Reviewed the user-supplied `resources/slow_example.py` (WPC QPF masked to NYS; ~18 products per process) and one of its existing outputs (`qpf_plot_day_1-3.png`, 2210 × 1848 RGBA). Per product only the QPF polygons and the subtitle change. Added the [QPF workload specification](#qpf-workload-specification), Session 01b (QPF baseline; Session 02 now depends on it), and Session 08b (polygon slots; Session 09 now depends on it). Extended Sessions 02, 04, 09, 10, 13, 14, the target package example, design constraints, and backlog for polygon slots, multiple data slots, fixed output crops, and per-product loop timing.
- **Verification:** Documentation change only; checked that `prepared_gpkg/` (7 GeoPackages) and the logos exist under `/Users/danielvanhoesen/swrcc/resources/`.
- **Evidence:** Not applicable.
- **Decisions/deviations (user, 2026-10-08):** QPF is the primary reference workload; the synthetic grid scene is kept as secondary and grid slots stay in v0.1. Runtime polygon projection uses a NumPy LCC implementation, not pyproj. Data acquisition (WPC download/extract) is excluded from timing. Existing session IDs kept; new sessions use suffixed IDs.
- **Remaining/blockers:** One-time network snapshot of WPC QPF products (Session 01b); whether `resources/` is committed.
- **Next:** Session 01b — extend `fetch_fixtures.py` with the GeoPackages, logo, and a QPF snapshot.

### 2026-10-08 — Session 01b — completed

- **Work:** Extended `benchmarks/fetch_fixtures.py` to copy the six SWRCC GeoPackages and `SWRCC_Logo_Transparent.png` from `/Users/danielvanhoesen/swrcc/resources/` (override: `SWRCC_RESOURCES`) and to snapshot all 18 WPC QPF products the example renders into `benchmarks/data/qpf/day_<key>/` (shapefile members only; kept once taken). Added `benchmarks/baseline_qpf.py` (PEP 723: cartopy 0.26.0, geopandas 1.2.0, matplotlib 3.11.2, numpy 2.5.3, pillow 12.3.0, pyogrio 0.13.0, pytz 2026.5, Python 3.13, `exclude-newer = 2026-10-08`; lockfile `benchmarks/baseline_qpf.py.lock`). It reuses `DrawTimer`, `block_network`, `peak_rss_bytes`, and `summarize` from `baseline_cartopy.py`; `DrawTimer` now takes its group names as an argument.
- **Port of `resources/slow_example.py`:** drawing code unchanged (same GeoPandas reads, clip, per-polygon `ax.add_geometries`, static layers, cities with path effects, title bar, title, subtitle, legend, logo, attribution, `plt.savefig(dpi=300, bbox_inches="tight", pad_inches=0)`). Deviations: fixture paths; the download, tar extraction, and cleanup are removed (out of scope); the per-product `try/except` is removed so failures surface; `savefig` writes to memory and then to a file so the write is timed separately. Like the example, figures are never closed, so memory grows over the loop.
- **Fixtures:** 120 files, 223 MB in total. QPF snapshot: 18 tarballs, 74.7 MB, WPC `Last-Modified` 2026-10-08 10:07–10:44 UTC, from `https://ftp-wpc.ncep.noaa.gov/shapefiles/qpf/…`, plus per-tar SHA-256 and members in `fixtures.json` → `qpf`. Example: Day 1-3 `day13_2026100812.*`, tar SHA-256 `c43cf9c62e0ee3f85c465f6cac135ce45720a7b76fbd9526d3aed576ad1e9a1b`. Polygons per product after clipping: 56–481 (Day 1-3: 312 polygons in 14 rows). A second `fetch_fixtures.py` run downloaded nothing.
- **Resolved geometry (Session 02 must reproduce):** figure 10 × 8 in at 100 DPI, saved at 300 DPI. Tight box (inches, x0 y0 w h) `(1.440166, 0.88, 7.369667, 6.16)` = the map axes after Cartopy's aspect adjustment (figure fraction `(0.144017, 0.11, 0.736967, 0.77)`), so the output is **2210 × 1848** RGBA. The axes span the whole output: left 0, top 0, width 2210.90 px, height 1848 (the fractional width is truncated). Legend box at left 1844.83, top 445.87, size 294.33 × 873.42 px; attribution box at left 44.22, top 1777.71, size 992.02 × 33.33 px. Projection `+ellps=WGS84 +proj=lcc +lon_0=-75.85 +lat_0=42.95 +x_0=0.0 +y_0=0.0 +lat_1=33 +lat_2=45 +no_defs`. Projected extent x `[-350859.050, 350859.050]`, y `[-285022.089, 301515.038]` m. Fonts: DejaVu Sans Bold (title, legend title, city labels), Oblique (subtitle), and Regular, with paths and SHA-256 in `results.json` → `versions.fonts`.
- **Verification:** `uv run benchmarks/baseline_qpf.py` ran 5 cold single-product runs (Day 1-3) and 2 cold loops of 18 products each. Every product's pixels were identical across all runs and across the single and loop modes (`identical_per_product: true`), with one output size (2210 × 1848) and one tight crop. After the lint fixes, `--child product --day 1-3` again gave pixel SHA-256 `f6f6d40c…`. `uvx ruff check --line-length 120 benchmarks/` passes; three findings in code ported from the example are suppressed inline. All 18 product PNGs were written, and Day 1-3 was inspected visually. Caches: OS file cache and bytecode warm (an unrecorded priming run comes first); network blocked in the render processes. Two earlier attempts were discarded and are not reported: one crashed while summarising, and one double-counted draw time in `product_total`. Both bugs were fixed before the recorded run.
- **Comparison with production** (`/Users/danielvanhoesen/swrcc/internal/qpf/images/qpf_plot_day_1-3.png`, produced 2026-03-04 from a different forecast): the size matches (2210 × 1848). The static text does not match. Under Matplotlib 3.11.2 the attribution text is narrower, and the legend title collapses so that "Inches" overlaps the first entry. In a minimal repro with the example's legend settings, the title box is 18.1 px tall in Matplotlib 3.10.6 (Anaconda) and 2.8 px in 3.11.2. Differing pixels: 29% of the legend box and 45% of the attribution box. This is a change between Matplotlib versions, not a porting error. Upgrading production Matplotlib would change those images the same way.
- **Evidence:** `benchmarks/results/qpf-20261008T152044Z/` holds `results.json` (every run), `single_1-3_0.png`, and `products/` (18 PNGs). Medians:
  - **Single cold product (Day 1-3), process wall 12.23 s.** Interpreter start 19.5 ms. Imports: numpy 28.0, PIL 7.1, matplotlib 144.1, geopandas/pyogrio/shapely 205.3, cartopy 9.1 ms (≈ 0.39 s in total). Static GeoPackages and logo 28.9 ms. Product: read 75.8 ms, clip 21.8 ms, build 58 ms, then `savefig` **11.28 s**, which breaks down into:
    - static above (counties, states, lakes, NY mask, cities, logo): **10.74 s**, mostly Cartopy projecting and clipping the static GeoPackage geometries on first use;
    - data polygons: 347 ms;
    - coastline: 49 ms;
    - decorations: 63 ms;
    - subtitle: 9 ms;
    - tight box, layout and PNG encoding: 78 ms.

    Peak RSS 718 MiB.
  - **Loop of 18 products in one process, process wall 32.1 s** (imports 0.40 s; static load 0.03 s). The first product took 11.67 s. The other 17 took a median of **0.92 s** each (range 0.67–1.36 s), split as:
    - static above redrawn every product: **464 ms**;
    - data polygons: 229 ms (36–648 ms, scaling with polygon count);
    - decorations: 54 ms;
    - subtitle: 6 ms;
    - coastline: 4.5 ms;
    - `savefig` remainder (tight box and encoding): 72 ms;
    - read: 25 ms; clip: 10 ms.

    Peak RSS 1.71 GiB because figures are never closed.
  - **Matched Pillow encodes at 2210 × 1848:** RGBA level 6 65.7 ms (578,952 B), RGB level 6 53.1 ms, RGBA level 1 49.3 ms, RGB level 1 38.6 ms.
- **Decisions/deviations:** GeoPandas was kept for a faithful port; its import (≈ 0.2 s) and reads are measured. The single-product runs model a cron job that renders one map. The loop is how the example actually runs, and is the main comparison for Session 02. Findings for Session 02:
  - In the loop, about half of each product's time is redrawing static content above the data. That content is identical every time and becomes one pre-flattened layer.
  - The data polygons cost 36–648 ms per product in Agg. The NumPy/Pillow polygon fill has to beat that.
  - Encoding a 4.1-megapixel image costs 40–70 ms whichever way it is done, so it will become a large share of the runtime.
- **Remaining/blockers:** None for Session 01b. Open (user decisions): whether `resources/` is committed (it now also contains `slow_example.py`); and whether production should pin Matplotlib < 3.11 or adjust the legend title (outside this repository).
- **Next:** Session 02. Write `benchmarks/prototype_build.py`: from the QPF scene, render the static-below and flattened static-above layers at the 2210 × 1848 crop, and export the bin table and LCC parameters.

### 2026-10-08 — Session 02 — completed

- **Work:**
  - Refactored `baseline_qpf.render_product` into `build_figure` + save, and `baseline_cartopy.render` into `build_scene` + draw. Baseline pixel hashes are unchanged (`f6f6d40c…`, `47d69487…`).
  - Added `benchmarks/prototype_build.py` (build env), `benchmarks/prototype_runtime.py` (PEP 723 with only `numpy==2.5.3` and `pillow==12.3.0`), and `benchmarks/prototype_compare.py` (build env), each with a lockfile.
  - The build renders each draw group alone (hiding all other artists, keeping originally hidden ones hidden) into transparent RGBA on the identical canvas. It writes ZIP_STORED prototype files containing `manifest.json`, layers, the embedded font, slot data, LCC parameters, and the projected→pixel affine. Each file is written in three encodings: `png`, `raw`, and `zlib`. QPF is also written with the static-above group split into 6 layers.
  - The runtime includes a NumPy shapefile/DBF reader (input only, timed separately), a NumPy LCC projection, a polygon slot (even-odd fill: Pillow fills each ring, rings are XOR-ed, k× supersampling, coverage blending in draw order, rings clipped to the canvas), a grid slot (index-map gather plus Matplotlib's float32 normalisation and colormap rules), a text slot (Pillow with Matplotlib's layout rule), and Pillow or Agg-exact stacking. Every child process asserts that none of matplotlib, cartopy, shapely, pyproj, geopandas, pyogrio or pandas is in `sys.modules` (276/272 modules loaded for QPF/grid; none forbidden).
- **Key implementation findings:**
  - Saving with the resolved tight box as an explicit `Bbox` is byte-identical to `bbox_inches="tight"`. That is how the QPF crop is fixed; it replaces this checklist's "never use tight".
  - QPF shapefiles are spherical lon/lat, and the baseline's `to_crs(4326)` leaves the coordinates unchanged (max change 0.0°).
  - The NumPy LCC matches pyproj to 4.4×10⁻⁹ m.
  - The build first rendered GeoAxes' hidden tick labels into layers (seen in the grid colorbar area) because it forced artists visible. This was fixed by keeping original visibility; afterwards stacking Agg layers reproduces the full render to within 3 levels (QPF) and 5 levels (grid), with nothing over 8.
  - Text needs Agg's one-pixel upward shift (`dy = -1`) and origin rounding.
  - Pillow's polygon cost scales with edges × rows. Clipping rings to the canvas cut the Day 1-3 fill from 283 ms to 90 ms (1×), because only ~4 % of the vertices of on-canvas rings are actually on the canvas.
- **Verification:** `uv run benchmarks/prototype_build.py`, then `uv run benchmarks/prototype_runtime.py`, then `uv run benchmarks/prototype_compare.py`. Inputs compared: all 18 QPF products, each against its own Session 01b full render, and grid variants 0–3, each against `baseline_cartopy.render(variant)` (references in `compare/ref_grid_*.png`). Comparison images inspected: `compare/sheet_*.png` show reference | runtime | 8× difference. `uvx ruff check --line-length 120 benchmarks/` passes. Caches warm (priming run). Two discarded attempts: one crashed while summarising, and one used the hidden-tick-label build. Both were fixed and re-run before these results.
- **Evidence:** `benchmarks/results/prototype-20261008T155139Z/` (`build.json`, `*.cstack`, `layers/*.png`), `runtime-20261008T155236Z/runtime.json` (every run), and `runtime-20261008T155236Z/compare/compare.json` with diff and sheet images.
  - **File sizes:**

    | File | png | zlib | raw |
    | --- | --- | --- | --- |
    | QPF | 1.13 MB | 1.07 MB | 33.3 MB |
    | QPF, static-above split into 6 layers | 1.30 MB | 1.28 MB | 115 MB |
    | Grid | 1.29 MB | 1.28 MB | 12.3 MB |

    The embedded DejaVu font accounts for 0.63 MB of each file.
  - **Authoring time:** QPF 13.1 s, grid 16.8 s (dominated by Cartopy's first projection).
  - **Cold runtime, one product per process** (in-process end-to-end plus interpreter start; process wall times additionally include measurement-only extra encodes):
    - **QPF Day 1-3: 0.33 s with zlib** (0.36 s png, 0.33 s raw), against the 12.23 s baseline: **≈ 37× faster**.
      - Interpreter 19 ms; imports: numpy 26 ms, Pillow 14 ms.
      - Load: manifest 0.5 ms; layer decode with png 10.5 + 22.6 ms, zlib 4.7 + 4.1 ms, raw ~0 ms (zero-copy).
      - Product: input read 5 ms, polygons (2×) 166 ms, text 1.4 ms, stacking 14 ms, PNG RGBA level 6 64 ms. Other encodes: RGBA level 1 49 ms, RGB level 1 38 ms, RGB level 6 52 ms.
      - Peak RSS 234–237 MiB.
    - **Grid: 0.12 s with zlib** (0.13 s png), against the 16.20 s baseline: **≈ 130× faster**.
      - Data 10 ms, text 1.2 ms, stacking 2.5 ms, encode RGB level 6 18 ms.
      - Index-map load 1.7–3.1 ms; peak RSS 81 MiB.
  - **QPF loop, 18 products in one process: 3.64 s with zlib** (3.68 s png, 3.60 s raw), against the 32.1 s baseline: **≈ 8.8× faster**.
    - Per product: median **0.156 s** against the baseline's 0.92 s after its first product (**≈ 5.9× faster**). Polygons 81 ms median (16–321 ms), text 1.4 ms, stacking 12.7 ms, encode 63 ms.
    - The runtime's first product costs the same as the rest (0.158 s), whereas Cartopy's first takes 11.7 s.
    - Peak RSS 402–409 MiB.
    - Polygon resolution: 1× gives a 2.68 s loop (polygons 48 ms median); 4× gives 4.63 s (113 ms).
  - **Grid, 4 variants in one process:** 0.23 s, 30 ms per product, against the baseline's 0.32 s per warm repetition: **≈ 10.5× faster**.
  - **Break-even:** authoring pays for itself after about one cold render (QPF 13.1 s ÷ 11.9 s saved; grid 16.8 s ÷ 16.1 s saved), or within a single 18-product QPF run (28.5 s saved).
  - **Accuracy against each product's own full render** (median over products; "differ" = share of pixels differing by more than 8 levels):

    | Region | Result |
    | --- | --- |
    | QPF static opaque | exact |
    | QPF text (45.8 px) | 0 pixels differ by more than 32 levels (4.9 % differ by more than 8, antialiasing) |
    | QPF clear map, 1× | 0.55 % differ (0.43 % by more than 32; max 255) |
    | QPF clear map, 2× | 0.49 % differ (0.23 % by more than 32; max 188) |
    | QPF clear map, 4× | 0.38 % differ (0.05 % by more than 32; max 140) |
    | QPF overall (2×) | 0.33 % differ |
    | Grid data | 2.0 % differ, max 29 levels |
    | Grid text (16.7 px) | 27 % differ by more than 32 levels |

    - QPF data differences are thin lines along polygon edges (antialiasing approximation), invisible at normal zoom.
    - Grid data differences are Matplotlib's semi-transparent `pcolormesh` cell seams, which the index-map render does not reproduce.
    - Grid text differences come from FreeType hinting: Matplotlib forces auto-hinting, which Pillow does not expose, so glyphs are about 1 px taller in Matplotlib. Placement agrees.
    - Pillow stacking leaves 17 % of QPF pixels differing by 1–3 levels. The Agg-exact option lowers that to 1.4 %, but stacking then costs 291 ms instead of 13 ms per product.
    - With the split static layers, stacking costs 26 ms instead of 13 ms and 2.4 % of static pixels differ, versus 0.012 % with the flattened layer.
    - The few "static" outliers (max 114) are subtitle pixels of longer subtitles that fall outside the comparison's text mask, which is derived from the Day 1-3 subtitle.
- **Decisions:**
  - (1) Default layer encoding: **zlib**. It gives the smallest file and decodes ~4 ms per 4 MP layer. Raw saves another ~5 ms per load but makes files 31× larger; PNG decodes 3–5× slower.
  - (2) **Store contiguous static runs pre-flattened by default.** That halves stacking and load time and is more accurate; individual layers stay optional for visibility edits (Session 12).
  - (3) Polygon supersampling default **2×**; 1× and 4× remain options.
  - (4) **Pillow stacking** by default; Agg-exact as an option until it is optimised.
  - (5) Match the PNG mode of the output being replaced: RGBA for QPF, RGB for the grid. Encoding is now **~40 % of a QPF product's runtime**, which triggers the backlog item "PNG encoding dominates recurring runtime" (RGB level 1 would save ~25 ms per product).
- **Feasibility blockers:** none. Open risks for later sessions:
  - Small-size text matching (Session 09: decide between a documented tolerance, glyph hinting control through another FreeType binding, or build-time rendering of fixed-size text).
  - Polygon fill is now the largest per-product cost on complex products (up to 321 ms). Candidates: vertex decimation below the pixel scale, or native filling (profiling-driven, Session 16).
  - Grid seams differ from Matplotlib by design.
  - The font dominates file size (subsetting).
- **Remaining/blockers:** none for Session 02. Open user decisions as before: committing `resources/`, and production Matplotlib < 3.11.
- **Next:** Session 03. Re-verify `cartostack` on PyPI and conda-forge, then create `pyproject.toml` (Hatchling, Python ≥ 3.12, NumPy/Pillow core, `build` extra).

### 2026-10-08 — Session 03 — completed

- **Work:**
  - **Name check:** `cartostack` is still free (PyPI JSON API, `api.anaconda.org/package/conda-forge/cartostack`, and `github.com/conda-forge/cartostack-feedstock` all return 404).
  - **`pyproject.toml`:**
    - Packaging: Hatchling (`hatchling>=1.27`), version read from `src/cartostack/__init__.py`, `requires-python >=3.12`, MIT licence (SPDX), classifiers for 3.12–3.14, `Typing :: Typed`.
    - Core dependencies `numpy>=2.0` and `pillow>=10.1` (float font sizes); `build` extra `matplotlib>=3.10` and `cartopy>=0.25`; `dev` group with pytest ≥ 8.3, ruff ≥ 0.15, mypy ≥ 2.0.
    - sdist limited to `src/`, `tests/`, `scripts/fetch_fixtures.py`, README, LICENSE and pyproject. Wheel contains `src/cartostack` only.
    - Ruff: line length 100; rules E, W, F, I, UP, B, SIM, RUF, PT, NPY; `resources/` excluded; relaxed per-file rules for `benchmarks/`; formatting excludes `benchmarks/` and `*.md` (Ruff 0.16 would otherwise reformat the code blocks in the design documents).
    - mypy strict on `src/cartostack`. pytest with `--strict-markers` and `--strict-config`, plus `build` and `fixtures` markers.
  - **Package and environment:** `uv.lock` (29 packages) and `.python-version` (3.13). Package: `src/cartostack/__init__.py` (`__version__ = "0.0.1.dev0"`, no other modules yet) and `py.typed`.
  - **Tests:** `tests/conftest.py` blocks network access for every test, auto-skips `build` tests without the extra and `fixtures` tests when fixtures don't verify, and provides `fixture_dir`. `tests/test_package.py` checks the version against metadata, that core requires exactly numpy and pillow, that a fresh-process `import cartostack` loads none of matplotlib, cartopy, pyproj or shapely (and the same with the extra installed), and that network access is blocked. `tests/test_fixtures.py` covers offline verification of missing, changed and present fixtures.
  - **Fixture script:** moved `benchmarks/fetch_fixtures.py` to `scripts/fetch_fixtures.py` (`git mv`). It gained `fixture_dir()` (`$CARTOSTACK_FIXTURE_DIR`, default `benchmarks/data`), `verify()`, and an offline `--check` mode. The data stays in place so the QPF snapshot is not re-downloaded and replaced by newer forecasts. Benchmark messages were updated to the new path.
  - **CI:** `.github/workflows/ci.yml` uses checkout v7, setup-uv v10 (uv pinned to 0.12.23) and upload-artifact v7, with these jobs:
    - lint, format, and types;
    - core only on Python 3.12, 3.13 and 3.14, asserting the extras are not installed;
    - lowest direct core dependencies on Python 3.12;
    - the `build` extra on Python 3.12 and 3.14;
    - dist: build, install the wheel alone and import it outside the source tree, run the tests from the sdist, and upload the distributions.
  - **Docs:** added `CONTRIBUTING.md` and updated `CLAUDE.md`'s repository state.
  - Removed `noqa` comments in benchmarks that only my user-level Ruff config needed (comment-only change).
- **Verification (local, macOS arm64):**
  - Core environment (`uv sync --group dev`; numpy and Pillow installed, matplotlib and cartopy not): `uv run ruff format --check .` clean, `uv run ruff check .` clean, `uv run mypy` clean (strict), `uv run pytest` 7 passed and 1 skipped (`build`), `uv lock --check` OK.
  - `build`-extra environment (`uv sync --extra build --group dev`, adding cartopy 0.26.0, matplotlib 3.11.2, pyproj 3.8.0, shapely 2.2.0): 8 passed. Switching back to core removed both again.
  - Other interpreters and versions: `uv run --isolated --python 3.12 --group dev pytest` and the same with `--python 3.14` each gave 7 passed and 1 skipped. Lowest direct dependencies (Python 3.12 venv with `uv pip install --resolution lowest-direct .`, giving numpy 2.0.0 and Pillow 10.1.0): 7 passed, 1 skipped.
  - Distributions: `uv build` gave a 19 kB wheel and a 38 kB sdist with the contents listed above. The wheel installed into a fresh 3.13 venv pulls in only numpy 2.5.3 and Pillow 12.3.0, imports from site-packages outside the repository, and loads no extras. Tests from the unpacked sdist: 6 passed, 2 skipped (fixtures absent, no extra), which also exercises the skip path.
  - The workflow's dist-job shell steps ran locally verbatim (paths redirected) and passed. `actionlint` 1.7.12 is clean.
  - `uv run scripts/fetch_fixtures.py --check` passes, and re-running the moved script left `fixtures.json` files, QPF and download records identical.
- **Evidence:** command outputs above; no benchmark artifacts in this session.
- **Decisions/deviations:**
  - Lower bounds numpy 2.0 and Pillow 10.1 are tested in CI. Python 3.12 is the minimum, consistent with Session 00's SPEC 0 reasoning; cartopy 0.26 has wheels for 3.11–3.15, so 3.14 is tested.
  - CI targets ubuntu-latest only for now; cross-platform smoke tests are part of Session 15.
  - Fixture-dependent tests skip in CI because fixtures are gitignored and partly local (SWRCC). Sessions that need them in CI must add small committed fixtures or a cached download.
  - The README (the 1,900-line proposal) is the package's long description for now; Session 14 separates a quickstart.
- **Remaining/blockers:** CI has not run on GitHub yet; it runs when these files are pushed, which needs the user's approval to commit and push. Open user decisions as before: committing `resources/` (not tracked), and production Matplotlib < 3.11.
- **Next:** Session 04. Write `docs/format.md` (container, canvas geometry including the output crop, georeferencing, layer, grid, polygon and text slot records) from the Session 02 prototype manifest, then `cartostack/geometry.py` with validation tests.

### 2026-10-08 — Session 04 — completed

- **Work:**
  - `docs/format.md` (format **1.0**) covers:
    - the container: ZIP with a `mimetype` member first, everything ZIP_STORED, `manifest.json`, member-name rules, and a `members` table with byte sizes and SHA-256;
    - versioning: reject other MAJOR versions first; MINOR versions only add optional fields; unknown fields are ignored and preserved; a new kind or encoding needs a new MAJOR;
    - geometry: canvas with dpi, the resolved axes rectangle, `clip: "axes"`, and a `figure` record whose `crop_in` represents the fixed output crop with the canvas being the cropped output;
    - georeference: `crs.proj4`/`wkt`, `projection` as plain PROJ-named numbers (LCC supported; other names make polygon slots invalid), `projected_extent`, and the `world_to_pixel` affine. Lon/lat are taken on the projection's ellipsoid;
    - consistency rules and a canonical-JSON SHA-256 geometry fingerprint;
    - layers: common fields, stacking ties broken by array order, straight-alpha source-over compositing, and the encodings `rgba8+zlib` (default), `rgba8`, `png`, `i32le+zlib`, `i32le`;
    - raster and colorbar layers (colorbar redraw parameters deferred to 1.1 / Session 11);
    - grid slots, with Matplotlib's float32 `Normalize`/`Colormap` rules and LUT rows under/over/bad;
    - polygon slots: first-match closed-open bins, `round_decimals`, fallback, ordering by bin order then input order, even-odd fill, k×k coverage supersampling, clipping;
    - text slots: Matplotlib's layout rule written out, snap, offset, rotation 0;
    - optional provenance, the validation contract, and a table mapping the Session 02 prototype to 1.0, with deferred items listed.
  - `src/cartostack/errors.py`: `CartoStackError`, `FormatError` (carries every problem), `GeometryError`, `UnsupportedVersionError`.
  - `src/cartostack/_validate.py`: field readers that collect problems.
  - `src/cartostack/geometry.py`: frozen `Rect`, `FigureCrop`, `Projection`, `Georeference` (`to_pixel`/`to_world`), and `CanvasGeometry` (`from_dict`/`to_dict`, `fingerprint`, `compatible_with`; validated in `__post_init__`).
  - `src/cartostack/manifest.py`: `parse_manifest` / `validate_manifest` returning frozen typed records (`RasterLayer`, `ColorbarLayer`, `GridSlot`, `PolygonSlot`, `TextSlot`, `Manifest.draw_order()`, read-only `members`), plus `load_schema()`.
  - `src/cartostack/schemas/manifest-1.schema.json`: JSON Schema draft 2020-12, structural only, shipped in the wheel.
  - `docs/examples/{qpf,grid,minimal}.manifest.json`. QPF and grid are the Session 02 prototype files translated to 1.0, with real member sizes and digests.
  - Tests: `tests/test_geometry.py` (17 tests) and `tests/test_manifest.py` (60 cases).
  - `jsonschema>=4.23` added to the dev group; the README's provisional manifest sketch now points to the spec.
- **Verification:**
  - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 6 files) and `uv lock --check` all pass.
  - `uv run pytest`: 85 passed and 1 skipped core-only; 86 passed with `--extra build`.
  - The tests cover:
    - the examples validated by both the JSON Schema and `parse_manifest`, and the schema itself checked as valid draft 2020-12;
    - a pinned fingerprint, its independence from key order, number spelling and unknown fields, and its sensitivity to geometry changes;
    - immutability;
    - invalid dimensions, DPI, and axes;
    - figure crop and georeference/axes inconsistencies, a non-invertible affine, LCC parameter rules, and an unknown projection;
    - version rules: other majors rejected first, 1.7 with unknown fields read, malformed versions rejected;
    - 24 inconsistent-record cases, each also asserting whether the JSON Schema rejects it (schema rejections are always a subset of the parser's);
    - collecting all problems, and parsing in a fresh process without loading numpy, PIL, or any extra.
  - The built wheel contains the schema; installed alone, it loads the schema and parses the QPF example without numpy.
  - Review against the prototype: `docs/format.md` §13 maps every Session 02 manifest field to 1.0, and the translated prototype manifests validate.
- **Bugs found by the tests and fixed:**
  - With invalid geometry, layers using default sizes reported spurious "width/height got None" errors.
  - A missing required field was reported twice.
- **Decisions/deviations:**
  - The canvas is the (cropped) output image. The crop is recorded in `figure.crop_in` rather than applied to integer-placed layers, because the tight crop's origin is fractional (432.05 px for QPF).
  - Layer kind `polygon` was added beside the planned raster, grid, text and colorbar.
  - Member checksums live in one `members` table instead of per layer.
  - Grid `alpha` is stored as a float with the 8-bit rule `floor(alpha·255 + 0.5)` (Agg's conversion).
  - Canvas size limit 100 000 px.
  - The member, encoding and LUT-row decoding checks, and the `mimetype` check, are left to the Session 05 reader.
- **Remaining/blockers:** none for Session 04. CI is still unverified on GitHub until a push. Open user decisions as before.
- **Next:** Session 05. Implement `cartostack/io.py`: an atomic writer (mimetype first, ZIP_STORED, members table) and an eager reader (version first, digests, encodings), with layer objects owning `uint8` RGBA buffers and round-trip tests.

### 2026-10-08 — Session 05 — completed

- **Work:**
  - **`src/cartostack/layers.py`:**
    - Immutable layer classes `RasterLayer`, `ColorbarLayer`, `GridSlot`, `PolygonSlot` and `TextSlot` (frozen, keyword-only dataclasses).
    - Pixels, index maps and LUTs are copied into owned, read-only, C-contiguous buffers, with dtype, shape and value-range checks (`owned_rgba`, `owned_index_map`, `owned_lut`; errors raise `LayerError`).
    - Sizes are resolved from pixels or the index map; numbers are normalised to float/int so equal scenes serialise identically.
    - Unknown manifest fields are kept in `extra`; encoded source bytes are kept for verbatim re-saves.
  - **`src/cartostack/io.py`:**
    - Encoders and decoders for `rgba8+zlib`, `rgba8`, `png`, `i32le+zlib` and `i32le`. Corrupt or mis-sized data raises `FormatError` naming the member.
    - `read_archive` checks, in order: ZIP; `mimetype` first and exact; manifest present and valid JSON; major version, before any member is trusted; ZIP_STORED storage; archive entries ↔ `members` table both ways; size and SHA-256 of every member.
    - `write_archive` is deterministic (fixed order, timestamps and attributes) and atomic (temp file in the same directory, fsync, `os.replace`, temp removed on any failure).
  - **`src/cartostack/scene.py`:**
    - `Scene(geometry, layers, assets=..., provenance=..., extra=...)` validates unique ids, placement against the canvas, text fonts present among assets, colorbar slots being grid layers, and polygon slots having a supported projection.
    - Read access: `draw_order()`, `[...]`, `in`, `len`, and `assets`.
    - `Scene.load` loads eagerly, wraps errors with the file path, and keeps unknown fields at the top level, in geometry, in layer records and in their sections. Members that are not layer data (fonts, others) become assets.
    - `Scene.save(path, encoding=None)` writes generated member names `layers/<id>.<ext>` and `slots/<id>/{index,lut}.<ext>`, reuses unchanged encoded bytes, validates its own manifest with `parse_manifest` before writing, and saves atomically.
  - **`cartostack/__init__.py`** exports the public names. The NumPy-backed ones (`Scene` and the layer classes) load lazily, so `cartostack.manifest`/`geometry` validation still imports no NumPy.
  - **Validator fix in `manifest.py`:** duplicate layer ids are now found from the raw ids, so they are reported even when the other layer is otherwise invalid.
  - `docs/format.md` §2 documents the reference writer's determinism and which unknown fields it preserves.
  - **Tests:** `tests/helpers.py` (scene and archive-surgery helpers), `tests/test_io.py` (round trips for every kind × 3 pixel encodings × 2 index encodings, byte stability, re-encoding, verbatim reuse, unknown fields, assets and provenance, two atomic-failure cases, 16 error cases, core-only subprocess), and `tests/test_layers.py` (ownership, validation, immutability, scene consistency).
- **Verification:**
  - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 9 files) and `uv lock --check` all pass.
  - `uv run pytest`: **130 passed, 1 skipped** core-only; 131 passed with `--extra build`; 130 passed and 1 skipped on Python 3.12 with the lowest direct dependencies (numpy 2.0.0, Pillow 10.1.0).
  - Save → load → save is byte-identical, archives and manifests alike, and so is saving the same scene twice.
  - Loaded layers are pixel-identical in every encoding.
  - A failed encode during save leaves the existing file byte-identical with no temporary files left, and a failed rename leaves the directory empty.
  - Every corruption case raises an actionable `FormatError` or `UnsupportedVersionError` naming the file and member: missing file, not a ZIP, missing or wrong `mimetype`, invalid JSON, major version 2 (reported before members), missing or unlisted member, checksum and size mismatch, compressed member, corrupt zlib, wrong decoded size, non-RGBA PNG, out-of-range index map, and invalid manifests (all problems listed).
  - Load and save in a fresh process import numpy and PIL but not matplotlib, cartopy, pyproj or shapely, and reproduce the same bytes.
- **Evidence:** the QPF scene at full size (2210 × 1848; the Session 02 below/above layers, polygon slot, subtitle and font), measured on an M3 Pro:

  | Encoding | File size | Save | Load | Load + re-save |
  | --- | --- | --- | --- | --- |
  | `rgba8+zlib` | 1.08 MB | 92 ms | 8.2 ms | 11 ms |
  | `png` | 1.14 MB | 90 ms | 31.7 ms | 34 ms |
  | `rgba8` | 33.3 MB | 21 ms | 16.5 ms | 39 ms |

  Every re-save was byte-identical.
- **Bugs found by the tests and fixed:**
  - An integer `order` serialised as `12` but reloaded as `12.0`, breaking byte stability on the first round trip; numbers are now normalised.
  - Duplicate ids could be masked by another error in the same layer.
- **Decisions/deviations:**
  - Layers are immutable value objects. Editing, replacement and atomicity of edits belong to Session 07 at the `Scene` level.
  - Width and height of `None` mean "canvas size" in memory; the writer always writes resolved sizes.
  - Fonts and any other non-layer members are scene `assets`, kept verbatim.
  - New scenes get `provenance = {"cartostack": <version>}`; loaded provenance is kept as is, with no save timestamps, to stay byte-stable.
  - Eager loading copies decoded arrays into owned buffers; avoiding that copy belongs to lazy loading in Session 12.
  - **User decision (2026-10-08):** `resources/` is gitignored and never committed (`.gitignore` line 2).
- **Remaining/blockers:** none for Session 05. CI has still not run on GitHub; it needs a push, which needs the user's approval.
- **Next:** Session 06. Write `cartostack/compositor.py`: source-over in draw order honouring visibility, opacity and cropped or offset placement; `Scene.render()` → RGBA; `Scene.save_png()`; tests against independently computed pixels.

### 2026-10-08 — Session 03 follow-up — CI fix

- **Work:** The first GitHub Actions run (run 37812167444, push of `1d76e38` to `dev`) failed in all 8 jobs at "Set up job", before any project code ran. Cause: `.github/workflows/ci.yml` referenced `astral-sh/setup-uv@v10`, but that action publishes only full version tags (`v10.2.0`, ...). The GitHub API returns 404 for `refs/tags/v10` and 200 for `v10.2.0`, `actions/checkout` `v7` and `actions/upload-artifact` `v7`. Pinned `astral-sh/setup-uv@v10.2.0` in all 5 places (one per job definition).
- **Verification:** `uvx --from actionlint-py actionlint` is clean. Job and step results were read through the public GitHub API (`/actions/runs/37812167444/jobs`). The fix is unverified on GitHub until the next push.
- **Next:** after the user pushes, confirm that all CI jobs pass, then continue with Session 06.

### 2026-10-08 — CI removed (user decision)

- **Work:** The user decided to drop CI and GitHub Actions entirely. Deleted `.github/workflows/ci.yml` (`git rm -r -f .github`; the forced removal also discarded the uncommitted `setup-uv@v10.2.0` pin from the "CI fix" entry above). Removed CI from the repository state, handoff, environment notes, development commands (`actionlint`), `CONTRIBUTING.md` and `CLAUDE.md`. Sessions 08b, 09, 10 and 13 now verify in local environments; Session 13's deliverable is a documented manual fresh-environment procedure with recorded results. The procedure is in `CONTRIBUTING.md` → "Environment checks". It contains the same commands the CI jobs ran (lint and types; core-only on Python 3.12–3.14; lowest direct dependencies; `build` extra; wheel and sdist), run by hand. Session 03's record above is kept as history.
- **Verification:** `git status` shows `.github/workflows/ci.yml` staged as deleted, and no `.github/` directory remains. Local checks pass: `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy`, and `uv run pytest` (130 passed, 1 skipped).
- **Decisions:** No automated remote checks until the user re-enables them. Manual environment checks happen once the package works end to end (Sessions 13 and 15).
- **Next:** Session 06 (compositor, cropped placement, PNG output).

### 2026-10-08 — Session 06 — completed

- **Work:**
  - **`src/cartostack/compositor.py`:**
    - Straight-alpha source-over with Pillow's `Image.alpha_composite`, starting from a transparent canvas in draw order. Hidden layers, layers without pixels (slots not yet rendered) and opacity 0 are skipped. Opacity scales alpha by `floor(alpha · opacity + 0.5)` through a 256-entry table.
    - `window()` clips a placed array to the canvas (negative offsets, partly off-canvas, larger than the canvas). Map clipping (`geometry.clip`) is not applied here; it stays a slot-rendering rule.
    - `Compositor` splits the drawn layers into runs of static layers (`raster`, `colorbar`) and slots. It flattens the bottom run onto the canvas, flattens a static run of 2 or more layers above a slot into an image cropped to the run's bounding box, and reuses each one while the run holds the same layer objects (identity; layers are immutable). Slots and single static layers above a slot are composited every render. Only runs used by the latest render stay cached.
    - `composite()` is plain layer-by-layer stacking; `encode_png(image, mode="RGBA"|"RGB", compress_level=0..9, background=(255, 255, 255))`. RGB first composites over the opaque background, skipped when the image is already opaque.
  - **`Scene`:** `render(*, flatten=True)` returns a new, writable `(height, width, 4)` uint8 array; `save_png(path, *, mode, compress_level, background, flatten)` writes atomically. The compositor is created lazily, per scene.
  - **`io.py`:** the temporary-file, fsync and rename logic was factored out of `write_archive` into `atomic_file()`, which `save_png` reuses. Archive bytes are unchanged; the Session 05 atomicity tests still pass.
  - **`docs/format.md` §6.2:** adds the opacity rounding rule, keeps placement clipping separate from map clipping, documents the reference runtime's flattening tolerance, and states that output has the canvas size, with RGB composited over a background.
  - **Tests:** `tests/test_compositor.py` (36 tests).
    - Two independent references: a NumPy re-derivation of Pillow's integer formula (exact) and textbook float Porter-Duff (within 1 level per operation, within 3 over a stack).
    - Hand-computed pixels, tie ordering, hidden/transparent/zero-opacity/empty layers, and slots without pixels.
    - Five placement cases where a cropped layer equals the same pixels padded to the full canvas, `window()` itself, and raster pixels kept outside the axes.
    - Flattening: an `_over` spy shows the second render composites only the slots and single layers; a replaced layer object invalidates only its own run; exact below slots; ≤3 levels above slots; a 200-stack stress test; exact for opaque runs.
    - `render()` returns independent arrays, and a save → load → render round trip matches.
    - PNG: RGBA decodes pixel-identical at levels 0/1/6/9, RGB over two backgrounds matches the NumPy reference, an opaque canvas drops alpha, sizes shrink with level, bytes are deterministic, bad options raise with no file left behind, and a failed rename leaves the existing PNG intact.
    - A fresh-process `load` + `save_png` imports none of matplotlib, cartopy, pyproj or shapely.
  - **`benchmarks/compositor_timing.py`** (project environment): builds 1.0 scenes from the Session 02 prototype layers (grid, QPF, and QPF with static-above split into 6 layers; subtitle cropped to its ink box), saves them, then times load, render and encoding.
- **Verification (local, macOS arm64):**
  - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 10 files) and `uv lock --check` all pass.
  - `uv run pytest`: **166 passed, 1 skipped** core-only; 167 passed with `--extra build` (environment restored to core only afterwards). 166 passed and 1 skipped on Python 3.12 with the lowest direct dependencies (numpy 2.0.0, Pillow 10.1.0; venv in the session scratchpad per `CONTRIBUTING.md`).
  - `uv run python benchmarks/compositor_timing.py` (20 reps, Python 3.13.4, numpy 2.5.3, Pillow 12.3.0). For every scene, layer-by-layer renders equal the Session 02 prototype's Pillow stacking exactly, and saved PNGs decode identical to `render()` at the canvas size. No forbidden module was loaded.
  - The QPF output (`qpf.png`) was inspected visually and is correct.
- **Evidence:** `benchmarks/results/compositor-20261008T181244Z/` (`results.json`, scenes, PNGs). Medians, M3 Pro:

  | Scene | Load | First render | Warm render | Layer by layer | PNG RGBA 6 | RGBA 1 | RGB 6 | RGB 1 |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | Grid (Session 01), 1200 × 800, 4 layers | 5.7 ms | 4.6 ms | **2.1 ms** | 4.0 ms | 23.0 ms | 13.1 ms | 19.4 ms | 10.8 ms |
  | QPF, 2210 × 1848, 4 layers | 12.0 ms | 25.3 ms | **15.3 ms** | 22.9 ms | 64.8 ms | 48.6 ms | 54.9 ms | 41.7 ms |
  | QPF split, 9 layers | 27.4 ms | 44.9 ms | **15.0 ms** | 33.2 ms | 64.3 ms | 49.3 ms | 56.8 ms | 41.8 ms |

  - Warm renders include a copy of the cached bottom run and the copy into the returned array.
  - Flattening: grid and QPF are exact (their upper runs are single layers). The split QPF's 6-layer upper run differs from layer-by-layer stacking by at most 1 level in 19,120 of 16.3 M values (0.12 %), and flattening makes it as fast as the pre-flattened file.
  - PNG encoding remains the largest per-product cost (as in Session 02).
- **Decisions/deviations:**
  - Exactness: renders equal layer-by-layer Pillow stacking except for multi-layer static runs above a slot, which are bounded by rounding (≤3 levels in stress tests, documented in §6.2). `flatten=False` gives the exact per-layer result.
  - "Unchanged" means the same layer object. Slots are treated as changing every render and are never flattened.
  - RGB output composites over white by default (Matplotlib's default figure colour) rather than dropping alpha; `background` overrides it. The default PNG mode is RGBA with `compress_level=6`. Session 02's per-workload choice (QPF RGBA, grid RGB) is a caller argument.
  - `render()` returns an owned copy (~3 ms at 4 MP), so callers cannot corrupt the cache; `save_png` encodes from the internal image without that copy.
- **Remaining/blockers:** none for Session 06.
- **Next:** Session 07. Add the editing API on `Scene` (`replace_layer`/`scene[id] = rgba`, `add_layer`, `remove_layer`, visibility, opacity and order). Each edit swaps in new immutable layer objects so that the compositor's run cache invalidates exactly the edited run.

### 2026-10-08 — Session 07 — completed

- **Work:**
  - **Editing API on `Scene`** (`src/cartostack/scene.py`). Every edit builds the new layer tuple, validates it against the geometry and the other layers with the same checks as construction (`_problems`), and only then swaps it in (`_commit`). A failed edit therefore leaves the scene, and its compositor cache, as they were.
    - `replace_layer(id, new)` and `scene[id] = new`: `new` is either a layer object with the same id (any kind, size and placement), or an RGBA array for a raster or colorbar layer. An array keeps every other field and must have the layer's `(height, width)`. Raw pixels for a slot are refused (slots get new data, Sessions 08–09). A mismatched id is refused (rename with remove and add).
    - `add_layer(layer)` or `add_layer(id, rgba, *, order=None, left=0, top=0, visible=True, opacity=1.0, encoding=...)`: `order` defaults to above every existing layer, and a tie draws after the existing layers.
    - `remove_layer(id)` and `del scene[id]` return the removed layer. Removing a grid slot that a colorbar uses is refused.
    - `update_layer(id, **changes)` accepts `order`, `left`, `top`, `visible`, `opacity` and `encoding`. `show(id)` and `hide(id)` are shorthands.
    - `KeyError` messages list the existing ids. `with Scene.load(...) as scene:` works (no-op today; lazy loading may hold the file open).
  - **`src/cartostack/layers.py`:**
    - `evolve(layer, **changes)` re-creates a validated layer. It shares unchanged buffers: arrays created by the layer module are tracked in a weak registry, so they are not copied again. New `pixels` drop only the cached encoded pixel bytes.
    - `EDITABLE_FIELDS` lists the fields `update_layer` accepts.
    - Assigning or deleting a layer attribute raises `ImmutableLayerError` (a `FrozenInstanceError`, so an `AttributeError`). `edit_refusal()` builds its message, which names the right `Scene` call. For attributes the file does not store, such as `linewidth` on a raster or `cmap` on a colorbar, it says the layer holds only pixels and must be re-authored or replaced. Slot parameters such as `vmin` and text `value` direct to replacing the whole layer. `update_layer` gives the same explanations.
  - **README:** the "Adding layers", "Visibility" and "Style changes" snippets and the end-to-end example now use the settled names (`left`/`top`, `hide`, `update_layer`). The [target v0.1 example](#target-v01-package) records the settled API.
  - **Tests:**
    - `tests/test_editing.py` (37 tests): replacement by array or layer object, item assignment and deletion, adding and removing, hide/show, and field updates.
    - Failure cases leave the scene unchanged (layer identity, render and byte-identical re-save): 9 failed replacements, 12 refused updates and 5 refused additions.
    - Error messages for in-place assignment, and read-only pixels and geometry.
    - Spies show that remove, hide and update run no compositing, decoding or copying, and that only the edited run re-composites.
    - Saving an edited scene decodes nothing and encodes only the replaced and added pixels (2 calls, no index or LUT encodes). Untouched members are byte-identical to the template's.
    - The template is unchanged after edits and a save to a new path; the edited file reloads with the same draw order and pixels, and re-saves byte-identically.
    - A failed save over the template keeps it, and the edited scene can still be saved afterwards.
    - An empty scene renders transparent but cannot be saved (`FormatError`, no file).
    - The context manager is tested.
    - `tests/test_layers.py`: `evolve` shares owned buffers and still snapshots foreign read-only views.
- **Verification (local, macOS arm64):**
  - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 10 files) and `uv lock --check` all pass.
  - `uv run pytest`: **204 passed, 1 skipped** core-only; 205 passed with `--extra build` (environment restored to core only). 204 passed and 1 skipped on Python 3.12.15 with the lowest direct dependencies (numpy 2.0.0, Pillow 10.1.0).
- **Evidence:** edit costs on the full-size QPF file (`compositor-20261008T181244Z/qpf.cstack`, 2210 × 1848), medians on an M3 Pro:

  | Operation | Time |
  | --- | --- |
  | `update_layer(opacity)` | 0.009 ms |
  | `hide` + `show` | 0.016 ms |
  | Remove and re-add a layer object | 0.004 ms |
  | `replace_layer` with a 4 MP RGBA array (the ownership copy) | 0.51 ms |
  | `save` unedited, or after a metadata edit (all members reused) | 1.5 ms |
  | `save` after a pixel replacement (re-encodes zlib) | 63 ms |

  Measured with an inline script, not a committed benchmark.
- **Decisions/deviations:**
  - **Layers stay immutable**, so the README's `scene["counties"].visible = False` became `scene.hide("counties")` or `scene.update_layer(...)`; the assignment raises an error naming that call. This keeps edits atomic and lets the compositor recognise unchanged runs by identity.
  - `add_layer` uses `left`/`top` (the format's names) instead of the README's `x`/`y`.
  - Array replacement must keep the layer's size; to change size or placement, pass a layer object.
  - A layer's pixels are re-encoded at every save until it is reloaded; caching that encoding is left to Session 12 (storage performance).
  - Fonts and other assets cannot be added yet, so a new text slot must use a font already in the file (Session 09).
- **Remaining/blockers:** none for Session 07.
- **Next:** Session 08. Implement grid rendering: values → pixels through the index map, Matplotlib's float32 normalisation and the LUT (`docs/format.md` §8), exposed as `Scene.replace_grid(id, values)`, which swaps in a new `GridSlot` with the rendered pixels.

### 2026-10-09 — Plan revision — benchmark figures, polygon slots first, smaller Session 12

- **Work:** Planning only; no code changed. Reviewed progress through Session 07 with the user and revised the remaining sessions.
- **Changes:**
  - **New Session 07b (benchmark report and comparison figures),** next to run. Until now, timing evidence existed only as tables here and in `results.json`. The user wants figures comparing CartoStack's speed and efficiency with the standard Matplotlib/Cartopy render. `benchmarks/report.py` plots the Session 01/01b/02/06 results through a common schema. Every later session that records timings (08b, 08, 09, 12, 15, 16) regenerates it, which is now part of workflow step 4 and of those sessions' checklists. Figures keep cold, warm and loop speed-ups separate (for example, QPF is ~37× cold but ~5.9× per warm product), and pair speed with accuracy.
  - **Session 08b (polygon slots) now runs before 08 (grid slots).** Both depend on 07b, and 09 depends on both. QPF is the primary real workload, and polygon fill is its largest remaining per-product cost. Nothing in polygon slots needs grid slots.
  - **Package benchmark brought forward to 08b.** `benchmarks/package_qpf.py` measures the package itself (not the prototype) against the Cartopy baseline and the Session 02 prototype. A per-product regression of more than 10 % against the prototype must be investigated before 08b completes. Session 08 adds the grid equivalent, and Session 15 re-runs both from the installed wheel.
  - **Session 12 reduced** to archive lifetime/close semantics, pre-flattened static runs, and reusing encoded bytes at save. Lazy decoding is now gated on measurement: Session 06 measured a full QPF load at 12 ms (~4 % of a cold product). It is implemented only if load plus decode exceeds 10 % of a cold product; otherwise it moves to the extension backlog (row added). Session 13's lazy/eager check is now conditional.
  - Session 16 names the priority candidates from measurements so far: PNG encoding and polygon fill.
- **Remaining/blockers:** none. Open user decision unchanged: production Matplotlib < 3.11 or a legend-title fix.
- **Next:** Session 07b. Write the adapters from the existing `results.json`/`runtime.json`/`compare.json` files to the common schema, then the per-phase cold-product figure.

### 2026-10-09 — Session 07b — completed

- **Work:**
  - Added `benchmarks/report.py` (PEP 723: matplotlib 3.11.2, numpy 2.5.3, Python 3.13, `exclude-newer = 2026-10-08`; lockfile `benchmarks/report.py.lock`). Matplotlib is used only by this benchmark script, never on the runtime path.
  - **Common schema `cartostack-bench/1`**, documented in the script's docstring. Records have series, workload, mode (`cold-process`, `loop`, `warm`, `components`), config, source, runs with once-per-process phases, and products with per-product phases. Optional accuracy is attached. Phases: start-up, load, read input, render data, draw static content, text, composite, encode, other. Every run's total is the sum of its phases.
  - **Adapters** for the Session 01 grid baseline, the Session 01b QPF baseline, the Session 02 prototype runtime with its `compare.json`, and the Session 06 compositor timing. Native `bench.json` files under `benchmarks/results/` (or `--bench`) are read directly, so 08b/08 only have to write that file. Inputs default to the latest of each kind; baselines need at least 5 cold runs (shorter runs are treated as smoke runs). The chosen paths go into `report.json`, `index.md` and every figure's footer.
  - **Figures** (PNG, 150 dpi), using the `dataviz` skill's validated palette. One colour per series throughout: Cartopy blue, prototype orange, package aqua. Phases use the eight categorical slots in validated order, plus gray for "other".
    - `speedup_summary.png`: cold, loop and warm speed-ups as separate rows, on a log axis, with the median ratio and a min–max range.
    - `cold_phases.png`: total time to one PNG on a linear axis, next to each series' phase shares.
    - `qpf_loop_cumulative.png`: every loop run, cumulative over 18 products.
    - `qpf_time_vs_polygons.png`: data-render and whole-product time against polygon count.
    - `accuracy_vs_speed.png`: per-product time against the share of pixels differing by more than 8 and more than 32 levels, for each Session 02 configuration.
    - `package_components.png`: Session 06 package load, composite and encode times against the prototype.
  - `index.md` repeats every plotted number as a table (the palette's contrast warning requires a table view), and adds the reconciliation against this log and the notes.
- **Common basis (decision).** All totals are interpreter start plus in-process time, excluding measurement-only work (extra PNG encodes, output decode and hashing) and process exit, for every series.
  - Per-product ("warm") values are the median over products of each product's median across runs. The first product in a process is excluded.
  - Per-product speed-up ranges are the spread of same-product ratios.
  - Loop totals are compared only when both series rendered the same product list. A 4-product smoke loop is refused, and the refusal is reported.
- **Corrections found.** Several Session 02 headline ratios divided the baseline's process wall time by the prototype's in-process time. The baseline's process wall includes the post-run PNG analysis, the measurement encodes and process exit. On the common basis:

  | Comparison | Common basis | Logged |
  | --- | --- | --- |
  | QPF cold | **35.7×** (11.93 s → 334 ms) | ≈ 37× |
  | QPF 18-product loop | **7.6×** (27.62 s → 3.64 s) | ≈ 8.8× |
  | QPF per product | **5.8×** (923 → 158 ms; reading the input is now included in both) | — |
  | Grid cold | **131.5×** (16.11 s → 123 ms) | — |
  | Grid warm | **8.1×** (269 → 33 ms) | 10.5× |

  The grid warm baseline had included three extra encodes and hashing. No Session 02 decision changes. The older log entries are left as recorded.
- **Adapter bugs caught and fixed while verifying.**
  - The prototype imports NumPy at module level, so its NumPy time is already inside `pre_main_s`. Counting it again had made "other" negative.
  - The prototype's `render_total` excludes `read_input`.
  - The report now refuses to plot if any phase comes out below −1 ms, or if a phase name is unknown.
- **Verification:**
  - `uv run benchmarks/report.py` → `benchmarks/results/report-20261009T144517Z/`. 18 of the 19 logged values were recomputed on the log's own basis and match to the quoted precision. The exception is Session 02's "0.156 s" per product: the data give 0.1567 s (a truncation in the log).
  - All six figures were inspected visually. Two layout problems were fixed and re-inspected: overlapping titles and labels, and a wrong grid config label. One basis problem was also fixed: the components figure first compared prototype PNG loads with package zlib loads.
  - Smoke test of the native path: a synthetic `bench.json` with `package` cold and loop records (in the session scratchpad, not in `benchmarks/results`) produced the package series in the speed-up figure. Its 4-product loop was refused for loop totals, as intended.
  - `uvx ruff check --line-length 120 benchmarks/`, `uv run ruff check .` and `uv run ruff format --check .` pass. Three intermediate report directories from this session were deleted; only the final one remains.
- **Evidence:** `benchmarks/results/report-20261009T144517Z/` (`index.md`, `report.json`, six PNGs).
- **Remaining/blockers:** none.
- **Next:** Session 08b. Port the NumPy LCC and polygon fill from `benchmarks/prototype_runtime.py` into `cartostack/projection.py` and a polygon slot. Then write `benchmarks/package_qpf.py` to emit `bench.json` with series `package`, and regenerate the report.

### 2026-10-09 — Session 08b — completed

- **Work:**
  - **`src/cartostack/projection.py`:** ellipsoidal Lambert Conformal Conic forward projection in NumPy (Snyder eq. 15-1…15-10; tangent cone when `lat_1 == lat_2`). Constants are cached per `Projection`. `to_pixels` applies `world_to_pixel`. `ProjectionError` covers an unsupported projection, invalid parameters (re-checked even for directly built geometries), non-finite input, latitudes outside ±90, and the pole opposite the cone's apex. Longitudes are reduced to within 180° of `lon_0`, as PROJ does.
  - **`src/cartostack/polygons.py`:**
    - `normalize`: one record per value. A record is a list of `(n, 2)` rings, or one bare ring. Ragged lists, `None` and generators are accepted; values must be real numbers.
    - `classify`: Python `round`, first matching bin in list order, closed-open intervals, NaN to the fallback. A value with no bin and no fallback raises `PolygonDataError` naming the record.
    - `coverage`: the exact sample-centre fill (see Decisions).
    - `_blend`: Pillow's integer source-over, run only on partly covered pixels or translucent colours. Fully covered opaque pixels are assigned through a `uint32` view of the buffer.
    - `render`: projects every vertex of a product in one call and draws records in stable bin order.
    - `records_from_features` / `geometry_rings`: GeoJSON-like `FeatureCollection`, `Feature`, `Polygon` and `MultiPolygon` input, or any `__geo_interface__` object (shapely, GeoPandas). Neither library is imported; z coordinates are dropped.
  - **`Scene.replace_polygons(id, records, values)`:** renders, then swaps in a new `PolygonSlot` through `evolve` and `_commit`. It is atomic and re-composites only the slot. `PolygonDataError` and `ProjectionError` are exported from `cartostack`.
  - **`docs/format.md` §9** now states the fill exactly (spec changed before code relied on it). It covers the sample positions, the half-open crossing rule, sub-pixel clipping, `floor(a·c/k² + 0.5)` alpha with the §6.2 operator, and a "Choosing `k`" table recommending `supersample: 4`.
  - **Tests:**
    - `tests/test_projection.py` (26 tests): Snyder's two published worked examples (ellipsoid to 0.15 m, unit sphere to 1e-7), round trips through an independently written Snyder inverse (1e-9°) for five projections, wrapping, poles, invalid input and projections, and pyproj agreement within 1 mm over 61 × 41 grids for four projections (build extra).
    - `tests/test_polygons.py` (51 tests):
      - the exact fill against a brute-force even-odd test of every sample, over 360 random polygons with k = 1–5, including vertices on the sample grid (ties), clips and far-away vertices;
      - hand-computed pixels for squares, holes and islands, ring orientation, a 2× partial edge (alpha 128), partial and translucent blending against Pillow, draw order with ties, clipping to fractional axes, local placement and off-map input;
      - classification and input-validation cases, and GeoJSON-like input;
      - `Scene` behaviour: a new object swapped in, atomic failure, refusal for non-polygon layers, only the slot re-composited, and save/load/replace round trips;
      - a fresh-process `load` → `replace_polygons` → `save_png` with none of matplotlib, cartopy, pyproj, shapely, geopandas or pyogrio imported.
    - `tests/helpers.py` gains `lcc_inverse`, `map_geometry` (small consistent LCC canvases with arbitrary fractional axes) and `pixels_to_lonlat`.
  - **`benchmarks/package_qpf.py`** (project environment): builds QPF scenes from the Session 02 prototype layers at k = 4 (default), 1 and 2. Fresh processes load a scene and, per product, read the WPC shapefile (prototype NumPy reader), call `replace_polygons`, swap in a subtitle `TextSlot` with pixels from the prototype's Pillow code (until Session 09), composite, and encode RGBA level 6. It compares all 18 outputs with their Session 01b Cartopy renders using the Session 02 regions, and writes `bench.json` for the report. `benchmarks/report.py` gained package config labels, and its accuracy figure is now one row per configuration (bars for time, dots for pixel differences), because the scatter version became unreadable once the package points clustered next to the prototype's.
- **Decisions/deviations:**
  - **Exact NumPy fill instead of porting the prototype's Pillow fill.** Probing showed that Pillow truncates vertex coordinates and fills every pixel an edge touches, so the prototype was about half a sample too wide on every side and did not implement the §9 rule. The new fill:
    - computes each sample row's edge crossings with the half-open rule;
    - sorts them with `int32` keys (twice as fast as `int64`) and pairs them into runs, clamped to the clip;
    - scatters the runs' ends into a pixel-resolution difference array and takes one prefix sum per record.

    Its cost grows with crossings plus the bounding box at pixel resolution, not with k². It matches the brute force exactly. Crossings are computed as multiply-then-divide, which is exact for representable ties; the first version divided first and failed one tie case in 300.
  - Each record is composited with its coverage directly, not via the checklist's intermediate "bin-index image". This is equivalent for opaque bins and also correct for translucent ones and for partial coverage between neighbouring bins.
  - **Default edge treatment: `supersample: 4`** (was 2 in Session 02). The exact 4× fill costs only 5–15 % more than 2× and leaves 0.000 % of clear-map pixels more than 32 levels off Cartopy (median; worst 0.001 %), against 0.057 % at 2× and 0.386 % at 1×. The format's default stays 1; writers (Session 10) use 4.
- **Verification:**
  - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 12 files) and `uv lock --check` pass.
  - `uv run pytest`: **277 passed, 5 skipped** core-only. **282 passed** with `--extra build`, including the pyproj comparisons (environment restored to core only afterwards). **277 passed, 5 skipped** on Python 3.12.15 with NumPy 2.0.0 and Pillow 10.1.0 (lowest direct dependencies, venv in the session scratchpad).
  - `uvx ruff check --line-length 120 benchmarks/` passes.
  - `uv run python benchmarks/package_qpf.py` → `benchmarks/results/package-qpf-20261009T151524Z/`; no child loaded a forbidden module. `uv run benchmarks/report.py` → `report-20261009T151632Z/`. All six figures were inspected; the accuracy figure was redone and re-inspected.
  - Three earlier benchmark runs and their reports were deleted as superseded: one failed while building scenes, one used a raster subtitle (which re-flattened a static run every product), and one predated the blend and sort optimisations.
- **Evidence (Apple M3 Pro; common basis of the 07b report):**

  | QPF, 2210 × 1848 | Cartopy | Prototype (Pillow 2×) | Package (exact 4×) |
  | --- | --- | --- | --- |
  | Day 1-3, fresh process | 11.93 s | 334 ms (35.7×) | **286 ms (41.7×)** |
  | 18 products, one process | 27.62 s | 3.64 s (7.6×) | **2.52 s (11.0×)** |
  | Per product after the first (median) | 923 ms | 158 ms (5.8×) | **124 ms (7.4×)** |
  | Pixels > 8 / > 32 levels off (all pixels, median) | — | 0.33 % / 0.090 % | **0.11 % / 0.000 %** |

  - **Polygon fill per product** (median over products 2–18; Day 1-7 in brackets): k = 1: 40 ms (118); k = 2: 42 ms (125); k = 4: 45 ms (148). The prototype's 2× took 80 ms median.
  - **Package phases per product at 4×:** read 1.0 ms, polygons 45 ms, text 1.4 ms, composite 12 ms, PNG 63 ms. PNG encoding is now about half of each product.
  - **Cold Day 1-3:** start-up 77 ms, load 12 ms, polygons 89 ms, composite 24 ms (first render; see the handoff).
  - **Regression gate (no regression greater than 10 % per product against the prototype):** passed. The package is 22 % faster per product at its default, and its polygon phase is 1.8× faster than the prototype's at 2× and 2.4× faster than the prototype's at 4×.
- **Remaining/blockers:** none for Session 08b. Recorded for later sessions: the cold first-render cost of the bottom static run (Session 12/16), and PNG encoding as the dominant per-product cost (Session 16 backlog item).
- **Next:** Session 08. Implement `Scene.replace_grid(id, values)` by porting `render_grid`/`grid_colors` from `benchmarks/prototype_runtime.py` per `docs/format.md` §8. Swap in a new `GridSlot` through `evolve`/`_commit`, add a package grid benchmark writing `bench.json`, and regenerate the report.

### 2026-10-09 — Session 08 — completed

- **Work:**
  - **`src/cartostack/grids.py`:**
    - `check_values` requires exactly the slot's `(ny, nx)` shape and a real numeric dtype (integers or floats). Bool, complex and strings raise `GridMismatchError`. Masked arrays mark bad cells, and values are converted to `value_dtype`.
    - `lut_rows` implements Matplotlib's `Normalize` and `Colormap.__call__`.
    - `cell_colors` applies the slot alpha, keeping Matplotlib's all-zero bad colour transparent.
    - `render` does one `uint32` gather per pixel through `index_map + 1`, with row 0 transparent for `−1`.
  - **`Scene.replace_grid(id, values, *, vmin=None, vmax=None, lut=None)`:** validates new styling through `evolve`, renders, and swaps in a new `GridSlot` (atomic; `index_map` shared, not copied). Changing `vmin`, `vmax` or the LUT (including `n_colors`) lists that grid's colorbars in **`Scene.stale_colorbars`** until they are replaced or removed; Session 11 redraws them. `GridMismatchError` is exported.
  - **Latent bug fixed in `layers.evolve`:** it dropped cached encoded bytes only for `pixels`. A runtime LUT or index-map change would therefore have been saved from the old bytes. It now drops the bytes of every changed buffer (`ENCODED_ROLES`). Test added in `tests/test_layers.py`; `tests/test_grids.py` checks the saved norm and LUT bytes.
  - **`docs/format.md` §8:**
    - The exact arithmetic: each step in float64, rounded to `value_dtype`.
    - Bad values take `bad` even when out of range.
    - The all-zero bad colour keeps alpha 0.
    - A new "Runtime changes" rule for `vmin`/`vmax`/LUT.
  - **`tests/test_grids.py`** (35 tests):
    - LUT rows at every boundary (`x == n_colors`, ±1e-6, ±inf, NaN), masks, the alpha and bad-colour rules, and the float64 arithmetic against a float32 shortcut that really differs.
    - `value_dtype` float64, integer input, invalid shapes and dtypes, index-map gathering, and a magnified 2 × 2 grid with a no-data border.
    - **Exact equality with Matplotlib 3.11.2** `cmap(Normalize(vmin, vmax)(v), bytes=True)` for four ranges, including bin edges and their float32 neighbours, NaN, under/over, and alpha 0.8 (build extra).
    - `Scene` behaviour: a new object swapped in, five atomic-failure cases, stale-colorbar tracking, saving the new norm and LUT, only the slot re-composited, refusal for other layers, and a fresh process importing no rendering library.
  - **`benchmarks/package_grid.py`** (project environment): builds the Session 01 grid scene from the prototype layers (`compositor_timing.grid_scene`). It times cold variant-0 runs and loops of variants 0–3 in fresh processes: `make_field` as input, `replace_grid`, the subtitle text slot via the prototype's Pillow code, composite, and PNG RGB level 6. It compares outputs with Session 02's `pcolormesh` references, times in-process renders for 500 × 500 and HRRR-sized grids, and writes `bench.json`. To share code, `run_child` and `to_record` in `package_qpf.py` now take the script and workload. `report.py` labels the package grid configuration.
- **Decisions/deviations:**
  - **Matplotlib's arithmetic, not the prototype's.** The prototype computed in float32 with Python scalars, which matches Matplotlib only for "nice" ranges. Against Matplotlib 3.10.6 on 3M random values per range, it differed for 55 % (0.1–0.7), 69 % (−3.3–0.001), 100 % (250.15–310.7) and 57 % (1e-5–3e-5) of values, and for 0 % with the benchmark's −10–35. Computing each step in float64 and rounding to float32 gave 0 differences for every range. The spec now requires that.
  - "LUT regenerated from a stored colormap definition or supplied by the caller": the format stores LUTs, not colormap definitions, so the caller supplies the new LUT. Regenerating LUTs from named colormaps would need Matplotlib or a colormap table, which is out of scope for the runtime.
  - Values of another float type are converted to `value_dtype` rather than refused. Results match Matplotlib exactly when the input dtype equals `value_dtype`.
- **Verification:**
  - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 13 files) and `uv lock --check` pass.
  - `uv run pytest`: **309 passed, 9 skipped** core-only. **318 passed** with `--extra build` (environment restored to core only afterwards). **309 passed, 9 skipped** on Python 3.12.15 with NumPy 2.0.0 and Pillow 10.1.0.
  - `uvx ruff check --line-length 120 benchmarks/` passes.
  - `uv run python benchmarks/package_grid.py` → `benchmarks/results/package-grid-20261009T153231Z/`; no child loaded a forbidden module. `uv run benchmarks/report.py` → `report-20261009T153248Z/`. Figures inspected.
  - `uv run python benchmarks/compositor_timing.py --reps 3` still runs after the `evolve` change (smoke run; its output was deleted).
- **Evidence (Apple M3 Pro; common basis of the 07b report):**

  | Grid scene, 1200 × 800 | Cartopy | Prototype | Package |
  | --- | --- | --- | --- |
  | Variant 0, fresh process | 16.11 s | 123 ms (131.5×) | **127 ms (127.0×)** |
  | Per product, warm | 269 ms | 33 ms (8.1×) | **26 ms (10.4×)** |
  | Pixels > 8 / > 32 levels off (all pixels, median) | — | 2.31 % / 0.320 % | 2.31 % / 0.320 % |

  - **Against Session 02's `pcolormesh` references:** clear-map pixels differ by more than 8 levels in 2.02 % (median of 4 variants), at most 23 levels. These are Matplotlib's semi-transparent `pcolormesh` cell seams, which an index map does not reproduce; they were expected and are the same as in Session 02. The package's PNGs are **pixel-identical** to the prototype's for all four variants.
  - **Grid render timing** (in-process, median of 20):

    | Grid | Pixels shown | `grids.render` | `replace_grid` |
    | --- | --- | --- | --- |
    | 500 × 500 | 527,904 | 3.1 ms | 3.2 ms |
    | HRRR-sized 1799 × 1059 | 1,910,861 | 19.6 ms | 20.0 ms |

  - **Per product in the loop:** grid 3.3 ms, PNG encoding about 18 ms. Cold is 3 % slower than the prototype (127 vs 123 ms; start-up and first render), and warm products are 21 % faster.
- **Remaining/blockers:** none.
- **Next:** Session 09. Port `render_text` into the package, settle `scene.text[...]`, then switch both package benchmarks to it and verify the core-only milestone.

### 2026-10-09 — Session 09 — completed

- **Work:**
  - **`src/cartostack/text.py`:** text slots laid out with Matplotlib 3.11's single-line rule and rendered with Pillow.
    - Shaping applies GSUB ligatures. Pen positions are hinted advances plus GPOS kerning, each kerning value rounded to 1/64 px as HarfBuzz rounds it.
    - The ink box gives the width from the pen origin. The ascent and descent are at least the font's OS/2 typographic ascender/descender (`hhea` fallback).
    - Origin by `ha`/`va`, then optional `snap` and `offset`.
    - Each glyph is rendered by FreeType at a whole pixel, then shifted by its sub-pixel remainder with bilinear interpolation and composited source-over.
    - The layer holds straight-alpha pixels cropped to the ink and to the canvas. Blank or off-canvas text gives no pixels.
    - A missing or unusable font raises `TextFontError`; another font is never substituted.
  - **`src/cartostack/_opentype.py`** (standard library only): reads `head`, OS/2/`hhea` metrics, `cmap` formats 4 and 12, GSUB `rlig`/`liga`/`clig` ligature lookups (types 4 and 7), and GPOS `kern` pair adjustments (formats 1 and 2, extension type 9), else a format 0 `kern` table. A ligature is kept only when its glyph has a code point, because Pillow renders characters, not glyph ids. Parsing takes 0.5 ms for DejaVu Sans.
  - **`Scene.replace_text(id, value)` and `scene.text`** (a mapping: `scene.text[id]` reads, `scene.text[id] = s` re-renders, `del` raises a `TypeError` that names `remove_layer`). The slot is swapped in through `evolve`/`_commit`, so it is atomic, and only `value`, `left`, `top` and the pixels change. `TextFontError` is exported.
  - **`docs/format.md` §10** now specifies the Matplotlib 3.11 layout (shaping, box, origin, sub-pixel glyphs), records `snap: null` and `offset: [0, 0]` for Matplotlib ≥ 3.11, and documents the measured differences and what is not reproduced. The `docs/examples/` manifests keep the Session 02 prototype's `snap: "round"` and `offset: [0, −1]`, which remain valid values.
  - **`tests/test_text.py`** (84 tests; 36 core-only, 48 Matplotlib comparisons with the build extra). Core tests use Pillow's bundled TrueType font, which has GPOS kerning and ligatures, so they need no extra fixture.
    - Font errors, font tables, kerned pen positions, ligatures drawn as one glyph, `ha`/`va` rules, typographic versus ink metrics, snap and offset, and 0.25/0.5/0.75 px anchor shifts moving the ink by the same fraction (±0.02 px).
    - Colour and alpha, blank and off-canvas text, the one-line rule, and `scene.text` behaviour: atomic failures, round trips, and a broken font raising without fallback.
    - **The core-runtime milestone**: a hand-built `.cstack` with raster, grid, polygon and text slots is loaded in a fresh process. It takes new grid values, polygons and text plus an added layer, writes a PNG, and imports none of matplotlib, cartopy, pyproj, shapely, geopandas or pandas.
    - **Against Matplotlib 3.11.2** (build extra), for DejaVu Sans, Oblique and Bold at 11–58 px with plain, kerned and ligature strings: the shaped glyph ids equal Matplotlib's and pen positions agree within 1/64 px. Over four alignments: centroid within 0.3 px horizontally and 0.01 px vertically, ink amount within 0.2 %, and pixels more than 32 levels off below 40 % (small) or 25 % (large).
  - **Benchmarks:** `package_qpf.py` and `package_grid.py` set the subtitle through `scene.text` (Matplotlib 3.11 settings) instead of the prototype's Pillow code, and report text-region accuracy. `report.py` keeps only the newest native record per series, workload, mode and config.
- **Decisions/deviations:**
  - **Matplotlib 3.11 changed text rendering**, which Session 02's notes assumed was forced auto-hinting with a hinting factor of 8:
    - hinting is now FreeType's default (native hinting, the same as Pillow's);
    - layout is HarfBuzz (kerning and ligatures);
    - minimum line metrics come from the font's OS/2 typographic values, not from `"lp"`;
    - glyphs are drawn at 1/64-px positions without whole-pixel snapping or the old one-pixel lift.

    The prototype's `"lp"` rule plus a −1 px offset matched the QPF subtitle only because 10 + 1 px equalled the 11.0 px typographic descent at that size. The spec and code now follow 3.11. The ink-left rule (Matplotlib measures the width from the pen origin) was found through Oblique `A`, whose ink starts left of the origin.
  - **Remaining differences are anti-aliasing.** Pillow cannot rasterise at fractional positions, so glyphs are rendered at whole pixels and shifted bilinearly. Across the comparison set, a median of 11–14 % of inked pixels differ by more than 32 levels at 30–58 px, and 21–27 % at 11–17 px. Placement is within 0.02 px (worst 0.22 px) horizontally and 0.006 px vertically. Rounding to whole pixels instead gave 30–60 %.
  - **Not reproduced:** contextual alternates, mark positioning, and ligatures without a code point. Raqm (full HarfBuzz shaping in Pillow) needs a system FriBiDi library, which would break the NumPy + Pillow-only runtime.
- **Verification:**
  - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 15 files), `uv lock --check` and `uvx ruff check --line-length 120 benchmarks/` pass.
  - `uv run pytest`: **333 passed, 69 skipped** in the core-only environment (`uv sync --group dev`; matplotlib, cartopy and pyproj not installed), including `test_core_runtime_milestone`. **402 passed** with `--extra build`. **333 passed, 69 skipped** on Python 3.12.15 with NumPy 2.0.0 and Pillow 10.1.0. The environment was restored to core only.
  - `uv run python benchmarks/package_qpf.py` → `package-qpf-20261009T165539Z/`; `uv run python benchmarks/package_grid.py` → `package-grid-20261009T165603Z/`; `uv run benchmarks/report.py` → `report-20261009T165625Z/`. Figures inspected, and the inputs list shows only the newest package runs.
- **Evidence:**

  | Text against the full Cartopy render | Prototype (Session 02) | Package |
  | --- | --- | --- |
  | QPF subtitle, 45.8 px: text-region pixels > 8 / > 32 levels off; max | 4.9 % / 0 %; 24 | **2.65 % / 0 %; 21** |
  | Grid subtitle, 16.7 px: text-region pixels > 32 levels off | 27 % | **8.2 %** |
  | QPF, all pixels > 8 levels off (median) | 0.33 % | **0.069 %** (4×) |
  | Grid, all pixels > 32 levels off (median) | 0.32 % | **0.099 %** |

  | Speed (common basis) | Cartopy | Package |
  | --- | --- | --- |
  | QPF Day 1-3, fresh process | 11.93 s | 291 ms (41.0×) |
  | QPF, 18 products in one process | 27.62 s | 2.48 s (11.1×) |
  | QPF, per product after the first | 923 ms | 123 ms (7.5×) |
  | Grid, fresh process | 16.11 s | 132 ms (121.6×) |
  | Grid, per product (warm) | 269 ms | 28 ms (9.5×) |

  - Text costs 1.1–1.3 ms per product warm (the prototype took 1.1–1.4 ms) and 3–5 ms for the first text in a process (FreeType loading and the glyph cache).
  - The cold grid product is about 5 ms slower than in Session 08 (127 → 132 ms): the first text render plus run-to-run noise.
- **Remaining/blockers:** none. The core-runtime milestone (after 09) is met.
- **Next:** Session 10. Build `cartostack.build.SceneBuilder` (fixed-geometry figures, static-layer capture, index maps by the index-image method, polygon and text slot definitions with the Session 08b and 09 defaults, and the georeference export), then compare an authored and updated file with a full Cartopy render.

### 2026-10-09 — Session 10 — completed

- **Work:**
  - **`cartostack.build`** (the `build` extra; never imported by `import cartostack`):
    - **`SceneBuilder(fig, ax, *, dpi, crop)`** wraps an existing figure, such as the unmodified QPF example's.
    - **`SceneBuilder.new(projection, extent, *, width, height, dpi, axes, …)`** creates one.
    - Every visible *unit* of the figure goes to exactly one layer. Units are the map axes' children, other axes as a whole, and figure-level artists. Assignment is by `artists=`, by the artists a `draw(ax)` callback creates, or by a `zorder=(lo, hi)` band.
    - Slot artists (data previews, the text artist) are hidden from static layers. The figure and map-axes background patches go only to the layer marked `background=True`.
    - Unassigned visible units are an error, unless `build(rest=<layer>)` names a layer for them. Assigning a unit twice and reusing an id are errors too.
    - Layers stacked against Matplotlib's draw order raise a warning only when the misordered artists' rendered pixels actually overlap the other layer; the check renders just those artists.
    - Each static layer is rendered alone with `savefig` at the output DPI and crop, has canonical transparent pixels, and is cropped to its ink.
  - **`build/georef.py`:**
    - `crop="tight"` is resolved once to an explicit box, which Session 02 showed equals `bbox_inches="tight"`. The canvas size is the truncated `savefig` size.
    - The resolved axes rectangle comes from `apply_aspect`.
    - The georeference takes the projection from `proj4_params` plus the pyproj ellipsoid (`a`, `f`; sphere `f = 0`); LCC parameters get their defaults filled in. The projected extent is `ax.get_extent()`, and the affine is fitted from `ax.transData` and checked to be affine at a probe point.
  - **`build/index_map.py`:** the index-image method for 1-D or 2-D lon/lat given as cell `centers` (`shading="nearest"`) or `edges` (`"flat"`). Cells are encoded in 24 bits (at most 16,777,215 cells) with anti-aliasing off. Partly covered pixels, and pixels whose centre is outside the clip region, are −1.
  - **Slot definitions:**
    - Grid: the LUT from the colormap, `vmin`/`vmax` or a linear norm, alpha, extend, cells and `value_dtype`.
    - Polygon: bins as `(lower, upper, colour, order)` with Matplotlib colours converted to RGBA; fallback as `(colour, order)`; `supersample` defaults to 4.
    - Text: from a `Text` artist (anchor via its transform, `size_px = fontsize · dpi / 72`, colour with alpha, alignment, `snap: null`, `offset: [0, 0]`), or a new `fig.text(...)`. The font file is embedded as `fonts/<name>`, and two different fonts with the same file name are refused. Rotated, multi-line and mathtext/usetex text are refused.
    - Optional initial values render the slots' pixels with the runtime itself.
  - The provenance records the authoring tool and its Matplotlib and Cartopy versions.
  - **Packaging bug fixed:** `.gitignore` had `build/`, which also matched `src/cartostack/build/`. That hid the authoring package from git, and Hatchling (which honours `.gitignore`) would have left it out of the wheel and sdist. The rule is now anchored to the root (`/build/`). `uv build` was checked to include all four `cartostack/build` files, and a new test, `tests/test_package.py::test_no_package_source_is_gitignored`, fails if any file under `src/cartostack` is ignored (verified to fail with the old rule).
  - **The build extra now requires Matplotlib ≥ 3.11** (`pyproject.toml`, `uv.lock`), and `SceneBuilder` refuses older versions, because text slots reproduce 3.11's layout. mypy overrides cover Cartopy (no type information) and the build libraries in the core-only environment.
  - **`docs/format.md` §8:** writers SHOULD store LUT channels as `round(c · 255)`.
  - **`tests/test_build.py`** (21 tests, build extra; skipped as a module without it):
    - Geometry: the resolved axes rectangle and the georeference, and a tight crop equal to `savefig(bbox_inches="tight")` at 100 and 300 dpi.
    - **Control points:** 391 lon/lat points map through the stored georeference, through both the NumPy LCC and pyproj with the stored affine, to where Cartopy draws them, within 0.01 px.
    - Layers composite back to the full render: pixels more than 32 levels off are below 0.5 % with an alpha-0.8 grid and below 0.2 % with an opaque one. Layers are cropped and the background is not duplicated.
    - Assignment rules, zorder bands, the draw-order warning and its no-overlap case, and refusal of Matplotlib < 3.11.
    - **Index maps** for centres and edges, 1-D and 2-D: more than 99.5 % of mapped pixels are identical to `pcolormesh` with anti-aliasing off.
    - Polygon and text definitions, refusal of unsupported text, and polygon slots needing a supported projection.
    - **End to end:** author with data A, then update to grid values, polygons and text B in a fresh process that imports no Matplotlib, Cartopy, pyproj or shapely. Compared with Cartopy drawing B, fewer than 0.5 % of pixels are more than 32 levels off and fewer than 2 % more than 8.
  - **`benchmarks/author_scenes.py`** (PEP 723 using the local `cartostack[build]` plus GeoPandas; lockfile) authors **`qpf.cstack`** from `baseline_qpf.build_figure` for Day 1-3, unchanged: zorder bands `below` < 10 ≤ `above`, the polygon slot from `QPF_RANGES`, and the subtitle slot, at 300 dpi with the tight crop. It also authors **`grid.cstack`** from `baseline_cartopy.build_scene` (grid slot from the mesh's cmap and norm; title and colorbar axes in `above`). `package_qpf.py` and `package_grid.py` gained `--scene` to benchmark an authored file.
- **Decisions/deviations:**
  - The LUT is rounded, not truncated. With `cmap(..., bytes=True)` (truncation), most grid pixels were one level off Matplotlib's rendered mesh, because Agg rounds. Rounding, as the Session 02 prototype did, makes the index-map test pixel-exact.
  - Draw-order checking is pixel-based. The first version warned for the QPF subtitle, which shares zorder 1000 with the legend and attribution but overlaps neither; only an overlap of the late-drawn artists now warns.
  - Colorbars stay static rasters in this session (the grid scene's colorbar axes is in `above`). Their own kind is Session 11.
  - Wrapping existing figures is the primary path, so the real example needs no rewriting; `new()` with draw callbacks follows the README sketch.
- **Verification:**
  - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 19 files, in both environments), `uv lock --check` and `uvx ruff check --line-length 120 benchmarks/` pass.
  - `uv run pytest`: **424 passed** with `--extra build`. **334 passed, 70 skipped** core-only. **334 passed, 70 skipped** on Python 3.12.15 with NumPy 2.0.0 and Pillow 10.1.0. The environment was restored to core only.
  - `uv run benchmarks/author_scenes.py` → `authored-20261009T174311Z/`. `package_qpf.py --scene …/qpf.cstack` → `package-qpf-20261009T174405Z/`; `package_grid.py --scene …/grid.cstack` → `package-grid-20261009T174414Z/`; `report.py` → `report-20261009T174512Z/`.
  - Four back-to-back noise-check runs (authored versus assembled QPF scene, 2.56–2.80 s per 18-product loop for both) were deleted so that the report would not pick them up.
- **Evidence:**

  | Authored file | Size | Layers | Build time | Layers vs full render of the same figure |
  | --- | --- | --- | --- | --- |
  | `qpf.cstack`, 2210 × 1848 @ 300 dpi | 1.09 MB | below, qpf, above, subtitle | 11.7 s | > 8 levels 0.043 %, > 32 levels 0 %, max 21 (data slot empty) |
  | `grid.cstack`, 1200 × 800 @ 100 dpi | 1.34 MB | below, temperature, above, subtitle | 16.4 s | > 8 levels 1.85 %, > 32 levels 0.099 %, max 75 (`pcolormesh` seams, text) |

  - **Updated authored files against the full Cartopy renders** of each product are identical in accuracy to the hand-assembled scenes of Sessions 08b–09. QPF, all 18 products: 0.069 % / 0.000 % of pixels more than 8 / 32 levels off; subtitle region 2.65 % / 0 %, max 21. Grid, 4 variants: 2.20 % / 0.099 %.
  - **Speed** (report, common basis): QPF Day 1-3 cold 302 ms (39.5×), 18-product loop 2.65 s (10.4×), 128 ms per product (7.2×); grid cold 137 ms (118×), warm 29 ms (9.1×). Back-to-back runs showed no difference between authored and assembled scenes; the drop from Session 09's figures (2.48 s, 123 ms) is run-to-run variation on this machine (2.56–2.80 s for either).
- **Remaining/blockers:** none.
- **Next:** Session 11. Colorbar layer kind: author it from the colorbar axes, redraw it with NumPy/Pillow when `stale_colorbars` lists it, and run the build-to-runtime milestone comparison.

### 2026-10-09 — Session 11 — completed

- **Work:**
  - **Colorbar redraw data** (`ColorbarRedraw` on `ColorbarLayer`; `docs/format.md` §7; optional `colorbar.redraw` in the manifest and JSON Schema). It holds:
    - `orientation` and `box`, the strip from `vmin` to `vmax`;
    - `rows`, the LUT row of every strip pixel, with under/over for the extension triangles;
    - the `under`, `over` and `label` rasters (the axis label is separate so that it can move);
    - `label_edge`;
    - tick style: locator (`auto` with the resolved `nbins` and `steps`, or `fixed` values), side, direction, length, width and colour;
    - tick-label style: embedded font, size, colour, pad and minus sign.
  - **`src/cartostack/colorbars.py`:**
    - Exact ports of Matplotlib's `MaxNLocator` (`_nonsingular`, `scale_range`, `_Edge_integer`, `_raw_ticks`) and of `ScalarFormatter` (`_compute_offset`, `_set_order_of_magnitude`, `_set_format`, Unicode minus).
    - A range that would need an offset or scientific-notation label raises `ColorbarRedrawError`, and the colorbar stays stale.
    - `render` composites under → strip (recoloured from the slot's LUT and alpha; another `n_colors` re-bands by position) → over → tick marks (exact coverage, odd-width strokes centred on pixel centres) → tick labels (text-slot renderer) → the axis label, shifted by the whole-pixel change in the tick labels' outer edge.
  - **`Scene`:**
    - `replace_grid` redraws, in the same atomic commit, every colorbar of the slot whose `vmin`, `vmax` or LUT changed and that has redraw data. Colorbars without redraw data, or whose new range needs an offset, are listed in `stale_colorbars`.
    - `redraw_colorbar(id)` redraws explicitly.
    - The redraw data loads and saves (`colorbars/<id>/rows.i32.zz`, `under/over/label.rgba8.zz`).
    - `ColorbarRedraw` and `ColorbarRedrawError` are exported.
  - **Text:** `va: "center_baseline"` (Matplotlib's vertical-axis tick labels; baseline at `y + a/2`) in the code, the spec (§10) and the schema.
  - **`SceneBuilder.add_colorbar(id, colorbar, *, slot, order)`:**
    - Renders the colorbar axes as the layer, padded by 4 × the label size so longer labels after a range change fit.
    - Renders `over` with ticks and labels laid out but transparent, so the axis label keeps its place, and `label` alone.
    - Makes `rows` by an index image: each band coloured with its LUT row's code via `grids.lut_rows`, triangles coded under/over, anti-aliasing off.
    - Reads the tick and label style from the axis, and computes `label_edge` with the runtime's own layout.
    - Unsupported locators or formatters keep the colorbar as drawn, with a warning.
  - **Legends:** a legend can be its own static raster layer (`add_static(artists=[legend])`), which the milestone test covers. Raster legends need nothing new at runtime.
  - **`benchmarks/author_scenes.py`:** the grid scene's colorbar is now a colorbar layer, and the script records `replace_grid` timings with and without a new normalisation.
  - **Tests:**
    - `tests/test_colorbars.py` (25 tests; 2 build-extra): hand-computed tick values and labels, offset/scientific refusals, fixed ticks, strip recolouring with alpha, re-banding for a new LUT size, ticks and labels following a new range, stale handling without redraw data or with an offset range, file round trips, size validation, **1,500 random ranges identical to Matplotlib's locator and formatter**, and authored horizontal and vertical colorbars redrawn for 4 ranges and compared with Matplotlib redrawing them.
    - `tests/test_build.py`: **the build-to-runtime milestone test.**
- **Fixes found on the way:**
  - **Lint gap:** with `.gitignore` anchored in Session 10, Ruff (which honours `.gitignore`) checked `src/cartostack/build/` for the first time and found 11 issues: long lines hidden behind `# fmt: skip`, a docstring `×`, and a non-`pairwise` loop. Fixed.
  - **Canvas size:** `georef.canvas_size` now truncates the way Matplotlib's `get_width_height` does, which rounds up within 1e-8 px. A 460 px figure (4.6 in × 100 = 459.99999999999994) had come out 1 px short.
  - **Over raster:** removing ticks with a `NullLocator` let Matplotlib move the axis label into the tick-label space. Ticks and labels are now kept in place but transparent.
  - **Vertical colorbars:** Matplotlib moves the axis label with the widest tick label, by up to 8 px across the tested ranges. It is now a separate raster shifted at runtime, which brings the residual centroid error under 0.04 px.
- **Verification:**
  - `uv run ruff format --check .`, `uv run ruff check .` (now including `cartostack.build`), `uv run mypy` (strict, 20 files, both environments), `uv lock --check` and `uvx ruff check --line-length 120 benchmarks/` pass.
  - `uv run pytest`: **450 passed** with `--extra build`. **356 passed, 73 skipped** core-only. **356 passed, 73 skipped** on Python 3.12.15 with NumPy 2.0.0 and Pillow 10.1.0. The environment was restored to core only.
  - `uv run benchmarks/author_scenes.py` → `authored-20261009T185718Z/`; `package_grid.py --scene …/grid.cstack` → `package-grid-20261009T185754Z/`; `report.py` → `report-20261009T185757Z/`.
- **Evidence:**
  - **Tick and label ports:** 3,000 (development) plus 1,500 (test) random ranges. Every tick value and label is identical to Matplotlib 3.11.2. The 643 of 3,000 refused ranges are exactly those where `ScalarFormatter` shows an offset or a multiplier.
  - **Authored colorbars redrawn against Matplotlib**, horizontal and vertical, for −10–35, −5–30, 0–1 and 250–310:
    - the strip has under 1 % of pixels more than 32 levels off;
    - total ink is within 3 %;
    - the centroids of ticks and labels are within 0.15 px (0.04 px excluding the axis label);
    - the rest is text anti-aliasing at 12.5 px.
  - **Build-to-runtime milestone:** a 640 × 520 map with background, grid (alpha 0.8), polygons, borders, a legend layer, a colorbar, a title and a subtitle. It is authored, then updated in a fresh process without Matplotlib, Cartopy, pyproj or shapely: new grid values with `vmin`/`vmax` changed from −10/35 to −15/40 (the colorbar is redrawn and nothing is stale), new polygons, and a new title and subtitle. Against Cartopy drawing the same updated figure, **0.44 % of pixels differ by more than 32 levels and 2.55 % by more than 8**, from `pcolormesh` seams, polygon edges and text anti-aliasing. The test's tolerances are 0.6 % and 3 %.
  - **Authored grid file with a colorbar layer** (1.35 MB, 5 layers): unchanged accuracy against its full render (more than 32 levels off: 0.099 %). `replace_grid` takes 3.3 ms with the same normalisation and 5.5 ms with a new one, so a colorbar redraw costs about 2.3 ms. Speed in the report: grid cold 134 ms (120×), warm 27 ms (10.0×).
- **Decisions/deviations:**
  - The redraw is stored as parts plus Matplotlib's tick rules, rather than as a generic vector description. This is exact for Matplotlib's default colorbars, and other locators or formatters fall back to a static colorbar.
  - The runtime refuses offset/scientific labels instead of approximating them.
  - The axis label moves in whole pixels.
- **Remaining/blockers:** none. The build-to-runtime milestone (after 11) is met.
- **Next:** Session 12. Measure load and decode as a share of a cold product from the current report and decide on lazy decoding. Define archive lifetime and close behaviour, and cache encoded bytes at save (colorbar redraw members included).

### 2026-10-09 — Session 12 — completed

- **Lazy decoding: measured, not adopted (no-change decision).**
  - From report `report-20261009T193920Z/` (authored files), the load step, which includes decoding every layer, takes 13.1 of 318 ms (4.1 %) for a cold QPF product, 7.4 of 133 ms (5.6 %) for a cold grid product, and 0.5–3.3 % in the loops. All are below the 10 % gate.
  - Profiling `Scene.load` on the QPF file in a warm process (8.3 ms) shows zlib decompression at 6.7 ms (the same work as the prototype), buffer ownership copies at 0.7 ms, and SHA-256 plus manifest validation under 1 ms. The rest of the 13 ms cold figure is one-time start-up of the load path.
  - Lazy decoding stays in the extension backlog, whose row now records these numbers. The Session 13 checkbox about lazy/eager guarantees is marked not applicable.
- **Work:**
  - **Archive lifetime** is defined (the `Scene` docstring, `Scene.close`, `docs/format.md` §2): `Scene.load` reads, verifies and decodes everything and closes the file before returning. A scene holds no open file and stays usable after `close()` and after its file is moved, deleted or replaced, including by saving over it. `close()` and the context manager release nothing; they exist so code keeps working if a later version loads lazily.
  - **Save-time encode cache** (`scene._ENCODE_CACHE`): encoded bytes are kept per read-only buffer, by identity, while the buffer lives. Owned buffers are immutable, so a save re-encodes only buffers that are new since they were loaded or last saved. This covers layer pixels, index maps, LUTs and colorbar redraw members, closing the Session 07 deferral. Entries are removed by a weakref callback when their buffer is freed.
  - **Compositor first-render fast path** (the cold cost noted in Session 08b): when the bottom layer covers the whole canvas at full opacity, the canvas starts from its pixels, with fully transparent pixels zeroed, instead of compositing it onto a transparent canvas. The result is identical to `alpha_composite`, which a test checks. The first render of the authored QPF file in a fresh process fell from 17.7 to 13.0 ms (median of 7).
  - **Pre-flattened static runs:** no new file member. `SceneBuilder` already writes each static run, whether a zorder band or an artist group, as one raster, as Session 02 decided, and the compositor caches multi-layer runs at runtime. A stored flattened copy would only duplicate pixels.
  - **Tests:**
    - `tests/test_lifecycle.py` (6): no file descriptors left open by five loads; scenes usable after `close()` and after their file is deleted; saving over the source file; repeated saves encode nothing new, then exactly the replaced pixels once, then the new LUT and grid pixels once, with byte-identical repeat saves; another encoding is encoded once; cache entries go away with their buffers.
    - `tests/test_compositor.py`: the fast path's exactness with transparent pixels that carry colour, at full and half opacity, at the origin and offset; the flattening spy now expects no `_over` for a full-canvas bottom layer.
- **Verification:**
  - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 20 files, both environments), `uv lock --check` and `uvx ruff check --line-length 120 benchmarks/` pass.
  - `uv run pytest`: **366 passed, 73 skipped** core-only. **460 passed** with `--extra build`. **366 passed, 73 skipped** on Python 3.12.15 with NumPy 2.0.0 and Pillow 10.1.0. The environment was restored to core only.
  - `package_qpf.py --scene` → `package-qpf-20261009T193907Z/`; `package_grid.py --scene` → `package-grid-20261009T193917Z/`; `report.py` → `report-20261009T193920Z/`.
- **Evidence (cold-process load versus Session 02, medians):**

  | | Prototype (Session 02) | Package (Session 12) |
  | --- | --- | --- |
  | QPF load | 9.3 ms (2.8 % of 334 ms) | 13.1 ms (4.1 % of 318 ms) |
  | Grid load | 5.6 ms (4.6 % of 123 ms) | 7.4 ms (5.6 % of 133 ms) |

  Speed in the report: QPF 37.5× cold (318 ms), 10.7× for the 18-product loop (2.58 s) and 7.5× per product (123 ms); grid 121× cold (133 ms) and 10.1× warm (27 ms). Cold QPF varies between 286 and 318 ms from run to run.
- **Remaining/blockers:** none.
- **Next:** Session 13. Write and run the fresh-environment procedure: author with the build extra, then in a core-only venv outside the source tree, with sources moved away and the network blocked, load, update, render and compare.

### 2026-10-09 — Session 13 — completed

- **Work:**
  - **`scripts/check_portability.py`** (standard library only; runs each stage in its own process; procedure in `CONTRIBUTING.md` → "Environment checks"):
    1. Authors the QPF and grid scenes with the build extra, then exports the runtime inputs as plain NumPy files.
    2. Builds the wheel and installs it into a new core-only venv outside the repository.
    3. Renames `benchmarks/data` away, always restoring it, and points `HOME` into the sandbox so the Cartopy Natural Earth cache is unreachable without being touched.
    4. Runs the runtime with `python -I` and an audit hook that logs every opened file and refuses network calls: all 18 QPF products, 4 grid variants, a normalisation change with colorbar redraw, an added layer, then save, reload and re-render.
    5. Compares the PNGs with the development environment and with the full Cartopy renders.
  - **`tests/test_portability.py`** (2): the same no-outside-files and no-network rule in a fresh process on every test run. Its negative control shows the hook flags both a stray read of `README.md` and a connection attempt.
  - **Pre-check:** the audit hook was also checked by hand against the core venv. With a stray read and a socket connect added, it reported the stray read as outside the allowed roots and logged `socket.getaddrinfo` as a refused network call.
  - **Lazy decoding:** not applicable, as Session 12 kept eager loading. The no-open-file guarantee is in `tests/test_lifecycle.py`.
- **Verification:**
  - **Full procedure:** `uv run scripts/check_portability.py` produced `authored-20261009T194750Z/` and `portability-20261009T194750Z/`.
    - The core venv ran Python 3.13.4 with only `cartostack`, `numpy` 2.5.3 and `pillow` 12.3.0 installed.
    - All 8 checks pass: no build library installed or imported (including pyproj and shapely); no file opened outside the sandbox, the venv and the Python installation (498 files opened); no network attempt; cartostack imported from the venv; reload identical; no stale colorbar; all 25 PNGs pixel-identical to the development environment.
    - Against full Cartopy renders: QPF median over the 18 products is 0.069 % of pixels > 8 and 0.000 % > 32; grid median over the 4 variants is 2.20 % > 8 and 0.099 % > 32. The QPF figures match Session 12.
    - `benchmarks/data` was restored afterwards.
  - **Checks:** `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 20 files, both environments), `uv lock --check` and `uvx ruff check --line-length 120 benchmarks/` pass.
  - **Tests:**
    - core-only: 368 passed, 73 skipped
    - with `--extra build`: 462 passed
    - lowest allowed dependencies (Python 3.12.15, NumPy 2.0.0, Pillow 10.1.0): 368 passed, 73 skipped
    - `uv run --isolated --python 3.12` and `--python 3.14`: 368 passed, 73 skipped
    - The environment was restored to core only.
- **Remaining/blockers:** none.
- **Next:** Session 14, end-to-end examples and the public API documentation.

### 2026-10-09 — Session 14 — completed

- **Examples (`examples/`, guide in `examples/README.md`):**
  - **`author_grid.py`** (build extra): a standalone New York State temperature map drawn with `SceneBuilder.new` and local Natural Earth shapefiles. It writes `grid.cstack` with five layers: below, a `temperature` grid slot, above, a redrawable `colorbar` and a `subtitle` text slot. It also writes 4 sample `.npy` inputs.
  - **`author_qpf.py`** (PEP 723, adds GeoPandas): wraps the unchanged figure from `benchmarks/baseline_qpf.py`, the port of the production script, which is the primary authoring path.
  - **`update_grid.py`** (runtime only): cron-style; loads once, then replaces the grid and subtitle per input and writes a PNG. `--vmin`/`--vmax` change the range and redraw the colorbar.
  - **`update_qpf.py`** (runtime only): renders all WPC QPF product directories in one process.
  - **`wpc_qpf.py`**: a shapefile and dBASE reader that uses only the standard library and NumPy.
  - Both authoring scripts create the output's parent folder, and `update_qpf.py` adds its own folder to `sys.path`, so they work with the documented `out/...` paths and under `python -I`. Both were bugs found while verifying.
  - `/out/` is gitignored for example output.
- **Documentation:**
  - **`README.md`** was replaced by a short user-facing page: purpose, the speed figure (`docs/images/speedup_summary.png`, copied from `report-20261009T193920Z`) with the report's numbers, installation of the core runtime versus the `build` extra, a two-step quickstart, what a file can hold, and a table of the documents.
  - **`docs/design.md`** holds the full former README (1,886 lines) unchanged, under a header saying where the implemented API is documented; its relative links were fixed.
  - **`docs/api.md`** (new) covers installation, the runtime `Scene` (loading, saving and lifetime; data slots; layer editing; output; layer types; errors), `SceneBuilder`, embedded fonts and licensing, and the format 1.0 limitations.
  - `CONTRIBUTING.md`, the `SESSIONS.md` preamble and workflow, the development commands (examples added) and `CLAUDE.md` (repository state rewritten for the implemented package; README references now point to `docs/design.md`) are all reconciled.
- **Fix found while documenting limitations:** `SceneBuilder.add_grid_slot(norm=...)` used only `norm.vmin`/`vmax`. A `LogNorm`, `BoundaryNorm`, `TwoSlopeNorm` or `Normalize(clip=True)` would therefore have been stored as a linear range and given wrong colours without any error. It now raises `BuildError` unless the norm is exactly a `Normalize` without clip. The new test `test_grid_slots_refuse_norms_they_cannot_reproduce` covers all four cases plus the accepted case.
- **Tests:** `tests/test_examples.py` (2). It runs `update_grid.py` and `update_qpf.py` in fresh processes, as `python examples/<script>` would. Each must import none of Matplotlib, Cartopy, pyproj, shapely, GeoPandas or pyogrio, and every PNG must be pixel-identical to the same updates made in process. The grid test includes a `--vmin`/`--vmax` change; the QPF test (fixtures marker) uses two real WPC products. The test scene embeds Pillow's bundled font.
- **Verification:**
  - **Examples from the source tree:**
    - `author_grid.py` produces a 1200×900, 5-layer file. `author_qpf.py` produces a 2210×1848 file with layers below, qpf, above and subtitle.
    - `update_qpf.py` renders the 18 products in 2.45 s. All 18 PNGs are pixel-identical to the Session 13 core-environment renders in `portability-20261009T194750Z/out-core`.
    - `update_grid.py` renders 4 inputs in 0.12 s, and a new 0–40 °C range with the colorbar redrawn. I inspected the rendered grid and QPF maps by eye.
  - **Examples from an installed wheel:**
    - The wheel came from `uv build --wheel`. Authoring used a venv with `cartostack[build]` from the wheel; the updates used a core-only venv holding exactly `cartostack`, `numpy` 2.5.3 and `pillow` 12.3.0, in which none of Matplotlib, Cartopy, pyproj, shapely or GeoPandas can be found.
    - Everything ran from a sandbox outside the repository with `python -I` and `env -i`, and `cartostack` was imported from the venv's site-packages.
    - `author_grid.py` wrote the file, then `update_grid.py` (4 products in 0.32 s) and `update_qpf.py` (18 products in 2.47 s) ran in the core venv. All 22 PNGs are pixel-identical to the source-tree run.
  - **Checks:**
    - `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 20 files), `uv lock --check` and `uvx ruff check --line-length 120 benchmarks/` pass.
    - `uv run --extra build mypy --strict examples --ignore-missing-imports` passes, after fixing 3 annotation gaps.
    - All relative links in `README.md`, `docs/api.md`, `docs/design.md`, `examples/README.md` and `CONTRIBUTING.md` resolve.
  - **Tests:**
    - with `--extra build`: 468 passed
    - core-only: 370 passed, 73 skipped
    - lowest allowed dependencies (Python 3.12.15, NumPy 2.0.0, Pillow 10.1.0): 370 passed, 73 skipped
    - `uv run --isolated --python 3.12` and `--python 3.14`: 370 passed, 73 skipped
    - The environment was restored to core only.
- **Remaining/blockers:** none for Session 14. Two items for Session 15: the `[project.urls]` repository URL still names `GeoScene`, and the README image needs an absolute URL or packaging to render on PyPI.
- **Next:** Session 15, distribution validation and v0.1 readiness.

### 2026-10-09 — Session 15 — completed

- **Verdict: v0.1 is release-ready, pending maintainer actions.**
  - Sessions 00–14 are all `completed`. The artifacts build and pass `twine check`. Tests pass from the sdist against the installed wheel on every supported Python, core-only and with the build extra, at both the latest and the lowest allowed dependencies. The examples run from the installed wheel outside the repository, on macOS and in Linux containers.
  - Remaining actions are in `CONTRIBUTING.md` → "Releasing": commit, merge to `main` and push (the PyPI description's image and links point at `main`, which does not have them yet); set the version (still `0.0.1.dev0`) and classifier; publish to TestPyPI, then PyPI; tag. **Publishing requires a separate user request; nothing was committed or published.**
- **Fixes and changes:**
  - **Build-extra floors raised to what was verified:**
    - `matplotlib>=3.11.1`: with 3.11.0, `test_authored_colorbar_redraw_matches_matplotlib[horizontal|vertical]` fail, because 3.11.0 draws colorbars differently from what the runtime redraws.
    - `cartopy>=0.26`: with 0.25.0, `test_control_points_match_cartopy_and_pyproj` (up to 13 px), the fresh-process authored-file test and `test_build_to_runtime_milestone` fail; one build gave `rendered 600x459 px, expected 600x460`.
    - `SceneBuilder` now checks major.minor.patch (it compared only major.minor) and refuses 3.11.0, including release candidates. New tests: `test_matplotlib_below_the_verified_minimum_is_refused` (3) and `test_matplotlib_from_the_verified_minimum_is_accepted` (3, including dev versions).
    - The Cartopy 0.25 root cause was not investigated: no version before 0.26 had ever been tested.
  - **`pyproject.toml`:**
    - `[project.urls]` now points to `dvanhoesen/CartoStack` (it named `GeoScene`), plus Documentation and Format-specification links.
    - The sdist now includes `examples/`, which `tests/test_examples.py` needs; without it, tests run from the sdist would have failed. It also includes `docs/api.md`, `docs/format.md`, `docs/examples/`, `docs/images/` and `CONTRIBUTING.md`.
    - The PyPI description is generated by `hatch-fancy-pypi-readme`, a new build requirement. It makes the README's relative links absolute and points the image at `raw.githubusercontent.com/.../main/`.
    - `uv lock` was updated.
  - **Determinism wording** (`docs/format.md` §2, `docs/api.md`, `README.md`): saves are byte-identical for a given zlib. Linux (zlib 1.3.1) and macOS (zlib 1.2.12) give different PNG bytes but identical pixels.
  - **`package_qpf.py`/`package_grid.py`** record which `cartostack` ran (`package_origin()`: source tree or installed package, with version, file and interpreter) in `results.json` and the `bench.json` notes, and document running them with an installed wheel.
  - **`README.md`:** speed figure and table from the release report, a run-to-run variance note, and a status line.
  - **`CONTRIBUTING.md`:** the lowest-build-extra environment check and a new "Releasing" section.
- **Verification:**
  - **Artifacts** (`uv build`; the wheel is built from the sdist):
    - `uvx twine check`: both PASSED.
    - The wheel holds 26 files: `cartostack/` with `build/`, `schemas/manifest-1.schema.json` and `py.typed`, plus dist-info and LICENSE.
    - Metadata: Requires-Python `>=3.12`; `numpy>=2.0`, `pillow>=10.1`; extra `build` (`matplotlib>=3.11.1`, `cartopy>=0.26`); License-Expression MIT; `text/markdown` with no relative links left.
    - The sdist has no `benchmarks/`, `resources/`, `.cstack` files or `CLAUDE.md`.
  - **Matrix** (fresh venvs; the wheel installed; tests run from the unpacked sdist with `CARTOSTACK_FIXTURE_DIR`, so they import site-packages, not `src/`):
    - core on Python 3.12.15, 3.13.4 and 3.14.8 (NumPy 2.5.3, Pillow 12.3.0): 369 passed, 74 skipped each
    - `build` extra on 3.12, 3.13 and 3.14 (Matplotlib 3.11.2, Cartopy 0.26.0): 473 passed, 1 skipped each
    - lowest core on 3.12 (NumPy 2.0.2, Pillow 10.1.0): 369 passed, 74 skipped
    - lowest build on 3.12 (plus Matplotlib 3.11.1, Cartopy 0.26.0): 473 passed, 1 skipped
    - The one extra skip in every sdist run is the `.gitignore` regression test, which needs a git checkout.
  - **Examples from the installed wheel outside the repository:**
    - `author_grid.py` (build venv), then `update_grid.py` (4 products in 0.11 s) and `update_qpf.py` (18 products in 2.5 s) in core venvs on 3.12 and 3.14, with `python -I` and `env -i`.
    - All 22 PNGs are identical between 3.12 and 3.14 and to the Session 14 run.
    - Authored with Matplotlib 3.11.1 and updated with NumPy 2.0.2 and Pillow 10.1.0, the 4 grid PNGs are identical to the latest-version run.
  - **Linux smoke test:** Docker via Colima, started for this check and stopped afterwards, Linux 6.8 aarch64 with glibc 2.41. In `python:3.12-slim` and `python:3.14-slim` with the wheel and fixtures mounted read-only: 369 passed, 74 skipped; the examples ran (18 QPF products in 3.8–4.2 s in the VM). All 22 PNGs are pixel-identical to macOS; the bytes differ only because of zlib. Staging and logs are in `benchmarks/results/linux-smoke-20261009T205407Z/`.
  - **Release benchmark from the installed wheel** (3.13 core venv, authored files `authored-20261009T194750Z`): `package-qpf-20261009T205553Z`, `package-grid-20261009T205602Z` and `report-20261009T205609Z`.
    - QPF: 40.6× cold (11.93 s → 294 ms), 10.6× for the 18-product loop (27.62 s → 2.61 s), 7.2× per product (923 → 128 ms); accuracy unchanged (0.069 % of pixels > 8, 0.000 % > 32).
    - Grid: 112× cold (16.11 s → 144 ms), 9.6× warm (269 → 28 ms).
    - These are within run-to-run variation of Session 12 (QPF cold 286–318 ms).
    - The report's reconciliation prints one MISMATCH, which predates this session (it is in `report-20261009T193920Z` too): the Session 02 log quotes 0.156 s for the prototype QPF per-product median of 0.1567 s. The historical log entry is left as written.
  - **Distribution names:** `https://pypi.org/pypi/cartostack/json` returned 404 (unregistered), as did TestPyPI and `carto-stack`. `github.com/dvanhoesen/CartoStack` is public (200). The README image URL on `main` returns 404 until the work is pushed to `main`.
  - **Repository checks:** `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` (strict, 20 files), `uv lock --check` and `uvx ruff check --line-length 120 benchmarks/` pass. Project environment: 474 passed with `--extra build`, then restored to core: 370 passed, 73 skipped.
- **Unverified or unsupported environments (explicit):**
  - Windows (any), Linux x86_64 and macOS x86_64: no machine available.
  - Linux was tested on arm64 only, and without the build extra.
  - NumPy 2.0 and Pillow 10.1 on Python 3.14: neither publishes 3.14 wheels and the source builds failed, so on 3.14 the effective minimum is the oldest version with 3.14 wheels; pip picks it automatically. Not encoded as markers.
  - Cartopy < 0.26 and Matplotlib < 3.11.1: unsupported (floors raised).
- **Remaining/blockers:** none for Session 15. Release actions await the user.
- **Next:** Session 16, workload profiling and the next extension decision. Or, at the user's request, the release steps in `CONTRIBUTING.md` → "Releasing".
