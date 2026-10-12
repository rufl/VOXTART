# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic sprite-sheet frame observations and voxel deltas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .image_intake import IndexedImage
from .provenance import SourceIdentity
from .reconstruct import OrthographicView, ReconstructionResult, ReconstructionSettings, reconstruct_views
from .voxel import Cell, Color, VoxelAsset


@dataclass(frozen=True, slots=True)
class SequenceFrame:
    frame_id: str
    image: IndexedImage
    pose_id: str = ""
    camera_id: str = ""
    axis: str = "Z"
    direction: int = 1
    origin: Cell = (0, 0, 0)
    depth: int = 1

    def __post_init__(self) -> None:
        frame_id = str(self.frame_id).strip()
        if not frame_id:
            raise ValueError("sequence frame_id must not be empty")
        if not isinstance(self.image, IndexedImage):
            raise TypeError("sequence image must be an IndexedImage")
        object.__setattr__(self, "frame_id", frame_id)
        object.__setattr__(self, "pose_id", str(self.pose_id).strip())
        object.__setattr__(self, "camera_id", str(self.camera_id).strip())

    def source_identity(self) -> SourceIdentity:
        return self.image.source_identity(
            source_id=self.image.name,
            frame_id=self.frame_id,
            pose_id=self.pose_id,
            camera_id=self.camera_id,
        )

    def to_view(self, *, palette: Sequence[Color] | None = None) -> OrthographicView:
        if palette is not None and tuple(palette) != self.image.palette:
            raise ValueError("sequence frames must use the shared extracted palette")
        return self.image.to_view(
            name=f"frame_{self.frame_id}_{self.image.name}",
            axis=self.axis,
            direction=self.direction,
            origin=self.origin,
            depth=self.depth,
            source_identity=self.source_identity(),
        )


@dataclass(frozen=True, slots=True)
class SpriteSequence:
    frames: tuple[SequenceFrame, ...]
    name: str = "sequence"

    def __post_init__(self) -> None:
        frames = tuple(self.frames)
        if not frames:
            raise ValueError("sprite sequence requires at least one frame")
        if len({frame.frame_id for frame in frames}) != len(frames):
            raise ValueError("sprite sequence frame IDs must be unique")
        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "name", str(self.name).strip() or "sequence")


@dataclass(frozen=True, slots=True)
class PoseDelta:
    frame_id: str
    added: tuple[Cell, ...]
    removed: tuple[Cell, ...]
    recolored: tuple[Cell, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "frame_id": self.frame_id,
            "added": [list(cell) for cell in self.added],
            "removed": [list(cell) for cell in self.removed],
            "recolored": [list(cell) for cell in self.recolored],
        }


@dataclass(frozen=True, slots=True)
class SpriteSequenceResult:
    base: ReconstructionResult
    frames: tuple[ReconstructionResult, ...]
    deltas: tuple[PoseDelta, ...]


def _delta(base: VoxelAsset, current: VoxelAsset, frame_id: str) -> PoseDelta:
    base_cells = set(base.voxels)
    current_cells = set(current.voxels)
    added = tuple(sorted(current_cells - base_cells))
    removed = tuple(sorted(base_cells - current_cells))
    recolored = tuple(sorted(cell for cell in base_cells & current_cells if base.voxels[cell] != current.voxels[cell]))
    return PoseDelta(frame_id, added, removed, recolored)


def reconstruct_sequence(
    sequence: SpriteSequence,
    palette: Sequence[Color],
    *,
    settings: ReconstructionSettings = ReconstructionSettings(),
) -> SpriteSequenceResult:
    """Reconstruct frames independently and compare them to the first frame."""

    views = tuple(frame.to_view(palette=palette) for frame in sequence.frames)
    results = tuple(
        reconstruct_views(
            (view,),
            palette,
            settings,
            source_identities=(view.source_identity,) if view.source_identity is not None else (),
        )
        for view in views
    )
    base = results[0]
    deltas = tuple(
        _delta(base.asset, result.asset, frame.frame_id)
        for frame, result in zip(sequence.frames[1:], results[1:])
    )
    return SpriteSequenceResult(base, results, deltas)


__all__ = [
    "PoseDelta",
    "SequenceFrame",
    "SpriteSequence",
    "SpriteSequenceResult",
    "reconstruct_sequence",
]
