"""Authoring ``.cstack`` files from Matplotlib/Cartopy figures (the ``build`` extra).

Requires ``pip install cartostack[build]`` (Matplotlib ≥ 3.11, Cartopy). Nothing here is
imported by ``import cartostack``; the runtime never needs Matplotlib or Cartopy.

    from cartostack.build import SceneBuilder
"""

from __future__ import annotations

from .builder import BuildError, SceneBuilder
from .georef import canvas_geometry, georeference
from .index_map import index_map

__all__ = ["BuildError", "SceneBuilder", "canvas_geometry", "georeference", "index_map"]
