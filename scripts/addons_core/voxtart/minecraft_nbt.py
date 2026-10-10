# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Dependency-free Named Binary Tag reader and writer for Minecraft files."""

from __future__ import annotations

import gzip
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Mapping


TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12
MAX_NBT_ITEMS = 16_000_000
MAX_NBT_DEPTH = 512


@dataclass(frozen=True, slots=True)
class NBTList:
    element_type: int
    values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class NBTTag:
    name: str
    tag_type: int
    value: Any


def nbt_byte(name: str, value: int) -> NBTTag:
    return NBTTag(str(name), TAG_BYTE, int(value))


def nbt_short(name: str, value: int) -> NBTTag:
    return NBTTag(str(name), TAG_SHORT, int(value))


def nbt_int(name: str, value: int) -> NBTTag:
    return NBTTag(str(name), TAG_INT, int(value))


def nbt_long(name: str, value: int) -> NBTTag:
    return NBTTag(str(name), TAG_LONG, int(value))


def nbt_float(name: str, value: float) -> NBTTag:
    return NBTTag(str(name), TAG_FLOAT, float(value))


def nbt_double(name: str, value: float) -> NBTTag:
    return NBTTag(str(name), TAG_DOUBLE, float(value))


def nbt_byte_array(name: str, values) -> NBTTag:
    return NBTTag(str(name), TAG_BYTE_ARRAY, tuple(int(value) for value in values))


def nbt_string(name: str, value: str) -> NBTTag:
    return NBTTag(str(name), TAG_STRING, str(value))


def nbt_list(name: str, element_type: int, values) -> NBTTag:
    return NBTTag(str(name), TAG_LIST, NBTList(int(element_type), tuple(values)))


def nbt_compound(name: str, fields: Mapping[str, NBTTag] | None = None) -> NBTTag:
    fields = fields or {}
    if any(not isinstance(value, NBTTag) for value in fields.values()):
        raise TypeError("NBT compound values must be NBTTag instances")
    return NBTTag(str(name), TAG_COMPOUND, dict(fields))


def nbt_int_array(name: str, values) -> NBTTag:
    return NBTTag(str(name), TAG_INT_ARRAY, tuple(int(value) for value in values))


def nbt_long_array(name: str, values) -> NBTTag:
    return NBTTag(str(name), TAG_LONG_ARRAY, tuple(int(value) for value in values))


def compound_fields(tag: NBTTag) -> dict[str, NBTTag]:
    if not isinstance(tag, NBTTag) or tag.tag_type != TAG_COMPOUND or not isinstance(tag.value, dict):
        raise ValueError("NBT tag is not a compound")
    return tag.value


def field(tag: NBTTag, name: str, default: NBTTag | None = None) -> NBTTag | None:
    return compound_fields(tag).get(str(name), default)


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError("truncated NBT data")
        value = self.data[self.offset:self.offset + size]
        self.offset += size
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def i8(self) -> int:
        return struct.unpack(">b", self.take(1))[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def i16(self) -> int:
        return struct.unpack(">h", self.take(2))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack(">q", self.take(8))[0]

    def f32(self) -> float:
        return struct.unpack(">f", self.take(4))[0]

    def f64(self) -> float:
        return struct.unpack(">d", self.take(8))[0]

    def string(self) -> str:
        size = self.u16()
        return self.take(size).decode("utf-8")


def _check_count(count: int, field_name: str) -> int:
    if count < 0 or count > MAX_NBT_ITEMS:
        raise ValueError(f"NBT {field_name} count is outside the safety limit")
    return count


def _read_payload(reader: _Reader, tag_type: int, depth: int) -> Any:
    if depth > MAX_NBT_DEPTH:
        raise ValueError("NBT nesting exceeds the safety limit")
    if tag_type == TAG_BYTE:
        return reader.i8()
    if tag_type == TAG_SHORT:
        return reader.i16()
    if tag_type == TAG_INT:
        return reader.i32()
    if tag_type == TAG_LONG:
        return reader.i64()
    if tag_type == TAG_FLOAT:
        return reader.f32()
    if tag_type == TAG_DOUBLE:
        return reader.f64()
    if tag_type == TAG_BYTE_ARRAY:
        return tuple(reader.i8() for _ in range(_check_count(reader.i32(), "byte array")))
    if tag_type == TAG_STRING:
        return reader.string()
    if tag_type == TAG_LIST:
        element_type = reader.u8()
        count = _check_count(reader.i32(), "list")
        return NBTList(element_type, tuple(_read_payload(reader, element_type, depth + 1) for _ in range(count)))
    if tag_type == TAG_COMPOUND:
        values: dict[str, NBTTag] = {}
        while True:
            child_type = reader.u8()
            if child_type == TAG_END:
                break
            child_name = reader.string()
            values[child_name] = NBTTag(child_name, child_type, _read_payload(reader, child_type, depth + 1))
        return values
    if tag_type == TAG_INT_ARRAY:
        return tuple(reader.i32() for _ in range(_check_count(reader.i32(), "int array")))
    if tag_type == TAG_LONG_ARRAY:
        return tuple(reader.i64() for _ in range(_check_count(reader.i32(), "long array")))
    raise ValueError(f"unsupported NBT tag type: {tag_type}")


def _read_named(reader: _Reader, depth: int = 0) -> NBTTag:
    tag_type = reader.u8()
    if tag_type == TAG_END:
        return NBTTag("", TAG_END, None)
    name = reader.string()
    return NBTTag(name, tag_type, _read_payload(reader, tag_type, depth + 1))


def _decompress(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError as error:
            raise ValueError("invalid gzip-compressed NBT") from error
    try:
        return zlib.decompress(data)
    except zlib.error:
        return data


def decode_nbt(data: bytes, *, compression: str = "auto") -> NBTTag:
    """Decode a gzip, zlib, or raw NBT compound."""

    data = bytes(data)
    compression = str(compression or "auto").lower()
    if compression == "gzip":
        data = gzip.decompress(data)
    elif compression == "zlib":
        data = zlib.decompress(data)
    elif compression == "auto":
        data = _decompress(data)
    elif compression != "raw":
        raise ValueError(f"unsupported NBT compression: {compression}")
    tag = _read_named(_Reader(data))
    if tag.tag_type != TAG_COMPOUND:
        raise ValueError("NBT root must be a compound")
    return tag


def _write_string(value: str) -> bytes:
    encoded = str(value).encode("utf-8")
    if len(encoded) > 65535:
        raise ValueError("NBT strings may contain at most 65535 encoded bytes")
    return struct.pack(">H", len(encoded)) + encoded


def _signed_byte(value: int) -> int:
    value = int(value) & 0xFF
    return value if value < 128 else value - 256


def _write_payload(tag_type: int, value: Any, depth: int = 0) -> bytes:
    if depth > MAX_NBT_DEPTH:
        raise ValueError("NBT nesting exceeds the safety limit")
    if tag_type == TAG_BYTE:
        return struct.pack(">b", int(value))
    if tag_type == TAG_SHORT:
        return struct.pack(">h", int(value))
    if tag_type == TAG_INT:
        return struct.pack(">i", int(value))
    if tag_type == TAG_LONG:
        return struct.pack(">q", int(value))
    if tag_type == TAG_FLOAT:
        return struct.pack(">f", float(value))
    if tag_type == TAG_DOUBLE:
        return struct.pack(">d", float(value))
    if tag_type == TAG_BYTE_ARRAY:
        values = tuple(_signed_byte(value) for value in value)
        return struct.pack(">i", len(values)) + bytes(struct.pack(">b", item)[0] for item in values)
    if tag_type == TAG_STRING:
        return _write_string(value)
    if tag_type == TAG_LIST:
        if not isinstance(value, NBTList):
            raise TypeError("NBT list values must be NBTList instances")
        values = tuple(value.values)
        return bytes((value.element_type,)) + struct.pack(">i", len(values)) + b"".join(
            _write_payload(value.element_type, item, depth + 1) for item in values
        )
    if tag_type == TAG_COMPOUND:
        if not isinstance(value, Mapping):
            raise TypeError("NBT compound values must be mappings")
        return b"".join(_write_named(child) for child in value.values()) + bytes((TAG_END,))
    if tag_type == TAG_INT_ARRAY:
        values = tuple(int(item) for item in value)
        return struct.pack(">i", len(values)) + b"".join(struct.pack(">i", item) for item in values)
    if tag_type == TAG_LONG_ARRAY:
        values = tuple(int(item) for item in value)
        return struct.pack(">i", len(values)) + b"".join(struct.pack(">q", item) for item in values)
    raise ValueError(f"unsupported NBT tag type: {tag_type}")


def _write_named(tag: NBTTag) -> bytes:
    if tag.tag_type == TAG_END:
        return bytes((TAG_END,))
    return bytes((tag.tag_type,)) + _write_string(tag.name) + _write_payload(tag.tag_type, tag.value)


def encode_nbt(tag: NBTTag, *, compression: str = "gzip") -> bytes:
    """Encode an NBT compound as gzip, zlib, or raw bytes."""

    if not isinstance(tag, NBTTag) or tag.tag_type != TAG_COMPOUND:
        raise TypeError("NBT root must be an NBTTag compound")
    raw = _write_named(tag)
    compression = str(compression or "gzip").lower()
    if compression == "gzip":
        return gzip.compress(raw, mtime=0)
    if compression == "zlib":
        return zlib.compress(raw)
    if compression == "raw":
        return raw
    raise ValueError(f"unsupported NBT compression: {compression}")


__all__ = [
    "MAX_NBT_DEPTH",
    "MAX_NBT_ITEMS",
    "NBTList",
    "NBTTag",
    "TAG_BYTE",
    "TAG_BYTE_ARRAY",
    "TAG_COMPOUND",
    "TAG_DOUBLE",
    "TAG_END",
    "TAG_FLOAT",
    "TAG_INT",
    "TAG_INT_ARRAY",
    "TAG_LIST",
    "TAG_LONG",
    "TAG_LONG_ARRAY",
    "TAG_SHORT",
    "TAG_STRING",
    "compound_fields",
    "decode_nbt",
    "encode_nbt",
    "field",
    "nbt_byte",
    "nbt_byte_array",
    "nbt_compound",
    "nbt_double",
    "nbt_float",
    "nbt_int",
    "nbt_int_array",
    "nbt_list",
    "nbt_long",
    "nbt_long_array",
    "nbt_short",
    "nbt_string",
]
