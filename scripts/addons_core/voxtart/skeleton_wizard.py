# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Guided blob-assembly-to-skeleton retargeting workflow."""


import json
from typing import Any

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from .operators import ASSET_PROPERTY


_BACKEND_ITEMS = (
    ("AUTO", "Automatic", "Use the optional retargeting add-on when available"),
    ("MWNI", "Animation Retargeting", "Use the optional Animation Retargeting add-on"),
    ("NATIVE", "VOXTART Native", "Use constrained Blender fallback retargeting"),
)


def _active_blob_mesh(context: bpy.types.Context):
    obj = getattr(context, "active_object", None)
    if obj is None or obj.type != "MESH" or ASSET_PROPERTY not in obj:
        return None
    return obj


def _mapping(context: bpy.types.Context) -> list[dict[str, str]]:
    raw = str(getattr(context.scene, "voxtart_blob_wizard_mapping", "") or "").strip()
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"wizard mapping JSON is invalid: {error.msg}") from error
    if not isinstance(decoded, list):
        raise ValueError("wizard mapping must be a list")
    result = []
    for item in decoded:
        if not isinstance(item, dict) or not item.get("source") or not item.get("target"):
            continue
        result.append({
            "source": str(item["source"]),
            "target": str(item["target"]),
            "role": str(item.get("role", "custom")),
        })
    return result


def _write_mapping(context: bpy.types.Context, mapping: list[dict[str, str]]) -> None:
    context.scene.voxtart_blob_wizard_mapping = json.dumps(mapping, sort_keys=True)


def wizard_state(context: bpy.types.Context) -> dict[str, Any]:
    """Return actionable wizard state without invoking a retarget backend."""

    from . import animation

    source_name = str(getattr(context.scene, "voxtart_animation_source", "") or "")
    target_name = str(getattr(context.scene, "voxtart_animation_target", "") or "")
    source = animation._get_object(source_name, "ARMATURE")
    target = animation._get_object(target_name, "ARMATURE")
    mapping = _mapping(context)
    if not mapping and source is not None and target is not None:
        mapping = animation.suggest_bone_mapping(
            animation._armature_bone_names(source),
            animation._armature_bone_names(target),
        )
    return {
        "step": int(getattr(context.scene, "voxtart_blob_wizard_step", 1)),
        "source": source,
        "target": target,
        "action": str(getattr(context.scene, "voxtart_animation_action", "") or ""),
        "mapping": mapping,
        "mapping_count": len(mapping),
    }


class VOXTART_OT_blob_wizard_prepare(bpy.types.Operator):
    bl_idname = "voxtart.blob_wizard_prepare"
    bl_label = "Prepare Blob Character Rig"
    bl_description = "Create an editable target armature for the assembled blob character"
    bl_options = {"REGISTER", "UNDO"}

    bind: BoolProperty(name="Bind Mesh", default=True)

    @classmethod
    def poll(cls, context):
        return _active_blob_mesh(context) is not None

    def execute(self, context):
        from . import animation

        mesh = _active_blob_mesh(context)
        try:
            result = animation.create_blockout_rig(context, mesh, bind=self.bind)
            context.scene.voxtart_animation_target = result["armature"]
            context.scene.voxtart_blob_wizard_step = 2
            context.scene.voxtart_blob_wizard_mapping = ""
        except (RuntimeError, ValueError, TypeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"prepared {result['bone_count']}-bone target rig")
        return {"FINISHED"}


class VOXTART_OT_blob_wizard_suggest(bpy.types.Operator):
    bl_idname = "voxtart.blob_wizard_suggest"
    bl_label = "Suggest Bone Mapping"
    bl_description = "Build a deterministic source-to-target mapping from bone roles and names"

    def execute(self, context):
        from . import animation

        try:
            state = wizard_state(context)
            if state["source"] is None or state["target"] is None:
                raise ValueError("choose both source and target armatures")
            mapping = animation.suggest_bone_mapping(
                animation._armature_bone_names(state["source"]),
                animation._armature_bone_names(state["target"]),
            )
            if not mapping:
                raise ValueError("no bone mapping could be suggested")
            _write_mapping(context, mapping)
            context.scene.voxtart_blob_wizard_step = 3
        except (RuntimeError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"suggested {len(mapping)} bone mappings")
        return {"FINISHED"}


class VOXTART_OT_blob_wizard_retarget(bpy.types.Operator):
    bl_idname = "voxtart.blob_wizard_retarget"
    bl_label = "Retarget and Bake Blob Character"
    bl_description = "Retarget the selected animation onto the assembled character armature"
    bl_options = {"REGISTER", "UNDO"}

    backend: EnumProperty(name="Backend", items=_BACKEND_ITEMS, default="AUTO")
    bake: BoolProperty(name="Bake Action", default=True)

    def execute(self, context):
        from . import animation

        try:
            state = wizard_state(context)
            if state["source"] is None or state["target"] is None:
                raise ValueError("choose both source and target armatures")
            mapping = _mapping(context)
            if not mapping:
                mapping = state["mapping"]
            if not mapping:
                raise ValueError("suggest a bone mapping before retargeting")
            result = animation.retarget_animation(
                context,
                source_name=state["source"].name,
                target_name=state["target"].name,
                action_name=state["action"],
                backend=self.backend,
                bake=self.bake,
                mapping=mapping,
            )
            context.scene.voxtart_blob_wizard_step = 4
        except (RuntimeError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"retargeted {result['action']} using {result['backend']}")
        return {"FINISHED"}


class VOXTART_PT_blob_skeleton_wizard(bpy.types.Panel):
    bl_label = "Blob Skeleton Wizard"
    bl_idname = "VOXTART_PT_blob_skeleton_wizard"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 25

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        state = wizard_state(context)
        layout.label(text="Assemble blobs, fit a target rig, retarget animation.")

        box = layout.box()
        box.label(text="1. Prepare target", icon="ARMATURE_DATA")
        if _active_blob_mesh(context) is not None:
            box.operator(VOXTART_OT_blob_wizard_prepare.bl_idname, text="Create / Fit Target Rig")
        else:
            box.label(text="Select a VOXTART blob asset first", icon="INFO")

        box = layout.box()
        box.label(text="2. Import source and choose action", icon="IMPORT")
        box.operator("voxtart.import_animation_library", text="Import Animation Source")
        box.prop_search(scene, "voxtart_animation_source", bpy.data, "objects", text="Source")
        box.prop_search(scene, "voxtart_animation_action", bpy.data, "actions", text="Action")
        box.prop_search(scene, "voxtart_animation_target", bpy.data, "objects", text="Target")

        box = layout.box()
        box.label(text="3. Map bones", icon="BONE_DATA")
        box.label(text=f"Suggested mappings: {state['mapping_count']}")
        box.operator(VOXTART_OT_blob_wizard_suggest.bl_idname, text="Suggest Mapping")
        if state["mapping"]:
            for item in state["mapping"][:12]:
                box.label(text=f"{item['source']}  →  {item['target']}")
            if len(state["mapping"]) > 12:
                box.label(text=f"… and {len(state['mapping']) - 12} more")

        box = layout.box()
        box.label(text="4. Retarget", icon="CONSTRAINT_BONE")
        row = box.row(align=True)
        row.prop(scene, "voxtart_blob_wizard_backend", text="Backend")
        row.prop(scene, "voxtart_blob_wizard_bake", text="Bake")
        retarget = box.operator(VOXTART_OT_blob_wizard_retarget.bl_idname, text="Retarget and Bake")
        retarget.backend = scene.voxtart_blob_wizard_backend
        retarget.bake = scene.voxtart_blob_wizard_bake
        box.label(text="Optional retarget add-ons are used when enabled.", icon="INFO")


_CLASSES = (
    VOXTART_OT_blob_wizard_prepare,
    VOXTART_OT_blob_wizard_suggest,
    VOXTART_OT_blob_wizard_retarget,
    VOXTART_PT_blob_skeleton_wizard,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_blob_wizard_step = IntProperty(
        name="Wizard Step", min=1, max=4, default=1
    )
    bpy.types.Scene.voxtart_blob_wizard_mapping = StringProperty(
        name="Wizard Bone Mapping", default="", options={"HIDDEN"}
    )
    bpy.types.Scene.voxtart_blob_wizard_backend = EnumProperty(
        name="Wizard Backend", items=_BACKEND_ITEMS, default="AUTO"
    )
    bpy.types.Scene.voxtart_blob_wizard_bake = BoolProperty(
        name="Wizard Bake", default=True
    )


def unregister() -> None:
    for name in (
        "voxtart_blob_wizard_bake",
        "voxtart_blob_wizard_backend",
        "voxtart_blob_wizard_mapping",
        "voxtart_blob_wizard_step",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
