# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Stable source identity and per-cell evidence contracts.

The records in this module deliberately stay independent of Blender and of any
particular reconstruction implementation.  They make source identity,
uncertainty, conflicts, and deterministic content hashes explicit instead of
encoding them in ad-hoc custom properties.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_STATES = ("OUTSIDE", "UNKNOWN", "FREE", "OCCUPIED", "CONFLICT")
CellKey = tuple[int, int, int]


def sha256_json(value: Any) -> str:
    """Return the SHA-256 digest of canonical, finite JSON data."""

    try:
        document = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("value must be serializable as finite JSON") from error
    return sha256(document).hexdigest()


def _text(value: Any, field: str, *, required: bool = False, limit: int = 256) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) > limit:
        raise ValueError(f"{field} must contain at most {limit} characters")
    return result


def _cell(value: Any) -> CellKey:
    try:
        result = tuple(value)
    except TypeError as error:
        raise ValueError("evidence cell must contain three integer coordinates") from error
    if len(result) != 3 or any(isinstance(axis, bool) or not isinstance(axis, int) for axis in result):
        raise ValueError(f"evidence cell must contain three integer coordinates: {value!r}")
    return result  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """Immutable identity for one source frame, pose, or calibrated view."""

    source_id: str
    digest: str
    frame_id: str = ""
    pose_id: str = ""
    camera_id: str = ""
    revision: str = ""

    def __post_init__(self) -> None:
        source_id = _text(self.source_id, "source_id", required=True)
        digest = _text(self.digest, "source digest", required=True, limit=64).lower()
        if _SHA256.fullmatch(digest) is None:
            raise ValueError("source digest must be a lowercase SHA-256 hex digest")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "digest", digest)
        for field in ("frame_id", "pose_id", "camera_id", "revision"):
            object.__setattr__(self, field, _text(getattr(self, field), field))

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (self.source_id, self.digest, self.frame_id, self.pose_id, self.camera_id, self.revision)

    def to_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "digest": self.digest,
            "frame_id": self.frame_id,
            "pose_id": self.pose_id,
            "camera_id": self.camera_id,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceIdentity":
        if not isinstance(value, Mapping):
            raise TypeError("source identity must be a mapping")
        return cls(
            source_id=value.get("source_id", ""),
            digest=value.get("digest", ""),
            frame_id=value.get("frame_id", ""),
            pose_id=value.get("pose_id", ""),
            camera_id=value.get("camera_id", ""),
            revision=value.get("revision", ""),
        )


@dataclass(frozen=True, slots=True)
class EvidenceCell:
    """Validated evidence state for one voxel cell."""

    state: str
    source_mask: int = 0
    confidence: float | None = None
    error: float | None = None
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        state = str(self.state).upper()
        if state not in EVIDENCE_STATES:
            raise ValueError(f"unsupported evidence state: {self.state!r}")
        if isinstance(self.source_mask, bool) or not isinstance(self.source_mask, int) or self.source_mask < 0:
            raise ValueError("evidence source_mask must be a non-negative integer")
        for field in ("confidence", "error"):
            value = getattr(self, field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"evidence {field} must be finite")
            if field == "confidence" and not 0.0 <= float(value) <= 1.0:
                raise ValueError("evidence confidence must be in [0, 1]")
            if field == "error" and float(value) < 0.0:
                raise ValueError("evidence error must be non-negative")
            object.__setattr__(self, field, float(value))
        normalized_sources = tuple(sorted({_text(source, "evidence source", required=True) for source in self.sources}))
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "sources", normalized_sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "source_mask": self.source_mask,
            "confidence": self.confidence,
            "error": self.error,
            "sources": list(self.sources),
        }


@dataclass(frozen=True, slots=True)
class EvidenceMap:
    """Canonical, hashable collection of non-implicit voxel evidence."""

    cells: tuple[tuple[CellKey, EvidenceCell], ...] = ()
    sources: tuple[SourceIdentity, ...] = ()

    def __post_init__(self) -> None:
        normalized_cells: list[tuple[CellKey, EvidenceCell]] = []
        seen: set[CellKey] = set()
        for cell, evidence in self.cells:
            normalized_cell = _cell(cell)
            if normalized_cell in seen:
                raise ValueError(f"duplicate evidence cell: {normalized_cell!r}")
            if not isinstance(evidence, EvidenceCell):
                raise TypeError("evidence map values must be EvidenceCell values")
            seen.add(normalized_cell)
            normalized_cells.append((normalized_cell, evidence))
        normalized_sources = tuple(self.sources)
        if any(not isinstance(source, SourceIdentity) for source in normalized_sources):
            raise TypeError("evidence sources must contain SourceIdentity values")
        source_ids = [source.source_id for source in normalized_sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("evidence source IDs must be unique")
        object.__setattr__(self, "cells", tuple(sorted(normalized_cells)))
        object.__setattr__(self, "sources", tuple(sorted(normalized_sources, key=lambda source: source.key)))

    @classmethod
    def from_mapping(
        cls,
        cells: Mapping[CellKey, EvidenceCell],
        sources: tuple[SourceIdentity, ...] = (),
    ) -> "EvidenceMap":
        return cls(tuple(cells.items()), sources)

    def get(self, cell: CellKey) -> EvidenceCell | None:
        normalized = _cell(cell)
        for current, evidence in self.cells:
            if current == normalized:
                return evidence
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": [source.to_dict() for source in self.sources],
            "cells": [
                {"cell": list(cell), "evidence": evidence.to_dict()}
                for cell, evidence in self.cells
            ],
        }

    @property
    def digest(self) -> str:
        return sha256_json(self.to_dict())


__all__ = [
    "CellKey",
    "EVIDENCE_STATES",
    "EvidenceCell",
    "EvidenceMap",
    "SourceIdentity",
    "sha256_json",
]
