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
        assert result.daily == []
        assert result.total_actual_usd == 0.0

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
