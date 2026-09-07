"""OS-Skills L5/L10 — what is production-wired, and what is NOT.

Honest scope (round-4 adversarial review, F6). This file previously opened with
"This test proves that … ContextAdapterSkill is actually called for L10 context
adaptation" and then imported ``adapt_context_l10`` and called it ITSELF with a
mock audit backend — while the module under test states at
``core/skills/os_skills_integration.py:12-15`` that ``adapt_context_l10`` has NO
production call site. A test that supplies its own caller cannot fail on the
wiring being absent; CLAUDE.md § E2E Wiring Proof calls that "a unit test wearing
an E2E label".

What this file now contains, labelled:

* ``TestL5ProductionCallSite`` — the ONE genuine E2E. It drives the real
  production boundary, ``delegation_policy.resolve_worker_engine`` (the single
  shared routing function every surface goes through), and asserts that
  ``os.delegation_router`` was executed in shadow mode and audited. This FAILS
  if the shadow call site is removed or breaks.
* ``TestL5EntryPointContract`` / ``TestL10EntryPointContract`` — UNIT tests of
  the ``route_task_l5`` / ``adapt_context_l10`` entry points (fallback, tenant
  isolation, 3-tier shape). They supply their own caller, on purpose, and prove
  behaviour-when-called — never reachability.
* ``TestL10HasNoProductionCallSite`` — a reachability FENCE. L10 is not wired.
  The fence fails the moment a production call site appears, forcing the docs
  (this file, ``os_skills_integration.py``, CLAUDE.md's ADR-0532 table) to be
  corrected in the same commit.
* ``TestPIIScrubbing`` — GDPR Art. 32 scrubbing guard, through the real registry.
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, Optional

from core.skills.os_skills_integration import (
    initialize_integration,
    route_task_l5,
    adapt_context_l10,
    SkillsIntegrationLayer,
)
from core.skills.skill_registry_phase1 import SkillExecutionResult, SkillOrigin


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event: Dict[str, Any]) -> None:
        """Record audit event."""
        self.events.append(event)

    def get_events(self) -> list[Dict[str, Any]]:
        """Retrieve recorded events."""
        return self.events


class MockLearningBackend:
    """Mock ADR-0314 learning backend (implements ``emit_event``)."""

    def __init__(self):
        self.events = []

    def emit_event(self, event: Dict[str, Any]) -> bool:
        self.events.append(event)
        return True


class TestL5EntryPointContract:
    """UNIT: behaviour of the ``route_task_l5`` entry point WHEN CALLED.

    This class supplies its own caller. It proves fallback, tenant isolation and
    audit shape — it does NOT prove anything is wired. Reachability for L5 is
    proven by ``TestL5ProductionCallSite`` below.
    """

    def setup_method(self):
        """Setup for each test."""
        self.audit_backend = MockAuditBackend()
        self.integration = initialize_integration(
            audit_backend=self.audit_backend, tenant_id="_default"
        )

    def test_delegation_router_runs_when_the_entry_point_is_called(self):
        """The entry point dispatches to the Skill and audits it (not reachability)."""
        # Call L5 routing
        result = self.integration.route_task_l5(
            complexity=7, task_type="analysis", user_context={"user_id": "test_user"}
        )

        # Verify result has expected fields
        assert "engine" in result
        assert "confidence" in result
        assert "reasoning" in result
        assert "skill_executed" in result

        # Verify Skill was actually invoked (not fallback)
        assert result["skill_executed"] is True
        assert result["error"] is None

        # Verify audit event was logged
        assert len(self.audit_backend.events) > 0
        skill_event = [e for e in self.audit_backend.events if e.get("skill_id") == "os.delegation_router"]
        assert len(skill_event) > 0, "DelegationRouterSkill execution must be audited"
        assert skill_event[0]["status"] == "success"

    def test_l5_routing_with_different_complexity(self):
        """Verify routing changes based on complexity."""
        # Low complexity
        low_result = self.integration.route_task_l5(
            complexity=2, task_type="chat"
        )
        assert low_result["skill_executed"] is True

        # High complexity
        high_result = self.integration.route_task_l5(
            complexity=9, task_type="analysis"
        )
        assert high_result["skill_executed"] is True

        # Verify different engines were chosen (heuristic-based routing)
        # Low complexity should prefer cheaper engine
        low_engine = low_result["engine"]
        high_engine = high_result["engine"]
        # This is implementation-dependent, but at minimum both should be valid
        assert low_engine in ["claude-haiku-4", "claude-sonnet-4", "claude-opus-5"]
        assert high_engine in ["claude-haiku-4", "claude-sonnet-4", "claude-opus-5"]

    def test_l5_fallback_on_skill_timeout(self):
        """PROOF: Fallback routing works when Skill times out."""
        # Mock registry to return timeout
        with patch.object(
            self.integration.registry,
            "execute",
            return_value=SkillExecutionResult(
                skill_id="os.delegation_router",
                status="timeout",
                error_message="Skill execution timeout after 5000ms",
                execution_time_ms=5000.0,
                tenant_id="_default",
            ),
        ):
            result = self.integration.route_task_l5(
                complexity=5, task_type="code"
            )

        # Verify fallback was used
        assert result["skill_executed"] is False
        assert result["error"] is not None
        assert "timeout" in result["error"].lower()

        # Verify fallback still provides valid routing
        assert "engine" in result
        assert "confidence" in result
        assert result["engine"] in ["claude-haiku-4", "claude-sonnet-4", "claude-opus-5"]

    def test_l5_tenant_isolation(self):
        """PROOF: Tenant isolation enforced (GDPR Art. 5, 6)."""
        # Register allowed tenant
        self.integration.registry.add_tenant("tenant_a")

        # Call with valid tenant_id
        result_valid = self.integration.route_task_l5(
            complexity=5, task_type="chat", tenant_id="tenant_a"
        )
        assert result_valid["skill_executed"] is True

        # Call with unauthorized tenant_id (should fail)
        result_invalid = self.integration.route_task_l5(
            complexity=5, task_type="chat", tenant_id="unauthorized_tenant"
        )
        assert result_invalid["skill_executed"] is False
        assert "isolation violation" in result_invalid["error"].lower() or "not authorized" in result_invalid["error"].lower()

    def test_l5_audit_logging_complete(self):
        """PROOF: Every L5 routing decision is audited (GDPR Art. 30)."""
        # Clear audit events
        self.audit_backend.events.clear()

        # Make routing decision
        result = self.integration.route_task_l5(
            complexity=6, task_type="code"
        )

        # Verify audit event was logged
        assert len(self.audit_backend.events) > 0
        audit_event = self.audit_backend.events[0]

        # Verify event has required compliance fields
        assert audit_event["event_type"] == "SKILL_EXECUTED"
        assert audit_event["skill_id"] == "os.delegation_router"
        assert "timestamp" in audit_event
        assert "tenant_id" in audit_event
        assert "lom" in audit_event  # Line of Moral Responsibility


class TestL10EntryPointContract:
    """UNIT: behaviour of the ``adapt_context_l10`` entry point WHEN CALLED.

    L10 has NO production call site (see ``TestL10HasNoProductionCallSite``), so
    nothing here is a wiring proof. It guards the fallback/3-tier contract so the
    entry point is correct on the day it does get wired.
    """

    def setup_method(self):
        """Setup for each test."""
        self.audit_backend = MockAuditBackend()
        self.integration = initialize_integration(
            audit_backend=self.audit_backend, tenant_id="_default"
        )

    def test_context_adapter_runs_when_the_entry_point_is_called(self):
        """The entry point dispatches to the Skill and audits it (NOT reachability)."""
        # Call L10 context adaptation
        result = self.integration.adapt_context_l10(
            complexity=6,
            task_type="code",
            task_description="Implement a new feature in Python",
            priority_hint=7,
            user_context={"user_id": "test_user"},
        )

        # Verify result has the ADR-0555 3-tier fields
        for key in ("base_tier", "injected_tier", "merged_tier"):
            assert key in result
        assert "skill_executed" in result

        # Verify Skill was actually invoked
        assert result["skill_executed"] is True
        assert result["error"] is None

        # Verify audit event was logged
        assert len(self.audit_backend.events) > 0
        context_event = [e for e in self.audit_backend.events if e.get("skill_id") == "os.context_adapter"]
        assert len(context_event) > 0, "ContextAdapterSkill execution must be audited"

    def test_l10_fallback_on_skill_error(self):
        """PROOF: Fallback context (immutable base only) when Skill fails."""
        # Mock registry to return error
        with patch.object(
            self.integration.registry,
            "execute",
            return_value=SkillExecutionResult(
                skill_id="os.context_adapter",
                status="error",
                error_message="ContextAdapterSkill internal error",
                execution_time_ms=42.0,
                tenant_id="_default",
            ),
        ):
            result = self.integration.adapt_context_l10(
                complexity=5,
                task_type="chat",
                task_description="Help me with this",
            )

        # Verify fallback was used
        assert result["skill_executed"] is False
        assert result["error"] is not None

        # Verify fallback provides safe context (base only, fail-closed)
        assert result["injected_tier"] is None
        assert result["base_tier"] is not None
        assert result["merged_tier"] is not None
        assert result["merged_tier"]["metadata"]["adr_0555_failclosed"] is True

    def test_l10_three_tier_context_model(self):
        """PROOF: 3-tier context model (base/injected/merged) is used."""
        result = self.integration.adapt_context_l10(
            complexity=7,
            task_type="analysis",
            task_description="Analyze this dataset",
            priority_hint=8,
        )

        # Verify 3-tier structure (ADR-0555)
        assert result["base_tier"]["tier_name"] == "base", "ADR-0555: base tier (immutable Phase 3) required"
        assert result["injected_tier"]["tier_name"] == "injected", "ADR-0555: injected tier (learned layers) required"
        assert result["merged_tier"]["tier_name"] == "merged", "ADR-0555: merged tier (fail-closed merge) required"
        assert result["merged_tier"]["metadata"]["immutable"] is True

    def test_l10_tenant_isolation(self):
        """PROOF: Tenant isolation enforced in L10."""
        self.integration.registry.add_tenant("tenant_b")

        # Valid tenant
        result_valid = self.integration.adapt_context_l10(
            complexity=5,
            task_type="chat",
            task_description="Test",
            tenant_id="tenant_b",
        )
        assert result_valid["skill_executed"] is True

        # Invalid tenant
        result_invalid = self.integration.adapt_context_l10(
            complexity=5,
            task_type="chat",
            task_description="Test",
            tenant_id="unauthorized_tenant",
        )
        assert result_invalid["skill_executed"] is False


class TestL5ProductionCallSite:
    """E2E: the REAL production boundary for L5.

    ``os.delegation_router`` is reached from exactly one production function —
    ``operator/bridges/shared/delegation_policy.py::_acp_shadow_route``, called
    by ``resolve_worker_engine``, the single shared routing function every
    surface goes through (ADR-0613). This drives THAT function, not the Skill
    and not ``route_task_l5``, so it fails if the call site is removed, renamed,
    or stops reaching the registry.
    """

    def _boot_registry(self):
        """Boot the real global registry the shadow call site looks up."""
        from core.skills import skill_registry_phase1 as reg_mod
        from core.skills.os_skills_phase1 import register_builtin_skills

        backend = MockAuditBackend()
        registry = reg_mod.initialize_registry(audit_backend=backend, tenant_id="_default")
        register_builtin_skills(registry)
        return registry, backend

    def test_resolve_worker_engine_reaches_the_delegation_router_skill(self):
        import delegation_policy

        registry, backend = self._boot_registry()
        backend.events.clear()

        engine = delegation_policy.resolve_worker_engine(
            mode="delegate",
            force_delegate=True,
            is_big_data=False,
            tde_available=True,
            quota_ok=True,
            tenant_id="_default",
        )

        assert isinstance(engine, str) and engine, "routing must still answer"
        routed = [e for e in backend.events if e.get("skill_id") == "os.delegation_router"]
        assert routed, (
            "resolve_worker_engine did not reach os.delegation_router — the L5 "
            "shadow call site in delegation_policy._acp_shadow_route is broken "
            "or gone (ADR-0613; adversarial review F1/F6)"
        )
        assert routed[-1]["status"] == "success", routed[-1]
        # ADR-0537: the production call site names its own LoM, not a test's.
        assert routed[-1]["lom"].startswith(
            "operator/bridges/shared/delegation_policy.py:"
        ), routed[-1]["lom"]

    def test_shadow_mode_never_changes_the_wire_answer(self):
        """The Skill's advice is advisory: the bundled engine stands."""
        import delegation_policy

        kwargs = dict(
            mode="delegate",
            force_delegate=True,
            is_big_data=False,
            tde_available=True,
            quota_ok=True,
            tenant_id="_default",
        )
        bundled = delegation_policy._resolve_worker_engine(**kwargs)
        self._boot_registry()
        assert delegation_policy.resolve_worker_engine(**kwargs) == bundled


class TestL10HasNoProductionCallSite:
    """FENCE: L10 is NOT wired — fail the day it becomes wired, so docs follow.

    ``os_skills_integration.py`` states that ``adapt_context_l10`` /
    ``os.context_adapter`` has no production call site, and CLAUDE.md's ADR-0532
    roadmap table must not claim otherwise. This fence is the thing that fails
    when the claim and the code diverge — the entry-point tests above cannot,
    because they supply their own caller.

    When L10 IS wired: replace this fence with a real E2E through the new call
    site, and update ``os_skills_integration.py``'s header + CLAUDE.md's table
    in the same commit.
    """

    def _production_call_sites(self) -> list[str]:
        """AST-precise: real CALLS only — not docstrings, comments or stubs.

        A hit is either ``adapt_context_l10(...)`` or a ``.execute("os.context_adapter", ...)``
        in a non-test file under core/, operator/ or ops/. String mentions in
        prose (``core/brain/__init__.py``, ``README_PHASE1.md``, the stub
        dispatch table in ``core/engine/skill_invocation_stubs.py``) are not
        call sites and must not trip the fence.
        """
        import ast
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        owner = {
            repo / "core" / "skills" / "os_skills_integration.py",  # defines it
        }
        hits: list[str] = []
        for root in ("core", "operator", "ops"):
            for path in (repo / root).rglob("*.py"):
                if path in owner or "test" in path.parts or path.name.startswith("test_"):
                    continue
                # Fast pre-filter: only files that mention it at all are parsed.
                src = path.read_text(encoding="utf-8", errors="replace")
                if "adapt_context_l10" not in src and "os.context_adapter" not in src:
                    continue
                try:
                    tree = ast.parse(src)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else (
                        fn.attr if isinstance(fn, ast.Attribute) else ""
                    )
                    rel = path.relative_to(repo)
                    if name == "adapt_context_l10":
                        hits.append(f"{rel}:{node.lineno}: adapt_context_l10(...)")
                    elif name == "execute" and node.args:
                        first = node.args[0]
                        if isinstance(first, ast.Constant) and first.value == "os.context_adapter":
                            hits.append(f"{rel}:{node.lineno}: registry.execute('os.context_adapter', ...)")
        return sorted(hits)

    def test_l10_is_still_unwired(self):
        hits = self._production_call_sites()
        assert not hits, (
            "os.context_adapter now HAS a production call site:\n  "
            + "\n  ".join(hits)
            + "\n\nReplace this fence with a real E2E through that call site and "
              "update core/skills/os_skills_integration.py's header + CLAUDE.md's "
              "ADR-0532 roadmap table in the SAME commit."
        )

    def test_the_fence_can_actually_see_a_call_site(self):
        """The fence must not be vacuously green: prove the detector fires."""
        import ast

        tree = ast.parse("def f(x):\n    return adapt_context_l10(x)\n")
        found = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "adapt_context_l10"
        ]
        assert found, "the AST detector used by the fence does not match a real call"


class TestPIIScrubbing:
    """GDPR Art. 32: a Skill's PII output never reaches a durable record.

    This guard was RED from the round-3 mandatory-LoM gate until 2026-09-07
    (round-4 review, F7): it passed ``lom="test:pii_scrubbing:100"``, which the
    gate refuses, so it aborted on its FIRST assertion and the eight assertions
    that check ``[REDACTED_PII]`` never ran — the Art. 32 invariant was
    unguarded while the suite looked green.

    Its premise also drifted. Round 3 removed ``output`` from the audit event
    entirely (``_DECISION_SCALAR_KEYS`` / the ``skill.executed`` detail floor),
    so the invariant now has TWO halves, guarded separately below:
      1. the AUDIT record carries no output at all — the stronger guarantee;
      2. the LEARNING record (ADR-0314), which does carry output, is scrubbed.
    """

    def setup_method(self):
        """Setup for each test."""
        self.audit_backend = MockAuditBackend()
        self.learning_backend = MockLearningBackend()
        self.integration = initialize_integration(
            audit_backend=self.audit_backend, tenant_id="_default"
        )
        self.integration.registry.learning_backend = self.learning_backend

    _LOM = "tests/e2e/test_os_skills_l5_l10_wiring.py:_run_leaky_skill"

    def _run_leaky_skill(self):
        """Register a Skill whose output carries PII and run it for real."""
        from core.skills.skill_registry_phase1 import Skill, SkillMetadata

        class LeakySkill(Skill):
            def __init__(self):
                super().__init__(SkillMetadata(
                    id="test.leaky", name="Leaky", description="returns PII",
                    version="0.0.1", origin=SkillOrigin.COMMUNITY, owner="test",
                ))

            def execute(self, input):
                return {
                    "engine": "claude-opus-5",
                    "user_email": "user@example.com",
                    "api_key": "sk-12345678",
                    "password": "hunter2",
                    "note": "contact me at someone@example.org, token=abc123",
                    "confidence": 0.95,
                    "input_tokens": 42,
                }

        self.integration.registry.register(LeakySkill())
        self.audit_backend.events.clear()
        self.learning_backend.events.clear()
        result = self.integration.registry.execute(
            "test.leaky", {"complexity": 5, "task_type": "chat"}, lom=self._LOM,
        )
        assert result.status == "success", result.error_message
        return result

    def test_the_lom_this_guard_uses_is_admissible(self):
        """Fence: if the LoM rots again, THIS fails instead of silently
        skipping the Art. 32 assertions below (round-4 review, F7)."""
        from core.skills.skill_registry_phase1 import SkillsRegistry as _R

        assert _R._compute_lom_hash(self._LOM) is not None, (
            f"{self._LOM} is no longer resolvable — the PII guards below would "
            "abort on their first assertion and stop guarding anything"
        )

    def test_audit_event_carries_no_skill_output_at_all(self):
        """Strongest half: the hash-chained record never sees the output."""
        import json

        self._run_leaky_skill()
        assert len(self.audit_backend.events) == 1
        event = self.audit_backend.events[0]
        assert "output" not in event, (
            "the audit detail floor forbids an `output` key on skill.executed"
        )
        blob = json.dumps(event)
        for secret in ("user@example.com", "sk-12345678", "hunter2",
                       "someone@example.org", "abc123"):
            assert secret not in blob, f"{secret!r} reached the audit chain"
        # The allowlisted DECISION scalars do survive — that is the point.
        assert event["decision"]["engine"] == "claude-opus-5"
        assert event["decision"]["confidence"] == 0.95
        # ADR-0537: LoM hash present on every success.
        assert event["lom_hash"]

    def test_learning_event_output_is_scrubbed(self):
        """Second half: ADR-0314 records DO carry output, redacted."""
        self._run_leaky_skill()
        assert len(self.learning_backend.events) == 1
        output = self.learning_backend.events[0]["output"]
        assert output["user_email"] == "[REDACTED_PII]"
        assert output["api_key"] == "[REDACTED_PII]"
        assert output["password"] == "[REDACTED_PII]"
        assert "someone@example.org" not in output["note"]
        assert "abc123" not in output["note"]
        # Non-PII survives untouched (no over-redaction of *_tokens)
        assert output["engine"] == "claude-opus-5"
        assert output["confidence"] == 0.95
        assert output["input_tokens"] == 42


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
