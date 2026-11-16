"""The path gate refuses agent WRITES to the personal store and its device keys, and leaves the
source package alone (ADR-2325 item 5, AC-S9). Real hook process, crafted tool calls."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[2] / "corvin_operator" / "voice" / "hooks" / "path_gate.py"


def _decide(tool: str, tool_input: dict, env: dict) -> str:
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
                       capture_output=True, text=True, timeout=60, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    return "deny" if (r.returncode == 2 or '"deny"' in out) else "allow"


@pytest.fixture()
def env(home):
    import os
    return dict(os.environ)


OID = "01ABCDEFGHJKMNPQRSTVWXYZ01"


@pytest.mark.parametrize("rel", [f"tenants/_default/personal_kb/{OID}/objects/x.enc",
                                 f"tenants/_default/personal_kb/{OID}/owner.json",
                                 "tenants/_default/personal_kb/_index.json",
                                 "tenants/_default/personal_kb/_revoked/0123456789abcdef",
                                 f"secrets/personal_kb/_default/{OID}.key"])
def test_writes_to_the_store_are_refused(env, home, rel):
    assert _decide("Write", {"file_path": str(home / rel), "content": "x"}, env) == "deny"


def test_shell_delete_of_the_store_is_refused(env, home):
    assert _decide("Bash", {"command": f"rm -rf {home}/tenants/_default/personal_kb"}, env) == "deny"


@pytest.mark.parametrize("rel", ["core/personal_kb/store.py", "tests/personal_kb/test_x.py"])
def test_source_package_is_not_a_store(env, tmp_path, rel):
    assert _decide("Write", {"file_path": str(tmp_path / "repo" / rel), "content": "x"}, env) == "allow"
