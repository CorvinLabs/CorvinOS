#!/usr/bin/env python3
"""A bridge turn's task record says whether the operator sent it.

Through the real ``call_claude_streaming`` path with a fake ``claude`` binary
(the engine subprocess boundary), in a sandboxed CORVIN_HOME:

  1. the operator's uid is on the channel whitelist → the task record carries
     ``input.from_operator: true``; a stranger's turn carries ``false``;
  2. the uid itself is never written into the record;
  3. the console's task list (task_sources) titles the operator's turn with
     its instruction and keeps the stranger's turn untitled.

Run: python3 test_adapter_task_operator_flag.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[2] / "core" / "console"))
sys.path.insert(0, str(ROOT.parents[2]))

from test_adapter_stream_idle import _fresh_adapter, _make_fake_claude  # noqa: E402

OWNER = "owner-uid-4711"
_ENV = ("PATH", "ADAPTER_INBOX", "ADAPTER_OUTBOX", "CORVIN_HOME", "VOICE_AUDIT_PATH",
        "ADAPTER_BRIDGES_DIR", "CLAUDE_CONFIG_DIR")


def _tasks(home: Path, chat: str) -> list[dict]:
    d = home / "tenants" / "_default" / "sessions" / "voice" / "discord" / chat / "tasks"
    return [json.loads(p.read_text()) for p in d.glob("*.json")]


def test_operator_flag_reaches_the_task_list() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="adapter-opflag-"))
    saved = {k: os.environ.get(k) for k in _ENV}
    try:
        bin_dir = _make_fake_claude(tmp, "ok")
        home = tmp / "corvinOSHome"
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        os.environ["ADAPTER_INBOX"] = str(tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
        os.environ["CLAUDE_CONFIG_DIR"] = str(tmp / "claude")
        bridges = tmp / "bridges"
        (bridges / "discord").mkdir(parents=True)
        (bridges / "discord" / "settings.json").write_text(json.dumps({"whitelist": [OWNER]}))
        os.environ["ADAPTER_BRIDGES_DIR"] = str(bridges)
        # the console reads the daemon's canonical file under CORVIN_HOME
        (home / "bridges" / "discord").mkdir(parents=True)
        (home / "bridges" / "discord" / "settings.json").write_text(json.dumps({"whitelist": [OWNER]}))
        adapter = _fresh_adapter()

        adapter.call_claude_streaming("rebuild the release notes", channel="discord",
                                      chat_key="op_chat", sender=OWNER, msg_id="m1")
        adapter.call_claude_streaming("private words of a guest", channel="discord",
                                      chat_key="guest_chat", sender="stranger-9", msg_id="m2")

        [op] = _tasks(home, "op_chat")
        [guest] = _tasks(home, "guest_chat")
        assert op["input"].get("from_operator") is True, op["input"]
        assert guest["input"].get("from_operator") is False, guest["input"]
        for rec in (op, guest):
            assert OWNER not in json.dumps(rec) and "stranger-9" not in json.dumps(rec), rec

        from corvin_console import task_sources  # noqa: PLC0415

        task_sources._AGG_CACHE.clear()
        by_id = {r["id"]: r for r in task_sources.collect("_default")["records"]}
        assert by_id[f"chat:{op['task_id']}"]["title"] == "rebuild the release notes"
        assert "private" not in by_id[f"chat:{guest['task_id']}"]["title"]
        print("PASS: from_operator stamped by the adapter and honoured by the task list")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    test_operator_flag_reaches_the_task_list()
