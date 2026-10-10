# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Portable character-part recipes for the VOXTART voxel character editor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping


CHARACTER_FORMAT = "voxtart.character"
CHARACTER_FORMAT_VERSION = 1
MAX_CHARACTER_PARTS = 8
CHARACTER_SLOTS = (
    "ROOT",
    "TORSO",
    "HEAD",
    "ARM_L",
    "ARM_R",
    "LEG_L",
    "LEG_R",
    "ACCESSORY",
)
DEFAULT_PARENT_SLOTS = {
    "ROOT": "",
    "TORSO": "ROOT",
    "HEAD": "TORSO",
    "ARM_L": "TORSO",
    "ARM_R": "TORSO",
    "LEG_L": "TORSO",
    "LEG_R": "TORSO",
    "ACCESSORY": "TORSO",
}


def _text(value: Any, field: str, *, limit: int = 256, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"character {field} must not be empty")
    if len(result) > limit:
        raise ValueError(f"character {field} is too long")
    return result


def _settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("character settings must be a mapping")
    result = {str(key): item for key, item in value.items()}
    try:
        json.dumps(result)
    except (TypeError, ValueError) as error:
        raise ValueError("character settings must be JSON serializable") from error
    return result


def default_parent_slot(slot: str) -> str:
    """Return the canonical mount parent for a character slot."""

    slot = str(slot or "").upper()
    if slot not in CHARACTER_SLOTS:
        raise ValueError(f"unsupported character slot: {slot!r}")
    return DEFAULT_PARENT_SLOTS[slot]


@dataclass(frozen=True, slots=True)
class CharacterPart:
    """One gallery blob assigned to a named character slot."""

    slot: str
    blob_id: str
    name: str = ""
    parent_slot: str = ""
    source_tag: str = ""
    target_tag: str = ""
    visible: bool = True

    def __post_init__(self) -> None:
        slot = str(self.slot or "").upper()
        if slot not in CHARACTER_SLOTS:
            raise ValueError(f"unsupported character slot: {slot!r}")
        blob_id = _text(self.blob_id, "blob id", limit=128, required=True)
        name = _text(self.name, "part name") or slot.title()
        parent_slot = str(self.parent_slot or "").upper() or default_parent_slot(slot)
        if parent_slot and parent_slot not in CHARACTER_SLOTS:
            raise ValueError(f"unsupported character parent slot: {parent_slot!r}")
        if parent_slot == slot:
            raise ValueError("character part cannot parent itself")
        object.__setattr__(self, "slot", slot)
        object.__setattr__(self, "blob_id", blob_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "parent_slot", parent_slot)
        object.__setattr__(self, "source_tag", _text(self.source_tag, "source tag", limit=96))
        object.__setattr__(self, "target_tag", _text(self.target_tag, "target tag", limit=96))
        object.__setattr__(self, "visible", bool(self.visible))

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "blob_id": self.blob_id,
            "name": self.name,
            "parent_slot": self.parent_slot,
            "source_tag": self.source_tag,
            "target_tag": self.target_tag,
            "visible": self.visible,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CharacterPart":
        if not isinstance(data, Mapping):
            raise ValueError("character part must be a JSON object")
        return cls(
            slot=data.get("slot", ""),
            blob_id=data.get("blob_id", ""),
            name=data.get("name", ""),
            parent_slot=data.get("parent_slot", ""),
            source_tag=data.get("source_tag", ""),
            target_tag=data.get("target_tag", ""),
            visible=data.get("visible", True),
        )


@dataclass(slots=True)
class CharacterRecipe:
    """Validated, serializable character customization state."""

    name: str = "VOXTART Character"
    parts: list[CharacterPart] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    schema_version: int = CHARACTER_FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CHARACTER_FORMAT_VERSION:
            raise ValueError(f"unsupported character version: {self.schema_version}")
        self.name = _text(self.name, "name", limit=128, required=True)
        if len(self.parts) > MAX_CHARACTER_PARTS:
            raise ValueError(f"characters may contain at most {MAX_CHARACTER_PARTS} parts")
        if any(not isinstance(part, CharacterPart) for part in self.parts):
            raise TypeError("character parts must contain CharacterPart values")
        slots = [part.slot for part in self.parts]
        if len(slots) != len(set(slots)):
            raise ValueError("character part slots must be unique")
        self.settings = _settings(self.settings)
        self._validate_parent_cycles()

    def _validate_parent_cycles(self) -> None:
        by_slot = {part.slot: part for part in self.parts}
        for part in self.parts:
            seen: set[str] = set()
            current = part.slot
            while current in by_slot and by_slot[current].parent_slot:
                if current in seen:
                    raise ValueError("character part parents contain a cycle")
                seen.add(current)
                current = by_slot[current].parent_slot

    def get(self, slot: str) -> CharacterPart | None:
        slot = str(slot or "").upper()
        return next((part for part in self.parts if part.slot == slot), None)

    def upsert(self, part: CharacterPart) -> None:
        if not isinstance(part, CharacterPart):
            raise TypeError("character recipe accepts CharacterPart values")
        for index, current in enumerate(self.parts):
            if current.slot == part.slot:
                self.parts[index] = part
                try:
                    self._validate_parent_cycles()
                except ValueError:
                    self.parts[index] = current
                    raise
                return
        if len(self.parts) >= MAX_CHARACTER_PARTS:
            raise ValueError(f"characters may contain at most {MAX_CHARACTER_PARTS} parts")
        self.parts.append(part)
        try:
            self._validate_parent_cycles()
        except ValueError:
            self.parts.pop()
            raise

    def remove(self, slot: str) -> bool:
        slot = str(slot or "").upper()
        for index, part in enumerate(self.parts):
            if part.slot == slot:
                self.parts.pop(index)
                return True
        return False

    def ordered_parts(self) -> tuple[CharacterPart, ...]:
        """Return a stable parent-first order for Blender object rebuilding."""

        pending = {part.slot: part for part in self.parts}
        ordered: list[CharacterPart] = []
        while pending:
            progressed = False
            for slot in CHARACTER_SLOTS:
                part = pending.get(slot)
                if part is None:
                    continue
                if not part.parent_slot or part.parent_slot not in pending:
                    ordered.append(part)
                    del pending[slot]
                    progressed = True
            if not progressed:
                raise ValueError("character part parents contain a cycle")
        return tuple(ordered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": CHARACTER_FORMAT,
            "format_version": CHARACTER_FORMAT_VERSION,
            "name": self.name,
            "parts": [part.to_dict() for part in self.ordered_parts()],
            "settings": dict(self.settings),
        }

    def encode(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CharacterRecipe":
        if not isinstance(data, Mapping):
            raise ValueError("character recipe must be a JSON object")
        if data.get("format") != CHARACTER_FORMAT:
            raise ValueError(f"unsupported character format: {data.get('format')!r}")
        if int(data.get("format_version", -1)) != CHARACTER_FORMAT_VERSION:
            raise ValueError("unsupported character version")
        raw_parts = data.get("parts", ())
        if not isinstance(raw_parts, (list, tuple)):
            raise ValueError("character parts must be a JSON list")
        return cls(
            name=data.get("name", "VOXTART Character"),
            parts=[CharacterPart.from_dict(item) for item in raw_parts],
            settings=data.get("settings", {}),
            schema_version=CHARACTER_FORMAT_VERSION,
        )

    @classmethod
    def decode(cls, encoded: str) -> "CharacterRecipe":
        try:
            payload = json.loads(str(encoded))
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid character JSON: {error.msg}") from error
        return cls.from_dict(payload)


__all__ = [
    "CHARACTER_FORMAT",
    "CHARACTER_FORMAT_VERSION",
    "CHARACTER_SLOTS",
    "DEFAULT_PARENT_SLOTS",
    "MAX_CHARACTER_PARTS",
    "CharacterPart",
    "CharacterRecipe",
    "default_parent_slot",
]
