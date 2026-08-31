"""Regression: side-channel envelopes (/stop→_cancel, /btw, /sig→_signal) are
dispatched on a DEDICATED pool, so they are never starved by MAX_PARALLEL busy
turns. Without this, a /stop bypasses the per-chat lock but still queues behind
in-flight turns in the bounded turn pool — and the task it tries to abort runs
to completion before the cancel is dispatched (the "chat keeps running
autonomously" bug)."""
import json
import tempfile
from pathlib import Path

import pytest

adapter = pytest.importorskip("adapter")


class _RecordingPool:
    """Records submissions without executing the runner (process_one is heavy
    and irrelevant to the routing decision under test)."""
    def __init__(self):
        self.submits = 0

    def submit(self, fn):
        self.submits += 1

        class _F:
            def result(self_inner):
                return None
        return _F()


@pytest.fixture
def routed(monkeypatch):
    main_pool = _RecordingPool()
    side_pool = _RecordingPool()
    monkeypatch.setattr(adapter, "_executor", main_pool)
    monkeypatch.setattr(adapter, "_sidechannel_executor", side_pool)
    d = Path(tempfile.mkdtemp())
    monkeypatch.setattr(adapter, "INBOX", d)
    adapter._in_flight.clear()
    return main_pool, side_pool, d


def _write(d, name, payload):
    f = d / name
    f.write_text(json.dumps(payload))
    return f


def test_turn_goes_to_main_pool(routed):
    main_pool, side_pool, d = routed
    f = _write(d, "100_turn.json", {"from": "u", "chat_id": "c", "text": "hi"})
    adapter.submit_inbox_item(f, {})
    assert main_pool.submits == 1 and side_pool.submits == 0


@pytest.mark.parametrize("envelope", [
    {"_cancel": True},          # /stop, /cancel
    {"_btw": True, "text": "x"},  # /btw
    {"_signal": "SIGINT"},      # /sig
    {"_observer": True, "text": "x"},  # observer transcript
])
def test_side_channel_goes_to_dedicated_pool(routed, envelope):
    main_pool, side_pool, d = routed
    f = _write(d, "101_side.json", {"from": "u", "chat_id": "c", **envelope})
    adapter.submit_inbox_item(f, {})
    assert side_pool.submits == 1, "side-channel must use the dedicated pool"
    assert main_pool.submits == 0, "side-channel must NOT consume a turn slot"


def test_cancel_not_starved_by_saturated_turn_pool(routed):
    """The whole point: even when every turn slot is taken, a _cancel still
    gets dispatched on the side pool in the same poll tick."""
    main_pool, side_pool, d = routed
    for i in range(adapter.MAX_PARALLEL):
        adapter.submit_inbox_item(
            _write(d, f"{i:03d}_turn.json", {"from": "u", "chat_id": f"c{i}", "text": "x"}), {})
    assert main_pool.submits == adapter.MAX_PARALLEL  # pool saturated
    adapter.submit_inbox_item(
        _write(d, "999_cancel.json", {"from": "u", "chat_id": "c0", "_cancel": True}), {})
    assert side_pool.submits == 1  # cancel dispatched regardless of turn load
