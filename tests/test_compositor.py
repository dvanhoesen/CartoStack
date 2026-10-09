"""Compositing, placement, flattened-run reuse, and PNG output."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from helpers import full_scene, gradient
from PIL import Image

from cartostack import CanvasGeometry, PolygonSlot, RasterLayer, Scene, TextSlot
from cartostack import compositor as comp
from cartostack.layers import Layer
from cartostack.manifest import Bin

H, W = 48, 64


def geometry(
    width: int = W, height: int = H, axes: tuple[float, ...] | None = None
) -> CanvasGeometry:
    left, top, aw, ah = axes or (0.0, 0.0, float(width), float(height))
    return CanvasGeometry.from_dict(
        {
            "canvas": {"width": width, "height": height, "dpi": 100.0},
            "axes": {"left": left, "top": top, "width": aw, "height": ah},
        }
    )


def make_scene(layers: list[Layer], g: CanvasGeometry | None = None) -> Scene:
    return Scene(g or geometry(), layers, assets={"f.ttf": b"font bytes"})


def random_rgba(rng: np.random.Generator, height: int, width: int) -> np.ndarray:
    """Random pixels with many fully transparent, fully opaque, and partial alpha values."""
    px = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
    pick = rng.random((height, width))
    px[pick < 0.2, 3] = 0
    px[pick > 0.8, 3] = 255
    return px


def text(layer_id: str, order: float, pixels: np.ndarray, **kw: object) -> TextSlot:
    return TextSlot(
        id=layer_id, order=order, value="x", font="f.ttf", size_px=10.0, pixels=pixels, **kw
    )


# --- Independent references ------------------------------------------------------------


def over_int(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """Pillow's integer source-over (libImaging/AlphaComposite.c), re-derived in NumPy."""
    d, s = dst.astype(np.int64), src.astype(np.int64)
    sa, da = s[..., 3:], d[..., 3:]
    outa255 = sa * 255 + da * (255 - sa)
    coef1 = sa * 255 * 255 * 128 // np.where(outa255 == 0, 1, outa255)
    coef2 = 255 * 128 - coef1

    def div255(x: np.ndarray) -> np.ndarray:
        return ((x >> 8) + x) >> 8

    rgb = div255(s[..., :3] * coef1 + d[..., :3] * coef2 + (0x80 << 7)) >> 7
    out = np.concatenate([rgb, div255(outa255 + 0x80)], axis=-1).astype(np.uint8)
    return np.where(sa == 0, dst, out)


def over_float(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """Textbook straight-alpha Porter-Duff source-over, rounded to 8 bits."""
    d, s = dst.astype(np.float64) / 255, src.astype(np.float64) / 255
    sa, da = s[..., 3:], d[..., 3:]
    oa = sa + da * (1 - sa)
    rgb = (s[..., :3] * sa + d[..., :3] * da * (1 - sa)) / np.where(oa == 0, 1, oa)
    out = np.concatenate([rgb, oa], axis=-1)
    return np.where(oa == 0, 0, np.floor(out * 255 + 0.5)).astype(np.uint8)


def premultiplied_diff(a: np.ndarray, b: np.ndarray) -> int:
    """Largest channel difference, ignoring RGB of fully transparent pixels (meaningless)."""
    a, b = a.astype(int), b.astype(int)
    a[a[..., 3] == 0] = 0
    b[b[..., 3] == 0] = 0
    return int(np.abs(a - b).max())


def reference(
    layers: list[Layer],
    shape: tuple[int, int],
    over: Callable[[np.ndarray, np.ndarray], np.ndarray] = over_int,
) -> np.ndarray:
    """Stack by placing each layer on a full transparent canvas, then compositing."""
    canvas = np.zeros((*shape, 4), np.uint8)
    for _, layer in sorted(enumerate(layers), key=lambda item: (item[1].order, item[0])):
        if not layer.visible or layer.pixels is None:
            continue
        px = layer.pixels.copy()
        px[..., 3] = np.floor(px[..., 3] * layer.opacity + 0.5).astype(np.uint8)
        full = np.zeros_like(canvas)
        for y in range(px.shape[0]):
            for x in range(px.shape[1]):
                cy, cx = layer.top + y, layer.left + x
                if 0 <= cy < shape[0] and 0 <= cx < shape[1]:
                    full[cy, cx] = px[y, x]
        canvas = over(canvas, full)
    return canvas


def mixed_layers(rng: np.random.Generator) -> list[Layer]:
    """Overlapping, offset (some partly off-canvas), translucent, hidden, and tied layers."""
    return [
        RasterLayer(id="base", order=0, pixels=random_rgba(rng, H, W)),
        RasterLayer(id="patch", order=5, left=-7, top=-3, pixels=random_rgba(rng, 20, 30)),
        text("slot", 10, random_rgba(rng, 10, 40), left=40, top=30),
        RasterLayer(id="faded", order=20, opacity=0.37, pixels=random_rgba(rng, H, W)),
        RasterLayer(id="hidden", order=20, visible=False, pixels=random_rgba(rng, H, W)),
        RasterLayer(id="corner", order=20, left=50, top=40, pixels=random_rgba(rng, 25, 25)),
        RasterLayer(id="under", order=1, left=10, top=10, pixels=random_rgba(rng, 5, 5)),
    ]


# --- Compositing rules ------------------------------------------------------------------


def test_reference_formula_is_pillow() -> None:
    rng = np.random.default_rng(0)
    dst, src = random_rgba(rng, 40, 50), random_rgba(rng, 40, 50)
    img = Image.fromarray(dst, "RGBA")
    img.alpha_composite(Image.fromarray(src, "RGBA"))
    np.testing.assert_array_equal(np.asarray(img), over_int(dst, src))
    # And Pillow is within one level of exact straight-alpha arithmetic.
    assert premultiplied_diff(np.asarray(img), over_float(dst, src)) <= 1


def test_hand_computed_pixels() -> None:
    red = np.array([[[255, 0, 0, 255]]], np.uint8)
    half_green = np.array([[[0, 255, 0, 128]]], np.uint8)
    scene = Scene(
        geometry(1, 1),
        [
            RasterLayer(id="green", order=1, pixels=half_green),
            RasterLayer(id="red", order=0, pixels=red),
        ],
    )
    # 128/255 green over opaque red: RGB = 255·(1 - 128/255), 255·128/255; alpha stays opaque.
    np.testing.assert_array_equal(scene.render()[0, 0], [127, 128, 0, 255])
    alone = Scene(geometry(1, 1), [RasterLayer(id="g", order=0, pixels=half_green)])
    np.testing.assert_array_equal(alone.render()[0, 0], [0, 255, 0, 128])
    faded = Scene(geometry(1, 1), [RasterLayer(id="r", order=0, pixels=red, opacity=0.5)])
    np.testing.assert_array_equal(faded.render()[0, 0], [255, 0, 0, 128])  # floor(127.5 + 0.5)


@pytest.mark.parametrize("flatten", [True, False])
def test_matches_independent_reference(flatten: bool) -> None:
    rng = np.random.default_rng(1)
    layers = mixed_layers(rng)
    out = make_scene(layers).render(flatten=flatten)
    expected = reference(layers, (H, W))
    if flatten:
        # faded/hidden/corner form a flattened run above the slot: rounding only.
        assert np.abs(out.astype(int) - expected).max() <= 3
    else:
        np.testing.assert_array_equal(out, expected)
    assert premultiplied_diff(out, reference(layers, (H, W), over_float)) <= 3


def test_order_ties_keep_list_order() -> None:
    a = np.full((H, W, 4), [255, 0, 0, 255], np.uint8)
    b = np.full((H, W, 4), [0, 0, 255, 255], np.uint8)
    first = Scene(
        geometry(), [RasterLayer(id="a", order=1, pixels=a), RasterLayer(id="b", order=1, pixels=b)]
    )
    second = Scene(
        geometry(), [RasterLayer(id="b", order=1, pixels=b), RasterLayer(id="a", order=1, pixels=a)]
    )
    np.testing.assert_array_equal(first.render()[0, 0], [0, 0, 255, 255])
    np.testing.assert_array_equal(second.render()[0, 0], [255, 0, 0, 255])


def test_hidden_transparent_and_empty_layers_change_nothing() -> None:
    rng = np.random.default_rng(2)
    base = RasterLayer(id="base", order=0, pixels=random_rgba(rng, H, W))
    expected = make_scene([base]).render()
    others: list[Layer] = [
        RasterLayer(id="hidden", order=1, pixels=random_rgba(rng, H, W), visible=False),
        RasterLayer(id="clear", order=2, pixels=np.zeros((H, W, 4), np.uint8)),
        RasterLayer(id="zero", order=3, pixels=random_rgba(rng, H, W), opacity=0.0),
        text("empty", 4, np.zeros((3, 3, 4), np.uint8)),
    ]
    np.testing.assert_array_equal(make_scene([base, *others]).render(), expected)


def test_slots_without_pixels_draw_nothing() -> None:
    g = full_scene().geometry
    slot = PolygonSlot(id="qpf", order=1, bins=(Bin(0.0, 1.0, (0, 0, 255, 255), 1),))
    assert slot.pixels is None
    out = Scene(g, [slot]).render()
    assert out.shape == (g.height, g.width, 4)
    assert not out.any()


# --- Placement --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "top", "height", "width"),
    [
        (5, 7, 10, 12),  # inside
        (-4, -6, 10, 12),  # off the top-left
        (W - 5, H - 3, 10, 12),  # off the bottom-right
        (-10, -10, H + 20, W + 20),  # larger than the canvas
        (W - 1, 0, 1, 50),  # one column on the canvas
    ],
)
def test_cropped_layer_equals_full_canvas_layer(
    left: int, top: int, height: int, width: int
) -> None:
    rng = np.random.default_rng(3)
    base = RasterLayer(id="base", order=0, pixels=random_rgba(rng, H, W))
    small = random_rgba(rng, height, width)
    padded = np.zeros((H + 2 * 60, W + 2 * 60, 4), np.uint8)
    padded[60 + top : 60 + top + height, 60 + left : 60 + left + width] = small
    full = padded[60 : 60 + H, 60 : 60 + W]
    cropped = Scene(
        geometry(), [base, RasterLayer(id="top", order=1, left=left, top=top, pixels=small)]
    )
    whole = make_scene([base, RasterLayer(id="top", order=1, pixels=full)])
    np.testing.assert_array_equal(cropped.render(), whole.render())


def test_window() -> None:
    assert comp.window(5, 7, 10, 12, (H, W)) == comp.Window((0, 0, 12, 10), (5, 7))
    win = comp.window(-4, -6, 10, 12, (H, W))
    assert win == comp.Window((4, 6, 12, 10), (0, 0))
    assert win.box == (0, 0, 8, 4)
    assert comp.window(W, 0, 5, 5, (H, W)) is None
    assert comp.window(-5, 0, 5, 5, (H, W)) is None


def test_map_clip_is_not_applied_to_raster_layers() -> None:
    # Raster layers carry their own clipping; the axes clip is for slot rendering only.
    g = geometry(axes=(10.0, 10.0, 20.0, 20.0))
    px = np.full((H, W, 4), 200, np.uint8)
    out = Scene(g, [RasterLayer(id="r", order=0, pixels=px)]).render()
    np.testing.assert_array_equal(out, px)


# --- Flattened runs ---------------------------------------------------------------------


def count_overs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = comp._over

    def spy(canvas: Image.Image, layer: Layer, origin: tuple[int, int] = (0, 0)) -> None:
        calls.append(layer.id)
        original(canvas, layer, origin)

    monkeypatch.setattr(comp, "_over", spy)
    return calls


def stacked_scene(rng: np.random.Generator) -> list[Layer]:
    return [
        RasterLayer(id="b0", order=0, pixels=random_rgba(rng, H, W)),
        RasterLayer(id="b1", order=1, left=3, top=4, pixels=random_rgba(rng, 20, 20)),
        text("slot", 2, random_rgba(rng, H, W)),
        RasterLayer(id="a0", order=3, left=-5, top=10, pixels=random_rgba(rng, 15, 30)),
        RasterLayer(id="a1", order=4, opacity=0.6, pixels=random_rgba(rng, H, W)),
        RasterLayer(id="a2", order=5, left=40, top=30, pixels=random_rgba(rng, 30, 40)),
        text("title", 6, random_rgba(rng, 8, 20), left=2, top=2),
        RasterLayer(id="last", order=7, pixels=random_rgba(rng, 5, 5)),
    ]


def test_static_runs_are_flattened_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = count_overs(monkeypatch)
    scene = make_scene(stacked_scene(np.random.default_rng(4)))
    first = scene.render()
    # b0 covers the canvas, so the bottom run starts from its pixels instead of an _over.
    assert sorted(calls) == sorted(["b1", "slot", "a0", "a1", "a2", "title", "last"])
    calls.clear()
    second = scene.render()
    assert calls == ["slot", "title", "last"]  # slots and single static layers only
    np.testing.assert_array_equal(first, second)


def test_flattening_is_exact_below_slots_and_bounded_above() -> None:
    layers = stacked_scene(np.random.default_rng(5))
    scene = make_scene(layers)
    flat, single = scene.render(), scene.render(flatten=False)
    np.testing.assert_array_equal(single, reference(layers, (H, W)))
    diff = np.abs(flat.astype(int) - single.astype(int))
    assert diff.max() <= 3
    # Only the flattened run a0..a2 above the first slot may differ.
    below = make_scene(layers[:3])
    np.testing.assert_array_equal(below.render(), below.render(flatten=False))


def test_flattening_error_stress() -> None:
    rng = np.random.default_rng(6)
    worst, differing = 0, 0
    for _ in range(200):
        layers: list[Layer] = [text("slot", 0, random_rgba(rng, 32, 32))]
        layers += [
            RasterLayer(id=f"a{i}", order=i + 1, pixels=random_rgba(rng, 32, 32))
            for i in range(int(rng.integers(2, 5)))
        ]
        flat = comp.Compositor((32, 32)).render(layers)
        diff = np.abs(np.asarray(flat).astype(int) - np.asarray(comp.composite(layers, (32, 32))))
        worst = max(worst, int(diff.max()))
        differing += int((diff >= 2).sum())
    assert worst <= 3
    assert differing / (200 * 32 * 32 * 4) < 0.002


def test_opaque_upper_run_is_exact() -> None:
    rng = np.random.default_rng(7)
    layers: list[Layer] = [
        text("slot", 0, random_rgba(rng, H, W)),
        RasterLayer(id="a", order=1, pixels=np.full((10, 10, 4), 255, np.uint8)),
        RasterLayer(
            id="b", order=2, left=5, pixels=np.full((10, 10, 4), [99, 50, 20, 255], np.uint8)
        ),
    ]
    out = comp.Compositor((H, W)).render(layers)
    np.testing.assert_array_equal(np.asarray(out), np.asarray(comp.composite(layers, (H, W))))


def test_changed_layer_invalidates_its_run(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(8)
    layers = stacked_scene(rng)
    compositor = comp.Compositor((H, W))
    compositor.render(layers)
    calls = count_overs(monkeypatch)
    # Replace a1 (in the upper run) with a new object, as an edit would.
    layers[4] = RasterLayer(id="a1", order=4, pixels=random_rgba(rng, H, W))
    out = compositor.render(layers)
    assert calls == ["slot", "a0", "a1", "a2", "title", "last"]  # bottom run reused
    fresh = comp.Compositor((H, W)).render(layers)
    np.testing.assert_array_equal(np.asarray(out), np.asarray(fresh))


def test_render_returns_independent_arrays() -> None:
    scene = make_scene(stacked_scene(np.random.default_rng(9)))
    first = scene.render()
    expected = first.copy()
    assert first.flags.writeable
    assert first.dtype == np.uint8
    assert first.shape == (H, W, 4)
    first[:] = 0
    np.testing.assert_array_equal(scene.render(), expected)


def test_render_after_save_and_load(tmp_path: Path) -> None:
    scene = full_scene()
    scene.save(tmp_path / "s.cstack")
    loaded = Scene.load(tmp_path / "s.cstack")
    np.testing.assert_array_equal(loaded.render(), scene.render())
    np.testing.assert_array_equal(loaded.render(), loaded.render(flatten=False))


# --- PNG output -------------------------------------------------------------------------


def decode(path: Path) -> tuple[str, np.ndarray]:
    with Image.open(path) as img:
        return img.mode, np.asarray(img)


def test_png_rgba_round_trips_exactly(tmp_path: Path) -> None:
    scene = full_scene()
    scene.save_png(tmp_path / "out.png")
    mode, px = decode(tmp_path / "out.png")
    assert mode == "RGBA"
    assert px.shape == (scene.geometry.height, scene.geometry.width, 4)
    np.testing.assert_array_equal(px, scene.render())


def test_png_rgb_composites_over_background(tmp_path: Path) -> None:
    rng = np.random.default_rng(10)
    scene = make_scene([RasterLayer(id="r", order=0, pixels=random_rgba(rng, H, W))])
    rendered = scene.render()
    for background in [(255, 255, 255), (10, 20, 30)]:
        scene.save_png(tmp_path / "rgb.png", mode="RGB", background=background)
        mode, px = decode(tmp_path / "rgb.png")
        assert mode == "RGB"
        base = np.empty_like(rendered)
        base[:] = (*background, 255)
        np.testing.assert_array_equal(px, over_int(base, rendered)[..., :3])


def test_png_rgb_of_opaque_canvas_drops_alpha(tmp_path: Path) -> None:
    px = gradient(H, W)
    px[..., 3] = 255
    opaque = make_scene([RasterLayer(id="r", order=0, pixels=px)])
    opaque.save_png(tmp_path / "o.png", mode="RGB", background=(1, 2, 3))
    np.testing.assert_array_equal(decode(tmp_path / "o.png")[1], px[..., :3])


@pytest.mark.parametrize("level", [0, 1, 6, 9])
def test_png_compress_level_changes_size_not_pixels(tmp_path: Path, level: int) -> None:
    scene = full_scene()
    scene.save_png(tmp_path / f"{level}.png", compress_level=level)
    np.testing.assert_array_equal(decode(tmp_path / f"{level}.png")[1], scene.render())


def test_png_sizes_shrink_with_level() -> None:
    image = Image.fromarray(full_scene().render(), "RGBA")
    sizes = [len(comp.encode_png(image, compress_level=lv)) for lv in (0, 1, 9)]
    assert sizes[0] > sizes[1] >= sizes[2]


@pytest.mark.parametrize(
    "kwargs",
    [{"mode": "L"}, {"compress_level": 10}, {"compress_level": -1}, {"background": (0, 0)}],
)
def test_png_rejects_bad_options(tmp_path: Path, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=next(iter(kwargs))):
        full_scene().save_png(tmp_path / "x.png", **kwargs)  # type: ignore[arg-type]
    assert not list(tmp_path.iterdir())


def test_png_failed_write_leaves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out.png"
    target.write_bytes(b"previous")

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        full_scene().save_png(target)
    assert target.read_bytes() == b"previous"
    assert [p.name for p in tmp_path.iterdir()] == ["out.png"]


def test_png_bytes_are_deterministic() -> None:
    image = Image.fromarray(full_scene().render(), "RGBA")
    assert comp.encode_png(image) == comp.encode_png(image)
    with Image.open(io.BytesIO(comp.encode_png(image, mode="RGB"))) as img:
        assert img.size == image.size


# --- Dependency boundary ----------------------------------------------------------------


def test_render_uses_only_core_dependencies(tmp_path: Path) -> None:
    src = tmp_path / "s.cstack"
    full_scene().save(src)
    code = (
        "import json, sys; from cartostack import Scene;"
        f"Scene.load({str(src)!r}).save_png({str(tmp_path / 'o.png')!r});"
        "print(json.dumps(sorted(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    loaded = {name.split(".")[0] for name in json.loads(out)}
    assert loaded.isdisjoint({"matplotlib", "cartopy", "pyproj", "shapely"})
    np.testing.assert_array_equal(decode(tmp_path / "o.png")[1], Scene.load(src).render())


@pytest.mark.parametrize("opacity", [1.0, 0.5])
@pytest.mark.parametrize("placed", [False, True])
def test_full_canvas_bottom_layer_fast_path_is_exact(opacity: float, placed: bool) -> None:
    """Starting from a full-canvas bottom layer equals stacking it on a transparent canvas."""
    rng = np.random.default_rng(9)
    px = random_rgba(rng, H, W)
    px[::3, :, 3] = 0  # transparent pixels with non-zero colour
    px[:, ::4, 3] = 255
    bottom = RasterLayer(id="b", order=0, pixels=px, opacity=opacity,
                         left=1 if placed else 0)  # fmt: skip
    top = RasterLayer(id="t", order=1, left=5, top=6, pixels=random_rgba(rng, 20, 30))
    got = np.asarray(comp.composite([bottom, top], (H, W)))
    want = transparent_reference([bottom, top])
    np.testing.assert_array_equal(got, want)


def transparent_reference(layers: list[Layer]) -> np.ndarray:
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for layer in layers:
        assert layer.pixels is not None
        src = comp.apply_opacity(layer.pixels, layer.opacity)
        win = comp.window(layer.left, layer.top, *src.shape[:2], (H, W))
        assert win is not None
        x0, y0, x1, y1 = win.src
        canvas.alpha_composite(
            Image.fromarray(np.ascontiguousarray(src[y0:y1, x0:x1])), dest=win.dest
        )
    return np.asarray(canvas)
