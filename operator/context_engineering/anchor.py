"""Session Load-Bearing-Fact Anchor (ADR-0407 amendment — CEL truncation-safe re-injection).

Closes the *within-session context-drift* gap in the deterministic CEL brief:
``render_brief_to_text`` caps memory matches at ``[:5]`` and ``scan_blockers``
caps blocker facts at ``[:5]``; a load-bearing fact at rank 6+ (or with low
confidence) therefore falls out of the brief SILENTLY, every turn, with no
keep-list and no re-injection. This module is the keep-list: it persists the
turn's load-bearing facts (constraints / ids / decisions / goal) per session and
hands them back so the render can re-inject them, uncapped, at the very top of
the brief every turn.

Ship-dark behind the ``cel_load_bearing_anchor`` feature flag (default OFF); when
the flag is off nothing here is reached and the brief is byte-identical to today.

Store: a per-``(tenant, session)`` JSONL file under the tenant-scoped root
resolved via ``forge.paths.tenant_home`` — ``tenant_id`` is ALWAYS passed
explicitly, so there is no ``CORVIN_TENANT_ID`` env-var fallback for the
tenant/session path (CLAUDE.md § Multi-tenant Axis).

Move-2 (loud, not silent): ``INJECTED_FACTS_TOTAL`` is a module-level counter a
watchdog can read, and ``record_injection`` also emits a log line. It becomes
positive the moment anchor facts are actually injected into a rendered brief —
it is a SIGNAL a monitor reads, not merely a return value.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("corvin.cel.anchor")

# The four load-bearing kinds. An unknown kind is coerced to "constraint" (the
# safest default — a constraint is honoured, never dropped).
KINDS = ("constraint", "id", "decision", "goal")
_DEFAULT_KIND = "constraint"

# Append-only, dedup-by-text, cap the newest 20 (oldest evicted).
CAP = 20

# Blocker signal vocabulary (kept in sync with stages/_util._BLOCKER_SIGNALS).
# Used by ``collect_load_bearing`` to recover the rank-6+/low-confidence blocker
# facts that the pipeline's own [:5] caps drop — the exact gap this anchor closes.
_BLOCKER_SIGNALS = (
    "must not", "must-not", "fail-closed", "fail closed", "load-bearing",
    "load bearing", "blocker", "constraint", "deprecated", "do not", "don't",
    "never ", "irreversible", "locked", "blocked", "breaking change",
)

_lock = threading.RLock()

# ── Move-2: watchdog-readable injection signal ─────────────────────────────
# A monitor reads this counter (and the log stream); it is NOT a return value.
INJECTED_FACTS_TOTAL = 0


def record_injection(n: int, *, session_key: str = "") -> None:
    """Record that ``n`` anchor facts were injected into a rendered brief.

    LOUD, not silent (Move-2): bumps the module-level counter a watchdog polls
    AND emits a log line. Called from the render path exactly when the anchor
    slot is emitted, so the counter is positive iff facts actually reached a
    brief. Muting this call (the mutation the Move-2 test guards) leaves the
    counter flat — which the test catches.
    """
    global INJECTED_FACTS_TOTAL
    if n <= 0:
        return
    with _lock:
        INJECTED_FACTS_TOTAL += n
    logger.info("cel.anchor.injected facts=%d session=%s total=%d",
                n, session_key or "?", INJECTED_FACTS_TOTAL)


def injected_total() -> int:
    """Current value of the watchdog-readable injection counter."""
    return INJECTED_FACTS_TOTAL


# ── Path resolution (tenant/session scoped, no env fallback) ───────────────

def _safe_key(session_key: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_key or "")).strip("_")
    if not s:
        return "_nosession"
    if len(s) > 100:  # keep the path bounded but collision-safe
        digest = hashlib.sha1(str(session_key).encode("utf-8")).hexdigest()[:12]
        s = s[:80] + "_" + digest
    return s


def _store_path(tenant_id: str, session_key: str) -> Path:
    # tenant_id is ALWAYS explicit → forge.paths.tenant_home never falls back to
    # the CORVIN_TENANT_ID env var here (CLAUDE.md § Multi-tenant Axis).
    from forge.paths import tenant_home  # noqa: PLC0415
    return (Path(tenant_home(tenant_id)) / "cel_anchors"
            / f"{_safe_key(session_key)}.jsonl")


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


# ── Store API ──────────────────────────────────────────────────────────────

def load_facts(tenant_id: str, session_key: str) -> "list[dict]":
    """Return the persisted facts for this session, oldest-first. Never raises."""
    try:
        p = _store_path(tenant_id, session_key)
        if not p.is_file():
            return []
        out: list[dict] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001 — skip a corrupt line, never crash a turn
                continue
        return out
    except Exception:  # noqa: BLE001 — a broken store must never break the turn
        return []


def _write_all(tenant_id: str, session_key: str, facts: "list[dict]") -> None:
    p = _store_path(tenant_id, session_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(f, ensure_ascii=False) for f in facts)
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text(body + ("\n" if body else ""), encoding="utf-8")
    tmp.replace(p)  # atomic swap


def add_fact(tenant_id: str, session_key: str, kind: str, text: str) -> "dict | None":
    """Persist one load-bearing fact. Append-only + dedup-by-text + cap ``CAP``
    (oldest evicted). Returns the stored entry, or ``None`` when the text is
    empty or a duplicate. Never raises."""
    text = (text or "").strip()
    if not text:
        return None
    kind = kind if kind in KINDS else _DEFAULT_KIND
    h = _text_hash(text)
    try:
        with _lock:
            facts = load_facts(tenant_id, session_key)
            if any(f.get("hash") == h for f in facts):
                return None  # dedup by text-hash
            entry = {
                "id": h[:12],
                "kind": kind,
                "text": text,
                "added_at": time.time(),
                "hash": h,
            }
            facts.append(entry)
            if len(facts) > CAP:  # cap the newest CAP, evict oldest
                facts = facts[-CAP:]
            _write_all(tenant_id, session_key, facts)
            return entry
    except Exception:  # noqa: BLE001 — persistence is best-effort, never break a turn
        return None


def clear(tenant_id: str, session_key: str) -> None:
    """Delete the session's anchor store. Never raises."""
    try:
        _store_path(tenant_id, session_key).unlink()
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        pass


# ── Load-bearing fact collection (the gap-closer) ──────────────────────────

def collect_load_bearing(brief: Any) -> "list[tuple[str, str]]":
    """Extract ``(kind, text)`` load-bearing facts from a brief.

    Sources (per ADR-0407 amendment):
      * ``brief.blockers`` — the pipeline-computed constraints (already [:5]-capped
        by ``scan_blockers``), read verbatim; and
      * an UNCAPPED re-scan of the full memory-match set + related decisions for
        blocker-signal facts — this is what recovers the rank-6+/low-confidence
        constraint that the pipeline's own [:5] caps drop silently. Neither the
        ``scan_blockers`` cap nor the render blockers-section [:5] is modified;
        only this NEW anchor path is uncapped.

    Order-preserving, de-duplicated by text. The session GOAL is added by the
    caller (it needs the raw task), not here.
    """
    facts: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _push(kind: str, text: str) -> None:
        text = (text or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        facts.append((kind, text))

    for b in (getattr(brief, "blockers", None) or []):
        _push("constraint", str(b))

    mc = getattr(brief, "memory_context", None)
    for m in (getattr(mc, "matches", []) if mc else []):
        hay = f"{getattr(m, 'title', '')} {getattr(m, 'content_preview', '')}".lower()
        if any(sig in hay for sig in _BLOCKER_SIGNALS):
            _push("constraint", getattr(m, "title", None) or getattr(m, "filename", "?"))

    for d in (getattr(brief, "related_decisions", None) or []):
        title = getattr(d, "title", "") or ""
        if any(sig in title.lower() for sig in _BLOCKER_SIGNALS):
            _push("decision", getattr(d, "decision_id", None) or title)

    return facts


def render_lines(facts: "list[dict]") -> "list[str]":
    """Render persisted anchor facts into brief lines — UNCAPPED, assertive header.

    All facts render (no ``[:5]``): re-injecting them truncation-safe every turn is
    the whole point. Empty input → no lines (I5: off is a quiet path)."""
    if not facts:
        return []
    lines = [
        "Load-bearing facts (persist across this whole session — always honor these):"
    ]
    for f in facts:
        lines.append(f"  - {f.get('text', '')}")
    return lines
