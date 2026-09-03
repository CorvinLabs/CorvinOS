"""Tests for Phase 2: Feature Flags Audit Integration (skill.executed events).

Every feature-flag operation emits a hash-chained, metadata-only event into
the TENANT CORE AUDIT CHAIN — ``<CORVIN_HOME>/tenants/<tid>/global/forge/
audit.jsonl`` — through ``core.skills.skill_audit`` (the one skill audit
sink). The previous writer targeted a hard-coded ``~/.corvin/audit.jsonl``
in a record format the chain verifier does not read (adversarial review
D-07b), so these tests verify the chain with the REAL verifier.
"""

import json
from pathlib import Path

import pytest

from core.skills.feature_flags_skill import FeatureFlagsSkill


def _chain(home: Path, tenant: str) -> Path:
    return home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"


def _events(home: Path, tenant: str) -> list[dict]:
    p = _chain(home, tenant)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


class TestAuditIntegration:
    """Test skill.executed event emission (Phase 2)."""

    @pytest.fixture
    def skill(self):
        return FeatureFlagsSkill()

    @pytest.fixture
    def home(self, tmp_path, monkeypatch):
        """Sandboxed CORVIN_HOME — events never touch the live chain."""
        home = tmp_path / "corvin_home"
        monkeypatch.setenv("CORVIN_HOME", str(home))
        return home

    def test_is_enabled_emits_audit_event(self, skill, home):
        result = skill.execute({
            "operation": "is_enabled",
            "flag_id": "vibe_engineering",
            "tenant_id": "_default",
        })
        assert result["success"]

        events = _events(home, "_default")
        assert events, "No audit events found in the tenant core chain"
        last = events[-1]
        assert last["event_type"] == "skill.executed"
        assert last["tool"] == "os.feature_flags_system"
        assert last["details"]["skill_id"] == "os.feature_flags_system"
        assert last["details"]["operation"] == "is_enabled"
        assert last["details"]["flag_id"] == "vibe_engineering"
        assert last["details"]["tenant_id"] == "_default"
        assert "hash" in last  # Hash-chained

    def test_set_enabled_emits_audit_event(self, skill, home):
        result = skill.execute({
            "operation": "set_enabled",
            "flag_id": "vibe_engineering",   # a REGISTERED flag — unknown ids are refused
            "enabled": True,
            "tenant_id": "_default",
        })
        assert result["success"], result
        last = _events(home, "_default")[-1]
        assert last["details"]["operation"] == "set_enabled"
        assert last["details"]["flag_id"] == "vibe_engineering"
        assert last["details"]["enabled"] is True

    def test_audit_events_are_hash_chained(self, skill, home):
        skill.execute({"operation": "is_enabled", "flag_id": "flag1", "tenant_id": "_default"})
        skill.execute({"operation": "is_enabled", "flag_id": "flag2", "tenant_id": "_default"})

        events = _events(home, "_default")
        assert len(events) >= 2
        event1, event2 = events[-2], events[-1]
        assert event2.get("prev_hash") == event1.get("hash"), "Hash chain broken"
        assert event2.get("hash")

        import corvin_core._bootstrap  # noqa: F401 — forge on sys.path
        from forge.security_events import verify_chain  # type: ignore[import-not-found]
        ok, problems = verify_chain(_chain(home, "_default"))
        assert ok, problems

    def test_audit_events_are_tenant_scoped(self, skill, home):
        skill.execute({"operation": "is_enabled", "flag_id": "flag", "tenant_id": "tenant_test"})
        events = _events(home, "tenant_test")
        assert events and events[-1]["details"]["tenant_id"] == "tenant_test"
        assert not _chain(home, "_default").exists(), "no cross-tenant write"

    def test_audit_events_contain_no_pii(self, skill, home):
        skill.execute({
            "operation": "set_enabled",
            "flag_id": "vibe_engineering",
            "enabled": True,
            "tenant_id": "_default",
        })
        event_str = json.dumps(_events(home, "_default")[-1])
        assert "@" not in event_str
        assert "password" not in event_str.lower()
        assert "token" not in event_str.lower()
        # metadata only — no free-form input/output blobs
        details = _events(home, "_default")[-1]["details"]
        assert "input" not in details and "output" not in details

    def test_exception_path_emits_audit_event(self, skill, home):
        result = skill.execute({
            "operation": "is_enabled",
            "flag_id": None,  # Invalid: flag_id is required
            "tenant_id": "_default",
        })
        assert not result["success"]
        assert result["error"]
        events = _events(home, "_default")
        assert events, "Audit event must be recorded even on error"
        assert "latency_ms" in events[-1]["details"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
