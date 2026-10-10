# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Blender-native VOXTART registration and UI contract tests.

Run with a Blender executable from the repository root::

    blender --background --python tests/python/voxtart_blender_test.py

The system-Python suite in ``voxtart_core_test.py`` covers the deterministic
voxel and file-format algorithms without requiring Blender.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "scripts" / "addons_core"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import voxtart as addon  # noqa: E402
from voxtart import animation, blob_gallery_ui, bloformer_ui, character_editor_ui, desktop_branding, desktop_shell, desktop_splash, desktop_ui, optimizer_ui, operators, palette_ui, reconstruct_ui, scene_hierarchy_ui, spritesheet_export_ui, tiler_ui, ux, viewport_workflow_ui, voxel_editor  # noqa: E402
from voxtart.blob_gallery import BlobGallery, BlobJoint, make_blob_entry  # noqa: E402
from voxtart.character_editor import CharacterRecipe  # noqa: E402
from voxtart.voxel import VoxelAsset, VoxelSocket  # noqa: E402


def _gallery_entry(name, blob_id, normal):
    asset = VoxelAsset.box(1)
    socket = VoxelSocket((0, 0, 0), normal, "mount")
    asset.sockets = [socket]
    return make_blob_entry(
        asset,
        name,
        blob_id=blob_id,
        joints=(BlobJoint(name="mount", socket=socket, role="mount"),),
    )


def _remove_objects(objects):
    for obj in tuple(objects):
        if obj.name in bpy.data.objects:
            bpy.data.objects.remove(obj, do_unlink=True)


class BlenderRegistrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            addon.unregister()
        except (AttributeError, RuntimeError):
            pass
        addon.register()
        for selected in tuple(bpy.context.selected_objects):
            selected.select_set(False)
        bpy.context.view_layer.objects.active = None

    @classmethod
    def tearDownClass(cls):
        addon.unregister()

    def test_addon_metadata_and_scene_properties(self):
        self.assertEqual(addon.bl_info["name"], "VOXTART")
        self.assertGreaterEqual(addon.bl_info["version"], (0, 16, 0))
        scene = bpy.context.scene
        self.assertEqual(scene.voxtart_bloformer_mode, "ADD")
        self.assertEqual(scene.voxtart_bloformer_shape, "SPHERE")
        self.assertEqual(tuple(scene.voxtart_bloformer_center), (0, 0, 0))
        self.assertEqual(scene.voxtart_bloformer_radius, 3)
        self.assertEqual(scene.voxtart_bloformer_axis, "Z")

    def test_registration_exposes_operator_panel_and_menu(self):
        self.assertIs(getattr(bpy.types, "VOXTART_OT_bloformer"), bloformer_ui.VOXTART_OT_bloformer)
        self.assertIs(getattr(bpy.types, "VOXTART_PT_bloformer"), bloformer_ui.VOXTART_PT_bloformer)
        self.assertEqual(bloformer_ui.VOXTART_OT_bloformer.bl_idname, "voxtart.bloformer")
        self.assertTrue(bloformer_ui.VOXTART_PT_bloformer.poll(bpy.context))
        self.assertFalse(bloformer_ui.VOXTART_OT_bloformer.poll(bpy.context))
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_reconstruct_image"),
            reconstruct_ui.VOXTART_OT_reconstruct_image,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_reconstruction"),
            reconstruct_ui.VOXTART_PT_reconstruction,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_reconstruct_views"),
            reconstruct_ui.VOXTART_OT_reconstruct_views,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_multiview_reconstruction"),
            reconstruct_ui.VOXTART_PT_multiview_reconstruction,
        )
        self.assertTrue(hasattr(bpy.types.VIEW3D_MT_object, "append"))
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_zoo_asset_placer"),
            blob_gallery_ui.VOXTART_PT_zoo_asset_placer,
        )
        self.assertEqual(
            blob_gallery_ui.VOXTART_PT_zoo_asset_placer.bl_idname,
            "VOXTART_PT_zoo_asset_placer",
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_tool_settings"),
            viewport_workflow_ui.VOXTART_PT_tool_settings,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_view_workflow"),
            viewport_workflow_ui.VOXTART_PT_view_workflow,
        )
        self.assertEqual(
            viewport_workflow_ui.VOXTART_OT_camera_solo.bl_idname,
            "voxtart.camera_solo",
        )
        self.assertEqual(
            viewport_workflow_ui.VOXTART_OT_view_perspective.bl_idname,
            "voxtart.view_perspective",
        )

    def test_voxel_editor_registration_and_defaults(self):
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_voxel_editor"),
            voxel_editor.VOXTART_OT_voxel_editor,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_voxel_editor"),
            voxel_editor.VOXTART_PT_voxel_editor,
        )
        self.assertEqual(
            voxel_editor.VOXTART_OT_voxel_editor.bl_idname,
            "voxtart.voxel_editor",
        )
        self.assertFalse(voxel_editor.VOXTART_OT_voxel_editor.poll(bpy.context))
        scene = bpy.context.scene
        self.assertEqual(scene.voxtart_editor_mode, "ADD")
        self.assertEqual(scene.voxtart_editor_shape, "VOXEL")
        self.assertEqual(scene.voxtart_editor_radius, 1)
        self.assertFalse(scene.voxtart_editor_hollow)
        self.assertEqual(scene.voxtart_editor_axis, "Z")
        self.assertEqual(scene.voxtart_editor_workspace, "EDIT")
        self.assertEqual(scene.voxtart_editor_depth, 1)

    def test_voxel_editor_shortcut_uses_safe_modifier_profile(self):
        shortcut = next(
            shortcut
            for shortcut in ux.SHORTCUTS
            if shortcut.operator == "voxtart.voxel_editor"
        )
        self.assertEqual(shortcut.key, "Q")
        self.assertTrue(shortcut.shift)
        self.assertTrue(shortcut.alt)
        self.assertFalse(shortcut.ctrl)
        registered = [
            item
            for _keymap, item in ux._KEYMAP_ITEMS
            if item.idname == "voxtart.voxel_editor"
        ]
        self.assertEqual(len(registered), 1)

    def test_voxelizer_registration_and_defaults(self):
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_voxelize_active"),
            operators.VOXTART_OT_voxelize_active,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_refresh_voxelizer"),
            operators.VOXTART_OT_refresh_voxelizer,
        )
        self.assertEqual(
            operators.VOXTART_OT_refresh_voxelizer.bl_idname,
            "voxtart.refresh_voxelizer",
        )
        self.assertFalse(operators.VOXTART_OT_voxelize_active.poll(bpy.context))
        self.assertFalse(operators.VOXTART_OT_refresh_voxelizer.poll(bpy.context))
        scene = bpy.context.scene
        self.assertEqual(scene.voxtart_voxelizer_mode, "SOLID")
        self.assertEqual(scene.voxtart_voxelizer_shell_thickness, 1)

    def test_volume_transform_and_palette_reduction_registration(self):
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_transform_asset"),
            operators.VOXTART_OT_transform_asset,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_palette_reduce"),
            palette_ui.VOXTART_OT_palette_reduce,
        )
        self.assertEqual(operators.VOXTART_OT_transform_asset.bl_idname, "voxtart.transform_asset")
        self.assertEqual(palette_ui.VOXTART_OT_palette_reduce.bl_idname, "voxtart.palette_reduce")
        self.assertFalse(operators.VOXTART_OT_transform_asset.poll(bpy.context))
        self.assertFalse(palette_ui.VOXTART_OT_palette_reduce.poll(bpy.context))
        scene = bpy.context.scene
        self.assertEqual(scene.voxtart_transform_operation, "MIRROR")
        self.assertEqual(scene.voxtart_transform_axis, "X")
        self.assertEqual(scene.voxtart_transform_degrees, "90")
        self.assertEqual(tuple(scene.voxtart_transform_offset), (0, 0, 0))
        self.assertTrue(scene.voxtart_transform_rebase)
        self.assertEqual(scene.voxtart_palette_reduce_colors, 32)
        self.assertEqual(scene.voxtart_palette_reduce_method, "MEDIAN_CUT")


    def test_scene_hierarchy_registration_and_defaults(self):
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_scene_hierarchy_select"),
            scene_hierarchy_ui.VOXTART_OT_scene_hierarchy_select,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_scene_hierarchy"),
            scene_hierarchy_ui.VOXTART_PT_scene_hierarchy,
        )
        self.assertEqual(
            scene_hierarchy_ui.VOXTART_OT_scene_hierarchy_select.bl_idname,
            "voxtart.scene_hierarchy_select",
        )
        scene = bpy.context.scene
        self.assertEqual(scene.voxtart_scene_hierarchy_search, "")
        self.assertFalse(scene.voxtart_scene_hierarchy_include_unplaced)


    def test_character_editor_registration_and_defaults(self):
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_character_assign_blob"),
            character_editor_ui.VOXTART_OT_character_assign_blob,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_character_editor"),
            character_editor_ui.VOXTART_PT_character_editor,
        )
        self.assertEqual(
            character_editor_ui.VOXTART_OT_character_assign_blob.bl_idname,
            "voxtart.character_assign_blob",
        )
        self.assertEqual(
            character_editor_ui.VOXTART_OT_character_rebuild.bl_idname,
            "voxtart.character_rebuild",
        )
        scene = bpy.context.scene
        self.assertEqual(scene.voxtart_character_name, "VOXTART Character")
        self.assertEqual(scene.voxtart_character_slot, "ROOT")
        self.assertEqual(scene.voxtart_character_search, "")
        self.assertTrue(scene.voxtart_character_auto_snap)
    def test_optimizer_registration_and_defaults(self):
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_optimize_mesh"),
            optimizer_ui.VOXTART_OT_optimize_mesh,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_optimizer"),
            optimizer_ui.VOXTART_PT_optimizer,
        )
        self.assertEqual(
            optimizer_ui.VOXTART_OT_optimize_mesh.bl_idname,
            "voxtart.optimize_mesh",
        )
        self.assertFalse(optimizer_ui.VOXTART_OT_optimize_mesh.poll(bpy.context))
        scene = bpy.context.scene
        self.assertEqual(scene.voxtart_optimizer_backend, "greedy")
        self.assertTrue(scene.voxtart_optimizer_weld_vertices)
        self.assertTrue(scene.voxtart_optimizer_remove_degenerate)
        self.assertTrue(scene.voxtart_optimizer_remove_duplicate)
        self.assertFalse(scene.voxtart_optimizer_triangulate)
        self.assertTrue(scene.voxtart_optimizer_compact_materials)


    def test_spritesheet_export_registration_and_defaults(self):
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_export_spritesheet"),
            spritesheet_export_ui.VOXTART_OT_export_spritesheet,
        )
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_spritesheet_export"),
            spritesheet_export_ui.VOXTART_PT_spritesheet_export,
        )
        self.assertEqual(
            spritesheet_export_ui.VOXTART_OT_export_spritesheet.bl_idname,
            "voxtart.export_spritesheet",
        )
        self.assertFalse(spritesheet_export_ui.VOXTART_OT_export_spritesheet.poll(bpy.context))
        scene = bpy.context.scene
        self.assertEqual(scene.voxtart_spritesheet_start_frame, 1)
        self.assertEqual(scene.voxtart_spritesheet_end_frame, 24)
        self.assertEqual(scene.voxtart_spritesheet_frame_step, 1)
        self.assertEqual(scene.voxtart_spritesheet_frame_width, 128)
        self.assertEqual(scene.voxtart_spritesheet_frame_height, 128)
        self.assertEqual(scene.voxtart_spritesheet_columns, 0)
        self.assertEqual(scene.voxtart_spritesheet_view, "ISOMETRIC")
        self.assertTrue(scene.voxtart_spritesheet_write_metadata)

    def test_tiler_registration_and_scale_contract(self):
        self.assertIs(getattr(bpy.types, "VOXTART_OT_tiler_stamp"), tiler_ui.VOXTART_OT_tiler_stamp)
        self.assertIs(getattr(bpy.types, "VOXTART_OT_tiler_convert"), tiler_ui.VOXTART_OT_tiler_convert)
        self.assertIs(getattr(bpy.types, "VOXTART_PT_tiler"), tiler_ui.VOXTART_PT_tiler)
        self.assertEqual(tiler_ui.VOXTART_OT_tiler_stamp.bl_idname, "voxtart.tiler_stamp")
        self.assertEqual(tiler_ui.VOXTART_OT_tiler_convert.bl_idname, "voxtart.tiler_convert")
        self.assertTrue(tiler_ui.VOXTART_PT_tiler.poll(bpy.context))
        self.assertFalse(tiler_ui.VOXTART_OT_tiler_stamp.poll(bpy.context))
        self.assertTrue(tiler_ui.VOXTART_OT_tiler_convert.poll(bpy.context))
        scene = bpy.context.scene
        self.assertEqual(scene.voxtart_tiler_axis, "Z")
        self.assertEqual(scene.voxtart_tiler_side, "1")
        self.assertEqual(scene.voxtart_tiler_rotation, "0")
        self.assertEqual(scene.voxtart_tiler_depth, 1)
        self.assertEqual(scene.voxtart_tiler_source, "IMAGE")
        self.assertEqual(scene.voxtart_tiler_generated_pattern, "CHECKER")
        self.assertEqual(scene.voxtart_tiler_generated_width, 16)
        self.assertEqual(scene.voxtart_tiler_generated_height, 16)
        self.assertEqual(scene.voxtart_tiler_generated_repeat_x, 2)
        self.assertEqual(scene.voxtart_tiler_generated_repeat_y, 2)
        self.assertAlmostEqual(scene.voxtart_grid_size, 1.0)

    def test_character_editor_rebuild_assigns_and_clears(self):
        scene = bpy.context.scene
        previous_gallery_path = scene.voxtart_blob_gallery_path
        before_names = {obj.name for obj in bpy.data.objects}
        for property_name in (
            character_editor_ui.CHARACTER_RECIPE_PROPERTY,
            character_editor_ui.CHARACTER_ID_PROPERTY,
        ):
            if property_name in scene:
                del scene[property_name]
        try:
            with tempfile.TemporaryDirectory() as directory:
                scene.voxtart_blob_gallery_path = directory
                gallery = BlobGallery(Path(directory))
                gallery.save(_gallery_entry("Native Root", "native_root", (0, 0, 1)))
                gallery.save(_gallery_entry("Native Torso", "native_torso", (0, 0, -1)))

                self.assertEqual(
                    bpy.ops.voxtart.character_assign_blob(slot="ROOT", blob_id="native_root"),
                    {"FINISHED"},
                )
                self.assertEqual(
                    bpy.ops.voxtart.character_assign_blob(slot="TORSO", blob_id="native_torso"),
                    {"FINISHED"},
                )

                character_id = str(scene[character_editor_ui.CHARACTER_ID_PROPERTY])
                generated = {
                    str(obj.get(character_editor_ui.CHARACTER_SLOT_PROPERTY)): obj
                    for obj in bpy.data.objects
                    if obj.get(character_editor_ui.CHARACTER_OBJECT_ID_PROPERTY) == character_id
                }
                self.assertEqual(set(generated), {"ROOT", "TORSO"})
                self.assertIs(generated["TORSO"].parent, generated["ROOT"])
                self.assertIn("voxtart_character_snap", generated["TORSO"])
                snap = json.loads(generated["TORSO"]["voxtart_character_snap"])
                self.assertEqual(snap["target"], generated["ROOT"].name)

                recipe = CharacterRecipe.decode(scene[character_editor_ui.CHARACTER_RECIPE_PROPERTY])
                self.assertEqual({part.slot for part in recipe.parts}, {"ROOT", "TORSO"})
                self.assertEqual(recipe.get("TORSO").parent_slot, "ROOT")

                self.assertEqual(bpy.ops.voxtart.character_rebuild(), {"FINISHED"})
                rebuilt = tuple(
                    obj
                    for obj in bpy.data.objects
                    if obj.get(character_editor_ui.CHARACTER_OBJECT_ID_PROPERTY) == character_id
                )
                self.assertEqual(len(rebuilt), 2)

                self.assertEqual(
                    bpy.ops.voxtart.character_remove_part(slot="TORSO"),
                    {"FINISHED"},
                )
                recipe = CharacterRecipe.decode(scene[character_editor_ui.CHARACTER_RECIPE_PROPERTY])
                self.assertEqual({part.slot for part in recipe.parts}, {"ROOT"})
                self.assertEqual(
                    tuple(
                        obj
                        for obj in bpy.data.objects
                        if obj.get(character_editor_ui.CHARACTER_SLOT_PROPERTY) == "TORSO"
                    ),
                    (),
                )

                self.assertEqual(bpy.ops.voxtart.character_clear(), {"FINISHED"})
                self.assertNotIn(character_editor_ui.CHARACTER_RECIPE_PROPERTY, scene)
                self.assertNotIn(character_editor_ui.CHARACTER_ID_PROPERTY, scene)
                self.assertFalse(
                    any(
                        obj.get(character_editor_ui.CHARACTER_OBJECT_ID_PROPERTY) == character_id
                        for obj in bpy.data.objects
                    )
                )
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)
            scene.voxtart_blob_gallery_path = previous_gallery_path
            for property_name in (
                character_editor_ui.CHARACTER_RECIPE_PROPERTY,
                character_editor_ui.CHARACTER_ID_PROPERTY,
            ):
                if property_name in scene:
                    del scene[property_name]

    def test_optimizer_operator_replaces_mesh_and_preserves_source(self):
        before_names = {obj.name for obj in bpy.data.objects}
        obj = operators._create_asset_object(bpy.context, "Native Optimizer", VoxelAsset.box(2))
        source_payload = obj[operators.ASSET_PROPERTY]
        try:
            self.assertTrue(optimizer_ui.VOXTART_OT_optimize_mesh.poll(bpy.context))
            self.assertEqual(
                bpy.ops.voxtart.optimize_mesh(
                    backend="greedy",
                    weld_vertices=True,
                    remove_degenerate=True,
                    remove_duplicate=True,
                    triangulate=False,
                    compact_materials=True,
                ),
                {"FINISHED"},
            )
            greedy_report = optimizer_ui._report_from_object(obj)
            self.assertEqual(greedy_report["backend"], "greedy")
            self.assertGreater(greedy_report["source_vertices"], 0)
            self.assertGreater(greedy_report["target_faces"], 0)
            self.assertEqual(obj[operators.ASSET_PROPERTY], source_payload)
            self.assertGreater(len(obj.data.materials), 0)

            self.assertEqual(
                bpy.ops.voxtart.optimize_mesh(
                    backend="marching",
                    weld_vertices=True,
                    remove_degenerate=True,
                    remove_duplicate=True,
                    triangulate=True,
                    compact_materials=True,
                ),
                {"FINISHED"},
            )
            marching_report = optimizer_ui._report_from_object(obj)
            self.assertEqual(marching_report["backend"], "marching")
            self.assertGreater(marching_report["target_faces"], 0)
            self.assertTrue(all(len(polygon.vertices) == 3 for polygon in obj.data.polygons))
            self.assertEqual(obj[operators.ASSET_PROPERTY], source_payload)
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_voxel_authoring_operators_persist_reports(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            self.assertEqual(
                bpy.ops.voxtart.add_voxel_cube(size=2, grid_size=1.0),
                {"FINISHED"},
            )
            obj = bpy.context.active_object
            self.assertIsNotNone(obj)
            self.assertEqual(len(operators._load_asset(obj).voxels), 8)

            self.assertEqual(
                bpy.ops.voxtart.add_voxel(cell=(3, 0, 0), palette_index=0),
                {"FINISHED"},
            )
            self.assertIn((3, 0, 0), operators._load_asset(obj).voxels)
            self.assertEqual(
                bpy.ops.voxtart.set_protected_voxel(cell=(3, 0, 0), protected=True),
                {"FINISHED"},
            )
            self.assertEqual(
                bpy.ops.voxtart.remove_voxel(cell=(3, 0, 0), allow_protected=False),
                {"FINISHED"},
            )
            self.assertIn((3, 0, 0), operators._load_asset(obj).voxels)
            self.assertEqual(
                bpy.ops.voxtart.remove_voxel(cell=(3, 0, 0), allow_protected=True),
                {"FINISHED"},
            )
            self.assertNotIn((3, 0, 0), operators._load_asset(obj).voxels)

            self.assertEqual(
                bpy.ops.voxtart.transform_asset(
                    operation="TRANSLATE",
                    axis="X",
                    degrees="90",
                    offset=(1, 0, 0),
                    rebase=False,
                ),
                {"FINISHED"},
            )
            report = json.loads(obj["voxtart_volume_transform_report"])
            self.assertEqual(report["operation"], "TRANSLATE")
            self.assertEqual(report["offset"], [1, 0, 0])
            self.assertEqual(bpy.ops.voxtart.rebuild_asset(backend="greedy"), {"FINISHED"})
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_voxel_editor_stroke_and_history_round_trip(self):
        before_names = {obj.name for obj in bpy.data.objects}
        scene = bpy.context.scene
        previous = (
            scene.voxtart_editor_mode,
            scene.voxtart_editor_shape,
            scene.voxtart_editor_radius,
        )
        try:
            obj = operators._create_asset_object(bpy.context, "Editor Stroke", VoxelAsset.box(1))
            scene.voxtart_editor_mode = "ADD"
            scene.voxtart_editor_shape = "VOXEL"
            scene.voxtart_editor_radius = 1
            class EditorHarness:
                _load_state = voxel_editor.VOXTART_OT_voxel_editor._load_state
                _begin_stroke = voxel_editor.VOXTART_OT_voxel_editor._begin_stroke
                _stamp = voxel_editor.VOXTART_OT_voxel_editor._stamp
                _apply_cells = voxel_editor.VOXTART_OT_voxel_editor._apply_cells
                _commit_stroke = voxel_editor.VOXTART_OT_voxel_editor._commit_stroke
                _sync = voxel_editor.VOXTART_OT_voxel_editor._sync
                _record_history = voxel_editor.VOXTART_OT_voxel_editor._record_history
                _restore_history = voxel_editor.VOXTART_OT_voxel_editor._restore_history
                _cancel_stroke = voxel_editor.VOXTART_OT_voxel_editor._cancel_stroke
                _modal = voxel_editor.VOXTART_OT_voxel_editor.modal

            editor = EditorHarness()
            editor.obj = obj
            editor.scene = scene
            editor._load_state(bpy.context)
            editor.history = [voxel_editor._copy_history(editor.asset, editor.stack, editor.selection)]
            editor.history_index = 0
            editor.transaction = None
            editor._begin_stroke()
            hit = voxel_editor.RayHit((0, 0, 0), 0, 0.0, (1, 0, 0))
            editor._apply_cells(editor._stamp(hit))
            editor._commit_stroke(bpy.context)
            self.assertIn((1, 0, 0), operators._load_asset(obj).voxels)
            self.assertEqual(editor.history_index, 1)

            self.assertEqual(
                editor._modal(
                    bpy.context,
                    SimpleNamespace(type="Z", value="PRESS", ctrl=True, shift=False),
                ),
                {"RUNNING_MODAL"},
            )
            self.assertNotIn((1, 0, 0), operators._load_asset(obj).voxels)
            self.assertEqual(
                editor._modal(
                    bpy.context,
                    SimpleNamespace(type="Y", value="PRESS", ctrl=True, shift=False),
                ),
                {"RUNNING_MODAL"},
            )
            self.assertIn((1, 0, 0), operators._load_asset(obj).voxels)
        finally:
            (
                scene.voxtart_editor_mode,
                scene.voxtart_editor_shape,
                scene.voxtart_editor_radius,
            ) = previous
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_voxel_editor_stroke_preview_recovery_and_scene_transforms(self):
        before_names = {obj.name for obj in bpy.data.objects}
        scene = bpy.context.scene
        previous_workspace = scene.voxtart_editor_workspace
        previous_mode = scene.voxtart_editor_mode
        previous_shape = scene.voxtart_editor_shape
        previous_radius = scene.voxtart_editor_radius
        previous_depth = scene.voxtart_editor_depth
        original_replace = operators._replace_asset_mesh
        try:
            obj = operators._create_asset_object(bpy.context, "Editor End To End", VoxelAsset.box(1))
            scene.voxtart_editor_workspace = "EDIT"
            scene.voxtart_editor_mode = "ADD"
            scene.voxtart_editor_shape = "VOXEL"

            class EditorHarness:
                _load_state = voxel_editor.VOXTART_OT_voxel_editor._load_state
                _begin_stroke = voxel_editor.VOXTART_OT_voxel_editor._begin_stroke
                _apply_cells = voxel_editor.VOXTART_OT_voxel_editor._apply_cells
                _update_working_state = voxel_editor.VOXTART_OT_voxel_editor._update_working_state
                _persist_recovery = voxel_editor.VOXTART_OT_voxel_editor._persist_recovery
                _clear_recovery = voxel_editor.VOXTART_OT_voxel_editor._clear_recovery
                _commit_stroke = voxel_editor.VOXTART_OT_voxel_editor._commit_stroke
                _cancel_stroke = voxel_editor.VOXTART_OT_voxel_editor._cancel_stroke
                _sync = voxel_editor.VOXTART_OT_voxel_editor._sync
                _stamp = voxel_editor.VOXTART_OT_voxel_editor._stamp
                _reset_transform = voxel_editor.VOXTART_OT_voxel_editor._reset_transform
                _begin_transform = voxel_editor.VOXTART_OT_voxel_editor._begin_transform
                _transform_pose = voxel_editor.VOXTART_OT_voxel_editor._transform_pose
                _update_transform_pose = voxel_editor.VOXTART_OT_voxel_editor._update_transform_pose
                _transform_move = voxel_editor.VOXTART_OT_voxel_editor._transform_move
                _commit_transform = voxel_editor.VOXTART_OT_voxel_editor._commit_transform
                _record_history = voxel_editor.VOXTART_OT_voxel_editor._record_history
                _tag_redraw = voxel_editor.VOXTART_OT_voxel_editor._tag_redraw
                _copy_selection = voxel_editor.VOXTART_OT_voxel_editor._copy_selection
                _load_clipboard = voxel_editor.VOXTART_OT_voxel_editor._load_clipboard

            stamp = EditorHarness()
            stamp.scene = scene
            stamp.stroke_anchor = None
            hit = voxel_editor.RayHit((0, 0, 0), 0, 0.0, (1, 0, 0))
            scene.voxtart_editor_radius = 2
            scene.voxtart_editor_depth = 2
            scene.voxtart_editor_mode = "ADD"
            scene.voxtart_editor_shape = "FACE"
            self.assertEqual(len(stamp._stamp(hit)), 18)
            scene.voxtart_editor_mode = "ATTACH"
            scene.voxtart_editor_shape = "VOXEL"
            scene.voxtart_editor_radius = 1
            scene.voxtart_editor_depth = 1
            self.assertEqual(stamp._stamp(hit), ((1, 0, 0),))
            scene.voxtart_editor_radius = 2
            scene.voxtart_editor_depth = 2
            scene.voxtart_editor_mode = "ERASE"
            scene.voxtart_editor_shape = "FACE"
            self.assertIn((-1, 0, 0), stamp._stamp(hit))
            scene.voxtart_editor_mode = "PAINT"
            self.assertIn((-1, 0, 0), stamp._stamp(hit))
            scene.voxtart_editor_mode = "ADD"
            scene.voxtart_editor_shape = "VOXEL"
            scene.voxtart_editor_radius = 1
            scene.voxtart_editor_depth = 1
            editor = EditorHarness()
            editor.obj = obj
            editor.scene = scene
            editor._last_redraw = 0.0
            editor._load_state(bpy.context)
            editor.history = [voxel_editor._copy_history(editor.asset, editor.stack, editor.selection, obj)]
            editor.history_index = 0
            editor.transaction = None
            editor.preview_cells = ()
            calls = []
            operators._replace_asset_mesh = lambda *args, **kwargs: calls.append(args[1].to_dict())
            editor._begin_stroke()
            editor._apply_cells(((1, 0, 0),))
            editor._update_working_state()
            editor._apply_cells(((2, 0, 0),))
            editor._update_working_state()
            self.assertEqual(calls, [])
            self.assertIn(voxel_editor.EDITOR_STROKE_PROPERTY, obj)
            editor._commit_stroke(bpy.context)
            self.assertEqual(len(calls), 1)
            self.assertEqual(editor.history_index, 1)
            self.assertNotIn(voxel_editor.EDITOR_STROKE_PROPERTY, obj)
            scene.voxtart_editor_mode = "SELECT"
            editor.selection = ((1, 0, 0),)
            editor._reset_transform()
            editor._begin_transform("MOVE")
            editor._transform_move((2, 0, 0))
            self.assertEqual(tuple(sorted(editor.transform_current)), ((3, 0, 0),))
            editor._commit_transform(bpy.context)
            self.assertEqual(editor.history_index, 2)
            self.assertIn((3, 0, 0), editor.stack.active.voxels)
            scene.voxtart_editor_mode = "ADD"

            editor._begin_stroke()
            editor._apply_cells(((3, 0, 0),))
            editor._update_working_state()
            editor._persist_recovery(force=True)
            interrupted = EditorHarness()
            interrupted.obj = obj
            interrupted.scene = scene
            interrupted._load_state(bpy.context)
            interrupted._recover_interrupted_state = voxel_editor.VOXTART_OT_voxel_editor._recover_interrupted_state.__get__(
                interrupted,
                EditorHarness,
            )
            self.assertTrue(interrupted._recover_interrupted_state())
            self.assertIn((3, 0, 0), interrupted.asset.voxels)
            editor._cancel_stroke(bpy.context)
            self.assertNotIn(voxel_editor.EDITOR_STROKE_PROPERTY, obj)

            editor.selection = ((2, 0, 0), (3, 0, 0))
            editor._copy_selection()
            clipboard = obj[voxel_editor.EDITOR_CLIPBOARD_PROPERTY]
            voxel_editor._CLIPBOARD = ()
            recovery_reader = EditorHarness()
            recovery_reader.obj = obj
            recovery_reader.scene = scene
            recovery_reader._load_state(bpy.context)
            recovery_reader._load_clipboard = voxel_editor.VOXTART_OT_voxel_editor._load_clipboard.__get__(
                recovery_reader,
                EditorHarness,
            )
            recovery_reader._load_clipboard()
            self.assertEqual(obj[voxel_editor.EDITOR_CLIPBOARD_PROPERTY], clipboard)
            self.assertEqual(len(voxel_editor._CLIPBOARD), 2)
            obj[voxel_editor.EDITOR_CLIPBOARD_PROPERTY] = "[[0,0,0,999]]"
            voxel_editor._CLIPBOARD = ()
            recovery_reader._load_clipboard()
            self.assertEqual(voxel_editor._CLIPBOARD, ())
            self.assertNotIn(voxel_editor.EDITOR_CLIPBOARD_PROPERTY, obj)

            scene.voxtart_editor_workspace = "SCENE"
            scene_harness = EditorHarness()
            scene_harness.obj = obj
            scene_harness.scene = scene
            scene_harness._last_redraw = 0.0
            scene_harness._load_state(bpy.context)
            scene_harness.history = [voxel_editor._copy_history(scene_harness.asset, scene_harness.stack, scene_harness.selection, obj)]
            scene_harness.history_index = 0
            scene_harness.transaction = None
            scene_harness._reset_scene_transform = voxel_editor.VOXTART_OT_voxel_editor._reset_scene_transform.__get__(
                scene_harness,
                EditorHarness,
            )
            scene_harness._begin_scene_transform = voxel_editor.VOXTART_OT_voxel_editor._begin_scene_transform.__get__(
                scene_harness,
                EditorHarness,
            )
            scene_harness._update_scene_transform = voxel_editor.VOXTART_OT_voxel_editor._update_scene_transform.__get__(
                scene_harness,
                EditorHarness,
            )
            scene_harness._commit_scene_transform = voxel_editor.VOXTART_OT_voxel_editor._commit_scene_transform.__get__(
                scene_harness,
                EditorHarness,
            )
            scene_harness._reset_scene_transform()
            original_location = tuple(obj.location)
            scene_harness._begin_scene_transform("MOVE")
            scene_harness._update_scene_transform(SimpleNamespace(type="RIGHT_ARROW", shift=False))
            self.assertEqual(tuple(obj.location), (original_location[0] + scene_harness.asset.grid_size, original_location[1], original_location[2]))
            scene_harness._commit_scene_transform()
            self.assertEqual(scene_harness.history_index, 1)
        finally:
            operators._replace_asset_mesh = original_replace
            scene.voxtart_editor_workspace = previous_workspace
            scene.voxtart_editor_mode = previous_mode
            scene.voxtart_editor_shape = previous_shape
            scene.voxtart_editor_radius = previous_radius
            scene.voxtart_editor_depth = previous_depth
            voxel_editor._CLIPBOARD = ()
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_voxel_editor_workspace_switch_and_shortcut_scope(self):
        scene = bpy.context.scene
        previous_workspace = scene.voxtart_editor_workspace
        previous_mode = scene.voxtart_editor_mode
        try:
            class ModeHarness:
                modal = voxel_editor.VOXTART_OT_voxel_editor.modal
                _tag_redraw = voxel_editor.VOXTART_OT_voxel_editor._tag_redraw
                _modal_scene = voxel_editor.VOXTART_OT_voxel_editor._modal_scene

            editor = ModeHarness()
            editor.scene = scene
            editor._last_redraw = 0.0
            editor.transform_active = False
            editor.scene_transform_active = False
            editor.status = ""
            scene.voxtart_editor_workspace = "EDIT"
            scene.voxtart_editor_mode = "ADD"
            self.assertEqual(
                editor.modal(
                    bpy.context,
                    SimpleNamespace(type="TAB", value="PRESS", ctrl=False, alt=False, shift=False),
                ),
                {"RUNNING_MODAL"},
            )
            self.assertEqual(scene.voxtart_editor_workspace, "SCENE")
            self.assertEqual(
                editor.modal(
                    bpy.context,
                    SimpleNamespace(type="B", value="PRESS", ctrl=False, alt=False, shift=False),
                ),
                {"PASS_THROUGH"},
            )
            self.assertEqual(scene.voxtart_editor_mode, "ADD")
            self.assertEqual(
                editor.modal(
                    bpy.context,
                    SimpleNamespace(type="TAB", value="PRESS", ctrl=False, alt=False, shift=False),
                ),
                {"RUNNING_MODAL"},
            )
            self.assertEqual(scene.voxtart_editor_workspace, "EDIT")
        finally:
            scene.voxtart_editor_workspace = previous_workspace
            scene.voxtart_editor_mode = previous_mode

    def test_palette_workflow_preserves_indices_and_picker_state(self):
        before_names = {obj.name for obj in bpy.data.objects}
        scene = bpy.context.scene
        previous_enforce = scene.voxtart_palette_enforce
        previous_name = scene.voxtart_palette_name
        try:
            colors = [
                (1.0, 0.0, 0.0, 1.0),
                (0.0, 1.0, 0.0, 1.0),
                (0.0, 0.0, 1.0, 1.0),
            ]
            obj = operators._create_asset_object(
                bpy.context,
                "Palette End To End",
                VoxelAsset(palette=colors, voxels={(0, 0, 0): 0, (1, 0, 0): 1}),
            )
            scene.voxtart_palette_enforce = True
            palette_ui._set_palette(scene, colors, name="Picker Test")
            self.assertEqual(
                bpy.ops.voxtart.palette_select(index=1),
                {"FINISHED"},
            )
            self.assertEqual(scene.voxtart_palette_index, 1)
            self.assertEqual(scene.voxtart_palette_hover_index, 1)
            self.assertEqual(
                bpy.ops.voxtart.palette_select(index=0, extend=True),
                {"FINISHED"},
            )
            self.assertEqual(
                [index for index, item in enumerate(scene.voxtart_palette_colors) if item.selected],
                [0, 1],
            )
            self.assertEqual(
                bpy.ops.voxtart.palette_reorder(index=0, target_index=2),
                {"FINISHED"},
            )
            reordered = operators._load_asset(obj)
            self.assertEqual(reordered.palette, [colors[1], colors[2], colors[0]])
            self.assertEqual(reordered.voxels[(0, 0, 0)], 2)
            self.assertEqual(reordered.voxels[(1, 0, 0)], 0)
            self.assertEqual(
                bpy.ops.voxtart.palette_select(index=2),
                {"FINISHED"},
            )
            self.assertEqual(
                bpy.ops.voxtart.palette_select(index=0, range_select=True),
                {"FINISHED"},
            )
            self.assertEqual(
                [index for index, item in enumerate(scene.voxtart_palette_colors) if item.selected],
                [0, 1, 2],
            )
            self.assertEqual(
                bpy.ops.voxtart.palette_duplicate(index=0),
                {"FINISHED"},
            )
            self.assertEqual(len(scene.voxtart_palette_colors), 6)
            self.assertEqual(
                bpy.ops.voxtart.palette_compact(),
                {"FINISHED"},
            )
            self.assertEqual(len(scene.voxtart_palette_colors), 2)
            self.assertEqual(
                bpy.ops.voxtart.palette_gradient(
                    start=(0.0, 0.0, 0.0, 1.0),
                    end=(1.0, 1.0, 1.0, 1.0),
                    count=3,
                ),
                {"FINISHED"},
            )
            self.assertEqual(len(scene.voxtart_palette_colors), 3)
            self.assertEqual(tuple(scene.voxtart_palette_colors[0].color), (0.0, 0.0, 0.0, 1.0))
            self.assertEqual(tuple(scene.voxtart_palette_colors[-1].color), (1.0, 1.0, 1.0, 1.0))
        finally:
            scene.voxtart_palette_enforce = previous_enforce
            scene.voxtart_palette_name = previous_name
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_voxelizer_operator_keeps_source_link(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            bpy.ops.mesh.primitive_cube_add(size=2.0, location=(3.0, 0.0, 0.0))
            source = bpy.context.active_object
            source_name = source.name
            self.assertEqual(
                bpy.ops.voxtart.voxelize_active(
                    grid_size=1.0,
                    mode="SURFACE",
                    shell_thickness=1,
                ),
                {"FINISHED"},
            )
            target = bpy.context.active_object
            self.assertIsNot(target, source)
            self.assertEqual(target[operators.VOXELIZER_SOURCE_PROPERTY], source_name)
            self.assertEqual(target[operators.VOXELIZER_MODE_PROPERTY], "SURFACE")
            self.assertGreater(len(operators._load_asset(target).voxels), 0)

            self.assertEqual(bpy.ops.voxtart.refresh_voxelizer(), {"FINISHED"})
            report = json.loads(target["voxtart_voxelizer_report"])
            self.assertEqual(report["source"], source_name)
            self.assertEqual(report["mode"], "SURFACE")
            self.assertGreater(report["voxels"], 0)
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)

    def test_generated_rig_and_game_rig_pipeline(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            bpy.ops.mesh.primitive_cube_add(size=2.0)
            mesh = bpy.context.active_object
            blockout = animation.create_blockout_rig(bpy.context, mesh, bind=False)
            armature = bpy.data.objects[blockout["armature"]]
            self.assertEqual(blockout["bone_count"], 20)
            self.assertTrue(armature.get("voxtart_generated_rig"))
            self.assertEqual(len(armature.data.bones), 20)
            self.assertEqual(armature.data.bones["head"].parent.name, "neck")

            bind_result = animation.bind_mesh_to_armature(mesh, armature)
            self.assertEqual(bind_result["mesh"], mesh.name)
            self.assertEqual(bind_result["armature"], armature.name)
            self.assertTrue(any(modifier.type == "ARMATURE" for modifier in mesh.modifiers))

            for bone in armature.data.bones:
                bone.use_deform = True
            for selected in tuple(bpy.context.selected_objects):
                selected.select_set(False)
            mesh.select_set(True)
            armature.select_set(True)
            bpy.context.view_layer.objects.active = armature
            extracted = animation.extract_game_rig(
                bpy.context,
                armature,
                mode="DEFORM_ONLY",
                hierarchy="PRESERVE",
                bind_meshes=False,
            )
            game_rig = bpy.data.objects[extracted["game_rig"]]
            self.assertTrue(game_rig.get("voxtart_game_rig"))
            self.assertEqual(extracted["bone_count"], 20)
            self.assertEqual(len(game_rig.data.bones), 20)
            self.assertEqual(game_rig.get("voxtart_game_rig_source"), armature.name)
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)
    def test_large_asset_and_overlapping_layers_round_trip(self):
        before_names = {obj.name for obj in bpy.data.objects}
        try:
            palette = [(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0)]
            asset = VoxelAsset(
                palette=palette,
                voxels={
                    (x, y, z): 0
                    for x in range(12)
                    for y in range(12)
                    for z in range(12)
                },
            )
            stack = operators.LayerStack.from_asset(asset)
            overlay = stack.add("Overlay")
            overlay.voxels = {(x, y, z): 1 for x in range(3) for y in range(3) for z in range(3)}
            obj = operators._create_asset_object(
                bpy.context,
                "Large Layer Overlap",
                asset,
                layer_stack=stack,
            )
            loaded = operators._load_asset(obj)
            loaded_stack = operators._load_layer_stack(obj, loaded)
            self.assertEqual(len(loaded.voxels), 12 ** 3)
            self.assertEqual(len(loaded_stack.layers), 2)
            self.assertEqual(loaded_stack.layers[1].voxels[(0, 0, 0)], 1)
            self.assertEqual(loaded.voxels[(0, 0, 0)], 1)
        finally:
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)



    def test_image_reconstruction_operator_loads_calibrated_source(self):
        before_names = {obj.name for obj in bpy.data.objects}
        image = None
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            image_path = Path(handle.name)
        try:
            image = bpy.data.images.new("VOXTART Reconstruction Fixture", width=2, height=1, alpha=True)
            image.pixels = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
            image.filepath_raw = str(image_path)
            image.file_format = "PNG"
            image.save()
            bpy.data.images.remove(image)
            image = None

            result = bpy.ops.voxtart.reconstruct_image(
                filepath=str(image_path),
                axis="Z",
                direction="1",
                origin=(2, 3, 4),
                depth=1,
                background_mode="ALPHA",
                max_colors=8,
                mode="SURFACE",
            )
            self.assertEqual(result, {"FINISHED"})
            obj = bpy.context.active_object
            self.assertIsNotNone(obj)
            asset = operators._load_asset(obj)
            self.assertEqual(asset.voxels, {(2, 3, 4): 0})
            self.assertEqual(asset.provenance["image_width"], "2")
            self.assertEqual(asset.provenance["image_height"], "1")
            self.assertEqual(obj[reconstruct_ui.RECONSTRUCTION_SOURCE_PROPERTY], str(image_path))
            report = json.loads(obj[reconstruct_ui.RECONSTRUCTION_REPORT_PROPERTY])
            self.assertEqual(report["occupied_voxels"], 1)
            self.assertEqual(report["unresolved_voxels"], 0)
            self.assertEqual(report["missing_pixels"], 0)
            self.assertEqual(report["extra_pixels"], 0)
            self.assertEqual(
                json.loads(obj[reconstruct_ui.RECONSTRUCTION_UNRESOLVED_CELLS_PROPERTY]),
                [],
            )
            overlay_name = obj[reconstruct_ui.RECONSTRUCTION_OVERLAY_PROPERTY]
            self.assertIn(overlay_name, bpy.data.objects)
            self.assertTrue(bpy.data.objects[overlay_name].hide_viewport)
        finally:
            if image is not None and image.name in bpy.data.images:
                bpy.data.images.remove(image)
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)
            image_path.unlink(missing_ok=True)


    def test_multiview_reconstruction_operator_resolves_opposing_sources(self):
        before_names = {obj.name for obj in bpy.data.objects}
        scene = bpy.context.scene
        slots = (1, 2)
        slot_fields = ("filepath", "axis", "direction", "origin", "depth", "priority")
        previous_slots = {
            (slot, field): getattr(scene, reconstruct_ui._slot_property(slot, field))
            for slot in slots
            for field in slot_fields
        }
        shared_fields = (
            "voxtart_reconstruction_background",
            "voxtart_reconstruction_alpha_threshold",
            "voxtart_reconstruction_background_color",
            "voxtart_reconstruction_background_tolerance",
            "voxtart_reconstruction_max_colors",
            "voxtart_reconstruction_mode",
        )
        previous_shared = {name: getattr(scene, name) for name in shared_fields}
        paths = []

        def write_pixel(path, name, color):
            image = bpy.data.images.new(name, width=1, height=1, alpha=True)
            try:
                image.pixels = tuple(color)
                image.filepath_raw = str(path)
                image.file_format = "PNG"
                image.save()
            finally:
                bpy.data.images.remove(image)

        try:
            for index, color in enumerate(((1.0, 0.0, 0.0, 1.0), (1.0, 0.0, 0.0, 1.0)), 1):
                with tempfile.NamedTemporaryFile(suffix=f"_view{index}.png", delete=False) as handle:
                    path = Path(handle.name)
                paths.append(path)
                write_pixel(path, f"VOXTART Multiview Fixture {index}", color)

            for slot, path, direction, origin in (
                (1, paths[0], "1", (0, 0, 0)),
                (2, paths[1], "-1", (0, 0, 1)),
            ):
                setattr(scene, reconstruct_ui._slot_property(slot, "filepath"), str(path))
                setattr(scene, reconstruct_ui._slot_property(slot, "axis"), "Z")
                setattr(scene, reconstruct_ui._slot_property(slot, "direction"), direction)
                setattr(scene, reconstruct_ui._slot_property(slot, "origin"), origin)
                setattr(scene, reconstruct_ui._slot_property(slot, "depth"), 1)
                setattr(scene, reconstruct_ui._slot_property(slot, "priority"), slot - 1)
            scene.voxtart_reconstruction_background = "ALPHA"
            scene.voxtart_reconstruction_alpha_threshold = 0.0
            scene.voxtart_reconstruction_max_colors = 8
            scene.voxtart_reconstruction_mode = "SURFACE"

            result = bpy.ops.voxtart.reconstruct_views()
            self.assertEqual(result, {"FINISHED"})
            obj = bpy.context.active_object
            asset = operators._load_asset(obj)
            self.assertEqual(asset.voxels, {(0, 0, 0): 0, (0, 0, 1): 0})
            self.assertEqual(asset.provenance["view_count"], "2")
            report = json.loads(obj[reconstruct_ui.RECONSTRUCTION_REPORT_PROPERTY])
            self.assertEqual(report["view_count"], 2)
            self.assertEqual(report["unresolved_voxels"], 0)
            self.assertEqual(report["conflicting_rays"], 0)
            self.assertEqual(report["missing_pixels"], 0)
            self.assertEqual(report["extra_pixels"], 0)
            self.assertEqual(len(json.loads(obj[reconstruct_ui.RECONSTRUCTION_SOURCES_PROPERTY])), 2)
            self.assertEqual(len(json.loads(obj[reconstruct_ui.RECONSTRUCTION_VIEWS_PROPERTY])), 2)
        finally:
            for (slot, field), value in previous_slots.items():
                setattr(scene, reconstruct_ui._slot_property(slot, field), value)
            for name, value in previous_shared.items():
                setattr(scene, name, value)
            _remove_objects(obj for obj in bpy.data.objects if obj.name not in before_names)
            for path in paths:
                path.unlink(missing_ok=True)

    def test_desktop_workspace_contract(self):
        desktop_ui.configure_workspace()
        scene = bpy.context.scene
        starter = bpy.data.objects.get(desktop_ui.DEFAULT_PYRAMID_OBJECT_NAME)
        self.assertIsNotNone(starter)
        self.assertIs(bpy.context.active_object, starter)
        asset = operators._load_asset(starter)
        self.assertEqual(len(asset.voxels), 56)
        self.assertEqual(
            {level: sum(cell[2] == level for cell in asset.voxels) for level in (0, 1, 2)},
            {0: 36, 1: 16, 2: 4},
        )
        self.assertEqual(asset.palette, [desktop_ui.DEFAULT_PYRAMID_COLOR])
        self.assertEqual(asset.provenance["asset_type"], "starter_pyramid")
        self.assertTrue(starter[desktop_ui.DEFAULT_PYRAMID_PROPERTY])
        seam_name = starter.get(operators.SEAM_OVERLAY_PROPERTY)
        self.assertIsInstance(seam_name, str)
        seam = bpy.data.objects[seam_name]
        self.assertFalse(seam.hide_viewport)
        self.assertEqual(seam.display_type, "WIRE")
        self.assertEqual(seam["voxtart_overlay_kind"], "Seams")
        for actual, expected in zip(seam.color, operators.SEAM_OVERLAY_COLOR):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertEqual(scene.voxtart_editor_mode, "ADD")
        self.assertEqual(scene.voxtart_editor_workspace, "EDIT")
        self.assertTrue(scene.get(desktop_ui.DESKTOP_MODE_PROPERTY))
        self.assertEqual(bpy.context.window.workspace.name, "VOXTART")
        self.assertIs(
            getattr(bpy.types, "VOXTART_PT_desktop_home"),
            desktop_ui.VOXTART_PT_desktop_home,
        )
        self.assertEqual(desktop_ui.VOXTART_PT_desktop_home.bl_label, "Start Here")
        self.assertEqual(desktop_ui.VOXTART_PT_desktop_home.bl_category, "Tool")
        self.assertIs(
            getattr(bpy.types, "VOXTART_OT_desktop_shell"),
            desktop_shell.VOXTART_OT_desktop_shell,
        )
        self.assertEqual(desktop_shell.SHELL_MODES, ("HOME", "BUILD", "PAINT", "CHARACTER"))
        self.assertEqual(desktop_shell._active_mode(scene), "HOME")
        desktop_shell._set_mode(scene, "PAINT")
        self.assertEqual(desktop_shell._active_mode(scene), "PAINT")
        desktop_shell._set_mode(scene, "unknown")
        self.assertEqual(desktop_shell._active_mode(scene), "HOME")
        self.assertEqual(desktop_branding.WINDOW_TITLE, "VoxTart")
        self.assertTrue(desktop_branding.ICON_PATH.is_file())
        self.assertFalse(desktop_branding.apply())
        self.assertEqual(desktop_splash.SPLASH_TITLE, "VoxStart")
        self.assertEqual(desktop_splash.SPLASH_VERSION, "0.0.1-dogfood-(build)")
        self.assertTrue(desktop_splash.ICON_IMAGE_PATH.is_file())
        self.assertFalse(desktop_splash.show())
        self.assertGreater(len(bpy.context.scene.voxtart_palette_colors), 0)
        self.assertTrue(scene.get(desktop_ui.DESKTOP_FULLSCREEN_PROPERTY))
        self.assertFalse(bpy.context.screen.show_statusbar)
        view_areas = tuple(
            area
            for area in bpy.context.screen.areas
            if area.type == "VIEW_3D"
        )
        if view_areas:
            self.assertTrue(any(area.spaces.active.show_region_ui for area in view_areas))
            self.assertFalse(any(area.spaces.active.show_region_header for area in view_areas))
            self.assertFalse(any(area.spaces.active.show_region_footer for area in view_areas))

    def test_operator_property_contract_and_error_path(self):
        declared = set(getattr(bloformer_ui.VOXTART_OT_bloformer, "__annotations__", ()))
        for name in (
            "mode",
            "shape",
            "center",
            "radius",
            "thickness",
            "axis",
            "palette_index",
            "overwrite",
            "allow_protected",
        ):
            self.assertIn(name, declared)
        self.assertFalse(bloformer_ui.VOXTART_OT_bloformer.poll(bpy.context))
        self.assertFalse(bloformer_ui._is_asset(None))

    def test_animation_and_operator_helpers(self):
        self.assertEqual(animation.canonical_bone_role("upper_arm.L"), "upper_arm_l")
        self.assertEqual(animation.canonical_bone_role("Left_Leg-global"), "lower_leg_l")
        mapping = animation.suggest_bone_mapping(("root", "hand.L"), ("root", "hand.L"))
        self.assertEqual({entry["target"] for entry in mapping}, {"root", "hand.L"})
        status = animation.plugin_status()
        self.assertTrue(status)
        self.assertTrue(all("installed" in entry for entry in status))
        self.assertEqual(bloformer_ui._MODE_ITEMS[0][0], "ADD")
        with self.assertRaises(ValueError):
            bloformer_ui._apply_to_active(bpy.context, {"mode": "ADD"})


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
