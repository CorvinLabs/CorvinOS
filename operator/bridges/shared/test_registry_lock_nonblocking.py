"""shared/ registry writes must never block forever on their advisory lock.

Every registry in this directory serialises its read-modify-write cycles with a
POSIX advisory lock on a ``.lock`` sidecar. Until 2026-09-07 each took that
lock as a plain ``fcntl.flock(fd, LOCK_EX)`` with NO timeout. A wedged holder —
a crashed writer whose fd the kernel had not reaped, an NFS mount, a
debugger-stopped process — hung the caller FOREVER, and no ``try/except`` can
catch a hang. These modules sit on the bridge message path (adapter.py) and on
the L38 remote-trigger receiver, so one wedged lock file took the request with
it.

They are bounded now (``LOCK_EX | LOCK_NB`` + deadline, the contract from
``core.infinite_session.event_store``). What each site does AT the deadline is
the load-bearing part, and it is what these tests pin:

  consent    REFUSE on the write path (grant/revoke); DEGRADE on the lazy
             expiry-prune persist inside ``is_granted`` — and the busy prune
             must still DENY the expired uid, because the decision is taken
             from the pruned in-memory snapshot.
  quota      REFUSE — a quota write that silently did not land under-counts
             usage, i.e. hands the user free messages.
  roles      REFUSE — roles are an authorisation mechanism.
  disclosure REFUSE, and NOT via the OSError retry ladder (which would re-wait
             the deadline four times on a request path).
  acs        DEGRADE — documented fail-OPEN daily backstop counter that guards
             no compliance decision.

Every test holds the lock from an INDEPENDENT file description — exactly what a
foreign process holding it looks like to ``flock`` — and every compliance site
additionally asserts the busy path did not GRANT anything.

Run:  cd operator/bridges && ../../.venv/bin/python shared/test_registry_lock_nonblocking.py
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _bounded_lock  # noqa: E402
import acs_engine_adapter  # noqa: E402
import consent  # noqa: E402
import disclosure  # noqa: E402
import quota  # noqa: E402
import roles  # noqa: E402

# The deadline is what is under test, not its default value. 5.0s is the
# "did it block?" ceiling in every assertion.
SHORT_DEADLINE = 0.2
BLOCKED_CEILING = 5.0


@contextmanager
def _held(lock_path: Path):
    """Hold ``lock_path`` from an independent file description."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


@contextmanager
def _deadline(module, seconds: float = SHORT_DEADLINE):
    prev = module.LOCK_TIMEOUT_SECONDS
    module.LOCK_TIMEOUT_SECONDS = seconds
    try:
        yield
    finally:
        module.LOCK_TIMEOUT_SECONDS = prev


class _TmpHome(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="lockbusy-")
        self._prev = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev
        shutil.rmtree(self._tmp, ignore_errors=True)


# ── the helper itself ─────────────────────────────────────────────────────


class BoundedFlockTests(_TmpHome):
    def test_acquire_raises_instead_of_waiting(self):
        lock_path = Path(self._tmp) / "probe.lock"
        with _held(lock_path):
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                started = time.monotonic()
                with self.assertRaises(_bounded_lock.LockBusy):
                    _bounded_lock.acquire_exclusive(fd, "probe", timeout=SHORT_DEADLINE)
                elapsed = time.monotonic() - started
            finally:
                os.close(fd)
        self.assertLess(elapsed, BLOCKED_CEILING)

    def test_it_is_still_a_real_mutex_when_free(self):
        lock_path = Path(self._tmp) / "probe2.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _bounded_lock.acquire_exclusive(fd, "probe", timeout=SHORT_DEADLINE)
            # A second, independent description must now find it taken.
            other = open(lock_path, "a+")
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                other.close()
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def test_lock_busy_is_a_timeout_error(self):
        """Callers that already degrade on I/O failure keep degrading."""
        self.assertTrue(issubclass(_bounded_lock.LockBusy, TimeoutError))
        self.assertTrue(issubclass(_bounded_lock.LockBusy, OSError))


# ── consent (L16) ─────────────────────────────────────────────────────────


class ConsentLockTests(_TmpHome):
    CH, KEY, UID = "discord", "555", "u1"

    def _store(self) -> Path:
        return consent._store_path(self.CH, self.KEY)

    def _lock(self) -> Path:
        p = self._store()
        return p.with_suffix(p.suffix + ".lock")

    def test_grant_refuses_and_grants_nothing_when_the_lock_is_wedged(self):
        store = self._store()
        store.parent.mkdir(parents=True, exist_ok=True)
        with _deadline(consent), _held(self._lock()):
            started = time.monotonic()
            with self.assertRaises(consent.ConsentLockBusy):
                consent.grant(self.CH, self.KEY, self.UID)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, BLOCKED_CEILING)
        # The refusal granted NOTHING — is_granted still denies.
        granted, reason = consent.is_granted(self.CH, self.KEY, self.UID)
        self.assertFalse(granted, reason)

    def test_revoke_refuses_rather_than_reporting_a_revoke_that_did_not_land(self):
        consent.grant(self.CH, self.KEY, self.UID)
        with _deadline(consent), _held(self._lock()):
            started = time.monotonic()
            with self.assertRaises(consent.ConsentLockBusy):
                consent.revoke(self.CH, self.KEY, self.UID)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, BLOCKED_CEILING)
        # Still on file: the caller was told, not silently lied to.
        self.assertTrue(consent.is_granted(self.CH, self.KEY, self.UID)[0])

    def test_is_granted_degrades_on_the_prune_write_but_still_denies_expired(self):
        """The ONE documented degrade in consent, and it may not become an allow.

        ``is_granted`` persists the lazy expiry prune best-effort. With the
        store lock wedged that write is skipped — but the decision is taken
        from the PRUNED in-memory snapshot, so the expired uid is still denied.
        """
        consent.grant(self.CH, self.KEY, self.UID, ttl_s=consent.MIN_TTL_S)
        # Age the entry past its expiry directly on disk.
        store = self._store()
        data = json.loads(store.read_text())
        data[self.UID]["expires_at"] = time.time() - 1
        store.write_text(json.dumps(data))

        with _deadline(consent), _held(self._lock()):
            started = time.monotonic()
            granted, reason = consent.is_granted(self.CH, self.KEY, self.UID)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, BLOCKED_CEILING, "is_granted blocked on a wedged lock")
        self.assertFalse(granted, f"busy prune lock became an ALLOW: {reason}")
        self.assertEqual(reason, "expired")
        # It degraded rather than raising: the expired entry is still on disk.
        self.assertIn(self.UID, json.loads(store.read_text()))

    def test_grant_still_works_once_the_lock_is_free(self):
        consent.grant(self.CH, self.KEY, self.UID)
        self.assertTrue(consent.is_granted(self.CH, self.KEY, self.UID)[0])

    def test_the_cli_boundary_reports_lock_busy_as_json_not_a_traceback(self):
        """The REAL CLI entry point (subprocess), which the JS handler parses.

        ``/consent on`` is spawned by the bridge's slash-command handler and
        its stdout is parsed as JSON — a traceback would reach the operator as
        an opaque crash, and a zero exit would read as a grant.
        """
        import subprocess

        env = dict(os.environ)
        env["CORVIN_HOME"] = self._tmp
        # Default deadline (2 s) applies in the child; keep the hold short.
        self._store().parent.mkdir(parents=True, exist_ok=True)
        with _held(self._lock()):
            started = time.monotonic()
            proc = subprocess.run(
                [sys.executable, str(HERE / "consent.py"),
                 "on", self.CH, self.KEY, self.UID],
                capture_output=True, text=True, env=env, timeout=30,
            )
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 10.0, "the CLI blocked on a wedged lock")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["error"], "lock_busy", payload)
        # …and nothing was granted.
        self.assertFalse(consent.is_granted(self.CH, self.KEY, self.UID)[0])


# ── quota (L20) ───────────────────────────────────────────────────────────


class QuotaLockTests(_TmpHome):
    CH, KEY, UID = "discord", "555", "u1"

    def _lock(self) -> Path:
        p = quota._store_path(self.CH, self.KEY)
        return p.with_suffix(p.suffix + ".lock")

    def test_record_refuses_rather_than_under_counting_usage(self):
        """A silently-dropped quota write hands the user free messages."""
        quota.record(self.CH, self.KEY, self.UID, role="member", tokens=10)
        before = quota.get_usage(self.CH, self.KEY, self.UID, role="member")["messages_today"]

        with _deadline(quota), _held(self._lock()):
            started = time.monotonic()
            with self.assertRaises(quota.QuotaLockBusy):
                quota.record(self.CH, self.KEY, self.UID, role="member", tokens=10)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, BLOCKED_CEILING)
        after = quota.get_usage(self.CH, self.KEY, self.UID, role="member")["messages_today"]
        self.assertEqual(after, before, "a refused record must not appear to have landed")

    def test_the_check_gate_itself_never_takes_the_lock(self):
        """``check()`` is read-only, so a wedged writer cannot stall the gate."""
        quota.record(self.CH, self.KEY, self.UID, role="member", tokens=10)
        with _deadline(quota), _held(self._lock()):
            started = time.monotonic()
            result = quota.check(self.CH, self.KEY, self.UID, role="member")
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, BLOCKED_CEILING)
        self.assertIn("allowed", result)

    def test_set_limit_refuses_and_does_not_raise_the_cap(self):
        with _deadline(quota), _held(self._lock()):
            with self.assertRaises(quota.QuotaLockBusy):
                quota.set_limit(self.CH, self.KEY, self.UID, limit_msgs=9999)
        usage = quota.get_usage(self.CH, self.KEY, self.UID, role="member")
        self.assertFalse(usage["limit_msgs_overridden"],
                         "a busy lock raised the operator's quota cap")


# ── roles (L18) ───────────────────────────────────────────────────────────


class RolesLockTests(_TmpHome):
    # A channel with no ``settings.json`` has an EMPTY whitelist, which
    # ``is_intrinsic_owner`` treats as DEV mode (every uid is owner) — that is
    # what gives the grantor authority here. Using "discord" would read the
    # checked-in bridge settings and answer "insufficient-authority" instead.
    CH, KEY, UID = "lockprobe", "555", "u1"

    def _lock(self) -> Path:
        p = roles._store_path(self.CH, self.KEY)
        return p.with_suffix(p.suffix + ".lock")

    def test_grant_refuses_and_writes_no_role_entry(self):
        with _deadline(roles), _held(self._lock()):
            started = time.monotonic()
            with self.assertRaises(roles.RolesLockBusy):
                roles.grant(self.CH, self.KEY, "target1",
                            bundle="member", granted_by="grantor1")
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, BLOCKED_CEILING)
        store = roles._store_path(self.CH, self.KEY)
        on_disk = json.loads(store.read_text()) if store.exists() else {}
        self.assertNotIn("target1", on_disk, "a busy lock granted a role bundle")

    def test_grant_still_lands_once_the_lock_is_free(self):
        roles.grant(self.CH, self.KEY, "target1", bundle="member", granted_by="grantor1")
        store = roles._store_path(self.CH, self.KEY)
        self.assertIn("target1", json.loads(store.read_text()))

    def test_role_reads_never_take_the_lock(self):
        with _deadline(roles), _held(self._lock()):
            started = time.monotonic()
            role = roles.effective_role(self.CH, self.KEY, self.UID)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, BLOCKED_CEILING)
        self.assertIsInstance(role, str)


# ── disclosure (L19, EU AI Act Art. 50) ───────────────────────────────────


class DisclosureLockTests(_TmpHome):
    CH, KEY, UID = "discord", "555", "u1"

    def _lock(self) -> Path:
        p = disclosure._store_path(self.CH, self.KEY)
        return p.with_suffix(p.suffix + ".lock")

    def test_mark_seen_refuses_and_the_card_stays_unseen(self):
        """A busy lock must not report the disclosure card as recorded."""
        with _deadline(disclosure), _held(self._lock()):
            started = time.monotonic()
            with self.assertRaises(disclosure.DisclosureLockBusy):
                disclosure.mark_seen(self.CH, self.KEY, self.UID)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, BLOCKED_CEILING)
        self.assertFalse(
            disclosure.has_seen(self.CH, self.KEY, self.UID),
            "a busy lock suppressed the disclosure card",
        )

    def test_the_busy_lock_is_not_fed_into_the_osfailure_retry_ladder(self):
        """The ladder would re-wait the deadline 4x (~9s) on a request path.

        The deadline is 0.2s here, and the ladder's own sleeps total 1.4s — so
        anything under 1.0s proves the ladder was skipped, not merely that the
        acquire was bounded.
        """
        with _deadline(disclosure), _held(self._lock()):
            started = time.monotonic()
            with self.assertRaises(disclosure.DisclosureLockBusy):
                disclosure.mark_seen(self.CH, self.KEY, self.UID)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0, f"retry ladder re-waited the deadline ({elapsed:.2f}s)")

    def test_has_seen_never_takes_the_lock(self):
        with _deadline(disclosure), _held(self._lock()):
            started = time.monotonic()
            disclosure.has_seen(self.CH, self.KEY, self.UID)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, BLOCKED_CEILING)

    def test_mark_seen_still_works_once_the_lock_is_free(self):
        disclosure.mark_seen(self.CH, self.KEY, self.UID)
        self.assertTrue(disclosure.has_seen(self.CH, self.KEY, self.UID))


# ── acs fallback counter (documented fail-open backstop) ──────────────────


class AcsFallbackCounterLockTests(_TmpHome):
    def _lock(self) -> Path:
        return acs_engine_adapter._acs_runs_dir("_default").parent / "fallback_count.lock"

    def test_it_degrades_instead_of_hanging_the_submit(self):
        """This counter guards no compliance decision — degrading is correct.

        The module documents it as a fail-OPEN backstop ("the cap is a
        backstop, not a security boundary"): every operational error already
        returns ``(True, -1)``. A busy lock joins them EXPLICITLY (logged)
        rather than hanging the console submit forever.
        """
        lock = self._lock()
        lock.parent.mkdir(parents=True, exist_ok=True)
        with _deadline(acs_engine_adapter), _held(lock):
            started = time.monotonic()
            allowed, count = acs_engine_adapter._fallback_quota_ok("_default")
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, BLOCKED_CEILING)
        self.assertTrue(allowed)
        self.assertEqual(count, -1, "the degrade must be visible as an uncounted run")

    def test_it_still_counts_when_the_lock_is_free(self):
        allowed, count = acs_engine_adapter._fallback_quota_ok("_default")
        self.assertTrue(allowed)
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
