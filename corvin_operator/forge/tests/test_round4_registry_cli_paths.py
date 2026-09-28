"""Round-4 adversarial-review regressions for forge registry + CLI path wiring.

1. ``Registry._audit`` passed ``hash_chain=self.hash_chain`` (the legacy
   ``policy.audit.hash_chain`` knob) to ``write_event``, which refuses every
   unchained record except the gap marker — so with the knob false every
   create/delete/promote RAISED after the manifest had been written.
2. ``forge.py::_default_root`` imported ``corvin_operator.forge.forge.paths`` by
   full name, which resolves through whatever ``corvin_operator`` the
   interpreter finds first (an editable install of ANOTHER checkout), instead
   of the ``forge`` package every other import in the CLI uses.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_FORGE_DIR = Path(__file__).resolve().parents[1]
if str(_FORGE_DIR) not in sys.path:
    sys.path.insert(0, str(_FORGE_DIR))


def test_registry_with_hash_chain_false_still_writes_a_chained_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_AGENTS_SKIP_LIVE", "1")
    monkeypatch.setenv("CORVIN_INTEGRATION_TEST", "1")
    from forge.registry import Registry
    from forge.security_events import verify_chain

    reg = Registry(tmp_path / "ws", hash_chain=False)
    reg.create("t_r4", "d", {"type": "object", "properties": {}},
               "def run(**k):\n    return 1\n")
    chain = reg._audit_chain()
    recs = [json.loads(ln) for ln in chain.read_text("utf-8").splitlines() if ln.strip()]
    created = [r for r in recs if r.get("event_type") == "tool.created"]
    assert created and created[-1].get("hash"), recs
    ok, problems = verify_chain(chain)
    assert ok, problems


def test_default_root_does_not_import_paths_by_full_dotted_name(tmp_path):
    code = (
        "import sys, types, importlib.util\n"
        f"sys.path.insert(0, {str(_FORGE_DIR)!r})\n"
        "trap = types.ModuleType('corvin_operator.forge.forge.paths')\n"
        "def _boom(*a, **k): raise RuntimeError('full-name paths import used')\n"
        "trap._resolve_tenant_id = _boom\n"
        "sys.modules['corvin_operator.forge.forge.paths'] = trap\n"
        f"spec = importlib.util.spec_from_file_location('forge_cli', {str(_FORGE_DIR / 'forge.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "print(m.DEFAULT_ROOT)\n"
    )
    env = dict(os.environ)
    env.pop("FORGE_ROOT", None)
    env["CORVIN_TENANT_ID"] = "acme"
    env["CORVIN_HOME"] = str(tmp_path / "home")
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "acme" in r.stdout
