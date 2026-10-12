# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""VoxStart splash surface shown before the VOXTART project editor."""

from __future__ import annotations

from pathlib import Path

import bpy
import gpu
from gpu_extras.presets import draw_texture_2d
from .desktop_shell import (
    _draw_centered,
    _draw_circle,
    _draw_rect,
    _draw_round_rect,
    _draw_text,
)

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
_CARD_BORDER = (0.16, 0.2, 0.29, 0.98)
_STATUS = (0.09, 0.12, 0.18, 1.0)
_EYEBROW = "VOXTART DESKTOP"
_STATUS_TEXT = "Preparing your editor"


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
        _draw_round_rect(card_x, card_y, card_width, card_height, _CARD_BORDER, radius=18.0)
        _draw_round_rect(
            card_x + 1.0,
            card_y + 1.0,
            card_width - 2.0,
            card_height - 2.0,
            _CARD,
            radius=17.0,
        )
        _draw_rect(card_x + 28.0, card_y + 1.0, card_width - 56.0, 2.0, _ACCENT)
        _draw_text(_EYEBROW, card_x + 34.0, card_y + card_height - 34.0, 11, _ACCENT)
        _draw_text(SPLASH_TITLE, card_x + 34.0, card_y + card_height - 72.0, 30, _TEXT)
        _draw_text(SPLASH_VERSION, card_x + 36.0, card_y + card_height - 98.0, 13, _MUTED)

        if _ICON_TEXTURE is not None:
            icon_size = min(180.0, card_height * 0.42)
            draw_texture_2d(
                _ICON_TEXTURE,
                (card_x + (card_width - icon_size) / 2.0, card_y + card_height * 0.31),
                icon_size,
                icon_size,
            )

        _draw_centered("Voxel worlds start here.", card_x, card_y + 72.0, card_width, 17, _TEXT)
        _draw_centered("A focused workspace for making, shaping, and sharing.", card_x, card_y + 48.0, card_width, 11, _MUTED)
        status_x = card_x + 32.0
        status_y = card_y + 16.0
        status_width = card_width - 64.0
        _draw_round_rect(status_x, status_y, status_width, 24.0, _STATUS, radius=12.0)
        _draw_circle(status_x + 15.0, status_y + 12.0, 3.0, _ACCENT, segments=12)
        _draw_text(_STATUS_TEXT, status_x + 28.0, status_y + 8.0, 11, _MUTED)
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
    from . import desktop_shell, desktop_ui

    if not desktop_ui.open_default_editor():
        desktop_shell.open_shell()


def show() -> bool:
    """Show the splash briefly, then open the VOXTART project editor."""
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
