# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Layered sprite-stack documents and conversion to sparse voxel assets."""

from __future__ import annotations

import base64
import io
import json
import math
import struct
import zlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from .palette import MAX_PALETTE_COLORS, nearest_palette_index
from .voxel import Color, VoxelAsset


STACK_FORMAT = "voxtart.sprite_stack"
STACK_SCHEMA_VERSION = 1
STACK_PROPERTY = "voxtart_stack_json"
STACK_SOURCE_PROPERTY = "voxtart_stack_source"
STACK_AXIS_ITEMS = ("X", "Y", "Z")
STACK_ORDER_ITEMS = ("BOTTOM_TO_TOP", "TOP_TO_BOTTOM")
MAX_STACK_LAYERS = 4096
MAX_STACK_DIMENSION = 8192
MAX_STACK_PIXELS = 16_000_000
MAX_STACK_DOCUMENT_BYTES = 128 * 1024 * 1024


def _color(value: Iterable[float]) -> Color:
    result = tuple(float(channel) for channel in value)
    if len(result) != 4 or not all(math.isfinite(channel) for channel in result):
        raise ValueError("stack pixels must contain four finite channels")
    if any(channel < 0.0 or channel > 1.0 for channel in result):
        raise ValueError("stack pixel channels must be in [0, 1]")
    return result  # type: ignore[return-value]


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError as error:
            raise ValueError(f"{field} must be an integer") from error
    raise ValueError(f"{field} must be an integer")


def _dimension(value: Any, field: str) -> int:
    result = _integer(value, field)
    if result <= 0 or result > MAX_STACK_DIMENSION:
        raise ValueError(f"{field} must be between 1 and {MAX_STACK_DIMENSION}")
    return result


def _offset(value: Iterable[int]) -> tuple[int, int]:
    try:
        result = tuple(_integer(axis, "stack layer offset") for axis in value)
    except TypeError as error:
        raise ValueError("stack layer offsets must contain two integers") from error
    if len(result) != 2:
        raise ValueError("stack layer offsets must contain two integers")
    return result  # type: ignore[return-value]


def _rgba8(pixels: Iterable[Color]) -> bytes:
    output = bytearray()
    for color in pixels:
        output.extend(max(0, min(255, int(round(channel * 255.0)))) for channel in color)
    return bytes(output)


def _decode_rgba8(value: Any, count: int) -> tuple[Color, ...]:
    if isinstance(value, Mapping):
        encoding = str(value.get("encoding", ""))
        encoded = value.get("data", "")
        if encoding != "rgba8+zlib+base64" or not isinstance(encoded, str):
            raise ValueError("unsupported stack pixel encoding")
        try:
            raw = zlib.decompress(base64.b64decode(encoded, validate=True))
        except (ValueError, zlib.error) as error:
            raise ValueError("invalid compressed stack pixels") from error
        if len(raw) != count * 4:
            raise ValueError("compressed stack pixel count does not match dimensions")
        return tuple(
            tuple(channel / 255.0 for channel in raw[index:index + 4])  # type: ignore[misc]
            for index in range(0, len(raw), 4)
        )
    if isinstance(value, str):
        try:
            raw = base64.b64decode(value, validate=True)
        except ValueError as error:
            raise ValueError("invalid stack pixel data") from error
        if len(raw) != count * 4:
            raise ValueError("stack pixel count does not match dimensions")
        return tuple(
            tuple(channel / 255.0 for channel in raw[index:index + 4])  # type: ignore[misc]
            for index in range(0, len(raw), 4)
        )
    if not isinstance(value, (list, tuple)):
        raise ValueError("stack layer pixels must be encoded data or a list")
    if len(value) == count * 4 and value and not isinstance(value[0], (list, tuple)):
        return tuple(
            _color(value[index:index + 4])
            for index in range(0, len(value), 4)
        )
    if len(value) != count:
        raise ValueError("stack layer pixel count does not match dimensions")
    return tuple(_color(pixel) for pixel in value)


@dataclass(frozen=True, slots=True)
class StackLayer:
    """One RGBA image slice in a bottom-to-top stack."""

    name: str
    width: int
    height: int
    pixels: tuple[Color, ...]
    offset: tuple[int, int] = (0, 0)
    visible: bool = True
    opacity: float = 1.0

    def __post_init__(self) -> None:
        width = _dimension(self.width, "layer width")
        height = _dimension(self.height, "layer height")
        pixels = tuple(_color(pixel) for pixel in self.pixels)
        if len(pixels) != width * height:
            raise ValueError("layer pixel count does not match dimensions")
        opacity = float(self.opacity)
        if not math.isfinite(opacity):
            raise ValueError("layer opacity must be finite")
        object.__setattr__(self, "name", str(self.name).strip() or "Layer")
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "pixels", pixels)
        object.__setattr__(self, "offset", _offset(self.offset))
        object.__setattr__(self, "visible", bool(self.visible))
        object.__setattr__(self, "opacity", max(0.0, min(1.0, opacity)))

    def pixel(self, x: int, y: int) -> Color:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            return (0.0, 0.0, 0.0, 0.0)
        return self.pixels[y * self.width + x]

    def to_dict(self) -> dict[str, Any]:
        compressed = zlib.compress(_rgba8(self.pixels), level=9)
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "offset": list(self.offset),
            "visible": self.visible,
            "opacity": self.opacity,
            "pixels": {
                "encoding": "rgba8+zlib+base64",
                "data": base64.b64encode(compressed).decode("ascii"),
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StackLayer":
        if not isinstance(data, Mapping):
            raise ValueError("stack layer must be an object")
        width = _dimension(data.get("width", 0), "layer width")
        height = _dimension(data.get("height", 0), "layer height")
        return cls(
            name=str(data.get("name", "Layer")),
            width=width,
            height=height,
            pixels=_decode_rgba8(data.get("pixels", ()), width * height),
            offset=data.get("offset", (0, 0)),
            visible=data.get("visible", True),
            opacity=data.get("opacity", 1.0),
        )


@dataclass(frozen=True, slots=True)
class StackDocument:
    """A validated layered image document with bottom-to-top ordering."""

    width: int
    height: int
    layers: tuple[StackLayer, ...]
    source_format: str = "image-stack"
    source_name: str = ""

    def __post_init__(self) -> None:
        width = _dimension(self.width, "stack width")
        height = _dimension(self.height, "stack height")
        layers = tuple(self.layers)
        if len(layers) > MAX_STACK_LAYERS:
            raise ValueError(f"stacks may contain at most {MAX_STACK_LAYERS} layers")
        if width * height * max(1, len(layers)) > MAX_STACK_PIXELS:
            raise ValueError("stack exceeds the pixel budget")
        if any(not isinstance(layer, StackLayer) for layer in layers):
            raise TypeError("stack layers must contain StackLayer values")
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "layers", layers)
        object.__setattr__(self, "source_format", str(self.source_format or "image-stack"))
        object.__setattr__(self, "source_name", str(self.source_name or ""))

    @classmethod
    def from_layers(
        cls,
        layers: Iterable[StackLayer],
        *,
        canvas_size: tuple[int, int] | None = None,
        source_format: str = "image-stack",
        source_name: str = "",
    ) -> "StackDocument":
        values = tuple(layers)
        if not values:
            raise ValueError("a stack requires at least one layer")
        if canvas_size is None:
            minimum_x = min(layer.offset[0] for layer in values)
            minimum_y = min(layer.offset[1] for layer in values)
            maximum_x = max(layer.offset[0] + layer.width for layer in values)
            maximum_y = max(layer.offset[1] + layer.height for layer in values)
            width = maximum_x - minimum_x
            height = maximum_y - minimum_y
            values = tuple(
                replace(layer, offset=(layer.offset[0] - minimum_x, layer.offset[1] - minimum_y))
                for layer in values
            )
        else:
            width, height = canvas_size
        return cls(width, height, values, source_format, source_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": STACK_FORMAT,
            "schema_version": STACK_SCHEMA_VERSION,
            "width": self.width,
            "height": self.height,
            "source_format": self.source_format,
            "source_name": self.source_name,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    def encode(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StackDocument":
        if not isinstance(data, Mapping):
            raise ValueError("stack document must be an object")
        if data.get("format") != STACK_FORMAT:
            raise ValueError(f"unsupported stack format: {data.get('format')!r}")
        schema_version = data.get("schema_version", -1)
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != STACK_SCHEMA_VERSION:
            raise ValueError("unsupported stack schema version")
        raw_layers = data.get("layers", ())
        if not isinstance(raw_layers, (list, tuple)):
            raise ValueError("stack layers must be a list")
        return cls(
            width=data.get("width", 0),
            height=data.get("height", 0),
            layers=tuple(StackLayer.from_dict(item) for item in raw_layers),
            source_format=data.get("source_format", "image-stack"),
            source_name=data.get("source_name", ""),
        )

    @classmethod
    def decode(cls, encoded: str) -> "StackDocument":
        raw = str(encoded).encode("utf-8")
        if len(raw) > MAX_STACK_DOCUMENT_BYTES:
            raise ValueError("stack document exceeds the size limit")
        try:
            payload = json.loads(encoded)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid stack JSON: {error.msg}") from error
        return cls.from_dict(payload)


def _manifest_layers(payload: Any) -> tuple[Any, ...]:
    if isinstance(payload, list):
        return tuple(payload)
    if not isinstance(payload, Mapping):
        raise ValueError("stack manifest must contain an object or list")
    for key in ("layers", "slices", "frames"):
        value = payload.get(key)
        if isinstance(value, (list, tuple)):
            return tuple(value)
    nested = payload.get("stack")
    if isinstance(nested, Mapping):
        return _manifest_layers(nested)
    raise ValueError("stack manifest contains no layers or slices")


def decode_stack_manifest(
    data: bytes | str,
    load_layer: Callable[[str, bytes | None], StackLayer],
    *,
    base_path: str | Path = ".",
    source_name: str = "",
) -> StackDocument:
    """Decode a JSON slice manifest used by SpriteStack-style workflows."""

    try:
        payload = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid stack manifest JSON") from error
    values: list[StackLayer] = []
    root = payload if isinstance(payload, Mapping) else {}
    base = Path(base_path)
    for index, item in enumerate(_manifest_layers(payload)):
        if isinstance(item, str):
            reference = item
            metadata: Mapping[str, Any] = {}
        elif isinstance(item, Mapping):
            metadata = item
            reference = str(
                item.get("file", item.get("src", item.get("path", item.get("image", ""))))
            )
        else:
            raise ValueError(f"invalid stack layer entry at index {index}")
        if metadata.get("pixels") is not None:
            layer_data = dict(metadata)
            layer_data.setdefault("name", f"Layer {index + 1:03d}")
            layer = StackLayer.from_dict(layer_data)
        else:
            if not reference:
                raise ValueError(f"stack layer {index + 1} has no image reference")
            loaded = load_layer(str(base / reference), None)
            visibility = metadata.get("visible", metadata.get("visibility", True))
            layer = replace(
                loaded,
                name=str(metadata.get("name", loaded.name or f"Layer {index + 1:03d}")),
                offset=metadata.get("offset", (metadata.get("x", 0), metadata.get("y", 0))),
                visible=visibility not in (False, 0, "hidden", "HIDDEN", "false", "False", "0"),
                opacity=metadata.get("opacity", 1.0),
            )
        values.append(layer)
    if not values:
        raise ValueError("stack manifest contains no usable layers")
    width = root.get("width") if isinstance(root, Mapping) else None
    height = root.get("height") if isinstance(root, Mapping) else None
    canvas = (int(width), int(height)) if width is not None and height is not None else None
    return StackDocument.from_layers(
        values,
        canvas_size=canvas,
        source_format=str(root.get("format", "sprite-stack-manifest")) if isinstance(root, Mapping) else "sprite-stack-manifest",
        source_name=source_name,
    )


def decode_openraster(
    data: bytes,
    load_layer: Callable[[str, bytes | None], StackLayer],
    *,
    source_name: str = "",
) -> StackDocument:
    """Decode OpenRaster (.ora) layer PNGs and their canvas offsets."""

    try:
        archive = ZipFile(io.BytesIO(data))
    except BadZipFile as error:
        raise ValueError("invalid OpenRaster archive") from error
    with archive:
        try:
            root = ElementTree.fromstring(archive.read("stack.xml"))
        except (KeyError, ElementTree.ParseError) as error:
            raise ValueError("OpenRaster file is missing stack.xml") from error
        image = root
        while image.tag.rsplit("}", 1)[-1] != "image" and len(image):
            image = next(iter(image))
        width = _dimension(image.attrib.get("w", image.attrib.get("width", 0)), "stack width")
        height = _dimension(image.attrib.get("h", image.attrib.get("height", 0)), "stack height")
        values: list[StackLayer] = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "layer":
                continue
            source = element.attrib.get("src", "")
            if not source:
                continue
            try:
                layer_data = archive.read(source)
            except KeyError as error:
                raise ValueError(f"OpenRaster layer is missing: {source}") from error
            loaded = load_layer(source, layer_data)
            values.append(replace(
                loaded,
                name=element.attrib.get("name", loaded.name),
                offset=(int(element.attrib.get("x", 0)), int(element.attrib.get("y", 0))),
                visible=element.attrib.get("visibility", "visible") != "hidden",
                opacity=float(element.attrib.get("opacity", 1.0)),
            ))
    if not values:
        raise ValueError("OpenRaster file contains no image layers")
    return StackDocument.from_layers(
        values,
        canvas_size=(width, height),
        source_format="openraster",
        source_name=source_name,
    )


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError("truncated layered image data")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def i16(self) -> int:
        return struct.unpack(">h", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]


@dataclass(frozen=True, slots=True)
class _PSDChannel:
    channel_id: int
    length: int


@dataclass(frozen=True, slots=True)
class _PSDRecord:
    name: str
    left: int
    top: int
    right: int
    bottom: int
    channels: tuple[_PSDChannel, ...]
    opacity: float
    visible: bool

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


def _pascal_name(data: bytes, offset: int) -> tuple[str, int]:
    if offset >= len(data):
        raise ValueError("truncated PSD layer name")
    length = data[offset]
    start = offset + 1
    end = start + length
    padded_end = (end + 3) & ~3
    if padded_end > len(data):
        raise ValueError("truncated PSD layer name")
    return data[start:end].decode("latin-1", errors="replace"), padded_end


def _psd_extra_name(data: bytes, start: int, end: int, fallback: str) -> str:
    try:
        mask_length = struct.unpack(">I", data[start:start + 4])[0]
        cursor = start + 4 + mask_length
        blending_length = struct.unpack(">I", data[cursor:cursor + 4])[0]
        cursor += 4 + blending_length
        name, cursor = _pascal_name(data, cursor)
        while cursor + 12 <= end:
            signature = data[cursor:cursor + 4]
            key = data[cursor + 4:cursor + 8]
            length = struct.unpack(">I", data[cursor + 8:cursor + 12])[0]
            payload = data[cursor + 12:cursor + 12 + length]
            if key == b"luni" and len(payload) >= 4:
                count = struct.unpack(">I", payload[:4])[0]
                raw = payload[4:4 + count * 2]
                name = raw.decode("utf-16-be", errors="replace") or name
            cursor += 12 + length + (length & 1 if signature == b"8BIM" else 0)
        return name or fallback
    except (IndexError, struct.error, UnicodeError, ValueError):
        return fallback


def _packbits(data: bytes, expected: int) -> bytes:
    output = bytearray()
    cursor = 0
    while cursor < len(data) and len(output) < expected:
        value = struct.unpack("b", data[cursor:cursor + 1])[0]
        cursor += 1
        if 0 <= value <= 127:
            count = value + 1
            output.extend(data[cursor:cursor + count])
            cursor += count
        elif -127 <= value <= -1:
            if cursor >= len(data):
                break
            output.extend(data[cursor:cursor + 1] * (1 - value))
            cursor += 1
    if len(output) != expected:
        raise ValueError("invalid PSD PackBits channel")
    return bytes(output)


def _psd_channel(payload: bytes, width: int, height: int, depth: int) -> tuple[float, ...]:
    if width <= 0 or height <= 0:
        return ()
    bytes_per_sample = depth // 8
    if bytes_per_sample not in (1, 2):
        raise ValueError("PSD supports 8-bit and 16-bit channels only")
    if len(payload) < 2:
        raise ValueError("truncated PSD channel")
    compression = struct.unpack(">H", payload[:2])[0]
    body = payload[2:]
    row_size = width * bytes_per_sample
    if compression == 0:
        raw = body[:row_size * height]
    elif compression == 1:
        table_size = height * 2
        if len(body) < table_size:
            raise ValueError("truncated PSD RLE row table")
        lengths = struct.unpack(f">{height}H", body[:table_size])
        cursor = table_size
        rows = []
        for length in lengths:
            rows.append(_packbits(body[cursor:cursor + length], row_size))
            cursor += length
        raw = b"".join(rows)
    elif compression == 2:
        raw = zlib.decompress(body)
    else:
        raise ValueError("unsupported PSD channel compression")
    if len(raw) != row_size * height:
        raise ValueError("PSD channel dimensions do not match layer bounds")
    if bytes_per_sample == 1:
        return tuple(value / 255.0 for value in raw)
    return tuple(
        struct.unpack(">H", raw[index:index + 2])[0] / 65535.0
        for index in range(0, len(raw), 2)
    )


def _psd_layer_pixels(
    record: _PSDRecord,
    payloads: Mapping[int, bytes],
    depth: int,
    mode: int,
) -> tuple[Color, ...]:
    width = record.width
    height = record.height
    channels = {
        channel_id: _psd_channel(payload, width, height, depth)
        for channel_id, payload in payloads.items()
    }
    alpha = channels.get(-1, ())
    red = channels.get(0, ())
    green = channels.get(1, red)
    blue = channels.get(2, red)
    if mode == 1:
        green = blue = red
    result = []
    for index in range(width * height):
        value = red[index] if index < len(red) else 0.0
        result.append((
            value,
            green[index] if index < len(green) else value,
            blue[index] if index < len(blue) else value,
            alpha[index] if index < len(alpha) else 1.0,
        ))
    return tuple(result)


def decode_psd(data: bytes, *, source_name: str = "") -> StackDocument:
    """Decode common RGB/greyscale PSD layers without external dependencies."""

    reader = _Reader(data)
    if reader.take(4) != b"8BPS":
        raise ValueError("not a PSD file")
    version = reader.u16()
    if version != 1:
        raise ValueError("PSB files are not supported; save the document as PSD")
    reader.take(6)
    channels = reader.u16()
    height = _dimension(reader.u32(), "PSD height")
    width = _dimension(reader.u32(), "PSD width")
    depth = reader.u16()
    mode = reader.u16()
    if mode not in (1, 3):
        raise ValueError("PSD import supports RGB and greyscale documents")
    if channels < 1:
        raise ValueError("PSD contains no color channels")
    color_mode_length = reader.u32()
    reader.take(color_mode_length)
    resources_length = reader.u32()
    reader.take(resources_length)
    layer_mask_length = reader.u32()
    layer_mask = reader.take(layer_mask_length)
    if len(layer_mask) < 4:
        raise ValueError("PSD contains no layer information")
    layer_info_length = struct.unpack(">i", layer_mask[:4])[0]
    if layer_info_length <= 0:
        raise ValueError("PSD contains no editable pixel layers")
    layer_info_end = min(len(layer_mask), 4 + layer_info_length)
    if layer_info_end < 6:
        raise ValueError("invalid PSD layer information")
    cursor = 4
    layer_count = abs(struct.unpack(">h", layer_mask[cursor:cursor + 2])[0])
    cursor += 2
    records: list[_PSDRecord] = []
    channel_sets: list[tuple[_PSDChannel, ...]] = []
    for index in range(layer_count):
        if cursor + 18 > layer_info_end:
            raise ValueError("truncated PSD layer record")
        top, left, bottom, right = struct.unpack(">iiii", layer_mask[cursor:cursor + 16])
        cursor += 16
        channel_count = struct.unpack(">H", layer_mask[cursor:cursor + 2])[0]
        cursor += 2
        layer_channels = []
        for _channel_index in range(channel_count):
            if cursor + 6 > layer_info_end:
                raise ValueError("truncated PSD layer channel record")
            channel_id, length = struct.unpack(">hI", layer_mask[cursor:cursor + 6])
            cursor += 6
            layer_channels.append(_PSDChannel(channel_id, length))
        if cursor + 12 > layer_info_end:
            raise ValueError("truncated PSD layer blend record")
        cursor += 4  # blend signature
        cursor += 4  # blend key
        opacity = layer_mask[cursor] / 255.0
        flags = layer_mask[cursor + 2]
        cursor += 4
        extra_length = struct.unpack(">I", layer_mask[cursor:cursor + 4])[0]
        cursor += 4
        extra_end = cursor + extra_length
        if extra_end > layer_info_end:
            raise ValueError("truncated PSD layer extra data")
        name = _psd_extra_name(layer_mask, cursor, extra_end, f"Layer {index + 1:03d}")
        cursor = extra_end
        records.append(_PSDRecord(name, left, top, right, bottom, tuple(layer_channels), opacity, not bool(flags & 0x02)))
        channel_sets.append(tuple(layer_channels))
    payload_cursor = cursor
    record_payloads: list[dict[int, bytes]] = []
    for layer_channels in channel_sets:
        payloads: dict[int, bytes] = {}
        for channel in layer_channels:
            end = payload_cursor + channel.length
            if end > layer_info_end:
                raise ValueError("truncated PSD layer pixel data")
            payloads[channel.channel_id] = layer_mask[payload_cursor:end]
            payload_cursor = end
        record_payloads.append(payloads)
    values: list[StackLayer] = []
    for record, payloads in zip(reversed(records), reversed(record_payloads)):
        if record.width <= 0 or record.height <= 0 or not payloads or not record.visible:
            continue
        pixels = _psd_layer_pixels(record, payloads, depth, mode)
        values.append(StackLayer(
            name=record.name,
            width=record.width,
            height=record.height,
            pixels=pixels,
            offset=(record.left, record.top),
            visible=record.visible,
            opacity=record.opacity,
        ))
    if not values:
        raise ValueError("PSD contains no visible pixel layers")
    return StackDocument.from_layers(
        values,
        canvas_size=(width, height),
        source_format="psd",
        source_name=source_name,
    )


def stack_to_voxel_asset(
    document: StackDocument,
    *,
    axis: str = "Z",
    layer_order: str = "BOTTOM_TO_TOP",
    alpha_threshold: float = 0.05,
    voxel_scale: float = 1.0,
    palette_limit: int = MAX_PALETTE_COLORS,
) -> VoxelAsset:
    """Rasterize every visible layer into a palette-indexed voxel asset."""

    axis = str(axis or "Z").upper()
    if axis not in STACK_AXIS_ITEMS:
        raise ValueError(f"unsupported stack axis: {axis}")
    layer_order = str(layer_order or "BOTTOM_TO_TOP").upper()
    if layer_order not in STACK_ORDER_ITEMS:
        raise ValueError(f"unsupported stack order: {layer_order}")
    alpha_threshold = float(alpha_threshold)
    voxel_scale = float(voxel_scale)
    palette_limit = int(palette_limit)
    if not 0.0 <= alpha_threshold <= 1.0:
        raise ValueError("stack alpha threshold must be in [0, 1]")
    if not math.isfinite(voxel_scale) or voxel_scale <= 0.0:
        raise ValueError("stack voxel scale must be positive and finite")
    if not 1 <= palette_limit <= MAX_PALETTE_COLORS:
        raise ValueError(f"stack palette limit must be between 1 and {MAX_PALETTE_COLORS}")
    palette: list[Color] = []
    palette_lookup: dict[Color, int] = {}
    voxels: dict[tuple[int, int, int], int] = {}
    depth_count = len(document.layers)
    visible_layers = [layer for layer in document.layers if layer.visible and layer.opacity > 0.0]
    for layer_index, layer in enumerate(document.layers):
        if not layer.visible or layer.opacity <= 0.0:
            continue
        depth_index = layer_index if layer_order == "BOTTOM_TO_TOP" else depth_count - 1 - layer_index
        depth = depth_index - depth_count // 2
        for y in range(layer.height):
            canvas_y = y + layer.offset[1]
            if not 0 <= canvas_y < document.height:
                continue
            for x in range(layer.width):
                canvas_x = x + layer.offset[0]
                if not 0 <= canvas_x < document.width:
                    continue
                red, green, blue, alpha = layer.pixel(x, y)
                alpha *= layer.opacity
                if alpha <= alpha_threshold:
                    continue
                color = (red, green, blue, alpha)
                if color not in palette_lookup:
                    if len(palette) < palette_limit:
                        palette_lookup[color] = len(palette)
                        palette.append(color)
                    else:
                        palette_lookup[color] = nearest_palette_index(color, palette)
                palette_index = palette_lookup[color]
                u = canvas_x - document.width // 2
                v = document.height - 1 - canvas_y - document.height // 2
                if axis == "Z":
                    cell = (u, v, depth)
                elif axis == "Y":
                    cell = (u, depth, v)
                else:
                    cell = (depth, u, v)
                voxels[cell] = palette_index
    if not palette:
        palette = [(0.8, 0.8, 0.8, 1.0)]
    return VoxelAsset(
        grid_size=voxel_scale,
        palette=palette,
        voxels=voxels,
        provenance={
            "workflow": "layered_stack_import",
            "source_format": document.source_format,
            "source_name": document.source_name,
            "layer_count": str(len(visible_layers)),
            "axis": axis,
        },
    )


__all__ = [
    "MAX_STACK_DOCUMENT_BYTES",
    "MAX_STACK_DIMENSION",
    "MAX_STACK_LAYERS",
    "STACK_AXIS_ITEMS",
    "STACK_FORMAT",
    "STACK_ORDER_ITEMS",
    "STACK_PROPERTY",
    "STACK_SCHEMA_VERSION",
    "STACK_SOURCE_PROPERTY",
    "StackDocument",
    "StackLayer",
    "decode_openraster",
    "decode_psd",
    "decode_stack_manifest",
    "stack_to_voxel_asset",
]
