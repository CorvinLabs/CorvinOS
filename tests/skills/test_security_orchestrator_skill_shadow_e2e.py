"""E2E: os.security_orchestrator runs in shadow mode behind the real
PIN-elevation lockout (L16).

ADR-0532 Phase 1 migration (L16). Verifies, through the REAL entry point
(``auth_elevation.grant`` — the function the adapter actually calls on
every ``/auth-up <pin>`` attempt):

1. The Skill is registered and reachable via the audited registry.
2. The shadow comparison runs without altering grant()'s return value or
   the deterministic lockout threshold.
3. A shadow failure (forced exception) never breaks PIN elevation.

Drives the real transport boundary: a real subprocess calling the real
``auth_elevation.grant()`` function repeatedly, not a direct unit call to
SecurityOrchestratorSkill.execute() (CLAUDE.md § E2E Wiring Proof).
"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "corvin_operator" / "bridges" / "shared"


def test_security_orchestrator_registered_in_builtin_ids():
    """Reachability: os.security_orchestrator must be a real builtin Skill id."""
    from core.skills.os_skills_phase1 import BUILTIN_SKILL_IDS

    assert "os.security_orchestrator" in BUILTIN_SKILL_IDS


def test_grant_lockout_behavior_unaffected_by_shadow_wiring():
    """The real grant() lockout sequence (5 fails -> lockout) must be
    byte-identical with the shadow Skill wired in."""
    with tempfile.TemporaryDirectory() as tmp:
        script = f"""
import sys, os
sys.path.insert(0, {str(SHARED_DIR)!r})
os.environ["CORVIN_HOME"] = {tmp!r}
os.environ["VOICE_AUDIT_PATH"] = {str(Path(tmp) / "audit.jsonl")!r}

import auth_elevation

results = []
for i in range(6):
    ok, reason = auth_elevation.grant(
        chat_key="e2e_test_chat", pin="wrong", settings_pin="1234", channel="test",
    )
    results.append((ok, reason))

print("RESULTS=" + repr(results))
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        assert "RESULTS=" in proc.stdout, proc.stdout
        results_line = [l for l in proc.stdout.splitlines() if l.startswith("RESULTS=")][0]
        results = eval(results_line.split("=", 1)[1])

        # First 5 attempts: wrong-pin (shadow runs, decision unaffected)
        for i in range(5):
            assert results[i] == (False, "wrong-pin"), f"attempt {i}: {results[i]}"
        # 6th attempt: locked out (deterministic threshold, unaffected by shadow)
        assert results[5] == (False, "pin-lockout"), f"6th attempt: {results[5]}"


def test_shadow_compare_failure_never_raises_into_grant():
    """A broken shadow Skill must not break PIN elevation (real subprocess
    call — proves the exception boundary holds end-to-end)."""
    with tempfile.TemporaryDirectory() as tmp:
        script = f"""
import sys, os
sys.path.insert(0, {str(SHARED_DIR)!r})
os.environ["CORVIN_HOME"] = {tmp!r}
os.environ["VOICE_AUDIT_PATH"] = {str(Path(tmp) / "audit.jsonl")!r}

# Force the shadow path to explode on every call.
import core.skills.os_skills.security_orchestrator_skill as so_mod
def _boom(**kwargs):
    raise RuntimeError("simulated shadow Skill crash")
so_mod.shadow_compare = _boom

import auth_elevation
ok, reason = auth_elevation.grant(
    chat_key="e2e_test_chat_2", pin="wrong", settings_pin="1234", channel="test",
)
print(f"RESULT={{(ok, reason)!r}}")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        assert "RESULT=(False, 'wrong-pin')" in proc.stdout, (
            f"grant() must still return correctly even when the shadow Skill "
            f"raises. Got: {proc.stdout}\nstderr: {proc.stderr}"
        )


if __name__ == "__main__":
    test_security_orchestrator_registered_in_builtin_ids()
    test_grant_lockout_behavior_unaffected_by_shadow_wiring()
    test_shadow_compare_failure_never_raises_into_grant()
    print("✓ All security_orchestrator shadow E2E tests passed")
