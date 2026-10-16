# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender operators for portable voxel format import and export."""


from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector

from . import operators
from .voxel import VoxelAsset, remap_palette_materials
from .voxel_formats import (
    VoxelFormatModel,
    decode_magica_vox_models,
    decode_qb_models,
    encode_magica_vox_models,
    encode_qb_models,
)


_FORMAT_ITEMS = (
    ("AUTO", "Automatic", "Infer the voxel format from the filename extension"),
    ("VOX", "MagicaVoxel VOX", "MagicaVoxel's .vox format"),
    ("QB", "Qubicle QB", "Qubicle Binary's .qb format"),
)
_QB_COLOR_ITEMS = (
    ("0", "RGBA", "Store Qubicle voxel colors as RGBA"),
    ("1", "BGRA", "Store Qubicle voxel colors as BGRA"),
)
_MENU_TARGETS: list[object] = []


def _format_for_path(filepath: str, requested: str) -> str:
    requested = str(requested or "AUTO").upper()
    if requested != "AUTO":
        if requested not in {"VOX", "QB"}:
            raise ValueError(f"unsupported voxel format: {requested}")
        return requested
    extension = Path(filepath).suffix.lower()
    if extension == ".vox":
        return "VOX"
    if extension == ".qb":
        return "QB"
    raise ValueError("choose a .vox or .qb filename")


def _asset_objects(context: bpy.types.Context, export_selected: bool) -> tuple[bpy.types.Object, ...]:
    active = context.active_object
    if export_selected:
        objects = tuple(obj for obj in context.selected_objects if operators.ASSET_PROPERTY in obj)
        if not objects and active is not None and operators.ASSET_PROPERTY in active:
            objects = (active,)
    else:
        objects = (active,) if active is not None and operators.ASSET_PROPERTY in active else ()
    return tuple(sorted(objects, key=lambda obj: obj.name.casefold()))


def _compact_asset(asset: VoxelAsset) -> VoxelAsset:
    """Avoid creating unused palette slots for large indexed VOX palettes."""

    used = sorted(set(asset.voxels.values()))
    if not used:
        return VoxelAsset(
            grid_size=asset.grid_size,
            palette=[asset.palette[0]],
            provenance=dict(asset.provenance),
            palette_materials=remap_palette_materials(
                asset.get_palette_materials(),
                1,
                index_map={0: 0},
            ),
        )
    remap = {old: new for new, old in enumerate(used)}
    return VoxelAsset(
        grid_size=asset.grid_size,
        palette=[asset.palette[index] for index in used],
        voxels={cell: remap[index] for cell, index in asset.voxels.items()},
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
        palette_materials=remap_palette_materials(
            asset.get_palette_materials(),
            len(used),
            index_map=remap,
        ),
    )


def _world_voxel_position(obj: bpy.types.Object, asset: VoxelAsset) -> tuple[int, int, int]:
    translation = obj.matrix_world.to_translation()
    return tuple(round(float(translation[index]) / asset.grid_size) for index in range(3))


class VOXTART_OT_import_voxel_format(bpy.types.Operator, ImportHelper):
    bl_idname = "voxtart.import_voxel_format"
    bl_label = "Import VOX/QB Voxel File"
    bl_description = "Import MagicaVoxel VOX or Qubicle QB models as VOXTART assets"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".vox"
    filter_glob: StringProperty(default="*.vox;*.qb", options={'HIDDEN'})

    def execute(self, context):
        try:
            format_name = _format_for_path(self.filepath, "AUTO")
            data = Path(self.filepath).read_bytes()
            grid_size = float(context.scene.voxtart_grid_size)
            if format_name == "VOX":
                models = decode_magica_vox_models(data, grid_size=grid_size)
            else:
                models = decode_qb_models(data, grid_size=grid_size)
            if not models:
                raise ValueError("voxel file contains no models")
            source_stem = Path(self.filepath).stem
            for index, model in enumerate(models, start=1):
                asset = _compact_asset(model.asset)
                if len(models) == 1:
                    name = source_stem or model.name
                else:
                    name = f"{source_stem or 'Voxel'} {model.name or index}"
                offset = Vector(model.position) * asset.grid_size
                obj = operators._create_asset_object(
                    context,
                    name,
                    asset,
                    matrix_world=Matrix.Translation(offset),
                )
                obj["voxtart_import_format"] = format_name
            self.report({'INFO'}, f"imported {len(models)} {format_name} model(s)")
        except (OSError, TypeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_OT_export_voxel_format(bpy.types.Operator, ExportHelper):
    bl_idname = "voxtart.export_voxel_format"
    bl_label = "Export VOX/QB Voxel File"
    bl_description = "Export VOXTART assets as MagicaVoxel VOX or Qubicle QB models"
    filename_ext = ".vox"
    check_extension = False
    filter_glob: StringProperty(default="*.vox;*.qb", options={'HIDDEN'})

    format: EnumProperty(name="Format", items=_FORMAT_ITEMS, default="AUTO")
    export_selected: BoolProperty(
        name="Export Selected Assets",
        description="Export all selected VOXTART assets instead of only the active asset",
        default=True,
    )
    qb_compressed: BoolProperty(
        name="Qubicle RLE Compression",
        description="Use Qubicle's run-length encoding for QB voxel data",
        default=True,
    )
    qb_color_format: EnumProperty(
        name="Qubicle Color Format",
        items=_QB_COLOR_ITEMS,
        default="0",
    )

    @classmethod
    def poll(cls, context):
        return bool(_asset_objects(context, True))

    def execute(self, context):
        try:
            format_name = _format_for_path(self.filepath, self.format)
            objects = _asset_objects(context, self.export_selected)
            if not objects:
                raise ValueError("select or activate at least one VOXTART voxel asset")
            models = []
            for obj in objects:
                asset = operators._load_asset(obj)
                position = _world_voxel_position(obj, asset)
                models.append(VoxelFormatModel(obj.name, asset, position))
            if format_name == "VOX":
                data = encode_magica_vox_models(models)
            else:
                data = encode_qb_models(
                    models,
                    compressed=self.qb_compressed,
                    color_format=int(self.qb_color_format),
                )
            Path(self.filepath).write_bytes(data)
            self.report({'INFO'}, f"exported {len(models)} {format_name} model(s)")
        except (OSError, TypeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_PT_voxel_formats(bpy.types.Panel):
    bl_label = "Portable Voxel Formats"
    bl_idname = "VOXTART_PT_voxel_formats"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VOXTART"
    bl_order = 35

    @classmethod
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.operator(VOXTART_OT_import_voxel_format.bl_idname, text="Import .VOX / .QB")
        layout.separator()
        layout.prop(scene, "voxtart_format_export_selected")
        layout.prop(scene, "voxtart_qb_compressed")
        layout.prop(scene, "voxtart_qb_color_format")
        has_assets = bool(_asset_objects(context, True))
        if not has_assets:
            layout.label(text="Select a VOXTART asset to enable export.", icon="INFO")
        export_row = layout.row()
        export_row.enabled = has_assets
        export = export_row.operator(VOXTART_OT_export_voxel_format.bl_idname, text="Export .VOX / .QB")
        export.format = 'AUTO'
        export.export_selected = scene.voxtart_format_export_selected
        export.qb_compressed = scene.voxtart_qb_compressed
        export.qb_color_format = scene.voxtart_qb_color_format


def _menu_import(self, _context):
    self.layout.operator(VOXTART_OT_import_voxel_format.bl_idname, text="VOXTART VOX/QB")


def _menu_export(self, _context):
    self.layout.operator(VOXTART_OT_export_voxel_format.bl_idname, text="VOXTART VOX/QB")


_CLASSES = (
    VOXTART_OT_import_voxel_format,
    VOXTART_OT_export_voxel_format,
    VOXTART_PT_voxel_formats,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_format_export_selected = BoolProperty(
        name="Export Selected Assets", default=True
    )
    bpy.types.Scene.voxtart_qb_compressed = BoolProperty(
        name="Qubicle RLE Compression", default=True
    )
    bpy.types.Scene.voxtart_qb_color_format = EnumProperty(
        name="Qubicle Color Format", items=_QB_COLOR_ITEMS, default="0"
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
        "voxtart_qb_color_format",
        "voxtart_qb_compressed",
        "voxtart_format_export_selected",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "VOXTART_OT_export_voxel_format",
    "VOXTART_OT_import_voxel_format",
    "VOXTART_PT_voxel_formats",
    "register",
    "unregister",
]
