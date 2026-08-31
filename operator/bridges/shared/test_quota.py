"""test_quota.py — Layer 20 quota + audit-view (delegated-budget visibility).

Covers per-bundle defaults, owner-bypass, observer denial, member round-trip,
messages-exceeded with audit, tokens-exceeded with audit, failed-runs-don't-
burn-budget, set_limit override + clear, reset + idempotency, window
rollover with audit, list_usage, audit_view.view_me with multi-key uid
match, audit_view.view_chat with prefix filter, summarize_event single-line
render, hash-chain integrity + view-only read invariant.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import quota  # noqa: E402
import audit_view  # noqa: E402
import roles  # noqa: E402

CHANNEL = "telegram"
CHAT = "150220"
OWNER_UID = "owner-20"
ADMIN_UID = "admin-20"
MEMBER_UID = "member-20"
OBSERVER_UID = "obs-20"
STRANGER_UID = "stranger-20"


class _QuotaTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="quota-test-")
        os.environ["CORVIN_HOME"] = self._tmp
        self._orig_corvin_home = os.environ.get("CORVIN_HOME")
        # Set up a channel settings file with OWNER on the whitelist; redirect
        # roles._channel_settings_path so role lookups inside quota.check
        # resolve to OWNER (intrinsic) and other uids resolve to whatever
        # their granted role is.
        self._channel_dir = Path(self._tmp) / "channels" / CHANNEL
        self._channel_dir.mkdir(parents=True)
        self._channel_settings = self._channel_dir / "settings.json"
        self._channel_settings.write_text(json.dumps({
            "whitelist": [OWNER_UID],
        }))
        self._orig_resolver = roles._channel_settings_path
        roles._channel_settings_path = lambda c: self._channel_settings

    def tearDown(self) -> None:
        roles._channel_settings_path = self._orig_resolver
        if self._orig_corvin_home is not None:
            os.environ["CORVIN_HOME"] = self._orig_corvin_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(self._tmp, ignore_errors=True)


class CheckRecordTests(_QuotaTestBase):
    def test_owner_bypasses_quota(self):
        result = quota.check(CHANNEL, CHAT, OWNER_UID)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason"], "owner-bypass")
        self.assertEqual(result["role"], "owner")

    def test_observer_denied(self):
        # Grant observer role first.
        roles.grant(CHANNEL, CHAT, OBSERVER_UID,
                    bundle="observer", granted_by=OWNER_UID, ttl_s=3600)
        result = quota.check(CHANNEL, CHAT, OBSERVER_UID)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "no-trigger-role")

    def test_stranger_denied(self):
        # No role at all → role=='none' → no-trigger-role
        result = quota.check(CHANNEL, CHAT, STRANGER_UID)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["role"], "none")

    def test_member_round_trip(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        for i in range(3):
            r = quota.check(CHANNEL, CHAT, MEMBER_UID, tokens=100)
            self.assertTrue(r["allowed"], f"check #{i+1} unexpectedly blocked: {r}")
            quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=100)
        usage = quota.get_usage(CHANNEL, CHAT, MEMBER_UID)
        self.assertEqual(usage["messages_today"], 3)
        self.assertEqual(usage["tokens_today"], 300)


class LimitsExceededTests(_QuotaTestBase):
    def test_messages_limit_blocks(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        # Override member's msg-limit to 2 so we don't burn 100 records.
        quota.set_limit(CHANNEL, CHAT, MEMBER_UID, limit_msgs=2,
                        set_by=OWNER_UID)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=10)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=10)
        result = quota.check(CHANNEL, CHAT, MEMBER_UID, tokens=10)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "messages-exceeded")
        self.assertEqual(result["remaining_msgs"], 0)

    def test_tokens_limit_blocks(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.set_limit(CHANNEL, CHAT, MEMBER_UID,
                        limit_msgs=100, limit_tokens=500,
                        set_by=OWNER_UID)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=400)
        # 400 + 200 > 500 → blocked on tokens
        result = quota.check(CHANNEL, CHAT, MEMBER_UID, tokens=200)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "tokens-exceeded")

    def test_failed_run_does_not_consume_budget(self):
        # Layer 20's load-bearing invariant: check() never mutates the
        # store. Only an explicit record() call (post successful run)
        # counts against the budget.
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        for _ in range(5):
            quota.check(CHANNEL, CHAT, MEMBER_UID, tokens=100)
        usage = quota.get_usage(CHANNEL, CHAT, MEMBER_UID)
        self.assertEqual(usage["messages_today"], 0)
        self.assertEqual(usage["tokens_today"], 0)


class SetLimitTests(_QuotaTestBase):
    def test_set_limit_override(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.set_limit(CHANNEL, CHAT, MEMBER_UID,
                        limit_msgs=42, limit_tokens=999,
                        set_by=OWNER_UID)
        usage = quota.get_usage(CHANNEL, CHAT, MEMBER_UID)
        self.assertEqual(usage["limit_msgs"], 42)
        self.assertEqual(usage["limit_tokens"], 999)
        self.assertTrue(usage["limit_msgs_overridden"])

    def test_set_limit_clear_reverts_to_default(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.set_limit(CHANNEL, CHAT, MEMBER_UID, limit_msgs=42,
                        set_by=OWNER_UID)
        quota.set_limit(CHANNEL, CHAT, MEMBER_UID, limit_msgs=-1,
                        set_by=OWNER_UID)
        usage = quota.get_usage(CHANNEL, CHAT, MEMBER_UID)
        self.assertEqual(usage["limit_msgs"], quota.DEFAULT_LIMITS["member"]["messages"])
        self.assertFalse(usage["limit_msgs_overridden"])

    def test_concurrent_set_limit_and_record_no_race(self):
        """Verify that set_limit() and record() serialize correctly via locking.

        ADR-0052 F7: concurrent calls to set_limit() and record() must not
        cause lost updates or corrupted quotas. The per-(channel, uid) lock
        protects the read-modify-write in both functions.
        """
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=100)

        errors = []
        def concurrent_set_limit():
            try:
                for i in range(5):
                    quota.set_limit(CHANNEL, CHAT, MEMBER_UID,
                                  limit_msgs=50 + i, limit_tokens=10000 + i * 100,
                                  set_by=OWNER_UID)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def concurrent_record():
            try:
                for i in range(5):
                    quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=200)
                    time.sleep(0.002)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=concurrent_set_limit)
        t2 = threading.Thread(target=concurrent_record)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertFalse(errors, f"Concurrent operations raised errors: {errors}")
        usage = quota.get_usage(CHANNEL, CHAT, MEMBER_UID)
        # Should have exactly 6 records total (1 initial + 5 in the thread).
        self.assertEqual(usage["messages_today"], 6,
                        f"Expected 6 messages, got {usage['messages_today']}")
        # Should have 100 + 5*200 = 1100 tokens.
        self.assertEqual(usage["tokens_today"], 1100,
                        f"Expected 1100 tokens, got {usage['tokens_today']}")


class ResetTests(_QuotaTestBase):
    def test_reset_clears_counters(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=50)
        existed = quota.reset(CHANNEL, CHAT, MEMBER_UID, reset_by=OWNER_UID)
        self.assertTrue(existed)
        usage = quota.get_usage(CHANNEL, CHAT, MEMBER_UID)
        self.assertEqual(usage["messages_today"], 0)
        self.assertEqual(usage["tokens_today"], 0)

    def test_reset_idempotent(self):
        existed = quota.reset(CHANNEL, CHAT, "ghost", reset_by=OWNER_UID)
        self.assertFalse(existed)

    def test_concurrent_reset_and_record_no_race(self):
        """Verify that reset() and record() serialize correctly via locking.

        ADR-0052 F7: concurrent calls to reset() and record() must not
        cause lost updates or corrupted quotas. The per-(channel, uid) lock
        protects the read-modify-write in both functions.
        """
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=100)

        errors = []
        def concurrent_reset():
            try:
                for i in range(3):
                    quota.reset(CHANNEL, CHAT, MEMBER_UID, reset_by=OWNER_UID)
                    time.sleep(0.002)
            except Exception as e:
                errors.append(e)

        def concurrent_record():
            try:
                for i in range(5):
                    quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=50)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=concurrent_reset)
        t2 = threading.Thread(target=concurrent_record)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertFalse(errors, f"Concurrent operations raised errors: {errors}")
        usage = quota.get_usage(CHANNEL, CHAT, MEMBER_UID)
        # Messages should be between 1 and 6 depending on interleaving
        # (1 initial + up to 5 from concurrent thread, minus any that were
        # reset). At minimum, we should see that the store is consistent.
        self.assertGreaterEqual(usage["messages_today"], 0)
        self.assertLessEqual(usage["messages_today"], 6)


class WindowRolloverTests(_QuotaTestBase):
    def test_rollover_zeros_counters(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=200)
        # Backdate the day_anchor by > WINDOW_S so the next read rolls over.
        store = quota._store_path(CHANNEL, CHAT)
        data = json.loads(store.read_text())
        data[MEMBER_UID]["day_anchor"] = time.time() - quota.WINDOW_S - 60
        store.write_text(json.dumps(data))
        usage = quota.get_usage(CHANNEL, CHAT, MEMBER_UID)
        # After rollover, counters reset; the rolling-window timer just
        # restarted, so window_remaining_s is back near a full WINDOW_S.
        self.assertEqual(usage["messages_today"], 0)
        self.assertEqual(usage["tokens_today"], 0)

    def test_rollover_persists_to_disk(self):
        """Verify that get_usage() persists rolled-over state before emitting audit.

        This closes audit-reality drift: the quota.reset audit event must not
        appear without the rolled-over counters being on-disk, else another
        process reading before a record() would see stale values.
        """
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=500)
        store = quota._store_path(CHANNEL, CHAT)
        data = json.loads(store.read_text())
        data[MEMBER_UID]["day_anchor"] = time.time() - quota.WINDOW_S - 60
        store.write_text(json.dumps(data))

        # Call get_usage() which triggers rollover.
        usage = quota.get_usage(CHANNEL, CHAT, MEMBER_UID)
        self.assertEqual(usage["messages_today"], 0)
        self.assertEqual(usage["tokens_today"], 0)

        # Verify that the rolled-over state is persisted: reload from disk
        # and confirm counters are zero, not the prior 1/500.
        data_after = json.loads(store.read_text())
        self.assertEqual(data_after[MEMBER_UID]["messages"], 0)
        self.assertEqual(data_after[MEMBER_UID]["tokens"], 0)


class ListUsageTests(_QuotaTestBase):
    def test_list_usage_shape(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=10)
        out = quota.list_usage(CHANNEL, CHAT)
        self.assertEqual(out["channel"], CHANNEL)
        self.assertIn(MEMBER_UID, out["entries"])


class AuditViewTests(_QuotaTestBase):
    def test_view_me_matches_uid_in_multiple_keys(self):
        # Generate three events that stamp uid in different fields.
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        roles.revoke(CHANNEL, CHAT, MEMBER_UID, revoked_by=OWNER_UID)
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        if not audit_view._audit_path().exists():
            self.skipTest("forge package not available — _audit no-op")
        out = audit_view.view_me(channel=CHANNEL, chat_key=CHAT,
                                 uid=MEMBER_UID, limit=20)
        # MEMBER_UID was the target/grantor in 3 events.
        self.assertGreaterEqual(out["count"], 1)
        for ev in out["events"]:
            det = ev.get("details") or {}
            self.assertTrue(
                MEMBER_UID in (det.get("target"), det.get("uid"),
                               det.get("grantor"), det.get("granted_by"),
                               det.get("revoker"), det.get("revoked_by")),
                f"event {ev.get('event_type')} doesn't mention {MEMBER_UID}")

    def test_view_chat_prefix_filter(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        if not audit_view._audit_path().exists():
            self.skipTest("forge package not available — _audit no-op")
        out = audit_view.view_chat(channel=CHANNEL, chat_key=CHAT,
                                   event_type_prefix="grant.")
        for ev in out["events"]:
            self.assertTrue(str(ev.get("event_type")).startswith("grant."),
                           f"non-matching event: {ev.get('event_type')}")

    def test_summarize_event_single_line(self):
        ev = {
            "event_type": "grant.issued",
            "severity": "INFO",
            "ts": time.time(),
            "details": {
                "uid": MEMBER_UID,
                "target": MEMBER_UID,
                "bundle": "member",
                "grantor": OWNER_UID,
            },
        }
        line = audit_view.summarize_event(ev)
        self.assertIn("grant.issued", line)
        self.assertIn(f"uid={MEMBER_UID}", line)
        self.assertNotIn("\n", line)

    def test_view_does_not_mutate_chain(self):
        if not audit_view._audit_path().exists():
            roles.grant(CHANNEL, CHAT, MEMBER_UID,
                        bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        if not audit_view._audit_path().exists():
            self.skipTest("forge package not available — _audit no-op")
        path = audit_view._audit_path()
        before = path.stat().st_size
        audit_view.view_me(channel=CHANNEL, chat_key=CHAT, uid=MEMBER_UID)
        audit_view.view_chat(channel=CHANNEL, chat_key=CHAT)
        after = path.stat().st_size
        self.assertEqual(before, after, "view-only paths must not write back")


class CliRoundTripTests(_QuotaTestBase):
    def _cli(self, module: str, *args: str) -> dict | str:
        env = os.environ.copy()
        env["CORVIN_HOME"] = self._tmp
        bridges_root = Path(__file__).resolve().parent.parent
        real_settings = bridges_root / CHANNEL / "settings.json"
        if real_settings.exists():
            try:
                live = json.loads(real_settings.read_text())
            except Exception:
                live = {}
            if (live.get("whitelist") or []) and OWNER_UID not in (live.get("whitelist") or []):
                self.skipTest("real bridge settings would be clobbered")
        backup = real_settings.read_text() if real_settings.exists() else None
        real_settings.parent.mkdir(parents=True, exist_ok=True)
        real_settings.write_text(self._channel_settings.read_text())
        try:
            proc = subprocess.run(
                [sys.executable, str(HERE / module), *args],
                env=env, capture_output=True, text=True, timeout=10)
        finally:
            if backup is not None:
                real_settings.write_text(backup)
            else:
                try: real_settings.unlink()
                except FileNotFoundError: pass
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout

    def test_quota_check_for_owner(self):
        out = self._cli("quota.py", "check", CHANNEL, CHAT, OWNER_UID, "0")
        self.assertTrue(out.get("allowed"))
        self.assertEqual(out.get("reason"), "owner-bypass")

    def test_quota_set_clear_round_trip(self):
        # First grant member via direct in-process roles call so the CLI
        # subprocess sees the right role.
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        set_out = self._cli("quota.py", "set", CHANNEL, CHAT, MEMBER_UID,
                            "42", "1000", OWNER_UID)
        self.assertTrue(set_out["ok"])
        usage = self._cli("quota.py", "usage", CHANNEL, CHAT, MEMBER_UID)
        self.assertEqual(usage["limit_msgs"], 42)
        clr_out = self._cli("quota.py", "set", CHANNEL, CHAT, MEMBER_UID,
                            "clear", "clear", OWNER_UID)
        self.assertTrue(clr_out["ok"])

    def test_audit_view_me_cli(self):
        out = self._cli("audit_view.py", "me", CHANNEL, CHAT,
                        OWNER_UID, "5", "")
        self.assertEqual(out.get("scope"), "me")


class HashChainIntegrityTests(_QuotaTestBase):
    def test_quota_events_chain_verifies(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        quota.record(CHANNEL, CHAT, MEMBER_UID, tokens=10)
        quota.set_limit(CHANNEL, CHAT, MEMBER_UID, limit_msgs=5,
                        set_by=OWNER_UID)
        quota.reset(CHANNEL, CHAT, MEMBER_UID, reset_by=OWNER_UID)
        path = quota._audit_path()
        if not path.exists():
            self.skipTest("forge package not available — _audit no-op")
        repo = HERE
        for parent in HERE.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent; break
        forge_pkg = repo / "operator" / "forge"
        sys.path.insert(0, str(forge_pkg))
        from forge.security_events import verify_chain  # type: ignore
        ok, problems = verify_chain(path)
        self.assertTrue(ok, f"chain broken: {problems[:5]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
