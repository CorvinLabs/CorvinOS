"""The worker cost series must never render an unmeasured day as $0.00.

Live finding 2026-09-19 (Models console, Usage & Cost tab, "Worker runs"):
the daily chart drew 0.0 for 2026-09-16, -17 and -18 while the OS series had
data on every one of those days. Root cause by layer:

1. The status route fills ``acs_actual_usd``/``acs_baseline_usd`` with ``0.0``
   for every date the OS series has and the worker series does not. Its own
   comment says "an unmeasured day is absent, never a zero" — it applied that
   to the DATE list only, not to each series on a date.
2. ``_read_worker_spans`` drops a worker span that carries a ``model_id`` but
   no token counts, so such a run is not even an UNPRICED turn: the day reads
   as "no worker runs" instead of "N runs, none priced".
3. The static ``engine.span.end`` allowlist in ``security_events`` predates
   ADR-0759 and lacks the four token fields; the union performed by
   ``engine_span._register_allowlists()`` is best-effort (import order). 25
   of 40 worker spans on 2026-09-18 landed with ``_dropped_fields`` naming all
   four — a span priced at nothing by the audit floor, not by the engine.

Each layer has its own test below; each was RED before the fix.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[3]
for _p in (
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "corvin_operator" / "forge",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import engine_span as ESPAN  # noqa: E402

from core.console.corvin_console import auth as session_auth  # noqa: E402
from core.console.corvin_console import deps as console_deps  # noqa: E402
from core.learning import model_selection_learner as MSL  # noqa: E402


def _fake_session_record(tenant_id: str) -> session_auth.SessionRecord:
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


# ── 1. the route: per-SERIES absence, not per-date ───────────────────


class TestStatusRouteNeverZeroFillsASeries:
    @pytest.fixture
    def client(self):
        from core.console.corvin_console.routes import model_cost_optimizer_api as api

        app = FastAPI()
        app.include_router(api.router, prefix="/v1/console")
        app.dependency_overrides[console_deps.require_session] = (
            lambda: _fake_session_record("_default")
        )
        app.dependency_overrides[console_deps.require_csrf] = lambda: None
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()

    @pytest.fixture
    def lopsided(self, monkeypatch):
        """OS turns on four days; worker runs priced on ONE of them, recorded
        but unpriced on another, absent on the other two — the live shape."""

        def _crafted(tenant_id, chain_path=None):
            return MSL.CostEfficiencyResult(
                has_data=True,
                daily=[
                    MSL.CostDayPoint(date="2026-09-15", actual_usd=14.3, baseline_usd=71.5,
                                     counted_turns=67, total_turns=70),
                    MSL.CostDayPoint(date="2026-09-16", actual_usd=22.7, baseline_usd=113.7,
                                     counted_turns=141, total_turns=142),
                    MSL.CostDayPoint(date="2026-09-17", actual_usd=11.1, baseline_usd=55.5,
                                     counted_turns=79, total_turns=79),
                    MSL.CostDayPoint(date="2026-09-18", actual_usd=14.6, baseline_usd=73.0,
                                     counted_turns=84, total_turns=87),
                ],
                total_actual_usd=62.7, total_baseline_usd=313.7, savings_percent=80.0,
                model_mix={"claude-haiku-4-5-20251001": 371},
                acs_daily=[
                    MSL.CostDayPoint(date="2026-09-15", actual_usd=1.7939, baseline_usd=3.1044,
                                     counted_turns=9, total_turns=9),
                    # recorded, none priced (model known, tokens missing)
                    MSL.CostDayPoint(date="2026-09-17", actual_usd=0.0, baseline_usd=0.0,
                                     counted_turns=0, total_turns=3),
                ],
                acs_total_actual_usd=1.7939, acs_total_baseline_usd=3.1044,
                acs_model_mix={"claude-opus-5": 3, "claude-sonnet-5": 4,
                               "claude-haiku-4-5-20251001": 2},
            )

        monkeypatch.setattr(MSL, "compute_cost_efficiency", _crafted)

    def test_a_day_without_worker_runs_carries_null_not_zero(self, client, lopsided):
        body = client.get("/v1/console/learning/model-cost-optimizer/status").json()
        rows = {r["date"]: r for r in body["cost_history"]}
        assert sorted(rows) == ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
        for day in ("2026-09-16", "2026-09-18"):
            assert rows[day]["acs_actual_usd"] is None, (day, rows[day])
            assert rows[day]["acs_baseline_usd"] is None, (day, rows[day])
            assert rows[day]["acs_counted_turns"] == 0
            assert rows[day]["acs_total_turns"] == 0
        # the priced day keeps its real dollars
        assert rows["2026-09-15"]["acs_actual_usd"] == pytest.approx(1.7939)
        assert rows["2026-09-15"]["acs_baseline_usd"] == pytest.approx(3.1044)

    def test_a_recorded_but_unpriced_day_is_null_with_its_count(self, client, lopsided):
        body = client.get("/v1/console/learning/model-cost-optimizer/status").json()
        row = next(r for r in body["cost_history"] if r["date"] == "2026-09-17")
        assert row["acs_actual_usd"] is None
        assert row["acs_baseline_usd"] is None
        assert row["acs_counted_turns"] == 0
        assert row["acs_total_turns"] == 3  # the reader can say "3 runs, none priced"
        # and the totals count it as seen, never as priced
        assert body["acs_total_turns"] == 12
        assert body["acs_counted_turns"] == 9

    def test_the_os_series_gets_the_same_treatment(self, client, monkeypatch):
        def _worker_only_day(tenant_id, chain_path=None):
            return MSL.CostEfficiencyResult(
                has_data=True,
                daily=[MSL.CostDayPoint(date="2026-09-15", actual_usd=1.0, baseline_usd=5.0,
                                        counted_turns=10, total_turns=10)],
                total_actual_usd=1.0, total_baseline_usd=5.0, savings_percent=80.0,
                model_mix={"claude-haiku-4-5-20251001": 10},
                acs_daily=[MSL.CostDayPoint(date="2026-09-16", actual_usd=2.0, baseline_usd=3.0,
                                            counted_turns=1, total_turns=1)],
                acs_total_actual_usd=2.0, acs_total_baseline_usd=3.0,
                acs_model_mix={"claude-sonnet-5": 1},
            )

        monkeypatch.setattr(MSL, "compute_cost_efficiency", _worker_only_day)
        body = client.get("/v1/console/learning/model-cost-optimizer/status").json()
        row = next(r for r in body["cost_history"] if r["date"] == "2026-09-16")
        assert row["actual_usd"] is None and row["baseline_usd"] is None
        assert row["counted_turns"] == 0 and row["total_turns"] == 0
        assert row["acs_actual_usd"] == pytest.approx(2.0)


# ── 2. the reader: a span with a model but no tokens is an UNPRICED turn ──


def _end(span_id: str, *, model: str, ts: float, tokens=(0, 0, 0, 0)) -> dict:
    in_t, out_t, cr_t, cw_t = tokens
    return {
        "ts": ts, "event_type": "engine.span.end",
        "details": ESPAN.end_details(
            span_id=span_id, role="worker", engine_id="claude_code", model_id=model,
            status="ok", duration_ms=1000, input_tokens=in_t, output_tokens=out_t,
            cache_read_tokens=cr_t, cache_write_tokens=cw_t),
    }


class TestWorkerSpanReader:
    def test_model_without_tokens_counts_as_total_not_as_priced(self, tmp_path):
        chain = tmp_path / "audit.jsonl"
        rows = [
            _end("priced", model="claude-sonnet-5", ts=1_800_000_000.0, tokens=(100, 50, 0, 0)),
            _end("unpriced", model="claude-sonnet-5", ts=1_800_000_010.0),
            # neither a model nor tokens: not attributable to any model turn
            # (a stub engine, an aborted spawn) — excluded, as before
            _end("nothing", model="", ts=1_800_000_020.0),
        ]
        chain.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        spans = MSL._read_worker_spans(chain, 10_000_000)
        by_id = {s["span_id"]: s for s in spans}
        assert set(by_id) == {"priced", "unpriced"}
        assert by_id["unpriced"]["input_tokens"] == 0
        assert by_id["unpriced"]["model"] == "claude-sonnet-5"

    def test_two_unpriced_spans_in_one_second_are_two_turns(self, tmp_path, monkeypatch):
        """The dedupe key used to be (ts, model, four zero token counts) — two
        unpriced spans from the same second collapsed into one."""
        chain = tmp_path / "audit.jsonl"
        rows = [
            _end("a", model="claude-sonnet-5", ts=1_800_000_000.0),
            _end("b", model="claude-sonnet-5", ts=1_800_000_000.0),
        ]
        chain.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        monkeypatch.setattr(MSL, "_acs_chain_paths", lambda tid: [tmp_path / "none.jsonl", chain])
        monkeypatch.setattr(MSL, "_read_completed_turns", lambda *a, **k: [])
        res = MSL.compute_cost_efficiency("_default", chain_path=chain)
        assert [(p.total_turns, p.counted_turns) for p in res.acs_daily] == [(2, 0)]
        assert res.acs_total_actual_usd == 0.0


# ── 3. the audit floor: the static allowlist carries every END field ──


def test_static_span_allowlist_is_a_superset_of_end_fields():
    """In a fresh interpreter that imports the audit floor WITHOUT engine_span
    (so ``_register_allowlists()`` never ran) the static set must already hold
    every END/START field. In-process the union has always happened by the
    time a test looks, which is why this was never caught."""
    import subprocess

    code = (
        "import sys, json; sys.path.insert(0, %r); "
        "from forge import security_events as sec; "
        "assert 'engine_span' not in sys.modules; "
        "print(json.dumps({k: sorted(sec._EVENT_ALLOWLIST[k]) for k in "
        "('engine.span.start', 'engine.span.end')}))"
    ) % str(_REPO / "corvin_operator" / "forge")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True, cwd=str(_REPO))
    static = json.loads(out.stdout.strip().splitlines()[-1])
    missing_end = sorted(ESPAN.END_FIELDS - set(static["engine.span.end"]))
    assert not missing_end, (
        "engine.span.end fields missing from the STATIC allowlist "
        f"(import-order dependent registration is not a floor): {missing_end}"
    )
    assert not (ESPAN.START_FIELDS - set(static["engine.span.start"]))
