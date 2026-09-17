"""
Creator 2.0 Input Validator (SECURITY FIX #2 — Resource Protection)

Fail-closed input validation with size limits and rate limiting.

**Protection Against:**
- Resource exhaustion attacks (huge input sizes)
- DDoS attacks (rate limiting per user)
- Memory pressure from processing

**Fail-Closed Behavior:**
- Default: deny all requests
- Only allow if within limits
- Never silently truncate or downgrade request

Usage:
    from core.skills.creator.input_validator import InputValidator, InputSizeLimitExceeded, RateLimitExceeded

    validator = InputValidator()

    try:
        validator.validate('user_123', {'skill_name': 'my_skill', 'code': '...'})
    except InputSizeLimitExceeded:
        return {'error': 'Input too large', 'status': 400}
    except RateLimitExceeded:
        return {'error': 'Rate limit exceeded', 'status': 429}

    # Safe to proceed
    return create_skill(skill_spec)
"""

from typing import Any, Dict, Set
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass


class InputSizeLimitExceeded(Exception):
    """Raised when input size exceeds configured limit"""
    pass


class RateLimitExceeded(Exception):
    """Raised when user has exceeded rate limits"""
    pass


@dataclass
class InputValidationConfig:
    """Configuration for input validation"""

    # Size limits (bytes)
    max_skill_code_size: int = 50_000  # 50 KB max
    max_skill_name_length: int = 100
    max_skill_description_length: int = 5_000
    max_request_payload_size: int = 100_000  # 100 KB total

    # Rate limits
    max_requests_per_hour: int = 100
    max_requests_per_minute: int = 10

    # Monitoring
    log_violations: bool = True


class RateLimitTracker:
    """Track requests per user for rate limiting

    In-memory implementation suitable for single-process deployments.
    For distributed systems, use Redis or similar.
    """

    def __init__(self, cleanup_interval: int = 300):
        """Initialize tracker

        Args:
            cleanup_interval: Seconds between cleanup of old entries
        """
        self.request_counts: Dict[str, list] = defaultdict(list)  # user_id → [timestamp, ...]
        self.last_cleanup = datetime.utcnow()
        self.cleanup_interval = cleanup_interval

    def _cleanup_old_entries(self, user_id: str, before: datetime) -> None:
        """Remove old entries (internal)"""
        self.request_counts[user_id] = [
            ts for ts in self.request_counts[user_id]
            if ts > before
        ]

    def record_request(self, user_id: str) -> None:
        """Record a request from user"""
        now = datetime.utcnow()
        self.request_counts[user_id].append(now)

        # Periodic cleanup to prevent unbounded memory growth
        if (now - self.last_cleanup).total_seconds() > self.cleanup_interval:
            self._cleanup_old_entries(user_id, now - timedelta(hours=1))
            self.last_cleanup = now

    def get_recent_count(self, user_id: str, minutes: int) -> int:
        """Get request count in last N minutes"""
        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=minutes)
        self._cleanup_old_entries(user_id, cutoff)
        return len(self.request_counts[user_id])

    def get_hourly_count(self, user_id: str) -> int:
        """Get request count in last hour"""
        return self.get_recent_count(user_id, 60)


class InputValidator:
    """Fail-closed input validation with limits (SECURITY FIX #2)"""

    def __init__(self, config: InputValidationConfig = None):
        """Initialize validator

        Args:
            config: Custom validation configuration (or use defaults)
        """
        self.config = config or InputValidationConfig()
        self.rate_limiter = RateLimitTracker()

    def validate(self, user_id: str, input_data: Dict[str, Any]) -> bool:
        """Validate input before processing

        Performs checks in this order (fail-closed):
        1. Total payload size
        2. Individual field sizes
        3. Rate limits (per-minute, then per-hour)

        Args:
            user_id: User identifier
            input_data: Input to validate (typically skill_spec)

        Returns:
            True if validation passes (safe to proceed)

        Raises:
            InputSizeLimitExceeded: If any size limit is exceeded
            RateLimitExceeded: If user has exceeded rate limits
        """

        # Check 1: Total payload size
        payload_size = len(str(input_data))
        if payload_size > self.config.max_request_payload_size:
            if self.config.log_violations:
                print(f"[InputValidator] Payload size {payload_size} exceeds "
                      f"limit {self.config.max_request_payload_size} (user: {user_id})")
            raise InputSizeLimitExceeded(
                f"Input size {payload_size} bytes exceeds limit "
                f"{self.config.max_request_payload_size} bytes"
            )

        # Check 2: Skill name length
        if 'name' in input_data:
            name_len = len(str(input_data['name']))
            if name_len > self.config.max_skill_name_length:
                if self.config.log_violations:
                    print(f"[InputValidator] Skill name too long: {name_len} chars "
                          f"(limit: {self.config.max_skill_name_length}, user: {user_id})")
                raise InputSizeLimitExceeded(
                    f"Skill name length {name_len} exceeds limit "
                    f"{self.config.max_skill_name_length} characters"
                )

        # Check 3: Skill code size (if present)
        if 'code' in input_data:
            code_len = len(str(input_data['code']))
            if code_len > self.config.max_skill_code_size:
                if self.config.log_violations:
                    print(f"[InputValidator] Skill code too large: {code_len} bytes "
                          f"(limit: {self.config.max_skill_code_size}, user: {user_id})")
                raise InputSizeLimitExceeded(
                    f"Skill code size {code_len} bytes exceeds limit "
                    f"{self.config.max_skill_code_size} bytes"
                )

        # Check 4: Skill description size (if present)
        if 'description' in input_data:
            desc_len = len(str(input_data['description']))
            if desc_len > self.config.max_skill_description_length:
                if self.config.log_violations:
                    print(f"[InputValidator] Skill description too long: {desc_len} chars "
                          f"(limit: {self.config.max_skill_description_length}, user: {user_id})")
                raise InputSizeLimitExceeded(
                    f"Skill description length {desc_len} exceeds limit "
                    f"{self.config.max_skill_description_length} characters"
                )

        # Check 5: Rate limiting (fail-closed: check before recording request)
        self._check_rate_limit(user_id)

        # All checks passed; record the request
        self.rate_limiter.record_request(user_id)

        return True

    def _check_rate_limit(self, user_id: str) -> None:
        """Check rate limits for user (fail-closed)

        Raises:
            RateLimitExceeded: If user has exceeded limits
        """

        # Check per-minute limit (stricter)
        minute_count = self.rate_limiter.get_recent_count(user_id, 1)
        if minute_count >= self.config.max_requests_per_minute:
            if self.config.log_violations:
                print(f"[InputValidator] Rate limit (per-minute) exceeded: "
                      f"{minute_count} >= {self.config.max_requests_per_minute} (user: {user_id})")
            raise RateLimitExceeded(
                f"User {user_id} exceeded {self.config.max_requests_per_minute} "
                f"requests/minute limit"
            )

        # Check per-hour limit
        hour_count = self.rate_limiter.get_hourly_count(user_id)
        if hour_count >= self.config.max_requests_per_hour:
            if self.config.log_violations:
                print(f"[InputValidator] Rate limit (per-hour) exceeded: "
                      f"{hour_count} >= {self.config.max_requests_per_hour} (user: {user_id})")
            raise RateLimitExceeded(
                f"User {user_id} exceeded {self.config.max_requests_per_hour} "
                f"requests/hour limit"
            )

    def get_stats(self, user_id: str) -> Dict[str, int]:
        """Get statistics for a user

        Returns:
            Dict with request counts (last minute, last hour)
        """
        return {
            'requests_last_minute': self.rate_limiter.get_recent_count(user_id, 1),
            'requests_last_hour': self.rate_limiter.get_hourly_count(user_id),
            'minute_limit': self.config.max_requests_per_minute,
            'hour_limit': self.config.max_requests_per_hour,
        }
