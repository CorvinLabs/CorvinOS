"""
Data Flow Classifier — Detects PII, sensitive, and public data.

Classifies user data into security categories to inform flow policies.
Used by Flow Guard Skill (ADR-2032) to make allow/deny decisions.

Examples:
  >>> classifier = DataClassifier()
  >>> result = classifier.classify("user@example.com")
  >>> result.data_class  # "personal_email"
  >>> result.confidence  # 0.98
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from abc import ABC, abstractmethod


class DataClassification(str, Enum):
    """Data classification taxonomy (fail-closed)."""

    # High-risk: never allow to external engines
    CREDENTIALS = "credentials"  # API keys, passwords, tokens
    ENCRYPTION_KEY = "encryption_key"  # Private keys, master keys
    FINANCIAL_ACCOUNT = "financial_account"  # Bank accounts, credit cards
    HEALTH_RECORD = "health_record"  # Medical data, diagnoses

    # Medium-risk: allow only with consent + audit
    PERSONAL_EMAIL = "personal_email"  # Email addresses
    PHONE_NUMBER = "phone_number"  # Phone numbers
    SSN = "ssn"  # Social Security Numbers
    HOME_ADDRESS = "home_address"  # Physical addresses
    FINANCIAL_DATA = "financial_data"  # Transaction history, balances
    BIOMETRIC_DATA = "biometric_data"  # Fingerprints, face scans

    # Low-risk: generally safe
    BUSINESS_EMAIL = "business_email"  # Company emails
    GENERIC_ID = "generic_id"  # Non-sensitive IDs (user_id, order_id)
    PUBLIC_URL = "public_url"  # Public websites
    METADATA = "metadata"  # Timestamps, counts, categories

    # Fallback: unknown/ambiguous
    UNKNOWN = "unknown"


@dataclass
class ClassificationResult:
    """Result of a data classification attempt."""

    data_class: DataClassification
    confidence: float  # 0.0–1.0; < 0.5 = uncertain, >= 0.5 = confident
    reasoning: str  # Human-readable explanation
    evidence: Optional[str] = None  # Matched pattern, if any


class PatternDetector(ABC):
    """Base class for pattern-based data classifiers."""

    @abstractmethod
    def detect(self, data: str) -> Optional[ClassificationResult]:
        """Detect if data matches this pattern. Return None if no match."""
        pass


class CredentialsDetector(PatternDetector):
    """Detects API keys, tokens, passwords."""

    # Patterns for common credential types
    PATTERNS = [
        # AWS credentials (AKIA... or contains _SECRET_KEY_)
        (r"AKIA[0-9A-Z]{16}", "AWS access key", 0.95),
        (r"aws_secret_access_key\s*=", "AWS secret", 0.98),
        # Generic API tokens (Bearer, token=, api_key=)
        (r"Bearer\s+[A-Za-z0-9\-._~\+\/]+=*", "Bearer token", 0.90),
        (r"api[_-]?key\s*=\s*[A-Za-z0-9\-_.]{32,}", "API key", 0.85),
        # Private keys (BEGIN PRIVATE KEY, BEGIN RSA PRIVATE KEY)
        (r"-----BEGIN.*PRIVATE KEY-----", "Private key", 0.99),
        # Passwords (common patterns: password=, passwd:)
        (r"password\s*[:=]\s*[^\s]{8,}", "Password", 0.70),
    ]

    def detect(self, data: str) -> Optional[ClassificationResult]:
        data_lower = data.lower()
        for pattern, name, confidence in self.PATTERNS:
            if re.search(pattern, data, re.IGNORECASE):
                return ClassificationResult(
                    data_class=DataClassification.CREDENTIALS,
                    confidence=confidence,
                    reasoning=f"Matched {name} pattern",
                    evidence=name,
                )
        return None


class EmailDetector(PatternDetector):
    """Detects personal vs. business emails."""

    EMAIL_PATTERN = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    BUSINESS_DOMAINS = {
        "company",
        "corp",
        "business",
        "b2b",
        "enterprise",
        "work",
        "office",
    }
    PERSONAL_INDICATORS = {"gmail", "yahoo", "hotmail", "aol", "protonmail"}

    def detect(self, data: str) -> Optional[ClassificationResult]:
        email_match = re.search(self.EMAIL_PATTERN, data)
        if not email_match:
            return None

        email = email_match.group(0)
        domain = email.split("@")[1].lower()

        # Check for personal indicators
        if any(personal in domain for personal in self.PERSONAL_INDICATORS):
            return ClassificationResult(
                data_class=DataClassification.PERSONAL_EMAIL,
                confidence=0.95,
                reasoning="Personal email provider detected",
                evidence=domain,
            )

        # Check for business indicators
        if any(business in domain for business in self.BUSINESS_DOMAINS):
            return ClassificationResult(
                data_class=DataClassification.BUSINESS_EMAIL,
                confidence=0.90,
                reasoning="Business domain detected",
                evidence=domain,
            )

        # Ambiguous domain — classify as business (more conservative)
        return ClassificationResult(
            data_class=DataClassification.BUSINESS_EMAIL,
            confidence=0.60,
            reasoning="Non-personal email provider",
            evidence=domain,
        )


class PhoneNumberDetector(PatternDetector):
    """Detects phone numbers (US format + international)."""

    PATTERNS = [
        # US: (123) 456-7890, 123-456-7890, 123.456.7890, 1234567890
        (r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b", "US phone", 0.85),
        # International: +CC NNNNN...
        (r"\+\d{1,3}[-.\s]?\d{1,14}", "International phone", 0.80),
    ]

    def detect(self, data: str) -> Optional[ClassificationResult]:
        for pattern, name, confidence in self.PATTERNS:
            if re.search(pattern, data):
                return ClassificationResult(
                    data_class=DataClassification.PHONE_NUMBER,
                    confidence=confidence,
                    reasoning=f"Matched {name} pattern",
                    evidence=name,
                )
        return None


class SSNDetector(PatternDetector):
    """Detects US Social Security Numbers."""

    PATTERN = r"\b\d{3}-\d{2}-\d{4}\b"

    def detect(self, data: str) -> Optional[ClassificationResult]:
        if re.search(self.PATTERN, data):
            return ClassificationResult(
                data_class=DataClassification.SSN,
                confidence=0.95,
                reasoning="Matched SSN format (XXX-XX-XXXX)",
                evidence="SSN pattern",
            )
        return None


class URLDetector(PatternDetector):
    """Detects URLs and classifies as public if well-known."""

    PUBLIC_DOMAINS = {
        "google.com",
        "github.com",
        "stackoverflow.com",
        "wikipedia.org",
        "reddit.com",
        "youtube.com",
        "facebook.com",
        "twitter.com",
        "linkedin.com",
    }

    PATTERN = r"https?://[^\s]+"

    def detect(self, data: str) -> Optional[ClassificationResult]:
        url_match = re.search(self.PATTERN, data)
        if not url_match:
            return None

        url = url_match.group(0)
        try:
            domain = url.split("://")[1].split("/")[0].lower()
            if any(public_domain in domain for public_domain in self.PUBLIC_DOMAINS):
                return ClassificationResult(
                    data_class=DataClassification.PUBLIC_URL,
                    confidence=0.95,
                    reasoning="Public domain detected",
                    evidence=domain,
                )
            # Non-public URL — could be internal/sensitive
            return ClassificationResult(
                data_class=DataClassification.UNKNOWN,
                confidence=0.40,
                reasoning="Non-public URL detected",
                evidence=domain,
            )
        except Exception:
            return ClassificationResult(
                data_class=DataClassification.UNKNOWN,
                confidence=0.30,
                reasoning="Could not parse URL",
                evidence=url,
            )


class DataClassifier:
    """Main classifier orchestrating multiple pattern detectors."""

    def __init__(self):
        """Initialize with all pattern detectors."""
        self.detectors: list[PatternDetector] = [
            CredentialsDetector(),
            SSNDetector(),
            PhoneNumberDetector(),
            EmailDetector(),
            URLDetector(),
        ]

    def classify(self, data: str, context: Optional[dict] = None) -> ClassificationResult:
        """
        Classify data into a security category.

        Args:
            data: The data string to classify
            context: Optional context (e.g., {"field_name": "password", "user_id": "123"})

        Returns:
            ClassificationResult with data class, confidence, and reasoning

        Raises:
            ValueError: If data is None or empty
        """
        if not data or not isinstance(data, str):
            raise ValueError("Data must be a non-empty string")

        # Try all detectors in order
        for detector in self.detectors:
            result = detector.detect(data)
            if result:
                return result

        # Fallback: classify as unknown (conservative)
        return ClassificationResult(
            data_class=DataClassification.UNKNOWN,
            confidence=0.20,
            reasoning="No patterns matched; data type unknown",
            evidence=None,
        )

    def classify_bulk(
        self, data_list: list[str], context: Optional[dict] = None
    ) -> list[ClassificationResult]:
        """
        Classify multiple data items.

        Args:
            data_list: List of data strings
            context: Optional context shared across all items

        Returns:
            List of ClassificationResults in same order as input
        """
        return [self.classify(item, context) for item in data_list]
