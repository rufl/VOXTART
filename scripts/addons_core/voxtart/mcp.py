# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Optional MCP server and LLM chat panel for VOXTART.

The MCP endpoint is disabled until the user starts it from the VOXTART MCP
panel.  It binds only to localhost, requires a per-session bearer token, and
marshals all Blender mutations back to Blender's main thread.  The chat panel
is also opt-in and uses an OpenAI-compatible chat-completions endpoint; its
model receives the same bounded MCP tools exposed to external clients.
"""


import http.server
import json
import math
import os
import queue
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from .mcp_protocol import (
    MCPDispatcher,
    MCPError,
    MCPToolError,
    TOOL_DEFINITIONS,
)
from .voxel import VoxelAsset


ADDON_ID = (__package__ or "voxtart").split(".")[-1]
MCP_PATH = "/mcp"
MAX_HTTP_BODY = 4 * 1024 * 1024
MAX_TOOL_VOXELS = 100_000
MAX_BOX_SIZE = 46
MAX_CHAT_TRANSCRIPT = 60_000
MUTATING_TOOLS = frozenset(
    {
        "voxtart_create_box",
        "voxtart_create_asset",
        "voxtart_edit_asset",
        "voxtart_rebuild_asset",
        "voxtart_import_animation_library",
        "voxtart_prepare_character",
        "voxtart_retarget_animation",
    }
)


@dataclass(slots=True)
class _PendingMainCall:
    kind: str
    payload: Any
    event: threading.Event
    result: Any = None
    error: BaseException | None = None


@dataclass(slots=True)
class _ChatResult:
    kind: str
    payload: Any


_CHAT_RESULTS: queue.Queue[_ChatResult] = queue.Queue()
_CHAT_HISTORY: list[dict[str, Any]] = []
_CHAT_WORKER_ACTIVE = False
_CHAT_TIMER_REGISTERED = False
_CHAT_LOCK = threading.Lock()


def _preferences():
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    if addon is None:
        return None
    return addon.preferences


def _window_manager():
    return bpy.context.window_manager


def _set_chat_status(status: str) -> None:
    try:
        _window_manager().voxtart_chat_status = status
    except AttributeError:
        pass


def _append_chat_transcript(role: str, text: str) -> None:
    manager = _window_manager()
    current = str(getattr(manager, "voxtart_chat_transcript", ""))
    entry = f"{role}: {text.strip()}\n"
    manager.voxtart_chat_transcript = (current + entry)[-MAX_CHAT_TRANSCRIPT:]


def _json_safe(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _object_asset(obj: bpy.types.Object | None):
    if obj is None:
        raise MCPToolError("No Blender object is active")
    from . import operators

    try:
        return operators._load_asset(obj)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise MCPToolError(f"Object {obj.name!r} is not a VOXTART asset: {error}") from error


def _find_asset_object(context: bpy.types.Context, object_name: str | None = None):
    if object_name:
        obj = bpy.data.objects.get(str(object_name))
    else:
        obj = context.active_object
    if obj is None:
        raise MCPToolError("No VOXTART asset object was found")
    _object_asset(obj)
    return obj


def _asset_summary(obj: bpy.types.Object, asset: VoxelAsset, *, include_voxels: bool = False) -> dict[str, Any]:
    minimum_maximum = asset.bounds
    result: dict[str, Any] = {
        "object_name": obj.name,
        "voxel_count": len(asset.voxels),
        "palette_count": len(asset.palette),
        "grid_size": asset.grid_size,
        "bounds": _json_safe(minimum_maximum),
        "socket_count": len(asset.sockets),
        "provenance": dict(asset.provenance),
    }
    if include_voxels:
        if len(asset.voxels) > MAX_TOOL_VOXELS:
            raise MCPToolError(f"Asset has {len(asset.voxels)} voxels; limit is {MAX_TOOL_VOXELS}")
        result["voxels"] = [
            {"cell": list(cell), "palette_index": palette_index}
            for cell, palette_index in asset.iter_voxels()
        ]
        result["palette"] = [list(color) for color in asset.palette]
    return result


def _scene_state(context: bpy.types.Context) -> dict[str, Any]:
    assets = []
    for obj in sorted(context.scene.objects, key=lambda item: item.name):
        if "voxtart_asset_json" not in obj:
            continue
        try:
            asset = _object_asset(obj)
        except MCPToolError:
            continue
        assets.append(_asset_summary(obj, asset))
    return {
        "active_object": context.active_object.name if context.active_object else None,
        "assets": assets,
        "asset_count": len(assets),
    }


def _refresh_asset(obj: bpy.types.Object, asset: VoxelAsset, backend: str | None = None) -> None:
    from . import operators
    from .layers import LayerStack

    operators._replace_asset_mesh(
        obj,
        asset,
        backend=backend,
        layer_stack=LayerStack.from_asset(asset),
    )

def _create_asset(context: bpy.types.Context, name: str, asset: VoxelAsset):
    from . import operators

    safe_name = str(name or "VOXTART AI Asset").strip()[:128] or "VOXTART AI Asset"
    return operators._create_asset_object(context, safe_name, asset)


def _require_edit_permission() -> None:
    preferences = _preferences()
    if preferences is None or not preferences.allow_mcp_edits:
        raise MCPToolError(
            "LLM edits are disabled. Enable 'Allow LLM scene edits' in the VOXTART MCP panel first."
        )


def _coerce_cell(value: Any, field: str = "cell") -> tuple[int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise MCPToolError(f"{field} must contain exactly three integer coordinates")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise MCPToolError(f"{field} must contain exactly three integer coordinates")
    return tuple(value)  # type: ignore[return-value]


def _coerce_int(value: Any, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MCPToolError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise MCPToolError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise MCPToolError(f"{field} must be at most {maximum}")
    return value


def _coerce_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise MCPToolError(f"{field} must be a finite number") from error
    if not math.isfinite(result):
        raise MCPToolError(f"{field} must be a finite number")
    return result


def _bounded_volume(minimum: tuple[int, int, int], maximum: tuple[int, int, int]) -> None:
    spans = tuple(maximum[axis] - minimum[axis] + 1 for axis in range(3))
    if any(span <= 0 for span in spans):
        raise MCPToolError("maximum bounds must not be below minimum bounds")
    volume = spans[0] * spans[1] * spans[2]
    if volume > MAX_TOOL_VOXELS:
        raise MCPToolError(f"edit bounds contain {volume} cells; limit is {MAX_TOOL_VOXELS}")


def _coerce_color(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise MCPToolError("color must contain four RGBA values")
    try:
        color = tuple(float(item) for item in value)
    except (TypeError, ValueError, OverflowError) as error:
        raise MCPToolError("color must contain numeric RGBA values") from error
    if any(not math.isfinite(item) or item < 0.0 or item > 1.0 for item in color):
        raise MCPToolError("color values must be finite and in the range [0, 1]")
    return color  # type: ignore[return-value]


def _tool_call(context: bpy.types.Context, name: str, arguments: Mapping[str, Any]) -> Any:
    if name in MUTATING_TOOLS:
        _require_edit_permission()

    if name == "voxtart_scene_state":
        return _scene_state(context)

    if name == "voxtart_asset_state":
        obj = _find_asset_object(context, arguments.get("object_name"))
        return _asset_summary(obj, _object_asset(obj), include_voxels=bool(arguments.get("include_voxels", False)))

    if name == "voxtart_create_box":
        size = arguments.get("size", 1)
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_BOX_SIZE:
            raise MCPToolError(f"size must be an integer between 1 and {MAX_BOX_SIZE}")
        grid_size = _coerce_float(arguments.get("grid_size", 1.0), "grid_size")
        if grid_size <= 0.0:
            raise MCPToolError("grid_size must be positive")
        palette_index = _coerce_int(arguments.get("palette_index", 0), "palette_index", minimum=0, maximum=255)
        palette = [(0.8, 0.8, 0.8, 1.0)] * (palette_index + 1)
        if "color" in arguments:
            palette[palette_index] = _coerce_color(arguments["color"])
        asset = VoxelAsset(grid_size=grid_size, palette=palette)
        offset = -(size // 2)
        for x in range(size):
            for y in range(size):
                for z in range(size):
                    asset.set_voxel((offset + x, offset + y, offset + z), palette_index)
        obj = _create_asset(context, str(arguments.get("name", "VOXTART AI Box")), asset)
        return _asset_summary(obj, asset)

    if name == "voxtart_create_asset":
        raw_voxels = arguments.get("voxels", [])
        if not isinstance(raw_voxels, list) or len(raw_voxels) > MAX_TOOL_VOXELS:
            raise MCPToolError(f"voxels must be a list with at most {MAX_TOOL_VOXELS} entries")
        raw_palette = arguments.get("palette")
        palette = raw_palette if raw_palette is not None else [(0.8, 0.8, 0.8, 1.0)]
        if not isinstance(palette, list) or not palette or len(palette) > 256:
            raise MCPToolError("palette must contain between 1 and 256 colors")
        normalized_palette = [_coerce_color(color) for color in palette]
        voxels: dict[tuple[int, int, int], int] = {}
        for entry in raw_voxels:
            if not isinstance(entry, Mapping):
                raise MCPToolError("each voxel entry must be an object")
            cell = _coerce_cell(entry.get("cell"))
            palette_index = _coerce_int(
                entry.get("palette_index", 0),
                "palette_index",
                minimum=0,
                maximum=len(normalized_palette) - 1,
            )
            voxels[cell] = palette_index
        grid_size = _coerce_float(arguments.get("grid_size", 1.0), "grid_size")
        if grid_size <= 0.0:
            raise MCPToolError("grid_size must be positive")
        asset = VoxelAsset(grid_size=grid_size, palette=normalized_palette, voxels=voxels)
        obj = _create_asset(context, str(arguments.get("name", "VOXTART AI Asset")), asset)
        return _asset_summary(obj, asset)

    if name == "voxtart_edit_asset":
        obj = _find_asset_object(context, arguments.get("object_name"))
        asset = _object_asset(obj)
        operation = str(arguments.get("operation", ""))
        palette_index = _coerce_int(
            arguments.get("palette_index", 0),
            "palette_index",
            minimum=0,
            maximum=len(asset.palette) - 1,
        )
        from . import operators
        from .edit import VoxelEditTransaction

        protected = operators._protected_cells(obj)
        with VoxelEditTransaction(
            asset,
            protected=protected,
            allow_protected=bool(arguments.get("allow_protected", False)),
            operation=operation,
        ) as transaction:
            if operation == "add":
                transaction.add(_coerce_cell(arguments.get("cell")), palette_index)
            elif operation == "remove":
                transaction.remove(_coerce_cell(arguments.get("cell")))
            elif operation == "paint":
                transaction.paint(_coerce_cell(arguments.get("cell")), palette_index)
            elif operation == "fill_box":
                minimum = _coerce_cell(arguments.get("minimum"), "minimum")
                maximum = _coerce_cell(arguments.get("maximum"), "maximum")
                _bounded_volume(minimum, maximum)
                transaction.fill_box(minimum, maximum, palette_index)
            elif operation == "flood_fill":
                minimum = (
                    _coerce_cell(arguments.get("minimum"), "minimum")
                    if arguments.get("minimum") is not None
                    else _coerce_cell(arguments.get("cell"))
                )
                maximum = _coerce_cell(arguments.get("maximum"), "maximum")
                _bounded_volume(minimum, maximum)
                transaction.flood_fill(
                    _coerce_cell(arguments.get("cell")),
                    maximum,
                    palette_index,
                    minimum=minimum,
                )
            else:
                raise MCPToolError(f"unsupported edit operation: {operation}")
            report = transaction.commit()
        _refresh_asset(obj, asset)
        return {"object_name": obj.name, "edit": report.to_dict(), "asset": _asset_summary(obj, asset)}

    if name == "voxtart_rebuild_asset":
        obj = _find_asset_object(context, arguments.get("object_name"))
        backend = str(arguments.get("backend", "greedy"))
        if backend not in {"greedy", "marching"}:
            raise MCPToolError("backend must be 'greedy' or 'marching'")
        asset = _object_asset(obj)
        _refresh_asset(obj, asset, backend=backend)
        return {"object_name": obj.name, "backend": backend, "asset": _asset_summary(obj, asset)}

    if name == "voxtart_animation_plugins":
        from . import animation

        return {"plugins": animation.plugin_status(), "state": animation.animation_state(context)}

    if name == "voxtart_import_animation_library":
        from . import animation

        filepath = arguments.get("filepath")
        if not isinstance(filepath, str) or not filepath.strip():
            raise MCPToolError("filepath must be a non-empty local path")
        try:
            return animation.import_animation_library(context, filepath)
        except (OSError, RuntimeError, ValueError) as error:
            raise MCPToolError(str(error)) from error

    if name == "voxtart_prepare_character":
        from . import animation

        object_name = arguments.get("object_name")
        mesh = animation._get_object(object_name, "MESH") if object_name else context.active_object
        if mesh is None or mesh.type != "MESH":
            raise MCPToolError("object_name must identify a mesh character or the active object must be a mesh")
        try:
            return animation.create_blockout_rig(context, mesh, bind=bool(arguments.get("bind", True)))
        except (RuntimeError, ValueError) as error:
            raise MCPToolError(str(error)) from error

    if name == "voxtart_retarget_animation":
        from . import animation

        raw_mapping = arguments.get("mapping")
        if raw_mapping is not None:
            if not isinstance(raw_mapping, list):
                raise MCPToolError("mapping must be a list of source/target objects")
            mapping = []
            for item in raw_mapping:
                if not isinstance(item, Mapping) or not isinstance(item.get("source"), str) or not isinstance(item.get("target"), str):
                    raise MCPToolError("each mapping entry requires string source and target fields")
                mapping.append({"source": item["source"], "target": item["target"]})
        else:
            mapping = None
        backend = str(arguments.get("backend", "AUTO")).upper()
        try:
            return animation.retarget_animation(
                context,
                source_name=arguments.get("source_armature"),
                target_name=arguments.get("target_armature"),
                action_name=arguments.get("action"),
                backend=backend,
                bake=bool(arguments.get("bake", True)),
                mapping=mapping,
            )
        except (RuntimeError, ValueError) as error:
            raise MCPToolError(str(error)) from error

    raise MCPToolError(f"unsupported VOXTART tool: {name}")


def _resource_data(context: bpy.types.Context, uri: str) -> Any:
    if uri == "__list__":
        resources = [
            {
                "uri": "voxtart://scene",
                "name": "VOXTART scene",
                "description": "Current VOXTART asset summaries in the Blender scene.",
                "mimeType": "application/json",
            },
            {
                "uri": "voxtart://animation",
                "name": "VOXTART animation state",
                "description": "Current animation source, target, action, mappings, and optional plugin status.",
                "mimeType": "application/json",
            },
        ]
        for obj in sorted(context.scene.objects, key=lambda item: item.name):
            if "voxtart_asset_json" in obj:
                resources.append(
                    {
                        "uri": f"voxtart://asset/{urllib.parse.quote(obj.name, safe='')}",
                        "name": obj.name,
                        "description": "Sparse VOXTART voxel asset.",
                        "mimeType": "application/json",
                    }
                )
        return resources

    if uri == "voxtart://scene":
        return {"uri": uri, "mimeType": "application/json", "text": json.dumps(_scene_state(context), sort_keys=True)}
    if uri == "voxtart://animation":
        from . import animation

        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(animation.animation_state(context), sort_keys=True),
        }

    prefix = "voxtart://asset/"
    if uri.startswith(prefix):
        object_name = urllib.parse.unquote(uri[len(prefix):])
        obj = _find_asset_object(context, object_name)
        asset = _object_asset(obj)
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(_asset_summary(obj, asset, include_voxels=True), sort_keys=True),
        }
    raise MCPError(-32602, f"unknown VOXTART resource: {uri}")


class _MCPRequestHandler(http.server.BaseHTTPRequestHandler):
    bridge: "MCPBridge"
    server_version = "VOXTART-MCP/0.2"

    def _send_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _authorized(self) -> bool:
        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            if parsed.hostname not in {"localhost", "127.0.0.1"}:
                return False
        authorization = self.headers.get("Authorization", "")
        supplied = authorization.removeprefix("Bearer ").strip()
        supplied = supplied or self.headers.get("X-VOXTART-MCP-Token", "")
        return secrets.compare_digest(supplied, self.bridge.token)

    def do_OPTIONS(self) -> None:
        if self.path.split("?", 1)[0] != MCP_PATH:
            self.send_error(404)
            return
        self.send_response(204)
        self.send_header("Allow", "POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        self.send_error(405, "VOXTART MCP does not provide an SSE stream")

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != MCP_PATH:
            self.send_error(404)
            return
        if not self._authorized():
            self.send_error(401, "Missing or invalid VOXTART MCP bearer token")
            return
        protocol_version = self.headers.get("MCP-Protocol-Version")
        if protocol_version and protocol_version not in {"2025-06-18", "2025-03-26"}:
            self._send_json(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": "Unsupported MCP protocol version",
                        "data": {"supported": ["2025-06-18", "2025-03-26"]},
                    },
                },
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if length <= 0 or length > MAX_HTTP_BODY:
            self.send_error(413 if length > MAX_HTTP_BODY else 400, "Invalid MCP request size")
            return
        try:
            message = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("MCP request must be a JSON object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self._send_json(
                400,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}},
            )
            return
        response = self.bridge.dispatch(message)
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(200, response)

    def log_message(self, format: str, *args: Any) -> None:
        return


class MCPBridge:
    """Localhost Streamable-HTTP MCP bridge for Blender state."""

    def __init__(self) -> None:
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._pending: queue.Queue[_PendingMainCall] = queue.Queue()
        self._running = False
        self._timer_registered = False
        self.port = 0
        self.token = secrets.token_urlsafe(24)

    @property
    def running(self) -> bool:
        return self._running and self._server is not None

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}{MCP_PATH}"

    def start(self, port: int) -> None:
        if self.running:
            return
        port = int(port)
        if not 1024 <= port <= 65535:
            raise ValueError("MCP port must be between 1024 and 65535")
        self.token = secrets.token_urlsafe(24)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _MCPRequestHandler)
        server.daemon_threads = True
        _MCPRequestHandler.bridge = self
        self._server = server
        self.port = port
        self._running = True
        self._thread = threading.Thread(target=server.serve_forever, name="VOXTART-MCP", daemon=True)
        self._thread.start()
        self._ensure_main_timer()

    def stop(self) -> None:
        self._running = False
        server = self._server
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._server = None
        while True:
            try:
                pending = self._pending.get_nowait()
            except queue.Empty:
                break
            pending.error = MCPToolError("VOXTART MCP server stopped")
            pending.event.set()
        if self._timer_registered:
            try:
                bpy.app.timers.unregister(self._drain_main_queue)
            except (ValueError, RuntimeError):
                pass
            self._timer_registered = False

    def _ensure_main_timer(self) -> None:
        if self._timer_registered:
            return
        self._timer_registered = True
        bpy.app.timers.register(self._drain_main_queue, first_interval=0.05)

    def _drain_main_queue(self) -> float | None:
        if not self._running:
            self._timer_registered = False
            return None
        for _ in range(32):
            try:
                pending = self._pending.get_nowait()
            except queue.Empty:
                break
            try:
                pending.result = self._execute_main(pending.kind, pending.payload)
            except BaseException as error:  # Resolve the waiting HTTP request.
                pending.error = error
            finally:
                pending.event.set()
        return 0.05

    def _execute_main(self, kind: str, payload: Any) -> Any:
        context = bpy.context
        if kind == "tool":
            name, arguments = payload
            return _tool_call(context, name, arguments)
        if kind == "resource":
            return _resource_data(context, payload)
        raise RuntimeError(f"unknown main-thread MCP call kind: {kind}")

    def _on_main_thread(self, kind: str, payload: Any) -> Any:
        if threading.current_thread() is threading.main_thread():
            return self._execute_main(kind, payload)
        if not self._running:
            raise MCPToolError("VOXTART MCP server is not running")
        pending = _PendingMainCall(kind=kind, payload=payload, event=threading.Event())
        self._pending.put(pending)
        if not pending.event.wait(timeout=60.0):
            raise MCPToolError("Blender did not complete the MCP request within 60 seconds")
        if pending.error is not None:
            if isinstance(pending.error, (MCPToolError, MCPError)):
                raise pending.error
            raise MCPToolError(str(pending.error)) from pending.error
        return pending.result

    def dispatch(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        dispatcher = MCPDispatcher(
            tool_handler=lambda name, arguments: self._on_main_thread("tool", (name, arguments)),
            resource_handler=lambda uri: self._on_main_thread("resource", uri),
        )
        return dispatcher.dispatch(message)

    def dispatch_local(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        dispatcher = MCPDispatcher(
            tool_handler=lambda name, arguments: _tool_call(bpy.context, name, arguments),
            resource_handler=lambda uri: _resource_data(bpy.context, uri),
        )
        return dispatcher.dispatch(message)


_BRIDGE = MCPBridge()


def _openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            },
        }
        for tool in TOOL_DEFINITIONS
    ]


def _chat_request(endpoint: str, model: str, api_key: str, history: list[dict[str, Any]]) -> None:
    global _CHAT_WORKER_ACTIVE
    try:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the VOXTART voxel authoring assistant. "
                        "Use the provided tools for scene changes. Keep creations bounded, "
                        "grid-aligned, and explain the result briefly."
                    ),
                },
                *history,
            ],
            "tools": _openai_tools(),
            "tool_choice": "auto",
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120.0) as response:
            result = json.loads(response.read().decode("utf-8"))
        _CHAT_RESULTS.put(_ChatResult("response", result))
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        _CHAT_RESULTS.put(_ChatResult("error", str(error)))
    finally:
        with _CHAT_LOCK:
            _CHAT_WORKER_ACTIVE = False


def _ensure_chat_timer() -> None:
    global _CHAT_TIMER_REGISTERED
    if _CHAT_TIMER_REGISTERED:
        return
    _CHAT_TIMER_REGISTERED = True
    bpy.app.timers.register(_drain_chat_results, first_interval=0.1)


def _start_chat_request() -> None:
    global _CHAT_WORKER_ACTIVE
    preferences = _preferences()
    if preferences is None:
        raise RuntimeError("VOXTART preferences are unavailable")
    endpoint = preferences.llm_endpoint.strip()
    model = preferences.llm_model.strip()
    api_key = preferences.llm_api_key.strip() or os.environ.get("VOXTART_LLM_API_KEY", "")
    if not endpoint or not model:
        raise ValueError("Configure an LLM endpoint and model in VOXTART Add-on Preferences")
    with _CHAT_LOCK:
        if _CHAT_WORKER_ACTIVE:
            raise ValueError("A VOXTART chat request is already running")
        _CHAT_WORKER_ACTIVE = True
    thread = threading.Thread(
        target=_chat_request,
        args=(endpoint, model, api_key, list(_CHAT_HISTORY)),
        name="VOXTART-LLM",
        daemon=True,
    )
    thread.start()
    _ensure_chat_timer()


def _handle_chat_response(response: Mapping[str, Any]) -> None:
    global _CHAT_HISTORY
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LLM response did not contain choices")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ValueError("LLM response did not contain a chat message")
    assistant_message = dict(message)
    _CHAT_HISTORY.append(assistant_message)
    tool_calls = assistant_message.get("tool_calls") or []
    if tool_calls:
        for call in tool_calls:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function", {})
            if not isinstance(function, Mapping):
                continue
            name = str(function.get("name", ""))
            raw_arguments = function.get("arguments", "{}")
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                if not isinstance(arguments, Mapping):
                    raise ValueError("tool arguments must be an object")
                rpc_response = _BRIDGE.dispatch_local(
                    {"jsonrpc": "2.0", "id": call.get("id", name), "method": "tools/call", "params": {"name": name, "arguments": arguments}}
                )
                content = rpc_response.get("result", {}).get("content", []) if rpc_response else []
                tool_text = "\n".join(str(item.get("text", "")) for item in content if isinstance(item, Mapping))
                if rpc_response and "error" in rpc_response:
                    tool_text = str(rpc_response["error"])
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                tool_text = f"Tool call rejected: {error}"
            _CHAT_HISTORY.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", name),
                    "content": tool_text,
                }
            )
            _append_chat_transcript(f"Tool {name}", tool_text)
        _set_chat_status("Applying tool results…")
        _start_chat_request()
        return
    content = assistant_message.get("content") or "(The model returned no text.)"
    _append_chat_transcript("VOXTART", str(content))
    _set_chat_status("Ready")


def _drain_chat_results() -> float | None:
    global _CHAT_TIMER_REGISTERED
    while True:
        try:
            result = _CHAT_RESULTS.get_nowait()
        except queue.Empty:
            break
        try:
            if result.kind == "error":
                _set_chat_status(f"LLM error: {result.payload}")
            else:
                _handle_chat_response(result.payload)
        except Exception as error:
            _set_chat_status(f"Chat error: {error}")
    with _CHAT_LOCK:
        active = _CHAT_WORKER_ACTIVE
    if active or not _CHAT_RESULTS.empty():
        return 0.1
    _CHAT_TIMER_REGISTERED = False
    return None


class VOXTART_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    mcp_port: IntProperty(
        name="MCP Port",
        description="Localhost port used by the optional VOXTART MCP endpoint",
        min=1024,
        max=65535,
        default=8765,
    )
    allow_mcp_edits: BoolProperty(
        name="Allow LLM scene edits",
        description="Allow MCP and chat tools to create or mutate Blender voxel assets",
        default=False,
    )
    chat_enabled: BoolProperty(
        name="Enable optional chat panel",
        description="Show the optional OpenAI-compatible LLM chat panel in the 3D View sidebar",
        default=False,
    )
    llm_endpoint: StringProperty(
        name="LLM endpoint",
        description="OpenAI-compatible /v1/chat/completions endpoint",
        default="http://127.0.0.1:11434/v1/chat/completions",
    )
    llm_model: StringProperty(
        name="LLM model",
        description="Model identifier accepted by the configured endpoint",
        default="",
    )
    llm_api_key: StringProperty(
        name="LLM API key",
        description="Optional API key; VOXTART_LLM_API_KEY takes precedence when set",
        subtype="PASSWORD",
        default="",
    )

    def draw(self, _context):
        layout = self.layout
        layout.label(text="VOXTART MCP is opt-in and localhost-only.")
        layout.prop(self, "mcp_port")
        layout.prop(self, "allow_mcp_edits")
        layout.prop(self, "chat_enabled")
        layout.prop(self, "llm_endpoint")
        layout.prop(self, "llm_model")
        layout.prop(self, "llm_api_key")
        layout.label(text="The VOXTART_LLM_API_KEY environment variable overrides the stored key.")


class VOXTART_OT_mcp_start(bpy.types.Operator):
    bl_idname = "voxtart.mcp_start"
    bl_label = "Start MCP Server"
    bl_description = "Start the optional localhost MCP endpoint for external LLM clients"

    def execute(self, _context):
        preferences = _preferences()
        try:
            _BRIDGE.start(preferences.mcp_port if preferences else 8765)
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"MCP endpoint: {_BRIDGE.endpoint}; bearer token: {_BRIDGE.token}")
        return {'FINISHED'}


class VOXTART_OT_mcp_stop(bpy.types.Operator):
    bl_idname = "voxtart.mcp_stop"
    bl_label = "Stop MCP Server"
    bl_description = "Stop the optional localhost MCP endpoint"

    def execute(self, _context):
        _BRIDGE.stop()
        self.report({'INFO'}, "VOXTART MCP server stopped")
        return {'FINISHED'}


class VOXTART_OT_mcp_copy_config(bpy.types.Operator):
    bl_idname = "voxtart.mcp_copy_config"
    bl_label = "Copy MCP Connection"
    bl_description = "Copy a JSON connection snippet for an MCP client"

    def execute(self, context):
        if not _BRIDGE.running:
            self.report({'ERROR'}, "Start the VOXTART MCP server first")
            return {'CANCELLED'}
        config = {
            "mcpServers": {
                "voxtart": {
                    "url": _BRIDGE.endpoint,
                    "headers": {"Authorization": f"Bearer {_BRIDGE.token}"},
                }
            }
        }
        context.window_manager.clipboard = json.dumps(config, indent=2)
        self.report({'INFO'}, "MCP endpoint and bearer token copied to the clipboard")
        return {'FINISHED'}


class VOXTART_OT_chat_send(bpy.types.Operator):
    bl_idname = "voxtart.chat_send"
    bl_label = "Send to VOXTART Assistant"
    bl_description = "Send the prompt to the configured OpenAI-compatible LLM endpoint"

    def execute(self, _context):
        manager = _window_manager()
        prompt = manager.voxtart_chat_prompt.strip()
        if not prompt:
            self.report({'ERROR'}, "Enter a prompt first")
            return {'CANCELLED'}
        if not (_preferences() and _preferences().chat_enabled):
            self.report({'ERROR'}, "Enable the optional chat panel in VOXTART Add-on Preferences")
            return {'CANCELLED'}
        _CHAT_HISTORY.append({"role": "user", "content": prompt})
        _append_chat_transcript("You", prompt)
        manager.voxtart_chat_prompt = ""
        _set_chat_status("Requesting…")
        try:
            _start_chat_request()
        except (RuntimeError, ValueError) as error:
            _set_chat_status(str(error))
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class VOXTART_OT_chat_clear(bpy.types.Operator):
    bl_idname = "voxtart.chat_clear"
    bl_label = "Clear Chat"
    bl_description = "Clear the optional VOXTART chat transcript"

    def execute(self, _context):
        _CHAT_HISTORY.clear()
        manager = _window_manager()
        manager.voxtart_chat_prompt = ""
        manager.voxtart_chat_transcript = ""
        manager.voxtart_chat_status = "Ready"
        return {'FINISHED'}


class VOXTART_PT_mcp_panel(bpy.types.Panel):
    bl_label = "VOXTART MCP"
    bl_idname = "VOXTART_PT_mcp_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'VOXTART'

    def draw(self, _context):
        layout = self.layout
        preferences = _preferences()
        if _BRIDGE.running:
            layout.label(text=f"Running: {_BRIDGE.endpoint}")
            layout.label(text=f"Bearer token: {_BRIDGE.token}")
            layout.operator(VOXTART_OT_mcp_copy_config.bl_idname, icon='COPYDOWN')
            layout.operator(VOXTART_OT_mcp_stop.bl_idname, icon='PAUSE')
        else:
            layout.label(text="MCP server is stopped")
            layout.operator(VOXTART_OT_mcp_start.bl_idname, icon='PLAY')
        if preferences is not None:
            layout.prop(preferences, "allow_mcp_edits")
            layout.prop(preferences, "chat_enabled")


class VOXTART_PT_chat_panel(bpy.types.Panel):
    bl_label = "VOXTART Assistant"
    bl_idname = "VOXTART_PT_chat_panel"
    bl_parent_id = "VOXTART_PT_mcp_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'VOXTART'

    @classmethod
    def poll(cls, _context):
        preferences = _preferences()
        return preferences is not None and preferences.chat_enabled

    def draw(self, _context):
        layout = self.layout
        preferences = _preferences()
        manager = _window_manager()
        if preferences is not None:
            layout.prop(preferences, "llm_endpoint")
            layout.prop(preferences, "llm_model")
        layout.label(text=str(manager.voxtart_chat_status))
        transcript = str(manager.voxtart_chat_transcript)
        if transcript:
            box = layout.box()
            for line in transcript.splitlines()[-18:]:
                box.label(text=line[:240])
        layout.prop(manager, "voxtart_chat_prompt", text="Prompt")
        row = layout.row(align=True)
        row.operator(VOXTART_OT_chat_send.bl_idname, icon='PLAY')
        row.operator(VOXTART_OT_chat_clear.bl_idname, icon='TRASH')


_CLASSES = (
    VOXTART_AddonPreferences,
    VOXTART_OT_mcp_start,
    VOXTART_OT_mcp_stop,
    VOXTART_OT_mcp_copy_config,
    VOXTART_OT_chat_send,
    VOXTART_OT_chat_clear,
    VOXTART_PT_mcp_panel,
    VOXTART_PT_chat_panel,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.voxtart_chat_prompt = StringProperty(
        name="Prompt", maxlen=4096, default=""
    )
    bpy.types.WindowManager.voxtart_chat_transcript = StringProperty(
        name="Transcript", maxlen=MAX_CHAT_TRANSCRIPT, default=""
    )
    bpy.types.WindowManager.voxtart_chat_status = StringProperty(
        name="Status", maxlen=512, default="Ready"
    )


def unregister() -> None:
    _BRIDGE.stop()
    global _CHAT_HISTORY
    _CHAT_HISTORY.clear()
    for name in ("voxtart_chat_status", "voxtart_chat_transcript", "voxtart_chat_prompt"):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)