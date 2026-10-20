# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dependency-free MCP JSON-RPC routing for VOXTART.

This module deliberately contains no Blender imports.  It keeps protocol
negotiation, schemas, and response formatting testable outside Blender while
the Blender adapter supplies the actual scene operations.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any


MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = (MCP_PROTOCOL_VERSION, "2025-03-26")
MAX_TOOL_COORDINATE = 4096
SERVER_INFO = {
    "name": "voxtart",
    "title": "VOXTART Voxel Authoring and Animation",
    "version": "0.2.0",
}


class MCPError(Exception):
    """A JSON-RPC error that can be returned to an MCP client."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


class MCPToolError(Exception):
    """An expected tool failure returned as an MCP tool result."""


_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
_CELL_COORDINATE_SCHEMA = {
    "type": "integer",
    "minimum": -MAX_TOOL_COORDINATE,
    "maximum": MAX_TOOL_COORDINATE,
}
_CELL_SCHEMA = {
    "type": "array",
    "prefixItems": [_CELL_COORDINATE_SCHEMA, _CELL_COORDINATE_SCHEMA, _CELL_COORDINATE_SCHEMA],
    "minItems": 3,
    "maxItems": 3,
}
_COLOR_SCHEMA = {
    "type": "array",
    "items": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "minItems": 4,
    "maxItems": 4,
}


TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "voxtart_scene_state",
        "title": "Inspect VOXTART scene",
        "description": "List VOXTART voxel assets and the active asset without changing the scene.",
        "inputSchema": _EMPTY_SCHEMA,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "voxtart_asset_state",
        "title": "Inspect a voxel asset",
        "description": "Return metadata for a VOXTART asset; optionally include its sparse voxel cells.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "object_name": {"type": "string", "description": "Blender object name; defaults to the active object."},
                "include_voxels": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "voxtart_anycreature_status",
        "title": "Inspect the anyCreature pipeline",
        "description": "Report whether the bundled offline anyCreature Node pipeline is ready; development overrides are supported.",
        "inputSchema": _EMPTY_SCHEMA,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "voxtart_generate_anycreature",
        "title": "Generate a skinned creature",
        "description": (
            "Compile and import a game-ready anyCreature GLB from a complete JSON spec using "
            "the bundled offline engine. Use this for creature or character requests when the "
            "pipeline is ready. The spec should author its own height, palette, sections, "
            "joints, chains, attachments, mirrored limbs, volumes or named parts, and "
            "idle/move/attack animations. Do not pass file paths or code in the spec; the "
            "tool owns output paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "object",
                    "description": (
                        "Complete anyCreature spec JSON. Common keys are height, palette, "
                        "sections, joints, chains, attach, mirror, volumes, parts, and animations."
                    ),
                    "additionalProperties": True,
                },
                "output_name": {
                    "type": "string",
                    "maxLength": 48,
                    "description": "Optional display name used for the persistent GLB and spec files.",
                },
            },
            "required": ["spec"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "voxtart_create_box",
        "title": "Create a voxel box",
        "description": "Create a new cubic VOXTART asset with deterministic grid-aligned voxels.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "New Blender object name."},
                "size": {"type": "integer", "minimum": 1, "maximum": 46},
                "grid_size": {"type": "number", "exclusiveMinimum": 0},
                "palette_index": {"type": "integer", "minimum": 0},
                "color": _COLOR_SCHEMA,
            },
            "required": ["size"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "voxtart_create_asset",
        "title": "Create a voxel asset",
        "description": "Create a sparse VOXTART asset from explicit cells and an optional RGBA palette.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "New Blender object name."},
                "grid_size": {"type": "number", "exclusiveMinimum": 0},
                "palette": {"type": "array", "items": _COLOR_SCHEMA, "maxItems": 256},
                "voxels": {
                    "type": "array",
                    "maxItems": 100000,
                    "items": {
                        "type": "object",
                        "properties": {"cell": _CELL_SCHEMA, "palette_index": {"type": "integer", "minimum": 0}},
                        "required": ["cell"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["voxels"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "voxtart_edit_asset",
        "title": "Edit a voxel asset",
        "description": "Apply one bounded add, remove, paint, box-fill, or flood-fill edit atomically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "object_name": {"type": "string", "description": "Blender object name; defaults to the active object."},
                "operation": {"type": "string", "enum": ["add", "remove", "paint", "fill_box", "flood_fill"]},
                "cell": _CELL_SCHEMA,
                "minimum": _CELL_SCHEMA,
                "maximum": _CELL_SCHEMA,
                "palette_index": {"type": "integer", "minimum": 0},
                "allow_protected": {"type": "boolean", "default": False},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "voxtart_rebuild_asset",
        "title": "Rebuild a voxel mesh",
        "description": "Rebuild the active or named asset using the greedy or marching surface backend.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "object_name": {"type": "string", "description": "Blender object name; defaults to the active object."},
                "backend": {"type": "string", "enum": ["greedy", "marching"], "default": "greedy"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    },
    {
        "name": "voxtart_animation_plugins",
        "title": "Inspect animation plugins",
        "description": "List the optional animation-library and retargeting integrations available to VOXTART.",
        "inputSchema": _EMPTY_SCHEMA,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "voxtart_import_animation_library",
        "title": "Import a humanoid animation library",
        "description": "Import a user-owned Quaternius Universal Animation Library .blend, .fbx, .glb, or .gltf file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Absolute local path to the downloaded animation source."},
            },
            "required": ["filepath"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "voxtart_prepare_character",
        "title": "Prepare a non-rigged character",
        "description": "Create an editable humanoid blockout rig for a mesh and bind it with Blender automatic weights.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "object_name": {"type": "string", "description": "Mesh object name; defaults to the active mesh."},
                "bind": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "voxtart_retarget_animation",
        "title": "Retarget a humanoid animation",
        "description": "Map a source action to a target armature and optionally bake it into a target action.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_armature": {"type": "string"},
                "target_armature": {"type": "string"},
                "action": {"type": "string"},
                "backend": {"type": "string", "enum": ["AUTO", "MWNI", "NATIVE"], "default": "AUTO"},
                "bake": {"type": "boolean", "default": True},
                "mapping": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "target": {"type": "string"},
                        },
                        "required": ["source", "target"],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
)


PROMPT_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "voxtart_create",
        "title": "Create a voxel asset",
        "description": "Guide an LLM to create a bounded VOXTART asset using the exposed tools.",
        "arguments": [
            {"name": "request", "description": "Natural-language voxel creation request.", "required": True},
        ],
    },
    {
        "name": "voxtart_animate_character",
        "title": "Animate a character",
        "description": "Guide an LLM through importing a humanoid source, preparing an unrigged mesh, and retargeting an action.",
        "arguments": [
            {"name": "request", "description": "Natural-language character animation request.", "required": True},
        ],
    },
)


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, error: MCPError) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": error.as_dict()}


class MCPDispatcher:
    """Route MCP requests while delegating Blender operations to callbacks."""

    def __init__(
        self,
        tool_handler: Callable[[str, Mapping[str, Any]], Any],
        resource_handler: Callable[[str], Any] | None = None,
    ) -> None:
        self._tool_handler = tool_handler
        self._resource_handler = resource_handler

    def dispatch(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        is_notification = "id" not in message
        try:
            if message.get("jsonrpc") != "2.0":
                raise MCPError(-32600, "JSON-RPC version must be 2.0")
            method = message.get("method")
            if not isinstance(method, str) or not method:
                raise MCPError(-32600, "JSON-RPC request requires a method")
            params = message.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, Mapping):
                raise MCPError(-32602, "params must be an object")
            result = self._dispatch_method(method, params)
            if is_notification:
                return None
            return _rpc_result(request_id, result)
        except MCPError as error:
            if is_notification:
                return None
            return _rpc_error(request_id, error)
        except Exception as error:  # Boundary: tools must not break the transport.
            if is_notification:
                return None
            return _rpc_error(request_id, MCPError(-32603, "Internal MCP server error", str(error)))

    def _dispatch_method(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "initialize":
            requested = params.get("protocolVersion", MCP_PROTOCOL_VERSION)
            version = (
                requested
                if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
                else MCP_PROTOCOL_VERSION
            )
            return {
                "protocolVersion": version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": dict(SERVER_INFO),
                "instructions": (
                    "VOXTART exposes bounded voxel authoring, a bundled offline anyCreature "
                    "creature generator, and humanoid animation tools. Mutating calls require "
                    "the user to enable LLM edits in Blender."
                ),
            }
        if method == "server/discover":
            return {
                "protocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                "serverInfo": dict(SERVER_INFO),
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [dict(tool) for tool in TOOL_DEFINITIONS]}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not name:
                raise MCPError(-32602, "tools/call requires a tool name")
            if not isinstance(arguments, Mapping):
                raise MCPError(-32602, "tool arguments must be an object")
            known = {tool["name"] for tool in TOOL_DEFINITIONS}
            if name not in known:
                raise MCPError(-32602, f"unknown VOXTART tool: {name}")
            try:
                value = self._tool_handler(name, arguments)
            except MCPToolError as error:
                return {"content": [{"type": "text", "text": str(error)}], "isError": True}
            return {
                "content": [{"type": "text", "text": _json_text(value)}],
                "structuredContent": value if isinstance(value, Mapping) else {"value": value},
                "isError": False,
            }
        if method == "resources/list":
            resources = self._resource_handler("__list__") if self._resource_handler else []
            return {"resources": resources}
        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                raise MCPError(-32602, "resources/read requires a URI")
            if self._resource_handler is None:
                raise MCPError(-32601, "resources are unavailable")
            return {"contents": [self._resource_handler(uri)]}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "prompts/list":
            return {"prompts": [dict(prompt) for prompt in PROMPT_DEFINITIONS]}
        if method == "prompts/get":
            name = params.get("name")
            if name not in {"voxtart_create", "voxtart_animate_character"}:
                raise MCPError(-32602, f"unknown VOXTART prompt: {name}")
            arguments = params.get("arguments", {})
            request = arguments.get("request", "") if isinstance(arguments, Mapping) else ""
            description = (
                "Use VOXTART tools to animate the requested character."
                if name == "voxtart_animate_character"
                else "Use VOXTART tools to create the requested bounded voxel asset."
            )
            return {
                "description": description,
                "messages": [{"role": "user", "content": {"type": "text", "text": str(request)}}],
            }
        if method == "logging/setLevel":
            return {}
        raise MCPError(-32601, f"method not found: {method}")