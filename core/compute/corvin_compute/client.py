"""Sync client for the worker socket (ADR-0013 Phase 13.4).

Used by:
- ``tests/test_worker.py`` to drive the worker end-to-end.
- The Forge MCP bridge (Phase 13.5) to translate MCP calls into worker
  RPCs.

The client is intentionally sync — MCP tool handlers run in the MCP
server's thread; mixing asyncio across the MCP/worker boundary would
require a second event-loop run per call. Sync sockets are simpler.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any, Mapping

from .transport import recv_frame_sync, send_frame_sync, TransportError

# Messenger channels whose completions can be routed back to a chat. Kept in
# sync with the worker's _MESSENGER_CHANNELS.
_MESSENGER_CHANNELS = frozenset(
    {"discord", "telegram", "whatsapp", "slack", "signal", "email", "teams"}
)


def _origin_from_env() -> "dict | None":
    """Derive a messenger origin from the per-turn engine env so a detached
    compute run can notify the user on completion. The adapter sets
    CORVIN_CHANNEL_ID='<channel>:<chat_id>' on the engine spawn; MCP tool
    handlers (which drive this client) inherit it. Returns None for non-messenger
    origins (e.g. console 'web:sid') → compute stays poll-only there."""
    raw = os.environ.get("CORVIN_CHANNEL_ID", "")
    if ":" not in raw:
        return None
    channel, chat_id = raw.split(":", 1)
    if channel not in _MESSENGER_CHANNELS or not chat_id:
        return None
    sender = os.environ.get("CORVIN_ORIGIN_SENDER", "").strip() or chat_id
    return {"channel": channel, "chat_id": chat_id, "sender": sender}


class WorkerClientError(RuntimeError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(f"{error_class}: {message}")
        self.error_class = error_class
        self.message = message


class WorkerClient:
    def __init__(self, socket_path: Path, *, timeout_s: float = 30.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout_s = timeout_s

    def _call(self, op: str, params: Mapping[str, Any] | None = None) -> dict:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_s)
        try:
            sock.connect(str(self.socket_path))
            send_frame_sync(sock, {"op": op, "params": dict(params or {})})
            response = recv_frame_sync(sock)
        finally:
            try:
                sock.close()
            except OSError:
                pass
        if not isinstance(response, dict):
            raise TransportError(f"unexpected response type: {type(response).__name__}")
        if response.get("ok"):
            return response.get("result", {})
        raise WorkerClientError(
            error_class=str(response.get("error_class", "UnknownError")),
            message=str(response.get("error", "")),
        )

    # -- convenience wrappers ---------------------------------------------------

    def ping(self) -> dict:
        return self._call("ping")

    def submit_run(self, **params: Any) -> dict:
        # Auto-attach the messenger origin from the per-turn env so a detached
        # compute run notifies on completion — unless the caller set it
        # explicitly (or opted out with notify=None/False).
        if "notify" not in params:
            origin = _origin_from_env()
            if origin:
                params["notify"] = origin
        elif not params.get("notify"):
            params.pop("notify", None)  # explicit opt-out
        return self._call("submit_run", params)

    def get_status(self, compute_handle: str) -> dict:
        return self._call("get_status", {"compute_handle": compute_handle})

    def get_result(self, compute_handle: str, *, wait_s: float = 0.0) -> dict:
        return self._call("get_result",
                          {"compute_handle": compute_handle, "wait_s": wait_s})

    def abort_run(self, compute_handle: str) -> dict:
        return self._call("abort_run", {"compute_handle": compute_handle})

    def list_runs(self) -> dict:
        return self._call("list_runs")

    def gate_action(self, compute_handle: str, action_type: str,
                    payload: dict | None = None) -> dict:
        """ADR-0029 — send a GateAction to a pipeline or HAC job."""
        return self._call("gate_action", {
            "compute_handle": compute_handle,
            "action_type": action_type,
            "payload": payload or {},
        })

    def submit_engine_run(self, engine: str, budget: dict, extra: dict,
                          tenant_id: str | None = None) -> dict:
        """ADR-0029 unified submit for non-flat engines."""
        params: dict = {"engine": engine, "budget": budget, "extra": extra}
        if tenant_id:
            params["tenant_id"] = tenant_id
        return self._call("submit_run", params)


def is_socket_reachable(socket_path: Path, *, timeout_s: float = 0.1) -> bool:
    """Cheap probe — non-blocking connect with a tight timeout."""
    if not Path(socket_path).exists():
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect(str(socket_path))
        return True
    except (OSError, socket.timeout):
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass
