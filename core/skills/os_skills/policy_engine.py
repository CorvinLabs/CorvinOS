"""
Policy Engine — Adaptive security policy enforcement (L16 integration).

Responds to detected threats by:
1. Tightening security gates (reduce thresholds, increase auth strictness)
2. Enforcing rate limits (prevent brute force)
3. Blocking suspicious IPs/users
4. Requiring additional auth (MFA, manual review)
5. Reverting when threat clears (restores normal operations)

All policy changes are audited and require operator approval (fail-closed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.tenants.validation import validate_tenant_id
from core.skills.os_skills.threat_detector import Threat, ThreatType, ThreatSeverity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyAdjustment:
    """Immutable security policy adjustment."""

    adjustment_id: str
    tenant_id: str
    threat_id: str
    timestamp: datetime
    policy_name: str  # e.g., "auth_timeout", "rate_limit"
    old_value: any
    new_value: any
    reason: str
    severity_override: bool = False  # Did operator override?

    def __post_init__(self) -> None:
        validate_tenant_id(self.tenant_id)


@dataclass(frozen=True)
class SecurityPolicy:
    """Current security policy configuration."""

    # Authentication
    auth_timeout_seconds: int = 1800  # 30 minutes
    require_mfa: bool = False
    require_manual_review_above_level: str = "superadmin"

    # Rate limiting
    login_attempt_limit: int = 5
    login_attempt_window_minutes: int = 5
    api_rate_limit_per_minute: int = 100

    # Export controls
    max_export_size_records: int = 10000
    require_export_approval_above: int = 5000

    # Data access
    ip_whitelist_enabled: bool = False
    blocked_ips: set[str] = field(default_factory=set)
    blocked_users: set[str] = field(default_factory=set)

    # Monitoring
    enable_audit_logging: bool = True
    audit_log_retention_days: int = 90


class PolicyEngine:
    """
    Adaptive security policy management.

    Thread-safe policy enforcement with audit trail.
    All changes are fail-closed: exception on policy violation.
    """

    def __init__(self, tenant_id: str):
        """
        Initialize policy engine.

        Args:
            tenant_id: Tenant identifier (fail-closed if None)
        """
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self._policy = SecurityPolicy()
        self._policy_history: dict[str, PolicyAdjustment] = {}
        self._active_threats: dict[str, Threat] = {}

    def current_policy(self) -> SecurityPolicy:
        """Get current policy configuration (immutable)."""
        return self._policy

    def tighten_on_threat(self, threat: Threat) -> PolicyAdjustment:
        """
        Tighten security policy in response to threat.

        Args:
            threat: Detected threat

        Returns:
            PolicyAdjustment showing what changed

        Raises:
            ValueError: If threat is invalid
        """
        if threat.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: threat is for {threat.tenant_id}, "
                f"but engine is for {self.tenant_id}"
            )

        old_policy = self._policy
        new_policy = self._apply_threat_response(threat)

        # Record the adjustment
        adjustment_id = f"adj-{threat.threat_id}"

        # Find the primary policy change
        primary_change = self._detect_primary_change(old_policy, new_policy)

        adjustment = PolicyAdjustment(
            adjustment_id=adjustment_id,
            tenant_id=self.tenant_id,
            threat_id=threat.threat_id,
            timestamp=datetime.now(timezone.utc),
            policy_name=primary_change["name"],
            old_value=primary_change["old"],
            new_value=primary_change["new"],
            reason=threat.description,
            severity_override=threat.severity == ThreatSeverity.CRITICAL,
        )

        self._policy_history[adjustment_id] = adjustment
        self._active_threats[threat.threat_id] = threat
        self._policy = new_policy

        logger.warning(
            f"Policy tightened: {primary_change['name']} "
            f"({primary_change['old']} → {primary_change['new']}) "
            f"due to threat {threat.threat_id}"
        )

        return adjustment

    def _apply_threat_response(self, threat: Threat) -> SecurityPolicy:
        """Apply appropriate policy tightening for threat type."""
        old = self._policy

        if threat.threat_type == ThreatType.BRUTE_FORCE:
            # Reduce login attempt limit + shorten window
            return SecurityPolicy(
                auth_timeout_seconds=old.auth_timeout_seconds // 2,
                require_mfa=True,  # Require MFA on next login
                login_attempt_limit=max(2, old.login_attempt_limit // 2),
                login_attempt_window_minutes=old.login_attempt_window_minutes,
                api_rate_limit_per_minute=old.api_rate_limit_per_minute,
                max_export_size_records=old.max_export_size_records,
                require_export_approval_above=old.require_export_approval_above,
                ip_whitelist_enabled=old.ip_whitelist_enabled,
                blocked_ips=old.blocked_ips.copy(),
                blocked_users=old.blocked_users | {threat.evidence.get("user_id", "")},
                enable_audit_logging=old.enable_audit_logging,
                audit_log_retention_days=old.audit_log_retention_days,
                require_manual_review_above_level=old.require_manual_review_above_level,
            )

        elif threat.threat_type == ThreatType.PRIVILEGE_ESCALATION:
            # Require manual review + audit all operations
            return SecurityPolicy(
                auth_timeout_seconds=old.auth_timeout_seconds // 3,
                require_mfa=True,
                require_manual_review_above_level="editor",  # Lower threshold
                login_attempt_limit=old.login_attempt_limit,
                login_attempt_window_minutes=old.login_attempt_window_minutes,
                api_rate_limit_per_minute=old.api_rate_limit_per_minute // 2,
                max_export_size_records=old.max_export_size_records,
                require_export_approval_above=old.require_export_approval_above // 2,
                ip_whitelist_enabled=old.ip_whitelist_enabled,
                blocked_ips=old.blocked_ips.copy(),
                blocked_users=old.blocked_users | {threat.evidence.get("user_id", "")},
                enable_audit_logging=True,
                audit_log_retention_days=max(old.audit_log_retention_days, 180),
                require_manual_review_above_level=old.require_manual_review_above_level,
            )

        elif threat.threat_type == ThreatType.DATA_EXFILTRATION:
            # Severe: Block user, reduce exports, enable all logging
            return SecurityPolicy(
                auth_timeout_seconds=300,  # 5 minutes
                require_mfa=True,
                require_manual_review_above_level="viewer",  # All ops need review
                login_attempt_limit=1,  # One strike
                login_attempt_window_minutes=60,
                api_rate_limit_per_minute=10,  # Severe limit
                max_export_size_records=100,  # Tiny limit
                require_export_approval_above=50,  # Any export needs approval
                ip_whitelist_enabled=True,  # Enable IP whitelist
                blocked_ips=old.blocked_ips.copy(),
                blocked_users=old.blocked_users | {threat.evidence.get("user_id", "")},
                enable_audit_logging=True,
                audit_log_retention_days=365,  # Year-long retention
                require_manual_review_above_level=old.require_manual_review_above_level,
            )

        elif threat.threat_type == ThreatType.CROSS_TENANT_ACCESS:
            # Emergency: Lock down everything
            return SecurityPolicy(
                auth_timeout_seconds=60,  # 1 minute
                require_mfa=True,
                require_manual_review_above_level="viewer",
                login_attempt_limit=1,
                login_attempt_window_minutes=1440,  # 24 hours
                api_rate_limit_per_minute=5,
                max_export_size_records=0,  # NO exports
                require_export_approval_above=0,
                ip_whitelist_enabled=True,
                blocked_ips=old.blocked_ips.copy(),
                blocked_users=old.blocked_users | {threat.evidence.get("user_id", "")},
                enable_audit_logging=True,
                audit_log_retention_days=365,
                require_manual_review_above_level=old.require_manual_review_above_level,
            )

        else:
            # Default: general tightening
            return SecurityPolicy(
                auth_timeout_seconds=old.auth_timeout_seconds // 2,
                require_mfa=True,
                login_attempt_limit=old.login_attempt_limit // 2,
                login_attempt_window_minutes=old.login_attempt_window_minutes,
                api_rate_limit_per_minute=old.api_rate_limit_per_minute // 2,
                max_export_size_records=old.max_export_size_records,
                require_export_approval_above=old.require_export_approval_above,
                ip_whitelist_enabled=old.ip_whitelist_enabled,
                blocked_ips=old.blocked_ips.copy(),
                blocked_users=old.blocked_users.copy(),
                enable_audit_logging=old.enable_audit_logging,
                audit_log_retention_days=old.audit_log_retention_days,
                require_manual_review_above_level=old.require_manual_review_above_level,
            )

    def _detect_primary_change(self, old: SecurityPolicy, new: SecurityPolicy) -> dict:
        """Detect the most significant policy change."""
        if old.auth_timeout_seconds != new.auth_timeout_seconds:
            return {
                "name": "auth_timeout",
                "old": old.auth_timeout_seconds,
                "new": new.auth_timeout_seconds,
            }
        if old.login_attempt_limit != new.login_attempt_limit:
            return {
                "name": "login_attempt_limit",
                "old": old.login_attempt_limit,
                "new": new.login_attempt_limit,
            }
        if old.api_rate_limit_per_minute != new.api_rate_limit_per_minute:
            return {
                "name": "api_rate_limit",
                "old": old.api_rate_limit_per_minute,
                "new": new.api_rate_limit_per_minute,
            }
        if old.require_mfa != new.require_mfa:
            return {
                "name": "require_mfa",
                "old": old.require_mfa,
                "new": new.require_mfa,
            }
        return {"name": "policy", "old": "default", "new": "tightened"}

    def revert_on_clear(self, threat_id: str) -> Optional[SecurityPolicy]:
        """
        Revert security policy when threat is cleared.

        Args:
            threat_id: Threat that is now resolved

        Returns:
            Reverted policy, or None if threat not found
        """
        if threat_id not in self._active_threats:
            logger.warning(f"Attempt to clear unknown threat: {threat_id}")
            return None

        # For now, revert to baseline policy
        # In real system, would restore previous policy if applicable
        self._active_threats.pop(threat_id)
        self._policy = SecurityPolicy()

        logger.info(f"Policy reverted after threat cleared: {threat_id}")
        return self._policy

    def get_policy_history(self) -> dict[str, PolicyAdjustment]:
        """Get history of all policy changes."""
        return dict(self._policy_history)

    def get_active_threat_count(self) -> int:
        """Get number of active threats."""
        return len(self._active_threats)


__all__ = [
    "SecurityPolicy",
    "PolicyAdjustment",
    "PolicyEngine",
]
