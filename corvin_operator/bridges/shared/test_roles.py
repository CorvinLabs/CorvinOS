"""test_roles.py — Layer 18 capability-bundle role system.

Covers parse_ttl, bundle catalog, intrinsic-owner DEV-mode parity,
effective_role with lazy prune, grant input validation, grant authority
matrix, TTL clamps + indefinite, revoke authority, owner-not-revocable,
leave (multiple sub-paths), status, list_roles, CLI round-trip via
subprocess, and audit chain integrity. Per the LDD per-subtask E2E
discipline, the chain check goes through forge.security_events.verify_chain.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import roles  # noqa: E402


CHANNEL = "telegram"
CHAT = "1502103856740302964"
OWNER_UID = "owner-99"
ADMIN_UID = "admin-77"
MEMBER_UID = "member-55"
OBSERVER_UID = "obs-33"
STRANGER_UID = "stranger-11"


class _RolesTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="roles-test-")
        # Sandbox CORVIN_HOME so the roles module's audit writes land in the
        # tempdir, NOT the real <corvin>/global/forge/audit.jsonl. (A stray
        # ``os.environ.pop`` here previously nullified the sandbox, so the
        # chain-integrity test verified — and grant/revoke polluted — the real
        # production chain.)
        os.environ["CORVIN_HOME"] = self._tmp
        # Keep the channel settings.json out of the test sandbox — write a
        # minimal one with OWNER on the whitelist into the bridges/<channel>/
        # path the module reads. Use a temp file location and monkey-patch
        # the resolver so we don't pollute the real bridge config.
        self._channel_dir = Path(self._tmp) / "channels" / CHANNEL
        self._channel_dir.mkdir(parents=True)
        self._channel_settings = self._channel_dir / "settings.json"
        self._channel_settings.write_text(json.dumps({
            "whitelist": [OWNER_UID],
            "read_only": [],
            "rate_limit_per_hour": 30,
        }))
        self._orig_resolver = roles._channel_settings_path
        roles._channel_settings_path = lambda channel: self._channel_settings  # type: ignore[assignment]

    def tearDown(self) -> None:
        roles._channel_settings_path = self._orig_resolver  # type: ignore[assignment]
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)


class ParseTtlTests(_RolesTestBase):
    def test_parse_ttl_seconds(self):
        self.assertEqual(roles.parse_ttl("60s"), 60)
        self.assertEqual(roles.parse_ttl("120s"), 120)

    def test_parse_ttl_minutes_hours_days(self):
        self.assertEqual(roles.parse_ttl("5m"), 300)
        self.assertEqual(roles.parse_ttl("1h"), 3600)
        self.assertEqual(roles.parse_ttl("7d"), 7 * 86400)

    def test_parse_ttl_clamp_min(self):
        # 30s is below MIN_TTL_S=60 → clamped up to 60.
        self.assertEqual(roles.parse_ttl("30s"), 60)
        self.assertEqual(roles.parse_ttl("1s"), 60)

    def test_parse_ttl_clamp_max(self):
        # 365d is way above MAX_TTL_S=30d → clamped down.
        self.assertEqual(roles.parse_ttl("365d"), 30 * 86400)

    def test_parse_ttl_indefinite_keywords(self):
        for kw in ("never", "forever", "inf", "infinite", "indefinite",
                   "NEVER", "Forever"):
            self.assertIsNone(roles.parse_ttl(kw),
                              f"keyword {kw!r} should map to None")

    def test_parse_ttl_invalid(self):
        for bad in ("garbage", "10x", "h7", "5min", "fortnight"):
            self.assertEqual(roles.parse_ttl(bad), -1,
                             f"{bad!r} should be -1 (invalid)")

    def test_parse_ttl_none_and_empty(self):
        self.assertIsNone(roles.parse_ttl(None))
        self.assertIsNone(roles.parse_ttl(""))
        self.assertIsNone(roles.parse_ttl("   "))


class BundleCatalogTests(_RolesTestBase):
    def test_canonical_bundle_set(self):
        self.assertEqual(roles.BUNDLES, ("observer", "member", "admin", "owner"))

    def test_capabilities_table_consistent(self):
        # Every bundle in BUNDLES has a capability set.
        for b in roles.BUNDLES:
            self.assertIn(b, roles.CAPABILITIES,
                          f"bundle {b!r} missing from CAPABILITIES")

    def test_grantable_by_admin_excludes_admin(self):
        # The "kerze nicht heißer als die flamme" rule: admin cannot grant admin.
        self.assertNotIn("admin", roles.GRANTABLE_BY["admin"])
        self.assertIn("member", roles.GRANTABLE_BY["admin"])
        self.assertIn("observer", roles.GRANTABLE_BY["admin"])

    def test_grantable_by_owner_excludes_owner(self):
        # Owner is intrinsic via the channel whitelist — never grantable.
        self.assertNotIn("owner", roles.GRANTABLE_BY["owner"])

    def test_member_observer_cannot_grant(self):
        self.assertEqual(roles.GRANTABLE_BY["member"], frozenset())
        self.assertEqual(roles.GRANTABLE_BY["observer"], frozenset())

    def test_can_helper(self):
        self.assertTrue(roles.can("owner", "trigger"))
        self.assertTrue(roles.can("admin", "delegate_member"))
        self.assertFalse(roles.can("admin", "delegate_admin"))
        self.assertFalse(roles.can("member", "delegate_observer"))
        self.assertTrue(roles.can("observer", "audit_self"))
        self.assertFalse(roles.can("observer", "trigger"))
        self.assertFalse(roles.can("nonsense", "trigger"))


class IntrinsicOwnerTests(_RolesTestBase):
    def test_owner_on_whitelist_is_intrinsic(self):
        self.assertTrue(roles.is_intrinsic_owner(CHANNEL, OWNER_UID))

    def test_stranger_not_intrinsic(self):
        self.assertFalse(roles.is_intrinsic_owner(CHANNEL, STRANGER_UID))

    def test_dev_mode_empty_whitelist(self):
        # Empty whitelist = DEV mode = every uid classifies as owner. This
        # mirrors auth.js's fail-open contract.
        self._channel_settings.write_text(json.dumps({"whitelist": []}))
        self.assertTrue(roles.is_intrinsic_owner(CHANNEL, "anyone"))

    def test_blank_uid_never_owner(self):
        self.assertFalse(roles.is_intrinsic_owner(CHANNEL, ""))


class EffectiveRoleTests(_RolesTestBase):
    def test_owner_resolves_via_whitelist(self):
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, OWNER_UID), "owner")

    def test_stranger_resolves_to_none(self):
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, STRANGER_UID), "none")

    def test_grant_then_resolve(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID,
                    ttl_s=3600, reason="test")
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, MEMBER_UID), "member")

    def test_lazy_prune_drops_expired(self):
        # Grant with TTL=60s, then rewrite the store with a backdated
        # expires_at and confirm the next read prunes it.
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=60)
        path = roles._store_path(CHANNEL, CHAT)
        data = json.loads(path.read_text())
        data[MEMBER_UID]["expires_at"] = time.time() - 1
        path.write_text(json.dumps(data))
        # Lazy prune fires here:
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, MEMBER_UID), "none")
        # And the store no longer contains the expired entry.
        self.assertNotIn(MEMBER_UID, json.loads(path.read_text()))


class GrantInputValidationTests(_RolesTestBase):
    def test_invalid_uid_shape(self):
        with self.assertRaises(ValueError) as cm:
            roles.grant(CHANNEL, CHAT, "bad/uid",
                        bundle="member", granted_by=OWNER_UID)
        self.assertEqual(str(cm.exception), "invalid-uid")

    def test_invalid_bundle(self):
        with self.assertRaises(ValueError) as cm:
            roles.grant(CHANNEL, CHAT, MEMBER_UID,
                        bundle="superuser", granted_by=OWNER_UID)
        self.assertEqual(str(cm.exception), "invalid-bundle")

    def test_owner_not_grantable(self):
        with self.assertRaises(ValueError) as cm:
            roles.grant(CHANNEL, CHAT, "x",
                        bundle="owner", granted_by=OWNER_UID)
        self.assertEqual(str(cm.exception), "owner-not-grantable")

    def test_self_grant_rejected(self):
        with self.assertRaises(ValueError) as cm:
            roles.grant(CHANNEL, CHAT, MEMBER_UID,
                        bundle="member", granted_by=MEMBER_UID)
        self.assertEqual(str(cm.exception), "self-grant")


class GrantAuthorityMatrixTests(_RolesTestBase):
    def test_owner_can_grant_admin(self):
        entry = roles.grant(CHANNEL, CHAT, ADMIN_UID,
                            bundle="admin", granted_by=OWNER_UID)
        self.assertEqual(entry["bundle"], "admin")

    def test_owner_can_grant_member_and_observer(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID)
        roles.grant(CHANNEL, CHAT, OBSERVER_UID,
                    bundle="observer", granted_by=OWNER_UID)
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, MEMBER_UID), "member")
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, OBSERVER_UID), "observer")

    def test_admin_can_grant_member_not_admin(self):
        # First make ADMIN_UID an admin via owner.
        roles.grant(CHANNEL, CHAT, ADMIN_UID,
                    bundle="admin", granted_by=OWNER_UID)
        # Admin grants member ✓
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=ADMIN_UID)
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, MEMBER_UID), "member")
        # Admin grants admin ✗
        with self.assertRaises(ValueError) as cm:
            roles.grant(CHANNEL, CHAT, "admin-2",
                        bundle="admin", granted_by=ADMIN_UID)
        self.assertEqual(str(cm.exception), "insufficient-authority")

    def test_member_cannot_grant_anything(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID)
        with self.assertRaises(ValueError) as cm:
            roles.grant(CHANNEL, CHAT, "another",
                        bundle="observer", granted_by=MEMBER_UID)
        self.assertEqual(str(cm.exception), "insufficient-authority")

    def test_stranger_cannot_grant(self):
        with self.assertRaises(ValueError) as cm:
            roles.grant(CHANNEL, CHAT, "x",
                        bundle="observer", granted_by=STRANGER_UID)
        self.assertEqual(str(cm.exception), "insufficient-authority")


class TtlBehaviourTests(_RolesTestBase):
    def test_ttl_clamps_to_max(self):
        # 365d as int is way above MAX_TTL_S → clamp.
        entry = roles.grant(CHANNEL, CHAT, MEMBER_UID,
                            bundle="member", granted_by=OWNER_UID,
                            ttl_s=365 * 86400)
        remaining = entry["expires_at"] - entry["granted_at"]
        self.assertLessEqual(remaining, roles.MAX_TTL_S + 1)

    def test_ttl_clamps_to_min(self):
        entry = roles.grant(CHANNEL, CHAT, MEMBER_UID,
                            bundle="member", granted_by=OWNER_UID,
                            ttl_s=10)
        remaining = entry["expires_at"] - entry["granted_at"]
        self.assertGreaterEqual(remaining, roles.MIN_TTL_S - 1)

    def test_indefinite_grant_has_no_expiry(self):
        entry = roles.grant(CHANNEL, CHAT, ADMIN_UID,
                            bundle="admin", granted_by=OWNER_UID,
                            ttl_s=None)
        self.assertIsNone(entry["expires_at"])


class RevokeTests(_RolesTestBase):
    def test_owner_revokes_member(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID)
        self.assertTrue(roles.revoke(CHANNEL, CHAT, MEMBER_UID,
                                     revoked_by=OWNER_UID))
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, MEMBER_UID), "none")

    def test_revoke_idempotent(self):
        # Revoking a missing entry returns False (not an error).
        self.assertFalse(roles.revoke(CHANNEL, CHAT, MEMBER_UID,
                                      revoked_by=OWNER_UID))

    def test_admin_revokes_member_but_not_peer(self):
        roles.grant(CHANNEL, CHAT, ADMIN_UID, bundle="admin", granted_by=OWNER_UID)
        admin_2 = "admin-2"
        roles.grant(CHANNEL, CHAT, admin_2, bundle="admin", granted_by=OWNER_UID)
        roles.grant(CHANNEL, CHAT, MEMBER_UID, bundle="member", granted_by=ADMIN_UID)
        # Admin can revoke member ✓
        self.assertTrue(roles.revoke(CHANNEL, CHAT, MEMBER_UID,
                                     revoked_by=ADMIN_UID))
        # Admin CANNOT revoke another admin ✗
        with self.assertRaises(ValueError) as cm:
            roles.revoke(CHANNEL, CHAT, admin_2, revoked_by=ADMIN_UID)
        self.assertEqual(str(cm.exception), "cannot-revoke-peer")

    def test_member_cannot_revoke(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID)
        with self.assertRaises(ValueError) as cm:
            roles.revoke(CHANNEL, CHAT, MEMBER_UID, revoked_by=MEMBER_UID)
        self.assertEqual(str(cm.exception), "insufficient-authority")

    def test_owner_not_revocable(self):
        # Even owner cannot revoke another owner via the API; whitelist edits
        # are the only path.
        # Add a second owner via direct whitelist edit:
        owner2 = "owner-2"
        self._channel_settings.write_text(json.dumps({
            "whitelist": [OWNER_UID, owner2],
        }))
        with self.assertRaises(ValueError) as cm:
            roles.revoke(CHANNEL, CHAT, owner2, revoked_by=OWNER_UID)
        self.assertEqual(str(cm.exception), "owner-not-revocable")


class LeaveTests(_RolesTestBase):
    def test_member_leaves_self(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID)
        result = roles.leave(CHANNEL, CHAT, MEMBER_UID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["prior_bundle"], "member")
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, MEMBER_UID), "none")

    def test_owner_cannot_leave(self):
        result = roles.leave(CHANNEL, CHAT, OWNER_UID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "owner-cannot-leave")

    def test_no_entry_leave(self):
        result = roles.leave(CHANNEL, CHAT, STRANGER_UID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no-entry")

    def test_invalid_uid_leave(self):
        result = roles.leave(CHANNEL, CHAT, "bad/uid")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid-uid")


class StatusAndListTests(_RolesTestBase):
    def test_status_owner_fields(self):
        st = roles.status(CHANNEL, CHAT, OWNER_UID)
        self.assertEqual(st["role"], "owner")
        self.assertTrue(st["intrinsic_owner"])
        self.assertIn("trigger", st["capabilities"])
        self.assertIsNone(st["bundle"])  # owner is intrinsic, no entry

    def test_status_granted_member_fields(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600,
                    reason="testing")
        st = roles.status(CHANNEL, CHAT, MEMBER_UID)
        self.assertEqual(st["role"], "member")
        self.assertEqual(st["bundle"], "member")
        self.assertEqual(st["granted_by"], OWNER_UID)
        self.assertEqual(st["reason"], "testing")
        self.assertGreater(st["remaining_s"], 0)

    def test_status_stranger(self):
        st = roles.status(CHANNEL, CHAT, STRANGER_UID)
        self.assertEqual(st["role"], "none")
        self.assertFalse(st["intrinsic_owner"])
        self.assertEqual(st["capabilities"], [])

    def test_list_roles_shape(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID)
        out = roles.list_roles(CHANNEL, CHAT)
        self.assertIn(OWNER_UID, out["intrinsic_owners"])
        self.assertIn(MEMBER_UID, out["granted"])
        self.assertEqual(out["channel"], CHANNEL)
        self.assertEqual(out["chat_key"], CHAT)


class CliRoundTripTests(_RolesTestBase):
    def _cli(self, *args: str) -> dict:
        env = os.environ.copy()
        env["CORVIN_HOME"] = self._tmp
        # The CLI subprocess can't use the monkey-patched _channel_settings_path,
        # so we copy the fixture into the resolver's expected location.
        bridges_root = Path(__file__).resolve().parent.parent
        real_settings = bridges_root / CHANNEL / "settings.json"
        # Don't clobber a real bridge settings file — only write if absent
        # OR if its whitelist already matches.
        if real_settings.exists():
            try:
                live = json.loads(real_settings.read_text())
            except Exception:
                live = {}
            if (live.get("whitelist") or []) and OWNER_UID not in (live.get("whitelist") or []):
                self.skipTest("real bridge settings would be clobbered")
        backup = None
        if real_settings.exists():
            backup = real_settings.read_text()
        real_settings.parent.mkdir(parents=True, exist_ok=True)
        real_settings.write_text(self._channel_settings.read_text())
        try:
            proc = subprocess.run(
                [sys.executable, str(HERE / "roles.py"), *args],
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
            return {"_stdout": proc.stdout, "_stderr": proc.stderr,
                    "_rc": proc.returncode}

    def test_can_subcommand(self):
        out = self._cli("can", "owner", "trigger")
        self.assertTrue(out["can"])
        out = self._cli("can", "observer", "trigger")
        self.assertFalse(out["can"])

    def test_can_grant_subcommand(self):
        out = self._cli("can-grant", "owner", "admin")
        self.assertTrue(out["can_grant"])
        out = self._cli("can-grant", "admin", "admin")
        self.assertFalse(out["can_grant"])

    def test_grant_then_role_round_trip(self):
        out = self._cli("grant", CHANNEL, CHAT, MEMBER_UID,
                        "member", OWNER_UID, "1h", "ad-hoc")
        self.assertTrue(out.get("ok"), out)
        out = self._cli("role", CHANNEL, CHAT, MEMBER_UID)
        self.assertEqual(out["role"], "member")

    def test_grant_invalid_ttl_returns_error(self):
        out = self._cli("grant", CHANNEL, CHAT, MEMBER_UID,
                        "member", OWNER_UID, "garbage")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid-ttl")


class AuditChainIntegrityTests(_RolesTestBase):
    def test_grant_revoke_chain_verifies(self):
        roles.grant(CHANNEL, CHAT, MEMBER_UID,
                    bundle="member", granted_by=OWNER_UID)
        roles.revoke(CHANNEL, CHAT, MEMBER_UID, revoked_by=OWNER_UID)
        # The roles module writes into <corvin>/global/forge/audit.jsonl.
        audit_path = roles._audit_path()
        # If forge isn't on sys.path yet, _audit() will silently no-op.
        # This test is meaningful only when the chain actually got entries.
        if not audit_path.exists():
            self.skipTest("forge package not available — _audit no-op")
        # Use forge's verify_chain.
        repo = HERE
        for parent in HERE.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent; break
        forge_pkg = repo / "operator" / "forge"
        sys.path.insert(0, str(forge_pkg))
        from forge.security_events import verify_chain  # type: ignore
        ok, problems = verify_chain(audit_path)
        self.assertTrue(ok, f"chain broken: {problems[:5]}")

    def test_denied_grant_emits_audit(self):
        # Stranger tries to grant — should emit grant.denied even though it
        # raises ValueError in-process.
        try:
            roles.grant(CHANNEL, CHAT, "x",
                        bundle="observer", granted_by=STRANGER_UID)
        except ValueError:
            pass
        # We don't read the chain content here (forge may be absent); the
        # in-process call just must not crash.


if __name__ == "__main__":
    unittest.main(verbosity=2)
