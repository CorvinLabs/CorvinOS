"""
Checkpoint Signer (ADR-0625: Watchdog Circumvention Mitigation)

High-level interface for signing and verifying checkpoints used by the
Divergence Watchdog. Provides fail-closed checkpoint integrity validation
to prevent watchdog circumvention attacks.

Security Guarantees:
1. Every checkpoint is signed with a tenant-specific HMAC key that is SECRET
2. Merkle root hash detects any tampering with checkpoint state
3. Signature verification uses constant-time comparison (no timing attacks)
4. Fail-closed: any verification failure raises exception, never silently accepts
5. Tenant-scoped: checkpoints from one tenant cannot be used by another

Key material (round-4 review, F2 — the whole point of this module)
------------------------------------------------------------------
The key is 256 bits of ``secrets.token_hex(32)`` stored at
``<corvin_home>/tenants/<tenant_id>/keys/checkpoint_signing.key`` with mode
0600, created on first use — the SAME scheme as
:mod:`core.learning.feedback_signature` and
:mod:`core.infinite_session.crypto_binding`. Reading it fails closed
(:class:`CheckpointKeyUnavailable`); there is no in-code fallback, because a
fallback is exactly what the defect was.

Until 2026-09-07 ``get_tenant_key()`` returned
``sha256(b"checkpoint.signer:" + tenant_id)`` — a pure function of a public
string. Anybody could recompute it, sign an arbitrary state (e.g. α pinned at
the top of the watchdog's bound and damping at the bottom: the maximum
learning-rate / minimum-damping corner) and have ``restore_checkpoint`` accept
it as authentic. Reproduced in the round-4 review.

**Migration is deliberately breaking.** A checkpoint signed with the old public
derivation does NOT verify under the real key — it raises
:class:`CheckpointSignatureError` like any other bad signature, and the caller
(the divergence watchdog) treats it as unusable. That is correct: those
checkpoints are exactly as trustworthy as an attacker-written file, so silently
honouring them would preserve the vulnerability under a new name. Nothing in
the repo reads a checkpoint back across a process boundary today (see
``DivergenceWatchdog.restore_checkpoint``, which consults only its in-memory
``signed_checkpoints``), so the practical cost of the break is zero.
"""

import json
import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from core.paths.tenant import corvin_home
from core.tenants import validate_tenant_id

#: Filename of the per-tenant checkpoint signing key.
KEY_FILENAME = "checkpoint_signing.key"


class CheckpointKeyUnavailable(Exception):
    """The tenant signing key could not be created or read — fail closed."""


class CheckpointSignatureError(Exception):
    """Raised when checkpoint signature verification fails."""
    pass


class CheckpointSigner:
    """
    High-level interface for signing/verifying Meta Loop checkpoints.

    Used by DivergenceWatchdog to ensure checkpoint integrity across
    save/restore cycles.

    Threat Model Mitigations:
    1. Merkle Root Binding — any state change → different hash
    2. HMAC Signature — attacker cannot forge without the tenant's SECRET
       key file (``<tenant>/keys/checkpoint_signing.key``, 0600)
    3. Tenant Isolation — checkpoints bound to creating tenant
    4. Constant-Time Verification — no timing side-channels
    5. Fail-Closed — verification failure halts restoration
    """

    def __init__(self, tenant_id: str, corvin_home_override: Optional[str | Path] = None):
        """
        Initialize signer for a tenant.

        Args:
            tenant_id: Owning tenant (scopes the signing key)
            corvin_home_override: Optional CORVIN_HOME override (tests)
        """
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self._home = Path(corvin_home_override) if corvin_home_override else None

    # ── key management (same scheme as feedback_signature.py) ────────────

    @property
    def key_path(self) -> Path:
        home = self._home if self._home is not None else corvin_home()
        return Path(home) / "tenants" / self.tenant_id / "keys" / KEY_FILENAME

    def get_tenant_key(self) -> bytes:
        """
        Read (creating on first use) this tenant's SECRET HMAC key.

        256 bits from :func:`secrets.token_hex`, stored 0600 under
        ``<corvin_home>/tenants/<tenant>/keys/``. Never derived from the
        tenant id or any other public value.

        Returns:
            HMAC key as bytes

        Raises:
            CheckpointKeyUnavailable: the key cannot be created or read.
        """
        path = self.key_path
        if not path.exists():
            try:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(secrets.token_hex(32))
            except FileExistsError:
                pass  # concurrent creator won the race — read theirs
            except OSError as exc:
                raise CheckpointKeyUnavailable(
                    f"cannot create checkpoint signing key for {self.tenant_id!r}: {exc}"
                ) from exc
        try:
            material = path.read_text().strip()
        except OSError as exc:
            raise CheckpointKeyUnavailable(
                f"cannot read checkpoint signing key for {self.tenant_id!r}: {exc}"
            ) from exc
        if not material:
            raise CheckpointKeyUnavailable(
                f"checkpoint signing key for {self.tenant_id!r} is empty (fail-closed)"
            )
        return material.encode()

    def compute_merkle_root(self, state: Dict[str, Any]) -> str:
        """
        Compute Merkle tree root hash of checkpoint state.

        Args:
            state: Checkpoint state dict

        Returns:
            SHA256 hex digest
        """
        # Deterministic JSON serialization
        state_json = json.dumps(state, sort_keys=True, default=str)

        # Merkle root is hash of entire state
        merkle_root = hashlib.sha256(state_json.encode()).hexdigest()

        return merkle_root

    def sign_checkpoint(self, state: Dict[str, Any]) -> Dict[str, str]:
        """
        Sign a checkpoint state.

        Args:
            state: Checkpoint state dict

        Returns:
            Dict with 'merkle_root' and 'signature' fields
        """
        merkle_root = self.compute_merkle_root(state)
        key = self.get_tenant_key()

        # HMAC-SHA256 of merkle root
        signature = hmac.new(
            key,
            merkle_root.encode(),
            hashlib.sha256
        ).hexdigest()

        return {
            'merkle_root': merkle_root,
            'signature': signature,
            'tenant_id': self.tenant_id,
        }

    def verify_checkpoint(
        self,
        state: Dict[str, Any],
        merkle_root: str,
        signature: str,
        tenant_id: Optional[str] = None
    ) -> bool:
        """
        Verify checkpoint integrity.

        Fail-closed: raises CheckpointSignatureError on any mismatch.

        Args:
            state: Checkpoint state dict
            merkle_root: Claimed merkle root
            signature: Claimed signature
            tenant_id: Optional; must match this signer's tenant if provided

        Returns:
            True if verification passes

        Raises:
            CheckpointSignatureError: If verification fails
        """
        # Tenant check (fail-closed)
        if tenant_id is not None and tenant_id != self.tenant_id:
            raise CheckpointSignatureError(
                f"Tenant mismatch: checkpoint for {tenant_id!r}, "
                f"verifier for {self.tenant_id!r}"
            )

        # Recompute merkle root
        computed_merkle = self.compute_merkle_root(state)
        if computed_merkle != merkle_root:
            raise CheckpointSignatureError(
                f"Merkle root mismatch: expected {merkle_root}, "
                f"computed {computed_merkle}"
            )

        # Verify signature (constant-time)
        key = self.get_tenant_key()
        computed_sig = hmac.new(
            key,
            merkle_root.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_sig, signature):
            raise CheckpointSignatureError(
                "Signature verification failed (tampering detected)"
            )

        return True


class CheckpointSigningContext:
    """
    Thread-safe context for checkpoint signing in Meta Loop optimizer.

    Tracks all signed checkpoints and validates before restoration.
    """

    def __init__(self, tenant_id: str, corvin_home_override: Optional[str | Path] = None):
        """Initialize signing context."""
        self.tenant_id = tenant_id
        self.signer = CheckpointSigner(tenant_id, corvin_home_override=corvin_home_override)
        self.signed_checkpoints = {}  # id -> (state, merkle, sig)

    def sign_and_store(
        self,
        checkpoint_id: str,
        state: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Sign checkpoint and store for later verification.

        Args:
            checkpoint_id: Unique checkpoint identifier
            state: Checkpoint state

        Returns:
            Signing result {merkle_root, signature, tenant_id}
        """
        result = self.signer.sign_checkpoint(state)

        # Store for later verification
        self.signed_checkpoints[checkpoint_id] = {
            'state': state.copy(),
            'merkle_root': result['merkle_root'],
            'signature': result['signature'],
        }

        return result

    def verify_and_restore(
        self,
        checkpoint_id: str,
        state: Dict[str, Any],
        merkle_root: str,
        signature: str
    ) -> Dict[str, Any]:
        """
        Verify checkpoint and restore state (fail-closed).

        Args:
            checkpoint_id: Checkpoint identifier
            state: Claimed checkpoint state
            merkle_root: Claimed merkle root
            signature: Claimed signature

        Returns:
            Verified state

        Raises:
            CheckpointSignatureError: If verification fails
        """
        # Verify integrity
        self.signer.verify_checkpoint(
            state,
            merkle_root,
            signature,
            self.tenant_id
        )

        return state.copy()

    def clear(self):
        """Clear all stored checkpoints."""
        self.signed_checkpoints.clear()
