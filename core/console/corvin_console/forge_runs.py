"""One generation-run mechanism for every Forge artifact kind (ADR-2217 D1).

Skill, tool and plugin generation are minutes-long engine runs: a worker thread
drives the orchestrator, the console polls a tenant-bound status record. This
module owns that record store, the spawn, the console pre-spawn gates and the
success/failure audit, so the three kinds cannot drift apart again — the skill
route had already lost the pre-spawn gates before this module existed.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from . import audit as console_audit

logger = logging.getLogger(__name__)

#: run_id -> record. Mutated from worker threads, guarded by ``runs_lock``.
runs: Dict[str, Dict[str, Any]] = {}
runs_lock = threading.Lock()

#: A finished run stays pollable; the oldest finished ones go first once the
#: store holds this many records, so a long-lived console cannot grow it forever.
MAX_RUNS = 500
#: Engine runs are minutes long and spawn several `claude -p` calls each; a
#: tenant gets this many in flight at once, the next POST is answered 429.
MAX_RUNNING_PER_TENANT = 2

ProgressCb = Callable[[str, int, str], None]


class GenerationRefused(Exception):
    """A console pre-spawn gate refused the engine spawn; ``str()`` is user-facing."""


class GenerationBusy(Exception):
    """The tenant already has ``MAX_RUNNING_PER_TENANT`` runs in flight (or the store is full)."""


def check_spawn_gates(prompt: str, *, tenant_id: str, sid_fingerprint: str,
                      kind: str) -> None:
    """Run the four console pre-spawn gates (L44, ADR-0141, L34, L35).

    Raises :class:`GenerationRefused` with the gate's own text when any gate
    blocks. Fail-closed: an error inside the gates is a refusal, never an allow.
    """
    try:
        from ._spawn_gates import check_console_spawn_or_refusal  # noqa: PLC0415
        refusal = check_console_spawn_or_refusal(
            prompt, tenant_id=tenant_id, persona="assistant",
            channel="web", chat_key=f"forge-{kind}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("forge %s: pre-spawn gates raised", kind)
        refusal = f"Generation refused: the safety gates could not run ({type(exc).__name__})."
    if refusal:
        console_audit.action_failed(
            tenant_id=tenant_id, sid_fingerprint=sid_fingerprint,
            action="forge.generation_refused", target_kind=f"generated_{kind}",
            target_id="new", reason="spawn_gate_refused",
        )
        raise GenerationRefused(refusal)


def new_run(*, tenant_id: str, kind: str, phases: tuple[str, ...],
            sid_fingerprint: Optional[str], **extra: Any) -> str:
    run_id = f"run-{uuid4().hex[:12]}"
    record = {
        "tenant_id": tenant_id,
        "kind": kind,
        "status": "running",
        "phase": phases[0],
        "phases": list(phases),
        "progress": 5,
        "message": "Initializing…",
        "engine": "unknown",
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sid_fingerprint": sid_fingerprint,
    }
    record.update(extra)
    with runs_lock:
        running = sum(1 for r in runs.values()
                      if r.get("tenant_id") == tenant_id and r.get("status") == "running")
        if running >= MAX_RUNNING_PER_TENANT:
            raise GenerationBusy(
                f"{running} generation runs are already in progress — wait for one to finish.")
        _evict_locked()
        if len(runs) >= MAX_RUNS:
            raise GenerationBusy("The console's run store is full — try again shortly.")
        runs[run_id] = record
    return run_id


def _evict_locked() -> None:
    if len(runs) < MAX_RUNS:
        return
    finished = [rid for rid, r in runs.items() if r.get("status") in ("success", "failed")]
    for rid in finished[: len(runs) - MAX_RUNS + 1]:
        runs.pop(rid, None)


def update_run(run_id: str, **fields: Any) -> None:
    with runs_lock:
        run = runs.get(run_id)
        if run is not None:
            run.update(fields)


def get_run(run_id: str, tenant_id: str, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A copy of the run, or None when it is unknown, another tenant's, or another kind's."""
    with runs_lock:
        run = dict(runs.get(run_id) or {})
    if not run or run.get("tenant_id") != tenant_id:
        return None
    if kind is not None and run.get("kind") != kind:
        return None
    return run


def spawn(*, run_id: str, kind: str, tenant_id: str, sid_fingerprint: Optional[str],
          work: Callable[[ProgressCb], Dict[str, Any]],
          success_action: str, failure_action: str,
          hint: Callable[[Exception], str] = str,
          failure_target_id: str = "new") -> None:
    """Run ``work(progress_cb)`` on a daemon thread and fold its result into the record.

    ``work`` returns the fields to merge on success; it must include
    ``target_id`` (the artifact name, for the audit record) and ``message``.
    An exception may carry ``result_fields`` (a dict) to merge into the failed
    record — e.g. the test results that made a tool run fail.
    ``tenant_id`` is captured from the authenticated request: a worker thread
    has no session, and reading an env var there is the console tenant-routing
    violation CLAUDE.md forbids.
    """
    def progress(phase: str, pct: int, message: str) -> None:
        update_run(run_id, phase=phase, progress=pct, message=message)

    def run_task() -> None:
        try:
            result = work(progress)
            target_id = str(result.pop("target_id", "") or "unknown")
            update_run(run_id, status="success", progress=100, error=None, **result)
            if sid_fingerprint:
                console_audit.action_performed(
                    tenant_id=tenant_id, sid_fingerprint=sid_fingerprint,
                    action=success_action, target_kind=f"generated_{kind}",
                    target_id=target_id, run_id=run_id,
                )
        except Exception as exc:  # noqa: BLE001 — surface, never crash the thread
            logger.exception("forge %s run %s failed", kind, run_id)
            if sid_fingerprint:
                console_audit.action_failed(
                    tenant_id=tenant_id, sid_fingerprint=sid_fingerprint,
                    action=failure_action, target_kind=f"generated_{kind}",
                    target_id=failure_target_id, reason="async_task_failed",
                )
            extra = getattr(exc, "result_fields", None)
            update_run(run_id, status="failed", error=str(exc), message=hint(exc),
                       **(extra if isinstance(extra, dict) else {}))

    threading.Thread(target=run_task, name=f"forge-{kind}-{run_id}", daemon=True).start()
