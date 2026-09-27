"""A2A Discovery Coordinator — ADR-2059 Integration.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) — the only
importer is ``core/discovery/__init__.py``; ``grep -rn "core.discovery"`` outside
tests finds no route, CLI, daemon or plugin that constructs a coordinator. The
live A2A pairing path is ``corvin_operator/bridges/shared/a2a_friendship.py`` /
``corvin_a2a.py pair``, not this module.

HANDSHAKE NOT IMPLEMENTED: there is no peer/relay transport here. Until
2026-09-27 ``_simulate_handshake`` returned a scripted success on the second
attempt, so every imported token reached ``ACTIVE`` and was audited as
``discovery.peer_paired`` without a single byte reaching the peer. The default
transport now raises ``NotImplementedError`` and ``attempt_handshake`` records
the pairing as ``FAILED`` (``handshake_transport_not_implemented``) — fail-closed.
A real transport is plugged in by overriding :meth:`DiscoveryCoordinator._perform_handshake`.

Implements zero-config A2A pairing via friendship tokens with:
  - A2ATokenCodec: HMAC-SHA256 based token creation/parsing (delegated to a2a_friendship.py)
  - Kid-based instance identity: HMAC-SHA256(master_key, org_id || instance_id)
  - Handshake state machine: PENDING → ACTIVE | FAILED (transport pluggable, see above)
  - Retry logic: exponential backoff (1s, 2s, 4s, …) when a transport reports a
    temporary failure
  - Audit events (content-free, via ``core/deployment/audit_sink.py`` onto the
    tenant chain): discovery.pairing_token_created, discovery.pairing_failed,
    discovery.peer_pairing_initiated, discovery.peer_paired,
    discovery.peer_pairing_failed, discovery.handshake_retry_scheduled

Load-bearing rules (ADR-2059 + ADR-0232):
  - Audit-FIRST and fail-closed: a state transition is committed only after its
    record reached the tenant chain; ``AuditWriteFailed`` propagates.
  - Kid hash (never plaintext), never labels or URLs, in audit records.

Tenant isolation: all lookups filtered by tenant_id (fail-closed on missing)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ``a2a_friendship`` imports its sibling ``_bounded_lock`` as a top-level
# module (the bridges run with ``bridges/shared`` on sys.path). Put that
# directory on the path so ``import core.discovery`` works from a plain
# repo-root PYTHONPATH instead of dying with ModuleNotFoundError.
_SHARED = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

from corvin_operator.bridges.shared.a2a_friendship import (  # noqa: E402
    FriendshipToken,
    create_friendship_token,
    parse_and_verify,
    FriendshipError,
    get_my_url,
    get_my_relay_url,
    _derive_channel_keys,
)

from core.deployment import audit_sink  # noqa: E402

logger = logging.getLogger(__name__)

# Content-free field sets (ids, hashes, counts, codes, booleans) — never the
# operator's label, a peer URL or an exception text.
_AUDIT_EVENTS: dict[str, frozenset[str]] = {
    "discovery.pairing_token_created": frozenset({"kid_hash", "label_present", "ttl_seconds", "relay_used"}),
    "discovery.pairing_failed": frozenset({"reason"}),
    "discovery.peer_pairing_initiated": frozenset({"kid_hash", "label_present", "peer_url_present", "relay_used"}),
    "discovery.peer_paired": frozenset({"kid_hash", "relay_used", "attempts"}),
    "discovery.peer_pairing_failed": frozenset({"kid_hash", "reason", "attempts"}),
    "discovery.handshake_retry_scheduled": frozenset({"kid_hash", "attempt", "next_retry_in_s"}),
}
audit_sink.register_events(_AUDIT_EVENTS)

HANDSHAKE_NOT_IMPLEMENTED = "handshake_transport_not_implemented"


class PairingState(Enum):
    """Pairing state machine transitions."""
    PENDING = "pending"           # Token created, awaiting ACK
    ACTIVE = "active"              # Handshake complete, both peers know each other
    FAILED = "failed"              # Handshake failed (max retries exceeded or authoritative error)
    REVOKED = "revoked"            # Peer explicitly revoked
    EXPIRED = "expired"            # Token or session TTL exceeded


class RetryStrategy(Enum):
    """Exponential backoff parameters."""
    INITIAL_DELAY_S = 1.0
    MAX_DELAY_S = 240.0             # 4 minutes (production use: 5 min keepalive)
    BACKOFF_MULTIPLIER = 2.0
    MAX_ATTEMPTS = 10               # Limits to ~10 min total with backoff


@dataclass(frozen=True)
class InstanceIdentity:
    """Cryptographically-bound instance identity per kid (org_id || instance_id)."""
    org_id: str                     # Org/tenant identifier
    instance_id: str                # Instance UUID
    master_key: str                 # Hex-encoded HMAC key (from CORVIN_HOME)

    def kid_hash(self) -> str:
        """HMAC-SHA256(master_key, org_id || instance_id) → kid_hash for audit logging.

        Never log plaintext kid; use this hash instead (forward-secure in audit trail).
        """
        data = f"{self.org_id}||{self.instance_id}".encode("utf-8")
        kb = bytes.fromhex(self.master_key)
        return hmac.new(kb, data, "sha256").hexdigest()

    @classmethod
    def from_env(cls) -> InstanceIdentity:
        """Load instance identity from environment and CORVIN_HOME.

        Raises ValueError if required keys are missing.
        """
        org_id = os.environ.get("CORVIN_ORG_ID", "_default")
        instance_id = os.environ.get("CORVIN_INSTANCE_ID", str(uuid.uuid4()))
        master_key_hex = os.environ.get("CORVIN_A2A_MASTER_KEY")

        if not master_key_hex:
            # Per-install key under the tenant-resolved runtime root (honours
            # CORVIN_HOME). It used to be sha256("instance_<org>_<instance>") —
            # a value anyone who knows the two ids can recompute, i.e. not a
            # key — and was written world-readable before the chmod.
            from core.paths.tenant import tenant_home  # noqa: PLC0415

            key_file = tenant_home(org_id) / "global" / "a2a_master_key"
            if key_file.exists():
                master_key_hex = key_file.read_text("utf-8").strip()
            else:
                master_key_hex = secrets.token_hex(32)
                key_file.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(master_key_hex)

        return cls(org_id=org_id, instance_id=instance_id, master_key=master_key_hex)


@dataclass
class PairingRecord:
    """In-memory pairing state (persisted to audit trail, not local storage)."""
    kid: str                        # Friendship token key ID
    peer_label: str | None          # Peer connection name
    state: PairingState             # Current state (PENDING | ACTIVE | FAILED | REVOKED)
    tenant_id: str                  # Tenant scoping (GDPR Art. 5)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    peer_url: str | None = None     # Peer's A2A URL (once discovered)
    relay_url: str | None = None    # Relay URL for fallback (from token or config)
    hmac_key: str | None = None     # Derived HMAC key (hex, 64 chars)
    recv_key: str | None = None     # Derived recv key (hex, 64 chars)
    retry_count: int = 0
    retry_delay_s: float = RetryStrategy.INITIAL_DELAY_S.value
    next_retry_at: float | None = None
    last_error: str | None = None

    def kid_hash(self) -> str:
        """Hash the kid for audit logging (never log plaintext)."""
        return hashlib.sha256(self.kid.encode()).hexdigest()

    def to_audit_dict(self) -> dict[str, Any]:
        """Convert to audit event payload (no plaintext kid, no secrets)."""
        return {
            "kid_hash": self.kid_hash(),
            "peer_label": self.peer_label,
            "state": self.state.value,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "peer_url": self.peer_url,
            "relay_url": self.relay_url,
            "retry_count": self.retry_count,
            "last_error": self.last_error,
        }


class DiscoveryCoordinator:
    """Manages A2A pairing state machine with audit-first design.

    Responsibilities:
      1. Token creation/parsing (delegates to a2a_friendship)
      2. Instance identity management (kid-based)
      3. Handshake state machine (PENDING → ACTIVE)
      4. Retry logic with exponential backoff
      5. Audit event emission (immutable, hash-chained)

    All state changes are logged to audit trail before local updates.
    """

    def __init__(self, instance_id: InstanceIdentity, tenant_id: str = "_default"):
        """Initialize coordinator for the given instance and tenant.

        Args:
            instance_id: InstanceIdentity with org, instance, and master key
            tenant_id: Tenant scope for GDPR isolation (fail-closed on missing)

        Raises:
            ValueError: If tenant_id is None or empty
        """
        if not tenant_id:
            raise ValueError("tenant_id must not be empty (GDPR Art. 5)")
        self.instance = instance_id
        self.tenant_id = tenant_id
        self.pairings: dict[str, PairingRecord] = {}  # kid → record
        logger.info(
            f"DiscoveryCoordinator initialized for tenant={tenant_id}, "
            f"kid_hash={instance_id.kid_hash()}"
        )

    def create_pairing_token(
        self,
        *,
        label: str | None = None,
        ttl_seconds: float = 30 * 86400,
        relay_url: str | None = None,
    ) -> tuple[FriendshipToken, str]:
        """Create a friendship token for pairing discovery.

        Returns ``(FriendshipToken, token_string)``. The pairing record is
        registered only after ``discovery.pairing_token_created`` committed to
        the tenant chain (``AuditWriteFailed`` propagates — fail-closed).
        """
        my_url = get_my_url()
        relay = relay_url or get_my_relay_url()

        token, token_str = create_friendship_token(
            url=my_url,
            label=label,
            ttl_seconds=ttl_seconds,
            relay_url=relay,
        )

        self._emit_audit_event(
            event_type="discovery.pairing_token_created",
            payload={
                "kid_hash": hashlib.sha256(token.kid.encode()).hexdigest(),
                "label_present": bool(label),
                "ttl_seconds": ttl_seconds,
                "relay_used": bool(relay),
            },
            lom="DiscoveryCoordinator.create_pairing_token",
        )

        record = PairingRecord(
            kid=token.kid,
            peer_label=label,
            state=PairingState.PENDING,
            tenant_id=self.tenant_id,
            relay_url=relay,
        )
        self.pairings[token.kid] = record

        logger.info("Token created: kid_hash=%s", record.kid_hash())
        return token, token_str

    def import_pairing_token(
        self, token_str: str, *, override_relay: str | None = None
    ) -> PairingRecord:
        """Import a friendship token from a peer (state=PENDING).

        Raises:
            FriendshipError: If token is invalid (audited as discovery.pairing_failed)
            ValueError: If tenant_id is missing
            AuditWriteFailed: If the audit record did not commit (fail-closed)
        """
        if not self.tenant_id:
            raise ValueError("tenant_id must not be empty (GDPR Art. 5)")

        try:
            token = parse_and_verify(token_str)
        except FriendshipError:
            self._emit_audit_event(
                event_type="discovery.pairing_failed",
                payload={"reason": "invalid_token"},
                lom="DiscoveryCoordinator.import_pairing_token",
            )
            raise

        hmac_key, recv_key = _derive_channel_keys(token.key)

        record = PairingRecord(
            kid=token.kid,
            peer_label=token.label,
            state=PairingState.PENDING,
            tenant_id=self.tenant_id,
            peer_url=token.url,
            relay_url=override_relay or token.relay_url,
            hmac_key=hmac_key,
            recv_key=recv_key,
        )
        record.next_retry_at = time.time()

        self._emit_audit_event(
            event_type="discovery.peer_pairing_initiated",
            payload={
                "kid_hash": record.kid_hash(),
                "label_present": bool(token.label),
                "peer_url_present": bool(token.url),
                "relay_used": record.relay_url is not None,
            },
            lom="DiscoveryCoordinator.import_pairing_token",
        )
        self.pairings[token.kid] = record

        logger.info("Token imported: kid_hash=%s", record.kid_hash())
        return record

    def attempt_handshake(self, kid: str) -> bool:
        """Attempt a single handshake with the peer.

        Returns True only when the transport (:meth:`_perform_handshake`)
        confirmed the peer; False when pending, retrying or failed. With the
        default transport (none — NOT IMPLEMENTED) the pairing is marked
        FAILED on the first attempt: fail-closed, never a fabricated ACTIVE.

        Raises:
            KeyError: If kid is not found
            AuditWriteFailed: If the transition's audit record did not commit
        """
        record = self.pairings.get(kid)
        if not record:
            raise KeyError(f"Pairing not found: kid={kid}")

        if record.state == PairingState.ACTIVE:
            return True

        if record.state in (PairingState.REVOKED, PairingState.EXPIRED, PairingState.FAILED):
            return False

        now = time.time()
        if record.next_retry_at and record.next_retry_at > now:
            return False

        attempts = record.retry_count + 1
        try:
            success = bool(self._perform_handshake(record))
        except NotImplementedError:
            self._emit_audit_event(
                event_type="discovery.peer_pairing_failed",
                payload={
                    "kid_hash": record.kid_hash(),
                    "reason": HANDSHAKE_NOT_IMPLEMENTED,
                    "attempts": attempts,
                },
                lom="DiscoveryCoordinator.attempt_handshake",
            )
            record.state = PairingState.FAILED
            record.last_error = HANDSHAKE_NOT_IMPLEMENTED
            record.retry_count = attempts
            record.updated_at = time.time()
            logger.error("Handshake not implemented — pairing FAILED: kid_hash=%s", record.kid_hash())
            return False

        if success:
            self._emit_audit_event(
                event_type="discovery.peer_paired",
                payload={
                    "kid_hash": record.kid_hash(),
                    "relay_used": record.relay_url is not None,
                    "attempts": attempts,
                },
                lom="DiscoveryCoordinator.attempt_handshake",
            )
            record.state = PairingState.ACTIVE
            record.updated_at = time.time()
            record.retry_count = 0
            logger.info("Handshake succeeded: kid_hash=%s", record.kid_hash())
            return True

        if attempts >= RetryStrategy.MAX_ATTEMPTS.value:
            self._emit_audit_event(
                event_type="discovery.peer_pairing_failed",
                payload={
                    "kid_hash": record.kid_hash(),
                    "reason": "max_retries_exceeded",
                    "attempts": attempts,
                },
                lom="DiscoveryCoordinator.attempt_handshake",
            )
            record.retry_count = attempts
            record.state = PairingState.FAILED
            record.last_error = "max_retries_exceeded"
            logger.error("Handshake failed (max retries): kid_hash=%s", record.kid_hash())
            return False

        # Exponential backoff: wait the CURRENT delay (1s after the first
        # failure, then 2s, 4s, …), then double it for the next one. It used to
        # double first, so the first retry waited 2s and the documented 1s
        # step never happened.
        delay = min(record.retry_delay_s, RetryStrategy.MAX_DELAY_S.value)
        self._emit_audit_event(
            event_type="discovery.handshake_retry_scheduled",
            payload={
                "kid_hash": record.kid_hash(),
                "attempt": attempts,
                "next_retry_in_s": delay,
            },
            lom="DiscoveryCoordinator.attempt_handshake",
        )
        record.retry_count = attempts
        record.next_retry_at = now + delay
        record.retry_delay_s = min(
            delay * RetryStrategy.BACKOFF_MULTIPLIER.value,
            RetryStrategy.MAX_DELAY_S.value,
        )
        return False

    def _perform_handshake(self, record: PairingRecord) -> bool:
        """Send the ACK to the peer (or relay) and verify its signed answer.

        NOT IMPLEMENTED: this module has no transport. Returns True only for a
        verified peer answer, False for a temporary failure (retried with
        backoff); the default raises so no pairing is ever reported ACTIVE
        without having reached the peer.
        """
        raise NotImplementedError(HANDSHAKE_NOT_IMPLEMENTED)

    def get_pairing_state(self, kid: str) -> PairingRecord | None:
        """Retrieve current pairing state for a kid."""
        if not self.tenant_id:
            return None  # Fail-closed on missing tenant_id
        return self.pairings.get(kid)

    def _emit_audit_event(
        self, event_type: str, payload: dict[str, Any], lom: str
    ) -> dict:
        """Append one record to this tenant's audit chain (fail-closed).

        Goes through ``core.deployment.audit_sink.emit`` →
        ``forge.security_events.write_event`` on ``tenant_audit_chain``. Until
        2026-09-27 this called ``security_events.emit_discovery_event`` — a
        function that does not exist — and swallowed the AttributeError, so no
        discovery event was ever recorded.

        Raises:
            ValueError: If tenant_id is missing
            AuditWriteFailed: If the record did not commit
        """
        if not self.tenant_id:
            raise ValueError("tenant_id must not be empty (GDPR Art. 5)")
        return audit_sink.emit(
            event_type,
            {**payload, "lom": lom},
            tenant_id=self.tenant_id,
            severity="WARNING" if event_type.endswith("failed") else "INFO",
        )


# ── Module-level helpers ────────────────────────────────────────────────

def bootstrap_discovery_coordinator(
    tenant_id: str = "_default",
) -> DiscoveryCoordinator:
    """Bootstrap a discovery coordinator for the current instance/tenant.

    Args:
        tenant_id: Tenant scope (default: _default)

    Returns:
        DiscoveryCoordinator ready for pairing operations

    Raises:
        ValueError: If required environment is missing
    """
    instance = InstanceIdentity.from_env()
    coordinator = DiscoveryCoordinator(instance, tenant_id=tenant_id)
    return coordinator
