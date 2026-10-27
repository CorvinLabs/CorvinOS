"""Layer 38 — sender-side outbox for chat messages to a peer that is offline (T-0252, ADR-2300 step 2).

A message the sender could not deliver — the peer's console was down, or the relay queued it and no
answer came back — used to end as ``unconfirmed`` / ``unreachable`` and was never tried again. The
outbox keeps it, tries again when the peer is back, and gives up honestly at a deadline.

Safety rule (why a retry can never run a message twice on a peer that has this build):

* A failure that PROVES non-delivery (``unreachable``, ``maybe_delivered`` False) is resent as soon as
  the peer looks active or the backoff elapsed.
* A failure that MAY have delivered (the answer timed out) is resent only after the peer was asked
  with the signed stage query (ADR-2242, ``task_status``) and said it has never heard of the
  ``task_id``. A peer that knows the task is left alone (the feed shows its stage); a peer that cannot
  answer a stage query (older build) is never retried — the message stays ``unconfirmed``.
* Every attempt is a FRESH envelope (new nonce, new ``issued_at``) with the SAME ``task_id``.

Scope of v1: plain chat text. No attachments, no group messages, no federation tasks.

Invariants
----------

* **Bounded.** At most ``MAX_WAITING`` waiting entries, text <= 16 KiB, deadline <= 7 days.
* **Text is kept only while waiting.** Closing an entry (any state) deletes its text from memory AND
  rewrites the file, so no line on disk carries it.
* **Never raises into the send path.** Every public function swallows storage errors.
* **Erasable.** ``erase_peer`` / ``clear`` are called by ``a2a_feed`` (GDPR Art. 17, L36).

Layout: ``<tenant>/global/a2a_feed/outbox.jsonl`` (append-only; the last line per ``task_id`` wins),
dir 0o700 / file 0o600, same directory, lock and retention as the feed.

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

OUTBOX_FILE = "outbox.jsonl"

#: how long a chat message may wait for its peer (the operator's lever is the caller's argument)
DEFAULT_DEADLINE_S = 24 * 3600
MAX_DEADLINE_S = 7 * 86400
MAX_WAITING = 200
MAX_TEXT_BYTES = 16 * 1024
#: never two attempts for one entry closer than this, however active the peer looks
MIN_ATTEMPT_GAP_S = 15.0
BACKOFF_MIN_S = 30.0
BACKOFF_MAX_S = 300.0
_MAX_FILE_BYTES = 1024 * 1024
_FINAL_KEEP_S = 7 * 86400
_ID_MAX = 128
#: the relay drops a queued copy only this long after its deliver-by (a2a_relay._DELIVER_BY_GRACE_S)
RELAY_GRACE_S = 30.0

WAITING = "waiting"
#: closed states: sent (peer answered), delivered (peer knows the task), expired, cancelled,
#: unconfirmed (cannot be resolved safely), failed (the peer refused it)
FINAL = frozenset({"sent", "delivered", "expired", "cancelled", "unconfirmed", "failed"})

_lock = threading.RLock()
_mem: dict[str, dict[str, dict[str, Any]]] = {}      # root -> task_id -> entry
_inflight: set[str] = set()                           # task ids being sent right now

_log = logging.getLogger(__name__)

Audit = Callable[[str, str, dict], None]


def _root(tenant_id: str | None = None) -> Path:
    import a2a_feed  # noqa: PLC0415 — same dir/lock/retention as the feed
    return a2a_feed.feed_dir(tenant_id)


def _send_budget_s() -> float:
    """How long one blocking send may wait (= the deliver-by horizon of the copy it queues)."""
    try:
        import remote_trigger_sender as rts  # noqa: PLC0415
        return float(max(rts._DEFAULT_TIMEOUT_S, rts._DEFAULT_TTL_S + rts._TIMEOUT_MARGIN_S))
    except Exception:  # noqa: BLE001
        return 120.0


def _clean_id(v: Any) -> str:
    return v[:_ID_MAX] if isinstance(v, str) else ""


def _persist(root: Path, entry: dict[str, Any]) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / OUTBOX_FILE
        if path.exists() and path.stat().st_size > _MAX_FILE_BYTES:
            _compact(root, path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 — never into the send path
        pass


def _compact(root: Path, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for e in _mem.get(str(root), {}).values():
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _load(root: Path) -> dict[str, dict[str, Any]]:
    """Fold the file into memory once per process."""
    rk = str(root)
    with _lock:
        if rk in _mem:
            return _mem[rk]
        table: dict[str, dict[str, Any]] = {}
        _mem[rk] = table
        try:
            lines = (root / OUTBOX_FILE).read_text(encoding="utf-8").splitlines()
        except OSError:
            return table
        cutoff = time.time() - _FINAL_KEEP_S
        for line in lines:
            try:
                r = json.loads(line)
                tid = _clean_id(r.get("task_id"))
                if not tid or not _clean_id(r.get("endpoint_id")):
                    continue
                if r.get("state") in FINAL and float(r.get("closed_at") or 0) < cutoff:
                    table.pop(tid, None)
                    continue
                table[tid] = r
            except (ValueError, TypeError):
                continue
        return table


def backoff_s(attempts: int) -> float:
    """30 s, 60 s, 120 s, 240 s, then 300 s."""
    return min(BACKOFF_MAX_S, BACKOFF_MIN_S * (2 ** max(0, int(attempts))))


def enqueue(endpoint_id: str, task_id: str, text: str, *, deadline_s: int | float | None,
            maybe_delivered: bool, tenant_id: str | None = None,
            now: float | None = None) -> dict[str, Any] | None:
    """Keep ``text`` for ``endpoint_id`` until it can be delivered. ``None`` = not kept (the
    caller reports the failure as before): bad ids, empty or oversized text, a full outbox, a
    task already known, or an unusable deadline."""
    try:
        ep, tid = _clean_id(endpoint_id), _clean_id(task_id)
        if not ep or not tid or not isinstance(text, str) or not text.strip():
            return None
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            return None
        if isinstance(deadline_s, bool) or not isinstance(deadline_s, (int, float)) or deadline_s <= 0:
            return None
        deadline = min(float(deadline_s), MAX_DEADLINE_S)
        t = time.time() if now is None else float(now)
        root = _root(tenant_id)
        with _lock:
            table = _load(root)
            if tid in table:
                return None
            if sum(1 for e in table.values() if e.get("state") == WAITING) >= MAX_WAITING:
                return None
            entry = {"task_id": tid, "endpoint_id": ep, "text": text, "state": WAITING,
                     "created_at": t, "deadline_at": t + deadline, "attempts": 0,
                     "next_at": t + BACKOFF_MIN_S, "last_attempt_at": 0.0,
                     "maybe_delivered": bool(maybe_delivered)}
            if maybe_delivered:
                # The failed attempt may have left a copy in a relay queue (alive until its
                # deliver-by + grace). Hold every retry, active peer or not, until it is gone.
                entry["stale_until"] = t + _send_budget_s() + RELAY_GRACE_S
            table[tid] = entry
            _persist(root, entry)
            return dict(entry)
    except Exception:  # noqa: BLE001
        return None


def get(task_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    try:
        with _lock:
            e = _load(_root(tenant_id)).get(_clean_id(task_id))
            return dict(e) if e is not None else None
    except Exception:  # noqa: BLE001
        return None


def waiting(tenant_id: str | None = None, endpoint_id: str | None = None) -> list[dict[str, Any]]:
    """Waiting entries, oldest first (a copy: callers cannot corrupt the table)."""
    try:
        with _lock:
            rows = [dict(e) for e in _load(_root(tenant_id)).values() if e.get("state") == WAITING
                    and (endpoint_id is None or e.get("endpoint_id") == endpoint_id)]
        return sorted(rows, key=lambda e: e.get("created_at", 0))
    except Exception:  # noqa: BLE001
        return []


def update(task_id: str, *, tenant_id: str | None = None, **fields: Any) -> dict[str, Any] | None:
    """Change fields of a WAITING entry. A closed entry is immutable."""
    try:
        root = _root(tenant_id)
        with _lock:
            e = _load(root).get(_clean_id(task_id))
            if e is None or e.get("state") != WAITING:
                return None
            e.update(fields)
            _persist(root, e)
            return dict(e)
    except Exception:  # noqa: BLE001
        return None


def close(task_id: str, state: str, *, tenant_id: str | None = None,
          now: float | None = None) -> dict[str, Any] | None:
    """Close a WAITING entry in a final ``state`` and delete its text. Returns the entry, or
    ``None`` when it was not waiting (so a race closes it once)."""
    if state not in FINAL:
        return None
    try:
        root = _root(tenant_id)
        with _lock:
            e = _load(root).get(_clean_id(task_id))
            if e is None or e.get("state") != WAITING:
                return None
            e.update(state=state, text="", closed_at=time.time() if now is None else float(now))
            # Rewrite the file: appending a text-less line would leave the original line (and the
            # withdrawn text) on disk until compaction.
            ok = False
            for _attempt in (1, 2):
                try:
                    _rewrite(root, _load(root))
                    ok = True
                    break
                except Exception:  # noqa: BLE001 — never into the send path
                    continue
            if not ok:
                # The old line, with the withdrawn text, is still on disk: say so loudly.
                _log.error("a2a_outbox: could not rewrite the outbox file; the text of closed "
                           "task %s may remain on disk until the next compaction", e.get("task_id"))
                _persist(root, e)
            return dict(e)
    except Exception:  # noqa: BLE001
        return None


def cancel(task_id: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
    """The operator withdrew a waiting message. ``None`` when it was not waiting."""
    with _lock:
        if _clean_id(task_id) in _inflight:
            return None          # an attempt is on the wire right now: let it finish first
        return close(task_id, "cancelled", tenant_id=tenant_id)


def erase_peer(endpoint_id: str, tenant_id: str | None = None) -> int:
    """GDPR Art. 17: drop every entry filed under ``endpoint_id``. Returns the number removed."""
    try:
        ep = _clean_id(endpoint_id)
        if not ep:
            return 0
        root = _root(tenant_id)
        with _lock:
            table = _load(root)
            gone = [t for t, e in table.items() if e.get("endpoint_id") == ep]
            for t in gone:
                del table[t]
            if gone:
                _rewrite(root, table)
            return len(gone)
    except Exception:  # noqa: BLE001
        return 0


def clear(tenant_id: str | None = None) -> int:
    try:
        root = _root(tenant_id)
        with _lock:
            n = len(_load(root))
            _mem[str(root)] = {}
            try:
                (root / OUTBOX_FILE).unlink()
            except FileNotFoundError:
                pass
            return n
    except Exception:  # noqa: BLE001
        return 0


def _rewrite(root: Path, table: dict[str, dict[str, Any]]) -> None:
    path = root / OUTBOX_FILE
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for e in table.values():
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _reset_for_tests() -> None:
    with _lock:
        _mem.clear()
        _inflight.clear()


# ── the drain: one pass over the waiting entries ────────────────────────────

def _feed_row(entry: dict[str, Any], status: str, error: str | None) -> None:
    """The feed row that ends a waiting message (best-effort, same shape ``send()`` writes)."""
    try:
        import a2a_feed  # noqa: PLC0415
        a2a_feed.record(direction="in", kind="response", peer_id=entry["endpoint_id"],
                        task_id=entry["task_id"], status=status, error=error)
    except Exception:  # noqa: BLE001
        pass


def waiting_view(endpoint_id: str | None = None, tenant_id: str | None = None) -> dict[str, dict[str, Any]]:
    """What the UI may know about waiting messages: ``{task_id: {deadline_at, attempts}}``.

    Never the text. This is how the feed answers "this message is waiting for its peer" — derived
    from the outbox itself, not from a fake reply row (a reply row renders as a message FROM the peer).
    """
    return {e["task_id"]: {"deadline_at": int(e.get("deadline_at") or 0),
                           "attempts": int(e.get("attempts") or 0)}
            for e in waiting(tenant_id, endpoint_id)}


def _emit(audit: Audit | None, event: str, entry: dict[str, Any], **extra: Any) -> None:
    if audit is None:
        return
    try:
        audit(event, "INFO", {"endpoint_id": entry["endpoint_id"], "task_id": entry["task_id"], **extra})
    except Exception:  # noqa: BLE001
        pass


def _close_and_report(entry: dict[str, Any], state: str, audit: Audit | None, tenant_id: str | None,
                      *, feed_status: str | None, feed_error: str | None, now: float) -> None:
    closed = close(entry["task_id"], state, tenant_id=tenant_id, now=now)
    if closed is None:
        return
    if feed_status is not None:
        _feed_row(entry, feed_status, feed_error)
    _emit(audit, "A2A.outbox_closed", entry, state=state, attempts=int(entry.get("attempts") or 0))


def drain(sender: Any, *, audit: Audit | None = None,
          is_active: Callable[[str], bool] | None = None,
          tenant_id: str | None = None, now: float | None = None) -> dict[str, int]:
    """One pass over the waiting entries. ``sender`` needs ``send(...)`` and ``task_status(...)``
    (a ``RemoteTriggerSender``). ``is_active(endpoint_id)`` says whether the connectivity
    manager currently sees the peer as active. Returns counters, for logs and tests."""
    t = time.time() if now is None else float(now)
    counts = {"checked": 0, "expired": 0, "resent": 0, "sent": 0, "delivered": 0,
              "kept": 0, "unconfirmed": 0, "failed": 0}
    for snap in waiting(tenant_id):
        tid = snap["task_id"]
        with _lock:
            # The snapshot may be stale: an earlier blocking send gave the operator time to cancel
            # or erase this entry. Re-read it and send only what is STILL waiting, with its
            # current text.
            e = get(tid, tenant_id)
            if e is None or e.get("state") != WAITING or tid in _inflight:
                continue
            _inflight.add(tid)
        try:
            counts["checked"] += 1
            _drain_one(sender, e, audit, is_active, tenant_id, t, counts)
        except Exception:  # noqa: BLE001 — one entry must not stop the pass
            counts["kept"] += 1
        finally:
            with _lock:
                _inflight.discard(tid)
    return counts


def _drain_one(sender: Any, e: dict[str, Any], audit: Audit | None,
               is_active: Callable[[str], bool] | None, tenant_id: str | None, now: float,
               counts: dict[str, int]) -> None:
    tid, ep = e["task_id"], e["endpoint_id"]
    if now > float(e.get("deadline_at") or 0):
        _close_and_report(e, "expired", audit, tenant_id, now=now, feed_status="error",
                          feed_error="Not delivered — the peer stayed offline until the deadline.")
        counts["expired"] += 1
        return
    if now - float(e.get("last_attempt_at") or 0) < MIN_ATTEMPT_GAP_S:
        counts["kept"] += 1
        return
    if now < float(e.get("stale_until") or 0):
        # The previous copy may still sit in a relay queue and run on the peer until its
        # deliver-by (+ grace) passes: a retry before then could run the task twice.
        counts["kept"] += 1
        return
    active = False
    if is_active is not None:
        try:
            active = bool(is_active(ep))
        except Exception:  # noqa: BLE001
            active = False
    if not active and now < float(e.get("next_at") or 0):
        counts["kept"] += 1
        return

    attempts = int(e.get("attempts") or 0)
    if e.get("maybe_delivered"):
        st = sender.task_status(ep, tid)
        if not st.get("reachable"):
            update(tid, tenant_id=tenant_id, last_attempt_at=now, next_at=now + backoff_s(attempts))
            counts["kept"] += 1
            return
        if not st.get("supported"):
            # The peer answers but cannot say whether it has the task: resending could run it twice.
            _close_and_report(e, "unconfirmed", audit, tenant_id, now=now, feed_status="unconfirmed",
                              feed_error=None)
            counts["unconfirmed"] += 1
            return
        if st.get("stage") != "unknown":
            try:
                import a2a_feed  # noqa: PLC0415
                a2a_feed.observe_stage(peer_id=ep, task_id=tid, stage=str(st["stage"]),
                                       stage_seq=int(st.get("stage_seq") or 0),
                                       reason=str(st.get("reason") or ""))
            except Exception:  # noqa: BLE001
                pass
            _close_and_report(e, "delivered", audit, tenant_id, now=now, feed_status="unconfirmed",
                              feed_error=None)
            counts["delivered"] += 1
            return
        # supported and "unknown": the peer has never heard of this task_id -> safe to resend

    # Crash-safe: mark the entry "may have delivered" BEFORE the attempt, so a process that dies
    # mid-send is treated as unconfirmed on the next pass, never as provably undelivered.
    # The row is re-read under the lock by update(): a cancel/erase that landed since the
    # snapshot makes it return None and nothing is sent. The text sent is the one read here.
    # The relay copy lives from the REAL send start (earlier entries of this pass may have blocked
    # for minutes), so every timestamp below is taken from a fresh clock, not the pass-start `now`.
    started = time.time()
    fresh = update(tid, tenant_id=tenant_id, last_attempt_at=started, attempts=attempts + 1,
                   maybe_delivered=True, stale_until=started + _send_budget_s() + RELAY_GRACE_S)
    if fresh is None:
        counts["kept"] += 1
        return
    _emit(audit, "A2A.outbox_retried", e, attempts=attempts + 1)
    counts["resent"] += 1
    res = sender.send(ep, fresh.get("text") or "", task_id=tid, feed_task_recorded=True, outbox_retry=True)
    done = time.time()                        # when the blocking send RETURNED
    stale_until = started + max(done - started, _send_budget_s()) + RELAY_GRACE_S

    if getattr(res, "ok", False):
        _close_and_report(e, "sent", audit, tenant_id, now=done, feed_status=None, feed_error=None)
        counts["sent"] += 1
        return
    cat = getattr(res, "error_category", "")
    provably_not = (cat == "unreachable") and not getattr(res, "maybe_delivered", False)
    if provably_not or cat == "timeout_transport" or getattr(res, "maybe_delivered", False):
        # A provable miss left no copy anywhere; anything else may sit queued at the relay.
        hold = 0.0 if provably_not else stale_until
        update(tid, tenant_id=tenant_id, maybe_delivered=not provably_not, last_attempt_at=done,
               stale_until=hold, next_at=max(done + backoff_s(attempts + 1), hold))
        counts["kept"] += 1
        return
    # The peer answered and refused (rejected, auth, filtered ...): send() already wrote that row.
    _close_and_report(e, "failed", audit, tenant_id, now=done, feed_status=None, feed_error=None)
    counts["failed"] += 1


__all__ = [
    "DEFAULT_DEADLINE_S", "enqueue", "get", "waiting", "update", "close", "cancel", "erase_peer",
    "clear", "drain", "backoff_s", "waiting_view", "WAITING", "FINAL",
]
