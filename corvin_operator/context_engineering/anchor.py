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

# Per-kind sub-cap for "decision" (ADR-0407 amendment — decision-point capture):
# keep only the newest N decision menus, evicting the OLDEST decision WITHOUT
# ever displacing a constraint/id/goal. A stale "Option N" from three menus ago
# is noise; the newest few are what "Option 2" still refers to after compression.
DECISION_SUBCAP = 3

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


# ── Move-2 (capture side): watchdog-readable decision-capture signal ────────
# Symmetric to INJECTED_FACTS_TOTAL: a monitor reads this counter (and the log
# stream) to confirm the OUTBOUND capture path fires. Positive iff a decision
# menu was actually persisted (not a duplicate, not a non-decision reply).
CAPTURED_DECISIONS_TOTAL = 0


def record_capture(*, session_key: str = "") -> None:
    """Record that one decision menu was captured from an outbound reply.

    LOUD, not silent (Move-2): bumps the module-level counter a watchdog polls
    AND emits a log line. Muting this call (the mutation the Move-2 test guards)
    leaves the counter flat — which the fires-test catches."""
    global CAPTURED_DECISIONS_TOTAL
    with _lock:
        CAPTURED_DECISIONS_TOTAL += 1
    logger.info("cel.anchor.decision_captured session=%s total=%d",
                session_key or "?", CAPTURED_DECISIONS_TOTAL)


def captured_total() -> int:
    """Current value of the watchdog-readable decision-capture counter."""
    return CAPTURED_DECISIONS_TOTAL


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
            # Per-kind sub-cap for "decision": keep only the newest
            # DECISION_SUBCAP decision menus, evicting the OLDEST decision only —
            # constraint/id/goal entries are never displaced by this rule (that
            # is the whole point of a SUB-cap, not the global CAP below).
            decisions = [f for f in facts if f.get("kind") == "decision"]
            if len(decisions) > DECISION_SUBCAP:
                _evict = {id(f) for f in decisions[:-DECISION_SUBCAP]}
                facts = [f for f in facts if id(f) not in _evict]
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


# ── Decision-point capture (ADR-0407 amendment — outbound path) ─────────────
# When an assistant reply OFFERS the user a choice, the block is persisted
# verbatim (+ framing) so that after context compression a later "Option 2"
# still resolves. CONSERVATIVE detection — most replies are NOT decision menus.

# ≥2 "Option N" tokens (DE/EN — the word "Option" is identical in both).
_OPTION_RE = re.compile(r"\boption\s*\d", re.IGNORECASE)
# Choice-intent keywords that qualify a labelled/numbered list as a real choice.
_CHOICE_WORDS = (
    "?", "wähl", "waehl", "choose", "which ", "welche", "welchen", "welches",
    "prefer", "bevorzug", "option", "möchtest", "moechtest", "entscheid",
    "shall i", "soll ich", "pick ", "auswahl",
)


def _has_choice_intent(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in _CHOICE_WORDS)


def _numbered_after_question(text: str) -> bool:
    """True iff lines ``1.`` and ``2.`` (or ``1)``/``2)``) directly follow a
    question line or a ``**Frage`` marker — the ONLY numbered lists we treat as a
    choice. A plain enumeration (no preceding question) is NOT captured."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        s = ln.strip()
        low = s.lower()
        is_q = s.endswith("?") or low.startswith("**frage") or "**frage:" in low
        if not is_q:
            continue
        nums: list[int] = []
        for ln2 in lines[i + 1:]:
            t = ln2.strip()
            if not t:
                if nums:
                    break  # blank line ends the block once it started
                continue
            m = re.match(r"^(\d+)[.)]\s", t)
            if m:
                nums.append(int(m.group(1)))
            else:
                break
        if 1 in nums and 2 in nums:
            return True
    return False


def _classify_decision(text: str) -> "str | None":
    """Return the decision kind ('option' | 'label' | 'numbered') or None.
    Conservative: unclear input → None (do not capture every list)."""
    if len(_OPTION_RE.findall(text)) >= 2:
        return "option"
    if re.search(r"\(a\)", text, re.IGNORECASE) and re.search(r"\(b\)", text, re.IGNORECASE):
        if _has_choice_intent(text):
            return "label"
    if _numbered_after_question(text):
        return "numbered"
    return None


def _first_trigger_pos(text: str, kind: str) -> int:
    if kind == "option":
        m = _OPTION_RE.search(text)
    elif kind == "label":
        m = re.search(r"\(a\)", text, re.IGNORECASE)
    else:  # numbered
        m = re.search(r"(?m)^\s*1[.)]\s", text)
    return m.start() if m else 0


def _extract_decision_block(text: str) -> "str | None":
    """Extract the choice block VERBATIM plus one framing line before it, bounded
    to ~1500 chars. None when the reply is not a clear decision menu."""
    kind = _classify_decision(text)
    if not kind:
        return None
    pos = _first_trigger_pos(text, kind)
    line_start = text.rfind("\n", 0, pos) + 1  # start of the option/label line
    framing = ""
    head = text[:line_start].rstrip()
    if head:
        for ln in reversed(head.split("\n")):
            if ln.strip():
                framing = ln.rstrip()
                break
    block = text[line_start:]
    if framing:
        block = framing + "\n" + block
    block = block.strip()
    if len(block) > 1500:
        block = block[:1500].rstrip()
    return block or None


def capture_decision_point(tenant_id: str, session_key: str,
                           reply_text: str) -> "dict | None":
    """Persist a decision/options block from an assistant reply (verbatim + a
    framing line) as a ``kind='decision'`` anchor fact, so a later "Option N"
    resolves even after the context is compressed. Returns the stored entry, or
    None when the reply is not a clear decision menu / the block is a duplicate.

    Best-effort: NEVER raises (an error here must never break a turn)."""
    try:
        text = (reply_text or "").strip()
        if not text:
            return None
        block = _extract_decision_block(text)
        if not block:
            return None
        entry = add_fact(tenant_id, session_key, "decision", block)
        if entry is not None:  # actually stored (not a duplicate) → loud signal
            record_capture(session_key=session_key)
        return entry
    except Exception:  # noqa: BLE001 — capture is best-effort, never break a turn
        return None


def render_lines(facts: "list[dict]") -> "list[str]":
    """Render persisted anchor facts into brief lines — UNCAPPED, assertive header.

    All facts render (no ``[:5]``): re-injecting them truncation-safe every turn is
    the whole point. Empty input → no lines (I5: off is a quiet path).

    Grouped by kind (ADR-0407 amendment): decision-point facts get their OWN
    protected header so a compressed "Option N" resolves against the verbatim
    block; constraint/id/goal facts stay under the load-bearing header. Both
    UNCAPPED."""
    if not facts:
        return []
    decisions = [f for f in facts if f.get("kind") == "decision"]
    others = [f for f in facts if f.get("kind") != "decision"]
    lines: list[str] = []
    if others:
        lines.append(
            "Load-bearing facts (persist across this whole session — always honor these):"
        )
        for f in others:
            lines.append(f"  - {f.get('text', '')}")
    if decisions:
        lines.append(
            "Open decision points you were offered (verbatim — 'Option N' refers to these):"
        )
        for f in decisions:
            lines.append(f"  - {f.get('text', '')}")
    return lines
