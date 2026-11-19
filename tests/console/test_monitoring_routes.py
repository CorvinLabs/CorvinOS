"""Tests for the Production Monitoring routes (ADR-0906).

The routes have no data source on this build. They used to return invented
health scores, skills, alerts and trend lines; these tests pin the honest
contract instead: an empty result plus ``available: false`` — and they fail if
any of the old sample values comes back.

Every request goes through the real router (FastAPI TestClient), with only the
session dependency overridden.
"""
import ast
import inspect
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from corvin_console.deps import require_session
from corvin_console.routes import monitoring_routes

PREFIX = "/v1/console/monitoring"

ENDPOINTS = [
    "/health",
    "/autonomous-stats",
    "/skill-performance",
    "/alerts",
    "/metrics/timeseries?metric=confidence&days=7",
]

# Literals the fabricated responses carried. None may appear in any answer.
FABRICATED = [
    "87.5", "168.25", "os.delegation_router", "os.context_adapter",
    "os.workflow_optimizer", "alert-2026", "Optimizer convergence",
    "Context snapshot timeout", "4250",
]


def _client(tenant_id: str = "_default") -> TestClient:
    class _Rec:
        sid_fingerprint = "test-op"

    _Rec.tenant_id = tenant_id
    app = FastAPI()
    app.include_router(monitoring_routes.router, prefix=PREFIX)
    app.dependency_overrides[require_session] = lambda: _Rec()
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return _client()


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_endpoint_says_unavailable(client, path):
    r = client.get(PREFIX + path)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["reason"] == monitoring_routes.UNAVAILABLE_REASON
    assert body["tenant_id"] == "_default"
    assert body["timestamp"].endswith("Z")


@pytest.mark.parametrize("path", ENDPOINTS)
def test_no_fabricated_value_is_served(client, path):
    raw = client.get(PREFIX + path).text
    for needle in FABRICATED:
        assert needle not in raw, f"{path} still serves sample data: {needle!r}"


def test_health_carries_no_score(client):
    body = client.get(PREFIX + "/health").json()
    assert body["health_score"] is None
    assert body["uptime_hours"] is None
    assert body["last_check"] is None
    assert body["status"] == "unknown"


def test_autonomous_stats_are_all_null(client):
    body = client.get(PREFIX + "/autonomous-stats").json()
    for key in ("skills_forged", "skills_approved", "skills_deferred",
                "skills_active", "success_rate", "avg_optimization_confidence",
                "avg_latency_ms", "avg_error_rate", "last_fork_timestamp"):
        assert key in body and body[key] is None, key


def test_skill_performance_is_empty(client):
    assert client.get(PREFIX + "/skill-performance").json()["skills"] == []


def test_alerts_are_empty(client):
    body = client.get(PREFIX + "/alerts").json()
    assert body["active_alerts"] == []
    assert body["recent_alerts"] == []
    assert body["total_active"] == 0


@pytest.mark.parametrize("metric", ["confidence", "latency_p95_ms", "error_rate"])
def test_timeseries_has_no_points(client, metric):
    body = client.get(f"{PREFIX}/metrics/timeseries?metric={metric}&days=3").json()
    assert body["metric_name"] == metric
    assert body["data_points"] == []
    assert body["window_start"] < body["window_end"]


def test_timeseries_still_validates_its_parameters(client):
    assert client.get(PREFIX + "/metrics/timeseries?metric=nope").status_code == 400
    assert client.get(PREFIX + "/metrics/timeseries").status_code == 422
    assert client.get(
        PREFIX + "/metrics/timeseries?metric=confidence&days=0").status_code == 422
    assert client.get(
        PREFIX + "/metrics/timeseries?metric=confidence&days=31").status_code == 422


def test_tenant_comes_from_the_session():
    body = _client("tenant-2").get(PREFIX + "/alerts").json()
    assert body["tenant_id"] == "tenant-2"


def test_routes_require_a_session():
    app = FastAPI()
    app.include_router(monitoring_routes.router, prefix=PREFIX)
    with TestClient(app) as c:
        for path in ENDPOINTS:
            assert c.get(PREFIX + path).status_code in (401, 403), path


def test_module_holds_no_numeric_sample_literals():
    """Structural guard: outside the documented constants the module contains
    no float literal at all — a fabricated metric needs one."""
    tree = ast.parse(inspect.getsource(monitoring_routes))
    floats = [n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    assert floats == []
    assert not [n for n in dir(monitoring_routes) if n.startswith("_generate_")]


def test_responses_are_plain_json(client):
    for path in ENDPOINTS:
        json.loads(client.get(PREFIX + path).text)
