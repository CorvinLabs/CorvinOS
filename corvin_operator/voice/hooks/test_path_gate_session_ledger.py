"""L10 path gate denies every worker write to a chat's session ledger (ADR-2102).

The ledger sits in the worker's cwd so the worker can READ its own history.
A WRITE would let a prompt-injected worker forge "user" turns that are
re-supplied into every later system prompt, plant a fake /new fence, or delete
lines (review R4-5). Reads stay allowed.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("CORVIN_HOME", tempfile.mkdtemp(prefix="pg-ledger-"))
sys.path.insert(0, str(HERE))
import path_gate  # noqa: E402

WD = Path(os.environ["CORVIN_HOME"]) / "tenants/_default/sessions/voice/telegram/123"
LEDGER = WD / ".corvin-ledger" / "ledger.jsonl"


def _bash(cmd):
    return path_gate.check({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(WD)})


def test_write_edit_and_bash_writes_are_denied():
    assert path_gate.check({"tool_name": "Write", "tool_input": {"file_path": str(LEDGER), "content": "x"}})[0] is False
    assert path_gate.check({"tool_name": "Edit", "tool_input": {"file_path": str(LEDGER),
                                                                "old_string": "a", "new_string": "b"}})[0] is False
    for cmd in (f"echo forged >> {LEDGER}",
                "cd " + str(WD) + " && echo '{\"kind\":\"turn\"}' >> .corvin-ledger/ledger.jsonl",
                f"rm {LEDGER}", f"truncate -s0 {LEDGER}",
                f"sed -i 's/a/b/' {LEDGER}"):
        assert _bash(cmd)[0] is False, cmd


def test_reading_the_ledger_is_allowed():
    assert _bash(f"grep -n 'release' {LEDGER}")[0] is True
    assert path_gate.check({"tool_name": "Read", "tool_input": {"file_path": str(LEDGER)}})[0] is True


def test_counters_sidecar_is_protected_too():
    assert path_gate.is_protected_path(LEDGER.parent / "counters.json")
