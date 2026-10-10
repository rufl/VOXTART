# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Pure scene hierarchy data used by the Blender scene window."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


HIERARCHY_KINDS = ("ROOT", "COLLECTION", "ASSET")
_MAX_NAME = 256


def _text(value: object, field_name: str, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"hierarchy {field_name} must not be empty")
    if len(result) > _MAX_NAME:
        raise ValueError(f"hierarchy {field_name} is too long")
    return result


def _path(value: Iterable[object]) -> tuple[str, ...]:
    result = tuple(_text(item, "collection name", required=True) for item in value)
    if len(result) > 64:
        raise ValueError("hierarchy collection path is too deep")
    return result


@dataclass(frozen=True, slots=True)
class SceneHierarchyRecord:
    """One asset object and the metadata needed to place it in the tree."""

    object_name: str
    display_name: str = ""
    collection_path: tuple[str, ...] = ()
    parent_name: str = ""
    category: str = ""
    placement_id: str = ""
    visible: bool = True
    selected: bool = False

    def __post_init__(self) -> None:
        object_name = _text(self.object_name, "object name", required=True)
        display_name = _text(self.display_name, "display name") or object_name
        object.__setattr__(self, "object_name", object_name)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "collection_path", _path(self.collection_path))
        object.__setattr__(self, "parent_name", _text(self.parent_name, "parent name"))
        object.__setattr__(self, "category", _text(self.category, "category"))
        object.__setattr__(self, "placement_id", _text(self.placement_id, "placement id"))
        object.__setattr__(self, "visible", bool(self.visible))
        object.__setattr__(self, "selected", bool(self.selected))


@dataclass(slots=True)
class SceneHierarchyNode:
    """A collection or asset node in a deterministic scene tree."""

    name: str
    kind: str
    object_name: str = ""
    category: str = ""
    placement_id: str = ""
    visible: bool = True
    selected: bool = False
    children: list["SceneHierarchyNode"] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = _text(self.name, "node name", required=True)
        self.kind = str(self.kind or "").upper()
        if self.kind not in HIERARCHY_KINDS:
            raise ValueError(f"unsupported hierarchy node kind: {self.kind!r}")
        self.object_name = _text(self.object_name, "object name")
        self.category = _text(self.category, "category")
        self.placement_id = _text(self.placement_id, "placement id")
        self.visible = bool(self.visible)
        self.selected = bool(self.selected)

    @property
    def is_asset(self) -> bool:
        return self.kind == "ASSET"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "object_name": self.object_name,
            "category": self.category,
            "placement_id": self.placement_id,
            "visible": self.visible,
            "selected": self.selected,
            "children": [child.to_dict() for child in self.children],
        }


def _contains(node: SceneHierarchyNode, target: SceneHierarchyNode) -> bool:
    if node is target:
        return True
    return any(_contains(child, target) for child in node.children)


def _sort_tree(node: SceneHierarchyNode) -> None:
    node.children.sort(
        key=lambda child: (
            0 if child.kind == "COLLECTION" else 1,
            child.name.casefold(),
            child.object_name.casefold(),
        )
    )
    for child in node.children:
        _sort_tree(child)


def build_scene_hierarchy(
    records: Iterable[SceneHierarchyRecord],
    *,
    include_unplaced: bool = True,
) -> SceneHierarchyNode:
    """Build a collection/object tree from asset records.

    Collection paths provide the primary hierarchy. Asset parent relationships
    are applied afterward when both objects are present, while cycles are
    ignored so malformed Blender parenting cannot break the scene window.
    """

    normalized = tuple(record if isinstance(record, SceneHierarchyRecord) else SceneHierarchyRecord(**record) for record in records)
    filtered = tuple(
        record for record in normalized
        if include_unplaced or record.placement_id
    )
    object_names = [record.object_name for record in filtered]
    if len(object_names) != len(set(object_names)):
        raise ValueError("scene hierarchy object names must be unique")

    root = SceneHierarchyNode("Scene", "ROOT")
    collection_nodes: dict[tuple[str, ...], SceneHierarchyNode] = {}
    for record in sorted(filtered, key=lambda item: (item.collection_path, item.object_name.casefold())):
        parent = root
        prefix: tuple[str, ...] = ()
        for name in record.collection_path:
            prefix = (*prefix, name)
            node = collection_nodes.get(prefix)
            if node is None:
                node = SceneHierarchyNode(name, "COLLECTION")
                collection_nodes[prefix] = node
                parent.children.append(node)
            parent = node

    asset_nodes: dict[str, SceneHierarchyNode] = {}
    asset_parents: dict[str, str] = {}
    for record in filtered:
        node = SceneHierarchyNode(
            record.display_name,
            "ASSET",
            object_name=record.object_name,
            category=record.category,
            placement_id=record.placement_id,
            visible=record.visible,
            selected=record.selected,
        )
        asset_nodes[record.object_name] = node
        asset_parents[record.object_name] = record.parent_name
        parent = collection_nodes.get(record.collection_path, root)
        parent.children.append(node)

    for object_name in sorted(asset_nodes, key=str.casefold):
        parent_name = asset_parents[object_name]
        if not parent_name or parent_name not in asset_nodes:
            continue
        node = asset_nodes[object_name]
        parent = asset_nodes[parent_name]
        if node is parent or _contains(node, parent):
            continue
        for container in (root, *collection_nodes.values(), *asset_nodes.values()):
            if node in container.children:
                container.children.remove(node)
                break
        parent.children.append(node)

    _sort_tree(root)
    return root


def flatten_hierarchy(root: SceneHierarchyNode) -> tuple[tuple[SceneHierarchyNode, int], ...]:
    """Return depth-first rows suitable for a Blender panel or list widget."""

    rows: list[tuple[SceneHierarchyNode, int]] = []

    def visit(node: SceneHierarchyNode, depth: int) -> None:
        rows.append((node, depth))
        for child in node.children:
            visit(child, depth + 1)

    visit(root, 0)
    return tuple(rows)


__all__ = [
    "HIERARCHY_KINDS",
    "SceneHierarchyNode",
    "SceneHierarchyRecord",
    "build_scene_hierarchy",
    "flatten_hierarchy",
]
