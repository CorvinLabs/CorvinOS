"""test_elr_m3_networked.py — M3 Networked Entropy + Offline Fallback E2E.

M3 scope: optional external entropy (ADR-0103) for true forward-secrecy.
Offline ratchet always works (fail-closed, no network dependency).

Test matrix:
  1. Offline-only derivation (M1/M2 base, always works)
  2. Networked epoch (entropy provided, different tile)
  3. Offline fallback (network down → offline ratchet)
  4. Network/offline parity (same chain head, entropy present/absent → different tiles)
  5. Expiry + networked (epoch-based expiry, networked tier)
"""
from __future__ import annotations

from pathlib import Path
import pytest
import sys

here = Path(__file__).resolve().parent.parent
if str(here) not in sys.path:
    sys.path.insert(0, str(here))

from elr import EntangledRatchet, make_root_from_license_token


class TestOfflineOnly:
    """M1/M2 baseline: offline-only ratchet (no networked entropy)."""

    def test_offline_advance_no_entropy(self):
        """advance() with no entropy parameter uses offline ratchet."""
        root = b"a" * 32
        chain_head_0 = b"b" * 32
        chain_head_1 = b"c" * 32

        ratchet = EntangledRatchet(root, chain_head_0)
        assert not ratchet.is_networked_epoch

        # Advance without entropy
        ratchet.advance(chain_head_1)
        assert not ratchet.is_networked_epoch  # Still offline

    def test_offline_tile_derivation(self):
        """Offline tile derivation is deterministic."""
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        tile_1 = ratchet.derive_tile("test-cap")
        tile_2 = ratchet.derive_tile("test-cap")
        assert tile_1 == tile_2  # Deterministic


class TestNetworkedEntropy:
    """M3: networked external entropy for forward-secrecy."""

    def test_networked_entropy_provided(self):
        """advance() with entropy parameter creates networked epoch."""
        root = b"a" * 32
        chain_head_0 = b"b" * 32
        chain_head_1 = b"c" * 32
        external_entropy = b"entropy" * 8  # 56 bytes, > 32

        ratchet = EntangledRatchet(root, chain_head_0)
        ratchet.advance(chain_head_1, external_entropy=external_entropy)
        assert ratchet.is_networked_epoch

    def test_networked_vs_offline_tile(self):
        """Same chain head + label → different tiles offline vs networked."""
        root = b"a" * 32
        chain_head = b"b" * 32
        external_entropy = b"entropy" * 8

        # Offline ratchet
        ratchet_offline = EntangledRatchet(root, chain_head)
        tile_offline = ratchet_offline.derive_tile("test-cap")

        # Networked ratchet (same root, chain head, but with entropy)
        ratchet_networked = EntangledRatchet(root, chain_head)
        ratchet_networked.advance(chain_head, external_entropy=external_entropy)
        # Derive tile at epoch 1 (after advance)
        chain_head_1 = b"d" * 32
        ratchet_networked.advance(chain_head_1)  # Advance to epoch 1
        tile_networked = ratchet_networked.derive_tile("test-cap")

        # Different tiles (networked added entropy to epoch 1)
        assert tile_offline != tile_networked

    def test_networked_entropy_short_ignored(self):
        """Malformed entropy (<32 bytes) is ignored (fail-closed to offline)."""
        root = b"a" * 32
        chain_head_0 = b"b" * 32
        chain_head_1 = b"c" * 32
        short_entropy = b"short"  # < 32 bytes

        ratchet = EntangledRatchet(root, chain_head_0)
        ratchet.advance(chain_head_1, external_entropy=short_entropy)
        assert not ratchet.is_networked_epoch  # Entropy ignored, offline mode


class TestOfflineFallback:
    """M3: Network down → offline ratchet still works (no brick)."""

    def test_offline_fallback_no_network(self):
        """Ratchet works even if network is never available."""
        root = b"a" * 32
        chain_heads = [b"h" + bytes([i]) * 31 for i in range(1, 5)]  # 4 chain heads

        ratchet = EntangledRatchet(root, chain_heads[0])

        # Advance 4 times, never providing entropy (network always down)
        for chain_head in chain_heads[1:]:
            ratchet.advance(chain_head, external_entropy=None)

        # Ratchet is still operational
        assert ratchet.current_state.epoch_k == 3
        tile = ratchet.derive_tile("test-cap")
        assert isinstance(tile, bytes)
        assert len(tile) == 32

    def test_offline_fallback_network_intermittent(self):
        """Entropy provided sometimes, not always → graceful degrade."""
        root = b"a" * 32
        chain_heads = [b"h" + bytes([i]) * 31 for i in range(1, 6)]

        ratchet = EntangledRatchet(root, chain_heads[0])
        entropy = b"ent" * 20  # 60 bytes, valid

        # Epoch 0: offline
        ratchet.advance(chain_heads[1], external_entropy=None)
        assert not ratchet.is_networked_epoch

        # Epoch 1: networked
        ratchet.advance(chain_heads[2], external_entropy=entropy)
        assert ratchet.is_networked_epoch

        # Epoch 2: network down again
        ratchet.advance(chain_heads[3], external_entropy=None)
        assert not ratchet.is_networked_epoch

        # Epoch 3: networked again
        ratchet.advance(chain_heads[4], external_entropy=entropy)
        assert ratchet.is_networked_epoch

        # Ratchet handles all transitions gracefully
        assert ratchet.current_state.epoch_k == 4


class TestNetworkedIntegration:
    """M3: Full end-to-end with ADR-0103 membership credential."""

    def test_m3_e2e_with_membership(self):
        """Simulate ADR-0103 membership entropy binding."""
        from elr_capabilities_m2 import A2ASesTokenCapability, create_capability_from_dict
        from elr import CapabilityEnvelope

        root = b"a" * 32
        chain_head_0 = b"b" * 32
        chain_head_1 = b"c" * 32

        # Create ratchet
        ratchet = EntangledRatchet(root, chain_head_0)

        # Simulate ADR-0103 membership nonce as external entropy
        membership_entropy = b"membership-nonce" * 2  # 32 bytes

        # Advance with membership entropy
        ratchet.advance(chain_head_1, external_entropy=membership_entropy)

        # Derive A2A capability
        a2a_cap = A2ASesTokenCapability(
            membership_nonce="derived-from-entropy",
            network_id="corvin-prod",
        )

        # Wrap with networked tile
        tile_networked = ratchet.derive_tile("a2a-sestoken")
        wrapped = CapabilityEnvelope.wrap(a2a_cap.to_dict(), tile_networked)

        # Unwrap (simulating remote peer)
        plaintext = CapabilityEnvelope.unwrap(wrapped, tile_networked)
        assert plaintext is not None

        cap_unwrapped = create_capability_from_dict("a2a-sestoken", plaintext)
        assert cap_unwrapped.network_id == "corvin-prod"


class TestFailClosedNetworked:
    """M3: Fail-closed semantics when network entropy is unavailable."""

    def test_no_key_leak_offline_fallback(self):
        """Offline fallback doesn't leak key material (M1/M2 baseline)."""
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        # Offline tile
        tile = ratchet.derive_tile("test-cap")
        commit = ratchet.commit_tile_hash("test-cap")

        # Commitment is hash(tile), not tile itself
        import hashlib
        assert commit == hashlib.sha256(tile).digest()
        assert tile != commit

    def test_network_down_no_crash(self):
        """Network unavailable doesn't crash, ratchet continues offline."""
        root = b"a" * 32
        chain_heads = [b"h" + bytes([i]) * 31 for i in range(1, 5)]

        ratchet = EntangledRatchet(root, chain_heads[0])

        try:
            for ch in chain_heads[1:]:
                # Simulating network request that returns None (unavailable)
                entropy = None  # "Network returned None"
                ratchet.advance(ch, external_entropy=entropy)
            # Success: ratchet continues
            assert ratchet.current_state.epoch_k == 3
        except Exception as e:
            pytest.fail(f"Network down should not crash ratchet: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
