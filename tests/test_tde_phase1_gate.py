"""Tests for TDE Phase 1: L34DelegationGate (10+ tests)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.l34_delegation_gate import L34DelegationGate
from initial_analysis import GlobalPlan, Step


class TestL34DelegationGate:
    """Test L34 Data-Safe Gate (fail-closed)."""

    @pytest.fixture
    def gate(self):
        """Create gate without real L34 classifier (use heuristic)."""
        return L34DelegationGate(l34_classifier=None)

    @pytest.fixture
    def basic_step(self):
        """Create basic step."""
        return Step(step=1, action="refactor", depends_on=[], can_parallelize=[])

    def test_gate_initialization(self, gate):
        """Gate initializes correctly."""
        assert gate.QUALITY_THRESHOLD == 0.05
        assert "PUBLIC" in gate.CLASSIFICATIONS

    def test_can_delegate_public_data(self, gate, basic_step):
        """PUBLIC data → can delegate."""
        statement = {"filename": "utils.py", "content": "def helper(): pass"}
        result = gate.can_delegate_step(basic_step, statement, max_classification="PUBLIC")
        assert result.can_delegate is True

    def test_cannot_delegate_confidential(self, gate, basic_step):
        """CONFIDENTIAL data → cannot delegate (fail-closed)."""
        statement = {
            "customer_email": "john@example.com",
            "filename": "index.py",
        }
        result = gate.can_delegate_step(basic_step, statement, max_classification="PUBLIC")
        # Email is CONFIDENTIAL, exceeds PUBLIC
        assert result.can_delegate is False

    def test_cannot_delegate_restricted(self, gate, basic_step):
        """RESTRICTED data → cannot delegate."""
        statement = {
            "api_key": "sk_live_abcd123",
            "filename": "config.py",
        }
        result = gate.can_delegate_step(basic_step, statement, max_classification="INTERNAL")
        # API key is RESTRICTED
        assert result.can_delegate is False

    def test_internal_data_allowed_if_max_internal(self, gate, basic_step):
        """INTERNAL data OK if max_classification=INTERNAL."""
        statement = {
            "database_url": "postgresql://internal.db",
            "filename": "models.py",
        }
        result = gate.can_delegate_step(basic_step, statement, max_classification="INTERNAL")
        assert result.can_delegate is True

    def test_internal_data_blocked_if_max_public(self, gate, basic_step):
        """INTERNAL data blocked if max_classification=PUBLIC."""
        statement = {
            "database_url": "postgresql://internal.db",
            "filename": "models.py",
        }
        result = gate.can_delegate_step(basic_step, statement, max_classification="PUBLIC")
        # Database URL is INTERNAL, exceeds PUBLIC
        assert result.can_delegate is False

    def test_sanitize_snapshot_filters_dangerous(self, gate):
        """Snapshot sanitization removes dangerous vars."""
        statement = {
            "code": "def foo(): pass",
            "password": "secret123",
            "api_key": "sk_live_xyz",
        }
        snapshot = gate.sanitize_snapshot(
            statement,
            required_vars={"code", "password", "api_key"},
            max_classification="PUBLIC",
        )
        # Code is PUBLIC: included
        assert "code" in snapshot
        assert snapshot["code"] == "def foo(): pass"

        # Password + API key are blocked: redacted
        assert "[RESTRICTED_DATA_REDACTED]" in str(snapshot.get("password", ""))
        assert "[RESTRICTED_DATA_REDACTED]" in str(snapshot.get("api_key", ""))

    def test_filter_plan_removes_entities(self, gate):
        """Plan filtering removes sensitive entities."""
        plan = GlobalPlan(
            steps=[
                Step(step=1, action="read user john@example.com data", depends_on=[], can_parallelize=[]),
                Step(step=2, action="call API with key sk_live_xyz", depends_on=[1], can_parallelize=[]),
            ],
            estimated_duration_s=10,
            estimated_tokens=5000,
        )
        filtered = gate.filter_plan(plan, max_classification="INTERNAL")

        # Check that email and key were redacted
        step1_action = filtered.steps[0].action
        step2_action = filtered.steps[1].action

        assert "john@example.com" not in step1_action or "[EMAIL_REDACTED]" in step1_action
        assert "sk_live_xyz" not in step2_action or "[PHONE_REDACTED]" in step2_action

    def test_classification_heuristic_email(self, gate):
        """Email heuristic classifies as CONFIDENTIAL."""
        data_class = gate._classify_variable("user_email", "test@example.com")
        assert data_class == "CONFIDENTIAL"

    def test_classification_heuristic_password(self, gate):
        """Password heuristic classifies as RESTRICTED."""
        data_class = gate._classify_variable("password", "secret")
        assert data_class == "RESTRICTED"

    def test_classification_heuristic_code(self, gate):
        """Code defaults to PUBLIC (no sensitive patterns)."""
        data_class = gate._classify_variable("code", "def foo(): pass")
        assert data_class == "PUBLIC"

    def test_exceeds_max_check(self, gate):
        """Exceeds-max ranking correct."""
        assert gate._exceeds_max("RESTRICTED", "INTERNAL") is True  # RESTRICTED > INTERNAL
        assert gate._exceeds_max("INTERNAL", "PUBLIC") is True  # INTERNAL > PUBLIC
        assert gate._exceeds_max("PUBLIC", "PUBLIC") is False  # Equal
        assert gate._exceeds_max("PUBLIC", "INTERNAL") is False  # PUBLIC < INTERNAL
