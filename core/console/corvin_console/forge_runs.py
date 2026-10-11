"""One generation-run mechanism for every Forge artifact kind (ADR-2217 D1).

Skill, tool and plugin generation are minutes-long engine runs: a worker thread
drives the orchestrator, the console polls a tenant-bound status record. This
module owns that record store, the spawn, the console pre-spawn gates and the
success/failure audit, so the three kinds cannot drift apart again — the skill
route had already lost the pre-spawn gates before this module existed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Dict, Optional, Tuple
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

#: Identifies THIS process. A record still "running" under another boot id was
#: orphaned by a restart: its worker thread (and engine subprocess) died with
#: the old process, so nothing will ever update it again.
BOOT_ID = uuid4().hex
#: An interrupted run is re-spawned from its persisted request at most this
#: many times, so a request that crashes the console cannot restart-loop.
MAX_RESUMES = 2
#: The owning writer refreshes ``heartbeat_at`` this often while its worker runs (ADR-2253 D6). The console and the
#: gateway are two processes sharing these files: a record under a FOREIGN boot id is a dead owner's only when its
#: heartbeat is older than ``ORPHAN_AFTER_S``; a fresher one belongs to a live peer and must be left alone.
#: Initial values - the measurement task (T-0146) may amend them.
HEARTBEAT_S = 10.0
ORPHAN_AFTER_S = 90.0
#: Finished run files older than this are pruned when a new run starts.
_FINISHED_TTL_S = 14 * 86400
_RUN_ID_RE = re.compile(r"^run-[0-9a-f]{12}$")

#: kind -> builder(run_id, record) returning the spawn kwargs (``work``,
#: ``success_action``, ``failure_action``, ``hint``, ``failure_target_id``).
#: Routes register one per kind at import so an interrupted run can be rebuilt
#: from its persisted ``resume`` payload without the original request context.
ResumeBuilder = Callable[[str, Dict[str, Any]], Dict[str, Any]]
_resumers: Dict[str, ResumeBuilder] = {}


def register_resumer(kind: str, builder: ResumeBuilder) -> None:
    _resumers[kind] = builder


def _runs_dir(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_home  # noqa: PLC0415
    return tenant_home(tenant_id) / "global" / "forge_runs"


def _persist(run_id: str, record: Dict[str, Any]) -> None:
    """Write the record atomically (0600). A persistence failure never fails the run."""
    try:
        d = _runs_dir(str(record.get("tenant_id") or ""))
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".run-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(record, fh, default=str)
            os.chmod(tmp, 0o600)
            os.replace(tmp, d / f"{run_id}.json")
        except BaseException:
            _unlink_quiet(tmp)
            raise
    except Exception:  # noqa: BLE001
        logger.warning("forge run %s: could not persist record", run_id, exc_info=True)


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _load(run_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    if not _RUN_ID_RE.match(run_id):
        return None
    try:
        path = _runs_dir(tenant_id) / f"{run_id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("tenant_id") != tenant_id:
        return None
    return record


def _prune(tenant_id: str) -> None:
    try:
        cutoff = time.time() - _FINISHED_TTL_S
        for f in _runs_dir(tenant_id).glob("run-*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.debug("forge run prune failed", exc_info=True)


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


def _fresh(record: Dict[str, Any]) -> bool:
    """True when a record's owner is still alive: this process, or a peer with a recent heartbeat."""
    if record.get("boot_id") == BOOT_ID:
        return True
    try:
        return time.time() - float(record.get("heartbeat_at") or 0.0) < ORPHAN_AFTER_S
    except (TypeError, ValueError):
        return False


def _running_count_locked(tenant_id: str) -> int:
    """Live running runs of a tenant across every host sharing its run files (caller holds ``runs_lock``).

    In-memory records of this process are authoritative; the rest come from disk and count only while their
    owner's heartbeat is fresh, so a dead process's leftovers never fill the cap forever."""
    owned: set[str] = set()
    count = 0
    for rid, r in runs.items():
        if r.get("tenant_id") != tenant_id:
            continue
        owned.add(rid)
        if r.get("status") == "running" and _fresh(r):
            count += 1
    try:
        files = list(_runs_dir(tenant_id).glob("run-*.json"))
    except OSError:
        files = []
    for f in files:
        if f.stem in owned:
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(rec, dict) and rec.get("tenant_id") == tenant_id
                and rec.get("status") == "running" and _fresh(rec)):
            count += 1
    return count


def running_count(tenant_id: str) -> int:
    with runs_lock:
        return _running_count_locked(tenant_id)


def _owner() -> Dict[str, Any]:
    import socket  # noqa: PLC0415
    return {"boot_id": BOOT_ID, "pid": os.getpid(), "host": socket.gethostname()}


def new_run(*, tenant_id: str, kind: str, phases: tuple[str, ...],
            sid_fingerprint: Optional[str], resume: Optional[Dict[str, Any]] = None,
            **extra: Any) -> str:
    """``resume`` is the JSON payload a registered resumer needs to rebuild this run's work."""
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
        "boot_id": BOOT_ID,
        "owner": _owner(),
        "heartbeat_at": time.time(),
        "resume": resume,
        "resumes": 0,
    }
    record.update(extra)
    with runs_lock:
        running = _running_count_locked(tenant_id)
        if running >= MAX_RUNNING_PER_TENANT:
            raise GenerationBusy(
                f"{running} generation runs are already in progress — wait for one to finish.")
        _evict_locked()
        if len(runs) >= MAX_RUNS:
            raise GenerationBusy("The console's run store is full — try again shortly.")
        runs[run_id] = record
        snapshot = dict(record)
    _prune(tenant_id)
    _persist(run_id, snapshot)
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
        if run is None:
            return
        run.update(fields)
        if run.get("status") == "running" and run.get("boot_id") == BOOT_ID:
            run["heartbeat_at"] = time.time()
        snapshot = dict(run)
    _persist(run_id, snapshot)


def _fail_locked(run: Dict[str, Any], code: str, error: str, message: str) -> None:
    run.update(status="failed", boot_id=BOOT_ID, interrupted=True, error=error, message=message, failure_code=code)


def get_run(run_id: str, tenant_id: str, kind: Optional[str] = None, *,
            kinds: Optional[Collection[str]] = None) -> Optional[Dict[str, Any]]:
    """A copy of the run, or None when it is unknown, another tenant's, or not one of the allowed kinds.

    ``kind`` / ``kinds`` name what the CALLER may see. They are checked BEFORE anything else happens: a caller
    who may not see a run must not be able to resume it (the status route of the tool/plugin forge used to
    re-spawn a skill run it then answered 404 for).

    A run unknown to this process is looked up on disk (the console restarted, or another host owns it):
      * owner alive (a peer's fresh heartbeat) -> returned read-only, never cached, never resumed or failed;
      * owner dead -> re-spawned from its persisted request (at most ``MAX_RESUMES`` times, and only under the
        tenant's concurrency cap) or reported ``failed`` with an explicit code - never a bare 404. The resume is
        atomic: a resumer that raises leaves the record ``failed: resume_failed``, never ``running``.
    """
    allowed = set(kinds) if kinds else ({kind} if kind else None)
    resume_args: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None
    changed = False
    with runs_lock:
        run = runs.get(run_id)
        if run is None:
            loaded = _load(run_id, tenant_id)
            if loaded is None:
                return None
            if allowed is not None and loaded.get("kind") not in allowed:
                return None  # not this caller's to see: no side effect at all
            if loaded.get("status") == "running" and loaded.get("boot_id") != BOOT_ID:
                if _fresh(loaded):
                    return dict(loaded)  # a live peer owns it; we only read
                builder = _resumers.get(str(loaded.get("kind")))
                if builder and loaded.get("resume") and int(loaded.get("resumes") or 0) < MAX_RESUMES:
                    if _running_count_locked(tenant_id) >= MAX_RUNNING_PER_TENANT:
                        _fail_locked(loaded, "resume_over_cap", "not resumed: the concurrency cap is reached",
                                     "This run was interrupted and cannot be resumed while the tenant already has "
                                     f"{MAX_RUNNING_PER_TENANT} runs in progress. Start it again later.")
                    else:
                        try:
                            kwargs = builder(run_id, dict(loaded))
                        except Exception:  # noqa: BLE001 - atomic: a failing resumer must not leave 'running'
                            logger.exception("forge %s run %s: resumer raised", loaded.get("kind"), run_id)
                            _fail_locked(loaded, "resume_failed", "the run could not be resumed",
                                         "The run was interrupted and its saved request could not be rebuilt. "
                                         "Start it again.")
                        else:
                            loaded.update(boot_id=BOOT_ID, owner=_owner(), heartbeat_at=time.time(),
                                          resumes=int(loaded.get("resumes") or 0) + 1,
                                          message="The console restarted — resuming this run…")
                            resume_args = (dict(loaded), kwargs)
                else:
                    _fail_locked(loaded, "interrupted", "interrupted by a console restart",
                                 "The console restarted while this run was in progress and it could not be "
                                 "resumed. Start it again.")
                changed = True
            runs[run_id] = loaded
            run = loaded
        copy = dict(run or {})
    if copy and (resume_args is not None or changed):
        _persist(run_id, copy)
    if resume_args is not None:
        record, kwargs = resume_args
        logger.warning("forge %s run %s: resuming after console restart (attempt %s)",
                       record.get("kind"), run_id, copy.get("resumes"))
        spawn(run_id=run_id, kind=str(record["kind"]), tenant_id=tenant_id,
              sid_fingerprint=record.get("sid_fingerprint"), **kwargs)
    if not copy or copy.get("tenant_id") != tenant_id:
        return None
    if allowed is not None and copy.get("kind") not in allowed:
        return None
    return copy


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
        # The engine can be silent for minutes between progress calls; the heartbeat tells a peer host this
        # run's owner is alive, so it is not mistaken for an orphan (ADR-2253 D6).
        done = threading.Event()

        def beat() -> None:
            while not done.wait(HEARTBEAT_S):
                update_run(run_id)

        threading.Thread(target=beat, name=f"forge-{kind}-{run_id}-heartbeat", daemon=True).start()
        try:
            _run_body()
        finally:
            done.set()

    def _run_body() -> None:
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
