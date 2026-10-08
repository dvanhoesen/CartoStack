# /// script
# requires-python = "==3.13.*"
# dependencies = [
#     "cartopy==0.26.0",
#     "geopandas==1.2.0",
#     "matplotlib==3.11.2",
#     "numpy==2.5.3",
#     "pillow==12.3.0",
#     "pyogrio==0.13.0",
#     "pytz==2026.5",
# ]
# [tool.uv]
# exclude-newer = "2026-10-08T00:00:00Z"
# ///
"""Session 02: author prototype layered files from the two reference scenes.

Builds each scene's figure with the baseline code (``baseline_qpf.build_figure``
and ``baseline_cartopy.build_scene``), renders its draw groups into transparent
RGBA layers on the identical canvas, and writes prototype ``.cstack`` ZIPs
(``ZIP_STORED``; JSON manifest plus members) in three layer encodings:

* ``png``: Pillow PNG, compress_level 6;
* ``raw``: uint8 bytes, uncompressed;
* ``zlib``: uint8 bytes, zlib level 6 (stdlib, so the runtime stays NumPy + Pillow).

zstd is not used: on Python 3.13 it would add a runtime dependency (stdlib
``compression.zstd`` arrives in 3.14).

QPF file: static below (white background, coastline), static above (states,
lakes, counties, NY mask, cities, logo, title bar, title, legend, attribution)
flattened into one layer and, for the flattening measurement, also split into
six layers; a polygon slot (bin table and draw order); the subtitle text slot
with its embedded font; LCC parameters and the projected→pixel affine.

Grid file: static below, data as a grid slot (pixel→cell int32 index map from
the index-image method, colormap LUT, vmin/vmax, alpha), static above (borders,
cities, title, colorbar), and the subtitle text slot.

    uv run benchmarks/prototype_build.py     # → benchmarks/results/prototype-<UTC stamp>/
"""

import time

T0 = time.perf_counter()

import argparse
import hashlib
import io
import json
import sys
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path

import baseline_cartopy as bc
import baseline_qpf as bq
import prototype_runtime as rt

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
ENCODINGS = ("png", "raw", "zlib")
QPF_BUILD_DAY = "1-3"  # any product: its data and subtitle are hidden while authoring
# Split of the QPF static-above group (zorder bands) for the flattening measurement.
QPF_ABOVE_SPLIT = (("states_lakes", 60, 61), ("counties", 62, 63), ("nymask", 75, 76),
                   ("cities", 90, 92), ("logo", 95, 96), ("decorations", 995, 10_000))


# --- Rendering layers ----------------------------------------------------------


def save_rgba(fig, bbox_in, dpi):
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.transforms import Bbox
    from PIL import Image

    buf = io.BytesIO()
    kwargs = {"bbox_inches": Bbox.from_bounds(*bbox_in), "pad_inches": 0} if bbox_in else {}
    plt.figure(fig.number)
    fig.savefig(buf, format="png", dpi=dpi, **kwargs)
    return np.asarray(Image.open(buf).convert("RGBA")).copy()


def render_groups(fig, members: dict, groups: list[str], bbox_in, dpi, background_group: str):
    """Render each group alone: every artist not in the group is hidden.

    ``members`` maps artist → group; artists that were hidden stay hidden. The
    figure patch belongs to ``background_group``; other groups are transparent.
    """
    import numpy as np

    layers = {}
    original = {artist: artist.get_visible() for artist in members}  # e.g. GeoAxes hides its axes
    for group in groups:
        for artist, g in members.items():
            artist.set_visible(original[artist] and g == group)
        fig.patch.set_visible(group == background_group)
        layers[group] = save_rgba(fig, bbox_in, dpi)
    for artist, vis in original.items():
        artist.set_visible(vis)
    fig.patch.set_visible(True)
    for group, arr in layers.items():
        arr[arr[..., 3] == 0] = 0  # canonical transparent pixels
        layers[group] = np.ascontiguousarray(arr)
    return layers


def affine_from(ax, scale: float, offset_px: tuple[float, float], height_px: float) -> list[float]:
    """Projected metres → output pixels (top-left origin), from ax.transData."""
    import numpy as np

    pts = np.array([[0.0, 0.0], [1e5, 0.0], [0.0, 1e5]])
    disp = ax.transData.transform(pts)
    col = disp[:, 0] * scale - offset_px[0]
    row = height_px - (disp[:, 1] * scale - offset_px[1])
    coeffs = [(col[1] - col[0]) / 1e5, (col[2] - col[0]) / 1e5, col[0],
              (row[1] - row[0]) / 1e5, (row[2] - row[0]) / 1e5, row[0]]
    probe = np.array([[123456.0, -98765.0]])
    d = ax.transData.transform(probe)[0]
    expect = (d[0] * scale - offset_px[0], height_px - (d[1] * scale - offset_px[1]))
    got = (coeffs[0] * probe[0, 0] + coeffs[1] * probe[0, 1] + coeffs[2],
           coeffs[3] * probe[0, 0] + coeffs[4] * probe[0, 1] + coeffs[5])
    assert max(abs(got[0] - expect[0]), abs(got[1] - expect[1])) < 1e-6, "transData is not affine"
    return [float(c) for c in coeffs]


def lcc_params(ax) -> dict:
    import pyproj

    p = ax.projection.proj4_params
    ell = pyproj.CRS(ax.projection.proj4_init).ellipsoid
    return {"a": ell.semi_major_metre, "f": 1 / ell.inverse_flattening,
            "lat_1": float(p["lat_1"]), "lat_2": float(p["lat_2"]), "lat_0": float(p["lat_0"]),
            "lon_0": float(p["lon_0"]), "x_0": float(p["x_0"]), "y_0": float(p["y_0"]),
            "proj4": ax.projection.proj4_init}


def check_lcc(ax, lcc: dict) -> float:
    """Max error (m) of the runtime's NumPy LCC against pyproj over a lon/lat grid."""
    import numpy as np
    import pyproj

    lon, lat = np.meshgrid(np.linspace(-90, -60, 121), np.linspace(30, 55, 101))
    x1, y1 = rt.lcc_forward(lon, lat, lcc)
    tr = pyproj.Transformer.from_crs("EPSG:4326", ax.projection.proj4_init, always_xy=True)
    x2, y2 = tr.transform(lon, lat)
    return float(max(np.abs(x1 - x2).max(), np.abs(y1 - y2).max()))


def text_slot(artist, scale, offset_px, height_px, dy: float) -> dict:
    """Text slot record: anchor in output pixels, font, size, colour, alignment."""
    from matplotlib import colors as mcolors
    from matplotlib import font_manager

    x, y = artist.get_transform().transform(artist.get_position())
    font_path = Path(font_manager.findfont(artist.get_fontproperties(), fallback_to_default=False))
    rgba = [round(c * 255) for c in mcolors.to_rgba(artist.get_color(), artist.get_alpha())]
    return {
        "text": artist.get_text(),
        "font": f"fonts/{font_path.name}",
        "font_path": str(font_path),
        "size_px": artist.get_fontsize() * artist.figure.dpi * scale / 72,
        "x": float(x * scale - offset_px[0]),
        "y": float(height_px - (y * scale - offset_px[1])),
        "ha": artist.get_horizontalalignment(),
        "va": artist.get_verticalalignment(),
        "color": rgba,
        "snap": "round",
        "dy": dy,
    }


def mpl_color_u8(c) -> list[int]:
    from matplotlib import colors as mcolors

    return [round(v * 255) for v in mcolors.to_rgba(c)]


# --- Scenes ----------------------------------------------------------------------


def build_qpf() -> dict:
    """Author the QPF scene. Returns manifest pieces and arrays."""

    t = time.perf_counter()
    bq.import_stack()
    static, _ = bq.load_static()
    prod = bq.read_product(QPF_BUILD_DAY)
    sc = bq.build_figure(static, prod)
    fig, ax, sub = sc["fig"], sc["ax"], sc["subtitle"]
    fig.canvas.draw()
    tight = fig.get_tightbbox(fig.canvas.get_renderer()).bounds
    scale = bq.SAVE_DPI / fig.dpi
    offset = (tight[0] * bq.SAVE_DPI, tight[1] * bq.SAVE_DPI)
    height_f = tight[3] * bq.SAVE_DPI

    data = set(map(id, sc["data_artists"]))
    members = {}
    for a in ax.get_children():
        if id(a) in data:
            members[a] = "data"
        elif a is sub:
            members[a] = "subtitle"
        else:
            members[a] = "below" if a.get_zorder() < 10 else "above"
    layers = render_groups(fig, members, ["below", "above", "data", "subtitle"], tight,
                           bq.SAVE_DPI, background_group="below")
    split = {}
    for name, z0, z1 in QPF_ABOVE_SPLIT:
        sub_members = {a: (name if (g == "above" and z0 <= a.get_zorder() < z1) else "hidden")
                       for a, g in members.items()}
        split[name] = render_groups(fig, sub_members, [name], tight, bq.SAVE_DPI, "none")[name]
    union = sum((v[..., 3] > 0) for v in split.values()) > 0
    split_coverage = {"union_pixels": int(union.sum()),
                      "flattened_pixels": int((layers["above"][..., 3] > 0).sum())}

    lcc = lcc_params(ax)
    georef = {"lcc": lcc, "affine": affine_from(ax, scale, offset, height_f)}
    ranges = [(lo, up) for lo, up, *_ in bq.QPF_RANGES]
    slot = {
        "ranges": ranges,
        "colors": [mpl_color_u8(c) for *_, c, _z in bq.QPF_RANGES] + [mpl_color_u8("gray")],
        "zorder": [z for *_, z in bq.QPF_RANGES] + [10],
        "default": len(ranges),  # the example's fallback: gray, zorder 10
        "rounding": 2,
        "fill_rule": "evenodd",
        "supersample": 2,
    }
    text = text_slot(sub, scale, offset, height_f, dy=-1.0)
    height, width = layers["below"].shape[:2]
    build_s = time.perf_counter() - t
    return {
        "scene": "qpf",
        "canvas": {"width": width, "height": height, "save_dpi": bq.SAVE_DPI,
                   "figsize_in": list(bq.FIGSIZE), "crop_bbox_in": list(tight)},
        "georef": georef,
        "lcc_check_max_error_m": check_lcc(ax, lcc),
        "rasters": {"below": layers["below"], "above": layers["above"]},
        "split_above": split,
        "reference_layers": {"data": layers["data"], "subtitle": layers["subtitle"]},
        "polygon_slot": slot,
        "text_slots": {"subtitle": text},
        "stack": ["below", "slot:qpf", "above", "text:subtitle"],
        "build_s": build_s,
        "build_day": QPF_BUILD_DAY,
        "n_split": len(split),
        "split_coverage": split_coverage,
    }


def build_grid() -> dict:
    import cartopy.crs as ccrs
    import numpy as np
    from matplotlib.collections import QuadMesh

    t = time.perf_counter()
    bc.import_stack()
    sources = bc.load_sources()
    sc = bc.build_scene(sources, 0)
    fig, ax, cax, mesh = sc["fig"], sc["ax"], sc["cax"], sc["mesh"]
    title, sub = sc["title"], sc["subtitle"]
    fig.canvas.draw()
    height_f = float(fig.bbox.height)

    members = {}
    for a in ax.get_children():
        if a is mesh:
            members[a] = "data"
        else:
            members[a] = "below" if a.get_zorder() < bc.DATA_ZORDER else "above"
    members[cax] = "above"
    members[title] = "above"
    members[sub] = "subtitle"
    layers = render_groups(fig, members, ["below", "above", "data", "subtitle"], None, bc.DPI,
                           background_group="below")

    # Index-image method: same mesh geometry, face colours encode the flat cell index.
    lon, lat, _ = bc.make_field(0)
    ny, nx = bc.GRID_SHAPE
    idx = np.arange(ny * nx)
    codes = np.column_stack([(idx >> 16) & 255, (idx >> 8) & 255, idx & 255]) / 255.0
    probe = ax.pcolormesh(lon, lat, np.zeros(bc.GRID_SHAPE, np.float32), transform=ccrs.PlateCarree(),
                          shading="nearest", antialiased=False, linewidth=0, zorder=bc.DATA_ZORDER)
    QuadMesh.set_array(probe, None)  # GeoQuadMesh.set_array rejects None
    probe.set_facecolor(np.column_stack([codes, np.ones(len(idx))]))
    probe_members = {a: ("probe" if a is probe else "hidden") for a in [*members, probe]}
    ax.patch.set_visible(False)
    img = render_groups(fig, probe_members, ["probe"], None, bc.DPI, "none")["probe"]
    ax.patch.set_visible(True)
    probe.remove()
    index_map = ((img[..., 0].astype(np.int32) << 16) | (img[..., 1].astype(np.int32) << 8)
                 | img[..., 2].astype(np.int32))
    index_map[img[..., 3] == 0] = -1
    partial = int(((img[..., 3] > 0) & (img[..., 3] < 255)).sum())

    lut = np.round(np.asarray(mesh.cmap._lut) * 255).astype(np.uint8)  # N + under, over, bad
    slot = {
        "grid_shape": [ny, nx],
        "vmin": float(mesh.norm.vmin), "vmax": float(mesh.norm.vmax),
        "n_colors": int(mesh.cmap.N),
        "lut_rows": {"under": int(mesh.cmap.N), "over": int(mesh.cmap.N) + 1,
                     "bad": int(mesh.cmap.N) + 2},
        "alpha_u8": round(float(mesh.get_alpha()) * 255),
        "dtype": "float32",
    }
    text = text_slot(sub, 1.0, (0.0, 0.0), height_f, dy=-1.0)
    height, width = layers["below"].shape[:2]
    build_s = time.perf_counter() - t
    return {
        "scene": "grid",
        "canvas": {"width": width, "height": height, "save_dpi": bc.DPI,
                   "figsize_in": list(bc.FIGSIZE), "crop_bbox_in": None},
        "georef": {"lcc": lcc_params(ax), "affine": affine_from(ax, 1.0, (0.0, 0.0), height_f)},
        "lcc_check_max_error_m": check_lcc(ax, lcc_params(ax)),
        "rasters": {"below": layers["below"], "above": layers["above"]},
        "reference_layers": {"data": layers["data"], "subtitle": layers["subtitle"]},
        "grid_slot": slot,
        "index_map": index_map,
        "index_map_partial_alpha_pixels": partial,
        "lut": lut,
        "text_slots": {"subtitle": text},
        "stack": ["below", "slot:grid", "above", "text:subtitle"],
        "build_s": build_s,
    }


# --- Writing -------------------------------------------------------------------


def encode(arr, encoding: str) -> tuple[bytes, str]:
    from PIL import Image

    if encoding == "png":
        buf = io.BytesIO()
        Image.fromarray(arr, "RGBA").save(buf, format="PNG", compress_level=6)
        return buf.getvalue(), "png"
    raw = arr.tobytes()
    return (raw, "raw") if encoding == "raw" else (zlib.compress(raw, 6), "zlib")


def array_member(arr, encoding: str) -> tuple[bytes, dict]:
    """Non-image arrays (index map, LUT): raw, or zlib for the compressed encodings."""
    raw = arr.tobytes()
    kind = "raw" if encoding == "raw" else "zlib"
    data = raw if kind == "raw" else zlib.compress(raw, 6)
    return data, {"encoding": kind, "dtype": arr.dtype.str, "shape": list(arr.shape)}


def write_cstack(scene: dict, encoding: str, path: Path, split: bool = False) -> dict:
    """Write one prototype file; return member sizes and timings."""
    t = time.perf_counter()
    members: dict[str, bytes] = {}
    layers = []
    rasters = dict(scene["rasters"])
    stack = list(scene["stack"])
    if split:
        rasters.pop("above")
        rasters.update(scene["split_above"])
        i = stack.index("above")
        stack[i:i + 1] = list(scene["split_above"])
    for name, arr in rasters.items():
        data, kind = encode(arr, encoding)
        member = f"layers/{name}.{ {'png': 'png', 'raw': 'rgba', 'zlib': 'rgba.zz'}[kind] }"
        members[member] = data
        layers.append({"id": name, "kind": "raster", "member": member, "encoding": kind,
                       "shape": list(arr.shape), "sha256": hashlib.sha256(data).hexdigest()})
    slots = {}
    if "polygon_slot" in scene:
        slots["qpf"] = {"kind": "polygon", **scene["polygon_slot"]}
    if "grid_slot" in scene:
        data, meta = array_member(scene["index_map"], encoding)
        members["grid/index_map.bin"] = data
        lut_data, lut_meta = array_member(scene["lut"], "raw")
        members["grid/lut.bin"] = lut_data
        slots["grid"] = {"kind": "grid", **scene["grid_slot"],
                         "index_map": {"member": "grid/index_map.bin", **meta},
                         "lut": {"member": "grid/lut.bin", **lut_meta}}
    texts = {}
    for name, ts in scene["text_slots"].items():
        font_path = Path(ts["font_path"])
        members[ts["font"]] = font_path.read_bytes()
        texts[name] = {k: v for k, v in ts.items() if k != "font_path"}
    manifest = {
        "format": "cartostack-prototype",
        "version": "0.0-session02",
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "scene": scene["scene"],
        "canvas": scene["canvas"],
        "georef": scene["georef"],
        "stack": stack,
        "layers": layers,
        "slots": slots,
        "text_slots": texts,
        "layer_encoding": encoding,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=1, default=float))
        for name, data in members.items():
            zf.writestr(name, data)
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "member_bytes": {k: len(v) for k, v in members.items()},
        "write_s": time.perf_counter() - t,
    }


def save_png(arr, path: Path) -> None:
    from PIL import Image

    Image.fromarray(arr, "RGBA").save(path, compress_level=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="output directory (default: new results/prototype-<stamp>)")
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.out) if args.out else RESULTS / f"prototype-{stamp}"
    (outdir / "layers").mkdir(parents=True, exist_ok=True)
    record = {"created_utc": stamp, "files": {}}

    for build in (build_qpf, build_grid):
        scene = build()
        name = scene["scene"]
        print(f"{name}: built in {scene['build_s']:.2f} s; LCC vs pyproj max error "
              f"{scene['lcc_check_max_error_m']:.2e} m")
        files = {}
        for enc in ENCODINGS:
            files[enc] = write_cstack(scene, enc, outdir / f"{name}-{enc}.cstack")
            print(f"  {enc:5} {files[enc]['bytes'] / 1e6:7.2f} MB  write {files[enc]['write_s']:.2f} s")
        if name == "qpf":
            for enc in ENCODINGS:
                files[f"{enc}-split"] = write_cstack(scene, enc, outdir / f"{name}-{enc}-split.cstack",
                                                     split=True)
        for lname, arr in {**scene["rasters"], **scene["reference_layers"]}.items():
            save_png(arr, outdir / "layers" / f"{name}_{lname}.png")
        if name == "grid":
            import numpy as np

            np.save(outdir / "layers" / "grid_index_map.npy", scene["index_map"])
        record["files"][name] = {
            "build_s": scene["build_s"],
            "lcc_check_max_error_m": scene["lcc_check_max_error_m"],
            "canvas": scene["canvas"],
            "georef": scene["georef"],
            "text_slots": {k: {kk: vv for kk, vv in v.items() if kk != "font_path"}
                           for k, v in scene["text_slots"].items()},
            "encodings": files,
            **({"split_coverage": scene["split_coverage"]} if name == "qpf" else {}),
            **({"index_map_partial_alpha_pixels": scene["index_map_partial_alpha_pixels"],
                "index_map_coverage": float((scene["index_map"] >= 0).mean())}
               if name == "grid" else {}),
        }
    record["total_build_script_s"] = time.perf_counter() - T0
    (outdir / "build.json").write_text(json.dumps(record, indent=2, default=float) + "\n")
    print(f"wrote {outdir.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
