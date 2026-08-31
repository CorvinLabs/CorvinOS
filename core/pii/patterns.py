"""
PII Detection Patterns — ADR-0297

Regex patterns for detecting personally identifiable information.
Used by PIIDetector for fail-closed scrubbing.

Patterns cover:
- Email (RFC5322)
- Phone numbers (US + International)
- SSN (Social Security Number)
- Credit cards (Visa, Mastercard, Amex, Discover)
- IBAN (International Bank Account Number)
- Passport numbers
- National IDs
- Date of Birth
- Names (high-confidence)
- Addresses
- API keys / secrets
- SQL passwords
"""

import re
from dataclasses import dataclass
from typing import Pattern, Optional


@dataclass
class PIIPattern:
    """One PII detection pattern with confidence metadata."""

    name: str
    pattern: Pattern
    replacement: str = "[REDACTED]"
    confidence: float = 0.85  # Default confidence (0.0-1.0)
    description: str = ""


# ============================================================================
# EMAIL PATTERNS
# ============================================================================

EMAIL_RFC5322 = re.compile(
    r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\b"
)

# ============================================================================
# PHONE PATTERNS
# ============================================================================

# US phone numbers: (555) 123-4567, 555-123-4567, +1-555-123-4567, etc.
PHONE_US = re.compile(
    r"\b(?:\+1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b"
)

# International phone: +CC-NNN-NNNN or similar
PHONE_INTL = re.compile(
    r"\b\+(?:[0-9]{1,3})[-.\s]?(?:[0-9]{1,4})[-.\s]?(?:[0-9]{1,4})[-.\s]?(?:[0-9]{1,9})\b"
)

# ============================================================================
# FINANCIAL PATTERNS
# ============================================================================

# US Social Security Number: XXX-XX-XXXX
US_SSN = re.compile(r"\b(?!000|666|9\d{2})[0-9]{3}-(?!00)[0-9]{2}-(?!0000)[0-9]{4}\b")

# Credit card: 16-digit, 15-digit (Amex), etc. with separators
CREDIT_CARD = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b|\b\d{15}\b|\b\d{14}\b")

# IBAN: Starts with 2-letter country code + 2 check digits + alphanumeric
IBAN = re.compile(
    r"\b[A-Z]{2}(?:\d{2})[A-Z0-9]{1,30}\b"
)

# ============================================================================
# IDENTIFICATION PATTERNS
# ============================================================================

# Passport: 1-2 letters + 6-9 digits (variable by country)
PASSPORT = re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")

# National ID (variable by country, examples):
# DE: 10 digits (Personalausweis)
# IT: 2 letters + 6 digits + 1 letter + 3 digits
# FR: 13 digits
NATIONAL_ID = re.compile(r"\b[A-Z]{2}\d{8,}\b|\b\d{13}\b")

# Date of Birth (YYYY-MM-DD or similar)
DATE_OF_BIRTH = re.compile(
    r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b"
)

# ============================================================================
# NAME/ADDRESS PATTERNS
# ============================================================================

# Full name pattern: Capital Letter(s) + space + Capital Letter(s)
# High confidence when combined with other signals
NAME = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\b")

# Address: street + number (e.g., "123 Main Street" or "Hauptstraße 42")
ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z\s]+(?:Street|Str\.|Straße|Avenue|Ave\.|Road|Rd\.|Boulevard|Blvd\.|Drive|Dr\.|Lane|Ln\.|Court|Ct\.|Circle|Cir\.)?)\b"
)

# ============================================================================
# SECRET/API KEY PATTERNS
# ============================================================================

# AWS secret key format: AKIA... + 16 alphanumeric
AWS_ACCESS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

# Generic API key pattern: key= followed by hex or alphanumeric (common in URLs)
API_KEY = re.compile(r"(?:api[_-]?key|apikey|access[_-]?token)[\s=:]*([A-Za-z0-9]{20,})")

# ============================================================================
# DATABASE PATTERNS
# ============================================================================

# SQL password in connection string: password=... or pwd=...
SQL_PASSWORD = re.compile(
    r"(?:password|pwd|passwd)[\s]*=[\s]*['\"]?([^'\"\s;]+)['\"]?"
)

# ============================================================================
# IPv4 and IPv6
# ============================================================================

IPv4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b")

IPv6 = re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b")

# ============================================================================
# COMPILED PATTERNS DICTIONARY
# ============================================================================

PII_PATTERNS = {
    # Email - very high confidence
    "email": PIIPattern(
        name="email",
        pattern=EMAIL_RFC5322,
        replacement="[EMAIL]",
        confidence=0.92,
        description="Email address (RFC5322)"
    ),
    # Phone - high confidence
    "phone": PIIPattern(
        name="phone",
        pattern=PHONE_US,
        replacement="[PHONE]",
        confidence=0.88,
        description="US phone number"
    ),
    "phone_intl": PIIPattern(
        name="phone_intl",
        pattern=PHONE_INTL,
        replacement="[PHONE_INTL]",
        confidence=0.85,
        description="International phone number"
    ),
    # SSN - very high confidence
    "us_ssn": PIIPattern(
        name="us_ssn",
        pattern=US_SSN,
        replacement="[SSN]",
        confidence=0.98,
        description="US Social Security Number"
    ),
    # Credit card - high confidence
    "credit_card": PIIPattern(
        name="credit_card",
        pattern=CREDIT_CARD,
        replacement="[CREDIT_CARD]",
        confidence=0.90,
        description="Credit card number"
    ),
    # IBAN - high confidence
    "iban": PIIPattern(
        name="iban",
        pattern=IBAN,
        replacement="[IBAN]",
        confidence=0.96,
        description="International Bank Account Number"
    ),
    # Passport - medium-high confidence
    "passport": PIIPattern(
        name="passport",
        pattern=PASSPORT,
        replacement="[PASSPORT]",
        confidence=0.80,
        description="Passport number"
    ),
    # National ID - medium-high confidence
    "national_id": PIIPattern(
        name="national_id",
        pattern=NATIONAL_ID,
        replacement="[NATIONAL_ID]",
        confidence=0.75,
        description="National ID number"
    ),
    # Date of birth - high confidence
    "date_of_birth": PIIPattern(
        name="date_of_birth",
        pattern=DATE_OF_BIRTH,
        replacement="[DOB]",
        confidence=0.85,
        description="Date of birth"
    ),
    # Name - medium confidence (high false positive rate)
    "name": PIIPattern(
        name="name",
        pattern=NAME,
        replacement="[NAME]",
        confidence=0.60,
        description="Personal name"
    ),
    # Address - medium confidence
    "address": PIIPattern(
        name="address",
        pattern=ADDRESS,
        replacement="[ADDRESS]",
        confidence=0.70,
        description="Street address"
    ),
    # AWS access key - very high confidence
    "aws_access_key": PIIPattern(
        name="aws_access_key",
        pattern=AWS_ACCESS_KEY,
        replacement="[AWS_KEY]",
        confidence=0.99,
        description="AWS access key"
    ),
    # API key - high confidence
    "api_key": PIIPattern(
        name="api_key",
        pattern=API_KEY,
        replacement="[API_KEY]",
        confidence=0.87,
        description="API key / token"
    ),
    # SQL password - high confidence
    "sql_password": PIIPattern(
        name="sql_password",
        pattern=SQL_PASSWORD,
        replacement="[PASSWORD]",
        confidence=0.89,
        description="SQL password in connection string"
    ),
    # IPv4 - medium confidence (many false positives in version numbers, etc.)
    "ipv4": PIIPattern(
        name="ipv4",
        pattern=IPv4,
        replacement="[IP]",
        confidence=0.65,
        description="IPv4 address"
    ),
    # IPv6 - high confidence
    "ipv6": PIIPattern(
        name="ipv6",
        pattern=IPv6,
        replacement="[IPV6]",
        confidence=0.90,
        description="IPv6 address"
    ),
}


# ============================================================================
# DETECTION FUNCTIONS
# ============================================================================

def detect_pii_types(text: str) -> list[str]:
    """Detect which PII types are present in text.

    Args:
        text: Text to scan for PII

    Returns:
        List of detected PII type names
    """
    if not isinstance(text, str):
        return []

    detected = []
    for name, pattern_info in PII_PATTERNS.items():
        if pattern_info.pattern.search(text):
            detected.append(name)
    return detected


def scrub_pii(text: str, patterns: dict[str, PIIPattern] = None) -> str:
    """Scrub all known PII from text using registered patterns.

    Args:
        text: Text to scrub
        patterns: Custom patterns dict (uses PII_PATTERNS if None)

    Returns:
        Text with PII replaced with redaction markers
    """
    if not isinstance(text, str):
        return text

    if patterns is None:
        patterns = PII_PATTERNS

    result = text
    for pattern_info in patterns.values():
        result = pattern_info.pattern.sub(pattern_info.replacement, result)

    return result
