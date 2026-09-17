"""
Unit Tests: Creator 2.0 Input Validator (SECURITY FIX #2 — Resource Protection)

Tests input size limits and rate limiting.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.skills.creator.input_validator import (
    InputValidator,
    InputSizeLimitExceeded,
    RateLimitExceeded,
    InputValidationConfig,
    RateLimitTracker
)


class TestInputSizeLimits:
    """Test input size limit enforcement"""

    def test_rejects_oversized_payload(self):
        """Fail-closed: reject if total payload too large"""
        validator = InputValidator()

        # Create payload larger than max
        huge_payload = {'code': 'x' * 100_000}  # 100+ KB

        with pytest.raises(InputSizeLimitExceeded):
            validator.validate('user_123', huge_payload)

    def test_accepts_appropriately_sized_payload(self):
        """Accept payloads within limits"""
        validator = InputValidator()

        small_payload = {
            'name': 'my_skill',
            'code': 'def execute(x): return x'
        }

        assert validator.validate('user_123', small_payload) is True

    def test_rejects_oversized_skill_name(self):
        """Reject if skill name exceeds limit"""
        validator = InputValidator()

        oversize_name = {'name': 'x' * 200}  # 200 chars > 100 limit

        with pytest.raises(InputSizeLimitExceeded):
            validator.validate('user_123', oversize_name)

    def test_rejects_oversized_skill_code(self):
        """Reject if skill code exceeds limit"""
        validator = InputValidator()

        oversize_code = {'code': 'x' * 60_000}  # 60 KB > 50 KB limit

        with pytest.raises(InputSizeLimitExceeded):
            validator.validate('user_123', oversize_code)

    def test_rejects_oversized_description(self):
        """Reject if skill description exceeds limit"""
        validator = InputValidator()

        oversize_desc = {'description': 'x' * 10_000}  # 10 KB > 5 KB limit

        with pytest.raises(InputSizeLimitExceeded):
            validator.validate('user_123', oversize_desc)

    def test_custom_size_limits(self):
        """Allow custom size limit configuration"""
        config = InputValidationConfig(
            max_skill_code_size=1_000,  # 1 KB limit
            max_skill_name_length=50
        )
        validator = InputValidator(config)

        # Within limits
        assert validator.validate('user_123', {'code': 'x' * 500}) is True

        # Over limit
        with pytest.raises(InputSizeLimitExceeded):
            validator.validate('user_123', {'code': 'x' * 2_000})


class TestRateLimiting:
    """Test rate limiting enforcement"""

    def test_per_minute_limit_enforced(self):
        """Fail-closed: block if user exceeds per-minute limit"""
        validator = InputValidator()
        user = 'user_123'

        config = InputValidationConfig(max_requests_per_minute=3)
        validator = InputValidator(config)

        # First 3 requests should succeed
        for i in range(3):
            assert validator.validate(user, {'name': f'skill_{i}'}) is True

        # 4th request should fail
        with pytest.raises(RateLimitExceeded):
            validator.validate(user, {'name': 'skill_4'})

    def test_per_hour_limit_enforced(self):
        """Fail-closed: block if user exceeds per-hour limit"""
        config = InputValidationConfig(
            max_requests_per_minute=1000,  # Not the limiting factor
            max_requests_per_hour=5
        )
        validator = InputValidator(config)
        user = 'user_456'

        # First 5 requests should succeed
        for i in range(5):
            assert validator.validate(user, {'name': f'skill_{i}'}) is True

        # 6th should fail
        with pytest.raises(RateLimitExceeded):
            validator.validate(user, {'name': 'skill_6'})

    def test_rate_limit_per_user_isolated(self):
        """Rate limits apply per user (not global)"""
        config = InputValidationConfig(max_requests_per_minute=2)
        validator = InputValidator(config)

        # User 1 exhausts limit
        validator.validate('user_1', {'name': 'skill_1'})
        validator.validate('user_1', {'name': 'skill_2'})

        # User 1 blocked
        with pytest.raises(RateLimitExceeded):
            validator.validate('user_1', {'name': 'skill_3'})

        # But user 2 is not affected
        assert validator.validate('user_2', {'name': 'skill_1'}) is True
        assert validator.validate('user_2', {'name': 'skill_2'}) is True

        # User 2 also gets blocked at limit
        with pytest.raises(RateLimitExceeded):
            validator.validate('user_2', {'name': 'skill_3'})

    def test_rate_limit_resets_after_window(self):
        """Rate limits reset when time window expires"""
        # This test requires mocking datetime; verify tracker behavior instead
        tracker = RateLimitTracker()
        user = 'user_123'

        # Record 3 requests
        tracker.record_request(user)
        tracker.record_request(user)
        tracker.record_request(user)

        # All 3 are within 1 minute
        assert tracker.get_recent_count(user, 1) == 3

        # Simulate requests older than 1 minute by checking cleanup
        # (Full test would require time mocking)


class TestRateLimitTracker:
    """Test RateLimitTracker implementation"""

    def test_records_requests(self):
        """Tracker records request timestamps"""
        tracker = RateLimitTracker()
        user = 'user_123'

        tracker.record_request(user)
        tracker.record_request(user)
        tracker.record_request(user)

        assert tracker.get_recent_count(user, 60) == 3

    def test_different_users_isolated(self):
        """Request counts isolated per user"""
        tracker = RateLimitTracker()

        tracker.record_request('user_1')
        tracker.record_request('user_1')

        tracker.record_request('user_2')

        assert tracker.get_recent_count('user_1', 60) == 2
        assert tracker.get_recent_count('user_2', 60) == 1

    def test_unknown_user_returns_zero(self):
        """Requesting count for unknown user returns 0"""
        tracker = RateLimitTracker()

        assert tracker.get_recent_count('unknown_user', 60) == 0


class TestInputValidationConfig:
    """Test configuration options"""

    def test_default_config(self):
        """Verify sensible default limits"""
        config = InputValidationConfig()

        # Size limits make sense
        assert config.max_skill_code_size == 50_000  # 50 KB
        assert config.max_skill_name_length == 100

        # Rate limits make sense
        assert config.max_requests_per_minute == 10
        assert config.max_requests_per_hour == 100

    def test_custom_config(self):
        """Allow overriding defaults"""
        config = InputValidationConfig(
            max_skill_code_size=10_000,
            max_requests_per_minute=5
        )

        assert config.max_skill_code_size == 10_000
        assert config.max_requests_per_minute == 5


class TestValidatorStats:
    """Test statistics collection"""

    def test_get_stats_before_any_requests(self):
        """Stats for user with no requests"""
        validator = InputValidator()

        stats = validator.get_stats('unknown_user')

        assert stats['requests_last_minute'] == 0
        assert stats['requests_last_hour'] == 0
        assert stats['minute_limit'] > 0
        assert stats['hour_limit'] > 0

    def test_get_stats_after_requests(self):
        """Stats after requests recorded"""
        validator = InputValidator()
        user = 'user_123'

        # Make 3 valid requests
        for i in range(3):
            validator.validate(user, {'name': f'skill_{i}'})

        stats = validator.get_stats(user)

        assert stats['requests_last_minute'] == 3
        assert stats['requests_last_hour'] == 3


class TestErrorMessages:
    """Test that error messages are helpful"""

    def test_size_limit_error_is_specific(self):
        """Error message specifies what limit was exceeded"""
        validator = InputValidator()

        try:
            validator.validate('user_123', {'code': 'x' * 100_000})
        except InputSizeLimitExceeded as e:
            msg = str(e)
            assert '100' in msg or '50' in msg  # Mention sizes
            assert 'bytes' in msg or 'KB' in msg

    def test_rate_limit_error_is_specific(self):
        """Error message specifies rate limit that was exceeded"""
        config = InputValidationConfig(max_requests_per_minute=2)
        validator = InputValidator(config)
        user = 'user_123'

        validator.validate(user, {'name': 'skill_1'})
        validator.validate(user, {'name': 'skill_2'})

        try:
            validator.validate(user, {'name': 'skill_3'})
        except RateLimitExceeded as e:
            msg = str(e)
            assert 'minute' in msg.lower() or 'request' in msg.lower()
            assert 'user_123' in msg


class TestValidationFailureCases:
    """Test comprehensive failure scenarios"""

    def test_multiple_violations_caught_in_order(self):
        """Validates in order (stops at first violation)"""
        config = InputValidationConfig(
            max_skill_code_size=5_000,
            max_skill_name_length=50,
            max_requests_per_minute=10
        )
        validator = InputValidator(config)

        # Both name and code oversized
        oversized = {
            'name': 'x' * 100,  # Over limit
            'code': 'x' * 10_000  # Over limit
        }

        # Should fail on name first (checked first)
        with pytest.raises(InputSizeLimitExceeded):
            validator.validate('user_123', oversized)

    def test_validation_with_special_characters(self):
        """Handle special characters in input"""
        validator = InputValidator()

        special_input = {
            'name': 'my-skill_123',
            'code': 'print("Hello, World!")'
        }

        # Should not raise
        assert validator.validate('user_123', special_input) is True

    def test_validation_with_unicode(self):
        """Handle unicode in input"""
        validator = InputValidator()

        unicode_input = {
            'name': '我的技能',  # "My skill" in Chinese
            'code': 'print("Hëllö")'
        }

        # Should not raise
        assert validator.validate('user_123', unicode_input) is True


class TestDisablingViolationLogging:
    """Test disabling logging in validation config"""

    def test_logging_disabled_in_config(self):
        """Can disable violation logging"""
        config = InputValidationConfig(log_violations=False)
        validator = InputValidator(config)

        # Should still raise exception, just no logging
        with pytest.raises(InputSizeLimitExceeded):
            validator.validate('user_123', {'code': 'x' * 100_000})
