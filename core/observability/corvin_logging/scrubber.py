"""PII scrubber for log records (ADR-0231 Stage 1, GDPR Art. 5).

Deliberate deviation from the design sketch: it raises ``ValueError`` when a
pattern matches. A logger that raises turns a logging problem into a request
failure, and callers wrap log calls in nothing — one stray email address in a
message would take down the turn that logged it.

Here a match is **redacted**, the record is marked ``pii_redacted: true``, and the
record still ships. That is fail-closed in the sense that matters: the personal
data does not reach the log, while the operational signal survives. The marker is
what makes the redaction auditable rather than invisible.

The patterns are intentionally conservative. This is a backstop for a mistake, not
a licence to log user content: the rule remains "log metadata, not payloads".
"""
from __future__ import annotations

import re
from typing import Any, Dict, Tuple

#: (kind, pattern). Order matters only for overlapping matches; each is replaced
#: by ``[REDACTED:<kind>]``.
_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")),
    # Bearer/API-token shapes that show up in error strings.
    ("token", re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    # A URL with embedded credentials — the classic connection-string leak.
    ("url_credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
    # 13-19 digit card-like runs, optionally grouped. Kept after IBAN so an IBAN
    # is not partially matched as a card number.
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # IPv4 that is not obviously loopback/link-local. An IP is personal data under
    # GDPR, and log aggregators are the classic place it accumulates.
    ("ipv4", re.compile(r"\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)\d{1,3}(?:\.\d{1,3}){3}\b")),
)

#: Keys whose VALUE is redacted wholesale regardless of shape — a field named
#: "password" is never worth pattern-matching.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "session",
        "credential",
        "credentials",
        "private_key",
    }
)

#: Guard against a pathological nested structure eating the request thread.
_MAX_DEPTH = 6


def scrub_text(text: str) -> Tuple[str, bool]:
    """Redact known PII shapes in a string.  Returns ``(text, was_redacted)``."""
    redacted = False
    for kind, pattern in _PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{kind}]", text)
        if n:
            redacted = True
    return text, redacted


def scrub(value: Any, *, _depth: int = 0) -> Tuple[Any, bool]:
    """Recursively redact a log payload.  Returns ``(value, was_redacted)``.

    Never raises: a scrubber that throws is a scrubber that gets wrapped in a bare
    ``except`` at the call site, which is how PII ends up unscrubbed in practice.
    """
    if _depth > _MAX_DEPTH:
        return "[REDACTED:too-deep]", True

    if isinstance(value, str):
        return scrub_text(value)

    if isinstance(value, dict):
        out: Dict[Any, Any] = {}
        redacted = False
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS:
                out[key] = "[REDACTED:sensitive-key]"
                redacted = True
                continue
            out[key], hit = scrub(item, _depth=_depth + 1)
            redacted = redacted or hit
        return out, redacted

    if isinstance(value, (list, tuple)):
        items, redacted = [], False
        for item in value:
            scrubbed, hit = scrub(item, _depth=_depth + 1)
            items.append(scrubbed)
            redacted = redacted or hit
        return (type(value)(items) if isinstance(value, tuple) else items), redacted

    # int / float / bool / None and anything else opaque: nothing to scan.
    return value, False


def contains_pii(value: Any) -> bool:
    """True when :func:`scrub` would redact something.  For tests and gates."""
    _, redacted = scrub(value)
    return redacted


__all__ = ["contains_pii", "scrub", "scrub_text"]
