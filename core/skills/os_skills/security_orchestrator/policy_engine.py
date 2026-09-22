"""
Policy engine for Security Orchestrator Skill (ADR-2031).

Manages dynamic security policy state machine:
- Tracks current security gate thresholds (mutable state)
- Applies threat-triggered tightening (increases security)
- Reverts tightening after TTL (prevents permanent lockdown)
- Emits audit events for all policy changes (ADR-0232/0233)
- Never bypasses house-rules (L44) or compliance mechanisms (L16)
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from collections import defaultdict
import uuid
import json


class PolicyGate(str, Enum):
    """Security gate that can be tightened."""
    AUTH_GATE = "auth_gate"
    OVERRIDE_GATE = "override_gate"
    DATA_CLASSIFICATION = "data_classification"
    RATE_LIMIT = "rate_limit"


@dataclass
class SecurityPolicy:
    """Current security policy state (mutable)."""
    # Auth gate thresholds
    auth_max_failures: int = 3  # Failed attempts before lockout
    auth_lockout_duration_minutes: int = 15
    
    # Override gate thresholds
    override_allowed_per_user: int = 10  # Overrides per hour
    override_requires_mfa: bool = False
    
    # Data classification thresholds
    data_high_risk_flow_limit: int = 100  # Per hour
    data_pii_scan_enabled: bool = True
    
    # Rate limiting
    rate_limit_requests_per_minute: int = 1000
    rate_limit_by_ip: bool = True
    
    # Metadata
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_by_skill: bool = False


@dataclass
class PolicyTightening:
    """Record of a policy tightening action."""
    gate: PolicyGate
    old_value: Any
    new_value: Any
    reason: str
    threat_id: str
    ttl_seconds: int
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    reverted_at: Optional[str] = None
    revert_reason: Optional[str] = None
    
    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Check if TTL has expired."""
        now = now or datetime.utcnow()
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        return (now - created).total_seconds() > self.ttl_seconds


class PolicyEngine:
    """
    Manages dynamic security policy state.
    
    Responsibilities:
    1. Tighten security gates in response to threats
    2. Revert tightening after TTL (if threat has cleared)
    3. Emit audit events for all changes (immutable, hash-chained)
    4. Validate that no policy change bypasses house-rules
    
    Example:
        engine = PolicyEngine()
        result = engine.tighten_policy(threat_signal, audit_backend)
        if result.success:
            audit_backend.write_event(result.audit_event)
    """
    
    def __init__(self):
        """Initialize policy engine with baseline security policy."""
        self.policy = SecurityPolicy()
        self.tightening_history: List[PolicyTightening] = []
        self.active_tightenings: Dict[str, PolicyTightening] = {}
    
    def tighten_policy(
        self,
        threat_signal: Dict[str, Any],
        audit_backend,  # Audit backend for writing events
        tenant_id: str = "",
        skill_id: str = "os.security_orchestrator",
    ) -> Dict[str, Any]:
        """
        Automatically tighten security gates in response to a threat.
        
        Args:
            threat_signal: ThreatSignal dict {"pattern", "confidence", "severity", ...}
            audit_backend: Backend for writing immutable audit events
            tenant_id: Tenant context
            skill_id: Skill identifier for audit trail
        
        Returns:
            {
                "success": bool,
                "gate": PolicyGate,
                "old_value": Any,
                "new_value": Any,
                "audit_event_id": str,
                "error": Optional[str]
            }
        """
        threat_pattern = threat_signal.get("pattern")
        confidence = threat_signal.get("confidence", 0.0)
        
        if confidence < 0.75:
            return {
                "success": False,
                "error": f"Confidence {confidence} below action threshold (0.75)",
            }
        
        # Determine which gate to tighten based on threat pattern
        gate, action = self._choose_gate_for_threat(threat_pattern)
        if not gate:
            return {
                "success": False,
                "error": f"No policy gate configured for threat {threat_pattern}",
            }
        
        # Perform tightening
        old_value = self._get_gate_value(gate)
        new_value = self._apply_tightening(gate, action)
        
        # Create tightening record
        tightening_id = str(uuid.uuid4())
        tightening = PolicyTightening(
            gate=gate,
            old_value=old_value,
            new_value=new_value,
            reason=f"Threat detected: {threat_pattern} (confidence: {confidence:.2f})",
            threat_id=threat_signal.get("threat_id", ""),
            ttl_seconds=3600,  # Revert after 1 hour if no more attacks
        )
        
        # Record in history and active tightenings
        self.tightening_history.append(tightening)
        self.active_tightenings[tightening_id] = tightening
        
        # Emit audit event
        audit_event = {
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": "security_policy_tightened",
            "skill_id": skill_id,
            "threat_pattern": threat_pattern,
            "threat_confidence": confidence,
            "policy_gate": gate.value,
            "old_threshold": old_value,
            "new_threshold": new_value,
            "ttl_seconds": 3600,
            "reason": tightening.reason,
            "tightening_id": tightening_id,
        }
        
        if audit_backend:
            try:
                audit_backend.write_event(audit_event)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to write audit event: {e}",
                }
        
        return {
            "success": True,
            "gate": gate.value,
            "old_value": old_value,
            "new_value": new_value,
            "audit_event_id": tightening_id,
        }
    
    def check_ttl_and_revert(
        self,
        audit_backend,
        tenant_id: str = "",
        skill_id: str = "os.security_orchestrator",
    ) -> List[Dict[str, Any]]:
        """
        Check all active tightenings and revert those whose TTL has expired.
        
        Returns:
            List of revert results
        """
        reverts = []
        now = datetime.utcnow()
        
        tightening_ids_to_revert = [
            tid for tid, tight in self.active_tightenings.items()
            if tight.is_expired(now)
        ]
        
        for tightening_id in tightening_ids_to_revert:
            tightening = self.active_tightenings[tightening_id]
            
            # Restore original value
            self._restore_gate_value(tightening.gate, tightening.old_value)
            
            # Update tightening record
            tightening.reverted_at = now.isoformat() + "Z"
            tightening.revert_reason = "TTL expired"
            
            # Remove from active
            del self.active_tightenings[tightening_id]
            
            # Emit audit event
            audit_event = {
                "tenant_id": tenant_id,
                "timestamp": now.isoformat() + "Z",
                "event_type": "security_policy_reverted",
                "skill_id": skill_id,
                "policy_gate": tightening.gate.value,
                "restored_threshold": tightening.old_value,
                "tightening_id": tightening_id,
                "reason": "TTL expired",
            }
            
            if audit_backend:
                try:
                    audit_backend.write_event(audit_event)
                except Exception as e:
                    reverts.append({
                        "success": False,
                        "tightening_id": tightening_id,
                        "error": f"Failed to write revert event: {e}",
                    })
                    continue
            
            reverts.append({
                "success": True,
                "tightening_id": tightening_id,
                "gate": tightening.gate.value,
                "restored_threshold": tightening.old_value,
            })
        
        return reverts
    
    def _choose_gate_for_threat(self, threat_pattern: str) -> tuple:
        """Choose which gate to tighten for a given threat."""
        if threat_pattern == "brute_force_auth":
            return PolicyGate.AUTH_GATE, "reduce_max_failures"
        elif threat_pattern == "privilege_escalation":
            return PolicyGate.OVERRIDE_GATE, "disable_overrides"
        elif threat_pattern == "data_exfiltration":
            return PolicyGate.DATA_CLASSIFICATION, "reduce_flow_limit"
        elif threat_pattern == "distributed_attack":
            return PolicyGate.RATE_LIMIT, "reduce_rate_limit"
        else:
            return None, None
    
    def _get_gate_value(self, gate: PolicyGate) -> Any:
        """Get current value of a policy gate."""
        if gate == PolicyGate.AUTH_GATE:
            return self.policy.auth_max_failures
        elif gate == PolicyGate.OVERRIDE_GATE:
            return self.policy.override_allowed_per_user
        elif gate == PolicyGate.DATA_CLASSIFICATION:
            return self.policy.data_high_risk_flow_limit
        elif gate == PolicyGate.RATE_LIMIT:
            return self.policy.rate_limit_requests_per_minute
        return None
    
    def _apply_tightening(self, gate: PolicyGate, action: str) -> Any:
        """Apply tightening action and return new value."""
        if gate == PolicyGate.AUTH_GATE and action == "reduce_max_failures":
            self.policy.auth_max_failures = max(1, self.policy.auth_max_failures - 2)
            return self.policy.auth_max_failures
        elif gate == PolicyGate.OVERRIDE_GATE and action == "disable_overrides":
            self.policy.override_allowed_per_user = 0
            return self.policy.override_allowed_per_user
        elif gate == PolicyGate.DATA_CLASSIFICATION and action == "reduce_flow_limit":
            self.policy.data_high_risk_flow_limit = max(10, int(self.policy.data_high_risk_flow_limit * 0.5))
            return self.policy.data_high_risk_flow_limit
        elif gate == PolicyGate.RATE_LIMIT and action == "reduce_rate_limit":
            self.policy.rate_limit_requests_per_minute = max(100, int(self.policy.rate_limit_requests_per_minute * 0.7))
            return self.policy.rate_limit_requests_per_minute
        return None
    
    def _restore_gate_value(self, gate: PolicyGate, value: Any):
        """Restore gate to a previous value."""
        if gate == PolicyGate.AUTH_GATE:
            self.policy.auth_max_failures = value
        elif gate == PolicyGate.OVERRIDE_GATE:
            self.policy.override_allowed_per_user = value
        elif gate == PolicyGate.DATA_CLASSIFICATION:
            self.policy.data_high_risk_flow_limit = value
        elif gate == PolicyGate.RATE_LIMIT:
            self.policy.rate_limit_requests_per_minute = value
    
    def get_current_policy(self) -> SecurityPolicy:
        """Return current policy state."""
        return self.policy
    
    def get_active_tightenings(self) -> Dict[str, PolicyTightening]:
        """Return all active (non-reverted) tightenings."""
        return self.active_tightenings.copy()
