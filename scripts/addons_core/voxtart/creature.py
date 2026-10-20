# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic graph-authored creature templates and voxel generation."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .voxel import Cell, Color, VoxelAsset, VoxelSocket


CREATURE_FORMAT = "voxtart.creature"
CREATURE_FORMAT_VERSION = 1
CREATURE_TEMPLATES = ("BIPED", "QUADRUPED")
_CREATURE_TEMPLATE_VALUES = (*CREATURE_TEMPLATES, "CUSTOM")
MAX_CREATURE_NODES = 256
MAX_CREATURE_EDGES = 512
MAX_CREATURE_VOXELS = 1_000_000
_DEFAULT_CREATURE_PALETTE: tuple[Color, ...] = ((0.56, 0.64, 0.72, 1.0),)
_AXIS_DIRECTIONS: tuple[Cell, ...] = (
    (1, 0, 0),
    (-1, 0, 0),
    (0, 1, 0),
    (0, -1, 0),
    (0, 0, 1),
    (0, 0, -1),
)


Point = tuple[float, float, float]


def _text(value: Any, field_name: str, *, limit: int = 128, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"creature {field_name} must not be empty")
    if len(result) > limit:
        raise ValueError(f"creature {field_name} is too long")
    return result


def _finite_range(value: Any, field_name: str, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"creature {field_name} must be a finite number in [{minimum}, {maximum}]"
        ) from error
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(
            f"creature {field_name} must be a finite number in [{minimum}, {maximum}]"
        )
    return result


def _point(value: Iterable[float], field_name: str) -> Point:
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"creature {field_name} must contain three finite values") from error
    if len(result) != 3 or any(not math.isfinite(item) or abs(item) > 1_000_000.0 for item in result):
        raise ValueError(f"creature {field_name} must contain three finite values")
    return result  # type: ignore[return-value]


def _settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("creature settings must be a mapping")
    result = {str(key): item for key, item in value.items()}
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("creature settings must be finite JSON") from error
    return result


def _role_label(node: "CreatureNode") -> str:
    return node.role or node.id.replace("-", "_").upper()


@dataclass(frozen=True, slots=True)
class CreatureNode:
    """A radius-bearing graph node used by the creature sweeper."""

    id: str
    position: Point
    radius: float = 1.0
    role: str = ""
    deform_width: float = 1.0
    deform_thickness: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "node id", limit=64, required=True))
        object.__setattr__(self, "position", _point(self.position, "node position"))
        object.__setattr__(self, "radius", _finite_range(self.radius, "node radius", 0.05, 4096.0))
        object.__setattr__(self, "role", _text(self.role, "node role", limit=96))
        object.__setattr__(
            self,
            "deform_width",
            _finite_range(self.deform_width, "node deform width", 0.05, 16.0),
        )
        object.__setattr__(
            self,
            "deform_thickness",
            _finite_range(self.deform_thickness, "node deform thickness", 0.05, 16.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "position": list(self.position),
            "radius": self.radius,
            "role": self.role,
            "deform_width": self.deform_width,
            "deform_thickness": self.deform_thickness,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreatureNode":
        if not isinstance(data, Mapping):
            raise ValueError("creature node must be a JSON object")
        return cls(
            id=data.get("id", ""),
            position=data.get("position", (0.0, 0.0, 0.0)),
            radius=data.get("radius", 1.0),
            role=data.get("role", ""),
            deform_width=data.get("deform_width", 1.0),
            deform_thickness=data.get("deform_thickness", 1.0),
        )


@dataclass(frozen=True, slots=True)
class CreatureEdge:
    """A labeled graph edge swept into a voxel limb or torso segment."""

    id: str
    start: str
    end: str
    part: str
    bone: str = ""
    mirror: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "edge id", limit=64, required=True))
        object.__setattr__(self, "start", _text(self.start, "edge start", limit=64, required=True))
        object.__setattr__(self, "end", _text(self.end, "edge end", limit=64, required=True))
        object.__setattr__(self, "part", _text(self.part, "edge part", limit=96, required=True))
        object.__setattr__(self, "bone", _text(self.bone, "edge bone", limit=128))
        object.__setattr__(self, "mirror", bool(self.mirror))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "part": self.part,
            "bone": self.bone,
            "mirror": self.mirror,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreatureEdge":
        if not isinstance(data, Mapping):
            raise ValueError("creature edge must be a JSON object")
        return cls(
            id=data.get("id", ""),
            start=data.get("start", ""),
            end=data.get("end", ""),
            part=data.get("part", ""),
            bone=data.get("bone", ""),
            mirror=data.get("mirror", False),
        )


@dataclass(frozen=True, slots=True)
class CreatureJoint:
    """A named graph node that becomes a socket and animation binding hint."""

    node_id: str
    name: str
    role: str = "custom"
    tag: str = "creature"
    bone: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _text(self.node_id, "joint node id", limit=64, required=True))
        object.__setattr__(self, "name", _text(self.name, "joint name", limit=96, required=True))
        object.__setattr__(self, "role", _text(self.role, "joint role", limit=96) or "custom")
        object.__setattr__(self, "tag", _text(self.tag, "joint tag", limit=96, required=True))
        object.__setattr__(self, "bone", _text(self.bone, "joint bone", limit=128))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "role": self.role,
            "tag": self.tag,
            "bone": self.bone,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreatureJoint":
        if not isinstance(data, Mapping):
            raise ValueError("creature joint must be a JSON object")
        return cls(
            node_id=data.get("node_id", ""),
            name=data.get("name", ""),
            role=data.get("role", "custom"),
            tag=data.get("tag", "creature"),
            bone=data.get("bone", ""),
        )


@dataclass(slots=True)
class CreatureRecipe:
    """Validated, serializable graph recipe for a generated creature."""

    name: str = "VOXTART Creature"
    template: str = "CUSTOM"
    nodes: tuple[CreatureNode, ...] = ()
    edges: tuple[CreatureEdge, ...] = ()
    joints: tuple[CreatureJoint, ...] = ()
    grid_size: float = 1.0
    settings: dict[str, Any] = field(default_factory=dict)
    schema_version: int = CREATURE_FORMAT_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != CREATURE_FORMAT_VERSION
        ):
            raise ValueError(f"unsupported creature version: {self.schema_version}")
        template = str(self.template or "").upper()
        if template not in _CREATURE_TEMPLATE_VALUES:
            raise ValueError(f"unsupported creature template: {self.template!r}")
        self.template = template
        self.name = _text(self.name, "name", limit=128, required=True)
        self.grid_size = _finite_range(self.grid_size, "grid size", 0.01, 1000.0)
        self.nodes = tuple(self.nodes)
        self.edges = tuple(self.edges)
        self.joints = tuple(self.joints)
        if len(self.nodes) > MAX_CREATURE_NODES:
            raise ValueError(f"creatures may contain at most {MAX_CREATURE_NODES} nodes")
        if len(self.edges) > MAX_CREATURE_EDGES:
            raise ValueError(f"creatures may contain at most {MAX_CREATURE_EDGES} edges")
        if any(not isinstance(node, CreatureNode) for node in self.nodes):
            raise TypeError("creature nodes must contain CreatureNode values")
        if any(not isinstance(edge, CreatureEdge) for edge in self.edges):
            raise TypeError("creature edges must contain CreatureEdge values")
        if any(not isinstance(joint, CreatureJoint) for joint in self.joints):
            raise TypeError("creature joints must contain CreatureJoint values")
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("creature node ids must be unique")
        edge_ids = [edge.id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("creature edge ids must be unique")
        joint_names = [joint.name for joint in self.joints]
        if len(joint_names) != len(set(joint_names)):
            raise ValueError("creature joint names must be unique")
        node_id_set = set(node_ids)
        for edge in self.edges:
            if edge.start not in node_id_set or edge.end not in node_id_set:
                raise ValueError(f"creature edge {edge.id!r} references an unknown node")
            if edge.start == edge.end:
                raise ValueError(f"creature edge {edge.id!r} cannot connect a node to itself")
        for joint in self.joints:
            if joint.node_id not in node_id_set:
                raise ValueError(f"creature joint {joint.name!r} references an unknown node")
        self.settings = _settings(self.settings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": CREATURE_FORMAT,
            "format_version": CREATURE_FORMAT_VERSION,
            "name": self.name,
            "template": self.template,
            "grid_size": self.grid_size,
            "nodes": [node.to_dict() for node in sorted(self.nodes, key=lambda item: item.id)],
            "edges": [edge.to_dict() for edge in sorted(self.edges, key=lambda item: item.id)],
            "joints": [joint.to_dict() for joint in sorted(self.joints, key=lambda item: item.name)],
            "settings": dict(self.settings),
        }

    def encode(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreatureRecipe":
        if not isinstance(data, Mapping):
            raise ValueError("creature recipe must be a JSON object")
        if data.get("format") != CREATURE_FORMAT:
            raise ValueError(f"unsupported creature format: {data.get('format')!r}")
        format_version = data.get("format_version", -1)
        if (
            isinstance(format_version, bool)
            or not isinstance(format_version, int)
            or format_version != CREATURE_FORMAT_VERSION
        ):
            raise ValueError("unsupported creature version")
        raw_nodes = data.get("nodes", ())
        raw_edges = data.get("edges", ())
        raw_joints = data.get("joints", ())
        if not isinstance(raw_nodes, (list, tuple)):
            raise ValueError("creature nodes must be a JSON list")
        if not isinstance(raw_edges, (list, tuple)):
            raise ValueError("creature edges must be a JSON list")
        if not isinstance(raw_joints, (list, tuple)):
            raise ValueError("creature joints must be a JSON list")
        return cls(
            name=data.get("name", "VOXTART Creature"),
            template=data.get("template", "CUSTOM"),
            grid_size=data.get("grid_size", 1.0),
            nodes=tuple(CreatureNode.from_dict(item) for item in raw_nodes),
            edges=tuple(CreatureEdge.from_dict(item) for item in raw_edges),
            joints=tuple(CreatureJoint.from_dict(item) for item in raw_joints),
            settings=data.get("settings", {}),
            schema_version=CREATURE_FORMAT_VERSION,
        )

    @classmethod
    def decode(cls, encoded: str) -> "CreatureRecipe":
        try:
            payload = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("creature recipe must be valid JSON") from error
        return cls.from_dict(payload)


@dataclass(frozen=True, slots=True)
class CreatureJointRecord:
    """Object metadata record for a generated creature joint."""

    name: str
    role: str
    bone: str
    node_id: str
    socket_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "bone": self.bone,
            "node_id": self.node_id,
            "socket_index": self.socket_index,
        }


@dataclass(frozen=True, slots=True)
class CreatureBuildResult:
    """Generated asset plus stable joint metadata for Blender/gallery consumers."""

    asset: VoxelAsset
    joints: tuple[CreatureJointRecord, ...]


def _node(node_id: str, position: Point, radius: float, role: str) -> CreatureNode:
    return CreatureNode(id=node_id, position=position, radius=radius, role=role)


def _edge(edge_id: str, start: str, end: str, part: str, bone: str) -> CreatureEdge:
    return CreatureEdge(id=edge_id, start=start, end=end, part=part, bone=bone)


def _biped_recipe(name: str, grid_size: float) -> CreatureRecipe:
    nodes = (
        _node("root", (0.0, 0.0, 5.8), 1.20, "ROOT"),
        _node("pelvis", (0.0, 0.0, 5.3), 1.45, "PELVIS"),
        _node("spine", (0.0, 0.0, 7.1), 1.10, "SPINE"),
        _node("chest", (0.0, 0.0, 8.9), 1.40, "CHEST"),
        _node("neck", (0.0, 0.0, 10.6), 0.70, "NECK"),
        _node("head", (0.0, 0.0, 12.2), 1.25, "HEAD"),
        _node("shoulder_l", (-1.75, 0.0, 8.9), 0.80, "SHOULDER_L"),
        _node("elbow_l", (-2.95, 0.0, 7.5), 0.64, "ELBOW_L"),
        _node("hand_l", (-3.75, 0.0, 6.1), 0.62, "HAND_L"),
        _node("shoulder_r", (1.75, 0.0, 8.9), 0.80, "SHOULDER_R"),
        _node("elbow_r", (2.95, 0.0, 7.5), 0.64, "ELBOW_R"),
        _node("hand_r", (3.75, 0.0, 6.1), 0.62, "HAND_R"),
        _node("hip_l", (-0.78, 0.0, 5.2), 0.88, "HIP_L"),
        _node("knee_l", (-0.92, 0.0, 2.75), 0.78, "KNEE_L"),
        _node("foot_l", (-1.05, -0.55, 0.72), 0.76, "FOOT_L"),
        _node("hip_r", (0.78, 0.0, 5.2), 0.88, "HIP_R"),
        _node("knee_r", (0.92, 0.0, 2.75), 0.78, "KNEE_R"),
        _node("foot_r", (1.05, -0.55, 0.72), 0.76, "FOOT_R"),
        _node("tail_base", (0.0, 0.55, 5.4), 0.62, "TAIL_BASE"),
        _node("tail_tip", (0.0, 2.55, 5.2), 0.38, "TAIL_TIP"),
    )
    edges = (
        _edge("root_pelvis", "root", "pelvis", "ROOT", "root"),
        _edge("pelvis_spine", "pelvis", "spine", "TORSO", "pelvis"),
        _edge("spine_chest", "spine", "chest", "TORSO", "spine"),
        _edge("chest_neck", "chest", "neck", "TORSO", "chest"),
        _edge("neck_head", "neck", "head", "HEAD", "neck"),
        _edge("chest_shoulder_l", "chest", "shoulder_l", "ARM_L", "upper_arm.L"),
        _edge("shoulder_l_elbow_l", "shoulder_l", "elbow_l", "ARM_L", "forearm.L"),
        _edge("elbow_l_hand_l", "elbow_l", "hand_l", "ARM_L", "hand.L"),
        _edge("chest_shoulder_r", "chest", "shoulder_r", "ARM_R", "upper_arm.R"),
        _edge("shoulder_r_elbow_r", "shoulder_r", "elbow_r", "ARM_R", "forearm.R"),
        _edge("elbow_r_hand_r", "elbow_r", "hand_r", "ARM_R", "hand.R"),
        _edge("pelvis_hip_l", "pelvis", "hip_l", "LEG_L", "thigh.L"),
        _edge("hip_l_knee_l", "hip_l", "knee_l", "LEG_L", "shin.L"),
        _edge("knee_l_foot_l", "knee_l", "foot_l", "LEG_L", "foot.L"),
        _edge("pelvis_hip_r", "pelvis", "hip_r", "LEG_R", "thigh.R"),
        _edge("hip_r_knee_r", "hip_r", "knee_r", "LEG_R", "shin.R"),
        _edge("knee_r_foot_r", "knee_r", "foot_r", "LEG_R", "foot.R"),
        _edge("pelvis_tail_base", "pelvis", "tail_base", "TAIL", "tail.01"),
        _edge("tail_base_tail_tip", "tail_base", "tail_tip", "TAIL", "tail.02"),
    )
    joints = (
        CreatureJoint("root", "root", "ROOT", "creature_root", "root"),
        CreatureJoint("pelvis", "pelvis", "PELVIS", "creature_pelvis", "pelvis"),
        CreatureJoint("head", "head", "HEAD", "creature_head", "head"),
        CreatureJoint("hand_l", "hand_l", "HAND_L", "creature_hand", "hand.L"),
        CreatureJoint("hand_r", "hand_r", "HAND_R", "creature_hand", "hand.R"),
        CreatureJoint("foot_l", "foot_l", "FOOT_L", "creature_foot", "foot.L"),
        CreatureJoint("foot_r", "foot_r", "FOOT_R", "creature_foot", "foot.R"),
        CreatureJoint("tail_tip", "tail_tip", "TAIL_TIP", "creature_tail", "tail.02"),
    )
    return CreatureRecipe(
        name=name,
        template="BIPED",
        nodes=nodes,
        edges=edges,
        joints=joints,
        grid_size=grid_size,
        settings={"axis": "Z_UP", "symmetry": "X"},
    )


def _quadruped_recipe(name: str, grid_size: float) -> CreatureRecipe:
    nodes = (
        _node("root", (0.0, 0.0, 4.4), 1.20, "ROOT"),
        _node("pelvis", (0.0, -0.35, 4.3), 1.35, "PELVIS"),
        _node("spine", (0.0, 0.8, 4.9), 1.10, "SPINE"),
        _node("chest", (0.0, 1.8, 5.1), 1.35, "CHEST"),
        _node("neck", (0.0, 2.75, 5.35), 0.78, "NECK"),
        _node("head", (0.0, 3.75, 5.25), 1.18, "HEAD"),
        _node("shoulder_fl", (-1.15, 1.65, 4.8), 0.78, "SHOULDER_FL"),
        _node("elbow_fl", (-1.25, 1.72, 2.55), 0.70, "ELBOW_FL"),
        _node("paw_fl", (-1.35, 1.90, 0.70), 0.72, "PAW_FL"),
        _node("shoulder_fr", (1.15, 1.65, 4.8), 0.78, "SHOULDER_FR"),
        _node("elbow_fr", (1.25, 1.72, 2.55), 0.70, "ELBOW_FR"),
        _node("paw_fr", (1.35, 1.90, 0.70), 0.72, "PAW_FR"),
        _node("hip_bl", (-1.10, -0.55, 4.15), 0.86, "HIP_BL"),
        _node("knee_bl", (-1.22, -0.72, 2.35), 0.76, "KNEE_BL"),
        _node("paw_bl", (-1.38, -0.95, 0.70), 0.72, "PAW_BL"),
        _node("hip_br", (1.10, -0.55, 4.15), 0.86, "HIP_BR"),
        _node("knee_br", (1.22, -0.72, 2.35), 0.76, "KNEE_BR"),
        _node("paw_br", (1.38, -0.95, 0.70), 0.72, "PAW_BR"),
        _node("tail_base", (0.0, -1.35, 4.45), 0.70, "TAIL_BASE"),
        _node("tail_mid", (0.0, -2.45, 4.65), 0.55, "TAIL_MID"),
        _node("tail_tip", (0.0, -3.65, 4.95), 0.34, "TAIL_TIP"),
    )
    edges = (
        _edge("root_pelvis", "root", "pelvis", "ROOT", "root"),
        _edge("pelvis_spine", "pelvis", "spine", "TORSO", "pelvis"),
        _edge("spine_chest", "spine", "chest", "TORSO", "spine"),
        _edge("chest_neck", "chest", "neck", "TORSO", "chest"),
        _edge("neck_head", "neck", "head", "HEAD", "neck"),
        _edge("chest_shoulder_fl", "chest", "shoulder_fl", "LEG_FL", "upper_leg.FL"),
        _edge("shoulder_fl_elbow_fl", "shoulder_fl", "elbow_fl", "LEG_FL", "lower_leg.FL"),
        _edge("elbow_fl_paw_fl", "elbow_fl", "paw_fl", "LEG_FL", "paw.FL"),
        _edge("chest_shoulder_fr", "chest", "shoulder_fr", "LEG_FR", "upper_leg.FR"),
        _edge("shoulder_fr_elbow_fr", "shoulder_fr", "elbow_fr", "LEG_FR", "lower_leg.FR"),
        _edge("elbow_fr_paw_fr", "elbow_fr", "paw_fr", "LEG_FR", "paw.FR"),
        _edge("pelvis_hip_bl", "pelvis", "hip_bl", "LEG_BL", "upper_leg.BL"),
        _edge("hip_bl_knee_bl", "hip_bl", "knee_bl", "LEG_BL", "lower_leg.BL"),
        _edge("knee_bl_paw_bl", "knee_bl", "paw_bl", "LEG_BL", "paw.BL"),
        _edge("pelvis_hip_br", "pelvis", "hip_br", "LEG_BR", "upper_leg.BR"),
        _edge("hip_br_knee_br", "hip_br", "knee_br", "LEG_BR", "lower_leg.BR"),
        _edge("knee_br_paw_br", "knee_br", "paw_br", "LEG_BR", "paw.BR"),
        _edge("pelvis_tail_base", "pelvis", "tail_base", "TAIL", "tail.01"),
        _edge("tail_base_tail_mid", "tail_base", "tail_mid", "TAIL", "tail.02"),
        _edge("tail_mid_tail_tip", "tail_mid", "tail_tip", "TAIL", "tail.03"),
    )
    joints = (
        CreatureJoint("root", "root", "ROOT", "creature_root", "root"),
        CreatureJoint("pelvis", "pelvis", "PELVIS", "creature_pelvis", "pelvis"),
        CreatureJoint("head", "head", "HEAD", "creature_head", "head"),
        CreatureJoint("paw_fl", "paw_fl", "PAW_FL", "creature_paw", "paw.FL"),
        CreatureJoint("paw_fr", "paw_fr", "PAW_FR", "creature_paw", "paw.FR"),
        CreatureJoint("paw_bl", "paw_bl", "PAW_BL", "creature_paw", "paw.BL"),
        CreatureJoint("paw_br", "paw_br", "PAW_BR", "creature_paw", "paw.BR"),
        CreatureJoint("tail_tip", "tail_tip", "TAIL_TIP", "creature_tail", "tail.03"),
    )
    return CreatureRecipe(
        name=name,
        template="QUADRUPED",
        nodes=nodes,
        edges=edges,
        joints=joints,
        grid_size=grid_size,
        settings={"axis": "Z_UP", "symmetry": "X"},
    )


def template_recipe(
    template: str,
    *,
    name: str | None = None,
    grid_size: float = 1.0,
) -> CreatureRecipe:
    """Return a stable graph recipe for one of the built-in creature templates."""

    normalized = str(template or "").upper()
    default_name = f"VOXTART {normalized.title()}"
    recipe_name = str(name).strip() if name is not None and str(name).strip() else default_name
    if normalized == "BIPED":
        return _biped_recipe(recipe_name, grid_size)
    if normalized == "QUADRUPED":
        return _quadruped_recipe(recipe_name, grid_size)
    raise ValueError(f"unsupported creature template: {template!r}")


def _length(vector: Point) -> float:
    return math.sqrt(sum(axis * axis for axis in vector))


def _subtract(left: Point, right: Point) -> Point:
    return tuple(left[index] - right[index] for index in range(3))  # type: ignore[return-value]


def _add(left: Point, right: Point) -> Point:
    return tuple(left[index] + right[index] for index in range(3))  # type: ignore[return-value]


def _scale(vector: Point, scalar: float) -> Point:
    return tuple(axis * scalar for axis in vector)  # type: ignore[return-value]


def _dot(left: Point, right: Point) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _cross(left: Point, right: Point) -> Point:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _unit(vector: Point, fallback: Point = (0.0, 0.0, 1.0)) -> Point:
    length = _length(vector)
    if length <= 1.0e-9:
        return fallback
    return _scale(vector, 1.0 / length)


def _quantize_normal(vector: Point) -> Cell:
    if _length(vector) <= 1.0e-9:
        return (0, 0, 1)
    best = _AXIS_DIRECTIONS[0]
    best_score = -math.inf
    for candidate in _AXIS_DIRECTIONS:
        score = _dot(vector, tuple(float(axis) for axis in candidate))
        if score > best_score:
            best = candidate
            best_score = score
    return best


def _cell_position(point: Point) -> Cell:
    return tuple(int(round(axis)) for axis in point)  # type: ignore[return-value]


def _generated_joints(recipe: CreatureRecipe) -> tuple[CreatureJoint, ...]:
    if recipe.joints:
        return tuple(sorted(recipe.joints, key=lambda item: item.name))
    degree = {node.id: 0 for node in recipe.nodes}
    for edge in recipe.edges:
        degree[edge.start] += 1
        degree[edge.end] += 1
    candidates = [
        node
        for node in sorted(recipe.nodes, key=lambda item: item.id)
        if degree[node.id] <= 1 or node.role.upper() in {"ROOT", "PELVIS", "HEAD"}
    ]
    if not candidates and recipe.nodes:
        candidates = [sorted(recipe.nodes, key=lambda item: item.id)[0]]
    return tuple(
        CreatureJoint(
            node_id=node.id,
            name=node.id,
            role=_role_label(node),
            tag=f"creature_{_role_label(node).lower()}",
            bone=node.id,
        )
        for node in candidates
    )


def _outward_vector(node: CreatureNode, neighbors: Iterable[CreatureNode]) -> Point:
    vectors = [_subtract(node.position, neighbor.position) for neighbor in neighbors]
    if vectors:
        average = tuple(sum(vector[index] for vector in vectors) for index in range(3))
        if _length(average) > 1.0e-9:
            return average  # type: ignore[return-value]
    role = node.role.upper()
    if role.startswith("HAND_") or role.startswith("PAW_") or role.startswith("SHOULDER_"):
        return (-1.0, 0.0, 0.0) if node.position[0] < 0.0 else (1.0, 0.0, 0.0)
    if role.startswith("FOOT") or role.startswith("KNEE") or role.startswith("ELBOW"):
        return (0.0, 0.0, -1.0)
    if role.startswith("TAIL"):
        return (0.0, 1.0, 0.0) if node.position[1] >= 0.0 else (0.0, -1.0, 0.0)
    if role in {"HEAD", "NECK"}:
        return (0.0, 0.0, 1.0)
    return (0.0, 0.0, 1.0)


def _reference_axis(direction: Point) -> Point:
    candidates = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    return min(candidates, key=lambda candidate: abs(_dot(direction, candidate)))


def build_voxel_asset(
    recipe: CreatureRecipe,
    *,
    palette: Iterable[Color] | None = None,
    max_voxels: int = MAX_CREATURE_VOXELS,
) -> CreatureBuildResult:
    """Sweep a graph recipe into a labeled, socketed sparse voxel asset."""

    if not isinstance(recipe, CreatureRecipe):
        raise TypeError("creature build requires a CreatureRecipe")
    if isinstance(max_voxels, bool) or not isinstance(max_voxels, int) or max_voxels <= 0:
        raise ValueError("creature max_voxels must be a positive integer")
    palette_values = list(_DEFAULT_CREATURE_PALETTE if palette is None else palette)
    if not palette_values:
        raise ValueError("creature palettes must contain at least one color")

    nodes = {node.id: node for node in recipe.nodes}
    neighbors: dict[str, list[CreatureNode]] = {node.id: [] for node in recipe.nodes}
    for edge in recipe.edges:
        neighbors[edge.start].append(nodes[edge.end])
        neighbors[edge.end].append(nodes[edge.start])

    voxels: dict[Cell, int] = {}
    part_assignments: dict[Cell, str] = {}

    def add_cell(cell: Cell, label: str) -> None:
        if cell in voxels:
            return
        if len(voxels) >= max_voxels:
            raise ValueError(f"creature sweep exceeded max_voxels ({max_voxels})")
        voxels[cell] = 0
        part_assignments[cell] = label

    def sweep_point(point: Point, radius: float, width: float, thickness: float, label: str) -> None:
        radii = (max(0.5, radius * width), max(0.5, radius * thickness), max(0.5, radius))
        maximum = max(radii)
        minimum = tuple(math.floor(point[index] - maximum - 1.0) for index in range(3))
        maximum_cell = tuple(math.ceil(point[index] + maximum + 1.0) for index in range(3))
        for x in range(minimum[0], maximum_cell[0] + 1):
            for y in range(minimum[1], maximum_cell[1] + 1):
                for z in range(minimum[2], maximum_cell[2] + 1):
                    offset = (x - point[0], y - point[1], z - point[2])
                    if sum((offset[index] / radii[index]) ** 2 for index in range(3)) <= 1.0:
                        add_cell((x, y, z), label)

    def sweep_edge(edge: CreatureEdge) -> None:
        start = nodes[edge.start]
        end = nodes[edge.end]
        delta = _subtract(end.position, start.position)
        length = _length(delta)
        label = edge.part
        if length <= 1.0e-9:
            sweep_point(start.position, start.radius, start.deform_width, start.deform_thickness, label)
            return
        direction = _unit(delta)
        reference = _reference_axis(direction)
        width_axis = _unit(_cross(direction, reference), (0.0, 1.0, 0.0))
        thickness_axis = _unit(_cross(direction, width_axis), (0.0, 0.0, 1.0))
        step = max(0.25, min(start.radius, end.radius) * 0.5)
        sample_count = max(1, int(math.ceil(length / step)))
        for sample_index in range(sample_count + 1):
            amount = sample_index / sample_count
            point = _add(start.position, _scale(delta, amount))
            radius = start.radius + (end.radius - start.radius) * amount
            width = start.deform_width + (end.deform_width - start.deform_width) * amount
            thickness = start.deform_thickness + (
                end.deform_thickness - start.deform_thickness
            ) * amount
            radii = (max(0.5, radius * width), max(0.5, radius * thickness), max(0.5, radius))
            maximum = max(radii)
            minimum = tuple(math.floor(point[index] - maximum - 1.0) for index in range(3))
            maximum_cell = tuple(math.ceil(point[index] + maximum + 1.0) for index in range(3))
            for x in range(minimum[0], maximum_cell[0] + 1):
                for y in range(minimum[1], maximum_cell[1] + 1):
                    for z in range(minimum[2], maximum_cell[2] + 1):
                        offset = (x - point[0], y - point[1], z - point[2])
                        axial = _dot(offset, direction)
                        width_offset = _dot(offset, width_axis)
                        thickness_offset = _dot(offset, thickness_axis)
                        if (
                            (axial / max(radii[2], 0.5)) ** 2
                            + (width_offset / radii[0]) ** 2
                            + (thickness_offset / radii[1]) ** 2
                            <= 1.0
                        ):
                            add_cell((x, y, z), label)

    for edge in sorted(recipe.edges, key=lambda item: item.id):
        sweep_edge(edge)

    for node in sorted(recipe.nodes, key=lambda item: item.id):
        labels = sorted(
            edge.part
            for edge in recipe.edges
            if edge.start == node.id or edge.end == node.id
        )
        sweep_point(
            node.position,
            node.radius,
            node.deform_width,
            node.deform_thickness,
            labels[0] if labels else _role_label(node),
        )

    asset = VoxelAsset(
        grid_size=recipe.grid_size,
        palette=palette_values,
        voxels=voxels,
        part_assignments=part_assignments,
        provenance={
            "workflow": "voxtart_creature",
            "creature_format": CREATURE_FORMAT,
            "template": recipe.template,
            "recipe_name": recipe.name,
            "node_count": str(len(recipe.nodes)),
            "edge_count": str(len(recipe.edges)),
        },
    )

    joints = _generated_joints(recipe)
    sockets: list[VoxelSocket] = []
    records: list[CreatureJointRecord] = []
    for socket_index, joint in enumerate(joints):
        node = nodes[joint.node_id]
        normal = _quantize_normal(_outward_vector(node, neighbors[node.id]))
        sockets.append(
            VoxelSocket(
                position=_cell_position(node.position),
                normal=normal,
                tag=joint.tag,
            )
        )
        records.append(
            CreatureJointRecord(
                name=joint.name,
                role=joint.role,
                bone=joint.bone,
                node_id=joint.node_id,
                socket_index=socket_index,
            )
        )
    asset.sockets = sockets
    return CreatureBuildResult(asset=asset, joints=tuple(records))


__all__ = [
    "CREATURE_FORMAT",
    "CREATURE_FORMAT_VERSION",
    "CREATURE_TEMPLATES",
    "MAX_CREATURE_NODES",
    "MAX_CREATURE_EDGES",
    "MAX_CREATURE_VOXELS",
    "CreatureBuildResult",
    "CreatureEdge",
    "CreatureJoint",
    "CreatureJointRecord",
    "CreatureNode",
    "CreatureRecipe",
    "build_voxel_asset",
    "template_recipe",
]
