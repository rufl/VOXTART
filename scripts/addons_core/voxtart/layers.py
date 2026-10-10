# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Non-destructive voxel layer stacks and compositing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .palette import nearest_palette_index, remap_voxels_to_palette
from .voxel import Cell, Color, VoxelAsset


LAYER_SCHEMA_VERSION = 1
LAYER_STACK_PROPERTY = "voxtart_layers_json"
MAX_LAYERS = 128
BLEND_MODES = ("NORMAL", "ADD", "SUBTRACT", "MULTIPLY", "SCREEN")
LAYER_SURFACE_BACKENDS = ("INHERIT", "GREEDY", "MARCHING")



def _cell(value: Iterable[int]) -> Cell:
    result = tuple(value)
    if len(result) != 3 or any(isinstance(item, bool) or not isinstance(item, int) for item in result):
        raise ValueError(f"invalid layer cell: {value!r}")
    return result  # type: ignore[return-value]


def _color(value: Iterable[float]) -> Color:
    result = tuple(float(item) for item in value)
    if len(result) != 4:
        raise ValueError(f"invalid layer color: {value!r}")
    return result  # type: ignore[return-value]


@dataclass(slots=True)
class Layer:
    name: str = "Layer"
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    blend_mode: str = "NORMAL"
    voxels: dict[Cell, int] = field(default_factory=dict)
    surface_backend: str = "INHERIT"

    def __post_init__(self) -> None:
        self.name = str(self.name).strip()[:128] or "Layer"
        self.visible = bool(self.visible)
        self.locked = bool(self.locked)
        self.opacity = max(0.0, min(1.0, float(self.opacity)))
        self.blend_mode = str(self.blend_mode).upper()
        if self.blend_mode not in BLEND_MODES:
            raise ValueError(f"unsupported layer blend mode: {self.blend_mode!r}")
        self.surface_backend = str(self.surface_backend).upper()
        if self.surface_backend not in LAYER_SURFACE_BACKENDS:
            raise ValueError(f"unsupported layer surface backend: {self.surface_backend!r}")
        self.voxels = {_cell(cell): self._index(index) for cell, index in self.voxels.items()}

    @staticmethod
    def _index(index: int) -> int:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError(f"invalid layer palette index: {index!r}")
        return index

    def copy(self, *, name: str | None = None) -> "Layer":
        return Layer(
            name=name if name is not None else self.name,
            visible=self.visible,
            locked=self.locked,
            opacity=self.opacity,
            blend_mode=self.blend_mode,
            voxels=dict(self.voxels),
            surface_backend=self.surface_backend,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "visible": self.visible,
            "locked": self.locked,
            "opacity": self.opacity,
            "blend_mode": self.blend_mode,
            "surface_backend": self.surface_backend,
            "voxels": [[*cell, index] for cell, index in sorted(self.voxels.items())],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Layer":
        voxels: dict[Cell, int] = {}
        for entry in data.get("voxels", []):
            if not isinstance(entry, (list, tuple)) or len(entry) != 4:
                raise ValueError("layer voxel entries must contain x, y, z, and palette index")
            voxels[_cell(entry[:3])] = cls._index(entry[3])
        return cls(
            name=data.get("name", "Layer"),
            visible=data.get("visible", True),
            locked=data.get("locked", False),
            opacity=data.get("opacity", 1.0),
            blend_mode=data.get("blend_mode", "NORMAL"),
            voxels=voxels,
            surface_backend=data.get("surface_backend", "INHERIT"),
        )


@dataclass(slots=True)
class LayerStack:
    layers: list[Layer] = field(default_factory=list)
    active_index: int = 0
    schema_version: int = LAYER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LAYER_SCHEMA_VERSION:
            raise ValueError(f"unsupported layer schema: {self.schema_version}")
        if not self.layers:
            self.layers = [Layer(name="Base")]
        if len(self.layers) > MAX_LAYERS:
            raise ValueError(f"layer stacks may contain at most {MAX_LAYERS} layers")
        self.active_index = max(0, min(len(self.layers) - 1, int(self.active_index)))

    @classmethod
    def from_asset(cls, asset: VoxelAsset) -> "LayerStack":
        return cls(layers=[Layer(name="Base", voxels=dict(asset.voxels))], active_index=0)

    @property
    def active(self) -> Layer:
        return self.layers[self.active_index]

    def set_active(self, index: int) -> Layer:
        if not 0 <= int(index) < len(self.layers):
            raise IndexError("layer index out of range")
        self.active_index = int(index)
        return self.active

    def add(self, name: str = "Layer") -> Layer:
        if len(self.layers) >= MAX_LAYERS:
            raise ValueError(f"layer stacks may contain at most {MAX_LAYERS} layers")
        layer = Layer(name=name)
        self.layers.insert(self.active_index + 1, layer)
        self.active_index += 1
        return layer

    def duplicate_active(self) -> Layer:
        if len(self.layers) >= MAX_LAYERS:
            raise ValueError(f"layer stacks may contain at most {MAX_LAYERS} layers")
        duplicate = self.active.copy(name=f"{self.active.name} Copy")
        self.layers.insert(self.active_index + 1, duplicate)
        self.active_index += 1
        return duplicate

    def remove_active(self) -> None:
        if len(self.layers) == 1:
            raise ValueError("the last layer cannot be deleted")
        self.layers.pop(self.active_index)
        self.active_index = min(self.active_index, len(self.layers) - 1)

    def move_active(self, delta: int) -> None:
        target = self.active_index + int(delta)
        if not 0 <= target < len(self.layers):
            return
        layer = self.layers.pop(self.active_index)
        self.layers.insert(target, layer)
        self.active_index = target

    def merge_down(self, palette: Iterable[Color]) -> None:
        if self.active_index == 0:
            raise ValueError("the bottom layer cannot be merged down")
        lower_index = self.active_index - 1
        lower = self.layers[lower_index]
        upper = self.active
        merged = LayerStack(layers=[lower.copy(), upper.copy()], active_index=1).composite(palette)
        lower.voxels = merged
        lower.name = f"{lower.name} + {upper.name}"[:128]
        lower.visible = True
        lower.locked = lower.locked or upper.locked
        lower.opacity = 1.0
        lower.blend_mode = "NORMAL"
        if upper.surface_backend != "INHERIT":
            lower.surface_backend = upper.surface_backend
        self.layers.pop(self.active_index)
        self.active_index = lower_index

    def flatten(self, palette: Iterable[Color], name: str = "Flattened") -> None:
        surface_backend = self.active.surface_backend
        self.layers = [Layer(name=name, voxels=self.composite(palette), surface_backend=surface_backend)]
        self.active_index = 0

    def remap_palette(self, source_palette: Iterable[Color], target_palette: Iterable[Color]) -> None:
        source = tuple(source_palette)
        target = tuple(target_palette)
        for layer in self.layers:
            layer.voxels = remap_voxels_to_palette(layer.voxels, source, target)

    def remap_palette_indices(self, index_map: Mapping[int, int]) -> None:
        """Remap palette indices exactly, preserving duplicate color slots."""
        mapping = {int(source): int(target) for source, target in index_map.items()}
        for layer in self.layers:
            remapped: dict[Cell, int] = {}
            for cell, palette_index in layer.voxels.items():
                if palette_index not in mapping:
                    raise ValueError(f"palette index is missing from remap: {palette_index}")
                target = mapping[palette_index]
                if target < 0:
                    raise ValueError(f"palette remap target must be non-negative: {target}")
                remapped[cell] = target
            layer.voxels = remapped

    def composite(self, palette: Iterable[Color]) -> dict[Cell, int]:
        colors = tuple(_color(color) for color in palette)
        if not colors:
            raise ValueError("cannot composite without a palette")
        working: dict[Cell, Color] = {}
        for layer in self.layers:
            if not layer.visible or layer.opacity <= 0.0:
                continue
            for cell, palette_index in layer.voxels.items():
                if not 0 <= palette_index < len(colors):
                    raise ValueError(f"layer palette index out of range: {palette_index}")
                source = colors[palette_index]
                source_alpha = source[3] * layer.opacity
                if source_alpha <= 0.0:
                    continue
                destination = working.get(cell, (0.0, 0.0, 0.0, 0.0))
                working[cell] = _blend(destination, source, source_alpha, layer.blend_mode)
        return {
            cell: nearest_palette_index(color, colors)
            for cell, color in working.items()
            if color[3] > 0.01
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "active_index": self.active_index,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    def encode(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LayerStack":
        layers = [Layer.from_dict(entry) for entry in data.get("layers", [])]
        return cls(
            layers=layers,
            active_index=data.get("active_index", 0),
            schema_version=data.get("schema_version", LAYER_SCHEMA_VERSION),
        )

    @classmethod
    def decode(cls, encoded: str) -> "LayerStack":
        return cls.from_dict(json.loads(encoded))


def _blend(destination: Color, source: Color, source_alpha: float, mode: str) -> Color:
    dst_r, dst_g, dst_b, dst_a = destination
    src_r, src_g, src_b, _ = source
    alpha = max(0.0, min(1.0, source_alpha))
    if mode == "NORMAL":
        inverse = 1.0 - alpha
        return (
            src_r * alpha + dst_r * inverse,
            src_g * alpha + dst_g * inverse,
            src_b * alpha + dst_b * inverse,
            alpha + dst_a * inverse,
        )
    if mode == "ADD":
        return (
            min(1.0, dst_r + src_r * alpha),
            min(1.0, dst_g + src_g * alpha),
            min(1.0, dst_b + src_b * alpha),
            max(dst_a, alpha),
        )
    if mode == "SUBTRACT":
        return (
            max(0.0, dst_r - src_r * alpha),
            max(0.0, dst_g - src_g * alpha),
            max(0.0, dst_b - src_b * alpha),
            dst_a,
        )
    if mode == "MULTIPLY":
        return (
            dst_r * (1.0 - alpha + src_r * alpha),
            dst_g * (1.0 - alpha + src_g * alpha),
            dst_b * (1.0 - alpha + src_b * alpha),
            max(dst_a, alpha),
        )
    if mode == "SCREEN":
        return (
            dst_r + (1.0 - dst_r) * src_r * alpha,
            dst_g + (1.0 - dst_g) * src_g * alpha,
            dst_b + (1.0 - dst_b) * src_b * alpha,
            max(dst_a, alpha),
        )
    raise ValueError(f"unsupported layer blend mode: {mode!r}")
