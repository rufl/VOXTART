# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Write and reopen a VOXTART recovery/clipboard state in separate Blender runs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "scripts" / "addons_core"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import voxtart as addon  # noqa: E402
from voxtart import operators, voxel_editor  # noqa: E402
from voxtart.voxel import VoxelAsset  # noqa: E402


class _EditorHarness:
    _apply_cells = voxel_editor.VOXTART_OT_voxel_editor._apply_cells
    _begin_stroke = voxel_editor.VOXTART_OT_voxel_editor._begin_stroke
    _copy_selection = voxel_editor.VOXTART_OT_voxel_editor._copy_selection
    _load_clipboard = voxel_editor.VOXTART_OT_voxel_editor._load_clipboard
    _load_state = voxel_editor.VOXTART_OT_voxel_editor._load_state
    _persist_recovery = voxel_editor.VOXTART_OT_voxel_editor._persist_recovery
    _recover_interrupted_state = voxel_editor.VOXTART_OT_voxel_editor._recover_interrupted_state
    _update_working_state = voxel_editor.VOXTART_OT_voxel_editor._update_working_state
    _clear_recovery = voxel_editor.VOXTART_OT_voxel_editor._clear_recovery


def _ensure_registered() -> None:
    if not hasattr(bpy.types, "VOXTART_OT_voxel_editor"):
        addon.register()


def _write_marker(payload: dict) -> None:
    marker = os.environ.get("VOXTART_PERSISTENCE_MARKER")
    if not marker:
        raise RuntimeError("VOXTART_PERSISTENCE_MARKER is not set")
    Path(marker).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_phase() -> None:
    blend_path = os.environ.get("VOXTART_PERSISTENCE_BLEND")
    if not blend_path:
        raise RuntimeError("VOXTART_PERSISTENCE_BLEND is not set")
    _ensure_registered()
    scene = bpy.context.scene
    obj = operators._create_asset_object(
        bpy.context,
        "P0 Persistent Asset",
        VoxelAsset(
            palette=[(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0)],
            voxels={(0, 0, 0): 0},
        ),
    )
    editor = _EditorHarness()
    editor.obj = obj
    editor.scene = scene
    editor._load_state(bpy.context)
    editor.transaction = None
    editor.selection = ((0, 0, 0), (1, 0, 0))
    editor._begin_stroke()
    editor._apply_cells(((1, 0, 0),))
    editor._update_working_state()
    editor._persist_recovery(force=True)
    editor._copy_selection()
    result = bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save persistence fixture: {result!r}")
    _write_marker(
        {
            "phase": "write",
            "recovery_property": voxel_editor.EDITOR_STROKE_PROPERTY in obj,
            "clipboard_property": voxel_editor.EDITOR_CLIPBOARD_PROPERTY in obj,
            "voxel_count": len(editor.asset.voxels),
        }
    )


def _read_phase() -> None:
    _ensure_registered()
    objects = [obj for obj in bpy.data.objects if voxel_editor.EDITOR_STROKE_PROPERTY in obj]
    if len(objects) != 1:
        raise RuntimeError(f"Expected one persisted recovery object, found {len(objects)}")
    obj = objects[0]
    editor = _EditorHarness()
    editor.obj = obj
    editor.scene = bpy.context.scene
    editor._load_state(bpy.context)
    recovered = editor._recover_interrupted_state()
    if not recovered:
        raise RuntimeError("Persisted interrupted stroke was not recovered")
    editor._load_clipboard()
    expected = {(0, 0, 0), (1, 0, 0)}
    if expected - set(editor.asset.voxels):
        raise RuntimeError(f"Recovered voxels missing: {expected - set(editor.asset.voxels)}")
    if len(voxel_editor._CLIPBOARD) != 2:
        raise RuntimeError(f"Persisted clipboard was not restored: {voxel_editor._CLIPBOARD!r}")
    if voxel_editor.EDITOR_STROKE_PROPERTY in obj:
        raise RuntimeError("Recovery property was not cleared after successful recovery")
    _write_marker(
        {
            "phase": "read",
            "recovered": True,
            "voxel_count": len(editor.asset.voxels),
            "clipboard_count": len(voxel_editor._CLIPBOARD),
            "recovery_cleared": voxel_editor.EDITOR_STROKE_PROPERTY not in obj,
        }
    )


phase = os.environ.get("VOXTART_PERSISTENCE_PHASE", "")
if phase == "write":
    _write_phase()
elif phase == "read":
    _read_phase()
else:
    raise RuntimeError(f"Unsupported persistence phase: {phase!r}")
