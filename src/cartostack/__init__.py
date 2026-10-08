"""CartoStack: layered map files (``.cstack``) and a NumPy/Pillow-only runtime.

The runtime imports only NumPy and Pillow. Authoring files from Matplotlib and
Cartopy lives behind the ``build`` extra (``cartostack.build``) and is never
imported by ``import cartostack``.
"""

__version__ = "0.0.1.dev0"

__all__ = ["__version__"]
