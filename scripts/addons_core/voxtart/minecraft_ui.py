# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Blender operators and panels for Blockbench and Minecraft interchange."""


from pathlib import Path

import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import operators
from .blockbench_formats import decode_blockbench, encode_blockbench
from .minecraft_formats import (
    MINECRAFT_DEFAULT_BLOCK,
    MinecraftVolume,
    decode_minecraft_file,
    encode_minecraft_file,
)


_BLOCKBENCH_FORMAT = "BLOCKBENCH"
_BLOCKBENCH_FILTER = "*.bbmodel;*.json"
_MINECRAFT_FORMAT_ITEMS = (
    ("AUTO", "Automatic", "Infer the Minecraft format from the filename extension"),
    ("SCHEM", "Sponge Schematic (.schem)", "Compressed Sponge schematic v2"),
    ("SCHEMATIC", "Legacy Schematic (.schematic)", "Legacy MCEdit schematic"),
    ("STRUCTURE", "Structure (.nbt)", "Minecraft structure NBT"),
    ("MCA", "Anvil Region / World (.mca)", "Minecraft Anvil region or world directory"),
)
_MINECRAFT_FILTER = "*.schem;*.schematic;*.nbt;*.mca"
_MENU_TARGETS: list[tuple[object, object]] = []


def _active_asset(context: bpy.types.Context):
    obj = context.active_object
    if obj is None or operators.ASSET_PROPERTY not in obj:
        raise ValueError("activate a VOXTART voxel asset")
    return obj, operators._load_asset(obj)


def _minecraft_format(filepath: str, requested: str) -> str:
    requested = str(requested or "AUTO").upper()
    if requested != "AUTO":
        if requested not in {item[0] for item in _MINECRAFT_FORMAT_ITEMS}:
            raise ValueError(f"unsupported Minecraft format: {requested}")
        return requested
    return {
        ".schem": "SCHEM",
        ".schematic": "SCHEMATIC",
        ".nbt": "STRUCTURE",
        ".mca": "MCA",
    }.get(Path(filepath).suffix.lower(), "AUTO")


class VOXTART_OT_import_blockbench(bpy.types.Operator, ImportHelper):
    bl_idname = "voxtart.import_blockbench"
    bl_label = "Import Blockbench Model"
    bl_description = "Import Blockbench cube and Bedrock geometry models as VOXTART assets"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".bbmodel"
    filter_glob: StringProperty(default=_BLOCKBENCH_FILTER, options={'HIDDEN'})
    units_per_voxel: FloatProperty(
        name="Blockbench Units per Voxel",
        description="How many Blockbench model units make one VOXTART voxel",
        default=0.0,
        min=0.0,
    )

    def execute(self, context):
        try:
            units_per_voxel = float(self.units_per_voxel or context.scene.voxtart_blockbench_units_per_voxel)
            if units_per_voxel <= 0.0:
                raise ValueError("Blockbench units per voxel must be positive")
            asset = decode_blockbench(
                Path(self.filepath).read_bytes(),
                grid_size=float(context.scene.voxtart_grid_size),
                units_per_voxel=units_per_voxel,
            )
            name = Path(self.filepath).stem or "Blockbench"
            obj = operators._create_asset_object(context, name, asset)
            obj["voxtart_import_format"] = _BLOCKBENCH_FORMAT
            obj["voxtart_blockbench_units_per_voxel"] = units_per_voxel
            self.report({'INFO'}, f"imported Blockbench model with {len(asset.voxels)} voxel(s)")
        except (OSError, TypeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_OT_export_blockbench(bpy.types.Operator, ExportHelper):
    bl_idname = "voxtart.export_blockbench"
    bl_label = "Export Blockbench Model"
    bl_description = "Export the active VOXTART asset as a cube-based Blockbench model"
    bl_options = {'REGISTER'}
    filename_ext = ".bbmodel"
    filter_glob: StringProperty(default="*.bbmodel", options={'HIDDEN'})
    units_per_voxel: FloatProperty(
        name="Blockbench Units per Voxel",
        description="How many Blockbench model units make one VOXTART voxel",
        default=0.0,
        min=0.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and operators.ASSET_PROPERTY in obj

    def execute(self, context):
        try:
            obj, asset = _active_asset(context)
            units_per_voxel = float(self.units_per_voxel or context.scene.voxtart_blockbench_units_per_voxel)
            data = encode_blockbench(asset, units_per_voxel=units_per_voxel)
            Path(self.filepath).write_bytes(data)
            self.report({'INFO'}, f"exported {obj.name} as Blockbench model")
        except (OSError, TypeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_OT_import_minecraft(bpy.types.Operator, ImportHelper):
    bl_idname = "voxtart.import_minecraft"
    bl_label = "Import Minecraft Map or Schematic"
    bl_description = "Import Sponge, legacy, structure, or Anvil Minecraft data as a VOXTART asset"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".schem"
    filter_glob: StringProperty(default=_MINECRAFT_FILTER, options={'HIDDEN'})
    format: EnumProperty(name="Format", items=_MINECRAFT_FORMAT_ITEMS, default="AUTO")
    grid_size: FloatProperty(
        name="Voxel Grid Size",
        description="Blender units occupied by one Minecraft block",
        default=0.0,
        min=0.0,
    )

    def execute(self, context):
        try:
            format_name = _minecraft_format(self.filepath, self.format)
            volume = decode_minecraft_file(self.filepath, format_hint=format_name)
            grid_size = float(self.grid_size or context.scene.voxtart_minecraft_grid_size)
            asset = volume.to_voxel_asset(grid_size=grid_size)
            name = Path(self.filepath).stem or "Minecraft"
            obj = operators._create_asset_object(context, name, asset)
            obj["voxtart_import_format"] = format_name
            obj["voxtart_minecraft_origin"] = ",".join(str(axis) for axis in volume.origin)
            obj["voxtart_minecraft_block_count"] = len(volume.blocks)
            self.report({'INFO'}, f"imported Minecraft volume with {len(volume.blocks)} block(s)")
        except (OSError, TypeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_OT_export_minecraft(bpy.types.Operator, ExportHelper):
    bl_idname = "voxtart.export_minecraft"
    bl_label = "Export Minecraft Map or Schematic"
    bl_description = "Export the active VOXTART asset as a Minecraft schematic, structure, or Anvil region"
    bl_options = {'REGISTER'}
    filename_ext = ".schem"
    check_extension = False
    filter_glob: StringProperty(default=_MINECRAFT_FILTER, options={'HIDDEN'})
    format: EnumProperty(name="Format", items=_MINECRAFT_FORMAT_ITEMS, default="SCHEM")
    default_block: StringProperty(
        name="Default Block",
        description="Minecraft block used for fully transparent voxel palette entries",
        default=MINECRAFT_DEFAULT_BLOCK,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and operators.ASSET_PROPERTY in obj

    def execute(self, context):
        try:
            obj, asset = _active_asset(context)
            format_name = _minecraft_format(self.filepath, self.format)
            volume = MinecraftVolume.from_voxel_asset(asset, default_block=self.default_block)
            written = encode_minecraft_file(volume, self.filepath, format_name)
            self.report({'INFO'}, f"exported {obj.name} to {written[0].name}")
        except (OSError, TypeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_PT_blockbench_minecraft(bpy.types.Panel):
    bl_label = "Blockbench / Minecraft"
    bl_idname = "VOXTART_PT_blockbench_minecraft"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VOXTART"
    bl_order = 38

    @classmethod
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        blockbench = layout.box()
        blockbench.label(text="Blockbench")
        blockbench.prop(scene, "voxtart_blockbench_units_per_voxel")
        blockbench.operator(VOXTART_OT_import_blockbench.bl_idname, text="Import .BBMODEL")
        export = blockbench.operator(VOXTART_OT_export_blockbench.bl_idname, text="Export .BBMODEL")
        export.units_per_voxel = scene.voxtart_blockbench_units_per_voxel
        minecraft = layout.box()
        minecraft.label(text="Minecraft")
        minecraft.prop(scene, "voxtart_minecraft_grid_size")
        minecraft.prop(scene, "voxtart_minecraft_format")
        minecraft.prop(scene, "voxtart_minecraft_default_block")
        minecraft.operator(VOXTART_OT_import_minecraft.bl_idname, text="Import Map / Schematic")
        export = minecraft.operator(VOXTART_OT_export_minecraft.bl_idname, text="Export Map / Schematic")
        export.format = scene.voxtart_minecraft_format
        export.default_block = scene.voxtart_minecraft_default_block


def _menu_import(self, _context):
    self.layout.operator(VOXTART_OT_import_blockbench.bl_idname, text="VOXTART Blockbench")
    self.layout.operator(VOXTART_OT_import_minecraft.bl_idname, text="VOXTART Minecraft")


def _menu_export(self, _context):
    self.layout.operator(VOXTART_OT_export_blockbench.bl_idname, text="VOXTART Blockbench")
    self.layout.operator(VOXTART_OT_export_minecraft.bl_idname, text="VOXTART Minecraft")


_CLASSES = (
    VOXTART_OT_import_blockbench,
    VOXTART_OT_export_blockbench,
    VOXTART_OT_import_minecraft,
    VOXTART_OT_export_minecraft,
    VOXTART_PT_blockbench_minecraft,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_blockbench_units_per_voxel = FloatProperty(
        name="Blockbench Units per Voxel", default=1.0, min=0.0001
    )
    bpy.types.Scene.voxtart_minecraft_grid_size = FloatProperty(
        name="Minecraft Grid Size", default=1.0, min=0.0001
    )
    bpy.types.Scene.voxtart_minecraft_format = EnumProperty(
        name="Minecraft Format", items=_MINECRAFT_FORMAT_ITEMS, default="SCHEM"
    )
    bpy.types.Scene.voxtart_minecraft_default_block = StringProperty(
        name="Minecraft Default Block", default=MINECRAFT_DEFAULT_BLOCK
    )
    for menu_name, callback in (
        ("TOPBAR_MT_file_import", _menu_import),
        ("TOPBAR_MT_file_export", _menu_export),
    ):
        menu = getattr(bpy.types, menu_name, None)
        if menu is not None:
            menu.append(callback)
            _MENU_TARGETS.append((menu, callback))


def unregister():
    for menu, callback in reversed(_MENU_TARGETS):
        menu.remove(callback)
    _MENU_TARGETS.clear()
    for property_name in (
        "voxtart_minecraft_default_block",
        "voxtart_minecraft_format",
        "voxtart_minecraft_grid_size",
        "voxtart_blockbench_units_per_voxel",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "VOXTART_OT_export_blockbench",
    "VOXTART_OT_export_minecraft",
    "VOXTART_OT_import_blockbench",
    "VOXTART_OT_import_minecraft",
    "VOXTART_PT_blockbench_minecraft",
    "register",
    "unregister",
]
