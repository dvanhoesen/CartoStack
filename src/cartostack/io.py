"""Reading and writing the ``.cstack`` container (``docs/format.md`` §2, §6.3).

Archives are written deterministically (fixed member order, timestamps, and
attributes) and atomically (temporary file in the target directory, fsync,
rename), so saving the same scene twice gives identical bytes and a failed save
never touches an existing file.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import zipfile
import zlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Final

import numpy as np
from numpy.typing import NDArray

from .errors import FormatError, UnsupportedVersionError
from .manifest import FORMAT_VERSION, MANIFEST_NAME, MIMETYPE, MIMETYPE_NAME, parse_version

ZIP_DATE_TIME: Final = (1980, 1, 1, 0, 0, 0)
ZLIB_LEVEL: Final = 6
PNG_COMPRESS_LEVEL: Final = 6

PathLike = str | os.PathLike[str]


# --- Encodings -----------------------------------------------------------------------


def encode_rgba(pixels: NDArray[np.uint8], encoding: str) -> bytes:
    if encoding == "rgba8":
        return pixels.tobytes()
    if encoding == "rgba8+zlib":
        return zlib.compress(pixels.tobytes(), ZLIB_LEVEL)
    if encoding == "png":
        from PIL import Image

        buf = io.BytesIO()
        Image.fromarray(pixels, "RGBA").save(buf, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
        return buf.getvalue()
    raise FormatError(f"unknown RGBA encoding {encoding!r}")


def decode_rgba(
    data: bytes, encoding: str, height: int, width: int, what: str
) -> NDArray[np.uint8]:
    """Decode to a read-only ``(height, width, 4)`` uint8 array; corrupt data raises FormatError."""
    if encoding == "png":
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(io.BytesIO(data)) as img:
                if img.mode != "RGBA":
                    raise FormatError(f"{what}: PNG must be 8-bit RGBA, got mode {img.mode}")
                if img.size != (width, height):
                    raise FormatError(
                        f"{what}: PNG is {img.size[0]}x{img.size[1]}, record says {width}x{height}"
                    )
                arr = np.asarray(img)
        except (OSError, UnidentifiedImageError, SyntaxError) as exc:
            raise FormatError(f"{what}: corrupt PNG ({exc})") from exc
        arr.flags.writeable = False
        return arr
    raw = _raw(data, encoding, "rgba8", what)
    return _frombuffer(raw, np.dtype(np.uint8), (height, width, 4), what)


def encode_index(index_map: NDArray[np.int32], encoding: str) -> bytes:
    raw = index_map.astype("<i4", copy=False).tobytes()
    if encoding == "i32le":
        return raw
    if encoding == "i32le+zlib":
        return zlib.compress(raw, ZLIB_LEVEL)
    raise FormatError(f"unknown index encoding {encoding!r}")


def decode_index(
    data: bytes, encoding: str, height: int, width: int, what: str
) -> NDArray[np.int32]:
    raw = _raw(data, encoding, "i32le", what)
    arr = _frombuffer(raw, np.dtype("<i4"), (height, width), what)
    return arr.astype(np.int32, copy=False)


def _raw(data: bytes, encoding: str, base: str, what: str) -> bytes:
    if encoding == base:
        return data
    if encoding == f"{base}+zlib":
        try:
            return zlib.decompress(data)
        except zlib.error as exc:
            raise FormatError(f"{what}: corrupt zlib stream ({exc})") from exc
    raise FormatError(f"{what}: unknown encoding {encoding!r}")


def _frombuffer(
    raw: bytes, dtype: np.dtype[Any], shape: tuple[int, ...], what: str
) -> NDArray[Any]:
    expected = int(np.prod(shape)) * dtype.itemsize
    if len(raw) != expected:
        raise FormatError(
            f"{what}: decoded {len(raw)} bytes, expected {expected} for shape {shape} {dtype}"
        )
    return np.frombuffer(raw, dtype).reshape(shape)  # read-only view of immutable bytes


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Archive -------------------------------------------------------------------------


def dumps_manifest(manifest: Mapping[str, Any]) -> bytes:
    """Deterministic manifest bytes: UTF-8 JSON, 2-space indent, trailing newline."""
    text = json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def read_archive(path: PathLike) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Read and check the container; return (decoded manifest JSON, verified member bytes).

    Checks the ``mimetype`` member, ``ZIP_STORED`` storage, that every archive
    entry is listed in ``members`` and vice versa, and every member's size and
    SHA-256. The manifest's contents are validated by the caller.
    """
    if not Path(path).is_file():
        raise FileNotFoundError(f"{path}: no such file")
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FormatError(f"{path}: not a ZIP archive ({exc})") from exc
    with zf:
        infos = zf.infolist()
        if not infos or infos[0].filename != MIMETYPE_NAME:
            raise FormatError(f"{path}: not a .cstack file (first member must be 'mimetype')")
        if zf.read(MIMETYPE_NAME) != MIMETYPE.encode("ascii"):
            raise FormatError(f"{path}: not a .cstack file (mimetype is not {MIMETYPE!r})")
        names = [i.filename for i in infos]
        if MANIFEST_NAME not in names:
            raise FormatError(f"{path}: manifest.json is missing")
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FormatError(f"{path}: manifest.json is not valid UTF-8 JSON ({exc})") from exc
        if not isinstance(manifest, dict):
            raise FormatError(f"{path}: manifest.json must contain a JSON object")
        # The major version is checked before members are trusted or decoded.
        version = parse_version(manifest.get("format_version"))
        if version[0] != FORMAT_VERSION[0]:
            raise UnsupportedVersionError(
                f"{path}: format_version {version[0]}.{version[1]} is not supported; this "
                f"reader implements {FORMAT_VERSION[0]}.x"
            )
        table = manifest.get("members")
        if not isinstance(table, dict):
            raise FormatError(f"{path}: manifest.json has no members table")
        problems: list[str] = []
        if len(set(names)) != len(names):
            problems.append("archive contains duplicate entries")
        members: dict[str, bytes] = {}
        for info in infos:
            name = info.filename
            if name in (MIMETYPE_NAME, MANIFEST_NAME):
                continue
            if info.compress_type != zipfile.ZIP_STORED:
                problems.append(f"member {name!r} is compressed in the ZIP; members must be stored")
                continue
            if name not in table:
                problems.append(f"archive entry {name!r} is not listed in members")
                continue
            data = zf.read(name)
            rec = table[name]
            if not isinstance(rec, dict):
                continue  # reported by manifest validation
            if rec.get("bytes") != len(data):
                problems.append(
                    f"member {name!r}: size {len(data)} bytes, manifest says {rec.get('bytes')} "
                    "(file truncated or modified)"
                )
            elif rec.get("sha256") != sha256(data):
                problems.append(
                    f"member {name!r}: SHA-256 mismatch (file corrupt or modified after writing)"
                )
            else:
                members[name] = data
        for name in table:
            if name not in names:
                problems.append(
                    f"member {name!r} is listed in members but missing from the archive"
                )
        if problems:
            raise FormatError([f"{path}: {p}" for p in problems])
    return manifest, members


@contextmanager
def atomic_file(path: PathLike) -> Iterator[BinaryIO]:
    """Write ``path`` via a temporary file in its directory, fsync, then rename over it.

    If the body raises, the temporary file is removed and ``path`` is untouched.
    """
    target = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            yield fh
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_archive(path: PathLike, manifest_bytes: bytes, members: Mapping[str, bytes]) -> None:
    """Atomically write mimetype, manifest.json, then members in sorted name order."""
    with (
        atomic_file(path) as fh,
        zipfile.ZipFile(fh, "w", compression=zipfile.ZIP_STORED) as zf,
    ):
        _put(zf, MIMETYPE_NAME, MIMETYPE.encode("ascii"))
        _put(zf, MANIFEST_NAME, manifest_bytes)
        for name in sorted(members):
            _put(zf, name, members[name])


def _put(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3  # Unix, so the bytes do not depend on the writing platform
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)
