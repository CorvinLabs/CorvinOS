"""
Phase 5: Drift Categorization

Categorizes detected drifts into SAFE_AUTO_FIX, REQUIRES_APPROVAL, or BLOCKED.
Determines which remediation path each drift takes.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from datetime import datetime


class DriftCategory(Enum):
    """Drift risk categories"""
    SAFE_AUTO_FIX = "safe_auto_fix"          # Auto-remediate without approval
    REQUIRES_APPROVAL = "requires_approval"  # Needs operator sign-off
    BLOCKED = "blocked"                      # Stay in failed state, alert operator


class DriftSeverity(Enum):
    """Drift severity levels"""
    CRITICAL = "critical"    # System down or data at risk
    HIGH = "high"             # Functional impairment
    MEDIUM = "medium"         # Degraded performance
    LOW = "low"               # Non-critical drift


class RemediationType(Enum):
    """Remediation action types"""
    PLUGIN_INSTALL = "plugin_install"
    PLUGIN_UPDATE = "plugin_update"
    CONFIG_SYNC = "config_sync"
    CONFIG_OVERRIDE = "config_override"
    SCHEMA_MIGRATION = "schema_migration"
    CODE_DEPLOY = "code_deploy"
    ROLLBACK = "rollback"


@dataclass
class RiskAssessment:
    """Risk assessment for a drift"""
    drift_type: str
    drift_id: str
    category: DriftCategory
    severity: DriftSeverity
    remediation_type: Optional[RemediationType] = None
    estimated_duration_seconds: int = 0
    estimated_risk_score: float = 0.0  # 0.0 (safe) to 1.0 (blocked)
    requires_rollback: bool = False
    impacts_services: List[str] = field(default_factory=list)
    rollback_plan: Optional[str] = None
    notes: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class DriftCategorizer:
    """Categorizes drifts and assigns remediation paths"""

    # Safe drifts: can be auto-fixed without approval
    SAFE_DRIFT_TYPES = {
        "PLUGIN_MISSING": {
            "category": DriftCategory.SAFE_AUTO_FIX,
            "severity": DriftSeverity.MEDIUM,
            "remediation": RemediationType.PLUGIN_INSTALL,
            "duration_seconds": 15,
            "risk_score": 0.1,
            "requires_rollback": False,
        },
        "PLUGIN_VERSION_MISMATCH": {
            "category": DriftCategory.SAFE_AUTO_FIX,
            "severity": DriftSeverity.LOW,
            "remediation": RemediationType.PLUGIN_UPDATE,
            "duration_seconds": 20,
            "risk_score": 0.15,
            "requires_rollback": True,
        },
        "CONFIG_OVERRIDE_DRIFT": {
            "category": DriftCategory.SAFE_AUTO_FIX,
            "severity": DriftSeverity.LOW,
            "remediation": RemediationType.CONFIG_SYNC,
            "duration_seconds": 5,
            "risk_score": 0.05,
            "requires_rollback": False,
        },
    }

    # High-risk drifts: require operator approval
    HIGH_RISK_DRIFT_TYPES = {
        "CODE_VERSION_DRIFT": {
            "category": DriftCategory.REQUIRES_APPROVAL,
            "severity": DriftSeverity.HIGH,
            "remediation": RemediationType.CODE_DEPLOY,
            "duration_seconds": 120,
            "risk_score": 0.7,
            "requires_rollback": True,
        },
        "MANIFEST_HASH_MISMATCH": {
            "category": DriftCategory.REQUIRES_APPROVAL,
            "severity": DriftSeverity.HIGH,
            "remediation": RemediationType.CODE_DEPLOY,
            "duration_seconds": 90,
            "risk_score": 0.75,
            "requires_rollback": True,
        },
        "SCHEMA_VERSION_MISMATCH": {
            "category": DriftCategory.REQUIRES_APPROVAL,
            "severity": DriftSeverity.CRITICAL,
            "remediation": RemediationType.SCHEMA_MIGRATION,
            "duration_seconds": 300,
            "risk_score": 0.9,
            "requires_rollback": True,
        },
        "DATABASE_MIGRATION_PENDING": {
            "category": DriftCategory.REQUIRES_APPROVAL,
            "severity": DriftSeverity.CRITICAL,
            "remediation": RemediationType.SCHEMA_MIGRATION,
            "duration_seconds": 600,
            "risk_score": 0.95,
            "requires_rollback": True,
        },
        "CERTIFICATE_EXPIRY_WARNING": {
            "category": DriftCategory.REQUIRES_APPROVAL,
            "severity": DriftSeverity.HIGH,
            "remediation": RemediationType.CONFIG_OVERRIDE,
            "duration_seconds": 30,
            "risk_score": 0.6,
            "requires_rollback": False,
        },
    }

    # Blocked drifts: operator must manually intervene
    BLOCKED_DRIFT_TYPES = {
        "INVALID_CONFIG": {
            "category": DriftCategory.BLOCKED,
            "severity": DriftSeverity.CRITICAL,
            "remediation": None,
            "notes": "Invalid configuration detected. Manual review required.",
        },
        "CREDENTIAL_MISSING": {
            "category": DriftCategory.BLOCKED,
            "severity": DriftSeverity.CRITICAL,
            "remediation": None,
            "notes": "Missing credentials. Cannot proceed without operator intervention.",
        },
        "EXTERNAL_SERVICE_DOWN": {
            "category": DriftCategory.BLOCKED,
            "severity": DriftSeverity.CRITICAL,
            "remediation": None,
            "notes": "External service unavailable. Wait and retry.",
        },
        "QUOTA_EXCEEDED": {
            "category": DriftCategory.BLOCKED,
            "severity": DriftSeverity.HIGH,
            "remediation": None,
            "notes": "Resource quota exceeded. Operator action required.",
        },
    }

    def categorize_drift(
        self,
        drift_type: str,
        drift_id: str,
        instance_id: Optional[str] = None,
        affected_services: Optional[List[str]] = None,
    ) -> RiskAssessment:
        """
        Categorize a drift and return risk assessment.

        Returns:
            RiskAssessment with category (safe/approval/blocked) and metadata
        """
        config = None
        category = None

        # Check safe drifts first
        if drift_type in self.SAFE_DRIFT_TYPES:
            config = self.SAFE_DRIFT_TYPES[drift_type]
            category = DriftCategory.SAFE_AUTO_FIX
        # Check high-risk drifts
        elif drift_type in self.HIGH_RISK_DRIFT_TYPES:
            config = self.HIGH_RISK_DRIFT_TYPES[drift_type]
            category = DriftCategory.REQUIRES_APPROVAL
        # Check blocked drifts
        elif drift_type in self.BLOCKED_DRIFT_TYPES:
            config = self.BLOCKED_DRIFT_TYPES[drift_type]
            category = DriftCategory.BLOCKED
        else:
            # Unknown drift type: default to REQUIRES_APPROVAL (safe default)
            config = {
                "category": DriftCategory.REQUIRES_APPROVAL,
                "severity": DriftSeverity.MEDIUM,
                "remediation": None,
                "duration_seconds": 60,
                "risk_score": 0.5,
                "requires_rollback": False,
                "notes": f"Unknown drift type: {drift_type}. Manual review required.",
            }
            category = DriftCategory.REQUIRES_APPROVAL

        # Build risk assessment
        return RiskAssessment(
            drift_type=drift_type,
            drift_id=drift_id,
            category=config.get("category", category),
            severity=DriftSeverity(config.get("severity", "medium")),
            remediation_type=config.get("remediation"),
            estimated_duration_seconds=config.get("duration_seconds", 30),
            estimated_risk_score=config.get("risk_score", 0.5),
            requires_rollback=config.get("requires_rollback", False),
            impacts_services=affected_services or [],
            rollback_plan=self._generate_rollback_plan(
                drift_type, config.get("requires_rollback", False)
            ),
            notes=config.get("notes"),
        )

    def _generate_rollback_plan(self, drift_type: str, requires_rollback: bool) -> Optional[str]:
        """Generate rollback plan for remediations that support it"""
        if not requires_rollback:
            return None

        return f"""
Rollback Plan for {drift_type}:
1. Capture current state (before remediation)
2. If remediation fails or causes issues:
   - Restore previous version/config
   - Verify services recover
   - Alert operator
3. Rollback is atomic (all-or-nothing)
        """

    def categorize_multiple(
        self, drifts: List[tuple]
    ) -> tuple[List[RiskAssessment], dict]:
        """
        Categorize multiple drifts.

        Args:
            drifts: List of (drift_type, drift_id, instance_id) tuples

        Returns:
            (assessments, summary) where summary has counts by category
        """
        assessments = []
        summary = {
            "total": len(drifts),
            "safe_auto_fix": 0,
            "requires_approval": 0,
            "blocked": 0,
            "by_severity": {s.value: 0 for s in DriftSeverity},
        }

        for drift_type, drift_id, instance_id in drifts:
            assessment = self.categorize_drift(drift_type, drift_id, instance_id)
            assessments.append(assessment)

            # Update summary
            category_name = assessment.category.value.replace("-", "_")
            summary[category_name] += 1
            summary["by_severity"][assessment.severity.value] += 1

        return assessments, summary


# Global categorizer instance
_categorizer = DriftCategorizer()


def categorize_drift(
    drift_type: str, drift_id: str, instance_id: Optional[str] = None
) -> RiskAssessment:
    """Convenience function to categorize a single drift"""
    return _categorizer.categorize_drift(drift_type, drift_id, instance_id)


def categorize_drifts(drifts: List[tuple]) -> tuple[List[RiskAssessment], dict]:
    """Convenience function to categorize multiple drifts"""
    return _categorizer.categorize_multiple(drifts)
