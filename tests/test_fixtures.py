"""Shared fixture directory: offline verification only (no downloads in tests)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXTURES


def test_verify_reports_missing_manifest(tmp_path: Path) -> None:
    problems = FIXTURES.verify(tmp_path)
    assert len(problems) == 1
    assert "fixtures.json missing" in problems[0]


def test_verify_detects_changed_and_missing_files(tmp_path: Path) -> None:
    good, changed = tmp_path / "a.bin", tmp_path / "b.bin"
    good.write_bytes(b"a")
    changed.write_bytes(b"b")
    manifest = {
        "files": [
            {"path": "a.bin", "sha256": FIXTURES.sha256(good)},
            {"path": "b.bin", "sha256": "0" * 64},
            {"path": "c.bin", "sha256": "0" * 64},
        ]
    }
    (tmp_path / "fixtures.json").write_text(json.dumps(manifest))
    problems = FIXTURES.verify(tmp_path)
    assert problems == [f"changed: {changed}", f"missing: {tmp_path / 'c.bin'}"]


@pytest.mark.fixtures
def test_prepared_fixtures_include_reference_sources(fixture_dir: Path) -> None:
    assert (fixture_dir / "NYS_Shoreline_Counties" / "NYS Counties.shp").exists()
    assert any((fixture_dir / "qpf").glob("day_*/*.shp"))
