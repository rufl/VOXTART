# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Start the bundled Blender runtime as the VOXTART desktop editor."""

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "scripts" / "addons_core"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


if not hasattr(bpy.types, "VOXTART_PT_desktop_home"):
    result = bpy.ops.preferences.addon_enable(module="voxtart")
    if "FINISHED" not in result and not hasattr(bpy.types, "VOXTART_PT_desktop_home"):
        raise RuntimeError(f"Could not enable VOXTART: {result!r}")

from voxtart import desktop_branding, desktop_shell, desktop_splash, desktop_ui  # noqa: E402


desktop_ui.configure_workspace()
splash_shown = desktop_splash.show()
if not splash_shown:
    desktop_shell.open_shell()
branding_applied = desktop_branding.apply()
print(f"VOXTART_SPLASH_READY shown={splash_shown} title={desktop_splash.SPLASH_TITLE} version={desktop_splash.SPLASH_VERSION}")
print(f"VOXTART_BRANDING_READY title={desktop_branding.WINDOW_TITLE} applied={branding_applied} icon={desktop_branding.icon_loaded()}")
print(f"VOXTART_DESKTOP_READY root={ROOT}")
