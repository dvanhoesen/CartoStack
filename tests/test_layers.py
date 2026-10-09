"""Layer objects and scene construction: ownership, validation, and compatibility."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from helpers import FONT, full_scene, gradient

from cartostack import (
    GridSlot,
    LayerError,
    PolygonSlot,
    RasterLayer,
    Scene,
    SceneError,
    TextSlot,
)
from cartostack.geometry import CanvasGeometry, Rect
from cartostack.manifest import Bin

PLAIN = CanvasGeometry(64, 48, 100.0, Rect(0, 0, 64, 48))


def test_caller_arrays_are_snapshotted() -> None:
    src = gradient(48, 64)
    layer = RasterLayer(id="a", order=0, pixels=src)
    src[:] = 0
    assert layer.pixels is not None
    assert layer.pixels.max() > 0
    assert not layer.pixels.flags.writeable
    with pytest.raises(ValueError, match="read-only"):
        layer.pixels[0, 0, 0] = 1


def test_non_contiguous_input_becomes_contiguous() -> None:
    layer = RasterLayer(id="a", order=0, pixels=gradient(48, 128)[:, ::2])
    assert layer.pixels is not None
    assert layer.pixels.flags.c_contiguous


@pytest.mark.parametrize(
    ("pixels", "message"),
    [
        (np.zeros((4, 4, 4), np.float32), "dtype uint8"),
        (np.zeros((4, 4, 3), np.uint8), "shape"),
        (np.zeros((4, 4), np.uint8), "shape"),
        (np.zeros((0, 4, 4), np.uint8), "shape"),
        ([[1, 2, 3, 4]], "dtype uint8"),
    ],
)
def test_invalid_pixels_are_rejected(pixels: object, message: str) -> None:
    with pytest.raises(LayerError, match=message):
        RasterLayer(id="a", order=0, pixels=pixels)  # type: ignore[arg-type]


def test_declared_size_must_match_pixels() -> None:
    with pytest.raises(LayerError, match="width=10 does not match"):
        RasterLayer(id="a", order=0, width=10, pixels=gradient(48, 64))


def test_raster_needs_pixels() -> None:
    with pytest.raises(LayerError, match="needs pixels"):
        RasterLayer(id="a", order=0)


def test_layers_are_immutable() -> None:
    layer = RasterLayer(id="a", order=0, pixels=gradient(4, 4))
    with pytest.raises(dataclasses.FrozenInstanceError):
        layer.order = 3  # type: ignore[misc]


def grid(**kw: object) -> GridSlot:
    args: dict[str, object] = {
        "id": "g",
        "order": 1,
        "shape": (2, 3),
        "index_map": np.zeros((48, 64), np.int32),
        "lut": np.zeros((259, 4), np.uint8),
        "vmin": 0.0,
        "vmax": 1.0,
    }
    args.update(kw)
    return GridSlot(**args)  # type: ignore[arg-type]


def test_grid_slot_validation() -> None:
    assert grid().n_colors == 256
    assert (grid().height, grid().width) == (48, 64)
    with pytest.raises(LayerError, match="dtype int32"):
        grid(index_map=np.zeros((48, 64), np.int64))
    with pytest.raises(LayerError, match=r"0\.\.5"):
        grid(index_map=np.full((48, 64), 6, np.int32))
    with pytest.raises(LayerError, match="lut must be uint8"):
        grid(lut=np.zeros((3, 4), np.uint8))
    with pytest.raises(LayerError, match="vmin < vmax"):
        grid(vmin=1.0, vmax=1.0)
    with pytest.raises(LayerError, match="does not match index_map"):
        grid(pixels=gradient(10, 10))


def test_polygon_and_text_validation() -> None:
    with pytest.raises(LayerError, match="at least one bin"):
        PolygonSlot(id="p", order=1, bins=())
    with pytest.raises(LayerError, match="lower < upper"):
        PolygonSlot(id="p", order=1, bins=(Bin(1.0, 1.0, (0, 0, 0, 255), 1),))
    with pytest.raises(LayerError, match="supersample"):
        PolygonSlot(id="p", order=1, bins=(Bin(0.0, 1.0, (0, 0, 0, 255), 1),), supersample=9)
    with pytest.raises(LayerError, match="rotation"):
        TextSlot(id="t", order=1, value="x", font="f.ttf", size_px=10, rotation=90)
    with pytest.raises(LayerError, match="invalid va"):
        TextSlot(id="t", order=1, value="x", font="f.ttf", size_px=10, va="middle")


def test_scene_rejects_inconsistent_layers() -> None:
    raster = RasterLayer(id="a", order=0, pixels=gradient(48, 64))
    cases: list[tuple[list[object], dict[str, object], str]] = [
        ([raster, raster], {}, "duplicate layer id 'a'"),
        (
            [RasterLayer(id="b", order=0, left=64, pixels=gradient(4, 4))],
            {},
            "does not intersect the canvas",
        ),
        (
            [TextSlot(id="t", order=1, value="x", font="fonts/x.ttf", size_px=10)],
            {},
            "not among the scene assets",
        ),
        (
            [PolygonSlot(id="p", order=1, bins=(Bin(0.0, 1.0, (0, 0, 0, 255), 1),))],
            {},
            "supported projection",
        ),
    ]
    for layers, kw, message in cases:
        with pytest.raises(SceneError, match=message):
            Scene(PLAIN, layers, **kw)  # type: ignore[arg-type]
    Scene(
        PLAIN,
        [TextSlot(id="t", order=1, value="x", font="fonts/x.ttf", size_px=10)],
        assets={"fonts/x.ttf": FONT},
    )


def test_scene_access_and_draw_order() -> None:
    scene = full_scene()
    assert "qpf" in scene
    assert scene["qpf"].kind == "polygon"
    assert len(scene) == 6
    assert [layer.id for layer in scene.draw_order()] == [
        "below",
        "temperature",
        "qpf",
        "above",
        "cbar",
        "subtitle",
    ]
    with pytest.raises(KeyError):
        scene["missing"]


def test_evolve_shares_owned_buffers_but_snapshots_others() -> None:
    from cartostack.layers import evolve

    layer = RasterLayer(id="a", order=0, pixels=gradient(4, 4))
    moved = evolve(layer, left=2, visible=False)
    assert moved.pixels is layer.pixels
    assert (moved.left, moved.visible, layer.left, layer.visible) == (2, False, 0, True)
    assert layer.pixels is not None
    view = layer.pixels.view()  # read-only, but not a buffer a layer created
    assert RasterLayer(id="b", order=0, pixels=view).pixels is not view
    replaced = evolve(layer, pixels=gradient(2, 3))
    assert replaced.size((48, 64)) == (2, 3)


def test_evolve_drops_encoded_bytes_of_changed_buffers() -> None:
    """A new LUT or index map must not be saved from the bytes it was loaded with."""
    from cartostack.layers import evolve

    lut = np.zeros((7, 4), np.uint8)
    imap = np.zeros((2, 2), np.int32)
    encoded = {"pixels": ("rgba8", b"p"), "index_map": ("i32le", b"i"), "lut": ("rgba8", b"l")}
    g = GridSlot(id="g", order=0, shape=(1, 1), index_map=imap, lut=lut, vmin=0.0, vmax=1.0,
                 _encoded=encoded)  # fmt: skip
    assert set(evolve(g, vmin=-1.0)._encoded) == {"pixels", "index_map", "lut"}
    assert set(evolve(g, lut=lut + 1)._encoded) == {"pixels", "index_map"}
    assert set(evolve(g, index_map=imap)._encoded) == {"pixels", "lut"}
    assert set(evolve(g, pixels=np.zeros((2, 2, 4), np.uint8))._encoded) == {"index_map", "lut"}
