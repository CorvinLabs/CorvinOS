"""Regression tests for scripts defused in the 2026-09-27 adversarial review.

- scripts/rotate_secrets.py "rotated" a hard-coded mock inventory, threw the new
  secrets away, wrote unchained records to a side file and crashed on first run.
  It must refuse (exit 2) and write nothing.
- scripts/adr2083_staging_validation.py advertised "100+ Discord / 50+ Slack
  tests" and printed "Ready for Phase 2 Canary" after 12 in-process checks on
  stub executors. It must never report a staging pass (exit 1 or 3).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["CORVIN_HOME"] = str(tmp_path / "corvin-home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "home" / ".config")
    return env


def test_rotate_secrets_refuses_and_writes_nothing(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "rotate_secrets.py"),
         "--policy-file", str(REPO / "core" / "compliance" / "secret_rotation_policy.yaml")],
        capture_output=True, text=True, timeout=60, env=_env(tmp_path), cwd=tmp_path,
    )
    assert proc.returncode == 2
    assert "defused" in proc.stderr
    assert not (tmp_path / "home" / ".corvin").exists()
    assert not (tmp_path / "corvin-home").exists()


def test_rotate_secrets_manager_raises():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import rotate_secrets  # noqa: PLC0415
    finally:
        sys.path.remove(str(REPO / "scripts"))
    with pytest.raises(NotImplementedError):
        rotate_secrets.SecretRotationManager("any.yaml")


def test_adr2083_never_reports_a_staging_pass(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "adr2083_staging_validation.py")],
        capture_output=True, text=True, timeout=240, env=_env(tmp_path), cwd=tmp_path,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode in (1, 3), out[-2000:]
    assert "Ready for Phase 2 Canary" not in out
    assert "VALIDATION PASSED" not in out
    assert "SIMULATED" in out
    assert "Discord/Slack traffic exercised: 0" in out


def test_compliance_verification_e2e_refuses(tmp_path):
    """Its 15 'proofs' read back lines it wrote itself; a pass proved nothing."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "compliance_verification_e2e.py")],
        capture_output=True, text=True, timeout=60, env=_env(tmp_path), cwd=tmp_path,
    )
    assert proc.returncode == 2
    assert "defused" in proc.stderr
    assert "PASS" not in proc.stdout
