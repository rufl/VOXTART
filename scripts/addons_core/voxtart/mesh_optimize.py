# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic cleanup and material compaction for exported voxel meshes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .meshing import MeshData


@dataclass(frozen=True, slots=True)
class MeshOptimizationReport:
    """Counts and material mapping produced by export cleanup."""

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

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["material_remap"] = list(self.material_remap)
        return value


def _face_key(face: tuple[int, ...], material: int) -> tuple[int, tuple[int, ...]]:
    return material, tuple(sorted(face))


def optimize_mesh_data(
    mesh: MeshData,
    *,
    weld_vertices: bool = True,
    remove_degenerate: bool = True,
    remove_duplicate: bool = True,
    triangulate: bool = False,
    compact_materials: bool = True,
) -> tuple[MeshData, MeshOptimizationReport]:
    """Return a stable, smaller mesh buffer without changing its surface cells.

    The input mesh is expected to have one material index per face.  Quads are
    retained by default because greedy voxel meshing is already quad-efficient;
    triangulation is available for exporters or downstream tools that require
    triangles.
    """

    source_vertices_raw = list(mesh.vertices)
    faces = list(mesh.faces)
    materials = list(mesh.materials)
    if len(materials) != len(faces):
        raise ValueError("mesh materials must contain one index per face")

    source_vertices = len(source_vertices_raw)
    source_faces = len(faces)
    materials_before = len(set(materials))

    if weld_vertices:
        working_vertices: list[tuple[float, float, float]] = []
        welded_indices: dict[tuple[float, float, float], int] = {}
        vertex_remap: list[int] = []
        for vertex in source_vertices_raw:
            normalized = tuple(float(axis) for axis in vertex)
            index = welded_indices.get(normalized)
            if index is None:
                index = len(working_vertices)
                welded_indices[normalized] = index
                working_vertices.append(normalized)
            vertex_remap.append(index)
    else:
        working_vertices = source_vertices_raw
        vertex_remap = list(range(source_vertices))

    remapped_faces: list[tuple[int, ...]] = []
    for face in faces:
        normalized = tuple(int(index) for index in face)
        if any(index < 0 or index >= source_vertices for index in normalized):
            raise ValueError("mesh face references a vertex outside the vertex buffer")
        remapped_faces.append(tuple(vertex_remap[index] for index in normalized))

    kept: list[tuple[tuple[int, ...], int]] = []
    seen_faces: set[tuple[int, tuple[int, ...]]] = set()
    removed_degenerate = 0
    removed_duplicate = 0
    triangulated_faces = 0

    for normalized, material in zip(remapped_faces, materials):
        material = int(material)
        if remove_degenerate and len(set(normalized)) < 3:
            removed_degenerate += 1
            continue
        if remove_duplicate:
            key = _face_key(normalized, material)
            if key in seen_faces:
                removed_duplicate += 1
                continue
            seen_faces.add(key)
        if triangulate and len(normalized) > 3:
            for index in range(1, len(normalized) - 1):
                kept.append(((normalized[0], normalized[index], normalized[index + 1]), material))
            triangulated_faces += 1
        else:
            kept.append((normalized, material))

    material_remap: tuple[int, ...]
    if compact_materials:
        material_remap = tuple(sorted({material for _face, material in kept}))
        remap = {original: index for index, original in enumerate(material_remap)}
        kept = [(face, remap[material]) for face, material in kept]
        materials_after = len(material_remap)
    else:
        material_remap = ()
        materials_after = materials_before

    output_faces = [face for face, _material in kept]
    if weld_vertices:
        used = sorted({index for face in output_faces for index in face})
        compact_indices = {old: new for new, old in enumerate(used)}
        output_vertices = [working_vertices[index] for index in used]
        output_faces = [
            tuple(compact_indices[index] for index in face)
            for face in output_faces
        ]
    else:
        output_vertices = working_vertices

    output_materials = [material for _face, material in kept]
    output = MeshData(output_vertices, output_faces, output_materials)
    report = MeshOptimizationReport(
        source_vertices=source_vertices,
        target_vertices=len(output_vertices),
        source_faces=source_faces,
        target_faces=len(output_faces),
        removed_degenerate_faces=removed_degenerate,
        removed_duplicate_faces=removed_duplicate,
        triangulated_faces=triangulated_faces,
        materials_before=materials_before,
        materials_after=materials_after,
        material_remap=material_remap,
    )
    return output, report


__all__ = ["MeshOptimizationReport", "optimize_mesh_data"]
