"""Token Bucket Rate Limiter — DoS Protection for Webhooks (GH-002, Finding 2).

Implements per-tenant rate limiting for webhook processing to prevent
DoS attacks via webhook flooding. Uses token bucket algorithm.
"""

import time
import logging
from typing import Dict, Optional
from threading import Lock

logger = logging.getLogger(__name__)


class RateLimiter:
    """Token bucket rate limiter for webhook processing."""

    def __init__(self, rate: float = 100.0, capacity: float = 100.0):
        """Initialize rate limiter.

        Args:
            rate: Tokens per second (events/sec allowed)
            capacity: Maximum tokens in bucket (burst capacity)
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens: Dict[str, float] = {}  # tenant_id -> tokens
        self.last_update: Dict[str, float] = {}  # tenant_id -> timestamp
        self.lock = Lock()

    def allow(self, tenant_id: str, tokens_required: int = 1) -> bool:
        """Check if request is allowed under rate limit.

        Args:
            tenant_id: Tenant making the request
            tokens_required: Number of tokens needed (default 1)

        Returns:
            True if allowed, False if rate limit exceeded
        """
        with self.lock:
            now = time.time()

            # Initialize tenant if not seen before
            if tenant_id not in self.tokens:
                self.tokens[tenant_id] = self.capacity
                self.last_update[tenant_id] = now
                return True

            # Refill tokens based on time elapsed
            last = self.last_update[tenant_id]
            elapsed = max(0, now - last)
            refill = elapsed * self.rate
            self.tokens[tenant_id] = min(self.capacity, self.tokens[tenant_id] + refill)
            self.last_update[tenant_id] = now

            # Check if enough tokens available
            if self.tokens[tenant_id] >= tokens_required:
                self.tokens[tenant_id] -= tokens_required
                return True

            return False

    def get_status(self, tenant_id: str) -> Dict[str, float]:
        """Get rate limit status for a tenant.

        Returns:
            Dict with: tokens_available, capacity, rate
        """
        with self.lock:
            now = time.time()
            if tenant_id in self.tokens:
                last = self.last_update[tenant_id]
                elapsed = max(0, now - last)
                refill = elapsed * self.rate
                tokens = min(self.capacity, self.tokens[tenant_id] + refill)
            else:
                tokens = self.capacity

            return {
                "tokens_available": tokens,
                "capacity": self.capacity,
                "rate": self.rate,
            }
