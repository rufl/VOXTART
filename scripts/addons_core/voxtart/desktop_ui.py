# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""VOXTART-owned desktop landing panel and startup workspace controls."""

import bpy
from . import operators
from .operators import ASSET_PROPERTY
from .voxel import VoxelAsset
from .voxel_editor import (
    EDITOR_MODE_PROPERTY,
    EDITOR_SHAPE_PROPERTY,
    EDITOR_WORKSPACE_PROPERTY,
    FIRST_RUN_HINT_PROPERTY,
)


DESKTOP_MODE_PROPERTY = "voxtart_desktop_mode"
DESKTOP_VERSION_PROPERTY = "voxtart_desktop_version"
DESKTOP_FULLSCREEN_PROPERTY = "voxtart_desktop_fullscreen"
DESKTOP_NATIVE_CHROME_PROPERTY = "voxtart_native_chrome"
DEFAULT_PYRAMID_OBJECT_NAME = "VOXTART Starter Pyramid"
DEFAULT_PYRAMID_PROPERTY = "voxtart_default_pyramid"
DEFAULT_PYRAMID_LAYER_SIZES = (6, 4, 2)
DEFAULT_PYRAMID_COLOR = (1.0, 0.9, 0.45, 1.0)

def _clear_factory_scene() -> bool:
    """Remove Blender's starter cube, camera, and light for a clean editor."""

    if bpy.data.filepath:
        return False
    objects = tuple(bpy.data.objects)
    if not objects or not all(obj.name in {"Cube", "Camera", "Light"} for obj in objects):
        return False
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    return True


def _default_pyramid_asset() -> VoxelAsset:
    asset = VoxelAsset(
        grid_size=1.0,
        palette=[DEFAULT_PYRAMID_COLOR],
        provenance={
            "asset_type": "starter_pyramid",
            "base_size": "6",
            "layer_sizes": ",".join(str(size) for size in DEFAULT_PYRAMID_LAYER_SIZES),
            "editable": "true",
        },
    )
    for level, size in enumerate(DEFAULT_PYRAMID_LAYER_SIZES):
        offset = -(size // 2)
        for x in range(size):
            for y in range(size):
                asset.set_voxel((offset + x, offset + y, level))
    return asset


def _activate_object(obj) -> None:
    for selected in tuple(bpy.context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _ensure_default_pyramid(context):
    """Create the editable starter asset only for a new, unsaved project."""

    if bpy.data.filepath:
        return None
    existing = next(
        (obj for obj in bpy.data.objects if bool(obj.get(DEFAULT_PYRAMID_PROPERTY))),
        None,
    )
    if existing is not None:
        _activate_object(existing)
        if FIRST_RUN_HINT_PROPERTY not in context.scene:
            context.scene[FIRST_RUN_HINT_PROPERTY] = True
        return existing
    if bpy.data.objects:
        return None
    obj = operators._create_asset_object(
        context,
        DEFAULT_PYRAMID_OBJECT_NAME,
        _default_pyramid_asset(),
    )
    obj[DEFAULT_PYRAMID_PROPERTY] = True
    _activate_object(obj)
    for property_name, value in (
        (EDITOR_MODE_PROPERTY, "ADD"),
        (EDITOR_SHAPE_PROPERTY, "VOXEL"),
        (EDITOR_WORKSPACE_PROPERTY, "EDIT"),
    ):
        if hasattr(context.scene, property_name):
            setattr(context.scene, property_name, value)
    context.scene[FIRST_RUN_HINT_PROPERTY] = True
    return obj


def native_chrome_visible(scene=None) -> bool:
    scene = scene or getattr(bpy.context, "scene", None)
    return bool(scene is not None and scene.get(DESKTOP_NATIVE_CHROME_PROPERTY, False))


def set_native_chrome_visibility(visible: bool) -> None:
    """Show or hide Blender's native chrome around the VOXTART surface."""

    visible = bool(visible)
    scene = getattr(bpy.context, "scene", None)
    if scene is not None:
        scene[DESKTOP_NATIVE_CHROME_PROPERTY] = visible
    for window in tuple(bpy.context.window_manager.windows):
        screen = window.screen
        if screen is None:
            continue
        try:
            screen.show_statusbar = visible
        except (AttributeError, RuntimeError, TypeError):
            pass
        for area in tuple(screen.areas):
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            for property_name in (
                "show_region_toolbar",
                "show_region_ui",
                "show_region_header",
                "show_region_footer",
                "show_region_tool_header",
            ):
                try:
                    setattr(space, property_name, visible)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            try:
                space.show_region_hud = False
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                space.show_region_asset_shelf = False
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                space.show_gizmo = visible
                space.overlay.show_floor = visible
                space.overlay.show_axis_x = visible
                space.overlay.show_axis_y = visible
            except (AttributeError, RuntimeError, TypeError):
                pass


def _frame_object_in_viewports(obj) -> None:
    """Frame the starter asset in every available 3D viewport."""

    if bpy.app.background:
        return
    _activate_object(obj)
    for window in tuple(bpy.context.window_manager.windows):
        screen = window.screen
        if screen is None:
            continue
        for area in tuple(screen.areas):
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is None:
                continue
            try:
                with bpy.context.temp_override(
                    window=window,
                    screen=screen,
                    area=area,
                    region=region,
                ):
                    bpy.ops.view3d.view_selected(use_all_regions=False)
            except (AttributeError, RuntimeError, TypeError):
                pass


def open_default_editor() -> bool:
    """Open the starter pyramid directly in the voxel editor."""

    if bpy.app.background:
        return False
    starter = next(
        (
            obj
            for obj in bpy.data.objects
            if bool(obj.get(DEFAULT_PYRAMID_PROPERTY)) and ASSET_PROPERTY in obj
        ),
        None,
    )
    if starter is None:
        return False
    _activate_object(starter)
    set_native_chrome_visibility(False)
    for window in tuple(bpy.context.window_manager.windows):
        screen = window.screen
        if screen is None:
            continue
        for area in tuple(screen.areas):
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is None:
                continue
            try:
                with bpy.context.temp_override(
                    window=window,
                    screen=screen,
                    area=area,
                    region=region,
                ):
                    result = bpy.ops.voxtart.voxel_editor("INVOKE_DEFAULT")
            except (AttributeError, RuntimeError, TypeError):
                result = {"CANCELLED"}
            return "RUNNING_MODAL" in result
    return False


class VOXTART_PT_desktop_home(bpy.types.Panel):
    """The friendly first screen for the VOXTART editor."""

    bl_label = "Start Here"
    bl_idname = "VOXTART_PT_desktop_home"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tool"
    bl_order = -100

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        active = getattr(context, "active_object", None)
        has_mesh = active is not None and active.type == "MESH"
        has_asset = has_mesh and ASSET_PROPERTY in active
        layout.scale_y = 1.2

        intro = layout.box()
        intro.label(text="What do you want to make?", icon="INFO")
        intro.label(text=f"Active: {active.name}" if active else "Start with a new block toy or open a model.")

        make = layout.box()
        make.label(text="Make something", icon="CUBE")
        new_asset = make.operator("voxtart.add_voxel_cube", text="Make a block toy", icon="CUBE")
        new_asset.size = scene.voxtart_cube_size
        new_asset.grid_size = scene.voxtart_grid_size
        make.label(text="Start with a tiny block world.")

        open_box = layout.box()
        open_box.label(text="Open something", icon="FILE_FOLDER")
        open_box.operator("voxtart.import_asset", text="Open a VOXTART file", icon="IMPORT")
        open_box.operator("voxtart.import_voxel_format", text="Open a VOX or QB file", icon="FILE_3D")
        open_box.operator("voxtart.import_blockbench", text="Open a Blockbench model", icon="MESH_CUBE")

        character = layout.box()
        character.label(text="Character tools", icon="ARMATURE_DATA")
        character.label(
            text="Select a mesh to enable character actions." if not has_mesh else "Use the active mesh as your character base.",
            icon="INFO" if not has_mesh else "CHECKMARK",
        )
        character_controls = character.column()
        character_controls.enabled = has_mesh
        character_controls.operator("voxtart.prepare_character", text="Prepare a block character", icon="ARMATURE_DATA")
        character_controls.operator("voxtart.character_rebuild", text="Rebuild the character", icon="FILE_REFRESH")
        character_controls.operator("voxtart.export_game_rig", text="Save it for a game", icon="EXPORT")

        more = layout.box()
        more.label(text="More block tools", icon="MODIFIER")
        more.label(
            text="Voxelize the active mesh first." if not has_mesh else (
                "Select a VOXTART asset for asset-only tools." if not has_asset else "Active asset ready."
            ),
            icon="INFO" if not has_asset else "CHECKMARK",
        )
        voxelize_row = more.row()
        voxelize_row.enabled = has_mesh
        voxelize = voxelize_row.operator("voxtart.voxelize_active", text="Turn a 3D shape into blocks", icon="MOD_REMESH")
        voxelize.grid_size = scene.voxtart_grid_size
        voxelize.mode = scene.voxtart_voxelizer_mode
        voxelize.shell_thickness = scene.voxtart_voxelizer_shell_thickness
        asset_tools = more.column()
        asset_tools.enabled = has_asset
        asset_tools.operator("voxtart.optimize_mesh", text="Make it lighter", icon="MOD_DECIM")
        asset_tools.operator("voxtart.bloformer", text="Squish or stretch blocks", icon="MOD_DISPLACE")
        asset_tools.operator("voxtart.tiler_convert", text="Turn pixels into blocks", icon="TEXTURE")

        footer = layout.box()
        footer.label(text="Need more control?", icon="PREFERENCES")
        footer.label(text="Use More tools when you need Blender controls.")

_CLASSES = (VOXTART_PT_desktop_home,)


def configure_workspace() -> None:
    """Make the current Blender window present itself as the VOXTART editor."""

    version = ".".join(str(value) for value in bpy.app.version[:3])
    _clear_factory_scene()
    scene = bpy.context.scene
    starter = _ensure_default_pyramid(bpy.context)
    try:
        from .palette_ui import _ensure_palette

        _ensure_palette(scene)
    except (AttributeError, RuntimeError, TypeError):
        pass
    for window in tuple(bpy.context.window_manager.windows):
        workspace = window.workspace
        if workspace is not None:
            workspace.name = "VOXTART"
        screen = window.screen
        if screen is None:
            continue
        try:
            screen.show_statusbar = False
        except (AttributeError, RuntimeError, TypeError):
            pass
        view_areas = tuple(area for area in screen.areas if area.type == "VIEW_3D")
        for area in view_areas:
            space = area.spaces.active
            for property_name, value in (
                ("show_region_toolbar", False),
                ("show_region_header", False),
                ("show_region_footer", False),
                ("show_region_ui", False),
                ("show_region_hud", False),
                ("show_region_asset_shelf", False),
                ("show_region_tool_header", False),
            ):
                try:
                    setattr(space, property_name, value)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            try:
                space.show_gizmo = False
                space.overlay.show_floor = False
                space.overlay.show_axis_x = False
                space.overlay.show_axis_y = False
            except (AttributeError, RuntimeError, TypeError):
                pass
            space.shading.type = "MATERIAL"

        if len(screen.areas) <= 1 or not view_areas:
            scene[DESKTOP_FULLSCREEN_PROPERTY] = True
            continue
        main_area = max(view_areas, key=lambda item: item.width * item.height)
        region = next(
            (item for item in main_area.regions if item.type == "WINDOW"),
            None,
        )
        if region is None:
            continue
        try:
            with bpy.context.temp_override(
                window=window,
                screen=screen,
                area=main_area,
                region=region,
            ):
                result = bpy.ops.screen.screen_full_area(use_hide_panels=True)
        except (RuntimeError, TypeError):
            result = {"CANCELLED"}
        if "FINISHED" in result:
            scene[DESKTOP_FULLSCREEN_PROPERTY] = True

    set_native_chrome_visibility(False)
    if starter is not None:
        _frame_object_in_viewports(starter)
    scene[DESKTOP_MODE_PROPERTY] = True
    scene[DESKTOP_VERSION_PROPERTY] = version


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "DEFAULT_PYRAMID_COLOR",
    "DEFAULT_PYRAMID_LAYER_SIZES",
    "DEFAULT_PYRAMID_OBJECT_NAME",
    "DEFAULT_PYRAMID_PROPERTY",
    "DESKTOP_FULLSCREEN_PROPERTY",
    "DESKTOP_MODE_PROPERTY",
    "DESKTOP_NATIVE_CHROME_PROPERTY",
    "DESKTOP_VERSION_PROPERTY",
    "FIRST_RUN_HINT_PROPERTY",
    "VOXTART_PT_desktop_home",
    "configure_workspace",
    "native_chrome_visible",
    "open_default_editor",
    "register",
    "set_native_chrome_visibility",
    "unregister",
]
