"""Hybrid workload classifier — ADR-0043.

Classifies user messages into CHAT vs. CODE workloads using heuristic patterns
(engine-agnostic). Classification result is consumed by engine_models.py to
resolve the appropriate model tier for the target engine.

Scope: Chat (conversational, explanatory) → fast model tier of the engine;
Code (write/review code, architecture, debugging with artifacts) → full capability.

No LLM involved in classification; pure regex on code keywords.
"""
from __future__ import annotations

import re
import unicodedata
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


# Compiled regex patterns for code keywords (case-insensitive).
# Used to compute a "code score" — ratio of matched keywords to total tokens.
# NOTE: Avoid common English words (let, while, return, for-in). Prefer language-specific
# patterns (def, class, import, async, await, lambda, function, => in correct context).
_CODE_PATTERNS = [
    r'\bdef\b',                    # Python function (no false pos: not English)
    r'\bclass\b',                  # Python class (no false pos)
    r'\bimport\b',                 # Python import (no false pos)
    r'\bfrom\s+\w+\s+import\b',    # Python from-import (no false pos)
    r'\basync\s+(?:def|function)',  # Async def/function (context required, avoid "async task")
    r'\bawait\b',                  # Python/JS await (rare in English)
    r'\blambda\b',                 # Python lambda (no false pos: not English)
    r'\bfunction\s*\(|\bfunction\s*\{',  # JS function with parens/brace (context)
    r'=>',                         # Arrow function (just =>, no word bounds — catches () => {} )
    r'\btry\s*[:({]',             # Python try: or JS try { (context)
    r'\bexcept\b',                # Python except (no false pos)
    r'\bfinally\b',               # Exception handling (no false pos)
    r'\bthrow\b',                 # Exception throwing (no false pos)
    r'\bif\s+__name__\b',         # Python main guard (no false pos)
    r'```',                       # Code fence (markdown, no false pos)
    r'\bfunction\*',              # JS generator (no false pos)
    r'\binterface\s+\w+',         # TypeScript interface (no false pos)
    r'\btype\s+\w+\s*=',          # TypeScript type (no false pos)
]

_COMPILED_PATTERNS = [
    re.compile(pat, re.IGNORECASE | re.DOTALL) for pat in _CODE_PATTERNS
]


def _count_code_keywords(message: str) -> int:
    """Count how many code keyword patterns match in the message."""
    count = 0
    for pattern in _COMPILED_PATTERNS:
        # Count all non-overlapping matches
        count += len(pattern.findall(message))
    return count


def classify_workload(
    message: str,
    confidence_threshold: float = 0.5,
) -> ClassificationResult:
    """Classify a user message into CHAT, CODE, or UNCERTAIN.

    Args:
        message: The user's input message (any length)
        confidence_threshold: Confidence bounds for uncertain classification

    Returns:
        ClassificationResult with workload type and confidence ∈ [0.0, 1.0]

    Logic:
      - Normalize Unicode (NFC) to prevent combining-mark evasion (e.g., "d​ef" → "def")
      - Count code keyword matches (regex patterns)
      - Compute code score = matches / max(total_words, 1)
      - Dual criteria for CODE classification:
        (a) Short message (< 20 tokens) AND has >= 1 code keyword → CODE
        (b) Long message AND code_score > 0.5 → CODE
      - Otherwise: CHAT
      - Confidence = code_score if CODE, else 1-code_score
      - Only return definitive if confidence >= threshold; else UNCERTAIN
    """
    if not message or not isinstance(message, str):
        return ClassificationResult(WorkloadType.UNCERTAIN, 0.0)

    # Normalize Unicode to NFC form (prevents evasion via combining marks / zero-width chars)
    message = unicodedata.normalize("NFC", message)

    # Tokenize roughly: split on whitespace, count non-empty tokens
    tokens = message.split()
    total_tokens = max(len(tokens), 1)

    # Count code keyword matches
    code_keyword_count = _count_code_keywords(message)

    # Compute code score (ratio of code patterns to tokens, clamped to [0, 1])
    code_score = min(1.0, code_keyword_count / total_tokens)

    # Dual criteria for code classification:
    # Short messages: if we see ANY code keywords, lean CODE
    # Long messages: need substantial code density (> 0.5)
    is_code = (
        (total_tokens < 20 and code_keyword_count >= 1)  # Short + has code
        or (total_tokens >= 20 and code_score > 0.5)      # Long + dense code
    )

    if is_code:
        workload = WorkloadType.CODE
        confidence = code_score
    else:
        workload = WorkloadType.CHAT
        confidence = 1.0 - code_score

    # Apply confidence threshold: only return definitive if above threshold
    if confidence < confidence_threshold:
        return ClassificationResult(WorkloadType.UNCERTAIN, confidence)

    return ClassificationResult(workload, confidence)
