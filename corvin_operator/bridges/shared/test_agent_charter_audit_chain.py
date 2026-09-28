"""Round-4 regression: agent-charter events reach THE tenant chain.

Before: ``_REPO = parents[1]`` made the forge path ``corvin_operator/
corvin_operator/forge`` (nonexistent) — any process where ``forge`` was not
already importable (e.g. ``voice/scripts/agent_sunset.py``) had ``_HAS_AUDIT``
False and dropped every charter record silently; the chain path was composed
by hand from a bare ``import paths``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_THIS = Path(__file__).resolve().parent


def test_charter_event_lands_in_tenant_chain_without_forge_preloaded(tmp_path):
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(_THIS)!r})\n"
        "import agent_charter as a\n"
        "assert a._FORGE_PATH.is_dir(), a._FORGE_PATH\n"
        "assert a._HAS_AUDIT\n"
        "a._emit('acme', 'agent.charter_created', agent_id='x', kind='forge_tool')\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["CORVIN_HOME"] = str(tmp_path / "home")
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    chain = tmp_path / "home" / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
    assert chain.is_file(), r.stderr[-2000:]
    assert "agent.charter_created" in chain.read_text("utf-8")
