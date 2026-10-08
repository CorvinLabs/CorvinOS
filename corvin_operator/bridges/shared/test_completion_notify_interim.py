"""``completion_notify.send_interim`` (ADR-2236 D3/D7): one intermediate message of a
still-running task goes straight to the outbox the messenger daemons poll — complete,
ordered before the completion, and never as a final message."""
from __future__ import annotations

import importlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


@pytest.fixture()
def cn(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    import completion_notify as m
    importlib.reload(m)
    m.register("t1", channel="discord", chat_id="123456789012345678", sender="u1", label="job")
    return m


def _files(d: Path):
    return sorted(d.glob("*.json"))


def test_an_interim_is_a_normal_message_with_routing_and_provenance(cn, tmp_path):
    out = tmp_path / "out"
    assert cn.send_interim("t1", "Tick 1 arrived", out) is True
    (f,) = _files(out)
    env = json.loads(f.read_text())
    assert env["text"] == "Tick 1 arrived"
    assert env["channel"] == "discord" and env["chat_id"] == "123456789012345678"   # string, never int
    assert "_final" not in env
    assert env["provenance"]["ai_generated"] is True
    assert env["msg_id"].startswith("cn_") and env["msg_id"].count("_i") == 1
    assert stat.S_IMODE(f.stat().st_mode) == 0o600          # routing PII


def test_none_of_several_interims_replaces_another_and_all_sort_before_the_completion(cn, tmp_path):
    out = tmp_path / "out"
    for i in range(4):
        assert cn.send_interim("t1", f"update {i}", out)
    cn.mark_done("t1", text="all done", ok=True)
    assert cn.deliver_ready(out) == 1
    names = [f.name for f in _files(out)]
    assert len(names) == 5
    texts = [json.loads(f.read_text())["text"] for f in _files(out)]
    assert texts[:4] == [f"update {i}" for i in range(4)], texts
    assert "all done" in texts[4]
    assert json.loads(_files(out)[4].read_text())["_final"] is True


def test_only_a_pending_task_may_send_an_interim(cn, tmp_path):
    out = tmp_path / "out"
    assert cn.send_interim("nope", "x", out) is False                   # no such record
    assert cn.send_interim("t1", "   ", out) is False                   # nothing to say
    cn.mark_done("t1", text="done", ok=True)
    assert cn.send_interim("t1", "late", out) is False                  # already ready: the completion owns the rest
    assert not out.exists() or _files(out) == []


def test_whatsapp_gets_its_jid_routing(cn, tmp_path):
    cn.register("t2", channel="whatsapp", chat_id="4912345@c.us", sender="u2", label="job")
    out = tmp_path / "out"
    assert cn.send_interim("t2", "hi", out)
    env = json.loads(_files(out)[0].read_text())
    assert env["to"] == "4912345@c.us" and env["channel"] == "whatsapp"
