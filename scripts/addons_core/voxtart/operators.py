# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Blender-facing operators for the VOXTART voxel prototype."""


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
from .io import load_asset, load_asset_with_layers, save_asset
from .kitbash import find_best_match
from .layers import LAYER_STACK_PROPERTY, LayerStack
from .meshing import scaled_mesh
from .optimize import DecimationPolicy, decimate
from .palette import DEFAULT_PALETTE, MAX_PALETTE_COLORS, nearest_palette_index, scene_palette
from .pick import raycast_voxel
from .voxel import VoxelAsset, VoxelSocket
from .voxelizer import MAX_SHELL_THICKNESS, VOXELIZER_MODES, apply_voxelizer_mode
from .volume_ops import (
    ROTATION_DEGREES,
    TRANSFORM_AXES,
    crop_asset,
    mirror_asset,
    rotate_asset,
    translate_asset,
)


ASSET_PROPERTY = "voxtart_asset_json"
SCHEMA_PROPERTY = "voxtart_schema_version"
MESH_BACKEND_PROPERTY = "voxtart_mesh_backend"
MESH_BACKEND_OVERRIDE_PROPERTY = "voxtart_mesh_backend_override"
COMPONENT_SELECTION_PROPERTY = "voxtart_component_selection"
COMPONENT_OVERLAY_PROPERTY = "voxtart_component_overlay"
PICKED_CELL_PROPERTY = "voxtart_picked_cell"
PICKED_NORMAL_PROPERTY = "voxtart_picked_normal"
PICKED_OVERLAY_PROPERTY = "voxtart_picked_overlay"
PROTECTED_OVERLAY_PROPERTY = "voxtart_protected_overlay"
SEAM_OVERLAY_PROPERTY = "voxtart_seam_overlay"
SEAM_OVERLAY_COLOR = (0.2, 0.17, 0.09, 1.0)
MAX_VOXELIZE_CELLS = 2_000_000
VOXELIZER_SOURCE_PROPERTY = "voxtart_voxelizer_source"
VOXELIZER_MODE_PROPERTY = "voxtart_voxelizer_mode"
VOXELIZER_THICKNESS_PROPERTY = "voxtart_voxelizer_shell_thickness"
VOXELIZER_REPORT_PROPERTY = "voxtart_voxelizer_report"
_VOXELIZER_MODE_ITEMS = (
    ("SOLID", "Solid", "Keep every occupied sample from the mesh"),
    ("SURFACE", "Surface", "Keep only the outer one-voxel surface"),
    ("SHELL", "Shell", "Keep a configurable outer voxel shell"),
)
_TRANSFORM_OPERATION_ITEMS = (
    ("MIRROR", "Mirror", "Reflect the asset across its occupied bounding-box midpoint"),
    ("ROTATE", "Rotate", "Rotate the asset by a right angle on the selected axis"),
    ("TRANSLATE", "Translate", "Move the asset by an integer voxel offset"),
    ("CROP", "Crop", "Compact the asset to its occupied bounds"),
)
_TRANSFORM_AXIS_ITEMS = tuple(
    (axis, axis, f"Transform around the {axis} axis") for axis in TRANSFORM_AXES
)
_TRANSFORM_DEGREE_ITEMS = tuple(
    (str(degrees), f"{degrees}°", f"Rotate by {degrees} degrees")
    for degrees in ROTATION_DEGREES[1:]
)


def _store_asset(obj: bpy.types.Object, asset: VoxelAsset) -> None:
    obj[SCHEMA_PROPERTY] = asset.schema_version
    obj[ASSET_PROPERTY] = json.dumps(asset.to_dict(), sort_keys=True, separators=(",", ":"))


def _store_layer_stack(obj: bpy.types.Object, stack: LayerStack) -> None:
    obj[LAYER_STACK_PROPERTY] = stack.encode()


def _load_asset(obj: bpy.types.Object) -> VoxelAsset:
    raw = obj.get(ASSET_PROPERTY)
    if not isinstance(raw, str):
        raise ValueError("active object is not a VOXTART voxel asset")
    return VoxelAsset.from_dict(json.loads(raw))


def _load_layer_stack(obj: bpy.types.Object, asset: VoxelAsset | None = None) -> LayerStack:
    asset = asset or _load_asset(obj)
    raw = obj.get(LAYER_STACK_PROPERTY)
    if isinstance(raw, str):
        try:
            return LayerStack.decode(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return LayerStack.from_asset(asset)


def _asset_from_layer_stack(asset: VoxelAsset, stack: LayerStack) -> VoxelAsset:
    return VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(asset.palette),
        voxels=stack.composite(asset.palette),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
    )


def _retarget_asset_palette(
    asset: VoxelAsset,
    palette,
    layer_stack: LayerStack | None = None,
) -> tuple[VoxelAsset, LayerStack]:
    stack = layer_stack or LayerStack.from_asset(asset)
    stack.remap_palette(asset.palette, palette)
    replacement = VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(palette),
        voxels=stack.composite(palette),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
    )
    return replacement, stack


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
        material = bpy.data.materials.get(f"VOXTART {obj.name} Palette {index}")
        if material is None:
            material = bpy.data.materials.new(f"VOXTART {obj.name} Palette {index}")
        material.diffuse_color = color
        obj.data.materials.append(material)


def _link_object(context: bpy.types.Context, obj: bpy.types.Object) -> None:
    collection = context.collection or context.scene.collection
    collection.objects.link(obj)
    for selected in context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _create_asset_object(
    context: bpy.types.Context,
    name: str,
    asset: VoxelAsset,
    matrix_world=None,
    backend: str = "greedy",
    layer_stack: LayerStack | None = None,
) -> bpy.types.Object:
    stack = layer_stack or LayerStack.from_asset(asset)
    visible_asset = _asset_from_layer_stack(asset, stack)
    obj = bpy.data.objects.new(name, _new_mesh(f"{name} Mesh", visible_asset, backend=backend))
    if matrix_world is not None:
        obj.matrix_world = matrix_world
    _palette_materials(obj, visible_asset)
    _store_asset(obj, visible_asset)
    _store_layer_stack(obj, stack)
    obj[MESH_BACKEND_PROPERTY] = backend
    obj[MESH_BACKEND_OVERRIDE_PROPERTY] = False
    _link_object(context, obj)
    _update_visual_overlays(obj, visible_asset)
    return obj


def _replace_asset_mesh(
    obj: bpy.types.Object,
    asset: VoxelAsset,
    backend: str | None = None,
    layer_stack: LayerStack | None = None,
) -> None:
    stack = layer_stack or _load_layer_stack(obj, asset)
    if backend is None:
        backend = str(obj.get(MESH_BACKEND_PROPERTY, "greedy")).lower()
        if not bool(obj.get(MESH_BACKEND_OVERRIDE_PROPERTY, False)):
            layer_backend = str(getattr(stack.active, "surface_backend", "INHERIT")).lower()
            if layer_backend in {"greedy", "marching"}:
                backend = layer_backend
    backend = str(backend).lower()
    if backend not in {"greedy", "marching"}:
        raise ValueError(f"unsupported mesh backend: {backend!r}")
    visible_asset = _asset_from_layer_stack(asset, stack)
    old_mesh = obj.data
    obj.data = _new_mesh(f"{obj.name} Mesh", visible_asset, backend=backend)
    _palette_materials(obj, visible_asset)
    _store_asset(obj, visible_asset)
    _store_layer_stack(obj, stack)
    obj[MESH_BACKEND_PROPERTY] = backend
    _update_visual_overlays(obj, visible_asset)
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
    _update_overlay(
        obj,
        asset,
        SEAM_OVERLAY_PROPERTY,
        tuple(asset.voxels),
        "Seams",
        SEAM_OVERLAY_COLOR,
    )
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


def _source_material_color(obj: bpy.types.Object, polygon_index: int):
    polygons = getattr(obj.data, "polygons", ())
    if polygon_index is None or not 0 <= polygon_index < len(polygons):
        return None
    polygon = polygons[polygon_index]
    materials = getattr(obj.data, "materials", ())
    material_index = int(getattr(polygon, "material_index", -1))
    if not 0 <= material_index < len(materials):
        return None
    color = getattr(materials[material_index], "diffuse_color", None)
    if color is None:
        return None
    try:
        channels = tuple(float(channel) for channel in color)
    except (TypeError, ValueError):
        return None
    if len(channels) == 3:
        return (*channels, 1.0)
    if len(channels) == 4:
        return channels
    return None


def _voxelize_object(
    obj: bpy.types.Object,
    grid_size: float,
    palette=None,
    *,
    mode: str = "SOLID",
    shell_thickness: int = 1,
) -> VoxelAsset:
    if obj.type != 'MESH' or not obj.data.vertices or not obj.data.polygons:
        raise ValueError("active object must be a non-empty mesh")
    if grid_size <= 0.0:
        raise ValueError("grid size must be positive")
    palette = list(palette or DEFAULT_PALETTE)
    if not palette:
        raise ValueError("voxelizer requires a non-empty palette")

    vertices = [vertex.co.copy() for vertex in obj.data.vertices]
    polygons = [list(polygon.vertices) for polygon in obj.data.polygons]
    tree = BVHTree.FromPolygons(vertices, polygons, epsilon=1.0e-6)
    bounds = [Vector(corner) for corner in obj.bound_box]
    minimum = Vector((min(corner[axis] for corner in bounds) for axis in range(3)))
    maximum = Vector((max(corner[axis] for corner in bounds) for axis in range(3)))
    start = tuple(math.floor(minimum[axis] / grid_size) for axis in range(3))
    stop = tuple(math.ceil(maximum[axis] / grid_size) for axis in range(3))
    estimated_cells = math.prod(max(0, stop[axis] - start[axis]) for axis in range(3))
    if estimated_cells > MAX_VOXELIZE_CELLS:
        raise ValueError(
            f"voxelizer search volume is {estimated_cells:,} cells; increase Grid Size "
            f"or keep it below {MAX_VOXELIZE_CELLS:,} cells"
        )

    asset = VoxelAsset(grid_size=grid_size, palette=palette, provenance={"source_object": obj.name})
    for x in range(start[0], stop[0]):
        for y in range(start[1], stop[1]):
            for z in range(start[2], stop[2]):
                point = Vector(((x + 0.5) * grid_size, (y + 0.5) * grid_size, (z + 0.5) * grid_size))
                if not _inside_bvh(tree, point, minimum.x, maximum.x, grid_size):
                    continue
                palette_index = 0
                try:
                    nearest = tree.find_nearest(point)
                except (AttributeError, RuntimeError):
                    nearest = None
                if nearest and len(nearest) >= 3:
                    material_color = _source_material_color(obj, nearest[2])
                    if material_color is not None:
                        palette_index = nearest_palette_index(material_color, palette)
                asset.set_voxel((x, y, z), palette_index)
    if not asset.voxels:
        raise ValueError("mesh produced no occupied voxels at this grid size")
    return apply_voxelizer_mode(
        asset,
        mode=mode,
        shell_thickness=shell_thickness,
    )


def _apply_edit(
    obj: bpy.types.Object,
    operation: str,
    callback,
    *,
    allow_protected: bool = False,
):
    asset = _load_asset(obj)
    stack = _load_layer_stack(obj, asset)
    if stack.active.locked:
        raise ValueError(f"active layer is locked: {stack.active.name}")
    editable = VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(asset.palette),
        voxels=dict(stack.active.voxels),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
    )
    report = edit_asset(
        editable,
        operation,
        callback,
        protected=_protected_cells(obj),
        allow_protected=allow_protected,
    )
    stack.active.voxels = dict(editable.voxels)
    _replace_asset_mesh(obj, _asset_from_layer_stack(asset, stack), layer_stack=stack)
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
        palette = scene_palette(context.scene)
        asset = VoxelAsset(grid_size=self.grid_size, palette=palette)
        offset = -(self.size // 2)
        for x in range(self.size):
            for y in range(self.size):
                for z in range(self.size):
                    asset.set_voxel((offset + x, offset + y, offset + z))
        _create_asset_object(context, "Voxel Cube", asset)
        return {'FINISHED'}


class VOXTART_OT_voxelize_active(bpy.types.Operator):
    bl_idname = "voxtart.voxelize_active"
    bl_label = "Voxelize Selected Mesh"
    bl_description = "Convert the active mesh into a configurable sparse voxel asset"
    bl_options = {'REGISTER', 'UNDO'}

    grid_size: FloatProperty(name="Grid Size", min=0.0001, default=1.0)
    mode: EnumProperty(name="Voxelizer Mode", items=_VOXELIZER_MODE_ITEMS, default="SOLID")
    shell_thickness: IntProperty(
        name="Shell Thickness",
        min=1,
        max=MAX_SHELL_THICKNESS,
        default=1,
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        source = context.active_object
        try:
            asset = _voxelize_object(
                source,
                self.grid_size,
                scene_palette(context.scene),
                mode=self.mode,
                shell_thickness=self.shell_thickness,
            )
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        target = _create_asset_object(
            context,
            f"{source.name}_Voxel",
            asset,
            source.matrix_world.copy(),
        )
        target[VOXELIZER_SOURCE_PROPERTY] = source.name
        target[VOXELIZER_MODE_PROPERTY] = self.mode
        target[VOXELIZER_THICKNESS_PROPERTY] = self.shell_thickness
        return {'FINISHED'}


class VOXTART_OT_refresh_voxelizer(bpy.types.Operator):
    bl_idname = "voxtart.refresh_voxelizer"
    bl_label = "Refresh Linked Voxelizer"
    bl_description = "Re-voxelize the linked source mesh with its stored voxelizer settings"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        return obj is not None and (
            ASSET_PROPERTY in obj and VOXELIZER_SOURCE_PROPERTY in obj
        )

    def execute(self, context):
        target = context.active_object
        source_name = str(target.get(VOXELIZER_SOURCE_PROPERTY, "")).strip()
        source = bpy.data.objects.get(source_name)
        if source is None or source.type != "MESH":
            self.report({'ERROR'}, f"voxelizer source mesh is unavailable: {source_name!r}")
            return {'CANCELLED'}
        try:
            current = _load_asset(target)
            asset = _voxelize_object(
                source,
                current.grid_size,
                scene_palette(context.scene),
                mode=str(target.get(VOXELIZER_MODE_PROPERTY, "SOLID")),
                shell_thickness=int(target.get(VOXELIZER_THICKNESS_PROPERTY, 1)),
            )
        except (TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        _replace_asset_mesh(
            target,
            asset,
            layer_stack=LayerStack.from_asset(asset),
        )
        target.matrix_world = source.matrix_world.copy()
        target["voxtart_voxelizer_source"] = source.name
        target[VOXELIZER_REPORT_PROPERTY] = json.dumps({
            "source": source.name,
            "mode": asset.provenance.get("voxelizer_mode", "SOLID"),
            "voxels": len(asset.voxels),
        }, sort_keys=True)
        self.report({'INFO'}, f"refreshed {len(asset.voxels)} voxels from {source.name}")
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
    bl_description = "Append a color when palette enforcement is disabled; otherwise use the VOXTART Palette window"
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
            if bool(getattr(context.scene, "voxtart_palette_enforce", True)):
                palette = scene_palette(context.scene)
                color = tuple(float(channel) for channel in self.color)
                if color not in palette:
                    raise ValueError("palette enforcement is active; add colors in the VOXTART Palette window")
                palette_index = palette.index(color)
                context.scene.voxtart_edit_palette_index = palette_index
                obj["voxtart_last_palette_index"] = palette_index
            else:
                asset = _load_asset(obj)
                if len(asset.palette) >= MAX_PALETTE_COLORS:
                    raise ValueError(f"palettes may contain at most {MAX_PALETTE_COLORS} colors")
                palette_index = asset.add_palette_color(self.color)
                _replace_asset_mesh(obj, asset)
                obj["voxtart_last_palette_index"] = palette_index
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"palette color {palette_index}")
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


class VOXTART_OT_edit_picked_voxel(bpy.types.Operator):
    bl_idname = "voxtart.edit_picked_voxel"
    bl_label = "Edit Picked Voxel"
    bl_description = "Place, remove, or paint the picked voxel"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(
        name="Action",
        items=(
            ('add', "Place Adjacent", "Place a voxel immediately outside the picked face"),
            ('remove', "Remove Picked", "Remove the picked voxel"),
            ('paint', "Paint Picked", "Apply the selected palette index to the picked voxel"),
        ),
        default='add',
    )
    palette_index: IntProperty(name="Palette Index", min=0, max=65535, default=0)
    allow_protected: BoolProperty(name="Edit Protected", default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def _target(self, obj):
        cells = _overlay_cells(obj, PICKED_CELL_PROPERTY)
        if len(cells) != 1:
            raise ValueError("pick a voxel before editing")
        cell = cells[0]
        if self.action != 'add':
            return cell
        raw_normal = obj.get(PICKED_NORMAL_PROPERTY)
        if raw_normal is None:
            raise ValueError("picked voxel has no face normal")
        encoded = json.loads(raw_normal) if isinstance(raw_normal, str) else raw_normal
        if (
            not isinstance(encoded, (list, tuple))
            or len(encoded) != 3
            or any(axis not in (-1, 0, 1) for axis in encoded)
            or not any(encoded)
        ):
            raise ValueError("picked voxel normal is invalid")
        return tuple(cell[axis] + int(encoded[axis]) for axis in range(3))

    def execute(self, context):
        obj = context.active_object
        try:
            target = self._target(obj)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        if self.action == 'add':
            callback = lambda tx: tx.add(target, self.palette_index, overwrite=False)
            operation = "add_picked_voxel"
        elif self.action == 'remove':
            callback = lambda tx: tx.remove(target)
            operation = "remove_picked_voxel"
        else:
            callback = lambda tx: tx.paint(target, self.palette_index)
            operation = "paint_picked_voxel"
        return _run_edit_operator(
            self,
            context,
            operation,
            callback,
            self.allow_protected,
        )


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
            obj[MESH_BACKEND_OVERRIDE_PROPERTY] = True
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
        flattened = LayerStack.from_asset(optimized)
        flattened.layers[0].name = "Flattened"
        _replace_asset_mesh(obj, optimized, layer_stack=flattened)
        obj["voxtart_decimation_report"] = json.dumps(report.to_dict(), sort_keys=True)
        self.report(
            {'INFO'},
            f"decimated {report.source_voxels} to {report.target_voxels} voxels",
        )
        return {'FINISHED'}

def _layer_reference_bounds(stack: LayerStack, fallback):
    cells = [cell for layer in stack.layers for cell in layer.voxels]
    if not cells:
        return fallback
    minimum = tuple(min(cell[axis] for cell in cells) for axis in range(3))
    maximum = tuple(max(cell[axis] for cell in cells) for axis in range(3))
    return minimum, maximum


def _apply_volume_operation(
    asset: VoxelAsset,
    operation: str,
    axis: str,
    degrees: int,
    offset,
    rebase: bool,
    reference_bounds,
) -> VoxelAsset:
    if operation == "MIRROR":
        return mirror_asset(asset, axis, reference_bounds=reference_bounds)
    if operation == "ROTATE":
        return rotate_asset(asset, axis, degrees, reference_bounds=reference_bounds)
    if operation == "TRANSLATE":
        return translate_asset(asset, offset)
    if operation == "CROP":
        if reference_bounds is None:
            return crop_asset(asset, rebase=rebase)
        return crop_asset(
            asset,
            reference_bounds[0],
            reference_bounds[1],
            rebase=rebase,
        )
    raise ValueError(f"unsupported volume operation: {operation!r}")


class VOXTART_OT_transform_asset(bpy.types.Operator):
    bl_idname = "voxtart.transform_asset"
    bl_label = "Transform Voxel Asset"
    bl_description = "Mirror, rotate, translate, or compact the active voxel asset and every layer"
    bl_options = {'REGISTER', 'UNDO'}

    operation: EnumProperty(name="Operation", items=_TRANSFORM_OPERATION_ITEMS, default="MIRROR")
    axis: EnumProperty(name="Axis", items=_TRANSFORM_AXIS_ITEMS, default="X")
    degrees: EnumProperty(name="Degrees", items=_TRANSFORM_DEGREE_ITEMS, default="90")
    offset: IntVectorProperty(name="Offset", size=3, default=(0, 0, 0))
    rebase: BoolProperty(
        name="Rebase To Origin",
        description="Move the cropped minimum cell to the local origin",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        obj = context.active_object
        try:
            asset = _load_asset(obj)
            stack = _load_layer_stack(obj, asset)
            reference_bounds = _layer_reference_bounds(stack, asset.bounds)
            original_minimum = reference_bounds[0] if reference_bounds is not None else None
            transformed = _apply_volume_operation(
                asset,
                self.operation,
                self.axis,
                int(self.degrees),
                self.offset,
                self.rebase,
                reference_bounds,
            )
            for layer in stack.layers:
                layer_asset = VoxelAsset(
                    grid_size=asset.grid_size,
                    palette=list(asset.palette),
                    voxels=dict(layer.voxels),
                )
                layer_result = _apply_volume_operation(
                    layer_asset,
                    self.operation,
                    self.axis,
                    int(self.degrees),
                    self.offset,
                    self.rebase,
                    reference_bounds,
                )
                layer.voxels = dict(layer_result.voxels)
            if self.operation == "CROP" and self.rebase and original_minimum is not None:
                local_offset = Vector(
                    tuple(axis * asset.grid_size for axis in original_minimum)
                )
                obj.matrix_world.translation += obj.matrix_world.to_3x3() @ local_offset
            _replace_asset_mesh(obj, transformed, layer_stack=stack)
            report = {
                "operation": self.operation,
                "axis": self.axis,
                "degrees": int(self.degrees),
                "offset": list(self.offset),
                "rebase": bool(self.rebase),
                "before_voxels": len(asset.voxels),
                "after_voxels": len(transformed.voxels),
                "layers": len(stack.layers),
            }
            obj["voxtart_volume_transform_report"] = json.dumps(report, sort_keys=True)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            f"{self.operation.lower()} transformed {len(asset.voxels)} to {len(transformed.voxels)} voxels",
        )
        return {'FINISHED'}


class VOXTART_PT_voxel_panel(bpy.types.Panel):
    bl_label = "Voxel Authoring (Advanced)"
    bl_idname = "VOXTART_PT_voxel_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'VOXTART'
    bl_order = 55
    bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "voxtart_grid_size")
        layout.prop(scene, "voxtart_cube_size")
        layout.label(text="Voxelizer", icon='MOD_REMESH')
        layout.prop(scene, "voxtart_voxelizer_mode", text="Mode")
        layout.prop(scene, "voxtart_voxelizer_shell_thickness", text="Shell Thickness")
        voxelize = layout.operator(VOXTART_OT_voxelize_active.bl_idname, icon='MOD_REMESH')
        voxelize.grid_size = scene.voxtart_grid_size
        voxelize.mode = scene.voxtart_voxelizer_mode
        voxelize.shell_thickness = scene.voxtart_voxelizer_shell_thickness
        if (
            context.active_object
            and ASSET_PROPERTY in context.active_object
            and VOXELIZER_SOURCE_PROPERTY in context.active_object
        ):
            layout.operator(VOXTART_OT_refresh_voxelizer.bl_idname, icon='FILE_REFRESH')
        layout.operator(VOXTART_OT_import_asset.bl_idname, text="Import VOXTART Asset")
        layout.separator()
        layout.label(text="Volume Tools", icon='MOD_DISPLACE')
        layout.prop(scene, "voxtart_transform_operation", text="Operation")
        operation = scene.voxtart_transform_operation
        layout.prop(scene, "voxtart_transform_axis", text="Axis")
        if operation == "ROTATE":
            layout.prop(scene, "voxtart_transform_degrees", text="Degrees")
        elif operation == "TRANSLATE":
            layout.prop(scene, "voxtart_transform_offset", text="Offset")
        elif operation == "CROP":
            layout.prop(scene, "voxtart_transform_rebase", text="Rebase To Origin")
        transform = layout.operator(VOXTART_OT_transform_asset.bl_idname, text="Apply Volume Tool")
        transform.operation = operation
        transform.axis = scene.voxtart_transform_axis
        transform.degrees = scene.voxtart_transform_degrees
        transform.offset = tuple(scene.voxtart_transform_offset)
        transform.rebase = scene.voxtart_transform_rebase

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
        layout.label(text="Picked Face Actions")
        picked_row = layout.row(align=True)
        place = picked_row.operator(VOXTART_OT_edit_picked_voxel.bl_idname, text="Place")
        place.action = 'add'
        place.palette_index = scene.voxtart_edit_palette_index
        place.allow_protected = scene.voxtart_edit_allow_protected
        picked_remove = picked_row.operator(VOXTART_OT_edit_picked_voxel.bl_idname, text="Remove")
        picked_remove.action = 'remove'
        picked_remove.allow_protected = scene.voxtart_edit_allow_protected
        picked_paint = picked_row.operator(VOXTART_OT_edit_picked_voxel.bl_idname, text="Paint")
        picked_paint.action = 'paint'
        picked_paint.palette_index = scene.voxtart_edit_palette_index
        picked_paint.allow_protected = scene.voxtart_edit_allow_protected
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
            asset, layer_stack = load_asset_with_layers(self.filepath)
            if bool(getattr(context.scene, "voxtart_palette_enforce", True)):
                asset, layer_stack = _retarget_asset_palette(
                    asset,
                    scene_palette(context.scene),
                    layer_stack,
                )
                asset.provenance["palette"] = str(getattr(context.scene, "voxtart_palette_name", "VOXTART Palette"))
                source = str(getattr(context.scene, "voxtart_palette_source", ""))
                if source:
                    asset.provenance["palette_source"] = source
            filename = Path(self.filepath).name
            suffix = ".voxel.json"
            name = filename[:-len(suffix)] if filename.endswith(suffix) else Path(filename).stem
            _create_asset_object(context, name or "VOXTART Asset", asset, layer_stack=layer_stack)
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
            obj = context.active_object
            save_asset(_load_asset(obj), self.filepath, _load_layer_stack(obj))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}




def _menu_add_voxel_cube(self, _context):
    self.layout.operator(VOXTART_OT_add_voxel_cube.bl_idname, icon='CUBE')


_CLASSES = (
    VOXTART_OT_add_voxel_cube,
    VOXTART_OT_voxelize_active,
    VOXTART_OT_refresh_voxelizer,
    VOXTART_OT_edit_picked_voxel,
    VOXTART_OT_add_voxel,
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
    VOXTART_OT_transform_asset,
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
    bpy.types.Scene.voxtart_voxelizer_mode = EnumProperty(
        name="Voxelizer Mode", items=_VOXELIZER_MODE_ITEMS, default="SOLID"
    )
    bpy.types.Scene.voxtart_voxelizer_shell_thickness = IntProperty(
        name="Voxelizer Shell Thickness",
        min=1,
        max=MAX_SHELL_THICKNESS,
        default=1,
    )
    bpy.types.Scene.voxtart_transform_operation = EnumProperty(
        name="Volume Operation",
        items=_TRANSFORM_OPERATION_ITEMS,
        default="MIRROR",
    )
    bpy.types.Scene.voxtart_transform_axis = EnumProperty(
        name="Transform Axis",
        items=_TRANSFORM_AXIS_ITEMS,
        default="X",
    )
    bpy.types.Scene.voxtart_transform_degrees = EnumProperty(
        name="Transform Degrees",
        items=_TRANSFORM_DEGREE_ITEMS,
        default="90",
    )
    bpy.types.Scene.voxtart_transform_offset = IntVectorProperty(
        name="Transform Offset",
        size=3,
        default=(0, 0, 0),
        min=-1024,
        max=1024,
    )
    bpy.types.Scene.voxtart_transform_rebase = BoolProperty(
        name="Rebase Cropped Volume",
        default=True,
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
    del bpy.types.Scene.voxtart_transform_rebase
    del bpy.types.Scene.voxtart_transform_offset
    del bpy.types.Scene.voxtart_transform_degrees
    del bpy.types.Scene.voxtart_transform_axis
    del bpy.types.Scene.voxtart_transform_operation
    del bpy.types.Scene.voxtart_voxelizer_shell_thickness
    del bpy.types.Scene.voxtart_voxelizer_mode
    del bpy.types.Scene.voxtart_cube_size
    del bpy.types.Scene.voxtart_grid_size
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
