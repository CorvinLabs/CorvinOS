"""ScopeTracker + normaliser tests (ADR-2236 D1/D3/D9, PLAN-0938 P1, T-0071).

The reducer is replayed with the REAL captured streams (tests/fixtures/bgscope)
at their captured timestamps, so what is asserted is the behaviour on what the
claude CLI actually emits — not on hand-made events.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bg_scope as bgs  # noqa: E402
import bgscope_testkit as kit  # noqa: E402


def replay(name: str, *, drop=lambda e: False, tracker=None):
    """Feed a fixture through a tracker; returns (tracker, transitions, result classes)."""
    tr = tracker or bgs.ScopeTracker(scope_id="t", tenant_id="_default")
    transitions, classes = [], []
    for e in kit.load_fixture(name):
        if drop(e):
            continue
        now = e.get("_t", 0.0)
        if e.get("type") == "assistant":
            for b in (e.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tr.note_tool_use(b.get("id", ""), b.get("name", ""))
        elif e.get("type") == "result":
            classes.append(tr.note_result(e))
        else:
            transitions += tr.feed(e, now)
    return tr, transitions, classes


def kinds(ts):
    return [t.kind for t in ts]


# ------------------------------------------------------------ real streams ---

def test_bash_child_lifecycle():
    tr, ts, cls = replay("bash_bg_ok")
    assert kinds(ts) == ["child_started", "child_finished", "all_children_done"]
    started, finished = ts[0].child, ts[1].child
    assert (started.kind, started.task_type) == ("bash", "local_bash")
    assert finished.state == bgs.COMPLETED and finished.exit_code == 0
    assert finished.ended_at - finished.started_at == pytest.approx(8.0, abs=0.1)
    assert tr.open_children == []
    assert cls == ["user", "wakeup"]


def test_failed_child_carries_its_exit_code():
    _, ts, _ = replay("bash_bg_fail")
    fin = [t.child for t in ts if t.kind == "child_finished"][0]
    assert (fin.state, fin.exit_code) == (bgs.FAILED, 3)


def test_monitor_is_told_apart_from_bash_by_its_tool_name():
    """Both are task_type local_bash; only the tool_use id correlation separates them."""
    tr, ts, cls = replay("monitor_3lines")
    child = [t.child for t in ts if t.kind == "child_started"][0]
    assert child.task_type == "local_bash" and child.kind == "monitor"
    assert cls == ["user", "wakeup", "wakeup", "wakeup", "wakeup"]
    assert tr.wakeups == 4 and tr.results_seen == 5


def test_agent_child_and_the_prompt_is_never_kept():
    tr, ts, _ = replay("agent_bg")
    child = [t.child for t in ts if t.kind == "child_started"][0]
    assert (child.kind, child.task_type) == ("agent", "local_agent")
    blob = repr(tr.all_children) + repr(ts)
    assert "Zaehle bis 3" not in blob, "sub-agent prompt leaked into tracker state"
    assert not hasattr(child, "prompt")


def test_mixed_children_peak_and_agent_ending_before_the_first_result():
    tr, ts, cls = replay("bash_and_agent_mixed")
    assert tr.peak_open == 2
    assert sorted(t.child.kind for t in ts if t.kind == "child_started") == ["agent", "bash"]
    states = {t.child.kind: t.child.state for t in ts if t.kind == "child_finished"}
    assert states == {"agent": bgs.COMPLETED, "bash": bgs.FAILED}
    assert kinds(ts).count("all_children_done") == 1 and ts[-1].kind == "all_children_done"
    assert cls == ["user", "wakeup", "wakeup"]


def test_turn_without_children_produces_nothing():
    tr, ts, cls = replay("plain_no_children")
    assert ts == [] and tr.all_children == [] and cls == ["user"]


# ------------------------------------------------------------- robustness ---

def test_dropped_notification_is_healed_by_the_next_snapshot():
    """D1: background_tasks_changed is a full snapshot — a missed delta must not leave
    the scope 'running' forever."""
    tr, ts, _ = replay("bash_bg_ok", drop=lambda e: e.get("subtype") in ("task_updated", "task_notification"))
    assert "healed" in kinds(ts)
    healed = [t.child for t in ts if t.kind == "healed"][0]
    assert healed.state == bgs.UNKNOWN
    assert tr.open_children == [] and kinds(ts)[-1] == "all_children_done"


def test_dropped_notification_keeps_the_state_task_updated_reported():
    """task_updated says HOW it ended (no exit code); losing the notification must not
    downgrade a known `completed` to `unknown`."""
    tr, ts, _ = replay("bash_bg_ok", drop=lambda e: e.get("subtype") == "task_notification")
    fin = [t.child for t in ts if t.kind in ("child_finished", "healed")]
    assert len(fin) == 1 and fin[0].state == bgs.COMPLETED and fin[0].exit_code is None
    assert "healed" not in kinds(ts) and tr.open_children == []


def test_finalize_closes_a_provisional_end_when_nothing_else_arrives():
    tr = bgs.ScopeTracker()
    tr.feed({"type": "system", "subtype": "task_started", "task_id": "a", "task_type": "local_bash"}, 0)
    tr.feed({"type": "system", "subtype": "task_updated", "task_id": "a",
             "patch": {"status": "failed"}}, 5)
    assert len(tr.open_children) == 1                 # provisional only
    out = tr.finalize(6)
    assert kinds(out) == ["child_finished", "all_children_done"]
    assert tr.all_children[0].state == bgs.FAILED and tr.all_children[0].ended_at == 5
    assert tr.finalize(7) == []


def test_late_notification_after_a_terminal_state_only_adds_the_exit_code():
    tr = bgs.ScopeTracker()
    tr.feed({"type": "system", "subtype": "task_started", "task_id": "a", "task_type": "local_bash"}, 0)
    tr.feed({"type": "system", "subtype": "task_notification", "task_id": "a",
             "status": "failed", "summary": "failed"}, 5)
    out = tr.feed({"type": "system", "subtype": "task_notification", "task_id": "a",
                   "status": "failed", "summary": "failed with exit code 9"}, 6)
    assert out == [] and tr.all_children[0].exit_code == 9


def test_end_all_closes_what_the_exited_process_left_running():
    """The CLI is gone, so is every child it owned. `finalize` alone leaves a running
    child running (the cap message must still be able to name it); end_all closes it."""
    tr = bgs.ScopeTracker()
    tr.feed({"type": "system", "subtype": "task_started", "task_id": "a", "task_type": "local_bash"}, 0)
    assert tr.finalize(5) == [] and len(tr.open_children) == 1
    named = list(tr.open_children)                       # what a cap message would quote
    out = tr.end_all(6)
    assert kinds(out) == ["healed", "all_children_done"]
    assert tr.open_children == [] and tr.all_children[0].state == bgs.UNKNOWN
    assert [c.kind for c in named] == ["bash"]
    assert tr.end_all(7) == []


def test_duplicate_events_are_idempotent():
    tr = bgs.ScopeTracker()
    evs = [e for e in kit.load_fixture("bash_bg_ok") if e.get("type") == "system"]
    out = []
    for e in evs + evs:
        out += tr.feed(e, e["_t"])
    assert kinds(out).count("child_started") == 1
    assert kinds(out).count("child_finished") == 1
    assert kinds(out).count("all_children_done") == 1


def test_snapshot_announcing_a_child_before_task_started_does_not_double_count():
    tr = bgs.ScopeTracker()
    tr.note_tool_use("tu1", "Monitor")
    snap = {"type": "system", "subtype": "background_tasks_changed",
            "tasks": [{"task_id": "a", "task_type": "local_bash", "description": "d"}]}
    started = {"type": "system", "subtype": "task_started", "task_id": "a", "run_id": "r",
               "tool_use_id": "tu1", "task_type": "local_bash", "description": "d2",
               "is_backgrounded": True}
    first = tr.feed(snap, 1.0)
    second = tr.feed(started, 1.0)
    # announced ONCE, only after task_started identified the tool — never as a bare 'bash'
    assert first == [] and kinds(second) == ["child_started"]
    assert second[0].child.kind == "monitor" and second[0].child.run_id == "r"
    (child,) = tr.open_children
    assert child.kind == "monitor" and child.description == "d2"


def test_a_child_only_seen_in_a_snapshot_is_announced_on_the_next_event_or_flush():
    snap = {"type": "system", "subtype": "background_tasks_changed",
            "tasks": [{"task_id": "a", "task_type": "local_agent", "description": "d"}]}
    tr = bgs.ScopeTracker()
    assert tr.feed(snap, 1.0) == []
    nxt = tr.feed({"type": "system", "subtype": "background_tasks_changed",
                   "tasks": snap["tasks"]}, 1.5)
    assert kinds(nxt) == ["child_started"] and nxt[0].child.kind == "agent"
    tr2 = bgs.ScopeTracker()
    tr2.feed(snap, 1.0)
    fl = tr2.flush()
    assert kinds(fl) == ["child_started"] and tr2.flush() == []


def test_a_snapshot_racing_ahead_of_task_started_does_not_kill_a_fresh_child():
    tr = bgs.ScopeTracker()
    tr.feed({"type": "system", "subtype": "task_started", "task_id": "a",
             "task_type": "local_bash", "description": "x"}, 10.0)
    out = tr.feed({"type": "system", "subtype": "background_tasks_changed", "tasks": []}, 10.2)
    assert out == [] and len(tr.open_children) == 1          # inside SNAPSHOT_GRACE_S
    out = tr.feed({"type": "system", "subtype": "background_tasks_changed", "tasks": []}, 12.0)
    assert kinds(out) == ["healed", "all_children_done"]


def test_foreground_subagent_is_not_a_background_child():
    tr = bgs.ScopeTracker()
    out = tr.feed({"type": "system", "subtype": "task_started", "task_id": "f",
                   "task_type": "local_agent", "is_backgrounded": False}, 1.0)
    assert out == [] and tr.open_children == []


@pytest.mark.parametrize("status,state", [("completed", bgs.COMPLETED), ("failed", bgs.FAILED),
                                          ("killed", bgs.UNKNOWN), ("stopped", bgs.UNKNOWN)])
def test_every_non_running_status_is_terminal(status, state):
    tr = bgs.ScopeTracker()
    tr.feed({"type": "system", "subtype": "task_started", "task_id": "a", "task_type": "local_bash"}, 0)
    tr.feed({"type": "system", "subtype": "task_notification", "task_id": "a",
             "status": status, "summary": "x"}, 1)
    assert tr.open_children == [] and tr.all_children[0].state == state


@pytest.mark.parametrize("junk", [
    None, 5, "x", [], {}, {"type": "system"}, {"type": "system", "subtype": "task_started"},
    {"type": "system", "subtype": "task_updated", "patch": None},
    {"type": "system", "subtype": "task_notification", "task_id": 7, "status": None, "summary": None},
    {"type": "system", "subtype": "background_tasks_changed", "tasks": "nope"},
    {"type": "system", "subtype": "background_tasks_changed", "tasks": [None, 3, {"task_id": ""}]},
    {"type": "system", "subtype": "something_new", "task_id": "z"},
])
def test_malformed_or_unknown_events_never_raise(junk):
    tr = bgs.ScopeTracker()
    tr.feed(junk, 1.0)            # must not raise
    assert tr.open_children == []


def test_oldest_open_age_and_exit_code_parser():
    tr = bgs.ScopeTracker()
    for tid, t0 in (("a", 100.0), ("b", 160.0)):
        tr.feed({"type": "system", "subtype": "task_started", "task_id": tid,
                 "task_type": "local_bash"}, t0)
    assert tr.oldest_open_age(200.0) == pytest.approx(100.0)
    assert bgs.parse_exit_code('X failed with exit code 3') == 3
    assert bgs.parse_exit_code("completed (exit code 0)") == 0
    assert bgs.parse_exit_code("no code") is None and bgs.parse_exit_code(None) is None


def test_classify_result():
    assert bgs.classify_result({"origin": {"kind": "task-notification"}}) == "wakeup"
    assert bgs.classify_result({"origin": {"kind": "other"}}) == "user"
    assert bgs.classify_result({}) == "user"


def test_safe_description_scrubs_and_truncates():
    d = bgs.safe_description("run\x00 as bob@example.com\nwith sk-abcdefghijklmnop1234 " + "word " * 60)
    assert "bob@example.com" not in d and "sk-abcdefghijklmnop1234" not in d
    assert "\n" not in d and "\x00" not in d
    assert len(d) <= bgs.DESCRIPTION_MAX and d.endswith("…")
    assert bgs.safe_description("a" * 40 + "B" * 40) == "<token>"   # 80-char opaque blob
    assert bgs.safe_description("sleep 25 && echo ok") == "sleep 25 && echo ok"


# ------------------------------------------------------------- normaliser ---

def test_engine_normaliser_surfaces_background_events():
    from agents.claude_code import ClaudeCodeEngine  # noqa: PLC0415

    seen = {}
    for e in kit.load_fixture("bash_and_agent_mixed"):
        for ev in ClaudeCodeEngine._normalise_all({k: v for k, v in e.items() if k != "_t"}):
            seen.setdefault(ev.type, 0)
            seen[ev.type] += 1
    assert seen["bg_started"] == 2 and seen["bg_finished"] == 2
    assert seen["bg_updated"] == 2 and seen["bg_snapshot"] >= 3
    assert seen["turn_completed"] == 3
    # init is still a plain session_started, repeated per wake-up turn
    assert seen["session_started"] == 3          # 1 + one per wake-up turn


def test_normaliser_leaves_unrelated_system_events_alone():
    from agents.claude_code import ClaudeCodeEngine  # noqa: PLC0415

    assert ClaudeCodeEngine._normalise_all({"type": "system", "subtype": "hook_started"}) == []
    assert ClaudeCodeEngine._normalise_all({"type": "rate_limit_event"}) == []


def test_audit_events_are_registered_in_both_places():
    sys.path.insert(0, str(HERE.parents[1] / "forge"))
    from forge import security_events as sec  # noqa: PLC0415

    for ev in bgs.AUDIT_EVENTS:
        assert ev in sec.EVENT_SEVERITY, ev
        assert ev in sec._EVENT_ALLOWLIST, ev
    # No free-text carrier may be allowlisted (D9)
    for ev in bgs.AUDIT_EVENTS:
        assert not (sec._EVENT_ALLOWLIST[ev] & {"description", "prompt", "summary", "output", "text"}), ev


# ------------------------------------------------- status lines (D7, D9) ---

def _tr(kind, child, open_n):
    return bgs.Transition(kind, child, open_n)


def _child(**kw):
    base = dict(task_id="a", kind="bash", task_type="local_bash", description="Sleep 8 seconds",
                started_at=0.0)
    base.update(kw)
    return bgs.Child(**base)


def test_status_lines_say_what_happened_from_the_events_not_from_model_prose():
    started = bgs.status_line(_tr("child_started", _child(), 1))
    assert started == "⏳ Background shell command started: Sleep 8 seconds — 1 running"
    ok = bgs.status_line(_tr("child_finished", _child(state=bgs.COMPLETED, exit_code=0), 0))
    assert ok == "✅ Background shell command finished: Sleep 8 seconds — all background work is done"
    more = bgs.status_line(_tr("child_finished", _child(state=bgs.COMPLETED, exit_code=0), 2))
    assert more.endswith("— 2 still running")
    bad = bgs.status_line(_tr("child_finished", _child(state=bgs.FAILED, exit_code=3), 0))
    assert bad.startswith("❌ Background shell command failed (exit 3)")
    cut = bgs.status_line(_tr("child_finished", _child(state=bgs.UNKNOWN), 0))
    assert cut.startswith("■ Background shell command ended")
    assert bgs.status_line(_tr("all_children_done", None, 0)) == ""
    assert bgs.status_line(_tr("healed", None, 0)) == ""


def test_status_lines_scrub_the_description_and_name_each_kind():
    line = bgs.status_line(_tr("child_started", _child(
        kind="monitor", description="tail -f for bob@example.com sk-abcdefghijklmnop1234"), 1))
    assert "bob@example.com" not in line and "sk-abcdefghijklmnop1234" not in line
    assert "Background monitor started" in line
    assert "sub-agent" in bgs.status_line(_tr("child_started", _child(kind="agent"), 1))
    assert "workflow" in bgs.status_line(_tr("child_started", _child(kind="workflow"), 1))


def test_every_real_fixture_produces_a_status_line_per_child_transition():
    for name, n_children in (("bash_bg_ok", 1), ("monitor_3lines", 1), ("agent_bg", 1),
                             ("bash_and_agent_mixed", 2)):
        _, ts, _ = replay(name)
        lines = [bgs.status_line(t) for t in ts if t.kind in ("child_started", "child_finished")]
        assert len(lines) == 2 * n_children and all(lines), (name, lines)
        assert lines[-1].endswith("all background work is done"), (name, lines[-1])


def test_children_snapshot_is_what_a_resumed_attempt_is_told():
    tr = bgs.ScopeTracker()
    tr.feed({"type": "system", "subtype": "task_started", "task_id": "a", "task_type": "local_bash",
             "description": "run as bob@example.com"}, 100.0)
    snap = bgs.children_snapshot(tr, now=700.0)
    assert snap == [{"kind": "bash", "age_s": 600, "description": "run as <email>"}]
    tr.feed({"type": "system", "subtype": "task_notification", "task_id": "a",
             "status": "completed", "summary": "x"}, 701.0)
    assert bgs.children_snapshot(tr, now=702.0) == []


# ----------------------------------------- review round 2: closing, died, mentions ---

def test_closing_line_is_derived_from_the_events_only():
    ok = [{"kind": "bash", "state": "completed", "exit_code": 0}]
    assert bgs.closing_line(ok) == "✅ Background work finished: 1 task done."
    mixed = ok + [{"kind": "agent", "state": "failed", "exit_code": 2}, {"kind": "bash", "state": "completed"}]
    assert bgs.closing_line(mixed) == "❌ Background work finished: 1 of 3 tasks failed."


def test_died_message_names_what_was_lost_and_never_a_description():
    kids = [_child(kind="monitor", description="tail -f /var/log/secret.log"), _child(task_id="b")]
    msg = bgs.died_message(kids, partial="last words")
    assert "ended while 2 background tasks were still running (bash, monitor)" in msg
    assert "incomplete" in msg and "Last update:\nlast words" in msg
    assert "secret.log" not in msg
    one = bgs.died_message([_child()])
    assert "1 background task was still running" in one and "Last update" not in one


def test_a_description_cannot_ping_a_whole_channel():
    d = bgs.safe_description("deploy @everyone @here <@123456789> <#42> <@&7> ok")
    for raw in ("@everyone", "@here", "<@123456789>", "<#42>", "<@&7>"):
        assert raw not in d, d
    assert "everyone" in d and "123456789" in d          # still readable, just inert


def test_first_started_at_is_the_scope_clock():
    tr = bgs.ScopeTracker()
    assert tr.first_started_at is None
    tr.feed({"type": "system", "subtype": "task_started", "task_id": "a", "task_type": "local_bash"}, 10.0)
    tr.feed({"type": "system", "subtype": "task_started", "task_id": "b", "task_type": "local_bash"}, 20.0)
    assert tr.first_started_at == 10.0
    snap = bgs.ScopeTracker()
    snap.feed({"type": "system", "subtype": "background_tasks_changed",
               "tasks": [{"task_id": "x", "task_type": "local_agent"}]}, 5.0)
    assert snap.first_started_at == 5.0

