# /// script
# requires-python = "==3.13.*"
# dependencies = [
#     "numpy==2.5.3",
#     "pillow==12.3.0",
# ]
# [tool.uv]
# exclude-newer = "2026-10-08T00:00:00Z"
# ///
"""Session 02: NumPy/Pillow-only runtime for the prototype layered files.

Loads a prototype ``.cstack`` written by ``prototype_build.py``, swaps in new
data (QPF polygons or grid values) and a new subtitle, stacks the layers, and
encodes PNG. Matplotlib, Cartopy, shapely, pyproj, and GeoPandas are never
imported; every child process asserts that through ``sys.modules``.

    uv run benchmarks/prototype_runtime.py                       # full benchmark, latest build
    uv run benchmarks/prototype_runtime.py --dir benchmarks/results/prototype-<stamp>

Results and images go to ``<dir>/runtime-<UTC timestamp>/``; compare them with
``prototype_compare.py``.
"""

import time

T0_PERF = time.perf_counter()
T0_WALL = time.time()

import argparse
import io
import json
import resource
import statistics
import struct
import subprocess
import sys
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmarks" / "data"
RESULTS = ROOT / "benchmarks" / "results"
FORBIDDEN = ("matplotlib", "cartopy", "shapely", "pyproj", "geopandas", "pyogrio", "pandas")

# --- Data providers (acquisition; timed separately, not part of rendering) --


def read_shapefile_polygons(shp: Path) -> list[list]:
    """Polygon rings per record from a .shp file (type 5), as float64 (N, 2) lon/lat."""
    buf = shp.read_bytes()
    pos, records = 100, []
    while pos < len(buf):
        _, length = struct.unpack(">ii", buf[pos:pos + 8])
        content = buf[pos + 8:pos + 8 + 2 * length]
        pos += 8 + 2 * length
        (shape_type,) = struct.unpack("<i", content[:4])
        if shape_type != 5:
            records.append([])
            continue
        n_parts, n_points = struct.unpack("<ii", content[36:44])
        parts = np.frombuffer(content, "<i4", n_parts, 44)
        points = np.frombuffer(content, "<f8", 2 * n_points, 44 + 4 * n_parts).reshape(-1, 2)
        bounds = [*parts.tolist(), n_points]
        records.append([points[bounds[i]:bounds[i + 1]] for i in range(n_parts)])
    return records


def read_dbf(dbf: Path) -> list[dict]:
    buf = dbf.read_bytes()
    n_records, header_len, record_len = struct.unpack("<IHH", buf[4:12])
    fields, pos = [], 32
    while buf[pos] != 0x0D:
        name = buf[pos:pos + 11].split(b"\0")[0].decode()
        ftype, size = chr(buf[pos + 11]), buf[pos + 16]
        fields.append((name, ftype, size))
        pos += 32
    rows = []
    for i in range(n_records):
        rec = buf[header_len + i * record_len:header_len + (i + 1) * record_len]
        off, row = 1, {}
        for name, ftype, size in fields:
            raw = rec[off:off + size].decode("latin-1").strip()
            row[name] = float(raw) if ftype in "NF" and raw else raw
            off += size
        rows.append(row)
    return rows


def read_qpf(day_dir: Path) -> dict:
    """Read one QPF product: rings, values, and the subtitle the example prints."""
    from zoneinfo import ZoneInfo

    shp = min(day_dir.glob("*.shp"))
    rings = read_shapefile_polygons(shp)
    rows = read_dbf(shp.with_suffix(".dbf"))
    eastern = ZoneInfo("America/New_York")

    def parse_valid(side: str) -> datetime:
        hour_str, date_str = side.strip().split()
        dt = datetime.strptime(date_str, "%m/%d/%y").replace(hour=int(hour_str[:-1]), tzinfo=UTC)
        return dt.astimezone(eastern)

    issue = datetime.strptime(rows[0]["ISSUE_TIME"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    left, right = rows[0]["VALID_TIME"].split(" - ")
    fmt = "%-I:%M %p %b %-d"
    subtitle = (f"{parse_valid(left).strftime(fmt)} - {parse_valid(right).strftime(fmt)} "
                f"(Issued: {issue.astimezone(eastern).strftime(fmt)})")
    return {"rings": rings, "values": [r["QPF"] for r in rows], "subtitle": subtitle}


# --- Projection (NumPy; replaces pyproj/Cartopy at runtime) -----------------

T_NUMPY0 = time.perf_counter()
import numpy as np

T_NUMPY = time.perf_counter() - T_NUMPY0


def lcc_constants(p: dict) -> tuple:
    """Constants of the ellipsoidal Lambert Conformal Conic (Snyder 1987, 15-1..15-10)."""
    a = p["a"]
    e = float(np.sqrt(p["f"] * (2 - p["f"])))
    rad = np.pi / 180
    phi1, phi2, phi0 = p["lat_1"] * rad, p["lat_2"] * rad, p["lat_0"] * rad

    def m(phi):
        return np.cos(phi) / np.sqrt(1 - (e * np.sin(phi)) ** 2)

    def t(phi):
        es = e * np.sin(phi)
        return np.tan(np.pi / 4 - phi / 2) / ((1 - es) / (1 + es)) ** (e / 2)

    if np.isclose(phi1, phi2):
        n = np.sin(phi1)
    else:
        n = (np.log(m(phi1)) - np.log(m(phi2))) / (np.log(t(phi1)) - np.log(t(phi2)))
    big_f = m(phi1) / (n * t(phi1) ** n)
    return e, float(n), float(a * big_f), float(a * big_f * t(phi0) ** n), p["lon_0"] * rad


def lcc_forward(lon, lat, p: dict):
    """lon/lat (degrees) → projected metres, ellipsoidal LCC with two standard parallels."""
    e, n, a_f, rho0, lam0 = lcc_constants(p)
    rad = np.pi / 180
    phi = np.asarray(lat, np.float64) * rad
    lam = (np.asarray(lon, np.float64) * rad - lam0 + np.pi) % (2 * np.pi) - np.pi
    es = e * np.sin(phi)
    t = np.tan(np.pi / 4 - phi / 2) / ((1 - es) / (1 + es)) ** (e / 2)
    rho = a_f * t**n
    theta = n * lam
    return rho * np.sin(theta) + p["x_0"], rho0 - rho * np.cos(theta) + p["y_0"]


def to_pixels(lon, lat, georef: dict):
    """lon/lat → continuous output pixel coordinates (top-left origin, pixel edges at integers)."""
    x, y = lcc_forward(lon, lat, georef["lcc"])
    ax_, bx, cx, ay, by, cy = georef["affine"]  # col = ax*x + bx*y + cx ; row = ay*x + by*y + cy
    return ax_ * x + bx * y + cx, ay * x + by * y + cy


# --- Polygon slot ------------------------------------------------------------


def classify(values, bins: dict) -> list[tuple[int, int]]:
    """(bin index, zorder) per record, as the example's colour loop does."""
    out = []
    for v in values:
        v = round(v, 2)
        idx = bins["default"]
        for i, (lower, upper) in enumerate(bins["ranges"]):
            if lower <= v < upper:
                idx = i
                break
        out.append((idx, bins["zorder"][idx]))
    return out


def clip_ring(pts, lo, hi):
    """Clip a closed ring (N, 2) to the box [lo, hi] (Sutherland-Hodgman, vectorized).

    For even-odd filling of concave rings this is exact inside the box: the
    extra edges it creates run along the box boundary, kept just off-canvas.
    """
    for axis in (0, 1):
        for bound, keep_ge in ((lo[axis], True), (hi[axis], False)):
            if len(pts) == 0:
                return pts
            v = pts[:, axis]
            inside = v >= bound if keep_ge else v <= bound
            if inside.all():
                continue
            prev, prev_in = np.roll(pts, 1, 0), np.roll(inside, 1)
            cross = inside != prev_in
            dv = v - prev[:, axis]
            t = np.divide(bound - prev[:, axis], dv, out=np.zeros_like(dv), where=cross)
            inter = prev + t[:, None] * (pts - prev)
            cand = np.stack([inter, pts], 1).reshape(-1, 2)
            pts = cand[np.stack([cross, inside], 1).ravel()]
    return pts


def record_coverage(rings, georef: dict, shape, k: int):
    """Even-odd coverage of one record's rings: ((y0, x0), uint8 coverage 0..255) or None.

    Rings are projected in one call, rings off the canvas are skipped, each
    visible ring is filled by Pillow at ``k``× resolution and XOR-ed into the
    record mask, and ``Image.reduce(k)`` turns the mask into coverage.
    """
    from PIL import Image, ImageChops, ImageDraw

    height, width = shape
    pts = np.concatenate(rings)
    starts = np.cumsum([0] + [len(r) for r in rings[:-1]])
    col, row = to_pixels(pts[:, 0], pts[:, 1], georef)
    cmin, cmax = np.minimum.reduceat(col, starts), np.maximum.reduceat(col, starts)
    rmin, rmax = np.minimum.reduceat(row, starts), np.maximum.reduceat(row, starts)
    visible = np.flatnonzero((cmax >= 0) & (cmin < width) & (rmax >= 0) & (rmin < height))
    if visible.size == 0:
        return None
    x0 = max(int(np.floor(cmin[visible].min())), 0)
    y0 = max(int(np.floor(rmin[visible].min())), 0)
    x1 = min(int(np.ceil(cmax[visible].max())) + 1, width)
    y1 = min(int(np.ceil(rmax[visible].max())) + 1, height)
    mask = Image.new("1", ((x1 - x0) * k, (y1 - y0) * k))
    ends = [*starts[1:], len(pts)]
    for j in visible:
        sl = slice(starts[j], ends[j])
        rx0 = max(int(np.floor(cmin[j] * k)), x0 * k)
        ry0 = max(int(np.floor(rmin[j] * k)), y0 * k)
        rx1 = min(int(np.ceil(cmax[j] * k)) + 1, x1 * k)
        ry1 = min(int(np.ceil(rmax[j] * k)) + 1, y1 * k)
        if rx1 <= rx0 or ry1 <= ry0:
            continue
        ring = Image.new("1", (rx1 - rx0, ry1 - ry0))
        clipped = clip_ring(np.column_stack([col[sl], row[sl]]), (-2.0, -2.0),
                            (width + 2.0, height + 2.0))
        if len(clipped) < 3:
            continue
        xy = (clipped * k - (rx0, ry0)).ravel().tolist()
        ImageDraw.Draw(ring).polygon(xy, fill=1)
        box = (rx0 - x0 * k, ry0 - y0 * k, rx1 - x0 * k, ry1 - y0 * k)
        mask.paste(ImageChops.logical_xor(mask.crop(box), ring), box)
    cov = mask.convert("L")
    if k > 1:
        cov = cov.reduce(k)
    return (y0, x0), np.asarray(cov)


def render_polygons(rings_per_record, values, slot: dict, georef: dict, shape, k: int = 1):
    """Fill classified polygons in draw order; return straight-alpha RGBA uint8.

    Painter's algorithm over coverage: fully covered pixels take the bin colour,
    partly covered ones blend it over what is already there (Agg-like
    anti-aliasing when ``k`` > 1; hard edges when ``k`` == 1).
    """
    height, width = shape
    colors = np.asarray(slot["colors"], np.uint8)
    classes = classify(values, slot)
    order = sorted(range(len(values)), key=lambda i: classes[i][1])  # stable: value order
    out = np.zeros((height, width, 4), np.uint8)
    for i in order:
        if not rings_per_record[i]:
            continue
        found = record_coverage(rings_per_record[i], georef, shape, k)
        if found is None:
            continue
        (y0, x0), cov = found
        region = out[y0:y0 + cov.shape[0], x0:x0 + cov.shape[1]]
        color = colors[classes[i][0]]
        region[cov == 255] = color
        part = (cov > 0) & (cov < 255)
        if part.any():
            c = cov[part].astype(np.float32)[:, None] / 255 * (color[3] / 255)
            dst = region[part].astype(np.float32) / 255
            da = dst[:, 3:4]
            oa = c + da * (1 - c)
            rgb = (color[:3] / 255 * c + dst[:, :3] * da * (1 - c)) / np.maximum(oa, 1e-12)
            region[part] = np.round(np.concatenate([rgb, oa], 1) * 255).astype(np.uint8)
    return out


# --- Text slot ---------------------------------------------------------------


def render_text(text: str, slot: dict, font_bytes: bytes, shape):
    """Render one line of text with Pillow, placed by Matplotlib's layout rule.

    Matplotlib aligns a box whose width is the ink width and whose height and
    descent are at least those of "lp" (``Text._get_layout``); ``slot["x"]`` and
    ``slot["y"]`` are the anchor in output pixels (top-left origin, y down).
    Returns (top, left, straight-alpha RGBA uint8 crop).
    """
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(io.BytesIO(font_bytes), slot["size_px"])
    left, top, right, bottom = font.getbbox(text, anchor="ls")  # relative to the baseline
    _, lp_top, _, lp_bottom = font.getbbox("lp", anchor="ls")
    w = right - left
    descent = max(bottom, lp_bottom)
    height = max(bottom - top, lp_bottom - lp_top)
    x = slot["x"] - {"left": 0.0, "center": w / 2, "right": w}[slot["ha"]]
    y = slot["y"]  # anchor row; the box spans [baseline - (height - descent), baseline + descent]
    baseline = {"bottom": y - descent, "top": y + height - descent,
                "baseline": y, "center": y + height / 2 - descent}[slot["va"]]
    origin_x, origin_y = x - left, baseline
    snap = slot.get("snap")  # Matplotlib's Agg backend draws text at whole pixels
    if snap:
        fn = {"round": np.round, "floor": np.floor, "ceil": np.ceil}[snap]
        origin_x, origin_y = float(fn(origin_x)), float(fn(origin_y))
    # Agg's draw_text adds one pixel upwards (draw_text_image(..., y + 1)).
    origin_x, origin_y = origin_x + slot.get("dx", 0.0), origin_y + slot.get("dy", 0.0)
    pad = 2
    x0, y0 = int(np.floor(origin_x + left)) - pad, int(np.floor(origin_y + top)) - pad
    x1, y1 = int(np.ceil(origin_x + right)) + pad, int(np.ceil(origin_y + bottom)) + pad
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, shape[1]), min(y1, shape[0])
    img = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((origin_x - x0, origin_y - y0), text, font=font,
                             fill=tuple(slot["color"]), anchor="ls")
    return y0, x0, np.asarray(img)


# --- Grid slot -----------------------------------------------------------------


def grid_colors(values, slot: dict, lut):
    """Per-cell RGBA uint8: Matplotlib's Normalize and Colormap.__call__ in float32."""
    x = np.array(values, np.float32, copy=True).ravel()
    x -= slot["vmin"]
    x /= slot["vmax"] - slot["vmin"]
    n = slot["n_colors"]
    x *= n
    x[x == n] = n - 1
    under, over, bad = x < 0, x >= n, np.isnan(x)
    with np.errstate(invalid="ignore"):
        idx = x.astype(np.intp)
    rows = slot["lut_rows"]
    idx[under], idx[over], idx[bad] = rows["under"], rows["over"], rows["bad"]
    rgba = lut[idx]
    rgba[:, 3] = slot["alpha_u8"]
    return rgba


def render_grid(values, slot: dict, prep: dict, shape):
    """Gather cell colours into pixels through the index map."""
    out = np.zeros((*shape, 4), np.uint8)
    cells = grid_colors(values, slot, prep["lut"])
    out.reshape(-1, 4)[prep["pixels"]] = cells[prep["cells"]]
    return out


# --- Compositing ---------------------------------------------------------------


def composite_pillow(base, layers):
    """Source-over in stack order with Pillow (straight alpha). ``layers``: (top, left, rgba)."""
    from PIL import Image

    img = Image.fromarray(base, "RGBA")
    for top, left, arr in layers:
        img.alpha_composite(Image.fromarray(arr, "RGBA"), dest=(left, top))
    return img


def agg_over(dst, top, left, src):
    """Matplotlib Agg's blend onto an opaque destination, in place: opaque pixels copy,
    partial ones use fixed_blender_rgba_plain's integer formula (dst alpha 255)."""
    region = dst[top:top + src.shape[0], left:left + src.shape[1]]
    a = src[..., 3]
    full = a == 255
    region[full, :3] = src[full, :3]
    part = (a > 0) & ~full
    s = src[part].astype(np.int32)
    d = region[part, :3].astype(np.int32)
    al = s[:, 3:4]
    r = d * 255
    den = (al << 8) + 65280 - al * 255
    region[part, :3] = ((((s[:, :3] << 8) - r) * al + (r << 8)) // den).astype(np.uint8)


def composite_agg(base, layers):
    from PIL import Image

    out = np.array(base, copy=True)
    for top, left, arr in layers:
        agg_over(out, top, left, arr)
    return Image.fromarray(out, "RGBA")


# --- Loading -------------------------------------------------------------------


def decode_raster(data: bytes, layer: dict):
    if layer["encoding"] == "png":
        from PIL import Image

        return np.asarray(Image.open(io.BytesIO(data)))
    if layer["encoding"] == "zlib":
        data = zlib.decompress(data)
    return np.frombuffer(data, np.uint8).reshape(layer["shape"])


def decode_array(data: bytes, meta: dict):
    if meta["encoding"] == "zlib":
        data = zlib.decompress(data)
    return np.frombuffer(data, np.dtype(meta["dtype"])).reshape(meta["shape"])


def load_scene(path: Path) -> tuple[dict, dict]:
    """Eagerly load a prototype file; return (scene, timings)."""
    timings: dict[str, float] = {}
    t = time.perf_counter()
    zf = zipfile.ZipFile(path)
    manifest = json.loads(zf.read("manifest.json"))
    timings["manifest"] = time.perf_counter() - t
    rasters = {}
    for layer in manifest["layers"]:
        t = time.perf_counter()
        data = zf.read(layer["member"])
        timings[f"read_{layer['id']}"] = time.perf_counter() - t
        t = time.perf_counter()
        rasters[layer["id"]] = decode_raster(data, layer)
        timings[f"decode_{layer['id']}"] = time.perf_counter() - t
    fonts = {}
    t = time.perf_counter()
    for ts in manifest["text_slots"].values():
        fonts[ts["font"]] = zf.read(ts["font"])
    timings["read_fonts"] = time.perf_counter() - t
    prep = {}
    for name, slot in manifest["slots"].items():
        if slot["kind"] == "grid":
            t = time.perf_counter()
            index_map = decode_array(zf.read(slot["index_map"]["member"]), slot["index_map"])
            lut = decode_array(zf.read(slot["lut"]["member"]), slot["lut"])
            flat = index_map.ravel()
            pixels = np.flatnonzero(flat >= 0)
            prep[name] = {"lut": lut, "pixels": pixels, "cells": flat[pixels]}
            timings[f"load_{name}_index_map"] = time.perf_counter() - t
    zf.close()
    canvas = manifest["canvas"]
    return {"manifest": manifest, "rasters": rasters, "fonts": fonts, "prep": prep,
            "shape": (canvas["height"], canvas["width"])}, timings


def render(scene: dict, data: dict, text: str, k: int | None = None,
           compositor: str = "pillow") -> tuple:
    """Render new data and subtitle into the stack; return (PIL image, timings)."""
    m = scene["manifest"]
    timings = {}
    layers = []
    base = None
    for entry in m["stack"]:
        if entry.startswith("slot:"):
            name = entry[5:]
            slot = m["slots"][name]
            t = time.perf_counter()
            if slot["kind"] == "polygon":
                arr = render_polygons(data["rings"], data["values"], slot, m["georef"],
                                      scene["shape"], k=k or slot["supersample"])
            else:
                arr = render_grid(data["values"], slot, scene["prep"][name], scene["shape"])
            timings["data"] = time.perf_counter() - t
            layers.append((0, 0, arr))
        elif entry.startswith("text:"):
            ts = m["text_slots"][entry[5:]]
            t = time.perf_counter()
            top, left, arr = render_text(text, ts, scene["fonts"][ts["font"]], scene["shape"])
            timings["text"] = time.perf_counter() - t
            layers.append((top, left, arr))
        elif base is None:
            base = scene["rasters"][entry]
        else:
            layers.append((0, 0, scene["rasters"][entry]))
    t = time.perf_counter()
    img = (composite_agg if compositor == "agg" else composite_pillow)(base, layers)
    timings["composite"] = time.perf_counter() - t
    return img, timings


def encode_png(img, mode: str, level: int) -> bytes:
    buf = io.BytesIO()
    (img.convert("RGB") if mode == "rgb" else img).save(buf, format="PNG", compress_level=level)
    return buf.getvalue()


# --- Benchmark harness -----------------------------------------------------------

QPF_DAYS = [
    "1", "2", "3", "4", "5", "6", "7", "1-2", "1-3", "1-5", "4-5", "6-7", "1-7",
    "1_6hr_f00-f06", "1_6hr_f06-f12", "1_6hr_f12-f18", "1_6hr_f18-f24", "1_6hr_f24-f30",
]


def import_stack() -> dict[str, float]:
    times = {"numpy": T_NUMPY}
    t = time.perf_counter()
    import PIL.Image
    import PIL.ImageChops
    import PIL.ImageDraw
    import PIL.ImageFont

    PIL.Image.preinit()  # registers the PNG plugin; otherwise paid inside the first decode
    times["PIL"] = time.perf_counter() - t
    return times


def forbidden_modules() -> list[str]:
    return sorted(m for m in sys.modules if m.split(".")[0] in FORBIDDEN)


def peak_rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss if sys.platform == "darwin" else rss * 1024


def products(args) -> list[tuple[str, dict, str]]:
    """(name, data, subtitle) inputs for the child's scene."""
    out = []
    if args.scene == "qpf":
        for day in args.items:
            t = time.perf_counter()
            q = read_qpf(DATA / "qpf" / f"day_{day}")
            q["read_s"] = time.perf_counter() - t
            out.append((f"qpf_plot_day_{day}", q, q["subtitle"]))
    else:
        sys.path.insert(0, str(Path(__file__).parent))
        from baseline_cartopy import make_field, title_text  # stdlib-only module top level

        for v in args.items:
            t = time.perf_counter()
            _, _, values = make_field(int(v))
            read_s = time.perf_counter() - t
            out.append((f"variant_{v}", {"values": values, "read_s": read_s},
                        title_text(int(v))[1]))
    return out


def child(args) -> dict:
    t_main = time.perf_counter()
    imports = import_stack()
    scene, load_t = load_scene(Path(args.file))
    load_s = sum(load_t.values())
    outdir = Path(args.outdir) if args.outdir else None
    write_mode = "rgba" if args.scene == "qpf" else "rgb"  # match each baseline's PNG mode
    results = []
    for name, data, subtitle in products(args):
        img, ph = render(scene, data, subtitle, k=args.k, compositor=args.compositor)
        ph = {"read_input": data["read_s"], **ph}
        t = time.perf_counter()
        png = encode_png(img, write_mode, 6)
        ph[f"encode_{write_mode}_cl6"] = time.perf_counter() - t
        if outdir:
            t = time.perf_counter()
            (outdir / f"{name}.png").write_bytes(png)
            ph["write_file"] = time.perf_counter() - t
        ph["render_total"] = sum(v for k_, v in ph.items() if k_ != "read_input")
        extra = {}
        for mode, level in (("rgba", 1), ("rgb", 1), ("rgb" if write_mode == "rgba" else "rgba", 6)):
            t = time.perf_counter()
            extra[f"encode_{mode}_cl{level}"] = {"s": 0.0, "bytes": len(encode_png(img, mode, level))}
            extra[f"encode_{mode}_cl{level}"]["s"] = time.perf_counter() - t
        results.append({"name": name, "phases_s": ph, "png_bytes": len(png),
                        "other_encodes": extra})
    end = time.perf_counter()
    other_encode_s = sum(e["s"] for r in results for e in r["other_encodes"].values())
    return {
        "imports_s": imports,
        "pre_main_s": t_main - T0_PERF,
        "load_s": load_s,
        "load_detail_s": load_t,
        "products": results,
        "end_to_end_in_process_s": end - T0_PERF - other_encode_s,
        "script_start_wall": T0_WALL,
        "peak_rss_bytes": peak_rss_bytes(),
        "forbidden_modules_loaded": forbidden_modules(),
        "modules_loaded": len(sys.modules),
    }


def run_child(argv: list[str]) -> dict:
    t_spawn = time.time()
    t = time.perf_counter()
    proc = subprocess.run([sys.executable, __file__, *argv], capture_output=True, text=True,
                          check=False)
    wall = time.perf_counter() - t
    if proc.returncode != 0:
        sys.exit(f"child {argv} failed:\n{proc.stderr}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    if result["forbidden_modules_loaded"]:
        sys.exit(f"runtime imported forbidden modules: {result['forbidden_modules_loaded']}")
    result["interpreter_start_s"] = result["script_start_wall"] - t_spawn
    result["process_wall_s"] = wall
    result["argv"] = argv
    return result


def summarize(values: list[float]) -> dict:
    return {"median": statistics.median(values), "min": min(values), "max": max(values),
            "n": len(values)}


def orchestrate(args) -> None:
    proto = Path(args.dir) if args.dir else max(RESULTS.glob("prototype-*"))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    outdir = proto / f"runtime-{stamp}"
    outdir.mkdir()
    runs: dict[str, list[dict]] = {}

    def run(key, scene, file, items, n=1, save=False, **opts):
        argv = ["--child", "--scene", scene, "--file", str(proto / file), "--items", *items]
        for opt, val in opts.items():
            argv += [f"--{opt}", str(val)]
        for i in range(n):
            extra = []
            if save and i == 0:
                d = outdir / key
                d.mkdir()
                extra = ["--outdir", str(d)]
            r = run_child(argv + extra)
            runs.setdefault(key, []).append(r)
        r = runs[key][-1]
        print(f"{key:28} {n} run(s): wall {statistics.median(x['process_wall_s'] for x in runs[key]):6.3f} s, "
              f"load {r['load_s'] * 1000:6.1f} ms, products {len(r['products'])}")

    bare = []
    for _ in range(5):
        t = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        bare.append(time.perf_counter() - t)
    run_child(["--child", "--scene", "qpf", "--file", str(proto / "qpf-png.cstack"),
               "--items", "1-3"])  # priming (bytecode, OS cache); not recorded

    for enc in ("png", "raw", "zlib"):
        run(f"qpf-single-{enc}", "qpf", f"qpf-{enc}.cstack", ["1-3"], n=5)
    for enc in ("png", "raw", "zlib"):
        run(f"qpf-loop-{enc}", "qpf", f"qpf-{enc}.cstack", QPF_DAYS, n=2, save=enc == "png")
    for k in (1, 4):
        run(f"qpf-loop-png-k{k}", "qpf", "qpf-png.cstack", QPF_DAYS, save=True, k=k)
    run("qpf-loop-png-agg", "qpf", "qpf-png.cstack", QPF_DAYS, save=True, compositor="agg")
    for enc in ("png", "zlib"):
        run(f"qpf-loop-{enc}-split", "qpf", f"qpf-{enc}-split.cstack", QPF_DAYS,
            save=enc == "png")
    for enc in ("png", "raw", "zlib"):
        run(f"grid-single-{enc}", "grid", f"grid-{enc}.cstack", ["0"], n=5)
    run("grid-variants-png", "grid", "grid-png.cstack", ["0", "1", "2", "3"], n=2, save=True)
    run("grid-variants-png-agg", "grid", "grid-png.cstack", ["0", "1", "2", "3"], save=True,
        compositor="agg")

    summary = {}
    for key, rs in runs.items():
        per = [p for r in rs for p in r["products"]]
        # write_file only exists for runs that saved images.
        phase_keys = [k_ for k_ in per[0]["phases_s"] if all(k_ in p["phases_s"] for p in per)]
        summary[key] = {
            "runs": len(rs),
            "products_per_run": len(rs[0]["products"]),
            "process_wall_s": summarize([r["process_wall_s"] for r in rs]),
            "interpreter_start_s": summarize([r["interpreter_start_s"] for r in rs]),
            "imports_s": {k_: summarize([r["imports_s"][k_] for r in rs]) for k_ in rs[0]["imports_s"]},
            "load_s": summarize([r["load_s"] for r in rs]),
            "load_detail_s": {k_: summarize([r["load_detail_s"][k_] for r in rs])
                              for k_ in rs[0]["load_detail_s"]},
            "per_product_phases_s": {k_: summarize([p["phases_s"][k_] for p in per])
                                     for k_ in phase_keys},
            "end_to_end_in_process_s": summarize([r["end_to_end_in_process_s"] for r in rs]),
            "peak_rss_bytes": summarize([r["peak_rss_bytes"] for r in rs]),
            "other_encodes_s": {k_: summarize([p["other_encodes"][k_]["s"] for p in per])
                                for k_ in per[0]["other_encodes"]},
            "modules_loaded": rs[0]["modules_loaded"],
        }
    result = {
        "created_utc": stamp,
        "prototype_dir": str(proto.relative_to(ROOT)),
        "python": sys.version,
        "numpy": np.__version__,
        "pillow": __import__("PIL").__version__,
        "bare_interpreter_s": summarize(bare),
        "caches": "OS file cache and bytecode warm (priming run)",
        "forbidden_checked": list(FORBIDDEN),
        "summary": summary,
        "runs": runs,
    }
    (outdir / "runtime.json").write_text(json.dumps(result, indent=1, default=float) + "\n")
    print(f"wrote {outdir.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", help="prototype build directory (default: latest)")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--scene", choices=["qpf", "grid"], help=argparse.SUPPRESS)
    parser.add_argument("--file", help=argparse.SUPPRESS)
    parser.add_argument("--items", nargs="+", help=argparse.SUPPRESS)
    parser.add_argument("--outdir", help=argparse.SUPPRESS)
    parser.add_argument("--k", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--compositor", default="pillow", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        print(json.dumps(child(args), default=float))
    else:
        orchestrate(args)


if __name__ == "__main__":
    main()
