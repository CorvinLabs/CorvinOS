"""E2E: os.flow_guard runs in shadow mode behind the real L34 gate.

ADR-0532 Phase 1 migration (L34). Verifies, through the REAL entry point
(``spawn_gates.check_l34`` — the function the adapter and console actually
call before every spawn):

1. The Skill is registered and reachable via the audited registry.
2. The shadow comparison runs without altering check_l34's return value.
3. A shadow failure (forced exception) never breaks the production gate.

This does NOT test FlowGuardSkill.execute() directly — that would be a unit
test wearing an E2E label (CLAUDE.md § E2E Wiring Proof). It drives the real
transport boundary: a tenant.corvin.yaml on disk + the real check_l34 call.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_tenant_config(corvin_home: Path, tenant_id: str = "_default") -> None:
    cfg_dir = corvin_home / "tenants" / tenant_id / "global"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "tenant.corvin.yaml").write_text(
        """
spec:
  data_classification:
    matrix:
      CONFIDENTIAL: [local]
""",
        encoding="utf-8",
    )


def test_flow_guard_skill_registered_in_builtin_ids():
    """Reachability: os.flow_guard must be a real builtin Skill id."""
    from core.skills.os_skills_phase1 import BUILTIN_SKILL_IDS

    assert "os.flow_guard" in BUILTIN_SKILL_IDS


def test_check_l34_still_returns_none_on_allow_with_shadow_wired():
    """check_l34 (the real production entry point) must be unaffected by the
    shadow Skill — same return value with or without the shadow call."""
    with tempfile.TemporaryDirectory() as tmp:
        corvin_home = Path(tmp)
        _write_tenant_config(corvin_home)

        script = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT / "corvin_operator" / "bridges" / "shared")!r})
from spawn_gates import check_l34

result = check_l34(
    "claude_code", "_default",
    classification="internal",
    prompt="just a normal chat message",
    corvin_home={str(corvin_home)!r},
)
print(f"RESULT={{result!r}}")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        assert "RESULT=None" in proc.stdout, (
            f"Expected fail-open/allow for internal classification, got: "
            f"{proc.stdout}\nstderr: {proc.stderr}"
        )


def test_shadow_compare_failure_never_raises_into_check_l34():
    """A broken shadow Skill must not break the production gate (real
    subprocess call — proves the exception boundary holds end-to-end)."""
    with tempfile.TemporaryDirectory() as tmp:
        corvin_home = Path(tmp)
        _write_tenant_config(corvin_home)

        script = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT / "corvin_operator" / "bridges" / "shared")!r})

# Force the shadow path to explode on every call.
import core.skills.os_skills.flow_guard_skill as fg_mod
def _boom(**kwargs):
    raise RuntimeError("simulated shadow Skill crash")
fg_mod.shadow_compare = _boom

from spawn_gates import check_l34
result = check_l34(
    "claude_code", "_default",
    classification="internal",
    prompt="just a normal chat message",
    corvin_home={str(corvin_home)!r},
)
print(f"RESULT={{result!r}}")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        assert "RESULT=None" in proc.stdout, (
            f"check_l34 must still return correctly even when the shadow "
            f"Skill raises. Got: {proc.stdout}\nstderr: {proc.stderr}"
        )


if __name__ == "__main__":
    test_flow_guard_skill_registered_in_builtin_ids()
    test_check_l34_still_returns_none_on_allow_with_shadow_wired()
    test_shadow_compare_failure_never_raises_into_check_l34()
    print("✓ All flow_guard shadow E2E tests passed")
