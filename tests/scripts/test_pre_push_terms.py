"""E2E tests for scripts/git-hooks/pre-push and scripts/git-hooks/scan-tree (T-0428).

Every test drives the real boundary: a real `git push` from a throw-away
repository to a local bare repository, with `core.hooksPath` pointing at the
checkout's `scripts/git-hooks`, or a real `scan-tree` subprocess. The terms
used here are neutral placeholders; the repository never carries a real
terms list.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "scripts" / "git-hooks"
SCAN_TREE = HOOKS_DIR / "scan-tree"

TERM_LINES = "# neutral test terms\nsecretproject\n\nhidden[-_ ]?codename\n"


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    terms = cfg / "terms.txt"
    terms.write_text(TERM_LINES, encoding="utf-8")
    remotes = cfg / "remotes.txt"
    remotes.write_text("# public remotes\npublic.git\n", encoding="utf-8")
    e = dict(os.environ)
    e.update(
        {
            "CORVIN_FORBIDDEN_TERMS_FILE": str(terms),
            "CORVIN_PUBLIC_REMOTES_FILE": str(remotes),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "HOME": str(tmp_path / "home"),
        }
    )
    return e


def git(cwd: Path, env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=check
    )


@pytest.fixture()
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    for name in ("public.git", "private.git"):
        subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / name)], env=env, check=True)
    work = tmp_path / "work"
    work.mkdir()
    git(work, env, "init", "-q", "-b", "main")
    git(work, env, "remote", "add", "public", str(tmp_path / "public.git"))
    git(work, env, "remote", "add", "private", str(tmp_path / "private.git"))
    (work / "README.txt").write_text("hello\n", encoding="utf-8")
    git(work, env, "add", "README.txt")
    git(work, env, "commit", "-q", "-m", "initial")
    return work


def commit(work: Path, env: dict[str, str], name: str, content: str, message: str) -> None:
    path = work / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(work, env, "add", name)
    git(work, env, "commit", "-q", "-m", message)


def push(work: Path, env: dict[str, str], remote: str = "public") -> subprocess.CompletedProcess[str]:
    return git(work, env, "-c", f"core.hooksPath={HOOKS_DIR}", "push", remote, "main", check=False)


def remote_has_main(tmp_path: Path, env: dict[str, str], remote: str = "public.git") -> bool:
    out = subprocess.run(
        ["git", "--git-dir", str(tmp_path / remote), "rev-parse", "--verify", "-q", "refs/heads/main"],
        env=env, capture_output=True, text=True,
    )
    return out.returncode == 0


def assert_no_leak(output: str) -> None:
    lowered = output.lower()
    assert "secretproject" not in lowered.replace("secretproject.txt", "")
    assert "codename" not in lowered


def test_hook_files_are_executable() -> None:
    for path in (HOOKS_DIR / "pre-push", SCAN_TREE):
        assert path.is_file() and os.access(path, os.X_OK), path


def test_clean_push_is_accepted(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    commit(repo, env, "notes.txt", "nothing to see\n", "add notes")
    result = push(repo, env)
    assert result.returncode == 0, result.stderr
    assert remote_has_main(tmp_path, env)


def test_term_in_diff_line_is_refused(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    git(repo, env, "-c", f"core.hooksPath={HOOKS_DIR}", "push", "-q", "public", "main")
    commit(repo, env, "docs/plan.txt", "intro\nthe SecretProject launch\n", "add plan")
    result = push(repo, env)
    assert result.returncode != 0
    assert "refused: docs/plan.txt matches term #2" in result.stderr
    assert_no_leak(result.stderr)
    # the remote kept its previous tip
    tip = git(repo, env, "--git-dir", str(tmp_path / "public.git"), "rev-parse", "main").stdout.strip()
    assert tip == git(repo, env, "rev-parse", "main~1").stdout.strip()


def test_term_on_an_added_line_starting_with_plus_plus_is_refused(repo: Path, env: dict[str, str]) -> None:
    """An added line whose text starts with "++ " shows up as "+++ ..." in the patch; it is content, not a header."""
    git(repo, env, "-c", f"core.hooksPath={HOOKS_DIR}", "push", "-q", "public", "main")
    commit(repo, env, "docs/notes.txt", "intro\n++ secretproject notes\n", "add notes")
    result = push(repo, env)
    assert result.returncode != 0 and "refused: docs/notes.txt" in result.stderr
    assert_no_leak(result.stderr)


def test_term_in_file_name_is_refused(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    commit(repo, env, "assets/hidden_codename.png.txt", "harmless\n", "add asset")
    result = push(repo, env)
    assert result.returncode != 0
    assert "matches term #4" in result.stderr
    assert "refused: assets/hidden_codename.png.txt matches term #4" in result.stderr
    assert not remote_has_main(tmp_path, env)


def test_term_in_renamed_file_name_is_refused(repo: Path, env: dict[str, str]) -> None:
    git(repo, env, "-c", f"core.hooksPath={HOOKS_DIR}", "push", "-q", "public", "main")
    git(repo, env, "mv", "README.txt", "secretproject.txt")
    git(repo, env, "commit", "-q", "-m", "rename")
    result = push(repo, env)
    assert result.returncode != 0
    assert "refused: secretproject.txt matches term #2" in result.stderr


def test_term_in_commit_message_is_refused(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    commit(repo, env, "notes.txt", "plain\n", "prepare the Hidden Codename release")
    result = push(repo, env)
    assert result.returncode != 0
    assert "refused: commit message matches term #4" in result.stderr
    assert_no_leak(result.stderr)
    assert not remote_has_main(tmp_path, env)


def test_unlisted_remote_is_not_scanned(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    commit(repo, env, "plan.txt", "secretproject\n", "secretproject")
    result = push(repo, env, remote="private")
    assert result.returncode == 0, result.stderr
    assert "refused" not in result.stderr
    assert remote_has_main(tmp_path, env, "private.git")


def test_missing_terms_file_is_refused(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    env = dict(env, CORVIN_FORBIDDEN_TERMS_FILE=str(tmp_path / "cfg" / "absent.txt"))
    result = push(repo, env)
    assert result.returncode != 0
    assert "terms file not found" in result.stderr
    assert not remote_has_main(tmp_path, env)


def test_empty_terms_file_is_refused(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    empty = tmp_path / "cfg" / "empty.txt"
    empty.write_text("# only a comment\n\n", encoding="utf-8")
    result = push(repo, dict(env, CORVIN_FORBIDDEN_TERMS_FILE=str(empty)))
    assert result.returncode != 0
    assert "terms file is empty" in result.stderr


def test_branch_deletion_is_allowed(repo: Path, env: dict[str, str]) -> None:
    git(repo, env, "-c", f"core.hooksPath={HOOKS_DIR}", "push", "-q", "public", "main:topic")
    result = git(repo, env, "-c", f"core.hooksPath={HOOKS_DIR}", "push", "public", ":topic", check=False)
    assert result.returncode == 0, result.stderr


def test_scan_tree_lists_tracked_hits(repo: Path, env: dict[str, str]) -> None:
    commit(repo, env, "docs/a.txt", "mentions SECRETPROJECT here\n", "add a")
    commit(repo, env, "hidden-codename.txt", "clean\n", "add b")
    (repo / "untracked.txt").write_text("secretproject\n", encoding="utf-8")
    result = subprocess.run([str(SCAN_TREE)], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "docs/a.txt: term #2" in result.stdout
    assert "hidden-codename.txt: term #4" in result.stdout
    assert "untracked.txt" not in result.stdout
    assert "2 hit(s) in 2 file(s)" in result.stdout
    assert "mentions" not in result.stdout


def test_scan_tree_clean_tree_exits_0(repo: Path, env: dict[str, str]) -> None:
    result = subprocess.run([str(SCAN_TREE)], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 hit(s)" in result.stdout


def test_scan_tree_missing_terms_file_exits_2(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    env = dict(env, CORVIN_FORBIDDEN_TERMS_FILE=str(tmp_path / "nope.txt"))
    result = subprocess.run([str(SCAN_TREE)], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 2


def test_a_commit_already_on_a_private_remote_is_still_scanned_for_the_public_one(repo: Path, env: dict[str, str]) -> None:
    """A new branch on the public remote: commits that only sit on ANOTHER remote's tracking refs were never scanned
    for this one, so they must not be skipped."""
    commit(repo, env, "docs/plan.txt", "the secretproject plan\n", "add plan")
    assert push(repo, env, "private").returncode == 0                      # unlisted remote: allowed, not scanned
    result = push(repo, env, "public")
    assert result.returncode != 0 and "refused: docs/plan.txt" in result.stderr
    assert_no_leak(result.stderr)


def test_a_push_to_a_url_scans_everything_reachable(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    """`git push <url> HEAD:refs/heads/x` passes the URL as the remote name: no tracking refs may excuse a commit."""
    commit(repo, env, "docs/plan.txt", "the secretproject plan\n", "add plan")
    assert push(repo, env, "private").returncode == 0
    result = git(repo, env, "-c", f"core.hooksPath={HOOKS_DIR}", "push", str(tmp_path / "public.git"),
                 "HEAD:refs/heads/side", check=False)
    assert result.returncode != 0 and "refused: docs/plan.txt" in result.stderr
