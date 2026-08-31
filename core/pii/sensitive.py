"""
Fail-Closed Sensitive-Content Gate — ADR-0297 (hardening extension)

The shipped :mod:`core.pii.patterns` detects only STRUCTURED PII shapes
(email / phone / credit-card / SSN / IP / passport / plate) and never fails
closed — a regex error there silently returns "no PII".

This module adds the FREE-TEXT half that structured matching misses and makes
the whole thing fail closed, per ADR-0297:

  * private-key blocks (RSA / EC / OPENSSH / DSA / PGP)
  * API keys / bearer tokens (AWS AKIA, GitHub ghp_/gho_/…, Slack xox…,
    Google AIza…, generic ``Bearer <token>``)
  * credential assignment shapes (``password = …`` / ``token: …`` /
    ``password is hunter2``)
  * a conservative high-entropy free-text-secret heuristic
  * free-text PII the structured detectors miss: personal names, postal
    addresses, and @-handles

Semantics (ADR-0297):

  * FAIL-CLOSED — any regex / parse error inside the scan raises
    :class:`PIIDetectionFailedClosed`. The caller must treat that as "sensitive"
    (drop the field), never as "clean". The gate NEVER swallows an error into a
    ``False``.
  * An ambiguous match is SUSPICIOUS and rejected (the heuristics err toward
    over-dropping — a dropped learned field is simply not injected, which loses
    context but never leaks).

The public entry point is :func:`has_sensitive`. It is a GATE, not a scrubber:
callers use it to DROP a whole learned field before injection rather than to
redact substrings (a partially-scrubbed secret is still a leaked secret).

Precision note — the gate deliberately does NOT reuse the two lowest-precision
structured patterns (``passport`` = ``[A-Z]{1,2}\\d{6,9}`` and ``license_plate``
= ``[A-Z]{1,3}\\s?\\d{1,4}``, plus the ``ipv6`` pattern which matches ``HH:MM:SS``
clock strings). Those stay INTACT in :data:`core.pii.patterns.PII_PATTERNS` for
``detect_pii_types`` / ``scrub_pii``, but folding them into an
injection-blocking gate would drop ordinary technical text ("ADR 297",
"10:30:00") on every turn. The gate uses the high-precision structured subset
(:data:`_GATE_STRUCTURED_TYPES`) plus the new free-text / secret detectors.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

from core.pii.patterns import PIIPattern, PII_PATTERNS


class PIIDetectionFailedClosed(RuntimeError):
    """Raised when the sensitive-content scan cannot complete.

    ADR-0297 fail-closed contract: a detection error MUST propagate as a
    rejection, never degrade into "no PII found". Callers gating field
    injection must treat this exactly like ``has_sensitive(...) is True`` —
    drop the field.
    """


# ---------------------------------------------------------------------------
# High-precision structured subset reused by the gate.
#
# email / phone / credit_card / ssn / ipv4 are specific enough that a match is
# almost always real PII. passport / license_plate / ipv6 are intentionally
# EXCLUDED here (see module docstring) — they remain fully live for
# detect_pii_types / scrub_pii.
# ---------------------------------------------------------------------------
_GATE_STRUCTURED_TYPES: tuple[str, ...] = (
    "email",
    "phone",
    "credit_card",
    "us_ssn",
    "ipv4",
)


def _p(name: str, pattern: str, flags: int = 0) -> PIIPattern:
    return PIIPattern(name=name, pattern=re.compile(pattern, flags), replacement="[REDACTED]")


# ---------------------------------------------------------------------------
# Secret / credential / free-text-PII detectors.
#
# Ordered most-specific first. Every entry is a compiled regex; a match makes
# the whole field SUSPICIOUS -> the gate returns True (reject).
# ---------------------------------------------------------------------------
_SENSITIVE_DETECTORS: list[PIIPattern] = [
    # --- Private key blocks (RSA / EC / OPENSSH / DSA / PGP / generic) -------
    _p(
        "private_key_block",
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----",
    ),
    # --- Cloud / VCS / chat provider API keys -------------------------------
    # AWS access key id
    _p("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b"),
    # AWS secret access key assignment (40-char base64-ish)
    _p(
        "aws_secret_key",
        r"(?i)\baws_?secret_?access_?key\b\s*[:=]\s*[A-Za-z0-9/+=]{20,}",
    ),
    # GitHub personal-access / app tokens
    _p("github_token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    _p("github_pat", r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    # Slack tokens
    _p("slack_token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # Google API key
    _p("google_api_key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    # Stripe / OpenAI style sk-/pk- secret keys
    _p("prefixed_secret_key", r"\b[sp]k[-_](?:live|test|proj)?[-_]?[A-Za-z0-9]{16,}\b"),
    # JWT (three base64url segments)
    _p("jwt", r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    # HTTP bearer / authorization header
    _p("bearer_token", r"(?i)\b(?:authorization\s*[:=]\s*)?bearer\s+[A-Za-z0-9\-._~+/]{12,}=*"),
    # --- Generic credential-assignment shapes -------------------------------
    # password/secret/token/api_key = <value>  OR  "password is hunter2"
    _p(
        "credential_assignment",
        r"(?i)\b(?:pass(?:word|phrase|wd)?|pwd|secret|api[_ -]?key|access[_ -]?key|"
        r"auth(?:_?token|orization)?|client[_ -]?secret|private[_ -]?key|"
        r"session[_ -]?token|bearer[_ -]?token|token)\b\s*(?:is|=|:|=>|===)\s*\S{2,}",
    ),
    # --- Free-text PII the structured detectors miss ------------------------
    # @-handle (mastodon/twitter/slack style); NOT the @ inside an email
    _p("social_handle", r"(?<![\w@./+])@[A-Za-z0-9_]{2,}\b"),
    # Postal address: street keyword adjacent to a number (DE + EN forms)
    _p(
        "street_address",
        r"(?i)\b(?:\d{1,4}\s+[A-Za-z.\s]{2,40}?(?:street|st\.|avenue|ave\.?|road|rd\.?|"
        r"lane|ln\.?|boulevard|blvd\.?|drive|dr\.?|way|court|ct\.?)"
        r"|[A-Za-zäöüß.\s]{2,40}?(?:stra(?:ss|ß)e|str\.|weg|gasse|allee|platz|ring)\s+\d{1,4})\b",
    ),
    # Postal code + city (DE 5-digit / US ZIP followed by a capitalised town)
    _p("postal_code_city", r"\b\d{5}(?:-\d{4})?\s+[A-ZÄÖÜ][A-Za-zäöüß.\-]{2,}"),
    # Personal name: two or more consecutive Title-case words. Intentionally
    # aggressive (fail-closed) — see ADR note. Requires each token to be a
    # plausible name token (leading capital + >=1 lowercase letter).
    _p("personal_name", r"\b[A-ZÄÖÜ][a-zäöüß]+(?:[-'][A-ZÄÖÜ][a-zäöüß]+)?(?:\s+[A-ZÄÖÜ][a-zäöüß]+){1,3}\b"),
]


# ---------------------------------------------------------------------------
# High-entropy free-text secret heuristic.
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
_ENTROPY_MIN_BITS_PER_CHAR = 3.5
_ENTROPY_MIN_LEN = 20


def _shannon_entropy(s: str) -> float:
    """Shannon entropy (bits/char) of a string. 0.0 for empty."""
    if not s:
        return 0.0
    n = len(s)
    counts = Counter(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _has_high_entropy_secret(text: str) -> bool:
    """Conservative random-looking-token heuristic.

    Fires only on a >=20 char token that mixes letters AND digits and whose
    Shannon entropy exceeds ~3.5 bits/char. This deliberately ignores ordinary
    long words ("internationalization" — no digit) and short secrets like
    ``hunter2`` (caught instead by the credential-assignment shape).
    """
    for m in _TOKEN_RE.finditer(text):
        tok = m.group()
        if len(tok) < _ENTROPY_MIN_LEN:
            continue
        has_digit = any(ch.isdigit() for ch in tok)
        has_alpha = any(ch.isalpha() for ch in tok)
        if has_digit and has_alpha and _shannon_entropy(tok) >= _ENTROPY_MIN_BITS_PER_CHAR:
            return True
    return False


def detect_sensitive_types(text: str) -> list[str]:
    """Return the names of every sensitive-content detector that fired.

    Fail-closed (ADR-0297): any error while scanning raises
    :class:`PIIDetectionFailedClosed` rather than returning a (possibly empty)
    list. An empty list therefore means "scanned cleanly, nothing found" — it
    can never be produced by a swallowed error.
    """
    if not isinstance(text, str):
        raise PIIDetectionFailedClosed(
            f"detect_sensitive_types requires str, got {type(text).__name__}"
        )
    found: list[str] = []
    try:
        for name in _GATE_STRUCTURED_TYPES:
            if PII_PATTERNS[name].pattern.search(text):
                found.append(f"structured:{name}")
        for det in _SENSITIVE_DETECTORS:
            if det.pattern.search(text):
                found.append(det.name)
        if _has_high_entropy_secret(text):
            found.append("high_entropy_secret")
    except PIIDetectionFailedClosed:
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed on ANY scan failure
        raise PIIDetectionFailedClosed(
            f"sensitive-content scan failed: {type(exc).__name__}: {exc}"
        ) from exc
    return found


def has_sensitive(text: Optional[str]) -> bool:
    """Fail-closed gate: does ``text`` contain any sensitive content?

    Returns ``True`` when the field must be DROPPED before injection (structured
    PII, a secret/credential, or free-text PII). Returns ``False`` only when the
    field scanned cleanly.

    Raises :class:`PIIDetectionFailedClosed` if the scan itself errors — the
    caller MUST treat that as ``True`` (drop), never as clean. ``None`` and the
    empty string are clean by definition; any other non-``str`` is coerced with
    ``str()`` before scanning (never silently passed through).
    """
    if text is None:
        return False
    if not isinstance(text, str):
        text = str(text)
    if not text.strip():
        return False
    return bool(detect_sensitive_types(text))


__all__ = [
    "PIIDetectionFailedClosed",
    "has_sensitive",
    "detect_sensitive_types",
]
