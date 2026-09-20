"""E2E + wiring proof: an A2A worker run is PRICEABLE — ADR-0759 (gap 8).

The defect this locks out was live on a real install on 2026-09-20. Measured
there, over the full history of the ``_default`` tenant:

    ACS days 11 · counted 0 · total 267        (worker runs, none priced)
    engine.span.end role=worker: 60 · model_id "" · every token field 0

and the Models console's Usage & Cost tab consequently read
"Worker runs — over 0 priced worker runs" / "No worker data in this window"
on an install that *does* delegate. The cause was not the console and not the
counting epoch: ``a2a_worker._emit_a2a_engine_span`` emitted the span with
neither a ``model_id`` nor a token split, and
``model_selection_learner._read_worker_spans`` deliberately DROPS a span that
has neither ("not attributable to any model turn — a stub engine, an aborted
spawn"). So every inbound A2A run was discarded one layer below the console.

Four things have to hold together, and each one has failed on its own before:

1. The emitter closes the span on the model the ENGINE reported, plus the
   four-way token split (never a single ``tokens_used`` total, which cannot be
   priced — four different rates).
2. The audit field floor keeps those fields (``engine_span.END_FIELDS``); a
   field missing from the allowlist is dropped silently, which is exactly how
   ``acs.engine_completed`` shipped its split into a floor for months.
3. The cost reader prices the span off the real chain.
4. The console route reports it over the real HTTP boundary.

Transport note: the worker assertions drive the real ``spawn_a2a_worker``
against a stub engine and the REAL ``forge.security_events`` chain writer — a
``claude -p`` subprocess is not admissible in a suite, and the boundary under
test is emitter → field floor → chain → cost reader → console route, which the
stub exercises end to end. The console half goes through the real FastAPI
router via TestClient; only the session dependency is overridden.
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


# The model the STUB ENGINE reports. Deliberately different from anything the
# caller could have asked for: A2A passes no ``model=``, so a span carrying
# this id can only have come from the engine's own frames.
REPORTED_MODEL = "claude-sonnet-5"

USAGE = {
    "input_tokens": 2,
    "output_tokens": 13,
    "cache_read_input_tokens": 10_010,
    "cache_creation_input_tokens": 57_353,
}
EXPECTED_SPLIT = {
    "input_tokens": 2,
    "output_tokens": 13,
    "cache_read_tokens": 10_010,
    "cache_write_tokens": 57_353,
}


# ── stub engine ──────────────────────────────────────────────────────


class _Ev:
    def __init__(self, **kw):
        self.type = kw.get("type")
        self.text = kw.get("text", "")
        self.usage = kw.get("usage")
        self.error = kw.get("error")
        self.raw = kw.get("raw")


class _StubEngine:
    """Emits the frame shapes the Claude CLI actually produces: a
    ``system.init`` carrying the model, an ``assistant`` tool_use, and a
    ``result`` carrying usage."""

    name = "claude_code"

    def __init__(self, *, model: str = REPORTED_MODEL, usage=USAGE, tool_calls: int = 2):
        self._model = model
        self._usage = usage
        self._tool_calls = tool_calls

    def spawn(self, prompt, **kwargs):
        if self._model:
            yield _Ev(type="session_started",
                      raw={"type": "system", "subtype": "init", "model": self._model})
        for _ in range(self._tool_calls):
            yield _Ev(type="tool_call",
                      raw={"type": "assistant", "message": {"content": []}})
        yield _Ev(type="text_delta", text='{"status": "ok", "result": {}}')
        yield _Ev(type="turn_completed",
                  raw={"type": "result", "subtype": "success"},
                  usage=self._usage)

    def cancel(self):
        pass


# ── helpers ──────────────────────────────────────────────────────────


def _reset_compute_quota() -> None:
    """Clear the A2A compute-quota counter for this process.

    ``a2a_worker`` snapshots ``CORVIN_HOME`` at IMPORT time (ADR-0144 A3), so
    every test in a session increments the SAME free-tier day counter no
    matter what the per-test home is — around the tenth spawn the gate starts
    rejecting, the span is never written, and the failure surfaces three
    layers away in an unrelated console assertion. Reset it so each test
    measures the emitter, not the quota.
    """
    import a2a_worker

    home = Path(getattr(a2a_worker, "_CORVIN_HOME_SNAPSHOT_A2A", "/nonexistent"))
    for name in ("compute_quota.json", ".compute_quota.json.lock"):
        try:
            (home / "global" / "license" / name).unlink()
        except OSError:
            pass


def _run_a2a(chain: Path, monkeypatch, engine: _StubEngine, *, task_id: str):
    """Drive the REAL ``spawn_a2a_worker`` with its audit chain redirected.

    Asserts the run actually happened — a positive control. Without it a
    rejected spawn (quota, L34 gate, sanitiser) writes no span, and every
    assertion below would pass or fail for reasons that have nothing to do
    with what is under test.
    """
    import a2a_worker

    _reset_compute_quota()
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    result = a2a_worker.spawn_a2a_worker(
        instruction="summarise the attached notes",
        origin_id="peer-1",
        task_id=task_id,
        persona="assistant",
        ttl_s=30,
        engine_factory=lambda: engine,
    )
    assert result.status == "ok", (
        f"the worker never ran ({result.status}: {result.error}) — nothing "
        f"below this line is testing the span emitter"
    )
    return result


def _spans(chain: Path, kind: str = "engine.span.end") -> list[dict]:
    """Every span of *kind* on the chain, as the reader sees it."""
    out: list[dict] = []
    for line in chain.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event_type") == kind:
            out.append(rec)
    return out


def _worker_end_span(chain: Path) -> dict:
    ends = [r for r in _spans(chain)
            if (r.get("details") or {}).get("role") == "worker"]
    assert ends, (
        "no engine.span.end with role=worker reached the chain at all — the "
        "emitter is unreachable, so nothing below it can be tested"
    )
    return ends[-1]["details"]


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


@pytest.fixture
def cost_client():
    """The real model-cost-optimizer router behind a TestClient."""
    from core.console.corvin_console.routes import model_cost_optimizer_api

    app = FastAPI()
    app.include_router(model_cost_optimizer_api.router, prefix="/v1/console")
    app.dependency_overrides[console_deps.require_session] = (
        lambda: _fake_session_record("_default")
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ── 0. reachability: something in the running system calls this ──────


class TestTheEmitterIsReachable:
    """Phase 1 of the e2e-wiring-proof gate. A span emitter nothing invokes is
    dead code, and every assertion below it passes vacuously."""

    def test_spawn_a2a_worker_has_a_production_call_site(self):
        receiver = (_REPO / "corvin_operator" / "bridges" / "shared"
                    / "remote_trigger_receiver.py").read_text()
        assert "spawn_a2a_worker(" in receiver, (
            "the A2A receiver no longer spawns the worker — this whole file "
            "would be testing an unreachable path"
        )

    def test_the_receiver_is_mounted_by_a_shipped_host(self):
        """``corvin_gateway.app`` is what ``corvin-webui.service`` runs."""
        gateway = (_REPO / "core" / "gateway" / "corvin_gateway" / "app.py").read_text()
        assert "RemoteTriggerReceiver" in gateway

    def test_the_end_span_is_emitted_from_the_worker_itself(self):
        worker = (_REPO / "corvin_operator" / "bridges" / "shared"
                  / "a2a_worker.py").read_text()
        assert "_attested_model_of(result)" in worker, (
            "the end span stopped reading the engine-reported model"
        )


# ── 1. the emitter ───────────────────────────────────────────────────


class TestA2ASpanCarriesModelAndTokens:
    """The defect itself: model-less, token-less worker spans."""

    def test_end_span_reaches_the_chain_with_the_reported_model(self, tmp_path, monkeypatch):
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(), task_id="t-model")

        details = _worker_end_span(chain)
        assert details["model_id"] == REPORTED_MODEL, (
            "the span must close on the model the ENGINE reported; an empty "
            "model_id is what made every A2A run unpriceable"
        )

    def test_end_span_carries_the_four_way_token_split(self, tmp_path, monkeypatch):
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(), task_id="t-tokens")

        details = _worker_end_span(chain)
        for field, expected in EXPECTED_SPLIT.items():
            assert details[field] == expected, (
                f"{field} did not survive emitter → field floor → chain; a "
                f"single tokens_used total cannot be priced"
            )
        assert details["tokens_used"] == sum(EXPECTED_SPLIT.values())

    def test_tool_calls_are_counted(self, tmp_path, monkeypatch):
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(tool_calls=3), task_id="t-tools")
        assert _worker_end_span(chain)["tool_call_count"] == 3

    def test_an_engine_that_reports_no_usage_still_yields_a_counted_run(
        self, tmp_path, monkeypatch
    ):
        """"Unmeasured" is not "did not happen". A span with a model but no
        tokens must still reach the chain so the console can say "N runs, none
        with token data"."""
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(usage=None), task_id="t-nousage")

        details = _worker_end_span(chain)
        assert details["model_id"] == REPORTED_MODEL
        assert details["input_tokens"] == 0
        assert details["tokens_used"] == 0

    def test_no_prompt_or_output_text_is_in_the_span(self, tmp_path, monkeypatch):
        """L16/L34: the span is metadata only. The instruction and the worker's
        answer must appear nowhere in the record."""
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(), task_id="t-pii")

        blob = json.dumps(_worker_end_span(chain))
        assert "summarise the attached notes" not in blob
        assert "peer-1" not in blob

    def test_start_span_reports_no_model_rather_than_a_guess(self, tmp_path, monkeypatch):
        """A2A passes no ``model=``, so before the first frame the model is
        unknown. An invented id would price the run at the wrong rate — the
        honest value is empty, and the END span is what pricing reads."""
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(), task_id="t-start")

        starts = [r for r in _spans(chain, "engine.span.start")
                  if (r.get("details") or {}).get("role") == "worker"]
        assert starts, "the paired start span is missing"
        assert starts[-1]["details"]["model_id"] == ""


# ── 2. one normaliser, every emitter ─────────────────────────────────


class TestOneUsageNormaliser:
    """A private per-path copy is how the same run prices differently
    depending on which path spawned it."""

    def test_both_cli_and_span_spellings_land_in_the_same_columns(self):
        assert ESPAN.usage_split(USAGE) == EXPECTED_SPLIT
        assert ESPAN.usage_split(EXPECTED_SPLIT) == EXPECTED_SPLIT

    def test_absent_usage_is_zero_never_inferred(self):
        zeros = {k: 0 for k in EXPECTED_SPLIT}
        assert ESPAN.usage_split(None) == zeros
        assert ESPAN.usage_split({}) == zeros
        assert ESPAN.usage_split({"input_tokens": True}) == zeros

    def test_the_dispatcher_uses_the_shared_one(self):
        from corvin_gateway.dispatcher import RunDispatcher

        assert RunDispatcher._usage_split(USAGE) == ESPAN.usage_split(USAGE)

    def test_every_split_field_is_allowlisted(self):
        """A field missing from the floor's allowlist is dropped SILENTLY."""
        assert set(EXPECTED_SPLIT) <= ESPAN.END_FIELDS


# ── 3. the cost reader prices it ─────────────────────────────────────


class TestCostReaderPricesTheRun:
    def _priced(self, chain: Path, monkeypatch):
        import core.learning.model_selection_learner as msl
        from core.console.corvin_console import usage_epoch

        monkeypatch.setattr(msl, "_acs_chain_paths", lambda tid: [chain])
        monkeypatch.setattr(usage_epoch, "epoch_ts", lambda tid: 0.0)
        return msl.compute_cost_efficiency("_default", chain_path=chain)

    def test_a_real_a2a_run_is_counted_and_priced(self, tmp_path, monkeypatch):
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(), task_id="t-priced")

        result = self._priced(chain, monkeypatch)
        counted = sum(p.counted_turns for p in result.acs_daily)
        total = sum(p.total_turns for p in result.acs_daily)
        assert total == 1, "the run must be counted as a worker run"
        assert counted == 1, (
            "the run must be PRICED — counted 0 of N is exactly the "
            "'0 priced worker runs' the console reported"
        )
        assert result.acs_total_actual_usd > 0
        assert result.acs_model_mix == {REPORTED_MODEL: 1}

    def test_the_baseline_is_the_same_tokens_at_the_opus_rate(self, tmp_path, monkeypatch):
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(), task_id="t-baseline")

        result = self._priced(chain, monkeypatch)
        assert result.acs_total_baseline_usd > result.acs_total_actual_usd, (
            "Sonnet against the Opus reference must show a saving"
        )

    def test_a_run_without_tokens_counts_but_is_not_priced(self, tmp_path, monkeypatch):
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(usage=None), task_id="t-unpriced")

        result = self._priced(chain, monkeypatch)
        assert sum(p.total_turns for p in result.acs_daily) == 1
        assert sum(p.counted_turns for p in result.acs_daily) == 0
        assert result.acs_total_actual_usd == 0.0

    def test_a_span_with_neither_is_still_skipped(self, tmp_path, monkeypatch):
        """The documented rule stays: a stub engine / aborted spawn that
        reported nothing at all is not attributable to any model turn, and is
        never estimated into the totals."""
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch,
                 _StubEngine(model="", usage=None, tool_calls=0), task_id="t-nothing")

        assert _worker_end_span(chain)["model_id"] == ""
        result = self._priced(chain, monkeypatch)
        assert result.acs_daily == []


# ── 4. the console surfaces it, over the real transport ──────────────


class TestConsoleReportsTheWorkerRun:
    """The user-visible half: '/app/models → Usage & Cost' reads this route."""

    def _status(self, chain: Path, monkeypatch, cost_client):
        import core.learning.model_selection_learner as msl
        from core.console.corvin_console import usage_epoch

        monkeypatch.setattr(msl, "_acs_chain_paths", lambda tid: [chain])
        monkeypatch.setattr(
            msl, "_default_chain_path", lambda tid: chain, raising=False)
        monkeypatch.setattr(usage_epoch, "epoch_ts", lambda tid: 0.0)

        _orig = msl.compute_cost_efficiency
        monkeypatch.setattr(
            msl, "compute_cost_efficiency",
            lambda tid, chain_path=None: _orig(tid, chain_path=chain))

        response = cost_client.get("/v1/console/learning/model-cost-optimizer/status")
        assert response.status_code == 200, response.text
        return response.json()

    def test_route_reports_a_priced_worker_run(self, tmp_path, monkeypatch, cost_client):
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(), task_id="t-http")

        body = self._status(chain, monkeypatch, cost_client)
        assert body["acs_data_available"] is True
        assert body["acs_total_turns"] == 1
        assert body["acs_counted_turns"] == 1, (
            "this exact field renders as 'over N priced worker runs'"
        )
        assert body["acs_cost_actual_usd"] > 0
        assert body["acs_model_mix"] == {REPORTED_MODEL: 1}

    def test_route_carries_the_counting_window_with_the_total(
        self, tmp_path, monkeypatch, cost_client
    ):
        """ADR-0760: a narrowed total never travels without its window."""
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(), task_id="t-window")

        body = self._status(chain, monkeypatch, cost_client)
        assert set(body["window"]) >= {"active", "epoch_ts", "since_iso", "reason"}

    def test_recorded_but_unpriced_runs_never_report_a_measured_zero(
        self, tmp_path, monkeypatch, cost_client
    ):
        """The live shape on 2026-09-20: runs happened, none could be priced.

        ``acs_data_available`` used to be ``bool(acs_daily)`` — true as soon as
        a DAY had a run — so the tile rendered a green "0.0% saved · $0.0000 on
        Opus → $0.0000 actual" over nothing measured. It now means "at least
        one PRICED run", symmetric with the OS half's ``has_data``, and the
        coverage pair carries the real message instead.
        """
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(usage=None), task_id="t-unmeasured")

        body = self._status(chain, monkeypatch, cost_client)
        assert body["acs_data_available"] is False, (
            "a recorded-but-unpriced run must not unlock the dollar figures"
        )
        assert body["combined_data_available"] is False
        # ... and the run is still VISIBLE, as coverage, not as a zero.
        assert body["acs_total_turns"] == 1
        assert body["acs_counted_turns"] == 0

    def test_a_day_with_no_priced_run_is_absent_not_zero(
        self, tmp_path, monkeypatch, cost_client
    ):
        """An unmeasured day must never render as a flat $0.00 line — that
        claims delegated runs were free."""
        chain = tmp_path / "audit.jsonl"
        _run_a2a(chain, monkeypatch, _StubEngine(usage=None), task_id="t-gap")

        body = self._status(chain, monkeypatch, cost_client)
        for point in body["cost_history"]:
            if point.get("acs_counted_turns", 0) == 0:
                assert point["acs_actual_usd"] is None
                assert point["acs_baseline_usd"] is None
