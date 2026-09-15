"""test_disclosure.py — Layer 19 bot-disclosure card + /join self-service.

Covers seen round-trip, owner-implicit (no audit, no store), action
transitions, card text DE / EN with length cap and transcript paragraph,
/join self-service, /join denials (owner-already, already-elevated),
/pass ack-without-grant, list_seen, CLI subcommand round-trip, and
audit chain integrity. Cross-module check: /join must produce a
roles.observer entry visible to roles.list_roles.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import disclosure  # noqa: E402
import roles  # noqa: E402

CHANNEL = "telegram"
CHAT = "150210"
OWNER_UID = "owner-19"
STRANGER_UID = "stranger-19"
OBSERVER_UID = "obs-19"


class _DisclosureTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="disclosure-test-")
        self._orig_corvin_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp
        # Channel settings: OWNER_UID is the only intrinsic owner.
        self._channel_dir = Path(self._tmp) / "channels" / CHANNEL
        self._channel_dir.mkdir(parents=True)
        self._channel_settings = self._channel_dir / "settings.json"
        self._channel_settings.write_text(json.dumps({
            "whitelist": [OWNER_UID],
            "read_only": [],
        }))
        # Patch BOTH disclosure._channel_settings_path AND
        # roles._channel_settings_path so /join (which crosses into roles)
        # sees the same whitelist.
        self._orig_disc = disclosure._channel_settings_path
        self._orig_roles = roles._channel_settings_path
        disclosure._channel_settings_path = lambda c: self._channel_settings
        roles._channel_settings_path = lambda c: self._channel_settings

    def tearDown(self) -> None:
        disclosure._channel_settings_path = self._orig_disc
        roles._channel_settings_path = self._orig_roles
        if self._orig_corvin_home is not None:
            os.environ["CORVIN_HOME"] = self._orig_corvin_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(self._tmp, ignore_errors=True)


class HasSeenTests(_DisclosureTestBase):
    def test_unseen_returns_false(self):
        self.assertFalse(disclosure.has_seen(CHANNEL, CHAT, STRANGER_UID))

    def test_owner_always_seen(self):
        self.assertTrue(disclosure.has_seen(CHANNEL, CHAT, OWNER_UID))

    def test_blank_uid_returns_false(self):
        self.assertFalse(disclosure.has_seen(CHANNEL, CHAT, ""))

    def test_seen_after_mark(self):
        disclosure.mark_seen(CHANNEL, CHAT, STRANGER_UID)
        self.assertTrue(disclosure.has_seen(CHANNEL, CHAT, STRANGER_UID))


class MarkSeenTests(_DisclosureTestBase):
    def test_first_contact_writes_pending(self):
        entry = disclosure.mark_seen(CHANNEL, CHAT, STRANGER_UID)
        self.assertEqual(entry["action"], disclosure.ACTION_PENDING)
        self.assertEqual(entry["channel"], CHANNEL)

    def test_owner_returns_implicit_no_store(self):
        before = disclosure._load_store(disclosure._store_path(CHANNEL, CHAT))
        entry = disclosure.mark_seen(CHANNEL, CHAT, OWNER_UID)
        self.assertEqual(entry["action"], disclosure.ACTION_OWNER_IMPLICIT)
        after = disclosure._load_store(disclosure._store_path(CHANNEL, CHAT))
        self.assertEqual(before, after, "owner mark_seen must not write the store")

    def test_action_transition(self):
        disclosure.mark_seen(CHANNEL, CHAT, STRANGER_UID,
                             action=disclosure.ACTION_PENDING)
        disclosure.mark_seen(CHANNEL, CHAT, STRANGER_UID,
                             action=disclosure.ACTION_PASSED)
        state = disclosure.get_state(CHANNEL, CHAT, STRANGER_UID)
        self.assertEqual(state["action"], disclosure.ACTION_PASSED)
        self.assertIsNotNone(state["last_action_at"])

    def test_invalid_action_raises(self):
        with self.assertRaises(ValueError):
            disclosure.mark_seen(CHANNEL, CHAT, STRANGER_UID,
                                 action="not-a-real-action")

    def test_invalid_uid_raises(self):
        with self.assertRaises(ValueError):
            disclosure.mark_seen(CHANNEL, CHAT, "")


class CardTextTests(_DisclosureTestBase):
    def test_de_card_under_cap(self):
        card = disclosure.get_card_text(owner_label="Silvio",
                                        channel=CHANNEL, lang="de")
        self.assertLessEqual(len(card), disclosure.MAX_CARD_CHARS)
        self.assertIn("Silvio", card)
        self.assertIn("/join", card)
        self.assertIn("/pass", card)
        self.assertIn("/leave", card)

    def test_en_card_under_cap(self):
        card = disclosure.get_card_text(owner_label="Silvio",
                                        channel=CHANNEL, lang="en")
        self.assertLessEqual(len(card), disclosure.MAX_CARD_CHARS)
        self.assertIn("Silvio", card)
        self.assertIn("/join", card)

    def test_transcript_paragraph_present_when_flag_on(self):
        off = disclosure.get_card_text(owner_label="X", channel=CHANNEL,
                                       has_observer_transcript=False, lang="de")
        on = disclosure.get_card_text(owner_label="X", channel=CHANNEL,
                                      has_observer_transcript=True, lang="de")
        self.assertNotEqual(off, on,
                            "transcript flag must change card body")
        self.assertIn("/consent on", on)

    def test_unknown_owner_label_falls_back_gracefully(self):
        card = disclosure.get_card_text(owner_label="", channel=CHANNEL, lang="de")
        # Renderer never crashes; some sentinel like "(unknown)" appears.
        self.assertGreater(len(card), 100)


class JoinTests(_DisclosureTestBase):
    def test_join_as_stranger_creates_observer(self):
        result = disclosure.join(CHANNEL, CHAT, STRANGER_UID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "joined")
        self.assertEqual(result["current"], "observer")
        # Cross-check via roles:
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, STRANGER_UID),
                         "observer")

    def test_join_as_owner_returns_owner_already(self):
        result = disclosure.join(CHANNEL, CHAT, OWNER_UID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "owner-already")

    def test_join_as_existing_member_returns_already_elevated(self):
        # Owner promotes a stranger to member first:
        roles.grant(CHANNEL, CHAT, STRANGER_UID,
                    bundle="member", granted_by=OWNER_UID, ttl_s=3600)
        result = disclosure.join(CHANNEL, CHAT, STRANGER_UID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "already-elevated")
        self.assertEqual(result["current"], "member")

    def test_join_idempotent_for_existing_observer(self):
        disclosure.join(CHANNEL, CHAT, STRANGER_UID)
        result = disclosure.join(CHANNEL, CHAT, STRANGER_UID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "already-observer")

    def test_join_visible_in_roles_list(self):
        disclosure.join(CHANNEL, CHAT, STRANGER_UID)
        listing = roles.list_roles(CHANNEL, CHAT)
        self.assertIn(STRANGER_UID, listing["granted"])
        self.assertEqual(listing["granted"][STRANGER_UID]["bundle"], "observer")


class PassCardTests(_DisclosureTestBase):
    def test_pass_records_action_passed(self):
        result = disclosure.pass_card(CHANNEL, CHAT, STRANGER_UID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "passed")
        state = disclosure.get_state(CHANNEL, CHAT, STRANGER_UID)
        self.assertEqual(state["action"], disclosure.ACTION_PASSED)

    def test_pass_does_not_grant_role(self):
        disclosure.pass_card(CHANNEL, CHAT, STRANGER_UID)
        # No role assigned by /pass.
        self.assertEqual(roles.effective_role(CHANNEL, CHAT, STRANGER_UID), "none")

    def test_pass_as_owner_returns_owner_already(self):
        result = disclosure.pass_card(CHANNEL, CHAT, OWNER_UID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "owner-already")


class ListSeenTests(_DisclosureTestBase):
    def test_list_seen_includes_intrinsic_owners(self):
        out = disclosure.list_seen(CHANNEL, CHAT)
        self.assertIn(OWNER_UID, out["intrinsic_owners"])
        self.assertEqual(out["seen"], {})

    def test_list_seen_after_mark(self):
        disclosure.mark_seen(CHANNEL, CHAT, STRANGER_UID)
        disclosure.mark_seen(CHANNEL, CHAT, OBSERVER_UID,
                             action=disclosure.ACTION_PASSED)
        out = disclosure.list_seen(CHANNEL, CHAT)
        self.assertEqual(set(out["seen"].keys()), {STRANGER_UID, OBSERVER_UID})


class CliRoundTripTests(_DisclosureTestBase):
    def _cli(self, *args: str) -> dict | str:
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
                [sys.executable, str(HERE / "disclosure.py"), *args],
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

    def test_state_subcommand(self):
        out = self._cli("state", CHANNEL, CHAT, OWNER_UID)
        self.assertIsInstance(out, dict)
        self.assertEqual(out["action"], disclosure.ACTION_OWNER_IMPLICIT)

    def test_card_subcommand_renders_text(self):
        out = self._cli("card", CHANNEL, "Silvio", "de")
        self.assertIsInstance(out, str)
        self.assertIn("Silvio", out)

    def test_join_then_state_round_trip(self):
        join_res = self._cli("join", CHANNEL, CHAT, STRANGER_UID)
        self.assertTrue(join_res.get("ok"), join_res)
        state = self._cli("state", CHANNEL, CHAT, STRANGER_UID)
        self.assertEqual(state["action"], disclosure.ACTION_JOINED)


class AuditChainIntegrityTests(_DisclosureTestBase):
    def test_card_show_and_join_audit_chain_verifies(self):
        disclosure.mark_seen(CHANNEL, CHAT, STRANGER_UID)
        disclosure.join(CHANNEL, CHAT, OBSERVER_UID)
        audit_path = disclosure._audit_path()
        if not audit_path.exists():
            self.skipTest("forge package not available — _audit no-op")
        repo = HERE
        for parent in HERE.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent; break
        forge_pkg = repo / "operator" / "forge"
        sys.path.insert(0, str(forge_pkg))
        from forge.security_events import verify_chain  # type: ignore
        ok, problems = verify_chain(audit_path)
        self.assertTrue(ok, f"chain broken: {problems[:5]}")


class StoreRetryTests(unittest.TestCase):
    """V-004/V-011: _save_store retries on OSError before giving up.

    Uses its own isolated tmpdir — does NOT inherit _DisclosureTestBase so it
    can fully control the store path without risking writes to ~/.corvin/.
    """

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="store-retry-test-")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_disclosure_mark_seen_retry(self):
        """_save_store internally retries on OSError from os.open (the lock-file
        acquire step).  Mock os.open so it raises OSError on the first two
        attempts and succeeds on the third; verify _save_store eventually returns
        without error and the file is written."""
        import time as _time_mod

        original_open = os.open
        open_call_count = 0

        def _flaky_open(*args, **kwargs):
            nonlocal open_call_count
            # Only intercept lock-file creates (O_CREAT | O_RDWR).
            if len(args) >= 2 and (args[1] & os.O_CREAT):
                open_call_count += 1
                if open_call_count <= 2:
                    raise OSError(f"simulated ENOSPC on attempt {open_call_count}")
            return original_open(*args, **kwargs)

        # Use a path entirely within our tmpdir — never touches ~/.corvin/.
        path = Path(self._tmp) / "disclosure_store.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        test_data: dict = {"user-retry-001": {"action": "pending", "channel": "test"}}

        with unittest.mock.patch("os.open", side_effect=_flaky_open), \
             unittest.mock.patch.object(_time_mod, "sleep", return_value=None):
            # Should NOT raise — the internal retry loop must absorb the first
            # two OSErrors and succeed on the third attempt.
            disclosure._save_store(path, test_data)

        self.assertGreaterEqual(open_call_count, 3,
                                "os.open must have been called at least 3 times (2 fails + 1 success)")
        # The file must have been written on the eventual successful attempt.
        self.assertTrue(path.exists(), "_save_store must have written the store file")
        persisted = disclosure._load_store(path)
        self.assertIn("user-retry-001", persisted)

    def test_save_store_retry_exhausted_raises(self):
        """When all retry attempts are exhausted, _save_store raises OSError."""
        import time as _time_mod

        open_call_count = 0

        def _always_fail_open(*args, **kwargs):
            nonlocal open_call_count
            # Only intercept lock-file opens (O_CREAT | O_RDWR); let others pass.
            if len(args) >= 2 and (args[1] & os.O_CREAT):
                open_call_count += 1
                raise OSError("simulated: no space left on device")
            return os.open(*args, **kwargs)

        path = Path(self._tmp) / "fail_store.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        with unittest.mock.patch("os.open", side_effect=_always_fail_open), \
             unittest.mock.patch.object(_time_mod, "sleep", return_value=None):
            with self.assertRaises(OSError):
                disclosure._save_store(path, {"some": "data"})

        self.assertGreaterEqual(open_call_count, 1,
                                "should have tried at least once before raising")


if __name__ == "__main__":
    unittest.main(verbosity=2)
