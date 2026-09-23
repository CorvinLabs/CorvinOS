"""
Comprehensive Unit Tests: Flow Guard Week 1 (Data Classification + Policy Engine)

Tests the data classifier and flow policy components.
Target: 14+ tests, all passing, ≥95% classification accuracy.

Run with: python3 -m unittest test_flow_guard_comprehensive -v
"""

import unittest
import sys

from core.skills.os_skills.flow_guard.data_classifier import (
    DataClassifier,
    DataClassification,
    CredentialsDetector,
    EmailDetector,
    PhoneNumberDetector,
    SSNDetector,
    URLDetector,
)
from core.skills.os_skills.flow_guard.flow_policy import (
    FlowPolicy,
    FlowPolicyManager,
    FlowDecision,
    PolicyRule,
)
from core.skills.os_skills.flow_guard.flow_guard import FlowGuard, FlowBlockReason


class TestCredentialsDetector(unittest.TestCase):
    def setUp(self):
        self.detector = CredentialsDetector()

    def test_aws_key_detection(self):
        result = self.detector.detect("AKIA1234567890ABCDEF")
        self.assertIsNotNone(result)
        self.assertEqual(result.data_class, DataClassification.CREDENTIALS)
        self.assertGreaterEqual(result.confidence, 0.90)

    def test_api_key_detection(self):
        result = self.detector.detect("api_key=sk-1234567890abcdefghij")
        self.assertIsNotNone(result)
        self.assertEqual(result.data_class, DataClassification.CREDENTIALS)
        self.assertGreaterEqual(result.confidence, 0.80)

    def test_private_key_detection(self):
        result = self.detector.detect(
            "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg...\n-----END PRIVATE KEY-----"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.data_class, DataClassification.CREDENTIALS)
        self.assertGreaterEqual(result.confidence, 0.99)

    def test_no_credentials_in_normal_text(self):
        result = self.detector.detect("This is a normal sentence without any credentials")
        self.assertIsNone(result)


class TestEmailDetector(unittest.TestCase):
    def setUp(self):
        self.detector = EmailDetector()

    def test_personal_email_gmail(self):
        result = self.detector.detect("user@gmail.com")
        self.assertIsNotNone(result)
        self.assertEqual(result.data_class, DataClassification.PERSONAL_EMAIL)
        self.assertGreaterEqual(result.confidence, 0.90)

    def test_business_email(self):
        result = self.detector.detect("user@company.com")
        self.assertIsNotNone(result)
        self.assertEqual(result.data_class, DataClassification.BUSINESS_EMAIL)


class TestPhoneNumberDetector(unittest.TestCase):
    def setUp(self):
        self.detector = PhoneNumberDetector()

    def test_us_phone_format(self):
        result = self.detector.detect("(123) 456-7890")
        self.assertIsNotNone(result)
        self.assertEqual(result.data_class, DataClassification.PHONE_NUMBER)


class TestSSNDetector(unittest.TestCase):
    def setUp(self):
        self.detector = SSNDetector()

    def test_ssn_detection(self):
        result = self.detector.detect("123-45-6789")
        self.assertIsNotNone(result)
        self.assertEqual(result.data_class, DataClassification.SSN)
        self.assertGreaterEqual(result.confidence, 0.95)


class TestDataClassifier(unittest.TestCase):
    def setUp(self):
        self.classifier = DataClassifier()

    def test_classifier_accuracy_95_percent(self):
        """Classifier should achieve ≥95% accuracy on test dataset (Week 1 Gate)."""
        test_cases = [
            ("AKIA1234567890ABCDEF", DataClassification.CREDENTIALS),
            ("api_key=sk-1234567890abcdefghij", DataClassification.CREDENTIALS),
            ("user@gmail.com", DataClassification.PERSONAL_EMAIL),
            ("john@yahoo.com", DataClassification.PERSONAL_EMAIL),
            ("user@company.com", DataClassification.BUSINESS_EMAIL),
            ("555-123-4567", DataClassification.PHONE_NUMBER),
            ("123-45-6789", DataClassification.SSN),
            ("+49 123 456789", DataClassification.PHONE_NUMBER),
            ("https://github.com/user/repo", DataClassification.PUBLIC_URL),
            ("https://wikipedia.org/wiki/Test", DataClassification.PUBLIC_URL),
        ]
        
        correct = 0
        for data, expected_class in test_cases:
            result = self.classifier.classify(data)
            if result.data_class == expected_class:
                correct += 1
        
        accuracy = correct / len(test_cases)
        print(f"\n📊 Classifier Accuracy: {accuracy:.1%} ({correct}/{len(test_cases)})")
        self.assertGreaterEqual(accuracy, 0.95, f"Accuracy {accuracy:.1%} < 95%")


class TestFlowPolicy(unittest.TestCase):
    def setUp(self):
        self.policy = FlowPolicy(tenant_id="test")

    def test_deny_rule_never_weakens(self):
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
            confidence=0.70,
        )
        self.policy.add_rule(rule1)
        self.policy.add_rule(rule2)
        self.assertEqual(self.policy.rules[0].confidence, 0.95)


class TestFlowGuard(unittest.TestCase):
    def setUp(self):
        self.guard = FlowGuard(tenant_id="test")

    def test_flow_guard_tenant_id_required(self):
        with self.assertRaises(ValueError):
            FlowGuard(tenant_id="")

    def test_evaluate_flow_credentials_always_deny(self):
        evaluation = self.guard.evaluate_flow(
            data="AKIA1234567890ABCDEF",
            destination_engine="anthropic/claude-opus-5",
        )
        self.assertEqual(evaluation.decision, FlowDecision.DENY)
        self.assertEqual(evaluation.block_reason, FlowBlockReason.CREDENTIALS_DETECTED)

    def test_evaluate_flow_audit_event_format(self):
        evaluation = self.guard.evaluate_flow(
            data="user@gmail.com",
            destination_engine="anthropic/claude-opus-5",
        )
        audit_event = evaluation.to_audit_dict()
        self.assertIn("event_type", audit_event)
        self.assertIn("skill_id", audit_event)
        self.assertIn("lom", audit_event)
        self.assertEqual(audit_event["skill_id"], "os.flow_guard")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromModule(sys.modules[__name__]))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("\n" + "="*70)
    print(f"✅ {result.testsRun} tests passing") if result.wasSuccessful() else print(f"❌ Tests failed")
    print("="*70)
