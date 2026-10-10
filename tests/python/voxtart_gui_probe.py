# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Drive the real Blender viewport and capture the VOXTART editor overlay."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "scripts" / "addons_core"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from voxtart import desktop_shell, desktop_ui, operators, optimizer_ui, voxel_editor  # noqa: E402
from voxtart.voxel import VoxelAsset  # noqa: E402


MARKER = Path(os.environ.get("VOXTART_GUI_MARKER", "voxtart_gui_probe.json"))
SCREENSHOT = Path(os.environ.get("VOXTART_GUI_SCREENSHOT", "voxtart_gui_probe.png"))
SHELL_SCREENSHOT = Path(
    os.environ.get(
        "VOXTART_GUI_SHELL_SCREENSHOT",
        str(SCREENSHOT.with_name(f"{SCREENSHOT.stem}_shell{SCREENSHOT.suffix}")),
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
        scene.voxtart_editor_mode = "PAINT"
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
        paint_point = next(
            (
                point
                for point, cell in (
                    (paint_hits[0], paint_hits[2]),
                    (paint_hits[1], paint_hits[3]),
                )
                if before_paint_voxels.get(cell) == 0
            ),
            None,
        )
        if paint_point is None:
            return _fail("Paint workflow could not find a visible default-color voxel")
        _modal_click(paint_point)
        painted_asset = operators._load_asset(STATE["obj"])
        painted_cells = [
            cell
            for cell, palette_index in before_paint_voxels.items()
            if painted_asset.voxels.get(cell) != palette_index
        ]
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
        with bpy.context.temp_override(
            window=STATE["window"],
            screen=STATE["screen"],
            area=STATE["area"],
            region=STATE["region"],
        ):
            screenshot_result = bpy.ops.screen.screenshot(filepath=str(SCREENSHOT))
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=10)
            screenshot_result = bpy.ops.screen.screenshot(filepath=str(SCREENSHOT))
        if "FINISHED" not in screenshot_result or not SCREENSHOT.is_file():
            return _fail(f"Could not capture GUI screenshot: {screenshot_result!r}")
        _write(
            {
                "ok": True,
                "operator_running_modal": True,
                "draw_overlay_handle": editor._draw_handle is not None,
                "draw_preview_handle": editor._preview_handle is not None,
                "view_workflow": STATE.get("view_workflow", False),
                "shell_toolbar": STATE.get("shell_toolbar", False),
                "shell_screenshot": STATE.get("shell_screenshot", ""),
                "shell_make": STATE.get("shell_make", False),
                "shell_editor": STATE.get("shell_editor", False),
                "shell_voxelize": STATE.get("shell_voxelize", False),
                "shell_refresh": STATE.get("shell_refresh", False),
                "shell_optimize": STATE.get("shell_optimize", False),
                "portable_roundtrip": STATE.get("portable_roundtrip", False),
                "mesh_export": STATE.get("mesh_export", False),
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
                "more_tools_escape_hatch": STATE.get("more_tools_escape_hatch", False),
                "region_info": STATE.get("region_info", []),
                "details_toggle": STATE.get("details_toggle", False),
                "native_region_props": {
                    name: bool(getattr(STATE["area"].spaces.active, name, False))
                    for name in (
                        "show_region_toolbar",
                        "show_region_ui",
                        "show_region_header",
                        "show_region_footer",
                    )
                },
                "paint_undo_redo": STATE.get("paint_undo_redo", False),
                "palette_non_default": STATE.get("palette_non_default", False),
                "palette_selection": STATE.get("palette_selection", False),
                "product_controls": STATE.get("product_controls", False),
                "project_control": STATE.get("project_control", False),
                "escape_project": STATE.get("escape_project", False),
                "shape": scene.voxtart_editor_shape,
                "preview_count": len(editor.preview_cells),
                "area": [STATE["area"].width, STATE["area"].height],
                "screenshot": str(SCREENSHOT),
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
        if not STATE["native_chrome_hidden"]:
            return _fail("VOXTART startup left Blender chrome visible")
        if screen.show_statusbar or any(
            bool(getattr(area.spaces.active, property_name, False))
            for property_name in (
                "show_region_toolbar",
                "show_region_ui",
                "show_region_header",
                "show_region_footer",
            )
        ):
            return _fail("VOXTART startup left a Blender native region visible")
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
            desktop_shell._draw_shell(shell_state, bpy.context)
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
                desktop_shell._set_mode(scene, "BUILD")
                desktop_shell._draw_shell(shell_state, bpy.context)
                if not any(key == "build:refresh" for key, _bounds in shell_state.targets):
                    return _fail("Build surface did not expose Refresh linked")
                if not any(key == "build:export" for key, _bounds in shell_state.targets):
                    return _fail("Build surface did not expose Export mesh")
                if not any(key == "build:editor" for key, _bounds in shell_state.targets):
                    return _fail("Build surface did not expose Open voxel editor")
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
        palette_asset = VoxelAsset.box(2)
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
            "PROJECT",
            "SAVE",
            "DETAILS",
            "MORE_TOOLS",
        ):
            return _fail("VOXTART editor did not expose product-native project controls")
        if scene.get(voxel_editor.EDITOR_DETAILS_PROPERTY) is not False:
            return _fail("VOXTART editor did not default to simple diagnostics")
        palette_panel, palette_entries = voxel_editor._editor_palette_layout(editor, region)
        if len(palette_entries) < 3 or len(palette_entries) != len(editor.asset.palette):
            return _fail("VOXTART editor did not expose its palette swatches")
        editor._activate_palette(1)
        if scene.voxtart_edit_palette_index != 1:
            return _fail("VOXTART editor palette swatch did not select a non-default color")
        STATE["palette_selection"] = True
        STATE["palette_non_default"] = True
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor._activate_topbar_action(bpy.context, "DETAILS")
            if scene.get(voxel_editor.EDITOR_DETAILS_PROPERTY) is not True:
                return _fail("VOXTART editor Details control did not reveal diagnostics")
            editor._activate_topbar_action(bpy.context, "DETAILS")
            if scene.get(voxel_editor.EDITOR_DETAILS_PROPERTY) is not False:
                return _fail("VOXTART editor Details control did not hide diagnostics")
        STATE["details_toggle"] = True
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
            editor.modal(bpy.context, escape_event)
        if desktop_shell._ACTIVE_STATE is None:
            return _fail("Escape did not return to the VOXTART project surface")
        desktop_shell.VOXTART_OT_desktop_shell._finish(None, bpy.context)
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            result = bpy.ops.voxtart.voxel_editor("INVOKE_DEFAULT")
            if "RUNNING_MODAL" not in result:
                return _fail(f"Escape transition did not reopen the voxel editor: {result!r}")
        editor = voxel_editor._ACTIVE_EDITOR
        if editor is None:
            return _fail("Escape transition returned without an active voxel editor")
        STATE["escape_project"] = True
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
        ):
            editor._activate_topbar_action(bpy.context, "MORE_TOOLS")
            if not desktop_ui.native_chrome_visible(scene):
                return _fail("More tools did not reveal Blender controls")
            editor._activate_topbar_action(bpy.context, "MORE_TOOLS")
            if desktop_ui.native_chrome_visible(scene):
                return _fail("More tools did not hide Blender controls again")
        STATE["more_tools_escape_hatch"] = True
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
