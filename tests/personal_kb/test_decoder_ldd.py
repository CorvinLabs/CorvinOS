"""The decoder's measured improvements (CONCEPT-0134, PLAN-0973 section 16).

LDD: on the fixed replay corpus the repair round must lower the mean loss and raise the gate pass
rate against the baseline without it (and match the recorded results.json).
LLD: the cached "find first" must return exactly what the uncached algorithm returns.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.personal_kb.decoder_eval import bench_find_first, run_eval

HERE = Path(__file__).parent / "decoder_eval"


def test_repair_round_lowers_the_measured_loss():
    before, after = run_eval.run(0), run_eval.run(1)
    assert after["mean_loss"] < before["mean_loss"]
    assert after["gate_pass_rate"] > before["gate_pass_rate"]
    assert after["ideas_with_plan"] == after["ideas"]
    recorded = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    assert {"before": before, "after": after} == recorded


def test_find_first_cache_is_bit_identical(home):
    res = bench_find_first.run(n=600, calls=12)
    assert res["identical"] is True


def test_cache_lives_on_the_store_not_the_process(home):
    from core.personal_kb import decoder

    assert not hasattr(decoder, "_SIGS")
    a, b = bench_find_first._FakeStore(bench_find_first.build_index(50)), bench_find_first._FakeStore({})
    decoder.find_first(a, next(iter(a.index.values())))
    assert a._sig_cache is not None and b._sig_cache is None
