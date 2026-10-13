# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Constraint-based orthographic pixel-art to voxel reconstruction.

The converter accepts calibrated, indexed orthographic views.  It never fills
unobserved depth with a guessed shape silently: candidate cells that remain
unresolved are returned separately from the authoritative voxel asset.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Iterable, Literal, Mapping, Sequence

from .depth_observations import DepthObservation, PartMask
from .metrics import compare_indexed_views
from .provenance import EvidenceCell, EvidenceMap, SourceIdentity, sha256_json
from .voxel import Cell, Color, VoxelAsset


AXES = ("X", "Y", "Z")
DIRECTIONS = (-1, 1)
Pixel = int | None
ReconstructionMode = Literal["SURFACE", "VOLUME", "LAYERED", "SHELL"]


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
    source_identity: SourceIdentity | None = None
    depth_observation: DepthObservation | None = None
    part_mask: PartMask | None = None

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
        if self.source_identity is not None and not isinstance(self.source_identity, SourceIdentity):
            raise TypeError("view source_identity must be a SourceIdentity")
        if self.depth_observation is not None and not isinstance(self.depth_observation, DepthObservation):
            raise TypeError("view depth_observation must be a DepthObservation")
        if self.part_mask is not None and not isinstance(self.part_mask, PartMask):
            raise TypeError("view part_mask must be a PartMask")
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
        if self.depth_observation is not None:
            if (self.depth_observation.width, self.depth_observation.height) != (width, len(rows)):
                raise ValueError("depth observation dimensions must match view pixels")
            for row_index in range(len(rows)):
                for column_index in range(width):
                    interval = self.depth_observation.interval(column_index, row_index, self.depth)
                    if interval is not None and interval[1] >= self.depth:
                        raise ValueError("depth observation exceeds the calibrated view depth")
        if self.part_mask is not None and (self.part_mask.width, self.part_mask.height) != (width, len(rows)):
            raise ValueError("part mask dimensions must match view pixels")
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
        source_identity: SourceIdentity | None = None,
        depth_observation: DepthObservation | None = None,
        part_mask: PartMask | None = None,
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
            source_identity=source_identity,
            depth_observation=depth_observation,
            part_mask=part_mask,
        )
    @property
    def width(self) -> int:
        return len(self.pixels[0])

    @property
    def height(self) -> int:
        return len(self.pixels)

    def pixel(self, column: int, row: int) -> Pixel:
        return self.pixels[row][column]

    def depth_interval(self, column: int, row: int) -> tuple[int, int]:
        if not 0 <= column < self.width or not 0 <= row < self.height:
            raise IndexError("view pixel is outside the image")
        if self.depth_observation is not None:
            interval = self.depth_observation.interval(column, row, self.depth)
            if interval is not None:
                return interval
        return 0, self.depth - 1

    def depth_indices(self, column: int, row: int) -> range:
        front, back = self.depth_interval(column, row)
        return range(front, back + 1)

    def part_at(self, column: int, row: int) -> str | None:
        if self.part_mask is None:
            return None
        return self.part_mask.part_at(column, row)


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
        front, back = self.depth_interval(column, row)
        if not front <= depth <= back:
            return None
        return column, row, depth


@dataclass(frozen=True, slots=True)
class ReconstructionSettings:
    """Deterministic depth, shell, and material policy for one pass."""

    mode: ReconstructionMode = "SURFACE"
    shell_thickness: int = 1
    material_policy: Literal["EVIDENCE", "VISIBLE_ONLY"] = "VISIBLE_ONLY"

    def __post_init__(self) -> None:
        mode = str(self.mode).upper()
        if mode not in ("SURFACE", "VOLUME", "LAYERED", "SHELL"):
            raise ValueError("reconstruction mode must be SURFACE, VOLUME, LAYERED, or SHELL")
        if isinstance(self.shell_thickness, bool) or not isinstance(self.shell_thickness, int) or self.shell_thickness <= 0:
            raise ValueError("shell_thickness must be a positive integer")
        material_policy = str(self.material_policy).upper()
        if material_policy not in ("EVIDENCE", "VISIBLE_ONLY"):
            raise ValueError("material_policy must be EVIDENCE or VISIBLE_ONLY")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "material_policy", material_policy)


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
    silhouette_intersection: int = 0
    silhouette_union: int = 0
    silhouette_iou: float = 1.0
    visible_material_conflicts: int = 0

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["conflict_pixels"] = [list(item) for item in self.conflict_pixels]
        return value


@dataclass(frozen=True, slots=True)
class AcceptancePolicy:
    """Explicit quality gate for publishing a reconstructed asset."""

    max_unresolved: int = 0
    max_conflicting_rays: int = 0
    max_material_conflicts: int = 0
    max_missing_pixels: int = 0
    max_extra_pixels: int = 0
    max_color_mismatches: int = 0
    minimum_silhouette_iou: float = 1.0
    require_source_identities: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "max_unresolved",
            "max_conflicting_rays",
            "max_material_conflicts",
            "max_missing_pixels",
            "max_extra_pixels",
            "max_color_mismatches",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if (
            isinstance(self.minimum_silhouette_iou, bool)
            or not isinstance(self.minimum_silhouette_iou, (int, float))
            or not 0.0 <= float(self.minimum_silhouette_iou) <= 1.0
        ):
            raise ValueError("minimum_silhouette_iou must be in [0, 1]")
        if not isinstance(self.require_source_identities, bool):
            raise TypeError("require_source_identities must be boolean")


@dataclass(frozen=True, slots=True)
class AcceptanceDecision:
    """Immutable review result; accepted assets have no failed reasons."""

    accepted: bool
    reasons: tuple[str, ...]
    asset_digest: str
    evidence_digest: str
    report: ReconstructionReport

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "asset_digest": self.asset_digest,
            "evidence_digest": self.evidence_digest,
            "report": self.report.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ReconstructionResult:
    """Authoritative asset plus explicit unresolved candidate cells."""

    asset: VoxelAsset
    unresolved: tuple[Cell, ...]
    report: ReconstructionReport
    evidence: EvidenceMap = field(default_factory=EvidenceMap)
    source_identities: tuple[SourceIdentity, ...] = ()

    def render_back(self, view: OrthographicView) -> tuple[tuple[Pixel, ...], ...]:
        return render_view(self.asset, view)

    def assess(self, policy: AcceptancePolicy = AcceptancePolicy()) -> AcceptanceDecision:
        """Evaluate the reconstruction against an explicit publication gate."""

        checks = (
            (len(self.unresolved) > policy.max_unresolved, "unresolved candidate cells exceed policy"),
            (
                (self.report.material_conflicts + self.report.visible_material_conflicts)
                > policy.max_material_conflicts,
                "material conflicts exceed policy",
            ),
            (self.report.conflicting_rays > policy.max_conflicting_rays, "conflicting rays exceed policy"),
            (self.report.missing_pixels > policy.max_missing_pixels, "missing pixels exceed policy"),
            (self.report.extra_pixels > policy.max_extra_pixels, "extra pixels exceed policy"),
            (self.report.color_mismatches > policy.max_color_mismatches, "color mismatches exceed policy"),
            (self.report.silhouette_iou < policy.minimum_silhouette_iou, "silhouette IoU is below policy"),
        )
        reasons = [reason for failed, reason in checks if failed]
        if policy.require_source_identities and not self.source_identities:
            reasons.append("source identities are required")
        return AcceptanceDecision(
            accepted=not reasons,
            reasons=tuple(reasons),
            asset_digest=self.asset.content_digest,
            evidence_digest=self.evidence.digest,
            report=self.report,
        )




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


def render_cells(asset: VoxelAsset, view: OrthographicView) -> tuple[tuple[Cell | None, ...], ...]:
    """Render the nearest occupied cell per calibrated image ray."""

    rows: list[list[Cell | None]] = [
        [None for _column in range(view.width)]
        for _row in range(view.height)
    ]
    depths: list[list[int | None]] = [
        [None for _column in range(view.width)]
        for _row in range(view.height)
    ]
    for cell, _material in asset.iter_voxels():
        projection = view.project_cell(cell)
        if projection is None:
            continue
        column, row, depth = projection
        previous_depth = depths[row][column]
        if previous_depth is None or depth < previous_depth:
            depths[row][column] = depth
            rows[row][column] = cell
    return tuple(tuple(row) for row in rows)


def render_view(asset: VoxelAsset, view: OrthographicView) -> tuple[tuple[Pixel, ...], ...]:
    """Render the nearest voxel per calibrated image ray as palette indices."""

    return tuple(
        tuple(None if cell is None else asset.voxels[cell] for cell in row)
        for row in render_cells(asset, view)
    )


def reconstruct_views(
    views: Sequence[OrthographicView],
    palette: Sequence[Color],
    settings: ReconstructionSettings = ReconstructionSettings(),
    *,
    source_provenance: Mapping[str, str] | None = None,
    source_identities: Sequence[SourceIdentity] = (),
) -> ReconstructionResult:
    """Reconstruct a bounded voxel asset from one to six orthographic views.

    Colored pixels create occupied-ray constraints. Transparent pixels create
    empty-ray constraints. ``SURFACE`` chooses the nearest viable cell,
    ``LAYERED`` follows each per-pixel depth interval, ``SHELL`` keeps a
    bounded front shell, and ``VOLUME`` retains every viable cell. Viable but
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

    source_by_id: dict[str, SourceIdentity] = {}
    view_sources = tuple(
        source
        for view in normalized_views
        for source in (
            view.source_identity,
            None if view.depth_observation is None else view.depth_observation.source_identity,
            None if view.part_mask is None else view.part_mask.source_identity,
        )
        if source is not None
    )
    for source in tuple(source_identities) + view_sources:
        if not isinstance(source, SourceIdentity):
            raise TypeError("source_identities must contain SourceIdentity values")
        previous = source_by_id.get(source.source_id)
        if previous is not None and previous != source:
            raise ValueError(f"source identity changed for source ID: {source.source_id!r}")
        source_by_id[source.source_id] = source
    normalized_sources = tuple(sorted(source_by_id.values(), key=lambda source: source.key))

    forbidden: set[Cell] = set()
    forbidden_masks: dict[Cell, int] = {}
    source_pixels = 0
    source_names_by_bit: dict[int, str] = {}
    for view_index, view in enumerate(normalized_views):
        source_bit = 1 << view_index
        source_names_by_bit[source_bit] = view.name
        for row in range(view.height):
            for column in range(view.width):
                pixel = view.pixel(column, row)
                if pixel is None:
                    for depth in view.depth_indices(column, row):
                        cell = view.cell_at(column, row, depth)
                        forbidden.add(cell)
                        forbidden_masks[cell] = forbidden_masks.get(cell, 0) | source_bit
                    continue
                if pixel >= len(normalized_palette):
                    raise ValueError(f"view pixel palette index out of range: {pixel}")
                source_pixels += 1

    rays: list[_Ray] = []
    possible: set[Cell] = set()
    possible_masks: dict[Cell, int] = {}
    for view_index, view in enumerate(normalized_views):
        source_bit = 1 << view_index
        for row in range(view.height):
            for column in range(view.width):
                pixel = view.pixel(column, row)
                if pixel is None:
                    continue
                candidate_cells = []
                for depth in view.depth_indices(column, row):
                    cell = view.cell_at(column, row, depth)
                    if cell not in forbidden:
                        candidate_cells.append(cell)
                        possible_masks[cell] = possible_masks.get(cell, 0) | source_bit
                candidates = tuple(candidate_cells)
                rays.append(_Ray(view, column, row, pixel, candidates))
                possible.update(candidates)

    votes: dict[Cell, list[tuple[int, int, str]]] = {}
    part_votes: dict[Cell, set[str]] = {}
    conflicts: list[tuple[str, int, int]] = []
    for ray in sorted(rays, key=lambda item: (item.view.priority, item.view.name, item.row, item.column)):
        if not ray.candidates:
            conflicts.append((ray.view.name, ray.column, ray.row))
            continue
        if settings.mode in ("VOLUME", "LAYERED"):
            selected = ray.candidates
        elif settings.mode == "SHELL":
            selected = ray.candidates[: settings.shell_thickness]
        else:
            selected = ray.candidates[:1]
        part = ray.view.part_at(ray.column, ray.row)
        for cell in selected:
            votes.setdefault(cell, []).append((ray.view.priority, ray.color, ray.view.name))
            if part is not None:
                part_votes.setdefault(cell, set()).add(part)

    occupied: dict[Cell, int] = {}
    conflict_cells: set[Cell] = set()
    material_conflicts = 0
    for cell in sorted(votes):
        entries = votes[cell]
        colors = {color for _priority, color, _source in entries}
        if len(colors) > 1:
            material_conflicts += 1
            conflict_cells.add(cell)
        counts: dict[int, int] = {}
        for _priority, color, _source in entries:
            counts[color] = counts.get(color, 0) + 1
        occupied[cell] = min(counts, key=lambda color: (-counts[color], color))
    part_assignments = {
        cell: min(parts)
        for cell, parts in sorted(part_votes.items())
        if cell in occupied
    }
    depth_digests = tuple(
        sorted(
            view.depth_observation.digest
            for view in normalized_views
            if view.depth_observation is not None
        )
    )
    part_digests = tuple(
        sorted(
            view.part_mask.digest
            for view in normalized_views
            if view.part_mask is not None
        )
    )
    provenance = {
        "reconstruction": "orthographic",
        "view_count": str(len(normalized_views)),
        "view_names": ",".join(view.name for view in normalized_views),
        "mode": settings.mode,
        "material_policy": settings.material_policy,
    }
    if depth_digests:
        provenance["depth_observation_digests"] = ",".join(depth_digests)
    if part_digests:
        provenance["part_mask_digests"] = ",".join(part_digests)
    if part_assignments:
        provenance["part_assignments"] = json.dumps(
            [[list(cell), part] for cell, part in sorted(part_assignments.items())],
            sort_keys=True,
            separators=(",", ":"),
        )
    if normalized_sources:
        provenance["source_identity_digest"] = sha256_json(
            [source.to_dict() for source in normalized_sources]
        )
    if source_provenance is not None:
        if not isinstance(source_provenance, Mapping):
            raise TypeError("source_provenance must be a mapping")
        for key, value in sorted(source_provenance.items(), key=lambda item: str(item[0])):
            normalized_key = str(key)
            if normalized_key in provenance:
                raise ValueError(f"source provenance key is reserved: {normalized_key!r}")
            provenance[normalized_key] = str(value)
    asset = VoxelAsset(
        palette=list(normalized_palette),
        voxels=occupied,
        part_assignments=part_assignments,
        provenance=provenance,
    )
    visible_material_conflicts = 0
    if settings.material_policy == "VISIBLE_ONLY":
        visible_votes: dict[Cell, list[tuple[int, int, str]]] = {}
        for view in normalized_views:
            rendered_cells = render_cells(asset, view)
            for row in range(view.height):
                for column in range(view.width):
                    source_pixel = view.pixel(column, row)
                    cell = rendered_cells[row][column]
                    if source_pixel is not None and cell is not None:
                        visible_votes.setdefault(cell, []).append(
                            (view.priority, source_pixel, view.name)
                        )
        for cell in sorted(visible_votes):
            entries = visible_votes[cell]
            if len({color for _priority, color, _source in entries}) > 1:
                visible_material_conflicts += 1
            counts: dict[int, int] = {}
            for _priority, color, _source in entries:
                counts[color] = counts.get(color, 0) + 1
            asset.voxels[cell] = min(counts, key=lambda color: (-counts[color], color))

    def source_names(mask: int) -> tuple[str, ...]:
        return tuple(
            source_names_by_bit[bit]
            for bit in sorted(source_names_by_bit)
            if mask & bit
        )

    evidence_cells: dict[Cell, EvidenceCell] = {}
    for cell, mask in forbidden_masks.items():
        evidence_cells[cell] = EvidenceCell(
            state="FREE",
            source_mask=mask,
            confidence=1.0,
            sources=source_names(mask),
        )
    for cell, mask in possible_masks.items():
        if cell in forbidden_masks:
            continue
        entries = votes.get(cell, [])
        if cell in conflict_cells:
            state = "CONFLICT"
        elif cell in occupied:
            state = "OCCUPIED"
        else:
            state = "UNKNOWN"
        confidence = min(1.0, len(entries) / len(normalized_views)) if entries else None
        evidence_cells[cell] = EvidenceCell(
            state=state,
            source_mask=mask,
            confidence=confidence,
            sources=tuple(sorted({source for _priority, _color, source in entries})),
        )
    evidence = EvidenceMap.from_mapping(evidence_cells, normalized_sources)

    missing_pixels = 0
    extra_pixels = 0
    color_mismatches = 0
    silhouette_intersection = 0
    silhouette_union = 0
    for view in normalized_views:
        metrics = compare_indexed_views(view.pixels, render_view(asset, view))
        missing_pixels += metrics.missing_pixels
        extra_pixels += metrics.extra_pixels
        color_mismatches += metrics.color_mismatches
        silhouette_intersection += metrics.intersection_pixels
        silhouette_union += metrics.union_pixels
    silhouette_iou = (
        silhouette_intersection / silhouette_union if silhouette_union else 1.0
    )

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
        silhouette_intersection=silhouette_intersection,
        silhouette_union=silhouette_union,
        silhouette_iou=silhouette_iou,
        visible_material_conflicts=visible_material_conflicts,
    )
    return ReconstructionResult(
        asset=asset,
        unresolved=unresolved,
        report=report,
        evidence=evidence,
        source_identities=normalized_sources,
    )

__all__ = [
    "AXES",
    "AcceptanceDecision",
    "AcceptancePolicy",
    "DIRECTIONS",
    "OrthographicView",
    "Pixel",
    "ReconstructionReport",
    "ReconstructionResult",
    "ReconstructionSettings",
    "render_view",
    "render_cells",
    "reconstruct_views",
]
