# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""A focused VOXTART shell drawn over the bundled Blender viewport.

The shell keeps Blender as the viewport and file/runtime host, but presents the
first release as a small project editor: project tab, simple modes, explorer,
palette, parts, rounded icon controls, and status bar. Hover hints expose labels
and shortcuts. The normal Blender sidebar remains available as a fallback when
the shell is closed.
"""

from __future__ import annotations

import math
from pathlib import Path

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from .operators import ASSET_PROPERTY
from .palette import DEFAULT_PALETTE
from .voxel_editor import EDITOR_MODE_PROPERTY, EDITOR_SHAPE_PROPERTY, EDITOR_TOOLBAR_ITEMS

SHELL_MODE_PROPERTY = "voxtart_shell_mode"
SHELL_OPEN_PROPERTY = "voxtart_shell_open"
SHELL_STATUS_PROPERTY = "voxtart_shell_status"
SHELL_MODES = ("HOME", "BUILD", "PAINT", "CHARACTER")

_TOPBAR_HEIGHT = 48
_BOTTOMBAR_HEIGHT = 32
_LEFT_WIDTH = 224
_RIGHT_WIDTH = 272
_SHELL_TOOLBAR_WIDTH = 144

_SHELL_TOOL_LABELS = {
    ("MODE", "ADD"): "Place",
    ("MODE", "ATTACH"): "Attach",
    ("MODE", "ERASE"): "Erase",
    ("MODE", "PAINT"): "Paint",
    ("MODE", "SELECT"): "Select",
    ("SHAPE", "VOXEL"): "Voxel",
    ("SHAPE", "LINE"): "Line",
    ("SHAPE", "BOX"): "Box",
    ("SHAPE", "SPHERE"): "Sphere",
    ("SHAPE", "FACE"): "Face",
    ("SELECT", "LASSO"): "Lasso",
    ("SELECT", "CIRCLE"): "Circle",
    ("SELECT", "COLOR"): "Color",
    ("SELECT", "COMPONENT"): "Component",
    ("SELECT", "SURFACE"): "Surface",
}



_BG = (0.055, 0.06, 0.07, 0.98)
_PANEL = (0.075, 0.08, 0.09, 0.98)
_PANEL_LIGHT = (0.11, 0.115, 0.13, 0.98)
_BORDER = (0.19, 0.2, 0.22, 1.0)
_TEXT = (0.92, 0.93, 0.95, 1.0)
_MUTED = (0.58, 0.61, 0.66, 1.0)
_ACCENT = (0.32, 0.58, 0.95, 1.0)
_ACCENT_DARK = (0.15, 0.27, 0.46, 1.0)
_SUCCESS = (0.29, 0.7, 0.48, 1.0)

_ACTIVE_STATE = None
_ACTIVE_HANDLE = None
_ACTIVE_AREA = None


class _ShellState:
    def __init__(self):
        self.targets = []
        self.tooltips = {}
        self.hovered = None
        self.mouse_x = 0.0
        self.mouse_y = 0.0


def _draw_rect(x: float, y: float, width: float, height: float, color) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(
        shader,
        "TRI_FAN",
        {
            "pos": (
                (x, y),
                (x + width, y),
                (x + width, y + height),
                (x, y + height),
            )
        },
    )
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_line(points, color, width: float = 1.0) -> None:
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("color", color)
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": points})
    batch.draw(shader)


def _draw_text(text: str, x: float, y: float, size: float, color=_TEXT) -> None:
    font_id = 0
    blf.position(font_id, x, y, 0)
    blf.size(font_id, size)
    blf.color(font_id, *color)
    blf.draw(font_id, text)


def _text_width(text: str, size: float) -> float:
    font_id = 0
    blf.size(font_id, size)
    return blf.dimensions(font_id, text)[0]

def _rounded_points(x: float, y: float, width: float, height: float, radius: float, segments: int = 5):
    radius = max(0.0, min(radius, width / 2.0, height / 2.0))
    corners = (
        (x + radius, y + radius, math.pi),
        (x + width - radius, y + radius, math.pi * 1.5),
        (x + width - radius, y + height - radius, 0.0),
        (x + radius, y + height - radius, math.pi * 0.5),
    )
    points = []
    for corner_index, (center_x, center_y, start) in enumerate(corners):
        first_point = 0 if corner_index == 0 else 1
        for index in range(first_point, segments + 1):
            angle = start + math.pi * 0.5 * index / segments
            points.append(
                (
                    center_x + math.cos(angle) * radius,
                    center_y + math.sin(angle) * radius,
                )
            )
        next_center_x, next_center_y, next_start = corners[(corner_index + 1) % len(corners)]
        next_angle = next_start
        points.append(
            (
                next_center_x + math.cos(next_angle) * radius,
                next_center_y + math.sin(next_angle) * radius,
            )
        )
    return tuple(points)


def _draw_round_rect(
    x: float,
    y: float,
    width: float,
    height: float,
    color,
    radius: float = 8.0,
) -> None:
    boundary = _rounded_points(x, y, width, height, radius)
    points = ((x + width / 2.0, y + height / 2.0), *boundary)
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": points})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_circle(x: float, y: float, radius: float, color, segments: int = 16) -> None:
    points = [(x, y)]
    points.extend(
        (
            x + math.cos(math.tau * index / segments) * radius,
            y + math.sin(math.tau * index / segments) * radius,
        )
        for index in range(segments + 1)
    )
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": tuple(points)})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_icon(name: str, x: float, y: float, size: float, color=_TEXT) -> None:
    half = size * 0.5
    if name in {"cube", "asset", "build"}:
        s = size * 0.32
        _draw_line(((x - s, y - s * 0.5), (x, y - s), (x + s, y - s * 0.5), (x + s, y + s * 0.5), (x, y + s), (x - s, y + s * 0.5), (x - s, y - s * 0.5)), color, 1.5)
        _draw_line(((x, y - s), (x, y), (x + s, y - s * 0.5)), color, 1.2)
        _draw_line(((x, y), (x, y + s)), color, 1.2)
        return
    if name == "folder":
        _draw_round_rect(x - half, y - half * 0.45, size, size * 0.72, color, 2.5)
        _draw_line(((x - half, y + half * 0.1), (x + half, y + half * 0.1)), _BG, 1.0)
        return
    if name in {"paint", "palette"}:
        _draw_circle(x - size * 0.08, y + size * 0.02, size * 0.3, color, 14)
        _draw_circle(x + size * 0.12, y - size * 0.17, size * 0.06, _BG, 10)
        _draw_line(((x + size * 0.12, y - size * 0.27), (x + size * 0.34, y - size * 0.48)), color, 1.5)
        return
    if name == "pick":
        _draw_circle(x, y, size * 0.3, color, 16)
        _draw_circle(x, y, size * 0.11, _BG, 12)
        _draw_line(((x, y - half), (x, y - size * 0.36)), color, 1.2)
        _draw_line(((x, y + size * 0.36), (x, y + half)), color, 1.2)
        _draw_line(((x - half, y), (x - size * 0.36, y)), color, 1.2)
        _draw_line(((x + size * 0.36, y), (x + half, y)), color, 1.2)
        return
    if name == "remove":
        _draw_line(((x - size * 0.32, y), (x + size * 0.32, y)), color, 2.0)
        return
    if name == "close":
        _draw_line(((x - size * 0.28, y - size * 0.28), (x + size * 0.28, y + size * 0.28)), color, 1.8)
        _draw_line(((x - size * 0.28, y + size * 0.28), (x + size * 0.28, y - size * 0.28)), color, 1.8)
        return
    if name in {"place", "add"}:
        _draw_line(((x - size * 0.32, y), (x + size * 0.32, y)), color, 1.8)
        _draw_line(((x, y - size * 0.32), (x, y + size * 0.32)), color, 1.8)
        return
    if name == "optimize":
        _draw_line(((x - size * 0.3, y - size * 0.35), (x - size * 0.05, y - size * 0.05), (x + size * 0.32, y + size * 0.34)), color, 1.8)
        _draw_line(((x + size * 0.1, y + size * 0.34), (x + size * 0.32, y + size * 0.34), (x + size * 0.32, y + size * 0.12)), color, 1.4)
        return
    if name == "character":
        _draw_circle(x, y + size * 0.2, size * 0.19, color, 12)
        _draw_line(((x - size * 0.34, y - size * 0.4), (x, y - size * 0.05), (x + size * 0.34, y - size * 0.4)), color, 1.8)
        _draw_line(((x, y - size * 0.05), (x, y - size * 0.42)), color, 1.6)
        return
    if name == "layers":
        for offset in (-size * 0.24, 0.0, size * 0.24):
            _draw_line(((x - size * 0.34, y + offset), (x, y + offset + size * 0.15), (x + size * 0.34, y + offset)), color, 1.4)
        return
    if name == "toolbox":
        _draw_round_rect(x - half, y - size * 0.2, size, size * 0.45, color, 2.0)
        _draw_line(((x - size * 0.18, y + size * 0.25), (x - size * 0.18, y + size * 0.42), (x + size * 0.18, y + size * 0.42), (x + size * 0.18, y + size * 0.25)), color, 1.4)
        return
    if name == "export":
        _draw_line(((x - size * 0.3, y - size * 0.34), (x - size * 0.3, y + size * 0.3), (x + size * 0.2, y + size * 0.3)), color, 1.6)
        _draw_line(((x - size * 0.02, y - size * 0.02), (x + size * 0.34, y - size * 0.02)), color, 1.8)
        _draw_line(((x + size * 0.16, y - size * 0.18), (x + size * 0.34, y - size * 0.02), (x + size * 0.16, y + size * 0.14)), color, 1.6)
        return
    _draw_circle(x, y, size * 0.28, color, 12)


_ICON_FOR_KEY = {
    "mode:BUILD": "build",
    "mode:PAINT": "paint",
    "mode:CHARACTER": "character",
    "project:blocks": "cube",
    "project:characters": "character",
    "project:layers": "layers",
    "home:make": "cube",
    "home:open": "folder",
    "build:voxelize": "cube",
    "build:optimize": "optimize",
    "build:editor": "toolbox",
    "build:toolbox": "toolbox",
    "paint:pick": "pick",
    "paint:paint": "paint",
    "paint:remove": "remove",
    "paint:place": "place",
    "character:prepare": "character",
    "character:rebuild": "build",
    "character:export": "export",
    "character:toolbox": "toolbox",
    "shell:close": "close",
}


_SHORTCUT_FOR_KEY = {
    "mode:BUILD": "B",
    "mode:PAINT": "P",
    "mode:CHARACTER": "C",
    "paint:pick": "Alt+Shift+E",
    "paint:paint": "Alt+Shift+P",
    "paint:remove": "Alt+Shift+X",
    "paint:place": "Alt+Shift+V",
    "build:toolbox": "Esc",
    "character:toolbox": "Esc",
    "shell:close": "Esc",
}


def _register_tooltip(state, key: str, label: str, shortcut: str = "") -> None:
    state.tooltips[key] = (label, shortcut)


def _draw_tooltip(state, width: float, height: float) -> None:
    if state.hovered is None or state.hovered not in state.tooltips:
        return
    label, shortcut = state.tooltips[state.hovered]
    text = f"{label}  ·  {shortcut}" if shortcut else label
    tooltip_width = min(width - 24.0, max(150.0, _text_width(text, 12) + 24.0))
    tooltip_height = 30.0
    x = min(max(12.0, state.mouse_x + 14.0), width - tooltip_width - 12.0)
    y = min(max(_BOTTOMBAR_HEIGHT + 8.0, state.mouse_y - tooltip_height - 12.0), height - tooltip_height - 8.0)
    _draw_round_rect(x, y, tooltip_width, tooltip_height, _PANEL_LIGHT, 7.0)
    _draw_line(
        ((x, y), (x + tooltip_width, y), (x + tooltip_width, y + tooltip_height), (x, y + tooltip_height), (x, y)),
        _ACCENT,
    )
    _draw_text(text, x + 12, y + 9, 12, _TEXT)

def _draw_centered(text: str, x: float, y: float, width: float, size: float, color=_TEXT) -> None:
    _draw_text(text, x + max(0.0, (width - _text_width(text, size)) / 2.0), y, size, color)


def _inside(x: float, y: float, bounds) -> bool:
    left, bottom, right, top = bounds
    return left <= x <= right and bottom <= y <= top


def _project_name() -> str:
    filepath = str(bpy.data.filepath or "").strip()
    return Path(filepath).stem if filepath else "Untitled Project"


def _palette_colors(scene):
    collection = getattr(scene, "voxtart_palette_colors", None)
    if collection:
        return tuple(tuple(item.color) for item in collection)
    return tuple(DEFAULT_PALETTE)


def _active_mode(scene) -> str:
    mode = str(scene.get(SHELL_MODE_PROPERTY, "HOME"))
    return mode if mode in SHELL_MODES else "HOME"


def _set_mode(scene, mode: str) -> None:
    scene[SHELL_MODE_PROPERTY] = mode if mode in SHELL_MODES else "HOME"


def _asset_objects():
    return tuple(
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH" and ASSET_PROPERTY in obj
    )


def _active_asset(context):
    obj = getattr(context, "active_object", None)
    return obj if obj is not None and ASSET_PROPERTY in obj else None


def _set_status(scene, message: str) -> None:
    scene[SHELL_STATUS_PROPERTY] = str(message)[:120]


def _run_operator(scene, action, success_message: str) -> bool:
    try:
        result = action()
    except (RuntimeError, TypeError, ValueError) as error:
        _set_status(scene, str(error))
        return False
    if "FINISHED" in result or "RUNNING_MODAL" in result:
        _set_status(scene, success_message)
        return True
    _set_status(scene, "Action cancelled")

def _draw_button(
    state,
    key: str,
    label: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    active=False,
    accent=False,
) -> None:
    hovered = getattr(state, "hovered", None) == key
    if accent:
        color = _ACCENT if hovered else _ACCENT_DARK
    elif active or hovered:
        color = _PANEL_LIGHT
    else:
        color = _PANEL
    radius = min(9.0, height / 2.0)
    _draw_round_rect(x, y, width, height, color, radius)
    _draw_line(
        _rounded_points(x, y, width, height, radius),
        _ACCENT if active or accent or hovered else _BORDER,
    )
    icon = _ICON_FOR_KEY.get(key)
    if key.startswith("part:"):
        icon = "asset"
    text_x = x + 12
    if icon:
        _draw_icon(icon, x + 18, y + height / 2.0, min(16.0, height - 8.0), _TEXT)
        text_x = x + 34
    _draw_text(label, text_x, y + (height - 14) / 2.0, 13, _TEXT)
    _register_tooltip(state, key, label, _SHORTCUT_FOR_KEY.get(key, ""))
    state.targets.append((key, (x, y, x + width, y + height)))

def _shell_toolbar_active(scene, item) -> bool:
    kind, value = item[:2]
    if kind == "MODE":
        return str(getattr(scene, EDITOR_MODE_PROPERTY, "ADD")) == value
    if kind == "SHAPE":
        return str(getattr(scene, EDITOR_SHAPE_PROPERTY, "VOXEL")) == value
    return (
        str(getattr(scene, EDITOR_MODE_PROPERTY, "ADD")) == "SELECT"
        and str(getattr(scene, EDITOR_SHAPE_PROPERTY, "VOXEL")) == value
    )


def _draw_shell_toolbar(state, context, height: float) -> None:
    panel_x = _LEFT_WIDTH + 12.0
    panel_y = _BOTTOMBAR_HEIGHT + 12.0
    panel_top = height - _TOPBAR_HEIGHT - 12.0
    panel_height = max(80.0, panel_top - panel_y)
    _draw_round_rect(panel_x, panel_y, _SHELL_TOOLBAR_WIDTH, panel_height, _PANEL, 10.0)
    _draw_line(
        _rounded_points(panel_x, panel_y, _SHELL_TOOLBAR_WIDTH, panel_height, 10.0),
        _BORDER,
    )
    _draw_text("TOOLS", panel_x + 12.0, panel_top - 25.0, 11, _MUTED)

    item_count = sum(item is not None for item in EDITOR_TOOLBAR_ITEMS)
    separator_count = sum(item is None for item in EDITOR_TOOLBAR_ITEMS)
    button_gap = 4.0
    separator_gap = 9.0
    content_top = panel_top - 42.0
    available = max(
        0.0,
        content_top
        - panel_y
        - 10.0
        - (item_count - 1) * button_gap
        - separator_count * separator_gap,
    )
    button_height = min(36.0, max(22.0, available / max(1, item_count)))
    button_width = _SHELL_TOOLBAR_WIDTH - 16.0
    cursor = content_top
    for item in EDITOR_TOOLBAR_ITEMS:
        if item is None:
            cursor -= separator_gap
            continue
        key = f"tool:{item[0]}:{item[1]}"
        y = cursor - button_height
        label = _SHELL_TOOL_LABELS.get(item[:2], item[3])
        _draw_button(
            state,
            key,
            label,
            panel_x + 8.0,
            y,
            button_width,
            button_height,
            active=_shell_toolbar_active(context.scene, item),
        )
        _register_tooltip(state, key, item[3], item[4])
        cursor = y - button_gap


def _draw_topbar(state, context, width: float, height: float) -> None:
    y = height - _TOPBAR_HEIGHT
    _draw_rect(0, y, width, _TOPBAR_HEIGHT, _BG)

    project_x = 16
    project_width = 220
    _draw_round_rect(project_x, y + 7, project_width, 34, _PANEL_LIGHT, 10.0)
    _draw_icon("folder", project_x + 19, y + 24, 16.0, _TEXT)
    _draw_text(_project_name()[:25], project_x + 34, y + 19, 13, _TEXT)

    mode = _active_mode(context.scene)
    nav_x = project_x + project_width + 20
    for key, label, button_width in (
        ("BUILD", "Build", 98),
        ("PAINT", "Paint", 98),
        ("CHARACTER", "Character", 118),
    ):
        _draw_button(state, f"mode:{key}", label, nav_x, y + 8, button_width, 32, active=mode == key)
        nav_x += button_width + 8

    close_width = 76
    _draw_button(state, "shell:close", "Close", width - close_width - 16, y + 8, close_width, 32)




def _draw_left_panel(state, context, width: float, height: float) -> None:
    panel_top = height - _TOPBAR_HEIGHT
    panel_height = height - _TOPBAR_HEIGHT - _BOTTOMBAR_HEIGHT
    _draw_rect(0, _BOTTOMBAR_HEIGHT, _LEFT_WIDTH, panel_height, _PANEL)
    x = 16
    _draw_text("PROJECT", x, panel_top - 28, 11, _MUTED)
    _draw_text(_project_name()[:24], x, panel_top - 55, 15, _TEXT)
    _draw_line(((x, panel_top - 68), (_LEFT_WIDTH - 16, panel_top - 68)), _BORDER)

    _draw_text("MY THINGS", x, panel_top - 94, 11, _MUTED)
    mode = _active_mode(context.scene)
    rows = (
        ("project:blocks", "Blocks", "BUILD"),
        ("project:characters", "Characters", "CHARACTER"),
        ("project:layers", "Layers", "PAINT"),
    )
    for index, (key, label, row_mode) in enumerate(rows):
        y = panel_top - 126 - index * 30
        _draw_button(state, key, label, x, y, _LEFT_WIDTH - 32, 24, active=mode == row_mode)

    quick_y = panel_top - 240
    _draw_text("QUICK ACTIONS", x, quick_y, 11, _MUTED)
    _draw_button(state, "home:make", "New block toy", x, quick_y - 36, _LEFT_WIDTH - 32, 28, accent=True)
    _draw_button(state, "home:open", "Open a model", x, quick_y - 70, _LEFT_WIDTH - 32, 28)
    _draw_text("All edits support undo.", x, _BOTTOMBAR_HEIGHT + 18, 10, _MUTED)


def _draw_right_panel(state, context, width: float, height: float) -> None:
    x0 = width - _RIGHT_WIDTH
    panel_top = height - _TOPBAR_HEIGHT
    panel_height = height - _TOPBAR_HEIGHT - _BOTTOMBAR_HEIGHT
    _draw_rect(x0, _BOTTOMBAR_HEIGHT, _RIGHT_WIDTH, panel_height, _PANEL)
    x = x0 + 16
    _draw_text("PALETTE", x, panel_top - 28, 11, _MUTED)
    _draw_text("Pick a color", x, panel_top - 53, 15, _TEXT)

    colors = _palette_colors(context.scene)
    swatch_size = 34
    gap = 8
    start_y = panel_top - 102
    selected_index = int(getattr(context.scene, "voxtart_palette_index", 0))
    for index, color in enumerate(colors[:16]):
        column = index % 4
        row = index // 4
        sx = x + column * (swatch_size + gap)
        sy = start_y - row * (swatch_size + gap)
        selected = index == selected_index
        _draw_round_rect(sx, sy, swatch_size, swatch_size, tuple(color), 7.0)
        _draw_line(
            _rounded_points(sx, sy, swatch_size, swatch_size, 7.0),
            _TEXT if selected else _BORDER,
            2.0 if selected else 1.0,
        )
        _register_tooltip(state, f"palette:{index}", f"Color slot {index + 1}")
        state.targets.append((f"palette:{index}", (sx, sy, sx + swatch_size, sy + swatch_size)))

    parts_y = start_y - 4 * (swatch_size + gap) - 26
    _draw_text("PARTS", x, parts_y, 11, _MUTED)
    _draw_text("Voxel assets", x, parts_y - 25, 13, _TEXT)
    objects = _asset_objects()
    if objects:
        active = _active_asset(context)
        for index, obj in enumerate(objects[:5]):
            y = parts_y - 78 - index * 30
            _draw_button(
                state,
                f"part:{index}",
                f"{index + 1}. {obj.name[:21]}",
                x,
                y,
                _RIGHT_WIDTH - 32,
                24,
                active=obj == active,
            )
    else:
        _draw_text("Start in Build to add one.", x, parts_y - 52, 12, _MUTED)


def _draw_bottom_bar(context, width: float, height: float) -> None:
    _draw_rect(0, 0, width, _BOTTOMBAR_HEIGHT, _BG)
    scene = context.scene
    status = str(scene.get(SHELL_STATUS_PROPERTY, "Ready"))[:64]
    assets = _asset_objects()
    active = _active_asset(context)
    active_name = active.name[:22] if active is not None else "No active asset"
    _draw_text(status, 16, 10, 11, _MUTED)
    _draw_text(
        f"{len(assets)} asset(s) | {active_name}",
        max(_LEFT_WIDTH + 24, width - _RIGHT_WIDTH - 220),
        10,
        11,
        _TEXT,
    )


def _draw_home(state, width: float, height: float) -> None:
    center_left = _LEFT_WIDTH
    center_right = width - _RIGHT_WIDTH
    center_width = center_right - center_left
    card_width = min(720.0, center_width - 80.0)
    card_height = 300.0
    card_x = center_left + (center_width - card_width) / 2.0
    card_y = _BOTTOMBAR_HEIGHT + (height - _TOPBAR_HEIGHT - _BOTTOMBAR_HEIGHT - card_height) / 2.0

    _draw_round_rect(card_x, card_y, card_width, card_height, _BG, 14.0)
    _draw_line(_rounded_points(card_x, card_y, card_width, card_height, 14.0), _BORDER)
    _draw_centered("Let's make something!", card_x, card_y + card_height - 58, card_width, 24, _TEXT)
    _draw_centered("Choose a starting point. You can change it later.", card_x, card_y + card_height - 84, card_width, 13, _MUTED)

    button_y = card_y + 78
    button_gap = 16
    button_width = (card_width - 64 - button_gap * 2) / 3.0
    for index, (key, title, subtitle, accent) in enumerate(
        (
            ("home:make", "Make Blocks", "Build a tiny world", True),
            ("home:open", "Open a Model", "Bring in something", False),
            ("mode:CHARACTER", "Character Tools", "Shape and rig later", False),
        )
    ):
        bx = card_x + 32 + index * (button_width + button_gap)
        hovered = state.hovered == key
        color = _ACCENT if accent and hovered else _ACCENT_DARK if accent else _PANEL_LIGHT
        radius = 12.0
        _draw_round_rect(bx, button_y, button_width, 104, color, radius)
        _draw_line(
            _rounded_points(bx, button_y, button_width, 104, radius),
            _ACCENT if accent or hovered else _BORDER,
        )
        _draw_icon(_ICON_FOR_KEY.get(key, "cube"), bx + 26, button_y + 72, 22.0, _TEXT)
        _draw_text(title, bx + 46, button_y + 66, 14, _TEXT)
        _draw_text(subtitle, bx + 46, button_y + 40, 11, _MUTED)
        _register_tooltip(state, key, title, _SHORTCUT_FOR_KEY.get(key, ""))
        state.targets.append((key, (bx, button_y, bx + button_width, button_y + 104)))


def _draw_mode(state, context, width: float, height: float) -> None:
    center_left = _LEFT_WIDTH + _SHELL_TOOLBAR_WIDTH + 24
    center_right = width - _RIGHT_WIDTH
    center_width = center_right - center_left
    mode = _active_mode(context.scene)
    title, subtitle = {
        "BUILD": ("Build Blocks", "Create, convert, and optimize voxel assets."),
        "PAINT": ("Paint Blocks", "Pick a voxel, choose a color, and edit it."),
        "CHARACTER": ("Character Tools", "Prepare, rebuild, and export a block character."),
    }[mode]
    _draw_text(title, center_left + 42, height - _TOPBAR_HEIGHT - 54, 25, _TEXT)
    _draw_text(subtitle, center_left + 42, height - _TOPBAR_HEIGHT - 78, 13, _MUTED)

    card_x = center_left + 42
    card_y = _BOTTOMBAR_HEIGHT + 52
    card_width = min(560.0, center_width - 84.0)
    card_height = 250.0
    _draw_round_rect(card_x, card_y, card_width, card_height, _BG, 12.0)
    _draw_line(_rounded_points(card_x, card_y, card_width, card_height, 12.0), _BORDER)
    active = _active_asset(context)
    active_label = f"Active asset: {active.name[:34]}" if active is not None else "No active voxel asset"
    _draw_text(active_label, card_x + 24, card_y + card_height - 54, 14, _TEXT if active else _MUTED)
    _draw_text(
        "Use the full toolbox with Esc when you need detailed settings.",
        card_x + 24,
        card_y + card_height - 78,
        11,
        _MUTED,
    )

    button_gap = 12
    button_width = (card_width - 48 - button_gap) / 2.0
    left = card_x + 24
    right = left + button_width + button_gap
    row_one = card_y + 76
    row_two = card_y + 30
    if mode == "BUILD":
        _draw_button(state, "home:make", "Add block toy", left, row_one, button_width, 34, accent=True)
        _draw_button(state, "build:voxelize", "Voxelize selected", right, row_one, button_width, 34)
        _draw_button(state, "build:editor", "Open voxel editor", left, row_two, button_width, 34)
        _draw_button(state, "build:optimize", "Optimize active", right, row_two, button_width, 34)
    elif mode == "PAINT":
        _draw_button(state, "paint:pick", "Pick voxel", left, row_one, button_width, 34, accent=True)
        _draw_button(state, "paint:paint", "Paint picked", right, row_one, button_width, 34)
        _draw_button(state, "paint:remove", "Remove picked", left, row_two, button_width, 34)
        _draw_button(state, "paint:place", "Place adjacent", right, row_two, button_width, 34)
    else:
        _draw_button(state, "character:prepare", "Prepare rig", left, row_one, button_width, 34, accent=True)
        _draw_button(state, "character:rebuild", "Rebuild character", right, row_one, button_width, 34)
        _draw_button(state, "character:export", "Export game rig", left, row_two, button_width, 34)
        _draw_button(state, "character:toolbox", "Open full toolbox", right, row_two, button_width, 34)


def _draw_shell(state, context) -> None:
    width = context.region.width
    height = context.region.height
    state.targets = []
    state.tooltips = {}
    gpu.state.blend_set("ALPHA")
    try:
        _draw_rect(0, 0, width, height, (0.03, 0.035, 0.04, 0.72))
        _draw_topbar(state, context, width, height)
        _draw_left_panel(state, context, width, height)
        _draw_right_panel(state, context, width, height)
        mode = _active_mode(context.scene)
        if mode != "HOME":
            _draw_shell_toolbar(state, context, height)
        _draw_bottom_bar(context, width, height)
        if mode == "HOME":
            _draw_home(state, width, height)
        else:
            _draw_mode(state, context, width, height)
        _draw_tooltip(state, width, height)
    finally:
        gpu.state.blend_set("NONE")


class VOXTART_OT_desktop_shell(bpy.types.Operator):
    """Show the focused VOXTART project editor shell."""

    bl_idname = "voxtart.desktop_shell"
    bl_label = "Open VOXTART Shell"
    bl_options = {"INTERNAL"}

    def _finish(self, context):
        global _ACTIVE_AREA, _ACTIVE_HANDLE, _ACTIVE_STATE

        if _ACTIVE_HANDLE is not None:
            bpy.types.SpaceView3D.draw_handler_remove(_ACTIVE_HANDLE, "WINDOW")
        _ACTIVE_HANDLE = None
        _ACTIVE_STATE = None
        scene = getattr(context, "scene", None)
        if scene is not None:
            scene[SHELL_OPEN_PROPERTY] = False
        area = _ACTIVE_AREA or getattr(context, "area", None)
        _ACTIVE_AREA = None
        if area is not None and area.type == "VIEW_3D":
            try:
                space = area.spaces.active
                space.show_region_ui = True
                space.show_region_header = True
                space.show_region_footer = True
                if context.screen is not None:
                    context.screen.show_statusbar = True
            except (AttributeError, RuntimeError, TypeError):
                pass
            area.tag_redraw()

    def _activate(self, context, key: str):
        scene = context.scene
        if key == "shell:close":
            self._finish(context)
            return
        if key.startswith("mode:"):
            mode = key.split(":", 1)[1]
            _set_mode(scene, mode)
            _set_status(scene, f"{mode.title()} mode")
            return
        if key.startswith("project:"):
            mode = {
                "project:blocks": "BUILD",
                "project:characters": "CHARACTER",
                "project:layers": "PAINT",
            }.get(key, "HOME")
            _set_mode(scene, mode)
            _set_status(scene, f"{mode.title()} mode")
            return
        if key.startswith("tool:"):
            _parts = key.split(":", 2)
            item = next(
                (
                    candidate
                    for candidate in EDITOR_TOOLBAR_ITEMS
                    if candidate is not None
                    and candidate[0] == _parts[1]
                    and candidate[1] == _parts[2]
                ),
                None,
            ) if len(_parts) == 3 else None
            if item is None:
                _set_status(scene, "That tool is no longer available")
                return
            kind, value, _glyph, label, _shortcut = item
            if kind == "MODE":
                setattr(scene, EDITOR_MODE_PROPERTY, value)
            elif kind == "SHAPE":
                setattr(scene, EDITOR_SHAPE_PROPERTY, value)
            else:
                setattr(scene, EDITOR_MODE_PROPERTY, "SELECT")
                setattr(scene, EDITOR_SHAPE_PROPERTY, value)
            if _active_asset(context) is None:
                _set_status(scene, "Select a voxel asset before opening the editor")
                return
            _set_status(scene, f"{label} selected")
            self._finish(context)
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.voxel_editor("INVOKE_DEFAULT"),
                "Voxel editor ready",
            )
            return
        if key == "home:make":
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.add_voxel_cube(
                    size=int(scene.voxtart_cube_size),
                    grid_size=float(scene.voxtart_grid_size),
                ),
                "Block toy added",
            )
            _set_mode(scene, "BUILD")
            return
        if key == "home:open":
            self._finish(context)
            _run_operator(scene, lambda: bpy.ops.voxtart.import_asset("INVOKE_DEFAULT"), "Choose a model to open")
            return
        if key.startswith("part:"):
            try:
                index = int(key.split(":", 1)[1])
                obj = _asset_objects()[index]
            except (IndexError, ValueError):
                _set_status(scene, "That asset is no longer available")
                return
            for selected in tuple(context.selected_objects):
                selected.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            _set_status(scene, f"Selected {obj.name}")
            return
        if key.startswith("palette:"):
            index = int(key.split(":", 1)[1])
            colors = _palette_colors(scene)
            if 0 <= index < len(colors):
                scene.voxtart_palette_index = index
                scene.voxtart_edit_palette_index = index
                _set_status(scene, f"Color slot {index + 1} selected")
            return
        if key == "build:voxelize":
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.voxelize_active(
                    grid_size=float(scene.voxtart_grid_size),
                    mode=str(scene.voxtart_voxelizer_mode),
                    shell_thickness=int(scene.voxtart_voxelizer_shell_thickness),
                ),
                "Selected mesh voxelized",
            )
            return
        if key == "build:editor":
            _set_status(scene, "Voxel editor opened")
            self._finish(context)
            _run_operator(scene, lambda: bpy.ops.voxtart.voxel_editor("INVOKE_DEFAULT"), "Voxel editor ready")
            return
        if key == "build:optimize":
            _run_operator(scene, lambda: bpy.ops.voxtart.optimize_mesh(), "Active mesh optimized")
            return
        if key == "build:toolbox":
            _set_status(scene, "Full toolbox opened")
            self._finish(context)
            return
        if key == "paint:pick":
            _run_operator(scene, lambda: bpy.ops.voxtart.pick_voxel("INVOKE_DEFAULT"), "Voxel picked")
            return
        if key in {"paint:paint", "paint:remove", "paint:place"}:
            action = {"paint:paint": "paint", "paint:remove": "remove", "paint:place": "add"}[key]
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.edit_picked_voxel(
                    action=action,
                    palette_index=int(scene.voxtart_edit_palette_index),
                    allow_protected=bool(scene.voxtart_edit_allow_protected),
                ),
                {"paint": "Voxel painted", "remove": "Voxel removed", "add": "Adjacent voxel placed"}[action],
            )
            return
        if key == "character:prepare":
            _run_operator(scene, lambda: bpy.ops.voxtart.prepare_character(), "Character rig prepared")
            return
        if key == "character:rebuild":
            _run_operator(scene, lambda: bpy.ops.voxtart.character_rebuild(), "Character rebuilt")
            return
        if key == "character:export":
            _run_operator(scene, lambda: bpy.ops.voxtart.export_game_rig("INVOKE_DEFAULT"), "Choose a rig export path")
            return
        if key == "character:toolbox":
            _set_status(scene, "Full toolbox opened")
            self._finish(context)

    def modal(self, context, event):
        area = _ACTIVE_AREA or getattr(context, "area", None)
        if area is not None:
            area.tag_redraw()
        if event.type == "ESC":
            self._finish(context)
            return {"CANCELLED"}
        if event.type == "MOUSEMOVE":
            state = _ACTIVE_STATE
            if state is not None:
                x = event.mouse_region_x
                y = event.mouse_region_y
                state.mouse_x = x
                state.mouse_y = y
                hovered = next(
                    (key for key, bounds in tuple(state.targets) if _inside(x, y, bounds)),
                    None,
                )
                if state.hovered != hovered:
                    state.hovered = hovered
                    if area is not None:
                        area.tag_redraw()
            return {"PASS_THROUGH"}
        if event.value == "PRESS" and not event.shift and not event.ctrl and not event.alt:
            mode = {"B": "BUILD", "P": "PAINT", "C": "CHARACTER"}.get(event.type)
            if mode is not None:
                self._activate(context, f"mode:{mode}")
                return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            state = _ACTIVE_STATE
            if state is None:
                return {"PASS_THROUGH"}
            x = event.mouse_region_x
            y = event.mouse_region_y
            for key, bounds in tuple(state.targets):
                if _inside(x, y, bounds):
                    self._activate(context, key)
                    return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def invoke(self, context, _event):
        global _ACTIVE_AREA, _ACTIVE_HANDLE, _ACTIVE_STATE

        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "VOXTART Shell needs a 3D View")
            return {"CANCELLED"}
        _ACTIVE_AREA = context.area
        try:
            context.area.spaces.active.show_region_ui = False
        except (AttributeError, RuntimeError, TypeError):
            pass
        _ACTIVE_STATE = _ShellState()
        _ACTIVE_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
            _draw_shell,
            (_ACTIVE_STATE, context),
            "WINDOW",
            "POST_PIXEL",
        )
        context.scene[SHELL_OPEN_PROPERTY] = True
        if SHELL_MODE_PROPERTY not in context.scene:
            context.scene[SHELL_MODE_PROPERTY] = "HOME"
        if SHELL_STATUS_PROPERTY not in context.scene:
            context.scene[SHELL_STATUS_PROPERTY] = "Ready"
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}


def open_shell() -> bool:
    """Open the shell in the first available interactive 3D View."""
    if bpy.app.background:
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
            with bpy.context.temp_override(window=window, screen=screen, area=area, region=region):
                result = bpy.ops.voxtart.desktop_shell("INVOKE_DEFAULT")
            return "RUNNING_MODAL" in result
    return False


_CLASSES = (VOXTART_OT_desktop_shell,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "SHELL_MODE_PROPERTY",
    "SHELL_MODES",
    "SHELL_OPEN_PROPERTY",
    "SHELL_STATUS_PROPERTY",
    "open_shell",
    "register",
    "unregister",
]
