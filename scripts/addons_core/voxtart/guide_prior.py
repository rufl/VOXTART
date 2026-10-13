# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic guide-volume refinement for sprite reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Literal, Sequence

from .provenance import sha256_json
from .reconstruct import ReconstructionResult
from .voxel import Cell, Color, VoxelAsset
GuideMode = Literal["OBSERVED_ONLY", "ALLOW_HIDDEN"]

MAX_GUIDE_CELLS = 2_000_000
Point = tuple[float, float, float]
Face = tuple[int, ...]


def _point(value: Iterable[float]) -> Point:
    result = tuple(float(item) for item in value)
    if len(result) != 3 or any(not math.isfinite(item) for item in result):
        raise ValueError(f"mesh guide vertices must be three finite values: {value!r}")
    return result  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class MeshGuide:
    """Portable triangle mesh input for deterministic guide voxelization."""

    vertices: tuple[Point, ...]
    faces: tuple[Face, ...]
    palette: tuple[Color, ...] = ((0.8, 0.8, 0.8, 1.0),)
    material_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        vertices = tuple(_point(vertex) for vertex in self.vertices)
        if not vertices:
            raise ValueError("mesh guide requires at least one vertex")
        faces: list[Face] = []
        for face in self.faces:
            normalized = tuple(face)
            if len(normalized) < 3:
                raise ValueError("mesh guide faces require at least three indices")
            if any(isinstance(index, bool) or not isinstance(index, int) for index in normalized):
                raise ValueError("mesh guide face indices must be integers")
            if any(index < 0 or index >= len(vertices) for index in normalized):
                raise ValueError("mesh guide face index is outside the vertex buffer")
            faces.append(normalized)
        if not faces:
            raise ValueError("mesh guide requires at least one face")
        palette = tuple(VoxelAsset(palette=list(self.palette)).palette)
        material_indices = tuple(self.material_indices)
        if material_indices and len(material_indices) != len(faces):
            raise ValueError("mesh guide material_indices must match faces")
        if any(
            isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(palette)
            for index in material_indices
        ):
            raise ValueError("mesh guide material index is outside the palette")
        object.__setattr__(self, "vertices", vertices)
        object.__setattr__(self, "faces", tuple(faces))
        object.__setattr__(self, "palette", palette)
        object.__setattr__(self, "material_indices", material_indices)

    def to_dict(self) -> dict[str, object]:
        return {
            "vertices": [list(vertex) for vertex in self.vertices],
            "faces": [list(face) for face in self.faces],
            "palette": [list(color) for color in self.palette],
            "material_indices": list(self.material_indices),
        }


def _cross(left: Point, right: Point) -> Point:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(left: Point, right: Point) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _subtract(left: Point, right: Point) -> Point:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _ray_triangle_distance(origin: Point, triangle: tuple[Point, Point, Point], epsilon: float) -> float | None:
    first, second, third = triangle
    edge_one = _subtract(second, first)
    edge_two = _subtract(third, first)
    direction = (1.0, 0.0, 0.0)
    perpendicular = _cross(direction, edge_two)
    determinant = _dot(edge_one, perpendicular)
    if abs(determinant) <= epsilon:
        return None
    inverse = 1.0 / determinant
    offset = _subtract(origin, first)
    u = inverse * _dot(offset, perpendicular)
    if u < -epsilon or u > 1.0 + epsilon:
        return None
    cross_offset = _cross(offset, edge_one)
    v = inverse * _dot(direction, cross_offset)
    if v < -epsilon or u + v > 1.0 + epsilon:
        return None
    distance = inverse * _dot(edge_two, cross_offset)
    return distance if distance > epsilon else None


def _inside_mesh(point: Point, triangles: Sequence[tuple[Point, Point, Point]], step: float) -> bool:
    origin = (point[0], point[1] + step * 0.173, point[2] + step * 0.297)
    epsilon = max(step * 1.0e-9, 1.0e-12)
    intersections = sum(
        _ray_triangle_distance(origin, triangle, epsilon) is not None
        for triangle in triangles
    )
    return bool(intersections & 1)


def voxelize_mesh_guide(
    guide: MeshGuide,
    *,
    grid_size: float = 1.0,
    mode: str = "SOLID",
    shell_thickness: int = 1,
    max_cells: int = MAX_GUIDE_CELLS,
) -> VoxelAsset:
    """Voxelize a closed mesh guide before evidence reconciliation."""

    if not isinstance(guide, MeshGuide):
        raise TypeError("mesh guide voxelization expects a MeshGuide")
    try:
        grid_size = float(grid_size)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("mesh guide grid_size must be finite and positive") from error
    if not math.isfinite(grid_size) or grid_size <= 0.0:
        raise ValueError("mesh guide grid_size must be finite and positive")
    if isinstance(max_cells, bool) or not isinstance(max_cells, int) or max_cells <= 0:
        raise ValueError("mesh guide max_cells must be a positive integer")
    triangles: list[tuple[Point, Point, Point, int]] = []
    for face_index, face in enumerate(guide.faces):
        material_index = guide.material_indices[face_index] if guide.material_indices else 0
        for index in range(1, len(face) - 1):
            triangles.append(
                (
                    guide.vertices[face[0]],
                    guide.vertices[face[index]],
                    guide.vertices[face[index + 1]],
                    material_index,
                )
            )
    minimum = tuple(min(vertex[axis] for vertex in guide.vertices) for axis in range(3))
    maximum = tuple(max(vertex[axis] for vertex in guide.vertices) for axis in range(3))
    start = tuple(math.floor(value / grid_size) for value in minimum)
    stop = tuple(math.ceil(value / grid_size) for value in maximum)
    estimated_cells = math.prod(max(0, stop[axis] - start[axis]) for axis in range(3))
    if estimated_cells > max_cells:
        raise ValueError(
            f"mesh guide search volume is {estimated_cells:,} cells; "
            f"keep it below {max_cells:,} cells"
        )
    asset = VoxelAsset(
        grid_size=grid_size,
        palette=list(guide.palette),
        provenance={
            "guide_source": "MESH",
            "guide_mesh_digest": sha256_json(guide.to_dict()),
        },
    )
    triangle_centers = tuple(
        (
            ((triangle[0][0] + triangle[1][0] + triangle[2][0]) / 3.0,
             (triangle[0][1] + triangle[1][1] + triangle[2][1]) / 3.0,
             (triangle[0][2] + triangle[1][2] + triangle[2][2]) / 3.0),
            triangle[3],
        )
        for triangle in triangles
    )
    triangle_geometry = tuple(triangle[:3] for triangle in triangles)
    for x in range(start[0], stop[0]):
        for y in range(start[1], stop[1]):
            for z in range(start[2], stop[2]):
                point = ((x + 0.5) * grid_size, (y + 0.5) * grid_size, (z + 0.5) * grid_size)
                if not _inside_mesh(point, triangle_geometry, grid_size):
                    continue
                material_index = min(
                    triangle_centers,
                    key=lambda item: (
                        (item[0][0] - point[0]) ** 2
                        + (item[0][1] - point[1]) ** 2
                        + (item[0][2] - point[2]) ** 2,
                        item[1],
                    ),
                )[1]
                asset.set_voxel((x, y, z), material_index)
    if not asset.voxels:
        raise ValueError("mesh guide produced no occupied voxels at this grid size")
    from .voxelizer import apply_voxelizer_mode

    result = apply_voxelizer_mode(asset, mode=mode, shell_thickness=shell_thickness)
    result.provenance["guide_mesh_mode"] = str(mode).upper()
    result.provenance["guide_mesh_shell_thickness"] = str(shell_thickness)
    return result


@dataclass(frozen=True, slots=True)
class GuidePriorResult:
    asset: VoxelAsset
    added_cells: tuple[Cell, ...]
    rejected_free_cells: tuple[Cell, ...]
    recolored_cells: tuple[Cell, ...]


def apply_guide_prior(
    result: ReconstructionResult,
    guide: VoxelAsset,
    *,
    mode: GuideMode = "ALLOW_HIDDEN",
) -> GuidePriorResult:
    """Merge a pre-voxelized guide without overriding free evidence."""

    if not isinstance(result, ReconstructionResult):
        raise TypeError("guide refinement expects a ReconstructionResult")
    if not isinstance(guide, VoxelAsset):
        raise TypeError("guide refinement expects a VoxelAsset guide")
    normalized_mode = str(mode).upper()
    if normalized_mode not in ("OBSERVED_ONLY", "ALLOW_HIDDEN"):
        raise ValueError("guide mode must be OBSERVED_ONLY or ALLOW_HIDDEN")
    free_cells = {cell for cell, evidence in result.evidence.cells if evidence.state == "FREE"}
    evidence_cells = set(cell for cell, _evidence in result.evidence.cells)
    asset = VoxelAsset(
        grid_size=result.asset.grid_size,
        palette=list(result.asset.palette),
        voxels=dict(result.asset.voxels),
        part_assignments=dict(result.asset.part_assignments),
        sockets=list(result.asset.sockets),
        provenance=dict(result.asset.provenance),
    )
    added: list[Cell] = []
    rejected: list[Cell] = []
    recolored: list[Cell] = []

    def guide_index(index: int) -> int:
        color = guide.palette[index]
        try:
            return asset.palette.index(color)
        except ValueError:
            asset.palette.append(color)
            return len(asset.palette) - 1

    for cell, palette_index in guide.iter_voxels():
        target_index = guide_index(palette_index)
        if cell in free_cells:
            rejected.append(cell)
            continue
        if normalized_mode == "OBSERVED_ONLY" and cell not in evidence_cells:
            continue
        existing = asset.voxels.get(cell)
        if existing is None:
            asset.voxels[cell] = target_index
            added.append(cell)
        elif existing != target_index:
            asset.voxels[cell] = target_index
            recolored.append(cell)
        if cell in guide.part_assignments:
            asset.part_assignments[cell] = guide.part_assignments[cell]
    if asset.part_assignments:
        asset.provenance["part_assignments"] = VoxelAsset._part_payload(asset.part_assignments)
    else:
        asset.provenance.pop("part_assignments", None)
    asset.provenance.update({
        "guide_prior": "VOXEL_GUIDE",
        "guide_mode": normalized_mode,
        "guide_digest": guide.content_digest,
        "guide_added": str(len(added)),
        "guide_rejected_free": str(len(rejected)),
    })
    return GuidePriorResult(asset, tuple(sorted(added)), tuple(sorted(rejected)), tuple(sorted(recolored)))


__all__ = [
    "GuidePriorResult",
    "MAX_GUIDE_CELLS",
    "MeshGuide",
    "apply_guide_prior",
    "voxelize_mesh_guide",
]
