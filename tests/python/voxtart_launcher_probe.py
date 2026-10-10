# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Probe executed through ``run_voxtart.cmd`` by the E2E runner."""

from __future__ import annotations

import json
import os
from pathlib import Path

import bpy


required_types = (
    "VOXTART_OT_voxel_editor",
    "VOXTART_OT_palette_select",
    "VOXTART_PT_desktop_home",
)
missing = [name for name in required_types if not hasattr(bpy.types, name)]
if missing:
    raise RuntimeError(f"VOXTART launcher did not register required types: {missing}")

scene = bpy.context.scene
payload = {
    "background": bool(bpy.app.background),
    "voxtart_registered": True,
    "workspace": str(scene.voxtart_editor_workspace),
    "editor_mode": str(scene.voxtart_editor_mode),
    "blender_version": list(bpy.app.version),
}
output_path = os.environ.get("VOXTART_E2E_PROBE_OUTPUT")
if not output_path:
    raise RuntimeError("VOXTART_E2E_PROBE_OUTPUT is not set")
Path(output_path).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
print("VOXTART_LAUNCHER_E2E_OK " + json.dumps(payload, sort_keys=True))
