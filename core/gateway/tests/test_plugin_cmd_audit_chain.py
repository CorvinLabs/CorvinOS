"""``corvin plugin install`` writes its events to the tenant's hash-chained audit chain.

Until 2026-11-05 ``plugin_cmd.emit_audit_event`` only logged, so a refused vetted
plugin (``plugin.signature_verification_failed``) never reached the chain the
ADR-0232 tripwire verifies. This drives the REAL CLI (``python -m
corvin_gateway.cli plugin install``) in a subprocess against a scratch
``CORVIN_HOME`` and reads the chain back through the real verifier.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_TENANT = "_default"


def _env(home: Path, tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["CORVIN_HOME"] = str(home)
    env["CORVIN_TENANT_ID"] = _TENANT
    env["CORVIN_AUDIT_ANCHOR_KEY"] = str(tmp_path / "anchor.key")
    env.pop("FORGE_ROOT", None)
    env.pop("CORVIN_PLUGIN_TRUST_ANCHORS", None)
    env["PYTHONPATH"] = os.pathsep.join([
        str(_REPO), str(_REPO / "corvin_operator" / "forge"), str(_REPO / "core" / "gateway"),
        str(_REPO / "core" / "plugins"), env.get("PYTHONPATH", ""),
    ])
    return env


def _run(args: list, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], env=env, cwd=str(cwd), capture_output=True,
                          text=True, timeout=120, encoding="utf-8", errors="replace")


def test_unsigned_vetted_plugin_refusal_is_chained(tmp_path):
    home = tmp_path / "corvin_home"
    (home / "tenants" / _TENANT / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / _TENANT / "global" / "tenant.corvin.yaml").write_text(
        "spec:\n  plugins:\n    installed: []\n", encoding="utf-8")
    plugin_dir = tmp_path / "unsigned_vetted"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "id: com.test.unsigned_vetted\nname: Unsigned Vetted\nversion: 1.0.0\n"
        "origin: vetted\nboot_layer: bundled\nplugin_type: data_connector\n", encoding="utf-8")
    env = _env(home, tmp_path)

    r = _run(["-m", "corvin_gateway.cli", "plugin", "install", str(plugin_dir),
              "--no-prompt", "--force", "--tenant", _TENANT], env, tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "signature verification failed" in r.stderr, r.stderr

    chain = home / "tenants" / _TENANT / "global" / "forge" / "audit.jsonl"
    assert chain.is_file(), "no record reached the tenant chain"
    recs = [json.loads(line) for line in chain.read_text(encoding="utf-8").splitlines() if line.strip()]
    refused = [x for x in recs if x.get("event_type") == "plugin.signature_verification_failed"]
    assert len(refused) == 1, [x.get("event_type") for x in recs]
    d = refused[0]["details"]
    assert d["plugin_id"] == "com.test.unsigned_vetted"
    assert d["tenant_id"] == _TENANT and d["origin"] == "vetted" and d["reason"]
    # closed fields: the registered allowlist kept every field and dropped none
    assert "_dropped_fields" not in d, d
    assert refused[0]["severity"] == "WARNING"

    # The real verifier, in a process holding the SAME anchor key the writer used.
    v = _run(["-c",
              "import sys; from pathlib import Path; from forge.security_events import verify_chain; "
              "ok, probs = verify_chain(Path(sys.argv[1])); print(probs); sys.exit(0 if ok else 1)",
              str(chain)], env, tmp_path)
    assert v.returncode == 0, v.stdout + v.stderr
