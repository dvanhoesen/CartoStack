"""Synthetic daily hazard rasters and the NumPy helpers shared by the hazard examples.

Many gridded products arrive as GeoTIFFs on a Web Mercator grid with integer categories.
This module makes a synthetic week of them (``make_inputs``) and holds what both the
Matplotlib script (``hazard_map.py``) and the CartoStack job (``update_hazard.py``) need,
using only NumPy and Pillow:

* ``read_geotiff``: the category band and its grid, read with Pillow (GeoTIFF tags);
* ``reprojection_index``: for every cell of a lon/lat grid, the nearest source pixel;
* ``area_shares``: the share of a zone's area in each level, from per-pixel weights.

Levels are 0 (none) to 4 (extreme); 5 is no data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

LEVELS = ("None", "Low", "Moderate", "High", "Extreme")  # values 0..4
NODATA = 5
R = 6378137.0  # Web Mercator sphere radius (m)

# The synthetic source grid: Web Mercator, 2.5 km pixels over the eastern US.
SRC_X0, SRC_Y0 = -10_000_000.0, 6_400_000.0  # upper-left corner (m)
SRC_RES = 2500.0
SRC_SHAPE = (1000, 1100)  # (rows, cols)
FIRST_DAY = datetime(2026, 7, 14, tzinfo=UTC)


@dataclass(frozen=True)
class Raster:
    values: np.ndarray  # (rows, cols) uint8
    x0: float  # upper-left x (m)
    y0: float  # upper-left y (m)
    res: float  # pixel size (m)
    nodata: int

    @property
    def signature(self) -> tuple:
        """What the stored reprojection index depends on."""
        return (*self.values.shape, self.x0, self.y0, self.res, self.nodata)


def _field(day: int) -> np.ndarray:
    """A smooth heat-like field: it builds over land through mid-week, then fades."""
    rows, cols = SRC_SHAPE
    y = SRC_Y0 - (np.arange(rows) + 0.5) * SRC_RES
    x = SRC_X0 + (np.arange(cols) + 0.5) * SRC_RES
    lat = np.degrees(2 * np.arctan(np.exp(y / R)) - math.pi / 2)[:, None]
    lon = np.degrees(x / R)[None, :]
    rng = np.random.default_rng(day)
    strength = (0.6, 1.2, 1.8, 2.3, 1.7, 0.9, 0.05)[(day - 1) % 7]
    f = 1.3 - 0.09 * (lat - 36.0)
    for k in range(3):
        cx = -80.0 + 1.6 * day + 3.0 * k + rng.normal(0, 0.6)
        cy = 41.0 + 1.2 * math.sin(day + 2 * k) + rng.normal(0, 0.4)
        f = f + strength * np.exp(-((lon - cx) ** 2 / 6.0 + (lat - cy) ** 2 / 2.5))
    return f + 0.2 * np.sin(lon * 0.9 + day) * np.cos(lat * 1.3)


def make_inputs(out: Path, days: int = 7) -> list[Path]:
    """Write ``hazard_day<N>.tif`` for each day: uint8 levels, GeoTIFF-tagged, LZW."""
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for day in range(1, days + 1):
        levels = np.digitize(_field(day), [1.2, 1.8, 2.4, 3.0]).astype(np.uint8)
        levels[:40, :] = NODATA  # a strip outside the product's coverage
        img = Image.fromarray(levels, mode="L")
        tags = {
            33550: (SRC_RES, SRC_RES, 0.0),  # ModelPixelScale
            33922: (0.0, 0.0, 0.0, SRC_X0, SRC_Y0, 0.0),  # ModelTiepoint
            42113: str(NODATA),  # GDAL_NODATA
            270: f"synthetic hazard levels, valid {valid_date(day):%Y-%m-%d}",
        }
        path = out / f"hazard_day{day}.tif"
        img.save(path, compression="tiff_lzw", tiffinfo=tags)
        paths.append(path)
    return paths


def valid_date(day: int) -> datetime:
    return FIRST_DAY + timedelta(days=day - 1)


def titles(day: int) -> tuple[str, str]:
    """The map title and subtitle for a day."""
    return (
        f"Hazard level, {valid_date(day):%a %b %-d, %Y}",
        f"Day {day} of 7 · synthetic daily outlook",
    )


def read_geotiff(path: Path) -> Raster:
    """The first band and its north-up grid, from the GeoTIFF tags (Pillow only)."""
    with Image.open(path) as im:
        values = np.asarray(im)
        sx, sy, _ = im.tag_v2[33550]
        _, _, _, x0, y0, _ = im.tag_v2[33922]
        nodata = int(float(im.tag_v2.get(42113, NODATA)))
    if sx != sy:
        raise ValueError(f"{path}: non-square pixels are not supported")
    return Raster(values, float(x0), float(y0), float(sx), nodata)


def reprojection_index(src: Raster, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Flat index of the source pixel nearest each (lat, lon) cell centre; -1 outside."""
    x = R * np.radians(lon)[None, :]
    y = R * np.log(np.tan(math.pi / 4 + np.radians(lat)[:, None] / 2))
    col = np.floor((x - src.x0) / src.res).astype(np.int64)
    row = np.floor((src.y0 - y) / src.res).astype(np.int64)
    rows, cols = src.values.shape
    inside = (row >= 0) & (row < rows) & (col >= 0) & (col < cols)
    return np.where(inside, row * cols + col, -1).astype(np.int32)


def regrid(src: Raster, index: np.ndarray) -> np.ndarray:
    """The source values on the lon/lat grid of ``index`` (no data where it is -1)."""
    flat = src.values.ravel()
    return np.where(index >= 0, flat[np.maximum(index, 0)], src.nodata).astype(np.uint8)


def area_shares(src: Raster, pixels: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    """Percent of the zone's area in each level (one decimal); no-data area is left out."""
    sums = np.bincount(src.values.ravel()[pixels], weights=weights, minlength=256)[: len(LEVELS)]
    total = sums.sum()
    if total <= 0:
        return dict.fromkeys(LEVELS, 0.0)
    return {name: round(float(v / total * 100), 1) for name, v in zip(LEVELS, sums, strict=True)}
