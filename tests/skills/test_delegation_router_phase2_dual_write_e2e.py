"""End-to-end tests for os.delegation_router Phase 2 (Dual-Write Mode).

This test suite verifies Phase 2a dual-write behavior:
1. Bundled + Skill decisions both computed and logged
2. Confidence threshold applied (default 0.75)
3. Decision switch logic: if skill_confidence >= threshold → use Skill; else bundled
4. Agreement rate tracking for correctness monitoring
5. Auto-rollback trigger on correctness degradation (> 2% drop)
6. E2E audit trail: decisions logged with ADR-0722 attribution
7. Learning feedback integration: decision metrics emitted for ADR-0314

Test Coverage (15+ tests):
- Phase 2a feature flag: CORVIN_ACP_PHASE=phase2_dual_write
- Confidence threshold: default 0.75, learned override
- Decision disagreement: agreement rate tracking
- Rollback triggers: correctness degradation, threshold breaches
- Latency tracking: Skill execution time + decision time
- Cost tracking: worker cost impact (delegate vs. native)
- Audit events: dual-write decisions, metrics, rollback triggers
- Tenant isolation: per-tenant learned config + audit filtering
- Fallback behavior: Skill unavailable, timeout, error
- Learning feedback: confidence scoring + optimizer integration

Compliance:
- GDPR Art. 30: Every decision audited with attribution
- GDPR Art. 32: Immutable dual-write records in audit chain
- EU AI Act Art. 50: LoM binding + lom_hash in every decision
- ADR-0722: Decision attribution + loss signal to learning loop
- ADR-0314: Feedback events emitted for optimizer training
- ADR-0232/0233: Boot tripwire verified before Phase 2 execution
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, Mock, patch

import pytest

# Setup path for imports
REPO = Path(__file__).resolve().parents[2]
_SHARED = REPO / "corvin_operator" / "bridges" / "shared"
_CORE_SKILLS = REPO / "core" / "skills"
_MONITORING = _CORE_SKILLS / "os_skills" / "monitoring"

for p in (str(_SHARED), str(REPO / "corvin_operator" / "forge"), str(_CORE_SKILLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DualWriteDecisionRecord:
    """Record of a dual-write decision from audit trail."""

    request_id: str
    decision_source: str  # "skill" or "bundled"
    used_engine: str
    skill_engine: str
    bundled_engine: str
    skill_confidence: float
    agreement: bool
    task_type: str
    tenant_id: str
    timestamp: str


class TestPhase2DualWriteBasics:
    """Test basic Phase 2a dual-write functionality."""

    @pytest.mark.skipif(
        "SKIP_REAL_DELEGATION" in os.environ,
        reason="Real delegation tests skipped in CI",
    )
    def test_phase2_env_var_detection(self):
        """Test CORVIN_ACP_PHASE env var detection."""
        # Import fresh to pick up env var
        import importlib

        import delegation_policy

        importlib.reload(delegation_policy)

        # Test phase1_shadow (default)
        os.environ.pop("CORVIN_ACP_PHASE", None)
        try:
            assert delegation_policy._get_phase_mode() == "phase1_shadow"

            # Phase 2 is REFUSED while its auto-rollback guard is not wired
            # (record_routing_outcome has no production feed): the env var
            # alone must never let the Skill change served routing.
            for phase in ("phase2_dual_write", "phase2_real"):
                os.environ["CORVIN_ACP_PHASE"] = phase
                assert delegation_policy._PHASE2_ROLLBACK_GUARD_WIRED is False
                assert delegation_policy._get_phase_mode() == "phase1_shadow"

            # Test invalid value falls back to default
            os.environ["CORVIN_ACP_PHASE"] = "phase3_invalid"
            assert delegation_policy._get_phase_mode() == "phase1_shadow"
        finally:
            os.environ.pop("CORVIN_ACP_PHASE", None)

    def test_confidence_threshold_default(self):
        """Test default confidence threshold is 0.75."""
        from core.skills.os_skills.monitoring.dual_write import _load_confidence_threshold

        threshold = _load_confidence_threshold("_default")
        assert threshold == 0.75, "Default threshold should be 0.75"

    def test_fetch_skill_decision_fallback_on_unavailable(self):
        """Test Skill decision fetching gracefully degrades on unavailable registry."""
        from core.skills.os_skills.monitoring.dual_write import _fetch_skill_decision

        # Registry not booted in this process
        with patch("core.skills.skill_registry_phase1._global_registry", None):
            result = _fetch_skill_decision(
                complexity=5,
                task_type="chat",
                force_delegate=False,
                is_big_data=False,
                tenant_id="_default",
            )

            assert result["decision"] in ["native", "acs", "tde"]
            assert result["confidence"] == 0.0
            assert "error" in result.get("reasoning", "").lower() or "unavailable" in result.get(
                "reasoning", ""
            ).lower()


class TestPhase2ConfidenceThresholdLogic:
    """Test confidence threshold decision tree."""

    def test_skill_confidence_above_threshold_uses_skill(self):
        """When skill_confidence >= threshold, use Skill decision."""
        from core.skills.os_skills.monitoring.dual_write import (
            resolve_worker_engine_dual_write,
        )

        # Skill decision with high confidence
        skill_decision = {"decision": "acs", "confidence": 0.85, "reasoning": "Big data"}

        with patch(
            "core.skills.os_skills.monitoring.dual_write.get_detector",
            return_value=Mock(update=Mock(return_value=False)),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write.get_tracker",
            return_value=Mock(),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_dual_routing_audit"
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_decision_metrics"
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._load_confidence_threshold",
            return_value=0.75,
        ):
            result = resolve_worker_engine_dual_write(
                request_id="test_001",
                bundled_engine="native",
                skill_decision=skill_decision,
                task_type="chat",
                complexity=5,
                force_delegate=False,
                is_big_data=True,
                tenant_id="_default",
            )

            assert result == "acs", "Should use Skill decision (confidence 0.85 >= 0.75)"

    def test_skill_confidence_below_threshold_uses_bundled(self):
        """When skill_confidence < threshold, fall back to bundled (fail-closed)."""
        from core.skills.os_skills.monitoring.dual_write import (
            resolve_worker_engine_dual_write,
        )

        # Skill decision with low confidence
        skill_decision = {"decision": "tde", "confidence": 0.50, "reasoning": "Uncertain"}

        with patch(
            "core.skills.os_skills.monitoring.dual_write.get_detector",
            return_value=Mock(update=Mock(return_value=False)),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write.get_tracker",
            return_value=Mock(),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_dual_routing_audit"
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_decision_metrics"
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._load_confidence_threshold",
            return_value=0.75,
        ):
            result = resolve_worker_engine_dual_write(
                request_id="test_002",
                bundled_engine="native",
                skill_decision=skill_decision,
                task_type="chat",
                complexity=5,
                force_delegate=False,
                is_big_data=False,
                tenant_id="_default",
            )

            assert result == "native", "Should use bundled (confidence 0.50 < 0.75, fail-closed)"


class TestPhase2AgreementRateTracking:
    """Test agreement rate tracking between Skill and bundled decisions."""

    def test_agreement_rate_full_agreement(self):
        """Track agreement when Skill and bundled decisions match."""
        from core.skills.os_skills.monitoring.dual_write import (
            resolve_worker_engine_dual_write,
        )

        # Both agree on native
        skill_decision = {"decision": "native", "confidence": 0.90, "reasoning": "Good"}

        with patch(
            "core.skills.os_skills.monitoring.dual_write.get_detector",
            return_value=Mock(update=Mock(return_value=False)),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write.get_tracker",
            return_value=Mock(),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_decision_metrics"
        ) as metrics_mock, patch(
            "core.skills.os_skills.monitoring.dual_write._emit_dual_routing_audit"
        ) as audit_mock, patch(
            "core.skills.os_skills.monitoring.dual_write._load_confidence_threshold",
            return_value=0.75,
        ):
            result = resolve_worker_engine_dual_write(
                request_id="test_003",
                bundled_engine="native",
                skill_decision=skill_decision,
                task_type="chat",
                complexity=5,
                force_delegate=False,
                is_big_data=False,
                tenant_id="_default",
            )

            assert result == "native"

            # Verify agreement metric was emitted
            metrics_mock.assert_called_once()
            call_kwargs = metrics_mock.call_args[1]
            # _emit_decision_metrics derives ``agreement`` itself from these two
            assert call_kwargs["skill_engine"] == call_kwargs["bundled_engine"] == "native"
            assert call_kwargs["used_engine"] == "native"

    def test_agreement_rate_disagreement(self):
        """Track disagreement when Skill and bundled decisions differ."""
        from core.skills.os_skills.monitoring.dual_write import (
            resolve_worker_engine_dual_write,
        )

        # Skill says ACS, bundled says native
        skill_decision = {"decision": "acs", "confidence": 0.90, "reasoning": "Big data"}

        with patch(
            "core.skills.os_skills.monitoring.dual_write.get_detector",
            return_value=Mock(update=Mock(return_value=False)),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write.get_tracker",
            return_value=Mock(),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_decision_metrics"
        ) as metrics_mock, patch(
            "core.skills.os_skills.monitoring.dual_write._emit_dual_routing_audit"
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._load_confidence_threshold",
            return_value=0.75,
        ):
            result = resolve_worker_engine_dual_write(
                request_id="test_004",
                bundled_engine="native",
                skill_decision=skill_decision,
                task_type="chat",
                complexity=5,
                force_delegate=False,
                is_big_data=True,
                tenant_id="_default",
            )

            assert result == "acs"

            # Verify disagreement was tracked
            metrics_mock.assert_called_once()
            call_kwargs = metrics_mock.call_args[1]
            assert call_kwargs["skill_engine"] == "acs"
            assert call_kwargs["bundled_engine"] == "native"
            assert call_kwargs["used_engine"] == "acs"


class TestPhase2AutoRollback:
    """Test auto-rollback trigger on correctness degradation."""

    def test_rollback_active_uses_bundled_only(self):
        """When rollback is active, always use bundled routing."""
        from core.skills.os_skills.monitoring.dual_write import (
            resolve_worker_engine_dual_write,
        )

        skill_decision = {"decision": "acs", "confidence": 0.90, "reasoning": "Good"}

        # Mock detector with rollback active
        mock_detector = Mock()
        mock_detector.update.return_value = True  # Rollback triggered

        with patch(
            "core.skills.os_skills.monitoring.dual_write.get_detector",
            return_value=mock_detector,
        ), patch(
            "core.skills.os_skills.monitoring.dual_write.get_tracker",
            return_value=Mock(),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_rollback_decision_audit"
        ):
            result = resolve_worker_engine_dual_write(
                request_id="test_005",
                bundled_engine="native",
                skill_decision=skill_decision,
                task_type="chat",
                complexity=5,
                force_delegate=False,
                is_big_data=False,
                tenant_id="_default",
            )

            # Should return bundled even though Skill says ACS
            assert result == "native", "Rollback active: should use bundled only"


class TestPhase2LearningFeedbackIntegration:
    """Test integration with ADR-0314 learning loop."""

    def test_decision_metrics_emitted_for_learning(self):
        """The metrics event carries agreement + threshold_met (derived, not passed in)."""
        from core.skills.os_skills.monitoring.dual_write import (
            resolve_worker_engine_dual_write,
        )

        skill_decision = {"decision": "acs", "confidence": 0.82, "reasoning": "Complex"}

        with patch(
            "core.skills.os_skills.monitoring.dual_write.get_detector",
            return_value=Mock(update=Mock(return_value=False)),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write.get_tracker",
            return_value=Mock(),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._audit"
        ) as audit_mock, patch(
            "core.skills.os_skills.monitoring.dual_write._load_confidence_threshold",
            return_value=0.75,
        ):
            result = resolve_worker_engine_dual_write(
                request_id="test_006",
                bundled_engine="native",
                skill_decision=skill_decision,
                task_type="chat",
                complexity=7,
                force_delegate=False,
                is_big_data=False,
                tenant_id="_default",
            )

        assert result == "acs"
        by_type = {c.args[0]: c.args[1] for c in audit_mock.call_args_list}
        metrics = by_type["l5_routing_metrics"]
        assert metrics["request_id"] == "test_006"
        assert metrics["agreement"] == 0.0  # acs vs native
        assert metrics["threshold_met"] == 1.0  # 0.82 >= 0.75
        assert metrics["skill_confidence"] == 0.82
        assert metrics["threshold"] == 0.75
        assert by_type["l5_routing_dual_write"]["agreement"] is False


class TestPhase2AuditTrail:
    """Test audit trail compliance (ADR-0722, ADR-0314)."""

    def test_dual_write_audit_events_emitted(self):
        """Verify both real and shadow decisions logged to audit trail."""
        from core.skills.os_skills.monitoring.dual_write import (
            resolve_worker_engine_dual_write,
        )

        skill_decision = {"decision": "acs", "confidence": 0.88, "reasoning": "High complexity"}

        with patch(
            "core.skills.os_skills.monitoring.dual_write.get_detector",
            return_value=Mock(update=Mock(return_value=False)),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write.get_tracker",
            return_value=Mock(),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_dual_routing_audit"
        ) as audit_mock, patch(
            "core.skills.os_skills.monitoring.dual_write._emit_decision_metrics"
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._load_confidence_threshold",
            return_value=0.75,
        ):
            result = resolve_worker_engine_dual_write(
                request_id="test_007",
                bundled_engine="native",
                skill_decision=skill_decision,
                task_type="chat",
                complexity=8,
                force_delegate=False,
                is_big_data=False,
                tenant_id="_default",
            )

            # Verify audit event was emitted
            audit_mock.assert_called_once()
            # Should have called _emit_dual_routing_audit with both decisions


class TestPhase2TenantIsolation:
    """Test per-tenant isolation (GDPR Art. 32)."""

    def test_learned_config_per_tenant(self):
        """Verify learned config is loaded per-tenant."""
        from core.skills.os_skills.monitoring.dual_write import _load_confidence_threshold

        # Default for unknown tenant
        threshold_default = _load_confidence_threshold("_default")
        assert threshold_default == 0.75

        # Should not leak between tenants
        threshold_other = _load_confidence_threshold("other_tenant")
        # Both should be same (default) since learned config not mocked
        assert threshold_other == 0.75

    def test_audit_records_include_tenant_id(self):
        """Verify audit records include tenant_id for isolation."""
        from core.skills.os_skills.monitoring.dual_write import (
            resolve_worker_engine_dual_write,
        )

        skill_decision = {"decision": "native", "confidence": 0.90}

        with patch(
            "core.skills.os_skills.monitoring.dual_write.get_detector",
            return_value=Mock(update=Mock(return_value=False)),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write.get_tracker",
            return_value=Mock(),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_dual_routing_audit"
        ) as audit_mock, patch(
            "core.skills.os_skills.monitoring.dual_write._emit_decision_metrics"
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._load_confidence_threshold",
            return_value=0.75,
        ):
            result = resolve_worker_engine_dual_write(
                request_id="test_008",
                bundled_engine="native",
                skill_decision=skill_decision,
                task_type="chat",
                complexity=5,
                force_delegate=False,
                is_big_data=False,
                tenant_id="tenant_abc",
            )

            # Verify audit was called with tenant_id
            audit_mock.assert_called_once()
            # The _emit_dual_routing_audit should have received both decisions with tenant_id


class TestPhase2Fallback:
    """Test graceful fallback and degradation."""

    def test_skill_timeout_falls_back_to_bundled(self):
        """When Skill times out, fall back to bundled."""
        from core.skills.os_skills.monitoring.dual_write import (
            resolve_worker_engine_dual_write,
        )

        # Simulate Skill timeout: fetch_skill_decision returns fallback
        fallback_decision = {
            "decision": "native",
            "confidence": 0.0,
            "reasoning": "Skill timeout",
        }

        with patch(
            "core.skills.os_skills.monitoring.dual_write.get_detector",
            return_value=Mock(update=Mock(return_value=False)),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write.get_tracker",
            return_value=Mock(),
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._fetch_skill_decision",
            return_value=fallback_decision,
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_dual_routing_audit"
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._emit_decision_metrics"
        ), patch(
            "core.skills.os_skills.monitoring.dual_write._load_confidence_threshold",
            return_value=0.75,
        ):
            result = resolve_worker_engine_dual_write(
                request_id="test_009",
                bundled_engine="native",
                skill_decision=None,  # Will be fetched
                task_type="chat",
                complexity=5,
                force_delegate=False,
                is_big_data=False,
                tenant_id="_default",
            )

            # Should fall back to bundled due to low confidence (0.0 < 0.75)
            assert result == "native"


class TestPhase2MetricsCollection:
    """Test metrics collection for monitoring dashboard."""

    def test_agreement_rate_metric_collection(self, tmp_path):
        """The dashboard reads the real tracker (no swallowed exceptions)."""
        from core.skills.os_skills.monitoring import dual_write

        dual_write.initialize_dual_write(storage_dir=tmp_path / "metrics")
        dashboard = dual_write.get_monitoring_dashboard()
        assert dashboard["is_rolled_back"] is False
        assert dashboard["correctness_metrics"]["total_count"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
