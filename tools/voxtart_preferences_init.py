# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Create the isolated Blender preferences used by the VOXTART launcher."""

from pathlib import Path

import bpy


PREFERENCES_MARKER = "voxtart-splash-disabled-v1"


bpy.context.preferences.view.show_splash = False
result = bpy.ops.wm.save_userpref()
if "FINISHED" not in result:
    raise RuntimeError(f"Could not save VOXTART preferences: {result!r}")

config_directory = bpy.utils.user_resource("CONFIG", create=True)
if not config_directory:
    raise RuntimeError("Could not resolve the VOXTART Blender config directory")
Path(config_directory, PREFERENCES_MARKER).write_text(
    "show_splash=False\n",
    encoding="utf-8",
)
print(f"VOXTART_PREFERENCES_READY splash=False marker={PREFERENCES_MARKER}")
