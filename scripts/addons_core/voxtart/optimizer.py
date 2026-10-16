# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""V-Optimizer-style voxel-to-game-mesh orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .mesh_optimize import MeshOptimizationReport, optimize_mesh_data
from .meshing import MeshData, scaled_mesh
from .voxel import VoxelAsset


OPTIMIZER_BACKENDS = ("greedy", "marching")


def _flag(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"optimizer {field} must be a boolean")
    return value


@dataclass(frozen=True, slots=True)
class OptimizationSettings:
    """Stable mesh-generation and cleanup choices for one optimization pass."""

    backend: str = "greedy"
    weld_vertices: bool = True
    remove_degenerate: bool = True
    remove_duplicate: bool = True
    triangulate: bool = False
    compact_materials: bool = True

    def __post_init__(self) -> None:
        backend = str(self.backend or "").lower()
        if backend not in OPTIMIZER_BACKENDS:
            raise ValueError(f"unsupported optimizer backend: {self.backend!r}")
        object.__setattr__(self, "backend", backend)
        for field_name in (
            "weld_vertices",
            "remove_degenerate",
            "remove_duplicate",
            "triangulate",
            "compact_materials",
        ):
            _flag(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class OptimizationReport:
    """Input/output counts and cleanup metrics for a voxel optimization pass."""

    backend: str
    source_voxels: int
    source_vertices: int
    target_vertices: int
    source_faces: int
    target_faces: int
    removed_degenerate_faces: int
    removed_duplicate_faces: int
    triangulated_faces: int
    materials_before: int
    materials_after: int
    material_remap: tuple[int, ...]

    @property
    def vertex_reduction_ratio(self) -> float:
        return _reduction_ratio(self.source_vertices, self.target_vertices)

    @property
    def face_reduction_ratio(self) -> float:
        return _reduction_ratio(self.source_faces, self.target_faces)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["material_remap"] = list(self.material_remap)
        value["vertex_reduction_ratio"] = self.vertex_reduction_ratio
        value["face_reduction_ratio"] = self.face_reduction_ratio
        return value


def _reduction_ratio(source: int, target: int) -> float:
    if source <= 0:
        return 0.0
    return round(1.0 - (float(target) / float(source)), 6)


def _report(backend: str, source_voxels: int, report: MeshOptimizationReport) -> OptimizationReport:
    return OptimizationReport(
        backend=backend,
        source_voxels=source_voxels,
        source_vertices=report.source_vertices,
        target_vertices=report.target_vertices,
        source_faces=report.source_faces,
        target_faces=report.target_faces,
        removed_degenerate_faces=report.removed_degenerate_faces,
        removed_duplicate_faces=report.removed_duplicate_faces,
        triangulated_faces=report.triangulated_faces,
        materials_before=report.materials_before,
        materials_after=report.materials_after,
        material_remap=report.material_remap,
    )


def optimize_asset(
    asset: VoxelAsset,
    settings: OptimizationSettings = OptimizationSettings(),
) -> tuple[MeshData, OptimizationReport]:
    """Generate and clean a deterministic game-ready mesh from a voxel asset."""

    if not isinstance(asset, VoxelAsset):
        raise TypeError("optimizer expects a VoxelAsset")
    if not isinstance(settings, OptimizationSettings):
        raise TypeError("optimizer settings must be OptimizationSettings")
    source_mesh = scaled_mesh(asset, backend=settings.backend)
    optimized_mesh, mesh_report = optimize_mesh_data(
        source_mesh,
        weld_vertices=settings.weld_vertices,
        remove_degenerate=settings.remove_degenerate,
        remove_duplicate=settings.remove_duplicate,
        triangulate=settings.triangulate,
        compact_materials=settings.compact_materials,
    )
    return optimized_mesh, _report(settings.backend, len(asset.voxels), mesh_report)


__all__ = [
    "OPTIMIZER_BACKENDS",
    "OptimizationReport",
    "OptimizationSettings",
    "optimize_asset",
]
