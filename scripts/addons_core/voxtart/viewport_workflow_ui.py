# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender-native tool, viewport, and multi-camera workflow panels."""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty, StringProperty


_VIEW_MODE_ITEMS = (
    ("PERSP", "Perspective", "Use perspective projection"),
    ("ORTHO", "Orthographic", "Use orthographic projection"),
    ("CAMERA", "Camera", "Look through the scene's active render camera"),
)
_VIEW_AXIS_ITEMS = tuple(
    (axis, axis.title(), f"Align the main view to the {axis.lower()} axis")
    for axis in ("FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM")
)


def _view3d_context(context):
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        return None, None
    space = getattr(area.spaces, "active", None)
    return space, getattr(space, "region_3d", None)


def _window_region(context):
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        return None
    return next((region for region in area.regions if region.type == "WINDOW"), None)


def _run_view_operator(context, operator, **kwargs):
    region = _window_region(context)
    area = getattr(context, "area", None)
    if region is None or area is None:
        raise RuntimeError("a 3D View window region is required")
    with bpy.context.temp_override(
        window=getattr(context, "window", None),
        screen=getattr(context, "screen", None),
        area=area,
        region=region,
        space_data=area.spaces.active,
    ):
        return operator(**kwargs)


def _scene_camera(context, object_name: str):
    target = context.scene.objects.get(object_name)
    if target is None or target.type != "CAMERA":
        raise ValueError("camera is not linked to the active scene")
    return target


def _select_object(context, obj) -> None:
    for selected in tuple(context.selected_objects):
        if selected != obj:
            selected.select_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _editor_mode(scene) -> str:
    return str(getattr(scene, "voxtart_editor_mode", "ADD"))


def _editor_shape(scene) -> str:
    return str(getattr(scene, "voxtart_editor_shape", "VOXEL"))


class VOXTART_PT_tool_settings(bpy.types.Panel):
    """Contextual VOXTART settings using Blender's standard property layout."""

    bl_label = "Tool Settings"
    bl_idname = "VOXTART_PT_tool_settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        mode = _editor_mode(scene)
        shape = _editor_shape(scene)

        tool = layout.box()
        tool.label(text=f"VOXTART · {mode.title()} / {shape.title()}", icon="TOOL_SETTINGS")
        if hasattr(scene, "voxtart_editor_mode"):
            tool.prop(scene, "voxtart_editor_mode", text="Mode")
        if hasattr(scene, "voxtart_editor_shape"):
            tool.prop(scene, "voxtart_editor_shape", text="Tool")
        if hasattr(scene, "voxtart_editor_radius"):
            tool.prop(scene, "voxtart_editor_radius", text="Radius")
        if hasattr(scene, "voxtart_editor_hollow") and shape in {"BOX", "SPHERE", "CYLINDER"}:
            tool.prop(scene, "voxtart_editor_hollow", text="Hollow")
        if hasattr(scene, "voxtart_editor_depth") and (shape == "FACE" or mode == "ATTACH"):
            tool.prop(scene, "voxtart_editor_depth", text="Depth")
        if hasattr(scene, "voxtart_editor_axis") and shape == "CYLINDER":
            tool.prop(scene, "voxtart_editor_axis", text="Axis")
        if hasattr(scene, "voxtart_editor_height") and shape == "CYLINDER":
            tool.prop(scene, "voxtart_editor_height", text="Height")
        mirror = tool.row(align=True)
        for property_name, label in (
            ("voxtart_editor_mirror_x", "X"),
            ("voxtart_editor_mirror_y", "Y"),
            ("voxtart_editor_mirror_z", "Z"),
        ):
            if hasattr(scene, property_name):
                mirror.prop(scene, property_name, text=label)
        if hasattr(scene, "voxtart_edit_palette_index") and mode == "PAINT":
            tool.prop(scene, "voxtart_edit_palette_index", text="Color Slot")
        if mode == "SELECT":
            tool.label(text="Shift adds · Ctrl subtracts", icon="SELECT_SET")
        tool.operator("voxtart.voxel_editor", text="Open Voxel Editor", icon="CUBE")

        blender = layout.box()
        blender.label(text="Blender View", icon="VIEW3D")
        space, _region_3d = _view3d_context(context)
        if space is not None:
            blender.prop(space, "show_gizmo", text="Gizmos")
            blender.prop(space.overlay, "show_floor", text="Floor")
            blender.prop(space.overlay, "show_axis_x", text="Axis X")
            blender.prop(space.overlay, "show_axis_y", text="Axis Y")
            blender.prop(space.shading, "type", text="Shading")
        blender.label(text="Use View & Cameras for perspective and focus.", icon="INFO")

        active = getattr(context, "active_object", None)
        if active is not None:
            object_box = layout.box()
            object_box.label(text=f"Selected: {active.name}", icon="OBJECT_DATA")
            object_box.prop(active, "display_type", text="Display")
            object_box.prop(active, "hide_viewport", text="Disable Viewport")
            object_box.prop(active, "hide_render", text="Disable Render")
            object_box.prop(active, "color", text="Object Color")


class VOXTART_OT_view_perspective(bpy.types.Operator):
    bl_idname = "voxtart.view_perspective"
    bl_label = "Set View Perspective"
    bl_description = "Switch the main 3D View between perspective, orthographic, and camera projection"

    mode: EnumProperty(name="Projection", items=_VIEW_MODE_ITEMS, default="PERSP", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        area = getattr(context, "area", None)
        return area is not None and area.type == "VIEW_3D"

    def execute(self, context):
        _space, region_3d = _view3d_context(context)
        if region_3d is None:
            self.report({"ERROR"}, "a 3D View region is required")
            return {"CANCELLED"}
        if self.mode == "CAMERA" and context.scene.camera is None:
            self.report({"ERROR"}, "set an active scene camera first")
            return {"CANCELLED"}
        try:
            if self.mode == "CAMERA":
                _run_view_operator(context, bpy.ops.view3d.view_camera)
            else:
                region_3d.view_perspective = self.mode
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_view_axis(bpy.types.Operator):
    bl_idname = "voxtart.view_axis"
    bl_label = "Align Main View"
    bl_description = "Align the main 3D View to a standard axis"

    axis: EnumProperty(name="Axis", items=_VIEW_AXIS_ITEMS, default="FRONT", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        area = getattr(context, "area", None)
        return area is not None and area.type == "VIEW_3D"

    def execute(self, context):
        try:
            _run_view_operator(context, bpy.ops.view3d.view_axis, type=self.axis)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_view_frame_selected(bpy.types.Operator):
    bl_idname = "voxtart.view_frame_selected"
    bl_label = "Frame Selected"
    bl_description = "Frame the selected VOXTART object in the main 3D View"

    @classmethod
    def poll(cls, context):
        area = getattr(context, "area", None)
        return area is not None and area.type == "VIEW_3D" and bool(context.selected_objects)

    def execute(self, context):
        try:
            _run_view_operator(context, bpy.ops.view3d.view_selected, use_all_regions=False)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_camera_add(bpy.types.Operator):
    bl_idname = "voxtart.camera_add"
    bl_label = "Add Camera"
    bl_description = "Add another camera to the current scene"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Camera Name", default="VOXTART Camera", options={"HIDDEN"})

    def execute(self, context):
        base_name = self.name.strip() or "VOXTART Camera"
        camera_data = bpy.data.cameras.new(f"{base_name} Data")
        camera = bpy.data.objects.new(base_name, camera_data)
        context.scene.collection.objects.link(camera)
        active = getattr(context, "active_object", None)
        if active is not None:
            camera.location = tuple(float(value) for value in active.location)
            camera.location.x += 7.0
            camera.location.y -= 7.0
            camera.location.z += 5.0
        else:
            camera.location = (7.0, -7.0, 5.0)
        if context.scene.camera is None:
            context.scene.camera = camera
        _select_object(context, camera)
        return {"FINISHED"}


class VOXTART_OT_camera_set_active(bpy.types.Operator):
    bl_idname = "voxtart.camera_set_active"
    bl_label = "Set Render Camera"
    bl_description = "Use this camera for scene rendering"
    bl_options = {"REGISTER", "UNDO"}

    object_name: StringProperty(name="Camera", options={"HIDDEN"})

    def execute(self, context):
        try:
            camera = _scene_camera(context, self.object_name)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        context.scene.camera = camera
        _select_object(context, camera)
        return {"FINISHED"}


class VOXTART_OT_camera_focus(bpy.types.Operator):
    bl_idname = "voxtart.camera_focus"
    bl_label = "Focus Camera"
    bl_description = "Select and frame this camera in the main 3D View"

    object_name: StringProperty(name="Camera", options={"HIDDEN"})

    def execute(self, context):
        try:
            camera = _scene_camera(context, self.object_name)
            _select_object(context, camera)
            if getattr(context, "area", None) is not None and context.area.type == "VIEW_3D":
                _run_view_operator(context, bpy.ops.view3d.view_selected, use_all_regions=False)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_camera_solo(bpy.types.Operator):
    bl_idname = "voxtart.camera_solo"
    bl_label = "Solo Camera"
    bl_description = "Disable other camera objects in the viewport and focus this camera"
    bl_options = {"REGISTER", "UNDO"}

    object_name: StringProperty(name="Camera", options={"HIDDEN"})

    def execute(self, context):
        try:
            camera = _scene_camera(context, self.object_name)
            for item in context.scene.objects:
                if item.type == "CAMERA":
                    item.hide_viewport = item != camera
            context.scene.camera = camera
            _select_object(context, camera)
            if getattr(context, "area", None) is not None and context.area.type == "VIEW_3D":
                _run_view_operator(context, bpy.ops.view3d.view_selected, use_all_regions=False)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_camera_show_all(bpy.types.Operator):
    bl_idname = "voxtart.camera_show_all"
    bl_label = "Show All Cameras"
    bl_description = "Enable every camera object in the viewport"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cameras = tuple(item for item in context.scene.objects if item.type == "CAMERA")
        if not cameras:
            self.report({"ERROR"}, "No cameras exist in the current scene")
            return {"CANCELLED"}
        for item in cameras:
            item.hide_viewport = False
        self.report({"INFO"}, f"Showed {len(cameras)} camera(s)")
        return {"FINISHED"}


class VOXTART_PT_view_workflow(bpy.types.Panel):
    bl_label = "View & Cameras"
    bl_idname = "VOXTART_PT_view_workflow"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 12

    def draw(self, context):
        layout = self.layout
        space, region_3d = _view3d_context(context)
        view = layout.box()
        view.label(text="Main View", icon="VIEW3D")
        current = getattr(region_3d, "view_perspective", "PERSP")
        row = view.row(align=True)
        for mode, label, _description in _VIEW_MODE_ITEMS:
            button = row.operator(
                VOXTART_OT_view_perspective.bl_idname,
                text=label,
                depress=current == mode,
            )
            button.mode = mode
        view.label(text=f"Projection: {current.title()}", icon="ORIENTATION_VIEW")
        axis_row = view.row(align=True)
        for axis in ("FRONT", "RIGHT", "TOP"):
            button = axis_row.operator(VOXTART_OT_view_axis.bl_idname, text=axis.title())
            button.axis = axis
        frame_row = view.row(align=True)
        frame_row.enabled = bool(context.selected_objects)
        frame_row.operator(VOXTART_OT_view_frame_selected.bl_idname, text="Frame Selected", icon="VIEWZOOM")
        if region_3d is not None:
            view.prop(region_3d, "view_distance", text="Distance")
            view.prop(region_3d, "view_location", text="Pivot")

        native = layout.box()
        native.label(text="Native Blender View", icon="PREFERENCES")
        if space is not None:
            native.prop(space, "show_gizmo", text="Gizmos")
            native.prop(space.overlay, "show_floor", text="Floor")
            native.prop(space.overlay, "show_axis_x", text="Axis X")
            native.prop(space.overlay, "show_axis_y", text="Axis Y")
            native.prop(space.shading, "type", text="Shading")

        cameras = layout.box()
        cameras.label(text="Scene Cameras", icon="CAMERA_DATA")
        cameras.prop(context.scene, "camera", text="Render Camera")
        camera_objects = sorted(
            (obj for obj in context.scene.objects if obj.type == "CAMERA"),
            key=lambda obj: obj.name.casefold(),
        )
        if not camera_objects:
            cameras.label(text="No cameras in this scene", icon="INFO")
        for camera in camera_objects:
            row = cameras.row(align=True)
            active = row.operator(
                VOXTART_OT_camera_set_active.bl_idname,
                text=camera.name,
                icon="CAMERA_DATA",
                depress=context.scene.camera == camera,
            )
            active.object_name = camera.name
            focus = row.operator(VOXTART_OT_camera_focus.bl_idname, text="Focus", icon="VIEWZOOM")
            focus.object_name = camera.name
            solo = row.operator(VOXTART_OT_camera_solo.bl_idname, text="Solo", icon="HIDE_OFF")
            solo.object_name = camera.name
            row.prop(camera, "hide_viewport", text="", icon="HIDE_ON" if camera.hide_viewport else "HIDE_OFF")
            row.prop(camera, "hide_render", text="", icon="RESTRICT_RENDER_ON" if camera.hide_render else "RESTRICT_RENDER_OFF")
        camera_actions = cameras.row(align=True)
        camera_actions.operator(VOXTART_OT_camera_add.bl_idname, text="Add Camera", icon="ADD")
        camera_actions.operator(VOXTART_OT_camera_show_all.bl_idname, text="Show All", icon="HIDE_OFF")


_CLASSES = (
    VOXTART_PT_tool_settings,
    VOXTART_OT_view_perspective,
    VOXTART_OT_view_axis,
    VOXTART_OT_view_frame_selected,
    VOXTART_OT_camera_add,
    VOXTART_OT_camera_set_active,
    VOXTART_OT_camera_focus,
    VOXTART_OT_camera_solo,
    VOXTART_OT_camera_show_all,
    VOXTART_PT_view_workflow,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "VOXTART_OT_camera_add",
    "VOXTART_OT_camera_focus",
    "VOXTART_OT_camera_set_active",
    "VOXTART_OT_camera_show_all",
    "VOXTART_OT_camera_solo",
    "VOXTART_OT_view_axis",
    "VOXTART_OT_view_frame_selected",
    "VOXTART_OT_view_perspective",
    "VOXTART_PT_tool_settings",
    "VOXTART_PT_view_workflow",
    "register",
    "unregister",
]
