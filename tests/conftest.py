"""Shared test configuration.

* Network access is blocked for every test: fixtures come only from the prepared
  fixture directory (``scripts/fetch_fixtures.py``), never from downloads.
* ``@pytest.mark.build`` tests are skipped unless the ``build`` extra is installed.
* ``@pytest.mark.fixtures`` tests are skipped unless the prepared fixtures verify.
"""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
HAS_BUILD_EXTRA = all(
    importlib.util.find_spec(name) is not None for name in ("matplotlib", "cartopy")
)


def _load_fixture_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "fetch_fixtures", ROOT / "scripts" / "fetch_fixtures.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURES = _load_fixture_script()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    fixture_problems: list[str] | None = None
    for item in items:
        if "build" in item.keywords and not HAS_BUILD_EXTRA:
            item.add_marker(pytest.mark.skip(reason="needs the `build` extra"))
        if "fixtures" in item.keywords:
            if fixture_problems is None:
                fixture_problems = FIXTURES.verify()
            if fixture_problems:
                reason = f"fixtures unavailable ({fixture_problems[0]})"
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("network access is disabled in tests; prepare fixtures first")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    """The verified fixture directory (tests using it must be marked ``fixtures``)."""
    return Path(FIXTURES.fixture_dir())
