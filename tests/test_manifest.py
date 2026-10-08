"""Manifest validation: examples, version rules, records, and schema agreement."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from cartostack.errors import FormatError, UnsupportedVersionError
from cartostack.geometry import CanvasGeometry
from cartostack.manifest import (
    FORMAT_VERSION,
    GridSlot,
    PolygonSlot,
    TextSlot,
    load_schema,
    parse_manifest,
    validate_manifest,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "examples"
NAMES = ["qpf", "grid", "minimal"]
SCHEMA = jsonschema.Draft202012Validator(load_schema())


def example(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / f"{name}.manifest.json").read_text())
    return data


def refingerprint(m: dict[str, Any]) -> dict[str, Any]:
    m["geometry_fingerprint"] = CanvasGeometry.from_dict(m["geometry"]).fingerprint()
    return m


def problems_of(m: dict[str, Any]) -> str:
    return "\n".join(validate_manifest(m))


def test_schema_is_a_valid_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(load_schema())


@pytest.mark.parametrize("name", NAMES)
def test_examples_are_valid(name: str) -> None:
    m = example(name)
    SCHEMA.validate(m)
    manifest = parse_manifest(m)
    assert manifest.version == FORMAT_VERSION
    assert manifest.fingerprint == manifest.geometry.fingerprint()


def test_qpf_example_records() -> None:
    m = parse_manifest(example("qpf"))
    assert [layer.id for layer in m.draw_order()] == ["below", "qpf", "above", "subtitle"]
    qpf = m.layer("qpf")
    assert isinstance(qpf, PolygonSlot)
    assert len(qpf.bins) == 17
    assert qpf.fallback is not None
    assert qpf.supersample == 2
    sub = m.layer("subtitle")
    assert isinstance(sub, TextSlot)
    assert sub.offset == (0.0, -1.0)
    assert sub.placement.width == m.geometry.width  # defaults to the canvas


def test_grid_example_records() -> None:
    grid = parse_manifest(example("grid")).layer("temperature")
    assert isinstance(grid, GridSlot)
    assert grid.shape == (500, 500)
    assert (grid.vmin, grid.vmax, grid.n_colors, grid.alpha) == (-10.0, 35.0, 256, 0.8)


def test_records_are_immutable() -> None:
    m = parse_manifest(example("qpf"))
    with pytest.raises(TypeError):
        m.members["x"] = m.members["layers/below.rgba8.zz"]  # type: ignore[index]
    with pytest.raises(AttributeError):
        m.layers[0].order = 5  # type: ignore[misc]


def test_ties_keep_array_order() -> None:
    m = example("qpf")
    for layer in m["layers"]:
        layer["order"] = 1
    m["layers"].reverse()
    assert [layer.id for layer in parse_manifest(m).draw_order()] == [
        "subtitle",
        "above",
        "qpf",
        "below",
    ]


# --- Versions --------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["2.0", "0.9", "10.1"])
def test_other_major_versions_are_rejected_first(version: str) -> None:
    m = {"format": "cartostack", "format_version": version, "geometry": "garbage"}
    with pytest.raises(UnsupportedVersionError, match="not supported"):
        parse_manifest(m)


def test_newer_minor_version_with_unknown_fields_is_read() -> None:
    m = example("qpf")
    m["format_version"] = "1.7"
    m["new_top_level"] = {"x": 1}
    m["layers"][0]["blend_hint"] = "fast"
    m["layers"][1]["polygon"]["future_option"] = True
    m["geometry"]["future_geometry_field"] = 3
    assert parse_manifest(m).version == (1, 7)
    assert SCHEMA.is_valid(m)


@pytest.mark.parametrize("version", ["1", "v1.0", "1.0.0", "01.0", 1.0, None])
def test_malformed_versions_are_rejected(version: object) -> None:
    m = example("minimal")
    m["format_version"] = version
    with pytest.raises(FormatError, match="format_version"):
        parse_manifest(m)


def test_wrong_format_name_is_rejected() -> None:
    m = example("minimal")
    m["format"] = "openraster"
    with pytest.raises(FormatError, match="format"):
        parse_manifest(m)


# --- Inconsistent records ------------------------------------------------------------

Mutation = Callable[[dict[str, Any]], object]


def _set(path: list[str | int], value: object) -> Mutation:
    def mutate(m: dict[str, Any]) -> None:
        target: Any = m
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _delete(path: list[str | int]) -> Mutation:
    def mutate(m: dict[str, Any]) -> None:
        target: Any = m
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


CASES: list[tuple[str, str, Mutation, str, bool]] = [
    # (id, example, mutation, expected message, schema also rejects)
    (
        "stale fingerprint",
        "qpf",
        _set(["geometry", "figure", "width_in"], 11.0),
        "geometry_fingerprint: does not match",
        False,
    ),
    ("duplicate id", "qpf", _set(["layers", 2, "id"], "below"), "duplicates layers[0].id", False),
    ("bad id", "qpf", _set(["layers", 0, "id"], "Below Layer"), "layers[0].id", True),
    ("unknown kind", "qpf", _set(["layers", 0, "kind"], "vector"), "layers[0].kind", True),
    (
        "unknown encoding",
        "qpf",
        _set(["layers", 0, "encoding"], "zstd"),
        "layers[0].encoding",
        True,
    ),
    (
        "member not listed",
        "qpf",
        _set(["layers", 0, "member"], "layers/nope.rgba8.zz"),
        "is not listed in members",
        False,
    ),
    ("raster without member", "qpf", _delete(["layers", 0, "member"]), "layers[0].member", True),
    (
        "reserved member",
        "qpf",
        _set(["members", "manifest.json"], {"bytes": 1, "sha256": "0" * 64}),
        "is reserved",
        True,
    ),
    (
        "traversal member",
        "qpf",
        _set(["members", "../evil"], {"bytes": 1, "sha256": "0" * 64}),
        "not a valid member name",
        True,
    ),
    (
        "bad digest",
        "qpf",
        _set(["members", "layers/below.rgba8.zz", "sha256"], "XYZ"),
        "sha256",
        True,
    ),
    ("opacity > 1", "qpf", _set(["layers", 0, "opacity"], 1.5), "layers[0].opacity", True),
    (
        "placement off canvas",
        "qpf",
        _set(["layers", 0, "left"], 5000),
        "placement does not intersect the canvas",
        False,
    ),
    ("no layers", "qpf", _set(["layers"], []), "at least one layer", True),
    (
        "bin lower >= upper",
        "qpf",
        _set(["layers", 1, "polygon", "bins", 0, "upper"], 0.0),
        "requires lower < upper",
        False,
    ),
    (
        "bin colour out of range",
        "qpf",
        _set(["layers", 1, "polygon", "bins", 0, "color"], [256, 0, 0, 255]),
        "color[0]",
        True,
    ),
    ("supersample 0", "qpf", _set(["layers", 1, "polygon", "supersample"], 0), "supersample", True),
    (
        "polygon, unsupported projection",
        "qpf",
        lambda m: (
            m["geometry"]["georeference"].update(projection={"name": "stere", "lat_0": 90.0}),
            refingerprint(m),
        ),
        "not supported by the runtime",
        False,
    ),
    ("text rotation", "qpf", _set(["layers", 3, "text", "rotation"], 90.0), "rotation", True),
    (
        "text font missing",
        "qpf",
        _set(["layers", 3, "text", "font"], "fonts/Missing.ttf"),
        "is not listed in members",
        False,
    ),
    ("text va", "qpf", _set(["layers", 3, "text", "va"], "middle"), "text.va", True),
    (
        "grid vmin >= vmax",
        "grid",
        _set(["layers", 1, "grid", "norm", "vmax"], -10.0),
        "requires vmin < vmax",
        False,
    ),
    (
        "grid index encoding",
        "grid",
        _set(["layers", 1, "grid", "index_map", "encoding"], "rgba8"),
        "index_map.encoding",
        True,
    ),
    (
        "grid shape too large",
        "grid",
        _set(["layers", 1, "grid", "shape"], [70000, 70000]),
        "int32",
        False,
    ),
    ("grid alpha", "grid", _set(["layers", 1, "grid", "alpha"], 2), "grid.alpha", True),
]


@pytest.mark.parametrize(
    ("name", "mutate", "message", "schema_rejects"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_inconsistent_records_are_rejected(
    name: str, mutate: Mutation, message: str, schema_rejects: bool
) -> None:
    m = example(name)
    mutate(m)
    with pytest.raises(FormatError):
        parse_manifest(m)
    assert message in problems_of(m)
    assert SCHEMA.is_valid(m) is not schema_rejects


def test_polygon_slot_needs_a_georeference() -> None:
    m = example("minimal")
    m["layers"].append(copy.deepcopy(example("qpf")["layers"][1]))
    assert "polygon slots need geometry.georeference" in problems_of(m)


def test_colorbar_must_reference_a_grid_slot() -> None:
    m = example("grid")
    bar = {
        "id": "cbar",
        "kind": "colorbar",
        "order": 25,
        "member": "layers/above.rgba8.zz",
        "encoding": "rgba8+zlib",
        "colorbar": {"slot": "subtitle"},
    }
    m["layers"].append(bar)
    assert "'subtitle' is not a grid layer" in problems_of(m)
    bar["colorbar"]["slot"] = "temperature"
    assert validate_manifest(m) == []


def test_every_problem_is_reported() -> None:
    m = example("qpf")
    m["layers"][0]["opacity"] = -1
    m["layers"][2]["id"] = "below"
    m["layers"][3]["text"]["rotation"] = 45
    m["members"]["layers/below.rgba8.zz"]["bytes"] = -5
    problems = validate_manifest(m)
    assert len(problems) >= 4, problems


def test_parse_valid_implies_schema_valid_for_defaults_omitted() -> None:
    """Optional fields may be omitted; both validators accept the defaults."""
    m = example("qpf")
    for layer in m["layers"]:
        for key in ("left", "top", "width", "height", "visible", "opacity"):
            layer.pop(key, None)
    m["layers"][3]["text"] = {
        k: m["layers"][3]["text"][k] for k in ("value", "font", "size_px", "color", "x", "y")
    }
    m.pop("provenance")
    parse_manifest(m)
    assert SCHEMA.is_valid(m)


def test_validation_needs_no_numpy_or_extras() -> None:
    code = (
        "import json, sys, cartostack.manifest as cm, cartostack.geometry;"
        f"cm.parse_manifest(json.load(open({str(EXAMPLES / 'qpf.manifest.json')!r})));"
        "print(json.dumps(sorted(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    loaded = {name.split(".")[0] for name in json.loads(out)}
    assert loaded.isdisjoint({"numpy", "PIL", "matplotlib", "cartopy", "pyproj", "shapely"})
