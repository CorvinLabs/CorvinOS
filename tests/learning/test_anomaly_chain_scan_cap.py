"""The anomaly monitor's chain readers are byte-capped.

Without ``max_scan_bytes`` a rare needle makes ``last_chain_records`` walk the whole tenant chain
in pure Python (measured on a 112 MB chain: ~68 s per scan, every ``ANOMALY_CHECK_S``), which
starved the console's event loop of the GIL until it stopped accepting connections. These tests
pin the cap so it cannot silently regress to an unbounded scan.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.learning import anomaly_monitor as am


class _FakeSe:
    def __init__(self):
        self.calls: list[dict] = []

    def last_chain_records(self, chain_path, **kwargs):
        self.calls.append({"chain_path": chain_path, **kwargs})
        return []


class _FakeFp:
    @staticmethod
    def tenant_audit_chain(tenant_id):
        return Path("/nonexistent") / tenant_id / "audit.jsonl"


@pytest.fixture
def fake_se(monkeypatch):
    import core.deployment.audit_sink as audit_sink

    se = _FakeSe()
    monkeypatch.setattr(audit_sink, "_forge", lambda: (se, _FakeFp()))
    return se


@pytest.mark.parametrize("reader", [am._chained_executions, am._chained_config_updates])
def test_chain_reader_passes_a_byte_cap(fake_se, reader):
    assert reader("_t") == []
    assert len(fake_se.calls) == 1
    cap = fake_se.calls[0].get("max_scan_bytes")
    assert cap is not None and cap == am.CHAIN_SCAN_MAX_BYTES
    assert 0 < cap <= 64 * 1024 * 1024


def test_cap_bounds_the_real_reader(tmp_path):
    """The real reader honours the cap: a needle older than the scanned tail is not seen."""
    from core.deployment.audit_sink import _forge

    se, _fp = _forge()
    chain = tmp_path / "audit.jsonl"
    old = '{"event": "skill.executed", "ts": 1.0, "details": {}}\n'
    filler = '{"event": "noise", "ts": 2.0, "pad": "' + "x" * 200 + '"}\n'
    chain.write_text(old + filler * 200, encoding="utf-8")
    needles = ('"skill.executed"',)
    assert se.last_chain_records(chain, max_records=10, max_bytes=None, needles=needles,
                                 include_history=False)
    assert se.last_chain_records(chain, max_records=10, max_bytes=None, needles=needles,
                                 include_history=False, max_scan_bytes=4096) == []
