"""PII Scrubber — CRITICAL Phase 1 Task 1.4b (Adversarial Review Finding).

Detects and redacts PII before voice synthesis (GDPR Art. 32).
Patterns: password, API key, email, SSN.

Fail-closed: if PII detected → text-only summary (no voice).
Audit-emitted: PIIScrubEvent if patterns found.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Tuple

_log = logging.getLogger("core.notification.pii_scrubber")


@dataclass
class ScrubResult:
    """Result of PII scrubbing."""
    text: str                       # Scrubbed text
    pii_detected: bool             # Whether any PII was found
    patterns_found: list[str]       # Which patterns matched
    redacted_length: int           # Total length of redacted text


class PIIScrubber:
    """Detect and redact PII patterns from text."""

    # Regex patterns for common PII
    PATTERNS = {
        "password": (
            r"(?i)(?:password|passwd|pwd|pass)\s*[:=]\s*[^\s\n]+",
            "[REDACTED_PASSWORD]",
        ),
        "api_key": (
            r"(?i)(?:api[_-]?key|apikey|api_secret|secret[_-]?key|sk_)\s*[:=]?\s*[A-Za-z0-9_\-]+",
            "[REDACTED_API_KEY]",
        ),
        "email": (
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            "[REDACTED_EMAIL]",
        ),
        "ssn": (
            r"\b\d{3}-\d{2}-\d{4}\b",
            "[REDACTED_SSN]",
        ),
        "credit_card": (
            r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
            "[REDACTED_CC]",
        ),
        "token": (
            r"(?i)(?:token|bearer|auth)\s*[:=]\s*[A-Za-z0-9_\-\.]+",
            "[REDACTED_TOKEN]",
        ),
    }

    def scrub(self, text: str) -> ScrubResult:
        """Scrub PII from text.

        Args:
            text: Input text to scrub

        Returns:
            ScrubResult with scrubbed text, detection status, patterns found
        """
        if not text:
            return ScrubResult(text="", pii_detected=False, patterns_found=[], redacted_length=0)

        scrubbed = text
        detected_patterns = []
        total_redacted = 0

        for pattern_name, (pattern_regex, replacement) in self.PATTERNS.items():
            try:
                # Find all matches
                matches = list(re.finditer(pattern_regex, text))
                if matches:
                    detected_patterns.append(pattern_name)
                    for match in matches:
                        total_redacted += len(match.group(0)) - len(replacement)

                    # Replace in scrubbed text
                    scrubbed = re.sub(pattern_regex, replacement, scrubbed)

            except re.error as e:
                _log.error(f"Regex error for pattern {pattern_name}: {e}")

        pii_detected = len(detected_patterns) > 0

        if pii_detected:
            _log.info(f"PII detected: {detected_patterns}")

        return ScrubResult(
            text=scrubbed,
            pii_detected=pii_detected,
            patterns_found=detected_patterns,
            redacted_length=total_redacted,
        )

    def contains_pii(self, text: str) -> bool:
        """Quick check: does text contain any PII pattern?

        Used to block voice synthesis before TTS call.
        """
        result = self.scrub(text)
        return result.pii_detected


# Global instance
_scrubber = PIIScrubber()


def scrub_text(text: str) -> ScrubResult:
    """Module-level convenience function."""
    return _scrubber.scrub(text)


def contains_pii(text: str) -> bool:
    """Module-level convenience function."""
    return _scrubber.contains_pii(text)
