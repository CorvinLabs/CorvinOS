"""flows.py — CorvinFlow console REST routes (ADR-0121 M4).

Exposes FlowRun manifests from the tenant's global/flows/runs/ directory.

Routes:
  GET    /flows/runs                     list FlowRuns (most recent first, limit=50)
  GET    /flows/runs/{run_id}            FlowRun event timeline
  POST   /flows/runs/{run_id}/approve    approve a pending human_approval checkpoint
  GET    /flows/definitions              list all saved flow definitions
  GET    /flows/definition/{flow_id}     flow step graph (id, depends_on, node, checkpoint)
  PUT    /flows/definition/{flow_id}     create or update a flow definition
  DELETE /flows/definition/{flow_id}     delete a flow definition directory
  POST   /flows/trigger/{flow_id}        start a flow run (async, background thread)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Annotated, Any

log = logging.getLogger(__name__)

_FLOW_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_STEP_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_NODE_RE    = re.compile(r"^[A-Za-z0-9_\-\.]{1,128}$")

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field, field_validator

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_session, require_csrf


# ── Request models ─────────────────────────────────────────────────────────────

class _StepInput(BaseModel):
    step_id: str
    node: str = "local"
    prompt: str
    depends_on: list[str] = Field(default_factory=list)
    checkpoint: str | None = None

    @field_validator("step_id")
    @classmethod
    def _valid_step_id(cls, v: str) -> str:
        if not _STEP_ID_RE.fullmatch(v):
            raise ValueError(f"Invalid step_id {v!r}")
        return v

    @field_validator("node")
    @classmethod
    def _valid_node(cls, v: str) -> str:
        if not _NODE_RE.fullmatch(v):
            raise ValueError(f"Invalid node {v!r}")
        return v


class _FlowDefinitionBody(BaseModel):
    version: str = "1.0.0"
    budget_tokens: int = Field(default=50_000, ge=1_000, le=10_000_000)
    budget_steps: int = Field(default=10, ge=1, le=100)
    budget_wall_time_s: int = Field(default=600, ge=10, le=86_400)
    steps: list[_StepInput] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def _unique_step_ids(cls, v: list[_StepInput]) -> list[_StepInput]:
        ids = [s.step_id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("step_id values must be unique")
        return v

router = APIRouter()

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


def _runs_dir(tenant_id: str) -> Path:
    from paths import corvin_home
    return corvin_home() / "tenants" / tenant_id / "global" / "flows" / "runs"


def _checkpoint_dir(tenant_id: str) -> Path:
    from paths import corvin_home
    return corvin_home() / "tenants" / tenant_id / "global" / "flows" / "checkpoints"


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _run_summary(path: Path) -> dict[str, Any]:
    events = _read_manifest(path)
    types = [e["type"] for e in events]
    started = next((e for e in events if e["type"] == "mesh_flow.run_started"), None)
    completed = next((e for e in events if e["type"] == "mesh_flow.run_completed"), None)
    paused = next((e for e in events if e["type"] == "mesh_flow.checkpoint_paused"), None)
    exceeded = next((e for e in events if e["type"] == "mesh_flow.budget_exceeded"), None)

    status = "running"
    if "mesh_flow.run_completed" in types:
        status = "completed"
    elif "mesh_flow.budget_exceeded" in types:
        status = "budget_exceeded"
    elif "mesh_flow.run_paused" in types and "mesh_flow.checkpoint_resumed" not in types:
        # mesh_flow.run_paused is the terminal event written by FlowRunner when a
        # human_approval checkpoint fires; presence without a subsequent
        # checkpoint_resumed means the run is waiting for operator approval.
        status = "paused"

    run_id = path.stem.replace(".manifest", "")
    return {
        "run_id": run_id,
        "flow_id": started.get("flow_id") if started else None,
        "flow_version": started.get("flow_version") if started else None,
        "status": status,
        "started_at": started.get("ts") if started else None,
        "completed_at": completed.get("ts") if completed else None,
        "steps_done": completed.get("steps_done") if completed else None,
        "paused_at_step": paused.get("step_id") if paused and status == "paused" else None,
        "event_count": len(events),
    }


@router.get("/flows/runs")
def list_flow_runs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = 50,
) -> dict[str, Any]:
    """List recent FlowRuns for the authenticated tenant."""
    tid = rec.tenant_id
    runs_dir = _runs_dir(tid)

    if not runs_dir.exists():
        return {"runs": []}

    manifests = sorted(
        runs_dir.glob("*.manifest.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    runs = []
    for m in manifests:
        try:
            runs.append(_run_summary(m))
        except Exception:
            continue

    return {"runs": runs}


@router.get("/flows/runs/{run_id}")
def get_flow_run(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the full event timeline for a FlowRun."""
    if not run_id.startswith("fr_") or len(run_id) > 64:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid run_id")

    tid = rec.tenant_id
    manifest_path = _runs_dir(tid) / f"{run_id}.manifest.jsonl"

    if not manifest_path.exists():
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="FlowRun not found")

    try:
        events = _read_manifest(manifest_path)
    except Exception as exc:
        log.error("read_manifest failed", exc_info=True)
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error")

    # Strip any output content that leaked through — defence in depth
    safe_events = []
    for ev in events:
        safe = {k: v for k, v in ev.items() if k not in ("output", "task", "instruction")}
        safe_events.append(safe)

    return {"run_id": run_id, "events": safe_events}


@router.post("/flows/runs/{run_id}/approve")
def approve_checkpoint(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, str]:
    """Approve a pending human_approval checkpoint for a FlowRun."""
    if not run_id.startswith("fr_") or len(run_id) > 64:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid run_id")

    tid = rec.tenant_id
    cp_dir = _checkpoint_dir(tid)

    step_id: str | None = None
    try:
        from flow_checkpoint import FlowCheckpointStore
        store = FlowCheckpointStore(cp_dir)
        if not store.is_paused(run_id):
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="No pending checkpoint for this run_id",
            )
        # Read step_id from pause sentinel before approving (EU AI Act Art. 14 traceability)
        try:
            pause_path = cp_dir / f"{run_id}.checkpoint"
            step_id = json.loads(pause_path.read_text()).get("step_id")
        except Exception:
            step_id = None
        store.approve(run_id)
    except HTTPException:
        raise
    except Exception as exc:
        log.error("approve_checkpoint failed", exc_info=True)
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error")

    console_audit.action_performed(
        tenant_id=tid,
        action="flows.approve_checkpoint",
        target_kind="flow_run",
        target_id=run_id,
        sid_fingerprint=rec.sid_fingerprint,
        run_id=run_id,
        step_id=step_id,
    )
    return {"status": "approved", "run_id": run_id, "step_id": step_id}


@router.get("/flows/definitions")
def list_flow_definitions(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all saved flow definitions for the authenticated tenant.

    Scans <tenant>/global/flows/ for directories containing flow.yaml.
    Returns id, version, step_count, and mtime so the UI can display
    definitions that have never been run (they don't appear in /flows/runs).
    """
    tid = rec.tenant_id
    from paths import corvin_home
    flows_root = corvin_home() / "tenants" / tid / "global" / "flows"
    definitions: list[dict[str, Any]] = []

    if flows_root.exists():
        for child in sorted(flows_root.iterdir()):
            # Skip the runs/ and checkpoints/ subdirs — they are not definitions.
            if not child.is_dir() or child.name in ("runs", "checkpoints"):
                continue
            flow_yaml = child / "flow.yaml"
            if not flow_yaml.exists():
                continue
            try:
                import yaml  # noqa: PLC0415
                doc = yaml.safe_load(flow_yaml.read_text(encoding="utf-8"))
                flow_sec = doc.get("flow", {}) if isinstance(doc, dict) else {}
                step_count = len(flow_sec.get("steps", {}))
                version = flow_sec.get("version", "1.0.0")
            except Exception:
                step_count = 0
                version = "?"
            definitions.append({
                "flow_id": child.name,
                "version": version,
                "step_count": step_count,
                "mtime": flow_yaml.stat().st_mtime,
            })

    return {"definitions": definitions}


@router.put("/flows/definition/{flow_id}")
def upsert_flow_definition(
    flow_id: str,
    body: _FlowDefinitionBody,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Create or update a flow definition (ADR-0122 M1).

    Writes <tenant>/global/flows/<flow_id>/flow.yaml atomically.
    Validates via FlowDefinition before writing — rejects structurally invalid
    definitions with HTTP 422. Audit-first: action_failed is emitted on any
    early-return path; action_performed only on a clean write.
    """
    if not _FLOW_ID_RE.fullmatch(flow_id):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid flow_id")

    if not body.steps:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="flows.upsert_definition",
            target_kind="flow",
            target_id=flow_id,
            reason="validation-error",
        )
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one step is required")

    tid = rec.tenant_id
    from paths import corvin_home  # noqa: PLC0415
    flow_dir  = corvin_home() / "tenants" / tid / "global" / "flows" / flow_id
    flow_path = flow_dir / "flow.yaml"

    # Build the canonical YAML dict
    steps_dict: dict[str, Any] = {}
    for step in body.steps:
        entry: dict[str, Any] = {"node": step.node, "task": step.prompt}
        if step.depends_on:
            entry["depends_on"] = step.depends_on
        if step.checkpoint:
            entry["checkpoint"] = step.checkpoint
        steps_dict[step.step_id] = entry

    flow_doc: dict[str, Any] = {
        "flow": {
            "id": flow_id,
            "version": body.version,
            "budget": {
                "max_tokens":     body.budget_tokens,
                "max_steps":      body.budget_steps,
                "max_wall_time_s": body.budget_wall_time_s,
                "require_audit":  True,
            },
            "steps": steps_dict,
        }
    }

    # Validate using FlowDefinition before touching the filesystem
    try:
        from flow_definition import FlowDefinition  # noqa: PLC0415
        FlowDefinition(flow_doc)
    except Exception as exc:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="flows.upsert_definition",
            target_kind="flow",
            target_id=flow_id,
            reason="validation-error",
        )
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid flow definition")

    # Atomic write via temp file + os.replace
    try:
        import tempfile  # noqa: PLC0415
        import yaml  # type: ignore[import-untyped]  # noqa: PLC0415
        flow_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=flow_dir, prefix=".flow.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.dump(flow_doc, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
            os.replace(tmp, flow_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="flows.upsert_definition",
            target_kind="flow",
            target_id=flow_id,
            reason="io-error",
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write flow definition",
        )

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        action="flows.upsert_definition",
        target_kind="flow",
        target_id=flow_id,
        sid_fingerprint=rec.sid_fingerprint,
    )
    return {"ok": True, "flow_id": flow_id, "version": body.version}


@router.get("/flows/definition/{flow_id}")
def get_flow_definition(
    flow_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    full: int = 0,
) -> dict[str, Any]:
    """Return the step graph for a flow definition.

    By default returns only graph topology (node, depends_on, checkpoint) so
    the FlowGraph UI can render the DAG without loading task templates.

    With ?full=1 the response also includes task prompts and budget settings so
    the FlowCreatorPanel can pre-populate the edit form.
    """
    if not _FLOW_ID_RE.fullmatch(flow_id):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid flow_id")

    tid = rec.tenant_id
    from paths import corvin_home
    flow_yaml_path = corvin_home() / "tenants" / tid / "global" / "flows" / flow_id / "flow.yaml"

    if not flow_yaml_path.exists():
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Flow definition not found")

    try:
        from flow_definition import FlowDefinition
        fd = FlowDefinition.from_file(flow_yaml_path)
    except Exception as exc:
        log.error("load flow definition failed", exc_info=True)
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error")

    if full:
        steps = [
            {
                "id": step_id,
                "node": step.get("node", "local"),
                "depends_on": step.get("depends_on", []),
                "checkpoint": step.get("checkpoint"),
                "task": step.get("task", ""),
            }
            for step_id, step in fd.steps.items()
        ]
    else:
        steps = [
            {
                "id": step_id,
                "node": step.get("node", "local"),
                "depends_on": step.get("depends_on", []),
                "checkpoint": step.get("checkpoint"),
            }
            for step_id, step in fd.steps.items()
        ]

    console_audit.action_performed(
        tenant_id=tid,
        action="flows.get_definition",
        target_kind="flow",
        target_id=flow_id,
        sid_fingerprint=rec.sid_fingerprint,
    )
    result: dict[str, Any] = {
        "flow_id": fd.id,
        "flow_version": fd.version,
        "steps": steps,
    }
    if full:
        budget = fd.budget if isinstance(fd.budget, dict) else {}
        result["budget_tokens"] = budget.get("max_tokens", 50_000)
        result["budget_steps"] = budget.get("max_steps", 10)
        result["budget_wall_time_s"] = budget.get("max_wall_time_s", 600)
    return result


@router.get("/flows/runs/{run_id}/outputs")
def get_flow_run_outputs(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the step output map for a completed (or partial) FlowRun.

    Outputs are written incrementally by FlowRunner after each step completes.
    Returns an empty dict when the run hasn't produced any output yet.
    """
    if not run_id.startswith("fr_") or len(run_id) > 64:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid run_id")

    tid = rec.tenant_id
    out_path = _runs_dir(tid) / f"{run_id}.outputs.json"

    if not out_path.exists():
        return {"run_id": run_id, "outputs": {}}

    try:
        outputs = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("read run outputs failed", exc_info=True)
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error")

    return {"run_id": run_id, "outputs": outputs}


@router.delete("/flows/definition/{flow_id}")
def delete_flow_definition(
    flow_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, str]:
    """Delete a flow definition directory (irreversible)."""
    if not _FLOW_ID_RE.fullmatch(flow_id):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid flow_id")

    tid = rec.tenant_id
    from paths import corvin_home
    flow_dir = corvin_home() / "tenants" / tid / "global" / "flows" / flow_id

    if not flow_dir.exists():
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Flow not found")

    # Refuse to delete if an active run is in progress for this flow
    runs_dir = _runs_dir(tid)
    if runs_dir.exists():
        for m in runs_dir.glob("*.manifest.jsonl"):
            try:
                summary = _run_summary(m)
                if summary.get("flow_id") == flow_id and summary.get("status") == "running":
                    raise HTTPException(
                        status_code=http_status.HTTP_409_CONFLICT,
                        detail="Cannot delete: a run for this flow is currently active",
                    )
            except HTTPException:
                raise
            except Exception:
                continue

    console_audit.action_performed(
        tenant_id=tid,
        action="flows.delete_definition",
        target_kind="flow",
        target_id=flow_id,
        sid_fingerprint=rec.sid_fingerprint,
    )
    shutil.rmtree(flow_dir)
    return {"ok": "true", "flow_id": flow_id}


@router.post("/flows/trigger/{flow_id}")
def trigger_flow_run(
    flow_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a FlowRun for the given flow definition in a background thread.

    Returns the run_id immediately — the frontend polls /flows/runs to track
    progress. The runner writes manifest events as it executes, so the timeline
    view fills in live.
    """
    if not _FLOW_ID_RE.fullmatch(flow_id):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid flow_id")

    tid = rec.tenant_id
    from paths import corvin_home
    flow_yaml = corvin_home() / "tenants" / tid / "global" / "flows" / flow_id / "flow.yaml"

    if not flow_yaml.exists():
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Flow definition not found")

    try:
        from flow_definition import FlowDefinition
        flow_def = FlowDefinition.from_file(flow_yaml)
    except Exception as exc:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid flow definition")

    # ADR-0147 FLOW-COMPUTE-01: a flow run spawns compute, but the runner's
    # per-run FlowBudget compares against a STATELESS limit (compute_used resets
    # to 0 each run) — so "compute_units_per_day" was effectively "per run" and a
    # free-tier user could trigger unlimited runs/day. Charge the run against the
    # SAME persistent per-UTC-day counter as the other compute entrypoints, at the
    # HTTP boundary (before the background thread), via the shared fail-closed
    # helper — so a 402 reaches the caller instead of dying in the daemon thread.
    from ._compute_license_gate import enforce_compute_quota  # noqa: PLC0415

    enforce_compute_quota(tid, rec.sid_fingerprint, audit_action="flows.trigger", channel="flows")

    manifest_root = _runs_dir(tid)
    manifest_root.mkdir(parents=True, exist_ok=True)

    flow_input: dict[str, Any] = body or {}

    try:
        from flow_runner import FlowRunner
        runner = FlowRunner(flow_def, manifest_root, flow_input)
    except Exception as exc:
        log.error("FlowRunner init failed", exc_info=True)
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error")

    run_id: str = runner._run_id  # set in FlowRunner.__init__ before any I/O

    console_audit.action_performed(
        tenant_id=tid,
        action="flows.trigger",
        target_kind="flow_run",
        target_id=run_id,
        sid_fingerprint=rec.sid_fingerprint,
    )

    def _run_in_bg() -> None:
        try:
            runner.run()
        except Exception as exc:
            log.error("Background FlowRun %s (%s) error: %s", run_id, flow_id, exc)

    threading.Thread(target=_run_in_bg, name=f"flow-{run_id}", daemon=True).start()

    return {"run_id": run_id, "flow_id": flow_id, "status": "started"}
