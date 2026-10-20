# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Original modular creature parts packs and inspector-style presets."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from .creature import (
    CREATURE_FORMAT,
    CREATURE_FORMAT_VERSION,
    CREATURE_TEMPLATES,
    CreatureEdge,
    CreatureJoint,
    CreatureNode,
    CreatureRecipe,
)


PRESET_FORMAT = "voxtart.creature_preset"
PRESET_FORMAT_VERSION = 1
CREATURE_PART_PACKS = ("NATURAL", "FANTASY", "MECHANICAL")
CREATURE_INSPECTOR_PRESETS = ("BALANCED", "AGILE", "HEAVY", "ARMORED")
PART_ANCHOR = "@anchor"


def _text(value: Any, field_name: str, *, limit: int = 128, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"creature preset {field_name} must not be empty")
    if len(result) > limit:
        raise ValueError(f"creature preset {field_name} is too long")
    return result


def _point(value: Iterable[float], field_name: str) -> tuple[float, float, float]:
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"creature preset {field_name} must contain three finite values") from error
    if len(result) != 3 or any(not math.isfinite(item) or abs(item) > 1_000_000.0 for item in result):
        raise ValueError(f"creature preset {field_name} must contain three finite values")
    return result  # type: ignore[return-value]


def _finite(value: Any, field_name: str, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"creature preset {field_name} must be a finite number in [{minimum}, {maximum}]"
        ) from error
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(
            f"creature preset {field_name} must be a finite number in [{minimum}, {maximum}]"
        )
    return result


@dataclass(frozen=True, slots=True)
class CreaturePartNode:
    """One node expressed relative to a part preset's anchor node."""

    id: str
    offset: tuple[float, float, float]
    radius: float = 0.5
    role: str = ""
    deform_width: float = 1.0
    deform_thickness: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "part node id", limit=64, required=True))
        object.__setattr__(self, "offset", _point(self.offset, "part node offset"))
        object.__setattr__(self, "radius", _finite(self.radius, "part node radius", 0.05, 4096.0))
        object.__setattr__(self, "role", _text(self.role, "part node role", limit=96))
        object.__setattr__(self, "deform_width", _finite(self.deform_width, "part node deform width", 0.05, 16.0))
        object.__setattr__(self, "deform_thickness", _finite(self.deform_thickness, "part node deform thickness", 0.05, 16.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "offset": list(self.offset),
            "radius": self.radius,
            "role": self.role,
            "deform_width": self.deform_width,
            "deform_thickness": self.deform_thickness,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreaturePartNode":
        if not isinstance(data, Mapping):
            raise ValueError("creature preset part node must be a JSON object")
        return cls(
            id=data.get("id", ""),
            offset=data.get("offset", (0.0, 0.0, 0.0)),
            radius=data.get("radius", 0.5),
            role=data.get("role", ""),
            deform_width=data.get("deform_width", 1.0),
            deform_thickness=data.get("deform_thickness", 1.0),
        )


@dataclass(frozen=True, slots=True)
class CreaturePartEdge:
    """An edge inside a modular part; ``@anchor`` references the base graph."""

    id: str
    start: str
    end: str
    part: str
    bone: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "part edge id", limit=64, required=True))
        object.__setattr__(self, "start", _text(self.start, "part edge start", limit=64, required=True))
        object.__setattr__(self, "end", _text(self.end, "part edge end", limit=64, required=True))
        object.__setattr__(self, "part", _text(self.part, "part edge label", limit=96, required=True))
        object.__setattr__(self, "bone", _text(self.bone, "part edge bone", limit=128))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "part": self.part,
            "bone": self.bone,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreaturePartEdge":
        if not isinstance(data, Mapping):
            raise ValueError("creature preset part edge must be a JSON object")
        return cls(
            id=data.get("id", ""),
            start=data.get("start", ""),
            end=data.get("end", ""),
            part=data.get("part", ""),
            bone=data.get("bone", ""),
        )


@dataclass(frozen=True, slots=True)
class CreaturePartJoint:
    """A named socket emitted by a modular part."""

    node_id: str
    name: str
    role: str = "custom"
    tag: str = "creature_part"
    bone: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _text(self.node_id, "part joint node id", limit=64, required=True))
        object.__setattr__(self, "name", _text(self.name, "part joint name", limit=96, required=True))
        object.__setattr__(self, "role", _text(self.role, "part joint role", limit=96) or "custom")
        object.__setattr__(self, "tag", _text(self.tag, "part joint tag", limit=96, required=True))
        object.__setattr__(self, "bone", _text(self.bone, "part joint bone", limit=128))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "role": self.role,
            "tag": self.tag,
            "bone": self.bone,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreaturePartJoint":
        if not isinstance(data, Mapping):
            raise ValueError("creature preset part joint must be a JSON object")
        return cls(
            node_id=data.get("node_id", ""),
            name=data.get("name", ""),
            role=data.get("role", "custom"),
            tag=data.get("tag", "creature_part"),
            bone=data.get("bone", ""),
        )


@dataclass(frozen=True, slots=True)
class CreaturePartPreset:
    """Reusable part fragment attached to a named creature graph node."""

    id: str
    name: str
    category: str
    anchor: str
    nodes: tuple[CreaturePartNode, ...]
    edges: tuple[CreaturePartEdge, ...]
    joints: tuple[CreaturePartJoint, ...] = ()
    description: str = ""
    templates: tuple[str, ...] = CREATURE_TEMPLATES

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "part id", limit=64, required=True).upper())
        object.__setattr__(self, "name", _text(self.name, "part name", limit=128, required=True))
        object.__setattr__(self, "category", _text(self.category, "part category", limit=64, required=True))
        object.__setattr__(self, "anchor", _text(self.anchor, "part anchor", limit=64, required=True))
        object.__setattr__(self, "description", _text(self.description, "part description", limit=512))
        templates = tuple(str(template or "").upper() for template in self.templates)
        if not templates or any(template not in (*CREATURE_TEMPLATES, "CUSTOM") for template in templates):
            raise ValueError("creature preset part templates are invalid")
        object.__setattr__(self, "templates", templates)
        nodes = tuple(self.nodes)
        edges = tuple(self.edges)
        joints = tuple(self.joints)
        if not nodes:
            raise ValueError(f"creature preset part {self.id!r} must contain nodes")
        if any(not isinstance(node, CreaturePartNode) for node in nodes):
            raise TypeError("creature preset part nodes must contain CreaturePartNode values")
        if any(not isinstance(edge, CreaturePartEdge) for edge in edges):
            raise TypeError("creature preset part edges must contain CreaturePartEdge values")
        if any(not isinstance(joint, CreaturePartJoint) for joint in joints):
            raise TypeError("creature preset part joints must contain CreaturePartJoint values")
        node_ids = [node.id for node in nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError(f"creature preset part {self.id!r} node ids must be unique")
        edge_ids = [edge.id for edge in edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError(f"creature preset part {self.id!r} edge ids must be unique")
        valid_ids = set(node_ids) | {PART_ANCHOR}
        for edge in edges:
            if edge.start not in valid_ids or edge.end not in valid_ids or edge.start == edge.end:
                raise ValueError(f"creature preset part {self.id!r} has an invalid edge reference")
        for joint in joints:
            if joint.node_id not in node_ids:
                raise ValueError(f"creature preset part {self.id!r} has an invalid joint reference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "anchor": self.anchor,
            "description": self.description,
            "templates": list(self.templates),
            "nodes": [node.to_dict() for node in sorted(self.nodes, key=lambda item: item.id)],
            "edges": [edge.to_dict() for edge in sorted(self.edges, key=lambda item: item.id)],
            "joints": [joint.to_dict() for joint in sorted(self.joints, key=lambda item: item.name)],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreaturePartPreset":
        if not isinstance(data, Mapping):
            raise ValueError("creature preset part must be a JSON object")
        raw_nodes = data.get("nodes", ())
        raw_edges = data.get("edges", ())
        raw_joints = data.get("joints", ())
        if not all(isinstance(value, (list, tuple)) for value in (raw_nodes, raw_edges, raw_joints)):
            raise ValueError("creature preset part arrays must be JSON lists")
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            category=data.get("category", ""),
            anchor=data.get("anchor", ""),
            description=data.get("description", ""),
            templates=data.get("templates", CREATURE_TEMPLATES),
            nodes=tuple(CreaturePartNode.from_dict(item) for item in raw_nodes),
            edges=tuple(CreaturePartEdge.from_dict(item) for item in raw_edges),
            joints=tuple(CreaturePartJoint.from_dict(item) for item in raw_joints),
        )


@dataclass(frozen=True, slots=True)
class CreaturePartsPack:
    """Named collection of original modular creature parts."""

    id: str
    name: str
    description: str
    parts: tuple[CreaturePartPreset, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "parts pack id", limit=64, required=True).upper())
        object.__setattr__(self, "name", _text(self.name, "parts pack name", limit=128, required=True))
        object.__setattr__(self, "description", _text(self.description, "parts pack description", limit=512))
        parts = tuple(self.parts)
        if any(not isinstance(part, CreaturePartPreset) for part in parts):
            raise TypeError("parts packs must contain CreaturePartPreset values")
        ids = [part.id for part in parts]
        if len(ids) != len(set(ids)):
            raise ValueError(f"parts pack {self.id!r} contains duplicate parts")
        object.__setattr__(self, "parts", parts)

    def get(self, part_id: str) -> CreaturePartPreset | None:
        normalized = str(part_id or "").upper()
        return next((part for part in self.parts if part.id == normalized), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "parts": [part.to_dict() for part in sorted(self.parts, key=lambda item: item.id)],
        }


@dataclass(frozen=True, slots=True)
class CreatureInspectorPreset:
    """Radius/deformation profile shown by the creature inspector."""

    id: str
    name: str
    description: str
    body_radius_scale: float = 1.0
    limb_radius_scale: float = 1.0
    head_radius_scale: float = 1.0
    part_radius_scale: float = 1.0
    deform_scale: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "inspector preset id", limit=64, required=True).upper())
        object.__setattr__(self, "name", _text(self.name, "inspector preset name", limit=128, required=True))
        object.__setattr__(self, "description", _text(self.description, "inspector preset description", limit=512))
        for field_name in (
            "body_radius_scale",
            "limb_radius_scale",
            "head_radius_scale",
            "part_radius_scale",
            "deform_scale",
        ):
            object.__setattr__(self, field_name, _finite(getattr(self, field_name), field_name, 0.25, 4.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "body_radius_scale": self.body_radius_scale,
            "limb_radius_scale": self.limb_radius_scale,
            "head_radius_scale": self.head_radius_scale,
            "part_radius_scale": self.part_radius_scale,
            "deform_scale": self.deform_scale,
        }


@dataclass(frozen=True, slots=True)
class CreaturePreset:
    """Serializable composition of a template, parts pack, and inspector profile."""

    template: str
    parts_pack: str = "NATURAL"
    inspector_preset: str = "BALANCED"
    enabled_parts: tuple[str, ...] = ()
    name: str = "Creature Preset"
    schema_version: int = PRESET_FORMAT_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != PRESET_FORMAT_VERSION
        ):
            raise ValueError(f"unsupported creature preset version: {self.schema_version}")
        template = str(self.template or "").upper()
        if template not in (*CREATURE_TEMPLATES, "CUSTOM"):
            raise ValueError(f"unsupported creature preset template: {self.template!r}")
        parts_pack = _text(self.parts_pack, "parts pack", limit=64, required=True).upper()
        if parts_pack not in CREATURE_PART_PACKS:
            raise ValueError(f"unsupported creature preset parts pack: {self.parts_pack!r}")
        inspector_preset = _text(
            self.inspector_preset,
            "inspector preset",
            limit=64,
            required=True,
        ).upper()
        if inspector_preset not in CREATURE_INSPECTOR_PRESETS:
            raise ValueError(
                f"unsupported creature inspector preset: {self.inspector_preset!r}"
            )
        object.__setattr__(self, "template", template)
        object.__setattr__(self, "parts_pack", parts_pack)
        object.__setattr__(self, "inspector_preset", inspector_preset)
        object.__setattr__(self, "name", _text(self.name, "preset name", limit=128, required=True))
        enabled = tuple(_text(item, "enabled part", limit=64, required=True).upper() for item in self.enabled_parts)
        if len(enabled) != len(set(enabled)):
            raise ValueError("creature preset enabled parts must be unique")
        object.__setattr__(self, "enabled_parts", enabled)
        available_parts = {part.id for part in get_parts_pack(parts_pack).parts}
        unknown = [part_id for part_id in enabled if part_id not in available_parts]
        if unknown:
            raise ValueError(
                f"creature preset contains unknown parts: {', '.join(sorted(unknown))}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": PRESET_FORMAT,
            "format_version": PRESET_FORMAT_VERSION,
            "template": self.template,
            "parts_pack": self.parts_pack,
            "inspector_preset": self.inspector_preset,
            "enabled_parts": list(self.enabled_parts),
            "name": self.name,
        }

    def encode(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreaturePreset":
        if not isinstance(data, Mapping):
            raise ValueError("creature preset must be a JSON object")
        if data.get("format") != PRESET_FORMAT:
            raise ValueError(f"unsupported creature preset format: {data.get('format')!r}")
        version = data.get("format_version", -1)
        if isinstance(version, bool) or not isinstance(version, int) or version != PRESET_FORMAT_VERSION:
            raise ValueError("unsupported creature preset version")
        enabled = data.get("enabled_parts", ())
        if not isinstance(enabled, (list, tuple)):
            raise ValueError("creature preset enabled parts must be a JSON list")
        return cls(
            template=data.get("template", "CUSTOM"),
            parts_pack=data.get("parts_pack", "NATURAL"),
            inspector_preset=data.get("inspector_preset", "BALANCED"),
            enabled_parts=tuple(enabled),
            name=data.get("name", "Creature Preset"),
            schema_version=PRESET_FORMAT_VERSION,
        )

    @classmethod
    def decode(cls, encoded: str) -> "CreaturePreset":
        try:
            payload = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("creature preset must be valid JSON") from error
        return cls.from_dict(payload)



def _part(
    part_id: str,
    name: str,
    category: str,
    anchor: str,
    description: str,
    nodes: tuple[CreaturePartNode, ...],
    edges: tuple[CreaturePartEdge, ...],
    joints: tuple[CreaturePartJoint, ...] = (),
) -> CreaturePartPreset:
    return CreaturePartPreset(
        id=part_id,
        name=name,
        category=category,
        anchor=anchor,
        description=description,
        nodes=nodes,
        edges=edges,
        joints=joints,
    )


_NATURAL_PACK = CreaturePartsPack(
    id="NATURAL",
    name="Natural Anatomy",
    description="Original ears, muzzle, and mane parts for animal-like silhouettes.",
    parts=(
        _part(
            "EARS",
            "Ears",
            "Face",
            "head",
            "Paired expressive ears.",
            (
                CreaturePartNode("left", (-0.95, 0.0, 0.85), 0.52, "EAR_L"),
                CreaturePartNode("right", (0.95, 0.0, 0.85), 0.52, "EAR_R"),
            ),
            (
                CreaturePartEdge("left_edge", PART_ANCHOR, "left", "EAR_L", "ear.L"),
                CreaturePartEdge("right_edge", PART_ANCHOR, "right", "EAR_R", "ear.R"),
            ),
            (
                CreaturePartJoint("left", "ear_l", "EAR_L", "creature_ear", "ear.L"),
                CreaturePartJoint("right", "ear_r", "EAR_R", "creature_ear", "ear.R"),
            ),
        ),
        _part(
            "MUZZLE",
            "Muzzle",
            "Face",
            "head",
            "A short forward-facing muzzle.",
            (CreaturePartNode("tip", (0.0, -1.2, -0.2), 0.62, "MUZZLE"),),
            (CreaturePartEdge("tip_edge", PART_ANCHOR, "tip", "MUZZLE", "muzzle"),),
            (CreaturePartJoint("tip", "muzzle", "MUZZLE", "creature_muzzle", "muzzle"),),
        ),
        _part(
            "MANE",
            "Mane",
            "Torso",
            "neck",
            "A compact dorsal mane for a stronger silhouette.",
            (
                CreaturePartNode("left", (-0.7, 0.2, 0.2), 0.42, "MANE_L"),
                CreaturePartNode("right", (0.7, 0.2, 0.2), 0.42, "MANE_R"),
            ),
            (
                CreaturePartEdge("left_edge", PART_ANCHOR, "left", "MANE_L", "mane.L"),
                CreaturePartEdge("right_edge", PART_ANCHOR, "right", "MANE_R", "mane.R"),
            ),
        ),
    ),
)

_FANTASY_PACK = CreaturePartsPack(
    id="FANTASY",
    name="Fantasy Anatomy",
    description="Original horns, wings, and crest parts for fantastical silhouettes.",
    parts=(
        _part(
            "HORNS",
            "Horns",
            "Face",
            "head",
            "Paired swept horns with terminal sockets.",
            (
                CreaturePartNode("left_base", (-0.55, 0.0, 0.9), 0.45, "HORN_L_BASE"),
                CreaturePartNode("left_tip", (-0.85, 0.0, 2.0), 0.25, "HORN_L_TIP"),
                CreaturePartNode("right_base", (0.55, 0.0, 0.9), 0.45, "HORN_R_BASE"),
                CreaturePartNode("right_tip", (0.85, 0.0, 2.0), 0.25, "HORN_R_TIP"),
            ),
            (
                CreaturePartEdge("left_root", PART_ANCHOR, "left_base", "HORN_L", "horn.L"),
                CreaturePartEdge("left_tip_edge", "left_base", "left_tip", "HORN_L", "horn.L"),
                CreaturePartEdge("right_root", PART_ANCHOR, "right_base", "HORN_R", "horn.R"),
                CreaturePartEdge("right_tip_edge", "right_base", "right_tip", "HORN_R", "horn.R"),
            ),
            (
                CreaturePartJoint("left_tip", "horn_l", "HORN_L", "creature_horn", "horn.L"),
                CreaturePartJoint("right_tip", "horn_r", "HORN_R", "creature_horn", "horn.R"),
            ),
        ),
        _part(
            "WINGS",
            "Wings",
            "Back",
            "chest",
            "Paired swept wings with terminal sockets.",
            (
                CreaturePartNode("left_root", (-1.25, 0.15, 0.35), 0.50, "WING_L_ROOT"),
                CreaturePartNode("left_tip", (-3.25, 0.8, 1.65), 0.28, "WING_L_TIP"),
                CreaturePartNode("right_root", (1.25, 0.15, 0.35), 0.50, "WING_R_ROOT"),
                CreaturePartNode("right_tip", (3.25, 0.8, 1.65), 0.28, "WING_R_TIP"),
            ),
            (
                CreaturePartEdge("left_root_edge", PART_ANCHOR, "left_root", "WING_L", "wing.L"),
                CreaturePartEdge("left_tip_edge", "left_root", "left_tip", "WING_L", "wing.L"),
                CreaturePartEdge("right_root_edge", PART_ANCHOR, "right_root", "WING_R", "wing.R"),
                CreaturePartEdge("right_tip_edge", "right_root", "right_tip", "WING_R", "wing.R"),
            ),
            (
                CreaturePartJoint("left_tip", "wing_l", "WING_L", "creature_wing", "wing.L"),
                CreaturePartJoint("right_tip", "wing_r", "WING_R", "creature_wing", "wing.R"),
            ),
        ),
        _part(
            "CREST",
            "Crest",
            "Head",
            "neck",
            "A dorsal crest behind the head.",
            (CreaturePartNode("tip", (0.0, 0.35, 1.15), 0.42, "CREST"),),
            (CreaturePartEdge("tip_edge", PART_ANCHOR, "tip", "CREST", "crest"),),
            (CreaturePartJoint("tip", "crest", "CREST", "creature_crest", "crest"),),
        ),
    ),
)

_MECHANICAL_PACK = CreaturePartsPack(
    id="MECHANICAL",
    name="Mechanical Anatomy",
    description="Original antenna, shoulder plate, and tail-thruster parts.",
    parts=(
        _part(
            "ANTENNA",
            "Antenna",
            "Sensor",
            "head",
            "A centered sensor mast.",
            (
                CreaturePartNode("stem", (0.0, 0.0, 1.15), 0.34, "ANTENNA_STEM"),
                CreaturePartNode("tip", (0.0, 0.0, 2.15), 0.28, "ANTENNA_TIP"),
            ),
            (
                CreaturePartEdge("stem_edge", PART_ANCHOR, "stem", "ANTENNA", "antenna"),
                CreaturePartEdge("tip_edge", "stem", "tip", "ANTENNA", "antenna"),
            ),
            (CreaturePartJoint("tip", "antenna", "ANTENNA", "creature_sensor", "antenna"),),
        ),
        _part(
            "SHOULDER_PLATES",
            "Shoulder Plates",
            "Armor",
            "chest",
            "Paired angular shoulder plates.",
            (
                CreaturePartNode("left", (-1.85, 0.0, 0.30), 0.68, "SHOULDER_PLATE_L"),
                CreaturePartNode("right", (1.85, 0.0, 0.30), 0.68, "SHOULDER_PLATE_R"),
            ),
            (
                CreaturePartEdge("left_edge", PART_ANCHOR, "left", "SHOULDER_PLATE_L", "plate.L"),
                CreaturePartEdge("right_edge", PART_ANCHOR, "right", "SHOULDER_PLATE_R", "plate.R"),
            ),
        ),
        _part(
            "TAIL_THRUSTERS",
            "Tail Thrusters",
            "Propulsion",
            "tail_base",
            "Twin propulsion pods at the tail base.",
            (
                CreaturePartNode("left", (-0.55, 0.15, -0.15), 0.48, "THRUSTER_L"),
                CreaturePartNode("right", (0.55, 0.15, -0.15), 0.48, "THRUSTER_R"),
            ),
            (
                CreaturePartEdge("left_edge", PART_ANCHOR, "left", "THRUSTER_L", "thruster.L"),
                CreaturePartEdge("right_edge", PART_ANCHOR, "right", "THRUSTER_R", "thruster.R"),
            ),
        ),
    ),
)

_PART_PACK_MAP = {
    pack.id: pack
    for pack in (_NATURAL_PACK, _FANTASY_PACK, _MECHANICAL_PACK)
}

_INSPECTOR_PRESET_MAP = {
    "BALANCED": CreatureInspectorPreset(
        "BALANCED", "Balanced", "Neutral body and limb proportions."
    ),
    "AGILE": CreatureInspectorPreset(
        "AGILE", "Agile", "Slim limbs and lighter volume for quick silhouettes.",
        body_radius_scale=0.92,
        limb_radius_scale=0.78,
        head_radius_scale=0.92,
        part_radius_scale=0.88,
        deform_scale=0.92,
    ),
    "HEAVY": CreatureInspectorPreset(
        "HEAVY", "Heavy", "Broad body and robust limbs for a grounded silhouette.",
        body_radius_scale=1.28,
        limb_radius_scale=1.12,
        head_radius_scale=1.12,
        part_radius_scale=1.08,
        deform_scale=1.08,
    ),
    "ARMORED": CreatureInspectorPreset(
        "ARMORED", "Armored", "Dense body volume with reinforced modular parts.",
        body_radius_scale=1.18,
        limb_radius_scale=1.02,
        head_radius_scale=1.08,
        part_radius_scale=1.24,
        deform_scale=1.14,
    ),
}


def get_parts_pack(pack_id: str) -> CreaturePartsPack:
    normalized = str(pack_id or "").upper()
    try:
        return _PART_PACK_MAP[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported creature parts pack: {pack_id!r}") from error


def get_inspector_preset(preset_id: str) -> CreatureInspectorPreset:
    normalized = str(preset_id or "").upper()
    try:
        return _INSPECTOR_PRESET_MAP[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported creature inspector preset: {preset_id!r}") from error


def make_creature_preset(
    template: str,
    *,
    parts_pack: str = "NATURAL",
    inspector_preset: str = "BALANCED",
    enabled_parts: Iterable[str] = (),
    name: str = "Creature Preset",
) -> CreaturePreset:
    preset = CreaturePreset(
        template=template,
        parts_pack=parts_pack,
        inspector_preset=inspector_preset,
        enabled_parts=tuple(enabled_parts),
        name=name,
    )
    pack = get_parts_pack(preset.parts_pack)
    get_inspector_preset(preset.inspector_preset)
    if preset.template not in CREATURE_TEMPLATES and preset.template != "CUSTOM":
        raise ValueError(f"unsupported creature preset template: {preset.template!r}")
    available = {part.id for part in pack.parts}
    if any(part_id not in available for part_id in preset.enabled_parts):
        raise ValueError("creature preset contains an unknown enabled part")
    return preset


def _node_scale(node: CreatureNode, inspector: CreatureInspectorPreset) -> CreatureNode:
    identity = f"{node.id} {node.role}".upper()
    if any(token in identity for token in ("HEAD", "NECK")):
        radius_scale = inspector.head_radius_scale
    elif any(token in identity for token in ("SHOULDER", "ELBOW", "HAND", "HIP", "KNEE", "FOOT", "PAW")):
        radius_scale = inspector.limb_radius_scale
    else:
        radius_scale = inspector.body_radius_scale
    return replace(
        node,
        radius=node.radius * radius_scale,
        deform_width=node.deform_width * inspector.deform_scale,
        deform_thickness=node.deform_thickness * inspector.deform_scale,
    )


def apply_creature_preset(recipe: CreatureRecipe, preset: CreaturePreset) -> CreatureRecipe:
    """Compose a parts pack and inspector profile onto a base graph recipe."""

    if not isinstance(recipe, CreatureRecipe):
        raise TypeError("creature preset application requires a CreatureRecipe")
    if not isinstance(preset, CreaturePreset):
        raise TypeError("creature preset application requires a CreaturePreset")
    if preset.template not in (recipe.template, "CUSTOM"):
        raise ValueError(
            f"creature preset template {preset.template!r} does not match {recipe.template!r}"
        )
    pack = get_parts_pack(preset.parts_pack)
    inspector = get_inspector_preset(preset.inspector_preset)
    selected_ids = tuple(preset.enabled_parts) if preset.enabled_parts else tuple(part.id for part in pack.parts)
    available = {part.id for part in pack.parts}
    unknown = [part_id for part_id in selected_ids if part_id not in available]
    if unknown:
        raise ValueError(f"creature preset contains unknown parts: {', '.join(sorted(unknown))}")

    nodes = [_node_scale(node, inspector) for node in recipe.nodes]
    edges = list(recipe.edges)
    joints = list(recipe.joints)
    node_by_id = {node.id: node for node in nodes}
    edge_ids = {edge.id for edge in edges}
    node_ids = set(node_by_id)
    joint_names = {joint.name for joint in joints}
    applied_parts: list[str] = []

    for part in sorted((pack.get(part_id) for part_id in selected_ids), key=lambda item: item.id):
        if part is None:
            continue
        if recipe.template not in part.templates and "CUSTOM" not in part.templates:
            continue
        anchor = node_by_id.get(part.anchor)
        if anchor is None:
            raise ValueError(
                f"creature part {part.id!r} requires missing anchor node {part.anchor!r}"
            )
        prefix = f"{part.id.lower()}_"
        local_to_global: dict[str, str] = {}
        for local_node in part.nodes:
            global_id = prefix + local_node.id
            if global_id in node_ids:
                raise ValueError(f"creature part node id collision: {global_id!r}")
            position = tuple(anchor.position[index] + local_node.offset[index] for index in range(3))
            node = CreatureNode(
                id=global_id,
                position=position,
                radius=local_node.radius * inspector.part_radius_scale,
                role=local_node.role,
                deform_width=local_node.deform_width * inspector.deform_scale,
                deform_thickness=local_node.deform_thickness * inspector.deform_scale,
            )
            nodes.append(node)
            node_by_id[global_id] = node
            node_ids.add(global_id)
            local_to_global[local_node.id] = global_id
        for local_edge in part.edges:
            global_id = prefix + local_edge.id
            if global_id in edge_ids:
                raise ValueError(f"creature part edge id collision: {global_id!r}")
            start = anchor.id if local_edge.start == PART_ANCHOR else local_to_global[local_edge.start]
            end = anchor.id if local_edge.end == PART_ANCHOR else local_to_global[local_edge.end]
            edges.append(
                CreatureEdge(
                    id=global_id,
                    start=start,
                    end=end,
                    part=local_edge.part,
                    bone=local_edge.bone,
                )
            )
            edge_ids.add(global_id)
        for local_joint in part.joints:
            name = f"{part.id.lower()}_{local_joint.name}"
            if name in joint_names:
                raise ValueError(f"creature part joint name collision: {name!r}")
            joints.append(
                CreatureJoint(
                    node_id=local_to_global[local_joint.node_id],
                    name=name,
                    role=local_joint.role,
                    tag=local_joint.tag,
                    bone=local_joint.bone,
                )
            )
            joint_names.add(name)
        applied_parts.append(part.id)

    settings = dict(recipe.settings)
    settings.update({
        "parts_pack": pack.id,
        "inspector_preset": inspector.id,
        "preset_parts": sorted(applied_parts),
        "preset": preset.to_dict(),
    })
    return CreatureRecipe(
        name=recipe.name,
        template=recipe.template,
        nodes=tuple(nodes),
        edges=tuple(edges),
        joints=tuple(joints),
        grid_size=recipe.grid_size,
        settings=settings,
    )


def preset_from_recipe(recipe: CreatureRecipe) -> CreaturePreset:
    """Recover the composed preset metadata from a generated recipe."""

    if not isinstance(recipe, CreatureRecipe):
        raise TypeError("preset inspection requires a CreatureRecipe")
    raw = recipe.settings.get("preset")
    if isinstance(raw, Mapping):
        return CreaturePreset.from_dict(raw)
    return make_creature_preset(
        recipe.template,
        parts_pack=recipe.settings.get("parts_pack", "NATURAL"),
        inspector_preset=recipe.settings.get("inspector_preset", "BALANCED"),
        enabled_parts=recipe.settings.get("preset_parts", ()),
    )


__all__ = [
    "CREATURE_INSPECTOR_PRESETS",
    "CREATURE_PART_PACKS",
    "PART_ANCHOR",
    "PRESET_FORMAT",
    "PRESET_FORMAT_VERSION",
    "CreatureInspectorPreset",
    "CreaturePartEdge",
    "CreaturePartJoint",
    "CreaturePartNode",
    "CreaturePartPreset",
    "CreaturePartsPack",
    "CreaturePreset",
    "apply_creature_preset",
    "get_inspector_preset",
    "get_parts_pack",
    "make_creature_preset",
    "preset_from_recipe",
]
