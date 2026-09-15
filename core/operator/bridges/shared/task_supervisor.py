"""task_supervisor.py — keeps a long autonomous run going until it is DONE.

THE PROBLEM this solves
-----------------------
``/task`` spawns ONE detached ``bg_task_worker`` process and hopes. Everything
that can end that process ends the work permanently:

* ``CORVIN_BG_TASK_TIMEOUT`` (default 1800 s) fires → the turn is cancelled and
  reported as a FAILURE. A genuinely long workflow is killed at 30 minutes.
* SIGKILL / OOM / reboot → ``completion_notify``'s dead-producer reap converts
  the record into "the background worker stopped without reporting a result".
* The engine wedges without exiting → nothing notices at all; the record sits
  pending until ``CN_PENDING_MAX_AGE`` (7 days), holding a ``/task``
  concurrency slot the whole time.

In every case the *instruction* was already gone — the ``/task`` spec file is
unlinked the moment the worker reads it — so nothing could have resumed the
work even if something had wanted to. There was no owner of "keep going until
done".

THE MECHANISM
-------------
A durable RUN RECORD (``CORVIN_HOME/task_runs/<task_id>.json``) that outlives
every worker process and holds what a resume needs: the instruction, the
originating routing, the profile, the attempt history, and the budgets that
bound it. A supervisor tick — driven by the pollers that already run every
~60 s (``bg_monitor``'s systemd timer and the adapter main loop, both
idempotent) — reconciles each run against reality:

* worker alive and its heartbeat fresh      → leave it alone
* worker gone / heartbeat stale, budget left → RESUME: spawn a fresh worker
  with a continuation prompt, tell the user via ``task_progress``
* budget exhausted (attempts or wall clock)  → terminal: ``mark_done(ok=False)``
  with an honest account of what was tried
* the run's completion record is no longer pending → the work finished; retire
  the run record

BOUNDS are the whole design. An unbounded "restart until done" is a fork bomb
with extra steps, so every resume is charged against two independent budgets
(``max_attempts`` AND a total wall clock) and a spawn is serialized by a
per-run ``O_EXCL`` lock so two concurrent pollers can never double-launch.

Pure stdlib. Never raises out of :func:`supervise` — a supervisor that can
crash is worse than no supervisor, because the pollers that call it also carry
the completion deliveries.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# How many worker launches one run may consume in total (the first attempt
# counts as one). Past this the run is failed honestly rather than retried.
SUP_MAX_ATTEMPTS = int(os.environ.get("SUP_MAX_ATTEMPTS", "5"))
# Total wall clock a run may occupy across ALL attempts. The second, independent
# bound: 5 attempts that each wedge for 30 min must not add up to 2.5 h of
# silence. Default 6 h — long enough for real multi-phase work, short enough
# that a runaway is not a day-long problem.
SUP_TOTAL_BUDGET = float(os.environ.get("SUP_TOTAL_BUDGET", str(6 * 3600)))
# Grace after a launch before the supervisor is willing to believe the worker
# is dead. Covers interpreter start-up + the heavy `import adapter` before the
# first heartbeat can possibly be written.
SUP_LAUNCH_GRACE = float(os.environ.get("SUP_LAUNCH_GRACE", "180"))
# A worker whose heartbeat is older than this is wedged even if its pid is
# alive — the case nothing detected before. Must be comfortably larger than the
# worker's own heartbeat interval (see bg_task_worker._HEARTBEAT_INTERVAL).
SUP_HEARTBEAT_STALE = float(os.environ.get("SUP_HEARTBEAT_STALE", "600"))
# Backoff between attempts, doubling, capped. Prevents a crash-on-startup loop
# from burning the attempt budget in five seconds.
SUP_BACKOFF_BASE = float(os.environ.get("SUP_BACKOFF_BASE", "60"))
SUP_BACKOFF_MAX = float(os.environ.get("SUP_BACKOFF_MAX", "900"))
# Retired run records are kept briefly for forensics, then pruned.
SUP_DONE_TTL = float(os.environ.get("SUP_DONE_TTL", str(24 * 3600)))
# A spawn lock older than this belonged to a poller that died mid-launch.
SUP_LOCK_STALE = float(os.environ.get("SUP_LOCK_STALE", "300"))
# How much of an interrupted attempt's output is carried into the continuation
# prompt. Bounded so a resume prompt cannot grow without limit across attempts.
SUP_CARRY_CHARS = int(os.environ.get("SUP_CARRY_CHARS", "2000"))

_STATE_ACTIVE = "active"
_STATE_DONE = "done"


# ─── paths ─────────────────────────────────────────────────────────────────


def _corvin_home() -> Path:
    v = os.environ.get("CORVIN_HOME")
    if v:
        return Path(os.path.expanduser(os.path.expandvars(v)))
    try:
        from paths import corvin_home as _ch  # type: ignore

        return _ch()
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin"


def _runs_dir() -> Path:
    return _corvin_home() / "task_runs"


def _run_path(task_id: str) -> Path:
    safe = "".join(c for c in str(task_id) if c.isalnum() or c in "-_")
    return _runs_dir() / f"{safe}.json"


def heartbeat_path(task_id: str) -> Path:
    """Where the worker for *task_id* stamps liveness. Public so the worker and
    the supervisor cannot drift on the location."""
    safe = "".join(c for c in str(task_id) if c.isalnum() or c in "-_")
    return _runs_dir() / f"{safe}.hb"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{secrets.token_hex(4)}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)  # the record holds the instruction + routing PII
        except OSError:
            pass
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


# ─── liveness (shared with completion_notify's reaper) ─────────────────────


def _is_zombie(pid: int) -> bool:
    """True when *pid* is an exited-but-unreaped child (Linux only).

    This matters because the adapter spawns workers with ``Popen`` and never
    waits on them: to the ADAPTER process a dead worker stays a zombie, and a
    zombie answers ``os.kill(pid, 0)`` successfully. Without this check a
    SIGKILLed worker looked alive to the supervisor tick running inside the
    adapter, and healing had to wait for the heartbeat to go stale (up to
    SUP_HEARTBEAT_STALE) instead of happening on the next tick.

    Best-effort: no /proc (macOS, Windows, a container without it) → False,
    and the heartbeat check still catches the case.
    """
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        # The comm field is parenthesised and may itself contain spaces, so
        # split after the closing paren — state is the first field there.
        return stat[stat.rindex(")") + 1:].split()[0] == "Z"
    except (OSError, ValueError, IndexError):
        return False


def _pid_alive(pid: int) -> bool:
    """Non-destructive liveness of a worker pid on THIS host.

    Delegates the existence check to completion_notify so the two owners of "is
    the producer still there" cannot disagree — including the Windows subtlety
    that ``os.kill(pid, 0)`` KILLS the process there — and additionally treats a
    zombie as NOT alive (see :func:`_is_zombie`).
    """
    try:
        import completion_notify as _cn  # type: ignore

        if not _cn._pid_alive(int(pid)):  # noqa: SLF001 — sibling module, one impl
            return False
    except Exception:  # noqa: BLE001
        return True  # unknown → assume alive (conservative: never double-launch)
    return not _is_zombie(pid)


def _host_boot_id() -> str:
    try:
        import completion_notify as _cn  # type: ignore

        return _cn._host_boot_id()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return ""


# ─── producer API (called by the adapter and the worker) ───────────────────


def register_run(task_id: str, *, instruction: str, channel: str,
                 chat_key: str, sender: str = "", tenant_id: str = "_default",
                 profile: dict | None = None, msg_id: str | None = None,
                 max_attempts: int | None = None,
                 total_budget_s: float | None = None,
                 supervise_enabled: bool = True, progress_enabled: bool = True,
                 now: float | None = None) -> dict:
    """Create the durable run record for a supervised background task.

    Called at ``/task`` spawn time, BEFORE the first worker starts, so a worker
    that dies during start-up is still resumable. Returns the record.

    ``supervise_enabled`` / ``progress_enabled`` are the per-run copies of the
    two feature flags, resolved ONCE at spawn time and stamped here. Reading
    them from the record rather than re-resolving the flag in every process
    means a run behaves consistently for its whole life even if the operator
    flips a flag halfway through, and gives the worker a single switch surface
    it can read without importing the console package.
    """
    now = time.time() if now is None else now
    rec = {
        "task_id": str(task_id),
        "instruction": str(instruction),
        "channel": str(channel),
        "chat_key": str(chat_key),
        "sender": str(sender or ""),
        "tenant_id": str(tenant_id or "_default"),
        "profile": profile,
        "msg_id": msg_id,
        "state": _STATE_ACTIVE,
        "supervise": bool(supervise_enabled),
        "progress": bool(progress_enabled),
        # ONE launch is counted per SPAWN, by whoever spawns. The adapter's own
        # launch is implicit in register_run, so the count starts at 1;
        # supervise() increments on each resume it starts. attempt_started()
        # deliberately does NOT increment — when both did, every resume cost
        # two attempts and a run configured for 5 got 3.
        "attempts": 1,
        "max_attempts": int(max_attempts or SUP_MAX_ATTEMPTS),
        "total_budget_s": float(total_budget_s or SUP_TOTAL_BUDGET),
        "created_at": now,
        # Stamped NOW even though no worker has started yet: the adapter calls
        # register_run and THEN spawns. A supervisor tick landing in that
        # window would otherwise see attempts=0 with no launch grace and spawn
        # a SECOND worker for the same task. The grace covers the adapter's own
        # spawn; if that spawn genuinely failed, the grace expires and the
        # supervisor starts the run — which is the healing we want.
        "last_attempt_at": now,
        "next_attempt_at": 0.0,
        "worker_pid": None,
        "worker_boot": None,
        # Bounded history of what each attempt ended with — this is what makes
        # the continuation prompt honest instead of "try again".
        "attempt_log": [],
        "carry": "",
        "done_at": None,
        "outcome": None,
    }
    _atomic_write(_run_path(task_id), rec)
    return rec


def get_run(task_id: str) -> dict | None:
    """Read one run record (None if unknown)."""
    return _read(_run_path(task_id))


def attempt_started(task_id: str, *, pid: int | None = None,
                    now: float | None = None) -> bool:
    """Stamp the calling worker as the current owner of *task_id*.

    Called by ``bg_task_worker`` at start-up, next to ``completion_notify.claim``.
    Records the pid + host boot id so a resume decision can tell "worker died"
    from "worker is still going". Does NOT consume an attempt — the launch was
    already counted by whoever spawned this worker.
    """
    now = time.time() if now is None else now
    path = _run_path(task_id)
    rec = _read(path)
    if rec is None or rec.get("state") != _STATE_ACTIVE:
        return False
    rec["worker_pid"] = int(pid if pid is not None else os.getpid())
    rec["worker_boot"] = _host_boot_id()
    # NOT `attempts += 1` — see the note in register_run: the spawner owns the
    # count. This call only says "that launch is now running, and here is who".
    rec["attempts"] = max(1, int(rec.get("attempts", 1)))
    rec["last_attempt_at"] = now
    try:
        _atomic_write(path, rec)
    except OSError:
        return False
    touch_heartbeat(task_id, now=now)
    return True


def touch_heartbeat(task_id: str, *, now: float | None = None) -> None:
    """Stamp liveness for *task_id*. Best-effort, never raises.

    A separate tiny file rather than a field in the run record: the worker
    writes it every few seconds and the record is rewritten by the supervisor,
    so sharing one file would make them fight over it.
    """
    now = time.time() if now is None else now
    try:
        p = heartbeat_path(task_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".hb.tmp{secrets.token_hex(4)}")
        tmp.write_text(str(now), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _read_heartbeat(task_id: str) -> float:
    try:
        return float(heartbeat_path(task_id).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0.0


def attempt_finished(task_id: str, *, ok: bool, summary: str = "",
                     resumable: bool = False, now: float | None = None) -> bool:
    """Record how the current attempt ended.

    ``resumable=True`` (the worker hit its own wall-clock watchdog with partial
    output) tells the supervisor to continue rather than treat the attempt as a
    terminal failure. ``ok=True`` retires the run: the work is done.
    """
    now = time.time() if now is None else now
    path = _run_path(task_id)
    rec = _read(path)
    if rec is None:
        return False
    log = list(rec.get("attempt_log") or [])
    log.append({
        "n": int(rec.get("attempts", 0)),
        "at": now,
        "ok": bool(ok),
        "resumable": bool(resumable),
        "summary": (summary or "")[:400],
    })
    rec["attempt_log"] = log[-10:]  # bounded: last 10 attempts
    rec["worker_pid"] = None
    if summary:
        rec["carry"] = summary[-SUP_CARRY_CHARS:]
    if ok:
        rec["state"] = _STATE_DONE
        rec["done_at"] = now
        rec["outcome"] = "completed"
    else:
        rec["next_attempt_at"] = now + _backoff_for(int(rec.get("attempts", 1)))
    try:
        _atomic_write(path, rec)
    except OSError:
        return False
    if ok:
        _cleanup_run_artifacts(task_id)
    return True


def _human_duration(seconds: float) -> str:
    """Render a budget the way a person would say it ("6-hour", "90-minute")."""
    minutes = int(seconds // 60)
    if minutes and minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours}-hour"
    return f"{minutes}-minute"


def _backoff_for(attempts: int) -> float:
    return min(SUP_BACKOFF_MAX, SUP_BACKOFF_BASE * (2 ** max(0, attempts - 1)))


def _cleanup_run_artifacts(task_id: str) -> None:
    try:
        heartbeat_path(task_id).unlink(missing_ok=True)
    except OSError:
        pass
    try:
        import task_progress as _tp  # type: ignore

        _tp.finish(task_id)
    except Exception:  # noqa: BLE001
        pass


def count_active(sender: str | None = None) -> int:
    """Count runs still being supervised, optionally for one sender."""
    d = _runs_dir()
    if not d.exists():
        return 0
    n = 0
    for path in d.glob("*.json"):
        rec = _read(path)
        if rec is None or rec.get("state") != _STATE_ACTIVE:
            continue
        if sender is not None and rec.get("sender") != sender:
            continue
        n += 1
    return n


def list_runs(*, active_only: bool = True) -> list[dict]:
    """List run records, newest first — backs the operator-facing status view."""
    d = _runs_dir()
    if not d.exists():
        return []
    out = []
    for path in d.glob("*.json"):
        rec = _read(path)
        if rec is None:
            continue
        if active_only and rec.get("state") != _STATE_ACTIVE:
            continue
        out.append(rec)
    out.sort(key=lambda r: float(r.get("created_at") or 0), reverse=True)
    return out


# ─── the continuation prompt ───────────────────────────────────────────────


def continuation_prompt(rec: dict) -> str:
    """Build the instruction for a RESUMED attempt.

    Carries three things the engine needs and would otherwise have lost: the
    original goal, the fact that this is a continuation (so it does not restart
    from scratch), and a bounded excerpt of where the last attempt got to.
    """
    goal = str(rec.get("instruction") or "").strip()
    n = int(rec.get("attempts", 0))
    carry = str(rec.get("carry") or "").strip()
    reasons = [a.get("summary", "") for a in (rec.get("attempt_log") or [])[-3:]]
    reason = next((r for r in reversed(reasons) if r), "the worker stopped "
                  "without reporting a result")
    parts = [
        "You are RESUMING an interrupted autonomous background task. "
        f"This is attempt {n + 1}. Do NOT start over from scratch — continue "
        "from where the previous attempt stopped, verify what is already done "
        "before redoing it, and carry the task through to completion.",
        "",
        f"ORIGINAL TASK:\n{goal}",
        "",
        f"WHY THE PREVIOUS ATTEMPT STOPPED:\n{reason}",
    ]
    if carry:
        parts += ["", "WHERE IT GOT TO (partial output from the last attempt):",
                  carry]
    parts += ["", "Finish the task now and report the complete result."]
    return "\n".join(parts)


# ─── the tick ──────────────────────────────────────────────────────────────


# Sentinel: the completion store could not be consulted at all. Distinct from
# None ("the record is gone"), because the two demand opposite actions —
# "gone" retires the run, "unknown" must leave it exactly as it was.
_UNKNOWN = "\x00unknown"


def _completion_state(task_id: str) -> str | None:
    """State of the task's completion record.

    Returns the state string, ``None`` when the record is genuinely gone, or
    ``_UNKNOWN`` when the store could not be read. Collapsing the last two into
    None (the original) meant one transient ImportError retired EVERY active
    run as "orphaned" — permanently ending every long task in flight.
    """
    try:
        import completion_notify as _cn  # type: ignore

        rec = _cn._read(_cn._record_path(task_id))  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        print(f"task_supervisor: completion store unreadable for {task_id}: {e}",
              file=sys.stderr)
        return _UNKNOWN
    return rec.get("state") if rec else None


def _spawn_worker(rec: dict, prompt: str) -> int | None:
    """Launch a detached ``bg_task_worker`` for *rec* with *prompt*.

    Mirrors the adapter's spawn exactly (0600 spec FILE, never argv, which is
    world-readable in /proc/<pid>/cmdline; Windows creationflags, because
    ``start_new_session`` is a no-op there and the "detached" worker would die
    with its parent). Returns the pid, or None on failure.
    """
    import tempfile as _tf

    spec = {
        "task_id": rec.get("task_id"),
        "instruction": prompt,
        "channel": rec.get("channel"),
        "chat_key": rec.get("chat_key"),
        # Session ISOLATION (ADR-0553 fix): same worker-private, per-task key the
        # adapter mints, derived identically so a supervised RETRY resumes the
        # worker's OWN prior isolated session (same task_id ⇒ same key) and never
        # the operator's live chat. Stable across attempts by construction.
        "engine_chat_key": (
            rec.get("engine_chat_key")
            or f"bgtask::{rec.get('chat_key')}::{rec.get('task_id')}"
        ),
        "sender": rec.get("sender") or "",
        "profile": rec.get("profile"),
        "msg_id": rec.get("msg_id"),
        "resumed": True,
    }
    spec_file = None
    try:
        fd, spec_file = _tf.mkstemp(prefix="bgspec_", suffix=".json")
        # encoding pinned: instruction text routinely carries emoji/umlauts and
        # a locale-default cp1252 on Windows would raise UnicodeEncodeError,
        # i.e. the resume would silently never start.
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        try:
            os.chmod(spec_file, 0o600)
        except OSError:
            pass
        worker = str(HERE / "bg_task_worker.py")
        flags = 0
        if sys.platform == "win32":
            flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        proc = subprocess.Popen(
            [sys.executable, worker, spec_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, creationflags=flags,
        )
        return proc.pid
    except Exception as e:  # noqa: BLE001
        print(f"task_supervisor: spawn failed for {rec.get('task_id')}: {e}",
              file=sys.stderr)
        if spec_file:
            try:
                os.unlink(spec_file)
            except OSError:
                pass
        return None


def _emit(task_id: str, text: str, kind: str) -> None:
    try:
        import task_progress as _tp  # type: ignore

        _tp.emit(task_id, text, kind=kind, force=True)
    except Exception:  # noqa: BLE001 — a status line never blocks healing
        pass


def _fail_terminally(rec: dict, reason: str, now: float) -> None:
    """Give up honestly: tell the user what was tried and retire the run."""
    task_id = str(rec.get("task_id"))
    attempts = int(rec.get("attempts", 0))
    tried = "; ".join(
        f"attempt {a.get('n')}: {a.get('summary') or 'no result'}"
        for a in (rec.get("attempt_log") or [])[-3:]
    )
    carry = str(rec.get("carry") or "").strip()
    text = (f"the background task could not be completed after {attempts} "
            f"attempt(s): {reason}.")
    if tried:
        text += f"\n\nWhat was tried — {tried}"
    if carry:
        text += f"\n\nLast partial output:\n{carry[-SUP_CARRY_CHARS:]}"
    try:
        import completion_notify as _cn  # type: ignore

        _cn.mark_done(task_id, text=text, ok=False)
    except Exception as e:  # noqa: BLE001
        print(f"task_supervisor: mark_done failed for {task_id}: {e}",
              file=sys.stderr)
    rec["state"] = _STATE_DONE
    rec["done_at"] = now
    rec["outcome"] = f"failed: {reason}"
    try:
        _atomic_write(_run_path(task_id), rec)
    except OSError:
        pass
    _cleanup_run_artifacts(task_id)


def supervise(*, now: float | None = None,
              spawn: "Callable[[dict, str], int | None] | None" = None) -> int:
    """Reconcile every supervised run against reality. Returns resumes started.

    Called by the pollers that already run (``bg_monitor.run_once`` on the
    systemd timer and the adapter main loop). Idempotent and concurrency-safe:
    a per-run ``O_EXCL`` spawn lock means two pollers in the same second can
    never double-launch a worker.

    Never raises — the callers also carry completion delivery, and a supervisor
    that can crash would take that down with it.
    """
    now = time.time() if now is None else now
    spawn = spawn or _spawn_worker
    d = _runs_dir()
    if not d.exists():
        return 0
    resumed = 0
    for path in sorted(d.glob("*.json")):
        try:
            rec = _read(path)
            if rec is None:
                # Malformed record — prune once it is clearly stale so it is
                # not re-scanned every poll forever.
                try:
                    if now - path.stat().st_mtime > SUP_DONE_TTL:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            task_id = str(rec.get("task_id") or "")
            if not task_id:
                path.unlink(missing_ok=True)
                continue

            if rec.get("state") == _STATE_DONE:
                if now - float(rec.get("done_at") or 0) > SUP_DONE_TTL:
                    path.unlink(missing_ok=True)
                    _cleanup_run_artifacts(task_id)
                continue

            # 1) Did the work actually finish? The completion record is the
            #    authority — a worker that reached mark_done leaves it
            #    ready/delivered. Never resurrect such a run.
            cstate = _completion_state(task_id)
            if cstate is _UNKNOWN:
                continue  # cannot decide anything safely this tick
            if cstate is None:
                # The completion record is gone (GDPR purge, or pruned) — there
                # is nowhere to deliver a result, so supervising is pointless.
                rec["state"] = _STATE_DONE
                rec["done_at"] = now
                rec["outcome"] = "orphaned: completion record gone"
                _atomic_write(path, rec)
                _cleanup_run_artifacts(task_id)
                continue
            if cstate != "pending":
                rec["state"] = _STATE_DONE
                rec["done_at"] = now
                rec["outcome"] = rec.get("outcome") or "completed"
                _atomic_write(path, rec)
                _cleanup_run_artifacts(task_id)
                continue

            # 1b) Supervision off for this run (flag was off at spawn): the
            #     record exists only to carry progress routing. Never resume it
            #     — flag-off must be byte-identical to the pre-feature path,
            #     where a dead worker was reaped by completion_notify.
            if not rec.get("supervise", True):
                continue

            # 2) Is the current attempt still alive?
            pid = rec.get("worker_pid")
            boot = rec.get("worker_boot")
            last_attempt = float(rec.get("last_attempt_at") or 0)
            hb = _read_heartbeat(task_id)
            in_grace = (now - last_attempt) < SUP_LAUNCH_GRACE

            if pid:
                rebooted = bool(boot) and boot != _host_boot_id()
                alive = (not rebooted) and _pid_alive(int(pid))
                if alive:
                    hb_age = now - hb if hb else (now - last_attempt)
                    if hb_age <= SUP_HEARTBEAT_STALE or in_grace:
                        continue  # healthy, leave it alone
                    # Alive but wedged: no heartbeat for too long. Stop it, then
                    # fall through to the resume decision. Without this a hung
                    # engine held the slot until CN_PENDING_MAX_AGE (7 days).
                    _emit(task_id, f"worker wedged (no heartbeat for "
                                   f"{int(hb_age)}s) — restarting it.", "stall")
                    _terminate(int(pid))
                    # PERSIST the clearing immediately. Falling through to a
                    # `continue` below (backoff, lock contention) would discard
                    # this in-memory change, and the next tick would find the
                    # same stale pid, SIGTERM it again and emit the same
                    # "wedged" notice again — once per poll, forced past the
                    # progress rate limit, for as long as the backoff lasts.
                    rec["worker_pid"] = None
                    try:
                        _atomic_write(path, rec)
                    except OSError:
                        pass
                else:
                    if in_grace:
                        continue  # too early to call it dead
            elif in_grace and last_attempt > 0:
                continue  # a spawn is in flight

            # 3) The attempt is over and the work is not done. Resume or fail.
            attempts = int(rec.get("attempts", 0))
            max_attempts = int(rec.get("max_attempts") or SUP_MAX_ATTEMPTS)
            budget = float(rec.get("total_budget_s") or SUP_TOTAL_BUDGET)
            elapsed = now - float(rec.get("created_at") or now)

            if elapsed > budget:
                _fail_terminally(rec, f"the {_human_duration(budget)} total "
                                      "time budget ran out", now)
                continue
            if attempts >= max_attempts:
                _fail_terminally(rec, f"the retry budget ({max_attempts} "
                                      "attempts) ran out", now)
                continue

            next_at = float(rec.get("next_attempt_at") or 0)
            if attempts > 0 and now < next_at:
                continue  # backing off

            # Serialize the spawn: O_EXCL is the mutex, so two pollers in the
            # same tick cannot both launch a worker for this run.
            lock = path.with_suffix(".json.spawnlock")
            try:
                if now - lock.stat().st_mtime > SUP_LOCK_STALE:
                    steal = str(lock) + f".steal{secrets.token_hex(4)}"
                    try:
                        os.rename(str(lock), steal)
                        os.unlink(steal)
                    except OSError:
                        pass
            except OSError:
                pass
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            except OSError:
                continue
            try:
                os.close(fd)
                # Re-read under the lock: another poller may have resumed it
                # between the scan and the claim.
                rec = _read(path)
                if rec is None or rec.get("state") != _STATE_ACTIVE:
                    continue
                if int(rec.get("attempts", 0)) != attempts:
                    continue

                prompt = continuation_prompt(rec)
                new_pid = spawn(rec, prompt)
                if new_pid is None:
                    rec["next_attempt_at"] = now + _backoff_for(attempts + 1)
                    _atomic_write(path, rec)
                    continue
                # Count this launch HERE (the spawner owns the count) and stamp
                # last_attempt_at NOW, so the launch grace starts at the spawn
                # and a worker that dies before it can stamp anything is still
                # covered by the grace rather than instantly re-launched.
                rec["last_attempt_at"] = now
                rec["worker_pid"] = int(new_pid)
                rec["worker_boot"] = _host_boot_id()
                rec["attempts"] = attempts + 1
                # Arm the backoff HERE, not only when a worker reports back.
                # A worker that is SIGKILLed never calls attempt_finished, so
                # without this the only thing spacing out relaunches would be
                # SUP_LAUNCH_GRACE — and a process that dies instantly on
                # start-up would burn the whole attempt budget in one grace
                # period. Belt and braces with the grace, deliberately.
                rec["next_attempt_at"] = now + _backoff_for(attempts + 1)
                _atomic_write(path, rec)
                touch_heartbeat(task_id, now=now)
                resumed += 1
                _emit(task_id,
                      f"the task stopped before finishing — resuming it "
                      f"(attempt {attempts + 1} of {max_attempts}).", "resume")
            finally:
                try:
                    os.unlink(str(lock))
                except OSError:
                    pass
        except Exception as e:  # noqa: BLE001 — per-run isolation: one poisoned
            # record must not stop every run sorted after it from being healed.
            print(f"task_supervisor: supervise failed on {path.name}: {e}",
                  file=sys.stderr)
    return resumed


def _terminate(pid: int) -> None:
    """Best-effort stop of a wedged worker. SIGTERM only — never SIGKILL from
    the supervisor: the worker's own except/finally path still wants to record
    a resumable summary, and SIGKILL would throw that away."""
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(pid), "/T"],
                           capture_output=True, check=False)
        else:
            os.kill(int(pid), 15)
    except Exception:  # noqa: BLE001
        pass


# ─── GDPR Art. 17 ──────────────────────────────────────────────────────────


def purge_user(uid: str) -> int:
    """Remove every run record whose sender matches *uid*.

    Run records carry the instruction text plus routing PII (chat_key, sender
    uid), so they are erased on the same Art. 17 request as the
    ``completion_notify`` and ``task_progress`` records. Returns the count.
    """
    d = _runs_dir()
    if not d.exists():
        return 0
    removed = 0
    for path in sorted(d.glob("*.json")):
        rec = _read(path)
        if rec is None or rec.get("sender") != uid:
            continue
        tid = str(rec.get("task_id") or "")
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".json.spawnlock").unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
        if tid:
            _cleanup_run_artifacts(tid)
    return removed
