# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compact Goxel-inspired tool, brush, and palette panels for Blender."""


import bpy

from . import palette_ui
from .operators import ASSET_PROPERTY, VOXELIZER_SOURCE_PROPERTY

def _has_asset(context: bpy.types.Context) -> bool:
    obj = getattr(context, "active_object", None)
    return obj is not None and ASSET_PROPERTY in obj


def _quick_edit(layout, action: str, *, icon: str, text: str = ""):
    button = layout.operator("voxtart.quick_edit_picked", text=text, icon=icon)
    button.action = action
    return button


class VOXTART_PT_goxel_tool_shelf(bpy.types.Panel):
    """Icon-first actions in Blender's left 3D View toolbar region."""

    bl_label = "Tools"
    bl_idname = "VOXTART_PT_goxel_tool_shelf"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 0

    @classmethod
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.operator("voxtart.voxel_editor", text="Open Voxel Editor", icon="CUBE")
        layout.separator()
        if not _has_asset(context):
            layout.label(text="Select a VOXTART asset for edit and pick tools.", icon="INFO")
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True)

        cube = grid.operator("voxtart.add_voxel_cube", text="", icon="CUBE")
        cube.size = scene.voxtart_cube_size
        cube.grid_size = scene.voxtart_grid_size
        voxelize = grid.operator("voxtart.voxelize_active", text="", icon="MOD_REMESH")
        voxelize.grid_size = scene.voxtart_grid_size
        grid.operator("voxtart.pick_voxel", text="", icon="PIVOT_CURSOR")
        _quick_edit(grid, "add", icon="ADD")
        _quick_edit(grid, "remove", icon="X")
        _quick_edit(grid, "paint", icon="CHECKMARK")
        grid.operator("voxtart.select_component", text="", icon="DUPLICATE")
        grid.operator("voxtart.focus_asset", text="", icon="VIEW_SELECTED")
        grid.operator("voxtart.import_voxel_format", text="", icon="IMPORT")
        grid.operator("voxtart.export_voxel_format", text="", icon="EXPORT")

        layout.separator()
        layout.label(text="VOXTART")


class VOXTART_PT_goxel_brush(bpy.types.Panel):
    """Compact right-sidebar controls corresponding to Goxel's Brush panel."""

    bl_label = "Brush"
    bl_idname = "VOXTART_PT_goxel_brush"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        active_asset = _has_asset(context)

        create = layout.box()
        create.label(text="Create", icon="CUBE")
        create.prop(scene, "voxtart_grid_size", text="Voxel Size")
        create.prop(scene, "voxtart_cube_size", text="Cube Size")
        create.prop(scene, "voxtart_voxelizer_mode", text="Voxelizer Mode")
        create.prop(scene, "voxtart_voxelizer_shell_thickness", text="Shell Thickness")
        row = create.row(align=True)
        cube = row.operator("voxtart.add_voxel_cube", text="New", icon="CUBE")
        cube.size = scene.voxtart_cube_size
        cube.grid_size = scene.voxtart_grid_size
        voxelize = row.operator("voxtart.voxelize_active", text="Voxelize", icon="MOD_REMESH")
        voxelize.grid_size = scene.voxtart_grid_size
        voxelize.mode = scene.voxtart_voxelizer_mode
        voxelize.shell_thickness = scene.voxtart_voxelizer_shell_thickness
        if active_asset and VOXELIZER_SOURCE_PROPERTY in context.active_object:
            create.operator("voxtart.refresh_voxelizer", text="Refresh Linked", icon="FILE_REFRESH")
        if not active_asset:
            layout.label(text="Select a VOXTART asset to edit.", icon="INFO")
            return

        brush = layout.box()
        brush.label(text="Brush", icon="PIVOT_CURSOR")
        brush.prop(scene, "voxtart_palette_color", text="Color")
        brush.prop(scene, "voxtart_edit_palette_index", text="Slot")
        brush.prop(scene, "voxtart_edit_cell", text="Cell")
        brush.prop(scene, "voxtart_edit_allow_protected", text="Protected")
        row = brush.row(align=True)
        row.operator("voxtart.pick_voxel", text="Pick", icon="PIVOT_CURSOR")
        _quick_edit(row, "add", icon="ADD", text="Place")
        _quick_edit(row, "remove", icon="X", text="Remove")
        _quick_edit(row, "paint", icon="CHECKMARK", text="Paint")

        shape = layout.box()
        shape.label(text="Shape", icon="CUBE")
        shape.prop(scene, "voxtart_edit_box_min", text="Minimum")
        shape.prop(scene, "voxtart_edit_box_max", text="Maximum")
        fill = shape.operator("voxtart.fill_box", text="Fill Box", icon="CUBE")
        fill.minimum = tuple(scene.voxtart_edit_box_min)
        fill.maximum = tuple(scene.voxtart_edit_box_max)
        fill.palette_index = scene.voxtart_edit_palette_index
        fill.allow_protected = scene.voxtart_edit_allow_protected
        shape.prop(scene, "voxtart_component_index", text="Component")
        shape.operator("voxtart.select_component", text="Select Component", icon="DUPLICATE")

        footer = layout.row(align=True)
        footer.operator("voxtart.focus_asset", text="Frame", icon="VIEW_SELECTED")
        footer.operator("voxtart.import_voxel_format", text="Import", icon="IMPORT")
        footer.operator("voxtart.export_voxel_format", text="Export", icon="EXPORT")


class VOXTART_PT_goxel_palette(bpy.types.Panel):
    """Compact swatch palette kept visible above the advanced palette library."""

    bl_label = "Palette"
    bl_idname = "VOXTART_PT_goxel_palette"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        colors = palette_ui._palette_for_display(scene)
        header = layout.row(align=True)
        header.prop(scene, "voxtart_palette_name", text="")
        header.label(text=f"{len(colors)}")
        header.prop(scene, "voxtart_palette_enforce", text="", icon="CHECKMARK")

        grid = layout.grid_flow(row_major=True, columns=4, even_columns=True, even_rows=True)
        for index, item in enumerate(scene.voxtart_palette_colors):
            slot = grid.row(align=True)
            slot.prop(item, "color", text="")
            select = slot.operator(
                "voxtart.palette_select",
                text="",
                icon="CHECKMARK" if index == int(scene.voxtart_palette_index) else "MATERIAL",
                depress=index == int(scene.voxtart_palette_index),
            )
            select.index = index

        footer = layout.row(align=True)
        footer.prop(scene, "voxtart_palette_edit_color", text="")
        add = footer.operator("voxtart.palette_add", text="", icon="ADD")
        add.color = tuple(scene.voxtart_palette_edit_color)
        footer.operator("voxtart.palette_import", text="", icon="FILE_FOLDER")


_CLASSES = (
    VOXTART_PT_goxel_tool_shelf,
    VOXTART_PT_goxel_brush,
    VOXTART_PT_goxel_palette,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "VOXTART_PT_goxel_brush",
    "VOXTART_PT_goxel_palette",
    "VOXTART_PT_goxel_tool_shelf",
    "register",
    "unregister",
]
