"""Round-4 regression: path_gate audit records land in THE tenant chain.

Before: ``path_gate.denied`` / ``code.exec_*`` / ``path_gate.self_test_failed``
went to ``<home>/global/forge/audit.jsonl`` (legacy host-wide chain) and the
ACS ``forge.tool_executed`` trace to ``<home>/tenants/<tid>/global/audit.jsonl``
— both composed by hand, neither the chain the tripwire and reports read.
Drives the real hook as a subprocess (stdin payload → exit 2 deny).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_HOOK = Path(__file__).resolve().parent / "path_gate.py"


def _run(tmp_path: Path, extra_env: dict) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({"CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "acme", **extra_env})
    payload = {"tool_name": "Write",
               "tool_input": {"file_path": str(home / "global" / "forge" / "tools" / "x.py"),
                              "content": "x"}}
    return subprocess.run([sys.executable, str(_HOOK)], input=json.dumps(payload),
                          env=env, cwd=tmp_path, capture_output=True, text=True,
                          timeout=120)


def _events(chain: Path) -> list[str]:
    if not chain.is_file():
        return []
    return [json.loads(ln)["event_type"] for ln in chain.read_text("utf-8").splitlines()
            if ln.strip()]


def test_denial_and_worker_trace_land_in_tenant_chain(tmp_path):
    r = _run(tmp_path, {"CORVIN_ACS_WORKER_ID": "w0", "CORVIN_ACS_RUN_ID": "r1",
                        "CORVIN_ACS_TENANT_ID": "acme"})
    assert r.returncode == 2, (r.returncode, r.stderr[-2000:])
    home = tmp_path / "home"
    events = _events(home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl")
    assert "path_gate.denied" in events, (events, r.stderr[-2000:])
    assert "forge.tool_executed" in events, (events, r.stderr[-2000:])
    # (``decision.dialectical`` from bridges/shared/dialectic.py still goes to
    # the legacy file — a separate module, reported cross-area.)
    legacy = _events(home / "global" / "forge" / "audit.jsonl")
    assert "path_gate.denied" not in legacy
    assert not (home / "tenants" / "acme" / "global" / "audit.jsonl").exists()
