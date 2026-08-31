"""elr.py — Entangled License Ratchet (ADR-0167 M1).

Offline-first ratchet seeded by the signed license token, advanced by the
audit chain head. Derives non-precomputable keys for entangled capabilities:
currently, the egress-allowlist signature of paid EU_PRODUCTION presets.

Mechanism:
  root         = HKDF-Extract( signed_license_token )
  epoch_input  = audit_chain_head_hash
  tile_k       = HKDF-Expand( ratchet_state_k, epoch_input, capability_label )
  capability   = AEAD-Unwrap( wrapped_descriptor, key = tile_k )
  ratchet_state_{k+1} = HKDF-Expand( ratchet_state_k, "ratchet-step" )
  commit_k     = H( tile_k ) → appended to L16 audit chain

Milestones:
  M1 (done): offline ratchet for egress-preset signature only.
  M2 (this): extend to A2A/SesT, paid-MCP, CLS Tier-B/C (10 capabilities).
  M3: networked external-entropy layer (ADR-0103 integration).
  M4: red-team round.

Must NOT:
  - Import anthropic.
  - Entangle any Apache-core feature or any L10/L16/L34/L35 enforcement.
  - Put root/tile/key material into audit details (hash only).
  - Add wall-clock dependencies to the epoch.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

# Reuse existing crypto primitives from sob_crypto.
# ADR-0215 F5: the dotted `from operator.license.sob_crypto import ...` that
# used to be the primary attempt here can NEVER resolve (stdlib `operator`
# always shadows the repo's operator/ directory) — it was dead code, and the
# bare form below was doing all the real work via the `except` branch. Made
# the working form primary; no fallback needed since this module already
# lives inside operator/license/ (its own sibling directory is always on
# sys.path when this file itself was importable at all).
from sob_crypto import (  # noqa: E402
    chacha20_decrypt,
    chacha20_encrypt,
    hkdf_derive,
)


@dataclass(frozen=True)
class RatchetState:
    """Immutable ratchet state at epoch k.

    The state itself is the HKDF state (32 bytes), ready to be expanded
    into capability tiles. Each derivation advances the state to k+1.
    """
    state_bytes: bytes  # 32-byte HKDF state at epoch k
    epoch_k: int        # which epoch (0-indexed)

    def __post_init__(self) -> None:
        if len(self.state_bytes) != 32:
            raise ValueError(f"state_bytes must be 32 bytes, got {len(self.state_bytes)}")


class EntangledRatchet:
    """Forward-only ratchet for entangled capabilities (ADR-0167).

    Initialized with a root (derived from the signed license token) and
    an audit-chain-head hash. Lazily derives capability keys on first use
    and caches the derivation for the current epoch (per-turn cache).

    Invariants:
      - ratchet_state is immutable; each derivation produces a new state.
      - The epoch is pinned to the audit-chain-head hash at init time.
      - Derivations are cached per epoch to avoid re-computing.
      - The ratchet never depends on wall-clock (immune to skew/timezone).
      - Committed hashes are NEVER the key material itself, only H(tile_k).
    """

    def __init__(self, root: bytes, audit_chain_head_hash: bytes) -> None:
        """Initialize the ratchet.

        Args:
            root: 32-byte root derived from signed_license_token (via HKDF-Extract).
            audit_chain_head_hash: Current L16 audit chain head (hash of latest event).
                The ratchet advances when this changes (new security-relevant work).
        """
        if len(root) != 32:
            raise ValueError(f"root must be 32 bytes, got {len(root)}")
        if len(audit_chain_head_hash) < 32:
            raise ValueError(f"audit_chain_head_hash must be ≥32 bytes, got {len(audit_chain_head_hash)}")

        self._root = root
        self._current_chain_head = audit_chain_head_hash
        self._current_state = RatchetState(root, epoch_k=0)
        self._derivation_cache: dict[str, bytes] = {}  # label → tile_k
        self._committed_hashes: dict[str, bytes] = {}  # label → H(tile_k)

    @property
    def current_state(self) -> RatchetState:
        """Return the current ratchet state."""
        return self._current_state

    @property
    def current_chain_head(self) -> bytes:
        """Return the current audit chain head (epoch input)."""
        return self._current_chain_head

    def advance(
        self,
        new_chain_head: bytes,
        external_entropy: bytes | None = None,
    ) -> bool:
        """Advance the ratchet, optionally with networked external entropy (M3).

        Args:
            new_chain_head: Audit chain head (≥32 bytes); advances epoch if changed.
            external_entropy: Optional external entropy from ADR-0103 membership.
                If provided (≥32 bytes), folded into ratchet for true forward-secrecy.
                If None, ratchet uses offline-only derivation (still forward-only, but
                recomputable by attacker holding the root). Offline mode is always available
                (fail-closed: no network dependency).

        Returns True if advanced, False if chain head unchanged.

        Raises ValueError if new_chain_head is malformed (fail-closed).

        M3: Networked entropy is *additive*, not required. Network down → offline ratchet works.
        """
        if not isinstance(new_chain_head, bytes) or len(new_chain_head) < 32:
            raise ValueError(f"new_chain_head must be ≥32 bytes, got {type(new_chain_head).__name__}({len(new_chain_head) if isinstance(new_chain_head, bytes) else '?'})")

        if new_chain_head == self._current_chain_head:
            return False

        # Validate external entropy if provided
        if external_entropy is not None:
            if not isinstance(external_entropy, bytes) or len(external_entropy) < 32:
                external_entropy = None  # Ignore malformed entropy (fail-closed to offline)

        # Chain head changed; step the ratchet forward.
        # Offline: ratchet_state_{k+1} = HKDF-Expand( state_k, "ratchet-step" )
        # Networked (M3): fold external_entropy into the step for true forward-secrecy
        step_info = b"ratchet-step"
        if external_entropy is not None:
            # Additive: combine offline step with networked entropy
            step_info = step_info + b":" + external_entropy[:16]  # 16-byte entropy prefix

        next_state_bytes = hkdf_derive(
            self._current_state.state_bytes,
            step_info,
            length=32,
        )
        self._current_state = RatchetState(next_state_bytes, epoch_k=self._current_state.epoch_k + 1)
        self._current_chain_head = new_chain_head
        self._derivation_cache.clear()  # Invalidate cache for new epoch
        self._committed_hashes.clear()  # Invalidate commitments for new epoch
        self._networked_this_epoch = external_entropy is not None  # Track networked tier

        return True

    @property
    def is_networked_epoch(self) -> bool:
        """Return True if the current epoch used networked external entropy (M3)."""
        return getattr(self, "_networked_this_epoch", False)

    def derive_tile(self, capability_label: str) -> bytes:
        """Derive a 32-byte tile key for a capability in the current epoch.

        The tile is computed from the current ratchet state and epoch input
        (audit chain head), combined with the capability label. If already
        cached for this epoch, returns the cached value.

        Tile format: tile_k = HKDF-Expand( state_k, epoch_input || label, 32 )
        """
        if capability_label in self._derivation_cache:
            return self._derivation_cache[capability_label]

        # Combine epoch input and capability label.
        info = self._current_chain_head + b":" + capability_label.encode("utf-8")

        tile_k = hkdf_derive(
            self._current_state.state_bytes,
            info,
            length=32,
        )
        self._derivation_cache[capability_label] = tile_k
        return tile_k

    def commit_tile_hash(self, capability_label: str) -> bytes:
        """Return H(tile_k) for audit chain commitment (not the key itself).

        The commitment is what gets appended to the L16 audit chain,
        never the tile key itself. This provides tamper-evident proof
        that the ratchet was advanced at this epoch.
        """
        if capability_label in self._committed_hashes:
            return self._committed_hashes[capability_label]

        tile = self.derive_tile(capability_label)
        commit_hash = hashlib.sha256(tile).digest()
        self._committed_hashes[capability_label] = commit_hash
        return commit_hash

    def all_commitments(self) -> dict[str, str]:
        """Return all committed hashes for this epoch as hex strings.

        Used when writing to the L16 audit chain: the commitment dict
        is serialized to JSON (hex-encoded) and appended to audit.jsonl
        with event type 'elr.committed' or similar.
        """
        return {
            label: commit_hash.hex()
            for label, commit_hash in self._committed_hashes.items()
        }


def make_root_from_license_token(signed_token: bytes, *,
                                 instance_id: str | None = None) -> bytes:
    """Extract the root from a signed license token (ADR-0167 M1).

    The signed token is whatever the license system has already validated
    (Ed25519 signature verified, expiry checked, instance_id bound).
    We derive a fresh root via HKDF so that the root is:
      - Deterministic (same token + same instance_id → same root).
      - Non-invertible (can't recover the token from the root).
      - Domain-separated ("elr-root-v1" label).
      - Instance-bound when ``instance_id`` is supplied (see below).

    Instance binding (security review 2026-06-27): the token plaintext carries
    ``instance_id`` as a signed claim, but binding was only TRANSITIVE on the
    token bytes — a token plaintext lifted to another machine derived an
    identical root. Folding ``instance_id`` into the HKDF ``info`` makes the root
    differ per machine even for the same token, so a copied token is useless off
    its bound host. Production callers MUST pass ``instance_id``; it is kept
    optional only for backward compatibility (``None`` → legacy "elr-root-v1"
    info, same value as before this change).

    Args:
        signed_token: The bytes of the parsed+verified SOB claims (canonically
            JSON). Must be at least 32 bytes. Typically the entire plaintext of
            the license SOB after unseal().
        instance_id: The instance the license is bound to (ADR-0103). When given,
            it is mixed into the HKDF info for per-instance domain separation.

    Returns:
        32-byte root suitable for initializing an EntangledRatchet.
    """
    if not isinstance(signed_token, bytes) or len(signed_token) < 32:
        raise ValueError(f"signed_token must be ≥32 bytes, got {len(signed_token)}")

    # HKDF(salt=None, IKM=signed_token, info=<domain>[:instance_id], L=32).
    # signed_token is the secret (IKM); the info label provides domain separation.
    info = b"elr-root-v1"
    if instance_id:
        info = info + b":" + instance_id.encode("utf-8")
    return hkdf_derive(signed_token, info, length=32)


@dataclass(frozen=True)
class WrappedCapabilityDescriptor:
    """AEAD-wrapped capability payload, ready to be unwrapped by a tile key.

    The descriptor is the encrypted capability (e.g., the egress-allowlist
    signature for a paid preset), encrypted with a tile key derived from the
    ratchet. Decryption with the wrong tile key (stale epoch, wrong license,
    tampering) yields garbage, not an error message — fail-closed by design.

    Fields:
      nonce: 12-byte ChaCha20 nonce.
      ciphertext: encrypted plaintext + Poly1305 tag.
    """
    nonce: bytes
    ciphertext: bytes

    def __post_init__(self) -> None:
        if len(self.nonce) != 12:
            raise ValueError(f"nonce must be 12 bytes, got {len(self.nonce)}")
        if len(self.ciphertext) < 16:  # 16-byte Poly1305 tag minimum
            raise ValueError(f"ciphertext must be ≥16 bytes, got {len(self.ciphertext)}")

    def to_bytes(self) -> bytes:
        """Serialize to wire format: nonce || ciphertext."""
        return self.nonce + self.ciphertext

    @classmethod
    def from_bytes(cls, data: bytes) -> WrappedCapabilityDescriptor:
        """Parse wire format: first 12 bytes = nonce, rest = ciphertext."""
        if len(data) < 12 + 16:
            raise ValueError(f"wrapped descriptor must be ≥28 bytes, got {len(data)}")
        nonce = data[:12]
        ciphertext = data[12:]
        return cls(nonce=nonce, ciphertext=ciphertext)


class CapabilityEnvelope:
    """Wrap/unwrap capability payloads with AEAD encryption.

    Used to encrypt capability data (e.g., egress-allowlist signatures)
    with a tile key derived from the ratchet. Decryption failure returns
    None (fail-closed), never raises.
    """

    @staticmethod
    def wrap(plaintext: dict[str, Any], tile_key: bytes) -> WrappedCapabilityDescriptor:
        """Encrypt a capability dict with ChaCha20-Poly1305.

        Args:
            plaintext: Dict to encrypt (e.g., {"allowed_hosts": ["..."], "sig": "..."}).
            tile_key: 32-byte tile key from the ratchet.

        Returns:
            WrappedCapabilityDescriptor with encrypted payload.
        """
        if len(tile_key) != 32:
            raise ValueError(f"tile_key must be 32 bytes, got {len(tile_key)}")

        plaintext_json = json.dumps(plaintext, sort_keys=True, separators=(",", ":"))
        plaintext_bytes = plaintext_json.encode("utf-8")

        nonce, ciphertext = chacha20_encrypt(tile_key, plaintext_bytes)
        return WrappedCapabilityDescriptor(nonce=nonce, ciphertext=ciphertext)

    @staticmethod
    def unwrap(
        wrapped: WrappedCapabilityDescriptor,
        tile_key: bytes,
    ) -> dict[str, Any] | None:
        """Decrypt a capability with ChaCha20-Poly1305.

        Returns the plaintext dict on success, None on any failure (decryption,
        JSON parse, tag verification). Never raises.

        Args:
            wrapped: The WrappedCapabilityDescriptor.
            tile_key: 32-byte tile key from the ratchet.

        Returns:
            Decrypted dict, or None on failure.
        """
        if len(tile_key) != 32:
            return None

        try:
            plaintext_bytes = chacha20_decrypt(tile_key, wrapped.nonce, wrapped.ciphertext)
        except Exception:  # noqa: BLE001
            return None

        try:
            plaintext = json.loads(plaintext_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

        return plaintext if isinstance(plaintext, dict) else None


# M2: Capability Registry (storage + loader)

@dataclass(frozen=True)
class CapabilityDescriptorRecord:
    """Tenant-stored reference to a wrapped capability descriptor.

    Stored in tenant.corvin.yaml::spec.elr.capabilities.LABEL, wire-format
    is base64-encoded bytes.
    """
    capability_label: str  # e.g., "egress-paid-preset", "a2a-sestoken"
    wrapped_bytes_b64: str  # base64 of WrappedCapabilityDescriptor.to_bytes()
    version: int = 1       # capability version (for descriptor rotation)

    def to_wrapped_descriptor(self) -> WrappedCapabilityDescriptor | None:
        """Decode base64 to WrappedCapabilityDescriptor. Returns None on decode failure."""
        try:
            import base64
            wire = base64.b64decode(self.wrapped_bytes_b64)
            return WrappedCapabilityDescriptor.from_bytes(wire)
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def from_wrapped(
        cls,
        capability_label: str,
        wrapped: WrappedCapabilityDescriptor,
        version: int = 1,
    ) -> CapabilityDescriptorRecord:
        """Create a record from a WrappedCapabilityDescriptor."""
        import base64
        wire = wrapped.to_bytes()
        wrapped_bytes_b64 = base64.b64encode(wire).decode("ascii")
        return cls(
            capability_label=capability_label,
            wrapped_bytes_b64=wrapped_bytes_b64,
            version=version,
        )


class CapabilityRegistry:
    """Load + cache wrapped capability descriptors from tenant config.

    M2: Manages 10 entangled capabilities (egress, A2A/SesT, MCP, CLS Tier-B/C).
    One registry per tenant. Lazy-loads descriptors from tenant.corvin.yaml.
    """

    def __init__(self, tenant_config: dict[str, Any] | None = None) -> None:
        """Initialize registry from tenant config.

        Args:
            tenant_config: Parsed tenant.corvin.yaml dict. Missing
                spec.elr.capabilities → empty registry (fail-closed fallback).
        """
        self._descriptors: dict[str, CapabilityDescriptorRecord] = {}
        self._loaded_descriptors: dict[str, WrappedCapabilityDescriptor] = {}

        if tenant_config and isinstance(tenant_config, dict):
            spec = tenant_config.get("spec")
            if isinstance(spec, dict):
                elr_block = spec.get("elr")
                if isinstance(elr_block, dict):
                    caps = elr_block.get("capabilities")
                    if isinstance(caps, dict):
                        for label, raw_record in caps.items():
                            if isinstance(raw_record, dict):
                                try:
                                    rec = CapabilityDescriptorRecord(
                                        capability_label=label,
                                        wrapped_bytes_b64=raw_record["wrapped_bytes_b64"],
                                        version=int(raw_record.get("version", 1)),
                                    )
                                    self._descriptors[label] = rec
                                except (KeyError, ValueError, TypeError):
                                    pass

    def get_descriptor(
        self,
        capability_label: str,
    ) -> WrappedCapabilityDescriptor | None:
        """Retrieve (and cache) a wrapped descriptor.

        Returns the decoded WrappedCapabilityDescriptor on success, None
        if not found or decode fails (fail-closed).
        """
        if capability_label in self._loaded_descriptors:
            return self._loaded_descriptors[capability_label]

        rec = self._descriptors.get(capability_label)
        if rec is None:
            return None

        wrapped = rec.to_wrapped_descriptor()
        if wrapped is not None:
            self._loaded_descriptors[capability_label] = wrapped
        return wrapped

    def all_labels(self) -> list[str]:
        """Return all registered capability labels."""
        return list(self._descriptors.keys())
