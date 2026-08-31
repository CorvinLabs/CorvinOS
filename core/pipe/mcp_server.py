"""Minimal MCP server exposing the Layer-18 pipe registry over stdio.

Transport: line-delimited JSON-RPC 2.0 on stdin/stdout (per MCP spec,
matching the skill-forge / forge MCP servers in this repo).

Tools surfaced:
  - pipe_create     — create a named / anonymous / broadcast pipe
  - pipe_write      — append a message to a pipe
  - pipe_read       — read messages (semantics depend on pipe type)
  - pipe_subscribe  — register a subscriber on a broadcast pipe
  - pipe_unsubscribe — drop a subscriber
  - pipe_list       — list all pipes with metadata
  - pipe_remove     — remove a pipe entirely
  - pipe_get_meta   — inspect a pipe's metadata without reading
  - pipe_queue_depth — return queue depth without consuming

The server is the production-facing interface that personas use to
compose. The underlying ``pipe_registry`` module owns the file
protocol; this server is a thin JSON-RPC adapter.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

# bridges/shared/ is alongside this plugin via the voice plugin tree
HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "voice" / "bridges" / "shared"
sys.path.insert(0, str(SHARED))

import pipe_registry  # type: ignore  # noqa: E402


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "claude-pipe"
SERVER_VERSION = "0.1.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# --------------------------------------------------------------------- schemas

PIPE_CREATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
        "type": {
            "type": "string",
            "enum": list(pipe_registry.PIPE_TYPES),
            "default": "named",
        },
        "owner": {"type": "string"},
    },
}

PIPE_WRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "payload"],
    "properties": {
        "name": {"type": "string"},
        "payload": {},
        "writer": {"type": "string"},
    },
}

PIPE_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
        "subscriber_id": {"type": "string"},
        "max_messages": {"type": "integer", "minimum": 1},
    },
}

PIPE_SUBSCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
        "subscriber_id": {"type": "string"},
    },
}

PIPE_UNSUBSCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "subscriber_id"],
    "properties": {
        "name": {"type": "string"},
        "subscriber_id": {"type": "string"},
    },
}

PIPE_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
}

PIPE_REMOVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
    },
}

PIPE_GET_META_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
    },
}

PIPE_QUEUE_DEPTH_SCHEMA: dict[str, Any] = PIPE_GET_META_SCHEMA


META_TOOLS: list[dict[str, Any]] = [
    {
        "name": "pipe_create",
        "description": (
            "Create a pipe. Type: 'named' (multi-write/multi-read, "
            "persistent), 'anonymous' (auto-removed on first read), "
            "'broadcast' (per-subscriber cursor)."
        ),
        "inputSchema": PIPE_CREATE_SCHEMA,
    },
    {
        "name": "pipe_write",
        "description": "Append a message to a pipe. Returns assigned seq number.",
        "inputSchema": PIPE_WRITE_SCHEMA,
    },
    {
        "name": "pipe_read",
        "description": (
            "Read messages from a pipe. Named/anonymous: consumes from "
            "head. Broadcast: requires subscriber_id, advances cursor."
        ),
        "inputSchema": PIPE_READ_SCHEMA,
    },
    {
        "name": "pipe_subscribe",
        "description": (
            "Register a subscriber on a broadcast pipe. Returns the "
            "subscriber_id (auto-generated if not provided)."
        ),
        "inputSchema": PIPE_SUBSCRIBE_SCHEMA,
    },
    {
        "name": "pipe_unsubscribe",
        "description": "Drop a broadcast subscriber. Returns whether it existed.",
        "inputSchema": PIPE_UNSUBSCRIBE_SCHEMA,
    },
    {
        "name": "pipe_list",
        "description": "List all pipes with metadata.",
        "inputSchema": PIPE_LIST_SCHEMA,
    },
    {
        "name": "pipe_remove",
        "description": "Remove a pipe entirely. Returns whether it existed.",
        "inputSchema": PIPE_REMOVE_SCHEMA,
    },
    {
        "name": "pipe_get_meta",
        "description": "Inspect a pipe's metadata without reading messages.",
        "inputSchema": PIPE_GET_META_SCHEMA,
    },
    {
        "name": "pipe_queue_depth",
        "description": "Return number of queued messages without consuming.",
        "inputSchema": PIPE_QUEUE_DEPTH_SCHEMA,
    },
]


# --------------------------------------------------------------------- server

class PipeMCPServer:
    def __init__(self, *, stdin=None, stdout=None, stderr=None) -> None:
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._stdout_lock = threading.Lock()
        self._initialized = False
        self._shutting_down = False

    # -- transport ---------------------------------------------------------

    def _send(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        with self._stdout_lock:
            self._stdout.write(line)
            self._stdout.flush()

    def _respond(self, msgid: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": msgid, "result": result})

    def _error(self, msgid: Any, code: int, message: str,
               data: Any = None) -> None:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._send({"jsonrpc": "2.0", "id": msgid, "error": err})

    def _log(self, *args: Any) -> None:
        print(*args, file=self._stderr, flush=True)

    def _tool_error(self, msgid: Any, message: str) -> None:
        # MCP convention: tool errors come back as a result with
        # isError=true, NOT as a JSON-RPC error.
        self._respond(
            msgid,
            {
                "isError": True,
                "content": [{"type": "text", "text": message}],
            },
        )

    def _tool_ok(self, msgid: Any, payload: Any) -> None:
        self._respond(
            msgid,
            {
                "isError": False,
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, indent=2, default=str),
                    }
                ],
                "structuredContent": payload,
            },
        )

    # -- main loop ---------------------------------------------------------

    def serve(self) -> int:
        try:
            for raw in self._stdin:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    self._error(None, PARSE_ERROR, "parse error")
                    continue
                try:
                    self._dispatch(msg)
                except Exception as exc:  # pragma: no cover (defense in depth)
                    self._log("server: unhandled", repr(exc))
                    self._log(traceback.format_exc())
                    msgid = msg.get("id") if isinstance(msg, dict) else None
                    self._error(msgid, INTERNAL_ERROR, f"internal error: {exc}")
                if self._shutting_down:
                    break
        finally:
            # Flush + close stdout so any client reader gets EOF cleanly,
            # close stdin to release any blocked readline if the client
            # disconnected mid-frame. Best-effort; never raises.
            self._cleanup_streams()
        return 0

    def _cleanup_streams(self) -> None:
        try:
            self._stdout.flush()
        except (OSError, ValueError):
            pass
        for stream in (self._stdout, self._stdin):
            try:
                if stream is not sys.stdout and stream is not sys.stdin:
                    stream.close()
            except (OSError, ValueError):
                pass

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self._error(msg.get("id") if isinstance(msg, dict) else None,
                        INVALID_REQUEST, "invalid request")
            return
        method = msg.get("method")
        msgid = msg.get("id")
        params = msg.get("params") or {}
        is_notification = "id" not in msg

        if method == "initialize":
            self._handle_initialize(msgid, params)
        elif method == "notifications/initialized":
            self._initialized = True
        elif method == "tools/list":
            self._respond(msgid, {"tools": META_TOOLS})
        elif method == "tools/call":
            self._handle_tools_call(msgid, params)
        elif method == "shutdown":
            self._respond(msgid, None)
            self._shutting_down = True
        elif method == "ping":
            self._respond(msgid, {})
        elif is_notification:
            return
        else:
            self._error(msgid, METHOD_NOT_FOUND,
                        f"method not found: {method}")

    def _handle_initialize(self, msgid: Any, params: dict) -> None:
        client_info = params.get("clientInfo", {})
        self._log(f"initialize from {client_info.get('name', '?')}")
        self._respond(
            msgid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        )

    # -- tools/call --------------------------------------------------------

    def _handle_tools_call(self, msgid: Any, params: dict) -> None:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str):
            self._error(msgid, INVALID_PARAMS, "missing tool name")
            return
        handler = getattr(self, f"_call_{name}", None)
        if handler is None:
            self._tool_error(msgid, f"unknown pipe tool: {name}")
            return
        try:
            handler(msgid, args)
        except (FileExistsError, FileNotFoundError, KeyError, ValueError) as exc:
            # Domain errors come back as tool errors, not protocol errors
            self._tool_error(msgid, f"{type(exc).__name__}: {exc}")

    # -- tool implementations ---------------------------------------------

    def _call_pipe_create(self, msgid: Any, args: dict) -> None:
        meta = pipe_registry.create_pipe(
            name=args["name"],
            pipe_type=args.get("type", "named"),
            owner=args.get("owner"),
        )
        self._tool_ok(msgid, meta)

    def _call_pipe_write(self, msgid: Any, args: dict) -> None:
        seq = pipe_registry.write(
            name=args["name"],
            payload=args["payload"],
            writer=args.get("writer"),
        )
        self._tool_ok(msgid, {"seq": seq})

    def _call_pipe_read(self, msgid: Any, args: dict) -> None:
        msgs = pipe_registry.read(
            name=args["name"],
            subscriber_id=args.get("subscriber_id"),
            max_messages=args.get("max_messages"),
        )
        self._tool_ok(msgid, {"messages": msgs, "count": len(msgs)})

    def _call_pipe_subscribe(self, msgid: Any, args: dict) -> None:
        sid = pipe_registry.subscribe(
            name=args["name"],
            subscriber_id=args.get("subscriber_id"),
        )
        self._tool_ok(msgid, {"subscriber_id": sid})

    def _call_pipe_unsubscribe(self, msgid: Any, args: dict) -> None:
        existed = pipe_registry.unsubscribe(
            name=args["name"],
            subscriber_id=args["subscriber_id"],
        )
        self._tool_ok(msgid, {"existed": existed})

    def _call_pipe_list(self, msgid: Any, args: dict) -> None:
        pipes = pipe_registry.list_pipes()
        self._tool_ok(msgid, {"pipes": pipes, "count": len(pipes)})

    def _call_pipe_remove(self, msgid: Any, args: dict) -> None:
        existed = pipe_registry.remove_pipe(name=args["name"])
        self._tool_ok(msgid, {"existed": existed})

    def _call_pipe_get_meta(self, msgid: Any, args: dict) -> None:
        meta = pipe_registry.get_meta(name=args["name"])
        if meta is None:
            self._tool_error(msgid, f"pipe {args['name']!r} does not exist")
            return
        self._tool_ok(msgid, meta)

    def _call_pipe_queue_depth(self, msgid: Any, args: dict) -> None:
        depth = pipe_registry.queue_depth(name=args["name"])
        self._tool_ok(msgid, {"depth": depth})


def main() -> int:
    return PipeMCPServer().serve()


if __name__ == "__main__":
    sys.exit(main())
