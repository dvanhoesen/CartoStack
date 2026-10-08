# Contributing to CartoStack

CartoStack is built one numbered session at a time; `SESSIONS.md` is the plan, the
handoff record, and the source of truth for commands. `README.md` is the longer
architecture proposal.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (CI pins 0.12.23). Python 3.12–3.14 are
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

CI (`.github/workflows/ci.yml`) runs the same commands: lint and types; core-only tests on
Python 3.12, 3.13, and 3.14 with a check that Matplotlib and Cartopy are not installed;
the lowest allowed core dependency versions; the `build` extra on 3.12 and 3.14; and a
distribution job that installs the wheel alone and runs the tests from the sdist.

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
