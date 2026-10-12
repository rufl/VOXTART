# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Headless Blender end-to-end checks for the VOXTART editor.

This suite exercises the modal editor state machine, persistence boundaries,
large-asset paths, and palette/layer data through the native Blender runtime.
Run it from the repository root with::

    build\\blender-5.2.2\\blender-5.2.2-windows-x64\\blender-core.exe \\
        --background --factory-startup --python tests/python/voxtart_e2e_blender.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "scripts" / "addons_core"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
import voxtart as addon  # noqa: E402
from voxtart import layers, operators, palette_ui, viewport_workflow_ui, voxel_brush, voxel_editor  # noqa: E402
from voxtart.voxel import VoxelAsset  # noqa: E402
EXPECTED_TESTS = 7


class _EditorContext:
    """Minimal context accepted by modal paths that do not raycast."""

    area = None
    region = None
    region_data = None

    def __init__(self, scene):
        self.scene = scene


def _event(
    event_type: str,
    *,
    value: str = "PRESS",
    ctrl: bool = False,
    shift: bool = False,
    alt: bool = False,
    mouse_region_x: float = 0,
    mouse_region_y: float = 0,
):
    return SimpleNamespace(
        type=event_type,
        value=value,
        ctrl=ctrl,
        shift=shift,
        alt=alt,
        mouse_region_x=mouse_region_x,
        mouse_region_y=mouse_region_y,
    )


def _remove_objects(objects) -> None:
    for obj in tuple(objects):
        if obj.name in bpy.data.objects:
            bpy.data.objects.remove(obj, do_unlink=True)


class _EditorHarness:
    """Plain-Python shell exposing the registered operator's workflow methods."""

    _apply_at_cursor = voxel_editor.VOXTART_OT_voxel_editor._apply_at_cursor
    _activate_toolbar_tool = voxel_editor.VOXTART_OT_voxel_editor._activate_toolbar_tool
    _grid_hit = voxel_editor.VOXTART_OT_voxel_editor._grid_hit
    _apply_cells = voxel_editor.VOXTART_OT_voxel_editor._apply_cells
    _begin_scene_transform = voxel_editor.VOXTART_OT_voxel_editor._begin_scene_transform
    _begin_stroke = voxel_editor.VOXTART_OT_voxel_editor._begin_stroke
    _begin_transform = voxel_editor.VOXTART_OT_voxel_editor._begin_transform
    _cancel_scene_transform = voxel_editor.VOXTART_OT_voxel_editor._cancel_scene_transform
    _cancel_stroke = voxel_editor.VOXTART_OT_voxel_editor._cancel_stroke
    _cancel_transform = voxel_editor.VOXTART_OT_voxel_editor._cancel_transform
    _clear_recovery = voxel_editor.VOXTART_OT_voxel_editor._clear_recovery
    _commit_scene_transform = voxel_editor.VOXTART_OT_voxel_editor._commit_scene_transform
    _commit_stroke = voxel_editor.VOXTART_OT_voxel_editor._commit_stroke
    _commit_transform = voxel_editor.VOXTART_OT_voxel_editor._commit_transform
    _copy_selection = voxel_editor.VOXTART_OT_voxel_editor._copy_selection
    _load_clipboard = voxel_editor.VOXTART_OT_voxel_editor._load_clipboard
    _load_state = voxel_editor.VOXTART_OT_voxel_editor._load_state
    _persist_recovery = voxel_editor.VOXTART_OT_voxel_editor._persist_recovery
    _recover_interrupted_state = voxel_editor.VOXTART_OT_voxel_editor._recover_interrupted_state
    _record_history = voxel_editor.VOXTART_OT_voxel_editor._record_history
    _reset_scene_transform = voxel_editor.VOXTART_OT_voxel_editor._reset_scene_transform
    _reset_transform = voxel_editor.VOXTART_OT_voxel_editor._reset_transform
    _restore_history = voxel_editor.VOXTART_OT_voxel_editor._restore_history
    _sync = voxel_editor.VOXTART_OT_voxel_editor._sync
    _tag_redraw = voxel_editor.VOXTART_OT_voxel_editor._tag_redraw
    _transform_move = voxel_editor.VOXTART_OT_voxel_editor._transform_move
    _transform_pose = voxel_editor.VOXTART_OT_voxel_editor._transform_pose
    _update_scene_transform = voxel_editor.VOXTART_OT_voxel_editor._update_scene_transform
    _modal_scene = voxel_editor.VOXTART_OT_voxel_editor._modal_scene
    _update_transform_pose = voxel_editor.VOXTART_OT_voxel_editor._update_transform_pose
    _update_working_state = voxel_editor.VOXTART_OT_voxel_editor._update_working_state
    _update_hover_preview = voxel_editor.VOXTART_OT_voxel_editor._update_hover_preview
    _stamp = voxel_editor.VOXTART_OT_voxel_editor._stamp
    modal = voxel_editor.VOXTART_OT_voxel_editor.modal

class BlenderEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            addon.unregister()
        except (AttributeError, RuntimeError):
            pass
        addon.register()
        for obj in tuple(bpy.context.selected_objects):
            obj.select_set(False)
        bpy.context.view_layer.objects.active = None

    @classmethod
    def tearDownClass(cls):
        _remove_objects(bpy.data.objects)
        addon.unregister()

    def setUp(self):
        self.scene = bpy.context.scene
        self.scene.voxtart_editor_workspace = "EDIT"
        self.scene.voxtart_editor_mode = "ADD"
        self.scene.voxtart_editor_shape = "VOXEL"
        self.scene.voxtart_editor_radius = 1
        self.scene.voxtart_editor_depth = 1
        self.scene.voxtart_editor_hollow = False
        self.scene.voxtart_editor_mirror_x = False
        self.scene.voxtart_editor_mirror_y = False
        self.scene.voxtart_editor_mirror_z = False

    def _make_editor(self, obj):
        editor = _EditorHarness()
        editor.obj = obj
        editor.scene = self.scene
        editor._load_state(bpy.context)
        editor.history = [voxel_editor._copy_history(editor.asset, editor.stack, editor.selection, obj)]
        editor.history_index = 0
        editor.transaction = None
        editor.stroke_anchor = None
        editor.stroke_asset = None
        editor.stroke_layers = None
        editor.selection_anchor = None
        editor.selection_screen_anchor = None
        editor.selection_path = []
        editor.selection_gesture_base = tuple(editor.selection)
        editor.preview_cells = ()
        editor._last_redraw = 0.0
        editor._reset_transform()
        editor._reset_scene_transform()
        editor.dragging = False
        editor.drag_mode = None
        editor.toolbar_hover = None
        editor.last_hit = None
        editor.mirrors = (False, False, False)
        editor.status = "Ready"
        return editor

    def test_modal_editor_scene_and_undo_workflow(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            obj = operators._create_asset_object(
                bpy.context,
                "E2E Modal Asset",
                VoxelAsset.box(1),
            )
            editor = self._make_editor(obj)
            context = _EditorContext(self.scene)

            self.assertEqual(editor.modal(context, _event("T")), {"RUNNING_MODAL"})
            self.assertEqual(self.scene.voxtart_editor_mode, "ATTACH")
            self.assertEqual(editor.modal(context, _event("F")), {"RUNNING_MODAL"})
            self.assertEqual(self.scene.voxtart_editor_shape, "FACE")
            self.assertEqual(editor.modal(context, _event("RIGHT_BRACKET")), {"RUNNING_MODAL"})
            self.assertEqual(self.scene.voxtart_editor_radius, 2)
            self.assertEqual(editor.modal(context, _event("ONE")), {"RUNNING_MODAL"})
            self.assertTrue(editor.mirrors[0])

            self.scene.voxtart_editor_mode = "SELECT"
            self.scene.voxtart_editor_shape = "VOXEL"
            editor.selection = ((0, 0, 0),)
            voxel_editor._store_selection(obj, editor.selection)
            self.assertEqual(editor.modal(context, _event("G")), {"RUNNING_MODAL"})
            self.assertEqual(editor.modal(context, _event("RIGHT_ARROW")), {"RUNNING_MODAL"})
            self.assertEqual(editor.modal(context, _event("RET")), {"RUNNING_MODAL"})
            self.assertIn((1, 0, 0), operators._load_asset(obj).voxels)
            self.assertEqual(editor.history_index, 1)

            self.assertEqual(editor.modal(context, _event("Z", ctrl=True)), {"RUNNING_MODAL"})
            self.assertIn((0, 0, 0), operators._load_asset(obj).voxels)
            self.assertNotIn((1, 0, 0), operators._load_asset(obj).voxels)
            self.assertEqual(editor.modal(context, _event("Y", ctrl=True)), {"RUNNING_MODAL"})
            self.assertIn((1, 0, 0), operators._load_asset(obj).voxels)

            original_location = tuple(obj.location)
            self.assertEqual(editor.modal(context, _event("TAB")), {"RUNNING_MODAL"})
            self.assertEqual(self.scene.voxtart_editor_workspace, "SCENE")
            self.assertEqual(editor.modal(context, _event("G")), {"RUNNING_MODAL"})
            self.assertEqual(editor.modal(context, _event("RIGHT_ARROW")), {"RUNNING_MODAL"})
            self.assertEqual(editor.modal(context, _event("RET")), {"RUNNING_MODAL"})
            self.assertEqual(tuple(obj.location), (original_location[0] + 1.0, original_location[1], original_location[2]))
            self.assertEqual(editor.modal(context, _event("TAB")), {"RUNNING_MODAL"})
            self.assertEqual(self.scene.voxtart_editor_workspace, "EDIT")
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_mouse_buttons_place_and_erase_voxels(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            obj = operators._create_asset_object(
                bpy.context,
                "E2E Mouse Editing Asset",
                VoxelAsset.box(1),
            )
            editor = self._make_editor(obj)
            toolbar_context = SimpleNamespace(
                scene=self.scene,
                area=None,
                region=SimpleNamespace(height=900, width=1200),
                region_data=None,
            )
            toolbar_panel, toolbar_entries = voxel_editor._editor_toolbar_layout(900)
            self.assertLessEqual(toolbar_panel[2], 48.0)
            erase_item, erase_bounds = next(
                (item, bounds)
                for item, bounds in toolbar_entries
                if item[0] == "MODE" and item[1] == "ERASE"
            )
            erase_x = (erase_bounds[0] + erase_bounds[2]) * 0.5
            erase_y = (erase_bounds[1] + erase_bounds[3]) * 0.5
            self.assertEqual(
                editor.modal(
                    toolbar_context,
                    _event("LEFTMOUSE", mouse_region_x=erase_x, mouse_region_y=erase_y),
                ),
                {"RUNNING_MODAL"},
            )
            self.assertEqual(self.scene.voxtart_editor_mode, erase_item[1])
            self.assertEqual(
                editor.modal(
                    toolbar_context,
                    _event(
                        "LEFTMOUSE",
                        value="RELEASE",
                        mouse_region_x=erase_x,
                        mouse_region_y=erase_y,
                    ),
                ),
                {"RUNNING_MODAL"},
            )
            self.scene.voxtart_editor_mode = "ADD"
            context = _EditorContext(self.scene)
            editor._ray_hit = lambda _context, _event: voxel_editor.RayHit(
                (0, 0, 0),
                0,
                0.0,
                (1, 0, 0),
            )

            empty_hit = voxel_editor.RayHit((3, 4, 0), 0, 0.0, (0, 0, 0))
            self.assertEqual(editor._stamp(empty_hit), ((3, 4, 0),))
            origin_3d = voxel_editor.view3d_utils.region_2d_to_origin_3d
            vector_3d = voxel_editor.view3d_utils.region_2d_to_vector_3d
            voxel_editor.view3d_utils.region_2d_to_origin_3d = lambda *_args: voxel_editor.Vector((3.25, 4.75, 2.0))
            voxel_editor.view3d_utils.region_2d_to_vector_3d = lambda *_args: voxel_editor.Vector((0.0, 0.0, -1.0))
            try:
                grid_hit = editor._grid_hit(
                    SimpleNamespace(region=object(), region_data=object()),
                    _event("MOUSEMOVE"),
                )
            finally:
                voxel_editor.view3d_utils.region_2d_to_origin_3d = origin_3d
                voxel_editor.view3d_utils.region_2d_to_vector_3d = vector_3d
            self.assertIsNotNone(grid_hit)
            self.assertEqual(grid_hit.cell, (3, 4, 0))
            self.assertEqual(grid_hit.normal, (0, 0, 0))

            self.assertEqual(editor.modal(context, _event("LEFTMOUSE")), {"RUNNING_MODAL"})
            self.assertEqual(editor.modal(context, _event("LEFTMOUSE", value="RELEASE")), {"RUNNING_MODAL"})
            asset = operators._load_asset(obj)
            self.assertIn((1, 0, 0), asset.voxels)

            self.assertEqual(editor.modal(context, _event("RIGHTMOUSE")), {"RUNNING_MODAL"})
            self.assertEqual(editor.modal(context, _event("RIGHTMOUSE", value="RELEASE")), {"RUNNING_MODAL"})
            asset = operators._load_asset(obj)
            self.assertNotIn((0, 0, 0), asset.voxels)
            self.assertIn((1, 0, 0), asset.voxels)

            editor._ray_hit = lambda _context, _event: empty_hit
            self.assertEqual(editor.modal(context, _event("LEFTMOUSE")), {"RUNNING_MODAL"})
            self.assertEqual(editor.modal(context, _event("LEFTMOUSE", value="RELEASE")), {"RUNNING_MODAL"})
            self.assertIn((3, 4, 0), operators._load_asset(obj).voxels)
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_recovery_clipboard_palette_and_layer_corruption(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            palette = [
                (1.0, 0.0, 0.0, 1.0),
                (0.0, 1.0, 0.0, 1.0),
            ]
            obj = operators._create_asset_object(
                bpy.context,
                "E2E Recovery Asset",
                VoxelAsset(palette=palette, voxels={(0, 0, 0): 0}),
            )
            editor = self._make_editor(obj)
            context = _EditorContext(self.scene)
            editor._begin_stroke()
            editor._apply_cells(((1, 0, 0),))
            editor._update_working_state()
            editor._persist_recovery(force=True)
            self.assertIn(voxel_editor.EDITOR_STROKE_PROPERTY, obj)

            recovered = self._make_editor(obj)
            self.assertTrue(recovered._recover_interrupted_state())
            self.assertIn((1, 0, 0), recovered.asset.voxels)
            self.assertNotIn(voxel_editor.EDITOR_STROKE_PROPERTY, obj)
            editor._cancel_stroke(context)

            editor.selection = ((0, 0, 0), (1, 0, 0))
            editor._copy_selection()
            self.assertIn(voxel_editor.EDITOR_CLIPBOARD_PROPERTY, obj)
            obj[voxel_editor.EDITOR_CLIPBOARD_PROPERTY] = "[[0, 0, 0, 99]]"
            reader = self._make_editor(obj)
            voxel_editor._CLIPBOARD = ()
            reader._load_clipboard()
            self.assertEqual(voxel_editor._CLIPBOARD, ())
            self.assertNotIn(voxel_editor.EDITOR_CLIPBOARD_PROPERTY, obj)

            obj[voxel_editor.EDITOR_STROKE_PROPERTY] = "{malformed"
            invalid_recovery = self._make_editor(obj)
            self.assertFalse(invalid_recovery._recover_interrupted_state())
            self.assertNotIn(voxel_editor.EDITOR_STROKE_PROPERTY, obj)

            stack = layers.LayerStack.from_asset(recovered.asset)
            stack.add("Overlay").voxels = {(0, 0, 0): 1, (2, 0, 0): 1}
            operators._store_layer_stack(obj, stack)
            loaded = operators._load_layer_stack(obj, recovered.asset)
            self.assertEqual(len(loaded.layers), 2)
            self.assertEqual(loaded.composite(recovered.asset.palette)[(0, 0, 0)], 1)
            self.assertIn((2, 0, 0), loaded.composite(recovered.asset.palette))
        finally:
            voxel_editor._CLIPBOARD = ()
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_large_asset_rebuild_and_selection_budget(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            asset = VoxelAsset.box(32)
            obj = operators._create_asset_object(bpy.context, "E2E Large Asset", asset)
            started = time.perf_counter()
            operators._replace_asset_mesh(obj, asset)
            rebuild_seconds = time.perf_counter() - started
            self.assertLess(rebuild_seconds, 5.0)

            minimum = -(32 // 2)
            top_cell = (minimum, minimum, minimum + 31)
            projected = {
                cell: (float(cell[0]), float(cell[1]))
                for cell in asset.voxels
            }
            selection_started = time.perf_counter()
            connected = voxel_brush.select_connected(asset.voxels, (minimum, minimum, minimum))
            flat = voxel_brush.select_flat_surface(asset.voxels, top_cell, (0, 0, 1))
            lasso = voxel_brush.select_lasso(
                projected,
                ((minimum - 1, minimum - 1), (minimum + 32, minimum - 1), (minimum + 32, minimum + 32), (minimum - 1, minimum + 32)),
            )
            circle = voxel_brush.select_circle(projected, (minimum + 15.5, minimum + 15.5), 16.0)
            selection_seconds = time.perf_counter() - selection_started
            self.assertLess(selection_seconds, 5.0)
            self.assertEqual(len(connected), 32 ** 3)
            self.assertEqual(len(flat), 32 ** 2)
            self.assertEqual(len(lasso), 32 ** 3)
            self.assertGreater(len(circle), 0)
            payload = {
                "schema": 1,
                "voxels": len(asset.voxels),
                "rebuild_ms": round(rebuild_seconds * 1000.0, 3),
                "selection_ms": round(selection_seconds * 1000.0, 3),
                "connected": len(connected),
                "flat": len(flat),
                "lasso": len(lasso),
                "circle": len(circle),
            }
            output_path = os.environ.get("VOXTART_PERF_OUTPUT")
            if output_path:
                target = Path(output_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            print(
                "VOXTART_E2E_PERF "
                f"voxels={len(asset.voxels)} rebuild_ms={rebuild_seconds * 1000.0:.2f} "
                f"selection_ms={selection_seconds * 1000.0:.2f} flat={len(flat)} circle={len(circle)}"
            )
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)
    def test_surface_backend_scope_controls(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            obj = operators._create_asset_object(
                bpy.context,
                "E2E Surface Scope Asset",
                VoxelAsset.box(2),
            )
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            self.assertEqual(bpy.ops.voxtart.layer_new(name="Rounded Layer"), {"FINISHED"})
            self.assertEqual(
                bpy.ops.voxtart.layer_surface(scope="LAYER", backend="MARCHING"),
                {"FINISHED"},
            )
            stack = operators._load_layer_stack(obj)
            self.assertEqual(stack.active.surface_backend, "MARCHING")
            self.assertFalse(bool(obj.get(operators.MESH_BACKEND_OVERRIDE_PROPERTY, False)))
            self.assertEqual(obj.get(operators.MESH_BACKEND_PROPERTY), "marching")
            self.assertEqual(
                bpy.ops.voxtart.layer_surface(scope="OBJECT", backend="GREEDY"),
                {"FINISHED"},
            )
            self.assertTrue(bool(obj.get(operators.MESH_BACKEND_OVERRIDE_PROPERTY, False)))
            self.assertEqual(obj.get(operators.MESH_BACKEND_PROPERTY), "greedy")
            self.assertEqual(
                bpy.ops.voxtart.layer_surface(scope="OBJECT", backend="INHERIT"),
                {"FINISHED"},
            )
            self.assertFalse(bool(obj.get(operators.MESH_BACKEND_OVERRIDE_PROPERTY, False)))
            self.assertEqual(obj.get(operators.MESH_BACKEND_PROPERTY), "marching")
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)


    def test_camera_workflow_controls(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            first_data = bpy.data.cameras.new("E2E Camera A Data")
            first = bpy.data.objects.new("E2E Camera A", first_data)
            self.scene.collection.objects.link(first)
            second_data = bpy.data.cameras.new("E2E Camera B Data")
            second = bpy.data.objects.new("E2E Camera B", second_data)
            self.scene.collection.objects.link(second)
            self.scene.camera = first

            self.assertEqual(
                bpy.ops.voxtart.camera_set_active(object_name=second.name),
                {"FINISHED"},
            )
            self.assertIs(self.scene.camera, second)
            self.assertIs(bpy.context.view_layer.objects.active, second)

            self.assertEqual(
                bpy.ops.voxtart.camera_solo(object_name=second.name),
                {"FINISHED"},
            )
            self.assertTrue(first.hide_viewport)
            self.assertFalse(second.hide_viewport)

            self.assertEqual(bpy.ops.voxtart.camera_show_all(), {"FINISHED"})
            self.assertFalse(first.hide_viewport)
            self.assertFalse(second.hide_viewport)

            self.assertEqual(
                bpy.ops.voxtart.camera_add(name="E2E Camera C"),
                {"FINISHED"},
            )
            self.assertIsNotNone(self.scene.objects.get("E2E Camera C"))
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_palette_operator_workflow(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            colors = [
                (1.0, 0.0, 0.0, 1.0),
                (0.0, 1.0, 0.0, 1.0),
                (0.0, 0.0, 1.0, 1.0),
            ]
            obj = operators._create_asset_object(
                bpy.context,
                "E2E Palette Asset",
                VoxelAsset(palette=colors, voxels={(0, 0, 0): 0, (1, 0, 0): 1}),
            )
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            palette_ui._set_palette(self.scene, colors, name="E2E Palette")
            self.scene.voxtart_palette_edit_color = (0.8, 0.4, 0.2, 1.0)
            self.assertEqual(bpy.ops.voxtart.palette_set_selected(), {"FINISHED"})
            self.assertEqual(
                tuple(round(channel, 6) for channel in self.scene.voxtart_palette_colors[0].color),
                (0.8, 0.4, 0.2, 1.0),
            )
            self.assertEqual(bpy.ops.voxtart.palette_select(index=1), {"FINISHED"})
            self.assertEqual(self.scene.voxtart_palette_index, 1)
            self.assertEqual(bpy.ops.voxtart.palette_select(index=0, extend=True), {"FINISHED"})
            self.assertEqual(
                [index for index, item in enumerate(self.scene.voxtart_palette_colors) if item.selected],
                [0, 1],
            )
            self.assertEqual(bpy.ops.voxtart.palette_duplicate(index=0), {"FINISHED"})
            self.assertEqual(bpy.ops.voxtart.palette_reorder(index=0, target_index=2), {"FINISHED"})
            self.assertEqual(bpy.ops.voxtart.palette_compact(), {"FINISHED"})
            self.assertEqual(bpy.ops.voxtart.palette_gradient(count=4), {"FINISHED"})
            self.assertEqual(len(self.scene.voxtart_palette_colors), 4)
            self.assertEqual(tuple(self.scene.voxtart_palette_colors[0].color), (0.0, 0.0, 0.0, 1.0))
            self.assertEqual(tuple(self.scene.voxtart_palette_colors[-1].color), (1.0, 1.0, 1.0, 1.0))
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)


if __name__ == "__main__":
    program = unittest.main(argv=[sys.argv[0]], exit=False)
    if program.result.testsRun != EXPECTED_TESTS:
        raise SystemExit(
            f"VOXTART_E2E_TEST_COUNT {program.result.testsRun} != {EXPECTED_TESTS}"
        )
    raise SystemExit(not program.result.wasSuccessful())
