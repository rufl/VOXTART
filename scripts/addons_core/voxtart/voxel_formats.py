# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Portable voxel codecs for MagicaVoxel VOX and Qubicle QB files."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable, Sequence

from .voxel import Color, VoxelAsset


MAX_FORMAT_VOXELS = 4_000_000
MAGICA_VOX_VERSION = 150
QB_CODEFLAG = 2
QB_NEXTSLICEFLAG = 6


def _color(channels: Iterable[int | float], *, alpha: float | None = None) -> Color:
    values = tuple(channels)
    if len(values) != 4:
        raise ValueError("voxel format colors must contain four channels")
    result = tuple(max(0.0, min(1.0, float(value) / 255.0)) for value in values)
    if alpha is not None:
        return result[:3] + (max(0.0, min(1.0, float(alpha))),)  # type: ignore[return-value]
    return result  # type: ignore[return-value]


_MAGICA_DEFAULT_PALETTE_VALUES = tuple(
    int(value, 16)
    for value in """
    00000000 ffffffff ffccffff ff99ffff ff66ffff ff33ffff ff00ffff ffffccff
    ffccccff ff99ccff ff66ccff ff33ccff ff00ccff ffff99ff ffcc99ff ff9999ff
    ff6699ff ff3399ff ff0099ff ffff66ff ffcc66ff ff9966ff ff6666ff ff3366ff
    ff0066ff ffff33ff ffcc33ff ff9933ff ff6633ff ff3333ff ff0033ff ffff00ff
    ffcc00ff ff9900ff ff6600ff ff3300ff ff0000ff ffffffcc ffccffcc ff99ffcc
    ff66ffcc ff33ffcc ff00ffcc ffffcccc ffcccccc ff99cccc ff66cccc ff33cccc
    ff00cccc ffff99cc ffcc99cc ff9999cc ff6699cc ff3399cc ff0099cc ffff66cc
    ffcc66cc ff9966cc ff6666cc ff3366cc ff0066cc ffff33cc ffcc33cc ff9933cc
    ff6633cc ff3333cc ff0033cc ffff00cc ffcc00cc ff9900cc ff6600cc ff3300cc
    ff0000cc ffffff99 ffccff99 ff99ff99 ff66ff99 ff33ff99 ff00ff99 ffffcc99
    ffcccc99 ff99cc99 ff66cc99 ff33cc99 ff00cc99 ffff9999 ffcc9999 ff999999
    ff669999 ff339999 ff009999 ffff6699 ffcc6699 ff996699 ff666699 ff336699
    ff006699 ffff3399 ffcc3399 ff993399 ff663399 ff333399 ff003399 ffff0099
    ffcc0099 ff990099 ff660099 ff330099 ff000099 ffffff66 ffccff66 ff99ff66
    ff66ff66 ff33ff66 ff00ff66 ffffcc66 ffcccc66 ff99cc66 ff66cc66 ff33cc66
    ff00cc66 ffff9966 ffcc9966 ff999966 ff669966 ff339966 ff009966 ffff6666
    ffcc6666 ff996666 ff666666 ff336666 ff006666 ffff3366 ffcc3366 ff993366
    ff663366 ff333366 ff003366 ffff0066 ffcc0066 ff990066 ff660066 ff330066
    ff000066 ffffff33 ffccff33 ff99ff33 ff66ff33 ff33ff33 ff00ff33 ffffcc33
    ffcccc33 ff99cc33 ff66cc33 ff33cc33 ff00cc33 ffff9933 ffcc9933 ff999933
    ff669933 ff339933 ff009933 ffff6633 ffcc6633 ff996633 ff666633 ff336633
    ff006633 ffff3333 ffcc3333 ff993333 ff663333 ff333333 ff003333 ffff0033
    ffcc0033 ff990033 ff660033 ff330033 ff000033 ffffff00 ffccff00 ff99ff00
    ff66ff00 ff33ff00 ff00ff00 ffffcc00 ffcccc00 ff99cc00 ff66cc00 ff33cc00
    ff00cc00 ffff9900 ffcc9900 ff999900 ff669900 ff339900 ff009900 ffff6600
    ffcc6600 ff996600 ff666600 ff336600 ff006600 ffff3300 ffcc3300 ff993300
    ff663300 ff333300 ff003300 ffff0000 ffcc0000 ff990000 ff660000 ff330000
    ff0000ee ff0000dd ff0000bb ff0000aa ff000088 ff000077 ff000055
    ff000044 ff000022 ff000011 ff00ee00 ff00dd00 ff00bb00 ff00aa00 ff008800
    ff007700 ff005500 ff004400 ff002200 ff001100 ffee0000 ffdd0000 ffbb0000
    ffaa0000 ff880000 ff770000 ff550000 ff440000 ff220000 ff110000 ffeeeeee
    ffdddddd ffbbbbbb ffaaaaaa ff888888 ff777777 ff555555 ff444444 ff222222
    ff111111
    """.split()
)
def _fallback_magica_palette() -> tuple[Color, ...]:
    return tuple(
        tuple(channel / 255.0 for channel in struct.pack("<I", value))  # type: ignore[return-value]
        for value in _MAGICA_DEFAULT_PALETTE_VALUES[1:]
    )


@dataclass(frozen=True, slots=True)
class VoxelFormatModel:
    """One model/matrix from a multi-model voxel file."""

    name: str
    asset: VoxelAsset
    position: tuple[int, int, int] = (0, 0, 0)


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError("truncated voxel format file")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def u8(self) -> int:
        return self.read(1)[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]


def _vox_chunks(data: bytes, start: int, end: int):
    offset = start
    while offset < end:
        if offset + 12 > end:
            raise ValueError("truncated MagicaVoxel chunk header")
        chunk_id = data[offset:offset + 4]
        content_size, child_size = struct.unpack_from("<II", data, offset + 4)
        content_start = offset + 12
        content_end = content_start + content_size
        child_end = content_end + child_size
        if content_end > end or child_end > end:
            raise ValueError("MagicaVoxel chunk exceeds file bounds")
        content = data[content_start:content_end]
        yield chunk_id, content
        if child_size:
            yield from _vox_chunks(data, content_end, child_end)
        offset = child_end
    if offset != end:
        raise ValueError("invalid MagicaVoxel chunk boundary")


def _vox_chunk(chunk_id: bytes, content: bytes = b"", children: bytes = b"") -> bytes:
    return chunk_id + struct.pack("<II", len(content), len(children)) + content + children


def decode_magica_vox_models(data: bytes, *, grid_size: float = 1.0) -> tuple[VoxelFormatModel, ...]:
    """Decode all SIZE/XYZI model pairs from a MagicaVoxel VOX file."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("MagicaVoxel data must be bytes")
    data = bytes(data)
    if len(data) < 8 or data[:4] != b"VOX ":
        raise ValueError("not a MagicaVoxel VOX file")
    version = struct.unpack_from("<I", data, 4)[0]
    if version <= 0:
        raise ValueError("invalid MagicaVoxel version")
    if data[8:12] != b"MAIN":
        raise ValueError("MagicaVoxel file is missing MAIN chunk")

    main_content_size, main_child_size = struct.unpack_from("<II", data, 12)
    main_content_start = 20
    main_content_end = main_content_start + main_content_size
    main_end = main_content_end + main_child_size
    if main_end > len(data):
        raise ValueError("MagicaVoxel MAIN chunk exceeds file bounds")

    palette: tuple[Color, ...] = _fallback_magica_palette()
    raw_models: list[tuple[tuple[int, int, int], tuple[tuple[int, int, int, int], ...]]] = []
    pending_size: tuple[int, int, int] | None = None
    for chunk_id, content in _vox_chunks(data, main_content_end, main_end):
        if chunk_id == b"RGBA":
            if len(content) < 256 * 4:
                raise ValueError("MagicaVoxel RGBA chunk is truncated")
            palette = tuple(
                _color(content[index:index + 4])
                for index in range(0, 255 * 4, 4)
            )
        elif chunk_id == b"SIZE":
            if len(content) < 12:
                raise ValueError("MagicaVoxel SIZE chunk is truncated")
            size = struct.unpack_from("<III", content, 0)
            if any(axis <= 0 or axis > 256 for axis in size):
                raise ValueError(f"MagicaVoxel model dimensions are unsupported: {size!r}")
            pending_size = size
        elif chunk_id == b"XYZI":
            if len(content) < 4:
                raise ValueError("MagicaVoxel XYZI chunk is truncated")
            count = struct.unpack_from("<I", content, 0)[0]
            if count > MAX_FORMAT_VOXELS or len(content) < 4 + count * 4:
                raise ValueError("MagicaVoxel XYZI voxel count is unsupported")
            voxels = tuple(
                tuple(content[offset + axis] for axis in range(4))  # type: ignore[misc]
                for offset in range(4, 4 + count * 4, 4)
            )
            if pending_size is None:
                inferred = tuple(
                    max((voxel[axis] for voxel in voxels), default=0) + 1
                    for axis in range(3)
                )
                pending_size = inferred
            raw_models.append((pending_size, voxels))
            pending_size = None

    if not raw_models:
        raise ValueError("MagicaVoxel file contains no model")
    models: list[VoxelFormatModel] = []
    for index, (size, raw_voxels) in enumerate(raw_models, start=1):
        voxels: dict[tuple[int, int, int], int] = {}
        for x, y, z, color_index in raw_voxels:
            if color_index == 0:
                continue
            if x >= size[0] or y >= size[1] or z >= size[2]:
                raise ValueError("MagicaVoxel voxel lies outside its SIZE chunk")
            palette_index = color_index - 1
            if palette_index >= len(palette):
                raise ValueError("MagicaVoxel color index exceeds its palette")
            voxels[(x, y, z)] = palette_index
        asset = VoxelAsset(
            grid_size=grid_size,
            palette=list(palette),
            voxels=voxels,
            provenance={"format": "magica_vox", "model_index": str(index)},
        )
        models.append(VoxelFormatModel(f"VOX Model {index}", asset))
    return tuple(models)


def decode_magica_vox(data: bytes, *, grid_size: float = 1.0) -> VoxelAsset:
    """Decode the first MagicaVoxel model for single-asset callers."""

    return decode_magica_vox_models(data, grid_size=grid_size)[0].asset


def _global_palette(assets: Sequence[VoxelAsset]) -> tuple[tuple[Color, ...], list[dict[int, int]]]:
    colors: list[Color] = []
    lookup: dict[Color, int] = {}
    mappings: list[dict[int, int]] = []
    for asset in assets:
        mapping: dict[int, int] = {}
        for index in sorted(set(asset.voxels.values())):
            color = asset.palette[index]
            global_index = lookup.get(color)
            if global_index is None:
                if len(colors) >= 255:
                    raise ValueError("MagicaVoxel supports at most 255 addressable palette colors")
                global_index = len(colors)
                lookup[color] = global_index
                colors.append(color)
            mapping[index] = global_index
        mappings.append(mapping)
    if not colors:
        raise ValueError("cannot export an empty palette")
    return tuple(colors), mappings


def _asset_bounds(asset: VoxelAsset) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    bounds = asset.bounds
    if bounds is None:
        raise ValueError("cannot export an empty voxel asset")
    return bounds


def encode_magica_vox_models(models: Sequence[VoxelFormatModel | VoxelAsset]) -> bytes:
    """Encode one or more assets as a MagicaVoxel VOX file."""

    normalized: list[VoxelFormatModel] = []
    for index, model in enumerate(models, start=1):
        if isinstance(model, VoxelAsset):
            normalized.append(VoxelFormatModel(f"VOX Model {index}", model))
        else:
            normalized.append(model)
    if not normalized:
        raise ValueError("cannot export an empty MagicaVoxel model list")
    assets = [model.asset for model in normalized]
    palette, mappings = _global_palette(assets)
    children: list[bytes] = []
    if len(normalized) > 1:
        children.append(_vox_chunk(b"PACK", struct.pack("<I", len(normalized))))
    for model, mapping in zip(normalized, mappings):
        minimum, maximum = _asset_bounds(model.asset)
        size = tuple(maximum[axis] - minimum[axis] + 1 for axis in range(3))
        if any(axis > 256 for axis in size):
            raise ValueError(f"MagicaVoxel model dimensions exceed 256: {size!r}")
        raw_voxels = bytearray()
        ordered_voxels = sorted(model.asset.voxels.items())
        if len(ordered_voxels) > MAX_FORMAT_VOXELS:
            raise ValueError("MagicaVoxel export voxel count is unsupported")
        raw_voxels.extend(struct.pack("<I", len(ordered_voxels)))
        for cell, palette_index in ordered_voxels:
            local = tuple(cell[axis] - minimum[axis] for axis in range(3))
            color_index = mapping[palette_index] + 1
            raw_voxels.extend(bytes((*local, color_index)))
        children.append(_vox_chunk(b"SIZE", struct.pack("<III", *size)))
        children.append(_vox_chunk(b"XYZI", bytes(raw_voxels)))
    rgba = bytearray()
    for color in palette:
        rgba.extend(bytes(round(max(0.0, min(1.0, channel)) * 255.0) for channel in color))
    rgba.extend(b"\x00\x00\x00\x00" * (256 - len(palette)))
    children.append(_vox_chunk(b"RGBA", bytes(rgba)))
    main = _vox_chunk(b"MAIN", b"", b"".join(children))
    return b"VOX " + struct.pack("<I", MAGICA_VOX_VERSION) + main


def encode_magica_vox(asset: VoxelAsset) -> bytes:
    return encode_magica_vox_models((asset,))


def _qb_color(color: Color, color_format: int) -> int:
    red, green, blue, alpha = (
        max(0, min(255, round(float(channel) * 255.0)))
        for channel in color
    )
    alpha = max(1, alpha)
    if color_format == 0:
        return struct.unpack("<I", bytes((red, green, blue, alpha)))[0]
    if color_format == 1:
        return struct.unpack("<I", bytes((blue, green, red, alpha)))[0]
    raise ValueError("Qubicle color format must be 0 (RGBA) or 1 (BGRA)")


def _qb_voxel_stream(values: Sequence[int], size_x: int, size_y: int, size_z: int, compressed: bool) -> bytes:
    output = bytearray()
    plane_size = size_x * size_y
    for z in range(size_z):
        plane = values[z * plane_size:(z + 1) * plane_size]
        if compressed:
            index = 0
            while index < len(plane):
                value = plane[index]
                end = index + 1
                while end < len(plane) and plane[end] == value:
                    end += 1
                count = end - index
                if count > 1 or value in (QB_CODEFLAG, QB_NEXTSLICEFLAG):
                    output.extend(struct.pack("<III", QB_CODEFLAG, count, value))
                else:
                    output.extend(struct.pack("<I", value))
                index = end
            output.extend(struct.pack("<I", QB_NEXTSLICEFLAG))
        else:
            output.extend(struct.pack("<" + "I" * len(plane), *plane))
    return bytes(output)


def encode_qb_models(
    models: Sequence[VoxelFormatModel | VoxelAsset],
    *,
    compressed: bool = True,
    color_format: int = 0,
) -> bytes:
    """Encode assets as Qubicle Binary matrices with optional RLE compression."""

    normalized: list[VoxelFormatModel] = []
    for index, model in enumerate(models, start=1):
        if isinstance(model, VoxelAsset):
            normalized.append(VoxelFormatModel(f"VOXTART Matrix {index}", model))
        else:
            normalized.append(model)
    if not normalized:
        raise ValueError("cannot export an empty Qubicle matrix list")
    if color_format not in (0, 1):
        raise ValueError("Qubicle color format must be 0 (RGBA) or 1 (BGRA)")

    body = bytearray()
    for model in normalized:
        asset = model.asset
        minimum, maximum = _asset_bounds(asset)
        size = tuple(maximum[axis] - minimum[axis] + 1 for axis in range(3))
        volume = size[0] * size[1] * size[2]
        if volume > MAX_FORMAT_VOXELS:
            raise ValueError("Qubicle matrix voxel volume is unsupported")
        name = str(model.name or "VOXTART Matrix").encode("utf-8")[:255]
        position = tuple(int(model.position[axis] + minimum[axis]) for axis in range(3))
        body.extend(struct.pack("<B", len(name)))
        body.extend(name)
        body.extend(struct.pack("<IIIiii", *size, *position))
        packed_palette = {
            index: _qb_color(color, color_format)
            for index, color in enumerate(asset.palette)
        }
        values = [
            packed_palette[asset.voxels[cell]] if cell in asset.voxels else 0
            for z in range(size[2])
            for y in range(size[1])
            for x in range(size[0])
            for cell in ((x + minimum[0], y + minimum[1], z + minimum[2]),)
        ]
        body.extend(_qb_voxel_stream(values, size[0], size[1], size[2], compressed))

    header = struct.pack(
        "<IIIIII",
        0x00000101,
        color_format,
        1,
        1 if compressed else 0,
        0,
        len(normalized),
    )
    return header + bytes(body)


def encode_qb(asset: VoxelAsset, *, compressed: bool = True, color_format: int = 0) -> bytes:
    return encode_qb_models((asset,), compressed=compressed, color_format=color_format)


def _decode_qb_color(value: int, color_format: int) -> Color:
    red, green, blue, alpha = struct.pack("<I", value)
    if color_format == 1:
        red, blue = blue, red
    if alpha == 0:
        return (red / 255.0, green / 255.0, blue / 255.0, 0.0)
    return (red / 255.0, green / 255.0, blue / 255.0, 1.0)


def decode_qb_models(data: bytes, *, grid_size: float = 1.0) -> tuple[VoxelFormatModel, ...]:
    """Decode compressed or uncompressed Qubicle Binary matrices."""

    reader = _Reader(bytes(data))
    if len(reader.data) < 24:
        raise ValueError("truncated Qubicle Binary header")
    _version = reader.u32()
    color_format = reader.u32()
    _z_axis_orientation = reader.u32()
    compressed = reader.u32()
    visibility_mask_encoded = reader.u32()
    matrix_count = reader.u32()
    if color_format not in (0, 1):
        raise ValueError("unsupported Qubicle color format")
    if matrix_count <= 0 or matrix_count > 4096:
        raise ValueError("unsupported Qubicle matrix count")

    models: list[VoxelFormatModel] = []
    for matrix_index in range(matrix_count):
        name_length = reader.u8()
        name = reader.read(name_length).decode("utf-8", errors="replace") or f"QB Matrix {matrix_index + 1}"
        size_x, size_y, size_z = reader.u32(), reader.u32(), reader.u32()
        position = reader.i32(), reader.i32(), reader.i32()
        volume = size_x * size_y * size_z
        if not all((size_x, size_y, size_z)) or volume > MAX_FORMAT_VOXELS:
            raise ValueError("unsupported Qubicle matrix dimensions")

        values: list[int] = []
        if compressed:
            plane_size = size_x * size_y
            for _z in range(size_z):
                while True:
                    value = reader.u32()
                    if value == QB_NEXTSLICEFLAG:
                        if len(values) % plane_size != 0:
                            raise ValueError("Qubicle compressed slice has the wrong voxel count")
                        break
                    if value == QB_CODEFLAG:
                        count = reader.u32()
                        color = reader.u32()
                        if count <= 0 or len(values) + count > volume:
                            raise ValueError("Qubicle RLE run exceeds matrix bounds")
                        values.extend([color] * count)
                    else:
                        if len(values) + 1 > volume:
                            raise ValueError("Qubicle voxel stream exceeds matrix bounds")
                        values.append(value)
        else:
            values = [reader.u32() for _ in range(volume)]

        palette: list[Color] = []
        palette_lookup: dict[Color, int] = {}
        voxels: dict[tuple[int, int, int], int] = {}
        for index, packed in enumerate(values):
            color = _decode_qb_color(packed, color_format)
            if color[3] <= 0.0:
                continue
            if visibility_mask_encoded:
                alpha_mask = struct.pack("<I", packed)[3]
                if alpha_mask == 0:
                    continue
            opaque = (color[0], color[1], color[2], 1.0)
            palette_index = palette_lookup.get(opaque)
            if palette_index is None:
                palette_index = len(palette)
                palette_lookup[opaque] = palette_index
                palette.append(opaque)
            x = index % size_x
            y = (index // size_x) % size_y
            z = index // (size_x * size_y)
            voxels[(x, y, z)] = palette_index
        if not palette:
            palette = [(0.8, 0.8, 0.8, 1.0)]
        asset = VoxelAsset(
            grid_size=grid_size,
            palette=palette,
            voxels=voxels,
            provenance={"format": "qubicle_qb", "matrix": name},
        )
        models.append(VoxelFormatModel(name, asset, position))
    return tuple(models)


def decode_qb(data: bytes, *, grid_size: float = 1.0) -> VoxelAsset:
    return decode_qb_models(data, grid_size=grid_size)[0].asset


__all__ = [
    "MAGICA_VOX_VERSION",
    "MAX_FORMAT_VOXELS",
    "VoxelFormatModel",
    "decode_magica_vox",
    "decode_magica_vox_models",
    "decode_qb",
    "decode_qb_models",
    "encode_magica_vox",
    "encode_magica_vox_models",
    "encode_qb",
    "encode_qb_models",
]
