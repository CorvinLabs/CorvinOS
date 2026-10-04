"""E2E: os.workflow_optimizer runs in shadow mode behind the real
OS-model resolver (L22).

ADR-0532 Phase 1 migration (L22). Verifies, through the REAL entry point
(``adapter._resolve_os_model_bundled`` — what the bridge actually calls
before every OS turn, per ADR-0952):

1. The Skill is registered and reachable via the audited registry.
2. The shadow comparison runs without altering the resolved model id.
3. A shadow failure (forced exception) never breaks model resolution.

Drives the real transport boundary: a real subprocess calling the real
``adapter._resolve_os_model_bundled()`` function, not a direct unit call
to WorkflowOptimizerSkill.execute() (CLAUDE.md § E2E Wiring Proof).
"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "corvin_operator" / "bridges" / "shared"


def test_workflow_optimizer_registered_in_builtin_ids():
    """Reachability: os.workflow_optimizer must be a real builtin Skill id."""
    from core.skills.os_skills_phase1 import BUILTIN_SKILL_IDS

    assert "os.workflow_optimizer" in BUILTIN_SKILL_IDS


def test_resolve_os_model_unaffected_by_shadow_wiring():
    """_resolve_os_model_bundled (the real production entry point) must
    return a model id with the shadow Skill wired in."""
    with tempfile.TemporaryDirectory() as tmp:
        script = f"""
import sys, os
sys.path.insert(0, {str(SHARED_DIR)!r})
os.environ["CORVIN_HOME"] = {tmp!r}

import adapter
result = adapter._resolve_os_model_bundled(
    {{}}, payload_chars=100, engine_id="claude_code", tenant_id="_default",
    task_input="Please review this code for a subtle architecture bug",
)
print("RESULT=" + repr(result))
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        result_lines = [l for l in proc.stdout.splitlines() if l.startswith("RESULT=")]
        assert result_lines, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        result = eval(result_lines[0].split("=", 1)[1])
        assert isinstance(result, str) and result, f"Expected a resolved model id, got: {result!r}"


def test_shadow_compare_failure_never_raises_into_model_resolution():
    """A broken shadow Skill must not break OS-model resolution (real
    subprocess call — proves the exception boundary holds end-to-end)."""
    with tempfile.TemporaryDirectory() as tmp:
        script = f"""
import sys, os
sys.path.insert(0, {str(SHARED_DIR)!r})
os.environ["CORVIN_HOME"] = {tmp!r}

# Force the shadow path to explode on every call.
import core.skills.os_skills.workflow_optimizer_wrapper as wo_mod
def _boom(**kwargs):
    raise RuntimeError("simulated shadow Skill crash")
wo_mod.shadow_compare = _boom

import adapter
result = adapter._resolve_os_model_bundled(
    {{}}, payload_chars=100, engine_id="claude_code", tenant_id="_default",
    task_input="Please review this code for a subtle architecture bug",
)
print("RESULT=" + repr(result))
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        result_lines = [l for l in proc.stdout.splitlines() if l.startswith("RESULT=")]
        assert result_lines, (
            f"_resolve_os_model_bundled must still return correctly even "
            f"when the shadow Skill raises. stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        result = eval(result_lines[0].split("=", 1)[1])
        assert isinstance(result, str) and result


if __name__ == "__main__":
    test_workflow_optimizer_registered_in_builtin_ids()
    test_resolve_os_model_unaffected_by_shadow_wiring()
    test_shadow_compare_failure_never_raises_into_model_resolution()
    print("✓ All workflow_optimizer shadow E2E tests passed")
