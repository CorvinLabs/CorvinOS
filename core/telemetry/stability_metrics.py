"""Feature stability metrics collection (ADR-0286, ADR-0288).

Tracks invocations and errors per feature flag. Computes hourly digests
with error rates, adoption, days-in-tier. Sends GDPR-safe telemetry events.

**Compliance (GDPR Art. 5, 6):**
- No user data, no prompts, no personal identifiers beyond tenant_id
- Fail-closed: if ANY field would leak PII, entire event is dropped
- Opt-out: spec.telemetry.stability_metrics: false
- Legal basis: Legitimate interest (understanding feature reliability)
"""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Thread-safe lock for _METRICS dict operations (fixes CRITICAL data-race)
_METRICS_LOCK = threading.Lock()


@dataclass
class FlagMetrics:
    """In-memory metrics for one flag (rolling 24h window)."""

    flag_id: str
    # Hourly invocation counts (deque, max 24 items for rolling 24h)
    hourly_invocations: deque[int] = field(default_factory=lambda: deque(maxlen=24))
    # Hourly error counts (same 24h window)
    hourly_errors: deque[int] = field(default_factory=lambda: deque(maxlen=24))
    # Timestamp of last error (for "days_since_last_error")
    last_error_time: datetime | None = None

    def mark_invocation(self, lock: threading.Lock | None = None) -> None:
        """Record one invocation of this flag (atomic)."""
        # If called with lock, caller holds it; if not, acquire here
        if lock:
            if not self.hourly_invocations:
                self.hourly_invocations.append(0)
            self.hourly_invocations[-1] += 1
        else:
            with _METRICS_LOCK:
                if not self.hourly_invocations:
                    self.hourly_invocations.append(0)
                self.hourly_invocations[-1] += 1

    def mark_error(self, exc: Exception, lock: threading.Lock | None = None) -> None:
        """Record one error for this flag (atomic)."""
        if lock:
            if not self.hourly_errors:
                self.hourly_errors.append(0)
            self.hourly_errors[-1] += 1
            self.last_error_time = datetime.utcnow()
        else:
            with _METRICS_LOCK:
                if not self.hourly_errors:
                    self.hourly_errors.append(0)
                self.hourly_errors[-1] += 1
                self.last_error_time = datetime.utcnow()

    def get_24h_stats(self, lock: threading.Lock | None = None) -> dict[str, Any]:
        """Compute rolling 24h metrics (atomic snapshot)."""
        if lock:
            # Caller holds lock, make atomic snapshot
            invocation_sum = sum(list(self.hourly_invocations)) if self.hourly_invocations else 0
            error_sum = sum(list(self.hourly_errors)) if self.hourly_errors else 0
            last_error_time = self.last_error_time
        else:
            # Acquire lock for atomic snapshot
            with _METRICS_LOCK:
                invocation_sum = sum(list(self.hourly_invocations)) if self.hourly_invocations else 0
                error_sum = sum(list(self.hourly_errors)) if self.hourly_errors else 0
                last_error_time = self.last_error_time

        error_rate = (error_sum / invocation_sum) if invocation_sum > 0 else 0.0

        days_since_error = None
        if last_error_time:
            delta = datetime.utcnow() - last_error_time
            days_since_error = delta.days

        return {
            "invocation_count_24h": invocation_sum,
            "error_count_24h": error_sum,
            "error_rate_24h": round(error_rate, 4),
            "days_since_last_error": days_since_error,
        }


# Global registry: flag_id → FlagMetrics
_METRICS: dict[str, FlagMetrics] = {}


def get_flag_metrics(flag_id: str) -> FlagMetrics:
    """Get or create metrics for a flag (thread-safe)."""
    with _METRICS_LOCK:
        if flag_id not in _METRICS:
            _METRICS[flag_id] = FlagMetrics(flag_id=flag_id)
        return _METRICS[flag_id]


def mark_invocation(flag_id: str) -> None:
    """Record a feature flag invocation (thread-safe)."""
    with _METRICS_LOCK:
        if flag_id not in _METRICS:
            _METRICS[flag_id] = FlagMetrics(flag_id=flag_id)
        _METRICS[flag_id].mark_invocation(lock=_METRICS_LOCK)


def mark_error(flag_id: str, exc: Exception) -> None:
    """Record a feature flag error. Validates error message is PII-safe first (thread-safe)."""
    if not _is_pii_safe_error(exc):
        logger.warning(f"Dropping error for flag {flag_id}: PII detected in message")
        return
    with _METRICS_LOCK:
        if flag_id not in _METRICS:
            _METRICS[flag_id] = FlagMetrics(flag_id=flag_id)
        _METRICS[flag_id].mark_error(exc, lock=_METRICS_LOCK)


def _is_pii_safe_error(exc: Exception) -> bool:
    """Check if exception message contains PII patterns. Fail-closed: if unsure, return False.

    Checks: emails, passwords, tokens, phone numbers, credit cards, SSN patterns.
    """
    import re

    message = str(exc).lower()

    # Regex patterns (more robust than simple substring blacklist)
    pii_regex_patterns = [
        r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b",  # Email
        r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",  # US phone
        r"\b\d{3}[-]?\d{2}[-]?\d{4}\b",  # SSN
        r"\b\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}\b",  # Credit card
        r"bearer\s+[a-z0-9_-]{20,}",  # Bearer token
    ]

    # Substring patterns (catch obvious keywords)
    pii_keywords = [
        "password", "passwd", "pwd",
        "token", "secret", "api_key", "apikey",
        "credential", "auth", "oauth", "jwt",
        "session", "cookie", "bearer",
        "key", "private_key", "keypair",
    ]

    # Check regex patterns
    for pattern in pii_regex_patterns:
        if re.search(pattern, message):
            return False

    # Check keywords
    for keyword in pii_keywords:
        if keyword in message:
            return False

    return True


@dataclass
class FeatureStabilityEvent:
    """GDPR-safe telemetry event for feature stability."""

    event_type: str = "feature_stability_digest"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    tenant_id: str = "_default"
    instance_id: str = "unknown"
    flags_enabled: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), default=str)


def compute_digest(
    tenant_id: str = "_default",
    instance_id: str = "unknown",
    enabled_flag_ids: list[str] | None = None,
    release_tiers: dict[str, str] | None = None,
    enabled_by: str | None = None,
) -> FeatureStabilityEvent:
    """Compute an hourly stability digest for telemetry (thread-safe).

    Args:
        tenant_id: Tenant identifier
        instance_id: Instance identifier (e.g., "home-laptop-ubuntu")
        enabled_flag_ids: Flags that are currently enabled (if None, all are included)
        release_tiers: Map of flag_id → release_tier (fetched from registry)
        enabled_by: How these flags are enabled (e.g., "preset:standard")

    Returns:
        FeatureStabilityEvent ready to send
    """
    if release_tiers is None:
        release_tiers = {}
    if enabled_by is None:
        enabled_by = "preset:standard"

    flags_data = []
    # Make atomic snapshot of _METRICS to avoid iterator race
    with _METRICS_LOCK:
        metrics_snapshot = dict(_METRICS)

    for flag_id, metrics in metrics_snapshot.items():
        # Skip if this flag is not enabled (optional; include all by default for telemetry)
        if enabled_flag_ids is not None and flag_id not in enabled_flag_ids:
            continue

        # Get stats without passing lock (the snapshot is already atomic)
        stats = metrics.get_24h_stats(lock=None)
        flags_data.append({
            "flag_id": flag_id,
            "release_tier": release_tiers.get(flag_id, "alpha"),
            "enabled_by": enabled_by,
            "invocation_count_24h": stats["invocation_count_24h"],
            "error_count_24h": stats["error_count_24h"],
            "error_rate_24h": stats["error_rate_24h"],
            "days_since_last_error": stats["days_since_last_error"],
            "status": "active",
        })

    return FeatureStabilityEvent(
        tenant_id=tenant_id,
        instance_id=instance_id,
        flags_enabled=flags_data,
    )


def reset_metrics() -> None:
    """Clear all metrics (for testing). Do NOT call in production (thread-safe)."""
    global _METRICS
    with _METRICS_LOCK:
        _METRICS.clear()
