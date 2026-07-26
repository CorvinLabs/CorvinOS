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
import sys
import threading
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
        self.assertEqual(len(backend.received), 1)
        event_type, details, severity, tenant = backend.received[0]
        self.assertEqual(event_type, "plugin_enabled")
        self.assertEqual(details, {"a": 1})
        self.assertEqual(severity, "WARNING")
        self.assertEqual(tenant, "t-1")

    def test_raising_backend_is_swallowed(self):
        audit_provider.set_active(ExplodingAuditBackend())
        with self.assertLogs("corvin.audit.fanout", level="ERROR") as logs:
            self.assertFalse(audit_provider.fanout("x.y", {"a": 1}))
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
        self.assertEqual(audit_provider.failure_count(), audit_provider._QUIET_AFTER)
        # Beyond the threshold nothing is logged, but the count keeps rising.
        audit_provider.fanout("x.y", {})
        self.assertEqual(audit_provider.failure_count(), audit_provider._QUIET_AFTER + 1)

    def test_recovery_resets_the_failure_count(self):
        audit_provider.set_active(ExplodingAuditBackend())
        with self.assertLogs("corvin.audit.fanout", level="ERROR"):
            audit_provider.fanout("x.y", {})
        self.assertEqual(audit_provider.failure_count(), 1)
        audit_provider.set_active(RecordingAuditBackend())
        self.assertTrue(audit_provider.fanout("x.y", {}))
        self.assertEqual(audit_provider.failure_count(), 0)

    def test_backend_cannot_mutate_the_callers_dict(self):
        backend = MutatingAuditBackend()
        audit_provider.set_active(backend)
        body = {"channel": "discord", "user": "hashed"}
        audit_provider.fanout("bridge.login", body)
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
        self.assertEqual(
            {r.name for r in results},
            {
                "audit_writer_reachable",
                "audit_chain_intact",
                "core_audit_owns_the_trail",
            },
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
            result = audit_provider.fanout("bridge.login", {"a": 1})
        self.assertFalse(result)
        joined = "\n".join(logs.output)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("postgres://", joined, "no credentials in the log line")
        self.assertNotIn("pw@host", joined)

    def test_the_callers_dict_is_untouched_after_a_leak(self):
        audit_provider.set_active(_HostileIdBackend())
        body = {"channel": "discord", "user": "hashed"}
        with self.assertLogs("corvin.audit.fanout", level="ERROR"):
            audit_provider.fanout("bridge.login", body)
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
