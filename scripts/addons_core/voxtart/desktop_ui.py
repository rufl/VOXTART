# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""VOXTART startup workspace controls."""

import bpy
from . import operators
from .operators import ASSET_PROPERTY
from .voxel import VoxelAsset
from .voxel_editor import (
    EDITOR_GRID_PROPERTY,
    EDITOR_MINIMAL_PROPERTY,
    EDITOR_MODE_PROPERTY,
    EDITOR_SHAPE_PROPERTY,
    EDITOR_WORKSPACE_PROPERTY,
)


DESKTOP_MODE_PROPERTY = "voxtart_desktop_mode"
DESKTOP_VERSION_PROPERTY = "voxtart_desktop_version"
DESKTOP_FULLSCREEN_PROPERTY = "voxtart_desktop_fullscreen"
DESKTOP_NATIVE_CHROME_PROPERTY = "voxtart_native_chrome"
DESKTOP_WINDOW_FULLSCREEN_PROPERTY = "voxtart_window_fullscreen"
DEFAULT_PYRAMID_OBJECT_NAME = "VOXTART Starter Pyramid"
DEFAULT_PYRAMID_PROPERTY = "voxtart_default_pyramid"
DEFAULT_PYRAMID_LAYER_SIZES = (6, 4, 2)
DEFAULT_PYRAMID_COLOR = (1.0, 0.9, 0.45, 1.0)
DEFAULT_STARTER_PALETTE = (
    DEFAULT_PYRAMID_COLOR,
    (0.98, 0.42, 0.18, 1.0),
    (0.24, 0.58, 0.96, 1.0),
    (0.56, 0.34, 0.86, 1.0),
    (0.18, 0.72, 0.44, 1.0),
    (0.94, 0.24, 0.28, 1.0),
    (0.78, 0.48, 0.22, 1.0),
    (0.94, 0.94, 0.94, 1.0),
)

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

def _hide_scene_helpers() -> None:
    """Keep new block worlds free of unused cameras and lights."""

    if bpy.data.filepath:
        return
    for obj in tuple(bpy.data.objects):
        if obj.type not in {"CAMERA", "LIGHT"}:
            continue
        try:
            obj.hide_set(True)
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            obj.hide_viewport = True
        except (AttributeError, RuntimeError, TypeError):
            pass


def _default_pyramid_asset() -> VoxelAsset:
    asset = VoxelAsset(
        grid_size=1.0,
        palette=list(DEFAULT_STARTER_PALETTE),
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
        if EDITOR_MINIMAL_PROPERTY not in context.scene:
            context.scene[EDITOR_MINIMAL_PROPERTY] = True
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
    context.scene[EDITOR_MINIMAL_PROPERTY] = True
    return obj


def native_chrome_visible(scene=None) -> bool:
    """Return whether Blender-owned chrome is visible to the user."""

    return False

def is_native_ui_shortcut(event) -> bool:
    """Identify Blender shortcuts that could reopen hidden native surfaces."""

    if getattr(event, "value", None) != "PRESS":
        return False
    event_type = str(getattr(event, "type", ""))
    if event_type in {"F3", "F4", "F11"}:
        return True
    if event_type == "SPACE" and (
        bool(getattr(event, "shift", False))
        or bool(getattr(event, "ctrl", False))
    ):
        return True
    return bool(getattr(event, "shift", False)) and event_type.startswith("F")


def grid_visible(scene=None) -> bool:
    scene = scene or getattr(bpy.context, "scene", None)
    return bool(scene is not None and scene.get(EDITOR_GRID_PROPERTY, False))


def _set_grid_overlay_visibility(scene=None) -> None:
    scene = scene or getattr(bpy.context, "scene", None)
    show_native_grid = bool(native_chrome_visible(scene) and not grid_visible(scene))
    for window in tuple(bpy.context.window_manager.windows):
        screen = window.screen
        if screen is None:
            continue
        for area in tuple(screen.areas):
            if area.type != "VIEW_3D":
                continue
            try:
                overlay = area.spaces.active.overlay
                overlay.show_floor = show_native_grid
                overlay.show_axis_x = show_native_grid
                overlay.show_axis_y = show_native_grid
                area.tag_redraw()
            except (AttributeError, RuntimeError, TypeError):
                pass


def set_grid_visibility(visible: bool, scene=None) -> None:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is not None:
        scene[EDITOR_GRID_PROPERTY] = bool(visible)
    _set_grid_overlay_visibility(scene)


def set_native_chrome_visibility(_visible: bool = False) -> None:
    """Keep Blender's native chrome hidden behind the VOXTART surface."""

    scene = getattr(bpy.context, "scene", None)
    if scene is not None:
        scene[DESKTOP_NATIVE_CHROME_PROPERTY] = False
    for window in tuple(bpy.context.window_manager.windows):
        screen = window.screen
        if screen is None:
            continue
        try:
            screen.show_statusbar = False
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
                    setattr(space, property_name, False)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            for property_name, value in (
                ("show_region_hud", False),
                ("show_region_asset_shelf", False),
                ("show_gizmo", False),
            ):
                try:
                    setattr(space, property_name, value)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            try:
                area.tag_redraw()
            except (AttributeError, RuntimeError, TypeError):
                pass
    _set_grid_overlay_visibility(scene)


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
                    region_3d = getattr(area.spaces.active, "region_3d", None)
                    if region_3d is not None:
                        region_3d.view_distance = max(1.0, float(region_3d.view_distance) * 1.2)
            except (AttributeError, RuntimeError, TypeError):
                pass



def _open_editor_for_object(obj) -> bool:
    if bpy.app.background or obj is None or ASSET_PROPERTY not in obj:
        return False
    _activate_object(obj)
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
            running = "RUNNING_MODAL" in result
            if running:
                set_native_chrome_visibility(False)
                _frame_object_in_viewports(obj)
            return running
    return False


def _find_asset_object():
    active = getattr(bpy.context, "active_object", None)
    if active is not None and ASSET_PROPERTY in active:
        return active
    return next(
        (obj for obj in bpy.data.objects if ASSET_PROPERTY in obj),
        None,
    )


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
    return _open_editor_for_object(starter)


def open_asset_editor() -> bool:
    """Open the active or first loaded VOXTART asset in the voxel editor."""

    return _open_editor_for_object(_find_asset_object())



_CLASSES = ()


def configure_palette_color_management(scene) -> None:
    """Use the sRGB picker space for an unmodified palette viewport."""

    view_settings = getattr(scene, "view_settings", None)
    if view_settings is not None:
        for property_name, value in (
            ("view_transform", "Standard"),
            ("look", "None"),
            ("exposure", 0.0),
            ("gamma", 1.0),
        ):
            try:
                setattr(view_settings, property_name, value)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
    display_settings = getattr(scene, "display_settings", None)
    if display_settings is not None:
        try:
            display_settings.display_device = "sRGB"
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass


def configure_viewport_shading(space) -> None:
    """Show palette material colors without studio-light or cavity drift."""

    shading = getattr(space, "shading", None)
    if shading is None:
        return
    for property_name, value in (
        ("type", "SOLID"),
        ("color_type", "MATERIAL"),
        ("light", "FLAT"),
        ("show_shadows", False),
        ("show_cavity", False),
        ("show_specular_highlight", False),
    ):
        try:
            setattr(shading, property_name, value)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass


def configure_workspace() -> None:
    """Make the current Blender window present itself as the VOXTART editor."""

    version = ".".join(str(value) for value in bpy.app.version[:3])
    _clear_factory_scene()
    _hide_scene_helpers()
    scene = bpy.context.scene
    configure_palette_color_management(scene)
    # Keep the Windows title bar and its minimize, maximize, and close controls.
    scene[DESKTOP_WINDOW_FULLSCREEN_PROPERTY] = False
    if EDITOR_GRID_PROPERTY not in scene:
        scene[EDITOR_GRID_PROPERTY] = False
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
            configure_viewport_shading(space)

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
    "DEFAULT_STARTER_PALETTE",
    "DEFAULT_PYRAMID_LAYER_SIZES",
    "DEFAULT_PYRAMID_OBJECT_NAME",
    "DEFAULT_PYRAMID_PROPERTY",
    "DESKTOP_FULLSCREEN_PROPERTY",
    "DESKTOP_WINDOW_FULLSCREEN_PROPERTY",
    "DESKTOP_MODE_PROPERTY",
    "DESKTOP_NATIVE_CHROME_PROPERTY",
    "DESKTOP_VERSION_PROPERTY",
    "configure_palette_color_management",
    "configure_viewport_shading",
    "native_chrome_visible",
    "is_native_ui_shortcut",
    "grid_visible",
    "open_default_editor",
    "open_asset_editor",
    "register",
    "set_native_chrome_visibility",
    "set_grid_visibility",
    "unregister",
]
