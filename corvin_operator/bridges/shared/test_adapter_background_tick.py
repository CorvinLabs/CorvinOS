"""The adapter's background-delivery tick runs clean (live bug 2026-09-28).

The running adapter logged every 30 s: ``orchestration_aggregator tick failed:
module 'orchestration_aggregator' has no attribute 'deliver_ready'``. The tick
called a function that never existed (ADR-2027 is PROPOSED; its adapter wiring
was written against an API the module does not have — wrong
``on_task_complete`` signature, dicts read as attributes, a stub audit write
that returned success). The call was removed; the module stays, marked NOT
WIRED. One tick must run every step without a single failure log.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from test_adapter_stream_idle import _fresh_adapter  # noqa: E402


def test_one_tick_logs_no_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ADAPTER_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.setenv("ADAPTER_INBOX", str(tmp_path / "inbox"))
    adapter = _fresh_adapter()
    assert str(adapter.OUTBOX).startswith(str(tmp_path)), adapter.OUTBOX
    (tmp_path / "outbox").mkdir()
    # a delivered completion record — the input the removed orchestration
    # block consumed (and mutated) on every tick
    import completion_notify as _cn  # noqa: PLC0415
    qdir = _cn._queue_dir()
    assert str(qdir).startswith(str(tmp_path)), qdir  # positive control: sandboxed
    qdir.mkdir(parents=True, exist_ok=True)
    rec = {"id": "t1", "state": "delivered", "ok": True, "created_at": time.time(),
           "delivered_at": time.time(), "chat_id": "c", "label": "x"}
    (qdir / "t1.json").write_text(json.dumps(rec))

    lines: list[str] = []
    monkeypatch.setattr(adapter, "log", lambda msg, *a, **k: lines.append(str(msg)))
    adapter._background_delivery_tick()

    failed = [l for l in lines if "failed" in l]
    assert not failed, failed
    # the tick no longer marks completion records as consumed for a feature
    # that never delivers anything
    assert "_aggregated" not in json.loads((qdir / "t1.json").read_text())


def test_adapter_calls_no_orchestration_aggregator_api():
    """Static guard: re-wiring the aggregator needs the module's real API
    (deliver_ready, a keyword-compatible on_task_complete, a real audit write)
    first — ADR-2027."""
    src = (HERE / "adapter.py").read_text(encoding="utf-8")
    assert "_background_delivery_tick" in src  # positive control: right file
    assert "_oa.deliver_ready" not in src and "_oa.on_task_complete" not in src
