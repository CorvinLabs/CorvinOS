"""Tests for Layer 41 Social Capability Grants (ADR-0054).

Run standalone: python3 shared/test_social_capability.py

Uses unittest (not pytest) to match the rest of the test suite.
SQLite databases and audit files are created in temporary directories
so tests are fully isolated.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap so tests run standalone without installing the package
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Redirect audit writes to a temp file so tests never touch production chains
_AUDIT_TMP = tempfile.mktemp(suffix=".audit.jsonl")
os.environ.setdefault("VOICE_AUDIT_PATH", _AUDIT_TMP)
# Also keep FORGE_ROOT isolated so audit_path() doesn't wander
os.environ.setdefault("FORGE_ROOT", tempfile.mkdtemp())

from social_capability import (  # noqa: E402
    CapabilityGrant,
    GrantChecker,
    GrantError,
    GrantStore,
    L41GrantHandler,
    _capability_matches,
    sign_grant,
    verify_grant_signature,
)

try:
    from social_envelope import generate_keypair  # noqa: E402
except ImportError:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    def generate_keypair() -> tuple[str, str]:
        priv = Ed25519PrivateKey.generate()
        pub = priv.public_key()
        private_hex = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
        public_hex = pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        return private_hex, public_hex


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_dir: str) -> GrantStore:
    db_path = Path(tmp_dir) / "grants.db"
    return GrantStore(db_path=db_path)


def _make_grant(
    grantor: str = "@local@corvin.sh",
    grantee: str = "@alice@remote.example",
    capabilities: list[str] | None = None,
    valid_until: float | None = None,
    rate_limit: str | None = None,
    data_class_ceiling: str | None = None,
) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="",
        schema_version=1,
        grantor_actor=grantor,
        grantee_actor=grantee,
        capabilities=capabilities or ["domain.research.read"],
        issued_at=time.time(),
        revoked_at=None,
        valid_until=valid_until,
        rate_limit=rate_limit,
        data_class_ceiling=data_class_ceiling,
        signature="",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGrantIssuanceAndStorage(unittest.TestCase):
    """Test 1: Grant issuance and storage."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, self.public_key = generate_keypair()

    def tearDown(self) -> None:
        self.store.close()

    def test_issue_creates_grant_with_id_and_signature(self) -> None:
        grant = _make_grant()
        issued = self.store.issue(grant, self.private_key)
        self.assertTrue(issued.grant_id.startswith("grnt_"))
        self.assertIsNotNone(issued.signature)
        self.assertGreater(len(issued.signature), 0)

    def test_issued_grant_is_retrievable(self) -> None:
        grant = _make_grant()
        issued = self.store.issue(grant, self.private_key)
        fetched = self.store.get(issued.grant_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.grant_id, issued.grant_id)
        self.assertEqual(fetched.capabilities, ["domain.research.read"])
        self.assertIsNone(fetched.revoked_at)

    def test_signature_is_valid_after_issue(self) -> None:
        grant = _make_grant()
        issued = self.store.issue(grant, self.private_key)
        self.assertTrue(verify_grant_signature(issued, self.public_key))

    def test_list_for_grantee_returns_issued_grant(self) -> None:
        grant = _make_grant(grantee="@bob@example.com")
        issued = self.store.issue(grant, self.private_key)
        grants = self.store.list_for_grantee("@bob@example.com")
        ids = [g.grant_id for g in grants]
        self.assertIn(issued.grant_id, ids)

    def test_get_nonexistent_grant_returns_none(self) -> None:
        self.assertIsNone(self.store.get("grnt_nonexistent12"))


class TestGrantCheckerHappyPath(unittest.TestCase):
    """Test 2: GrantChecker.check() — happy path (follower with valid grant)."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, self.public_key = generate_keypair()
        self.grantee = "@alice@remote.example"

        grant = _make_grant(grantee=self.grantee, capabilities=["domain.research.read"])
        self.store.issue(grant, self.private_key)

        self.checker = GrantChecker(
            grant_store=self.store,
            follower_check_fn=lambda actor: actor == self.grantee,
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_follower_with_matching_capability_is_allowed(self) -> None:
        self.assertTrue(
            self.checker.check(self.grantee, "domain.research.read")
        )

    def test_follower_without_matching_capability_is_denied(self) -> None:
        self.assertFalse(
            self.checker.check(self.grantee, "agent.invoke.assistant")
        )


class TestGrantCheckerNotFollower(unittest.TestCase):
    """Test 3: GrantChecker.check() — deny when not a follower."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, _ = generate_keypair()
        self.grantee = "@eve@attacker.example"

        grant = _make_grant(grantee=self.grantee, capabilities=["domain.research.read"])
        self.store.issue(grant, self.private_key)

        # follower_check_fn always returns False — actor is NOT a follower
        self.checker = GrantChecker(
            grant_store=self.store,
            follower_check_fn=lambda actor: False,
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_non_follower_with_grant_is_denied(self) -> None:
        self.assertFalse(
            self.checker.check(self.grantee, "domain.research.read")
        )


class TestGrantCheckerExpired(unittest.TestCase):
    """Test 4: GrantChecker.check() — deny when grant expired (valid_until in past)."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, _ = generate_keypair()
        self.grantee = "@alice@remote.example"

        # valid_until is 60 seconds in the past
        grant = _make_grant(
            grantee=self.grantee,
            capabilities=["domain.research.read"],
            valid_until=time.time() - 60,
        )
        self.store.issue(grant, self.private_key)

        self.checker = GrantChecker(
            grant_store=self.store,
            follower_check_fn=lambda actor: actor == self.grantee,
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_expired_grant_is_denied(self) -> None:
        self.assertFalse(
            self.checker.check(self.grantee, "domain.research.read")
        )

    def test_non_expired_grant_is_allowed(self) -> None:
        # Issue a second grant that is not expired
        grant2 = _make_grant(
            grantee=self.grantee,
            capabilities=["domain.research.read"],
            valid_until=time.time() + 3600,
        )
        self.store.issue(grant2, self.private_key)
        self.assertTrue(
            self.checker.check(self.grantee, "domain.research.read")
        )


class TestGrantCheckerRevoked(unittest.TestCase):
    """Test 5: GrantChecker.check() — deny when revoked."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, _ = generate_keypair()
        self.grantee = "@alice@remote.example"

        grant = _make_grant(grantee=self.grantee, capabilities=["domain.research.read"])
        issued = self.store.issue(grant, self.private_key)
        self.grant_id = issued.grant_id

        self.checker = GrantChecker(
            grant_store=self.store,
            follower_check_fn=lambda actor: actor == self.grantee,
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_active_grant_is_allowed(self) -> None:
        self.assertTrue(self.checker.check(self.grantee, "domain.research.read"))

    def test_revoked_grant_is_denied(self) -> None:
        self.store.revoke(self.grant_id)
        self.assertFalse(self.checker.check(self.grantee, "domain.research.read"))

    def test_revoked_at_is_set_after_revoke(self) -> None:
        self.store.revoke(self.grant_id)
        fetched = self.store.get(self.grant_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertIsNotNone(fetched.revoked_at)


class TestGrantCheckerWildcardGrantFollower(unittest.TestCase):
    """Test 6: Wildcard '*' grant applies to any follower."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, _ = generate_keypair()

        # Issue a wildcard grant
        grant = _make_grant(grantee="*", capabilities=["domain.open-source.read"])
        self.store.issue(grant, self.private_key)

        # follower_check_fn: accept any actor ending with "@remote.example"
        self.checker = GrantChecker(
            grant_store=self.store,
            follower_check_fn=lambda actor: actor.endswith("@remote.example"),
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_wildcard_grant_applies_to_follower(self) -> None:
        self.assertTrue(
            self.checker.check("@alice@remote.example", "domain.open-source.read")
        )

    def test_wildcard_grant_applies_to_different_follower(self) -> None:
        self.assertTrue(
            self.checker.check("@bob@remote.example", "domain.open-source.read")
        )


class TestGrantCheckerWildcardGrantNonFollower(unittest.TestCase):
    """Test 7: Wildcard '*' grant does NOT apply to non-followers."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, _ = generate_keypair()

        # Issue a wildcard grant
        grant = _make_grant(grantee="*", capabilities=["domain.open-source.read"])
        self.store.issue(grant, self.private_key)

        # follower_check_fn: only @alice is a follower
        self.checker = GrantChecker(
            grant_store=self.store,
            follower_check_fn=lambda actor: actor == "@alice@remote.example",
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_wildcard_grant_does_not_apply_to_non_follower(self) -> None:
        self.assertFalse(
            self.checker.check("@eve@attacker.example", "domain.open-source.read")
        )

    def test_wildcard_grant_does_apply_to_follower(self) -> None:
        self.assertTrue(
            self.checker.check("@alice@remote.example", "domain.open-source.read")
        )


class TestRateLimitEnforcement(unittest.TestCase):
    """Test 8: Rate limit enforcement (N/day)."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, _ = generate_keypair()
        self.grantee = "@alice@remote.example"

        # Grant with a limit of 3/day
        grant = _make_grant(
            grantee=self.grantee,
            capabilities=["agent.invoke.assistant"],
            rate_limit="3/day",
        )
        issued = self.store.issue(grant, self.private_key)
        self.grant_id = issued.grant_id

        self.checker = GrantChecker(
            grant_store=self.store,
            follower_check_fn=lambda actor: actor == self.grantee,
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_rate_limit_allows_up_to_n_calls(self) -> None:
        for i in range(3):
            result = self.checker.check(self.grantee, "agent.invoke.assistant")
            self.assertTrue(result, f"call {i+1} should succeed")

    def test_rate_limit_denies_after_n_calls(self) -> None:
        for _ in range(3):
            self.checker.check(self.grantee, "agent.invoke.assistant")
        # 4th call should fail
        self.assertFalse(
            self.checker.check(self.grantee, "agent.invoke.assistant")
        )

    def test_rate_limit_counter_tracks_correctly(self) -> None:
        # Direct store check
        self.assertTrue(
            self.store.check_and_increment_rate_limit(self.grant_id, "2/day")
        )
        self.assertTrue(
            self.store.check_and_increment_rate_limit(self.grant_id, "2/day")
        )
        self.assertFalse(
            self.store.check_and_increment_rate_limit(self.grant_id, "2/day")
        )


class TestRevokeAllForActor(unittest.TestCase):
    """Test 9: Revoke all grants for an actor."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, _ = generate_keypair()
        self.grantee = "@alice@remote.example"

    def tearDown(self) -> None:
        self.store.close()

    def test_revoke_all_revokes_multiple_grants(self) -> None:
        g1 = _make_grant(grantee=self.grantee, capabilities=["domain.research.read"])
        g2 = _make_grant(grantee=self.grantee, capabilities=["agent.invoke.assistant"])
        self.store.issue(g1, self.private_key)
        self.store.issue(g2, self.private_key)

        count = self.store.revoke_all_for_actor(self.grantee)
        self.assertEqual(count, 2)

        # All grants should be revoked
        all_grants = self.store.list_for_grantee(self.grantee)
        for g in all_grants:
            self.assertIsNotNone(g.revoked_at)

    def test_revoke_all_returns_zero_when_no_grants(self) -> None:
        count = self.store.revoke_all_for_actor("@unknown@example.com")
        self.assertEqual(count, 0)

    def test_revoke_all_does_not_touch_other_actor_grants(self) -> None:
        other = "@bob@remote.example"
        g_alice = _make_grant(grantee=self.grantee, capabilities=["domain.research.read"])
        g_bob = _make_grant(grantee=other, capabilities=["domain.research.read"])
        self.store.issue(g_alice, self.private_key)
        issued_bob = self.store.issue(g_bob, self.private_key)

        self.store.revoke_all_for_actor(self.grantee)

        bob_grants = self.store.list_for_grantee(other)
        active_bob = [g for g in bob_grants if g.revoked_at is None]
        self.assertEqual(len(active_bob), 1)
        self.assertEqual(active_bob[0].grant_id, issued_bob.grant_id)


class TestL36ErasureHandler(unittest.TestCase):
    """Test 10: L36 handler erasure removes grants for subject."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = _make_store(self.tmp)
        self.private_key, _ = generate_keypair()
        self.subject_prefix = "subject_42"
        self.grantee = f"{self.subject_prefix}@remote.example"

    def tearDown(self) -> None:
        self.store.close()

    def test_erasure_revokes_grants_for_subject(self) -> None:
        g1 = _make_grant(grantee=self.grantee, capabilities=["domain.research.read"])
        g2 = _make_grant(grantee=self.grantee, capabilities=["agent.invoke.assistant"])
        self.store.issue(g1, self.private_key)
        self.store.issue(g2, self.private_key)

        handler = L41GrantHandler(db_path=Path(self.tmp) / "grants.db")
        result = handler.purge(self.grantee, "er-test001")

        # Result must report APPLIED
        self.assertEqual(result.status.value, "applied")
        self.assertEqual(result.count, 2)

        # Verify grants are revoked in store
        grants = self.store.list_for_grantee(self.grantee)
        for g in grants:
            self.assertIsNotNone(g.revoked_at)

    def test_erasure_skipped_when_no_grants(self) -> None:
        handler = L41GrantHandler(db_path=Path(self.tmp) / "grants.db")
        result = handler.purge("unknown_subject", "er-test002")
        self.assertEqual(result.status.value, "skipped")

    def test_erasure_does_not_touch_other_actors(self) -> None:
        other = "@carol@other.example"
        g_subject = _make_grant(grantee=self.grantee, capabilities=["domain.research.read"])
        g_other = _make_grant(grantee=other, capabilities=["domain.research.read"])
        self.store.issue(g_subject, self.private_key)
        issued_other = self.store.issue(g_other, self.private_key)

        handler = L41GrantHandler(db_path=Path(self.tmp) / "grants.db")
        handler.purge(self.grantee, "er-test003")

        # Other actor's grant must still be active
        other_grants = self.store.list_for_grantee(other)
        active = [g for g in other_grants if g.revoked_at is None]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].grant_id, issued_other.grant_id)


class TestCapabilityMatching(unittest.TestCase):
    """Unit tests for the capability matching logic."""

    def test_exact_match(self) -> None:
        self.assertTrue(_capability_matches("domain.research.read", ["domain.research.read"]))

    def test_no_match(self) -> None:
        self.assertFalse(_capability_matches("agent.invoke.*", ["domain.research.read"]))

    def test_global_wildcard_matches_anything(self) -> None:
        self.assertTrue(_capability_matches("forge.exec.my_tool", ["*"]))

    def test_prefix_wildcard_matches_subpath(self) -> None:
        self.assertTrue(_capability_matches("domain.research.read", ["domain.*"]))

    def test_prefix_wildcard_matches_exact_prefix(self) -> None:
        self.assertTrue(_capability_matches("domain", ["domain.*"]))

    def test_prefix_wildcard_does_not_match_unrelated(self) -> None:
        self.assertFalse(_capability_matches("agent.invoke.assistant", ["domain.*"]))

    def test_empty_granted_list_denies(self) -> None:
        self.assertFalse(_capability_matches("domain.research.read", []))


class TestSignatureVerification(unittest.TestCase):
    """Unit tests for Ed25519 sign / verify."""

    def test_sign_and_verify_round_trip(self) -> None:
        private_key, public_key = generate_keypair()
        grant = _make_grant()
        grant.signature = sign_grant(grant, private_key)
        self.assertTrue(verify_grant_signature(grant, public_key))

    def test_tampered_grant_fails_verification(self) -> None:
        private_key, public_key = generate_keypair()
        grant = _make_grant()
        grant.signature = sign_grant(grant, private_key)
        # Tamper: change capabilities
        grant.capabilities = ["forge.exec.*"]
        self.assertFalse(verify_grant_signature(grant, public_key))

    def test_wrong_key_fails_verification(self) -> None:
        private_key, _ = generate_keypair()
        _, other_public = generate_keypair()
        grant = _make_grant()
        grant.signature = sign_grant(grant, private_key)
        self.assertFalse(verify_grant_signature(grant, other_public))

    def test_signature_check_in_checker(self) -> None:
        tmp = tempfile.mkdtemp()
        store = _make_store(tmp)
        private_key, public_key = generate_keypair()
        grantee = "@alice@remote.example"

        grant = _make_grant(grantee=grantee, capabilities=["domain.research.read"])
        store.issue(grant, private_key)

        checker = GrantChecker(
            grant_store=store,
            follower_check_fn=lambda actor: actor == grantee,
        )

        # Valid key → allowed
        self.assertTrue(
            checker.check(grantee, "domain.research.read", public_key_hex=public_key)
        )

        # Wrong key → signature invalid → denied
        _, wrong_public = generate_keypair()
        self.assertFalse(
            checker.check(grantee, "domain.research.read", public_key_hex=wrong_public)
        )
        store.close()


class TestGrantStoreIsolation(unittest.TestCase):
    """Regression: two separate GrantStore instances against same DB path
    share state correctly (WAL mode)."""

    def test_two_stores_see_same_data(self) -> None:
        tmp = tempfile.mkdtemp()
        db_path = Path(tmp) / "grants.db"
        private_key, _ = generate_keypair()

        store_a = GrantStore(db_path=db_path)
        store_b = GrantStore(db_path=db_path)

        grant = _make_grant()
        issued = store_a.issue(grant, private_key)

        fetched = store_b.get(issued.grant_id)
        self.assertIsNotNone(fetched)

        store_a.close()
        store_b.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
