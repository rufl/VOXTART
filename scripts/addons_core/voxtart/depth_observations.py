# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic depth and part observations for sprite reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .provenance import SourceIdentity, sha256_json


MAX_OBSERVATION_PIXELS = 1_000_000
MAX_OBSERVATION_BYTES = 64 * 1024 * 1024
MAX_DEPTH_VALUE = 4_000_000
DepthInterval = tuple[int, int]
DepthPixel = int | None
PartPixel = str | None
def _rows(value: Sequence[Sequence[Any]], name: str) -> tuple[tuple[Any, ...], ...]:
    result = tuple(tuple(row) for row in value)
    if not result or not result[0]:
        raise ValueError(f"{name} must contain at least one pixel")
    width = len(result[0])
    if any(len(row) != width for row in result):
        raise ValueError(f"{name} rows must have equal widths")
    if len(result) * width > MAX_OBSERVATION_PIXELS:
        raise ValueError(
            f"{name} exceeds the {MAX_OBSERVATION_PIXELS:,}-pixel observation limit"
        )
    return result


def _finite_confidence(value: float | None) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError("observation confidence must be finite and in [0, 1]")
    return normalized


@dataclass(frozen=True, slots=True)
class DepthObservation:
    """Optional per-pixel front/back depth intervals in view-local cells."""

    name: str
    front: tuple[tuple[DepthPixel, ...], ...]
    back: tuple[tuple[DepthPixel, ...], ...] | None = None
    confidence: tuple[tuple[float | None, ...], ...] | None = None
    source_identity: SourceIdentity | None = None
    method: str = "MANUAL"

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("depth observation name must not be empty")
        front = _rows(self.front, "depth front")
        back = None if self.back is None else _rows(self.back, "depth back")
        if back is not None and (len(back), len(back[0])) != (len(front), len(front[0])):
            raise ValueError("depth front and back dimensions must match")
        normalized_front: list[tuple[DepthPixel, ...]] = []
        normalized_back: list[tuple[DepthPixel, ...]] | None = [] if back is not None else None
        for row_index, row in enumerate(front):
            normalized_front_row: list[DepthPixel] = []
            normalized_back_row: list[DepthPixel] = []
            for column_index, value in enumerate(row):
                if value is not None:
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or not 0 <= value <= MAX_DEPTH_VALUE
                    ):
                        raise ValueError(
                            f"depth values must be integers in [0, {MAX_DEPTH_VALUE:,}] or None"
                        )
                back_value = None if back is None else back[row_index][column_index]
                if back_value is not None:
                    if (
                        isinstance(back_value, bool)
                        or not isinstance(back_value, int)
                        or not 0 <= back_value <= MAX_DEPTH_VALUE
                    ):
                        raise ValueError(
                            f"depth values must be integers in [0, {MAX_DEPTH_VALUE:,}] or None"
                        )
                if value is None and back_value is not None:
                    raise ValueError("a back depth requires a front depth")
                if value is not None and back_value is not None and back_value < value:
                    raise ValueError("back depth must not be nearer than front depth")
                normalized_front_row.append(value)
                if back is not None:
                    normalized_back_row.append(back_value)
            normalized_front.append(tuple(normalized_front_row))
            if normalized_back is not None:
                normalized_back.append(tuple(normalized_back_row))
        normalized_confidence = None
        if self.confidence is not None:
            confidence_rows = _rows(self.confidence, "depth confidence")
            if (len(confidence_rows), len(confidence_rows[0])) != (len(front), len(front[0])):
                raise ValueError("depth confidence dimensions must match depth front")
            normalized_confidence = tuple(
                tuple(_finite_confidence(value) for value in row)
                for row in confidence_rows
            )
        if self.source_identity is not None and not isinstance(self.source_identity, SourceIdentity):
            raise TypeError("depth source_identity must be a SourceIdentity")
        method = str(self.method).strip().upper()
        if not method:
            raise ValueError("depth observation method must not be empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "front", tuple(normalized_front))
        object.__setattr__(self, "back", None if normalized_back is None else tuple(normalized_back))
        object.__setattr__(self, "confidence", normalized_confidence)
        object.__setattr__(self, "method", method)

    @classmethod
    def from_rows(
        cls,
        name: str,
        front: Sequence[Sequence[DepthPixel]],
        *,
        back: Sequence[Sequence[DepthPixel]] | None = None,
        confidence: Sequence[Sequence[float | None]] | None = None,
        source_identity: SourceIdentity | None = None,
        method: str = "MANUAL",
    ) -> "DepthObservation":
        return cls(
            name=name,
            front=tuple(tuple(row) for row in front),
            back=None if back is None else tuple(tuple(row) for row in back),
            confidence=None if confidence is None else tuple(tuple(row) for row in confidence),
            source_identity=source_identity,
            method=method,
        )

    @property
    def width(self) -> int:
        return len(self.front[0])

    @property
    def height(self) -> int:
        return len(self.front)

    def interval(self, column: int, row: int, fallback_depth: int) -> DepthInterval | None:
        if not 0 <= column < self.width or not 0 <= row < self.height:
            raise IndexError("depth observation pixel is outside the image")
        front = self.front[row][column]
        if front is None:
            return None
        back = front if self.back is None or self.back[row][column] is None else self.back[row][column]
        return int(front), int(back)

    def confidence_at(self, column: int, row: int) -> float | None:
        if not 0 <= column < self.width or not 0 <= row < self.height:
            raise IndexError("depth observation pixel is outside the image")
        return None if self.confidence is None else self.confidence[row][column]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "name": self.name,
            "front": [list(row) for row in self.front],
            "back": None if self.back is None else [list(row) for row in self.back],
            "confidence": None if self.confidence is None else [list(row) for row in self.confidence],
            "source_identity": None if self.source_identity is None else self.source_identity.to_dict(),
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DepthObservation":
        if not isinstance(value, Mapping) or value.get("schema", 1) != 1:
            raise ValueError("unsupported depth observation schema")
        source = value.get("source_identity")
        return cls.from_rows(
            str(value.get("name", "depth")),
            value.get("front", ()),
            back=value.get("back"),
            confidence=value.get("confidence"),
            source_identity=None if source is None else SourceIdentity.from_dict(source),
            method=str(value.get("method", "MANUAL")),
        )

    @property
    def digest(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class PartMask:
    """Per-pixel semantic part labels; labels never imply hidden geometry."""

    name: str
    labels: tuple[tuple[PartPixel, ...], ...]
    source_identity: SourceIdentity | None = None

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("part mask name must not be empty")
        labels = _rows(self.labels, "part mask")
        normalized: list[tuple[PartPixel, ...]] = []
        for row in labels:
            normalized_row: list[PartPixel] = []
            for label in row:
                if label is not None:
                    label = str(label).strip()
                    if not label:
                        raise ValueError("part labels must be non-empty strings or None")
                normalized_row.append(label)
            normalized.append(tuple(normalized_row))
        if self.source_identity is not None and not isinstance(self.source_identity, SourceIdentity):
            raise TypeError("part mask source_identity must be a SourceIdentity")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "labels", tuple(normalized))

    @classmethod
    def from_rows(
        cls,
        name: str,
        labels: Sequence[Sequence[PartPixel]],
        *,
        source_identity: SourceIdentity | None = None,
    ) -> "PartMask":
        return cls(name, tuple(tuple(row) for row in labels), source_identity)

    @property
    def width(self) -> int:
        return len(self.labels[0])

    @property
    def height(self) -> int:
        return len(self.labels)

    def part_at(self, column: int, row: int) -> str | None:
        if not 0 <= column < self.width or not 0 <= row < self.height:
            raise IndexError("part mask pixel is outside the image")
        return self.labels[row][column]

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(sorted({label for row in self.labels for label in row if label is not None}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "name": self.name,
            "labels": [list(row) for row in self.labels],
            "source_identity": None if self.source_identity is None else self.source_identity.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PartMask":
        if not isinstance(value, Mapping) or value.get("schema", 1) != 1:
            raise ValueError("unsupported part mask schema")
        source = value.get("source_identity")
        return cls.from_rows(
            str(value.get("name", "parts")),
            value.get("labels", ()),
            source_identity=None if source is None else SourceIdentity.from_dict(source),
        )

    @property
    def digest(self) -> str:
        return sha256_json(self.to_dict())


def _load_json(path: str | Path) -> Mapping[str, Any]:
    candidate = Path(path)
    if candidate.stat().st_size > MAX_OBSERVATION_BYTES:
        raise ValueError(
            f"observation file exceeds the {MAX_OBSERVATION_BYTES:,}-byte limit"
        )
    value = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("observation JSON root must be an object")
    return value


def load_observation(path: str | Path) -> DepthObservation:
    """Load a strict, size-bounded JSON depth observation."""

    return DepthObservation.from_dict(_load_json(path))


def load_part_mask(path: str | Path) -> PartMask:
    """Load a strict, size-bounded JSON part mask."""

    return PartMask.from_dict(_load_json(path))


__all__ = [
    "DepthInterval",
    "DepthObservation",
    "DepthPixel",
    "MAX_DEPTH_VALUE",
    "MAX_OBSERVATION_BYTES",
    "MAX_OBSERVATION_PIXELS",
    "PartMask",
    "PartPixel",
    "load_observation",
    "load_part_mask",
]
