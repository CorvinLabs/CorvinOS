"""Boot E2E in a SUBPROCESS: the real loader, the real audit chain, a temp CORVIN_HOME.

No LLM is involved anywhere on the plugin load path — there is no ``claude -p``
call to make here, so the "live" proof for this subsystem is a real process
boot: ``bootstrap_all`` runs in a child interpreter (its own registry, its own
module state, its own hash chain under a throwaway ``CORVIN_HOME``) against the
real Corvin-Marketplace checkout, and this test then reads the chain the child
wrote and asserts one ``plugin.loaded`` record per discovered plugin, each
carrying the root-derived ``origin`` and ``source`` (F-P5).

Skips when no marketplace checkout is present. Never touches the live
``.corvin``: ``VOICE_AUDIT_PATH``/``FORGE_ROOT`` are cleared so the chain lands
under the temp home.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from corvin_plugins import bootstrap

_REPO = Path(__file__).resolve().parents[3]

_CHILD = r'''
import json, os, sys
from pathlib import Path
repo = Path(sys.argv[1]); home = Path(os.environ["CORVIN_HOME"])
for p in ("core/plugins", "operator/bridges/shared", "operator/forge", "operator", "operator/license"):
    sys.path.insert(0, str(repo / p))
from corvin_plugins import bootstrap
from corvin_plugins.registry import get_registry
loaded = bootstrap.bootstrap_all(
    tenant_id="_default", corvin_home=home, tenant_config={"spec": {"plugins": {}}},
)
import audit
print(json.dumps({
    "loaded": loaded,
    "registered": get_registry().discover(),
    "audit_path": str(audit.audit_path()),
}))
'''


def test_bootstrap_all_in_a_subprocess_loads_every_marketplace_plugin(tmp_path):
    root = bootstrap._marketplace_root()
    dirs = bootstrap._builtin_plugin_dirs(root)
    if not dirs:
        pytest.skip(f"no Corvin-Marketplace checkout at {root}")
    expected_sources = {
        f"marketplace_root:{d.relative_to(root).as_posix()}" for d in dirs
    }

    home = tmp_path / "corvin_home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("VOICE_AUDIT_PATH", "FORGE_ROOT", "CORVIN_TENANT_ID", "PYTHONPATH")}
    env["CORVIN_HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(_REPO)],
        capture_output=True, text=True, timeout=180, env=env, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    # 1. every discovered plugin loaded AND is registered in the child process
    assert len(out["loaded"]) >= len(dirs), (len(out["loaded"]), len(dirs), proc.stderr[-2000:])
    assert set(out["loaded"]) <= set(out["registered"])

    # 2. the child's chain lives under the temp home, never the live one
    audit_path = Path(out["audit_path"])
    assert audit_path.is_relative_to(home), audit_path
    assert audit_path.is_file(), "the child wrote no audit chain"

    # 3. one plugin.loaded record per plugin, with root-derived provenance
    seen: dict[str, str] = {}
    for line in audit_path.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("event_type") != "plugin.loaded":
            continue
        details = rec.get("details") or {}
        assert details.get("tenant_id") == "_default", details
        seen[details.get("source", "")] = details.get("origin", "")
    missing = expected_sources - set(seen)
    assert not missing, f"no plugin.loaded record for {sorted(missing)[:5]} (+{max(0, len(missing)-5)})"
    assert all(seen[s] == "vetted" for s in expected_sources), {
        s: seen[s] for s in expected_sources if seen[s] != "vetted"
    }
    assert not any(s.startswith("/") for s in seen), "source leaked an absolute path"
