"""A2A Delegation Verifier - Validate RSA-signed Tasks for Licensing Gate

Implements ADR-0704: Licensing Phase 2 - A2A RSA Gate
- Verify the AUTHORITY signature on the credential and the MEMBER signature
  on the task (task_id, credential_id and ttl_seconds are signed)
- Validate license tier (member-only access)
- Check revocation list (CRL) - an unreadable CRL revokes everything
- Server-capped task TTL and single-use task ids (replay store)
- Every verification is a hash-chained record in the tenant audit chain
- Fail-closed: deny by default unless all checks pass

License: Apache-2.0
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

from core.licensing import audit as licensing_audit
from core.licensing.member_credential import (
    MemberCredential,
    SignedTask,
    RSAKeyPair,
    LicenseTier,
    CredentialStore,
    InvalidLicensingId,
    MAX_TASK_CLOCK_SKEW_SECONDS,
    MAX_TASK_TTL_SECONDS,
    atomic_write_private,
    credential_signing_bytes,
    secure_dir,
    task_signing_bytes,
    validate_credential_id,
    validate_member_id,
    validate_task_id,
)

try:  # POSIX only; the in-process lock still serialises on Windows
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: File names of the authority key material under ``<root>/licensing/authority/``.
AUTHORITY_DIRNAME = "authority"
AUTHORITY_PRIVATE_KEY = "authority_key.pem"
AUTHORITY_PUBLIC_KEY = "authority_pub.pem"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================================
# Store lock
# ============================================================================

class StoreLock:
    """Re-entrant lock for one file of a licensing store: threads AND processes.

    A read-modify-write on a JSON file needs both halves: an in-process lock
    (``flock`` does not exclude two threads sharing a process on every
    platform, and Windows has no ``flock``) and an advisory file lock for the
    other processes writing the same store. One object per lock FILE, so two
    tenants never wait on each other.

    Re-entrant per thread. That matters: a second ``flock`` on a second
    descriptor of the same file blocks the process on itself.
    """

    _registry_lock = threading.Lock()
    _registry: Dict[str, "StoreLock"] = {}

    def __init__(self, path: Path):
        self.path = Path(path)
        #: In-process half; enough for a reader that only refreshes its copy.
        self.threads = threading.RLock()
        self._depth = 0
        self._fd: Optional[int] = None

    @classmethod
    def for_file(cls, path: Path) -> "StoreLock":
        key = os.path.abspath(str(path))
        with cls._registry_lock:
            lock = cls._registry.get(key)
            if lock is None:
                lock = cls._registry[key] = cls(Path(key))
            return lock

    def __enter__(self) -> "StoreLock":
        self.threads.acquire()
        try:
            if self._depth == 0:
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    if fcntl is not None:
                        fcntl.flock(fd, fcntl.LOCK_EX)
                except BaseException:
                    os.close(fd)
                    raise
                self._fd = fd
            self._depth += 1
        except BaseException:
            self.threads.release()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        self._depth -= 1
        if self._depth == 0 and self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                os.close(fd)  # closing the descriptor releases the flock
            except OSError:
                pass
        self.threads.release()


# ============================================================================
# CRL (Certificate Revocation List)
# ============================================================================

#: Written next to ``crl.json`` by the first save (see :class:`RevocationList`).
CRL_MARKER = ".crl_initialised"


class RevocationListError(RuntimeError):
    """The CRL could not be read or written."""


class RevocationList:
    """Manages revoked credentials.

    Fail-closed: while the CRL file exists but cannot be read or parsed,
    :meth:`is_revoked` answers ``True`` for EVERY credential. Before
    2026-11-19 a corrupt ``crl.json`` was logged and treated as an empty
    list, which un-revoked every revoked credential.

    A MISSING file is fail-closed too once the list has been initialised: the
    first write leaves the marker :data:`CRL_MARKER` next to it, and "marker
    present, ``crl.json`` absent" means the list was deleted - which used to
    read as "nothing revoked" and lifted every revocation at once. Without
    the marker (a store that never issued or revoked anything) a missing file
    is the empty list.

    Boundary: the marker lives in the same store. It catches a lost or removed
    ``crl.json`` (cleanup, partial restore, one deleted file); a process that
    can delete arbitrary files in the store can remove the marker as well -
    and could equally replace the authority key.

    Concurrency: every mutation is refresh -> change -> save under one
    :class:`StoreLock` (``licensing/.crl.lock``), so two processes revoking at
    the same time cannot each write a list that lacks the other's entry.
    Readers take the in-process half only.
    """

    def __init__(self, corvin_home: str):
        """Initialize revocation list.

        Args:
            corvin_home: Root directory (a tenant home in the console)
        """
        self.corvin_home = Path(corvin_home)
        secure_dir(self.corvin_home / "licensing")
        self.crl_file = self.corvin_home / "licensing" / "crl.json"
        self.marker_file = self.corvin_home / "licensing" / CRL_MARKER
        self.revoked: Dict[str, Dict[str, Any]] = {}
        self._loaded_mtime: Optional[float] = None
        #: True while the on-disk CRL is unreadable - everything is revoked.
        self.load_failed: bool = False
        self._lock = StoreLock.for_file(self.corvin_home / "licensing" / ".crl.lock")
        self._load()

    def _load(self) -> None:
        """Load revocation list from disk; a failure sets ``load_failed``."""
        try:
            exists = self.crl_file.exists()
        except OSError:
            exists = True  # cannot even stat it: treat as unreadable
        if not exists:
            self.revoked = {}
            self._loaded_mtime = None
            # Initialised once, gone now: deleted, not "empty".
            self.load_failed = self._initialised()
            if self.load_failed:
                logger.error(
                    "CRL file is missing although the list was initialised - "
                    "treating every credential as revoked"
                )
            return
        try:
            mtime = self.crl_file.stat().st_mtime_ns
            with open(self.crl_file, encoding="utf-8") as f:
                data = json.load(f)
            revoked = data["revoked"] if isinstance(data, dict) else None
            if not isinstance(revoked, dict) or not all(
                isinstance(k, str) and isinstance(v, dict) for k, v in revoked.items()
            ):
                raise ValueError("malformed CRL")
        except Exception as e:  # noqa: BLE001
            self.load_failed = True
            self._loaded_mtime = None
            logger.error(
                "Failed to load CRL (%s) - treating every credential as revoked",
                type(e).__name__,
            )
            return
        self.revoked = revoked
        self.load_failed = False
        self._loaded_mtime = mtime
        # A list written before the marker existed: mark it on first read, so
        # its later disappearance is fail-closed as well (best effort).
        if not self._initialised():
            try:
                atomic_write_private(self.marker_file, _utcnow_iso().encode("utf-8"))
            except OSError:
                pass
        logger.info("Loaded CRL with %d entries", len(self.revoked))

    def _refresh_if_stale(self) -> None:
        """Reload from disk when another process/instance wrote a newer CRL.

        A revocation made through one ``RevocationList`` instance (e.g. the
        authority server's) must be visible to every other instance (e.g. a
        verifier's) without waiting for it to be reconstructed - a revoked
        credential must stop verifying immediately, not after a restart.

        A MISSING file is always re-evaluated. "No file" equals the
        ``_loaded_mtime`` of an instance that never saw one, so comparing
        mtimes alone kept such an instance on its empty list after the list
        had been created, used and deleted - and it accepted a revoked
        credential. Only :meth:`_load` looks at the marker.
        """
        with self._lock.threads:
            try:
                mtime = self.crl_file.stat().st_mtime_ns
            except FileNotFoundError:
                mtime = None
            except OSError:
                self.load_failed = True
                return
            if mtime is None or self.load_failed or mtime != self._loaded_mtime:
                self._load()

    def _initialised(self) -> bool:
        try:
            return self.marker_file.exists()
        except OSError:
            return True  # cannot tell: fail closed

    def _save(self) -> None:
        """Save revocation list to disk atomically. Raises on failure."""
        payload = json.dumps(
            {"revoked": self.revoked, "timestamp": _utcnow_iso()},
            indent=2,
        ).encode("utf-8")
        atomic_write_private(self.crl_file, payload)
        self._loaded_mtime = self.crl_file.stat().st_mtime_ns
        # List first, marker second: a crash in between leaves an unmarked
        # list (harmless), never a marker without a list (everything revoked).
        if not self._initialised():
            atomic_write_private(self.marker_file, _utcnow_iso().encode("utf-8"))

    def ensure_initialised(self) -> None:
        """Create the (empty) list and its marker on first use.

        Called by the authority before it stores a credential, so from the
        first issuance on a missing ``crl.json`` is fail-closed. Never
        recreates a list that went missing after initialisation - that would
        be exactly the silent un-revocation the marker exists to prevent.
        """
        with self._lock:
            self._refresh_if_stale()
            if self.load_failed:
                raise RevocationListError(
                    "CRL is missing or unreadable; restore crl.json before issuing "
                    "(all credentials are treated as revoked until then)"
                )
            if not self._initialised() or not self.crl_file.exists():
                self._save()

    def revoke(self, credential_id: str, reason: str = "") -> None:
        """Revoke a credential. Raises when the revocation was not persisted.

        A CRL that cannot be read is NOT overwritten: doing so would silently
        drop every earlier revocation. The operator repairs or removes the
        file; until then everything is treated as revoked anyway.

        Refresh, change and save happen under the store lock: without it two
        writers each read the list, each added one id and the later save
        dropped the earlier revocation.
        """
        with self._lock:
            self._refresh_if_stale()
            if self.load_failed:
                raise RevocationListError(
                    "CRL is unreadable; refusing to overwrite it (all credentials "
                    "are treated as revoked until it is repaired)"
                )
            previous = self.revoked.get(credential_id)
            self.revoked[credential_id] = {
                "revoked_at": _utcnow_iso(),
                "reason": reason,
            }
            try:
                self._save()
            except Exception as e:
                # Keep memory and disk in agreement, then surface the failure.
                if previous is None:
                    self.revoked.pop(credential_id, None)
                else:
                    self.revoked[credential_id] = previous
                logger.error("Failed to save CRL: %s", type(e).__name__)
                raise RevocationListError("CRL could not be persisted") from e
        logger.info("Revoked credential %s", credential_id)

    def is_revoked(self, credential_id: str) -> bool:
        """Check if credential is revoked (``True`` while the CRL is unreadable)."""
        with self._lock.threads:
            self._refresh_if_stale()
            if self.load_failed:
                return True
            return credential_id in self.revoked

    def get_revocation_reason(self, credential_id: str) -> Optional[str]:
        """Get the revocation reason."""
        with self._lock.threads:
            self._refresh_if_stale()
            if self.load_failed:
                return "revocation list unreadable"
            entry = self.revoked.get(credential_id)
            return entry.get("reason") if entry else None

    def snapshot(self) -> Tuple[bool, List[Tuple[str, Dict[str, Any]]]]:
        """``(load_failed, entries)`` as one consistent copy for a reader.

        Iterating ``revoked`` directly races a concurrent revocation
        ("dictionary changed size during iteration").
        """
        with self._lock.threads:
            self._refresh_if_stale()
            return self.load_failed, [(k, dict(v)) for k, v in self.revoked.items()]


# ============================================================================
# Task replay store
# ============================================================================

class TaskReplayStore:
    """Single-use registry of verified task ids, kept until the task expires.

    One JSON file under ``<root>/licensing/task_nonces.json`` mapping
    ``"<credential_id>|<task_id>"`` to the epoch after which the entry may be
    forgotten. Keyed per credential so one member cannot burn another's ids.
    ``claim`` is check-and-insert under a :class:`StoreLock` (process lock
    plus advisory file lock) that belongs to THIS store's lock file - one
    tenant's verifications never queue behind another tenant's. Any I/O or
    parse error is a refusal (fail-closed).
    """

    def __init__(self, corvin_home: str):
        self.dir = secure_dir(Path(corvin_home) / "licensing")
        self.file = self.dir / "task_nonces.json"
        self.lock_file = self.dir / ".task_nonces.lock"
        self._lock = StoreLock.for_file(self.lock_file)

    def claim(self, credential_id: str, task_id: str, keep_until: float) -> bool:
        """``True`` exactly once per (credential, task) until *keep_until*."""
        key = f"{credential_id}|{task_id}"
        now = time.time()
        try:
            with self._lock:
                entries: Dict[str, float] = {}
                if self.file.exists():
                    with open(self.file, encoding="utf-8") as f:
                        loaded = json.load(f)
                    if not isinstance(loaded, dict):
                        raise ValueError("malformed replay store")
                    entries = {
                        str(k): float(v) for k, v in loaded.items() if float(v) > now
                    }
                if key in entries:
                    return False
                entries[key] = float(keep_until)
                atomic_write_private(self.file, json.dumps(entries).encode("utf-8"))
                return True
        except Exception as e:  # noqa: BLE001
            logger.error("Task replay store unavailable: %s", type(e).__name__)
            return False


# ============================================================================
# Verification Result
# ============================================================================

@dataclass
class VerificationResult:
    """Result of task verification."""

    is_valid: bool
    task_id: str
    member_id: str
    credential_id: str
    license_tier: str

    # Details
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    error_message: str = ""

    # Audit
    verified_at: str = ""
    verification_latency_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "is_valid": self.is_valid,
            "task_id": self.task_id,
            "member_id": self.member_id,
            "credential_id": self.credential_id,
            "license_tier": self.license_tier,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "error_message": self.error_message,
            "verified_at": self.verified_at,
            "verification_latency_ms": self.verification_latency_ms,
        }


# ============================================================================
# A2A Delegation Verifier
# ============================================================================

class A2ADelegationVerifier:
    """Verifies RSA-signed A2A delegation tasks.

    Implements fail-closed validation:
    - Ids must be well-formed
    - Credential must exist, belong to the claimed member and carry a valid
      AUTHORITY signature (no authority public key -> denied)
    - Credential must not be expired or revoked
    - License tier must be 'member' or 'enterprise'
    - Task signature must be valid (covers task_id, credential_id, ttl_seconds)
    - Task must not be expired (TTL capped server-side) and not replayed
    - Every outcome is a record in the tenant audit chain; a PASS whose
      record did not commit is turned into a denial
    """

    def __init__(self, corvin_home: str, *, tenant_id: str = "_default"):
        """Initialize verifier.

        Args:
            corvin_home: Root directory of the store (a tenant home in the console)
            tenant_id: Tenant whose audit chain receives the records
        """
        self.corvin_home = Path(corvin_home)
        self.tenant_id = tenant_id
        self.credential_store = CredentialStore(corvin_home)
        self.revocation_list = RevocationList(corvin_home)
        self.replay_store = TaskReplayStore(corvin_home)
        self.authority_public_key_file = (
            self.corvin_home / "licensing" / AUTHORITY_DIRNAME / AUTHORITY_PUBLIC_KEY
        )

    # ------------------------------------------------------------------ helpers

    def _deny(
        self,
        result: "VerificationResult",
        start_time: float,
        code: str,
        failed: str,
        message: str,
        operation: str = "",
    ) -> "VerificationResult":
        result.is_valid = False
        result.checks_failed.append(failed)
        result.error_message = message
        result.verification_latency_ms = int((time.time() - start_time) * 1000)
        self._audit(result, code, operation=operation)
        return result

    def _verify_credential_signature(self, credential: MemberCredential) -> Tuple[bool, str]:
        """Check the authority's signature on *credential*.

        Returns ``(ok, code)``. Missing or unreadable authority key material is
        a denial (``authority_key_unavailable``): a credential nobody can vouch
        for is not a credential. Credentials issued before 2026-11-19 were
        self-signed with their own key and therefore no longer verify - they
        have to be re-issued.
        """
        try:
            pem = self.authority_public_key_file.read_bytes()
            authority_key = RSAKeyPair.from_public_pem(pem)
        except Exception as e:  # noqa: BLE001
            logger.error("Authority public key unavailable: %s", type(e).__name__)
            return False, "authority_key_unavailable"
        try:
            signature = bytes.fromhex(credential.signature)
        except (TypeError, ValueError):
            return False, "credential_signature_invalid"
        if RSAKeyPair.verify(authority_key, credential_signing_bytes(credential), signature):
            return True, ""
        return False, "credential_signature_invalid"

    # --------------------------------------------------------------- verification

    def verify_signed_task(self, signed_task: SignedTask) -> VerificationResult:
        """Verify a signed delegation task.

        Args:
            signed_task: The signed task to verify

        Returns:
            VerificationResult with validation details
        """
        start_time = time.time()
        operation = signed_task.operation if isinstance(signed_task.operation, str) else ""

        # ====================================================================
        # Check 0: Ids Must Be Well-Formed
        # ====================================================================
        # Done before anything touches the filesystem or the audit chain. The
        # rejected values are not echoed into the result or the record.
        try:
            validate_task_id(signed_task.task_id)
            validate_member_id(signed_task.member_id)
            validate_credential_id(signed_task.credential_id)
        except InvalidLicensingId as e:
            result = VerificationResult(
                is_valid=False,
                task_id="",
                member_id="",
                credential_id="",
                license_tier="unknown",
            )
            return self._deny(result, start_time, "malformed_id", str(e), "Malformed identifier")

        result = VerificationResult(
            is_valid=False,
            task_id=signed_task.task_id,
            member_id=signed_task.member_id,
            credential_id=signed_task.credential_id,
            license_tier="unknown",
        )

        # ====================================================================
        # Check 1: Credential Must Exist
        # ====================================================================

        credential = self.credential_store.load(signed_task.credential_id)
        if not credential:
            return self._deny(
                result, start_time, "credential_not_found",
                f"Credential not found: {signed_task.credential_id}",
                "Credential not found", operation,
            )

        result.checks_passed.append("Credential found")

        # ====================================================================
        # Check 1b: Credential Must Belong To The Claimed Member
        # ====================================================================
        # Without this, a holder of ANY valid credential can sign a task that
        # claims an arbitrary member_id and have it verified against their own
        # key - full cross-member impersonation.
        if credential.member_id != signed_task.member_id:
            return self._deny(
                result, start_time, "credential_member_mismatch",
                "Credential does not belong to the claimed member",
                "Credential/member mismatch", operation,
            )
        result.checks_passed.append("Credential matches member")

        # ====================================================================
        # Check 1c: Credential Must Carry A Valid Authority Signature
        # ====================================================================
        # The credential file is the only source of the member's public key
        # and tier. Unless the authority vouches for exactly these bytes, a
        # writer of that file could mint itself an "enterprise" credential.
        ok, code = self._verify_credential_signature(credential)
        if not ok:
            message = (
                "Authority key unavailable"
                if code == "authority_key_unavailable"
                else "Invalid credential signature"
            )
            return self._deny(
                result, start_time, code,
                "Credential authority signature check failed", message, operation,
            )
        # Only now is the tier trustworthy enough to report.
        result.license_tier = credential.license_tier
        result.checks_passed.append("Credential signed by authority")

        # ====================================================================
        # Check 2: Credential Must Not Be Expired
        # ====================================================================

        if credential.is_expired():
            return self._deny(
                result, start_time, "credential_expired",
                f"Credential expired at {credential.expires_at}",
                "Credential has expired", operation,
            )

        result.checks_passed.append("Credential not expired")

        # ====================================================================
        # Check 3: Credential Must Not Be Revoked
        # ====================================================================

        if self.revocation_list.is_revoked(signed_task.credential_id):
            reason = self.revocation_list.get_revocation_reason(signed_task.credential_id)
            return self._deny(
                result, start_time, "credential_revoked",
                f"Credential revoked: {reason}",
                "Credential has been revoked", operation,
            )

        result.checks_passed.append("Credential not revoked")

        # ====================================================================
        # Check 4: License Tier Must Permit Delegation
        # ====================================================================

        if not credential.can_delegate():
            return self._deny(
                result, start_time, "insufficient_license_tier",
                f"License tier '{credential.license_tier}' does not permit delegation",
                "Insufficient license tier for A2A delegation", operation,
            )

        result.checks_passed.append("License tier permits delegation")

        # ====================================================================
        # Check 5: Task Signature Must Be Valid
        # ====================================================================

        if not self._verify_signature(signed_task, credential):
            return self._deny(
                result, start_time, "signature_invalid",
                "Signature verification failed", "Invalid task signature", operation,
            )

        result.checks_passed.append("Signature valid")

        # ====================================================================
        # Check 6: Task Must Not Be Expired (server-capped TTL)
        # ====================================================================

        ttl = signed_task.effective_ttl_seconds()
        if signed_task.is_expired():
            return self._deny(
                result, start_time, "task_expired",
                f"Task expired {ttl} seconds after creation "
                f"(server cap {MAX_TASK_TTL_SECONDS}s)",
                "Task has expired", operation,
            )

        result.checks_passed.append("Task not expired")

        # ====================================================================
        # Check 7: Task Must Not Be A Replay
        # ====================================================================
        # Last, so only an otherwise valid task consumes its id.
        signed_epoch = signed_task.signed_at_epoch() or time.time()
        keep_until = signed_epoch + ttl + MAX_TASK_CLOCK_SKEW_SECONDS
        if not self.replay_store.claim(signed_task.credential_id, signed_task.task_id, keep_until):
            return self._deny(
                result, start_time, "task_replayed",
                "Task id was already used (or the replay store is unavailable)",
                "Task replay refused", operation,
            )

        result.checks_passed.append("Task not replayed")

        # ====================================================================
        # Verification Passed
        # ====================================================================

        result.is_valid = True
        result.checks_passed.append("All checks passed")
        result.verified_at = _utcnow_iso()
        result.verification_latency_ms = int((time.time() - start_time) * 1000)

        if not self._audit(result, "verification_passed", operation=operation):
            # No record, no pass: an unaudited authorisation is not issued.
            result.is_valid = False
            result.checks_passed.remove("All checks passed")
            result.checks_failed.append("Audit record could not be written")
            result.error_message = "Audit trail unavailable"
            result.verified_at = ""
            return result

        logger.info("Verified task %s", signed_task.task_id)
        return result

    def _verify_signature(self, signed_task: SignedTask, credential: MemberCredential) -> bool:
        """Verify the member's RSA signature on the task.

        The signed message is :func:`task_signing_bytes` - it binds task_id,
        member_id, credential_id, operation, payload, signed_at and
        ttl_seconds.
        """
        try:
            message = task_signing_bytes(signed_task)
            public_key = RSAKeyPair.from_public_pem(
                credential.public_key_pem.encode()
            )
            signature_bytes = bytes.fromhex(signed_task.signature)
            return RSAKeyPair.verify(public_key, message, signature_bytes)

        except Exception as e:  # noqa: BLE001
            logger.error("Signature verification error: %s", type(e).__name__)
            return False

    def revoke_credential(self, credential_id: str, reason: str = "") -> None:
        """Revoke a credential (immediate effect). Raises if not persisted.

        Args:
            credential_id: The credential to revoke
            reason: Revocation reason
        """
        validate_credential_id(credential_id)
        self.revocation_list.revoke(credential_id, reason)
        self._audit_revocation(credential_id, reason)

    def _audit(self, result: VerificationResult, event_type: str, *, operation: str = "") -> bool:
        """Write one verification record to the tenant audit chain.

        Args:
            result: The verification result
            event_type: Outcome code (verification_passed, signature_invalid, ...)

        Returns:
            True when the record committed.
        """
        details = {
            "task_id": result.task_id or None,
            "member_id": result.member_id or None,
            "credential_id": result.credential_id or None,
            "license_tier": result.license_tier,
            "operation": operation[:64] if operation.isidentifier() else None,
            "latency_ms": result.verification_latency_ms,
        }
        if event_type == "verification_passed":
            return licensing_audit.emit(
                "a2a.license_task_verified", tenant_id=self.tenant_id, details=details,
            )
        details["reason_code"] = event_type
        return licensing_audit.emit(
            "a2a.license_task_denied", tenant_id=self.tenant_id, details=details,
        )

    def _audit_revocation(self, credential_id: str, reason: str) -> None:
        """Write revocation record (reason as a length, never as text)."""
        licensing_audit.emit(
            "a2a.license_credential_revoked",
            tenant_id=self.tenant_id,
            details={
                "credential_id": credential_id,
                "reason_len": len(reason or ""),
                "source": "verifier",
            },
        )

    def get_verification_audit(
        self,
        limit: int = 100,
        member_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get recent verification audit events of this verifier's tenant.

        Args:
            limit: Max events to return
            member_id: Filter by member (optional)

        Returns:
            List of audit events, newest first
        """
        return licensing_audit.read_verification_events(
            self.tenant_id, limit=limit, member_id=member_id,
        )
