# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Interactive voxel editing session for the Blender 3D View.

The editor keeps Blender navigation intact while adding a compact voxel-first
workflow: add, erase, paint, select, brush shapes, mirror axes, palette
picking, clipboard placement, and per-stroke undo/redo.
"""

from __future__ import annotations

import colorsys
import json
import math
import time
from dataclasses import dataclass

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy_extras import view3d_utils
from mathutils import Vector
from . import ui_style
try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except ImportError:
    gpu = None
    batch_for_shader = None

from . import operators
from .edit import VoxelEditTransaction
from .operators import ASSET_PROPERTY, COMPONENT_SELECTION_PROPERTY
from .pick import RayHit, raycast_voxel
from .palette import scene_linear_to_srgb, srgb_to_scene_linear
from .volume_ops import mirror_asset, rotate_asset
from .voxel import Cell, VoxelAsset
from .voxel_brush import (
    AXES,
    BRUSH_SHAPES,
    box_cells,
    brush_cells,
    dilate_cells,
    face_brush_cells,
    line_cells,
    mirror_cells,
    select_box,
    select_circle,
    select_color,
    select_connected,
    select_flat_surface,
    select_lasso,
    sphere_cells,
)


EDITOR_MODES = ("ADD", "ATTACH", "ERASE", "PAINT", "SELECT")
EDITOR_MODE_ITEMS = (
    ("ADD", "Add", "Left click places; right click erases; drag paints"),
    ("ATTACH", "Attach", "Place an oriented brush on the clicked face"),
    ("ERASE", "Erase", "Remove voxels from the active layer"),
    ("PAINT", "Paint", "Recolor occupied voxels without changing shape"),
    ("SELECT", "Select", "Select voxels for copy, cut, and paste"),
)
EDITOR_SELECTION_SHAPES = ("LASSO", "CIRCLE", "COLOR", "COMPONENT", "SURFACE")
EDITOR_SHAPES = (*BRUSH_SHAPES, *EDITOR_SELECTION_SHAPES)
EDITOR_SHAPE_ITEMS = (
    ("VOXEL", "Voxel", "One voxel or a spherical brush stamp"),
    ("LINE", "Line", "Draw a straight voxel line from the stroke anchor"),
    ("BOX", "Box", "Draw an inclusive box between two stroke points"),
    ("SPHERE", "Sphere", "Draw a filled or hollow sphere"),
    ("CYLINDER", "Cylinder", "Draw a centered cylinder along the selected axis"),
    ("FACE", "Face", "Draw a brush aligned to the hit face normal"),
    ("LASSO", "Lasso", "Select projected cells inside a freehand lasso"),
    ("CIRCLE", "Circle", "Select projected cells inside a screen circle"),
    ("COLOR", "Color", "Select the connected region sharing the hit color"),
    ("COMPONENT", "Component", "Select the six-connected voxel component"),
    ("SURFACE", "Surface", "Select the exposed flat surface under the cursor"),
)

EDITOR_MODE_PROPERTY = "voxtart_editor_mode"
EDITOR_SHAPE_PROPERTY = "voxtart_editor_shape"
EDITOR_WORKSPACE_PROPERTY = "voxtart_editor_workspace"
EDITOR_SELECTION_PROPERTY = COMPONENT_SELECTION_PROPERTY
EDITOR_CLIPBOARD_PROPERTY = "voxtart_editor_clipboard"
EDITOR_STROKE_PROPERTY = "voxtart_editor_stroke_recovery"
FIRST_RUN_HINT_PROPERTY = "voxtart_first_run_hint"
EDITOR_DETAILS_PROPERTY = "voxtart_editor_details"
EDITOR_HELP_PROPERTY = "voxtart_editor_help"
EDITOR_ADVANCED_PROPERTY = "voxtart_editor_advanced_tools"
EDITOR_OPTIONS_PROPERTY = "voxtart_editor_options_open"
EDITOR_GRID_PROPERTY = "voxtart_editor_grid"
EDITOR_MENU_PROPERTY = "voxtart_editor_menu_open"
EDITOR_PALETTE_FAVORITES_PROPERTY = "voxtart_editor_palette_favorites"

EDITOR_DOCK_LEFT_PROPERTY = "voxtart_editor_dock_left"
EDITOR_DOCK_RIGHT_PROPERTY = "voxtart_editor_dock_right"
EDITOR_DOCK_PANEL_WIDTH = 252.0
EDITOR_DOCK_PANEL_GAP = 8.0
EDITOR_DOCK_PANELS = ("PALETTE", "LAYERS", "HISTORY")
_EDITOR_DOCK_HINTS = {
    "PALETTE": ("Colors", "Choose a swatch or edit its material."),
    "LAYERS": ("Layers", "Drag this panel or click a layer to activate it."),
    "HISTORY": ("History", "Drag this panel or click a snapshot to restore it."),
}


def _editor_dock_hint(name):
    return _EDITOR_DOCK_HINTS.get(name)


EDITOR_VIEW_HUD_WIDTH = 190.0
EDITOR_VIEW_HUD_HEIGHT = 154.0
EDITOR_TOOL_OPTIONS_WIDTH = 286.0
EDITOR_TOOL_OPTIONS_HEIGHT = 190.0


_EDITOR_MENU_GROUPS = (
    (
        "FILE",
        (
            ("MENU_PROJECT", "Home", "folder"),
            ("MENU_SAVE", "Save", "save"),
        ),
    ),
    (
        "EDIT",
        (
            ("MENU_UNDO", "Undo", "undo"),
            ("MENU_REDO", "Redo", "redo"),
        ),
    ),
    (
        "VIEW",
        (
            ("MENU_GRID", "Grid", "grid"),
            ("MENU_DETAILS", "Edit info", "details"),
            ("MENU_DOCK_LEFT", "Left drawer", "dock_left"),
            ("MENU_DOCK_RIGHT", "Right drawer", "dock_right"),
        ),
    ),
    (
        "TOOLS",
        (
            ("MENU_MORE_TOOLS", "Advanced tools", "toolbox"),
            ("MENU_HELP", "Help", "help"),
        ),
    ),
)
_EDITOR_VIEW_HUD_ACTIONS = (
    ("VIEW_FRONT", "Front"),
    ("VIEW_RIGHT", "Right"),
    ("VIEW_TOP", "Top"),
    ("VIEW_FRAME", "Frame"),
    ("VIEW_PROJECTION", "Projection"),
)
_EDITOR_VIEW_HUD_HINTS = {
    "VIEW_FRONT": ("Front", "Align the viewport to the front axis.", "Numpad 1"),
    "VIEW_RIGHT": ("Right", "Align the viewport to the right axis.", "Numpad 3"),
    "VIEW_TOP": ("Top", "Align the viewport to the top axis.", "Numpad 7"),
    "VIEW_FRAME": ("Frame", "Center the active asset in the viewport.", "Home"),
    "VIEW_PROJECTION": (
        "Projection",
        "Toggle between orthographic and perspective view.",
        "Numpad 5",
    ),
}


def _editor_view_hud_hint(action):
    return _EDITOR_VIEW_HUD_HINTS.get(action)




EDITOR_WORKSPACES = ("EDIT", "SCENE")
EDITOR_WORKSPACE_ITEMS = (
    ("EDIT", "Edit", "Modify voxels with brushes and selections"),
    ("SCENE", "Scene", "Move and rotate the selected asset"),
)
EDITOR_MAX_RADIUS = 16
EDITOR_COLOR_WHEEL_SIZE = 148.0
EDITOR_PALETTE_FAVORITE_SWATCH = 28.0
EDITOR_PALETTE_FAVORITE_GAP = 6.0
EDITOR_PALETTE_FAVORITE_LABEL = 42.0
EDITOR_COLOR_WHEEL_SEGMENTS = 64
EDITOR_REDRAW_INTERVAL = 1.0 / 60.0
EDITOR_AUTOSAVE_INTERVAL = 0.5


EDITOR_TOOLBAR_ITEMS = (
    ("MODE", "ADD", "+", "Place voxel", "B"),
    ("MODE", "ATTACH", "T", "Attach brush", "T"),
    ("MODE", "ERASE", "X", "Erase voxel", "E"),
    ("MODE", "PAINT", "P", "Paint voxel", "P"),
    ("MODE", "SELECT", "S", "Select voxels", "S"),
    None,
    ("SHAPE", "VOXEL", "V", "Voxel brush", "V"),
    ("SHAPE", "LINE", "/", "Line brush", "L"),
    ("SHAPE", "BOX", "[]", "Box brush", "O"),
    ("SHAPE", "SPHERE", "O", "Sphere brush", "C"),
    ("SHAPE", "FACE", "F", "Face brush", "F"),
    None,
    ("SELECT", "LASSO", "J", "Lasso select", "J"),
    ("SELECT", "CIRCLE", "K", "Circle select", "K"),
    ("SELECT", "COLOR", "M", "Color region", "M"),
    ("SELECT", "COMPONENT", "N", "Component select", "N"),
    ("SELECT", "SURFACE", "R", "Surface select", "R"),
)


BEGINNER_TOOLBAR_ITEMS = (
    ("MODE", "ADD", "+", "Add block", "B"),
    ("MODE", "ERASE", "X", "Erase block", "E"),
    ("MODE", "PAINT", "P", "Color block", "P"),
    ("MODE", "SELECT", "S", "Select blocks", "S"),
)


_THEME = ui_style.THEME

def visible_editor_toolbar_items(scene=None):
    if scene is not None and not bool(scene.get(EDITOR_ADVANCED_PROPERTY)):
        return BEGINNER_TOOLBAR_ITEMS
    return EDITOR_TOOLBAR_ITEMS


EDITOR_TOOLBAR_BUTTON_MAX = 32.0
EDITOR_TOOLBAR_BUTTON_MIN = 20.0

_ACTIVE_EDITOR = None
_CLIPBOARD: tuple[tuple[Cell, int], ...] = ()


@dataclass(slots=True)
class _HistoryState:
    asset: dict
    layers: str
    selection: tuple[Cell, ...]
    location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    label: str = "Edit"



def _scene_mode(scene) -> str:
    mode = str(getattr(scene, EDITOR_MODE_PROPERTY, "ADD"))
    return mode if mode in EDITOR_MODES else "ADD"


def _editor_input_mode(editor) -> str:
    override = getattr(editor, "drag_mode", None)
    if override in EDITOR_MODES:
        return override
    return _scene_mode(editor.scene)


def _scene_workspace(scene) -> str:
    workspace = str(getattr(scene, EDITOR_WORKSPACE_PROPERTY, "EDIT"))
    return workspace if workspace in EDITOR_WORKSPACES else "EDIT"


def _scene_shape(scene) -> str:
    shape = str(getattr(scene, EDITOR_SHAPE_PROPERTY, "VOXEL"))
    return shape if shape in EDITOR_SHAPES else "VOXEL"


def _selection(obj: bpy.types.Object) -> tuple[Cell, ...]:
    raw = obj.get(EDITOR_SELECTION_PROPERTY)
    if raw is None:
        return ()
    encoded = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(encoded, list):
        raise ValueError("saved editor selection must be a list")
    result = []
    for cell in encoded:
        if (
            not isinstance(cell, (list, tuple))
            or len(cell) != 3
            or any(isinstance(axis, bool) or not isinstance(axis, int) for axis in cell)
        ):
            raise ValueError("saved editor selection contains an invalid cell")
        result.append(tuple(cell))
    return tuple(sorted(set(result)))  # type: ignore[return-value]


def _store_selection(obj: bpy.types.Object, cells: tuple[Cell, ...]) -> None:
    normalized = tuple(sorted(set(cells)))
    if normalized:
        obj[EDITOR_SELECTION_PROPERTY] = json.dumps(
            [list(cell) for cell in normalized],
            separators=(",", ":"),
        )
    elif EDITOR_SELECTION_PROPERTY in obj:
        del obj[EDITOR_SELECTION_PROPERTY]



def _decode_clipboard(raw, palette_size: int) -> tuple[tuple[Cell, int], ...]:
    encoded = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(encoded, list):
        raise ValueError("clipboard must contain a list")
    decoded = []
    for item in encoded:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            raise ValueError("clipboard entries must contain x, y, z, and palette index")
        values = tuple(item)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("clipboard entries must contain integers")
        cell = values[:3]
        palette_index = values[3]
        if not 0 <= palette_index < palette_size:
            raise ValueError(f"clipboard palette index out of range: {palette_index}")
        decoded.append((cell, palette_index))
    return tuple(decoded)  # type: ignore[return-value]


def _encode_clipboard(clipboard: tuple[tuple[Cell, int], ...]) -> str:
    return json.dumps(
        [[*cell, palette_index] for cell, palette_index in clipboard],
        separators=(",", ":"),
    )

def _active_asset(context):
    obj = getattr(context, "active_object", None)
    return obj if obj is not None and ASSET_PROPERTY in obj else None


def _layer_asset(asset: VoxelAsset, stack) -> VoxelAsset:
    return VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(asset.palette),
        voxels=dict(stack.active.voxels),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
        palette_materials=asset.get_palette_materials(),
    )


def _copy_history(
    asset: VoxelAsset,
    stack,
    selection: tuple[Cell, ...],
    obj=None,
    *,
    label: str = "Edit",
) -> _HistoryState:
    normalized_label = str(label or "Edit")
    if obj is None:
        return _HistoryState(
            asset.to_dict(),
            stack.encode(),
            tuple(selection),
            label=normalized_label,
        )
    return _HistoryState(
        asset.to_dict(),
        stack.encode(),
        tuple(selection),
        tuple(float(value) for value in obj.location),
        tuple(float(value) for value in obj.rotation_euler),
        tuple(float(value) for value in obj.scale),
        normalized_label,
    )


def _history_label(report) -> str:
    changed = int(getattr(report, "added", 0)) + int(getattr(report, "removed", 0)) + int(getattr(report, "painted", 0))
    unit = "block" if changed == 1 else "blocks"
    verb = {
        "editor_add": "Added",
        "editor_attach": "Added",
        "editor_erase": "Erased",
        "editor_paint": "Colored",
    }.get(str(getattr(report, "operation", "")), "Updated")
    return f"{verb} {changed} {unit}"


def _restore_overlay(obj, asset: VoxelAsset) -> None:
    try:
        operators._update_visual_overlays(obj, asset)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return
    active_editor = _ACTIVE_EDITOR
    if active_editor is not None and active_editor.obj is obj:
        active_editor._set_seam_overlay_visibility(True)


def _draw_editor_text(text: str, x: float, y: float, size: float, color) -> None:
    ui_style.draw_text(text, x, y, size, color)


def _editor_text_width(text: str, size: float) -> float:
    return ui_style.text_width(text, size)


def _request_redraw(editor, context, *, force: bool = False) -> None:
    redraw = getattr(editor, "_tag_redraw", None)
    if callable(redraw):
        redraw(context, force=force)
        return
    area = getattr(context, "area", None)
    if area is not None:
        area.tag_redraw()


def _editor_cursor(editor, event=None) -> str:
    if (
        getattr(editor, "toolbar_hover", None) is not None
        or getattr(editor, "topbar_hover", None) is not None
        or getattr(editor, "palette_hover", None) is not None
        or getattr(editor, "color_wheel_hover", None) is not None
        or getattr(editor, "dock_hover", None) is not None
        or getattr(editor, "dock_dragging", None) is not None
        or getattr(editor, "view_hud_hover", None) is not None
        or getattr(editor, "tool_options_hover", None) is not None
    ):
        return "HAND"
    if getattr(event, "alt", False):
        return "EYEDROPPER"
    return {
        "ADD": "PAINT_BRUSH",
        "ATTACH": "PAINT_BRUSH",
        "ERASE": "ERASER",
        "PAINT": "PAINT_BRUSH",
        "SELECT": "CROSSHAIR",
    }.get(_editor_input_mode(editor), "CROSSHAIR")

def _editor_view_hud_layout(editor, region):
    scale = ui_style.ui_scale()
    width = float(getattr(region, "width", 0.0))
    height = float(getattr(region, "height", 0.0))
    hud_width = min(
        EDITOR_VIEW_HUD_WIDTH * scale,
        max(150.0 * scale, width - 16.0 * scale),
    )
    hud_height = min(
        EDITOR_VIEW_HUD_HEIGHT * scale,
        max(128.0 * scale, height - 96.0 * scale),
    )
    right = max(8.0 * scale, width - 12.0 * scale)
    dock_layout = _editor_dock_layout(editor, region)
    right_panels = tuple(
        panel["bounds"][0]
        for panel in dock_layout["panels"].values()
        if panel.get("side") == "RIGHT"
    )
    if right_panels:
        right = min(right, min(right_panels) - 10.0 * scale)
    x0 = max(8.0 * scale, right - hud_width)
    x1 = min(width - 8.0 * scale, x0 + hud_width)
    y1 = max(72.0 * scale, height - 60.0 * scale)
    y0 = max(44.0 * scale, y1 - hud_height)
    cube = (
        x0 + 12.0 * scale,
        y0 + 43.0 * scale,
        x0 + 76.0 * scale,
        y0 + 107.0 * scale,
    )
    button_x0 = x0 + 84.0 * scale
    button_x1 = x1 - 10.0 * scale
    button_height = 20.0 * scale
    button_gap = 5.0 * scale
    entries = [
        (
            action,
            label,
            (
                button_x0,
                y0 + offset * scale,
                button_x1,
                y0 + offset * scale + button_height,
            ),
        )
        for action, label, offset in (
            ("VIEW_FRONT", "Front", 88.0),
            ("VIEW_RIGHT", "Right", 63.0),
            ("VIEW_TOP", "Top", 38.0),
        )
    ]
    bottom_x0 = x0 + 12.0 * scale
    bottom_x1 = x1 - 10.0 * scale
    bottom_width = (bottom_x1 - bottom_x0 - button_gap) * 0.5
    entries.extend(
        (
            (
                "VIEW_FRAME",
                "Frame",
                (
                    bottom_x0,
                    y0 + 10.0 * scale,
                    bottom_x0 + bottom_width,
                    y0 + 10.0 * scale + button_height,
                ),
            ),
            (
                "VIEW_PROJECTION",
                "Projection",
                (
                    bottom_x0 + bottom_width + button_gap,
                    y0 + 10.0 * scale,
                    bottom_x1,
                    y0 + 10.0 * scale + button_height,
                ),
            ),
        )
    )
    return {
        "panel": (x0, y0, x1, y1),
        "cube": cube,
        "entries": tuple(entries),
    }


def _editor_view_hud_action_at(context, event):
    region = getattr(context, "region", None)
    editor = _ACTIVE_EDITOR
    if region is None or editor is None:
        return None
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    for action, _label, bounds in _editor_view_hud_layout(editor, region)["entries"]:
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return action
    return None


def _set_editor_cursor(context, cursor: str) -> None:
    window = getattr(context, "window", None)
    if window is None:
        return
    try:
        window.cursor_set(cursor)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return


def _editor_toolbar_layout(height: float, scene=None):
    items = visible_editor_toolbar_items(scene)
    item_count = sum(item is not None for item in items)
    separator_count = sum(item is None for item in items)
    scale = ui_style.ui_scale()
    button_max = EDITOR_TOOLBAR_BUTTON_MAX * scale
    button_min = EDITOR_TOOLBAR_BUTTON_MIN * scale
    available = max(0.0, float(height) - 132.0 * scale)
    gap_base = 5.0 * scale
    separator_base = 9.0 * scale
    fit_height = (
        available - (item_count - 1) * gap_base - separator_count * separator_base
    ) / max(item_count, 1)
    button_height = min(
        button_max,
        max(12.0 * scale, fit_height),
    )
    if fit_height >= button_min:
        button_height = max(button_height, button_min)
    beginner = items is BEGINNER_TOOLBAR_ITEMS
    button_width = 126.0 * scale if beginner else button_height
    gap = max(3.0 * scale, min(6.0 * scale, button_height * 0.12))
    separator = max(6.0 * scale, button_height * 0.2)
    x = 8.0 * scale
    top = max(16.0 * scale, float(height) - 132.0 * scale)
    cursor = top
    entries = []
    for item in items:
        if item is None:
            cursor -= separator
            continue
        y = cursor - button_height
        entries.append((item, (x + 8.0 * scale, y, x + 8.0 * scale + button_width, y + button_height)))
        cursor = y - gap
    bottom = cursor + gap
    panel = (x, bottom - 8.0 * scale, button_width + 16.0 * scale, top - bottom + 16.0 * scale)
    return panel, tuple(entries)


def _editor_toolbar_item_at(editor, context, event):
    if _scene_workspace(editor.scene) != "EDIT":
        return None
    region = getattr(context, "region", None)
    if region is None:
        return None
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    for item, bounds in _editor_toolbar_layout(region.height, editor.scene)[1]:
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return item
    return None

def _editor_tool_options_layout(editor, region):
    scale = ui_style.ui_scale()
    width = float(getattr(region, "width", 0.0))
    height = float(getattr(region, "height", 0.0))
    workspace = _scene_workspace(editor.scene)
    mode = _editor_input_mode(editor)
    shape = _scene_shape(editor.scene)
    mode_item = next(
        (item for item in EDITOR_MODE_ITEMS if item[0] == mode),
        (mode, mode.title(), "Choose an editing mode"),
    )
    shape_item = next(
        (item for item in EDITOR_SHAPE_ITEMS if item[0] == shape),
        (shape, shape.title(), "Choose a brush shape"),
    )
    if workspace not in {"EDIT", "SCENE"}:
        return {
            "panel": (0.0, 0.0, 0.0, 0.0),
            "entries": (),
            "mode_item": mode_item,
            "shape_item": shape_item,
            "workspace": workspace,
        }
    toolbar_panel, _toolbar_entries = (
        _editor_toolbar_layout(height, editor.scene)
        if workspace == "EDIT"
        else ((0.0, 0.0, 0.0, 0.0), ())
    )
    dock_layout = _editor_dock_layout(editor, region)
    left_panel_right = max(
        (
            panel["bounds"][2]
            for panel in dock_layout["panels"].values()
            if panel.get("side") == "LEFT"
        ),
        default=0.0,
    )
    right_panel_left = min(
        (
            panel["bounds"][0]
            for panel in dock_layout["panels"].values()
            if panel.get("side") == "RIGHT"
        ),
        default=width,
    )
    x0 = max(
        8.0 * scale,
        toolbar_panel[2] + 12.0 * scale,
        left_panel_right + 12.0 * scale,
    )
    right_limit = min(width - 8.0 * scale, right_panel_left - 12.0 * scale)
    available_width = max(0.0, right_limit - x0)
    panel_width = min(EDITOR_TOOL_OPTIONS_WIDTH * scale, available_width)
    if panel_width < 210.0 * scale:
        panel_width = max(0.0, available_width)
        x0 = max(8.0 * scale, right_limit - panel_width)
    top = max(72.0 * scale, height - 60.0 * scale)
    panel_height = min(
        EDITOR_TOOL_OPTIONS_HEIGHT * scale,
        max(168.0 * scale, height - 96.0 * scale),
        max(0.0, top - 44.0 * scale),
    )
    y0 = max(44.0 * scale, top - panel_height)
    x1 = min(right_limit, x0 + panel_width)
    panel = (x0, y0, x1, top)
    entries = []

    def add(action, label, left, bottom, right):
        entries.append(
            (
                action,
                label,
                (left * scale, bottom * scale, right * scale, (bottom + 22.0) * scale),
            )
        )

    panel_width_units = max(210.0, (x1 - x0) / max(scale, 0.001))
    panel_x_units = x0 / max(scale, 0.001)
    panel_y_units = y0 / max(scale, 0.001)
    if workspace == "SCENE":
        add(
            "SCENE_MOVE",
            "Move",
            panel_x_units + 12.0,
            panel_y_units + 84.0,
            panel_x_units + panel_width_units - 12.0,
        )
        add(
            "SCENE_ROTATE",
            "Rotate",
            panel_x_units + 12.0,
            panel_y_units + 52.0,
            panel_x_units + panel_width_units - 12.0,
        )
        if getattr(editor, "scene_transform_active", False):
            inner_left = panel_x_units + 12.0
            inner_right = panel_x_units + panel_width_units - 12.0
            segment_width = max(0.0, (inner_right - inner_left - 12.0) / 3.0)
            add(
                "SCENE_SCALE",
                "Scale",
                inner_left,
                panel_y_units + 20.0,
                inner_left + segment_width,
            )
            add(
                "SCENE_APPLY",
                "Apply",
                inner_left + segment_width + 6.0,
                panel_y_units + 20.0,
                inner_left + (segment_width * 2.0) + 6.0,
            )
            add(
                "SCENE_CANCEL",
                "Cancel",
                inner_left + (segment_width * 2.0) + 12.0,
                panel_y_units + 20.0,
                inner_right,
            )
        else:
            add(
                "SCENE_SCALE",
                "Scale",
                panel_x_units + 12.0,
                panel_y_units + 20.0,
                panel_x_units + panel_width_units - 12.0,
            )
    elif mode == "SELECT":
        add(
            "TOOL_SELECT_ALL",
            "All",
            panel_x_units + 12.0,
            panel_y_units + 84.0,
            panel_x_units + 120.0,
        )
        add(
            "TOOL_CLEAR_SELECTION",
            "Clear",
            panel_x_units + 126.0,
            panel_y_units + 84.0,
            panel_x_units + panel_width_units - 12.0,
        )
        add(
            "TOOL_COPY_SELECTION",
            "Copy",
            panel_x_units + 12.0,
            panel_y_units + 52.0,
            panel_x_units + 98.0,
        )
        add(
            "TOOL_CUT_SELECTION",
            "Cut",
            panel_x_units + 104.0,
            panel_y_units + 52.0,
            panel_x_units + panel_width_units - 12.0,
        )
    else:
        add("TOOL_RADIUS_DOWN", "−", panel_x_units + 54.0, panel_y_units + 84.0, panel_x_units + 78.0)
        add("TOOL_RADIUS_UP", "+", panel_x_units + 116.0, panel_y_units + 84.0, panel_x_units + 140.0)
        add(
            "TOOL_HOLLOW",
            "Hollow",
            panel_x_units + 152.0,
            panel_y_units + 84.0,
            panel_x_units + panel_width_units - 12.0,
        )
        add("TOOL_MIRROR_X", "X", panel_x_units + 66.0, panel_y_units + 52.0, panel_x_units + 104.0)
        add("TOOL_MIRROR_Y", "Y", panel_x_units + 108.0, panel_y_units + 52.0, panel_x_units + 146.0)
        add("TOOL_MIRROR_Z", "Z", panel_x_units + 150.0, panel_y_units + 52.0, panel_x_units + 188.0)
        if shape == "CYLINDER":
            add("TOOL_AXIS_X", "X", panel_x_units + 66.0, panel_y_units + 20.0, panel_x_units + 100.0)
            add("TOOL_AXIS_Y", "Y", panel_x_units + 104.0, panel_y_units + 20.0, panel_x_units + 138.0)
            add("TOOL_AXIS_Z", "Z", panel_x_units + 142.0, panel_y_units + 20.0, panel_x_units + 176.0)
            add("TOOL_HEIGHT_DOWN", "−", panel_x_units + 218.0, panel_y_units + 20.0, panel_x_units + 240.0)
            add("TOOL_HEIGHT_UP", "+", panel_x_units + 246.0, panel_y_units + 20.0, panel_x_units + 268.0)
        elif shape == "FACE" or mode == "ATTACH":
            add("TOOL_DEPTH_DOWN", "−", panel_x_units + 76.0, panel_y_units + 20.0, panel_x_units + 100.0)
            add("TOOL_DEPTH_UP", "+", panel_x_units + 106.0, panel_y_units + 20.0, panel_x_units + 130.0)
    return {
        "panel": panel,
        "entries": tuple(entries),
        "mode_item": mode_item,
        "shape_item": shape_item,
        "workspace": workspace,
    }


def _editor_tool_options_action_at(context, event):
    region = getattr(context, "region", None)
    editor = _ACTIVE_EDITOR
    if region is None or editor is None:
        return None
    layout = _editor_tool_options_layout(editor, region)
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    for action, _label, bounds in layout["entries"]:
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return action
    return None


def _editor_topbar_action_bounds(region):
    width = float(getattr(region, "width", 0.0))
    scale = ui_style.ui_scale()
    actions = (
        ("UNDO", 78.0),
        ("REDO", 78.0),
        ("PROJECT", 88.0),
        ("SAVE", 68.0),
        ("HELP", 80.0),
        ("OPTIONS", 90.0),
        ("MENU", 68.0),
        ("DOCK_LEFT", 48.0),
        ("DOCK_RIGHT", 48.0),
    )
    base_total_width = sum(item[1] for item in actions) + 6.0 * (len(actions) - 1)
    scale = min(scale, max(0.5, (width - 24.0) / base_total_width))
    right = width - 12.0 * scale
    gap = 6.0 * scale
    total_width = sum(item[1] * scale for item in actions) + gap * (len(actions) - 1)
    cursor = max(8.0 * scale, right - total_width)
    top = float(getattr(region, "height", 0.0)) - 42.0 * scale
    bounds = []
    for action, action_width in actions:
        scaled_width = action_width * scale
        bounds.append((action, (cursor, top, cursor + scaled_width, top + 30.0 * scale)))
        cursor += scaled_width + gap
    return tuple(bounds)
def _editor_options_layout(region):
    scale = ui_style.ui_scale()
    width = float(getattr(region, "width", 0.0))
    options_bounds = next(
        (bounds for action, bounds in _editor_topbar_action_bounds(region) if action == "OPTIONS"),
        None,
    )
    if options_bounds is None or width <= 16.0 * scale:
        return (0.0, 0.0, 0.0, 0.0), ()
    margin = 8.0 * scale
    menu_width = min(280.0 * scale, max(220.0 * scale, width - 2.0 * margin))
    menu_height = 188.0 * scale
    menu_x = min(
        max(margin, options_bounds[2] - menu_width),
        max(margin, width - menu_width - margin),
    )
    menu_y = max(margin, options_bounds[1] - menu_height - 10.0 * scale)
    row_x = menu_x + 8.0 * scale
    row_width = max(0.0, menu_width - 16.0 * scale)
    row_height = 42.0 * scale
    row_gap = 4.0 * scale
    grid_y = menu_y + 8.0 * scale
    details_y = grid_y + row_height + row_gap
    more_y = details_y + row_height + row_gap
    return (
        (menu_x, menu_y, menu_x + menu_width, menu_y + menu_height),
        (
            (
                "OPTIONS_GRID",
                (row_x, grid_y, row_x + row_width, grid_y + row_height),
            ),
            (
                "OPTIONS_DETAILS",
                (row_x, details_y, row_x + row_width, details_y + row_height),
            ),
            (
                "OPTIONS_MORE_TOOLS",
                (row_x, more_y, row_x + row_width, more_y + row_height),
            ),
        ),
    )


def _editor_options_action_at(context, event):
    region = getattr(context, "region", None)
    scene = getattr(context, "scene", None)
    if region is None or scene is None or not bool(scene.get(EDITOR_OPTIONS_PROPERTY)):
        return None
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    for action, bounds in _editor_options_layout(region)[1]:
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return action
    return None


def _editor_menu_layout(region):
    scale = ui_style.ui_scale()
    width = float(getattr(region, "width", 0.0))
    menu_bounds = next(
        (bounds for action, bounds in _editor_topbar_action_bounds(region) if action == "MENU"),
        None,
    )
    if menu_bounds is None or width <= 16.0 * scale:
        return (0.0, 0.0, 0.0, 0.0), (), ()
    margin = 8.0 * scale
    menu_width = min(272.0 * scale, max(236.0 * scale, width - 2.0 * margin))
    row_height = 34.0 * scale
    heading_height = 18.0 * scale
    group_gap = 7.0 * scale
    padding = 10.0 * scale
    content_height = sum(
        heading_height + len(items) * row_height + group_gap
        for _label, items in _EDITOR_MENU_GROUPS
    )
    menu_height = padding * 2.0 + content_height
    menu_top = menu_bounds[1] - 8.0 * scale
    menu_y = max(margin, menu_top - menu_height)
    menu_x = min(
        max(margin, menu_bounds[2] - menu_width),
        max(margin, width - menu_width - margin),
    )
    cursor = menu_top - padding
    entries = []
    headings = []
    for group_label, items in _EDITOR_MENU_GROUPS:
        heading_top = cursor
        heading_bottom = heading_top - heading_height
        headings.append((group_label, (menu_x + padding, heading_bottom, menu_x + menu_width - padding, heading_top)))
        cursor = heading_bottom
        for action, label, icon in items:
            row_top = cursor - 2.0 * scale
            row_bottom = row_top - row_height
            entries.append(
                (
                    action,
                    label,
                    icon,
                    (
                        menu_x + padding,
                        row_bottom,
                        menu_x + menu_width - padding,
                        row_top,
                    ),
                )
            )
            cursor = row_bottom
        cursor -= group_gap
    return (
        (menu_x, menu_y, menu_x + menu_width, menu_top),
        tuple(entries),
        tuple(headings),
    )


def _editor_menu_action_at(context, event):
    region = getattr(context, "region", None)
    scene = getattr(context, "scene", None)
    if region is None or scene is None or not bool(scene.get(EDITOR_MENU_PROPERTY)):
        return None
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    for action, _label, _icon, bounds in _editor_menu_layout(region)[1]:
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return action
    return None


def _editor_topbar_action_at(context, event):
    region = getattr(context, "region", None)
    if region is None:
        return None
    menu_action = _editor_menu_action_at(context, event)
    if menu_action is not None:
        return menu_action
    option_action = _editor_options_action_at(context, event)
    if option_action is not None:
        return option_action
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    for action, bounds in _editor_topbar_action_bounds(region):
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return action
    return None

def _editor_chat_action_at(context, event) -> str | None:
    region = getattr(context, "region", None)
    if region is None:
        return None
    from . import mcp

    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    for action, bounds in mcp.chat_layout(
        float(region.width),
        float(region.height),
        scale=ui_style.ui_scale(),
    ).items():
        if action == "panel":
            continue
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return action
    return None





def _editor_scene_bool(editor, property_name: str, default: bool) -> bool:
    scene = getattr(editor, "scene", None)
    getter = getattr(scene, "get", None)
    if callable(getter):
        return bool(getter(property_name, default))
    return bool(default)


def _editor_dock_open(editor, side: str) -> bool:
    property_name = (
        EDITOR_DOCK_LEFT_PROPERTY
        if side == "LEFT"
        else EDITOR_DOCK_RIGHT_PROPERTY
    )
    return _editor_scene_bool(editor, property_name, side == "RIGHT")


def _editor_palette_color_key(color) -> str:
    return json.dumps(
        [round(float(channel), 6) for channel in tuple(color)[:4]],
        separators=(",", ":"),
    )


def _editor_palette_favorite_keys(editor) -> set[str]:
    scene = getattr(editor, "scene", None)
    getter = getattr(scene, "get", None)
    raw = getter(EDITOR_PALETTE_FAVORITES_PROPERTY, "[]") if callable(getter) else "[]"
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return set()
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(value) for value in raw if isinstance(value, str)}


def _set_editor_palette_favorite_keys(editor, keys) -> None:
    scene = getattr(editor, "scene", None)
    if scene is None:
        return
    scene[EDITOR_PALETTE_FAVORITES_PROPERTY] = json.dumps(
        sorted({str(key) for key in keys}),
        separators=(",", ":"),
    )


def _editor_palette_favorite_indices(editor) -> tuple[int, ...]:
    palette = tuple(getattr(getattr(editor, "asset", None), "palette", ()) or ())
    keys = _editor_palette_favorite_keys(editor)
    return tuple(
        index
        for index, color in enumerate(palette)
        if _editor_palette_color_key(color) in keys
    )


def _editor_palette_favorite_band_height(editor, panel_width: float, scale: float) -> float:
    favorite_size = EDITOR_PALETTE_FAVORITE_SWATCH * scale
    favorite_gap = EDITOR_PALETTE_FAVORITE_GAP * scale
    available = max(
        1.0 * scale,
        panel_width
        - 24.0 * scale
        - EDITOR_PALETTE_FAVORITE_LABEL * scale,
    )
    columns = max(
        1,
        int((available + favorite_gap) // (favorite_size + favorite_gap)),
    )
    rows = (
        len(_editor_palette_favorite_indices(editor)) + columns - 1
    ) // columns
    rows = max(1, rows)
    return (
        12.0 * scale
        + rows * favorite_size
        + max(0, rows - 1) * favorite_gap
        + 8.0 * scale
    )


def _editor_dock_layout(editor, region):
    scale = ui_style.ui_scale()
    width = float(getattr(region, "width", 0.0))
    height = float(getattr(region, "height", 0.0))
    dock_width = min(
        EDITOR_DOCK_PANEL_WIDTH * scale,
        max(210.0 * scale, width * 0.28),
    )
    top = max(64.0 * scale, height - 58.0 * scale)
    bottom = 42.0 * scale
    gap = EDITOR_DOCK_PANEL_GAP * scale
    palette = tuple(getattr(getattr(editor, "asset", None), "palette", ()) or ())
    swatch_size = 36.0 * scale
    swatch_gap = 8.0 * scale
    columns = max(
        1,
        min(
            len(palette) or 1,
            int((dock_width - 24.0 * scale + swatch_gap) // (swatch_size + swatch_gap)),
        ),
    )
    rows = (len(palette) + columns - 1) // columns if palette else 1
    wheel_size = min(
        EDITOR_COLOR_WHEEL_SIZE * scale,
        max(64.0 * scale, dock_width - 24.0 * scale),
    )
    desired = {
        "PALETTE": 58.0 * scale
        + wheel_size
        + 14.0 * scale
        + _editor_palette_favorite_band_height(editor, dock_width, scale)
        + rows * swatch_size
        + max(0, rows - 1) * swatch_gap
        + 12.0 * scale,
        "LAYERS": 212.0 * scale,
        "HISTORY": 178.0 * scale,
    }
    sides = dict(getattr(editor, "dock_panel_sides", {}) or {})
    sides.setdefault("PALETTE", "RIGHT")
    sides.setdefault("LAYERS", "LEFT")
    sides.setdefault("HISTORY", "LEFT")
    order = list(getattr(editor, "dock_panel_order", ()) or ())
    order.extend(name for name in EDITOR_DOCK_PANELS if name not in order)
    panels = {}
    drawers = {}
    for side in ("LEFT", "RIGHT"):
        side_names = [
            name
            for name in order
            if sides.get(name) == side and _editor_dock_open(editor, side)
        ]
        if not side_names:
            continue
        available = max(96.0 * scale, top - bottom)
        total = sum(desired[name] for name in side_names)
        panel_scale = min(1.0, max(0.24, (available - gap * (len(side_names) - 1)) / total))
        if side == "LEFT":
            x0 = 8.0 * scale
            if _scene_workspace(editor.scene) == "EDIT":
                toolbar_panel, _toolbar_entries = _editor_toolbar_layout(height, editor.scene)
                x0 = max(x0, toolbar_panel[2] + 12.0 * scale)
        else:
            x0 = width - dock_width - 8.0 * scale
        cursor = top
        drawer_bottom = top
        for name in side_names:
            panel_height = desired[name] * panel_scale
            panel_top = cursor
            panel_bottom = max(bottom, panel_top - panel_height)
            panels[name] = {
                "side": side,
                "bounds": (x0, panel_bottom, x0 + dock_width, panel_top),
            }
            cursor = panel_bottom - gap
            drawer_bottom = panel_bottom
        drawers[side] = (
            x0 - 4.0 * scale,
            max(bottom - 4.0 * scale, drawer_bottom - 4.0 * scale),
            x0 + dock_width + 4.0 * scale,
            top + 4.0 * scale,
        )
    return {
        "width": dock_width,
        "panels": panels,
        "drawers": drawers,
        "sides": sides,
        "order": tuple(order),
    }


def _editor_palette_layout(editor, region):
    palette = tuple(getattr(getattr(editor, "asset", None), "palette", ()) or ())
    if not palette:
        return (0.0, 0.0, 0.0, 0.0), ()
    dock_panel = _editor_dock_layout(editor, region)["panels"].get("PALETTE")
    if dock_panel is None:
        return (0.0, 0.0, 0.0, 0.0), ()
    panel = dock_panel["bounds"]
    scale = ui_style.ui_scale()
    panel_width = panel[2] - panel[0]
    swatch_size = 36.0 * scale
    gap = 8.0 * scale
    columns = max(
        1,
        min(
            len(palette),
            int((panel_width - 24.0 * scale + gap) // (swatch_size + gap)),
        ),
    )
    rows = (len(palette) + columns - 1) // columns
    entries = []
    swatch_bottom = panel[1] + _editor_palette_favorite_band_height(
        editor,
        panel_width,
        scale,
    )

    for index in range(len(palette)):
        row, column = divmod(index, columns)
        x = panel[0] + 12.0 * scale + column * (swatch_size + gap)
        y = swatch_bottom + (rows - row - 1) * (swatch_size + gap)
        entries.append((index, (x, y, x + swatch_size, y + swatch_size)))
    return panel, tuple(entries)
def _editor_palette_favorite_layout(editor, region):
    panel, regular_entries = _editor_palette_layout(editor, region)
    if not regular_entries:
        return panel, ()
    favorite_indices = _editor_palette_favorite_indices(editor)
    if not favorite_indices:
        return panel, ()
    scale = ui_style.ui_scale()
    panel_width = panel[2] - panel[0]
    swatch_size = EDITOR_PALETTE_FAVORITE_SWATCH * scale
    gap = EDITOR_PALETTE_FAVORITE_GAP * scale
    label_width = EDITOR_PALETTE_FAVORITE_LABEL * scale
    available = max(
        1.0 * scale,
        panel_width - 24.0 * scale - label_width,
    )
    columns = max(
        1,
        int((available + gap) // (swatch_size + gap)),
    )
    rows = (len(favorite_indices) + columns - 1) // columns
    entries = []
    for position, index in enumerate(favorite_indices):
        row, column = divmod(position, columns)
        x = panel[0] + 12.0 * scale + label_width + column * (swatch_size + gap)
        y = panel[1] + 12.0 * scale + (rows - row - 1) * (swatch_size + gap)
        entries.append((index, (x, y, x + swatch_size, y + swatch_size)))
    return panel, tuple(entries)




def _editor_color_wheel_geometry(editor, region):
    panel, entries = _editor_palette_layout(editor, region)
    if not entries:
        return None
    scale = ui_style.ui_scale()
    wheel_size = min(
        EDITOR_COLOR_WHEEL_SIZE * scale,
        max(64.0 * scale, panel[2] - panel[0] - 24.0 * scale),
    )
    wheel_top = panel[3] - 58.0 * scale
    wheel_bottom = wheel_top - wheel_size
    center = ((panel[0] + panel[2]) * 0.5, (wheel_top + wheel_bottom) * 0.5)
    outer_radius = wheel_size * 0.46
    inner_radius = max(outer_radius - 14.0 * scale, outer_radius * 0.68)
    triangle_radius = inner_radius * 0.84
    triangle = tuple(
        (
            center[0] + math.cos(math.pi / 2.0 + angle) * triangle_radius,
            center[1] + math.sin(math.pi / 2.0 + angle) * triangle_radius,
        )
        for angle in (0.0, math.tau / 3.0, -math.tau / 3.0)
    )
    return {
        "center": center,
        "outer_radius": outer_radius,
        "inner_radius": inner_radius,
        "triangle": triangle,
    }


def _editor_palette_color(editor):
    palette = tuple(getattr(getattr(editor, "asset", None), "palette", ()) or ())
    if not palette:
        return (1.0, 1.0, 1.0, 1.0)
    index = int(getattr(editor.scene, "voxtart_edit_palette_index", 0)) % len(palette)
    color = tuple(float(value) for value in palette[index])
    return color if len(color) == 4 else (*color[:3], 1.0)
def _editor_palette_metadata(editor, index: int):
    palette = tuple(getattr(getattr(editor, "asset", None), "palette", ()) or ())
    if not 0 <= index < len(palette):
        return "#FFFFFF", "Diffuse", False
    display_color = scene_linear_to_srgb(palette[index])
    channels = tuple(
        max(0, min(255, int(round(float(channel) * 255.0))))
        for channel in display_color[:3]
    )
    hex_color = "#" + "".join(f"{channel:02X}" for channel in channels)
    materials = ()
    getter = getattr(getattr(editor, "asset", None), "get_palette_materials", None)
    if callable(getter):
        materials = tuple(getter() or ())
    material = materials[index] if index < len(materials) else None
    material_kind = str(getattr(material, "kind", "DIFFUSE")).title()
    favorite = _editor_palette_color_key(palette[index]) in _editor_palette_favorite_keys(editor)
    return hex_color, material_kind, favorite


def _editor_palette_tooltip_content(editor, index: int):
    hex_color, material_kind, favorite = _editor_palette_metadata(editor, index)
    return (
        f"Color {index + 1}",
        f"{hex_color} · {material_kind}",
        "Pinned · right-click to unpin" if favorite else "Alt/Right-click to pin",
        favorite,
    )






def _editor_color_wheel_hsv(editor):
    color = scene_linear_to_srgb(_editor_palette_color(editor))
    hue, saturation, value = colorsys.rgb_to_hsv(*color[:3])
    return hue, saturation, value, color[3]


def _triangle_barycentric(point, triangle):
    (ax, ay), (bx, by), (cx, cy) = triangle
    px, py = point
    denominator = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(denominator) <= 1.0e-9:
        return None
    first = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / denominator
    second = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / denominator
    return first, second, 1.0 - first - second


def _editor_color_wheel_hit(editor, context, event):
    region = getattr(context, "region", None)
    geometry = _editor_color_wheel_geometry(editor, region) if region is not None else None
    if geometry is None:
        return None
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    center_x, center_y = geometry["center"]
    distance = math.hypot(x - center_x, y - center_y)
    if geometry["inner_radius"] <= distance <= geometry["outer_radius"]:
        angle = math.atan2(y - center_y, x - center_x)
        hue = ((math.pi / 2.0 - angle) / math.tau) % 1.0
        return ("HUE", hue)
    barycentric = _triangle_barycentric((x, y), geometry["triangle"])
    if barycentric is None or min(barycentric) < -0.01:
        return None
    hue, _saturation, _value, _alpha = _editor_color_wheel_hsv(editor)
    first, second, _third = barycentric
    value = max(0.0, min(1.0, first + second))
    saturation = first / max(value, 1.0e-9)
    return ("SV", hue, max(0.0, min(1.0, saturation)), value)


_EDITOR_COLOR_WHEEL_HINTS = {
    "HUE": ("Hue", "Drag the ring to change the active color hue."),
    "SV": (
        "Saturation / value",
        "Drag the triangle to adjust color intensity and brightness.",
    ),
}


def _editor_color_wheel_hint(hit):
    action = hit[0] if isinstance(hit, tuple) and hit else None
    return _EDITOR_COLOR_WHEEL_HINTS.get(action)


def _set_editor_color_from_wheel(editor, context, hit) -> None:
    if hit is None:
        return
    hue, saturation, value, alpha = _editor_color_wheel_hsv(editor)
    if hit[0] == "HUE":
        hue = hit[1]
    else:
        hue, saturation, value = hit[1:]
    display_rgb = colorsys.hsv_to_rgb(hue, saturation, value)
    color = srgb_to_scene_linear((*display_rgb, alpha))
    index = int(getattr(editor.scene, "voxtart_edit_palette_index", 0))
    if not 0 <= index < len(editor.asset.palette):
        return
    old_key = _editor_palette_color_key(editor.asset.palette[index])
    editor.asset.palette[index] = color
    editor.layer_asset.palette[index] = color
    favorite_keys = _editor_palette_favorite_keys(editor)
    if old_key in favorite_keys:
        favorite_keys.discard(old_key)
        favorite_keys.add(_editor_palette_color_key(color))
        _set_editor_palette_favorite_keys(editor, favorite_keys)
    collection = getattr(editor.scene, "voxtart_palette_colors", None)
    if collection is not None and index < len(collection):
        collection[index].color = color
    for property_name in ("voxtart_palette_edit_color", "voxtart_palette_color"):
        if hasattr(editor.scene, property_name):
            setattr(editor.scene, property_name, color)
    editor.scene.voxtart_palette_index = index
    editor.scene.voxtart_edit_palette_index = index
    editor.status = f"Color {index + 1} updated"
    editor._sync(context, rebuild=True)


def _editor_palette_index_at(editor, context, event):
    region = getattr(context, "region", None)
    if region is None:
        return None
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    favorite_entries = _editor_palette_favorite_layout(editor, region)[1]
    regular_entries = _editor_palette_layout(editor, region)[1]
    for index, bounds in (*favorite_entries, *regular_entries):
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return index
    return None




def _draw_editor_topbar(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None:
        return
    scale = ui_style.ui_scale()
    width = float(region.width)
    height = float(region.height)
    bar_height = 48.0 * scale
    bar_y = height - bar_height
    asset_name = str(getattr(getattr(editor, "obj", None), "name", "Voxel asset"))
    if asset_name.startswith("VOXTART "):
        asset_name = asset_name[len("VOXTART "):]
    asset_summary = f"{asset_name[:30]}  ·  {len(editor.asset.voxels)} blocks"
    _draw_editor_rect(0.0, bar_y, width, bar_height, _THEME["chrome"])
    _draw_editor_icon("cube", 28.0 * scale, bar_y + 24.0 * scale, 20.0 * scale, _THEME["accent"])
    _draw_editor_text(
        asset_summary,
        54.0 * scale,
        bar_y + 28.0 * scale,
        12.0,
        _THEME["text"],
    )
    _draw_editor_text(
        "Build mode",
        54.0 * scale,
        bar_y + 12.0 * scale,
        10.0,
        _THEME["text_muted"],
    )
    help_visible = bool(context.scene.get(EDITOR_HELP_PROPERTY))
    labels = {
        "UNDO": "Undo",
        "REDO": "Redo",
        "PROJECT": "Home",
        "SAVE": "Save",
        "HELP": "Close help" if help_visible else "Help",
        "OPTIONS": "More",
        "MENU": "Menu",
        "DOCK_LEFT": "Left",
        "DOCK_RIGHT": "Right",
    }
    history = tuple(getattr(editor, "history", ()))
    history_index = int(getattr(editor, "history_index", -1))
    history_enabled = {
        "UNDO": history_index > 0,
        "REDO": 0 <= history_index < len(history) - 1,
    }
    dock_open = {
        "DOCK_LEFT": bool(getattr(context.scene, "get", lambda *_args: False)(
            EDITOR_DOCK_LEFT_PROPERTY, False
        )),
        "DOCK_RIGHT": bool(getattr(context.scene, "get", lambda *_args: True)(
            EDITOR_DOCK_RIGHT_PROPERTY, True
        )),
    }
    try:
        if gpu is not None:
            gpu.state.blend_set("ALPHA")
        workspace = _scene_workspace(context.scene)
        workspace_label = {"EDIT": "MAKE", "SCENE": "PLACE"}[workspace]
        workspace_accent = (
            _THEME["accent"]
            if workspace == "EDIT"
            else _THEME["warning"]
        )
        workspace_pill_width = 96.0 * scale
        workspace_x = 54.0 * scale + _editor_text_width(asset_summary, 12.0) + 16.0 * scale
        project_x = next(
            bounds[0]
            for action, bounds in _editor_topbar_action_bounds(region)
            if action == "PROJECT"
        )
        if workspace_x + workspace_pill_width + 12.0 * scale < project_x:
            _draw_editor_panel(
                workspace_x,
                bar_y + 9.0 * scale,
                workspace_pill_width,
                28.0 * scale,
                _THEME["accent_soft"]
                if workspace == "EDIT"
                else _THEME["warning_fill"],
                border=workspace_accent,
                radius=14.0 * scale,
            )
            _draw_editor_round_rect(
                workspace_x + 11.0 * scale,
                bar_y + 20.0 * scale,
                6.0 * scale,
                6.0 * scale,
                workspace_accent,
                radius=3.0 * scale,
            )
            _draw_editor_text(
                workspace_label,
                workspace_x + 24.0 * scale,
                bar_y + 18.0 * scale,
                10.0,
                _THEME["text"],
            )
            _draw_editor_text(
                "TAB",
                workspace_x + 65.0 * scale,
                bar_y + 18.0 * scale,
                9.0,
                _THEME["text_muted"],
            )
        for action, bounds in _editor_topbar_action_bounds(region):
            x0, y0, x1, y1 = bounds
            enabled = history_enabled.get(action, True)
            active = dock_open.get(action, False)
            hovered = enabled and action == getattr(editor, "topbar_hover", None)
            button_color = (
                _THEME["panel_hover"]
                if hovered
                else _THEME["accent_soft"]
                if active
                else _THEME["panel"]
                if enabled
                else _THEME["canvas"]
            )
            border_color = (
                _THEME["accent"]
                if hovered or active
                else _THEME["border_subtle"]
            )
            text_color = (
                _THEME["text"]
                if enabled
                else _THEME["text_dim"]
            )
            _draw_editor_panel(
                x0,
                y0,
                x1 - x0,
                y1 - y0,
                button_color,
                border=border_color,
                radius=7.0 * scale,
            )
            icon = _EDITOR_TOPBAR_ICONS.get(action)
            if icon:
                _draw_editor_icon(
                    icon,
                    x0 + 16.0 * scale,
                    y0 + (y1 - y0) * 0.5,
                    15.0 * scale,
                    text_color,
                )
            _draw_editor_text(
                labels[action],
                x0 + (32.0 if icon else 10.0) * scale,
                y0 + 10.0 * scale,
                11.0,
                text_color,
            )
    finally:
        if gpu is not None:
            gpu.state.blend_set("NONE")


def _draw_editor_options_menu(editor, context) -> None:
    region = getattr(context, "region", None)
    scene = getattr(context, "scene", None)
    if region is None or scene is None or not bool(scene.get(EDITOR_OPTIONS_PROPERTY)):
        return
    scale = ui_style.ui_scale()
    panel, entries = _editor_options_layout(region)
    if not entries:
        return
    menu_x, menu_y, menu_x1, menu_y1 = panel
    _draw_editor_panel(
        menu_x,
        menu_y,
        menu_x1 - menu_x,
        menu_y1 - menu_y,
        _THEME["panel"],
        border=_THEME["border"],
        radius=10.0 * scale,
    )
    _draw_editor_text(
        "MORE",
        menu_x + 14.0 * scale,
        menu_y1 - 24.0 * scale,
        12.0,
        _THEME["accent"],
    )
    _draw_editor_text(
        "A few extra choices.",
        menu_x + 14.0 * scale,
        menu_y1 - 42.0 * scale,
        10.0,
        _THEME["text_muted"],
    )
    grid_on = bool(scene.get(EDITOR_GRID_PROPERTY))
    details_on = bool(scene.get(EDITOR_DETAILS_PROPERTY))
    advanced_on = bool(scene.get(EDITOR_ADVANCED_PROPERTY))
    rows = {
        "OPTIONS_GRID": (
            "Grid",
            "Show the building grid",
            "ON" if grid_on else "OFF",
            "grid",
        ),
        "OPTIONS_DETAILS": (
            "Hints",
            "Show helpful details",
            "ON" if details_on else "OFF",
            "details",
        ),
        "OPTIONS_MORE_TOOLS": (
            "Advanced tools",
            "Show every VOXTART tool",
            "ON" if advanced_on else "OFF",
            "toolbox",
        ),
    }
    for action, bounds in entries:
        x0, y0, x1, y1 = bounds
        hovered = action == getattr(editor, "topbar_hover", None)
        fill = _THEME["panel_hover"] if hovered else _THEME["panel_raised"]
        border = _THEME["accent"] if hovered else _THEME["border_subtle"]
        _draw_editor_panel(
            x0,
            y0,
            x1 - x0,
            y1 - y0,
            fill,
            border=border,
            radius=7.0 * scale,
        )
        title, description, state, icon = rows[action]
        _draw_editor_icon(
            icon,
            x0 + 17.0 * scale,
            y0 + 29.0 * scale,
            15.0 * scale,
            _THEME["text"],
        )
        _draw_editor_text(
            title,
            x0 + 34.0 * scale,
            y0 + 27.0 * scale,
            11.0,
            _THEME["text"],
        )
        _draw_editor_text(
            description,
            x0 + 34.0 * scale,
            y0 + 11.0 * scale,
            9.0,
            _THEME["text_muted"],
        )
        _draw_editor_text(
            state,
            x1 - _editor_text_width(state, 9.0) - 10.0 * scale,
            y0 + 27.0 * scale,
            9.0,
            _THEME["success"] if state == "ON" else _THEME["text_dim"],
        )

def _draw_editor_menu(editor, context) -> None:
    region = getattr(context, "region", None)
    scene = getattr(context, "scene", None)
    if region is None or scene is None or not bool(scene.get(EDITOR_MENU_PROPERTY)):
        return
    scale = ui_style.ui_scale()
    panel, entries, headings = _editor_menu_layout(region)
    if not entries:
        return
    menu_x, menu_y, menu_x1, menu_y1 = panel
    _draw_editor_panel(
        menu_x,
        menu_y,
        menu_x1 - menu_x,
        menu_y1 - menu_y,
        _THEME["panel"],
        border=_THEME["border"],
        radius=10.0 * scale,
    )
    _draw_editor_text(
        "VOXTART MENU",
        menu_x + 14.0 * scale,
        menu_y1 - 22.0 * scale,
        12.0,
        _THEME["accent"],
    )
    _draw_editor_text(
        "Common editor commands",
        menu_x + 14.0 * scale,
        menu_y1 - 40.0 * scale,
        10.0,
        _THEME["text_muted"],
    )
    for label, bounds in headings:
        _draw_editor_text(
            label,
            bounds[0],
            bounds[3] - 12.0 * scale,
            9.0,
            _THEME["text_dim"],
        )
    state_labels = {
        "MENU_GRID": "ON" if bool(scene.get(EDITOR_GRID_PROPERTY)) else "OFF",
        "MENU_DETAILS": "ON" if bool(scene.get(EDITOR_DETAILS_PROPERTY)) else "OFF",
        "MENU_DOCK_LEFT": "ON" if bool(scene.get(EDITOR_DOCK_LEFT_PROPERTY)) else "OFF",
        "MENU_DOCK_RIGHT": "ON" if bool(scene.get(EDITOR_DOCK_RIGHT_PROPERTY)) else "OFF",
        "MENU_MORE_TOOLS": "ON" if bool(scene.get(EDITOR_ADVANCED_PROPERTY)) else "OFF",
    }
    for action, label, icon, bounds in entries:
        x0, y0, x1, y1 = bounds
        hovered = action == getattr(editor, "topbar_hover", None)
        _draw_editor_panel(
            x0,
            y0,
            x1 - x0,
            y1 - y0,
            _THEME["panel_hover"] if hovered else _THEME["panel_raised"],
            border=_THEME["accent"] if hovered else _THEME["border_subtle"],
            radius=7.0 * scale,
        )
        _draw_editor_icon(
            icon,
            x0 + 17.0 * scale,
            (y0 + y1) * 0.5,
            15.0 * scale,
            _THEME["text"],
        )
        _draw_editor_text(
            label,
            x0 + 34.0 * scale,
            y0 + 12.0 * scale,
            11.0,
            _THEME["text"],
        )
        state = state_labels.get(action)
        if state is not None:
            _draw_editor_text(
                state,
                x1 - _editor_text_width(state, 9.0) - 10.0 * scale,
                y0 + 12.0 * scale,
                9.0,
                _THEME["success"] if state == "ON" else _THEME["text_dim"],
            )



def _draw_editor_topbar_tooltip(editor, context) -> None:
    action = getattr(editor, "topbar_hover", None)
    region = getattr(context, "region", None)
    if action is None or region is None:
        return
    scale = ui_style.ui_scale()
    bounds = next(
        (item_bounds for item, item_bounds in _editor_topbar_action_bounds(region) if item == action),
        None,
    )
    if bounds is None:
        return
    help_visible = bool(context.scene.get(EDITOR_HELP_PROPERTY))
    title = _editor_topbar_label(action, help_visible=help_visible)
    history = tuple(getattr(editor, "history", ()))
    history_index = int(getattr(editor, "history_index", -1))
    if action == "UNDO" and history_index <= 0:
        description = "Nothing to undo yet."
    elif action == "REDO" and not (0 <= history_index < len(history) - 1):
        description = "Nothing to redo yet."
    else:
        description = _editor_topbar_hint(action)
    lines = ui_style.wrap_text(
        description,
        11.0,
        min(316.0 * scale, float(region.width) - 40.0 * scale),
    )[:2]
    if not lines:
        lines = ("Choose this control to continue.",)
    shortcut = _EDITOR_TOPBAR_SHORTCUTS.get(action, "")
    shortcut_width = _editor_text_width(shortcut, 10.0) + 16.0 * scale if shortcut else 0.0
    tooltip_width = min(
        float(region.width) - 16.0 * scale,
        max(
            240.0 * scale,
            _editor_text_width(title, 13.0) + shortcut_width + 38.0 * scale,
            max((_editor_text_width(line, 11.0) for line in lines), default=0.0) + 24.0 * scale,
        ),
    )
    tooltip_height = (54.0 + max(0, len(lines) - 1) * 14.0) * scale
    tooltip_x = min(
        max(8.0 * scale, bounds[0]),
        float(region.width) - tooltip_width - 8.0 * scale,
    )
    tooltip_y = max(8.0 * scale, bounds[1] - tooltip_height - 10.0 * scale)
    _draw_editor_panel(
        tooltip_x,
        tooltip_y,
        tooltip_width,
        tooltip_height,
        _THEME["panel"],
        border=_THEME["border"],
        radius=9.0 * scale,
    )
    title_y = tooltip_y + tooltip_height - 20.0 * scale
    _draw_editor_text(
        title,
        tooltip_x + 12.0 * scale,
        title_y,
        13.0,
        _THEME["text"],
    )
    if shortcut:
        pill_x = tooltip_x + tooltip_width - shortcut_width - 10.0 * scale
        _draw_editor_round_rect(
            pill_x,
            title_y - 3.0 * scale,
            shortcut_width,
            18.0 * scale,
            _THEME["accent_fill"],
            radius=8.0 * scale,
        )
        _draw_editor_text(
            shortcut,
            pill_x + 8.0 * scale,
            title_y + 1.0 * scale,
            10.0,
            _THEME["text"],
        )
    for index, line in enumerate(lines):
        _draw_editor_text(
            line,
            tooltip_x + 12.0 * scale,
            tooltip_y + 11.0 * scale + (len(lines) - index - 1) * 14.0 * scale,
            11.0,
            _THEME["text_muted"],
        )

def _draw_editor_color_wheel(editor, geometry) -> None:
    center_x, center_y = geometry["center"]
    outer_radius = geometry["outer_radius"]
    inner_radius = geometry["inner_radius"]
    hue, saturation, value, _alpha = _editor_color_wheel_hsv(editor)
    segments = EDITOR_COLOR_WHEEL_SEGMENTS
    for index in range(segments):
        hue_start = index / segments
        hue_end = (index + 1) / segments
        angle_start = math.pi / 2.0 - math.tau * hue_start
        angle_end = math.pi / 2.0 - math.tau * hue_end
        points = (
            (
                center_x + math.cos(angle_start) * outer_radius,
                center_y + math.sin(angle_start) * outer_radius,
            ),
            (
                center_x + math.cos(angle_end) * outer_radius,
                center_y + math.sin(angle_end) * outer_radius,
            ),
            (
                center_x + math.cos(angle_end) * inner_radius,
                center_y + math.sin(angle_end) * inner_radius,
            ),
            (
                center_x + math.cos(angle_start) * inner_radius,
                center_y + math.sin(angle_start) * inner_radius,
            ),
        )
        _draw_editor_polygon(
            points,
            (*colorsys.hsv_to_rgb(hue_start, 1.0, 1.0), 1.0),
        )

    triangle = geometry["triangle"]
    _draw_editor_smooth_triangle(
        triangle,
        (
            (*colorsys.hsv_to_rgb(hue, 1.0, 1.0), 1.0),
            (1.0, 1.0, 1.0, 1.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
    )
    hue_angle = math.pi / 2.0 - math.tau * hue
    hue_marker_radius = (outer_radius + inner_radius) * 0.5
    hue_marker = (
        center_x + math.cos(hue_angle) * hue_marker_radius,
        center_y + math.sin(hue_angle) * hue_marker_radius,
    )
    _draw_editor_circle(*hue_marker, 6.5, (0.0, 0.0, 0.0, 0.9))
    _draw_editor_circle(*hue_marker, 4.0, (1.0, 1.0, 1.0, 1.0))

    first, second, third = saturation * value, (1.0 - saturation) * value, 1.0 - value
    sv_marker = tuple(
        first * triangle[0][axis]
        + second * triangle[1][axis]
        + third * triangle[2][axis]
        for axis in range(2)
    )
    _draw_editor_circle(*sv_marker, 6.5, (0.0, 0.0, 0.0, 0.9))
    _draw_editor_circle(
        *sv_marker,
        4.0,
        (*colorsys.hsv_to_rgb(hue, saturation, value), 1.0),
    )


def _draw_editor_color_wheel_tooltip(editor, context, geometry, panel, scale) -> None:
    region = getattr(context, "region", None)
    hint = _editor_color_wheel_hint(getattr(editor, "color_wheel_hover", None))
    if region is None or geometry is None or hint is None:
        return
    title, description = hint
    lines = ui_style.wrap_text(
        description,
        11.0,
        min(316.0 * scale, float(region.width) - 40.0 * scale),
    )[:2]
    if not lines:
        lines = (description,)
    tooltip_width = min(
        float(region.width) - 16.0 * scale,
        max(
            240.0 * scale,
            _editor_text_width(title, 13.0) + 24.0 * scale,
            max((_editor_text_width(line, 11.0) for line in lines), default=0.0)
            + 24.0 * scale,
        ),
    )
    tooltip_height = (54.0 + max(0, len(lines) - 1) * 14.0) * scale
    _center_x, center_y = geometry["center"]
    tooltip_x = max(
        8.0 * scale,
        min(
            panel[0] - tooltip_width - 10.0 * scale,
            float(region.width) - tooltip_width - 8.0 * scale,
        ),
    )
    tooltip_y = max(
        8.0 * scale,
        min(
            center_y - tooltip_height * 0.5,
            float(region.height) - tooltip_height - 8.0 * scale,
        ),
    )
    _draw_editor_panel(
        tooltip_x,
        tooltip_y,
        tooltip_width,
        tooltip_height,
        _THEME["panel"],
        border=_THEME["border"],
        radius=9.0 * scale,
    )
    title_y = tooltip_y + tooltip_height - 20.0 * scale
    _draw_editor_text(
        title,
        tooltip_x + 12.0 * scale,
        title_y,
        13.0,
        _THEME["text"],
    )
    for index, line in enumerate(lines):
        _draw_editor_text(
            line,
            tooltip_x + 12.0 * scale,
            tooltip_y + 11.0 * scale + (len(lines) - index - 1) * 14.0 * scale,
            11.0,
            _THEME["text_muted"],
        )


def _draw_editor_palette(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None or gpu is None or batch_for_shader is None:
        return
    scale = ui_style.ui_scale()
    panel, entries = _editor_palette_layout(editor, region)
    if not entries:
        return
    _favorite_panel, favorite_entries = _editor_palette_favorite_layout(editor, region)
    palette = tuple(getattr(editor.asset, "palette", ()) or ())
    selected = int(getattr(context.scene, "voxtart_edit_palette_index", 0)) % len(palette)
    selected_hex, selected_kind, _selected_favorite = _editor_palette_metadata(editor, selected)
    all_entries = (*favorite_entries, *entries)
    x0, y0, x1, y1 = panel
    try:
        gpu.state.blend_set("ALPHA")
        _draw_editor_panel(
            x0,
            y0,
            x1 - x0,
            y1 - y0,
            _THEME["panel"],
            border=_THEME["border_subtle"],
            radius=10.0 * scale,
        )
        wheel = _editor_color_wheel_geometry(editor, region)
        if wheel is not None:
            _draw_editor_color_wheel(editor, wheel)
            _draw_editor_color_wheel_tooltip(editor, context, wheel, panel, scale)
        _draw_editor_text("COLORS", x0 + 12.0 * scale, y1 - 22.0 * scale, 11.0, _THEME["text_muted"])
        _draw_editor_text(
            "Alt/Right-click to pin",
            x0 + 12.0 * scale,
            y1 - 40.0 * scale,
            10.0,
            _THEME["text_dim"],
        )
        color_label = f"{selected_hex} · {selected_kind}"
        _draw_editor_text(
            color_label,
            x1 - _editor_text_width(color_label, 10.0) - 12.0 * scale,
            y1 - 22.0 * scale,
            10.0,
            _THEME["text"],
        )
        favorite_label_y = y0 + 21.0 * scale
        _draw_editor_text(
            "PINNED",
            x0 + 12.0 * scale,
            favorite_label_y,
            8.0,
            _THEME["accent"] if favorite_entries else _THEME["text_dim"],
        )
        if not favorite_entries:
            _draw_editor_text(
                "Alt/Right-click",
                x0 + 55.0 * scale,
                favorite_label_y,
                8.0,
                _THEME["text_dim"],
            )
        hovered_index = getattr(editor, "palette_hover", None)
        for index, bounds in favorite_entries:
            sx0, sy0, sx1, sy1 = bounds
            active = index == selected
            hovered = index == hovered_index
            _draw_editor_panel(
                sx0 - 2.0 * scale,
                sy0 - 2.0 * scale,
                sx1 - sx0 + 4.0 * scale,
                sy1 - sy0 + 4.0 * scale,
                _THEME["panel_hover"] if hovered else _THEME["panel_raised"],
                border=_THEME["accent"] if active or hovered else _THEME["accent_hover"],
                radius=6.0 * scale,
            )
            color = scene_linear_to_srgb(palette[index])
            _draw_editor_round_rect(
                sx0,
                sy0,
                sx1 - sx0,
                sy1 - sy0,
                color[:4],
                radius=4.0 * scale,
            )
        for index, bounds in entries:
            sx0, sy0, sx1, sy1 = bounds
            active = index == selected
            hovered = index == hovered_index
            if active or hovered:
                _draw_editor_round_rect(
                    sx0 - 2.0 * scale,
                    sy0 - 2.0 * scale,
                    sx1 - sx0 + 4.0 * scale,
                    sy1 - sy0 + 4.0 * scale,
                    _THEME["accent"] if active else _THEME["accent_hover"],
                    radius=7.0 * scale,
                )
            color = scene_linear_to_srgb(palette[index])
            _draw_editor_round_rect(
                sx0,
                sy0,
                sx1 - sx0,
                sy1 - sy0,
                color[:4],
                radius=5.0 * scale,
            )
            if _editor_palette_metadata(editor, index)[2]:
                _draw_editor_circle(
                    sx1 - 6.0 * scale,
                    sy1 - 6.0 * scale,
                    2.5 * scale,
                    _THEME["accent"],
                )
        if hovered_index is not None:
            hovered_bounds = next(
                (bounds for index, bounds in all_entries if index == hovered_index),
                None,
            )
            if hovered_bounds is not None:
                tooltip_width = 222.0 * scale
                tooltip_height = 70.0 * scale
                tooltip_x = max(
                    8.0 * scale,
                    min(
                        x0 - tooltip_width - 10.0 * scale,
                        float(region.width) - tooltip_width - 8.0 * scale,
                    ),
                )
                tooltip_y = max(
                    8.0 * scale,
                    min(
                        hovered_bounds[1],
                        float(region.height) - tooltip_height - 8.0 * scale,
                    ),
                )
                hovered_title, hovered_metadata, hovered_gesture, hovered_favorite = (
                    _editor_palette_tooltip_content(editor, hovered_index)
                )
                _draw_editor_panel(
                    tooltip_x,
                    tooltip_y,
                    tooltip_width,
                    tooltip_height,
                    _THEME["panel"],
                    border=_THEME["border"],
                    radius=9.0 * scale,
                )
                _draw_editor_text(
                    hovered_title,
                    tooltip_x + 12.0 * scale,
                    tooltip_y + 48.0 * scale,
                    13.0,
                    _THEME["text"],
                )
                _draw_editor_text(
                    hovered_metadata,
                    tooltip_x + 12.0 * scale,
                    tooltip_y + 28.0 * scale,
                    10.0,
                    _THEME["accent"] if hovered_favorite else _THEME["text_muted"],
                )
                _draw_editor_text(
                    hovered_gesture,
                    tooltip_x + 12.0 * scale,
                    tooltip_y + 12.0 * scale,
                    10.0,
                    _THEME["text_muted"],
                )
    finally:
        gpu.state.blend_set("NONE")
def _draw_editor_view_hud(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None or gpu is None or batch_for_shader is None:
        return
    layout = _editor_view_hud_layout(editor, region)
    panel = layout["panel"]
    cube = layout["cube"]
    entries = layout["entries"]
    scale = ui_style.ui_scale()
    region_3d = getattr(
        getattr(getattr(context, "area", None), "spaces", None),
        "active",
        None,
    )
    region_3d = getattr(region_3d, "region_3d", None)
    projection = str(getattr(region_3d, "view_perspective", "PERSP"))
    projection_label = "ORTHO" if projection == "ORTHO" else "PERSP"
    label_map = dict(_EDITOR_VIEW_HUD_ACTIONS)
    x0, y0, x1, y1 = panel
    try:
        gpu.state.blend_set("ALPHA")
        _draw_editor_panel(
            x0,
            y0,
            x1 - x0,
            y1 - y0,
            _THEME["panel"],
            border=_THEME["border"],
            radius=10.0 * scale,
        )
        _draw_editor_text(
            "VIEWPORT",
            x0 + 12.0 * scale,
            y1 - 20.0 * scale,
            11.0,
            _THEME["text"],
        )
        _draw_editor_text(
            projection_label,
            x1 - _editor_text_width(projection_label, 10.0) - 12.0 * scale,
            y1 - 20.0 * scale,
            10.0,
            _THEME["accent"],
        )
        _draw_editor_text(
            "Orient and frame",
            x0 + 12.0 * scale,
            y1 - 37.0 * scale,
            10.0,
            _THEME["text_dim"],
        )
        cube_x0, cube_y0, cube_x1, cube_y1 = cube
        cube_center_x = (cube_x0 + cube_x1) * 0.5
        cube_center_y = (cube_y0 + cube_y1) * 0.5
        cube_mid_y = cube_center_y + 4.0 * scale
        cube_top = (cube_center_x, cube_y1)
        cube_left = (cube_x0, cube_mid_y + 6.0 * scale)
        cube_center = (cube_center_x, cube_mid_y - 10.0 * scale)
        cube_right = (cube_x1, cube_mid_y + 6.0 * scale)
        cube_bottom = (cube_center_x, cube_y0)
        _draw_editor_polygon(
            (cube_top, cube_right, cube_center, cube_left),
            _THEME["accent_fill"],
        )
        _draw_editor_polygon(
            (cube_left, cube_center, cube_bottom),
            _THEME["panel_raised"],
        )
        _draw_editor_polygon(
            (cube_center, cube_right, cube_bottom),
            _THEME["panel_hover"],
        )
        _draw_editor_text(
            "TOP",
            cube_center_x - _editor_text_width("TOP", 8.0) * 0.5,
            cube_center_y + 14.0 * scale,
            8.0,
            _THEME["text"],
        )
        _draw_editor_text(
            "FRONT",
            cube_x0 + 6.0 * scale,
            cube_center_y - 8.0 * scale,
            8.0,
            _THEME["text_muted"],
        )
        _draw_editor_text(
            "RIGHT",
            cube_center_x + 7.0 * scale,
            cube_center_y - 8.0 * scale,
            8.0,
            _THEME["text_muted"],
        )
        for action, label, bounds in entries:
            button_x0, button_y0, button_x1, button_y1 = bounds
            hovered = action == getattr(editor, "view_hud_hover", None)
            button_color = _THEME["panel_hover"] if hovered else _THEME["panel_raised"]
            border_color = _THEME["accent"] if hovered else _THEME["border_subtle"]
            _draw_editor_panel(
                button_x0,
                button_y0,
                button_x1 - button_x0,
                button_y1 - button_y0,
                button_color,
                border=border_color,
                radius=6.0 * scale,
            )
            button_label = label_map.get(action, label)
            if action == "VIEW_PROJECTION":
                button_label = "Ortho" if projection != "ORTHO" else "Persp"
            _draw_editor_round_rect(
                button_x0 + 8.0 * scale,
                button_y0 + 7.0 * scale,
                5.0 * scale,
                5.0 * scale,
                _THEME["accent"] if hovered else _THEME["text_dim"],
                radius=2.5 * scale,
            )
            _draw_editor_text(
                button_label,
                button_x0 + 19.0 * scale,
                button_y0 + 6.0 * scale,
                10.0,
                _THEME["text"],
            )
        _draw_editor_view_hud_tooltip(editor, context, entries, scale)
    finally:
        gpu.state.blend_set("NONE")


def _draw_editor_view_hud_tooltip(editor, context, entries, scale) -> None:
    action = getattr(editor, "view_hud_hover", None)
    hint = _editor_view_hud_hint(action)
    region = getattr(context, "region", None)
    entry = next(
        (
            (label, bounds)
            for entry_action, label, bounds in entries
            if entry_action == action
        ),
        None,
    )
    if hint is None or region is None or entry is None:
        return
    _label, bounds = entry
    title, description, shortcut = hint
    lines = ui_style.wrap_text(
        description,
        11.0,
        min(316.0 * scale, float(region.width) - 40.0 * scale),
    )[:2]
    if not lines:
        lines = ("Choose this control to continue.",)
    shortcut_width = (
        _editor_text_width(shortcut, 10.0) + 16.0 * scale
        if shortcut
        else 0.0
    )
    hint_width = max(
        (_editor_text_width(line, 11.0) for line in lines),
        default=0.0,
    )
    tooltip_width = min(
        float(region.width) - 16.0 * scale,
        max(
            244.0 * scale,
            _editor_text_width(title, 13.0) + shortcut_width + 38.0 * scale,
            hint_width + 24.0 * scale,
        ),
    )
    tooltip_height = (64.0 + max(0, len(lines) - 1) * 14.0) * scale
    tooltip_x = min(
        bounds[2] + 10.0 * scale,
        max(8.0 * scale, float(region.width) - tooltip_width - 8.0 * scale),
    )
    tooltip_y = max(
        8.0 * scale,
        min(
            bounds[1],
            float(region.height) - tooltip_height - 8.0 * scale,
        ),
    )
    _draw_editor_panel(
        tooltip_x,
        tooltip_y,
        tooltip_width,
        tooltip_height,
        _THEME["panel"],
        border=_THEME["border"],
        radius=9.0 * scale,
    )
    title_y = tooltip_y + tooltip_height - 20.0 * scale
    _draw_editor_text(
        title,
        tooltip_x + 12.0 * scale,
        title_y,
        13.0,
        _THEME["text"],
    )
    if shortcut:
        pill_x = tooltip_x + tooltip_width - shortcut_width - 10.0 * scale
        _draw_editor_round_rect(
            pill_x,
            title_y - 3.0 * scale,
            shortcut_width,
            18.0 * scale,
            _THEME["accent_fill"],
            radius=8.0 * scale,
        )
        _draw_editor_text(
            shortcut,
            pill_x + 8.0 * scale,
            title_y + 1.0 * scale,
            10.0,
            _THEME["text"],
        )
    for index, line in enumerate(lines):
        _draw_editor_text(
            line,
            tooltip_x + 12.0 * scale,
            tooltip_y + 27.0 * scale + (len(lines) - index - 1) * 14.0 * scale,
            11.0,
            _THEME["text_muted"],
        )



def _editor_dock_panel_bounds(editor, context, name: str):
    region = getattr(context, "region", None)
    if region is None:
        return None
    panel = _editor_dock_layout(editor, region)["panels"].get(name)
    return None if panel is None else panel["bounds"]


def _editor_dock_header_bounds(bounds, scale: float):
    x0, y0, x1, y1 = bounds
    return (x0, max(y0, y1 - 36.0 * scale), x1, y1)


def _editor_dock_hover_bounds(editor, layout, hover, scale):
    if not hover or len(hover) < 2:
        return None
    name, kind = hover[:2]
    panel_info = layout["panels"].get(name)
    if panel_info is None:
        return None
    bounds = panel_info["bounds"]
    if kind == "HEADER":
        return _editor_dock_header_bounds(bounds, scale)
    if kind != "ROW" or len(hover) < 3:
        return None
    index = hover[2]
    if not isinstance(index, int):
        return None
    if name == "LAYERS":
        rows = tuple(getattr(getattr(editor, "stack", None), "layers", ()) or ())
        if not 0 <= index < len(rows):
            return None
        display_index = len(rows) - 1 - index
    elif name == "HISTORY":
        rows = tuple(getattr(editor, "history", ()) or ())
        visible_start = max(0, len(rows) - 8)
        if not visible_start <= index < len(rows):
            return None
        display_index = len(rows) - 1 - index
    else:
        return None
    row_height = 28.0 * scale
    row_top = bounds[3] - 40.0 * scale - display_index * row_height
    row_bottom = row_top - row_height + 4.0 * scale
    if row_bottom < bounds[1]:
        return None
    return (
        bounds[0] + 8.0 * scale,
        row_bottom,
        bounds[2] - 8.0 * scale,
        row_top,
    )


def _editor_dock_tooltip_content(editor, hover):
    if not hover or len(hover) < 2:
        return None
    name, kind = hover[:2]
    if kind == "HEADER":
        return _editor_dock_hint(name)
    if kind != "ROW" or len(hover) < 3:
        return None
    index = hover[2]
    if not isinstance(index, int):
        return None
    if name == "LAYERS":
        layers = tuple(getattr(getattr(editor, "stack", None), "layers", ()) or ())
        if not 0 <= index < len(layers):
            return None
        layer = layers[index]
        title = str(getattr(layer, "name", f"Layer {index + 1}"))
        return title, "Make this voxel layer active."
    if name == "HISTORY":
        history = tuple(getattr(editor, "history", ()) or ())
        if not 0 <= index < len(history):
            return None
        snapshot = history[index]
        title = str(
            getattr(
                snapshot,
                "label",
                "Initial state" if index == 0 else f"Edit {index}",
            )
            or ("Initial state" if index == 0 else f"Edit {index}")
        )
        return title, "Restore this committed edit."
    return None


def _editor_dock_hit(editor, context, event):
    region = getattr(context, "region", None)
    if region is None:
        return None
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    scale = ui_style.ui_scale()
    layout = _editor_dock_layout(editor, region)
    for name in EDITOR_DOCK_PANELS:
        panel_info = layout["panels"].get(name)
        if panel_info is None:
            continue
        bounds = panel_info["bounds"]
        header = _editor_dock_header_bounds(bounds, scale)
        if header[0] <= x <= header[2] and header[1] <= y <= header[3]:
            return (name, "HEADER", None)
        if name == "LAYERS":
            layers = tuple(getattr(getattr(editor, "stack", None), "layers", ()) or ())
            row_height = 28.0 * scale
            for display_index, layer_index in enumerate(reversed(range(len(layers)))):
                row_top = bounds[3] - 40.0 * scale - display_index * row_height
                row_bottom = row_top - row_height + 4.0 * scale
                if row_bottom <= y <= row_top and bounds[0] <= x <= bounds[2]:
                    return (name, "ROW", layer_index)
        elif name == "HISTORY":
            history = tuple(getattr(editor, "history", ()) or ())
            row_height = 28.0 * scale
            visible = range(max(0, len(history) - 8), len(history))
            for display_index, history_index in enumerate(reversed(tuple(visible))):
                row_top = bounds[3] - 40.0 * scale - display_index * row_height
                row_bottom = row_top - row_height + 4.0 * scale
                if row_bottom <= y <= row_top and bounds[0] <= x <= bounds[2]:
                    return (name, "ROW", history_index)
    return None


def _draw_editor_dock_header(name: str, bounds, editor, scale: float) -> None:
    x0, y0, x1, y1 = bounds
    header = _editor_dock_header_bounds(bounds, scale)
    hovered = getattr(editor, "dock_hover", None)
    hovered = hovered is not None and hovered[0] == name and hovered[1] == "HEADER"
    _draw_editor_rect(
        header[0],
        header[1],
        header[2] - header[0],
        header[3] - header[1],
        _THEME["panel_hover"] if hovered else _THEME["panel"],
    )
    _draw_editor_icon(
        {"PALETTE": "palette", "LAYERS": "layers", "HISTORY": "details"}[name],
        x0 + 16.0 * scale,
        y1 - 18.0 * scale,
        15.0 * scale,
        _THEME["accent"] if hovered else _THEME["text_muted"],
    )
    _draw_editor_text(
        {"PALETTE": "COLORS", "LAYERS": "LAYERS", "HISTORY": "HISTORY"}[name],
        x0 + 30.0 * scale,
        y1 - 22.0 * scale,
        10.0,
        _THEME["text"],
    )
    for index in range(3):
        _draw_editor_circle(
            x1 - 17.0 * scale + index * 4.0 * scale,
            y1 - 18.0 * scale,
            1.2 * scale,
            _THEME["text_dim"],
        )


def _draw_editor_dock_tooltip(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None or gpu is None or batch_for_shader is None:
        return
    scale = ui_style.ui_scale()
    layout = _editor_dock_layout(editor, region)
    hover = getattr(editor, "dock_hover", None)
    content = _editor_dock_tooltip_content(editor, hover)
    target_bounds = _editor_dock_hover_bounds(editor, layout, hover, scale)
    if content is None or target_bounds is None:
        return
    title, description = content
    lines = ui_style.wrap_text(
        description,
        11.0,
        min(316.0 * scale, float(region.width) - 40.0 * scale),
    )[:2]
    if not lines:
        lines = ("Choose this panel to continue.",)
    hint_width = max(
        (_editor_text_width(line, 11.0) for line in lines),
        default=0.0,
    )
    tooltip_width = min(
        float(region.width) - 16.0 * scale,
        max(
            244.0 * scale,
            _editor_text_width(title, 13.0) + 32.0 * scale,
            hint_width + 24.0 * scale,
        ),
    )
    tooltip_height = (52.0 + max(0, len(lines) - 1) * 14.0) * scale
    panel_info = layout["panels"].get(hover[0])
    side = panel_info.get("side") if panel_info is not None else "RIGHT"
    if side == "LEFT":
        tooltip_x = min(
            target_bounds[2] + 10.0 * scale,
            max(8.0 * scale, float(region.width) - tooltip_width - 8.0 * scale),
        )
    else:
        tooltip_x = max(
            8.0 * scale,
            min(
                target_bounds[0] - tooltip_width - 10.0 * scale,
                float(region.width) - tooltip_width - 8.0 * scale,
            ),
        )
    tooltip_y = max(
        8.0 * scale,
        min(
            target_bounds[1],
            float(region.height) - tooltip_height - 8.0 * scale,
        ),
    )
    try:
        gpu.state.blend_set("ALPHA")
        _draw_editor_panel(
            tooltip_x,
            tooltip_y,
            tooltip_width,
            tooltip_height,
            _THEME["panel"],
            border=_THEME["border"],
            radius=9.0 * scale,
        )
        title_y = tooltip_y + tooltip_height - 20.0 * scale
        _draw_editor_text(
            title,
            tooltip_x + 12.0 * scale,
            title_y,
            13.0,
            _THEME["text"],
        )
        for index, line in enumerate(lines):
            _draw_editor_text(
                line,
                tooltip_x + 12.0 * scale,
                tooltip_y + 11.0 * scale + (len(lines) - index - 1) * 14.0 * scale,
                11.0,
                _THEME["text_muted"],
            )
    finally:
        gpu.state.blend_set("NONE")


def _draw_editor_dock_panels(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None or gpu is None or batch_for_shader is None:
        return
    scale = ui_style.ui_scale()
    layout = _editor_dock_layout(editor, region)
    try:
        gpu.state.blend_set("ALPHA")
        for bounds in layout["drawers"].values():
            _draw_editor_panel(
                bounds[0],
                bounds[1],
                bounds[2] - bounds[0],
                bounds[3] - bounds[1],
                _THEME["canvas"],
                border=_THEME["border_subtle"],
                radius=12.0 * scale,
            )
        for name in ("LAYERS", "HISTORY"):
            panel_info = layout["panels"].get(name)
            if panel_info is None:
                continue
            bounds = panel_info["bounds"]
            _draw_editor_panel(
                bounds[0],
                bounds[1],
                bounds[2] - bounds[0],
                bounds[3] - bounds[1],
                _THEME["panel"],
                border=_THEME["border_subtle"],
                radius=10.0 * scale,
            )
            _draw_editor_dock_header(name, bounds, editor, scale)
            x0, y0, x1, y1 = bounds
            row_height = 28.0 * scale
            if name == "LAYERS":
                layers = tuple(getattr(getattr(editor, "stack", None), "layers", ()) or ())
                active_index = int(getattr(getattr(editor, "stack", None), "active_index", -1))
                for display_index, layer_index in enumerate(reversed(range(len(layers)))):
                    row_top = y1 - 40.0 * scale - display_index * row_height
                    row_bottom = row_top - row_height + 4.0 * scale
                    if row_bottom < y0:
                        break
                    layer = layers[layer_index]
                    row_hover = getattr(editor, "dock_hover", None) == (name, "ROW", layer_index)
                    _draw_editor_panel(
                        x0 + 8.0 * scale,
                        row_bottom,
                        x1 - x0 - 16.0 * scale,
                        row_top - row_bottom,
                        _THEME["accent_soft"] if layer_index == active_index else (
                            _THEME["panel_hover"] if row_hover else _THEME["canvas"]
                        ),
                        border=_THEME["accent"] if layer_index == active_index else _THEME["border_subtle"],
                        radius=5.0 * scale,
                    )
                    _draw_editor_icon(
                        "layers",
                        x0 + 21.0 * scale,
                        (row_top + row_bottom) * 0.5,
                        13.0 * scale,
                        _THEME["accent"] if layer_index == active_index else _THEME["text_muted"],
                    )
                    label = str(getattr(layer, "name", f"Layer {layer_index + 1}"))
                    _draw_editor_text(label[:22], x0 + 34.0 * scale, row_bottom + 10.0 * scale, 10.0, _THEME["text"])
                    suffix = "LOCK" if bool(getattr(layer, "locked", False)) else f"{int(round(float(getattr(layer, 'opacity', 1.0)) * 100.0))}%"
                    _draw_editor_text(
                        suffix,
                        x1 - _editor_text_width(suffix, 9.0) - 14.0 * scale,
                        row_bottom + 10.0 * scale,
                        9.0,
                        _THEME["text_dim"],
                    )
            else:
                history = tuple(getattr(editor, "history", ()) or ())
                current = int(getattr(editor, "history_index", -1))
                visible = range(max(0, len(history) - 8), len(history))
                for display_index, history_index in enumerate(reversed(tuple(visible))):
                    row_top = y1 - 40.0 * scale - display_index * row_height
                    row_bottom = row_top - row_height + 4.0 * scale
                    if row_bottom < y0:
                        break
                    row_hover = getattr(editor, "dock_hover", None) == (name, "ROW", history_index)
                    _draw_editor_panel(
                        x0 + 8.0 * scale,
                        row_bottom,
                        x1 - x0 - 16.0 * scale,
                        row_top - row_bottom,
                        _THEME["accent_soft"] if history_index == current else (
                            _THEME["panel_hover"] if row_hover else _THEME["canvas"]
                        ),
                        border=_THEME["accent"] if history_index == current else _THEME["border_subtle"],
                        radius=5.0 * scale,
                    )
                    snapshot = history[history_index]
                    label = str(
                        getattr(
                            snapshot,
                            "label",
                            "Initial state" if history_index == 0 else f"Edit {history_index}",
                        )
                        or ("Initial state" if history_index == 0 else f"Edit {history_index}")
                    )
                    _draw_editor_text(label, x0 + 16.0 * scale, row_bottom + 10.0 * scale, 10.0, _THEME["text"])
                    _draw_editor_text(
                        "CURRENT" if history_index == current else str(history_index + 1),
                        x1 - 48.0 * scale,
                        row_bottom + 10.0 * scale,
                        9.0,
                        _THEME["accent"] if history_index == current else _THEME["text_dim"],
                    )
        dragging = getattr(editor, "dock_dragging", None)
        if dragging:
            drag_x = float(getattr(editor, "dock_drag_x", 0.0))
            drag_y = float(getattr(editor, "dock_drag_y", 0.0))
            drag_width = layout["width"]
            drag_height = 54.0 * scale
            _draw_editor_panel(
                max(8.0 * scale, drag_x - drag_width * 0.5),
                max(8.0 * scale, drag_y - drag_height * 0.5),
                drag_width,
                drag_height,
                _THEME["panel_hover"],
                border=_THEME["accent"],
                radius=8.0 * scale,
            )
            _draw_editor_text(
                dragging.title(),
                max(8.0 * scale, drag_x - drag_width * 0.5 + 16.0 * scale),
                max(8.0 * scale, drag_y - 4.0 * scale),
                11.0,
                _THEME["text"],
            )
    finally:
        gpu.state.blend_set("NONE")

def _editor_toolbar_active(editor, item) -> bool:
    kind, value = item[:2]
    if kind == "MODE":
        return _editor_input_mode(editor) == value
    if kind == "SHAPE":
        return _scene_shape(editor.scene) == value
    return _scene_mode(editor.scene) == "SELECT" and _scene_shape(editor.scene) == value
_EDITOR_TOOL_HINTS = {
    ("MODE", "ADD"): "Click a face to add a block.",
    ("MODE", "ATTACH"): "Stick a brush onto the face you click.",
    ("MODE", "ERASE"): "Right-click a block to remove it.",
    ("MODE", "PAINT"): "Drag over blocks to color them.",
    ("MODE", "SELECT"): "Click blocks to copy or move them.",
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


def _editor_toolbar_hint(item) -> str:
    return _EDITOR_TOOL_HINTS.get(item[:2], "Choose this tool for your next edit.")

_EDITOR_TOOL_OPTIONS_HINTS = {
    "SCENE_MOVE": ("Move", "Move the selected asset with the arrow keys.", "G"),
    "SCENE_ROTATE": ("Rotate", "Rotate the selected asset in 90° steps.", "R"),
    "SCENE_SCALE": ("Scale", "Scale the selected asset in 10% steps.", "S"),
    "SCENE_APPLY": ("Apply", "Keep the current scene transform.", "Enter"),
    "SCENE_CANCEL": ("Cancel", "Restore the transform snapshot.", "Esc"),
    "TOOL_SELECT_ALL": ("All", "Select every block in the active asset.", "Ctrl+A"),
    "TOOL_CLEAR_SELECTION": ("Clear", "Remove the current block selection.", "Ctrl+D"),
    "TOOL_COPY_SELECTION": ("Copy", "Copy selected blocks to the clipboard.", "Ctrl+C"),
    "TOOL_CUT_SELECTION": ("Cut", "Remove selected blocks and copy them.", "Ctrl+X"),
    "TOOL_RADIUS_DOWN": ("Smaller brush", "Reduce the brush radius by one cell.", "["),
    "TOOL_RADIUS_UP": ("Larger brush", "Increase the brush radius by one cell.", "]"),
    "TOOL_HOLLOW": ("Hollow", "Toggle hollow stamping for solid shapes.", "H"),
    "TOOL_MIRROR_X": ("Mirror X", "Mirror the next brush stamp across X.", "1"),
    "TOOL_MIRROR_Y": ("Mirror Y", "Mirror the next brush stamp across Y.", "2"),
    "TOOL_MIRROR_Z": ("Mirror Z", "Mirror the next brush stamp across Z.", "3"),
    "TOOL_AXIS_X": ("Axis X", "Use X as the cylinder axis.", ""),
    "TOOL_AXIS_Y": ("Axis Y", "Use Y as the cylinder axis.", ""),
    "TOOL_AXIS_Z": ("Axis Z", "Use Z as the cylinder axis.", ""),
    "TOOL_HEIGHT_DOWN": ("Shorter cylinder", "Reduce cylinder height by one cell.", ""),
    "TOOL_HEIGHT_UP": ("Taller cylinder", "Increase cylinder height by one cell.", ""),
    "TOOL_DEPTH_DOWN": ("Shallower face", "Reduce face brush depth by one cell.", ""),
    "TOOL_DEPTH_UP": ("Deeper face", "Increase face brush depth by one cell.", ""),
}


def _editor_tool_option_hint(action):
    return _EDITOR_TOOL_OPTIONS_HINTS.get(action)


_EDITOR_ICON_NAMES = {
    ("MODE", "ADD"): "place",
    ("MODE", "ATTACH"): "attach",
    ("MODE", "ERASE"): "erase",
    ("MODE", "PAINT"): "paint",
    ("MODE", "SELECT"): "pick",
    ("SHAPE", "VOXEL"): "cube",
    ("SHAPE", "LINE"): "line",
    ("SHAPE", "BOX"): "box",
    ("SHAPE", "SPHERE"): "sphere",
    ("SHAPE", "FACE"): "face",
    ("SELECT", "LASSO"): "lasso",
    ("SELECT", "CIRCLE"): "circle",
    ("SELECT", "COLOR"): "palette",
    ("SELECT", "COMPONENT"): "component",
    ("SELECT", "SURFACE"): "surface",
}
_EDITOR_TOPBAR_ICONS = {
    "UNDO": "undo",
    "REDO": "redo",
    "PROJECT": "folder",
    "SAVE": "save",
    "HELP": "help",
    "OPTIONS": "toolbox",
    "MENU": "menu",
    "DOCK_LEFT": "dock_left",
    "DOCK_RIGHT": "dock_right",
}


_EDITOR_TOPBAR_SHORTCUTS = {
    "UNDO": "Ctrl+Z",
    "REDO": "Ctrl+Y",
}


_EDITOR_TOPBAR_HINTS = {
    "UNDO": "Undo your last change. You can also press Ctrl+Z.",
    "REDO": "Bring back the change you just undid. You can also press Ctrl+Y.",
    "PROJECT": "Go home to your projects.",
    "SAVE": "Save your block world.",
    "HELP": "Show the simple steps.",
    "OPTIONS": "Open a few extra choices.",
    "MENU": "Open File, Edit, View, and Tools commands.",
    "DOCK_LEFT": "Show or hide the left panel drawer.",
    "DOCK_RIGHT": "Show or hide the right panel drawer.",
}


_EDITOR_TOPBAR_LABELS = {
    "UNDO": "Undo",
    "REDO": "Redo",
    "PROJECT": "Home",
    "SAVE": "Save",
    "HELP": "Help",
    "OPTIONS": "More",
    "MENU": "Menu",
    "DOCK_LEFT": "Left panel",
    "DOCK_RIGHT": "Right panel",
}


def _editor_topbar_hint(action):
    return _EDITOR_TOPBAR_HINTS.get(action, "Choose this control to continue.")


def _editor_topbar_label(action, *, help_visible=False):
    if action == "HELP" and help_visible:
        return "Close help"
    return _EDITOR_TOPBAR_LABELS.get(action, str(action).replace("_", " ").title())


def _draw_editor_icon(name: str, x: float, y: float, size: float, color) -> bool:
    try:
        from . import desktop_shell

        desktop_shell._draw_icon(name, x, y, size, color)
    except (AttributeError, ImportError, RuntimeError, TypeError):
        return False
    return True


def _draw_editor_rect(x: float, y: float, width: float, height: float, color) -> None:
    if gpu is None or batch_for_shader is None:
        return
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

def _editor_rounded_points(
    x: float,
    y: float,
    width: float,
    height: float,
    radius: float,
    segments: int = 5,
):
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
        points.append(
            (
                next_center_x + math.cos(next_start) * radius,
                next_center_y + math.sin(next_start) * radius,
            )
        )
    return tuple(points)


def _draw_editor_round_rect(
    x: float,
    y: float,
    width: float,
    height: float,
    color,
    radius: float = 8.0,
) -> None:
    if gpu is None or batch_for_shader is None:
        return
    boundary = _editor_rounded_points(x, y, width, height, radius)
    points = ((x + width / 2.0, y + height / 2.0), *boundary)
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": points})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)

def _draw_editor_polygon(points, color) -> None:
    if gpu is None or batch_for_shader is None or len(points) < 3:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": tuple(points)})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_editor_circle(x: float, y: float, radius: float, color, segments: int = 20) -> None:
    if radius <= 0.0:
        return
    boundary = tuple(
        (
            x + math.cos(math.tau * index / segments) * radius,
            y + math.sin(math.tau * index / segments) * radius,
        )
        for index in range(segments)
    )
    _draw_editor_polygon(((x, y), *boundary), color)


def _draw_editor_smooth_triangle(vertices, colors) -> None:
    if gpu is None or batch_for_shader is None:
        return
    try:
        shader = gpu.shader.from_builtin("3D_SMOOTH_COLOR")
        positions = tuple((x, y, 0.0) for x, y in vertices)
        batch = batch_for_shader(
            shader,
            "TRIS",
            {"pos": positions, "color": tuple(colors)},
        )
        shader.bind()
        batch.draw(shader)
    except Exception:
        average = tuple(
            sum(color[channel] for color in colors) / len(colors)
            for channel in range(4)
        )
        _draw_editor_polygon(vertices, average)


def _draw_editor_panel(
    x: float,
    y: float,
    width: float,
    height: float,
    color,
    *,
    border=None,
    radius: float = 8.0,
) -> None:
    if border is None:
        _draw_editor_round_rect(x, y, width, height, color, radius)
        return
    inset = min(1.0, width / 4.0, height / 4.0)
    _draw_editor_round_rect(x, y, width, height, border, radius)
    _draw_editor_round_rect(
        x + inset,
        y + inset,
        width - inset * 2.0,
        height - inset * 2.0,
        color,
        max(0.0, radius - inset),
    )


def _draw_editor_toolbar(editor, context) -> None:
    if gpu is None or batch_for_shader is None or _scene_workspace(context.scene) != "EDIT":
        return
    region = getattr(context, "region", None)
    if region is None:
        return
    scale = ui_style.ui_scale()
    panel, entries = _editor_toolbar_layout(region.height, context.scene)
    try:
        gpu.state.blend_set("ALPHA")
        _draw_editor_panel(
            *panel,
            _THEME["panel"],
            border=_THEME["border"],
            radius=10.0 * scale,
        )
        for item, bounds in entries:
            x0, y0, x1, y1 = bounds
            width = x1 - x0
            height = y1 - y0
            base_height = height / scale
            hovered = item == getattr(editor, "toolbar_hover", None)
            active = _editor_toolbar_active(editor, item)
            if active and item[0] == "MODE" and item[1] == "ERASE":
                color = _THEME["danger_fill"]
            elif active:
                color = _THEME["accent_fill"]
            elif hovered:
                color = _THEME["panel_hover"]
            else:
                color = _THEME["panel_raised"]
            border_color = (
                _THEME["danger"]
                if active and item[0] == "MODE" and item[1] == "ERASE"
                else _THEME["accent"]
                if active or hovered
                else _THEME["border_subtle"]
            )
            _draw_editor_panel(
                x0,
                y0,
                width,
                height,
                color,
                border=border_color,
                radius=min(7.0 * scale, height / 2.0),
            )
            beginner = visible_editor_toolbar_items(context.scene) is BEGINNER_TOOLBAR_ITEMS
            icon_name = _EDITOR_ICON_NAMES.get(item[:2])
            if beginner:
                icon_drawn = bool(
                    icon_name
                    and _draw_editor_icon(
                        icon_name,
                        x0 + 16.0 * scale,
                        y0 + height * 0.5,
                        min(17.0 * scale, height - 8.0 * scale),
                        _THEME["text"],
                    )
                )
                label_x = x0 + (30.0 if icon_drawn else 10.0) * scale
                _draw_editor_text(
                    item[3],
                    label_x,
                    y0 + height * 0.5,
                    max(10.0, min(13.0, base_height * 0.42)),
                    _THEME["text"],
                )
                shortcut_size = max(8.0, min(10.0, base_height * 0.23))
                shortcut_width = _editor_text_width(item[4], shortcut_size)
                _draw_editor_text(
                    item[4],
                    x1 - shortcut_width - 10.0 * scale,
                    y0 + 5.0 * scale,
                    shortcut_size,
                    _THEME["text_muted"],
                )
            else:
                icon_drawn = bool(
                    icon_name
                    and _draw_editor_icon(
                        icon_name,
                        x0 + width * 0.5,
                        y0 + height * 0.5,
                        max(14.0 * scale, min(19.0 * scale, height * 0.48)),
                        _THEME["text"],
                    )
                )
                if not icon_drawn:
                    glyph_size = max(12.0, min(19.0, base_height * 0.42))
                    glyph_offset = len(item[2]) * glyph_size * 0.25 * scale
                    _draw_editor_text(
                        item[2],
                        x0 + width * 0.5 - glyph_offset,
                        y0 + height * 0.48,
                        glyph_size,
                        _THEME["text"],
                    )
                shortcut_size = max(8.0, min(10.0, base_height * 0.23))
                shortcut_width = _editor_text_width(item[4], shortcut_size)
                _draw_editor_text(
                    item[4],
                    x1 - shortcut_width - 6.0 * scale,
                    y0 + 5.0 * scale,
                    shortcut_size,
                    _THEME["text_muted"],
                )
        hovered_item = getattr(editor, "toolbar_hover", None)
        if hovered_item is not None:
            hovered_bounds = next(
                (bounds for item, bounds in entries if item == hovered_item),
                None,
            )
            if hovered_bounds is not None:
                hint_lines = ui_style.wrap_text(
                    _editor_toolbar_hint(hovered_item),
                    11.0,
                    min(336.0 * scale, float(region.width) - 40.0 * scale),
                )[:2]
                if not hint_lines:
                    hint_lines = ("Choose this tool for your next edit.",)
                title_width = _editor_text_width(hovered_item[3], 13.0)
                shortcut_width = _editor_text_width(hovered_item[4], 10.0) + 16.0 * scale
                hint_width = max(
                    (_editor_text_width(line, 11.0) for line in hint_lines),
                    default=0.0,
                )
                tooltip_width = min(
                    max(
                        244.0 * scale,
                        title_width + shortcut_width + 38.0 * scale,
                        hint_width + 24.0 * scale,
                    ),
                    float(region.width) - 16.0 * scale,
                )
                tooltip_height = (64.0 + max(0, len(hint_lines) - 1) * 14.0) * scale
                tooltip_x = min(
                    hovered_bounds[2] + 10.0 * scale,
                    max(8.0 * scale, float(region.width) - tooltip_width - 8.0 * scale),
                )
                tooltip_y = max(
                    8.0 * scale,
                    min(
                        hovered_bounds[1],
                        float(region.height) - tooltip_height - 8.0 * scale,
                    ),
                )
                _draw_editor_panel(
                    tooltip_x,
                    tooltip_y,
                    tooltip_width,
                    tooltip_height,
                    _THEME["panel"],
                    border=_THEME["border"],
                    radius=9.0 * scale,
                )
                title_y = tooltip_y + tooltip_height - 20.0 * scale
                _draw_editor_text(
                    hovered_item[3],
                    tooltip_x + 12.0 * scale,
                    title_y,
                    13.0,
                    _THEME["text"],
                )
                pill_width = shortcut_width
                pill_x = tooltip_x + tooltip_width - pill_width - 10.0 * scale
                _draw_editor_round_rect(
                    pill_x,
                    title_y - 3.0 * scale,
                    pill_width,
                    18.0 * scale,
                    _THEME["accent_fill"],
                    radius=8.0 * scale,
                )
                _draw_editor_text(
                    hovered_item[4],
                    pill_x + 8.0 * scale,
                    title_y + 1.0 * scale,
                    10.0,
                    _THEME["text"],
                )
                for index, line in enumerate(hint_lines):
                    _draw_editor_text(
                        line,
                        tooltip_x + 12.0 * scale,
                        tooltip_y + 27.0 * scale + (len(hint_lines) - index - 1) * 14.0 * scale,
                        11.0,
                        _THEME["text_muted"],
                    )
    finally:
        gpu.state.blend_set("NONE")

def _draw_editor_tool_option_hint(editor, context, entries, scale) -> None:
    action = getattr(editor, "tool_options_hover", None)
    hint = _editor_tool_option_hint(action)
    region = getattr(context, "region", None)
    entry = entries.get(action)
    if hint is None or region is None or entry is None:
        return
    _label, bounds = entry
    title, description, shortcut = hint
    lines = ui_style.wrap_text(
        description,
        11.0,
        min(316.0 * scale, float(region.width) - 40.0 * scale),
    )[:2]
    if not lines:
        lines = ("Choose this control to continue.",)
    shortcut_width = (
        _editor_text_width(shortcut, 10.0) + 16.0 * scale
        if shortcut
        else 0.0
    )
    hint_width = max(
        (_editor_text_width(line, 11.0) for line in lines),
        default=0.0,
    )
    tooltip_width = min(
        float(region.width) - 16.0 * scale,
        max(
            244.0 * scale,
            _editor_text_width(title, 13.0) + shortcut_width + 38.0 * scale,
            hint_width + 24.0 * scale,
        ),
    )
    tooltip_height = (64.0 + max(0, len(lines) - 1) * 14.0) * scale
    tooltip_x = min(
        bounds[2] + 10.0 * scale,
        max(8.0 * scale, float(region.width) - tooltip_width - 8.0 * scale),
    )
    tooltip_y = max(
        8.0 * scale,
        min(
            bounds[1],
            float(region.height) - tooltip_height - 8.0 * scale,
        ),
    )
    _draw_editor_panel(
        tooltip_x,
        tooltip_y,
        tooltip_width,
        tooltip_height,
        _THEME["panel"],
        border=_THEME["border"],
        radius=9.0 * scale,
    )
    title_y = tooltip_y + tooltip_height - 20.0 * scale
    _draw_editor_text(
        title,
        tooltip_x + 12.0 * scale,
        title_y,
        13.0,
        _THEME["text"],
    )
    if shortcut:
        pill_x = tooltip_x + tooltip_width - shortcut_width - 10.0 * scale
        _draw_editor_round_rect(
            pill_x,
            title_y - 3.0 * scale,
            shortcut_width,
            18.0 * scale,
            _THEME["accent_fill"],
            radius=8.0 * scale,
        )
        _draw_editor_text(
            shortcut,
            pill_x + 8.0 * scale,
            title_y + 1.0 * scale,
            10.0,
            _THEME["text"],
        )
    for index, line in enumerate(lines):
        _draw_editor_text(
            line,
            tooltip_x + 12.0 * scale,
            tooltip_y + 27.0 * scale + (len(lines) - index - 1) * 14.0 * scale,
            11.0,
            _THEME["text_muted"],
        )


def _draw_editor_tool_options(editor, context) -> None:
    workspace = _scene_workspace(context.scene)
    if gpu is None or batch_for_shader is None or workspace not in {"EDIT", "SCENE"}:
        return
    region = getattr(context, "region", None)
    if region is None:
        return
    layout = _editor_tool_options_layout(editor, region)
    x0, y0, x1, y1 = layout["panel"]
    if x1 <= x0 or y1 <= y0:
        return
    scale = ui_style.ui_scale()
    scene = context.scene
    mode_item = layout["mode_item"]
    shape_item = layout["shape_item"]
    mode = _editor_input_mode(editor)
    shape = _scene_shape(scene)
    mode_colors = {
        "ADD": (_THEME["success_fill"], _THEME["success"]),
        "ATTACH": (_THEME["accent_soft"], _THEME["accent"]),
        "ERASE": (_THEME["danger_fill"], _THEME["danger"]),
        "PAINT": (_THEME["warning_fill"], _THEME["warning"]),
        "SELECT": (_THEME["accent_soft"], _THEME["accent_hover"]),
    }
    mode_fill, mode_accent = mode_colors.get(
        mode,
        (_THEME["panel_raised"], _THEME["accent"]),
    )
    panel_title = "SCENE OPTIONS" if workspace == "SCENE" else "TOOL OPTIONS"
    if workspace == "SCENE":
        mode_accent = _THEME["warning"]
    entries = {
        action: (label, bounds)
        for action, label, bounds in layout["entries"]
    }
    shortcuts = {
        "ADD": "B",
        "ATTACH": "T",
        "ERASE": "E",
        "PAINT": "P",
        "SELECT": "S",
    }
    shape_shortcuts = {
        "VOXEL": "V",
        "LINE": "L",
        "BOX": "O",
        "SPHERE": "C",
        "CYLINDER": "Y",
        "FACE": "F",
        "LASSO": "J",
        "CIRCLE": "K",
        "COLOR": "M",
        "COMPONENT": "N",
        "SURFACE": "R",
    }

    def active_action(action):
        if action == "TOOL_HOLLOW":
            return bool(scene.voxtart_editor_hollow)
        if action.startswith("TOOL_MIRROR_"):
            axis = action.rsplit("_", 1)[-1].lower()
            return bool(getattr(scene, f"voxtart_editor_mirror_{axis}", False))
        if action.startswith("TOOL_AXIS_"):
            return str(scene.voxtart_editor_axis) == action.rsplit("_", 1)[-1]
        return False

    try:
        gpu.state.blend_set("ALPHA")
        _draw_editor_panel(
            x0,
            y0,
            x1 - x0,
            y1 - y0,
            _THEME["panel"],
            border=_THEME["border"],
            radius=10.0 * scale,
        )
        _draw_editor_text(
            panel_title,
            x0 + 12.0 * scale,
            y1 - 19.0 * scale,
            10.0,
            _THEME["accent"],
        )
        if workspace == "SCENE":
            transform_kind = str(getattr(editor, "scene_transform_kind", "MOVE")).title()
            active_label = (
                f"Scene · {transform_kind}"
                if getattr(editor, "scene_transform_active", False)
                else "Scene transform"
            )
            shortcut_label = "G · R · S"
            description = (
                "Move, rotate, or scale the asset with arrows; "
                "Enter commits; Esc cancels."
            )
        else:
            active_label = f"{mode_item[1]} · {shape_item[1]}"
            if mode == "SELECT":
                active_label = f"{active_label} · {len(getattr(editor, 'selection', ()) or ())} selected"
            shortcut_label = f"{shortcuts.get(mode, '')} · {shape_shortcuts.get(shape, '')}"
            description = (
                mode_item[2]
                if shape == "VOXEL"
                else shape_item[2]
            )
        _draw_editor_text(
            active_label,
            x0 + 12.0 * scale,
            y1 - 40.0 * scale,
            13.0,
            _THEME["text"],
        )
        shortcut_width = _editor_text_width(shortcut_label, 10.0)
        _draw_editor_text(
            shortcut_label,
            x1 - shortcut_width - 12.0 * scale,
            y1 - 39.0 * scale,
            10.0,
            mode_accent,
        )
        description_lines = ui_style.wrap_text(
            description,
            9.0,
            max(120.0 * scale, x1 - x0 - 24.0 * scale),
        )[:2]
        for line_index, line in enumerate(description_lines):
            _draw_editor_text(
                line,
                x0 + 12.0 * scale,
                y1 - (58.0 + line_index * 12.0) * scale,
                9.0,
                _THEME["text_muted"],
            )
        if workspace == "SCENE":
            transform_kind = str(getattr(editor, "scene_transform_kind", "MOVE")).upper()
            for action in ("SCENE_MOVE", "SCENE_ROTATE", "SCENE_SCALE"):
                label, bounds = entries[action]
                bx0, by0, bx1, by1 = bounds
                active = (
                    getattr(editor, "scene_transform_active", False)
                    and transform_kind == action.rsplit("_", 1)[-1]
                )
                hovered = action == getattr(editor, "tool_options_hover", None)
                fill = _THEME["accent_fill"] if active else _THEME["panel_raised"]
                if hovered:
                    fill = _THEME["panel_hover"]
                border = mode_accent if active or hovered else _THEME["border_subtle"]
                _draw_editor_panel(
                    bx0,
                    by0,
                    bx1 - bx0,
                    by1 - by0,
                    fill,
                    border=border,
                    radius=6.0 * scale,
                )
                _draw_editor_text(
                    label,
                    bx0 + 8.0 * scale,
                    by0 + 7.0 * scale,
                    10.0,
                    _THEME["text"],
                )
            if getattr(editor, "scene_transform_active", False):
                for action in ("SCENE_APPLY", "SCENE_CANCEL"):
                    label, bounds = entries[action]
                    bx0, by0, bx1, by1 = bounds
                    hovered = action == getattr(editor, "tool_options_hover", None)
                    destructive = action == "SCENE_CANCEL"
                    fill = _THEME["danger_fill"] if destructive else _THEME["success_fill"]
                    if hovered:
                        fill = _THEME["panel_hover"]
                    border = (
                        _THEME["danger"]
                        if destructive and not hovered
                        else _THEME["success"]
                        if not destructive and not hovered
                        else mode_accent
                        if hovered
                        else _THEME["border_subtle"]
                    )
                    _draw_editor_panel(
                        bx0,
                        by0,
                        bx1 - bx0,
                        by1 - by0,
                        fill,
                        border=border,
                        radius=6.0 * scale,
                    )
                    _draw_editor_text(
                        label,
                        bx0 + 8.0 * scale,
                        by0 + 7.0 * scale,
                        10.0,
                        _THEME["text"],
                    )
            else:
                _draw_editor_text(
                    "Arrows adjust · Enter commit · Esc cancel",
                    x0 + 12.0 * scale,
                    y0 + 19.0 * scale,
                    9.0,
                    _THEME["text_muted"],
                )
            _draw_editor_tool_option_hint(editor, context, entries, scale)
            return
        if mode == "SELECT":
            for action in (
                "TOOL_SELECT_ALL",
                "TOOL_CLEAR_SELECTION",
                "TOOL_COPY_SELECTION",
                "TOOL_CUT_SELECTION",
            ):
                label, bounds = entries[action]
                bx0, by0, bx1, by1 = bounds
                hovered = action == getattr(editor, "tool_options_hover", None)
                destructive = action == "TOOL_CUT_SELECTION"
                fill = _THEME["danger_fill"] if destructive else _THEME["panel_raised"]
                if hovered:
                    fill = _THEME["panel_hover"]
                border = (
                    _THEME["danger"]
                    if destructive and not hovered
                    else mode_accent
                    if hovered
                    else _THEME["border_subtle"]
                )
                _draw_editor_panel(
                    bx0,
                    by0,
                    bx1 - bx0,
                    by1 - by0,
                    fill,
                    border=border,
                    radius=6.0 * scale,
                )
                _draw_editor_text(
                    label,
                    bx0 + 8.0 * scale,
                    by0 + 7.0 * scale,
                    10.0,
                    _THEME["text"],
                )
            _draw_editor_text(
                "Ctrl+A all · Ctrl+C copy · Ctrl+D clear",
                x0 + 12.0 * scale,
                y0 + 19.0 * scale,
                9.0,
                _THEME["text_muted"],
            )
            _draw_editor_tool_option_hint(editor, context, entries, scale)
            return
        _draw_editor_text(
            "SIZE",
            x0 + 12.0 * scale,
            y0 + 91.0 * scale,
            9.0,
            _THEME["text_dim"],
        )
        radius_value = str(int(getattr(scene, "voxtart_editor_radius", 1)))
        _radius_label, radius_bounds = entries["TOOL_RADIUS_DOWN"]
        for action in ("TOOL_RADIUS_DOWN", "TOOL_RADIUS_UP", "TOOL_HOLLOW"):
            label, bounds = entries[action]
            bx0, by0, bx1, by1 = bounds
            hovered = action == getattr(editor, "tool_options_hover", None)
            active = active_action(action)
            fill = _THEME["accent_fill"] if active else _THEME["panel_raised"]
            if hovered:
                fill = _THEME["panel_hover"]
            border = mode_accent if active or hovered else _THEME["border_subtle"]
            _draw_editor_panel(
                bx0,
                by0,
                bx1 - bx0,
                by1 - by0,
                fill,
                border=border,
                radius=6.0 * scale,
            )
            _draw_editor_text(
                label,
                bx0 + 8.0 * scale,
                by0 + 7.0 * scale,
                11.0,
                _THEME["text"],
            )
        _draw_editor_text(
            radius_value,
            radius_bounds[2] + 10.0 * scale,
            y0 + 91.0 * scale,
            11.0,
            _THEME["text"],
        )
        _draw_editor_text(
            "MIRROR",
            x0 + 12.0 * scale,
            y0 + 59.0 * scale,
            9.0,
            _THEME["text_dim"],
        )
        for action in ("TOOL_MIRROR_X", "TOOL_MIRROR_Y", "TOOL_MIRROR_Z"):
            label, bounds = entries[action]
            bx0, by0, bx1, by1 = bounds
            hovered = action == getattr(editor, "tool_options_hover", None)
            active = active_action(action)
            fill = _THEME["accent_fill"] if active else _THEME["panel_raised"]
            if hovered:
                fill = _THEME["panel_hover"]
            _draw_editor_panel(
                bx0,
                by0,
                bx1 - bx0,
                by1 - by0,
                fill,
                border=mode_accent if active or hovered else _THEME["border_subtle"],
                radius=6.0 * scale,
            )
            _draw_editor_text(
                label,
                bx0 + 14.0 * scale,
                by0 + 7.0 * scale,
                10.0,
                _THEME["text"],
            )
        detail_y = y0 + 20.0 * scale
        if shape == "CYLINDER":
            _draw_editor_text("AXIS", x0 + 12.0 * scale, detail_y + 7.0 * scale, 9.0, _THEME["text_dim"])
            for action in ("TOOL_AXIS_X", "TOOL_AXIS_Y", "TOOL_AXIS_Z"):
                label, bounds = entries[action]
                bx0, by0, bx1, by1 = bounds
                hovered = action == getattr(editor, "tool_options_hover", None)
                active = active_action(action)
                _draw_editor_panel(
                    bx0,
                    by0,
                    bx1 - bx0,
                    by1 - by0,
                    _THEME["accent_fill"] if active else _THEME["panel_raised"],
                    border=mode_accent if active or hovered else _THEME["border_subtle"],
                    radius=6.0 * scale,
                )
                _draw_editor_text(label, bx0 + 12.0 * scale, by0 + 7.0 * scale, 10.0, _THEME["text"])
            _draw_editor_text(
                f"H {int(scene.voxtart_editor_height)}",
                x0 + 192.0 * scale,
                detail_y + 7.0 * scale,
                9.0,
                _THEME["text_muted"],
            )
            for action in ("TOOL_HEIGHT_DOWN", "TOOL_HEIGHT_UP"):
                label, bounds = entries[action]
                bx0, by0, bx1, by1 = bounds
                hovered = action == getattr(editor, "tool_options_hover", None)
                _draw_editor_panel(
                    bx0,
                    by0,
                    bx1 - bx0,
                    by1 - by0,
                    _THEME["panel_hover"] if hovered else _THEME["panel_raised"],
                    border=mode_accent if hovered else _THEME["border_subtle"],
                    radius=6.0 * scale,
                )
                _draw_editor_text(label, bx0 + 7.0 * scale, by0 + 7.0 * scale, 10.0, _THEME["text"])
        elif shape == "FACE" or mode == "ATTACH":
            _draw_editor_text(
                f"DEPTH {int(scene.voxtart_editor_depth)}",
                x0 + 12.0 * scale,
                detail_y + 7.0 * scale,
                9.0,
                _THEME["text_dim"],
            )
            for action in ("TOOL_DEPTH_DOWN", "TOOL_DEPTH_UP"):
                label, bounds = entries[action]
                bx0, by0, bx1, by1 = bounds
                hovered = action == getattr(editor, "tool_options_hover", None)
                _draw_editor_panel(
                    bx0,
                    by0,
                    bx1 - bx0,
                    by1 - by0,
                    _THEME["panel_hover"] if hovered else _THEME["panel_raised"],
                    border=mode_accent if hovered else _THEME["border_subtle"],
                    radius=6.0 * scale,
                )
                _draw_editor_text(label, bx0 + 8.0 * scale, by0 + 7.0 * scale, 10.0, _THEME["text"])
        else:
            _draw_editor_text(
                "V Voxel · L Line · O Box · C Sphere",
                x0 + 12.0 * scale,
                detail_y + 7.0 * scale,
                9.0,
                _THEME["text_muted"],
            )
        _draw_editor_tool_option_hint(editor, context, entries, scale)
    finally:
        gpu.state.blend_set("NONE")


def _toggle_view_projection(context) -> str | None:
    area = getattr(context, "area", None)
    space = getattr(getattr(area, "spaces", None), "active", None)
    region_3d = getattr(space, "region_3d", None)
    if region_3d is None:
        return None
    try:
        current = str(getattr(region_3d, "view_perspective", "PERSP"))
        target = "ORTHO" if current != "ORTHO" else "PERSP"
        region_3d.view_perspective = target
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return "Orthographic view" if target == "ORTHO" else "Perspective view"


def _view_shortcut(context, event) -> str | None:
    if getattr(event, "value", None) != "PRESS" or getattr(event, "ctrl", False) or getattr(event, "alt", False):
        return None
    command = {
        "HOME": ("view_selected", "View recentered"),
        "NUMPAD1": ("front", "Front view"),
        "NUMPAD3": ("right", "Right view"),
        "NUMPAD5": ("projection", None),
        "NUMPAD7": ("top", "Top view"),
    }.get(getattr(event, "type", ""))
    if command is None:
        return None
    if command[0] == "projection":
        return _toggle_view_projection(context)
    try:
        if command[0] == "view_selected":
            bpy.ops.view3d.view_selected()
        else:
            bpy.ops.view3d.view_axis(type=command[0].upper())
    except (AttributeError, RuntimeError):
        return None
    return command[1]


_CUBE_EDGE_PAIRS = (
    (0, 1), (1, 2), (2, 3), (3, 0),
    (4, 5), (5, 6), (6, 7), (7, 4),
    (0, 4), (1, 5), (2, 6), (3, 7),
)
_CUBE_FACE_TRIANGLES = (
    (0, 1, 3), (0, 3, 2),
    (4, 5, 7), (4, 7, 6),
    (0, 1, 5), (0, 5, 4),
    (2, 3, 7), (2, 7, 6),
    (0, 2, 6), (0, 6, 4),
    (1, 3, 7), (1, 7, 5),
)




def _cube_corners(cell: Cell, grid_size: float):
    return tuple(
        Vector(
            (
                (cell[0] + (corner & 1)) * grid_size,
                (cell[1] + ((corner >> 1) & 1)) * grid_size,
                (cell[2] + ((corner >> 2) & 1)) * grid_size,
            )
        )
        for corner in range(8)
    )

EDITOR_GRID_MARGIN = 1


def _editor_canvas_bounds(editor):
    grid_size = max(float(editor.asset.grid_size), 0.0001)
    bounds = getattr(editor.asset, "bounds", None)
    if bounds is None:
        minimum = (-1, -1, -1)
        maximum = (2, 2, 2)
    else:
        occupied_minimum, occupied_maximum = bounds
        minimum = tuple(
            int(occupied_minimum[axis]) - EDITOR_GRID_MARGIN
            for axis in range(3)
        )
        maximum = tuple(
            int(occupied_maximum[axis]) + 1 + EDITOR_GRID_MARGIN
            for axis in range(3)
        )
    return minimum, maximum, grid_size


def _editor_grid_vertices(editor):
    """Return the bounded wire box with one line per voxel interval."""

    minimum, maximum, grid_size = _editor_canvas_bounds(editor)
    matrix = editor.obj.matrix_world
    spans = tuple(maximum[axis] - minimum[axis] for axis in range(3))
    cache_key = (
        minimum,
        maximum,
        grid_size,
        tuple(float(value) for row in matrix for value in row),
    )
    cached = getattr(editor, "_grid_vertices_cache", None)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    interior = []
    border = []

    def world_point(cell):
        return matrix @ Vector(tuple(value * grid_size for value in cell))

    for axis in range(3):
        other_axes = tuple(index for index in range(3) if index != axis)
        for first in range(spans[other_axes[0]] + 1):
            for second in range(spans[other_axes[1]] + 1):
                start = list(minimum)
                end = list(minimum)
                start[axis] = minimum[axis]
                end[axis] = maximum[axis]
                start[other_axes[0]] += first
                end[other_axes[0]] += first
                start[other_axes[1]] += second
                end[other_axes[1]] += second
                target = (
                    border
                    if first in {0, spans[other_axes[0]]}
                    and second in {0, spans[other_axes[1]]}
                    else interior
                )
                target.extend((world_point(start), world_point(end)))
    result = (tuple(interior), tuple(border))
    try:
        editor._grid_vertices_cache = (cache_key, result)
    except (AttributeError, TypeError):
        pass
    return result


def _draw_editor_grid(editor) -> None:
    if not bool(editor.scene.get(EDITOR_GRID_PROPERTY)):
        return
    interior, border = _editor_grid_vertices(editor)
    _draw_gpu_lines(
        editor,
        interior,
        (0.30, 0.44, 0.60, 0.24),
        width=1.0,
    )
    _draw_gpu_lines(
        editor,
        border,
        (0.42, 0.68, 0.92, 0.62),
        width=1.7,
    )


def _draw_gpu_faces(editor, vertices, color) -> None:
    if gpu is None or batch_for_shader is None or not vertices:
        return
    depth_disabled = False
    try:
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "TRIS", {"pos": vertices})
        shader.bind()
        shader.uniform_float("color", color)
        gpu.state.blend_set("ALPHA")
        try:
            gpu.state.depth_test_set("NONE")
            depth_disabled = True
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        batch.draw(shader)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    finally:
        try:
            if depth_disabled:
                gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.blend_set("NONE")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass


def _draw_gpu_lines(editor, vertices, color, *, width=2.0, always_on_top=False) -> None:
    if gpu is None or batch_for_shader is None or not vertices:
        return
    depth_disabled = False
    try:
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINES", {"pos": vertices})
        shader.bind()
        shader.uniform_float("color", color)
        gpu.state.blend_set("ALPHA")
        if always_on_top:
            try:
                gpu.state.depth_test_set("NONE")
                depth_disabled = True
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        gpu.state.line_width_set(width)
        batch.draw(shader)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    finally:
        try:
            if depth_disabled:
                gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass




def _draw_editor_preview(editor, _context) -> None:
    if gpu is None or batch_for_shader is None:
        return
    try:
        _draw_editor_grid(editor)
        matrix = editor.obj.matrix_world
        grid_size = editor.asset.grid_size
        preview_vertices = []
        preview_faces = []
        for cell in editor.preview_cells:
            corners = _cube_corners(cell, grid_size)
            world_corners = tuple(matrix @ corner for corner in corners)
            for start, end in _CUBE_EDGE_PAIRS:
                preview_vertices.extend((world_corners[start], world_corners[end]))
            for first, second, third in _CUBE_FACE_TRIANGLES:
                preview_faces.extend(
                    (world_corners[first], world_corners[second], world_corners[third])
                )
        mode = _editor_input_mode(editor)
        preview_color = {
            "ERASE": (1.0, 0.18, 0.16, 0.85),
            "PAINT": (1.0, 0.75, 0.16, 0.9),
            "SELECT": (0.24, 0.75, 1.0, 0.9),
        }.get(mode, (0.24, 1.0, 0.48, 0.9))
        fill_color = (*preview_color[:3], 0.32)
        _draw_gpu_faces(editor, preview_faces, fill_color)
        _draw_gpu_lines(
            editor,
            preview_vertices,
            preview_color,
            width=2.0,
            always_on_top=True,
        )

        hit = editor.last_hit
        if hit is not None:
            hit_corners = _cube_corners(hit.cell, grid_size)
            hit_vertices = []
            for start, end in _CUBE_EDGE_PAIRS:
                hit_vertices.extend((matrix @ hit_corners[start], matrix @ hit_corners[end]))
            _draw_gpu_lines(
                editor,
                hit_vertices,
                (1.0, 1.0, 1.0, 0.95),
                width=3.0,
                always_on_top=True,
            )

            normal = Vector(hit.normal)
            normal_axis = next((axis for axis, value in enumerate(hit.normal) if value), None)
            if normal_axis is not None:
                tangent_axes = [axis for axis in range(3) if axis != normal_axis]
                face_origin = [float(value) + 0.5 for value in hit.cell]
                face_origin[normal_axis] = float(hit.cell[normal_axis] + (1 if hit.normal[normal_axis] > 0 else 0))
                face_vertices = []
                for corner in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    point = list(face_origin)
                    point[tangent_axes[0]] = hit.cell[tangent_axes[0]] + corner[0]
                    point[tangent_axes[1]] = hit.cell[tangent_axes[1]] + corner[1]
                    face_vertices.append(matrix @ Vector(tuple(axis * grid_size for axis in point)))
                _draw_gpu_faces(
                    editor,
                    (
                        face_vertices[0],
                        face_vertices[1],
                        face_vertices[2],
                        face_vertices[0],
                        face_vertices[2],
                        face_vertices[3],
                    ),
                    (1.0, 0.8, 0.08, 0.34),
                )
                _draw_gpu_lines(
                    editor,
                    tuple(
                        vertex
                        for index in range(4)
                        for vertex in (face_vertices[index], face_vertices[(index + 1) % 4])
                    ),
                    (1.0, 0.9, 0.2, 0.95),
                    width=3.0,
                    always_on_top=True,
                )
                center = matrix @ Vector(
                    tuple(
                        (hit.cell[axis] + 0.5) * grid_size
                        for axis in range(3)
                    )
                )
                face_center = center + matrix.to_3x3() @ (normal * (0.5 * grid_size))
                _draw_gpu_lines(
                    editor,
                    (face_center, face_center + matrix.to_3x3() @ (normal * grid_size)),
                    (1.0, 0.2, 0.2, 1.0),
                    width=4.0,
                    always_on_top=True,
                )

        bounds = editor.asset.bounds
        if bounds is not None and any(editor.mirrors):
            minimum, maximum = bounds
            minimum = tuple(value * grid_size for value in minimum)
            maximum = tuple((value + 1) * grid_size for value in maximum)
            for axis, enabled in enumerate(editor.mirrors):
                if not enabled:
                    continue
                other = [value for value in range(3) if value != axis]
                midpoint = (minimum[axis] + maximum[axis]) * 0.5
                plane_vertices = []
                for corner in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    point = [0.0, 0.0, 0.0]
                    point[axis] = midpoint
                    point[other[0]] = minimum[other[0]] if corner[0] == 0 else maximum[other[0]]
                    point[other[1]] = minimum[other[1]] if corner[1] == 0 else maximum[other[1]]
                    plane_vertices.append(matrix @ Vector(tuple(point)))
                _draw_gpu_lines(
                    editor,
                    tuple(vertex for index in range(4) for vertex in (plane_vertices[index], plane_vertices[(index + 1) % 4])),
                    (0.35, 0.65, 1.0, 0.7),
                    width=1.5,
                )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return
def _draw_editor_help(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None:
        return
    scale = ui_style.ui_scale()
    width = float(region.width)
    height = float(region.height)
    panel_width = min(390.0 * scale, max(300.0 * scale, width - 112.0 * scale))
    panel_height = min(220.0 * scale, max(196.0 * scale, height - 96.0 * scale))
    panel_x = min(84.0 * scale, max(16.0 * scale, width - panel_width - 16.0 * scale))
    panel_y = 96.0 * scale
    if panel_y + panel_height > height - 40.0 * scale:
        panel_y = max(40.0 * scale, height - panel_height - 40.0 * scale)
    _draw_editor_panel(
        panel_x,
        panel_y,
        panel_width,
        panel_height,
        _THEME["panel"],
        border=_THEME["accent"],
        radius=12.0 * scale,
    )
    top = panel_y + panel_height
    _draw_editor_text(
        "HOW TO BUILD",
        panel_x + 16.0 * scale,
        top - 28.0 * scale,
        13.0,
        _THEME["accent"],
    )
    _draw_editor_text(
        "Start simple. You can undo every change.",
        panel_x + 16.0 * scale,
        top - 49.0 * scale,
        11.0,
        _THEME["text_muted"],
    )
    rows = (
        ("Click a block", "add one"),
        ("Right-click", "remove one"),
        ("Drag over blocks", "color them"),
        ("Middle mouse", "look around"),
    )
    row_y = top - 82.0 * scale
    for shortcut, description in rows:
        _draw_editor_text(
            shortcut,
            panel_x + 16.0 * scale,
            row_y,
            11.0,
            _THEME["text"],
        )
        _draw_editor_text(
            description,
            panel_x + 164.0 * scale,
            row_y,
            11.0,
            _THEME["text_muted"],
        )
        row_y -= 22.0 * scale
    _draw_editor_text(
        "Changed your mind? Use Undo.",
        panel_x + 16.0 * scale,
        panel_y + 14.0 * scale,
        11.0,
        _THEME["text_dim"],
    )




def _draw_editor_chat_prompt(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None:
        return
    from . import mcp

    scale = ui_style.ui_scale()
    layout = mcp.chat_layout(float(region.width), float(region.height), scale=scale)
    panel_x, panel_y, panel_right, panel_top = layout["panel"]
    panel_width = panel_right - panel_x
    _draw_editor_panel(
        panel_x,
        panel_y,
        panel_width,
        panel_top - panel_y,
        _THEME["panel"],
        border=_THEME["accent"] if mcp.chat_input_focused() else _THEME["border"],
        radius=12.0 * scale,
    )
    model_label = mcp.chat_model_label()
    status = str(getattr(mcp._window_manager(), "voxtart_chat_status", "Ready"))[:42]
    _draw_editor_text("ASK VOXTART", panel_x + 12.0 * scale, panel_top - 17.0 * scale, 9.0, _THEME["accent"])
    status_x = panel_right - _editor_text_width(status, 9.0) - 12.0 * scale
    _draw_editor_text(
        status,
        max(panel_x + 108.0 * scale, status_x),
        panel_top - 17.0 * scale,
        9.0,
        _THEME["text_muted"],
    )
    _draw_editor_text(model_label, panel_x + 12.0 * scale, panel_top - 31.0 * scale, 9.0, _THEME["text_muted"])

    def draw_button(action: str, label: str, icon: str | None = None, *, accent: bool = False) -> None:
        x0, y0, x1, y1 = layout[action]
        hovered = getattr(editor, "chat_hover", None) == action
        fill = (
            _THEME["accent"]
            if accent and hovered
            else _THEME["accent_fill"]
            if accent
            else _THEME["panel_hover"]
            if hovered
            else _THEME["panel_raised"]
        )
        border = _THEME["accent"] if accent or hovered else _THEME["border_subtle"]
        _draw_editor_panel(
            x0,
            y0,
            x1 - x0,
            y1 - y0,
            fill,
            border=border,
            radius=8.0 * scale,
        )
        if icon:
            _draw_editor_icon(icon, x0 + 17.0 * scale, (y0 + y1) * 0.5, 16.0 * scale, _THEME["text"])
            label_x = x0 + 31.0 * scale
        else:
            label_x = x0 + 12.0 * scale
        _draw_editor_text(label, label_x, y0 + 14.0 * scale, 11.0, _THEME["text"])

    draw_button("chat:image", "Image", "image", accent=bool(mcp.chat_attachment_names()))
    field_x, field_y, field_right, field_top = layout["chat:field"]
    focused = mcp.chat_input_focused()
    _draw_editor_panel(
        field_x,
        field_y,
        field_right - field_x,
        field_top - field_y,
        _THEME["panel_hover"] if focused else _THEME["canvas"],
        border=_THEME["accent"] if focused else _THEME["border_subtle"],
        radius=8.0 * scale,
    )
    prompt = str(getattr(mcp._window_manager(), "voxtart_chat_prompt", "")).strip()
    attachment_label = mcp.chat_attachment_label()
    display = prompt
    if attachment_label:
        display = f"{attachment_label}  ·  {display}" if display else attachment_label
    text_color = _THEME["text"]
    if not display:
        display = "Describe what to make, edit, or inspect…"
        text_color = _THEME["text_muted"]
    if len(display) > 82:
        display = f"…{display[-81:]}"
    _draw_editor_text(display, field_x + 12.0 * scale, field_y + 14.0 * scale, 11.0, text_color)
    if focused:
        caret_x = min(field_right - 10.0 * scale, field_x + 12.0 * scale + _editor_text_width(display, 11.0) + 2.0 * scale)
        _draw_editor_rect(
            caret_x,
            field_y + 10.0 * scale,
            1.2 * scale,
            19.0 * scale,
            _THEME["accent"],
        )
    draw_button("chat:send", "Send", accent=True)


def _draw_editor_overlay(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None:
        return
    scale = ui_style.ui_scale()
    scene = context.scene
    mode = _editor_input_mode(editor)
    shape = _scene_shape(scene)
    first_run = bool(scene.get(FIRST_RUN_HINT_PROPERTY))
    show_help = bool(scene.get(EDITOR_HELP_PROPERTY))
    details = bool(scene.get(EDITOR_DETAILS_PROPERTY))

    hit = editor.last_hit.cell if editor.last_hit is not None else "--"
    active_layer = getattr(editor.stack, "active", None)
    layer_name = active_layer.name if active_layer is not None else "--"
    layer_lock = " · Locked" if active_layer is not None and active_layer.locked else ""
    mode_label = {
        "ADD": "Add blocks",
        "ATTACH": "Add blocks",
        "ERASE": "Erase blocks",
        "PAINT": "Color blocks",
        "SELECT": "Select blocks",
    }.get(mode, mode.title())
    _draw_editor_topbar(editor, context)
    _draw_editor_options_menu(editor, context)
    _draw_editor_dock_panels(editor, context)
    _draw_editor_palette(editor, context)
    _draw_editor_dock_tooltip(editor, context)
    _draw_editor_view_hud(editor, context)
    _draw_editor_toolbar(editor, context)
    _draw_editor_tool_options(editor, context)
    _draw_editor_topbar_tooltip(editor, context)
    _draw_editor_chat_prompt(editor, context)
    context_label = f"{mode_label}  ·  {shape.title()} brush" if details else mode_label
    context_shortcut = {
        "ADD": "B",
        "ATTACH": "T",
        "ERASE": "E",
        "PAINT": "P",
        "SELECT": "S",
    }.get(mode, "")
    if details or bool(scene.get(EDITOR_ADVANCED_PROPERTY)):
        context_fill, context_accent = {
            "ADD": (_THEME["success_fill"], _THEME["success"]),
            "ATTACH": (_THEME["accent_soft"], _THEME["accent"]),
            "ERASE": (_THEME["danger_fill"], _THEME["danger"]),
            "PAINT": (_THEME["warning_fill"], _THEME["warning"]),
            "SELECT": (_THEME["accent_soft"], _THEME["accent_hover"]),
        }.get(mode, (_THEME["panel"], _THEME["accent"]))
        context_x = 84.0 * scale
        context_y = float(region.height) - 84.0 * scale
        context_width = min(
            520.0 * scale,
            max(230.0 * scale, _editor_text_width(context_label, 12.0) + 66.0 * scale),
        )
        _draw_editor_panel(
            context_x,
            context_y,
            context_width,
            28.0 * scale,
            context_fill,
            border=context_accent,
            radius=14.0 * scale,
        )
        _draw_editor_round_rect(
            context_x + 11.0 * scale,
            context_y + 11.0 * scale,
            6.0 * scale,
            6.0 * scale,
            context_accent,
            radius=3.0 * scale,
        )
        _draw_editor_text(
            context_label,
            context_x + 26.0 * scale,
            context_y + 9.0 * scale,
            12.0,
            _THEME["text"],
        )
        _draw_editor_text(
            context_shortcut,
            context_x + context_width - 22.0 * scale,
            context_y + 9.0 * scale,
            10.0,
            _THEME["text_muted"],
        )
    if first_run and not show_help:
        card_x = 84.0 * scale
        card_y = max(100.0 * scale, float(region.height) - 200.0 * scale)
        card_width = max(320.0 * scale, min(560.0 * scale, float(region.width) - card_x - 24.0 * scale))
        card_height = 110.0 * scale
        _draw_editor_panel(
            card_x,
            card_y,
            card_width,
            card_height,
            _THEME["panel"],
            border=_THEME["accent_soft"],
            radius=12.0 * scale,
        )
        _draw_editor_text(
            "START HERE",
            card_x + 14.0 * scale,
            card_y + 88.0 * scale,
            11.0,
            _THEME["accent"],
        )
        for offset, line in enumerate(
            (
                "1  Pick a color",
                "2  Click a block to add one",
                "3  Right-click to remove one",
            )
        ):
            _draw_editor_text(
                line,
                card_x + 14.0 * scale,
                card_y + 68.0 * scale - offset * 19.0 * scale,
                11.0,
                _THEME["text"],
            )
        _draw_editor_text(
            "Oops? Use Undo.",
            card_x + 14.0 * scale,
            card_y + 12.0 * scale,
            10.0,
            _THEME["text_muted"],
        )
    if show_help:
        _draw_editor_help(editor, context)
    if bool(scene.get(EDITOR_DETAILS_PROPERTY)):
        detail_summary = (
            f"Block {hit}  ·  Preview {len(editor.preview_cells)}"
            f"  ·  Selected {len(editor.selection)}"
        )
        detail_layer = f"Layer {layer_name}{layer_lock}"
        detail_width = min(
            760.0 * scale,
            max(300.0 * scale, _editor_text_width(detail_summary, 11.0) + 32.0 * scale),
        )
        _draw_editor_panel(
            14.0 * scale,
            36.0 * scale,
            detail_width,
            40.0 * scale,
            _THEME["panel"],
            border=_THEME["border"],
            radius=10.0 * scale,
        )
        _draw_editor_text(
            detail_summary,
            28.0 * scale,
            57.0 * scale,
            11.0,
            _THEME["text"],
        )
        _draw_editor_text(
            detail_layer,
            28.0 * scale,
            41.0 * scale,
            11.0,
            _THEME["text_muted"],
        )
    status = str(getattr(editor, "status", "Ready") or "Ready")
    status_display = status if len(status) <= 72 else f"{status[:69]}..."
    status_lower = status_display.lower()
    if first_run and not show_help and status_lower.startswith("ready"):
        return
    if any(
        fragment in status_lower
        for fragment in ("locked", "invalid", "nothing", "empty", "aim at", "cancelled", "discarded", "no block")
    ):
        status_fill = _THEME["danger_fill"]
        status_border = _THEME["danger"]
        status_accent = _THEME["danger"]
        status_text = (1.0, 0.9, 0.88, 1.0)
    elif any(
        fragment in status_lower
        for fragment in (
            "added ",
            "painted ",
            "removed ",
            "selected ",
            "saved",
            "recovered",
            "pasted",
            "committed",
            "undid",
            "redid",
            "history restored",
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
    status_width = min(
        620.0 * scale,
        max(176.0 * scale, float(region.width) - 28.0 * scale),
        max(176.0 * scale, _editor_text_width(status_display, 11.0) + 42.0 * scale),
    )
    _draw_editor_panel(
        14.0 * scale,
        8.0 * scale,
        status_width,
        24.0 * scale,
        status_fill,
        border=status_border,
        radius=12.0 * scale,
    )
    _draw_editor_round_rect(23.0 * scale, 17.0 * scale, 6.0 * scale, 6.0 * scale, status_accent, radius=3.0 * scale)
    _draw_editor_text(status_display, 38.0 * scale, 15.0 * scale, 11.0, status_text)
    _draw_editor_menu(editor, context)


class VOXTART_OT_voxel_editor(bpy.types.Operator):
    """Open a MagicaVoxel-inspired interactive voxel editing session."""

    bl_idname = "voxtart.voxel_editor"
    bl_label = "Open Voxel Editor"
    bl_description = "Edit the active VOXTART asset with voxel brushes and shortcuts"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}
    @classmethod
    def poll(cls, context):
        area = getattr(context, "area", None)
        obj = getattr(context, "active_object", None)
        return (
            area is not None
            and area.type == "VIEW_3D"
            and getattr(context, "mode", "OBJECT") == "OBJECT"
            and obj is not None
            and ASSET_PROPERTY in obj
        )

    def _set_seam_overlay_visibility(self, visible: bool) -> None:
        overlay_name = self.obj.get(operators.SEAM_OVERLAY_PROPERTY)
        overlay = bpy.data.objects.get(overlay_name) if isinstance(overlay_name, str) else None
        if overlay is None:
            return
        try:
            overlay.hide_set(not visible)
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            overlay.hide_viewport = not visible
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _finish_handler(self) -> None:
        global _ACTIVE_EDITOR
        self._set_seam_overlay_visibility(
            not bool(getattr(self, "_seam_overlay_was_hidden", True))
        )
        for handle in (getattr(self, "_draw_handle", None), getattr(self, "_preview_handle", None)):
            if handle is not None:
                try:
                    bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
                except (ReferenceError, RuntimeError):
                    pass
        self._draw_handle = None
        self._preview_handle = None
        if _ACTIVE_EDITOR is self:
            _ACTIVE_EDITOR = None

    def _tag_redraw(self, context, *, force: bool = False) -> None:
        now = time.perf_counter()
        if not force and now - self._last_redraw < EDITOR_REDRAW_INTERVAL:
            return
        self._last_redraw = now
        area = getattr(context, "area", None)
        if area is not None:
            area.tag_redraw()

    def _update_working_state(self) -> None:
        self.stack.active.voxels = dict(self.layer_asset.voxels)
        self.asset.voxels = self.stack.composite(self.asset.palette)
        self._persist_recovery()

    def _sync(self, context, *, rebuild: bool = True) -> None:
        updater = getattr(self, "_update_working_state", None)
        if callable(updater):
            updater()
        else:
            self.stack.active.voxels = dict(self.layer_asset.voxels)
            self.asset.voxels = self.stack.composite(self.asset.palette)
        if rebuild:
            operators._replace_asset_mesh(self.obj, self.asset, layer_stack=self.stack)
        _restore_overlay(self.obj, self.asset)
        _request_redraw(self, context, force=True)

    def _load_state(self, context) -> None:
        self.asset = operators._load_asset(self.obj)
        self.stack = operators._load_layer_stack(self.obj, self.asset)
        self.asset.voxels = self.stack.composite(self.asset.palette)
        self.layer_asset = _layer_asset(self.asset, self.stack)
        try:
            self.selection = _selection(self.obj)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            if EDITOR_SELECTION_PROPERTY in self.obj:
                del self.obj[EDITOR_SELECTION_PROPERTY]
            self.selection = ()
            self.status = f"Invalid editor selection discarded: {error}"

    def _clear_recovery(self) -> None:
        if EDITOR_STROKE_PROPERTY in self.obj:
            del self.obj[EDITOR_STROKE_PROPERTY]

    def _persist_recovery(self, *, force: bool = False) -> None:
        if self.transaction is None:
            return
        now = time.perf_counter()
        last_write = float(getattr(self, "_last_recovery_write", 0.0))
        if not force and now - last_write < EDITOR_AUTOSAVE_INTERVAL:
            return
        payload = {
            "version": 1,
            "asset": self.asset.to_dict(),
            "layers": self.stack.encode(),
            "selection": [list(cell) for cell in self.selection],
            "location": [float(value) for value in self.obj.location],
            "rotation": [float(value) for value in self.obj.rotation_euler],
            "scale": [float(value) for value in self.obj.scale],
        }
        self.obj[EDITOR_STROKE_PROPERTY] = json.dumps(payload, separators=(",", ":"))
        self._last_recovery_write = now

    def _recover_interrupted_state(self) -> bool:
        raw = self.obj.get(EDITOR_STROKE_PROPERTY)
        if raw is None:
            return False
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(payload, dict) or int(payload.get("version", -1)) != 1:
                raise ValueError("unsupported recovery payload")
            asset = VoxelAsset.from_dict(payload["asset"])
            stack = operators.LayerStack.decode(payload["layers"])
            asset.voxels = stack.composite(asset.palette)
            selection = tuple(
                tuple(int(axis) for axis in cell)
                for cell in payload.get("selection", [])
            )
            if any(len(cell) != 3 for cell in selection):
                raise ValueError("invalid recovery selection")
            self.asset = asset
            self.stack = stack
            self.layer_asset = _layer_asset(asset, stack)
            self.selection = tuple(sorted(set(selection)))  # type: ignore[assignment]
            self.obj.location = tuple(float(value) for value in payload.get("location", self.obj.location))
            self.obj.rotation_euler = tuple(float(value) for value in payload.get("rotation", self.obj.rotation_euler))
            self.obj.scale = tuple(float(value) for value in payload.get("scale", self.obj.scale))
            _store_selection(self.obj, self.selection)
            operators._replace_asset_mesh(self.obj, self.asset, layer_stack=self.stack)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, IndexError):
            self._clear_recovery()
            self.status = "Interrupted stroke recovery discarded"
            return False
        self._clear_recovery()
        self.status = "Recovered interrupted stroke"
        return True

    def _load_clipboard(self) -> None:
        global _CLIPBOARD
        raw = self.obj.get(EDITOR_CLIPBOARD_PROPERTY)
        if raw is None:
            raw = self.scene.get(EDITOR_CLIPBOARD_PROPERTY)
        if raw is None:
            return
        try:
            _CLIPBOARD = _decode_clipboard(raw, len(self.asset.palette))
        except (TypeError, ValueError, json.JSONDecodeError):
            _CLIPBOARD = ()
            if EDITOR_CLIPBOARD_PROPERTY in self.obj:
                del self.obj[EDITOR_CLIPBOARD_PROPERTY]
            if EDITOR_CLIPBOARD_PROPERTY in self.scene:
                del self.scene[EDITOR_CLIPBOARD_PROPERTY]
            self.status = "Invalid clipboard discarded"

    def _record_history(self, label: str = "Edit") -> None:
        self.history = self.history[: self.history_index + 1]
        self.history.append(
            _copy_history(
                self.asset,
                self.stack,
                self.selection,
                self.obj,
                label=label,
            )
        )
        self.history_index = len(self.history) - 1


    def _restore_history(self, context, index: int) -> None:
        if not 0 <= index < len(self.history):
            return
        state = self.history[index]
        self.asset = VoxelAsset.from_dict(state.asset)
        self.stack = operators.LayerStack.decode(state.layers)
        self.layer_asset = _layer_asset(self.asset, self.stack)
        self.selection = state.selection
        self.obj.location = state.location
        self.obj.rotation_euler = state.rotation
        self.obj.scale = state.scale
        _store_selection(self.obj, self.selection)
        self._sync(context)
        self.history_index = index
        self.status = f"History restored · {getattr(state, 'label', 'Edit')}"

    def _begin_stroke(self, operation: str | None = None) -> None:
        if self.stack.active.locked:
            self.status = f"Active layer locked: {self.stack.active.name}"
            return
        if self.transaction is not None:
            return
        self.stroke_asset = self.asset.to_dict()
        self.stroke_layers = self.stack.encode()
        self.stroke_selection = self.selection
        self.layer_asset = _layer_asset(self.asset, self.stack)
        self.transaction = VoxelEditTransaction(
            self.layer_asset,
            protected=operators._protected_cells(self.obj),
            allow_protected=bool(self.scene.voxtart_edit_allow_protected),
            operation=operation or f"editor_{_scene_mode(self.scene).lower()}",
        )
        self.stroke_anchor = None
        self.stroke_cursor_cell = None
        self._last_stroke_sync = 0.0
        self._last_recovery_write = 0.0
        persist = getattr(self, "_persist_recovery", None)
        if callable(persist):
            persist(force=True)

    def _cancel_stroke(self, context) -> None:
        self.drag_mode = None
        if self.transaction is None:
            return
        self.transaction.rollback()
        self.asset = VoxelAsset.from_dict(self.stroke_asset)
        self.stack = operators.LayerStack.decode(self.stroke_layers)
        self.layer_asset = _layer_asset(self.asset, self.stack)
        self.selection = self.stroke_selection
        _store_selection(self.obj, self.selection)
        self._sync(context, rebuild=True)
        self.transaction = None
        self.stroke_anchor = None
        self.stroke_cursor_cell = None
        self._last_stroke_sync = 0.0
        self.preview_cells = ()
        self.status = "Stroke cancelled"
        clear = getattr(self, "_clear_recovery", None)
        if callable(clear):
            clear()

    def _commit_stroke(self, context) -> None:
        if self.transaction is None:
            self.drag_mode = None
            return
        report = self.transaction.commit()
        self.stack.active.voxels = dict(self.layer_asset.voxels)
        self._sync(context, rebuild=report.changed)
        self.transaction = None
        clear = getattr(self, "_clear_recovery", None)
        if callable(clear):
            clear()
        self.stroke_anchor = None
        self.stroke_cursor_cell = None
        self._last_stroke_sync = 0.0
        self.preview_cells = ()
        if report.changed:
            self._record_history(_history_label(report))
            self.scene[FIRST_RUN_HINT_PROPERTY] = False
            changed = report.added + report.removed + report.painted
            unit = "block" if changed == 1 else "blocks"
            verb = {
                "editor_add": "Added",
                "editor_attach": "Added",
                "editor_erase": "Erased",
                "editor_paint": "Colored",
            }.get(report.operation, "Updated")
            self.status = f"{verb} {changed} {unit} · Ctrl+Z to undo"
        else:
            self.status = "No block changed"

    def _grid_hit(self, context, event) -> RayHit | None:
        if context.region is None or context.region_data is None:
            return None
        origin = view3d_utils.region_2d_to_origin_3d(
            context.region,
            context.region_data,
            (event.mouse_region_x, event.mouse_region_y),
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            context.region,
            context.region_data,
            (event.mouse_region_x, event.mouse_region_y),
        )
        try:
            inverse = self.obj.matrix_world.inverted()
            local_origin = inverse @ Vector(origin)
            local_direction = inverse.to_3x3() @ Vector(direction)
            if abs(local_direction.z) < 1.0e-9:
                return None
            distance = -local_origin.z / local_direction.z
            if distance < 0.0:
                return None
            point = local_origin + local_direction * distance
            grid = self.asset.grid_size
            cell = (math.floor(point.x / grid), math.floor(point.y / grid), 0)
            palette_index = int(self.scene.voxtart_edit_palette_index) % len(self.asset.palette)
            return RayHit(cell, palette_index, float(distance), (0, 0, 0))
        except (RuntimeError, TypeError, ValueError, ZeroDivisionError):
            return None

    def _ray_hit(self, context, event) -> RayHit | None:
        if context.region is None or context.region_data is None:
            return None
        origin = view3d_utils.region_2d_to_origin_3d(
            context.region,
            context.region_data,
            (event.mouse_region_x, event.mouse_region_y),
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            context.region,
            context.region_data,
            (event.mouse_region_x, event.mouse_region_y),
        )
        try:
            inverse = self.obj.matrix_world.inverted()
            local_origin = inverse @ Vector(origin)
            local_direction = inverse.to_3x3() @ Vector(direction)
            hit = raycast_voxel(self.asset, local_origin, local_direction)
            if hit is not None:
                return hit
            if _editor_input_mode(self) in {"ADD", "ATTACH"}:
                return self._grid_hit(context, event)
        except (RuntimeError, TypeError, ValueError):
            return None
        return None

    def _stamp(self, hit: RayHit, *, mode: str | None = None) -> tuple[Cell, ...]:
        mode = mode or _editor_input_mode(self)
        shape = _scene_shape(self.scene)
        radius = int(self.scene.voxtart_editor_radius)
        add_like = mode in {"ADD", "ATTACH"}
        has_normal = any(hit.normal)
        normal = hit.normal if has_normal else (0, 0, 1)
        center = hit.cell
        if add_like and has_normal:
            center = tuple(hit.cell[axis] + hit.normal[axis] for axis in range(3))  # type: ignore[assignment]
        if shape in EDITOR_SELECTION_SHAPES:
            shape = "VOXEL"
        if shape == "FACE" or mode == "ATTACH":
            return face_brush_cells(
                center,
                normal,
                radius,
                depth=int(getattr(self.scene, "voxtart_editor_depth", 1)),
                direction=1 if add_like else -1,
                hollow=bool(self.scene.voxtart_editor_hollow),
            )
        if shape == "LINE":
            if self.stroke_anchor is None:
                self.stroke_anchor = center
            cells = line_cells(self.stroke_anchor, center)
            return dilate_cells(cells, max(1, radius - 1)) if radius > 1 else cells
        if shape == "BOX":
            if self.stroke_anchor is None:
                self.stroke_anchor = center
            minimum = tuple(min(self.stroke_anchor[axis], center[axis]) for axis in range(3))
            maximum = tuple(max(self.stroke_anchor[axis], center[axis]) for axis in range(3))
            cells = box_cells(minimum, maximum, hollow=bool(self.scene.voxtart_editor_hollow))
            return dilate_cells(cells, radius - 1) if radius > 1 else cells
        if shape == "CYLINDER":
            return brush_cells(
                center,
                shape=shape,
                radius=radius,
                height=int(self.scene.voxtart_editor_height),
                hollow=bool(self.scene.voxtart_editor_hollow),
                axis=str(self.scene.voxtart_editor_axis),
            )
        if shape == "SPHERE" or radius > 1:
            return sphere_cells(center, radius, hollow=bool(self.scene.voxtart_editor_hollow))
        return brush_cells(center, shape="VOXEL")

    def _apply_cells(self, cells: tuple[Cell, ...], *, mode: str | None = None) -> None:
        if self.transaction is None:
            return
        flags = (
            bool(self.scene.voxtart_editor_mirror_x),
            bool(self.scene.voxtart_editor_mirror_y),
            bool(self.scene.voxtart_editor_mirror_z),
        )
        cells = mirror_cells(cells, self.asset.bounds, axes=flags)
        mode = mode or _editor_input_mode(self)
        palette_index = int(self.scene.voxtart_edit_palette_index) % len(self.layer_asset.palette)
        for cell in cells:
            if mode in {"ADD", "ATTACH"}:
                self.transaction.add(cell, palette_index, overwrite=False)
            elif mode == "ERASE":
                self.transaction.remove(cell)
            elif mode == "PAINT":
                if cell in self.layer_asset.voxels:
                    self.transaction.paint(cell, palette_index)
                elif cell in self.asset.voxels:
                    self.transaction.add(cell, palette_index, overwrite=True)

    def _reset_transform(self) -> None:
        self.transform_active = False
        self.transform_source = {}
        self.transform_current = {}
        self.transform_offset = (0, 0, 0)
        self.transform_turns = 0
        self.transform_axis = "Z"
        self.transform_kind = "MOVE"
        self.transform_duplicate = False

    def _begin_transform(self, kind: str = "MOVE", *, duplicate: bool = False) -> None:
        if _scene_mode(self.scene) != "SELECT" or not self.selection:
            self.status = "Select voxels before transforming"
            return
        if self.transaction is not None:
            self.status = "Finish the current stroke first"
            return
        self._begin_stroke(operation=f"editor_transform_{kind.lower()}")
        if self.transaction is None:
            return
        self.transform_source = {
            cell: self.layer_asset.voxels[cell]
            for cell in self.selection
            if cell in self.layer_asset.voxels
        }
        if not self.transform_source:
            self._cancel_stroke_without_context()
            self.status = "Selection is not on the active layer"
            return
        self.transform_active = True
        self.transform_current = {}
        self.transform_offset = (0, 0, 0)
        self.transform_turns = 0
        self.transform_axis = str(getattr(self.scene, "voxtart_editor_axis", "Z"))
        self.transform_kind = kind
        self.transform_duplicate = duplicate
        if not duplicate:
            for cell in self.transform_source:
                self.transaction.remove(cell)
        self._update_transform_pose()
        self.status = f"{'Duplicate' if duplicate else 'Move'} transform · arrows, Enter, Esc"

    def _cancel_stroke_without_context(self) -> None:
        if self.transaction is None:
            return
        self.transaction.rollback()
        self.transaction = None
        clear = getattr(self, "_clear_recovery", None)
        if callable(clear):
            clear()
        self._reset_transform()

    def _transform_pose(self) -> dict[Cell, int]:
        source = VoxelAsset(
            grid_size=self.layer_asset.grid_size,
            palette=list(self.layer_asset.palette),
            voxels=dict(self.transform_source),
            sockets=[],
            provenance=dict(self.layer_asset.provenance),
        )
        if self.transform_kind == "ROTATE":
            source = rotate_asset(
                source,
                self.transform_axis,
                (self.transform_turns % 4) * 90,
            )
        elif self.transform_kind == "MIRROR":
            source = mirror_asset(source, self.transform_axis)
        if self.transform_kind == "MOVE":
            return {
                tuple(cell[axis] + self.transform_offset[axis] for axis in range(3)): index
                for cell, index in source.voxels.items()
            }
        return dict(source.voxels)

    def _update_transform_pose(self) -> None:
        if self.transaction is None:
            return
        for cell in self.transform_current:
            self.transaction.remove(cell)
        desired = self._transform_pose()
        for cell, palette_index in desired.items():
            self.transaction.add(cell, palette_index, overwrite=True)
        self.transform_current = desired
        self.selection = tuple(sorted(desired))
        _store_selection(self.obj, self.selection)
        self._update_working_state()
        self.preview_cells = tuple(sorted(desired))

    def _transform_move(self, delta: tuple[int, int, int]) -> None:
        self.transform_offset = tuple(
            self.transform_offset[axis] + delta[axis] for axis in range(3)
        )
        self._update_transform_pose()
        self.status = f"Move offset {self.transform_offset} · Enter commit"

    def _transform_rotate(self, turns: int) -> None:
        self.transform_turns += turns
        self._update_transform_pose()
        self.status = f"Rotate {self.transform_turns * 90}° · Enter commit"

    def _commit_transform(self, context) -> None:
        if not self.transform_active:
            return
        self.selection = tuple(sorted(self.transform_current))
        _store_selection(self.obj, self.selection)
        self._commit_stroke(context)
        self._reset_transform()
        self.status = f"Transform committed · {len(self.selection)} voxel(s)"

    def _cancel_transform(self, context) -> None:
        if not self.transform_active:
            return
        self._cancel_stroke(context)
        self._reset_transform()
        self.status = "Transform cancelled"

    def _reset_scene_transform(self) -> None:
        self.scene_transform_active = False
        self.scene_transform_kind = "MOVE"
        self.scene_transform_snapshot = None

    def _begin_scene_transform(self, kind: str) -> None:
        if _scene_workspace(self.scene) != "SCENE":
            return
        if self.transaction is not None:
            self.status = "Finish the current voxel stroke first"
            return
        if self.scene_transform_active:
            return
        kind = str(kind).upper()
        if kind not in {"MOVE", "ROTATE", "SCALE"}:
            raise ValueError(f"unsupported scene transform: {kind!r}")
        self.scene_transform_active = True
        self.scene_transform_kind = kind
        self.scene_transform_snapshot = (
            tuple(float(value) for value in self.obj.location),
            tuple(float(value) for value in self.obj.rotation_euler),
            tuple(float(value) for value in self.obj.scale),
        )
        self.status = f"Scene {kind.lower()} · arrows, Enter, Esc"

    def _update_scene_transform(self, event) -> None:
        step = 5 if event.shift else 1
        if self.scene_transform_kind == "MOVE":
            distance = self.asset.grid_size * step
            deltas = {
                "LEFT_ARROW": (-distance, 0.0, 0.0),
                "RIGHT_ARROW": (distance, 0.0, 0.0),
                "UP_ARROW": (0.0, distance, 0.0),
                "DOWN_ARROW": (0.0, -distance, 0.0),
                "PAGE_UP": (0.0, 0.0, distance),
                "PAGE_DOWN": (0.0, 0.0, -distance),
            }
            delta = deltas.get(event.type)
            if delta is not None:
                self.obj.location = tuple(
                    self.obj.location[axis] + delta[axis] for axis in range(3)
                )
        elif self.scene_transform_kind == "ROTATE":
            turns = {"LEFT_ARROW": -1, "RIGHT_ARROW": 1, "UP_ARROW": -1, "DOWN_ARROW": 1}
            turn = turns.get(event.type)
            if turn is not None:
                axis = {"LEFT_ARROW": 2, "RIGHT_ARROW": 2, "UP_ARROW": 0, "DOWN_ARROW": 0}[event.type]
                self.obj.rotation_euler[axis] += math.radians(90.0 * turn)
        elif self.scene_transform_kind == "SCALE":
            delta = 0.1 * step
            if event.type in {"UP_ARROW", "RIGHT_ARROW", "PAGE_UP"}:
                factor = 1.0 + delta
            elif event.type in {"DOWN_ARROW", "LEFT_ARROW", "PAGE_DOWN"}:
                factor = max(0.1, 1.0 - delta)
            else:
                return
            self.obj.scale = tuple(max(0.01, value * factor) for value in self.obj.scale)
        else:
            return
        self.status = f"Scene {self.scene_transform_kind.lower()} · Enter commit"

    def _commit_scene_transform(self) -> None:
        if not self.scene_transform_active:
            return
        transform_label = {
            "MOVE": "Moved asset",
            "ROTATE": "Rotated asset",
            "SCALE": "Scaled asset",
        }.get(self.scene_transform_kind, "Transformed asset")
        self._record_history(transform_label)
        self._reset_scene_transform()
        self.status = f"Scene transform committed · {transform_label}"

    def _cancel_scene_transform(self) -> None:
        if not self.scene_transform_active:
            return
        if self.scene_transform_snapshot is not None:
            location, rotation, scale = self.scene_transform_snapshot
            self.obj.location = location
            self.obj.rotation_euler = rotation
            self.obj.scale = scale
        self._reset_scene_transform()
        self.status = "Scene transform cancelled"

    def _modal_scene(self, context, event):
        if self.scene_transform_active:
            if event.type == "ESC":
                self._cancel_scene_transform()
                return {"RUNNING_MODAL"}
            if event.type == "RET" and event.value == "PRESS":
                self._commit_scene_transform()
                return {"RUNNING_MODAL"}
            if event.value == "PRESS":
                self._update_scene_transform(event)
            return {"RUNNING_MODAL"}
        if event.type in {"Z", "Y"} and event.value == "PRESS" and event.ctrl:
            if event.type == "Y" or event.shift:
                self._restore_history(context, min(len(self.history) - 1, self.history_index + 1))
            else:
                self._restore_history(context, max(0, self.history_index - 1))
            return {"RUNNING_MODAL"}
        if event.value == "PRESS" and not event.ctrl and not event.alt:
            if event.type == "G":
                self._begin_scene_transform("MOVE")
                return {"RUNNING_MODAL"}
            if event.type == "R":
                self._begin_scene_transform("ROTATE")
                return {"RUNNING_MODAL"}
            if event.type == "S":
                self._begin_scene_transform("SCALE")
                return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def _select_hit(self, hit: RayHit, *, add: bool, subtract: bool) -> None:
        cells = set(self.selection)
        shape = _scene_shape(self.scene)
        radius = int(self.scene.voxtart_editor_radius)
        occupied = set(self.asset.voxels)
        if shape == "BOX" and self.selection_anchor is not None:
            minimum = tuple(min(self.selection_anchor[axis], hit.cell[axis]) for axis in range(3))
            maximum = tuple(max(self.selection_anchor[axis], hit.cell[axis]) for axis in range(3))
            selected = set(select_box(occupied, minimum, maximum))
        elif shape in {"LASSO", "CIRCLE"}:
            selected = set()
        elif shape == "COLOR":
            selected = set(select_color(self.asset.voxels, hit.cell))
        elif shape == "COMPONENT":
            selected = set(select_connected(self.asset.voxels, hit.cell))
        elif shape == "SURFACE":
            selected = set(select_flat_surface(self.asset.voxels, hit.cell, hit.normal))
        else:
            selected = set(
                sphere_cells(hit.cell, radius) & occupied
                if shape in {"SPHERE", "VOXEL"} and radius > 1
                else (hit.cell,)
            )
        if subtract:
            cells.difference_update(selected)
        elif add:
            cells.update(selected)
        else:
            cells = selected
        self.selection = tuple(sorted(cells))
        _store_selection(self.obj, self.selection)
        _restore_overlay(self.obj, self.asset)
        self.status = f"Selected {len(self.selection)} voxel(s)"

    def _projected_cells(self, context) -> dict[Cell, tuple[float, float]]:
        if context.region is None or context.region_data is None:
            return {}
        projected: dict[Cell, tuple[float, float]] = {}
        for cell in self.asset.voxels:
            local = Vector(
                tuple((cell[axis] + 0.5) * self.asset.grid_size for axis in range(3))
            )
            point = view3d_utils.location_3d_to_region_2d(
                context.region,
                context.region_data,
                self.obj.matrix_world @ local,
            )
            if point is not None:
                projected[cell] = (float(point.x), float(point.y))
        return projected
    def _update_hover_preview(self, context, event) -> None:
        hit = self._ray_hit(context, event)
        self.last_hit = hit
        if hit is None:
            self.preview_cells = ()
            _request_redraw(self, context)
            return
        mode = _editor_input_mode(self)
        if mode == "SELECT":
            self.preview_cells = (hit.cell,)
            _request_redraw(self, context)
            return
        anchor = self.stroke_anchor
        try:
            self.stroke_anchor = None
            self.preview_cells = self._stamp(hit, mode=mode)
        finally:
            self.stroke_anchor = anchor
        _request_redraw(self, context)

    def _update_screen_selection(self, context, event) -> None:
        shape = _scene_shape(self.scene)
        if shape not in {"LASSO", "CIRCLE"}:
            return
        point = (float(event.mouse_region_x), float(event.mouse_region_y))
        if shape == "LASSO":
            if not self.selection_path or self.selection_path[-1] != point:
                self.selection_path.append(point)
            selected = select_lasso(self._projected_cells(context), self.selection_path)
        else:
            anchor = self.selection_screen_anchor
            if anchor is None:
                return
            radius = ((point[0] - anchor[0]) ** 2 + (point[1] - anchor[1]) ** 2) ** 0.5
            selected = select_circle(self._projected_cells(context), anchor, radius)
        if not selected:
            return
        cells = set(self.selection_gesture_base)
        if event.ctrl:
            cells.difference_update(selected)
        elif event.shift:
            cells.update(selected)
        else:
            cells = set(selected)
        self.selection = tuple(sorted(cells))
        _store_selection(self.obj, self.selection)
        _restore_overlay(self.obj, self.asset)
        self.status = f"Selected {len(self.selection)} voxel(s)"

    def _pick_color(self, hit: RayHit) -> None:
        from . import palette_ui

        palette_ui._set_palette_selection(self.scene, hit.palette_index, edit_index=hit.palette_index)
        self.status = f"Picked color slot {hit.palette_index + 1}"

    def _stroke_hits(self, hit: RayHit) -> tuple[RayHit, ...]:
        previous = getattr(self, "stroke_cursor_cell", None)
        shape = _scene_shape(self.scene)
        cells = (hit.cell,)
        if previous is not None and shape not in {"LINE", "BOX"}:
            cells = line_cells(previous, hit.cell)
        return tuple(
            RayHit(cell, hit.palette_index, hit.distance, hit.normal)
            for cell in cells
        )

    def _apply_at_cursor(self, context, event) -> None:
        mode = _editor_input_mode(self)
        hit = self._ray_hit(context, event)
        self.last_hit = hit
        if hit is None:
            self.preview_cells = ()
            self.status = (
                "Aim at a voxel face or grid plane"
                if mode in {"ADD", "ATTACH"}
                else "Aim at an occupied voxel face"
            )
            _request_redraw(self, context)
            return
        if event.alt and any(hit.normal):
            self.preview_cells = ()
            self._pick_color(hit)
            _request_redraw(self, context)
            return
        if mode == "SELECT":
            self.preview_cells = ()
            self._select_hit(hit, add=bool(event.shift), subtract=bool(event.ctrl))
            _request_redraw(self, context)
            return
        self._begin_stroke(operation=f"editor_{mode.lower()}")
        if self.transaction is None:
            _request_redraw(self, context)
            return
        for stroke_hit in self._stroke_hits(hit):
            self.preview_cells = self._stamp(stroke_hit, mode=mode)
            self._apply_cells(self.preview_cells, mode=mode)
        self.stroke_cursor_cell = hit.cell
        now = time.perf_counter()
        if (
            getattr(event, "value", None) == "PRESS"
            or now - getattr(self, "_last_stroke_sync", 0.0) >= EDITOR_REDRAW_INTERVAL
        ):
            self._sync(context, rebuild=True)
            self._last_stroke_sync = now
        else:
            self._update_working_state()
        _request_redraw(self, context)

    def _activate_toolbar_tool(self, context, item) -> None:
        if item is None:
            return
        kind, value, _glyph, label, _shortcut = item
        self.dragging = False
        self._cancel_stroke(context)
        if kind == "MODE":
            self.scene.voxtart_editor_mode = value
            self.status = f"{label} mode"
        elif kind == "SHAPE":
            self.scene.voxtart_editor_shape = value
            self.status = f"{label} brush"
        else:
            self.scene.voxtart_editor_mode = "SELECT"
            self.scene.voxtart_editor_shape = value
            self.status = f"{label} selector"
        self.preview_cells = ()
        self.toolbar_hover = item
        _request_redraw(self, context, force=True)

    def _copy_selection(self) -> None:
        global _CLIPBOARD
        source = tuple(cell for cell in self.selection if cell in self.asset.voxels)
        if not source:
            self.status = "Nothing selected"
            return
        minimum = tuple(min(cell[axis] for cell in source) for axis in range(3))
        _CLIPBOARD = tuple(
            (
                tuple(cell[axis] - minimum[axis] for axis in range(3)),
                self.asset.voxels[cell],
            )
            for cell in sorted(source)
        )
        encoded = _encode_clipboard(_CLIPBOARD)
        self.obj[EDITOR_CLIPBOARD_PROPERTY] = encoded
        self.scene[EDITOR_CLIPBOARD_PROPERTY] = encoded
        self.status = f"Copied {len(_CLIPBOARD)} voxel(s)"

    def _cut_selection(self, context) -> None:
        if not self.selection:
            self.status = "Nothing selected"
            return
        self._begin_stroke()
        for cell in self.selection:
            self.transaction.remove(cell)
        self._commit_stroke(context)
        self.selection = ()
        _store_selection(self.obj, self.selection)
        _restore_overlay(self.obj, self.asset)
        self.status = "Selection cut"

    def _paste_selection(self, context, event) -> None:
        global _CLIPBOARD
        if not _CLIPBOARD:
            self._load_clipboard()
        if not _CLIPBOARD:
            self.status = "Clipboard is empty"
            return
        try:
            _CLIPBOARD = _decode_clipboard(_encode_clipboard(_CLIPBOARD), len(self.asset.palette))
        except (TypeError, ValueError, json.JSONDecodeError):
            self.status = "Clipboard palette indices are invalid"
            return
        hit = self._ray_hit(context, event)
        if hit is None:
            self.status = "Aim at a voxel face to paste"
            return
        anchor = tuple(hit.cell[axis] + hit.normal[axis] for axis in range(3))
        self._begin_stroke()
        if self.transaction is None:
            return
        cells = []
        for relative, palette_index in _CLIPBOARD:
            cell = tuple(anchor[axis] + relative[axis] for axis in range(3))  # type: ignore[assignment]
            self.transaction.add(cell, palette_index, overwrite=False)
            cells.append(cell)
        self._commit_stroke(context)
        self.selection = tuple(sorted(cells))
        _store_selection(self.obj, self.selection)
        _restore_overlay(self.obj, self.asset)
        self.status = f"Pasted {len(cells)} voxel(s)"

    def _clear_selection(self) -> None:
        self.selection = ()
        _store_selection(self.obj, self.selection)
        _restore_overlay(self.obj, self.asset)
        self.status = "Selection cleared"

    def _select_all(self) -> None:
        self.selection = tuple(sorted(self.asset.voxels))
        _store_selection(self.obj, self.selection)
        _restore_overlay(self.obj, self.asset)
        self.status = f"Selected all {len(self.selection)} voxel(s)"

    def _open_project_surface(self, context):
        from . import desktop_shell

        desktop_shell._set_mode(self.scene, "HOME")
        self._finish(context, {"FINISHED"})
        desktop_shell.open_shell()
        return {"FINISHED"}

    def _activate_palette(self, index: int) -> None:
        palette = tuple(getattr(self.asset, "palette", ()) or ())
        if not palette or not 0 <= index < len(palette):
            return
        from . import palette_ui

        palette_ui._set_palette_selection(self.scene, index, edit_index=index)
        self.scene.voxtart_palette_edit_color = palette[index]
        self.status = f"Color {index + 1} selected"
    def _toggle_palette_favorite(self, index: int) -> None:
        palette = tuple(getattr(self.asset, "palette", ()) or ())
        if not palette or not 0 <= index < len(palette):
            return
        key = _editor_palette_color_key(palette[index])
        favorites = _editor_palette_favorite_keys(self)
        if key in favorites:
            favorites.discard(key)
            self.status = f"Color {index + 1} unpinned"
        else:
            favorites.add(key)
            self.status = f"Color {index + 1} pinned"
        _set_editor_palette_favorite_keys(self, favorites)


    def _activate_dock_hit(self, context, hit) -> None:
        if hit is None or hit[1] != "ROW":
            return
        panel, _kind, index = hit
        if panel == "LAYERS":
            layers = tuple(getattr(self.stack, "layers", ()) or ())
            if not 0 <= index < len(layers):
                return
            self.stack.set_active(index)
            self.layer_asset = _layer_asset(self.asset, self.stack)
            self._sync(context, rebuild=True)
            self.status = f"Layer {layers[index].name} selected"
        elif panel == "HISTORY":
            self._restore_history(context, index)
        _request_redraw(self, context, force=True)

    def _finish_dock_drag(self, context, event) -> None:
        panel = getattr(self, "dock_dragging", None)
        if panel is None:
            return
        region = getattr(context, "region", None)
        if region is None:
            self.dock_dragging = None
            return
        x = float(getattr(event, "mouse_region_x", 0.0))
        y = float(getattr(event, "mouse_region_y", 0.0))
        side = "LEFT" if x < float(region.width) * 0.5 else "RIGHT"
        layout = _editor_dock_layout(self, region)
        sides = dict(getattr(self, "dock_panel_sides", {}) or {})
        order = list(getattr(self, "dock_panel_order", ()) or ())
        if panel not in order:
            order.append(panel)
        order.remove(panel)
        sides[panel] = side
        same_side = [name for name in order if sides.get(name) == side]
        insert_before = None
        for name in same_side:
            bounds = layout["panels"].get(name, {}).get("bounds")
            if bounds is not None and y > (bounds[1] + bounds[3]) * 0.5:
                insert_before = name
                break
        if insert_before is None:
            order.append(panel)
        else:
            order.insert(order.index(insert_before), panel)
        self.dock_panel_sides = sides
        self.dock_panel_order = order
        if side == "LEFT":
            self.scene[EDITOR_DOCK_LEFT_PROPERTY] = True
        else:
            self.scene[EDITOR_DOCK_RIGHT_PROPERTY] = True
        self.dock_dragging = None
        self.dock_hover = None
        self.status = f"{panel.title()} docked {side.lower()}"
        _request_redraw(self, context, force=True)

    def _activate_topbar_action(self, context, action: str) -> None:
        if action in {"OPTIONS_GRID", "OPTIONS_DETAILS", "OPTIONS_MORE_TOOLS"}:
            action = {
                "OPTIONS_GRID": "GRID",
                "OPTIONS_DETAILS": "DETAILS",
                "OPTIONS_MORE_TOOLS": "MORE_TOOLS",
            }[action]
        if self.transaction is not None:
            self._cancel_stroke(context)
        if action == "MENU":
            self.scene[EDITOR_OPTIONS_PROPERTY] = False
            visible = not bool(self.scene.get(EDITOR_MENU_PROPERTY))
            self.scene[EDITOR_MENU_PROPERTY] = visible
            self.status = "Menu open" if visible else "Menu closed"
            _request_redraw(self, context, force=True)
            return
        action = {
            "MENU_PROJECT": "PROJECT",
            "MENU_SAVE": "SAVE",
            "MENU_UNDO": "UNDO",
            "MENU_REDO": "REDO",
            "MENU_GRID": "GRID",
            "MENU_DETAILS": "DETAILS",
            "MENU_DOCK_LEFT": "DOCK_LEFT",
            "MENU_DOCK_RIGHT": "DOCK_RIGHT",
            "MENU_MORE_TOOLS": "MORE_TOOLS",
            "MENU_HELP": "HELP",
        }.get(action, action)
        if action != "OPTIONS":
            self.scene[EDITOR_OPTIONS_PROPERTY] = False
        self.scene[EDITOR_MENU_PROPERTY] = False
        if action in {"UNDO", "REDO"}:
            history = tuple(getattr(self, "history", ()))
            current = int(getattr(self, "history_index", -1))
            target = current - 1 if action == "UNDO" else current + 1
            if not 0 <= target < len(history):
                self.status = "Nothing to undo" if action == "UNDO" else "Nothing to redo"
                _request_redraw(self, context, force=True)
                return
            self._cancel_stroke(context)
            self._restore_history(context, target)
            self.status = "Undid last change" if action == "UNDO" else "Redid last change"
            _request_redraw(self, context, force=True)
            return
        if action == "SAVE":
            try:
                result = (
                    bpy.ops.wm.save_as_mainfile()
                    if bpy.data.filepath
                    else bpy.ops.wm.save_as_mainfile("INVOKE_DEFAULT")
                )
            except (RuntimeError, TypeError):
                result = {"CANCELLED"}
            self.status = (
                "Project saved"
                if "FINISHED" in result
                else "Choose a project file"
            )
            return
        if action == "HELP":
            visible = not bool(self.scene.get(EDITOR_HELP_PROPERTY))
            self.scene[EDITOR_HELP_PROPERTY] = visible
            self.status = (
                "Help is open — try these steps"
                if visible
                else "Help closed"
            )
            _request_redraw(self, context, force=True)
            return
        if action == "OPTIONS":
            visible = not bool(self.scene.get(EDITOR_OPTIONS_PROPERTY))
            self.scene[EDITOR_OPTIONS_PROPERTY] = visible
            self.status = "More choices open" if visible else "More choices closed"
            _request_redraw(self, context, force=True)
            return
        if action in {"DOCK_LEFT", "DOCK_RIGHT"}:
            property_name = (
                EDITOR_DOCK_LEFT_PROPERTY
                if action == "DOCK_LEFT"
                else EDITOR_DOCK_RIGHT_PROPERTY
            )
            visible = not bool(self.scene.get(property_name))
            self.scene[property_name] = visible
            side_label = "Left" if action == "DOCK_LEFT" else "Right"
            self.status = f"{side_label} panels open" if visible else f"{side_label} panels hidden"
            _request_redraw(self, context, force=True)
            return
        if action == "GRID":
            from . import desktop_ui

            visible = not bool(self.scene.get(EDITOR_GRID_PROPERTY))
            self.scene[EDITOR_GRID_PROPERTY] = visible
            desktop_ui.set_grid_visibility(visible, scene=self.scene)
            self.status = "Grid on" if visible else "Grid off"
            _request_redraw(self, context, force=True)
            return
        if action == "DETAILS":
            self.scene[EDITOR_OPTIONS_PROPERTY] = False
            visible = not bool(self.scene.get(EDITOR_DETAILS_PROPERTY))
            self.scene[EDITOR_DETAILS_PROPERTY] = visible
            self._set_seam_overlay_visibility(True)
            self.status = "Hints on" if visible else "Hints off"
            _request_redraw(self, context, force=True)
            return
        if action == "MORE_TOOLS":
            self.scene[EDITOR_OPTIONS_PROPERTY] = False
            advanced = not bool(self.scene.get(EDITOR_ADVANCED_PROPERTY))
            self.scene[EDITOR_ADVANCED_PROPERTY] = advanced
            self.status = "Advanced tools open" if advanced else "Simple tools only"
            _request_redraw(self, context, force=True)
            return
        if action == "PROJECT":
            self._open_project_surface(context)
    def _activate_view_action(self, context, action: str) -> None:
        if action not in {
            "VIEW_FRONT",
            "VIEW_RIGHT",
            "VIEW_TOP",
            "VIEW_FRAME",
            "VIEW_PROJECTION",
        }:
            return
        if self.transaction is not None:
            self._cancel_stroke(context)
        try:
            if action == "VIEW_FRONT":
                bpy.ops.view3d.view_axis(type="FRONT")
                status = "Front view"
            elif action == "VIEW_RIGHT":
                bpy.ops.view3d.view_axis(type="RIGHT")
                status = "Right view"
            elif action == "VIEW_TOP":
                bpy.ops.view3d.view_axis(type="TOP")
                status = "Top view"
            elif action == "VIEW_FRAME":
                bpy.ops.view3d.view_selected(use_all_regions=False)
                status = "View framed"
            else:
                status = _toggle_view_projection(context)
                if status is None:
                    self.status = "Viewport control unavailable"
                    _request_redraw(self, context, force=True)
                    return
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self.status = "Viewport control unavailable"
            _request_redraw(self, context, force=True)
            return
        self.status = status
        self.view_hud_hover = action
        _request_redraw(self, context, force=True)
    def _activate_tool_options_action(self, context, action: str) -> None:
        if not (action.startswith("TOOL_") or action.startswith("SCENE_")):
            return
        self._cancel_stroke(context)
        scene = self.scene
        if action == "SCENE_APPLY":
            self._commit_scene_transform()
        elif action == "SCENE_CANCEL":
            self._cancel_scene_transform()
        elif action in {"SCENE_MOVE", "SCENE_ROTATE", "SCENE_SCALE"}:
            self._begin_scene_transform(action.rsplit("_", 1)[-1])
        elif action == "TOOL_SELECT_ALL":
            self._select_all()
        elif action == "TOOL_CLEAR_SELECTION":
            self._clear_selection()
        elif action == "TOOL_COPY_SELECTION":
            self._copy_selection()
        elif action == "TOOL_CUT_SELECTION":
            self._cut_selection(context)
        elif action == "TOOL_RADIUS_DOWN":
            scene.voxtart_editor_radius = max(1, int(scene.voxtart_editor_radius) - 1)
            self.status = f"Brush radius {scene.voxtart_editor_radius}"
        elif action == "TOOL_RADIUS_UP":
            scene.voxtart_editor_radius = min(EDITOR_MAX_RADIUS, int(scene.voxtart_editor_radius) + 1)
            self.status = f"Brush radius {scene.voxtart_editor_radius}"
        elif action == "TOOL_HOLLOW":
            scene.voxtart_editor_hollow = not bool(scene.voxtart_editor_hollow)
            self.status = f"Hollow {'on' if scene.voxtart_editor_hollow else 'off'}"
        elif action.startswith("TOOL_MIRROR_"):
            axis = action.rsplit("_", 1)[-1].lower()
            property_name = f"voxtart_editor_mirror_{axis}"
            value = not bool(getattr(scene, property_name))
            setattr(scene, property_name, value)
            mirrors = list(self.mirrors)
            mirrors[("x", "y", "z").index(axis)] = value
            self.mirrors = tuple(mirrors)
            self.status = f"Mirror {axis.upper()} {'on' if value else 'off'}"
        elif action.startswith("TOOL_AXIS_"):
            axis = action.rsplit("_", 1)[-1]
            scene.voxtart_editor_axis = axis
            self.status = f"Brush axis {axis}"
        elif action == "TOOL_HEIGHT_DOWN":
            scene.voxtart_editor_height = max(1, int(scene.voxtart_editor_height) - 1)
            self.status = f"Brush height {scene.voxtart_editor_height}"
        elif action == "TOOL_HEIGHT_UP":
            scene.voxtart_editor_height = min(128, int(scene.voxtart_editor_height) + 1)
            self.status = f"Brush height {scene.voxtart_editor_height}"
        elif action == "TOOL_DEPTH_DOWN":
            scene.voxtart_editor_depth = max(1, int(scene.voxtart_editor_depth) - 1)
            self.status = f"Brush depth {scene.voxtart_editor_depth}"
        elif action == "TOOL_DEPTH_UP":
            scene.voxtart_editor_depth = min(128, int(scene.voxtart_editor_depth) + 1)
            self.status = f"Brush depth {scene.voxtart_editor_depth}"
        else:
            return
        self.preview_cells = ()
        self.tool_options_hover = action
        _request_redraw(self, context, force=True)



    def _finish(self, context, result=None):
        self.toolbar_hover = None
        self.topbar_hover = None
        self.palette_hover = None
        self.view_hud_hover = None
        self.tool_options_hover = None
        self.dock_hover = None
        self.dock_dragging = None
        try:
            from . import mcp

            mcp.set_chat_input_focus(False)
        except (ImportError, RuntimeError):
            pass
        self.scene[EDITOR_OPTIONS_PROPERTY] = False
        self.scene[EDITOR_MENU_PROPERTY] = False
        if self.transform_active:
            self._cancel_transform(context)
        elif self.scene_transform_active:
            self._cancel_scene_transform()
        else:
            self._cancel_stroke(context)
        self._finish_handler()
        _request_redraw(self, context, force=True)
        return {"FINISHED"} if result is None else result

    def modal(self, context, event):
        from . import desktop_ui, mcp

        _set_editor_cursor(context, _editor_cursor(self, event))
        if mcp.handle_chat_text_event(event):
            _request_redraw(self, context, force=True)
            return {"RUNNING_MODAL"}
        if desktop_ui.is_native_ui_shortcut(event):
            self.status = "VOXTART controls only"
            _request_redraw(self, context, force=True)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            chat_action = _editor_chat_action_at(context, event)
            if chat_action is not None:
                if chat_action == "chat:field":
                    mcp.set_chat_input_focus(True)
                elif chat_action == "chat:image":
                    mcp.set_chat_input_focus(False)
                    self.status = "Choose a reference image"
                    try:
                        bpy.ops.voxtart.chat_attach_image("INVOKE_DEFAULT")
                    except (AttributeError, RuntimeError):
                        self.status = "Image attachment is unavailable"
                else:
                    try:
                        bpy.ops.voxtart.chat_send()
                    except (AttributeError, RuntimeError):
                        self.status = "Local model chat is unavailable"
                _request_redraw(self, context, force=True)
                return {"RUNNING_MODAL"}
        view_status = _view_shortcut(context, event)
        if view_status is not None:
            self.status = view_status
            return {"RUNNING_MODAL"}
        if event.type == "ESC" and event.value == "PRESS":
            if getattr(self, "dock_dragging", None) is not None:
                self.dock_dragging = None
                self.dock_hover = None
                self.status = "Panel drag cancelled"
                _request_redraw(self, context, force=True)
                return {"RUNNING_MODAL"}
            if bool(self.scene.get(EDITOR_MENU_PROPERTY)):
                self.scene[EDITOR_MENU_PROPERTY] = False
                self.topbar_hover = None
                self.status = "Menu closed"
                _request_redraw(self, context, force=True)
                return {"RUNNING_MODAL"}
            if bool(self.scene.get(EDITOR_OPTIONS_PROPERTY)):
                self.scene[EDITOR_OPTIONS_PROPERTY] = False
                self.topbar_hover = None
                self.status = "Extra controls closed"
                _request_redraw(self, context, force=True)
                return {"RUNNING_MODAL"}
        if event.type == "TAB" and event.value == "PRESS":
            if getattr(self, "transform_active", False):
                self._cancel_transform(context)
            if self.scene_transform_active:
                self._cancel_scene_transform()
            self.scene.voxtart_editor_workspace = (
                "SCENE" if _scene_workspace(self.scene) == "EDIT" else "EDIT"
            )
            self.status = f"{_scene_workspace(self.scene).title()} workspace"
            return {"RUNNING_MODAL"}
        if (
            event.type == "G"
            and event.value == "PRESS"
            and not event.ctrl
            and not event.alt
        ):
            if not event.shift:
                visible = not desktop_ui.grid_visible(self.scene)
                desktop_ui.set_grid_visibility(visible, self.scene)
                self.status = f"Grid {'on' if visible else 'off'}"
                _request_redraw(self, context, force=True)
                return {"RUNNING_MODAL"}
            if _scene_workspace(self.scene) == "SCENE":
                self._begin_scene_transform("MOVE")
                return {"RUNNING_MODAL"}
            if _scene_mode(self.scene) == "SELECT":
                self._begin_transform("MOVE")
                return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE":
            self.chat_hover = _editor_chat_action_at(context, event)
            self.view_hud_hover = None
            self.tool_options_hover = None
            if self.chat_hover is not None:
                self.dock_hover = None
                self.color_wheel_hover = None
                self.topbar_hover = None
                self.palette_hover = None
                self.toolbar_hover = None
                self.last_hit = None
                self.preview_cells = ()
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}
            if getattr(self, "dock_dragging", None) is not None:
                self.dock_drag_x = float(getattr(event, "mouse_region_x", 0.0))
                self.dock_drag_y = float(getattr(event, "mouse_region_y", 0.0))
                self.dock_hover = None
                self.color_wheel_hover = None
                self.topbar_hover = None
                self.palette_hover = None
                self.toolbar_hover = None
                self.last_hit = None
                self.preview_cells = ()
                _set_editor_cursor(context, "HAND")
                _request_redraw(self, context)
                return {"RUNNING_MODAL"}
            menu_hit = _editor_menu_action_at(context, event)
            if menu_hit is not None:
                self.dock_hover = None
                self.color_wheel_hover = None
                self.topbar_hover = menu_hit
                self.palette_hover = None
                self.toolbar_hover = None
                self.last_hit = None
                self.preview_cells = ()
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}
            tool_options_hit = (
                None
                if bool(self.scene.get(EDITOR_MENU_PROPERTY))
                or bool(self.scene.get(EDITOR_OPTIONS_PROPERTY))
                else _editor_tool_options_action_at(context, event)
            )
            if tool_options_hit is not None:
                self.tool_options_hover = tool_options_hit
                self.dock_hover = None
                self.color_wheel_hover = None
                self.topbar_hover = None
                self.palette_hover = None
                self.toolbar_hover = None
                self.last_hit = None
                self.preview_cells = ()
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}
            view_hud_hit = (
                None
                if bool(self.scene.get(EDITOR_MENU_PROPERTY))
                or bool(self.scene.get(EDITOR_OPTIONS_PROPERTY))
                else _editor_view_hud_action_at(context, event)
            )
            if view_hud_hit is not None:
                self.view_hud_hover = view_hud_hit
                self.dock_hover = None
                self.color_wheel_hover = None
                self.topbar_hover = None
                self.palette_hover = None
                self.toolbar_hover = None
                self.last_hit = None
                self.preview_cells = ()
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}
            dock_hit = _editor_dock_hit(self, context, event)
            self.dock_hover = dock_hit
            if dock_hit is not None:
                self.color_wheel_hover = None
                self.topbar_hover = None
                self.palette_hover = None
                self.toolbar_hover = None
                self.last_hit = None
                self.preview_cells = ()
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}
            wheel_hit = _editor_color_wheel_hit(self, context, event)
            self.color_wheel_hover = wheel_hit
            if getattr(self, "color_wheel_dragging", False) or wheel_hit is not None:
                self.topbar_hover = None
                self.palette_hover = None
                self.toolbar_hover = None
                self.last_hit = None
                self.preview_cells = ()
                if getattr(self, "color_wheel_dragging", False) and wheel_hit is not None:
                    _set_editor_color_from_wheel(self, context, wheel_hit)
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}
            self.topbar_hover = _editor_topbar_action_at(context, event)
            if self.topbar_hover is not None:
                self.palette_hover = None
                self.toolbar_hover = None
                self.last_hit = None
                self.preview_cells = ()
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}
            self.palette_hover = _editor_palette_index_at(self, context, event)
            if self.palette_hover is not None:
                self.toolbar_hover = None
                self.last_hit = None
                self.preview_cells = ()
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}

        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "PRESS":
            if event.type == "LEFTMOUSE":
                mcp.set_chat_input_focus(False)
            menu_action = _editor_menu_action_at(context, event)
            if menu_action is not None:
                if event.type == "LEFTMOUSE":
                    self._activate_topbar_action(context, menu_action)
                return {"RUNNING_MODAL"}
            tool_options_action = (
                None
                if bool(self.scene.get(EDITOR_MENU_PROPERTY))
                or bool(self.scene.get(EDITOR_OPTIONS_PROPERTY))
                else _editor_tool_options_action_at(context, event)
            )
            if tool_options_action is not None:
                self.tool_options_hover = tool_options_action
                if event.type == "LEFTMOUSE":
                    self._activate_tool_options_action(context, tool_options_action)
                return {"RUNNING_MODAL"}
            view_hud_action = (
                None
                if bool(self.scene.get(EDITOR_MENU_PROPERTY))
                or bool(self.scene.get(EDITOR_OPTIONS_PROPERTY))
                else _editor_view_hud_action_at(context, event)
            )
            if view_hud_action is not None:
                self.view_hud_hover = view_hud_action
                if event.type == "LEFTMOUSE":
                    self._activate_view_action(context, view_hud_action)
                return {"RUNNING_MODAL"}
            dock_hit = _editor_dock_hit(self, context, event)
            if dock_hit is not None:
                self.dock_hover = dock_hit
                if event.type == "LEFTMOUSE" and dock_hit[1] == "HEADER":
                    self.dock_dragging = dock_hit[0]
                    self.dock_drag_x = float(getattr(event, "mouse_region_x", 0.0))
                    self.dock_drag_y = float(getattr(event, "mouse_region_y", 0.0))
                    self.color_wheel_hover = None
                    self.topbar_hover = None
                    self.palette_hover = None
                    self.toolbar_hover = None
                    self.last_hit = None
                    self.preview_cells = ()
                    _request_redraw(self, context, force=True)
                elif event.type == "LEFTMOUSE" and dock_hit[1] == "ROW":
                    self._activate_dock_hit(context, dock_hit)
                return {"RUNNING_MODAL"}
            wheel_hit = _editor_color_wheel_hit(self, context, event)
            if wheel_hit is not None:
                self.color_wheel_hover = wheel_hit
                if event.type == "LEFTMOUSE":
                    self.color_wheel_dragging = True
                    _set_editor_color_from_wheel(self, context, wheel_hit)
                return {"RUNNING_MODAL"}
            topbar_action = _editor_topbar_action_at(context, event)
            if topbar_action is not None:
                if event.type == "LEFTMOUSE":
                    self._activate_topbar_action(context, topbar_action)
                return {"RUNNING_MODAL"}
            if bool(self.scene.get(EDITOR_MENU_PROPERTY)):
                self.scene[EDITOR_MENU_PROPERTY] = False
                self.topbar_hover = None
                self.status = "Menu closed"
                _request_redraw(self, context, force=True)
                return {"RUNNING_MODAL"}
            if bool(self.scene.get(EDITOR_OPTIONS_PROPERTY)):
                self.scene[EDITOR_OPTIONS_PROPERTY] = False
                self.topbar_hover = None
                self.status = "Extra controls closed"
                _request_redraw(self, context, force=True)
                return {"RUNNING_MODAL"}
            palette_index = _editor_palette_index_at(self, context, event)
            if palette_index is not None:
                if event.type == "RIGHTMOUSE" or getattr(event, "alt", False):
                    self._toggle_palette_favorite(palette_index)
                    _request_redraw(self, context, force=True)
                elif event.type == "LEFTMOUSE":
                    self._activate_palette(palette_index)
                    _request_redraw(self, context, force=True)
                return {"RUNNING_MODAL"}
        if _scene_workspace(self.scene) == "SCENE" and not getattr(self, "transform_active", False):
            if event.type == "ESC":
                if self.scene_transform_active:
                    self._cancel_scene_transform()
                    self.status = "Scene move cancelled"
                else:
                    self.status = "VOXTART stays open"
                return {"RUNNING_MODAL"}
            return self._modal_scene(context, event)
        if getattr(self, "transform_active", False):
            if event.type == "ESC":
                self._cancel_transform(context)
                return {"RUNNING_MODAL"}
            if event.type == "RET" and event.value == "PRESS":
                self._commit_transform(context)
                return {"RUNNING_MODAL"}
            if event.value != "PRESS":
                return {"RUNNING_MODAL"}
            step = 5 if event.shift else 1
            if self.transform_kind == "MOVE":
                deltas = {
                    "LEFT_ARROW": (-step, 0, 0),
                    "RIGHT_ARROW": (step, 0, 0),
                    "UP_ARROW": (0, step, 0),
                    "DOWN_ARROW": (0, -step, 0),
                    "PAGE_UP": (0, 0, step),
                    "PAGE_DOWN": (0, 0, -step),
                }
                if event.type in deltas:
                    self._transform_move(deltas[event.type])
                    return {"RUNNING_MODAL"}
            elif self.transform_kind == "ROTATE" and event.type in {"LEFT_ARROW", "RIGHT_ARROW"}:
                self._transform_rotate(-1 if event.type == "LEFT_ARROW" else 1)
                return {"RUNNING_MODAL"}
            elif self.transform_kind == "MIRROR" and event.type in {"X", "Y", "Z"}:
                self.transform_axis = event.type
                self._update_transform_pose()
                self.status = f"Mirror {self.transform_axis} · Enter commit"
        if event.type == "ESC":
            if self.transaction is not None:
                self._cancel_stroke(context)
                self.status = "Stroke cancelled"
                return {"RUNNING_MODAL"}
            self.status = "VOXTART stays open"
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE":
            self.topbar_hover = None
            self.palette_hover = None
            self.toolbar_hover = _editor_toolbar_item_at(self, context, event)
            _set_editor_cursor(context, _editor_cursor(self, event))
            if self.toolbar_hover is not None:
                self.last_hit = None
                self.preview_cells = ()
                return {"RUNNING_MODAL"}
        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "PRESS":
            toolbar_item = _editor_toolbar_item_at(self, context, event)
            if toolbar_item is not None:
                if event.type == "LEFTMOUSE":
                    self._activate_toolbar_tool(context, toolbar_item)
                return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE":
            self.last_hit = self._ray_hit(context, event)
            mode = _editor_input_mode(self)
            if self.dragging and mode != "SELECT":
                self._apply_at_cursor(context, event)
            elif (
                self.dragging
                and mode == "SELECT"
                and _scene_shape(self.scene) == "BOX"
                and self.last_hit is not None
            ):
                self._select_hit(self.last_hit, add=bool(event.shift), subtract=bool(event.ctrl))
            elif (
                self.dragging
                and mode == "SELECT"
                and _scene_shape(self.scene) in {"LASSO", "CIRCLE"}
            ):
                self._update_screen_selection(context, event)
            elif not self.dragging:
                self._update_hover_preview(context, event)
            return {"RUNNING_MODAL"}
        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "PRESS":
            self.dragging = True
            self.drag_mode = "ERASE" if event.type == "RIGHTMOUSE" else _scene_mode(self.scene)
            _set_editor_cursor(context, _editor_cursor(self, event))
            self.selection_anchor = None
            self.selection_screen_anchor = None
            self.selection_path = []
            self.selection_gesture_base = tuple(self.selection)
            mode = _editor_input_mode(self)
            shape = _scene_shape(self.scene)
            if event.type == "LEFTMOUSE" and mode == "SELECT" and shape in {"LASSO", "CIRCLE"}:
                point = (float(event.mouse_region_x), float(event.mouse_region_y))
                self.selection_screen_anchor = point
                self.selection_path = [point]
                self.last_hit = self._ray_hit(context, event)
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and mode == "SELECT" and shape == "BOX":
                hit = self._ray_hit(context, event)
                if hit is not None:
                    self.selection_anchor = hit.cell
            self._apply_at_cursor(context, event)
            return {"RUNNING_MODAL"}
        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "RELEASE":
            if event.type == "LEFTMOUSE" and getattr(self, "dock_dragging", None) is not None:
                self._finish_dock_drag(context, event)
                _set_editor_cursor(context, _editor_cursor(self, event))
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and getattr(self, "color_wheel_dragging", False):
                self.color_wheel_dragging = False
                self.color_wheel_hover = None
                _set_editor_cursor(context, _editor_cursor(self, event))
                return {"RUNNING_MODAL"}
            if (
                self.dragging
                and event.type == "LEFTMOUSE"
                and _editor_input_mode(self) == "SELECT"
                and _scene_shape(self.scene) in {"LASSO", "CIRCLE"}
            ):
                self._update_screen_selection(context, event)
            self.dragging = False
            self.selection_anchor = None
            self.selection_screen_anchor = None
            self.selection_path = []
            self.selection_gesture_base = tuple(self.selection)
            self._commit_stroke(context)
            self.drag_mode = None
            _set_editor_cursor(context, _editor_cursor(self, event))
            return {"RUNNING_MODAL"}
        if event.type in {"Z", "Y"} and event.value == "PRESS" and event.ctrl:
            self._cancel_stroke(context)
            if event.type == "Y" or event.shift:
                self._restore_history(context, min(len(self.history) - 1, self.history_index + 1))
            else:
                self._restore_history(context, max(0, self.history_index - 1))
            return {"RUNNING_MODAL"}
        if event.type == "A" and event.value == "PRESS" and event.ctrl:
            self._select_all()
            return {"RUNNING_MODAL"}
        if event.type == "D" and event.value == "PRESS" and event.ctrl:
            self._clear_selection()
            return {"RUNNING_MODAL"}
        if event.type == "C" and event.value == "PRESS" and event.ctrl:
            self._copy_selection()
            return {"RUNNING_MODAL"}
        if event.type == "X" and event.value == "PRESS" and event.ctrl:
            self._cut_selection(context)
            return {"RUNNING_MODAL"}
        if event.type == "V" and event.value == "PRESS" and event.ctrl:
            self._paste_selection(context, event)
            return {"RUNNING_MODAL"}
        if event.value == "PRESS" and not event.ctrl and not event.alt and _scene_mode(self.scene) == "SELECT":
            if event.type == "R" and not event.shift:
                self._begin_transform("ROTATE")
                return {"RUNNING_MODAL"}
            if event.type == "M" and not event.shift:
                self._begin_transform("MIRROR")
                return {"RUNNING_MODAL"}
            if event.type == "D" and event.shift:
                self._begin_transform("MOVE", duplicate=True)
                return {"RUNNING_MODAL"}
        if event.value == "PRESS" and not event.ctrl and not event.alt:
            mode = {"B": "ADD", "T": "ATTACH", "E": "ERASE", "P": "PAINT", "S": "SELECT"}.get(event.type)
            shape = {
                "V": "VOXEL",
                "L": "LINE",
                "O": "BOX",
                "C": "SPHERE",
                "F": "FACE",
                "J": "LASSO",
                "K": "CIRCLE",
                "M": "COLOR",
                "N": "COMPONENT",
                "R": "SURFACE",
            }.get(event.type)
            if mode is not None:
                self.scene.voxtart_editor_mode = mode
                self._cancel_stroke(context)
                self.status = f"{mode.title()} mode"
                return {"RUNNING_MODAL"}
            if shape is not None:
                self.scene.voxtart_editor_shape = shape
                self._cancel_stroke(context)
                self.status = f"{shape.title()} brush"
                return {"RUNNING_MODAL"}
            if event.type in {"ONE", "TWO", "THREE"}:
                axis = {"ONE": 0, "TWO": 1, "THREE": 2}[event.type]
                values = list(self.mirrors)
                values[axis] = not values[axis]
                self.mirrors = tuple(values)
                setattr(self.scene, f"voxtart_editor_mirror_{AXES[axis].lower()}", values[axis])
                self.status = f"Mirror {AXES[axis]} {'on' if values[axis] else 'off'}"
                return {"RUNNING_MODAL"}
            if event.type == "H":
                self.scene.voxtart_editor_hollow = not self.scene.voxtart_editor_hollow
                self.status = f"Hollow {'on' if self.scene.voxtart_editor_hollow else 'off'}"
                return {"RUNNING_MODAL"}
            if event.type in {"LEFT_BRACKET", "RIGHT_BRACKET"}:
                delta = -1 if event.type == "LEFT_BRACKET" else 1
                self.scene.voxtart_editor_radius = max(
                    1,
                    min(EDITOR_MAX_RADIUS, self.scene.voxtart_editor_radius + delta),
                )
                self.status = f"Brush radius {self.scene.voxtart_editor_radius}"
                return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def invoke(self, context, _event):
        global _ACTIVE_EDITOR
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "Voxel Editor needs a 3D View")
            return {"CANCELLED"}
        self.obj = _active_asset(context)
        if self.obj is None:
            self.report({"WARNING"}, "Select a VOXTART voxel asset first")
            return {"CANCELLED"}
        try:
            for selected in tuple(getattr(context, "selected_objects", ()) or ()):
                if selected != self.obj:
                    selected.select_set(False)
            self.obj.select_set(True)
            context.view_layer.objects.active = self.obj
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            self.scene = context.scene
            self._last_recovery_write = 0.0
            self._load_state(context)
            recovered = self._recover_interrupted_state()
            operators._update_visual_overlays(self.obj, self.asset)
            recovery_status = getattr(self, "status", "")
            self._load_clipboard()
            self.history = [
                _copy_history(
                    self.asset,
                    self.stack,
                    self.selection,
                    self.obj,
                    label="Initial state",
                )
            ]
            self.history_index = 0
            self.transaction = None
            self.stroke_anchor = None
            self.stroke_cursor_cell = None
            self._last_stroke_sync = 0.0
            self.stroke_asset = None
            self.stroke_layers = None
            self.selection_anchor = None
            self.selection_screen_anchor = None
            self.selection_path = []
            self.selection_gesture_base = tuple(self.selection)
            self.preview_cells = ()
            self._last_redraw = 0.0
            self._preview_handle = None
            self._reset_transform()
            self._reset_scene_transform()
            self.dragging = False
            self.toolbar_hover = None
            self.topbar_hover = None
            self.palette_hover = None
            self.view_hud_hover = None
            self.color_wheel_hover = None
            self.tool_options_hover = None
            self.color_wheel_dragging = False
            self.dock_hover = None
            self.dock_dragging = None
            self.dock_drag_x = 0.0
            self.dock_drag_y = 0.0
            self.dock_panel_sides = {
                "PALETTE": "RIGHT",
                "LAYERS": "LEFT",
                "HISTORY": "LEFT",
            }
            self.dock_panel_order = ["LAYERS", "HISTORY", "PALETTE"]
            self.chat_hover = None
            if EDITOR_DETAILS_PROPERTY not in self.scene:
                self.scene[EDITOR_DETAILS_PROPERTY] = False
            if EDITOR_HELP_PROPERTY not in self.scene:
                self.scene[EDITOR_HELP_PROPERTY] = False
            if EDITOR_ADVANCED_PROPERTY not in self.scene:
                self.scene[EDITOR_ADVANCED_PROPERTY] = False
            if EDITOR_OPTIONS_PROPERTY not in self.scene:
                self.scene[EDITOR_OPTIONS_PROPERTY] = False
            if EDITOR_MENU_PROPERTY not in self.scene:
                self.scene[EDITOR_MENU_PROPERTY] = False
            if EDITOR_GRID_PROPERTY not in self.scene:
                self.scene[EDITOR_GRID_PROPERTY] = False
            if EDITOR_DOCK_LEFT_PROPERTY not in self.scene:
                self.scene[EDITOR_DOCK_LEFT_PROPERTY] = False
            if EDITOR_DOCK_RIGHT_PROPERTY not in self.scene:
                self.scene[EDITOR_DOCK_RIGHT_PROPERTY] = True
            overlay_name = self.obj.get(operators.SEAM_OVERLAY_PROPERTY)
            overlay = bpy.data.objects.get(overlay_name) if isinstance(overlay_name, str) else None
            self._seam_overlay_was_hidden = (
                True if overlay is None else bool(overlay.hide_viewport)
            )
            self._set_seam_overlay_visibility(True)
            self.drag_mode = None
            self.last_hit = None
            self.status = (
                recovery_status
                if (recovered or recovery_status)
                else (
                    "Ready — click a face to add a block"
                    if bool(self.scene.get(FIRST_RUN_HINT_PROPERTY))
                    else "Ready"
                )
            )
            self.mirrors = (
                bool(self.scene.voxtart_editor_mirror_x),
                bool(self.scene.voxtart_editor_mirror_y),
                bool(self.scene.voxtart_editor_mirror_z),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        try:
            from . import desktop_ui

            desktop_ui.configure_palette_color_management(self.scene)
            desktop_ui.configure_viewport_shading(context.space_data)
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            pass
        _ACTIVE_EDITOR = self
        self._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_editor_overlay,
            (self, context),
            "WINDOW",
            "POST_PIXEL",
        )
        self._preview_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_editor_preview,
            (self, context),
            "WINDOW",
            "POST_VIEW",
        )
        context.window_manager.modal_handler_add(self)
        _set_editor_cursor(context, _editor_cursor(self))
        return {"RUNNING_MODAL"}


class VOXTART_PT_voxel_editor(bpy.types.Panel):
    """Compact editor controls for the voxel-first workflow."""

    bl_label = "Voxel Editor"
    bl_idname = "VOXTART_PT_voxel_editor"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        active = _active_asset(context)
        if active is None:
            layout.label(text="Select a VOXTART asset first", icon="INFO")
            return
        layout.operator(VOXTART_OT_voxel_editor.bl_idname, text="Open Voxel Editor", icon="CUBE")
        layout.prop(scene, EDITOR_WORKSPACE_PROPERTY, text="Workspace")
        layout.prop(scene, EDITOR_MODE_PROPERTY, text="Mode")
        layout.prop(scene, EDITOR_SHAPE_PROPERTY, text="Brush")
        row = layout.row(align=True)
        row.prop(scene, "voxtart_editor_radius", text="Size")
        row.prop(scene, "voxtart_editor_hollow", text="Hollow")
        row = layout.row(align=True)
        row.prop(scene, "voxtart_editor_mirror_x", text="X")
        row.prop(scene, "voxtart_editor_mirror_y", text="Y")
        row.prop(scene, "voxtart_editor_mirror_z", text="Z")
        if scene.voxtart_editor_shape == "CYLINDER":
            layout.prop(scene, "voxtart_editor_axis", text="Axis")
            layout.prop(scene, "voxtart_editor_height", text="Height")
        if scene.voxtart_editor_shape == "FACE" or scene.voxtart_editor_mode == "ATTACH":
            layout.prop(scene, "voxtart_editor_depth", text="Depth")
        layout.label(text="LMB Place · RMB Erase · Drag paints · Alt+click picks color", icon="MOUSE_LMB")
        layout.label(text="B/T/E/P/S modes · F face · V/L/O/C brushes · M/N/R selectors", icon="INFO")
        layout.label(text="J/K lasso/circle · Ctrl A/C/X/V selection", icon="INFO")
class VOXTART_OT_history_restore(bpy.types.Operator):
    bl_idname = "voxtart.history_restore"
    bl_label = "Restore History Step"
    bl_description = "Restore a voxel editor history snapshot"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="History Step", min=0, default=0, options={"HIDDEN"})

    @classmethod
    def poll(cls, _context):
        return _ACTIVE_EDITOR is not None

    def execute(self, context):
        editor = _ACTIVE_EDITOR
        if editor is None:
            return {"CANCELLED"}
        history = getattr(editor, "history", ())
        if not 0 <= self.index < len(history):
            self.report({"ERROR"}, "history step is no longer available")
            return {"CANCELLED"}
        editor._restore_history(context, self.index)
        return {"FINISHED"}


class VOXTART_PT_editor_history(bpy.types.Panel):
    bl_label = "History"
    bl_idname = "VOXTART_PT_editor_history"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 4

    def draw(self, context):
        layout = self.layout
        editor = _ACTIVE_EDITOR
        if editor is None:
            layout.label(text="Open Voxel Editor to start history", icon="INFO")
            return
        history = tuple(getattr(editor, "history", ()))
        current = int(getattr(editor, "history_index", -1))
        layout.label(text=f"Step {current + 1} / {len(history)}", icon="TIME")
        row = layout.row(align=True)
        undo = row.operator(VOXTART_OT_history_restore.bl_idname, text="Undo", icon="LOOP_BACK")
        undo.index = max(0, current - 1)
        redo = row.operator(VOXTART_OT_history_restore.bl_idname, text="Redo", icon="LOOP_FORWARDS")
        redo.index = min(len(history) - 1, current + 1)
        for index, snapshot in enumerate(history[-12:], start=max(0, len(history) - 12)):
            label = str(getattr(snapshot, "label", "") or f"Step {index + 1}")
            entry = layout.row(align=True)
            entry.alert = index == current
            restore = entry.operator(
                VOXTART_OT_history_restore.bl_idname,
                text=f"{index + 1} · {label}",
                icon="RADIOBUT_ON" if index == current else "RADIOBUT_OFF",
            )
            restore.index = index



_CLASSES = (
    VOXTART_OT_voxel_editor,
    VOXTART_PT_voxel_editor,
    VOXTART_OT_history_restore,
    VOXTART_PT_editor_history,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_editor_workspace = EnumProperty(
        name="Voxel Editor Workspace",
        items=EDITOR_WORKSPACE_ITEMS,
        default="EDIT",
    )
    bpy.types.Scene.voxtart_editor_mode = EnumProperty(
        name="Voxel Editor Mode",
        items=EDITOR_MODE_ITEMS,
        default="ADD",
    )
    bpy.types.Scene.voxtart_editor_shape = EnumProperty(
        name="Voxel Editor Brush",
        items=EDITOR_SHAPE_ITEMS,
        default="VOXEL",
    )
    bpy.types.Scene.voxtart_editor_radius = IntProperty(
        name="Brush Radius",
        min=1,
        max=EDITOR_MAX_RADIUS,
        default=1,
    )
    bpy.types.Scene.voxtart_editor_depth = IntProperty(
        name="Face Brush Depth",
        min=1,
        max=128,
        default=1,
    )
    bpy.types.Scene.voxtart_editor_hollow = BoolProperty(
        name="Hollow Brush",
        description="Keep only the outer shell for box, sphere, and cylinder brushes",
        default=False,
    )
    bpy.types.Scene.voxtart_editor_mirror_x = BoolProperty(name="Mirror X", default=False)
    bpy.types.Scene.voxtart_editor_mirror_y = BoolProperty(name="Mirror Y", default=False)
    bpy.types.Scene.voxtart_editor_mirror_z = BoolProperty(name="Mirror Z", default=False)
    bpy.types.Scene.voxtart_editor_axis = EnumProperty(
        name="Brush Axis",
        items=[(axis, axis, f"Draw along the {axis} axis") for axis in AXES],
        default="Z",
    )
    bpy.types.Scene.voxtart_editor_height = IntProperty(
        name="Brush Height",
        min=1,
        max=128,
        default=3,
    )


def unregister() -> None:
    global _ACTIVE_EDITOR
    if _ACTIVE_EDITOR is not None:
        _ACTIVE_EDITOR._finish_handler()
        _ACTIVE_EDITOR = None
    for property_name in (
        "voxtart_editor_workspace",
        "voxtart_editor_depth",
        "voxtart_editor_height",
        "voxtart_editor_axis",
        "voxtart_editor_mirror_z",
        "voxtart_editor_mirror_y",
        "voxtart_editor_mirror_x",
        "voxtart_editor_hollow",
        "voxtart_editor_radius",
        "voxtart_editor_shape",
        "voxtart_editor_mode",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "BEGINNER_TOOLBAR_ITEMS",
    "EDITOR_CLIPBOARD_PROPERTY",
    "EDITOR_DETAILS_PROPERTY",
    "EDITOR_ADVANCED_PROPERTY",
    "EDITOR_OPTIONS_PROPERTY",
    "EDITOR_GRID_PROPERTY",
    "EDITOR_MENU_PROPERTY",
    "EDITOR_MODE_ITEMS",
    "EDITOR_MODE_PROPERTY",
    "EDITOR_MODES",
    "EDITOR_SHAPE_ITEMS",
    "EDITOR_SHAPE_PROPERTY",
    "EDITOR_STROKE_PROPERTY",
    "EDITOR_WORKSPACE_ITEMS",
    "EDITOR_WORKSPACES",
    "EDITOR_WORKSPACE_PROPERTY",
    "FIRST_RUN_HINT_PROPERTY",
    "VOXTART_OT_voxel_editor",
    "VOXTART_PT_voxel_editor",
    "register",
    "unregister",
    "visible_editor_toolbar_items",
]
