"""SRF-14: /engine/analytics/model/{id} must not invent sample timestamps.

The optimizer stores confidence values only; the route used to fabricate
``now - n minutes`` per sample (ADR-0763: the console fabricates nothing).
Driven over HTTP through the real route with a stubbed optimizer.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Opt:
    def __init__(self, history):
        self._confidence_history = {("coding", "m1", "_default"): history}

    def get_stats(self, task_type, model, tenant_id):
        return None


def test_history_samples_carry_position_not_invented_time(monkeypatch):
    from corvin_console import deps
    from corvin_console.routes import model_selection_analytics as msa

    monkeypatch.setattr(msa, "get_optimizer", lambda: _Opt([0.1, 0.2, 0.3, 0.4]))
    app = FastAPI()
    app.include_router(msa.router)
    app.dependency_overrides[deps.require_session] = lambda: SimpleNamespace(
        tenant_id="_default", sid_fingerprint="fp", tier="owner")
    r = TestClient(app).get("/v1/engine/analytics/model/m1",
                            params={"task_type": "coding", "limit": 2})
    assert r.status_code == 200, r.text
    samples = r.json()["samples"]
    assert samples == [{"index": 2, "confidence": 0.3}, {"index": 3, "confidence": 0.4}]
    assert all("timestamp" not in s for s in samples)
