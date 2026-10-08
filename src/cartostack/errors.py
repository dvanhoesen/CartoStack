"""Exception hierarchy."""

from __future__ import annotations

from collections.abc import Iterable


class CartoStackError(Exception):
    """Base class for CartoStack errors."""


class FormatError(CartoStackError, ValueError):
    """A manifest or file violates the ``.cstack`` format; ``problems`` lists every violation."""

    def __init__(self, problems: Iterable[str] | str) -> None:
        self.problems: tuple[str, ...] = (
            (problems,) if isinstance(problems, str) else tuple(problems)
        )
        lines = "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(f"invalid .cstack manifest ({len(self.problems)} problem(s)):\n{lines}")


class GeometryError(FormatError):
    """Canvas geometry or georeferencing is invalid or inconsistent."""


class UnsupportedVersionError(FormatError):
    """The file's format major version is not supported by this reader."""
