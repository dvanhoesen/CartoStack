"""Authoring with cartostack.build.SceneBuilder (needs the `build` extra)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytestmark = pytest.mark.build
ccrs = pytest.importorskip("cartopy.crs")
matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import colors as mcolors  # noqa: E402
from PIL import Image  # noqa: E402

from cartostack import Scene  # noqa: E402
from cartostack.build import BuildError, SceneBuilder  # noqa: E402
from cartostack.build.georef import display_to_canvas, resolve_crop  # noqa: E402
from cartostack.projection import to_pixels  # noqa: E402

PROJ: Any = None
EXTENT = (-80.5, -71.5, 40.2, 45.3)


@pytest.fixture(autouse=True)
def _projection() -> None:
    global PROJ
    PROJ = ccrs.LambertConformal(central_longitude=-76, central_latitude=42.5,
                                 standard_parallels=(40.5, 44.5))  # fmt: skip
    yield
    plt.close("all")


def full_render(fig: Any, dpi: float, crop: Any = None) -> np.ndarray:
    from matplotlib.transforms import Bbox

    buf = io.BytesIO()
    kwargs = {"bbox_inches": Bbox.from_bounds(*crop), "pad_inches": 0} if crop else {}
    fig.savefig(buf, format="png", dpi=dpi, **kwargs)
    return np.asarray(Image.open(buf).convert("RGBA")).astype(int)


def grid() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lon = np.linspace(-81, -71, 40)
    lat = np.linspace(40, 46, 30)
    vals = np.add.outer(np.linspace(-5, 30, 30), np.linspace(0, 10, 40)).astype(np.float32)
    return lon, lat, vals


def sample_builder(alpha: float | None = 0.8) -> tuple[SceneBuilder, dict[str, Any]]:
    b = SceneBuilder.new(
        PROJ, EXTENT, width=600, height=400, dpi=100, axes=(0.02, 0.13, 0.96, 0.78)
    )
    ax, fig = b.ax, b.fig
    b.add_static("below", order=0, background=True,
                 draw=lambda ax: ax.plot([-80, -72], [41, 44.5], transform=ccrs.PlateCarree(),
                                         color="tan", lw=6, zorder=1))  # fmt: skip
    lon, lat, vals = grid()
    mesh = ax.pcolormesh(lon, lat, vals, transform=ccrs.PlateCarree(), cmap="coolwarm", vmin=-10,
                         vmax=35, shading="nearest", alpha=alpha, zorder=2)  # fmt: skip
    b.add_grid_slot("t2m", order=10, lon=lon, lat=lat, cmap="coolwarm", vmin=-10, vmax=35,
                    alpha=alpha, values=vals, artists=[mesh])  # fmt: skip
    b.add_static("above", order=20,
                 draw=lambda ax: ax.plot([-79, -73, -73, -79, -79], [41, 41, 44, 44, 41],
                                         transform=ccrs.PlateCarree(), color="k", lw=1.5,
                                         zorder=3))  # fmt: skip
    title = fig.text(0.5, 0.95, "2-m Temperature", ha="center", va="top", fontsize=14)
    b.add_text_slot("title", title, order=30)
    return b, {"mesh": mesh, "title": title, "values": vals}


# --- Geometry and georeferencing ---------------------------------------------------------------


def test_geometry_records_the_resolved_axes_and_georeference() -> None:
    b, _ = sample_builder()
    fig, ax = b.fig, b.ax
    scene = b.build()
    g = scene.geometry
    assert (g.width, g.height, g.dpi) == (600, 400, 100.0)
    box = ax.get_position()
    # Cartopy shrank the requested 0.96-wide box to keep the map's aspect.
    assert box.width < 0.96
    assert g.axes.left == pytest.approx(box.x0 * 600, abs=1e-6)
    assert g.axes.width == pytest.approx(box.width * 600, abs=1e-6)
    assert g.axes.top == pytest.approx((1 - box.y1) * 400, abs=1e-6)
    geo = g.georeference
    assert geo is not None
    assert geo.projection.name == "lcc"
    assert geo.projection.as_dict()["lat_1"] == 40.5
    assert geo.extent == pytest.approx(ax.get_extent())
    assert fig.number not in plt.get_fignums()  # the builder made it, so it is closed


@pytest.mark.parametrize("dpi", [100, 300])
def test_tight_crop_is_resolved_once_and_matches_savefig(dpi: int) -> None:
    b = SceneBuilder.new(PROJ, EXTENT, width=500, height=400, dpi=100, axes=(0.1, 0.1, 0.8, 0.8),
                         crop="tight")  # fmt: skip
    b.add_static("below", order=0, background=True)
    b.dpi = float(dpi)
    buf = io.BytesIO()
    b.fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0)
    tight = Image.open(buf).size
    scene = b.build(rest="below")
    assert (scene.geometry.width, scene.geometry.height) == tight
    assert scene.geometry.figure is not None
    assert scene.geometry.figure.crop_in is not None


def test_control_points_match_cartopy_and_pyproj() -> None:
    """lon/lat → pixel through the stored georeference equals where Cartopy draws (pyproj too)."""
    import pyproj

    b, _ = sample_builder()
    fig, ax = b.fig, b.ax
    crop = resolve_crop(fig, None)
    to_canvas = display_to_canvas(fig, b.dpi, crop)
    scene = b.build()
    geo = scene.geometry.georeference
    assert geo is not None
    lon, lat = np.meshgrid(np.linspace(-80.4, -71.6, 23), np.linspace(40.3, 45.2, 17))
    lon, lat = lon.ravel(), lat.ravel()
    xy = PROJ.transform_points(ccrs.PlateCarree(), lon, lat)[:, :2]
    cartopy_px = to_canvas(ax.transData.transform(xy))
    col, row = to_pixels(geo, lon, lat)
    np.testing.assert_allclose(np.column_stack([col, row]), cartopy_px, atol=0.01)
    tr = pyproj.Transformer.from_crs("EPSG:4326", geo.proj4, always_xy=True)
    x, y = tr.transform(lon, lat)
    px = np.array([geo.to_pixel(xi, yi) for xi, yi in zip(x, y, strict=True)])
    np.testing.assert_allclose(px, cartopy_px, atol=0.01)


# --- Layers -------------------------------------------------------------------------------------


def test_layers_composite_back_to_the_full_render() -> None:
    b, _ = sample_builder()
    ref = full_render(b.fig, 100)
    scene = b.build()
    kinds = {la.id: la.kind for la in scene.layers}
    assert kinds == {"below": "raster", "t2m": "grid", "above": "raster", "title": "text"}
    above = scene["above"]
    assert above.pixels is not None
    assert above.pixels.shape[:2] != (400, 600)  # cropped to its ink
    assert (above.pixels[..., 3] < 255).any()  # transparent: no duplicated background
    d = np.abs(scene.render().astype(int) - ref).max(-1)
    assert (d > 32).mean() < 0.005  # grid seams and text anti-aliasing only
    assert (d > 8).mean() < 0.04


def test_opaque_grid_and_statics_match_closely() -> None:
    b, _ = sample_builder(alpha=None)
    ref = full_render(b.fig, 100)
    d = np.abs(b.build().render().astype(int) - ref).max(-1)
    assert (d > 32).mean() < 0.002


def test_unassigned_artists_are_an_error_unless_rest_is_given() -> None:
    b = SceneBuilder.new(PROJ, EXTENT, width=300, height=200)
    b.add_static("below", order=0, background=True)
    b.ax.plot([-80, -72], [41, 44], transform=ccrs.PlateCarree())
    with pytest.raises(BuildError, match="1 visible artist"):
        b.build()
    scene = b.build(rest="below")
    assert scene["below"].pixels is not None


def test_zorder_bands_and_double_assignment() -> None:
    b = SceneBuilder.new(PROJ, EXTENT, width=300, height=200)
    low = b.ax.plot([-80, -72], [41, 44], transform=ccrs.PlateCarree(), zorder=1)[0]
    b.ax.plot([-80, -72], [44, 41], transform=ccrs.PlateCarree(), zorder=50)
    b.add_static("below", order=0, background=True, zorder=(0, 10))
    b.add_static("above", order=10, zorder=(10, 100))
    scene = b.build()
    assert scene["above"].pixels is not None
    b2 = SceneBuilder.new(PROJ, EXTENT, width=300, height=200)
    line = b2.ax.plot([-80, -72], [41, 44], transform=ccrs.PlateCarree())[0]
    b2.add_static("a", order=0, artists=[line])
    b2.add_static("b", order=1, artists=[line])
    with pytest.raises(BuildError, match="assigned to both"):
        b2.build()
    with pytest.raises(BuildError, match="already used"):
        b2.add_static("a", order=2)
    assert low is not None


def test_layer_order_against_draw_order_warns() -> None:
    b = SceneBuilder.new(PROJ, EXTENT, width=300, height=200)
    top = b.ax.plot([-80, -72], [41, 44], transform=ccrs.PlateCarree(), zorder=50)[0]
    bottom = b.ax.plot([-80, -72], [44, 41], transform=ccrs.PlateCarree(), zorder=1)[0]
    b.add_static("first", order=0, background=True, artists=[top])
    b.add_static("second", order=1, artists=[bottom])
    with pytest.warns(UserWarning, match="draw order"):
        b.build()


def test_misordered_layers_that_do_not_overlap_do_not_warn() -> None:
    b = SceneBuilder.new(PROJ, EXTENT, width=300, height=200)
    top = b.ax.plot([-80, -79], [41, 41.5], transform=ccrs.PlateCarree(), zorder=50)[0]
    bottom = b.ax.plot([-73, -72], [44, 44.5], transform=ccrs.PlateCarree(), zorder=1)[0]
    b.add_static("first", order=0, artists=[top])
    b.add_static("second", order=1, artists=[bottom])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        b.build()


def test_matplotlib_older_than_3_11_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(matplotlib, "__version__", "3.10.6")
    fig = plt.figure()
    ax = fig.add_axes((0, 0, 1, 1), projection=PROJ)
    with pytest.raises(BuildError, match=r"3\.11"):
        SceneBuilder(fig, ax)


@pytest.mark.parametrize("version", ["3.11.0", "3.11.0rc1", "3.10.9"])
def test_matplotlib_below_the_verified_minimum_is_refused(
    monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    monkeypatch.setattr(matplotlib, "__version__", version)
    fig = plt.figure()
    ax = fig.add_axes((0, 0, 1, 1), projection=PROJ)
    with pytest.raises(BuildError, match=r"3\.11\.1"):
        SceneBuilder(fig, ax)


@pytest.mark.parametrize("version", ["3.11.1", "3.11.2", "3.12.0.dev123+g0abc"])
def test_matplotlib_from_the_verified_minimum_is_accepted(
    monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    monkeypatch.setattr(matplotlib, "__version__", version)
    fig = plt.figure()
    SceneBuilder(fig, fig.add_axes((0, 0, 1, 1), projection=PROJ))


# --- Slots ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "norm",
    [
        mcolors.LogNorm(1, 30),
        mcolors.BoundaryNorm([0, 5, 10, 20], 256),
        mcolors.TwoSlopeNorm(10, -5, 30),
        mcolors.Normalize(-5, 30, clip=True),
    ],
)
def test_grid_slots_refuse_norms_they_cannot_reproduce(norm: Any) -> None:
    """Only a linear range is stored, so other norms must fail rather than recolour."""
    b = SceneBuilder.new(PROJ, EXTENT, width=200, height=150)
    lon, lat, _ = grid()
    with pytest.raises(BuildError, match="linear Normalize"):
        b.add_grid_slot("t", order=1, lon=lon, lat=lat, cmap="viridis", norm=norm)
    b.add_grid_slot("t", order=1, lon=lon, lat=lat, cmap="viridis", norm=mcolors.Normalize(-5, 30))


@pytest.mark.parametrize("cells", ["centers", "edges"])
@pytest.mark.parametrize("two_d", [False, True])
def test_index_map_matches_pcolormesh_pixels(cells: str, two_d: bool) -> None:
    """With anti-aliasing off, the runtime grid equals Matplotlib's mesh wherever it maps a cell."""
    b = SceneBuilder.new(PROJ, EXTENT, width=500, height=350)
    lon, lat, vals = grid()
    if cells == "edges":
        lon, lat = np.linspace(-81.1, -70.9, 41), np.linspace(39.9, 46.1, 31)
    if two_d:
        lon, lat = np.meshgrid(lon, lat)
    mesh = b.ax.pcolormesh(lon, lat, vals, transform=ccrs.PlateCarree(), cmap="viridis", vmin=0,
                           vmax=35, shading="flat" if cells == "edges" else "nearest",
                           antialiased=False)  # fmt: skip
    b.add_grid_slot("g", order=0, lon=lon, lat=lat, cmap="viridis", vmin=0, vmax=35, cells=cells,
                    values=vals, artists=[mesh])  # fmt: skip
    b.fig.patch.set_visible(False)
    b.ax.patch.set_visible(False)
    ref = np.asarray(
        Image.open(io.BytesIO(_png(b.fig, 100, transparent=True))).convert("RGBA")
    ).astype(int)
    scene = b.build()
    slot = scene["g"]
    mapped = slot.index_map >= 0
    assert mapped.mean() > 0.5
    px = slot.pixels.astype(int)
    assert (np.abs(px - ref).max(-1)[mapped] == 0).mean() > 0.995


def _png(fig: Any, dpi: float, **kw: Any) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, **kw)
    return buf.getvalue()


def test_polygon_and_text_slot_definitions() -> None:
    b = SceneBuilder.new(PROJ, EXTENT, width=400, height=300)
    b.add_static("below", order=0, background=True)
    ring = [(-78, 41), (-74, 41), (-74, 44), (-78, 44)]
    bins = [(0.0, 1.0, "limegreen", 11), (1.0, 2.0, "#ff000080", 12)]
    b.add_polygon_slot("qpf", order=10, bins=bins, fallback=("gray", 10), round_decimals=2,
                       records=[[ring]], values=[0.5])  # fmt: skip
    sub = b.fig.text(0.5, 0.05, "Issued 6:19 AM", ha="center", va="bottom", fontsize=11,
                     style="italic", color="navy")  # fmt: skip
    b.add_text_slot("subtitle", sub, order=30)
    b.add_text_slot(
        "made", order=31, x=0.02, y=0.98, s="Made here", ha="left", va="top", fontsize=9
    )
    scene = b.build()
    qpf = scene["qpf"]
    assert qpf.supersample == 4
    assert qpf.bins[0].color == (50, 205, 50, 255)
    assert qpf.bins[1].color == (255, 0, 0, 128)
    assert qpf.fallback is not None
    assert qpf.fallback.color == (128, 128, 128, 255)
    assert qpf.pixels is not None
    text = scene["subtitle"]
    assert (text.snap, text.offset, text.ha, text.va) == (None, (0.0, 0.0), "center", "bottom")
    assert text.font.startswith("fonts/DejaVuSans-Oblique")
    assert text.font in scene.assets
    assert text.size_px == pytest.approx(11 * 100 / 72)
    assert text.color == (0, 0, 128, 255)
    assert (text.x, text.y) == pytest.approx((200.0, 300 - 0.05 * 300))
    assert scene.text["made"] == "Made here"


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"rotation": 30}, "rotated"),
        ({"s": "two\nlines"}, "one line"),
        ({"s": "$x^2$"}, "mathtext"),
    ],
)
def test_unsupported_text_is_refused(kw: dict[str, Any], match: str) -> None:
    b = SceneBuilder.new(PROJ, EXTENT, width=300, height=200)
    b.add_static("below", order=0, background=True)
    b.add_text_slot("t", order=1, **{"x": 0.5, "y": 0.5, "s": "plain", **kw})
    with pytest.raises(BuildError, match=match):
        b.build()


def test_polygon_slot_needs_a_supported_projection() -> None:
    b = SceneBuilder.new(ccrs.PlateCarree(), EXTENT, width=300, height=200)
    b.add_static("below", order=0, background=True)
    b.add_polygon_slot("qpf", order=1, bins=[(0, 1, "red", 1)])
    with pytest.raises(Exception, match="supported projection"):
        b.build()


# --- End to end -------------------------------------------------------------------------------


def draw_update(ax: Any, vals: np.ndarray, rings: list[Any]) -> list[Any]:
    from shapely.geometry import Polygon

    lon, lat, _ = grid()
    mesh = ax.pcolormesh(lon, lat, vals, transform=ccrs.PlateCarree(), cmap="coolwarm", vmin=-10,
                         vmax=35, shading="nearest", zorder=2)  # fmt: skip
    polys = ax.add_geometries([Polygon(r) for r in rings], crs=ccrs.PlateCarree(),
                              facecolor="limegreen", edgecolor="none", zorder=4)  # fmt: skip
    return [mesh, polys]


def test_authored_file_updated_in_a_fresh_process_matches_a_full_cartopy_render(
    tmp_path: Path,
) -> None:
    """Author with data A, update to data B without Matplotlib, compare with Cartopy drawing B."""

    def figure(
        vals: np.ndarray, rings: list[Any], subtitle: str
    ) -> tuple[SceneBuilder, list[Any], Any]:
        b = SceneBuilder.new(
            PROJ, EXTENT, width=600, height=400, dpi=100, axes=(0.02, 0.13, 0.96, 0.78)
        )
        b.ax.plot([-80, -72], [41, 44.5], transform=ccrs.PlateCarree(), color="tan", lw=6, zorder=1)
        data = draw_update(b.ax, vals, rings)
        b.ax.plot([-79, -73, -73, -79, -79], [41, 41, 44, 44, 41], transform=ccrs.PlateCarree(),
                  color="k", lw=1.5, zorder=5)  # fmt: skip
        sub = b.fig.text(0.5, 0.04, subtitle, ha="center", va="bottom", fontsize=11, style="italic")
        return b, data, sub

    _, _, vals_a = grid()
    vals_b = (vals_a[::-1] * 0.8 + 3).astype(np.float32)
    rings_a = [[(-78, 41), (-74, 41), (-74, 44), (-78, 44)]]
    rings_b = [
        [(-79.5, 42), (-75, 41.2), (-73, 43.8), (-77, 44.6)],
        [(-74, 40.6), (-72, 40.6), (-72, 42)],
    ]
    b, data, sub = figure(vals_a, rings_a, "Valid 12Z Thu")
    b.add_static("below", order=0, background=True, zorder=(0, 2))
    lon, lat, _ = grid()
    b.add_grid_slot("t2m", order=10, lon=lon, lat=lat, cmap="coolwarm", vmin=-10, vmax=35,
                    artists=[data[0]])  # fmt: skip
    b.add_polygon_slot("qpf", order=15, bins=[(0, 1, "limegreen", 1)], artists=[data[1]])
    b.add_static("above", order=20, zorder=(5, 100))
    b.add_text_slot("subtitle", sub, order=30)
    path = tmp_path / "authored.cstack"
    b.save(path)

    np.save(tmp_path / "vals.npy", vals_b)
    (tmp_path / "rings.json").write_text(json.dumps([[r] for r in rings_b]))
    code = (
        "import json, sys\nimport numpy as np\nfrom cartostack import Scene\n"
        f"s = Scene.load({str(path)!r})\n"
        f"s.replace_grid('t2m', np.load({str(tmp_path / 'vals.npy')!r}))\n"
        f"recs = json.load(open({str(tmp_path / 'rings.json')!r}))\n"
        "s.replace_polygons('qpf', recs, [0.5] * len(recs))\n"
        "s.text['subtitle'] = 'Valid 18Z Fri'\n"
        f"s.save_png({str(tmp_path / 'out.png')!r})\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = {m.split(".")[0] for m in json.loads(out.stdout)}
    assert loaded.isdisjoint({"matplotlib", "cartopy", "pyproj", "shapely"})

    ref_b, _, _ = figure(vals_b, rings_b, "Valid 18Z Fri")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ref = full_render(ref_b.fig, 100)
    got = np.asarray(Image.open(tmp_path / "out.png").convert("RGBA")).astype(int)
    d = np.abs(got - ref).max(-1)
    assert (d > 32).mean() < 0.005  # polygon edges and text anti-aliasing
    assert (d > 8).mean() < 0.02


# --- Build-to-runtime milestone (Session 11) ------------------------------------------------


def milestone_figure(vals: np.ndarray, vmin: float, vmax: float, rings: list[Any], title: str,
                     subtitle: str) -> tuple[SceneBuilder, dict[str, Any]]:  # fmt: skip
    from matplotlib.patches import Patch
    from shapely.geometry import Polygon

    b = SceneBuilder.new(PROJ, EXTENT, width=640, height=520, dpi=100, axes=(0.03, 0.2, 0.94, 0.68))
    ax, fig = b.ax, b.fig
    ax.plot([-80, -72], [41, 44.5], transform=ccrs.PlateCarree(), color="tan", lw=6, zorder=1)
    lon, lat, _ = grid()
    mesh = ax.pcolormesh(lon, lat, vals, transform=ccrs.PlateCarree(), cmap="coolwarm", vmin=vmin,
                         vmax=vmax, shading="nearest", alpha=0.8, zorder=2)  # fmt: skip
    polys = ax.add_geometries([Polygon(r) for r in rings], crs=ccrs.PlateCarree(),
                              facecolor="limegreen", edgecolor="none", zorder=4)  # fmt: skip
    ax.plot([-79, -73, -73, -79, -79], [41, 41, 44, 44, 41], transform=ccrs.PlateCarree(),
            color="k", lw=1.5, zorder=5)  # fmt: skip
    legend = ax.legend(handles=[Patch(facecolor="limegreen", label="QPF > 0")], loc="lower left")
    legend.set_zorder(6)
    cax = fig.add_axes((0.2, 0.09, 0.6, 0.03))
    cbar = fig.colorbar(mesh, cax=cax, orientation="horizontal", extend="both")
    cbar.set_label("°C", fontsize=10)
    cbar.ax.tick_params(labelsize=9)
    t = fig.text(0.5, 0.97, title, ha="center", va="top", fontsize=15)
    s = fig.text(0.5, 0.925, subtitle, ha="center", va="top", fontsize=10, color="#444444")
    return b, {"mesh": mesh, "polys": polys, "legend": legend, "cbar": cbar, "title": t, "sub": s}


def test_build_to_runtime_milestone(tmp_path: Path) -> None:
    """Authored file, then new data, norm and title without Matplotlib, vs a full Cartopy render."""
    _, _, vals_a = grid()
    vals_b = (vals_a[::-1] * 1.3 - 4).astype(np.float32)
    rings_a = [[(-78, 41), (-74, 41), (-74, 44), (-78, 44)]]
    rings_b = [[(-79.5, 42), (-75, 41.2), (-73, 43.8), (-77, 44.6)]]
    b, art = milestone_figure(vals_a, -10.0, 35.0, rings_a, "2-m Temperature", "Valid 12Z Thu")
    lon, lat, _ = grid()
    b.add_static("below", order=0, background=True, zorder=(0, 2))
    b.add_grid_slot("t2m", order=10, lon=lon, lat=lat, cmap="coolwarm", vmin=-10, vmax=35,
                    alpha=0.8, artists=[art["mesh"]])  # fmt: skip
    b.add_polygon_slot("qpf", order=15, bins=[(0, 1, "limegreen", 1)], artists=[art["polys"]])
    b.add_static("above", order=20, zorder=(5, 6))
    b.add_static("legend", order=25, artists=[art["legend"]])  # a static legend image
    b.add_colorbar("cbar", art["cbar"], slot="t2m", order=30)
    b.add_text_slot("title", art["title"], order=40)
    b.add_text_slot("subtitle", art["sub"], order=41)
    path = tmp_path / "milestone.cstack"
    b.save(path)
    assert Scene.load(path)["legend"].kind == "raster"

    np.save(tmp_path / "vals.npy", vals_b)
    (tmp_path / "rings.json").write_text(json.dumps([[r] for r in rings_b]))
    code = (
        "import json, sys\nimport numpy as np\nfrom cartostack import Scene\n"
        f"s = Scene.load({str(path)!r})\n"
        f"s.replace_grid('t2m', np.load({str(tmp_path / 'vals.npy')!r}), vmin=-15.0, vmax=40.0)\n"
        f"recs = json.load(open({str(tmp_path / 'rings.json')!r}))\n"
        "s.replace_polygons('qpf', recs, [0.5] * len(recs))\n"
        "s.text['title'] = 'Total Precipitation'\n"
        "s.text['subtitle'] = 'Valid 18Z Fri'\n"
        "assert s.stale_colorbars == (), s.stale_colorbars\n"
        f"s.save_png({str(tmp_path / 'out.png')!r})\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = {m.split(".")[0] for m in json.loads(out.stdout)}
    assert loaded.isdisjoint({"matplotlib", "cartopy", "pyproj", "shapely"})

    ref_b, _ = milestone_figure(
        vals_b, -15.0, 40.0, rings_b, "Total Precipitation", "Valid 18Z Fri"
    )
    ref = full_render(ref_b.fig, 100)
    got = np.asarray(Image.open(tmp_path / "out.png").convert("RGBA")).astype(int)
    d = np.abs(got - ref).max(-1)
    # Documented tolerances: pcolormesh seams under alpha, polygon edges, text anti-aliasing.
    print(f"milestone: >8 {(d > 8).mean():.4%} >32 {(d > 32).mean():.4%} max {d.max()}")
    assert (d > 32).mean() < 0.006
    assert (d > 8).mean() < 0.03
    assert d.max() <= 255
