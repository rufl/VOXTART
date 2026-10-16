# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Portable blob assets, user galleries, and snappable joint metadata.

The gallery deliberately stores the authoritative sparse voxel asset together
with human-facing metadata in one JSON document.  A file can be copied between
users without Blender or executable content; a Blender adapter can instantiate
it as a normal VOXTART asset and preserve the same joint contract.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .voxel import VoxelAsset, VoxelSocket


BLOB_FORMAT = "voxtart.blob"
BLOB_FORMAT_VERSION = 2
_SUPPORTED_BLOB_FORMAT_VERSIONS = (1, BLOB_FORMAT_VERSION)
BLOB_FILE_EXTENSION = ".voxtart.blob.json"
FAVORITES_FILENAME = ".favorites.json"
BLOB_JOINTS_PROPERTY = "voxtart_blob_joints"
BLOB_ENTRY_PROPERTY = "voxtart_blob_entry_id"
MAX_BLOB_BYTES = 8 * 1024 * 1024
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def new_blob_id() -> str:
    """Return a collision-resistant, path-safe blob identifier."""

    return uuid.uuid4().hex


def _clean_text(value: Any, field: str, *, limit: int = 512, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"blob {field} must not be empty")
    if len(text) > limit:
        raise ValueError(f"blob {field} is too long")
    return text


def _clean_category(value: Any) -> str:
    return _clean_text(value, "category", limit=96) or "Uncategorized"


def _clean_tags(values: Iterable[Any]) -> tuple[str, ...]:
    if isinstance(values, str):
        values = values.split(",")
    result: set[str] = set()
    for value in values:
        tag = _clean_text(value, "tag", limit=64).lower()
        if tag:
            result.add(tag)
    return tuple(sorted(result))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class BlobJoint:
    """A named, optionally role-mapped socket on a blob."""

    name: str
    socket: VoxelSocket
    role: str = "custom"
    bone: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean_text(self.name, "joint name", limit=96, required=True))
        object.__setattr__(self, "role", _clean_text(self.role, "joint role", limit=96) or "custom")
        object.__setattr__(self, "bone", _clean_text(self.bone, "joint bone", limit=128))
        if not isinstance(self.socket, VoxelSocket):
            raise TypeError("blob joint socket must be a VoxelSocket")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "bone": self.bone,
            **self.socket.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BlobJoint":
        if not isinstance(data, Mapping):
            raise ValueError("blob joint must be a JSON object")
        return cls(
            name=data.get("name", "socket"),
            role=data.get("role", "custom"),
            bone=data.get("bone", ""),
            socket=VoxelSocket(
                position=data["position"],
                normal=data["normal"],
                tag=str(data.get("tag", "default")),
                rotation=data.get("rotation", 0),
            ),
        )


def joints_from_asset(asset: VoxelAsset, metadata: Any = None) -> tuple[BlobJoint, ...]:
    """Build named joints from an asset and optional object custom-property JSON.

    Object metadata contains ``socket_index`` records so older assets that only
    have anonymous ``VoxelSocket`` values remain fully usable.
    """

    records: list[Mapping[str, Any]] = []
    if isinstance(metadata, str) and metadata.strip():
        try:
            decoded = json.loads(metadata)
        except json.JSONDecodeError:
            decoded = []
        if isinstance(decoded, list):
            records = [item for item in decoded if isinstance(item, Mapping)]
    elif isinstance(metadata, list):
        records = [item for item in metadata if isinstance(item, Mapping)]

    result: list[BlobJoint] = []
    used: set[int] = set()
    for fallback_index, record in enumerate(records):
        try:
            index = int(record.get("socket_index", fallback_index))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(asset.sockets) or index in used:
            continue
        socket = asset.sockets[index]
        result.append(BlobJoint(
            name=record.get("name", f"socket_{index + 1:02d}"),
            role=record.get("role", socket.tag),
            bone=record.get("bone", ""),
            socket=socket,
        ))
        used.add(index)

    for index, socket in enumerate(asset.sockets):
        if index in used:
            continue
        result.append(BlobJoint(
            name=f"socket_{index + 1:02d}",
            role=socket.tag,
            socket=socket,
        ))
    return tuple(result)


@dataclass(slots=True)
class BlobGalleryEntry:
    """One shareable blob and its metadata."""

    blob_id: str
    name: str
    asset: VoxelAsset
    joints: tuple[BlobJoint, ...] = ()
    description: str = ""
    author: str = ""
    license: str = ""
    tags: tuple[str, ...] = ()
    source_url: str = ""
    created_at: str = ""
    provenance: dict[str, str] | None = None
    schema_version: int = BLOB_FORMAT_VERSION
    category: str = "Uncategorized"

    def __post_init__(self) -> None:
        if not _ID_PATTERN.fullmatch(str(self.blob_id)):
            raise ValueError("blob id must contain 1-64 path-safe characters")
        self.blob_id = str(self.blob_id)
        self.name = _clean_text(self.name, "name", limit=128, required=True)
        self.category = _clean_category(self.category)
        self.description = _clean_text(self.description, "description", limit=2000)
        self.author = _clean_text(self.author, "author", limit=128)
        self.license = _clean_text(self.license, "license", limit=256)
        self.source_url = _clean_text(self.source_url, "source URL", limit=1024)
        self.tags = _clean_tags(self.tags)
        if self.schema_version not in _SUPPORTED_BLOB_FORMAT_VERSIONS:
            raise ValueError(f"unsupported blob schema version: {self.schema_version}")
        if not isinstance(self.asset, VoxelAsset):
            raise TypeError("blob asset must be a VoxelAsset")
        joints = tuple(self.joints) if self.joints else joints_from_asset(self.asset)
        if any(not isinstance(joint, BlobJoint) for joint in joints):
            raise TypeError("blob joints must contain BlobJoint values")
        self.joints = joints
        # Joints are the named view of the authoritative socket list.  Keeping
        # the two in lockstep prevents a gallery file from advertising sockets
        # that an instantiated object cannot actually snap to.
        self.asset.sockets = [joint.socket for joint in joints]
        self.created_at = _clean_text(self.created_at, "created_at", limit=64) or _now_iso()
        self.provenance = {
            str(key): str(value)
            for key, value in (self.provenance or {}).items()
        }

    def to_dict(self) -> dict[str, Any]:
        asset = self.asset.to_dict()
        asset["sockets"] = [joint.socket.to_dict() for joint in self.joints]
        return {
            "format": BLOB_FORMAT,
            "format_version": BLOB_FORMAT_VERSION,
            "blob_id": self.blob_id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "author": self.author,
            "license": self.license,
            "tags": list(self.tags),
            "source_url": self.source_url,
            "created_at": self.created_at,
            "provenance": dict(sorted(self.provenance.items())),
            "joints": [joint.to_dict() for joint in self.joints],
            "asset": asset,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BlobGalleryEntry":
        if not isinstance(data, Mapping):
            raise ValueError("blob document must contain a JSON object")
        if data.get("format") != BLOB_FORMAT:
            raise ValueError(f"unsupported blob format: {data.get('format')!r}")
        format_version = data.get("format_version", -1)
        if isinstance(format_version, bool) or not isinstance(format_version, int) or format_version not in _SUPPORTED_BLOB_FORMAT_VERSIONS:
            raise ValueError("unsupported blob format version")
        asset_data = data.get("asset")
        if not isinstance(asset_data, Mapping):
            raise ValueError("blob document is missing its asset")
        asset = VoxelAsset.from_dict(asset_data)
        raw_joints = data.get("joints", ())
        if raw_joints is None:
            raw_joints = ()
        if not isinstance(raw_joints, (list, tuple)):
            raise ValueError("blob joints must be a JSON list")
        joints = tuple(BlobJoint.from_dict(item) for item in raw_joints)
        if not joints:
            joints = joints_from_asset(asset)
        return cls(
            blob_id=str(data.get("blob_id", "")),
            name=data.get("name", "Blob"),
            asset=asset,
            category=data.get("category", "Uncategorized"),
            joints=joints,
            description=data.get("description", ""),
            author=data.get("author", ""),
            license=data.get("license", ""),
            tags=data.get("tags", ()),
            source_url=data.get("source_url", ""),
            created_at=data.get("created_at", ""),
            provenance=data.get("provenance", {}),
            schema_version=BLOB_FORMAT_VERSION,
        )


def make_blob_entry(
    asset: VoxelAsset,
    name: str,
    *,
    blob_id: str | None = None,
    category: str = "Uncategorized",
    joints: Iterable[BlobJoint] | None = None,
    description: str = "",
    author: str = "",
    license: str = "",
    tags: Iterable[str] = (),
    source_url: str = "",
    provenance: Mapping[str, Any] | None = None,
) -> BlobGalleryEntry:
    """Create a gallery entry from a live VOXTART asset."""

    return BlobGalleryEntry(
        blob_id=blob_id or new_blob_id(),
        name=name,
        asset=asset,
        category=category,
        joints=tuple(joints or joints_from_asset(asset)),
        description=description,
        author=author,
        license=license,
        tags=tags,
        source_url=source_url,
        provenance={str(key): str(value) for key, value in (provenance or {}).items()},
    )


def encode_blob(entry: BlobGalleryEntry) -> str:
    """Return deterministic, human-readable JSON for a blob."""

    return json.dumps(entry.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def decode_blob(document: str, *, max_bytes: int = MAX_BLOB_BYTES) -> BlobGalleryEntry:
    encoded = str(document).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("blob document exceeds the gallery size limit")
    try:
        payload = json.loads(document)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid blob JSON: {error.msg}") from error
    return BlobGalleryEntry.from_dict(payload)


def save_blob(entry: BlobGalleryEntry, filepath: str | Path) -> Path:
    """Atomically save a portable blob document."""

    target = Path(filepath).expanduser()
    if not target.name or target.suffix == "":
        raise ValueError("blob filepath must name a file")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(encode_blob(entry), encoding="utf-8", newline="\n")
        temporary.replace(target)
    except OSError:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def load_blob(filepath: str | Path, *, max_bytes: int = MAX_BLOB_BYTES) -> BlobGalleryEntry:
    target = Path(filepath).expanduser()
    size = target.stat().st_size
    if size > max_bytes:
        raise ValueError("blob document exceeds the gallery size limit")
    return decode_blob(target.read_text(encoding="utf-8"), max_bytes=max_bytes)


@dataclass(slots=True)
class BlobGallery:
    """Filesystem-backed gallery with bounded, validated discovery."""

    root: Path
    max_file_bytes: int = MAX_BLOB_BYTES

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        if self.max_file_bytes <= 0:
            raise ValueError("gallery size limit must be positive")

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def path_for(self, blob_id: str) -> Path:
        blob_id = str(blob_id)
        if not _ID_PATTERN.fullmatch(blob_id):
            raise ValueError("invalid blob id")
        return self.root / f"{blob_id}{BLOB_FILE_EXTENSION}"

    def _favorites_path(self) -> Path:
        return self.root / FAVORITES_FILENAME

    def favorites(self) -> frozenset[str]:
        """Return gallery-local favorite ids; corrupt preference files are ignored."""

        path = self._favorites_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return frozenset()
        if not isinstance(payload, (list, tuple, set)):
            return frozenset()
        return frozenset(
            item for item in payload
            if isinstance(item, str) and _ID_PATTERN.fullmatch(item)
        )

    def set_favorite(self, blob_id: str, favorite: bool = True) -> None:
        """Persist a favorite flag separately from the portable asset document."""

        blob_id = str(blob_id)
        if not _ID_PATTERN.fullmatch(blob_id):
            raise ValueError("invalid blob id")
        favorites = set(self.favorites())
        if favorite:
            favorites.add(blob_id)
        else:
            favorites.discard(blob_id)
        self.ensure()
        target = self._favorites_path()
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(sorted(favorites), separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            temporary.replace(target)
        except OSError:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def is_favorite(self, blob_id: str) -> bool:
        return str(blob_id) in self.favorites()

    def save(self, entry: BlobGalleryEntry) -> Path:
        self.ensure()
        return save_blob(entry, self.path_for(entry.blob_id))

    def load(self, blob_id: str) -> BlobGalleryEntry:
        return load_blob(self.path_for(blob_id), max_bytes=self.max_file_bytes)

    def remove(self, blob_id: str) -> None:
        path = self.path_for(blob_id)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        if self._favorites_path().is_file():
            self.set_favorite(blob_id, False)

    def scan(
        self,
        query: str = "",
        category: str = "",
        favorites_only: bool = False,
    ) -> tuple[tuple[BlobGalleryEntry, ...], tuple[str, ...]]:
        if not self.root.is_dir():
            return (), ()
        query = str(query or "").strip().lower()
        category = str(category or "").strip().casefold()
        favorites = self.favorites() if favorites_only else frozenset()
        entries: list[BlobGalleryEntry] = []
        errors: list[str] = []
        for path in sorted(self.root.glob(f"*{BLOB_FILE_EXTENSION}"), key=lambda item: item.name.lower()):
            try:
                entry = load_blob(path, max_bytes=self.max_file_bytes)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"{path.name}: {error}")
                continue
            if category and entry.category.casefold() != category:
                continue
            if favorites_only and entry.blob_id not in favorites:
                continue
            haystack = " ".join((
                entry.name,
                entry.category,
                entry.author,
                entry.description,
                *entry.tags,
            )).lower()
            if query and query not in haystack:
                continue
            entries.append(entry)
        entries.sort(key=lambda item: (item.category.casefold(), item.name.casefold(), item.blob_id))
        return tuple(entries), tuple(errors)

    def entries(
        self,
        query: str = "",
        category: str = "",
        favorites_only: bool = False,
    ) -> tuple[BlobGalleryEntry, ...]:
        return self.scan(query, category, favorites_only)[0]

    def categories(self) -> tuple[str, ...]:
        entries, _errors = self.scan()
        return tuple(sorted({entry.category for entry in entries}, key=str.casefold))


__all__ = [
    "BLOB_FORMAT",
    "BLOB_FORMAT_VERSION",
    "BLOB_FILE_EXTENSION",
    "FAVORITES_FILENAME",
    "BLOB_JOINTS_PROPERTY",
    "BLOB_ENTRY_PROPERTY",
    "MAX_BLOB_BYTES",
    "BlobGallery",
    "BlobGalleryEntry",
    "BlobJoint",
    "decode_blob",
    "encode_blob",
    "joints_from_asset",
    "load_blob",
    "make_blob_entry",
    "new_blob_id",
    "save_blob",
]
