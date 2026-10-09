"""Archive lifetime and save-time encoding (Session 12)."""

from __future__ import annotations

import gc
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from helpers import full_scene, gradient

from cartostack import Scene
from cartostack import io as cio
from cartostack import scene as cscene


def open_fds() -> int:
    for d in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(d):
            return len(os.listdir(d))
    pytest.skip("cannot count open file descriptors here")


@pytest.fixture
def template(tmp_path: Path) -> Path:
    path = tmp_path / "template.cstack"
    full_scene().save(path)
    return path


def test_load_keeps_no_file_open(template: Path) -> None:
    before = open_fds()
    scenes = [Scene.load(template) for _ in range(5)]
    assert open_fds() == before
    assert len(scenes) == 5


def test_scene_outlives_its_file_and_close(template: Path, tmp_path: Path) -> None:
    with Scene.load(template) as scene:
        image = scene.render()
    scene.close()
    template.unlink()
    np.testing.assert_array_equal(scene.render(), image)  # still usable: plain memory
    scene.replace_layer("above", gradient(40, 60, 9))
    out = tmp_path / "after.cstack"
    scene.save(out)
    np.testing.assert_array_equal(Scene.load(out).render(), scene.render())


def test_saving_over_the_source_file(template: Path) -> None:
    scene = Scene.load(template)
    scene.replace_layer("above", gradient(40, 60, 7))
    scene.save(template)  # atomic replace of the file it came from
    np.testing.assert_array_equal(Scene.load(template).render(), scene.render())


def spy(monkeypatch: pytest.MonkeyPatch, name: str) -> list[Any]:
    calls: list[Any] = []
    original: Callable[..., bytes] = getattr(cio, name)

    def wrapper(*args: Any, **kwargs: Any) -> bytes:
        calls.append(args[0].shape)
        return original(*args, **kwargs)

    monkeypatch.setattr(cio, name, wrapper)
    return calls


def test_repeated_saves_encode_only_new_buffers(
    template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene.load(template)
    rgba = spy(monkeypatch, "encode_rgba")
    index = spy(monkeypatch, "encode_index")
    scene.save(tmp_path / "a.cstack")
    assert (rgba, index) == ([], [])  # everything reuses the bytes it was loaded from
    scene.replace_layer("above", gradient(40, 60, 3))
    scene.save(tmp_path / "b.cstack")
    assert rgba == [(40, 60, 4)]
    rgba.clear()
    scene.save(tmp_path / "c.cstack")
    scene.save(tmp_path / "d.cstack")
    assert rgba == []  # the edited layer's bytes are reused from the first save
    assert (tmp_path / "c.cstack").read_bytes() == (tmp_path / "b.cstack").read_bytes()
    lut = np.array(scene["temperature"].lut)
    lut[:, 0] = 255 - lut[:, 0]
    scene.replace_grid("temperature", np.zeros(scene["temperature"].shape, np.float32), lut=lut)
    scene.save(tmp_path / "e.cstack")
    assert sorted(rgba) == sorted([(1, lut.shape[0], 4), scene["temperature"].pixels.shape])
    assert index == []  # the index map is unchanged and still shared
    rgba.clear()
    scene.save(tmp_path / "f.cstack")
    assert rgba == []


def test_a_new_encoding_is_encoded_once(
    template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene.load(template)
    rgba = spy(monkeypatch, "encode_rgba")
    scene.save(tmp_path / "png1.cstack", encoding="png")
    n = len(rgba)
    assert n > 0
    scene.save(tmp_path / "png2.cstack", encoding="png")
    assert len(rgba) == n
    assert (tmp_path / "png1.cstack").read_bytes() == (tmp_path / "png2.cstack").read_bytes()


def test_cache_entries_go_away_with_their_buffers(tmp_path: Path) -> None:
    s = full_scene()
    s.replace_layer("above", gradient(40, 60, 11))
    s.save(tmp_path / "x.cstack")
    buffer = s["above"].pixels
    assert buffer is not None
    key = id(buffer)
    assert key in cscene._ENCODE_CACHE
    del s, buffer
    gc.collect()
    assert key not in cscene._ENCODE_CACHE
