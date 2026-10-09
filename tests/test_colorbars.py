"""Colorbars: Matplotlib's tick rules, redraw from parts, Scene behaviour, and Matplotlib checks."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from helpers import gradient, map_geometry, unpack
from PIL import ImageFont

from cartostack import (
    ColorbarLayer,
    ColorbarRedraw,
    ColorbarRedrawError,
    GridSlot,
    RasterLayer,
    Scene,
)
from cartostack import colorbars as cb

FONT = ImageFont.load_default(10).path.getvalue()  # type: ignore[union-attr]
MINUS = "\u2212"


# --- Ticks and labels ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("vmin", "vmax", "nbins", "ticks"),
    [
        (-10.0, 35.0, 8, [-10, 0, 10, 20, 30, 40]),
        (-5.0, 30.0, 8, [-5, 0, 5, 10, 15, 20, 25, 30]),
        (0.0, 1.0, 8, [0, 0.2, 0.4, 0.6, 0.8, 1.0]),
        (-1.0, 1.0, 9, [-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0]),
        (250.0, 310.0, 8, [250, 260, 270, 280, 290, 300, 310]),
        (-0.3, 0.45, 4, [-0.4, -0.2, 0.0, 0.2, 0.4, 0.6]),
    ],
)
def test_tick_values(vmin: float, vmax: float, nbins: int, ticks: list[float]) -> None:
    got = cb.tick_values(vmin, vmax, nbins, (1, 2, 2.5, 5, 10))
    np.testing.assert_allclose(got, ticks, atol=1e-12)


@pytest.mark.parametrize(
    ("locs", "vmin", "vmax", "labels"),
    [
        ([-10, 0, 10, 20, 30, 40], -10, 35, [f"{MINUS}10", "0", "10", "20", "30", "40"]),
        ([0, 0.2, 0.4], 0, 1, ["0.0", "0.2", "0.4"]),
        ([0, 0.125, 0.25], 0, 1, ["0.000", "0.125", "0.250"]),
        ([-0.2, 0.0, 0.2], -0.3, 0.45, [f"{MINUS}0.2", "0.0", "0.2"]),
        ([2.5, 5.0, 7.5], 2, 8, ["2.5", "5.0", "7.5"]),
    ],
)
def test_tick_labels(locs: list[float], vmin: float, vmax: float, labels: list[str]) -> None:
    assert cb.tick_labels(np.asarray(locs, float), vmin, vmax) == labels


@pytest.mark.parametrize(("vmin", "vmax"), [(1000.0, 1000.5), (0.0, 2e7), (0.0, 3e-6)])
def test_offset_or_scientific_labels_are_refused(vmin: float, vmax: float) -> None:
    locs = cb.tick_values(vmin, vmax, 8, (1, 2, 2.5, 5, 10))
    with pytest.raises(ColorbarRedrawError, match="offset or scientific"):
        cb.tick_labels(locs, vmin, vmax)


def test_fixed_ticks_keep_their_values_inside_the_range() -> None:
    r = redraw(locator="fixed", values=(-10.0, 0.0, 12.5, 50.0))
    locs, labels = cb.visible_ticks(r, -10.0, 35.0)
    np.testing.assert_array_equal(locs, [-10.0, 0.0, 12.5])
    assert labels == [f"{MINUS}10.0", "0.0", "12.5"]


# --- Redraw ----------------------------------------------------------------------------------

LUT = np.vstack([np.c_[np.arange(0, 256, 64), np.zeros((4, 2)), np.full(4, 255)],
                 [[0, 0, 255, 255], [255, 0, 0, 255], [0, 0, 0, 0]]]).astype(np.uint8)  # fmt: skip
H, W = 40, 120


def redraw(**kw: Any) -> ColorbarRedraw:
    rows = np.full((H, W), -1, np.int32)
    rows[10:18, 20:100] = (np.arange(80) * 4 // 80).astype(np.int32)  # 4 bands
    rows[10:18, 14:20] = 4  # under triangle (as a block here)
    rows[10:18, 100:106] = 5  # over
    over = np.zeros((H, W, 4), np.uint8)
    over[9, 20:100] = (0, 0, 0, 255)  # outline top edge
    params: dict[str, Any] = {
        "orientation": "horizontal", "box": (20.0, 10.0, 80.0, 8.0), "rows": rows, "n_colors": 4,
        "over": over, "nbins": 4, "side": "bottom", "direction": "out", "tick_length": 3.0,
        "tick_width": 1.0, "font": "f.ttf", "size_px": 9.0, "pad": 2.0,
    }  # fmt: skip
    return ColorbarRedraw(**{**params, **kw})


def scene(r: ColorbarRedraw | None = None, alpha: float | None = None) -> Scene:
    g = map_geometry(width=W, height=H + 20)
    imap = np.full((H + 20, W), -1, np.int32)
    imap[25:55, 10:110] = 0
    grid = GridSlot(id="t", order=10, shape=(1, 1), index_map=imap, lut=LUT, vmin=0.0, vmax=4.0,
                    alpha=alpha)  # fmt: skip
    bar = ColorbarLayer(id="cbar", order=20, slot="t", pixels=gradient(H, W), redraw=r)
    return Scene(g, [RasterLayer(id="bg", order=0, pixels=gradient(H + 20, W)), grid, bar],
                 assets={"f.ttf": FONT})  # fmt: skip


def test_strip_is_recoloured_from_the_lut_with_slot_alpha() -> None:
    s = scene(redraw(), alpha=0.8)
    px = cb.render(s["cbar"], s["t"], FONT)
    assert tuple(px[12, 25]) == (0, 0, 0, 204)  # band 0, alpha 0.8
    assert tuple(px[12, 95]) == (192, 0, 0, 204)  # band 3
    assert tuple(px[12, 16]) == (0, 0, 255, 204)  # under
    assert tuple(px[12, 103]) == (255, 0, 0, 204)  # over
    assert tuple(px[9, 50]) == (0, 0, 0, 255)  # outline over the strip


def test_lut_with_another_size_rebands_by_position() -> None:
    s = scene(redraw())
    lut8 = np.vstack(
        [np.c_[np.arange(0, 256, 32), np.zeros((8, 2)), np.full(8, 255)], LUT[4:]]
    ).astype(np.uint8)
    s.replace_grid("t", np.zeros((1, 1)), lut=lut8)
    px = s["cbar"].pixels
    assert px is not None
    assert [int(px[12, x, 0]) for x in (20, 30, 40, 99)] == [0, 32, 64, 224]


def test_ticks_and_labels_follow_the_new_range() -> None:
    s = scene(redraw())
    s.replace_grid("t", np.zeros((1, 1)), vmin=0.0, vmax=4.0)  # unchanged: nothing redrawn
    assert s["cbar"].pixels is not None
    np.testing.assert_array_equal(s["cbar"].pixels, gradient(H, W))
    s.replace_grid("t", np.zeros((1, 1)), vmin=0.0, vmax=2.0)
    px = s["cbar"].pixels
    assert px is not None
    locs, labels = cb.visible_ticks(s["cbar"].redraw, 0.0, 2.0)
    for v in locs:  # each tick is a dark mark just below the strip
        x = int(np.floor(20 + v / 2.0 * 80))
        assert px[19, x, 3] == 255
    assert labels[0] == "0.0"
    assert px[24:34, 10:110, 3].any()  # labels under the ticks
    assert s.stale_colorbars == ()


def test_colorbar_without_redraw_data_is_stale_and_refuses_redraw() -> None:
    s = scene(None)
    before = s["cbar"]
    s.replace_grid("t", np.zeros((1, 1)), vmin=1.0)
    assert s["cbar"] is before
    assert s.stale_colorbars == ("cbar",)
    with pytest.raises(ColorbarRedrawError, match="no redraw data"):
        s.redraw_colorbar("cbar")


def test_range_needing_an_offset_leaves_the_colorbar_stale() -> None:
    s = scene(redraw())
    before = s["cbar"]
    s.replace_grid("t", np.zeros((1, 1)), vmin=1000.0, vmax=1000.5)
    assert s["cbar"] is before
    assert s.stale_colorbars == ("cbar",)
    s.replace_grid("t", np.zeros((1, 1)), vmin=0.0, vmax=3.0)
    assert s.stale_colorbars == ()


def test_redraw_round_trips_through_a_file(tmp_path: Path) -> None:
    s = scene(redraw(under=gradient(H, W) // 4))
    path = tmp_path / "c.cstack"
    s.save(path)
    manifest, _ = unpack(path)
    rec = next(r for r in manifest["layers"] if r["id"] == "cbar")
    assert rec["colorbar"]["redraw"]["rows"]["n_colors"] == 4
    assert rec["colorbar"]["redraw"]["labels"]["font"] == "f.ttf"
    loaded = Scene.load(path)
    r = loaded["cbar"].redraw
    assert r is not None
    np.testing.assert_array_equal(r.rows, s["cbar"].redraw.rows)
    np.testing.assert_array_equal(r.under, s["cbar"].redraw.under)
    assert (r.minus, r.nbins, r.steps) == (MINUS, 4, (1.0, 2.0, 2.5, 5.0, 10.0))
    loaded.replace_grid("t", np.zeros((1, 1)), vmin=0.0, vmax=2.0)
    s.replace_grid("t", np.zeros((1, 1)), vmin=0.0, vmax=2.0)
    np.testing.assert_array_equal(loaded.render(), s.render())
    assert "f.ttf" in loaded.assets  # the font stays an asset


def test_redraw_data_must_fit_the_layer() -> None:
    with pytest.raises(ValueError, match="redraw data"):
        ColorbarLayer(id="c", order=0, slot="t", pixels=gradient(10, 10), redraw=redraw())


# --- Matplotlib -----------------------------------------------------------------------------


@pytest.mark.build
def test_ticks_and_labels_equal_matplotlib_on_random_ranges() -> None:
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(6, 1), dpi=100)
    ax = fig.add_axes((0.1, 0.5, 0.8, 0.3))
    rng = np.random.default_rng(0)
    checked = 0
    for _ in range(1500):
        c = rng.choice([0, 1, -1]) * 10 ** rng.uniform(-3, 7)
        span = 10 ** rng.uniform(-4, 6)
        vmin = float(c - span * rng.uniform(0, 1))
        vmax = vmin + span
        ax.set_xlim(vmin, vmax)
        nbins = int(np.clip(ax.xaxis.get_tick_space(), 1, 9))
        want = ax.xaxis.get_major_locator().tick_values(vmin, vmax)
        got = cb.tick_values(vmin, vmax, nbins, (1, 2, 2.5, 5, 10))
        np.testing.assert_allclose(got, want, rtol=0, atol=1e-12 * max(1, abs(vmin), abs(vmax)))
        fmt = ax.xaxis.get_major_formatter()
        fmt.set_locs(want)
        try:
            ours = cb.tick_labels(got, vmin, vmax)
        except ColorbarRedrawError:
            assert fmt.get_offset() != ""
            continue
        assert ours == [fmt(t) for t in want]
        checked += 1
    assert checked > 1000
    plt.close(fig)


def _colorbar_figure(vmin: float, vmax: float, orientation: str) -> tuple[Any, Any, Any, Any]:
    ccrs = pytest.importorskip("cartopy.crs")
    from cartostack.build import SceneBuilder

    proj = ccrs.LambertConformal(central_longitude=-76, central_latitude=42.5,
                                 standard_parallels=(40.5, 44.5))  # fmt: skip
    b = SceneBuilder.new(proj, (-80.5, -71.5, 40.2, 45.3), width=600, height=460, dpi=100,
                         axes=(0.05, 0.25, 0.75, 0.7))  # fmt: skip
    lon, lat = np.linspace(-81, -71, 40), np.linspace(40, 46, 30)
    vals = np.add.outer(np.linspace(-5, 30, 30), np.linspace(0, 10, 40)).astype(np.float32)
    mesh = b.ax.pcolormesh(lon, lat, vals, transform=ccrs.PlateCarree(), cmap="coolwarm", vmin=vmin,
                           vmax=vmax, shading="nearest", alpha=0.8, zorder=2)  # fmt: skip
    rect = (0.15, 0.12, 0.6, 0.03) if orientation == "horizontal" else (0.86, 0.25, 0.025, 0.65)
    cax = b.fig.add_axes(rect)
    cbar = b.fig.colorbar(mesh, cax=cax, orientation=orientation, extend="both")
    cbar.set_label("°C", fontsize=10)
    cbar.ax.tick_params(labelsize=9)
    return b, mesh, cbar, (lon, lat, vals)


def _cax_pixels(b: Any, layer: ColorbarLayer) -> np.ndarray:
    from PIL import Image

    b.fig.patch.set_visible(False)
    b.ax.set_visible(False)
    buf = io.BytesIO()
    b.fig.savefig(buf, format="png", dpi=100, transparent=True)
    a = np.asarray(Image.open(buf).convert("RGBA")).astype(int)
    assert layer.pixels is not None
    h, w = layer.pixels.shape[:2]
    return a[layer.top : layer.top + h, layer.left : layer.left + w]


@pytest.mark.build
@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_authored_colorbar_redraw_matches_matplotlib(orientation: str) -> None:
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    b, mesh, cbar, (lon, lat, vals) = _colorbar_figure(-10.0, 35.0, orientation)
    b.add_static("below", order=0, background=True, zorder=(-1e9, 2))
    b.add_grid_slot("t2m", order=10, lon=lon, lat=lat, cmap="coolwarm", vmin=-10, vmax=35,
                    alpha=0.8, values=vals, artists=[mesh])  # fmt: skip
    b.add_colorbar("cbar", cbar, slot="t2m", order=20)
    s = b.build(rest="below")
    layer = s["cbar"]
    r = layer.redraw
    assert r is not None
    assert r.orientation == orientation
    assert r.side == ("bottom" if orientation == "horizontal" else "right")
    for vmin, vmax in [(-10.0, 35.0), (-5.0, 30.0), (0.0, 1.0), (250.0, 310.0)]:
        new = s["t2m"]
        assert isinstance(new, GridSlot)
        s.replace_grid("t2m", vals, vmin=vmin, vmax=vmax)
        ours = cb.render(layer, s["t2m"], s.assets[r.font]).astype(int)
        b2, *_ = _colorbar_figure(vmin, vmax, orientation)
        ref = _cax_pixels(b2, layer)
        plt.close(b2.fig)
        strip = r.rows >= 0
        d = np.abs(ours - ref).max(-1)
        assert (d[strip] > 32).mean() < 0.01  # the colour strip
        ink = (ours[..., 3] > 0) | (ref[..., 3] > 0)
        assert abs(ours[..., 3].sum() / ref[..., 3].sum() - 1) < 0.03  # same amount of ink
        yy, xx = np.mgrid[: d.shape[0], : d.shape[1]]
        outside = ink & ~strip
        for a in (ours, ref):
            a[..., 3] = np.where(outside, a[..., 3], 0)
        cx = [(xx * a[..., 3]).sum() / a[..., 3].sum() for a in (ours, ref)]
        cy = [(yy * a[..., 3]).sum() / a[..., 3].sum() for a in (ours, ref)]
        assert abs(cx[0] - cx[1]) < 0.15  # ticks and labels where Matplotlib puts them
        assert abs(cy[0] - cy[1]) < 0.15
    assert s.stale_colorbars == ()
