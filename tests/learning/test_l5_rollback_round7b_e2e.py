"""ADR-2092 G4 — round-7b findings on the L5 rollback judge (review c120 r7b).

r7b-1: a pending entry (lock-failure hold, judged-but-unrecorded trip) was dropped as
"stale" by a WALL-CLOCK comparison (the reset state's ``at`` vs the entry's
``created``); staleness is now decided by generations only.
r7b-2/-3: the post-reset window was an inode number + byte offset; a re-created ledger
(reused inode) or one truncated in place and regrown hid the post-reset turns. The
position now carries device + inode + a fingerprint of the bytes before the offset,
and any mismatch reads the ledger whole.
r7b-4: an EMPTY/torn marker recorded a trip over 0 samples; it is now a hold.
r7b-5: the reset route answered 200 ``tripped: false`` while a marker it could not
remove still held the tenant; it now answers 503 ``marker_not_removed``, no record.
r7b-6: a process holding the tenant in-process re-judged over another process's
committed judged marker and chained a second ``routing.rollback_triggered``.
An unreadable ledger position at reset answers 503 ``ledger_position_unreadable``
(it said ``state_not_written``).

Entry points as production calls them: ``delegation_policy.record_turn_outcome``
(starts the judge thread for a Skill-served turn), ``dual_write.decide`` (the phase
gate) and ``POST /v1/console/learning/routing/rollback/reset`` through the real
console router with a real session + CSRF token.
"""
from __future__ import annotations

import json
import os
import sys
import time as _real_time
import types

import pytest

from tests.learning.console_client import console_client
from tests.learning.test_l5_rollback_lock_and_window_e2e import _phase, _seed, _skill_turn
from tests.learning.test_l5_rollback_reset_route_e2e import RESET, _named, _trip, _verify_chain

_NAMES = ("routing.rollback_triggered", "routing.rollback_reset",
          "routing.rollback_unverified", "routing.rollback_reset_not_applied",
          "routing.rollback_hold_released")


def _events(sb) -> list[str]:
    out = []
    for r in sb.chain_records():
        ev = r.get("event") or r.get("event_type")
        if ev in _NAMES:
            out.append(ev)
    return out


@pytest.fixture(autouse=True)
def _phase2(monkeypatch):
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    yield
    from core.skills.os_skills.monitoring import dual_write, rollback_detector

    rollback_detector.reset_for_tests()
    dual_write.reset_for_tests()


def _clock(offset: float):
    return types.SimpleNamespace(
        time=lambda: _real_time.time() + offset, time_ns=_real_time.time_ns,
        monotonic=_real_time.monotonic, sleep=_real_time.sleep)


def _lock(sb):
    from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

    return routing_ledger.routing_dir(sb.tenant_id) / rollback_detector.LOCK_FILENAME


def _reset_with_clock(sb, monkeypatch, *, reset_offset: float, after_offset: float) -> None:
    """Trip, reset with the detector's clock at ``reset_offset``, then run on with it
    at ``after_offset`` (an NTP step between the reset and what follows)."""
    from core.skills.os_skills.monitoring import rollback_detector

    _trip(sb)
    monkeypatch.setattr(rollback_detector, "time", _clock(reset_offset))
    r = sb.client.post(RESET, headers=sb.csrf_headers)
    assert r.status_code == 200, r.text
    monkeypatch.setattr(rollback_detector, "time", _clock(after_offset))


# ── r7b-1: staleness by generations only ─────────────────────────────────────

# (reset clock, clock afterwards): a reset taken +1 h ahead, then NTP steps back;
# a reset at the true time, then the clock steps 1 h back.
_CLOCKS = [pytest.param(3600.0, 0.0, id="reset_clock_plus_1h"),
           pytest.param(0.0, -3600.0, id="clock_minus_1h_after_reset")]


@pytest.mark.parametrize("reset_offset,after_offset", _CLOCKS)
def test_a_lock_failure_hold_survives_a_clock_step_around_the_reset(
        tmp_path, monkeypatch, reset_offset, after_offset):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _reset_with_clock(sb, monkeypatch, reset_offset=reset_offset,
                          after_offset=after_offset)
        lock = _lock(sb)
        os.chmod(lock, 0o400)
        try:
            _skill_turn(sb.tenant_id, ok=False)        # the judge: lock unavailable -> hold
            assert rollback_detector.rollback_active(sb.tenant_id), \
                "the hold was erased by a wall-clock comparison"
            assert sb.tenant_id in rollback_detector._pending_trips
            assert rollback_detector.pending_path(sb.tenant_id).exists()
            assert _phase(sb.tenant_id).reason == "rollback_active"
        finally:
            os.chmod(lock, 0o600)
        assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset",
                               "routing.rollback_unverified"]


@pytest.mark.parametrize("reset_offset,after_offset", _CLOCKS)
def test_a_judged_trip_whose_record_failed_survives_a_clock_step_around_the_reset(
        tmp_path, monkeypatch, reset_offset, after_offset):
    with console_client(tmp_path) as sb:
        import delegation_policy
        from core.skills.os_skills.monitoring import rollback_detector

        _reset_with_clock(sb, monkeypatch, reset_offset=reset_offset,
                          after_offset=after_offset)
        _seed(sb.tenant_id, 150, source="skill", ok=False)
        _seed(sb.tenant_id, 150, source="bundled", ok=True)
        real_audit = delegation_policy._audit

        def _trip_fails(et, d, *, tenant_id):
            if et == "routing.rollback_triggered":
                return False
            return real_audit(et, d, tenant_id=tenant_id)
        monkeypatch.setattr(delegation_policy, "_audit", _trip_fails)
        _skill_turn(sb.tenant_id, ok=False)            # judged; its record does not commit
        monkeypatch.setattr(delegation_policy, "_audit", real_audit)
        assert rollback_detector.rollback_active(sb.tenant_id), \
            "a judged trip was erased by a wall-clock comparison"
        assert rollback_detector.pending_path(sb.tenant_id).exists()
        _seed(sb.tenant_id, 1000, source="skill", ok=True)   # the window recovers
        _skill_turn(sb.tenant_id, ok=True)             # the retry completes the JUDGED trip
        assert rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset",
                               "routing.rollback_triggered"]
        rec = _named(sb, "routing.rollback_triggered")[-1]
        assert (rec.get("details") or {}).get("skill_n") == 151
        assert _named(sb, "routing.rollback_hold_released") == []


def test_a_newer_persisted_generation_still_drops_a_stale_in_process_entry(
        tmp_path, monkeypatch):
    """No regression of r3_s2-5: another process's reset (a newer generation) wins over
    this process's judged-but-unrecorded trip."""
    with console_client(tmp_path) as sb:
        import delegation_policy
        from core.skills.os_skills.monitoring import rollback_detector

        _seed(sb.tenant_id, 150, source="skill", ok=False)
        _seed(sb.tenant_id, 150, source="bundled", ok=True)
        real_audit = delegation_policy._audit
        monkeypatch.setattr(delegation_policy, "_audit", lambda et, d, *, tenant_id: (
            False if et == "routing.rollback_triggered"
            else real_audit(et, d, tenant_id=tenant_id)))
        _skill_turn(sb.tenant_id, ok=False)
        monkeypatch.setattr(delegation_policy, "_audit", real_audit)
        entry = dict(rollback_detector._pending_trips[sb.tenant_id])
        gen_before = rollback_detector.persisted_generation(sb.tenant_id)
        assert entry["generation"] == gen_before
        r = sb.client.post(RESET, headers=sb.csrf_headers)     # the operator, "elsewhere"
        assert r.status_code == 200, r.text
        rollback_detector._pending_trips[sb.tenant_id] = entry  # this process's memory
        assert rollback_detector.persisted_generation(sb.tenant_id) > gen_before
        assert rollback_detector.rollback_active(sb.tenant_id) is False
        assert sb.tenant_id not in rollback_detector._pending_trips
        _skill_turn(sb.tenant_id, ok=True)
        assert _named(sb, "routing.rollback_triggered") == []
        assert _events(sb) == ["routing.rollback_reset"]


# ── r7b-2/-3: the ledger position names content, not an inode number ─────────

def _assert_post_reset_failures_trip(sb) -> None:
    from core.skills.os_skills.monitoring import rollback_detector

    assert rollback_detector.rollback_active(sb.tenant_id), \
        "151 failing Skill turns after the reset did not trip (window blinded)"
    assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset",
                           "routing.rollback_triggered"]
    state = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())
    assert state["tripped"] is True and state["skill_n"] == 151


def _post_reset_failures(sb) -> None:
    _seed(sb.tenant_id, 150, source="skill", ok=False)
    _seed(sb.tenant_id, 150, source="bundled", ok=True)
    _skill_turn(sb.tenant_id, ok=False)


def test_a_recreated_ledger_with_a_reused_inode_is_read_whole(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

        _trip(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        led = routing_ledger.ledger_path(sb.tenant_id)
        led.unlink()                                  # the operator frees disk space
        _seed(sb.tenant_id, 150, source="skill", ok=False)
        _seed(sb.tenant_id, 150, source="bundled", ok=True)
        # Simulate inode-number reuse deterministically: the stored position names the
        # NEW file's device and inode (the filesystem here reuses it on its own too).
        st = os.stat(led)
        sp = rollback_detector.state_path(sb.tenant_id)
        state = json.loads(sp.read_text())
        assert state["ledger_pos"]["offset"] < st.st_size
        state["ledger_pos"]["ino"] = st.st_ino
        state["ledger_pos"]["dev"] = st.st_dev
        tmp = sp.with_name(sp.name + ".sim.tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(sp)
        _skill_turn(sb.tenant_id, ok=False)
        _assert_post_reset_failures_trip(sb)
        _verify_chain(sb)


def test_a_ledger_truncated_in_place_and_regrown_is_read_whole(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

        _trip(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        pos = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())["ledger_pos"]
        assert set(pos) == {"dev", "ino", "offset", "fp"}
        led = routing_ledger.ledger_path(sb.tenant_id)
        ino = os.stat(led).st_ino
        os.truncate(led, 0)                           # copytruncate / `: > file`
        _post_reset_failures(sb)
        assert os.stat(led).st_ino == ino and os.stat(led).st_size > pos["offset"]
        _assert_post_reset_failures_trip(sb)


def test_an_untouched_ledger_still_keeps_the_pre_reset_rows_out(tmp_path):
    """The fingerprint matches on an append-only ledger: the window starts at the reset."""
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _trip(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        _skill_turn(sb.tenant_id, ok=False)
        assert not rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset"]


# ── r7b-4: an empty / torn marker is a hold, never a 0-sample trip ───────────

@pytest.mark.parametrize("content", ["", '{"pending": tr'], ids=["empty", "torn"])
def test_an_empty_or_torn_marker_holds_without_a_zero_sample_trip(tmp_path, content):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        m = rollback_detector.pending_path(sb.tenant_id)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(content)
        _skill_turn(sb.tenant_id, ok=False)           # one Skill turn: 1 sample, 0 bundled
        assert _named(sb, "routing.rollback_triggered") == [], \
            "a trip was recorded over a torn marker"
        assert not rollback_detector.state_path(sb.tenant_id).exists()
        assert rollback_detector.rollback_active(sb.tenant_id)
        assert _phase(sb.tenant_id).reason == "rollback_active"


def test_a_torn_marker_on_a_healthy_judgeable_window_is_released_audited(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _seed(sb.tenant_id, 120, source="skill", ok=True)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        m = rollback_detector.pending_path(sb.tenant_id)
        m.write_text("")
        _skill_turn(sb.tenant_id, ok=True)
        assert not rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_hold_released"]
        rec = _named(sb, "routing.rollback_hold_released")[0]
        assert (rec.get("details") or {}).get("cause") == "marker_torn"
        assert not m.exists()


def test_a_torn_marker_on_a_trailing_window_trips_on_the_judged_window(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _seed(sb.tenant_id, 120, source="skill", ok=False)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        rollback_detector.pending_path(sb.tenant_id).write_text("")
        _skill_turn(sb.tenant_id, ok=False)
        assert rollback_detector.rollback_active(sb.tenant_id)
        recs = _named(sb, "routing.rollback_triggered")
        assert len(recs) == 1 and (recs[0].get("details") or {}).get("skill_n") == 121


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc/self/fd")
def test_both_marker_writers_fsync_before_the_rename(tmp_path, monkeypatch):
    """Every fsync is attributed to the file it synced (``/proc/self/fd``): the audit
    writer fsyncs too, so a bare count would pass without the marker writers'."""
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        synced: list[str] = []
        real_fsync = os.fsync

        def _fsync(fd):
            try:
                synced.append(os.path.basename(os.readlink(f"/proc/self/fd/{fd}")))
            except OSError:
                pass
            return real_fsync(fd)
        monkeypatch.setattr(os, "fsync", _fsync)
        marker = rollback_detector.PENDING_FILENAME
        state = rollback_detector.STATE_FILENAME

        def _tmps(prefix: str, suffix: str = ".tmp") -> list[str]:
            return [n for n in synced if n.startswith(prefix + ".") and n.endswith(suffix)]
        lock = _lock(sb)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("")
        os.chmod(lock, 0o400)
        try:
            _skill_turn(sb.tenant_id, ok=False)       # hold marker: _create_marker_if_absent
        finally:
            os.chmod(lock, 0o600)
        assert _tmps(marker, ".hold.tmp"), \
            f"the hold marker was linked without an fsync of its temp file: {synced}"
        synced.clear()
        _seed(sb.tenant_id, 120, source="skill", ok=False)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        _skill_turn(sb.tenant_id, ok=False)           # judged marker + state: _write_state
        assert [n for n in _tmps(marker) if not n.endswith(".hold.tmp")], \
            f"the judged marker was renamed without an fsync: {synced}"
        assert _tmps(state), f"the trip state was renamed without an fsync: {synced}"
        assert json.loads(rollback_detector.state_path(sb.tenant_id).read_text())["tripped"]


# ── r7b-5: never 200 "not tripped" while still held ──────────────────────────

def test_reset_with_an_unremovable_marker_is_503_and_writes_no_record(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _seed(sb.tenant_id, 120, source="skill", ok=True)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        m = rollback_detector.pending_path(sb.tenant_id)
        m.mkdir()                                     # unreadable AND unremovable
        try:
            assert rollback_detector.rollback_active(sb.tenant_id)
            r = sb.client.post(RESET, headers=sb.csrf_headers)
            assert r.status_code == 503, r.text
            assert r.json()["detail"] == {"error": "marker_not_removed"}
            assert _named(sb, "routing.rollback_reset") == []
            assert not rollback_detector.state_path(sb.tenant_id).exists()
            assert rollback_detector.rollback_active(sb.tenant_id)
        finally:
            m.rmdir()


def test_a_marker_that_survives_the_reset_answers_503_and_chains_not_applied(
        tmp_path, monkeypatch):
    """The belt behind the pre-check: should the marker still hold the tenant AFTER
    the reset's record and state (the pre-check passed, the unlink then failed), the
    route still never answers 200 — the chain names the reset not applied."""
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _trip(sb)
        m = rollback_detector.pending_path(sb.tenant_id)
        m.mkdir()                                     # unremovable by unlink()
        monkeypatch.setattr(rollback_detector, "_marker_removable", lambda _t: True)
        try:
            r = sb.client.post(RESET, headers=sb.csrf_headers)
            assert r.status_code == 503, r.text
            assert r.json()["detail"] == {"error": "marker_not_removed"}
            assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset",
                                   "routing.rollback_reset_not_applied"]
            assert rollback_detector.rollback_active(sb.tenant_id)
        finally:
            m.rmdir()


# ── r7b-6: one trip, one record, across two processes ────────────────────────

#: Process P2, run as its own interpreter: no in-process entry; it judges the trailing
#: window, its record commits, its trip-state write fails (ENOSPC) — so the marker it
#: leaves says committed.
_CHILD = r"""
import sys
from core.skills.os_skills.monitoring import rollback_detector

tenant = sys.argv[1]
real_ws = rollback_detector._write_state


def _ws(tenant_id, data, *, path=None):
    if path is None and data.get("tripped") is True:
        raise OSError(28, "No space left on device")
    return real_ws(tenant_id, data, path=path)


rollback_detector._write_state = _ws
rollback_detector.evaluate(tenant, audit=rollback_detector._default_audit(tenant))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes (read-only lock file)")
def test_a_hold_does_not_rechain_a_trip_another_process_committed(tmp_path):
    """P2 is a SEPARATE interpreter, as the bridge and the console are in production.
    A ``multiprocessing`` ``fork`` of this multi-threaded test process copied the audit
    writer's in-process ``_write_lock`` in whatever state another thread held it, and
    the child deadlocked inside its own ``routing.rollback_triggered`` write while
    holding ``rollback.lock`` (measured: /proc/locks named the child as the flock
    holder, its traceback stood in ``security_events.write_event``)."""
    import subprocess

    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _seed(sb.tenant_id, 120, source="skill", ok=False)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        lock = _lock(sb)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("")
        os.chmod(lock, 0o400)
        try:
            _skill_turn(sb.tenant_id, ok=False)       # P1 (this process): in-process hold
        finally:
            os.chmod(lock, 0o600)
        assert rollback_detector._pending_trips[sb.tenant_id]["cause"] == "lock_unavailable"
        child = subprocess.run([sys.executable, "-c", _CHILD, sb.tenant_id],
                               env=dict(os.environ), capture_output=True, text=True,
                               timeout=120)
        assert child.returncode == 0, child.stderr[-4000:]
        marker = json.loads(rollback_detector.pending_path(sb.tenant_id).read_text())
        assert marker["committed"] is True and marker.get("cause") is None
        assert len(_named(sb, "routing.rollback_triggered")) == 1
        _skill_turn(sb.tenant_id, ok=False)           # P1 gets the lock and judges
        assert len(_named(sb, "routing.rollback_triggered")) == 1, \
            "one trip, two trip records"
        state = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())
        assert state["tripped"] is True and state["skill_n"] == marker["details"]["skill_n"]
        assert not rollback_detector.pending_path(sb.tenant_id).exists()
# ── item 6: the 503 names an unreadable ledger position ─────────────────

def test_reset_with_an_unreadable_ledger_position_names_the_cause(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

        _trip(sb)
        led = routing_ledger.ledger_path(sb.tenant_id)
        os.chmod(led, 0o000)
        try:
            r = sb.client.post(RESET, headers=sb.csrf_headers)
        finally:
            os.chmod(led, 0o600)
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == {"error": "ledger_position_unreadable"}
        assert _events(sb) == ["routing.rollback_triggered"]
        assert rollback_detector.rollback_active(sb.tenant_id)
