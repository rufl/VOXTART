# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Blender-facing operators for the VOXTART voxel prototype."""

from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper, ImportHelper
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    IntVectorProperty,
    StringProperty,
)
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from .edit import connected_components, edit_asset
from .io import load_asset, save_asset
from .kitbash import find_best_match
from .meshing import scaled_mesh
from .optimize import DecimationPolicy, decimate
from .pick import raycast_voxel
from .voxel import VoxelAsset, VoxelSocket


ASSET_PROPERTY = "voxtart_asset_json"
SCHEMA_PROPERTY = "voxtart_schema_version"
MESH_BACKEND_PROPERTY = "voxtart_mesh_backend"
COMPONENT_SELECTION_PROPERTY = "voxtart_component_selection"
COMPONENT_OVERLAY_PROPERTY = "voxtart_component_overlay"
PICKED_CELL_PROPERTY = "voxtart_picked_cell"
PICKED_OVERLAY_PROPERTY = "voxtart_picked_overlay"
PROTECTED_OVERLAY_PROPERTY = "voxtart_protected_overlay"


def _store_asset(obj: bpy.types.Object, asset: VoxelAsset) -> None:
    obj[SCHEMA_PROPERTY] = asset.schema_version
    obj[ASSET_PROPERTY] = json.dumps(asset.to_dict(), sort_keys=True, separators=(",", ":"))


def _load_asset(obj: bpy.types.Object) -> VoxelAsset:
    raw = obj.get(ASSET_PROPERTY)
    if not isinstance(raw, str):
        raise ValueError("active object is not a VOXTART voxel asset")
    return VoxelAsset.from_dict(json.loads(raw))


def _new_mesh(name: str, asset: VoxelAsset, backend: str = "greedy") -> bpy.types.Mesh:
    mesh_data = scaled_mesh(asset, backend=backend)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(mesh_data.vertices, [], mesh_data.faces)
    mesh.update()
    for polygon, palette_index in zip(mesh.polygons, mesh_data.materials):
        polygon.material_index = palette_index
    return mesh


def _palette_materials(obj: bpy.types.Object, asset: VoxelAsset) -> None:
    obj.data.materials.clear()
    for index, color in enumerate(asset.palette):
        material = bpy.data.materials.get(f"VOXTART Palette {index}")
        if material is None:
            material = bpy.data.materials.new(f"VOXTART Palette {index}")
        material.diffuse_color = color
        obj.data.materials.append(material)


def _link_object(context: bpy.types.Context, obj: bpy.types.Object) -> None:
    collection = context.collection or context.scene.collection
    collection.objects.link(obj)
    for selected in context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _create_asset_object(context: bpy.types.Context, name: str, asset: VoxelAsset,
                         matrix_world=None) -> bpy.types.Object:
    obj = bpy.data.objects.new(name, _new_mesh(f"{name} Mesh", asset))
    if matrix_world is not None:
        obj.matrix_world = matrix_world
    _palette_materials(obj, asset)
    _store_asset(obj, asset)
    obj[MESH_BACKEND_PROPERTY] = "greedy"
    _link_object(context, obj)
    _update_visual_overlays(obj, asset)
    return obj


def _replace_asset_mesh(obj: bpy.types.Object, asset: VoxelAsset, backend: str | None = None) -> None:
    backend = backend or str(obj.get(MESH_BACKEND_PROPERTY, "greedy"))
    old_mesh = obj.data
    obj.data = _new_mesh(f"{obj.name} Mesh", asset, backend=backend)
    _palette_materials(obj, asset)
    _store_asset(obj, asset)
    obj[MESH_BACKEND_PROPERTY] = backend
    _update_visual_overlays(obj, asset)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)


def _overlay_cells(obj: bpy.types.Object, property_name: str) -> tuple[tuple[int, int, int], ...]:
    raw = obj.get(property_name)
    if raw is None:
        return ()
    encoded = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(encoded, list):
        raise ValueError(f"{property_name} must be a JSON list")
    cells = set()
    for cell in encoded:
        if not isinstance(cell, (list, tuple)) or len(cell) != 3:
            raise ValueError(f"{property_name} contains an invalid cell")
        normalized = tuple(int(axis) for axis in cell)
        cells.add(normalized)
    return tuple(sorted(cells))


def _wire_mesh(name: str, asset: VoxelAsset, cells) -> bpy.types.Mesh:
    corners = (
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 0, 1),
        (1, 1, 1),
        (0, 1, 1),
    )
    edge_pairs = (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )
    vertices = []
    edges = []
    for cell in sorted(set(cells)):
        offset = len(vertices)
        vertices.extend(
            tuple(
                (cell[axis] + corner[axis]) * asset.grid_size
                for axis in range(3)
            )
            for corner in corners
        )
        edges.extend((offset + first, offset + second) for first, second in edge_pairs)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, edges, [])
    mesh.update()
    return mesh


def _update_overlay(
    obj: bpy.types.Object,
    asset: VoxelAsset,
    property_name: str,
    cells,
    kind: str,
    color: tuple[float, float, float, float],
) -> None:
    overlay_name = obj.get(property_name)
    overlay = bpy.data.objects.get(overlay_name) if isinstance(overlay_name, str) else None
    mesh = _wire_mesh(f"{obj.name} {kind} Mesh", asset, cells)
    if overlay is None or overlay.type != 'MESH':
        overlay = bpy.data.objects.new(f"{obj.name} {kind} Overlay", mesh)
        collection = obj.users_collection[0] if obj.users_collection else bpy.context.scene.collection
        collection.objects.link(overlay)
        obj[property_name] = overlay.name
    else:
        old_mesh = overlay.data
        overlay.data = mesh
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
    overlay.parent = obj
    overlay.matrix_parent_inverse = Matrix.Identity(4)
    overlay.display_type = 'WIRE'
    overlay.hide_render = True
    overlay.hide_viewport = not bool(cells)
    overlay.color = color
    overlay["voxtart_overlay_source"] = obj.name
    overlay["voxtart_overlay_kind"] = kind
    overlay.select_set(False)


def _update_visual_overlays(obj: bpy.types.Object, asset: VoxelAsset | None = None) -> None:
    asset = asset or _load_asset(obj)
    selected = tuple(cell for cell in _overlay_cells(obj, COMPONENT_SELECTION_PROPERTY) if cell in asset.voxels)
    picked = tuple(cell for cell in _overlay_cells(obj, PICKED_CELL_PROPERTY) if cell in asset.voxels)
    protected = tuple(sorted(_protected_cells(obj)))
    _update_overlay(
        obj,
        asset,
        COMPONENT_OVERLAY_PROPERTY,
        selected,
        "Component",
        (0.1, 0.6, 1.0, 1.0),
    )
    _update_overlay(
        obj,
        asset,
        PROTECTED_OVERLAY_PROPERTY,
        protected,
        "Protected",
        (1.0, 0.15, 0.1, 1.0),
    )
    _update_overlay(
        obj,
        asset,
        PICKED_OVERLAY_PROPERTY,
        picked,
        "Picked",
        (1.0, 0.7, 0.05, 1.0),
    )


def _inside_bvh(tree: BVHTree, point: Vector, _minimum_x: float, maximum_x: float, step: float) -> bool:
    """Odd/even ray test with a deterministic ray origin inside the sample cell."""

    origin = Vector((point.x, point.y + step * 0.173, point.z + step * 0.297))
    direction = Vector((1.0, 0.0, 0.0))
    remaining = maximum_x - origin.x + step
    intersections = 0
    for _ in range(4096):
        if remaining <= 0.0:
            break
        location, _normal, triangle_index, distance = tree.ray_cast(origin, direction, remaining)
        if triangle_index is None or triangle_index < 0 or distance is None:
            break
        intersections += 1
        advance = max(step * 1.0e-5, 1.0e-7)
        origin = location + direction * advance
        remaining = maximum_x - origin.x + step
    return bool(intersections & 1)


def _voxelize_object(obj: bpy.types.Object, grid_size: float) -> VoxelAsset:
    if obj.type != 'MESH' or not obj.data.vertices or not obj.data.polygons:
        raise ValueError("active object must be a non-empty mesh")

    vertices = [vertex.co.copy() for vertex in obj.data.vertices]
    polygons = [list(polygon.vertices) for polygon in obj.data.polygons]
    tree = BVHTree.FromPolygons(vertices, polygons, epsilon=1.0e-6)
    bounds = [Vector(corner) for corner in obj.bound_box]
    minimum = Vector((min(corner[axis] for corner in bounds) for axis in range(3)))
    maximum = Vector((max(corner[axis] for corner in bounds) for axis in range(3)))
    start = tuple(math.floor(minimum[axis] / grid_size) for axis in range(3))
    stop = tuple(math.ceil(maximum[axis] / grid_size) for axis in range(3))

    asset = VoxelAsset(grid_size=grid_size, provenance={"source_object": obj.name})
    for x in range(start[0], stop[0]):
        for y in range(start[1], stop[1]):
            for z in range(start[2], stop[2]):
                point = Vector(((x + 0.5) * grid_size, (y + 0.5) * grid_size, (z + 0.5) * grid_size))
                if _inside_bvh(tree, point, minimum.x, maximum.x, grid_size):
                    asset.set_voxel((x, y, z))
    if not asset.voxels:
        raise ValueError("mesh produced no occupied voxels at this grid size")
    return asset
def _apply_edit(
    obj: bpy.types.Object,
    operation: str,
    callback,
    *,
    allow_protected: bool = False,
):
    asset = _load_asset(obj)
    report = edit_asset(
        asset,
        operation,
        callback,
        protected=_protected_cells(obj),
        allow_protected=allow_protected,
    )
    _replace_asset_mesh(obj, asset)
    obj["voxtart_edit_report"] = json.dumps(report.to_dict(), sort_keys=True)
    return report


def _edit_message(report) -> str:
    return (
        f"{report.operation}: +{report.added} -{report.removed} "
        f"painted {report.painted}, {report.after_voxels} voxels"
    )


class VOXTART_OT_add_voxel_cube(bpy.types.Operator):
    bl_idname = "voxtart.add_voxel_cube"
    bl_label = "Add Voxel Cube"
    bl_description = "Create a palette-backed sparse voxel cube"
    bl_options = {'REGISTER', 'UNDO'}

    size: IntProperty(name="Size", min=1, max=256, default=4)
    grid_size: FloatProperty(name="Grid Size", min=0.0001, default=1.0)

    def execute(self, context):
        asset = VoxelAsset.box(self.size, grid_size=self.grid_size)
        _create_asset_object(context, "Voxel Cube", asset)
        return {'FINISHED'}


class VOXTART_OT_voxelize_active(bpy.types.Operator):
    bl_idname = "voxtart.voxelize_active"
    bl_label = "Voxelize Selected Mesh"
    bl_description = "Convert the active mesh into a sparse voxel asset"
    bl_options = {'REGISTER', 'UNDO'}

    grid_size: FloatProperty(name="Grid Size", min=0.0001, default=1.0)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        source = context.active_object
        try:
            asset = _voxelize_object(source, self.grid_size)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        _create_asset_object(context, f"{source.name}_Voxel", asset, source.matrix_world.copy())
        return {'FINISHED'}
def _run_edit_operator(operator, context, operation: str, callback, allow_protected: bool):
    try:
        report = _apply_edit(
            context.active_object,
            operation,
            callback,
            allow_protected=allow_protected,
        )
    except (TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        operator.report({'ERROR'}, str(error))
        return {'CANCELLED'}
    operator.report({'INFO'}, _edit_message(report))
    return {'FINISHED'}


class VOXTART_OT_add_voxel(bpy.types.Operator):
    bl_idname = "voxtart.add_voxel"
    bl_label = "Add Voxel"
    bl_description = "Add or recolor one voxel on the active asset"
    bl_options = {'REGISTER', 'UNDO'}

    cell: IntVectorProperty(name="Cell", size=3, default=(0, 0, 0))
    palette_index: IntProperty(name="Palette Index", min=0, max=65535, default=0)
    allow_protected: BoolProperty(name="Edit Protected", default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        return _run_edit_operator(
            self,
            context,
            "add_voxel",
            lambda tx: tx.add(self.cell, self.palette_index),
            self.allow_protected,
        )


class VOXTART_OT_remove_voxel(bpy.types.Operator):
    bl_idname = "voxtart.remove_voxel"
    bl_label = "Remove Voxel"
    bl_description = "Remove one voxel from the active asset"
    bl_options = {'REGISTER', 'UNDO'}

    cell: IntVectorProperty(name="Cell", size=3, default=(0, 0, 0))
    allow_protected: BoolProperty(name="Edit Protected", default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        return _run_edit_operator(
            self,
            context,
            "remove_voxel",
            lambda tx: tx.remove(self.cell),
            self.allow_protected,
        )


class VOXTART_OT_fill_box(bpy.types.Operator):
    bl_idname = "voxtart.fill_box"
    bl_label = "Fill Box"
    bl_description = "Fill an inclusive integer voxel box"
    bl_options = {'REGISTER', 'UNDO'}

    minimum: IntVectorProperty(name="Minimum", size=3, default=(0, 0, 0))
    maximum: IntVectorProperty(name="Maximum", size=3, default=(1, 1, 1))
    palette_index: IntProperty(name="Palette Index", min=0, max=65535, default=0)
    overwrite: BoolProperty(name="Overwrite", default=True)
    allow_protected: BoolProperty(name="Edit Protected", default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        return _run_edit_operator(
            self,
            context,
            "fill_box",
            lambda tx: tx.fill_box(
                self.minimum,
                self.maximum,
                self.palette_index,
                overwrite=self.overwrite,
            ),
            self.allow_protected,
        )


class VOXTART_OT_flood_fill(bpy.types.Operator):
    bl_idname = "voxtart.flood_fill"
    bl_label = "Flood Fill"
    bl_description = "Fill a six-connected voxel region inside explicit bounds"
    bl_options = {'REGISTER', 'UNDO'}

    cell: IntVectorProperty(name="Cell", size=3, default=(0, 0, 0))
    minimum: IntVectorProperty(name="Minimum", size=3, default=(-16, -16, -16))
    maximum: IntVectorProperty(name="Maximum", size=3, default=(16, 16, 16))
    palette_index: IntProperty(name="Palette Index", min=0, max=65535, default=0)
    allow_protected: BoolProperty(name="Edit Protected", default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        return _run_edit_operator(
            self,
            context,
            "flood_fill",
            lambda tx: tx.flood_fill(
                self.cell,
                self.maximum,
                self.palette_index,
                minimum=self.minimum,
            ),
            self.allow_protected,
        )


class VOXTART_OT_paint_voxel(bpy.types.Operator):
    bl_idname = "voxtart.paint_voxel"
    bl_label = "Paint Voxel"
    bl_description = "Change the palette index of one occupied voxel"
    bl_options = {'REGISTER', 'UNDO'}

    cell: IntVectorProperty(name="Cell", size=3, default=(0, 0, 0))
    palette_index: IntProperty(name="Palette Index", min=0, max=65535, default=0)
    allow_protected: BoolProperty(name="Edit Protected", default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        return _run_edit_operator(
            self,
            context,
            "paint_voxel",
            lambda tx: tx.paint(self.cell, self.palette_index),
            self.allow_protected,
        )


class VOXTART_OT_select_component(bpy.types.Operator):
    bl_idname = "voxtart.select_component"
    bl_label = "Select Component"
    bl_description = "Store one six-connected voxel component as the active selection"
    bl_options = {'REGISTER', 'UNDO'}

    component_index: IntProperty(name="Component", min=0, default=0)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        obj = context.active_object
        try:
            components = connected_components(_load_asset(obj))
            if self.component_index >= len(components):
                raise ValueError(f"component index {self.component_index} is out of range")
            selected = components[self.component_index]
            obj["voxtart_component_selection"] = json.dumps(
                [list(cell) for cell in selected],
                separators=(",", ":"),
            )
            obj["voxtart_component_index"] = self.component_index
            obj["voxtart_component_count"] = len(components)
            _update_visual_overlays(obj)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"selected component {self.component_index} ({len(selected)} voxels)")
        return {'FINISHED'}


class VOXTART_OT_set_protected_voxel(bpy.types.Operator):
    bl_idname = "voxtart.set_protected_voxel"
    bl_label = "Set Protected Voxel"
    bl_description = "Add or remove a protected voxel edit guard"
    bl_options = {'REGISTER', 'UNDO'}

    cell: IntVectorProperty(name="Cell", size=3, default=(0, 0, 0))
    protected: BoolProperty(name="Protected", default=True)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        obj = context.active_object
        try:
            protected = set(_protected_cells(obj))
            if self.protected:
                protected.add(tuple(self.cell))
            else:
                protected.discard(tuple(self.cell))
            obj["voxtart_protected_voxels"] = json.dumps(
                [list(cell) for cell in sorted(protected)],
                separators=(",", ":"),
            )
            _update_visual_overlays(obj)
            obj["voxtart_protection_report"] = json.dumps({
                "protected": self.protected,
                "cell": list(self.cell),
                "count": len(protected),
            }, sort_keys=True)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_OT_add_palette_color(bpy.types.Operator):
    bl_idname = "voxtart.add_palette_color"
    bl_label = "Add Palette Color"
    bl_description = "Append a color to the active voxel asset palette"
    bl_options = {'REGISTER', 'UNDO'}

    color: FloatVectorProperty(
        name="Color",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        obj = context.active_object
        try:
            asset = _load_asset(obj)
            palette_index = asset.add_palette_color(self.color)
            _replace_asset_mesh(obj, asset)
            obj["voxtart_last_palette_index"] = palette_index
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"added palette color {palette_index}")
        return {'FINISHED'}


class VOXTART_OT_pick_voxel(bpy.types.Operator):
    bl_idname = "voxtart.pick_voxel"
    bl_label = "Pick Voxel"
    bl_description = "Pick the first occupied voxel under the cursor"
    bl_options = {'REGISTER', 'UNDO'}

    origin: FloatVectorProperty(name="Origin", size=3, default=(0.0, 0.0, 0.0), options={'HIDDEN'})
    direction: FloatVectorProperty(name="Direction", size=3, default=(1.0, 0.0, 0.0), options={'HIDDEN'})
    max_distance: FloatProperty(name="Max Distance", min=0.0, default=1.0e6, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def _pick(self, context, origin, direction):
        obj = context.active_object
        try:
            asset = _load_asset(obj)
            inverse = obj.matrix_world.inverted()
            local_origin = inverse @ Vector(origin)
            local_direction = inverse.to_3x3() @ Vector(direction)
            hit = raycast_voxel(
                asset,
                local_origin,
                local_direction,
                max_distance=self.max_distance,
            )
            if hit is None:
                raise ValueError("ray did not hit an occupied voxel")
            obj[PICKED_CELL_PROPERTY] = json.dumps([list(hit.cell)], separators=(",", ":"))
            obj["voxtart_picked_normal"] = json.dumps(list(hit.normal), separators=(",", ":"))
            obj["voxtart_picked_palette_index"] = hit.palette_index
            context.scene.voxtart_edit_cell = hit.cell
            _update_visual_overlays(obj, asset)
        except (TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"picked voxel {hit.cell}")
        return {'FINISHED'}

    def execute(self, context):
        return self._pick(context, self.origin, self.direction)

    def invoke(self, context, event):
        if context.region is None or context.region_data is None:
            self.report({'ERROR'}, "pick requires a 3D View region")
            return {'CANCELLED'}
        origin = view3d_utils.region_2d_to_origin_3d(
            context.region,
            context.region_data,
            (event.mouse_region_x, event.mouse_region_y),
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            context.region,
            context.region_data,
            (event.mouse_region_x, event.mouse_region_y),
        )
        return self._pick(context, origin, direction)


class VOXTART_OT_add_socket(bpy.types.Operator):
    bl_idname = "voxtart.add_socket"
    bl_label = "Add Socket"
    bl_description = "Add a grid-aligned kitbash socket to the active voxel asset"
    bl_options = {'REGISTER', 'UNDO'}

    position: IntVectorProperty(name="Position", size=3, default=(0, 0, 0))
    normal: EnumProperty(
        name="Normal",
        items=(
            ('NEG_X', "-X", "Socket faces negative X"),
            ('POS_X', "+X", "Socket faces positive X"),
            ('NEG_Y', "-Y", "Socket faces negative Y"),
            ('POS_Y', "+Y", "Socket faces positive Y"),
            ('NEG_Z', "-Z", "Socket faces negative Z"),
            ('POS_Z', "+Z", "Socket faces positive Z"),
        ),
        default='POS_X',
    )
    tag: StringProperty(name="Tag", default="default")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            asset = _load_asset(context.active_object)
            normal = {
                'NEG_X': (-1, 0, 0),
                'POS_X': (1, 0, 0),
                'NEG_Y': (0, -1, 0),
                'POS_Y': (0, 1, 0),
                'NEG_Z': (0, 0, -1),
                'POS_Z': (0, 0, 1),
            }[self.normal]
            asset.sockets.append(VoxelSocket(tuple(self.position), normal, self.tag))
            _store_asset(context.active_object, asset)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_OT_snap_active(bpy.types.Operator):
    bl_idname = "voxtart.snap_active"
    bl_label = "Snap Active Asset"
    bl_description = "Snap the active voxel asset to a compatible selected target socket"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and ASSET_PROPERTY in context.active_object
            and sum(ASSET_PROPERTY in obj for obj in context.selected_objects) >= 2
        )

    def execute(self, context):
        source_obj = context.active_object
        targets = sorted(
            (obj for obj in context.selected_objects
             if obj != source_obj and ASSET_PROPERTY in obj),
            key=lambda obj: obj.name,
        )
        if not targets:
            self.report({'ERROR'}, "select a target voxel asset as well as the active source")
            return {'CANCELLED'}
        target_obj = targets[0]
        try:
            source_asset = _load_asset(source_obj)
            target_asset = _load_asset(target_obj)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

        match = find_best_match(source_asset, target_asset)
        if match is None:
            self.report({'ERROR'}, "no compatible socket pair found")
            return {'CANCELLED'}
        source_socket = source_asset.sockets[match.source_index]
        target_socket = target_asset.sockets[match.target_index]
        source_point = source_obj.matrix_world @ Vector(
            tuple(axis * source_asset.grid_size for axis in source_socket.position)
        )
        target_point = target_obj.matrix_world @ Vector(
            tuple(axis * target_asset.grid_size for axis in target_socket.position)
        )
        source_obj.matrix_world.translation += target_point - source_point
        source_obj["voxtart_last_snap"] = json.dumps({
            "target": target_obj.name,
            "source_socket": match.source_index,
            "target_socket": match.target_index,
        }, sort_keys=True)
        self.report({'INFO'}, f"snapped to {target_obj.name}")
        return {'FINISHED'}


class VOXTART_OT_rebuild_asset(bpy.types.Operator):
    bl_idname = "voxtart.rebuild_asset"
    bl_label = "Rebuild Voxel Mesh"
    bl_description = "Rebuild the visible mesh from its stored sparse voxel asset"
    bl_options = {'REGISTER', 'UNDO'}

    backend: EnumProperty(
        name="Surface",
        items=(
            ('greedy', "Greedy Blocks", "Merge exposed voxel faces for a blocky surface"),
            ('marching', "Marching Surface", "Build a smooth triangular marching voxel surface"),
        ),
        default='greedy',
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def invoke(self, context, _event):
        self.backend = str(context.active_object.get(MESH_BACKEND_PROPERTY, "greedy"))
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        try:
            asset = _load_asset(obj)
            _replace_asset_mesh(obj, asset, backend=self.backend)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def _protected_cells(obj: bpy.types.Object) -> frozenset[tuple[int, int, int]]:
    raw = obj.get("voxtart_protected_voxels")
    if raw is None:
        return frozenset()
    encoded = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(encoded, list):
        raise ValueError("voxtart_protected_voxels must be a JSON list")
    cells = set()
    for cell in encoded:
        if not isinstance(cell, (list, tuple)) or len(cell) != 3:
            raise ValueError("each protected voxel must contain three coordinates")
        cells.add(tuple(int(axis) for axis in cell))
    return frozenset(cells)


class VOXTART_OT_decimate_asset(bpy.types.Operator):
    bl_idname = "voxtart.decimate_asset"
    bl_label = "Decimate Voxel Asset"
    bl_description = "Downsample the active sparse voxel asset and record the optimization report"
    bl_options = {'REGISTER', 'UNDO'}

    factor: IntProperty(name="Factor", min=2, max=16, default=2)
    threshold: EnumProperty(
        name="Occupancy",
        items=(
            ('majority', "Majority", "Keep coarse cells occupied when at least half the source block is filled"),
            ('any', "Any", "Keep coarse cells occupied when any source voxel is filled"),
            ('full', "Full", "Keep coarse cells only when the source block is full"),
        ),
        default='majority',
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        obj = context.active_object
        try:
            asset = _load_asset(obj)
            policy = DecimationPolicy(
                factor=self.factor,
                threshold=self.threshold,
                protected=_protected_cells(obj),
            )
            optimized, report = decimate(asset, policy)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        _replace_asset_mesh(obj, optimized)
        obj["voxtart_decimation_report"] = json.dumps(report.to_dict(), sort_keys=True)
        self.report(
            {'INFO'},
            f"decimated {report.source_voxels} to {report.target_voxels} voxels",
        )
        return {'FINISHED'}


class VOXTART_PT_voxel_panel(bpy.types.Panel):
    bl_label = "VOXTART Voxel Authoring"
    bl_idname = "VOXTART_PT_voxel_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "voxtart_grid_size")
        layout.prop(scene, "voxtart_cube_size")
        layout.operator(VOXTART_OT_add_voxel_cube.bl_idname, icon='CUBE')
        layout.operator(VOXTART_OT_voxelize_active.bl_idname, icon='MOD_REMESH')
        layout.operator(VOXTART_OT_import_asset.bl_idname, text="Import VOXTART Asset")
        if not context.active_object or ASSET_PROPERTY not in context.active_object:
            return

        layout.separator()
        layout.label(text="Voxel Editing")
        layout.prop(scene, "voxtart_edit_cell", text="Cell")
        layout.operator(VOXTART_OT_pick_voxel.bl_idname, text="Pick Voxel")
        layout.prop(scene, "voxtart_edit_palette_index", text="Palette Index")
        layout.prop(scene, "voxtart_palette_color", text="Palette Color")
        palette = layout.operator(VOXTART_OT_add_palette_color.bl_idname)
        palette.color = tuple(scene.voxtart_palette_color)
        layout.prop(scene, "voxtart_edit_allow_protected", text="Edit Protected")
        cell = tuple(scene.voxtart_edit_cell)
        palette_index = scene.voxtart_edit_palette_index
        row = layout.row(align=True)
        add = row.operator(VOXTART_OT_add_voxel.bl_idname, text="Add")
        add.cell = cell
        add.palette_index = palette_index
        add.allow_protected = scene.voxtart_edit_allow_protected
        remove = row.operator(VOXTART_OT_remove_voxel.bl_idname, text="Remove")
        remove.cell = cell
        remove.allow_protected = scene.voxtart_edit_allow_protected
        paint = row.operator(VOXTART_OT_paint_voxel.bl_idname, text="Paint")
        paint.cell = cell
        paint.palette_index = palette_index
        paint.allow_protected = scene.voxtart_edit_allow_protected

        layout.label(text="Box Fill")
        layout.prop(scene, "voxtart_edit_box_min", text="Minimum")
        layout.prop(scene, "voxtart_edit_box_max", text="Maximum")
        fill = layout.operator(VOXTART_OT_fill_box.bl_idname)
        fill.minimum = tuple(scene.voxtart_edit_box_min)
        fill.maximum = tuple(scene.voxtart_edit_box_max)
        fill.palette_index = palette_index
        fill.allow_protected = scene.voxtart_edit_allow_protected

        layout.label(text="Flood Fill")
        layout.prop(scene, "voxtart_edit_flood_min", text="Minimum")
        layout.prop(scene, "voxtart_edit_flood_max", text="Maximum")
        flood = layout.operator(VOXTART_OT_flood_fill.bl_idname)
        flood.cell = cell
        flood.minimum = tuple(scene.voxtart_edit_flood_min)
        flood.maximum = tuple(scene.voxtart_edit_flood_max)
        flood.palette_index = palette_index
        flood.allow_protected = scene.voxtart_edit_allow_protected

        layout.label(text="Components and Protection")
        layout.prop(scene, "voxtart_component_index", text="Component")
        select = layout.operator(VOXTART_OT_select_component.bl_idname)
        select.component_index = scene.voxtart_component_index
        layout.prop(scene, "voxtart_protect_enabled", text="Protected")
        protect = layout.operator(VOXTART_OT_set_protected_voxel.bl_idname)
        protect.cell = cell
        protect.protected = scene.voxtart_protect_enabled

        layout.separator()
        layout.operator(VOXTART_OT_add_socket.bl_idname, text="Add Socket")
        if len(context.selected_objects) >= 2:
            layout.operator(VOXTART_OT_snap_active.bl_idname, text="Snap Active Asset")
        row = layout.row(align=True)
        greedy = row.operator(VOXTART_OT_rebuild_asset.bl_idname, text="Greedy")
        greedy.backend = 'greedy'
        marching = row.operator(VOXTART_OT_rebuild_asset.bl_idname, text="Marching")
        marching.backend = 'marching'
        layout.operator(VOXTART_OT_decimate_asset.bl_idname, text="Decimate Voxel Asset")
        layout.operator(VOXTART_OT_export_asset.bl_idname, text="Export VOXTART Asset")
        layout.label(text="Sparse asset stored on object")
class VOXTART_OT_import_asset(bpy.types.Operator, ImportHelper):
    bl_idname = "voxtart.import_asset"
    bl_label = "Import VOXTART Asset"
    bl_description = "Load a versioned VOXTART voxel asset as a new object"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".voxel.json"
    filter_glob: StringProperty(default="*.voxel.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            asset = load_asset(self.filepath)
            filename = Path(self.filepath).name
            suffix = ".voxel.json"
            name = filename[:-len(suffix)] if filename.endswith(suffix) else Path(filename).stem
            _create_asset_object(context, name or "VOXTART Asset", asset)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_OT_export_asset(bpy.types.Operator, ExportHelper):
    bl_idname = "voxtart.export_asset"
    bl_label = "Export VOXTART Asset"
    bl_description = "Save the active authoritative voxel asset as a versioned JSON file"
    filename_ext = ".voxel.json"
    filter_glob: StringProperty(default="*.voxel.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            save_asset(_load_asset(context.active_object), self.filepath)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}




def _menu_add_voxel_cube(self, _context):
    self.layout.operator(VOXTART_OT_add_voxel_cube.bl_idname, icon='CUBE')


_CLASSES = (
    VOXTART_OT_add_voxel_cube,
    VOXTART_OT_voxelize_active,
    VOXTART_OT_pick_voxel,
    VOXTART_OT_remove_voxel,
    VOXTART_OT_fill_box,
    VOXTART_OT_flood_fill,
    VOXTART_OT_paint_voxel,
    VOXTART_OT_select_component,
    VOXTART_OT_set_protected_voxel,
    VOXTART_OT_add_palette_color,
    VOXTART_OT_add_socket,
    VOXTART_OT_snap_active,
    VOXTART_OT_rebuild_asset,
    VOXTART_OT_decimate_asset,
    VOXTART_OT_import_asset,
    VOXTART_OT_export_asset,
    VOXTART_PT_voxel_panel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_grid_size = FloatProperty(
        name="Grid Size", description="Scene units per voxel", min=0.0001, default=1.0
    )
    bpy.types.Scene.voxtart_cube_size = IntProperty(
        name="Cube Size", description="Edge length in voxels", min=1, max=256, default=4
    )
    bpy.types.Scene.voxtart_edit_cell = IntVectorProperty(
        name="Edit Cell", size=3, default=(0, 0, 0), min=-1024, max=1024
    )
    bpy.types.Scene.voxtart_edit_palette_index = IntProperty(
        name="Edit Palette Index", min=0, max=65535, default=0
    )
    bpy.types.Scene.voxtart_palette_color = FloatVectorProperty(
        name="Palette Color",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )
    bpy.types.Scene.voxtart_edit_allow_protected = BoolProperty(
        name="Edit Protected Voxels", default=False
    )
    bpy.types.Scene.voxtart_edit_box_min = IntVectorProperty(
        name="Box Minimum", size=3, default=(0, 0, 0), min=-1024, max=1024
    )
    bpy.types.Scene.voxtart_edit_box_max = IntVectorProperty(
        name="Box Maximum", size=3, default=(1, 1, 1), min=-1024, max=1024
    )
    bpy.types.Scene.voxtart_edit_flood_min = IntVectorProperty(
        name="Flood Minimum", size=3, default=(-16, -16, -16), min=-1024, max=1024
    )
    bpy.types.Scene.voxtart_edit_flood_max = IntVectorProperty(
        name="Flood Maximum", size=3, default=(16, 16, 16), min=-1024, max=1024
    )
    bpy.types.Scene.voxtart_component_index = IntProperty(
        name="Component Index", min=0, default=0
    )
    bpy.types.Scene.voxtart_protect_enabled = BoolProperty(
        name="Protect Cell", default=True
    )
    bpy.types.VIEW3D_MT_add.append(_menu_add_voxel_cube)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(_menu_add_voxel_cube)
    del bpy.types.Scene.voxtart_protect_enabled
    del bpy.types.Scene.voxtart_component_index
    del bpy.types.Scene.voxtart_edit_flood_max
    del bpy.types.Scene.voxtart_edit_flood_min
    del bpy.types.Scene.voxtart_edit_box_max
    del bpy.types.Scene.voxtart_edit_box_min
    del bpy.types.Scene.voxtart_edit_allow_protected
    del bpy.types.Scene.voxtart_edit_palette_index
    del bpy.types.Scene.voxtart_palette_color
    del bpy.types.Scene.voxtart_edit_cell
    del bpy.types.Scene.voxtart_cube_size
    del bpy.types.Scene.voxtart_grid_size
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
