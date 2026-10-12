# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Interactive voxel editing session for the Blender 3D View.

The editor keeps Blender navigation intact while adding a compact voxel-first
workflow: add, erase, paint, select, brush shapes, mirror axes, palette
picking, clipboard placement, and per-stroke undo/redo.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass

import blf
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy_extras import view3d_utils
from mathutils import Vector
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
EDITOR_WORKSPACES = ("EDIT", "SCENE")
EDITOR_WORKSPACE_ITEMS = (
    ("EDIT", "Edit", "Modify voxels with brushes and selections"),
    ("SCENE", "Scene", "Move and rotate the selected asset"),
)
EDITOR_MAX_RADIUS = 16
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


def visible_editor_toolbar_items(scene=None):
    if scene is not None and bool(scene.get(FIRST_RUN_HINT_PROPERTY)):
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
    try:
        encoded = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(encoded, list):
            return ()
        result = []
        for cell in encoded:
            if isinstance(cell, (list, tuple)) and len(cell) == 3:
                result.append(tuple(int(axis) for axis in cell))
        return tuple(sorted(set(result)))  # type: ignore[return-value]
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()


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
    )


def _copy_history(asset: VoxelAsset, stack, selection: tuple[Cell, ...], obj=None) -> _HistoryState:
    if obj is None:
        return _HistoryState(asset.to_dict(), stack.encode(), tuple(selection))
    return _HistoryState(
        asset.to_dict(),
        stack.encode(),
        tuple(selection),
        tuple(float(value) for value in obj.location),
        tuple(float(value) for value in obj.rotation_euler),
        tuple(float(value) for value in obj.scale),
    )


def _restore_overlay(obj, asset: VoxelAsset) -> None:
    try:
        operators._update_visual_overlays(obj, asset)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return


def _draw_editor_text(text: str, x: float, y: float, size: float, color) -> None:
    blf.position(0, x, y, 0)
    blf.size(0, size)
    blf.color(0, *color)
    blf.draw(0, text)

def _editor_text_width(text: str, size: float) -> float:
    blf.size(0, size)
    return blf.dimensions(0, text)[0]


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
    available = max(0.0, float(height) - 120.0)
    button_height = min(
        EDITOR_TOOLBAR_BUTTON_MAX,
        max(
            EDITOR_TOOLBAR_BUTTON_MIN,
            (available - (item_count - 1) * 5.0 - separator_count * 9.0) / item_count,
        ),
    )
    beginner = items is BEGINNER_TOOLBAR_ITEMS
    button_width = 126.0 if beginner else button_height
    gap = max(3.0, min(6.0, button_height * 0.12))
    separator = max(6.0, button_height * 0.2)
    x = 8.0
    top = float(height) - 132.0
    cursor = top
    entries = []
    for item in items:
        if item is None:
            cursor -= separator
            continue
        y = cursor - button_height
        entries.append((item, (x + 8.0, y, x + 8.0 + button_width, y + button_height)))
        cursor = y - gap
    bottom = cursor + gap
    panel = (x, bottom - 8.0, button_width + 16.0, top - bottom + 16.0)
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


def _editor_topbar_action_bounds(region):
    width = float(getattr(region, "width", 0.0))
    top = float(getattr(region, "height", 0.0)) - 42.0
    more_x = max(8.0, width - 136.0)
    details_x = max(8.0, more_x - 96.0)
    save_x = max(8.0, details_x - 72.0)
    project_x = max(8.0, save_x - 104.0)
    return (
        ("PROJECT", (project_x, top, save_x - 8.0, top + 30.0)),
        ("SAVE", (save_x, top, details_x - 8.0, top + 30.0)),
        ("DETAILS", (details_x, top, more_x - 8.0, top + 30.0)),
        ("MORE_TOOLS", (more_x, top, max(more_x + 1.0, width - 12.0), top + 30.0)),
    )


def _editor_topbar_action_at(context, event):
    region = getattr(context, "region", None)
    if region is None:
        return None
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    for action, bounds in _editor_topbar_action_bounds(region):
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return action
    return None
def _editor_palette_layout(editor, region):
    palette = tuple(getattr(getattr(editor, "asset", None), "palette", ()) or ())
    if not palette:
        return (0.0, 0.0, 0.0, 0.0), ()
    width = float(getattr(region, "width", 0.0))
    swatch_size = 28.0
    gap = 6.0
    usable = max(120.0, width - 220.0)
    columns = max(1, min(len(palette), int((usable - 24.0 + gap) // (swatch_size + gap))))
    rows = (len(palette) + columns - 1) // columns
    panel_width = max(
        112.0,
        24.0 + columns * swatch_size + max(0, columns - 1) * gap,
    )
    panel_width = min(max(112.0, width - 24.0), panel_width)
    panel_height = 34.0 + rows * swatch_size + max(0, rows - 1) * gap
    panel_x = max(8.0, width - panel_width - 16.0)
    panel_y = 52.0
    entries = []
    for index in range(len(palette)):
        row, column = divmod(index, columns)
        x = panel_x + 12.0 + column * (swatch_size + gap)
        y = panel_y + panel_height - 12.0 - swatch_size - row * (swatch_size + gap)
        entries.append((index, (x, y, x + swatch_size, y + swatch_size)))
    return (panel_x, panel_y, panel_x + panel_width, panel_y + panel_height), tuple(entries)


def _editor_palette_index_at(editor, context, event):
    region = getattr(context, "region", None)
    if region is None:
        return None
    x = float(getattr(event, "mouse_region_x", -1.0))
    y = float(getattr(event, "mouse_region_y", -1.0))
    for index, bounds in _editor_palette_layout(editor, region)[1]:
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            return index
    return None


def _native_chrome_visible(scene) -> bool:
    try:
        from . import desktop_ui

        return desktop_ui.native_chrome_visible(scene)
    except (AttributeError, RuntimeError, TypeError):
        return False


def _draw_editor_topbar(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None:
        return
    width = float(region.width)
    height = float(region.height)
    bar_y = height - 48.0
    asset_name = getattr(getattr(editor, "obj", None), "name", "Voxel asset")
    asset_summary = f"{asset_name[:30]}  ·  {len(editor.asset.voxels)} blocks"
    _draw_editor_rect(0.0, bar_y, width, 48.0, (0.025, 0.032, 0.045, 0.98))
    _draw_editor_text("VOXTART", 18.0, bar_y + 28.0, 14.0, (0.98, 0.82, 0.35, 1.0))
    _draw_editor_text(
        asset_summary,
        102.0,
        bar_y + 28.0,
        12.0,
        (0.92, 0.95, 1.0, 1.0),
    )
    _draw_editor_text(
        "Voxel editor",
        102.0,
        bar_y + 12.0,
        10.0,
        (0.58, 0.68, 0.78, 1.0),
    )
    visible = _native_chrome_visible(context.scene)
    details = bool(context.scene.get(EDITOR_DETAILS_PROPERTY))
    labels = {
        "PROJECT": "Project",
        "SAVE": "Save",
        "DETAILS": "Hide details" if details else "Details",
        "MORE_TOOLS": "Hide tools" if visible else "More tools",
    }
    try:
        if gpu is not None:
            gpu.state.blend_set("ALPHA")
        workspace = _scene_workspace(context.scene)
        workspace_label = workspace
        workspace_accent = (
            (0.35, 0.62, 1.0, 1.0)
            if workspace == "EDIT"
            else (0.98, 0.72, 0.3, 1.0)
        )
        workspace_pill_width = 90.0
        workspace_x = 102.0 + _editor_text_width(asset_summary, 12.0) + 16.0
        project_x = _editor_topbar_action_bounds(region)[0][1][0]
        if workspace_x + workspace_pill_width + 12.0 < project_x:
            _draw_editor_panel(
                workspace_x,
                bar_y + 9.0,
                workspace_pill_width,
                28.0,
                (0.07, 0.12, 0.19, 0.98)
                if workspace == "EDIT"
                else (0.18, 0.12, 0.05, 0.98),
                border=workspace_accent,
                radius=14.0,
            )
            _draw_editor_round_rect(
                workspace_x + 11.0,
                bar_y + 20.0,
                6.0,
                6.0,
                workspace_accent,
                radius=3.0,
            )
            _draw_editor_text(
                workspace_label,
                workspace_x + 24.0,
                bar_y + 18.0,
                10.0,
                (0.9, 0.95, 1.0, 1.0),
            )
            _draw_editor_text(
                "TAB",
                workspace_x + 59.0,
                bar_y + 18.0,
                9.0,
                (0.62, 0.72, 0.84, 1.0),
            )
        for action, bounds in _editor_topbar_action_bounds(region):
            x0, y0, x1, y1 = bounds
            hovered = action == getattr(editor, "topbar_hover", None)
            button_color = (
                (0.16, 0.28, 0.48, 0.98)
                if hovered
                else (0.07, 0.09, 0.13, 0.98)
            )
            border_color = (
                (0.35, 0.62, 1.0, 0.98)
                if hovered
                else (0.15, 0.2, 0.29, 0.98)
            )
            _draw_editor_panel(
                x0,
                y0,
                x1 - x0,
                y1 - y0,
                button_color,
                border=border_color,
                radius=7.0,
            )
            _draw_editor_text(
                labels[action],
                x0 + 10.0,
                y0 + 10.0,
                11.0,
                (0.96, 0.98, 1.0, 1.0),
            )
    finally:
        if gpu is not None:
            gpu.state.blend_set("NONE")
def _draw_editor_palette(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None or gpu is None or batch_for_shader is None:
        return
    panel, entries = _editor_palette_layout(editor, region)
    if not entries:
        return
    palette = tuple(getattr(editor.asset, "palette", ()) or ())
    selected = int(getattr(context.scene, "voxtart_edit_palette_index", 0)) % len(palette)
    x0, y0, x1, y1 = panel
    try:
        gpu.state.blend_set("ALPHA")
        _draw_editor_panel(
            x0,
            y0,
            x1 - x0,
            y1 - y0,
            (0.035, 0.045, 0.06, 0.96),
            border=(0.15, 0.2, 0.29, 0.98),
            radius=10.0,
        )
        _draw_editor_text("COLORS", x0 + 12.0, y1 - 18.0, 10.0, (0.62, 0.74, 0.86, 1.0))
        _draw_editor_text(
            f"Color {selected + 1}",
            max(x0 + 58.0, x1 - 64.0),
            y1 - 18.0,
            10.0,
            (0.92, 0.95, 1.0, 1.0),
        )
        for index, bounds in entries:
            sx0, sy0, sx1, sy1 = bounds
            active = index == selected
            if active:
                _draw_editor_round_rect(
                    sx0 - 2.0,
                    sy0 - 2.0,
                    sx1 - sx0 + 4.0,
                    sy1 - sy0 + 4.0,
                    (0.98, 0.82, 0.35, 1.0),
                    radius=7.0,
                )
            color = tuple(float(value) for value in palette[index])
            if len(color) < 4:
                color = (*color[:3], 1.0)
            _draw_editor_round_rect(
                sx0,
                sy0,
                sx1 - sx0,
                sy1 - sy0,
                color[:4],
                radius=5.0,
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
    panel, entries = _editor_toolbar_layout(region.height, context.scene)
    try:
        gpu.state.blend_set("ALPHA")
        _draw_editor_panel(
            *panel,
            (0.035, 0.045, 0.06, 0.96),
            border=(0.15, 0.2, 0.29, 0.98),
            radius=10.0,
        )
        for item, bounds in entries:
            x0, y0, x1, y1 = bounds
            width = x1 - x0
            height = y1 - y0
            hovered = item == getattr(editor, "toolbar_hover", None)
            active = _editor_toolbar_active(editor, item)
            if active and item[0] == "MODE" and item[1] == "ERASE":
                color = (0.55, 0.15, 0.14, 0.98)
            elif active:
                color = (0.18, 0.42, 0.72, 0.98)
            elif hovered:
                color = (0.18, 0.21, 0.28, 0.98)
            else:
                color = (0.09, 0.11, 0.15, 0.96)
            border_color = (
                (0.9, 0.3, 0.24, 0.98)
                if active and item[0] == "MODE" and item[1] == "ERASE"
                else (0.35, 0.62, 1.0, 0.98)
                if active or hovered
                else (0.15, 0.2, 0.29, 0.98)
            )
            _draw_editor_panel(
                x0,
                y0,
                width,
                height,
                color,
                border=border_color,
                radius=min(7.0, height / 2.0),
            )
            beginner = visible_editor_toolbar_items(context.scene) is BEGINNER_TOOLBAR_ITEMS
            if beginner:
                _draw_editor_text(
                    item[3],
                    x0 + 10.0,
                    y0 + height * 0.5,
                    max(10.0, min(13.0, height * 0.42)),
                    (0.96, 0.97, 1.0, 1.0),
                )
                _draw_editor_text(
                    item[4],
                    x1 - max(10.0, height * 0.24),
                    y0 + 5.0,
                    max(8.0, min(10.0, height * 0.23)),
                    (0.67, 0.74, 0.84, 1.0),
                )
            else:
                glyph_size = max(12.0, min(19.0, height * 0.42))
                glyph_offset = (len(item[2]) * glyph_size * 0.25)
                _draw_editor_text(
                    item[2],
                    x0 + width * 0.5 - glyph_offset,
                    y0 + height * 0.48,
                    glyph_size,
                    (0.96, 0.97, 1.0, 1.0),
                )
                _draw_editor_text(
                    item[4],
                    x1 - max(10.0, height * 0.24),
                    y0 + 5.0,
                    max(8.0, min(10.0, height * 0.23)),
                    (0.67, 0.74, 0.84, 1.0),
                )
        hovered_item = getattr(editor, "toolbar_hover", None)
        if hovered_item is not None:
            hovered_bounds = next(
                (bounds for item, bounds in entries if item == hovered_item),
                None,
            )
            if hovered_bounds is not None:
                tooltip_width = 154.0
                tooltip_height = 38.0
                tooltip_x = min(
                    hovered_bounds[2] + 10.0,
                    max(8.0, float(region.width) - tooltip_width - 8.0),
                )
                tooltip_y = max(
                    8.0,
                    min(hovered_bounds[1], float(region.height) - tooltip_height - 8.0),
                )
                _draw_editor_panel(
                    tooltip_x,
                    tooltip_y,
                    tooltip_width,
                    tooltip_height,
                    (0.045, 0.055, 0.075, 0.98),
                    border=(0.2, 0.28, 0.4, 0.98),
                    radius=8.0,
                )
                _draw_editor_text(
                    hovered_item[3],
                    tooltip_x + 10.0,
                    tooltip_y + 23.0,
                    12.0,
                    (0.94, 0.96, 1.0, 1.0),
                )
                _draw_editor_text(
                    f"Shortcut {hovered_item[4]}",
                    tooltip_x + 10.0,
                    tooltip_y + 9.0,
                    9.0,
                    (0.62, 0.7, 0.8, 1.0),
                )
    finally:
        gpu.state.blend_set("NONE")


def _view_shortcut(context, event) -> str | None:
    if getattr(event, "value", None) != "PRESS" or getattr(event, "ctrl", False) or getattr(event, "alt", False):
        return None
    command = {
        "HOME": ("view_selected", "View recentered"),
        "NUMPAD1": ("front", "Front view"),
        "NUMPAD3": ("right", "Right view"),
        "NUMPAD7": ("top", "Top view"),
    }.get(getattr(event, "type", ""))
    if command is None:
        return None
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


def _draw_gpu_lines(editor, vertices, color, *, width=2.0) -> None:
    if gpu is None or batch_for_shader is None or not vertices:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINES", {"pos": vertices})
    shader.bind()
    shader.uniform_float("color", color)
    try:
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(width)
    except (AttributeError, RuntimeError):
        pass
    batch.draw(shader)
    try:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")
    except (AttributeError, RuntimeError):
        pass


def _draw_editor_preview(editor, _context) -> None:
    if gpu is None or batch_for_shader is None:
        return
    try:
        matrix = editor.obj.matrix_world
        grid_size = editor.asset.grid_size
        preview_vertices = []
        for cell in editor.preview_cells:
            corners = _cube_corners(cell, grid_size)
            for start, end in _CUBE_EDGE_PAIRS:
                preview_vertices.extend((matrix @ corners[start], matrix @ corners[end]))
        mode = _editor_input_mode(editor)
        preview_color = {
            "ERASE": (1.0, 0.18, 0.16, 0.85),
            "PAINT": (1.0, 0.75, 0.16, 0.9),
            "SELECT": (0.24, 0.75, 1.0, 0.9),
        }.get(mode, (0.24, 1.0, 0.48, 0.9))
        _draw_gpu_lines(editor, preview_vertices, preview_color, width=2.0)

        hit = editor.last_hit
        if hit is not None:
            hit_corners = _cube_corners(hit.cell, grid_size)
            hit_vertices = []
            for start, end in _CUBE_EDGE_PAIRS:
                hit_vertices.extend((matrix @ hit_corners[start], matrix @ hit_corners[end]))
            _draw_gpu_lines(editor, hit_vertices, (1.0, 1.0, 1.0, 0.95), width=3.0)

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
                _draw_gpu_lines(
                    editor,
                    tuple(vertex for index in range(4) for vertex in (face_vertices[index], face_vertices[(index + 1) % 4])),
                    (1.0, 0.9, 0.2, 0.95),
                    width=3.0,
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


def _draw_editor_overlay(editor, context) -> None:
    region = getattr(context, "region", None)
    if region is None:
        return
    scene = context.scene
    mode = _editor_input_mode(editor)
    shape = _scene_shape(scene)
    first_run = bool(scene.get(FIRST_RUN_HINT_PROPERTY))
    hit = editor.last_hit.cell if editor.last_hit is not None else "--"
    normal = (
        editor.last_hit.normal
        if editor.last_hit is not None and any(editor.last_hit.normal)
        else ("GRID" if editor.last_hit is not None else "--")
    )
    active_layer = getattr(editor.stack, "active", None)
    layer_name = active_layer.name if active_layer is not None else "--"
    layer_lock = " locked" if active_layer is not None and active_layer.locked else ""
    mode_label = {
        "ADD": "Add blocks",
        "ATTACH": "Add blocks",
        "ERASE": "Erase blocks",
        "PAINT": "Color blocks",
        "SELECT": "Select blocks",
    }.get(mode, mode.title())
    _draw_editor_topbar(editor, context)
    _draw_editor_palette(editor, context)
    _draw_editor_toolbar(editor, context)
    context_label = mode_label if first_run else f"{mode_label}  ·  {shape.title()} brush"
    context_shortcut = {
        "ADD": "B",
        "ATTACH": "T",
        "ERASE": "E",
        "PAINT": "P",
        "SELECT": "S",
    }.get(mode, "")
    context_fill, context_accent = {
        "ADD": ((0.06, 0.15, 0.12, 0.98), (0.34, 0.92, 0.58, 1.0)),
        "ATTACH": ((0.07, 0.12, 0.19, 0.98), (0.35, 0.62, 1.0, 1.0)),
        "ERASE": ((0.18, 0.08, 0.08, 0.98), (1.0, 0.42, 0.3, 1.0)),
        "PAINT": ((0.18, 0.13, 0.05, 0.98), (0.98, 0.72, 0.3, 1.0)),
        "SELECT": ((0.12, 0.08, 0.2, 0.98), (0.66, 0.5, 1.0, 1.0)),
    }.get(mode, ((0.07, 0.09, 0.13, 0.98), (0.35, 0.62, 1.0, 1.0)))
    context_x = 84.0
    context_y = float(region.height) - 84.0
    context_width = min(
        520.0,
        max(230.0, _editor_text_width(context_label, 12.0) + 66.0),
    )
    _draw_editor_panel(
        context_x,
        context_y,
        context_width,
        28.0,
        context_fill,
        border=context_accent,
        radius=14.0,
    )
    _draw_editor_round_rect(
        context_x + 11.0,
        context_y + 11.0,
        6.0,
        6.0,
        context_accent,
        radius=3.0,
    )
    _draw_editor_text(
        context_label,
        context_x + 26.0,
        context_y + 9.0,
        12.0,
        (0.92, 0.97, 1.0, 1.0),
    )
    _draw_editor_text(
        context_shortcut,
        context_x + context_width - 22.0,
        context_y + 9.0,
        10.0,
        (0.7, 0.78, 0.88, 1.0),
    )
    if first_run:
        card_x = 84.0
        card_y = max(100.0, float(region.height) - 164.0)
        card_width = max(300.0, min(760.0, float(region.width) - card_x - 24.0))
        _draw_editor_panel(
            card_x,
            card_y,
            card_width,
            74.0,
            (0.045, 0.09, 0.14, 0.96),
            border=(0.18, 0.35, 0.5, 0.98),
            radius=12.0,
        )
        _draw_editor_text(
            "TRY THIS",
            card_x + 14.0,
            card_y + 55.0,
            11.0,
            (0.98, 0.82, 0.35, 1.0),
        )
        _draw_editor_text(
            "Pick a color below, then click a face to add a block",
            card_x + 14.0,
            card_y + 37.0,
            11.0,
            (0.9, 0.94, 1.0, 1.0),
        )
        _draw_editor_text(
            "Right click erases  ·  Move to orbit  ·  Ctrl+Z undoes",
            card_x + 14.0,
            card_y + 20.0,
            10.0,
            (0.62, 0.74, 0.86, 1.0),
        )
    elif bool(scene.get(EDITOR_DETAILS_PROPERTY)):
        _draw_editor_text(
            "Left click Add  ·  Right click Erase  ·  Drag Paint  ·  Alt+click Pick color  ·  Middle mouse Orbit",
            18,
            region.height - 112,
            11,
            (0.62, 0.72, 0.84, 1.0),
        )
        _draw_editor_text(
            "Workspace Tab  ·  Add B  ·  Attach T  ·  Erase E  ·  Color P  ·  Select S  ·  Brushes V/L/O/C/F",
            18,
            region.height - 130,
            11,
            (0.62, 0.72, 0.84, 1.0),
        )
    if bool(scene.get(EDITOR_DETAILS_PROPERTY)):
        detail_summary = (
            f"Cell {hit}  ·  Normal {normal}  ·  Preview {len(editor.preview_cells)}"
            f"  ·  Selection {len(editor.selection)}"
        )
        detail_layer = f"Layer {layer_name}{layer_lock}"
        detail_width = min(
            760.0,
            max(300.0, _editor_text_width(detail_summary, 11.0) + 32.0),
        )
        _draw_editor_panel(
            14.0,
            36.0,
            detail_width,
            40.0,
            (0.045, 0.09, 0.1, 0.98),
            border=(0.2, 0.5, 0.45, 0.98),
            radius=10.0,
        )
        _draw_editor_text(
            detail_summary,
            28.0,
            57.0,
            11.0,
            (0.7, 0.94, 0.84, 1.0),
        )
        _draw_editor_text(
            detail_layer,
            28.0,
            41.0,
            11.0,
            (0.88, 0.8, 0.58, 1.0),
        )
    status = str(getattr(editor, "status", "Ready") or "Ready")
    status_display = status if len(status) <= 72 else f"{status[:69]}..."
    status_lower = status_display.lower()
    if any(
        fragment in status_lower
        for fragment in ("locked", "invalid", "nothing", "empty", "aim at", "cancelled", "discarded", "no block")
    ):
        status_fill = (0.18, 0.08, 0.08, 0.98)
        status_border = (0.78, 0.28, 0.22, 0.98)
        status_accent = (1.0, 0.42, 0.3, 1.0)
        status_text = (1.0, 0.88, 0.84, 1.0)
    elif any(
        fragment in status_lower
        for fragment in ("added ", "painted ", "removed ", "selected ", "saved", "recovered", "pasted", "committed")
    ):
        status_fill = (0.06, 0.15, 0.12, 0.98)
        status_border = (0.2, 0.66, 0.45, 0.98)
        status_accent = (0.34, 0.92, 0.58, 1.0)
        status_text = (0.86, 1.0, 0.9, 1.0)
    else:
        status_fill = (0.07, 0.09, 0.13, 0.98)
        status_border = (0.2, 0.35, 0.52, 0.98)
        status_accent = (0.35, 0.62, 1.0, 1.0)
        status_text = (0.86, 0.92, 1.0, 1.0)
    status_width = min(
        620.0,
        max(176.0, float(region.width) - 28.0),
        max(176.0, _editor_text_width(status_display, 11.0) + 42.0),
    )
    _draw_editor_panel(
        14.0,
        8.0,
        status_width,
        24.0,
        status_fill,
        border=status_border,
        radius=12.0,
    )
    _draw_editor_round_rect(23.0, 17.0, 6.0, 6.0, status_accent, radius=3.0)
    _draw_editor_text(status_display, 38.0, 15.0, 11.0, status_text)


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

    def _finish_handler(self) -> None:
        global _ACTIVE_EDITOR
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
        self.selection = _selection(self.obj)

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

    def _record_history(self) -> None:
        self.history = self.history[: self.history_index + 1]
        self.history.append(_copy_history(self.asset, self.stack, self.selection, self.obj))
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
        self.status = "History restored"

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
        self._sync(context, rebuild=False)
        self.transaction = None
        self.stroke_anchor = None
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
        self.preview_cells = ()
        if report.changed:
            self._record_history()
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
                self.transaction.paint(cell, palette_index)

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
        self._record_history()
        self._reset_scene_transform()
        self.status = "Scene transform committed"

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
            return
        mode = _editor_input_mode(self)
        if mode == "SELECT":
            self.preview_cells = (hit.cell,)
            return
        anchor = self.stroke_anchor
        try:
            self.stroke_anchor = None
            self.preview_cells = self._stamp(hit, mode=mode)
        finally:
            self.stroke_anchor = anchor

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
        self.scene.voxtart_edit_palette_index = hit.palette_index
        if hasattr(self.scene, "voxtart_palette_index"):
            self.scene.voxtart_palette_index = hit.palette_index
        self.status = f"Picked color slot {hit.palette_index + 1}"

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
        cells = self._stamp(hit, mode=mode)
        self.preview_cells = cells
        self._apply_cells(cells, mode=mode)
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
        self.scene.voxtart_edit_palette_index = index
        if hasattr(self.scene, "voxtart_palette_index"):
            self.scene.voxtart_palette_index = index
        self.status = f"Color {index + 1} selected"

    def _activate_topbar_action(self, context, action: str) -> None:
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
        if action == "DETAILS":
            visible = not bool(self.scene.get(EDITOR_DETAILS_PROPERTY))
            self.scene[EDITOR_DETAILS_PROPERTY] = visible
            self.status = "Technical details shown" if visible else "Simple view"
            _request_redraw(self, context, force=True)
            return
        if action == "MORE_TOOLS":
            from . import desktop_ui

            visible = not desktop_ui.native_chrome_visible(self.scene)
            desktop_ui.set_native_chrome_visibility(visible)
            self.status = "Blender tools shown" if visible else "VOXTART tools only"
            return
        if action == "PROJECT":
            self._open_project_surface(context)

    def _finish(self, context, result=None):
        self.toolbar_hover = None
        self.topbar_hover = None
        self.palette_hover = None
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
        _set_editor_cursor(context, _editor_cursor(self, event))
        view_status = _view_shortcut(context, event)
        if view_status is not None:
            self.status = view_status
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
        if event.type == "MOUSEMOVE":
            self.topbar_hover = _editor_topbar_action_at(context, event)
            if self.topbar_hover is not None:
                self.palette_hover = None
                self.toolbar_hover = None
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}
            self.palette_hover = _editor_palette_index_at(self, context, event)
            if self.palette_hover is not None:
                self.toolbar_hover = None
                _set_editor_cursor(context, "HAND")
                return {"RUNNING_MODAL"}
        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "PRESS":
            topbar_action = _editor_topbar_action_at(context, event)
            if topbar_action is not None:
                if event.type == "LEFTMOUSE":
                    self._activate_topbar_action(context, topbar_action)
                return {"RUNNING_MODAL"}
            palette_index = _editor_palette_index_at(self, context, event)
            if palette_index is not None:
                if event.type == "LEFTMOUSE":
                    self._activate_palette(palette_index)
                    _request_redraw(self, context, force=True)
                return {"RUNNING_MODAL"}
        if _scene_workspace(self.scene) == "SCENE" and not getattr(self, "transform_active", False):
            if event.type == "ESC":
                if self.scene_transform_active:
                    self._cancel_scene_transform()
                    return {"RUNNING_MODAL"}
                return self._open_project_surface(context)
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
                return {"RUNNING_MODAL"}
            return {"RUNNING_MODAL"}
        if event.type == "ESC":
            if self.transaction is not None:
                self._cancel_stroke(context)
                self.status = "Stroke cancelled"
                return {"RUNNING_MODAL"}
            return self._open_project_surface(context)
        if event.type == "MOUSEMOVE":
            self.topbar_hover = None
            self.palette_hover = None
            self.toolbar_hover = _editor_toolbar_item_at(self, context, event)
            _set_editor_cursor(context, _editor_cursor(self, event))
            if self.toolbar_hover is not None:
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
            if event.type == "G":
                self._begin_transform("MOVE")
                return {"RUNNING_MODAL"}
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
            self.scene = context.scene
            self._last_recovery_write = 0.0
            self._load_state(context)
            recovered = self._recover_interrupted_state()
            recovery_status = getattr(self, "status", "")
            self._load_clipboard()
            self.history = [_copy_history(self.asset, self.stack, self.selection, self.obj)]
            self.history_index = 0
            self.transaction = None
            self.stroke_anchor = None
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
            if EDITOR_DETAILS_PROPERTY not in self.scene:
                self.scene[EDITOR_DETAILS_PROPERTY] = False
            self.drag_mode = None
            self.last_hit = None
            self.status = recovery_status if (recovered or recovery_status) else "Ready"
            self.mirrors = (
                bool(self.scene.voxtart_editor_mirror_x),
                bool(self.scene.voxtart_editor_mirror_y),
                bool(self.scene.voxtart_editor_mirror_z),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
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
