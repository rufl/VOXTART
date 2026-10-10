# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""VOXTART-owned desktop landing panel and startup workspace controls."""

import bpy
from .operators import ASSET_PROPERTY


DESKTOP_MODE_PROPERTY = "voxtart_desktop_mode"
DESKTOP_VERSION_PROPERTY = "voxtart_desktop_version"
DESKTOP_FULLSCREEN_PROPERTY = "voxtart_desktop_fullscreen"


def _clear_factory_scene() -> None:
    """Remove Blender's starter cube, camera, and light for a clean editor."""

    if bpy.data.filepath:
        return
    objects = tuple(bpy.data.objects)
    if not objects or not all(obj.name in {"Cube", "Camera", "Light"} for obj in objects):
        return
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)


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
        footer.label(text="Press Esc to open the full toolbox.")

_CLASSES = (VOXTART_PT_desktop_home,)


def configure_workspace() -> None:
    """Make the current Blender window present itself as the VOXTART editor."""

    version = ".".join(str(value) for value in bpy.app.version[:3])
    _clear_factory_scene()
    scene = bpy.context.scene
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
                ("show_region_ui", True),
                ("show_region_hud", False),
                ("show_region_asset_shelf", False),
                ("show_region_tool_header", False),
            ):
                try:
                    setattr(space, property_name, value)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            space.show_gizmo = True
            space.overlay.show_floor = True
            space.overlay.show_axis_x = True
            space.overlay.show_axis_y = True
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

    scene[DESKTOP_MODE_PROPERTY] = True
    scene[DESKTOP_VERSION_PROPERTY] = version


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "DESKTOP_FULLSCREEN_PROPERTY",
    "DESKTOP_MODE_PROPERTY",
    "DESKTOP_VERSION_PROPERTY",
    "VOXTART_PT_desktop_home",
    "configure_workspace",
    "register",
    "unregister",
]
