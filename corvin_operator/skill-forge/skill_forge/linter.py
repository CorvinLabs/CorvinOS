"""Skill markdown linter — fail-closed for prompt-injection / secrets / persona-boundary,
warning-only for length & code-density.

Errors block writes; warnings only get logged. The substring/regex set is a
deliberate STARTER set — false positives are accepted; the alternative is a
silent leak past the gate.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


# 1) prompt-injection scan (case-insensitive substring)
_INJECTION_SUBSTRINGS: tuple[str, ...] = (
    "ignore previous instructions",
    "disregard the above",
    "you are now",
    "<|im_start|>",
    "<|im_end|>",
)
# system: at line start (any case)
_SYSTEM_LINE_START = re.compile(r"(?im)^\s*system:\s")
# Base64-ish run of >=64 chars. False positives accepted (sha256-hex is fine
# at 64 chars but won't be triggered because hex has no '+', '/', '=' — only
# real base64 blobs hit this).
_LONG_BASE64 = re.compile(r"[A-Za-z0-9+/=]{64,}")

# 2) secret-leak scan
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-pat",     re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("anthropic-key",  re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("private-key-pem",
     re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----")),
)

# 3) persona-boundary check — phrases that try to widen capabilities
_PERSONA_BOUNDARY_SUBSTRINGS: tuple[str, ...] = (
    "you can now use bash",
    "bypass permissions",
    "--dangerously-skip-permissions",
    "you may execute",
)

# 4) length & density
_MAX_BODY_BYTES = 8192
_CODE_FENCE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_DENSITY_WARN_THRESHOLD = 0.40


@dataclass
class LintResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def lint(body_md: str) -> LintResult:
    """Lint a SKILL.md body. Returns LintResult; errors block create()."""
    errors: list[str] = []
    warnings: list[str] = []

    # Normalise to NFKC + lowercase BEFORE pattern-matching so a
    # confusable like "іgnоrе previous" (cyrillic i/o/e) collapses to
    # the ASCII form. We also fold a few common cyrillic confusables
    # to their Latin look-alikes — confusables aren't covered by NFKC.
    # The original body_md is kept untouched for storage; only the
    # match-input is normalised.
    body_norm = unicodedata.normalize("NFKC", body_md)
    _CONFUSABLES = str.maketrans({
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
        "у": "y", "х": "x", "і": "i", "ј": "j",
        "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C",
        "Х": "X", "І": "I",
    })
    body_norm = body_norm.translate(_CONFUSABLES)
    body_lower = body_norm.lower()

    # 1) prompt-injection (run on normalised body, but record original
    # for the error message so the user sees what they wrote)
    for needle in _INJECTION_SUBSTRINGS:
        if needle in body_lower:
            errors.append(f"prompt-injection: contains {needle!r}")
    if _SYSTEM_LINE_START.search(body_norm):
        errors.append("prompt-injection: line begins with 'system:'")
    if _LONG_BASE64.search(body_norm):
        errors.append(
            "prompt-injection: base64-like block of >=64 chars detected "
            "(may be encoded payload — split or move to attachment)"
        )

    # 2) secret leaks
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(body_md):
            errors.append(f"secret-leak: matched {label}")

    # 3) persona-boundary
    for needle in _PERSONA_BOUNDARY_SUBSTRINGS:
        if needle in body_lower:
            errors.append(f"persona-boundary: contains {needle!r}")

    # 4a) length (errors)
    body_bytes = len(body_md.encode("utf-8"))
    if body_bytes > _MAX_BODY_BYTES:
        errors.append(
            f"length: body is {body_bytes} bytes (>{_MAX_BODY_BYTES}); "
            f"split into multiple skills"
        )

    # 4b) code density (warning only)
    if body_md:
        code_chars = sum(len(m.group(0)) for m in _CODE_FENCE_RE.finditer(body_md))
        ratio = code_chars / max(1, len(body_md))
        if ratio > _DENSITY_WARN_THRESHOLD:
            warnings.append(
                f"density: {ratio*100:.0f}% of body is fenced code; "
                f"consider a forge tool instead of a skill"
            )

    return LintResult(ok=not errors, errors=errors, warnings=warnings)
