"""update.sh: a failed update never leaves the autostash unannounced (review 2026-11-19, TEN-7).

Executes the real ``_restore_stash`` and ``_on_exit`` functions extracted from
update.sh against a sandbox git repository - update.sh itself is never run.
"""
from __future__ import annotations

import re
import time
import signal
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UPDATE_SH = REPO / "update.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required")


def _function(name: str) -> str:
    text = UPDATE_SH.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{.*?^\}}\n", text, re.S | re.M)
    assert m, f"{name} not found in update.sh"
    return m.group(0)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "src"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a1\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "one")
    (r / "a.txt").write_text("my uncommitted work\n", encoding="utf-8")
    _git(r, "stash", "push", "-q", "-m", "corvin-update autostash test")
    assert (r / "a.txt").read_text(encoding="utf-8") == "a1\n"
    return r


def _run(repo: Path, tmp_path: Path, body: str, *, stashed: bool = True) -> subprocess.CompletedProcess:
    # ``stashed``: start as update.sh is after ITS autostash - STASHED=1 and
    # STASH_SHA naming that commit (the fixture's stash stands in for it).
    state = 'STASHED=1; STASH_SHA="$(git -C "$SRC" rev-parse refs/stash)"' if stashed else 'STASHED=0; STASH_SHA=""'
    script = (
        f'SRC="{repo}"; LOG="{tmp_path}/log"; LOCK_DIR="{tmp_path}/lock"\n'
        'export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t\n'
        '_git() { git -C "$SRC" "$@"; }\n'
        f'{state}\n'
        "_r() { printf '%s' \"$*\"; }\n"
        "warn() { printf 'WARN %s\\n' \"$*\"; }\n"
        "ok() { printf 'OK %s\\n' \"$*\"; }\n"
        + _function("_autostash") + _function("_drop_autostash") + _function("_apply_autostash")
        + _function("_restore_stash") + _function("_on_exit") + body
    )
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def test_failed_update_puts_the_changes_back(repo, tmp_path):
    out = _run(repo, tmp_path, 'trap _on_exit EXIT\n_restore_stash\necho "STASHED=$STASHED"\nexit 1\n')
    assert "STASHED=0" in out.stdout
    assert "were put back" in out.stdout
    assert (repo / "a.txt").read_text(encoding="utf-8") == "my uncommitted work\n"
    assert _git(repo, "stash", "list") == ""
    assert "git stash" not in out.stderr          # nothing left to announce


def test_conflicting_changes_stay_in_the_stash_and_are_announced(repo, tmp_path):
    (repo / "a.txt").write_text("upstream rewrote this line\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "upstream")
    out = _run(repo, tmp_path, 'trap _on_exit EXIT\n_restore_stash\necho "STASHED=$STASHED"\nexit 1\n')
    assert out.returncode == 1
    assert "STASHED=1" in out.stdout
    assert "changes are in git stash" in out.stdout
    assert "corvin-update autostash test" in _git(repo, "stash", "list")   # still there
    text = (repo / "a.txt").read_text(encoding="utf-8")
    assert text == "upstream rewrote this line\n" and "<<<<<<<" not in text  # clean tree
    # the loud exit note names OUR stash by its commit, not "whatever is on top"
    sha = _git(repo, "rev-parse", "refs/stash")
    assert "are in git stash" in out.stderr and f"stash apply {sha}" in out.stderr


def test_exit_note_only_on_failure_with_a_pending_stash(repo, tmp_path):
    ok = _run(repo, tmp_path, "trap _on_exit EXIT\nexit 0\n")
    assert ok.stderr == ""
    failed = _run(repo, tmp_path, "trap _on_exit EXIT\nexit 2\n")
    assert "NOT lost" in failed.stderr
    none = _run(repo, tmp_path, "trap _on_exit EXIT\nSTASHED=0\nexit 2\n")
    assert none.stderr == ""


def test_the_failure_branch_calls_restore_before_dying():
    text = UPDATE_SH.read_text(encoding="utf-8")
    branch = text.split("checkout) fetch_checkout || {", 1)[1].split("} ;;", 1)[0]
    assert branch.index("_restore_stash") < branch.index("die ")
    assert "nothing changed" in branch and "are in git stash" in branch
    assert "trap _on_exit EXIT" in text


def test_update_ps1_has_the_same_guard():
    ps1 = (REPO / "update.ps1").read_text(encoding="utf-8-sig")
    block = ps1.split('Send-UpdateProgress delivery failed "download failed"', 1)[1][:1400]
    assert 'Invoke-Git @("stash", "apply", "-q", $script:StashSha)' in block
    assert "changes are in git stash" in block


# ───────────── refutation round (R2-CON-5): never somebody else's stash ─────────────

@pytest.fixture
def submodule_dirty_repo(tmp_path):
    """A checkout whose ONLY dirt is inside a submodule, plus an older,
    unrelated stash. ``git status --porcelain --untracked-files=no`` is
    non-empty there, ``git stash push`` exits 0 and stashes nothing."""
    sub_origin = tmp_path / "sub-origin"
    sub_origin.mkdir()
    _git(sub_origin, "init", "-q", "-b", "main")
    (sub_origin / "s.txt").write_text("s1\n", encoding="utf-8")
    _git(sub_origin, "add", ".")
    _git(sub_origin, "commit", "-qm", "sub one")

    r = tmp_path / "src"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a1\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "one")
    _git(r, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_origin), "sub")
    _git(r, "commit", "-qm", "add submodule")

    # Somebody's own, older stash - not the updater's.
    (r / "a.txt").write_text("FOREIGN WORK - not the updater's\n", encoding="utf-8")
    _git(r, "stash", "push", "-q", "-m", "my own experiment")
    assert (r / "a.txt").read_text(encoding="utf-8") == "a1\n"

    # Now the tree is dirty ONLY inside the submodule.
    (r / "sub" / "s.txt").write_text("dirty inside the submodule\n", encoding="utf-8")
    assert _git(r, "status", "--porcelain", "--untracked-files=no") != ""   # update.sh's trigger
    return r


def test_a_push_that_stashes_nothing_does_not_claim_a_stash(submodule_dirty_repo, tmp_path):
    repo = submodule_dirty_repo
    foreign = _git(repo, "rev-parse", "refs/stash")
    out = _run(repo, tmp_path, '_autostash; echo "rc=$? STASHED=$STASHED SHA=[$STASH_SHA]"\n', stashed=False)
    assert "rc=0 STASHED=0 SHA=[]" in out.stdout, out.stdout + out.stderr
    assert _git(repo, "rev-parse", "refs/stash") == foreign


def test_a_failed_update_leaves_a_foreign_stash_and_the_tree_alone(submodule_dirty_repo, tmp_path):
    repo = submodule_dirty_repo
    foreign = _git(repo, "rev-parse", "refs/stash")
    out = _run(repo, tmp_path,
               'trap _on_exit EXIT\n_autostash\n_restore_stash\necho "STASHED=$STASHED"\nexit 1\n',
               stashed=False)
    assert "STASHED=0" in out.stdout
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a1\n"          # foreign work NOT applied
    assert (repo / "sub" / "s.txt").read_text(encoding="utf-8") == "dirty inside the submodule\n"
    assert _git(repo, "rev-parse", "refs/stash") == foreign               # still there, untouched
    assert _git(repo, "stash", "list").count("\n") == 0
    assert "git stash" not in out.stderr                                   # nothing of ours to announce


def test_the_success_path_does_not_drop_a_foreign_stash(submodule_dirty_repo, tmp_path):
    """fetch_checkout's tail: apply + drop. It used to apply and DROP stash@{0}."""
    repo = submodule_dirty_repo
    foreign = _git(repo, "rev-parse", "refs/stash")
    text = UPDATE_SH.read_text(encoding="utf-8")
    tail = text.split("# Put the local changes back (git pull --autostash semantics).", 1)[1]
    tail = tail[tail.index('    if [ "$STASHED" = 1 ]; then'):].split("\n}\n", 1)[0]
    assert "_apply_autostash" in tail and "_drop_autostash" in tail            # positive control
    out = _run(repo, tmp_path, "_autostash\n" + tail + '\necho "STASHED=$STASHED"\n', stashed=False)
    assert "STASHED=0" in out.stdout, out.stdout + out.stderr
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a1\n"
    assert _git(repo, "rev-parse", "refs/stash") == foreign


def test_our_stash_is_applied_and_dropped_even_below_a_newer_one(repo, tmp_path):
    """Found by commit: a stash made on top of ours meanwhile is not the one taken."""
    ours = _git(repo, "rev-parse", "refs/stash")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "stash", "push", "-q", "-m", "a newer foreign stash")
    newer = _git(repo, "rev-parse", "refs/stash")
    assert newer != ours
    out = _run(repo, tmp_path,
               f'STASH_SHA={ours}\n_restore_stash\necho "STASHED=$STASHED"\n')
    assert "STASHED=0" in out.stdout, out.stdout + out.stderr
    assert (repo / "a.txt").read_text(encoding="utf-8") == "my uncommitted work\n"
    assert not (repo / "b.txt").exists()
    listing = _git(repo, "stash", "list")
    assert "a newer foreign stash" in listing and "corvin-update autostash test" not in listing


def test_no_positional_stash_command_is_left():
    """Every stash apply/drop/pop in both updaters names a commit or a found ref."""
    sh = UPDATE_SH.read_text(encoding="utf-8")
    code = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
    # The one remaining "stash pop" is the manual go-back hint, where the
    # operator pops the stash they made themselves one command earlier.
    pops = [l for l in code.splitlines() if "stash pop" in l]
    assert len(pops) == 1 and "To go back" in code.splitlines()[code.splitlines().index(pops[0]) - 1], pops
    found = list(re.finditer(r"stash (apply|drop) -q(?: --index)?([^\n>]*)", code))
    assert len(found) >= 3, "positive control: the stash commands were found"
    for m in found:
        assert m.group(2).strip().startswith('"$'), m.group(0)
    ps1 = (REPO / "update.ps1").read_text(encoding="utf-8-sig")
    assert not re.search(r'Invoke-Git @\("stash", "(pop|apply|drop)", "-q"\)', ps1)
    assert ps1.count("Get-StashTip") >= 3 and "$script:StashSha = $stashAfter" in ps1


def test_a_signal_ends_the_run_and_releases_the_lock(tmp_path):
    """INT/TERM used to remove the lock and RETURN into the script."""
    text = UPDATE_SH.read_text(encoding="utf-8")
    traps = [l.strip() for l in text.splitlines() if l.startswith("trap ")]
    assert traps == ["trap _on_exit EXIT", "trap '_on_signal 130' INT", "trap '_on_signal 143' TERM"], traps
    install = (REPO / "install.sh").read_text(encoding="utf-8")
    itraps = [l.strip() for l in install.splitlines() if l.strip().startswith("trap ")]
    assert itraps == ["trap '' INT TERM", "trap 'rm -rf \"$LOCK_DIR\"' EXIT",
                      "trap '_on_signal 130' INT", "trap '_on_signal 143' TERM"], itraps

    lock = tmp_path / "lock"
    lock.mkdir()
    marker = tmp_path / "continued"
    script = (
        f'LOCK_DIR="{lock}"; STASHED=0; SRC=.\n'
        "_r() { printf '%s' \"$*\"; }\n"
        + _function("_on_exit") + _function("_tree_pids") + _function("_kill_tree")
        + _function("_on_signal") + "\n".join(traps) + "\n"
        'kill -TERM $$\n'
        'sleep 1\n'
        f': > "{marker}"\n'
    )
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    assert out.returncode == 143, (out.returncode, out.stderr)
    assert not marker.exists(), "the script carried on after SIGTERM"
    assert not lock.exists(), "the lock must be released on the way out"


# ── R3-CON-8: a signal stops the background step before the lock is released ─

def _signal_script(tmp_path: Path, source: Path, runner: str, pid_var: str) -> str:
    text = source.read_text(encoding="utf-8")

    def fn(name: str) -> str:
        m = re.search(rf"^{name}\(\) \{{.*?^\}}\n", text, re.S | re.M)
        assert m, f"{name} not found in {source.name}"
        return m.group(0)

    lock = tmp_path / "lock"
    step = (
        f'echo $$ > "{tmp_path}/step.pid"; '
        f'(sleep 30; : > "{tmp_path}/grandchild.done") & echo $! > "{tmp_path}/grandchild.pid"; '
        f'sleep 6; : > "{tmp_path}/step.done"'
    )
    return (
        f'LOG="{tmp_path}/log"; INSTALL_LOG="$LOG"; LOCK_DIR="{lock}"; mkdir "$LOCK_DIR"; STASHED=0; SRC=.\n'
        "log() { :; }\n"
        "_g() { printf '%s' \"$*\"; }; _r() { printf '%s' \"$*\"; }\n"
        "_green() { printf '%s' \"$*\"; }; _yellow() { printf '%s' \"$*\"; }; _dim() { printf '%s' \"$*\"; }\n"
        + fn(runner) + fn("_tree_pids") + fn("_kill_tree") + fn("_on_signal")
        + "trap 'rm -rf \"$LOCK_DIR\"' EXIT\n"
        "trap '_on_signal 130' INT\ntrap '_on_signal 143' TERM\n"
        f"{runner} 'long build' sh -c '{step}'\n"
        f': > "{tmp_path}/continued"\n'
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill -0; it is not running.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


@pytest.mark.parametrize("script,runner,pid_var", [
    ("update.sh", "_quiet", "_q_pid"), ("install.sh", "_await", "_aw_pid")])
@pytest.mark.parametrize("sig,code,whole_group", [
    (signal.SIGINT, 130, True),     # Ctrl-C: the group gets it, the "&" step ignores it
    (signal.SIGTERM, 143, False),   # kill <pid>: only the script gets it
])
def test_a_signal_stops_the_background_step(tmp_path, script, runner, pid_var, sig, code, whole_group):
    body = _signal_script(tmp_path, REPO / script, runner, pid_var)
    proc = subprocess.Popen(["sh", "-c", body], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "grandchild.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        step = int((tmp_path / "step.pid").read_text())
        grandchild = int((tmp_path / "grandchild.pid").read_text())
        assert _alive(step) and _alive(grandchild), "positive control: the step is running"
        if whole_group:
            os.killpg(proc.pid, sig)
        else:
            os.kill(proc.pid, sig)
        assert proc.wait(30) == code
        assert not (tmp_path / "lock").exists(), "the lock is released on the way out"
        assert not _alive(step), "the background step outlived the script"
        assert not _alive(grandchild), "a child of the background step outlived the script"
        time.sleep(0.2)
        assert not (tmp_path / "step.done").exists()
        assert not (tmp_path / "continued").exists(), "the script carried on after the signal"
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_quiet_without_a_signal_still_reports_the_exit_status(tmp_path):
    text = UPDATE_SH.read_text(encoding="utf-8")
    head = (f'LOG="{tmp_path}/log"\nlog() {{ :; }}\n'
            "_g() { printf '%s' \"$*\"; }; _r() { printf '%s' \"$*\"; }\n" + _function("_quiet"))
    ok = subprocess.run(["sh", "-c", head + '_quiet m true; echo "rc=$? pid=[$_q_pid]"'],
                        capture_output=True, text=True)
    bad = subprocess.run(["sh", "-c", head + '_quiet m false; echo "rc=$? pid=[$_q_pid]"'],
                         capture_output=True, text=True)
    assert "rc=0 pid=[]" in ok.stdout and "rc=1 pid=[]" in bad.stdout, (ok.stdout, bad.stdout)
    assert "_on_signal" in text


# ── R3-CON-10: the autostash comes back WITH its index state ────────────────

@pytest.fixture
def staged_repo(tmp_path):
    r = tmp_path / "src"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a1\n", encoding="utf-8")
    (r / "b.txt").write_text("b1\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "one")
    (r / "a.txt").write_text("a1\nstaged\n", encoding="utf-8")
    _git(r, "add", "a.txt")
    (r / "a.txt").write_text("a1\nstaged\nunstaged\n", encoding="utf-8")   # MM
    (r / "new.txt").write_text("new\n", encoding="utf-8")
    _git(r, "add", "new.txt")                                               # A
    (r / "b.txt").write_text("b1\nunstaged only\n", encoding="utf-8")       # " M"
    return r


def _porcelain(repo: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                         check=True, capture_output=True, text=True).stdout
    return sorted(out.splitlines())


def test_reapplied_changes_keep_their_index_state(staged_repo, tmp_path):
    before = _porcelain(staged_repo)
    assert before == [" M b.txt", "A  new.txt", "MM a.txt"], before
    out = _run(staged_repo, tmp_path, "_autostash || exit 9\n_restore_stash\nexit 1\n", stashed=False)
    assert out.returncode == 1, out.stdout + out.stderr
    assert _porcelain(staged_repo) == before, "MM must stay MM"      # was [" M a.txt", " M b.txt", "A  new.txt"]
    assert _git(staged_repo, "stash", "list") == ""
    assert "came back unstaged" not in out.stdout


def test_index_state_that_no_longer_applies_falls_back_to_a_plain_apply(staged_repo, tmp_path):
    # The "update" adds new.txt itself: the staged add cannot be replayed into
    # the index, the content still applies to the working tree.
    body = (
        "_autostash || exit 9\n"
        'printf "new\\n" > "$SRC/new.txt"; _git add new.txt; _git commit -qm upstream\n'
        "_restore_stash\nexit 1\n"
    )
    out = _run(staged_repo, tmp_path, body, stashed=False)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "came back unstaged" in out.stdout, out.stdout + (tmp_path / "log").read_text()
    assert (staged_repo / "a.txt").read_text(encoding="utf-8") == "a1\nstaged\nunstaged\n"
    assert (staged_repo / "b.txt").read_text(encoding="utf-8") == "b1\nunstaged only\n"
    assert _git(staged_repo, "stash", "list") == "", "our entry is dropped once it is applied"


def test_the_success_path_uses_the_same_index_preserving_apply():
    text = UPDATE_SH.read_text(encoding="utf-8")
    fetch = _function("fetch_checkout")
    assert "if _apply_autostash; then" in fetch and "stash apply" not in fetch
    assert "if _apply_autostash; then" in _function("_restore_stash")
    apply_fn = _function("_apply_autostash")
    assert apply_fn.index('stash apply -q --index "$STASH_SHA"') < apply_fn.index('stash apply -q "$STASH_SHA"')
    assert text.count("_apply_autostash") == 3
