# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""VoxStart splash surface shown before the VOXTART project shell."""

from __future__ import annotations

from pathlib import Path

import bpy
import gpu
from gpu_extras.presets import draw_texture_2d
from .desktop_shell import _draw_centered, _draw_rect, _draw_text

SPLASH_TITLE = "VoxStart"
SPLASH_VERSION = "0.0.1-dogfood-(build)"
ICON_IMAGE_PATH = Path(__file__).resolve().parents[3] / "assets" / "voxtart_icon_256.png"

_ACTIVE_AREA = None
_DRAW_HANDLE = None
_ICON_IMAGE = None
_ICON_TEXTURE = None

_BG = (0.025, 0.03, 0.045, 0.94)
_CARD = (0.07, 0.08, 0.11, 0.96)
_TEXT = (0.95, 0.96, 0.98, 1.0)
_MUTED = (0.62, 0.67, 0.75, 1.0)
_ACCENT = (0.35, 0.62, 1.0, 1.0)


def _load_icon() -> bool:
    global _ICON_IMAGE, _ICON_TEXTURE
    if not ICON_IMAGE_PATH.is_file():
        return False
    try:
        _ICON_IMAGE = bpy.data.images.load(str(ICON_IMAGE_PATH), check_existing=True)
        _ICON_TEXTURE = gpu.texture.from_image(_ICON_IMAGE)
    except (OSError, RuntimeError, TypeError):
        _ICON_IMAGE = None
        _ICON_TEXTURE = None
    return _ICON_TEXTURE is not None


def _draw_splash(_state, context) -> None:
    width = context.region.width
    height = context.region.height
    card_width = min(620.0, width - 80.0)
    card_height = min(470.0, height - 80.0)
    card_x = (width - card_width) / 2.0
    card_y = (height - card_height) / 2.0

    gpu.state.blend_set("ALPHA")
    try:
        _draw_rect(0, 0, width, height, _BG)
        _draw_rect(card_x, card_y, card_width, card_height, _CARD)
        _draw_rect(card_x, card_y, card_width, 2.0, _ACCENT)
        _draw_text(SPLASH_TITLE, card_x + 34, card_y + card_height - 58, 30, _TEXT)
        _draw_text(SPLASH_VERSION, card_x + 36, card_y + card_height - 84, 13, _MUTED)

        if _ICON_TEXTURE is not None:
            icon_size = min(180.0, card_height * 0.42)
            draw_texture_2d(
                _ICON_TEXTURE,
                (card_x + (card_width - icon_size) / 2.0, card_y + card_height * 0.31),
                icon_size,
                icon_size,
            )

        _draw_centered("Voxel worlds start here.", card_x, card_y + 72, card_width, 17, _TEXT)
        _draw_centered("Starting your workspace...", card_x, card_y + 43, card_width, 12, _MUTED)
    finally:
        gpu.state.blend_set("NONE")


def _finish() -> None:
    global _ACTIVE_AREA, _DRAW_HANDLE, _ICON_IMAGE, _ICON_TEXTURE
    if _DRAW_HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
    _DRAW_HANDLE = None
    _ICON_TEXTURE = None
    _ICON_IMAGE = None
    area = _ACTIVE_AREA
    _ACTIVE_AREA = None
    if area is not None:
        area.tag_redraw()
    from . import desktop_shell

    desktop_shell.open_shell()


def show() -> bool:
    """Show the splash briefly, then open the VOXTART project shell."""
    global _ACTIVE_AREA, _DRAW_HANDLE
    if bpy.app.background:
        return False
    if not _load_icon():
        return False
    for window in tuple(bpy.context.window_manager.windows):
        screen = window.screen
        if screen is None:
            continue
        for area in tuple(screen.areas):
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is None:
                continue
            _ACTIVE_AREA = area
            with bpy.context.temp_override(window=window, screen=screen, area=area, region=region):
                draw_context = bpy.context
                _DRAW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
                    _draw_splash,
                    (None, draw_context),
                    "WINDOW",
                    "POST_PIXEL",
                )
            try:
                bpy.app.timers.register(_finish, first_interval=2.4)
            except (ValueError, RuntimeError):
                _finish()
            area.tag_redraw()
            return True
    return False


__all__ = ["ICON_IMAGE_PATH", "SPLASH_TITLE", "SPLASH_VERSION", "show"]
