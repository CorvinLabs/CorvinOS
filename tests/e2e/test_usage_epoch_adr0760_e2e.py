"""Counting epoch for the usage + cost panels — ADR-0760.

The operator asked for the OS and worker series to start from the same point.
The tempting implementation is to trim the audit chain, and this suite exists
largely to make that permanently non-viable: every test below holds only if the
chain is untouched and one timestamp does the work.

Three properties are load-bearing and each has its own class:

1. **Nothing is deleted.** Clearing the epoch restores every historical turn.
   A "reset" that cannot be undone is a deletion wearing a nicer name.
2. **One epoch, both readers.** ``model_usage`` (turns) and
   ``compute_cost_efficiency`` (dollars) narrow by the SAME stored value. Two
   numbers on one screen counted over different windows is worse than no reset.
3. **The window travels with the numbers.** Every payload carrying a narrowed
   total also carries the period it covers, so a caller cannot render one
   without having been handed the other.

Plus the arithmetic the panel now shows for the worker half, which must be
derived from real dollar totals and never from averaging two percentages.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[2]
for _p in (
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "corvin_operator" / "forge",
    _REPO / "core" / "gateway",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import engine_span as ESPAN  # noqa: E402

from core.console.corvin_console import auth as session_auth  # noqa: E402
from core.console.corvin_console import deps as console_deps  # noqa: E402
from core.console.corvin_console import model_usage as MU  # noqa: E402
from core.console.corvin_console import usage_epoch as UE  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────


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


#: Two eras either side of the epoch used throughout.
_OLD = 1_800_000_000.0
_NEW = 1_800_100_000.0
_EPOCH = 1_800_050_000.0


def _span(span_id: str, *, role: str, model: str, ts: float,
          tokens: tuple[int, int, int, int]) -> list[dict]:
    """A complete start/end pair — the unit _collect folds on."""
    in_t, out_t, cr_t, cw_t = tokens
    return [
        {"ts": ts, "event_type": "engine.span.start",
         "details": ESPAN.start_details(span_id=span_id, role=role,
                                        engine_id="claude_code", model_id=model)},
        {"ts": ts + 2.0, "event_type": "engine.span.end",
         "details": ESPAN.end_details(
             span_id=span_id, role=role, engine_id="claude_code", model_id=model,
             status="ok", duration_ms=2000, input_tokens=in_t, output_tokens=out_t,
             cache_read_tokens=cr_t, cache_write_tokens=cw_t)},
    ]


@pytest.fixture
def chain(tmp_path) -> Path:
    """Four worker turns: two before the epoch, two after."""
    rows: list[dict] = []
    rows += _span("old-1", role="worker", model="claude-opus-5", ts=_OLD,
                  tokens=(100, 100, 0, 0))
    rows += _span("old-2", role="os", model="claude-haiku-4-5-20251001", ts=_OLD + 10,
                  tokens=(100, 100, 0, 0))
    rows += _span("new-1", role="worker", model="claude-sonnet-5", ts=_NEW,
                  tokens=(200, 200, 0, 0))
    rows += _span("new-2", role="os", model="claude-haiku-4-5-20251001", ts=_NEW + 10,
                  tokens=(200, 200, 0, 0))
    path = tmp_path / "audit.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """A tenant home the epoch file lands in, never the operator's real one."""
    home = tmp_path / "home"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setattr(UE, "_path",
                        lambda tid: home / "tenants" / tid / "global" / "usage_epoch.json")
    return home


# ── 1. nothing is deleted ────────────────────────────────────────────


class TestResetDeletesNothing:
    def test_clearing_restores_every_historical_turn(self, chain, isolated_home, monkeypatch):
        monkeypatch.setattr(MU, "_chain_path", lambda tid: chain)

        all_time = MU.model_usage("_default")
        assert all_time["totals"]["turns"] == 4
        assert all_time["window"]["active"] is False

        UE.set_epoch("_default", at=_EPOCH, reason="test")
        narrowed = MU.model_usage("_default")
        assert narrowed["totals"]["turns"] == 2

        UE.clear_epoch("_default")
        restored = MU.model_usage("_default")
        assert restored["totals"]["turns"] == 4, (
            "clearing the epoch must bring the full history back — if it does "
            "not, the reset deleted something"
        )
        assert restored["totals"] == all_time["totals"]

    def test_the_chain_file_is_never_written(self, chain, isolated_home):
        before = chain.read_bytes()
        UE.set_epoch("_default", at=_EPOCH, reason="test")
        MU.model_usage("_default")
        UE.clear_epoch("_default")
        assert chain.read_bytes() == before, "the audit chain was modified"

    def test_epoch_file_is_not_world_readable(self, isolated_home):
        UE.set_epoch("_default", at=_EPOCH, reason="test")
        path = UE._path("_default")
        assert path.stat().st_mode & 0o777 == 0o600

    def test_a_corrupt_epoch_file_means_all_time_not_an_error(self, chain, isolated_home, monkeypatch):
        monkeypatch.setattr(MU, "_chain_path", lambda tid: chain)
        UE.set_epoch("_default", at=_EPOCH, reason="test")
        UE._path("_default").write_text("{ not json")
        assert UE.epoch_ts("_default") == 0.0
        assert MU.model_usage("_default")["totals"]["turns"] == 4


# ── 2. one epoch, both readers ───────────────────────────────────────


class TestOneEpochBothReaders:
    def test_turn_view_and_dollar_view_use_the_same_stored_value(
        self, chain, isolated_home, monkeypatch,
    ):
        from core.learning import model_selection_learner as MSL

        monkeypatch.setattr(MU, "_chain_path", lambda tid: chain)
        monkeypatch.setattr(MSL, "_acs_chain_paths", lambda tid: [chain])
        UE.set_epoch("_default", at=_EPOCH, reason="test")

        usage = MU.model_usage("_default")
        cost = MSL.compute_cost_efficiency("_default", chain_path=chain)

        worker_turns = next(r["turns"] for r in usage["roles"] if r["role"] == "worker")
        acs_turns = sum(p.counted_turns for p in cost.acs_daily)
        assert worker_turns == acs_turns == 1, (
            "the turn view and the dollar view disagree about which worker "
            "turns are in the window"
        )

    def test_epoch_is_per_tenant(self, isolated_home):
        UE.set_epoch("_default", at=_EPOCH, reason="test")
        assert UE.epoch_ts("_default") == _EPOCH
        assert UE.epoch_ts("other") == 0.0


# ── 3. a narrowed number never travels without its window ────────────


class TestWindowTravelsWithTheNumbers:
    def test_model_usage_payload_carries_the_window(self, chain, isolated_home, monkeypatch):
        monkeypatch.setattr(MU, "_chain_path", lambda tid: chain)
        UE.set_epoch("_default", at=_EPOCH, reason="operator reset")

        payload = MU.model_usage("_default")
        assert payload["window"]["active"] is True
        assert payload["window"]["epoch_ts"] == _EPOCH
        assert payload["window"]["since_iso"]
        assert payload["window"]["reason"] == "operator reset"

    def test_a_half_span_across_the_boundary_is_excluded_whole(
        self, tmp_path, isolated_home, monkeypatch,
    ):
        """A span that STARTS before the epoch and ENDS after it must not be
        half-counted. Folding only its end would produce a turn with no start,
        no status and no duration — which the roll-up reports as `unfinished`,
        i.e. a crash that never happened."""
        rows = [
            {"ts": _EPOCH - 5, "event_type": "engine.span.start",
             "details": ESPAN.start_details(span_id="straddle", role="worker",
                                            engine_id="claude_code",
                                            model_id="claude-sonnet-5")},
            {"ts": _EPOCH + 5, "event_type": "engine.span.end",
             "details": ESPAN.end_details(span_id="straddle", role="worker",
                                          engine_id="claude_code",
                                          model_id="claude-sonnet-5", status="ok",
                                          duration_ms=10_000, input_tokens=1,
                                          output_tokens=1)},
        ]
        path = tmp_path / "straddle.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        monkeypatch.setattr(MU, "_chain_path", lambda tid: path)
        UE.set_epoch("_default", at=_EPOCH, reason="test")

        result = MU.model_usage("_default")
        assert result["totals"]["unfinished"] == 0, (
            "a span straddling the epoch was half-counted and now reads as an "
            "unfinished turn"
        )


# ── 4. the worker arithmetic the panel shows ─────────────────────────


class TestWorkerAndCombinedArithmetic:
    """The numbers the operator reads off the two new cards."""

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

    def test_status_exposes_the_worker_half_in_the_same_shape_as_the_os_half(self, client):
        body = client.get("/v1/console/learning/model-cost-optimizer/status").json()
        # Every OS field has a worker counterpart. The worker half used to be
        # two dollar totals and nothing else, so the panel could draw a line it
        # could not explain.
        for field in ("acs_counted_turns", "acs_total_turns", "acs_savings_percent",
                      "acs_model_mix", "acs_worker_model_pin",
                      "combined_savings_percent", "combined_actual_usd",
                      "combined_baseline_usd", "combined_data_available",
                      "window"):
            assert field in body, f"{field} missing from the status payload"

    @pytest.fixture
    def crafted(self, monkeypatch):
        """Deterministic, asymmetric cost data injected at the route's source.

        NOT the live install: the previous revision read whatever this host
        happened to have, and when the in-process chain path did not resolve it
        skipped — so the one assertion that pins the combination arithmetic did
        not run at all. The two series below are deliberately lopsided (a cheap
        500-turn OS series against an expensive 4-turn worker series) because
        that is the shape where "average the two percentages" and "divide the
        two dollar totals" give visibly different answers.
        """
        from core.learning import model_selection_learner as MSL

        os_actual, os_baseline = 10.0, 50.0        # 80.00% saved, 500 turns
        w_actual, w_baseline = 8.0, 10.0           #  20.00% saved,   4 turns

        def _crafted(tenant_id, chain_path=None):
            return MSL.CostEfficiencyResult(
                has_data=True,
                daily=[MSL.CostDayPoint(date="2026-09-15", actual_usd=os_actual,
                                        baseline_usd=os_baseline,
                                        counted_turns=500, total_turns=500)],
                total_actual_usd=os_actual,
                total_baseline_usd=os_baseline,
                savings_percent=80.0,
                model_mix={"claude-haiku-4-5-20251001": 500},
                acs_daily=[MSL.CostDayPoint(date="2026-09-15", actual_usd=w_actual,
                                            baseline_usd=w_baseline,
                                            counted_turns=4, total_turns=4)],
                acs_total_actual_usd=w_actual,
                acs_total_baseline_usd=w_baseline,
                acs_model_mix={"claude-opus-5": 2, "claude-sonnet-5": 2},
            )

        monkeypatch.setattr(MSL, "compute_cost_efficiency", _crafted)
        return {"os": (os_actual, os_baseline), "worker": (w_actual, w_baseline)}

    def test_combined_is_derived_from_dollars_not_from_averaging_percentages(
        self, client, crafted,
    ):
        """Averaging two percentages weights a 4-turn worker series like a
        500-turn OS one and describes traffic that never ran."""
        body = client.get("/v1/console/learning/model-cost-optimizer/status").json()
        assert body["combined_data_available"] is True

        assert body["combined_actual_usd"] == pytest.approx(18.0, abs=1e-4)
        assert body["combined_baseline_usd"] == pytest.approx(60.0, abs=1e-4)
        # 1 - 18/60 = 70.00%
        assert body["combined_savings_percent"] == pytest.approx(70.0, abs=0.01)

        # The wrong answer, stated explicitly so the test fails loudly if the
        # implementation ever drifts to it: (80 + 20) / 2 = 50.00%.
        naive_average = (body["cost_savings_percent"] + body["acs_savings_percent"]) / 2
        assert naive_average == pytest.approx(50.0, abs=0.01)
        assert abs(body["combined_savings_percent"] - naive_average) > 15.0

    def test_worker_savings_use_the_same_definition_as_the_os_savings(
        self, client, crafted,
    ):
        body = client.get("/v1/console/learning/model-cost-optimizer/status").json()
        assert body["acs_savings_percent"] == pytest.approx(20.0, abs=0.01)
        assert body["acs_counted_turns"] == 4
        assert body["acs_total_turns"] == 4
        assert body["acs_model_mix"] == {"claude-opus-5": 2, "claude-sonnet-5": 2}

    def test_combined_is_withheld_when_only_one_side_has_data(self, client, monkeypatch):
        """A total computed from one half is not a total."""
        from core.learning import model_selection_learner as MSL

        def _os_only(tenant_id, chain_path=None):
            return MSL.CostEfficiencyResult(
                has_data=True,
                daily=[MSL.CostDayPoint(date="2026-09-15", actual_usd=10.0,
                                        baseline_usd=50.0, counted_turns=500,
                                        total_turns=500)],
                total_actual_usd=10.0, total_baseline_usd=50.0, savings_percent=80.0,
                model_mix={"claude-haiku-4-5-20251001": 500},
            )

        monkeypatch.setattr(MSL, "compute_cost_efficiency", _os_only)
        body = client.get("/v1/console/learning/model-cost-optimizer/status").json()
        assert body["cost_data_available"] is True
        assert body["acs_data_available"] is False
        assert body["combined_data_available"] is False

    def test_reset_endpoint_sets_and_clears_the_window(self, client, isolated_home):
        set_body = client.post(
            "/v1/console/learning/model-cost-optimizer/usage-epoch",
            json={"reason": "e2e"},
        ).json()
        assert set_body["status"] == "ok"
        assert set_body["window"]["active"] is True
        # The response says in words what the button did NOT do. An operator
        # who believes this deleted their audit history has been misled by the
        # UI, not by the data.
        assert "no audit record was modified" in set_body["note"].lower()

        clear_body = client.post(
            "/v1/console/learning/model-cost-optimizer/usage-epoch",
            json={"clear": True},
        ).json()
        assert clear_body["window"]["active"] is False
