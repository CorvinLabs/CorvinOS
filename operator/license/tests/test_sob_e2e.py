"""End-to-end tests for the Sealed Offline Bundle system (ADR-0111).

Coverage:
  * Happy path: register → load → verify feature config + limits
  * Day-offset window: days 0, 15, 29 → valid; day 30, -1 → expired
  * Tampered ciphertext → None
  * Wrong instance_id → None
  * Wrong device_fp → None
  * Wrong server signing key → None
  * SOB file permissions too permissive → None
  * Capability.get_feature_config() → dict or None
  * Capability.get_limit() → numeric / None
  * Capability.assert_limit() → raises on exceed
  * Free tier fallback when no SOB present
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

# Insert operator/ into sys.path so that `license` is importable without
# conflicting with Python's built-in `operator` module.
_OPERATOR_DIR = Path(__file__).resolve().parents[2]  # operator/
if str(_OPERATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR_DIR))

from license.sob_crypto import (   # noqa: E402
    generate_ed25519_keypair,
    generate_x25519_keypair,
    unseal_sob,
)
from license.sob_issuer import SobIssuer, register_local   # noqa: E402
from license import _corvin_seal_stub as _stub             # noqa: E402
from license.sob import SobClient                          # noqa: E402
from license.capability import Capability                  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def corvin_home(tmp_path: Path) -> Path:
    """Isolated corvin_home for each test."""
    home = tmp_path / ".corvin"
    home.mkdir()
    (home / "global").mkdir()
    (home / "global" / "license").mkdir()
    # instance_id.json
    iid_path = home / "global" / "instance_id.json"
    iid_path.write_text(json.dumps({"instance_id": "test-instance-uuid-1234"}))
    os.chmod(iid_path, 0o600)
    return home


@pytest.fixture()
def issuer() -> SobIssuer:
    """Fresh SobIssuer with a generated server keypair."""
    return SobIssuer()


@pytest.fixture()
def registration(corvin_home: Path, issuer: SobIssuer) -> tuple[Path, SobIssuer]:
    """Register the test instance and configure the stub's verify key + private key."""
    sob_bytes, verify_key_raw = register_local(
        corvin_home,
        instance_id="test-instance-uuid-1234",
        device_fp=_test_device_fp(),
        tier="member",
        issuer=issuer,
    )
    _stub._TEST_SERVER_VERIFY_KEY_RAW = verify_key_raw
    # Also inject the sub-private key so the stub can unseal without touching ~/.corvin
    _stub._TEST_SUB_PRIVATE_KEY_RAW = (
        corvin_home / "global" / "license" / "sub_private.key"
    ).read_bytes()
    yield corvin_home, issuer
    # Cleanup
    _stub._TEST_SERVER_VERIFY_KEY_RAW = None
    _stub._TEST_SUB_PRIVATE_KEY_RAW = None


def _test_device_fp() -> str:
    """Compute the same device_fp SobClient._device_fp() would return.

    Must mirror the production formula exactly — delegate to the canonical
    compute_device_fp(). The old hand-rolled sha256("{hostname}:{mac}") diverged
    from the unified FND-LIC-07 formula (which adds machine_id and double-hashes),
    so SOBs sealed here no longer unsealed → every paid-tier test fell back to
    Free. (NOTE: field SOBs sealed with the pre-FND-LIC-07 formula will likewise
    stop unsealing — a re-seal/migration path is still owed; see review notes.)
    """
    from license.device_fp import compute_device_fp  # type: ignore
    return compute_device_fp()


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_sob_client(corvin_home: Path) -> SobClient:
    """Create and load a SobClient pointing at our isolated corvin_home."""
    client = SobClient(corvin_home)
    # Patch _instance_id to return the test value
    client._device_fp_override = _test_device_fp()
    return client


# ── Happy path ────────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_register_and_load(self, registration):
        home, _ = registration
        client = SobClient(home)
        # Monkey-patch _instance_id resolution to match our fixture
        ok = client.load()
        assert ok
        claims = client.get_claims()
        assert claims is not None
        assert claims["tier"] == "member"
        assert claims["instance_id"] == "test-instance-uuid-1234"

    def test_feature_config_returned(self, registration):
        home, _ = registration
        client = SobClient(home)
        client.load()
        cap = Capability(client)
        cfg = cap.get_feature_config("data_residency")
        assert cfg is not None
        assert "zones" in cfg
        assert "strict" in cfg

    def test_feature_config_missing_for_free_tier_key(self, registration):
        home, _ = registration
        client = SobClient(home)
        client.load()
        cap = Capability(client)
        # "non_existent_feature" is not in the issued features dict
        assert cap.get_feature_config("non_existent_feature") is None

    def test_limit_none_means_unlimited(self, registration):
        home, _ = registration
        client = SobClient(home)
        client.load()
        cap = Capability(client)
        # member tier has None for most limits → unlimited
        assert cap.get_limit("tenants_max") is None
        assert cap.get_limit("workflows_concurrent") is None

    def test_assert_limit_unlimited_never_raises(self, registration):
        home, _ = registration
        client = SobClient(home)
        client.load()
        cap = Capability(client)
        # Should not raise for any requested value when limit is None
        cap.assert_limit("tenants_max", 99999)

    def test_active_tier(self, registration):
        home, _ = registration
        client = SobClient(home)
        client.load()
        assert client.active_tier() == "member"

    def test_is_registered(self, registration):
        home, _ = registration
        client = SobClient(home)
        assert client.is_registered()


# ── Day-offset window ─────────────────────────────────────────────────────────

class TestNonceEpochFailClosed:
    """R2-08 (review 2026-06-17): a stale manifest must NOT reset the SOB nonce
    epoch to 0 (the fail-OPEN that kept the 7-day offline replay window open)."""

    def test_stale_manifest_keeps_last_known_epoch(self, monkeypatch):
        import license.manifest as _m
        from license import sob as _sob
        # A stale manifest still carries the last-known epoch; enforcement must
        # keep using it (it can only LAG the true epoch, never drop to 0).
        monkeypatch.setattr(_m, "load_cached_manifest", lambda home: {"nonce_epoch": 5})
        assert _sob._current_nonce_epoch(Path("/nonexistent")) == 5

    def test_absent_manifest_yields_zero(self, monkeypatch):
        import license.manifest as _m
        from license import sob as _sob
        monkeypatch.setattr(_m, "load_cached_manifest", lambda home: None)
        assert _sob._current_nonce_epoch(Path("/nonexistent")) == 0


class TestDayWindow:
    def _issue_with_time(self, home, issuer, now_offset_days):
        """Issue a fresh SOB and try to unseal it at ``now + offset``."""
        from license.sob_crypto import generate_x25519_keypair
        sub_priv, sub_pub = generate_x25519_keypair()

        valid_from = time.time()
        sob_bytes = issuer.issue(
            instance_id="test-instance-uuid-1234",
            device_fp=_test_device_fp(),
            client_pub_raw=sub_pub,
            tier="member",
            nonce_count=30,
            valid_from=valid_from,
        )

        # Save sub_private.key
        key_path = home / "global" / "license" / "sub_private.key"
        key_path.write_bytes(sub_priv)
        os.chmod(key_path, 0o600)

        fake_now = valid_from + now_offset_days * 86400
        return unseal_sob(
            sob_bytes,
            sub_private_key_raw=sub_priv,
            server_verify_key_raw=issuer.verify_key_raw,
            instance_id="test-instance-uuid-1234",
            device_fp=_test_device_fp(),
            manifest_nonce_epoch=0,
            now=fake_now,
        )

    def test_day_0_valid(self, corvin_home, issuer):
        assert self._issue_with_time(corvin_home, issuer, 0) is not None

    def test_day_15_valid(self, corvin_home, issuer):
        assert self._issue_with_time(corvin_home, issuer, 15) is not None

    def test_day_29_valid(self, corvin_home, issuer):
        assert self._issue_with_time(corvin_home, issuer, 29) is not None

    def test_day_30_expired(self, corvin_home, issuer):
        assert self._issue_with_time(corvin_home, issuer, 30) is None

    def test_day_minus1_future_start(self, corvin_home, issuer):
        """SOB not yet valid (day_offset = -1)."""
        assert self._issue_with_time(corvin_home, issuer, -1) is None

    def test_valid_until_enforced_before_nonce_window_end(self, corvin_home, issuer):
        """SOB-CRYPTO-02: a signed valid_until shorter than the nonce window is
        enforced. Unseal at day 15 with valid_until=day 7 must REJECT, even though
        day_offset (15) < nonce_count (30) — the absolute expiry is not cosmetic."""
        from license.sob_crypto import generate_x25519_keypair
        sub_priv, sub_pub = generate_x25519_keypair()
        valid_from = time.time()
        sob_bytes = issuer.issue(
            instance_id="test-instance-uuid-1234",
            device_fp=_test_device_fp(),
            client_pub_raw=sub_pub,
            tier="member",
            nonce_count=30,
            valid_from=valid_from,
            valid_until=valid_from + 7 * 86400,   # absolute expiry inside the nonce window
        )
        key_path = corvin_home / "global" / "license" / "sub_private.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(sub_priv)
        os.chmod(key_path, 0o600)
        result = unseal_sob(
            sob_bytes,
            sub_private_key_raw=sub_priv,
            server_verify_key_raw=issuer.verify_key_raw,
            instance_id="test-instance-uuid-1234",
            device_fp=_test_device_fp(),
            manifest_nonce_epoch=0,
            now=valid_from + 15 * 86400,   # past valid_until (day 7), inside nonce window
        )
        assert result is None, "SOB past its signed valid_until must be rejected (SOB-CRYPTO-02)"


# ── Tamper resistance ─────────────────────────────────────────────────────────

class TestTamperResistance:
    def _issue_direct(self, issuer, instance_id, device_fp):
        _, sub_pub = generate_x25519_keypair()
        sob_bytes = issuer.issue(
            instance_id=instance_id,
            device_fp=device_fp,
            client_pub_raw=sub_pub,
            tier="member",
        )
        return sob_bytes, sub_pub

    def test_tampered_ciphertext_rejected(self, issuer):
        sub_priv, sub_pub = generate_x25519_keypair()
        sob_bytes = issuer.issue(
            instance_id="iid-1", device_fp="d" * 32, client_pub_raw=sub_pub,
        )
        # Flip a byte deep in the ciphertext
        tampered = bytearray(sob_bytes)
        tampered[-5] ^= 0xFF
        result = unseal_sob(
            bytes(tampered),
            sub_private_key_raw=sub_priv,
            server_verify_key_raw=issuer.verify_key_raw,
            instance_id="iid-1",
            device_fp="d" * 32,
            manifest_nonce_epoch=0,
        )
        assert result is None

    def test_wrong_instance_id_rejected(self, issuer):
        sub_priv, sub_pub = generate_x25519_keypair()
        sob_bytes = issuer.issue(
            instance_id="correct-iid", device_fp="d" * 32, client_pub_raw=sub_pub,
        )
        result = unseal_sob(
            sob_bytes,
            sub_private_key_raw=sub_priv,
            server_verify_key_raw=issuer.verify_key_raw,
            instance_id="wrong-iid",   # mismatch
            device_fp="d" * 32,
            manifest_nonce_epoch=0,
        )
        assert result is None

    def test_wrong_device_fp_rejected(self, issuer):
        sub_priv, sub_pub = generate_x25519_keypair()
        sob_bytes = issuer.issue(
            instance_id="iid", device_fp="correct" + "x" * 26, client_pub_raw=sub_pub,
        )
        result = unseal_sob(
            sob_bytes,
            sub_private_key_raw=sub_priv,
            server_verify_key_raw=issuer.verify_key_raw,
            instance_id="iid",
            device_fp="wrong" + "y" * 27,   # mismatch
            manifest_nonce_epoch=0,
        )
        assert result is None

    def test_wrong_server_key_rejected(self, issuer):
        sub_priv, sub_pub = generate_x25519_keypair()
        sob_bytes = issuer.issue(
            instance_id="iid", device_fp="d" * 32, client_pub_raw=sub_pub,
        )
        _, attacker_verify_key = generate_ed25519_keypair()
        result = unseal_sob(
            sob_bytes,
            sub_private_key_raw=sub_priv,
            server_verify_key_raw=attacker_verify_key,  # attacker's key
            instance_id="iid",
            device_fp="d" * 32,
            manifest_nonce_epoch=0,
        )
        assert result is None

    def test_truncated_sob_rejected(self, issuer):
        sub_priv, sub_pub = generate_x25519_keypair()
        sob_bytes = issuer.issue(
            instance_id="iid", device_fp="d" * 32, client_pub_raw=sub_pub,
        )
        result = unseal_sob(
            sob_bytes[:20],  # far too short
            sub_private_key_raw=sub_priv,
            server_verify_key_raw=issuer.verify_key_raw,
            instance_id="iid",
            device_fp="d" * 32,
            manifest_nonce_epoch=0,
        )
        assert result is None


# ── File-mode check ───────────────────────────────────────────────────────────

class TestFileMode:
    def test_world_readable_sob_rejected(self, registration):
        home, _ = registration
        sob_path = home / "global" / "license" / "sob.enc"
        os.chmod(sob_path, 0o644)  # world-readable — must be rejected

        client = SobClient(home)
        ok = client.load()
        assert not ok
        assert client.get_claims() is None

    def test_world_readable_key_rejected(self, registration):
        home, _ = registration
        key_path = home / "global" / "license" / "sub_private.key"
        os.chmod(key_path, 0o644)

        client = SobClient(home)
        ok = client.load()
        assert not ok


# ── Free tier fallback ────────────────────────────────────────────────────────

class TestFreeTier:
    def test_no_sob_gives_free_defaults(self, corvin_home):
        """No sob.enc present → Capability returns Free tier limits."""
        client = SobClient(corvin_home)
        ok = client.load()
        assert not ok

        cap = Capability(client)
        assert cap.active_tier() == "free"
        assert cap.get_feature_config("data_residency") is None
        assert cap.get_limit("compute_units_per_day") == 10
        assert cap.get_limit("tenants_max") == 1

    def test_no_sob_assert_limit_raises_on_exceed(self, corvin_home):
        from license.limits import LicenseLimitError
        client = SobClient(corvin_home)
        client.load()
        cap = Capability(client)
        with pytest.raises(LicenseLimitError):
            cap.assert_limit("tenants_max", 5)  # Free limit is 1


# ── Nonce epoch gate ──────────────────────────────────────────────────────────

class TestNonceEpoch:
    def test_old_epoch_rejected(self, issuer):
        """SOB with nonce_epoch=1 rejected when manifest says epoch=3."""
        sub_priv, sub_pub = generate_x25519_keypair()
        sob_bytes = issuer.issue(
            instance_id="iid", device_fp="d" * 32, client_pub_raw=sub_pub,
            nonce_epoch=1,
        )
        result = unseal_sob(
            sob_bytes,
            sub_private_key_raw=sub_priv,
            server_verify_key_raw=issuer.verify_key_raw,
            instance_id="iid",
            device_fp="d" * 32,
            manifest_nonce_epoch=3,  # > SOB's epoch
        )
        assert result is None

    def test_matching_epoch_accepted(self, issuer):
        sub_priv, sub_pub = generate_x25519_keypair()
        sob_bytes = issuer.issue(
            instance_id="iid", device_fp="d" * 32, client_pub_raw=sub_pub,
            nonce_epoch=3,
        )
        result = unseal_sob(
            sob_bytes,
            sub_private_key_raw=sub_priv,
            server_verify_key_raw=issuer.verify_key_raw,
            instance_id="iid",
            device_fp="d" * 32,
            manifest_nonce_epoch=3,
        )
        assert result is not None
