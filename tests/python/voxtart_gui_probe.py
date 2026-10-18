# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Drive the real Blender viewport and capture the VOXTART editor overlay."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
import urllib.error
import urllib.request
from types import SimpleNamespace
import bpy


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "scripts" / "addons_core"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from voxtart import (
    blob_gallery,
    blockbench_formats,
    character_editor_ui,
    desktop_shell,
    desktop_ui,
    mcp,
    minecraft_formats,
    operators,
    optimizer_ui,
    palette_ui,
    scene_assembly,
    scene_hierarchy,
    scene_hierarchy_ui,
    stacking,
    voxel_editor,
)
from voxtart.voxel import VoxelAsset, VoxelSocket  # noqa: E402


MARKER = Path(os.environ.get("VOXTART_GUI_MARKER", "voxtart_gui_probe.json"))
SCREENSHOT = Path(os.environ.get("VOXTART_GUI_SCREENSHOT", "voxtart_gui_probe.png"))
SHELL_SCREENSHOT = Path(
    os.environ.get(
        "VOXTART_GUI_SHELL_SCREENSHOT",
        str(SCREENSHOT.with_name(f"{SCREENSHOT.stem}_shell{SCREENSHOT.suffix}")),
    )
)
MENU_SCREENSHOT = Path(
    os.environ.get(
        "VOXTART_GUI_MENU_SCREENSHOT",
        str(SCREENSHOT.with_name(f"{SCREENSHOT.stem}_menu{SCREENSHOT.suffix}")),
    )
)
SCENE_SCREENSHOT = Path(
    os.environ.get(
        "VOXTART_GUI_SCENE_SCREENSHOT",
        str(SCREENSHOT.with_name(f"{SCREENSHOT.stem}_scene{SCREENSHOT.suffix}")),
    )
)
GUI_SAVE_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_SAVE_PATH",
        str(SCREENSHOT.with_suffix(".blend")),
    )
)
GUI_ASSET_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_ASSET_PATH",
        str(SCREENSHOT.with_suffix(".voxel.json")),
    )
)
GUI_MESH_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_MESH_PATH",
        str(SCREENSHOT.with_suffix(".obj")),
    )
)
GUI_PALETTE_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_PALETTE_PATH",
        str(SCREENSHOT.with_suffix(".hex")),
    )
)
GUI_VOX_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_VOX_PATH",
        str(SCREENSHOT.with_suffix(".vox")),
    )
)
GUI_GAME_RIG_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_GAME_RIG_PATH",
        str(SCREENSHOT.with_suffix(".glb")),
    )
)
GUI_BLOCKBENCH_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_BLOCKBENCH_PATH",
        str(SCREENSHOT.with_suffix(".bbmodel")),
    )
)
GUI_MINECRAFT_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_MINECRAFT_PATH",
        str(SCREENSHOT.with_suffix(".schem")),
    )
)
GUI_STACK_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_STACK_PATH",
        str(SCREENSHOT.with_suffix(".sstack.json")),
    )
)
GUI_GALLERY_PATH = Path(
    os.environ.get(
        "VOXTART_GUI_GALLERY_PATH",
        str(SCREENSHOT.with_suffix(".blob-gallery")),
    )
)
STATE = {"attempts": 0}

class _ShellHarness:
    _activate = desktop_shell.VOXTART_OT_desktop_shell._activate
    _finish = desktop_shell.VOXTART_OT_desktop_shell._finish


def _write(payload: dict) -> None:
    MARKER.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


try:
    bpy.context.preferences.view.show_splash = False
except (AttributeError, RuntimeError):
    pass


def _quit() -> None:
    try:
        bpy.ops.wm.quit_blender()
    except RuntimeError:
        pass


def _fail(error: str):
    _write({"ok": False, "error": error})
    bpy.app.timers.register(_quit, first_interval=0.1)
    return None


def _window_area():
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        area = next((item for item in screen.areas if item.type == "VIEW_3D"), None)
        if area is None:
            continue
        region = next((item for item in area.regions if item.type == "WINDOW"), None)
        if region is not None:
            return window, screen, area, region
    return None


def _viewport_hits(editor, obj, area, region):
    region_data = area.spaces.active.region_3d
    ray_context = SimpleNamespace(region=region, region_data=region_data)
    asset = operators._load_asset(obj)
    hits = {}
    for cell in asset.voxels:
        local = voxel_editor.Vector(
            tuple((cell[axis] + 0.5) * asset.grid_size for axis in range(3))
        )
        point = voxel_editor.view3d_utils.location_3d_to_region_2d(
            region,
            region_data,
            obj.matrix_world @ local,
        )
        if point is None:
            continue
        local_point = (float(point.x), float(point.y))
        hit = editor._ray_hit(
            ray_context,
            SimpleNamespace(
                mouse_region_x=local_point[0],
                mouse_region_y=local_point[1],
            ),
        )
        if hit is not None and any(hit.normal):
            hits.setdefault(hit.cell, local_point)
    if len(hits) < 2:
        raise RuntimeError(f"Could not find two visible voxel faces: {sorted(hits)}")
    cells = sorted(hits)
    return hits[cells[0]], hits[cells[1]], cells[0], cells[1]


def _modal_click(point: tuple[float, float]) -> None:
    editor = STATE["editor"]
    event = SimpleNamespace(
        type="LEFTMOUSE",
        value="PRESS",
        ctrl=False,
        shift=False,
        alt=False,
        mouse_region_x=point[0],
        mouse_region_y=point[1],
    )
    with bpy.context.temp_override(
        window=STATE["window"],
        screen=STATE["screen"],
        area=STATE["area"],
        region=STATE["region"],
    ):
        editor.modal(bpy.context, event)
        event.value = "RELEASE"
        editor.modal(bpy.context, event)

def _modal_drag(points: list[tuple[float, float]]) -> None:
    if len(points) < 2:
        raise ValueError("drag probe requires at least two points")
    editor = STATE["editor"]
    with bpy.context.temp_override(
        window=STATE["window"],
        screen=STATE["screen"],
        area=STATE["area"],
        region=STATE["region"],
    ):
        press = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=points[0][0],
            mouse_region_y=points[0][1],
        )
        editor.modal(bpy.context, press)
        for point in points[1:]:
            move = SimpleNamespace(
                type="MOUSEMOVE",
                value="NOTHING",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=point[0],
                mouse_region_y=point[1],
            )
            editor.modal(bpy.context, move)
        release = SimpleNamespace(
            type="LEFTMOUSE",
            value="RELEASE",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=points[-1][0],
            mouse_region_y=points[-1][1],
        )
        editor.modal(bpy.context, release)


def _visible_paint_targets(editor, obj, area, region):
    region_data = area.spaces.active.region_3d
    ray_context = SimpleNamespace(region=region, region_data=region_data)
    asset = operators._load_asset(obj)
    targets = {}
    for cell, palette_index in asset.voxels.items():
        if palette_index != 0:
            continue
        local = voxel_editor.Vector(
            tuple((cell[axis] + 0.5) * asset.grid_size for axis in range(3))
        )
        point = voxel_editor.view3d_utils.location_3d_to_region_2d(
            region,
            region_data,
            obj.matrix_world @ local,
        )
        if point is None:
            continue
        hit = editor._ray_hit(
            ray_context,
            SimpleNamespace(
                mouse_region_x=float(point.x),
                mouse_region_y=float(point.y),
            ),
        )
        if hit is not None and any(hit.normal) and asset.voxels.get(hit.cell) == 0:
            targets.setdefault(hit.cell, (float(point.x), float(point.y)))
    return [(point, cell) for cell, point in sorted(targets.items())]
def _modal_key(event_type: str, *, ctrl: bool = False, shift: bool = False):
    event = SimpleNamespace(
        type=event_type,
        value="PRESS",
        ctrl=ctrl,
        shift=shift,
        alt=False,
        mouse_region_x=-1.0,
        mouse_region_y=-1.0,
    )
    with bpy.context.temp_override(
        window=STATE["window"],
        screen=STATE["screen"],
        area=STATE["area"],
        region=STATE["region"],
    ):
        return STATE["editor"].modal(bpy.context, event)


def _send_left_click():
    try:
        _modal_click(STATE["left_point"])
        bpy.app.timers.register(_check_left_click, first_interval=0.5)
    except Exception as error:
        return _fail(f"{type(error).__name__}: {error}")
    return None


def _check_left_click():
    try:
        asset = operators._load_asset(STATE["obj"])
        baseline = STATE["baseline_voxels"]
        added = set(asset.voxels).difference(baseline)
        if len(added) != 1:
            return _fail(f"Left click did not place exactly one voxel: added={sorted(added)}")
        added_cell = next(iter(added))
        if asset.voxels[added_cell] != 1:
            return _fail(f"Left click did not use the selected non-default palette: {asset.voxels[added_cell]!r}")
        mesh_material_indices = {
            int(polygon.material_index)
            for polygon in getattr(STATE["obj"].data, "polygons", ())
        }
        if 1 not in mesh_material_indices:
            return _fail(
                f"Left click changed voxel data but not the displayed material: "
                f"materials={mesh_material_indices!r}, palette={asset.palette!r}, "
                f"voxel={asset.voxels.get(added_cell)!r}, "
                f"slots={len(getattr(STATE['obj'].data, 'materials', ()))}"
            )
        palette_material = STATE["obj"].data.materials[1]
        shader = getattr(getattr(palette_material, "node_tree", None), "nodes", {}).get(
            "Principled BSDF"
        )
        base_color = shader.inputs.get("Base Color") if shader is not None else None
        actual_color = (
            tuple(float(value) for value in base_color.default_value)
            if base_color is not None
            else ()
        )
        expected_color = tuple(float(value) for value in asset.palette[1])
        if (
            len(actual_color) != len(expected_color)
            or any(abs(actual - expected) > 1.0e-6 for actual, expected in zip(actual_color, expected_color))
        ):
            return _fail(
                f"Palette material node did not use the selected color: "
                f"actual={actual_color!r}, expected={expected_color!r}"
            )
        if STATE["scene"].get(voxel_editor.FIRST_RUN_HINT_PROPERTY) is not False:
            return _fail("First-edit guidance did not close after a changed edit")
        editor = STATE["editor"]
        if not editor.status.startswith("Added ") or "Ctrl+Z" not in editor.status:
            return _fail(f"First-edit feedback is not actionable: {editor.status!r}")
        STATE["first_edit_feedback"] = editor.status
        STATE["after_left_voxels"] = set(asset.voxels)
        STATE["added_cell"] = next(iter(added))
        STATE["scene"].voxtart_editor_mode = "ERASE"
        bpy.app.timers.register(_send_erase_click, first_interval=0.25)
    except Exception as error:
        return _fail(f"{type(error).__name__}: {error}")
    return None


def _send_erase_click():
    try:
        _modal_click(STATE["right_point"])
        bpy.app.timers.register(_finish_mouse_clicks, first_interval=0.5)
    except Exception as error:
        return _fail(f"{type(error).__name__}: {error}")
    return None


def _finish_mouse_clicks():
    try:
        asset = operators._load_asset(STATE["obj"])
        removed = STATE["after_left_voxels"].difference(asset.voxels)
        if len(removed) != 1:
            return _fail(f"Erase click did not remove exactly one voxel: removed={sorted(removed)}")
        if STATE["added_cell"] not in asset.voxels:
            return _fail("Erase click removed the placed voxel instead of the hit voxel")
        editor = STATE["editor"]
        scene = STATE["scene"]
        toolbar_items = voxel_editor._editor_toolbar_layout(STATE["region"].height, scene)[1]
        paint_item, paint_bounds = next(
            (item, bounds)
            for item, bounds in toolbar_items
            if item[0] == "MODE" and item[1] == "PAINT"
        )
        paint_mode_event = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=(paint_bounds[0] + paint_bounds[2]) * 0.5,
            mouse_region_y=(paint_bounds[1] + paint_bounds[3]) * 0.5,
        )
        with bpy.context.temp_override(
            window=STATE["window"],
            screen=STATE["screen"],
            area=STATE["area"],
            region=STATE["region"],
        ):
            paint_mode_result = editor.modal(bpy.context, paint_mode_event)
        if (
            "RUNNING_MODAL" not in paint_mode_result
            or scene.voxtart_editor_mode != paint_item[1]
        ):
            return _fail("Color block toolbar click did not activate paint mode")
        scene.voxtart_editor_shape = "VOXEL"
        scene.voxtart_editor_radius = 1
        scene.voxtart_edit_palette_index = 1
        before_paint_asset = operators._load_asset(STATE["obj"])
        before_paint_voxels = dict(before_paint_asset.voxels)
        paint_hits = _viewport_hits(
            STATE["editor"],
            STATE["obj"],
            STATE["area"],
            STATE["region"],
        )
        paint_target = next(
            (
                (point, cell)
                for point, cell in (
                    (paint_hits[0], paint_hits[2]),
                    (paint_hits[1], paint_hits[3]),
                )
                if before_paint_voxels.get(cell) == 0
            ),
            None,
        )
        if paint_target is None:
            return _fail("Paint workflow could not find a visible default-color voxel")
        paint_point, paint_cell = paint_target
        hover_event = SimpleNamespace(
            type="MOUSEMOVE",
            value="NOTHING",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=paint_point[0],
            mouse_region_y=paint_point[1],
        )
        with bpy.context.temp_override(
            window=STATE["window"],
            screen=STATE["screen"],
            area=STATE["area"],
            region=STATE["region"],
        ):
            hover_result = editor.modal(bpy.context, hover_event)
        if (
            "RUNNING_MODAL" not in hover_result
            or editor.last_hit is None
            or editor.last_hit.cell != paint_cell
            or paint_cell not in editor.preview_cells
        ):
            return _fail(
                f"Voxel mouseover did not highlight the target cell: "
                f"hit={getattr(editor.last_hit, 'cell', None)!r}, "
                f"preview={editor.preview_cells!r}"
            )
        STATE["hover_highlight"] = True
        _modal_click(paint_point)
        painted_asset = operators._load_asset(STATE["obj"])
        painted_cells = [
            cell
            for cell, palette_index in before_paint_voxels.items()
            if painted_asset.voxels.get(cell) != palette_index
        ]
        painted_mesh_materials = {
            int(polygon.material_index)
            for polygon in getattr(STATE["obj"].data, "polygons", ())
        }
        if 1 not in painted_mesh_materials:
            return _fail(
                f"Paint changed voxel data but not the displayed material: "
                f"materials={painted_mesh_materials!r}"
            )
        if len(painted_cells) != 1 or painted_asset.voxels.get(painted_cells[0]) != 1:
            return _fail(
                f"Paint click did not apply the selected palette color: "
                f"changed={painted_cells!r}, palette={painted_asset.voxels!r}"
            )
        painted_cell = painted_cells[0]
        previous_palette_index = before_paint_voxels[painted_cell]
        if _modal_key("Z", ctrl=True) != {"RUNNING_MODAL"}:
            return _fail("Ctrl+Z did not run through the editor")
        undone_asset = operators._load_asset(STATE["obj"])
        if undone_asset.voxels.get(painted_cell) != previous_palette_index:
            return _fail("Ctrl+Z did not restore the prior voxel color")
        if _modal_key("Y", ctrl=True) != {"RUNNING_MODAL"}:
            return _fail("Ctrl+Y did not run through the editor")
        redone_asset = operators._load_asset(STATE["obj"])
        if redone_asset.voxels.get(painted_cell) != 1:
            return _fail("Ctrl+Y did not restore the painted voxel color")
        with bpy.context.temp_override(
            window=STATE["window"],
            screen=STATE["screen"],
            area=STATE["area"],
            region=STATE["region"],
        ):
            editor._activate_topbar_action(bpy.context, "UNDO")
        toolbar_undone_asset = operators._load_asset(STATE["obj"])
        if toolbar_undone_asset.voxels.get(painted_cell) != previous_palette_index:
            return _fail("Undo button did not restore the prior voxel color")
        with bpy.context.temp_override(
            window=STATE["window"],
            screen=STATE["screen"],
            area=STATE["area"],
            region=STATE["region"],
        ):
            editor._activate_topbar_action(bpy.context, "REDO")
        toolbar_redone_asset = operators._load_asset(STATE["obj"])
        if toolbar_redone_asset.voxels.get(painted_cell) != 1:
            return _fail("Redo button did not restore the painted voxel color")
        STATE["undo_redo_controls"] = True
        scene.voxtart_editor_mode = "PAINT"
        scene.voxtart_editor_shape = "VOXEL"
        scene.voxtart_editor_radius = 1
        scene.voxtart_edit_palette_index = 1
        drag_targets = _visible_paint_targets(
            editor,
            STATE["obj"],
            STATE["area"],
            STATE["region"],
        )
        if len(drag_targets) < 2:
            return _fail(f"Drag paint found too few visible target voxels: {drag_targets!r}")
        drag_targets = drag_targets[: min(3, len(drag_targets))]
        before_drag_voxels = dict(operators._load_asset(STATE["obj"]).voxels)
        history_before_drag = editor.history_index
        _modal_drag([point for point, _cell in drag_targets])
        after_drag_asset = operators._load_asset(STATE["obj"])
        drag_cells = [cell for _point, cell in drag_targets]
        changed_drag_cells = [
            cell
            for cell, palette_index in before_drag_voxels.items()
            if after_drag_asset.voxels.get(cell) != palette_index
        ]
        if (
            any(after_drag_asset.voxels.get(cell) != 1 for cell in drag_cells)
            or len(changed_drag_cells) < 2
            or editor.transaction is not None
            or editor.history_index != history_before_drag + 1
        ):
            return _fail(
                f"Left drag did not paint one committed brush stroke: "
                f"targets={drag_cells!r}, changed={changed_drag_cells!r}, "
                f"history={history_before_drag}->{editor.history_index}"
            )
        STATE["drag_paint"] = True
        if voxel_editor.visible_editor_toolbar_items(scene) is not voxel_editor.BEGINNER_TOOLBAR_ITEMS:
            return _fail("Beginner toolbar expanded before the user asked for more tools")
        STATE["beginner_toolbar_after_first_edit"] = True
        scene.voxtart_editor_mode = "SELECT"
        scene.voxtart_editor_shape = "VOXEL"
        _modal_click(paint_point)
        selected_cells = tuple(editor.selection)
        if len(selected_cells) != 1 or editor.last_hit is None:
            return _fail(f"Select click did not select one voxel: {selected_cells!r}")
        if selected_cells != (editor.last_hit.cell,):
            return _fail(
                f"Select click stored the wrong hit: {selected_cells!r} != {editor.last_hit.cell!r}"
            )
        if _modal_key("C", ctrl=True) != {"RUNNING_MODAL"}:
            return _fail("Ctrl+C did not copy the selected voxel")
        if voxel_editor.EDITOR_CLIPBOARD_PROPERTY not in STATE["obj"]:
            return _fail("Selected voxel copy did not persist clipboard state")
        if _modal_key("D", ctrl=True) != {"RUNNING_MODAL"} or editor.selection:
            return _fail("Ctrl+D did not clear the visible selection")
        STATE["selection_workflow"] = True
        STATE["painted_cell"] = painted_cell
        STATE["paint_undo_redo"] = True
        history_labels = tuple(
            str(getattr(snapshot, "label", ""))
            for snapshot in editor.history
        )
        if (
            not history_labels
            or history_labels[0] != "Initial state"
            or not any(label.startswith("Added ") for label in history_labels)
            or not any(label.startswith("Erased ") for label in history_labels)
            or not any(label.startswith("Colored ") for label in history_labels)
        ):
            return _fail(f"VOXTART history labels were not descriptive: {history_labels!r}")
        STATE["history_labels"] = history_labels
        STATE["history_presentation"] = True

        scene.voxtart_editor_mode = "ADD"
        scene.voxtart_editor_shape = "FACE"
        scene.voxtart_editor_radius = 2
        scene.voxtart_editor_depth = 2
        scene.voxtart_editor_mirror_x = True
        editor.last_hit = voxel_editor.RayHit((0, 0, 0), 0, 0.0, (1, 0, 0))
        editor.toolbar_hover = ("MODE", "ADD", "+", "Place voxel", "B")
        editor.preview_cells = editor._stamp(editor.last_hit)
        editor.status = "GUI E2E LMB place + LMB erase passed"
        STATE["area"].tag_redraw()
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=10)
        SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        saved_preview_cells = editor.preview_cells
        saved_last_hit = editor.last_hit
        saved_toolbar_hover = editor.toolbar_hover
        try:
            with bpy.context.temp_override(
                window=STATE["window"],
                screen=STATE["screen"],
                area=STATE["area"],
                region=STATE["region"],
            ):
                voxel_editor._draw_editor_overlay(editor, bpy.context)
                STATE["editor_overlay_draw"] = True
                bpy.ops.view3d.view_selected(use_all_regions=False)
                screenshot_result = bpy.ops.screen.screenshot(filepath=str(SCREENSHOT))
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=10)
                screenshot_result = bpy.ops.screen.screenshot(filepath=str(SCREENSHOT))
        finally:
            editor.preview_cells = saved_preview_cells
            editor.last_hit = saved_last_hit
            editor.toolbar_hover = saved_toolbar_hover
        if "FINISHED" not in screenshot_result or not SCREENSHOT.is_file():
            return _fail(f"Could not capture GUI screenshot: {screenshot_result!r}")
        _write(
            {
                "ok": True,
                "editor_overlay_draw": STATE.get("editor_overlay_draw", False),
                "operator_running_modal": True,
                "draw_overlay_handle": editor._draw_handle is not None,
                "draw_preview_handle": editor._preview_handle is not None,
                "view_workflow": STATE.get("view_workflow", False),
                "viewport_hud": STATE.get("viewport_hud", False),
                "tool_options": STATE.get("tool_options", False),
                "tool_options_hints": STATE.get("tool_options_hints", False),
                "selection_tool_options": STATE.get("selection_tool_options", False),
                "scene_options": STATE.get("scene_options", False),
                "scene_completion_controls": STATE.get("scene_completion_controls", False),
                "scene_option_hints": STATE.get("scene_option_hints", False),
                "scene_screenshot": STATE.get("scene_options_screenshot", ""),
                "shell_draw_handle": STATE.get("shell_draw_handle", False),
                "shell_screenshot": STATE.get("shell_screenshot", ""),
                "shell_make": STATE.get("shell_make", False),
                "shell_native_ui_blocked": STATE.get("shell_native_ui_blocked", False),
                "chatbox_surface": STATE.get("chatbox_surface", False),
                "shell_editor": STATE.get("shell_editor", False),
                "shell_voxelize": STATE.get("shell_voxelize", False),
                "shell_refresh": STATE.get("shell_refresh", False),
                "shell_optimize": STATE.get("shell_optimize", False),
                "portable_roundtrip": STATE.get("portable_roundtrip", False),
                "mesh_export": STATE.get("mesh_export", False),
                "palette_import": STATE.get("palette_import", False),
                "palette_reduce": STATE.get("palette_reduce", False),
                "blockbench_roundtrip": STATE.get("blockbench_roundtrip", False),
                "hierarchy_roundtrip": STATE.get("hierarchy_roundtrip", False),
                "kitbash_roundtrip": STATE.get("kitbash_roundtrip", False),
                "character_editor_roundtrip": STATE.get("character_editor_roundtrip", False),
                "character_native_ui_blocked": STATE.get("character_native_ui_blocked", False),
                "mcp_roundtrip": STATE.get("mcp_roundtrip", False),
                "minecraft_roundtrip": STATE.get("minecraft_roundtrip", False),
                "stack_roundtrip": STATE.get("stack_roundtrip", False),
                "gallery_roundtrip": STATE.get("gallery_roundtrip", False),
                "native_vox_roundtrip": STATE.get("native_vox_roundtrip", False),
                "character_prepare": STATE.get("character_prepare", False),
                "character_extract": STATE.get("character_extract", False),
                "character_bake": STATE.get("character_bake", False),
                "character_preview": STATE.get("character_preview", False),
                "character_export": STATE.get("character_export", False),
                "save_control": STATE.get("save_control", False),
                "starter_pyramid": STATE.get("starter_pyramid", 0),
                "starter_palette": STATE.get("starter_palette", 0),
                "startup_editor": STATE.get("startup_editor", False),
                "first_run_hint": STATE.get("first_run_hint", False),
                "first_edit_feedback": STATE.get("first_edit_feedback", ""),
                "native_chrome_hidden": STATE.get("native_chrome_hidden", False),
                "selection_workflow": STATE.get("selection_workflow", False),
                "area_types": STATE.get("area_types", []),
                "window_fullscreen": bool(
                    STATE["scene"].get(desktop_ui.DESKTOP_WINDOW_FULLSCREEN_PROPERTY)
                ),
                "native_ui_lock": STATE.get("native_ui_lock", False),
                "advanced_tools_toggle": STATE.get("advanced_tools_toggle", False),
                "beginner_toolbar_after_first_edit": STATE.get("beginner_toolbar_after_first_edit", False),
                "grid_toggle": STATE.get("grid_toggle", False),
                "options_menu": STATE.get("options_menu", False),
                "region_info": STATE.get("region_info", []),
                "details_toggle": STATE.get("details_toggle", False),
                "help_toggle": STATE.get("help_toggle", False),
                "native_region_props": {
                    name: bool(getattr(STATE["area"].spaces.active, name, False))
                    for name in (
                        "show_region_toolbar",
                        "show_region_ui",
                        "show_region_header",
                        "show_region_footer",
                        "show_region_tool_header",
                    )
                },
                "undo_redo_controls": STATE.get("undo_redo_controls", False),
                "paint_undo_redo": STATE.get("paint_undo_redo", False),
                "history_presentation": STATE.get("history_presentation", False),
                "history_labels": list(STATE.get("history_labels", ())),
                "drag_paint": STATE.get("drag_paint", False),
                "palette_non_default": STATE.get("palette_non_default", False),
                "hover_highlight": STATE.get("hover_highlight", False),
                "material_shading": STATE.get("material_shading", False),
                "palette_selection": STATE.get("palette_selection", False),
                "palette_favorite": STATE.get("palette_favorite", False),
                "color_wheel": STATE.get("color_wheel", False),
                "grid_shortcut": STATE.get("grid_shortcut", False),
                "dock_drawers": STATE.get("dock_drawers", False),
                "menu_surface": STATE.get("menu_surface", False),
                "dock_snap": STATE.get("dock_snap", False),
                "product_controls": STATE.get("product_controls", False),
                "project_control": STATE.get("project_control", False),
                "escape_keeps_ui": STATE.get("escape_keeps_ui", False),
                "preview_count": len(editor.preview_cells),
                "area": [STATE["area"].width, STATE["area"].height],
                "screenshot": str(SCREENSHOT),
                "menu_screenshot": str(MENU_SCREENSHOT),
                "mouse_place_erase": True,
                "baseline_count": len(STATE["baseline_voxels"]),
                "after_left_count": len(STATE["after_left_voxels"]),
                "after_erase_count": len(asset.voxels),
                "added_cell": list(STATE["added_cell"]),
                "removed_cell": list(next(iter(removed))),
            }
        )
        bpy.app.timers.register(_quit, first_interval=0.5)
    except Exception as error:
        return _fail(f"{type(error).__name__}: {error}")
    return None


def _exercise_view_workflow(window, screen, area, region, scene):
    cameras = []
    try:
        for name, location in (
            ("P0 GUI Camera A", (6.0, -6.0, 4.0)),
            ("P0 GUI Camera B", (-6.0, -6.0, 4.0)),
        ):
            data = bpy.data.cameras.new(f"{name} Data")
            camera = bpy.data.objects.new(name, data)
            camera.location = location
            scene.collection.objects.link(camera)
            cameras.append(camera)
        scene.camera = cameras[0]
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            if bpy.ops.voxtart.view_perspective(mode="ORTHO") != {"FINISHED"}:
                raise RuntimeError("orthographic view control did not finish")
            if area.spaces.active.region_3d.view_perspective != "ORTHO":
                raise RuntimeError("orthographic view control did not change the viewport")
            if bpy.ops.voxtart.view_perspective(mode="CAMERA") != {"FINISHED"}:
                raise RuntimeError("camera view control did not finish")
            if area.spaces.active.region_3d.view_perspective != "CAMERA":
                raise RuntimeError("camera view control did not enter camera view")
            if bpy.ops.voxtart.camera_set_active(object_name=cameras[1].name) != {"FINISHED"}:
                raise RuntimeError("camera selection control did not finish")
            if bpy.ops.voxtart.camera_solo(object_name=cameras[1].name) != {"FINISHED"}:
                raise RuntimeError("camera solo control did not finish")
            if not cameras[0].hide_viewport or cameras[1].hide_viewport:
                raise RuntimeError("camera solo visibility state is incorrect")
            if bpy.ops.voxtart.camera_show_all() != {"FINISHED"}:
                raise RuntimeError("camera visibility reset did not finish")
            if cameras[0].hide_viewport or cameras[1].hide_viewport:
                raise RuntimeError("camera visibility reset is incorrect")
            if bpy.ops.voxtart.view_perspective(mode="PERSP") != {"FINISHED"}:
                raise RuntimeError("perspective view control did not finish")
            if area.spaces.active.region_3d.view_perspective != "PERSP":
                raise RuntimeError("perspective view control did not restore the viewport")
    finally:
        for camera in cameras:
            camera_data = camera.data
            if camera.name in bpy.data.objects:
                bpy.data.objects.remove(camera, do_unlink=True)
            if camera_data is not None and camera_data.name in bpy.data.cameras:
                bpy.data.cameras.remove(camera_data)
    return True


def _probe():
    STATE["attempts"] += 1
    try:
        location = _window_area()
        if location is None:
            if STATE["attempts"] < 120:
                return 0.25
            return _fail("No interactive VIEW_3D window appeared")
        window, screen, area, region = location
        scene = bpy.context.scene
        if not scene.get(desktop_ui.DESKTOP_MODE_PROPERTY):
            desktop_ui.configure_workspace()
            location = _window_area()
            if location is None:
                return _fail("Desktop workspace did not expose a VIEW_3D area")
            window, screen, area, region = location
        STATE["region_info"] = [
            [region_item.type, region_item.width, region_item.height, region_item.y]
            for region_item in area.regions
        ]
        starter = bpy.data.objects.get(desktop_ui.DEFAULT_PYRAMID_OBJECT_NAME)
        if starter is None or not starter.get(desktop_ui.DEFAULT_PYRAMID_PROPERTY):
            return _fail("VOXTART startup did not create the editable starter pyramid")
        starter_asset = operators._load_asset(starter)
        if (
            len(starter_asset.voxels) != 56
            or starter_asset.palette != list(desktop_ui.DEFAULT_STARTER_PALETTE)
        ):
            return _fail("VOXTART starter pyramid has the wrong geometry or palette")
        STATE["starter_pyramid"] = len(starter_asset.voxels)
        STATE["starter_palette"] = len(starter_asset.palette)
        STATE["area_types"] = [area.type for area in screen.areas]
        STATE["startup_editor"] = voxel_editor._ACTIVE_EDITOR is not None
        STATE["first_run_hint"] = bool(scene.get(voxel_editor.FIRST_RUN_HINT_PROPERTY))
        STATE["native_chrome_hidden"] = not bool(scene.get(desktop_ui.DESKTOP_NATIVE_CHROME_PROPERTY))
        if scene.get(desktop_ui.DESKTOP_WINDOW_FULLSCREEN_PROPERTY):
            return _fail("VOXTART startup hid the Windows title bar controls")
        if not STATE["native_chrome_hidden"]:
            return _fail("VOXTART startup left Blender chrome visible")
        if screen.show_statusbar or any(
            bool(getattr(area.spaces.active, property_name, False))
            for property_name in (
                "show_region_toolbar",
                "show_region_ui",
                "show_region_header",
                "show_region_footer",
                "show_region_tool_header",
            )
        ):
            return _fail("VOXTART startup left a Blender native region visible")
        shading = area.spaces.active.shading
        if (
            getattr(shading, "type", None) != "SOLID"
            or getattr(shading, "color_type", None) != "MATERIAL"
            or getattr(shading, "light", None) != "FLAT"
        ):
            return _fail("VOXTART startup did not enable flat palette material shading")
        if not STATE["startup_editor"]:
            return _fail("VOXTART startup did not open the voxel editor")
        if not STATE["first_run_hint"]:
            return _fail("VOXTART startup did not enable first-edit guidance")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            voxel_editor._ACTIVE_EDITOR._finish(bpy.context, {"CANCELLED"})
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            desktop_shell._set_mode(scene, "BUILD")
            if desktop_shell._ACTIVE_STATE is None:
                result = bpy.ops.voxtart.desktop_shell("INVOKE_DEFAULT")
                if "RUNNING_MODAL" not in result:
                    return _fail(f"Desktop shell did not enter modal state: {result!r}")
            shell_state = desktop_shell._ACTIVE_STATE
            if shell_state is None:
                return _fail("Desktop shell state was not created")
            if desktop_shell._ACTIVE_HANDLE is None:
                return _fail("Desktop shell did not install its draw overlay")
            STATE["shell_draw_handle"] = True
            desktop_shell._draw_shell(shell_state, bpy.context)
            chat_targets = {key: bounds for key, bounds in shell_state.targets if key.startswith("chat:")}
            if not {"chat:image", "chat:field", "chat:send"}.issubset(chat_targets):
                return _fail(f"Desktop shell chat bar targets are incomplete: {chat_targets!r}")
            chat_panel = mcp.chat_layout(
                float(region.width),
                float(region.height),
            )["panel"]
            if abs((chat_panel[0] + chat_panel[2]) * 0.5 - float(region.width) * 0.5) > 1.0:
                return _fail(f"Desktop shell chat bar is not centered: {chat_panel!r}")
            STATE["chatbox_surface"] = True
            if any(key == "mode:CHARACTER" for key, _bounds in shell_state.targets):
                return _fail("First-run shell exposed premature Character navigation")
            shell_toolbar_count = sum(key.startswith("tool:") for key, _bounds in shell_state.targets)
            toolbar_items = voxel_editor.visible_editor_toolbar_items(scene)
            expected_toolbar_count = sum(item is not None for item in toolbar_items)
            if tuple(item[3] for item in toolbar_items if item is not None) != (
                "Add block",
                "Erase block",
                "Color block",
                "Select blocks",
            ):
                return _fail("First-run toolbar did not expose plain beginner labels")
            if shell_toolbar_count != expected_toolbar_count:
                return _fail(
                    f"Desktop shell toolbar target count mismatch: "
                    f"{shell_toolbar_count} != {expected_toolbar_count}"
                )
            shell_toolbar_widths = [
                bounds[2] - bounds[0]
                for key, bounds in shell_state.targets
                if key.startswith("tool:")
            ]
            maximum_width = 136.0 if STATE["first_run_hint"] else 52.0
            if not shell_toolbar_widths or max(shell_toolbar_widths) > maximum_width:
                return _fail(f"Desktop shell toolbar is not compact: {shell_toolbar_widths!r}")
            if any(key == "shell:close" for key, _bounds in shell_state.targets):
                return _fail("VOXTART shell close control is still exposed")
            area.tag_redraw()
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
            SHELL_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            screenshot_result = bpy.ops.screen.screenshot(filepath=str(SHELL_SCREENSHOT))
            if "FINISHED" not in screenshot_result or not SHELL_SCREENSHOT.is_file():
                return _fail(f"Could not capture shell screenshot: {screenshot_result!r}")
            STATE["shell_toolbar"] = shell_toolbar_count
            STATE["shell_screenshot"] = str(SHELL_SCREENSHOT)
            with bpy.context.temp_override(
                window=window,
                screen=screen,
                area=area,
                region=region,
            ):
                desktop_shell._set_mode(scene, "HOME")
                shell_operator = _ShellHarness()
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "home:make" for key, _bounds in shell_state.targets):
                    return _fail("Project surface did not expose Make Blocks")
                before_shell_assets = set(desktop_shell._asset_objects())
                shell_operator._activate(bpy.context, "home:make")
                created_shell_assets = set(desktop_shell._asset_objects()).difference(before_shell_assets)
                if len(created_shell_assets) != 1:
                    return _fail(
                        f"Make Blocks did not create exactly one asset: {created_shell_assets!r}"
                    )
                created_shell_asset = next(iter(created_shell_assets))
                if len(operators._load_asset(created_shell_asset).voxels) != 64:
                    return _fail("Make Blocks created an asset with the wrong voxel count")
                STATE["shell_make"] = True
                with bpy.context.temp_override(
                    window=window,
                    screen=screen,
                    area=area,
                    region=region,
                ):
                    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(8.0, 0.0, 0.0))
                source_mesh = bpy.context.active_object
                if source_mesh is None or source_mesh.type != "MESH":
                    return _fail("Build pipeline could not create a source mesh")
                before_voxelized_assets = set(desktop_shell._asset_objects())
                shell_operator._activate(bpy.context, "build:voxelize")
                voxelized_assets = set(desktop_shell._asset_objects()).difference(
                    before_voxelized_assets
                )
                if len(voxelized_assets) != 1:
                    return _fail(
                        f"Voxelize selected did not create exactly one asset: {voxelized_assets!r}"
                    )
                voxelized_asset = next(iter(voxelized_assets))
                if (
                    voxelized_asset.get(operators.VOXELIZER_SOURCE_PROPERTY) != source_mesh.name
                    or not operators._load_asset(voxelized_asset).voxels
                ):
                    return _fail("Voxelize selected did not preserve its source relationship")
                before_refresh_voxels = len(operators._load_asset(voxelized_asset).voxels)
                for vertex in source_mesh.data.vertices:
                    vertex.co *= 2.0
                source_mesh.data.update()
                shell_operator._activate(bpy.context, "build:refresh")
                refreshed_asset = operators._load_asset(voxelized_asset)
                if (
                    len(refreshed_asset.voxels) <= before_refresh_voxels
                    or voxelized_asset.get(operators.VOXELIZER_SOURCE_PROPERTY) != source_mesh.name
                    or not voxelized_asset.get(operators.VOXELIZER_REPORT_PROPERTY)
                ):
                    return _fail("Refresh linked did not rebuild the asset from its source mesh")
                STATE["shell_refresh"] = True
                STATE["shell_voxelize"] = True
                shell_operator._activate(bpy.context, "build:optimize")
                if not voxelized_asset.get(optimizer_ui.OPTIMIZER_REPORT_PROPERTY):
                    return _fail("Optimize active did not write an optimization report")
                STATE["shell_optimize"] = True
                portable_source = operators._load_asset(voxelized_asset)
                GUI_ASSET_PATH.parent.mkdir(parents=True, exist_ok=True)
                export_result = bpy.ops.voxtart.export_asset(filepath=str(GUI_ASSET_PATH))
                if "FINISHED" not in export_result or not GUI_ASSET_PATH.is_file():
                    return _fail(
                        f"Portable asset export did not write its file: {export_result!r}"
                    )
                import_result = bpy.ops.voxtart.import_asset(filepath=str(GUI_ASSET_PATH))
                if "FINISHED" not in import_result:
                    return _fail(f"Portable asset import did not finish: {import_result!r}")
                imported_asset_object = bpy.context.active_object
                if imported_asset_object is None or operators.ASSET_PROPERTY not in imported_asset_object:
                    return _fail("Portable asset import did not activate a VOXTART asset")
                imported_asset = operators._load_asset(imported_asset_object)
                if (
                    imported_asset.voxels != portable_source.voxels
                    or imported_asset.palette != portable_source.palette
                ):
                    return _fail("Portable asset round-trip changed authoritative voxel data")
                STATE["portable_roundtrip"] = True
                GUI_MESH_PATH.parent.mkdir(parents=True, exist_ok=True)
                mesh_export_result = bpy.ops.voxtart.export_optimized_mesh(
                    filepath=str(GUI_MESH_PATH),
                    format="OBJ",
                    backend="GREEDY",
                    export_selected=False,
                )
                if (
                    "FINISHED" not in mesh_export_result
                    or not GUI_MESH_PATH.is_file()
                    or GUI_MESH_PATH.stat().st_size <= 0
                    or "voxtart_last_mesh_export" not in imported_asset_object
                ):
                    return _fail(
                        f"Optimized mesh export did not write its report and file: "
                        f"{mesh_export_result!r}"
                    )
                STATE["mesh_export"] = True
                GUI_PALETTE_PATH.parent.mkdir(parents=True, exist_ok=True)
                GUI_PALETTE_PATH.write_text("#112233\n#DDEEFF\n", encoding="utf-8")
                scene.voxtart_palette_enforce = True
                palette_result = bpy.ops.voxtart.palette_import(filepath=str(GUI_PALETTE_PATH))
                expected_palette = [
                    tuple(round(channel, 6) for channel in color)
                    for color in (
                        (17 / 255, 34 / 255, 51 / 255, 1.0),
                        (221 / 255, 238 / 255, 1.0, 1.0),
                    )
                ]
                actual_palette = [
                    tuple(round(channel, 6) for channel in color)
                    for color in palette_ui._palette_for_display(scene)
                ]
                imported_palette = [
                    tuple(round(channel, 6) for channel in color)
                    for color in operators._load_asset(imported_asset_object).palette
                ]
                if (
                    "FINISHED" not in palette_result
                    or actual_palette != expected_palette
                    or imported_palette != expected_palette
                ):
                    return _fail(f"Palette file import did not update the active palette: {palette_result!r}")
                STATE["palette_import"] = True
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                imported_asset_object.select_set(True)
                bpy.context.view_layer.objects.active = imported_asset_object
                desktop_shell._set_mode(scene, "BUILD")
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "palette:reduce" for key, _bounds in shell_state.targets):
                    return _fail("Project surface did not expose Reduce palette")
                before_reduced_cells = dict(operators._load_asset(imported_asset_object).voxels)
                scene.voxtart_palette_reduce_colors = 1
                shell_operator._activate(bpy.context, "palette:reduce")
                reduced_asset = operators._load_asset(imported_asset_object)
                try:
                    reduction_report = json.loads(
                        imported_asset_object.get("voxtart_palette_reduction_report", "{}")
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    reduction_report = {}
                if (
                    len(reduced_asset.palette) != 1
                    or set(reduced_asset.voxels) != set(before_reduced_cells)
                    or reduction_report.get("target_colors") != 1
                    or reduction_report.get("target_voxels") != len(before_reduced_cells)
                ):
                    return _fail(
                        f"Palette reduction changed asset occupancy or report: "
                        f"{reduction_report!r}"
                    )
                STATE["palette_reduce"] = True
                native_source = reduced_asset
                before_native_assets = set(desktop_shell._asset_objects())
                GUI_VOX_PATH.parent.mkdir(parents=True, exist_ok=True)
                vox_export_result = bpy.ops.voxtart.export_voxel_format(
                    filepath=str(GUI_VOX_PATH),
                    format="VOX",
                    export_selected=False,
                )
                if (
                    "FINISHED" not in vox_export_result
                    or not GUI_VOX_PATH.is_file()
                    or GUI_VOX_PATH.stat().st_size <= 0
                ):
                    return _fail(f"VOX export did not write its file: {vox_export_result!r}")
                vox_import_result = bpy.ops.voxtart.import_voxel_format(filepath=str(GUI_VOX_PATH))
                native_assets = set(desktop_shell._asset_objects()).difference(before_native_assets)
                if len(native_assets) != 1:
                    return _fail(f"VOX import did not create exactly one asset: {native_assets!r}")
                native_object = next(iter(native_assets))
                native_asset = operators._load_asset(native_object)
                def _normalized_colors(asset):
                    minimum = asset.bounds[0] if asset.bounds is not None else (0, 0, 0)
                    return {
                        (
                            tuple(cell[axis] - minimum[axis] for axis in range(3)),
                            tuple(round(channel, 6) for channel in asset.palette[palette_index]),
                        )
                        for cell, palette_index in asset.voxels.items()
                    }

                source_colors = _normalized_colors(native_source)
                imported_colors = _normalized_colors(native_asset)
                if (
                    "FINISHED" not in vox_import_result
                    or native_object.get("voxtart_import_format") != "VOX"
                    or source_colors != imported_colors
                ):
                    return _fail(f"VOX round-trip changed the native voxel model: {vox_import_result!r}")
                STATE["native_vox_roundtrip"] = True
                desktop_shell._set_mode(scene, "BUILD")
                desktop_shell._draw_shell(shell_state, bpy.context)
                required_interchange_keys = {
                    "format:blockbench",
                    "format:minecraft",
                    "format:stack",
                }
                visible_interchange_keys = {key for key, _bounds in shell_state.targets}
                if not required_interchange_keys.issubset(visible_interchange_keys):
                    return _fail("Project surface did not expose interchange imports")
                before_interchange_assets = set(desktop_shell._asset_objects())
                GUI_BLOCKBENCH_PATH.parent.mkdir(parents=True, exist_ok=True)
                GUI_BLOCKBENCH_PATH.write_text(
                    json.dumps(
                        {
                            "meta": {"format_version": "5.0", "model_format": "free"},
                            "voxtart_palette": [
                                [0.2, 0.3, 0.4, 1.0],
                                [0.8, 0.7, 0.6, 1.0],
                            ],
                            "elements": [
                                {
                                    "name": "body",
                                    "from": [0, 0, 0],
                                    "to": [2, 2, 2],
                                    "color": 0,
                                },
                                {
                                    "name": "cap",
                                    "from": [0, 2, 0],
                                    "to": [2, 3, 2],
                                    "color": 1,
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                expected_blockbench = blockbench_formats.decode_blockbench(
                    GUI_BLOCKBENCH_PATH.read_bytes(),
                    grid_size=float(scene.voxtart_grid_size),
                )
                blockbench_import_result = bpy.ops.voxtart.import_blockbench(
                    filepath=str(GUI_BLOCKBENCH_PATH)
                )
                blockbench_object = bpy.context.active_object
                blockbench_asset = (
                    operators._load_asset(blockbench_object)
                    if blockbench_object is not None
                    and operators.ASSET_PROPERTY in blockbench_object
                    else None
                )
                blockbench_export_result = bpy.ops.voxtart.export_blockbench(
                    filepath=str(GUI_BLOCKBENCH_PATH),
                    units_per_voxel=float(scene.voxtart_blockbench_units_per_voxel),
                )
                blockbench_roundtrip = blockbench_formats.decode_blockbench(
                    GUI_BLOCKBENCH_PATH.read_bytes(),
                    grid_size=float(scene.voxtart_grid_size),
                )
                if (
                    "FINISHED" not in blockbench_import_result
                    or "FINISHED" not in blockbench_export_result
                    or blockbench_asset is None
                    or blockbench_asset.voxels != expected_blockbench.voxels
                    or blockbench_roundtrip.voxels != blockbench_asset.voxels
                    or blockbench_object.get("voxtart_import_format") != "BLOCKBENCH"
                ):
                    return _fail(
                        "Blockbench round-trip changed voxel data: "
                        f"{blockbench_import_result!r}, {blockbench_export_result!r}"
                    )
                STATE["blockbench_roundtrip"] = True
                GUI_MINECRAFT_PATH.parent.mkdir(parents=True, exist_ok=True)
                minecraft_source_volume = minecraft_formats.MinecraftVolume(
                    3,
                    2,
                    2,
                    {
                        (0, 0, 0): "minecraft:stone",
                        (1, 0, 0): "minecraft:stone",
                        (0, 1, 0): "minecraft:stone",
                        (1, 1, 0): "minecraft:stone",
                    },
                )
                minecraft_formats.encode_minecraft_file(
                    minecraft_source_volume,
                    GUI_MINECRAFT_PATH,
                    "SCHEM",
                )
                minecraft_import_result = bpy.ops.voxtart.import_minecraft(
                    filepath=str(GUI_MINECRAFT_PATH),
                    format="SCHEM",
                    grid_size=float(scene.voxtart_minecraft_grid_size),
                )
                minecraft_object = bpy.context.active_object
                minecraft_asset = (
                    operators._load_asset(minecraft_object)
                    if minecraft_object is not None
                    and operators.ASSET_PROPERTY in minecraft_object
                    else None
                )
                minecraft_export_result = bpy.ops.voxtart.export_minecraft(
                    filepath=str(GUI_MINECRAFT_PATH),
                    format="SCHEM",
                    default_block="minecraft:stone",
                )
                minecraft_roundtrip = minecraft_formats.decode_minecraft_file(
                    GUI_MINECRAFT_PATH,
                    format_hint="SCHEM",
                )
                if (
                    "FINISHED" not in minecraft_import_result
                    or "FINISHED" not in minecraft_export_result
                    or minecraft_asset is None
                    or len(minecraft_asset.voxels) != len(minecraft_roundtrip.blocks)
                    or not minecraft_roundtrip.blocks
                    or minecraft_object.get("voxtart_import_format") != "SCHEM"
                ):
                    return _fail(
                        "Minecraft round-trip changed block data: "
                        f"{minecraft_import_result!r}, {minecraft_export_result!r}"
                    )
                STATE["minecraft_roundtrip"] = True
                required_stack_document = stacking.StackDocument.from_layers(
                    (
                        stacking.StackLayer(
                            "Base",
                            2,
                            2,
                            (
                                (0.9, 0.2, 0.2, 1.0),
                                (0.9, 0.2, 0.2, 1.0),
                                (0.9, 0.2, 0.2, 1.0),
                                (0.9, 0.2, 0.2, 1.0),
                            ),
                        ),
                        stacking.StackLayer(
                            "Highlight",
                            2,
                            2,
                            (
                                (0.2, 0.8, 0.3, 1.0),
                                (0.2, 0.8, 0.3, 1.0),
                                (0.2, 0.8, 0.3, 1.0),
                                (0.2, 0.8, 0.3, 1.0),
                            ),
                            offset=(1, 0),
                            opacity=0.75,
                        ),
                    ),
                    canvas_size=(3, 2),
                    source_format="sprite-stack-manifest",
                )
                scene.voxtart_stack_axis = "Z"
                scene.voxtart_stack_order = "BOTTOM_TO_TOP"
                scene.voxtart_stack_alpha_threshold = 0.05
                scene.voxtart_stack_voxel_scale = 1.0
                scene.voxtart_stack_palette_limit = 16
                GUI_STACK_PATH.parent.mkdir(parents=True, exist_ok=True)
                GUI_STACK_PATH.write_text(
                    required_stack_document.encode() + "\n",
                    encoding="utf-8",
                )
                stack_import_result = bpy.ops.voxtart.import_stack(
                    filepath=str(GUI_STACK_PATH)
                )
                stack_object = bpy.context.active_object
                stack_document = (
                    stacking.StackDocument.decode(stack_object[stacking.STACK_PROPERTY])
                    if stack_object is not None
                    and isinstance(stack_object.get(stacking.STACK_PROPERTY), str)
                    else None
                )
                stack_asset = (
                    operators._load_asset(stack_object)
                    if stack_object is not None
                    and operators.ASSET_PROPERTY in stack_object
                    else None
                )
                expected_stack_asset = (
                    stacking.stack_to_voxel_asset(
                        stack_document,
                        axis=str(scene.voxtart_stack_axis),
                        layer_order=str(scene.voxtart_stack_order),
                        alpha_threshold=float(scene.voxtart_stack_alpha_threshold),
                        voxel_scale=float(scene.voxtart_stack_voxel_scale),
                        palette_limit=int(scene.voxtart_stack_palette_limit),
                    )
                    if stack_document is not None
                    else None
                )
                stack_export_result = bpy.ops.voxtart.export_stack_manifest(
                    filepath=str(GUI_STACK_PATH)
                )
                exported_stack_document = stacking.StackDocument.decode(
                    GUI_STACK_PATH.read_text(encoding="utf-8")
                )
                if (
                    "FINISHED" not in stack_import_result
                    or "FINISHED" not in stack_export_result
                    or stack_object is None
                    or stack_document is None
                    or stack_asset is None
                    or expected_stack_asset is None
                    or stack_asset.voxels != expected_stack_asset.voxels
                    or exported_stack_document.to_dict() != stack_document.to_dict()
                    or len(stack_document.layers) != 2
                    or not stack_object.get("voxtart_stack_mode")
                ):
                    return _fail(
                        "Layered stack round-trip changed document data: "
                        f"{stack_import_result!r}, {stack_export_result!r}"
                    )
                STATE["stack_roundtrip"] = True
                for interchange_object in set(desktop_shell._asset_objects()).difference(
                    before_interchange_assets
                ):
                    bpy.data.objects.remove(interchange_object, do_unlink=True)
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                native_object.select_set(True)
                bpy.context.view_layer.objects.active = native_object
                scene[voxel_editor.FIRST_RUN_HINT_PROPERTY] = False
                desktop_shell._set_mode(scene, "HOME")
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "mode:CHARACTER" for key, _bounds in shell_state.targets):
                    return _fail("Post-first-edit project surface did not expose Character Tools")
                shell_operator._activate(bpy.context, "mode:CHARACTER")
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "character:prepare" for key, _bounds in shell_state.targets):
                    return _fail("Character surface did not expose Prepare rig")
                if any(key == "character:toolbox" for key, _bounds in shell_state.targets):
                    return _fail("Character surface exposed a native Blender toolbox")
                if desktop_ui.native_chrome_visible(scene):
                    return _fail("Character surface exposed native Blender chrome")
                STATE["character_native_ui_blocked"] = True
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                native_object.select_set(True)
                bpy.context.view_layer.objects.active = native_object
                before_character_armatures = {
                    obj.name for obj in bpy.data.objects if obj.type == "ARMATURE"
                }
                shell_operator._activate(bpy.context, "character:prepare")
                character_armatures = [
                    obj
                    for obj in bpy.data.objects
                    if obj.type == "ARMATURE" and obj.name not in before_character_armatures
                ]
                if (
                    len(character_armatures) != 1
                    or not character_armatures[0].get("voxtart_generated_rig")
                    or native_object.get("voxtart_animation_armature") != character_armatures[0].name
                    or len(character_armatures[0].data.bones) < 10
                ):
                    return _fail("Prepare rig did not create and bind a blockout armature")
                STATE["character_prepare"] = True
                character_armature = character_armatures[0]
                if not any(key == "character:extract" for key, _bounds in shell_state.targets):
                    return _fail("Character surface did not expose Extract game rig")
                scene.voxtart_game_rig_bind_meshes = True
                before_game_rigs = {obj.name for obj in bpy.data.objects if obj.type == "ARMATURE"}
                shell_operator._activate(bpy.context, "character:extract")
                game_rigs = [
                    obj
                    for obj in bpy.data.objects
                    if obj.type == "ARMATURE" and obj.name not in before_game_rigs
                ]
                if (
                    len(game_rigs) != 1
                    or not game_rigs[0].get("voxtart_game_rig")
                    or game_rigs[0].get("voxtart_game_rig_source") != character_armature.name
                    or len(game_rigs[0].data.bones) < 5
                ):
                    return _fail("Extract game rig did not create a compact target armature")
                game_rig = game_rigs[0]
                required_character_keys = {
                    "character:import",
                    "character:bake",
                    "character:preview",
                    "character:export",
                }
                visible_character_keys = {key for key, _bounds in shell_state.targets}
                if not required_character_keys.issubset(visible_character_keys):
                    return _fail("Character surface did not expose animation retargeting controls")
                character_armature.animation_data_create()
                source_bone = character_armature.pose.bones.get("root")
                if source_bone is None:
                    return _fail("Blockout rig did not expose a root pose bone")
                source_bone.rotation_mode = "XYZ"
                scene.frame_set(1)
                source_bone.rotation_euler.z = 0.0
                source_bone.keyframe_insert(data_path="rotation_euler", frame=1)
                source_bone.rotation_euler.z = 0.35
                source_bone.keyframe_insert(data_path="rotation_euler", frame=5)
                source_action = character_armature.animation_data.action
                if source_action is None:
                    return _fail("Could not create a source animation action")
                source_action.name = "VOXTART GUI Walk"
                scene.voxtart_animation_source = character_armature.name
                scene.voxtart_animation_target = game_rig.name
                scene.voxtart_animation_action = source_action.name
                scene.voxtart_animation_bake_action_names = source_action.name
                scene.voxtart_animation_bake_all_actions = False
                scene.voxtart_animation_bake_prefix = "Game_"
                shell_operator._activate(bpy.context, "character:bake")
                baked_action = (
                    game_rig.animation_data.action
                    if game_rig.animation_data is not None
                    else None
                )
                if (
                    baked_action is None
                    or not baked_action.get("voxtart_game_rig_baked")
                    or baked_action.frame_range[1] < 5.0
                    or not game_rig.get("voxtart_game_rig_baked_actions")
                ):
                    return _fail("Native action bake did not create a game-rig action")
                STATE["character_bake"] = True
                scene.voxtart_animation_preview_action = baked_action.name
                shell_operator._activate(bpy.context, "character:preview")
                if (
                    game_rig.animation_data is None
                    or game_rig.animation_data.action != baked_action
                    or scene.voxtart_animation_preview_action != baked_action.name
                    or scene.frame_current != int(baked_action.frame_range[0])
                ):
                    return _fail("Game-rig preview did not assign the baked action")
                STATE["character_preview"] = True
                STATE["character_extract"] = True
                GUI_GAME_RIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                game_rig_export_result = bpy.ops.voxtart.export_game_rig(
                    filepath=str(GUI_GAME_RIG_PATH),
                    export_format="GLTF",
                    include_meshes=True,
                )
                if (
                    "FINISHED" not in game_rig_export_result
                    or not GUI_GAME_RIG_PATH.is_file()
                    or GUI_GAME_RIG_PATH.stat().st_size <= 0
                    or not game_rig.get("voxtart_game_rig_last_export")
                    or game_rig.get("voxtart_game_rig_last_export_format") != "GLTF"
                ):
                    return _fail(
                        f"Game rig export did not write its report and file: "
                        f"{game_rig_export_result!r}"
                    )
                STATE["character_export"] = True
                scene.voxtart_game_rig_bind_meshes = False
                scene.voxtart_animation_source = ""
                scene.voxtart_animation_target = ""
                native_object.parent = None
                for modifier in tuple(native_object.modifiers):
                    if modifier.type == "ARMATURE":
                        native_object.modifiers.remove(modifier)
                for property_name in ("voxtart_animation_armature",):
                    if property_name in native_object:
                        del native_object[property_name]
                for armature_object in (game_rig, character_armature):
                    armature_data = armature_object.data
                    bpy.data.objects.remove(armature_object, do_unlink=True)
                    if armature_data.users == 0:
                        bpy.data.armatures.remove(armature_data)
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                native_object.select_set(True)
                bpy.context.view_layer.objects.active = native_object
                scene.voxtart_blob_gallery_path = str(GUI_GALLERY_PATH)
                character_gallery = blob_gallery.BlobGallery(GUI_GALLERY_PATH)
                root_socket = VoxelSocket((0, 0, 0), (0, 0, 1), "mount")
                root_asset = VoxelAsset.box(1)
                root_asset.sockets = [root_socket]
                torso_socket = VoxelSocket((0, 0, 0), (0, 0, -1), "mount")
                torso_asset = VoxelAsset.box(1)
                torso_asset.sockets = [torso_socket]
                root_entry = blob_gallery.make_blob_entry(
                    root_asset,
                    "GUI Character Root",
                    blob_id="gui_character_root",
                    category="GUI Tests",
                    joints=(
                        blob_gallery.BlobJoint(
                            name="mount",
                            socket=root_socket,
                            role="mount",
                        ),
                    ),
                )
                torso_entry = blob_gallery.make_blob_entry(
                    torso_asset,
                    "GUI Character Torso",
                    blob_id="gui_character_torso",
                    category="GUI Tests",
                    joints=(
                        blob_gallery.BlobJoint(
                            name="mount",
                            socket=torso_socket,
                            role="mount",
                        ),
                    ),
                )
                character_gallery.save(root_entry)
                character_gallery.save(torso_entry)
                scene.voxtart_character_name = "GUI Character"
                scene.voxtart_character_slot = "ROOT"
                scene.voxtart_character_auto_snap = True
                desktop_shell._set_mode(scene, "CHARACTER")
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "character:rebuild" for key, _bounds in shell_state.targets):
                    return _fail("Character surface did not expose Rebuild character")
                root_result = bpy.ops.voxtart.character_assign_blob(
                    slot="ROOT",
                    blob_id=root_entry.blob_id,
                )
                torso_result = bpy.ops.voxtart.character_assign_blob(
                    slot="TORSO",
                    blob_id=torso_entry.blob_id,
                )
                shell_operator._activate(bpy.context, "character:rebuild")
                character_id = str(scene.get(character_editor_ui.CHARACTER_ID_PROPERTY, ""))
                recipe = character_editor_ui._load_recipe(scene)
                generated = {
                    str(obj.get(character_editor_ui.CHARACTER_SLOT_PROPERTY)): obj
                    for obj in bpy.data.objects
                    if obj.get(character_editor_ui.CHARACTER_OBJECT_ID_PROPERTY) == character_id
                }
                snap_record = json.loads(
                    generated["TORSO"].get("voxtart_character_snap", "{}")
                ) if "TORSO" in generated else {}
                if (
                    "FINISHED" not in root_result
                    or "FINISHED" not in torso_result
                    or scene.get(desktop_shell.SHELL_STATUS_PROPERTY) != "Character rebuilt"
                    or set(generated) != {"ROOT", "TORSO"}
                    or generated["TORSO"].parent != generated["ROOT"]
                    or recipe.get("ROOT") is None
                    or recipe.get("TORSO") is None
                    or recipe.get("TORSO").parent_slot != "ROOT"
                    or snap_record.get("target") != generated["ROOT"].name
                ):
                    return _fail(
                        f"Character editor assignment/rebuild failed: "
                        f"{root_result!r}, {torso_result!r}, {generated!r}"
                    )
                remove_result = bpy.ops.voxtart.character_remove_part(slot="TORSO")
                removed_recipe = character_editor_ui._load_recipe(scene)
                if (
                    "FINISHED" not in remove_result
                    or removed_recipe.get("TORSO") is not None
                    or any(
                        obj.get(character_editor_ui.CHARACTER_SLOT_PROPERTY) == "TORSO"
                        and obj.get(character_editor_ui.CHARACTER_OBJECT_ID_PROPERTY)
                        == character_id
                        for obj in bpy.data.objects
                    )
                ):
                    return _fail(f"Character editor remove failed: {remove_result!r}")
                reassign_result = bpy.ops.voxtart.character_assign_blob(
                    slot="TORSO",
                    blob_id=torso_entry.blob_id,
                )
                clear_result = bpy.ops.voxtart.character_clear()
                character_gallery.remove(root_entry.blob_id)
                character_gallery.remove(torso_entry.blob_id)
                if (
                    "FINISHED" not in reassign_result
                    or "FINISHED" not in clear_result
                    or character_editor_ui.CHARACTER_RECIPE_PROPERTY in scene
                    or character_editor_ui.CHARACTER_ID_PROPERTY in scene
                    or any(
                        obj.get(character_editor_ui.CHARACTER_OBJECT_ID_PROPERTY)
                        == character_id
                        for obj in bpy.data.objects
                    )
                ):
                    return _fail(
                        f"Character editor clear failed: {reassign_result!r}, {clear_result!r}"
                    )
                STATE["character_editor_roundtrip"] = True
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                native_object.select_set(True)
                bpy.context.view_layer.objects.active = native_object
                preferences = mcp._preferences()
                if preferences is None:
                    return _fail("VOXTART MCP preferences were not available")
                previous_allow_mcp_edits = bool(preferences.allow_mcp_edits)
                previous_mcp_port = int(preferences.mcp_port)
                preferences.allow_mcp_edits = False
                blocked_response = mcp._BRIDGE.dispatch_local(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "voxtart_create_box",
                            "arguments": {"size": 1, "name": "GUI MCP Blocked"},
                        },
                    }
                )
                port_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                port_socket.bind(("127.0.0.1", 0))
                mcp_port = int(port_socket.getsockname()[1])
                port_socket.close()
                preferences.mcp_port = mcp_port
                preferences.allow_mcp_edits = True
                start_result = bpy.ops.voxtart.mcp_start()
                unauthorized_status = None
                try:
                    request = urllib.request.Request(
                        mcp._BRIDGE.endpoint,
                        data=b"{}",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=2.0) as response:
                        unauthorized_status = int(response.status)
                except urllib.error.HTTPError as error:
                    unauthorized_status = int(error.code)
                initialize_response = mcp._BRIDGE.dispatch_local(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "initialize",
                        "params": {},
                    }
                )
                tools_response = mcp._BRIDGE.dispatch_local(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/list",
                        "params": {},
                    }
                )
                create_response = mcp._BRIDGE.dispatch_local(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "voxtart_create_box",
                            "arguments": {
                                "name": "GUI MCP Asset",
                                "size": 2,
                            },
                        },
                    }
                )
                create_structured = create_response.get("result", {}).get(
                    "structuredContent", {}
                )
                mcp_object_name = str(create_structured.get("object_name", ""))
                edit_response = mcp._BRIDGE.dispatch_local(
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {
                            "name": "voxtart_edit_asset",
                            "arguments": {
                                "object_name": mcp_object_name,
                                "operation": "add",
                                "cell": [2, 0, 0],
                                "palette_index": 0,
                            },
                        },
                    }
                )
                edit_structured = edit_response.get("result", {}).get(
                    "structuredContent", {}
                )
                asset_state_response = mcp._BRIDGE.dispatch_local(
                    {
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "tools/call",
                        "params": {
                            "name": "voxtart_asset_state",
                            "arguments": {
                                "object_name": mcp_object_name,
                                "include_voxels": True,
                            },
                        },
                    }
                )
                asset_state_structured = asset_state_response.get("result", {}).get(
                    "structuredContent", {}
                )
                rebuild_response = mcp._BRIDGE.dispatch_local(
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "tools/call",
                        "params": {
                            "name": "voxtart_rebuild_asset",
                            "arguments": {
                                "object_name": mcp_object_name,
                                "backend": "marching",
                            },
                        },
                    }
                )
                rebuild_structured = rebuild_response.get("result", {}).get(
                    "structuredContent", {}
                )
                if mcp_object_name in bpy.data.objects:
                    bpy.data.objects.remove(
                        bpy.data.objects[mcp_object_name],
                        do_unlink=True,
                    )
                stop_result = bpy.ops.voxtart.mcp_stop()
                preferences.allow_mcp_edits = previous_allow_mcp_edits
                preferences.mcp_port = previous_mcp_port
                blocked_result = blocked_response.get("result", {})
                initialize_result = initialize_response.get("result", {})
                tools_result = tools_response.get("result", {})
                if (
                    "FINISHED" not in start_result
                    or "FINISHED" not in stop_result
                    or unauthorized_status != 401
                    or not mcp._BRIDGE.token
                    or not blocked_result.get("isError")
                    or initialize_result.get("serverInfo", {}).get("name") != "voxtart"
                    or "voxtart_create_box"
                    not in {
                        tool.get("name")
                        for tool in tools_result.get("tools", [])
                        if isinstance(tool, dict)
                    }
                    or create_structured.get("voxel_count") != 8
                    or edit_structured.get("asset", {}).get("voxel_count") != 9
                    or asset_state_structured.get("voxel_count") != 9
                    or len(asset_state_structured.get("voxels", [])) != 9
                    or rebuild_structured.get("backend") != "marching"
                    or mcp_object_name in bpy.data.objects
                    or mcp._BRIDGE.running
                ):
                    return _fail(
                        f"MCP bridge/tool flow failed: "
                        f"{start_result!r}, {stop_result!r}, {unauthorized_status!r}"
                    )
                STATE["mcp_roundtrip"] = True
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                native_object.select_set(True)
                bpy.context.view_layer.objects.active = native_object
                scene.voxtart_blob_gallery_path = str(GUI_GALLERY_PATH)
                scene.voxtart_blob_name = "GUI Gallery Asset"
                scene.voxtart_blob_category = "GUI Tests"
                scene.voxtart_blob_tags = "gui,roundtrip"
                desktop_shell._set_mode(scene, "BUILD")
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "gallery:save" for key, _bounds in shell_state.targets):
                    return _fail("Project surface did not expose Save active to Zoo")
                native_gallery_asset = operators._load_asset(native_object)
                shell_operator._activate(bpy.context, "gallery:save")
                gallery = blob_gallery.BlobGallery(GUI_GALLERY_PATH)
                gallery_entries, gallery_errors = gallery.scan()
                if (
                    scene.get(desktop_shell.SHELL_STATUS_PROPERTY) != "Active asset saved to Zoo"
                    or len(gallery_entries) != 1
                    or gallery_errors
                ):
                    return _fail(
                        f"Blob Gallery save did not produce one valid entry: "
                        f"{gallery_entries!r}, {gallery_errors!r}"
                    )
                gallery_entry = gallery_entries[0]
                favorite_result = bpy.ops.voxtart.blob_gallery_toggle_favorite(
                    blob_id=gallery_entry.blob_id,
                    favorite=True,
                )
                favorite_entries, favorite_errors = gallery.scan(favorites_only=True)
                before_gallery_insert_assets = set(desktop_shell._asset_objects())
                insert_result = bpy.ops.voxtart.blob_gallery_insert(
                    blob_id=gallery_entry.blob_id,
                    snap_to_selection=False,
                )
                inserted_gallery_assets = set(desktop_shell._asset_objects()).difference(
                    before_gallery_insert_assets
                )
                inserted_gallery_object = (
                    next(iter(inserted_gallery_assets))
                    if len(inserted_gallery_assets) == 1
                    else None
                )
                inserted_gallery_asset = (
                    operators._load_asset(inserted_gallery_object)
                    if inserted_gallery_object is not None
                    else None
                )
                if (
                    "FINISHED" not in favorite_result
                    or "FINISHED" not in insert_result
                    or favorite_errors
                    or len(favorite_entries) != 1
                    or inserted_gallery_asset is None
                    or inserted_gallery_asset.voxels != native_gallery_asset.voxels
                    or inserted_gallery_object.get(blob_gallery.BLOB_ENTRY_PROPERTY)
                    != gallery_entry.blob_id
                ):
                    return _fail(
                        f"Blob Gallery favorite/insert failed: "
                        f"{favorite_result!r}, {insert_result!r}"
                    )
                STATE["gallery_roundtrip"] = True
                bpy.data.objects.remove(inserted_gallery_object, do_unlink=True)
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                native_object.select_set(True)
                bpy.context.view_layer.objects.active = native_object
                before_hierarchy_assets = set(desktop_shell._asset_objects())
                place_result = bpy.ops.voxtart.zoo_place_asset(
                    blob_id=gallery_entry.blob_id,
                    location=(3.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0),
                    scale=(1.0, 1.0, 1.0),
                )
                placed_assets = set(desktop_shell._asset_objects()).difference(
                    before_hierarchy_assets
                )
                placed_object = next(iter(placed_assets)) if len(placed_assets) == 1 else None
                assembly = (
                    scene_assembly.SceneAssembly.decode(scene[scene_assembly.ASSEMBLY_PROPERTY])
                    if isinstance(scene.get(scene_assembly.ASSEMBLY_PROPERTY), str)
                    else None
                )
                hierarchy_records = scene_hierarchy_ui._scene_hierarchy_records(bpy.context)
                hierarchy_root = scene_hierarchy.build_scene_hierarchy(
                    hierarchy_records,
                    include_unplaced=False,
                )
                hierarchy_rows = tuple(scene_hierarchy.flatten_hierarchy(hierarchy_root))
                placed_row = next(
                    (
                        node
                        for node, _depth in hierarchy_rows
                        if node.is_asset
                        and placed_object is not None
                        and node.object_name == placed_object.name
                    ),
                    None,
                )
                if (
                    "FINISHED" not in place_result
                    or placed_object is None
                    or assembly is None
                    or len(assembly.placements) != 1
                    or placed_row is None
                    or placed_row.category != "GUI Tests"
                ):
                    return _fail(
                        f"Scene hierarchy placement was not recorded: "
                        f"{place_result!r}, {assembly!r}"
                    )
                placed_object_name = placed_object.name
                placed_object.location.x = 7.0
                desktop_shell._set_mode(scene, "BUILD")
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "scene:sync" for key, _bounds in shell_state.targets):
                    return _fail("Project surface did not expose Sync scene hierarchy")
                shell_operator._activate(bpy.context, "scene:sync")
                synced_assembly = scene_assembly.SceneAssembly.decode(
                    scene[scene_assembly.ASSEMBLY_PROPERTY]
                )
                synced_placement = synced_assembly.placements[0]
                select_result = bpy.ops.voxtart.scene_hierarchy_select(
                    object_name=placed_object_name
                )
                selected_hierarchy_object = bpy.context.view_layer.objects.active == placed_object
                clear_result = bpy.ops.voxtart.zoo_clear_placements()
                if (
                    scene.get(desktop_shell.SHELL_STATUS_PROPERTY)
                    != "Scene hierarchy synchronized"
                    or abs(synced_placement.location[0] - 7.0) > 1.0e-6
                    or "FINISHED" not in select_result
                    or not selected_hierarchy_object
                    or "FINISHED" not in clear_result
                    or placed_object_name in bpy.data.objects
                    or scene_assembly.ASSEMBLY_PROPERTY in scene
                ):
                    return _fail(
                        f"Scene hierarchy sync/select/clear failed: "
                        f"{select_result!r}, {clear_result!r}"
                    )
                STATE["hierarchy_roundtrip"] = True
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                native_object.select_set(True)
                bpy.context.view_layer.objects.active = native_object
                before_kitbash_assets = set(desktop_shell._asset_objects())
                target_kitbash_object = operators._create_asset_object(
                    bpy.context,
                    "GUI Kitbash Target",
                    VoxelAsset.box(1),
                    backend="greedy",
                )
                target_kitbash_object.location = (5.0, 0.0, 0.0)
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                target_kitbash_object.select_set(True)
                bpy.context.view_layer.objects.active = target_kitbash_object
                target_socket_result = bpy.ops.voxtart.add_socket(
                    position=(0, 0, 0),
                    normal="NEG_X",
                    tag="mount",
                )
                source_kitbash_object = operators._create_asset_object(
                    bpy.context,
                    "GUI Kitbash Source",
                    VoxelAsset.box(1),
                    backend="greedy",
                )
                source_kitbash_object.location = (0.0, 0.0, 0.0)
                source_socket_result = bpy.ops.voxtart.add_socket(
                    position=(0, 0, 0),
                    normal="POS_X",
                    tag="mount",
                )
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                target_kitbash_object.select_set(True)
                source_kitbash_object.select_set(True)
                bpy.context.view_layer.objects.active = source_kitbash_object
                desktop_shell._set_mode(scene, "BUILD")
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "kitbash:snap" for key, _bounds in shell_state.targets):
                    return _fail("Project surface did not expose Snap active to target")
                shell_operator._activate(bpy.context, "kitbash:snap")
                source_kitbash_asset = operators._load_asset(source_kitbash_object)
                target_kitbash_asset = operators._load_asset(target_kitbash_object)
                snap_record = json.loads(
                    source_kitbash_object.get("voxtart_last_snap", "{}")
                )
                kitbash_source_name = source_kitbash_object.name
                kitbash_target_name = target_kitbash_object.name
                kitbash_assets = set(desktop_shell._asset_objects()).difference(
                    before_kitbash_assets
                )
                if (
                    len(kitbash_assets) != 2
                    or "FINISHED" not in target_socket_result
                    or "FINISHED" not in source_socket_result
                    or len(source_kitbash_asset.sockets) != 1
                    or len(target_kitbash_asset.sockets) != 1
                    or source_kitbash_asset.sockets[0].normal != (1, 0, 0)
                    or target_kitbash_asset.sockets[0].normal != (-1, 0, 0)
                    or scene.get(desktop_shell.SHELL_STATUS_PROPERTY)
                    != "Selected assets snapped"
                    or abs(source_kitbash_object.location.x - target_kitbash_object.location.x)
                    > 1.0e-6
                    or snap_record.get("target") != kitbash_target_name
                    or snap_record.get("source_socket") != 0
                    or snap_record.get("target_socket") != 0
                ):
                    return _fail(
                        f"Kitbash joint authoring/snap failed: "
                        f"{target_socket_result!r}, {source_socket_result!r}, {snap_record!r}"
                    )
                bpy.data.objects.remove(source_kitbash_object, do_unlink=True)
                bpy.data.objects.remove(target_kitbash_object, do_unlink=True)
                if (
                    kitbash_source_name in bpy.data.objects
                    or kitbash_target_name in bpy.data.objects
                ):
                    return _fail("Kitbash probe did not clean up its temporary assets")
                STATE["kitbash_roundtrip"] = True
                for selected in tuple(bpy.context.selected_objects):
                    selected.select_set(False)
                native_object.select_set(True)
                bpy.context.view_layer.objects.active = native_object
                desktop_shell._set_mode(scene, "BUILD")
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "palette:import" for key, _bounds in shell_state.targets):
                    return _fail("Project surface did not expose Import palette")
                if not any(key == "voxel:import" for key, _bounds in shell_state.targets):
                    return _fail("Project surface did not expose Import VOX / QB")
                if not any(key == "build:refresh" for key, _bounds in shell_state.targets):
                    return _fail("Build surface did not expose Refresh linked")
                if not any(key == "build:export" for key, _bounds in shell_state.targets):
                    return _fail("Build surface did not expose Export mesh")
                if not any(key == "build:editor" for key, _bounds in shell_state.targets):
                    return _fail("Build surface did not expose Open voxel editor")
                if any(key == "build:toolbox" for key, _bounds in shell_state.targets):
                    return _fail("Build surface exposed a native Blender toolbox")
                if desktop_ui.native_chrome_visible(scene):
                    return _fail("Build surface exposed native Blender chrome")
                STATE["shell_native_ui_blocked"] = True
                shell_operator._activate(bpy.context, "build:editor")
                if voxel_editor._ACTIVE_EDITOR is None:
                    return _fail("Open voxel editor did not enter the editor")
                STATE["shell_editor"] = True
                voxel_editor._ACTIVE_EDITOR._finish(bpy.context, {"CANCELLED"})
            desktop_shell.VOXTART_OT_desktop_shell._finish(None, bpy.context)
        STATE["view_workflow"] = _exercise_view_workflow(window, screen, area, region, scene)
        if not hasattr(bpy.types, "VOXTART_OT_voxel_editor"):
            return _fail("VOXTART voxel editor is not registered")
        existing = bpy.data.objects.get("P0 GUI Asset")
        if existing is not None:
            bpy.data.objects.remove(existing, do_unlink=True)
        palette_asset = VoxelAsset.box(2, grid_size=0.25)
        palette_asset.add_palette_color((0.24, 0.58, 0.96, 1.0))
        palette_asset.add_palette_color((0.94, 0.24, 0.28, 1.0))
        obj = operators._create_asset_object(
            bpy.context,
            "P0 GUI Asset",
            palette_asset,
        )
        for selected in tuple(bpy.context.selected_objects):
            selected.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        scene.voxtart_editor_mode = "ADD"
        scene.voxtart_editor_shape = "VOXEL"
        scene.voxtart_editor_radius = 1
        scene.voxtart_editor_depth = 1
        scene.voxtart_editor_mirror_x = False
        scene.voxtart_editor_mirror_y = False
        scene.voxtart_editor_mirror_z = False
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            bpy.ops.view3d.view_selected(use_all_regions=False)
            result = bpy.ops.voxtart.voxel_editor("INVOKE_DEFAULT")
            if "RUNNING_MODAL" not in result:
                return _fail(f"Voxel editor did not enter modal state: {result!r}")
        editor = voxel_editor._ACTIVE_EDITOR
        if editor is None:
            return _fail("Voxel editor did not become active")
        shading = area.spaces.active.shading
        if (
            getattr(shading, "type", None) != "SOLID"
            or getattr(shading, "color_type", None) != "MATERIAL"
            or getattr(shading, "light", None) != "FLAT"
        ):
            return _fail("VOXTART editor did not enable flat palette material shading")
        STATE["material_shading"] = True
        GUI_SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_result = bpy.ops.wm.save_as_mainfile(filepath=str(GUI_SAVE_PATH))
        if "FINISHED" not in save_result or not GUI_SAVE_PATH.is_file():
            return _fail(f"GUI save fixture did not save a .blend: {save_result!r}")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor._activate_topbar_action(bpy.context, "SAVE")
        if editor.status != "Project saved":
            return _fail(f"Product Save control reported an invalid status: {editor.status!r}")
        STATE["save_control"] = True

        if tuple(action for action, _bounds in voxel_editor._editor_topbar_action_bounds(region)) != (
            "UNDO",
            "REDO",
            "PROJECT",
            "SAVE",
            "HELP",
            "OPTIONS",
            "MENU",
            "DOCK_LEFT",
            "DOCK_RIGHT",
        ):
            return _fail("VOXTART editor did not expose the simple product controls")
        if scene.get(voxel_editor.EDITOR_DETAILS_PROPERTY) is not False:
            return _fail("VOXTART editor did not default to simple edit info")
        if scene.get(voxel_editor.EDITOR_HELP_PROPERTY) is not False:
            return _fail("VOXTART editor did not default to closed help")
        if scene.get(voxel_editor.EDITOR_OPTIONS_PROPERTY) is not False:
            return _fail("VOXTART editor did not default to closed extra controls")
        if scene.get(voxel_editor.EDITOR_DOCK_LEFT_PROPERTY) is not False:
            return _fail("VOXTART editor did not default to a closed left panel drawer")
        if scene.get(voxel_editor.EDITOR_DOCK_RIGHT_PROPERTY) is not True:
            return _fail("VOXTART editor did not default to an open right panel drawer")
        if scene.get(voxel_editor.EDITOR_GRID_PROPERTY) is not False:
            return _fail("VOXTART editor did not default to a hidden grid")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor._activate_topbar_action(bpy.context, "MENU")
            if scene.get(voxel_editor.EDITOR_MENU_PROPERTY) is not True:
                return _fail("VOXTART editor Menu control did not open the command menu")
            menu_panel, menu_entries, _menu_headings = voxel_editor._editor_menu_layout(region)
            menu_bounds = {
                action: bounds
                for action, _label, _icon, bounds in menu_entries
            }
            required_menu_actions = {
                "MENU_PROJECT",
                "MENU_SAVE",
                "MENU_UNDO",
                "MENU_REDO",
                "MENU_GRID",
                "MENU_DETAILS",
                "MENU_DOCK_LEFT",
                "MENU_DOCK_RIGHT",
                "MENU_MORE_TOOLS",
                "MENU_HELP",
            }
            if (
                menu_panel[2] <= menu_panel[0]
                or menu_panel[3] <= menu_panel[1]
                or not required_menu_actions.issubset(menu_bounds)
            ):
                return _fail("VOXTART editor Menu control did not expose grouped commands")
            menu_grid_bounds = menu_bounds["MENU_GRID"]
            menu_event = SimpleNamespace(
                type="LEFTMOUSE",
                value="PRESS",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=(menu_grid_bounds[0] + menu_grid_bounds[2]) * 0.5,
                mouse_region_y=(menu_grid_bounds[1] + menu_grid_bounds[3]) * 0.5,
            )
            if voxel_editor._editor_topbar_action_at(bpy.context, menu_event) != "MENU_GRID":
                return _fail("VOXTART editor Menu grid target was not clickable")
            menu_result = editor.modal(bpy.context, menu_event)
            if (
                "RUNNING_MODAL" not in menu_result
                or scene.get(voxel_editor.EDITOR_MENU_PROPERTY) is not False
                or scene.get(voxel_editor.EDITOR_GRID_PROPERTY) is not True
            ):
                return _fail("VOXTART editor Menu command did not activate the grid")
            editor._activate_topbar_action(bpy.context, "GRID")
            editor._activate_topbar_action(bpy.context, "MENU")
            MENU_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            area.tag_redraw()
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
            voxel_editor._draw_editor_overlay(editor, bpy.context)
            menu_screenshot_result = bpy.ops.screen.screenshot(filepath=str(MENU_SCREENSHOT))
            if "FINISHED" not in menu_screenshot_result or not MENU_SCREENSHOT.is_file():
                return _fail(f"Could not capture Menu screenshot: {menu_screenshot_result!r}")
            editor._activate_topbar_action(bpy.context, "MENU")
        STATE["menu_surface"] = True
        tool_layout = voxel_editor._editor_tool_options_layout(editor, region)
        tool_entries = {
            action: bounds
            for action, _label, bounds in tool_layout["entries"]
        }
        toolbar_panel, _toolbar_entries = voxel_editor._editor_toolbar_layout(
            region.height,
            scene,
        )
        right_docks = tuple(
            panel["bounds"][0]
            for panel in voxel_editor._editor_dock_layout(editor, region)["panels"].values()
            if panel.get("side") == "RIGHT"
        )
        left_docks = tuple(
            panel["bounds"][0]
            for panel in voxel_editor._editor_dock_layout(editor, region)["panels"].values()
            if panel.get("side") == "LEFT"
        )
        if (
            tool_layout["panel"][2] <= tool_layout["panel"][0]
            or tool_layout["panel"][0] <= toolbar_panel[2]
            or left_docks and toolbar_panel[2] >= min(left_docks)
            or right_docks and tool_layout["panel"][2] >= min(right_docks)
            or not {
                "TOOL_RADIUS_DOWN",
                "TOOL_RADIUS_UP",
                "TOOL_HOLLOW",
                "TOOL_MIRROR_X",
                "TOOL_MIRROR_Y",
                "TOOL_MIRROR_Z",
            }.issubset(tool_entries)
            or tool_layout["mode_item"][0] != "ADD"
            or tool_layout["shape_item"][0] != "VOXEL"
        ):
            return _fail("VOXTART tool options card did not expose a compact edit context")

        for action, title, shortcut in (
            ("TOOL_RADIUS_DOWN", "Smaller brush", "["),
            ("TOOL_RADIUS_UP", "Larger brush", "]"),
            ("TOOL_HOLLOW", "Hollow", "H"),
            ("TOOL_MIRROR_X", "Mirror X", "1"),
            ("TOOL_MIRROR_Y", "Mirror Y", "2"),
            ("TOOL_MIRROR_Z", "Mirror Z", "3"),
        ):
            hint = voxel_editor._editor_tool_option_hint(action)
            if (
                hint is None
                or hint[0] != title
                or not hint[1]
                or hint[2] != shortcut
            ):
                return _fail(f"VOXTART edit hint contract is incomplete: {action}")

        def _tool_event(action, event_type="LEFTMOUSE"):
            bounds = tool_entries[action]
            return SimpleNamespace(
                type=event_type,
                value="NOTHING" if event_type == "MOUSEMOVE" else "PRESS",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=(bounds[0] + bounds[2]) * 0.5,
                mouse_region_y=(bounds[1] + bounds[3]) * 0.5,
            )

        original_workspace = scene.voxtart_editor_workspace
        original_tool_state = (
            scene.voxtart_editor_mode,
            scene.voxtart_editor_shape,
            int(scene.voxtart_editor_radius),
            int(scene.voxtart_editor_depth),
            int(scene.voxtart_editor_height),
            bool(scene.voxtart_editor_hollow),
            bool(scene.voxtart_editor_mirror_x),
            bool(scene.voxtart_editor_mirror_y),
            bool(scene.voxtart_editor_mirror_z),
            scene.voxtart_editor_axis,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            tool_hover_result = editor.modal(
                bpy.context,
                _tool_event("TOOL_RADIUS_UP", "MOUSEMOVE"),
            )
        if (
            "RUNNING_MODAL" not in tool_hover_result
            or editor.tool_options_hover != "TOOL_RADIUS_UP"
        ):
            return _fail("VOXTART tool options card did not report hover feedback")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            voxel_editor._draw_editor_overlay(editor, bpy.context)
        radius_before = int(scene.voxtart_editor_radius)
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor.modal(bpy.context, _tool_event("TOOL_RADIUS_UP"))
        if (
            int(scene.voxtart_editor_radius) != radius_before + 1
            or editor.status != f"Brush radius {radius_before + 1}"
        ):
            return _fail("VOXTART tool options radius control did not update the brush")
        for action in ("TOOL_HOLLOW", "TOOL_MIRROR_X"):
            bounds = tool_entries[action]
            event = SimpleNamespace(
                type="LEFTMOUSE",
                value="PRESS",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=(bounds[0] + bounds[2]) * 0.5,
                mouse_region_y=(bounds[1] + bounds[3]) * 0.5,
            )
            with bpy.context.temp_override(
                window=window,
                screen=screen,
                area=area,
                region=region,
            ):
                editor.modal(bpy.context, event)
        if not scene.voxtart_editor_hollow or not scene.voxtart_editor_mirror_x:
            return _fail("VOXTART tool options toggles did not update scene settings")

        scene.voxtart_editor_shape = "FACE"
        face_layout = voxel_editor._editor_tool_options_layout(editor, region)
        face_entries = {
            action: bounds
            for action, _label, bounds in face_layout["entries"]
        }
        if not {"TOOL_DEPTH_DOWN", "TOOL_DEPTH_UP"}.issubset(face_entries):
            return _fail("VOXTART tool options did not expose face depth")
        for action, title in (
            ("TOOL_DEPTH_DOWN", "Shallower face"),
            ("TOOL_DEPTH_UP", "Deeper face"),
        ):
            hint = voxel_editor._editor_tool_option_hint(action)
            if hint is None or hint[0] != title or not hint[1] or hint[2] != "":
                return _fail(f"VOXTART face hint contract is incomplete: {action}")
        depth_before = int(scene.voxtart_editor_depth)
        depth_bounds = face_entries["TOOL_DEPTH_UP"]
        depth_event = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=(depth_bounds[0] + depth_bounds[2]) * 0.5,
            mouse_region_y=(depth_bounds[1] + depth_bounds[3]) * 0.5,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor.modal(bpy.context, depth_event)
        if int(scene.voxtart_editor_depth) != depth_before + 1:
            return _fail("VOXTART tool options face depth control did not update")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            voxel_editor._draw_editor_overlay(editor, bpy.context)

        scene.voxtart_editor_shape = "CYLINDER"
        cylinder_layout = voxel_editor._editor_tool_options_layout(editor, region)
        cylinder_entries = {
            action: bounds
            for action, _label, bounds in cylinder_layout["entries"]
        }
        if not {
            "TOOL_AXIS_X",
            "TOOL_AXIS_Y",
            "TOOL_AXIS_Z",
            "TOOL_HEIGHT_DOWN",
            "TOOL_HEIGHT_UP",
        }.issubset(cylinder_entries):
            return _fail("VOXTART tool options did not expose cylinder controls")
        for action, title in (
            ("TOOL_AXIS_X", "Axis X"),
            ("TOOL_AXIS_Y", "Axis Y"),
            ("TOOL_AXIS_Z", "Axis Z"),
            ("TOOL_HEIGHT_DOWN", "Shorter cylinder"),
            ("TOOL_HEIGHT_UP", "Taller cylinder"),
        ):
            hint = voxel_editor._editor_tool_option_hint(action)
            if hint is None or hint[0] != title or not hint[1] or hint[2] != "":
                return _fail(f"VOXTART cylinder hint contract is incomplete: {action}")
        axis_bounds = cylinder_entries["TOOL_AXIS_Y"]
        axis_event = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=(axis_bounds[0] + axis_bounds[2]) * 0.5,
            mouse_region_y=(axis_bounds[1] + axis_bounds[3]) * 0.5,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor.modal(bpy.context, axis_event)
        if scene.voxtart_editor_axis != "Y":
            return _fail("VOXTART tool options cylinder axis control did not update")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            voxel_editor._draw_editor_overlay(editor, bpy.context)
        original_selection = tuple(editor.selection)
        scene.voxtart_editor_mode = "SELECT"
        scene.voxtart_editor_shape = "VOXEL"
        select_layout = voxel_editor._editor_tool_options_layout(editor, region)
        select_entries = {
            action: bounds
            for action, _label, bounds in select_layout["entries"]
        }
        if (
            not {
                "TOOL_SELECT_ALL",
                "TOOL_CLEAR_SELECTION",
                "TOOL_COPY_SELECTION",
                "TOOL_CUT_SELECTION",
            }.issubset(select_entries)
            or {
                "TOOL_RADIUS_DOWN",
                "TOOL_RADIUS_UP",
                "TOOL_HOLLOW",
                "TOOL_MIRROR_X",
            }.intersection(select_entries)
            or select_layout["mode_item"][0] != "SELECT"
        ):
            return _fail("VOXTART selection context did not replace brush controls")

        for action, title, shortcut in (
            ("TOOL_SELECT_ALL", "All", "Ctrl+A"),
            ("TOOL_CLEAR_SELECTION", "Clear", "Ctrl+D"),
            ("TOOL_COPY_SELECTION", "Copy", "Ctrl+C"),
            ("TOOL_CUT_SELECTION", "Cut", "Ctrl+X"),
        ):
            hint = voxel_editor._editor_tool_option_hint(action)
            if (
                hint is None
                or hint[0] != title
                or not hint[1]
                or hint[2] != shortcut
            ):
                return _fail(f"VOXTART Select hint contract is incomplete: {action}")

        def _select_tool_event(action, event_type="LEFTMOUSE"):
            bounds = select_entries[action]
            return SimpleNamespace(
                type=event_type,
                value="NOTHING" if event_type == "MOUSEMOVE" else "PRESS",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=(bounds[0] + bounds[2]) * 0.5,
                mouse_region_y=(bounds[1] + bounds[3]) * 0.5,
            )

        def _select_tool_action(action, event_type="LEFTMOUSE"):
            with bpy.context.temp_override(
                window=window,
                screen=screen,
                area=area,
                region=region,
            ):
                return editor.modal(
                    bpy.context,
                    _select_tool_event(action, event_type),
                )

        select_hover_result = _select_tool_action("TOOL_SELECT_ALL", "MOUSEMOVE")
        if (
            "RUNNING_MODAL" not in select_hover_result
            or editor.tool_options_hover != "TOOL_SELECT_ALL"
        ):
            return _fail("VOXTART selection context did not report hover feedback")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            voxel_editor._draw_editor_overlay(editor, bpy.context)
        _select_tool_action("TOOL_SELECT_ALL")
        if len(editor.selection) != len(editor.asset.voxels):
            return _fail("VOXTART selection context Select all did not select the asset")
        _select_tool_action("TOOL_COPY_SELECTION")
        if not editor.status.startswith("Copied "):
            return _fail("VOXTART selection context Copy did not update the status")
        _select_tool_action("TOOL_CLEAR_SELECTION")
        if editor.selection:
            return _fail("VOXTART selection context Clear did not clear the selection")
        editor.selection = original_selection
        voxel_editor._store_selection(editor.obj, editor.selection)
        voxel_editor._restore_overlay(editor.obj, editor.asset)
        STATE["selection_tool_options"] = True
        STATE["tool_options_hints"] = True

        scene.voxtart_editor_workspace = "SCENE"
        scene_options_layout = voxel_editor._editor_tool_options_layout(editor, region)
        scene_entries = {
            action: bounds
            for action, _label, bounds in scene_options_layout["entries"]
        }
        if (
            scene_options_layout.get("workspace") != "SCENE"
            or scene_options_layout["panel"][2] <= scene_options_layout["panel"][0]
            or not {"SCENE_MOVE", "SCENE_ROTATE", "SCENE_SCALE"}.issubset(scene_entries)
            or {"SCENE_APPLY", "SCENE_CANCEL"}.intersection(scene_entries)
            or {"TOOL_RADIUS_DOWN", "TOOL_HOLLOW", "TOOL_MIRROR_X"}.intersection(scene_entries)
        ):
            return _fail("VOXTART Scene context did not expose transform actions")
        for action, title, shortcut in (
            ("SCENE_MOVE", "Move", "G"),
            ("SCENE_ROTATE", "Rotate", "R"),
            ("SCENE_SCALE", "Scale", "S"),
            ("SCENE_APPLY", "Apply", "Enter"),
            ("SCENE_CANCEL", "Cancel", "Esc"),
        ):
            hint = voxel_editor._editor_tool_option_hint(action)
            if (
                hint is None
                or hint[0] != title
                or not hint[1]
                or hint[2] != shortcut
            ):
                return _fail(f"VOXTART Scene hint contract is incomplete: {action}")
        STATE["scene_option_hints"] = True

        def _scene_tool_event(action, event_type="LEFTMOUSE"):
            current_layout = voxel_editor._editor_tool_options_layout(editor, region)
            current_entries = {
                current_action: bounds
                for current_action, _label, bounds in current_layout["entries"]
            }
            bounds = current_entries[action]
            return SimpleNamespace(
                type=event_type,
                value="NOTHING" if event_type == "MOUSEMOVE" else "PRESS",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=(bounds[0] + bounds[2]) * 0.5,
                mouse_region_y=(bounds[1] + bounds[3]) * 0.5,
            )

        def _scene_tool_action(action, event_type="LEFTMOUSE"):
            with bpy.context.temp_override(
                window=window,
                screen=screen,
                area=area,
                region=region,
            ):
                return editor.modal(
                    bpy.context,
                    _scene_tool_event(action, event_type),
                )

        scene_hover_result = _scene_tool_action("SCENE_MOVE", "MOUSEMOVE")
        if (
            "RUNNING_MODAL" not in scene_hover_result
            or editor.tool_options_hover != "SCENE_MOVE"
        ):
            return _fail("VOXTART Scene context did not report hover feedback")
        baseline_location = tuple(float(value) for value in editor.obj.location)
        _scene_tool_action("SCENE_MOVE")
        if not editor.scene_transform_active or editor.scene_transform_kind != "MOVE":
            return _fail("VOXTART Scene Move action did not begin a transform")
        arrow_event = SimpleNamespace(
            type="RIGHT_ARROW",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=0.0,
            mouse_region_y=0.0,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            arrow_result = editor.modal(bpy.context, arrow_event)
        if (
            "RUNNING_MODAL" not in arrow_result
            or not editor.obj.location[0] > baseline_location[0]
        ):
            return _fail("VOXTART Scene Move did not respond to an arrow")
        escape_event = SimpleNamespace(
            type="ESC",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=0.0,
            mouse_region_y=0.0,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor.modal(bpy.context, escape_event)
        if (
            editor.scene_transform_active
            or any(
                abs(current - original) > 1e-6
                for current, original in zip(editor.obj.location, baseline_location)
            )
        ):
            return _fail("VOXTART Scene transform did not cancel cleanly")
        for action, expected in (
            ("SCENE_ROTATE", "ROTATE"),
            ("SCENE_SCALE", "SCALE"),
        ):
            _scene_tool_action(action)
            if not editor.scene_transform_active or editor.scene_transform_kind != expected:
                return _fail(f"VOXTART {expected.title()} action did not begin a transform")
            with bpy.context.temp_override(
                window=window,
                screen=screen,
                area=area,
                region=region,
            ):
                editor.modal(bpy.context, escape_event)
            if editor.scene_transform_active:
                return _fail(f"VOXTART {expected.title()} action did not cancel")
        committed_baseline_location = tuple(float(value) for value in editor.obj.location)
        history_before_apply = editor.history_index
        _scene_tool_action("SCENE_MOVE")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            committed_arrow_result = editor.modal(bpy.context, arrow_event)
        committed_location = tuple(float(value) for value in editor.obj.location)
        active_scene_entries = {
            action: bounds
            for action, _label, bounds in voxel_editor._editor_tool_options_layout(
                editor,
                region,
            )["entries"]
        }
        if (
            "RUNNING_MODAL" not in committed_arrow_result
            or not committed_location[0] > committed_baseline_location[0]
            or not {"SCENE_APPLY", "SCENE_CANCEL"}.issubset(active_scene_entries)
        ):
            return _fail("VOXTART active Scene transform did not expose completion controls")
        apply_hover_result = _scene_tool_action("SCENE_APPLY", "MOUSEMOVE")
        if (
            "RUNNING_MODAL" not in apply_hover_result
            or editor.tool_options_hover != "SCENE_APPLY"
        ):
            return _fail("VOXTART Scene Apply did not report hover feedback")
        apply_result = _scene_tool_action("SCENE_APPLY")
        history_labels = tuple(
            str(getattr(snapshot, "label", ""))
            for snapshot in editor.history
        )
        if (
            "RUNNING_MODAL" not in apply_result
            or editor.scene_transform_active
            or any(
                abs(current - expected) > 1e-6
                for current, expected in zip(editor.obj.location, committed_location)
            )
            or editor.history_index != history_before_apply + 1
            or not history_labels
            or history_labels[-1] != "Moved asset"
            or not str(editor.status).startswith("Scene transform committed")
        ):
            return _fail("VOXTART Scene Apply did not commit the transform")
        cancel_baseline_location = tuple(float(value) for value in editor.obj.location)
        history_after_apply = editor.history_index
        _scene_tool_action("SCENE_MOVE")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            cancel_arrow_result = editor.modal(bpy.context, arrow_event)
        cancel_moved_location = tuple(float(value) for value in editor.obj.location)
        cancel_scene_entries = {
            action: bounds
            for action, _label, bounds in voxel_editor._editor_tool_options_layout(
                editor,
                region,
            )["entries"]
        }
        if (
            "RUNNING_MODAL" not in cancel_arrow_result
            or not cancel_moved_location[0] > cancel_baseline_location[0]
            or not {"SCENE_APPLY", "SCENE_CANCEL"}.issubset(cancel_scene_entries)
        ):
            return _fail("VOXTART active Scene transform controls were not stable")
        cancel_hover_result = _scene_tool_action("SCENE_CANCEL", "MOUSEMOVE")
        if (
            "RUNNING_MODAL" not in cancel_hover_result
            or editor.tool_options_hover != "SCENE_CANCEL"
        ):
            return _fail("VOXTART Scene Cancel did not report hover feedback")
        cancel_result = _scene_tool_action("SCENE_CANCEL")
        if (
            "RUNNING_MODAL" not in cancel_result
            or editor.scene_transform_active
            or any(
                abs(current - expected) > 1e-6
                for current, expected in zip(editor.obj.location, cancel_baseline_location)
            )
            or editor.history_index != history_after_apply
            or str(editor.status) != "Scene transform cancelled"
        ):
            return _fail("VOXTART Scene Cancel did not restore the snapshot")
        STATE["scene_completion_controls"] = True
        _scene_tool_action("SCENE_MOVE")
        _scene_tool_action("SCENE_APPLY", "MOUSEMOVE")
        STATE["scene_options"] = True
        editor.tool_options_hover = "SCENE_APPLY"
        SCENE_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            voxel_editor._draw_editor_overlay(editor, bpy.context)
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
            scene_screenshot_result = bpy.ops.screen.screenshot(
                filepath=str(SCENE_SCREENSHOT)
            )
        if "FINISHED" not in scene_screenshot_result or not SCENE_SCREENSHOT.is_file():
            return _fail(f"Could not capture Scene screenshot: {scene_screenshot_result!r}")
        screenshot_cancel_result = _scene_tool_action("SCENE_CANCEL")
        if "RUNNING_MODAL" not in screenshot_cancel_result or editor.scene_transform_active:
            return _fail("VOXTART Scene screenshot left an active transform")
        STATE["scene_options_screenshot"] = str(SCENE_SCREENSHOT)

        scene.voxtart_editor_workspace = original_workspace
        scene.voxtart_editor_mode = original_tool_state[0]
        scene.voxtart_editor_shape = original_tool_state[1]
        scene.voxtart_editor_radius = original_tool_state[2]
        scene.voxtart_editor_depth = original_tool_state[3]
        scene.voxtart_editor_height = original_tool_state[4]
        scene.voxtart_editor_hollow = original_tool_state[5]
        scene.voxtart_editor_mirror_x = original_tool_state[6]
        scene.voxtart_editor_mirror_y = original_tool_state[7]
        scene.voxtart_editor_mirror_z = original_tool_state[8]
        scene.voxtart_editor_axis = original_tool_state[9]
        editor.mirrors = (
            scene.voxtart_editor_mirror_x,
            scene.voxtart_editor_mirror_y,
            scene.voxtart_editor_mirror_z,
        )
        editor.tool_options_hover = None
        STATE["tool_options"] = True
        hud_layout = voxel_editor._editor_view_hud_layout(editor, region)
        hud_entries = {
            action: bounds
            for action, _label, bounds in hud_layout["entries"]
        }
        required_hud_actions = {
            "VIEW_FRONT",
            "VIEW_RIGHT",
            "VIEW_TOP",
            "VIEW_FRAME",
            "VIEW_PROJECTION",
        }
        palette_dock = voxel_editor._editor_dock_layout(editor, region)["panels"].get("PALETTE")
        if (
            hud_layout["panel"][2] <= hud_layout["panel"][0]
            or not required_hud_actions.issubset(hud_entries)
            or palette_dock is None
            or hud_layout["panel"][2] >= palette_dock["bounds"][0]
        ):
            return _fail("VOXTART viewport HUD did not expose a compact non-overlapping action panel")

        def _hud_event(action, event_type="LEFTMOUSE"):
            bounds = hud_entries[action]
            return SimpleNamespace(
                type=event_type,
                value="NOTHING" if event_type == "MOUSEMOVE" else "PRESS",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=(bounds[0] + bounds[2]) * 0.5,
                mouse_region_y=(bounds[1] + bounds[3]) * 0.5,
            )

        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            hud_hover_result = editor.modal(bpy.context, _hud_event("VIEW_FRONT", "MOUSEMOVE"))
        if (
            "RUNNING_MODAL" not in hud_hover_result
            or editor.view_hud_hover != "VIEW_FRONT"
        ):
            return _fail("VOXTART viewport HUD did not report hover feedback")

        for action, expected_status in (
            ("VIEW_FRONT", "Front view"),
            ("VIEW_RIGHT", "Right view"),
            ("VIEW_TOP", "Top view"),
            ("VIEW_FRAME", "View framed"),
        ):
            with bpy.context.temp_override(
                window=window,
                screen=screen,
                area=area,
                region=region,
            ):
                hud_result = editor.modal(bpy.context, _hud_event(action))
            if "RUNNING_MODAL" not in hud_result or editor.status != expected_status:
                return _fail(f"VOXTART viewport HUD action failed: {action} -> {editor.status!r}")

        projection_before = area.spaces.active.region_3d.view_perspective
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            projection_result = editor.modal(
                bpy.context,
                _hud_event("VIEW_PROJECTION"),
            )
        projection_after = area.spaces.active.region_3d.view_perspective
        if (
            "RUNNING_MODAL" not in projection_result
            or projection_after == projection_before
            or editor.status not in {"Orthographic view", "Perspective view"}
        ):
            return _fail("VOXTART viewport HUD projection toggle did not change the view")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor.modal(bpy.context, _hud_event("VIEW_PROJECTION"))
        if area.spaces.active.region_3d.view_perspective != projection_before:
            return _fail("VOXTART viewport HUD projection toggle did not restore the view")
        STATE["viewport_hud"] = True

        seam_name = editor.obj.get(operators.SEAM_OVERLAY_PROPERTY)
        seam_overlay = bpy.data.objects.get(seam_name) if isinstance(seam_name, str) else None
        if seam_overlay is None or seam_overlay.hide_viewport:
            return _fail("VOXTART editor did not show voxel outlines in simple view")

        palette_panel, palette_entries = voxel_editor._editor_palette_layout(editor, region)
        if len(palette_entries) < 3 or len(palette_entries) != len(editor.asset.palette):
            return _fail("VOXTART editor did not expose its palette swatches")
        palette_index, palette_bounds = palette_entries[1]
        palette_event = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=(palette_bounds[0] + palette_bounds[2]) * 0.5,
            mouse_region_y=(palette_bounds[1] + palette_bounds[3]) * 0.5,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            palette_result = editor.modal(bpy.context, palette_event)
        if "RUNNING_MODAL" not in palette_result or scene.voxtart_edit_palette_index != palette_index:
            return _fail("VOXTART editor palette swatch click did not select a non-default color")
        metadata = voxel_editor._editor_palette_metadata(editor, palette_index)
        if not metadata[0].startswith("#") or metadata[1] != "Diffuse":
            return _fail(f"VOXTART palette swatch metadata is incomplete: {metadata!r}")
        favorite_event = SimpleNamespace(
            type="RIGHTMOUSE",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=(palette_bounds[0] + palette_bounds[2]) * 0.5,
            mouse_region_y=(palette_bounds[1] + palette_bounds[3]) * 0.5,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            favorite_result = editor.modal(bpy.context, favorite_event)
        favorite_indices = voxel_editor._editor_palette_favorite_indices(editor)
        favorite_panel, favorite_entries = voxel_editor._editor_palette_favorite_layout(editor, region)
        favorite_bounds = next(
            (bounds for index, bounds in favorite_entries if index == palette_index),
            None,
        )
        if (
            "RUNNING_MODAL" not in favorite_result
            or favorite_indices != (palette_index,)
            or favorite_bounds is None
            or "pinned" not in editor.status
        ):
            return _fail("VOXTART palette right-click did not persist a pinned swatch")
        favorite_select_event = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=(favorite_bounds[0] + favorite_bounds[2]) * 0.5,
            mouse_region_y=(favorite_bounds[1] + favorite_bounds[3]) * 0.5,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            favorite_select_result = editor.modal(bpy.context, favorite_select_event)
        if (
            "RUNNING_MODAL" not in favorite_select_result
            or scene.voxtart_edit_palette_index != palette_index
            or favorite_panel[2] <= favorite_panel[0]
        ):
            return _fail("VOXTART pinned swatch was not a selectable palette shortcut")
        STATE["palette_favorite"] = True

        STATE["palette_selection"] = True
        STATE["palette_non_default"] = True
        wheel_geometry = voxel_editor._editor_color_wheel_geometry(editor, region)
        if wheel_geometry is None:
            return _fail("VOXTART editor did not expose its color wheel")
        before_wheel_color = tuple(editor.asset.palette[palette_index])
        wheel_target = wheel_geometry["triangle"][0]
        wheel_press = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=wheel_target[0],
            mouse_region_y=wheel_target[1],
        )
        wheel_release = SimpleNamespace(
            type="LEFTMOUSE",
            value="RELEASE",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=wheel_target[0],
            mouse_region_y=wheel_target[1],
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            wheel_press_result = editor.modal(bpy.context, wheel_press)
            editor.modal(bpy.context, wheel_release)
        if (
            "RUNNING_MODAL" not in wheel_press_result
            or tuple(editor.asset.palette[palette_index]) == before_wheel_color
            or editor.color_wheel_dragging
        ):
            return _fail("VOXTART editor color wheel did not update the active palette color")
        STATE["color_wheel"] = True
        if palette_index not in voxel_editor._editor_palette_favorite_indices(editor):
            return _fail("VOXTART pinned palette metadata was lost while editing its color")
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor._activate_topbar_action(bpy.context, "DOCK_LEFT")
            if scene.get(voxel_editor.EDITOR_DOCK_LEFT_PROPERTY) is not True:
                return _fail("VOXTART editor Left drawer control did not open the left drawer")
            dock_layout = voxel_editor._editor_dock_layout(editor, region)
            if not {"LAYERS", "HISTORY"}.issubset(dock_layout["panels"]):
                return _fail("VOXTART editor did not render the left layer and history panels")
            editor._activate_topbar_action(bpy.context, "DOCK_RIGHT")
            if voxel_editor._editor_palette_layout(editor, region)[1]:
                return _fail("VOXTART editor Right drawer control did not hide the palette")
            editor._activate_topbar_action(bpy.context, "DOCK_RIGHT")
            if scene.get(voxel_editor.EDITOR_DOCK_RIGHT_PROPERTY) is not True:
                return _fail("VOXTART editor Right drawer control did not reopen the palette")
        dock_layout = voxel_editor._editor_dock_layout(editor, region)
        layer_bounds = dock_layout["panels"]["LAYERS"]["bounds"]
        header_x = (layer_bounds[0] + layer_bounds[2]) * 0.5
        header_y = layer_bounds[3] - 18.0
        dock_press = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=header_x,
            mouse_region_y=header_y,
        )
        dock_move = SimpleNamespace(
            type="MOUSEMOVE",
            value="NOTHING",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=float(region.width) - 40.0,
            mouse_region_y=header_y,
        )
        dock_release = SimpleNamespace(
            type="LEFTMOUSE",
            value="RELEASE",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=float(region.width) - 40.0,
            mouse_region_y=header_y,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor.modal(bpy.context, dock_press)
            editor.modal(bpy.context, dock_move)
            dock_release_result = editor.modal(bpy.context, dock_release)
        if (
            "RUNNING_MODAL" not in dock_release_result
            or editor.dock_panel_sides.get("LAYERS") != "RIGHT"
        ):
            return _fail("VOXTART editor panel drag did not snap Layers to the right drawer")
        STATE["dock_drawers"] = True
        STATE["dock_snap"] = True
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor._activate_topbar_action(bpy.context, "HELP")
            if scene.get(voxel_editor.EDITOR_HELP_PROPERTY) is not True:
                return _fail("VOXTART editor Help control did not open the guide")
            editor._activate_topbar_action(bpy.context, "HELP")
            if scene.get(voxel_editor.EDITOR_HELP_PROPERTY) is not False:
                return _fail("VOXTART editor Help control did not close the guide")
        STATE["help_toggle"] = True
        mcp.set_chat_input_focus(False)
        grid_event = SimpleNamespace(
            type="G",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=-1.0,
            mouse_region_y=-1.0,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            grid_on_result = editor.modal(bpy.context, grid_event)
            grid_on_visible = scene.get(voxel_editor.EDITOR_GRID_PROPERTY) is True
            grid_off_result = editor.modal(bpy.context, grid_event)
            grid_off_hidden = scene.get(voxel_editor.EDITOR_GRID_PROPERTY) is False
        if "RUNNING_MODAL" not in grid_on_result or not grid_on_visible:
            return _fail("VOXTART editor G shortcut did not show the grid")
        if "RUNNING_MODAL" not in grid_off_result or not grid_off_hidden:
            return _fail("VOXTART editor G shortcut did not hide the grid")
        STATE["grid_shortcut"] = True
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor._activate_topbar_action(bpy.context, "OPTIONS")
            if scene.get(voxel_editor.EDITOR_OPTIONS_PROPERTY) is not True:
                return _fail("VOXTART editor Options control did not open extra controls")
            option_entries = dict(voxel_editor._editor_options_layout(region)[1])
            grid_bounds = option_entries.get("OPTIONS_GRID")
            if grid_bounds is None:
                return _fail("VOXTART editor Options menu did not expose the grid toggle")
            grid_event = SimpleNamespace(
                type="LEFTMOUSE",
                value="PRESS",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=(grid_bounds[0] + grid_bounds[2]) * 0.5,
                mouse_region_y=(grid_bounds[1] + grid_bounds[3]) * 0.5,
            )
            if voxel_editor._editor_topbar_action_at(bpy.context, grid_event) != "OPTIONS_GRID":
                return _fail("VOXTART editor Options menu grid target was not clickable")
            editor.modal(bpy.context, grid_event)
            overlay = area.spaces.active.overlay
            grid_interior, grid_border = voxel_editor._editor_grid_vertices(editor)
            grid_vertices = (*grid_interior, *grid_border)
            grid_min, grid_max, grid_size = voxel_editor._editor_canvas_bounds(editor)
            grid_spans = tuple(
                max(float(vertex[axis]) for vertex in grid_vertices)
                - min(float(vertex[axis]) for vertex in grid_vertices)
                for axis in range(3)
            )
            expected_grid_spans = tuple(
                (grid_max[axis] - grid_min[axis]) * grid_size
                for axis in range(3)
            )
            if (
                scene.get(voxel_editor.EDITOR_GRID_PROPERTY) is not True
                or len(grid_border) != 24
                or not grid_interior
                or any(span <= 0.0 for span in grid_spans)
                or any(
                    abs(grid_spans[axis] - expected_grid_spans[axis]) > 1.0e-9
                    for axis in range(3)
                )
                or overlay.show_floor
                or overlay.show_axis_x
                or overlay.show_axis_y
            ):
                return _fail("VOXTART editor grid did not follow voxel-sized box spacing")
            editor._activate_topbar_action(bpy.context, "OPTIONS")
            option_entries = dict(voxel_editor._editor_options_layout(region)[1])
            grid_bounds = option_entries.get("OPTIONS_GRID")
            grid_event.mouse_region_x = (grid_bounds[0] + grid_bounds[2]) * 0.5
            grid_event.mouse_region_y = (grid_bounds[1] + grid_bounds[3]) * 0.5
            editor.modal(bpy.context, grid_event)
            if (
                scene.get(voxel_editor.EDITOR_GRID_PROPERTY) is not False
                or overlay.show_floor
                or overlay.show_axis_x
                or overlay.show_axis_y
            ):
                return _fail("VOXTART editor grid toggle did not hide the viewport grid")
            STATE["grid_toggle"] = True
            editor._activate_topbar_action(bpy.context, "OPTIONS")
            option_entries = dict(voxel_editor._editor_options_layout(region)[1])
            if scene.get(voxel_editor.EDITOR_OPTIONS_PROPERTY) is not True:
                return _fail("VOXTART editor Options menu did not reopen after the grid toggle")
            STATE["grid_toggle"] = True
            details_bounds = option_entries.get("OPTIONS_DETAILS")
            if details_bounds is None:
                return _fail("VOXTART editor Options menu did not expose edit info")
            details_event = SimpleNamespace(
                type="LEFTMOUSE",
                value="PRESS",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=(details_bounds[0] + details_bounds[2]) * 0.5,
                mouse_region_y=(details_bounds[1] + details_bounds[3]) * 0.5,
            )
            if voxel_editor._editor_topbar_action_at(bpy.context, details_event) != "OPTIONS_DETAILS":
                return _fail("VOXTART editor Options menu edit info target was not clickable")
            editor.modal(bpy.context, details_event)
            if scene.get(voxel_editor.EDITOR_OPTIONS_PROPERTY) is not False:
                return _fail("VOXTART editor edit info did not close extra controls")
            if scene.get(voxel_editor.EDITOR_DETAILS_PROPERTY) is not True:
                return _fail("VOXTART editor edit info did not reveal diagnostics")
            if seam_overlay.hide_viewport:
                return _fail("VOXTART editor edit info hid the voxel outlines")
            editor._activate_topbar_action(bpy.context, "DETAILS")
            if scene.get(voxel_editor.EDITOR_DETAILS_PROPERTY) is not False:
                return _fail("VOXTART editor edit info did not hide diagnostics")
            if seam_overlay.hide_viewport:
                return _fail("VOXTART editor edit info hid the voxel outlines")
            overlay = area.spaces.active.overlay
            editor._activate_topbar_action(bpy.context, "GRID")
            if (
                scene.get(voxel_editor.EDITOR_GRID_PROPERTY) is not True
                or overlay.show_floor
                or overlay.show_axis_x
                or overlay.show_axis_y
            ):
                return _fail("VOXTART grid did not reopen from the simple editor")
            editor._activate_topbar_action(bpy.context, "MORE_TOOLS")
            if (
                desktop_ui.native_chrome_visible(scene)
                or overlay.show_floor
                or overlay.show_axis_x
                or overlay.show_axis_y
                or voxel_editor.visible_editor_toolbar_items(scene)
                is not voxel_editor.EDITOR_TOOLBAR_ITEMS
            ):
                return _fail("VOXTART advanced tools did not preserve its hidden native canvas")
            editor._activate_topbar_action(bpy.context, "MORE_TOOLS")
            if (
                desktop_ui.native_chrome_visible(scene)
                or overlay.show_floor
                or overlay.show_axis_x
                or overlay.show_axis_y
                or voxel_editor.visible_editor_toolbar_items(scene)
                is not voxel_editor.BEGINNER_TOOLBAR_ITEMS
            ):
                return _fail("VOXTART simple tools did not preserve its hidden native canvas")
            editor._activate_topbar_action(bpy.context, "GRID")
            if (
                scene.get(voxel_editor.EDITOR_GRID_PROPERTY) is not False
                or overlay.show_floor
                or overlay.show_axis_x
                or overlay.show_axis_y
            ):
                return _fail("VOXTART grid did not close after advanced tools testing")
        STATE["details_toggle"] = True
        STATE["options_menu"] = True
        STATE["product_controls"] = True
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor._activate_topbar_action(bpy.context, "PROJECT")
        if desktop_shell._ACTIVE_STATE is None:
            return _fail("Project control did not open the VOXTART project surface")
        desktop_shell.VOXTART_OT_desktop_shell._finish(None, bpy.context)
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            result = bpy.ops.voxtart.voxel_editor("INVOKE_DEFAULT")
            if "RUNNING_MODAL" not in result:
                return _fail(f"Project control did not return to the voxel editor: {result!r}")
        editor = voxel_editor._ACTIVE_EDITOR
        if editor is None:
            return _fail("Project control returned without an active voxel editor")
        STATE["project_control"] = True
        mcp.set_chat_input_focus(False)
        escape_event = SimpleNamespace(
            type="ESC",
            value="PRESS",
            ctrl=False,
            shift=False,
            alt=False,
            mouse_region_x=-1.0,
            mouse_region_y=-1.0,
        )
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            escape_result = editor.modal(bpy.context, escape_event)
        if (
            "RUNNING_MODAL" not in escape_result
            or desktop_shell._ACTIVE_STATE is not None
            or voxel_editor._ACTIVE_EDITOR is not editor
            or editor.status != "VOXTART stays open"
        ):
            return _fail("Escape closed or replaced the VOXTART editor UI")
        STATE["escape_keeps_ui"] = True
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor._activate_topbar_action(bpy.context, "OPTIONS")
            option_entries = dict(voxel_editor._editor_options_layout(region)[1])
            more_bounds = option_entries.get("OPTIONS_MORE_TOOLS")
            if more_bounds is None:
                return _fail("VOXTART editor Options menu did not expose More tools")
            more_event = SimpleNamespace(
                type="LEFTMOUSE",
                value="PRESS",
                ctrl=False,
                shift=False,
                alt=False,
                mouse_region_x=(more_bounds[0] + more_bounds[2]) * 0.5,
                mouse_region_y=(more_bounds[1] + more_bounds[3]) * 0.5,
            )
            if voxel_editor._editor_topbar_action_at(bpy.context, more_event) != "OPTIONS_MORE_TOOLS":
                return _fail("VOXTART editor Options menu More tools target was not clickable")
            editor.modal(bpy.context, more_event)
            if desktop_ui.native_chrome_visible(scene):
                return _fail("More tools exposed Blender's native chrome")
            if voxel_editor.visible_editor_toolbar_items(scene) is not voxel_editor.EDITOR_TOOLBAR_ITEMS:
                return _fail("More tools did not reveal the advanced editor toolbar")
            editor._activate_topbar_action(bpy.context, "OPTIONS")
            option_entries = dict(voxel_editor._editor_options_layout(region)[1])
            more_bounds = option_entries.get("OPTIONS_MORE_TOOLS")
            more_event.mouse_region_x = (more_bounds[0] + more_bounds[2]) * 0.5
            more_event.mouse_region_y = (more_bounds[1] + more_bounds[3]) * 0.5
            editor.modal(bpy.context, more_event)
            if desktop_ui.native_chrome_visible(scene):
                return _fail("More tools exposed Blender's native chrome")
            if voxel_editor.visible_editor_toolbar_items(scene) is not voxel_editor.BEGINNER_TOOLBAR_ITEMS:
                return _fail("More tools did not restore the beginner editor toolbar")
        STATE["advanced_tools_toggle"] = True
        STATE["native_ui_lock"] = not desktop_ui.native_chrome_visible(scene)
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        left_point, right_point, left_cell, right_cell = _viewport_hits(editor, obj, area, region)
        STATE.update(
            {
                "window": window,
                "screen": screen,
                "area": area,
                "region": region,
                "editor": editor,
                "scene": scene,
                "obj": obj,
                "left_point": left_point,
                "right_point": right_point,
                "left_cell": left_cell,
                "right_cell": right_cell,
                "baseline_voxels": set(operators._load_asset(obj).voxels),
            }
        )
        bpy.app.timers.register(_send_left_click, first_interval=0.5)
        return None
    except Exception as error:
        return _fail(f"{type(error).__name__}: {error}")


bpy.app.timers.register(_probe, first_interval=3.5)
