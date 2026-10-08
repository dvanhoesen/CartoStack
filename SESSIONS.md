# CartoStack build sessions

This is the implementation checklist and handoff record for building CartoStack one session at a time. `README.md` remains the architectural proposal; this file records what has actually been built and verified. Proposed paths, APIs, and commands below become authoritative only when their session implements them.

**Objective.** CartoStack is a layered map file (`.cstack`): N×M RGBA layers plus metadata describing each layer, the canvas, and how geographic data maps to pixels. A lightweight runtime that needs only NumPy and Pillow loads the file, swaps in or adds only the new data (grid values, classified polygons, text), stacks the layers, and writes the image, much faster than building the map with Matplotlib and Cartopy. Matplotlib and Cartopy are build-time authoring tools; they are never imported on the runtime path. Performance comes from the file design and the small runtime, not from caching inside a Matplotlib workflow.

## Current handoff

- Last updated: 2026-10-08.
- Active session: none.
- Last completed session: **04 — `.cstack` format specification v1** (2026-10-08).
- Next implementation session: **05 — Core reader/writer and layer model**.
- Repository implementation state: installable `cartostack` package skeleton (`pyproject.toml`, `uv.lock`, `src/cartostack/` with `errors.py`, `geometry.py`, `manifest.py` (format 1.0 validation, plain Python) and `schemas/manifest-1.schema.json`, `docs/format.md` and `docs/examples/`, `tests/`, `.github/workflows/ci.yml`, `CONTRIBUTING.md`); no reader/writer or rendering modules yet. Shared fixture script `scripts/fetch_fixtures.py`. Configured standalone benchmarks (PEP 723, each with a `.lock` file): `baseline_cartopy.py` (synthetic grid), `baseline_qpf.py` (QPF), and the Session 02 prototype `prototype_build.py`, `prototype_runtime.py` (NumPy + Pillow only), `prototype_compare.py`. Reference results (gitignored): `benchmarks/results/20261008T145213Z/` (grid), `qpf-20261008T152044Z/` (QPF), `prototype-20261008T155139Z/` (prototype files, runtime and comparison).
- Before starting Session 05: build the reader/writer on `cartostack.manifest.parse_manifest` and the encodings in `docs/format.md` §2 and §6.3 (`mimetype` first, ZIP_STORED, member size and SHA-256 checks, atomic writes, preserving unknown fields). CI has still not run on GitHub; it runs on the first push (needs the user's approval). Still undecided: whether `resources/` is committed.

## How to execute and update a session

1. Read this handoff, the requested session, its prerequisites, and the relevant README sections. Implement one numbered session per request unless the user explicitly requests more.
2. Before implementation, change that session's index status to `in_progress`, update the active session above, and check that its prerequisites are `completed`. Have at most one active session.
3. Work through its checkboxes. Check a box only when its deliverable or behavior exists and its relevant verification passes. Add meaningful tests alongside implementation; later integration sessions do not postpone earlier testing.
4. Run the session's verification. Record the exact commands and outcomes, including visual artifacts, measurements, or environment prerequisites where relevant. A skipped or failed required check does not count as passing.
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
| [03](#session-03) | Python package, tooling, and initial CI | 02 | completed |
| [04](#session-04) | `.cstack` format specification v1 | 03 | completed |
| [05](#session-05) | Core reader/writer and layer model | 04 | planned |
| [06](#session-06) | Compositor, cropped placement, and PNG output | 05 | planned |
| [07](#session-07) | Layer editing API | 06 | planned |
| [08](#session-08) | Grid slots: values to pixels with NumPy | 07 | planned |
| [08b](#session-08b) | Polygon slots: classified polygons to pixels with NumPy/Pillow | 08 | planned |
| [09](#session-09) | Text slots with Pillow | 08b | planned |
| [10](#session-10) | Build-side authoring from Matplotlib/Cartopy | 09 | planned |
| [11](#session-11) | Colorbars and legends | 10 | planned |
| [12](#session-12) | Lazy loading and layer storage performance | 11 | planned |
| [13](#session-13) | Core-only runtime and source-independence verification | 12 | planned |
| [14](#session-14) | End-to-end examples and public API documentation | 13 | planned |
| [15](#session-15) | Distribution validation and v0.1 readiness | 14 | planned |
| [16](#session-16) | Workload profiling and next extension decision | 15 | planned |

Milestones:

- **After 02:** measured proof that a file-based, core-only runtime beats a full Cartopy render by enough to justify the package.
- **After 09:** working core runtime: load a hand-built `.cstack`, replace grid values, classified polygons, and text, add a layer, composite, and write PNG using only NumPy and Pillow.
- **After 11:** build-to-runtime round trip: a file authored from Matplotlib/Cartopy renders, after updates, to match a full Cartopy render of the same inputs.
- **After 13:** portability verified in fresh environments without source data or rendering libraries.
- **After 15:** v0.1 release candidate.

## Target v0.1 package

Sessions 03–15 produce one installable `cartostack` package. Method names are placeholders until Sessions 04 (format) and 07 (editing API) fix them.

```python
# Build machine: pip install cartostack[build]  (Matplotlib + Cartopy)
from cartostack.build import SceneBuilder

builder = SceneBuilder(geometry)
builder.add_static("land_ocean", draw=draw_land_ocean, order=10)
builder.add_grid_slot("temperature", lon=lon, lat=lat, order=20, cmap="coolwarm", vmin=-30, vmax=40)
builder.add_polygon_slot("qpf", bins=qpf_bins, order=25)   # classified polygons, e.g. WPC QPF
builder.add_static("borders", draw=draw_borders, order=30)
builder.add_colorbar("cbar", grid="temperature", order=95)
builder.add_text_slot("title", text="", position=..., font="DejaVuSans.ttf", size=16, order=90)
builder.save("northeast.cstack")

# Runtime worker: pip install cartostack  (NumPy + Pillow only)
from cartostack import Scene

with Scene.load("northeast.cstack") as scene:
    scene.replace_grid("temperature", t2m)
    scene.replace_polygons("qpf", rings, values)   # lon/lat rings, one value per polygon
    scene.text["title"] = "2-m Temperature"
    scene.add_layer("logo", logo_rgba, order=93, x=1100, y=740)
    scene.save_png("t2m.png")
```

Guarantees checked by tests: the runtime path imports neither Matplotlib nor Cartopy (checked through `sys.modules` in a fresh process), opens no source geospatial files, decodes no layer it does not need, and produces output matching a full Cartopy render of the same inputs within documented tolerances. Polygon slots project lon/lat with a NumPy implementation of the stored projection (Lambert Conformal Conic first; decided 2026-10-08), so the runtime needs no pyproj. Ad hoc runtime overlays (stations, warning outlines) outside declared slots remain a post-v0.1 extension. A file may declare any number of data slots of either kind, including just one.

## Environment (observed 2026-10-07)

Recorded so sessions do not re-derive it; re-check before relying on it.

- Machine: Apple M3 Pro, macOS (Darwin 25.6). Git remote `origin` is GitHub (`dvanhoesen/GeoScene`, repository name unchanged), so Session 03 CI targets GitHub Actions.
- `uv`: installed 2026-10-08 — `uv 0.12.23 (Homebrew 2026-10-03 aarch64-apple-darwin)` at `/opt/homebrew/bin/uv`.
- Existing interpreters: Anaconda Python 3.11.8 (`cartopy 0.25.0`, `matplotlib 3.10.6`, `numpy 2.3.0`, `Pillow 11.3.0`, `pyproj 3.6.1`, `shapely 2.1.1`, `pytest 7.4.0`, `mypy 1.8.0`) and Homebrew Python 3.13. Use uv-managed environments, not the Anaconda base environment, for recorded results.
- Cartopy Natural Earth cache (`~/.local/share/cartopy/shapefiles/natural_earth/`): 10m/50m/110m land, ocean, lakes, coastline, rivers, `admin_1_states_provinces_lakes`, `admin_0_boundary_lines_land`. **Missing:** `ne_10m_populated_places` (city labels).
- County fixture (added by the user 2026-10-08): `resources/NYS_Shoreline_Counties/` — "NYS County Boundaries – Shoreline Version" (NYS ITS GIS Program Office, published January 2024; counties clipped to major shorelines). Polygon shapefile, 62 records, WGS 84 geographic (`.prj`), UTF-8 (`.cpg`), bbox `[-79.762, 40.4961, -71.8561, 45.0129]`, 306,418 vertices (high detail, a realistic stress case). Fields include `NAME`, `ABBREV`, `FIPS_CODE`, `POP2020`. The base name contains a space (`NYS Counties.*`); quote paths. QGIS metadata is in `NYS Counties.qmd`. Licence/redistribution terms not yet recorded; decide whether `resources/` is committed or gitignored before Session 01 commits anything. SHA-256:
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

- [ ] Implement layer objects for the v1 kinds with owned `uint8` RGBA buffers; snapshot caller arrays so later mutation cannot alter a layer.
- [ ] Validate dtype, shape, placement, and geometry compatibility on creation and load.
- [ ] Implement eager load and save for each supported layer encoding, writing atomically (temporary file then rename) so a failed save never corrupts an existing file.
- [ ] Return actionable errors for missing members, checksum mismatches, unsupported versions, and corrupt data.
- [ ] Test save → load → save byte-stable manifests and pixel-identical layers.

**Verification:** Round-trip tests, corrupted/invalid-file tests, and a check that load and save run with only core dependencies installed.

### Session 06

**Goal:** Combine layers into the final image correctly and quickly.

**Deliverables:** `cartostack/compositor.py`, cropped-layer placement, `Scene.render()` returning RGBA, and `Scene.save_png()`.

- [ ] Implement source-over composition in the documented straight-alpha convention (Pillow `alpha_composite` as the reference), honouring order, visibility, and uniform opacity.
- [ ] Place cropped layers at integer offsets, including layers partially outside the canvas; keep map clipping distinct from canvas placement clipping.
- [ ] Flatten contiguous runs of unchanged layers and reuse them across renders; verify output equals per-layer composition.
- [ ] Encode PNG (RGB or RGBA, configurable `compress_level`) without changing canvas dimensions.
- [ ] Test transparent/partial-alpha/overlapping fixtures against independently computed expected pixels, and cropped versus full-canvas equivalence.

**Verification:** Exact or explicitly bounded numeric comparisons, PNG decode comparisons, and composite timing for the Session 01 scene.

### Session 07

**Goal:** Edit a loaded scene: replace, add, remove, show/hide, reorder, and save.

**Deliverables:** Public editing API on `Scene`, error types, and lifecycle tests.

- [ ] Implement `replace_layer`/`scene[id] = rgba`, `add_layer`, `remove_layer`, `visible`, `opacity`, and order changes, validating against the file's geometry.
- [ ] Make replacements atomic so a failed replacement leaves the previous layer usable.
- [ ] Ensure edits never decode, modify, or rewrite untouched layers; saving a modified scene to a new path leaves the source file (the template) unchanged.
- [ ] Reject operations that need information the file does not hold (for example restyling a raster's line width) with a clear error.
- [ ] Settle public method names and record them in the [target v0.1 package](#target-v01-package) example.

**Verification:** Lifecycle tests with decode/encode spies, atomic-failure tests, and template-immutability tests.

### Session 08

**Goal:** Render new values for a grid slot with NumPy only.

**Deliverables:** Grid slot implementation, `replace_grid()`, colormap LUT handling, and correctness tests.

- [ ] Render values via index-map gather → normalize → LUT → layer alpha, with NaN/masked values and out-of-range values following the slot's documented rules.
- [ ] Validate value shape and dtype against the slot; raise a grid-mismatch error otherwise.
- [ ] Allow changing `vmin`/`vmax` and colormap at runtime (LUT regenerated from a stored colormap definition or supplied by the caller) and mark dependent colorbars stale for Session 11.
- [ ] Test against analytic fixtures with hand-computed expected pixels, including masks and domain edges.
- [ ] Compare against the Session 02 `pcolormesh` reference and record the difference (expected: equal away from anti-aliased domain edges).

**Verification:** Analytic and masked-grid tests, reference comparison, and timing of the grid render for 500 × 500 and 1799 × 1059 (HRRR-sized) grids.

### Session 08b

**Goal:** Render new classified polygons (e.g. WPC QPF) with NumPy and Pillow only.

**Deliverables:** `cartostack/projection.py` (NumPy LCC forward), polygon slot implementation, `replace_polygons()`, and tests.

- [ ] Implement ellipsoidal Lambert Conformal Conic forward projection in NumPy; test against pyproj to ≤ 1 mm in the `build`-extra job.
- [ ] Map projected rings to pixels via the stored affine transform; fill in draw order with holes into a bin-index image; clip to the map area; colour via the bin LUT.
- [ ] Accept rings plus one value per polygon (and a helper for shapely/GeoJSON-like input without importing shapely); validate inputs and reject values with no bin unless an out-of-range colour is defined.
- [ ] Choose the default edge treatment (none vs supersampling) from Session 02 measurements; quantify differences against Cartopy renders of the same polygons.
- [ ] Test analytic polygons (squares, holes, overlaps, polygons crossing the map edge) with hand-computed expected pixels.

**Verification:** Projection accuracy tests, analytic rasterization tests, QPF comparison against the Session 01b baseline, and per-product timing at 2210 × 1848.

### Session 09

**Goal:** Replace text with Pillow so runtime text needs no Matplotlib.

**Deliverables:** Text slot implementation, `scene.text[...]`, embedded-font handling, and text tests.

- [ ] Render text with Pillow/FreeType from the embedded font at the stored size, color, anchor, and alignment; place it as a cropped layer.
- [ ] Replace one text slot without touching other layers or moving any geometry.
- [ ] Error clearly when a text slot has no usable font; never fall back silently to a different font.
- [ ] Compare Pillow output with Matplotlib text for the same font/size/anchor; document the offset/weight differences and adjust anchor conversion to minimize them.
- [ ] Verify the core-runtime milestone: a hand-built `.cstack` loads, takes new grid values, new polygons, new text, and an added layer, and writes PNG with only NumPy and Pillow installed.

**Verification:** Text placement tests, Matplotlib comparison images (in the `build`-extra job), and the core-only milestone test in CI.

### Session 10

**Goal:** Author `.cstack` files from Matplotlib/Cartopy on a build machine.

**Deliverables:** `cartostack.build.SceneBuilder`, fixed-geometry figure construction, static-layer capture, index-map generation, georeferencing export, and an authored example file.

- [ ] Build figures from the canvas geometry with transparent backgrounds and fixed axes placement; resolve Cartopy's aspect adjustment once and record the resolved axes rectangle in the file.
- [ ] Capture static layers via user draw callbacks into owned RGBA buffers, avoiding duplicated frames/backgrounds across layers; release figures afterwards.
- [ ] Author polygon slots from a bin table (as in the QPF example) and resolve a `bbox_inches="tight"` output crop once at build time.
- [ ] Generate grid-slot index maps with the index-image method from caller-supplied lon/lat (centers or edges, documented).
- [ ] Capture text-slot definitions (font file, size, color, anchor) from the intended style and embed the font.
- [ ] Export the CRS and affine pixel transform; test that control-point lon/lat positions map to the same pixels via the stored transform (using pyproj in the test) as Cartopy draws them.
- [ ] Compare a freshly authored and updated file's output with a full Cartopy render of the same inputs.

**Verification:** Authoring tests in the `build`-extra job, control-point alignment tests, and authored-versus-baseline image comparison.

### Session 11

**Goal:** Keep colorbars and legends consistent with grid normalization without Matplotlib at runtime.

**Deliverables:** Colorbar layer kind, its dependency on a grid slot, and tests.

- [ ] Author colorbars at build time; store the colorbar's geometry (gradient box, tick positions, label text/font) so the runtime can redraw it with NumPy/Pillow.
- [ ] Reuse the stored colorbar when only values change; redraw it when `vmin`/`vmax` or the colormap change.
- [ ] Support a static legend image (raster layer) for non-grid content.
- [ ] Verify the build-to-runtime milestone: authored file → updated data, normalization, and title → output matches a full Cartopy render within documented tolerances.

**Verification:** Fixed/changed-normalization tests, colorbar comparison against Matplotlib's colorbar, and the end-to-end milestone comparison.

### Session 12

**Goal:** Make loading fast by decoding only what is needed.

**Deliverables:** Lazy loading, `Scene.close()`/context-manager support, and the encoding/flattening defaults chosen in Session 02.

- [ ] Load the manifest without decoding layers; decode on first use and reuse the buffer afterwards.
- [ ] Replace a lazily loaded layer without decoding its previous pixels.
- [ ] Optionally store pre-flattened static runs in the file and use them when the layers in a run are unchanged.
- [ ] Define archive lifetime and close behavior; error clearly when a closed scene needs undecoded data.
- [ ] Test lazy/eager output equivalence and saving scenes that mix replaced and never-decoded layers.

**Verification:** Decoder-call instrumentation, exact lazy/eager comparisons, lifecycle tests, and cold-process load timings versus Session 02.

### Session 13

**Goal:** Verify portability and dependency boundaries independently of the build environment.

**Deliverables:** Source-independence and fresh-environment tests in CI.

- [ ] Author and save a file, remove the source fixtures and Natural Earth cache, then load, update, and render it in a fresh process.
- [ ] In an environment with only core dependencies, verify load, grid, polygon, and text replacement (no pyproj or shapely installed), layer addition, compositing, save, and PNG output, and assert Matplotlib/Cartopy are absent from `sys.modules`.
- [ ] Disable network access during runtime tests and verify no downloads or source lookups are attempted.
- [ ] Confirm lazy and eager loading meet the same guarantees.

**Verification:** Fresh-environment, fresh-process CI jobs; image comparisons; explicitly removed sources and blocked network.

### Session 14

**Goal:** Make the build and runtime workflows usable from documented examples.

**Deliverables:** Authoring and runtime examples, API and format documentation, and updated repository instructions.

- [ ] Add executable examples: author the reference scenes, then cron-style runtime scripts that update grid and text, and QPF polygons and subtitle for all products in one process, and write PNGs.
- [ ] Document installation (core vs `build` extra), embedded fonts, archive lifecycle, the format reference, and limitations.
- [ ] Separate the README quickstart and supported features from the longer design roadmap without losing the architectural reference.
- [ ] Document focused test/benchmark commands and reconcile `CLAUDE.md` with the implemented repository.

**Verification:** Run examples from an installed package with prepared fixtures, including runtime use in a core-only environment.

### Session 15

**Goal:** Validate distribution artifacts and record whether v0.1 is ready for release.

**Deliverables:** Wheel/sdist validation, supported-environment checks, benchmark summary, and a release-readiness record.

- [ ] Build wheel and sdist; inspect metadata, extras, and included assets.
- [ ] Install the artifacts into fresh environments and verify imports/examples outside the repository source directory.
- [ ] Run core-only and `build`-extra tests across the declared Python range, with small cross-platform smoke tests where available.
- [ ] Re-run the Session 02 benchmark against the package and record the results alongside the baseline.
- [ ] Check distribution-name availability and document remaining release actions; publishing requires a separate user request.
- [ ] Confirm Sessions 01–14 are completed or resolve their blockers before declaring v0.1 readiness.

**Verification:** Full configured checks, fresh artifact installation, runnable example smoke tests, and benchmark evidence. Record unsupported/unverified environments explicitly.

### Session 16

**Goal:** Choose the next extension from measured needs rather than implementing the speculative roadmap.

**Deliverables:** Small/medium/large workload report and explicitly scoped follow-up sessions.

- [ ] Profile cold start, manifest read, layer decode, grid render, text render, compositing, PNG encoding, and peak memory separately for each workload size.
- [ ] Compare with the equivalent full Cartopy baseline, cold and warm.
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
uvx --from actionlint-py actionlint          # lint .github/workflows
```

Focused (Session 04):

```bash
uv run pytest tests/test_geometry.py tests/test_manifest.py   # format 1.0 validation, examples, schema agreement
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
