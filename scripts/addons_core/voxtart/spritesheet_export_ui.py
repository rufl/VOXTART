# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Blender renderer and UI for exporting animated voxels as sprite frames."""


import json
import math
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector

from . import operators
from .spritesheet import compose_spritesheet, sample_frame_range
from .tiler import PixelTexture


_VIEW_ITEMS = (
    ("FRONT", "Front", "Render from the negative Y axis"),
    ("BACK", "Back", "Render from the positive Y axis"),
    ("LEFT", "Left", "Render from the negative X axis"),
    ("RIGHT", "Right", "Render from the positive X axis"),
    ("TOP", "Top", "Render from the positive Z axis"),
    ("ISOMETRIC", "Isometric", "Render from a three-quarter voxel view"),
)
_VIEW_DIRECTIONS = {
    "FRONT": Vector((0.0, -1.0, 0.0)),
    "BACK": Vector((0.0, 1.0, 0.0)),
    "LEFT": Vector((-1.0, 0.0, 0.0)),
    "RIGHT": Vector((1.0, 0.0, 0.0)),
    "TOP": Vector((0.0, 0.0, 1.0)),
    "ISOMETRIC": Vector((1.0, -1.0, 0.8)).normalized(),
}


def _active_asset(context: bpy.types.Context):
    obj = getattr(context, "active_object", None)
    return obj if obj is not None and operators.ASSET_PROPERTY in obj else None


def _animation_candidates(scene: bpy.types.Scene, source_obj) -> tuple[bpy.types.Object, ...]:
    candidates = []
    seen = set()

    def add(obj) -> None:
        if obj is None or getattr(obj, "name", None) in seen:
            return
        seen.add(obj.name)
        candidates.append(obj)

    target_name = str(getattr(scene, "voxtart_animation_target", "") or "").strip()
    add(bpy.data.objects.get(target_name) if target_name else None)
    add(source_obj)
    parent = getattr(source_obj, "parent", None)
    while parent is not None:
        add(parent)
        parent = getattr(parent, "parent", None)
    for modifier in getattr(source_obj, "modifiers", ()):
        if getattr(modifier, "type", "") == "ARMATURE":
            add(getattr(modifier, "object", None))
    return tuple(candidates)


def _resolve_animation(scene: bpy.types.Scene, source_obj, requested_name: str = ""):
    requested = str(requested_name or getattr(scene, "voxtart_spritesheet_action", "") or "").strip()
    if not requested:
        requested = str(getattr(scene, "voxtart_animation_action", "") or "").strip()
    candidates = _animation_candidates(scene, source_obj)
    if requested:
        action = bpy.data.actions.get(requested)
        if action is None:
            raise ValueError(f"Unknown animation action: {requested}")
        owner = next(
            (
                candidate
                for candidate in candidates
                if getattr(candidate, "type", "") == "ARMATURE"
                or getattr(candidate, "animation_data", None) is not None
            ),
            None,
        )
        if owner is None:
            raise ValueError("The selected animation needs an animated object or armature")
        return owner, action
    for candidate in candidates:
        animation_data = getattr(candidate, "animation_data", None)
        action = getattr(animation_data, "action", None) if animation_data else None
        if action is not None:
            return candidate, action
    return None, None


def _world_bounds(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    corners = tuple(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)
    if not corners:
        center = Vector(getattr(obj, "location", (0.0, 0.0, 0.0)))
        return center, center, center
    minimum = Vector((
        min(corner.x for corner in corners),
        min(corner.y for corner in corners),
        min(corner.z for corner in corners),
    ))
    maximum = Vector((
        max(corner.x for corner in corners),
        max(corner.y for corner in corners),
        max(corner.z for corner in corners),
    ))
    return minimum, maximum, corners


def _configure_camera(
    camera,
    source_obj,
    depsgraph,
    view: str,
    frame_width: int,
    frame_height: int,
    margin: float,
    explicit_scale: float,
    bounds=None,
) -> float:
    if bounds is None:
        minimum, maximum, corners = _world_bounds(source_obj, depsgraph)
    else:
        minimum, maximum, corners = bounds
    center = (minimum + maximum) * 0.5
    direction = _VIEW_DIRECTIONS.get(str(view).upper())
    if direction is None:
        raise ValueError(f"unsupported spritesheet view: {view!r}")
    direction = direction.normalized()
    up_reference = Vector((0.0, 1.0, 0.0)) if abs(direction.z) > 0.9 else Vector((0.0, 0.0, 1.0))
    right = up_reference.cross(direction).normalized()
    up = direction.cross(right).normalized()
    horizontal = max(abs((corner - center).dot(right)) for corner in corners) * 2.0
    vertical = max(abs((corner - center).dot(up)) for corner in corners) * 2.0
    aspect = max(float(frame_width) / max(float(frame_height), 1.0), 0.001)
    required_scale = max(vertical, horizontal / aspect, 0.001) * max(float(margin), 1.0)
    ortho_scale = max(float(explicit_scale), required_scale) if explicit_scale > 0.0 else required_scale
    extent = max((corner - center).length for corner in corners)
    distance = max(extent * 4.0, ortho_scale * 2.0, 1.0)
    camera.location = center + direction * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = ortho_scale
    camera.data.clip_start = max(0.001, distance - extent * 2.0)
    camera.data.clip_end = distance + extent * 2.0
    return ortho_scale


def _render_result_texture(image) -> PixelTexture:
    width, height = (int(image.size[0]), int(image.size[1]))
    if width < 1 or height < 1:
        raise RuntimeError("Blender returned an empty render result")
    raw = tuple(float(value) for value in image.pixels)
    row_size = width * 4
    if len(raw) != width * height * 4:
        raise RuntimeError("Blender returned an incomplete render result")
    top_left = tuple(
        channel
        for row in reversed(range(height))
        for channel in raw[row * row_size:(row + 1) * row_size]
    )
    return PixelTexture.from_rgba(width, height, top_left)


def _render_dependencies(scene: bpy.types.Scene, source_obj, animation_owner) -> frozenset:
    allowed = {source_obj}
    if animation_owner is not None:
        allowed.add(animation_owner)
    parent = getattr(source_obj, "parent", None)
    while parent is not None:
        allowed.add(parent)
        parent = getattr(parent, "parent", None)
    for modifier in getattr(source_obj, "modifiers", ()):
        if getattr(modifier, "type", "") == "ARMATURE" and getattr(modifier, "object", None) is not None:
            allowed.add(modifier.object)
    return frozenset(allowed)


def _render_frames(
    context: bpy.types.Context,
    source_obj,
    frame_numbers: tuple[int, ...],
    *,
    frame_width: int,
    frame_height: int,
    view: str,
    margin: float,
    explicit_scale: float,
    animation_owner,
) -> tuple[tuple[PixelTexture, ...], float]:
    scene = context.scene
    if frame_width < 1 or frame_height < 1:
        raise ValueError("spritesheet frame dimensions must be positive")
    selected_camera = scene.camera
    frame_before = int(scene.frame_current)
    subframe_before = float(getattr(scene, "frame_subframe", 0.0))
    render = scene.render
    render_state = {
        "engine": render.engine,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "film_transparent": render.film_transparent,
        "file_format": render.image_settings.file_format,
    }
    shading = getattr(getattr(scene, "display", None), "shading", None)
    shading_state = {}
    for name in ("light", "color_type", "show_shadows", "show_cavity"):
        if shading is not None and hasattr(shading, name):
            shading_state[name] = getattr(shading, name)
    hide_states = {obj: bool(obj.hide_render) for obj in scene.objects}
    camera_data = bpy.data.cameras.new("VOXTART Spritesheet Camera Data")
    camera = bpy.data.objects.new("VOXTART Spritesheet Camera", camera_data)
    scene.collection.objects.link(camera)
    allowed = _render_dependencies(scene, source_obj, animation_owner)
    frames = []
    used_scale = 0.0
    try:
        for obj in scene.objects:
            obj.hide_render = obj not in allowed and obj is not camera
        source_obj.hide_render = False
        render.engine = "BLENDER_WORKBENCH"
        render.resolution_x = int(frame_width)
        render.resolution_y = int(frame_height)
        render.resolution_percentage = 100
        render.film_transparent = True
        render.image_settings.file_format = "PNG"
        scene.camera = camera
        all_corners = []
        for frame_number in frame_numbers:
            scene.frame_set(frame_number)
            depsgraph = context.evaluated_depsgraph_get()
            all_corners.extend(_world_bounds(source_obj, depsgraph)[2])
        if not all_corners:
            raise RuntimeError("the voxel asset has no renderable bounds")
        stable_minimum = Vector((
            min(corner.x for corner in all_corners),
            min(corner.y for corner in all_corners),
            min(corner.z for corner in all_corners),
        ))
        stable_maximum = Vector((
            max(corner.x for corner in all_corners),
            max(corner.y for corner in all_corners),
            max(corner.z for corner in all_corners),
        ))
        stable_bounds = (stable_minimum, stable_maximum, tuple(all_corners))
        for frame_number in frame_numbers:
            scene.frame_set(frame_number)
            depsgraph = context.evaluated_depsgraph_get()
            used_scale = _configure_camera(
                camera,
                source_obj,
                depsgraph,
                view,
                frame_width,
                frame_height,
                margin,
                explicit_scale,
                bounds=stable_bounds,
            )
            bpy.ops.render.render(write_still=False)
            render_result = bpy.data.images.get("Render Result")
            if render_result is None:
                raise RuntimeError("Blender did not produce a render result")
            frames.append(_render_result_texture(render_result))
    finally:
        scene.camera = selected_camera
        scene.frame_set(frame_before, subframe=subframe_before)
        render.engine = render_state["engine"]
        render.resolution_x = render_state["resolution_x"]
        render.resolution_y = render_state["resolution_y"]
        render.resolution_percentage = render_state["resolution_percentage"]
        render.film_transparent = render_state["film_transparent"]
        render.image_settings.file_format = render_state["file_format"]
        for name, value in shading_state.items():
            setattr(shading, name, value)
        for obj, hidden in hide_states.items():
            try:
                obj.hide_render = hidden
            except (ReferenceError, RuntimeError):
                pass
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(camera_data)
    return tuple(frames), used_scale


def _image_from_texture(texture: PixelTexture, name: str):
    image = bpy.data.images.get(name)
    if image is None:
        image = bpy.data.images.new(name, width=texture.width, height=texture.height, alpha=True)
    elif tuple(image.size) != (texture.width, texture.height):
        image.scale(texture.width, texture.height)
    values = tuple(
        channel
        for row in reversed(range(texture.height))
        for pixel in texture.pixels[row * texture.width:(row + 1) * texture.width]
        for channel in pixel
    )
    image.pixels.foreach_set(values)
    image.update()
    return image


def _write_metadata(filepath: str, metadata: dict[str, object]) -> Path:
    path = Path(filepath).with_suffix(".json")
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return path


class VOXTART_OT_export_spritesheet(bpy.types.Operator, ExportHelper):
    bl_idname = "voxtart.export_spritesheet"
    bl_label = "Export Voxel Spritesheet"
    bl_description = "Render animation frames of the active voxel asset into a static PNG spritesheet"
    bl_options = {"REGISTER"}

    filename_ext = ".png"
    check_extension = True
    filter_glob: StringProperty(default="*.png", options={"HIDDEN"})

    start_frame: IntProperty(name="Start Frame", default=1)
    end_frame: IntProperty(name="End Frame", default=24)
    frame_step: IntProperty(name="Frame Step", min=1, max=1024, default=1)
    frame_width: IntProperty(name="Frame Width", min=1, max=2048, default=128)
    frame_height: IntProperty(name="Frame Height", min=1, max=2048, default=128)
    columns: IntProperty(name="Columns", min=0, max=256, default=0)
    spacing: IntProperty(name="Frame Spacing", min=0, max=256, default=0)
    padding: IntProperty(name="Atlas Padding", min=0, max=256, default=0)
    view: EnumProperty(name="View", items=_VIEW_ITEMS, default="ISOMETRIC")
    margin: FloatProperty(name="Camera Margin", min=1.0, max=4.0, default=1.15)
    orthographic_scale: FloatProperty(name="Orthographic Scale", min=0.0, max=100000.0, default=0.0)
    action: StringProperty(name="Action", default="", options={"HIDDEN"})
    write_metadata: BoolProperty(name="Write JSON Metadata", default=True)
    keep_image: BoolProperty(name="Keep Blender Image", default=True)

    @classmethod
    def poll(cls, context):
        return _active_asset(context) is not None

    def execute(self, context):
        source_obj = _active_asset(context)
        if source_obj is None:
            self.report({"ERROR"}, "select a VOXTART voxel asset to export")
            return {"CANCELLED"}
        animation_owner = None
        action = None
        previous_action = None
        metadata_path = None
        result = None
        frame_numbers = ()
        try:
            frame_numbers = sample_frame_range(self.start_frame, self.end_frame, self.frame_step)
            animation_owner, action = _resolve_animation(context.scene, source_obj, self.action)
            if animation_owner is not None:
                animation_data = getattr(animation_owner, "animation_data", None)
                previous_action = getattr(animation_data, "action", None) if animation_data else None
                if action is not None and previous_action != action:
                    animation_owner.animation_data_create()
                    animation_owner.animation_data.action = action
            frames, camera_scale = _render_frames(
                context,
                source_obj,
                frame_numbers,
                frame_width=self.frame_width,
                frame_height=self.frame_height,
                view=self.view,
                margin=self.margin,
                explicit_scale=self.orthographic_scale,
                animation_owner=animation_owner,
            )
            result = compose_spritesheet(
                frames,
                scene_frames=frame_numbers,
                columns=self.columns,
                spacing=self.spacing,
                padding=self.padding,
            )
            metadata = result.to_dict()
            metadata.update(
                {
                    "source_object": source_obj.name,
                    "action": action.name if action is not None else "",
                    "view": self.view,
                    "camera_scale": camera_scale,
                    "frame_start": frame_numbers[0],
                    "frame_end": frame_numbers[-1],
                    "frame_step": self.frame_step,
                }
            )
            image_name = f"VOXTART Spritesheet {Path(self.filepath).stem}"
            image = _image_from_texture(result.texture, image_name)
            image.filepath_raw = str(Path(self.filepath).resolve())
            image.file_format = "PNG"
            image.save()
            metadata_path = _write_metadata(self.filepath, metadata) if self.write_metadata else None
            source_obj["voxtart_spritesheet_report"] = json.dumps(metadata, sort_keys=True)
            if not self.keep_image:
                bpy.data.images.remove(image)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        finally:
            if animation_owner is not None and action is not None:
                animation_owner.animation_data_create()
                animation_owner.animation_data.action = previous_action
        sidecar = f" + {metadata_path.name}" if metadata_path is not None else ""
        self.report(
            {"INFO"},
            f"exported {len(frame_numbers)} frame(s) to {Path(self.filepath).name}"
            f" ({result.layout.width}×{result.layout.height}){sidecar}",
        )
        return {"FINISHED"}


class VOXTART_PT_spritesheet_export(bpy.types.Panel):
    bl_label = "Spritesheet Export"
    bl_idname = "VOXTART_PT_spritesheet_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 35

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        source = _active_asset(context)
        if source is None:
            layout.label(text="Select a VOXTART voxel asset to export.", icon="INFO")
            return
        layout.label(text="Render animation frames as static PNG tiles", icon="TEXTURE")
        layout.prop_search(scene, "voxtart_spritesheet_action", bpy.data, "actions", text="Action")
        frame_box = layout.box()
        frame_box.label(text="Frame Range")
        frame_box.prop(scene, "voxtart_spritesheet_start_frame", text="Start")
        frame_box.prop(scene, "voxtart_spritesheet_end_frame", text="End")
        frame_box.prop(scene, "voxtart_spritesheet_frame_step", text="Step")
        size_box = layout.box()
        size_box.label(text="Tile and Atlas")
        row = size_box.row(align=True)
        row.prop(scene, "voxtart_spritesheet_frame_width", text="Width")
        row.prop(scene, "voxtart_spritesheet_frame_height", text="Height")
        size_box.prop(scene, "voxtart_spritesheet_columns", text="Columns (0 = Auto)")
        row = size_box.row(align=True)
        row.prop(scene, "voxtart_spritesheet_spacing", text="Spacing")
        row.prop(scene, "voxtart_spritesheet_padding", text="Padding")
        camera_box = layout.box()
        camera_box.label(text="Camera")
        camera_box.prop(scene, "voxtart_spritesheet_view", text="View")
        camera_box.prop(scene, "voxtart_spritesheet_margin", text="Margin")
        camera_box.prop(scene, "voxtart_spritesheet_orthographic_scale", text="Ortho Scale (0 = Auto)")
        layout.prop(scene, "voxtart_spritesheet_write_metadata", text="Write JSON Metadata")
        layout.prop(scene, "voxtart_spritesheet_keep_image", text="Keep Blender Image")
        export = layout.operator(VOXTART_OT_export_spritesheet.bl_idname, text="Export Spritesheet", icon="EXPORT")
        export.start_frame = scene.voxtart_spritesheet_start_frame
        export.end_frame = scene.voxtart_spritesheet_end_frame
        export.frame_step = scene.voxtart_spritesheet_frame_step
        export.frame_width = scene.voxtart_spritesheet_frame_width
        export.frame_height = scene.voxtart_spritesheet_frame_height
        export.columns = scene.voxtart_spritesheet_columns
        export.spacing = scene.voxtart_spritesheet_spacing
        export.padding = scene.voxtart_spritesheet_padding
        export.view = scene.voxtart_spritesheet_view
        export.margin = scene.voxtart_spritesheet_margin
        export.orthographic_scale = scene.voxtart_spritesheet_orthographic_scale
        export.action = scene.voxtart_spritesheet_action
        export.write_metadata = scene.voxtart_spritesheet_write_metadata
        export.keep_image = scene.voxtart_spritesheet_keep_image


_CLASSES = (
    VOXTART_OT_export_spritesheet,
    VOXTART_PT_spritesheet_export,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_spritesheet_action = StringProperty(
        name="Spritesheet Action",
        description="Optional action assigned while frames are rendered",
        default="",
    )
    bpy.types.Scene.voxtart_spritesheet_start_frame = IntProperty(
        name="Spritesheet Start Frame", default=1
    )
    bpy.types.Scene.voxtart_spritesheet_end_frame = IntProperty(
        name="Spritesheet End Frame", default=24
    )
    bpy.types.Scene.voxtart_spritesheet_frame_step = IntProperty(
        name="Spritesheet Frame Step", min=1, max=1024, default=1
    )
    bpy.types.Scene.voxtart_spritesheet_frame_width = IntProperty(
        name="Spritesheet Frame Width", min=1, max=2048, default=128
    )
    bpy.types.Scene.voxtart_spritesheet_frame_height = IntProperty(
        name="Spritesheet Frame Height", min=1, max=2048, default=128
    )
    bpy.types.Scene.voxtart_spritesheet_columns = IntProperty(
        name="Spritesheet Columns", min=0, max=256, default=0
    )
    bpy.types.Scene.voxtart_spritesheet_spacing = IntProperty(
        name="Spritesheet Spacing", min=0, max=256, default=0
    )
    bpy.types.Scene.voxtart_spritesheet_padding = IntProperty(
        name="Spritesheet Padding", min=0, max=256, default=0
    )
    bpy.types.Scene.voxtart_spritesheet_view = EnumProperty(
        name="Spritesheet View", items=_VIEW_ITEMS, default="ISOMETRIC"
    )
    bpy.types.Scene.voxtart_spritesheet_margin = FloatProperty(
        name="Spritesheet Camera Margin", min=1.0, max=4.0, default=1.15
    )
    bpy.types.Scene.voxtart_spritesheet_orthographic_scale = FloatProperty(
        name="Spritesheet Orthographic Scale", min=0.0, max=100000.0, default=0.0
    )
    bpy.types.Scene.voxtart_spritesheet_write_metadata = BoolProperty(
        name="Write Spritesheet Metadata", default=True
    )
    bpy.types.Scene.voxtart_spritesheet_keep_image = BoolProperty(
        name="Keep Spritesheet Image", default=True
    )


def unregister() -> None:
    for name in (
        "voxtart_spritesheet_keep_image",
        "voxtart_spritesheet_write_metadata",
        "voxtart_spritesheet_orthographic_scale",
        "voxtart_spritesheet_margin",
        "voxtart_spritesheet_view",
        "voxtart_spritesheet_padding",
        "voxtart_spritesheet_spacing",
        "voxtart_spritesheet_columns",
        "voxtart_spritesheet_frame_height",
        "voxtart_spritesheet_frame_width",
        "voxtart_spritesheet_frame_step",
        "voxtart_spritesheet_end_frame",
        "voxtart_spritesheet_start_frame",
        "voxtart_spritesheet_action",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "VOXTART_OT_export_spritesheet",
    "VOXTART_PT_spritesheet_export",
    "register",
    "unregister",
]
