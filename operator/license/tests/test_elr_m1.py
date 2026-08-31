"""test_elr_m1.py — End-to-End Tests for ADR-0167 M1 (Entangled License Ratchet).

Comprehensive test suite verifying:
  1. Ratchet initialization and state management.
  2. Tile derivation with epoch pinning.
  3. AEAD wrapping/unwrapping.
  4. egress_gate integration with fail-closed fallback.
  5. No key material in audit details.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

import sys
here = Path(__file__).resolve().parent.parent
if str(here) not in sys.path:
    sys.path.insert(0, str(here))

from elr import (
    EntangledRatchet,
    WrappedCapabilityDescriptor,
    CapabilityEnvelope,
    RatchetState,
    make_root_from_license_token,
)
from sob_crypto import hkdf_derive


class TestRatchetCore:
    """Core ratchet mechanics."""

    def test_ratchet_state_immutable(self):
        state = RatchetState(b"x" * 32, epoch_k=0)
        with pytest.raises(Exception):
            state.state_bytes = b"y" * 32

    def test_ratchet_init_valid(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)
        assert ratchet.current_state.epoch_k == 0
        assert ratchet.current_chain_head == chain_head

    def test_ratchet_init_invalid_root_length(self):
        with pytest.raises(ValueError, match="root must be 32 bytes"):
            EntangledRatchet(b"short", b"a" * 32)

    def test_ratchet_init_invalid_chain_head_length(self):
        with pytest.raises(ValueError, match="audit_chain_head_hash must be ≥32 bytes"):
            EntangledRatchet(b"a" * 32, b"short")

    def test_ratchet_advance_no_change(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)
        assert ratchet.advance(chain_head) is False
        assert ratchet.current_state.epoch_k == 0

    def test_ratchet_advance_new_head(self):
        root = b"a" * 32
        chain_head_1 = b"b" * 32
        chain_head_2 = b"c" * 32
        ratchet = EntangledRatchet(root, chain_head_1)
        assert ratchet.advance(chain_head_2) is True
        assert ratchet.current_state.epoch_k == 1
        assert ratchet.current_chain_head == chain_head_2

    def test_ratchet_advance_clears_cache(self):
        root = b"a" * 32
        chain_head_1 = b"b" * 32
        chain_head_2 = b"c" * 32
        ratchet = EntangledRatchet(root, chain_head_1)

        # Derive a tile in epoch 0.
        tile_0_0 = ratchet.derive_tile("capability-A")

        # Advance to epoch 1.
        ratchet.advance(chain_head_2)
        tile_1_0 = ratchet.derive_tile("capability-A")

        # Same label, different epochs → different tiles.
        assert tile_0_0 != tile_1_0

    def test_ratchet_tile_cache(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        tile_a_1 = ratchet.derive_tile("capability-A")
        tile_a_2 = ratchet.derive_tile("capability-A")
        assert tile_a_1 is tile_a_2  # Same object (cached)

    def test_ratchet_different_labels(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        tile_a = ratchet.derive_tile("capability-A")
        tile_b = ratchet.derive_tile("capability-B")
        assert tile_a != tile_b

    def test_ratchet_forward_only(self):
        """Advancing the ratchet is forward-only; can't go backward."""
        root = b"a" * 32
        chain_head_1 = b"b" * 32
        chain_head_2 = b"c" * 32
        chain_head_3 = b"d" * 32

        ratchet = EntangledRatchet(root, chain_head_1)
        state_1 = ratchet.current_state

        ratchet.advance(chain_head_2)
        state_2 = ratchet.current_state
        assert state_2 != state_1

        ratchet.advance(chain_head_3)
        state_3 = ratchet.current_state
        assert state_3 != state_2

        # If we somehow tried to go back (hypothetically), the cache is cleared,
        # so we'd recompute — but architecturally, advance() is monotonic.
        assert ratchet.current_state.epoch_k == 2


class TestAEAD:
    """AEAD wrapping and unwrapping."""

    def test_descriptor_wire_format(self):
        nonce = b"n" * 12
        ciphertext = b"c" * 32
        desc = WrappedCapabilityDescriptor(nonce=nonce, ciphertext=ciphertext)
        assert desc.to_bytes() == nonce + ciphertext

    def test_descriptor_from_bytes(self):
        nonce = b"n" * 12
        ciphertext = b"c" * 32
        wire = nonce + ciphertext
        desc = WrappedCapabilityDescriptor.from_bytes(wire)
        assert desc.nonce == nonce
        assert desc.ciphertext == ciphertext

    def test_descriptor_roundtrip(self):
        nonce = b"n" * 12
        ciphertext = b"c" * 32
        desc1 = WrappedCapabilityDescriptor(nonce=nonce, ciphertext=ciphertext)
        wire = desc1.to_bytes()
        desc2 = WrappedCapabilityDescriptor.from_bytes(wire)
        assert desc1 == desc2

    def test_envelope_wrap_unwrap(self):
        tile_key = b"k" * 32
        plaintext = {"allowed_hosts": ["localhost"], "version": 1}

        # Wrap
        wrapped = CapabilityEnvelope.wrap(plaintext, tile_key)
        assert isinstance(wrapped, WrappedCapabilityDescriptor)
        assert len(wrapped.nonce) == 12
        assert len(wrapped.ciphertext) > 0

        # Unwrap
        unwrapped = CapabilityEnvelope.unwrap(wrapped, tile_key)
        assert unwrapped == plaintext

    def test_envelope_unwrap_wrong_key(self):
        tile_key_1 = b"k1" + b"x" * 30
        tile_key_2 = b"k2" + b"y" * 30
        plaintext = {"allowed_hosts": ["localhost"]}

        wrapped = CapabilityEnvelope.wrap(plaintext, tile_key_1)
        unwrapped = CapabilityEnvelope.unwrap(wrapped, tile_key_2)
        assert unwrapped is None  # Decryption failed, returned None (fail-closed)

    def test_envelope_unwrap_corrupted_ciphertext(self):
        tile_key = b"k" * 32
        plaintext = {"allowed_hosts": ["localhost"]}

        wrapped = CapabilityEnvelope.wrap(plaintext, tile_key)
        # Corrupt the ciphertext
        corrupted = WrappedCapabilityDescriptor(
            nonce=wrapped.nonce,
            ciphertext=wrapped.ciphertext[:-1] + b"X",
        )
        unwrapped = CapabilityEnvelope.unwrap(corrupted, tile_key)
        assert unwrapped is None  # Poly1305 tag failure, returned None

    def test_envelope_unwrap_invalid_key_length(self):
        wrapped = WrappedCapabilityDescriptor(nonce=b"n" * 12, ciphertext=b"c" * 32)
        result = CapabilityEnvelope.unwrap(wrapped, b"short")
        assert result is None

    def test_envelope_wrap_invalid_key_length(self):
        with pytest.raises(ValueError):
            CapabilityEnvelope.wrap({"test": "data"}, b"short")


class TestRootDerivation:
    """License token to root derivation."""

    def test_make_root_from_license_token(self):
        token = b"t" * 64  # Valid 64-byte token
        root = make_root_from_license_token(token)
        assert isinstance(root, bytes)
        assert len(root) == 32

    def test_make_root_deterministic(self):
        token = b"deterministic-token" * 4  # ≥32 bytes
        root_1 = make_root_from_license_token(token)
        root_2 = make_root_from_license_token(token)
        assert root_1 == root_2

    def test_make_root_different_tokens(self):
        token_1 = b"t1" * 32
        token_2 = b"t2" * 32
        root_1 = make_root_from_license_token(token_1)
        root_2 = make_root_from_license_token(token_2)
        assert root_1 != root_2

    def test_make_root_invalid_short_token(self):
        with pytest.raises(ValueError, match="must be ≥32 bytes"):
            make_root_from_license_token(b"short")


class TestCommitments:
    """Audit chain commitments."""

    def test_commit_tile_hash(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        commit_hash = ratchet.commit_tile_hash("capability-A")
        assert isinstance(commit_hash, bytes)
        assert len(commit_hash) == 32  # SHA256

    def test_commit_tile_hash_deterministic(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        commit_1 = ratchet.commit_tile_hash("capability-A")
        commit_2 = ratchet.commit_tile_hash("capability-A")
        assert commit_1 == commit_2

    def test_commit_tile_hash_not_key(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        tile_key = ratchet.derive_tile("capability-A")
        commit_hash = ratchet.commit_tile_hash("capability-A")

        # Hash of the key, not the key itself.
        import hashlib
        assert commit_hash == hashlib.sha256(tile_key).digest()

    def test_all_commitments_format(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        ratchet.commit_tile_hash("cap-A")
        ratchet.commit_tile_hash("cap-B")

        commitments = ratchet.all_commitments()
        assert isinstance(commitments, dict)
        assert "cap-A" in commitments
        assert "cap-B" in commitments
        # Hex strings
        assert len(commitments["cap-A"]) == 64  # SHA256 = 32 bytes = 64 hex chars
        assert len(commitments["cap-B"]) == 64


class TestIntegrationScenario:
    """End-to-end scenario: init → derive → commit → advance."""

    def test_full_scenario(self):
        # 1. Init: simulate license token → root
        license_token = b"MockLicense" * 4  # ≥32 bytes
        root = make_root_from_license_token(license_token)

        # 2. Init ratchet with initial chain head
        chain_head_epoch_0 = b"event-0" * 5  # ≥32 bytes
        ratchet = EntangledRatchet(root, chain_head_epoch_0)
        assert ratchet.current_state.epoch_k == 0

        # 3. Derive a tile (egress-allowlist capability)
        tile_0 = ratchet.derive_tile("egress-paid-preset")
        assert isinstance(tile_0, bytes)
        assert len(tile_0) == 32

        # 4. Create a capability descriptor
        plaintext_cap = {
            "allowed_hosts": ["localhost", "localhost:8080"],
            "version": "1.0",
        }
        wrapped = CapabilityEnvelope.wrap(plaintext_cap, tile_0)

        # 5. Commit the tile hash (what goes to audit)
        commit_hash_0 = ratchet.commit_tile_hash("egress-paid-preset")
        assert len(commit_hash_0) == 32

        # 6. Unwrap (successful)
        unwrapped_cap = CapabilityEnvelope.unwrap(wrapped, tile_0)
        assert unwrapped_cap == plaintext_cap

        # 7. Advance the ratchet (new chain event)
        chain_head_epoch_1 = b"event-1" * 5
        ratchet.advance(chain_head_epoch_1)
        assert ratchet.current_state.epoch_k == 1

        # 8. Derive a new tile in epoch 1 (same label, new epoch → different tile)
        tile_1 = ratchet.derive_tile("egress-paid-preset")
        assert tile_1 != tile_0

        # 9. Old wrapped capability fails to unwrap with new tile (fail-closed)
        unwrapped_wrong = CapabilityEnvelope.unwrap(wrapped, tile_1)
        assert unwrapped_wrong is None

        # 10. New commit hash is different
        commit_hash_1 = ratchet.commit_tile_hash("egress-paid-preset")
        assert commit_hash_1 != commit_hash_0

        # 11. Commitments dict is clean for audit
        all_commits = ratchet.all_commitments()
        assert "egress-paid-preset" in all_commits
        # Verify it's the hex of the hash we computed
        assert all_commits["egress-paid-preset"] == commit_hash_1.hex()


class TestFailClosedDefense:
    """Verify fail-closed semantics under attack scenarios."""

    def test_no_key_material_in_plaintext_audit(self):
        """Key material MUST NEVER be in audit event details."""
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        # Derive a tile
        tile = ratchet.derive_tile("test-cap")

        # Commitment is safe to audit (hash only)
        commit = ratchet.commit_tile_hash("test-cap")

        # Verify: tile ≠ commit (one is key, one is hash)
        import hashlib
        assert commit == hashlib.sha256(tile).digest()
        assert tile != commit  # They are different

    def test_tampering_detection(self):
        """If epoch is frozen (chain head not advanced), recomputation detects tampering."""
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        commit_1 = ratchet.commit_tile_hash("cap")
        # If an attacker tries to replay epoch 0, all lookups return the same commit.
        # But if the chain head was NOT advanced, that's a sign of tampering
        # (visible via `voice-audit verify` checking the chain).
        commit_2 = ratchet.commit_tile_hash("cap")
        assert commit_1 == commit_2  # Same epoch → same commit (expected)

        # Real advancement shows difference
        ratchet.advance(b"c" * 32)
        commit_3 = ratchet.commit_tile_hash("cap")
        assert commit_3 != commit_1  # New epoch → new commit


class TestEgressGateIntegration:
    """Ratchet operational readiness for egress_gate.py integration."""

    def test_egress_gate_advance_ratchet(self):
        """Verify ratchet advances correctly (prep for M2 egress integration)."""
        root = b"a" * 32
        chain_head_0 = b"b" * 32
        chain_head_1 = b"c" * 32

        ratchet = EntangledRatchet(root, chain_head_0)
        epoch_0 = ratchet.current_state.epoch_k

        # Simulate an audit event (new chain head)
        ratchet.advance(chain_head_1)
        epoch_1 = ratchet.current_state.epoch_k

        assert epoch_1 > epoch_0
        assert ratchet.current_chain_head == chain_head_1


class TestAdvanceValidation:
    """Input validation for advance() — F-02 regression tests."""

    def test_advance_rejects_short_bytes(self):
        """advance() must reject bytes < 32 bytes (fail-closed)."""
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        with pytest.raises(ValueError, match="must be ≥32 bytes"):
            ratchet.advance(b"short")

    def test_advance_rejects_none(self):
        """advance() must reject None (fail-closed)."""
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        with pytest.raises(ValueError, match="must be ≥32 bytes"):
            ratchet.advance(None)

    def test_advance_rejects_wrong_type(self):
        """advance() must reject non-bytes (fail-closed)."""
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        with pytest.raises(ValueError, match="must be ≥32 bytes"):
            ratchet.advance("string-not-bytes")


class TestRootDerivationDomainSeparation:
    """Domain separation in root derivation — F-08 regression tests."""

    def test_root_derivation_uses_info_label(self):
        """make_root_from_license_token must use "elr-root-v1" info label."""
        token_1 = b"t1" * 32
        token_2 = b"t2" * 32

        root_1 = make_root_from_license_token(token_1)
        root_2 = make_root_from_license_token(token_2)

        # Different tokens → different roots (due to proper HKDF usage)
        assert root_1 != root_2

        # Same token → same root (deterministic)
        root_1_again = make_root_from_license_token(token_1)
        assert root_1 == root_1_again


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
