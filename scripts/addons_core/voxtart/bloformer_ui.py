# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender operator and panel for volume-based blob deformation."""


import json

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, IntVectorProperty

from . import operators
from .bloformer import BLOFORMER_AXES, BLOFORMER_MODES, BLOFORMER_SHAPES, apply_bloformer
from .voxel import VoxelAsset


_MODE_ITEMS = (
    ("ADD", "Add", "Add the selected volume without overwriting occupied voxels"),
    ("SUBTRACT", "Subtract", "Remove every occupied voxel inside the selected volume"),
    ("CARVE", "Carve", "Remove the outside shell of the selected volume for a hollow cut"),
    ("INFLATE", "Inflate", "Grow the blob into empty cells touched by the selected volume"),
    ("ERODE", "Erode", "Remove exposed blob cells inside the selected volume"),
    ("SMOOTH", "Smooth", "Apply a six-neighbor majority pass inside the selected volume"),
)
_SHAPE_ITEMS = (
    ("SPHERE", "Sphere", "Spherical volume centered on the brush position"),
    ("BOX", "Box", "Axis-aligned cubic volume centered on the brush position"),
    ("CYLINDER", "Cylinder", "Cylindrical volume along the selected axis"),
)
_AXIS_ITEMS = tuple(
    (axis, axis, f"Align a cylindrical Bloformer volume to {axis}")
    for axis in BLOFORMER_AXES
)
_MENU_TARGETS: list[tuple[object, object]] = []


def _is_asset(obj) -> bool:
    return obj is not None and operators.ASSET_PROPERTY in obj


def _apply_to_active(context, settings: dict):
    obj = getattr(context, "active_object", None)
    if not _is_asset(obj):
        raise ValueError("activate a VOXTART blob before using Bloformer")
    asset = operators._load_asset(obj)
    stack = operators._load_layer_stack(obj, asset)
    if stack.active.locked:
        raise ValueError(f"active layer is locked: {stack.active.name}")
    editable = VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(asset.palette),
        voxels=dict(stack.active.voxels),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
        palette_materials=asset.get_palette_materials(),
    )
    report = apply_bloformer(
        editable,
        protected=operators._protected_cells(obj),
        **settings,
    )
    stack.active.voxels = dict(editable.voxels)
    operators._replace_asset_mesh(obj, asset, layer_stack=stack)
    encoded_report = json.dumps(report.to_dict(), sort_keys=True)
    obj["voxtart_bloformer_report"] = encoded_report
    obj["voxtart_edit_report"] = encoded_report
    return report


class VOXTART_OT_bloformer(bpy.types.Operator):
    bl_idname = "voxtart.bloformer"
    bl_label = "Apply Bloformer"
    bl_description = "Add, subtract, carve, inflate, erode, or smooth a volume on the active blob"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(name="Mode", items=_MODE_ITEMS, default="ADD")
    shape: EnumProperty(name="Shape", items=_SHAPE_ITEMS, default="SPHERE")
    center: IntVectorProperty(name="Center", size=3, default=(0, 0, 0), min=-4096, max=4096)
    radius: IntProperty(name="Radius", min=1, max=512, default=3)
    thickness: FloatProperty(name="Carve Thickness", min=0.1, max=512.0, default=1.0)
    axis: EnumProperty(name="Axis", items=_AXIS_ITEMS, default="Z")
    palette_index: IntProperty(name="Palette Index", min=0, max=65535, default=0)
    overwrite: BoolProperty(
        name="Overwrite Existing",
        description="Allow Add mode to repaint occupied cells with the selected palette color",
        default=False,
    )
    allow_protected: BoolProperty(name="Edit Protected", default=False)

    @classmethod
    def poll(cls, context):
        return _is_asset(getattr(context, "active_object", None))

    def execute(self, context):
        scene = context.scene
        try:
            report = _apply_to_active(context, {
                "center": tuple(getattr(self, "center", getattr(scene, "voxtart_bloformer_center", (0, 0, 0)))),
                "radius": getattr(self, "radius", getattr(scene, "voxtart_bloformer_radius", 3)),
                "mode": getattr(self, "mode", getattr(scene, "voxtart_bloformer_mode", "ADD")),
                "shape": getattr(self, "shape", getattr(scene, "voxtart_bloformer_shape", "SPHERE")),
                "axis": getattr(self, "axis", getattr(scene, "voxtart_bloformer_axis", "Z")),
                "thickness": getattr(self, "thickness", getattr(scene, "voxtart_bloformer_thickness", 1.0)),
                "palette_index": getattr(self, "palette_index", getattr(scene, "voxtart_bloformer_palette_index", 0)),
                "overwrite": getattr(self, "overwrite", getattr(scene, "voxtart_bloformer_overwrite", False)),
                "allow_protected": getattr(self, "allow_protected", getattr(scene, "voxtart_bloformer_allow_protected", False)),
            })
        except (TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"{report.operation}: +{report.added} -{report.removed} "
            f"painted {report.painted}, {report.after_voxels} voxels",
        )
        return {"FINISHED"}


class VOXTART_PT_bloformer(bpy.types.Panel):
    bl_label = "Bloformer"
    bl_idname = "VOXTART_PT_bloformer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 36

    @classmethod
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        active_asset = _is_asset(getattr(context, "active_object", None))
        layout.label(text="Volume deformation for the active blob", icon="MOD_DISPLACE")
        if not active_asset:
            layout.label(text="Select a VOXTART blob to enable deformation.", icon="INFO")
        controls = layout.column()
        controls.enabled = active_asset
        controls.prop(scene, "voxtart_bloformer_mode", text="Mode")
        controls.prop(scene, "voxtart_bloformer_shape", text="Shape")
        controls.prop(scene, "voxtart_bloformer_center", text="Center")
        controls.prop(scene, "voxtart_bloformer_radius", text="Radius")
        if scene.voxtart_bloformer_mode == "CARVE":
            controls.prop(scene, "voxtart_bloformer_thickness", text="Shell Thickness")
        if scene.voxtart_bloformer_shape == "CYLINDER":
            controls.prop(scene, "voxtart_bloformer_axis", text="Axis")
        if scene.voxtart_bloformer_mode in {"ADD", "INFLATE", "SMOOTH"}:
            controls.prop(scene, "voxtart_bloformer_palette_index", text="Palette")
        if scene.voxtart_bloformer_mode == "ADD":
            controls.prop(scene, "voxtart_bloformer_overwrite", text="Overwrite Existing")
        controls.prop(scene, "voxtart_bloformer_allow_protected", text="Edit Protected")
        operation = controls.operator(VOXTART_OT_bloformer.bl_idname, text="Apply Bloformer", icon="MOD_DISPLACE")
        operation.mode = scene.voxtart_bloformer_mode
        operation.shape = scene.voxtart_bloformer_shape
        operation.center = tuple(scene.voxtart_bloformer_center)
        operation.radius = scene.voxtart_bloformer_radius
        operation.thickness = scene.voxtart_bloformer_thickness
        operation.axis = scene.voxtart_bloformer_axis
        operation.palette_index = scene.voxtart_bloformer_palette_index
        operation.overwrite = scene.voxtart_bloformer_overwrite
        operation.allow_protected = scene.voxtart_bloformer_allow_protected
        layout.label(text="Carve removes the primitive's outside shell; Subtract removes its full volume.", icon="INFO")


def _menu_object(self, _context):
    self.layout.operator(VOXTART_OT_bloformer.bl_idname, text="VOXTART Bloformer")


_CLASSES = (
    VOXTART_OT_bloformer,
    VOXTART_PT_bloformer,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_bloformer_mode = EnumProperty(
        name="Bloformer Mode", items=_MODE_ITEMS, default="ADD"
    )
    bpy.types.Scene.voxtart_bloformer_shape = EnumProperty(
        name="Bloformer Shape", items=_SHAPE_ITEMS, default="SPHERE"
    )
    bpy.types.Scene.voxtart_bloformer_center = IntVectorProperty(
        name="Bloformer Center", size=3, default=(0, 0, 0), min=-4096, max=4096
    )
    bpy.types.Scene.voxtart_bloformer_radius = IntProperty(
        name="Bloformer Radius", min=1, max=512, default=3
    )
    bpy.types.Scene.voxtart_bloformer_thickness = FloatProperty(
        name="Bloformer Carve Thickness", min=0.1, max=512.0, default=1.0
    )
    bpy.types.Scene.voxtart_bloformer_axis = EnumProperty(
        name="Bloformer Axis", items=_AXIS_ITEMS, default="Z"
    )
    bpy.types.Scene.voxtart_bloformer_palette_index = IntProperty(
        name="Bloformer Palette Index", min=0, max=65535, default=0
    )
    bpy.types.Scene.voxtart_bloformer_overwrite = BoolProperty(
        name="Bloformer Overwrite", default=False
    )
    bpy.types.Scene.voxtart_bloformer_allow_protected = BoolProperty(
        name="Bloformer Edit Protected", default=False
    )
    menu = getattr(bpy.types, "VIEW3D_MT_object", None)
    if menu is not None:
        menu.append(_menu_object)
        _MENU_TARGETS.append((menu, _menu_object))


def unregister():
    for menu, callback in reversed(_MENU_TARGETS):
        menu.remove(callback)
    _MENU_TARGETS.clear()
    for property_name in (
        "voxtart_bloformer_allow_protected",
        "voxtart_bloformer_overwrite",
        "voxtart_bloformer_palette_index",
        "voxtart_bloformer_axis",
        "voxtart_bloformer_thickness",
        "voxtart_bloformer_radius",
        "voxtart_bloformer_center",
        "voxtart_bloformer_shape",
        "voxtart_bloformer_mode",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "VOXTART_OT_bloformer",
    "VOXTART_PT_bloformer",
    "register",
    "unregister",
]
