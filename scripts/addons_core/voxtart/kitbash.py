# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Socket compatibility and deterministic kitbash matching."""

from __future__ import annotations

from dataclasses import dataclass

from .voxel import Cell, VoxelAsset, VoxelSocket


AXIS_NORMALS: tuple[Cell, ...] = (
    (-1, 0, 0),
    (0, -1, 0),
    (0, 0, -1),
    (0, 0, 1),
    (0, 1, 0),
    (1, 0, 0),
)


@dataclass(frozen=True, slots=True)
class SocketMatch:
    source_index: int
    target_index: int
    tag_rank: int
    grid_distance: int

    @property
    def sort_key(self) -> tuple[int, int, int, int]:
        return self.tag_rank, self.grid_distance, self.source_index, self.target_index


def opposite(normal: Cell) -> Cell:
    if normal not in AXIS_NORMALS:
        raise ValueError(f"invalid axis normal: {normal!r}")
    return tuple(-axis for axis in normal)  # type: ignore[return-value]


def _tag_rank(source: str, target: str) -> int | None:
    if source == target:
        return 0
    if source == "*" or target == "*":
        return 1
    return None


def socket_compatibility(source: VoxelSocket, target: VoxelSocket) -> int | None:
    """Return a lower-is-better tag rank, or None when sockets cannot connect."""

    if source.normal != opposite(target.normal):
        return None
    return _tag_rank(source.tag, target.tag)


def _manhattan(a: Cell, b: Cell) -> int:
    return sum(abs(a[axis] - b[axis]) for axis in range(3))


def find_best_match(source: VoxelAsset, target: VoxelAsset) -> SocketMatch | None:
    """Choose a stable compatible pair; input list order never breaks ties."""

    matches: list[SocketMatch] = []
    for source_index, source_socket in enumerate(source.sockets):
        for target_index, target_socket in enumerate(target.sockets):
            tag_rank = socket_compatibility(source_socket, target_socket)
            if tag_rank is None:
                continue
            matches.append(SocketMatch(
                source_index=source_index,
                target_index=target_index,
                tag_rank=tag_rank,
                grid_distance=_manhattan(source_socket.position, target_socket.position),
            ))
    return min(matches, key=lambda match: match.sort_key) if matches else None


def grid_translation(source: VoxelAsset, target: VoxelAsset, match: SocketMatch) -> tuple[float, float, float]:
    """Translation that puts the source socket on the target socket."""

    source_socket = source.sockets[match.source_index]
    target_socket = target.sockets[match.target_index]
    return tuple(
        (target_socket.position[axis] - source_socket.position[axis]) * source.grid_size
        for axis in range(3)
    )
