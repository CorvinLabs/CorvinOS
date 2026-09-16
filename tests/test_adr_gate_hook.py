"""The ADR gate, exercised through real `git commit` calls.

Drives scripts/git-hooks/commit-msg the way git drives it — a throwaway
repo with core.hooksPath pointed at the tracked hooks — because the defect
this replaces was invisible to any test that called the logic directly:
the hook read the staged DIFF instead of the message, so it only misbehaved
when a real message and real file content were both present.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO_ROOT / "scripts" / "git-hooks"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check, env=env)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "core.hooksPath", str(HOOKS_DIR))
    # Keep the ADR-existence check pointed somewhere empty unless a test
    # populates it, so results never depend on the developer's real repo.
    (tmp_path / "adr" / "decisions").mkdir(parents=True)
    return r


def _commit(repo: Path, message: str) -> subprocess.CompletedProcess:
    env_repo = repo.parent / "adr"
    env = {**os.environ, "CORVIN_ADR_REPO": str(env_repo),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    return subprocess.run(["git", "-C", str(repo), "commit", "-m", message],
                          capture_output=True, text=True, env=env)


def _stage(repo: Path, name: str, body: str = "x = 1\n") -> None:
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    _git(repo, "add", name)


# ── the regression this hook replaces ───────────────────────────────────

def test_flag_in_the_message_is_honoured(repo: Path):
    """The old hook read the diff, so a correct flag was REJECTED."""
    _stage(repo, "core/mod.py")
    r = _commit(repo, "fix(core): thing [skip-adr-check]\n\nA bug fix, no design decision.")
    assert r.returncode == 0, r.stderr


def test_flag_only_in_file_content_does_not_pass(repo: Path):
    """The old hook read the diff, so THIS passed. It must not."""
    _stage(repo, "core/mod.py", "# [skip-adr-check]\nx = 1\n")
    r = _commit(repo, "fix(core): thing")
    assert r.returncode != 0
    assert "ADR Gate" in r.stderr


# ── the gate itself ─────────────────────────────────────────────────────

def test_source_change_without_any_flag_is_blocked(repo: Path):
    _stage(repo, "core/mod.py")
    r = _commit(repo, "feat(core): a new module")
    assert r.returncode != 0
    assert "core/mod.py" in r.stderr


def test_skip_flag_without_a_body_is_blocked(repo: Path):
    """CLAUDE.md requires a documented justification, not a bare flag."""
    _stage(repo, "core/mod.py")
    r = _commit(repo, "fix(core): thing [skip-adr-check]")
    assert r.returncode != 0
    assert "justification" in r.stderr


def test_test_only_flag_rejects_a_non_test_file(repo: Path):
    _stage(repo, "tests/test_a.py")
    _stage(repo, "core/mod.py")
    r = _commit(repo, "test: add coverage [test-only]")
    assert r.returncode != 0
    assert "core/mod.py" in r.stderr


def test_test_only_flag_accepts_real_tests(repo: Path):
    _stage(repo, "tests/test_a.py")
    r = _commit(repo, "test: add coverage [test-only]")
    assert r.returncode == 0, r.stderr


def test_docs_only_flag_rejects_a_code_file(repo: Path):
    _stage(repo, "README.md", "# hi\n")
    _stage(repo, "core/mod.py")
    r = _commit(repo, "docs: update [docs-only]")
    assert r.returncode != 0


def test_docs_change_without_a_flag_is_fine(repo: Path):
    """No source touched — nothing for the gate to ask about."""
    _stage(repo, "README.md", "# hi\n")
    r = _commit(repo, "docs: tidy the readme")
    assert r.returncode == 0, r.stderr


def test_tests_only_change_without_a_flag_is_fine(repo: Path):
    _stage(repo, "tests/test_a.py")
    r = _commit(repo, "test: another case")
    assert r.returncode == 0, r.stderr


# ── ADR references must resolve ─────────────────────────────────────────

def test_reference_to_a_missing_adr_is_blocked(repo: Path):
    _stage(repo, "core/mod.py")
    r = _commit(repo, "feat(core): thing [ADR-9999]")
    assert r.returncode != 0
    assert "ADR-9999" in r.stderr


def test_reference_to_an_existing_adr_passes(repo: Path):
    (repo.parent / "adr" / "decisions" / "ADR-9999-thing.md").write_text("id: ADR-9999\n")
    _stage(repo, "core/mod.py")
    r = _commit(repo, "feat(core): thing [ADR-9999]")
    assert r.returncode == 0, r.stderr


def test_merge_commits_are_exempt(repo: Path):
    _stage(repo, "core/mod.py")
    r = _commit(repo, "Merge branch 'feature/x' into main")
    assert r.returncode == 0, r.stderr


def test_extensionless_shell_script_counts_as_code(repo: Path):
    """The hooks in scripts/git-hooks are shell with no suffix — the gate
    must not wave through the very files that implement it."""
    _stage(repo, "scripts/git-hooks/commit-msg", "#!/usr/bin/env bash\nexit 0\n")
    r = _commit(repo, "fix: tweak the gate")
    assert r.returncode != 0
    assert "commit-msg" in r.stderr


def test_extensionless_non_script_is_not_code(repo: Path):
    _stage(repo, "LICENSE", "Apache 2.0\n")
    r = _commit(repo, "chore: add licence")
    assert r.returncode == 0, r.stderr


def test_flag_mentioned_only_in_the_body_grants_no_exemption(repo: Path):
    """A message EXPLAINING the flags must not inherit them. This fired the
    first time the hook ran against its own commit."""
    _stage(repo, "core/mod.py")
    r = _commit(repo, "fix(core): thing\n\nThe gate checks [docs-only] and [test-only] against staged files.")
    assert r.returncode != 0
    assert "core/mod.py" in r.stderr


def test_subject_flag_still_wins_with_prose_below(repo: Path):
    _stage(repo, "core/mod.py")
    r = _commit(repo, "fix(core): thing [skip-adr-check]\n\nMentions [docs-only] in passing; a bug fix.")
    assert r.returncode == 0, r.stderr
