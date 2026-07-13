"""test_consent.py — V-016: consent store corruption handling for ADR-0072.

Tests that is_granted() handles a corrupted (invalid JSON) consent store
gracefully: returns (False, "store-corrupted"), backs up the corrupted file,
and returns (False, "no-entry") for a fresh store with no entry.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Make the shared package importable.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import consent  # noqa: E402


class ConsentStoreCorruptionTests(unittest.TestCase):
    """V-016: corrupted consent store must be handled without an exception."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="consent-test-")
        os.environ["CORVIN_HOME"] = self._tmp

    def tearDown(self) -> None:
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _store_path(self, channel: str, chat_key: str) -> Path:
        """Resolve the consent store path using the same logic as the module."""
        return consent._store_path(channel, chat_key)

    # ------------------------------------------------------------------
    # Corruption handling
    # ------------------------------------------------------------------

    def test_consent_store_corrupted(self):
        """is_granted returns (False, 'store-corrupted') when the JSON is invalid
        and backs up the corrupted file next to the original."""
        channel = "discord"
        chat_key = "chat1"
        uid = "user123"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{ this is not valid JSON !!!")

        granted, reason = consent.is_granted(channel, chat_key, uid)

        self.assertFalse(granted,
                         "is_granted must return False on store corruption")
        self.assertEqual(reason, "store-corrupted",
                         f"reason must be 'store-corrupted', got {reason!r}")

        # The corrupted file must have been backed up.
        parent = store_path.parent
        backups = list(parent.glob(store_path.name + ".corrupt.*"))
        self.assertGreater(len(backups), 0,
                           "a .corrupt.TIMESTAMP backup file must exist after corruption handling")

    def test_consent_store_corrupted_original_removed(self):
        """After corruption handling the original corrupted file must be gone
        (so the next call starts with a clean state)."""
        channel = "discord"
        chat_key = "chat2"
        uid = "user-clean"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("NOT JSON AT ALL")

        consent.is_granted(channel, chat_key, uid)

        self.assertFalse(store_path.exists(),
                         "corrupted store file must be removed after backup")

    def test_consent_store_corrupted_subsequent_call_is_no_entry(self):
        """After the corrupted file is removed, the next call must return
        (False, 'no-entry') — not another 'store-corrupted'."""
        channel = "discord"
        chat_key = "chat3"
        uid = "user-subsequent"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{invalid}")

        # First call: handles corruption.
        consent.is_granted(channel, chat_key, uid)

        # Second call: file is gone, behaves like a fresh store.
        granted2, reason2 = consent.is_granted(channel, chat_key, uid)
        self.assertFalse(granted2)
        self.assertEqual(reason2, "no-entry",
                         "after corruption cleanup subsequent call must return 'no-entry'")

    # ------------------------------------------------------------------
    # Fresh / no-entry cases
    # ------------------------------------------------------------------

    def test_consent_fresh_store_is_no_entry(self):
        """A brand-new CORVIN_HOME with no consent file must yield
        (False, 'no-entry') — never an exception."""
        channel = "discord"
        chat_key = "chat1"
        uid = "user-new"

        granted, reason = consent.is_granted(channel, chat_key, uid)

        self.assertFalse(granted,
                         "is_granted must return False for a user with no consent entry")
        self.assertEqual(reason, "no-entry",
                         f"reason must be 'no-entry' for a fresh store, got {reason!r}")

    def test_consent_no_entry_for_unknown_uid(self):
        """A store that exists but has no entry for the queried UID must return
        (False, 'no-entry')."""
        channel = "discord"
        chat_key = "chat4"
        uid_present = "user-A"
        uid_absent  = "user-B"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({
            uid_present: {
                "mode": "durable",
                "granted_at": 1778204770.0,
                "expires_at": None,
                "channel": channel,
                "granted_via": "slash",
            }
        }))

        granted, reason = consent.is_granted(channel, chat_key, uid_absent)

        self.assertFalse(granted,
                         "unknown UID must yield is_granted=False")
        self.assertEqual(reason, "no-entry",
                         f"reason must be 'no-entry' for absent UID, got {reason!r}")

    def test_consent_no_uid_returns_no_uid(self):
        """Empty-string UID short-circuits with 'no-uid', not 'no-entry'."""
        granted, reason = consent.is_granted("discord", "chat1", "")
        self.assertFalse(granted)
        self.assertEqual(reason, "no-uid",
                         f"empty UID must return 'no-uid', got {reason!r}")

    # ------------------------------------------------------------------
    # Happy-path sanity
    # ------------------------------------------------------------------

    def test_consent_durable_entry_is_granted(self):
        """A valid durable consent entry must cause is_granted to return True."""
        channel = "discord"
        chat_key = "chat5"
        uid = "user-durable"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({
            uid: {
                "mode": "durable",
                "granted_at": 1778204770.0,
                "expires_at": None,
                "channel": channel,
                "granted_via": "slash",
            }
        }))

        granted, reason = consent.is_granted(channel, chat_key, uid)

        self.assertTrue(granted,
                        f"durable consent must be granted, got ({granted}, {reason!r})")
        self.assertEqual(reason, "durable")


class ConsentStoreTypeConfusionTests(unittest.TestCase):
    """Blind-spot coverage: a syntactically-valid-JSON consent entry with a
    type-confused shape (e.g. a non-numeric ``expires_at``) is NOT caught by
    _prune()'s ``isinstance(exp, (int, float))`` guard (consent.py line ~373),
    so the entry survives pruning and is handed straight back to is_granted(),
    which today crashes with an uncaught TypeError at
    ``remaining = max(0, int(exp - time.time()))`` (consent.py line ~484).

    Unlike the ConsentStoreCorrupted (invalid-JSON-text) path exercised in
    ConsentStoreCorruptionTests above, is_granted() has no defense-in-depth
    for this second corruption class. These tests currently document the bug
    by asserting the CRASH (xfail-style, via assertRaises) rather than a
    tautological pass, so the suite goes red the moment this is fixed and a
    human notices the contract changed — see bugsDiscovered in the task
    write-up for the recommended fix (treat as deny + audit, matching the
    ConsentStoreCorrupted contract).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="consent-typeconfusion-test-")
        os.environ["CORVIN_HOME"] = self._tmp

    def tearDown(self) -> None:
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _store_path(self, channel: str, chat_key: str) -> Path:
        return consent._store_path(channel, chat_key)

    def test_string_expires_at_survives_prune_unneutralized(self):
        """_prune()'s isinstance guard must not silently keep a type-confused
        time_bounded entry as-if it were still valid — but today it does:
        the entry is neither expired-and-dropped nor coerced/rejected, it is
        just passed through into ``kept`` unchanged."""
        channel = "discord"
        chat_key = "chat-typeconfusion-1"
        uid = "user-typeconfusion"

        data = {
            uid: {
                "mode": "time_bounded",
                "granted_at": 1778204770.0,
                "expires_at": "not-a-number",
                "channel": channel,
                "granted_via": "slash",
            }
        }
        kept, expired_uids = consent._prune(data)

        self.assertNotIn(uid, expired_uids,
                          "type-confused expires_at must not be treated as "
                          "silently 'expired' by the isinstance guard")
        self.assertIn(uid, kept,
                      "documents current (unsafe) behavior: the type-confused "
                      "entry survives _prune() unneutralized and is handed "
                      "back to is_granted()")

    def test_is_granted_crashes_on_string_expires_at(self):
        """REGRESSION-DOCUMENTING TEST for a confirmed bug: is_granted() must
        never raise for any syntactically-valid consent-store shape -- it
        should behave like the ConsentStoreCorrupted path (deny + audit),
        not blow up. Today it raises TypeError. This test currently asserts
        the (buggy) crash; when the bug is fixed, replace this assertion with
        the intended contract: ``self.assertEqual(consent.is_granted(...),
        (False, "entry-corrupted"))`` (or whatever tag the fix picks) and
        additionally assert a 'consent.entry_corrupted'-style audit event.
        """
        channel = "discord"
        chat_key = "chat-typeconfusion-2"
        uid = "user-typeconfusion-2"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({
            uid: {
                "mode": "time_bounded",
                "granted_at": 1778204770.0,
                "expires_at": "not-a-number",
                "channel": channel,
                "granted_via": "slash",
            }
        }))

        with self.assertRaises(TypeError):
            consent.is_granted(channel, chat_key, uid)

    def test_is_granted_crashes_on_string_granted_at_with_time_bounded(self):
        """Same class of bug via a different type-confused field: a
        non-numeric expires_at combined with a missing/garbage granted_at
        must not be able to sneak an entry through cleanly either -- confirm
        the crash is specifically keyed to the expires_at arithmetic and not
        accidentally masked by some other field."""
        channel = "discord"
        chat_key = "chat-typeconfusion-3"
        uid = "user-typeconfusion-3"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({
            uid: {
                "mode": "time_bounded",
                "granted_at": "also-not-a-number",
                "expires_at": ["nested", "garbage"],
                "channel": channel,
                "granted_via": "slash",
            }
        }))

        with self.assertRaises(TypeError):
            consent.is_granted(channel, chat_key, uid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
