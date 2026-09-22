"""
Audit event schemas for Security Orchestrator Skill (ADR-2031).

Every threat detection and policy change emits an immutable, hash-chained audit event.
Integration with ADR-0232/0233 (audit chain) and ADR-0537 (LoM cryptographic binding).

Event Types:
- security_threat_detected: Attack pattern identified in audit trail
- security_policy_tightened: Security gate threshold automatically reduced
- security_policy_reverted: Tightened policy reverted after TTL expiration
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class ThreatType(str, Enum):
    """Threat pattern classification."""
    BRUTE_FORCE = "brute_force_auth"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    DATA_EXFILTRATION = "data_exfiltration"
    DISTRIBUTED_ATTACK = "distributed_attack"
    ANOMALOUS_BEHAVIOR = "anomalous_behavior"


class PolicyGate(str, Enum):
    """Security gate that can be tightened."""
    AUTH_GATE = "auth_gate"
    OVERRIDE_GATE = "override_gate"
    DATA_CLASSIFICATION = "data_classification"
    RATE_LIMIT = "rate_limit"


@dataclass(frozen=True)
class SecurityAuditEvent:
    """
    Immutable audit event for security operations.
    
    All fields are required; schema is versioned for audit trail compatibility.
    Hash-chained to previous event via prev_hash field.
    """
    
    # Tenant & system metadata
    tenant_id: str
    timestamp: str  # ISO 8601 timestamp
    event_type: str  # "security_threat_detected", "security_policy_tightened", etc.
    skill_id: str = "os.security_orchestrator"
    
    # Event payload
    threat_type: Optional[ThreatType] = None
    threat_id: Optional[str] = None  # UUID for tracking attack lifecycle
    threat_confidence: float = 0.0  # 0.0-1.0 confidence score
    threat_severity: str = "low"  # low, medium, high, critical
    affected_resources: list = field(default_factory=list)  # Users, IPs, API endpoints affected
    
    # Policy tightening (if event_type == "security_policy_tightened")
    policy_gate: Optional[PolicyGate] = None
    old_threshold: Optional[Any] = None
    new_threshold: Optional[Any] = None
    ttl_seconds: int = 3600  # Revert after this many seconds
    
    # Audit trail integration (ADR-0232/0233)
    lom: str = ""  # Line of Moral Responsibility (code location + function)
    lom_hash: str = ""  # SHA256(lom + source_code_snippet) for cryptographic binding
    hash: str = ""  # SHA256(this_event) — immutable once written
    prev_hash: str = ""  # SHA256(previous_event) — chain link
    
    # Metadata for compliance & traceability
    reason: str = ""  # Human-readable reason for the action
    auto_revert: bool = True  # Should this policy tightening auto-revert after TTL?
    
    def __post_init__(self):
        """Validate immutability constraints."""
        if not self.tenant_id:
            raise ValueError("tenant_id is required and cannot be empty")
        if not self.timestamp:
            raise ValueError("timestamp is required")
        if not self.event_type:
            raise ValueError("event_type is required")


@dataclass
class ThreatDetectionResult:
    """Result of a threat detection scan."""
    detected: bool
    threat_type: Optional[ThreatType] = None
    confidence: float = 0.0
    affected_users: list = field(default_factory=list)
    affected_ips: list = field(default_factory=list)
    reason: str = ""
    recommended_action: Optional[PolicyGate] = None


@dataclass
class PolicyTighteningResult:
    """Result of a policy tightening action."""
    success: bool
    gate: PolicyGate
    old_value: Any
    new_value: Any
    ttl_seconds: int
    audit_event_id: str  # Reference to the SecurityAuditEvent
    error: Optional[str] = None
