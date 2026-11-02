"""TEB path gate: Windows spellings of a protected path must be denied.

``_looks_like_path`` required a "/", so a plain ``C:\\...\\forge\\x`` argument was
never inspected, and the name/segment tests were case- and alias-sensitive on a
filesystem that is neither. Runs only on Windows (the spellings mean nothing on
POSIX, where the old behaviour is kept)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teb import HookContext  # noqa: E402
from teb.path_gate_hook import path_gate_pre_hook  # noqa: E402

B = "\\"
HOME = "C:" + B + "Users" + B + "u" + B + ".corvin" + B + "tenants" + B + "t1"


def _denied(path: str) -> bool:
    ctx = HookContext(engine_id="e", tool_name="write_file", args={"path": path}, chat_key="c")
    path_gate_pre_hook(ctx)
    return bool(ctx.denied)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")
@pytest.mark.parametrize("p", [
    HOME + B + "global" + B + "forge" + B + "tool.py",
    HOME + B + "FORGE" + B + "tool.py",
    HOME + B + "AUDIT.JSONL",
    HOME + B + "audit.jsonl.",
    HOME + B + "policy.json::$DATA",
    HOME + B + "Forge." + B + "x.py",
])
def test_windows_spellings_denied(p):
    assert _denied(p)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")
def test_ordinary_windows_path_allowed():
    assert not _denied(HOME + B + "notes" + B + "todo.txt")
