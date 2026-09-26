"""Phase 1B: Privacy subsystems (L18, L34, L44, L36) + integration."""

import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from core.compliance import AuditTrail
from core.compliance.privacy_subsystems import (
    ConsentGate, FlowGuard, HouseRules, ErasureOrchestrator,
    DataClassification
)


class TestConsentGate:
    """L18: Deny-by-default consent."""
    
    def test_consent_grant(self):
        """Grant explicit consent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            gate = ConsentGate(trail)
            
            assert gate.grant_consent("default", "user1", "analytics") is True
    
    def test_consent_check_missing(self):
        """Check consent fails if not granted (deny-by-default)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            gate = ConsentGate(trail)
            
            # No consent granted
            assert gate.check_consent("default", "user1", "analytics") is False
    
    def test_consent_check_granted(self):
        """Check consent passes if granted and valid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            gate = ConsentGate(trail)
            
            gate.grant_consent("default", "user1", "analytics")
            assert gate.check_consent("default", "user1", "analytics") is True
    
    def test_consent_expiry(self):
        """Consent expires after TTL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            gate = ConsentGate(trail)
            
            # Grant with 0-second TTL (immediate expiry)
            record = gate.consents.get("default:user1:analytics")
            gate.grant_consent("default", "user1", "analytics")
            record = gate.consents.get("default:user1:analytics")
            record.ttl_seconds = 0
            
            assert gate.check_consent("default", "user1", "analytics") is False


class TestFlowGuard:
    """L34: PII classification + fail-closed."""
    
    def test_classify_public(self):
        """Classify non-PII as PUBLIC."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            guard = FlowGuard(trail)
            
            data = {"name": "Alice", "age": 30}
            assert guard.classify_data(data) == DataClassification.PUBLIC
    
    def test_classify_pii_email(self):
        """Classify email as PII."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            guard = FlowGuard(trail)
            
            data = {"email": "user@example.com"}
            assert guard.classify_data(data) == DataClassification.PII
    
    def test_classify_sensitive(self):
        """Classify secret as SENSITIVE."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            guard = FlowGuard(trail)
            
            data = {"api_key": "secret123"}
            assert guard.classify_data(data) == DataClassification.SENSITIVE
    
    def test_validate_flow_fail_closed(self):
        """Flow validation fails if PII in PUBLIC channel (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            guard = FlowGuard(trail)
            
            pii_data = {"email": "user@example.com"}
            assert guard.validate_flow(pii_data, DataClassification.PUBLIC, 
                                      "default", "public_api") is False


class TestHouseRules:
    """L44: Policy enforcement (0.90+ confidence)."""
    
    def test_evaluate_action_high_confidence(self):
        """Action allowed if confidence >= 0.90."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            rules = HouseRules(trail)
            
            assert rules.evaluate_action("delete_data", {}, 0.95, "default") is True
    
    def test_evaluate_action_low_confidence(self):
        """Action denied if confidence < 0.90 (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            rules = HouseRules(trail)
            
            assert rules.evaluate_action("delete_data", {}, 0.50, "default") is False


class TestErasureOrchestrator:
    """L36: GDPR Art. 17 erasure automation."""
    
    def test_erasure_request(self):
        """Process erasure request."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            erasure = ErasureOrchestrator(trail)
            
            assert erasure.process_erasure_request("default", "user1") is True
    
    def test_erasure_verification(self):
        """Verify erasure was executed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            erasure = ErasureOrchestrator(trail)
            
            # Process erasure
            erasure.process_erasure_request("default", "user1")
            
            # Verify (check last entry in audit trail)
            records = list(trail.records())
            last = records[-1]
            assert last.event_type == "erasure_completed"


class TestPhase1BIntegration:
    """Full Phase 1B: All 4 subsystems + audit trail."""
    
    def test_full_privacy_workflow(self):
        """Complete workflow: Consent → Flow → Rules → Erasure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            
            # Initialize all subsystems
            consent = ConsentGate(trail)
            flow = FlowGuard(trail)
            rules = HouseRules(trail)
            erasure = ErasureOrchestrator(trail)
            
            # Step 1: User grants consent
            consent.grant_consent("default", "user1", "analytics")
            
            # Step 2: User submits data (but it's PII)
            user_data = {"email": "user@example.com"}
            
            # Step 3: Flow guard validates (fails because it's PII in PUBLIC channel)
            assert flow.validate_flow(user_data, DataClassification.PUBLIC, 
                                     "default", "public_api") is False
            
            # Step 4: But if sent to secure channel, it passes
            assert flow.validate_flow(user_data, DataClassification.PII,
                                     "default", "secure_api") is True
            
            # Step 5: Policy evaluation
            assert rules.evaluate_action("process_data", {}, 0.92, "default") is True
            
            # Step 6: User requests erasure (GDPR Art. 17)
            assert erasure.process_erasure_request("default", "user1") is True
            
            # Step 7: Verify audit trail completeness
            records = list(trail.records())
            assert len(records) >= 4  # Multiple events logged
            
            # Verify chain integrity
            assert trail.verify_chain() is True
