"""
Unit Tests for PII Detection & Scrubber — ADR-0297

Tests for PII detection, scrubbing, and fail-closed audit logging.
"""

import pytest

from core.pii import PIIDetector, PIIScrubber, PII_PATTERNS


class TestPIIDetector:
    """Test PII detection."""

    def test_detect_email(self):
        """Email detection."""
        text = "Contact me at user@example.com"
        detected = {f.pii_class for f in PIIDetector().detect_all(text)}
        assert "email" in detected

    def test_detect_phone(self):
        """Phone number detection."""
        text = "Call me at 555-123-4567"
        detected = {f.pii_class for f in PIIDetector().detect_all(text)}
        assert "phone" in detected

    def test_detect_credit_card(self):
        """Credit card detection."""
        text = "Card: 1234-5678-9012-3456"
        detected = {f.pii_class for f in PIIDetector().detect_all(text)}
        assert "credit_card" in detected

    def test_detect_ssn(self):
        """SSN detection."""
        text = "SSN is 123-45-6789"
        detected = {f.pii_class for f in PIIDetector().detect_all(text)}
        assert "us_ssn" in detected  # class id is jurisdiction-qualified

    def test_detect_ipv4(self):
        """IPv4 address detection."""
        text = "Server at 192.168.1.1"
        detected = {f.pii_class for f in PIIDetector().detect_all(text)}
        assert "ipv4" in detected

    def test_detect_ipv6(self):
        """IPv6 address detection."""
        text = "IPv6: 2001:db8::1"
        detected = {f.pii_class for f in PIIDetector().detect_all(text)}
        assert "ipv6" in detected

    def test_detect_multiple_types(self):
        """Multiple PII types detected."""
        text = "Email: user@example.com, Phone: 555-123-4567"
        detected = {f.pii_class for f in PIIDetector().detect_all(text)}
        assert "email" in detected
        assert "phone" in detected

    def test_detect_no_pii(self):
        """Text with no PII returns empty list."""
        text = "This is safe text"
        detected = {f.pii_class for f in PIIDetector().detect_all(text)}
        assert len(detected) == 0

    def test_detect_non_string(self):
        """Non-string input returns empty list."""
        detected = PIIDetector().detect_all(123)
        assert not detected

    def test_has_pii_true(self):
        """has_pii returns True when PII present."""
        text = "Email: user@example.com"
        assert PIIDetector().has_pii(text) is True

    def test_has_pii_false(self):
        """has_pii returns False when no PII."""
        text = "Safe text"
        assert PIIDetector().has_pii(text) is False

    def test_has_pii_non_string(self):
        """has_pii returns False for non-string."""
        assert PIIDetector().has_pii(123) is False


class TestPIIScrubber:
    """Test PII scrubbing."""

    def test_scrub_email(self):
        """Email scrubbed."""
        scrubber = PIIScrubber()
        text = "Contact user@example.com"
        scrubbed = scrubber.scrub(text, log_detection=False)
        assert "user@example.com" not in scrubbed
        assert "[EMAIL]" in scrubbed

    def test_scrub_phone(self):
        """Phone number scrubbed."""
        scrubber = PIIScrubber()
        text = "Call 555-123-4567"
        scrubbed = scrubber.scrub(text, log_detection=False)
        assert "555-123-4567" not in scrubbed
        assert "[PHONE]" in scrubbed

    def test_scrub_credit_card(self):
        """Credit card scrubbed."""
        scrubber = PIIScrubber()
        text = "Card 1234-5678-9012-3456"
        scrubbed = scrubber.scrub(text, log_detection=False)
        assert "1234-5678-9012-3456" not in scrubbed
        assert "[CREDIT_CARD]" in scrubbed

    def test_scrub_ssn(self):
        """SSN scrubbed."""
        scrubber = PIIScrubber()
        text = "SSN 123-45-6789"
        scrubbed = scrubber.scrub(text, log_detection=False)
        assert "123-45-6789" not in scrubbed
        assert "[SSN]" in scrubbed

    def test_scrub_ipv4(self):
        """IPv4 scrubbed."""
        scrubber = PIIScrubber()
        text = "IP 192.168.1.1"
        scrubbed = scrubber.scrub(text, log_detection=False)
        assert "192.168.1.1" not in scrubbed
        assert "[IP]" in scrubbed

    def test_scrub_multiple_pii(self):
        """Multiple PII types scrubbed."""
        scrubber = PIIScrubber()
        text = "Email: user@example.com, Phone: 555-123-4567"
        scrubbed = scrubber.scrub(text, log_detection=False)
        assert "user@example.com" not in scrubbed
        assert "555-123-4567" not in scrubbed
        assert "[EMAIL]" in scrubbed
        assert "[PHONE]" in scrubbed

    def test_scrub_non_string(self):
        """Non-string returns unchanged."""
        scrubber = PIIScrubber()
        result = scrubber.scrub(123, log_detection=False)
        assert result == 123

    def test_scrub_audit_log_called(self):
        """Audit log function called when PII detected."""
        audit_log_calls = []

        def mock_audit(event):
            audit_log_calls.append(event)

        scrubber = PIIScrubber(audit_log_fn=mock_audit)
        text = "Email: user@example.com"
        scrubber.scrub(text, log_detection=True)

        assert len(audit_log_calls) == 1
        assert audit_log_calls[0]["event"] == "pii_detected_and_scrubbed"
        assert "email" in audit_log_calls[0]["types"]

    def test_scrub_audit_log_not_called_when_disabled(self):
        """Audit log not called when log_detection=False."""
        audit_log_calls = []

        def mock_audit(event):
            audit_log_calls.append(event)

        scrubber = PIIScrubber(audit_log_fn=mock_audit)
        text = "Email: user@example.com"
        scrubber.scrub(text, log_detection=False)

        assert len(audit_log_calls) == 0

    def test_scrub_dict_email(self):
        """Dict email scrubbed."""
        scrubber = PIIScrubber()
        data = {"user_email": "user@example.com", "name": "John"}
        scrubbed = scrubber.scrub_dict(data, log_detection=False)
        assert "[EMAIL]" in scrubbed["user_email"]
        assert scrubbed["name"] == "John"

    def test_scrub_dict_nested(self):
        """Nested dict scrubbed."""
        scrubber = PIIScrubber()
        data = {
            "user": {
                "email": "user@example.com",
                "phone": "555-123-4567",
            }
        }
        scrubbed = scrubber.scrub_dict(data, log_detection=False)
        assert "[EMAIL]" in scrubbed["user"]["email"]
        assert "[PHONE]" in scrubbed["user"]["phone"]

    def test_scrub_dict_with_list(self):
        """Dict with list scrubbed."""
        scrubber = PIIScrubber()
        data = {"emails": ["user1@example.com", "user2@example.com"]}
        scrubbed = scrubber.scrub_dict(data, log_detection=False)
        assert "[EMAIL]" in scrubbed["emails"][0]
        assert "[EMAIL]" in scrubbed["emails"][1]

    def test_should_log_raw_safe(self):
        """should_log_raw returns True for safe text."""
        scrubber = PIIScrubber()
        text = "Safe text with no PII"
        assert scrubber.should_log_raw(text) is True

    def test_should_log_raw_unsafe(self):
        """should_log_raw returns False for text with PII."""
        scrubber = PIIScrubber()
        text = "Email: user@example.com"
        assert scrubber.should_log_raw(text) is False

    def test_scrub_preserves_context(self):
        """Scrubbing preserves surrounding text."""
        scrubber = PIIScrubber()
        text = "Contact user@example.com for support"
        scrubbed = scrubber.scrub(text, log_detection=False)
        assert "Contact" in scrubbed
        assert "for support" in scrubbed
        assert "[EMAIL]" in scrubbed


class TestPIIPatterns:
    """Test PII pattern definitions."""

    def test_all_patterns_have_replacement(self):
        """All patterns have a replacement defined."""
        for name, pattern in PII_PATTERNS.items():
            assert pattern.replacement is not None
            assert "[" in pattern.replacement  # Should be in form [TYPE]

    def test_patterns_are_compiled(self):
        """All patterns are pre-compiled regex."""
        for name, pattern in PII_PATTERNS.items():
            assert hasattr(pattern.pattern, "search")  # Is a compiled regex
            assert hasattr(pattern.pattern, "sub")

    def test_pattern_count(self):
        """Minimum number of patterns defined."""
        assert len(PII_PATTERNS) >= 8  # email, phone, cc, ssn, ipv4, ipv6, passport, license
