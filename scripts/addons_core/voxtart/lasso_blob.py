# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Viewport lasso tool for rounded marching-voxel blobs."""


import math
from typing import Any

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty

from .blob import rounded_lasso_voxels


BLOB_PROPERTY = "voxtart_lasso_blob"
BLOB_SURFACE_PROPERTY = "voxtart_lasso_blob_surface"
BLOB_AXIS_PROPERTY = "voxtart_lasso_blob_symmetry_axis"
BLOB_SYMMETRY_PROPERTY = "voxtart_lasso_blob_symmetry"


def _is_blob(obj: bpy.types.Object | None) -> bool:
    return obj is not None and bool(obj.get(BLOB_PROPERTY, False))


def _blob_palette(
    context: bpy.types.Context,
) -> tuple[list[tuple[float, float, float, float]], int]:
    from .palette import scene_palette

    palette = scene_palette(context.scene)
    palette_index = int(context.scene.voxtart_edit_palette_index)
    if not 0 <= palette_index < len(palette):
        palette_index = 0
    return palette, palette_index


def _screen_points_to_blob(context: bpy.types.Context, points: list[tuple[float, float]]):
    palette, palette_index = _blob_palette(context)
    from bpy_extras import view3d_utils
    from mathutils import Matrix, Vector

    scene = context.scene
    region = context.region
    region_3d = context.space_data.region_3d
    grid_size = max(float(scene.voxtart_grid_size), 0.0001)
    center = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )
    depth_location = scene.cursor.location.copy()
    origin = view3d_utils.region_2d_to_location_3d(
        region,
        region_3d,
        center,
        depth_location,
    )
    world_points = tuple(
        view3d_utils.region_2d_to_location_3d(region, region_3d, point, depth_location)
        for point in points
    )
    view_to_world = region_3d.view_matrix.inverted().to_3x3()
    right = (view_to_world @ Vector((1.0, 0.0, 0.0))).normalized()
    up = (view_to_world @ Vector((0.0, 1.0, 0.0))).normalized()
    forward = (view_to_world @ Vector((0.0, 0.0, 1.0))).normalized()
    polygon = tuple(
        (
            (world_point - origin).dot(right) / grid_size,
            (world_point - origin).dot(up) / grid_size,
        )
        for world_point in world_points
    )
    symmetry_axis = None
    if scene.voxtart_lasso_blob_symmetry:
        symmetry_axis = {"X": 0, "Y": 1, "Z": 2}[scene.voxtart_lasso_blob_axis]
    voxels = rounded_lasso_voxels(
        polygon,
        depth_voxels=int(scene.voxtart_lasso_blob_depth),
        palette_index=palette_index,
        symmetry_axis=symmetry_axis,
    )
    basis = Matrix((right, up, forward)).transposed()
    translation = origin - ((right + up + forward) * (grid_size * 0.5))
    matrix_world = Matrix.Translation(translation) @ basis.to_4x4()
    return voxels, polygon, matrix_world, grid_size, symmetry_axis, palette


def _create_blob(context: bpy.types.Context, points: list[tuple[float, float]]) -> dict[str, Any]:
    from . import operators
    from .voxel import VoxelAsset

    (
        voxels,
        polygon,
        matrix_world,
        grid_size,
        symmetry_axis,
        palette,
    ) = _screen_points_to_blob(context, points)
    scene = context.scene
    asset = VoxelAsset(
        grid_size=grid_size,
        palette=palette,
        voxels=voxels,
        provenance={
            "tool": "voxtart_lasso_blob",
            "surface": "marching",
            "symmetry": "enabled" if symmetry_axis is not None else "disabled",
        },
    )
    obj = operators._create_asset_object(
        context,
        "VOXTART Lasso Blob",
        asset,
        matrix_world=matrix_world,
        backend="marching",
    )
    obj[BLOB_PROPERTY] = True
    obj[BLOB_SURFACE_PROPERTY] = "marching"
    obj[BLOB_SYMMETRY_PROPERTY] = bool(scene.voxtart_lasso_blob_symmetry)
    obj[BLOB_AXIS_PROPERTY] = scene.voxtart_lasso_blob_axis
    obj["voxtart_lasso_blob_depth"] = int(scene.voxtart_lasso_blob_depth)
    obj["voxtart_lasso_blob_polygon"] = [list(point) for point in polygon]
    return {
        "object": obj,
        "voxel_count": len(voxels),
        "surface": "marching",
        "symmetry_axis": scene.voxtart_lasso_blob_axis if symmetry_axis is not None else None,
    }


class VOXTART_OT_lasso_blob(bpy.types.Operator):
    bl_idname = "voxtart.lasso_blob"
    bl_label = "Lasso Blob"
    bl_description = "Draw a lasso in the 3D View and create a rounded marching-voxel blob"
    bl_options = {"REGISTER", "UNDO"}

    _points: list[tuple[float, float]]
    _drawing: bool
    _area: Any
    _draw_handler: Any
    _shader: Any
    _batch_for_shader: Any

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.region is not None
            and context.space_data is not None
            and context.space_data.region_3d is not None
        )

    def invoke(self, context, _event):
        self._points = []
        self._drawing = False
        self._area = context.area
        self._draw_handler = None
        self._shader = None
        self._batch_for_shader = None
        try:
            import gpu
            from gpu_extras.batch import batch_for_shader

            self._shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            self._batch_for_shader = batch_for_shader
            self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
                self._draw_lasso,
                (),
                "WINDOW",
                "POST_PIXEL",
            )
        except (ImportError, RuntimeError):
            self._draw_handler = None
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        self.report({"INFO"}, "Draw a lasso around the blob, then release the mouse")
        return {"RUNNING_MODAL"}

    def _remove_draw_handler(self, context):
        if self._draw_handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._draw_handler, "WINDOW")
            except (ReferenceError, RuntimeError, ValueError):
                pass
            self._draw_handler = None
        area = self._area or getattr(context, "area", None)
        if area is not None:
            area.tag_redraw()

    def _draw_lasso(self):
        if self._shader is None or self._batch_for_shader is None or len(self._points) < 2:
            return
        points = list(self._points)
        if not self._drawing and len(points) >= 3:
            points.append(points[0])
        batch = self._batch_for_shader(self._shader, "LINE_STRIP", {"pos": points})
        self._shader.bind()
        self._shader.uniform_float("color", (0.1, 0.8, 1.0, 0.95))
        batch.draw(self._shader)

    def _finish(self, context, *, cancel: bool):
        self._remove_draw_handler(context)
        if cancel:
            return {"CANCELLED"}
        if len(self._points) < 3:
            self.report({"ERROR"}, "Lasso Blob needs at least three points")
            return {"CANCELLED"}
        try:
            result = _create_blob(context, self._points)
        except (RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"created rounded blob with {result['voxel_count']} voxels")
        return {"FINISHED"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            return self._finish(context, cancel=True)
        if event.type == "LEFTMOUSE":
            point = (float(event.mouse_region_x), float(event.mouse_region_y))
            if event.value == "PRESS":
                self._points = [point]
                self._drawing = True
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE" and self._drawing:
                self._points.append(point)
                self._drawing = False
                return self._finish(context, cancel=False)
        if event.type == "MOUSEMOVE" and self._drawing:
            point = (float(event.mouse_region_x), float(event.mouse_region_y))
            if not self._points or math.dist(point, self._points[-1]) >= 2.0:
                self._points.append(point)
                if self._area is not None:
                    self._area.tag_redraw()
        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS" and self._drawing:
            self._drawing = False
            return self._finish(context, cancel=False)
        return {"RUNNING_MODAL"}


class VOXTART_OT_convert_lasso_blob(bpy.types.Operator):
    bl_idname = "voxtart.convert_lasso_blob"
    bl_label = "Convert to Block Voxels"
    bl_description = "Convert a marching rounded blob to the default block voxel surface"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _is_blob(getattr(context, "active_object", None))

    def execute(self, context):
        from . import operators

        obj = context.active_object
        try:
            asset = operators._load_asset(obj)
            operators._replace_asset_mesh(obj, asset, backend="greedy")
            obj[BLOB_SURFACE_PROPERTY] = "greedy"
            obj["voxtart_lasso_blob_converted"] = True
        except (RuntimeError, TypeError, ValueError):
            self.report({"ERROR"}, "active Lasso Blob data is invalid")
            return {"CANCELLED"}
        self.report({"INFO"}, "converted Lasso Blob to block voxels")
        return {"FINISHED"}


class VOXTART_PT_lasso_blob_panel(bpy.types.Panel):
    bl_label = "VOXTART Lasso Blob"
    bl_idname = "VOXTART_PT_lasso_blob_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 5

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.label(text="Draw a 2D lasso; VOXTART makes a rounded voxel blob.")
        layout.operator(VOXTART_OT_lasso_blob.bl_idname, text="Draw Lasso Blob")
        layout.prop(scene, "voxtart_lasso_blob_depth", text="Round depth")
        layout.prop(scene, "voxtart_lasso_blob_symmetry", text="Symmetrical")
        if scene.voxtart_lasso_blob_symmetry:
            layout.prop(scene, "voxtart_lasso_blob_axis", text="Mirror axis")
        layout.label(text="Edit it normally, or keep marching / convert to blocks.", icon="INFO")
        if _is_blob(getattr(context, "active_object", None)):
            surface = context.active_object.get(BLOB_SURFACE_PROPERTY, "marching")
            layout.label(text=f"Current surface: {surface}")
            if surface == "marching":
                layout.operator(VOXTART_OT_convert_lasso_blob.bl_idname, text="Convert to Block Voxels")


_CLASSES = (
    VOXTART_OT_lasso_blob,
    VOXTART_OT_convert_lasso_blob,
    VOXTART_PT_lasso_blob_panel,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_lasso_blob_depth = IntProperty(
        name="Lasso Blob Depth",
        description="Maximum rounded depth in voxels",
        min=1,
        max=64,
        default=6,
    )
    bpy.types.Scene.voxtart_lasso_blob_symmetry = BoolProperty(
        name="Lasso Blob Symmetry",
        description="Mirror the generated blob across the selected local axis",
        default=False,
    )
    bpy.types.Scene.voxtart_lasso_blob_axis = EnumProperty(
        name="Lasso Blob Mirror Axis",
        items=(
            ("X", "X", "Mirror across the local X axis"),
            ("Y", "Y", "Mirror across the local Y axis"),
            ("Z", "Z", "Mirror across the local Z axis"),
        ),
        default="X",
    )


def unregister() -> None:
    for name in (
        "voxtart_lasso_blob_axis",
        "voxtart_lasso_blob_symmetry",
        "voxtart_lasso_blob_depth",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
