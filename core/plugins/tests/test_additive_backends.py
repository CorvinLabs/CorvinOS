"""Tests for the additive audit/user backends and the boot tripwire (ADR-0233).

The load-bearing claim under test is ADR-0233 D4: an installed plugin may only
*add* to a compliance mechanism, never replace or weaken it.  Concretely:

* a hostile audit backend (raises, hangs, mutates, tries to drop the record)
  cannot affect the core hash-chained write;
* a user backend that raises, times out, or returns garbage results in DENY,
  never in an admit or a guest session;
* the tripwire fails closed and has no override switch.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# ── Adjust path so tests can be run standalone ───────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]            # CorvinOS repo root
_PKG = _HERE.parents[1]             # core/plugins (holds the corvin_plugins package)
_COMPLIANCE = _REPO / "core" / "compliance"
_SHARED = _REPO / "operator" / "bridges" / "shared"
for _p in (str(_PKG), str(_COMPLIANCE), str(_SHARED), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import circuit_breaker as _breakers  # noqa: E402
from corvin_plugins.protocol import (  # noqa: E402
    KNOWN_PLUGIN_TYPES,
    AuditBackend,
    HealthStatus,
    PluginContext,
    UserBackend,
)
from corvin_plugins.providers import audit_backend as audit_provider  # noqa: E402
from corvin_plugins.providers import user_backend as user_provider  # noqa: E402


def _reset_breakers() -> None:
    """Circuit breakers live in a process-global registry — isolate every case."""
    _breakers._registry = _breakers.BreakerRegistry()



def _await_delivery(timeout: float = 3.0) -> None:
    """Block until every queued copy has actually been DELIVERED.

    Delivery happens on the provider's drain thread, so a test that wants to see the
    effect (or the log line) must wait for it INSIDE its assertion block. This used
    to poll queue_depth(), which is the wrong signal: an empty queue only means the
    worker has PICKED UP the last item, not that the backend has seen it — the exact
    confusion that made drain_now() drop the copy in flight. drain_now() now waits on
    the unfinished-task count, so it is the whole helper.
    """
    audit_provider.drain_now(timeout=timeout)

# ── Test doubles ──────────────────────────────────────────────────────────────


class RecordingAuditBackend:
    plugin_id = "test.recording-audit"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "Recording Audit"

    def __init__(self) -> None:
        self.received: list[tuple[str, dict, str, str]] = []

    def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
        self.received.append((event_type, details, severity, tenant_id))

    def verify_chain(self) -> HealthStatus:
        return HealthStatus(ok=True)

    def enforce_retention(self, max_age_days, *, tenant_id="_default"):
        return {"deleted": 0}


class ExplodingAuditBackend(RecordingAuditBackend):
    """Raises on every fan-out — the classic hostile/buggy sink."""

    plugin_id = "test.exploding-audit"

    def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
        raise RuntimeError("sink is on fire: postgres://user:pw@host/db")


class MutatingAuditBackend(RecordingAuditBackend):
    """Tries to tamper with the dict it was handed."""

    plugin_id = "test.mutating-audit"

    def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
        details.clear()
        details["injected"] = "by the plugin"
        self.received.append((event_type, details, severity, tenant_id))


class OkUserBackend:
    plugin_id = "test.ok-users"
    plugin_type = "user_backend"
    version = "1.0.0"
    display_name = "OK Users"

    async def authenticate(self, credentials):
        if credentials.get("password") == "correct":
            return {
                "user_id": "u-1",
                "roles": ["operator"],
                "password_hash": "$2b$should-be-stripped",
                "token": "should-be-stripped",
            }
        return None

    async def get_user(self, user_id):
        return {"user_id": user_id, "roles": []}

    async def list_users(self):
        return []

    async def enforce_quota(self, user_id, resource):
        return None


class ExplodingUserBackend(OkUserBackend):
    async def authenticate(self, credentials):
        raise ConnectionError("ldaps://dc1.corp.example:636 bind failed for jdoe")

    async def get_user(self, user_id):
        raise ConnectionError("nope")


class HangingUserBackend(OkUserBackend):
    async def authenticate(self, credentials):
        await asyncio.sleep(30)
        return {"user_id": "should-never-arrive", "roles": ["admin"]}

    async def enforce_quota(self, user_id, resource):
        await asyncio.sleep(30)


class GarbageUserBackend(OkUserBackend):
    """Returns things that are not principals."""

    def __init__(self, payload):
        self.payload = payload

    async def authenticate(self, credentials):
        return self.payload


# ── Protocol registration ─────────────────────────────────────────────────────


class TestProtocolTypes(unittest.TestCase):
    def test_new_plugin_types_are_known(self):
        self.assertIn("audit_backend", KNOWN_PLUGIN_TYPES)
        self.assertIn("user_backend", KNOWN_PLUGIN_TYPES)

    def test_doubles_satisfy_the_protocols(self):
        self.assertIsInstance(RecordingAuditBackend(), AuditBackend)
        self.assertIsInstance(OkUserBackend(), UserBackend)

    def test_context_carries_the_new_registry_handles(self):
        ctx = PluginContext(
            plugin_id="x",
            tenant_id="_default",
            corvin_home=Path("/tmp"),
            config={},
            audit_emit=lambda *a, **k: None,
        )
        self.assertIsNone(ctx.audit_registry)
        self.assertIsNone(ctx.user_registry)


# ── Audit fan-out ─────────────────────────────────────────────────────────────


class TestAuditFanout(unittest.TestCase):
    def setUp(self):
        _reset_breakers()

    def tearDown(self):
        audit_provider.clear()
        _reset_breakers()

    def test_no_backend_means_no_sink_and_no_error(self):
        audit_provider.clear()
        self.assertIsNone(audit_provider.get_active())
        self.assertFalse(audit_provider.fanout("x.y", {"a": 1}))

    def test_installed_backend_receives_the_copy(self):
        backend = RecordingAuditBackend()
        audit_provider.set_active(backend)
        self.assertTrue(
            audit_provider.fanout("plugin_enabled", {"a": 1}, severity="WARNING",
                                  tenant_id="t-1")
        )
        # fanout() is a HAND-OFF (it must never make the core audit path wait on a
        # plugin), so delivery is asserted after draining.
        _await_delivery()
        self.assertEqual(len(backend.received), 1)
        event_type, details, severity, tenant = backend.received[0]
        self.assertEqual(event_type, "plugin_enabled")
        self.assertEqual(details, {"a": 1})
        self.assertEqual(severity, "WARNING")
        self.assertEqual(tenant, "t-1")

    def test_raising_backend_is_swallowed(self):
        audit_provider.set_active(ExplodingAuditBackend())
        with self.assertLogs("corvin.audit.fanout", level="ERROR") as logs:
            audit_provider.fanout("x.y", {"a": 1})
            _await_delivery()
        # The connection string in the exception message must not reach the log.
        joined = "\n".join(logs.output)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("postgres://", joined)
        self.assertNotIn("pw@host", joined)

    def test_repeated_failures_go_quiet_but_are_counted(self):
        audit_provider.set_active(ExplodingAuditBackend())
        with self.assertLogs("corvin.audit.fanout", level="ERROR"):
            for _ in range(audit_provider._QUIET_AFTER):
                audit_provider.fanout("x.y", {})
                _await_delivery()
        self.assertGreaterEqual(audit_provider.failure_count(), 1)

    def test_recovery_resets_the_failure_count(self):
        audit_provider.set_active(ExplodingAuditBackend())
        with self.assertLogs("corvin.audit.fanout", level="ERROR"):
            audit_provider.fanout("x.y", {})
            _await_delivery()
        self.assertEqual(audit_provider.failure_count(), 1)
        audit_provider.set_active(RecordingAuditBackend())
        audit_provider.fanout("x.y", {})
        _await_delivery()
        self.assertEqual(audit_provider.failure_count(), 0)

    def test_backend_cannot_mutate_the_callers_dict(self):
        backend = MutatingAuditBackend()
        audit_provider.set_active(backend)
        body = {"channel": "discord", "user": "hashed"}
        audit_provider.fanout("bridge.login", body)
        _await_delivery()
        self.assertEqual(
            body,
            {"channel": "discord", "user": "hashed"},
            "the core writer still holds this dict; a plugin must not reach it",
        )

    def test_clear_detaches_the_backend(self):
        backend = RecordingAuditBackend()
        audit_provider.set_active(backend)
        audit_provider.clear()
        self.assertFalse(audit_provider.fanout("x.y", {}))
        audit_provider.drain_now()
        self.assertEqual(backend.received, [])

    def test_provider_exposes_no_trail_owning_api(self):
        """ADR-0233 D4: there must be no way for a plugin to become the trail."""
        # Read the SAME constant the boot tripwire reads, so the two cannot drift.
        self.assertTrue(audit_provider.TRAIL_OWNING_ATTRS)
        for forbidden in audit_provider.TRAIL_OWNING_ATTRS:
            self.assertFalse(
                hasattr(audit_provider, forbidden),
                f"audit provider must not expose {forbidden}",
            )

    def test_fanout_is_thread_safe(self):
        backend = RecordingAuditBackend()
        audit_provider.set_active(backend)

        def hammer():
            for i in range(50):
                audit_provider.fanout("x.y", {"i": i})

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _await_delivery(timeout=8.0)
        self.assertEqual(len(backend.received), 200)


class TestCoreAuditIsUnaffected(unittest.TestCase):
    """The core chain must be written and verifiable regardless of the backend."""

    def setUp(self):
        import tempfile

        _reset_breakers()
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "audit.jsonl"
        import os

        os.environ["VOICE_AUDIT_PATH"] = str(self._path)
        # Import after the env var is set: audit_path() reads it per call, but the
        # module-level forge import must succeed for the write to happen at all.
        import audit as _audit  # type: ignore[import-not-found]

        self._audit = _audit

    def tearDown(self):
        import os

        os.environ.pop("VOICE_AUDIT_PATH", None)
        audit_provider.clear()
        _reset_breakers()
        self._tmp.cleanup()

    def _core_available(self) -> bool:
        return self._audit._se is not None

    def test_core_writes_and_verifies_with_a_hostile_backend_installed(self):
        if not self._core_available():
            self.skipTest("forge.security_events not importable in this layout")
        audit_provider.set_active(ExplodingAuditBackend())
        self._audit.audit_event("bridge.login", channel="test", user="u", tenant_id="t")
        self.assertTrue(self._path.exists(), "core record must exist")
        contents = self._path.read_text()
        self.assertIn("bridge.login", contents)
        ok, problems = self._audit.verify_audit(self._path)
        self.assertTrue(ok, f"core chain must verify: {problems}")

    def test_core_record_is_identical_with_and_without_a_backend(self):
        if not self._core_available():
            self.skipTest("forge.security_events not importable in this layout")
        audit_provider.clear()
        self._audit.audit_event("bridge.login", channel="test", user="u")
        without = self._path.read_text().count("bridge.login")

        audit_provider.set_active(MutatingAuditBackend())
        self._audit.audit_event("bridge.login", channel="test", user="u")
        after = self._path.read_text()
        self.assertEqual(after.count("bridge.login"), without + 1)
        self.assertNotIn("injected", after, "a plugin must not alter the core record")
        ok, _ = self._audit.verify_audit(self._path)
        self.assertTrue(ok)

    def test_backend_sees_the_event_after_the_core_write(self):
        if not self._core_available():
            self.skipTest("forge.security_events not importable in this layout")
        backend = RecordingAuditBackend()
        audit_provider.set_active(backend)
        self._audit.audit_event("bridge.login", channel="test", user="u", tenant_id="t-9")
        _await_delivery()
        self.assertEqual(len(backend.received), 1)
        event_type, details, _severity, tenant = backend.received[0]
        self.assertEqual(event_type, "bridge.login")
        self.assertEqual(tenant, "t-9")
        self.assertEqual(details.get("channel"), "test")


# ── User backend: deny on anything but an explicit success ────────────────────


def _run(coro):
    return asyncio.run(coro)


class TestUserBackendDenyPaths(unittest.TestCase):
    def setUp(self):
        _reset_breakers()

    def tearDown(self):
        user_provider.clear()
        _reset_breakers()

    def test_no_backend_is_not_installed_and_denies(self):
        user_provider.clear()
        self.assertFalse(user_provider.is_installed())
        self.assertIsNone(_run(user_provider.authenticate({"username": "a",
                                                           "password": "correct"})))

    def test_successful_auth_returns_a_principal(self):
        user_provider.set_active(OkUserBackend())
        result = _run(user_provider.authenticate({"username": "a", "password": "correct"}))
        self.assertIsNotNone(result)
        self.assertEqual(result["user_id"], "u-1")
        self.assertEqual(result["roles"], ["operator"])

    def test_secrets_are_stripped_from_the_principal(self):
        user_provider.set_active(OkUserBackend())
        result = _run(user_provider.authenticate({"username": "a", "password": "correct"}))
        for leaked in ("password", "password_hash", "token", "secret", "client_secret"):
            self.assertNotIn(leaked, result)

    def test_wrong_password_denies(self):
        user_provider.set_active(OkUserBackend())
        self.assertIsNone(_run(user_provider.authenticate({"username": "a",
                                                           "password": "wrong"})))

    def test_raising_backend_denies_and_does_not_leak(self):
        user_provider.set_active(ExplodingUserBackend())
        with self.assertLogs("corvin.auth.backend", level="ERROR") as logs:
            self.assertIsNone(_run(user_provider.authenticate({"username": "jdoe",
                                                               "password": "x"})))
        joined = "\n".join(logs.output)
        self.assertIn("ConnectionError", joined)
        self.assertNotIn("jdoe", joined, "no username in log lines")
        self.assertNotIn("ldaps://", joined, "no directory URL in log lines")

    def test_hanging_backend_times_out_into_a_deny(self):
        user_provider.set_active(HangingUserBackend())
        with self.assertLogs("corvin.auth.backend", level="ERROR"):
            result = _run(
                user_provider.authenticate({"username": "a", "password": "correct"},
                                           timeout_s=0.05)
            )
        self.assertIsNone(result, "a timeout must deny, never admit")

    def test_malformed_principals_deny(self):
        for payload in (
            {},                                  # no user_id
            {"user_id": ""},                      # empty user_id
            {"roles": ["admin"]},                 # roles without identity
            "u-1",                                # not a dict
            ["u-1"],                              # not a dict
            True,                                 # not a dict
        ):
            user_provider.set_active(GarbageUserBackend(payload))
            self.assertIsNone(
                _run(user_provider.authenticate({"username": "a", "password": "correct"})),
                f"payload {payload!r} must not authenticate",
            )

    def test_none_from_get_user_on_error(self):
        user_provider.set_active(ExplodingUserBackend())
        with self.assertLogs("corvin.auth.backend", level="ERROR"):
            self.assertIsNone(_run(user_provider.get_user("u-1")))

    def test_quota_timeout_is_fail_closed(self):
        user_provider.set_active(HangingUserBackend())
        with self.assertLogs("corvin.auth.backend", level="ERROR"):
            with self.assertRaises(user_provider.QuotaUndeterminedError):
                _run(user_provider.enforce_quota("u-1", "tokens", timeout_s=0.05))

    def test_quota_without_backend_is_a_no_op(self):
        user_provider.clear()
        self.assertIsNone(_run(user_provider.enforce_quota("u-1", "tokens")))

    def test_backend_denial_raises_through(self):
        class DenyingBackend(OkUserBackend):
            async def enforce_quota(self, user_id, resource):
                raise RuntimeError("over budget")

        user_provider.set_active(DenyingBackend())
        with self.assertRaises(RuntimeError):
            _run(user_provider.enforce_quota("u-1", "tokens"))

    def test_clear_detaches_and_reverts_to_core_auth(self):
        user_provider.set_active(OkUserBackend())
        self.assertTrue(user_provider.is_installed())
        user_provider.clear()
        self.assertFalse(user_provider.is_installed())
        self.assertIsNone(_run(user_provider.authenticate({"username": "a",
                                                           "password": "correct"})))


# ── Tripwire ──────────────────────────────────────────────────────────────────


class TestTripwire(unittest.TestCase):
    def setUp(self):
        from corvin_compliance_reports import tripwire

        self.tripwire = tripwire

    def test_check_all_returns_one_result_per_tripwire(self):
        results = self.tripwire.check_all()
        self.assertEqual(len(results), len(self.tripwire.TRIPWIRES))
        # The audit-specific three; the set grew when ADR-0232's other four
        # mandatory mechanisms got their own gates (see
        # TestMandatoryMechanismTripwires for the completeness assertion).
        self.assertLessEqual(
            {
                "audit_writer_reachable",
                "audit_chain_intact",
                "core_audit_owns_the_trail",
            },
            {r.name for r in results},
        )

    def test_core_owns_the_trail_passes_today(self):
        self.assertTrue(self.tripwire.core_audit_owns_the_trail().ok)

    def test_fresh_install_with_no_chain_passes(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["VOICE_AUDIT_PATH"] = str(Path(tmp) / "audit.jsonl")
            try:
                self.assertTrue(self.tripwire.audit_writer_reachable().ok)
                result = self.tripwire.audit_chain_intact()
                self.assertTrue(result.ok)
                self.assertIn("fresh install", result.detail)
            finally:
                os.environ.pop("VOICE_AUDIT_PATH", None)

    def test_unwritable_audit_dir_fails_the_tripwire(self):
        import os
        import stat
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked"
            locked.mkdir()
            locked.chmod(stat.S_IREAD | stat.S_IEXEC)  # r-x: no writes
            os.environ["VOICE_AUDIT_PATH"] = str(locked / "audit.jsonl")
            try:
                if os.access(locked, os.W_OK):
                    self.skipTest("running as root — directory mode is not enforced")
                result = self.tripwire.audit_writer_reachable()
                self.assertFalse(result.ok)
                with self.assertRaises(self.tripwire.TripwireError):
                    self.tripwire.assert_all()
            finally:
                os.environ.pop("VOICE_AUDIT_PATH", None)
                locked.chmod(stat.S_IRWXU)

    def test_broken_chain_fails_the_tripwire(self):
        """Tamper with a REAL chained record — a hand-written line is skipped.

        ``verify_chain`` treats lines without a ``hash`` field as legitimate
        pre-chain entries, so a fabricated record proves nothing.  This writes two
        genuine events and then edits the first one's payload without recomputing
        its hash, which is exactly the tampering the chain exists to detect.
        """
        import json
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            os.environ["VOICE_AUDIT_PATH"] = str(path)
            try:
                import audit as _audit  # type: ignore[import-not-found]

                if _audit._se is None:
                    self.skipTest("forge.security_events not importable in this layout")

                _audit.audit_event("bridge.login", channel="test", user="u1")
                _audit.audit_event("bridge.login", channel="test", user="u2")
                self.assertTrue(self.tripwire.audit_chain_intact().ok,
                                "a freshly written chain must verify")

                lines = path.read_text().splitlines()
                self.assertGreaterEqual(len(lines), 2)
                record = json.loads(lines[0])
                self.assertIn("hash", record, "core writes must be chained")
                record["details"]["user"] = "tampered-by-attacker"
                lines[0] = json.dumps(record)
                path.write_text("\n".join(lines) + "\n")

                result = self.tripwire.audit_chain_intact()
                self.assertFalse(result.ok, "a tampered chain must fail the tripwire")
                with self.assertRaises(self.tripwire.TripwireError):
                    self.tripwire.assert_all()
            finally:
                os.environ.pop("VOICE_AUDIT_PATH", None)

    def test_there_is_no_override_switch(self):
        """CLAUDE.md: no compliance-off mode via any env var."""
        source = Path(self.tripwire.__file__).read_text()
        for forbidden in (
            "CORVIN_TRIPWIRE_DEV_OVERRIDE",
            "TRIPWIRE_DISABLE",
            "SKIP_TRIPWIRE",
            "COMPLIANCE_OFF",
        ):
            self.assertNotIn(
                forbidden, source, f"{forbidden} would be a compliance kill-switch"
            )
        # And no environment read at all in the failure path.
        self.assertNotIn("os.environ", source)


if __name__ == "__main__":
    unittest.main()


# ── Adversarial-review regressions (ADR-0233 review round) ────────────────────


class _HostileIdBackend:
    """A backend whose plugin_id ACCESS raises — the leak path F3 found."""

    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "Hostile Id"

    @property
    def plugin_id(self):
        raise RuntimeError("plugin_id property explodes: postgres://u:pw@host/db")

    def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
        pass

    def verify_chain(self):
        return HealthStatus(ok=True)

    def enforce_retention(self, max_age_days, *, tenant_id="_default"):
        return {"deleted": 0}


class TestFanoutNeverLeaks(unittest.TestCase):
    """F3: 'fanout never raises into the caller' was only true inside one try.

    The registry guarded ``backend.fanout(...)`` but not the breaker lookup or the
    plugin_id read around it. A leak reached audit.py's handler, which logs
    "audit_event(...): dropped on non-IO error" — a false compliance alarm, since
    the core record had already committed.
    """

    def setUp(self):
        _reset_breakers()

    def tearDown(self):
        audit_provider.clear()
        _reset_breakers()

    def test_a_raising_plugin_id_property_is_contained(self):
        audit_provider.set_active(_HostileIdBackend())
        with self.assertLogs("corvin.audit.fanout", level="ERROR") as logs:
            audit_provider.fanout("bridge.login", {"a": 1})
            _await_delivery()
        joined = "\n".join(logs.output)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("postgres://", joined, "no credentials in the log line")
        self.assertNotIn("pw@host", joined)

    def test_the_callers_dict_is_untouched_after_a_leak(self):
        audit_provider.set_active(_HostileIdBackend())
        body = {"channel": "discord", "user": "hashed"}
        with self.assertLogs("corvin.audit.fanout", level="ERROR"):
            audit_provider.fanout("bridge.login", body)
            _await_delivery()
        self.assertEqual(body, {"channel": "discord", "user": "hashed"})

    def test_core_write_is_not_reported_as_dropped_when_the_sink_leaks(self):
        """The false-alarm case: core committed, sink leaked, log must not lie."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            os.environ["VOICE_AUDIT_PATH"] = str(path)
            try:
                import audit as _audit  # type: ignore[import-not-found]

                if _audit._se is None:
                    self.skipTest("forge.security_events not importable in this layout")
                audit_provider.set_active(_HostileIdBackend())
                with self.assertLogs("corvin.audit.fanout", level="ERROR"):
                    _audit.audit_event("bridge.login", channel="test", user="u")
                    _await_delivery()
                self.assertIn("bridge.login", path.read_text())
                ok, problems = _audit.verify_audit(path)
                self.assertTrue(ok, f"core chain must still verify: {problems}")
            finally:
                os.environ.pop("VOICE_AUDIT_PATH", None)

    def test_fanout_runs_outside_the_core_write_try_block(self):
        """Structural pin: the sink call must not sit inside the core write's try."""
        import audit as _audit  # type: ignore[import-not-found]

        source = Path(_audit.__file__).read_text()
        marker = "core_write_committed = True"
        self.assertIn(marker, source, "the commit flag is the ordering guard")
        commit_pos = source.index(marker)
        sink_pos = source.index("_audit_sink.fanout(")
        self.assertGreater(
            sink_pos, commit_pos,
            "fan-out must come AFTER the core write block, not inside it",
        )
        # And it must be gated on the flag, so a failed core write skips the sink.
        gate = source.index("if core_write_committed and _audit_sink is not None:")
        self.assertLess(gate, sink_pos)


class TestTripwireHasNoSecondCopyOfTheList(unittest.TestCase):
    """Second-round finding: the F5 fix left an inline fallback copy of the list.

    A `getattr(..., default=(...))` fallback is a second source of truth — the very
    drift F5 removed. A missing constant now fails the tripwire instead.
    """

    def setUp(self):
        from corvin_compliance_reports import tripwire

        self.tripwire = tripwire

    def test_source_holds_no_duplicate_name_list(self):
        source = Path(self.tripwire.__file__).read_text()
        # The names may appear at most once each — inside the failure message or a
        # comment, never as a second executable tuple.
        for name in ("set_writer", "replace_writer", "disable_core"):
            self.assertLessEqual(
                source.count(f'"{name}"'), 1, f"{name} appears twice — duplicate list?"
            )

    def test_missing_constant_fails_the_tripwire(self):
        from corvin_plugins.providers import audit_backend as provider

        original = provider.TRAIL_OWNING_ATTRS
        try:
            provider.TRAIL_OWNING_ATTRS = ()
            result = self.tripwire.core_audit_owns_the_trail()
            self.assertFalse(result.ok)
            self.assertIn("TRAIL_OWNING_ATTRS", result.detail)
        finally:
            provider.TRAIL_OWNING_ATTRS = original

    def test_a_trail_owning_attribute_fails_the_tripwire(self):
        from corvin_plugins.providers import audit_backend as provider

        provider.write_event = lambda *a, **k: None  # type: ignore[attr-defined]
        try:
            result = self.tripwire.core_audit_owns_the_trail()
            self.assertFalse(result.ok, "write_event must be caught by the BOOT gate")
            self.assertIn("write_event", result.detail)
        finally:
            del provider.write_event  # type: ignore[attr-defined]


# ── ADR-0232: one tripwire per mandatory mechanism ────────────────────────────


class TestMandatoryMechanismTripwires(unittest.TestCase):
    """ADR-0232 lists five mandatory mechanisms, each "hardcoded, tripwired".

    Only L16 was covered; L18/L34/L44/L36 had no boot gate at all. Each case here
    proves the tripwire FIRES when its mechanism is broken — a tripwire that only
    ever returns ok proves nothing.
    """

    def setUp(self):
        from corvin_compliance_reports import tripwire

        self.tripwire = tripwire
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["VOICE_AUDIT_PATH"] = str(Path(self._tmp.name) / "audit.jsonl")

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        self._tmp.cleanup()

    def test_all_five_mechanisms_have_a_tripwire(self):
        names = {t.__name__ for t in self.tripwire.TRIPWIRES}
        for expected in (
            "audit_writer_reachable",          # L16
            "consent_gate_denies_by_default",  # L18
            "flow_guard_present",              # L34
            "house_rules_gate_intact",         # L44
            "erasure_orchestrator_present",    # L36
        ):
            self.assertIn(expected, names, f"{expected} is not in the boot set")

    def test_everything_passes_on_a_healthy_install(self):
        results = self.tripwire.check_all()
        failed = [f"{r.name}: {r.detail}" for r in results if not r.ok]
        self.assertEqual(failed, [], "a healthy install must boot")

    # ── each tripwire must actually fire ─────────────────────────────────────

    def test_consent_tripwire_fires_when_the_gate_admits(self):
        consent = self.tripwire._shared_module("consent")
        original = consent.is_granted
        try:
            consent.is_granted = lambda *a, **k: (True, "auto-admit")
            result = self.tripwire.consent_gate_denies_by_default()
            self.assertFalse(result.ok)
            self.assertIn("ADMITS", result.detail)
        finally:
            consent.is_granted = original

    def test_consent_tripwire_fires_on_a_wrong_return_shape(self):
        """A bare bool would make the tuple check silently pass the wrong branch."""
        consent = self.tripwire._shared_module("consent")
        original = consent.is_granted
        try:
            consent.is_granted = lambda *a, **k: False  # not a 2-tuple
            result = self.tripwire.consent_gate_denies_by_default()
            self.assertFalse(result.ok)
            self.assertIn("2-tuple", result.detail)
        finally:
            consent.is_granted = original

    def test_flow_guard_tripwire_fires_when_the_deny_path_is_gone(self):
        dc = self.tripwire._shared_module("data_classification")
        original = dc.DataFlowDenied
        try:
            del dc.DataFlowDenied
            result = self.tripwire.flow_guard_present()
            self.assertFalse(result.ok)
            self.assertIn("DataFlowDenied", result.detail)
        finally:
            dc.DataFlowDenied = original

    def test_house_rules_tripwire_fires_on_a_tampered_policy(self):
        hr = self.tripwire._shared_module("house_rules")
        original = hr.verify_policy_integrity
        try:
            hr.verify_policy_integrity = lambda *a, **k: (False, "hash mismatch")
            result = self.tripwire.house_rules_gate_intact()
            self.assertFalse(result.ok)
            self.assertIn("integrity", result.detail)
        finally:
            hr.verify_policy_integrity = original

    def test_erasure_tripwire_fires_when_the_validator_accepts_anything(self):
        eo = self.tripwire._shared_module("erasure_orchestrator")
        original = eo.validate_subject_id
        try:
            eo.validate_subject_id = lambda subject_id: subject_id
            result = self.tripwire.erasure_orchestrator_present()
            self.assertFalse(result.ok)
            self.assertIn("empty subject", result.detail)
        finally:
            eo.validate_subject_id = original

    def test_assert_all_aborts_the_boot_on_any_failure(self):
        hr = self.tripwire._shared_module("house_rules")
        original = hr.verify_policy_integrity
        try:
            hr.verify_policy_integrity = lambda *a, **k: (False, "tampered")
            with self.assertRaises(self.tripwire.TripwireError) as ctx:
                self.tripwire.assert_all()
            self.assertIn("house_rules", str(ctx.exception))
        finally:
            hr.verify_policy_integrity = original

    def test_a_raising_probe_counts_as_a_failure_not_a_pass(self):
        consent = self.tripwire._shared_module("consent")
        original = consent.is_granted
        try:
            def boom(*a, **k):
                raise RuntimeError("gate exploded")

            consent.is_granted = boom
            result = self.tripwire.consent_gate_denies_by_default()
            self.assertFalse(result.ok)
            self.assertIn("RuntimeError", result.detail)
        finally:
            consent.is_granted = original


class TestFanoutIsAHandOff(unittest.TestCase):
    """Review finding: a slow sink blocked the CORE audit path.

    Measured before: a backend with a 400 ms fanout() added 2.07 s to five
    audit_event() calls — i.e. it slowed every bridge turn, login and tool use.
    The core must hand the copy off and keep going.
    """

    def setUp(self):
        _reset_breakers()
        audit_provider.clear()

    def tearDown(self):
        audit_provider.clear()
        _reset_breakers()

    def test_a_slow_sink_does_not_slow_the_caller(self):
        class _Slow:
            plugin_id = "test.slow-sink"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "Slow"

            def __init__(self):
                self.calls = 0

            def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
                time.sleep(0.3)
                self.calls += 1

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, max_age_days, *, tenant_id="_default"):
                return {"deleted": 0}

        sink = _Slow()
        audit_provider.set_active(sink)
        started = time.monotonic()
        for i in range(5):
            audit_provider.fanout("bridge.login", {"i": i})
        elapsed = time.monotonic() - started
        self.assertLess(
            elapsed, 0.5, f"the caller waited {elapsed:.2f}s on a slow plugin"
        )
        _await_delivery(timeout=6.0)
        self.assertGreaterEqual(sink.calls, 1, "delivery still happens, just later")

    def test_a_full_queue_drops_the_oldest_and_never_blocks(self):
        blocked = threading.Event()

        class _Stuck:
            plugin_id = "test.stuck-sink"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "Stuck"

            def fanout(self, *a, **k):
                blocked.wait(timeout=5.0)

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, max_age_days, *, tenant_id="_default"):
                return {"deleted": 0}

        audit_provider.set_active(_Stuck())
        try:
            started = time.monotonic()
            with self.assertLogs("corvin.audit.fanout", level="ERROR"):
                for i in range(audit_provider.MAX_QUEUED_EVENTS + 50):
                    audit_provider.fanout("x.y", {"i": i})
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 5.0, "enqueueing must not block on a stuck sink")
            self.assertGreater(audit_provider.dropped_count(), 0)
            self.assertLessEqual(
                audit_provider.queue_depth(),
                audit_provider.MAX_QUEUED_EVENTS,
                "the queue must stay bounded",
            )
        finally:
            blocked.set()

    def test_a_backend_that_kills_the_worker_would_silence_monitoring(self):
        """The guard that matters: _deliver must never raise into the drain loop."""
        audit_provider.set_active(_HostileIdBackend())
        with self.assertLogs("corvin.audit.fanout", level="ERROR"):
            audit_provider.fanout("x.y", {"a": 1})
            _await_delivery()
        # A healthy backend installed afterwards must still receive events, which
        # only holds if the worker survived the hostile one.
        good = RecordingAuditBackend()
        audit_provider.set_active(good)
        audit_provider.fanout("x.y", {"b": 2})
        _await_delivery()
        self.assertEqual(len(good.received), 1, "the drain thread must have survived")

    def test_a_slow_sink_eventually_trips_its_breaker(self):
        """A sink that never raises but takes seconds is still broken."""
        class _VerySlow:
            plugin_id = "test.very-slow"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "Very Slow"

            def fanout(self, *a, **k):
                time.sleep(audit_provider.SLOW_SINK_S + 0.3)

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, max_age_days, *, tenant_id="_default"):
                return {"deleted": 0}

        audit_provider.set_active(_VerySlow())
        with self.assertLogs("corvin.audit.fanout", level="WARNING"):
            audit_provider.fanout("x.y", {})
            _await_delivery(timeout=8.0)
        stats = _breakers.snapshot().get("test.very-slow") or {}
        self.assertGreaterEqual(
            stats.get("total_failures", 0), 1, "slowness must reach the breaker"
        )

    def test_clear_discards_queued_copies_for_the_old_backend(self):
        backend = RecordingAuditBackend()
        audit_provider.set_active(backend)
        audit_provider.fanout("x.y", {})
        audit_provider.clear()
        self.assertEqual(audit_provider.queue_depth(), 0)


class TestHistoricalVsCurrentChainBreakage(unittest.TestCase):
    """Review finding: the boot gate bricked the maintainer's own install.

    audit_chain_intact was a full-file verify wired fail-closed into the gateway
    lifespan. On the live machine the chain carried a KNOWN historical key-mismatch
    window (380 records, ~77 000 records before the tail), so the platform refused to
    boot outright — a compliance hardening that STOPS the audit trail it exists to
    protect. The chain is append-only, so that state never repairs itself, and the
    only way out for an operator under pressure is deleting or truncating the audit
    log: destroying evidence, which is strictly worse under GDPR Art. 30 than a
    documented seam.

    The split: "is the writer sound NOW" blocks boot; "has this file ever been
    broken" is recorded into the chain on every boot and reported, but never fatal.
    Neither has an override switch.
    """

    def setUp(self):
        from corvin_compliance_reports import tripwire

        self.tripwire = tripwire
        self.tripwire._verify_cache.clear()

    def tearDown(self):
        import os

        os.environ.pop("VOICE_AUDIT_PATH", None)
        self.tripwire._verify_cache.clear()

    def _chain(self, tmp, count):
        import os

        path = Path(tmp) / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(path)
        import audit as _audit  # type: ignore[import-not-found]

        if _audit._se is None:
            self.skipTest("forge.security_events not importable in this layout")
        for i in range(count):
            _audit.audit_event("bridge.login", channel="test", user=f"u{i}")
        return path, _audit

    def _tamper(self, path, index):
        import json

        lines = path.read_text().splitlines()
        record = json.loads(lines[index])
        self.assertIn("hash", record, "core writes must be chained")
        record["details"]["user"] = "tampered-by-attacker"
        lines[index] = json.dumps(record)
        path.write_text("\n".join(lines) + "\n")
        self.tripwire._verify_cache.clear()

    def test_an_old_break_does_not_stop_the_platform_but_is_recorded(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path, _audit = self._chain(tmp, self.tripwire.TAIL_RECORDS + 60)
            self._tamper(path, 2)  # far outside the tail

            self.assertTrue(
                self.tripwire.audit_chain_intact().ok,
                "a break 200+ records back must not block boot",
            )
            history = self.tripwire.audit_chain_history_clean()
            self.assertFalse(history.ok, "the break must still be reported")
            self.assertIn("permanent", history.detail)

            before = len(path.read_text().splitlines())
            self.tripwire.assert_all()  # must NOT raise
            lines = path.read_text().splitlines()
            self.assertGreater(len(lines), before, "the finding must be appended")
            recorded = json.loads(lines[-1])
            self.assertEqual(recorded["event_type"], "compliance.chain_discontinuity")

    def test_a_break_in_the_tail_still_refuses_to_boot(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path, _audit = self._chain(tmp, 20)
            self._tamper(path, 5)  # inside the tail: the writer is not sound

            result = self.tripwire.audit_chain_intact()
            self.assertFalse(result.ok, "current breakage must block the boot")
            self.assertIn("not sound", result.detail)
            with self.assertRaises(self.tripwire.TripwireError):
                self.tripwire.assert_all()

    def test_the_reporting_tripwire_cannot_be_the_only_signal(self):
        """It must appear in check_all(), or the Console never shows it."""
        names = {r.name for r in self.tripwire.check_all()}
        self.assertIn("audit_chain_history_clean", names)
        self.assertIn("audit_chain_history_clean", self.tripwire.REPORTING_ONLY)

    def test_reporting_only_never_grows_to_cover_a_blocking_mechanism(self):
        """A future edit must not quietly move a real gate onto the soft list."""
        self.assertEqual(
            self.tripwire.REPORTING_ONLY, frozenset({"audit_chain_history_clean"}),
            "moving a mandatory mechanism to reporting-only is a compliance change "
            "that needs an ADR, not a set edit",
        )

    def test_a_clean_chain_passes_both(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self._chain(tmp, 5)
            self.assertTrue(self.tripwire.audit_chain_intact().ok)
            self.assertTrue(self.tripwire.audit_chain_history_clean().ok)


class TestShutdownDrainIsComplete(unittest.TestCase):
    """Review finding: an empty queue is not an empty pipeline.

    drain_now() is what the gateway shutdown calls to flush the fan-out. It stopped
    at the first empty get_nowait() — but the worker thread races it, and whichever
    wins the get() performs the delivery. Measured 9 of 10 copies delivered: the one
    the worker was holding when drain_now() returned was lost at process exit.
    """

    def setUp(self):
        _reset_breakers()
        audit_provider.clear()

    def tearDown(self):
        audit_provider.clear()
        _reset_breakers()

    def test_the_copy_in_flight_is_not_lost(self):
        class _Slow:
            plugin_id = "test.drain-sink"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "Drain"

            def __init__(self):
                self.received = []

            def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
                time.sleep(0.05)
                self.received.append(event_type)

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, max_age_days, *, tenant_id="_default"):
                return {"deleted": 0}

        sink = _Slow()
        audit_provider.set_active(sink)
        for i in range(10):
            audit_provider.fanout("bridge.login", {"i": i})
        audit_provider.drain_now(timeout=10.0)
        self.assertEqual(
            len(sink.received), 10,
            f"{len(sink.received)}/10 delivered — a copy was in flight at return",
        )
        self.assertEqual(audit_provider.queue_depth(), 0)

    def test_the_drain_gives_up_rather_than_hanging_shutdown(self):
        release = threading.Event()

        class _Wedged:
            plugin_id = "test.wedged-sink"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "Wedged"

            def fanout(self, *a, **k):
                release.wait(timeout=30.0)

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, max_age_days, *, tenant_id="_default"):
                return {"deleted": 0}

        audit_provider.set_active(_Wedged())
        try:
            audit_provider.fanout("x.y", {})
            audit_provider.fanout("x.y", {})
            started = time.monotonic()
            with self.assertLogs("corvin.audit.fanout", level="WARNING"):
                audit_provider.drain_now(timeout=0.5)
            elapsed = time.monotonic() - started
            self.assertLess(
                elapsed, 5.0,
                "a wedged sink must not hold the shutdown open (the outbox-poller "
                "lesson: a hanging sendFn stalled delivery for 38 minutes)",
            )
        finally:
            release.set()

    def test_clear_leaves_no_phantom_in_flight_work(self):
        """clear() discards copies; a missed task_done() would hang the next drain."""
        backend = RecordingAuditBackend()
        audit_provider.set_active(backend)
        for _ in range(5):
            audit_provider.fanout("x.y", {})
        audit_provider.clear()
        audit_provider.set_active(RecordingAuditBackend())
        started = time.monotonic()
        audit_provider.drain_now(timeout=2.0)
        self.assertLess(time.monotonic() - started, 1.5, "drain hung on phantom work")
