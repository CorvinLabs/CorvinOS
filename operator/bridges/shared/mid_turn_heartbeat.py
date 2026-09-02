"""Mid-turn background-task heartbeat (ADR-0551 C1, variant B — ship-dark).

The gap (ADR-0551): when the agent spawns a background sub-agent *mid-turn*, the
bridge's durable `/task` backbone is never engaged, so no "still working" pings
reach the user while it runs. Variant B closes ONLY that gap: completion still
comes from the agent's own reply when the host re-invokes it; this module adds
the missing *intermediate* heartbeat and nothing else (no completion ping, so no
double-ping, and no coupling to host task-file internals for completion).

Signal source (deterministic, no host-internal coupling): the agent emits a
marker ``⟦bgtask:<label>⟧`` in the reply that launches background work. The
adapter parses it at the outbound reply hook and calls :func:`mark_active`; it
strips the marker so it never reaches the channel. A marker is cleared when the
same session produces its NEXT reply (the prior work is done/superseded) and, as
a backstop, self-expires by age/ping cap so it can never ping forever.

Ship-dark behind ``bridge_mid_turn_task_notify`` (default OFF); the adapter gates
every call, so with the flag off nothing here is reached.

Delivery reuses the normal outbox envelope (`channel`/`chat_id`/`text`/unique
`msg_id`/`_task_progress`) — the exact shape ``daemon.js sendDiscord`` +
``outbox.js`` already deliver proactively (channel-filtered, not reply-bound).

Best-effort throughout: a broken heartbeat must never break a turn or the loop.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

# Tunables (conservative — a heartbeat is a courtesy, not a firehose).
FIRST_AFTER_S = 60      # no ping until a task has run this long
INTERVAL_S = 60         # min gap between pings for one task
MAX_PINGS = 10          # then emit a final note and stop
MAX_AGE_S = 1800        # 30 min hard cap; stop pinging past this

_MARKER_RE = re.compile(r"⟦bgtask:([^⟧]{1,80})⟧")


# ── Marker parsing / stripping (used by the adapter reply hook) ──────────────

def parse_markers(reply_text: str) -> "list[str]":
    """Return the labels of any ``⟦bgtask:<label>⟧`` markers in a reply."""
    if not reply_text:
        return []
    out, seen = [], set()
    for m in _MARKER_RE.findall(reply_text):
        lbl = m.strip()
        if lbl and lbl not in seen:
            seen.add(lbl)
            out.append(lbl)
    return out


def strip_markers(reply_text: str) -> str:
    """Remove all bgtask markers so they never reach the channel."""
    if not reply_text:
        return reply_text
    return _MARKER_RE.sub("", reply_text).rstrip()


# ── Store (one file per (session, label), so N concurrent tasks per session) ─

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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def mark_active(state_dir: str | Path, session_key: str, *, channel: str,
                chat_id: str | None, sender: str | None, label: str) -> "dict | None":
    """Record that a background task ``label`` is running for this session.
    Idempotent per (session, label): re-marking refreshes nothing (keeps the
    original ``started_at``). Never raises."""
    try:
        label = (label or "").strip()[:80]
        if not label:
            return None
        p = _marker_path(state_dir, session_key, label)
        if p.is_file():
            return None  # already tracked; keep original started_at
        rec = {
            "session_key": session_key, "channel": channel or "discord",
            "chat_id": chat_id, "sender": sender, "label": label,
            "started_at": time.time(), "last_ping_at": 0.0, "ping_count": 0,
        }
        _atomic_write(p, rec)
        return rec
    except Exception:  # noqa: BLE001 — best-effort
        return None


def clear_session(state_dir: str | Path, session_key: str) -> int:
    """Remove every active marker for a session (its next reply arrived → the
    prior background work is done/superseded). Returns count removed. Never raises."""
    n = 0
    try:
        pref = f"{_safe(session_key)}__"
        d = _dir(state_dir)
        if not d.is_dir():
            return 0
        for p in d.glob(f"{pref}*.json"):
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

def _envelope(rec: dict, text: str) -> dict:
    uid = hashlib.sha1(
        f"{rec.get('session_key')}{rec.get('label')}{rec.get('ping_count')}"
        .encode("utf-8")).hexdigest()[:12]
    env = {
        "msg_id": f"mth_{uid}",
        "channel": rec.get("channel") or "discord",
        "chat_id": rec.get("chat_id"),
        "text": text,
        "_task_progress": True,   # normal proactive envelope; NOT a sticky _progress
        "ts": time.time(),
    }
    if rec.get("sender") and not rec.get("chat_id"):
        env["to"] = rec.get("sender")
    return env


def _fmt(rec: dict, *, final: bool) -> str:
    mins = max(1, int((time.time() - rec.get("started_at", time.time())) // 60))
    label = rec.get("label", "Hintergrund-Task")
    if final:
        return f"⏳ „{label}“ läuft weiter im Hintergrund (seit {mins} min) — Ergebnis kommt, sobald es fertig ist."
    return f"⏳ Noch dran an „{label}“ — läuft seit {mins} min…"


def deliver_due(state_dir: str | Path, outbox_dir: str | Path, *,
                first_after_s: float = FIRST_AFTER_S, interval_s: float = INTERVAL_S,
                max_pings: int = MAX_PINGS, max_age_s: float = MAX_AGE_S,
                now: float | None = None) -> int:
    """Write a heartbeat envelope for every active task that is due. Bounded by
    ``max_pings``/``max_age_s`` (then a final note + marker removed). Returns the
    number of envelopes written. Never raises."""
    written = 0
    now = time.time() if now is None else now
    try:
        d = _dir(state_dir)
        if not d.is_dir():
            return 0
        outp = Path(outbox_dir)
        outp.mkdir(parents=True, exist_ok=True)
        for p in sorted(d.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — skip a corrupt marker
                continue
            age = now - rec.get("started_at", now)
            if age < first_after_s:
                continue
            if now - rec.get("last_ping_at", 0.0) < interval_s:
                continue
            over = rec.get("ping_count", 0) >= max_pings or age >= max_age_s
            env = _envelope(rec, _fmt(rec, final=over))
            _atomic_write(outp / f"{env['msg_id']}.json", env)
            written += 1
            if over:
                try:
                    p.unlink()
                except OSError:
                    pass
            else:
                rec["last_ping_at"] = now
                rec["ping_count"] = rec.get("ping_count", 0) + 1
                _atomic_write(p, rec)
    except Exception:  # noqa: BLE001
        pass
    return written
