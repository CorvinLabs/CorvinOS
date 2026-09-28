"""The default chain writer must find the forge writer on its own.

Until 2026-09-28 ``service._default_chain_writer`` did ``from forge import
security_events`` and relied on some host having put ``corvin_operator/forge``
on ``sys.path``. Run standalone (``pytest core/task_tracking/tests``) 17 of 50
tests failed with ``No module named 'forge'`` → ``AuditUnavailable`` — every
mutation refused. Proven in a fresh interpreter whose path holds ONLY the repo
root, so nothing else in this process can mask it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_PROG = """
import json, sys
assert not any(p.rstrip('/').endswith('corvin_operator/forge') for p in sys.path), sys.path
from core.task_tracking import service
h = service._default_chain_writer('_default', 'task.created', {'task_id': 't-1'})
print(json.dumps({'hash': h}))
"""


def test_default_chain_writer_imports_forge_without_host_sys_path(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(PYTHONPATH=str(REPO), CORVIN_HOME=str(tmp_path / "home"),
               HOME=str(tmp_path / "userhome"), XDG_CONFIG_HOME=str(tmp_path / "xdg"),
               CORVIN_AUDIT_ANCHOR_KEY=str(tmp_path / "anchor.key"))
    r = subprocess.run([sys.executable, "-c", _PROG], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["hash"]
    chain = tmp_path / "home" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    recs = [json.loads(line) for line in chain.read_text().splitlines()]
    assert any(rec.get("event_type") == "task.created" for rec in recs)
