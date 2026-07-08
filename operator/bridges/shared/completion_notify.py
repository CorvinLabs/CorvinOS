"""completion_notify.py — durable, acknowledged background-completion notifications.

THE PROBLEM this solves
-----------------------
Background work that finishes AFTER the originating turn's ``claude -p``
subprocess has already exited had no reliable way to reach the user's
messenger:

* The Claude Code SDK background-agent + ``bg_monitor`` idle-wakeup path cannot
  carry a result across the per-turn process boundary — ``claude -p`` is a
  one-shot subprocess that dies at turn end; a later ``--resume`` restores
  conversation history, not a dead process's in-flight background agent.
* The task-engine, scheduler-workflow and notification-relay paths each wrote
  their "done" envelope into an outbox directory no messenger daemon polls.

THE MECHANISM
-------------
A durable backbone with an acknowledgement (exactly-once) guarantee:

1. A producer that starts long-running work calls :func:`register` with the
   ORIGINATING channel + routing id + tenant, getting back a stable task id.
2. When the work finishes (success OR failure) it calls :func:`mark_done` with
   the result text.
3. A poller — the adapter main loop AND the ``bg_monitor`` systemd timer, both
   idempotent — calls :func:`deliver_ready`, which writes a correctly-routed
   envelope into the SHARED outbox the daemons poll and then ACKNOWLEDGES the
   record (marks it delivered) so it is sent exactly once, even with two
   concurrent pollers (per-record ``O_EXCL`` lock).

Records live in ``CORVIN_HOME/pending_notifications/<id>.json``. They carry
routing PII (chat_id, sender uid), so :func:`purge_user` honours GDPR Art. 17,
mirroring ``bg_monitor.purge_user``. Pure stdlib, no subprocess, no network —
runs identically on Linux / macOS / Windows.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

# Delivered records are kept briefly for idempotency/forensics, then pruned.
CN_DELIVERED_TTL = float(os.environ.get("CN_DELIVERED_TTL", str(24 * 3600)))
# A pending record never marked done is pruned after this — a producer that
# crashed without calling mark_done must not leak a record forever. Also caps
# how long a wedged `ready` record may linger before it is force-pruned.
CN_PENDING_MAX_AGE = float(os.environ.get("CN_PENDING_MAX_AGE", str(7 * 24 * 3600)))
# A per-record delivery lock older than this belonged to a poller that crashed
# mid-delivery; steal it so the record is not wedged forever.
CN_LOCK_STALE = float(os.environ.get("CN_LOCK_STALE", "600"))
# A pending record whose producer (the detached bg_task_worker) died WITHOUT
# calling mark_done — SIGKILL/OOM/reboot, which no except: can catch — is reaped
# into a failed completion after this grace so (a) the user is still told the
# task stopped and (b) it stops counting against the /task concurrency cap. Far
# shorter than CN_PENDING_MAX_AGE (7d) which used to leave a hard-killed worker's
# record wedged for a week, locking the user out of /task.
CN_PENDING_REAP = float(os.environ.get("CN_PENDING_REAP", str(30 * 60)))


def _pid_alive(pid: int) -> bool:
    """Best-effort, NON-DESTRUCTIVE liveness of a producer pid on THIS host.

    CRITICAL: `os.kill(pid, 0)` is NOT a probe on Windows — CPython maps it to
    TerminateProcess, so it would KILL the very worker we are checking. Use the
    platform-appropriate non-destructive check.
    """
    if not pid or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes  # noqa: PLC0415
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False  # no such process (or access denied → treat as gone)
            exit_code = ctypes.c_ulong(0)
            ok = k32.GetExitCodeProcess(h, ctypes.byref(exit_code))
            k32.CloseHandle(h)
            STILL_ACTIVE = 259
            return bool(ok) and exit_code.value == STILL_ACTIVE
        except Exception:  # noqa: BLE001 — unknown → assume alive (don't reap)
            return True
    try:
        os.kill(pid, 0)  # POSIX signal 0 = existence check, genuinely a no-op
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return True  # unknown → assume alive (conservative: don't reap)


def _host_boot_id() -> str:
    """Best-effort host boot identifier so a reused pid after a reboot is not
    mistaken for a live producer. Empty string when unavailable (Windows/mac):
    the pid check + grace still bound the lockout."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""

_STATE_PENDING = "pending"
_STATE_READY = "ready"
_STATE_DELIVERED = "delivered"

# Channels that route on chat_id vs. the whatsapp `to` (JID). Mirrors the
# per-daemon routing keys documented in the delivery map.
_CHAT_ID_CHANNELS = frozenset(
    {"discord", "telegram", "slack", "signal", "email", "teams"}
)


# ─── paths ─────────────────────────────────────────────────────────────────


def _corvin_home() -> Path:
    v = os.environ.get("CORVIN_HOME")
    if v:
        return Path(os.path.expanduser(os.path.expandvars(v)))
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import corvin_home as _ch  # type: ignore

        return _ch()
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin"


def _queue_dir() -> Path:
    d = _corvin_home() / "pending_notifications"
    return d


def _record_path(task_id: str) -> Path:
    # task_id is caller-supplied; keep the filename filesystem-safe.
    safe = "".join(c for c in str(task_id) if c.isalnum() or c in "-_")[:80]
    if not safe:
        safe = secrets.token_hex(8)
    return _queue_dir() / f"{safe}.json"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    # Records carry routing PII (sender uid, chat_id, instruction label). Lock
    # them to owner-only, matching the 0600 the /task spec file gets — otherwise
    # they land world-readable (umask-default 0644) on a shared host.
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)  # atomic on POSIX + Windows


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


# ─── producer API ──────────────────────────────────────────────────────────


def register(
    task_id: str | None = None,
    *,
    channel: str,
    chat_id: str | int | None = None,
    to: str | None = None,
    sender: str = "",
    tenant_id: str = "_default",
    label: str = "",
) -> str:
    """Register a pending completion notification and return its task id.

    Capture the originating routing context NOW, while it is still available in
    the turn, so the later (out-of-band) completion can be routed. ``chat_id``
    is required for chat_id-routed channels; ``to`` for whatsapp.
    """
    tid = str(task_id) if task_id else f"cn_{secrets.token_hex(8)}"
    rec = {
        "id": tid,
        "channel": str(channel),
        "chat_id": chat_id,
        "to": to,
        "sender": str(sender or ""),
        "tenant_id": str(tenant_id or "_default"),
        "label": str(label or ""),
        "state": _STATE_PENDING,
        "text": None,
        "ok": None,
        "created_at": time.time(),
        "ready_at": None,
        "delivered_at": None,
        # Set by the worker via claim() once it starts, so a hard-killed worker
        # can be reaped (see CN_PENDING_REAP / deliver_ready).
        "producer_pid": None,
        "producer_boot": None,
    }
    _atomic_write(_record_path(tid), rec)
    return tid


def claim(task_id: str) -> bool:
    """Stamp the calling process as the producer of *task_id* (the detached
    worker calls this at startup). Enables dead-producer reaping. No-op if the
    record is gone or already done."""
    path = _record_path(task_id)
    rec = _read(path)
    if rec is None or rec.get("state") != _STATE_PENDING:
        return False
    rec["producer_pid"] = os.getpid()
    rec["producer_boot"] = _host_boot_id()
    _atomic_write(path, rec)
    return True


def count_active(sender: str | None = None) -> int:
    """Count not-yet-delivered records (pending + ready), optionally for one
    sender. Used to cap concurrent background tasks so `/task` can't fork-bomb.
    """
    qdir = _queue_dir()
    if not qdir.exists():
        return 0
    n = 0
    for path in qdir.glob("*.json"):
        rec = _read(path)
        if rec is None or rec.get("state") == _STATE_DELIVERED:
            continue
        if sender is not None and rec.get("sender") != sender:
            continue
        n += 1
    return n


def mark_done(task_id: str, *, text: str, ok: bool = True) -> bool:
    """Mark a registered task's work as finished and ready to deliver.

    Idempotent: a second call updates the text but never resurrects an
    already-delivered record. Returns False if no such pending record exists
    (e.g. the producer never called register).
    """
    path = _record_path(task_id)
    rec = _read(path)
    if rec is None:
        return False
    if rec.get("state") == _STATE_DELIVERED:
        return False
    rec["state"] = _STATE_READY
    rec["text"] = str(text)
    rec["ok"] = bool(ok)
    rec["ready_at"] = time.time()
    _atomic_write(path, rec)
    return True


# ─── delivery (poller) API ─────────────────────────────────────────────────


def _envelope_for(rec: dict) -> dict:
    """Build the outbox envelope with the correct per-channel routing key."""
    channel = rec.get("channel") or "discord"
    label = rec.get("label") or "background task"
    ok = rec.get("ok")
    status = "✅" if ok else "⚠️"
    body = rec.get("text") or ""
    text = f"{status} {label} finished.\n\n{body}".strip()
    env: dict = {
        "msg_id": f"cn_{rec.get('id')}",
        "channel": channel,
        "text": text,
        "_completion_notify": True,
        "ts": time.time(),
    }
    # Route: chat_id for most channels; `to` (JID) for whatsapp. Stamp both when
    # available so a channel that reads either key still delivers.
    #
    # chat_id stays a STRING — never int-coerce. The daemons pass it straight to
    # their client (discord.js channels.fetch, telegram sendMessage, …), all of
    # which accept string ids. A Discord channel snowflake is 19 digits (> 2^53);
    # emitting it as a JSON number loses precision when the daemon re-parses it
    # with JSON.parse (float64) and the completion lands in the wrong/no channel.
    chat_id = rec.get("chat_id")
    if chat_id is not None and chat_id != "":
        env["chat_id"] = str(chat_id)
    to = rec.get("to")
    if to:
        env["to"] = to
    elif channel == "whatsapp" and chat_id:
        env["to"] = str(chat_id)
    if rec.get("tenant_id"):
        env["tenant_id"] = rec["tenant_id"]
    # ADR-0057 / EU AI Act Art. 50 §4 — the body is AI-generated content (the
    # engine's result), so it carries the same provenance marking + _final flag
    # every normal final reply gets. Shared build_provenance keeps the marking
    # contract identical across adapter / completion_notify / scheduler.
    from provenance import build_provenance  # type: ignore
    env["_final"] = True
    env["provenance"] = build_provenance(channel, chat_id or to or "")
    return env


def deliver_ready(outbox_dir: str | Path, *, now: float | None = None) -> int:
    """Deliver every ready notification to *outbox_dir* exactly once.

    For each ready record: acquire a per-record ``O_EXCL`` lock (so the adapter
    loop and the bg_monitor timer never double-send), write the outbox envelope,
    then mark the record delivered (the acknowledgement). Also prunes delivered
    records past ``CN_DELIVERED_TTL`` and abandoned pending records past
    ``CN_PENDING_MAX_AGE``. Fail-safe: any per-record error is logged to stderr
    and skipped; never raises. Returns the count delivered this call.
    """
    now = time.time() if now is None else now
    qdir = _queue_dir()
    if not qdir.exists():
        return 0
    outbox = Path(outbox_dir)
    try:
        outbox.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0

    delivered = 0
    for path in sorted(qdir.glob("*.json")):
        rec = _read(path)
        if rec is None:
            # Malformed/partial record — prune when clearly stale so it is not
            # re-scanned every poll forever.
            try:
                if now - path.stat().st_mtime > CN_PENDING_MAX_AGE:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        state = rec.get("state")

        # Prune terminal / abandoned records.
        if state == _STATE_DELIVERED:
            da = rec.get("delivered_at") or 0
            if now - float(da or 0) > CN_DELIVERED_TTL:
                path.unlink(missing_ok=True)
            continue
        if state == _STATE_PENDING:
            ca = rec.get("created_at") or 0
            age = now - float(ca or 0)
            if age > CN_PENDING_MAX_AGE:
                path.unlink(missing_ok=True)
                continue
            # Dead-producer reap: after a grace, if the worker that owned this
            # record is provably gone (host rebooted since claim, or the claimed
            # pid is no longer alive, or it never claimed at all), convert it to
            # a failed completion so the user is notified and the /task cap frees
            # — instead of leaving it wedged for CN_PENDING_MAX_AGE (7d).
            if age > CN_PENDING_REAP:
                pid = rec.get("producer_pid")
                boot = rec.get("producer_boot")
                # ONLY reap a record whose producer actually CLAIMED it (stamped
                # its pid). A record with pid=None is one whose producer never
                # claims — e.g. the compute worker for a legitimately long
                # (>30min) L24/L25 job. Reaping those by "no pid" produced a
                # false "worker stopped" AND dropped the real result when the job
                # later finished (mark_done found it DELIVERED). Unclaimed records
                # are left to the CN_PENDING_MAX_AGE prune instead. A claimed
                # record is reaped only when its host rebooted or its pid is dead.
                producer_gone = bool(pid) and (
                    (boot and boot != _host_boot_id())
                    or not _pid_alive(int(pid))
                )
                if producer_gone:
                    rec["state"] = _STATE_READY
                    rec["ok"] = False
                    rec["text"] = ("the background worker stopped without "
                                   "reporting a result (it was killed or the "
                                   "host restarted).")
                    rec["ready_at"] = now
                    _atomic_write(path, rec)
                    # fall through into the ready-delivery path below this poll
            continue
        if state != _STATE_READY:
            continue

        lock = path.with_suffix(".json.lock")

        # Force-prune a ready record stuck undelivered far too long (e.g. wedged
        # by a crash between outbox-write and mark-delivered) so it neither leaks
        # PII nor lingers forever.
        ra = rec.get("ready_at") or rec.get("created_at") or 0
        if now - float(ra or 0) > CN_PENDING_MAX_AGE:
            path.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
            continue

        # Recover an orphaned lock: if it is older than CN_LOCK_STALE the poller
        # that held it died mid-delivery — steal it ATOMICALLY. A plain unlink
        # here was racy: poller B (having already stat'd the stale lock) could
        # unlink poller A's FRESH lock created a microsecond earlier, so both
        # entered the critical section → double delivery. Renaming is atomic:
        # exactly one poller moves the stale file away; the other's rename fails
        # (source gone) and it falls through to the O_EXCL claim below, which is
        # the real single mutex. (at-least-once on crash-after-outbox-write.)
        try:
            if now - lock.stat().st_mtime > CN_LOCK_STALE:
                steal = str(lock) + f".steal{secrets.token_hex(4)}"
                try:
                    os.rename(str(lock), steal)
                    os.unlink(steal)
                except OSError:
                    pass  # another poller stole it first
        except OSError:
            pass

        # Claim: O_EXCL create wins the race; the loser skips this record.
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue  # another poller is delivering it
        except OSError as e:
            print(f"completion_notify: lock failed {path.name}: {e}", file=sys.stderr)
            continue
        try:
            os.close(fd)
            # Re-read under lock in case it was delivered between list and claim.
            rec = _read(path)
            if rec is None or rec.get("state") != _STATE_READY:
                continue
            env = _envelope_for(rec)
            out_file = outbox / f"cn_{rec.get('id')}_{secrets.token_hex(4)}.json"
            tmp = out_file.with_suffix(out_file.suffix + ".tmp")
            tmp.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)  # envelope carries the same routing PII
            except OSError:
                pass
            tmp.replace(out_file)
            # GDPR: a concurrent purge_user may have unlinked this record between
            # the re-read above and here; don't resurrect it with PII if so.
            if not path.exists():
                continue
            rec["state"] = _STATE_DELIVERED
            rec["delivered_at"] = now
            _atomic_write(path, rec)
            delivered += 1
        except Exception as e:  # noqa: BLE001 — per-record isolation: one poisoned
            # record (e.g. an unexpected data shape, or a provenance import error
            # in _envelope_for) must not abort the loop and starve every record
            # sorted after it. Log and skip, as the docstring promises.
            print(
                f"completion_notify: deliver failed {path.name}: {e}",
                file=sys.stderr,
            )
        finally:
            try:
                os.unlink(str(lock))
            except OSError:
                pass
    return delivered


# ─── GDPR Art. 17 ──────────────────────────────────────────────────────────


def purge_user(uid: str) -> int:
    """Remove all pending-notification records whose sender matches *uid*.

    Mirrors bg_monitor.purge_user for the Right-to-Erasure path — these records
    hold routing PII (sender uid + chat_id). Returns the number removed.
    """
    qdir = _queue_dir()
    if not qdir.exists():
        return 0
    removed = 0
    for path in qdir.glob("*.json"):
        rec = _read(path)
        if rec is not None and rec.get("sender") == uid:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
