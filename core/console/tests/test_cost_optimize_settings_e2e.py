"""S5 of PLAN-0955 / ADR-2259: ``GET/PUT /v1/console/settings/cost-optimize``.

Real HTTP through the real router (TestClient), real files under a sandboxed
CORVIN_HOME, the real reader (``savings_cap``) on the other side of the file.
Session/CSRF dependencies are overridden ONLY in the app used for behaviour; a
second app WITHOUT overrides proves the route is not reachable unauthenticated.
"""
from __future__ import annotations

import dataclasses
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO / "core" / "console"), str(_REPO / "corvin_operator" / "bridges" / "shared"),
           str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import settings as settings_route  # noqa: E402

URL = "/v1/console/settings/cost-optimize"
HAIKU = "claude-haiku-4-5-20251001"


def _record(tenant_id: str = "_default") -> session_auth.SessionRecord:
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = 1_000_000.0 + (3600 if f.name == "expires_at" else 0)
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


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    import savings_cap
    from core.learning import savings_meter
    savings_meter.reset_cache()
    monkeypatch.setattr(savings_meter, "_epoch_ts", lambda tenant_id: 0.0)
    monkeypatch.setattr(savings_cap, "_tier", lambda: "free")
    yield tmp_path
    savings_meter.reset_cache()


def _client(tenant: str = "_default", *, overrides: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(settings_route.router, prefix="/v1/console")
    if overrides:
        app.dependency_overrides[console_deps.require_session] = lambda: _record(tenant)
        app.dependency_overrides[console_deps.require_csrf] = lambda: _record(tenant)
    return TestClient(app)


def _file(home: Path, tenant: str = "_default") -> Path:
    return home / "tenants" / tenant / "global" / "cost_optimize.json"


def test_get_default_reports_ceiling_effective_and_tier(home):
    body = _client().get(URL).json()
    assert body["tier"] == "free"
    assert body["choice"] == {"cap_ratio": None, "floor_usd": None}
    assert body["ceiling"] == {"ratio": 0.10, "floor_usd": 5.0}
    assert body["effective"] == {"ratio": 0.10, "floor_usd": 5.0}
    assert body["clamped"] is False
    assert body["meta"]["cap_ratio"] == {"min": 0.0, "max": 1.0}
    assert body["state"]["capped"] is False


def test_put_lower_value_is_stored_0600_and_read_back_by_the_real_reader(home):
    r = _client().put(URL, json={"cap_ratio": 0.05})
    assert r.status_code == 200, r.text
    assert r.json()["effective"]["ratio"] == 0.05
    f = _file(home)
    assert json.loads(f.read_text()) == {"cap_ratio": 0.05}
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    import savings_cap
    assert savings_cap.resolve("_default").effective_ratio == 0.05      # the other side of the file


def test_free_cannot_raise_the_ceiling_and_is_told_so(home):
    r = _client().put(URL, json={"cap_ratio": 0.5})
    assert r.status_code == 200
    body = r.json()
    assert body["choice"]["cap_ratio"] == 0.5
    assert body["effective"]["ratio"] == 0.10
    assert body["clamped"] is True
    import savings_cap
    assert savings_cap.resolve("_default").effective_ratio == 0.10


def test_zero_switches_the_automation_off(home):
    body = _client().put(URL, json={"cap_ratio": 0.0}).json()
    assert body["effective"]["ratio"] == 0.0 and body["state"]["reason"] == "automation_off"
    assert body["state"]["capped"] is True


def test_null_returns_to_the_tier_default(home):
    c = _client()
    c.put(URL, json={"cap_ratio": 0.02})
    body = c.put(URL, json={"cap_ratio": None}).json()
    assert body["choice"]["cap_ratio"] is None and body["effective"]["ratio"] == 0.10


def test_put_merges_keys_instead_of_replacing(home):
    c = _client()
    c.put(URL, json={"cap_ratio": 0.05})
    c.put(URL, json={"floor_usd": 1.0})
    assert json.loads(_file(home).read_text()) == {"cap_ratio": 0.05, "floor_usd": 1.0}


@pytest.mark.parametrize("raw", [
    '{"cap_ratio": NaN}', '{"cap_ratio": Infinity}', '{"cap_ratio": -0.1}', '{"cap_ratio": 1.1}',
    '{"cap_ratio": true}', '{"cap_ratio": "0.5"}', '{"cap_ratio": [0.1]}',
    '{"floor_usd": -5}', '{"floor_usd": 1e999}', '{"unknown": 1}', '{}',
])
def test_hostile_or_empty_bodies_are_rejected_and_write_nothing(home, raw):
    r = _client().put(URL, content=raw, headers={"content-type": "application/json"})
    assert r.status_code in (400, 422), (raw, r.status_code, r.text)
    assert not _file(home).exists()


def test_member_may_set_any_value_and_null_means_unlimited(home, monkeypatch):
    import savings_cap
    monkeypatch.setattr(savings_cap, "_tier", lambda: "member")
    c = _client()
    body = c.put(URL, json={"cap_ratio": 0.5}).json()
    assert body["tier"] == "member" and body["effective"]["ratio"] == 0.5 and body["clamped"] is False
    body = c.put(URL, json={"cap_ratio": None}).json()
    assert body["effective"]["ratio"] is None and body["state"]["reason"] == "tier_unlimited"


def test_get_reports_the_measured_state(home):
    from core.paths.tenant import tenant_audit_chain
    chain = tenant_audit_chain("_default")
    chain.parent.mkdir(parents=True, exist_ok=True)
    with chain.open("a") as fh:
        for n in range(10):
            fh.write(json.dumps({"ts": time.time(), "event_type": "os_turn.completed", "details": {
                "turn_id": f"t{n}", "model": HAIKU, "exit_code": 0,
                "input_tokens": 1000, "output_tokens": 1000}}) + "\n")
    c = _client()
    c.put(URL, json={"floor_usd": 0.01})
    deadline = time.time() + 5
    body = c.get(URL).json()
    while body["state"]["reason"] == "meter_warming" and time.time() < deadline:
        time.sleep(0.05)
        body = c.get(URL).json()
    assert body["state"]["capped"] is True and body["state"]["reason"] == "cap_reached"
    m = body["measured"]
    assert m["counted_turns"] == 10 and m["ratio"] == pytest.approx(0.5)
    assert m["window"] == {"unit": "utc_month", "month": m["month"]}


def test_the_session_tenant_is_the_only_tenant_used(home):
    _client("tenant-b").put(URL, json={"cap_ratio": 0.03})
    assert _file(home, "tenant-b").exists()
    assert not _file(home, "_default").exists()


def test_unauthenticated_requests_never_reach_the_handler(home):
    c = _client(overrides=False)
    assert c.get(URL).status_code in (401, 403)
    assert c.put(URL, json={"cap_ratio": 0.05}).status_code in (401, 403)
    assert not _file(home).exists()


def test_there_is_no_reset_or_quota_endpoint_for_the_allowance(home):
    paths = {getattr(r, "path", "") for r in settings_route.router.routes}
    assert not [p for p in paths if "cost-optimize" in p and ("reset" in p or "quota" in p)]
    c = _client()
    for suffix in ("/reset", "/quota/reset"):
        assert c.post(URL + suffix).status_code in (404, 405)
        assert c.delete(URL + suffix).status_code in (404, 405)


def test_a_config_change_is_audited_with_what_was_asked_and_what_is_in_force(home):
    from core.paths.tenant import tenant_audit_chain
    c = _client()
    c.put(URL, json={"cap_ratio": 0.5})                      # Free: asked 0.5, in force 0.10
    chain = tenant_audit_chain("_default")
    recs = [json.loads(l) for l in chain.read_text().splitlines()
            if '"cost.optimize_config_changed"' in l]
    recs = [r for r in recs if r.get("event_type") == "cost.optimize_config_changed"]
    assert len(recs) == 1, recs
    d = recs[0]["details"]
    assert d["cap_ratio_after"] == 0.5 and d["effective_ratio"] == 0.10 and d["clamped"] is True
    assert d["tier"] == "free" and d["tenant_id"] == "_default" and "cap_ratio_before" not in d
    assert recs[0].get("hash") and "prev_hash" in recs[0]
