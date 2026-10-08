"""Field readers that collect every problem instead of stopping at the first.

Plain Python only: manifest validation must run without NumPy or any extra.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Final


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _Missing()


class Problems:
    """Accumulates ``"<path>: <message>"`` strings."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, path: str, message: str) -> None:
        self.items.append(f"{path}: {message}" if path else message)

    def __bool__(self) -> bool:
        return bool(self.items)


def join(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else key


def is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def obj(p: Problems, path: str, value: object) -> Mapping[str, Any] | None:
    if value is MISSING:  # already reported by get()
        return None
    if isinstance(value, Mapping):
        return value
    p.add(path, f"must be an object, got {type(value).__name__}")
    return None


def get(
    p: Problems, mapping: Mapping[str, Any], key: str, path: str, default: object = MISSING
) -> Any:
    if key in mapping:
        return mapping[key]
    if default is MISSING:
        p.add(join(path, key), "is required")
    return default


def number(
    p: Problems,
    path: str,
    value: object,
    *,
    lo: float | None = None,
    hi: float | None = None,
    lo_open: bool = False,
) -> float | None:
    if value is MISSING:  # already reported by get()
        return None
    if not is_number(value):
        p.add(path, f"must be a finite number, got {value!r}")
        return None
    v = float(value)  # type: ignore[arg-type]
    if lo is not None and (v < lo or (lo_open and v == lo)):
        p.add(path, f"must be {'>' if lo_open else '>='} {lo:g}, got {v:g}")
        return None
    if hi is not None and v > hi:
        p.add(path, f"must be <= {hi:g}, got {v:g}")
        return None
    return v


def integer(
    p: Problems, path: str, value: object, *, lo: int | None = None, hi: int | None = None
) -> int | None:
    if value is MISSING:  # already reported by get()
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        p.add(path, f"must be an integer, got {value!r}")
        return None
    if lo is not None and value < lo:
        p.add(path, f"must be >= {lo}, got {value}")
        return None
    if hi is not None and value > hi:
        p.add(path, f"must be <= {hi}, got {value}")
        return None
    return value


def string(
    p: Problems,
    path: str,
    value: object,
    *,
    choices: tuple[str, ...] | None = None,
    pattern: re.Pattern[str] | None = None,
) -> str | None:
    if value is MISSING:  # already reported by get()
        return None
    if not isinstance(value, str):
        p.add(path, f"must be a string, got {value!r}")
        return None
    if choices is not None and value not in choices:
        p.add(path, f"must be one of {', '.join(choices)}; got {value!r}")
        return None
    if pattern is not None and not pattern.fullmatch(value):
        p.add(path, f"{value!r} does not match {pattern.pattern}")
        return None
    return value


def boolean(p: Problems, path: str, value: object) -> bool | None:
    if value is MISSING:  # already reported by get()
        return None
    if not isinstance(value, bool):
        p.add(path, f"must be true or false, got {value!r}")
        return None
    return value


def array(p: Problems, path: str, value: object, *, length: int | None = None) -> list[Any] | None:
    if value is MISSING:  # already reported by get()
        return None
    if not isinstance(value, list):
        p.add(path, f"must be an array, got {type(value).__name__}")
        return None
    if length is not None and len(value) != length:
        p.add(path, f"must have {length} items, got {len(value)}")
        return None
    return value


def numbers(p: Problems, path: str, value: object, length: int) -> tuple[float, ...] | None:
    if value is MISSING:  # already reported by get()
        return None
    items = array(p, path, value, length=length)
    if items is None:
        return None
    out = [number(p, join(path, i), v) for i, v in enumerate(items)]
    return None if any(v is None for v in out) else tuple(v for v in out if v is not None)


def color(p: Problems, path: str, value: object) -> tuple[int, int, int, int] | None:
    """An 8-bit straight-alpha RGBA colour ``[r, g, b, a]``."""
    if value is MISSING:  # already reported by get()
        return None
    items = array(p, path, value, length=4)
    if items is None:
        return None
    out = [integer(p, join(path, i), v, lo=0, hi=255) for i, v in enumerate(items)]
    if any(v is None for v in out):
        return None
    r, g, b, a = (v for v in out if v is not None)
    return (r, g, b, a)
