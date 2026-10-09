# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic greedy and marching voxel surface meshing."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import product

from .voxel import Cell, VoxelAsset


@dataclass(slots=True)
class MeshData:
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, ...]]
    materials: list[int]


def greedy_mesh(asset: VoxelAsset) -> MeshData:
    """Build exposed faces, merging coplanar same-material cells greedily.

    The scan order is explicit so equal input assets produce byte-for-byte stable
    serialized mesh buffers. Faces are wound outward for all six axis directions.
    """

    occupied = asset.voxels
    if not occupied:
        return MeshData([], [], [])

    # (axis, sign, plane) -> (u, v) -> palette index.
    planes: dict[tuple[int, int, int], dict[tuple[int, int], int]] = {}
    for cell, palette_index in asset.iter_voxels():
        for axis in range(3):
            u_axis = (axis + 1) % 3
            v_axis = (axis + 2) % 3
            for sign in (-1, 1):
                neighbor = list(cell)
                neighbor[axis] += sign
                if tuple(neighbor) in occupied:
                    continue
                plane = cell[axis] + (1 if sign > 0 else 0)
                key = (axis, sign, plane)
                planes.setdefault(key, {})[(cell[u_axis], cell[v_axis])] = palette_index

    vertices: list[tuple[int, int, int]] = []
    vertex_indices: dict[tuple[int, int, int], int] = {}
    faces: list[tuple[int, int, int, int]] = []
    materials: list[int] = []

    def vertex_index(coordinate: tuple[int, int, int]) -> int:
        index = vertex_indices.get(coordinate)
        if index is None:
            index = len(vertices)
            vertex_indices[coordinate] = index
            vertices.append(coordinate)
        return index

    for axis, sign, plane in sorted(planes):
        mask = planes[(axis, sign, plane)]
        u_axis = (axis + 1) % 3
        v_axis = (axis + 2) % 3
        while mask:
            u0, v0 = min(mask, key=lambda uv: (uv[1], uv[0]))
            palette_index = mask[(u0, v0)]

            width = 1
            while mask.get((u0 + width, v0)) == palette_index:
                width += 1

            height = 1
            while True:
                row = range(u0, u0 + width)
                if all(mask.get((u, v0 + height)) == palette_index for u in row):
                    height += 1
                else:
                    break

            for v in range(v0, v0 + height):
                for u in range(u0, u0 + width):
                    del mask[(u, v)]

            base = [0, 0, 0]
            base[axis] = plane
            base[u_axis] = u0
            base[v_axis] = v0
            origin = tuple(base)

            u_end = list(origin)
            u_end[u_axis] += width
            v_end = list(origin)
            v_end[v_axis] += height
            far_corner = list(u_end)
            far_corner[v_axis] += height

            if sign > 0:
                corners = (origin, tuple(u_end), tuple(far_corner), tuple(v_end))
            else:
                corners = (origin, tuple(v_end), tuple(far_corner), tuple(u_end))

            faces.append(tuple(vertex_index(corner) for corner in corners))
            materials.append(palette_index)

    return MeshData(vertices, faces, materials)


_CUBE_CORNERS = (
    (0, 0, 0),
    (1, 0, 0),
    (0, 1, 0),
    (1, 1, 0),
    (0, 0, 1),
    (1, 0, 1),
    (0, 1, 1),
    (1, 1, 1),
)
_TETRAHEDRA = (
    (0, 1, 3, 7),
    (0, 3, 2, 7),
    (0, 2, 6, 7),
    (0, 6, 4, 7),
    (0, 4, 5, 7),
    (0, 5, 1, 7),
)


def _lattice_value(occupied: dict[Cell, int], point: Cell) -> float:
    count = 0
    for offset in product((-1, 0), repeat=3):
        if (
            point[0] + offset[0],
            point[1] + offset[1],
            point[2] + offset[2],
        ) in occupied:
            count += 1
    return count / 8.0


def _material_at_point(point: tuple[float, float, float], occupied: dict[Cell, int]) -> int:
    base = tuple(math.floor(coordinate) for coordinate in point)
    candidates: list[tuple[float, Cell, int]] = []
    for cell in product(
        range(base[0] - 1, base[0] + 2),
        range(base[1] - 1, base[1] + 2),
        range(base[2] - 1, base[2] + 2),
    ):
        palette_index = occupied.get(cell)
        if palette_index is None:
            continue
        distance = sum(
            (point[axis] - (cell[axis] + 0.5)) ** 2
            for axis in range(3)
        )
        candidates.append((distance, cell, palette_index))
    if not candidates:
        return 0
    return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]


def marching_voxel_mesh(asset: VoxelAsset, *, iso: float = 0.5) -> MeshData:
    """Build a smooth triangular surface with deterministic marching tetrahedra.

    Lattice values are the local occupancy average of the eight neighboring
    voxels. Tetrahedra avoid a lookup-table ambiguity while retaining stable
    topology for the same sparse asset.
    """

    if not 0.0 < iso < 1.0:
        raise ValueError("marching iso value must be in the open interval (0, 1)")
    if not asset.voxels:
        return MeshData([], [], [])

    minimum, maximum = asset.bounds  # type: ignore[misc]
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    materials: list[int] = []
    edge_vertices: dict[tuple[Cell, Cell], int] = {}

    def edge_vertex(
        first: Cell,
        second: Cell,
        first_value: float,
        second_value: float,
    ) -> int:
        key = tuple(sorted((first, second)))
        existing = edge_vertices.get(key)
        if existing is not None:
            return existing
        if abs(second_value - first_value) < 1.0e-12:
            factor = 0.5
        else:
            factor = (iso - first_value) / (second_value - first_value)
            factor = max(0.0, min(1.0, factor))
        coordinate = tuple(
            first[axis] + (second[axis] - first[axis]) * factor
            for axis in range(3)
        )
        index = len(vertices)
        edge_vertices[key] = index
        vertices.append(coordinate)
        return index

    def add_triangle(
        triangle: list[int],
        inside_center: tuple[float, float, float],
        outside_center: tuple[float, float, float],
    ) -> None:
        if len(set(triangle)) != 3:
            return
        first, second, third = (vertices[index] for index in triangle)
        normal = (
            (second[1] - first[1]) * (third[2] - first[2])
            - (second[2] - first[2]) * (third[1] - first[1]),
            (second[2] - first[2]) * (third[0] - first[0])
            - (second[0] - first[0]) * (third[2] - first[2]),
            (second[0] - first[0]) * (third[1] - first[1])
            - (second[1] - first[1]) * (third[0] - first[0]),
        )
        if sum(component * component for component in normal) < 1.0e-16:
            return
        toward_outside = tuple(
            outside_center[axis] - inside_center[axis] for axis in range(3)
        )
        if sum(normal[axis] * toward_outside[axis] for axis in range(3)) < 0.0:
            triangle[1], triangle[2] = triangle[2], triangle[1]
        faces.append(tuple(triangle))
        center = tuple(
            sum(vertices[index][axis] for index in triangle) / 3.0
            for axis in range(3)
        )
        materials.append(_material_at_point(center, asset.voxels))

    origins = (
        range(minimum[axis] - 1, maximum[axis] + 2)
        for axis in range(3)
    )
    for origin in product(*origins):
        cube_points = [
            tuple(origin[axis] + corner[axis] for axis in range(3))
            for corner in _CUBE_CORNERS
        ]
        cube_values = [_lattice_value(asset.voxels, point) for point in cube_points]
        if all(value < iso for value in cube_values) or all(value >= iso for value in cube_values):
            continue
        for tetrahedron in _TETRAHEDRA:
            inside = [index for index in tetrahedron if cube_values[index] >= iso]
            outside = [index for index in tetrahedron if cube_values[index] < iso]
            if not inside or not outside:
                continue
            inside_center = tuple(
                sum(cube_points[index][axis] for index in inside) / len(inside)
                for axis in range(3)
            )
            outside_center = tuple(
                sum(cube_points[index][axis] for index in outside) / len(outside)
                for axis in range(3)
            )

            def crossing(first: int, second: int) -> int:
                return edge_vertex(
                    cube_points[first],
                    cube_points[second],
                    cube_values[first],
                    cube_values[second],
                )

            if len(inside) == 1:
                add_triangle(
                    [crossing(inside[0], index) for index in outside],
                    inside_center,
                    outside_center,
                )
            elif len(inside) == 3:
                add_triangle(
                    [crossing(outside[0], index) for index in inside],
                    inside_center,
                    outside_center,
                )
            else:
                first, second = inside
                first_outside, second_outside = outside
                quad = [
                    crossing(first, first_outside),
                    crossing(first, second_outside),
                    crossing(second, second_outside),
                    crossing(second, first_outside),
                ]
                add_triangle([quad[0], quad[1], quad[2]], inside_center, outside_center)
                add_triangle([quad[0], quad[2], quad[3]], inside_center, outside_center)

    return MeshData(vertices, faces, materials)


def scaled_mesh(asset: VoxelAsset, *, backend: str = "greedy") -> MeshData:
    """Return the selected voxel surface mesh in scene units."""

    if backend == "greedy":
        mesh = greedy_mesh(asset)
    elif backend == "marching":
        mesh = marching_voxel_mesh(asset)
    else:
        raise ValueError(f"unsupported mesh backend: {backend!r}")
    if asset.grid_size == 1.0:
        return mesh
    mesh.vertices = [
        tuple(round(coordinate * asset.grid_size, 9) for coordinate in vertex)
        for vertex in mesh.vertices
    ]
    return mesh
