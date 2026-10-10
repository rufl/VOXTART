# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Blender operators and panels for the VOXTART Blob Gallery."""


import json
import math
from pathlib import Path
from typing import Any

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    IntVectorProperty,
    StringProperty,
)
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Vector

from .asset_placer import (
    PLACEMENT_MODES,
    PlacementSettings,
    disc_samples,
    nearest_point,
    polyline_samples,
    randomized_transform,
    snap_to_grid,
    thumbnail_pixels,
)

from .blob_gallery import (
    BLOB_ENTRY_PROPERTY,
    BLOB_FILE_EXTENSION,
    BLOB_JOINTS_PROPERTY,
    BlobGallery,
    BlobGalleryEntry,
    BlobJoint,
    joints_from_asset,
    load_blob,
    make_blob_entry,
    save_blob,
)
from .kitbash import find_best_match
from .palette import MAX_PALETTE_COLORS, nearest_palette_index
from .scene_assembly import (
    ASSEMBLY_PROPERTY,
    SceneAssembly,
    ScenePlacement,
    new_placement_id,
)
from .voxel import VoxelAsset, VoxelSocket

from .operators import ASSET_PROPERTY


_NORMALS = {
    "NEG_X": (-1, 0, 0),
    "POS_X": (1, 0, 0),
    "NEG_Y": (0, -1, 0),
    "POS_Y": (0, 1, 0),
    "NEG_Z": (0, 0, -1),
    "POS_Z": (0, 0, 1),
}
_NORMAL_ITEMS = tuple(
    (identifier, label, description)
    for identifier, label, description in (
        ("NEG_X", "-X", "Joint faces negative X"),
        ("POS_X", "+X", "Joint faces positive X"),
        ("NEG_Y", "-Y", "Joint faces negative Y"),
        ("POS_Y", "+Y", "Joint faces positive Y"),
        ("NEG_Z", "-Z", "Joint faces negative Z"),
        ("POS_Z", "+Z", "Joint faces positive Z"),
    )
)


def _is_asset(obj: Any) -> bool:
    return obj is not None and ASSET_PROPERTY in obj


def _gallery_path(context: bpy.types.Context) -> Path:
    scene = context.scene
    configured = str(getattr(scene, "voxtart_blob_gallery_path", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    try:
        configured = bpy.utils.user_resource(
            "CONFIG",
            path="voxtart/blob_gallery",
            create=True,
        )
    except (AttributeError, RuntimeError, TypeError):
        configured = ""
    if not configured:
        configured = str(Path.home() / ".voxtart" / "blob_gallery")
    return Path(configured).expanduser()


def _gallery(context: bpy.types.Context) -> BlobGallery:
    return BlobGallery(_gallery_path(context))



def _gallery_entries(context: bpy.types.Context) -> tuple[tuple[BlobGalleryEntry, ...], tuple[str, ...]]:
    scene = context.scene
    return _gallery(context).scan(
        getattr(scene, "voxtart_blob_gallery_search", ""),
        getattr(scene, "voxtart_blob_gallery_category", ""),
        bool(getattr(scene, "voxtart_blob_gallery_favorites_only", False)),
    )



def _placement_settings(context: bpy.types.Context) -> PlacementSettings:
    scene = context.scene
    return PlacementSettings(
        seed=int(getattr(scene, "voxtart_zoo_random_seed", 0)),
        random_position=tuple(getattr(scene, "voxtart_zoo_random_position", (0.0, 0.0, 0.0))),
        random_rotation=tuple(getattr(scene, "voxtart_zoo_random_rotation", (0.0, 0.0, 0.0))),
        random_scale=tuple(getattr(scene, "voxtart_zoo_random_scale", (0.0, 0.0, 0.0))),
    )


def _placement_mode(context: bpy.types.Context) -> str:
    mode = str(getattr(context.scene, "voxtart_zoo_placement_mode", "FREE") or "FREE").upper()
    return mode if mode in PLACEMENT_MODES else "FREE"


def _placement_target_mesh(context: bpy.types.Context):
    candidates = tuple(
        obj for obj in getattr(context, "selected_objects", ())
        if getattr(obj, "type", None) == "MESH" and not _is_asset(obj)
    )
    active = getattr(context, "active_object", None)
    if active in candidates:
        return active
    return sorted(candidates, key=lambda obj: obj.name.casefold())[0] if candidates else None


def _surface_hit(context: bpy.types.Context, location: tuple[float, float, float]):
    scene = context.scene
    ray_origin = Vector(location)
    ray_origin.z += max(100.0, abs(ray_origin.z) + 100.0)
    ray_direction = Vector((0.0, 0.0, -1.0))
    try:
        depsgraph = context.evaluated_depsgraph_get()
    except (AttributeError, RuntimeError, TypeError):
        depsgraph = None
    for _attempt in range(16):
        try:
            result = (
                scene.ray_cast(depsgraph, ray_origin, ray_direction)
                if depsgraph is not None
                else scene.ray_cast(ray_origin, ray_direction)
            )
        except (AttributeError, RuntimeError, TypeError):
            if depsgraph is not None:
                depsgraph = None
                continue
            return None
        if not result or not result[0]:
            return None
        hit_object = result[4] if len(result) > 4 else None
        if not _is_asset(hit_object):
            hit_location = tuple(float(axis) for axis in result[1])
            normal = Vector(result[2])
            if normal.length <= 1.0e-8:
                return None
            return hit_location, tuple(float(axis) for axis in normal.normalized())
        ray_origin = Vector(result[1]) + ray_direction * 1.0e-4
    return None


def _nearest_vertex(context: bpy.types.Context, location: tuple[float, float, float]):
    target = _placement_target_mesh(context)
    if target is None:
        return None
    matrix = getattr(target, "matrix_world", None)
    points = (
        tuple(matrix @ Vector(vertex.co)) if matrix is not None else tuple(vertex.co)
        for vertex in getattr(getattr(target, "data", None), "vertices", ())
    )
    return nearest_point(points, location)


def _path_object(context: bpy.types.Context):
    scene = context.scene
    name = str(getattr(scene, "voxtart_zoo_path_object", "") or "").strip()
    path = bpy.data.objects.get(name) if name else None
    if path is not None and path.type == "CURVE":
        return path
    active = getattr(context, "active_object", None)
    if active is not None and active.type == "CURVE":
        return active
    return next(
        (obj for obj in getattr(context, "selected_objects", ()) if getattr(obj, "type", None) == "CURVE"),
        None,
    )


def _curve_points(path) -> tuple[tuple[float, float, float], ...]:
    matrix = getattr(path, "matrix_world", None)
    points = []
    for spline in getattr(getattr(path, "data", None), "splines", ()):
        source = getattr(spline, "bezier_points", ()) or getattr(spline, "points", ())
        for point in source:
            coordinate = Vector(point.co[:3])
            if len(point.co) > 3 and abs(float(point.co[3])) > 1.0e-8:
                coordinate /= float(point.co[3])
            world = matrix @ coordinate if matrix is not None else coordinate
            points.append(tuple(float(axis) for axis in world))
    return tuple(points)


def _align_rotation(rotation, normal):
    normal = Vector(normal)
    if normal.length <= 1.0e-8:
        return tuple(rotation)
    normal.normalize()
    alignment = Vector((0.0, 0.0, 1.0)).rotation_difference(normal)
    try:
        from mathutils import Euler
    except ImportError:
        return tuple(rotation)
    return tuple((alignment @ Euler(tuple(rotation), "XYZ").to_quaternion()).to_euler())


def _resolve_placement_transform(
    context: bpy.types.Context,
    location: tuple[float, float, float],
    rotation: tuple[float, float, float],
    scale: tuple[float, float, float],
    index: int,
    surface_normal=None,
):
    scene = context.scene
    mode = _placement_mode(context)
    resolved_location = tuple(float(axis) for axis in location)
    resolved_rotation = tuple(float(axis) for axis in rotation)
    normal = surface_normal
    if mode == "GRID":
        resolved_location = snap_to_grid(
            resolved_location,
            float(getattr(scene, "voxtart_zoo_grid_step", 1.0)),
        )
    elif mode == "VERTEX":
        resolved_location = _nearest_vertex(context, resolved_location) or resolved_location
    if mode == "SURFACE" or bool(getattr(scene, "voxtart_zoo_surface_snap", False)):
        hit = _surface_hit(context, resolved_location)
        if hit is not None:
            resolved_location, normal = hit
            resolved_location = tuple(
                resolved_location[axis] + normal[axis] * float(getattr(scene, "voxtart_zoo_surface_offset", 0.0))
                for axis in range(3)
            )
    if normal is not None and bool(getattr(scene, "voxtart_zoo_align_to_surface", True)):
        resolved_rotation = _align_rotation(resolved_rotation, normal)
    return randomized_transform(
        resolved_location,
        resolved_rotation,
        scale,
        index=index,
        settings=_placement_settings(context),
    )


def _scene_placer_settings(context: bpy.types.Context) -> dict[str, Any]:
    scene = context.scene
    return {
        "mode": _placement_mode(context),
        "placement_location": list(getattr(scene, "voxtart_zoo_placement_location", (0.0, 0.0, 0.0))),
        "placement_rotation": list(getattr(scene, "voxtart_zoo_placement_rotation", (0.0, 0.0, 0.0))),
        "placement_scale": list(getattr(scene, "voxtart_zoo_placement_scale", (1.0, 1.0, 1.0))),
        "category_spacing": float(getattr(scene, "voxtart_zoo_spacing", 4.0)),
        "grid_step": float(getattr(scene, "voxtart_zoo_grid_step", 1.0)),
        "surface_snap": bool(getattr(scene, "voxtart_zoo_surface_snap", False)),
        "surface_offset": float(getattr(scene, "voxtart_zoo_surface_offset", 0.0)),
        "align_to_surface": bool(getattr(scene, "voxtart_zoo_align_to_surface", True)),
        "brush_radius": float(getattr(scene, "voxtart_zoo_brush_radius", 4.0)),
        "brush_density": float(getattr(scene, "voxtart_zoo_brush_density", 1.0)),
        "path_object": str(getattr(scene, "voxtart_zoo_path_object", "")),
        "path_spacing": float(getattr(scene, "voxtart_zoo_path_spacing", 2.0)),
        "path_closed": bool(getattr(scene, "voxtart_zoo_path_closed", False)),
        "align_to_path": bool(getattr(scene, "voxtart_zoo_align_to_path", True)),
        "random_seed": int(getattr(scene, "voxtart_zoo_random_seed", 0)),
        "random_position": list(getattr(scene, "voxtart_zoo_random_position", (0.0, 0.0, 0.0))),
        "random_rotation": list(getattr(scene, "voxtart_zoo_random_rotation", (0.0, 0.0, 0.0))),
        "random_scale": list(getattr(scene, "voxtart_zoo_random_scale", (0.0, 0.0, 0.0))),
        "collision_preset": str(getattr(scene, "voxtart_zoo_collision_preset", "NONE")),
        "material_override": str(getattr(scene, "voxtart_zoo_material_override", "")),
        "instance_meshes": bool(getattr(scene, "voxtart_zoo_instance_meshes", True)),
    }
_PLACER_SETTING_PROPERTIES = {
    "mode": "voxtart_zoo_placement_mode",
    "placement_location": "voxtart_zoo_placement_location",
    "placement_rotation": "voxtart_zoo_placement_rotation",
    "placement_scale": "voxtart_zoo_placement_scale",
    "category_spacing": "voxtart_zoo_spacing",
    "grid_step": "voxtart_zoo_grid_step",
    "surface_snap": "voxtart_zoo_surface_snap",
    "surface_offset": "voxtart_zoo_surface_offset",
    "align_to_surface": "voxtart_zoo_align_to_surface",
    "brush_radius": "voxtart_zoo_brush_radius",
    "brush_density": "voxtart_zoo_brush_density",
    "path_object": "voxtart_zoo_path_object",
    "path_spacing": "voxtart_zoo_path_spacing",
    "path_closed": "voxtart_zoo_path_closed",
    "align_to_path": "voxtart_zoo_align_to_path",
    "random_seed": "voxtart_zoo_random_seed",
    "random_position": "voxtart_zoo_random_position",
    "random_rotation": "voxtart_zoo_random_rotation",
    "random_scale": "voxtart_zoo_random_scale",
    "collision_preset": "voxtart_zoo_collision_preset",
    "material_override": "voxtart_zoo_material_override",
    "instance_meshes": "voxtart_zoo_instance_meshes",
}


def _restore_scene_placer_settings(context: bpy.types.Context) -> int:
    assembly = _load_scene_assembly(context.scene)
    restored = 0
    for key, property_name in _PLACER_SETTING_PROPERTIES.items():
        if key not in assembly.settings or not hasattr(context.scene, property_name):
            continue
        setattr(context.scene, property_name, assembly.settings[key])
        restored += 1
    return restored

def _load_scene_assembly(scene: bpy.types.Scene) -> SceneAssembly:
    raw = scene.get(ASSEMBLY_PROPERTY)
    if not isinstance(raw, str) or not raw.strip():
        return SceneAssembly()
    try:
        return SceneAssembly.decode(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return SceneAssembly()


def _store_scene_assembly(scene: bpy.types.Scene, assembly: SceneAssembly) -> None:
    scene[ASSEMBLY_PROPERTY] = assembly.encode()


def _set_object_transform(
    obj: bpy.types.Object,
    location: tuple[float, float, float],
    rotation: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> None:
    obj.location = tuple(float(value) for value in location)
    obj.rotation_euler = tuple(float(value) for value in rotation)
    obj.scale = tuple(float(value) for value in scale)


def _apply_material_override(obj: bpy.types.Object, material_name: str) -> None:
    material_name = str(material_name or "").strip()
    if not material_name:
        return
    obj["voxtart_material_override"] = material_name
    materials_library = getattr(getattr(bpy, "data", None), "materials", None)
    material = materials_library.get(material_name) if materials_library is not None else None
    materials = getattr(getattr(obj, "data", None), "materials", None)
    if material is None or materials is None:
        obj["voxtart_material_override_pending"] = True
        return
    materials.clear()
    materials.append(material)


def _apply_collision_preset(context: bpy.types.Context, obj: bpy.types.Object, preset: str) -> None:
    preset = str(preset or "NONE").upper()
    obj["voxtart_collision_preset"] = preset
    if preset == "NONE":
        return
    shapes = {"BOX": "BOX", "CONVEX": "CONVEX_HULL", "MESH": "MESH"}
    shape = shapes.get(preset)
    if shape is None:
        obj["voxtart_collision_preset"] = "NONE"
        obj["voxtart_collision_pending"] = True
        return
    selected_before = tuple(getattr(context, "selected_objects", ()))
    active_before = getattr(context, "active_object", None)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if getattr(obj, "rigid_body", None) is None:
            bpy.ops.rigidbody.object_add()
        rigid_body = getattr(obj, "rigid_body", None)
        if rigid_body is None:
            raise RuntimeError("Blender did not create a rigid-body collision")
        rigid_body.type = "PASSIVE"
        rigid_body.collision_shape = shape
    except (AttributeError, RuntimeError, TypeError):
        obj["voxtart_collision_pending"] = True
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for previous in selected_before:
            previous.select_set(True)
        if active_before is not None:
            context.view_layer.objects.active = active_before


def _apply_placement_overrides(context: bpy.types.Context, obj: bpy.types.Object) -> None:
    scene = context.scene
    obj["voxtart_placer_mode"] = _placement_mode(context)
    obj["voxtart_placer_seed"] = int(getattr(scene, "voxtart_zoo_random_seed", 0))
    _apply_material_override(obj, getattr(scene, "voxtart_zoo_material_override", ""))
    _apply_collision_preset(
        context,
        obj,
        getattr(scene, "voxtart_zoo_collision_preset", "NONE"),
    )


def _record_placement(context: bpy.types.Context, obj: bpy.types.Object, entry: BlobGalleryEntry) -> None:
    placement_id = str(obj.get("voxtart_zoo_placement_id", "")).strip() or new_placement_id()
    placement = ScenePlacement(
        placement_id=placement_id,
        blob_id=entry.blob_id,
        name=entry.name,
        category=entry.category,
        location=tuple(obj.location),
        rotation=tuple(obj.rotation_euler),
        scale=tuple(obj.scale),
        object_name=obj.name,
    )
    assembly = _load_scene_assembly(context.scene)
    assembly.settings = _scene_placer_settings(context)
    assembly.upsert(placement)
    _store_scene_assembly(context.scene, assembly)
    obj["voxtart_zoo_placement_id"] = placement.placement_id
    obj["voxtart_zoo_blob_id"] = entry.blob_id


def _sync_scene_assembly(context: bpy.types.Context) -> int:
    assembly = _load_scene_assembly(context.scene)
    objects = {obj.name: obj for obj in context.scene.objects}
    updated = 0
    for placement in tuple(assembly.placements):
        obj = objects.get(placement.object_name)
        if obj is None or obj.get("voxtart_zoo_placement_id") != placement.placement_id:
            continue
        assembly.upsert(ScenePlacement(
            placement_id=placement.placement_id,
            blob_id=str(obj.get("voxtart_zoo_blob_id", placement.blob_id)),
            name=placement.name,
            category=placement.category,
            location=tuple(obj.location),
            rotation=tuple(obj.rotation_euler),
            scale=tuple(obj.scale),
            object_name=obj.name,
        ))
        updated += 1
    assembly.settings = _scene_placer_settings(context)
    _store_scene_assembly(context.scene, assembly)
    return updated

def _place_entry(
    context: bpy.types.Context,
    entry: BlobGalleryEntry,
    location: tuple[float, float, float],
    rotation: tuple[float, float, float],
    scale: tuple[float, float, float],
    *,
    placement_index: int = 0,
    surface_normal=None,
    mesh_source=None,
):
    obj = _instantiate_entry(context, entry, mesh_source=mesh_source)
    location, rotation, scale = _resolve_placement_transform(
        context,
        location,
        rotation,
        scale,
        placement_index,
        surface_normal=surface_normal,
    )
    _set_object_transform(obj, location, rotation, scale)
    _apply_placement_overrides(context, obj)
    _record_placement(context, obj, entry)
    return obj


def _cursor_location(context: bpy.types.Context) -> tuple[float, float, float]:
    cursor = getattr(context.scene, "cursor", None)
    location = getattr(cursor, "location", (0.0, 0.0, 0.0))
    return tuple(float(value) for value in location)


def _tags(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def _object_joint_records(obj: bpy.types.Object, asset) -> list[dict[str, Any]]:
    joints = joints_from_asset(asset, obj.get(BLOB_JOINTS_PROPERTY))
    return [
        {
            "name": joint.name,
            "role": joint.role,
            "bone": joint.bone,
            "socket_index": index,
        }
        for index, joint in enumerate(joints)
    ]


def _set_blob_metadata(obj: bpy.types.Object, entry: BlobGalleryEntry) -> None:
    obj[BLOB_ENTRY_PROPERTY] = entry.blob_id
    obj["voxtart_blob_gallery_name"] = entry.name
    obj["voxtart_blob_gallery_category"] = entry.category
    obj["voxtart_blob_gallery_author"] = entry.author
    obj["voxtart_blob_gallery_license"] = entry.license
    obj["voxtart_blob_gallery_tags"] = ",".join(entry.tags)
    obj[BLOB_JOINTS_PROPERTY] = json.dumps(
        [
            {
                "name": joint.name,
                "role": joint.role,
                "bone": joint.bone,
                "socket_index": index,
            }
            for index, joint in enumerate(entry.joints)
        ],
        sort_keys=True,
    )


def _entry_from_active(context: bpy.types.Context) -> BlobGalleryEntry:
    from . import operators

    obj = context.active_object
    if not _is_asset(obj):
        raise ValueError("select a VOXTART asset first")
    asset = operators._load_asset(obj)
    blob_id = str(obj.get(BLOB_ENTRY_PROPERTY, "")).strip() or None
    return make_blob_entry(
        asset,
        str(getattr(context.scene, "voxtart_blob_name", "Blob") or "Blob"),
        blob_id=blob_id,
        category=str(getattr(context.scene, "voxtart_blob_category", "Uncategorized") or "Uncategorized"),
        joints=joints_from_asset(asset, obj.get(BLOB_JOINTS_PROPERTY)),
        description=str(getattr(context.scene, "voxtart_blob_description", "") or ""),
        author=str(getattr(context.scene, "voxtart_blob_author", "") or ""),
        license=str(getattr(context.scene, "voxtart_blob_license", "") or ""),
        tags=_tags(getattr(context.scene, "voxtart_blob_tags", "")),
        source_url=str(getattr(context.scene, "voxtart_blob_source_url", "") or ""),
        provenance={
            "source_object": obj.name,
            "workflow": "voxtart_blob_gallery",
        },
    )


def _selected_assets(context: bpy.types.Context) -> tuple[bpy.types.Object, ...]:
    return tuple(sorted(
        (obj for obj in context.selected_objects if _is_asset(obj)),
        key=lambda obj: obj.name.casefold(),
    ))


def _asset_from_selection(context: bpy.types.Context) -> VoxelAsset:
    from . import operators

    objects = _selected_assets(context)
    if not objects:
        raise ValueError("select at least one VOXTART asset")
    active = context.active_object if _is_asset(context.active_object) else objects[0]
    active_asset = operators._load_asset(active)
    target_grid = active_asset.grid_size
    world_cells: list[tuple[tuple[float, float, float], tuple[float, float, float, float]]] = []
    for obj in objects:
        asset = operators._load_asset(obj)
        matrix = getattr(obj, "matrix_world", None)
        for cell, palette_index in asset.voxels.items():
            local = tuple(axis * asset.grid_size for axis in cell)
            world = matrix @ Vector(local) if matrix is not None else Vector(local)
            world_cells.append((
                tuple(float(world[axis]) / target_grid for axis in range(3)),
                asset.palette[palette_index],
            ))
    if not world_cells:
        raise ValueError("selected assets contain no voxels")
    minimum = tuple(math.floor(min(point[axis] for point, _color in world_cells)) for axis in range(3))
    palette = list(active_asset.palette)
    voxels: dict[tuple[int, int, int], int] = {}
    for point, color in world_cells:
        cell = tuple(int(round(point[axis] - minimum[axis])) for axis in range(3))
        try:
            palette_index = palette.index(color)
        except ValueError:
            if len(palette) < MAX_PALETTE_COLORS:
                palette.append(color)
                palette_index = len(palette) - 1
            else:
                palette_index = nearest_palette_index(color, palette)
        voxels[cell] = palette_index
    return VoxelAsset(
        grid_size=target_grid,
        palette=palette,
        voxels=voxels,
        provenance={
            "workflow": "voxtart_zoo_selection",
            "source_objects": ",".join(obj.name for obj in objects),
        },
    )


def _entry_from_selection(context: bpy.types.Context) -> BlobGalleryEntry:
    asset = _asset_from_selection(context)
    objects = _selected_assets(context)
    blob_id = str(objects[0].get(BLOB_ENTRY_PROPERTY, "")).strip() if len(objects) == 1 else ""
    return make_blob_entry(
        asset,
        str(getattr(context.scene, "voxtart_blob_name", "Scene Asset") or "Scene Asset"),
        blob_id=blob_id or None,
        category=str(getattr(context.scene, "voxtart_blob_category", "Uncategorized") or "Uncategorized"),
        description=str(getattr(context.scene, "voxtart_blob_description", "") or ""),
        author=str(getattr(context.scene, "voxtart_blob_author", "") or ""),
        license=str(getattr(context.scene, "voxtart_blob_license", "") or ""),
        tags=_tags(getattr(context.scene, "voxtart_blob_tags", "")),
        source_url=str(getattr(context.scene, "voxtart_blob_source_url", "") or ""),
        provenance={
            "workflow": "voxtart_zoo_selection",
            "source_objects": ",".join(obj.name for obj in objects),
        },
    )


def _instantiate_entry(context: bpy.types.Context, entry: BlobGalleryEntry, *, mesh_source=None):
    from . import operators

    obj = operators._create_asset_object(
        context,
        entry.name,
        entry.asset,
        backend="marching",
    )
    if (
        mesh_source is not None
        and bool(getattr(context.scene, "voxtart_zoo_instance_meshes", True))
        and getattr(mesh_source, "data", None) is not None
    ):
        obj.data = mesh_source.data
        obj["voxtart_shared_mesh_source"] = mesh_source.name
    _set_blob_metadata(obj, entry)
    return obj


def _snap_source_to_target(source_obj: bpy.types.Object, target_obj: bpy.types.Object) -> dict[str, Any]:
    from . import operators

    source_asset = operators._load_asset(source_obj)
    target_asset = operators._load_asset(target_obj)
    match = find_best_match(source_asset, target_asset)
    if match is None:
        raise ValueError("no compatible blob joints found")
    source_socket = source_asset.sockets[match.source_index]
    target_socket = target_asset.sockets[match.target_index]
    source_point = source_obj.matrix_world @ Vector(
        tuple(axis * source_asset.grid_size for axis in source_socket.position)
    )
    target_point = target_obj.matrix_world @ Vector(
        tuple(axis * target_asset.grid_size for axis in target_socket.position)
    )
    source_obj.matrix_world.translation += target_point - source_point
    result = {
        "target": target_obj.name,
        "source_socket": match.source_index,
        "target_socket": match.target_index,
    }
    source_obj["voxtart_last_blob_snap"] = json.dumps(result, sort_keys=True)
    return result


def _selected_armature(context: bpy.types.Context):
    candidates = tuple(
        obj for obj in context.selected_objects
        if obj.type == "ARMATURE" and obj != context.active_object
    )
    if candidates:
        return sorted(candidates, key=lambda obj: obj.name.casefold())[0]
    from . import animation

    target_name = str(getattr(context.scene, "voxtart_animation_target", "") or "")
    return animation._get_object(target_name, "ARMATURE")


def _bone_head_world(armature: bpy.types.Object, bone: Any) -> Vector:
    head = getattr(bone, "head_local", None)
    if head is None:
        head = getattr(bone, "head", None)
    if head is None:
        raise ValueError("selected bone has no head position")
    return armature.matrix_world @ Vector(head)


def _update_joint_bone(obj: bpy.types.Object, asset, socket_index: int, bone_name: str) -> None:
    records = _object_joint_records(obj, asset)
    for index, record in enumerate(records):
        if index == socket_index:
            record["bone"] = bone_name
    obj[BLOB_JOINTS_PROPERTY] = json.dumps(records, sort_keys=True)


_PREVIEW_COLLECTION = None


def _thumbnail_path(context: bpy.types.Context, entry: BlobGalleryEntry) -> Path:
    return _gallery(context).root / f"{entry.blob_id}.png"


def _ensure_thumbnail(context: bpy.types.Context, entry: BlobGalleryEntry) -> Path | None:
    path = _thumbnail_path(context, entry)
    if path.is_file():
        return path
    image = None
    try:
        gallery = _gallery(context)
        gallery.ensure()
        image = bpy.data.images.new(
            f"VOXTART Preview {entry.blob_id}",
            width=96,
            height=96,
            alpha=True,
        )
        image.pixels = thumbnail_pixels(entry.asset, 96, 96)
        image.filepath_raw = str(path)
        image.file_format = "PNG"
        image.save()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    finally:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                pass
    return path if path.is_file() else None


def _preview_icon(context: bpy.types.Context, entry: BlobGalleryEntry) -> int:
    global _PREVIEW_COLLECTION
    path = _ensure_thumbnail(context, entry)
    if path is None:
        return 0
    try:
        previews = bpy.utils.previews
        if _PREVIEW_COLLECTION is None:
            _PREVIEW_COLLECTION = previews.new()
        key = f"{path}:{path.stat().st_mtime_ns}"
        if key not in _PREVIEW_COLLECTION:
            _PREVIEW_COLLECTION.load(key, str(path), "IMAGE")
        return int(_PREVIEW_COLLECTION[key].icon_id)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return 0


def _release_previews() -> None:
    global _PREVIEW_COLLECTION
    if _PREVIEW_COLLECTION is None:
        return
    try:
        bpy.utils.previews.remove(_PREVIEW_COLLECTION)
    except (AttributeError, RuntimeError, TypeError):
        pass
    _PREVIEW_COLLECTION = None


class VOXTART_OT_blob_gallery_refresh(bpy.types.Operator):
    bl_idname = "voxtart.blob_gallery_refresh"
    bl_label = "Refresh Blob Gallery"
    bl_description = "Scan the configured Blob Gallery for validated portable blobs"

    def execute(self, context):
        entries, errors = _gallery_entries(context)
        if entries and not str(getattr(context.scene, "voxtart_blob_gallery_selected", "")):
            context.scene.voxtart_blob_gallery_selected = entries[0].blob_id
        message = f"found {len(entries)} blob(s)"
        if errors:
            message += f"; skipped {len(errors)} invalid file(s)"
        self.report({"WARNING" if errors else "INFO"}, message)
        return {"FINISHED"}


class VOXTART_OT_blob_gallery_toggle_favorite(bpy.types.Operator):
    bl_idname = "voxtart.blob_gallery_toggle_favorite"
    bl_label = "Toggle Favorite"
    bl_description = "Add or remove an asset from the local favorite set"
    bl_options = {"REGISTER", "UNDO"}

    blob_id: StringProperty(options={"HIDDEN"})
    favorite: BoolProperty(default=True, options={"HIDDEN"})

    def execute(self, context):
        try:
            _gallery(context).set_favorite(self.blob_id, self.favorite)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{'favorited' if self.favorite else 'unfavorited'} asset")
        return {"FINISHED"}


class VOXTART_OT_blob_gallery_save_active(bpy.types.Operator):
    bl_idname = "voxtart.blob_gallery_save_active"
    bl_label = "Save Active as Blob"
    bl_description = "Save the active VOXTART asset and its named joints into the Blob Gallery"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _is_asset(getattr(context, "active_object", None))

    def execute(self, context):
        try:
            entry = _entry_from_active(context)
            _gallery(context).save(entry)
            _set_blob_metadata(context.active_object, entry)
            context.scene.voxtart_blob_gallery_selected = entry.blob_id
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"saved {entry.name} to Blob Gallery")
        return {"FINISHED"}


class VOXTART_OT_zoo_save_selection(bpy.types.Operator):
    bl_idname = "voxtart.zoo_save_selection"
    bl_label = "Save Selection as Asset"
    bl_description = "Combine selected VOXTART assets into one categorized Zoo Gallery asset"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_selected_assets(context))

    def execute(self, context):
        try:
            entry = _entry_from_selection(context)
            _gallery(context).save(entry)
            objects = _selected_assets(context)
            if len(objects) == 1:
                _set_blob_metadata(objects[0], entry)
            context.scene.voxtart_blob_gallery_selected = entry.blob_id
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"saved {entry.name} to Zoo Gallery")
        return {"FINISHED"}


class VOXTART_OT_blob_gallery_insert(bpy.types.Operator):
    bl_idname = "voxtart.blob_gallery_insert"
    bl_label = "Insert Blob"
    bl_description = "Insert a Blob Gallery asset, optionally snapping it to a selected blob"
    bl_options = {"REGISTER", "UNDO"}

    blob_id: StringProperty(options={"HIDDEN"})
    snap_to_selection: BoolProperty(name="Snap to Selected Blob", default=True)

    def execute(self, context):
        targets = tuple(sorted(
            (obj for obj in context.selected_objects if _is_asset(obj)),
            key=lambda obj: obj.name.casefold(),
        ))
        try:
            entry = _gallery(context).load(self.blob_id)
            obj = _instantiate_entry(context, entry)
            if self.snap_to_selection and targets:
                try:
                    result = _snap_source_to_target(obj, targets[0])
                except ValueError as error:
                    self.report({"WARNING"}, f"inserted {entry.name}; {error}")
                else:
                    self.report({"INFO"}, f"inserted and snapped {entry.name} to {result['target']}")
            else:
                self.report({"INFO"}, f"inserted {entry.name}")
            context.scene.voxtart_blob_gallery_selected = entry.blob_id
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_zoo_place_asset(bpy.types.Operator):
    bl_idname = "voxtart.zoo_place_asset"
    bl_label = "Place Asset"
    bl_description = "Instantiate a categorized Zoo Gallery asset at the chosen transform"
    bl_options = {"REGISTER", "UNDO"}

    blob_id: StringProperty(options={"HIDDEN"})
    location: FloatVectorProperty(name="Location", size=3, default=(0.0, 0.0, 0.0))
    rotation: FloatVectorProperty(name="Rotation", size=3, default=(0.0, 0.0, 0.0))
    scale: FloatVectorProperty(name="Scale", size=3, min=0.001, max=100.0, default=(1.0, 1.0, 1.0))

    def execute(self, context):
        try:
            entry = _gallery(context).load(self.blob_id)
            _place_entry(
                context,
                entry,
                tuple(self.location),
                tuple(self.rotation),
                tuple(self.scale),
            )
            context.scene.voxtart_blob_gallery_selected = entry.blob_id
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"placed {entry.name}")
        return {"FINISHED"}


class VOXTART_OT_zoo_place_category(bpy.types.Operator):
    bl_idname = "voxtart.zoo_place_category"
    bl_label = "Place Category"
    bl_description = "Place every asset in the selected Zoo Gallery category on a simple scene grid"
    bl_options = {"REGISTER", "UNDO"}

    category: StringProperty(options={"HIDDEN"})
    origin: FloatVectorProperty(name="Origin", size=3, default=(0.0, 0.0, 0.0))
    spacing: FloatProperty(name="Spacing", min=0.1, max=100.0, default=4.0)
    rotation: FloatVectorProperty(name="Rotation", size=3, default=(0.0, 0.0, 0.0))
    scale: FloatVectorProperty(name="Scale", size=3, min=0.001, max=100.0, default=(1.0, 1.0, 1.0))

    def execute(self, context):
        try:
            query = getattr(context.scene, "voxtart_blob_gallery_search", "")
            entries, errors = _gallery(context).scan(
                query,
                self.category,
                bool(getattr(context.scene, "voxtart_blob_gallery_favorites_only", False)),
            )
            if not entries:
                raise ValueError("the selected Zoo Gallery category has no matching assets")
            columns = max(1, math.ceil(math.sqrt(len(entries))))
            for index, entry in enumerate(entries):
                column = index % columns
                row = index // columns
                location = (
                    float(self.origin[0]) + column * self.spacing,
                    float(self.origin[1]) + row * self.spacing,
                    float(self.origin[2]),
                )
                _place_entry(
                    context,
                    entry,
                    location,
                    tuple(self.rotation),
                    tuple(self.scale),
                    placement_index=index,
                )
            context.scene.voxtart_blob_gallery_selected = entries[0].blob_id
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        message = f"placed {len(entries)} asset(s)"
        if errors:
            message += f"; skipped {len(errors)} invalid file(s)"
            self.report({"WARNING"}, message)
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}


class VOXTART_OT_zoo_brush_paint(bpy.types.Operator):
    bl_idname = "voxtart.zoo_brush_paint"
    bl_label = "Paint Assets"
    bl_description = "Scatter the selected gallery asset around the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    blob_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        scene = context.scene
        try:
            entry = _gallery(context).load(self.blob_id)
            radius = float(getattr(scene, "voxtart_zoo_brush_radius", 4.0))
            density = float(getattr(scene, "voxtart_zoo_brush_density", 1.0))
            count = max(1, min(2048, int(round(math.pi * radius * radius * density))))
            positions = disc_samples(
                _cursor_location(context),
                radius,
                count,
                seed=int(getattr(scene, "voxtart_zoo_random_seed", 0)),
            )
            mesh_source = None
            for index, location in enumerate(positions):
                obj = _place_entry(
                    context,
                    entry,
                    location,
                    tuple(scene.voxtart_zoo_placement_rotation),
                    tuple(scene.voxtart_zoo_placement_scale),
                    placement_index=index,
                    mesh_source=mesh_source,
                )
                mesh_source = mesh_source or obj
            scene.voxtart_blob_gallery_selected = entry.blob_id
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"painted {len(positions)} {entry.name} instance(s)")
        return {"FINISHED"}


class VOXTART_OT_zoo_place_path(bpy.types.Operator):
    bl_idname = "voxtart.zoo_place_path"
    bl_label = "Distribute Along Path"
    bl_description = "Repeat the selected gallery asset at even distances along a curve"
    bl_options = {"REGISTER", "UNDO"}

    blob_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        scene = context.scene
        try:
            entry = _gallery(context).load(self.blob_id)
            path = _path_object(context)
            if path is None:
                raise ValueError("choose a curve as Path Object")
            points = _curve_points(path)
            samples = polyline_samples(
                points,
                float(getattr(scene, "voxtart_zoo_path_spacing", 2.0)),
                closed=bool(getattr(scene, "voxtart_zoo_path_closed", False)),
            )
            if not samples:
                raise ValueError("the selected curve has no usable points")
            base_rotation = tuple(scene.voxtart_zoo_placement_rotation)
            mesh_source = None
            for index, location in enumerate(samples):
                rotation = base_rotation
                if bool(getattr(scene, "voxtart_zoo_align_to_path", True)) and len(samples) > 1:
                    previous = samples[max(0, index - 1)]
                    following = samples[min(len(samples) - 1, index + 1)]
                    tangent = Vector(following) - Vector(previous)
                    if tangent.length > 1.0e-8:
                        rotation = (
                            base_rotation[0],
                            base_rotation[1],
                            base_rotation[2] + math.atan2(tangent.y, tangent.x),
                        )
                obj = _place_entry(
                    context,
                    entry,
                    location,
                    rotation,
                    tuple(scene.voxtart_zoo_placement_scale),
                    placement_index=index,
                    mesh_source=mesh_source,
                )
                mesh_source = mesh_source or obj
            scene.voxtart_blob_gallery_selected = entry.blob_id
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"distributed {len(samples)} {entry.name} instance(s)")
        return {"FINISHED"}


class VOXTART_OT_zoo_apply_overrides(bpy.types.Operator):
    bl_idname = "voxtart.zoo_apply_overrides"
    bl_label = "Apply Placement Overrides"
    bl_description = "Apply the selected collision and material settings to placed assets"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        assets = tuple(obj for obj in context.selected_objects if _is_asset(obj))
        if not assets:
            self.report({"ERROR"}, "select at least one placed asset")
            return {"CANCELLED"}
        for obj in assets:
            _apply_placement_overrides(context, obj)
        _sync_scene_assembly(context)
        self.report({"INFO"}, f"updated {len(assets)} placed asset(s)")
        return {"FINISHED"}


class VOXTART_OT_zoo_restore_settings(bpy.types.Operator):
    bl_idname = "voxtart.zoo_restore_settings"
    bl_label = "Restore Saved Settings"
    bl_description = "Restore the placement settings stored in the current scene assembly"

    def execute(self, context):
        restored = _restore_scene_placer_settings(context)
        if not restored:
            self.report({"WARNING"}, "the scene has no saved asset placer settings")
            return {"CANCELLED"}
        self.report({"INFO"}, f"restored {restored} asset placer setting(s)")
        return {"FINISHED"}


class VOXTART_OT_zoo_sync_placements(bpy.types.Operator):
    bl_idname = "voxtart.zoo_sync_placements"
    bl_label = "Sync Layout"
    bl_description = "Store the current transforms of Zoo-placed assets in the scene assembly"

    def execute(self, context):
        try:
            updated = _sync_scene_assembly(context)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"synced {updated} placement(s)")
        return {"FINISHED"}


class VOXTART_OT_zoo_clear_placements(bpy.types.Operator):
    bl_idname = "voxtart.zoo_clear_placements"
    bl_label = "Clear Placed Assets"
    bl_description = "Remove assets created by the Zoo Asset Placer without touching other scene objects"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            placed = tuple(
                obj for obj in context.scene.objects
                if obj.get("voxtart_zoo_placement_id")
            )
            for obj in placed:
                bpy.data.objects.remove(obj, do_unlink=True)
            if ASSEMBLY_PROPERTY in context.scene:
                del context.scene[ASSEMBLY_PROPERTY]
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"cleared {len(placed)} placed asset(s)")
        return {"FINISHED"}


class VOXTART_OT_blob_gallery_remove(bpy.types.Operator):
    bl_idname = "voxtart.blob_gallery_remove"
    bl_label = "Remove Blob"
    bl_description = "Remove the selected blob document from the local Blob Gallery"

    blob_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        try:
            _gallery(context).remove(self.blob_id)
        except (OSError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        if getattr(context.scene, "voxtart_blob_gallery_selected", "") == self.blob_id:
            context.scene.voxtart_blob_gallery_selected = ""
        self.report({"INFO"}, "removed blob from Blob Gallery")
        return {"FINISHED"}


class VOXTART_OT_blob_gallery_add_joint(bpy.types.Operator):
    bl_idname = "voxtart.blob_gallery_add_joint"
    bl_label = "Add Blob Joint"
    bl_description = "Add a named snappable joint to the active blob asset"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="socket")
    role: StringProperty(name="Role", default="custom")
    bone: StringProperty(name="Bone", default="")
    normal: EnumProperty(name="Normal", items=_NORMAL_ITEMS, default="POS_X")

    @classmethod
    def poll(cls, context):
        return _is_asset(getattr(context, "active_object", None))

    def execute(self, context):
        from . import operators

        try:
            obj = context.active_object
            asset = operators._load_asset(obj)
            socket = VoxelSocket(
                position=tuple(int(axis) for axis in context.scene.voxtart_blob_joint_position),
                normal=_NORMALS[self.normal],
                tag=str(self.role or "custom").strip() or "custom",
            )
            asset.sockets.append(socket)
            operators._store_asset(obj, asset)
            records = _object_joint_records(obj, asset)
            records[-1].update({
                "name": str(self.name or f"socket_{len(records):02d}").strip(),
                "role": str(self.role or "custom").strip() or "custom",
                "bone": str(self.bone or "").strip(),
                "socket_index": len(asset.sockets) - 1,
            })
            obj[BLOB_JOINTS_PROPERTY] = json.dumps(records, sort_keys=True)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"added joint {records[-1]['name']}")
        return {"FINISHED"}


class VOXTART_OT_blob_snap_to_bone(bpy.types.Operator):
    bl_idname = "voxtart.blob_snap_to_bone"
    bl_label = "Snap Blob to Bone"
    bl_description = "Place a blob joint at an armature bone head and optionally parent it to that bone"
    bl_options = {"REGISTER", "UNDO"}

    bone_name: StringProperty(name="Bone", default="")
    joint_index: IntProperty(name="Joint", min=0, default=0)
    parent_to_bone: BoolProperty(name="Parent to Bone", default=True)

    @classmethod
    def poll(cls, context):
        return _is_asset(getattr(context, "active_object", None)) and _selected_armature(context) is not None

    def execute(self, context):
        from . import operators

        try:
            obj = context.active_object
            armature = _selected_armature(context)
            asset = operators._load_asset(obj)
            if armature is None:
                raise ValueError("select a target armature")
            if not 0 <= self.joint_index < len(asset.sockets):
                raise ValueError("joint index is outside the blob joint list")
            if self.bone_name:
                bone = armature.data.bones.get(self.bone_name)
                if bone is None:
                    raise ValueError(f"target armature has no bone named {self.bone_name}")
            else:
                active_bone = getattr(armature.data.bones, "active", None)
                bone = active_bone or next(iter(armature.data.bones), None)
            if bone is None:
                raise ValueError("target armature has no bones")
            socket = asset.sockets[self.joint_index]
            source_point = obj.matrix_world @ Vector(
                tuple(axis * asset.grid_size for axis in socket.position)
            )
            obj.matrix_world.translation += _bone_head_world(armature, bone) - source_point
            if self.parent_to_bone:
                world = obj.matrix_world.copy()
                obj.parent = armature
                obj.parent_type = "BONE"
                obj.parent_bone = bone.name
                obj.matrix_world = world
            obj["voxtart_blob_skeleton_armature"] = armature.name
            obj["voxtart_blob_skeleton_bone"] = bone.name
            obj["voxtart_blob_skeleton_joint"] = self.joint_index
            _update_joint_bone(obj, asset, self.joint_index, bone.name)
        except (AttributeError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"snapped joint to {bone.name}")
        return {"FINISHED"}


class VOXTART_OT_blob_gallery_export(bpy.types.Operator, ExportHelper):
    bl_idname = "voxtart.blob_gallery_export"
    bl_label = "Export Shared Blob"
    bl_description = "Export the active blob and its metadata as a portable shareable file"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = BLOB_FILE_EXTENSION
    filter_glob: StringProperty(default=f"*{BLOB_FILE_EXTENSION}", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _is_asset(getattr(context, "active_object", None))

    def execute(self, context):
        try:
            save_blob(_entry_from_active(context), self.filepath)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"exported shared blob {Path(self.filepath).name}")
        return {"FINISHED"}


class VOXTART_OT_blob_gallery_import(bpy.types.Operator, ImportHelper):
    bl_idname = "voxtart.blob_gallery_import"
    bl_label = "Import Shared Blob"
    bl_description = "Validate a shared blob file, add it to the local gallery, and insert it"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = BLOB_FILE_EXTENSION
    filter_glob: StringProperty(default=f"*{BLOB_FILE_EXTENSION}", options={"HIDDEN"})

    snap_to_selection: BoolProperty(name="Snap to Selected Blob", default=True)

    def execute(self, context):
        targets = tuple(sorted(
            (obj for obj in context.selected_objects if _is_asset(obj)),
            key=lambda obj: obj.name.casefold(),
        ))
        try:
            entry = load_blob(self.filepath)
            _gallery(context).save(entry)
            obj = _instantiate_entry(context, entry)
            if self.snap_to_selection and targets:
                try:
                    _snap_source_to_target(obj, targets[0])
                except ValueError as error:
                    self.report({"WARNING"}, f"imported {entry.name}; {error}")
            context.scene.voxtart_blob_gallery_selected = entry.blob_id
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"imported {entry.name} into Blob Gallery")
        return {"FINISHED"}


class VOXTART_PT_blob_gallery(bpy.types.Panel):
    bl_label = "Zoo Gallery"
    bl_idname = "VOXTART_PT_blob_gallery"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 15

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "voxtart_blob_gallery_path", text="Gallery")
        create_row = layout.row(align=True)
        create_row.operator("voxtart.lasso_blob", text="Lasso Blob", icon="MESH_UVSPHERE")
        create_row.operator("voxtart.add_voxel_cube", text="New Blob", icon="CUBE")
        row = layout.row(align=True)
        row.operator(VOXTART_OT_blob_gallery_refresh.bl_idname, text="Refresh", icon="FILE_REFRESH")
        row.operator(VOXTART_OT_blob_gallery_import.bl_idname, text="Import", icon="IMPORT")
        layout.prop(scene, "voxtart_blob_gallery_search", text="Search / Tags")
        layout.prop(scene, "voxtart_blob_gallery_category", text="Category")
        layout.prop(scene, "voxtart_blob_gallery_favorites_only", text="Favorites Only")
        try:
            entries, errors = _gallery_entries(context)
        except (OSError, ValueError) as error:
            layout.label(text=str(error), icon="ERROR")
            entries, errors = (), ()
        favorites = _gallery(context).favorites()
        if not entries:
            layout.label(text="No assets yet — save the active or selected art.", icon="INFO")
        else:
            for entry in entries[:24]:
                row = layout.row(align=True)
                icon_id = _preview_icon(context, entry)
                if icon_id:
                    row.label(text="", icon_value=icon_id)
                insert = row.operator(
                    VOXTART_OT_blob_gallery_insert.bl_idname,
                    text=entry.name,
                    icon="MESH_UVSPHERE",
                )
                insert.blob_id = entry.blob_id
                insert.snap_to_selection = True
                favorite = row.operator(
                    VOXTART_OT_blob_gallery_toggle_favorite.bl_idname,
                    text="",
                    icon="SOLO_ON" if entry.blob_id in favorites else "SOLO_OFF",
                )
                favorite.blob_id = entry.blob_id
                favorite.favorite = entry.blob_id not in favorites
                remove = row.operator(VOXTART_OT_blob_gallery_remove.bl_idname, text="", icon="X")
                remove.blob_id = entry.blob_id
                row.label(text=f"{entry.category} · {len(entry.asset.voxels)}v / {len(entry.joints)}j · {', '.join(entry.tags[:3])}")
        if len(tuple(obj for obj in context.selected_objects if _is_asset(obj))) >= 2:
            layout.operator("voxtart.snap_active", text="Snap Active Blob", icon="CONSTRAINT")
        if errors:
            layout.label(text=f"Skipped {len(errors)} invalid file(s)", icon="ERROR")

        layout.separator()
        layout.label(text="Active asset metadata")
        layout.prop(scene, "voxtart_blob_name", text="Name")
        layout.prop(scene, "voxtart_blob_category", text="Category")
        layout.prop(scene, "voxtart_blob_author", text="Author")
        layout.prop(scene, "voxtart_blob_license", text="License")
        layout.prop(scene, "voxtart_blob_tags", text="Tags")
        layout.prop(scene, "voxtart_blob_source_url", text="Source URL")
        layout.prop(scene, "voxtart_blob_description", text="Description")
        row = layout.row(align=True)
        row.operator(VOXTART_OT_blob_gallery_save_active.bl_idname, text="Save Active", icon="FILE_TICK")
        row.operator(VOXTART_OT_zoo_save_selection.bl_idname, text="Save Selection", icon="FILE_TICK")
        row.operator(VOXTART_OT_blob_gallery_export.bl_idname, text="Share", icon="EXPORT")

        if _is_asset(getattr(context, "active_object", None)):
            layout.separator()
            layout.label(text="Add snappable joint")
            layout.prop(scene, "voxtart_blob_joint_name", text="Name")
            layout.prop(scene, "voxtart_blob_joint_role", text="Role / tag")
            layout.prop(scene, "voxtart_blob_joint_bone", text="Bone hint")
            layout.prop(scene, "voxtart_blob_joint_position", text="Position")
            add_joint = layout.operator(VOXTART_OT_blob_gallery_add_joint.bl_idname, text="Add Joint", icon="PIVOT_CURSOR")
            add_joint.name = scene.voxtart_blob_joint_name
            add_joint.role = scene.voxtart_blob_joint_role
            add_joint.bone = scene.voxtart_blob_joint_bone
            add_joint.normal = scene.voxtart_blob_joint_normal

            layout.label(text="Assemble with an armature")
            layout.prop(scene, "voxtart_blob_snap_bone", text="Bone")
            snap = layout.operator(VOXTART_OT_blob_snap_to_bone.bl_idname, text="Snap Joint to Bone", icon="BONE_DATA")
            snap.bone_name = scene.voxtart_blob_snap_bone
            snap.joint_index = scene.voxtart_blob_snap_joint
            snap.parent_to_bone = scene.voxtart_blob_parent_to_bone
            layout.prop(scene, "voxtart_blob_snap_joint", text="Joint index")
class VOXTART_PT_zoo_asset_placer(bpy.types.Panel):
    bl_label = "Zoo Asset Placer"
    bl_idname = "VOXTART_PT_zoo_asset_placer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 16

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.label(text="Compose a scene from categorized Zoo assets.", icon="SCENE")
        layout.prop(scene, "voxtart_blob_gallery_search", text="Search / Tags")
        layout.prop(scene, "voxtart_blob_gallery_category", text="Category")
        layout.prop(scene, "voxtart_blob_gallery_favorites_only", text="Favorites Only")

        transform = layout.box()
        transform.label(text="Placement Transform")
        transform.prop(scene, "voxtart_zoo_placement_mode", text="Mode")
        transform.prop(scene, "voxtart_zoo_placement_location", text="Location")
        transform.prop(scene, "voxtart_zoo_placement_rotation", text="Rotation")
        transform.prop(scene, "voxtart_zoo_placement_scale", text="Scale")
        mode = str(getattr(scene, "voxtart_zoo_placement_mode", "FREE"))
        if mode == "GRID":
            transform.prop(scene, "voxtart_zoo_grid_step", text="Grid Step")
        if mode == "SURFACE" or bool(getattr(scene, "voxtart_zoo_surface_snap", False)):
            transform.prop(scene, "voxtart_zoo_surface_snap", text="Surface Snap")
            transform.prop(scene, "voxtart_zoo_surface_offset", text="Surface Offset")
            transform.prop(scene, "voxtart_zoo_align_to_surface", text="Align to Normal")
        if mode == "VERTEX":
            transform.label(text="Uses the nearest vertex of a selected non-asset mesh.", icon="INFO")

        random_box = layout.box()
        random_box.label(text="Transform Randomization")
        random_box.prop(scene, "voxtart_zoo_random_seed", text="Seed")
        random_box.prop(scene, "voxtart_zoo_random_position", text="Position ±")
        random_box.prop(scene, "voxtart_zoo_random_rotation", text="Rotation ±°")
        random_box.prop(scene, "voxtart_zoo_random_scale", text="Scale ±")

        override_box = layout.box()
        override_box.label(text="Collision and Material Overrides")
        override_box.prop(scene, "voxtart_zoo_collision_preset", text="Collision")
        override_box.prop_search(scene, "voxtart_zoo_material_override", bpy.data, "materials", text="Material")
        override_box.prop(scene, "voxtart_zoo_instance_meshes", text="Shared Mesh Instances")
        override_box.operator(VOXTART_OT_zoo_apply_overrides.bl_idname, icon="MOD_PHYSICS")

        selected_id = str(getattr(scene, "voxtart_blob_gallery_selected", "") or "")
        if selected_id:
            place = transform.operator(
                VOXTART_OT_zoo_place_asset.bl_idname,
                text="Place Selected Asset",
                icon="ADD",
            )
            place.blob_id = selected_id
            place.location = tuple(scene.voxtart_zoo_placement_location)
            place.rotation = tuple(scene.voxtart_zoo_placement_rotation)
            place.scale = tuple(scene.voxtart_zoo_placement_scale)
            brush = transform.operator(
                VOXTART_OT_zoo_brush_paint.bl_idname,
                text="Paint Selected Asset",
                icon="BRUSH_DATA",
            )
            brush.blob_id = selected_id

        place_category = transform.operator(
            VOXTART_OT_zoo_place_category.bl_idname,
            text="Place Current Category",
            icon="OUTLINER_COLLECTION",
        )
        place_category.category = str(scene.voxtart_blob_gallery_category or "")
        place_category.origin = tuple(scene.voxtart_zoo_placement_location)
        place_category.spacing = scene.voxtart_zoo_spacing
        place_category.rotation = tuple(scene.voxtart_zoo_placement_rotation)
        place_category.scale = tuple(scene.voxtart_zoo_placement_scale)

        brush_box = layout.box()
        brush_box.label(text="Brush / Foliage Painting", icon="BRUSH_DATA")
        brush_box.prop(scene, "voxtart_zoo_brush_radius", text="Radius")
        brush_box.prop(scene, "voxtart_zoo_brush_density", text="Density")
        brush_box.label(text="Uses deterministic shared-mesh instances.", icon="INFO")

        path_box = layout.box()
        path_box.label(text="Path / Spline Distribution", icon="CURVE_DATA")
        path_box.prop_search(scene, "voxtart_zoo_path_object", bpy.data, "objects", text="Path Object")
        path_box.prop(scene, "voxtart_zoo_path_spacing", text="Spacing")
        path_box.prop(scene, "voxtart_zoo_path_closed", text="Closed Path")
        path_box.prop(scene, "voxtart_zoo_align_to_path", text="Align to Tangent")
        if selected_id:
            path_operator = path_box.operator(
                VOXTART_OT_zoo_place_path.bl_idname,
                text="Distribute Selected Asset",
                icon="CURVE_DATA",
            )
            path_operator.blob_id = selected_id

        try:
            entries, errors = _gallery_entries(context)
        except (OSError, ValueError) as error:
            layout.label(text=str(error), icon="ERROR")
            entries, errors = (), ()
        favorites = _gallery(context).favorites()
        layout.label(text=f"{len(entries)} matching asset(s)", icon="ASSET_MANAGER")
        for entry in entries[:24]:
            row = layout.row(align=True)
            icon_id = _preview_icon(context, entry)
            if icon_id:
                row.label(text="", icon_value=icon_id)
            row.label(text=f"{entry.category} / {entry.name}", icon="MESH_UVSPHERE")
            place = row.operator(VOXTART_OT_zoo_place_asset.bl_idname, text="Place", icon="ADD")
            place.blob_id = entry.blob_id
            place.location = tuple(scene.voxtart_zoo_placement_location)
            place.rotation = tuple(scene.voxtart_zoo_placement_rotation)
            place.scale = tuple(scene.voxtart_zoo_placement_scale)
            favorite = row.operator(
                VOXTART_OT_blob_gallery_toggle_favorite.bl_idname,
                text="",
                icon="SOLO_ON" if entry.blob_id in favorites else "SOLO_OFF",
            )
            favorite.blob_id = entry.blob_id
            favorite.favorite = entry.blob_id not in favorites
        if errors:
            layout.label(text=f"Skipped {len(errors)} invalid asset file(s)", icon="ERROR")

        row = layout.row(align=True)
        row.operator(VOXTART_OT_zoo_sync_placements.bl_idname, text="Sync Layout", icon="FILE_REFRESH")
        row.operator(VOXTART_OT_zoo_clear_placements.bl_idname, text="Clear Placed", icon="TRASH")
        row.operator(VOXTART_OT_zoo_restore_settings.bl_idname, text="Restore Settings", icon="FILE_REFRESH")

        help_box = layout.box()
        help_box.label(text="Placement Help", icon="QUESTION")
        help_box.label(text="FREE: cursor placement · GRID: modular snapping")
        help_box.label(text="SURFACE: downward geometry raycast · VERTEX: nearest vertex")
        help_box.label(text="BRUSH: paint around cursor · PATH: repeat along a curve")
        help_box.label(text="Shift+Right Click moves the 3D Cursor; save layout to persist settings.")

        save = layout.box()
        save.label(text="Save created art as a categorized asset", icon="FILE_TICK")
        save.prop(scene, "voxtart_blob_name", text="Name")
        save.prop(scene, "voxtart_blob_category", text="Category")
        save.prop(scene, "voxtart_blob_tags", text="Tags")
        save.operator(VOXTART_OT_zoo_save_selection.bl_idname, text="Save Selected Art", icon="FILE_TICK")



_CLASSES = (
    VOXTART_OT_blob_gallery_refresh,
    VOXTART_OT_blob_gallery_toggle_favorite,
    VOXTART_OT_blob_gallery_save_active,
    VOXTART_OT_zoo_save_selection,
    VOXTART_OT_blob_gallery_insert,
    VOXTART_OT_zoo_place_asset,
    VOXTART_OT_zoo_place_category,
    VOXTART_OT_zoo_brush_paint,
    VOXTART_OT_zoo_place_path,
    VOXTART_OT_zoo_apply_overrides,
    VOXTART_OT_zoo_sync_placements,
    VOXTART_OT_zoo_clear_placements,
    VOXTART_OT_zoo_restore_settings,
    VOXTART_OT_blob_gallery_remove,
    VOXTART_OT_blob_gallery_add_joint,
    VOXTART_OT_blob_snap_to_bone,
    VOXTART_OT_blob_gallery_export,
    VOXTART_OT_blob_gallery_import,
    VOXTART_PT_blob_gallery,
    VOXTART_PT_zoo_asset_placer,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_blob_gallery_path = StringProperty(
        name="Blob Gallery Path",
        subtype="DIR_PATH",
        default="",
    )
    bpy.types.Scene.voxtart_blob_gallery_search = StringProperty(
        name="Blob Search",
        default="",
    )
    bpy.types.Scene.voxtart_blob_gallery_selected = StringProperty(
        name="Selected Blob",
        default="",
    )
    bpy.types.Scene.voxtart_blob_gallery_category = StringProperty(
        name="Gallery Category Filter", default=""
    )
    bpy.types.Scene.voxtart_blob_gallery_favorites_only = BoolProperty(
        name="Favorites Only",
        description="Show only assets marked as favorites in the local gallery",
        default=False,
    )
    bpy.types.Scene.voxtart_blob_category = StringProperty(
        name="Asset Category", default="Uncategorized"
    )
    bpy.types.Scene.voxtart_zoo_placement_location = FloatVectorProperty(
        name="Placement Location", size=3, default=(0.0, 0.0, 0.0)
    )
    bpy.types.Scene.voxtart_zoo_placement_rotation = FloatVectorProperty(
        name="Placement Rotation", size=3, default=(0.0, 0.0, 0.0)
    )
    bpy.types.Scene.voxtart_zoo_placement_scale = FloatVectorProperty(
        name="Placement Scale", size=3, min=0.001, max=100.0, default=(1.0, 1.0, 1.0)
    )
    bpy.types.Scene.voxtart_zoo_placement_mode = EnumProperty(
        name="Placement Mode",
        description="How a placement anchor is resolved",
        items=(
            ("FREE", "Free", "Place at the 3D cursor"),
            ("GRID", "Grid", "Snap the anchor to a uniform grid"),
            ("SURFACE", "Surface", "Raycast downward onto scene geometry"),
            ("VERTEX", "Vertex", "Snap to the nearest vertex of a selected mesh"),
            ("BRUSH", "Brush", "Scatter instances around the 3D cursor"),
            ("PATH", "Path", "Distribute instances along a curve"),
        ),
        default="FREE",
    )
    bpy.types.Scene.voxtart_zoo_grid_step = FloatProperty(
        name="Grid Step", min=0.001, max=1000.0, default=1.0
    )
    bpy.types.Scene.voxtart_zoo_surface_snap = BoolProperty(
        name="Surface Snap", description="Raycast placement anchors onto scene geometry", default=False
    )
    bpy.types.Scene.voxtart_zoo_surface_offset = FloatProperty(
        name="Surface Offset", min=-100.0, max=100.0, default=0.0
    )
    bpy.types.Scene.voxtart_zoo_align_to_surface = BoolProperty(
        name="Align to Surface", description="Align the asset's local Z axis to the hit normal", default=True
    )
    bpy.types.Scene.voxtart_zoo_brush_radius = FloatProperty(
        name="Brush Radius", min=0.01, max=1000.0, default=4.0
    )
    bpy.types.Scene.voxtart_zoo_brush_density = FloatProperty(
        name="Brush Density", min=0.01, max=100.0, default=1.0
    )
    bpy.types.Scene.voxtart_zoo_path_object = StringProperty(
        name="Path Object", description="Curve used by Path distribution", default=""
    )
    bpy.types.Scene.voxtart_zoo_path_spacing = FloatProperty(
        name="Path Spacing", min=0.01, max=1000.0, default=2.0
    )
    bpy.types.Scene.voxtart_zoo_path_closed = BoolProperty(
        name="Closed Path", default=False
    )
    bpy.types.Scene.voxtart_zoo_align_to_path = BoolProperty(
        name="Align to Path", description="Rotate each instance toward its local curve tangent", default=True
    )
    bpy.types.Scene.voxtart_zoo_random_seed = IntProperty(
        name="Random Seed", min=0, max=2147483647, default=0
    )
    bpy.types.Scene.voxtart_zoo_random_position = FloatVectorProperty(
        name="Random Position", size=3, min=0.0, max=1000.0, default=(0.0, 0.0, 0.0)
    )
    bpy.types.Scene.voxtart_zoo_random_rotation = FloatVectorProperty(
        name="Random Rotation", size=3, min=0.0, max=180.0, default=(0.0, 0.0, 0.0)
    )
    bpy.types.Scene.voxtart_zoo_random_scale = FloatVectorProperty(
        name="Random Scale", size=3, min=0.0, max=1.0, default=(0.0, 0.0, 0.0)
    )
    bpy.types.Scene.voxtart_zoo_collision_preset = EnumProperty(
        name="Collision Preset",
        description="Physics collision shape applied to newly placed assets",
        items=(
            ("NONE", "None", "Do not create a rigid-body collision"),
            ("BOX", "Passive Box", "Create a passive box collision"),
            ("CONVEX", "Passive Convex", "Create a passive convex-hull collision"),
            ("MESH", "Passive Mesh", "Create a passive mesh collision"),
        ),
        default="NONE",
    )
    bpy.types.Scene.voxtart_zoo_material_override = StringProperty(
        name="Material Override", description="Material assigned to newly placed assets", default=""
    )
    bpy.types.Scene.voxtart_zoo_instance_meshes = BoolProperty(
        name="Shared Mesh Instances",
        description="Reuse generated mesh data for brush and path instances",
        default=True,
    )
    bpy.types.Scene.voxtart_zoo_spacing = FloatProperty(
        name="Zoo Grid Spacing", min=0.1, max=100.0, default=4.0
    )
    bpy.types.Scene.voxtart_blob_name = StringProperty(name="Blob Name", default="Blob")
    bpy.types.Scene.voxtart_blob_author = StringProperty(name="Blob Author", default="")
    bpy.types.Scene.voxtart_blob_license = StringProperty(name="Blob License", default="")
    bpy.types.Scene.voxtart_blob_tags = StringProperty(name="Blob Tags", default="")
    bpy.types.Scene.voxtart_blob_source_url = StringProperty(name="Blob Source URL", default="")
    bpy.types.Scene.voxtart_blob_description = StringProperty(name="Blob Description", default="")
    bpy.types.Scene.voxtart_blob_joint_name = StringProperty(name="Joint Name", default="socket")
    bpy.types.Scene.voxtart_blob_joint_role = StringProperty(name="Joint Role", default="custom")
    bpy.types.Scene.voxtart_blob_joint_bone = StringProperty(name="Joint Bone Hint", default="")
    bpy.types.Scene.voxtart_blob_joint_position = IntVectorProperty(
        name="Joint Position", size=3, default=(0, 0, 0), min=-1024, max=1024
    )
    bpy.types.Scene.voxtart_blob_joint_normal = EnumProperty(
        name="Joint Normal", items=_NORMAL_ITEMS, default="POS_X"
    )
    bpy.types.Scene.voxtart_blob_snap_bone = StringProperty(name="Snap Bone", default="")
    bpy.types.Scene.voxtart_blob_snap_joint = IntProperty(name="Snap Joint", min=0, default=0)
    bpy.types.Scene.voxtart_blob_parent_to_bone = BoolProperty(name="Parent Blob to Bone", default=True)


def unregister() -> None:
    for name in (
        "voxtart_zoo_instance_meshes",
        "voxtart_zoo_material_override",
        "voxtart_zoo_collision_preset",
        "voxtart_zoo_random_scale",
        "voxtart_zoo_random_rotation",
        "voxtart_zoo_random_position",
        "voxtart_zoo_random_seed",
        "voxtart_zoo_align_to_path",
        "voxtart_zoo_path_closed",
        "voxtart_zoo_path_spacing",
        "voxtart_zoo_path_object",
        "voxtart_zoo_brush_density",
        "voxtart_zoo_brush_radius",
        "voxtart_zoo_align_to_surface",
        "voxtart_zoo_surface_offset",
        "voxtart_zoo_surface_snap",
        "voxtart_zoo_grid_step",
        "voxtart_zoo_placement_mode",
        "voxtart_zoo_spacing",
        "voxtart_zoo_placement_scale",
        "voxtart_zoo_placement_rotation",
        "voxtart_zoo_placement_location",
        "voxtart_blob_category",
        "voxtart_blob_gallery_favorites_only",
        "voxtart_blob_gallery_category",
        "voxtart_blob_snap_joint",
        "voxtart_blob_snap_bone",
        "voxtart_blob_joint_normal",
        "voxtart_blob_joint_position",
        "voxtart_blob_joint_bone",
        "voxtart_blob_joint_role",
        "voxtart_blob_joint_name",
        "voxtart_blob_description",
        "voxtart_blob_source_url",
        "voxtart_blob_tags",
        "voxtart_blob_license",
        "voxtart_blob_author",
        "voxtart_blob_name",
        "voxtart_blob_gallery_selected",
        "voxtart_blob_gallery_search",
        "voxtart_blob_gallery_path",
        "voxtart_blob_parent_to_bone",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    _release_previews()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
