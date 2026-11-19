"""Authority Server - Issue and Manage Credentials for A2A Licensing

Implements ADR-0704: Licensing Phase 2 - A2A RSA Gate
- Issue credentials to members with specified tier
- Maintain credential lifecycle
- Revoke compromised credentials
- Audit all credential operations
- Operator-only access (no public endpoint)

License: Apache-2.0
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any

from core.licensing import audit as licensing_audit
from core.licensing.member_credential import (
    MemberCredential,
    RSAKeyPair,
    LicenseTier,
    CredentialStore,
    InvalidLicensingId,
    atomic_write_private,
    credential_signing_bytes,
    secure_dir,
    validate_credential_id,
    validate_member_id,
    validate_task_id,
)
from core.licensing.a2a_verifier import (
    StoreLock,
    AUTHORITY_DIRNAME,
    AUTHORITY_PRIVATE_KEY,
    AUTHORITY_PUBLIC_KEY,
    RevocationList,
)

logger = logging.getLogger(__name__)

#: Tier order for :meth:`AuthorityServer.downgrade_tier` (higher = more rights).
TIER_RANK: Dict[str, int] = {
    LicenseTier.FREE.value: 0,
    LicenseTier.MEMBER.value: 1,
    LicenseTier.ENTERPRISE.value: 2,
}

#: Bounds for a credential's validity. 0 stays admissible ("already expired",
#: used for tests and for issuing a placeholder); negative or absurd values are not.
MIN_VALIDITY_DAYS = 0
MAX_VALIDITY_DAYS = 36500


# ============================================================================
# Credential Issuance Response
# ============================================================================

#: Appended to an error when the old credential is already revoked but its
#: successor was not stored - the member holds no usable credential.
_OLD_REVOKED_HINT = "the old credential is revoked; issue a new one"


@dataclass
class IssuanceResult:
    """Result of credential issuance.

    ``private_key_pem`` is set ONLY when the authority generated the member's
    keypair, and only on this one return value: it is never written to disk,
    never logged and deliberately absent from :meth:`to_dict`. The caller
    hands it to the member once; a lost key means "issue a new credential".
    """

    success: bool
    credential: Optional[MemberCredential] = None
    error_message: str = ""
    issue_id: str = ""  # Unique issuance tracking ID
    private_key_pem: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict (never includes the private key)."""
        return {
            "success": self.success,
            "credential": self.credential.to_dict() if self.credential else None,
            "error_message": self.error_message,
            "issue_id": self.issue_id,
        }

    def __repr__(self) -> str:  # keep the key out of logs and tracebacks
        return (
            f"IssuanceResult(success={self.success!r}, issue_id={self.issue_id!r}, "
            f"error_message={self.error_message!r}, "
            f"private_key_pem={'<set>' if self.private_key_pem else None})"
        )


# ============================================================================
# Authority Server
# ============================================================================

class AuthorityServer:
    """Issues and manages member credentials for A2A licensing.

    Responsibilities:
    - Sign every credential with the AUTHORITY key (``licensing/authority/``,
      private key 0o600) - the verifier checks that signature
    - Bind a member public key: submitted by the member, or generated here
      and handed back exactly once
    - Record every lifecycle step in the tenant audit chain, audit-first
    - Coordinate with revocation list
    """

    # Default credential validity period
    DEFAULT_VALIDITY_DAYS = 90

    def __init__(self, corvin_home: str, *, tenant_id: str = "_default"):
        """Initialize authority server.

        Args:
            corvin_home: Root directory of the store (a tenant home in the console)
            tenant_id: Tenant whose audit chain receives the records
        """
        self.corvin_home = Path(corvin_home)
        self.tenant_id = tenant_id
        self.credential_store = CredentialStore(corvin_home)
        self.revocation_list = RevocationList(corvin_home)
        self.authority_dir = self.corvin_home / "licensing" / AUTHORITY_DIRNAME
        #: ONE lock for every lifecycle step that reads the store and then
        #: changes it (renew, downgrade, revoke). Without it a renewal and a
        #: downgrade of the same credential interleaved: each saw "not
        #: revoked" and the renewal left a full-tier credential behind.
        self._store_lock = StoreLock.for_file(
            self.corvin_home / "licensing" / ".store.lock"
        )

    # ------------------------------------------------------------------ key

    def _authority_keypair(self) -> RSAKeyPair:
        """Load the authority keypair, creating it on first use.

        The private key is created with ``O_EXCL`` at 0o600 so two processes
        racing on first use cannot each keep a different key; the loser reads
        the winner's. The public half is (re)published next to it for the
        verifier.
        """
        secure_dir(self.authority_dir)
        key_file = self.authority_dir / AUTHORITY_PRIVATE_KEY
        pub_file = self.authority_dir / AUTHORITY_PUBLIC_KEY
        if not key_file.exists():
            fresh = RSAKeyPair.generate()
            try:
                fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as f:
                    f.write(fresh.private_pem())
                    f.flush()
                    os.fsync(f.fileno())
        keypair = RSAKeyPair.from_private_pem(key_file.read_bytes())
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        public_pem = keypair.public_pem()
        try:
            current = pub_file.read_bytes()
        except OSError:
            current = b""
        if current != public_pem:
            atomic_write_private(pub_file, public_pem)
        return keypair

    def signature_valid(self, credential: MemberCredential) -> bool:
        """True when *credential* carries THIS authority's signature.

        Read-only: unlike :meth:`_authority_keypair` it never creates a key.
        No authority key, an unreadable key or a malformed signature is False.
        A stored credential is a file; anything read back from the store is
        checked here before it is re-signed or reported as usable.
        """
        key_file = self.authority_dir / AUTHORITY_PRIVATE_KEY
        try:
            keypair = RSAKeyPair.from_private_pem(key_file.read_bytes())
            signature = bytes.fromhex(credential.signature or "")
        except Exception:  # noqa: BLE001 - no key / unreadable key / not hex
            return False
        if not signature:
            return False
        return RSAKeyPair.verify(
            keypair.public_key, credential_signing_bytes(credential), signature
        )

    def _sign(self, credential: MemberCredential) -> None:
        credential.signature = self._authority_keypair().sign(
            credential_signing_bytes(credential)
        ).hex()

    # ---------------------------------------------------------------- issuance

    def issue_credential(
        self,
        member_id: str,
        license_tier: str,
        validity_days: int = DEFAULT_VALIDITY_DAYS,
        metadata: Optional[Dict[str, Any]] = None,
        public_key_pem: Optional[str] = None,
    ) -> IssuanceResult:
        """Issue a new credential to a member.

        Args:
            member_id: The member receiving the credential
            license_tier: The license tier (free, member, enterprise)
            validity_days: How many days until expiry (default 90)
            metadata: Optional metadata (e.g., organization, contact)
            public_key_pem: The member's own RSA public key. When given, no
                private key ever exists on this host. When omitted, a keypair
                is generated and its private half returned once in
                ``IssuanceResult.private_key_pem``.

        Returns:
            IssuanceResult with credential or error
        """
        issue_id = str(uuid.uuid4())

        try:
            validate_member_id(member_id)
        except InvalidLicensingId as e:
            return IssuanceResult(success=False, error_message=str(e), issue_id=issue_id)

        # Validate license tier
        if license_tier not in TIER_RANK:
            return IssuanceResult(
                success=False,
                error_message="Invalid license tier",
                issue_id=issue_id,
            )
        if (
            isinstance(validity_days, bool)
            or not isinstance(validity_days, int)
            or not MIN_VALIDITY_DAYS <= validity_days <= MAX_VALIDITY_DAYS
        ):
            return IssuanceResult(
                success=False,
                error_message=(
                    f"validity_days must be an integer in "
                    f"[{MIN_VALIDITY_DAYS}, {MAX_VALIDITY_DAYS}]"
                ),
                issue_id=issue_id,
            )

        try:
            private_key_pem: Optional[str] = None
            if public_key_pem is not None:
                try:
                    loaded = RSAKeyPair.from_public_pem(public_key_pem.encode())
                    if getattr(loaded, "key_size", 0) < RSAKeyPair.BITS:
                        raise ValueError("key too small")
                    # Re-serialise: the stored PEM is canonical, not caller text.
                    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
                    member_public_pem = loaded.public_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PublicFormat.SubjectPublicKeyInfo,
                    ).decode()
                except Exception:  # noqa: BLE001
                    return IssuanceResult(
                        success=False,
                        error_message=(
                            f"public_key_pem must be an RSA public key of at "
                            f"least {RSAKeyPair.BITS} bits"
                        ),
                        issue_id=issue_id,
                    )
                key_origin = "submitted"
            else:
                member_keypair = RSAKeyPair.generate()
                member_public_pem = member_keypair.public_pem().decode()
                private_key_pem = member_keypair.private_pem().decode()
                key_origin = "generated"

            # Create credential
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            expiry = now + timedelta(days=validity_days)

            credential = MemberCredential(
                member_id=member_id,
                license_tier=license_tier,
                issued_at=now.isoformat() + "Z",
                expires_at=expiry.isoformat() + "Z",
                public_key_pem=member_public_pem,
                issuer="corvin_authority",
                metadata=metadata or {},
            )

            # Signed by the AUTHORITY key - not by the member's own key, which
            # proved nothing (anyone can self-sign).
            self._sign(credential)

            # From the first issuance on, a missing CRL file is fail-closed.
            self.revocation_list.ensure_initialised()

            # Audit FIRST: no chain record, no credential.
            self._audit_issuance(
                credential, issue_id, "success",
                key_origin=key_origin, validity_days=validity_days,
            )

            # Persist credential
            self.credential_store.save(credential)

            logger.info(
                "Issued credential %s (tier=%s, expires=%s)",
                credential.credential_id, license_tier, expiry.date(),
            )

            return IssuanceResult(
                success=True,
                credential=credential,
                issue_id=issue_id,
                private_key_pem=private_key_pem,
            )

        except Exception as e:
            logger.error("Failed to issue credential: %s", type(e).__name__)
            try:
                self._audit_issuance(
                    None,
                    issue_id,
                    "error",
                    member_id=member_id,
                    license_tier=license_tier,
                    strict=False,
                )
            except Exception:  # noqa: BLE001
                pass
            return IssuanceResult(
                success=False,
                error_message=f"Issuance failed: {type(e).__name__}",
                issue_id=issue_id,
            )

    def renew_credential(
        self,
        credential_id: str,
        validity_days: int = DEFAULT_VALIDITY_DAYS,
    ) -> IssuanceResult:
        """Renew an existing credential.

        The renewed credential keeps the member's PUBLIC key (the member keeps
        signing with the key they already hold), so no private key is returned.

        Renewal REPLACES: the old id is revoked. It used to stay valid next to
        its successor, so a later downgrade of the successor left the old,
        higher-tier credential usable - and renewable again.

        Args:
            credential_id: The credential to renew
            validity_days: New validity period

        Returns:
            IssuanceResult with new credential or error
        """
        with self._store_lock:
            return self._renew_locked(credential_id, validity_days)

    def _renew_locked(self, credential_id: str, validity_days: int) -> IssuanceResult:
        # Load existing credential
        old_credential = self.credential_store.load(credential_id)
        if not old_credential:
            return IssuanceResult(
                success=False,
                error_message="Credential not found",
            )
        # Verify BEFORE re-signing. Renewal copies member, tier and public key
        # from the stored file into a freshly signed credential: an edited
        # file (tier raised, key swapped) was laundered into a valid one.
        if not self.signature_valid(old_credential):
            logger.error("Refusing to renew a credential whose authority signature does not verify")
            return IssuanceResult(
                success=False,
                error_message="Credential signature is invalid and cannot be renewed",
            )
        if self.revocation_list.is_revoked(credential_id):
            return IssuanceResult(
                success=False,
                error_message="Credential is revoked and cannot be renewed",
            )
        # Checked here as well as in issue_credential: a bad argument must be
        # refused BEFORE the old credential is revoked.
        if (
            isinstance(validity_days, bool)
            or not isinstance(validity_days, int)
            or not MIN_VALIDITY_DAYS <= validity_days <= MAX_VALIDITY_DAYS
        ):
            return IssuanceResult(
                success=False,
                error_message=(
                    f"validity_days must be an integer in "
                    f"[{MIN_VALIDITY_DAYS}, {MAX_VALIDITY_DAYS}]"
                ),
            )

        # Revoke first (audit-first), then issue: a failure in between leaves
        # the member without a credential, never with two.
        try:
            self._audit_revocation(
                old_credential, "superseded by renewal", source="renewal"
            )
            self.revocation_list.revoke(credential_id, "superseded by renewal")
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to renew credential: %s", type(e).__name__)
            return IssuanceResult(
                success=False,
                error_message=f"Renewal failed: {type(e).__name__}",
            )

        # Issue new credential with same properties
        result = self.issue_credential(
            member_id=old_credential.member_id,
            license_tier=old_credential.license_tier,
            validity_days=validity_days,
            metadata=old_credential.metadata,
            public_key_pem=old_credential.public_key_pem,
        )

        if result.success:
            logger.info("Renewed credential %s", credential_id)
            self._audit_renewal(credential_id, result.credential)
        else:
            result.error_message = (
                f"{result.error_message}; {_OLD_REVOKED_HINT}"
            )

        return result

    def downgrade_tier(
        self,
        credential_id: str,
        new_tier: str,
    ) -> IssuanceResult:
        """Downgrade a credential's license tier.

        Strictly downward in :data:`TIER_RANK` - this method used to accept
        any tier, so "downgrade" from ``free`` to ``enterprise`` was an
        unaudited upgrade path. Raising a tier means issuing a new credential.

        The downgrade issues a NEW credential (new ``credential_id``, same
        member key and expiry, lower tier) and REVOKES the old id. It used to
        re-sign the same id in place: the earlier file, still carrying a valid
        authority signature for the higher tier, verified again as soon as it
        was copied back over the store entry (rollback). The old id is revoked
        before the new credential is stored, so a failure in between leaves
        the member with no credential rather than with the old tier.

        A downgrade is a statement about the MEMBER, not about one file: every
        other unrevoked credential of the member above the new tier is revoked
        with it. Revoking only the named id left a sibling (an earlier
        credential the named one was renewed from) at the old tier.

        Args:
            credential_id: The credential to downgrade
            new_tier: The new license tier

        Returns:
            IssuanceResult whose ``credential`` is the NEW credential, or an error
        """
        with self._store_lock:
            return self._downgrade_locked(credential_id, new_tier)

    def _downgrade_locked(self, credential_id: str, new_tier: str) -> IssuanceResult:
        # Load existing credential
        old = self.credential_store.load(credential_id)
        if not old:
            return IssuanceResult(
                success=False,
                error_message="Credential not found",
            )

        # Validate new tier
        if new_tier not in TIER_RANK:
            return IssuanceResult(
                success=False,
                error_message="Invalid license tier",
            )

        # Verify BEFORE re-signing: the stored file supplies the member key
        # and the expiry of the new credential.
        if not self.signature_valid(old):
            logger.error("Refusing to downgrade a credential whose authority signature does not verify")
            return IssuanceResult(
                success=False,
                error_message="Credential signature is invalid and cannot be downgraded",
            )
        if self.revocation_list.is_revoked(credential_id):
            return IssuanceResult(
                success=False,
                error_message="Credential is revoked and cannot be downgraded",
            )

        old_tier = old.license_tier
        old_rank = TIER_RANK.get(old_tier)
        if old_rank is None or TIER_RANK[new_tier] >= old_rank:
            return IssuanceResult(
                success=False,
                error_message=(
                    f"Not a downgrade: '{old_tier}' -> '{new_tier}'. "
                    "Issue a new credential to raise a tier."
                ),
            )

        # The named credential first, then every sibling above the new tier.
        # A sibling with an unknown tier is revoked too (cannot be ranked).
        superseded = [old]
        for sibling in self.credential_store.list_for_member(old.member_id):
            if sibling.credential_id == credential_id:
                continue
            rank = TIER_RANK.get(sibling.license_tier)
            if rank is not None and rank <= TIER_RANK[new_tier]:
                continue
            if self.revocation_list.is_revoked(sibling.credential_id):
                continue
            superseded.append(sibling)

        revoked_any = False
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            credential = MemberCredential(
                member_id=old.member_id,
                license_tier=new_tier,
                issued_at=now.isoformat() + "Z",
                expires_at=old.expires_at,
                public_key_pem=old.public_key_pem,
                issuer=old.issuer,
                metadata=dict(old.metadata or {}),
            )
            self._sign(credential)
            # Audit first, then revoke the old ids, then store the new one.
            self._audit_downgrade(credential, old_tier, new_tier, previous_id=credential_id)
            for victim in superseded:
                self._audit_revocation(victim, "superseded by downgrade", source="downgrade")
                self.revocation_list.revoke(
                    victim.credential_id, f"superseded by downgrade to {new_tier}"
                )
                revoked_any = True
            self.credential_store.save(credential)
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to downgrade credential: %s", type(e).__name__)
            message = f"Downgrade failed: {type(e).__name__}"
            if revoked_any:
                # Say what state the member is in: without this the caller
                # retried the downgrade and got "revoked and cannot be
                # downgraded" with no way to tell why.
                message = f"{message}; {_OLD_REVOKED_HINT}"
            return IssuanceResult(
                success=False,
                error_message=message,
            )

        logger.info(
            "Downgraded credential %s from %s to %s as %s (%d credential(s) revoked)",
            credential_id, old_tier, new_tier, credential.credential_id, len(superseded),
        )

        return IssuanceResult(
            success=True,
            credential=credential,
        )

    def record_delegation_usage(
        self,
        task_id: str,
        credential_id: str,
        member_id: str,
        operation: str,
        result: str,  # "success" or "failure"
    ) -> None:
        """Record when a credential was used for delegation.

        Args:
            task_id: The delegated task ID
            credential_id: The credential used
            member_id: The member that delegated
            operation: The operation performed
            result: Whether it succeeded
        """
        try:
            validate_task_id(task_id)
            validate_credential_id(credential_id)
            validate_member_id(member_id)
        except InvalidLicensingId:
            logger.error("Refusing to record delegation usage with a malformed id")
            return
        licensing_audit.emit(
            "a2a.license_delegation_recorded",
            tenant_id=self.tenant_id,
            details={
                "task_id": task_id,
                "credential_id": credential_id,
                "member_id": member_id,
                "operation": operation[:64] if str(operation).isidentifier() else None,
                "status": "success" if result == "success" else "failure",
            },
        )

    def get_credential_info(self, credential_id: str) -> Optional[Dict[str, Any]]:
        """Get credential info (for management UI).

        Args:
            credential_id: The credential to look up

        Returns:
            Credential details or None if not found
        """
        credential = self.credential_store.load(credential_id)
        if not credential:
            return None

        is_revoked = self.revocation_list.is_revoked(credential_id)
        revocation_reason = self.revocation_list.get_revocation_reason(credential_id)
        # The fields below are read from a file. Without the authority's
        # signature over them they say nothing: an edited tier used to be
        # reported as "can delegate".
        signature_valid = self.signature_valid(credential)

        return {
            "credential_id": credential.credential_id,
            "member_id": credential.member_id,
            "license_tier": credential.license_tier,
            "issued_at": credential.issued_at,
            "expires_at": credential.expires_at,
            "is_expired": credential.is_expired(),
            "remaining_ttl_seconds": credential.get_remaining_ttl_seconds(),
            "is_revoked": is_revoked,
            "revocation_reason": revocation_reason,
            "signature_valid": signature_valid,
            "is_valid": signature_valid and credential.is_valid() and not is_revoked,
            "can_delegate": signature_valid and credential.can_delegate() and not is_revoked,
        }

    def list_member_credentials(
        self,
        member_id: str,
        include_expired: bool = False,
    ) -> List[Dict[str, Any]]:
        """List all credentials for a member.

        Args:
            member_id: The member to query
            include_expired: Include expired credentials

        Returns:
            List of credential info dicts
        """
        credentials = self.credential_store.list_for_member(member_id)
        result = []

        for credential in credentials:
            if credential.is_expired() and not include_expired:
                continue

            info = self.get_credential_info(credential.credential_id)
            if info:
                result.append(info)

        return result

    def revoke_credential(
        self,
        credential_id: str,
        reason: str = "Revoked by authority",
    ) -> bool:
        """Revoke a credential (immediate effect).

        Args:
            credential_id: The credential to revoke
            reason: Revocation reason

        Returns:
            True if the revocation was recorded AND persisted
        """
        try:
            with self._store_lock:
                credential = self.credential_store.load(credential_id)
                if not credential:
                    logger.warning("Cannot revoke: credential not found")
                    return False

                self._audit_revocation(credential, reason)
                self.revocation_list.revoke(credential_id, reason)
            logger.info("Revoked credential %s", credential_id)

            return True

        except Exception as e:
            logger.error("Failed to revoke credential: %s", type(e).__name__)
            return False

    # ========================================================================
    # Audit Logging (tenant audit chain, audit-first)
    # ========================================================================

    def _audit_issuance(
        self,
        credential: Optional[MemberCredential],
        issue_id: str,
        status: str,
        member_id: Optional[str] = None,
        license_tier: Optional[str] = None,
        key_origin: Optional[str] = None,
        validity_days: Optional[int] = None,
        strict: bool = True,
    ) -> None:
        """Audit credential issuance event."""
        licensing_audit.emit(
            "a2a.license_credential_issued",
            tenant_id=self.tenant_id,
            strict=strict,
            details={
                "issue_id": issue_id,
                "status": status,
                "member_id": credential.member_id if credential else member_id,
                "credential_id": credential.credential_id if credential else None,
                "license_tier": credential.license_tier if credential else license_tier,
                "key_origin": key_origin,
                "validity_days": validity_days,
            },
        )

    def _audit_renewal(self, old_id: str, new_credential: MemberCredential) -> None:
        """Audit credential renewal event (the issuance itself is already recorded)."""
        licensing_audit.emit(
            "a2a.license_credential_renewed",
            tenant_id=self.tenant_id,
            details={
                "previous_credential_id": old_id,
                "credential_id": new_credential.credential_id,
                "member_id": new_credential.member_id,
                "license_tier": new_credential.license_tier,
            },
        )

    def _audit_downgrade(
        self, credential: MemberCredential, old_tier: str, new_tier: str,
        *, previous_id: Optional[str] = None,
    ) -> None:
        """Audit credential tier downgrade event (audit-first)."""
        licensing_audit.emit(
            "a2a.license_credential_downgraded",
            tenant_id=self.tenant_id,
            strict=True,
            details={
                "credential_id": credential.credential_id,
                "previous_credential_id": previous_id,
                "member_id": credential.member_id,
                "license_tier": new_tier,
                "old_tier": old_tier,
                "new_tier": new_tier,
            },
        )

    def _audit_revocation(
        self, credential: MemberCredential, reason: str, *, source: str = "authority",
    ) -> None:
        """Audit credential revocation event (audit-first; reason as a length)."""
        licensing_audit.emit(
            "a2a.license_credential_revoked",
            tenant_id=self.tenant_id,
            strict=True,
            details={
                "credential_id": credential.credential_id,
                "member_id": credential.member_id,
                "license_tier": credential.license_tier,
                "reason_len": len(reason or ""),
                "source": source,
            },
        )
