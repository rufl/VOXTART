# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Apply VoxTart title and icon to the bundled Windows host window."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

import bpy

WINDOW_TITLE = "VoxTart"
ICON_PATH = Path(__file__).resolve().parents[3] / "assets" / "voxtart.ico"

_ICON_HANDLE = None
_TIMER_REGISTERED = False


def _window_handles() -> tuple[int, ...]:
    if os.name != "nt" or bpy.app.background:
        return ()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_windows = user32.EnumWindows
    enum_windows.restype = ctypes.c_bool
    get_process_id = user32.GetWindowThreadProcessId
    get_process_id.restype = ctypes.c_ulong
    is_visible = user32.IsWindowVisible
    is_visible.restype = ctypes.c_bool
    process_id = os.getpid()
    handles: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_long)
    def collect(hwnd, _lparam):
        if not is_visible(hwnd):
            return True
        owner_process = ctypes.c_ulong()
        get_process_id(hwnd, ctypes.byref(owner_process))
        if owner_process.value == process_id:
            handles.append(int(hwnd))
        return True

    enum_windows(collect, 0)
    return tuple(handles)


def _apply_to_windows() -> int:
    handles = _window_handles()
    if not handles:
        return 0

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    set_title = user32.SetWindowTextW
    set_title.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    set_title.restype = ctypes.c_bool

    global _ICON_HANDLE
    if _ICON_HANDLE is None and ICON_PATH.is_file():
        load_image = user32.LoadImageW
        load_image.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        load_image.restype = ctypes.c_void_p
        image_icon = 1
        load_from_file = 0x00000010
        default_size = 0x00000040
        _ICON_HANDLE = load_image(None, str(ICON_PATH), image_icon, 0, 0, load_from_file | default_size)

    send_message = user32.SendMessageW
    send_message.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    send_message.restype = ctypes.c_ssize_t
    wm_seticon = 0x0080
    icon_small = 0
    icon_big = 1
    for hwnd in handles:
        set_title(hwnd, WINDOW_TITLE)
        if _ICON_HANDLE:
            send_message(hwnd, wm_seticon, icon_small, _ICON_HANDLE)
            send_message(hwnd, wm_seticon, icon_big, _ICON_HANDLE)
    return len(handles)


def _refresh_title() -> float | None:
    if os.name != "nt" or bpy.app.background:
        return None
    _apply_to_windows()
    return 1.0


def apply() -> bool:
    """Apply branding now and keep Blender from restoring its own title."""
    global _TIMER_REGISTERED
    if os.name != "nt" or bpy.app.background:
        return False
    applied = _apply_to_windows() > 0
    if not _TIMER_REGISTERED:
        try:
            bpy.app.timers.register(_refresh_title, first_interval=1.0, persistent=True)
            _TIMER_REGISTERED = True
        except (ValueError, RuntimeError):
            pass
    return applied


def icon_loaded() -> bool:
    """Return whether the VoxTart icon was loaded into the host window."""
    return bool(_ICON_HANDLE)


__all__ = ["ICON_PATH", "WINDOW_TITLE", "apply", "icon_loaded"]
