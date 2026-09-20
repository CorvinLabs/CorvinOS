"""ADR-0905 — the OTA feature root key must never be observable as FREE while
a reload is resolving a paid licence.

Why this exists as a test and not as a comment: the defect it pins was already
WRITTEN DOWN (docs/claude-ref/layer-engines.md carried it as "known, outside
this ADR") and survived anyway, because the note recorded it at "one 401 in
~40 requests" — a single-Playwright-worker measurement. Against the real
request pattern (a console panel firing ~10 requests in parallel, one reload
per 5 s throttle window) it denied 8 of 10. A prose note does not fail a build;
this does.

The invariant: every authenticated console request derives its ADR-0154 M3
session proof from ``feature_root_key()`` via
``auth.py::_compute_lic_proof`` -> ``reload_from_disk()``. If the reload drops
the root to free before it has verified the on-disk token, a concurrent
derivation produces a free-root proof, mismatches the proof stored in the
session record, and the request is denied "session expired".
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO), str(_REPO / "corvin_operator")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from license import feature_lattice as FL  # noqa: E402
from license import validator as V  # noqa: E402

TOKEN = "header.payload.signature"
CLAIMS = {"tier": "member", "jti": "test-jti-0905", "iss": "corvinlabs.io", "type": "license"}


class RootKeyAtomicityTests(unittest.TestCase):
    """Drive the real reload with every I/O and crypto step stubbed out, and
    watch the root key from INSIDE the window the bug lived in."""

    def setUp(self) -> None:
        self._prev = {
            name: getattr(V, name)
            for name in (
                "_LICENSE_INITIALIZED", "_CORVIN_HOME_SNAPSHOT",
                "_LAST_RELOAD_AT", "_LAST_LOADED_TOKEN_HASH", "_LICENSE_LOADED_AT",
            )
        }
        V._LICENSE_INITIALIZED = True
        V._CORVIN_HOME_SNAPSHOT = Path("/nonexistent-adr-0905")
        # No throttle: every call in these tests must do real work.
        V._LAST_RELOAD_AT = 0.0
        V._LAST_LOADED_TOKEN_HASH = None
        self._paid_root = FL._paid_root_key(TOKEN)
        FL.set_feature_root_key(TOKEN)

    def tearDown(self) -> None:
        for name, value in self._prev.items():
            setattr(V, name, value)
        FL.set_feature_root_key(None)

    def _patches(self, observer, token: str | None = TOKEN):
        """Stub the reload's I/O, crypto and side effects. ``observer`` runs at
        the exact point the old code had already dropped the root to free."""
        def _verify(_tok):
            observer()
            return dict(CLAIMS)

        return (
            mock.patch.object(V, "_find_token_disk_only", lambda: token),
            mock.patch.object(V, "_verify_ed25519", _verify),
            mock.patch.object(V, "_is_token_fp_revoked", lambda _t: False),
            mock.patch.object(V, "_validate_claims", lambda c: dict(c)),
            mock.patch.object(V, "_check_instance_id_bound", lambda _c: True),
            mock.patch.object(V, "_check_device_fp", lambda _c: True),
            mock.patch.object(V, "_set_active_license", lambda _c: None),
            mock.patch.object(V, "_init_instance_seed", lambda: None),
            mock.patch.object(V, "_audit", lambda *a, **k: None),
        )

    def _run_reload(self, observer, token: str | None = TOKEN) -> None:
        patches = self._patches(observer, token)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        V.reload_from_disk()

    def test_root_key_is_never_free_mid_reload(self) -> None:
        seen: list[bytes] = []
        self._run_reload(lambda: seen.append(FL.feature_root_key()))

        self.assertEqual(len(seen), 1, "the observer never ran — the stub missed")
        self.assertEqual(
            seen[0], self._paid_root,
            "the root key was reset to FREE before the token was resolved — "
            "every concurrent session-proof derivation in this window is denied "
            "401 'session expired' (ADR-0905)",
        )
        self.assertNotEqual(seen[0], FL._free_root_key())
        # ...and it is still the paid root afterwards.
        self.assertEqual(FL.feature_root_key(), self._paid_root)

    def test_session_proof_is_stable_across_a_reload(self) -> None:
        """The property the console actually depends on."""
        sid = "adr0905-session-id"
        before = FL.session_lic_proof(sid)
        mid: list[str] = []
        self._run_reload(lambda: mid.append(FL.session_lic_proof(sid)))

        self.assertEqual(mid, [before], "proof changed mid-reload")
        self.assertEqual(FL.session_lic_proof(sid), before)

    def test_a_removed_token_still_lands_on_the_free_root(self) -> None:
        """The `finally` must not turn the fix into a paid-root leak: with no
        token on disk the reload still ends on free."""
        patches = (
            mock.patch.object(V, "_find_token_disk_only", lambda: None),
            mock.patch.object(V, "_set_active_license", lambda _c: None),
            mock.patch.object(V, "_init_instance_seed", lambda: None),
            mock.patch.object(V, "_audit", lambda *a, **k: None),
        )
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        V.reload_from_disk()
        self.assertEqual(FL.feature_root_key(), FL._free_root_key())

    def test_concurrent_reloads_are_serialised(self) -> None:
        """Two callers must not interleave inside the reload: it mutates
        process-wide state and the throttle's own bookkeeping."""
        inside = 0
        overlap = False
        guard = threading.Lock()

        def observer() -> None:
            nonlocal inside, overlap
            with guard:
                inside += 1
                if inside > 1:
                    overlap = True
            # long enough that an unserialised second caller would be caught
            threading.Event().wait(0.05)
            with guard:
                inside -= 1

        patches = self._patches(observer)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        def call() -> None:
            # defeat the 5 s throttle so every thread does real work
            V._LAST_RELOAD_AT = 0.0
            V.reload_from_disk()

        threads = [threading.Thread(target=call) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertFalse(overlap, "two reloads ran concurrently — _RELOAD_LOCK is not held")


if __name__ == "__main__":
    unittest.main()
