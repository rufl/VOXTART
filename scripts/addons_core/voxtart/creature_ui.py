# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender operators and inspectors for modular creature presets."""

from __future__ import annotations

import json

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

from . import operators
from .blob_gallery import BLOB_JOINTS_PROPERTY, BlobJoint, make_blob_entry
from .creature import CREATURE_TEMPLATES, CreatureRecipe, build_voxel_asset, template_recipe
from .creature_presets import (
    CREATURE_INSPECTOR_PRESETS,
    CREATURE_PART_PACKS,
    CreaturePreset,
    apply_creature_preset,
    get_inspector_preset,
    get_parts_pack,
    make_creature_preset,
    preset_from_recipe,
)
from .layers import LayerStack
from .palette import scene_palette


CREATURE_RECIPE_PROPERTY = "voxtart_creature_recipe"
CREATURE_BASE_RECIPE_PROPERTY = "voxtart_creature_base_recipe"
CREATURE_PRESET_PROPERTY = "voxtart_creature_preset"
CREATURE_TEMPLATE_PROPERTY = "voxtart_creature_template"
CREATURE_JOINTS_PROPERTY = "voxtart_creature_joints"
_TEMPLATE_ITEMS = tuple(
    (
        template,
        template.title(),
        "Generate a deterministic graph-authored %s creature" % template.lower(),
    )
    for template in CREATURE_TEMPLATES
)
_PART_PACK_ITEMS = tuple(
    (
        pack_id,
        get_parts_pack(pack_id).name,
        get_parts_pack(pack_id).description,
    )
    for pack_id in CREATURE_PART_PACKS
)
_INSPECTOR_ITEMS = tuple(
    (
        preset_id,
        get_inspector_preset(preset_id).name,
        get_inspector_preset(preset_id).description,
    )
    for preset_id in CREATURE_INSPECTOR_PRESETS
)
_MENU_TARGETS: list[tuple[object, object]] = []


def _is_asset(obj) -> bool:
    return obj is not None and operators.ASSET_PROPERTY in obj


def _record_payload(result) -> list[dict[str, object]]:
    return [record.to_dict() for record in result.joints]


def _blob_joints(result) -> tuple[BlobJoint, ...]:
    return tuple(
        BlobJoint(
            name=record.name,
            role=record.role,
            bone=record.bone,
            socket=result.asset.sockets[record.socket_index],
        )
        for record in result.joints
    )


def _store_generated_metadata(
    obj,
    base_recipe: CreatureRecipe,
    recipe: CreatureRecipe,
    preset: CreaturePreset,
    result,
) -> None:
    records = _record_payload(result)
    obj[CREATURE_RECIPE_PROPERTY] = recipe.encode()
    obj[CREATURE_BASE_RECIPE_PROPERTY] = base_recipe.encode()
    obj[CREATURE_PRESET_PROPERTY] = preset.encode()
    obj[CREATURE_TEMPLATE_PROPERTY] = recipe.template
    obj[CREATURE_JOINTS_PROPERTY] = json.dumps(records, sort_keys=True)
    obj[BLOB_JOINTS_PROPERTY] = json.dumps(
        [
            {
                "name": record["name"],
                "role": record["role"],
                "bone": record["bone"],
                "socket_index": record["socket_index"],
            }
            for record in records
        ],
        sort_keys=True,
    )


def _generate(
    context,
    template: str,
    name: str,
    grid_size: float,
    parts_pack: str,
    inspector_preset: str,
    save_to_gallery: bool,
):
    scene = context.scene
    base_recipe = template_recipe(template, name=name, grid_size=grid_size)
    preset = make_creature_preset(
        base_recipe.template,
        parts_pack=parts_pack,
        inspector_preset=inspector_preset,
        name=f"{base_recipe.name} Preset",
    )
    recipe = apply_creature_preset(base_recipe, preset)
    result = build_voxel_asset(recipe, palette=scene_palette(scene))
    obj = operators._create_asset_object(
        context,
        recipe.name,
        result.asset,
        backend="marching",
    )
    _store_generated_metadata(obj, base_recipe, recipe, preset, result)
    if save_to_gallery:
        from .blob_gallery_ui import _gallery, _set_blob_metadata

        entry = make_blob_entry(
            result.asset,
            recipe.name,
            category="Creatures",
            joints=_blob_joints(result),
            tags=("creature", recipe.template.lower(), parts_pack.lower(), inspector_preset.lower()),
            provenance={
                "workflow": "voxtart_creature_builder",
                "template": recipe.template,
                "parts_pack": parts_pack,
                "inspector_preset": inspector_preset,
                "creature_format": recipe.to_dict()["format"],
            },
        )
        _gallery(context).save(entry)
        _set_blob_metadata(obj, entry)
        scene.voxtart_blob_gallery_selected = entry.blob_id
    return obj, base_recipe, recipe, preset, result


def _apply_to_active(context, parts_pack: str, inspector_preset: str):
    obj = getattr(context, "active_object", None)
    if not _is_asset(obj):
        raise ValueError("activate a generated creature before applying a preset")
    raw_base = obj.get(CREATURE_BASE_RECIPE_PROPERTY)
    if isinstance(raw_base, str) and raw_base.strip():
        base_recipe = CreatureRecipe.decode(raw_base)
    else:
        raw_recipe = obj.get(CREATURE_RECIPE_PROPERTY)
        if not isinstance(raw_recipe, str) or not raw_recipe.strip():
            raise ValueError("active asset does not contain a creature recipe")
        base_recipe = CreatureRecipe.decode(raw_recipe)
    preset = make_creature_preset(
        base_recipe.template,
        parts_pack=parts_pack,
        inspector_preset=inspector_preset,
        name=f"{base_recipe.name} Preset",
    )
    recipe = apply_creature_preset(base_recipe, preset)
    result = build_voxel_asset(recipe, palette=scene_palette(context.scene))
    backend = str(obj.get(operators.MESH_BACKEND_PROPERTY, "marching")).lower()
    if backend not in {"greedy", "marching"}:
        backend = "marching"
    operators._replace_asset_mesh(
        obj,
        result.asset,
        backend=backend,
        layer_stack=LayerStack.from_asset(result.asset),
    )
    _store_generated_metadata(obj, base_recipe, recipe, preset, result)
    return obj, base_recipe, recipe, preset, result


def _draw_preset_summary(layout, recipe: CreatureRecipe, preset: CreaturePreset) -> None:
    pack = get_parts_pack(preset.parts_pack)
    inspector = get_inspector_preset(preset.inspector_preset)
    box = layout.box()
    box.label(text=f"Parts Pack: {pack.name}", icon="ASSET_MANAGER")
    box.label(text=pack.description, icon="INFO")
    box.label(text=f"Inspector: {inspector.name}", icon="PROPERTIES")
    box.label(text=inspector.description, icon="INFO")
    box.label(
        text=(
            f"{len(recipe.nodes)} nodes · {len(recipe.edges)} edges · "
            f"{len(recipe.joints)} joints"
        ),
        icon="MESH_CUBE",
    )
    parts = recipe.settings.get("preset_parts", ())
    if parts:
        box.label(text="Parts: " + ", ".join(str(part) for part in parts), icon="MOD_ARRAY")


class VOXTART_OT_creature_generate(bpy.types.Operator):
    bl_idname = "voxtart.creature_generate"
    bl_label = "Generate Creature"
    bl_description = "Generate a socketed voxel creature from a modular graph preset"
    bl_options = {"REGISTER", "UNDO"}

    template: EnumProperty(name="Template", items=_TEMPLATE_ITEMS, default="BIPED")
    name: StringProperty(name="Name", default="")
    grid_size: FloatProperty(name="Grid Size", min=0.1, max=100.0, default=1.0)
    parts_pack: EnumProperty(name="Parts Pack", items=_PART_PACK_ITEMS, default="NATURAL")
    inspector_preset: EnumProperty(
        name="Inspector Preset",
        items=_INSPECTOR_ITEMS,
        default="BALANCED",
    )
    save_to_gallery: BoolProperty(name="Save to Zoo", default=True)

    def execute(self, context):
        scene = context.scene
        template = str(getattr(self, "template", getattr(scene, "voxtart_creature_template", "BIPED")))
        name = str(getattr(self, "name", getattr(scene, "voxtart_creature_name", "")) or "").strip()
        if not name:
            name = f"VOXTART {template.title()}"
        grid_size = float(
            getattr(self, "grid_size", getattr(scene, "voxtart_creature_grid_size", 1.0))
        )
        parts_pack = str(
            getattr(self, "parts_pack", getattr(scene, "voxtart_creature_part_pack", "NATURAL"))
        )
        inspector_preset = str(
            getattr(
                self,
                "inspector_preset",
                getattr(scene, "voxtart_creature_inspector_preset", "BALANCED"),
            )
        )
        save_to_gallery = bool(
            getattr(
                self,
                "save_to_gallery",
                getattr(scene, "voxtart_creature_save_to_gallery", True),
            )
        )
        try:
            _obj, _base, recipe, preset, result = _generate(
                context,
                template,
                name,
                grid_size,
                parts_pack,
                inspector_preset,
                save_to_gallery,
            )
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Generated {recipe.template.title()} / {preset.parts_pack.title()} with "
            f"{len(result.asset.voxels)} voxels and {len(result.joints)} joints",
        )
        return {"FINISHED"}


class VOXTART_OT_creature_apply_preset(bpy.types.Operator):
    bl_idname = "voxtart.creature_apply_preset"
    bl_label = "Apply Creature Preset"
    bl_description = "Replace the active creature's parts pack and inspector profile"
    bl_options = {"REGISTER", "UNDO"}

    parts_pack: EnumProperty(name="Parts Pack", items=_PART_PACK_ITEMS, default="NATURAL")
    inspector_preset: EnumProperty(
        name="Inspector Preset",
        items=_INSPECTOR_ITEMS,
        default="BALANCED",
    )

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        return _is_asset(obj) and bool(obj.get(CREATURE_RECIPE_PROPERTY))

    def execute(self, context):
        try:
            _obj, _base, recipe, preset, result = _apply_to_active(
                context,
                str(self.parts_pack),
                str(self.inspector_preset),
            )
        except (TypeError, ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Applied {preset.parts_pack.title()} / {preset.inspector_preset.title()} "
            f"to {recipe.name} ({len(result.asset.voxels)} voxels)",
        )
        return {"FINISHED"}


class VOXTART_PT_creature_builder(bpy.types.Panel):
    bl_label = "Creature Builder"
    bl_idname = "VOXTART_PT_creature_builder"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 18

    @classmethod
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.label(text="Modular creature presets", icon="ARMATURE_DATA")
        layout.prop(scene, "voxtart_creature_template", text="Template")
        layout.prop(scene, "voxtart_creature_name", text="Name")
        layout.prop(scene, "voxtart_creature_grid_size", text="Grid Size")
        layout.prop(scene, "voxtart_creature_part_pack", text="Parts Pack")
        layout.prop(scene, "voxtart_creature_inspector_preset", text="Inspector")
        layout.prop(scene, "voxtart_creature_save_to_gallery", text="Save to Zoo")
        operator = layout.operator(
            VOXTART_OT_creature_generate.bl_idname,
            text="Generate Creature",
            icon="MESH_UVSPHERE",
        )
        operator.template = scene.voxtart_creature_template
        operator.name = scene.voxtart_creature_name
        operator.grid_size = scene.voxtart_creature_grid_size
        operator.parts_pack = scene.voxtart_creature_part_pack
        operator.inspector_preset = scene.voxtart_creature_inspector_preset
        operator.save_to_gallery = scene.voxtart_creature_save_to_gallery
        pack = get_parts_pack(scene.voxtart_creature_part_pack)
        inspector = get_inspector_preset(scene.voxtart_creature_inspector_preset)
        layout.label(text=f"{len(pack.parts)} original modular parts", icon="ASSET_MANAGER")
        layout.label(text=inspector.description, icon="INFO")


class VOXTART_PT_creature_inspector(bpy.types.Panel):
    bl_label = "Creature Inspector"
    bl_idname = "VOXTART_PT_creature_inspector"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 19

    @classmethod
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        active = getattr(context, "active_object", None)
        if not _is_asset(active) or not active.get(CREATURE_RECIPE_PROPERTY):
            layout.label(text="Select a generated creature to inspect it.", icon="INFO")
            return
        try:
            recipe = CreatureRecipe.decode(active[CREATURE_RECIPE_PROPERTY])
            preset = CreaturePreset.decode(active[CREATURE_PRESET_PROPERTY])
            _draw_preset_summary(layout, recipe, preset)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            layout.label(text=f"Creature preset is invalid: {error}", icon="ERROR")
            return
        layout.prop(context.scene, "voxtart_creature_part_pack", text="Parts Pack")
        layout.prop(context.scene, "voxtart_creature_inspector_preset", text="Inspector")
        operator = layout.operator(
            VOXTART_OT_creature_apply_preset.bl_idname,
            text="Apply Preset to Active",
            icon="FILE_REFRESH",
        )
        operator.parts_pack = context.scene.voxtart_creature_part_pack
        operator.inspector_preset = context.scene.voxtart_creature_inspector_preset


def _menu_object(self, _context):
    self.layout.operator(VOXTART_OT_creature_generate.bl_idname, text="VOXTART Generate Creature")


_CLASSES = (
    VOXTART_OT_creature_generate,
    VOXTART_OT_creature_apply_preset,
    VOXTART_PT_creature_builder,
    VOXTART_PT_creature_inspector,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_creature_template = EnumProperty(
        name="Creature Template",
        items=_TEMPLATE_ITEMS,
        default="BIPED",
    )
    bpy.types.Scene.voxtart_creature_name = StringProperty(
        name="Creature Name",
        default="VOXTART Biped",
    )
    bpy.types.Scene.voxtart_creature_grid_size = FloatProperty(
        name="Creature Grid Size",
        min=0.1,
        max=100.0,
        default=1.0,
    )
    bpy.types.Scene.voxtart_creature_part_pack = EnumProperty(
        name="Creature Parts Pack",
        items=_PART_PACK_ITEMS,
        default="NATURAL",
    )
    bpy.types.Scene.voxtart_creature_inspector_preset = EnumProperty(
        name="Creature Inspector Preset",
        items=_INSPECTOR_ITEMS,
        default="BALANCED",
    )
    bpy.types.Scene.voxtart_creature_save_to_gallery = BoolProperty(
        name="Save Creature to Zoo",
        default=True,
    )
    menu = getattr(bpy.types, "VIEW3D_MT_object", None)
    if menu is not None:
        menu.append(_menu_object)
        _MENU_TARGETS.append((menu, _menu_object))


def unregister() -> None:
    for menu, callback in reversed(_MENU_TARGETS):
        menu.remove(callback)
    _MENU_TARGETS.clear()
    for property_name in (
        "voxtart_creature_save_to_gallery",
        "voxtart_creature_inspector_preset",
        "voxtart_creature_part_pack",
        "voxtart_creature_grid_size",
        "voxtart_creature_name",
        "voxtart_creature_template",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "CREATURE_BASE_RECIPE_PROPERTY",
    "CREATURE_JOINTS_PROPERTY",
    "CREATURE_PRESET_PROPERTY",
    "CREATURE_RECIPE_PROPERTY",
    "CREATURE_TEMPLATE_PROPERTY",
    "VOXTART_OT_creature_apply_preset",
    "VOXTART_OT_creature_generate",
    "VOXTART_PT_creature_builder",
    "VOXTART_PT_creature_inspector",
    "register",
    "unregister",
]
