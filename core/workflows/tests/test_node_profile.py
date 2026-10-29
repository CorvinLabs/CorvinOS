"""ADR-2308 / T-0329 — the per-node measurement stream: what it may hold, how it is written and aged,
what the report says, and that no gate can use it.

Pure file-system tests in the sandboxed CORVIN_HOME of the repo-root conftest; ``forge`` is made
importable for the duration of each test only (production has it on PYTHONPATH).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import pytest

from corvin_workflows import node_profile as np

_FORGE = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "forge")


@pytest.fixture(autouse=True)
def _forge_only_here(monkeypatch, tmp_path):
    before = set(sys.modules)
    monkeypatch.syspath_prepend(_FORGE)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))    # one profile directory per test
    np._last_prune.clear()
    yield
    for name in set(sys.modules) - before:
        if name == "forge" or name.startswith("forge."):
            sys.modules.pop(name, None)


def meta(**kw):
    base = {
        "model_resolution": object(), "class_view": {"type": "agent", "tools": []},
        "run_id": "abc123", "span_id": "spn_0123456789abcdef", "tenant_id": "_default",
        "usage": {"model_id": "claude-haiku-5-5", "cost_usd": 0.01, "input_tokens": 3, "output_tokens": 2,
                  "cache_read_tokens": 100, "cache_write_tokens": 20},
    }
    base.update(kw)
    return base


def day(offset=0):
    return dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=offset)


def at(d):
    return dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone.utc).timestamp()


# ── what an entry may hold ───────────────────────────────────────────────────

def test_an_entry_holds_only_closed_fields_and_is_always_a_proxy():
    e = np.build_entry(meta(item_index=3), "agent|n|<8k|json", "ok", now=1.0)
    assert set(e) == {"ts", "rid", "span_id", "node_class", "model_id", "requested_model_id", "input_tokens", "output_tokens",
                      "cache_read_tokens", "cache_write_tokens", "cost_usd", "status", "signal", "item_index"}
    assert (e["signal"], e["status"], e["item_index"]) == ("proxy", "ok", 3)


@pytest.mark.parametrize("field,evil", [
    ("run_id", "alice@example.com"), ("span_id", "send the report to bob please"),
    ("run_id", "/etc/passwd"), ("run_id", "x" * 500),
])
def test_text_a_person_wrote_never_reaches_an_id_field(field, evil):
    e = np.build_entry(meta(**{field: evil}), "agent|n|<8k|json", "ok")
    assert evil not in json.dumps(e)
    assert e["rid" if field == "run_id" else "span_id"] == ""


def test_a_model_id_or_node_class_outside_the_closed_vocabulary_is_blanked():
    e = np.build_entry(meta(usage={"model_id": "name@corp.example", "cost_usd": -1}), "Free text node", "ok")
    assert e["model_id"] == "" and e["node_class"] == "" and e["cost_usd"] is None


def test_a_non_ok_status_is_recorded_as_error():
    assert np.build_entry(meta(), "agent|n|<8k|json", "anything-else")["status"] == "error"


# ── how it is written ────────────────────────────────────────────────────────

def test_append_writes_one_line_to_the_dated_segment_and_never_rewrites(tmp_path):
    e1 = np.build_entry(meta(), "agent|n|<8k|json", "ok", now=at(day()))
    e2 = np.build_entry(meta(run_id="second"), "agent|n|<8k|json", "ok", now=at(day()))
    seg = np.append("_default", e1)
    first = seg.read_bytes()
    assert seg.name == f"workflow_node_profile.{day().isoformat()}.jsonl"
    np.append("_default", e2)
    assert seg.read_bytes().startswith(first)                      # the earlier line is byte-identical
    assert len(seg.read_text().splitlines()) == 2
    assert (seg.stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW is POSIX")
def test_a_planted_symlink_segment_is_never_followed(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched\n")
    directory = np.profile_dir("_default")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"workflow_node_profile.{day().isoformat()}.jsonl").symlink_to(victim)
    with pytest.raises(OSError):
        np.append("_default", np.build_entry(meta(), "agent|n|<8k|json", "ok", now=at(day())))
    assert victim.read_text() == "untouched\n"


def test_record_writes_for_an_in_scope_spawn_and_nothing_for_an_excluded_one():
    np.record("_default", meta(), 100, "ok")
    np.record("_default", {k: v for k, v in meta().items() if k != "model_resolution"}, 100, "ok")
    rows = np.read_entries("_default")
    assert len(rows) == 1 and rows[0]["node_class"] == "agent|n|<8k|json"


# ── retention ────────────────────────────────────────────────────────────────

def _plant_segments(offsets):
    directory = np.profile_dir("_default")
    directory.mkdir(parents=True, exist_ok=True)
    for off in offsets:
        (directory / f"workflow_node_profile.{day(off).isoformat()}.jsonl").write_text(f'{{"day": {off}}}\n')
    return directory


def test_an_append_prunes_old_segments_once_a_day_from_the_real_today():
    directory = _plant_segments((200, 91, 90, 1))
    np.append("_default", np.build_entry(meta(), "agent|n|<8k|json", "ok", now=at(day(0))))
    names = {p.name for p in directory.glob("workflow_node_profile.*.jsonl")}
    assert names == {f"workflow_node_profile.{day(o).isoformat()}.jsonl" for o in (90, 1, 0)}


def test_a_back_dated_entry_does_not_move_the_cutoff():
    directory = _plant_segments((60,))
    # an entry dated 400 days ago is older than the window: its segment goes, the 60-day one stays
    np.append("_default", np.build_entry(meta(), "agent|n|<8k|json", "ok", now=at(day(400))))
    names = {p.name for p in directory.glob("workflow_node_profile.*.jsonl")}
    assert names == {f"workflow_node_profile.{day(60).isoformat()}.jsonl"}


def test_retention_removes_whole_old_segments_only(tmp_path):
    directory = _plant_segments((200, 91, 90, 1, 0))
    kept_before = {p.name: p.read_bytes() for p in directory.glob("workflow_node_profile.*.jsonl")}
    removed = np.prune(directory, day())
    left = {p.name: p.read_bytes() for p in directory.glob("workflow_node_profile.*.jsonl")}
    assert {p.name for p in removed} == {f"workflow_node_profile.{day(o).isoformat()}.jsonl" for o in (200, 91)}
    assert set(left) == set(kept_before) - {p.name for p in removed}
    assert all(left[n] == kept_before[n] for n in left)             # the survivors are untouched, byte for byte


# ── report and gate filter ───────────────────────────────────────────────────

def test_the_report_says_no_data_underpowered_or_ok_and_never_invents_a_loss():
    assert np.loss_report("_default")["status"] == "no_data"
    for i in range(3):
        np.record("_default", meta(run_id=f"r{i}"), 100, "ok")
    np.record("_default", meta(run_id="bad", usage={"model_id": "claude-haiku-5-5"}), 100, "error")
    rep = np.loss_report("_default", min_runs=30)
    assert rep["status"] == "underpowered" and rep["entries"] == 4 and rep["loss"] is None
    row = rep["classes"][0]
    assert (row["runs"], row["error_rate"], row["underpowered"]) == (4, 0.25, True)
    assert row["mean_cost_usd"] == 0.01 and row["mean_tokens"] == pytest.approx((125 * 3) / 4, abs=0.05)  # rounded to 0.1
    assert np.loss_report("_default", min_runs=2)["status"] == "ok"


def test_a_proxy_entry_is_never_eligible_for_a_gate():
    for i in range(5):
        np.record("_default", meta(run_id=f"r{i}"), 100, "ok")
    entries = np.read_entries("_default")
    assert len(entries) == 5 and all(e["signal"] == "proxy" for e in entries)
    assert np.gate_eligible(entries) == []                          # by construction, whatever the volume
    assert np.gate_eligible([{"signal": "oracle_shadow"}, {"signal": "proxy"}]) == [{"signal": "oracle_shadow"}]


def test_the_module_writes_no_free_text_field_names():
    src = Path(np.__file__).read_text(encoding="utf-8")
    assert re.search(r"\b(prompt|instructions|agent|node_id|message|reason)\b\s*:", src.split("def build_entry")[1]
                     .split("# ── write")[0]) is None
