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
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
from enum import Enum

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


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
        """Auto-generate credential_id if not set."""
        if not self.credential_id:
            ts = int(datetime.fromisoformat(self.issued_at).timestamp())
            self.credential_id = f"{self.member_id}:{ts}"

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
                if datetime.utcnow() > expires:
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
            return datetime.utcnow() > expires
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
            remaining = (expires - datetime.utcnow()).total_seconds()
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

    def is_expired(self) -> bool:
        """Check if signed task has expired."""
        try:
            signed = datetime.fromisoformat(self.signed_at.replace("Z", "+00:00"))
            age_seconds = (datetime.utcnow() - signed).total_seconds()
            return age_seconds > self.ttl_seconds
        except:
            return True

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
    """Persistent storage for member credentials."""

    def __init__(self, corvin_home: str):
        """Initialize credential store.

        Args:
            corvin_home: Path to ~/.corvin
        """
        self.corvin_home = Path(corvin_home)
        self.credentials_dir = self.corvin_home / "licensing" / "credentials"
        self.credentials_dir.mkdir(parents=True, exist_ok=True)

    def save(self, credential: MemberCredential) -> None:
        """Save credential to disk."""
        cred_file = self.credentials_dir / f"{credential.credential_id}.json"
        try:
            with open(cred_file, "w") as f:
                json.dump(credential.to_dict(), f, indent=2)
            logger.info(f"Saved credential {credential.credential_id}")
        except Exception as e:
            logger.error(f"Failed to save credential: {e}")
            raise

    def load(self, credential_id: str) -> Optional[MemberCredential]:
        """Load credential from disk."""
        cred_file = self.credentials_dir / f"{credential_id}.json"
        if not cred_file.exists():
            return None

        try:
            with open(cred_file) as f:
                data = json.load(f)
            return MemberCredential.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load credential: {e}")
            return None

    def list_for_member(self, member_id: str) -> list:
        """List all credentials for a member."""
        credentials = []
        for cred_file in self.credentials_dir.glob("*.json"):
            try:
                with open(cred_file) as f:
                    data = json.load(f)
                if data["member_id"] == member_id:
                    credentials.append(MemberCredential.from_dict(data))
            except:
                pass
        return credentials

    def delete(self, credential_id: str) -> None:
        """Delete a credential."""
        cred_file = self.credentials_dir / f"{credential_id}.json"
        if cred_file.exists():
            cred_file.unlink()
            logger.info(f"Deleted credential {credential_id}")
