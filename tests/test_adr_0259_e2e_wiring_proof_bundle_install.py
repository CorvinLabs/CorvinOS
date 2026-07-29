"""E2E tests for ADR-0259 — the e2e-wiring-proof bundle skill's own reachability.

Dogfoods the skill's own rubric: Phase 1 (reachability — is the skill actually
picked up by the real install mechanism, not just present on disk?) and Phase 2
(a real subprocess run of the installer, not a direct import of its logic).

No mocks: this runs the real ``operator/bundle/install.sh`` as a subprocess
against a throwaway ``$HOME``/``$CORVIN_HOME``, exactly as a fresh install would.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = REPO_ROOT / "operator" / "bundle"
INSTALL_SH = BUNDLE_DIR / "install.sh"
SKILL_NAME = "e2e-wiring-proof"
SKILL_SOURCE = BUNDLE_DIR / "skills" / "ldd" / SKILL_NAME / "SKILL.md"


def _run_install(tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the real installer subprocess against a throwaway HOME/CORVIN_HOME."""
    fake_home = tmp_path / "home"
    fake_corvin_home = tmp_path / "corvin_home"
    fake_home.mkdir()
    fake_corvin_home.mkdir()

    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["CORVIN_HOME"] = str(fake_corvin_home)

    return subprocess.run(
        ["bash", str(INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(BUNDLE_DIR),
    ), fake_home


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh not present in this checkout")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_source_skill_file_exists_and_has_valid_frontmatter():
    """Phase-1 reachability precondition: the skill file itself is present and parseable."""
    assert SKILL_SOURCE.is_file(), (
        f"{SKILL_SOURCE} is missing — e2e-wiring-proof would not exist in the "
        "canonical bundle location siblings like adr_gate ship from."
    )
    text = SKILL_SOURCE.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must start with a YAML frontmatter block"
    assert f"name: {SKILL_NAME}" in text.splitlines()[1], (
        "frontmatter 'name' field must match the directory name exactly, "
        "or Claude Code's skill loader will not resolve it by this name"
    )


def test_manifest_glob_actually_matches_the_new_skill_directory():
    """Phase-1 reachability: manifest.yaml's declared component glob must really
    match the new skill dir — not just claim to via a stale comment/count."""
    import fnmatch

    manifest_text = (BUNDLE_DIR / "manifest.yaml").read_text(encoding="utf-8")
    assert 'skills:' in manifest_text and 'skills/ldd/*' in manifest_text, (
        "manifest.yaml no longer declares the skills/ldd/* component glob — "
        "e2e-wiring-proof (and every sibling LDD skill) would silently stop "
        "being a declared bundle component"
    )
    relative = str(SKILL_SOURCE.parent.relative_to(BUNDLE_DIR))
    assert fnmatch.fnmatch(relative, "skills/ldd/*"), (
        f"{relative} does not match the manifest's declared glob 'skills/ldd/*'"
    )


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh not present in this checkout")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_install_sh_actually_copies_the_skill_end_to_end(tmp_path):
    """Phase-2 functional proof: run the REAL installer subprocess (not the
    copy-loop logic re-implemented in Python) and verify the skill lands where
    Claude Code's native skill loader reads from — ~/.claude/skills/<name>/."""
    result, fake_home = _run_install(tmp_path)

    assert result.returncode == 0, (
        f"install.sh exited {result.returncode}\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}"
    )

    installed_skill_md = fake_home / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
    assert installed_skill_md.is_file(), (
        f"install.sh ran successfully but {installed_skill_md} does not exist — "
        f"the skill is reachable on disk but NOT installed by the real installer "
        f"subprocess. stdout was:\n{result.stdout}"
    )

    installed_text = installed_skill_md.read_text(encoding="utf-8")
    source_text = SKILL_SOURCE.read_text(encoding="utf-8")
    assert installed_text == source_text, (
        "installed copy diverges from the bundle source — install.sh must copy "
        "the file verbatim, not transform it"
    )

    # Evidence: the installer's own log line names this exact skill.
    assert f"installed skill: {SKILL_NAME}" in result.stdout, (
        f"installer did not log installing {SKILL_NAME!r} — stdout:\n{result.stdout}"
    )


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh not present in this checkout")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_install_sh_installs_all_twelve_ldd_skills_not_just_this_one(tmp_path):
    """Guards against a future skill being added to skills/ldd/ without the
    glob/loop actually picking it up (silent under-count regression)."""
    result, fake_home = _run_install(tmp_path)
    assert result.returncode == 0

    source_skill_dirs = {p.name for p in (BUNDLE_DIR / "skills" / "ldd").iterdir() if p.is_dir()}
    installed_skill_dirs = {
        p.name for p in (fake_home / ".claude" / "skills").iterdir() if p.is_dir()
    }

    missing = source_skill_dirs - installed_skill_dirs
    assert not missing, f"install.sh failed to install: {sorted(missing)}"
