"""Phase 3 k=4: Plugin Signing & Verification — Ed25519 Signatures (ADR-0775).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) —
``grep -rn PluginSignatureVerifier`` outside tests finds nothing. Until
2026-09-27 ``verify_plugin_signature`` set ``verified = True  # Placeholder``
for every Corvin and maintainer key, so ANY bytes passed as a signature
"verified", and ``revoke_certificate`` never marked the certificate revoked.
It now performs a real Ed25519 verification (``cryptography``) over the
canonical manifest bytes and fails closed on anything else. The built-in
"Corvin root" certificate carries no real public key (placeholder), so nothing
verifies against it until a real key is provisioned.

This module provides cryptographic signing and verification for plugins:
1. Generate Ed25519 signatures over manifest + code hash
2. Verify signatures at load time (tripwire checks before execution)
3. Certificate pinning (trust only Corvin + maintainer keys)
4. Audit events: plugin_load, signature_check, certificate_validation

Fail-closed: unsigned / mis-signed plugins raise SignatureVerificationError.
Audit-first: every signature check outcome is recorded on the tenant chain
(``marketplace.plugin_signature_checked`` via ``core/deployment/audit_sink.py``)
before the result is returned or raised; ``AuditWriteFailed`` propagates.
Tenant-scoped: all events filtered by tenant_id (GDPR Art. 32).
"""

from __future__ import annotations

import hashlib
import logging
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from base64 import b64encode, b64decode
import dataclasses

from core.deployment import audit_sink

logger = logging.getLogger(__name__)

audit_sink.register_events({
    "marketplace.plugin_signature_checked": frozenset(
        {"plugin_id", "status", "signature_key_id", "cert_type"}),
    "marketplace.signing_certificate_revoked": frozenset({"signature_key_id", "cert_type"}),
})


def canonical_manifest_bytes(manifest: "PluginManifest") -> bytes:
    """The exact bytes a plugin signature covers (timestamp excluded)."""
    return json.dumps(
        {
            "plugin_id": manifest.plugin_id,
            "version": manifest.version,
            "source_url": manifest.source_url,
            "tier": manifest.tier,
            "code_hash": manifest.code_hash,
            "author": manifest.author,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def manifest_digest(manifest: "PluginManifest") -> str:
    """Base64 SHA-256 of :func:`canonical_manifest_bytes`."""
    return b64encode(hashlib.sha256(canonical_manifest_bytes(manifest)).digest()).decode("ascii")


def _ed25519_verify(public_key_b64: str, signature_b64: str, message: bytes) -> bool:
    """True only for a valid Ed25519 signature; any decode/verify error → False."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pub = Ed25519PublicKey.from_public_bytes(b64decode(public_key_b64, validate=True))
        pub.verify(b64decode(signature_b64, validate=True), message)
        return True
    except Exception:  # noqa: BLE001 — InvalidSignature, bad base64, bad key length, no crypto
        return False


class SignatureAlgorithm(Enum):
    """Supported signature algorithms."""

    ED25519 = "ed25519"  # EdDSA with Ed25519 curve


class KeyType(Enum):
    """Classification of signing keys."""

    CORVIN_ROOT = "corvin_root"  # Corvin Labs root key (highest trust)
    CORVIN_RELEASE = "corvin_release"  # Corvin Labs release signing key
    MAINTAINER = "maintainer"  # Plugin maintainer key (lower trust, can be revoked)


class SignatureVerificationError(Exception):
    """Raised when signature verification fails."""

    pass


class CertificateNotFoundError(Exception):
    """Raised when signing certificate not found."""

    pass


class CertificateRevokedError(Exception):
    """Raised when certificate has been revoked."""

    pass


@dataclass(frozen=True)
class PluginManifest:
    """Immutable plugin manifest for signing."""

    plugin_id: str
    version: str
    source_url: str
    tier: str  # "buildin" | "vetted" | "community"
    code_hash: str  # SHA256 hash of plugin code (base64)
    author: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class SigningCertificate:
    """Immutable signing certificate (public key)."""

    key_id: str
    key_type: KeyType
    public_key: str  # Ed25519 public key (base64)
    subject: str  # Who owns this key (e.g., "Corvin Labs", "plugin-author")
    issued_at: datetime
    expires_at: Optional[datetime]  # None = no expiry (root key)
    revoked: bool = False
    revocation_time: Optional[datetime] = None


@dataclass(frozen=True)
class PluginSignature:
    """Immutable plugin signature."""

    plugin_id: str
    signature: str  # Ed25519 signature (base64)
    manifest_hash: str  # SHA256 of manifest (base64)
    signing_key_id: str  # Which certificate signed this
    algorithm: SignatureAlgorithm
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class SignatureCheckEvent:
    """Immutable audit event for signature verification (ADR-0232)."""

    plugin_id: str
    status: str  # "verified" | "failed" | "signature_invalid" | "cert_not_found"
    signature_key_id: str
    cert_type: Optional[KeyType]
    tenant_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    error_msg: Optional[str] = None


class PluginSignatureVerifier:
    """Verifies Ed25519 signatures over plugin manifests (ADR-0775).

    Fail-closed: invalid signatures raise SignatureVerificationError.
    Audit-first: every verification is logged.
    Certificate pinning: only Corvin + trusted maintainer keys.
    """

    # In production, these would be stored in a certificate store
    # For now, we use hard-coded test certificates
    DEFAULT_CORVIN_ROOT_KEY = {
        "key_id": "corvin-root-2026",
        "key_type": KeyType.CORVIN_ROOT,
        "public_key": "corvin_root_ed25519_public_key_placeholder",
        "subject": "Corvin Labs",
        "issued_at": datetime.now(),
        "expires_at": None,
        "revoked": False,
    }

    def __init__(self, tenant_id: str):
        """Initialize verifier.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)

        Raises:
            ValueError: If tenant_id missing (fail-closed)
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")

        self.tenant_id = tenant_id
        self._certificate_store: dict[str, SigningCertificate] = {}
        self._verification_history: list[SignatureCheckEvent] = []
        self._revoked_certificates: set[str] = set()

        # Load default Corvin root certificate
        self._register_certificate(
            key_id=self.DEFAULT_CORVIN_ROOT_KEY["key_id"],
            key_type=KeyType.CORVIN_ROOT,
            public_key=self.DEFAULT_CORVIN_ROOT_KEY["public_key"],
            subject=self.DEFAULT_CORVIN_ROOT_KEY["subject"],
            issued_at=self.DEFAULT_CORVIN_ROOT_KEY["issued_at"],
            expires_at=self.DEFAULT_CORVIN_ROOT_KEY["expires_at"],
        )

    def _register_certificate(
        self,
        key_id: str,
        key_type: KeyType,
        public_key: str,
        subject: str,
        issued_at: datetime,
        expires_at: Optional[datetime] = None,
    ) -> SigningCertificate:
        """Register a signing certificate (internal use only).

        Args:
            key_id: Unique key identifier
            key_type: Type of key (CORVIN_ROOT/CORVIN_RELEASE/MAINTAINER)
            public_key: Ed25519 public key (base64)
            subject: Certificate subject (who owns this key)
            issued_at: When key was issued
            expires_at: When key expires (None = no expiry)

        Returns:
            Immutable SigningCertificate
        """
        cert = SigningCertificate(
            key_id=key_id,
            key_type=key_type,
            public_key=public_key,
            subject=subject,
            issued_at=issued_at,
            expires_at=expires_at,
            revoked=False,
        )
        self._certificate_store[key_id] = cert
        return cert

    def register_maintainer_key(
        self,
        key_id: str,
        public_key: str,
        subject: str,
        expires_in_days: int = 365,
    ) -> SigningCertificate:
        """Register a plugin maintainer's signing key.

        Args:
            key_id: Unique key identifier (e.g., "maintainer-author-2026")
            public_key: Ed25519 public key (base64)
            subject: Maintainer name
            expires_in_days: Key expiry (default 1 year)

        Returns:
            Immutable SigningCertificate

        Raises:
            ValueError: If key_id or public_key missing (fail-closed)
        """
        if not key_id or not public_key:
            raise ValueError("key_id and public_key required")

        issued_at = datetime.now()
        expires_at = issued_at + timedelta(days=expires_in_days)

        return self._register_certificate(
            key_id=key_id,
            key_type=KeyType.MAINTAINER,
            public_key=public_key,
            subject=subject,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def verify_plugin_signature(
        self,
        plugin_signature: PluginSignature,
        manifest: PluginManifest,
    ) -> bool:
        """Verify plugin signature (fail-closed on any error).

        Algorithm:
        1. Lookup signing certificate by key_id
        2. Check certificate is not revoked
        3. Check certificate is not expired
        4. Verify Ed25519 signature over manifest
        5. Audit log result (success or failure)

        Args:
            plugin_signature: PluginSignature to verify
            manifest: PluginManifest that was signed

        Returns:
            True if verification succeeded

        Raises:
            SignatureVerificationError: If verification fails (fail-closed)
            CertificateNotFoundError: If signing certificate not found
            CertificateRevokedError: If certificate is revoked
        """
        key_id = plugin_signature.signing_key_id

        try:
            # Lookup certificate
            if key_id not in self._certificate_store:
                event = SignatureCheckEvent(
                    plugin_id=plugin_signature.plugin_id,
                    status="cert_not_found",
                    signature_key_id=key_id,
                    cert_type=None,
                    tenant_id=self.tenant_id,
                    error_msg=f"Certificate {key_id} not found",
                )
                self._record_check(event)
                logger.error(f"Signature verification failed: cert {key_id} not found")
                raise CertificateNotFoundError(f"Certificate {key_id} not found")

            cert = self._certificate_store[key_id]

            # Check revocation
            if cert.revoked:
                event = SignatureCheckEvent(
                    plugin_id=plugin_signature.plugin_id,
                    status="failed",
                    signature_key_id=key_id,
                    cert_type=cert.key_type,
                    tenant_id=self.tenant_id,
                    error_msg=f"Certificate {key_id} is revoked",
                )
                self._record_check(event)
                logger.error(f"Signature verification failed: cert {key_id} revoked")
                raise CertificateRevokedError(f"Certificate {key_id} is revoked")

            # Check expiry
            if cert.expires_at and datetime.now() > cert.expires_at:
                event = SignatureCheckEvent(
                    plugin_id=plugin_signature.plugin_id,
                    status="failed",
                    signature_key_id=key_id,
                    cert_type=cert.key_type,
                    tenant_id=self.tenant_id,
                    error_msg=f"Certificate {key_id} is expired",
                )
                self._record_check(event)
                logger.error(f"Signature verification failed: cert {key_id} expired")
                raise SignatureVerificationError(f"Certificate {key_id} is expired")

            # Real verification: the signature must bind THIS plugin id and
            # THIS manifest, and be a valid Ed25519 signature by the cert key.
            verified = (
                plugin_signature.algorithm == SignatureAlgorithm.ED25519
                and plugin_signature.plugin_id == manifest.plugin_id
                and plugin_signature.manifest_hash == manifest_digest(manifest)
                and _ed25519_verify(cert.public_key, plugin_signature.signature,
                                    canonical_manifest_bytes(manifest))
            )

            if not verified:
                event = SignatureCheckEvent(
                    plugin_id=plugin_signature.plugin_id,
                    status="signature_invalid",
                    signature_key_id=key_id,
                    cert_type=cert.key_type,
                    tenant_id=self.tenant_id,
                    error_msg="Ed25519 signature verification failed",
                )
                self._record_check(event)
                logger.error(
                    f"Signature verification failed: invalid signature for {plugin_signature.plugin_id}"
                )
                raise SignatureVerificationError("Ed25519 signature verification failed")

            # Success
            event = SignatureCheckEvent(
                plugin_id=plugin_signature.plugin_id,
                status="verified",
                signature_key_id=key_id,
                cert_type=cert.key_type,
                tenant_id=self.tenant_id,
            )
            self._record_check(event)
            logger.info(
                f"Signature verified: {plugin_signature.plugin_id} "
                f"(key={key_id}, cert_type={cert.key_type.value})"
            )

            return True

        except (CertificateNotFoundError, CertificateRevokedError, SignatureVerificationError,
                audit_sink.AuditWriteFailed):
            raise
        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            raise SignatureVerificationError(str(e))

    def _record_check(self, event: SignatureCheckEvent) -> None:
        """Chain record FIRST, then the in-memory read model."""
        audit_sink.emit(
            "marketplace.plugin_signature_checked",
            {
                "plugin_id": event.plugin_id,
                "status": event.status,
                "signature_key_id": event.signature_key_id,
                "cert_type": event.cert_type.value if event.cert_type else "",
                "lom": "PluginSignatureVerifier.verify_plugin_signature",
            },
            tenant_id=self.tenant_id,
            severity="INFO" if event.status == "verified" else "WARNING",
        )
        self._verification_history.append(event)

    def revoke_certificate(self, key_id: str, reason: str = "") -> None:
        """Revoke a signing certificate (MAINTAINER keys only).

        Args:
            key_id: Certificate to revoke
            reason: Reason for revocation (logged)

        Raises:
            ValueError: If key is CORVIN_ROOT (cannot revoke root)
        """
        if key_id not in self._certificate_store:
            logger.warning(f"Cannot revoke {key_id}: not found")
            return

        cert = self._certificate_store[key_id]

        # Cannot revoke Corvin root keys
        if cert.key_type == KeyType.CORVIN_ROOT:
            raise ValueError("Cannot revoke Corvin root certificate")

        audit_sink.emit(
            "marketplace.signing_certificate_revoked",
            {"signature_key_id": key_id, "cert_type": cert.key_type.value,
             "lom": "PluginSignatureVerifier.revoke_certificate"},
            tenant_id=self.tenant_id,
            severity="WARNING",
        )
        # The certificate itself is marked revoked — ``verify_plugin_signature``
        # checks ``cert.revoked``. Only adding the id to a side set (as before)
        # left a revoked key fully trusted.
        self._certificate_store[key_id] = dataclasses.replace(
            cert, revoked=True, revocation_time=datetime.now())
        self._revoked_certificates.add(key_id)
        logger.warning(f"Revoked certificate {key_id} ({reason})")

    def get_verification_history(self) -> list[SignatureCheckEvent]:
        """Get signature verification history (audit trail, read-only).

        Returns:
            List of SignatureCheckEvent in chronological order
        """
        return list(self._verification_history)

    def get_certificate(self, key_id: str) -> Optional[SigningCertificate]:
        """Get signing certificate (read-only).

        Args:
            key_id: Certificate to query

        Returns:
            SigningCertificate or None if not found
        """
        return self._certificate_store.get(key_id)

    def get_all_certificates(self) -> list[SigningCertificate]:
        """Get all registered certificates (read-only).

        Returns:
            List of SigningCertificate
        """
        return list(self._certificate_store.values())
