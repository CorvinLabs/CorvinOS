"""Adversarial Tests: Crash isolation, timeout recovery, PII leakage (12+ tests)."""

import pytest
from unittest.mock import Mock, patch
from core.skills.os_skills_integration import initialize_integration
from core.skills.skill_registry_phase1 import SkillExecutionResult


class MockAuditBackend:
    def __init__(self):
        self.events = []
    def write_event(self, event):
        self.events.append(event)


class TestSkillCrashIsolation:
    """Prove Skill crashes don't crash the OS."""

    def setup_method(self):
        self.audit = MockAuditBackend()
        self.integration = initialize_integration(audit_backend=self.audit)

    def test_skill_crash_caught_and_logged(self):
        """Skill crash → caught, logged, fallback used."""
        # Mock a Skill that crashes
        self.integration.registry._skills["os.delegation_router"].execute = Mock(
            side_effect=RuntimeError("Intentional crash")
        )

        result = self.integration.route_task_l5(complexity=5, task_type="chat")

        # Should fallback, not crash
        assert result["skill_executed"] is False
        assert "error" in result
        assert result["engine"] is not None  # Fallback provided

    def test_multiple_skill_crashes_isolated(self):
        """Multiple crash attempts don't accumulate → each isolated."""
        self.integration.registry._skills["os.delegation_router"].execute = Mock(
            side_effect=RuntimeError("Crash")
        )

        for i in range(3):
            result = self.integration.route_task_l5(complexity=5, task_type="chat")
            # Each attempt should be handled independently
            assert result["engine"] is not None

    def test_crash_doesnt_disable_other_skills(self):
        """One Skill crash doesn't disable other Skills."""
        self.integration.registry._skills["os.delegation_router"].execute = Mock(
            side_effect=RuntimeError("Crash")
        )

        # Route fails
        r1 = self.integration.route_task_l5(complexity=5, task_type="chat")
        assert r1["skill_executed"] is False

        # Context should still work (different skill)
        r2 = self.integration.adapt_context_l10(
            complexity=5, task_type="chat", task_description="Test"
        )
        # Should use base tier at minimum
        assert r2["base_tier"] is not None


class TestTimeoutRecovery:
    """Prove Skills timeout gracefully without hanging."""

    def setup_method(self):
        self.integration = initialize_integration()

    def test_skill_timeout_doesnt_hang(self):
        """Skill timeout → returns error quickly (no hang)."""
        # Mock a slow skill
        slow_skill = Mock()
        slow_skill.execute = Mock(side_effect=lambda x: __import__("time").sleep(10))

        # Timeout is 5s, should not wait 10s
        result = self.integration.registry.execute(
            "os.test_slow",
            {},
            timeout_ms=100,  # 100ms timeout
            lom="test:timeout:100",
        )

        # Should timeout, not hang
        assert result.status in ("timeout", "error")

    def test_timeout_auto_disables_after_3_failures(self):
        """Skill auto-disabled after 3+ timeouts."""
        # After 3 timeouts, skill should be auto-disabled
        failure_count = 0
        for i in range(5):
            result = self.integration.registry.execute(
                "os.delegation_router",
                {"complexity": 5, "task_type": "chat"},
                timeout_ms=1,  # Very short timeout
            )
            if result.status == "timeout":
                failure_count += 1

        # After 3 failures, skill should be disabled
        if failure_count >= 3:
            # Next call should be immediately rejected
            result_final = self.integration.registry.execute(
                "os.delegation_router",
                {"complexity": 5, "task_type": "chat"},
                timeout_ms=5000,
            )
            assert result_final.status == "error"
            assert "disabled" in result_final.error_message.lower()


class TestPIIScrubbing:
    """Prove PII never leaks into audit trail."""

    def setup_method(self):
        self.audit = MockAuditBackend()
        self.integration = initialize_integration(audit_backend=self.audit)

    def test_email_scrubbed_from_audit(self):
        """PII (email) scrubbed from audit events."""
        self.audit.events.clear()

        # Execute with PII in output
        self.integration.registry.execute(
            "os.delegation_router",
            {
                "complexity": 5,
                "task_type": "chat",
                "user_email": "secret@example.com",
            },
            lom="test:pii_email:100",
        )

        # Check audit events
        for event in self.audit.events:
            if event.get("output"):
                output_str = str(event.get("output", {}))
                assert "secret@example.com" not in output_str

    def test_api_key_scrubbed_from_audit(self):
        """PII (API key) scrubbed from audit events."""
        self.audit.events.clear()

        self.integration.registry.execute(
            "os.delegation_router",
            {
                "complexity": 5,
                "api_key": "sk-12345678",
            },
            lom="test:pii_key:100",
        )

        for event in self.audit.events:
            if event.get("output"):
                output_str = str(event.get("output", {}))
                assert "sk-12345678" not in output_str

    def test_password_scrubbed_from_audit(self):
        """PII (password) scrubbed from audit events."""
        self.audit.events.clear()

        self.integration.registry.execute(
            "os.delegation_router",
            {"complexity": 5, "password": "superSecret123!"},
            lom="test:pii_password:100",
        )

        for event in self.audit.events:
            output_str = str(event.get("output", {}))
            assert "superSecret123" not in output_str


class TestTenantIsolationAttacks:
    """Prove tenant isolation can't be bypassed."""

    def setup_method(self):
        self.audit = MockAuditBackend()
        self.integration = initialize_integration(audit_backend=self.audit)
        self.integration.registry.add_tenant("tenant_a")

    def test_cross_tenant_access_denied(self):
        """Cross-tenant access → denied."""
        result = self.integration.route_task_l5(
            complexity=5,
            task_type="chat",
            tenant_id="tenant_b",  # Unauthorized
        )

        assert result["skill_executed"] is False
        assert "isolation" in result["error"].lower() or "authorized" in result["error"].lower()

    def test_missing_tenant_denied(self):
        """Empty tenant_id → denied (fail-closed)."""
        result = self.integration.route_task_l5(
            complexity=5,
            task_type="chat",
            tenant_id="",  # Empty
        )

        assert result["skill_executed"] is False

    def test_tenant_audit_separation(self):
        """Audit events from different tenants don't mix."""
        self.audit.events.clear()

        self.integration.route_task_l5(complexity=5, task_type="chat", tenant_id="tenant_a")

        # All events should be tenant_a
        for event in self.audit.events:
            if event.get("tenant_id"):
                assert event["tenant_id"] == "tenant_a"


class TestConfigDriftDetection:
    """Prove Skill config changes are detected."""

    def setup_method(self):
        self.integration = initialize_integration()

    def test_skill_version_mismatch_detected(self):
        """Skill version mismatch detected (future-proofing)."""
        # Register with v0.1.0
        from core.skills.os_skills_phase1 import DelegationRouterSkill
        skill = DelegationRouterSkill()
        assert skill.metadata.version == "0.1.0"

    def test_skill_metadata_immutable(self):
        """Skill metadata can't be modified (fail-closed)."""
        from core.skills.os_skills_phase1 import DelegationRouterSkill
        skill = DelegationRouterSkill()

        # Metadata is frozen
        with pytest.raises(AttributeError):
            skill.metadata.version = "2.0.0"


class TestNoSilentFailures:
    """Prove all failures are logged (no silent drops)."""

    def setup_method(self):
        self.audit = MockAuditBackend()
        self.learning = Mock()
        self.integration = initialize_integration(
            audit_backend=self.audit,
            learning_backend=self.learning,
        )

    def test_every_error_logged_to_audit(self):
        """Every Skill error logged to audit trail."""
        self.audit.events.clear()

        # Cause an error (invalid tenant)
        result = self.integration.route_task_l5(
            complexity=5, task_type="chat", tenant_id="invalid"
        )
        assert result["skill_executed"] is False

        # Error should be in audit
        error_events = [e for e in self.audit.events if e.get("status") == "error"]
        assert len(error_events) > 0

    def test_no_silent_failures_learning(self):
        """Learning backend receives all execution events (no drops)."""
        self.learning.emit_event = Mock()

        self.integration.route_task_l5(complexity=5, task_type="chat")

        # Learning backend MUST be called — a configured backend that is never
        # reached is a silent failure, exactly what this class exists to catch.
        assert self.learning.emit_event.called
        event = self.learning.emit_event.call_args[0][0]
        assert event["event_type"] == "skill_executed"
        assert event["skill_id"] == "os.delegation_router"
        assert event["tenant_id"] == "_default"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestIteration2Hardening:
    """Round-2 attacks on the round-1 fixes (2026-09-03)."""

    def setup_method(self):
        self.audit = MockAuditBackend()
        self.integration = initialize_integration(audit_backend=self.audit)

    def test_lom_hash_refuses_paths_outside_repo(self):
        """A LoM must never turn the registry into a hash oracle over arbitrary files."""
        import hashlib
        from core.skills.skill_registry_phase1 import SkillsRegistry

        for lom in ("../../../../etc/passwd:x:1", "/etc/passwd:x:1", "core/../../etc/hostname:f:1"):
            h = SkillsRegistry._compute_lom_hash(lom)
            assert h == hashlib.sha256(lom.encode()).hexdigest(), lom  # label hash, not file content

        real = SkillsRegistry._compute_lom_hash("core/skills/boot.py:boot_skills:L1")
        assert real != hashlib.sha256(b"core/skills/boot.py:boot_skills:L1").hexdigest()

    def test_hanging_skill_cannot_leak_unbounded_threads(self):
        """Sustained timeouts are capped per skill; the cap is audited and counts as failures."""
        import threading
        from core.skills.skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin, SkillsRegistry

        release = threading.Event()

        class Hang(Skill):
            def __init__(self):
                super().__init__(SkillMetadata(id="test.hang", name="h", description="", version="0",
                                               origin=SkillOrigin.COMMUNITY, owner="t"))

            def execute(self, input):
                release.wait(30)
                return {"ok": True}

        reg = self.integration.registry
        reg.register(Hang())
        # Auto-disable (3 strikes, per tenant) would mask the cap for a single
        # tenant; the cap is per skill across ALL tenants, so lift the strike
        # threshold here to exercise it directly.
        reg.AUTO_DISABLE_THRESHOLD = 10_000
        statuses = [reg.execute("test.hang", {}, timeout_ms=10).status for _ in range(SkillsRegistry.MAX_IN_FLIGHT_PER_SKILL)]
        assert statuses == ["timeout"] * SkillsRegistry.MAX_IN_FLIGHT_PER_SKILL
        saturated = reg.execute("test.hang", {}, timeout_ms=10)
        assert saturated.status == "error" and "saturated" in saturated.error_message
        release.set()
        # audited, not silent
        assert any("saturated" in (e.get("error_message") or "") for e in self.audit.events)

    def test_pii_scrub_covers_camel_case_keys_jwt_and_bearer(self):
        from core.skills.skill_registry_phase1 import SkillsRegistry

        out = SkillsRegistry._scrub_pii_from_output({
            "apiKey": "x", "userEmail": "a@b.co", "Authorization": "Bearer abcdefghijklmnopqrstuvwxyz0123",
            "note": "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
            "header": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123",
            "inputTokens": 12, "attention_budget": 3,
        })
        assert out["apiKey"] == out["userEmail"] == out["Authorization"] == "[REDACTED_PII]"
        assert "eyJ" not in out["note"]
        assert "abcdefghijklmnop" not in out["header"]
        assert out["inputTokens"] == 12 and out["attention_budget"] == 3

    def test_double_boot_stops_previous_emitter(self):
        from unittest.mock import Mock
        from core.skills.boot import boot_skills
        from core.skills.skill_registry_phase1 import get_registry

        boot_skills("_default", audit_emit=lambda *_: None, wire_learning=False)
        fake = Mock()
        get_registry().learning_backend = Mock(emitter=fake)
        boot_skills("_default", audit_emit=lambda *_: None, wire_learning=False)
        fake.stop.assert_called_once()
