"""Text slots: fonts, Matplotlib 3.11 layout, rendering, Scene use, and Matplotlib checks."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from helpers import gradient, map_geometry
from PIL import Image, ImageFont

from cartostack import RasterLayer, Scene, SceneError, TextFontError, TextSlot
from cartostack import _opentype as ot
from cartostack import text as ctext


def pillow_font() -> bytes:
    """Pillow's bundled TrueType font (has GPOS kerning and ligatures): no extra fixture."""
    path = ImageFont.load_default(10).path
    assert isinstance(path, io.BytesIO)
    return path.getvalue()


FONT = pillow_font()
CANVAS = (120, 400)


def slot(**kw: Any) -> TextSlot:
    params = {"id": "t", "order": 30, "value": "Valid 12Z", "font": "f.ttf", "size_px": 30.0,
              "x": 200.0, "y": 60.0, "snap": None, "offset": (0.0, 0.0)}  # fmt: skip
    return TextSlot(**{**params, **kw})


def alpha_canvas(
    s: TextSlot, value: str | None = None, canvas: tuple[int, int] = CANVAS
) -> np.ndarray:
    left, top, px = ctext.render(s, FONT, canvas, value)
    out = np.zeros(canvas, float)
    if px is not None:
        out[top : top + px.shape[0], left : left + px.shape[1]] = px[..., 3]
    return out


def centroid(a: np.ndarray) -> tuple[float, float]:
    ys, xs = np.mgrid[: a.shape[0], : a.shape[1]]
    return float((xs * a).sum() / a.sum()), float((ys * a).sum() / a.sum())


def ink_box(a: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(a)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# --- Fonts ---------------------------------------------------------------------------------


def test_missing_font_is_an_error() -> None:
    with pytest.raises(TextFontError, match=r"'f\.ttf' is not among the scene's assets"):
        ctext.render(slot(), None, CANVAS)


@pytest.mark.parametrize("data", [b"", b"not a font at all", FONT[:200]])
def test_unusable_font_is_an_error_without_fallback(data: bytes) -> None:
    with pytest.raises(TextFontError, match="never substitutes another font"):
        ctext.render(slot(), data, CANVAS)


def test_font_tables() -> None:
    info = ot.read(FONT)
    assert (info.units_per_em, info.typo_ascender, info.typo_descender) == (1000, 770, -230)
    assert info.kerning("A", "V").first_advance == -60
    assert info.kerning("V", "a").first_advance == -15
    assert not info.kerning("a", "b")
    assert info.ligate("office flight") == "of\ufb01ce \ufb02ight"
    assert info.ligate("Tf") == "Tf"
    with pytest.raises(ot.FontTableError):
        ot.read(b"\x00\x01\x00\x00" + b"\x00" * 8)


# --- Layout ----------------------------------------------------------------------------------


def test_pen_positions_add_kerning_to_hinted_advances() -> None:
    font = ctext._font(FONT, 30.0)
    pens = ctext.pen_positions(font, "AVA")
    advance = font.face.getlength("A")
    kern = -60 * 30.0 / 1000
    assert pens[1] == pytest.approx(round((advance + kern) * 64) / 64)
    assert pens[0] == 0.0
    assert all(p * 64 == int(p * 64) for p in pens)  # 1/64-pixel positions


def test_ligatures_are_drawn_as_one_glyph() -> None:
    plain = alpha_canvas(slot(value="fi"), "fi")
    font = ctext._font(FONT, 30.0)
    lig = ctext._glyph(font, "\ufb01")[0]
    # The rendered text is the single "ﬁ" glyph, not "f" then "i".
    assert plain.sum() == pytest.approx(lig.sum() * 255, rel=0.02)


def test_horizontal_alignment_uses_width_from_the_origin() -> None:
    font = ctext._font(FONT, 30.0)
    left = ctext.layout(slot(ha="left"), font, "Valid 12Z")
    center = ctext.layout(slot(ha="center"), font, "Valid 12Z")
    right = ctext.layout(slot(ha="right"), font, "Valid 12Z")
    ink_left, _, ink_right, _ = left.ink
    width = ink_right - max(ink_left, 0.0)
    assert left.origin[0] == 200.0
    assert center.origin[0] == pytest.approx(200.0 - width / 2, abs=1 / 64)
    assert right.origin[0] == pytest.approx(200.0 - width, abs=1 / 64)


def test_vertical_alignment_uses_typographic_metrics() -> None:
    font = ctext._font(FONT, 30.0)
    a = 770 * 30.0 / 1000  # sTypoAscender: larger than the ink ascent of these glyphs
    d = 230 * 30.0 / 1000
    base = {
        va: ctext.layout(slot(va=va), font, "aceo").origin[1]
        for va in ("baseline", "bottom", "top", "center")
    }
    assert base["baseline"] == 60.0
    assert base["bottom"] == pytest.approx(60.0 - d, abs=1 / 64)
    assert base["top"] == pytest.approx(60.0 + a, abs=1 / 64)
    assert base["center"] == pytest.approx(60.0 + (a - d) / 2, abs=1 / 64)


def test_ink_taller_than_the_typographic_box_wins() -> None:
    font = ctext._font(FONT, 30.0)
    lay = ctext.layout(slot(va="top"), font, "(g)")
    assert -lay.ink[1] > 770 * 30.0 / 1000  # parentheses rise above the typographic ascender
    assert lay.origin[1] == pytest.approx(60.0 - lay.ink[1], abs=1 / 64)


def test_snap_and_offset() -> None:
    font = ctext._font(FONT, 30.0)
    lay = ctext.layout(slot(x=200.3, y=60.6, snap="round", offset=(0.5, -1.0)), font, "Valid")
    assert lay.origin == (200.5, 60.0)
    lay = ctext.layout(slot(x=200.3, y=60.6, snap="floor"), font, "Valid")
    assert lay.origin == (200.0, 60.0)


@pytest.mark.parametrize("shift", [0.25, 0.5, 0.75])
def test_fractional_anchors_move_the_ink_by_the_fraction(shift: float) -> None:
    a = alpha_canvas(slot())
    bx = alpha_canvas(slot(x=200.0 + shift))
    by = alpha_canvas(slot(y=60.0 + shift))
    assert centroid(bx)[0] - centroid(a)[0] == pytest.approx(shift, abs=0.02)
    assert centroid(by)[1] - centroid(a)[1] == pytest.approx(shift, abs=0.02)


# --- Rendering ---------------------------------------------------------------------------


def test_pixels_are_the_colour_with_coverage_alpha() -> None:
    s = slot(color=(10, 20, 30, 128))
    left, top, px = ctext.render(s, FONT, CANVAS)
    assert px is not None
    assert (px[..., :3] == (10, 20, 30)).all()
    assert px[..., 3].max() == 128
    assert px[0].any()
    assert px[-1].any()
    assert px[:, 0].any()
    assert px[:, -1].any()
    assert (left, top) == ink_box(alpha_canvas(s))[:2]


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_text_has_no_pixels(value: str) -> None:
    assert ctext.render(slot(), FONT, CANVAS, value) == (0, 0, None)


def test_text_is_cropped_to_the_canvas() -> None:
    assert ctext.render(slot(x=-500.0), FONT, CANVAS)[2] is None
    left, _top, px = ctext.render(slot(x=-20.0), FONT, CANVAS)
    assert px is not None
    assert left == 0
    assert left + px.shape[1] <= CANVAS[1]
    full = alpha_canvas(slot(x=380.0, ha="left"))
    assert full[:, -1].any()  # runs off the right edge, kept up to it


def test_one_line_only() -> None:
    with pytest.raises(ValueError, match="one line"):
        ctext.render(slot(), FONT, CANVAS, "two\nlines")


# --- Scene -----------------------------------------------------------------------------------


def text_scene() -> Scene:
    return Scene(
        map_geometry(width=400, height=120),
        [
            RasterLayer(id="below", order=0, pixels=gradient(120, 400)),
            slot(id="title", va="top", y=5.0),
            slot(
                id="subtitle", order=31, va="bottom", y=115.0, ha="center", color=(200, 0, 0, 255)
            ),
        ],
        assets={"f.ttf": FONT},
    )


def test_replace_text_swaps_in_a_new_slot_and_keeps_the_rest() -> None:
    scene = text_scene()
    before = list(scene.layers)
    geometry = scene.geometry
    new = scene.replace_text("title", "Total precipitation (in)")
    assert scene["title"] is new
    assert new.value == "Total precipitation (in)"
    assert (new.x, new.y, new.ha, new.va, new.size_px) == (200.0, 5.0, "left", "top", 30.0)
    assert all(a is b for a, b in zip(scene.layers, before, strict=True) if a.id != "title")
    assert scene.geometry is geometry
    left, top, px = ctext.render(new, FONT, scene.shape)
    assert (new.left, new.top) == (left, top)
    np.testing.assert_array_equal(new.pixels, px)


def test_scene_text_mapping() -> None:
    scene = text_scene()
    assert dict(scene.text) == {"title": "Valid 12Z", "subtitle": "Valid 12Z"}
    scene.text["subtitle"] = "Issued 6:19 AM"
    assert scene.text["subtitle"] == "Issued 6:19 AM"
    assert scene["subtitle"].value == "Issued 6:19 AM"
    assert len(scene.text) == 2
    assert list(scene.text) == ["title", "subtitle"]
    with pytest.raises(KeyError, match="raster layer, not a text slot"):
        scene.text["below"]
    with pytest.raises(TypeError, match="remove_layer"):
        del scene.text["title"]  # type: ignore[attr-defined]


def test_failed_replacement_leaves_scene_unchanged() -> None:
    scene = text_scene()
    before, image = list(scene.layers), scene.render()
    for bad, error in [("a\nb", ValueError), (42, TypeError)]:
        with pytest.raises(error):
            scene.replace_text("title", bad)  # type: ignore[arg-type]
    with pytest.raises(SceneError, match="not a text slot"):
        scene.replace_text("below", "x")
    broken = Scene(scene.geometry, list(scene.layers), assets={"f.ttf": b"garbage"})
    with pytest.raises(TextFontError):
        broken.text["title"] = "x"
    assert all(a is b for a, b in zip(scene.layers, before, strict=True))
    np.testing.assert_array_equal(scene.render(), image)


def test_blank_text_clears_the_pixels_and_round_trips(tmp_path: Path) -> None:
    scene = text_scene()
    scene.text["title"] = ""
    assert scene["title"].pixels is None
    path = tmp_path / "t.cstack"
    scene.save(path)
    loaded = Scene.load(path)
    assert loaded.text["title"] == ""
    np.testing.assert_array_equal(loaded.render(), scene.render())
    loaded.text["title"] = "Back"
    scene.text["title"] = "Back"
    np.testing.assert_array_equal(loaded.render(), scene.render())


# --- Core-runtime milestone ------------------------------------------------------------------


def test_core_runtime_milestone(tmp_path: Path) -> None:
    """Hand-built file: new grid values, polygons, text, and a layer, to PNG, NumPy/Pillow only."""
    from cartostack import GridSlot, PolygonSlot
    from cartostack.manifest import Bin

    g = map_geometry(width=160, height=120)
    imap = np.full((120, 160), -1, np.int32)
    imap[10:110, 10:150] = (
        np.arange(10 * 14, dtype=np.int32).reshape(10, 14).repeat(10, 0).repeat(10, 1)
    )
    lut = np.vstack(
        [
            np.c_[np.arange(0, 256, 32), np.zeros((8, 2)), np.full(8, 255)],
            [[0, 0, 255, 255], [255, 0, 0, 255], [0, 0, 0, 0]],
        ]
    ).astype(np.uint8)
    Scene(
        g,
        [
            RasterLayer(id="below", order=0, pixels=gradient(120, 160)),
            GridSlot(id="t2m", order=10, shape=(10, 14), index_map=imap, lut=lut, vmin=0.0,
                     vmax=8.0, alpha=0.8),
            PolygonSlot(id="qpf", order=20, supersample=4,
                        bins=(Bin(0.0, 1.0, (0, 200, 0, 255), 1),)),
            RasterLayer(id="above", order=30, pixels=gradient(20, 40, 3), left=110, top=90),
            slot(id="title", order=40, x=80.0, y=4.0, ha="center", va="top", size_px=14.0),
        ],
        assets={"f.ttf": FONT},
    ).save(tmp_path / "hand.cstack")  # fmt: skip
    np.save(tmp_path / "values.npy", np.linspace(-1, 9, 140, dtype=np.float32).reshape(10, 14))
    code = (
        "import json, sys\n"
        "import numpy as np\n"
        "from cartostack import Scene\n"
        f"with Scene.load({str(tmp_path / 'hand.cstack')!r}) as s:\n"
        f"    s.replace_grid('t2m', np.load({str(tmp_path / 'values.npy')!r}))\n"
        "    ring = [(-76.5, 42.5), (-75.0, 42.5), (-75.0, 43.5), (-76.5, 43.5)]\n"
        "    s.replace_polygons('qpf', [[ring]], [0.5])\n"
        "    s.text['title'] = 'Total precipitation'\n"
        "    s.add_layer('logo', np.full((8, 8, 4), 255, np.uint8), left=2, top=110)\n"
        f"    s.save_png({str(tmp_path / 'out.png')!r})\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = {name.split(".")[0] for name in json.loads(out.stdout)}
    forbidden = {"matplotlib", "cartopy", "pyproj", "shapely", "geopandas", "pandas"}
    assert loaded.isdisjoint(forbidden), sorted(loaded & forbidden)
    with Image.open(tmp_path / "out.png") as img:
        px = np.asarray(img.convert("RGBA"))
    assert px.shape == (120, 160, 4)
    assert (px[110:118, 2:10] == 255).all()  # the added layer
    assert (px[40:60, 70:90, 1] > 100).any()  # the polygon's green
    assert (px[2:22, 20:140, :3].max(-1) < 40).sum() > 50  # the title's dark glyphs


# --- Matplotlib --------------------------------------------------------------------------------


def _mpl_alpha(value: str, path: Path, pt: float, dpi: float, x: float, y: float, ha: str, va: str,
               canvas: tuple[int, int]) -> np.ndarray:  # fmt: skip
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties

    h, w = canvas
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)
    fig.text(x / w, 1 - y / h, value, fontproperties=FontProperties(fname=str(path)), fontsize=pt,
             ha=ha, va=va)  # fmt: skip
    fig.canvas.draw()
    a = np.asarray(fig.canvas.buffer_rgba())[..., 3].astype(float)
    plt.close(fig)
    return a


MPL_CASES = [
    ("DejaVuSans.ttf", 8, 100), ("DejaVuSans-Oblique.ttf", 12, 100),
    ("DejaVuSans-Bold.ttf", 11, 300), ("DejaVuSans-Oblique.ttf", 14, 300),
]  # fmt: skip
MPL_STRINGS = [
    "8:00 AM Oct 8 - 8:00 AM Oct 11 (Issued: 6:19 AM Oct 8)",
    "AVATAR To Ty Wo: 2-m Temperature, Valid P.",
    "office flight affine",
]


@pytest.mark.build
@pytest.mark.parametrize(("face", "pt", "dpi"), MPL_CASES)
@pytest.mark.parametrize("value", MPL_STRINGS)
def test_shaping_matches_matplotlib(face: str, pt: float, dpi: float, value: str) -> None:
    """Same glyphs (ligatures) at the same pen positions as Matplotlib's layout, to 1/64 px."""
    import matplotlib
    from matplotlib.font_manager import get_font
    from matplotlib.ft2font import LoadFlags

    path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / face
    f = get_font(str(path))
    f.set_size(pt, dpi)
    items = f._layout(value, flags=LoadFlags.DEFAULT)
    font = ctext._font(path.read_bytes(), pt * dpi / 72)
    shaped = font.info.ligate(value)
    assert [font.info.glyph(c) for c in shaped] == [i.glyph_index for i in items]
    np.testing.assert_allclose(
        ctext.pen_positions(font, shaped), [i.x for i in items], atol=1.01 / 64
    )


@pytest.mark.build
@pytest.mark.parametrize(("face", "pt", "dpi"), MPL_CASES)
@pytest.mark.parametrize(
    ("ha", "va"),
    [("left", "baseline"), ("center", "bottom"), ("right", "top"), ("center", "center")],
)
@pytest.mark.parametrize("value", MPL_STRINGS)
def test_placement_matches_matplotlib(
    face: str, pt: float, dpi: float, ha: str, va: str, value: str
) -> None:
    import matplotlib

    path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / face
    canvas = (200, 2600)
    x, y = 1300.3, 100.6
    ref = _mpl_alpha(value, path, pt, dpi, x, y, ha, va, canvas)
    s = TextSlot(id="t", order=0, value=value, font="f", size_px=pt * dpi / 72, x=x, y=y, ha=ha,
                 va=va, snap=None, offset=(0.0, 0.0))  # fmt: skip
    left, top, px = ctext.render(s, path.read_bytes(), canvas)
    assert px is not None
    ours = np.zeros(canvas)
    ours[top : top + px.shape[0], left : left + px.shape[1]] = px[..., 3]
    (rx, ry), (ox, oy) = centroid(ref), centroid(ours)
    assert abs(ox - rx) < 0.3
    assert abs(oy - ry) < 0.01
    assert abs(ours.sum() / ref.sum() - 1) < 0.002  # same amount of ink
    ink = (ref > 0) | (ours > 0)
    assert (np.abs(ours - ref)[ink] > 32).mean() < (0.40 if pt * dpi / 72 < 25 else 0.25)
