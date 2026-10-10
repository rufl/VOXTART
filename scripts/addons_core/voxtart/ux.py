# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Beginner-friendly VOXTART workflow controls and shortcut profile."""


import json
from dataclasses import dataclass
from typing import Any

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty

from .operators import ASSET_PROPERTY, VOXELIZER_SOURCE_PROPERTY


@dataclass(frozen=True, slots=True)
class ShortcutSpec:
    """One optional Object Mode shortcut that leaves Blender defaults intact."""

    operator: str
    key: str
    label: str
    description: str
    shift: bool = True
    ctrl: bool = False
    alt: bool = True
    properties: tuple[tuple[str, Any], ...] = ()

    @property
    def display(self) -> str:
        modifiers = []
        if self.ctrl:
            modifiers.append("Ctrl")
        if self.shift:
            modifiers.append("Shift")
        if self.alt:
            modifiers.append("Alt")
        key_label = {"LEFT_BRACKET": "[", "RIGHT_BRACKET": "]"}.get(self.key, self.key)
        modifiers.append(key_label)
        return "+".join(modifiers)


SHORTCUTS: tuple[ShortcutSpec, ...] = (
    ShortcutSpec(
        "voxtart.pick_voxel",
        "E",
        "Pick",
        "Pick the voxel under the mouse so the next action knows where to work.",
    ),
    ShortcutSpec(
        "voxtart.quick_edit_picked",
        "V",
        "Place",
        "Place one voxel beside the picked face.",
        properties=(("action", "add"),),
    ),
    ShortcutSpec(
        "voxtart.quick_edit_picked",
        "X",
        "Remove",
        "Remove the picked voxel.",
        properties=(("action", "remove"),),
    ),
    ShortcutSpec(
        "voxtart.quick_edit_picked",
        "P",
        "Paint",
        "Paint the picked voxel with the selected color slot.",
        properties=(("action", "paint"),),
    ),
    ShortcutSpec(
        "voxtart.quick_palette_step",
        "LEFT_BRACKET",
        "Previous color",
        "Choose the previous color slot.",
        properties=(("delta", -1),),
    ),
    ShortcutSpec(
        "voxtart.quick_palette_step",
        "RIGHT_BRACKET",
        "Next color",
        "Choose the next color slot.",
        properties=(("delta", 1),),
    ),
    ShortcutSpec(
        "voxtart.focus_asset",
        "F",
        "Focus asset",
        "Frame the active VOXTART asset in the 3D View.",
    ),
    ShortcutSpec(
        "voxtart.voxel_editor",
        "Q",
        "Voxel editor",
        "Open the MagicaVoxel-inspired interactive editor.",
    ),
    ShortcutSpec(
        "voxtart.show_shortcuts",
        "H",
        "Show shortcuts",
        "Open this friendly shortcut list.",
    ),
)

_SHORTCUT_OPERATOR_IDS = {spec.operator for spec in SHORTCUTS}
_KEYMAP_ITEMS: list[tuple[Any, Any]] = []


def _shortcut_enabled(context: bpy.types.Context) -> bool:
    return bool(getattr(context.scene, "voxtart_shortcuts_enabled", True))


def _is_asset(context: bpy.types.Context) -> bool:
    obj = getattr(context, "active_object", None)
    return obj is not None and ASSET_PROPERTY in obj


def _draw_shortcuts(menu, _context) -> None:
    layout = menu.layout
    layout.label(text="VOXTART shortcuts — Blender shortcuts stay active")
    layout.label(text="Use these in Object Mode with a VOXTART asset selected.")
    layout.separator()
    for shortcut in SHORTCUTS:
        row = layout.row(align=True)
        row.label(text=shortcut.display)
        row.label(text=shortcut.label)


class VOXTART_OT_quick_edit_picked(bpy.types.Operator):
    bl_idname = "voxtart.quick_edit_picked"
    bl_label = "Quick Edit Picked Voxel"
    bl_description = "Use the current color slot to place, remove, or paint the picked voxel"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        name="Action",
        items=(
            ("add", "Place", "Place one voxel beside the picked face"),
            ("remove", "Remove", "Remove the picked voxel"),
            ("paint", "Paint", "Paint the picked voxel"),
        ),
        default="add",
    )

    @classmethod
    def poll(cls, context):
        return _shortcut_enabled(context) and _is_asset(context)

    def execute(self, context):
        scene = context.scene
        return bpy.ops.voxtart.edit_picked_voxel(
            action=self.action,
            palette_index=int(scene.voxtart_edit_palette_index),
            allow_protected=bool(scene.voxtart_edit_allow_protected),
        )


class VOXTART_OT_quick_palette_step(bpy.types.Operator):
    bl_idname = "voxtart.quick_palette_step"
    bl_label = "Change VOXTART Color Slot"
    bl_description = "Choose the previous or next color slot on the active voxel asset"
    bl_options = {"REGISTER", "UNDO"}

    delta: IntProperty(name="Step", min=-1, max=1, default=1, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _shortcut_enabled(context) and _is_asset(context)

    def execute(self, context):
        from . import operators

        scene = context.scene
        try:
            asset = operators._load_asset(context.active_object)
            if not asset.palette:
                raise ValueError("the active asset has no color slots")
            current = int(scene.voxtart_edit_palette_index)
            scene.voxtart_edit_palette_index = (current + self.delta) % len(asset.palette)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"color slot {scene.voxtart_edit_palette_index}")
        return {"FINISHED"}


class VOXTART_OT_focus_asset(bpy.types.Operator):
    bl_idname = "voxtart.focus_asset"
    bl_label = "Focus VOXTART Asset"
    bl_description = "Frame the active VOXTART asset in the 3D View"

    @classmethod
    def poll(cls, context):
        return _shortcut_enabled(context) and _is_asset(context) and context.mode == "OBJECT"

    def execute(self, context):
        try:
            bpy.ops.view3d.view_selected(use_all_regions=True)
        except RuntimeError as error:
            self.report({"WARNING"}, str(error))
        return {"FINISHED"}


class VOXTART_OT_show_shortcuts(bpy.types.Operator):
    bl_idname = "voxtart.show_shortcuts"
    bl_label = "Show VOXTART Shortcuts"
    bl_description = "Show the beginner-friendly VOXTART shortcut list"

    def execute(self, context):
        context.window_manager.popup_menu(_draw_shortcuts, title="VOXTART Shortcuts", icon="INFO")
        return {"FINISHED"}


class VOXTART_PT_quickstart_panel(bpy.types.Panel):
    bl_label = "VOXTART Quick Start (Advanced)"
    bl_idname = "VOXTART_PT_quickstart_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 50
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        active_asset = _is_asset(context)

        intro = layout.box()
        intro.label(text="Make, pick, place, paint.")
        intro.label(text="Start with a cube, then build one voxel at a time.")

        layout.label(text="1. Start a model")
        row = layout.row(align=True)
        cube = row.operator("voxtart.add_voxel_cube", text="New Cube")
        cube.size = scene.voxtart_cube_size
        cube.grid_size = scene.voxtart_grid_size
        voxelize = row.operator("voxtart.voxelize_active", text="Voxelize")
        voxelize.grid_size = scene.voxtart_grid_size
        voxelize.mode = scene.voxtart_voxelizer_mode
        voxelize.shell_thickness = scene.voxtart_voxelizer_shell_thickness
        if active_asset and VOXELIZER_SOURCE_PROPERTY in context.active_object:
            row.operator("voxtart.refresh_voxelizer", text="Refresh")
        row = layout.row(align=True)
        row.operator("voxtart.import_asset", text="Import")
        if active_asset:
            row.operator("voxtart.export_asset", text="Export")

        layout.prop(scene, "voxtart_grid_size", text="Voxel size")
        layout.prop(scene, "voxtart_cube_size", text="Starter cube")
        layout.prop(scene, "voxtart_voxelizer_mode", text="Voxelizer mode")
        layout.prop(scene, "voxtart_voxelizer_shell_thickness", text="Shell thickness")

        if not active_asset:
            layout.label(text="Select a VOXTART asset to edit.", icon="INFO")
        else:
            layout.separator()
            layout.label(text="2. Edit the selected asset")
            layout.prop(scene, "voxtart_edit_palette_index", text="Color slot")
            layout.prop(scene, "voxtart_palette_color", text="New color")
            add_color = layout.operator("voxtart.add_palette_color", text="Add color")
            add_color.color = tuple(scene.voxtart_palette_color)
            row = layout.row(align=True)
            previous = row.operator("voxtart.quick_palette_step", text="Previous")
            previous.delta = -1
            following = row.operator("voxtart.quick_palette_step", text="Next")
            following.delta = 1
            layout.prop(scene, "voxtart_edit_cell", text="Cell")
            row = layout.row(align=True)
            row.operator("voxtart.pick_voxel", text="Pick")
            for action, label in (("add", "Place"), ("remove", "Remove"), ("paint", "Paint")):
                button = row.operator("voxtart.quick_edit_picked", text=label)
                button.action = action
            layout.label(text="Tip: Pick a face, then Place, Remove, or Paint.", icon="INFO")

        layout.separator()
        layout.label(text="3. Your VOXTART shortcuts")
        row = layout.row(align=True)
        row.prop(scene, "voxtart_shortcuts_enabled", text="Enabled")
        row.operator(VOXTART_OT_show_shortcuts.bl_idname, text="Show list")
        shortcut_box = layout.box()
        if scene.voxtart_shortcuts_enabled:
            for shortcut in SHORTCUTS:
                row = shortcut_box.row(align=True)
                row.label(text=shortcut.display)
                row.label(text=shortcut.label)
        else:
            shortcut_box.label(text="Blender shortcuts only. VOXTART keys are paused.")
        shortcut_box.label(text="VOXTART uses Shift+Alt so Blender shortcuts stay unchanged.")


_CLASSES = (
    VOXTART_OT_quick_edit_picked,
    VOXTART_OT_quick_palette_step,
    VOXTART_OT_focus_asset,
    VOXTART_OT_show_shortcuts,
    VOXTART_PT_quickstart_panel,
)


def _remove_keymaps() -> None:
    for keymap, keymap_item in _KEYMAP_ITEMS:
        try:
            keymap.keymap_items.remove(keymap_item)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    _KEYMAP_ITEMS.clear()

    window_manager = getattr(bpy.context, "window_manager", None)
    keyconfigs = getattr(window_manager, "keyconfigs", None)
    keyconfig = getattr(keyconfigs, "addon", None)
    if keyconfig is None:
        return
    for keymap in keyconfig.keymaps:
        for keymap_item in tuple(keymap.keymap_items):
            if keymap_item.idname in _SHORTCUT_OPERATOR_IDS:
                keymap.keymap_items.remove(keymap_item)


def _register_keymaps() -> None:
    _remove_keymaps()
    window_manager = getattr(bpy.context, "window_manager", None)
    keyconfigs = getattr(window_manager, "keyconfigs", None)
    keyconfig = getattr(keyconfigs, "addon", None)
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Object Mode", space_type="EMPTY", region_type="WINDOW")
    for shortcut in SHORTCUTS:
        keymap_item = keymap.keymap_items.new(
            shortcut.operator,
            shortcut.key,
            "PRESS",
            shift=shortcut.shift,
            ctrl=shortcut.ctrl,
            alt=shortcut.alt,
        )
        for property_name, value in shortcut.properties:
            setattr(keymap_item.properties, property_name, value)
        _KEYMAP_ITEMS.append((keymap, keymap_item))


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_shortcuts_enabled = BoolProperty(
        name="VOXTART Shortcuts",
        description="Enable beginner-friendly VOXTART shortcuts without replacing Blender defaults",
        default=True,
    )
    _register_keymaps()


def unregister() -> None:
    _remove_keymaps()
    if hasattr(bpy.types.Scene, "voxtart_shortcuts_enabled"):
        del bpy.types.Scene.voxtart_shortcuts_enabled
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
