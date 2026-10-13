"""S2 of PLAN-0955 / ADR-2259: the savings meter reads MEASURED turns from the chain.

Every expected number below is computed by hand from the published rate card
(haiku-4-5: $0.001 in / $0.005 out per 1K, cache read 0.1x input;
 sonnet-5:  $0.002 in / $0.010 out per 1K, cache read 0.1x input) — the meter
may never carry a constant "saving".
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.learning import savings_meter as sm

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"
OPUS = "claude-opus-5-5"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc).timestamp()


def _turn(model, *, ts=NOW, i=0, o=0, cw=0, cr=0, turn_id="t"):
    return {
        "ts": ts, "event_type": "os_turn.completed",
        "details": {"turn_id": turn_id, "model": model, "exit_code": 0,
                    "input_tokens": i, "output_tokens": o,
                    "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr},
    }


def _write(path: Path, recs, *, trailing_newline=True):
    text = "\n".join(json.dumps(r) for r in recs)
    path.write_text(text + ("\n" if trailing_newline else ""), encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    sm.reset_cache()
    monkeypatch.setattr(sm, "_epoch_ts", lambda tenant_id: 0.0)
    yield
    sm.reset_cache()


def _read(chain, default=SONNET, **kw):
    return sm.read("_default", default, now=NOW, chain_path=chain, max_age_s=0, **kw)


def test_hand_computed_month_totals(tmp_path):
    chain = tmp_path / "audit.jsonl"
    _write(chain, [
        _turn(HAIKU, i=1000, o=1000, turn_id="a"),        # actual .006  cf .012
        _turn(HAIKU, cr=10000, turn_id="b"),              # actual .001  cf .002
        _turn(SONNET, i=1000, o=1000, turn_id="c"),       # actual .012  cf .012
    ])
    r = _read(chain)
    assert r.counted_turns == 3 and r.total_turns == 3
    assert r.actual_usd == pytest.approx(0.019)
    assert r.counterfactual_usd == pytest.approx(0.026)
    assert r.saved_usd == pytest.approx(0.007)
    assert r.ratio == pytest.approx(0.007 / 0.026)
    assert r.month == "2026-10" and r.priced is True


def test_other_months_and_pre_epoch_turns_are_excluded(tmp_path, monkeypatch):
    chain = tmp_path / "audit.jsonl"
    sep = datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc).timestamp()
    _write(chain, [
        _turn(HAIKU, ts=sep, i=1000, o=1000, turn_id="old"),
        _turn(HAIKU, ts=NOW - 100, i=1000, o=1000, turn_id="before-epoch"),
        _turn(HAIKU, ts=NOW, i=1000, o=1000, turn_id="in"),
    ])
    monkeypatch.setattr(sm, "_epoch_ts", lambda tenant_id: NOW - 50)
    sm.reset_cache()
    r = _read(chain)
    assert r.counted_turns == 1 and r.actual_usd == pytest.approx(0.006)


def test_unpriced_and_tokenless_turns_are_counted_but_never_guessed(tmp_path):
    chain = tmp_path / "audit.jsonl"
    _write(chain, [
        _turn("some-unknown-model", i=5000, o=5000, turn_id="u"),
        _turn(HAIKU, turn_id="empty"),
        _turn(HAIKU, i=1000, o=1000, turn_id="ok"),
    ])
    r = _read(chain)
    assert (r.counted_turns, r.total_turns) == (1, 3)
    assert r.actual_usd == pytest.approx(0.006)


def test_incremental_equals_full_rebuild_and_partial_line_waits(tmp_path):
    chain = tmp_path / "audit.jsonl"
    _write(chain, [_turn(HAIKU, i=1000, o=1000, turn_id="a")])
    first = _read(chain)
    assert first.counted_turns == 1
    # append one complete line and one PARTIAL line (no newline yet)
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_turn(HAIKU, i=1000, o=1000, turn_id="b")) + "\n")
        fh.write(json.dumps(_turn(HAIKU, i=1000, o=1000, turn_id="c"))[:40])
    mid = _read(chain)
    assert mid.counted_turns == 2          # partial line not consumed
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_turn(HAIKU, i=1000, o=1000, turn_id="c"))[40:] + "\n")
    done = _read(chain)
    sm.reset_cache()
    rebuilt = _read(chain)
    assert done.counted_turns == rebuilt.counted_turns == 3
    assert done.saved_usd == pytest.approx(rebuilt.saved_usd)


def test_changing_the_default_model_reprices_without_rescanning(tmp_path):
    chain = tmp_path / "audit.jsonl"
    _write(chain, [_turn(HAIKU, i=1000, o=1000)])
    on_sonnet = _read(chain, SONNET)
    on_opus = _read(chain, OPUS)           # opus-5-5: .004 in / .020 out
    assert on_sonnet.counterfactual_usd == pytest.approx(0.012)
    assert on_opus.counterfactual_usd == pytest.approx(0.024)


def test_a_shrunk_or_replaced_chain_is_rebuilt_not_trusted(tmp_path):
    chain = tmp_path / "audit.jsonl"
    _write(chain, [_turn(HAIKU, i=1000, o=1000, turn_id=str(n)) for n in range(5)])
    assert _read(chain).counted_turns == 5
    _write(chain, [_turn(HAIKU, i=1000, o=1000, turn_id="only")])
    assert _read(chain).counted_turns == 1


def test_unpriced_default_model_yields_no_ratio(tmp_path):
    chain = tmp_path / "audit.jsonl"
    _write(chain, [_turn(HAIKU, i=1000, o=1000)])
    r = _read(chain, "not-a-model")
    assert r.priced is False and r.ratio is None


def test_missing_chain_is_an_empty_reading_not_an_error(tmp_path):
    r = _read(tmp_path / "nope.jsonl")
    assert (r.counted_turns, r.total_turns, r.ratio) == (0, 0, None)


def test_per_call_cost_is_constant_after_first_load(tmp_path):
    chain = tmp_path / "audit.jsonl"
    _write(chain, [_turn(HAIKU, i=1000, o=1000, turn_id=str(n)) for n in range(2000)])
    sm.read("_default", SONNET, now=NOW, chain_path=chain)      # first load
    t0 = time.perf_counter()
    for _ in range(200):
        sm.read("_default", SONNET, now=NOW, chain_path=chain)  # cached (TTL)
    assert (time.perf_counter() - t0) / 200 < 0.002
