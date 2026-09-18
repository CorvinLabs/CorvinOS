"""
E2E tests for real cost-efficiency tracking — ADR-0696.

Replaces the ADR-0377 Phase 2b placeholder (`cost_baseline = 100.0`,
hardcoded regardless of real usage) with a computation grounded entirely in
real `os_turn.completed` audit events: real `input_tokens`/`output_tokens`
(added to the event in `chat_runtime.py::_os_emit_completed`) times real,
published per-model USD pricing (`model_selection_learner.py`).

Tests:
1. `compute_cost_efficiency` derives real $ totals + a real daily series
   from a synthetic but realistically-shaped audit chain.
2. A turn with an unrecognized model, or no token counts, is excluded
   rather than assigned a guessed price — no fabricated numbers.
3. With zero usable data, `has_data` is False and totals are zero — the
   honest "insufficient data" state, not a flat 100.
4. The real `/status` HTTP endpoint (FastAPI TestClient — the actual
   transport boundary, not a direct function call) reflects the same
   real computation end-to-end.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console.corvin_console import auth as session_auth
from core.console.corvin_console import deps as console_deps
from core.learning.learned_threshold_store import get_store, reset_store
from core.learning.model_selection_learner import compute_cost_efficiency


def _fake_session_record(tenant_id: str) -> session_auth.SessionRecord:
    """Build a minimal valid SessionRecord for dependency-override auth.

    Same technique as core/console/tests/test_vibe_engineering_audit_tenant_isolation.py
    — drives the REAL router through TestClient; only the session dependency
    is stubbed, so the request still exercises real routing, real tenant_id
    plumbing, and the real compute_cost_efficiency() call.
    """
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


def _write_turn(fh, *, turn_id: str, model: str, input_tokens: int, output_tokens: int, ts: float) -> None:
    fh.write(json.dumps({
        "ts": ts,
        "event_type": "os_turn.started",
        "details": {"turn_id": turn_id, "model": model, "persona": "assistant"},
    }) + "\n")
    fh.write(json.dumps({
        "ts": ts + 1.0,
        "event_type": "os_turn.completed",
        "details": {
            "turn_id": turn_id,
            "model": model,
            "tools_called": 3,
            "exit_code": 0,
            "timed_out": False,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }) + "\n")


def _write_acs_completion(fh, *, model_id: str, input_tokens: int,
                          output_tokens: int, ts: float) -> None:
    """One real-shaped `acs.engine_completed` record (delegated worker run).

    Field names mirror `acs_runtime.py`'s emission exactly — a single event
    carries the whole completion, no started/completed join.
    """
    fh.write(json.dumps({
        "ts": ts,
        "event_type": "acs.engine_completed",
        "details": {
            "run_id": "acs_run_1",
            "worker_id": "w1",
            "engine_id": "claude_code",
            "model_id": model_id,
            "locality": "local",
            "duration_ms": 42_000,
            "tokens_used": input_tokens + output_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }) + "\n")


class TestComputeCostEfficiency:
    def test_real_token_usage_produces_real_totals(self, tmp_path):
        chain = tmp_path / "audit.jsonl"
        with chain.open("w") as fh:
            # Actual model used: cheap sonnet turn, 10K in / 2K out.
            _write_turn(fh, turn_id="t1", model="claude-sonnet-5",
                        input_tokens=10_000, output_tokens=2_000, ts=1_800_000_000.0)

        result = compute_cost_efficiency("_default", chain_path=chain)

        assert result.has_data is True
        assert len(result.daily) == 1

        # actual: sonnet price (0.002, 0.010) per 1K
        expected_actual = (10_000 / 1000.0) * 0.002 + (2_000 / 1000.0) * 0.010
        # baseline: always-opus price (0.005, 0.025) per 1K, same tokens
        expected_baseline = (10_000 / 1000.0) * 0.005 + (2_000 / 1000.0) * 0.025

        assert result.daily[0].actual_usd == pytest.approx(expected_actual, abs=1e-4)
        assert result.daily[0].baseline_usd == pytest.approx(expected_baseline, abs=1e-4)
        assert result.total_actual_usd == pytest.approx(expected_actual, abs=1e-4)
        # Cheaper model than the baseline → positive savings.
        assert result.savings_percent > 0

    def test_unrecognized_model_excluded_not_guessed(self, tmp_path):
        chain = tmp_path / "audit.jsonl"
        with chain.open("w") as fh:
            _write_turn(fh, turn_id="t1", model="some-future-model-nobody-priced-yet",
                        input_tokens=5_000, output_tokens=1_000, ts=1_800_000_000.0)

        result = compute_cost_efficiency("_default", chain_path=chain)

        assert result.has_data is False
        assert result.total_actual_usd == 0.0
        # The day is still listed, with a visible coverage gap (0 of 1 turn
        # counted) rather than vanishing from the chart — that "thin bar"
        # behaviour was added after this test was written, and dropping the day
        # is what would hide the exclusion. What must stay zero is the money.
        assert [(p.counted_turns, p.total_turns, p.actual_usd, p.baseline_usd)
                for p in result.daily] == [(0, 1, 0.0, 0.0)]

    def test_turn_without_token_counts_excluded(self, tmp_path):
        chain = tmp_path / "audit.jsonl"
        with chain.open("w") as fh:
            # Pre-ADR-0696 event shape: no input_tokens/output_tokens at all.
            fh.write(json.dumps({
                "ts": 1_800_000_000.0,
                "event_type": "os_turn.started",
                "details": {"turn_id": "t1", "model": "claude-sonnet-5", "persona": "assistant"},
            }) + "\n")
            fh.write(json.dumps({
                "ts": 1_800_000_001.0,
                "event_type": "os_turn.completed",
                "details": {
                    "turn_id": "t1", "model": "claude-sonnet-5",
                    "tools_called": 1, "exit_code": 0, "timed_out": False,
                },
            }) + "\n")

        result = compute_cost_efficiency("_default", chain_path=chain)

        assert result.has_data is False
        assert result.total_actual_usd == 0.0

    def test_no_chain_file_is_insufficient_data_not_fabricated(self, tmp_path):
        result = compute_cost_efficiency("_default", chain_path=tmp_path / "missing.jsonl")

        assert result.has_data is False
        assert result.daily == []
        assert result.total_baseline_usd == 0.0
        assert result.savings_percent == 0.0

    def test_multi_day_series_buckets_by_day(self, tmp_path):
        chain = tmp_path / "audit.jsonl"
        day1 = 1_800_000_000.0          # some day
        day2 = day1 + 24 * 3600.0       # next day
        with chain.open("w") as fh:
            _write_turn(fh, turn_id="t1", model="claude-haiku-4-5",
                        input_tokens=1_000, output_tokens=500, ts=day1)
            _write_turn(fh, turn_id="t2", model="claude-opus-5",
                        input_tokens=1_000, output_tokens=500, ts=day2)

        result = compute_cost_efficiency("_default", chain_path=chain)

        assert result.has_data is True
        assert len(result.daily) == 2
        dates = [p.date for p in result.daily]
        assert dates == sorted(dates)


class TestStatusEndpointRealCost:
    """Hits the real FastAPI route (transport boundary), not the function directly.

    Auth is stubbed via dependency_overrides (proven pattern — see
    test_vibe_engineering_audit_tenant_isolation.py); tenant routing, the
    audit-chain read, and the cost computation are all real.
    """

    @pytest.fixture(autouse=True)
    def isolated_corvin_home(self, tmp_path):
        self.home = tmp_path / "corvin_home"
        with patch.dict(os.environ, {"CORVIN_HOME": str(self.home)}):
            yield

    @pytest.fixture
    def test_client(self):
        from core.console.corvin_console.routes.model_cost_optimizer_api import (
            router as ms_router,
        )

        app = FastAPI()
        # ms_router already declares prefix="/learning/model-cost-optimizer" —
        # mirror the real gateway mount (/v1/console) exactly, not the
        # inner path too (that double-prefix 404 bug bit ADR-0377 P2b once
        # already, see test_model_selection_learning_e2e.py's comment).
        app.include_router(ms_router, prefix="/v1/console")
        rec = _fake_session_record("_default")
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        return TestClient(app)

    @pytest.fixture
    def store(self):
        reset_store("_default")
        return get_store("_default")

    def _chain_path(self) -> Path:
        d = self.home / "tenants" / "_default" / "global" / "forge"
        d.mkdir(parents=True, exist_ok=True)
        return d / "audit.jsonl"

    def _acs_chain_path(self) -> Path:
        """Where ACS actually writes — one directory ABOVE the forge chain."""
        d = self.home / "tenants" / "_default" / "global"
        d.mkdir(parents=True, exist_ok=True)
        return d / "audit.jsonl"

    def test_status_reports_no_cost_data_by_default(self, test_client, store):
        """With no real usage recorded, the endpoint must say so honestly —
        never fall back to the old hardcoded $100.0 baseline."""
        response = test_client.get("/v1/console/learning/model-cost-optimizer/status")

        assert response.status_code == 200
        data = response.json()

        assert data["cost_data_available"] is False
        assert data["cost_baseline_usd"] == 0.0
        assert data["cost_current_usd"] == 0.0
        assert data["cost_history"] == []

    def test_status_reflects_real_recorded_usage(self, test_client, store):
        """With real os_turn.completed events on the chain, the real HTTP
        response carries real $ totals derived from them — not $100 flat."""
        chain = self._chain_path()
        with chain.open("w") as fh:
            _write_turn(fh, turn_id="t1", model="claude-sonnet-5",
                        input_tokens=10_000, output_tokens=2_000, ts=1_800_000_000.0)

        response = test_client.get("/v1/console/learning/model-cost-optimizer/status")

        assert response.status_code == 200
        data = response.json()

        assert data["cost_data_available"] is True
        assert len(data["cost_history"]) == 1
        assert data["cost_baseline_usd"] > 0.0
        assert data["cost_current_usd"] > 0.0
        # Never the old hardcoded placeholder.
        assert data["cost_baseline_usd"] != 100.0

    def test_status_says_no_acs_data_when_worker_chain_is_absent(self, test_client, store):
        """`acs_data_available` must reach the client.

        Without it the panel cannot tell "delegated workers cost $0.00" from
        "no delegated worker run was ever recorded" — and the per-day
        `acs_*_usd` zeros then plot as a flat line that reads as the former.
        This install has no ACS chain file at all, which is exactly the state
        this flag exists to name.
        """
        chain = self._chain_path()
        with chain.open("w") as fh:
            _write_turn(fh, turn_id="t1", model="claude-sonnet-5",
                        input_tokens=10_000, output_tokens=2_000, ts=1_800_000_000.0)

        data = test_client.get(
            "/v1/console/learning/model-cost-optimizer/status"
        ).json()

        # OS-turn data present, worker data absent — the two are measured from
        # two different chain files and must never imply each other.
        assert data["cost_data_available"] is True
        assert data["acs_data_available"] is False
        assert data["acs_cost_actual_usd"] == 0.0
        assert data["acs_cost_baseline_usd"] == 0.0
        assert data["acs_model_mix"] == {}
        # The per-day zeros are still emitted (the OS series needs the day) —
        # which is precisely why the flag, not the zeros, must drive the chart.
        assert all(p["acs_total_turns"] == 0 for p in data["cost_history"])

    def test_status_reflects_real_acs_worker_spend(self, test_client, store):
        """A real `acs.engine_completed` record shows up as worker spend.

        Drives the SECOND chain (`<home>/tenants/<tid>/global/audit.jsonl`,
        one directory above the canonical forge chain — ADR-0650/0654) through
        the real HTTP route.
        """
        acs_chain = self._acs_chain_path()
        with acs_chain.open("w") as fh:
            _write_acs_completion(fh, model_id="claude-haiku-4-5-20251001",
                                  input_tokens=8_000, output_tokens=3_000,
                                  ts=1_800_000_000.0)

        data = test_client.get(
            "/v1/console/learning/model-cost-optimizer/status"
        ).json()

        assert data["acs_data_available"] is True
        assert data["acs_cost_actual_usd"] > 0.0
        # Haiku against the Opus baseline — a real, measured saving.
        assert data["acs_cost_baseline_usd"] > data["acs_cost_actual_usd"]
        assert data["acs_model_mix"] == {"claude-haiku-4-5-20251001": 1}
        assert sum(p["acs_total_turns"] for p in data["cost_history"]) == 1


class TestAcsWorkerCostSurvivesTheRealAuditWriter:
    """The worker series must survive the REAL chain writer, not just a fixture.

    Every other test in this file hand-writes the JSONL, which skips the
    ADR-0129 M2 per-event field floor — and that floor is exactly where the
    worker cost dimension was dying. `acs.engine_completed`'s per-event
    allowlist in `forge/security_events.py` listed only `tokens_used`, so the
    four-way token split both emitters pass was moved into `_dropped_fields` at
    write time. The reader (`_read_acs_completions`) reads ONLY the split keys,
    so delegated worker spend priced as $0.00 for every tenant, permanently —
    a state indistinguishable from "no worker has run yet".

    Writing through `write_event` here is what makes this test able to see it.
    """

    @pytest.fixture(autouse=True)
    def isolated_corvin_home(self, tmp_path):
        self.home = tmp_path / "corvin_home"
        with patch.dict(os.environ, {"CORVIN_HOME": str(self.home)}):
            yield

    @staticmethod
    def _write_event():
        repo = Path(__file__).resolve().parents[2]
        forge_path = str(repo / "corvin_operator" / "forge")
        if forge_path not in sys.path:
            sys.path.insert(0, forge_path)
        from forge import security_events  # noqa: PLC0415

        return security_events.write_event

    def test_token_split_is_not_dropped_by_the_field_floor(self, tmp_path):
        chain = tmp_path / "acs_chain.jsonl"
        self._write_event()(chain, "acs.engine_completed", details={
            "run_id": "r1", "worker_id": "w0", "engine_id": "claude_code",
            "model_id": "claude-haiku-4-5-20251001", "locality": "us_cloud",
            "duration_ms": 42_000, "tokens_used": 11_000, "exit_code": 0,
            "input_tokens": 8_000, "output_tokens": 3_000,
            "cache_creation_input_tokens": 22_902, "cache_read_input_tokens": 5,
        })
        details = json.loads(chain.read_text(encoding="utf-8").splitlines()[-1])["details"]

        assert "_dropped_fields" not in details, (
            f"field floor dropped {details.get('_dropped_fields')} — worker cost "
            "cannot be computed from what reaches the chain"
        )
        assert details["input_tokens"] == 8_000
        assert details["cache_creation_input_tokens"] == 22_902

    def test_content_is_still_dropped_negative_control(self, tmp_path):
        """Widening the allowlist must not have opened it to content (GDPR Art. 5)."""
        chain = tmp_path / "acs_chain.jsonl"
        self._write_event()(chain, "acs.engine_completed", details={
            "run_id": "r1", "input_tokens": 1,
            "prompt": "the user's actual words",
        })
        details = json.loads(chain.read_text(encoding="utf-8").splitlines()[-1])["details"]

        assert "prompt" not in details
        assert "prompt" in (details.get("_dropped_fields") or [])
        assert details["input_tokens"] == 1

    def test_real_writer_to_real_route_yields_real_worker_dollars(self):
        """Full path: real audit writer -> real reader -> real HTTP response."""
        from core.console.corvin_console.routes.model_cost_optimizer_api import (
            router as ms_router,
        )

        acs_dir = self.home / "tenants" / "_default" / "global"
        acs_dir.mkdir(parents=True, exist_ok=True)
        self._write_event()(acs_dir / "audit.jsonl", "acs.engine_completed", details={
            "run_id": "r1", "worker_id": "w0", "engine_id": "claude_code",
            "model_id": "claude-haiku-4-5-20251001", "locality": "us_cloud",
            "duration_ms": 42_000, "tokens_used": 11_000, "exit_code": 0,
            "input_tokens": 8_000, "output_tokens": 3_000,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        })

        app = FastAPI()
        app.include_router(ms_router, prefix="/v1/console")
        rec = _fake_session_record("_default")
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        data = TestClient(app).get(
            "/v1/console/learning/model-cost-optimizer/status"
        ).json()

        assert data["acs_data_available"] is True
        assert data["acs_cost_actual_usd"] > 0.0
        assert data["acs_model_mix"] == {"claude-haiku-4-5-20251001": 1}


class TestBedrockPrefixedModelIdsArePriced:
    """Region-prefixed model ids must price, not vanish from the totals.

    On a Bedrock-authenticated install the model id carries a cross-region
    inference-profile prefix (`eu.anthropic.claude-sonnet-5`). The pricing
    table is keyed on the bare family and the lookup is prefix-ANCHORED, so an
    unstripped prefix used to match nothing — and an unpriced turn is silently
    dropped from cost while still counting toward `total_turns`.

    That is not hypothetical here: ACS worker model resolution takes
    `ANTHROPIC_MODEL` as its third step (`acs_runtime.py::_resolve_worker_model`),
    and on this box that variable IS `eu.anthropic.claude-sonnet-5` — so 100%
    of delegated worker spend would have priced as $0.00.
    """

    @pytest.fixture(autouse=True)
    def isolated_corvin_home(self, tmp_path):
        self.home = tmp_path / "corvin_home"
        with patch.dict(os.environ, {"CORVIN_HOME": str(self.home)}):
            yield

    @pytest.mark.parametrize("prefix", ["eu.anthropic.", "us.anthropic.",
                                       "apac.anthropic.", "anthropic."])
    def test_prefixed_id_costs_the_same_as_the_bare_id(self, tmp_path, prefix):
        bare = tmp_path / "bare.jsonl"
        prefixed = tmp_path / "prefixed.jsonl"
        with bare.open("w") as fh:
            _write_turn(fh, turn_id="t1", model="claude-sonnet-5",
                        input_tokens=10_000, output_tokens=2_000, ts=1_800_000_000.0)
        with prefixed.open("w") as fh:
            _write_turn(fh, turn_id="t1", model=f"{prefix}claude-sonnet-5",
                        input_tokens=10_000, output_tokens=2_000, ts=1_800_000_000.0)

        bare_result = compute_cost_efficiency("_default", chain_path=bare)
        prefixed_result = compute_cost_efficiency("_default", chain_path=prefixed)

        assert bare_result.has_data is True
        # The load-bearing half: without the normalization this is False and
        # every total below is 0.0.
        assert prefixed_result.has_data is True
        assert prefixed_result.total_actual_usd == pytest.approx(
            bare_result.total_actual_usd, abs=1e-6)
        assert prefixed_result.total_baseline_usd == pytest.approx(
            bare_result.total_baseline_usd, abs=1e-6)
        assert prefixed_result.daily[0].counted_turns == 1

    def test_prefixed_opus_is_the_baseline_not_a_saving(self):
        """Stripping must also be applied to the baseline-model recognition.

        A prefixed Opus turn priced against the Opus baseline yields 0%
        savings. If the prefix leaked through, the turn would be dropped
        entirely and the panel would show no data instead of "no saving".
        """
        from core.learning.model_selection_learner import _price_for_model

        assert _price_for_model("eu.anthropic.claude-opus-5") == \
            _price_for_model("claude-opus-5")

    def test_genuinely_unknown_model_still_returns_none(self):
        """The "exclude rather than guess" rule is unchanged.

        Stripping a known routing prefix is not the same as guessing a price:
        an id whose family is still unrecognized after stripping must stay
        unpriced.
        """
        from core.learning.model_selection_learner import _price_for_model

        assert _price_for_model("eu.anthropic.some-future-model") is None
        assert _price_for_model("gpt-5-turbo") is None
        assert _price_for_model("") is None
        # Not a routing prefix — a different vendor's namespace must not be
        # stripped into a Claude family match.
        assert _price_for_model("openai.claude-sonnet-5-lookalike") is None
