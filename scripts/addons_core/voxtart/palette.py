# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Palette parsing, quantization, and source metadata for VOXTART."""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .voxel import Color


MAX_PALETTE_COLORS = 256
MAX_PALETTE_BYTES = 4 * 1024 * 1024
DEFAULT_PALETTE: tuple[Color, ...] = (
    (0.05, 0.05, 0.05, 1.0),
    (0.18, 0.18, 0.18, 1.0),
    (0.32, 0.32, 0.32, 1.0),
    (0.46, 0.46, 0.46, 1.0),
    (0.60, 0.60, 0.60, 1.0),
    (0.74, 0.74, 0.74, 1.0),
    (0.88, 0.88, 0.88, 1.0),
    (1.00, 1.00, 1.00, 1.0),
)


@dataclass(frozen=True, slots=True)
class PaletteSource:
    source_id: str
    name: str
    page_url: str
    download_url: str
    formats: tuple[str, ...]
    license_note: str


PALETTE_SOURCES = (
    PaletteSource(
        "lospec_dawnbringer_16",
        "DawnBringer 16 (Lospec)",
        "https://lospec.com/palette-list/dawnbringer-16",
        "https://lospec.com/palette-list/dawnbringer-16.hex",
        ("HEX", "GPL", "PAL", "ASE", "TXT", "PNG"),
        "Lospec marks the palette download free; retain creator attribution and verify the creator's terms for your project.",
    ),
    PaletteSource(
        "lospec_endesga_32",
        "Endesga 32 (Lospec)",
        "https://lospec.com/palette-list/endesga-32",
        "https://lospec.com/palette-list/endesga-32.hex",
        ("HEX", "GPL", "PAL", "ASE", "TXT", "PNG"),
        "Lospec marks the palette download free; retain creator attribution and verify the creator's terms for your project.",
    ),
    PaletteSource(
        "lospec_endesga_64",
        "Endesga 64 (Lospec)",
        "https://lospec.com/palette-list/endesga-64",
        "https://lospec.com/palette-list/endesga-64.hex",
        ("HEX", "GPL", "PAL", "ASE", "TXT", "PNG"),
        "Lospec marks the palette download free; retain creator attribution and verify the creator's terms for your project.",
    ),
)

_HEX_TOKEN = re.compile(r"^(?:#)?([0-9a-fA-F]{3,8})$")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _channel(value: Any) -> float:
    number = float(value)
    if number > 1.0:
        number /= 255.0
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"palette channel out of range: {value!r}")
    return number


def coerce_color(value: Any) -> Color:
    if isinstance(value, str):
        token = value.strip()
        match = _HEX_TOKEN.fullmatch(token)
        if match is None:
            raise ValueError(f"invalid palette color: {value!r}")
        token = match.group(1)
        if len(token) in (3, 4):
            token = "".join(character * 2 for character in token)
        if len(token) not in (6, 8):
            raise ValueError(f"invalid palette color: {value!r}")
        channels = tuple(int(token[index:index + 2], 16) / 255.0 for index in range(0, len(token), 2))
        if len(channels) == 3:
            return (*channels, 1.0)
        return channels  # type: ignore[return-value]
    try:
        values = tuple(value)
    except TypeError as error:
        raise ValueError(f"palette color must be a hex string or numeric sequence: {value!r}") from error
    if len(values) not in (3, 4):
        raise ValueError(f"palette color must contain three or four channels: {value!r}")
    channels = tuple(_channel(item) for item in values)
    if len(channels) == 3:
        return (*channels, 1.0)
    return channels  # type: ignore[return-value]


def normalize_palette(colors: Iterable[Any]) -> list[Color]:
    result: list[Color] = []
    seen: set[Color] = set()
    for value in colors:
        color = coerce_color(value)
        if color in seen:
            continue
        seen.add(color)
        result.append(color)
        if len(result) > MAX_PALETTE_COLORS:
            raise ValueError(f"palettes may contain at most {MAX_PALETTE_COLORS} colors")
    if not result:
        raise ValueError("palette contains no colors")
    return result


def palette_gradient(
    start: Iterable[Any],
    end: Iterable[Any],
    count: int,
) -> list[Color]:
    """Return an inclusive linear RGBA gradient with a deterministic length."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_PALETTE_COLORS:
        raise ValueError(f"gradient count must be an integer in [1, {MAX_PALETTE_COLORS}]")
    first = coerce_color(start)
    last = coerce_color(end)
    if count == 1:
        return [first]
    return [
        tuple(
            first[channel] + (last[channel] - first[channel]) * index / (count - 1)
            for channel in range(4)
        )  # type: ignore[list-item]
        for index in range(count)
    ]


def reorder_palette(
    colors: Iterable[Any],
    order: Iterable[int],
) -> tuple[list[Color], dict[int, int]]:
    """Reorder palette entries and return the old-index to new-index map."""
    source = [coerce_color(color) for color in colors]
    if not source:
        raise ValueError("palette contains no colors")
    permutation = [int(index) for index in order]
    if sorted(permutation) != list(range(len(source))):
        raise ValueError("palette order must be a permutation of every palette index")
    remapped = [source[index] for index in permutation]
    index_map = {old_index: new_index for new_index, old_index in enumerate(permutation)}
    return remapped, index_map


def compact_palette(
    colors: Iterable[Any],
    voxel_indices: Iterable[int] | None = None,
) -> tuple[list[Color], dict[int, int]]:
    """Remove duplicate or unused entries while returning an index remap."""
    source = [coerce_color(color) for color in colors]
    if not source:
        raise ValueError("palette contains no colors")
    used = None if voxel_indices is None else {int(index) for index in voxel_indices}
    target: list[Color] = []
    mapping: dict[int, int] = {}
    by_color: dict[Color, int] = {}
    for old_index, color in enumerate(source):
        if used is not None and old_index not in used:
            continue
        new_index = by_color.get(color)
        if new_index is None:
            new_index = len(target)
            by_color[color] = new_index
            target.append(color)
        mapping[old_index] = new_index
    if not target:
        target = [source[0]]
        mapping[0] = 0
    return target, mapping


def _first_hex_token(line: str) -> str | None:
    for token in line.split():
        token = token.rstrip(",;")
        if _HEX_TOKEN.fullmatch(token):
            return token
    return None


def parse_hex_palette(document: str) -> list[Color]:
    colors: list[Color] = []
    for raw_line in document.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "//")):
            continue
        token = _first_hex_token(line)
        if token is not None:
            colors.append(coerce_color(token))
            continue
        fields = line.split()
        if len(fields) >= 3:
            try:
                colors.append(coerce_color([int(fields[0]), int(fields[1]), int(fields[2])]))
            except (TypeError, ValueError):
                continue
    return normalize_palette(colors)


def parse_gpl(document: str) -> list[Color]:
    lines = [line.strip() for line in document.splitlines() if line.strip()]
    if not lines or lines[0].upper() != "GIMP PALETTE":
        raise ValueError("not a GIMP GPL palette")
    colors: list[Color] = []
    for line in lines[1:]:
        if line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            colors.append(coerce_color([int(fields[0]), int(fields[1]), int(fields[2])]))
        except (TypeError, ValueError):
            continue
    return normalize_palette(colors)


def parse_jasc_pal(document: str) -> list[Color]:
    lines = [line.strip() for line in document.splitlines() if line.strip()]
    if not lines or lines[0].upper() != "JASC-PAL":
        raise ValueError("not a JASC PAL palette")
    colors: list[Color] = []
    for line in lines[2:]:
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            colors.append(coerce_color([int(fields[0]), int(fields[1]), int(fields[2])]))
        except (TypeError, ValueError):
            continue
    return normalize_palette(colors)


def parse_json_palette(document: str) -> list[Color]:
    payload = json.loads(document)
    if isinstance(payload, Mapping):
        payload = payload.get("colors", payload.get("palette"))
    if not isinstance(payload, list):
        raise ValueError("JSON palette must contain a colors or palette array")
    return normalize_palette(payload)


def _ase_rgb(model: str, values: tuple[float, ...]) -> Color | None:
    model = model.strip().upper()
    if model == "RGB" and len(values) >= 3:
        return (*(_clamp(value) for value in values[:3]), 1.0)
    if model == "GRAY" and values:
        value = _clamp(values[0])
        return (value, value, value, 1.0)
    if model == "CMYK" and len(values) >= 4:
        cyan, magenta, yellow, key = (_clamp(value) for value in values[:4])
        return (
            1.0 - min(1.0, cyan + key),
            1.0 - min(1.0, magenta + key),
            1.0 - min(1.0, yellow + key),
            1.0,
        )
    return None


def parse_ase(data: bytes) -> list[Color]:
    if len(data) < 12 or data[:4] != b"ASEF":
        raise ValueError("not an Adobe Swatch Exchange palette")
    block_count = struct.unpack_from(">I", data, 8)[0]
    offset = 12
    colors: list[Color] = []
    for _ in range(block_count):
        if offset + 6 > len(data):
            raise ValueError("truncated Adobe Swatch Exchange palette")
        block_type, block_length = struct.unpack_from(">HI", data, offset)
        offset += 6
        end = offset + block_length
        if end > len(data):
            raise ValueError("truncated Adobe Swatch Exchange block")
        if block_type == 0x0001 and offset + 2 <= end:
            name_length = struct.unpack_from(">H", data, offset)[0]
            name_bytes = name_length * 2
            color_offset = offset + 2 + name_bytes
            if color_offset + 4 <= end:
                model = data[color_offset:color_offset + 4].decode("ascii", errors="ignore")
                value_count = {"RGB ": 3, "GRAY": 1, "CMYK": 4}.get(model.upper())
                if value_count is not None and color_offset + 4 + value_count * 4 <= end:
                    values = struct.unpack_from(">" + "f" * value_count, data, color_offset + 4)
                    color = _ase_rgb(model, values)
                    if color is not None:
                        colors.append(color)
        offset = end
    return normalize_palette(colors)


def parse_palette_bytes(data: bytes, extension: str = "") -> list[Color]:
    if len(data) > MAX_PALETTE_BYTES:
        raise ValueError("palette file is too large")
    extension = extension.lower()
    if data.startswith(b"ASEF") or extension == ".ase":
        return parse_ase(data)
    document = data.decode("utf-8-sig")
    stripped = document.lstrip()
    if extension == ".json" or stripped.startswith(("[", "{")):
        return parse_json_palette(document)
    if stripped.upper().startswith("GIMP PALETTE") or extension == ".gpl":
        return parse_gpl(document)
    if stripped.upper().startswith("JASC-PAL") or extension == ".pal":
        return parse_jasc_pal(document)
    return parse_hex_palette(document)


def load_palette_file(filepath: str | Path) -> list[Color]:
    path = Path(filepath).expanduser()
    return parse_palette_bytes(path.read_bytes(), path.suffix)


def nearest_palette_index(color: Iterable[float], palette: Iterable[Color]) -> int:
    target = coerce_color(color)
    choices = tuple(palette)
    if not choices:
        raise ValueError("palette contains no colors")
    return min(
        range(len(choices)),
        key=lambda index: (
            sum((target[channel] - choices[index][channel]) ** 2 for channel in range(4)),
            index,
        ),
    )


def remap_voxels_to_palette(
    voxels: Mapping[tuple[int, int, int], int],
    source_palette: Iterable[Color],
    target_palette: Iterable[Color],
) -> dict[tuple[int, int, int], int]:
    source = tuple(source_palette)
    target = tuple(target_palette)
    if not source or not target:
        raise ValueError("source and target palettes must contain colors")
    mapping = {index: nearest_palette_index(color, target) for index, color in enumerate(source)}
    return {cell: mapping.get(index, 0) for cell, index in voxels.items()}


def source_by_id(source_id: str) -> PaletteSource:
    for source in PALETTE_SOURCES:
        if source.source_id == source_id:
            return source
    raise KeyError(f"unknown palette source: {source_id!r}")


def scene_palette(scene: Any, *, deduplicate: bool = True) -> list[Color]:
    collection = getattr(scene, "voxtart_palette_colors", None)
    if collection is not None and len(collection) > 0:
        colors = [coerce_color(item.color) for item in collection]
        return normalize_palette(colors) if deduplicate else colors
    return list(DEFAULT_PALETTE)
