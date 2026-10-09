# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Stable, human-readable serialization for authoritative VOXTART assets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .voxel import VoxelAsset


FORMAT_NAME = "voxtart.voxel"
FORMAT_VERSION = 1


def encode_asset(asset: VoxelAsset) -> str:
    """Return a deterministic UTF-8 JSON document for an asset."""

    payload: dict[str, Any] = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        **asset.to_dict(),
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def decode_asset(document: str) -> VoxelAsset:
    """Decode and validate a VOXTART asset document."""

    payload = json.loads(document)
    if not isinstance(payload, dict):
        raise ValueError("VOXTART asset document must contain a JSON object")
    if payload.get("format") != FORMAT_NAME:
        raise ValueError(f"unsupported VOXTART asset format: {payload.get('format')!r}")
    version = payload.get("format_version")
    if isinstance(version, bool) or int(version) != FORMAT_VERSION:
        raise ValueError(f"unsupported VOXTART asset format version: {version!r}")
    return VoxelAsset.from_dict(payload)


def save_asset(asset: VoxelAsset, filepath: str | Path) -> Path:
    """Atomically save an asset and return its resolved target path."""

    target = Path(filepath).expanduser()
    if not target.name:
        raise ValueError("asset filepath must name a file")
    temporary = target.with_name(f".{target.name}.tmp")
    document = encode_asset(asset)
    try:
        temporary.write_text(document, encoding="utf-8", newline="\n")
        temporary.replace(target)
    except OSError:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def load_asset(filepath: str | Path) -> VoxelAsset:
    """Load and validate an asset from a UTF-8 file."""

    return decode_asset(Path(filepath).expanduser().read_text(encoding="utf-8"))
