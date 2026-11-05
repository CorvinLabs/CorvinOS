"""RL-5 (refutation 2026-11-05): a worker may not start its own skill-forge / forge MCP server.

Such a server reads its caller persona from the env the worker gives it
(``CORVIN_CALLER_PERSONA=skill-forge python3 -m skill_forge.mcp_server``). Driven through
the real PreToolUse hook process (stdin JSON → exit code 2 = deny), like Claude Code runs it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent / "path_gate.py"


def _run(home: Path, cmd: str) -> tuple[int, str]:
    env = dict(os.environ, CORVIN_HOME=str(home), CORVIN_TENANT_ID="_default",
               XDG_CONFIG_HOME=str(home / "xdg"))
    env.pop("FORGE_ROOT", None)
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stderr


@pytest.mark.parametrize("cmd", [
    "CORVIN_CALLER_PERSONA=skill-forge python3 -m skill_forge.mcp_server < /tmp/req.jsonl",
    "env SKILL_FORGE_PERSONA=forge PYTHONPATH=corvin_operator/skill-forge:corvin_operator/forge "
    "python3 -m skill_forge.mcp_server < /tmp/req.jsonl",
    "cd corvin_operator/skill-forge && CORVIN_CALLER_PERSONA=skill-forge "
    "python3 skill_forge/mcp_server.py < /tmp/r",
    "nohup python3 corvin_operator/forge/forge.py mcp --permission-mode yes &",
])
def test_rl5_worker_cannot_start_the_mcp_server(tmp_path, cmd):
    rc, err = _run(tmp_path, cmd)
    assert rc == 2, (rc, err[-300:])
    assert "may not start the skill-forge / forge MCP server" in err


@pytest.mark.parametrize("cmd", [
    "grep -n caller_persona corvin_operator/skill-forge/skill_forge/mcp_server.py",
    "python3 -m pytest corvin_operator/skill-forge/tests -q",
])
def test_rl5_reading_and_testing_the_server_stay_allowed(tmp_path, cmd):
    rc, err = _run(tmp_path, cmd)
    assert rc == 0, (rc, err[-300:])
