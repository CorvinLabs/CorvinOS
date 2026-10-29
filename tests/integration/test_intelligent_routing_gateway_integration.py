"""
E2E integration tests for IntelligentRouter in dispatcher.py (ADR-0867)

Validates the complete wiring of intelligent model routing from task submission
through gateway dispatch to engine spawning.

These tests verify:
1. Simple tasks → Haiku (low cost, fast execution)
2. Medium tasks → Sonnet (balanced cost/quality)
3. Complex tasks → Opus (best reasoning, higher cost)
4. Audit trail logging of routing decisions
5. Fallback to operator config if router unavailable
6. Real end-to-end dispatch with correct model selection

Expected model ids are the NEWEST id of a family (``model_lineage.latest``, ADR-2090), never a literal:
the router routes a tier to the newest registered model of its family, so a literal goes stale with
every release. The tests run against a sandboxed ``CORVIN_HOME`` without an operator pin, because a
``worker_model`` pin outranks the router (ADR-2309) and would make every routing assertion vacuous.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.skills.os_skills.intelligent_router import (
    IntelligentRouter,
    RoutingDecision,
)
from core.gateway.corvin_gateway.dispatcher import RunDispatcher

_SHARED = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


def _newest(family: str) -> str:
    """The newest registered model id of ``family`` - the id the router must route that tier to."""
    import model_lineage  # type: ignore[import-not-found]  # noqa: PLC0415

    model = model_lineage.latest(family)
    assert model, f"model_lineage knows no model for family {family!r}"
    return model


@pytest.fixture(autouse=True)
def _sandboxed_tenant_without_pin(tmp_path, monkeypatch):
    """A fresh CORVIN_HOME: no tenant.corvin.yaml, hence no worker_model pin (ADR-2309)."""
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    return home


# Real-word prompts. The router counts words/characters, not whitespace, so padding a short prompt with
# spaces (as an earlier revision did) never changes its tier.
_SIMPLE = "Write a hello world function in Python"
_MEDIUM = (
    "Please rewrite the following meeting notes into a short friendly summary for the whole team, "
    "keep the dates and the names of the owners, and list the open action items at the end of the "
    "message so that nobody has to read the long original text again before the next weekly meeting"
)
_COMPLEX = " ".join(f"requirement{i}" for i in range(220))


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1: Model Routing Decision Tests
# Verify that _resolve_worker_model uses IntelligentRouter correctly
# ─────────────────────────────────────────────────────────────────────────────


class TestIntelligentRoutingIntegration:
    """Test IntelligentRouter integration in gateway dispatcher."""

    def test_resolve_worker_model_uses_intelligent_router_for_simple_task(self):
        """Simple task (short prompt) → _resolve_worker_model returns Haiku."""
        model = RunDispatcher._resolve_worker_model(
            tenant_id="_default", engine_id="native", prompt=_SIMPLE,
        )
        assert model == _newest("haiku"), f"Expected Haiku for simple task, got {model}"

    def test_resolve_worker_model_uses_intelligent_router_for_medium_task(self):
        """Medium task (~50-70 tokens, no deep-analysis keyword) → Sonnet."""
        model = RunDispatcher._resolve_worker_model(
            tenant_id="_default", engine_id="native", prompt=_MEDIUM,
        )
        assert model == _newest("sonnet"), f"Expected Sonnet for medium task, got {model}"

    def test_analysis_keyword_bumps_a_medium_prompt_to_opus(self):
        """Documented Phase 2 rule (intelligent_router.py): an analysis keyword on a prompt over
        80 chars bumps SIMPLE/MEDIUM to COMPLEX. The old 'medium' prompt started with 'Analyze', so
        it legitimately routes to Opus - this pins that rule instead of the stale Sonnet expectation."""
        prompt = (
            "Analyze the following code and suggest improvements:\n"
            "def calculate_fibonacci(n):\n    if n <= 1:\n        return n\n"
            "    return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)\n"
            "The function is slow. What's the issue and how would you fix it?"
        )
        model = RunDispatcher._resolve_worker_model(
            tenant_id="_default", engine_id="native", prompt=prompt,
        )
        assert model == _newest("opus"), f"analysis keyword should bump to Opus, got {model}"

    def test_resolve_worker_model_uses_intelligent_router_for_complex_task(self):
        """Complex task (>= 150 tokens) → _resolve_worker_model returns Opus."""
        model = RunDispatcher._resolve_worker_model(
            tenant_id="_default", engine_id="native", prompt=_COMPLEX,
        )
        assert model == _newest("opus"), f"Expected Opus for complex task, got {model}"

    def test_resolve_worker_model_without_prompt_uses_operator_config(self):
        """No prompt provided → fallback to operator-configured model."""
        model = RunDispatcher._resolve_worker_model(
            tenant_id="_default", engine_id="native", prompt="",
        )
        # Without prompt, should fall back to config (empty or the engine default)
        assert isinstance(model, str), "Model should be a string"

    def test_resolve_worker_model_handles_router_unavailable(self):
        """IntelligentRouter unavailable → fallback to operator config."""
        # The bridge is imported inside _resolve_worker_model and its classmethod is called, so the
        # unavailability must be injected at the method. (Replacing the class with a mock whose
        # *constructor* raises never fires: the class is never constructed, and the call returned a
        # MagicMock instead of a str.)
        with patch(
            "core.skills.os_skills.intelligent_router_integration.IntelligentRouterBridge."
            "route_with_intelligent_selection",
            side_effect=ImportError("Router not available"),
        ):
            model = RunDispatcher._resolve_worker_model(
                tenant_id="_default", engine_id="native", prompt="Some task",
            )
        assert isinstance(model, str), "Should fall back to string config"

    def test_resolve_worker_model_handles_router_exception(self):
        """IntelligentRouter raises exception → fallback gracefully."""
        with patch(
            "core.skills.os_skills.intelligent_router_integration.IntelligentRouterBridge."
            "route_with_intelligent_selection",
            side_effect=RuntimeError("Router error"),
        ):
            model = RunDispatcher._resolve_worker_model(
                tenant_id="_default", engine_id="native", prompt="Some task",
            )
        assert isinstance(model, str), "Should handle exception and return string"


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2: Distribution Tests
# Verify that over many tasks, models are distributed correctly
# ─────────────────────────────────────────────────────────────────────────────


class TestModelDistribution:
    """Verify correct distribution of models across task types."""

    def test_model_distribution_across_varied_tasks(self):
        """Varied tasks reach all three tiers, each task landing on its own tier's model."""
        tasks = [
            ("hello world", "haiku"),
            ("write a function", "haiku"),
            ("calculate sum", "haiku"),
            (_MEDIUM, "sonnet"),
            (_MEDIUM + " and then post the result in the team channel", "sonnet"),
            (_MEDIUM + " and mention who joined late", "sonnet"),
            (_COMPLEX, "opus"),
            (_COMPLEX + " requirement221", "opus"),
            (_COMPLEX + " requirement221 requirement222", "opus"),
        ]
        counts = {"haiku": 0, "sonnet": 0, "opus": 0}
        for prompt, family in tasks:
            model = RunDispatcher._resolve_worker_model(
                tenant_id="_default", engine_id="native", prompt=prompt,
            )
            assert model == _newest(family), f"{prompt[:30]!r} → {model}, expected {_newest(family)}"
            counts[family] += 1

        for family, count in counts.items():
            assert count > 0, f"Model family {family} was never selected"


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3: Audit Trail Logging Tests
# Verify that routing decisions are logged immutably
# ─────────────────────────────────────────────────────────────────────────────

_AUDIT_DEFECT = (
    "PRODUCT DEFECT (found 2026-10-30): RunDispatcher._resolve_worker_model calls "
    "forge.security_events.audit_event(...) - that function does not exist, and 'intelligent_routing_decision' "
    "is registered in neither EVENT_SEVERITY nor _EVENT_ALLOWLIST. The AttributeError is swallowed by the "
    "best-effort 'except Exception: pass', so NO routing decision has ever been written to the audit chain. "
    "strict xfail: this test turns green (XPASS → failure) the moment the dispatcher really emits the event."
)


def _events_double() -> MagicMock:
    """A stand-in for the security_events module that only has the REAL module's attributes.

    A bare MagicMock accepts any attribute, so it would happily accept the dispatcher's call to the
    non-existent ``audit_event`` and hide exactly the defect these tests exist to expose."""
    from forge import security_events  # noqa: PLC0415

    return MagicMock(spec=security_events)


def _routing_audit_calls(mock_events: MagicMock) -> list:
    """Every call on the mocked security_events module that carries the routing-decision event name."""
    found = []
    for call in mock_events.mock_calls:
        flat = list(call.args) + list(call.kwargs.values())
        if any(a == "intelligent_routing_decision" for a in flat):
            found.append(call)
    return found


class TestAuditTrailLogging:
    """Verify routing decisions are logged to audit trail."""

    @pytest.mark.xfail(strict=True, reason=_AUDIT_DEFECT)
    def test_routing_decision_logged_to_audit_trail(self):
        """Every routing decision should be logged."""
        mock_events = _events_double()
        with patch("core.gateway.corvin_gateway.dispatcher._security_events", mock_events):
            RunDispatcher._resolve_worker_model(
                tenant_id="_default", engine_id="native", prompt=_SIMPLE,
            )
        assert _routing_audit_calls(mock_events), "no routing decision reached the audit writer"

    @pytest.mark.xfail(strict=True, reason=_AUDIT_DEFECT)
    def test_audit_trail_contains_model_and_reasoning(self):
        """Audit log should contain model selected and reasoning."""
        mock_events = _events_double()
        with patch("core.gateway.corvin_gateway.dispatcher._security_events", mock_events):
            model = RunDispatcher._resolve_worker_model(
                tenant_id="_default", engine_id="native", prompt=_COMPLEX,
            )
        calls = _routing_audit_calls(mock_events)
        assert calls, "no routing decision reached the audit writer"
        payload = next(a for c in calls for a in list(c.args) + list(c.kwargs.values()) if isinstance(a, dict))
        assert payload["model_selected"] == model
        assert payload["reasoning"]


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4: Integration with RunDispatcher._run_one
# Verify end-to-end dispatch with model routing
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatcherIntegration:
    """Test full dispatcher integration with intelligent routing."""

    def test_run_one_passes_prompt_to_model_resolver(self):
        """_run_one must hand the prompt to _resolve_worker_model (the call site that makes routing live)."""
        import inspect

        src = inspect.getsource(RunDispatcher)
        assert "self._resolve_worker_model(tenant_id, _gw_engine_id, prompt=clean_prompt)" in src


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5: Real-World Scenarios
# ─────────────────────────────────────────────────────────────────────────────


class TestRealWorldScenarios:
    """Test realistic task routing scenarios."""

    def test_code_review_routes_to_sonnet_or_opus(self):
        """Code review task → Sonnet (balanced) or Opus (complex)."""
        prompt = """
        Review this production code:

        class UserManager:
            def authenticate(self, username, password):
                user = self.db.query(User).filter_by(username=username).first()
                if user and check_password(user.password_hash, password):
                    return user
                return None

            def create_user(self, username, email, password):
                if self.db.query(User).filter_by(username=username).exists():
                    raise UserExists()
                user = User(username=username, email=email)
                user.password_hash = hash_password(password)
                self.db.add(user)
                self.db.commit()
                return user

        Issues found:
        - No input validation
        - No rate limiting on auth
        - DB query vulnerable to timing attacks
        - Missing password complexity requirements

        Please suggest fixes and provide secure implementation.
        """

        model = RunDispatcher._resolve_worker_model(
            tenant_id="_default", engine_id="native", prompt=prompt,
        )

        # Code review is moderately complex → Sonnet or Opus
        assert model in (_newest("sonnet"), _newest("opus")), \
            f"Code review should use Sonnet/Opus, got {model}"

    def test_simple_help_request_routes_to_haiku(self):
        """Simple help → Haiku (cheap, fast)."""
        model = RunDispatcher._resolve_worker_model(
            tenant_id="_default", engine_id="native", prompt="How do I reverse a list in Python?",
        )
        assert model == _newest("haiku"), f"Simple help should use Haiku, got {model}"

    def test_ml_pipeline_design_routes_to_opus(self):
        """Complex ML system design → Opus."""
        prompt = """
        Design an end-to-end ML pipeline for real-time recommendation system:

        1. Data ingestion from 100+ sources (user behavior, products, context)
        2. Feature engineering (user embeddings, item embeddings, context)
        3. Model selection (candidate generation, ranking, personalization)
        4. Online serving with <100ms p99 latency
        5. A/B testing framework
        6. Model monitoring and retraining
        7. Privacy preservation (GDPR, user data deletion)
        8. Cost optimization for 1M+ QPS

        Provide detailed architecture, tech stack recommendations, and trade-offs.
        """

        model = RunDispatcher._resolve_worker_model(
            tenant_id="_default", engine_id="native", prompt=prompt,
        )

        assert model == _newest("opus"), f"ML system design should use Opus, got {model}"
