# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender adapter for the deterministic VOXTART pixel tiler."""


import json
from pathlib import Path

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

from . import operators
from .palette import scene_palette
from .tiler import (
    MAX_PIXELS_PER_VOXEL,
    MAX_SEAMLESS_REPEAT,
    SEAMLESS_PATTERNS,
    TILER_AXES,
    TILER_MODES,
    TILER_ROTATIONS,
    TilerSettings,
    PixelTexture,
    apply_tile,
    extract_tile,
    generate_seamless_tile,
    tile_to_voxel_asset,
    tile_uvs,
)
from .voxel import VoxelAsset


_AXIS_ITEMS = tuple((axis, axis, f"Project the tile on the {axis} voxel plane") for axis in TILER_AXES)
_SOURCE_ITEMS = (
    ("IMAGE", "Image", "Use an external image or spritesheet"),
    ("GENERATED", "Generated", "Build a deterministic seamless tile internally"),
)
_PATTERN_ITEMS = tuple(
    (
        pattern,
        pattern.title(),
        f"Generate a seamless {pattern.lower()} tile",
    )
    for pattern in SEAMLESS_PATTERNS
)
_SIDE_ITEMS = (
    ("1", "Positive", "Project into the positive direction of the selected axis"),
    ("-1", "Negative", "Project into the negative direction of the selected axis"),
)
_ROTATION_ITEMS = tuple((str(angle), f"{angle}°", f"Rotate the tile {angle} degrees") for angle in TILER_ROTATIONS)
_MODE_ITEMS = (
    ("STAMP", "Stamp", "Leave transparent pixels and existing cells outside the tile untouched"),
    ("REPLACE", "Replace", "Clear the tile footprint before writing opaque pixels"),
)


def _scene_settings(scene: bpy.types.Scene) -> TilerSettings:
    return TilerSettings(
        pixel_scale=float(scene.voxtart_grid_size),
        axis=scene.voxtart_tiler_axis,
        side=int(scene.voxtart_tiler_side),
        depth=int(scene.voxtart_tiler_depth),
        rotation=int(scene.voxtart_tiler_rotation),
        flip_x=bool(scene.voxtart_tiler_flip_x),
        flip_y=bool(scene.voxtart_tiler_flip_y),
        mode=scene.voxtart_tiler_mode,
        alpha_threshold=float(scene.voxtart_tiler_alpha_threshold),
        pixels_per_voxel=int(scene.voxtart_tiler_pixels_per_voxel),
    )


def _read_image_texture(
    filepath: str,
    *,
    keep_image: bool = False,
) -> tuple[PixelTexture, bpy.types.Image | None]:
    path = str(filepath or "").strip()
    if not path:
        raise ValueError("choose a pixel texture or spritesheet")
    image = None
    success = False
    try:
        image = bpy.data.images.load(path, check_existing=False)
        width, height = image.size
        texture = PixelTexture.from_rgba(width, height, tuple(image.pixels))
        success = True
        return texture, image
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ValueError(f"could not load pixel texture: {error}") from error
    finally:
        if image is not None and (not keep_image or not success):
            try:
                bpy.data.images.remove(image)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                pass


def _generated_image(tile) -> bpy.types.Image:
    image_name = "VOXTART Generated Seamless Tile"
    image = bpy.data.images.get(image_name)
    if image is None:
        image = bpy.data.images.new(
            image_name,
            width=tile.width,
            height=tile.height,
            alpha=True,
        )
    elif tuple(image.size) != (tile.width, tile.height):
        image.scale(tile.width, tile.height)
    image.pixels.foreach_set(
        tuple(channel for pixel in tile.pixels.pixels for channel in pixel)
    )
    image.update()
    return image


def _scene_tile(
    scene: bpy.types.Scene,
    *,
    keep_image: bool = False,
):
    if scene.voxtart_tiler_source == "GENERATED":
        tile = generate_seamless_tile(
            int(scene.voxtart_tiler_generated_width),
            int(scene.voxtart_tiler_generated_height),
            pattern=scene.voxtart_tiler_generated_pattern,
            color_a=tuple(scene.voxtart_tiler_generated_color_a),
            color_b=tuple(scene.voxtart_tiler_generated_color_b),
            seed=int(scene.voxtart_tiler_generated_seed),
            repeat_x=int(scene.voxtart_tiler_generated_repeat_x),
            repeat_y=int(scene.voxtart_tiler_generated_repeat_y),
        )
        return tile, _generated_image(tile) if keep_image else None
    texture, image = _read_image_texture(
        scene.voxtart_tiler_texture_path,
        keep_image=keep_image,
    )
    tile = extract_tile(
        texture,
        int(scene.voxtart_tiler_tile_width),
        int(scene.voxtart_tiler_tile_height),
        int(scene.voxtart_tiler_tile_index),
    )
    return tile, image


def _scene_source_name(scene: bpy.types.Scene) -> str:
    if scene.voxtart_tiler_source == "GENERATED":
        return f"generated:{scene.voxtart_tiler_generated_pattern}"
    return Path(scene.voxtart_tiler_texture_path).name


def _report_dict(report) -> dict[str, object]:
    return {
        "tile_index": report.tile_index,
        "mode": report.mode,
        "added": report.added,
        "removed": report.removed,
        "painted": report.painted,
        "projected": report.projected,
        "footprint": report.footprint,
        "world_size": list(report.world_size),
    }


def _mesh_face_coordinates(obj, polygon, axis: str) -> tuple[tuple[float, float], ...]:
    coordinates = tuple(
        obj.matrix_world @ obj.data.vertices[index].co
        for index in polygon.vertices
    )
    if axis == "X":
        return tuple((vertex.y, vertex.z) for vertex in coordinates)
    if axis == "Y":
        return tuple((vertex.x, vertex.z) for vertex in coordinates)
    return tuple((vertex.x, vertex.y) for vertex in coordinates)


def _tiler_material(image: bpy.types.Image) -> bpy.types.Material:
    material_name = f"VOXTART Tiler: {image.name}"
    material = bpy.data.materials.get(material_name)
    if material is None:
        material = bpy.data.materials.new(material_name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    texture_node = nodes.get("VOXTART Tiler Texture")
    if texture_node is None:
        texture_node = nodes.new("ShaderNodeTexImage")
        texture_node.name = "VOXTART Tiler Texture"
        texture_node.label = "VOXTART Pixel Tiler"
    texture_node.image = image
    texture_node.interpolation = "Closest"
    texture_node.extension = "REPEAT"
    shader = nodes.get("Principled BSDF")
    output = nodes.get("Material Output")
    if shader is None or output is None:
        nodes.clear()
        texture_node = nodes.new("ShaderNodeTexImage")
        texture_node.name = "VOXTART Tiler Texture"
        texture_node.image = image
        texture_node.interpolation = "Closest"
        texture_node.extension = "REPEAT"
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        shader.name = "Principled BSDF"
        output = nodes.new("ShaderNodeOutputMaterial")
        output.name = "Material Output"
    if not any(
        link.from_node is texture_node and link.to_node is shader
        for link in links
    ):
        links.new(texture_node.outputs["Color"], shader.inputs["Base Color"])
    if not any(
        link.from_node is shader and link.to_node is output
        for link in links
    ):
        links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def _project_mesh_uv(context: bpy.types.Context) -> dict[str, object]:
    obj = getattr(context, "active_object", None)
    if obj is None or obj.type != "MESH":
        raise ValueError("activate a mesh or VOXTART voxel asset before using the Tiler")
    settings = _scene_settings(context.scene)
    tile, image = _scene_tile(context.scene, keep_image=True)
    if image is None:
        raise RuntimeError("tiler did not create a Blender image")
    uv_layer = obj.data.uv_layers.active or obj.data.uv_layers.new(name="VOXTART Tiler")
    polygons = [polygon for polygon in obj.data.polygons if polygon.select]
    if not polygons:
        polygons = list(obj.data.polygons)
    material = _tiler_material(image)
    material_index = next(
        (
            index
            for index, slot in enumerate(obj.data.materials)
            if slot == material
        ),
        None,
    )
    if material_index is None:
        obj.data.materials.append(material)
        material_index = len(obj.data.materials) - 1
    for polygon in polygons:
        coordinates = _mesh_face_coordinates(obj, polygon, settings.axis)
        mapped = tile_uvs(tile, coordinates, settings)
        for loop_index, uv in zip(polygon.loop_indices, mapped):
            uv_layer.data[loop_index].uv = uv
        polygon.material_index = material_index
    report = {
        "kind": "mesh_uv",
        "tile_index": tile.index,
        "faces": len(polygons),
        "pixel_scale": settings.pixel_scale,
        "world_size": [tile.width * settings.pixel_scale, tile.height * settings.pixel_scale],
    }
    obj["voxtart_tiler_report"] = json.dumps(report, sort_keys=True)
    return report


def _convert_tile_to_asset(context: bpy.types.Context):
    settings = _scene_settings(context.scene)
    tile, _image = _scene_tile(context.scene)
    asset, report = tile_to_voxel_asset(
        tile,
        settings,
        palette=scene_palette(context.scene),
        anchor=tuple(context.scene.voxtart_tiler_anchor),
    )
    name = f"VOXTART Tile {tile.index}"
    obj = operators._create_asset_object(context, name, asset)
    obj["voxtart_tiler_report"] = json.dumps(_report_dict(report), sort_keys=True)
    return obj, report






def _stamp_active(context: bpy.types.Context):
    obj = getattr(context, "active_object", None)
    if obj is None or operators.ASSET_PROPERTY not in obj:
        raise ValueError("activate a VOXTART voxel asset before using the Tiler")
    asset = operators._load_asset(obj)
    stack = operators._load_layer_stack(obj, asset)
    if stack.active.locked:
        raise ValueError(f"active layer is locked: {stack.active.name}")
    settings = _scene_settings(context.scene)
    tile, _image = _scene_tile(context.scene)
    editable = VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(asset.palette),
        voxels=dict(stack.active.voxels),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
        palette_materials=asset.get_palette_materials(),
    )
    report = apply_tile(
        editable,
        tile,
        tuple(context.scene.voxtart_tiler_anchor),
        settings,
    )
    stack.active.voxels = dict(editable.voxels)
    asset.palette = list(editable.palette)
    asset.palette_materials = editable.get_palette_materials()
    asset.provenance.update({
        "tiler_texture": _scene_source_name(context.scene),
        "tiler_pixel_scale": f"{settings.pixel_scale:g}",
        "tiler_tile_index": str(tile.index),
        "tiler_pixels_per_voxel": str(settings.pixels_per_voxel),
    })
    operators._replace_asset_mesh(obj, asset, layer_stack=stack)
    obj["voxtart_tiler_report"] = json.dumps(_report_dict(report), sort_keys=True)
    return report


class VOXTART_OT_tiler_stamp(bpy.types.Operator):
    bl_idname = "voxtart.tiler_stamp"
    bl_label = "Apply Pixel Tile"
    bl_description = "Apply a proportional spritesheet tile to mesh UVs or a VOXTART voxel layer"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        return obj is not None and (
            obj.type == "MESH" or operators.ASSET_PROPERTY in obj
        )

    def execute(self, context):
        try:
            obj = getattr(context, "active_object", None)
            if obj is not None and operators.ASSET_PROPERTY in obj:
                report = _stamp_active(context)
            else:
                report = _project_mesh_uv(context)
        except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        if isinstance(report, dict):
            self.report(
                {"INFO"},
                f"tile {report['tile_index']}: UV mapped {report['faces']} mesh faces",
            )
        else:
            self.report(
                {"INFO"},
                f"tile {report.tile_index}: +{report.added} -{report.removed} "
                f"painted {report.painted}",
            )
        return {"FINISHED"}


class VOXTART_OT_tiler_convert(bpy.types.Operator):
    bl_idname = "voxtart.tiler_convert"
    bl_label = "Convert Tile to Voxel Asset"
    bl_description = "Create a new VOXTART voxel asset from the selected spritesheet tile"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return getattr(context, "scene", None) is not None

    def execute(self, context):
        try:
            _obj, report = _convert_tile_to_asset(context)
        except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"tile {report.tile_index}: created {report.projected} voxel cells",
        )
        return {"FINISHED"}




class VOXTART_PT_tiler(bpy.types.Panel):
    bl_label = "Pixel Tiler"
    bl_idname = "VOXTART_PT_tiler"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 32

    @classmethod
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.label(text="Spritesheet tiles for mesh UVs or voxel layers", icon="TEXTURE")
        layout.prop(scene, "voxtart_tiler_source", text="Source")
        if scene.voxtart_tiler_source == "GENERATED":
            generated = layout.box()
            generated.label(text="Internal Seamless Generator", icon="SHADERFX")
            generated.prop(scene, "voxtart_tiler_generated_pattern", text="Pattern")
            row = generated.row(align=True)
            row.prop(scene, "voxtart_tiler_generated_width", text="Width")
            row.prop(scene, "voxtart_tiler_generated_height", text="Height")
            row = generated.row(align=True)
            row.prop(scene, "voxtart_tiler_generated_repeat_x", text="Repeat X")
            row.prop(scene, "voxtart_tiler_generated_repeat_y", text="Repeat Y")
            generated.prop(scene, "voxtart_tiler_generated_seed", text="Seed")
            generated.prop(scene, "voxtart_tiler_generated_color_a", text="Color A")
            generated.prop(scene, "voxtart_tiler_generated_color_b", text="Color B")
        else:
            layout.prop(scene, "voxtart_tiler_texture_path", text="Spritesheet")
            row = layout.row(align=True)
            row.prop(scene, "voxtart_tiler_tile_width", text="Tile W")
            row.prop(scene, "voxtart_tiler_tile_height", text="Tile H")
            row = layout.row(align=True)
            row.prop(scene, "voxtart_tiler_tile_index", text="Tile")
        layout.prop(scene, "voxtart_tiler_mode", text="Mode")
        layout.prop(scene, "voxtart_grid_size", text="Block Size")
        layout.prop(scene, "voxtart_tiler_pixels_per_voxel", text="Pixels / Block")
        layout.label(
            text=f"{scene.voxtart_tiler_pixels_per_voxel}×{scene.voxtart_tiler_pixels_per_voxel} source pixels → 1 block",
            icon="INFO",
        )
        row = layout.row(align=True)
        row.prop(scene, "voxtart_tiler_axis", text="Plane")
        row.prop(scene, "voxtart_tiler_side", text="Side")
        row = layout.row(align=True)
        row.prop(scene, "voxtart_tiler_depth", text="Depth")
        row.prop(scene, "voxtart_tiler_rotation", text="Rotate")
        row = layout.row(align=True)
        row.prop(scene, "voxtart_tiler_flip_x", text="Flip X")
        row.prop(scene, "voxtart_tiler_flip_y", text="Flip Y")
        layout.prop(scene, "voxtart_tiler_alpha_threshold", text="Alpha Threshold")
        layout.prop(scene, "voxtart_tiler_anchor", text="Anchor Cell")
        layout.operator(
            VOXTART_OT_tiler_convert.bl_idname,
            text="Convert Tile to New Voxel Asset",
            icon="CUBE",
        )
        if not VOXTART_OT_tiler_stamp.poll(context):
            layout.label(text="Select a mesh or VOXTART asset to apply a tile.", icon="INFO")
            return
        layout.operator(VOXTART_OT_tiler_stamp.bl_idname, text="Apply Pixel Tile", icon="TEXTURE")


_CLASSES = (
    VOXTART_OT_tiler_stamp,
    VOXTART_OT_tiler_convert,
    VOXTART_PT_tiler,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_tiler_texture_path = StringProperty(
        name="Tiler Spritesheet", subtype="FILE_PATH", default=""
    )
    bpy.types.Scene.voxtart_tiler_source = EnumProperty(
        name="Tiler Source", items=_SOURCE_ITEMS, default="IMAGE"
    )
    bpy.types.Scene.voxtart_tiler_generated_pattern = EnumProperty(
        name="Seamless Pattern", items=_PATTERN_ITEMS, default="CHECKER"
    )
    bpy.types.Scene.voxtart_tiler_generated_width = IntProperty(
        name="Generated Width", min=1, max=2048, default=16
    )
    bpy.types.Scene.voxtart_tiler_generated_height = IntProperty(
        name="Generated Height", min=1, max=2048, default=16
    )
    bpy.types.Scene.voxtart_tiler_generated_repeat_x = IntProperty(
        name="Generated Repeat X", min=1, max=MAX_SEAMLESS_REPEAT, default=2
    )
    bpy.types.Scene.voxtart_tiler_generated_repeat_y = IntProperty(
        name="Generated Repeat Y", min=1, max=MAX_SEAMLESS_REPEAT, default=2
    )
    bpy.types.Scene.voxtart_tiler_generated_seed = IntProperty(
        name="Generated Seed", default=0
    )
    bpy.types.Scene.voxtart_tiler_generated_color_a = FloatVectorProperty(
        name="Generated Color A",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.15, 0.15, 0.15, 1.0),
    )
    bpy.types.Scene.voxtart_tiler_generated_color_b = FloatVectorProperty(
        name="Generated Color B",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.75, 0.75, 0.75, 1.0),
    )
    bpy.types.Scene.voxtart_tiler_tile_width = IntProperty(
        name="Tile Width", min=1, max=8192, default=16
    )
    bpy.types.Scene.voxtart_tiler_tile_height = IntProperty(
        name="Tile Height", min=1, max=8192, default=16
    )
    bpy.types.Scene.voxtart_tiler_tile_index = IntProperty(
        name="Tile Index", min=0, default=0
    )
    bpy.types.Scene.voxtart_tiler_axis = EnumProperty(
        name="Tiler Plane", items=_AXIS_ITEMS, default="Z"
    )
    bpy.types.Scene.voxtart_tiler_side = EnumProperty(
        name="Tiler Side", items=_SIDE_ITEMS, default="1"
    )
    bpy.types.Scene.voxtart_tiler_depth = IntProperty(
        name="Tile Depth", min=1, max=256, default=1
    )
    bpy.types.Scene.voxtart_tiler_rotation = EnumProperty(
        name="Tile Rotation", items=_ROTATION_ITEMS, default="0"
    )
    bpy.types.Scene.voxtart_tiler_flip_x = BoolProperty(
        name="Flip Tile X", default=False
    )
    bpy.types.Scene.voxtart_tiler_flip_y = BoolProperty(
        name="Flip Tile Y", default=False
    )
    bpy.types.Scene.voxtart_tiler_mode = EnumProperty(
        name="Tiler Mode", items=_MODE_ITEMS, default="STAMP"
    )
    bpy.types.Scene.voxtart_tiler_alpha_threshold = FloatProperty(
        name="Tile Alpha Threshold", min=0.0, max=1.0, default=0.05
    )
    bpy.types.Scene.voxtart_tiler_pixels_per_voxel = IntProperty(
        name="Pixels per Block",
        description="Group this many source pixels into one editable voxel",
        min=1,
        max=MAX_PIXELS_PER_VOXEL,
        default=2,
    )
    bpy.types.Scene.voxtart_tiler_anchor = IntVectorProperty(
        name="Tile Anchor", size=3, default=(0, 0, 0), min=-4096, max=4096
    )


def unregister():
    for property_name in (
        "voxtart_tiler_generated_color_b",
        "voxtart_tiler_generated_color_a",
        "voxtart_tiler_generated_seed",
        "voxtart_tiler_generated_repeat_y",
        "voxtart_tiler_generated_repeat_x",
        "voxtart_tiler_generated_height",
        "voxtart_tiler_generated_width",
        "voxtart_tiler_generated_pattern",
        "voxtart_tiler_source",
        "voxtart_tiler_anchor",
        "voxtart_tiler_alpha_threshold",
        "voxtart_tiler_pixels_per_voxel",
        "voxtart_tiler_mode",
        "voxtart_tiler_flip_y",
        "voxtart_tiler_flip_x",
        "voxtart_tiler_rotation",
        "voxtart_tiler_depth",
        "voxtart_tiler_side",
        "voxtart_tiler_axis",
        "voxtart_tiler_tile_index",
        "voxtart_tiler_tile_height",
        "voxtart_tiler_tile_width",
        "voxtart_tiler_texture_path",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "VOXTART_OT_tiler_stamp",
    "VOXTART_OT_tiler_convert",
    "VOXTART_PT_tiler",
    "register",
    "unregister",
]
