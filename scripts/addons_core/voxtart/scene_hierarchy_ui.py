# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Blender scene window for browsing and selecting placed VOXTART assets."""


import json

import bpy
from bpy.props import BoolProperty, StringProperty

from .operators import ASSET_PROPERTY, MESH_BACKEND_PROPERTY
from .scene_assembly import ASSEMBLY_PROPERTY, SceneAssembly
from .scene_hierarchy import (
    SceneHierarchyNode,
    SceneHierarchyRecord,
    build_scene_hierarchy,
    flatten_hierarchy,
)


_HIERARCHY_SEARCH_PROPERTY = "voxtart_scene_hierarchy_search"
_INCLUDE_UNPLACED_PROPERTY = "voxtart_scene_hierarchy_include_unplaced"


def _is_asset(obj) -> bool:
    return obj is not None and ASSET_PROPERTY in obj


def _load_scene_assembly(scene: bpy.types.Scene) -> SceneAssembly:
    raw = scene.get(ASSEMBLY_PROPERTY)
    if not isinstance(raw, str) or not raw.strip():
        return SceneAssembly()
    try:
        return SceneAssembly.decode(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return SceneAssembly()


def _collection_path(scene: bpy.types.Scene, target) -> tuple[str, ...]:
    """Return the shortest deterministic collection path containing target."""

    paths: list[tuple[str, ...]] = []

    def visit(collection, path: tuple[str, ...]) -> None:
        if any(item.name == target.name for item in collection.objects):
            paths.append(path)
        for child in collection.children:
            visit(child, (*path, child.name))

    visit(scene.collection, ())
    return min(paths, key=lambda path: (len(path), tuple(item.casefold() for item in path))) if paths else ()


def _scene_hierarchy_records(context: bpy.types.Context) -> tuple[SceneHierarchyRecord, ...]:
    scene = context.scene
    assembly = _load_scene_assembly(scene)
    placements = {
        placement.object_name: placement
        for placement in assembly.placements
        if placement.object_name
    }
    records = []
    for obj in sorted((item for item in scene.objects if _is_asset(item)), key=lambda item: item.name.casefold()):
        placement = placements.get(obj.name)
        placement_id = str(
            obj.get("voxtart_zoo_placement_id", "")
            or (placement.placement_id if placement is not None else "")
        ).strip()
        records.append(
            SceneHierarchyRecord(
                object_name=obj.name,
                display_name=placement.name if placement is not None else obj.name,
                collection_path=_collection_path(scene, obj),
                parent_name=obj.parent.name if _is_asset(getattr(obj, "parent", None)) else "",
                category=placement.category if placement is not None else "",
                placement_id=placement_id,
                visible=not bool(getattr(obj, "hide_viewport", False)),
                selected=bool(obj.select_get()),
            )
        )
    return tuple(records)


def _node_matches(node: SceneHierarchyNode, query: str) -> bool:
    query = query.casefold()
    return any(
        query in value.casefold()
        for value in (node.name, node.object_name, node.category, node.placement_id)
        if value
    )

def _filtered_rows(root: SceneHierarchyNode, query: str) -> tuple[tuple[SceneHierarchyNode, int], ...]:
    query = str(query or "").strip()
    if not query:
        return flatten_hierarchy(root)

    def collect(node: SceneHierarchyNode, depth: int) -> tuple[bool, list[tuple[SceneHierarchyNode, int]]]:
        matching_children = False
        child_rows: list[tuple[SceneHierarchyNode, int]] = []
        for child in node.children:
            child_matches, nested_rows = collect(child, depth + 1)
            matching_children = matching_children or child_matches
            child_rows.extend(nested_rows)
        matches = node.kind == "ROOT" or _node_matches(node, query) or matching_children
        if not matches:
            return False, []
        return True, [(node, depth), *child_rows]

    _matches, rows = collect(root, 0)
    return tuple(rows)


def _row_label(node: SceneHierarchyNode) -> str:
    label = node.name
    if node.object_name and node.object_name != node.name:
        label = f"{label} · {node.object_name}"
    if node.category:
        label = f"{label} [{node.category}]"
    return label


def _draw_scene_inspector(layout, context) -> None:
    active = getattr(context.view_layer.objects, "active", None)
    inspector = layout.box()
    inspector.label(text="Inspector", icon="PROPERTIES")
    if active is None:
        inspector.label(text="Select an element in the hierarchy", icon="INFO")
        return
    inspector.prop(active, "name", text="Name")
    inspector.label(text=f"Type: {active.type}")
    parent = getattr(active, "parent", None)
    inspector.label(text=f"Parent: {parent.name if parent is not None else 'Scene'}", icon="CONSTRAINT")
    inspector.prop(active, "location", text="Location")
    inspector.prop(active, "rotation_euler", text="Rotation")
    inspector.prop(active, "scale", text="Scale")
    if not _is_asset(active):
        return
    try:
        from . import operators

        asset = operators._load_asset(active)
        stack = operators._load_layer_stack(active, asset)
    except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
        inspector.label(text=str(error), icon="ERROR")
        return
    inspector.separator()
    inspector.label(text=f"VOXTART · {len(asset.voxels)} voxels · {len(stack.layers)} layers", icon="MESH_CUBE")
    inspector.label(text=f"Active layer: {stack.active.name}")
    override = bool(active.get("voxtart_mesh_backend_override", False))
    stored_backend = str(active.get(MESH_BACKEND_PROPERTY, "greedy")).lower()
    layer_backend = str(getattr(stack.active, "surface_backend", "INHERIT")).lower()
    backend = layer_backend if not override and layer_backend in {"greedy", "marching"} else stored_backend
    inspector.label(text=f"Surface: {backend.title()}{' (object)' if override else ' (layer/object)'}", icon="MOD_SMOOTH")
    surface = inspector.row(align=True)
    greedy = surface.operator("voxtart.layer_surface", text="Greedy", icon="MESH_CUBE")
    greedy.scope = "OBJECT"
    greedy.backend = "GREEDY"
    marching = surface.operator("voxtart.layer_surface", text="Marching / Round", icon="MESH_UVSPHERE")
    marching.scope = "OBJECT"
    marching.backend = "MARCHING"
    inherit = inspector.operator("voxtart.layer_surface", text="Use Layer Surface", icon="FILE_REFRESH")
    inherit.scope = "OBJECT"
    inherit.backend = "INHERIT"

class VOXTART_OT_scene_hierarchy_select(bpy.types.Operator):
    bl_idname = "voxtart.scene_hierarchy_select"
    bl_label = "Select Scene Asset"
    bl_description = "Select the asset represented by this scene hierarchy row"

    object_name: StringProperty(name="Object", options={"HIDDEN"})
    extend: BoolProperty(name="Extend Selection", default=False, options={"HIDDEN"})

    def execute(self, context):
        target = bpy.data.objects.get(self.object_name)
        if target is None or target.name not in context.scene.objects:
            self.report({"ERROR"}, "scene hierarchy object is no longer available")
            return {"CANCELLED"}
        if not self.extend:
            for selected in context.selected_objects:
                selected.select_set(False)
        target.select_set(True)
        context.view_layer.objects.active = target
        return {"FINISHED"}


class VOXTART_PT_scene_hierarchy(bpy.types.Panel):
    bl_label = "Scene Hierarchy"
    bl_idname = "VOXTART_PT_scene_hierarchy"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 10

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        controls = layout.row(align=True)
        controls.prop(scene, _HIERARCHY_SEARCH_PROPERTY, text="", icon="VIEWZOOM")
        controls.prop(scene, _INCLUDE_UNPLACED_PROPERTY, text="Unplaced")
        controls.operator("voxtart.zoo_sync_placements", text="", icon="FILE_REFRESH")

        try:
            records = _scene_hierarchy_records(context)
            root = build_scene_hierarchy(
                records,
                include_unplaced=bool(getattr(scene, _INCLUDE_UNPLACED_PROPERTY, False)),
            )
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
            layout.label(text=str(error), icon="ERROR")
            _draw_scene_inspector(layout, context)
            return

        rows = _filtered_rows(root, getattr(scene, _HIERARCHY_SEARCH_PROPERTY, ""))
        if len(rows) == 1 and not root.children:
            message = "No placed VOXTART assets in this scene."
            if not bool(getattr(scene, _INCLUDE_UNPLACED_PROPERTY, False)):
                message += " Enable Unplaced to include unsaved assets."
            layout.label(text=message, icon="INFO")
            _draw_scene_inspector(layout, context)
            return

        asset_count = sum(1 for node, _depth in rows if node.is_asset)
        layout.label(text=f"{asset_count} asset row(s)", icon="OUTLINER_COLLECTION")
        for node, depth in rows:
            row = layout.row(align=True)
            prefix = "    " * depth
            if node.kind == "ASSET":
                button = row.operator(
                    VOXTART_OT_scene_hierarchy_select.bl_idname,
                    text=f"{prefix}{_row_label(node)}",
                    icon="MESH_CUBE",
                )
                button.object_name = node.object_name
                obj = bpy.data.objects.get(node.object_name)
                if obj is not None:
                    row.prop(obj, "hide_viewport", text="")
            else:
                row.label(text=f"{prefix}{node.name}", icon="OUTLINER_COLLECTION")
        _draw_scene_inspector(layout, context)


_CLASSES = (
    VOXTART_OT_scene_hierarchy_select,
    VOXTART_PT_scene_hierarchy,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_scene_hierarchy_search = StringProperty(
        name="Scene Hierarchy Search",
        description="Filter the placed asset hierarchy by name, category, or object",
        default="",
    )
    bpy.types.Scene.voxtart_scene_hierarchy_include_unplaced = BoolProperty(
        name="Include Unplaced",
        description="Show VOXTART assets that are not recorded as Zoo placements",
        default=False,
    )


def unregister() -> None:
    for name in (_INCLUDE_UNPLACED_PROPERTY, _HIERARCHY_SEARCH_PROPERTY):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "VOXTART_OT_scene_hierarchy_select",
    "VOXTART_PT_scene_hierarchy",
    "register",
    "unregister",
]
