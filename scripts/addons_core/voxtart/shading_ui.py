# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Blender operators and controls for whole-voxel shading and dithering."""


import json
from typing import Any

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty

from . import operators
from .layers import LayerStack
from .palette import nearest_palette_index
from .shading import DITHER_AXES, DITHER_BAYER_SIZES, dither_voxels, shade_voxels
from .voxel import VoxelAsset


ASSET_PROPERTY = operators.ASSET_PROPERTY
_EFFECT_LAYER_PREFIX = "VOXTART "
_DITHER_EFFECT_NAME = f"{_EFFECT_LAYER_PREFIX}Dither"

_DITHER_PATTERN_ITEMS = (
    ("CHECKER", "Checker", "Alternate the two colors in a coarse checker pattern"),
    ("BAYER2", "Bayer 2×2", "Use the existing 2×2 ordered Bayer threshold"),
    ("BAYER4", "Bayer 4×4", "Use the existing 4×4 ordered Bayer threshold"),
    ("ORDERED", "Ordered (Bayer)", "Use an ordered Bayer matrix with the selected size"),
    ("CIRCUIT", "Circuit (Bayer + Noise)", "Blend an ordered Bayer threshold with deterministic noise"),
    ("NOISE", "Noise", "Use deterministic coordinate noise"),
)
_BAYER_SIZE_ITEMS = tuple(
    (str(size), f"{size}×{size}", f"Use an ordered {size}×{size} Bayer matrix")
    for size in DITHER_BAYER_SIZES
)


def _is_asset(obj: Any) -> bool:
    return obj is not None and ASSET_PROPERTY in obj


def _selected_assets(context: bpy.types.Context) -> tuple[bpy.types.Object, ...]:
    return tuple(sorted(
        (obj for obj in context.selected_objects if _is_asset(obj)),
        key=lambda obj: obj.name.casefold(),
    ))


def _effect_layer_stack(obj: bpy.types.Object, asset, effect_name: str) -> LayerStack:
    """Keep existing layers and put the voxel result in a visible effect layer."""

    stack = operators._load_layer_stack(obj, asset)
    if len(stack.layers) == 1 and not stack.active.locked:
        stack.active.voxels = dict(asset.voxels)
        return stack

    if stack.layers and stack.layers[-1].name == effect_name and not stack.layers[-1].locked:
        stack.layers[-1].voxels = dict(asset.voxels)
        stack.layers[-1].visible = True
        stack.layers[-1].opacity = 1.0
        stack.layers[-1].blend_mode = "NORMAL"
        stack.active_index = len(stack.layers) - 1
        return stack

    stack.active_index = len(stack.layers) - 1
    layer = stack.add(effect_name)
    layer.voxels = dict(asset.voxels)
    layer.visible = True
    layer.opacity = 1.0
    layer.blend_mode = "NORMAL"
    layer.locked = False
    return stack


def _apply_result(obj: bpy.types.Object, asset, report, effect_name: str) -> None:
    if set(asset.voxels) != set(operators._load_asset(obj).voxels):
        raise RuntimeError("voxel shading changed occupancy; shading must preserve whole voxels")
    stack = _effect_layer_stack(obj, asset, effect_name)
    operators._replace_asset_mesh(obj, asset, layer_stack=stack)
    obj["voxtart_last_shading_report"] = json.dumps({
        **report.to_dict(),
        "object": obj.name,
        "effect_layer": effect_name,
        "whole_voxels": True,
    }, sort_keys=True)

def _copy_asset_with_voxels(asset: VoxelAsset, voxels: dict[tuple[int, int, int], int]) -> VoxelAsset:
    return VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(asset.palette),
        voxels=dict(voxels),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
    )


def _strip_dither_layers(stack: LayerStack) -> bool:
    retained = []
    removed = False
    active_index = stack.active_index
    for index, layer in enumerate(stack.layers):
        if index > 0 and layer.name == _DITHER_EFFECT_NAME:
            if layer.locked:
                raise ValueError("the existing dither layer is locked")
            if index < stack.active_index:
                active_index -= 1
            removed = True
            continue
        retained.append(layer)
    if removed:
        stack.layers = retained
        stack.active_index = min(active_index, len(stack.layers) - 1)
    return removed


def _prepare_dither_source(obj: bpy.types.Object, asset: VoxelAsset) -> tuple[VoxelAsset, LayerStack]:
    stack = operators._load_layer_stack(obj, asset)
    _strip_dither_layers(stack)
    source = _copy_asset_with_voxels(asset, stack.composite(asset.palette))
    return source, stack


def _store_dither_layer(
    obj: bpy.types.Object,
    source: VoxelAsset,
    result: VoxelAsset,
    stack: LayerStack,
    report,
    settings: dict[str, Any],
) -> None:
    if set(result.voxels) != set(source.voxels):
        raise RuntimeError("voxel dithering changed occupancy; dithering must preserve whole voxels")
    stack.active_index = len(stack.layers) - 1
    layer = stack.add(_DITHER_EFFECT_NAME)
    layer.voxels = dict(result.voxels)
    layer.visible = True
    layer.opacity = 1.0
    layer.blend_mode = "NORMAL"
    layer.locked = False
    operators._replace_asset_mesh(obj, result, layer_stack=stack)
    obj["voxtart_dither_settings"] = json.dumps(settings, sort_keys=True)
    obj["voxtart_last_shading_report"] = json.dumps({
        **report.to_dict(),
        "object": obj.name,
        "effect_layer": _DITHER_EFFECT_NAME,
        "whole_voxels": True,
        "nondestructive": True,
    }, sort_keys=True)


def _clear_dither_layer(obj: bpy.types.Object) -> bool:
    asset = operators._load_asset(obj)
    stack = operators._load_layer_stack(obj, asset)
    if not _strip_dither_layers(stack):
        return False
    restored = _copy_asset_with_voxels(asset, stack.composite(asset.palette))
    operators._replace_asset_mesh(obj, restored, layer_stack=stack)
    if "voxtart_dither_settings" in obj:
        del obj["voxtart_dither_settings"]
    return True


def _report_many(operator, operation: str, results: list[tuple[str, Any]], errors: list[str]) -> set[str]:
    changed = sum(result.changed for _name, result in results)
    objects = len(results)
    if errors and not results:
        operator.report({"ERROR"}, "; ".join(errors[:3]))
        return {"CANCELLED"}
    message = f"{operation}: {changed} voxel color changes across {objects} blob(s)"
    if errors:
        message += f"; skipped {len(errors)} object(s)"
        operator.report({"WARNING"}, message)
    else:
        operator.report({"INFO"}, message)
    return {"FINISHED"}


class VOXTART_OT_shade_blobs(bpy.types.Operator):
    bl_idname = "voxtart.shade_blobs"
    bl_label = "Shade Selected Voxels"
    bl_description = "Apply quantized surface lighting to selected blobs without changing voxel occupancy"
    bl_options = {"REGISTER", "UNDO"}

    surface_only: BoolProperty(name="Surface Only", default=True)
    light_direction: FloatVectorProperty(
        name="Light Direction", subtype="DIRECTION", size=3, default=(0.35, -0.45, 0.82)
    )
    ambient: FloatProperty(name="Ambient", min=0.0, max=1.0, default=0.35)
    strength: FloatProperty(name="Strength", min=0.0, max=1.0, default=0.8)
    levels: IntProperty(name="Shade Levels", min=2, max=64, default=4)
    group_size: IntProperty(name="Voxel Group", min=1, max=64, default=1)
    allow_protected: BoolProperty(name="Edit Protected", default=False)

    @classmethod
    def poll(cls, context):
        return bool(_selected_assets(context))

    def execute(self, context):
        results: list[tuple[str, Any]] = []
        errors: list[str] = []
        for obj in _selected_assets(context):
            try:
                asset = operators._load_asset(obj)
                report = shade_voxels(
                    asset,
                    surface_only=self.surface_only,
                    light_direction=self.light_direction,
                    ambient=self.ambient,
                    strength=self.strength,
                    levels=self.levels,
                    group_size=self.group_size,
                    protected=operators._protected_cells(obj),
                    allow_protected=self.allow_protected,
                )
                _apply_result(obj, asset, report, f"{_EFFECT_LAYER_PREFIX}Shade")
                results.append((obj.name, report))
            except (TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
                errors.append(f"{obj.name}: {error}")
        return _report_many(self, "shading", results, errors)


class VOXTART_OT_dither_blobs(bpy.types.Operator):
    bl_idname = "voxtart.dither_blobs"
    bl_label = "Apply Dither Layer"
    bl_description = "Apply an adjustable, non-destructive dither layer above the selected voxel artwork"
    bl_options = {"REGISTER", "UNDO"}

    low_index: IntProperty(name="Low Palette Index", min=0, max=65535, default=0)
    high_index: IntProperty(name="High Palette Index", min=0, max=65535, default=1)
    source_index: IntProperty(name="Source Palette Index", min=-1, max=65535, default=-1)
    density: FloatProperty(name="High Color Density", min=0.0, max=1.0, default=0.5)
    noise_amount: FloatProperty(name="Noise Amount", min=0.0, max=1.0, default=0.0)
    threshold_bias: FloatProperty(name="Threshold Bias", min=-0.5, max=0.5, default=0.0)
    pattern: EnumProperty(
        name="Pattern",
        items=_DITHER_PATTERN_ITEMS,
        default="BAYER4",
    )
    bayer_size: EnumProperty(
        name="Bayer Matrix",
        items=_BAYER_SIZE_ITEMS,
        default="4",
    )
    axis: EnumProperty(
        name="Pattern Plane",
        items=tuple((value, value, f"Dither in the {value} plane") for value in DITHER_AXES),
        default="XY",
    )
    group_size: IntProperty(name="Voxel Group", min=1, max=64, default=1)
    surface_only: BoolProperty(name="Surface Only", default=True)
    allow_protected: BoolProperty(name="Edit Protected", default=False)

    @classmethod
    def poll(cls, context):
        return bool(_selected_assets(context))

    def execute(self, context):
        objects = _selected_assets(context)
        active = context.active_object
        if not _is_asset(active):
            active = objects[0]
        try:
            active_asset = operators._load_asset(active)
            low_color = active_asset.palette[self.low_index]
            high_color = active_asset.palette[self.high_index]
            source_color = active_asset.palette[self.source_index] if self.source_index >= 0 else None
        except (IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, f"active palette: {error}")
            return {"CANCELLED"}

        results: list[tuple[str, Any]] = []
        errors: list[str] = []
        for obj in objects:
            try:
                asset = operators._load_asset(obj)
                source_asset, stack = _prepare_dither_source(obj, asset)
                result_asset = _copy_asset_with_voxels(source_asset, source_asset.voxels)
                low_index = nearest_palette_index(low_color, result_asset.palette)
                high_index = nearest_palette_index(high_color, result_asset.palette)
                source_indices = None
                if source_color is not None:
                    source_indices = (nearest_palette_index(source_color, result_asset.palette),)
                report = dither_voxels(
                    result_asset,
                    low_index,
                    high_index,
                    density=self.density,
                    pattern=self.pattern,
                    axis=self.axis,
                    bayer_size=int(self.bayer_size),
                    noise_amount=self.noise_amount,
                    threshold_bias=self.threshold_bias,
                    group_size=self.group_size,
                    surface_only=self.surface_only,
                    source_indices=source_indices,
                    protected=operators._protected_cells(obj),
                    allow_protected=self.allow_protected,
                )
                _store_dither_layer(
                    obj,
                    source_asset,
                    result_asset,
                    stack,
                    report,
                    {
                        "low_index": self.low_index,
                        "high_index": self.high_index,
                        "source_index": self.source_index,
                        "density": self.density,
                        "pattern": self.pattern,
                        "bayer_size": int(self.bayer_size),
                        "noise_amount": self.noise_amount,
                        "threshold_bias": self.threshold_bias,
                        "axis": self.axis,
                        "group_size": self.group_size,
                        "surface_only": self.surface_only,
                        "allow_protected": self.allow_protected,
                    },
                )
                results.append((obj.name, report))
            except (TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
                errors.append(f"{obj.name}: {error}")
        return _report_many(self, "dithering", results, errors)


class VOXTART_OT_clear_dither(bpy.types.Operator):
    bl_idname = "voxtart.clear_dither"
    bl_label = "Remove Dither Layer"
    bl_description = "Remove the non-destructive dither layer and restore the underlying voxel artwork"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_selected_assets(context))

    def execute(self, context):
        cleared = 0
        errors: list[str] = []
        for obj in _selected_assets(context):
            try:
                if _clear_dither_layer(obj):
                    cleared += 1
            except (TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
                errors.append(f"{obj.name}: {error}")
        if errors and not cleared:
            self.report({"ERROR"}, "; ".join(errors[:3]))
            return {"CANCELLED"}
        message = f"removed dither layer from {cleared} blob(s)"
        if errors:
            message += f"; skipped {len(errors)} object(s)"
            self.report({"WARNING"}, message)
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}


class VOXTART_PT_shading_panel(bpy.types.Panel):
    bl_label = "Voxel Shading"
    bl_idname = "VOXTART_PT_shading_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 18

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        if not _selected_assets(context):
            layout.label(text="Select one or more VOXTART blobs.", icon="INFO")
            return

        layout.label(text="Whole voxels only — occupancy is never subdivided.", icon="INFO")
        box = layout.box()
        box.label(text="Quantized surface shading")
        box.prop(scene, "voxtart_shading_surface_only", text="Surface Only")
        box.prop(scene, "voxtart_shading_light_direction", text="Light")
        box.prop(scene, "voxtart_shading_ambient", text="Ambient")
        box.prop(scene, "voxtart_shading_strength", text="Strength")
        box.prop(scene, "voxtart_shading_levels", text="Levels")
        box.prop(scene, "voxtart_shading_group_size", text="Voxel Group")
        box.prop(scene, "voxtart_shading_allow_protected", text="Edit Protected")
        shade = box.operator(VOXTART_OT_shade_blobs.bl_idname, text="Shade Selected Blobs", icon="LIGHT")
        shade.surface_only = scene.voxtart_shading_surface_only
        shade.light_direction = tuple(scene.voxtart_shading_light_direction)
        shade.ambient = scene.voxtart_shading_ambient
        shade.strength = scene.voxtart_shading_strength
        shade.levels = scene.voxtart_shading_levels
        shade.group_size = scene.voxtart_shading_group_size
        shade.allow_protected = scene.voxtart_shading_allow_protected

        box = layout.box()
        box.label(text="Palette dithering")
        box.label(text="Builds/updates a visible VOXTART Dither layer above the artwork.")
        box.label(text="Low/high colors are taken from the active blob palette.")
        box.prop(scene, "voxtart_dither_low_index", text="Low Index")
        box.prop(scene, "voxtart_dither_high_index", text="High Index")
        box.prop(scene, "voxtart_dither_source_index", text="Source Filter")
        box.prop(scene, "voxtart_dither_density", text="High Density")
        box.prop(scene, "voxtart_dither_pattern", text="Pattern")
        if scene.voxtart_dither_pattern in {"ORDERED", "CIRCUIT"}:
            box.prop(scene, "voxtart_dither_bayer_size", text="Bayer Matrix")
            box.prop(scene, "voxtart_dither_noise_amount", text="Noise Amount")
        box.prop(scene, "voxtart_dither_threshold_bias", text="Threshold Bias")
        box.prop(scene, "voxtart_dither_axis", text="Plane")
        box.prop(scene, "voxtart_dither_group_size", text="Voxel Group")
        box.prop(scene, "voxtart_dither_surface_only", text="Surface Only")
        box.prop(scene, "voxtart_shading_allow_protected", text="Edit Protected")
        row = box.row(align=True)
        dither = row.operator(VOXTART_OT_dither_blobs.bl_idname, text="Apply / Update Dither Layer")
        row.operator(VOXTART_OT_clear_dither.bl_idname, text="Remove", icon="X")
        dither.low_index = scene.voxtart_dither_low_index
        dither.high_index = scene.voxtart_dither_high_index
        dither.source_index = scene.voxtart_dither_source_index
        dither.density = scene.voxtart_dither_density
        dither.pattern = scene.voxtart_dither_pattern
        dither.axis = scene.voxtart_dither_axis
        dither.bayer_size = scene.voxtart_dither_bayer_size
        dither.noise_amount = scene.voxtart_dither_noise_amount
        dither.threshold_bias = scene.voxtart_dither_threshold_bias
        dither.group_size = scene.voxtart_dither_group_size
        dither.surface_only = scene.voxtart_dither_surface_only
        dither.allow_protected = scene.voxtart_shading_allow_protected


_CLASSES = (
    VOXTART_OT_shade_blobs,
    VOXTART_OT_dither_blobs,
    VOXTART_OT_clear_dither,
    VOXTART_PT_shading_panel,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_shading_surface_only = BoolProperty(
        name="Surface Only", default=True
    )
    bpy.types.Scene.voxtart_shading_light_direction = FloatVectorProperty(
        name="Light Direction", subtype="DIRECTION", size=3, default=(0.35, -0.45, 0.82)
    )
    bpy.types.Scene.voxtart_shading_ambient = FloatProperty(
        name="Ambient", min=0.0, max=1.0, default=0.35
    )
    bpy.types.Scene.voxtart_shading_strength = FloatProperty(
        name="Shading Strength", min=0.0, max=1.0, default=0.8
    )
    bpy.types.Scene.voxtart_shading_levels = IntProperty(
        name="Shading Levels", min=2, max=64, default=4
    )
    bpy.types.Scene.voxtart_shading_group_size = IntProperty(
        name="Shading Voxel Group", min=1, max=64, default=1
    )
    bpy.types.Scene.voxtart_shading_allow_protected = BoolProperty(
        name="Edit Protected Voxels", default=False
    )
    bpy.types.Scene.voxtart_dither_low_index = IntProperty(
        name="Dither Low Index", min=0, max=65535, default=0
    )
    bpy.types.Scene.voxtart_dither_high_index = IntProperty(
        name="Dither High Index", min=0, max=65535, default=1
    )
    bpy.types.Scene.voxtart_dither_source_index = IntProperty(
        name="Dither Source Filter", min=-1, max=65535, default=-1
    )
    bpy.types.Scene.voxtart_dither_density = FloatProperty(
        name="Dither Density", min=0.0, max=1.0, default=0.5
    )
    bpy.types.Scene.voxtart_dither_noise_amount = FloatProperty(
        name="Dither Noise Amount", min=0.0, max=1.0, default=0.0
    )
    bpy.types.Scene.voxtart_dither_threshold_bias = FloatProperty(
        name="Dither Threshold Bias", min=-0.5, max=0.5, default=0.0
    )
    bpy.types.Scene.voxtart_dither_pattern = EnumProperty(
        name="Dither Pattern",
        items=_DITHER_PATTERN_ITEMS,
        default="BAYER4",
    )
    bpy.types.Scene.voxtart_dither_bayer_size = EnumProperty(
        name="Dither Bayer Matrix",
        items=_BAYER_SIZE_ITEMS,
        default="4",
    )
    bpy.types.Scene.voxtart_dither_axis = EnumProperty(
        name="Dither Plane", items=tuple((value, value, value) for value in DITHER_AXES), default="XY"
    )
    bpy.types.Scene.voxtart_dither_group_size = IntProperty(
        name="Dither Voxel Group", min=1, max=64, default=1
    )
    bpy.types.Scene.voxtart_dither_surface_only = BoolProperty(
        name="Dither Surface Only", default=True
    )


def unregister() -> None:
    for name in (
        "voxtart_dither_threshold_bias",
        "voxtart_dither_noise_amount",
        "voxtart_dither_surface_only",
        "voxtart_dither_group_size",
        "voxtart_dither_bayer_size",
        "voxtart_dither_axis",
        "voxtart_dither_pattern",
        "voxtart_dither_density",
        "voxtart_dither_source_index",
        "voxtart_dither_high_index",
        "voxtart_dither_low_index",
        "voxtart_shading_allow_protected",
        "voxtart_shading_group_size",
        "voxtart_shading_levels",
        "voxtart_shading_strength",
        "voxtart_shading_ambient",
        "voxtart_shading_light_direction",
        "voxtart_shading_surface_only",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
