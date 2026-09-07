"""The ACP Skills registry must boot in a FRESH interpreter (F-K1 regression).

On 2026-09-07 ``core.skills`` was unimportable in a fresh process (a deleted
transitive module), ``bootstrap._boot_skills_registry`` swallowed the
ImportError at DEBUG, and every restart came up with an EMPTY registry —
capabilities all False, learning API 503 — while every in-process test kept
passing, because the test process had the modules cached. This test runs the
boot in a SUBPROCESS under a throw-away ``CORVIN_HOME`` so an import break in
the skills package can never ship silently again.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_BOOT = r"""
import json, os, sys
from core.skills import boot as _boot                    # the module the host imports
from core.skills.os_skills_phase1 import BUILTIN_SKILL_IDS
from core.skills.skill_registry_phase1 import get_registry, SkillTier
registered = _boot.boot_skills("_default", audit_emit=lambda *_a, **_k: None, wire_learning=False)
reg = get_registry()
print(json.dumps({
    "registered": sorted(registered),
    "builtin": sorted(BUILTIN_SKILL_IDS),
    "capabilities_tier": reg.get("os.capabilities").metadata.tier.value,
    "capabilities": reg.execute(
        "os.capabilities", {"tenant_id": "_default", "gated_flags": ["a"]},
        lom="tests/skills/test_skills_boot_subprocess.py:test_boot_skills_in_fresh_interpreter",
    ).status,
}))
"""


def test_boot_skills_in_fresh_interpreter(tmp_path: Path) -> None:
    home = tmp_path / "corvin-home"
    home.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CORVIN_HOME": str(home),
        "VOICE_AUDIT_PATH": str(home / "audit.jsonl"),
        "CORVIN_TENANT_ID": "_default",
        "PYTHONPATH": ":".join(str(REPO / p) for p in (
            ".", "operator/bridges/shared", "operator/forge", "core/plugins", "core/console",
        )),
    }
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT], cwd=str(REPO), env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"fresh-process boot failed:\n{proc.stderr[-4000:]}"
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["registered"], "registry EMPTY after boot in a fresh interpreter"
    assert set(out["builtin"]) <= set(out["registered"]), out
    assert out["capabilities_tier"] == "compliance"
    assert out["capabilities"] == "success"
    # nothing leaked into the operator's home
    assert not (tmp_path / ".corvin").exists()
