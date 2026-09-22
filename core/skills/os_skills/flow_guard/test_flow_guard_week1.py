"""
Unit Tests: Flow Guard Week 1-2 (Data Classification + Policy Engine)

Tests the data classifier and flow policy components (350 LoC foundations).
Target: 28 tests, all green before proceeding to Week 2-4.

Test coverage:
  - 8 classifier tests (credentials, emails, PII patterns)
  - 10 policy tests (rule management, decision logic)
  - 10 integration tests (classifier + policy)
"""

import pytest
from datetime import datetime

from .data_classifier import (
    DataClassifier,
    DataClassification,
    CredentialsDetector,
    EmailDetector,
    PhoneNumberDetector,
    SSNDetector,
    URLDetector,
)
from .flow_policy import (
    FlowPolicy,
    FlowPolicyManager,
    FlowDecision,
    PolicyRule,
    FlowOutcome,
)
from .flow_guard import FlowGuard, FlowBlockReason


class TestCredentialsDetector:
    """Test credentials detection."""

    def test_aws_key_detection(self):
        detector = CredentialsDetector()
        result = detector.detect("AKIA1234567890ABCDEF")
        assert result is not None
        assert result.data_class == DataClassification.CREDENTIALS
        assert result.confidence >= 0.90

    def test_api_key_detection(self):
        detector = CredentialsDetector()
        result = detector.detect("api_key=sk-1234567890abcdefghij")
        assert result is not None
        assert result.data_class == DataClassification.CREDENTIALS
        assert result.confidence >= 0.80

    def test_private_key_detection(self):
        detector = CredentialsDetector()
        result = detector.detect("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg...\n-----END PRIVATE KEY-----")
        assert result is not None
        assert result.data_class == DataClassification.CREDENTIALS
        assert result.confidence >= 0.99

    def test_no_credentials_in_normal_text(self):
        detector = CredentialsDetector()
        result = detector.detect("This is a normal sentence without any credentials")
        assert result is None


class TestEmailDetector:
    """Test email classification."""

    def test_personal_email_gmail(self):
        detector = EmailDetector()
        result = detector.detect("user@gmail.com")
        assert result is not None
        assert result.data_class == DataClassification.PERSONAL_EMAIL
        assert result.confidence >= 0.90

    def test_personal_email_yahoo(self):
        detector = EmailDetector()
        result = detector.detect("john.doe@yahoo.com")
        assert result is not None
        assert result.data_class == DataClassification.PERSONAL_EMAIL

    def test_business_email(self):
        detector = EmailDetector()
        result = detector.detect("user@company.com")
        assert result is not None
        assert result.data_class == DataClassification.BUSINESS_EMAIL

    def test_no_email_in_text(self):
        detector = EmailDetector()
        result = detector.detect("This has no email address")
        assert result is None


class TestPhoneNumberDetector:
    """Test phone number detection."""

    def test_us_phone_format1(self):
        detector = PhoneNumberDetector()
        result = detector.detect("(123) 456-7890")
        assert result is not None
        assert result.data_class == DataClassification.PHONE_NUMBER
        assert result.confidence >= 0.80

    def test_us_phone_format2(self):
        detector = PhoneNumberDetector()
        result = detector.detect("123-456-7890")
        assert result is not None
        assert result.data_class == DataClassification.PHONE_NUMBER

    def test_international_phone(self):
        detector = PhoneNumberDetector()
        result = detector.detect("+49 123 456789")
        assert result is not None
        assert result.data_class == DataClassification.PHONE_NUMBER

    def test_no_phone_number(self):
        detector = PhoneNumberDetector()
        result = detector.detect("No phone numbers here")
        assert result is None


class TestSSNDetector:
    """Test SSN detection."""

    def test_ssn_detection(self):
        detector = SSNDetector()
        result = detector.detect("123-45-6789")
        assert result is not None
        assert result.data_class == DataClassification.SSN
        assert result.confidence >= 0.95

    def test_no_ssn(self):
        detector = SSNDetector()
        result = detector.detect("123-45-678")  # Incomplete
        assert result is None


class TestURLDetector:
    """Test URL detection and classification."""

    def test_public_url_github(self):
        detector = URLDetector()
        result = detector.detect("https://github.com/username/repo")
        assert result is not None
        assert result.data_class == DataClassification.PUBLIC_URL
        assert result.confidence >= 0.90

    def test_public_url_wikipedia(self):
        detector = URLDetector()
        result = detector.detect("https://wikipedia.org/wiki/Example")
        assert result is not None
        assert result.data_class == DataClassification.PUBLIC_URL

    def test_non_public_url(self):
        detector = URLDetector()
        result = detector.detect("https://internal.company.local/api")
        assert result is not None
        assert result.data_class == DataClassification.UNKNOWN
        assert result.confidence < 0.50


class TestDataClassifier:
    """Test the main data classifier."""

    def test_classify_credentials(self):
        classifier = DataClassifier()
        result = classifier.classify("AKIA1234567890ABCDEF")
        assert result.data_class == DataClassification.CREDENTIALS
        assert result.confidence >= 0.90

    def test_classify_email(self):
        classifier = DataClassifier()
        result = classifier.classify("alice@gmail.com")
        assert result.data_class == DataClassification.PERSONAL_EMAIL

    def test_classify_phone(self):
        classifier = DataClassifier()
        result = classifier.classify("555-123-4567")
        assert result.data_class == DataClassification.PHONE_NUMBER

    def test_classify_unknown_text(self):
        classifier = DataClassifier()
        result = classifier.classify("Lorem ipsum dolor sit amet")
        assert result.data_class == DataClassification.UNKNOWN
        assert result.confidence < 0.50

    def test_classify_bulk(self):
        classifier = DataClassifier()
        results = classifier.classify_bulk([
            "user@gmail.com",
            "555-123-4567",
            "random text",
        ])
        assert len(results) == 3
        assert results[0].data_class == DataClassification.PERSONAL_EMAIL
        assert results[1].data_class == DataClassification.PHONE_NUMBER
        assert results[2].data_class == DataClassification.UNKNOWN

    def test_classify_empty_raises_error(self):
        classifier = DataClassifier()
        with pytest.raises(ValueError):
            classifier.classify("")

    def test_classify_none_raises_error(self):
        classifier = DataClassifier()
        with pytest.raises(ValueError):
            classifier.classify(None)


class TestFlowPolicy:
    """Test the flow policy engine."""

    def test_add_allow_rule(self):
        policy = FlowPolicy(tenant_id="test")
        rule = PolicyRule(
            data_class="personal_email",
            destination_engine="anthropic/claude-opus-5",
            decision=FlowDecision.ALLOW,
            confidence=0.85,
        )
        policy.add_rule(rule)
        assert len(policy.rules) == 1
        assert policy.rules[0].decision == FlowDecision.ALLOW

    def test_add_deny_rule(self):
        policy = FlowPolicy(tenant_id="test")
        rule = PolicyRule(
            data_class="credentials",
            destination_engine="*",
            decision=FlowDecision.DENY,
            confidence=1.0,
        )
        policy.add_rule(rule)
        assert len(policy.rules) == 1
        assert policy.rules[0].decision == FlowDecision.DENY

    def test_duplicate_rule_updates_confidence(self):
        policy = FlowPolicy(tenant_id="test")
        rule1 = PolicyRule(
            data_class="personal_email",
            destination_engine="anthropic/claude-opus-5",
            decision=FlowDecision.ALLOW,
            confidence=0.70,
        )
        rule2 = PolicyRule(
            data_class="personal_email",
            destination_engine="anthropic/claude-opus-5",
            decision=FlowDecision.ALLOW,
            confidence=0.85,
        )
        policy.add_rule(rule1)
        policy.add_rule(rule2)
        # Should update, not add duplicate
        assert len(policy.rules) == 1
        assert policy.rules[0].confidence == 0.85
        assert policy.rules[0].feedback_count == 2

    def test_deny_rule_never_weakens(self):
        policy = FlowPolicy(tenant_id="test")
        rule1 = PolicyRule(
            data_class="credentials",
            destination_engine="*",
            decision=FlowDecision.DENY,
            confidence=0.95,
        )
        rule2 = PolicyRule(
            data_class="credentials",
            destination_engine="*",
            decision=FlowDecision.DENY,
            confidence=0.70,  # Lower confidence
        )
        policy.add_rule(rule1)
        policy.add_rule(rule2)
        # Should keep higher confidence (never weaken)
        assert policy.rules[0].confidence == 0.95

    def test_get_decision_allow(self):
        policy = FlowPolicy(tenant_id="test")
        rule = PolicyRule(
            data_class="personal_email",
            destination_engine="anthropic/claude-opus-5",
            decision=FlowDecision.ALLOW,
            confidence=0.85,
        )
        policy.add_rule(rule)
        decision = policy.get_decision(
            "personal_email",
            "anthropic/claude-opus-5",
            confidence_threshold=0.70,
        )
        assert decision == FlowDecision.ALLOW

    def test_get_decision_deny(self):
        policy = FlowPolicy(tenant_id="test")
        rule = PolicyRule(
            data_class="credentials",
            destination_engine="*",
            decision=FlowDecision.DENY,
            confidence=1.0,
        )
        policy.add_rule(rule)
        decision = policy.get_decision(
            "credentials",
            "anthropic/claude-opus-5",
            confidence_threshold=0.70,
        )
        assert decision == FlowDecision.DENY

    def test_get_decision_uncertain(self):
        policy = FlowPolicy(tenant_id="test")
        # No rules for this pair
        decision = policy.get_decision(
            "personal_email",
            "unknown-engine",
            confidence_threshold=0.70,
        )
        assert decision == FlowDecision.UNCERTAIN

    def test_get_decision_low_confidence(self):
        policy = FlowPolicy(tenant_id="test")
        rule = PolicyRule(
            data_class="personal_email",
            destination_engine="anthropic/claude-opus-5",
            decision=FlowDecision.ALLOW,
            confidence=0.50,  # Below threshold
        )
        policy.add_rule(rule)
        decision = policy.get_decision(
            "personal_email",
            "anthropic/claude-opus-5",
            confidence_threshold=0.70,
        )
        assert decision == FlowDecision.UNCERTAIN

    def test_update_from_outcome_success(self):
        policy = FlowPolicy(tenant_id="test")
        outcome = FlowOutcome(
            data_class="personal_email",
            destination_engine="anthropic/claude-opus-5",
            flow_allowed=True,
            result="success",
        )
        policy.update_from_outcome(outcome)
        # Should add ALLOW rule with high confidence
        allow_rules = [r for r in policy.rules if r.decision == FlowDecision.ALLOW]
        assert len(allow_rules) > 0
        assert allow_rules[0].confidence >= 0.85

    def test_update_from_outcome_pii_leak(self):
        policy = FlowPolicy(tenant_id="test")
        outcome = FlowOutcome(
            data_class="personal_email",
            destination_engine="unknown-engine",
            flow_allowed=False,
            result="pii_leak_detected",
        )
        policy.update_from_outcome(outcome)
        # Should add DENY rule with high confidence
        deny_rules = [r for r in policy.rules if r.decision == FlowDecision.DENY]
        assert len(deny_rules) > 0
        assert deny_rules[0].confidence >= 0.90


class TestFlowGuard:
    """Test the main Flow Guard skill."""

    def test_flow_guard_blocks_credentials(self):
        guard = FlowGuard(tenant_id="test")
        eval = guard.evaluate_flow(
            data="AKIA1234567890ABCDEF",
            destination_engine="anthropic/claude-opus-5",
        )
        assert eval.decision == FlowDecision.DENY
        assert eval.block_reason == FlowBlockReason.CREDENTIALS_DETECTED

    def test_flow_guard_uncertain_email(self):
        guard = FlowGuard(tenant_id="test")
        eval = guard.evaluate_flow(
            data="user@gmail.com",
            destination_engine="anthropic/claude-opus-5",
        )
        # No consent provided, no policy
        assert eval.decision == FlowDecision.DENY
        assert eval.data_class == "personal_email"

    def test_flow_guard_allow_with_consent(self):
        guard = FlowGuard(tenant_id="test")
        # Add allow policy
        from .flow_policy import PolicyRule
        rule = PolicyRule(
            data_class="personal_email",
            destination_engine="anthropic/claude-opus-5",
            decision=FlowDecision.ALLOW,
            confidence=0.85,
        )
        guard.add_policy_rule(rule)

        eval = guard.evaluate_flow(
            data="user@gmail.com",
            destination_engine="anthropic/claude-opus-5",
            user_consent={"personal_email": True},
        )
        assert eval.decision == FlowDecision.ALLOW

    def test_record_outcome_success(self):
        guard = FlowGuard(tenant_id="test")
        guard.record_outcome(
            data_class="personal_email",
            destination_engine="anthropic/claude-opus-5",
            result="success",
        )
        # Policy should now have ALLOW rule
        policy = guard.get_policy()
        allow_rules = [r for r in policy.rules if r.decision == FlowDecision.ALLOW]
        assert len(allow_rules) > 0

    def test_invalid_data_raises_error(self):
        guard = FlowGuard(tenant_id="test")
        with pytest.raises(ValueError):
            guard.evaluate_flow(data="", destination_engine="anthropic/claude-opus-5")

    def test_invalid_engine_raises_error(self):
        guard = FlowGuard(tenant_id="test")
        with pytest.raises(ValueError):
            guard.evaluate_flow(data="test", destination_engine="")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
