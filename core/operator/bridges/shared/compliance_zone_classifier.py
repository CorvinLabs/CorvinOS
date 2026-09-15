"""compliance_zone_classifier.py — Phase 5 (ADR-0004) zone classifier.

Pure-Python heuristic that picks a *compliance zone* for a given task,
so the engine_policy can route the task to a zone-allowed engine
(``personal_data`` → eu-resident model, ``code_only`` → developer model,
etc.).

Three signal classes, deterministic precedence:

  1. **Explicit user marker** — ``[zone:foo]`` prefix in the task text.
     Operator-driven override; always wins.
  2. **PII regex bank** — e-mail, +49 / +1 / +44 phone, IBAN, 16-digit
     credit-card, US SSN / CH AHV / DE Steuer-ID patterns. Match → zone
     ``personal_data``.
  3. **Persona-hint table** — ``inbox`` → ``personal_data``,
     ``coder`` → ``code_only``, ``research`` → ``external_facing``,
     ``forge`` → ``code_only``. Persona is informational; PII regex
     overrides it.

Default zone (no signal): ``general``. The policy treats unknown zones
as "use default_chain" so this is the safe fall-through.

False-negative is fine — operator can always pre-pin via the explicit
marker. False-positive (routing a benign task into ``personal_data``)
is the conservative side: worst case, the request goes to the EU
engine instead of the default.

Public API:

    classify_zone(task: str, persona: str | None = None) -> dict
        Returns ``{zone, signals, confidence, reason}``.
"""
from __future__ import annotations

import re

# ----- regex banks ---------------------------------------------------------

_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE
)
# International phone numbers — +<country><digits>, allowing spaces and dashes.
_PHONE_RE = re.compile(
    r"\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{2,5}[\s\-]?\d{2,7}"
)
# IBAN — country code + 2 check digits + up to 30 alphanumeric.
_IBAN_RE = re.compile(
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}[A-Z0-9]{1,30}\b"
)
# Credit card — 13–19 digits, optionally separated by spaces or dashes.
_CC_RE = re.compile(
    r"\b(?:\d[\s\-]?){13,19}\b"
)
# US SSN — XXX-XX-XXXX
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# CH AHV — 756.XXXX.XXXX.XX
_AHV_RE = re.compile(r"\b756\.\d{4}\.\d{4}\.\d{2}\b")
# DE Steuer-ID — 11 digits (loose; the official check-digit is not
# enforced — false-positive on "11 random digits in a row" is OK
# because the conservative side of zone classification is
# "personal_data" routing).
_STEUER_RE = re.compile(r"\b\d{11}\b")

_PII_PATTERNS = [
    ("email",      _EMAIL_RE),
    ("phone",      _PHONE_RE),
    ("iban",       _IBAN_RE),
    ("credit_card", _CC_RE),
    ("ssn",        _SSN_RE),
    ("ahv",        _AHV_RE),
    ("steuer_id",  _STEUER_RE),
]

# ----- persona hints ------------------------------------------------------

_PERSONA_ZONE: dict[str, str] = {
    "inbox":          "personal_data",
    "coder":          "code_only",
    "forge":          "code_only",
    "skill-forge":    "code_only",
    "research":       "external_facing",
    "homeassistant":  "personal_data",
    "assistant":      "general",
    "os":             "general",
}

# ----- explicit marker ----------------------------------------------------

_MARKER_RE = re.compile(r"^\s*\[zone:([a-z0-9_\-]+)\]\s*", re.IGNORECASE)


def classify_zone(task: str, persona: str | None = None) -> dict:
    """Pick a compliance zone for the task. Returns
    ``{zone, signals, confidence, reason}``.

    Precedence (first non-empty wins):
      1. Explicit ``[zone:foo]`` marker at start of task → trust operator
      2. PII regex match → "personal_data"
      3. Persona-hint table → that zone
      4. "general"

    ``signals`` is a list of strings naming the matched signal(s),
    useful for audit attribution.
    """
    if not isinstance(task, str) or not task.strip():
        return {
            "zone": "general",
            "signals": [],
            "confidence": 0.0,
            "reason": "empty task",
        }

    # 1. Explicit marker
    m = _MARKER_RE.match(task)
    if m:
        zone = m.group(1).lower()
        return {
            "zone": zone,
            "signals": [f"marker:{zone}"],
            "confidence": 1.0,
            "reason": f"explicit [zone:{zone}] marker",
        }

    # 2. PII regex
    pii_hits: list[str] = []
    for tag, pattern in _PII_PATTERNS:
        if pattern.search(task):
            pii_hits.append(tag)
    if pii_hits:
        return {
            "zone": "personal_data",
            "signals": [f"pii:{h}" for h in pii_hits],
            "confidence": 0.85,
            "reason": "PII regex hit: " + ",".join(pii_hits),
        }

    # 3. Persona hint
    if persona:
        zone = _PERSONA_ZONE.get(persona.strip().lower())
        if zone:
            return {
                "zone": zone,
                "signals": [f"persona:{persona}"],
                "confidence": 0.6,
                "reason": f"persona hint: {persona}→{zone}",
            }

    # 4. Default
    return {
        "zone": "general",
        "signals": [],
        "confidence": 0.0,
        "reason": "no signal",
    }
