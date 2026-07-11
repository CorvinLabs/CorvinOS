"""ADR-0190 M4/M5/M6 — consolidated orchestration MCP server over stdio.

Transport: line-delimited JSON-RPC 2.0 on stdin/stdout (same shape as the
forge / skill-forge / corvin_delegate MCP servers — this file mirrors
``core/delegate/corvin_delegate/mcp_server.py``'s protocol skeleton).

Six tools across three previously chat-unreachable subsystems:

  - ``workflow_run`` / ``workflow_resume`` / ``workflow_list_paused``
    (AWP DAG-Workflows, ``core/workflows/corvin_workflows``)
  - ``a2a_send`` / ``a2a_list_endpoints``
    (instance-to-instance, ``operator/bridges/shared/remote_trigger_sender``)
  - ``acs_delegate``
    (Autonomous Compute Shell, ``operator/bridges/shared/acs_engine_adapter``)

Each group's external dependency is imported defensively (try/except at
module load) — a missing plugin (e.g. base install without the workflows
package) silently omits that group's tools from ``tools/list`` rather than
crashing the whole server, mirroring ``forge/mcp_server.py``'s established
pattern for the optional compute plugin.

Run via::

    python -m corvin_orchestration.mcp_server
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "corvin-orchestration"
SERVER_VERSION = "0.1.0"

# JSON-RPC error codes (subset — same values as forge/corvin_delegate)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_WID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# ---------------------------------------------------------------------------
# forge (tenant resolution + audit chain) — same package the other MCP
# servers already depend on via PYTHONPATH; optional so a caller with a
# broken PYTHONPATH degrades to "_default" tenant + no audit instead of
# crashing the whole server on import.
# ---------------------------------------------------------------------------
try:
    from forge.paths import corvin_home as _corvin_home  # type: ignore[import]
    from forge.security_events import write_event as _write_event  # type: ignore[import]
    from forge.tenants import current_tenant as _current_tenant  # type: ignore[import]
    from forge.tenants import tenant_home as _tenant_home  # type: ignore[import]
    _FORGE_AVAILABLE = True
except ImportError:
    _FORGE_AVAILABLE = False

    def _current_tenant(tenant_id: str | None = None) -> str:  # type: ignore[misc]
        return tenant_id or "_default"

    def _tenant_home(tenant_id: str | None = None, **_kw: Any) -> Path:  # type: ignore[misc]
        return Path.home() / ".corvin" / "tenants" / (tenant_id or "_default")


def _audit_sink_for(tenant_id: str, prefix: str) -> Callable[[dict], None] | None:
    """Best-effort audit sink writing to the forge hash chain. Returns None
    (not a no-op callable) when forge is unavailable so callers that treat
    ``audit_sink=None`` as "skip" don't pay a wasted call."""
    if not _FORGE_AVAILABLE:
        return None
    try:
        audit_path = _corvin_home() / "tenants" / tenant_id / "audit.jsonl"
    except Exception:  # noqa: BLE001
        return None

    def _sink(event: dict) -> None:
        try:
            ev = event.get("event", "event")
            _write_event(audit_path, f"{prefix}.{ev}", severity="INFO", details=event)
        except Exception:  # noqa: BLE001
            pass  # never let audit shaping abort a live run

    return _sink


def _clamp(value: Any, *, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _run_with_budget(fn: Callable[[], Any], *, budget_s: int) -> Any:
    """Run *fn* on a daemon thread, joined with a wall-clock timeout.

    ADR-0029/ADR-0190 watchdog pattern — DAGRunner.run() / resume_workflow()
    / run_acs_workflow() have no built-in wall-clock cutoff and can run
    indefinitely (subprocess spawns). Best-effort, detached: on timeout the
    thread is NOT killed (Python has no safe thread-kill primitive) — it
    keeps running in the background and this call returns a typed timeout
    envelope rather than blocking the MCP server forever.
    """
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=budget_s)
    if t.is_alive():
        return {
            "status": "timeout",
            "error": (
                f"exceeded budget_s={budget_s}s — the underlying run may "
                "still be executing in the background; it was not killed."
            ),
        }
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ---------------------------------------------------------------------------
# Group 1 — AWP DAG-Workflows (ADR-0190 M5)
# ---------------------------------------------------------------------------
try:
    from corvin_workflows import (  # type: ignore[import]
        DAGRunner as _DAGRunner,
    )
    from corvin_workflows import (
        WorkflowInvalid as _WorkflowInvalid,
    )
    from corvin_workflows import checkpoint as _awp_checkpoint  # type: ignore[import]
    from corvin_workflows import (
        load_workflow as _load_workflow,
    )
    from corvin_workflows import (
        resume_workflow as _resume_workflow,
    )
    from corvin_workflows import (
        validate as _validate_workflow,
    )
    from corvin_workflows.engines_claude import (  # type: ignore[import]
        ClaudeCliEngine as _ClaudeCliEngine,
    )
    from corvin_workflows.engines_claude import (
        ClaudeEngineError as _ClaudeEngineError,
    )
    from corvin_workflows.runner import (
        UnauthorizedReplier as _UnauthorizedReplier,  # type: ignore[import]
    )
    _AWP_AVAILABLE = True
except ImportError:
    _AWP_AVAILABLE = False

_WORKFLOW_RUN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "workflow_id": {
            "type": "string",
            "description": (
                "The workflow's id (matches [a-z][a-z0-9_-]{0,63}) — same id "
                "space as the console's Workflows editor. Resolves to "
                "<tenant>/workflows/<workflow_id>.awp.yaml."
            ),
        },
        "inputs": {
            "type": "object",
            "description": "Key-value inputs merged into the workflow's initial state.",
        },
        "tenant_id": {"type": ["string", "null"]},
        "budget_s": {
            "type": "integer",
            "minimum": 10, "maximum": 600,
            "description": "Wall-clock budget in seconds. Clamped [10,600], default 120.",
        },
    },
    "required": ["workflow_id"],
    "additionalProperties": False,
}

_WORKFLOW_RESUME_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string", "description": "The paused run's id (from workflow_list_paused)."},
        "reply": {"type": "string", "description": "The human reply to inject at the paused ask_human node."},
        "tenant_id": {"type": ["string", "null"]},
        "budget_s": {
            "type": "integer", "minimum": 10, "maximum": 600,
            "description": "Wall-clock budget in seconds. Clamped [10,600], default 120.",
        },
    },
    "required": ["run_id", "reply"],
    "additionalProperties": False,
}

_WORKFLOW_LIST_PAUSED_SCHEMA: dict = {
    "type": "object",
    "properties": {"tenant_id": {"type": ["string", "null"]}},
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Group 2 — A2A instance-to-instance send (ADR-0190 M4)
# ---------------------------------------------------------------------------
try:
    from remote_trigger_sender import (  # type: ignore[import]
        EndpointError as _EndpointError,
    )
    from remote_trigger_sender import (
        RemoteEndpointRegistry as _RemoteEndpointRegistry,
    )
    from remote_trigger_sender import (
        RemoteTriggerSender as _RemoteTriggerSender,
    )
    _A2A_AVAILABLE = True
except ImportError:
    _A2A_AVAILABLE = False

_A2A_SEND_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "endpoint_id": {
            "type": "string",
            "description": "Target endpoint id — see a2a_list_endpoints for the configured set.",
        },
        "instruction": {
            "type": "string",
            "description": "The task instruction sent to the remote instance.",
        },
        "ttl_s": {
            "type": ["integer", "null"],
            "description": "Override the endpoint's default_ttl_s. Omit to use the endpoint default.",
        },
        "timeout_s": {
            "type": "integer", "minimum": 5, "maximum": 120,
            "description": "HTTP round-trip timeout. Clamped [5,120], default 30.",
        },
        "purpose_id": {
            "type": ["string", "null"],
            "description": "Optional ADR purpose/consent tag threaded into the signed envelope.",
        },
    },
    "required": ["endpoint_id", "instruction"],
    "additionalProperties": False,
}

_A2A_LIST_ENDPOINTS_SCHEMA: dict = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Group 3 — ACS delegation_loop (ADR-0190 M6)
# ---------------------------------------------------------------------------
try:
    from acs_engine_adapter import run_acs_workflow as _run_acs_workflow  # type: ignore[import]
    _ACS_AVAILABLE = True
except ImportError:
    _ACS_AVAILABLE = False

_ACS_DELEGATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "Natural-language task for the autonomous delegation_loop "
                "engine (ADR-0104) — a manager sub-agent plans and spawns "
                "worker sub-agents until the task converges or the budget "
                "is exhausted."
            ),
        },
        "tenant_id": {"type": ["string", "null"]},
        "dry_run": {
            "type": "boolean",
            "description": "Validate the spec and return without spending compute quota. Default false.",
        },
        "budget_override": {
            "type": ["object", "null"],
            "description": "Optional override of the delegation_loop budget dict (e.g. max_iterations).",
        },
        "budget_s": {
            "type": "integer", "minimum": 30, "maximum": 600,
            "description": "Wall-clock watchdog in seconds (separate from the compute budget). Clamped [30,600], default 180.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


def _tool_definitions() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if _AWP_AVAILABLE:
        tools.extend([
            {
                "name": "workflow_run",
                "description": (
                    "Run an AWP DAG-workflow (ADR-0090/ADR-0188) end to end via "
                    "the real engine — code/merge/route/ask_human nodes all "
                    "supported, unlike the console's separate chat-mode "
                    "executor. May pause and return paused_at_node/paused_prompt "
                    "if the workflow hits an ask_human node; resume via "
                    "workflow_resume. NOTE: concurrent-run quota "
                    "(workflows_concurrent) is enforced on the console's own "
                    "run-tracking today but NOT YET unified with this chat "
                    "path (ADR-0190 known limitation) — only a wall-clock "
                    "watchdog (budget_s) bounds this call."
                ),
                "inputSchema": _WORKFLOW_RUN_SCHEMA,
            },
            {
                "name": "workflow_resume",
                "description": (
                    "Resume a paused AWP workflow run (from workflow_list_paused) "
                    "by injecting a human reply at its ask_human node."
                ),
                "inputSchema": _WORKFLOW_RESUME_SCHEMA,
            },
            {
                "name": "workflow_list_paused",
                "description": "List paused AWP workflow runs awaiting a human reply for this tenant.",
                "inputSchema": _WORKFLOW_LIST_PAUSED_SCHEMA,
            },
        ])
    if _A2A_AVAILABLE:
        tools.extend([
            {
                "name": "a2a_send",
                "description": (
                    "Send a signed task instruction to a paired CorvinOS "
                    "instance (ADR-0103/ADR-0116/ADR-0038 A2A protocol). "
                    "Pairing must already be configured (console-managed); "
                    "this only sends. Never raises on remote/transport "
                    "failure — check the response's ok/status fields."
                ),
                "inputSchema": _A2A_SEND_SCHEMA,
            },
            {
                "name": "a2a_list_endpoints",
                "description": "List configured A2A endpoint ids and labels for this instance.",
                "inputSchema": _A2A_LIST_ENDPOINTS_SCHEMA,
            },
        ])
    if _ACS_AVAILABLE:
        tools.append({
            "name": "acs_delegate",
            "description": (
                "Delegate a task to the Autonomous Compute Shell (ADR-0104 "
                "delegation_loop engine) — an autonomous manager/worker "
                "sub-agent loop, out of this turn's context. Spends compute "
                "quota (charge_quota=true) unless dry_run=true. Distinct "
                "from workflow_run: this is an open-ended autonomous loop, "
                "not a fixed DAG."
            ),
            "inputSchema": _ACS_DELEGATE_SCHEMA,
        })
    return tools


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class OrchestrationServer:
    def __init__(self, *, stdin=None, stdout=None, stderr=None) -> None:
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._stdout_lock = threading.Lock()
        self._initialized = False
        self._shutting_down = False
        self.caller_persona = (os.environ.get("CORVIN_CALLER_PERSONA") or "").strip()

    # -- transport -----------------------------------------------------

    def _send(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        with self._stdout_lock:
            self._stdout.write(line)
            self._stdout.flush()

    def _respond(self, msgid: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": msgid, "result": result})

    def _error(self, msgid: Any, code: int, message: str, data: Any = None) -> None:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._send({"jsonrpc": "2.0", "id": msgid, "error": err})

    def _log(self, *args: Any) -> None:
        print(*args, file=self._stderr, flush=True)

    def _text_result(self, payload: dict, *, is_error: bool = False) -> dict:
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "isError": is_error,
        }

    # -- main loop -------------------------------------------------------

    def serve(self) -> int:
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
            except Exception as e:  # noqa: BLE001
                self._log("server: unhandled", repr(e))
                self._log(traceback.format_exc())
                msgid = msg.get("id") if isinstance(msg, dict) else None
                self._error(msgid, INTERNAL_ERROR, f"internal error: {e}")
            if self._shutting_down:
                break
        return 0

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
            self._respond(msgid, {"tools": _tool_definitions()})
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
            self._error(msgid, METHOD_NOT_FOUND, f"method not found: {method}")

    def _handle_initialize(self, msgid: Any, params: dict) -> None:
        client_info = params.get("clientInfo", {})
        self._log(f"initialize from {client_info.get('name', '?')}")
        self._respond(msgid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    def _handle_tools_call(self, msgid: Any, params: dict) -> None:
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            self._error(msgid, INVALID_PARAMS, "arguments must be an object")
            return

        handlers: dict[str, Callable[[Any, dict], None]] = {
            "workflow_run": self._call_workflow_run,
            "workflow_resume": self._call_workflow_resume,
            "workflow_list_paused": self._call_workflow_list_paused,
            "a2a_send": self._call_a2a_send,
            "a2a_list_endpoints": self._call_a2a_list_endpoints,
            "acs_delegate": self._call_acs_delegate,
        }
        handler = handlers.get(name)
        if handler is None:
            self._error(msgid, METHOD_NOT_FOUND, f"unknown tool: {name!r}")
            return
        handler(msgid, args)

    # -- Group 1: AWP workflows -----------------------------------------

    def _call_workflow_run(self, msgid: Any, args: dict) -> None:
        if not _AWP_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "AWP workflow engine not installed (core/workflows/)")
            return
        workflow_id = str(args.get("workflow_id") or "")
        if not _WID_RE.match(workflow_id):
            self._error(msgid, INVALID_PARAMS, "workflow_id must match [a-z][a-z0-9_-]{0,63}")
            return
        tenant_id = _current_tenant(args.get("tenant_id"))
        yaml_path = _tenant_home(tenant_id) / "workflows" / f"{workflow_id}.awp.yaml"
        if not yaml_path.exists():
            self._error(msgid, INVALID_PARAMS, f"workflow {workflow_id!r} not found for tenant {tenant_id!r}")
            return

        try:
            doc = _load_workflow(str(yaml_path))
            _validate_workflow(doc)
        except _WorkflowInvalid as exc:
            self._error(msgid, INVALID_PARAMS, f"[{exc.code}] {exc.message}")
            return
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad, not just (OSError, ValueError): malformed
            # YAML raises yaml.YAMLError (ScannerError/ParserError/...),
            # which is not a subclass of either — a corrupted workflow file
            # must surface as a clean "invalid workflow" INVALID_PARAMS
            # response, not an opaque INTERNAL_ERROR from the top-level
            # unhandled-exception handler.
            self._error(msgid, INVALID_PARAMS, f"invalid workflow: {exc}")
            return

        try:
            engine = _ClaudeCliEngine()
        except _ClaudeEngineError as exc:
            self._respond(msgid, self._text_result(
                {"status": "engine_unavailable", "error": str(exc)}, is_error=True,
            ))
            return

        budget_s = _clamp(args.get("budget_s"), lo=10, hi=600, default=120)
        sink = _audit_sink_for(tenant_id, "workflow")
        runner = _DAGRunner(doc, engine=engine, audit_sink=sink, tenant_id=tenant_id)
        try:
            result = _run_with_budget(
                lambda: runner.run(inputs=args.get("inputs") or {}), budget_s=budget_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR, f"workflow run failed: {exc}")
            return
        self._respond(msgid, self._run_result_envelope(result))

    def _call_workflow_resume(self, msgid: Any, args: dict) -> None:
        if not _AWP_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "AWP workflow engine not installed (core/workflows/)")
            return
        run_id = str(args.get("run_id") or "")
        reply = str(args.get("reply") or "")
        if not run_id or not reply:
            self._error(msgid, INVALID_PARAMS, "run_id and reply are required")
            return
        tenant_id = _current_tenant(args.get("tenant_id"))
        budget_s = _clamp(args.get("budget_s"), lo=10, hi=600, default=120)
        sink = _audit_sink_for(tenant_id, "workflow")

        try:
            result = _run_with_budget(
                lambda: _resume_workflow(
                    run_id, reply,
                    engine=_ClaudeCliEngine(),
                    tenant_id=tenant_id,
                    # Chat-tool caller is the persona's own privileged turn —
                    # same posture as the console's operator caller
                    # (replier=None is always authorized; the per-approver
                    # WF-A3 binding is for the bridge's specific-participant
                    # reply path, not this MCP tool).
                    replier=None,
                    audit_sink=sink,
                ),
                budget_s=budget_s,
            )
        except _ClaudeEngineError as exc:
            self._respond(msgid, self._text_result(
                {"status": "engine_unavailable", "error": str(exc)}, is_error=True,
            ))
            return
        except KeyError as exc:
            self._error(msgid, INVALID_PARAMS, f"no paused run found: {exc}")
            return
        except _awp_checkpoint.AlreadyClaimedError as exc:
            self._respond(msgid, self._text_result(
                {"status": "already_resuming", "error": str(exc)}, is_error=True,
            ))
            return
        except _UnauthorizedReplier as exc:
            self._error(msgid, INVALID_PARAMS, f"unauthorized reply: {exc}")
            return
        except RuntimeError as exc:
            self._error(msgid, INTERNAL_ERROR, f"resume failed: {exc}")
            return
        self._respond(msgid, self._run_result_envelope(result))

    def _run_result_envelope(self, result: Any) -> dict:
        if isinstance(result, dict):  # _run_with_budget timeout sentinel
            return self._text_result(result, is_error=True)
        envelope = {
            "status": result.state,  # "complete" | "failed" | "paused"
            "run_id": result.run_id,
            "error": result.error,
            "paused_at_node": result.paused_at_node,
            "paused_prompt": result.paused_prompt,
            "final_state": result.final_state,
            "node_count": len(result.nodes),
        }
        return self._text_result(envelope, is_error=(result.state == "failed"))

    def _call_workflow_list_paused(self, msgid: Any, args: dict) -> None:
        if not _AWP_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "AWP workflow engine not installed (core/workflows/)")
            return
        tenant_id = _current_tenant(args.get("tenant_id"))
        try:
            paused = _awp_checkpoint.list_paused(tenant_id=tenant_id)
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR, f"list_paused failed: {exc}")
            return
        self._respond(msgid, self._text_result({"paused": paused}))

    # -- Group 2: A2A ------------------------------------------------------

    def _call_a2a_send(self, msgid: Any, args: dict) -> None:
        if not _A2A_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "A2A sender not installed (operator/bridges/shared/)")
            return
        endpoint_id = str(args.get("endpoint_id") or "")
        instruction = str(args.get("instruction") or "")
        if not endpoint_id or not instruction:
            self._error(msgid, INVALID_PARAMS, "endpoint_id and instruction are required")
            return
        timeout_s = _clamp(args.get("timeout_s"), lo=5, hi=120, default=30)
        ttl_s = args.get("ttl_s")
        purpose_id = args.get("purpose_id")

        sender = _RemoteTriggerSender()
        try:
            result = _run_with_budget(
                lambda: sender.send(
                    endpoint_id, instruction,
                    ttl_s=ttl_s, timeout_s=timeout_s, purpose_id=purpose_id,
                ),
                budget_s=timeout_s + 15,
            )
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR, f"a2a_send failed: {exc}")
            return
        if isinstance(result, dict):  # _run_with_budget timeout sentinel
            self._respond(msgid, self._text_result(result, is_error=True))
            return
        envelope = {
            "ok": result.ok,
            "status": result.status,
            "task_id": result.task_id,
            "instance_id": result.instance_id,
            "instance_id_match": result.instance_id_match,
            "data": result.data,
            "duration_ms": result.duration_ms,
        }
        self._respond(msgid, self._text_result(envelope, is_error=not result.ok))

    def _call_a2a_list_endpoints(self, msgid: Any, args: dict) -> None:
        if not _A2A_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "A2A sender not installed (operator/bridges/shared/)")
            return
        registry = _RemoteEndpointRegistry()
        out: list[dict] = []
        for eid in registry.list_ids():
            try:
                cfg = registry.load(eid)
                out.append({"endpoint_id": eid, "label": cfg.get("label", ""), "enabled": True})
            except _EndpointError as exc:
                out.append({"endpoint_id": eid, "enabled": False, "error": str(exc)})
        self._respond(msgid, self._text_result({"endpoints": out}))

    # -- Group 3: ACS --------------------------------------------------

    def _call_acs_delegate(self, msgid: Any, args: dict) -> None:
        if not _ACS_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "ACS engine not installed (operator/bridges/shared/)")
            return
        task = str(args.get("task") or "")
        if not task:
            self._error(msgid, INVALID_PARAMS, "task is required")
            return
        tenant_id = _current_tenant(args.get("tenant_id"))
        dry_run = bool(args.get("dry_run", False))
        budget_override = args.get("budget_override")
        budget_s = _clamp(args.get("budget_s"), lo=30, hi=600, default=180)

        # R31: delegation_loop.budget, if present at all, must carry max_depth
        # — so an absent/empty budget_override must omit the key entirely
        # (letting the engine apply its own defaults) rather than send an
        # empty {} that trips validation. budget_override is passed to
        # run_acs_workflow() as its own kwarg below (which merges it over
        # spec.orchestration.delegation_loop.budget) — NOT embedded into the
        # spec here too, so there is exactly one source of truth for it.
        spec = {
            "awp": "1.0.0",
            "workflow": {
                "name": f"chat-delegated-{int(time.time())}",
                "description": task,
                "version": "1.0.0",
            },
            "orchestration": {
                "engine": "delegation_loop",
                "delegation_loop": {},
            },
            "state": {"initial": {"task": task}},
        }

        try:
            result = _run_with_budget(
                lambda: _run_acs_workflow(
                    spec, tenant_id=tenant_id, dry_run=dry_run,
                    budget_override=budget_override,
                ),
                budget_s=budget_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR, f"acs_delegate failed: {exc}")
            return
        is_error = isinstance(result, dict) and result.get("status") in ("timeout", "failed")
        self._respond(msgid, self._text_result(result, is_error=is_error))


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    server = OrchestrationServer()
    return server.serve()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
