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


# ─────────────────────────────────────────────────────────────────────────────
# F4 (round-4 review): the swallow must discriminate on whether the PACKAGE
# resolves, not on a `core.skills` prefix. A deleted transitive module raises
# ModuleNotFoundError(name="core.skills.<submodule>"), which the old
# `startswith("core.skills")` classified as "package absent" and sent down the
# quiet DEBUG branch — re-opening the exact F-K1 failure the test above guards.
# These drive the REAL `bootstrap._boot_skills_registry` (the test above imports
# `core.skills.boot` directly and therefore cannot catch the mis-classification).
# ─────────────────────────────────────────────────────────────────────────────

_BROKEN_TRANSITIVE = r"""
import json, logging, sys
records = []

class _Cap(logging.Handler):
    def emit(self, rec):
        records.append((rec.levelname, rec.getMessage()))

logging.getLogger().addHandler(_Cap())
logging.getLogger().setLevel(logging.DEBUG)

import corvin_plugins.bootstrap as B

# Exactly the ModuleNotFoundError shape a DELETED submodule of a PRESENT
# package produces: name == "core.skills.<submodule>".
sys.modules["core.skills.os_skills_integration"] = None
for cached in [m for m in sys.modules if m.startswith("core.skills.boot")]:
    del sys.modules[cached]
sys.modules.pop("core.skills.boot", None)

result = B._boot_skills_registry()
print(json.dumps({
    "registered": result,
    "error_lines": [m for lvl, m in records if lvl == "ERROR" and "Skills registry boot FAILED" in m],
    "debug_swallow": [m for lvl, m in records if "core.skills absent" in m],
}))
"""

_STRIPPED_INSTALL = r"""
import json, logging, os, sys

# Simulate a STRIPPED install: a `core` package with no `skills` subpackage,
# shadowing the repo copy. `from core.skills.boot import ...` then raises
# ModuleNotFoundError(name="core.skills") — the only shape that may stay quiet.
sys.path.insert(0, os.environ["STRIPPED_SHIM"])
sys.modules.pop("core", None)

records = []

class _Cap(logging.Handler):
    def emit(self, rec):
        records.append((rec.levelname, rec.getMessage()))

logging.getLogger().addHandler(_Cap())
logging.getLogger().setLevel(logging.DEBUG)

import importlib.util
assert importlib.util.find_spec("core.skills") is None, "shim did not shadow the repo core/"

import corvin_plugins.bootstrap as B
result = B._boot_skills_registry()
print(json.dumps({
    "registered": result,
    "error_lines": [m for lvl, m in records if lvl == "ERROR"],
    "debug_swallow": [m for lvl, m in records if "core.skills absent" in m],
}))
"""


def _run(script: str, tmp_path: Path, *, extra_env: dict | None = None) -> dict:
    home = tmp_path / "corvin-home"
    home.mkdir(exist_ok=True)
    parts = [".", "operator/bridges/shared", "operator/forge", "core/plugins", "core/console"]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CORVIN_HOME": str(home),
        "VOICE_AUDIT_PATH": str(home / "audit.jsonl"),
        "CORVIN_TENANT_ID": "_default",
        "PYTHONPATH": ":".join(str(REPO / p) for p in parts),
    }
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=str(REPO), env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"subprocess failed:\n{proc.stderr[-4000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_broken_transitive_module_fails_loudly(tmp_path: Path) -> None:
    """A PRESENT core.skills with a broken import chain must log at ERROR."""
    out = _run(_BROKEN_TRANSITIVE, tmp_path)
    assert out["registered"] == [], "a broken import chain cannot yield skills"
    assert out["error_lines"], (
        "the F-K1 failure mode is back: a deleted submodule of a PRESENT "
        "core.skills was swallowed silently instead of logged at ERROR"
    )
    assert "os_skills_integration" in out["error_lines"][0], out["error_lines"]
    assert not out["debug_swallow"], (
        "classified as 'package absent' — that is the prefix-match bug (F4)"
    )


def test_genuinely_stripped_install_stays_quiet(tmp_path: Path) -> None:
    """The other half: no core.skills at all is still tolerated at DEBUG."""
    shim = tmp_path / "shim"
    (shim / "core").mkdir(parents=True)
    (shim / "core" / "__init__.py").write_text("", encoding="utf-8")
    out = _run(_STRIPPED_INSTALL, tmp_path, extra_env={"STRIPPED_SHIM": str(shim)})
    assert out["registered"] == []
    assert out["debug_swallow"], "a stripped install must take the quiet branch"
    assert not out["error_lines"], out["error_lines"]
