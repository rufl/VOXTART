# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Versioned sparse voxel data model used by the Blender adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
Cell = tuple[int, int, int]
Color = tuple[float, float, float, float]


def _cell(value: Iterable[int]) -> Cell:
    result = tuple(value)
    if len(result) != 3 or any(isinstance(item, bool) or not isinstance(item, int) for item in result):
        raise ValueError(f"voxel coordinates must be three integers: {value!r}")
    return result  # type: ignore[return-value]


def _color(value: Iterable[float]) -> Color:
    result = tuple(float(item) for item in value)
    if len(result) != 4 or any(item < 0.0 or item > 1.0 for item in result):
        raise ValueError(f"palette colors must be four values in [0, 1]: {value!r}")
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
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError("socket rotation must be a right-angle quarter turn")
        if not self.tag:
            raise ValueError("socket tag must not be empty")
        object.__setattr__(self, "position", position)
        object.__setattr__(self, "normal", normal)

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
    sockets: list[VoxelSocket] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported voxel schema: {self.schema_version}")
        if self.grid_size <= 0.0:
            raise ValueError("grid_size must be positive")
        self.palette = [_color(color) for color in self.palette]
        if not self.palette:
            raise ValueError("voxel assets require at least one palette entry")
        self.voxels = {_cell(cell): self._palette_index(index) for cell, index in self.voxels.items()}
        self.provenance = {str(key): str(value) for key, value in self.provenance.items()}

    def _palette_index(self, index: int) -> int:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.palette):
            raise ValueError(f"palette index out of range: {index!r}")
        return index

    def add_palette_color(self, color: Iterable[float]) -> int:
        """Append a palette entry and return its stable palette index."""

        normalized = _color(color)
        self.palette.append(normalized)
        return len(self.palette) - 1
    def set_voxel(self, cell: Iterable[int], palette_index: int = 0) -> None:
        self.voxels[_cell(cell)] = self._palette_index(palette_index)

    def remove_voxel(self, cell: Iterable[int]) -> None:
        self.voxels.pop(_cell(cell), None)

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
            "voxels": [[*cell, palette_index] for cell, palette_index in self.iter_voxels()],
            "sockets": [socket.to_dict() for socket in self.sockets],
            "provenance": dict(sorted(self.provenance.items())),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VoxelAsset":
        if int(data.get("schema_version", -1)) != SCHEMA_VERSION:
            raise ValueError("unsupported or missing voxel schema version")
        voxels: dict[Cell, int] = {}
        for item in data.get("voxels", []):
            if not isinstance(item, (list, tuple)) or len(item) != 4:
                raise ValueError(f"invalid voxel entry: {item!r}")
            voxels[_cell(item[:3])] = int(item[3])
        sockets = []
        for item in data.get("sockets", []):
            sockets.append(VoxelSocket(
                position=item["position"],
                normal=item["normal"],
                tag=str(item.get("tag", "default")),
                rotation=int(item.get("rotation", 0)),
            ))
        return cls(
            grid_size=float(data["grid_size"]),
            palette=data["palette"],
            voxels=voxels,
            sockets=sockets,
            provenance=data.get("provenance", {}),
            schema_version=SCHEMA_VERSION,
        )
