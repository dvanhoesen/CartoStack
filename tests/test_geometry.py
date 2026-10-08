"""Canvas geometry: validation, immutability, consistency, and fingerprints."""

from __future__ import annotations

import copy
import dataclasses
import json
import math
from pathlib import Path
from typing import Any

import pytest

from cartostack.errors import GeometryError
from cartostack.geometry import CanvasGeometry, Rect

EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "examples"


def example(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / f"{name}.manifest.json").read_text())
    return data


def geometry_dict(name: str = "qpf") -> dict[str, Any]:
    return copy.deepcopy(example(name)["geometry"])


@pytest.mark.parametrize("name", ["qpf", "grid", "minimal"])
def test_round_trip_and_fingerprint_are_stable(name: str) -> None:
    g = CanvasGeometry.from_dict(geometry_dict(name))
    again = CanvasGeometry.from_dict(g.to_dict())
    assert again == g
    assert again.fingerprint() == g.fingerprint() == example(name)["geometry_fingerprint"]


def test_fingerprint_is_pinned() -> None:
    """Locks the canonicalisation rules of docs/format.md §5.6."""
    g = CanvasGeometry.from_dict(
        {
            "canvas": {"width": 640, "height": 480, "dpi": 100},
            "axes": {"left": 0, "top": 0, "width": 640, "height": 480},
        }
    )
    expected = "sha256:22e73497bcc6c025344e1020a4fb519fd99cc06d7312b004bf92dde16f277662"
    assert g.fingerprint() == expected


def test_fingerprint_ignores_key_order_number_spelling_and_unknown_fields() -> None:
    d = geometry_dict()
    shuffled = json.loads(json.dumps(d, sort_keys=False))
    shuffled["canvas"] = {"dpi": 300, "height": 1848, "width": 2210}  # int dpi, reordered
    shuffled["future_field"] = {"anything": 1}
    a, b = CanvasGeometry.from_dict(d), CanvasGeometry.from_dict(shuffled)
    assert a.fingerprint() == b.fingerprint()
    assert a.compatible_with(b)


@pytest.mark.parametrize(
    ("path", "delta"),
    [
        (("figure", "width_in"), 0.5),
        (("axes", "height"), -1e-6),
        (("figure", "dpi"), 1.0),
        (("georeference", "projection", "lat_1"), 1e-9),
    ],
)
def test_fingerprint_changes_with_any_geometry_change(path: tuple[str, ...], delta: float) -> None:
    d = geometry_dict()
    target = d
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] += delta
    assert CanvasGeometry.from_dict(d).fingerprint() != example("qpf")["geometry_fingerprint"]


def test_geometry_is_immutable() -> None:
    g = CanvasGeometry.from_dict(geometry_dict())
    with pytest.raises(dataclasses.FrozenInstanceError):
        g.width = 10  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        g.axes.left = 1.0  # type: ignore[misc]


@pytest.mark.parametrize("value", [0, -1, 100_001, 1.5, True, "800", None])
def test_invalid_canvas_width_is_rejected(value: object) -> None:
    d = geometry_dict("minimal")
    d["canvas"]["width"] = value
    with pytest.raises(GeometryError, match=r"geometry\.canvas\.width"):
        CanvasGeometry.from_dict(d)


@pytest.mark.parametrize("dpi", [0, -72, math.nan, math.inf])
def test_invalid_dpi_is_rejected(dpi: float) -> None:
    d = geometry_dict("minimal")
    d["canvas"]["dpi"] = dpi
    with pytest.raises(GeometryError, match="dpi"):
        CanvasGeometry.from_dict(d)


def test_all_problems_are_reported_together() -> None:
    d = geometry_dict("minimal")
    d["canvas"]["width"] = 0
    d["canvas"]["dpi"] = -1
    del d["axes"]["height"]
    with pytest.raises(GeometryError) as exc:
        CanvasGeometry.from_dict(d)
    assert len(exc.value.problems) == 3


def test_direct_construction_validates() -> None:
    with pytest.raises(GeometryError, match="width"):
        CanvasGeometry(0, 10, 100.0, Rect(0, 0, 10, 10))
    with pytest.raises(GeometryError, match="do not intersect"):
        CanvasGeometry(100, 100, 100.0, Rect(150, 0, 10, 10))


def test_axes_may_overhang_by_a_tight_crop_but_must_intersect() -> None:
    d = geometry_dict()
    assert d["axes"]["width"] > d["canvas"]["width"]  # 2210.9 px axes on a 2210 px canvas
    CanvasGeometry.from_dict(d)
    d["axes"]["width"] = 0
    with pytest.raises(GeometryError, match=r"axes\.width"):
        CanvasGeometry.from_dict(d)


def test_figure_crop_must_match_canvas_size() -> None:
    d = geometry_dict()
    d["canvas"]["width"] = 2000
    d["georeference"] = None
    with pytest.raises(GeometryError, match=r"does not match figure\.crop_in"):
        CanvasGeometry.from_dict(d)


def test_georeference_must_agree_with_axes() -> None:
    d = geometry_dict()
    d["georeference"]["world_to_pixel"][2] += 0.5  # shift columns by half a pixel
    with pytest.raises(GeometryError, match="georeference inconsistent with axes"):
        CanvasGeometry.from_dict(d)


def test_world_to_pixel_must_be_invertible() -> None:
    d = geometry_dict()
    d["georeference"]["world_to_pixel"] = [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]
    with pytest.raises(GeometryError, match="not invertible"):
        CanvasGeometry.from_dict(d)


def test_lcc_requires_its_parameters_and_valid_parallels() -> None:
    d = geometry_dict()
    del d["georeference"]["projection"]["lat_2"]
    with pytest.raises(GeometryError, match="lat_2: is required for projection 'lcc'"):
        CanvasGeometry.from_dict(d)
    d = geometry_dict()
    d["georeference"]["projection"]["lat_1"] = -45.0
    d["georeference"]["projection"]["lat_2"] = 45.0
    with pytest.raises(GeometryError, match="symmetric"):
        CanvasGeometry.from_dict(d)


def test_unknown_projection_is_allowed_but_unsupported() -> None:
    d = geometry_dict()
    d["georeference"]["projection"] = {"name": "stere", "lat_0": 90.0}
    g = CanvasGeometry.from_dict(d)
    assert g.georeference is not None
    assert not g.georeference.projection.supported


def test_pixel_world_round_trip() -> None:
    g = CanvasGeometry.from_dict(geometry_dict())
    geo = g.georeference
    assert geo is not None
    col, row = geo.to_pixel(12345.0, -6789.0)
    x, y = geo.to_world(col, row)
    assert x == pytest.approx(12345.0, abs=1e-6)
    assert y == pytest.approx(-6789.0, abs=1e-6)
    assert geo.to_pixel(geo.extent[0], geo.extent[3]) == pytest.approx((g.axes.left, g.axes.top))
