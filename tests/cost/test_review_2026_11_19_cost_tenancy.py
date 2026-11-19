"""Regression tests for the 2026-11-19 review, cost insights (TEN-5).

Drives the real router over HTTP; only the session dependency is replaced.
"""
from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.cost.guardrails import (
    BudgetConfigUnreadable,
    BudgetGuard,
    InvalidBudget,
    validate_budget_amount,
)
from core.cost.optimizer import CostOptimizer


@pytest.fixture
def client():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from corvin_console import deps
    from corvin_console.routes import cost_insights_routes as routes

    state = {"tenant": "_default"}
    app = fastapi.FastAPI()
    app.include_router(routes.router)
    rec = lambda: SimpleNamespace(tenant_id=state["tenant"], tier="owner")  # noqa: E731
    for dep in (deps.require_session, deps.require_csrf, deps.require_session_csrf_on_mutation):
        app.dependency_overrides[dep] = rec
    http = TestClient(app)
    http.as_tenant = lambda t: state.__setitem__("tenant", t)
    return http


def _budget_file(tenant_id):
    from core.paths.tenant import tenant_home
    return tenant_home(tenant_id) / "global" / "budget" / "config.json"


def test_budget_is_per_tenant(client):
    client.as_tenant("tenant-a")
    r = client.post("/cost/guardrails", json={"monthly_budget": "250"})
    assert r.status_code == 200, r.text
    assert r.json()["budget_limit"] == "250"

    client.as_tenant("tenant-b")
    assert client.get("/cost/guardrails").json()["budget_limit"] == "100"  # the default, not A's
    r = client.post("/cost/guardrails", json={"monthly_budget": "7"})
    assert r.status_code == 200

    client.as_tenant("tenant-a")
    assert client.get("/cost/guardrails").json()["budget_limit"] == "250"
    assert json.loads(_budget_file("tenant-a").read_text())["monthly_budget"] == "250"
    assert json.loads(_budget_file("tenant-b").read_text())["monthly_budget"] == "7"


def test_every_route_answers_without_a_boot_time_init(client):
    for path in ("/cost/summary", "/cost/breakdown", "/cost/by-model",
                 "/cost/recommendations", "/cost/guardrails"):
        assert client.get(path).status_code == 200, path


@pytest.mark.parametrize("body", [
    {"monthly_budget": "0"},
    {"monthly_budget": "-5"},
    {"monthly_budget": "NaN"},
    {"monthly_budget": "Infinity"},
    {"monthly_budget": "-Infinity"},
    {"monthly_budget": "sNaN"},
    {"monthly_budget": "abc"},
    {"monthly_budget": ""},
    {"monthly_budget": "100", "daily_budget": "0"},
    {"monthly_budget": "100", "daily_budget": "NaN"},
    {"monthly_budget": "100", "daily_budget": "-1"},
    {"monthly_budget": "100", "warning_threshold_pct": 0},
    {"monthly_budget": "100", "warning_threshold_pct": 95, "critical_threshold_pct": 90},
    {"monthly_budget": "1" * 40},
])
def test_invalid_budget_is_a_422_and_stores_nothing(client, body):
    client.as_tenant("tenant-v")
    client.post("/cost/guardrails", json={"monthly_budget": "50"})
    r = client.post("/cost/guardrails", json=body)
    assert r.status_code == 422, (body, r.text)
    assert json.loads(_budget_file("tenant-v").read_text())["monthly_budget"] == "50"
    assert client.get("/cost/guardrails").status_code == 200


@pytest.mark.parametrize("days", [0, -1, 100000])
def test_days_is_bounded(client, days):
    assert client.get(f"/cost/breakdown?days={days}").status_code == 422
    assert client.get(f"/cost/by-model?days={days}").status_code == 422


@pytest.mark.parametrize("bad", ["0", "-1", "NaN", "Infinity", "x", None, Decimal("0"), Decimal("NaN")])
def test_validate_budget_amount(bad):
    with pytest.raises(InvalidBudget):
        validate_budget_amount(bad)


def test_guard_refuses_bad_budgets_and_survives_a_poisoned_config(tmp_path):
    guard = BudgetGuard(tmp_path, tenant_root=tmp_path / "tenants" / "t1")
    assert guard.tenant_id == "t1"
    with pytest.raises(InvalidBudget):
        guard.set_budget(Decimal("0"))
    with pytest.raises(InvalidBudget):
        guard.set_budget(Decimal("10"), Decimal("NaN"))

    # A config file written before the validation existed.
    guard.budget_dir.mkdir(parents=True, exist_ok=True)  # a guard creates nothing by itself
    (guard.budget_dir / "config.json").write_text(
        json.dumps({"monthly_budget": "0", "daily_budget": "NaN"}), encoding="utf-8")
    poisoned = BudgetGuard(tmp_path, tenant_root=tmp_path / "tenants" / "t1")
    # No crash - and NOT the 100 EUR default presented as this tenant's limit
    # (R3-CON-13): the state is "invalid", reads refuse, the cap fails closed.
    assert poisoned.config_state == "invalid"
    with pytest.raises(BudgetConfigUnreadable):                   # no ZeroDivisionError
        poisoned.check_budget(Decimal("150"), Decimal("1"))
    allowed, reason = poisoned.enforce_spend_cap(Decimal("1"), Decimal("1"))
    assert allowed is False and "unreadable" in reason
    # Setting the budget again repairs it.
    poisoned.set_budget(Decimal("40"))
    assert poisoned.config_state == "stored"
    assert poisoned.check_budget(Decimal("60"), Decimal("1")).percentage_used == 150.0
    assert BudgetGuard(tmp_path, tenant_root=tmp_path / "tenants" / "t1").config_state == "stored"


def test_optimizer_does_not_divide_by_zero():
    opt = CostOptimizer()
    rec = opt._recommend_model_downsample(Decimal("10"), {})
    assert Decimal(rec.estimated_savings_monthly) == 0
    assert opt.generate_recommendations(Decimal("0"), 0, {}, 1.0) == []


# ───────────────── refutation round (R2-CON-7, -9) ─────────────────

def test_thresholds_are_stored_not_replaced_by_the_defaults(client):
    """The validated thresholds were dropped: set_budget hard-coded 75/90."""
    client.as_tenant("tenant-thr")
    r = client.post("/cost/guardrails", json={
        "monthly_budget": "200", "warning_threshold_pct": 40, "critical_threshold_pct": 60})
    assert r.status_code == 200, r.text
    assert (r.json()["warning_threshold_pct"], r.json()["critical_threshold_pct"]) == (40, 60)
    stored = json.loads(_budget_file("tenant-thr").read_text())
    assert (stored["warning_threshold_pct"], stored["critical_threshold_pct"]) == (40, 60)
    again = client.get("/cost/guardrails").json()
    assert (again["warning_threshold_pct"], again["critical_threshold_pct"]) == (40, 60)

    # ... and they decide the alert level.
    from core.paths.tenant import tenant_home
    guard = BudgetGuard(tenant_home("tenant-thr").parent.parent, tenant_root=tenant_home("tenant-thr"))
    assert guard.check_budget(Decimal("100"), Decimal("0"), record_alert=False).alert_level.value == "warning"
    assert guard.check_budget(Decimal("130"), Decimal("0"), record_alert=False).alert_level.value == "critical"
    assert guard.check_budget(Decimal("70"), Decimal("0"), record_alert=False).alert_active is False


def test_guard_validates_thresholds_itself(tmp_path):
    guard = BudgetGuard(tmp_path, tenant_root=tmp_path / "tenants" / "t1")
    for warn, crit in ((0, 90), (101, 101), (95, 90), (float("nan"), 90), ("x", 90)):
        with pytest.raises(InvalidBudget):
            guard.set_budget(Decimal("10"), warning_threshold_pct=warn, critical_threshold_pct=crit)
    assert not guard.budget_dir.exists()


def test_a_get_creates_nothing_on_disk(client):
    from core.paths.tenant import tenant_home
    client.as_tenant("tenant-readonly")
    root = tenant_home("tenant-readonly")
    assert not root.exists()
    for path in ("/cost/summary", "/cost/breakdown", "/cost/by-model",
                 "/cost/recommendations", "/cost/guardrails"):
        assert client.get(path).status_code == 200, path
    assert not root.exists(), sorted(str(p) for p in root.rglob("*"))
    # positive control: the POST is what creates the budget file
    assert client.post("/cost/guardrails", json={"monthly_budget": "5"}).status_code == 200
    assert _budget_file("tenant-readonly").exists()


def test_a_polled_status_read_records_no_alert(client):
    from core.paths.tenant import tenant_home
    from datetime import datetime, timezone
    client.as_tenant("tenant-poll")
    root = tenant_home("tenant-poll")
    costs = root / "global" / "costs"
    costs.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    (costs / f"calls_{now.date().isoformat()}.jsonl").write_text(
        json.dumps({"timestamp": now.isoformat(), "total_cost": "900", "model_id": "m"}) + "\n")
    assert client.post("/cost/guardrails", json={"monthly_budget": "10"}).status_code == 200
    for _ in range(3):
        body = client.get("/cost/guardrails").json()
        assert body["alert_active"] is True and body["alert_level"] == "exceeded"
    assert not (root / "global" / "budget" / "alerts.jsonl").exists()


def test_trend_reads_each_cost_file_once_and_only_inside_the_window(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from core.cost import calculator as calc_mod

    calc = calc_mod.CostCalculator(tmp_path, tenant_root=tmp_path / "tenants" / "t1")
    calc.costs_dir.mkdir(parents=True)
    today = datetime.now(timezone.utc).date()
    expected = {}
    for back in range(0, 400, 1):
        day = today - timedelta(days=back)
        ts = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).isoformat()
        (calc.costs_dir / f"calls_{day.isoformat()}.jsonl").write_text(
            json.dumps({"timestamp": ts, "total_cost": "1.5", "model_id": "m"}) + "\n"
            + json.dumps({"timestamp": ts, "total_cost": "0.5", "model_id": "m"}) + "\n")
        expected[day.isoformat()] = "2.0"

    opened = []
    real_open = open

    def counting_open(path, *a, **k):
        if str(path).endswith(".jsonl"):
            opened.append(str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr(calc_mod, "open", counting_open, raising=False)

    trend = calc.get_daily_costs_trend(days=30)
    assert [d["date"] for d in trend] == sorted(d["date"] for d in trend)
    assert len(trend) == 30 and trend[-1]["date"] == today.isoformat()
    assert all(d["cost"] == expected[d["date"]] for d in trend), trend[:3]
    # 30 days + one day of slack on each side, each file once. It was 30 x 400.
    assert len(opened) == len(set(opened)) <= 32, len(opened)

    opened.clear()
    assert calc.get_daily_cost(today.isoformat()) == Decimal("2.0")
    assert len(opened) <= 3
    assert len(calc.get_daily_costs_trend(days=10**6)) == calc.MAX_TREND_DAYS


@pytest.mark.parametrize("tenant", ["test", "local", "admin", "system", "internal", "service", "operator"])
def test_tenants_the_canonical_resolver_admits_are_served(client, tenant):
    from forge.tenants import validate_tenant_id
    assert validate_tenant_id(tenant) == tenant         # positive control
    client.as_tenant(tenant)
    assert client.get("/cost/guardrails").status_code == 200


@pytest.mark.parametrize("tenant", ["..", "a/b", "Acme", "__x"])
def test_tenants_the_canonical_resolver_refuses_stay_refused(client, tenant):
    client.as_tenant(tenant)
    assert client.get("/cost/guardrails").status_code == 403


# ───────────────── third round (R3-CON-13): measured or absent ─────────────────

def _costs_dir(tenant_id):
    from core.paths.tenant import tenant_home
    d = tenant_home(tenant_id) / "global" / "costs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _call(model, cost, *, hours_ago=1, input_tokens=100, cache_read_tokens=0):
    from datetime import datetime, timedelta, timezone
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return json.dumps({
        "call_id": "c", "timestamp": ts.isoformat(), "tenant_id": "t", "model_id": model,
        "input_tokens": input_tokens, "output_tokens": 10, "input_cost": "0", "output_cost": "0",
        "total_cost": cost, "cache_read_tokens": cache_read_tokens,
    })


def _today_file(tenant_id):
    from datetime import datetime, timezone
    return _costs_dir(tenant_id) / f"calls_{datetime.now(timezone.utc).date().isoformat()}.jsonl"


def test_breakdown_reports_only_what_is_measured(client):
    client.as_tenant("tenant-bd")
    _today_file("tenant-bd").write_text(
        _call("claude-sonnet-5", "3.00") + "\n" + _call("claude-opus-5", "1.50") + "\n", encoding="utf-8")
    body = client.get("/cost/breakdown").json()
    assert Decimal(body["llm_cost"]) == Decimal("4.50")
    # No invented 20 % / 5 % shares: compute used to read 1.20, storage 0.30.
    assert body["compute_cost"] is None and body["storage_cost"] is None
    assert body["total_cost"] is None
    assert body["llm_pct"] is None and body["compute_pct"] is None and body["storage_pct"] is None
    assert body["components_available"] == {"llm": True, "compute": False, "storage": False}
    assert "not available" in body["note"]


def test_recommendations_are_withheld_without_measured_calls(client):
    client.as_tenant("tenant-rec0")
    body = client.get("/cost/recommendations").json()
    # Used to be three recommendations with savings, for a tenant with no call at all.
    assert body["available"] is False and body["recommendations"] == []
    assert body["total_potential_savings"] is None
    assert body["inputs"]["calls"] == 0 and body["inputs"]["cache_hit_rate"] is None
    assert "Not enough measured calls" in body["note"]


def test_recommendations_use_the_measured_distribution(client):
    from corvin_console.routes import cost_insights_routes as routes
    client.as_tenant("tenant-rec1")
    n = routes.MIN_CALLS_FOR_RECOMMENDATIONS * 20
    lines = [_call("claude-haiku-4-5", "0.01", input_tokens=100, cache_read_tokens=900) for _ in range(n)]
    _today_file("tenant-rec1").write_text("\n".join(lines) + "\n", encoding="utf-8")
    body = client.get("/cost/recommendations").json()
    assert body["available"] is True
    assert body["inputs"]["calls"] == n
    assert body["inputs"]["calls_by_model"] == {"claude-haiku-4-5": n}
    assert body["inputs"]["cache_hit_rate"] == pytest.approx(0.9)
    kinds = {r["optimization_type"] for r in body["recommendations"]}
    # No Opus call was measured and the cache already hits 90 %: the invented
    # 500-Opus / 35 %-cache inputs produced exactly these three for everyone.
    assert kinds.isdisjoint({"lora", "model_downsample", "prompt_cache"}), kinds


def test_unknown_cache_rate_gives_no_cache_advice():
    opt = CostOptimizer()
    recs = opt.generate_recommendations(Decimal("10"), 10, {"claude-sonnet-5": 10}, None)
    assert all(r.optimization_type != "prompt_cache" for r in recs)
    assert any(r.optimization_type == "prompt_cache"
               for r in opt.generate_recommendations(Decimal("10"), 10, {"claude-sonnet-5": 10}, 0.1))


def test_no_invented_constant_is_left_in_the_cost_routes():
    import inspect
    from corvin_console.routes import cost_insights_routes as routes
    src = inspect.getsource(routes)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    for needle in ("daily_call_count = 1000", "cache_hit_rate = 0.35", '"claude-opus-5": 500',
                   'Decimal("0.20")', 'Decimal("0.05")', 'Decimal("0.75")'):
        assert needle not in code, needle
    assert "usage_stats(" in code                      # positive control: the measured path


def test_a_broken_budget_file_is_reported_not_replaced_by_the_default(client):
    client.as_tenant("tenant-broken")
    f = _budget_file("tenant-broken")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{not json", encoding="utf-8")
    body = client.get("/cost/guardrails").json()
    assert body["available"] is False and body["config_state"] == "invalid"
    assert body["budget_limit"] is None and body["daily_limit"] is None      # was "100" / "3.33"
    assert body["percentage_used"] is None and "unreadable" in body["detail"]
    assert f.read_text(encoding="utf-8") == "{not json", "a read repairs nothing"
    # The operator sets it again: stored, available.
    r = client.post("/cost/guardrails", json={"monthly_budget": "40"})
    assert r.status_code == 200, r.text
    fixed = client.get("/cost/guardrails").json()
    assert fixed["available"] is True and fixed["config_state"] == "stored"
    assert Decimal(fixed["budget_limit"]) == Decimal("40")


def test_a_tenant_without_a_stored_budget_is_told_it_is_the_default(client):
    client.as_tenant("tenant-nobudget")
    body = client.get("/cost/guardrails").json()
    assert body["available"] is True and body["config_state"] == "default"


@pytest.mark.parametrize("bad", [
    "{cut off",                                           # crash mid-write
    json.dumps({"timestamp": "not a date", "model_id": "m", "total_cost": "1"}),
    json.dumps({"timestamp": "2026-11-19T10:00:00+00:00", "model_id": "m", "total_cost": "NaN"}),
    json.dumps({"timestamp": "2026-11-19T10:00:00+00:00", "model_id": "m"}),
    json.dumps({"timestamp": "2026-11-19T10:00:00+00:00", "total_cost": "1"}),
    json.dumps(["a", "list"]),
    "\x00\x01 binary",
])
def test_one_defective_cost_line_does_not_take_the_routes_down(client, bad):
    client.as_tenant("tenant-badline")
    _today_file("tenant-badline").write_text(
        _call("claude-sonnet-5", "2.00") + "\n" + bad + "\n" + _call("claude-sonnet-5", "3.00") + "\n",
        encoding="utf-8")
    for path in ("/cost/summary", "/cost/breakdown", "/cost/by-model", "/cost/recommendations",
                 "/cost/guardrails"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text)     # every one was a 500
        assert r.json()["skipped_records"] >= 1, path
    assert Decimal(client.get("/cost/summary").json()["today_spend"]) == Decimal("5.00")
    assert client.get("/cost/by-model").json()["models"] == {"claude-sonnet-5": "5.00"}


def test_a_timestamp_without_an_offset_is_read_as_utc(client):
    from datetime import datetime, timezone
    client.as_tenant("tenant-naive")
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    _today_file("tenant-naive").write_text(json.dumps(
        {"timestamp": naive, "model_id": "m", "total_cost": "1.25"}) + "\n", encoding="utf-8")
    r = client.get("/cost/by-model")                       # was a TypeError -> 500
    assert r.status_code == 200 and r.json() == {"models": {"m": "1.25"}, "skipped_records": 0}
