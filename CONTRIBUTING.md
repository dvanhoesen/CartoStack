# Contributing to CartoStack

CartoStack is built one numbered session at a time; `SESSIONS.md` is the plan, the
handoff record, and the source of truth for commands. `README.md` is the user-facing
overview and quickstart. `docs/api.md` is the API reference, `docs/format.md` the format
specification, and `docs/design.md` the longer architecture proposal (formerly the
README).

## Setup

Requires [uv](https://docs.astral.sh/uv/) (developed with 0.12.23). Python 3.12–3.14 are
supported; `.python-version` selects 3.13 for local work.

```bash
uv sync --group dev                  # core only: NumPy + Pillow (what the runtime may use)
uv sync --extra build --group dev    # plus Matplotlib + Cartopy for authoring (cartostack.build)
```

`uv sync` is exact: running the first command after the second removes the extra
again, which is how to check that the runtime still works without it.

## Checks

```bash
uv run ruff format --check .         # formatting (package, tests, scripts)
uv run ruff check .                  # lint (benchmarks get relaxed rules)
uv run mypy                          # strict type check of src/cartostack
uv run pytest                        # tests; build-extra and fixture tests skip when unavailable
uv run pytest tests/test_package.py::test_import_does_not_load_rendering_libraries
uv build                             # sdist + wheel into dist/
```

There is no CI. Run the checks above locally before handing off work.

## Environment checks

Run these manually once the package works end to end (Sessions 13 and 15), and whenever
dependencies change. Together they show that it works outside the development
environment. Record the results in `SESSIONS.md`.

```bash
# Core only on each supported Python (Matplotlib/Cartopy must not be installed)
uv run --isolated --python 3.12 --group dev pytest
uv run --isolated --python 3.13 --group dev pytest
uv run --isolated --python 3.14 --group dev pytest

# Lowest allowed core dependencies (numpy 2.0, Pillow 10.1)
uv venv --python 3.12 /tmp/cs-lowest
uv pip install --python /tmp/cs-lowest/bin/python --resolution lowest-direct . "pytest>=8.3" "jsonschema>=4.23"
/tmp/cs-lowest/bin/python -m pytest

# Lowest allowed build extra (Matplotlib 3.11.1, Cartopy 0.26), from the built wheel
uv venv --python 3.12 /tmp/cs-lowest-build
uv pip install --python /tmp/cs-lowest-build/bin/python "cartostack[build] @ file://$(ls /tmp/cs-dist/*.whl)" \
    "numpy==2.0.*" "pillow==10.1.*" "matplotlib==3.11.1" "cartopy==0.26.0" "pytest>=8.3" "jsonschema>=4.23"
(cd <unpacked sdist> && /tmp/cs-lowest-build/bin/python -m pytest)   # tests the installed wheel, not src/

# With the build extra
uv sync --extra build --group dev && uv run pytest && uv sync --group dev

# Distributions: the wheel installs alone and imports outside the source tree
uv build --out-dir /tmp/cs-dist
uv venv --python 3.13 /tmp/cs-wheel && uv pip install --python /tmp/cs-wheel/bin/python /tmp/cs-dist/*.whl
(cd /tmp && /tmp/cs-wheel/bin/python -c "import cartostack; print(cartostack.__file__)")
```

### Source independence, core-only, no network (Session 13)

```bash
uv sync --extra build --group dev   # authoring needs the build extra; fixtures in benchmarks/data
uv run scripts/check_portability.py [--sandbox DIR] [--python 3.13] [--authored benchmarks/results/authored-<stamp>]
uv sync --group dev
```

The script, step by step:

1. **Author** the QPF and grid scenes (`benchmarks/author_scenes.py`, unless `--authored`
   is given), and export the runtime inputs (every WPC QPF product's rings, values and
   subtitle; the grid's fields) as plain NumPy files in the sandbox.
2. **Core-only venv:** builds the wheel, installs it into a new venv in the sandbox
   (outside the repository) with only its own dependencies, and fails if Matplotlib,
   Cartopy, pyproj, shapely, GeoPandas, pyogrio or pandas can be imported there.
3. **Sources removed:** renames `benchmarks/data` away during the runtime stage (always
   restored) and runs with `HOME` inside the sandbox, so the Cartopy Natural Earth
   cache is out of reach (it is not modified).
4. **Runtime** in the core venv with `python -I` and a minimal environment: an audit
   hook records every opened file and refuses every network call. It renders all 18 QPF
   products and 4 grid variants, changes the grid normalisation (colorbar redraw), adds
   a layer, saves, reloads and re-renders.
5. **Checks:** no build library installed or imported; no file opened outside the
   sandbox, the venv and the Python installation; no network attempt; the reload renders
   identically; no stale colorbar; every PNG pixel-identical to the same updates run in
   the development environment. The script also prints the difference from the full
   Cartopy renders (Session 01b products, Session 02 grid references).

Results go to `benchmarks/results/portability-<stamp>/` (`report.json`, `compare.json`
and the core-environment PNGs); the script exits non-zero if any check fails.
`tests/test_portability.py` checks the same "no outside files, no network" rule in every
test run, with a negative control that shows the audit hook catches both.

## Rules that tests enforce

- `import cartostack` must never import Matplotlib, Cartopy, pyproj, or shapely; the
  runtime depends only on NumPy and Pillow. Authoring code goes behind the `build` extra.
- Tests never use the network (an autouse fixture blocks sockets).

## Fixtures

Reference data (Natural Earth, NY counties, WPC QPF snapshot, SWRCC GeoPackages) is
prepared once, with network access, into the gitignored `benchmarks/data/` (override with
`CARTOSTACK_FIXTURE_DIR`):

```bash
uv run scripts/fetch_fixtures.py           # prepare (network; idempotent)
uv run scripts/fetch_fixtures.py --check   # verify against fixtures.json (offline)
```

Tests that need fixtures are marked `@pytest.mark.fixtures` and skip when the directory
does not verify; they read it through the `fixture_dir` pytest fixture. The SWRCC files
come from a local directory (`SWRCC_RESOURCES`); the QPF snapshot is kept once taken.

## Benchmarks

`benchmarks/` holds standalone PEP 723 scripts (each with a `.lock` file) recording the
Session 01–02 experiments; run them with `uv run benchmarks/<script>.py`. They are not
part of the package or the test suite. See `SESSIONS.md` → "Development commands".

## Releasing

Publishing needs an explicit decision by the maintainer; no session publishes on its own.
The checks below are the Session 15 release validation. The record of the last run, and
what it left unverified, is in `SESSIONS.md`.

1. Commit the work, merge it to `main` and push. The PyPI description links to files on
   `main`: the README's speed figure is `raw.githubusercontent.com/.../main/docs/images/...`.
2. Set the version in `src/cartostack/__init__.py` (for example `0.1.0`) and the
   `Development Status` classifier.
3. Build and check: `uv build --out-dir dist && uvx twine check dist/*`. Inspect the wheel
   (only `cartostack/`, including `schemas/` and `py.typed`) and the sdist (`src`, `tests`,
   `examples`, `docs/api.md`, `docs/format.md`, `docs/examples`, `docs/images`; no
   `benchmarks/`, `resources/` or `CLAUDE.md`).
4. Run "Environment checks" above against the built artifacts, and the examples from the
   installed wheel outside the repository (`examples/README.md`).
5. Upload to TestPyPI first, install from it into a fresh environment, then upload to PyPI
   (`uv publish`, preferably with PyPI Trusted Publishing from a tagged commit).
6. Tag the release (`git tag v0.1.0`) and push the tag.
