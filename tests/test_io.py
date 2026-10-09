"""Reading and writing .cstack files: round trips, stability, atomicity, and errors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from helpers import build_archive, full_scene, gradient, png_bytes, rewrite, unpack

from cartostack import FormatError, GridSlot, PolygonSlot, Scene, TextSlot, UnsupportedVersionError
from cartostack import io as cio

ENCODINGS = ["rgba8+zlib", "rgba8", "png"]


def assert_same_scene(a: Scene, b: Scene) -> None:
    assert a.geometry == b.geometry
    assert a.geometry.fingerprint() == b.geometry.fingerprint()
    assert [layer.id for layer in a.draw_order()] == [layer.id for layer in b.draw_order()]
    assert dict(a.assets) == dict(b.assets)
    assert dict(a.provenance) == dict(b.provenance)
    for la, lb in zip(a.layers, b.layers, strict=True):
        assert type(la) is type(lb)
        for field in ("id", "order", "left", "top", "visible", "opacity"):
            assert getattr(la, field) == getattr(lb, field), (la.id, field)
        assert la.size(a.shape) == lb.size(b.shape)
        if la.pixels is None:
            assert lb.pixels is None
        else:
            assert lb.pixels is not None
            np.testing.assert_array_equal(la.pixels, lb.pixels)
        if isinstance(la, GridSlot):
            assert isinstance(lb, GridSlot)
            np.testing.assert_array_equal(la.index_map, lb.index_map)
            np.testing.assert_array_equal(la.lut, lb.lut)
            assert (la.shape, la.vmin, la.vmax, la.alpha, la.extend) == (
                lb.shape,
                lb.vmin,
                lb.vmax,
                lb.alpha,
                lb.extend,
            )
        if isinstance(la, PolygonSlot):
            assert isinstance(lb, PolygonSlot)
            assert (la.bins, la.fallback, la.supersample, la.round_decimals) == (
                lb.bins,
                lb.fallback,
                lb.supersample,
                lb.round_decimals,
            )
        if isinstance(la, TextSlot):
            assert isinstance(lb, TextSlot)
            for field in (
                "value",
                "font",
                "size_px",
                "color",
                "x",
                "y",
                "ha",
                "va",
                "snap",
                "offset",
            ):
                assert getattr(la, field) == getattr(lb, field), (la.id, field)


# --- Round trips -------------------------------------------------------------------------


@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize("index_encoding", ["i32le+zlib", "i32le"])
def test_round_trip_every_kind_and_encoding(
    tmp_path: Path, encoding: str, index_encoding: str
) -> None:
    scene = full_scene(encoding, index_encoding)
    path = tmp_path / "scene.cstack"
    scene.save(path)
    assert_same_scene(scene, Scene.load(path))
    manifest, members = unpack(path)
    ext = {"rgba8+zlib": ".rgba8.zz", "rgba8": ".rgba8", "png": ".png"}[encoding]
    assert f"layers/below{ext}" in members
    assert manifest["layers"][1]["grid"]["index_map"]["encoding"] == index_encoding


def test_save_load_save_is_byte_stable(tmp_path: Path) -> None:
    first, second, third = (tmp_path / f"{n}.cstack" for n in ("a", "b", "c"))
    scene = full_scene("png")
    scene.save(first)
    scene.save(third)  # same in-memory scene twice
    Scene.load(first).save(second)
    assert first.read_bytes() == second.read_bytes() == third.read_bytes()
    with zipfile.ZipFile(first) as zf:
        assert zf.namelist()[:2] == ["mimetype", "manifest.json"]
        assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
        assert zf.read("mimetype") == b"application/x-cartostack"


def test_reencoding_on_save_keeps_pixels(tmp_path: Path) -> None:
    src, dst = tmp_path / "src.cstack", tmp_path / "dst.cstack"
    full_scene("rgba8").save(src)
    Scene.load(src).save(dst, encoding="png")
    manifest, members = unpack(dst)
    assert {r["encoding"] for r in manifest["layers"] if "encoding" in r} == {"png"}
    assert "layers/below.png" in members
    assert_same_scene(Scene.load(src), Scene.load(dst))


def test_unchanged_members_are_written_back_verbatim(tmp_path: Path) -> None:
    path, out = tmp_path / "in.cstack", tmp_path / "out.cstack"
    full_scene("png").save(path)

    # Replace the PNG member with an equivalent but differently compressed PNG.
    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        members["layers/below.png"] = png_bytes(gradient(184, 221), "RGBA")

    rewrite(path, edit)
    Scene.load(path).save(out)
    assert unpack(out)[1]["layers/below.png"] == unpack(path)[1]["layers/below.png"]


def test_unknown_fields_are_preserved(tmp_path: Path) -> None:
    path, out = tmp_path / "in.cstack", tmp_path / "out.cstack"
    full_scene().save(path)

    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        manifest["format_version"] = "1.3"
        manifest["x_top"] = {"a": 1}
        manifest["geometry"]["x_geometry"] = [1, 2]
        manifest["layers"][0]["x_layer"] = True
        manifest["layers"][1]["grid"]["x_section"] = "keep"

    rewrite(path, edit)
    Scene.load(path).save(out)
    manifest, _ = unpack(out)
    assert manifest["x_top"] == {"a": 1}
    assert manifest["geometry"]["x_geometry"] == [1, 2]
    assert manifest["layers"][0]["x_layer"] is True
    assert manifest["layers"][1]["grid"]["x_section"] == "keep"
    assert manifest["format_version"] == "1.0"  # written by a 1.0 writer


def test_assets_and_provenance_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "s.cstack"
    full_scene().save(path)
    loaded = Scene.load(path)
    assert loaded.assets["notes/readme.txt"] == b"kept"
    assert loaded.provenance["created_utc"] == "2026-10-08T00:00:00Z"


# --- Atomic writes ------------------------------------------------------------------------


def test_failed_save_leaves_existing_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "template.cstack"
    full_scene().save(path)
    before = path.read_bytes()
    calls = {"n": 0}
    real = cio.encode_rgba

    def flaky(pixels: np.ndarray, encoding: str) -> bytes:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full (simulated)")
        return real(pixels, encoding)

    monkeypatch.setattr(cio, "encode_rgba", flaky)
    with pytest.raises(OSError, match="disk full"):
        full_scene("rgba8").save(path)  # different encoding: every layer is re-encoded
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["template.cstack"]


def test_failed_rename_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "t.cstack"

    def boom(src: str, dst: object) -> None:
        raise OSError("rename failed (simulated)")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="rename failed"):
        full_scene().save(path)
    assert list(tmp_path.iterdir()) == []


# --- Errors ---------------------------------------------------------------------------------


@pytest.fixture
def saved(tmp_path: Path) -> Path:
    path = tmp_path / "s.cstack"
    full_scene().save(path)
    return path


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Scene.load(tmp_path / "nope.cstack")


def test_not_a_zip(tmp_path: Path) -> None:
    path = tmp_path / "x.cstack"
    path.write_bytes(b"PNG? no")
    with pytest.raises(FormatError, match="not a ZIP archive"):
        Scene.load(path)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mimetype": None}, "first member must be 'mimetype'"),
        ({"mimetype_first": False}, "first member must be 'mimetype'"),
        ({"mimetype": b"application/zip"}, "mimetype is not"),
    ],
)
def test_mimetype_is_required(saved: Path, kwargs: dict[str, Any], message: str) -> None:
    rewrite(saved, lambda m, mem: None, **kwargs)
    with pytest.raises(FormatError, match=message):
        Scene.load(saved)


def test_manifest_must_be_json(saved: Path, tmp_path: Path) -> None:
    _, members = unpack(saved)
    build_archive(saved, b"{not json", members)
    with pytest.raises(FormatError, match="not valid UTF-8 JSON"):
        Scene.load(saved)


def test_unsupported_major_version_is_reported_before_anything_else(tmp_path: Path) -> None:
    path = tmp_path / "future.cstack"
    build_archive(
        path,
        {"format": "cartostack", "format_version": "2.0", "layers": "??"},
        {"weird/member.bin": b"\x00"},
        restamp=False,
    )
    with pytest.raises(UnsupportedVersionError, match=r"2\.0 is not supported"):
        Scene.load(path)


def test_member_missing_from_archive(saved: Path) -> None:
    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        del members["layers/above.rgba8.zz"]

    rewrite(saved, edit, restamp=False)
    with pytest.raises(FormatError, match="listed in members but missing from the archive"):
        Scene.load(saved)


def test_unlisted_archive_entry(saved: Path) -> None:
    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        members["stray.bin"] = b"?"

    rewrite(saved, edit, restamp=False)
    with pytest.raises(FormatError, match=r"'stray\.bin' is not listed in members"):
        Scene.load(saved)


def test_checksum_mismatch(saved: Path) -> None:
    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        data = bytearray(members["layers/below.rgba8.zz"])
        data[len(data) // 2] ^= 0xFF
        members["layers/below.rgba8.zz"] = bytes(data)

    rewrite(saved, edit, restamp=False)
    with pytest.raises(FormatError, match="SHA-256 mismatch") as exc:
        Scene.load(saved)
    assert "layers/below.rgba8.zz" in str(exc.value)


def test_size_mismatch(saved: Path) -> None:
    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        members["fonts/Test.ttf"] += b"!"

    rewrite(saved, edit, restamp=False)
    with pytest.raises(FormatError, match="truncated or modified"):
        Scene.load(saved)


def test_compressed_members_are_rejected(saved: Path) -> None:
    rewrite(saved, lambda m, mem: None, compress=frozenset({"fonts/Test.ttf"}))
    with pytest.raises(FormatError, match="compressed in the ZIP"):
        Scene.load(saved)


def test_corrupt_zlib_stream(saved: Path) -> None:
    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        members["layers/below.rgba8.zz"] = b"definitely not zlib"

    rewrite(saved, edit)  # restamped: the checksum matches the corrupt data
    with pytest.raises(FormatError, match="corrupt zlib stream"):
        Scene.load(saved)


def test_decoded_size_must_match_record(saved: Path) -> None:
    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        manifest["layers"][3]["width"] = 59

    rewrite(saved, edit)
    with pytest.raises(FormatError, match="expected"):
        Scene.load(saved)


def test_png_must_be_rgba(tmp_path: Path) -> None:
    path = tmp_path / "p.cstack"
    full_scene("png").save(path)

    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        members["layers/below.png"] = png_bytes(gradient(184, 221), "RGB")

    rewrite(path, edit)
    with pytest.raises(FormatError, match="must be 8-bit RGBA"):
        Scene.load(path)


def test_index_map_values_out_of_range(saved: Path) -> None:
    import zlib

    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        idx = np.full((184, 221), 10_000, "<i4")
        members["slots/temperature/index.i32.zz"] = zlib.compress(idx.tobytes())

    rewrite(saved, edit)
    with pytest.raises(FormatError, match=r"layer 'temperature'.*cell index"):
        Scene.load(saved)


def test_invalid_manifest_lists_problems_with_the_path(saved: Path) -> None:
    def edit(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
        manifest["layers"][2]["id"] = "below"
        manifest["layers"][0]["opacity"] = 3

    rewrite(saved, edit)
    with pytest.raises(FormatError) as exc:
        Scene.load(saved)
    assert len(exc.value.problems) == 2
    assert all(str(saved) in p for p in exc.value.problems)


# --- Dependency boundary -----------------------------------------------------------------


def test_load_and_save_use_only_core_dependencies(tmp_path: Path) -> None:
    src = tmp_path / "s.cstack"
    full_scene("png").save(src)
    code = (
        "import json, sys; from cartostack import Scene;"
        f"Scene.load({str(src)!r}).save({str(tmp_path / 'o.cstack')!r});"
        "print(json.dumps(sorted(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    loaded = {name.split(".")[0] for name in json.loads(out)}
    assert loaded.isdisjoint({"matplotlib", "cartopy", "pyproj", "shapely"})
    assert {"numpy", "PIL"} <= loaded
    assert (tmp_path / "o.cstack").read_bytes() == src.read_bytes()
