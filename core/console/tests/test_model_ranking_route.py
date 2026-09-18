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
        self._feed("SIMPLE", DEAR, [0.9] * 6, tenant_id="tenant_a")
        r = self._client("tenant_b").get("/v1/engine/analytics/task-type/SIMPLE")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["models"], [])

    def test_cap_rejects_negative(self) -> None:
        r = self._client().get(
            "/v1/engine/analytics/task-type/SIMPLE", params={"max_output_usd_per_1k": -1},
        )
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
