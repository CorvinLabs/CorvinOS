"""ACS worker engine-span: the END span is priceable (ADR-0759).

A worker span closes on the model the ENGINE reported (``agents.attested_model``)
and carries the four-way token split (``engine_span.usage_split``). Before this
fix the ok-path end span took its ``model_id`` from the configured fallback in
``_call_worker_sync`` and carried no split, so every ACS worker was priced at
$0.00 or at the wrong model rate.

The boundary under test is ``_dispatch_workers`` → ``_call_worker_sync`` → a
real subprocess (a stub ``claude`` binary that prints a CLI JSON envelope) →
the audit writer. Only ``_write_audit`` is captured.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from . import acs_runtime as _rt
    from . import engine_span as _espan
except ImportError:
    import acs_runtime as _rt  # type: ignore[no-redef]
    import engine_span as _espan  # type: ignore[no-redef]

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="stub claude binary is a POSIX shell script",
)

CONFIGURED = "configured-model-from-config"
REPORTED = "claude-opus-5-5"


def _stub_claude(tmp_path: Path, envelope: dict) -> str:
    """A stand-in ``claude`` that drains stdin and prints ``envelope``."""
    script = tmp_path / "claude-stub"
    payload = json.dumps(envelope)
    script.write_text(
        "#!/bin/sh\ncat >/dev/null\ncat <<'__ENVELOPE__'\n"
        + payload + "\n__ENVELOPE__\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


def _run_one_worker(tmp_path: Path, envelope: dict) -> list[tuple[str, dict]]:
    """Dispatch one worker against the stub and return every audit write."""
    from test_acs_runtime import _minimal_spec  # type: ignore[import-not-found]

    binary = _stub_claude(tmp_path, envelope)
    spec = _minimal_spec()
    root_budget = _rt._budget_from_spec(spec)
    root_budget.max_total_workers = 100
    root_budget.max_workers_per_iteration = 5
    ctx = _rt.RunContext(
        run_id="run-span", workflow_id="wf-span", workflow_spec=spec,
        budget=root_budget.fraction(1.0), run_dir=tmp_path, root_budget=root_budget,
    )
    writes: list[tuple[str, dict]] = []

    def _capture(tenant_id, event_type, details):
        writes.append((event_type, dict(details)))

    subtasks = [{"id": "t0", "instructions": "do the thing"}]
    with (
        patch.object(_rt, "_resolve_worker_engine", return_value=("claude_code", CONFIGURED)),
        patch.object(_rt, "_assert_engine_licensed", return_value=None),
        patch.object(_rt, "_claude_binary", return_value=binary),
        patch.object(_rt, "_apply_provider_redirect", return_value=None),
        patch.object(_rt.shutil, "which", return_value=binary),
        patch.object(_rt, "_write_audit", side_effect=_capture),
    ):
        import asyncio
        asyncio.run(_rt._dispatch_workers(
            subtasks, ctx, depth=1, manager_model="m", worker_model=CONFIGURED,
        ))
    return writes


def _end_spans(writes: list[tuple[str, dict]]) -> list[dict]:
    return [d for et, d in writes if et == _espan.ENGINE_SPAN_END and d.get("role") == "worker"]


_FULL_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": json.dumps({"status": "success", "confidence": 0.9, "result": {"ok": True}}),
    "model": REPORTED,
    "usage": {
        "input_tokens": 120,
        "output_tokens": 45,
        "cache_creation_input_tokens": 300,
        "cache_read_input_tokens": 9000,
    },
}


def test_ok_end_span_carries_the_engine_reported_model(tmp_path):
    ends = _end_spans(_run_one_worker(tmp_path, _FULL_ENVELOPE))
    assert len(ends) == 1, ends
    assert ends[0]["status"] == "ok"
    assert ends[0]["model_id"] == REPORTED, (
        "the span must close on the model the engine reported, not on the "
        f"configured fallback {CONFIGURED!r}"
    )


def test_ok_end_span_carries_the_four_way_token_split(tmp_path):
    ends = _end_spans(_run_one_worker(tmp_path, _FULL_ENVELOPE))
    end = ends[0]
    assert end["input_tokens"] == 120
    assert end["output_tokens"] == 45
    assert end["cache_write_tokens"] == 300
    assert end["cache_read_tokens"] == 9000


def test_no_model_reported_leaves_model_empty_not_configured(tmp_path):
    """An engine that reports no model yields an UNPRICED span, never a guess
    from config (a guessed id prices the run at the wrong rate)."""
    envelope = {k: v for k, v in _FULL_ENVELOPE.items() if k != "model"}
    ends = _end_spans(_run_one_worker(tmp_path, envelope))
    assert ends[0]["model_id"] == "", ends[0]
    # The tokens are still reported, so the run is counted with its split.
    assert ends[0]["input_tokens"] == 120


def test_error_end_span_does_not_fill_model_from_config(tmp_path):
    """A failed engine reported nothing usable; the end span must not invent a
    model id from the configured worker model."""
    spec_writes: list[tuple[str, dict]] = []
    from test_acs_runtime import _minimal_spec  # type: ignore[import-not-found]
    spec = _minimal_spec()
    root_budget = _rt._budget_from_spec(spec)
    root_budget.max_total_workers = 100
    root_budget.max_workers_per_iteration = 5
    ctx = _rt.RunContext(
        run_id="run-err", workflow_id="wf-err", workflow_spec=spec,
        budget=root_budget.fraction(1.0), run_dir=tmp_path, root_budget=root_budget,
    )

    def _capture(tenant_id, event_type, details):
        spec_writes.append((event_type, dict(details)))

    import asyncio
    with (
        patch.object(_rt, "_resolve_worker_engine", return_value=("claude_code", CONFIGURED)),
        patch.object(_rt, "_assert_engine_licensed", return_value=None),
        patch.object(_rt, "_call_worker_sync", side_effect=OSError("engine down")),
        patch.object(_rt, "_write_audit", side_effect=_capture),
    ):
        asyncio.run(_rt._dispatch_workers(
            [{"id": "t0", "instructions": "x"}], ctx, depth=1,
            manager_model="m", worker_model=CONFIGURED,
        ))
    ends = _end_spans(spec_writes)
    assert ends and ends[0]["status"] == "error"
    assert ends[0]["model_id"] == "", ends[0]
