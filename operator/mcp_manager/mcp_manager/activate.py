"""MCP Plugin Manager — activation management (ADR-0096 M1/M4).

active.json format (global — user + tenant scopes only)::

    {
      "version": 1,
      "user":    ["brave-search"],
      "tenant":  []
    }

active.json location: <corvin_home>/tenants/<tid>/global/mcp-tools/active.json

Session-scope (M4 ephemeral):
  <corvin_home>/tenants/<tid>/sessions/<session_key>/mcp-session-active.json
  Format: ["tool-a", "tool-b"]   (plain JSON list)
  Purged on session reset via clear_session_scope().

Project-scope (M4):
  <project_dir>/.corvin/mcp-active.json
  Format: ["tool-a", "tool-b"]   (plain JSON list)
  Controlled by CORVIN_PROJECT_DIR env-var; never auto-created by this module.

get_active_mcp_servers() is called per-spawn from adapter.py. It uses an
mtime-based hot-reload cache so the cost on a cache-hit is one stat() syscall.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from . import catalog as _cat
from . import compliance as _compliance

# M4: session + project scopes handled via dedicated files; the global
# active.json retains backward-compatible "session" key but is no longer
# written for session-scope activations.
VALID_SCOPES = ("session", "project", "user", "tenant")

_DEFAULT_ACTIVE: dict[str, Any] = {
    "version": 1,
    "user": [],
    "project": [],
    "session": [],
    "tenant": [],
}

_active_cache: dict[str, dict] = {}  # tid -> {"mtime": float, "data": dict}
_active_lock = threading.RLock()


def active_path(tid: str = "_default") -> Path:
    from .catalog import catalog_dir  # local import avoids circular at module level
    return catalog_dir(tid) / "active.json"


def _session_active_path(tid: str, session_key: str) -> Path:
    """Path for ephemeral session-scope MCP list (M4).

    session_key is the forge channel id: e.g. "discord:123456789".
    """
    home = _cat._corvin_home()
    # Sanitise the session_key to avoid path-traversal.
    safe = session_key.replace("/", "_").replace("..", "_").replace("\\", "_")
    return home / "tenants" / tid / "sessions" / safe / "mcp-session-active.json"


def _project_active_path(project_dir: str) -> Path:
    """Path for project-scope MCP list (M4).

    project_dir is the value of CORVIN_PROJECT_DIR — the git repository root.
    """
    return Path(project_dir) / ".corvin" / "mcp-active.json"


def _load_list_file(path: Path) -> list[str]:
    """Load a plain JSON list from *path*. Returns [] on missing/corrupt."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [str(x) for x in raw]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_list_file(path: Path, items: list[str]) -> None:
    """Atomically write *items* as a JSON list to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def load_active(tid: str = "_default") -> dict[str, Any]:
    """Load the global active.json (user + tenant scopes).

    Uses mtime-based hot-reload cache. The returned dict always contains
    all VALID_SCOPES keys for backward-compat, but "session" and "project"
    will be empty (those scopes live in separate files from M4 onwards).
    """
    path = active_path(tid)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    with _active_lock:
        cached = _active_cache.get(tid)
        if cached is not None and cached["mtime"] == mtime:
            return cached["data"]

    data: dict[str, Any] = {"version": _DEFAULT_ACTIVE["version"]}
    for _scope in VALID_SCOPES:
        data[_scope] = []
    if mtime > 0.0:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for scope in VALID_SCOPES:
                    val = raw.get(scope)
                    if isinstance(val, list):
                        data[scope] = [str(x) for x in val]
        except (json.JSONDecodeError, OSError):
            pass

    with _active_lock:
        _active_cache[tid] = {"mtime": mtime, "data": data}
    return data


def _save_active(tid: str, data: dict[str, Any]) -> None:
    path = active_path(tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    # Invalidate hot-reload cache
    with _active_lock:
        _active_cache.pop(tid, None)


def activate(
    tid: str,
    tool_id: str,
    scope: str = "user",
    *,
    session_key: str | None = None,
    project_dir: str | None = None,
) -> None:
    """Activate *tool_id* for tenant *tid* in the given *scope*.

    M4 routing:
      scope="session" + session_key → write to session-specific JSON file
      scope="project" + project_dir → write to .corvin/mcp-active.json
      scope in ("user", "tenant")   → write to global active.json
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope {scope!r}. Valid: {VALID_SCOPES}")
    entry = _cat.get_tool(tid, tool_id)
    if entry is None:
        raise ValueError(f"Tool {tool_id!r} is not installed for tenant {tid!r}")
    # M2: activation-time compliance check (fail-closed)
    _compliance.check_activation_compliance(entry, tid)

    if scope == "session":
        if not session_key:
            raise ValueError("scope='session' requires session_key")
        path = _session_active_path(tid, session_key)
        items = _load_list_file(path)
        if tool_id not in items:
            items.append(tool_id)
        _save_list_file(path, items)
        return

    if scope == "project":
        if not project_dir:
            raise ValueError("scope='project' requires project_dir")
        path = _project_active_path(project_dir)
        items = _load_list_file(path)
        if tool_id not in items:
            items.append(tool_id)
        _save_list_file(path, items)
        return

    # user / tenant → global active.json
    data = load_active(tid)
    lst = data.setdefault(scope, [])
    if tool_id not in lst:
        lst.append(tool_id)
    _save_active(tid, data)


def deactivate(
    tid: str,
    tool_id: str,
    scope: str = "user",
    *,
    session_key: str | None = None,
    project_dir: str | None = None,
) -> bool:
    """Deactivate *tool_id*. Returns True iff it was active in *scope*.

    M4 routing mirrors activate().
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope {scope!r}. Valid: {VALID_SCOPES}")

    if scope == "session":
        if not session_key:
            raise ValueError("scope='session' requires session_key")
        path = _session_active_path(tid, session_key)
        items = _load_list_file(path)
        if tool_id not in items:
            return False
        items = [x for x in items if x != tool_id]
        _save_list_file(path, items)
        return True

    if scope == "project":
        if not project_dir:
            raise ValueError("scope='project' requires project_dir")
        path = _project_active_path(project_dir)
        items = _load_list_file(path)
        if tool_id not in items:
            return False
        items = [x for x in items if x != tool_id]
        _save_list_file(path, items)
        return True

    # user / tenant → global active.json
    data = load_active(tid)
    lst = data.get(scope, [])
    if tool_id not in lst:
        return False
    data[scope] = [x for x in lst if x != tool_id]
    _save_active(tid, data)
    return True


def clear_session_scope(tid: str, session_key: str) -> None:
    """Delete the ephemeral session-scope activation file (M4).

    Called by session_reset.py during /new /clear /reset.  Best-effort:
    missing file is silently ignored; failures do not raise.
    """
    try:
        path = _session_active_path(tid, session_key)
        path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def get_active_tool_ids(
    tid: str = "_default",
    *,
    session_key: str | None = None,
    project_dir: str | None = None,
) -> list[str]:
    """Return de-duplicated list of active tool IDs across all scopes.

    Merge order (lowest priority first → highest wins via seen-set):
      tenant → user → project → session
    Result: session-scope tools appear first in the de-duped list, which
    ensures the most-local scope wins when the same tool appears in multiple.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(ids: list[str]) -> None:
        for tool_id in ids:
            if tool_id not in seen:
                seen.add(tool_id)
                result.append(tool_id)

    # Global active.json: tenant + user (project/session keys ignored)
    data = load_active(tid)
    _add(data.get("tenant", []))
    _add(data.get("user", []))

    # Project-scope file
    if project_dir:
        _add(_load_list_file(_project_active_path(project_dir)))

    # Session-scope file (ephemeral, highest priority)
    if session_key:
        _add(_load_list_file(_session_active_path(tid, session_key)))

    return result


def get_active_mcp_servers(
    tid: str = "_default",
    *,
    session_key: str | None = None,
    project_dir: str | None = None,
    image_outdir: str | None = None,
) -> dict[str, Any]:
    """Return an mcp_servers dict (ready for cowork materialize_mcp) for all active tools.

    Called per-spawn from adapter._resolve_spawn_inputs; mtime-based cache
    makes this cheap on the hot path. Applies M2 spawn-time compliance filter
    (SHA256, secrets, L34, L35) — non-compliant tools are silently removed and
    a mcp_plugin.spawn_blocked audit event is emitted for each.

    M4: merges session-scope and project-scope activations on top of the
    global (user/tenant) scopes.

    ``image_outdir`` (bug report 2026-07-12: generated images not rendering
    inline in chat): when given, overrides ``CORVIN_IMAGE_OUTDIR`` on the
    ``imagegen-zero-config`` catalog entry's env to this exact, caller-known
    path (the session/chat workdir's ``outputs/`` subdirectory) instead of
    leaving the image server to guess its own write location from
    ``Path.cwd()`` — an implicit cwd-inheritance-through-the-claude-CLI
    assumption this same server's code comments already flag as unverified
    for this exact spawn chain (the identical class of gap that needed an
    explicit ``CORVIN_HOME``/``CORVIN_TENANT_ID`` workaround below). Per-call,
    not cached, since it is session-specific and the mtime cache is keyed
    only on tenant-global state.
    """
    active_ids = get_active_tool_ids(
        tid, session_key=session_key, project_dir=project_dir,
    )
    if not active_ids:
        return {}

    servers: dict[str, Any] = {}
    tool_entries: dict[str, Any] = {}

    for tool_id in active_ids:
        entry = _cat.get_tool(tid, tool_id)
        if entry is None:
            continue
        runtime = entry.get("runtime")
        if not isinstance(runtime, dict) or not runtime.get("command"):
            continue

        server_cfg: dict[str, Any] = {
            "command": runtime["command"],
            "args": list(runtime.get("args") or []),
        }

        # Plaintext env passthrough declared on the catalog entry itself
        # (ADR-0191: CORVIN_HOME / CORVIN_TENANT_ID for builtin servers —
        # an MCP subprocess does not reliably inherit these from its
        # spawning process, and unlike secrets these are non-sensitive
        # literal values, so no ${VAR} template indirection is needed).
        env: dict[str, str] = {}
        runtime_env = runtime.get("env")
        if isinstance(runtime_env, dict):
            env.update({str(k): str(v) for k, v in runtime_env.items()})

        # Build env from secrets: {"SECRET_NAME": "${SECRET_NAME}"}
        # The vault has already injected the values into the process env;
        # the ${VAR} template is resolved by cowork materialize_mcp at spawn time.
        for secret in entry.get("secrets") or []:
            name = secret.get("name")
            if name:
                env[name] = f"${{{name}}}"
        if env:
            server_cfg["env"] = env

        servers[tool_id] = server_cfg
        tool_entries[tool_id] = entry

    if image_outdir and "imagegen-zero-config" in servers:
        servers["imagegen-zero-config"].setdefault("env", {})["CORVIN_IMAGE_OUTDIR"] = image_outdir

    # M2 — spawn-time compliance filter (SHA256, secrets, L34, L35)
    return _compliance.filter_compliant_servers(servers, tool_entries, tid)
