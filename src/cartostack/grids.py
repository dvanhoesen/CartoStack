"""Grid slots: new values → pixels through the stored index map and LUT (``docs/format.md`` §8).

Nothing is projected at runtime. Each pixel's grid cell is fixed by the slot's
``index_map`` (``-1``: no data), so a render is a normalisation and colour lookup
per cell followed by one gather per pixel.

Values are coloured with Matplotlib's ``Normalize`` and ``Colormap.__call__``
rules, reproduced exactly (``tests/test_grids.py`` compares them with Matplotlib):

1. Values are converted to the slot's ``value_dtype``. ``x = value - vmin`` and then
   ``x = x / (vmax - vmin)`` are each computed in float64 and rounded back to
   ``value_dtype``, which is what Matplotlib's in-place NumPy operations do.
   Computing in float32 throughout differs for many ranges (e.g. 0.1..0.7).
2. ``x *= n_colors`` in ``value_dtype``; ``x == n_colors`` becomes ``n_colors - 1``.
3. The LUT row is ``under`` (``x < 0``), ``over`` (``x >= n_colors``), ``bad`` (NaN or
   masked), else ``trunc(x)``.
4. With a slot ``alpha``, the alpha channel becomes ``floor(alpha * 255 + 0.5)``,
   except that an all-zero ``bad`` colour stays fully transparent (Matplotlib ignores
   alpha for it).
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .errors import CartoStackError
from .layers import RGBA, GridSlot


class GridMismatchError(CartoStackError, ValueError):
    """Values do not fit the grid slot (shape or dtype)."""


def check_values(
    slot: GridSlot, values: ArrayLike
) -> tuple[NDArray[np.floating], NDArray[np.bool_] | None]:
    """Validate ``values`` against ``slot``: ``(data in value_dtype, mask or None)``.

    ``values`` must have exactly the slot's ``shape`` ``(ny, nx)`` and a real numeric
    dtype (integers or floats; not bool or complex). Masked arrays mark masked
    cells as bad. Values are converted to the slot's ``value_dtype``.
    """
    mask = None
    if np.ma.isMaskedArray(values):
        mask = np.ma.getmaskarray(values)
        arr = np.asarray(np.ma.getdata(values))
    else:
        arr = np.asarray(values)
    if arr.dtype == np.bool_ or arr.dtype.kind not in "iuf":
        raise GridMismatchError(
            f"{slot.id}: values must be real numbers (integer or float), got dtype {arr.dtype}"
        )
    if arr.shape != slot.shape:
        raise GridMismatchError(
            f"{slot.id}: values have shape {arr.shape}, the grid is {slot.shape} (ny, nx)"
        )
    data = arr.astype(np.dtype(slot.value_dtype), copy=False)
    if mask is not None and not mask.any():
        mask = None
    return data, mask


def lut_rows(slot: GridSlot, values: ArrayLike) -> NDArray[np.intp]:
    """The LUT row of every cell, flattened in cell order (``j * nx + i``)."""
    data, mask = check_values(slot, values)
    dtype = data.dtype
    n = slot.n_colors
    # Each step in float64, rounded to value_dtype, as Matplotlib's in-place operations do.
    x = (data.ravel().astype(np.float64) - slot.vmin).astype(dtype)
    x = (x.astype(np.float64) / (slot.vmax - slot.vmin)).astype(dtype)
    x *= dtype.type(n)
    x[x == n] = n - 1
    with np.errstate(invalid="ignore"):
        rows = x.astype(np.intp)  # trunc; the out-of-range rows are replaced below
    rows[x < 0] = n
    rows[x >= n] = n + 1
    bad = np.isnan(x)
    if mask is not None:
        bad |= mask.ravel()
    rows[bad] = n + 2
    return rows


def cell_colors(slot: GridSlot, values: ArrayLike) -> RGBA:
    """RGBA colour of every cell, ``(ny * nx, 4)`` uint8."""
    lut = np.array(slot.lut)
    if slot.alpha is not None:
        bad = lut[-1].copy()
        lut[:, 3] = math.floor(slot.alpha * 255 + 0.5)
        if not bad.any():
            lut[-1] = 0
    colors: RGBA = lut[lut_rows(slot, values)]
    return colors


def render(slot: GridSlot, values: ArrayLike) -> RGBA:
    """New pixels for ``slot``: ``(height, width, 4)`` uint8; index ``-1`` is transparent."""
    colors = cell_colors(slot, values)
    table = np.zeros(colors.shape[0] + 1, np.uint32)  # row 0: transparent, for index -1
    table[1:] = colors.view(np.uint32).ravel()
    shifted = slot.index_map + np.int32(1)
    out = table[shifted]
    pixels: RGBA = out.view(np.uint8).reshape(*shifted.shape, 4)
    return pixels
