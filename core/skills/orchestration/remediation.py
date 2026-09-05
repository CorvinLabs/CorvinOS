"""Adversarial Review Findings Remediation (ADR-0610/0611/0612)."""

from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger(__name__)


# Finding #2: Plugin timeout wrapper
class PluginTimeoutError(Exception):
    """Plugin invocation timed out."""
    pass


def invoke_with_timeout(
    plugin_callable,
    *args,
    timeout_s: float = 30.0,
    **kwargs,
) -> None:
    """
    Invoke plugin with timeout (Finding #2).

    Raises PluginTimeoutError if timeout exceeded.
    """
    import signal

    def timeout_handler(signum, frame):
        raise PluginTimeoutError(f"Plugin invocation exceeded {timeout_s}s timeout")

    # Set timeout
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(int(timeout_s))

    try:
        return plugin_callable(*args, **kwargs)
    finally:
        signal.alarm(0)  # Cancel alarm


# Finding #5: Whitelist validation in manifest loader
def validate_allowed_plugins(allowed_plugins: list[str], registry) -> list[str]:
    """
    Validate all allowed_plugins exist (Finding #5).

    Returns: list of errors (empty = valid)
    """
    errors = []
    for plugin_id in allowed_plugins:
        if not registry.get_plugin_manifest(plugin_id):
            errors.append(f"allowed_plugin '{plugin_id}' not registered")
    return errors


# Finding #6: Tenant_id enforcement
def enforce_tenant_id(tenant_id: Optional[str]) -> str:
    """
    Enforce tenant_id is always present (Finding #6).

    Raises ValueError if missing.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required (fail-closed)")
    return tenant_id


# Finding #7: Learning model tenant scoping
def get_model_key(skill_id: str, tenant_id: str) -> str:
    """
    Generate tenant-scoped model key (Finding #7).

    Ensures models are never shared across tenants.
    """
    if not tenant_id:
        raise ValueError("tenant_id required for model key (fail-closed)")
    return f"{tenant_id}:{skill_id}"


# Finding #9: Feedback validation against outcome
def validate_feedback_matches_outcome(
    feedback_rating: str,  # good | neutral | bad
    invocation_success: bool,
    invocation_slo_met: bool,
) -> tuple[bool, str]:
    """
    Validate feedback is consistent with observed outcome (Finding #9).

    Returns: (is_valid, note)
    """
    # If outcome was success AND SLO met, but feedback is "bad", that's an outlier
    if invocation_success and invocation_slo_met and feedback_rating == "bad":
        return False, "outlier: success+slo_met but feedback=bad (capping confidence delta)"

    # If outcome was failure, but feedback is "good", that's an outlier
    if not invocation_success and feedback_rating == "good":
        return False, "outlier: failed but feedback=good (capping confidence delta)"

    return True, "feedback consistent with outcome"


# Finding #1: Staleness detection
class ManifestFreshnessValidator:
    """Detect stale manifests (Finding #1)."""

    def __init__(self, ttl_seconds: int = 3600):
        """Initialize validator with TTL."""
        self.ttl_seconds = ttl_seconds
        self.plugin_manifest_timestamps: dict[str, float] = {}

    def mark_manifest_verified(self, plugin_id: str) -> None:
        """Mark a manifest as verified just now."""
        self.plugin_manifest_timestamps[plugin_id] = time.time()

    def is_manifest_fresh(self, plugin_id: str) -> bool:
        """Check if manifest is fresh (within TTL)."""
        if plugin_id not in self.plugin_manifest_timestamps:
            return False

        age_seconds = time.time() - self.plugin_manifest_timestamps[plugin_id]
        return age_seconds < self.ttl_seconds

    def get_manifest_age_seconds(self, plugin_id: str) -> Optional[float]:
        """Get manifest age in seconds."""
        if plugin_id not in self.plugin_manifest_timestamps:
            return None
        return time.time() - self.plugin_manifest_timestamps[plugin_id]
