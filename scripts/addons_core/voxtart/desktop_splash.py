# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""VoxTart splash surface shown before the VOXTART project editor."""

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

SPLASH_TITLE = "VoxTart"
SPLASH_VERSION = "A friendly voxel editor"
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
_EYEBROW = "WELCOME"
_STATUS_TEXT = "Opening your block world..."


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
    if _ACTIVE_AREA is None or _DRAW_HANDLE is None:
        return
    width = context.region.width
    height = context.region.height
    card_width = min(680.0, width - 64.0)
    card_height = min(520.0, height - 64.0)
    card_x = (width - card_width) / 2.0
    card_y = (height - card_height) / 2.0

    gpu.state.blend_set("ALPHA")
    try:
        _draw_rect(0, 0, width, height, _BG)
        _draw_round_rect(card_x, card_y, card_width, card_height, _CARD_BORDER, radius=20.0)
        _draw_round_rect(
            card_x + 1.0,
            card_y + 1.0,
            card_width - 2.0,
            card_height - 2.0,
            _CARD,
            radius=19.0,
        )
        _draw_rect(card_x + 30.0, card_y + 1.0, card_width - 60.0, 3.0, _ACCENT)
        _draw_text(SPLASH_TITLE, card_x + 38.0, card_y + card_height - 80.0, 32, _TEXT)
        _draw_text("Make a block world.", card_x + 40.0, card_y + card_height - 108.0, 14, _MUTED)
        _draw_text(SPLASH_VERSION, card_x + 40.0, card_y + card_height - 128.0, 12, _MUTED)

        if _ICON_TEXTURE is not None:
            icon_size = min(184.0, card_height * 0.38)
            draw_texture_2d(
                _ICON_TEXTURE,
                (card_x + (card_width - icon_size) / 2.0, card_y + card_height * 0.31),
                icon_size,
                icon_size,
            )

        _draw_centered(
            "Start with the little pyramid, then make it yours.",
            card_x,
            card_y + 112.0,
            card_width,
            16,
            _TEXT,
        )
        _draw_centered(
            "1  Pick a color     2  Click a face     3  Keep creating",
            card_x,
            card_y + 84.0,
            card_width,
            12,
            _MUTED,
        )
        status_x = card_x + 34.0
        status_y = card_y + 28.0
        status_width = card_width - 68.0
        _draw_round_rect(status_x, status_y, status_width, 30.0, _STATUS, radius=15.0)
        _draw_circle(status_x + 17.0, status_y + 15.0, 4.0, _ACCENT, segments=12)
        _draw_text(_STATUS_TEXT, status_x + 30.0, status_y + 10.0, 12, _MUTED)
    finally:
        gpu.state.blend_set("NONE")




def dismiss() -> None:
    global _ACTIVE_AREA, _DRAW_HANDLE, _ICON_IMAGE, _ICON_TEXTURE
    try:
        bpy.app.timers.unregister(_finish)
    except (AttributeError, ValueError, RuntimeError):
        pass
    if _DRAW_HANDLE is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
        except (ReferenceError, RuntimeError):
            pass
    _DRAW_HANDLE = None
    _ICON_TEXTURE = None
    _ICON_IMAGE = None
    area = _ACTIVE_AREA
    _ACTIVE_AREA = None
    if area is not None:
        area.tag_redraw()


def _finish() -> None:
    dismiss()
    from . import desktop_shell, desktop_ui

    if not desktop_ui.open_default_editor():
        desktop_shell.open_shell()
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    except (AttributeError, RuntimeError):
        pass


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


__all__ = ["ICON_IMAGE_PATH", "SPLASH_TITLE", "SPLASH_VERSION", "dismiss", "show"]
