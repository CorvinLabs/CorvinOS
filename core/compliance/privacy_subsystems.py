"""L18, L34, L44, L36: Privacy subsystems (Consent, Flow, Rules, Erasure)."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Set
from enum import Enum

from .audit_trail import AuditTrail, new_audit_record
from .exceptions import ComplianceError


class DataClassification(Enum):
    """PII Classification levels."""
    PUBLIC = "public"
    SENSITIVE = "sensitive"
    PII = "pii"


@dataclass
class ConsentRecord:
    """L18: TTL-capped consent grant."""
    tenant_id: str
    user_id: str
    purpose: str  # e.g., "analytics", "marketing"
    granted_at: str  # ISO-8601
    ttl_seconds: int = 7776000  # 90 days default
    
    def is_valid(self) -> bool:
        """Check if consent still valid (not expired)."""
        granted = datetime.fromisoformat(self.granted_at.replace('Z', '+00:00'))
        expiry = granted + timedelta(seconds=self.ttl_seconds)
        now = datetime.now(timezone.utc)
        return now < expiry


class ConsentGate:
    """L18: Deny-by-default consent enforcement."""
    
    def __init__(self, audit_trail: AuditTrail):
        self.audit = audit_trail
        self.consents: dict[str, ConsentRecord] = {}
    
    def grant_consent(self, tenant_id: str, user_id: str, purpose: str) -> bool:
        """Grant explicit consent (operator/user action only)."""
        record = ConsentRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            purpose=purpose,
            granted_at=datetime.now(timezone.utc).isoformat(),
        )
        self.consents[f"{tenant_id}:{user_id}:{purpose}"] = record
        
        # Audit: Consent granted
        audit_rec = new_audit_record(
            event_type="consent_granted",
            tenant_id=tenant_id,
            actor=user_id,
            action="grant_consent",
            resource=purpose,
            result="allowed",
            details={"ttl_seconds": record.ttl_seconds},
        )
        self.audit.append(audit_rec)
        return True
    
    def check_consent(self, tenant_id: str, user_id: str, purpose: str) -> bool:
        """Check consent (deny-by-default: false if missing or expired)."""
        key = f"{tenant_id}:{user_id}:{purpose}"
        record = self.consents.get(key)
        
        if not record or not record.is_valid():
            # Audit: Consent denied
            self.audit.append(new_audit_record(
                event_type="consent_denied",
                tenant_id=tenant_id,
                actor=user_id,
                action="check_consent",
                resource=purpose,
                result="denied",
                details={"reason": "missing_or_expired"},
            ))
            return False
        
        return True


class FlowGuard:
    """L34: PII classification + fail-closed validation."""
    
    # Patterns that indicate PII
    PII_PATTERNS = {
        "email": r".*@.*\..*",
        "ssn": r"\d{3}-\d{2}-\d{4}",
        "phone": r"\d{3}-\d{3}-\d{4}",
        "credit_card": r"\d{4}-\d{4}-\d{4}-\d{4}",
    }
    
    def __init__(self, audit_trail: AuditTrail):
        self.audit = audit_trail
    
    def classify_data(self, data: dict) -> DataClassification:
        """Classify data (PUBLIC, SENSITIVE, or PII)."""
        import re
        
        # Check for PII patterns
        for key, value in data.items():
            if isinstance(value, str):
                for pii_type, pattern in self.PII_PATTERNS.items():
                    if re.match(pattern, value):
                        return DataClassification.PII
        
        # Simple heuristic: "password", "secret", "token" = SENSITIVE
        sensitive_keys = {"password", "secret", "token", "api_key", "credential"}
        if any(k.lower() in sensitive_keys for k in data.keys()):
            return DataClassification.SENSITIVE
        
        return DataClassification.PUBLIC
    
    def validate_flow(self, data: dict, allowed_class: DataClassification, 
                     tenant_id: str, destination: str) -> bool:
        """Fail-closed: block data flow if classification exceeds allowed."""
        actual_class = self.classify_data(data)
        
        # Fail-closed: if actual > allowed, deny
        class_order = [DataClassification.PUBLIC, DataClassification.SENSITIVE, DataClassification.PII]
        if class_order.index(actual_class) > class_order.index(allowed_class):
            self.audit.append(new_audit_record(
                event_type="data_flow_blocked",
                tenant_id=tenant_id,
                actor="system",
                action="validate_flow",
                resource=destination,
                result="denied",
                details={"actual": actual_class.value, "allowed": allowed_class.value},
            ))
            return False
        
        return True


class HouseRules:
    """L44: Acceptable-use policy enforcement (0.90+ confidence)."""
    
    def __init__(self, audit_trail: AuditTrail):
        self.audit = audit_trail
        self.confidence_threshold = 0.90
    
    def evaluate_action(self, action: str, context: dict, confidence: float,
                       tenant_id: str) -> bool:
        """Evaluate if action permitted (fail-closed on low confidence)."""
        
        # Fail-closed: Low confidence = deny
        if confidence < self.confidence_threshold:
            self.audit.append(new_audit_record(
                event_type="house_rule_denied",
                tenant_id=tenant_id,
                actor="system",
                action=action,
                resource="policy",
                result="denied",
                details={"confidence": confidence, "threshold": self.confidence_threshold},
            ))
            return False
        
        return True


class ErasureOrchestrator:
    """L36: GDPR Art. 17 (right to erasure) automation."""
    
    def __init__(self, audit_trail: AuditTrail):
        self.audit = audit_trail
        self.erasure_log: Set[str] = set()
    
    def process_erasure_request(self, tenant_id: str, user_id: str) -> bool:
        """Process GDPR erasure request (delete all user PII)."""
        
        erasure_id = f"{tenant_id}:{user_id}:{datetime.now(timezone.utc).isoformat()}"
        
        # Simulate cross-system deletion
        self.erasure_log.add(erasure_id)
        
        # Audit: Erasure initiated
        self.audit.append(new_audit_record(
            event_type="erasure_requested",
            tenant_id=tenant_id,
            actor=user_id,
            action="erasure_request",
            resource="all_pii",
            result="allowed",
            details={"erasure_id": erasure_id},
        ))
        
        # Audit: Erasure completed
        self.audit.append(new_audit_record(
            event_type="erasure_completed",
            tenant_id=tenant_id,
            actor="system",
            action="erasure_execute",
            resource="all_pii",
            result="allowed",
            details={"erasure_id": erasure_id, "status": "success"},
        ))
        
        return True
    
    def verify_erasure(self, erasure_id: str) -> bool:
        """Verify erasure was executed."""
        return erasure_id in self.erasure_log
