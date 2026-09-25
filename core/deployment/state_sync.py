"""
Deployment State Synchronization — Phase 1: Code Version Drift Detection

Detects when instances diverge from canonical code version.
Implements fail-closed semantics: drift → alert + prevent operations.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
from enum import Enum
import json

from .manifest import ManifestManager, ManifestSnapshot


class DriftSeverity(Enum):
    """Drift severity levels"""
    CRITICAL = "CRITICAL"  # Immediate page + auto-prevent
    HIGH = "HIGH"          # Slack alert + wait for approval
    MEDIUM = "MEDIUM"      # Log + metrics
    LOW = "LOW"            # Metric only


@dataclass
class DriftAlert:
    """Immutable drift detection alert"""
    alert_type: str  # "CODE_VERSION_DRIFT", "MANIFEST_HASH_MISMATCH", etc.
    severity: DriftSeverity
    instance_id: str
    expected_value: str
    actual_value: str
    message: str
    timestamp: str
    remediation_available: bool = False
    remediation_action: Optional[str] = None


class DeploymentStateManager:
    """
    Central deployment state management.

    Maintains canonical state from first/primary instance.
    Compares all other instances against it.
    """

    def __init__(self):
        self.canonical_state: Optional[ManifestSnapshot] = None
        self.instance_states: Dict[str, ManifestSnapshot] = {}
        self.drift_alerts: List[DriftAlert] = []

    def register_instance(self, instance_id: str, tenant_id: str = "_default") -> ManifestSnapshot:
        """
        Register instance and capture its manifest state.
        First instance becomes canonical.
        """
        state = ManifestManager.create_snapshot(instance_id, tenant_id)
        self.instance_states[instance_id] = state

        # First instance is canonical
        if self.canonical_state is None:
            self.canonical_state = state
            print(f"✅ Canonical state set from {instance_id}: {state.git_sha}")

        return state

    def detect_drift(self, instance_id: str) -> List[DriftAlert]:
        """
        Compare instance state against canonical.
        Returns list of detected drifts.
        """
        if not self.canonical_state:
            raise RuntimeError("No canonical state set — register instance first")

        current_state = self.instance_states.get(instance_id)
        if not current_state:
            current_state = self.register_instance(instance_id)

        drifts = []
        canonical = self.canonical_state

        # Check 1: Git SHA (code version)
        if current_state.git_sha != canonical.git_sha:
            drifts.append(DriftAlert(
                alert_type="CODE_VERSION_DRIFT",
                severity=DriftSeverity.CRITICAL,
                instance_id=instance_id,
                expected_value=canonical.git_sha,
                actual_value=current_state.git_sha,
                message=f"{instance_id}: Git SHA mismatch (expected {canonical.git_sha[:8]}, got {current_state.git_sha[:8]})",
                timestamp=current_state.timestamp,
                remediation_available=True,
                remediation_action="REDEPLOY_INSTANCE"
            ))

        # Check 2: Manifest hash (entire code tree)
        if current_state.manifest_hash != canonical.manifest_hash:
            drifts.append(DriftAlert(
                alert_type="MANIFEST_HASH_MISMATCH",
                severity=DriftSeverity.CRITICAL,
                instance_id=instance_id,
                expected_value=canonical.manifest_hash,
                actual_value=current_state.manifest_hash,
                message=f"{instance_id}: Code divergence detected (manifest hash mismatch)",
                timestamp=current_state.timestamp,
                remediation_available=True,
                remediation_action="REDEPLOY_INSTANCE"
            ))

        # Check 3: Version tag
        if current_state.version_tag != canonical.version_tag:
            drifts.append(DriftAlert(
                alert_type="VERSION_TAG_MISMATCH",
                severity=DriftSeverity.HIGH,
                instance_id=instance_id,
                expected_value=canonical.version_tag,
                actual_value=current_state.version_tag,
                message=f"{instance_id}: Version tag mismatch (expected {canonical.version_tag}, got {current_state.version_tag})",
                timestamp=current_state.timestamp,
                remediation_available=True,
                remediation_action="REDEPLOY_INSTANCE"
            ))

        self.drift_alerts.extend(drifts)
        return drifts

    def detect_all_drifts(self) -> List[DriftAlert]:
        """
        Run drift detection across all registered instances.
        Returns all detected drifts.
        """
        all_drifts = []
        for instance_id in self.instance_states.keys():
            if instance_id != self.canonical_state.instance_id:  # Skip canonical itself
                drifts = self.detect_drift(instance_id)
                all_drifts.extend(drifts)
        return all_drifts

    def get_canonical_state(self) -> ManifestSnapshot:
        """Return canonical deployment state"""
        if not self.canonical_state:
            raise RuntimeError("No canonical state set")
        return self.canonical_state

    def get_instance_state(self, instance_id: str) -> ManifestSnapshot:
        """Get deployment state for instance"""
        if instance_id not in self.instance_states:
            raise ValueError(f"Instance {instance_id} not registered")
        return self.instance_states[instance_id]

    def report_summary(self) -> str:
        """Generate human-readable drift report"""
        if not self.canonical_state:
            return "No canonical state set"

        lines = [
            "═" * 70,
            "DEPLOYMENT STATE REPORT",
            "═" * 70,
            f"Canonical: {self.canonical_state.instance_id}",
            f"  Git SHA: {self.canonical_state.git_sha}",
            f"  Version: {self.canonical_state.version_tag}",
            f"  Manifest: {self.canonical_state.manifest_hash[:16]}...",
            "",
            f"Instances Tracked: {len(self.instance_states)}",
            "",
        ]

        for instance_id, state in self.instance_states.items():
            if instance_id == self.canonical_state.instance_id:
                lines.append(f"✅ {instance_id} (CANONICAL)")
            else:
                drifts = self.detect_drift(instance_id)
                if not drifts:
                    lines.append(f"✅ {instance_id} (SYNCED)")
                else:
                    lines.append(f"🔴 {instance_id} (DRIFTED)")
                    for drift in drifts:
                        lines.append(f"   - {drift.alert_type}: {drift.message}")

        if self.drift_alerts:
            lines.append("")
            lines.append(f"Total Drifts Detected: {len(self.drift_alerts)}")
            lines.append(f"Critical: {len([d for d in self.drift_alerts if d.severity == DriftSeverity.CRITICAL])}")

        lines.append("═" * 70)
        return "\n".join(lines)


# Global singleton for managing deployment state
_deployment_state_manager = None


def get_deployment_manager() -> DeploymentStateManager:
    """Get or create global deployment state manager"""
    global _deployment_state_manager
    if _deployment_state_manager is None:
        _deployment_state_manager = DeploymentStateManager()
    return _deployment_state_manager
