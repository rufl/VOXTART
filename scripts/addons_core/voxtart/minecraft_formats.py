# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Minecraft model, structure, schematic, and Anvil map conversion codecs."""

from __future__ import annotations

import colorsys
import hashlib
import math
import re
import struct
import time
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .minecraft_nbt import (
    NBTList,
    NBTTag,
    TAG_BYTE,
    TAG_BYTE_ARRAY,
    TAG_COMPOUND,
    TAG_INT,
    TAG_INT_ARRAY,
    TAG_LIST,
    TAG_LONG_ARRAY,
    TAG_SHORT,
    TAG_STRING,
    compound_fields,
    decode_nbt,
    encode_nbt,
    nbt_byte,
    nbt_byte_array,
    nbt_compound,
    nbt_int,
    nbt_int_array,
    nbt_list,
    nbt_long_array,
    nbt_short,
    nbt_string,
)
from .palette import nearest_palette_index
from .voxel import Color, VoxelAsset


MINECRAFT_DEFAULT_BLOCK = "minecraft:stone"
MINECRAFT_AIR_BLOCKS = frozenset({
    "air",
    "cave_air",
    "void_air",
    "structure_void",
    "minecraft:air",
    "minecraft:cave_air",
    "minecraft:void_air",
    "minecraft:structure_void",
})
MINECRAFT_REGION_SECTOR = 4096
MINECRAFT_REGION_HEADER = MINECRAFT_REGION_SECTOR * 2
MAX_MINECRAFT_BLOCKS = 16_000_000
MAX_MINECRAFT_CHUNKS = 4096

_BLOCK_COLORS: dict[str, Color] = {
    "minecraft:stone": (0.50, 0.50, 0.50, 1.0),
    "minecraft:granite": (0.58, 0.38, 0.28, 1.0),
    "minecraft:diorite": (0.78, 0.77, 0.74, 1.0),
    "minecraft:andesite": (0.43, 0.44, 0.43, 1.0),
    "minecraft:dirt": (0.43, 0.27, 0.13, 1.0),
    "minecraft:grass_block": (0.25, 0.55, 0.18, 1.0),
    "minecraft:sand": (0.82, 0.72, 0.45, 1.0),
    "minecraft:gravel": (0.48, 0.47, 0.45, 1.0),
    "minecraft:clay": (0.57, 0.61, 0.62, 1.0),
    "minecraft:oak_planks": (0.66, 0.46, 0.22, 1.0),
    "minecraft:spruce_planks": (0.32, 0.20, 0.10, 1.0),
    "minecraft:birch_planks": (0.78, 0.68, 0.43, 1.0),
    "minecraft:jungle_planks": (0.56, 0.34, 0.17, 1.0),
    "minecraft:acacia_planks": (0.66, 0.30, 0.14, 1.0),
    "minecraft:dark_oak_planks": (0.25, 0.14, 0.07, 1.0),
    "minecraft:oak_log": (0.39, 0.25, 0.11, 1.0),
    "minecraft:leaves": (0.20, 0.50, 0.17, 1.0),
    "minecraft:oak_leaves": (0.20, 0.50, 0.17, 1.0),
    "minecraft:grass": (0.28, 0.65, 0.15, 1.0),
    "minecraft:water": (0.10, 0.32, 0.75, 0.65),
    "minecraft:lava": (0.92, 0.27, 0.04, 1.0),
    "minecraft:glass": (0.72, 0.88, 0.94, 0.35),
    "minecraft:ice": (0.56, 0.82, 0.95, 0.65),
    "minecraft:snow_block": (0.92, 0.94, 0.96, 1.0),
    "minecraft:coal_ore": (0.18, 0.19, 0.18, 1.0),
    "minecraft:iron_ore": (0.65, 0.52, 0.40, 1.0),
    "minecraft:gold_ore": (0.78, 0.63, 0.16, 1.0),
    "minecraft:diamond_ore": (0.16, 0.70, 0.76, 1.0),
    "minecraft:redstone_ore": (0.70, 0.12, 0.08, 1.0),
    "minecraft:emerald_ore": (0.10, 0.68, 0.27, 1.0),
    "minecraft:lapis_ore": (0.10, 0.22, 0.65, 1.0),
    "minecraft:brick": (0.67, 0.22, 0.16, 1.0),
    "minecraft:stone_bricks": (0.42, 0.43, 0.41, 1.0),
    "minecraft:obsidian": (0.08, 0.05, 0.15, 1.0),
    "minecraft:netherrack": (0.47, 0.15, 0.13, 1.0),
    "minecraft:end_stone": (0.76, 0.75, 0.50, 1.0),
    "minecraft:quartz_block": (0.88, 0.85, 0.78, 1.0),
    "minecraft:iron_block": (0.78, 0.79, 0.76, 1.0),
    "minecraft:gold_block": (0.95, 0.70, 0.10, 1.0),
    "minecraft:diamond_block": (0.15, 0.82, 0.84, 1.0),
    "minecraft:emerald_block": (0.10, 0.78, 0.30, 1.0),
}
_LEGACY_BLOCKS = {
    0: "minecraft:air",
    1: "minecraft:stone",
    2: "minecraft:grass_block",
    3: "minecraft:dirt",
    4: "minecraft:cobblestone",
    5: "minecraft:oak_planks",
    6: "minecraft:oak_sapling",
    7: "minecraft:bedrock",
    8: "minecraft:water",
    9: "minecraft:water",
    10: "minecraft:lava",
    11: "minecraft:lava",
    12: "minecraft:sand",
    13: "minecraft:gravel",
    14: "minecraft:gold_ore",
    15: "minecraft:iron_ore",
    16: "minecraft:coal_ore",
    17: "minecraft:oak_log",
    18: "minecraft:oak_leaves",
    20: "minecraft:glass",
    21: "minecraft:lapis_ore",
    22: "minecraft:lapis_block",
    24: "minecraft:sandstone",
    35: "minecraft:white_wool",
    41: "minecraft:gold_block",
    42: "minecraft:iron_block",
    45: "minecraft:brick",
    49: "minecraft:obsidian",
    56: "minecraft:diamond_ore",
    57: "minecraft:diamond_block",
    73: "minecraft:redstone_ore",
    79: "minecraft:ice",
    80: "minecraft:snow_block",
    82: "minecraft:clay",
    87: "minecraft:netherrack",
    89: "minecraft:glowstone",
    98: "minecraft:stone_bricks",
    121: "minecraft:end_stone",
    129: "minecraft:emerald_ore",
    133: "minecraft:emerald_block",
}


def _state_name(value: str) -> str:
    value = str(value or "minecraft:air")
    if ":" not in value:
        value = f"minecraft:{value}"
    return value


def _base_block_name(state: str) -> str:
    return _state_name(state).split("[", 1)[0]


def minecraft_block_color(state: str) -> Color:
    base = _base_block_name(state)
    if base in _BLOCK_COLORS:
        return _BLOCK_COLORS[base]
    digest = hashlib.sha256(base.encode("utf-8")).digest()
    hue = int.from_bytes(digest[:2], "big") / 65535.0
    saturation = 0.35 + digest[2] / 255.0 * 0.35
    value = 0.45 + digest[3] / 255.0 * 0.45
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return red, green, blue, 1.0


def minecraft_block_for_color(color: Color) -> str:
    candidates = tuple(_BLOCK_COLORS.items())
    return min(
        candidates,
        key=lambda item: sum((float(color[index]) - item[1][index]) ** 2 for index in range(4)),
    )[0] if candidates else MINECRAFT_DEFAULT_BLOCK


def _is_air(state: str) -> bool:
    return _state_name(state) in MINECRAFT_AIR_BLOCKS


def _dimension(value: Any, field_name: str) -> int:
    result = int(value)
    if isinstance(value, bool) or result <= 0 or result > 4096:
        raise ValueError(f"Minecraft {field_name} must be between 1 and 4096")
    return result


@dataclass(slots=True)
class MinecraftVolume:
    """Sparse Minecraft block volume in local X/Y/Z coordinates."""

    width: int
    height: int
    length: int
    blocks: dict[tuple[int, int, int], str] = field(default_factory=dict)
    origin: tuple[int, int, int] = (0, 0, 0)
    data_version: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.width = _dimension(self.width, "width")
        self.height = _dimension(self.height, "height")
        self.length = _dimension(self.length, "length")
        self.origin = tuple(int(axis) for axis in self.origin)
        if len(self.origin) != 3:
            raise ValueError("Minecraft volume origin must contain three integers")
        normalized: dict[tuple[int, int, int], str] = {}
        for cell, state in self.blocks.items():
            position = tuple(int(axis) for axis in cell)
            if len(position) != 3 or not all(0 <= position[index] < (self.width, self.height, self.length)[index] for index in range(3)):
                raise ValueError(f"Minecraft block lies outside volume: {cell!r}")
            state = _state_name(state)
            if not _is_air(state):
                normalized[position] = state
        if len(normalized) > MAX_MINECRAFT_BLOCKS:
            raise ValueError("Minecraft volume exceeds the block safety limit")
        self.blocks = normalized

    @classmethod
    def from_world_blocks(
        cls,
        blocks: Mapping[tuple[int, int, int], str],
        *,
        data_version: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> "MinecraftVolume":
        values = {tuple(int(axis) for axis in position): _state_name(state) for position, state in blocks.items() if not _is_air(state)}
        if not values:
            return cls(1, 1, 1, data_version=data_version, metadata=dict(metadata or {}))
        minimum = tuple(min(position[index] for position in values) for index in range(3))
        maximum = tuple(max(position[index] for position in values) for index in range(3))
        local = {
            tuple(position[index] - minimum[index] for index in range(3)): state
            for position, state in values.items()
        }
        return cls(
            maximum[0] - minimum[0] + 1,
            maximum[1] - minimum[1] + 1,
            maximum[2] - minimum[2] + 1,
            local,
            origin=minimum,
            data_version=data_version,
            metadata=dict(metadata or {}),
        )

    def get(self, position: tuple[int, int, int]) -> str:
        return self.blocks.get(tuple(position), "minecraft:air")

    def set(self, position: tuple[int, int, int], state: str) -> None:
        position = tuple(int(axis) for axis in position)
        if not all(0 <= position[index] < (self.width, self.height, self.length)[index] for index in range(3)):
            raise ValueError(f"Minecraft block lies outside volume: {position!r}")
        state = _state_name(state)
        if _is_air(state):
            self.blocks.pop(position, None)
        else:
            self.blocks[position] = state

    def iter_blocks(self):
        for position in sorted(self.blocks):
            yield position, self.blocks[position]

    def to_voxel_asset(self, *, grid_size: float = 1.0, palette_limit: int = 256) -> VoxelAsset:
        palette: list[Color] = []
        palette_lookup: dict[Color, int] = {}
        voxels: dict[tuple[int, int, int], int] = {}
        for (x, y, z), state in self.blocks.items():
            color = minecraft_block_color(state)
            if color not in palette_lookup:
                if len(palette) < int(palette_limit):
                    palette_lookup[color] = len(palette)
                    palette.append(color)
                else:
                    palette_lookup[color] = nearest_palette_index(color, palette)
            voxels[(x - self.width // 2, y - self.height // 2, z - self.length // 2)] = palette_lookup[color]
        if not palette:
            palette = [minecraft_block_color(MINECRAFT_DEFAULT_BLOCK)]
        return VoxelAsset(
            grid_size=float(grid_size),
            palette=palette,
            voxels=voxels,
            provenance={
                "workflow": "minecraft_import",
                "origin": ",".join(str(axis) for axis in self.origin),
                "block_count": str(len(self.blocks)),
            },
        )

    @classmethod
    def from_voxel_asset(cls, asset: VoxelAsset, *, default_block: str = MINECRAFT_DEFAULT_BLOCK) -> "MinecraftVolume":
        if asset.bounds is None:
            return cls(1, 1, 1)
        minimum, maximum = asset.bounds
        fallback = _state_name(default_block)
        blocks: dict[tuple[int, int, int], str] = {}
        for cell, palette_index in asset.voxels.items():
            color = asset.palette[palette_index]
            state = fallback if color[3] <= 0.0 else minecraft_block_for_color(color)
            blocks[tuple(cell[index] - minimum[index] for index in range(3))] = state
        return cls(
            maximum[0] - minimum[0] + 1,
            maximum[1] - minimum[1] + 1,
            maximum[2] - minimum[2] + 1,
            blocks,
            origin=tuple(int(axis) for axis in minimum),
            metadata={"default_block": fallback},
        )


def _value(tag: NBTTag | None, default: Any = None) -> Any:
    return default if tag is None else tag.value


def _list_values(tag: NBTTag | None) -> tuple[Any, ...]:
    if tag is None or tag.tag_type != TAG_LIST or not isinstance(tag.value, NBTList):
        return ()
    return tuple(tag.value.values)


def _payload_fields(payload: Any) -> dict[str, NBTTag]:
    if not isinstance(payload, Mapping):
        return {}
    return dict(payload)


def _state_from_palette_payload(payload: Any) -> str:
    fields = _payload_fields(payload)
    name = str(_value(fields.get("Name"), "minecraft:air"))
    properties = fields.get("Properties")
    if properties is not None and properties.tag_type == TAG_COMPOUND:
        values = []
        for key, value in sorted(compound_fields(properties).items()):
            values.append(f"{key}={value.value}")
        if values:
            name += "[" + ",".join(values) + "]"
    return _state_name(name)


def _decode_varints(values: Iterable[int], expected: int) -> list[int]:
    data = bytes(int(value) & 0xFF for value in values)
    result: list[int] = []
    current = 0
    shift = 0
    for value in data:
        current |= (value & 0x7F) << shift
        if value & 0x80:
            shift += 7
            if shift > 35:
                raise ValueError("Minecraft varint is too long")
        else:
            result.append(current)
            if len(result) >= expected:
                break
            current = 0
            shift = 0
    if len(result) < expected:
        raise ValueError("Minecraft block data ended before the volume was filled")
    return result


def _encode_varints(values: Iterable[int]) -> bytes:
    output = bytearray()
    for value in values:
        value = int(value)
        if value < 0:
            raise ValueError("Minecraft schematic palette indices must be non-negative")
        while value & ~0x7F:
            output.append((value & 0x7F) | 0x80)
            value >>= 7
        output.append(value)
    return bytes(output)


def decode_sponge_schematic(root: NBTTag) -> MinecraftVolume:
    fields = compound_fields(root)
    width = _dimension(_value(fields.get("Width"), 0), "width")
    height = _dimension(_value(fields.get("Height"), 0), "height")
    length = _dimension(_value(fields.get("Length"), 0), "length")
    palette_tag = fields.get("Palette")
    palette: dict[int, str] = {}
    if palette_tag is not None and palette_tag.tag_type == TAG_COMPOUND:
        for name, value in compound_fields(palette_tag).items():
            palette[int(value.value)] = _state_name(name)
    data_tag = fields.get("BlockData")
    indices = _decode_varints(_value(data_tag, ()), width * height * length)
    blocks: dict[tuple[int, int, int], str] = {}
    for index, palette_index in enumerate(indices):
        y, remainder = divmod(index, width * length)
        z, x = divmod(remainder, width)
        state = palette.get(palette_index, "minecraft:air")
        if not _is_air(state):
            blocks[(x, y, z)] = state
    offset = tuple(_value(fields.get("Offset"), (0, 0, 0)))
    return MinecraftVolume(width, height, length, blocks, origin=offset, data_version=int(_value(fields.get("DataVersion"), 0)))


def decode_legacy_schematic(root: NBTTag) -> MinecraftVolume:
    fields = compound_fields(root)
    width = _dimension(_value(fields.get("Width"), 0), "width")
    height = _dimension(_value(fields.get("Height"), 0), "height")
    length = _dimension(_value(fields.get("Length"), 0), "length")
    blocks_data = tuple(int(value) & 0xFF for value in _value(fields.get("Blocks"), ()))
    add_data = tuple(int(value) & 0xFF for value in _value(fields.get("AddBlocks"), ()))
    expected = width * height * length
    if len(blocks_data) < expected:
        raise ValueError("legacy schematic Blocks array is truncated")
    blocks: dict[tuple[int, int, int], str] = {}
    for index in range(expected):
        block_id = blocks_data[index]
        if add_data:
            nibble = add_data[index // 2]
            block_id |= ((nibble >> 4) if index % 2 == 0 else (nibble & 0x0F)) << 8
        state = _LEGACY_BLOCKS.get(block_id, MINECRAFT_DEFAULT_BLOCK)
        y, remainder = divmod(index, width * length)
        z, x = divmod(remainder, width)
        if not _is_air(state):
            blocks[(x, y, z)] = state
    return MinecraftVolume(width, height, length, blocks)


def decode_structure(root: NBTTag) -> MinecraftVolume:
    fields = compound_fields(root)
    size = tuple(int(value) for value in _list_values(fields.get("size")))
    if len(size) != 3:
        raise ValueError("Minecraft structure is missing a three-component size")
    width, height, length = (_dimension(size[0], "width"), _dimension(size[1], "height"), _dimension(size[2], "length"))
    palette_values = tuple(_state_from_palette_payload(value) for value in _list_values(fields.get("palette")))
    blocks: dict[tuple[int, int, int], str] = {}
    for payload in _list_values(fields.get("blocks")):
        block_fields = _payload_fields(payload)
        position = tuple(int(value) for value in _list_values(block_fields.get("pos")))
        if len(position) != 3:
            continue
        state_index = int(_value(block_fields.get("state"), 0))
        state = palette_values[state_index] if 0 <= state_index < len(palette_values) else MINECRAFT_DEFAULT_BLOCK
        if not _is_air(state):
            blocks[position] = state
    return MinecraftVolume(width, height, length, blocks, data_version=int(_value(fields.get("DataVersion"), 0)))


def decode_minecraft_nbt(data: bytes, *, format_hint: str = "AUTO") -> MinecraftVolume:
    root = decode_nbt(data)
    fields = compound_fields(root)
    hint = str(format_hint or "AUTO").upper()
    if hint in {"SCHEM", "SPONGE"} or "BlockData" in fields:
        return decode_sponge_schematic(root)
    if hint in {"SCHEMATIC", "LEGACY"} or "Blocks" in fields:
        return decode_legacy_schematic(root)
    if hint in {"STRUCTURE", "NBT"} or "size" in fields and "palette" in fields and "blocks" in fields:
        return decode_structure(root)
    raise ValueError("NBT file is not a supported Sponge schematic, legacy schematic, or structure")


def _palette_payload(state: str) -> dict[str, NBTTag]:
    base = _base_block_name(state)
    properties: dict[str, NBTTag] = {}
    if "[" in state and state.endswith("]"):
        raw_properties = state.split("[", 1)[1][:-1]
        for item in raw_properties.split(","):
            if "=" in item:
                key, value = item.split("=", 1)
                properties[key] = nbt_string(key, value)
    fields: dict[str, NBTTag] = {"Name": nbt_string("Name", base)}
    if properties:
        fields["Properties"] = nbt_compound("Properties", properties)
    return fields


def encode_sponge_schematic(volume: MinecraftVolume, *, data_version: int = 3105) -> bytes:
    states = sorted(set(volume.blocks.values()))
    palette_states = ["minecraft:air", *states]
    palette = {state: index for index, state in enumerate(palette_states)}
    indices = []
    for y in range(volume.height):
        for z in range(volume.length):
            for x in range(volume.width):
                indices.append(palette.get(volume.get((x, y, z)), 0))
    palette_fields = {
        state: nbt_int(state, index)
        for state, index in palette.items()
    }
    fields = {
        "Version": nbt_int("Version", 2),
        "DataVersion": nbt_int("DataVersion", int(data_version)),
        "Width": nbt_short("Width", volume.width),
        "Height": nbt_short("Height", volume.height),
        "Length": nbt_short("Length", volume.length),
        "Offset": nbt_int_array("Offset", volume.origin),
        "PaletteMax": nbt_int("PaletteMax", len(palette_states)),
        "Palette": nbt_compound("Palette", palette_fields),
        "BlockData": nbt_byte_array("BlockData", _encode_varints(indices)),
        "BlockEntities": nbt_list("BlockEntities", TAG_COMPOUND, ()),
        "Entities": nbt_list("Entities", TAG_COMPOUND, ()),
        "Metadata": nbt_compound("Metadata", {"Name": nbt_string("Name", "VOXTART Export")}),
    }
    return encode_nbt(nbt_compound("Schematic", fields), compression="gzip")


def encode_legacy_schematic(volume: MinecraftVolume) -> bytes:
    reverse = {state: block_id for block_id, state in _LEGACY_BLOCKS.items()}
    values = []
    for y in range(volume.height):
        for z in range(volume.length):
            for x in range(volume.width):
                state = volume.get((x, y, z))
                values.append(reverse.get(state, 1))
    blocks = bytes(value & 0xFF for value in values)
    add_blocks = bytearray((len(values) + 1) // 2)
    for index, value in enumerate(values):
        high = (value >> 8) & 0x0F
        if index % 2 == 0:
            add_blocks[index // 2] |= high << 4
        else:
            add_blocks[index // 2] |= high
    fields = {
        "Materials": nbt_string("Materials", "Alpha"),
        "Width": nbt_short("Width", volume.width),
        "Height": nbt_short("Height", volume.height),
        "Length": nbt_short("Length", volume.length),
        "Blocks": nbt_byte_array("Blocks", blocks),
        "Data": nbt_byte_array("Data", bytes(len(values) // 2 + len(values) % 2)),
    }
    if any(add_blocks):
        fields["AddBlocks"] = nbt_byte_array("AddBlocks", add_blocks)
    return encode_nbt(nbt_compound("Schematic", fields), compression="gzip")


def encode_structure(volume: MinecraftVolume, *, data_version: int = 3105) -> bytes:
    states = sorted(set(volume.blocks.values()))
    palette_states = ["minecraft:air", *states]
    palette_index = {state: index for index, state in enumerate(palette_states)}
    palette = tuple(_palette_payload(state) for state in palette_states)
    block_values = []
    for position, state in volume.iter_blocks():
        block_values.append(nbt_compound("", {
            "pos": nbt_list("pos", TAG_INT, position),
            "state": nbt_int("state", palette_index[state]),
        }).value)
    fields = {
        "DataVersion": nbt_int("DataVersion", int(data_version)),
        "size": nbt_list("size", TAG_INT, (volume.width, volume.height, volume.length)),
        "palette": nbt_list("palette", TAG_COMPOUND, palette),
        "blocks": nbt_list("blocks", TAG_COMPOUND, block_values),
        "entities": nbt_list("entities", TAG_COMPOUND, ()),
    }
    return encode_nbt(nbt_compound("", fields), compression="gzip")


def _read_chunk_payload(payload: bytes) -> NBTTag:
    if not payload:
        raise ValueError("empty Minecraft chunk payload")
    compression = payload[0]
    data = payload[1:]
    if compression == 1:
        return decode_nbt(data, compression="gzip")
    if compression == 2:
        return decode_nbt(data, compression="zlib")
    if compression == 3:
        return decode_nbt(data, compression="raw")
    raise ValueError(f"unsupported Anvil chunk compression: {compression}")


def _decode_packed_indices(words: Iterable[int], count: int, palette_size: int) -> list[int]:
    if palette_size <= 1:
        return [0] * count
    bits = max(4, (palette_size - 1).bit_length())
    values = [int(word) & ((1 << 64) - 1) for word in words]
    result = []
    mask = (1 << bits) - 1
    for index in range(count):
        offset = index * bits
        word_index, shift = divmod(offset, 64)
        if word_index >= len(values):
            result.append(0)
            continue
        value = values[word_index] >> shift
        if shift + bits > 64 and word_index + 1 < len(values):
            value |= values[word_index + 1] << (64 - shift)
        result.append(value & mask)
    return result


def _encode_packed_indices(values: Iterable[int], palette_size: int) -> tuple[int, ...]:
    values = tuple(int(value) for value in values)
    if palette_size <= 1:
        return ()
    bits = max(4, (palette_size - 1).bit_length())
    words = [0] * ((len(values) * bits + 63) // 64)
    mask = (1 << bits) - 1
    for index, value in enumerate(values):
        offset = index * bits
        word_index, shift = divmod(offset, 64)
        value &= mask
        words[word_index] |= value << shift
        if shift + bits > 64:
            words[word_index + 1] |= value >> (64 - shift)
    return tuple(value if value < (1 << 63) else value - (1 << 64) for value in words)


def _decode_chunk(root: NBTTag, chunk_x: int, chunk_z: int) -> dict[tuple[int, int, int], str]:
    fields = compound_fields(root)
    level_tag = fields.get("Level")
    level = compound_fields(level_tag) if level_tag is not None and level_tag.tag_type == TAG_COMPOUND else fields
    sections_tag = level.get("sections", level.get("Sections"))
    blocks: dict[tuple[int, int, int], str] = {}
    for payload in _list_values(sections_tag):
        section = _payload_fields(payload)
        section_y = int(_value(section.get("Y"), 0))
        block_states_tag = section.get("block_states", section.get("BlockStates"))
        if block_states_tag is not None and block_states_tag.tag_type == TAG_COMPOUND:
            block_states = compound_fields(block_states_tag)
            palette_values = tuple(_state_from_palette_payload(item) for item in _list_values(block_states.get("palette")))
            if not palette_values:
                continue
            indices = _decode_packed_indices(_value(block_states.get("data"), ()), 4096, len(palette_values))
            for index, palette_index in enumerate(indices):
                state = palette_values[palette_index] if 0 <= palette_index < len(palette_values) else "minecraft:air"
                if _is_air(state):
                    continue
                local_y, remainder = divmod(index, 256)
                local_z, local_x = divmod(remainder, 16)
                blocks[(chunk_x * 16 + local_x, section_y * 16 + local_y, chunk_z * 16 + local_z)] = state
            continue
        legacy_blocks = _value(section.get("Blocks"), ())
        if not legacy_blocks:
            continue
        add = tuple(int(value) & 0xFF for value in _value(section.get("Add"), ()))
        for index, raw_id in enumerate(legacy_blocks[:4096]):
            block_id = int(raw_id) & 0xFF
            if add:
                nibble = add[index // 2]
                block_id |= ((nibble >> 4) if index % 2 == 0 else nibble & 0x0F) << 8
            state = _LEGACY_BLOCKS.get(block_id, MINECRAFT_DEFAULT_BLOCK)
            if _is_air(state):
                continue
            local_y, remainder = divmod(index, 256)
            local_z, local_x = divmod(remainder, 16)
            blocks[(chunk_x * 16 + local_x, section_y * 16 + local_y, chunk_z * 16 + local_z)] = state
    return blocks


def decode_anvil_region(data: bytes, *, region_x: int = 0, region_z: int = 0) -> MinecraftVolume:
    if len(data) < MINECRAFT_REGION_HEADER:
        raise ValueError("Anvil region is smaller than its 8 KiB header")
    world_blocks: dict[tuple[int, int, int], str] = {}
    chunk_count = 0
    for index in range(1024):
        location = int.from_bytes(data[index * 4:index * 4 + 4], "big")
        sector_offset = location >> 8
        sector_count = location & 0xFF
        if sector_offset == 0 or sector_count == 0:
            continue
        chunk_count += 1
        if chunk_count > MAX_MINECRAFT_CHUNKS:
            raise ValueError("Anvil region contains too many chunks")
        start = sector_offset * MINECRAFT_REGION_SECTOR
        end = start + sector_count * MINECRAFT_REGION_SECTOR
        if end > len(data) or start + 5 > len(data):
            raise ValueError("Anvil chunk points outside the region")
        length = struct.unpack(">I", data[start:start + 4])[0]
        payload = data[start + 4:start + 4 + length]
        chunk_x = region_x * 32 + index % 32
        chunk_z = region_z * 32 + index // 32
        world_blocks.update(_decode_chunk(_read_chunk_payload(payload), chunk_x, chunk_z))
    return MinecraftVolume.from_world_blocks(world_blocks)


def decode_minecraft_world(path: str | Path) -> MinecraftVolume:
    path = Path(path).expanduser()
    if path.is_file() and path.suffix.lower() == ".mca":
        match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name, re.IGNORECASE)
        region_x, region_z = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
        return decode_anvil_region(path.read_bytes(), region_x=region_x, region_z=region_z)
    if not path.is_dir():
        raise ValueError("choose an Anvil .mca file or a Minecraft world directory")
    region_directory = path / "region" if (path / "region").is_dir() else path
    files = sorted(region_directory.glob("r.*.*.mca"))
    if not files:
        raise ValueError("Minecraft world contains no Anvil region files")
    world_blocks: dict[tuple[int, int, int], str] = {}
    data_version = 0
    for region_path in files:
        match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", region_path.name, re.IGNORECASE)
        if match is None:
            continue
        region = decode_anvil_region(region_path.read_bytes(), region_x=int(match.group(1)), region_z=int(match.group(2)))
        for position, state in region.iter_blocks():
            world = tuple(region.origin[index] + position[index] for index in range(3))
            world_blocks[world] = state
        data_version = max(data_version, region.data_version)
    return MinecraftVolume.from_world_blocks(world_blocks, data_version=data_version)


def _encode_chunk(chunk_x: int, chunk_z: int, blocks: Mapping[tuple[int, int, int], str], *, data_version: int = 3105) -> bytes:
    sections: dict[int, dict[tuple[int, int, int], str]] = defaultdict(dict)
    for (world_x, world_y, world_z), state in blocks.items():
        section_y, local_y = divmod(world_y, 16)
        local_x = world_x % 16
        local_z = world_z % 16
        sections[section_y][(local_x, local_y, local_z)] = state
    section_payloads = []
    for section_y in sorted(sections):
        section_blocks = sections[section_y]
        palette_states = ["minecraft:air", *sorted(set(section_blocks.values()))]
        palette_index = {state: index for index, state in enumerate(palette_states)}
        indices = [0] * 4096
        for (local_x, local_y, local_z), state in section_blocks.items():
            indices[(local_y * 16 + local_z) * 16 + local_x] = palette_index[state]
        palette = tuple(_palette_payload(state) for state in palette_states)
        block_states_fields = {"palette": nbt_list("palette", TAG_COMPOUND, palette)}
        packed = _encode_packed_indices(indices, len(palette_states))
        if packed:
            block_states_fields["data"] = nbt_long_array("data", packed)
        section_payloads.append(nbt_compound("", {
            "Y": nbt_byte("Y", section_y),
            "block_states": nbt_compound("block_states", block_states_fields),
        }).value)
    root = nbt_compound("", {
        "DataVersion": nbt_int("DataVersion", int(data_version)),
        "xPos": nbt_int("xPos", int(chunk_x)),
        "zPos": nbt_int("zPos", int(chunk_z)),
        "Status": nbt_string("Status", "minecraft:full"),
        "sections": nbt_list("sections", TAG_COMPOUND, section_payloads),
    })
    raw = encode_nbt(root, compression="raw")
    compressed = zlib.compress(raw)
    payload = bytes((2,)) + compressed
    return struct.pack(">I", len(payload)) + payload


def encode_anvil_region(
    volume: MinecraftVolume,
    *,
    region_x: int | None = None,
    region_z: int | None = None,
    data_version: int = 3105,
) -> bytes:
    grouped: dict[tuple[int, int], dict[tuple[int, int, int], str]] = defaultdict(dict)
    for (local_x, local_y, local_z), state in volume.iter_blocks():
        world_x = volume.origin[0] + local_x
        world_y = volume.origin[1] + local_y
        world_z = volume.origin[2] + local_z
        chunk = (math.floor(world_x / 16), math.floor(world_z / 16))
        grouped[chunk][(world_x, world_y, world_z)] = state
    if grouped:
        calculated_region_x = min(chunk[0] // 32 for chunk in grouped)
        calculated_region_z = min(chunk[1] // 32 for chunk in grouped)
    else:
        calculated_region_x = calculated_region_z = 0
    region_x = calculated_region_x if region_x is None else int(region_x)
    region_z = calculated_region_z if region_z is None else int(region_z)
    if any(chunk[0] // 32 != region_x or chunk[1] // 32 != region_z for chunk in grouped):
        raise ValueError("volume spans more than one Anvil region; export a world directory instead")
    output = bytearray(MINECRAFT_REGION_HEADER)
    sector_cursor = 2
    timestamps = int(time.time())
    for (chunk_x, chunk_z), blocks in sorted(grouped.items()):
        payload = _encode_chunk(chunk_x, chunk_z, blocks, data_version=data_version)
        sector_count = (len(payload) + MINECRAFT_REGION_SECTOR - 1) // MINECRAFT_REGION_SECTOR
        if sector_count > 255:
            raise ValueError("Minecraft chunk exceeds the Anvil sector limit")
        payload += bytes(sector_count * MINECRAFT_REGION_SECTOR - len(payload))
        index = (chunk_z - region_z * 32) * 32 + (chunk_x - region_x * 32)
        output[index * 4:index * 4 + 4] = ((sector_cursor << 8) | sector_count).to_bytes(4, "big")
        output[MINECRAFT_REGION_SECTOR + index * 4:MINECRAFT_REGION_SECTOR + index * 4 + 4] = timestamps.to_bytes(4, "big")
        output.extend(payload)
        sector_cursor += sector_count
    return bytes(output)


def write_anvil_world(volume: MinecraftVolume, directory: str | Path, *, data_version: int = 3105) -> tuple[Path, ...]:
    directory = Path(directory).expanduser()
    region_groups: set[tuple[int, int]] = set()
    for (local_x, _local_y, local_z), _state in volume.iter_blocks():
        world_x = volume.origin[0] + local_x
        world_z = volume.origin[2] + local_z
        region_groups.add((math.floor(world_x / 512), math.floor(world_z / 512)))
    if not region_groups:
        region_groups.add((0, 0))
    region_directory = directory / "region"
    region_directory.mkdir(parents=True, exist_ok=True)
    written = []
    for region_x, region_z in sorted(region_groups):
        path = region_directory / f"r.{region_x}.{region_z}.mca"
        region_blocks = {}
        for (local_x, local_y, local_z), state in volume.iter_blocks():
            world_position = (
                volume.origin[0] + local_x,
                volume.origin[1] + local_y,
                volume.origin[2] + local_z,
            )
            if (
                math.floor(world_position[0] / 512) == region_x
                and math.floor(world_position[2] / 512) == region_z
            ):
                region_blocks[world_position] = state
        region_volume = MinecraftVolume.from_world_blocks(region_blocks, data_version=volume.data_version)
        path.write_bytes(encode_anvil_region(region_volume, region_x=region_x, region_z=region_z, data_version=data_version))
        written.append(path)
    return tuple(written)


def decode_minecraft_file(path: str | Path, *, format_hint: str = "AUTO") -> MinecraftVolume:
    path = Path(path).expanduser()
    suffix = path.suffix.lower()
    if path.is_dir() or suffix == ".mca":
        return decode_minecraft_world(path)
    data = path.read_bytes()
    hint = str(format_hint or "AUTO").upper()
    if hint == "AUTO":
        hint = {".schem": "SCHEM", ".schematic": "SCHEMATIC", ".nbt": "STRUCTURE"}.get(suffix, "AUTO")
    return decode_minecraft_nbt(data, format_hint=hint)


def encode_minecraft_file(volume: MinecraftVolume, path: str | Path, format_name: str) -> tuple[Path, ...]:
    path = Path(path).expanduser()
    format_name = str(format_name or "AUTO").upper()
    if format_name == "AUTO":
        format_name = {".schem": "SCHEM", ".schematic": "SCHEMATIC", ".nbt": "STRUCTURE", ".mca": "MCA"}.get(path.suffix.lower(), "SCHEM")
    if format_name == "MCA":
        if path.suffix.lower() == ".mca":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encode_anvil_region(volume))
            return (path,)
        return write_anvil_world(volume, path)
    data = {
        "SCHEM": encode_sponge_schematic,
        "SCHEMATIC": encode_legacy_schematic,
        "STRUCTURE": encode_structure,
    }.get(format_name)
    if data is None:
        raise ValueError(f"unsupported Minecraft export format: {format_name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data(volume))
    return (path,)


__all__ = [
    "MAX_MINECRAFT_BLOCKS",
    "MINECRAFT_AIR_BLOCKS",
    "MINECRAFT_DEFAULT_BLOCK",
    "MinecraftVolume",
    "decode_anvil_region",
    "decode_legacy_schematic",
    "decode_minecraft_file",
    "decode_minecraft_nbt",
    "decode_minecraft_world",
    "decode_sponge_schematic",
    "decode_structure",
    "encode_anvil_region",
    "encode_legacy_schematic",
    "encode_minecraft_file",
    "encode_sponge_schematic",
    "encode_structure",
    "minecraft_block_color",
    "minecraft_block_for_color",
    "write_anvil_world",
]
