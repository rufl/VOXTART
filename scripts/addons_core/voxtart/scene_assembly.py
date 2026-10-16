# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Portable placement records for the VOXTART Zoo scene assembler."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping


ASSEMBLY_FORMAT = "voxtart.zoo_scene"
ASSEMBLY_FORMAT_VERSION = 1
ASSEMBLY_PROPERTY = "voxtart_zoo_scene_json"
MAX_PLACEMENTS = 4096
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def new_placement_id() -> str:
    """Return a collision-resistant placement identifier."""

    return uuid.uuid4().hex


def _text(value: Any, field: str, *, limit: int = 256, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"placement {field} must not be empty")
    if len(result) > limit:
        raise ValueError(f"placement {field} is too long")
    return result


def _vector(value: Any, field: str, *, positive: bool = False) -> tuple[float, float, float]:
    try:
        result = tuple(float(axis) for axis in value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"placement {field} must contain three finite numbers") from error
    if len(result) != 3 or not all(math.isfinite(axis) for axis in result):
        raise ValueError(f"placement {field} must contain three finite numbers")
    if positive and any(abs(axis) <= 1.0e-8 for axis in result):
        raise ValueError(f"placement {field} values must be non-zero")
    return result  # type: ignore[return-value]


@dataclass(slots=True)
class ScenePlacement:
    """One gallery asset instance in a saved Zoo scene."""

    placement_id: str
    blob_id: str
    name: str
    category: str = "Uncategorized"
    location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    object_name: str = ""

    def __post_init__(self) -> None:
        self.placement_id = _text(self.placement_id, "id", limit=64, required=True)
        if not _ID_PATTERN.fullmatch(self.placement_id):
            raise ValueError("placement id must contain path-safe characters")
        self.blob_id = _text(self.blob_id, "blob id", limit=128, required=True)
        self.name = _text(self.name, "name", required=True)
        self.category = _text(self.category, "category", limit=96) or "Uncategorized"
        self.location = _vector(self.location, "location")
        self.rotation = _vector(self.rotation, "rotation")
        self.scale = _vector(self.scale, "scale", positive=True)
        self.object_name = _text(self.object_name, "object name", limit=256)

    def to_dict(self) -> dict[str, Any]:
        return {
            "placement_id": self.placement_id,
            "blob_id": self.blob_id,
            "name": self.name,
            "category": self.category,
            "location": list(self.location),
            "rotation": list(self.rotation),
            "scale": list(self.scale),
            "object_name": self.object_name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScenePlacement":
        if not isinstance(data, Mapping):
            raise ValueError("scene placement must be a JSON object")
        return cls(
            placement_id=str(data.get("placement_id", "")),
            blob_id=str(data.get("blob_id", "")),
            name=data.get("name", "Asset"),
            category=data.get("category", "Uncategorized"),
            location=data.get("location", (0.0, 0.0, 0.0)),
            rotation=data.get("rotation", (0.0, 0.0, 0.0)),
            scale=data.get("scale", (1.0, 1.0, 1.0)),
            object_name=data.get("object_name", ""),
        )


@dataclass(slots=True)
class SceneAssembly:
    """Validated placement collection persisted on a Blender scene."""

    placements: list[ScenePlacement] = field(default_factory=list)
    schema_version: int = ASSEMBLY_FORMAT_VERSION
    settings: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != ASSEMBLY_FORMAT_VERSION:
            raise ValueError(f"unsupported scene assembly version: {self.schema_version}")
        if len(self.placements) > MAX_PLACEMENTS:
            raise ValueError(f"scene assemblies may contain at most {MAX_PLACEMENTS} placements")
        if any(not isinstance(item, ScenePlacement) for item in self.placements):
            raise TypeError("scene placements must contain ScenePlacement values")
        ids = [item.placement_id for item in self.placements]
        if len(ids) != len(set(ids)):
            raise ValueError("scene placement ids must be unique")
        if not isinstance(self.settings, Mapping):
            raise TypeError("scene assembly settings must be a mapping")
        self.settings = {str(key): value for key, value in self.settings.items()}
        try:
            json.dumps(self.settings)
        except (TypeError, ValueError) as error:
            raise ValueError("scene assembly settings must be JSON serializable") from error

    def upsert(self, placement: ScenePlacement) -> None:
        if not isinstance(placement, ScenePlacement):
            raise TypeError("scene assembly accepts ScenePlacement values")
        for index, current in enumerate(self.placements):
            if current.placement_id == placement.placement_id:
                self.placements[index] = placement
                return
        if len(self.placements) >= MAX_PLACEMENTS:
            raise ValueError(f"scene assemblies may contain at most {MAX_PLACEMENTS} placements")
        self.placements.append(placement)

    def remove(self, placement_id: str) -> bool:
        placement_id = str(placement_id)
        for index, placement in enumerate(self.placements):
            if placement.placement_id == placement_id:
                self.placements.pop(index)
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": ASSEMBLY_FORMAT,
            "format_version": ASSEMBLY_FORMAT_VERSION,
            "placements": [item.to_dict() for item in self.placements],
            "settings": dict(self.settings),
        }

    def encode(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SceneAssembly":
        if not isinstance(data, Mapping):
            raise ValueError("scene assembly must contain a JSON object")
        if data.get("format") != ASSEMBLY_FORMAT:
            raise ValueError(f"unsupported scene assembly format: {data.get('format')!r}")
        format_version = data.get("format_version", -1)
        if isinstance(format_version, bool) or not isinstance(format_version, int) or format_version != ASSEMBLY_FORMAT_VERSION:
            raise ValueError("unsupported scene assembly version")
        raw_placements = data.get("placements", ())
        if not isinstance(raw_placements, (list, tuple)):
            raise ValueError("scene placements must be a JSON list")
        raw_settings = data.get("settings", {})
        if not isinstance(raw_settings, Mapping):
            raise ValueError("scene assembly settings must be a JSON object")
        return cls(
            placements=[ScenePlacement.from_dict(item) for item in raw_placements],
            schema_version=ASSEMBLY_FORMAT_VERSION,
            settings=dict(raw_settings),
        )

    @classmethod
    def decode(cls, encoded: str) -> "SceneAssembly":
        try:
            payload = json.loads(str(encoded))
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid scene assembly JSON: {error.msg}") from error
        return cls.from_dict(payload)


__all__ = [
    "ASSEMBLY_FORMAT",
    "ASSEMBLY_FORMAT_VERSION",
    "ASSEMBLY_PROPERTY",
    "MAX_PLACEMENTS",
    "SceneAssembly",
    "ScenePlacement",
    "new_placement_id",
]
