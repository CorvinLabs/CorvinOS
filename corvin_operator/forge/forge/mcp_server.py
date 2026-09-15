"""Minimal MCP server exposing the forge to Claude Code over stdio.

Transport: line-delimited JSON-RPC 2.0 on stdin/stdout (per MCP spec).

Surface:
  - tool ``forge_tool`` — register a new tool at runtime
  - tool ``forge_promote`` — promote a forged tool to a Skill folder
  - all forged tools, listed alongside ``forge_tool`` after each forge

After every successful forge / promote / delete, the server emits
``notifications/tools/list_changed`` so the client (Claude Code) refreshes
its tool list and can immediately call the freshly forged tool.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

# ── debug logging bootstrap ────────────────────────────────────────────
# Best-effort: pull in the Corvin rotating-file logger if the bridge
# tree is on PYTHONPATH (it is during normal voice/cowork startup). Forge
# itself must not crash if the helper is unavailable — many CI / unit
# environments instantiate the MCP server directly.
_corvin_log = None
try:
    _bridge_shared = (
        Path(__file__).resolve().parents[2]
        / "bridges" / "shared"
    )
    if _bridge_shared.is_dir() and str(_bridge_shared) not in sys.path:
        sys.path.insert(0, str(_bridge_shared))
    from debug_logging import get_logger as _corvin_get_logger  # type: ignore
    _corvin_log = _corvin_get_logger("forge.mcp_server")
except Exception:
    _corvin_log = None

_uah_write = None
try:
    from activity_writer import write_chat_activity as _uah_write  # type: ignore
except Exception:
    _uah_write = None

from .breakers import BreakerRegistry
from .multi_registry import MultiRegistry
from .permissions import Mode
from .policy import Policy
from .registry import Registry, ToolSpec
from .runner import (
    PermissionDenied,
    SchemaError,
    TamperError,
    ToolError,
    run_tool,
)
from .security_events import write_event as _write_security_event
from .static_check import StaticCheckError, assert_imports_ok

# ADR-0069 M1 — Tool Execution Broker for non-CC engines.
# Imported lazily-safe: if the teb package is somehow absent the forge
# server degrades gracefully (TEB hooks skip silently).
try:
    import sys as _sys
    import pathlib as _pathlib
    _teb_shared = str(_pathlib.Path(__file__).resolve().parents[3] / "bridges" / "shared")
    if _teb_shared not in _sys.path:
        _sys.path.insert(0, _teb_shared)
    from teb.broker import ToolExecutionBroker as _ToolExecutionBroker
    from teb.path_gate_hook import path_gate_pre_hook as _path_gate_pre_hook
    _TEB_AVAILABLE = True
except Exception:
    _ToolExecutionBroker = None  # type: ignore[assignment,misc]
    _path_gate_pre_hook = None   # type: ignore[assignment]
    _TEB_AVAILABLE = False

# ADR-0012 — large-data snapshot layer. Imported lazily-safe; if the
# corvin_data package is somehow missing the imports raise, but the
# package ships with forge so this is a hard dep at install time.
from .corvin_data import (
    DATA_REGISTER_SCHEMA,
    DATA_SNAPSHOT_SCHEMA,
    DATA_UNREGISTER_SCHEMA,
    DataRegistry as _DataRegistry,
    ToolError as _DataToolError,
    call_data_register as _call_data_register,
    call_data_snapshot as _call_data_snapshot,
    call_data_unregister as _call_data_unregister,
)

# ADR-0013 — compute-worker plugin. Worker socket discovery + MCP
# routing live here; the actual worker is an out-of-process plugin
# under core/compute/.
from ._compute_discovery import (
    is_worker_reachable as _compute_worker_reachable,
    socket_path_for as _compute_socket_path_for,
)
_COMPUTE_TOOL_DEFS: list[dict] | None = None
_COMPUTE_TOOL_NAMES: frozenset[str] = frozenset()
_COMPUTE_ENGINE_TOOL_DEFS: list[dict] | None = None
_COMPUTE_ENGINE_TOOLS_NAMES: frozenset[str] = frozenset()
try:
    # Lazy resolution — the plugin tree exposes `corvin_compute` only
    # when bootstrapped. We add its parent dir to sys.path so the
    # MCP bridge module is reachable even when the compute venv exists
    # alongside but is not on the runtime PYTHONPATH.
    import sys as _sys
    _compute_plugin = (Path(__file__).resolve().parents[3] / "core" / "compute")
    if _compute_plugin.is_dir() and str(_compute_plugin) not in _sys.path:
        _sys.path.append(str(_compute_plugin))
    from corvin_compute.mcp_bridge import (  # type: ignore[import]
        COMPUTE_TOOL_NAMES as _COMPUTE_TOOL_NAMES,
        compute_tool_definitions as _compute_tool_definitions,
        COMPUTE_ENGINE_TOOLS_NAMES as _COMPUTE_ENGINE_TOOLS_NAMES,
        compute_engine_tool_definitions as _compute_engine_tool_definitions,
    )
    from corvin_compute.client import (  # type: ignore[import]
        WorkerClient as _ComputeWorkerClient,
        WorkerClientError as _ComputeWorkerClientError,
    )
    # ADR-0190 M3 — General Availability datasource registration. Register()
    # is pure filesystem + audit_writer (no compute-worker socket needed),
    # so this import is independent of worker reachability.
    from corvin_compute.fabric.datasources.registry import (  # type: ignore[import]
        DataSourceRegistry as _DataSourceRegistry,
    )
    _COMPUTE_TOOL_DEFS = list(_compute_tool_definitions())
    # ADR-0029 — pipeline/HAC engines (compute_submit/compute_gate). Fully
    # coded in mcp_bridge.py since ADR-0029 but never imported here until
    # ADR-0190 M2 — this was dead code from the MCP server's perspective.
    _COMPUTE_ENGINE_TOOL_DEFS = list(_compute_engine_tool_definitions())
    # ADR-0017/ADR-0013 — license gate; imported here so the path extension
    # above makes corvin_compute reachable before the import.
    from corvin_compute.license_gate import (  # type: ignore[import]
        check_compute_access as _check_compute_access,
        enforce_trial_strategy as _enforce_trial_strategy,
        record_trial_iteration as _record_trial_iteration,
    )
    # ADR-0026 — Compute Fabric MCP bridge extension. Imported in the same
    # try block so a partially-installed compute plugin stays consistent.
    try:
        from corvin_compute.mcp_bridge import (  # type: ignore[import]
            FABRIC_TOOL_NAMES as _FABRIC_TOOL_NAMES,
            fabric_tool_definitions as _fabric_tool_definitions,
            FABRIC_NOT_ENABLED as _FABRIC_NOT_ENABLED,
        )
        _FABRIC_TOOL_DEFS: list[dict] | None = list(_fabric_tool_definitions())
    except ImportError:
        _FABRIC_TOOL_DEFS = None
        _FABRIC_TOOL_NAMES: frozenset[str] = frozenset()
        _FABRIC_NOT_ENABLED: dict = {"status": "error", "error": "FabricNotEnabled"}
except ImportError:
    _COMPUTE_TOOL_DEFS = None
    _COMPUTE_ENGINE_TOOL_DEFS = None
    _DataSourceRegistry = None  # type: ignore[assignment]
    _ComputeWorkerClient = None  # type: ignore[assignment]
    _ComputeWorkerClientError = None  # type: ignore[assignment]
    _check_compute_access = None  # type: ignore[assignment]
    _enforce_trial_strategy = None  # type: ignore[assignment]
    _record_trial_iteration = None  # type: ignore[assignment]
    _FABRIC_TOOL_DEFS = None
    _FABRIC_TOOL_NAMES = frozenset()
    _FABRIC_NOT_ENABLED = {"status": "error", "error": "FabricNotEnabled"}

# ADR-0190 M3 — General Availability datasource-adapter license gate. This is
# the EXACT same three-level defensive fallback chain as
# core/console/corvin_console/routes/data_sources.py's ``_lic_get_limit`` —
# both surfaces must resolve "datasource_adapters_allowed" identically, so
# any change to this chain must be mirrored there (and vice versa). Kept as
# a local copy rather than a cross-import because forge/mcp_server.py must
# not depend on the console webapp package (heavy FastAPI/DB import graph,
# wrong direction of coupling for a stdio MCP subprocess).
#
# Verification finding: an earlier version of this block imported
# license.validator without first putting operator/ on sys.path (unlike
# data_sources.py, which does `sys.path.insert(0, str(_OPERATOR))` before
# its own identical import) — the import silently failed every time and
# EVERY datasource_connect call fell through to the hardcoded free-tier
# fallback regardless of the tenant's real license tier. The path insertion
# below is required, not cosmetic.
_DS_FREE_TIER_FALLBACK: dict = {"datasource_adapters_allowed": ["local_file"]}
_DS_OPERATOR_ROOT = Path(__file__).resolve().parents[2]  # operator/
if _DS_OPERATOR_ROOT.is_dir() and str(_DS_OPERATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DS_OPERATOR_ROOT))
try:
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
except ImportError:
    try:
        from license.limits import FREE_TIER as _DS_FREE_TIER  # type: ignore[import]
        _lic_get_limit = _DS_FREE_TIER.get  # type: ignore[assignment]
    except ImportError:
        _lic_get_limit = _DS_FREE_TIER_FALLBACK.get  # type: ignore[assignment]

_DATASOURCE_CONNECT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "manifest": {
            "type": "object",
            "description": (
                "AdapterManifest dict for the new connection — same shape as "
                "the console's Data Sources > Register form. Must include at "
                "least 'adapter' (e.g. 'postgresql', 'mysql', 's3_parquet', "
                "'local_file', ...) and 'name'. Free tier: only "
                "adapter='local_file' is allowed; Member+ unlocks all 13 "
                "built-in adapters."
            ),
        },
        "tenant_id": {"type": ["string", "null"]},
    },
    "required": ["manifest"],
    "additionalProperties": False,
}


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "claude-tool-forge"
SERVER_VERSION = "0.1.0"

# JSON-RPC error codes (subset)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

FORGE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "description", "input_schema", "impl"],
    "properties": {
        "name": {
            "type": "string",
            "description": "Tool name (alphanumeric + underscore).",
        },
        "description": {
            "type": "string",
            "description": "One-line description of what the tool does.",
        },
        "input_schema": {
            "type": "object",
            "description": "JSONSchema describing the tool's input payload.",
        },
        "impl": {
            "type": "string",
            "description": (
                "Implementation source. The runtime reads a JSON payload on "
                "stdin and writes a JSON result to stdout. Mark string "
                "schema fields with x-bind: ro to expose paths read-only "
                "inside the bubblewrap sandbox."
            ),
        },
        "runtime": {
            "type": "string",
            "enum": ["python", "bash"],
            "default": "python",
        },
        "overwrite": {
            "type": "boolean",
            "default": False,
            "description": "Replace an existing tool with the same name.",
        },
        "meta": {
            "type": "object",
            "description": (
                "Optional metadata. Set deterministic=true to enable the "
                "result cache (identical inputs → cached envelope, no "
                "subprocess). Set side_effects=false to declare the tool "
                "doesn't mutate state outside its artifacts dir. "
                "Set requirements=[...] to list Python packages that must "
                "be available inside the sandbox (e.g. ['matplotlib>=3.6', "
                "'pandas']); they are installed once via pip --target and "
                "cached by content hash."
            ),
            "properties": {
                "deterministic": {"type": "boolean"},
                "side_effects":  {"type": "boolean"},
                "requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Python packages to install before running this tool. "
                        "Pip PEP 440 specifiers are supported "
                        "(e.g. 'matplotlib>=3.6', 'pandas==2.1.*'). "
                        "Packages are cached by content hash; subsequent "
                        "runs reuse the cache without re-running pip."
                    ),
                },
            },
        },
        "scope": {
            "type": "string",
            "enum": ["task", "session", "project", "user"],
            "description": (
                "Workspace scope. Defaults to detected scope "
                "(CORVIN_DEFAULT_SCOPE / CORVIN_CHANNEL_ID / "
                "git-repo / user; legacy CORVIN_* aliases honored)."
            ),
        },
    },
}

PROMOTE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string", "description": "Forged tool to promote."},
        "to": {
            "type": "string",
            "enum": ["session", "project", "user"],
            "description": "Target workspace scope to promote to.",
        },
    },
}

LIST_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "enum": ["task", "session", "project", "user"],
            "description": "Optional scope filter — leave unset to see all "
                           "visible tools across scopes.",
        },
    },
}


# ── Layer 33 — Session Artifact Memory ──────────────────────────────────────

ARTIFACT_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "after_ts": {"type": "number",
                     "description": "Only return artifacts newer than this "
                                    "epoch-seconds timestamp."},
        "mime": {"type": "string",
                 "description": "Filter by exact MIME type."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                  "default": 20},
        "scope": {"type": "string", "enum": ["session", "global", "all"],
                  "default": "session"},
    },
}

ARTIFACT_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string",
                  "description": "FTS5 query against the artifact_summary "
                                 "index. Supports phrase + boolean syntax."},
        "scope": {"type": "string", "enum": ["session", "global", "all"],
                  "default": "session"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
    },
    "required": ["query"],
}

ARTIFACT_GET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576,
                      "default": 65536},
        "encoding": {"type": "string", "enum": ["auto", "text", "base64"],
                     "default": "auto"},
    },
    "required": ["name"],
}

ARTIFACT_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "range": {"type": "string",
                  "description": "pages:N-M | lines:N-M | bytes:N-M | meta"},
    },
    "required": ["name", "range"],
}

ARTIFACT_REGISTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string",
                 "description": "Absolute path under <session>/artifacts/. "
                                "Paths outside that tree are refused."},
        "description": {"type": "string",
                        "description": "Optional override; if empty, "
                                       "Haiku generates one async."},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["path"],
}

ARTIFACT_PIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
    },
    "required": ["name"],
}


class MCPServer:
    def __init__(
        self,
        root: Path,
        *,
        permission_mode: Mode = "yes",
        stdin=None,
        stdout=None,
        stderr=None,
        allowed_forged_tools: list[str] | None = None,
    ):
        # Load policy first, then build the registry with the chosen
        # hash-chain mode so registry-level events match server-level events.
        # ``Policy.load`` doesn't need a fully-built Registry — only a path.
        policy = Policy.load(Path(root))
        self.registry = Registry(root, hash_chain=policy.audit_hash_chain)
        # ADR-0012 — per-workspace dataset handle registry.
        self.data_registry = _DataRegistry(self.registry.root)
        # MultiRegistry composes the four workspace scopes with shadowing
        # semantics for tools/list and tools/call lookup. The single-root
        # ``self.registry`` stays as the canonical sink for policy / breaker /
        # permission state — those layers all hang off one filesystem root.
        # Tools forged with an explicit ``scope`` go into the multi-registry
        # *and* (when ``scope`` resolves to the same root) into self.registry.
        self.multi = MultiRegistry(hash_chain=policy.audit_hash_chain)
        self.policy = policy
        self.breakers = BreakerRegistry(self.policy)
        self.permission_mode = permission_mode
        # Hot-reload: track policy.json's mtime so the operator can edit
        # the envelope mid-session and the next tools/call honours it.
        # This matches the voice repo's "everything reloads without restart"
        # convention. Tokens / ports are explicitly out of scope (they can't
        # change without restart anyway — but no policy field exists for them).
        self.forge_persona = os.environ.get("FORGE_PERSONA", "")
        # Layer 9 — caller-persona namespace gate. The bridge adapter exports
        # CORVIN_CALLER_PERSONA per turn so any persona can opt into the
        # forge MCP server, but each persona may only register tool names
        # within its own prefix (policy.persona_namespaces). Empty / missing
        # => wildcard (legacy behaviour).
        self.caller_persona = os.environ.get("CORVIN_CALLER_PERSONA") or ""
        self._policy_path = Path(root) / "policy.json"
        self._policy_mtime = self._current_policy_mtime()
        # Per-persona allowlist for forged tools. None = no restriction
        # (the persona sees every forged tool). A list of glob patterns
        # (e.g. ["csv.*", "stats.median"]) gates both tools/list visibility
        # and tools/call execution. forge_tool / forge_promote are always
        # allowed when the server is reachable at all — that's a forge-
        # persona thing, not a per-call gate.
        if allowed_forged_tools is None:
            allowed_forged_tools = self._parse_allowed_env(
                os.environ.get("FORGE_ALLOWED_TOOLS", "")
            )
        self.allowed_forged_tools: list[str] | None = allowed_forged_tools
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._stdout_lock = threading.Lock()
        self._initialized = False
        self._shutting_down = False

        # ADR-0069 M1 — Tool Execution Broker.
        # engine_id is set by the adapter via CORVIN_ENGINE_ID when spawning
        # the MCP server for a non-CC engine. Empty / missing = "claude_code"
        # (CC engines have their own native hook system; TEB is additive).
        self.engine_id: str = os.environ.get("CORVIN_ENGINE_ID", "claude_code")
        if _TEB_AVAILABLE and _ToolExecutionBroker is not None:
            # Executor is None at init; passed per-call via execute(executor=...)
            # so concurrent _call_forged() invocations never share mutable state.
            self._teb: "_ToolExecutionBroker | None" = _ToolExecutionBroker(
                tool_executor=lambda _n, _a: None,  # placeholder, never called
                pre_hooks=[_path_gate_pre_hook] if _path_gate_pre_hook else [],
            )
        else:
            self._teb = None

    # -- transport ---------------------------------------------------------

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

    def _notify(self, method: str, params: dict | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _log(self, *args: Any) -> None:
        # Stderr stays the primary channel because the MCP wire frames are
        # on stdout — printing there would corrupt the protocol. The
        # Corvin debug_logging helper additionally pushes the line into
        # the rotating file at <corvin_home>/logs/corvin.log so forge
        # traces sit alongside the bridge-adapter records.
        msg = " ".join(str(a) for a in args)
        print(msg, file=self._stderr, flush=True)
        if _corvin_log is not None:
            try:
                _corvin_log.debug(msg)
            except Exception:
                pass

    # -- main loop ---------------------------------------------------------

    def serve(self) -> int:
        for raw in self._stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                # Per JSON-RPC: parse errors get an error response with id=null.
                self._error(None, PARSE_ERROR, "parse error")
                continue
            try:
                self._dispatch(msg)
            except Exception as e:  # never let a handler crash the loop
                self._log("server: unhandled", repr(e))
                self._log(traceback.format_exc())
                msgid = msg.get("id") if isinstance(msg, dict) else None
                self._error(msgid, INTERNAL_ERROR, f"internal error: {e}")
            if self._shutting_down:
                break
        return 0

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self._error(msg.get("id"), INVALID_REQUEST, "invalid request")
            return
        method = msg.get("method")
        msgid = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no id → don't respond.
        is_notification = "id" not in msg

        if method == "initialize":
            self._handle_initialize(msgid, params)
        elif method == "notifications/initialized":
            self._initialized = True
        elif method == "tools/list":
            self._handle_tools_list(msgid)
        elif method == "tools/call":
            self._handle_tools_call(msgid, params)
        elif method == "shutdown":
            self._respond(msgid, None)
            self._shutting_down = True
        elif method == "ping":
            self._respond(msgid, {})
        elif is_notification:
            return  # unknown notifications are silently ignored
        else:
            self._error(msgid, METHOD_NOT_FOUND, f"method not found: {method}")

    # -- handlers ----------------------------------------------------------

    def _handle_initialize(self, msgid: Any, params: dict) -> None:
        client_info = params.get("clientInfo", {})
        self._log(f"initialize from {client_info.get('name', '?')}")
        self._respond(
            msgid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    def _handle_tools_list(self, msgid: Any) -> None:
        self._refresh_policy_if_changed()
        self._respond(msgid, {"tools": self._all_tools()})

    # -- policy hot-reload ------------------------------------------------

    def _current_policy_mtime(self) -> float:
        """0.0 means absent (or unreadable). Any positive value is an mtime."""
        try:
            return self._policy_path.stat().st_mtime
        except (OSError, AttributeError):
            return 0.0

    def _refresh_policy_if_changed(self) -> None:
        """Re-read policy.json on mtime drift, then refresh breaker thresholds.

        BreakerRegistry's per-tool state (consecutive_failures, opened_at,
        token bucket level) is preserved — only the *thresholds* are
        updated. That keeps an in-flight failure storm intact across a
        policy edit; the operator can tune sensitivity without resetting
        the breaker.
        """
        new_mtime = self._current_policy_mtime()
        if new_mtime == self._policy_mtime:
            return
        try:
            new_policy = Policy.load(self.registry.root)
        except Exception as e:  # malformed policy.json — keep old policy
            self._log_security_event(
                "policy.reload_failed",
                details={"error": str(e), "kept_old_policy": True},
            )
            return
        old_audit = self.policy.audit_hash_chain
        self.policy = new_policy
        self.breakers._policy = new_policy
        # propagate threshold changes to live breakers
        for cb in self.breakers._cbs.values():
            cb.failure_threshold = new_policy.circuit_breaker_failure_threshold
            cb.reset_timeout = new_policy.circuit_breaker_reset_timeout
            cb.half_open_max = new_policy.circuit_breaker_half_open_max
        for tool, rl in self.breakers._rls.items():
            new_cap = new_policy.rate_limit_for(tool)
            if rl.capacity != new_cap:
                rl.capacity = new_cap
                rl.tokens = min(rl.tokens, float(new_cap))
        # registry hash_chain mode is sticky for now (changing it mid-run
        # would split the chain). Surface it in the event for observability.
        self._policy_mtime = new_mtime
        self._log_security_event(
            "policy.reloaded",
            details={
                "mtime": new_mtime,
                "audit_hash_chain": old_audit,
                "audit_hash_chain_after": new_policy.audit_hash_chain,
            },
        )

    @staticmethod
    def _parse_allowed_env(raw: str) -> list[str] | None:
        """Parse FORGE_ALLOWED_TOOLS env: empty = no restriction, else
        comma/space separated glob patterns."""
        if not raw or not raw.strip():
            return None
        return [p.strip() for p in raw.replace(",", " ").split() if p.strip()]

    def _is_forged_tool_allowed(self, name: str) -> bool:
        """Per-persona ACL gate. Meta-tools (forge_tool, forge_promote,
        forge_list) are always allowed when the server is reachable;
        restriction only applies to forged tools."""
        if name in ("forge_tool", "forge_promote", "forge_list", "forge_exec"):
            return True
        if self.allowed_forged_tools is None:
            return True
        import fnmatch
        return any(
            fnmatch.fnmatch(name, glob)
            for glob in self.allowed_forged_tools
        )

    def _all_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = [
            {
                "name": "forge_tool",
                "description": (
                    "Register a new schema-bound tool at runtime. The newly "
                    "forged tool becomes callable on the next tools/list."
                ),
                "inputSchema": FORGE_TOOL_SCHEMA,
            },
            {
                "name": "forge_promote",
                "description": (
                    "Promote a forged tool to a durable Skill folder under "
                    ".forge/skills/<name>/."
                ),
                "inputSchema": PROMOTE_TOOL_SCHEMA,
            },
            {
                "name": "forge_list",
                "description": (
                    "List the forged tools currently available in the workspace. "
                    "Optional `scope` filter (task / session / project / user); "
                    "without it, returns all visible tools across scopes. Call "
                    "this BEFORE forge_tool to avoid creating duplicates of "
                    "tools that already exist in your scope."
                ),
                "inputSchema": LIST_TOOL_SCHEMA,
            },
            {
                "name": "forge_exec",
                "description": (
                    "Execute a registered forged tool by name in the bwrap "
                    "sandbox — works even if the tool was registered in the "
                    "same session turn (bypasses the tools/list_changed cycle). "
                    "Use this instead of Bash when you need to run LLM-generated "
                    "code safely: no network, read-only /usr, fresh /tmp. "
                    "Emits a code.exec_attempt audit event on every call."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Name of the forged tool to execute "
                                "(e.g. 'code.paint_dog')."
                            ),
                        },
                        "input": {
                            "type": "object",
                            "description": (
                                "Input payload matching the tool's input_schema."
                            ),
                        },
                    },
                    "required": ["name", "input"],
                },
            },
            # ADR-0012 — large-data snapshot layer (data-locality surface)
            {
                "name": "data_register",
                "description": (
                    "Register a large dataset by path. Returns a data_handle "
                    "plus a PII-redacted statistical snapshot (schema, "
                    "stats, sample). The raw bytes stay sandbox-side; the "
                    "LLM sees only the projection. Use the handle in "
                    "subsequent forged-tool inputs."
                ),
                "inputSchema": DATA_REGISTER_SCHEMA,
            },
            {
                "name": "data_snapshot",
                "description": (
                    "Re-generate the snapshot for an already-registered "
                    "data_handle with different options (more rows, "
                    "different sampling strategy, different stats)."
                ),
                "inputSchema": DATA_SNAPSHOT_SCHEMA,
            },
            {
                "name": "data_unregister",
                "description": (
                    "Drop a previously-registered data_handle. Idempotent."
                ),
                "inputSchema": DATA_UNREGISTER_SCHEMA,
            },
        ]
        # ADR-0013 — compute-worker plugin (opt-in). Append the four
        # compute_* tools only when the worker socket is reachable for
        # the current tenant. Failure to import the bridge module
        # silently omits the tools.
        _worker_up = False
        if _COMPUTE_TOOL_DEFS is not None:
            try:
                if _compute_worker_reachable(audit_emit=self._log_security_event):
                    _worker_up = True
                    tools.extend(_COMPUTE_TOOL_DEFS)
            except Exception:
                # Discovery failure must never crash tools/list.
                pass
        # ADR-0029/ADR-0190 M2 — pipeline/HAC engine tools (compute_submit,
        # compute_gate). General Availability, same worker-reachability
        # gate as the flat compute_* tools — no separate license/fabric
        # gate needed at list-time (that happens in _call_compute_tool).
        if _worker_up and _COMPUTE_ENGINE_TOOL_DEFS is not None:
            tools.extend(_COMPUTE_ENGINE_TOOL_DEFS)
        # ADR-0026 — Compute Fabric tools (opt-in per tenant).
        # Advertised only when: (a) worker is reachable AND (b) fabric_enabled=true
        # in the tenant config. Failure to read tenant config silently omits tools.
        if _worker_up and _FABRIC_TOOL_DEFS is not None:
            try:
                if self._is_fabric_enabled():
                    tools.extend(_FABRIC_TOOL_DEFS)
            except Exception:
                pass
        # ADR-0190 M3 — General Availability datasource_connect. Unlike the
        # Fabric datasource_* tools above (Enterprise-only, routed through the
        # worker socket), this calls DataSourceRegistry.register() in-process
        # and needs no worker — advertised whenever the compute plugin (which
        # ships DataSourceRegistry) is importable at all.
        if _DataSourceRegistry is not None:
            tools.append({
                "name": "datasource_connect",
                "description": (
                    "Register a typed database/warehouse connection (Postgres, "
                    "MySQL, Snowflake, BigQuery, S3, ...) for later agentic-"
                    "compute jobs. General Availability — gated by your license "
                    "tier's adapter allowlist (Free tier: local_file only)."
                ),
                "inputSchema": _DATASOURCE_CONNECT_SCHEMA,
            })
        # Union with shadowing across all four workspace scopes plus the
        # legacy single-root registry. Higher scope (task > session >
        # project > user) wins; the single root counts as fallback after
        # all four scopes (so an explicit per-scope tool always shadows
        # an unscoped legacy tool with the same name).
        seen: set[str] = set()
        for ws_scope, spec in self.multi.list_with_scope():
            if spec.name in seen:
                continue
            if not self._is_forged_tool_allowed(spec.name):
                continue
            seen.add(spec.name)
            tools.append(self._spec_to_tool(spec, ws_scope=ws_scope))
        for spec in self.registry.list():
            if spec.name in seen:
                continue
            if not self._is_forged_tool_allowed(spec.name):
                continue
            seen.add(spec.name)
            tools.append(self._spec_to_tool(spec))
        # ADR-0116 M2 — Worker Audit Gateway.
        # Workers use this to emit events into the parent chain without direct
        # filesystem access to audit.jsonl. Server validates event_type against
        # EVENT_SEVERITY allowlist and strips forbidden keys from details.
        tools.append({
            "name": "audit.write_event",
            "description": (
                "Write a structured event to the parent audit chain. "
                "Only allowlisted event types (registered in security_events.py) "
                "are accepted. Forbidden detail keys (prompt text, tool inputs, "
                "tool outputs) are silently stripped before write. "
                "Use this from delegated worker turns to keep the parent chain "
                "complete (ADR-0116 M2)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "description": "Registered event type, e.g. 'worker.event_relayed'.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["INFO", "WARNING", "ERROR", "CRITICAL"],
                        "description": "Override severity (defaults to registered value).",
                    },
                    "details": {
                        "type": "object",
                        "description": "Metadata-only fields. Forbidden keys are stripped.",
                    },
                    "delegation_id": {
                        "type": "string",
                        "description": "delegation_id from the parent turn (ADR-0116 M1).",
                    },
                },
                "required": ["event_type"],
            },
        })
        # Layer 33 — Session Artifact Memory tools (always advertised when
        # MCP server is reachable; per-persona ACLs apply via the same
        # mechanism as the meta-tools).
        tools.extend([
            {
                "name": "artifact_list",
                "description": (
                    "List artifacts (PDFs, images, exports) that earlier "
                    "tasks generated in this session. Metadata only — "
                    "use artifact_get to fetch content."
                ),
                "inputSchema": ARTIFACT_LIST_SCHEMA,
            },
            {
                "name": "artifact_search",
                "description": (
                    "Full-text search over artifact descriptions (FTS5). "
                    "Returns name + snippet for each hit."
                ),
                "inputSchema": ARTIFACT_SEARCH_SCHEMA,
            },
            {
                "name": "artifact_get",
                "description": (
                    "Fetch an artifact's content by name. Text-decoded for "
                    "text MIME types, base64-encoded otherwise. Capped at "
                    "max_bytes (default 64 KB) — larger artifacts return a "
                    "size hint and a pointer to artifact_extract."
                ),
                "inputSchema": ARTIFACT_GET_SCHEMA,
            },
            {
                "name": "artifact_extract",
                "description": (
                    "Extract a slice of an artifact: pages:N-M (PDFs), "
                    "lines:N-M (text), bytes:N-M (raw), or meta "
                    "(EXIF / PDF metadata only)."
                ),
                "inputSchema": ARTIFACT_EXTRACT_SCHEMA,
            },
            {
                "name": "artifact_register",
                "description": (
                    "Manually register a file under <session>/artifacts/ "
                    "as an artifact. Most artifacts auto-register on "
                    "PostToolUse; use this only when the auto-register "
                    "decision tree doesn't catch your file."
                ),
                "inputSchema": ARTIFACT_REGISTER_SCHEMA,
            },
            {
                "name": "artifact_pin",
                "description": (
                    "Promote a session artifact to project scope so it "
                    "survives session reset (/new /clear /reset). The "
                    "pinned copy lives under <global>/artifacts/."
                ),
                "inputSchema": ARTIFACT_PIN_SCHEMA,
            },
        ])
        return tools

    @staticmethod
    def _spec_to_tool(
        spec: ToolSpec, *, ws_scope: str | None = None
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.input_schema,
        }
        if ws_scope is not None:
            out["_scope"] = ws_scope
        return out

    def _handle_tools_call(self, msgid: Any, params: dict) -> None:
        self._refresh_policy_if_changed()
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str):
            self._error(msgid, INVALID_PARAMS, "missing tool name")
            return
        if _corvin_log is not None:
            try:
                _corvin_log.debug(
                    "tools/call name=%s argc=%d keys=%s",
                    name,
                    len(args) if isinstance(args, dict) else -1,
                    list(args.keys())[:8] if isinstance(args, dict) else [],
                )
            except Exception:
                pass
        if name == "forge_tool":
            self._call_forge_tool(msgid, args)
            return
        if name == "forge_promote":
            self._call_forge_promote(msgid, args)
            return
        if name == "forge_list":
            self._call_forge_list(msgid, args)
            return
        if name == "forge_exec":
            self._call_forge_exec(msgid, args)
            return
        # ADR-0012 — data-locality MCP surface
        if name == "data_register":
            self._call_data_tool(msgid, _call_data_register, args)
            return
        if name == "data_snapshot":
            self._call_data_tool(msgid, _call_data_snapshot, args)
            return
        if name == "data_unregister":
            self._call_data_tool(msgid, _call_data_unregister, args)
            return
        # ADR-0013 — route compute_* tools to the worker (if reachable).
        # ADR-0029/ADR-0190 M2 — compute_submit/compute_gate (pipeline/HAC
        # engines) share the exact same worker-routing + license/quota gate
        # path as the flat compute_* tools.
        if name in _COMPUTE_TOOL_NAMES or name in _COMPUTE_ENGINE_TOOLS_NAMES:
            self._call_compute_tool(msgid, name, args)
            return
        # ADR-0026 — route Fabric tools; check fabric_enabled gate first.
        if name in _FABRIC_TOOL_NAMES:
            self._call_fabric_tool(msgid, name, args)
            return
        # ADR-0190 M3 — General Availability datasource registration.
        if name == "datasource_connect":
            self._call_datasource_connect(msgid, args)
            return
        # ADR-0116 M2 — Worker Audit Gateway
        if name == "audit.write_event":
            self._call_audit_write_event(msgid, args)
            return
        # ADR-0040 — Layer 33 Session Artifact Memory
        if name == "artifact_list":
            self._call_artifact_list(msgid, args); return
        if name == "artifact_search":
            self._call_artifact_search(msgid, args); return
        if name == "artifact_get":
            self._call_artifact_get(msgid, args); return
        if name == "artifact_extract":
            self._call_artifact_extract(msgid, args); return
        if name == "artifact_register":
            self._call_artifact_register(msgid, args); return
        if name == "artifact_pin":
            self._call_artifact_pin(msgid, args); return
        self._call_forged(msgid, name, args)

    # -- ADR-0013 compute-worker MCP handler ------------------------------

    def _call_compute_tool(self, msgid: Any, name: str, args: dict) -> None:
        """Route compute_* tool calls to the worker over its Unix socket.

        Fail-loud: a call to a compute_* tool when the worker is
        unreachable returns a typed error to the LLM. Discovery-side
        omission (the worker is silently dropped from tools/list when
        the socket is absent) is the path for "operator hasn't started
        the worker yet"; this branch covers the race where the socket
        was up at list-time but went away before the call.

        License gate (ADR-0017 Phase III): compute_run is gated before
        submission. Status / result / abort are never blocked — stranding
        an in-flight run on license change would be hostile to operators.
        """
        if _ComputeWorkerClient is None:
            self._error(
                msgid, METHOD_NOT_FOUND,
                "compute plugin not installed (core/compute/)",
            )
            return

        # ── License gate — compute_run + compute_submit (both spend compute
        # quota; compute_gate/status/result/abort act on an existing run and
        # stay ungated here, matching the pre-existing compute_run-only scope
        # for those) ──────────────────────────────────────────────────────
        _access = None
        if name in ("compute_run", "compute_submit") and _check_compute_access is not None:
            try:
                from forge import paths as _forge_paths  # local import; forge is always on path
                _home = _forge_paths.corvin_home()
                _access = _check_compute_access(corvin_home=_home)
                self._log_security_event(
                    "compute.license.checked",
                    tool=name,
                    details=_access.as_audit_dict(),
                )
                if not _access.allowed:
                    self._respond(msgid, {
                        "content": [{
                            "type": "text",
                            "text": json.dumps({
                                "status": "error",
                                "error": "ComputeLicenseRequired",
                                "message": _access.reason,
                                "upgrade": "https://corvin-labs.com/pricing",
                            }, ensure_ascii=False),
                        }],
                        "isError": True,
                    })
                    self._log_security_event(
                        "compute.license.denied",
                        tool=name,
                        details=_access.as_audit_dict(),
                    )
                    return
                if _access.mode == "trial" and _enforce_trial_strategy is not None:
                    try:
                        # ADR-0190 verification finding: enforce_trial_strategy()
                        # reads a top-level args["strategy"] — correct for
                        # compute_run, but compute_submit's engine="flat" carries
                        # the same field nested under extra.strategy instead (see
                        # COMPUTE_SUBMIT_SCHEMA), and engine="pipeline"/"hac" has
                        # no strategy concept at all. Without this, a trial user
                        # submitting engine="flat" extra={"strategy":"bayesian"}
                        # via compute_submit would silently get the wrong (more
                        # generous) TRIAL_ITERATION_CAP instead of the tighter
                        # TRIAL_BAYESIAN_CAP, since args.get("strategy") always
                        # defaults to "grid" for compute_submit's own shape.
                        _trial_args = args
                        if name == "compute_submit" and args.get("engine") == "flat":
                            _flat_strategy = (args.get("extra") or {}).get("strategy")
                            if _flat_strategy:
                                _trial_args = dict(args)
                                _trial_args["strategy"] = _flat_strategy
                        args = _enforce_trial_strategy(_trial_args, corvin_home=_home)
                    except ValueError as exc:
                        self._respond(msgid, {
                            "content": [{
                                "type": "text",
                                "text": json.dumps({
                                    "status": "error",
                                    "error": "TrialStrategyNotAvailable",
                                    "message": str(exc),
                                }, ensure_ascii=False),
                            }],
                            "isError": True,
                        })
                        return
            except Exception as gate_exc:  # noqa: BLE001
                # Gate errors must never crash the compute surface. Fall through
                # and let the worker validate. Log so operators can investigate.
                self._log_security_event(
                    "compute.license.gate_error",
                    tool=name,
                    details={"error": str(gate_exc)},
                )

        # ADR-0094 M2 / ADR-0095 M3 — daily compute-unit quota gate.
        # Tries server-side permit first; falls back to local counter.
        # Fail-open: I/O / network errors never block compute.
        # ADR-0190 M2 — compute_submit spends compute the same way compute_run
        # does, so it shares the same quota gate.
        if name in ("compute_run", "compute_submit"):
            _cq_blocked = False
            _cq_msg = ""

            # ADR-0095 M3 / ADR-0098 P1: server-side permit (authoritative).
            # "no_credentials" = free-tier / unactivated install → fall through to local.
            # "server_error"   = credentials exist but server unreachable → fail-closed.
            # "granted"        = server issued permit → proceed.
            _server_result = "no_credentials"
            try:
                _server_result = _request_server_compute_permit(
                    job_id=str(args.get("id", "forge")),
                    tenant_id=str(args.get("tenant_id", "_default")),
                )
            except Exception as _srv_exc:
                import urllib.error as _ue
                if isinstance(_srv_exc, _ue.HTTPError) and _srv_exc.code == 403:
                    _cq_blocked = True
                    _cq_msg = "compute-quota-exceeded (server)"
                else:
                    # Credentials exist but server failed — treat as server_error.
                    _server_result = "server_error"
                    self._log_security_event(
                        "compute.permit.server_error",
                        tool=name,
                        details={"error": str(_srv_exc)[:200]},
                    )

            if not _cq_blocked:
                if _server_result == "server_error":
                    # ADR-0098 P1: fail-closed — paid subscription detected but server
                    # unreachable. The offline fallback is removed to close the quota-bypass
                    # attack vector (block corvin-features server → patch local counter).
                    _cq_blocked = True
                    _cq_msg = (
                        "compute-permit-required: Corvin-Features server unreachable. "
                        "A valid server-issued compute permit is required for paid tiers. "
                        "Check your internet connection and retry."
                    )
                    self._log_security_event(
                        "compute.permit.fail_closed",
                        tool=name,
                        details={"reason": "server_unreachable_with_credentials"},
                    )
                elif _server_result == "no_credentials":
                    # Free tier or unactivated install — local counter handles quota.
                    # ADR-0144 CMP-02/03 fix: the free-tier compute daily-quota gate
                    # was DEAD CODE. ``license`` is a package at operator/license whose
                    # compute_quota.py opens with a relative import (``from .limits ...``).
                    # The old code inserted ``parents[3]/"license"`` — i.e. <repo>/license,
                    # which does NOT exist — and then did a bare ``import compute_quota``,
                    # which ALSO breaks the relative import. Both faults guaranteed an
                    # ImportError that the broad ``except`` swallowed as an "operational
                    # error" → silent fail-OPEN: free-tier compute_units_per_day was never
                    # enforced via Forge MCP compute_run. Mirror the working a2a_worker
                    # pattern: put operator/ (parents[2]) on sys.path and import the module
                    # by its package-qualified name so the relative import resolves.
                    _CQLimitError: type | None = None  # guard isinstance on import failure
                    try:
                        _lic_root = str(Path(__file__).resolve().parents[2])  # operator/
                        if _lic_root not in sys.path:
                            sys.path.insert(0, _lic_root)
                        from license.compute_quota import increment_and_check as _cq_check  # type: ignore
                        from license.limits import LicenseLimitError as _CQLimitError  # type: ignore[assignment]
                        from forge import paths as _cq_paths
                        # increment_and_check raises LicenseLimitError or returns None
                        _cq_check(
                            _cq_paths.corvin_home(),
                            channel=args.get("channel", ""),
                            chat_key=args.get("chat_key", ""),
                        )
                    except Exception as _cq_exc:
                        if _CQLimitError is not None and isinstance(_cq_exc, _CQLimitError):
                            _cq_blocked = True
                            _cq_msg = str(_cq_exc)
                        else:
                            # Operational error (e.g. license package genuinely absent —
                            # a boot self_test CRITICAL) — log + fall through (fail-open
                            # for free tier, adapter/a2a_worker parity).
                            self._log_security_event(
                                "compute.license.gate_error",
                                tool=name,
                                details={"error": str(_cq_exc)},
                            )
                # "granted" → no action needed, proceed to compute
            if _cq_blocked:
                self._respond(msgid, {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "status": "error",
                            "error": "ComputeQuotaExceeded",
                            "message": _cq_msg,
                            "upgrade": "https://corvin-labs.com/pricing",
                        }, ensure_ascii=False),
                    }],
                    "isError": True,
                })
                self._log_security_event(
                    "compute.quota_exceeded",
                    tool=name,
                    details={"error": _cq_msg},
                )
                return

        try:
            sock = _compute_socket_path_for()
            if not sock.exists():
                self._error(
                    msgid, INVALID_PARAMS,
                    "compute worker socket not present — bootstrap + start "
                    "the plugin (core/compute/README.md).",
                )
                return
            client = _ComputeWorkerClient(sock, timeout_s=35.0)
            if name == "compute_run":
                result = client.submit_run(**args)
            elif name == "compute_status":
                result = client.get_status(args["compute_handle"])
            elif name == "compute_result":
                wait_s = float(args.get("wait_s") or 0.0)
                result = client.get_result(args["compute_handle"],
                                           wait_s=wait_s)
            elif name == "compute_abort":
                result = client.abort_run(args["compute_handle"])
            elif name == "compute_submit":
                # ADR-0029/ADR-0190 M2 — unified submit for pipeline/HAC engines.
                result = client.submit_engine_run(
                    engine=args["engine"],
                    budget=args["budget"],
                    extra=args.get("extra") or {},
                    tenant_id=args.get("tenant_id"),
                )
            elif name == "compute_gate":
                # COMPUTE_GATE_SCHEMA nests action_type/payload under "action";
                # WorkerClient.gate_action() takes them as flat params.
                _gate_action = args.get("action") or {}
                result = client.gate_action(
                    args["compute_handle"],
                    _gate_action.get("action_type"),
                    payload=_gate_action.get("payload"),
                )
            else:
                self._error(msgid, METHOD_NOT_FOUND,
                            f"unknown compute tool: {name}")
                return
        except _ComputeWorkerClientError as exc:
            self._error(msgid, INVALID_PARAMS,
                        f"compute worker {exc.error_class}: {exc.message}")
            return
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR,
                        f"compute worker call failed: {exc}")
            return

        # ── Post-submission: record trial iteration ───────────────────────
        if (
            name == "compute_run"
            and _access is not None
            and _access.mode == "trial"
            and _record_trial_iteration is not None
            and not result.get("error")
        ):
            try:
                from forge import paths as _forge_paths
                _record_trial_iteration(
                    _forge_paths.corvin_home(),
                    strategy=args.get("strategy", "grid"),
                )
            except Exception:
                pass

        # ── Inject trial watermark into response ──────────────────────────
        if name == "compute_run" and _access is not None:
            watermark = _access.trial_watermark()
            if watermark:
                result = dict(result)
                result["_license"] = watermark

        self._respond(msgid, {
            "content": [{"type": "text",
                         "text": json.dumps(result, ensure_ascii=False)}],
        })

        # UAH: register chat-initiated compute runs in the activity feed.
        if name == "compute_run" and not result.get("error") and _uah_write is not None:
            try:
                _uah_write(
                    action="compute.run_submit",
                    panel="compute",
                    entity_id=str(result.get("compute_handle", "")),
                    summary=str(args.get("tool_name", "compute run")),
                    extra={"strategy": str(args.get("strategy", ""))},
                )
            except Exception:
                pass

    # -- ADR-0026 Compute Fabric helpers ----------------------------------

    def _is_fabric_enabled(self) -> bool:
        """Return True when fabric_enabled=true in the current tenant config.

        Reads the tenant.corvin.yaml (or .yml / .json) from the registry
        root — the same location the gateway uses for ADR-0007 Phase 3.1
        config. Fail-closed: any read or parse error returns False so that
        missing / malformed config never silently enables the Fabric.
        """
        import yaml  # type: ignore[import]  # PyYAML is a gateway dep

        for name in (
            "tenant.corvin.yaml",
            "tenant.corvin.yml",
            "tenant.corvin.json",
        ):
            candidate = self.registry.root / name
            if not candidate.exists():
                # Walk up to corvin_home in case the registry root is a
                # workspace subdir (e.g. tenants/_default/forge/).
                for parent in candidate.parents:
                    candidate2 = parent / name
                    if candidate2.exists():
                        candidate = candidate2
                        break
                else:
                    continue
            try:
                raw = candidate.read_text(encoding="utf-8")
                if candidate.suffix in (".yaml", ".yml"):
                    data = yaml.safe_load(raw) or {}
                else:
                    import json as _json
                    data = _json.loads(raw)
                compute = (data.get("spec") or {}).get("compute") or {}
                return bool(compute.get("fabric_enabled", False))
            except Exception:
                return False
        return False

    def _call_datasource_connect(self, msgid: Any, args: dict) -> None:
        """ADR-0190 M3 — General Availability datasource registration.

        Applies the SAME ``license.validator.get_limit("datasource_adapters_
        allowed")`` gate already enforced on ``POST /v1/console/data-sources``
        (core/console/corvin_console/routes/data_sources.py) — same feature
        key, same fail-closed FREE_TIER fallback — so the console and chat
        surfaces stay authorization-consistent. Unlike the Fabric
        ``datasource_*`` tools (Enterprise-only, routed through the compute
        worker socket), this calls ``DataSourceRegistry.register()``
        in-process — register() is pure filesystem + audit_writer, so no
        worker needs to be running.
        """
        if _DataSourceRegistry is None:
            self._error(
                msgid, METHOD_NOT_FOUND,
                "compute plugin not installed (core/compute/)",
            )
            return

        manifest = args.get("manifest") or {}
        adapter = manifest.get("adapter", "")

        # ── License gate — identical decision to the console REST route ────
        allowed_adapters = _lic_get_limit("datasource_adapters_allowed")
        if allowed_adapters is not None and adapter not in allowed_adapters:
            self._respond(msgid, {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "error",
                        "error": "license_limit",
                        "feature": "datasource_adapters_allowed",
                        "adapter": adapter,
                        "message": (
                            f"Adapter '{adapter}' requires a Member licence or "
                            "higher. Only 'local_file' connections are "
                            "available on the Free tier."
                        ),
                        "upgrade": "https://corvin-labs.com/pricing",
                    }, ensure_ascii=False),
                }],
                "isError": True,
            })
            self._log_security_event(
                "datasource.license_denied",
                tool="datasource_connect",
                details={"adapter": adapter},
            )
            return

        try:
            from .paths import corvin_home as _forge_corvin_home
            from .tenants import current_tenant as _current_tenant
            from .security_events import write_event as _write_event

            tenant_id = _current_tenant(args.get("tenant_id"))
            audit_path = _forge_corvin_home() / "tenants" / tenant_id / "audit.jsonl"

            def _audit_writer(event_type: str, severity: str, details: dict) -> None:
                _write_event(audit_path, event_type, severity=severity, details=details)

            reg = _DataSourceRegistry()
            registered = reg.register(manifest, tenant_id, audit_writer=_audit_writer)
        except (KeyError, ValueError) as exc:
            self._error(msgid, INVALID_PARAMS, f"invalid datasource manifest: {exc}")
            return
        except PermissionError as exc:
            self._error(msgid, INVALID_PARAMS, f"datasource connect not permitted: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR, f"datasource connect failed: {exc}")
            return

        self._log_security_event(
            "datasource.connected",
            tool="datasource_connect",
            details={"adapter": adapter, "name": getattr(registered, "name", "")},
        )
        self._respond(msgid, {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "status": "ok",
                    "name": getattr(registered, "name", ""),
                    "adapter": adapter,
                }, ensure_ascii=False),
            }],
        })

    def _call_fabric_tool(self, msgid: Any, name: str, args: dict) -> None:
        """Route ADR-0026 Fabric tool calls.

        Gates (both must pass):
          1. fabric_enabled=true in the tenant config (operator opt-in).
          2. compute_fabric feature flag present in the installed license
             (ADR-0017 Phase III, enterprise tier or above).

        If either gate fails, return a typed sentinel and emit an audit event.
        When both pass, forward to the compute worker via the same socket
        client used by compute_* tools.
        """
        if not self._is_fabric_enabled():
            self._respond(msgid, {
                "content": [{"type": "text",
                             "text": json.dumps(_FABRIC_NOT_ENABLED,
                                                ensure_ascii=False)}],
                "isError": True,
            })
            return

        # License gate — fabric requires enterprise compute_fabric flag.
        if _check_compute_access is not None:
            try:
                from forge import paths as _forge_paths
                _fabric_access = _check_compute_access(corvin_home=_forge_paths.corvin_home())
                self._log_security_event(
                    "compute.license.fabric_checked",
                    tool=name,
                    details=_fabric_access.as_audit_dict(),
                )
                if not _fabric_access.allowed or not _fabric_access.fabric_allowed:
                    _reason = (
                        _fabric_access.reason
                        or "Compute Fabric requires an Enterprise license. "
                           "Upgrade at https://corvin-labs.com/pricing"
                    )
                    self._respond(msgid, {
                        "content": [{
                            "type": "text",
                            "text": json.dumps({
                                "status": "error",
                                "error": "ComputeFabricLicenseRequired",
                                "message": _reason,
                                "upgrade": "https://corvin-labs.com/pricing",
                            }, ensure_ascii=False),
                        }],
                        "isError": True,
                    })
                    self._log_security_event(
                        "compute.license.fabric_denied",
                        tool=name,
                        details=_fabric_access.as_audit_dict(),
                    )
                    return
            except Exception as gate_exc:  # noqa: BLE001
                self._log_security_event(
                    "compute.license.gate_error",
                    tool=name,
                    details={"error": str(gate_exc), "surface": "fabric"},
                )
        if _ComputeWorkerClient is None:
            self._error(
                msgid, METHOD_NOT_FOUND,
                "compute plugin not installed (core/compute/)",
            )
            return
        try:
            sock = _compute_socket_path_for()
            if not sock.exists():
                self._error(
                    msgid, INVALID_PARAMS,
                    "compute worker socket not present — start the plugin "
                    "(core/compute/README.md).",
                )
                return
            client = _ComputeWorkerClient(sock, timeout_s=35.0)
            # Forward the fabric tool call to the worker by name.
            # The worker is responsible for routing to the correct
            # Fabric sub-system (BackendRegistry, ShardManager, etc.).
            result = client.call_fabric(name, args)  # type: ignore[attr-defined]
        except _ComputeWorkerClientError as exc:
            self._error(msgid, INVALID_PARAMS,
                        f"fabric worker {exc.error_class}: {exc.message}")
            return
        except AttributeError:
            # call_fabric not yet implemented in the installed worker client
            # (Phase 26.1+ required). Return a clear error so the LLM knows
            # the worker needs upgrading — not a hard crash.
            self._error(
                msgid, METHOD_NOT_FOUND,
                f"fabric tool '{name}' requires compute worker >= Phase 26.1; "
                "please upgrade core/compute/.",
            )
            return
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR,
                        f"fabric worker call failed: {exc}")
            return
        self._respond(msgid, {
            "content": [{"type": "text",
                         "text": json.dumps(result, ensure_ascii=False)}],
        })

    # -- ADR-0012 data-locality MCP handlers ------------------------------

    def _call_data_tool(self, msgid: Any, fn: Any, args: dict) -> None:
        """Wrap an corvin_data.call_data_* handler.

        The handler is a pure function returning a dict on success or
        raising ToolError on user-facing errors. Audit events the
        handler emits are routed through self._log_security_event.
        """
        def _audit(event_type: str, details: dict) -> None:
            try:
                self._log_security_event(event_type, details=details)
            except Exception as exc:  # observability is best-effort
                self._log(f"data audit emit failed: {exc}")

        try:
            result = fn(
                self.data_registry,
                args,
                persona=self.caller_persona,
                audit=_audit,
            )
        except _DataToolError as exc:
            self._tool_error(msgid, exc.message)
            return
        except Exception as exc:  # belt-and-braces — never crash dispatch
            traceback.print_exc(file=self._stderr)
            self._tool_error(msgid, f"data tool error: {exc}")
            return
        # Minimal text summary + structured payload — mirrors forge_list shape.
        if "data_handle" in result:
            summary = (
                f"data_handle={result['data_handle']}; "
                f"snapshot ready"
                if "snapshot" in result
                else f"data_handle={result['data_handle']}"
            )
        elif "ok" in result:
            summary = "ok" if result.get("found") else "ok (handle was not present)"
        else:
            summary = "done"
        self._tool_success(msgid, summary, structured=result)
        # UAH: register chat-initiated data_register calls in the activity feed.
        if fn is _call_data_register and _uah_write is not None:
            try:
                _uah_write(
                    action="datasource.register",
                    panel="datasources",
                    entity_id=str(args.get("name", "")),
                    summary=str(args.get("description", args.get("name", "datasource"))),
                )
            except Exception:
                pass

    # -- meta-tool: forge_list --------------------------------------------

    def _call_forge_list(self, msgid: Any, args: dict) -> None:
        """List forged tools (without the meta-tools themselves) so the
        caller can discover what's already available before forging a
        duplicate. Optional scope filter restricts to one workspace level."""
        scope_filter = args.get("scope")
        if scope_filter is not None and scope_filter not in (
            "task", "session", "project", "user"
        ):
            self._tool_error(
                msgid,
                f"invalid scope {scope_filter!r}; valid: task|session|project|user",
            )
            return
        meta_names = {"forge_tool", "forge_promote", "forge_list"}
        listed: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ws_scope, spec in self.multi.list_with_scope():
            if scope_filter is not None and ws_scope != scope_filter:
                continue
            if spec.name in seen or spec.name in meta_names:
                continue
            if not self._is_forged_tool_allowed(spec.name):
                continue
            seen.add(spec.name)
            listed.append({
                "name":        spec.name,
                "description": spec.description,
                "scope":       ws_scope,
                "call_count":  getattr(spec, "call_count", 0),
            })
        # legacy single-root registry — only included when no scope filter
        # is set, since it has no scope label.
        if scope_filter is None:
            for spec in self.registry.list():
                if spec.name in seen or spec.name in meta_names:
                    continue
                if not self._is_forged_tool_allowed(spec.name):
                    continue
                seen.add(spec.name)
                listed.append({
                    "name":        spec.name,
                    "description": spec.description,
                    "scope":       "legacy",
                    "call_count":  getattr(spec, "call_count", 0),
                })
        self._tool_success(
            msgid,
            f"{len(listed)} forged tool(s)"
            + (f" in scope={scope_filter}" if scope_filter else " (all scopes)"),
            structured={"tools": listed},
        )

    # -- meta-tool: forge_exec --------------------------------------------

    def _call_forge_exec(self, msgid: Any, args: dict) -> None:
        """Execute a registered forged tool by name (Gate 2 + 3).

        Differs from a direct forged-tool MCP call only in one way: it
        works even when the tool was registered in the *same* session turn
        (the MCP tools/list_changed notification hasn't been processed by
        the client yet).  The forge server's MultiRegistry already holds
        the new spec in memory, so the lookup succeeds immediately.

        Security properties inherited from the normal call path:
          - bwrap sandbox (no network, ro /usr, fresh /tmp)
          - policy gate (name_allowed, persona ACL)
          - rate limiter + circuit breaker
          - audit via tool.created / run_tool
        Additional: emits code.exec_attempt into the audit chain.
        """
        tool_name = args.get("name")
        tool_input = args.get("input")
        if not isinstance(tool_name, str) or not tool_name:
            self._error(msgid, INVALID_PARAMS,
                        "forge_exec: 'name' (string) is required")
            return
        if not isinstance(tool_input, dict):
            self._error(msgid, INVALID_PARAMS,
                        "forge_exec: 'input' must be an object")
            return
        # Emit Gate-4 audit event before execution.
        self._log_security_event(
            "code.exec_attempt",
            tool=tool_name,
            details={
                "language": "python",
                "outcome": "attempting",
                "caller_persona": os.environ.get("CORVIN_CALLER_PERSONA", ""),
            },
        )
        # Reuse the full _call_forged path (policy + rate-limit +
        # circuit-breaker + bwrap runner).
        self._call_forged(msgid, tool_name, tool_input)

    # -- meta-tool: forge_tool --------------------------------------------

    def _call_forge_tool(self, msgid: Any, args: dict) -> None:
        # Policy gate #1: tool name (forbidden globs + namespace allowlist)
        name = args.get("name", "")
        if isinstance(name, str):
            allowed, reason = self.policy.name_allowed(name)
            if not allowed:
                self._log_security_event(
                    "policy.namespace_denied",
                    tool=name,
                    details={"reason": reason},
                )
                self._tool_error(
                    msgid,
                    f"policy denied tool name {name!r}: {reason}",
                )
                return

        # Policy gate #1b — layer 9 per-persona namespace gate. ``coder``
        # may only register tools under ``code.``, ``inbox`` only under
        # ``inbox.``, etc. Wildcard (no persona env, persona missing from
        # policy.persona_namespaces) = legacy behaviour, no gate.
        if isinstance(name, str):
            ok, ns_reason = self.policy.namespace_check(
                self.caller_persona, name,
            )
            if not ok:
                self._log_security_event(
                    "tool.namespace_denied",
                    tool=name,
                    details={
                        "reason": ns_reason,
                        "caller_persona": self.caller_persona,
                        "allowed_prefix": self.policy.namespace_for(
                            self.caller_persona),
                    },
                )
                self._tool_error(msgid, ns_reason)
                return

        # Policy gate #2: forbidden imports static check (Python only)
        impl = args.get("impl", "") or ""
        runtime = args.get("runtime", "python")
        try:
            assert_imports_ok(
                impl,
                forbidden=self.policy.forbidden_imports,
                runtime=runtime,
            )
        except StaticCheckError as e:
            self._log_security_event(
                "policy.import_denied",
                tool=name if isinstance(name, str) else "",
                details={"violations": e.violations},
            )
            self._tool_error(
                msgid,
                f"policy denied: {e}",
            )
            return

        ws_scope_arg = args.get("scope")
        if ws_scope_arg is not None and not isinstance(ws_scope_arg, str):
            self._tool_error(msgid, "scope must be a string if given")
            return
        try:
            if ws_scope_arg is None:
                # Legacy: single-root registry, no workspace-scope routing.
                # This keeps existing tests / ops tooling that doesn't pass
                # a scope working unchanged.
                spec = self.registry.create(
                    name=args["name"],
                    description=args["description"],
                    input_schema=args["input_schema"],
                    impl=args["impl"],
                    runtime=runtime,
                    overwrite=bool(args.get("overwrite", False)),
                    meta=args.get("meta") or {},
                )
            else:
                spec = self.multi.create(
                    scope=ws_scope_arg,
                    name=args["name"],
                    description=args["description"],
                    input_schema=args["input_schema"],
                    impl=args["impl"],
                    runtime=runtime,
                    overwrite=bool(args.get("overwrite", False)),
                    meta=args.get("meta") or {},
                )
        except KeyError as e:
            self._tool_error(msgid, f"missing argument: {e.args[0]}")
            return
        except (ValueError, FileExistsError) as e:
            self._tool_error(msgid, str(e))
            return
        # Only auto-record approval when the operator explicitly opted in via
        # --permission-mode yes. In ask/deny mode, the freshly forged tool
        # must still pass the permission gate on first call.
        if self.permission_mode == "yes":
            from .permissions import PermissionStore
            PermissionStore(self.registry.root).record(
                spec.name, spec.sha256, mode="yes"
            )
        self._tool_success(
            msgid,
            f"forged {spec.name} (sha={spec.sha256}, runtime={spec.runtime})",
            structured=asdict(spec),
        )
        # UAH: register chat-initiated forge tool creation in the activity feed.
        if _uah_write is not None:
            try:
                _uah_write(
                    action="forge.tool_create",
                    panel="forge",
                    entity_id=spec.name,
                    summary=spec.description[:200] if spec.description else spec.name,
                    extra={"runtime": spec.runtime, "scope": str(ws_scope_arg or "legacy")},
                )
            except Exception:
                pass
        self._notify("notifications/tools/list_changed")

    def _call_forge_promote(self, msgid: Any, args: dict) -> None:
        name = args.get("name")
        if not isinstance(name, str):
            self._tool_error(msgid, "missing 'name'")
            return
        to = args.get("to")
        if to is not None and not isinstance(to, str):
            self._tool_error(msgid, "'to' must be a string if given")
            return
        # If 'to' is given, do a workspace-scope promotion via MultiRegistry.
        # Otherwise fall back to the legacy single-root promotion that
        # materialises a Skill folder under .forge/skills/<name>/.
        if to is not None:
            try:
                spec = self.multi.promote(name, to=to)
            except KeyError:
                self._tool_error(msgid, f"unknown tool: {name}")
                return
            except ValueError as e:
                self._tool_error(msgid, str(e))
                return
            self._tool_success(
                msgid,
                f"promoted {name} -> scope={to}",
                structured=asdict(spec),
            )
            self._notify("notifications/tools/list_changed")
            return
        try:
            skill_dir = self.registry.promote(name)
        except KeyError:
            self._tool_error(msgid, f"unknown tool: {name}")
            return
        self._tool_success(msgid, f"promoted {name} -> {skill_dir}")
        self._notify("notifications/tools/list_changed")

    # -- forged tool dispatch ---------------------------------------------

    def _call_forged(self, msgid: Any, name: str, args: dict) -> None:
        # Per-persona ACL: gate forged-tool calls before any registry
        # work happens, so unknown-or-disallowed look identical to
        # callers (no oracle for tool existence).
        if not self._is_forged_tool_allowed(name):
            self._log_security_event(
                "acl.persona_denied",
                tool=name,
                details={
                    "allowed_globs": self.allowed_forged_tools,
                    "reason": "not in allowed_forged_tools",
                },
            )
            self._tool_error(
                msgid,
                f"acl.persona_denied: {name!r} not in this persona's "
                f"allowed_forged_tools",
            )
            return

        # Workflow-policy gate at call time. The same check runs at
        # forge_tool time, but a tool registered before a policy edit
        # could otherwise outlive its ban — re-checking here closes
        # the post-hoc-deny gap. Hot-reload (above) guarantees we see
        # the latest forbidden_tool_names list.
        policy_ok, policy_reason = self.policy.name_allowed(name)
        if not policy_ok:
            self._log_security_event(
                "policy.namespace_denied",
                tool=name,
                details={"reason": policy_reason, "phase": "call"},
            )
            self._tool_error(
                msgid,
                f"policy denied tool name {name!r}: {policy_reason}",
            )
            return

        spec = self.registry.get(name)
        # Fall back to multi-scope lookup with shadowing if the tool isn't in
        # the legacy single-root registry.
        registry_for_call: Registry = self.registry
        if spec is None:
            ws_scope = self.multi.find_scope(name)
            if ws_scope is not None:
                registry_for_call = self.multi._registry(ws_scope)
                spec = registry_for_call.get(name)
        if spec is None:
            self._tool_error(msgid, f"unknown tool: {name}")
            return

        # Rate limiter (always on; capacity from policy.rate_limit_for(name))
        limiter = self.breakers.get_limiter(name)
        if not limiter.try_consume():
            self._log_security_event(
                "rate_limit.exceeded",
                tool=name,
                details={"capacity_per_minute": self.policy.rate_limit_for(name)},
            )
            self._tool_error(
                msgid,
                f"rate_limit.exceeded for tool {name!r}: "
                f"capacity {self.policy.rate_limit_for(name)}/min",
            )
            return

        # Circuit breaker (gated by policy.circuit_breaker_enabled)
        if self.policy.circuit_breaker_enabled:
            breaker = self.breakers.get_breaker(name)
            ok, reason = breaker.can_execute()
            if not ok:
                self._log_security_event(
                    "circuit_breaker.rejected",
                    tool=name,
                    details={"state": breaker.state, "reason": reason,
                             "consecutive_failures": breaker.consecutive_failures},
                )
                self._tool_error(
                    msgid,
                    f"circuit_breaker.{reason} for tool {name!r}: "
                    f"state={breaker.state}, "
                    f"failures={breaker.consecutive_failures}",
                )
                return
            if reason == "half_open_probe":
                self._log_security_event(
                    "circuit_breaker.half_open",
                    tool=name,
                    details={"probe_attempt": breaker.half_open_attempts},
                )

        # ADR-0069 M1: route through TEB when available.  The broker runs the
        # path-gate pre-hook (deny writes to protected paths for non-CC engines)
        # and emits audit events around the actual run_tool() call.  For CC
        # engines the broker is still active but its path-gate is supplementary
        # — CC's native PreToolUse hooks remain the primary gate.
        if self._teb is not None:
            # Build a call-scoped executor closure (thread-safe: captures
            # local variables, never mutates shared broker state).
            _call_registry = registry_for_call
            _call_permission_mode = self.permission_mode
            _call_policy = self.policy
            _call_persona = self.forge_persona

            def _scoped_executor(tool_name: str, args: dict) -> Any:
                try:
                    return run_tool(
                        _call_registry, tool_name, args,
                        permission_mode=_call_permission_mode,
                        policy=_call_policy,
                        caller_persona=_call_persona,
                    )
                except SchemaError as _se:
                    # Re-raise with "schema error: " prefix so the TEB's
                    # generic except clause preserves the human-readable
                    # format expected by the caller (audit_event["error"]).
                    raise SchemaError(f"schema error: {_se}") from _se

            # Pass executor per-call so concurrent _call_forged() invocations
            # never share mutable state on self._teb.
            broker_result = self._teb.execute(
                engine_id=self.engine_id,
                tool_name=name,
                args=args if isinstance(args, dict) else {},
                chat_key=os.environ.get("CORVIN_CHAT_KEY", ""),
                executor=_scoped_executor,
            )
            if broker_result.denied:
                self._log_security_event(
                    "teb.path_gate.denied",
                    tool=name,
                    details={"reason": broker_result.denial_reason,
                             "engine_id": self.engine_id},
                )
                self._tool_error(msgid, broker_result.denial_reason)
                return
            if not broker_result.success:
                # broker caught an exception from run_tool — return error directly.
                # Do NOT fall through to the run_tool() block below, which would
                # execute the tool a second time (double-run bug).
                _broker_err = "tool execution failed"
                for _ev in reversed(broker_result.audit_events):
                    if _ev.get("event") == "tool_call.failed" and _ev.get("error"):
                        _broker_err = _ev["error"]
                        break
                # Mirror the ToolError handler: update the circuit breaker on failure.
                # SchemaError (marked with "schema error:" prefix by _scoped_executor)
                # is a caller-validation error and must NOT trip the circuit breaker.
                _is_schema_err = _broker_err.startswith("schema error:")
                if self.policy.circuit_breaker_enabled and not _is_schema_err:
                    _cb = self.breakers.get_breaker(name)
                    _trans = _cb.record_failure()
                    if _trans in ("opened", "reopened_from_half_open"):
                        self._log_security_event(
                            "circuit_breaker.opened",
                            tool=name,
                            details={"transition": _trans,
                                     "failures": _cb.consecutive_failures,
                                     "reset_timeout_s": _cb.reset_timeout},
                        )
                # SchemaError → "schema error: …"; ToolError → "tool error: …"
                _err_prefix = "" if _is_schema_err else "tool error: "
                self._tool_error(msgid, f"{_err_prefix}{_broker_err}")
                return
            else:
                # TEB executed successfully — mirror the non-TEB success path:
                # record circuit breaker success before responding.
                if self.policy.circuit_breaker_enabled:
                    _cb_s = self.breakers.get_breaker(name)
                    _trans_s = _cb_s.record_success()
                    if _trans_s == "closed_from_half_open":
                        self._log_security_event(
                            "circuit_breaker.closed", tool=name,
                            details={"transition": _trans_s},
                        )
                if broker_result.output is not None:
                    self._mirror_run_artifacts_to_outputs(
                        broker_result.output, registry_for_call)
                self._tool_success(
                    msgid,
                    json.dumps(broker_result.output.data, indent=2)
                    if broker_result.output is not None and broker_result.output.data is not None
                    else "(no output)",
                    structured=broker_result.output.to_dict()
                    if broker_result.output is not None else None,
                )
                return

        try:
            result = run_tool(
                registry_for_call, name, args,
                permission_mode=self.permission_mode,
                policy=self.policy,
                # Pass the immutable startup-persona explicitly. Eliminates
                # env-trust at the runner choke-point — even if FORGE_PERSONA
                # in the runner's env were tampered with, the kwarg wins.
                caller_persona=self.forge_persona,
            )
        except SchemaError as e:
            # caller-side problem — does NOT count as a tool failure
            self._tool_error(msgid, f"schema error: {e}")
            return
        except PermissionDenied as e:
            # security event, not a tool failure
            self._tool_error(msgid, f"permission denied: {e}")
            return
        except TamperError as e:
            self._log_security_event(
                "tool.tamper_detected", tool=name, details={"error": str(e)},
            )
            self._tool_error(msgid, f"tamper detected: {e}")
            return
        except ToolError as e:
            # genuine tool-side failure → record + maybe trip the breaker
            if self.policy.circuit_breaker_enabled:
                breaker = self.breakers.get_breaker(name)
                transition = breaker.record_failure()
                if transition in ("opened", "reopened_from_half_open"):
                    self._log_security_event(
                        "circuit_breaker.opened",
                        tool=name,
                        details={"transition": transition,
                                 "failures": breaker.consecutive_failures,
                                 "reset_timeout_s": breaker.reset_timeout},
                    )
            self._tool_error(msgid, f"tool error: {e}")
            return

        # Success path → reset / close the breaker
        if self.policy.circuit_breaker_enabled:
            breaker = self.breakers.get_breaker(name)
            transition = breaker.record_success()
            if transition == "closed_from_half_open":
                self._log_security_event(
                    "circuit_breaker.closed", tool=name,
                    details={"transition": transition},
                )
        self._mirror_run_artifacts_to_outputs(result, registry_for_call)
        self._tool_success(
            msgid,
            json.dumps(result.data, indent=2) if result.data is not None else "(no output)",
            structured=result.to_dict(),
        )

    def _mirror_run_artifacts_to_outputs(
        self, result: "RunResult", registry: "Registry"
    ) -> None:
        """Copy image/plot artifacts from a Forge run dir to the session outputs/.

        The session outputs/ directory is sibling to the forge/ workspace
        (``registry.root.parent / "outputs"``).  The adapter auto-attaches
        every new file in outputs/ as a Discord/Telegram attachment, so this
        makes Forge-generated plots appear in-chat for ALL engine types
        (not just ClaudeCodeEngine which has PostToolUse hooks).
        """
        if not result.run_id:
            return
        _MIRROR_EXTS = {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
            ".pdf", ".html", ".csv", ".mp4", ".webm",
        }
        try:
            import shutil as _shutil
            run_artifacts = Path(registry.root) / "runs" / result.run_id / "artifacts"
            session_outputs = Path(registry.root).parent / "outputs"
            if not run_artifacts.exists():
                return
            session_outputs.mkdir(exist_ok=True)
            for _f in run_artifacts.iterdir():
                if not _f.is_file():
                    continue
                if _f.suffix.lower() not in _MIRROR_EXTS:
                    continue
                _dest = session_outputs / _f.name
                if not _dest.exists():
                    try:
                        _shutil.copy2(_f, _dest)
                        if _corvin_log is not None:
                            _corvin_log.debug(
                                "forge artifact→outputs: %s", _f.name)
                    except OSError as _e:
                        if _corvin_log is not None:
                            _corvin_log.warning(
                                "artifact→outputs copy failed for %s: %s",
                                _f.name, _e)
        except Exception as _exc:
            if _corvin_log is not None:
                _corvin_log.warning(
                    "_mirror_run_artifacts_to_outputs: %s", _exc)

    # -- response helpers --------------------------------------------------

    def _tool_success(
        self, msgid: Any, text: str, *, structured: Any = None
    ) -> None:
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }
        if structured is not None:
            result["structuredContent"] = structured
        self._respond(msgid, result)

    def _tool_error(self, msgid: Any, text: str) -> None:
        self._respond(
            msgid,
            {
                "content": [{"type": "text", "text": text}],
                "isError": True,
            },
        )

    # -- security events --------------------------------------------------

    def _log_security_event(
        self,
        event_type: str,
        *,
        tool: str = "",
        run_id: str = "",
        details: dict | None = None,
    ) -> None:
        """Append a structured security event with hash-chain integrity."""
        path = self.registry.root / self.registry.AUDIT_NAME
        if getattr(self, "forge_persona", ""):
            details = dict(details or {})
            details.setdefault("persona", self.forge_persona)
        try:
            _write_security_event(
                path, event_type,
                tool=tool, run_id=run_id, details=details,
                hash_chain=self.policy.audit_hash_chain,
            )
        except OSError:
            pass

    # ── Layer 33 — Session Artifact Memory handlers ──────────────────────

    def _session_artifacts_root(self) -> Path:
        """Resolve the session-scope artifacts directory.

        Priority:
        1. CORVIN_SESSION_KEY env (adapter-set, format ``<bridge>:<chat>``)
        2. ``self.registry.root.parent / "artifacts"`` — derived from the
           MCP server's workspace root, which is conventionally
           ``<session>/forge/`` so the sibling is ``<session>/artifacts/``.
        """
        from . import artifacts as _art
        sk = os.environ.get("CORVIN_SESSION_KEY") or ""
        if sk and "/" not in sk and ".." not in sk:
            try:
                return _art.session_artifacts_dir(sk)
            except Exception:
                pass
        return Path(self.registry.root).parent / "artifacts"

    def _global_artifacts_root(self) -> Path:
        from . import artifacts as _art
        return _art.global_artifacts_dir()

    def _artifact_scopes(self, scope_arg: str) -> list[tuple[str, Path]]:
        """Return [(scope_label, root), ...] for a given scope argument."""
        session_root = self._session_artifacts_root()
        global_root = self._global_artifacts_root()
        if scope_arg == "global":
            return [("global", global_root)]
        if scope_arg == "all":
            return [("session", session_root), ("global", global_root)]
        return [("session", session_root)]

    def _call_artifact_list(self, msgid: Any, args: dict) -> None:
        from . import artifacts as _art
        after_ts = args.get("after_ts")
        mime = args.get("mime") or None
        limit = int(args.get("limit") or 20)
        scope_arg = str(args.get("scope") or "session")
        items: list[dict[str, Any]] = []
        for scope_label, root in self._artifact_scopes(scope_arg):
            for e in _art.list_active(root, mime=mime, after_ts=after_ts,
                                      limit=limit):
                items.append({
                    "name": e.name,
                    "mime": e.mime,
                    "size": e.size,
                    "ts": e.ts,
                    "description": e.description,
                    "tags": e.tags,
                    "scope": scope_label,
                    "pinned": e.pinned,
                })
        items.sort(key=lambda x: x["ts"], reverse=True)
        truncated = len(items) > limit
        items = items[:limit]
        self._respond(msgid, {
            "content": [{"type": "text",
                         "text": json.dumps({"artifacts": items,
                                             "truncated": truncated},
                                            ensure_ascii=False)}]
        })

    def _call_artifact_search(self, msgid: Any, args: dict) -> None:
        """FTS5 search against recall.db's artifact_summary class.

        Best-effort: when recall isn't wired (older deployments), falls back
        to substring search across descriptions.
        """
        from . import artifacts as _art
        query = (args.get("query") or "").strip()
        if not query:
            self._error(msgid, INVALID_PARAMS, "missing query")
            return
        limit = int(args.get("limit") or 5)
        scope_arg = str(args.get("scope") or "session")
        hits: list[dict[str, Any]] = []
        for scope_label, root in self._artifact_scopes(scope_arg):
            entries = _art.list_active(root, limit=10_000)
            q_lower = query.lower()
            for e in entries:
                hay = f"{e.name}\n{e.description}\n{' '.join(e.tags)}".lower()
                if q_lower in hay:
                    # Build a snippet around the hit.
                    idx = hay.find(q_lower)
                    start = max(0, idx - 30)
                    end = min(len(hay), idx + len(query) + 30)
                    hits.append({
                        "name": e.name,
                        "snippet": hay[start:end],
                        "scope": scope_label,
                        "mime": e.mime,
                        "size": e.size,
                    })
                if len(hits) >= limit:
                    break
        self._respond(msgid, {
            "content": [{"type": "text",
                         "text": json.dumps({"hits": hits},
                                            ensure_ascii=False)}]
        })

    def _call_artifact_get(self, msgid: Any, args: dict) -> None:
        from . import artifacts as _art
        name = str(args.get("name") or "")
        if not name:
            self._error(msgid, INVALID_PARAMS, "missing name")
            return
        max_bytes = int(args.get("max_bytes") or 65536)
        encoding = str(args.get("encoding") or "auto")

        for scope_label, root in self._artifact_scopes("all"):
            entry = _art.find_by_name(root, name)
            if entry is None:
                continue
            if entry.size > max_bytes:
                payload = {
                    "too_large": True,
                    "size": entry.size,
                    "mime": entry.mime,
                    "hint": "Use artifact_extract with a range, or call again "
                            "with a larger max_bytes (up to 1 MB).",
                }
                self._respond(msgid, {
                    "content": [{"type": "text",
                                 "text": json.dumps(payload)}]})
                return
            data = _art.read_artifact_bytes(root, entry, max_bytes=max_bytes)
            text_decoded: str | None = None
            is_text = (entry.mime.startswith("text/")
                       or entry.mime in ("application/json",
                                         "application/xml"))
            if encoding == "auto":
                encoding = "text" if is_text else "base64"
            if encoding == "text":
                try:
                    text_decoded = data.decode("utf-8")
                except UnicodeDecodeError:
                    encoding = "base64"
            payload = {
                "name": entry.name,
                "mime": entry.mime,
                "size": entry.size,
                "encoding": encoding,
                "scope": scope_label,
                "content": text_decoded if encoding == "text"
                           else _b64(data),
            }
            # Audit: artifact.read — name + size only.
            from . import artifacts as _arts
            _arts._emit_audit(
                "artifact.read", severity="INFO",
                details={"name": entry.name, "size": entry.size,
                         "max_bytes": max_bytes})
            self._respond(msgid, {
                "content": [{"type": "text",
                             "text": json.dumps(payload, ensure_ascii=False)}]})
            return
        self._error(msgid, METHOD_NOT_FOUND, f"no artifact named {name!r}")

    def _call_artifact_extract(self, msgid: Any, args: dict) -> None:
        from . import artifacts as _art
        name = str(args.get("name") or "")
        rng = str(args.get("range") or "")
        if not name or not rng:
            self._error(msgid, INVALID_PARAMS, "missing name or range")
            return
        for _, root in self._artifact_scopes("all"):
            entry = _art.find_by_name(root, name)
            if entry is None:
                continue
            payload = _extract_range(root, entry, rng)
            self._respond(msgid, {
                "content": [{"type": "text",
                             "text": json.dumps(payload, ensure_ascii=False)}]})
            return
        self._error(msgid, METHOD_NOT_FOUND, f"no artifact named {name!r}")

    def _call_artifact_register(self, msgid: Any, args: dict) -> None:
        from . import artifacts as _art
        path_str = str(args.get("path") or "")
        if not path_str:
            self._error(msgid, INVALID_PARAMS, "missing path")
            return
        src = Path(path_str)
        artifacts_root = self._session_artifacts_root()
        # Refuse any path outside the session artifact root.
        try:
            src_resolved = src.resolve(strict=True)
            artifacts_root.mkdir(parents=True, exist_ok=True)
            root_resolved = artifacts_root.resolve()
            src_resolved.relative_to(root_resolved)
        except (FileNotFoundError, OSError, ValueError):
            self._error(msgid, INVALID_PARAMS,
                        "path must already live under <session>/artifacts/")
            return
        try:
            entry = _art.register(
                source_path=src,
                artifacts_root=artifacts_root,
                description=str(args.get("description") or ""),
                tags=args.get("tags") or [],
                by_tool="mcp.artifact_register",
            )
        except _art.ArtifactError as e:
            self._error(msgid, INVALID_PARAMS, str(e))
            return
        self._respond(msgid, {
            "content": [{"type": "text",
                         "text": json.dumps({"registered": entry.name,
                                             "sha256": entry.sha256,
                                             "size": entry.size,
                                             "mime": entry.mime})}]})

    def _call_artifact_pin(self, msgid: Any, args: dict) -> None:
        from . import artifacts as _art
        name = str(args.get("name") or "")
        if not name:
            self._error(msgid, INVALID_PARAMS, "missing name")
            return
        try:
            pinned = _art.pin(
                session_root=self._session_artifacts_root(),
                global_root=self._global_artifacts_root(),
                name=name,
            )
        except _art.ArtifactError as e:
            self._error(msgid, METHOD_NOT_FOUND, str(e))
            return
        self._respond(msgid, {
            "content": [{"type": "text",
                         "text": json.dumps({"pinned": pinned.name,
                                             "sha256": pinned.sha256,
                                             "path_rel": pinned.path_rel})}]})


    def _call_audit_write_event(self, msgid: Any, args: dict) -> None:
        """ADR-0116 M2 — Worker Audit Gateway.

        Validates event_type against the EVENT_SEVERITY allowlist, strips
        forbidden detail keys (prompt text, outputs), then writes to the
        audit chain via the existing _write_security_event path.
        """
        from .security_events import EVENT_SEVERITY as _ES

        event_type = str(args.get("event_type") or "").strip()
        if not event_type:
            self._error(msgid, INVALID_PARAMS, "event_type is required")
            return
        if event_type not in _ES:
            self._log_security_event(
                "audit.worker_event_rejected",
                details={"event_type": event_type[:64],
                         "reason": "unknown_event_type"},
            )
            self._error(
                msgid, INVALID_PARAMS,
                f"unknown event_type '{event_type}' — "
                "only registered events may be written via this gateway",
            )
            return

        severity = str(args.get("severity") or _ES[event_type])
        raw_details = dict(args.get("details") or {})
        # Strip keys that are forbidden from the audit chain (GDPR Art. 5
        # data minimisation — prompt/output text must never reach the chain).
        _FORBIDDEN = frozenset({
            "prompt", "instruction", "output", "result", "text",
            "content", "message", "body", "data",
        })
        details = {k: v for k, v in raw_details.items()
                   if k not in _FORBIDDEN and isinstance(k, str)}
        # Inject delegation_id as a top-level detail if provided.
        dlg_id = args.get("delegation_id")
        if isinstance(dlg_id, str) and dlg_id:
            details["delegation_id"] = dlg_id[:40]
        # Gate: only INFO/WARNING allowed from workers; ERROR/CRITICAL reserved
        # for server-internal events.  Downgrade silently to keep fail-open.
        if severity not in ("INFO", "WARNING"):
            severity = "INFO"
        try:
            _audit_path = self.registry.root / self.registry.AUDIT_NAME
            _write_security_event(
                _audit_path, event_type,
                severity=severity, details=details,
                hash_chain=self.policy.audit_hash_chain,
            )
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, -32603, f"audit write failed: {exc}")
            return
        self._log_security_event(
            "audit.worker_event_written",
            details={"event_type": event_type},
        )
        self._respond(msgid, {
            "content": [{"type": "text",
                         "text": json.dumps({"written": event_type})}],
        })


_FEATURES_SERVER_PROD = "https://corvin-features-production.up.railway.app"


def _forge_features_url() -> str:
    """Return the Corvin-Features base URL for compute permit requests.

    CORVIN_FEATURES_URL is accepted ONLY when CORVIN_TEST_MODE=1.
    In production the URL is hardcoded to prevent mock-server redirection
    (ADR-0098 security hardening: closing env-var bypass vectors).
    """
    if os.environ.get("CORVIN_TEST_MODE") == "1":
        override = os.environ.get("CORVIN_FEATURES_URL", "").rstrip("/")
        return override or _FEATURES_SERVER_PROD
    return _FEATURES_SERVER_PROD


def _forge_device_fp() -> str:
    """Compute ADR-0098 device fingerprint from hardware (not from disk)."""
    import hashlib as _hl
    import socket as _s
    import uuid as _u
    try:
        _core_lic = Path(__file__).resolve().parents[3] / "core" / "license"
        import sys as _sys
        if str(_core_lic) not in _sys.path:
            _sys.path.insert(0, str(_core_lic))
        from corvin_license.trial import machine_fingerprint as _mfp  # type: ignore
        machine_fp = _mfp()
    except Exception:
        hostname = "unknown"
        try:
            hostname = _s.gethostname()
        except Exception:
            pass
        machine_fp = _hl.sha256(f"{hostname}:{format(_u.getnode(), '012x')}".encode()).hexdigest()[:32]
    return _hl.sha256(machine_fp.encode()).hexdigest()[:32]


def _request_server_compute_permit(job_id: str, tenant_id: str) -> str:
    """Try to obtain a server-side compute permit (ADR-0095 M3 / ADR-0098 P1).

    Returns one of three literal strings:
      "granted"        — server issued a valid permit (quota check passed).
      "no_credentials" — no features.json / api_key / license_token present;
                         caller treats this as free-tier and uses local counter.
      "server_error"   — credentials exist but the request failed for any reason
                         other than quota-exceeded; caller treats this as fail-closed.

    Raises urllib.error.HTTPError with code 403 when the server explicitly
    rejects the permit (daily quota exceeded) — the caller catches this as a
    hard block.

    ADR-0098 P1: removing the "fall through to local counter" path for paid
    tiers closes the offline-redirect quota-bypass attack vector.
    """
    import hashlib as _hl
    import hmac as _hmac
    import urllib.error
    import urllib.request

    features_url = _forge_features_url()

    xdg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    feat_path = Path(os.path.expanduser(xdg)) / "corvin-voice" / "features.json"
    if not feat_path.exists():
        return "no_credentials"
    try:
        import json as _j
        feat = _j.loads(feat_path.read_text(encoding="utf-8"))
    except Exception:
        return "no_credentials"

    api_key = feat.get("api_key", "")
    license_token = os.environ.get("CORVIN_LICENSE_KEY", "").strip()
    if not license_token:
        corvin_home = Path(
            os.environ.get("CORVIN_HOME", "") or (Path.home() / ".corvin")
        )
        key_file = corvin_home / "global" / "license.key"
        if key_file.exists():
            try:
                license_token = key_file.read_text().strip()
            except OSError:
                pass
    if not api_key or not license_token:
        return "no_credentials"

    # Credentials present — a failed request is now a hard block (fail-closed).
    try:
        import json as _j
        body = _j.dumps({"job_id": job_id or "forge", "tenant_id": tenant_id}).encode()
        ts = str(int(time.time()))
        sig = _hmac.new(api_key.encode(), body + b"." + ts.encode(), _hl.sha256).hexdigest()

        req = urllib.request.Request(
            f"{features_url}/v1/permits/compute",
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {license_token}")
        req.add_header("X-Corvin-Ts", ts)
        req.add_header("X-Corvin-Sig", sig)
        req.add_header("X-Corvin-Device-Fp", _forge_device_fp())

        with urllib.request.urlopen(req, timeout=4) as resp:
            result = _j.loads(resp.read().decode())
            return "granted" if result.get("permit") else "server_error"
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise  # quota exceeded — re-raise so caller emits the right message
        return "server_error"  # other HTTP errors with credentials = fail-closed
    except Exception:
        return "server_error"  # network errors with credentials = fail-closed


def _b64(data: bytes) -> str:
    import base64 as _b
    return _b.b64encode(data).decode("ascii")


def _extract_range(artifacts_root: Path, entry: Any, rng: str) -> dict[str, Any]:
    """Parse ``rng`` and return the slice payload.

    Supported syntaxes (see ADR-0040 / layer-33 ref doc):

    - ``pages:N-M`` — PDFs, via ``pdftotext`` (best-effort).
    - ``lines:N-M`` — text artifacts.
    - ``bytes:N-M`` — raw byte range, base64-encoded.
    - ``meta``     — PDF / image metadata only.
    """
    import base64 as _b
    path = artifacts_root / entry.path_rel
    if rng == "meta":
        return {"name": entry.name, "mime": entry.mime,
                "size": entry.size, "sha256": entry.sha256,
                "tags": entry.tags, "ts": entry.ts}
    if ":" not in rng:
        return {"error": "invalid range syntax"}
    kind, spec = rng.split(":", 1)
    try:
        a_s, b_s = spec.split("-", 1)
        a, b = int(a_s), int(b_s)
    except (ValueError, IndexError):
        return {"error": "range must be N-M"}
    if a < 0 or b < a:
        return {"error": "range out of bounds"}
    if kind == "bytes":
        with path.open("rb") as fh:
            fh.seek(a)
            data = fh.read(min(b - a + 1, 1_048_576))
        return {"name": entry.name, "range": rng,
                "encoding": "base64", "content": _b.b64encode(data).decode()}
    if kind == "lines":
        out_lines: list[str] = []
        with path.open("r", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                if i > b:
                    break
                if i >= a:
                    out_lines.append(line.rstrip("\n"))
        return {"name": entry.name, "range": rng,
                "encoding": "text", "content": "\n".join(out_lines)}
    if kind == "pages":
        import subprocess as _sub
        try:
            r = _sub.run(["pdftotext", "-f", str(a), "-l", str(b),
                          "-layout", str(path), "-"],
                         capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                return {"error": f"pdftotext rc={r.returncode}: "
                                 f"{r.stderr.strip()[:120]}"}
            return {"name": entry.name, "range": rng,
                    "encoding": "text", "content": r.stdout[:65536]}
        except FileNotFoundError:
            return {"error": "pdftotext not installed in container"}
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
    return {"error": f"unsupported range kind: {kind}"}


def main(root: Path) -> int:
    return MCPServer(root).serve()
