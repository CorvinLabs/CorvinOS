"""Workflow Builder backend routes — ADR-0039 Phases 1-7.

Storage layout (per tenant):
    <tenant_home>/workflows/
        <wid>.awp.yaml          AWP workflow YAML
        <wid>.meta.json         title, description, phase, timestamps
        <wid>.chat.jsonl        design-session conversation history
        <wid>/runs/
            <rid>.meta.json     run metadata (status, started_at, etc.)
            <rid>.jsonl         run event log (not in audit chain — ADR-0039 must-NOT)

Routes (Phases 1-7):
    GET    /workflows                       list
    POST   /workflows                       create
    POST   /workflows/import                import YAML or AWPKG (Phase 5)
    GET    /workflows/{wid}                 get + parsed graph
    PATCH  /workflows/{wid}                 update title/description
    DELETE /workflows/{wid}                 delete
    GET    /workflows/{wid}/yaml            raw YAML text
    PUT    /workflows/{wid}/yaml            replace + validate YAML
    GET    /workflows/{wid}/export.awpkg    export as AWPKG bundle (Phase 5)
    POST   /workflows/{wid}/runs            start run (SSE streaming)
    GET    /workflows/{wid}/runs            list runs
    GET    /workflows/{wid}/runs/{rid}      run detail
    DELETE /workflows/{wid}/runs/{rid}      delete run
    GET    /workflows/{wid}/schedule        get cron schedule
    PUT    /workflows/{wid}/schedule        set + register with corvin-scheduler (Phase 4)
    DELETE /workflows/{wid}/schedule        remove + unregister (Phase 4)
    WS     /workflows/{wid}/chat            guided design assistant + voice TTS (Phase 7)
"""
from __future__ import annotations

import asyncio
import base64

try:
    import fcntl
except ModuleNotFoundError:  # Windows: no stdlib fcntl.
    # This route is normally imported after ``from forge import paths`` has
    # run forge/__init__ → _wincompat.install() (which seeds a no-op fcntl
    # stub into sys.modules). But when this module is imported in ISOLATION
    # on Windows, that hasn't happened yet — so seed the SAME stub here,
    # using the same mechanism, before the flock() call sites below.
    import sys as _sys
    from pathlib import Path as _Path

    # parents[4] == repo root (this file is core/console/corvin_console/routes/…);
    # mirrors the _REPO = _THIS_DIR.parents[3] resolution used below.
    _forge_pkg = _Path(__file__).resolve().parents[4] / "operator" / "forge"
    if str(_forge_pkg) not in _sys.path:
        _sys.path.insert(0, str(_forge_pkg))
    from forge import _wincompat as _wc

    _wc.install()
    import fcntl  # now resolves to the seeded no-op stub (flock/LOCK_* are inert)
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

import anyio

_log = logging.getLogger(__name__)

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status as http_status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from .. import _spawn_gates  # shared fail-closed pre-spawn chokepoint (CRITICAL compliance)
from ..deps import require_csrf, require_session
from ..utils import read_json_or_none as _read_json

# ── Path resolution ────────────────────────────────────────────────────────

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402

# ── License gate (soft dep) ───────────────────────────────────────────────
_OPERATOR = _REPO / "operator"
if str(_OPERATOR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR))
try:
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
    from license.validator import assert_limit as _lic_assert  # type: ignore[import]
    from license.limits import LicenseLimitError as _LicLimitError  # type: ignore[import]
    _WF_LIC_OK = True
except ImportError:
    try:
        from license.limits import FREE_TIER as _FREE_TIER, LicenseLimitError as _LicLimitError  # type: ignore[import]
    except ImportError:
        _FREE_TIER: dict = {}
        class _LicLimitError(Exception): pass  # type: ignore[misc]
    _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]
    def _lic_assert(feature: str, requested: int = 1, **_kw: object) -> None:  # type: ignore[assignment,misc]
        _limit = _FREE_TIER.get(feature)
        if _limit is not None and isinstance(_limit, int) and requested > _limit:
            raise _LicLimitError(feature, requested, _limit)
    _WF_LIC_OK = False

# ── AWP stack (soft dep) ───────────────────────────────────────────────────

_AWP_PATH = _REPO / "core" / "workflows"
if str(_AWP_PATH) not in sys.path:
    sys.path.insert(0, str(_AWP_PATH))

try:
    from corvin_workflows import load_workflow as _load_workflow  # noqa: E402
    from corvin_workflows import validate as _validate_workflow   # noqa: E402
    from corvin_workflows import WorkflowInvalid                  # noqa: E402
    _AWP_OK = True
except Exception:
    _load_workflow = None     # type: ignore[assignment]
    _validate_workflow = None  # type: ignore[assignment]
    class WorkflowInvalid(Exception):  # type: ignore[no-redef]
        pass
    _AWP_OK = False

# ── PyYAML (soft dep) ──────────────────────────────────────────────────────

try:
    import yaml as _yaml
    _YAML_OK = True
except ImportError:
    _yaml = None  # type: ignore[assignment]
    _YAML_OK = False

# ── corvin-scheduler (Phase 4, soft dep) ─────────────────────────────────

_SCHEDULER_PATH = _REPO / "operator" / "bridges" / "shared"
if str(_SCHEDULER_PATH) not in sys.path:
    sys.path.insert(0, str(_SCHEDULER_PATH))

try:
    import scheduler as _sched  # noqa: E402
    _SCHEDULER_OK = True
except Exception:
    _sched = None  # type: ignore[assignment]
    _SCHEDULER_OK = False

# ── AWPKG builder (Phase 5, soft dep) ─────────────────────────────────────

_AWPKG_LIB = _REPO / "core" / "awpkg"
if str(_AWPKG_LIB) not in sys.path:
    sys.path.insert(0, str(_AWPKG_LIB))

try:
    from awpkg.builder import build_from_dict as _awpkg_build  # noqa: E402
    from awpkg.builder import BuildError as _AwpkgBuildError    # noqa: E402
    _AWPKG_OK = True
except Exception:
    _awpkg_build = None  # type: ignore[assignment]
    class _AwpkgBuildError(Exception):  # type: ignore[no-redef]
        pass
    _AWPKG_OK = False

# ── Voice scripts path (Phase 7, soft dep) ────────────────────────────────

_VOICE_SCRIPTS = _REPO / "operator" / "voice" / "scripts"

# ── Constants ──────────────────────────────────────────────────────────────

_WID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RID_BYTES = 8
_MAX_YAML_BYTES = 256 * 1024   # 256 KiB
_MAX_CHAT_MSG_CHARS = 4000

router = APIRouter()

# ── Storage helpers ────────────────────────────────────────────────────────

def _workflows_dir(tenant_id: str) -> Path:
    return _forge_paths.tenant_home(tenant_id) / "workflows"

def _yaml_path(tenant_id: str, wid: str) -> Path:
    return _workflows_dir(tenant_id) / f"{wid}.awp.yaml"

def _meta_path(tenant_id: str, wid: str) -> Path:
    return _workflows_dir(tenant_id) / f"{wid}.meta.json"

def _chat_path(tenant_id: str, wid: str) -> Path:
    return _workflows_dir(tenant_id) / f"{wid}.chat.jsonl"

def _runs_dir(tenant_id: str, wid: str) -> Path:
    return _workflows_dir(tenant_id) / wid / "runs"

def _run_meta_path(tenant_id: str, wid: str, rid: str) -> Path:
    return _runs_dir(tenant_id, wid) / f"{rid}.meta.json"

def _run_log_path(tenant_id: str, wid: str, rid: str) -> Path:
    return _runs_dir(tenant_id, wid) / f"{rid}.jsonl"

def _approval_path(tenant_id: str, wid: str, rid: str) -> Path:
    return _runs_dir(tenant_id, wid) / f"{rid}.approval.json"

def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def _count_running_workflows(tenant_id: str) -> int:
    """Count workflow runs with status='running' across all workflows for a tenant.

    Used to enforce the workflows_concurrent licence limit in start_run().
    WF-CONC-02 (ADR-0148): individual unreadable run-meta files are skipped
    (bounded under-count of at most that file), but a failure to enumerate the
    runs tree at all RE-RAISES so the caller's gate denies fail-closed — returning
    0 there would under-count to zero and slip a run past workflows_concurrent.
    """
    wf_root = _workflows_dir(tenant_id)
    if not wf_root.exists():
        return 0
    count = 0
    try:
        for wf_dir in wf_root.iterdir():
            runs_dir = wf_dir / "runs"
            if not runs_dir.is_dir():
                continue
            for meta_file in runs_dir.glob("*.meta.json"):
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    if meta.get("status") == "running":
                        count += 1
                except Exception:
                    pass  # one unreadable meta file: bounded under-count, skip it
    except Exception:
        # WF-CONC-02: cannot enumerate the runs tree — re-raise so the caller's
        # gate denies (fail-closed) instead of granting unlimited concurrency.
        raise
    return count

def _write_atomic(path: Path, data: dict[str, Any] | str) -> None:
    _ensure_dir(path.parent)
    raw = (data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False)) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def _append_chat_line(tenant_id: str, wid: str, line: dict[str, Any]) -> None:
    """Append a JSON line atomically with file-level locking to prevent
    concurrent appends from interleaving mid-line. Tier 1 fix: fcntl locking prevents JSONL corruption."""
    import fcntl

    path = _chat_path(tenant_id, wid)
    lock_path = path.with_suffix(".append.lock")
    _ensure_dir(path.parent)

    # Acquire exclusive lock to ensure atomic append
    with open(lock_path, 'w') as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

def _read_chat(tenant_id: str, wid: str) -> list[dict[str, Any]]:
    path = _chat_path(tenant_id, wid)
    if not path.exists():
        return []
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            try:
                lines.append(json.loads(raw))
            except Exception:
                pass
    return lines

def _validate_wid(wid: str) -> str:
    if not _WID_RE.match(wid):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, f"invalid workflow id: {wid!r}")
    return wid

def _require_workflow(tenant_id: str, wid: str) -> dict[str, Any]:
    meta = _read_json(_meta_path(tenant_id, wid))
    if meta is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "workflow not found")
    return meta

def _parse_graph(yaml_text: str) -> list[dict[str, Any]]:
    """Extract graph nodes from AWP YAML for the canvas (best-effort)."""
    if not _YAML_OK or not yaml_text.strip():
        return []
    try:
        parsed = _yaml.safe_load(yaml_text) or {}
        graph = parsed.get("orchestration", {}).get("graph", [])
        if not isinstance(graph, list):
            return []
        return [
            {
                "id": str(n.get("id", f"node_{i}")),
                "type": str(n.get("type", "agent")),
                "depends_on": list(n.get("depends_on", []) or []),
                "agent": n.get("agent"),
                "instructions": str(n.get("instructions", ""))[:120],
                "tools": list(n.get("tools") or []),
                "forge_tools": list(n.get("forge_tools") or []),
                "skills": list(n.get("skills") or []),
                "items_from": n.get("items_from"),
                "config": n.get("config"),
            }
            for i, n in enumerate(graph)
            if isinstance(n, dict)
        ]
    except Exception:
        return []

def _topo_sort(graph: list[dict[str, Any]]) -> list[str]:
    """Return node IDs in topological execution order (Kahn's algorithm)."""
    ids = [n["id"] for n in graph if isinstance(n, dict) and "id" in n]
    deps: dict[str, list[str]] = {}
    for n in graph:
        if isinstance(n, dict) and "id" in n:
            deps[n["id"]] = [d for d in (n.get("depends_on") or []) if d in ids]
    in_degree = {nid: 0 for nid in ids}
    for nid, ndeps in deps.items():
        in_degree[nid] = len(ndeps)
    queue = [nid for nid, d in in_degree.items() if d == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for other in ids:
            if nid in deps.get(other, []):
                in_degree[other] -= 1
                if in_degree[other] == 0:
                    queue.append(other)
    # Append any cycle nodes that weren't reached (cycle guard)
    for nid in ids:
        if nid not in order:
            order.append(nid)
    return order

def _run_node_claude(prompt: str, mcp_config: dict | None = None) -> str:
    """Execute a single workflow node via claude -p and return the text output.

    When mcp_config is provided (non-empty mcpServers), writes it to a
    temp file and passes --mcp-config so the session MCP tools are available.
    Even an empty mcpServers dict triggers MCP loading in the subprocess.
    """
    cmd = ["claude", "-p", "--output-format", "text"]
    tmp_path: str | None = None

    # Always write a config file when any tools are requested — this activates
    # all session MCP tools (Gmail, Drive, etc.) in the subprocess.
    if mcp_config is not None:
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="wf_mcp_")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(mcp_config, fh)
            cmd.append(f"--mcp-config={tmp_path}")
        except Exception:
            tmp_path = None

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return f"[error rc={result.returncode}] {result.stderr.strip()[:400]}"
    except FileNotFoundError:
        return "[claude CLI not found]"
    except subprocess.TimeoutExpired:
        return "[timeout after 180s]"
    except Exception as exc:
        return f"[exception: {exc}]"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

def _build_mcp_with_forge(
    tenant_id: str,
    node_tools: list[str],
    forge_tools: list[str],
) -> dict[str, Any]:
    """Build MCP config combining connector tools and forge MCP server."""
    try:
        from .connectors import build_mcp_config_for_node
        cfg = build_mcp_config_for_node(tenant_id, node_tools)
    except Exception:
        cfg = {"mcpServers": {}}

    if forge_tools:
        # Add the forge stdio MCP server so forge tools are available
        forge_mcp_py = str(_REPO / "operator" / "forge" / "forge" / "mcp_server.py")
        cfg["mcpServers"]["__forge__"] = {
            "command": sys.executable,
            "args": [forge_mcp_py],
            "env": {
                "PYTHONPATH": str(_REPO / "operator" / "forge"),
                "CORVIN_HOME": str(_forge_paths.corvin_home()),
            },
        }
    return cfg

def _read_skill_content(skill_name: str) -> str | None:
    """Read a skill's SKILL.md body from the slot mirror or skill-forge directory."""
    slug = re.sub(r"[^a-z0-9_-]", "_", skill_name.lower())
    candidates = [
        _REPO / "operator" / "skill-forge" / "skills" / "dyn" / slug / "SKILL.md",
        _forge_paths.tenant_skill_forge_dir("_default") / "user" / slug / "SKILL.md",
        _forge_paths.tenant_skill_forge_dir("_default") / "project" / slug / "SKILL.md",
    ]
    for p in candidates:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")[:4096]
            except Exception:
                pass
    return None

def _run_delegation_loop_node(
    node: dict[str, Any],
    state: dict[str, Any],
    mcp_config: dict | None,
    tenant_id: str = "_default",
    node_id: str = "delegation_loop",
    wid: str = "",
) -> str:
    """Execute a delegation_loop node: manager issues DELEGATE/COMPLETE decisions.

    Round-4 finding #1: each manager + worker spawn is a distinct `claude -p`
    process driven by LLM-derived text, so each is gated fail-closed +
    audit-first via the shared console chokepoint BEFORE it spawns. A deny
    aborts the loop with a refusal string (surfaced as the node output)."""
    cfg = node.get("config") or {}
    budget = cfg.get("budget") or {}
    max_loops = int(budget.get("max_loops", 3))
    max_workers = int(budget.get("max_total_workers", 6))
    manager_agent = cfg.get("manager", "assistant")
    instructions = str(node.get("instructions", "Complete the task."))

    # Build upstream context
    deps = node.get("depends_on") or []
    upstream = []
    for dep in deps:
        dv = state.get(dep)
        if isinstance(dv, dict):
            upstream.append(str(dv.get("output", "")))
        elif dv is not None:
            upstream.append(str(dv))
    context_text = "\n\n".join(p for p in upstream if p)

    iterations: list[dict[str, Any]] = []
    workers_spawned = 0

    for it in range(1, max_loops + 1):
        history = ""
        if iterations:
            history_parts = []
            for prev in iterations:
                history_parts.append(f"Iteration {prev['iteration']}:")
                for wr in prev.get("workers", []):
                    history_parts.append(f"  Worker result: {str(wr.get('output',''))[:500]}")
            history = "\n".join(history_parts)

        manager_prompt = f"""You are coordinating work on this task.

TASK: {instructions}

UPSTREAM DATA:
{context_text or "(none)"}

PRIOR ITERATIONS:
{history or "(first iteration)"}

AVAILABLE WORKERS (agent: {manager_agent})

Decide what to do. Output EXACTLY ONE of these JSON structures — no other text:

If you need more work done:
{{"decision": "DELEGATE", "workers": [{{"agent": "{manager_agent}", "instructions": "specific task"}}]}}

If the task is complete:
{{"decision": "COMPLETE", "result": "your final answer"}}
"""
        _mgr_refusal = _spawn_gates.check_console_spawn_or_refusal(
            manager_prompt, tenant_id=tenant_id, persona="assistant",
            channel="workflow", chat_key=f"workflow:{wid}:{node_id}:manager",
            engine_id="claude_code",
        )
        if _mgr_refusal is not None:
            return _mgr_refusal
        manager_raw = _run_node_claude(manager_prompt, mcp_config)

        # Parse manager JSON
        decision_json: dict[str, Any] = {}
        for line in manager_raw.splitlines():
            line = line.strip()
            if line.startswith("{") and "decision" in line:
                try:
                    decision_json = json.loads(line)
                    break
                except Exception:
                    pass
        if not decision_json:
            # Try full response as JSON
            try:
                decision_json = json.loads(manager_raw.strip())
            except Exception:
                # Heuristic: treat response as COMPLETE
                decision_json = {"decision": "COMPLETE", "result": manager_raw}

        decision = decision_json.get("decision", "COMPLETE")

        if decision == "COMPLETE":
            result = str(decision_json.get("result", manager_raw))
            return result

        elif decision == "DELEGATE":
            workers_def = decision_json.get("workers", [])
            if not workers_def:
                # Nothing to delegate — treat as COMPLETE
                return str(decision_json.get("result", "Task completed."))

            worker_results = []
            for wd in workers_def[:max(1, max_workers - workers_spawned)]:
                if workers_spawned >= max_workers:
                    break
                worker_instructions = str(wd.get("instructions", ""))
                worker_prompt = f"""Execute this task:
{worker_instructions}

Context:
{context_text or "(none)"}
"""
                _wkr_refusal = _spawn_gates.check_console_spawn_or_refusal(
                    worker_prompt, tenant_id=tenant_id, persona="assistant",
                    channel="workflow",
                    chat_key=f"workflow:{wid}:{node_id}:worker",
                    engine_id="claude_code",
                )
                if _wkr_refusal is not None:
                    return _wkr_refusal
                worker_out = _run_node_claude(worker_prompt, mcp_config)
                worker_results.append({"instructions": worker_instructions, "output": worker_out})
                workers_spawned += 1

            iterations.append({
                "iteration": it,
                "manager": decision_json,
                "workers": worker_results,
            })
        else:
            # Unknown decision — abort
            return f"[delegation_loop] unknown decision: {decision}"

    # Budget exhausted
    all_worker_outputs = [
        wr.get("output", "")
        for it_data in iterations
        for wr in it_data.get("workers", [])
    ]
    return "\n\n".join(filter(None, all_worker_outputs)) or "[delegation_loop: budget exhausted]"

def _run_fan_out_node(
    node: dict[str, Any],
    state: dict[str, Any],
    mcp_config: dict | None,
    tenant_id: str = "_default",
    node_id: str = "fan_out",
    wid: str = "",
) -> str:
    """Execute a fan_out node: run the same agent over N items from state.

    Round-4 finding #1: each per-item spawn is a distinct `claude -p` driven by
    item-substituted (data-derived) text, so each is gated fail-closed +
    audit-first via the shared console chokepoint BEFORE it spawns."""
    items_from_field = str(node.get("items_from", "")).strip()
    instructions_template = str(node.get("instructions", "Process: {item}"))
    max_items = 10  # safety cap

    # Resolve items from state
    items: list[str] = []
    if items_from_field:
        parts = items_from_field.split(".")
        source_val = state.get(parts[0], {})
        if isinstance(source_val, dict):
            raw = source_val.get(parts[1] if len(parts) > 1 else "output", "")
        else:
            raw = str(source_val)
        # Parse as newline- or comma-separated list
        if raw:
            lines = [l.strip().lstrip("•-* ").strip() for l in raw.splitlines()]
            items = [l for l in lines if l][:max_items]
    else:
        # Fall back to upstream node outputs
        for dep in (node.get("depends_on") or []):
            dv = state.get(dep)
            if isinstance(dv, dict):
                raw = dv.get("output", "")
                lines = [l.strip().lstrip("•-* ").strip() for l in str(raw).splitlines()]
                items = [l for l in lines if l][:max_items]
                if items:
                    break

    if not items:
        return "[fan_out] no items found to process"

    results: list[str] = []
    for item in items:
        prompt = instructions_template.replace("{item}", item)
        _item_refusal = _spawn_gates.check_console_spawn_or_refusal(
            prompt, tenant_id=tenant_id, persona="assistant",
            channel="workflow", chat_key=f"workflow:{wid}:{node_id}:fanout",
            engine_id="claude_code",
        )
        if _item_refusal is not None:
            results.append(f"• {item}:\n  {_item_refusal}")
            continue
        out = _run_node_claude(prompt, mcp_config)
        results.append(f"• {item}:\n  {out.strip()}")

    return "\n\n".join(results)

def _execute_deliver_node(node: dict[str, Any], state: dict[str, Any]) -> str:
    """Write upstream output to the bridge outbox (console runner path).

    Returns a human-readable status string that becomes the node output.
    """
    cfg = node.get("config") or {}
    channel = str(cfg.get("channel", "discord"))
    chat_id = str(cfg.get("chat_id", "auto"))

    # Resolve chat_id="auto" from state trigger context
    if chat_id == "auto":
        chat_id = str(state.get("__trigger_chat_id__", ""))
        if not chat_id:
            return "[deliver] skipped — chat_id=auto but no trigger context available"

    # Resolve channel name "#general" → Discord channel ID
    if chat_id.startswith("#") and channel == "discord":
        try:
            from .connectors import resolve_channel_name
            resolved = resolve_channel_name(chat_id, channel)
            if resolved:
                chat_id = resolved
            else:
                return f"[deliver] skipped — could not resolve Discord channel '{chat_id}'"
        except Exception as e:
            return f"[deliver] skipped — channel resolution failed: {e}"

    # Collect text from upstream nodes
    deps = node.get("depends_on") or []
    text_parts: list[str] = []
    for dep in deps:
        dep_val = state.get(dep)
        if isinstance(dep_val, dict):
            text_parts.append(str(dep_val.get("output", dep_val.get("text", ""))))
        elif dep_val is not None:
            text_parts.append(str(dep_val))
    text = "\n\n".join(p for p in text_parts if p).strip()

    if not text:
        return "[deliver] skipped — no upstream output to deliver"

    # Write to shared outbox
    outbox_dir = _REPO / "operator" / "bridges" / "shared" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)

    tok = secrets.token_hex(6)
    envelope: dict[str, Any] = {
        "channel": channel,
        "chat_id": chat_id,
        "text": text[:4000],
        "_workflow_deliver": True,
        "ts": int(time.time() * 1000),
    }

    # Voice note — generate TTS and attach as audio file
    voice_note = bool(cfg.get("voice", False))
    voice_status = ""
    if voice_note:
        say_script = _VOICE_SCRIPTS / "say.py"
        if say_script.exists():
            voice_path = outbox_dir / f"wf_{tok}.ogg"
            # Truncate to ~60s speech (~600 words / ~3000 chars)
            tts_text = text[:3000]
            try:
                result = subprocess.run(
                    [sys.executable, str(say_script), str(voice_path), tts_text],
                    capture_output=True,
                    timeout=60,
                )
                if result.returncode == 0 and voice_path.exists() and voice_path.stat().st_size > 0:
                    envelope["voice_path"] = str(voice_path)
                    voice_status = " + voice note"
                else:
                    voice_status = " (voice note skipped — TTS unavailable)"
            except Exception as exc:
                voice_status = f" (voice note failed: {exc})"
        else:
            voice_status = " (voice note skipped — say.py not found)"

    fname = f"wf_{tok}.json"
    (outbox_dir / fname).write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    preview = text[:80].replace("\n", " ")
    return f"[deliver] sent to {channel}/{chat_id} ({len(text)} chars){voice_status}: {preview}…"

def _validate_yaml_str(raw: str) -> None:
    """Parse + validate AWP YAML. Raises HTTPException on failure.

    Draft tolerance: a workflow in the discovering/structuring phase
    legitimately has an empty ``orchestration.graph`` — nodes are built
    incrementally via the design chat. The terminal AWP validator
    enforces R5 ("graph must contain at least one node"), which would
    reject the very default document this module generates on create
    (an empty-graph save could never round-trip). So while the graph is
    empty we treat the document as a draft and skip the terminal
    validator; YAML well-formedness is always checked, and full AWP
    validation applies the moment nodes exist. The run path
    (``_stream_run``) parses independently and is unaffected.
    """
    if not _YAML_OK:
        return  # can't validate without PyYAML; allow through
    try:
        parsed = _yaml.safe_load(raw)
    except Exception as exc:
        _log.error("YAML parsing failed", exc_info=True)
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "Invalid workflow YAML syntax") from exc
    if not _AWP_OK:
        return
    orch = parsed.get("orchestration") if isinstance(parsed, dict) else None
    graph = orch.get("graph") if isinstance(orch, dict) else None
    if not graph:
        return  # draft state — defer terminal validation until nodes exist
    fd, tmp = tempfile.mkstemp(suffix=".awp.yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
        try:
            doc = _load_workflow(tmp)
            _validate_workflow(doc)
        except WorkflowInvalid as exc:
            _log.error("Workflow validation failed", exc_info=True)
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "Workflow validation failed — please check your AWP structure") from exc
        except Exception as exc:
            _log.error("Workflow processing error", exc_info=True)
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "Workflow processing failed") from exc
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

# ── Pydantic models ────────────────────────────────────────────────────────

class CreateWorkflowRequest(BaseModel):
    id: str = Field(..., pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    title: str = Field("", max_length=120)
    description: str = Field("", max_length=1000)
    yaml: str | None = Field(None, max_length=_MAX_YAML_BYTES)
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}

class PatchWorkflowRequest(BaseModel):
    title: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=1000)
    model_config = {"extra": "forbid"}

class UpdateYamlRequest(BaseModel):
    yaml: str = Field(..., max_length=_MAX_YAML_BYTES)
    model_config = {"extra": "forbid"}

class StartRunRequest(BaseModel):
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        max_items=100,  # Max 100 input parameters to prevent DOS
    )
    dry_run: bool = False
    model_config = {"extra": "forbid"}

class SetScheduleRequest(BaseModel):
    cron: str = Field(..., max_length=100)
    timezone: str = Field("UTC", max_length=64)
    overrun: str = Field("skip", pattern=r"^(skip|queue|parallel)$")
    model_config = {"extra": "forbid"}

class ApproveRunRequest(BaseModel):
    comment: str = Field("", max_length=2000)
    model_config = {"extra": "forbid"}

# ── Routes: CRUD ──────────────────────────────────────────────────────────

@router.get("/workflows")
def list_workflows(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    base = _workflows_dir(rec.tenant_id)
    if not base.exists():
        return {"tenant_id": rec.tenant_id, "count": 0, "workflows": []}
    items = []
    for mf in sorted(base.glob("*.meta.json")):
        data = _read_json(mf)
        if data:
            items.append(data)
    items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return {"tenant_id": rec.tenant_id, "count": len(items), "workflows": items}

def _count_existing_workflows(tenant_id: str) -> int:
    """Count valid workflow meta files for the tenant.

    Uses the same falsy filter as list_workflows() (`if data:`) so that any
    .meta.json that decodes as an empty/falsy value ({}, [], 0, false, or
    corrupt JSON) is excluded from both the frontend list AND the enforcement
    count — keeping them in sync.  Fail-closed: I/O errors propagate to the
    caller rather than returning 0 (which would grant unlimited slots).
    """
    base = _workflows_dir(tenant_id)
    if not base.exists():
        return 0
    return sum(1 for mf in base.glob("*.meta.json") if _read_json(mf))

@contextmanager
def _wf_create_lock(tenant_id: str):
    """Per-tenant advisory fcntl.LOCK_EX for the workflow create/import critical section.

    Uses fcntl (not threading.Lock) so the guard works across multiple uvicorn
    worker processes — advisory, so callers must cooperate.  Holds the lock
    from the workflows_max count read through the file write to prevent the
    TOCTOU race where two concurrent requests both observe count=0 and both
    pass _lic_assert before either commits its meta file.
    """
    lock_path = _forge_paths.tenant_home(tenant_id) / ".wf_create.lock"
    _ensure_dir(lock_path.parent)
    with open(lock_path, "a") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

def _enforce_workflows_max(tenant_id: str, rec: session_auth.SessionRecord) -> None:
    """Enforce the workflows_max limit. Must be called inside _wf_create_lock.

    Short-circuits for unlimited tiers (limit is None) to avoid the glob cost.
    Raises HTTPException(402) on limit exceeded. Fail-closed per ADR-0094:
    any I/O error during limit check is treated as a limit enforcement failure
    (not fail-open to unlimited).
    """
    if not _WF_LIC_OK:
        # License module unavailable — enforce free-tier limit via the stub
        # _lic_get_limit which reads FREE_TIER directly from license.limits.
        _free_max = _lic_get_limit("workflows_max")
        if _free_max is not None:
            existing = _count_existing_workflows(tenant_id)
            if existing >= _free_max:
                raise HTTPException(
                    status_code=http_status.HTTP_402_PAYMENT_REQUIRED,
                    detail={
                        "error": "license_limit",
                        "feature": "workflows_max",
                        "current": existing,
                        "limit": _free_max,
                        "upgrade_url": "https://corvin-labs.com/pricing",
                        "msg": f"Free tier: maximum {_free_max} workflow(s).",
                    },
                )
        return
    limit = _lic_get_limit("workflows_max")
    if limit is None:
        return  # unlimited tier — skip the filesystem glob entirely
    existing = _count_existing_workflows(tenant_id)
    try:
        _lic_assert("workflows_max", existing + 1)
    except _LicLimitError as exc:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="workflow.create",
            target_kind="workflow",
            target_id="pending",
            reason="license_limit_exceeded",
        )
        raise HTTPException(
            status_code=http_status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "license_limit",
                "feature": "workflows_max",
                "current": existing,
                "limit": limit,
                "upgrade_url": "https://corvin-labs.com/pricing",
                "msg": str(exc),
            },
        ) from exc

@router.post("/workflows")
def create_workflow(
    body: CreateWorkflowRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    wid = body.id

    # Build content before the lock (lock scope stays minimal).
    title = body.title or wid
    desc = body.description or ""
    initial_yaml = body.yaml or (
        f'awp: "1.0.0"\n'
        f'workflow:\n'
        f'  name: {wid}\n'
        f'  description: "{desc or title}"\n'
        f'orchestration:\n'
        f'  engine: dag\n'
        f'  graph: []\n'
    )

    # ADR-0094: 409 check → YAML validation → workflows_max → write, all under
    # a per-tenant fcntl lock so count-read and file-write are atomic.
    # YAML validation is inside the lock so duplicate-ID takes priority over
    # a validation error (409 before 400/422).
    with _wf_create_lock(rec.tenant_id):
        if _meta_path(rec.tenant_id, wid).exists():
            raise HTTPException(http_status.HTTP_409_CONFLICT, "workflow already exists")

        if body.yaml:
            _validate_yaml_str(initial_yaml)

        _enforce_workflows_max(rec.tenant_id, rec)

        now = time.time()
        meta: dict[str, Any] = {
            "id": wid,
            "title": title,
            "description": desc,
            "phase": "discovering",
            "created_at": now,
            "updated_at": now,
            "has_schedule": False,
        }
        _ensure_dir(_workflows_dir(rec.tenant_id))
        _write_atomic(_yaml_path(rec.tenant_id, wid), initial_yaml)
        _write_atomic(_meta_path(rec.tenant_id, wid), meta)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.created",
        target_kind="workflow",
        target_id=wid,
    )
    return {"ok": True, "workflow": meta}

@router.get("/workflows/{wid}")
def get_workflow(
    wid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)
    yaml_p = _yaml_path(rec.tenant_id, wid)
    yaml_text = yaml_p.read_text(encoding="utf-8") if yaml_p.exists() else ""
    graph = _parse_graph(yaml_text)
    chat = _read_chat(rec.tenant_id, wid)
    return {
        "workflow": meta,
        "yaml": yaml_text,
        "graph": graph,
        "chat": chat[-50:],
    }

@router.patch("/workflows/{wid}")
def patch_workflow(
    wid: str,
    body: PatchWorkflowRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)
    if body.title is not None:
        meta["title"] = body.title
    if body.description is not None:
        meta["description"] = body.description
    meta["updated_at"] = time.time()
    _write_atomic(_meta_path(rec.tenant_id, wid), meta)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.updated",
        target_kind="workflow",
        target_id=wid,
    )
    return {"ok": True, "workflow": meta}

@router.delete("/workflows/{wid}")
def delete_workflow(
    wid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)
    # Unregister from corvin-scheduler (Phase 4) before deleting files.
    if _SCHEDULER_OK and meta.get("schedule_task_id"):
        try:
            _sched.remove_task(meta["schedule_task_id"])
        except Exception:
            pass
    base = _workflows_dir(rec.tenant_id)
    for suffix in (".awp.yaml", ".meta.json", ".chat.jsonl"):
        p = base / f"{wid}{suffix}"
        if p.exists():
            p.unlink()
    run_dir = base / wid
    if run_dir.exists():
        shutil.rmtree(run_dir)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.deleted",
        target_kind="workflow",
        target_id=wid,
    )
    return {"ok": True, "id": wid}

# ── Routes: YAML ──────────────────────────────────────────────────────────

@router.get("/workflows/{wid}/yaml")
def get_workflow_yaml(
    wid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    _validate_wid(wid)
    _require_workflow(rec.tenant_id, wid)
    yaml_p = _yaml_path(rec.tenant_id, wid)
    return {"yaml": yaml_p.read_text(encoding="utf-8") if yaml_p.exists() else ""}

@router.put("/workflows/{wid}/yaml")
def put_workflow_yaml(
    wid: str,
    body: UpdateYamlRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)
    _validate_yaml_str(body.yaml)
    _write_atomic(_yaml_path(rec.tenant_id, wid), body.yaml)
    _write_awpkg_sidecar(rec.tenant_id, wid)
    meta["updated_at"] = time.time()
    _write_atomic(_meta_path(rec.tenant_id, wid), meta)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.updated",
        target_kind="workflow",
        target_id=wid,
    )
    return {"ok": True, "id": wid, "graph": _parse_graph(body.yaml)}

# ── AWPKG sidecar writer ──────────────────────────────────────────────────

def _write_awpkg_sidecar(tenant_id: str, wid: str) -> None:
    """Write/update {wid}.awpkg next to the YAML after every YAML change.

    Best-effort: any failure is logged at DEBUG and silently suppressed so
    that a ZIP-write failure never blocks the primary YAML write.
    """
    try:
        yaml_p = _yaml_path(tenant_id, wid)
        if not yaml_p.exists():
            return
        yaml_text = yaml_p.read_text(encoding="utf-8")
        meta = _read_json(_meta_path(tenant_id, wid)) or {}
        manifest_dict: dict[str, Any] = {
            "awpkg": "1.0",
            "id": f"com.corvin.{wid.replace('_', '-')}",
            "name": meta.get("title", wid),
            "version": "0.1.0",
            "description": meta.get("description", "") or "",
            "components": {"workflows": [f"workflows/{wid}.awp.yaml"]},
            "permissions": {"network": False, "compute": False, "secrets": []},
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if _YAML_OK:
                manifest_bytes = _yaml.dump(
                    manifest_dict, allow_unicode=True, default_flow_style=False
                ).encode("utf-8")
            else:
                manifest_bytes = json.dumps(
                    manifest_dict, indent=2, ensure_ascii=False
                ).encode("utf-8")
            zf.writestr("manifest.yaml", manifest_bytes)
            zf.writestr(f"workflows/{wid}.awp.yaml", yaml_text.encode("utf-8"))
        pkg_bytes = buf.getvalue()
        pkg_path = _workflows_dir(tenant_id) / f"{wid}.awpkg"
        fd, tmp = tempfile.mkstemp(
            prefix=wid + ".", suffix=".awpkg", dir=str(pkg_path.parent)
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(pkg_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, pkg_path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        _log.debug("awpkg sidecar write failed for %s/%s", tenant_id, wid, exc_info=True)

# ── Phase 5: AWPKG helpers ────────────────────────────────────────────────

def _collect_workflow_refs(
    yaml_text: str,
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[str], list[str]]:
    """Parse workflow YAML and return tool/skill refs.

    Returns:
        agent_tools   : {agent_id: [tool_name, ...]}   — per node
        agent_skills  : {agent_id: [skill_name, ...]}  — per node
        all_tools     : flat deduplicated list of tool names
        all_skills    : flat deduplicated list of skill names
    """
    agent_tools: dict[str, list[str]] = {}
    agent_skills: dict[str, list[str]] = {}
    all_tools: list[str] = []
    all_skills: list[str] = []
    seen_tools: set[str] = set()
    seen_skills: set[str] = set()

    if not _YAML_OK:
        return agent_tools, agent_skills, all_tools, all_skills

    try:
        doc = _yaml.safe_load(yaml_text) or {}
    except Exception:
        return agent_tools, agent_skills, all_tools, all_skills

    graph = (doc.get("orchestration") or {}).get("graph") or []
    for node in graph:
        if not isinstance(node, dict):
            continue
        agent_id = str(node.get("agent") or node.get("id") or "")
        node_tools = node.get("forge_tools") or node.get("tools") or []
        node_skills = node.get("skills") or []
        if isinstance(node_tools, str):
            node_tools = [node_tools]
        if isinstance(node_skills, str):
            node_skills = [node_skills]
        node_tools = [str(t) for t in node_tools if t]
        node_skills = [str(s) for s in node_skills if s]

        if node_tools:
            agent_tools.setdefault(agent_id, []).extend(node_tools)
        if node_skills:
            agent_skills.setdefault(agent_id, []).extend(node_skills)

        for t in node_tools:
            if t not in seen_tools:
                seen_tools.add(t)
                all_tools.append(t)
        for s in node_skills:
            if s not in seen_skills:
                seen_skills.add(s)
                all_skills.append(s)

    return agent_tools, agent_skills, all_tools, all_skills

def _resolve_forge_tool(tenant_id: str, tool_name: str) -> bytes | None:
    """Return a bundleable tool JSON (name+schema+code) from the Forge registry.

    Looks up the registry.json, reads the .py implementation (handling stale
    absolute impl_path from the CorvinOS repo rename history), and returns
    a self-contained JSON bundle as bytes.
    """
    home = _forge_paths.tenant_home(tenant_id)
    registry_file = home / "global" / "forge" / "registry.json"
    if not registry_file.exists():
        return None
    try:
        registry: dict = json.loads(registry_file.read_text(encoding="utf-8"))
    except Exception:
        return None

    # Match by exact name or with/without "code." prefix
    entry = registry.get(tool_name) or registry.get(f"code.{tool_name}") or registry.get(
        tool_name.removeprefix("code.") if tool_name.startswith("code.") else None
    )
    if not entry:
        return None

    actual_name: str = entry["name"]
    tools_dir = home / "global" / "forge" / "tools"

    # 1. Canonical location: <tenant>/global/forge/tools/<name>.py
    py_file = tools_dir / f"{actual_name}.py"

    # 2. Fallback: impl_path with stale-path rewriting (legacy path migration)
    if not py_file.exists():
        raw_impl = entry.get("impl_path", "")
        candidate = Path(raw_impl)
        if not candidate.exists():
            # Handle repo rename: replace any known stale prefix
            for old_frag, new_frag in [
                ("/projects/corvinOS/", "/projects/Corvin/"),
                ("/projects/corvinOS/", "/projects/corvin/"),
            ]:
                if old_frag in raw_impl:
                    candidate = Path(raw_impl.replace(old_frag, new_frag, 1))
                    if candidate.exists():
                        break
        if candidate.exists():
            py_file = candidate

    if not py_file.exists():
        return None

    code = py_file.read_text(encoding="utf-8")
    bundle = {
        "name": actual_name,
        "description": entry.get("description", ""),
        "input_schema": entry.get("input_schema", {"type": "object", "properties": {}, "required": []}),
        "meta": {
            "language": "python",
            "network": "deny",
            **entry.get("meta", {}),
        },
        "code": code,
    }
    return json.dumps(bundle, indent=2, ensure_ascii=False).encode("utf-8")

def _resolve_skill(tenant_id: str, skill_name: str) -> bytes | None:
    """Return SKILL.md bytes for skill_name, checking SkillForge and Claude Code skill dirs."""
    home = _forge_paths.tenant_home(tenant_id)
    user_home = Path.home()

    candidates = [
        # SkillForge tenant scope
        home / "global" / "skill-forge" / "skills" / skill_name / "SKILL.md",
        # SkillForge global ~/.corvin scope
        user_home / ".corvin" / "global" / "skill-forge" / "skills" / skill_name / "SKILL.md",
        # Claude Code user-global skills
        user_home / ".claude" / "skills" / skill_name / "SKILL.md",
    ]
    for c in candidates:
        if c.exists():
            return c.read_bytes()

    # Claude Code plugin cache (skills installed via skill packages)
    plugin_cache = user_home / ".claude" / "plugins" / "cache"
    if plugin_cache.exists():
        for match in plugin_cache.rglob(f"*/skills/{skill_name}/SKILL.md"):
            return match.read_bytes()

    return None

def _generate_ascii_chart(yaml_text: str, wid: str) -> str:
    """Generate a compact ASCII DAG diagram from the workflow YAML."""
    if not _YAML_OK:
        return f"[{wid}]"
    try:
        doc = _yaml.safe_load(yaml_text) or {}
    except Exception:
        return f"[{wid}]"

    graph = (doc.get("orchestration") or {}).get("graph") or []
    if not graph:
        return f"[{wid}] (empty)"

    lines = [f"Workflow: {wid}", ""]
    deps = {n.get("id", ""): list(n.get("depends_on") or []) for n in graph}

    def _roots():
        return [nid for nid, d in deps.items() if not d]

    def _children(nid):
        return [n for n, d in deps.items() if nid in d]

    visited: set[str] = set()

    def _render(nid: str, prefix: str = "") -> None:
        if nid in visited:
            return
        visited.add(nid)
        node = next((n for n in graph if n.get("id") == nid), {})
        agent = node.get("agent", "")
        tools = node.get("forge_tools") or node.get("tools") or []
        skills = node.get("skills") or []
        extras = []
        if agent:
            extras.append(f"agent:{agent}")
        if tools:
            extras.append(f"tools:{','.join(str(t) for t in tools)}")
        if skills:
            extras.append(f"skills:{','.join(str(s) for s in skills)}")
        suffix = f"  [{', '.join(extras)}]" if extras else ""
        lines.append(f"{prefix}[{nid}]{suffix}")
        ch = _children(nid)
        for i, c in enumerate(ch):
            connector = "├── " if i < len(ch) - 1 else "└── "
            _render(c, prefix + connector)

    for root in _roots():
        _render(root)

    return "\n".join(lines)

def _register_tools_from_zip(
    zf: zipfile.ZipFile,
    tenant_id: str,
) -> list[str]:
    """Write tools from an imported AWPKG into the tenant's Forge registry.

    Writes the .py implementation to <tenant>/global/forge/tools/<name>.py
    and upserts the registry.json entry with an absolute impl_path.
    """
    home = _forge_paths.tenant_home(tenant_id)
    tools_dir = home / "global" / "forge" / "tools"
    registry_file = home / "global" / "forge" / "registry.json"

    # Load existing registry
    registry: dict = {}
    if registry_file.exists():
        try:
            registry = json.loads(registry_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    registered: list[str] = []
    tool_entries = [n for n in zf.namelist() if n.startswith("tools/") and n.endswith(".json")]
    for arc_path in tool_entries:
        raw_bytes = zf.read(arc_path)
        try:
            bundle = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            continue
        tool_name: str = bundle.get("name") or Path(arc_path).stem
        code: str = bundle.get("code", "")

        # Validate tool_name to prevent path traversal
        import re as _re
        if not _re.match(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$', tool_name):
            raise HTTPException(422, f"invalid tool name: {tool_name!r}")
        # Write .py implementation
        tools_dir.mkdir(parents=True, exist_ok=True)
        py_path = tools_dir / f"{tool_name}.py"
        # Guard against directory traversal
        if py_path.resolve().parent != tools_dir.resolve():
            raise HTTPException(422, f"tool name would escape tools directory")
        py_path.write_text(code, encoding="utf-8")

        # Upsert registry entry
        registry[tool_name] = {
            "name": tool_name,
            "description": bundle.get("description", ""),
            "input_schema": bundle.get("input_schema", {"type": "object", "properties": {}}),
            "runtime": "python",
            "impl_path": str(py_path),
            "scope": "user",
            "created_at": time.time(),
            "sha256": "",
            "call_count": 0,
            "promoted": False,
            "meta": bundle.get("meta", {}),
        }
        registered.append(tool_name)

    if registered:
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text(
            json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return registered

def _register_skills_from_zip(
    zf: zipfile.ZipFile,
    tenant_id: str,
) -> list[str]:
    """Write skills from an imported AWPKG into the tenant's SkillForge user registry."""
    home = _forge_paths.tenant_home(tenant_id)
    skill_root = home / "skill-forge" / "skills" / "user"
    registered: list[str] = []
    skill_entries = [n for n in zf.namelist()
                     if n.startswith("skills/") and n.endswith("/SKILL.md")]
    import re as _re
    _SKILL_NAME_RE = _re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$')
    for arc_path in skill_entries:
        parts = Path(arc_path).parts          # ("skills", "<name>", "SKILL.md")
        skill_name = parts[1] if len(parts) >= 3 else Path(arc_path).stem
        if not _SKILL_NAME_RE.match(skill_name):
            raise HTTPException(422, f"invalid skill name: {skill_name!r}")
        dest = skill_root / skill_name
        # Guard against path traversal (e.g. skill_name == "..")
        if not dest.resolve().is_relative_to(skill_root.resolve()):
            raise HTTPException(422, f"skill name would escape skills directory")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_bytes(zf.read(arc_path))
        meta_file = dest / "meta.json"
        if not meta_file.exists():
            meta_file.write_text(
                json.dumps({
                    "name": skill_name,
                    "scope": "user",
                    "created_at": time.time(),
                    "grades": [],
                    "mean_score": 0.0,
                    "source": "awpkg:imported",
                }, indent=2),
                encoding="utf-8",
            )
        registered.append(skill_name)
    return registered

# ── Phase 5: Export AWPKG ─────────────────────────────────────────────────

@router.get("/workflows/{wid}/export.awpkg")
def export_awpkg(
    wid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Response:
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)
    yaml_p = _yaml_path(rec.tenant_id, wid)
    if not yaml_p.exists():
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "no YAML defined")
    yaml_text = yaml_p.read_text(encoding="utf-8")

    # ── Collect tool/skill refs from the workflow YAML ────────────────────
    agent_tools, agent_skills, all_tools, all_skills = _collect_workflow_refs(yaml_text)

    # ── Resolve and bundle each tool/skill ────────────────────────────────
    arc_files: dict[str, bytes] = {f"workflows/{wid}.awp.yaml": yaml_text.encode("utf-8")}
    forge_tool_arcs: list[str] = []
    skill_arcs: list[str] = []

    for tool_name in all_tools:
        data = _resolve_forge_tool(rec.tenant_id, tool_name)
        if data:
            safe = tool_name.replace("/", "_")
            arc = f"tools/{safe}.json"
            arc_files[arc] = data
            forge_tool_arcs.append(arc)

    for skill_name in all_skills:
        data = _resolve_skill(rec.tenant_id, skill_name)
        if data:
            arc = f"skills/{skill_name}/SKILL.md"
            arc_files[arc] = data
            skill_arcs.append(arc)

    # ── Build agents section ───────────────────────────────────────────────
    agents_manifest: dict[str, Any] = {}
    for agent_id in set(agent_tools) | set(agent_skills):
        t_arcs = [f"tools/{t.replace('/', '_')}.json" for t in agent_tools.get(agent_id, [])
                  if f"tools/{t.replace('/', '_')}.json" in forge_tool_arcs]
        s_arcs = [f"skills/{s}/SKILL.md" for s in agent_skills.get(agent_id, [])
                  if f"skills/{s}/SKILL.md" in skill_arcs]
        if t_arcs or s_arcs:
            agents_manifest[agent_id] = {
                "description": f"Agent {agent_id} in workflow {wid}",
                "instructions": "",
                "tools": t_arcs,
                "skills": s_arcs,
            }

    # ── Build components + manifest ────────────────────────────────────────
    components: dict[str, list[str]] = {"workflows": [f"workflows/{wid}.awp.yaml"]}
    if forge_tool_arcs:
        components["forge_tools"] = forge_tool_arcs
    if skill_arcs:
        components["skills"] = skill_arcs

    manifest_dict: dict[str, Any] = {
        "awpkg": "1.0",
        "id": f"com.corvin.{wid.replace('_', '-')}",
        "name": meta.get("title", wid),
        "version": "0.1.0",
        "description": meta.get("description", "Exported Corvin workflow.") or "Exported Corvin workflow.",
        "author": f"tenant/{rec.tenant_id}",
        "license": "Apache-2.0",
        "workflow_description": meta.get("description", ""),
        "ascii_chart": _generate_ascii_chart(yaml_text, wid),
        "components": components,
        "permissions": {"network": False, "compute": False, "secrets": []},
        "dependencies": [],
    }
    if agents_manifest:
        manifest_dict["agents"] = agents_manifest

    # ── Build ZIP directly — no awpkg library dependency ──────────────────
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if _YAML_OK:
            manifest_bytes = _yaml.dump(manifest_dict, allow_unicode=True,
                                        default_flow_style=False).encode("utf-8")
        else:
            manifest_bytes = json.dumps(manifest_dict, indent=2,
                                        ensure_ascii=False).encode("utf-8")
        zf.writestr("manifest.yaml", manifest_bytes)
        for arc_path, data in arc_files.items():
            zf.writestr(arc_path, data)
    pkg_bytes = buf.getvalue()

    return Response(
        content=pkg_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{wid}.awpkg"'},
    )

# ── Phase 5: Import YAML or AWPKG ─────────────────────────────────────────

_MAX_IMPORT_BYTES = 512 * 1024  # 512 KiB

def _import_write_locked(
    tenant_id: str,
    wid: str,
    yaml_content: str,
    filename: str,
    rec: session_auth.SessionRecord,
) -> tuple[str, dict[str, Any]]:
    """Sync helper: collision resolve + workflows_max check + file write, all under
    the per-tenant fcntl lock.  Called via anyio.to_thread.run_sync from the async
    import_workflow handler so the blocking flock never stalls the event loop.

    Returns the resolved (wid, meta) tuple.
    """
    with _wf_create_lock(tenant_id):
        # Resolve wid collision inside the lock to avoid TOCTOU on the counter.
        base_wid = wid
        counter = 0
        while _meta_path(tenant_id, wid).exists() and counter < 100:
            counter += 1
            wid = f"{base_wid}_{counter}"
        if counter >= 100:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "too many workflows with this name")

        # ADR-0094: enforce workflows_max before writing (mirrors create_workflow).
        _enforce_workflows_max(tenant_id, rec)

        now = time.time()
        meta: dict[str, Any] = {
            "id": wid,
            "title": wid.replace("_", " ").title(),
            "description": "",
            "phase": "ready",
            "created_at": now,
            "updated_at": now,
            "has_schedule": False,
            "imported_from": filename,
        }
        _ensure_dir(_workflows_dir(tenant_id))
        _write_atomic(_yaml_path(tenant_id, wid), yaml_content)
        _write_atomic(_meta_path(tenant_id, wid), meta)
        _write_awpkg_sidecar(tenant_id, wid)

    return wid, meta

@router.post("/workflows/import")
async def import_workflow(
    file: Annotated[UploadFile, File(description="AWP YAML or AWPKG bundle")],
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    data = await file.read(_MAX_IMPORT_BYTES + 1)
    if len(data) > _MAX_IMPORT_BYTES:
        raise HTTPException(http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large (max 512 KiB)")

    filename = file.filename or "workflow.awp.yaml"

    registered_tools: list[str] = []
    registered_skills: list[str] = []

    if filename.endswith(".awpkg"):
        # Extract workflow YAML only here — tool/skill registration happens after
        # the license check passes (inside anyio.to_thread.run_sync below), so
        # bundled tools/skills are never registered for tenants that are over-limit.
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                yaml_names = [n for n in zf.namelist() if n.endswith(".awp.yaml")]
                if not yaml_names:
                    raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "no .awp.yaml found in .awpkg")
                yaml_content = zf.read(yaml_names[0]).decode("utf-8")
        except zipfile.BadZipFile as exc:
            _log.error("Invalid AWPKG file", exc_info=True)
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "Invalid .awpkg file — file may be corrupted") from exc
    else:
        try:
            yaml_content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            _log.error("File encoding error", exc_info=True)
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "File must be valid UTF-8 encoded text") from exc

    _validate_yaml_str(yaml_content)

    # Extract workflow id from YAML
    wid: str | None = None
    if _YAML_OK:
        try:
            parsed = _yaml.safe_load(yaml_content) or {}
            wid = parsed.get("workflow", {}).get("name") or None
        except Exception:
            pass
    if not wid:
        wid = re.sub(r"[^a-z0-9_-]", "_", filename.split(".")[0].lower())[:63] or "imported"
    if not _WID_RE.match(wid):
        wid = "workflow_" + re.sub(r"[^a-z0-9]", "_", wid)[:55]

    # Run the locking+write critical section in a thread so the blocking
    # fcntl.flock call does not stall the event loop.
    wid, meta = await anyio.to_thread.run_sync(
        lambda: _import_write_locked(rec.tenant_id, wid, yaml_content, filename, rec)
    )

    # Register bundled tools/skills only after the license check and write
    # succeeded — prevents side-effects for over-limit tenants.
    if filename.endswith(".awpkg"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            registered_tools = _register_tools_from_zip(zf, rec.tenant_id)
            registered_skills = _register_skills_from_zip(zf, rec.tenant_id)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.created",
        target_kind="workflow",
        target_id=wid,
    )
    return {
        "ok": True,
        "id": wid,
        "workflow": meta,
        "graph": _parse_graph(yaml_content),
        "registered_tools": registered_tools,
        "registered_skills": registered_skills,
    }

# ── Routes: runs ──────────────────────────────────────────────────────────

@router.get("/workflows/{wid}/runs")
def list_runs(
    wid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    _validate_wid(wid)
    _require_workflow(rec.tenant_id, wid)
    runs_d = _runs_dir(rec.tenant_id, wid)
    if not runs_d.exists():
        return {"wid": wid, "count": 0, "runs": []}
    items = []
    for mf in sorted(runs_d.glob("*.meta.json")):
        data = _read_json(mf)
        if data:
            items.append(data)
    items.sort(key=lambda x: x.get("started_at", 0), reverse=True)
    return {"wid": wid, "count": len(items), "runs": items}

@router.get("/workflows/{wid}/runs/{rid}")
def get_run(
    wid: str,
    rid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    _validate_wid(wid)
    _require_workflow(rec.tenant_id, wid)
    meta = _read_json(_run_meta_path(rec.tenant_id, wid, rid))
    if meta is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "run not found")
    events: list[dict[str, Any]] = []
    log_p = _run_log_path(rec.tenant_id, wid, rid)
    if log_p.exists():
        for raw in log_p.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                try:
                    events.append(json.loads(raw))
                except Exception:
                    pass
    return {"run": meta, "events": events}

@router.delete("/workflows/{wid}/runs/{rid}")
def delete_run(
    wid: str,
    rid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    _validate_wid(wid)
    _require_workflow(rec.tenant_id, wid)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.run_deleted",
        target_kind="workflow_run",
        target_id=f"{wid}/{rid}",
    )
    for p in (_run_meta_path(rec.tenant_id, wid, rid), _run_log_path(rec.tenant_id, wid, rid)):
        if p.exists():
            p.unlink()
    return {"ok": True, "rid": rid}

# ── Human-in-the-Loop (approval) endpoints ────────────────────────────────

@router.get("/workflows/{wid}/runs/{rid}/approval")
def get_approval_state(
    wid: str,
    rid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the current approval state for a paused run node."""
    _validate_wid(wid)
    _require_workflow(rec.tenant_id, wid)
    ap = _approval_path(rec.tenant_id, wid, rid)
    if not ap.exists():
        return {"status": "none"}
    try:
        return json.loads(ap.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "error"}

@router.post("/workflows/{wid}/runs/{rid}/approve")
def approve_run_node(
    wid: str,
    rid: str,
    body: ApproveRunRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Approve a paused approval node — continues the workflow run."""
    _validate_wid(wid)
    _require_workflow(rec.tenant_id, wid)
    ap = _approval_path(rec.tenant_id, wid, rid)
    if not ap.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no pending approval for this run")
    try:
        current = json.loads(ap.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "failed to read approval state") from exc
    if current.get("status") != "pending":
        raise HTTPException(http_status.HTTP_409_CONFLICT, f"approval is already {current.get('status')!r}")
    _write_atomic(ap, {**current, "status": "approved", "comment": body.comment, "decided_at": time.time()})
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.run_approved",
        target_kind="workflow_run",
        target_id=f"{wid}/{rid}",
    )
    return {"ok": True, "status": "approved"}

@router.post("/workflows/{wid}/runs/{rid}/reject")
def reject_run_node(
    wid: str,
    rid: str,
    body: ApproveRunRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Reject a paused approval node — aborts the workflow run."""
    _validate_wid(wid)
    _require_workflow(rec.tenant_id, wid)
    ap = _approval_path(rec.tenant_id, wid, rid)
    if not ap.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no pending approval for this run")
    try:
        current = json.loads(ap.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "failed to read approval state") from exc
    if current.get("status") != "pending":
        raise HTTPException(http_status.HTTP_409_CONFLICT, f"approval is already {current.get('status')!r}")
    _write_atomic(ap, {**current, "status": "rejected", "comment": body.comment, "decided_at": time.time()})
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.run_rejected",
        target_kind="workflow_run",
        target_id=f"{wid}/{rid}",
    )
    return {"ok": True, "status": "rejected"}

# ══════════════════════════════════════════════════════════════════════════
# ── ADR-0091: Workflow Run Media ───────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

_MEDIA_MIME: dict[str, str] = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}
_MEDIA_EXTENSIONS = frozenset(_MEDIA_MIME)

def _run_artifacts_dir(tenant_id: str, wid: str, rid: str) -> Path:
    """Layer 33 session artifacts dir for a workflow run."""
    # Layer 33 path: <corvin_home>/tenants/<tid>/sessions/<bridge>:<rid>/artifacts/
    # We use a simplified path scoped to the workflow+run:
    # <workflows_dir>/<wid>/runs/<rid>/artifacts/
    return _runs_dir(tenant_id, wid) / rid / "artifacts"

@router.get("/workflows/{wid}/runs/{rid}/media")
def list_run_media(
    wid: str,
    rid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List media artifacts for a workflow run (ADR-0091 M1)."""
    for v in (wid, rid):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid id")

    tid = rec.tenant_id
    artifacts_dir = _run_artifacts_dir(tid, wid, rid)

    media: list[dict[str, Any]] = []
    if artifacts_dir.is_dir():
        try:
            for f in sorted(artifacts_dir.iterdir(), key=lambda x: x.stat().st_mtime):
                ext = f.suffix.lower()
                if not f.is_file() or ext not in _MEDIA_EXTENSIONS:
                    continue
                # Skip thumbnail files (stem ends with _thumb)
                if f.stem.endswith("_thumb"):
                    continue
                stat = f.stat()
                thumb_name = f"{f.stem}_thumb{f.suffix}"
                thumb_path = artifacts_dir / thumb_name
                # Derive node_id from filename prefix (pattern: {node_id}_{name}.ext)
                parts = f.stem.split("_", 1)
                node_id = parts[0] if len(parts) > 1 else None
                media.append({
                    "media_id": f"{node_id}_{f.stem}" if node_id else f.stem,
                    "node_id": node_id,
                    "filename": f.name,
                    "mime_type": _MEDIA_MIME.get(ext, "application/octet-stream"),
                    "size_bytes": stat.st_size,
                    "label": None,  # populated async by Haiku (future)
                    "src": f"/v1/console/workflows/{wid}/runs/{rid}/media/{f.name}",
                    "thumbnail_src": f"/v1/console/workflows/{wid}/runs/{rid}/media/{thumb_name}" if thumb_path.exists() else None,
                    "ts": stat.st_mtime,
                })
        except OSError:
            pass

    return {"run_id": rid, "media": media, "count": len(media)}

@router.get("/workflows/{wid}/runs/{rid}/media/{filename}")
def serve_run_media(
    wid: str,
    rid: str,
    filename: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """Serve a run media artifact (ADR-0091 M1)."""
    import re as _re

    for v in (wid, rid):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid id")
    if not _re.fullmatch(r"[A-Za-z0-9_.\-]{1,128}", filename):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid filename")

    tid = rec.tenant_id
    artifacts_dir = _run_artifacts_dir(tid, wid, rid)
    file_path = artifacts_dir / filename

    try:
        file_path.resolve().relative_to(artifacts_dir.resolve())
    except ValueError:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "path outside artifacts directory")

    if not file_path.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"{filename!r} not found")

    ext = file_path.suffix.lower()
    mime = _MEDIA_MIME.get(ext, "application/octet-stream")
    safe_fn = filename.replace('"', "").replace("\r", "").replace("\n", "")

    return StreamingResponse(
        open(str(file_path), "rb"),  # noqa: WPS515
        media_type=mime,
        headers={
            "Content-Disposition": f'inline; filename="{safe_fn}"',
            "Cache-Control": "private, max-age=3600",
        },
    )

@router.get("/workflows/{wid}/runs/{rid}/media.zip")
def download_run_media_zip(
    wid: str,
    rid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """Bulk download all run media as ZIP (ADR-0091 M4)."""
    import io

    for v in (wid, rid):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid id")

    tid = rec.tenant_id
    artifacts_dir = _run_artifacts_dir(tid, wid, rid)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if artifacts_dir.is_dir():
            try:
                for f in sorted(artifacts_dir.iterdir()):
                    ext = f.suffix.lower()
                    if f.is_file() and ext in _MEDIA_EXTENSIONS and not f.stem.endswith("_thumb"):
                        zf.write(f, arcname=f.name)
            except OSError:
                pass

    buf.seek(0)
    safe_rid = rid.replace('"', "").replace("\r", "").replace("\n", "")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_rid}_media.zip"'},
    )

# ══════════════════════════════════════════════════════════════════════════
# ── ADR-0091: Workflow Run Table Support ──────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

_TABLE_EXTS = frozenset({".csv", ".parquet", ".pq", ".json", ".jsonl", ".tsv"})

@router.get("/workflows/{wid}/runs/{rid}/tables")
def list_run_tables(
    wid: str,
    rid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List tabular data artifacts for a workflow run."""
    for v in (wid, rid):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid id")

    tid = rec.tenant_id
    artifacts_dir = _run_artifacts_dir(tid, wid, rid)
    tables: list[dict[str, Any]] = []
    if artifacts_dir.is_dir():
        try:
            for f in sorted(artifacts_dir.iterdir(), key=lambda x: x.stat().st_mtime):
                if f.is_file() and f.suffix.lower() in _TABLE_EXTS:
                    stat = f.stat()
                    tables.append({
                        "filename": f.name,
                        "mime_type": {
                            ".csv": "text/csv", ".tsv": "text/tab-separated-values",
                            ".parquet": "application/octet-stream",
                            ".pq": "application/octet-stream",
                            ".json": "application/json",
                            ".jsonl": "application/x-ndjson",
                        }.get(f.suffix.lower(), "application/octet-stream"),
                        "size_bytes": stat.st_size,
                        "src": f"/v1/console/workflows/{wid}/runs/{rid}/tables/{f.name}",
                        "ts": stat.st_mtime,
                    })
        except OSError:
            pass
    return {"run_id": rid, "tables": tables, "count": len(tables)}

@router.get("/workflows/{wid}/runs/{rid}/tables/{filename}")
def serve_run_table(
    wid: str,
    rid: str,
    filename: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    page: int = 1,
    per_page: int = 50,
    sort_col: str | None = None,
    sort_dir: str = "asc",
    filter: str | None = None,
    cols: str | None = None,
) -> dict[str, Any]:
    """Serve a run table artifact with sort/filter/pagination (same DuckDB engine as compute)."""
    import re as _re
    for v in (wid, rid):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid id")
    if not _re.fullmatch(r"[A-Za-z0-9_.\-]{1,128}", filename):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid filename")

    tid = rec.tenant_id
    artifacts_dir = _run_artifacts_dir(tid, wid, rid)
    file_path = artifacts_dir / filename
    try:
        file_path.resolve().relative_to(artifacts_dir.resolve())
    except ValueError:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "path outside artifacts directory")
    if not file_path.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"{filename!r} not found")

    # Reuse the same DuckDB query helper from compute routes
    from .compute import _duckdb_table_query  # noqa: E402
    selected_cols = [c.strip() for c in cols.split(",")] if cols else None
    result = _duckdb_table_query(
        file_path,
        pii_cols=[],  # workflow tables: no pre-tagged PII columns
        page=page,
        per_page=per_page,
        sort_col=sort_col,
        sort_dir=sort_dir,
        filter_text=filter,
        selected_cols=selected_cols,
    )
    result["filename"] = filename
    result["rows_returned"] = len(result["rows"])
    return result

def _is_safe_output_path(resolved: "Path") -> bool:
    """Return True only for paths in workflow-safe output locations.

    Prevents LLM prompt-injection from exfiltrating arbitrary host files by
    restricting the source of file-copy operations to /tmp and the corvin
    workflow output tree. Protected config paths (.corvin, .config, /etc, /home
    outside of /tmp-like dirs) are explicitly blocked.
    """
    import tempfile as _tf
    s = str(resolved)
    # Block any path containing corvin config, system dirs, or home dot-files.
    _BLOCKED_PREFIXES = (
        str(_forge_paths.corvin_home()),
        str(Path.home() / ".config"),
        str(Path.home() / ".ssh"),
        str(Path.home() / ".gnupg"),
        "/etc/",
        "/proc/",
        "/sys/",
        "/root/",
        "/var/",
        "/usr/",
        "/boot/",
        "/dev/",
    )
    for prefix in _BLOCKED_PREFIXES:
        if s.startswith(prefix):
            return False
    # Allow: /tmp and other system-temp dirs, explicit workflow output dirs
    _ALLOWED_PREFIXES = (
        _tf.gettempdir(),
        "/tmp",
    )
    for prefix in _ALLOWED_PREFIXES:
        if s.startswith(prefix):
            return True
    # Allow paths under the corvin tenant workflow output tree
    try:
        from .. import auth as _auth_mod  # noqa: PLC0415
        home = _forge_paths.corvin_home()
        if s.startswith(str(home / "tenants")) and "workflows" in s:
            return True
    except Exception:
        pass
    return False

def _scan_output_for_tables(
    output_text: str,
    node_id: str,
    tenant_id: str,
    wid: str,
    rid: str,
) -> list[dict]:
    """Scan node output text for tabular data file paths.
    Copies found CSV/Parquet/JSON files into the run artifacts dir.
    Returns list of table event dicts (without 'type' key — caller adds it).
    """
    import re as _re
    pattern = _re.compile(
        r'[^\s\'"]+\.(?:csv|tsv|parquet|pq|json|jsonl)\b', _re.IGNORECASE
    )
    found = pattern.findall(output_text)
    artifacts_dir = _run_artifacts_dir(tenant_id, wid, rid)
    table_events = []

    for raw_path in found:
        src_path = Path(raw_path).resolve()
        if not src_path.exists() or not src_path.is_file():
            continue
        ext = src_path.suffix.lower()
        if ext not in _TABLE_EXTS:
            continue
        # Path containment: only accept files from safe directories to prevent
        # LLM prompt-injection from exfiltrating host files via path mention.
        if not _is_safe_output_path(src_path):
            continue
        try:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            dest_name = f"{node_id}_{src_path.name}"
            dest_path = artifacts_dir / dest_name
            if not dest_path.exists():
                import shutil as _sh
                _sh.copy2(src_path, dest_path)
                os.chmod(str(dest_path), 0o600)
        except OSError:
            continue

        # Quick row-count estimate (cheap: only read header + metadata)
        row_estimate: int | None = None
        try:
            import duckdb as _ddb
            _c = _ddb.connect()
            row_estimate = int(_c.execute(
                "SELECT COUNT(*) FROM read_csv_auto(?) LIMIT 1",
                [str(dest_path)]
            ).fetchone()[0]) if ext in (".csv", ".tsv") else None
            _c.close()
        except Exception:
            pass

        mime_map = {
            ".csv": "text/csv", ".tsv": "text/tab-separated-values",
            ".parquet": "application/octet-stream", ".pq": "application/octet-stream",
            ".json": "application/json", ".jsonl": "application/x-ndjson",
        }
        table_events.append({
            "node_id": node_id,
            "table_id": f"{node_id}_{src_path.stem}",
            "filename": dest_name,
            "mime_type": mime_map.get(ext, "application/octet-stream"),
            "row_count": row_estimate,
            "size_bytes": dest_path.stat().st_size if dest_path.exists() else 0,
            "src": f"/v1/console/workflows/{wid}/runs/{rid}/tables/{dest_name}",
            "ts": time.time(),
        })

    return table_events

def _scan_compute_stage_tables(
    tenant_id: str,
    node_id: str,
    tool_name: str,
    wid: str,
    rid: str,
    wf_meta: dict | None = None,
) -> list[dict]:
    """M5 bridge for tables: copy existing pipeline stage CSV/Parquet artifacts
    into the run store. Analogous to _scan_compute_stage_artifacts() for images.
    """
    table_events: list[dict] = []
    compute_root = _forge_paths.tenant_home(tenant_id) / "compute" / "pipelines"
    if not compute_root.exists():
        return []

    source_pid = (wf_meta or {}).get("pipeline_id")
    pipeline_dirs = (
        [compute_root / source_pid]
        if source_pid and (compute_root / source_pid).is_dir()
        else [p for p in compute_root.iterdir() if p.is_dir()]
    )

    artifacts_dir = _run_artifacts_dir(tenant_id, wid, rid)
    seen: set[str] = set()

    for pipeline_dir in pipeline_dirs:
        stages_root = pipeline_dir / "stages"
        if not stages_root.exists():
            continue
        for stage_dir in stages_root.iterdir():
            if not stage_dir.is_dir():
                continue
            matched = False
            manifest_path = pipeline_dir / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    for s in manifest.get("stages", []):
                        if s.get("tool_name") == tool_name:
                            matched = True
                            break
                except (OSError, json.JSONDecodeError):
                    pass
            if not matched:
                matched = stage_dir.name in tool_name or tool_name.endswith(stage_dir.name)
            if not matched:
                continue

            stage_artifacts = stage_dir / "artifacts"
            if not stage_artifacts.exists():
                continue

            for tbl_file in sorted(stage_artifacts.iterdir()):
                ext = tbl_file.suffix.lower()
                if not tbl_file.is_file() or ext not in _TABLE_EXTS:
                    continue
                if tbl_file.name in seen:
                    continue
                seen.add(tbl_file.name)

                dest_name = f"{node_id}_{tbl_file.name}"
                try:
                    artifacts_dir.mkdir(parents=True, exist_ok=True)
                    dest_path = artifacts_dir / dest_name
                    if not dest_path.exists():
                        import shutil as _sh
                        _sh.copy2(tbl_file, dest_path)
                        os.chmod(str(dest_path), 0o600)
                except OSError:
                    continue

                size = dest_path.stat().st_size if dest_path.exists() else tbl_file.stat().st_size
                mime_map = {
                    ".csv": "text/csv", ".tsv": "text/tab-separated-values",
                    ".parquet": "application/octet-stream", ".pq": "application/octet-stream",
                    ".json": "application/json", ".jsonl": "application/x-ndjson",
                }
                table_events.append({
                    "node_id": node_id,
                    "table_id": f"{node_id}_{tbl_file.stem}",
                    "filename": dest_name,
                    "mime_type": mime_map.get(ext, "application/octet-stream"),
                    "row_count": None,  # estimated lazily on first fetch
                    "size_bytes": size,
                    "src": f"/v1/console/workflows/{wid}/runs/{rid}/tables/{dest_name}",
                    "ts": time.time(),
                })
            break  # found matching stage

    return table_events

# ── Compliance helpers (EU AI Act Art. 14 + DSGVO Art. 6) ─────────────────

def _tenant_yaml(tenant_id: str) -> dict[str, Any]:
    """Load tenant.corvin.yaml; return {} when absent (fail-open for new tenants)."""
    try:
        yaml_path = _forge_paths.tenant_global_dir(tenant_id) / "tenant.corvin.yaml"
        if yaml_path.exists() and _YAML_OK:
            raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    return {}

def _check_compliance_zone(
    tenant_id: str,
    wid: str,
    rid: str,
    sid_fingerprint: str,
) -> str | None:
    """Check engine policy from tenant.corvin.yaml (EU AI Act Art. 14).

    Returns an error string if the workflow is blocked; None if allowed.
    The console workflow runner uses 'claude-code' as the sole engine.
    """
    cfg = _tenant_yaml(tenant_id)
    residency = (cfg.get("spec") or {}).get("data_residency") or {}

    allowed: list[str] = residency.get("allowed_engines") or []
    forbidden: list[str] = residency.get("forbid_engines") or []

    # An empty allowed list means "all engines allowed" (template default).
    engine = "claude-code"
    if forbidden and engine in forbidden:
        console_audit.action_denied(
            tenant_id=tenant_id,
            sid_fingerprint=sid_fingerprint,
            action="workflow.run_blocked",
            target_kind="workflow",
            target_id=wid,
            reason=f"engine '{engine}' is in tenant forbid_engines list",
            run_id=rid,
        )
        return f"Engine '{engine}' is not permitted by this tenant's compliance policy."

    if allowed and engine not in allowed:
        console_audit.action_denied(
            tenant_id=tenant_id,
            sid_fingerprint=sid_fingerprint,
            action="workflow.run_blocked",
            target_kind="workflow",
            target_id=wid,
            reason=f"engine '{engine}' not in tenant allowed_engines list",
            run_id=rid,
        )
        return f"Engine '{engine}' is not in this tenant's allowed_engines list."

    return None

def _check_consent_policy(
    tenant_id: str,
    wid: str,
    rid: str,
    sid_fingerprint: str,
) -> str | None:
    """Check consent gate policy from tenant.corvin.yaml (DSGVO Art. 6).

    Console owners always have implicit consent for their own workflows.
    If the tenant's config sets spec.workflow.require_consent_gate: true,
    an additional consent check is enforced. Default: false (pass-through).

    Returns an error string if blocked; None if allowed.
    """
    cfg = _tenant_yaml(tenant_id)
    wf_cfg = (cfg.get("spec") or {}).get("workflow") or {}
    require_gate = bool(wf_cfg.get("require_consent_gate", False))

    if not require_gate:
        # Emit an INFO trace so the audit trail shows the consent check ran.
        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=sid_fingerprint,
            action="workflow.consent_checked",
            target_kind="workflow",
            target_id=wid,
            run_id=rid,
            trigger="manual",
        )
        return None

    # Gate is enabled — owner session always passes; external callers blocked.
    # (The console is single-tenant: if you have a session, you are the owner.)
    console_audit.action_performed(
        tenant_id=tenant_id,
        sid_fingerprint=sid_fingerprint,
        action="workflow.consent_gate_passed",
        target_kind="workflow",
        target_id=wid,
        run_id=rid,
        trigger="manual",
    )
    return None

def _scan_output_for_media(
    output_text: str,
    node_id: str,
    tenant_id: str,
    wid: str,
    rid: str,
) -> list[dict]:
    """Scan node output text for image file paths.

    Returns list of media event dicts (without 'type' key — caller adds it).
    Copies found image files into the run artifacts dir for Layer-33 lifecycle.
    """
    import re as _re

    # Image extensions we care about
    _IMG_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf"})
    _MIME_MAP = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp",
        ".svg": "image/svg+xml", ".pdf": "application/pdf",
    }

    # Find file path candidates in the output
    # Matches: /absolute/path/to/file.png or relative paths ending in image ext
    path_pattern = _re.compile(r'[^\s\'"]+\.(?:png|jpg|jpeg|gif|webp|svg|pdf)\b', _re.IGNORECASE)
    found = path_pattern.findall(output_text)

    artifacts_dir = _run_artifacts_dir(tenant_id, wid, rid)
    media_events = []

    for raw_path in found:
        src_path = Path(raw_path).resolve()
        if not src_path.exists() or not src_path.is_file():
            continue
        ext = src_path.suffix.lower()
        if ext not in _IMG_EXTS:
            continue
        # Path containment: only accept files from safe directories.
        if not _is_safe_output_path(src_path):
            continue

        # Copy to run artifacts dir (async lifecycle independence from compute)
        try:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            # Prefix with node_id for easy association
            dest_name = f"{node_id}_{src_path.name}"
            dest_path = artifacts_dir / dest_name
            if not dest_path.exists():
                import shutil as _shutil
                _shutil.copy2(src_path, dest_path)
                os.chmod(str(dest_path), 0o600)
        except OSError:
            continue

        mime = _MIME_MAP.get(ext, "application/octet-stream")
        # Check for pre-generated thumbnail
        thumb_name = f"{node_id}_{src_path.stem}_thumb{ext}"
        thumb_path = artifacts_dir / thumb_name
        orig_thumb = src_path.parent / f"{src_path.stem}_thumb{ext}"
        if orig_thumb.exists() and not thumb_path.exists():
            try:
                import shutil as _shutil
                _shutil.copy2(orig_thumb, thumb_path)
                os.chmod(str(thumb_path), 0o600)
            except OSError:
                pass

        media_events.append({
            "node_id": node_id,
            "media_id": f"{node_id}_{src_path.stem}",
            "filename": dest_name,
            "mime_type": mime,
            "label": None,
            "src": f"/v1/console/workflows/{wid}/runs/{rid}/media/{dest_name}",
            "thumbnail_src": f"/v1/console/workflows/{wid}/runs/{rid}/media/{thumb_name}" if thumb_path.exists() else None,
            "ts": time.time(),
        })

    return media_events

def _scan_compute_stage_artifacts(
    tenant_id: str,
    node_id: str,
    tool_name: str,
    wid: str,
    rid: str,
    wf_meta: dict | None = None,
) -> list[dict]:
    """ADR-0091 M5 — For x_compute nodes: copy existing pipeline stage charts
    into the run artifacts store and return media event dicts.

    Since awpkg workflows are exported in Replay mode from completed pipelines,
    the charts ALREADY EXIST in the source pipeline stage artifacts. This bridge
    finds them by matching tool_name and promotes them into the run's media store.
    """
    _IMG_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
    _MIME_MAP = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    }

    media_events: list[dict] = []
    artifacts_dir = _run_artifacts_dir(tenant_id, wid, rid)
    compute_root = _forge_paths.tenant_home(tenant_id) / "compute" / "pipelines"
    if not compute_root.exists():
        return []

    # Narrow search: if we know the source pipeline_id from workflow meta, use it
    source_pid = (wf_meta or {}).get("pipeline_id")
    pipeline_dirs = (
        [compute_root / source_pid]
        if source_pid and (compute_root / source_pid).is_dir()
        else [p for p in compute_root.iterdir() if p.is_dir()]
    )

    seen_names: set[str] = set()
    for pipeline_dir in pipeline_dirs:
        stages_root = pipeline_dir / "stages"
        if not stages_root.exists():
            continue

        for stage_dir in stages_root.iterdir():
            if not stage_dir.is_dir():
                continue

            # Match stage by tool_name in stage_summary.json or pipeline manifest
            matched = False
            summary_path = stage_dir / "stage_summary.json"
            if summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    if summary.get("tool_name") == tool_name:
                        matched = True
                except (OSError, json.JSONDecodeError):
                    pass

            # Also check pipeline manifest stages
            if not matched:
                manifest_path = pipeline_dir / "manifest.json"
                if manifest_path.exists():
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        for s in manifest.get("stages", []):
                            if s.get("tool_name") == tool_name and s.get("stage_id") == stage_dir.name:
                                matched = True
                                break
                    except (OSError, json.JSONDecodeError):
                        pass

            if not matched:
                # Fallback: match by stage_dir name being part of tool_name or vice versa
                matched = (stage_dir.name in tool_name or tool_name.endswith(stage_dir.name))

            if not matched:
                continue

            # Found the stage — collect image artifacts
            stage_artifacts = stage_dir / "artifacts"
            if not stage_artifacts.exists():
                continue

            for img_file in sorted(stage_artifacts.iterdir()):
                ext = img_file.suffix.lower()
                if not img_file.is_file() or ext not in _IMG_EXTS:
                    continue
                if img_file.stem.endswith("_thumb"):
                    continue
                if img_file.name in seen_names:
                    continue
                seen_names.add(img_file.name)

                dest_name = f"{node_id}_{img_file.name}"
                try:
                    artifacts_dir.mkdir(parents=True, exist_ok=True)
                    dest_path = artifacts_dir / dest_name
                    if not dest_path.exists():
                        import shutil as _shutil
                        _shutil.copy2(img_file, dest_path)
                        os.chmod(str(dest_path), 0o600)
                    # Thumbnail
                    orig_thumb = stage_artifacts / f"{img_file.stem}_thumb{ext}"
                    thumb_name = f"{node_id}_{img_file.stem}_thumb{ext}"
                    thumb_dest = artifacts_dir / thumb_name
                    if orig_thumb.exists() and not thumb_dest.exists():
                        _shutil.copy2(orig_thumb, thumb_dest)
                except OSError:
                    continue

                mime = _MIME_MAP.get(ext, "application/octet-stream")
                media_events.append({
                    "node_id": node_id,
                    "media_id": f"{node_id}_{img_file.stem}",
                    "filename": dest_name,
                    "mime_type": mime,
                    "label": None,
                    "src": f"/v1/console/workflows/{wid}/runs/{rid}/media/{dest_name}",
                    "thumbnail_src": (
                        f"/v1/console/workflows/{wid}/runs/{rid}/media/{thumb_name}"
                        if (artifacts_dir / thumb_name).exists() else None
                    ),
                    "ts": time.time(),
                })
            break  # found matching stage — stop searching this pipeline

    return media_events

def _load_wf_meta(tenant_id: str, wid: str) -> dict:
    """Load workflow meta.json if available."""
    meta_path = _workflows_dir(tenant_id) / f"{wid}.meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}

def _enrich_prompt_with_charts(
    prompt: str,
    node: dict,
    wf_meta: dict,
    tenant_id: str,
) -> str:
    """For analyst nodes running after compute stages: inject chart paths into the prompt
    so the LLM can describe what was produced. Looks up existing stage artifacts.
    """
    # Only enrich if node has no x_compute (it's a text/analyst node)
    if node.get("x_compute"):
        return prompt

    # Find charts from the source pipeline
    compute_root = _forge_paths.tenant_home(tenant_id) / "compute" / "pipelines"
    source_pid = wf_meta.get("pipeline_id")
    if not source_pid or not compute_root.exists():
        return prompt

    pipeline_dir = compute_root / source_pid
    if not pipeline_dir.is_dir():
        return prompt

    _IMG_EXTS = frozenset({".png", ".jpg", ".jpeg", ".svg"})
    chart_lines = []
    for stage_dir in sorted((pipeline_dir / "stages").iterdir()):
        stage_artifacts = stage_dir / "artifacts"
        if not stage_artifacts.exists():
            continue
        for f in sorted(stage_artifacts.iterdir()):
            if f.is_file() and f.suffix.lower() in _IMG_EXTS and not f.stem.endswith("_thumb"):
                chart_lines.append(f"  - {stage_dir.name}/{f.name} ({f.stat().st_size // 1024} KB)")

    if not chart_lines:
        return prompt

    chart_context = (
        "\n\n[Verfügbare Charts aus der Compute-Pipeline (bitte in deiner Analyse referenzieren):\n"
        + "\n".join(chart_lines)
        + "\n"
        + "\n".join(str(pipeline_dir / "stages" / line.strip().split(" ")[0].split("/")[0] / "artifacts" / line.strip().split(" ")[0].split("/")[1])
                    for line in chart_lines if "/" in line.strip().split(" ")[0])
        + "]"
    )
    return prompt + chart_context

async def _stream_run(
    tenant_id: str,
    sid_fingerprint: str,
    wid: str,
    rid: str,
    yaml_text: str,
    inputs: dict[str, Any],
    dry_run: bool,
) -> AsyncIterator[str]:
    """SSE event stream for a workflow run (Phase 3)."""
    log_path = _run_log_path(tenant_id, wid, rid)
    _ensure_dir(log_path.parent)

    run_meta: dict[str, Any] = {
        "rid": rid,
        "wid": wid,
        "status": "running",
        "dry_run": dry_run,
        "started_at": time.time(),
        "finished_at": None,
        "ok": None,
        "error": None,
    }
    _write_atomic(_run_meta_path(tenant_id, wid, rid), run_meta)

    def _emit(event: dict[str, Any]) -> str:
        line = json.dumps(event, ensure_ascii=False)
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        return f"data: {line}\n\n"

    if not _YAML_OK:
        yield _emit({"type": "error", "ts": time.time(), "message": "PyYAML unavailable on server"})
        run_meta.update({"status": "failed", "ok": False, "error": "pyyaml unavailable", "finished_at": time.time()})
        _write_atomic(_run_meta_path(tenant_id, wid, rid), run_meta)
        return

    try:
        parsed = _yaml.safe_load(yaml_text) or {}
    except Exception as exc:
        yield _emit({"type": "error", "ts": time.time(), "message": f"YAML error: {exc}"})
        run_meta.update({"status": "failed", "ok": False, "error": str(exc), "finished_at": time.time()})
        _write_atomic(_run_meta_path(tenant_id, wid, rid), run_meta)
        return

    graph = parsed.get("orchestration", {}).get("graph", []) or []
    if not isinstance(graph, list):
        graph = []

    if dry_run:
        # Schema-only: enumerate nodes without executing
        for node in graph:
            node_id = str(node.get("id", "?"))
            yield _emit({"type": "node_started", "node_id": node_id, "ts": time.time()})
            await asyncio.sleep(0.05)
            yield _emit({"type": "node_completed", "node_id": node_id, "tokens": 0, "elapsed_s": 0.0, "ts": time.time()})
        yield _emit({"type": "run_completed", "ok": True, "dry_run": True, "budget": {}, "ts": time.time()})
        run_meta.update({"status": "complete", "ok": True, "finished_at": time.time()})
        _write_atomic(_run_meta_path(tenant_id, wid, rid), run_meta)
        return

    # Real run: topological execution via claude -p per node, streaming events as each completes.
    try:
        # Validate YAML if AWP stack available
        if _AWP_OK:
            fd, tmp = tempfile.mkstemp(suffix=".awp.yaml")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(yaml_text)
                doc = _load_workflow(tmp)
                _validate_workflow(doc)
            except WorkflowInvalid as exc:
                yield _emit({"type": "error", "ts": time.time(), "message": f"Validation failed: {exc}"})
                run_meta.update({"status": "failed", "ok": False, "error": str(exc), "finished_at": time.time()})
                _write_atomic(_run_meta_path(tenant_id, wid, rid), run_meta)
                return
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

        # Build execution order from depends_on (topological sort)
        node_map = {n["id"]: n for n in graph if isinstance(n, dict) and "id" in n}
        order = _topo_sort(graph)
        state: dict[str, Any] = dict(inputs)  # accumulated outputs
        loop = asyncio.get_running_loop()
        all_ok = True

        # Load workflow meta for M5 compute bridge (source pipeline_id)
        _wf_meta = _load_wf_meta(tenant_id, wid)

        # Load connector module for MCP config generation (soft dep)
        try:
            from .connectors import build_mcp_config_for_node as _build_mcp  # noqa: E402
            _CONNECTORS_OK = True
        except Exception:
            _build_mcp = None  # type: ignore[assignment]
            _CONNECTORS_OK = False

        for node_id in order:
            node = node_map.get(node_id, {})
            node_type = str(node.get("type", "agent"))
            _raw_instructions = str(node.get("instructions", f"Execute step: {node_id}"))
            # ADR-0091 M5: enrich analyst node prompts with existing chart context
            instructions = _enrich_prompt_with_charts(
                _raw_instructions, node, _wf_meta, tenant_id
            )
            node_tools: list[str] = list(node.get("tools") or [])
            yield _emit({"type": "node_started", "node_id": node_id, "ts": time.time(),
                         "node_type": node_type, "tools": node_tools})

            t0 = time.time()

            # ── Round-4 finding #1 (HIGH): fail-closed, audit-first pre-spawn gate ──
            # EVERY workflow node spawns one or more `claude -p` processes (the
            # agent path, the delegation_loop manager + workers, the fan_out
            # workers). The bridge adapter gates every OS-turn spawn with the L44
            # acceptable-use + ADR-0141 capability + L34/L35 flow gates; the
            # workflow runner did NOT — an authenticated ungated LLM spawn path.
            # Classify the node's `instructions` text (the manager/agent
            # instruction; worker prompts are LLM-derived FROM it) BEFORE any
            # spawn branch below. On deny the gate has already written its
            # house_rules.* / data_flow.* / egress.* L16 event (audit-first); we
            # fail the node (node_failed) and do NOT spawn.
            _node_refusal = _spawn_gates.check_console_spawn_or_refusal(
                instructions, tenant_id=tenant_id, persona="assistant",
                channel="workflow", chat_key=f"workflow:{wid}:{node_id}",
                engine_id="claude_code",
            )
            if _node_refusal is not None:
                elapsed = round(time.time() - t0, 2)
                state[node_id] = {"error": _node_refusal}
                yield _emit({"type": "node_failed", "node_id": node_id,
                             "error": _node_refusal, "elapsed_s": elapsed,
                             "ts": time.time()})
                all_ok = False
                break

            # ── deliver node: write to bridge outbox, no LLM call ─────
            if node_type == "deliver":
                try:
                    output_text = await loop.run_in_executor(
                        None, _execute_deliver_node, node, state
                    )
                    elapsed = round(time.time() - t0, 2)
                    state[node_id] = {"output": output_text}
                    yield _emit({
                        "type": "node_completed",
                        "node_id": node_id,
                        "elapsed_s": elapsed,
                        "tokens": 0,
                        "output_preview": output_text[:200],
                        "output": output_text,
                        "ts": time.time(),
                    })
                except Exception as node_exc:
                    elapsed = round(time.time() - t0, 2)
                    state[node_id] = {"error": str(node_exc)}
                    yield _emit({"type": "node_failed", "node_id": node_id,
                                 "error": str(node_exc), "elapsed_s": elapsed, "ts": time.time()})
                    all_ok = False
                    break
                continue  # skip the LLM path below

            # ── approval node: pause for human (Newman in the Loop) ───
            if node_type == "approval":
                message = str(node.get("message", "Please review and approve or reject to continue."))
                timeout_s = int(node.get("timeout_s", 3600))
                ap_path = _approval_path(tenant_id, wid, rid)
                _write_atomic(ap_path, {
                    "status": "pending",
                    "node_id": node_id,
                    "message": message,
                    "ts": time.time(),
                })
                yield _emit({
                    "type": "node_awaiting_approval",
                    "node_id": node_id,
                    "message": message,
                    "timeout_s": timeout_s,
                    "ts": time.time(),
                })
                deadline = time.time() + timeout_s
                decision: dict[str, Any] | None = None
                while time.time() < deadline:
                    await asyncio.sleep(2.0)
                    try:
                        raw_dec = ap_path.read_text(encoding="utf-8")
                        dec_data = json.loads(raw_dec)
                        if dec_data.get("status") in ("approved", "rejected"):
                            decision = dec_data
                            break
                    except Exception:
                        pass
                elapsed = round(time.time() - t0, 2)
                if decision and decision.get("status") == "approved":
                    state[node_id] = {"output": "approved", "comment": decision.get("comment", "")}
                    yield _emit({
                        "type": "node_completed",
                        "node_id": node_id,
                        "elapsed_s": elapsed,
                        "tokens": 0,
                        "output_preview": "Approved ✓",
                        "output": "approved",
                        "ts": time.time(),
                    })
                elif decision and decision.get("status") == "rejected":
                    reason = decision.get("comment") or "Rejected by reviewer"
                    state[node_id] = {"error": f"rejected: {reason}"}
                    yield _emit({
                        "type": "node_failed",
                        "node_id": node_id,
                        "error": f"Rejected: {reason}",
                        "elapsed_s": elapsed,
                        "ts": time.time(),
                    })
                    all_ok = False
                    break
                else:
                    state[node_id] = {"error": "approval timeout"}
                    yield _emit({
                        "type": "node_failed",
                        "node_id": node_id,
                        "error": "Approval timed out — no decision received",
                        "elapsed_s": elapsed,
                        "ts": time.time(),
                    })
                    all_ok = False
                    break
                continue  # skip LLM path

            # ── delegation_loop ────────────────────────────────────────
            if node_type == "delegation_loop":
                forge_tools: list[str] = list(node.get("forge_tools") or [])
                node_skills: list[str] = list(node.get("skills") or [])
                mcp_cfg = _build_mcp_with_forge(tenant_id, node_tools, forge_tools)

                # Inject skill content into state context
                if node_skills:
                    skill_blocks = []
                    for sk in node_skills:
                        body = _read_skill_content(sk)
                        if body:
                            skill_blocks.append(f"<skill:{sk}>\n{body}\n</skill:{sk}>")
                    if skill_blocks:
                        state[f"__skills_{node_id}__"] = "\n\n".join(skill_blocks)

                try:
                    output_text = await loop.run_in_executor(
                        None, _run_delegation_loop_node, node, state,
                        mcp_cfg or None, tenant_id, node_id, wid
                    )
                    elapsed = round(time.time() - t0, 2)
                    state[node_id] = {"output": output_text}
                    yield _emit({"type": "node_completed", "node_id": node_id,
                                 "elapsed_s": elapsed, "tokens": 0,
                                 "output_preview": output_text[:200],
                                 "output": output_text[:50_000], "ts": time.time()})
                    # Scan output for media artifacts (ADR-0091 M2)
                    _media = _scan_output_for_media(output_text, node_id, tenant_id, wid, rid)
                    for _m in _media:
                        yield _emit({**_m, "type": "media"})
                except Exception as exc:
                    elapsed = round(time.time() - t0, 2)
                    state[node_id] = {"error": str(exc)}
                    yield _emit({"type": "node_failed", "node_id": node_id,
                                 "error": str(exc), "elapsed_s": elapsed, "ts": time.time()})
                    all_ok = False
                    break
                continue

            # ── fan_out ───────────────────────────────────────────────
            if node_type == "fan_out":
                forge_tools = list(node.get("forge_tools") or [])
                mcp_cfg = _build_mcp_with_forge(tenant_id, node_tools, forge_tools)
                try:
                    output_text = await loop.run_in_executor(
                        None, _run_fan_out_node, node, state,
                        mcp_cfg or None, tenant_id, node_id, wid
                    )
                    elapsed = round(time.time() - t0, 2)
                    state[node_id] = {"output": output_text}
                    yield _emit({"type": "node_completed", "node_id": node_id,
                                 "elapsed_s": elapsed, "tokens": 0,
                                 "output_preview": output_text[:200],
                                 "output": output_text[:50_000], "ts": time.time()})
                    # Scan output for media artifacts (ADR-0091 M2)
                    _media = _scan_output_for_media(output_text, node_id, tenant_id, wid, rid)
                    for _m in _media:
                        yield _emit({**_m, "type": "media"})
                except Exception as exc:
                    elapsed = round(time.time() - t0, 2)
                    state[node_id] = {"error": str(exc)}
                    yield _emit({"type": "node_failed", "node_id": node_id,
                                 "error": str(exc), "elapsed_s": elapsed, "ts": time.time()})
                    all_ok = False
                    break
                continue

            # ── agent / http / other → claude -p ─────────────────────
            forge_tools = list(node.get("forge_tools") or [])
            node_skills = list(node.get("skills") or [])

            # Build context from upstream outputs
            context_parts: list[str] = [f"Step: {node_id}", f"Instructions: {instructions}"]
            if node_tools:
                context_parts.append(
                    f"Available MCP connectors: {', '.join(node_tools)}. "
                    "Use them as needed to complete the instructions."
                )
            if forge_tools:
                context_parts.append(
                    f"Available forge tools: {', '.join(forge_tools)}. "
                    "These are custom tools available via the forge MCP server."
                )
            if node_skills:
                skill_blocks = []
                for sk in node_skills:
                    body = _read_skill_content(sk)
                    if body:
                        skill_blocks.append(f"<skill:{sk}>\n{body}\n</skill:{sk}>")
                if skill_blocks:
                    context_parts.append("Active skills:\n" + "\n\n".join(skill_blocks))

            deps = node.get("depends_on") or []
            for dep in deps:
                dep_out = state.get(dep, {})
                if dep_out:
                    out_text = dep_out.get("output", "") if isinstance(dep_out, dict) else str(dep_out)
                    context_parts.append(f"Output from '{dep}':\n{out_text[:1000]}")
            if inputs:
                context_parts.append(f"Workflow inputs: {json.dumps(inputs, ensure_ascii=False)[:300]}")
            prompt = "\n\n".join(context_parts)

            # Build MCP config (connectors + forge)
            mcp_config: dict | None = None
            if node_tools or forge_tools:
                mcp_config = _build_mcp_with_forge(tenant_id, node_tools, forge_tools)
            elif node_tools:
                mcp_config = {"mcpServers": {}}

            try:
                output_text = await loop.run_in_executor(None, _run_node_claude, prompt, mcp_config)
                elapsed = round(time.time() - t0, 2)
                state[node_id] = {"output": output_text}
                yield _emit({
                    "type": "node_completed",
                    "node_id": node_id,
                    "elapsed_s": elapsed,
                    "tokens": 0,
                    "output_preview": output_text[:200],
                    "output": output_text[:50_000],
                    "ts": time.time(),
                })
                # Scan output for media artifacts (ADR-0091 M2)
                _media = _scan_output_for_media(output_text, node_id, tenant_id, wid, rid)
                # Scan output for table artifacts
                _tables = _scan_output_for_tables(output_text, node_id, tenant_id, wid, rid)
                # ADR-0091 M5: for x_compute nodes inject pipeline stage charts + tables
                if node.get("x_compute"):
                    _tool = node["x_compute"].get("tool_name", "")
                    _media += _scan_compute_stage_artifacts(
                        tenant_id, node_id, _tool, wid, rid, _wf_meta
                    )
                    _tables += _scan_compute_stage_tables(
                        tenant_id, node_id, _tool, wid, rid, _wf_meta
                    )
                for _m in _media:
                    yield _emit({**_m, "type": "media"})
                for _t in _tables:
                    yield _emit({**_t, "type": "table"})
            except Exception as node_exc:
                elapsed = round(time.time() - t0, 2)
                state[node_id] = {"error": str(node_exc)}
                yield _emit({"type": "node_failed", "node_id": node_id,
                              "error": str(node_exc), "elapsed_s": elapsed, "ts": time.time()})
                all_ok = False
                break  # stop on first failure; could make configurable

        yield _emit({"type": "run_completed", "ok": all_ok, "budget": {}, "ts": time.time()})
        run_meta.update({"status": "complete" if all_ok else "failed", "ok": all_ok, "finished_at": time.time()})

    except Exception as exc:
        yield _emit({"type": "error", "ts": time.time(), "message": str(exc)})
        run_meta.update({"status": "failed", "ok": False, "error": str(exc), "finished_at": time.time()})

    _write_atomic(_run_meta_path(tenant_id, wid, rid), run_meta)
    console_audit.action_performed(
        tenant_id=tenant_id,
        sid_fingerprint=sid_fingerprint,
        action="workflow.run_completed",
        target_kind="workflow",
        target_id=wid,
        run_id=rid,
    )

@router.post("/workflows/{wid}/runs")
def start_run(
    wid: str,
    body: StartRunRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> StreamingResponse:
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)
    yaml_p = _yaml_path(rec.tenant_id, wid)
    if not yaml_p.exists():
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "no YAML defined for this workflow")
    yaml_text = yaml_p.read_text(encoding="utf-8")
    if _YAML_OK:
        try:
            _parsed_check = _yaml.safe_load(yaml_text) or {}
            _graph_check = _parsed_check.get("orchestration", {}).get("graph") or []
            if not _graph_check:
                raise HTTPException(
                    http_status.HTTP_400_BAD_REQUEST,
                    f"Workflow is still in '{meta.get('phase', 'unknown')}' phase — "
                    "use the design assistant to build the graph before running.",
                )
        except HTTPException:
            raise
        except Exception:
            pass  # YAML parse errors are caught later in _stream_run

    # Generate run ID early so all compliance checks can reference it.
    rid = secrets.token_hex(_RID_BYTES)

    # ADR-0094: enforce workflows_concurrent limit before starting the run.
    # _lic_assert is ALWAYS defined (either from license.validator or from the
    # FREE_TIER stub in the ImportError fallback) — the _WF_LIC_OK guard was
    # wrong and would silently skip concurrent enforcement when the validator
    # module failed to import. Outer try/except still fail-opens on genuine
    # infrastructure errors (I/O, etc.) per the original intent.
    try:
        _running = _count_running_workflows(rec.tenant_id)
        try:
            _lic_assert("workflows_concurrent", _running + 1)
        except _LicLimitError as _wf_exc:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="workflow.run_started",
                target_kind="workflow",
                target_id=wid,
                reason="quota_exceeded",
            )
            raise HTTPException(
                status_code=http_status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "license_limit",
                    "feature": "workflows_concurrent",
                    "running": _running,
                    "limit": _lic_get_limit("workflows_concurrent"),
                    "msg": str(_wf_exc),
                    "upgrade_url": "https://corvin-labs.com/pricing",
                },
            ) from _wf_exc
    except HTTPException:
        raise
    except Exception as _wf_unexpected:
        # WF-CONC-02 (ADR-0148): the concurrent-workflow gate erroring unexpectedly
        # (e.g. _count_running_workflows could not enumerate the runs tree) must
        # DENY, not "allow run" — a fail-open here lets a run slip past the cap.
        _log.warning(
            "license: concurrent-workflow check raised unexpectedly (%s) — refusing run (fail-closed)",
            _wf_unexpected,
        )
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="concurrent-workflow limit check unavailable — try again",
        ) from _wf_unexpected

    # ADR-0149 LIC-WFRUN-01: a workflow run spawns paid `claude -p` compute per
    # node (delegation_loop fans out to manager + workers), but start_run only
    # enforced workflows_concurrent (a CONCURRENCY axis) — the per-UTC-day VOLUME
    # ceiling (compute_units_per_day) never bound the workflow surface, so a
    # free-tier tenant could run unbounded paid compute serially. Charge the run
    # against the SAME persistent daily counter as the four compute entrypoints,
    # synchronously here (before the StreamingResponse) so a 402 reaches the
    # client before any node spawns. dry_run spawns no claude → exempt. The two
    # gates are orthogonal: concurrency (above) and daily volume (here).
    if not body.dry_run:
        from ._compute_license_gate import enforce_compute_quota  # noqa: PLC0415

        enforce_compute_quota(
            rec.tenant_id, rec.sid_fingerprint,
            audit_action="workflow.run_started", channel="workflows",
        )

    # ── Fix 2: Consent gate (DSGVO Art. 6) ──────────────────────────────
    consent_err = _check_consent_policy(
        rec.tenant_id, wid, rid, rec.sid_fingerprint
    )
    if consent_err:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, consent_err)

    # ── Fix 3: Compliance zone / engine policy (EU AI Act Art. 14) ──────
    zone_err = _check_compliance_zone(
        rec.tenant_id, wid, rid, rec.sid_fingerprint
    )
    if zone_err:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, zone_err)

    # ── Fix 1: Audit with run_id (DSGVO Art. 30) ────────────────────────
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.run_started",
        target_kind="workflow",
        target_id=wid,
        run_id=rid,
        trigger="manual",
    )
    return StreamingResponse(
        _stream_run(rec.tenant_id, rec.sid_fingerprint, wid, rid, yaml_text, body.inputs, body.dry_run),
        media_type="text/event-stream",
        headers={"X-Run-Id": rid, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ── Routes: schedule ───────────────────────────────────────────────────────

@router.get("/workflows/{wid}/schedule")
def get_schedule(
    wid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)
    return {"schedule": meta.get("schedule"), "has_schedule": bool(meta.get("has_schedule"))}

@router.put("/workflows/{wid}/schedule")
def set_schedule(
    wid: str,
    body: SetScheduleRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)

    # Remove old scheduler task if any (Phase 4)
    if _SCHEDULER_OK and meta.get("schedule_task_id"):
        try:
            _sched.remove_task(meta["schedule_task_id"])
        except Exception:
            pass

    meta["schedule"] = {"cron": body.cron, "timezone": body.timezone, "overrun": body.overrun}
    meta["has_schedule"] = True
    meta["updated_at"] = time.time()

    # Register with corvin-scheduler (Phase 4)
    if _SCHEDULER_OK:
        try:
            task = _sched.add_task(
                channel="console",
                chat_id=rec.tenant_id,
                sender="console",
                text=f"Scheduled run: workflow {wid}",
                when=body.cron,
                kind="workflow",
                workflow_name=wid,
                workflow_inputs={},
                tenant_id=rec.tenant_id,
            )
            meta["schedule_task_id"] = task["id"]
        except Exception:
            meta.pop("schedule_task_id", None)  # scheduler unavailable, stored in meta only

    # Audit-first: write event before persisting to disk.
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.scheduled",
        target_kind="workflow",
        target_id=wid,
    )
    _write_atomic(_meta_path(rec.tenant_id, wid), meta)
    return {
        "ok": True,
        "schedule": meta["schedule"],
        "scheduler_registered": _SCHEDULER_OK and "schedule_task_id" in meta,
    }

@router.delete("/workflows/{wid}/schedule")
def delete_schedule(
    wid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)

    # Unregister from corvin-scheduler (Phase 4)
    if _SCHEDULER_OK and meta.get("schedule_task_id"):
        try:
            _sched.remove_task(meta["schedule_task_id"])
        except Exception:
            pass

    meta.pop("schedule", None)
    meta.pop("schedule_task_id", None)
    meta["has_schedule"] = False
    meta["updated_at"] = time.time()
    # Audit-first: write event before persisting to disk.
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="workflow.unscheduled",
        target_kind="workflow",
        target_id=wid,
    )
    _write_atomic(_meta_path(rec.tenant_id, wid), meta)
    return {"ok": True}

# ── Workflow explanation (M7 ADR-0062) ────────────────────────────────────

@router.post("/workflows/{wid}/explain")
def explain_workflow(
    wid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Plain-language explanation of the workflow YAML via claude -p (Haiku-4.5).

    Result is cached in meta for 5 minutes.
    Must NOT import anthropic — subprocess only.
    """
    _validate_wid(wid)
    meta = _require_workflow(rec.tenant_id, wid)

    cached = meta.get("explanation")
    cached_ts = meta.get("explanation_ts", 0.0)
    if cached and (time.time() - cached_ts) < 300:
        return {"ok": True, "explanation": cached, "cached": True}

    yaml_path = _yaml_path(rec.tenant_id, wid)
    if not yaml_path.exists():
        return {"ok": True, "explanation": "Noch kein Workflow-YAML vorhanden.", "cached": False}

    yaml_text = yaml_path.read_text(encoding="utf-8")[:8_000]

    # ── Round-N finding #1: fail-closed, audit-first pre-spawn gate ──────────
    # The workflow YAML is user-controlled and is fed verbatim to `claude -p`.
    # Gate it with the SAME four console gates (L44/capability/L34/L35) before
    # the subprocess.run; on a non-None refusal return it AS the explanation and
    # skip the spawn (mirrors the design-assistant handler). The gate already
    # wrote its L16 deny event synchronously, audit-first, before returning.
    _explain_refusal = _spawn_gates.check_console_spawn_or_refusal(
        yaml_text, tenant_id=rec.tenant_id, persona="assistant",
        channel="workflow-explain", chat_key=f"workflow-explain:{wid}",
        engine_id="claude_code",
    )
    if _explain_refusal is not None:
        meta["explanation"] = _explain_refusal
        meta["explanation_ts"] = time.time()
        _write_atomic(_meta_path(rec.tenant_id, wid), meta)
        return {"ok": True, "explanation": _explain_refusal, "cached": False}

    system = (
        "You are a helpful assistant that explains AWP workflows in plain German. "
        "Given an AWP workflow YAML, describe what the workflow does overall in 2-3 German sentences. "
        "Then list each node on its own line exactly in the format: NodeName — was es tut. "
        "Be concise. Do NOT output YAML, code, or JSON."
    )
    try:
        result = subprocess.run(
            ["claude", "-p", "--max-turns", "1", "--tools", "", "--model", "claude-haiku-4-5",
             "--system-prompt", system, yaml_text],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
        )
        explanation = result.stdout.strip() or "Erklärung nicht verfügbar."
    except FileNotFoundError:
        explanation = "Claude CLI nicht gefunden."
    except subprocess.TimeoutExpired:
        explanation = "Zeitüberschreitung beim Generieren der Erklärung."
    except Exception as exc:
        explanation = f"Fehler: {exc}"

    meta["explanation"] = explanation
    meta["explanation_ts"] = time.time()
    _write_atomic(_meta_path(rec.tenant_id, wid), meta)
    return {"ok": True, "explanation": explanation, "cached": False}

# ── Design assistant (WebSocket chat) ────────────────────────────────────

_TEMPLATES: dict[str, dict[str, Any]] = {
    "daily-digest": {
        "keywords": ["daily", "digest", "rss", "feed", "morning", "news", "summary"],
        "steps": "TRIGGER > fetch_content > summarise > deliver",
        "yaml": (
            'awp: "1.0.0"\n'
            'workflow:\n'
            '  name: daily_digest\n'
            '  description: "Fetch and summarise content, then deliver a digest."\n'
            'orchestration:\n'
            '  engine: dag\n'
            '  graph:\n'
            '    - id: cron_trigger\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: []\n'
            '      instructions: "Triggered by cron schedule."\n'
            '    - id: fetch_content\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [cron_trigger]\n'
            '      instructions: "Fetch content from configured sources."\n'
            '    - id: summarise\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [fetch_content]\n'
            '      instructions: "Summarise the fetched content into a concise digest."\n'
            '    - id: deliver\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [summarise]\n'
            '      instructions: "Send the digest to the configured channel."\n'
        ),
    },
    "inbox-monitor": {
        "keywords": ["inbox", "email", "mail", "monitor", "filter", "classify", "route"],
        "steps": "email_trigger > filter > classify > route",
        "yaml": (
            'awp: "1.0.0"\n'
            'workflow:\n'
            '  name: inbox_monitor\n'
            '  description: "Monitor inbox, classify emails, and route them."\n'
            'orchestration:\n'
            '  engine: dag\n'
            '  graph:\n'
            '    - id: email_trigger\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: []\n'
            '      instructions: "Triggered when new email arrives."\n'
            '    - id: filter_emails\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [email_trigger]\n'
            '      instructions: "Filter relevant emails."\n'
            '    - id: classify\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [filter_emails]\n'
            '      instructions: "Classify emails by topic or priority."\n'
            '    - id: route\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [classify]\n'
            '      instructions: "Route emails to the appropriate handler."\n'
        ),
    },
    "data-pipeline": {
        "keywords": ["data", "pipeline", "fetch", "transform", "store", "etl", "http"],
        "steps": "http_fetch > transform > store > notify",
        "yaml": (
            'awp: "1.0.0"\n'
            'workflow:\n'
            '  name: data_pipeline\n'
            '  description: "Fetch, transform, store, and notify about data."\n'
            'orchestration:\n'
            '  engine: dag\n'
            '  graph:\n'
            '    - id: http_fetch\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: []\n'
            '      instructions: "Fetch data from HTTP source."\n'
            '    - id: transform\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [http_fetch]\n'
            '      instructions: "Transform and clean the data."\n'
            '    - id: store\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [transform]\n'
            '      instructions: "Store the processed data."\n'
            '    - id: notify\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [store]\n'
            '      instructions: "Send completion notification."\n'
        ),
    },
    "approval-loop": {
        "keywords": ["approval", "loop", "delegate", "review", "accept", "reject", "condition"],
        "steps": "submission > delegation_loop > on_accept / on_reject",
        "yaml": (
            'awp: "1.0.0"\n'
            'workflow:\n'
            '  name: approval_loop\n'
            '  description: "Route items through approval and branch on outcome."\n'
            'orchestration:\n'
            '  engine: dag\n'
            '  graph:\n'
            '    - id: submission\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: []\n'
            '      instructions: "Receive submission for approval."\n'
            '    - id: review\n'
            '      type: delegation_loop\n'
            '      agent: assistant\n'
            '      depends_on: [submission]\n'
            '      instructions: "Manager reviews and decides."\n'
            '      config:\n'
            '        manager: assistant\n'
            '        budget:\n'
            '          max_loops: 3\n'
            '          max_total_workers: 5\n'
            '    - id: on_accept\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [review]\n'
            '      instructions: "Handle approval outcome."\n'
            '    - id: on_reject\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: [review]\n'
            '      instructions: "Handle rejection outcome."\n'
        ),
    },
}

def _match_template(text: str) -> tuple[str, float] | None:
    lower = text.lower()
    best_key: str | None = None
    best_score = 0.0
    for key, tmpl in _TEMPLATES.items():
        keywords = tmpl["keywords"]
        hits = sum(1 for kw in keywords if kw in lower)
        score = hits / len(keywords) if keywords else 0.0
        if score > best_score:
            best_score = score
            best_key = key
    if best_score >= 0.25 and best_key:
        return (best_key, best_score)
    return None

_DESIGN_SYSTEM_PROMPT = """\
You are a workflow design assistant for Corvin (AWP format).
Help the operator design AWP workflows through conversation.

Current phase: {phase}
Current AWP YAML:
{yaml}

Rules:
1. Ask at most ONE focused question per turn — phone-first UX.
2. Stay in the current phase (discovering/structuring/detailing/ready); do not jump ahead.
3. When the canvas should change, output exactly: YAML_UPDATE: followed by the complete new AWP YAML on the next lines (until the next sentinel or end of response). Never output partial YAML.
4. When the phase should advance, output exactly: PHASE_UPDATE: <new_phase>
5. At the Structuring→Detailing and Detailing→Ready transitions, output: SUMMARY_CARD: {{"goal":"...","trigger":"...","steps":["..."],"conditions":["..."]}}
6. Detect gaps and warn once: no trigger, disconnected nodes, no output delivery.
7. Keep replies short (2-4 sentences max).

AWP snippet for reference:
awp: "1.0.0"
workflow:
  name: snake_case_name
  description: "..."
orchestration:
  engine: dag
  graph:
    - id: step_id
      type: agent
      agent: assistant
      depends_on: []
      instructions: "..."
"""

def _tts_b64(text: str, lang: str = "en") -> str | None:
    """Generate TTS audio via say.py and return base64-encoded OGG, or None on failure."""
    say_script = _VOICE_SCRIPTS / "say.py"
    if not say_script.exists():
        return None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        result = subprocess.run(
            [sys.executable, str(say_script), "--lang", lang, "--out", tmp, text],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        audio_bytes = Path(tmp).read_bytes()
        if not audio_bytes:
            return None  # TTS silently disabled (no key etc.)
        return base64.b64encode(audio_bytes).decode("ascii")
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass

def _run_claude_for_design(prompt: str) -> str:
    """Call claude -p with the full prompt via stdin to avoid arg-length limits."""
    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=90,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except FileNotFoundError:
        return ""
    except Exception:
        return ""

_AWP_YAML_PROMPT = """\
Based on this conversation about a workflow, generate a complete valid AWP YAML.

STRICT RULES — violations will break the system:
- Only these node types: agent | fan_out | delegation_loop | deliver | approval
- agent must always be: assistant
- ALL nodes require depends_on (list, may be empty)
- ALL nodes require instructions (one clear sentence describing what this step does)
  EXCEPTION: deliver and approval nodes do NOT need instructions
- Workflow name must be snake_case
- Output ONLY the YAML block — no explanation, no markdown fences
- If inputs or outputs were discussed, declare them under workflow.inputs / workflow.outputs

EXACT FORMAT (copy this structure):
awp: "1.0.0"
workflow:
  name: workflow_name
  description: "One sentence description"
  inputs:                    # optional — declare if inputs were discussed
    - name: topic
      description: "The topic to research"
      required: true
  outputs:                   # optional — declare if outputs were discussed
    - name: report
      description: "The final markdown report"
orchestration:
  engine: dag
  graph:
    - id: step_one
      type: agent
      agent: assistant
      depends_on: []
      instructions: "Fetch data from source X"
      # tools: [gmail]   ← add only if the node needs an external connector
    - id: step_two
      type: agent
      agent: assistant
      depends_on: [step_one]
      instructions: "Process the fetched data and format it"

If a node needs an external service (Gmail, Drive, GitHub, Brave, Slack, etc.), add:
  tools: [connector_id]   (e.g. tools: [gmail] or tools: [gdrive, gcalendar])

For delivery to a messenger (Discord, Telegram, Slack, WhatsApp, Email), use type: deliver:
    - id: send_result
      type: deliver
      depends_on: [previous_node]
      config:
        channel: discord      # discord | telegram | slack | whatsapp | email
        chat_id: auto         # "auto" = same chat that triggered the workflow, or explicit ID
        format: markdown      # text | markdown
        voice: false          # true = also send TTS voice note

For parallel processing over a list, use type: fan_out:
    - id: process_items
      type: fan_out
      agent: assistant
      depends_on: [fetch_list]
      items_from: fetch_list.output   # field in the upstream output
      instructions: "Process this item: {{item}}"

For complex multi-step reasoning with manager + workers, use type: delegation_loop:
    - id: research_loop
      type: delegation_loop
      agent: assistant
      depends_on: [init_step]
      instructions: "Research X thoroughly using multiple sub-agents"
      config:
        manager: assistant
        budget:
          max_loops: 3
          max_total_workers: 6

For human-in-the-loop review checkpoints (Newman in the Loop), use type: approval:
    - id: human_review
      type: approval
      depends_on: [previous_step]
      message: "Please review the results above and approve or reject to continue."
      timeout_s: 3600   # max wait time in seconds (default 3600 = 1 hour)

For forge tools (custom code tools) and skills, add to agent nodes:
    - id: analyze
      type: agent
      forge_tools: [tool_name]   # forge tool IDs
      skills: [skill-name]       # SkillForge skill names

Conversation:
{history}

Generate the AWP YAML now:"""

def _chat_history_text(chat: list[dict[str, Any]], n: int = 10) -> str:
    lines: list[str] = []
    for m in chat[-n:]:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str) and content and role in ("user", "assistant"):
            lines.append(f"{'User' if role == 'user' else 'Assistant'}: {content}")
    return "\n".join(lines)

def _generate_awp_yaml(chat: list[dict[str, Any]], user_msg: str, wid: str) -> str | None:
    """Call Claude to generate a valid AWP YAML from conversation context."""
    full_chat = chat + [{"role": "user", "content": user_msg}]
    history = _chat_history_text(full_chat, n=12)
    prompt = _AWP_YAML_PROMPT.format(history=history)
    raw = _run_claude_for_design(prompt)
    if not raw:
        return None
    # Strip markdown fences if Claude added them
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(l for l in lines if not l.strip().startswith("```"))
    # Must start with awp:
    if 'awp:' not in raw and 'workflow:' not in raw:
        return None
    # Ensure it starts at awp:
    lines = raw.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().startswith("awp:")), None)
    if start is not None:
        raw = "\n".join(lines[start:])
    return raw.strip() or None

def _get_connected_connector_ids(tenant_id: str) -> list[str]:
    """Return connector IDs that are currently enabled."""
    try:
        from .connectors import _read_enabled, _CATALOG_BY_ID, _connector_status, _read_vault
        enabled_cfg = _read_enabled(tenant_id)
        vault = _read_vault(tenant_id)
        connected = []
        for cid in _CATALOG_BY_ID:
            if _connector_status(cid, enabled_cfg, vault) == "connected":
                connected.append(cid)
        return connected
    except Exception:
        return []

_DE_MARKERS = frozenset([
    "ich", "ist", "die", "der", "das", "und", "nicht", "ein", "eine", "mein",
    "meine", "haben", "habe", "mit", "für", "auch", "aber", "wie", "was",
    "wann", "wo", "warum", "bitte", "danke", "ja", "nein", "hier", "dann",
    "wenn", "oder", "wir", "ihr", "sie", "er", "es", "du", "zu", "von", "in",
    "soll", "kann", "muss", "will", "möchte", "bitte", "erstelle", "mach",
    "zeig", "gibt", "hat", "hast", "bin", "sind", "wird", "würde", "wäre",
])

def _detect_lang(text: str) -> str:
    """Return 'de' when German is detected in text, 'en' otherwise."""
    words = re.findall(r'\b\w+\b', text.lower())
    de_count = sum(1 for w in words if w in _DE_MARKERS)
    return "de" if de_count >= 2 else "en"


def _build_chat_prompt(
    phase: str,
    nodes_summary: str,
    history: str,
    user_msg: str,
    will_build: bool,
    connected_tools: list[str] | None = None,
    lang: str = "en",
) -> str:
    if lang == "de":
        phase_hints = {
            "discovering": (
                "Du führst den Nutzer Schritt für Schritt durch die Workflow-Planung. "
                "Halte dich genau an diese Reihenfolge — stelle EINE Frage auf einmal und gehe weiter, wenn beantwortet:\n"
                "  Schritt 1: Ziel — Was soll der Workflow erreichen? (überspringe, wenn bereits beschrieben)\n"
                "  Schritt 2: Eingaben — Welche Daten/Parameter braucht er? (z.B. Thema, Datei, URL, Datum)\n"
                "  Schritt 3: Ausgaben — Was soll er produzieren? (z.B. Bericht, Nachricht, Datei)\n"
                "  Schritt 4: Schritte — Wie viele Hauptschritte? Braucht ein Schritt menschliche Freigabe (Human-in-the-Loop)?\n"
                "Wenn du Ziel + Eingaben + Ausgaben hast, sage: 'Super, ich habe genug. Ich erstelle den Workflow jetzt.'"
            ),
            "structuring": "Das Gerüst ist auf der Leinwand. Stelle EINE Bestätigungsfrage: 'Sieht die Struktur so richtig aus? Braucht ein Schritt eine Freigabe?'",
            "detailing": "Geh Knoten für Knoten durch — stelle EINE konkrete Frage zum nächsten unkonfigurierten Knoten. Bei Freigabe-Knoten: kläre die Prüfnachricht.",
            "ready": "Workflow ist fertig. Sage dem Nutzer, dass er bereit ist, und empfehle den Test-Button (oder Dry-run zum Prüfen ohne LLM-Aufrufe).",
        }
        lang_instruction = "Antworte auf Deutsch. Kurze, klare Sätze."
        build_hint = "\n\nWICHTIG: Nach deiner Antwort generiert das System automatisch den Workflow-Graphen aus diesem Gespräch." if will_build else ""
        tools_hint = ""
        if connected_tools:
            tools_hint = (
                f"\n\nVerfügbare Konnektoren: {', '.join(connected_tools)}. "
                "Schlage bei Bedarf tools: [id] für Knoten vor (z.B. tools: [gmail] für E-Mail-Schritte). "
                "Für Messenger-Ausgabe (Discord, Telegram etc.) einen deliver-Knoten hinzufügen: "
                "type: deliver | config: {channel: discord, chat_id: auto, format: markdown}."
            )
    else:
        phase_hints = {
            "discovering": (
                "You are interviewing the user step-by-step to design a workflow. "
                "Follow this exact order — ask ONE question at a time and move on once answered:\n"
                "  Step 1: Goal — what should this workflow accomplish? (already answered if user described it)\n"
                "  Step 2: Inputs — what data/parameters does it need to start? (e.g. topic, date, file, URL, keywords)\n"
                "  Step 3: Outputs — what should it produce? (e.g. report, message, file, notification)\n"
                "  Step 4: Steps — how many main processing steps? Should any step need human approval (Human-in-the-Loop)?\n"
                "Once you have goal + inputs + outputs, say: 'Great, I have enough to build the workflow. Let me create it now.'"
            ),
            "structuring": "The skeleton is on the canvas. Ask ONE confirmation: 'Does this structure look right? Any approval steps needed?'",
            "detailing": "Walk through each node — ask ONE specific question about the next unconfigured node. For approval nodes, clarify the review message.",
            "ready": "Workflow is complete. Tell the user it's ready and suggest using the Test button (or Dry-run to check without LLM calls).",
        }
        lang_instruction = "Reply in English. Short, clear sentences."
        build_hint = "\n\nIMPORTANT: After your reply, the system will automatically generate the workflow graph from this conversation." if will_build else ""
        tools_hint = ""
        if connected_tools:
            tools_hint = (
                f"\n\nAvailable connectors: {', '.join(connected_tools)}. "
                "When relevant, suggest adding tools: [id] to nodes (e.g. tools: [gmail] for email steps). "
                "When the user wants output delivered to a messenger (Discord, Telegram, etc.), add a deliver node: "
                "type: deliver | config: {channel: discord, chat_id: auto, format: markdown}."
            )

    hint = phase_hints.get(phase, "")

    return f"""You are a concise workflow design assistant (phone UX — short replies).
Phase: {phase}
Language: {lang_instruction}

{hint}{nodes_summary}{build_hint}{tools_hint}

{history}
User: {user_msg}
Assistant (2-4 sentences, ONE question max, be direct):"""

def _is_confirmation(msg: str) -> bool:
    """Heuristic: did the user confirm/approve the current step?"""
    lower = msg.lower()
    yes_words = ["yes", "ja", "ok", "okay", "looks good", "correct", "right", "passt",
                 "gut", "prima", "genau", "korrekt", "richtig", "sure", "yep", "👍",
                 "sounds good", "perfect", "great", "go ahead", "proceed"]
    return any(w in lower for w in yes_words)

async def _design_turn(
    tenant_id: str,
    wid: str,
    user_msg: str,
) -> dict[str, Any]:
    meta = _read_json(_meta_path(tenant_id, wid)) or {}
    phase = str(meta.get("phase", "discovering"))
    yaml_p = _yaml_path(tenant_id, wid)
    yaml_text = yaml_p.read_text(encoding="utf-8") if yaml_p.exists() else ""

    chat = _read_chat(tenant_id, wid)
    prior_user_msgs = [m for m in chat if m.get("role") == "user"]
    n_user = len(prior_user_msgs)  # includes the current message (appended before this call)

    # ── Template match on very first message ─────────────────────────
    if n_user <= 1 and phase == "discovering":
        match = _match_template(user_msg)
        if match:
            key, conf = match
            tmpl = _TEMPLATES[key]
            return {
                "reply": (
                    f"That sounds like the '{key.replace('-', ' ')}' pattern. "
                    f"I have a template: {tmpl['steps']}. "
                    "Should I load it as a starting point?"
                ),
                "template_offer": {"key": key, "yaml": tmpl["yaml"], "confidence": conf},
                "yaml_update": None,
                "phase_update": None,
                "summary_card": None,
            }

    # ── Decide whether to auto-build YAML this turn ───────────────────
    # Build after the 2nd user message in discovering (enough context)
    # or when in structuring and user hasn't confirmed yet (show skeleton)
    # or in detailing/ready (keep YAML up to date)
    should_build_yaml = (
        (phase == "discovering" and n_user >= 2) or
        (phase == "structuring" and not yaml_text.strip()) or
        (phase == "detailing")
    )

    # ── Determine phase transition ────────────────────────────────────
    phase_update: str | None = None
    if phase == "discovering" and n_user >= 2:
        phase_update = "structuring"
    elif phase == "structuring" and _is_confirmation(user_msg):
        phase_update = "detailing"
    elif phase == "detailing":
        # Move to ready when user confirms all nodes are fine
        if _is_confirmation(user_msg) and yaml_text.strip():
            existing_graph = _parse_graph(yaml_text)
            if len(existing_graph) >= 2:
                phase_update = "ready"

    # ── Current canvas summary for chat prompt ────────────────────────
    nodes_summary = ""
    if yaml_text.strip():
        graph = _parse_graph(yaml_text)
        if graph:
            steps = " → ".join(n["id"] for n in graph)
            nodes_summary = f"\nCurrent canvas: {steps}"

    history = _chat_history_text(chat, n=8)
    lang = _detect_lang(user_msg + " " + history)
    connected_tools = _get_connected_connector_ids(tenant_id)
    chat_prompt = _build_chat_prompt(phase, nodes_summary, history, user_msg, should_build_yaml, connected_tools, lang)

    # ── Round-4 finding #1: fail-closed, audit-first pre-spawn gate ──────────
    # The design-assistant spawns `claude -p` (`_run_claude_for_design`, and the
    # parallel `_generate_awp_yaml`) on the operator's chat_prompt. Gate it with
    # the SAME four console gates before any spawn; on deny return the refusal as
    # the reply (the gate already wrote its L16 deny event, audit-first).
    _design_refusal = _spawn_gates.check_console_spawn_or_refusal(
        chat_prompt, tenant_id=tenant_id, persona="assistant",
        channel="workflow-design", chat_key=f"workflow-design:{wid}",
        engine_id="claude_code",
    )
    if _design_refusal is not None:
        return {
            "reply": _design_refusal,
            "yaml_update": None,
            "phase_update": None,
            "summary_card": None,
        }

    loop = asyncio.get_running_loop()

    # ── Run chat reply and (optionally) YAML generation in parallel ───
    if should_build_yaml:
        chat_task = loop.run_in_executor(None, _run_claude_for_design, chat_prompt)
        yaml_task = loop.run_in_executor(None, _generate_awp_yaml, chat, user_msg, wid)
        results = await asyncio.gather(chat_task, yaml_task, return_exceptions=True)
        raw_reply = results[0] if not isinstance(results[0], BaseException) else ""
        new_yaml = results[1] if not isinstance(results[1], BaseException) else None
    else:
        raw_reply = await loop.run_in_executor(None, _run_claude_for_design, chat_prompt)
        new_yaml = None

    reply = (raw_reply or _scripted_response(phase, user_msg)).strip()
    yaml_update: str | None = None

    # ── Apply YAML if generated ───────────────────────────────────────
    if new_yaml:
        # Validate before writing
        try:
            if _YAML_OK:
                _yaml.safe_load(new_yaml)  # basic parse check
            _write_atomic(_yaml_path(tenant_id, wid), new_yaml)
            _write_awpkg_sidecar(tenant_id, wid)
            yaml_update = new_yaml
            meta["updated_at"] = time.time()
        except Exception as e:
            # Don't silently fail — include a note in the reply
            reply += f"\n\n_(Note: YAML generation encountered an issue: {e})_"
            yaml_update = None

    # ── Apply phase update ────────────────────────────────────────────
    valid_phases = {"discovering", "structuring", "detailing", "ready"}
    if phase_update and phase_update in valid_phases:
        meta["phase"] = phase_update
        meta["updated_at"] = time.time()

    if yaml_update or (phase_update and phase_update in valid_phases):
        _write_atomic(_meta_path(tenant_id, wid), meta)

    return {
        "reply": reply,
        "yaml_update": yaml_update,
        "phase_update": phase_update,
        "summary_card": None,
        "template_offer": None,
    }

def _scripted_response(phase: str, user_msg: str) -> str:
    """Fallback responses when Claude CLI is unavailable."""
    lower = user_msg.lower()
    if phase == "discovering":
        if any(w in lower for w in ["schedule", "cron", "daily", "hourly", "weekly"]):
            return "Got it — scheduled trigger. Should it process new items only, or replay everything each run?"
        if any(w in lower for w in ["email", "inbox", "mail"]):
            return "Email trigger noted. Should it check all emails or only unread ones?"
        return "Understood. How often should this workflow run — on a schedule, on demand, or triggered by an event?"
    if phase == "structuring":
        return (
            "Here's a first draft of the structure.\n"
            "YAML_UPDATE:\n"
            'awp: "1.0.0"\n'
            'workflow:\n'
            '  name: new_workflow\n'
            '  description: "Auto-generated skeleton."\n'
            'orchestration:\n'
            '  engine: dag\n'
            '  graph:\n'
            '    - id: start\n'
            '      type: agent\n'
            '      agent: assistant\n'
            '      depends_on: []\n'
            '      instructions: "First step."\n'
            "Does this shape look right?\n"
            "PHASE_UPDATE: detailing"
        )
    if phase == "detailing":
        return "Which model should this step use — Haiku (fast) or Sonnet (thorough)?"
    return "The workflow looks complete. Would you like to run a dry-run to verify the structure?"

@router.websocket("/workflows/{wid}/chat")
async def workflow_chat_ws(
    wid: str,
    websocket: WebSocket,
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
) -> None:
    """Guided design assistant — bidirectional WebSocket."""
    if not corvin_console_sid:
        await websocket.close(code=4401)
        return
    rec = session_auth.load_session(corvin_console_sid)
    if rec is None:
        await websocket.close(code=4401)
        return

    _validate_wid(wid)
    if not _meta_path(rec.tenant_id, wid).exists():
        await websocket.close(code=4404)
        return

    await websocket.accept()

    meta = _read_json(_meta_path(rec.tenant_id, wid)) or {}
    chat = _read_chat(rec.tenant_id, wid)
    yaml_p = _yaml_path(rec.tenant_id, wid)
    yaml_text = yaml_p.read_text(encoding="utf-8") if yaml_p.exists() else ""

    await websocket.send_json({
        "type": "init",
        "phase": meta.get("phase", "discovering"),
        "yaml": yaml_text,
        "graph": _parse_graph(yaml_text),
        "chat": chat[-50:],
    })

    if not chat:
        opening = (
            "What should this workflow do? Describe the goal in one or two sentences — "
            "I will ask follow-up questions to fill in the details."
        )
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": opening, "ts": time.time()}
        _append_chat_line(rec.tenant_id, wid, assistant_msg)
        await websocket.send_json({"type": "message", **assistant_msg})

    # Phase 7: Voice mode flag — client sends {"type": "set_voice", "enabled": true, "lang": "en"}
    voice_enabled = False
    voice_lang = "en"

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "user")

            if msg_type == "set_voice":
                voice_enabled = bool(data.get("enabled", False))
                voice_lang = str(data.get("lang", "en"))
                await websocket.send_json({"type": "voice_ack", "enabled": voice_enabled, "lang": voice_lang})
                continue

            if msg_type == "accept_template":
                key = str(data.get("key", ""))
                if key in _TEMPLATES:
                    tmpl_yaml = _TEMPLATES[key]["yaml"]
                    _write_atomic(_yaml_path(rec.tenant_id, wid), tmpl_yaml)
                    meta["phase"] = "detailing"
                    meta["updated_at"] = time.time()
                    _write_atomic(_meta_path(rec.tenant_id, wid), meta)
                    graph = _parse_graph(tmpl_yaml)
                    reply_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": "Template loaded. Let me walk through each step to fill in the details.",
                        "ts": time.time(),
                        "yaml_update": tmpl_yaml,
                        "phase_update": "detailing",
                        "graph": graph,
                    }
                    _append_chat_line(rec.tenant_id, wid, reply_msg)
                    await websocket.send_json({"type": "message", **reply_msg})
                    if voice_enabled:
                        loop = asyncio.get_running_loop()
                        audio_b64 = await loop.run_in_executor(None, _tts_b64, reply_msg["content"], voice_lang)
                        if audio_b64:
                            await websocket.send_json({"type": "audio", "data": audio_b64, "mime_type": "audio/ogg"})
                continue

            if msg_type != "user":
                continue

            user_text = str(data.get("text", "")).strip()[:_MAX_CHAT_MSG_CHARS]
            if not user_text:
                continue

            # ADR-0150 LIC-WFDESIGN-SPAWN-02: each design message spawns 1-2 paid
            # `claude -p`. Charge the SEPARATE chat_turns_per_day axis (interactive,
            # not the compute-workload counter) before spawning; on 402 send an
            # in-band error and keep the socket open.
            try:
                from ._compute_license_gate import enforce_chat_turns  # noqa: PLC0415
                enforce_chat_turns(
                    rec.tenant_id, rec.sid_fingerprint,
                    audit_action="workflow.design_turn", channel="workflows",
                )
            except HTTPException:
                await websocket.send_json({
                    "type": "error", "code": 402,
                    "message": "daily chat-turn limit reached (chat_turns_per_day)",
                })
                continue

            user_msg_obj: dict[str, Any] = {"role": "user", "content": user_text, "ts": time.time()}
            _append_chat_line(rec.tenant_id, wid, user_msg_obj)
            await websocket.send_json({"type": "message", **user_msg_obj})
            await websocket.send_json({"type": "typing"})

            result = await _design_turn(rec.tenant_id, wid, user_text)

            assistant_payload: dict[str, Any] = {
                "role": "assistant",
                "content": result["reply"],
                "ts": time.time(),
            }
            if result.get("yaml_update"):
                assistant_payload["yaml_update"] = result["yaml_update"]
                assistant_payload["graph"] = _parse_graph(result["yaml_update"])
            if result.get("phase_update"):
                assistant_payload["phase_update"] = result["phase_update"]
            if result.get("summary_card"):
                assistant_payload["summary_card"] = result["summary_card"]
            if result.get("template_offer"):
                assistant_payload["template_offer"] = result["template_offer"]

            _append_chat_line(rec.tenant_id, wid, assistant_payload)
            await websocket.send_json({"type": "message", **assistant_payload})

            # Phase 7: TTS for assistant reply
            if voice_enabled and result["reply"]:
                loop = asyncio.get_running_loop()
                audio_b64 = await loop.run_in_executor(None, _tts_b64, result["reply"], voice_lang)
                if audio_b64:
                    await websocket.send_json({"type": "audio", "data": audio_b64, "mime_type": "audio/ogg"})

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
