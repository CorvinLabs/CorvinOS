"""update.sh rollback must never destroy uncommitted work in a developer checkout.

Since 1830a8842 update.sh picks the tree the console SERVES as its source —
on a developer host that is the operator's own checkout. The rollback block
used ``git reset --hard $PREV_REV`` for every git tree, which wiped
uncommitted changes that fetch_checkout had deliberately left in place (a
non-main branch) or re-applied from the autostash (and then dropped).

These tests execute the real rollback block extracted from update.sh.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UPDATE_SH = REPO / "update.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required")


def _rollback_block() -> str:
    text = UPDATE_SH.read_text()
    m = re.search(r"^RB=1\n(.*?)^if \[ -d \"\$WEB/dist\.prev\" \]", text, re.S | re.M)
    assert m, "rollback block not found in update.sh"
    return m.group(1)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "src"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a1\n")
    (repo / "wip.txt").write_text("w1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "one")
    return repo, _git(repo, "rev-parse", "HEAD")


def _run_rollback(repo: Path, kind: str, prev: str, tmp_path: Path) -> str:
    script = (
        f'SRC="{repo}"; KIND="{kind}"; PREV_REV="{prev}"; LOG="{tmp_path}/log"; RB=1\n'
        '_git() { git -C "$SRC" "$@"; }\n'
        + _rollback_block()
        + 'echo "RB=$RB"\n'
    )
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    return out.stdout


def test_checkout_unmoved_head_keeps_uncommitted_work(tmp_path):
    repo, prev = _repo(tmp_path)
    (repo / "wip.txt").write_text("operator work in progress\n")
    _run_rollback(repo, "checkout", prev, tmp_path)
    assert (repo / "wip.txt").read_text() == "operator work in progress\n"


def test_checkout_moved_head_goes_back_and_keeps_work(tmp_path):
    repo, prev = _repo(tmp_path)
    (repo / "a.txt").write_text("a2\n")
    _git(repo, "commit", "-qam", "update")
    (repo / "wip.txt").write_text("operator work in progress\n")
    out = _run_rollback(repo, "checkout", prev, tmp_path)
    assert _git(repo, "rev-parse", "HEAD") == prev
    assert (repo / "a.txt").read_text() == "a1\n"
    assert (repo / "wip.txt").read_text() == "operator work in progress\n"
    assert "RB=1" in out


def test_checkout_overlapping_work_is_not_overwritten(tmp_path):
    repo, prev = _repo(tmp_path)
    (repo / "a.txt").write_text("a2\n")
    _git(repo, "commit", "-qam", "update")
    (repo / "a.txt").write_text("local edit on an updated file\n")
    out = _run_rollback(repo, "checkout", prev, tmp_path)
    assert (repo / "a.txt").read_text() == "local edit on an updated file\n"
    assert "RB=0" in out  # reported as a failed rollback, not silently "restored"


def test_managed_tree_still_resets_hard(tmp_path):
    repo, prev = _repo(tmp_path)
    (repo / "a.txt").write_text("a2\n")
    _git(repo, "commit", "-qam", "update")
    _run_rollback(repo, "managed", prev, tmp_path)
    assert _git(repo, "rev-parse", "HEAD") == prev
