# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender UI for assembling socketed voxel character parts."""


from dataclasses import replace

import json
import uuid

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from .character_editor import (
    CHARACTER_SLOTS,
    CharacterPart,
    CharacterRecipe,
)
from .animation import canonical_bone_role
from .operators import ASSET_PROPERTY


CHARACTER_RECIPE_PROPERTY = "voxtart_character_recipe"
CHARACTER_ID_PROPERTY = "voxtart_character_id"
CHARACTER_OBJECT_ID_PROPERTY = "voxtart_character_object_id"
CHARACTER_SLOT_PROPERTY = "voxtart_character_slot"
CHARACTER_BLOB_PROPERTY = "voxtart_character_blob_id"
CHARACTER_ANIMATION_SLOT_PROPERTY = "voxtart_character_animation_slot"
CHARACTER_ANIMATION_BONE_PROPERTY = "voxtart_character_animation_bone"


_SLOT_ITEMS = (
    ("ROOT", "Root", "Main character anchor; required before socketed parts."),
    ("TORSO", "Torso", "Central body part mounted to the root."),
    ("HEAD", "Head", "Head or helmet mounted to the torso."),
    ("ARM_L", "Arm L", "Left arm mounted to the torso."),
    ("ARM_R", "Arm R", "Right arm mounted to the torso."),
    ("LEG_L", "Leg L", "Left leg mounted to the torso."),
    ("LEG_R", "Leg R", "Right leg mounted to the torso."),
    ("ACCESSORY", "Accessory", "Optional accessory mounted to the torso."),
)

_CHARACTER_ANIMATION_ROLES = {
    "ROOT": ("root", "pelvis"),
    "TORSO": ("chest", "spine", "pelvis", "root"),
    "HEAD": ("head", "neck"),
    "ARM_L": ("upper_arm_l", "shoulder_l", "forearm_l", "hand_l"),
    "ARM_R": ("upper_arm_r", "shoulder_r", "forearm_r", "hand_r"),
    "LEG_L": ("upper_leg_l", "lower_leg_l", "foot_l"),
    "LEG_R": ("upper_leg_r", "lower_leg_r", "foot_r"),
    "ACCESSORY": ("chest", "spine", "head", "pelvis"),
}


def _gallery(context):
    from .blob_gallery_ui import _gallery as get_gallery

    return get_gallery(context)


def _gallery_entries(context):
    scene = context.scene
    return _gallery(context).scan(
        getattr(scene, "voxtart_character_search", ""),
        getattr(scene, "voxtart_blob_gallery_category", ""),
        False,
    )


def _new_recipe(scene: bpy.types.Scene) -> CharacterRecipe:
    return CharacterRecipe(
        name=str(
            getattr(scene, "voxtart_character_name", "VOXTART Character")
            or "VOXTART Character"
        )
    )


def _load_recipe(scene: bpy.types.Scene) -> CharacterRecipe:
    encoded = scene.get(CHARACTER_RECIPE_PROPERTY, "")
    if not encoded:
        return _new_recipe(scene)
    try:
        return CharacterRecipe.decode(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError(f"saved character recipe is invalid: {error}") from error


def _save_recipe(scene: bpy.types.Scene, recipe: CharacterRecipe) -> None:
    scene[CHARACTER_RECIPE_PROPERTY] = recipe.encode()
    scene.voxtart_character_name = recipe.name


def _character_id(scene: bpy.types.Scene) -> str:
    value = str(scene.get(CHARACTER_ID_PROPERTY, "") or "").strip()
    if not value:
        value = uuid.uuid4().hex
        scene[CHARACTER_ID_PROPERTY] = value
    return value


def _scene_name(scene: bpy.types.Scene, recipe: CharacterRecipe) -> str:
    name = str(getattr(scene, "voxtart_character_name", "") or "").strip()
    if name:
        recipe.name = name[:128]
    return recipe.name


def _root_location(recipe: CharacterRecipe, context: bpy.types.Context) -> tuple[float, float, float]:
    raw = recipe.settings.get("root_location")
    if raw is not None:
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise ValueError("saved character root location must contain three coordinates")
        try:
            return tuple(float(value) for value in raw)
        except (TypeError, ValueError) as error:
            raise ValueError("saved character root location contains invalid coordinates") from error
    location = tuple(float(value) for value in context.scene.cursor.location)
    recipe.settings["root_location"] = list(location)
    return location


def _animation_target_object(context, name: str = "") -> bpy.types.Object:
    target_name = str(
        name or getattr(context.scene, "voxtart_animation_target", "") or ""
    ).strip()
    target = bpy.data.objects.get(target_name) if target_name else None
    if target is None or target.type != "ARMATURE":
        raise ValueError("choose an animation target armature before binding the character")
    return target


def _animation_bone_for_slot(target: bpy.types.Object, slot: str) -> str:
    roles = _CHARACTER_ANIMATION_ROLES.get(slot)
    if roles is None:
        raise ValueError(f"unsupported character animation slot: {slot}")
    for role in roles:
        matches = sorted(
            (
                bone.name
                for bone in target.data.bones
                if canonical_bone_role(bone.name) == role
            ),
            key=lambda name: (name.casefold(), name),
        )
        if matches:
            return matches[0]
    raise ValueError(
        f"target armature has no compatible bone for character slot {slot}"
    )


def _animation_bindings_for_target(
    recipe: CharacterRecipe, target: bpy.types.Object
) -> dict[str, str]:
    raw_overrides = recipe.settings.get("animation_overrides", {})
    overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
    bindings: dict[str, str] = {}
    for part in recipe.ordered_parts():
        override = str(overrides.get(part.slot, "") or "").strip()
        if override:
            if target.data.bones.get(override) is None:
                raise ValueError(
                    f"animation override for character slot {part.slot} names "
                    f"missing target bone {override!r}"
                )
            bindings[part.slot] = override
        else:
            bindings[part.slot] = _animation_bone_for_slot(target, part.slot)
    return bindings


def _bind_created_to_animation_target(
    context: bpy.types.Context,
    recipe: CharacterRecipe,
    created: dict[str, bpy.types.Object],
) -> None:
    target_name = str(recipe.settings.get("animation_target", "") or "").strip()
    if not target_name:
        return
    target = _animation_target_object(context, target_name)
    raw_bindings = recipe.settings.get("animation_bindings")
    required_slots = tuple(created)
    if not isinstance(raw_bindings, dict) or any(
        slot not in raw_bindings for slot in required_slots
    ):
        raw_bindings = _animation_bindings_for_target(recipe, target)
        recipe.settings["animation_bindings"] = dict(raw_bindings)
    bindings = {str(slot): str(name).strip() for slot, name in raw_bindings.items()}
    world_matrices = {
        slot: obj.matrix_world.copy() for slot, obj in created.items()
    }
    for obj in created.values():
        obj.parent = None
    for slot, obj in created.items():
        bone_name = bindings.get(slot, "")
        if not bone_name or target.data.bones.get(bone_name) is None:
            raise ValueError(
                f"target armature binding for character slot {slot} is invalid"
            )
        obj.parent = target
        obj.parent_type = "BONE"
        obj.parent_bone = bone_name
        obj.matrix_world = world_matrices[slot]
        obj["voxtart_animation_armature"] = target.name
        obj["voxtart_animation_bone"] = bone_name


def _remove_objects_with_id(character_id: str) -> None:
    for obj in tuple(bpy.data.objects):
        if obj.get(CHARACTER_OBJECT_ID_PROPERTY) == character_id:
            bpy.data.objects.remove(obj, do_unlink=True)


def _remove_created(created: dict[str, bpy.types.Object]) -> None:
    for obj in tuple(created.values()):
        if obj.name in bpy.data.objects:
            bpy.data.objects.remove(obj, do_unlink=True)


def _rebuild_character(context: bpy.types.Context, recipe: CharacterRecipe) -> tuple[bpy.types.Object, ...]:
    """Build a staged character, replacing the previous build only on success."""

    if not recipe.parts:
        _remove_objects_with_id(_character_id(context.scene))
        return ()
    root = recipe.get("ROOT")
    if root is None:
        raise ValueError("assign a Root part before adding socketed parts")

    from .blob_gallery_ui import _instantiate_entry, _snap_source_to_target

    gallery = _gallery(context)
    loaded = [(part, gallery.load(part.blob_id)) for part in recipe.ordered_parts()]
    character_id = _character_id(context.scene)
    created: dict[str, bpy.types.Object] = {}
    try:
        for part, entry in loaded:
            obj = _instantiate_entry(context, entry)
            obj.name = f"{recipe.name} {part.slot}"
            created[part.slot] = obj
            parent = created.get(part.parent_slot) if part.parent_slot else None
            if part.parent_slot and parent is None:
                raise ValueError(
                    f"slot {part.slot} requires parent slot {part.parent_slot}"
                )
            if parent is None:
                obj.location = _root_location(recipe, context)
            else:
                if bool(getattr(context.scene, "voxtart_character_auto_snap", True)):
                    try:
                        snap = _snap_source_to_target(obj, parent)
                    except ValueError as error:
                        raise ValueError(
                            f"slot {part.slot} cannot snap to {part.parent_slot}: {error}"
                        ) from error
                    obj["voxtart_character_snap"] = json.dumps(snap, sort_keys=True)
                else:
                    obj.location = parent.location
                world_matrix = obj.matrix_world.copy()
                obj.parent = parent
                obj.matrix_world = world_matrix
            obj[CHARACTER_SLOT_PROPERTY] = part.slot
            obj[CHARACTER_BLOB_PROPERTY] = part.blob_id
            obj["voxtart_character_parent_slot"] = part.parent_slot
            obj["voxtart_character_part_name"] = part.name
            obj["voxtart_character_visible"] = bool(part.visible)
            obj.hide_viewport = not part.visible
            obj.hide_render = not part.visible
            obj.hide_set(not part.visible)
        _bind_created_to_animation_target(context, recipe, created)


    except Exception:
        _remove_created(created)
        raise

    _remove_objects_with_id(character_id)
    for obj in created.values():
        obj[CHARACTER_OBJECT_ID_PROPERTY] = character_id
    root_object = created["ROOT"]
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    root_object.select_set(True)
    context.view_layer.objects.active = root_object
    return tuple(created.values())


def _assign_part(context: bpy.types.Context, part: CharacterPart) -> tuple[bpy.types.Object, ...]:
    scene = context.scene
    old_recipe = _load_recipe(scene)
    recipe = CharacterRecipe(
        name=old_recipe.name,
        parts=list(old_recipe.parts),
        settings=dict(old_recipe.settings),
    )
    _scene_name(scene, recipe)
    _root_location(recipe, context)
    recipe.upsert(part)
    try:
        objects = _rebuild_character(context, recipe)
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        _save_recipe(scene, old_recipe)
        raise ValueError(str(error)) from error
    _save_recipe(scene, recipe)
    return objects


class VOXTART_OT_character_assign_blob(bpy.types.Operator):
    bl_idname = "voxtart.character_assign_blob"
    bl_label = "Assign Character Part"
    bl_description = "Assign a gallery blob to the selected character slot and rebuild the socketed character"
    bl_options = {'REGISTER', 'UNDO'}

    slot: EnumProperty(name="Slot", items=_SLOT_ITEMS, default="ROOT", options={'HIDDEN'})
    blob_id: StringProperty(name="Blob ID", options={'HIDDEN'})

    def execute(self, context):
        try:
            entry = _gallery(context).load(self.blob_id)
            _assign_part(
                context,
                CharacterPart(slot=self.slot, blob_id=entry.blob_id, name=entry.name),
            )
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Assigned {entry.name} to {self.slot}")
        return {'FINISHED'}


class VOXTART_OT_character_assign_active(bpy.types.Operator):
    bl_idname = "voxtart.character_assign_active"
    bl_label = "Use Active Blob"
    bl_description = "Save the active voxel asset to the Blob Gallery and assign it to the selected character slot"
    bl_options = {'REGISTER', 'UNDO'}

    slot: EnumProperty(name="Slot", items=_SLOT_ITEMS, default="ROOT", options={'HIDDEN'})

    def execute(self, context):
        obj = context.active_object
        if obj is None or ASSET_PROPERTY not in obj:
            self.report({'ERROR'}, "select a VOXTART voxel asset first")
            return {'CANCELLED'}
        try:
            from .blob_gallery_ui import _entry_from_active

            entry = _entry_from_active(context)
            _gallery(context).save(entry)
            _assign_part(
                context,
                CharacterPart(slot=self.slot, blob_id=entry.blob_id, name=entry.name),
            )
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Assigned active blob to {self.slot}")
        return {'FINISHED'}


class VOXTART_OT_character_rebuild(bpy.types.Operator):
    bl_idname = "voxtart.character_rebuild"
    bl_label = "Rebuild Character"
    bl_description = "Rebuild the current character from its saved gallery recipe"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            recipe = _load_recipe(context.scene)
            if not recipe.parts:
                self.report({'ERROR'}, "assign at least one character part first")
                return {'CANCELLED'}
            _scene_name(context.scene, recipe)
            _rebuild_character(context, recipe)
            _save_recipe(context.scene, recipe)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Rebuilt {recipe.name}")
        return {'FINISHED'}


class VOXTART_OT_character_remove_part(bpy.types.Operator):
    bl_idname = "voxtart.character_remove_part"
    bl_label = "Remove Character Part"
    bl_description = "Remove a slot and rebuild the character"
    bl_options = {'REGISTER', 'UNDO'}

    slot: EnumProperty(name="Slot", items=_SLOT_ITEMS, default="ROOT", options={'HIDDEN'})

    def execute(self, context):
        scene = context.scene
        try:
            recipe = _load_recipe(scene)
            if self.slot == "ROOT":
                self.report({'ERROR'}, "use Clear Character to remove the root and its children")
                return {'CANCELLED'}
            if not recipe.remove(self.slot):
                self.report({'ERROR'}, f"slot {self.slot} is empty")
                return {'CANCELLED'}
            _rebuild_character(context, recipe)
            _save_recipe(scene, recipe)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}



class VOXTART_OT_character_toggle_visibility(bpy.types.Operator):
    bl_idname = "voxtart.character_toggle_visibility"
    bl_label = "Toggle Character Part Visibility"
    bl_description = "Show or hide an assigned character part without removing it from the recipe"
    bl_options = {'REGISTER', 'UNDO'}

    slot: EnumProperty(name="Slot", items=_SLOT_ITEMS, default="ROOT", options={'HIDDEN'})

    def execute(self, context):
        scene = context.scene
        try:
            recipe = _load_recipe(scene)
            part = recipe.get(self.slot)
            if part is None:
                self.report({'ERROR'}, f"slot {self.slot} is empty")
                return {'CANCELLED'}
            part = replace(part, visible=not part.visible)
            recipe.upsert(part)
            _rebuild_character(context, recipe)
            _save_recipe(scene, recipe)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        state = "Shown" if part.visible else "Hidden"
        self.report({'INFO'}, f"{state} {self.slot}")
        return {'FINISHED'}


class VOXTART_OT_character_bind_animation(bpy.types.Operator):
    bl_idname = "voxtart.character_bind_animation"
    bl_label = "Bind Character to Target Rig"
    bl_description = "Parent assembled character parts to compatible bones on the selected animation target"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        try:
            recipe = _load_recipe(scene)
            if not recipe.parts:
                self.report({'ERROR'}, "assign at least one character part first")
                return {'CANCELLED'}
            target = _animation_target_object(context)
            recipe.settings["animation_target"] = target.name
            recipe.settings["animation_bindings"] = _animation_bindings_for_target(
                recipe, target
            )
            _rebuild_character(context, recipe)
            _save_recipe(scene, recipe)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Bound {recipe.name} to {target.name}")
        return {'FINISHED'}


class VOXTART_OT_character_unbind_animation(bpy.types.Operator):
    bl_idname = "voxtart.character_unbind_animation"
    bl_label = "Unbind Character from Target Rig"
    bl_description = "Restore socketed character parenting and remove animation-target binding"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        try:
            recipe = _load_recipe(scene)
            if not str(recipe.settings.get("animation_target", "") or "").strip():
                self.report({'ERROR'}, "character is not bound to an animation target")
                return {'CANCELLED'}
            recipe.settings.pop("animation_target", None)
            recipe.settings.pop("animation_bindings", None)
            _rebuild_character(context, recipe)
            _save_recipe(scene, recipe)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Unbound {recipe.name} from its animation target")
        return {'FINISHED'}


class VOXTART_OT_character_set_animation_bone(bpy.types.Operator):
    bl_idname = "voxtart.character_set_animation_bone"
    bl_label = "Set Character Bone Override"
    bl_description = "Override the automatic target bone for one assembled character slot"
    bl_options = {'REGISTER', 'UNDO'}

    slot: EnumProperty(name="Slot", items=_SLOT_ITEMS, default="ROOT", options={'HIDDEN'})
    bone_name: StringProperty(name="Target Bone", options={'HIDDEN'})

    def execute(self, context):
        scene = context.scene
        try:
            recipe = _load_recipe(scene)
            if recipe.get(self.slot) is None:
                self.report({'ERROR'}, f"slot {self.slot} is empty")
                return {'CANCELLED'}
            target = _animation_target_object(
                context,
                str(recipe.settings.get("animation_target", "") or ""),
            )
            bone_name = str(self.bone_name or "").strip()
            if not bone_name:
                self.report({'ERROR'}, "enter a target bone name")
                return {'CANCELLED'}
            if target.data.bones.get(bone_name) is None:
                self.report({'ERROR'}, f"target bone does not exist: {bone_name}")
                return {'CANCELLED'}
            raw_overrides = recipe.settings.get("animation_overrides", {})
            overrides = dict(raw_overrides) if isinstance(raw_overrides, dict) else {}
            overrides[self.slot] = bone_name
            recipe.settings["animation_overrides"] = overrides
            recipe.settings["animation_bindings"] = _animation_bindings_for_target(
                recipe, target
            )
            _rebuild_character(context, recipe)
            _save_recipe(scene, recipe)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Mapped {self.slot} to {bone_name}")
        return {'FINISHED'}


class VOXTART_OT_character_clear_animation_bone(bpy.types.Operator):
    bl_idname = "voxtart.character_clear_animation_bone"
    bl_label = "Reset Character Bone Override"
    bl_description = "Restore automatic target-bone selection for one assembled character slot"
    bl_options = {'REGISTER', 'UNDO'}

    slot: EnumProperty(name="Slot", items=_SLOT_ITEMS, default="ROOT", options={'HIDDEN'})

    def execute(self, context):
        scene = context.scene
        try:
            recipe = _load_recipe(scene)
            if recipe.get(self.slot) is None:
                self.report({'ERROR'}, f"slot {self.slot} is empty")
                return {'CANCELLED'}
            target = _animation_target_object(
                context,
                str(recipe.settings.get("animation_target", "") or ""),
            )
            raw_overrides = recipe.settings.get("animation_overrides", {})
            overrides = dict(raw_overrides) if isinstance(raw_overrides, dict) else {}
            if self.slot not in overrides:
                self.report({'ERROR'}, f"slot {self.slot} has no bone override")
                return {'CANCELLED'}
            del overrides[self.slot]
            if overrides:
                recipe.settings["animation_overrides"] = overrides
            else:
                recipe.settings.pop("animation_overrides", None)
            recipe.settings["animation_bindings"] = _animation_bindings_for_target(
                recipe, target
            )
            _rebuild_character(context, recipe)
            _save_recipe(scene, recipe)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Reset {self.slot} to automatic bone mapping")
        return {'FINISHED'}


class VOXTART_OT_character_clear(bpy.types.Operator):
    bl_idname = "voxtart.character_clear"
    bl_label = "Clear Character"
    bl_description = "Remove the assembled character and its saved recipe"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        _remove_objects_with_id(str(scene.get(CHARACTER_ID_PROPERTY, "") or ""))
        for name in (CHARACTER_RECIPE_PROPERTY, CHARACTER_ID_PROPERTY):
            if name in scene:
                del scene[name]
        scene.voxtart_character_name = "VOXTART Character"
        self.report({'INFO'}, "Character cleared")
        return {'FINISHED'}


class VOXTART_PT_character_editor(bpy.types.Panel):
    bl_label = "Voxel Character Editor"
    bl_idname = "VOXTART_PT_character_editor"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VOXTART"
    bl_order = 18

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        try:
            recipe = _load_recipe(scene)
        except (TypeError, ValueError) as error:
            recipe = _new_recipe(scene)
            layout.label(text=str(error), icon="ERROR")
        layout.prop(scene, "voxtart_character_name", text="Name")
        layout.prop(scene, CHARACTER_SLOT_PROPERTY, text="Target Slot")
        layout.prop(scene, "voxtart_character_search", text="Part Search")
        layout.prop(scene, "voxtart_character_auto_snap", text="Auto Snap")
        row = layout.row(align=True)
        row.operator(VOXTART_OT_character_rebuild.bl_idname, text="Rebuild", icon="FILE_REFRESH")
        row.operator(VOXTART_OT_character_clear.bl_idname, text="Clear", icon="TRASH")

        active = layout.operator(VOXTART_OT_character_assign_active.bl_idname, text="Use Active Blob", icon="EYEDROPPER")
        active.slot = scene.voxtart_character_slot

        try:
            entries, errors = _gallery_entries(context)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            entries, errors = (), (str(error),)
        if errors:
            layout.label(text=f"Skipped {len(errors)} invalid asset(s)", icon="ERROR")
        layout.label(text=f"Gallery parts: {len(entries)}", icon="ASSET_MANAGER")
        for entry in entries[:16]:
            row = layout.row(align=True)
            assign = row.operator(
                VOXTART_OT_character_assign_blob.bl_idname,
                text=f"{entry.category} / {entry.name}",
                icon="MESH_CUBE",
            )
            assign.slot = scene.voxtart_character_slot
            assign.blob_id = entry.blob_id
            row.label(text=f"{len(entry.asset.voxels)}v / {len(entry.joints)}j")

        box = layout.box()
        box.label(text=f"Assigned parts ({len(recipe.parts)}/{len(CHARACTER_SLOTS)})", icon="OUTLINER_OB_MESH")
        for slot in CHARACTER_SLOTS:
            row = box.row(align=True)
            part = recipe.get(slot)
            row.label(text=slot.replace("_", " "), icon="DOT")
            if part is None:
                row.label(text="Empty")
                continue
            visibility = row.operator(
                VOXTART_OT_character_toggle_visibility.bl_idname,
                text=part.name,
                icon="HIDE_OFF" if part.visible else "HIDE_ON",
            )
            visibility.slot = slot
            if slot != "ROOT":
                remove = row.operator(VOXTART_OT_character_remove_part.bl_idname, text="", icon="X")
                remove.slot = slot

        if recipe.parts:
            animation_box = layout.box()
            bound_target = str(recipe.settings.get("animation_target", "") or "").strip()
            if bound_target:
                animation_box.label(text=f"Animation rig: {bound_target}", icon="ARMATURE_DATA")
                animation_box.operator(
                    VOXTART_OT_character_unbind_animation.bl_idname,
                    text="Unbind Animation Rig",
                    icon="UNLINKED",
                )
                animation_box.prop(
                    scene,
                    CHARACTER_ANIMATION_SLOT_PROPERTY,
                    text="Slot",
                )
                animation_box.prop(
                    scene,
                    CHARACTER_ANIMATION_BONE_PROPERTY,
                    text="Bone",
                )
                selected_slot = getattr(scene, CHARACTER_ANIMATION_SLOT_PROPERTY)
                bindings = recipe.settings.get("animation_bindings", {})
                resolved_bone = (
                    str(bindings.get(selected_slot, "") or "")
                    if isinstance(bindings, dict)
                    else ""
                )
                animation_box.label(
                    text=f"Resolved bone: {resolved_bone or 'None'}",
                    icon="BONE_DATA",
                )
                row = animation_box.row(align=True)
                set_bone = row.operator(
                    VOXTART_OT_character_set_animation_bone.bl_idname,
                    text="Set Override",
                    icon="BONE_DATA",
                )
                set_bone.slot = selected_slot
                set_bone.bone_name = getattr(
                    scene, CHARACTER_ANIMATION_BONE_PROPERTY
                )
                clear_bone = row.operator(
                    VOXTART_OT_character_clear_animation_bone.bl_idname,
                    text="Reset",
                    icon="LOOP_BACK",
                )
                clear_bone.slot = selected_slot
            else:
                animation_box.label(
                    text="Bind parts to the selected animation target.",
                    icon="ARMATURE_DATA",
                )
                animation_box.operator(
                    VOXTART_OT_character_bind_animation.bl_idname,
                    text="Bind Animation Rig",
                    icon="CONSTRAINT",
                )
            box.label(text="Parts snap through compatible opposite-facing joints.", icon="CONSTRAINT")


_CLASSES = (
    VOXTART_OT_character_assign_blob,
    VOXTART_OT_character_assign_active,
    VOXTART_OT_character_rebuild,
    VOXTART_OT_character_remove_part,
    VOXTART_OT_character_toggle_visibility,
    VOXTART_OT_character_bind_animation,
    VOXTART_OT_character_unbind_animation,
    VOXTART_OT_character_set_animation_bone,
    VOXTART_OT_character_clear_animation_bone,
    VOXTART_OT_character_clear,
    VOXTART_PT_character_editor,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_character_name = StringProperty(
        name="Character Name",
        default="VOXTART Character",
    )
    bpy.types.Scene.voxtart_character_slot = EnumProperty(
        name="Character Slot",
        items=_SLOT_ITEMS,
        default="ROOT",
    )
    bpy.types.Scene.voxtart_character_search = StringProperty(
        name="Character Part Search",
        default="",
    )
    bpy.types.Scene.voxtart_character_auto_snap = BoolProperty(
        name="Auto Snap",
        description="Use compatible Blob Gallery joints while rebuilding character parts",
        default=True,
    )
    bpy.types.Scene.voxtart_character_animation_slot = EnumProperty(
        name="Animation Slot",
        items=_SLOT_ITEMS,
        default="ROOT",
    )
    bpy.types.Scene.voxtart_character_animation_bone = StringProperty(
        name="Animation Bone",
        default="",
    )


def unregister() -> None:
    for name in (
        "voxtart_character_animation_bone",
        "voxtart_character_animation_slot",
        "voxtart_character_auto_snap",
        "voxtart_character_search",
        "voxtart_character_slot",
        "voxtart_character_name",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "CHARACTER_ANIMATION_BONE_PROPERTY",
    "CHARACTER_ANIMATION_SLOT_PROPERTY",
    "CHARACTER_BLOB_PROPERTY",
    "CHARACTER_ID_PROPERTY",
    "CHARACTER_OBJECT_ID_PROPERTY",
    "CHARACTER_RECIPE_PROPERTY",
    "CHARACTER_SLOT_PROPERTY",
    "VOXTART_OT_character_bind_animation",
    "VOXTART_OT_character_clear_animation_bone",
    "VOXTART_OT_character_set_animation_bone",
    "VOXTART_OT_character_toggle_visibility",
    "VOXTART_OT_character_unbind_animation",
    "register",
    "unregister",
]
