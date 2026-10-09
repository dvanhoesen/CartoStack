"""CartoStack: layered map files (``.cstack``) and a NumPy/Pillow-only runtime.

The runtime imports only NumPy and Pillow. Authoring files from Matplotlib and
Cartopy lives behind the ``build`` extra (``cartostack.build``) and is never
imported by ``import cartostack``. Manifest and geometry validation
(``cartostack.manifest``, ``cartostack.geometry``) need only the standard
library, so the NumPy-backed names below are imported on first use.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.0.1.dev0"

from .errors import CartoStackError, FormatError, GeometryError, UnsupportedVersionError
from .geometry import CanvasGeometry

if TYPE_CHECKING:
    from .colorbars import ColorbarRedrawError
    from .grids import GridMismatchError
    from .layers import (
        ColorbarLayer,
        ColorbarRedraw,
        GridSlot,
        Layer,
        LayerError,
        PolygonSlot,
        RasterLayer,
        TextSlot,
    )
    from .polygons import PolygonDataError
    from .projection import ProjectionError
    from .scene import Scene, SceneError
    from .text import TextFontError

_LAZY = {
    "ColorbarLayer": "layers",
    "ColorbarRedraw": "layers",
    "ColorbarRedrawError": "colorbars",
    "GridMismatchError": "grids",
    "GridSlot": "layers",
    "Layer": "layers",
    "LayerError": "layers",
    "PolygonDataError": "polygons",
    "PolygonSlot": "layers",
    "ProjectionError": "projection",
    "RasterLayer": "layers",
    "TextSlot": "layers",
    "Scene": "scene",
    "SceneError": "scene",
    "TextFontError": "text",
}

__all__ = [
    "CanvasGeometry",
    "CartoStackError",
    "ColorbarLayer",
    "ColorbarRedraw",
    "ColorbarRedrawError",
    "FormatError",
    "GeometryError",
    "GridMismatchError",
    "GridSlot",
    "Layer",
    "LayerError",
    "PolygonDataError",
    "PolygonSlot",
    "ProjectionError",
    "RasterLayer",
    "Scene",
    "SceneError",
    "TextFontError",
    "TextSlot",
    "UnsupportedVersionError",
    "__version__",
]


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'cartostack' has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
