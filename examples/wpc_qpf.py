"""Read a WPC QPF product (ESRI shapefile) with the standard library and NumPy.

The runtime takes polygons as ``(lon, lat)`` rings and one value per polygon; where they
come from is up to the caller. This reader covers what WPC's QPF shapefiles use: polygon
records (type 5) in lon/lat degrees, and a dBASE table with ``QPF`` (inches),
``ISSUE_TIME`` and ``VALID_TIME``. No GeoPandas, pyogrio or shapely.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

EASTERN = ZoneInfo("America/New_York")


@dataclass
class Product:
    rings: list[list[np.ndarray]]  # per polygon: its rings, (n, 2) lon/lat
    values: list[float]  # per polygon: QPF in inches
    subtitle: str  # "valid start - valid end (Issued: …)" in US Eastern time


def read_polygons(shp: Path) -> list[list[np.ndarray]]:
    """Rings per record of a polygon shapefile; non-polygon records give no rings."""
    buf = shp.read_bytes()
    pos, records = 100, list[list[np.ndarray]]()
    while pos < len(buf):
        _, length = struct.unpack(">ii", buf[pos : pos + 8])
        content = buf[pos + 8 : pos + 8 + 2 * length]
        pos += 8 + 2 * length
        (shape_type,) = struct.unpack("<i", content[:4])
        if shape_type != 5:
            records.append([])
            continue
        n_parts, n_points = struct.unpack("<ii", content[36:44])
        parts = [*np.frombuffer(content, "<i4", n_parts, 44).tolist(), n_points]
        points = np.frombuffer(content, "<f8", 2 * n_points, 44 + 4 * n_parts).reshape(-1, 2)
        records.append([points[parts[i] : parts[i + 1]] for i in range(n_parts)])
    return records


def read_table(dbf: Path) -> list[dict[str, float | str]]:
    """The rows of a dBASE III table (numbers as float, everything else as str)."""
    buf = dbf.read_bytes()
    n_records, header_len, record_len = struct.unpack("<IHH", buf[4:12])
    fields, pos = [], 32
    while buf[pos] != 0x0D:
        name = buf[pos : pos + 11].split(b"\0")[0].decode()
        fields.append((name, chr(buf[pos + 11]), buf[pos + 16]))
        pos += 32
    rows = []
    for i in range(n_records):
        rec = buf[header_len + i * record_len : header_len + (i + 1) * record_len]
        off, row = 1, dict[str, float | str]()
        for name, ftype, size in fields:
            raw = rec[off : off + size].decode("latin-1").strip()
            row[name] = float(raw) if ftype in "NF" and raw else raw
            off += size
        rows.append(row)
    return rows


def _valid(side: str) -> datetime:
    hour, date = side.strip().split()  # e.g. "12Z 10/09/26"
    dt = datetime.strptime(date, "%m/%d/%y").replace(hour=int(hour[:-1]), tzinfo=UTC)
    return dt.astimezone(EASTERN)


def read_product(directory: Path) -> Product:
    """The product in ``directory`` (its one ``.shp`` with the matching ``.dbf``)."""
    shp = min(directory.glob("*.shp"))
    rows = read_table(shp.with_suffix(".dbf"))
    issued = datetime.strptime(str(rows[0]["ISSUE_TIME"]), "%Y-%m-%d %H:%M:%S")
    start, end = str(rows[0]["VALID_TIME"]).split(" - ")
    fmt = "%-I:%M %p %b %-d"
    subtitle = (
        f"{_valid(start).strftime(fmt)} - {_valid(end).strftime(fmt)} "
        f"(Issued: {issued.replace(tzinfo=UTC).astimezone(EASTERN).strftime(fmt)})"
    )
    return Product(read_polygons(shp), [float(r["QPF"]) for r in rows], subtitle)
