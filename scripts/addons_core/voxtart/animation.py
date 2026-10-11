# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Optional animation-library plugins and humanoid retargeting for VOXTART.

VOXTART does not redistribute third-party animation packs or retargeting
add-ons.  It provides a small host layer around Blender's import operators,
a guided blockout rig for unrigged meshes, and adapters for user-installed
retargeting extensions.  The Quaternius Universal Animation Library is the
first supported source profile.
"""


import difflib
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from .game_rig import (
    HIERARCHY_MODES,
    RIG_EXTRACTION_MODES,
    choose_action_names,
    make_extraction_plan,
    parse_name_list,
)

try:
    from bpy_extras.io_utils import ImportHelper
except ImportError:  # pragma: no cover - only used by non-Blender import probes.
    class ImportHelper:
        pass


QUATERNIUS_URL = "https://quaternius.com/packs/universalanimationlibrary.html"
QUATERNIUS_LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
ANIMATION_RETARGETING_URL = "https://github.com/Mwni/blender-animation-retargeting"
RETARGET_EXTENSION_URL = "https://extensions.blender.org/add-ons/retarget/"
RIGIFY_URL = "https://docs.blender.org/manual/en/5.2/addons/rigging/rigify/index.html"

PLUGIN_ID = "quaternius_universal_animation"
MAX_IMPORTED_ACTIONS = 512


@dataclass(frozen=True, slots=True)
class AnimationPlugin:
    """Metadata for an optional source or retargeting integration."""

    plugin_id: str
    name: str
    description: str
    formats: tuple[str, ...] = ()
    url: str = ""
    license_url: str = ""
    bundled: bool = False


ANIMATION_PLUGINS = (
    AnimationPlugin(
        plugin_id=PLUGIN_ID,
        name="Quaternius Universal Animation Library",
        description="120+ universal humanoid animations; import the user-owned source pack.",
        formats=(".blend", ".fbx", ".glb", ".gltf"),
        url=QUATERNIUS_URL,
        license_url=QUATERNIUS_LICENSE_URL,
    ),
    AnimationPlugin(
        plugin_id="rigify",
        name="Rigify",
        description="Blender's built-in humanoid rigging system for fitting a target metarig.",
        formats=(),
        url=RIGIFY_URL,
        license_url="https://www.gnu.org/licenses/gpl-3.0.html",
        bundled=True,
    ),
    AnimationPlugin(
        plugin_id="animation_retargeting",
        name="Animation Retargeting",
        description="Optional GPL retargeter with mapping, rest-pose alignment, and baking.",
        formats=(),
        url=ANIMATION_RETARGETING_URL,
        license_url="https://www.gnu.org/licenses/gpl-3.0.html",
    ),
    AnimationPlugin(
        plugin_id="retarget",
        name="Retarget Blender Extension",
        description="Optional Blender 5 retargeting extension with Rigify conversion tools.",
        formats=(),
        url=RETARGET_EXTENSION_URL,
        license_url="https://www.gnu.org/licenses/gpl-3.0.html",
    ),
)


# Ordered from the most specific roles to the broadest roles.  Names are
# intentionally permissive because FBX/glTF exporters commonly add prefixes,
# namespaces, and numeric suffixes.
_ROLE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("forearm", ("forearm", "lowerarm", "loarm")),
    ("upper_arm", ("upperarm", "uparm", "arm")),
    ("upper_leg", ("upperleg", "thigh", "upleg")),
    ("lower_leg", ("lowerleg", "leg", "shin", "calf", "loleg")),
    ("shoulder", ("clavicle", "shoulder", "collar")),
    ("hand", ("hand", "wrist")),
    ("foot", ("foot", "ankle")),
    ("toe", ("toe", "ball")),
    ("head", ("head", "skull")),
    ("neck", ("neck", "cervical")),
    ("chest", ("chest", "upperchest", "spine02", "spine2")),
    ("spine", ("spine", "abdomen", "torso")),
    ("pelvis", ("pelvis", "hips", "hip")),
    ("root", ("root", "rootbone", "cog")),
)


_SIDE_ALIASES = {
    "left": "l",
    "l": "l",
    "right": "r",
    "r": "r",
}


def _normalized_bone_name(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    value = re.sub(r"(?<=[a-z])(?=\d)", "_", value)
    return value


def _bone_side(name: str) -> str:
    normalized = _normalized_bone_name(name)
    tokens = normalized.split("_")
    for token in reversed(tokens):
        if token in _SIDE_ALIASES:
            return _SIDE_ALIASES[token]
    if any(token.startswith("left") or token.endswith("left") for token in tokens):
        return "l"
    if any(token.startswith("right") or token.endswith("right") for token in tokens):
        return "r"
    return ""


def _bone_compact(name: str) -> str:
    normalized = _normalized_bone_name(name)
    normalized = normalized.replace("mixamorig", "")
    normalized = normalized.replace("left", "l").replace("right", "r")
    return normalized.replace("_", "")


def canonical_bone_role(name: str) -> str | None:
    """Return a stable humanoid role for a source or target bone name."""

    compact = _bone_compact(name)
    side = _bone_side(name)
    for role, aliases in _ROLE_ALIASES:
        if any(alias in compact for alias in aliases):
            return f"{role}_{side}" if side else role
    return None


def _mapping_score(source: str, target: str) -> float:
    source_normalized = _normalized_bone_name(source)
    target_normalized = _normalized_bone_name(target)
    if source_normalized == target_normalized:
        return 100.0
    source_role = canonical_bone_role(source)
    target_role = canonical_bone_role(target)
    if source_role is not None and source_role == target_role:
        return 90.0
    if source_role and target_role and source_role.rsplit("_", 1)[0] == target_role.rsplit("_", 1)[0]:
        return 55.0
    return difflib.SequenceMatcher(None, _bone_compact(source), _bone_compact(target)).ratio() * 20.0


def suggest_bone_mapping(
    source_names: Iterable[str], target_names: Iterable[str]
) -> list[dict[str, str]]:
    """Build deterministic, one-to-one best-effort humanoid bone mappings."""

    source_names = tuple(str(name) for name in source_names)
    target_names = tuple(str(name) for name in target_names)
    candidates: list[tuple[float, str, str]] = []
    for source in source_names:
        for target in target_names:
            score = _mapping_score(source, target)
            if score >= 55.0:
                candidates.append((score, source, target))
    candidates.sort(key=lambda item: (-item[0], item[1].lower(), item[2].lower()))

    used_source: set[str] = set()
    used_target: set[str] = set()
    result: list[dict[str, str]] = []
    for _score, source, target in candidates:
        if source in used_source or target in used_target:
            continue
        used_source.add(source)
        used_target.add(target)
        role = canonical_bone_role(source) or canonical_bone_role(target) or "custom"
        result.append({"source": source, "target": target, "role": role})
    return result


def _armature_bone_names(obj: bpy.types.Object) -> tuple[str, ...]:
    if obj is None or obj.type != "ARMATURE":
        return ()
    return tuple(bone.name for bone in obj.data.bones)


def _has_mwni_retargeter() -> bool:
    return any(
        hasattr(obj, "retargeting_context")
        for obj in getattr(bpy.data, "objects", ())
        if getattr(obj, "type", None) == "ARMATURE"
    ) or hasattr(getattr(bpy.types, "Object", object), "retargeting_context")


def _has_retarget_extension() -> bool:
    try:
        return hasattr(bpy.ops, "retarget")
    except (AttributeError, RuntimeError):
        return False


def plugin_status() -> list[dict[str, Any]]:
    """Return installed/available state without importing optional add-ons."""

    rigify_installed = importlib.util.find_spec("rigify") is not None
    mwni_installed = _has_mwni_retargeter()
    retarget_installed = _has_retarget_extension()
    status = {
        "rigify": rigify_installed,
        "animation_retargeting": mwni_installed,
        "retarget": retarget_installed,
    }
    return [
        {
            "id": plugin.plugin_id,
            "name": plugin.name,
            "description": plugin.description,
            "formats": list(plugin.formats),
            "url": plugin.url,
            "license_url": plugin.license_url,
            "bundled": plugin.bundled,
            "installed": True if plugin.plugin_id == PLUGIN_ID else status[plugin.plugin_id],
        }
        for plugin in ANIMATION_PLUGINS
    ]


def animation_state(context: bpy.types.Context) -> dict[str, Any]:
    scene = context.scene
    source = bpy.data.objects.get(str(getattr(scene, "voxtart_animation_source", "")))
    target = bpy.data.objects.get(str(getattr(scene, "voxtart_animation_target", "")))
    action_name = str(getattr(scene, "voxtart_animation_action", ""))
    action = bpy.data.actions.get(action_name) if action_name else None
    mapping = suggest_bone_mapping(_armature_bone_names(source), _armature_bone_names(target))
    is_game_rig = bool(target and target.get("voxtart_game_rig", False))
    return {
        "source_armature": source.name if source and source.type == "ARMATURE" else None,
        "target_armature": target.name if target and target.type == "ARMATURE" else None,
        "game_rig": target.name if is_game_rig else None,
        "action": action.name if action else None,
        "available_action_count": len(tuple(bpy.data.actions)),
        "suggested_mapping_count": len(mapping),
        "suggested_mapping": mapping,
        "plugins": plugin_status(),
    }


def _get_object(name: str | None, object_type: str | None = None) -> bpy.types.Object | None:
    if not name:
        return None
    obj = bpy.data.objects.get(str(name))
    if obj is None or (object_type is not None and obj.type != object_type):
        return None
    return obj


def _ensure_source_collection(context: bpy.types.Context) -> bpy.types.Collection:
    collection = bpy.data.collections.get("VOXTART Animation Sources")
    if collection is None:
        collection = bpy.data.collections.new("VOXTART Animation Sources")
    if collection.name not in {item.name for item in context.scene.collection.children}:
        context.scene.collection.children.link(collection)
    return collection


def _link_to_collection(obj: bpy.types.Object, collection: bpy.types.Collection) -> None:
    if all(linked.name != collection.name for linked in obj.users_collection):
        collection.objects.link(obj)


def _activate_source(context: bpy.types.Context, objects: Iterable[bpy.types.Object], filepath: str) -> bpy.types.Object:
    imported = tuple(objects)
    source = next((obj for obj in imported if obj.type == "ARMATURE"), None)
    if source is None:
        raise RuntimeError("The animation file did not contain an armature")
    collection = _ensure_source_collection(context)
    for obj in imported:
        _link_to_collection(obj, collection)
    source["voxtart_animation_plugin"] = PLUGIN_ID
    source["voxtart_animation_source_path"] = os.path.abspath(filepath)
    source["voxtart_animation_source_license"] = QUATERNIUS_LICENSE_URL
    context.scene.voxtart_animation_source = source.name
    if source.animation_data and source.animation_data.action:
        context.scene.voxtart_animation_action = source.animation_data.action.name
    else:
        actions = tuple(bpy.data.actions)
        if actions:
            context.scene.voxtart_animation_action = actions[-1].name
    return source


def import_animation_library(context: bpy.types.Context, filepath: str) -> dict[str, Any]:
    """Import a user-owned Quaternius-compatible animation source file."""

    filepath = os.path.abspath(os.path.expanduser(str(filepath)))
    if not os.path.isfile(filepath):
        raise FileNotFoundError(filepath)
    extension = os.path.splitext(filepath)[1].lower()
    if extension not in {".blend", ".fbx", ".glb", ".gltf"}:
        raise ValueError("Animation source must be .blend, .fbx, .glb, or .gltf")

    before = {obj.name for obj in bpy.data.objects}
    if extension == ".blend":
        with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
            data_to.objects = list(data_from.objects)
            data_to.actions = list(data_from.actions)[:MAX_IMPORTED_ACTIONS]
        imported_objects = tuple(obj for obj in data_to.objects if obj is not None)
    elif extension == ".fbx":
        bpy.ops.import_scene.fbx(filepath=filepath, use_anim=True, automatic_bone_orientation=False)
        imported_objects = tuple(obj for obj in bpy.data.objects if obj.name not in before)
    else:
        bpy.ops.import_scene.gltf(filepath=filepath)
        imported_objects = tuple(obj for obj in bpy.data.objects if obj.name not in before)

    source = _activate_source(context, imported_objects, filepath)
    return {
        "plugin_id": PLUGIN_ID,
        "filepath": filepath,
        "source_armature": source.name,
        "imported_object_count": len(imported_objects),
        "action_count": len(tuple(bpy.data.actions)),
        "license_url": QUATERNIUS_LICENSE_URL,
    }


# Relative coordinates for a simple, editable humanoid scaffold.  This is a
# deliberately transparent fallback for blocky/voxel characters, not a claim
# of fully automatic anatomical rigging.
_BLOCKOUT_BONES: tuple[tuple[str, str | None, tuple[float, float, float], tuple[float, float, float]], ...] = (
    ("root", None, (0.0, 0.0, 0.00), (0.0, 0.0, 0.08)),
    ("pelvis", "root", (0.0, 0.0, 0.42), (0.0, 0.0, 0.52)),
    ("spine", "pelvis", (0.0, 0.0, 0.52), (0.0, 0.0, 0.66)),
    ("chest", "spine", (0.0, 0.0, 0.66), (0.0, 0.0, 0.77)),
    ("neck", "chest", (0.0, 0.0, 0.77), (0.0, 0.0, 0.87)),
    ("head", "neck", (0.0, 0.0, 0.87), (0.0, 0.0, 1.00)),
    ("thigh_l", "pelvis", (-0.12, 0.0, 0.42), (-0.13, 0.0, 0.23)),
    ("shin_l", "thigh_l", (-0.13, 0.0, 0.23), (-0.13, 0.0, 0.08)),
    ("foot_l", "shin_l", (-0.13, 0.0, 0.08), (-0.13, 0.18, 0.04)),
    ("toe_l", "foot_l", (-0.13, 0.18, 0.04), (-0.13, 0.28, 0.04)),
    ("thigh_r", "pelvis", (0.12, 0.0, 0.42), (0.13, 0.0, 0.23)),
    ("shin_r", "thigh_r", (0.13, 0.0, 0.23), (0.13, 0.0, 0.08)),
    ("foot_r", "shin_r", (0.13, 0.0, 0.08), (0.13, 0.18, 0.04)),
    ("toe_r", "foot_r", (0.13, 0.18, 0.04), (0.13, 0.28, 0.04)),
    ("upper_arm_l", "chest", (-0.23, 0.0, 0.74), (-0.38, 0.0, 0.64)),
    ("forearm_l", "upper_arm_l", (-0.38, 0.0, 0.64), (-0.49, 0.0, 0.58)),
    ("hand_l", "forearm_l", (-0.49, 0.0, 0.58), (-0.55, 0.0, 0.58)),
    ("upper_arm_r", "chest", (0.23, 0.0, 0.74), (0.38, 0.0, 0.64)),
    ("forearm_r", "upper_arm_r", (0.38, 0.0, 0.64), (0.49, 0.0, 0.58)),
    ("hand_r", "forearm_r", (0.49, 0.0, 0.58), (0.55, 0.0, 0.58)),
)


def _world_bounds(obj: bpy.types.Object):
    from mathutils import Vector

    corners = tuple(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    minimum = Vector((min(corner.x for corner in corners), min(corner.y for corner in corners), min(corner.z for corner in corners)))
    maximum = Vector((max(corner.x for corner in corners), max(corner.y for corner in corners), max(corner.z for corner in corners)))
    return minimum, maximum


def bind_mesh_to_armature(mesh: bpy.types.Object, armature: bpy.types.Object) -> dict[str, Any]:
    if mesh is None or mesh.type != "MESH":
        raise ValueError("Select a mesh character to bind")
    if armature is None or armature.type != "ARMATURE":
        raise ValueError("A target armature is required")
    bpy.ops.object.mode_set(mode="OBJECT") if getattr(mesh, "mode", "OBJECT") != "OBJECT" else None
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    try:
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        method = "automatic weights"
    except RuntimeError:
        bpy.ops.object.parent_set(type="ARMATURE_ENVELOPE")
        method = "envelope weights"
    mesh["voxtart_animation_armature"] = armature.name
    return {"mesh": mesh.name, "armature": armature.name, "method": method}


def create_blockout_rig(context: bpy.types.Context, mesh: bpy.types.Object, bind: bool = True) -> dict[str, Any]:
    if mesh is None or mesh.type != "MESH":
        raise ValueError("The active object must be a mesh character")
    from mathutils import Matrix, Vector

    minimum, maximum = _world_bounds(mesh)
    height = max(float(maximum.z - minimum.z), 0.001)
    width = max(float(maximum.x - minimum.x), height * 0.25)
    depth = max(float(maximum.y - minimum.y), height * 0.20)
    center_x = (minimum.x + maximum.x) * 0.5
    center_y = (minimum.y + maximum.y) * 0.5
    armature_data = bpy.data.armatures.new(f"{mesh.name} VOXTART Humanoid Data")
    armature = bpy.data.objects.new(f"{mesh.name} VOXTART Humanoid", armature_data)
    context.scene.collection.objects.link(armature)
    armature.matrix_world = Matrix.Identity(4)

    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones: dict[str, Any] = {}
    try:
        for name, parent_name, head_ratio, tail_ratio in _BLOCKOUT_BONES:
            bone = armature_data.edit_bones.new(name)
            bone.head = Vector((center_x + head_ratio[0] * width, center_y + head_ratio[1] * depth, minimum.z + head_ratio[2] * height))
            bone.tail = Vector((center_x + tail_ratio[0] * width, center_y + tail_ratio[1] * depth, minimum.z + tail_ratio[2] * height))
            if (bone.tail - bone.head).length < 0.0001:
                bone.tail.z += max(height * 0.01, 0.001)
            edit_bones[name] = bone
        for name, parent_name, _head, _tail in _BLOCKOUT_BONES:
            if parent_name:
                edit_bones[name].parent = edit_bones[parent_name]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    armature["voxtart_generated_rig"] = True
    armature["voxtart_bind_source"] = mesh.name
    context.scene.voxtart_animation_target = armature.name
    bind_result = bind_mesh_to_armature(mesh, armature) if bind else None
    return {
        "armature": armature.name,
        "mesh": mesh.name,
        "bone_count": len(_BLOCKOUT_BONES),
        "bound": bind_result,
        "note": "Blockout rig; adjust bone placement before production retargeting.",
    }


def _armature_bone_records(armature: bpy.types.Object) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "name": bone.name,
            "parent": bone.parent.name if bone.parent else None,
            "use_deform": bool(getattr(bone, "use_deform", False)),
            "selected": bool(getattr(bone, "select", False)),
        }
        for bone in armature.data.bones
    )


def extract_game_rig(
    context: bpy.types.Context,
    source: bpy.types.Object,
    *,
    mode: str = "DEFORM_ONLY",
    hierarchy: str = "FLATTEN_MISSING",
    keep_bones: str | Iterable[str] = (),
    include_root: bool = True,
    root_name: str = "",
    bind_meshes: bool = False,
) -> dict[str, Any]:
    """Create a compact, deform-oriented armature from a control rig."""

    if source is None or source.type != "ARMATURE":
        raise ValueError("Choose a source armature for game-rig extraction")
    plan = make_extraction_plan(
        _armature_bone_records(source),
        mode=mode,
        hierarchy=hierarchy,
        keep_bones=parse_name_list(keep_bones),
        include_root=include_root,
        root_name=root_name,
    )
    source_mode = getattr(source, "mode", "OBJECT")
    if source_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    source_meshes = tuple(
        obj
        for obj in getattr(context, "selected_objects", ())
        if getattr(obj, "type", None) == "MESH"
    )
    if not source_meshes and getattr(context, "active_object", None) is not None:
        active = context.active_object
        if getattr(active, "type", None) == "MESH":
            source_meshes = (active,)

    armature_data = bpy.data.armatures.new(f"{source.name} Game Rig Data")
    game_rig = bpy.data.objects.new(f"{source.name} Game Rig", armature_data)
    collections = tuple(getattr(source, "users_collection", ()))
    collection = collections[0] if collections else context.scene.collection
    collection.objects.link(game_rig)
    if hasattr(source, "matrix_world") and hasattr(source.matrix_world, "copy"):
        game_rig.matrix_world = source.matrix_world.copy()

    source_bones = {bone.name: bone for bone in source.data.bones}
    edit_bones: dict[str, Any] = {}
    _set_active_object(context, game_rig)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        for name in plan.names:
            source_bone = source_bones[name]
            copied = armature_data.edit_bones.new(name)
            head = getattr(source_bone, "head_local", None)
            tail = getattr(source_bone, "tail_local", None)
            copied.head = head if head is not None else source_bone.head
            copied.tail = tail if tail is not None else source_bone.tail
            copied.roll = float(getattr(source_bone, "roll", 0.0))
            edit_bones[name] = copied
        for name, parent_name in plan.parents:
            if parent_name:
                edit_bones[name].parent = edit_bones[parent_name]
                source_bone = source_bones[name]
                edit_bones[name].use_connect = bool(
                    getattr(source_bone, "use_connect", False)
                    and source_bones[name].parent
                    and source_bones[name].parent.name == parent_name
                )
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    for name in plan.names:
        copied = armature_data.bones[name]
        copied.use_deform = bool(getattr(source_bones[name], "use_deform", False))
    game_rig.show_in_front = True
    game_rig["voxtart_game_rig"] = True
    game_rig["voxtart_game_rig_source"] = source.name
    game_rig["voxtart_game_rig_include_mode"] = plan.mode
    game_rig["voxtart_game_rig_hierarchy"] = plan.hierarchy
    game_rig["voxtart_game_rig_bones"] = json.dumps(list(plan.names), separators=(",", ":"))
    game_rig["voxtart_game_rig_parents"] = json.dumps(
        {name: parent for name, parent in plan.parents},
        separators=(",", ":"),
    )
    context.scene.voxtart_animation_target = game_rig.name

    bindings = []
    if bind_meshes:
        for mesh in source_meshes:
            bindings.append(bind_mesh_to_armature(mesh, game_rig))
    _set_active_object(context, game_rig)
    return {
        "source_armature": source.name,
        "game_rig": game_rig.name,
        "bone_count": len(plan.names),
        "bones": list(plan.names),
        "hierarchy": plan.hierarchy,
        "bindings": bindings,
    }


def _resolve_action(source: bpy.types.Object, action_name: str | None):
    if action_name:
        action = bpy.data.actions.get(str(action_name))
        if action is None:
            raise ValueError(f"Unknown Blender action: {action_name}")
    elif source.animation_data and source.animation_data.action:
        action = source.animation_data.action
    else:
        action = next(iter(bpy.data.actions), None)
        if action is None:
            raise ValueError("No animation action is available")
    source.animation_data_create()
    source.animation_data.action = action
    return action


def _set_active_object(context: bpy.types.Context, obj: bpy.types.Object) -> None:
    for selected in tuple(getattr(context, "selected_objects", ())):
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj

def _retarget_with_mwni(
    context: bpy.types.Context,
    source: bpy.types.Object,
    target: bpy.types.Object,
    action: bpy.types.Action,
    mapping: list[dict[str, str]],
    bake: bool,
) -> dict[str, Any]:
    retarget_context = getattr(target, "retargeting_context", None)
    if retarget_context is None:
        raise RuntimeError("Animation Retargeting add-on is not enabled")
    _set_active_object(context, target)
    retarget_context.selected_source = source
    retarget_context.source = source
    retarget_context.target = target
    retarget_context.is_importing = True
    retarget_context.reset()
    for item in mapping:
        source_name = item["source"]
        target_name = item["target"]
        if source_name not in source.pose.bones or target_name not in target.pose.bones:
            continue
        pair = retarget_context.mappings.add()
        pair.source = source_name
        pair.target = target_name
    retarget_context.is_importing = False
    retarget_context.did_setup_empty_alignment = True
    retarget_context.update_drivers()
    if bake:
        baking_operator = getattr(getattr(bpy.ops, "baking", None), "bake", None)
        if baking_operator is None:
            raise RuntimeError("Animation Retargeting baking operator is unavailable")
        baking_operator()
    target["voxtart_animation_retarget_backend"] = "animation_retargeting"
    return {"backend": "animation_retargeting", "baked": bake, "mapping_count": len(retarget_context.mappings)}


def _retarget_natively(
    context: bpy.types.Context,
    source: bpy.types.Object,
    target: bpy.types.Object,
    action: bpy.types.Action,
    mapping: list[dict[str, str]],
    bake: bool,
) -> dict[str, Any]:
    _set_active_object(context, target)
    constraints: list[tuple[Any, Any]] = []
    mapped = 0
    preserve_constraints = False
    try:
        for item in mapping:
            source_name = item["source"]
            target_name = item["target"]
            if source_name not in source.pose.bones or target_name not in target.pose.bones:
                continue
            target_bone = target.pose.bones[target_name]
            rotation = target_bone.constraints.new("COPY_ROTATION")
            rotation.name = f"VOXTART {source_name} -> {target_name}"
            rotation.target = source
            rotation.subtarget = source_name
            rotation.target_space = "POSE"
            rotation.owner_space = "POSE"
            rotation.use_offset = True
            constraints.append((target_bone, rotation))
            role = canonical_bone_role(target_name) or ""
            if role in {"root", "pelvis"}:
                location = target_bone.constraints.new("COPY_LOCATION")
                location.name = f"VOXTART {source_name} location"
                location.target = source
                location.subtarget = source_name
                location.target_space = "POSE"
                location.owner_space = "POSE"
                location.use_offset = True
                constraints.append((target_bone, location))
            mapped += 1
        if mapped == 0:
            raise RuntimeError("No compatible source/target bone mappings were found")
        if bake:
            start, end = action.frame_range
            bpy.ops.object.mode_set(mode="POSE")
            bpy.ops.nla.bake(
                frame_start=int(start),
                frame_end=int(end),
                step=1,
                only_selected=False,
                visual_keying=True,
                clear_constraints=True,
                use_current_action=False,
                bake_types={"POSE"},
            )
            if target.animation_data and target.animation_data.action:
                target.animation_data.action.name = f"{target.name}|{action.name}|VOXTART"
        else:
            preserve_constraints = True
        target["voxtart_animation_retarget_backend"] = "native"
        return {"backend": "native", "baked": bake, "mapping_count": mapped}
    finally:
        if not preserve_constraints:
            for owner, constraint in constraints:
                try:
                    owner.constraints.remove(constraint)
                except (ReferenceError, RuntimeError):
                    pass
        if getattr(target, "mode", "OBJECT") != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")


def retarget_animation(
    context: bpy.types.Context,
    source_name: str | None = None,
    target_name: str | None = None,
    action_name: str | None = None,
    backend: str = "AUTO",
    bake: bool = True,
    mapping: Iterable[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    scene = context.scene
    source = _get_object(source_name or getattr(scene, "voxtart_animation_source", ""), "ARMATURE")
    target = _get_object(target_name or getattr(scene, "voxtart_animation_target", ""), "ARMATURE")
    if source is None or target is None:
        raise ValueError("Choose both source and target armatures")
    if source == target:
        raise ValueError("Source and target armatures must be different")
    action = _resolve_action(source, action_name or getattr(scene, "voxtart_animation_action", ""))
    pairs = [dict(item) for item in mapping] if mapping is not None else suggest_bone_mapping(
        _armature_bone_names(source), _armature_bone_names(target)
    )
    if not pairs:
        raise ValueError("No bone mappings could be suggested; map the humanoid bones manually")
    backend = str(backend or "AUTO").upper()
    if backend not in {"AUTO", "MWNI", "NATIVE"}:
        raise ValueError("backend must be AUTO, MWNI, or NATIVE")
    if backend == "AUTO":
        backend = "MWNI" if _has_mwni_retargeter() and hasattr(target, "retargeting_context") else "NATIVE"
    if backend == "MWNI":
        result = _retarget_with_mwni(context, source, target, action, pairs, bake)
    else:
        result = _retarget_natively(context, source, target, action, pairs, bake)
    result.update(
        {
            "source_armature": source.name,
            "target_armature": target.name,
            "action": action.name,
            "suggested_mapping": pairs,
        }
    )
    return result


def bake_game_rig_actions(
    context: bpy.types.Context,
    *,
    source_name: str | None = None,
    target_name: str | None = None,
    requested_actions: str = "",
    all_actions: bool = False,
    prefix: str = "Game_",
    suffix: str = "",
    backend: str = "NATIVE",
    mapping: Iterable[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Bake one or more source actions onto an extracted game rig."""

    scene = context.scene
    source = _get_object(source_name or getattr(scene, "voxtart_animation_source", ""), "ARMATURE")
    target = _get_object(target_name or getattr(scene, "voxtart_animation_target", ""), "ARMATURE")
    if source is None or target is None:
        raise ValueError("Choose both source and game-rig target armatures")
    if not bool(target.get("voxtart_game_rig", False)):
        raise ValueError("Extract a game rig before using the Action Bakery")

    available = tuple(
        action.name
        for action in bpy.data.actions
        if not bool(action.get("voxtart_game_rig_baked", False))
    )
    selected = choose_action_names(
        available,
        requested=requested_actions,
        all_actions=all_actions,
        active=getattr(scene, "voxtart_animation_action", ""),
    )
    pairs = [dict(item) for item in mapping] if mapping is not None else suggest_bone_mapping(
        _armature_bone_names(source), _armature_bone_names(target)
    )
    if not pairs:
        raise ValueError("No compatible source/target bone mappings were found")

    baked: list[dict[str, str]] = []
    for action_name in selected:
        result = retarget_animation(
            context,
            source_name=source.name,
            target_name=target.name,
            action_name=action_name,
            backend=backend,
            bake=True,
            mapping=pairs,
        )
        animation_data = getattr(target, "animation_data", None)
        action = getattr(animation_data, "action", None) if animation_data else None
        if action is None:
            raise RuntimeError(f"Retargeting produced no baked action for {action_name}")
        action.name = f"{prefix}{action_name}{suffix}"
        action["voxtart_game_rig_baked"] = True
        action["voxtart_game_rig_source"] = source.name
        action["voxtart_game_rig_target"] = target.name
        baked.append(
            {
                "source_action": action_name,
                "target_action": action.name,
                "backend": str(result.get("backend", backend)).lower(),
            }
        )

    target["voxtart_game_rig_bake_source"] = source.name
    target["voxtart_game_rig_baked_actions"] = json.dumps(baked, separators=(",", ":"))
    return {
        "source_armature": source.name,
        "target_armature": target.name,
        "actions": baked,
        "action_count": len(baked),
    }


def preview_game_rig_action(
    context: bpy.types.Context,
    *,
    target_name: str | None = None,
    action_name: str | None = None,
) -> dict[str, Any]:
    """Assign a baked action to the game rig and move to its first frame."""

    scene = context.scene
    target = _get_object(target_name or getattr(scene, "voxtart_animation_target", ""), "ARMATURE")
    if target is None or not bool(target.get("voxtart_game_rig", False)):
        raise ValueError("Choose an extracted game rig as Target")
    requested = str(action_name or getattr(scene, "voxtart_animation_preview_action", "")).strip()
    action = bpy.data.actions.get(requested) if requested else None
    if action is None:
        animation_data = getattr(target, "animation_data", None)
        action = getattr(animation_data, "action", None) if animation_data else None
    if action is None:
        raise ValueError("Choose a baked game-rig action to preview")
    target.animation_data_create()
    target.animation_data.action = action
    scene.voxtart_animation_preview_action = action.name
    start, end = action.frame_range
    scene.frame_set(int(start))
    _set_active_object(context, target)
    return {
        "target_armature": target.name,
        "action": action.name,
        "frame_start": int(start),
        "frame_end": int(end),
    }


def export_game_rig(
    context: bpy.types.Context,
    filepath: str,
    *,
    export_format: str = "GLTF",
    include_meshes: bool = True,
) -> dict[str, Any]:
    """Export the extracted rig and its VOXTART-bound meshes with Blender IO."""

    target = _get_object(getattr(context.scene, "voxtart_animation_target", ""), "ARMATURE")
    if target is None or not bool(target.get("voxtart_game_rig", False)):
        raise ValueError("Choose an extracted game rig as Target")
    export_format = str(export_format or "GLTF").upper()
    if export_format not in {"GLTF", "FBX"}:
        raise ValueError("Export format must be GLTF or FBX")
    raw_filepath = str(filepath or "").strip()
    if not raw_filepath:
        raise ValueError("Choose an export file path")
    filepath = os.path.abspath(os.path.expanduser(raw_filepath))
    extension = ".glb" if export_format == "GLTF" else ".fbx"
    valid_extensions = (".glb", ".gltf") if export_format == "GLTF" else (".fbx",)
    if not filepath.lower().endswith(valid_extensions):
        filepath += extension

    objects = [target]
    if include_meshes:
        objects.extend(
            obj
            for obj in bpy.data.objects
            if getattr(obj, "type", None) == "MESH"
            and (
                obj.get("voxtart_animation_armature", "") == target.name
                or getattr(obj, "parent", None) == target
            )
        )
    selected_before = tuple(getattr(context, "selected_objects", ()))
    active_before = getattr(context, "active_object", None)
    if getattr(context, "active_object", None) is not None and context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        context.view_layer.objects.active = target
        if export_format == "GLTF":
            bpy.ops.export_scene.gltf(
                filepath=filepath,
                export_format="GLB" if filepath.lower().endswith(".glb") else "GLTF_SEPARATE",
                use_selection=True,
                export_animations=True,
            )
        else:
            bpy.ops.export_scene.fbx(
                filepath=filepath,
                use_selection=True,
                bake_anim=True,
            )
    except AttributeError as error:
        raise RuntimeError("The selected Blender export operator is unavailable") from error
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in selected_before:
            if bpy.data.objects.get(getattr(obj, "name", "")) is not None:
                obj.select_set(True)
        if active_before is not None:
            context.view_layer.objects.active = active_before
    target["voxtart_game_rig_last_export"] = filepath
    target["voxtart_game_rig_last_export_format"] = export_format
    return {
        "target_armature": target.name,
        "filepath": filepath,
        "format": export_format,
        "object_count": len(objects),
    }


def _active_mesh(context: bpy.types.Context) -> bpy.types.Object | None:
    obj = context.active_object
    return obj if obj is not None and obj.type == "MESH" else None


class VOXTART_OT_import_animation_library(bpy.types.Operator, ImportHelper):
    bl_idname = "voxtart.import_animation_library"
    bl_label = "Import Animation Library"
    bl_description = "Import a user-owned Quaternius Universal Animation Library source file"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".blend"
    filter_glob: StringProperty(default="*.blend;*.fbx;*.glb;*.gltf", options={"HIDDEN"})

    def execute(self, context):
        try:
            result = import_animation_library(context, self.filepath)
        except (OSError, RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {result['source_armature']} with {result['action_count']} actions")
        return {"FINISHED"}


class VOXTART_OT_prepare_character(bpy.types.Operator):
    bl_idname = "voxtart.prepare_character"
    bl_label = "Create Blockout Humanoid Rig"
    bl_description = "Create a transparent humanoid scaffold and bind the active non-rigged mesh"
    bl_options = {"REGISTER", "UNDO"}

    bind: BoolProperty(name="Bind Mesh", default=True)

    def execute(self, context):
        mesh = _active_mesh(context)
        if mesh is None:
            self.report({"ERROR"}, "Select the non-rigged character mesh first")
            return {"CANCELLED"}
        try:
            result = create_blockout_rig(context, mesh, bind=self.bind)
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Created {result['armature']} with {result['bone_count']} bones")
        return {"FINISHED"}


class VOXTART_OT_bind_character(bpy.types.Operator):
    bl_idname = "voxtart.bind_character"
    bl_label = "Bind Mesh to Target Armature"
    bl_description = "Bind the active mesh to the selected VOXTART target armature using automatic weights"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        mesh = _active_mesh(context)
        target = _get_object(getattr(context.scene, "voxtart_animation_target", ""), "ARMATURE")
        if target is None and context.active_object and context.active_object.type == "ARMATURE":
            target = context.active_object
        if mesh is None or target is None:
            self.report({"ERROR"}, "Select a mesh and choose a target armature")
            return {"CANCELLED"}
        try:
            result = bind_mesh_to_armature(mesh, target)
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Bound {result['mesh']} with {result['method']}")
        return {"FINISHED"}


class VOXTART_OT_retarget_animation(bpy.types.Operator):
    bl_idname = "voxtart.retarget_animation"
    bl_label = "Retarget and Bake Animation"
    bl_description = "Map the source humanoid animation to the target armature and bake an action"
    bl_options = {"REGISTER", "UNDO"}

    backend: EnumProperty(
        name="Backend",
        items=(
            ("AUTO", "Automatic", "Use the installed retargeting add-on when available"),
            ("MWNI", "Animation Retargeting", "Use the optional Animation Retargeting add-on"),
            ("NATIVE", "VOXTART Native", "Use VOXTART's constrained Blender fallback"),
        ),
        default="AUTO",
    )
    bake: BoolProperty(name="Bake Action", default=True)

    def execute(self, context):
        try:
            result = retarget_animation(
                context,
                backend=self.backend,
                bake=self.bake,
            )
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Retargeted {result['action']} using {result['backend']}")
        return {"FINISHED"}


class VOXTART_OT_extract_game_rig(bpy.types.Operator):
    bl_idname = "voxtart.extract_game_rig"
    bl_label = "Extract Game Rig"
    bl_description = "Create a compact deform skeleton from the selected control rig"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        source = _get_object(getattr(context.scene, "voxtart_animation_source", ""), "ARMATURE")
        if source is None and getattr(context, "active_object", None) is not None:
            if context.active_object.type == "ARMATURE":
                source = context.active_object
        if source is None:
            self.report({"ERROR"}, "Choose or select a source armature")
            return {"CANCELLED"}
        try:
            result = extract_game_rig(
                context,
                source,
                mode=getattr(context.scene, "voxtart_game_rig_extract_mode", "DEFORM_ONLY"),
                hierarchy=getattr(context.scene, "voxtart_game_rig_hierarchy", "FLATTEN_MISSING"),
                keep_bones=getattr(context.scene, "voxtart_game_rig_keep_bones", ""),
                include_root=bool(getattr(context.scene, "voxtart_game_rig_include_root", True)),
                root_name=getattr(context.scene, "voxtart_game_rig_root_bone", ""),
                bind_meshes=bool(getattr(context.scene, "voxtart_game_rig_bind_meshes", False)),
            )
        except (RuntimeError, ValueError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Extracted {result['game_rig']} with {result['bone_count']} bones")
        return {"FINISHED"}


class VOXTART_OT_bake_game_rig_actions(bpy.types.Operator):
    bl_idname = "voxtart.bake_game_rig_actions"
    bl_label = "Bake Actions to Game Rig"
    bl_description = "Bake selected source actions onto the extracted game rig"
    bl_options = {"REGISTER", "UNDO"}

    backend: EnumProperty(
        name="Backend",
        items=(
            ("AUTO", "Automatic", "Use the installed retargeting add-on when available"),
            ("MWNI", "Animation Retargeting", "Use the optional Animation Retargeting add-on"),
            ("NATIVE", "VOXTART Native", "Use VOXTART's constrained Blender fallback"),
        ),
        default="NATIVE",
    )

    def execute(self, context):
        scene = context.scene
        try:
            result = bake_game_rig_actions(
                context,
                requested_actions=getattr(scene, "voxtart_animation_bake_action_names", ""),
                all_actions=bool(getattr(scene, "voxtart_animation_bake_all_actions", False)),
                prefix=getattr(scene, "voxtart_animation_bake_prefix", "Game_"),
                suffix=getattr(scene, "voxtart_animation_bake_suffix", ""),
                backend=self.backend,
            )
        except (RuntimeError, ValueError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Baked {result['action_count']} action(s) onto {result['target_armature']}")
        return {"FINISHED"}


class VOXTART_OT_preview_game_rig_action(bpy.types.Operator):
    bl_idname = "voxtart.preview_game_rig_action"
    bl_label = "Preview Game-Rig Action"
    bl_description = "Assign the selected baked action to the game rig and jump to its first frame"
    bl_options = {"REGISTER", "UNDO"}

    action_name: StringProperty(default="")

    def execute(self, context):
        try:
            result = preview_game_rig_action(
                context,
                action_name=self.action_name or getattr(context.scene, "voxtart_animation_preview_action", ""),
            )
        except (RuntimeError, ValueError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Previewing {result['action']} from frame {result['frame_start']}")
        return {"FINISHED"}


class VOXTART_OT_export_game_rig(bpy.types.Operator):
    bl_idname = "voxtart.export_game_rig"
    bl_label = "Export Game Rig"
    bl_description = "Export the extracted game rig and its bound meshes through Blender's native exporter"
    bl_options = {"REGISTER"}

    filepath: StringProperty(name="Filepath", subtype="FILE_PATH")
    export_format: EnumProperty(
        name="Format",
        items=(
            ("GLTF", "glTF", "Export a binary or separate-file glTF scene"),
            ("FBX", "FBX", "Export an FBX scene"),
        ),
        default="GLTF",
    )
    include_meshes: BoolProperty(name="Include Bound Meshes", default=True)

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            result = export_game_rig(
                context,
                self.filepath,
                export_format=self.export_format,
                include_meshes=self.include_meshes,
            )
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {result['target_armature']} to {result['filepath']}")
        return {"FINISHED"}


class VOXTART_OT_open_animation_url(bpy.types.Operator):
    bl_idname = "voxtart.open_animation_url"
    bl_label = "Open Animation Resource"
    bl_description = "Open the selected animation integration resource in a browser"

    url: StringProperty()

    def execute(self, _context):
        if not self.url.startswith(("https://", "http://")):
            self.report({"ERROR"}, "Only web URLs are allowed")
            return {"CANCELLED"}
        bpy.ops.wm.url_open(url=self.url)
        return {"FINISHED"}


class VOXTART_PT_animation_panel(bpy.types.Panel):
    bl_label = "VOXTART Animation Plugins"
    bl_idname = "VOXTART_PT_animation_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 20

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        status = plugin_status()
        quaternius = next(item for item in status if item["id"] == PLUGIN_ID)
        layout.label(text="Import a user-owned humanoid animation source")
        row = layout.row(align=True)
        import_button = row.operator(VOXTART_OT_import_animation_library.bl_idname, icon="FILE_FOLDER")
        import_button.filepath = ""
        link = row.operator(VOXTART_OT_open_animation_url.bl_idname, text="UAL page", icon="URL")
        link.url = QUATERNIUS_URL
        layout.label(text="Pack page lists CC0; keep the downloaded license with assets.", icon="INFO")

        layout.separator()
        layout.prop_search(scene, "voxtart_animation_source", bpy.data, "objects", text="Source")
        layout.prop_search(scene, "voxtart_animation_action", bpy.data, "actions", text="Action")
        layout.prop_search(scene, "voxtart_animation_target", bpy.data, "objects", text="Target")
        state = animation_state(context)
        layout.label(text=f"Suggested bone mappings: {state['suggested_mapping_count']}")
        layout.operator(VOXTART_OT_retarget_animation.bl_idname, icon="ARMATURE_DATA")
        layout.operator(VOXTART_OT_bind_character.bl_idname, icon="BONE_DATA")

        game_box = layout.box()
        game_box.label(text="Game Rig Extraction", icon="ARMATURE_DATA")
        game_box.prop(scene, "voxtart_game_rig_extract_mode", text="Keep")
        game_box.prop(scene, "voxtart_game_rig_hierarchy", text="Hierarchy")
        game_box.prop(scene, "voxtart_game_rig_include_root", text="Preserve Root")
        game_box.prop(scene, "voxtart_game_rig_root_bone", text="Root Bone")
        game_box.prop(scene, "voxtart_game_rig_keep_bones", text="Extra Bones")
        game_box.prop(scene, "voxtart_game_rig_bind_meshes", text="Bind Selected Meshes")
        game_box.operator(VOXTART_OT_extract_game_rig.bl_idname, icon="ARMATURE_DATA")

        bake_box = layout.box()
        bake_box.label(text="Action Bakery", icon="ACTION")
        bake_box.prop(scene, "voxtart_animation_bake_all_actions", text="All Source Actions")
        if not getattr(scene, "voxtart_animation_bake_all_actions", False):
            bake_box.prop(scene, "voxtart_animation_bake_action_names", text="Actions")
        bake_box.prop(scene, "voxtart_animation_bake_prefix", text="Name Prefix")
        bake_box.prop(scene, "voxtart_animation_bake_suffix", text="Name Suffix")
        bake_box.prop(scene, "voxtart_animation_bake_backend", text="Backend")
        bake_box.label(text=f"Available actions: {len(tuple(bpy.data.actions))}")
        bake_operator = bake_box.operator(VOXTART_OT_bake_game_rig_actions.bl_idname, icon="ACTION")
        bake_operator.backend = getattr(scene, "voxtart_animation_bake_backend", "NATIVE")

        output_box = layout.box()
        output_box.label(text="Game Rig Preview and Export", icon="EXPORT")
        output_box.prop_search(scene, "voxtart_animation_preview_action", bpy.data, "actions", text="Preview Action")
        preview_operator = output_box.operator(VOXTART_OT_preview_game_rig_action.bl_idname, icon="PLAY")
        preview_operator.action_name = getattr(scene, "voxtart_animation_preview_action", "")
        output_box.prop(scene, "voxtart_game_rig_export_format", text="Export Format")
        output_box.prop(scene, "voxtart_game_rig_export_meshes", text="Include Bound Meshes")
        export_operator = output_box.operator(VOXTART_OT_export_game_rig.bl_idname, icon="EXPORT")
        export_operator.export_format = getattr(scene, "voxtart_game_rig_export_format", "GLTF")
        export_operator.include_meshes = bool(getattr(scene, "voxtart_game_rig_export_meshes", True))

        target = _get_object(getattr(scene, "voxtart_animation_target", ""), "ARMATURE")
        if target is not None and not bool(target.get("voxtart_game_rig", False)):
            bake_box.label(text="Extract a game rig, then choose it as Target.", icon="INFO")

        mesh = _active_mesh(context)
        if mesh is not None:
            layout.operator(VOXTART_OT_prepare_character.bl_idname, icon="ARMATURE_DATA")
            layout.label(text="Blockout rig is editable; fit it before baking.", icon="INFO")

        layout.separator()
        layout.label(text="Optional integrations")
        for item in status:
            if item["id"] == PLUGIN_ID:
                continue
            icon = "CHECKMARK" if item["installed"] else "RADIOBUT_OFF"
            layout.label(text=f"{item['name']}: {'available' if item['installed'] else 'not enabled'}", icon=icon)
        row = layout.row(align=True)
        retarget_link = row.operator(VOXTART_OT_open_animation_url.bl_idname, text="Retarget add-on", icon="URL")
        retarget_link.url = ANIMATION_RETARGETING_URL
        rigify_link = row.operator(VOXTART_OT_open_animation_url.bl_idname, text="Rigify", icon="URL")
        rigify_link.url = RIGIFY_URL

_CLASSES = (
    VOXTART_OT_import_animation_library,
    VOXTART_OT_prepare_character,
    VOXTART_OT_bind_character,
    VOXTART_OT_retarget_animation,
    VOXTART_OT_extract_game_rig,
    VOXTART_OT_bake_game_rig_actions,
    VOXTART_OT_preview_game_rig_action,
    VOXTART_OT_export_game_rig,
    VOXTART_OT_open_animation_url,
    VOXTART_PT_animation_panel,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_animation_source = StringProperty(
        name="Source Armature",
        description="Source armature imported from an animation library",
        default="",
    )
    bpy.types.Scene.voxtart_animation_action = StringProperty(
        name="Animation Action",
        description="Action to retarget from the source armature",
        default="",
    )
    bpy.types.Scene.voxtart_animation_target = StringProperty(
        name="Target Armature",
        description="Target armature that deforms the character mesh",
        default="",
    )
    bpy.types.Scene.voxtart_animation_preview_action = StringProperty(
        name="Preview Action",
        description="Baked action assigned by the Game Rig preview control",
        default="",
    )
    bpy.types.Scene.voxtart_game_rig_export_format = EnumProperty(
        name="Game Rig Export Format",
        description="Native Blender exporter used for game-rig output",
        items=(
            ("GLTF", "glTF", "Export a binary or separate-file glTF scene"),
            ("FBX", "FBX", "Export an FBX scene"),
        ),
        default="GLTF",
    )
    bpy.types.Scene.voxtart_game_rig_export_meshes = BoolProperty(
        name="Include Bound Meshes",
        description="Include meshes bound to the selected game rig",
        default=True,
    )
    bpy.types.Scene.voxtart_game_rig_extract_mode = EnumProperty(
        name="Game Rig Bone Set",
        description="Choose which source bones become export bones",
        items=(
            ("DEFORM_ONLY", "Deform Bones", "Keep bones marked as deforming"),
            ("DEFORM_AND_SELECTED", "Deform + Selected", "Keep deform bones and selected control or attachment bones"),
            ("ALL_BONES", "All Bones", "Copy the complete source skeleton"),
        ),
        default="DEFORM_ONLY",
    )
    bpy.types.Scene.voxtart_game_rig_hierarchy = EnumProperty(
        name="Game Rig Hierarchy",
        description="Choose how omitted source parents are handled",
        items=(
            ("PRESERVE", "Preserve", "Leave selected bones unparented when their source parent is omitted"),
            ("FLATTEN_MISSING", "Flatten Missing Parents", "Reparent through omitted controls to the nearest kept ancestor"),
        ),
        default="FLATTEN_MISSING",
    )
    bpy.types.Scene.voxtart_game_rig_include_root = BoolProperty(
        name="Preserve Root",
        description="Include each source hierarchy root even when it is not deforming",
        default=True,
    )
    bpy.types.Scene.voxtart_game_rig_root_bone = StringProperty(
        name="Root Bone",
        description="Optional additional root or motion bone name",
        default="",
    )
    bpy.types.Scene.voxtart_game_rig_keep_bones = StringProperty(
        name="Extra Bones",
        description="Comma-separated attachment or custom bones to preserve",
        default="",
    )
    bpy.types.Scene.voxtart_game_rig_bind_meshes = BoolProperty(
        name="Bind Selected Meshes",
        description="Parent selected meshes to the extracted rig with automatic weights",
        default=False,
    )
    bpy.types.Scene.voxtart_animation_bake_action_names = StringProperty(
        name="Bakery Actions",
        description="Comma-separated source actions; blank uses the selected Action",
        default="",
    )
    bpy.types.Scene.voxtart_animation_bake_all_actions = BoolProperty(
        name="Bake All Actions",
        description="Bake every unbaked Blender action",
        default=False,
    )
    bpy.types.Scene.voxtart_animation_bake_prefix = StringProperty(
        name="Bakery Prefix",
        description="Prefix applied to every generated game-rig action",
        default="Game_",
    )
    bpy.types.Scene.voxtart_animation_bake_suffix = StringProperty(
        name="Bakery Suffix",
        description="Suffix applied to every generated game-rig action",
        default="",
    )
    bpy.types.Scene.voxtart_animation_bake_backend = EnumProperty(
        name="Bakery Backend",
        description="Retargeting implementation used by the Action Bakery",
        items=(
            ("AUTO", "Automatic", "Use the installed retargeting add-on when available"),
            ("MWNI", "Animation Retargeting", "Use the optional Animation Retargeting add-on"),
            ("NATIVE", "VOXTART Native", "Use VOXTART's constrained Blender fallback"),
        ),
        default="NATIVE",
    )


def unregister() -> None:
    for name in (
        "voxtart_game_rig_export_meshes",
        "voxtart_game_rig_export_format",
        "voxtart_animation_preview_action",
        "voxtart_animation_bake_backend",
        "voxtart_animation_bake_suffix",
        "voxtart_animation_bake_prefix",
        "voxtart_animation_bake_all_actions",
        "voxtart_animation_bake_action_names",
        "voxtart_game_rig_bind_meshes",
        "voxtart_game_rig_keep_bones",
        "voxtart_game_rig_root_bone",
        "voxtart_game_rig_include_root",
        "voxtart_game_rig_hierarchy",
        "voxtart_game_rig_extract_mode",
        "voxtart_animation_target",
        "voxtart_animation_action",
        "voxtart_animation_source",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
