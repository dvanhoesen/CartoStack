"""Small scenes and archive surgery for the I/O tests."""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from cartostack import (
    CanvasGeometry,
    ColorbarLayer,
    GridSlot,
    PolygonSlot,
    RasterLayer,
    Scene,
    TextSlot,
)
from cartostack.geometry import Projection
from cartostack.manifest import Bin

EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "examples"
FONT = b"\x00\x01\x00\x00not-a-real-font-but-bytes-are-bytes"


def small_georeferenced_geometry(scale: float = 0.1) -> CanvasGeometry:
    """The QPF geometry scaled down (same projection and extent, fewer pixels)."""
    g = json.loads((EXAMPLES / "qpf.manifest.json").read_text())["geometry"]
    g["figure"] = None
    g["canvas"] = {
        "width": int(g["canvas"]["width"] * scale),
        "height": int(g["canvas"]["height"] * scale),
        "dpi": 30.0,
    }
    g["axes"] = {k: v * scale for k, v in g["axes"].items()}
    g["georeference"]["world_to_pixel"] = [t * scale for t in g["georeference"]["world_to_pixel"]]
    return CanvasGeometry.from_dict(g)


def gradient(height: int, width: int, seed: int = 0) -> np.ndarray:
    """Deterministic, compressible RGBA test pattern with partial alpha."""
    yy, xx = np.mgrid[0:height, 0:width]
    out = np.empty((height, width, 4), np.uint8)
    out[..., 0] = (xx * 3 + seed) % 256
    out[..., 1] = (yy * 5 + seed) % 256
    out[..., 2] = ((xx + yy) * 7) % 256
    out[..., 3] = ((xx // 8 + yy // 8) % 2) * 127 + 128
    return out


def full_scene(encoding: str = "rgba8+zlib", index_encoding: str = "i32le+zlib") -> Scene:
    g = small_georeferenced_geometry()
    h, w = g.height, g.width
    ny, nx = 12, 9
    index_map = (np.arange(h * w, dtype=np.int32).reshape(h, w) % (ny * nx)).astype(np.int32)
    index_map[:2] = -1
    lut = np.zeros((256 + 3, 4), np.uint8)
    lut[:256, 0] = np.arange(256)
    lut[:, 3] = 255
    lut[256:] = [[0, 0, 255, 255], [255, 0, 0, 255], [0, 0, 0, 0]]
    layers = [
        RasterLayer(id="below", order=0, pixels=gradient(h, w), encoding=encoding),
        GridSlot(
            id="temperature",
            order=10,
            shape=(ny, nx),
            index_map=index_map,
            lut=lut,
            vmin=-10.0,
            vmax=35.0,
            alpha=0.8,
            extend="both",
            pixels=gradient(h, w, 1),
            encoding=encoding,
            index_encoding=index_encoding,
        ),
        PolygonSlot(
            id="qpf",
            order=12,
            supersample=2,
            round_decimals=2,
            bins=(Bin(0.01, 0.1, (127, 255, 0, 255), 11), Bin(0.1, 0.25, (0, 205, 0, 255), 12)),
            fallback=Bin(0.0, 0.0, (128, 128, 128, 255), 10),
        ),
        RasterLayer(
            id="above",
            order=20,
            left=-3,
            top=5,
            pixels=gradient(40, 60, 2),
            opacity=0.5,
            encoding=encoding,
        ),
        ColorbarLayer(
            id="cbar",
            order=25,
            slot="temperature",
            pixels=gradient(8, 50, 3),
            left=10,
            top=150,
            encoding=encoding,
        ),
        TextSlot(
            id="subtitle",
            order=30,
            value="Valid 12:00 UTC",
            font="fonts/Test.ttf",
            size_px=45.833,
            color=(0, 0, 0, 255),
            x=110.5,
            y=15.7,
            ha="center",
            va="bottom",
            snap="round",
            offset=(0.0, -1.0),
            visible=False,
        ),
    ]
    return Scene(
        g,
        layers,
        assets={"fonts/Test.ttf": FONT, "notes/readme.txt": b"kept"},
        provenance={"cartostack": "test", "created_utc": "2026-10-08T00:00:00Z"},
    )


def build_archive(
    path: Path,
    manifest: Mapping[str, Any] | bytes,
    members: Mapping[str, bytes],
    *,
    mimetype: bytes | None = b"application/x-cartostack",
    mimetype_first: bool = True,
    compress: frozenset[str] = frozenset(),
    restamp: bool = True,
) -> None:
    """Write an arbitrary (possibly broken) archive; ``restamp`` recomputes the members table."""
    if isinstance(manifest, Mapping) and restamp:
        manifest = dict(manifest)
        manifest["members"] = {
            n: {"bytes": len(d), "sha256": hashlib.sha256(d).hexdigest()}
            for n, d in sorted(members.items())
        }
    data = manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode()
    entries: list[tuple[str, bytes]] = [("manifest.json", data), *members.items()]
    if mimetype is not None:
        entries.insert(0 if mimetype_first else 1, ("mimetype", mimetype))
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in entries:
            method = zipfile.ZIP_DEFLATED if name in compress else zipfile.ZIP_STORED
            zf.writestr(name, payload, compress_type=method)


def unpack(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        members = {n: zf.read(n) for n in zf.namelist() if n not in ("mimetype", "manifest.json")}
    return manifest, members


def rewrite(
    path: Path, edit: Callable[[dict[str, Any], dict[str, bytes]], None], **kw: Any
) -> None:
    manifest, members = unpack(path)
    edit(manifest, members)
    build_archive(path, manifest, members, **kw)


def png_bytes(arr: np.ndarray, mode: str) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(arr).convert(mode).save(buf, format="PNG")
    return buf.getvalue()


def lcc_inverse(p: Projection, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Snyder's inverse (eq. 15-9, 7-9 iterated), written independently of the forward code."""
    q = p.as_dict()
    e = math.sqrt(q["f"] * (2 - q["f"]))

    def m(phi: float) -> float:
        return math.cos(phi) / math.sqrt(1 - (e * math.sin(phi)) ** 2)

    def t(phi: float) -> float:
        s = e * math.sin(phi)
        return math.tan(math.pi / 4 - phi / 2) / ((1 - s) / (1 + s)) ** (e / 2)

    p1, p2, p0 = (math.radians(q[k]) for k in ("lat_1", "lat_2", "lat_0"))
    n = (
        math.sin(p1)
        if math.isclose(p1, p2)
        else (math.log(m(p1)) - math.log(m(p2))) / (math.log(t(p1)) - math.log(t(p2)))
    )
    big_f = m(p1) / (n * t(p1) ** n)
    rho0 = q["a"] * big_f * t(p0) ** n
    dx, dy = x - q["x_0"], rho0 - (y - q["y_0"])
    sign = math.copysign(1.0, n)
    rho = sign * np.hypot(dx, dy)
    theta = np.arctan2(sign * dx, sign * dy)
    tt = (rho / (q["a"] * big_f)) ** (1 / n)
    phi = np.pi / 2 - 2 * np.arctan(tt)
    for _ in range(20):
        s = e * np.sin(phi)
        phi = np.pi / 2 - 2 * np.arctan(tt * ((1 - s) / (1 + s)) ** (e / 2))
    return np.degrees(theta / n) + q["lon_0"], np.degrees(phi)


def map_geometry(
    width: int = 20,
    height: int = 16,
    axes: tuple[float, float, float, float] | None = None,
    half_width_m: float = 200_000.0,
) -> CanvasGeometry:
    """A small LCC canvas (the QPF projection) whose axes are ``(left, top, width, height)``.

    The projected extent is centred on the projection origin and scaled to the axes, so
    the georeference is consistent by construction.
    """
    left, top, aw, ah = axes if axes is not None else (0.0, 0.0, float(width), float(height))
    g = json.loads((EXAMPLES / "qpf.manifest.json").read_text())["geometry"]
    xmin, xmax = -half_width_m, half_width_m
    ymin, ymax = -half_width_m * ah / aw, half_width_m * ah / aw
    a, e = aw / (xmax - xmin), -ah / (ymax - ymin)
    g["figure"] = None
    g["canvas"] = {"width": width, "height": height, "dpi": 100.0}
    g["axes"] = {"left": left, "top": top, "width": aw, "height": ah}
    g["georeference"]["projected_extent"] = {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}
    g["georeference"]["world_to_pixel"] = [a, 0.0, left - a * xmin, 0.0, e, top - e * ymax]
    return CanvasGeometry.from_dict(g)


def pixels_to_lonlat(g: CanvasGeometry, ring: object) -> np.ndarray:
    """Lon/lat whose projection lands on the given continuous pixel coordinates."""
    geo = g.georeference
    assert geo is not None
    pts = np.asarray(ring, np.float64)
    world = np.array([geo.to_world(c, r) for c, r in pts])
    lon, lat = lcc_inverse(geo.projection, world[:, 0], world[:, 1])
    return np.column_stack([lon, lat])
