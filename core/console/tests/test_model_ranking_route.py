"""E2E wiring proof for ``ConfidenceOptimizer.rank_models`` (ADR-0885 step 0).

The ranking has exactly one production caller: ``GET
/v1/engine/analytics/task-type/{task_type}`` in
``routes/model_selection_analytics.py``. This test drives that route over
HTTP (FastAPI TestClient) against the REAL persistent optimizer under a temp
``CORVIN_HOME`` — the same store and audit path the console process uses —
so it proves the ranking is reachable and correct, not merely that the
method returns the right list when imported and called.

What it pins:
  * order is learned confidence, not alphabet or rate;
  * every row carries the published rate card, ``null`` when unpriced;
  * ``?max_output_usd_per_1k=`` drops models over the cap AND unpriced models
    (never treated as free, ADR-0763);
  * the response is tenant-scoped: another tenant's learning does not leak in.
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
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO))

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import model_selection_analytics as MSA  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.learning import model_selection_optimizer as MSO  # noqa: E402
from core.learning.model_selection_learner import model_price_per_1k  # noqa: E402


def _as_process_tenant(tenant_id: str):
    """Patch forge.security_events._current_tenant_id for one tenant."""
    from unittest import mock

    import corvin_core._bootstrap  # noqa: F401
    from forge import security_events  # type: ignore[import-not-found]
    return mock.patch.object(security_events, "_current_tenant_id", lambda: tenant_id)

# Real ids on the published rate card — positive control below asserts it.
CHEAP = "claude-haiku-4-5-20251001"
MID = "claude-sonnet-5"
DEAR = "claude-opus-5"
UNPRICED = "llama-3.3-70b"


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


class ModelRankingRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name
        MSO._optimizer = None  # the singleton must bind to THIS home

    def tearDown(self) -> None:
        MSO._optimizer = None
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _client(self, tenant_id: str = "_default") -> TestClient:
        app = FastAPI()
        app.include_router(MSA.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record(tenant_id)
        app.dependency_overrides[console_deps.require_csrf] = lambda: None
        return TestClient(app)

    @staticmethod
    def _feed(task_type: str, model: str, qualities: list[float], tenant_id: str = "_default") -> None:
        opt = MSO.get_optimizer()
        for q in qualities:
            opt.process_feedback(task_type, model, q, tenant_id)

    def test_rate_card_positive_control(self) -> None:
        for m in (CHEAP, MID, DEAR):
            self.assertIsNotNone(model_price_per_1k(m), m)
        self.assertIsNone(model_price_per_1k(UNPRICED))

    def test_ranked_by_learned_confidence_with_rates(self) -> None:
        self._feed("MEDIUM", DEAR, [0.95] * 8)
        self._feed("MEDIUM", CHEAP, [0.3] * 8)
        self._feed("MEDIUM", MID, [0.7] * 8)
        # A model that only ever appears under another task type must not show.
        self._feed("SIMPLE", UNPRICED, [0.99] * 8)

        r = self._client().get("/v1/engine/analytics/task-type/MEDIUM")
        self.assertEqual(r.status_code, 200, r.text)
        rows = r.json()["models"]
        self.assertEqual([x["model"] for x in rows], [DEAR, MID, CHEAP])
        for row in rows:
            self.assertEqual(row["n_samples"], 8)
            self.assertTrue(row["priced"])
            self.assertEqual(
                (row["input_usd_per_1k"], row["output_usd_per_1k"]),
                model_price_per_1k(row["model"]),
            )
            self.assertGreater(row["posterior_mean"], 0.0)
            self.assertLess(row["posterior_mean"], 1.0)

    def test_budget_cap_drops_dear_and_unpriced(self) -> None:
        self._feed("COMPLEX", DEAR, [0.9] * 6)
        self._feed("COMPLEX", MID, [0.6] * 6)
        self._feed("COMPLEX", UNPRICED, [0.99] * 6)  # would win on confidence

        client = self._client()
        uncapped = client.get("/v1/engine/analytics/task-type/COMPLEX").json()["models"]
        self.assertEqual([x["model"] for x in uncapped], [UNPRICED, DEAR, MID])
        unpriced = next(x for x in uncapped if x["model"] == UNPRICED)
        self.assertFalse(unpriced["priced"])
        self.assertIsNone(unpriced["output_usd_per_1k"])

        cap = model_price_per_1k(MID)[1]
        capped = client.get(
            "/v1/engine/analytics/task-type/COMPLEX",
            params={"max_output_usd_per_1k": cap},
        ).json()["models"]
        self.assertEqual([x["model"] for x in capped], [MID])

    def test_tenant_isolation(self) -> None:
        # The chain writer refuses a record whose details.tenant_id is not the
        # PROCESS tenant, and since ADR-0885 step 0b that refusal fails closed
        # in the learner — so a non-default tenant is fed with the process
        # tenant patched to it (see test_learner_audit_first.py).
        with _as_process_tenant("tenant_a"):
            self._feed("SIMPLE", DEAR, [0.9] * 6, tenant_id="tenant_a")
        r = self._client("tenant_b").get("/v1/engine/analytics/task-type/SIMPLE")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["models"], [])

    def test_cap_rejects_negative(self) -> None:
        r = self._client().get(
            "/v1/engine/analytics/task-type/SIMPLE", params={"max_output_usd_per_1k": -1},
        )
        self.assertEqual(r.status_code, 422)



# ─────────────────────────────────────────────────────────────────
# ADR-0885 step 2b — GET /recent and POST /feedback over HTTP
# ─────────────────────────────────────────────────────────────────

CLASSIFIED = "skill.model_selector.classified"
CLASSIFIED_FIELDS = {
    "complexity", "confidence", "recommended_provider", "recommended_model",
    "shadow", "token_estimate", "code_blocks", "dependency_count",
}


def _seed_classified(tenant_id: str, complexity: str, model: str, confidence: float) -> None:
    """Write one REAL classified record to the tenant chain under the temp home.

    ``emit_skill_audit`` registers only its generic vocabulary for an event
    type and the chain writer DROPS every unlisted detail — the classified
    fields are registered by the shadow emitter at call time, so the fixture
    registers them first (positive control below asserts they landed)."""
    import corvin_core._bootstrap  # noqa: F401
    from forge import security_events  # type: ignore[import-not-found]

    from core.skills.skill_audit import emit_skill_audit
    security_events.register_event_allowlist(CLASSIFIED, CLASSIFIED_FIELDS)
    ok = emit_skill_audit(
        tenant_id, CLASSIFIED, tool="os.model_selector",
        details={
            "complexity": complexity, "confidence": confidence,
            "recommended_provider": None, "recommended_model": model,
            "shadow": True, "token_estimate": 120, "code_blocks": 0, "dependency_count": 0,
        },
    )
    assert ok, "seeding must reach the chain"


def _chain_records(home: str, tenant_id: str) -> list[dict]:
    import json
    p = Path(home) / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


class RecentAndFeedbackRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name
        MSO._optimizer = None

    def tearDown(self) -> None:
        MSO._optimizer = None
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _client(self, tenant_id: str = "_default", *, csrf_override: bool = True) -> TestClient:
        app = FastAPI()
        app.include_router(MSA.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record(tenant_id)
        if csrf_override:
            app.dependency_overrides[console_deps.require_csrf] = lambda: None
        return TestClient(app)

    # positive control FIRST: the seeded record carries the classified fields
    def test_seeded_record_carries_the_classified_fields(self) -> None:
        _seed_classified("_default", "medium", MID, 0.8)
        recs = [r for r in _chain_records(self._tmp.name, "_default") if r.get("event_type") == CLASSIFIED]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["details"].get("complexity"), "medium")
        self.assertEqual(recs[0]["details"].get("recommended_model"), MID)
        self.assertRegex(recs[0]["hash"], r"^[0-9a-f]{16}$")

    def test_recent_empty_on_fresh_home(self) -> None:
        r = self._client().get("/v1/engine/analytics/recent")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"tenant_id": "_default", "windowed": False, "items": []})

    def test_recent_newest_first_projection_and_limit(self) -> None:
        _seed_classified("_default", "simple", CHEAP, 0.6)
        _seed_classified("_default", "medium", MID, 0.7)
        _seed_classified("_default", "complex", DEAR, 0.9)
        r = self._client().get("/v1/engine/analytics/recent")
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()["items"]
        self.assertEqual([x["task_type"] for x in items], ["COMPLEX", "MEDIUM", "SIMPLE"])
        self.assertEqual([x["model"] for x in items], [DEAR, MID, CHEAP])
        for x in items:
            self.assertEqual(set(x), {"record_hash", "ts", "task_type", "model", "confidence"})
        self.assertGreaterEqual(items[0]["ts"], items[-1]["ts"])
        r2 = self._client().get("/v1/engine/analytics/recent", params={"limit": 2})
        self.assertEqual(len(r2.json()["items"]), 2)
        self.assertEqual(self._client().get("/v1/engine/analytics/recent", params={"limit": 0}).status_code, 422)
        self.assertEqual(self._client().get("/v1/engine/analytics/recent", params={"limit": 51}).status_code, 422)

    def test_feedback_moves_confidence_then_replay_is_409(self) -> None:
        _seed_classified("_default", "medium", MID, 0.7)
        client = self._client()
        h = client.get("/v1/engine/analytics/recent").json()["items"][0]["record_hash"]
        before = client.get("/v1/engine/analytics/task-type/MEDIUM").json()["models"]
        self.assertEqual(before, [])  # nothing learned yet
        r = client.post("/v1/engine/analytics/feedback", json={"record_hash": h, "rating": "good"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["task_type"], body["model"], body["n_samples"]), ("MEDIUM", MID, 1))
        self.assertGreater(body["confidence"], 0.5)
        after = client.get("/v1/engine/analytics/task-type/MEDIUM").json()["models"]
        self.assertEqual([(x["model"], x["n_samples"]) for x in after], [(MID, 1)])
        # the learner's fail-closed record is on the chain
        types = [x.get("event_type") for x in _chain_records(self._tmp.name, "_default")]
        self.assertIn("confidence_updated", types)
        # replay
        r2 = client.post("/v1/engine/analytics/feedback", json={"record_hash": h, "rating": "poor"})
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(client.get("/v1/engine/analytics/task-type/MEDIUM").json()["models"][0]["n_samples"], 1)

    def test_feedback_unknown_hash_404(self) -> None:
        r = self._client().post("/v1/engine/analytics/feedback",
                                json={"record_hash": "0123456789abcdef", "rating": "good"})
        self.assertEqual(r.status_code, 404)

    def test_feedback_other_tenants_hash_404(self) -> None:
        with _as_process_tenant("tenant_a"):
            _seed_classified("tenant_a", "medium", MID, 0.7)
            h = self._client("tenant_a").get("/v1/engine/analytics/recent").json()["items"][0]["record_hash"]
        with _as_process_tenant("tenant_b"):
            r = self._client("tenant_b").post("/v1/engine/analytics/feedback",
                                              json={"record_hash": h, "rating": "good"})
        self.assertEqual(r.status_code, 404)

    def test_feedback_requires_csrf(self) -> None:
        from unittest import mock
        _seed_classified("_default", "medium", MID, 0.7)
        client = self._client(csrf_override=False)
        h = client.get("/v1/engine/analytics/recent").json()["items"][0]["record_hash"]
        # require_csrf validates the session ITSELF (plain call, not Depends) and
        # only then the header — with a valid session and no x-csrf-token → 403.
        with mock.patch.object(console_deps, "require_session", lambda **_k: _fake_record()):
            r = client.post("/v1/engine/analytics/feedback", json={"record_hash": h, "rating": "good"})
        self.assertEqual(r.status_code, 403, r.text)
        # and nothing was learned
        self.assertEqual(self._client().get("/v1/engine/analytics/task-type/MEDIUM").json()["models"], [])

    def test_feedback_rejects_free_text_and_bad_rating(self) -> None:
        _seed_classified("_default", "medium", MID, 0.7)
        client = self._client()
        h = client.get("/v1/engine/analytics/recent").json()["items"][0]["record_hash"]
        r = client.post("/v1/engine/analytics/feedback",
                        json={"record_hash": h, "rating": "good", "reason": "free text"})
        self.assertEqual(r.status_code, 422)
        r = client.post("/v1/engine/analytics/feedback", json={"record_hash": h, "rating": "meh"})
        self.assertEqual(r.status_code, 422)

    def test_feedback_503_when_chain_refuses_and_learns_nothing(self) -> None:
        from unittest import mock

        import corvin_core._bootstrap  # noqa: F401
        from forge import security_events  # type: ignore[import-not-found]
        _seed_classified("_default", "medium", MID, 0.7)
        client = self._client()
        h = client.get("/v1/engine/analytics/recent").json()["items"][0]["record_hash"]

        def refuse(*_a, **_k):
            raise RuntimeError("chain refused")

        with mock.patch.object(security_events, "write_event", refuse):
            r = client.post("/v1/engine/analytics/feedback", json={"record_hash": h, "rating": "good"})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(client.get("/v1/engine/analytics/task-type/MEDIUM").json()["models"], [])
        # not marked as rated either — the operator can retry once the chain is back
        r2 = client.post("/v1/engine/analytics/feedback", json={"record_hash": h, "rating": "good"})
        self.assertEqual(r2.status_code, 200, r2.text)


if __name__ == "__main__":
    unittest.main()
