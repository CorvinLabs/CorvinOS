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
# "system:" at line start (any case). Whitespace between the word and the colon is
# allowed for: ``SYSTEM : ...`` read the same to a model and passed ``system:\s``.
_SYSTEM_LINE_START = re.compile(r"(?im)^\s*system\s*:")
_WS_RUN = re.compile(r"\s+")
# Description: one line of prose that is rendered into a YAML front-matter line and into
# an ``<auto_skill description="...">`` attribute.
MAX_DESCRIPTION_CHARS = 1024
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
_MAX_BODY_BYTES = 16384  # ADR-2274: raised from 8192 together with the injection cap
_CODE_FENCE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_DENSITY_WARN_THRESHOLD = 0.40


@dataclass
class LintResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ո": "n", "ս": "u",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C",
    "Х": "X", "І": "I", "Ѕ": "S", "Т": "T", "М": "M", "У": "Y",
    "ο": "o", "ν": "v", "ι": "i", "α": "a", "ρ": "p", "υ": "u",
})


def _normalise(text: str) -> str:
    """The match input: NFKC, format characters removed, confusables folded.

    Format characters (category ``Cf``: zero-width space / joiner / non-joiner, word
    joiner, soft hyphen, BOM, bidi controls, the tag block) are invisible and split a
    needle without changing what a model reads: ``ig<ZWSP>nore previous instructions``
    passed the substring scan (review R3-NEW-15). The stored text is never changed.
    """
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return text.translate(_CONFUSABLES)


def _collapsed(norm: str) -> str:
    """Lower case with every whitespace run as ONE space: ``ignore  previous`` (two
    spaces, a tab, a line break) is the same needle as ``ignore previous``."""
    return _WS_RUN.sub(" ", norm).lower()


def _looks_encoded(run: str) -> bool:
    """A long run that mixes at least three of lower / upper / digit / ``+/=`` - the
    shape of a base64 payload, unlike a long word, a hex digest or a repeated letter."""
    classes = (any(c.islower() for c in run), any(c.isupper() for c in run),
               any(c.isdigit() for c in run), any(c in "+/=" for c in run))
    return sum(classes) >= 3


def _content_errors(text: str, *, strict_base64: bool = True) -> list[str]:
    """Injection / secret / persona-boundary findings of one piece of skill text.

    ``strict_base64=False`` (the one-line description) flags a long run only when it
    looks encoded: a generated purpose can legitimately carry one long token."""
    errors: list[str] = []
    norm = _normalise(text)
    flat = _collapsed(norm)
    for needle in _INJECTION_SUBSTRINGS:
        if needle in flat:
            errors.append(f"prompt-injection: contains {needle!r}")
    if _SYSTEM_LINE_START.search(norm):
        errors.append("prompt-injection: line begins with 'system:'")
    runs = _LONG_BASE64.findall(norm)
    if runs and (strict_base64 or any(_looks_encoded(r) for r in runs)):
        errors.append(
            "prompt-injection: base64-like block of >=64 chars detected "
            "(may be encoded payload — split or move to attachment)"
        )
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text) or pattern.search(norm):
            errors.append(f"secret-leak: matched {label}")
    for needle in _PERSONA_BOUNDARY_SUBSTRINGS:
        if needle in flat:
            errors.append(f"persona-boundary: contains {needle!r}")
    return errors


def lint_description(description: str) -> LintResult:
    """Lint a skill ``description`` (review R3-NEW-15: it was never linted).

    The description is rendered into the SKILL.md front-matter and into the
    ``<auto_skill description="...">`` attribute of the injected block, so beyond the
    body's content rules it must be ONE line, carry no ``<`` / ``>`` (it could close
    the wrapper: ``x"></auto_skill>``) and no control or format characters.
    """
    errors: list[str] = []
    if not isinstance(description, str):
        return LintResult(ok=False, errors=["description: must be a string"])
    if len(description) > MAX_DESCRIPTION_CHARS:
        errors.append(
            f"description: {len(description)} chars (>{MAX_DESCRIPTION_CHARS})")
    if any(ch in description for ch in "\n\r\v\f\x85\u2028\u2029"):
        errors.append("description: must be a single line")
    if "<" in description or ">" in description:
        errors.append("description: must not contain '<' or '>'")
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs") for ch in description):
        errors.append("description: must not contain control or format characters")
    if description.strip().startswith("---"):
        errors.append("description: must not start with a front-matter delimiter")
    errors.extend(e.replace("prompt-injection:", "description: prompt-injection:")
                   .replace("secret-leak:", "description: secret-leak:")
                   .replace("persona-boundary:", "description: persona-boundary:")
                  for e in _content_errors(description, strict_base64=False))
    return LintResult(ok=not errors, errors=errors, warnings=[])


def lint(body_md: str) -> LintResult:
    """Lint a SKILL.md body. Returns LintResult; errors block create()."""
    errors: list[str] = []
    warnings: list[str] = []

    # 1-3) prompt-injection, secret leaks, persona boundary - on the normalised,
    # whitespace-collapsed match form (see ``_normalise`` / ``_collapsed``). The original
    # body_md is kept untouched for storage; only the match input is normalised.
    errors.extend(_content_errors(body_md))

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
