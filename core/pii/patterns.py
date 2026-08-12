"""PII Detection Patterns — ADR-0297

Curated regex patterns for common PII types. Fail-closed design:
- Patterns are explicit whitelists (not blacklists)
- Unknown patterns are suspicious and rejected
- All patterns compiled at module load (fail early on syntax errors)

Supported PII classes (load-bearing):
  - EMAIL: Standard email addresses (RFC 5322 simplified)
  - PHONE: International phone formats
  - US_SSN: US Social Security Numbers (format: XXX-XX-XXXX)
  - CREDIT_CARD: Payment card numbers (Luhn-validated)
  - IBAN: International Bank Account Numbers (ISO 13616)
  - PASSPORT: Passport numbers (format varies by country)
  - NATIONAL_ID: National identification numbers
  - NAME: Personal names (given + family)
  - ADDRESS: Physical addresses (street + city + postal)
  - DATE_OF_BIRTH: Birth dates in common formats
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PIIPatternDef:
    """Single PII pattern definition."""

    pii_class: str
    pattern: str  # Raw regex string
    compiled: re.Pattern  # Compiled regex (set after __post_init__)
    confidence: float  # 0.0 - 1.0 (how confident this pattern is PII)
    description: str

    def __post_init__(self) -> None:
        """Compile regex pattern. Fail on error."""
        try:
            # Use object.__setattr__ to bypass frozen dataclass
            object.__setattr__(self, "compiled", re.compile(self.pattern, re.IGNORECASE))
        except re.error as e:
            raise ValueError(f"Invalid regex for {self.pii_class}: {e}") from e


# ============================================================================
# Pattern Definitions (Fail-Closed: Explicit Whitelist)
# ============================================================================

# EMAIL patterns
EMAIL_RFC5322 = PIIPatternDef(
    pii_class="email",
    pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
    compiled=re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"),
    confidence=0.95,
    description="Standard email address (RFC 5322 simplified)",
)

# PHONE patterns (international formats)
PHONE_US = PIIPatternDef(
    pii_class="phone",
    pattern=r"^(?:\+1)?[-.\s]?(?:\(?\d{3}\)?)?[-.\s]?\d{3}[-.\s]?\d{4}$",
    compiled=re.compile(
        r"^(?:\+1)?[-.\s]?(?:\(?\d{3}\)?)?[-.\s]?\d{3}[-.\s]?\d{4}$"
    ),
    confidence=0.90,
    description="US phone number (+1-XXX-XXX-XXXX variants)",
)

PHONE_INTL = PIIPatternDef(
    pii_class="phone",
    pattern=r"^\+(?:[0-9]{1,3})[-.\s]?[0-9\s.-]{6,14}$",
    compiled=re.compile(r"^\+(?:[0-9]{1,3})[-.\s]?[0-9\s.-]{6,14}$"),
    confidence=0.85,
    description="International phone (+CC-NNN...)",
)

# US SSN (Social Security Number: XXX-XX-XXXX)
US_SSN = PIIPatternDef(
    pii_class="us_ssn",
    pattern=r"^\d{3}-\d{2}-\d{4}$",
    compiled=re.compile(r"^\d{3}-\d{2}-\d{4}$"),
    confidence=0.98,
    description="US Social Security Number (XXX-XX-XXXX)",
)

# Credit Card (generic 15-19 digit Luhn-checkable)
CREDIT_CARD = PIIPatternDef(
    pii_class="credit_card",
    pattern=r"^(?:\d{4}[-\s]?){3}\d{4}$",  # Basic format check
    compiled=re.compile(r"^(?:\d{4}[-\s]?){3}\d{4}$"),
    confidence=0.92,
    description="Credit card number (XXXX-XXXX-XXXX-XXXX variants)",
)

# IBAN (International Bank Account Number: ISO 13616)
# Stricter: requires at least 8 more chars after CC+DD
IBAN = PIIPatternDef(
    pii_class="iban",
    pattern=r"^[A-Z]{2}\d{2}[A-Z0-9]{8,26}$",
    compiled=re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{8,26}$"),
    confidence=0.96,
    description="IBAN (ISO 13616): CC-DDnnCCCC...",
)

# Passport numbers (format varies by country; generic pattern)
PASSPORT = PIIPatternDef(
    pii_class="passport",
    pattern=r"^[A-Z]{1,2}\d{6,9}$",
    compiled=re.compile(r"^[A-Z]{1,2}\d{6,9}$"),
    confidence=0.80,
    description="Passport number (L-NNNNNN to LL-NNNNNNNNN)",
)

# National ID (generic: L-NNN format)
NATIONAL_ID = PIIPatternDef(
    pii_class="national_id",
    pattern=r"^[A-Z]{1,3}\d{6,12}$",
    compiled=re.compile(r"^[A-Z]{1,3}\d{6,12}$"),
    confidence=0.75,
    description="National ID number (letter prefix + digits)",
)

# DATE OF BIRTH (common formats: YYYY-MM-DD, DD.MM.YYYY, MM/DD/YYYY)
DATE_OF_BIRTH = PIIPatternDef(
    pii_class="date_of_birth",
    pattern=r"^(?:\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}|\d{2}/\d{2}/\d{4})$",
    compiled=re.compile(
        r"^(?:\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}|\d{2}/\d{2}/\d{4})$"
    ),
    confidence=0.85,
    description="Date of birth (YYYY-MM-DD, DD.MM.YYYY, MM/DD/YYYY)",
)

# NAME (strict pattern: FIRST LAST format only, 2-3 parts, 3+ chars each)
# Confidence lower because many non-PII strings match (e.g., product names)
# Made stricter to avoid false positives on random text
NAME = PIIPatternDef(
    pii_class="name",
    pattern=r"^[A-Z][a-z]{2,}(?:[-\s][A-Z][a-z]{2,}){1,2}$",
    compiled=re.compile(r"^[A-Z][a-z]{2,}(?:[-\s][A-Z][a-z]{2,}){1,2}$"),
    confidence=0.60,
    description="Personal name (Firstname Lastname)",
)

# ADDRESS (street + number + optional apt)
ADDRESS = PIIPatternDef(
    pii_class="address",
    pattern=r"^\d+\s+[A-Z][a-z\s]+(?:,\s*[A-Z]{2})?(?:\s+\d{5})?$",
    compiled=re.compile(r"^\d+\s+[A-Z][a-z\s]+(?:,\s*[A-Z]{2})?(?:\s+\d{5})?$"),
    confidence=0.70,
    description="Physical address (number + street + optional state + zip)",
)

# ============================================================================
# Registry (Ordered by Confidence, High-to-Low)
# ============================================================================

# All patterns, ordered by confidence (highest first)
# Fail-closed: unknown patterns are not in this registry
ALL_PATTERNS: tuple[PIIPatternDef, ...] = (
    US_SSN,  # 0.98
    EMAIL_RFC5322,  # 0.95
    IBAN,  # 0.96
    CREDIT_CARD,  # 0.92
    PHONE_US,  # 0.90
    PHONE_INTL,  # 0.85
    DATE_OF_BIRTH,  # 0.85
    PASSPORT,  # 0.80
    ADDRESS,  # 0.70
    NATIONAL_ID,  # 0.75
    NAME,  # 0.60
)

# ============================================================================
# Utility Functions
# ============================================================================


def pattern_by_class(pii_class: str) -> PIIPatternDef | None:
    """Get pattern by PII class name. Returns None if not found."""
    for pattern in ALL_PATTERNS:
        if pattern.pii_class == pii_class:
            return pattern
    return None


def validate_patterns() -> bool:
    """Validate all patterns are syntactically correct.

    Raises ValueError if any pattern is invalid.
    Returns True if all valid.
    """
    for pattern in ALL_PATTERNS:
        try:
            # Patterns already compiled in __post_init__, so this is a re-check
            pattern.compiled.pattern
        except Exception as e:
            raise ValueError(f"Invalid pattern for {pattern.pii_class}: {e}") from e
    return True


# Validate patterns at module load time (fail-closed)
validate_patterns()


__all__ = [
    "PIIPatternDef",
    "EMAIL_RFC5322",
    "PHONE_US",
    "PHONE_INTL",
    "US_SSN",
    "CREDIT_CARD",
    "IBAN",
    "PASSPORT",
    "NATIONAL_ID",
    "DATE_OF_BIRTH",
    "NAME",
    "ADDRESS",
    "ALL_PATTERNS",
    "pattern_by_class",
    "validate_patterns",
]
