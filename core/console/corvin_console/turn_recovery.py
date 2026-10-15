"""Interrupted-turn recovery: a console turn cut off by a host restart is run again.

Measured 2026-10-15: ``systemctl --user restart corvin-webui`` (KillMode=control-group)
ended a running chat turn after 337 s - the ``claude`` child got SIGTERM, the task was
finalized ``failed`` / exit 143 with an empty ``result_summary``, and nothing ever ran
it again. The user got no output and no retry. ``TaskManager.reap_stale_running`` only
finalizes such a task; it never re-runs it.

Contract:

* On host shutdown the lifespan calls :func:`note_shutdown` (one timestamp per tenant).
* After the next start :func:`schedule_recovery` looks for the NEWEST task of each chat
  that failed from a restart - signal exit code (143/137/-15/-9/-1), finalized inside the
  shutdown window or reaped as an orphan - and produced no output. It is re-run on the
  same chat through the normal ``stream_turn`` path, so persistence, ledger, L44 and
  L34 gates apply exactly as for a typed message.
* A run that again ends without a ``result`` is re-run, at most :data:`MAX_ATTEMPTS`
  times per request in total (persisted per chat, so a crash loop cannot restart forever).
* Deliberate stops (cancel, background-scope caps) are never matched: they are not
  signal exits inside the shutdown window.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corvin_core.failure_classes import FailureClass, classify_failure

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
WINDOW_S = 30 * 60               # only a recent interruption is worth re-running
SHUTDOWN_SLACK_S = 120.0         # a turn finalized this long before the shutdown note belongs to it
SHUTDOWN_AFTER_S = 10.0
START_DELAY_S = 15.0
SYSTEM_SID = "auto-recovery"
PROMPT_PREFIX = (
    "[Automatic restart: the previous attempt at this request was cut off by a service "
    "restart before it produced an answer. Continue from the current state of the "
    "workspace and do not repeat finished work.]\n\n"
)

_started = False


@dataclass(frozen=True)
class Candidate:
    tenant_id: str
    sid: str
    task_id: str
    instruction: str
    key: str
    attempts: int
    failure_class: str          # shared classifier verdict (corvin_core.failure_classes); always C1 here


def _marker_path(tenant_id: str) -> Path:
    from . import _bootstrap
    return _bootstrap.forge_paths.tenant_global_dir(tenant_id) / "last_shutdown.json"


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def note_shutdown(tenant_id: str | None = None) -> None:
    """Called from the host lifespan teardown; never raises."""
    try:
        tid = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
        _atomic_write(_marker_path(tid), {"at": time.time()})
    except Exception as exc:  # noqa: BLE001 - shutdown must not fail
        logger.warning("turn-recovery: shutdown not noted: %s", exc)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _last_event(tasks_dir: Path, task_id: str) -> dict[str, Any]:
    try:
        lines = (tasks_dir / f"{task_id}.events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            return ev
    return {}


def _key(instruction: str) -> str:
    return hashlib.sha256(instruction.strip().encode("utf-8")).hexdigest()[:16]


def _attempts(tasks_dir: Path) -> dict[str, int]:
    raw = _read_json(tasks_dir / "recovery_attempts.json")
    return {k: int(v) for k, v in raw.items() if isinstance(v, int)}


def _bump(tasks_dir: Path, key: str) -> int:
    data = _attempts(tasks_dir)
    data[key] = data.get(key, 0) + 1
    _atomic_write(tasks_dir / "recovery_attempts.json", data)
    return data[key]


def interrupted_at(meta: dict[str, Any], last_event: dict[str, Any]) -> float:
    """When the task was cut off. A FAILED task carries ``ended_at``; an INTERRUPTED one
    (PLAN-0958 P1: reaper, killed worker) is not terminal and has none - its last event is."""
    return float(meta.get("ended_at") or last_event.get("timestamp") or 0)


def is_restart_kill(meta: dict[str, Any], last_event: dict[str, Any], marker: Any) -> bool:
    """Cause test: the task was cut off with no output by a signal exit AND either the reaper
    found it dead or it ended inside the shutdown window. Pure - no I/O, no clock; the
    recovery window (:data:`WINDOW_S`) is a recoverability test and stays in find_interrupted.

    Both ``failed`` (signal exit finalized in-process) and ``interrupted`` (reaped orphan,
    killed worker) qualify: after P1 the reaper no longer writes ``failed``, so testing only
    ``failed`` would silently stop re-running every reaped orphan.
    """
    if meta.get("status") not in ("failed", "interrupted") or meta.get("result_summary"):
        return False
    if classify_failure(exit_code=meta.get("exit_code")).failure_class is not FailureClass.C1:
        return False
    ended = interrupted_at(meta, last_event)
    in_window = isinstance(marker, (int, float)) and marker - SHUTDOWN_SLACK_S <= ended <= marker + SHUTDOWN_AFTER_S
    return bool(last_event.get("reaped")) or in_window


def find_interrupted(tenant_id: str, now: float | None = None) -> list[Candidate]:
    """Newest task per chat, if a restart cut it off before it produced output."""
    from . import chat_runtime
    now = now or time.time()
    marker = _read_json(_marker_path(tenant_id)).get("at")
    out: list[Candidate] = []
    for sess in chat_runtime.list_sessions(tenant_id):
        if sess.workdir is None:
            continue
        tasks_dir = Path(sess.workdir) / "tasks"
        newest: dict[str, Any] | None = None
        for meta_file in tasks_dir.glob("*.json"):
            if meta_file.name == "recovery_attempts.json":
                continue
            meta = _read_json(meta_file)
            if meta.get("task_id") and (newest is None or (meta.get("created_at") or 0) > (newest.get("created_at") or 0)):
                newest = meta
        if not newest:
            continue
        last = _last_event(tasks_dir, newest["task_id"])
        if now - interrupted_at(newest, last) > WINDOW_S:
            continue
        if not is_restart_kill(newest, last, marker):
            continue
        instruction = str((newest.get("input") or {}).get("instruction") or "").strip()
        if not instruction:
            continue
        key = _key(instruction)
        failure_class = classify_failure(exit_code=newest.get("exit_code")).code
        out.append(Candidate(tenant_id, sess.sid, newest["task_id"], instruction, key,
                             _attempts(tasks_dir).get(key, 0), failure_class))
    return out


async def _run_once(cand: Candidate) -> bool:
    """Run the request through the normal turn path. True iff it produced a result."""
    from . import chat_runtime, live_turns
    from .routes.chat import _session_turn_lock
    got_output = False
    async with _session_turn_lock(cand.tenant_id, cand.sid):
        sess = chat_runtime.get_session(cand.tenant_id, cand.sid)
        if sess is None:
            return True            # the chat was deleted: nothing to recover
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        chat_runtime.register_live_turn(cand.tenant_id, cand.sid, loop, task)
        turn_id = live_turns.begin(cand.tenant_id, cand.sid, live_turns.new_token())
        try:
            async with contextlib.aclosing(
                chat_runtime.stream_turn(sess, PROMPT_PREFIX + cand.instruction, sid_fingerprint=SYSTEM_SID)
            ) as gen:
                async for event in gen:
                    live_turns.publish(cand.tenant_id, cand.sid, turn_id, event)
                    if event.get("type") == "result" and str(event.get("text") or "").strip():
                        got_output = True
        finally:
            chat_runtime.unregister_live_turn(cand.tenant_id, cand.sid, loop, task)
            live_turns.end(cand.tenant_id, cand.sid, turn_id)
    return got_output


def _audit(cand: Candidate, *, ok: bool, action: str, reason: str | None = None) -> None:
    try:
        from . import audit as console_audit
        kw = dict(tenant_id=cand.tenant_id, sid_fingerprint=SYSTEM_SID, action=action,
                  target_kind="chat", target_id=cand.sid)
        if ok:
            console_audit.action_performed(**kw)
        else:
            console_audit.action_failed(reason=reason or "failed", **kw)
    except Exception as exc:  # noqa: BLE001
        logger.error("turn-recovery audit %s not written: %s", action, exc)


def _audit_failure_class(cand: Candidate, *, source: str) -> None:
    """The class the shared classifier gave this interruption, on the chain (CONCEPT-0112).
    Never raises: a failed audit write must not stop the recovery it describes."""
    try:
        from . import audit as console_audit
        console_audit.system_event(
            tenant_id=cand.tenant_id, event="turn.failure_classified",
            details={"tenant_id": cand.tenant_id, "failure_class": cand.failure_class,
                     "reason_code": "host_signal", "source": source},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("turn-recovery failure-class audit not written: %s", exc)


def _notify(cand: Candidate, kind: str, attempt: int) -> None:
    """Tell the user in the chat (PLAN-0958 P2). Never raises."""
    try:
        from . import chat_runtime, task_notices
        sess = chat_runtime.get_session(cand.tenant_id, cand.sid)
        if sess is not None and sess.workdir is not None:
            task_notices.emit(sess, kind, task_id=cand.task_id, failure_class=cand.failure_class,
                              instruction=cand.instruction, attempt=attempt, max_attempts=MAX_ATTEMPTS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("turn-recovery notice %s not sent: %s", kind, type(exc).__name__)


async def recover_candidate(cand: Candidate) -> str:
    """Re-run until it yields output or the attempt budget is spent. Returns the outcome.
    The user gets one notice per step: resuming (before each attempt), recovered, or gave up."""
    from . import chat_runtime
    sess = chat_runtime.get_session(cand.tenant_id, cand.sid)
    if sess is None or sess.workdir is None:
        return "gone"
    tasks_dir = Path(sess.workdir) / "tasks"
    while _attempts(tasks_dir).get(cand.key, 0) < MAX_ATTEMPTS:
        n = _bump(tasks_dir, cand.key)
        logger.info("turn-recovery: re-running interrupted turn %s (chat %s, attempt %d/%d)",
                    cand.task_id, cand.sid, n, MAX_ATTEMPTS)
        _audit(cand, ok=True, action="chat.turn.recovery_started")
        _audit_failure_class(cand, source="console")
        _notify(cand, "interrupted_resuming", n)
        try:
            if await _run_once(cand):
                _notify(cand, "recovered", n)
                return "recovered"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one chat must not stop the others
            logger.warning("turn-recovery: attempt %d for %s failed: %s", n, cand.sid, type(exc).__name__)
    _audit(cand, ok=False, action="chat.turn.recovery_exhausted", reason="no_output_after_retries")
    _notify(cand, "gave_up", MAX_ATTEMPTS)
    logger.warning("turn-recovery: chat %s gave no output after %d attempts - giving up", cand.sid, MAX_ATTEMPTS)
    return "exhausted"


async def recover_all(tenant_id: str, *, delay_s: float = START_DELAY_S) -> list[tuple[str, str]]:
    await asyncio.sleep(delay_s)
    results: list[tuple[str, str]] = []
    for cand in find_interrupted(tenant_id):
        if cand.attempts >= MAX_ATTEMPTS:
            continue
        results.append((cand.sid, await recover_candidate(cand)))
    return results


def schedule_recovery(tenant_id: str | None = None, *, delay_s: float = START_DELAY_S) -> None:
    """Fire-and-forget; called from the lifespan of BOTH hosts (console app, gateway app)."""
    global _started
    if _started:
        return
    try:
        tid = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"

        async def _run() -> None:
            try:
                done = await recover_all(tid, delay_s=delay_s)
                if done:
                    logger.info("turn-recovery: %s", done)
            except Exception as exc:  # noqa: BLE001 - never affects startup
                logger.warning("turn-recovery failed: %s", exc)

        asyncio.get_running_loop().create_task(_run())
        _started = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("turn-recovery not scheduled: %s", exc)
