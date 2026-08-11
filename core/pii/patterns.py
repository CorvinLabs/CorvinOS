"""
PII Detection Patterns — ADR-0297

Regex patterns for detecting personally identifiable information.
Used by PIIDetector for fail-closed scrubbing.
"""

import re
from dataclasses import dataclass
from typing import Pattern


@dataclass
class PIIPattern:
    """One PII detection pattern."""

    name: str
    pattern: Pattern
    replacement: str = "[REDACTED]"


# Compiled regex patterns for PII detection
PII_PATTERNS = {
    # Email address
    "email": PIIPattern(
        name="email",
        pattern=re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        replacement="[EMAIL]",
    ),
    # Phone number (various formats)
    "phone": PIIPattern(
        name="phone",
        pattern=re.compile(r"\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b"),
        replacement="[PHONE]",
    ),
    # Credit card (16 digits)
    "credit_card": PIIPattern(
        name="credit_card",
        pattern=re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
        replacement="[CREDIT_CARD]",
    ),
    # Social Security Number (XXX-XX-XXXX)
    "ssn": PIIPattern(
        name="ssn",
        pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        replacement="[SSN]",
    ),
    # IPv4 address
    "ipv4": PIIPattern(
        name="ipv4",
        pattern=re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"),
        replacement="[IP]",
    ),
    # IPv6 address
    "ipv6": PIIPattern(
        name="ipv6",
        pattern=re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b"),
        replacement="[IPV6]",
    ),
    # Passport number (variable formats)
    "passport": PIIPattern(
        name="passport",
        pattern=re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),
        replacement="[PASSPORT]",
    ),
    # License plate (common formats)
    "license_plate": PIIPattern(
        name="license_plate",
        pattern=re.compile(r"\b[A-Z]{1,3}\s?\d{1,4}\b"),
        replacement="[LICENSE_PLATE]",
    ),
}


def detect_pii_types(text: str) -> list[str]:
    """Detect which PII types are present in text."""
    detected = []
    for name, pattern_info in PII_PATTERNS.items():
        if pattern_info.pattern.search(text):
            detected.append(name)
    return detected


def scrub_pii(text: str, patterns: dict[str, PIIPattern] = None) -> str:
    """Scrub all known PII from text using registered patterns."""
    if patterns is None:
        patterns = PII_PATTERNS

    result = text
    for pattern_info in patterns.values():
        result = pattern_info.pattern.sub(pattern_info.replacement, result)

    return result
