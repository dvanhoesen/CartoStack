"""Just enough OpenType reading for Matplotlib-compatible text layout (standard library only).

Reads, from a TrueType/OpenType font's bytes:

* ``head.unitsPerEm``, the OS/2 typographic ascender/descender (falling back to
  ``hhea``), which Matplotlib 3.11 uses as each line's minimum ascent and descent;
* the character map (``cmap`` formats 4 and 12);
* ligatures: GSUB ``rlig``, ``liga`` and ``clig`` ligature substitutions (also through
  extension lookups), kept only when the ligature glyph has a Unicode code point (e.g.
  U+FB01 "ﬁ"), because Pillow can render characters but not glyph ids;
* pair kerning: GPOS ``kern`` feature lookups (pair adjustment formats 1 and 2, also
  through extension lookups), else a legacy ``kern`` table (format 0).

HarfBuzz, which Matplotlib 3.11 uses to lay out text, applies the same substitutions and
adjustments. Other shaping (contextual alternates, mark positioning) is not implemented. Malformed
tables raise ``FontTableError``; callers treat that as an unusable font.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


class FontTableError(ValueError):
    """The font's tables cannot be read."""


def _u16(b: bytes, o: int) -> int:
    return int(struct.unpack_from(">H", b, o)[0])


def _i16(b: bytes, o: int) -> int:
    return int(struct.unpack_from(">h", b, o)[0])


def _u32(b: bytes, o: int) -> int:
    return int(struct.unpack_from(">I", b, o)[0])


# Value record fields, in order: XPlacement, YPlacement, XAdvance, YAdvance, then device offsets.
_X_PLACEMENT, _X_ADVANCE = 0x1, 0x4


@dataclass(frozen=True)
class PairAdjustment:
    """Design-unit adjustments for a glyph pair (first's advance; second's placement, advance)."""

    first_advance: int = 0
    second_placement: int = 0
    second_advance: int = 0

    def __add__(self, other: PairAdjustment) -> PairAdjustment:
        return PairAdjustment(
            self.first_advance + other.first_advance,
            self.second_placement + other.second_placement,
            self.second_advance + other.second_advance,
        )

    def __bool__(self) -> bool:
        return bool(self.first_advance or self.second_placement or self.second_advance)


NO_ADJUSTMENT = PairAdjustment()


@dataclass
class FontInfo:
    units_per_em: int
    typo_ascender: int
    typo_descender: int  # negative below the baseline, as stored
    cmap: dict[int, int]
    #: GSUB ligature lookups, each a list of ligature-substitution subtable offsets.
    ligature_lookups: list[list[int]] = field(default_factory=list, repr=False)
    #: GPOS ``kern`` lookups, each a list of pair-adjustment subtable offsets.
    pair_lookups: list[list[int]] = field(default_factory=list, repr=False)
    legacy_kern: dict[tuple[int, int], int] = field(default_factory=dict, repr=False)
    data: bytes = field(default=b"", repr=False)
    _cache: dict[tuple[int, int], PairAdjustment] = field(default_factory=dict, repr=False)
    _reverse: dict[int, int] | None = field(default=None, repr=False)

    def glyph(self, char: str) -> int:
        return self.cmap.get(ord(char), 0)

    def ligate(self, text: str) -> str:
        """``text`` with the font's ligatures as their presentation-form characters."""
        if not self.ligature_lookups or len(text) < 2:
            return text
        reverse = self._reverse_cmap()
        glyphs = [self.glyph(c) for c in text]
        chars = list(text)
        for subtables in self.ligature_lookups:
            i = 0
            while i < len(glyphs) - 1:
                for offset in subtables:
                    found = _ligature(self.data, offset, glyphs, i)
                    if found is not None:
                        lig, n = found
                        if lig in reverse:  # renderable: substitute
                            glyphs[i : i + n] = [lig]
                            chars[i : i + n] = [chr(reverse[lig])]
                        break
                i += 1
        return "".join(chars)

    def _reverse_cmap(self) -> dict[int, int]:
        if self._reverse is None:
            self._reverse = {}
            for cp, g in sorted(self.cmap.items(), reverse=True):  # lowest code point wins
                self._reverse[g] = cp
        return self._reverse

    def kerning(self, left: str, right: str) -> PairAdjustment:
        """Adjustment between two adjacent characters, in design units."""
        key = (self.glyph(left), self.glyph(right))
        found = self._cache.get(key)
        if found is None:
            found = self._lookup(*key)
            self._cache[key] = found
        return found

    def _lookup(self, g1: int, g2: int) -> PairAdjustment:
        if not self.pair_lookups:
            return PairAdjustment(first_advance=self.legacy_kern.get((g1, g2), 0))
        total = NO_ADJUSTMENT
        for subtables in self.pair_lookups:
            for offset in subtables:  # the first subtable covering the pair applies
                found = _pair_value(self.data, offset, g1, g2)
                if found is not None:
                    total = total + found
                    break
        return total


def _tables(data: bytes) -> dict[str, tuple[int, int]]:
    if len(data) < 12:
        raise FontTableError("not an OpenType font (too short)")
    base = 0
    if data[:4] == b"ttcf":
        base = _u32(data, 12)  # first font of a collection
    n = _u16(data, base + 4)
    out = {}
    for i in range(n):
        rec = base + 12 + 16 * i
        tag = data[rec : rec + 4].decode("latin-1")
        out[tag] = (_u32(data, rec + 8), _u32(data, rec + 12))
    return out


def _cmap(data: bytes, offset: int) -> dict[int, int]:
    n = _u16(data, offset + 2)
    best: tuple[int, int] | None = None
    for i in range(n):
        rec = offset + 4 + 8 * i
        platform, encoding, sub = _u16(data, rec), _u16(data, rec + 2), _u32(data, rec + 4)
        fmt = _u16(data, offset + sub)
        rank = {(3, 10): 0, (0, 4): 1, (3, 1): 2, (0, 3): 3}.get((platform, encoding), 9)
        if fmt in (4, 12) and (best is None or rank < best[0]):
            best = (rank, offset + sub)
    if best is None:
        raise FontTableError("no Unicode cmap (format 4 or 12)")
    sub = best[1]
    out: dict[int, int] = {}
    if _u16(data, sub) == 12:
        for i in range(_u32(data, sub + 12)):
            rec = sub + 16 + 12 * i
            start, end, gid = _u32(data, rec), _u32(data, rec + 4), _u32(data, rec + 8)
            for cp in range(start, end + 1):
                out[cp] = gid + cp - start
        return out
    segs = _u16(data, sub + 6) // 2
    ends = sub + 14
    starts = ends + 2 * segs + 2
    deltas = starts + 2 * segs
    ranges = deltas + 2 * segs
    for s in range(segs):
        end, start = _u16(data, ends + 2 * s), _u16(data, starts + 2 * s)
        delta, roff = _i16(data, deltas + 2 * s), _u16(data, ranges + 2 * s)
        for cp in range(start, end + 1):
            if cp == 0xFFFF:
                continue
            if roff == 0:
                gid = (cp + delta) & 0xFFFF
            else:
                gid = _u16(data, ranges + 2 * s + roff + 2 * (cp - start))
                gid = (gid + delta) & 0xFFFF if gid else 0
            if gid:
                out[cp] = gid
    return out


def _coverage(data: bytes, offset: int, glyph: int) -> int:
    """Coverage index of ``glyph``, or -1."""
    fmt, count = _u16(data, offset), _u16(data, offset + 2)
    if fmt == 1:
        lo, hi = 0, count - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            g = _u16(data, offset + 4 + 2 * mid)
            if g == glyph:
                return mid
            lo, hi = (mid + 1, hi) if g < glyph else (lo, mid - 1)
        return -1
    if fmt == 2:
        for i in range(count):
            rec = offset + 4 + 6 * i
            start, end = _u16(data, rec), _u16(data, rec + 2)
            if start <= glyph <= end:
                return _u16(data, rec + 4) + glyph - start
        return -1
    raise FontTableError(f"unknown coverage format {fmt}")


def _class(data: bytes, offset: int, glyph: int) -> int:
    fmt = _u16(data, offset)
    if fmt == 1:
        start, count = _u16(data, offset + 2), _u16(data, offset + 4)
        i = glyph - start
        return _u16(data, offset + 6 + 2 * i) if 0 <= i < count else 0
    if fmt == 2:
        for i in range(_u16(data, offset + 2)):
            rec = offset + 4 + 6 * i
            if _u16(data, rec) <= glyph <= _u16(data, rec + 2):
                return _u16(data, rec + 4)
        return 0
    raise FontTableError(f"unknown class definition format {fmt}")


def _value(data: bytes, offset: int, fmt: int) -> tuple[int, int]:
    """(XPlacement, XAdvance) of a value record; other fields are skipped."""
    x_placement = x_advance = 0
    pos = offset
    for bit in range(8):
        if fmt & (1 << bit):
            if 1 << bit == _X_PLACEMENT:
                x_placement = _i16(data, pos)
            elif 1 << bit == _X_ADVANCE:
                x_advance = _i16(data, pos)
            pos += 2
    return x_placement, x_advance


def _record_size(fmt: int) -> int:
    return 2 * bin(fmt & 0xFF).count("1")


def _pair_value(data: bytes, offset: int, g1: int, g2: int) -> PairAdjustment | None:
    fmt = _u16(data, offset)
    index = _coverage(data, offset + _u16(data, offset + 2), g1)
    if index < 0:
        return None
    vf1, vf2 = _u16(data, offset + 4), _u16(data, offset + 6)
    s1, s2 = _record_size(vf1), _record_size(vf2)
    if fmt == 1:
        if index >= _u16(data, offset + 8):
            return None
        pairset = offset + _u16(data, offset + 10 + 2 * index)
        count = _u16(data, pairset)
        size = 2 + s1 + s2
        lo, hi = 0, count - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            rec = pairset + 2 + size * mid
            g = _u16(data, rec)
            if g == g2:
                _, adv1 = _value(data, rec + 2, vf1)
                pl2, adv2 = _value(data, rec + 2 + s1, vf2)
                return PairAdjustment(adv1, pl2, adv2)
            lo, hi = (mid + 1, hi) if g < g2 else (lo, mid - 1)
        return None
    if fmt == 2:
        c1 = _class(data, offset + _u16(data, offset + 8), g1)
        c2 = _class(data, offset + _u16(data, offset + 10), g2)
        n1, n2 = _u16(data, offset + 12), _u16(data, offset + 14)
        if c1 >= n1 or c2 >= n2:
            return None
        rec = offset + 16 + (c1 * n2 + c2) * (s1 + s2)
        _, adv1 = _value(data, rec, vf1)
        pl2, adv2 = _value(data, rec + s1, vf2)
        return PairAdjustment(adv1, pl2, adv2)
    raise FontTableError(f"unknown pair adjustment format {fmt}")


def _feature_lookups(data: bytes, table: int, tags: tuple[bytes, ...]) -> list[int]:
    """Lookup indices of features ``tags`` for the DFLT/latn default language systems."""
    script_list = table + _u16(data, table + 4)
    feature_list = table + _u16(data, table + 6)
    features = []
    for i in range(_u16(data, script_list)):
        rec = script_list + 2 + 6 * i
        tag = data[rec : rec + 4]
        if tag not in (b"DFLT", b"latn"):
            continue
        script = script_list + _u16(data, rec + 4)
        default = _u16(data, script)
        if default:
            langsys = script + default
            features += [_u16(data, langsys + 6 + 2 * j) for j in range(_u16(data, langsys + 4))]
    lookups: list[int] = []
    for fi in sorted(set(features)):
        rec = feature_list + 2 + 6 * fi
        if data[rec : rec + 4] not in tags:
            continue
        feature = feature_list + _u16(data, rec + 4)
        for j in range(_u16(data, feature + 2)):
            idx = _u16(data, feature + 4 + 2 * j)
            if idx not in lookups:
                lookups.append(idx)
    return sorted(lookups)


def _lookups(
    data: bytes, table: int, tags: tuple[bytes, ...], kind: int, extension: int
) -> list[list[int]]:
    """Subtable offsets of lookup type ``kind`` (directly or via ``extension``) per lookup."""
    lookup_list = table + _u16(data, table + 8)
    out = []
    for idx in _feature_lookups(data, table, tags):
        lookup = lookup_list + _u16(data, lookup_list + 2 + 2 * idx)
        lookup_kind = _u16(data, lookup)
        subtables = []
        for j in range(_u16(data, lookup + 4)):
            sub = lookup + _u16(data, lookup + 6 + 2 * j)
            if lookup_kind == extension:  # the real subtable is elsewhere
                if _u16(data, sub + 2) != kind:
                    continue
                sub += _u32(data, sub + 4)
            elif lookup_kind != kind:
                continue
            subtables.append(sub)
        if subtables:
            out.append(subtables)
    return out


def _ligature(data: bytes, offset: int, glyphs: list[int], i: int) -> tuple[int, int] | None:
    """(ligature glyph, components consumed) for a ligature starting at ``glyphs[i]``."""
    if _u16(data, offset) != 1:
        return None
    index = _coverage(data, offset + _u16(data, offset + 2), glyphs[i])
    if index < 0 or index >= _u16(data, offset + 4):
        return None
    ligset = offset + _u16(data, offset + 6 + 2 * index)
    for k in range(_u16(data, ligset)):  # in font order: the first match wins
        lig = ligset + _u16(data, ligset + 2 + 2 * k)
        count = _u16(data, lig + 2)
        if i + count > len(glyphs):
            continue
        if all(glyphs[i + c] == _u16(data, lig + 4 + 2 * (c - 1)) for c in range(1, count)):
            return _u16(data, lig), count
    return None


def _legacy_kern(data: bytes, offset: int) -> dict[tuple[int, int], int]:
    out: dict[tuple[int, int], int] = {}
    if _u16(data, offset) != 0:
        return out
    pos = offset + 4
    for _ in range(_u16(data, offset + 2)):
        length, coverage = _u16(data, pos + 2), _u16(data, pos + 4)
        if coverage >> 8 == 0 and coverage & 1:  # format 0, horizontal
            n = _u16(data, pos + 6)
            for k in range(n):
                rec = pos + 14 + 6 * k
                out[(_u16(data, rec), _u16(data, rec + 2))] = _i16(data, rec + 4)
        pos += length
    return out


def read(data: bytes) -> FontInfo:
    """Parse the tables CartoStack needs; ``FontTableError`` if they are missing or broken."""
    try:
        tables = _tables(data)
        if "head" not in tables or "cmap" not in tables:
            raise FontTableError("missing head or cmap table")
        upem = _u16(data, tables["head"][0] + 18)
        if "OS/2" in tables and tables["OS/2"][1] >= 74:
            os2 = tables["OS/2"][0]
            ascender, descender = _i16(data, os2 + 68), _i16(data, os2 + 70)
        elif "hhea" in tables:
            hhea = tables["hhea"][0]
            ascender, descender = _i16(data, hhea + 4), _i16(data, hhea + 6)
        else:
            raise FontTableError("missing OS/2 and hhea tables")
        info = FontInfo(upem, ascender, descender, _cmap(data, tables["cmap"][0]), data=data)
        if "GSUB" in tables:
            info.ligature_lookups = _lookups(
                data, tables["GSUB"][0], (b"rlig", b"liga", b"clig"), kind=4, extension=7
            )
        if "GPOS" in tables:
            info.pair_lookups = _lookups(data, tables["GPOS"][0], (b"kern",), kind=2, extension=9)
        if not info.pair_lookups and "kern" in tables:
            info.legacy_kern = _legacy_kern(data, tables["kern"][0])
        return info
    except (struct.error, IndexError) as exc:
        raise FontTableError(f"malformed font tables ({exc})") from exc
