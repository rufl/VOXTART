# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bounded, deterministic voxel edits with protection and rollback semantics."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Iterable

from .voxel import Cell, VoxelAsset


Bounds = tuple[Cell, Cell]


def _cell(value: Iterable[int]) -> Cell:
    result = tuple(value)
    if len(result) != 3 or any(isinstance(axis, bool) or not isinstance(axis, int) for axis in result):
        raise ValueError(f"voxel coordinates must be three integers: {value!r}")
    return result  # type: ignore[return-value]


def _bounds(minimum: Iterable[int], maximum: Iterable[int]) -> Bounds:
    minimum_cell = _cell(minimum)
    maximum_cell = _cell(maximum)
    if any(minimum_cell[axis] > maximum_cell[axis] for axis in range(3)):
        raise ValueError(f"invalid voxel bounds: {minimum_cell!r} > {maximum_cell!r}")
    return minimum_cell, maximum_cell


def _neighbors(cell: Cell) -> tuple[Cell, ...]:
    result = []
    for axis in range(3):
        for delta in (-1, 1):
            neighbor = list(cell)
            neighbor[axis] += delta
            result.append(tuple(neighbor))  # type: ignore[arg-type]
    return tuple(result)


@dataclass(frozen=True, slots=True)
class EditReport:
    operation: str
    before_voxels: int
    after_voxels: int
    added: int
    removed: int
    painted: int
    skipped_protected: int
    skipped_existing: int
    skipped_empty: int

    @property
    def changed(self) -> bool:
        return (
            self.before_voxels != self.after_voxels
            or self.added > 0
            or self.removed > 0
            or self.painted > 0
        )

    def to_dict(self) -> dict[str, int | str | bool]:
        return {**asdict(self), "changed": self.changed}


class VoxelEditTransaction:
    """Mutate an asset atomically; failed edits restore the exact prior state."""

    def __init__(
        self,
        asset: VoxelAsset,
        *,
        protected: Iterable[Cell] = (),
        allow_protected: bool = False,
        operation: str = "edit",
    ) -> None:
        self.asset = asset
        self.protected = frozenset(_cell(cell) for cell in protected)
        self.allow_protected = bool(allow_protected)
        self.operation = operation
        self._snapshot = asset.to_dict()
        self._before_voxels = len(asset.voxels)
        self._added = 0
        self._removed = 0
        self._painted = 0
        self._skipped_protected = 0
        self._skipped_existing = 0
        self._skipped_empty = 0
        self._closed = False
        self.report: EditReport | None = None

    def __enter__(self) -> "VoxelEditTransaction":
        return self

    def __exit__(self, exception_type, _exception, _traceback) -> bool:
        if exception_type is not None:
            self.rollback()
            return False
        if not self._closed:
            self.commit()
        return False

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("voxel edit transaction is already closed")

    def _can_edit(self, cell: Cell) -> bool:
        if cell in self.protected and not self.allow_protected:
            self._skipped_protected += 1
            return False
        return True

    def add(self, cell: Iterable[int], palette_index: int = 0, *, overwrite: bool = True) -> None:
        self._check_open()
        normalized = _cell(cell)
        if not self._can_edit(normalized):
            return
        current = self.asset.get_voxel(normalized)
        if current is not None and not overwrite:
            self._skipped_existing += 1
            return
        if current is None:
            self.asset.set_voxel(normalized, palette_index)
            self._added += 1
        elif current != palette_index:
            self.asset.set_voxel(normalized, palette_index)
            self._painted += 1

    def remove(self, cell: Iterable[int]) -> None:
        self._check_open()
        normalized = _cell(cell)
        if not self._can_edit(normalized):
            return
        if self.asset.get_voxel(normalized) is None:
            self._skipped_empty += 1
            return
        self.asset.remove_voxel(normalized)
        self._removed += 1

    def paint(self, cell: Iterable[int], palette_index: int) -> None:
        self._check_open()
        normalized = _cell(cell)
        if not self._can_edit(normalized):
            return
        current = self.asset.get_voxel(normalized)
        if current is None:
            self._skipped_empty += 1
            return
        if current != palette_index:
            self.asset.set_voxel(normalized, palette_index)
            self._painted += 1

    def fill_box(
        self,
        minimum: Iterable[int],
        maximum: Iterable[int],
        palette_index: int = 0,
        *,
        overwrite: bool = True,
    ) -> None:
        self._check_open()
        minimum_cell, maximum_cell = _bounds(minimum, maximum)
        for x in range(minimum_cell[0], maximum_cell[0] + 1):
            for y in range(minimum_cell[1], maximum_cell[1] + 1):
                for z in range(minimum_cell[2], maximum_cell[2] + 1):
                    self.add((x, y, z), palette_index, overwrite=overwrite)

    def flood_fill(
        self,
        start: Iterable[int],
        maximum: Iterable[int],
        palette_index: int = 0,
        *,
        minimum: Iterable[int] | None = None,
    ) -> None:
        """Replace the six-connected region of the start cell within explicit bounds."""

        self._check_open()
        start_cell = _cell(start)
        minimum_cell, maximum_cell = _bounds(
            minimum if minimum is not None else start_cell,
            maximum,
        )
        if any(
            start_cell[axis] < minimum_cell[axis] or start_cell[axis] > maximum_cell[axis]
            for axis in range(3)
        ):
            raise ValueError("flood-fill start cell must be inside its bounds")

        source = dict(self.asset.voxels)
        target = source.get(start_cell)
        pending = deque([start_cell])
        visited = {start_cell}
        region: list[Cell] = []
        while pending:
            cell = pending.popleft()
            if source.get(cell) != target:
                continue
            region.append(cell)
            for neighbor in _neighbors(cell):
                if neighbor in visited:
                    continue
                if any(
                    neighbor[axis] < minimum_cell[axis] or neighbor[axis] > maximum_cell[axis]
                    for axis in range(3)
                ):
                    continue
                visited.add(neighbor)
                pending.append(neighbor)
        for cell in sorted(region):
            if target is None:
                self.add(cell, palette_index, overwrite=False)
            else:
                self.paint(cell, palette_index)

    def commit(self) -> EditReport:
        self._check_open()
        self._closed = True
        self.report = EditReport(
            operation=self.operation,
            before_voxels=self._before_voxels,
            after_voxels=len(self.asset.voxels),
            added=self._added,
            removed=self._removed,
            painted=self._painted,
            skipped_protected=self._skipped_protected,
            skipped_existing=self._skipped_existing,
            skipped_empty=self._skipped_empty,
        )
        return self.report

    def rollback(self) -> None:
        if self._closed:
            return
        restored = VoxelAsset.from_dict(self._snapshot)
        self.asset.grid_size = restored.grid_size
        self.asset.palette = restored.palette
        self.asset.palette_materials = restored.palette_materials
        self.asset.voxels.clear()
        self.asset.voxels.update(restored.voxels)
        self.asset.sockets = restored.sockets
        self.asset.provenance = restored.provenance
        self.asset.schema_version = restored.schema_version
        self._closed = True
        self.report = None


def connected_components(asset: VoxelAsset) -> list[tuple[Cell, ...]]:
    """Return six-connected occupied components in deterministic order."""

    remaining = set(asset.voxels)
    components: list[tuple[Cell, ...]] = []
    while remaining:
        start = min(remaining)
        pending = [start]
        remaining.remove(start)
        component = [start]
        while pending:
            cell = pending.pop()
            for neighbor in _neighbors(cell):
                if neighbor not in remaining:
                    continue
                remaining.remove(neighbor)
                pending.append(neighbor)
                component.append(neighbor)
        components.append(tuple(sorted(component)))
    components.sort(key=lambda component: (component[0], len(component), component))
    return components


def edit_asset(
    asset: VoxelAsset,
    operation: str,
    callback,
    *,
    protected: Iterable[Cell] = (),
    allow_protected: bool = False,
) -> EditReport:
    """Run a caller-supplied edit with automatic commit/rollback."""

    with VoxelEditTransaction(
        asset,
        protected=protected,
        allow_protected=allow_protected,
        operation=operation,
    ) as transaction:
        callback(transaction)
    assert transaction.report is not None
    return transaction.report
