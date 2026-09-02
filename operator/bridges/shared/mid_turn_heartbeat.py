"""Mid-turn background-task heartbeat + status (ADR-0551 C1, variant B — ship-dark).

Closes the "is it still working / what is it doing?" gap for a sub-agent the
assistant spawns MID-TURN (which never engages the durable `/task` backbone).
Completion still comes from the agent's own reply (variant B — no double-ping);
this module adds the missing *intermediate* signal, in TWO shapes the operator
asked for:

  * ⏱️ a slim **liveness** line on a slow cadence ("läuft seit N min"), and
  * 🔧 a **status** line whenever the current step changes ("Phase 2/4: E2E",
    "Iteration 3/5", "committe…") — a short "what is happening", not just "that".

Signal source (deterministic, no host-internal coupling): the assistant emits
markers in its replies; the adapter parses+strips them (so they never reach the
channel):

  * ``⟦bgtask:<label>⟧``            — start tracking a task
  * ``⟦bgstep:<label>|<status>⟧``   — update its current step → emits a status line
  * ``⟦bgdone:<label>⟧``            — stop tracking it

A task auto-expires after ``MAX_AGE_S`` as a backstop so nothing can ping forever.

Ship-dark behind ``bridge_mid_turn_task_notify`` (default OFF); the adapter gates
every call. Delivery reuses the normal outbox envelope. Best-effort throughout:
a broken heartbeat must never break a turn or the loop.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

# Serialises the read-modify-write sections of the store (mark_active /
# update_status / clear_task / deliver_due) within a single process, so two
# threads in the long-running adapter can't interleave a load()→mutate→write
# against the same marker and lose an update. Cross-PROCESS safety still rests
# on the atomic tmp-file replace in `_atomic_write` (unique tmp per writer).
_lock = threading.RLock()

# Liveness cadence (human-friendly — a courtesy, not a firehose).
FIRST_AFTER_S = 90      # no liveness ping until a task has run this long
INTERVAL_S = 180        # min gap between liveness pings (3 min)
MAX_PINGS = 8           # liveness pings, then a final note and stop
MAX_AGE_S = 3600        # 1h hard cap; expire the marker past this
STATUS_CAP = 120        # max chars of a status line

_MARKER_RE = re.compile(r"⟦bgtask:([^⟧]{1,80})⟧")
_STEP_RE = re.compile(r"⟦bgstep:([^|⟧]{1,80})\|([^⟧]{1,120})⟧")
_DONE_RE = re.compile(r"⟦bgdone:([^⟧]{1,80})⟧")
# Strips COMPLETE, PARTIAL, empty AND oversized markers so nothing leaks into
# the channel. The closing ⟧ is optional and the label is unbounded up to the
# next ⟧/newline — a marker split across chunks (no ⟧ yet) or one whose label
# blew past the 80/120 parse caps is removed all the same.
_ANY_RE = re.compile(r"⟦bg(?:task|step|done):[^⟧\n]*⟧?")
# In the streaming live-scan we can advance the scan index past everything but
# the last ~this-many chars — only a marker split across the final chunk could
# still be incomplete, and no complete marker is longer than this (prefix
# `⟦bgstep:` + 80 label + `|` + 120 status + `⟧`). Keeps scan_new_steps O(n).
_MAX_MARKER_LEN = 210


# ── Marker parsing / stripping (used by the adapter reply hook) ──────────────

def parse_markers(reply_text: str) -> "list[str]":
    """Start markers: labels of ``⟦bgtask:<label>⟧``."""
    if not isinstance(reply_text, str) or not reply_text:
        return []
    out, seen = [], set()
    for m in _MARKER_RE.findall(reply_text):
        lbl = m.strip()
        if lbl and lbl not in seen:
            seen.add(lbl)
            out.append(lbl)
    return out


def parse_steps(reply_text: str) -> "list[tuple[str, str]]":
    """Status updates: ``(label, status)`` from ``⟦bgstep:<label>|<status>⟧``."""
    if not isinstance(reply_text, str) or not reply_text:
        return []
    out = []
    for lbl, status in _STEP_RE.findall(reply_text):
        lbl, status = lbl.strip(), status.strip()[:STATUS_CAP]
        if lbl and status:
            out.append((lbl, status))
    return out


def parse_done(reply_text: str) -> "list[str]":
    """Stop markers: labels of ``⟦bgdone:<label>⟧``."""
    if not isinstance(reply_text, str) or not reply_text:
        return []
    return [m.strip() for m in _DONE_RE.findall(reply_text) if m.strip()]


def scan_new_steps(buf: str, from_index: int) -> "tuple[list[tuple[str, str]], int]":
    """Live-scan a growing token buffer for COMPLETE ``⟦bgstep:<label>|<status>⟧``
    markers past ``from_index``. Returns ``(steps, new_index)`` where ``new_index``
    advances only past the last *complete* marker — so a marker split across token
    chunks (a partial trailing ``⟦bgstep:…`` with no closing ``⟧``) is left for the
    next call once its rest arrives. Used by the streaming path to surface a
    synchronous turn's current step live, not only at the final reply."""
    out: list[tuple[str, str]] = []
    if not isinstance(from_index, int) or from_index < 0:
        from_index = 0
    end = from_index
    if not isinstance(buf, str) or not buf:
        return out, end
    for m in _STEP_RE.finditer(buf, from_index):
        lbl, status = m.group(1).strip(), m.group(2).strip()[:STATUS_CAP]
        if lbl and status:
            out.append((lbl, status))
        end = m.end()
    # Advance the scan cursor past markerless text so the next call is not O(n)
    # over the whole (ever-growing) buffer again: only the trailing
    # ``_MAX_MARKER_LEN`` chars can still hold a marker split across chunks, so
    # everything before that is safe to skip. Never move backwards (< end) and
    # never past what we already consumed complete markers up to.
    end = max(end, min(len(buf) - _MAX_MARKER_LEN, len(buf)))
    if end < from_index:
        end = from_index
    return out, end


def strip_markers(reply_text: str) -> str:
    """Remove all bg markers so they never reach the channel — including
    partial (unterminated), empty-label and oversized ones (see ``_ANY_RE``)."""
    if not isinstance(reply_text, str) or not reply_text:
        return reply_text
    return _ANY_RE.sub("", reply_text).rstrip()


# ── Store (one file per (session, label)) ────────────────────────────────────

def _safe(s: str) -> str:
    s2 = re.sub(r"[^A-Za-z0-9_.-]", "_", str(s or "")).strip("_")
    return s2[:100] or "_none"


def _dir(state_dir: str | Path) -> Path:
    return Path(state_dir) / "mid_turn_heartbeats"


def _marker_path(state_dir: str | Path, session_key: str, label: str) -> Path:
    h = hashlib.sha1(label.encode("utf-8")).hexdigest()[:10]
    return _dir(state_dir) / f"{_safe(session_key)}__{h}.json"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # UNIQUE tmp name per writer (pid + random) — a fixed ``.tmp`` sibling let two
    # concurrent writers to the same marker clobber each other's tmp file and
    # replace() a half-written one into place. Uniqueness makes the write-then-
    # atomic-replace safe across processes; ``_lock`` covers threads.
    tmp = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load(path: Path) -> "dict | None":
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def mark_active(state_dir: str | Path, session_key: str, *, channel: str,
                chat_id: str | None, sender: str | None, label: str,
                status: str = "") -> "dict | None":
    """Start tracking a background task. Idempotent per (session, label): keeps
    the original ``started_at`` on re-mark. Never raises."""
    try:
        label = (label or "").strip()[:80]
        if not label:
            return None
        p = _marker_path(state_dir, session_key, label)
        with _lock:
            existing = _load(p) if p.is_file() else None
            if existing:
                return existing  # keep original started_at
            rec = {
                "session_key": session_key, "channel": channel or "discord",
                "chat_id": chat_id, "sender": sender, "label": label,
                "status": (status or "").strip()[:STATUS_CAP],
                "last_status_sent": "", "started_at": time.time(),
                "last_ping_at": 0.0, "ping_count": 0,
            }
            _atomic_write(p, rec)
            return rec
    except Exception:  # noqa: BLE001
        return None


def update_status(state_dir: str | Path, session_key: str, *, channel: str = "discord",
                  chat_id: str | None = None, sender: str | None = None,
                  label: str = "", status: str = "") -> "dict | None":
    """Set a task's current step. Creates the marker if the step arrives before
    the start marker (so no update is silently lost). Never raises."""
    try:
        label = (label or "").strip()[:80]
        status = (status or "").strip()[:STATUS_CAP]
        if not label:
            return None
        p = _marker_path(state_dir, session_key, label)
        with _lock:  # RLock: mark_active re-enters it below
            rec = _load(p) if p.is_file() else None
            if rec is None:
                rec = mark_active(state_dir, session_key, channel=channel, chat_id=chat_id,
                                  sender=sender, label=label, status=status)
                return rec
            rec["status"] = status
            _atomic_write(p, rec)
            return rec
    except Exception:  # noqa: BLE001
        return None


def clear_task(state_dir: str | Path, session_key: str, label: str) -> bool:
    try:
        p = _marker_path(state_dir, session_key, label)
        with _lock:
            if p.is_file():
                p.unlink()
                return True
    except OSError:
        pass
    return False


def clear_session(state_dir: str | Path, session_key: str) -> int:
    """Remove every marker for a session. Never raises."""
    n = 0
    try:
        d = _dir(state_dir)
        if not d.is_dir():
            return 0
        for p in d.glob(f"{_safe(session_key)}__*.json"):
            try:
                p.unlink(); n += 1
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
    return n


def active_count(state_dir: str | Path) -> int:
    d = _dir(state_dir)
    return len(list(d.glob("*.json"))) if d.is_dir() else 0


# ── Delivery (called from the adapter main loop) ────────────────────────────

def _envelope(rec: dict, text: str, kind: str, seq) -> dict:
    # Collision-free msg_id: a sha1(session+label+kind+seq) collided whenever two
    # ticks produced the same (kind, seq) for a label, silently overwriting one
    # outbox envelope. Stickiness is chId-keyed in the daemon, so a unique
    # per-envelope id is safe (uniqueness is irrelevant to the in-place edit).
    env = {
        "msg_id": f"mth_{uuid.uuid4().hex[:16]}",
        "channel": rec.get("channel") or "discord",
        "chat_id": rec.get("chat_id"),
        "text": text,
        "ts": time.time(),
    }
    # Liveness ("läuft seit N min") is a SINGLE sticky the daemon edits in place —
    # so the time is shown once and updated, never flooded as a new message per
    # interval. A status CHANGE is a distinct new message (a real event worth a
    # ping), via the non-sticky _task_progress envelope.
    if kind == "live":
        env["_progress"] = True
    else:
        env["_task_progress"] = True
    if rec.get("sender") and not rec.get("chat_id"):
        env["to"] = rec.get("sender")
    return env


def _mins(rec: dict, now: float) -> int:
    try:
        started = float(rec.get("started_at", now))
    except (TypeError, ValueError):
        started = now
    return max(1, int((now - started) // 60))


def deliver_due(state_dir: str | Path, outbox_dir: str | Path, *,
                first_after_s: float = FIRST_AFTER_S, interval_s: float = INTERVAL_S,
                max_pings: int = MAX_PINGS, max_age_s: float = MAX_AGE_S,
                now: float | None = None) -> int:
    """Write due envelopes for every active task: a status line the moment the
    step changes, and a slim liveness line on the slow cadence. Bounded by
    ``max_pings``/``max_age_s``. Returns envelopes written. Never raises."""
    written = 0
    now = time.time() if now is None else now
    try:
        d = _dir(state_dir)
        if not d.is_dir():
            return 0
        outp = Path(outbox_dir)
        outp.mkdir(parents=True, exist_ok=True)
        for p in sorted(d.glob("*.json")):
            # Per-record isolation: one corrupt/poisoned marker must not abort
            # the whole tick and starve every marker sorted after it.
            try:
                with _lock:
                    rec = _load(p)
                    if rec is None:
                        # Unreadable/corrupt marker — GC it so it is not
                        # re-scanned every tick forever (it can never ping).
                        try:
                            p.unlink()
                        except OSError:
                            pass
                        continue
                    label = rec.get("label", "Task")
                    dirty = False

                    # 🔧 status line — immediate, whenever the step changed.
                    status = (rec.get("status") or "").strip()
                    if status and status != rec.get("last_status_sent", ""):
                        env = _envelope(rec, f"🔧 {label}: {status}", "status",
                                        rec.get("_status_seq", 0))
                        _atomic_write(outp / f"{env['msg_id']}.json", env)
                        written += 1
                        rec["last_status_sent"] = status
                        rec["_status_seq"] = rec.get("_status_seq", 0) + 1
                        dirty = True

                    # ⏱️ liveness line — slim, slow cadence, bounded. A record
                    # missing/garbage started_at falls back to `now` (age 0), so
                    # a bad timestamp can never make a marker appear expired and
                    # ping forever; float() guards a non-numeric value.
                    try:
                        started_at = float(rec.get("started_at", now))
                    except (TypeError, ValueError):
                        started_at = now
                    age = now - started_at
                    expired = age >= max_age_s
                    due = age >= first_after_s and (now - rec.get("last_ping_at", 0.0)) >= interval_s
                    if due or expired:
                        over = rec.get("ping_count", 0) >= max_pings or expired
                        text = (f"⏱️ {label} läuft weiter im Hintergrund (seit {_mins(rec, now)} min)."
                                if over else
                                f"⏱️ {label} — läuft seit {_mins(rec, now)} min…")
                        env = _envelope(rec, text, "live", rec.get("ping_count", 0))
                        _atomic_write(outp / f"{env['msg_id']}.json", env)
                        written += 1
                        if over:
                            try:
                                p.unlink()
                            except OSError:
                                pass
                            continue
                        rec["last_ping_at"] = now
                        rec["ping_count"] = rec.get("ping_count", 0) + 1
                        dirty = True

                    if dirty:
                        _atomic_write(p, rec)
            except Exception:  # noqa: BLE001 — isolate a single bad record
                continue
    except Exception:  # noqa: BLE001
        pass
    return written
