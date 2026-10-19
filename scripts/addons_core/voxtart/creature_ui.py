# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender operator and panel for fast graph-authored creature generation."""

from __future__ import annotations

import json

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

from . import operators
from .blob_gallery import BLOB_JOINTS_PROPERTY, BlobJoint, make_blob_entry
from .creature import CREATURE_TEMPLATES, build_voxel_asset, template_recipe
from .palette import scene_palette


CREATURE_RECIPE_PROPERTY = "voxtart_creature_recipe"
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
_MENU_TARGETS: list[tuple[object, object]] = []


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


def _generate(context, template: str, name: str, grid_size: float, save_to_gallery: bool):
    scene = context.scene
    recipe = template_recipe(template, name=name, grid_size=grid_size)
    result = build_voxel_asset(recipe, palette=scene_palette(scene))
    obj = operators._create_asset_object(
        context,
        recipe.name,
        result.asset,
        backend="marching",
    )
    records = _record_payload(result)
    obj[CREATURE_RECIPE_PROPERTY] = recipe.encode()
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
    if save_to_gallery:
        from .blob_gallery_ui import _gallery, _set_blob_metadata

        entry = make_blob_entry(
            result.asset,
            recipe.name,
            category="Creatures",
            joints=_blob_joints(result),
            tags=("creature", recipe.template.lower()),
            provenance={
                "workflow": "voxtart_creature_builder",
                "template": recipe.template,
                "creature_format": recipe.to_dict()["format"],
            },
        )
        _gallery(context).save(entry)
        _set_blob_metadata(obj, entry)
        scene.voxtart_blob_gallery_selected = entry.blob_id
    return obj, recipe, result


class VOXTART_OT_creature_generate(bpy.types.Operator):
    bl_idname = "voxtart.creature_generate"
    bl_label = "Generate Creature"
    bl_description = "Generate a socketed voxel creature from a deterministic graph template"
    bl_options = {"REGISTER", "UNDO"}

    template: EnumProperty(name="Template", items=_TEMPLATE_ITEMS, default="BIPED")
    name: StringProperty(name="Name", default="")
    grid_size: FloatProperty(name="Grid Size", min=0.1, max=100.0, default=1.0)
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
        save_to_gallery = bool(
            getattr(
                self,
                "save_to_gallery",
                getattr(scene, "voxtart_creature_save_to_gallery", True),
            )
        )
        try:
            _obj, recipe, result = _generate(
                context,
                template,
                name,
                grid_size,
                save_to_gallery,
            )
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Generated {recipe.template.title()} with {len(result.asset.voxels)} voxels "
            f"and {len(result.joints)} joints",
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
        layout.label(text="Graph-authored voxel creatures", icon="ARMATURE_DATA")
        layout.prop(scene, "voxtart_creature_template", text="Template")
        layout.prop(scene, "voxtart_creature_name", text="Name")
        layout.prop(scene, "voxtart_creature_grid_size", text="Grid Size")
        layout.prop(scene, "voxtart_creature_save_to_gallery", text="Save to Zoo")
        operator = layout.operator(
            VOXTART_OT_creature_generate.bl_idname,
            text="Generate Creature",
            icon="MESH_UVSPHERE",
        )
        operator.template = scene.voxtart_creature_template
        operator.name = scene.voxtart_creature_name
        operator.grid_size = scene.voxtart_creature_grid_size
        operator.save_to_gallery = scene.voxtart_creature_save_to_gallery
        layout.label(text="Biped and quadruped templates create labeled parts and sockets.", icon="INFO")


def _menu_object(self, _context):
    self.layout.operator(VOXTART_OT_creature_generate.bl_idname, text="VOXTART Generate Creature")


_CLASSES = (
    VOXTART_OT_creature_generate,
    VOXTART_PT_creature_builder,
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
        "voxtart_creature_grid_size",
        "voxtart_creature_name",
        "voxtart_creature_template",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "CREATURE_JOINTS_PROPERTY",
    "CREATURE_RECIPE_PROPERTY",
    "CREATURE_TEMPLATE_PROPERTY",
    "VOXTART_OT_creature_generate",
    "VOXTART_PT_creature_builder",
    "register",
    "unregister",
]
