"""A KB checkout whose ``.git/index`` a hard-killed writer left unreadable.

Before: ``git status`` failed with ``fatal: unknown index entry format 0x…``, the projector
ignored the stderr, and every 2-s tick re-ran heal/export against the broken checkout (log
noise forever, board writes failing with an unrelated error). Now the projector reports it
ONCE as an ERROR naming the repair, pauses for ``KB_INDEX_RETRY_S`` and resumes on its own
once the index is readable again; board writes are refused with the same hint.

Real git, a real corrupted index (the extended-flag bit set on a v2 entry — the exact
failure seen on 2026-11-06).
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from corvin_console import kb_projection as kp


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "kb-repo"
    (r / "kb" / "tasks").mkdir(parents=True)
    (r / "kb" / "tasks" / "T-0001.md").write_text("---\nid: T-0001\n---\n", encoding="utf-8")
    assert _git(r.parent, "init", "-q", str(r)).returncode == 0
    assert _git(r, "add", "-A").returncode == 0
    assert _git(r, "commit", "-qm", "init").returncode == 0
    return r


def _corrupt_index(repo: Path) -> None:
    p = repo / ".git" / "index"
    b = bytearray(p.read_bytes())
    assert b[:4] == b"DIRC" and int.from_bytes(b[8:12], "big") >= 1
    b[12 + 60:12 + 62] = b"\x52\x41"     # flags of entry 1: extended bit on a v2 index
    p.write_bytes(bytes(b))


def _repair(repo: Path) -> None:
    (repo / ".git" / "index").replace(repo / ".git" / "index.bad")
    assert _git(repo, "reset", "-q").returncode == 0


@pytest.fixture
def projector(monkeypatch, repo):
    saved: list[dict] = []
    monkeypatch.setattr(kp, "kb_repo", lambda tenant_id=None: repo)
    monkeypatch.setattr(kp, "_uses_tenant_kb", lambda tenant_id: False)
    monkeypatch.setattr(kp, "_save", lambda tenant_id, st: saved.append(dict(st)))
    calls: list[tuple] = []

    def fake_run(_repo, *args, **_kw):
        calls.append(args)
        return 1, {"error": "stop here"}      # the test is about what happens BEFORE kb runs
    monkeypatch.setattr(kp, "_run", fake_run)
    monkeypatch.setattr(kp, "_state", {})
    monkeypatch.setattr(kp, "_index_corrupt", {})
    return saved, calls


def test_uncommitted_work_raises_on_a_corrupt_index(repo):
    assert kp._uncommitted_work(repo) == []
    _corrupt_index(repo)
    with pytest.raises(kp.KbIndexCorrupt, match="unknown index entry format"):
        kp._uncommitted_work(repo)


def test_projector_reports_once_pauses_and_resumes(repo, projector, caplog, monkeypatch):
    saved, calls = projector
    _corrupt_index(repo)
    caplog.set_level(logging.WARNING, logger=kp.log.name)

    st = kp.sync("_t")
    assert st["state"] == "error" and "index corrupt" in st["error"]
    assert "git -C" in st["repair"] and "reset -q" in st["repair"]
    assert calls == []                                    # heal/export never ran on the broken checkout
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "index.lock" in errors[0].getMessage()

    # within the back-off: no git, no kb, no log line
    for _ in range(5):
        kp.sync("_t")
    assert calls == []
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1

    # back-off elapsed, still broken: still exactly one ERROR
    kp._index_corrupt["_t"] = (0.0, "x")
    kp.sync("_t")
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1

    # repaired: the next check resumes the normal tick (reaches kb heal/export again)
    _repair(repo)
    kp._index_corrupt["_t"] = (0.0, "x")
    kp.sync("_t")
    assert "_t" not in kp._index_corrupt
    assert calls and calls[0] == ("heal",)
    assert any("resumed" in r.getMessage() for r in caplog.records)


@pytest.fixture
def stateful_projector(monkeypatch, repo):
    """A projector whose kb subprocesses succeed and whose state persists between ticks."""
    state: dict = {}
    monkeypatch.setattr(kp, "kb_repo", lambda tenant_id=None: repo)
    monkeypatch.setattr(kp, "_uses_tenant_kb", lambda tenant_id: False)
    monkeypatch.setattr(kp, "_state", state)
    monkeypatch.setattr(kp, "_save", lambda tenant_id, st: state.__setitem__(tenant_id, dict(st)))
    monkeypatch.setattr(kp, "_index_corrupt", {})
    calls: list[tuple] = []

    def fake_run(_repo, *args, **_kw):
        calls.append(args)
        return {"export": (0, {"ok": True, "items": []}), "queue": (0, {"queue": []})}.get(
            args[0], (0, {}))
    monkeypatch.setattr(kp, "_run", fake_run)
    monkeypatch.setattr(kp.projection, "apply", lambda tenant_id, payload, **kw: {"state": "ok", "remaining": 0})
    monkeypatch.setattr(kp.projection, "diff", lambda tenant_id, payload: [])
    return state, calls


def test_a_repair_that_changes_no_kb_file_still_clears_the_error(repo, stateful_projector):
    """Review 2026-11-06: `git reset` changes neither HEAD nor a KB file, so a kept
    fingerprint matched and the error state stuck until the KB changed."""
    state, calls = stateful_projector
    assert kp.sync("_t")["state"] == "ok"
    _corrupt_index(repo)
    assert kp.sync("_t")["state"] == "error"
    _repair(repo)
    # a manual resync (force) is honoured inside the back-off
    st = kp.sync("_t", force=True)
    assert st["state"] == "ok", st
    assert "_t" not in kp._index_corrupt
    # and the plain tick afterwards stays ok
    assert kp.sync("_t")["state"] == "ok"


def test_without_force_the_back_off_holds_and_the_next_tick_after_it_recovers(repo, stateful_projector):
    state, calls = stateful_projector
    kp.sync("_t")
    _corrupt_index(repo)
    kp.sync("_t")
    _repair(repo)
    assert kp.sync("_t")["state"] == "error"          # inside the back-off: not re-checked
    kp._index_corrupt["_t"] = (0.0, "x")
    assert kp.sync("_t")["state"] == "ok"             # after it: full re-export


def test_an_unreadable_index_inside_the_tick_is_a_state_not_an_exception(repo, stateful_projector, monkeypatch):
    """transition()/create_item() call sync() after their KB write was applied: an index
    that breaks mid-tick must not turn that into a 500."""
    state, calls = stateful_projector
    real = kp._uncommitted_work
    n = {"calls": 0}

    def flaky(r):
        n["calls"] += 1
        if n["calls"] >= 2:                             # fingerprint ok, the later check breaks
            raise kp.KbIndexCorrupt("fatal: unknown index entry format 0x52410000")
        return real(r)
    monkeypatch.setattr(kp, "_uncommitted_work", flaky)
    st = kp.sync("_t", force=True)
    assert st["state"] == "error" and st["fingerprint"] is None
