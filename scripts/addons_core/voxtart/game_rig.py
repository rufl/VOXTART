# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure planning helpers for extracting export-friendly game rigs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


RIG_EXTRACTION_MODES = ("DEFORM_ONLY", "DEFORM_AND_SELECTED", "ALL_BONES")
HIERARCHY_MODES = ("PRESERVE", "FLATTEN_MISSING")


def _read(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _names(value: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    for item in value:
        name = str(item or "").strip()
        if name and name not in result:
            result.append(name)
    return tuple(result)


def parse_name_list(value: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        value = value.replace(";", ",").replace("\n", ",").split(",")
    return _names(value)


@dataclass(frozen=True, slots=True)
class RigExtractionPlan:
    """Deterministic bone and parent selection for a game rig."""

    names: tuple[str, ...]
    parents: tuple[tuple[str, str | None], ...]
    mode: str
    hierarchy: str

    @property
    def parent_by_name(self) -> dict[str, str | None]:
        return dict(self.parents)


def make_extraction_plan(
    bones: Iterable[Any],
    *,
    mode: str = "DEFORM_ONLY",
    hierarchy: str = "PRESERVE",
    keep_bones: Iterable[str] = (),
    include_root: bool = True,
    root_name: str = "",
) -> RigExtractionPlan:
    """Choose a game-rig skeleton without depending on Blender APIs."""

    mode = str(mode or "DEFORM_ONLY").upper()
    hierarchy = str(hierarchy or "PRESERVE").upper()
    if mode not in RIG_EXTRACTION_MODES:
        raise ValueError(f"unsupported rig extraction mode: {mode}")
    if hierarchy not in HIERARCHY_MODES:
        raise ValueError(f"unsupported rig hierarchy mode: {hierarchy}")

    records = []
    by_name: dict[str, dict[str, Any]] = {}
    for item in bones:
        name = str(_read(item, "name", "")).strip()
        if not name:
            continue
        if name in by_name:
            raise ValueError(f"duplicate source bone name: {name}")
        parent = _read(item, "parent", None)
        parent_name = str(_read(parent, "name", parent or "")).strip() or None
        record = {
            "name": name,
            "parent": parent_name,
            "use_deform": bool(_read(item, "use_deform", False)),
            "selected": bool(_read(item, "selected", _read(item, "select", False))),
        }
        records.append(record)
        by_name[name] = record
    if not records:
        raise ValueError("source armature has no named bones")

    explicit = set(parse_name_list(keep_bones))
    unknown = sorted(explicit.difference(by_name), key=str.casefold)
    if unknown:
        raise ValueError(f"requested game-rig bones do not exist: {', '.join(unknown)}")
    requested_root = str(root_name or "").strip()
    if requested_root and requested_root not in by_name:
        raise ValueError(f"requested root bone does not exist: {requested_root}")

    if mode == "ALL_BONES":
        included = {record["name"] for record in records}
    elif mode == "DEFORM_AND_SELECTED":
        included = {
            record["name"]
            for record in records
            if record["use_deform"] or record["selected"]
        }
    else:
        included = {record["name"] for record in records if record["use_deform"]}
    included.update(explicit)
    if not included:
        raise ValueError("the extraction settings selected no bones")

    def top_root(name: str) -> str:
        seen: set[str] = set()
        current = name
        while by_name[current]["parent"] in by_name:
            if current in seen:
                raise ValueError("source armature contains a parent cycle")
            seen.add(current)
            current = by_name[current]["parent"]
        return current

    if include_root:
        included.update(top_root(name) for name in tuple(included))
    if requested_root:
        included.add(requested_root)

    names = tuple(record["name"] for record in records if record["name"] in included)
    parent_pairs: list[tuple[str, str | None]] = []
    for name in names:
        parent = by_name[name]["parent"]
        if hierarchy == "FLATTEN_MISSING":
            while parent is not None and parent not in included:
                parent = by_name[parent]["parent"] if parent in by_name else None
        elif parent not in included:
            parent = None
        parent_pairs.append((name, parent))
    return RigExtractionPlan(names, tuple(parent_pairs), mode, hierarchy)


def choose_action_names(
    available: Iterable[str],
    *,
    requested: str = "",
    all_actions: bool = False,
    active: str = "",
) -> tuple[str, ...]:
    """Resolve a bakery selection from names typed by the user."""

    available_names = tuple(sorted(_names(available), key=str.casefold))
    if not available_names:
        raise ValueError("no Blender actions are available")
    if all_actions:
        return available_names
    requested_names = parse_name_list(requested)
    if not requested_names:
        active = str(active or "").strip()
        if active:
            requested_names = (active,)
        else:
            requested_names = (available_names[0],)
    missing = [name for name in requested_names if name not in available_names]
    if missing:
        raise ValueError(f"requested actions do not exist: {', '.join(missing)}")
    return requested_names


__all__ = [
    "HIERARCHY_MODES",
    "RIG_EXTRACTION_MODES",
    "RigExtractionPlan",
    "choose_action_names",
    "make_extraction_plan",
    "parse_name_list",
]
