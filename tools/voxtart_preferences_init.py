# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Create the isolated Blender preferences used by the VOXTART launcher."""

import bpy


bpy.context.preferences.view.show_splash = False
result = bpy.ops.wm.save_userpref()
if "FINISHED" not in result:
    raise RuntimeError(f"Could not save VOXTART preferences: {result!r}")
print("VOXTART_PREFERENCES_READY splash=False")
