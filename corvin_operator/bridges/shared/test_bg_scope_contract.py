"""CLI stream contract for background scopes (ADR-2236 D1, PLAN-0938 P0).

Offline: every committed fixture satisfies the contract the tracker depends on.
Live canary (opt-in, costs a few cents): the REAL claude CLI still emits the same
shape — run it after every CLI update and re-capture fixtures if it fails:

    CORVIN_BGSCOPE_CANARY=1 pytest shared/test_bg_scope_contract.py -k canary
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

CHILD_FIXTURES = ["bash_bg_ok", "bash_bg_fail", "monitor_3lines", "agent_bg",
                  "bash_and_agent_mixed"]


@pytest.mark.parametrize("name", CHILD_FIXTURES)
def test_fixture_satisfies_the_contract(name):
    assert kit.check_stream_contract(kit.load_fixture(name)) == []


def test_turn_without_children_has_exactly_one_result_and_no_task_events():
    ev = kit.load_fixture("plain_no_children")
    assert kit.check_stream_contract(ev, expect_children=False) == []
    assert [e["type"] for e in ev].count("result") == 1
    assert not [e for e in ev if str(e.get("subtype", "")).startswith("task_")]


def test_the_contract_checker_is_not_vacuous():
    """Positive control: a stream stripped of its task events must be reported."""
    ev = [e for e in kit.load_fixture("bash_bg_ok")
          if not str(e.get("subtype", "")).startswith(("task_", "background_"))]
    assert kit.check_stream_contract(ev), "checker accepted a stream with no task events"


def test_monitor_fixture_shows_one_result_per_wakeup():
    """Measured 2026-10-08: a 3-line Monitor yields 1 own-turn result + 4 wake-ups."""
    res = [e for e in kit.load_fixture("monitor_3lines") if e.get("type") == "result"]
    assert len(res) == 5
    assert "origin" not in res[0]
    assert all((r.get("origin") or {}).get("kind") == "task-notification" for r in res[1:])


def test_agent_prompt_is_present_in_task_started_so_the_tracker_must_drop_it():
    """Why ADR-2236 D9 exists: the CLI hands us the sub-agent prompt text."""
    started = [e for e in kit.load_fixture("agent_bg") if e.get("subtype") == "task_started"]
    assert started and started[0].get("task_type") == "local_agent"
    assert started[0].get("prompt")


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("CORVIN_BGSCOPE_CANARY") != "1",
                    reason="opt-in live canary (real API call): set CORVIN_BGSCOPE_CANARY=1")
@pytest.mark.skipif(shutil.which(os.environ.get("CORVIN_CLAUDE_BIN") or "claude") is None,
                    reason="no claude binary")
def test_canary_real_cli_still_matches_the_contract():
    import capture_bgscope_fixture as cap  # noqa: PLC0415

    prompt = ("Starte mit Bash und run_in_background=true: sleep 6 && echo FERTIG. "
              "Antworte danach nur 'gestartet'.")
    events = cap.scrub(cap.capture(prompt, timeout=90))
    assert kit.check_stream_contract(events) == [], (
        "claude CLI event schema drifted — re-capture fixtures with "
        "tests/capture_bgscope_fixture.py and update the tracker (ADR-2236)")
