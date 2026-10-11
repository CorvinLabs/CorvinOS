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


def run_dir(tenant_id: str, run_id: str) -> Path:
    """The per-run directory for events and snapshots (ADR-2253 D8). Only a well-formed run id maps to a path."""
    if not _RUN_ID_RE.match(run_id):
        raise ValueError("malformed run id")
    return _runs_dir(tenant_id) / run_id


def prune(tenant_id: str) -> None:
    """Age out one tenant's run files (ADR-2253 D8).

    * a FINISHED record older than the TTL is removed together with its run directory;
    * a record still ``running`` whose owner's heartbeat is fresh is NEVER touched, however old its file is;
    * a ``running`` record whose owner is dead and that is older than the TTL is first marked ``failed``
      (``interrupted``) - which makes it a fresh finished record - and is removed one TTL later.
    Only files named like a run are looked at, and a symlinked run directory is never followed or removed.
    """
    import shutil  # noqa: PLC0415
    try:
        d = _runs_dir(tenant_id)
        cutoff = time.time() - _FINISHED_TTL_S
        for f in d.glob("run-*.json"):
            run_id = f.stem
            if not _RUN_ID_RE.match(run_id) or f.is_symlink():
                continue
            try:
                if f.stat().st_mtime >= cutoff:
                    continue
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("status") == "running":
                if _fresh(rec):
                    continue
                rec.update(status="failed", interrupted=True, failure_code="interrupted",
                           error="interrupted: its owner stopped and it never finished",
                           message="This run's owner stopped and it never finished. Start it again.")
                with runs_lock:
                    mem = runs.get(run_id)
                    if mem is not None:
                        mem.update(rec)
                _persist(run_id, rec)
                continue
            with runs_lock:
                runs.pop(run_id, None)
            sub = d / run_id
            if sub.is_dir() and not sub.is_symlink():
                shutil.rmtree(sub, ignore_errors=True)
            f.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.debug("forge run prune failed", exc_info=True)


_prune = prune  # the name new_run has always called


def prune_all() -> None:
    """Prune every tenant that has run files - what a console boot does, so a tenant that never starts another
    run (and never lists its runs) is not left with files forever."""
    try:
        from core.paths.tenant import corvin_home  # noqa: PLC0415
        for t in sorted((corvin_home() / "tenants").iterdir()):
            if t.is_dir() and not t.is_symlink():
                prune(t.name)
    except Exception:  # noqa: BLE001
        logger.debug("forge run boot prune failed", exc_info=True)


_boot_prune_done = threading.Event()


def boot_prune_done() -> bool:
    return _boot_prune_done.is_set()


def _boot_prune() -> None:
    try:
        prune_all()
    finally:
        _boot_prune_done.set()


# Every host (console standalone, gateway, app) imports this module; one background pass at import is the
# single boot hook, instead of one call per host's lifespan.
threading.Thread(target=_boot_prune, name="forge-runs-boot-prune", daemon=True).start()


# ── Event log (ADR-2253 D2) ───────────────────────────────────────────────────────────────────────
#: Initial caps - the measurement task (T-0146) may amend them. Excess is COUNTED, never silently dropped: a final
#: ``truncated`` event states how many were left out.
MAX_EVENTS_PER_RUN = 2000
MAX_EVENT_BYTES_PER_RUN = 512 * 1024
EVENT_TYPES = frozenset({"progress", "iteration", "file_written", "test_result", "finding", "gate", "intake",
                         "error", "truncated"})
_EVENT_LEVELS = frozenset({"info", "warning", "error"})
_event_lock = threading.Lock()
_event_state: Dict[str, Dict[str, int]] = {}


def _pii_shaped(text: str) -> bool:
    try:
        from forge.security_events import _audit_value_pii  # noqa: PLC0415
        return bool(_audit_value_pii(text))
    except Exception:  # noqa: BLE001 - never let the scrubber's import break a run; fall back to a strict check
        return bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)) or bool(re.search(r"\+?\d[\d ()./-]{8,}\d", text))


def _scrub(text: Any, limit: int = 500) -> str:
    """Free text in an event: bounded, and withheld entirely when it carries a personal-data shape."""
    s = str(text if text is not None else "")
    s = " ".join(s.split())[:limit]
    return "[withheld: looks like personal data]" if _pii_shaped(s) else s


def _safe_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Scalars only, at most 16 keys, strings scrubbed - structured detail without room for payloads."""
    out: Dict[str, Any] = {}
    for k, v in list((data or {}).items())[:16]:
        key = _scrub(k, 40)
        if isinstance(v, (bool, int, float)) or v is None:
            out[key] = v
        elif isinstance(v, str):
            out[key] = _scrub(v, 200)
    return out


def _events_path(tenant_id: str, run_id: str) -> Path:
    return run_dir(tenant_id, run_id) / "events.jsonl"


def _state_for(path: Path, run_id: str) -> Dict[str, int]:
    st = _event_state.get(run_id)
    if st is None:
        seq = size = 0
        try:  # a resumed run continues the sequence it had
            with open(path, "rb") as fh:
                for line in fh:
                    seq += 1
                    size += len(line)
        except OSError:
            pass
        st = _event_state[run_id] = {"seq": seq, "bytes": size, "dropped": 0, "truncated_written": 0}
    return st


def emit_event(run_id: str, type_: str, message: str, *, phase: Optional[str] = None, level: str = "info",
               data: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Append one event to a run's log; returns its ``seq`` or None when nothing was written.

    Only the OWNING process appends (a peer host only reads), the writer is serialised per process, the file is
    opened ``O_APPEND|O_NOFOLLOW`` at 0600, and a failure to write never fails the run. Prompts and engine
    output are not events: callers pass what the Forge DID, and free text is scrubbed here regardless.
    """
    if type_ not in EVENT_TYPES or level not in _EVENT_LEVELS:
        raise ValueError(f"unknown event type or level: {type_!r}/{level!r}")
    with runs_lock:
        run = runs.get(run_id)
        if run is None or run.get("boot_id") != BOOT_ID:
            return None
        tenant_id = str(run.get("tenant_id") or "")
        phase = phase if phase is not None else run.get("phase")
    try:
        path = _events_path(tenant_id, run_id)
        with _event_lock:
            st = _state_for(path, run_id)
            if type_ != "truncated" and (st["seq"] >= MAX_EVENTS_PER_RUN or st["bytes"] >= MAX_EVENT_BYTES_PER_RUN):
                st["dropped"] += 1
                return None
            rec = {"seq": st["seq"] + 1, "ts": datetime.now(timezone.utc).isoformat(), "phase": phase,
                   "level": level, "type": type_, "message": _scrub(message), "data": _safe_data(data)}
            line = (json.dumps(rec, default=str) + "\n").encode("utf-8")
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.parent.is_symlink():
                return None
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
            st["seq"] += 1
            st["bytes"] += len(line)
            return rec["seq"]
    except Exception:  # noqa: BLE001
        logger.warning("forge run %s: could not write an event", run_id, exc_info=True)
        return None


def finalize_events(run_id: str) -> None:
    """Write the final ``truncated`` marker once, if the caps left events out."""
    with _event_lock:
        st = _event_state.get(run_id)
        dropped = st["dropped"] if st else 0
        if not st or not dropped or st["truncated_written"]:
            return
        st["truncated_written"] = 1
    emit_event(run_id, "truncated", f"{dropped} events were left out (per-run cap)",
               data={"dropped": dropped, "max_events": MAX_EVENTS_PER_RUN, "max_bytes": MAX_EVENT_BYTES_PER_RUN})


def read_events(tenant_id: str, run_id: str, after: int = 0, limit: int = 500) -> list[Dict[str, Any]]:
    """Events with ``seq > after``, oldest first, at most ``limit``. A missing log is an empty list."""
    out: list[Dict[str, Any]] = []
    try:
        with open(_events_path(tenant_id, run_id), "rb") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and int(rec.get("seq") or 0) > after:
                    out.append(rec)
                    if len(out) >= limit:
                        break
    except (OSError, ValueError):
        return []
    return out


def _events_from_result(run_id: str, kind: str, result: Dict[str, Any]) -> None:
    """What the Forge produced, as events: tests, files, findings, iterations - from fields every kind already returns."""
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else None
    plugin = result.get("plugin") if isinstance(result.get("plugin"), dict) else None
    skill = result.get("skill") if isinstance(result.get("skill"), dict) else None
    if tool and isinstance(tool.get("tests"), dict):
        t = tool["tests"]
        emit_event(run_id, "test_result", f"sandbox tests: {t.get('passed')}/{t.get('total')} passed",
                   data={"passed": t.get("passed"), "total": t.get("total")})
    if plugin:
        for path in (plugin.get("files") or [])[:200]:
            emit_event(run_id, "file_written", str(path), data={"path": str(path)})
    if skill:
        emit_event(run_id, "iteration", f"{skill.get('iterations')} LDD iterations, quality {skill.get('quality')}",
                   data={"iterations": skill.get("iterations"), "quality": skill.get("quality")})
    for src in (tool, plugin, skill):
        for f in ((src or {}).get("findings") or [])[:100]:
            if isinstance(f, dict):
                emit_event(run_id, "finding", str(f.get("summary") or ""),
                           level="warning" if f.get("verdict") == "confirmed" else "info",
                           data={"dimension": f.get("dimension"), "verdict": f.get("verdict")})


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


def _request_digest(resume: Optional[Dict[str, Any]]) -> Optional[str]:
    """A short hash of the request text, never the text itself (ADR-2253 D1): enough to tell two runs of the
    same request apart from two different ones, nothing that can leak what was asked."""
    import hashlib  # noqa: PLC0415
    if not isinstance(resume, dict):
        return None
    text = resume.get("request") or resume.get("user_request")
    if not isinstance(text, str) or not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "model": "unknown",          # what the engine REPORTS, filled in once it is known (never a guess)
        "artifact_ref": None,
        "request_digest": _request_digest(resume),
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
    _audit_run("started", tenant_id, run_id, kind)
    return run_id


def _audit_run(what: str, tenant_id: str, run_id: str, kind: str, **extra: Any) -> None:
    """The run's start/end on the hash chain (ADR-2253). A failing chain write never fails the run."""
    try:
        if what == "started":
            console_audit.forge_run_started(tenant_id=tenant_id, run_id=run_id, run_kind=kind)
        else:
            with _event_lock:
                st = _event_state.get(run_id) or {}
            console_audit.forge_run_finished(tenant_id=tenant_id, run_id=run_id, run_kind=kind,
                                             event_count=st.get("seq", 0), truncated=bool(st.get("dropped", 0)), **extra)
    except Exception:  # noqa: BLE001
        logger.warning("forge run %s: could not audit %s", run_id, what, exc_info=True)


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


#: What a client may see of a run. Never ``resume`` (it carries the request and drafts), ``sid_fingerprint`` or the
#: owner's pid/host - this is an allowlist, so a field added to the record later is private until named here.
PUBLIC_RUN_FIELDS = (
    "kind", "status", "phase", "phases", "progress", "message", "engine", "model", "started_at", "ended_at",
    "artifact_ref", "request_digest", "error", "failure_code", "operation", "base_skill", "tool", "plugin", "skill",
    "interrupted", "resumes",
)


def public_view(run_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"run_id": run_id}
    for k in PUBLIC_RUN_FIELDS:
        if k in record:
            out[k] = record[k]
    out.setdefault("started_at", record.get("created_at"))
    out["live"] = record.get("status") == "running" and _fresh(record)
    return out


def peek_run(run_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """A READ-ONLY copy of a run: no resume, no failure marking, no caching. For viewers (the run API); the
    polling routes use ``get_run``, which may recover an orphan."""
    with runs_lock:
        run = runs.get(run_id)
        if run is not None:
            return dict(run) if run.get("tenant_id") == tenant_id else None
    return _load(run_id, tenant_id)


def list_runs(tenant_id: str, *, kind: Optional[str] = None, status: Optional[str] = None,
              limit: int = 50) -> list[Dict[str, Any]]:
    """The tenant's runs, newest first. Listing is also a reason to prune (a quiet tenant is pruned here too)."""
    prune(tenant_id)
    seen: Dict[str, Dict[str, Any]] = {}
    with runs_lock:
        for rid, r in runs.items():
            if r.get("tenant_id") == tenant_id:
                seen[rid] = dict(r)
    try:
        for f in _runs_dir(tenant_id).glob("run-*.json"):
            if f.stem in seen or not _RUN_ID_RE.match(f.stem) or f.is_symlink():
                continue
            rec = _load(f.stem, tenant_id)
            if rec is not None:
                seen[f.stem] = rec
    except OSError:
        pass
    rows = [(rid, r) for rid, r in seen.items()
            if (kind is None or r.get("kind") == kind) and (status is None or r.get("status") == status)]
    rows.sort(key=lambda x: str(x[1].get("started_at") or x[1].get("created_at") or ""), reverse=True)
    return [public_view(rid, r) for rid, r in rows[: max(1, min(int(limit), 200))]]


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
    last_progress: list = [None]

    def progress(phase: str, pct: int, message: str) -> None:
        update_run(run_id, phase=phase, progress=pct, message=message)
        if last_progress[0] != (phase, message):  # the same line repeated is one event, not many
            last_progress[0] = (phase, message)
            emit_event(run_id, "progress", message, phase=phase, data={"progress": pct})

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
            _events_from_result(run_id, kind, result)
            update_run(run_id, status="success", progress=100, error=None,
                       ended_at=datetime.now(timezone.utc).isoformat(),
                       artifact_ref={"kind": kind, "id": target_id}, **result)
            emit_event(run_id, "progress", str(result.get("message") or "finished"), phase=None, data={"progress": 100})
            finalize_events(run_id)
            _audit_run("finished", tenant_id, run_id, kind, status="success", artifact_kind=kind, artifact_id=target_id)
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
                       ended_at=datetime.now(timezone.utc).isoformat(),
                       **(extra if isinstance(extra, dict) else {}))
            emit_event(run_id, "error", hint(exc), level="error", data={"error_class": type(exc).__name__})
            finalize_events(run_id)
            _audit_run("finished", tenant_id, run_id, kind, status="failed", artifact_kind=kind)

    threading.Thread(target=run_task, name=f"forge-{kind}-{run_id}", daemon=True).start()
