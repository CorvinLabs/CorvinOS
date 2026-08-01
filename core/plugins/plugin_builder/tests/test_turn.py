"""turn.py — checkpoint retry/data-loss discipline and cross-store exclusivity
(ADR-0262/0263 review rounds 1+2; this file didn't exist before round 2
flagged the gap — see round-2 Backend finding 2)."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest

from plugin_builder import ideation, session_store, turn
from plugin_builder.interview import InterviewPhase, InterviewSession


@pytest.fixture(autouse=True)
def _tmp_output_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(turn, "output_dir", lambda tenant_id: Path(d))
        yield Path(d)


def _register_in_store(tenant_id: str, key: str, session: InterviewSession) -> None:
    """Test-only: install a hand-built ``session`` into ``session_store``'s
    slot for ``(tenant_id, key)``, the same way ``start()`` would if it had
    constructed this object itself — routed through ``_remove_locked`` for
    any prior occupant so removal hooks fire, exactly like production.
    Needed because these tests build a session by hand (some answers already
    applied) rather than through ``session_store.start()`` — and, since
    round 7, ``_checkpoint_reply`` only caches state for whichever session
    object ``session_store`` actually holds for that key (see its
    docstring), so a test driving a hand-built session through CHECKPOINT
    must register it here first or the write is a no-op by design."""
    import time as _time

    with session_store._lock:  # noqa: SLF001
        store_key = session_store._key(tenant_id, key)  # noqa: SLF001
        if store_key in session_store._sessions:  # noqa: SLF001
            session_store._remove_locked(store_key)  # noqa: SLF001
        session_store._sessions[store_key] = session_store._Entry(  # noqa: SLF001
            session=session, last_touched=_time.time()
        )


def _new_checkpoint_session(session_id: str) -> InterviewSession:
    session = InterviewSession(
        session_id=session_id, idea_first=True, checkpoint_enabled=True,
    )
    session.answer("A plugin idea with no special signals at all.")
    session.answer("Retry Test Plugin")
    while session.phase == InterviewPhase.CONFIRM_GAPS:
        session.answer("none")
    assert session.phase == InterviewPhase.REVIEW
    return session


def test_failed_checkpoint_write_does_not_set_docs_written(monkeypatch):
    session = _new_checkpoint_session("t1")

    def _boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(turn, "write_idea_docs", _boom)
    reply = turn.drive(session, "confirm", tenant_id="_default", session_key="t1-key")
    assert "failed" in reply.lower()
    assert session.phase == InterviewPhase.CHECKPOINT
    assert session.checkpoint_docs_written is False


def test_retry_after_failed_write_recovers_the_session(monkeypatch):
    session = _new_checkpoint_session("t2")
    _register_in_store("_default", "t2-key", session)

    calls = {"n": 0}
    from plugin_builder.generators import write_idea_docs as _real_write_idea_docs

    def _fail_once(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return _real_write_idea_docs(*a, **kw)

    monkeypatch.setattr(turn, "write_idea_docs", _fail_once)
    first = turn.drive(session, "confirm", tenant_id="_default", session_key="t2-key")
    assert "failed" in first.lower()
    assert session.checkpoint_docs_written is False

    # Retry — ANY text (not just "confirm") should retry the write, per the
    # round-1 fix, since nothing was cached the first time.
    second = turn.drive(session, "please try again", tenant_id="_default", session_key="t2-key")
    assert "failed" not in second.lower()
    assert session.checkpoint_docs_written is True
    assert calls["n"] == 2
    session_store.clear("_default", "t2-key")


def test_cancel_after_failed_write_is_honored_not_retried(monkeypatch):
    """Round-2 regression: the round-1 retry fix treated ANY text arriving
    while checkpoint_docs_written=False as an implicit retry — including a
    genuine "cancel"/"restart" decision, which got silently swallowed."""
    session = _new_checkpoint_session("t3")
    monkeypatch.setattr(turn, "write_idea_docs", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    turn.drive(session, "confirm", tenant_id="_default", session_key="t3-key")
    assert session.checkpoint_docs_written is False

    reply = turn.drive(session, "cancel", tenant_id="_default", session_key="t3-key")
    assert "cancel" in reply.lower()
    assert session.phase == InterviewPhase.CANCELLED
    assert session.is_finished()


def test_cancel_after_failed_write_is_case_and_whitespace_insensitive(monkeypatch):
    """Round-3 finding: the round-2 fix's own unit-level token normalization
    was tested, but no test drove a non-lowercase/whitespace-padded cancel
    through the actual failed-write-then-cancel sequence in turn.drive()."""
    session = _new_checkpoint_session("t3b")
    monkeypatch.setattr(turn, "write_idea_docs", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    turn.drive(session, "confirm", tenant_id="_default", session_key="t3b-key")
    assert session.checkpoint_docs_written is False

    reply = turn.drive(session, "  CANCEL  ", tenant_id="_default", session_key="t3b-key")
    assert "cancel" in reply.lower()
    assert session.phase == InterviewPhase.CANCELLED


def test_restart_after_failed_write_is_honored_not_retried(monkeypatch):
    session = _new_checkpoint_session("t4")
    monkeypatch.setattr(turn, "write_idea_docs", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    turn.drive(session, "confirm", tenant_id="_default", session_key="t4-key")
    assert session.checkpoint_docs_written is False

    turn.drive(session, "restart", tenant_id="_default", session_key="t4-key")
    assert session.phase == InterviewPhase.IDEA
    assert session.checkpoint_docs_written is False


def test_starting_plain_interview_replaces_an_active_ideas_session():
    """Symmetric to ideation.start()'s own cross-store check — round 2 found
    the reverse direction was unguarded: a plain /plugin-builder used to
    leave a stale --ideas session running, silently swallowing the new
    interview's answers (ideation.continue_active is checked before
    session_store by both transports)."""
    tenant_id, key = "_default", "cross-store-key"
    session_store.clear(tenant_id, key)
    ideation.clear(tenant_id, key)
    try:
        ideation.start(tenant_id, key, idea_first=True)
        assert ideation.is_active(tenant_id, key)

        reply = turn.command("", tenant_id=tenant_id, session_key=key)
        assert "replaced" in reply.lower()
        assert not ideation.is_active(tenant_id, key)
        assert session_store.get(tenant_id, key) is not None
    finally:
        session_store.clear(tenant_id, key)
        ideation.clear(tenant_id, key)


def test_concurrent_plain_and_ideas_start_never_leave_both_stores_holding_a_session():
    """Round-3 regression: the round-1/round-2 cross-store checks were each
    correct sequentially but raced against each other — two concurrent
    calls (a double-click, two tabs, a bridge retry) could both pass their
    "is the other store active?" check before either had written, leaving
    BOTH `session_store` and `ideation`'s own store holding a session for
    the same caller at once (verified with this exact thread-barrier
    reproduction before `session_store.cross_store_lock` was added)."""
    import threading

    tenant_id, key = "_default", "cross-store-race-key"
    session_store.clear(tenant_id, key)
    ideation.clear(tenant_id, key)
    try:
        barrier = threading.Barrier(2)

        def start_plain():
            barrier.wait()
            turn.command("", tenant_id=tenant_id, session_key=key)

        def start_ideas():
            barrier.wait()
            ideation.start(tenant_id, key, idea_first=True)

        threads = [threading.Thread(target=start_plain), threading.Thread(target=start_ideas)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        has_plain = session_store.get(tenant_id, key) is not None
        has_ideas = ideation.is_active(tenant_id, key)
        assert not (has_plain and has_ideas), (
            f"both stores hold a session at once: plain={has_plain} ideas={has_ideas}"
        )
        assert has_plain or has_ideas, "exactly one store should hold the winning session"
    finally:
        session_store.clear(tenant_id, key)
        ideation.clear(tenant_id, key)


def _finished_checkpoint_session(name: str) -> InterviewSession:
    """Two DIFFERENT InterviewSession OBJECTS built with the SAME
    session_id string — exactly what a double-click/bridge-retry produces
    (session_store.start() derives session_id deterministically from
    (tenant_id, session_key), so every object for one caller shares it)."""
    session = InterviewSession(
        session_id="shared-key", idea_first=True, checkpoint_enabled=True,
    )
    session.answer(f"A plugin idea for {name}, fully vague, no signals.")
    session.answer(name)
    while session.phase == InterviewPhase.CONFIRM_GAPS:
        session.answer("none")
    return session


def test_two_sessions_sharing_a_session_id_do_not_corrupt_each_others_checkpoint_state():
    """Round-4 regression, TIGHTENED in round 7: _checkpoint_state used to be
    keyed by the deterministic session_id STRING, not object identity — two
    different InterviewSession objects for the same caller (a legitimate
    shape: a double-click/bridge-retry starting a new one while an old one
    is still finishing) collided on that key, and a later _finish_reply
    could write one session's idea into the other's scaffold directory.
    Round 4 fixed the collision by keying on id(session) instead — but round
    7 found id()-keying alone still let a STALE object's _checkpoint_reply
    cache an entry nobody would ever clean up if session_store had already
    moved on to a newer object for the same key (a permanent orphan, see the
    _checkpoint_state module comment). _checkpoint_reply now also refuses to
    cache anything unless it IS session_store's CURRENT session for that
    key — so this test now asserts the strictly stronger guarantee: only
    the object actually registered in session_store gets a working
    checkpoint -> scaffold path; a superseded object is a harmless dead end,
    never a silent cross-contamination or a leak."""
    tenant_id, key = "_default", "shared-key"
    session_store.clear(tenant_id, key)
    try:
        session_a = _finished_checkpoint_session("Alpha")
        _register_in_store(tenant_id, key, session_a)
        turn.drive(session_a, "confirm", tenant_id=tenant_id, session_key=key)
        assert id(session_a) in turn._checkpoint_state  # noqa: SLF001
        assert session_a.checkpoint_docs_written is True

        # session_b replaces session_a in session_store — same shape as a
        # double-click/bridge-retry racing session_a's own finish. The
        # replace routes through _remove_locked (see _register_in_store),
        # so the removal hook drops session_a's entry here, same as a real
        # session_store.start() would.
        session_b = _finished_checkpoint_session("Beta")
        assert session_a.session_id == session_b.session_id  # the actual precondition
        _register_in_store(tenant_id, key, session_b)
        assert id(session_a) not in turn._checkpoint_state  # noqa: SLF001
        turn.drive(session_b, "confirm", tenant_id=tenant_id, session_key=key)
        assert id(session_b) in turn._checkpoint_state  # noqa: SLF001
        assert session_b.checkpoint_docs_written is True

        # session_a is now stale — session_store no longer holds it — so
        # driving IT to CHECKPOINT would refuse to cache (already proven
        # above), and its own _finish_reply must fail gracefully rather
        # than ever read session_b's cached state under the same key.
        reply_a = turn._finish_reply(session_a, tenant_id=tenant_id)  # noqa: SLF001
        assert "something went wrong" in reply_a.lower()

        reply_b = turn._finish_reply(session_b, tenant_id=tenant_id)  # noqa: SLF001
        assert "beta" in reply_b.lower() and "alpha" not in reply_b.lower()
    finally:
        session_store.clear(tenant_id, key)


def test_finished_session_does_not_clear_a_newer_sessions_slot():
    """Round-4 regression: drive()'s final session_store.clear() ran
    unconditionally — a session object finishing AFTER a newer,
    legitimate session had already replaced it in session_store silently
    deleted that newer session instead of its own (already-stale) slot."""
    tenant_id, key = "_default", "clobber-key"
    session_store.clear(tenant_id, key)
    try:
        old_session = InterviewSession(session_id=f"{tenant_id}:{key}", idea_first=True)
        old_session.answer("A vague idea with no clear signals at all.")
        old_session.answer("Old Session")
        while old_session.phase == InterviewPhase.CONFIRM_GAPS:
            old_session.answer("none")
        assert old_session.phase == InterviewPhase.REVIEW

        # A newer session has since taken the slot (simulating a concurrent
        # /plugin-builder start racing the old session's finish) — same
        # deterministic session_id, different object.
        new_session = session_store.start(tenant_id, key)
        assert session_store.get(tenant_id, key) is new_session

        # old_session finishes (REVIEW -> CANCELLED, inside drive()'s own
        # session.answer() call) and reaches is_finished() == True.
        turn.drive(old_session, "cancel", tenant_id=tenant_id, session_key=key)

        assert session_store.get(tenant_id, key) is new_session, (
            "the newer session was clobbered by the stale one's cleanup"
        )
    finally:
        session_store.clear(tenant_id, key)


def test_session_store_clear_expected_is_atomic_even_under_a_widened_race_window(monkeypatch):
    """Round-5 regression: a round-4 version of this guard (`_owns_slot()`)
    checked ownership, then called `clear()` as a SEPARATE lock acquisition
    — a real gap a concurrent start() could land in between the two calls.
    `session_store.clear(..., expected=...)` must do the check-and-delete
    under ONE lock acquisition; verified here by artificially widening the
    window between two threads to a size no accidental race would ever
    produce, and confirming the newer session still survives."""
    import time

    tenant_id, key = "_default", "toctou-key"
    session_store.clear(tenant_id, key)
    try:
        old_session = session_store.start(tenant_id, key)
        new_session_holder = {}
        cleared_holder = {}

        real_clear = session_store.clear

        def widened_clear(*a, **kw):
            time.sleep(0.05)  # widen any check-to-delete gap far beyond realistic scheduling jitter
            real_clear(*a, **kw)
            cleared_holder["done"] = True

        monkeypatch.setattr(session_store, "clear", widened_clear)

        def stale_clear():
            session_store.clear(tenant_id, key, expected=old_session)

        def concurrent_new_start():
            time.sleep(0.01)  # land inside the widened window above
            new_session_holder["session"] = session_store.start(tenant_id, key)

        t1 = threading.Thread(target=stale_clear)
        t2 = threading.Thread(target=concurrent_new_start)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert cleared_holder.get("done") is True
        assert session_store.get(tenant_id, key) is new_session_holder["session"], (
            "the newer session was wiped despite the atomic expected= guard"
        )
    finally:
        session_store.clear(tenant_id, key)


def test_command_cancel_does_not_clobber_a_session_started_during_its_own_window(monkeypatch):
    """Round-5 regression: turn.command()'s cancel branch had NO ownership
    guard at all (not even the TOCTOU-vulnerable one drive() had) — between
    its internal session_store.get() and clear(), a concurrent turn could
    start a brand-new session for the same caller and have it wiped."""
    import time

    tenant_id, key = "_default", "cancel-clobber-key"
    session_store.clear(tenant_id, key)
    try:
        stale_session = session_store.start(tenant_id, key)
        new_session_holder = {}

        real_get = session_store.get

        def widened_get(*a, **kw):
            result = real_get(*a, **kw)
            time.sleep(0.05)  # widen command()'s internal get()-to-clear() gap
            return result

        monkeypatch.setattr(session_store, "get", widened_get)

        def run_cancel():
            turn.command("cancel", tenant_id=tenant_id, session_key=key)

        def concurrent_new_start():
            time.sleep(0.01)  # land inside the widened window above
            new_session_holder["session"] = session_store.start(tenant_id, key)

        t1 = threading.Thread(target=run_cancel)
        t2 = threading.Thread(target=concurrent_new_start)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert session_store.get(tenant_id, key) is new_session_holder["session"], (
            "a stale cancel clobbered a session started during its own race window"
        )
        assert stale_session is not new_session_holder["session"]  # precondition sanity
    finally:
        session_store.clear(tenant_id, key)


def test_checkpoint_state_removal_hook_fires_on_unconditional_clear():
    """Round-6 regression: _checkpoint_state was keyed by id(session)
    (round 4) under the assumption that a session can't be garbage-collected
    while session_store still holds it — true only if EVERY removal path
    tells this dict the session is gone. An unconditional clear() (the
    documented flag-off-cleanup shape, no `expected=`) used to skip that
    entirely, so a later id() reuse could hand a stale checkpoint entry to
    an unrelated new session. Tested deterministically here — via the hook
    actually firing, not by relying on CPython reusing a specific address,
    which the round-6 reproduction observed but this suite shouldn't depend
    on for a reliable pass/fail signal."""
    tenant_id, key = "_default", "id-reuse-key"
    session_store.clear(tenant_id, key)
    try:
        session_a = session_store.start(tenant_id, key)
        turn._checkpoint_state[id(session_a)] = (Path("/fake"), "fake.id", 0.0)
        session_id_a = id(session_a)
        assert session_id_a in turn._checkpoint_state  # precondition

        session_store.clear(tenant_id, key)  # unconditional — no expected=

        assert session_id_a not in turn._checkpoint_state, (
            "an unconditional clear() left a stale _checkpoint_state entry "
            "behind — the removal hook did not fire"
        )
    finally:
        session_store.clear(tenant_id, key)


def test_start_replacing_an_existing_session_also_fires_the_removal_hook():
    """Round-6 regression: session_store.start() replaced an existing entry
    via a bare dict assignment, bypassing every removal hook — the same
    class of gap as an unconditional clear(), just via a fourth, distinct
    removal path (a direct restart without an intervening clear())."""
    tenant_id, key = "_default", "start-replace-key"
    session_store.clear(tenant_id, key)
    try:
        session_a = session_store.start(tenant_id, key)
        turn._checkpoint_state[id(session_a)] = (Path("/fake"), "fake.id", 0.0)
        session_id_a = id(session_a)
        assert session_id_a in turn._checkpoint_state  # precondition

        session_store.start(tenant_id, key)  # replaces without an explicit clear()

        assert session_id_a not in turn._checkpoint_state, (
            "start() replaced a session without firing its removal hook"
        )
    finally:
        session_store.clear(tenant_id, key)


def test_checkpoint_write_racing_a_concurrent_replace_does_not_orphan_state(monkeypatch):
    """Round-7 regression: _checkpoint_reply used to write
    ``_checkpoint_state[id(session)]`` unconditionally once
    ``write_idea_docs()`` (unbounded disk I/O) returned — with no check that
    ``session_store`` still held this exact session for ``(tenant_id,
    session_key)``. A concurrent ``session_store.start()`` replacing the
    slot WHILE the write was still in flight fired the removal hook as a
    no-op (nothing to remove yet — the entry didn't exist), and the write
    that followed then created an entry no future removal event would ever
    fire for again: a permanent orphan, bounded only by TTL/MAX eviction,
    not eliminated by the hook the way its own docstring otherwise promises
    (found live via an 8x60 randomized stress run, reproduced
    deterministically here by blocking the write on an Event until a
    concurrent start() has already replaced the session)."""
    import time

    tenant_id, key = "_default", "checkpoint-race-key"
    session_store.clear(tenant_id, key)
    try:
        session = _new_checkpoint_session(f"{tenant_id}:{key}")
        _register_in_store(tenant_id, key, session)

        replaced = threading.Event()
        real_write = turn.write_idea_docs

        def _blocking_write(*a, **kw):
            replaced.wait(timeout=5)
            return real_write(*a, **kw)

        monkeypatch.setattr(turn, "write_idea_docs", _blocking_write)

        def _drive():
            turn.drive(session, "confirm", tenant_id=tenant_id, session_key=key)

        t = threading.Thread(target=_drive)
        t.start()
        time.sleep(0.05)  # let the thread reach and block inside _blocking_write

        # A concurrent /plugin-builder replaces the session before the
        # write returns — same shape as a double-click/bridge-retry.
        session_store.start(tenant_id, key)
        replaced.set()
        t.join(timeout=5)

        assert id(session) not in turn._checkpoint_state, (
            "a session superseded mid-write left an orphaned _checkpoint_state entry"
        )
        assert session.checkpoint_docs_written is False
    finally:
        session_store.clear(tenant_id, key)
