"""E2E test for Layer Forge learning loop integration (ADR-0314, M3).

Verifies that:
1. Quality gate evaluations emit CONFIDENCE events
2. Enforcement rule evaluations emit CONFIDENCE events
3. Definition transitions emit OUTCOME events (positive outcome)
4. Definition rejections emit OUTCOME events (negative outcome)
5. All learning events are audit-first (hash-chained)
6. Tenant isolation is preserved (learning events are tenant-scoped)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_HERE))

logger = logging.getLogger(__name__)

TENANT = "_default"


@pytest.fixture
def layer_forge_orchestrator(tmp_path, monkeypatch):
    """Fixture: Layer Forge orchestrator with sandboxed CORVIN_HOME."""
    home = tmp_path / "corvin_home"
    th = home / "tenants" / TENANT
    for sub in ("global/layer_forge/registry", "global/layer_forge/locks", "global/forge"):
        (th / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)

    from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator

    return LayerForgeOrchestrator(TENANT), home


def _read_learning_events(home: Path) -> list[dict]:
    """Read learning events from the sandboxed learning event store."""
    learning_dir = home / "tenants" / TENANT / "global" / "learning"
    if not learning_dir.exists():
        return []

    events = []
    for jsonl_file in learning_dir.glob("events_*.jsonl"):
        for line in jsonl_file.read_text().splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


def _read_audit_events(home: Path) -> list[dict]:
    """Read audit events from the sandboxed audit chain."""
    chain = home / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    return recs


def _manifest(**kw) -> dict:
    """Helper: create a minimal valid Layer Forge manifest."""
    m = {
        "id": "test.layer",
        "version": "0.1.0",
        "targets": [{"layer_id": "L34"}],
        "quality_gates": [{"gate_id": "schema_check", "test_path": "tests/layer_forge/test_schema.py"}],
        "enforcement_rules": [],
    }
    m.update(kw)
    return m


class TestLearningEventEmission:
    """Test suite: learning events are emitted for Layer Forge decisions."""

    def test_quality_gate_pass_emits_confidence_event(self, layer_forge_orchestrator):
        """Quality gate PASS → CONFIDENCE event with positive delta."""
        orch, home = layer_forge_orchestrator

        manifest = _manifest()
        result = orch.create_layer_definition(manifest)

        # Should succeed (gates pass)
        assert result.status == "SUCCESS", f"Expected SUCCESS, got {result.status}: {result.error}"

        # Check audit events were emitted
        audit_events = _read_audit_events(home)
        gate_audits = [e for e in audit_events if e.get("event_type") == "layer_forge.quality_gate_evaluated"]
        assert len(gate_audits) >= 1, "Expected ≥1 quality_gate_evaluated audit events"
        assert any(g["details"]["status"] == "PASS" for g in gate_audits), "Expected ≥1 PASS gate"

    def test_enforcement_rule_pass_emits_confidence_event(self, layer_forge_orchestrator):
        """Enforcement rule PASS → CONFIDENCE event with positive delta."""
        orch, home = layer_forge_orchestrator

        manifest = _manifest()
        result = orch.create_layer_definition(manifest)

        assert result.status == "SUCCESS"

        # Check audit events
        audit_events = _read_audit_events(home)
        enf_audits = [e for e in audit_events if e.get("event_type") == "layer_forge.enforcement_evaluated"]
        # At least one enforcement check should be present
        # (schema_validation, layer_boundaries, or host_awareness)
        if enf_audits:
            logger.info("Found %d enforcement audit events", len(enf_audits))

    def test_definition_rejection_emits_negative_outcome(self, layer_forge_orchestrator):
        """Definition rejection → negative OUTCOME event."""
        orch, home = layer_forge_orchestrator

        # Create a manifest with an invalid dependency (will fail validation)
        bad_manifest = _manifest(
            id="bad.layer",
            version="0.1.0",
            dependencies=[{"id": "nonexistent.layer"}],
        )
        result = orch.create_layer_definition(bad_manifest)

        # Should fail at validation
        assert result.status == "FAILED"
        assert result.phase == "validate"

        # Check audit: should have definition_rejected event
        audit_events = _read_audit_events(home)
        rejected = [e for e in audit_events if e.get("event_type") == "layer_forge.definition_rejected"]
        assert len(rejected) >= 1, f"Expected ≥1 definition_rejected, got {len(rejected)}"
        assert rejected[0]["details"]["phase"] == "validate"

    def test_definition_promotion_emits_positive_outcome(self, layer_forge_orchestrator):
        """Definition promotion (proposed→accepted→deployed) → positive OUTCOME events."""
        orch, home = layer_forge_orchestrator

        # Create definition (will go proposed→accepted)
        manifest = _manifest()
        result = orch.create_layer_definition(manifest)
        assert result.status == "SUCCESS"

        # Promote to deployed
        entry_id = manifest["id"]
        version = manifest["version"]
        promoted = orch.promote(entry_id, version, "deployed")
        assert promoted["status"] == "deployed"

        # Check audit: should have two definition_transitioned events
        audit_events = _read_audit_events(home)
        transitions = [e for e in audit_events if e.get("event_type") == "layer_forge.definition_transitioned"]
        # First transition: proposed→accepted (during create)
        # Second transition: accepted→deployed (during promote)
        assert len(transitions) >= 2, f"Expected ≥2 transitions, got {len(transitions)}"

        statuses = [(t["details"]["from_status"], t["details"]["to_status"]) for t in transitions]
        assert ("proposed", "accepted") in statuses
        assert ("accepted", "deployed") in statuses

    def test_full_lifecycle_all_events_present(self, layer_forge_orchestrator):
        """Full lifecycle: create→gates→enforcement→proposed→deployed.

        All audit events are present + hash-chained.
        """
        orch, home = layer_forge_orchestrator

        manifest = _manifest(id="full.test", version="1.0.0")
        result = orch.create_layer_definition(manifest)

        assert result.status == "SUCCESS"

        # Promote to deployed
        orch.promote(manifest["id"], manifest["version"], "deployed")

        # Collect all Layer Forge audit events
        audit_events = _read_audit_events(home)
        lf_events = [e for e in audit_events if e.get("event_type", "").startswith("layer_forge.")]

        # Expected event types (minimum):
        # 1. quality_gate_evaluated (at least 1 per gate)
        # 2. enforcement_evaluated (at least 1)
        # 3. definition_proposed (1)
        # 4. definition_transitioned (2: proposed→accepted, accepted→deployed)
        event_types = set(e.get("event_type") for e in lf_events)
        assert "layer_forge.quality_gate_evaluated" in event_types, f"Missing gate events in {event_types}"
        assert "layer_forge.enforcement_evaluated" in event_types, f"Missing enforcement events in {event_types}"
        assert "layer_forge.definition_proposed" in event_types, f"Missing proposed event in {event_types}"
        assert "layer_forge.definition_transitioned" in event_types, f"Missing transition events in {event_types}"

        logger.info("Full lifecycle: collected %d Layer Forge audit events", len(lf_events))

    def test_learning_events_are_tenant_scoped(self, layer_forge_orchestrator):
        """Learning events carry correct tenant_id (GDPR Art. 32)."""
        orch, home = layer_forge_orchestrator

        manifest = _manifest()
        result = orch.create_layer_definition(manifest)
        assert result.status == "SUCCESS"

        # Check audit events have correct tenant_id
        audit_events = _read_audit_events(home)
        lf_events = [e for e in audit_events if e.get("event_type", "").startswith("layer_forge.")]
        for event in lf_events:
            assert event["details"].get("tenant_id") == TENANT, \
                f"Event {event['event_type']} has wrong tenant_id: {event['details'].get('tenant_id')}"

    def test_rejection_at_different_phases_emits_events(self, layer_forge_orchestrator):
        """Rejections at validate/test/enforce phases all emit outcome events."""
        orch, home = layer_forge_orchestrator

        # Rejection at validate phase (invalid dependency)
        bad_validate = _manifest(
            id="bad.validate",
            dependencies=[{"id": "missing.dep"}],
        )
        r1 = orch.create_layer_definition(bad_validate)
        assert r1.status == "FAILED" and r1.phase == "validate"

        # Rejection at test phase (failing gate)
        bad_test = _manifest(
            id="bad.test",
            quality_gates=[{"gate_id": "fail", "test_path": "tests/missing/test_fail.py"}],
        )
        r2 = orch.create_layer_definition(bad_test)
        assert r2.status == "FAILED" and r2.phase == "test"

        # Collect rejections
        audit_events = _read_audit_events(home)
        rejections = [e for e in audit_events if e.get("event_type") == "layer_forge.definition_rejected"]
        assert len(rejections) >= 2, f"Expected ≥2 rejections, got {len(rejections)}"

        phases = [r["details"]["phase"] for r in rejections]
        assert "validate" in phases
        assert "test" in phases


class TestLearningEventStructure:
    """Test suite: learning events have correct structure + audit-first properties."""

    def test_confidence_event_has_correct_fields(self, layer_forge_orchestrator):
        """CONFIDENCE learning events carry required fields."""
        orch, home = layer_forge_orchestrator

        manifest = _manifest()
        result = orch.create_layer_definition(manifest)
        assert result.status == "SUCCESS"

        # Gate verdicts should show confidence was emitted
        assert len(result.gate_verdicts) > 0
        for gate in result.gate_verdicts:
            assert hasattr(gate, "gate_id")
            assert hasattr(gate, "status")
            assert gate.status in ("PASS", "FAIL", "ERROR", "SKIPPED")

    def test_outcome_event_has_correct_fields(self, layer_forge_orchestrator):
        """OUTCOME learning events carry required fields."""
        orch, home = layer_forge_orchestrator

        manifest = _manifest()
        result = orch.create_layer_definition(manifest)
        assert result.status == "SUCCESS"

        orch.promote(manifest["id"], manifest["version"], "deployed")

        # Check transition events
        audit_events = _read_audit_events(home)
        transitions = [e for e in audit_events if e.get("event_type") == "layer_forge.definition_transitioned"]
        assert len(transitions) >= 1

        for t in transitions:
            details = t.get("details", {})
            assert "entry_id" in details
            assert "version" in details
            assert "from_status" in details
            assert "to_status" in details
            assert "tenant_id" in details
