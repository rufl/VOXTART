# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Versioned sparse voxel data model used by the Blender adapter."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from .provenance import sha256_json


SCHEMA_VERSION = 1
Cell = tuple[int, int, int]
Color = tuple[float, float, float, float]

PALETTE_MATERIALS_KEY = "palette_materials"
PALETTE_MATERIAL_KINDS = ("DIFFUSE", "METAL", "GLASS", "EMISSIVE", "CUSTOM")


def _finite_range(value: Any, name: str, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite number in [{minimum}, {maximum}]") from error
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be a finite number in [{minimum}, {maximum}]")
    return result


@dataclass(frozen=True, slots=True)
class PaletteMaterial:
    """Per-palette physically based material controls."""

    kind: str = "DIFFUSE"
    alpha: float = 1.0
    metallic: float = 0.0
    roughness: float = 0.5
    transmission: float = 0.0
    emission_strength: float = 0.0
    ior: float = 1.45

    def __post_init__(self) -> None:
        kind = str(self.kind).upper()
        if kind not in PALETTE_MATERIAL_KINDS:
            raise ValueError(f"unsupported palette material kind: {self.kind!r}")
        object.__setattr__(self, "kind", kind)
        for name, minimum, maximum in (
            ("alpha", 0.0, 1.0),
            ("metallic", 0.0, 1.0),
            ("roughness", 0.0, 1.0),
            ("transmission", 0.0, 1.0),
            ("emission_strength", 0.0, 1000.0),
            ("ior", 1.0, 3.0),
        ):
            object.__setattr__(
                self,
                name,
                _finite_range(getattr(self, name), name, minimum, maximum),
            )

    @classmethod
    def from_preset(cls, kind: str) -> "PaletteMaterial":
        normalized = str(kind).upper()
        if normalized == "DIFFUSE":
            return cls()
        if normalized == "METAL":
            return cls(kind=normalized, metallic=1.0, roughness=0.22)
        if normalized == "GLASS":
            return cls(
                kind=normalized,
                alpha=0.35,
                roughness=0.05,
                transmission=1.0,
                ior=1.45,
            )
        if normalized == "EMISSIVE":
            return cls(kind=normalized, emission_strength=5.0)
        if normalized == "CUSTOM":
            return cls(kind=normalized)
        raise ValueError(f"unsupported palette material preset: {kind!r}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PaletteMaterial":
        if not isinstance(data, Mapping):
            raise ValueError(f"palette material must be an object: {data!r}")
        return cls(
            kind=data.get("kind", "DIFFUSE"),
            alpha=data.get("alpha", 1.0),
            metallic=data.get("metallic", 0.0),
            roughness=data.get("roughness", 0.5),
            transmission=data.get("transmission", 0.0),
            emission_strength=data.get("emission_strength", 0.0),
            ior=data.get("ior", 1.45),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "alpha": self.alpha,
            "metallic": self.metallic,
            "roughness": self.roughness,
            "transmission": self.transmission,
            "emission_strength": self.emission_strength,
            "ior": self.ior,
        }


def _normalize_palette_materials(value: Any, count: int) -> list[PaletteMaterial]:
    if value is None:
        raw = ()
    elif isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("palette materials must be valid JSON") from error
    else:
        raw = value
    if not isinstance(raw, (list, tuple)):
        raise ValueError("palette materials must be a list")
    materials = [
        item if isinstance(item, PaletteMaterial) else PaletteMaterial.from_dict(item)
        for item in raw
    ]
    if len(materials) > count:
        raise ValueError("palette materials cannot exceed the palette size")
    materials.extend(PaletteMaterial() for _ in range(count - len(materials)))
    return materials


def remap_palette_materials(
    materials: Iterable[PaletteMaterial],
    target_count: int,
    index_map: Mapping[int, int] | None = None,
) -> list[PaletteMaterial]:
    if isinstance(target_count, bool) or not isinstance(target_count, int) or target_count < 0:
        raise ValueError("target palette material count must be a non-negative integer")
    source = list(materials)
    target = [PaletteMaterial() for _ in range(target_count)]
    if index_map is None:
        for index in range(min(len(source), target_count)):
            target[index] = source[index]
        return target
    for source_index, target_index in index_map.items():
        if (
            isinstance(source_index, bool)
            or not isinstance(source_index, int)
            or isinstance(target_index, bool)
            or not isinstance(target_index, int)
            or not 0 <= source_index < len(source)
            or not 0 <= target_index < target_count
        ):
            continue
        target[target_index] = source[source_index]
    return target


def _cell(value: Iterable[int]) -> Cell:
    result = tuple(value)
    if len(result) != 3 or any(isinstance(item, bool) or not isinstance(item, int) for item in result):
        raise ValueError(f"voxel coordinates must be three integers: {value!r}")
    return result  # type: ignore[return-value]


def _color(value: Iterable[float]) -> Color:
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"palette colors must be four finite values in [0, 1]: {value!r}") from error
    if len(result) != 4 or any(
        not math.isfinite(item) or item < 0.0 or item > 1.0
        for item in result
    ):
        raise ValueError(f"palette colors must be four finite values in [0, 1]: {value!r}")
    return result  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class VoxelSocket:
    """A grid-aligned attachment point for kitbashing."""

    position: Cell
    normal: Cell
    tag: str = "default"
    rotation: int = 0

    def __post_init__(self) -> None:
        position = _cell(self.position)
        normal = _cell(self.normal)
        if sum(abs(axis) for axis in normal) != 1:
            raise ValueError("socket normal must be one of the six axis directions")
        if isinstance(self.rotation, bool) or not isinstance(self.rotation, int) or self.rotation not in (0, 90, 180, 270):
            raise ValueError("socket rotation must be a right-angle quarter turn")
        if not isinstance(self.tag, str) or not self.tag.strip():
            raise ValueError("socket tag must be a non-empty string")
        object.__setattr__(self, "position", position)
        object.__setattr__(self, "normal", normal)
        object.__setattr__(self, "tag", self.tag.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": list(self.position),
            "normal": list(self.normal),
            "tag": self.tag,
            "rotation": self.rotation,
        }


@dataclass(slots=True)
class VoxelAsset:
    """Sparse, palette-indexed voxel asset with stable serialization."""

    grid_size: float = 1.0
    palette: list[Color] = field(default_factory=lambda: [(0.8, 0.8, 0.8, 1.0)])
    voxels: dict[Cell, int] = field(default_factory=dict)
    part_assignments: dict[Cell, str] = field(default_factory=dict)
    sockets: list[VoxelSocket] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    palette_materials: list[PaletteMaterial] | None = None

    @staticmethod
    def _normalize_part_assignments(value: Any) -> dict[Cell, str]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            records = value.items()
        else:
            records = []
            for item in value:
                if not isinstance(item, (list, tuple)):
                    raise ValueError(f"invalid voxel part assignment: {item!r}")
                if len(item) == 4:
                    records.append((item[:3], item[3]))
                elif len(item) == 2:
                    records.append((item[0], item[1]))
                else:
                    raise ValueError(f"invalid voxel part assignment: {item!r}")
        normalized: dict[Cell, str] = {}
        for cell, label in records:
            normalized_cell = _cell(cell)
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f"voxel part labels must be non-empty strings: {label!r}")
            normalized[normalized_cell] = label.strip()
        return normalized

    @staticmethod
    def _part_payload(assignments: Mapping[Cell, str]) -> str:
        return json.dumps(
            [[list(cell), label] for cell, label in sorted(assignments.items())],
            sort_keys=True,
            separators=(",", ":"),
        )

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported voxel schema: {self.schema_version}")
        try:
            grid_size = float(self.grid_size)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("grid_size must be a finite positive number") from error
        if not math.isfinite(grid_size) or grid_size <= 0.0:
            raise ValueError("grid_size must be a finite positive number")
        self.palette = [_color(color) for color in self.palette]
        if not self.palette:
            raise ValueError("voxel assets require at least one palette entry")
        self.voxels = {_cell(cell): self._palette_index(index) for cell, index in self.voxels.items()}
        self.palette_materials = _normalize_palette_materials(self.palette_materials, len(self.palette))
        provenance = {str(key): str(value) for key, value in self.provenance.items()}
        assignments = self._normalize_part_assignments(self.part_assignments)
        encoded_assignments = provenance.get("part_assignments")
        if not assignments and encoded_assignments:
            try:
                assignments = self._normalize_part_assignments(json.loads(encoded_assignments))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError("provenance part_assignments must be valid JSON") from error
        self.part_assignments = {
            cell: label for cell, label in assignments.items() if cell in self.voxels
        }
        if self.part_assignments:
            provenance["part_assignments"] = self._part_payload(self.part_assignments)
        else:
            provenance.pop("part_assignments", None)
        self.provenance = provenance

    def _palette_index(self, index: int) -> int:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.palette):
            raise ValueError(f"palette index out of range: {index!r}")
        return index

    def get_palette_materials(self) -> list[PaletteMaterial]:
        return list(self.palette_materials or ())

    def set_palette_materials(self, materials: Iterable[PaletteMaterial | Mapping[str, Any]]) -> None:
        self.palette_materials = _normalize_palette_materials(list(materials), len(self.palette))

    def set_palette_material(self, index: int, material: PaletteMaterial | Mapping[str, Any]) -> None:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.palette):
            raise IndexError(f"palette material index out of range: {index!r}")
        materials = self.get_palette_materials()
        materials[index] = (
            material if isinstance(material, PaletteMaterial) else PaletteMaterial.from_dict(material)
        )
        self.set_palette_materials(materials)

    def add_palette_color(self, color: Iterable[float]) -> int:
        """Append a palette entry and return its stable palette index."""

        normalized = _color(color)
        self.palette.append(normalized)
        self.palette_materials = _normalize_palette_materials(self.palette_materials, len(self.palette))
        return len(self.palette) - 1

    def set_voxel(self, cell: Iterable[int], palette_index: int = 0) -> None:
        self.voxels[_cell(cell)] = self._palette_index(palette_index)

    def set_part(self, cell: Iterable[int], label: str) -> None:
        """Assign a semantic part label to an occupied voxel."""

        normalized_cell = _cell(cell)
        if normalized_cell not in self.voxels:
            raise KeyError(f"cannot assign a part to an unoccupied voxel: {normalized_cell!r}")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("voxel part labels must be non-empty strings")
        self.part_assignments[normalized_cell] = label.strip()
        self.provenance["part_assignments"] = self._part_payload(self.part_assignments)

    def remove_part(self, cell: Iterable[int]) -> None:
        normalized_cell = _cell(cell)
        self.part_assignments.pop(normalized_cell, None)
        if self.part_assignments:
            self.provenance["part_assignments"] = self._part_payload(self.part_assignments)
        else:
            self.provenance.pop("part_assignments", None)

    def remove_voxel(self, cell: Iterable[int]) -> None:
        normalized_cell = _cell(cell)
        self.voxels.pop(normalized_cell, None)
        self.remove_part(normalized_cell)

    def get_voxel(self, cell: Iterable[int]) -> int | None:
        return self.voxels.get(_cell(cell))

    def iter_voxels(self):
        for cell in sorted(self.voxels):
            yield cell, self.voxels[cell]

    @property
    def bounds(self) -> tuple[Cell, Cell] | None:
        if not self.voxels:
            return None
        cells = tuple(self.voxels)
        minimum = tuple(min(cell[axis] for cell in cells) for axis in range(3))
        maximum = tuple(max(cell[axis] for cell in cells) for axis in range(3))
        return minimum, maximum  # type: ignore[return-value]

    @classmethod
    def box(cls, size: int, *, grid_size: float = 1.0, palette_index: int = 0) -> "VoxelAsset":
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError("box size must be a positive integer")
        asset = cls(grid_size=grid_size)
        asset._palette_index(palette_index)
        offset = -(size // 2)
        for x in range(size):
            for y in range(size):
                for z in range(size):
                    asset.voxels[(offset + x, offset + y, offset + z)] = palette_index
        return asset

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "grid_size": self.grid_size,
            "palette": [list(color) for color in self.palette],
            "palette_materials": [
                material.to_dict() for material in self.get_palette_materials()
            ],
            "voxels": [[*cell, palette_index] for cell, palette_index in self.iter_voxels()],
            "part_assignments": [
                [*cell, label] for cell, label in sorted(self.part_assignments.items())
            ],
            "sockets": [socket.to_dict() for socket in self.sockets],
            "provenance": dict(sorted(self.provenance.items())),
        }

    @property
    def content_digest(self) -> str:
        """Return the canonical digest of the authoritative asset payload."""

        return sha256_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VoxelAsset":
        schema_version = data.get("schema_version", -1)
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported or missing voxel schema version")
        voxels: dict[Cell, int] = {}
        for item in data.get("voxels", []):
            if not isinstance(item, (list, tuple)) or len(item) != 4:
                raise ValueError(f"invalid voxel entry: {item!r}")
            palette_index = item[3]
            if isinstance(palette_index, bool) or not isinstance(palette_index, int):
                raise ValueError(f"voxel palette index must be an integer: {palette_index!r}")
            voxels[_cell(item[:3])] = palette_index
        provenance = dict(data.get("provenance", {}))
        sockets = []
        for item in data.get("sockets", []):
            sockets.append(VoxelSocket(
                position=item["position"],
                normal=item["normal"],
                tag=str(item.get("tag", "default")),
                rotation=item.get("rotation", 0),
            ))
        return cls(
            grid_size=data["grid_size"],
            palette=data["palette"],
            palette_materials=data.get("palette_materials"),
            voxels=voxels,
            part_assignments=data.get("part_assignments"),
            sockets=sockets,
            provenance=provenance,
            schema_version=SCHEMA_VERSION,
        )
