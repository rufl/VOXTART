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

from voxtart import desktop_shell, desktop_ui, operators, voxel_editor  # noqa: E402
from voxtart.voxel import VoxelAsset  # noqa: E402


MARKER = Path(os.environ.get("VOXTART_GUI_MARKER", "voxtart_gui_probe.json"))
SCREENSHOT = Path(os.environ.get("VOXTART_GUI_SCREENSHOT", "voxtart_gui_probe.png"))
SHELL_SCREENSHOT = Path(
    os.environ.get(
        "VOXTART_GUI_SHELL_SCREENSHOT",
        str(SCREENSHOT.with_name(f"{SCREENSHOT.stem}_shell{SCREENSHOT.suffix}")),
    )
)
STATE = {"attempts": 0}


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
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        with bpy.context.temp_override(
            window=STATE["window"],
            screen=STATE["screen"],
            area=STATE["area"],
            region=STATE["region"],
        ):
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
                "starter_pyramid": STATE.get("starter_pyramid", 0),
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
        starter = bpy.data.objects.get(desktop_ui.DEFAULT_PYRAMID_OBJECT_NAME)
        if starter is None or not starter.get(desktop_ui.DEFAULT_PYRAMID_PROPERTY):
            return _fail("VOXTART startup did not create the editable starter pyramid")
        starter_asset = operators._load_asset(starter)
        if len(starter_asset.voxels) != 56 or starter_asset.palette != [desktop_ui.DEFAULT_PYRAMID_COLOR]:
            return _fail("VOXTART starter pyramid has the wrong geometry or palette")
        STATE["starter_pyramid"] = len(starter_asset.voxels)
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
            shell_toolbar_count = sum(key.startswith("tool:") for key, _bounds in shell_state.targets)
            expected_toolbar_count = sum(item is not None for item in voxel_editor.EDITOR_TOOLBAR_ITEMS)
            if shell_toolbar_count != expected_toolbar_count:
                return _fail(
                    f"Desktop shell toolbar target count mismatch: "
                    f"{shell_toolbar_count} != {expected_toolbar_count}"
                )
            area.tag_redraw()
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
            SHELL_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            screenshot_result = bpy.ops.screen.screenshot(filepath=str(SHELL_SCREENSHOT))
            if "FINISHED" not in screenshot_result or not SHELL_SCREENSHOT.is_file():
                return _fail(f"Could not capture shell screenshot: {screenshot_result!r}")
            STATE["shell_toolbar"] = shell_toolbar_count
            STATE["shell_screenshot"] = str(SHELL_SCREENSHOT)
            desktop_shell.VOXTART_OT_desktop_shell._finish(None, bpy.context)
        STATE["view_workflow"] = _exercise_view_workflow(window, screen, area, region, scene)
        if not hasattr(bpy.types, "VOXTART_OT_voxel_editor"):
            return _fail("VOXTART voxel editor is not registered")
        existing = bpy.data.objects.get("P0 GUI Asset")
        if existing is not None:
            bpy.data.objects.remove(existing, do_unlink=True)
        obj = operators._create_asset_object(
            bpy.context,
            "P0 GUI Asset",
            VoxelAsset.box(2),
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
