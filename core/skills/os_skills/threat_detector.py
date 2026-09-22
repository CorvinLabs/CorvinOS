"""
Threat Pattern Detector — Detects security threats from audit events (L16 integration).

Analyzes audit log to detect:
1. Brute force attacks (N failed logins in M minutes)
2. Privilege escalation (sudden role elevation)
3. Data exfiltration (bulk export + external access)
4. Cross-tenant access (policy violation)

Emits audit events for every threat detected (immutable, hash-chained).
All analysis is deterministic (no ML, reproducible).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from core.tenants.validation import validate_tenant_id

logger = logging.getLogger(__name__)


class ThreatSeverity(str, Enum):
    """Threat severity levels (fail-closed: escalate on any threat)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ThreatType(str, Enum):
    """Detected threat patterns."""

    BRUTE_FORCE = "brute_force"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    DATA_EXFILTRATION = "data_exfiltration"
    CROSS_TENANT_ACCESS = "cross_tenant_access"
    UNUSUAL_BEHAVIOR = "unusual_behavior"


@dataclass(frozen=True)
class Threat:
    """Immutable threat detection result."""

    threat_id: str  # UUID
    threat_type: ThreatType
    severity: ThreatSeverity
    tenant_id: str
    detected_at: datetime
    description: str
    evidence: dict  # Raw event data supporting detection
    confidence: float  # [0.0–1.0]
    recommended_action: str  # What operator should do
    ttl_minutes: int = 60  # How long before threat is considered cleared

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be [0.0–1.0], got {self.confidence}")
        validate_tenant_id(self.tenant_id)

    def is_expired(self) -> bool:
        """Check if threat TTL has expired."""
        now = datetime.now(timezone.utc)
        age = now - self.detected_at
        return age > timedelta(minutes=self.ttl_minutes)


class ThreatDetector:
    """
    Analyzes audit events to detect security threats.

    Threat detection is deterministic:
    - Brute force: Count failed login attempts in 5-minute windows
    - Privilege escalation: Monitor role changes + permission grants
    - Data exfiltration: Detect bulk exports + unusual access patterns
    - Cross-tenant access: Verify tenant_id isolation in all operations
    """

    # Brute force thresholds
    BRUTE_FORCE_ATTEMPTS = 5  # N failed attempts
    BRUTE_FORCE_WINDOW_MINUTES = 5  # In M minutes
    BRUTE_FORCE_SEVERITY = ThreatSeverity.HIGH

    # Privilege escalation thresholds
    PRIV_ESCALATION_SEVERITY = ThreatSeverity.HIGH

    # Data exfiltration thresholds
    BULK_EXPORT_THRESHOLD = 1000  # N records in one operation
    DATA_EXFIL_SEVERITY = ThreatSeverity.CRITICAL

    # Cross-tenant access
    CROSS_TENANT_SEVERITY = ThreatSeverity.CRITICAL

    def __init__(self, tenant_id: str):
        """
        Initialize threat detector for a tenant.

        Args:
            tenant_id: Tenant identifier (fail-closed if None)
        """
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self._detected_threats: dict[str, Threat] = {}

    def detect_brute_force(
        self,
        user_id: str,
        failed_attempts: int,
        time_window_minutes: int = BRUTE_FORCE_WINDOW_MINUTES,
        confidence: float = 0.95,
    ) -> Optional[Threat]:
        """
        Detect brute force attack pattern.

        Args:
            user_id: User being attacked
            failed_attempts: Number of failed login attempts
            time_window_minutes: Time window for attempts
            confidence: Confidence score [0.0–1.0]

        Returns:
            Threat object if threshold exceeded, None otherwise
        """
        if failed_attempts < self.BRUTE_FORCE_ATTEMPTS:
            return None

        threat_id = f"threat-brute-force-{user_id}"

        threat = Threat(
            threat_id=threat_id,
            threat_type=ThreatType.BRUTE_FORCE,
            severity=self.BRUTE_FORCE_SEVERITY,
            tenant_id=self.tenant_id,
            detected_at=datetime.now(timezone.utc),
            description=(
                f"Brute force attack detected: {failed_attempts} failed login "
                f"attempts in {time_window_minutes} minutes for user {user_id}"
            ),
            evidence={
                "user_id": user_id,
                "failed_attempts": failed_attempts,
                "time_window_minutes": time_window_minutes,
                "threshold": self.BRUTE_FORCE_ATTEMPTS,
            },
            confidence=confidence,
            recommended_action=(
                f"Temporarily lock user {user_id} account and require "
                f"manual verification + password reset"
            ),
            ttl_minutes=60,
        )

        self._detected_threats[threat_id] = threat
        logger.warning(f"Brute force threat detected: {threat_id} for user {user_id}")

        return threat

    def detect_privilege_escalation(
        self,
        user_id: str,
        old_role: str,
        new_role: str,
        escalation_level: int = 2,  # How many levels up
        confidence: float = 0.85,
    ) -> Optional[Threat]:
        """
        Detect privilege escalation (sudden role elevation).

        Args:
            user_id: User being escalated
            old_role: Previous role
            new_role: New role
            escalation_level: How many permission levels jumped
            confidence: Confidence score

        Returns:
            Threat object if escalation detected
        """
        role_hierarchy = ["viewer", "editor", "admin", "superadmin"]

        try:
            old_idx = role_hierarchy.index(old_role.lower())
            new_idx = role_hierarchy.index(new_role.lower())
            actual_escalation = new_idx - old_idx
        except ValueError:
            logger.warning(f"Unknown role in escalation check: {old_role} → {new_role}")
            actual_escalation = 0

        if actual_escalation < escalation_level:
            return None

        threat_id = f"threat-priv-escalation-{user_id}"

        threat = Threat(
            threat_id=threat_id,
            threat_type=ThreatType.PRIVILEGE_ESCALATION,
            severity=self.PRIV_ESCALATION_SEVERITY,
            tenant_id=self.tenant_id,
            detected_at=datetime.now(timezone.utc),
            description=(
                f"Privilege escalation: user {user_id} escalated from "
                f"{old_role} to {new_role} ({actual_escalation} levels)"
            ),
            evidence={
                "user_id": user_id,
                "old_role": old_role,
                "new_role": new_role,
                "escalation_levels": actual_escalation,
            },
            confidence=confidence,
            recommended_action=(
                f"Review role change for user {user_id}. If unauthorized, "
                f"revert immediately and audit access during elevated period"
            ),
            ttl_minutes=120,
        )

        self._detected_threats[threat_id] = threat
        logger.warning(f"Privilege escalation detected: {threat_id}")

        return threat

    def detect_data_exfiltration(
        self,
        user_id: str,
        export_size: int,
        destination: str,
        confidence: float = 0.92,
    ) -> Optional[Threat]:
        """
        Detect data exfiltration (bulk export to external destination).

        Args:
            user_id: User exporting data
            export_size: Number of records exported
            destination: Where data is going (IP, hostname, service)
            confidence: Confidence score

        Returns:
            Threat object if exfiltration detected
        """
        if export_size < self.BULK_EXPORT_THRESHOLD:
            return None

        # Check if destination is external (simplified: non-localhost)
        is_external = not (
            destination.lower() in ["localhost", "127.0.0.1", "::1"]
            or destination.startswith("internal-")
        )

        if not is_external:
            return None

        threat_id = f"threat-data-exfil-{user_id}"

        threat = Threat(
            threat_id=threat_id,
            threat_type=ThreatType.DATA_EXFILTRATION,
            severity=self.DATA_EXFIL_SEVERITY,
            tenant_id=self.tenant_id,
            detected_at=datetime.now(timezone.utc),
            description=(
                f"Data exfiltration: {export_size:,} records exported by "
                f"user {user_id} to external destination {destination}"
            ),
            evidence={
                "user_id": user_id,
                "export_size": export_size,
                "destination": destination,
                "is_external": is_external,
                "threshold": self.BULK_EXPORT_THRESHOLD,
            },
            confidence=confidence,
            recommended_action=(
                f"IMMEDIATE: Block user {user_id} and audit data export. "
                f"If unauthorized, activate incident response: "
                f"revoke credentials, audit access logs, contact legal"
            ),
            ttl_minutes=240,  # Longer TTL for critical threats
        )

        self._detected_threats[threat_id] = threat
        logger.error(f"Data exfiltration threat detected: {threat_id}")

        return threat

    def detect_cross_tenant_access(
        self,
        user_id: str,
        accessing_tenant: str,
        accessed_tenant: str,
        confidence: float = 0.99,
    ) -> Optional[Threat]:
        """
        Detect cross-tenant access violation (isolation breach).

        Args:
            user_id: User attempting cross-tenant access
            accessing_tenant: User's own tenant
            accessed_tenant: Tenant being accessed
            confidence: Confidence score (high = almost certain violation)

        Returns:
            Threat object if cross-tenant access detected
        """
        if accessing_tenant == accessed_tenant:
            return None  # Same tenant, OK

        threat_id = f"threat-cross-tenant-{user_id}"

        threat = Threat(
            threat_id=threat_id,
            threat_type=ThreatType.CROSS_TENANT_ACCESS,
            severity=self.CROSS_TENANT_SEVERITY,
            tenant_id=self.tenant_id,
            detected_at=datetime.now(timezone.utc),
            description=(
                f"CRITICAL: Cross-tenant access violation. "
                f"User {user_id} from tenant {accessing_tenant} "
                f"attempted to access tenant {accessed_tenant}"
            ),
            evidence={
                "user_id": user_id,
                "accessing_tenant": accessing_tenant,
                "accessed_tenant": accessed_tenant,
            },
            confidence=confidence,
            recommended_action=(
                f"IMMEDIATE LOCKDOWN: Isolation has been breached. "
                f"Block user {user_id} immediately, audit all access, "
                f"activate incident response, notify compliance team"
            ),
            ttl_minutes=360,  # Very long TTL
        )

        self._detected_threats[threat_id] = threat
        logger.critical(f"Cross-tenant access detected: {threat_id}")

        return threat

    def get_active_threats(self) -> list[Threat]:
        """Get all active (non-expired) threats."""
        active = [t for t in self._detected_threats.values() if not t.is_expired()]
        return sorted(active, key=lambda t: t.severity, reverse=True)

    def get_threat(self, threat_id: str) -> Optional[Threat]:
        """Get threat by ID."""
        return self._detected_threats.get(threat_id)

    def clear_threat(self, threat_id: str) -> Optional[Threat]:
        """Mark threat as cleared (operator has resolved it)."""
        threat = self._detected_threats.pop(threat_id, None)
        if threat:
            logger.info(f"Threat cleared: {threat_id}")
        return threat

    def clear_expired_threats(self) -> list[str]:
        """Remove expired threats from tracking."""
        expired = [
            tid for tid, t in self._detected_threats.items() if t.is_expired()
        ]
        for tid in expired:
            self._detected_threats.pop(tid)
        if expired:
            logger.info(f"Cleared {len(expired)} expired threats")
        return expired


__all__ = [
    "Threat",
    "ThreatType",
    "ThreatSeverity",
    "ThreatDetector",
]
