"""Adversarial review 2026-11-05 (area LR) — Autonomous Forge (L19) + savings cap (L09).

LR-3  The autopilot's loss signal classifies a grade as an ORGANIC outcome by its
      note prefix ``outcome (`` — and a worker could write that prefix through the
      MCP ``skill_grade`` tool. Three self-awarded "outcome (rejection)" grades
      made the console report a loss signal, and the next autopilot pass forked
      the skill (engine minutes + a Free-tier generation credit).
LR-5  The canary gate re-used the SAME outcome grades at every traffic step: five
      candidate grades at 10 % walked the canary 10 -> 25 -> 50 -> ready in three
      autopilot passes with no new evidence.
LR-4  (open) Resetting the console counting window (POST /model-cost-optimizer/
      usage-epoch, ADR-0760) also resets the Free-tier savings meter, so the
      cap ADR-2259 says has "no reset endpoint" is lifted by that button.

Real boundaries: the console routes over HTTP, the real MCP server dispatch
(``SkillForgeMCPServer`` fed JSON-RPC lines), the real injection + follow-up
grading, the real ``autonomous.tick``. Only the LLM engine is faked.
"""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

import pytest

from test_autonomous_forge_canary_e2e import (  # noqa: E402  (sets sys.path)
    BASE, SKILL, follow_up, forge_client, post, serve, wait_fork,
)


def _mcp_calls(home: Path, persona: str, calls: list[tuple[str, dict]]) -> list[dict]:
    """Run tool calls through the real SkillForge MCP server dispatch."""
    from skill_forge.mcp_server import SkillForgeMCPServer

    lines = [json.dumps({"jsonrpc": "2.0", "id": i + 1, "method": "tools/call",
                         "params": {"name": tool, "arguments": args}})
             for i, (tool, args) in enumerate(calls)]
    out = io.StringIO()
    prev = os.environ.get("CORVIN_CALLER_PERSONA")
    os.environ["CORVIN_CALLER_PERSONA"] = persona
    try:
        srv = SkillForgeMCPServer(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out,
                                  stderr=io.StringIO())
        srv.serve()
    finally:
        if prev is None:
            os.environ.pop("CORVIN_CALLER_PERSONA", None)
        else:
            os.environ["CORVIN_CALLER_PERSONA"] = prev
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_lr3_self_awarded_outcome_notes_are_no_loss_signal(tmp_path):
    with forge_client(tmp_path) as (client, root, home):
        replies = _mcp_calls(home, "assistant", [
            ("skill_grade", {"name": SKILL, "run_id": f"self-{n}", "score": 0.1,
                             "notes": "outcome (rejection) prev_run=self"})
            for n in range(3)
        ])
        assert all(r["result"].get("isError") is False for r in replies), replies

        (row,) = client.get(f"{BASE}/status").json()["skills"]
        assert row["outcome_n"] == 0, row
        assert row["loss_signal"] is False, row

        from skill_creator import autonomous
        forked: list[str] = []
        autonomous.tick(root, fork_fn=lambda r, s, **k: forked.append(s) or {})
        assert forked == [], "the autopilot forked on self-awarded grades"


def test_lr3_real_followup_is_still_a_loss_signal(tmp_path, monkeypatch):
    """Positive control: the organic follow-up grade keeps its outcome prefix.

    A user-scope skill takes ONE organic grade per tenant, signal kind and
    rolling 24 h (``followup_dedupe_key``), so the three rejections are given
    on three different days (inside the 14-day loss window)."""
    real = time.time()
    with forge_client(tmp_path) as (client, root, home):
        for n in range(3):
            monkeypatch.setattr(time, "time", lambda n=n: real - (2 - n) * 2 * 86400)
            serve(home, f"telegram:{n}")
            follow_up(home, f"telegram:{n}", "falsch, das stimmt nicht")
        monkeypatch.setattr(time, "time", lambda: real)
        (row,) = client.get(f"{BASE}/status").json()["skills"]
        assert row["outcome_n"] == 3 and row["loss_signal"] is True, row


def test_lr5_each_traffic_step_needs_new_candidate_evidence(tmp_path):
    with forge_client(tmp_path) as (client, root, home):
        resp = post(client, "/fork", {"skill_id": SKILL, "instruction": "warn on duplicate keys"})
        assert resp.status_code == 202, resp.text
        assert wait_fork(client, resp.json()["run_id"])["status"] == "success"

        # Five outcome grades per variant, written through the canary store's own
        # grading call (the one skill_inject._grade_with_canary uses): the
        # follow-up path takes one organic grade per tenant/kind/24 h for a
        # user-scope skill, which would need five real days here.
        from skill_creator import autonomous
        store = autonomous.canary_store(root)
        for n in range(5):
            store.record_grade(SKILL, variant="candidate", score=0.9, run_id=f"c{n}", kind="outcome")
            store.record_grade(SKILL, variant="live", score=0.1, run_id=f"l{n}", kind="outcome")

        autonomous.tick(root)
        canary = client.get(f"{BASE}/status").json()["canaries"][0]
        assert canary["traffic_percent"] == 25, canary

        # no new grade arrived — the next passes must not escalate on the same five
        autonomous.tick(root)
        autonomous.tick(root)
        canary = client.get(f"{BASE}/status").json()["canaries"][0]
        assert canary["status"] == "canary" and canary["traffic_percent"] == 25, canary
        assert canary["verdict"]["decision"] == "hold", canary["verdict"]


@pytest.mark.xfail(strict=True, reason="LR-4 open: needs an operator decision whether the "
                   "ADR-0760 counting epoch may narrow the ADR-2259 allowance meter")
def test_lr4_counting_window_reset_does_not_lift_the_free_savings_cap(tmp_path, monkeypatch):
    home = tmp_path / "h"
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    import savings_cap
    from core.learning import savings_meter
    from corvin_console import usage_epoch

    savings_meter.reset_cache()
    monkeypatch.setattr(savings_cap, "_tier", lambda: "free")
    chain = Path(savings_meter._chain_path("_default"))
    now = time.time()
    # 200 Haiku turns this month: a large saving against a Sonnet default.
    with chain.open("a", encoding="utf-8") as fh:
        for n in range(200):
            fh.write(json.dumps({"ts": now - 3600 + n, "event_type": "os_turn.completed",
                                 "details": {"model": "claude-haiku-4-5-20251001",
                                             "input_tokens": 200_000, "output_tokens": 20_000}})
                     + "\n")
    default = "claude-sonnet-4-5-20250929"
    before = savings_cap.evaluate("_default", default_model=default, block=True)
    assert before.capped and before.reason == "cap_reached", before

    usage_epoch.set_epoch("_default", reason="review", actor="fp")
    savings_meter.reset_cache()
    after = savings_cap.evaluate("_default", default_model=default, block=True)
    assert after.capped, f"a counting-window reset lifted the cap: {after.reason}"
