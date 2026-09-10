"""E2E-wiring — GET/PUT /v1/engine/config, POST /v1/engine/external-provider/test
(routes/engine_api.py).

Phase 1 (2026-09-10, K=1) shipped this router with an in-memory
``_MOCK_ENGINE_CONFIG`` dict and a "test" endpoint that always returned
success — the same class of gap this repo's own culture calls out
repeatedly (built, unit-tested in isolation, never proven against real
state). This suite pins the REAL FastAPI router with a real tenant JSON
config file under a temp CORVIN_HOME, and proves the run_count/confidence
numbers come from real audit-chain aggregation (via
operator/bridges/shared/model_selector_shadow.py), not from constants.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import engine_api as EA  # noqa: E402


def _fake_record(tenant_id: str = "_default") -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


class EngineApiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(EA.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: None
        return TestClient(app)

    def test_get_default_is_honest_zero_state(self) -> None:
        r = self._client().get("/v1/engine/config")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["total_samples"], 0)
        self.assertEqual(body["total_learned_samples"], 0)
        self.assertEqual(body["learning_status"], "idle")
        self.assertIsNone(body["last_learning_update"])
        for task_type in ("corvinOS", "SIMPLE", "MEDIUM", "COMPLEX"):
            self.assertEqual(body["models"][task_type]["run_count"], 0)
            # 0.5 = ConfidenceOptimizer's uninformed Bayesian prior (ADR-0644),
            # not 0.0 — ConfidenceOptimizer.get_stats() never returns 0.0 for
            # an unseen (task_type, model): that would misreport "confidently
            # bad" for a model nobody has fed outcomes for yet.
            self.assertEqual(body["models"][task_type]["confidence_score"], 0.5)
            self.assertFalse(body["models"][task_type]["is_converged"])

    def test_put_persists_and_get_reflects_it(self) -> None:
        client = self._client()
        r = client.put(
            "/v1/engine/config",
            json={"models": {"MEDIUM": {
                "task_type": "MEDIUM", "selected_model": "z-ai/glm-4.6",
                "provider": "openrouter", "alternatives": [],
            }}},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["models"]["MEDIUM"]["selected_model"], "z-ai/glm-4.6")
        self.assertEqual(r.json()["models"]["MEDIUM"]["provider"], "openrouter")

        r2 = client.get("/v1/engine/config")
        self.assertEqual(r2.json()["models"]["MEDIUM"]["selected_model"], "z-ai/glm-4.6")
        # Untouched tiers keep their defaults — a partial PUT is a merge, not a replace.
        self.assertEqual(r2.json()["models"]["SIMPLE"]["provider"], None)

    def test_put_rejects_unknown_anthropic_model(self) -> None:
        r = self._client().put(
            "/v1/engine/config",
            json={"models": {"SIMPLE": {
                "task_type": "SIMPLE", "selected_model": "gpt-5-nonexistent",
                "provider": None, "alternatives": [],
            }}},
        )
        self.assertEqual(r.status_code, 400, r.text)

    def test_put_rejects_unknown_provider(self) -> None:
        r = self._client().put(
            "/v1/engine/config",
            json={"models": {"SIMPLE": {
                "task_type": "SIMPLE", "selected_model": "x",
                "provider": "not_a_real_provider", "alternatives": [],
            }}},
        )
        self.assertEqual(r.status_code, 400, r.text)

    def test_classification_count_is_real_not_constant(self) -> None:
        """total_samples (the top-bar classification tally) comes from real
        shadow-classification audit events, not a hand-picked number."""
        import model_selector_shadow as mss

        # A short, keyword-free prompt classifies as "simple" (deterministic).
        mss.shadow_classify_task("list the files here", "_default")
        mss.shadow_classify_task("summarize this text", "_default")

        r = self._client().get("/v1/engine/config")
        body = r.json()
        self.assertEqual(body["total_samples"], 2)
        self.assertEqual(body["learning_status"], "learning")
        self.assertIsNotNone(body["last_learning_update"])

    def test_learned_confidence_is_real_bayesian_not_constant(self) -> None:
        """confidence_score/run_count per task type come from
        ConfidenceOptimizer's real Bayesian+EMA update (ADR-0644), fed by
        real turn outcomes — not the classifier's own static heuristic
        confidence, and not a hand-picked number."""
        import model_selector_shadow as mss

        for _ in range(6):
            mss.shadow_classify_task("list the files here", "_default", chat_key="c1")
            mss.report_turn_outcome("c1", success=True)

        r = self._client().get("/v1/engine/config")
        body = r.json()
        self.assertEqual(body["models"]["SIMPLE"]["run_count"], 6)
        self.assertGreater(body["models"]["SIMPLE"]["confidence_score"], 0.5)  # moved up from the prior
        self.assertEqual(body["total_learned_samples"], 6)
        # corvinOS has no classifier equivalent — always zero, never fabricated.
        self.assertEqual(body["models"]["corvinOS"]["run_count"], 0)

    def test_saved_override_changes_future_classification(self) -> None:
        """A PUT'd preference must have a real effect on the next shadow
        classification (via ModelSelector(overrides=...)), not be cosmetic."""
        import model_selector_shadow as mss
        from core.models.model_selection_config import classifier_overrides
        from core.skills.os_skills.model_selector import ModelSelector

        client = self._client()
        client.put(
            "/v1/engine/config",
            json={"models": {"SIMPLE": {
                "task_type": "SIMPLE", "selected_model": "claude-opus-5",
                "provider": None, "alternatives": [],
            }}},
        )
        overrides = classifier_overrides("_default")
        result = ModelSelector(overrides=overrides).classify("list the files here", "_default")
        self.assertEqual(result.complexity, "simple")
        self.assertEqual(result.recommended_model, "claude-opus-5")
        self.assertIsNone(result.recommended_provider)

    def test_external_provider_test_reflects_real_fetch_result(self) -> None:
        """Proves the route actually calls engine_providers.fetch_models and
        forwards its verdict — not the Phase-1 mock's hardcoded
        is_connected=True regardless of input. Patches fetch_models itself
        (rather than asserting live network state, which the test sandbox
        doesn't control — e.g. a dev machine's local Ollama may genuinely be
        reachable) so the assertion is deterministic either way."""
        import engine_providers

        original = engine_providers.fetch_models
        engine_providers.fetch_models = lambda *a, **k: {  # noqa: E731
            "provider": "ollama_local", "reachable": False, "models": [],
            "count": 0, "error": "ollama not reachable at localhost:11434",
        }
        try:
            r = self._client().post(
                "/v1/engine/external-provider/test",
                json={"provider": "ollama_local"},
            )
        finally:
            engine_providers.fetch_models = original

        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body["is_connected"])
        self.assertIn("not reachable", body["error_message"])


if __name__ == "__main__":
    unittest.main()
