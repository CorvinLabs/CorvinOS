"""The licence layer's chain writer does not depend on which file owns ``paths``.

``forge/forge/paths.py``, ``bridges/shared/paths.py`` and ``cowork/lib/paths.py`` all load under
the bare name ``paths``; the cowork one has no ``tenant_audit_chain``. In a process that loaded
it first, ``from paths import tenant_audit_chain`` raised ImportError inside the licence layer's
try-blocks: ``license.capability_decision`` records were not written and the chain-DNA seeding
was skipped, with nothing in the log.

Each case runs in a FRESH interpreter (module identity is the subject), with a sandboxed home
and anchor key, imports the cowork ``paths`` first — the positive control asserts it really is
the one without the resolver — and then goes through the production functions. The record is
read back from the tenant chain file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

_CHILD = r'''
import json, os, sys
from pathlib import Path

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "corvin_operator" / "cowork" / "lib"))
import paths                                            # the cowork file wins the bare name
assert paths.__file__.replace(os.sep, "/").endswith("cowork/lib/paths.py"), paths.__file__
assert not hasattr(paths, "tenant_audit_chain")         # positive control: the colliding module
cowork_paths = paths

from corvin_operator.license import capability_api as api
from corvin_operator.license import validator, shard_verifier

write_event, tenant_audit_chain = api._chain_writer()
chain = Path(tenant_audit_chain("_default"))
committed = api._audit_capability_decision(
    "_default", "tool_forge.create", "free", "allow", 1, None, "test_entry_point")
fp = validator._forge_paths_module()
out = {
    "chain": str(chain),
    "committed": committed,
    "validator_chain": str(fp.tenant_audit_chain("_default")),
    "paths_untouched": sys.modules["paths"] is cowork_paths,
    "shard_home": str(shard_verifier._resolve_corvin_home()),
    "events": [json.loads(l).get("event_type") or json.loads(l).get("event")
               for l in chain.read_text(encoding="utf-8").splitlines() if l.strip()]
              if chain.exists() else [],
}
print("RESULT " + json.dumps(out))
'''


def _run(tmp_path: Path) -> dict:
    home = tmp_path / "corvin"
    key = tmp_path / "anchor.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    env = {k: v for k, v in os.environ.items()
           if k not in ("FORGE_ROOT", "PYTHONPATH", "VOICE_AUDIT_PATH", "CORVIN_TENANT_ID")}
    env.update({"CORVIN_HOME": str(home), "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
                "HOME": str(tmp_path / "home"), "CORVIN_AUDIT_ANCHOR_KEY": str(key),
                "CORVIN_MODEL_USAGE_LIVE": "0"})
    (tmp_path / "home").mkdir()
    proc = subprocess.run([sys.executable, "-c", _CHILD, str(_REPO)], env=env, cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=120)
    lines = [l for l in proc.stdout.splitlines() if l.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, proc.stdout[-2000:] + proc.stderr[-4000:]
    return json.loads(lines[-1][len("RESULT "):])


def test_capability_decision_is_recorded_when_cowork_owns_the_bare_paths_name(tmp_path):
    out = _run(tmp_path)
    home = tmp_path / "corvin"
    expected = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    assert Path(out["chain"]) == expected
    assert out["committed"] is True, out
    assert "license.capability_decision" in out["events"], out


def test_the_validator_resolver_agrees_and_leaves_the_loaded_paths_alone(tmp_path):
    out = _run(tmp_path)
    assert out["validator_chain"] == out["chain"]
    assert out["paths_untouched"] is True
    assert Path(out["shard_home"]) == tmp_path / "corvin"
