"""Editing a loaded scene: replace, add, remove, show/hide, reorder, and save."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from helpers import full_scene, gradient, unpack

from cartostack import (
    FormatError,
    LayerError,
    RasterLayer,
    Scene,
    SceneError,
)
from cartostack import compositor as comp
from cartostack import io as cio
from cartostack import layers as clayers
from cartostack.layers import Layer


@pytest.fixture
def template(tmp_path: Path) -> Path:
    path = tmp_path / "template.cstack"
    full_scene().save(path)
    return path


def snapshot(scene: Scene) -> list[Layer]:
    return list(scene.layers)


def assert_unchanged(scene: Scene, before: list[Layer], image: np.ndarray) -> None:
    assert list(scene.layers) == before  # identity: eq=False compares objects
    assert all(a is b for a, b in zip(scene.layers, before, strict=True))
    np.testing.assert_array_equal(scene.render(), image)


def spy(monkeypatch: pytest.MonkeyPatch, module: Any, name: str, arg: int = 0) -> list[Any]:
    """Record argument ``arg`` of every call to ``module.name``."""
    calls: list[Any] = []
    original: Callable[..., Any] = getattr(module, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[arg])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapper)
    return calls


def rgba(height: int, width: int, seed: int) -> np.ndarray:
    return gradient(height, width, seed)


# --- Replacing --------------------------------------------------------------------------


def test_replace_pixels_keeps_other_fields_and_layers(template: Path) -> None:
    scene = Scene.load(template)
    old, others = scene["above"], [la for la in scene.layers if la.id != "above"]
    new_px = rgba(40, 60, 9)
    new = scene.replace_layer("above", new_px)
    assert scene["above"] is new
    np.testing.assert_array_equal(new.pixels, new_px)
    for name in ("order", "left", "top", "visible", "opacity", "encoding"):
        assert getattr(new, name) == getattr(old, name)
    assert all(
        a is b for a, b in zip([la for la in scene.layers if la.id != "above"], others, strict=True)
    )
    expected = comp.composite(scene.draw_order(), scene.shape)
    np.testing.assert_array_equal(scene.render(), np.asarray(expected))


def test_item_assignment_and_deletion(template: Path) -> None:
    scene = Scene.load(template)
    scene["below"] = rgba(*scene.shape, 7)
    np.testing.assert_array_equal(scene["below"].pixels, rgba(*scene.shape, 7))
    layer = RasterLayer(id="above", order=50, pixels=rgba(10, 10, 1), left=3, top=4)
    scene["above"] = layer
    assert scene["above"] is layer
    del scene["above"]
    assert "above" not in scene


def test_replace_with_new_layer_object_may_change_size_and_kind(template: Path) -> None:
    scene = Scene.load(template)
    scene.replace_layer("qpf", RasterLayer(id="qpf", order=12, pixels=rgba(5, 6, 3), left=-2))
    assert scene["qpf"].kind == "raster"
    assert scene["qpf"].size(scene.shape) == (5, 6)


@pytest.mark.parametrize(
    ("edit", "error", "match"),
    [
        (lambda s: s.replace_layer("above", rgba(41, 60, 0)), SceneError, "41x60"),
        (
            lambda s: s.replace_layer("above", np.zeros((40, 60, 4), np.float32)),
            LayerError,
            "uint8",
        ),
        (lambda s: s.replace_layer("above", np.zeros((40, 60, 3), np.uint8)), LayerError, "4\\)"),
        (lambda s: s.replace_layer("temperature", rgba(*s.shape, 0)), SceneError, "slot"),
        (lambda s: s.replace_layer("subtitle", rgba(5, 5, 0)), SceneError, "slot"),
        (
            lambda s: s.replace_layer("above", RasterLayer(id="x", order=0, pixels=rgba(4, 4, 0))),
            SceneError,
            "does not match",
        ),
        (
            # The colorbar refers to this grid slot.
            lambda s: s.replace_layer(
                "temperature", RasterLayer(id="temperature", order=0, pixels=rgba(4, 4, 0))
            ),
            SceneError,
            "colorbar slot",
        ),
        (
            lambda s: s.replace_layer(
                "above", RasterLayer(id="above", order=0, left=5000, pixels=rgba(4, 4, 0))
            ),
            SceneError,
            "does not intersect",
        ),
        (lambda s: s.replace_layer("nope", rgba(4, 4, 0)), KeyError, "nope"),
    ],
)
def test_failed_replacement_leaves_scene_usable(
    template: Path, edit: Callable[[Scene], object], error: type[Exception], match: str
) -> None:
    scene = Scene.load(template)
    scene.render()  # warm the compositor cache
    before, image = snapshot(scene), scene.render()
    with pytest.raises(error, match=match):
        edit(scene)
    assert_unchanged(scene, before, image)
    scene.save(template.with_name("after.cstack"))
    assert template.with_name("after.cstack").read_bytes() == template.read_bytes()


# --- Adding and removing ----------------------------------------------------------------


def test_add_layer_from_pixels(template: Path) -> None:
    scene = Scene.load(template)
    top = max(layer.order for layer in scene.layers)
    logo = scene.add_layer("logo", rgba(12, 20, 4), left=200, top=170)
    assert scene.draw_order()[-1] is logo
    assert logo.order == top + 1
    assert (logo.left, logo.top, logo.kind) == (200, 170, "raster")
    tied = scene.add_layer("tied", rgba(12, 20, 5), order=logo.order, opacity=0.5)
    assert scene.draw_order()[-2:] == (logo, tied)
    # above, cbar, logo and tied form one flattened run above a slot: rounding only.
    assert np.abs(scene.render().astype(int) - scene.render(flatten=False)).max() <= 3
    np.testing.assert_array_equal(
        scene.render(flatten=False), np.asarray(comp.composite(scene.draw_order(), scene.shape))
    )


def test_add_layer_object_and_errors(template: Path) -> None:
    scene = Scene.load(template)
    before, image = snapshot(scene), scene.render()
    layer = RasterLayer(id="warnings", order=11, pixels=rgba(*scene.shape, 6))
    with pytest.raises(TypeError, match="either"):
        scene.add_layer(layer, order=3)
    with pytest.raises(TypeError, match="needs RGBA pixels"):
        scene.add_layer("x")
    with pytest.raises(SceneError, match="duplicate layer id 'below'"):
        scene.add_layer("below", rgba(4, 4, 0))
    with pytest.raises(SceneError, match="does not intersect"):
        scene.add_layer("off", rgba(4, 4, 0), left=-4)
    with pytest.raises(LayerError, match="opacity"):
        scene.add_layer("x", rgba(4, 4, 0), opacity=2.0)
    assert_unchanged(scene, before, image)
    assert scene.add_layer(layer) is layer
    ids = [la.id for la in scene.draw_order()]
    assert ids.index("temperature") < ids.index("warnings") < ids.index("qpf")


def test_remove_layer(template: Path) -> None:
    scene = Scene.load(template)
    removed = scene.remove_layer("above")
    assert removed.id == "above"
    assert "above" not in scene
    reference = Scene(
        scene.geometry, [la for la in full_scene().layers if la.id != "above"], assets=scene.assets
    )
    np.testing.assert_array_equal(scene.render(), reference.render())
    before, image = snapshot(scene), scene.render()
    with pytest.raises(SceneError, match="colorbar slot 'temperature'"):
        scene.remove_layer("temperature")
    with pytest.raises(KeyError):
        scene.remove_layer("above")
    assert_unchanged(scene, before, image)


def test_remove_needs_no_rendering_or_decoding(
    template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene.load(template)
    overs = spy(monkeypatch, comp, "_over", 1)
    decodes = spy(monkeypatch, cio, "decode_rgba")
    copies = spy(monkeypatch, clayers, "_readonly")
    scene.remove_layer("above")
    scene.hide("below")
    scene.update_layer("cbar", order=1.5, left=12, top=140, opacity=0.25)
    assert (overs, decodes, copies) == ([], [], [])


# --- Visibility, opacity, order, placement ----------------------------------------------


def test_hide_and_show(template: Path) -> None:
    scene = Scene.load(template)
    original = scene.render()
    pixels = scene["above"].pixels
    hidden = scene.hide("above")
    assert not hidden.visible
    assert hidden.pixels is pixels  # the cached raster stays available, uncopied
    without = Scene(
        scene.geometry, [la for la in scene.layers if la.id != "above"], assets=scene.assets
    )
    np.testing.assert_array_equal(scene.render(), without.render())
    scene.show("above")
    np.testing.assert_array_equal(scene.render(), original)


def test_update_layer_fields(template: Path) -> None:
    scene = Scene.load(template)
    old = scene["above"]
    new = scene.update_layer("above", order=-1, left=7, top=-2, opacity=1.0, encoding="png")
    assert (new.order, new.left, new.top, new.opacity, new.encoding) == (-1.0, 7, -2, 1.0, "png")
    assert new.pixels is old.pixels
    assert scene.draw_order()[0] is new
    np.testing.assert_array_equal(
        scene.render(), np.asarray(comp.composite(scene.draw_order(), scene.shape))
    )


@pytest.mark.parametrize(
    ("layer_id", "changes", "error", "match"),
    [
        ("below", {"linewidth": 2}, SceneError, "cannot be restyled"),
        ("cbar", {"cmap": "viridis"}, SceneError, "cannot be restyled"),
        ("temperature", {"vmin": 0.0}, SceneError, "replace the whole layer"),
        ("subtitle", {"value": "new"}, SceneError, "replace the whole layer"),
        ("above", {"pixels": None}, SceneError, "replace_layer"),
        ("above", {"id": "renamed"}, SceneError, "replace the whole layer"),
        ("above", {"opacity": -0.1}, LayerError, "opacity"),
        ("above", {"order": float("nan")}, LayerError, "order"),
        ("above", {"left": 1.5}, LayerError, "integer"),
        ("above", {"encoding": "jpeg"}, LayerError, "encoding"),
        ("above", {"left": 10_000}, SceneError, "does not intersect"),
        ("missing", {"order": 1}, KeyError, "missing"),
    ],
)
def test_refused_updates_leave_scene_unchanged(
    template: Path,
    layer_id: str,
    changes: dict[str, object],
    error: type[Exception],
    match: str,
) -> None:
    scene = Scene.load(template)
    before, image = snapshot(scene), scene.render()
    with pytest.raises(error, match=match):
        scene.update_layer(layer_id, **changes)
    assert_unchanged(scene, before, image)


def test_layers_cannot_be_changed_in_place(template: Path) -> None:
    scene = Scene.load(template)
    with pytest.raises(dataclasses.FrozenInstanceError, match=r"update_layer\('below', visible="):
        scene["below"].visible = False  # type: ignore[misc]
    with pytest.raises(AttributeError, match="cannot be restyled"):
        scene["below"].linewidth = 2  # type: ignore[attr-defined]
    with pytest.raises(AttributeError, match=r"replace_layer\('below', rgba\)"):
        del scene["below"].pixels
    with pytest.raises(ValueError, match="read-only"):
        scene["below"].pixels[0, 0] = 0  # type: ignore[index]
    with pytest.raises(AttributeError):
        scene.geometry = scene.geometry  # type: ignore[misc]


# --- Rendering after edits --------------------------------------------------------------


def test_edits_invalidate_only_the_edited_run(
    template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene.load(template)
    scene.update_layer("subtitle", visible=False)
    scene.render()
    overs = spy(monkeypatch, comp, "_over", 1)
    scene.replace_layer("cbar", rgba(8, 50, 11))
    out = scene.render()
    # The bottom run (below) is reused; the slot and the static layers above it redraw.
    assert [la.id for la in overs] == ["temperature", "above", "cbar"]
    fresh = Scene(scene.geometry, scene.layers, assets=scene.assets)
    np.testing.assert_array_equal(out, fresh.render())


# --- Saving: untouched layers, templates, atomicity -------------------------------------


def edit_scene(scene: Scene) -> None:
    scene.replace_layer("above", rgba(40, 60, 9))
    scene.add_layer("logo", rgba(12, 20, 4), left=200, top=170)
    scene.remove_layer("qpf")
    scene.hide("below")
    scene.update_layer("cbar", order=26.5)


def test_save_reencodes_only_changed_pixels(
    template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene.load(template)
    decodes = spy(monkeypatch, cio, "decode_rgba")
    index_decodes = spy(monkeypatch, cio, "decode_index")
    edit_scene(scene)
    encodes = spy(monkeypatch, cio, "encode_rgba")
    index_encodes = spy(monkeypatch, cio, "encode_index")
    scene.save(tmp_path / "edited.cstack")
    assert (decodes, index_decodes, index_encodes) == ([], [], [])
    assert len(encodes) == 2  # the replaced and the added pixels
    assert {arr.shape for arr in encodes} == {(40, 60, 4), (12, 20, 4)}
    _, old_members = unpack(template)
    _, new_members = unpack(tmp_path / "edited.cstack")
    for name in ("layers/below.rgba8.zz", "layers/cbar.rgba8.zz", "fonts/Test.ttf"):
        assert new_members[name] == old_members[name]
    assert "layers/logo.rgba8.zz" in new_members
    assert new_members["layers/above.rgba8.zz"] != old_members["layers/above.rgba8.zz"]


def test_template_is_unchanged_and_edits_round_trip(template: Path, tmp_path: Path) -> None:
    original = template.read_bytes()
    scene = Scene.load(template)
    edit_scene(scene)
    out = tmp_path / "edited.cstack"
    scene.save(out)
    assert template.read_bytes() == original
    np.testing.assert_array_equal(
        Scene.load(template).render(), Scene.load(template).render(flatten=False)
    )
    reloaded = Scene.load(out)
    assert [la.id for la in reloaded.draw_order()] == [la.id for la in scene.draw_order()]
    assert not reloaded["below"].visible
    np.testing.assert_array_equal(reloaded.render(), scene.render())
    reloaded.save(tmp_path / "again.cstack")
    assert (tmp_path / "again.cstack").read_bytes() == out.read_bytes()
    # A second scene loaded from the template is independent of the edited one.
    np.testing.assert_array_equal(Scene.load(template).render(), full_scene().render())


def test_failed_save_over_template_keeps_it(
    template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = template.read_bytes()
    scene = Scene.load(template)
    edit_scene(scene)

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        scene.save(template)
    assert template.read_bytes() == original
    assert sorted(p.name for p in template.parent.iterdir()) == ["template.cstack"]
    monkeypatch.undo()
    scene.save(template)  # the edited scene is still intact and saveable
    np.testing.assert_array_equal(Scene.load(template).render(), scene.render())


def test_removing_every_layer_cannot_be_saved(template: Path, tmp_path: Path) -> None:
    scene = Scene.load(template)
    for layer in [la for la in scene.layers if la.id != "temperature"]:
        scene.remove_layer(layer.id)
    scene.remove_layer("temperature")
    assert not scene.render().any()
    with pytest.raises(FormatError, match="layers"):
        scene.save(tmp_path / "empty.cstack")
    assert not (tmp_path / "empty.cstack").exists()


def test_scene_is_a_context_manager(template: Path, tmp_path: Path) -> None:
    with Scene.load(template) as scene:
        scene.add_layer("logo", rgba(12, 20, 4), order=93, left=200, top=170)
        scene.save_png(tmp_path / "out.png")
    assert (tmp_path / "out.png").stat().st_size > 0
    assert "logo" in scene  # still usable: eager loading holds nothing open
