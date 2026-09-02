"""task_progress.py — durable, rate-limited INTERMEDIATE notifications for a
long-running background task.

THE PROBLEM this solves
-----------------------
``completion_notify`` is a one-shot backbone: one record, one delivery, at the
END of the work. An autonomous orchestration run that takes hours therefore
produced NOTHING until it finished — and if it never finished, nothing at all.
The messenger-origin ``/task`` producer even passes ``on_status=None``
explicitly ("no live progress spam"), so no intermediate signal existed.

The other two notification abstractions in the tree
(``core.vibe_engineering.notification_router`` and ``core.notifications.bus``)
could not fill the gap: the first posts to ``DISCORD_WEBHOOK_URL``, which
nothing in this repo ever sets, and the second has no subscriber. Neither is
connected to a daemon that can actually reach Discord.

THE MECHANISM
-------------
The SAME durable backbone the completion path uses — the shared outbox the
messenger daemons poll — but for the middle of the run instead of its end:

1. A producer (``bg_task_worker``, the supervisor, an orchestrator phase)
   calls :func:`emit` with the task id and a short status line. Routing is
   inherited from the task's ``completion_notify`` record — captured at
   ``/task`` time, so there is exactly ONE routing store, never two.
2. A poller — the adapter main loop AND the ``bg_monitor`` systemd timer, both
   idempotent — calls :func:`deliver_progress`, which writes a normal outbox
   envelope (unique ``msg_id``, NOT ``_progress``) and acknowledges the record
   under a per-record ``O_EXCL`` lock, so it is sent exactly once.

Why a NORMAL envelope and not the daemon's ``_progress`` sticky: a sticky
message is edited in place, dropped once the turn is finalized, and DELETED
when the next real reply lands (see ``discord/daemon.js`` sendDiscord). That is
right for tool-call chatter inside one turn and wrong for an out-of-band run
whose updates must survive every intervening turn.

RATE LIMITING is a first-class concern, not an afterthought: a wedged loop that
emits every 100 ms would rate-limit the bot at Discord's edge and bury the
user. Two independent bounds apply, both per task:

* ``TP_MIN_INTERVAL`` (default 120 s) — an emit inside the window COALESCES
  into the still-undelivered record instead of queueing a second one, so the
  user always sees the LATEST state and never a backlog of stale ones.
* ``TP_MAX_UPDATES`` (default 40) — a hard ceiling on delivered updates per
  task. Past it, emits are recorded in the counter and dropped silently; the
  final completion still arrives through ``completion_notify``.

Records carry routing PII (chat_id, sender uid), so :func:`purge_user` honours
GDPR Art. 17, mirroring ``completion_notify.purge_user`` and
``bg_monitor.purge_user``. Pure stdlib, no subprocess, no network — runs
identically on Linux / macOS / Windows.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Minimum seconds between two DELIVERED updates for one task. An emit inside
# the window coalesces into the pending record rather than adding a second.
TP_MIN_INTERVAL = float(os.environ.get("TP_MIN_INTERVAL", "120"))
# Hard ceiling on delivered updates per task, so a wedged producer cannot
# flood the channel (and burn the bot's invalid-request/rate budget).
TP_MAX_UPDATES = int(os.environ.get("TP_MAX_UPDATES", "40"))
# Delivered records are kept briefly for idempotency/forensics, then pruned.
TP_DELIVERED_TTL = float(os.environ.get("TP_DELIVERED_TTL", str(6 * 3600)))
# A queued update never delivered (no poller ran, routing unresolvable) is
# pruned after this so the queue cannot grow without bound.
TP_MAX_AGE = float(os.environ.get("TP_MAX_AGE", str(24 * 3600)))
# A per-record delivery lock older than this belonged to a poller that crashed
# mid-delivery; steal it so the record is not wedged forever.
TP_LOCK_STALE = float(os.environ.get("TP_LOCK_STALE", "600"))
# An update older than this is stale news by the time a poller sees it — ship
# the newest one for the task and drop the rest, rather than replaying a
# backlog of "iteration 3 of 40" lines an hour late.
TP_STALE_UPDATE = float(os.environ.get("TP_STALE_UPDATE", "1800"))

_STATE_QUEUED = "queued"
_STATE_DELIVERED = "delivered"

# Visual prefix per update kind. Kept here (not at the call sites) so the
# vocabulary of an autonomous run stays consistent across every producer.
_KIND_PREFIX = {
    "progress": "⏳",
    "phase": "▶️",
    "resume": "🔁",
    "stall": "⏱️",
    "warning": "⚠️",
    "error": "❌",
}


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


def _queue_dir() -> Path:
    return _corvin_home() / "task_progress"


def _record_path(update_id: str) -> Path:
    safe = "".join(c for c in str(update_id) if c.isalnum() or c in "-_")
    return _queue_dir() / f"{safe}.json"


def _counter_path(task_id: str) -> Path:
    safe = "".join(c for c in str(task_id) if c.isalnum() or c in "-_")
    return _queue_dir() / f"_ctr_{safe}.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write *data* atomically with 0600 perms (records carry routing PII)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{secrets.token_hex(4)}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
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


# ─── routing ───────────────────────────────────────────────────────────────


def _routing_for(task_id: str) -> dict | None:
    """Resolve the originating channel/chat for *task_id*.

    Read from the task's ``completion_notify`` record, which captured the
    origin at ``/task`` time. Keeping ONE routing store means a progress
    update can never be routed differently from its own completion.
    """
    try:
        import completion_notify as _cn  # type: ignore

        rec = _cn._read(_cn._record_path(task_id))  # noqa: SLF001 — sibling module
    except Exception as e:  # noqa: BLE001
        print(f"[task_progress] ERROR: _routing_for({task_id}) import/read failed: {e}",
              file=sys.stderr)
        return None
    if not rec:
        print(f"[task_progress] WARNING: _routing_for({task_id}) no completion_notify record found "
              f"— task was not registered for notifications",
              file=sys.stderr)
        return None
    return {
        "channel": rec.get("channel") or "discord",
        "chat_id": rec.get("chat_id"),
        "to": rec.get("to"),
        "sender": rec.get("sender") or "",
        "tenant_id": rec.get("tenant_id") or "_default",
        "label": rec.get("label") or "",
    }


# ─── counters (per-task rate state) ────────────────────────────────────────


def _load_counter(task_id: str) -> dict:
    rec = _read(_counter_path(task_id))
    if not isinstance(rec, dict):
        return {"task_id": task_id, "delivered": 0, "last_emit": 0.0,
                "last_delivered": 0.0, "pending_id": None, "dropped": 0}
    rec.setdefault("delivered", 0)
    rec.setdefault("last_emit", 0.0)
    rec.setdefault("last_delivered", 0.0)
    rec.setdefault("pending_id", None)
    rec.setdefault("dropped", 0)
    return rec


def _save_counter(task_id: str, ctr: dict) -> None:
    try:
        _atomic_write(_counter_path(task_id), ctr)
    except OSError:
        pass  # best-effort: a lost counter degrades to more updates, never fewer


# ─── producer API ──────────────────────────────────────────────────────────


def emit(task_id: str, text: str, *, kind: str = "progress",
         now: float | None = None, force: bool = False) -> str | None:
    """Queue an intermediate update for *task_id*.

    Returns the update id that will be delivered (a NEW id, or the id of the
    pending record this call coalesced into), or ``None`` when the update was
    dropped by the rate ceiling / because the task has no routing.

    ``force=True`` bypasses ``TP_MIN_INTERVAL`` coalescing (used for
    state-change updates such as a heal/resume, which must not be swallowed by
    a routine progress line) but still respects ``TP_MAX_UPDATES``.

    Never raises: a producer's status line must never be able to kill the work
    it is reporting on.
    """
    now = time.time() if now is None else now
    text = (text or "").strip()
    if not task_id or not text:
        return None
    try:
        routing = _routing_for(task_id)
        if routing is None:
            # No completion record → nowhere to send. Not an error: the task
            # may be a console/CLI run with no messenger origin.
            return None

        ctr = _load_counter(task_id)
        if ctr["delivered"] >= TP_MAX_UPDATES:
            ctr["dropped"] = int(ctr.get("dropped", 0)) + 1
            _save_counter(task_id, ctr)
            return None

        prefix = _KIND_PREFIX.get(kind, _KIND_PREFIX["progress"])
        body = f"{prefix} {text}"

        # Coalesce into the still-undelivered record when inside the window.
        pending_id = ctr.get("pending_id")
        if pending_id:
            ppath = _record_path(pending_id)
            prec = _read(ppath)
            if prec is not None and prec.get("state") == _STATE_QUEUED:
                within_window = (now - float(ctr.get("last_delivered") or 0)
                                 < TP_MIN_INTERVAL)
                if within_window and not force:
                    prec["text"] = body
                    prec["kind"] = kind
                    prec["updated_at"] = now
                    prec["coalesced"] = int(prec.get("coalesced", 0)) + 1
                    # `force` is STICKY across coalescing: once a state-change
                    # update has been folded into this record it must keep
                    # bypassing the delivery interval, or the resume notice it
                    # carries gets throttled like routine progress.
                    prec["force"] = bool(prec.get("force")) or force
                    _atomic_write(ppath, prec)
                    ctr["last_emit"] = now
                    _save_counter(task_id, ctr)
                    return pending_id
                # Outside the window (or forced) but the previous record is
                # still queued — overwrite it too rather than stacking a
                # second one: a poller that has not run yet must not suddenly
                # deliver two updates back to back.
                prec["text"] = body
                prec["kind"] = kind
                prec["updated_at"] = now
                prec["coalesced"] = int(prec.get("coalesced", 0)) + 1
                prec["force"] = bool(prec.get("force")) or force  # sticky, as above
                _atomic_write(ppath, prec)
                ctr["last_emit"] = now
                _save_counter(task_id, ctr)
                return pending_id
            # Stale pointer (record delivered or pruned) — fall through.

        if not force and (now - float(ctr.get("last_delivered") or 0)
                          < TP_MIN_INTERVAL):
            # Inside the window with nothing pending to coalesce into: queue it
            # anyway. The poller enforces the interval at DELIVERY time, which
            # is the bound that actually matters, and a queued record gives the
            # next emit something to coalesce into.
            pass

        uid = f"tp_{secrets.token_hex(8)}"
        rec = {
            "id": uid,
            "task_id": str(task_id),
            "kind": str(kind),
            "text": body,
            "state": _STATE_QUEUED,
            "created_at": now,
            "updated_at": now,
            "delivered_at": None,
            "coalesced": 0,
            "force": bool(force),
            **routing,
        }
        _atomic_write(_record_path(uid), rec)
        ctr["pending_id"] = uid
        ctr["last_emit"] = now
        _save_counter(task_id, ctr)
        return uid
    except Exception as e:  # noqa: BLE001 — a status line must never kill the work
        print(f"task_progress: emit failed for {task_id}: {e}", file=sys.stderr)
        return None


def finish(task_id: str) -> None:
    """Drop the per-task rate state once the task is done.

    Any still-queued update is left for the poller: the last thing the user
    saw before the completion should be the last thing that happened.
    """
    try:
        _counter_path(task_id).unlink(missing_ok=True)
    except OSError:
        pass


def pending_count(task_id: str | None = None) -> int:
    """Count queued (not yet delivered) updates, optionally for one task."""
    qdir = _queue_dir()
    if not qdir.exists():
        return 0
    n = 0
    for path in qdir.glob("tp_*.json"):
        rec = _read(path)
        if rec is None or rec.get("state") != _STATE_QUEUED:
            continue
        if task_id is not None and rec.get("task_id") != task_id:
            continue
        n += 1
    return n


# ─── delivery (poller) API ─────────────────────────────────────────────────


def _envelope_for(rec: dict) -> dict:
    """Build the outbox envelope for one progress update.

    A NORMAL envelope with a unique ``msg_id`` — deliberately not the daemon's
    ``_progress`` sticky, which is edited in place and deleted by the next real
    reply (see the module docstring).
    """
    channel = rec.get("channel") or "discord"
    label = rec.get("label") or ""
    body = rec.get("text") or ""
    text = f"{body}\n_{label}_".strip() if label else body
    env: dict = {
        "msg_id": f"tp_{rec.get('id')}",
        "channel": channel,
        "text": text,
        "_task_progress": True,
        "ts": time.time(),
    }
    # chat_id stays a STRING — never int-coerce. A Discord channel snowflake is
    # 19 digits (> 2^53) and loses precision as a JSON number when the daemon
    # re-parses it with JSON.parse (float64). Same contract as
    # completion_notify._envelope_for.
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
    # ADR-0057 / EU AI Act Art. 50 §4 — an autonomous run's status line is
    # machine-generated content delivered to a human, so it carries the same
    # provenance marking every other outbound envelope gets. `_final` is NOT
    # set: this is an intermediate update, the completion is still to come.
    try:
        from provenance import build_provenance  # type: ignore

        env["provenance"] = build_provenance(channel, chat_id or to or "")
    except Exception:  # noqa: BLE001 — marking is stamped where available; a
        # missing sibling module must not silently drop the update.
        pass
    return env


def _proactive_flag_on(tenant_id: str) -> bool:
    """Resolve the ship-dark ``proactive_communication`` flag for ``tenant_id``.

    OFF (default / unresolved) → the migrated delivery path uses the
    pre-migration DIRECT outbox write (byte-identical, ship-dark). ON → the
    progress update routes through the governed gate. Never raises."""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import proactive as _p  # type: ignore
        return bool(_p._flag_on(tenant_id or "_default"))
    except Exception:  # noqa: BLE001 — primitive/console absent → OFF (direct write)
        return False


def _write_outbox_direct(env: dict, outbox: "str | Path",
                         out_file_name: str) -> bool:
    """Byte-identical pre-migration DIRECT outbox write (atomic tmp-replace, 0600).

    Used for the ship-dark default (flag OFF) AND as the DENIED/unavailable
    fallback under flag ON — a SOLICITED progress update belonging to an explicit
    ``/task`` run must not be silently lost to a house-rules false-positive or a
    broken gate. Never raises."""
    try:
        out_file = Path(outbox) / out_file_name
        tmp = out_file.with_suffix(out_file.suffix + ".tmp")
        tmp.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)  # envelope carries routing PII
        except OSError:
            pass
        tmp.replace(out_file)
        return True
    except Exception as ex:  # noqa: BLE001 — never raise
        print(f"task_progress: direct outbox write failed {out_file_name}: {ex}",
              file=sys.stderr)
        return False


def _emit_via_proactive(env: dict, rec: dict, *, outbox: "str | Path",
                        out_file_name: str) -> str:
    """Route ONE queued progress envelope through the proactive gate (ADR-0554
    Phase 2 / ADR-0553 amendment). Only reached when the ``proactive_communication``
    flag is ON for the record's tenant.

    ``solicited=True``: an intermediate update belongs to an explicit ``/task``
    run, so the flag / consent / disclosure gates are SKIPPED — House-rules +
    rate/flood + the content-free ``proactive.emitted`` audit STILL apply. The
    pre-built ``env`` (``_task_progress`` marker, provenance, ``tp_`` filename) is
    written verbatim; progress carries no voice.

    Returns a status string: ``"emitted"`` (envelope written), ``"rate_limited"``
    (transient — leave QUEUED, retry next poll), or ``"denied"`` (house-rules
    fail-closed / false-positive / primitive unavailable — the caller MUST fall
    back to a direct write so the update is not lost). Never raises.
    """
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import proactive as _p  # type: ignore
    except Exception as e:  # noqa: BLE001 — primitive missing → deny → direct fallback
        print(f"task_progress: proactive gate unavailable {out_file_name}: {e}",
              file=sys.stderr)
        return "denied"
    try:
        res = _p.emit_proactive(
            channel=env.get("channel") or rec.get("channel") or "discord",
            chat_id=rec.get("chat_id"), to=rec.get("to"),
            tenant_id=rec.get("tenant_id") or "_default",
            uid=str(rec.get("sender") or ""),
            text=env.get("text") or "",
            kind="progress", solicited=True,
            envelope=env, out_file_name=out_file_name, outbox_dir=outbox,
        )
        if res == _p.EmitResult.EMITTED:
            return "emitted"
        if res == _p.EmitResult.RATE_LIMITED:
            return "rate_limited"
        return "denied"
    except Exception as e:  # noqa: BLE001 — emit_proactive never raises; belt + braces
        print(f"task_progress: proactive emit failed {out_file_name}: {e}",
              file=sys.stderr)
        return "denied"


def deliver_progress(outbox_dir: str | Path, *, now: float | None = None) -> int:
    """Deliver queued progress updates to *outbox_dir*, rate-limited.

    At most ONE update per task per ``TP_MIN_INTERVAL`` reaches the outbox; the
    newest queued update for a task wins and any older ones for the same task
    are dropped as stale news. Exactly-once per record via the same per-record
    ``O_EXCL`` lock the completion path uses, so the adapter loop and the
    bg_monitor timer never double-send. Fail-safe: any per-record error is
    logged to stderr and skipped; never raises. Returns the count delivered.
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

    # Pass 1: collect the newest queued update per task, prune the rest.
    newest: dict[str, tuple[float, Path, dict]] = {}
    for path in sorted(qdir.glob("tp_*.json")):
        rec = _read(path)
        if rec is None:
            try:
                if now - path.stat().st_mtime > TP_MAX_AGE:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        state = rec.get("state")
        if state == _STATE_DELIVERED:
            da = float(rec.get("delivered_at") or 0)
            if now - da > TP_DELIVERED_TTL:
                path.unlink(missing_ok=True)
                path.with_suffix(".json.lock").unlink(missing_ok=True)
            continue
        if state != _STATE_QUEUED:
            continue
        created = float(rec.get("created_at") or 0)
        if now - created > TP_MAX_AGE:
            path.unlink(missing_ok=True)
            continue
        tid = str(rec.get("task_id") or "")
        updated = float(rec.get("updated_at") or created)
        prev = newest.get(tid)
        if prev is None or updated > prev[0]:
            if prev is not None:
                # Older duplicate for the same task — stale news, drop it.
                try:
                    prev[1].unlink(missing_ok=True)
                except OSError:
                    pass
            newest[tid] = (updated, path, rec)
        else:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # Prune counters whose task has no queued update and whose completion
    # record is gone: nothing will ever call finish() for them, so without this
    # they accumulate in the queue dir forever.
    for cpath in qdir.glob("_ctr_*.json"):
        try:
            if cpath.stem[len("_ctr_"):] in newest:
                continue
            if now - cpath.stat().st_mtime > TP_MAX_AGE:
                cpath.unlink(missing_ok=True)
        except OSError:
            pass

    delivered = 0
    for tid, (updated, path, rec) in sorted(newest.items()):
        try:
            # An update that sat in the queue far longer than a poll cycle is
            # stale news; drop it rather than replaying old status.
            if now - updated > TP_STALE_UPDATE:
                path.unlink(missing_ok=True)
                continue

            ctr = _load_counter(tid)
            if ctr["delivered"] >= TP_MAX_UPDATES:
                path.unlink(missing_ok=True)
                continue
            # THE rate bound that matters: at most one delivery per task per
            # window, regardless of how many times the producer emitted.
            if (not rec.get("force")) and (
                now - float(ctr.get("last_delivered") or 0) < TP_MIN_INTERVAL
            ):
                continue
            if not rec.get("chat_id") and not rec.get("to"):
                # Unroutable — never becomes deliverable; drop rather than
                # re-scan it every poll forever.
                path.unlink(missing_ok=True)
                continue

            lock = path.with_suffix(".json.lock")
            # Steal an orphaned lock ATOMICALLY (rename, not unlink): a plain
            # unlink is racy — poller B, having already stat'd the stale lock,
            # could remove poller A's FRESH lock and both would enter the
            # critical section. Same reasoning as completion_notify.
            try:
                if now - lock.stat().st_mtime > TP_LOCK_STALE:
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
                continue  # another poller is delivering it
            except OSError as e:
                print(f"task_progress: lock failed {path.name}: {e}",
                      file=sys.stderr)
                continue
            try:
                os.close(fd)
                # Re-read under lock in case it changed between scan and claim.
                rec = _read(path)
                if rec is None or rec.get("state") != _STATE_QUEUED:
                    continue
                # ADR-0554 Phase 2 — SHIP-DARK (ADR-0553 amendment): build the
                # envelope here (preserving the exact _task_progress shape). The
                # default (proactive_communication OFF) writes it DIRECTLY
                # (byte-identical, no silent loss). Only when the operator turned
                # the flag ON does the update route through the governed gate; a
                # DENIED / unavailable outcome then falls back to a direct write
                # (a solicited update must not be dropped by a house-rules
                # false-positive), while a transient RATE_LIMITED leaves the
                # record QUEUED to retry next poll.
                env = _envelope_for(rec)
                out_name = f"tp_{rec.get('id')}_{secrets.token_hex(4)}.json"
                if _proactive_flag_on(rec.get("tenant_id") or "_default"):
                    status = _emit_via_proactive(env, rec, outbox=outbox,
                                                 out_file_name=out_name)
                    if status == "rate_limited":
                        # Leave QUEUED; next poll retries. NOT marked delivered /
                        # counted, so exactly-once (O_EXCL) is preserved.
                        continue
                    if status != "emitted":
                        # DENIED / unavailable → deliver directly (never lose it).
                        _write_outbox_direct(env, outbox, out_name)
                else:
                    _write_outbox_direct(env, outbox, out_name)
                # GDPR: a concurrent purge_user may have unlinked this record
                # between the re-read and here; don't resurrect it with PII.
                if not path.exists():
                    continue
                rec["state"] = _STATE_DELIVERED
                rec["delivered_at"] = now
                _atomic_write(path, rec)
                ctr = _load_counter(tid)
                ctr["delivered"] = int(ctr.get("delivered", 0)) + 1
                ctr["last_delivered"] = now
                ctr["pending_id"] = None
                _save_counter(tid, ctr)
                delivered += 1
            finally:
                try:
                    os.unlink(str(lock))
                except OSError:
                    pass
        except Exception as e:  # noqa: BLE001 — per-record isolation: one
            # poisoned record must not starve every task sorted after it.
            print(f"task_progress: deliver failed {path.name}: {e}",
                  file=sys.stderr)
    return delivered


# ─── GDPR Art. 17 ──────────────────────────────────────────────────────────


def purge_user(uid: str) -> int:
    """Remove every progress record whose sender matches *uid*.

    Called by the GDPR Art. 17 erasure path, mirroring
    ``completion_notify.purge_user`` and ``bg_monitor.purge_user``: these
    records carry the same routing PII (chat_id, sender uid) and must be
    erased on the same request. Returns the number of records removed.
    """
    qdir = _queue_dir()
    if not qdir.exists():
        return 0
    removed = 0
    victim_tasks: set[str] = set()
    for path in sorted(qdir.glob("tp_*.json")):
        rec = _read(path)
        if rec is None or rec.get("sender") != uid:
            continue
        victim_tasks.add(str(rec.get("task_id") or ""))
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".json.lock").unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    # The per-task counters are keyed by task id, which is itself a routing
    # correlate of the erased subject — drop them too.
    for tid in victim_tasks:
        if tid:
            try:
                _counter_path(tid).unlink(missing_ok=True)
            except OSError:
                pass
    return removed
