"""Grid index maps by the index-image method (``docs/format.md`` §8).

The grid is drawn once with ``pcolormesh`` exactly as a data render would place it, but
with anti-aliasing off and each cell's face colour encoding its flat index
``j * nx + i`` in 24 bits. Decoding the rendered pixels gives, for every pixel, the cell
Matplotlib draws there. Pixels that are not fully covered (edges of the axes clip path)
and pixels outside the clip region become ``-1``.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
from numpy.typing import NDArray

MAX_CELLS = 2**24 - 1


def _shape(lon: NDArray[Any], lat: NDArray[Any], cells: str) -> tuple[int, int]:
    edge = 1 if cells == "edges" else 0
    if lon.ndim == 1 and lat.ndim == 1:
        return lat.size - edge, lon.size - edge
    if lon.shape != lat.shape or lon.ndim != 2:
        raise ValueError("lon and lat must both be 1-D, or 2-D with the same shape")
    return lon.shape[0] - edge, lon.shape[1] - edge


def grid_shape(lon: Any, lat: Any, cells: str = "centers") -> tuple[int, int]:
    """``(ny, nx)`` of the values for cell ``centers`` or ``edges`` coordinates."""
    if cells not in ("centers", "edges"):
        raise ValueError(f"cells must be 'centers' or 'edges', got {cells!r}")
    ny, nx = _shape(np.asarray(lon), np.asarray(lat), cells)
    if ny < 1 or nx < 1:
        raise ValueError("the grid needs at least one cell")
    return ny, nx


def index_map(
    fig: Any,
    ax: Any,
    lon: Any,
    lat: Any,
    *,
    cells: str = "centers",
    dpi: float,
    crop: tuple[float, float, float, float] | None,
    hide: Any = (),
    clip: tuple[float, float, float, float] | None = None,
) -> tuple[NDArray[np.int32], int]:
    """``(index_map, partial_pixels)`` for a grid drawn on ``ax``, at the saved size.

    ``lon``/``lat`` are geodetic degrees (``PlateCarree``): 1-D or 2-D cell centres
    (``cells="centers"``, ``shading="nearest"``) or cell edges (``cells="edges"``,
    ``shading="flat"``). ``hide`` lists artists to hide while drawing (everything else
    already on the figure). ``clip`` is the clip region ``(left, top, right, bottom)`` in
    canvas pixels; pixels whose centre lies outside it become ``-1``.
    """
    import cartopy.crs as ccrs
    import matplotlib.pyplot as plt
    from matplotlib.collections import QuadMesh
    from matplotlib.transforms import Bbox
    from PIL import Image

    ny, nx = grid_shape(lon, lat, cells)
    if ny * nx > MAX_CELLS:
        raise ValueError(
            f"grid has {ny * nx} cells; the index-image method encodes at most {MAX_CELLS}"
        )
    idx = np.arange(ny * nx)
    codes = (
        np.column_stack([(idx >> 16) & 255, (idx >> 8) & 255, idx & 255, np.full(idx.size, 255)])
        / 255.0
    )
    probe = ax.pcolormesh(
        np.asarray(lon),
        np.asarray(lat),
        np.zeros((ny, nx), np.float32),
        transform=ccrs.PlateCarree(),
        shading="flat" if cells == "edges" else "nearest",
        antialiased=False,
        linewidth=0,
        edgecolors="none",
    )
    QuadMesh.set_array(probe, None)  # GeoQuadMesh.set_array rejects None
    probe.set_facecolor(codes)
    hidden = [a for a in hide if a.get_visible()]
    patches = [p for p in (fig.patch, ax.patch) if p.get_visible()]
    try:
        for artist in (*hidden, *patches):
            artist.set_visible(False)
        buf = io.BytesIO()
        kwargs = {"bbox_inches": Bbox.from_bounds(*crop), "pad_inches": 0} if crop else {}
        plt.figure(fig.number)
        fig.savefig(buf, format="png", dpi=dpi, transparent=True, **kwargs)
    finally:
        for artist in (*hidden, *patches):
            artist.set_visible(True)
        probe.remove()
    img = np.asarray(Image.open(buf).convert("RGBA")).astype(np.int32)
    out = ((img[..., 0] << 16) | (img[..., 1] << 8) | img[..., 2]).astype(np.int32)
    full = img[..., 3] == 255
    partial = int(((img[..., 3] > 0) & ~full).sum())
    out[~full] = -1
    out[out >= ny * nx] = -1
    if clip is not None:
        left, top, right, bottom = clip
        rows = np.arange(out.shape[0]) + 0.5
        cols = np.arange(out.shape[1]) + 0.5
        out[(rows < top) | (rows >= bottom), :] = -1
        out[:, (cols < left) | (cols >= right)] = -1
    return np.ascontiguousarray(out), partial
