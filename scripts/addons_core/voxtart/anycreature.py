# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bundled anyCreature bridge used by the VOXTART chat authoring surface.

The release tree carries a dependency-free anyCreature engine and a matching
Node.js runtime so this module can build creatures without network access.
An explicit root or Node executable remains available for development
overrides. This module owns the narrow boundary around the engine: locate it,
validate a JSON spec, invoke the CLI without a shell, and import the resulting
GLB into Blender when the host is available.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_SPEC_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
MAX_CHECKS_BYTES = 1 * 1024 * 1024
MAX_PARTS = 256
MAX_VOLUMES = 256
MAX_CHAINS = 256
MAX_TIMEOUT_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 120.0
_ENGINE_PATH = Path("engine") / "cli.js"
_REQUIRED_DOCUMENTS = (
    Path("MANUAL.md"),
    Path("cards") / "SYNTAX.md",
)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")


class AnyCreatureError(RuntimeError):
    """Expected configuration, validation, or build failure."""


@dataclass(frozen=True, slots=True)
class BuildArtifact:
    """Persistent files and diagnostics produced by one anyCreature build."""

    root: Path
    spec_path: Path
    glb_path: Path
    checks_path: Path
    checks: Mapping[str, Any]
    stdout: str
    stderr: str


def _path_from(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None:
        return None
    text = os.fspath(value).strip()
    if not text:
        return None
    try:
        return Path(text).expanduser()
    except (TypeError, ValueError, OSError):
        return None


def _candidate_roots(configured_root: str | os.PathLike[str] | None) -> tuple[Path, ...]:
    configured = _path_from(configured_root)
    environment = _path_from(os.environ.get("VOXTART_ANYCREATURE_ROOT"))
    repository = Path(__file__).resolve().parents[3]
    candidates = [
        configured,
        environment,
        repository / "third_party" / "anyCreature",
        repository.parent / "anyCreature",
        Path.cwd() / "anyCreature",
        Path.home() / "anyCreature",
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            key = str(candidate.resolve()).casefold()
        except OSError:
            key = str(candidate.absolute()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def is_engine_root(root: str | os.PathLike[str] | None) -> bool:
    candidate = _path_from(root)
    if candidate is None:
        return False
    return (candidate / _ENGINE_PATH).is_file()


def resolve_root(configured_root: str | os.PathLike[str] | None = None) -> Path | None:
    """Resolve the first local checkout containing the anyCreature CLI."""

    for candidate in _candidate_roots(configured_root):
        if is_engine_root(candidate):
            try:
                return candidate.resolve()
            except OSError:
                return candidate.absolute()
    return None


def bundled_node_path() -> Path | None:
    """Return the packaged Node executable when this distribution includes it."""

    repository = Path(__file__).resolve().parents[3]
    executable = "node.exe" if os.name == "nt" else "node"
    candidate = repository / "third_party" / "node" / executable
    return candidate if candidate.is_file() else None


def _node_available(executable: str) -> bool:
    candidate = _path_from(executable)
    return bool((candidate and candidate.is_file()) or shutil.which(executable))


def _node_command(executable: str | None) -> str:
    requested = str(executable or "").strip()
    if not requested:
        requested = os.environ.get("VOXTART_NODE", "").strip()
    if not requested or requested.casefold() in {"node", "node.exe"}:
        bundled = bundled_node_path()
        if bundled is not None:
            return str(bundled)
    return requested or "node"


def _spec_bytes(spec: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            spec,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise AnyCreatureError(f"anyCreature spec must be finite JSON: {error}") from error
    if len(encoded) > MAX_SPEC_BYTES:
        raise AnyCreatureError(
            f"anyCreature spec is {len(encoded)} bytes; limit is {MAX_SPEC_BYTES} bytes"
        )
    return encoded


def validate_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate transport limits without duplicating the engine's full grammar."""

    if not isinstance(spec, Mapping) or isinstance(spec, (str, bytes, bytearray)):
        raise AnyCreatureError("anyCreature spec must be a JSON object")
    encoded = _spec_bytes(spec)
    normalized = json.loads(encoded.decode("utf-8"))
    if not isinstance(normalized, dict):
        raise AnyCreatureError("anyCreature spec must be a JSON object")

    height = normalized.get("height")
    if height is not None:
        if isinstance(height, bool) or not isinstance(height, (int, float)) or not math.isfinite(float(height)):
            raise AnyCreatureError("anyCreature height must be a finite number")
        if float(height) <= 0.0:
            raise AnyCreatureError("anyCreature height must be positive")

    mapping_fields = ("palette", "sections", "joints", "joints_R", "chains", "attach", "animations", "shading")
    for field in mapping_fields:
        if field in normalized and not isinstance(normalized[field], dict):
            raise AnyCreatureError(f"anyCreature {field} must be an object")

    list_fields = ("mirror", "touch")
    for field in list_fields:
        if field in normalized and not isinstance(normalized[field], list):
            raise AnyCreatureError(f"anyCreature {field} must be an array")

    for field, maximum in (("parts", MAX_PARTS), ("volumes", MAX_VOLUMES)):
        value = normalized.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise AnyCreatureError(f"anyCreature {field} must be an array")
        if len(value) > maximum:
            raise AnyCreatureError(f"anyCreature {field} contains more than {maximum} entries")
        for index, entry in enumerate(value):
            if not isinstance(entry, dict):
                raise AnyCreatureError(f"anyCreature {field}[{index}] must be an object")
            if field == "parts" and not str(entry.get("type", "")).strip():
                raise AnyCreatureError(f"anyCreature parts[{index}] needs a type")

    chains = normalized.get("chains")
    if chains is not None and len(chains) > MAX_CHAINS:
        raise AnyCreatureError(f"anyCreature chains contains more than {MAX_CHAINS} entries")
    return normalized


def safe_output_stem(value: Any, spec_bytes: bytes) -> str:
    """Return a filesystem-safe, spec-specific output stem."""

    text = str(value or "creature").strip()
    text = _SAFE_NAME.sub("-", text).strip("-_")[:48] or "creature"
    digest = hashlib.sha256(spec_bytes).hexdigest()[:10]
    return f"{text}-{digest}"


def integration_status(
    configured_root: str | os.PathLike[str] | None = None,
    node_executable: str | None = None,
) -> dict[str, Any]:
    """Return a UI/MCP-safe status without invoking Node."""

    repository = Path(__file__).resolve().parents[3]
    bundled_root = repository / "third_party" / "anyCreature"
    bundled_node = bundled_node_path()
    root = resolve_root(configured_root)
    node = _node_command(node_executable)
    node_available = _node_available(node)
    root_is_bundled = bool(root and root.resolve() == bundled_root.resolve())
    try:
        node_is_bundled = bool(bundled_node and Path(node).resolve() == bundled_node.resolve())
    except OSError:
        node_is_bundled = False
    offline_ready = bool(root_is_bundled and node_is_bundled and node_available)
    return {
        "available": root is not None and node_available,
        "root": str(root) if root else "",
        "engine": str(root / _ENGINE_PATH) if root else "",
        "node": node,
        "node_available": node_available,
        "bundled_node": str(bundled_node) if bundled_node else "",
        "bundled_node_available": bool(bundled_node and _node_available(str(bundled_node))),
        "offline_ready": offline_ready,
        "documents_present": bool(root and all((root / path).is_file() for path in _REQUIRED_DOCUMENTS)),
        "message": (
            "Offline ready"
            if offline_ready and node_available
            else "Ready"
            if root is not None and node_available
            else "Configure the bundled or development anyCreature pipeline"
        ),
    }


def _tail(text: str, limit: int = 8000) -> str:
    text = str(text or "").strip()
    return text[-limit:] if text else ""


def compile_spec(
    spec: Mapping[str, Any],
    *,
    configured_root: str | os.PathLike[str] | None = None,
    node_executable: str | None = None,
    output_dir: str | os.PathLike[str] | None = None,
    output_name: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> BuildArtifact:
    """Compile one spec with the local CLI and retain its provenance files."""

    normalized = validate_spec(spec)
    spec_bytes = _spec_bytes(normalized)
    destination = _path_from(output_dir) or (Path.cwd() / "voxtart_anycreatures")
    try:
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise AnyCreatureError(f"cannot create anyCreature output folder: {error}") from error
    stem = safe_output_stem(output_name or normalized.get("name"), spec_bytes)
    spec_path = destination / f"{stem}.json"
    glb_path = destination / f"{stem}.glb"
    checks_path = destination / f"{stem}.checks.json"
    try:
        for stale_path in (glb_path, checks_path):
            try:
                stale_path.unlink()
            except FileNotFoundError:
                pass
    except OSError as error:
        raise AnyCreatureError(f"cannot prepare anyCreature output files: {error}") from error

    root = resolve_root(configured_root)
    if root is None:
        raise AnyCreatureError(
            "anyCreature engine is unavailable. Leave the root blank for the bundled engine "
            "or set a development checkout containing engine/cli.js."
        )
    node = _node_command(node_executable)
    if not _node_available(node):
        raise AnyCreatureError(
            f"Node.js executable is unavailable: {node!r}. Leave the Node executable blank "
            "for the bundled runtime or set an absolute Node.js path."
        )
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError, OverflowError) as error:
        raise AnyCreatureError("anyCreature build timeout must be a finite positive number") from error
    if not math.isfinite(timeout_value) or timeout_value <= 0.0:
        raise AnyCreatureError("anyCreature build timeout must be a finite positive number")
    timeout_value = min(timeout_value, MAX_TIMEOUT_SECONDS)
    try:
        spec_path.write_bytes(spec_bytes)
    except OSError as error:
        raise AnyCreatureError(f"cannot prepare anyCreature output files: {error}") from error

    command = [node, str(_ENGINE_PATH), str(spec_path), str(glb_path)]
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_value,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise AnyCreatureError(
            f"anyCreature did not finish within {timeout_value:.0f} seconds"
        ) from error
    except OSError as error:
        raise AnyCreatureError(f"could not start anyCreature: {error}") from error

    stdout = _tail(completed.stdout)
    stderr = _tail(completed.stderr)
    if completed.returncode != 0:
        detail = stderr or stdout or f"Node exited with status {completed.returncode}"
        raise AnyCreatureError(f"anyCreature refused the spec:\n{detail}")
    if not glb_path.is_file():
        raise AnyCreatureError("anyCreature reported success but did not write a GLB")
    try:
        output_size = glb_path.stat().st_size
    except OSError as error:
        raise AnyCreatureError(f"cannot inspect anyCreature GLB: {error}") from error
    if output_size <= 0 or output_size > MAX_OUTPUT_BYTES:
        raise AnyCreatureError(
            f"anyCreature GLB size {output_size} is outside the 1..{MAX_OUTPUT_BYTES} byte limit"
        )
    if not checks_path.is_file():
        raise AnyCreatureError("anyCreature did not write its checks stamp")
    try:
        with checks_path.open("rb") as handle:
            checks_bytes = handle.read(MAX_CHECKS_BYTES + 1)
    except OSError as error:
        raise AnyCreatureError(f"cannot read anyCreature checks stamp: {error}") from error
    if len(checks_bytes) > MAX_CHECKS_BYTES:
        raise AnyCreatureError(
            f"anyCreature checks stamp exceeds the {MAX_CHECKS_BYTES} byte limit"
        )
    try:
        checks = json.loads(checks_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AnyCreatureError(f"cannot read anyCreature checks stamp: {error}") from error
    if not isinstance(checks, Mapping) or checks.get("passed") is not True:
        raise AnyCreatureError("anyCreature checks stamp is not fully passing")
    checks = dict(checks)
    try:
        json.dumps(checks, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise AnyCreatureError("anyCreature checks stamp must be finite JSON") from error
    return BuildArtifact(
        root=root,
        spec_path=spec_path,
        glb_path=glb_path,
        checks_path=checks_path,
        checks=checks,
        stdout=stdout,
        stderr=stderr,
    )


def import_artifact(context: Any, artifact: BuildArtifact, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Import a compiled GLB and attach inspectable VOXTART provenance."""

    normalized = validate_spec(spec)
    try:
        encoded_spec = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        encoded_checks = json.dumps(
            artifact.checks,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise AnyCreatureError("anyCreature provenance must be finite JSON") from error
    try:
        import bpy
    except ImportError as error:  # pragma: no cover - exercised only outside Blender
        raise AnyCreatureError("Blender is required to import an anyCreature GLB") from error

    before = {obj.as_pointer() for obj in bpy.data.objects}

    def remove_imported() -> None:
        for candidate in tuple(bpy.data.objects):
            try:
                if candidate.as_pointer() not in before:
                    bpy.data.objects.remove(candidate, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass

    try:
        result = bpy.ops.import_scene.gltf(filepath=str(artifact.glb_path))
    except (RuntimeError, TypeError, ValueError) as error:
        remove_imported()
        raise AnyCreatureError(f"Blender could not import anyCreature GLB: {error}") from error
    if "FINISHED" not in result:
        remove_imported()
        raise AnyCreatureError(f"Blender cancelled anyCreature GLB import: {result}")
    imported = [obj for obj in bpy.data.objects if obj.as_pointer() not in before]
    if not imported:
        raise AnyCreatureError("anyCreature GLB import created no Blender objects")

    metadata = {
        "voxtart_pipeline": "anyCreature",
        "voxtart_anycreature": True,
        "voxtart_anycreature_spec": encoded_spec,
        "voxtart_anycreature_checks": encoded_checks,
        "voxtart_anycreature_spec_path": str(artifact.spec_path),
        "voxtart_anycreature_glb_path": str(artifact.glb_path),
        "voxtart_anycreature_root": str(artifact.root),
    }
    try:
        for obj in imported:
            for key, value in metadata.items():
                obj[key] = value
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as error:
        remove_imported()
        raise AnyCreatureError(f"could not attach anyCreature provenance: {error}") from error

    armatures = [obj for obj in imported if obj.type == "ARMATURE"]
    meshes = [obj for obj in imported if obj.type == "MESH"]
    active = armatures[0] if armatures else meshes[0] if meshes else imported[0]
    try:
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for obj in imported:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active
    except (AttributeError, RuntimeError):
        pass
    return {
        "pipeline": "anyCreature",
        "object_names": [obj.name for obj in imported],
        "object_count": len(imported),
        "mesh_names": [obj.name for obj in meshes],
        "armature_names": [obj.name for obj in armatures],
        "active_object": active.name,
        "glb_path": str(artifact.glb_path),
        "spec_path": str(artifact.spec_path),
        "checks_path": str(artifact.checks_path),
        "checks": dict(artifact.checks),
    }


def build_and_import(
    context: Any,
    spec: Mapping[str, Any],
    *,
    configured_root: str | os.PathLike[str] | None = None,
    node_executable: str | None = None,
    output_dir: str | os.PathLike[str] | None = None,
    output_name: str | None = None,
) -> dict[str, Any]:
    """Compile and import one chat-authored creature."""

    normalized = validate_spec(spec)
    artifact = compile_spec(
        normalized,
        configured_root=configured_root,
        node_executable=node_executable,
        output_dir=output_dir,
        output_name=output_name,
    )
    return import_artifact(context, artifact, normalized)
