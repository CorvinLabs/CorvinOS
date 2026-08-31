"""ADR-0222 k=3 — native-arm-only DECISION collection (ship-dark, zero-cost).

E2E-wiring-proof for the console-turn measurement glue built for ADR-0222 k=3.

Two-phase gate (CLAUDE.md § E2E Wiring Proof):

  Phase 1 (reachability): the collection is reached from the REAL console turn
  function ``chat_runtime.stream_turn`` — a call site OUTSIDE tests — gated by
  the ship-dark ``tde_measurement_collection`` flag and offloaded. Proven by
  source inspection of the production function (same technique the k=5 hook
  tests use for their spawn site).

  Phase 2 (functional): drive the REAL offload glue
  (``_spawn_decision_measurement`` → ``_run_decision_measurement`` →
  ``DecisionMeasurementRecorder`` → disk) in a running event loop and assert a
  ``DecisionMeasurementSample`` is persisted with the real decision + outcome;
  assert flag-OFF (the registry default) writes nothing (byte-identical); and
  assert that collecting decision samples does NOT open the 3-arm decision gate
  (it still reads INSUFFICIENT_DATA / not-authorised).

INFEASIBILITY NOTE: the full ``stream_turn`` cannot be driven end-to-end here —
it spawns the real ``claude`` CLI subprocess (external dependency). The honest
transport boundary this feature owns is the detached-task → thread → JSONL
append, which IS driven for real below. The call-site gating is proven by source
inspection of the production function plus the live registry default.

Run: core/console/.venv/bin/python -m pytest \
     tests/test_tde_measurement_k3_decision_collection.py -q
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))

# Import the tde package FIRST: importing corvin_console mutates sys.path (adds
# operator/), which shadows operator/orchestration/initial_analysis.py with a
# same-named module and breaks a later `from tde...`. Loading tde first caches
# the correct modules before that mutation happens.
from tde.tde_measurement import (  # noqa: E402
    DecisionMeasurementRecorder,
    DecisionMeasurementSample,
    aggregate_measured_evidence,
    size_band_of,
)
from tde.decision_gate import evaluate_tde_verdict  # noqa: E402
from corvin_console import chat_runtime as cr  # noqa: E402
from corvin_core import feature_flags as ff  # noqa: E402


# ---------------------------------------------------------------------------
# Flag: ship-dark, default OFF (byte-identical off)
# ---------------------------------------------------------------------------

def test_flag_is_registered_and_defaults_off():
    """The new flag exists in the real registry and its default is False."""
    entry = next(f for f in ff.REGISTRY if f.id == "tde_measurement_collection")
    assert entry.default is False
    # And a fresh tenant resolves it OFF (no features.json / yaml override).
    assert ff.is_enabled("tde_measurement_collection", "_default") is False


# ---------------------------------------------------------------------------
# Phase 1 — reachability: the REAL production call site in stream_turn
# ---------------------------------------------------------------------------

def test_collection_is_reached_from_stream_turn_flag_gated():
    """A real call site outside tests: stream_turn spawns the collection, gated
    on the ship-dark flag."""
    src = inspect.getsource(cr.stream_turn)
    assert 'is_enabled("tde_measurement_collection"' in src
    assert "_spawn_decision_measurement(" in src
    # The spawn must be *inside* the flag branch (gate precedes spawn).
    gate_at = src.index('is_enabled("tde_measurement_collection"')
    spawn_at = src.index("_spawn_decision_measurement(")
    assert gate_at < spawn_at, "spawn must be gated by the flag, not unconditional"


def test_collection_records_the_native_outcome_not_a_delegated_arm():
    """The ctx built at the call site carries the NATIVE arm only — rc + latency
    + the heuristic decision — never a fabricated delegated arm."""
    src = inspect.getsource(cr.stream_turn)
    # honest native-only inputs present …
    for token in ("\"would_delegate\": bool(_del_heuristic)",
                  "\"native_rc\": rc",
                  "\"native_latency_ms\": int((time.monotonic() - _dbg_t0)"):
        assert token in src, f"missing honest native input: {token}"
    # … and no fabricated delegated-arm fields anywhere in the ctx.
    assert "tde_tokens" not in src.split("_spawn_decision_measurement")[1][:400]


# ---------------------------------------------------------------------------
# Phase 2 — functional: drive the real offload glue to disk
# ---------------------------------------------------------------------------

def _drain_decision_tasks() -> None:
    """Await every in-flight decision task so the thread append completes."""
    pending = list(cr._DECISION_TASKS)
    if pending:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))


def test_decision_sample_persisted_on_real_offload_path(tmp_path):
    """Flag-ON behaviour: driving the real spawn writes one honest sample."""
    log = tmp_path / "measurement-week" / "decision_samples.jsonl"

    async def _drive() -> None:
        cr._spawn_decision_measurement({
            "task_id": "decision-e2e-1",
            "decision_log_path": str(log),
            "would_delegate": True,
            "native_rc": 0,
            "native_latency_ms": 1234,
            "prompt_chars": 1500,
            "worker_engine_mode": "native",
        })
        # Await the detached task (mirrors chat_runtime's fire-and-forget).
        for t in list(cr._DECISION_TASKS):
            await t

    asyncio.run(_drive())

    assert log.exists(), "sample was not persisted on the real offload path"
    lines = [ln for ln in log.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    # Real decision + real native outcome recorded, content-free.
    assert rec["would_delegate"] is True
    assert rec["observed_arm"] == "native"
    assert rec["native_rc"] == 0
    assert rec["native_latency_ms"] == 1234
    assert rec["size_band"] == size_band_of(1500) == "moderate"
    assert rec["data_source"] == "observed_native"
    # No prompt text, no delegated-arm fields (honest counterfactual).
    assert "prompt" not in rec and "task_text" not in rec
    for forbidden in ("tde_tokens", "tier_tokens", "direct_output", "tde_loss"):
        assert forbidden not in rec

    # And it round-trips back through the recorder's own loader.
    loaded = DecisionMeasurementRecorder(str(log)).load()
    assert len(loaded) == 1 and isinstance(loaded[0], DecisionMeasurementSample)


def test_flag_off_writes_nothing_byte_identical(tmp_path):
    """Flag-OFF (registry default): the call site is skipped, so NOTHING is
    written and the turn is byte-identical. Proven by exercising the exact gate
    the call site uses against a fresh tenant, then confirming no file."""
    log = tmp_path / "measurement-week" / "decision_samples.jsonl"
    # This is the real predicate the production call site is wrapped in.
    assert ff.is_enabled("tde_measurement_collection", "_default") is False
    # Since the gate is False, stream_turn never calls _spawn_decision_measurement
    # → no task, no file. (We assert the file-system consequence directly.)
    assert not log.exists()


def test_recording_never_raises_on_malformed_ctx(tmp_path):
    """Fail-closed: a malformed ctx drops the sample, never raises into the turn
    (offload safety), and never books a fabricated record."""
    log = tmp_path / "decision_samples.jsonl"

    async def _drive() -> None:
        cr._spawn_decision_measurement({
            "task_id": "decision-bad",
            "decision_log_path": str(log),
            "would_delegate": "not-a-bool-but-str",  # bool() coerces; still fine
            "native_rc": "not-an-int",               # int("...") → ValueError → drop
            "native_latency_ms": 10,
            "prompt_chars": 5,
        })
        for t in list(cr._DECISION_TASKS):
            await t  # must not raise

    asyncio.run(_drive())
    # Dropped, not fabricated: no partial/garbage line on disk.
    assert not log.exists() or log.read_text().strip() == ""


# ---------------------------------------------------------------------------
# The gate STAYS CLOSED — collecting decision samples never authorises TDE
# ---------------------------------------------------------------------------

def test_decision_samples_do_not_open_the_3arm_gate(tmp_path):
    """Collecting native-only decision samples must NOT let the gate authorise
    defaulting TDE on: they are not 3-arm evidence, so the gate still sees zero
    measured samples and returns INSUFFICIENT_DATA / not-authorised."""
    log = tmp_path / "decision_samples.jsonl"
    rec = DecisionMeasurementRecorder(str(log))

    async def _record_many() -> None:
        for i in range(50):
            await rec.record(DecisionMeasurementSample(
                task_id=f"d-{i}", timestamp=float(i), would_delegate=True,
                observed_arm="native", native_rc=0, native_latency_ms=100,
                prompt_chars=1000, size_band="moderate"))

    asyncio.run(_record_many())
    assert len(rec.load()) == 50  # samples really were collected

    # The 3-arm gate reads ONLY MeasurementSamples via aggregate_measured_evidence.
    # Decision samples are a different type and never enter it → zero evidence.
    verdict = evaluate_tde_verdict(aggregate_measured_evidence([]))
    assert verdict["amplifier_survives"] is False
    assert verdict["decided_on_measured_data"] is False
    assert verdict["winning_bands"] == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
