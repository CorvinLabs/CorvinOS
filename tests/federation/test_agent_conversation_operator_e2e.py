"""Operator participation in a running agent conversation (agent_conversations plugin).

Same real transport and fixtures as ``test_agent_conversation_e2e``: console login + CSRF, real
signed peer turns, real ``spawn_a2a_worker`` gates; only the engine at the very end is a stub.

Proven: an interjection posted mid-turn is written by the moderator (single writer, strictly
increasing ``seq``) BEFORE the next turn and reaches that turn's prompt; one addressed to the peer
is withheld from the local agent's prompt; pause holds the run at a turn boundary and resume
continues it; settings change the next prompt; bad input is refused; the chain carries metadata
only (never the text).
"""
from __future__ import annotations

import time

from tests.federation.test_agent_conversation_e2e import (  # noqa: F401 — fixtures + helpers
    BASE, LOCAL, PEER, _Talk, _conv_events, _details, _start, _wait_done, talk,
)
from tests.federation.test_agent_conversation_e2e import (  # noqa: F401 — talk's own fixtures
    _Ev, _RecordingEngine, _chain_events, console, federation_pair,
)


def _wait_for(pred, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def _get(c, cid):
    return c.get(f"{BASE}/conversations/{cid}").json()


def test_interjection_is_written_before_the_next_turn_and_reaches_its_prompt(talk):
    import threading
    c, h, pair = talk
    _Talk.hold, _Talk.hold_from = threading.Event(), 2
    cid = _start(c, h).json()["conversation_id"]
    _wait_for(lambda: len(_Talk.prompts) >= 2)           # turn 2 is in flight (held)
    r = c.post(f"{BASE}/conversations/{cid}/messages", json={"text": "Prefer snake_case."}, headers=h)
    assert r.status_code == 202 and r.json()["queued"] == 1
    _Talk.hold.set()

    done = _wait_done(c, cid)
    msgs = done["messages"]
    assert [m["seq"] for m in msgs] == list(range(1, len(msgs) + 1))
    op = [m for m in msgs if m["speaker"] == "operator"]
    assert [m["text"] for m in op] == ["Agree on one name for a test fixture.", "Prefer snake_case."]
    after = [m["speaker"] for m in msgs[msgs.index(op[1]) - 1:msgs.index(op[1]) + 2]]
    assert after[1] == "operator" and after[2] in ("local", "peer")
    assert done["turns"] == 4                             # operator lines are not agent turns
    assert any("operator: Prefer snake_case." in p for _, p in _Talk.prompts[2:])

    audit = [e for e in _conv_events(pair) if "operator_message" in str(e.get("event_type"))]
    assert len(audit) == 1
    assert "Prefer snake_case" not in str(audit[0])        # metadata only


def test_a_message_for_the_peer_is_withheld_from_the_local_agent(talk):
    import threading
    c, h, _ = talk
    _Talk.hold, _Talk.hold_from = threading.Event(), 2
    cid = _start(c, h, max_turns=4).json()["conversation_id"]
    _wait_for(lambda: len(_Talk.prompts) >= 2)
    assert c.post(f"{BASE}/conversations/{cid}/messages",
                  json={"text": "SECRET-FOR-PEER", "target": "peer"}, headers=h).status_code == 202
    _Talk.hold.set()
    _wait_done(c, cid)
    local_prompts = [p for m, p in _Talk.prompts if "opus" in m]
    peer_prompts = [p for m, p in _Talk.prompts if "haiku" in m]
    assert not any("SECRET-FOR-PEER" in p for p in local_prompts)
    assert any("SECRET-FOR-PEER" in p for p in peer_prompts)


def test_pause_holds_the_run_at_a_turn_boundary_and_resume_continues(talk):
    import threading
    c, h, _ = talk
    _Talk.hold, _Talk.hold_from = threading.Event(), 2
    cid = _start(c, h, max_turns=4).json()["conversation_id"]
    _wait_for(lambda: len(_Talk.prompts) >= 2)
    assert c.post(f"{BASE}/conversations/{cid}/pause", headers=h).json() == {"paused": True}
    _Talk.hold.set()
    _wait_for(lambda: any(e["event"] == "paused" for e in _get(c, cid)["events"]))
    held = len(_Talk.prompts)
    time.sleep(0.8)
    assert len(_Talk.prompts) == held and _get(c, cid)["paused"] is True

    assert c.post(f"{BASE}/conversations/{cid}/resume", headers=h).json() == {"paused": False}
    done = _wait_done(c, cid)
    assert done["status"] == "completed" and done["turns"] == 4
    assert [e["event"] for e in done["events"]] == ["paused", "resumed"]


def test_stop_works_while_paused(talk):
    c, h, _ = talk
    cid = _start(c, h, max_turns=6, settings={"pace_s": 1}).json()["conversation_id"]
    _wait_for(lambda: len(_Talk.prompts) >= 1)
    c.post(f"{BASE}/conversations/{cid}/pause", headers=h)
    _wait_for(lambda: any(e["event"] == "paused" for e in _get(c, cid)["events"]))
    assert c.post(f"{BASE}/conversations/{cid}/stop", headers=h).json()["stopping"] is True
    done = _wait_done(c, cid)
    assert done["status"] == "stopped" and done["reason"] == "operator_stop"


def test_settings_shape_the_next_prompt_and_are_recorded(talk):
    import threading
    c, h, _ = talk
    _Talk.hold, _Talk.hold_from = threading.Event(), 2
    cid = _start(c, h, max_turns=4,
                 settings={"max_words": 120, "role_notes": {"local": "Be terse."}}
                 ).json()["conversation_id"]
    _wait_for(lambda: len(_Talk.prompts) >= 2)
    assert "at most about 120 words" in _Talk.prompts[0][1]
    assert "Operator's instruction for you: Be terse." in _Talk.prompts[0][1]
    assert "Operator's instruction" not in _Talk.prompts[1][1]    # peer has no note
    r = c.patch(f"{BASE}/conversations/{cid}/settings", json={"settings": {"max_words": 60}}, headers=h)
    assert r.status_code == 202
    _Talk.hold.set()
    done = _wait_done(c, cid)
    assert "at most about 60 words" in _Talk.prompts[2][1]
    assert done["settings"]["max_words"] == 60 and done["settings"]["role_notes"]["local"] == "Be terse."
    assert [e["event"] for e in done["events"]] == ["settings"]


def test_bad_input_is_refused_and_a_finished_conversation_takes_no_messages(talk):
    c, h, _ = talk
    assert _start(c, h, settings={"max_words": 5}).status_code == 400
    assert _start(c, h, settings={"nope": 1}).status_code == 400
    assert _start(c, h, settings={"role_notes": {"local": "x" * 501}}).status_code == 400
    cid = _start(c, h, max_turns=1).json()["conversation_id"]
    _wait_done(c, cid)
    for path, body in (("messages", {"text": "late"}), ("pause", None),
                       ("settings", None)):
        r = (c.patch(f"{BASE}/conversations/{cid}/settings", json={"settings": {"pace_s": 1}}, headers=h)
             if path == "settings" else c.post(f"{BASE}/conversations/{cid}/{path}", json=body, headers=h))
        assert r.status_code == 400, (path, r.text)
    assert c.post(f"{BASE}/conversations/{'0' * 32}/messages", json={"text": "x"}, headers=h).status_code in (400, 404)
    assert c.post(f"{BASE}/conversations/{cid}/messages", json={"text": "x", "target": "bogus"},
                  headers=h).status_code == 422


def test_slash_commands_are_parsed_server_side_and_unknown_ones_are_refused(talk):
    c, h, _ = talk
    table = c.get(f"{BASE}/conversations-commands").json()["commands"]
    assert {t["cmd"] for t in table} == {"/pause", "/resume", "/stop", "/words", "/pace"}
    cid = _start(c, h, max_turns=6, settings={"pace_s": 1}).json()["conversation_id"]
    _wait_for(lambda: len(_Talk.prompts) >= 1)

    def run(line):
        return c.post(f"{BASE}/conversations/{cid}/command", json={"line": line}, headers=h)

    assert run("/pause").status_code == 200 and _get(c, cid)["pending"] >= 0
    assert run("/words 90").status_code == 200
    for bad in ("/ask @mine hi", "/words", "/words abc", "/pause now", "hello"):
        r = run(bad)
        assert r.status_code == 400, (bad, r.text)
    assert run("/resume").status_code == 200
    assert run("/stop").status_code == 200
    done = _wait_done(c, cid)
    assert done["status"] == "stopped" and done["settings"]["max_words"] == 90
