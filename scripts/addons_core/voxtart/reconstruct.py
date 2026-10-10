# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Constraint-based orthographic pixel-art to voxel reconstruction.

The converter accepts calibrated, indexed orthographic views.  It never fills
unobserved depth with a guessed shape silently: candidate cells that remain
unresolved are returned separately from the authoritative voxel asset.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Literal, Sequence

from .voxel import Cell, Color, VoxelAsset


AXES = ("X", "Y", "Z")
DIRECTIONS = (-1, 1)
Pixel = int | None
ReconstructionMode = Literal["SURFACE", "VOLUME"]


def _cell(value: Iterable[int]) -> Cell:
    result = tuple(value)
    if len(result) != 3 or any(isinstance(item, bool) or not isinstance(item, int) for item in result):
        raise ValueError(f"voxel coordinates must be three integers: {value!r}")
    return result  # type: ignore[return-value]


def _axis_index(axis: str) -> int:
    normalized = str(axis).upper()
    if normalized not in AXES:
        raise ValueError(f"view axis must be one of {AXES}: {axis!r}")
    return AXES.index(normalized)


def _plane_axes(axis: str) -> tuple[int, int]:
    """Return horizontal and top-to-bottom world axes for one view axis."""

    if axis == "X":
        return 1, 2
    if axis == "Y":
        return 0, 2
    return 0, 1


@dataclass(frozen=True, slots=True)
class OrthographicView:
    """One calibrated indexed image and its bounded depth slab.

    ``origin`` is the world cell at image column 0, row 0, depth 0.  Columns
    advance along the view's horizontal plane axis; rows advance toward the
    negative vertical plane axis.  ``direction`` selects the depth direction.
    ``None`` pixels are transparent/background constraints.
    """

    name: str
    axis: str
    direction: int
    pixels: tuple[tuple[Pixel, ...], ...]
    origin: Cell = (0, 0, 0)
    depth: int = 1
    priority: int = 0

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("view name must not be empty")
        axis = str(self.axis).upper()
        _axis_index(axis)
        if isinstance(self.direction, bool) or self.direction not in DIRECTIONS:
            raise ValueError("view direction must be -1 or 1")
        if isinstance(self.depth, bool) or not isinstance(self.depth, int) or self.depth <= 0:
            raise ValueError("view depth must be a positive integer")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError("view priority must be an integer")
        origin = _cell(self.origin)
        rows = tuple(tuple(row) for row in self.pixels)
        if not rows or not rows[0]:
            raise ValueError("view pixels must contain at least one cell")
        width = len(rows[0])
        for row in rows:
            if len(row) != width:
                raise ValueError("view pixel rows must have equal widths")
            for pixel in row:
                if pixel is not None and (isinstance(pixel, bool) or not isinstance(pixel, int) or pixel < 0):
                    raise ValueError(f"view pixels must be non-negative palette indices or None: {pixel!r}")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "axis", axis)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "pixels", rows)

    @classmethod
    def from_rows(
        cls,
        name: str,
        rows: Sequence[Sequence[Pixel]],
        *,
        axis: str = "Z",
        direction: int = 1,
        origin: Cell = (0, 0, 0),
        depth: int = 1,
        priority: int = 0,
    ) -> "OrthographicView":
        """Build a view from top-to-bottom image rows."""

        return cls(
            name=name,
            axis=axis,
            direction=direction,
            pixels=tuple(tuple(row) for row in rows),
            origin=origin,
            depth=depth,
            priority=priority,
        )

    @property
    def width(self) -> int:
        return len(self.pixels[0])

    @property
    def height(self) -> int:
        return len(self.pixels)

    def pixel(self, column: int, row: int) -> Pixel:
        return self.pixels[row][column]

    def cell_at(self, column: int, row: int, depth: int = 0) -> Cell:
        """Map an image pixel and slab depth to an integer voxel cell."""

        if not 0 <= column < self.width or not 0 <= row < self.height:
            raise IndexError("view pixel is outside the image")
        if not 0 <= depth < self.depth:
            raise IndexError("view depth is outside the calibrated slab")
        horizontal_axis, vertical_axis = _plane_axes(self.axis)
        depth_axis = _axis_index(self.axis)
        cell = list(self.origin)
        cell[horizontal_axis] += column
        cell[vertical_axis] -= row
        cell[depth_axis] += self.direction * depth
        return tuple(cell)  # type: ignore[return-value]

    def project_cell(self, cell: Iterable[int]) -> tuple[int, int, int] | None:
        """Return ``(column, row, depth)`` when a cell is inside this view."""

        normalized = _cell(cell)
        horizontal_axis, vertical_axis = _plane_axes(self.axis)
        depth_axis = _axis_index(self.axis)
        depth = (normalized[depth_axis] - self.origin[depth_axis]) * self.direction
        column = normalized[horizontal_axis] - self.origin[horizontal_axis]
        row = self.origin[vertical_axis] - normalized[vertical_axis]
        if not (0 <= column < self.width and 0 <= row < self.height and 0 <= depth < self.depth):
            return None
        return column, row, depth


@dataclass(frozen=True, slots=True)
class ReconstructionSettings:
    """Deterministic depth policy for one reconstruction pass."""

    mode: ReconstructionMode = "SURFACE"

    def __post_init__(self) -> None:
        mode = str(self.mode).upper()
        if mode not in ("SURFACE", "VOLUME"):
            raise ValueError("reconstruction mode must be SURFACE or VOLUME")
        object.__setattr__(self, "mode", mode)


@dataclass(frozen=True, slots=True)
class ReconstructionReport:
    """Source comparison and ambiguity metrics for a reconstruction pass."""

    view_count: int
    source_pixels: int
    occupied_voxels: int
    unresolved_voxels: int
    conflicting_rays: int
    material_conflicts: int
    missing_pixels: int
    extra_pixels: int
    color_mismatches: int
    conflict_pixels: tuple[tuple[str, int, int], ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["conflict_pixels"] = [list(item) for item in self.conflict_pixels]
        return value


@dataclass(frozen=True, slots=True)
class ReconstructionResult:
    """Authoritative asset plus explicit unresolved candidate cells."""

    asset: VoxelAsset
    unresolved: tuple[Cell, ...]
    report: ReconstructionReport

    def render_back(self, view: OrthographicView) -> tuple[tuple[Pixel, ...], ...]:
        return render_view(self.asset, view)


@dataclass(frozen=True, slots=True)
class _Ray:
    view: OrthographicView
    column: int
    row: int
    color: int
    candidates: tuple[Cell, ...]


def _palette(value: Sequence[Color]) -> tuple[Color, ...]:
    # Let the authoritative asset validator normalize colors and enforce the
    # same palette contract as every other VOXTART core operation.
    return tuple(VoxelAsset(palette=list(value)).palette)


def render_view(asset: VoxelAsset, view: OrthographicView) -> tuple[tuple[Pixel, ...], ...]:
    """Render the nearest voxel per calibrated image ray as palette indices."""

    rows = [[None for _column in range(view.width)] for _row in range(view.height)]
    depths: list[list[int | None]] = [[None for _column in range(view.width)] for _row in range(view.height)]
    for cell, material in asset.iter_voxels():
        projection = view.project_cell(cell)
        if projection is None:
            continue
        column, row, depth = projection
        previous_depth = depths[row][column]
        if previous_depth is None or depth < previous_depth:
            depths[row][column] = depth
            rows[row][column] = material
    return tuple(tuple(row) for row in rows)


def reconstruct_views(
    views: Sequence[OrthographicView],
    palette: Sequence[Color],
    settings: ReconstructionSettings = ReconstructionSettings(),
) -> ReconstructionResult:
    """Reconstruct a bounded voxel asset from one to six orthographic views.

    Colored pixels create occupied-ray constraints.  Transparent pixels create
    empty-ray constraints.  ``SURFACE`` chooses the nearest viable cell for
    each colored ray; ``VOLUME`` retains every viable cell.  Viable but
    unselected cells are returned as ``unresolved`` rather than guessed away.
    """

    if not isinstance(settings, ReconstructionSettings):
        raise TypeError("settings must be ReconstructionSettings")
    normalized_views = tuple(views)
    if not 1 <= len(normalized_views) <= 6:
        raise ValueError("reconstruction requires between one and six views")
    if any(not isinstance(view, OrthographicView) for view in normalized_views):
        raise TypeError("views must contain OrthographicView values")
    names = [view.name for view in normalized_views]
    if len(set(names)) != len(names):
        raise ValueError("view names must be unique")
    normalized_palette = _palette(palette)

    forbidden: set[Cell] = set()
    source_pixels = 0
    for view in normalized_views:
        for row in range(view.height):
            for column in range(view.width):
                pixel = view.pixel(column, row)
                if pixel is None:
                    forbidden.update(view.cell_at(column, row, depth) for depth in range(view.depth))
                    continue
                if pixel >= len(normalized_palette):
                    raise ValueError(f"view pixel palette index out of range: {pixel}")
                source_pixels += 1

    rays: list[_Ray] = []
    possible: set[Cell] = set()
    for view in normalized_views:
        for row in range(view.height):
            for column in range(view.width):
                pixel = view.pixel(column, row)
                if pixel is None:
                    continue
                candidate_cells = []
                for depth in range(view.depth):
                    cell = view.cell_at(column, row, depth)
                    if cell not in forbidden:
                        candidate_cells.append(cell)
                candidates = tuple(candidate_cells)
                rays.append(_Ray(view, column, row, pixel, candidates))
                possible.update(candidates)

    votes: dict[Cell, list[tuple[int, int]]] = {}
    conflicts: list[tuple[str, int, int]] = []
    for ray in sorted(rays, key=lambda item: (item.view.priority, item.view.name, item.row, item.column)):
        if not ray.candidates:
            conflicts.append((ray.view.name, ray.column, ray.row))
            continue
        selected = ray.candidates if settings.mode == "VOLUME" else ray.candidates[:1]
        for cell in selected:
            votes.setdefault(cell, []).append((ray.view.priority, ray.color))

    occupied: dict[Cell, int] = {}
    material_conflicts = 0
    for cell in sorted(votes):
        entries = votes[cell]
        colors = {color for _priority, color in entries}
        if len(colors) > 1:
            material_conflicts += 1
        counts: dict[int, int] = {}
        for _priority, color in entries:
            counts[color] = counts.get(color, 0) + 1
        occupied[cell] = min(counts, key=lambda color: (-counts[color], color))
    asset = VoxelAsset(
        palette=list(normalized_palette),
        voxels=occupied,
        provenance={
            "reconstruction": "orthographic",
            "view_count": str(len(normalized_views)),
            "view_names": ",".join(view.name for view in normalized_views),
            "mode": settings.mode,
        },
    )
    missing_pixels = 0
    extra_pixels = 0
    color_mismatches = 0
    for view in normalized_views:
        rendered = render_view(asset, view)
        for row in range(view.height):
            for column in range(view.width):
                source = view.pixel(column, row)
                result = rendered[row][column]
                if source is None:
                    if result is not None:
                        extra_pixels += 1
                elif result is None:
                    missing_pixels += 1
                elif source != result:
                    color_mismatches += 1

    unresolved = tuple(sorted(possible.difference(occupied)))
    report = ReconstructionReport(
        view_count=len(normalized_views),
        source_pixels=source_pixels,
        occupied_voxels=len(asset.voxels),
        unresolved_voxels=len(unresolved),
        conflicting_rays=len(conflicts),
        material_conflicts=material_conflicts,
        missing_pixels=missing_pixels,
        extra_pixels=extra_pixels,
        color_mismatches=color_mismatches,
        conflict_pixels=tuple(conflicts),
    )
    return ReconstructionResult(asset=asset, unresolved=unresolved, report=report)


__all__ = [
    "AXES",
    "DIRECTIONS",
    "OrthographicView",
    "Pixel",
    "ReconstructionReport",
    "ReconstructionResult",
    "ReconstructionSettings",
    "render_view",
    "reconstruct_views",
]
