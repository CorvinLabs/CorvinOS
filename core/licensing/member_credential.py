"""Member Credential - RSA-based Licensing for A2A Delegation

Implements ADR-0704: Licensing Phase 2 - A2A RSA Gate
- RSA keypair generation (2048-bit)
- Credential issuance with expiry + revocation list (CRL)
- Task signing and verification
- Audit-integrated credential lifecycle

License: Apache-2.0
"""

import json
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any
from enum import Enum

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


# ============================================================================
# Identifier validation (path-safety + signature canonicalisation)
# ============================================================================
#
# ``credential_id`` doubles as the on-disk filename of the credential, and
# ``member_id`` is its prefix. Until 2026-11-19 neither was validated, so an id
# such as ``../../../escaped`` wrote / read / deleted a JSON file anywhere the
# console user could reach. Both are now matched with ``fullmatch`` (never
# ``match``: ``$`` and a prefix match both admit a trailing newline / suffix).

_MEMBER_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_CREDENTIAL_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}(:\d+:[0-9a-f]{8})?")
_TASK_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


class InvalidLicensingId(ValueError):
    """A member / credential / task id is not in the accepted shape."""


def _check_id(value: Any, pattern: "re.Pattern[str]", kind: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value) or not value.strip("."):
        # The value itself is not echoed: it is attacker-controlled input.
        raise InvalidLicensingId(f"invalid {kind}")
    return value


def validate_member_id(value: Any) -> str:
    """``[A-Za-z0-9_.-]{1,64}``, not dots only. Raises :class:`InvalidLicensingId`."""
    return _check_id(value, _MEMBER_ID_RE, "member_id")


def validate_credential_id(value: Any) -> str:
    """``<member-shaped id>`` optionally followed by ``:<ts>:<8 hex>``."""
    return _check_id(value, _CREDENTIAL_ID_RE, "credential_id")


def validate_task_id(value: Any) -> str:
    """``[A-Za-z0-9_.:-]{1,128}``, not dots only."""
    return _check_id(value, _TASK_ID_RE, "task_id")


#: Hard server-side ceiling for a signed task's lifetime. The client-supplied
#: ``ttl_seconds`` is signed AND clamped to this; it can only shorten the window.
MAX_TASK_TTL_SECONDS = 3600

#: Tolerated clock skew for a ``signed_at`` that lies in the future.
MAX_TASK_CLOCK_SKEW_SECONDS = 300


def credential_signing_bytes(credential: "MemberCredential") -> bytes:
    """Canonical bytes the AUTHORITY key signs for a credential.

    Covers every field a holder could profit from changing — including
    ``credential_id`` and ``license_tier`` — so an edited credential file no
    longer verifies.
    """
    return json.dumps(
        {
            "v": 2,
            "member_id": credential.member_id,
            "credential_id": credential.credential_id,
            "license_tier": credential.license_tier,
            "issued_at": credential.issued_at,
            "expires_at": credential.expires_at,
            "public_key_pem": credential.public_key_pem,
            "issuer": credential.issuer,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def task_signing_bytes(task: "SignedTask") -> bytes:
    """Canonical bytes the MEMBER key signs for a delegation task.

    JSON instead of a ``|``-joined string: a joined string is ambiguous when a
    field contains the separator. ``task_id``, ``credential_id`` and
    ``ttl_seconds`` are part of the message — before 2026-11-19 they were not,
    so one captured signature verified under any task id and any TTL.
    """
    return json.dumps(
        {
            "v": 2,
            "task_id": task.task_id,
            "member_id": task.member_id,
            "credential_id": task.credential_id,
            "operation": task.operation,
            "payload": task.payload,
            "signed_at": task.signed_at,
            "ttl_seconds": task.ttl_seconds,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def secure_dir(path: Path) -> Path:
    """Create *path* (and parents) and restrict it to the owner (0o700)."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:  # e.g. a filesystem without POSIX modes
        pass
    return path


def atomic_write_private(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically at mode 0o600 (tmp, fsync, replace).

    Raises on failure — a caller that persists security state must know when
    it did not land.
    """
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class LicenseTier(Enum):
    """License tiers for A2A delegation access."""
    FREE = "free"  # No A2A access
    MEMBER = "member"  # A2A access enabled
    ENTERPRISE = "enterprise"  # A2A + priority


# ============================================================================
# RSA Key Management
# ============================================================================

class RSAKeyPair:
    """Manages RSA keypairs for credential signing."""

    BITS = 2048

    @staticmethod
    def generate() -> "RSAKeyPair":
        """Generate a new 2048-bit RSA keypair."""
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=RSAKeyPair.BITS,
            backend=default_backend(),
        )
        return RSAKeyPair(private_key)

    def __init__(self, private_key):
        """Initialize with a private key."""
        self.private_key = private_key
        self.public_key = private_key.public_key()

    def private_pem(self) -> bytes:
        """Export private key as PEM."""
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_pem(self) -> bytes:
        """Export public key as PEM."""
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    @staticmethod
    def from_private_pem(pem_data: bytes) -> "RSAKeyPair":
        """Load from PEM-encoded private key."""
        private_key = serialization.load_pem_private_key(
            pem_data,
            password=None,
            backend=default_backend(),
        )
        return RSAKeyPair(private_key)

    @staticmethod
    def from_public_pem(pem_data: bytes):
        """Load public key from PEM."""
        return serialization.load_pem_public_key(
            pem_data,
            backend=default_backend(),
        )

    def sign(self, data: bytes) -> bytes:
        """Sign data with private key."""
        return self.private_key.sign(
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

    @staticmethod
    def verify(public_key, data: bytes, signature: bytes) -> bool:
        """Verify signature with public key."""
        try:
            public_key.verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return True
        except Exception as e:
            logger.error(f"Signature verification failed: {e}")
            return False


# ============================================================================
# Member Credential
# ============================================================================

@dataclass
class MemberCredential:
    """Credential for A2A delegation access."""

    member_id: str
    license_tier: str  # "free", "member", "enterprise"
    issued_at: str  # ISO 8601
    expires_at: str  # ISO 8601
    public_key_pem: str  # PEM-encoded public key
    credential_id: str = ""  # Unique credential ID
    issuer: str = "corvin_authority"
    signature: str = ""  # Hex-encoded signature of credential
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Auto-generate credential_id if not set.

        Second-resolution timestamps collide when a member is issued more
        than one credential within the same second (renewal races, tests) —
        the id doubles as the on-disk filename, so a collision silently
        overwrote the earlier credential. A short random suffix keeps ids
        stable in length while making them collision-resistant.
        """
        validate_member_id(self.member_id)
        if not self.credential_id:
            ts = int(
                datetime.fromisoformat(self.issued_at.replace("Z", "+00:00")).timestamp()
            )
            self.credential_id = f"{self.member_id}:{ts}:{uuid.uuid4().hex[:8]}"
        validate_credential_id(self.credential_id)

    def is_valid(self, check_expiry: bool = True) -> bool:
        """Check if credential is valid.

        Args:
            check_expiry: Also check expiration time

        Returns:
            True if valid (not expired, not revoked)
        """
        if not self.public_key_pem or not self.signature:
            return False

        if check_expiry:
            try:
                expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > expires:
                    logger.warning(f"Credential {self.credential_id} has expired")
                    return False
            except Exception as e:
                logger.error(f"Failed to parse expiry: {e}")
                return False

        return True

    def is_expired(self) -> bool:
        """Check if credential has expired."""
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) > expires
        except:
            return False

    def can_delegate(self) -> bool:
        """Check if this credential can perform A2A delegation.

        Returns:
            True if tier is 'member' or 'enterprise' and not expired
        """
        return (
            self.license_tier in ("member", "enterprise") and
            self.is_valid() and
            not self.is_expired()
        )

    def get_remaining_ttl_seconds(self) -> int:
        """Get remaining time-to-live in seconds."""
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            remaining = (expires - datetime.now(timezone.utc)).total_seconds()
            return max(0, int(remaining))
        except:
            return 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "member_id": self.member_id,
            "credential_id": self.credential_id,
            "license_tier": self.license_tier,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "public_key_pem": self.public_key_pem,
            "issuer": self.issuer,
            "signature": self.signature,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "MemberCredential":
        """Create from dict."""
        return MemberCredential(
            member_id=data["member_id"],
            license_tier=data["license_tier"],
            issued_at=data["issued_at"],
            expires_at=data["expires_at"],
            public_key_pem=data["public_key_pem"],
            credential_id=data.get("credential_id", ""),
            issuer=data.get("issuer", "corvin_authority"),
            signature=data.get("signature", ""),
            metadata=data.get("metadata", {}),
        )


# ============================================================================
# Signed Task Payload
# ============================================================================

@dataclass
class SignedTask:
    """A delegation task signed by a member's credential."""

    task_id: str
    member_id: str
    credential_id: str
    operation: str  # e.g., "delegate_to_agent", "execute_workflow"
    payload: Dict[str, Any]
    signed_at: str  # ISO 8601
    signature: str  # Hex-encoded RSA signature
    ttl_seconds: int = 3600  # Default 1 hour

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "task_id": self.task_id,
            "member_id": self.member_id,
            "credential_id": self.credential_id,
            "operation": self.operation,
            "payload": self.payload,
            "signed_at": self.signed_at,
            "signature": self.signature,
            "ttl_seconds": self.ttl_seconds,
        }

    def effective_ttl_seconds(self) -> int:
        """The requested TTL clamped to ``[0, MAX_TASK_TTL_SECONDS]``."""
        try:
            ttl = int(self.ttl_seconds)
        except (TypeError, ValueError, OverflowError):
            # OverflowError: int(float("inf")) - an unbounded TTL is no TTL.
            return 0
        return max(0, min(ttl, MAX_TASK_TTL_SECONDS))

    def signed_at_epoch(self) -> Optional[float]:
        """``signed_at`` as a UTC epoch, or ``None`` when unparseable."""
        try:
            signed = datetime.fromisoformat(str(self.signed_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if signed.tzinfo is None:
            signed = signed.replace(tzinfo=timezone.utc)
        return signed.timestamp()

    def is_expired(self) -> bool:
        """Check if signed task has expired (fail-closed).

        Expired when ``signed_at`` is unparseable, lies further in the future
        than the tolerated clock skew, or is older than the SERVER-capped TTL.
        """
        signed = self.signed_at_epoch()
        if signed is None:
            return True
        age_seconds = time.time() - signed
        if age_seconds < -MAX_TASK_CLOCK_SKEW_SECONDS:
            return True
        return age_seconds > self.effective_ttl_seconds()

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "SignedTask":
        """Create from dict."""
        return SignedTask(
            task_id=data["task_id"],
            member_id=data["member_id"],
            credential_id=data["credential_id"],
            operation=data["operation"],
            payload=data["payload"],
            signed_at=data["signed_at"],
            signature=data["signature"],
            ttl_seconds=data.get("ttl_seconds", 3600),
        )


# ============================================================================
# Credential Storage
# ============================================================================

class CredentialStore:
    """Persistent storage for member credentials.

    ``corvin_home`` is the ROOT the store lives under. The console passes a
    tenant home (``tenant_home(rec.tenant_id)``), so one tenant's credentials
    are never visible to another; the name is kept for compatibility.
    """

    def __init__(self, corvin_home: str):
        """Initialize credential store.

        Args:
            corvin_home: Root directory (a tenant home in the console)
        """
        self.corvin_home = Path(corvin_home)
        secure_dir(self.corvin_home / "licensing")
        self.credentials_dir = secure_dir(self.corvin_home / "licensing" / "credentials")

    def _path(self, credential_id: str) -> Path:
        """Validated, contained path for *credential_id*.

        Two independent guards: the id shape, then containment of the resolved
        path — so a future relaxation of the shape cannot reopen traversal.
        """
        validate_credential_id(credential_id)
        base = self.credentials_dir.resolve()
        cred_file = (base / f"{credential_id}.json").resolve()
        if not cred_file.is_relative_to(base) or cred_file.parent != base:
            raise InvalidLicensingId("invalid credential_id")
        return cred_file

    def save(self, credential: MemberCredential) -> None:
        """Save credential to disk (atomic, 0o600). Raises on failure."""
        cred_file = self._path(credential.credential_id)
        try:
            atomic_write_private(
                cred_file,
                json.dumps(credential.to_dict(), indent=2).encode("utf-8"),
            )
            logger.info("Saved credential %s", credential.credential_id)
        except Exception as e:
            logger.error("Failed to save credential: %s", type(e).__name__)
            raise

    def load(self, credential_id: str) -> Optional[MemberCredential]:
        """Load credential from disk. An invalid id is simply "not found"."""
        try:
            cred_file = self._path(credential_id)
        except InvalidLicensingId:
            return None
        if not cred_file.exists():
            return None

        try:
            with open(cred_file, encoding="utf-8") as f:
                data = json.load(f)
            credential = MemberCredential.from_dict(data)
        except Exception as e:
            logger.error("Failed to load credential: %s", type(e).__name__)
            return None
        if credential.credential_id != credential_id:
            # The file name is the lookup key; a body naming another id is
            # either corruption or a planted file.
            logger.error("Credential file does not match its id")
            return None
        return credential

    def list_for_member(self, member_id: str) -> list:
        """List all credentials for a member."""
        credentials = []
        try:
            validate_member_id(member_id)
        except InvalidLicensingId:
            return credentials
        for cred_file in self.credentials_dir.glob("*.json"):
            try:
                with open(cred_file, encoding="utf-8") as f:
                    data = json.load(f)
                if data["member_id"] == member_id:
                    credentials.append(MemberCredential.from_dict(data))
            except Exception:  # noqa: BLE001 - one unreadable file must not hide the rest
                continue
        return credentials

    def delete(self, credential_id: str) -> None:
        """Delete a credential. An invalid id raises :class:`InvalidLicensingId`."""
        cred_file = self._path(credential_id)
        if cred_file.exists():
            cred_file.unlink()
            logger.info("Deleted credential %s", credential_id)
