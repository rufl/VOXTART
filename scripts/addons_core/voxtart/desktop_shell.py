# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A focused VOXTART project surface drawn over the bundled Blender viewport.

The shell is a secondary project surface reached from the editor's Project
control. It keeps Blender hidden by default and exposes the product's project,
palette, asset, and advanced-workflow controls without making them part of the
first-run editor path.
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from . import ui_style
from .operators import ASSET_PROPERTY
from .palette import DEFAULT_PALETTE, scene_linear_to_srgb
from .voxel_editor import (
    EDITOR_MODE_PROPERTY,
    EDITOR_SHAPE_PROPERTY,
    FIRST_RUN_HINT_PROPERTY,
    EDITOR_TOOLBAR_ITEMS,
    BEGINNER_TOOLBAR_ITEMS,
    visible_editor_toolbar_items,
)

SHELL_MODE_PROPERTY = "voxtart_shell_mode"
SHELL_OPEN_PROPERTY = "voxtart_shell_open"
SHELL_STATUS_PROPERTY = "voxtart_shell_status"
SHELL_MODES = ("HOME", "BUILD", "PAINT", "CHARACTER")

_TOPBAR_HEIGHT = 48
_BOTTOMBAR_HEIGHT = 32
_LEFT_WIDTH = 224
_RIGHT_WIDTH = 272
_SHELL_TOOLBAR_WIDTH = 64
_SHELL_BEGINNER_TOOLBAR_WIDTH = 148

_SHELL_TOOL_LABELS = {
    ("MODE", "ADD"): "Add block",
    ("MODE", "ATTACH"): "Stick blocks",
    ("MODE", "ERASE"): "Erase block",
    ("MODE", "PAINT"): "Color block",
    ("MODE", "SELECT"): "Select blocks",
    ("SHAPE", "VOXEL"): "Single block",
    ("SHAPE", "LINE"): "Line brush",
    ("SHAPE", "BOX"): "Box brush",
    ("SHAPE", "SPHERE"): "Ball brush",
    ("SHAPE", "FACE"): "Face brush",
    ("SELECT", "LASSO"): "Freehand select",
    ("SELECT", "CIRCLE"): "Circle select",
    ("SELECT", "COLOR"): "Same-color select",
    ("SELECT", "COMPONENT"): "Connected select",
    ("SELECT", "SURFACE"): "Surface select",
}






_THEME = ui_style.THEME
_BG = _THEME["chrome"]
_PANEL = _THEME["panel"]
_PANEL_LIGHT = _THEME["panel_raised"]
_BORDER = _THEME["border"]
_TEXT = _THEME["text"]
_MUTED = _THEME["text_muted"]
_ACCENT = _THEME["accent"]
_ACCENT_DARK = _THEME["accent_fill"]
_SUCCESS = _THEME["success"]

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
    ui_style.draw_text(text, x, y, size, color)


def _text_width(text: str, size: float) -> float:
    return ui_style.text_width(text, size)

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
    if name == "refresh":
        _draw_circle(x, y, size * 0.29, color, 14)
        _draw_line(((x - size * 0.08, y - size * 0.32), (x + size * 0.28, y - size * 0.32), (x + size * 0.28, y - size * 0.08)), _BG, 2.0)
        _draw_line(((x + size * 0.08, y + size * 0.32), (x - size * 0.28, y + size * 0.32), (x - size * 0.28, y + size * 0.08)), _BG, 2.0)
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
    if name in {"dock_left", "dock_right"}:
        direction = -1.0 if name == "dock_left" else 1.0
        edge_x = x + direction * size * 0.28
        inner_x = x - direction * size * 0.16
        _draw_line(
            (
                (edge_x, y - size * 0.34),
                (edge_x, y + size * 0.34),
                (inner_x, y + size * 0.34),
            ),
            color,
            1.5,
        )
        _draw_line(((inner_x, y - size * 0.34), (inner_x, y + size * 0.34)), color, 1.3)
        _draw_line(
            (
                (x - direction * size * 0.02, y - size * 0.12),
                (x - direction * size * 0.18, y),
                (x - direction * size * 0.02, y + size * 0.12),
            ),
            color,
            1.5,
        )
        return
    if name == "grid":
        for offset in (-size * 0.24, 0.0, size * 0.24):
            _draw_line(((x - size * 0.34, y + offset), (x + size * 0.34, y + offset)), color, 1.2)
            _draw_line(((x + offset, y - size * 0.34), (x + offset, y + size * 0.34)), color, 1.2)
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
    if name in {"undo", "redo"}:
        direction = -1.0 if name == "undo" else 1.0
        start_x = x + direction * size * 0.3
        end_x = x - direction * size * 0.28
        _draw_line(
            (
                (start_x, y - size * 0.28),
                (end_x, y - size * 0.28),
                (end_x - direction * size * 0.12, y),
                (end_x, y + size * 0.28),
            ),
            color,
            1.7,
        )
        _draw_line(
            (
                (start_x, y - size * 0.28),
                (start_x - direction * size * 0.18, y - size * 0.1),
            ),
            color,
            1.4,
        )
        return
    if name == "save":
        _draw_round_rect(x - half * 0.75, y - half * 0.7, size * 0.75, size * 0.8, color, 2.0)
        _draw_rect(x - size * 0.22, y + size * 0.02, size * 0.44, size * 0.2, _BG)
        _draw_round_rect(x - size * 0.2, y - size * 0.48, size * 0.4, size * 0.28, _BG, 1.0)
        return
    if name == "help":
        _draw_circle(x, y, size * 0.34, color, 16)
        _draw_line(((x - size * 0.08, y + size * 0.12), (x, y + size * 0.2), (x + size * 0.1, y + size * 0.1), (x + size * 0.1, y - size * 0.02), (x, y - size * 0.14)), color, 1.5)
        _draw_circle(x, y - size * 0.27, size * 0.045, color, 8)
        return
    if name == "details":
        _draw_circle(x, y, size * 0.34, color, 16)
        _draw_line(((x, y - size * 0.08), (x, y + size * 0.24)), color, 1.6)
        _draw_circle(x, y - size * 0.22, size * 0.045, color, 8)
        return
    if name == "attach":
        _draw_line(((x - size * 0.32, y + size * 0.1), (x - size * 0.08, y + size * 0.34), (x + size * 0.3, y - size * 0.04), (x + size * 0.08, y - size * 0.28), (x - size * 0.32, y + size * 0.1)), color, 1.5)
        _draw_line(((x - size * 0.08, y + size * 0.34), (x + size * 0.34, y + size * 0.34)), color, 1.5)
        return
    if name == "erase":
        _draw_line(((x - size * 0.34, y - size * 0.14), (x - size * 0.02, y - size * 0.42), (x + size * 0.34, y - size * 0.06), (x + size * 0.02, y + size * 0.28), (x - size * 0.34, y - size * 0.14)), color, 1.5)
        _draw_line(((x - size * 0.03, y - size * 0.41), (x + size * 0.32, y - size * 0.06)), color, 1.2)
        return
    if name == "line":
        _draw_line(((x - size * 0.34, y - size * 0.28), (x + size * 0.34, y + size * 0.28)), color, 1.8)
        _draw_circle(x - size * 0.34, y - size * 0.28, size * 0.07, color, 8)
        _draw_circle(x + size * 0.34, y + size * 0.28, size * 0.07, color, 8)
        return
    if name == "box":
        _draw_line(((x - size * 0.3, y - size * 0.25), (x + size * 0.28, y - size * 0.25), (x + size * 0.28, y + size * 0.28), (x - size * 0.3, y + size * 0.28), (x - size * 0.3, y - size * 0.25)), color, 1.5)
        _draw_line(((x - size * 0.3, y + size * 0.28), (x - size * 0.08, y + size * 0.42), (x + size * 0.28, y + size * 0.28)), color, 1.2)
        return
    if name == "sphere":
        _draw_circle(x, y, size * 0.34, color, 18)
        _draw_line(((x - size * 0.28, y), (x - size * 0.14, y - size * 0.12), (x + size * 0.14, y - size * 0.12), (x + size * 0.28, y)), color, 1.0)
        _draw_line(((x - size * 0.28, y), (x - size * 0.14, y + size * 0.12), (x + size * 0.14, y + size * 0.12), (x + size * 0.28, y)), color, 1.0)
        return
    if name == "face":
        _draw_line(((x - size * 0.32, y - size * 0.28), (x + size * 0.32, y - size * 0.28), (x + size * 0.32, y + size * 0.28), (x - size * 0.32, y + size * 0.28), (x - size * 0.32, y - size * 0.28)), color, 1.5)
        _draw_line(((x, y - size * 0.42), (x, y + size * 0.42)), color, 1.2)
        _draw_line(((x - size * 0.12, y + size * 0.3), (x, y + size * 0.42), (x + size * 0.12, y + size * 0.3)), color, 1.2)
        return
    if name == "lasso":
        points = tuple(
            (
                x + math.cos(math.tau * index / 12.0) * size * (0.3 + 0.06 * math.sin(index)),
                y + math.sin(math.tau * index / 12.0) * size * (0.34 + 0.05 * math.cos(index)),
            )
            for index in range(13)
        )
        _draw_line(points, color, 1.5)
        return
    if name == "circle":
        _draw_circle(x, y, size * 0.3, color, 16)
        _draw_line(((x - size * 0.48, y), (x - size * 0.28, y)), color, 1.2)
        _draw_line(((x + size * 0.28, y), (x + size * 0.48, y)), color, 1.2)
        _draw_line(((x, y - size * 0.48), (x, y - size * 0.28)), color, 1.2)
        _draw_line(((x, y + size * 0.28), (x, y + size * 0.48)), color, 1.2)
        return
    if name == "component":
        _draw_line(((x - size * 0.28, y), (x, y + size * 0.28), (x + size * 0.3, y - size * 0.22)), color, 1.4)
        _draw_line(((x - size * 0.28, y), (x + size * 0.12, y - size * 0.32)), color, 1.2)
        for offset_x, offset_y in ((-0.28, 0.0), (0.0, 0.28), (0.3, -0.22), (0.12, -0.32)):
            _draw_circle(x + size * offset_x, y + size * offset_y, size * 0.08, color, 8)
        return
    if name == "image":
        _draw_round_rect(x - half * 0.72, y - half * 0.58, size * 0.72, size * 0.68, color, 2.0)
        _draw_circle(x - size * 0.2, y + size * 0.18, size * 0.07, color, 8)
        _draw_line(
            (
                (x - size * 0.48, y - size * 0.22),
                (x - size * 0.16, y + size * 0.08),
                (x + size * 0.02, y - size * 0.08),
                (x + size * 0.2, y + size * 0.12),
                (x + size * 0.28, y - size * 0.22),
            ),
            color,
            1.2,
        )
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
    "build:refresh": "refresh",
    "build:export": "export",
    "palette:import": "palette",
    "palette:reduce": "optimize",
    "voxel:import": "folder",
    "format:blockbench": "cube",
    "format:stack": "layers",
    "format:minecraft": "cube",
    "gallery:save": "layers",
    "scene:sync": "refresh",
    "kitbash:snap": "place",
    "build:optimize": "optimize",
    "build:editor": "cube",
    "chat:image": "image",
    "paint:pick": "pick",
    "paint:paint": "paint",
    "paint:remove": "remove",
    "paint:place": "place",
    "character:prepare": "character",
    "character:import": "folder",
    "character:extract": "build",
    "character:bake": "build",
    "character:preview": "paint",
    "character:export": "export",
    "character:rebuild": "build",
}
_ICON_FOR_TOOL_KEY = {
    "tool:MODE:ADD": "place",
    "tool:MODE:ATTACH": "attach",
    "tool:MODE:ERASE": "erase",
    "tool:MODE:PAINT": "paint",
    "tool:MODE:SELECT": "pick",
    "tool:SHAPE:VOXEL": "cube",
    "tool:SHAPE:LINE": "line",
    "tool:SHAPE:BOX": "box",
    "tool:SHAPE:SPHERE": "sphere",
    "tool:SHAPE:FACE": "face",
    "tool:SELECT:LASSO": "lasso",
    "tool:SELECT:CIRCLE": "circle",
    "tool:SELECT:COLOR": "palette",
    "tool:SELECT:COMPONENT": "component",
    "tool:SELECT:SURFACE": "surface",
}


_TOOLTIP_DESCRIPTIONS = {
    "mode:BUILD": "Build and edit your block worlds.",
    "mode:PAINT": "Choose a color, then paint your blocks.",
    "mode:CHARACTER": "Turn your blocks into a character.",
    "project:blocks": "Go back to your block-making workspace.",
    "project:characters": "Open character tools and animation.",
    "chat:image": "Add a reference image for the local model.",
    "project:layers": "Work with colors and layers.",
    "home:make": "Start with a small block world you can change.",
    "home:open": "Open a saved block world or voxel model.",
    "build:voxelize": "Turn the current shape into editable blocks.",
    "build:refresh": "Rebuild blocks from the current shape.",
    "build:export": "Save a 3D copy you can use elsewhere.",
    "build:optimize": "Make the block model lighter to work with.",
    "build:editor": "Open the hands-on block editor.",
    "palette:import": "Load a color palette from a file.",
    "palette:reduce": "Simplify the palette to fewer colors.",
    "voxel:import": "Open a voxel model as a block world.",
    "format:blockbench": "Open a Blockbench model.",
    "format:stack": "Turn a layered picture into blocks.",
    "format:minecraft": "Open a Minecraft map.",
    "gallery:save": "Save this block model to the Blob Gallery.",
    "scene:sync": "Sync the scene hierarchy from the selected shape.",
    "kitbash:snap": "Join nearby block pieces together.",
    "paint:pick": "Pick a block to work on.",
    "paint:paint": "Color the block you picked.",
    "paint:remove": "Remove the block you picked.",
    "paint:place": "Add the next block using this color.",
    "character:prepare": "Set up a block character.",
    "character:import": "Open an animation file.",
    "character:extract": "Make a game-ready character rig.",
    "character:bake": "Save animation into the character.",
    "character:preview": "Preview the character animation.",
    "character:export": "Save the character for a game.",
    "character:rebuild": "Build the character again from its blocks.",
}


_TOOLTIP_TOOL_DESCRIPTIONS = {
    ("MODE", "ADD"): "Click a face to add one block.",
    ("MODE", "ATTACH"): "Stick a brush onto the face you click.",
    ("MODE", "ERASE"): "Click a block to remove it.",
    ("MODE", "PAINT"): "Drag over blocks to color them.",
    ("MODE", "SELECT"): "Click blocks to select and copy them.",
    ("SHAPE", "VOXEL"): "Work on one block at a time.",
    ("SHAPE", "LINE"): "Drag to make a straight line.",
    ("SHAPE", "BOX"): "Drag to make a box.",
    ("SHAPE", "SPHERE"): "Drag to make a round shape.",
    ("SHAPE", "FACE"): "Make a brush that follows a face.",
    ("SELECT", "LASSO"): "Draw around the blocks you want.",
    ("SELECT", "CIRCLE"): "Drag a circle around blocks.",
    ("SELECT", "COLOR"): "Pick every block with this color.",
    ("SELECT", "COMPONENT"): "Pick the connected block group.",
    ("SELECT", "SURFACE"): "Pick the flat surface under the cursor.",
}


def _tooltip_description(key: str, label: str) -> str:
    if key in _TOOLTIP_DESCRIPTIONS:
        return _TOOLTIP_DESCRIPTIONS[key]
    if key.startswith("tool:"):
        parts = key.split(":", 2)
        if len(parts) == 3:
            return _TOOLTIP_TOOL_DESCRIPTIONS.get(
                (parts[1], parts[2]),
                "Choose this tool for your next edit.",
            )
    if key.startswith("palette:"):
        return "Choose this color for the next block."
    if key.startswith("part:"):
        return "Select this block model."
    return f"{label}. Choose this control to continue."


_SHORTCUT_FOR_KEY = {
    "mode:BUILD": "B",
    "mode:PAINT": "P",
    "mode:CHARACTER": "C",
    "paint:pick": "Alt+Shift+E",
    "paint:paint": "Alt+Shift+P",
    "paint:remove": "Alt+Shift+X",
    "paint:place": "Alt+Shift+V",
}


def _register_tooltip(
    state,
    key: str,
    label: str,
    shortcut: str = "",
    description: str | None = None,
) -> None:
    state.tooltips[key] = (
        label,
        shortcut,
        description or _tooltip_description(key, label),
    )


def _draw_tooltip(state, width: float, height: float) -> None:
    if state.hovered is None or state.hovered not in state.tooltips:
        return
    label, shortcut, description = state.tooltips[state.hovered]
    max_width = min(360.0, width - 36.0)
    description_lines = ui_style.wrap_text(description, 11.0, max_width - 24.0)[:2]
    if not description_lines:
        description_lines = ("Choose this control to continue.",)
    title_width = _text_width(label, 13.0)
    shortcut_width = _text_width(shortcut, 10.0) + 16.0 if shortcut else 0.0
    description_width = max(
        (_text_width(line, 11.0) for line in description_lines),
        default=0.0,
    )
    tooltip_width = min(
        width - 24.0,
        max(220.0, title_width + shortcut_width + 38.0, description_width + 24.0),
    )
    tooltip_height = 56.0 + max(0, len(description_lines) - 1) * 14.0
    x = min(max(12.0, state.mouse_x + 14.0), width - tooltip_width - 12.0)
    y = min(
        max(_BOTTOMBAR_HEIGHT + 8.0, state.mouse_y - tooltip_height - 12.0),
        height - tooltip_height - 8.0,
    )
    _draw_round_rect(x, y, tooltip_width, tooltip_height, _PANEL_LIGHT, 9.0)
    _draw_line(
        (
            (x, y),
            (x + tooltip_width, y),
            (x + tooltip_width, y + tooltip_height),
            (x, y + tooltip_height),
            (x, y),
        ),
        _ACCENT,
        1.2,
    )
    title_y = y + tooltip_height - 20.0
    _draw_text(label, x + 12.0, title_y, 13.0, _TEXT)
    if shortcut:
        pill_width = shortcut_width
        pill_x = x + tooltip_width - pill_width - 10.0
        _draw_round_rect(pill_x, title_y - 3.0, pill_width, 18.0, _ACCENT_DARK, 8.0)
        _draw_text(shortcut, pill_x + 8.0, title_y + 1.0, 10.0, _TEXT)
    for index, line in enumerate(description_lines):
        _draw_text(
            line,
            x + 12.0,
            y + 11.0 + (len(description_lines) - index - 1) * 14.0,
            11.0,
            _MUTED,
        )



def _draw_centered(text: str, x: float, y: float, width: float, size: float, color=_TEXT) -> None:
    _draw_text(text, x + max(0.0, (width - _text_width(text, size)) / 2.0), y, size, color)


def _inside(x: float, y: float, bounds) -> bool:
    left, bottom, right, top = bounds
    return left <= x <= right and bottom <= y <= top


def _project_name() -> str:
    filepath = str(bpy.data.filepath or "").strip()
    return Path(filepath).stem if filepath else "My block world"




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
    _set_status(scene, "Cancelled — no changes were made")
    return False

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
    shortcut = _SHORTCUT_FOR_KEY.get(key, "")
    if shortcut:
        shortcut_width = _text_width(shortcut, 10)
        _draw_text(
            shortcut,
            x + width - shortcut_width - 10,
            y + (height - 11) / 2.0,
            10,
            _MUTED,
        )
    _register_tooltip(state, key, label, shortcut)
    state.targets.append((key, (x, y, x + width, y + height)))


def _draw_compact_tool_button(
    state,
    key: str,
    glyph: str,
    label: str,
    shortcut: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    active=False,
    label_first=False,
) -> None:
    hovered = getattr(state, "hovered", None) == key
    color = _PANEL_LIGHT if active or hovered else _PANEL
    _draw_round_rect(x, y, width, height, color, min(7.0, height / 2.0))
    _draw_line(
        _rounded_points(x, y, width, height, min(7.0, height / 2.0)),
        _ACCENT if active or hovered else _BORDER,
    )
    icon = _ICON_FOR_TOOL_KEY.get(key)
    if label_first:
        if icon:
            _draw_icon(icon, x + 17.0, y + height / 2.0, min(17.0, height - 8.0), _TEXT)
            text_x = x + 31.0
        else:
            text_x = x + 10.0
        _draw_text(label, text_x, y + (height - 13.0) / 2.0, 12.0, _TEXT)
        shortcut_width = _text_width(shortcut, 10.0)
        _draw_text(
            shortcut,
            x + width - shortcut_width - 10.0,
            y + (height - 11.0) / 2.0,
            10.0,
            _MUTED,
        )
    elif icon:
        _draw_icon(icon, x + width / 2.0, y + height / 2.0, min(18.0, height - 8.0), _TEXT)
    else:
        _draw_centered(glyph, x, y + (height - 16.0) / 2.0, width, 16.0, _TEXT)
    _register_tooltip(state, key, label, shortcut)
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
    items = visible_editor_toolbar_items(context.scene)
    beginner = items is BEGINNER_TOOLBAR_ITEMS
    toolbar_width = _SHELL_BEGINNER_TOOLBAR_WIDTH if beginner else _SHELL_TOOLBAR_WIDTH
    panel_x = _LEFT_WIDTH + 12.0
    panel_y = _BOTTOMBAR_HEIGHT + 12.0
    panel_top = height - _TOPBAR_HEIGHT - 12.0
    panel_height = max(80.0, panel_top - panel_y)
    _draw_round_rect(panel_x, panel_y, toolbar_width, panel_height, _PANEL, 10.0)
    _draw_line(
        _rounded_points(panel_x, panel_y, toolbar_width, panel_height, 10.0),
        _BORDER,
    )
    _draw_text("TOOLS", panel_x + 12.0, panel_top - 25.0, 11, _MUTED)

    item_count = sum(item is not None for item in items)
    separator_count = sum(item is None for item in items)
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
    button_width = toolbar_width - 16.0
    cursor = content_top
    for item in items:
        if item is None:
            cursor -= separator_gap
            continue
        key = f"tool:{item[0]}:{item[1]}"
        y = cursor - button_height
        label = item[3] if beginner else _SHELL_TOOL_LABELS.get(item[:2], item[3])
        _draw_compact_tool_button(
            state,
            key,
            item[2],
            label,
            item[4],
            panel_x + 8.0,
            y,
            button_width,
            button_height,
            active=_shell_toolbar_active(context.scene, item),
            label_first=beginner,
        )
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
    first_run = bool(context.scene.get(FIRST_RUN_HINT_PROPERTY))
    nav_items = [
        ("BUILD", "Make", 98),
        ("PAINT", "Color", 98),
    ]
    if not first_run:
        nav_items.append(("CHARACTER", "Character", 118))
    nav_x = project_x + project_width + 20
    for key, label, button_width in nav_items:
        _draw_button(state, f"mode:{key}", label, nav_x, y + 8, button_width, 32, active=mode == key)
        nav_x += button_width + 8





def _draw_left_panel(state, context, width: float, height: float) -> None:
    panel_top = height - _TOPBAR_HEIGHT
    panel_height = height - _TOPBAR_HEIGHT - _BOTTOMBAR_HEIGHT
    _draw_rect(0, _BOTTOMBAR_HEIGHT, _LEFT_WIDTH, panel_height, _PANEL)
    x = 16
    _draw_text("PROJECT", x, panel_top - 28, 11, _MUTED)
    _draw_text(_project_name()[:24], x, panel_top - 55, 15, _TEXT)
    _draw_line(((x, panel_top - 68), (_LEFT_WIDTH - 16, panel_top - 68)), _BORDER)

    _draw_text("WORKSPACES", x, panel_top - 94, 11, _MUTED)
    mode = _active_mode(context.scene)
    first_run = bool(context.scene.get(FIRST_RUN_HINT_PROPERTY))
    rows = (
        (("project:blocks", "Blocks", "BUILD"),)
        if first_run
        else (
            ("project:blocks", "Blocks", "BUILD"),
            ("project:characters", "Characters", "CHARACTER"),
            ("project:layers", "Layers", "PAINT"),
        )
    )
    for index, (key, label, row_mode) in enumerate(rows):
        y = panel_top - 126 - index * 32
        _draw_button(state, key, label, x, y, _LEFT_WIDTH - 32, 28, active=mode == row_mode)

    quick_y = panel_top - (180 if first_run else 240)
    _draw_text("START HERE" if first_run else "QUICK ACTIONS", x, quick_y, 11, _MUTED)
    _draw_button(state, "home:make", "Make blocks", x, quick_y - 38, _LEFT_WIDTH - 32, 32, accent=True)
    if not first_run:
        _draw_button(state, "home:open", "Open something", x, quick_y - 76, _LEFT_WIDTH - 32, 32)
    _draw_text("You can undo every change.", x, _BOTTOMBAR_HEIGHT + 18, 10, _MUTED)



def _draw_right_panel(state, context, width: float, height: float) -> None:
    x0 = width - _RIGHT_WIDTH
    panel_top = height - _TOPBAR_HEIGHT
    panel_height = height - _TOPBAR_HEIGHT - _BOTTOMBAR_HEIGHT
    _draw_rect(x0, _BOTTOMBAR_HEIGHT, _RIGHT_WIDTH, panel_height, _PANEL)
    x = x0 + 16
    _draw_text("COLORS", x, panel_top - 28, 11, _MUTED)
    _draw_text("Choose a color", x, panel_top - 53, 15, _TEXT)

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
        _draw_round_rect(sx, sy, swatch_size, swatch_size, scene_linear_to_srgb(color), 7.0)
        _draw_line(
            _rounded_points(sx, sy, swatch_size, swatch_size, 7.0),
            _TEXT if selected else _BORDER,
            2.0 if selected else 1.0,
        )
        _register_tooltip(state, f"palette:{index}", f"Color slot {index + 1}")
        state.targets.append((f"palette:{index}", (sx, sy, sx + swatch_size, sy + swatch_size)))

    parts_y = start_y - 4 * (swatch_size + gap) - 26
    for key, label, offset in (
        ("palette:import", "Load a palette", 64),
        ("palette:reduce", "Simplify colors", 34),
        ("voxel:import", "Open a voxel model", 4),
        ("format:blockbench", "Open Blockbench", -26),
        ("format:minecraft", "Open Minecraft", -56),
        ("format:stack", "Turn picture into blocks", -86),
        ("gallery:save", "Save to Gallery", -116),
        ("scene:sync", "Sync scene", -146),
        ("kitbash:snap", "Join pieces", -176),
    ):
        _draw_button(
            state,
            key,
            label,
            x,
            parts_y + offset,
            _RIGHT_WIDTH - 32,
            28,
        )
    parts_y -= 236
    _draw_text("YOUR MODELS", x, parts_y, 11, _MUTED)
    _draw_text("Block models in this project", x, parts_y - 25, 13, _TEXT)
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
                28,
                active=obj == active,
            )
    else:
        _draw_text("Make blocks or open a model to see it here.", x, parts_y - 52, 12, _MUTED)


def _draw_bottom_bar(context, width: float, height: float) -> None:
    _draw_rect(0, 0, width, _BOTTOMBAR_HEIGHT, _BG)
    scene = context.scene
    status = str(scene.get(SHELL_STATUS_PROPERTY, "Ready"))[:64]
    status_lower = status.lower()
    if any(
        fragment in status_lower
        for fragment in ("error", "failed", "invalid", "cancel", "unable", "no ", "not ")
    ):
        status_fill = _THEME["danger_fill"]
        status_border = _THEME["danger"]
        status_accent = _THEME["danger"]
        status_text = (1.0, 0.9, 0.88, 1.0)
    elif any(
        fragment in status_lower
        for fragment in (
            "ready",
            "saved",
            "created",
            "exported",
            "complete",
            "refreshed",
            "reduced",
            "painted",
            "voxelized",
            "baked",
            "added",
            "removed",
            "picked",
            "selected",
            "opened",
            "optimized",
            "synchronized",
        )
    ):
        status_fill = _THEME["success_fill"]
        status_border = _THEME["success"]
        status_accent = _THEME["success"]
        status_text = (0.88, 1.0, 0.92, 1.0)
    else:
        status_fill = _THEME["accent_soft"]
        status_border = _THEME["accent"]
        status_accent = _THEME["accent"]
        status_text = _THEME["text"]
    status_width = min(420.0, max(180.0, _text_width(status, 11) + 40.0))
    _draw_round_rect(16.0, 4.0, status_width, 24.0, status_fill, 12.0)
    _draw_line(
        _rounded_points(16.0, 4.0, status_width, 24.0, 12.0),
        status_border,
    )
    _draw_circle(29.0, 16.0, 3.0, status_accent, segments=12)
    _draw_text(status, 40.0, 10.0, 11, status_text)

    assets = _asset_objects()
    active = _active_asset(context)
    active_name = active.name[:22] if active is not None else "No block model selected"
    asset_summary = f"{len(assets)} block model(s)  ·  {active_name}"
    summary_width = min(320.0, max(180.0, _text_width(asset_summary, 11) + 28.0))
    summary_x = max(
        _LEFT_WIDTH + 24.0,
        width - _RIGHT_WIDTH - summary_width - 16.0,
    )
    _draw_round_rect(summary_x, 4.0, summary_width, 24.0, _PANEL, 12.0)
    _draw_line(
        _rounded_points(summary_x, 4.0, summary_width, 24.0, 12.0),
        _BORDER,
    )
    _draw_text(asset_summary, summary_x + 14.0, 10.0, 11, _TEXT)

def _chat_action_at(width: float, height: float, x: float, y: float) -> str | None:
    from . import mcp

    for action in ("chat:image", "chat:field", "chat:send"):
        bounds = mcp.chat_layout(width, height).get(action)
        if bounds is not None and _inside(x, y, bounds):
            return action
    return None


def _draw_chat_prompt(state, context, width: float, height: float) -> None:
    from . import mcp

    layout = mcp.chat_layout(width, height)
    panel_x, panel_y, panel_right, panel_top = layout["panel"]
    panel_width = panel_right - panel_x
    _draw_round_rect(panel_x, panel_y, panel_width, panel_top - panel_y, _PANEL, 12.0)
    _draw_line(
        _rounded_points(panel_x, panel_y, panel_width, panel_top - panel_y, 12.0),
        _ACCENT if mcp.chat_input_focused() else _BORDER,
    )
    model_label = mcp.chat_model_label()
    status = str(getattr(mcp._window_manager(), "voxtart_chat_status", "Ready"))[:42]
    _draw_text("ASK VOXTART", panel_x + 12.0, panel_top - 17.0, 9.0, _ACCENT)
    status_x = panel_right - _text_width(status, 9.0) - 12.0
    _draw_text(status, max(panel_x + 108.0, status_x), panel_top - 17.0, 9.0, _MUTED)
    _draw_text(model_label, panel_x + 12.0, panel_top - 31.0, 9.0, _MUTED)

    image_bounds = layout["chat:image"]
    _draw_button(
        state,
        "chat:image",
        "Image",
        image_bounds[0],
        image_bounds[1],
        image_bounds[2] - image_bounds[0],
        image_bounds[3] - image_bounds[1],
        active=bool(mcp.chat_attachment_names()),
    )
    field_x, field_y, field_right, field_top = layout["chat:field"]
    focused = mcp.chat_input_focused()
    _draw_round_rect(
        field_x,
        field_y,
        field_right - field_x,
        field_top - field_y,
        _PANEL_LIGHT if focused else _BG,
        8.0,
    )
    _draw_line(
        _rounded_points(field_x, field_y, field_right - field_x, field_top - field_y, 8.0),
        _ACCENT if focused else _BORDER,
    )
    prompt = str(getattr(mcp._window_manager(), "voxtart_chat_prompt", "")).strip()
    attachment_label = mcp.chat_attachment_label()
    display = prompt
    if attachment_label:
        display = f"{attachment_label}  ·  {display}" if display else attachment_label
    if not display:
        display = "Describe what to make, edit, or inspect…"
        text_color = _MUTED
    else:
        text_color = _TEXT
    if len(display) > 82:
        display = f"…{display[-81:]}"
    _draw_text(display, field_x + 12.0, field_y + 14.0, 11.0, text_color)
    if focused:
        caret_x = min(field_right - 10.0, field_x + 12.0 + _text_width(display, 11.0) + 2.0)
        _draw_line(((caret_x, field_y + 10.0), (caret_x, field_y + 29.0)), _ACCENT, 1.2)
    state.tooltips["chat:field"] = (
        "Prompt",
        "Enter",
        "Ask the local model to create or change VOXTART content.",
    )
    state.targets.append(("chat:field", layout["chat:field"]))

    send_bounds = layout["chat:send"]
    _draw_button(
        state,
        "chat:send",
        "Send",
        send_bounds[0],
        send_bounds[1],
        send_bounds[2] - send_bounds[0],
        send_bounds[3] - send_bounds[1],
        accent=True,
    )


def _draw_home(state, context, width: float, height: float) -> None:
    center_left = _LEFT_WIDTH
    center_right = width - _RIGHT_WIDTH
    center_width = center_right - center_left
    card_width = min(720.0, center_width - 80.0)
    card_height = 300.0
    card_x = center_left + (center_width - card_width) / 2.0
    card_y = _BOTTOMBAR_HEIGHT + (height - _TOPBAR_HEIGHT - _BOTTOMBAR_HEIGHT - card_height) / 2.0

    _draw_round_rect(card_x, card_y, card_width, card_height, _BG, 14.0)
    _draw_line(_rounded_points(card_x, card_y, card_width, card_height, 14.0), _BORDER)
    _draw_centered("Pick a starting point", card_x, card_y + card_height - 58, card_width, 24, _TEXT)
    _draw_centered("You can change or undo every choice.", card_x, card_y + card_height - 84, card_width, 13, _MUTED)
    button_y = card_y + 78
    button_gap = 16
    cards = [
        ("home:make", "Make blocks", "Build a tiny world", True),
        ("home:open", "Open a model", "Bring in something", False),
    ]
    if not context.scene.get(FIRST_RUN_HINT_PROPERTY):
        cards.append(("mode:CHARACTER", "Make a character", "Shape and animate it", False))
    button_width = (card_width - 64 - button_gap * (len(cards) - 1)) / len(cards)
    for index, (key, title, subtitle, accent) in enumerate(cards):
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
    toolbar_width = (
        _SHELL_BEGINNER_TOOLBAR_WIDTH
        if visible_editor_toolbar_items(context.scene) is BEGINNER_TOOLBAR_ITEMS
        else _SHELL_TOOLBAR_WIDTH
    )
    center_left = _LEFT_WIDTH + toolbar_width + 24
    center_right = width - _RIGHT_WIDTH
    center_width = center_right - center_left
    mode = _active_mode(context.scene)
    title, subtitle = {
        "BUILD": ("Make blocks", "Choose a task below. You can undo every change."),
        "PAINT": ("Color your blocks", "Pick a color, then click a block to paint it."),
        "CHARACTER": ("Make a character", "Start with blocks, then build it in small steps."),
    }[mode]
    _draw_text(title, center_left + 42, height - _TOPBAR_HEIGHT - 54, 25, _TEXT)
    _draw_text(subtitle, center_left + 42, height - _TOPBAR_HEIGHT - 78, 13, _MUTED)

    card_x = center_left + 42
    card_y = _BOTTOMBAR_HEIGHT + 52
    card_width = min(560.0, center_width - 84.0)
    card_height = 340.0 if mode == "BUILD" else (360.0 if mode == "CHARACTER" else 250.0)
    _draw_round_rect(card_x, card_y, card_width, card_height, _BG, 12.0)
    _draw_line(_rounded_points(card_x, card_y, card_width, card_height, 12.0), _BORDER)
    active = _active_asset(context)
    active_label = f"Working on: {active.name[:34]}" if active is not None else "No block model selected yet"
    _draw_text(active_label, card_x + 24, card_y + card_height - 54, 14, _TEXT if active else _MUTED)
    mode_hint = (
        "Create or open a block model to unlock these tools."
        if active is None
        else {
            "BUILD": "Pick a button below to start building.",
            "PAINT": "Pick a color, then choose what to do with a block.",
            "CHARACTER": "Prepare a character, then add parts and animation.",
        }[mode]
    )
    _draw_text(
        mode_hint,
        card_x + 24,
        card_y + card_height - 78,
        11,
        _MUTED,
    )

    button_gap = 12
    button_width = (card_width - 48 - button_gap) / 2.0
    left = card_x + 24
    right = left + button_width + button_gap
    row_one = card_y + (166 if mode == "BUILD" else 164 if mode == "CHARACTER" else 76)
    row_two = card_y + (120 if mode == "BUILD" else 120 if mode == "CHARACTER" else 30)
    row_three = card_y + (74 if mode == "BUILD" else 76 if mode == "CHARACTER" else 30)
    row_four = card_y + 32
    if mode == "BUILD":
        _draw_button(state, "home:make", "Make blocks", left, row_one, button_width, 34, accent=True)
        _draw_button(state, "build:voxelize", "Turn a shape into blocks", right, row_one, button_width, 34)
        _draw_button(state, "build:refresh", "Refresh from shape", left, row_two, button_width, 34)
        _draw_button(state, "build:optimize", "Make it lighter", right, row_two, button_width, 34)
        _draw_button(state, "build:editor", "Edit blocks", left, row_three, button_width, 34)
        _draw_button(state, "build:export", "Save a 3D copy", right, row_three, button_width, 34)
    elif mode == "PAINT":
        _draw_button(state, "paint:pick", "Pick a block", left, row_one, button_width, 34, accent=True)
        _draw_button(state, "paint:paint", "Color picked block", right, row_one, button_width, 34)
        _draw_button(state, "paint:remove", "Remove picked block", left, row_two, button_width, 34)
        _draw_button(state, "paint:place", "Add next block", right, row_two, button_width, 34)
    else:
        _draw_button(state, "character:prepare", "Prepare character", left, row_one, button_width, 34, accent=True)
        _draw_button(state, "character:import", "Open animations", right, row_one, button_width, 34)
        _draw_button(state, "character:extract", "Make game rig", left, row_two, button_width, 34)
        _draw_button(state, "character:bake", "Bake animations", right, row_two, button_width, 34)
        _draw_button(state, "character:preview", "Preview animation", left, row_three, button_width, 34)
        _draw_button(state, "character:export", "Save for a game", right, row_three, button_width, 34)
        _draw_button(state, "character:rebuild", "Build again", right, row_four, button_width, 34)


def _draw_shell(state, context) -> None:
    width = context.region.width
    height = context.region.height
    state.targets = []
    state.tooltips = {}
    gpu.state.blend_set("ALPHA")
    try:
        _draw_rect(0, 0, width, height, _THEME["canvas"])
        _draw_topbar(state, context, width, height)
        _draw_left_panel(state, context, width, height)
        _draw_right_panel(state, context, width, height)
        mode = _active_mode(context.scene)
        if mode != "HOME":
            _draw_shell_toolbar(state, context, height)
        _draw_bottom_bar(context, width, height)
        if mode == "HOME":
            _draw_home(state, context, width, height)
        else:
            _draw_mode(state, context, width, height)
        _draw_chat_prompt(state, context, width, height)
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
        from . import desktop_ui

        try:
            from . import mcp

            mcp.set_chat_input_focus(False)
        except (ImportError, RuntimeError):
            pass
        desktop_ui.set_native_chrome_visibility(False)
        scene = getattr(context, "scene", None)
        if scene is not None:
            scene[SHELL_OPEN_PROPERTY] = False
        area = _ACTIVE_AREA or getattr(context, "area", None)
        _ACTIVE_AREA = None
        if area is not None and area.type == "VIEW_3D":
            area.tag_redraw()

    def _activate(self, context, key: str):
        scene = context.scene
        if key == "chat:image":
            from . import mcp

            mcp.set_chat_input_focus(False)
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.chat_attach_image("INVOKE_DEFAULT"),
                "Choose a reference image",
            )
            return
        if key == "chat:send":
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.chat_send(),
                "Prompt sent to the local model",
            )
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
                "Block builder ready",
            )
            return
        if key == "home:make":
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.add_voxel_cube(
                    size=int(scene.voxtart_cube_size),
                    grid_size=float(scene.voxtart_grid_size),
                ),
                "Block model created",
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
        if key == "voxel:import":
            self._finish(context)
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.import_voxel_format("INVOKE_DEFAULT"),
                "Choose a VOX or QB model",
            )
            return
        if key == "format:blockbench":
            self._finish(context)
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.import_blockbench("INVOKE_DEFAULT"),
                "Choose a Blockbench model",
            )
            return
        if key == "format:minecraft":
            self._finish(context)
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.import_minecraft("INVOKE_DEFAULT"),
                "Choose a Minecraft map or schematic",
            )
            return
        if key == "format:stack":
            self._finish(context)
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.import_stack("INVOKE_DEFAULT"),
                "Choose a layered stack or image document",
            )
            return
        if key == "gallery:save":
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.blob_gallery_save_active(),
                "Active asset saved to Zoo",
            )
            return
        if key == "scene:sync":
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.zoo_sync_placements(),
                "Scene hierarchy synchronized",
            )
            return
        if key == "kitbash:snap":
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.snap_active(),
                "Selected assets snapped",
            )
            return
        if key == "palette:reduce":
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.palette_reduce(
                    max_colors=int(scene.voxtart_palette_reduce_colors),
                    method=str(scene.voxtart_palette_reduce_method),
                ),
                "Palette reduced",
            )
            return
        if key == "palette:import":
            self._finish(context)
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.palette_import("INVOKE_DEFAULT"),
                "Choose a palette file",
            )
            return
        if key.startswith("palette:"):
            from . import palette_ui

            index = int(key.split(":", 1)[1])
            colors = _palette_colors(scene)
            if 0 <= index < len(colors):
                palette_ui._set_palette_selection(scene, index)
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
        if key == "build:refresh":
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.refresh_voxelizer(),
                "Linked asset refreshed",
            )
            return
        if key == "build:editor":
            _set_status(scene, "Block builder opened")
            self._finish(context)
            _run_operator(scene, lambda: bpy.ops.voxtart.voxel_editor("INVOKE_DEFAULT"), "Block builder ready")
            return
        if key == "build:optimize":
            _run_operator(scene, lambda: bpy.ops.voxtart.optimize_mesh(), "Active mesh optimized")
            return
        if key == "build:export":
            self._finish(context)
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.export_optimized_mesh("INVOKE_DEFAULT"),
                "Choose mesh export path",
            )
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
        if key == "character:import":
            self._finish(context)
            _run_operator(
                scene,
                lambda: bpy.ops.voxtart.import_animation_library("INVOKE_DEFAULT"),
                "Choose an animation source",
            )
            return
        if key == "character:extract":
            _run_operator(scene, lambda: bpy.ops.voxtart.extract_game_rig(), "Game rig extracted")
            return
        if key == "character:bake":
            _run_operator(scene, lambda: bpy.ops.voxtart.bake_game_rig_actions(), "Animation actions baked")
            return
        if key == "character:preview":
            _run_operator(scene, lambda: bpy.ops.voxtart.preview_game_rig_action(), "Animation preview ready")
            return
        if key == "character:rebuild":
            _run_operator(scene, lambda: bpy.ops.voxtart.character_rebuild(), "Character rebuilt")
            return
        if key == "character:export":
            _run_operator(scene, lambda: bpy.ops.voxtart.export_game_rig("INVOKE_DEFAULT"), "Choose a rig export path")
            return
    def modal(self, context, event):
        area = _ACTIVE_AREA or getattr(context, "area", None)
        if area is not None:
            area.tag_redraw()
        from . import desktop_ui, mcp

        if mcp.handle_chat_text_event(event):
            return {"RUNNING_MODAL"}
        if desktop_ui.is_native_ui_shortcut(event):
            _set_status(context.scene, "VOXTART controls only")
            return {"RUNNING_MODAL"}
        region = getattr(context, "region", None)
        width = float(getattr(region, "width", getattr(area, "width", 0.0)))
        height = float(getattr(region, "height", getattr(area, "height", 0.0)))
        if event.type == "ESC":
            _set_status(context.scene, "VOXTART stays open")
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE":
            state = _ACTIVE_STATE
            if state is not None:
                x = event.mouse_region_x
                y = event.mouse_region_y
                state.mouse_x = x
                state.mouse_y = y
                chat_action = _chat_action_at(width, height, x, y)
                if chat_action is not None:
                    hovered = chat_action
                else:
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
            chat_action = _chat_action_at(width, height, x, y)
            if chat_action is not None:
                if chat_action == "chat:field":
                    mcp.set_chat_input_focus(True)
                else:
                    self._activate(context, chat_action)
                return {"RUNNING_MODAL"}
            mcp.set_chat_input_focus(False)
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
        from . import desktop_ui

        desktop_ui.set_native_chrome_visibility(False)
        _ACTIVE_STATE = _ShellState()
        context.scene[SHELL_OPEN_PROPERTY] = True
        if SHELL_MODE_PROPERTY not in context.scene:
            has_starter = bool(context.scene.get(FIRST_RUN_HINT_PROPERTY)) or any(
                bool(obj.get("voxtart_default_pyramid")) for obj in bpy.data.objects
            )
            context.scene[SHELL_MODE_PROPERTY] = "BUILD" if has_starter else "HOME"
        if SHELL_STATUS_PROPERTY not in context.scene:
            context.scene[SHELL_STATUS_PROPERTY] = "Ready"
        _ACTIVE_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
            _draw_shell,
            (_ACTIVE_STATE, context),
            "WINDOW",
            "POST_PIXEL",
        )
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}


def open_shell() -> bool:
    """Open the shell in the first available interactive 3D View."""
    if bpy.app.background:
        return False
    from . import desktop_ui

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
            running = "RUNNING_MODAL" in result
            if running:
                desktop_ui.set_native_chrome_visibility(False)
            return running
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
