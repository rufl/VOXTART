# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Blender UI and operators for non-destructive voxel layers."""


import json

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from .layers import BLEND_MODES, LayerStack
from .operators import (
    ASSET_PROPERTY,
    MESH_BACKEND_OVERRIDE_PROPERTY,
    MESH_BACKEND_PROPERTY,
)
_SURFACE_BACKEND_ITEMS = (
    ("INHERIT", "Object", "Use the object's surface setting"),
    ("GREEDY", "Greedy Blocks", "Keep the active layer blocky"),
    ("MARCHING", "Marching / Round", "Use a smooth rounded marching surface for the active layer"),
)



def _active_stack(context):
    from . import operators

    obj = getattr(context, "active_object", None)
    if obj is None or ASSET_PROPERTY not in obj:
        raise ValueError("select a VOXTART asset first")
    asset = operators._load_asset(obj)
    return obj, asset, operators._load_layer_stack(obj, asset)
def _sync(context, obj, asset, stack) -> None:
    from . import operators

    operators._replace_asset_mesh(obj, asset, layer_stack=stack)
    if getattr(context, "area", None) is not None:
        context.area.tag_redraw()


def _set_scene_controls(scene, layer) -> None:
    scene["voxtart_layer_ui_sync"] = True
    try:
        scene.voxtart_layer_opacity = layer.opacity
        scene.voxtart_layer_blend_mode = layer.blend_mode
        scene.voxtart_layer_surface_backend = layer.surface_backend
    finally:
        scene["voxtart_layer_ui_sync"] = False
class VOXTART_OT_layer_select(bpy.types.Operator):
    bl_idname = "voxtart.layer_select"
    bl_label = "Select Layer"
    bl_description = "Make a voxel layer active"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="Layer", min=0, default=0, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            stack.set_active(self.index)
            _set_scene_controls(context.scene, stack.active)
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_layer_update(bpy.types.Operator):
    bl_idname = "voxtart.layer_update"
    bl_label = "Update Layer"
    bl_description = "Change a layer's visibility, lock, opacity, or blend mode"
    bl_options = {"REGISTER", "UNDO"}

    target: EnumProperty(
        name="Property",
        items=(
            ("VISIBLE", "Visibility", "Change visibility"),
            ("LOCKED", "Lock", "Change edit lock"),
            ("OPACITY", "Opacity", "Change opacity"),
            ("BLEND", "Blend", "Change blend mode"),
        ),
        default="VISIBLE",
        options={"HIDDEN"},
    )
    index: IntProperty(name="Layer", min=0, default=0, options={"HIDDEN"})
    visible: BoolProperty(name="Visible", default=True, options={"HIDDEN"})
    locked: BoolProperty(name="Locked", default=False, options={"HIDDEN"})
    opacity: FloatProperty(name="Opacity", min=0.0, max=1.0, default=1.0, options={"HIDDEN"})
    blend_mode: EnumProperty(name="Blend", items=[(mode, mode.title(), mode.title()) for mode in BLEND_MODES])

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            layer = stack.set_active(self.index)
            if self.target == "VISIBLE":
                layer.visible = self.visible
            elif self.target == "LOCKED":
                layer.locked = self.locked
            elif self.target == "OPACITY":
                layer.opacity = self.opacity
            elif self.target == "BLEND":
                layer.blend_mode = self.blend_mode
            _set_scene_controls(context.scene, layer)
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}
class VOXTART_OT_layer_surface(bpy.types.Operator):
    bl_idname = "voxtart.layer_surface"
    bl_label = "Set Surface"
    bl_description = "Choose rounded marching or blocky greedy meshing for a layer or object"
    bl_options = {"REGISTER", "UNDO"}

    scope: EnumProperty(
        name="Scope",
        items=(
            ("LAYER", "Layer", "Store the surface backend on the active layer"),
            ("OBJECT", "Object", "Override the surface backend for the whole object"),
        ),
        default="LAYER",
        options={"HIDDEN"},
    )
    backend: EnumProperty(name="Surface", items=_SURFACE_BACKEND_ITEMS, default="MARCHING")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            if self.scope == "LAYER":
                stack.active.surface_backend = self.backend
                obj[MESH_BACKEND_OVERRIDE_PROPERTY] = False
            elif self.backend == "INHERIT":
                obj[MESH_BACKEND_OVERRIDE_PROPERTY] = False
            else:
                obj[MESH_BACKEND_PROPERTY] = self.backend.lower()
                obj[MESH_BACKEND_OVERRIDE_PROPERTY] = True
            _set_scene_controls(context.scene, stack.active)
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}



class VOXTART_OT_layer_new(bpy.types.Operator):
    bl_idname = "voxtart.layer_new"
    bl_label = "New Layer"
    bl_description = "Create an empty layer above the active layer"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="Layer")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            stack.add(self.name)
            _set_scene_controls(context.scene, stack.active)
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_layer_duplicate(bpy.types.Operator):
    bl_idname = "voxtart.layer_duplicate"
    bl_label = "Duplicate Layer"
    bl_description = "Duplicate the active layer above it"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            stack.duplicate_active()
            _set_scene_controls(context.scene, stack.active)
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_layer_rename(bpy.types.Operator):
    bl_idname = "voxtart.layer_rename"
    bl_label = "Rename Layer"
    bl_description = "Rename the active layer"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="Layer")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def invoke(self, context, _event):
        try:
            _obj, _asset, stack = _active_stack(context)
            self.name = stack.active.name
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            stack.active.name = self.name.strip()[:128] or "Layer"
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_layer_delete(bpy.types.Operator):
    bl_idname = "voxtart.layer_delete"
    bl_label = "Delete Layer"
    bl_description = "Delete the active layer"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            stack.remove_active()
            _set_scene_controls(context.scene, stack.active)
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_layer_move(bpy.types.Operator):
    bl_idname = "voxtart.layer_move"
    bl_label = "Move Layer"
    bl_description = "Move the active layer up or down"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(
        name="Direction",
        items=(("UP", "Up", "Move toward the top of the stack"), ("DOWN", "Down", "Move toward the bottom of the stack")),
        default="UP",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            stack.move_active(1 if self.direction == "UP" else -1)
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_layer_merge_down(bpy.types.Operator):
    bl_idname = "voxtart.layer_merge_down"
    bl_label = "Merge Down"
    bl_description = "Merge the active layer into the layer below it"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            stack.merge_down(asset.palette)
            _set_scene_controls(context.scene, stack.active)
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_layer_flatten(bpy.types.Operator):
    bl_idname = "voxtart.layer_flatten"
    bl_label = "Flatten Layers"
    bl_description = "Flatten the complete layer stack into one layer"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            obj, asset, stack = _active_stack(context)
            stack.flatten(asset.palette)
            _set_scene_controls(context.scene, stack.active)
            _sync(context, obj, asset, stack)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_PT_layers_panel(bpy.types.Panel):
    bl_label = "Layers"
    bl_idname = "VOXTART_PT_layers_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 6

    def draw(self, context):
        layout = self.layout
        obj = getattr(context, "active_object", None)
        if obj is None or ASSET_PROPERTY not in obj:
            layout.label(text="Select a VOXTART asset")
            return
        try:
            _obj, _asset, stack = _active_stack(context)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            layout.label(text=str(error))
            return
        scene = context.scene
        _set_scene_controls(scene, stack.active)
        controls = layout.row(align=True)
        controls.operator(VOXTART_OT_layer_new.bl_idname, text="", icon="ADD")
        controls.operator(VOXTART_OT_layer_duplicate.bl_idname, text="", icon="DUPLICATE")
        controls.operator(VOXTART_OT_layer_delete.bl_idname, text="", icon="X")
        controls.separator()
        controls.operator(VOXTART_OT_layer_move.bl_idname, text="", icon="TRIA_UP").direction = "UP"
        controls.operator(VOXTART_OT_layer_move.bl_idname, text="", icon="TRIA_DOWN").direction = "DOWN"
        controls.operator(VOXTART_OT_layer_merge_down.bl_idname, text="", icon="SORT_ASC")
        controls.operator(VOXTART_OT_layer_flatten.bl_idname, text="", icon="SEQ_STRIP_META")
        layout.separator()
        for index in reversed(range(len(stack.layers))):
            layer = stack.layers[index]
            row = layout.row(align=True)
            eye = row.operator(
                VOXTART_OT_layer_update.bl_idname,
                text="",
                icon="HIDE_OFF" if layer.visible else "HIDE_ON",
            )
            eye.index = index
            eye.target = "VISIBLE"
            eye.visible = not layer.visible
            select = row.operator(
                VOXTART_OT_layer_select.bl_idname,
                text=layer.name,
                depress=index == stack.active_index,
            )
            select.index = index
            lock = row.operator(
                VOXTART_OT_layer_update.bl_idname,
                text="",
                icon="LOCKED" if layer.locked else "UNLOCKED",
            )
            lock.index = index
            lock.target = "LOCKED"
            lock.locked = not layer.locked
        layout.operator(VOXTART_OT_layer_rename.bl_idname, text="Rename Active Layer", icon="GREASEPENCIL")
        layout.separator()
        layout.prop(scene, "voxtart_layer_opacity", slider=True)
        layout.prop(scene, "voxtart_layer_blend_mode")
        layout.prop(scene, "voxtart_layer_surface_backend", text="Surface")
        surface = layout.row(align=True)
        layer_marching = surface.operator(
            VOXTART_OT_layer_surface.bl_idname,
            text="Layer Round",
            icon="MOD_SMOOTH",
        )
        layer_marching.scope = "LAYER"
        layer_marching.backend = "MARCHING"
        object_marching = surface.operator(
            VOXTART_OT_layer_surface.bl_idname,
            text="Object Round",
            icon="MESH_UVSPHERE",
        )
        object_marching.scope = "OBJECT"
        object_marching.backend = "MARCHING"
        inherit = layout.operator(
            VOXTART_OT_layer_surface.bl_idname,
            text="Use Object Surface",
            icon="FILE_REFRESH",
        )
        inherit.scope = "LAYER"
        inherit.backend = "INHERIT"


_CLASSES = (
    VOXTART_OT_layer_select,
    VOXTART_OT_layer_update,
    VOXTART_OT_layer_surface,
    VOXTART_OT_layer_new,
    VOXTART_OT_layer_duplicate,
    VOXTART_OT_layer_rename,
    VOXTART_OT_layer_delete,
    VOXTART_OT_layer_move,
    VOXTART_OT_layer_merge_down,
    VOXTART_OT_layer_flatten,
    VOXTART_PT_layers_panel,
)


def _update_layer_opacity(scene, _context):
    if scene.get("voxtart_layer_ui_sync"):
        return
    from . import operators

    obj = getattr(bpy.context, "active_object", None)
    if obj is None or ASSET_PROPERTY not in obj:
        return
    try:
        asset = operators._load_asset(obj)
        stack = operators._load_layer_stack(obj, asset)
        stack.active.opacity = scene.voxtart_layer_opacity
        operators._replace_asset_mesh(obj, asset, layer_stack=stack)
    except (TypeError, ValueError, IndexError, json.JSONDecodeError):
        return


def _update_layer_blend(scene, _context):
    if scene.get("voxtart_layer_ui_sync"):
        return
    from . import operators

    obj = getattr(bpy.context, "active_object", None)
    if obj is None or ASSET_PROPERTY not in obj:
        return
    try:
        asset = operators._load_asset(obj)
        stack = operators._load_layer_stack(obj, asset)
        stack.active.blend_mode = scene.voxtart_layer_blend_mode
        operators._replace_asset_mesh(obj, asset, layer_stack=stack)
    except (TypeError, ValueError, IndexError, json.JSONDecodeError):
        return
def _update_layer_surface(scene, _context):
    if scene.get("voxtart_layer_ui_sync"):
        return
    from . import operators

    obj = getattr(bpy.context, "active_object", None)
    if obj is None or ASSET_PROPERTY not in obj:
        return
    try:
        asset = operators._load_asset(obj)
        stack = operators._load_layer_stack(obj, asset)
        stack.active.surface_backend = scene.voxtart_layer_surface_backend
        obj[MESH_BACKEND_OVERRIDE_PROPERTY] = False
        operators._replace_asset_mesh(obj, asset, layer_stack=stack)
    except (TypeError, ValueError, IndexError, json.JSONDecodeError):
        return



def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_layer_opacity = FloatProperty(
        name="Layer Opacity",
        min=0.0,
        max=1.0,
        default=1.0,
        update=_update_layer_opacity,
    )
    bpy.types.Scene.voxtart_layer_blend_mode = EnumProperty(
        name="Layer Blend",
        items=[(mode, mode.title(), mode.title()) for mode in BLEND_MODES],
        default="NORMAL",
        update=_update_layer_blend,
    )
    bpy.types.Scene.voxtart_layer_surface_backend = EnumProperty(
        name="Layer Surface",
        items=_SURFACE_BACKEND_ITEMS,
        default="INHERIT",
        update=_update_layer_surface,
    )


def unregister():
    if hasattr(bpy.types.Scene, "voxtart_layer_surface_backend"):
        del bpy.types.Scene.voxtart_layer_surface_backend
    if hasattr(bpy.types.Scene, "voxtart_layer_blend_mode"):
        del bpy.types.Scene.voxtart_layer_blend_mode
    if hasattr(bpy.types.Scene, "voxtart_layer_opacity"):
        del bpy.types.Scene.voxtart_layer_opacity
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
