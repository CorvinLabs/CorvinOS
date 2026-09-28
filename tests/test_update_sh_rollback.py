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
    # up to the keep-refusal exit — the source-revision part of the rollback
    m = re.search(r"^RB=1\n(.*?)^if \[ \"\$\{KEEP_REFUSED:-0\}\" = 1 \]", text, re.S | re.M)
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


def _rollback_tail(text: str | None = None) -> str:
    """Everything from ``RB=1`` to the end of update.sh — the whole rollback."""
    text = UPDATE_SH.read_text() if text is None else text
    i = text.index("\nRB=1\n")
    return text[i + 1:]


def _run_rollback_tail(repo: Path, prev: str, tmp_path: Path, text: str | None = None):
    web = tmp_path / "web"
    (web / "dist").mkdir(parents=True)
    (web / "dist" / "marker").write_text("new\n")
    (web / "dist.prev").mkdir()
    (web / "dist.prev" / "marker").write_text("old\n")
    calls = tmp_path / "calls"
    script = (
        f'SRC="{repo}"; KIND="checkout"; PREV_REV="{prev}"; LOG="{tmp_path}/log"; WEB="{web}"\n'
        '_git() { git -C "$SRC" "$@"; }\n'
        '_r() { printf "%s" "$1"; }; _y() { printf "%s" "$1"; }\n'
        f'_quiet() {{ echo "quiet $1" >>"{calls}"; }}\n'
        f'_uv_install_healing() {{ echo reinstall >>"{calls}"; }}\n'
        f'restart_all() {{ echo restart >>"{calls}"; }}\n'
        'wait_live() { return 0; }\n'
        + _rollback_tail(text)
    )
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    called = calls.read_text() if calls.exists() else ""
    return out, (web / "dist" / "marker").read_text(), called


def test_keep_refusal_leaves_new_tree_frontend_and_services_alone(tmp_path):
    """--keep refused (local edits overlap the update): the old frontend must
    not be paired with the new code, nothing is reinstalled or restarted, and
    the message says what actually happened and how to go back."""
    repo, prev = _repo(tmp_path)
    (repo / "a.txt").write_text("a2\n")
    _git(repo, "commit", "-qam", "update")
    new = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("local edit on an updated file\n")
    out, dist_marker, called = _run_rollback_tail(repo, prev, tmp_path)
    assert out.returncode == 2, out
    assert _git(repo, "rev-parse", "HEAD") == new
    assert (repo / "a.txt").read_text() == "local edit on an updated file\n"
    assert dist_marker == "new\n", "old frontend restored onto the new code"
    assert called == "", f"reinstalled/restarted the NEW code as a 'rollback': {called!r}"
    assert "Rollback failed too" not in out.stderr, out.stderr
    assert "Not rolled back" in out.stderr and prev[:9] in out.stderr, out.stderr
    assert "stash" in out.stderr, out.stderr


def test_clean_rollback_tail_still_restores_everything(tmp_path):
    """Positive control for the tail harness: a rollback --keep accepts goes
    back, restores the previous frontend, reinstalls and reports success."""
    repo, prev = _repo(tmp_path)
    (repo / "a.txt").write_text("a2\n")
    _git(repo, "commit", "-qam", "update")
    out, dist_marker, called = _run_rollback_tail(repo, prev, tmp_path)
    assert out.returncode == 1, out  # 1 = failed update, rolled back
    assert _git(repo, "rev-parse", "HEAD") == prev
    assert dist_marker == "old\n"
    assert "reinstall" in called or "quiet" in called, called
    assert "Rolled back" in out.stdout, out
