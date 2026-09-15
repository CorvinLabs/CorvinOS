"""Hybrid workload classifier — ADR-0043.

Classifies user messages into CHAT vs. CODE workloads using heuristic patterns
(engine-agnostic). Classification result is consumed by engine_models.py to
resolve the appropriate model tier for the target engine.

Scope: Chat (conversational, explanatory) → fast model tier of the engine;
Code (write/review code, architecture, debugging with artifacts) → full capability.

No LLM involved in classification; pure regex on code keywords.

Design principle (adversarial review 2026-07-18): the risk is ASYMMETRIC.
A false CHAT verdict downgrades a real coding request to the fast tier
(user-visible failure); a false CODE/UNCERTAIN verdict merely keeps the
user's chosen model (zero cost). Therefore CHAT is only returned when the
message carries none of the code signals this heuristic KNOWS — syntax,
error output, file references, EN/DE coding-intent verbs and nouns, and
anaphoric work-request imperatives. This is a heuristic, not a proof: a
sufficiently oblique paraphrase can still read as chat, which is why the
feature stays opt-in and every routing decision is audited. The measured
flip side: everyday chat containing words like "Test"/"Datei"/"error" in
non-code senses classifies UNCERTAIN, so fast-chat simply doesn't fire
there (~60 % chat recall — safety over coverage by design).
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from collections import deque
from enum import Enum
from typing import NamedTuple


class WorkloadType(Enum):
    """User message workload classification."""
    CHAT = "chat"
    CODE = "code"
    UNCERTAIN = "uncertain"


class ClassificationResult(NamedTuple):
    """Classifier output: workload type + confidence ∈ [0.0, 1.0]."""
    workload: WorkloadType
    confidence: float


# ── Strong syntax signals ────────────────────────────────────────────────
# Literal code constructs. Any hit is unambiguous enough to call the turn
# CODE (which is the cheap direction — the user's model choice is kept).
_CODE_PATTERNS = [
    r'\bdef\s+\w+\s*\(',           # Python function definition
    r'\bclass\s+\w+\s*[:(]',       # Python class definition (needs : or ( — "I have a class tomorrow" must not hit)
    r'^\s*import\s+\w+', r'\bfrom\s+\w+\s+import\b',  # Python imports
    r'\basync\s+(?:def|function)\b',
    r'\bawait\s+\w+',
    r'\blambda\s+\w*:',
    r'\bfunction\s*\w*\s*\(',      # JS function
    r'=>',                         # arrow function
    r'\btry\s*[:{]',
    r'\bexcept\s+\w*Error\b|\bexcept\s*:',
    r'\bif\s+__name__\b',
    r'```',                        # code fence
    r'\binterface\s+\w+\s*\{',
    r'\btype\s+\w+\s*=',
    # SQL — the column list is bounded and newline-free: an unbounded `.+`
    # was quadratic on `SELECT`-repeat inputs (refutation round 2026-07-18:
    # ~9 s at 144 KB, ~7 min at the 1 MB cap, holding the GIL — a full
    # daemon freeze triggerable by one chat message even with the feature
    # disabled, since classification runs before the flag check).
    r'\bSELECT\s+[^\n]{1,200}?\s+FROM\s+\w+',
    r'Traceback \(most recent call last\)',
    r'\b\w+Error:\s', r'\b\w+Exception\b',
    r'\b\w+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|sh|sql|ya?ml|json|toml)\b',  # file reference
]

_COMPILED_PATTERNS = [
    re.compile(pat, re.IGNORECASE | re.MULTILINE) for pat in _CODE_PATTERNS
]

# ── Natural-language coding intent (EN + DE) ─────────────────────────────
# "Write me a Python function that sorts a list" contains zero syntax but is
# a coding request. Verb AND noun together → CODE; either alone → UNCERTAIN.
_INTENT_VERBS = re.compile(
    r'\b(writ(?:e|ing)|creat(?:e|ing)|implement\w*|fix\w*|debug\w*|'
    r'refactor\w*|optimi[sz]\w*|program\w*|patch\w*|deploy\w*|'
    r'schreib\w*|erstell\w*|implementier\w*|behebe?\w*|korrigier\w*|'
    r'debugg\w*|refaktorier\w*|optimier\w*|programmier\w*|'
    # Anaphoric/imperative work requests without any code noun —
    # "mach das Ding aus Punkt 3 schneller", "redo yesterday's solution",
    # "automate that for me" (refutation round 2026-07-18: these leaked
    # through as CHAT 0.9). Verb alone → UNCERTAIN, which is the point:
    # a paraphrased request about prior work must never hit the fast tier.
    r'mach\w*|bau\w*|lös\w*|automat\w*|beschleunig\w*|verbesser\w*|'
    r'änder\w*|redo|automate|rebuild|rewrite|speed)\b'
    r'|\bmake\b.{0,50}\b(?:faster|better|work)\b',
    re.IGNORECASE)
_CODE_NOUNS = re.compile(
    r'\b(function\w*|script\w*|code|bug\w*|error\w*|exception\w*|'
    r'module\w*|api|endpoint\w*|quer(?:y|ies)|regex\w*|test\w*|repo\w*|'
    r'branch\w*|commit\w*|variable\w*|array\w*|loop\w*|database\w*|sql|'
    r'json|yaml|python|javascript|typescript|funktion\w*|skript\w*|'
    r'klasse\w*|modul\w*|abfrage\w*|fehler\w*|datei\w*)\b',
    re.IGNORECASE)

# ── Rate limiting (BUG#13 — now real) ────────────────────────────────────
# The commit that claimed "rate limiting" shipped only a size cap. This is
# an actual sliding-window limiter: beyond the cap, classification is
# SKIPPED and UNCERTAIN 0.0 returned, which fails safe to the user's model.
_RATE_LIMIT_WINDOW_S = 60.0
_RATE_LIMIT_MAX_PER_WINDOW = 120
_rate_lock = threading.Lock()
_rate_events: deque[float] = deque()


def _rate_limited() -> bool:
    now = time.monotonic()
    with _rate_lock:
        while _rate_events and now - _rate_events[0] > _RATE_LIMIT_WINDOW_S:
            _rate_events.popleft()
        if len(_rate_events) >= _RATE_LIMIT_MAX_PER_WINDOW:
            # Log the trip (once per window edge would be nicer, but a
            # line per skipped classification is cheap and makes the
            # otherwise-silent degradation observable for operators).
            import sys
            print("[WARN] classify_workload: rate limit reached — "
                  "classification skipped (UNCERTAIN)", file=sys.stderr)
            return True
        _rate_events.append(now)
        return False


def _count_code_keywords(message: str) -> int:
    """Count how many code keyword patterns match in the message."""
    count = 0
    for pattern in _COMPILED_PATTERNS:
        count += len(pattern.findall(message))
    return count


def classify_workload(
    message: str,
    confidence_threshold: float = 0.5,
    max_message_bytes: int = 1_000_000,  # 1 MB limit for DoS protection
) -> ClassificationResult:
    """Classify a user message into CHAT, CODE, or UNCERTAIN.

    Args:
        message: The user's input message (any length)
        confidence_threshold: Below this confidence, the verdict collapses
            to UNCERTAIN (safe: keeps the user's chosen model)
        max_message_bytes: Maximum message size to process (DoS protection)

    Returns:
        ClassificationResult with workload type and confidence ∈ [0.0, 1.0]

    Decision table (asymmetric-risk design, see module docstring):
      1. Rate-limited / oversized / empty  → UNCERTAIN 0.0
      2. Any syntax signal (fence, def, traceback, file ref, SQL, …)
                                           → CODE, conf 0.6 + 0.1/hit (≤0.95)
      3. Coding-intent verb AND code noun  → CODE, conf 0.75
      4. Verb XOR noun (ambiguous)         → UNCERTAIN 0.4
      5. No code signal at all             → CHAT, conf 0.9
    """
    if not message or not isinstance(message, str):
        return ClassificationResult(WorkloadType.UNCERTAIN, 0.0)

    if _rate_limited():
        return ClassificationResult(WorkloadType.UNCERTAIN, 0.0)

    # DoS protection: reject overly large messages
    if len(message.encode("utf-8")) > max_message_bytes:
        import sys
        print(f"[WARN] classify_workload: message exceeds {max_message_bytes} bytes, rejecting as UNCERTAIN", file=sys.stderr)
        return ClassificationResult(WorkloadType.UNCERTAIN, 0.0)

    # Normalize Unicode to NFC form (prevents evasion via combining marks)
    message = unicodedata.normalize("NFC", message)

    # Classify on the HEAD of the message only. The workload signal of a
    # long paste is in its first lines, and bounding the regex input is
    # defense-in-depth against any future quadratic pattern (the SQL
    # pattern froze the daemon for minutes at the 1 MB cap before it was
    # bounded — refutation round 2026-07-18).
    message = message[:10_000]

    syntax_hits = _count_code_keywords(message)
    if syntax_hits:
        confidence = min(0.95, 0.6 + 0.1 * syntax_hits)
        workload = WorkloadType.CODE
    else:
        has_verb = bool(_INTENT_VERBS.search(message))
        has_noun = bool(_CODE_NOUNS.search(message))
        if has_verb and has_noun:
            workload, confidence = WorkloadType.CODE, 0.75
        elif has_verb or has_noun:
            workload, confidence = WorkloadType.UNCERTAIN, 0.4
        else:
            workload, confidence = WorkloadType.CHAT, 0.9

    if confidence < confidence_threshold:
        return ClassificationResult(WorkloadType.UNCERTAIN, confidence)
    return ClassificationResult(workload, confidence)


def classify_and_store_workload_hint(
    user_message: str,
    session: dict,
    audit_callback: "callable | None" = None,
) -> dict:
    """
    ADR-0043 Phase 1: Classify user message and store hint in session.

    Called early in the bridge request handler (before spawn-input
    resolution; the hint is threaded into _resolve_os_model as a parameter).

    Args:
        user_message: The raw user input from Discord/Web/CLI
        session: Dict the hint is additionally written into under
            "workload_hint". NOTE: the production call sites pass a
            throwaway {} and use the RETURN VALUE — the session store
            exists for callers that keep per-chat state, not as the
            transport mechanism.
        audit_callback: Optional callback to log audit event

    Returns:
        WorkloadHint dict with workload type, confidence, and timestamp
    """
    from datetime import datetime

    # Empty/None input carries zero evidence → UNCERTAIN, which keeps the
    # user's chosen model. (A previous revision returned CHAT 1.0 here and
    # thereby routed empty prompts to the fast tier — the opposite of the
    # documented "safe default".)
    if not user_message or not isinstance(user_message, str):
        classification_result = ClassificationResult(WorkloadType.UNCERTAIN, 0.0)
    else:
        classification_result = classify_workload(user_message)

    hint = {
        "workload": str(classification_result.workload.value),
        "confidence": classification_result.confidence,
        "timestamp": int(datetime.now().timestamp() * 1000),
    }
    session["workload_hint"] = hint

    # Audit trail: log the classification decision. Never the message text
    # (GDPR Art. 5 / repo PII rule); the hash is sha256-based so it is
    # stable across processes (hash() is PYTHONHASHSEED-salted and was
    # useless for correlation).
    if audit_callback:
        try:
            digest = hashlib.sha256(user_message.encode("utf-8", "replace")).hexdigest()[:16] \
                if isinstance(user_message, str) else ""
            audit_callback({
                "event_type": "workload_classification",
                "workload": hint["workload"],
                "confidence": hint["confidence"],
                "message_hash": digest,
                "timestamp": hint["timestamp"],
            })
        except Exception as e:
            # Audit failure is non-fatal; never break the request
            import sys
            print(f"[WARN] audit callback failed: {e}", file=sys.stderr)

    return hint
