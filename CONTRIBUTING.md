# Contributing to CartoStack

`README.md` is the overview. `docs/api.md` is the API reference, `docs/format.md` the
file format specification and `docs/design.md` explains how the package works.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12–3.14 are supported;
`.python-version` selects 3.13 for local work.

```bash
uv sync --group dev                  # core only: NumPy + Pillow (what the runtime may use)
uv sync --extra build --group dev    # plus Matplotlib + Cartopy for authoring (cartostack.build)
```

`uv sync` is exact: running the first command after the second removes the extra again,
which is how to check that the runtime still works without it.

## Checks

There is no CI. Run these locally before opening a pull request:

```bash
uv run ruff format --check .         # formatting
uv run ruff check .                  # lint
uv run mypy                          # strict type check of src/cartostack
uv run pytest                        # tests; build-extra and fixture tests skip when unavailable
uv build                             # sdist + wheel into dist/
```

Two rules are enforced by the tests:

- `import cartostack` never imports Matplotlib, Cartopy, pyproj or shapely; the runtime
  depends only on NumPy and Pillow. Authoring code goes behind the `build` extra.
- Tests never use the network (an autouse fixture blocks sockets).

## Environment checks

Run these when dependencies change and before a release, to show that the package works
outside the development environment:

```bash
# Core only on each supported Python
uv run --isolated --python 3.12 --group dev pytest
uv run --isolated --python 3.13 --group dev pytest
uv run --isolated --python 3.14 --group dev pytest

# Lowest allowed core dependencies (NumPy 2.0, Pillow 10.1)
uv venv --python 3.12 /tmp/cs-lowest
uv pip install --python /tmp/cs-lowest/bin/python --resolution lowest-direct . "pytest>=8.3" "jsonschema>=4.23"
/tmp/cs-lowest/bin/python -m pytest

# With the build extra
uv sync --extra build --group dev && uv run pytest && uv sync --group dev

# The wheel installs alone and imports outside the source tree
uv build --out-dir /tmp/cs-dist
uv venv --python 3.13 /tmp/cs-wheel && uv pip install --python /tmp/cs-wheel/bin/python /tmp/cs-dist/*.whl
(cd /tmp && /tmp/cs-wheel/bin/python -c "import cartostack; print(cartostack.__file__)")
```

`scripts/check_portability.py` (needs the build extra and the fixtures) authors the
reference scenes, installs the wheel into a fresh core-only environment, hides the source
data, and renders every product with file access audited and the network blocked. It
fails if any build library is importable, any file outside the sandbox is opened, or any
output differs from the development environment:

```bash
uv run scripts/check_portability.py [--sandbox DIR] [--python 3.13]
```

## Fixtures

Reference data (Natural Earth, NY counties, a WPC QPF snapshot, SWRCC GeoPackages) is
prepared once, with network access, into the gitignored `benchmarks/data/` (override with
`CARTOSTACK_FIXTURE_DIR`):

```bash
uv run scripts/fetch_fixtures.py           # prepare (network; idempotent)
uv run scripts/fetch_fixtures.py --check   # verify (offline)
```

Tests that need fixtures are marked `@pytest.mark.fixtures` and skip when the directory
does not verify.

## Benchmarks

`benchmarks/` holds standalone scripts comparing CartoStack with the Matplotlib/Cartopy
render; run them with `uv run benchmarks/<script>.py`. `benchmarks/report.py` turns the
results into figures. They are not part of the package or the test suite.

## Releasing

1. Merge to `main` and push. The PyPI description links to files on `main`, including
   the README's speed figure.
2. Set the version in `src/cartostack/__init__.py` and the `Development Status`
   classifier in `pyproject.toml`.
3. Build and check: `uv build --out-dir dist && uvx twine check dist/*`. The wheel should
   contain only `cartostack/`.
4. Run the environment checks above against the built artifacts.
5. Upload to TestPyPI, install from it into a fresh environment, then publish to PyPI
   (`uv publish`).
6. Tag the release (`git tag v0.1.0`) and push the tag.
