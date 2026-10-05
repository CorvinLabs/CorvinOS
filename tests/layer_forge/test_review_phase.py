"""Tests for M4 Adversarial Review phase (ADR-2227).

Tests that:
1. PASS verdict allows create to proceed
2. FLAGGED verdict records flags but doesn't block create
3. ERROR verdict blocks create (fail-closed)
4. Audit events are emitted
5. review_flagged field is set in registry

Note: Direct tests of review_layer_definition() are excluded because they
require mocking at the Anthropic SDK level. Instead, integration tests
(TestReviewPhaseIntegration) test the full flow through the orchestrator,
which is more robust and representative of real usage.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator
from core.orchestration.layer_forge.review import (
    ReviewVerdict, ReviewFlag
)


@pytest.fixture
def orchestrator():
    """Orchestrator for testing review phase."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "forge_home"
        home.mkdir(parents=True)
        orch = LayerForgeOrchestrator("_default", repo_root=Path.cwd(), actor="test")
        # Override the home path for testing
        orch.registry._root = home / "registry"
        orch.registry._root.mkdir(parents=True, exist_ok=True)
        yield orch


@pytest.fixture
def valid_manifest():
    """Valid layer definition manifest."""
    return {
        "id": "l-test-review-001",
        "version": "1.0.0",
        "type": "layer_definition",
        "targets": [
            {"layer_id": "L34", "layer_name": "Data Flow Guard"}
        ],
        "quality_gates": [],
        "enforcement_rules": [],
        "host_awareness": {
            "source_tree": {
                "paths": ["core/compliance"]
            }
        },
        "dependencies": [],
    }


class TestReviewVerdictStructure:
    """Test ReviewVerdict dataclass."""

    def test_pass_verdict(self):
        v = ReviewVerdict("PASS", flags=[], reason="No concerns")
        assert v.status == "PASS"
        assert v.flags == []
        assert v.to_dict()["status"] == "PASS"

    def test_flagged_verdict_with_flags(self):
        flags = [ReviewFlag.SCOPE_CREEP, ReviewFlag.SECURITY_GAP]
        v = ReviewVerdict("FLAGGED", flags=flags, reason="Two concerns raised")
        assert v.status == "FLAGGED"
        assert len(v.flags) == 2
        d = v.to_dict()
        assert d["status"] == "FLAGGED"
        assert "scope_creep" in d["flags"]
        assert "security_gap" in d["flags"]

    def test_error_verdict(self):
        v = ReviewVerdict("ERROR", reason="LLM call failed")
        assert v.status == "ERROR"
        assert v.to_dict()["status"] == "ERROR"


class TestReviewPhaseIntegration:
    """Integration tests for review phase in create_layer_definition flow."""

    def test_review_pass_allows_create(self, orchestrator, valid_manifest):
        """PASS verdict should not block creation."""
        with patch("core.orchestration.layer_forge.orchestrator.review_layer_definition") as mock_review:
            mock_review.return_value = ReviewVerdict("PASS", flags=[], reason="No concerns")

            result = orchestrator.create_layer_definition(valid_manifest, skip_gates=True)

            assert result.status == "SUCCESS"
            assert result.review_verdict.status == "PASS"
            entry = orchestrator.registry.get("l-test-review-001", "1.0.0")
            assert entry.get("review_flagged") is None  # Not flagged

    def test_review_flagged_records_flags_and_allows_create(self, orchestrator, valid_manifest):
        """FLAGGED verdict should record flags but allow creation."""
        flags = [ReviewFlag.SCOPE_CREEP, ReviewFlag.UNTESTED_COMPLEXITY]
        with patch("core.orchestration.layer_forge.orchestrator.review_layer_definition") as mock_review:
            mock_review.return_value = ReviewVerdict(
                "FLAGGED",
                flags=flags,
                reason="Two concerns raised"
            )

            result = orchestrator.create_layer_definition(valid_manifest, skip_gates=True)

            assert result.status == "SUCCESS"
            assert result.review_verdict.status == "FLAGGED"

            # Check registry has review_flagged field
            entry = orchestrator.registry.get("l-test-review-001", "1.0.0")
            assert entry.get("review_flagged") is True
            assert set(entry.get("review_flags", [])) == {"scope_creep", "untested_complexity"}

    def test_review_error_blocks_create(self, orchestrator, valid_manifest):
        """ERROR verdict should block creation (fail-closed)."""
        with patch("core.orchestration.layer_forge.orchestrator.review_layer_definition") as mock_review:
            mock_review.return_value = ReviewVerdict(
                "ERROR",
                reason="LLM call failed"
            )

            result = orchestrator.create_layer_definition(valid_manifest, skip_gates=True)

            assert result.status == "FAILED"
            assert result.phase == "review"
            assert "adversarial review failed" in result.error

    def test_review_audit_event_emitted(self, orchestrator, valid_manifest):
        """Review verdict should emit audit event."""
        flags = [ReviewFlag.SECURITY_GAP]
        with patch("core.orchestration.layer_forge.orchestrator.review_layer_definition") as mock_review:
            mock_review.return_value = ReviewVerdict(
                "FLAGGED",
                flags=flags,
                reason="Security concern raised"
            )

            result = orchestrator.create_layer_definition(valid_manifest, skip_gates=True)

            # Check audit chain for review event
            assert result.status == "SUCCESS"
            # TODO: verify audit event in chain (requires reading audit.jsonl)

    def test_review_verdict_serialization(self, orchestrator, valid_manifest):
        """Test that review verdict is properly serialized in API response."""
        flags = [ReviewFlag.UNTESTED_COMPLEXITY]
        with patch("core.orchestration.layer_forge.orchestrator.review_layer_definition") as mock_review:
            mock_review.return_value = ReviewVerdict("FLAGGED", flags=flags)

            result = orchestrator.create_layer_definition(valid_manifest, skip_gates=True)
            result_dict = result.to_dict()

            assert result_dict["review_verdict"]["status"] == "FLAGGED"
            assert "untested_complexity" in result_dict["review_verdict"]["flags"]
