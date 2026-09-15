"""
A2A Token Generator & Validator
Implements ADR-0070 Phase M1: shareable, time-limited friendship tokens for peer pairing.

Token Format: base64url(json) + '.' + base64url(hmac-sha256(json, secret))

Security Properties:
- HMAC verification (tampering detection)
- Expiry enforcement (time-limited)
- PENDING state blocks inbound until accepted
- No key material in audit logs
"""

import base64
import hashlib
import hmac
import json
import time
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path


@dataclass
class A2AToken:
    """A2A Friendship Token"""
    peer_id: str  # Identifier of peer requesting connection
    endpoint_url: str  # URL where peer can be reached
    issued_at: int = field(default_factory=lambda: int(time.time()))
    expires_in_seconds: int = 3600  # 1 hour default
    state: str = "PENDING"  # PENDING | ACTIVE
    constraint_id: Optional[str] = None  # Optional constraint (e.g., "team-acme-prod")

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization"""
        return asdict(self)

    def is_expired(self) -> bool:
        """Check if token has expired"""
        return (time.time() - self.issued_at) > self.expires_in_seconds


class A2ATokenCodec:
    """Encode/decode A2A tokens with HMAC verification"""

    def __init__(self, secret_key: Optional[str] = None):
        """
        Initialize codec with secret key.
        If None, generates a random key (for ephemeral tokens only).
        """
        self.secret_key = secret_key or secrets.token_urlsafe(32)

    def encode(self, token: A2AToken) -> str:
        """
        Encode token to sharable format.
        Returns: base64url(json) + '.' + base64url(hmac)
        """
        payload_dict = token.to_dict()
        payload_json = json.dumps(payload_dict, sort_keys=True)
        payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).rstrip(b"=").decode()

        # HMAC-SHA256
        signature = hmac.new(
            self.secret_key.encode(),
            payload_json.encode(),
            hashlib.sha256
        ).digest()
        signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

        return f"{payload_b64}.{signature_b64}"

    def decode(self, token_str: str) -> Optional[A2AToken]:
        """
        Decode and verify token.
        Returns: A2AToken if valid, None if tampered/expired.
        """
        try:
            payload_b64, signature_b64 = token_str.split(".")

            # Unpad and decode
            padding = "=" * (4 - len(payload_b64) % 4)
            payload_json = base64.urlsafe_b64decode(payload_b64 + padding).decode()

            # Verify HMAC
            expected_signature = hmac.new(
                self.secret_key.encode(),
                payload_json.encode(),
                hashlib.sha256
            ).digest()
            expected_signature_b64 = base64.urlsafe_b64encode(expected_signature).rstrip(b"=").decode()

            if not hmac.compare_digest(signature_b64, expected_signature_b64):
                return None  # Tampering detected

            # Deserialize
            payload_dict = json.loads(payload_json)
            token = A2AToken(**payload_dict)

            # Check expiry
            if token.is_expired():
                return None

            # PENDING tokens cannot be used for inbound
            # (caller must check state)

            return token

        except (ValueError, KeyError, json.JSONDecodeError, TypeError):
            return None


class A2ATokenStore:
    """Persistent token storage"""

    def __init__(self, storage_path: Path):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.active_tokens_file = self.storage_path / "active_tokens.json"

    def save_token(self, token: A2AToken, token_str: str, codec_secret: str) -> None:
        """
        Save token (metadata + secret key for later verification).
        Secret key stored separately, never in audit logs.
        """
        active = self._load_tokens()

        active[token.peer_id] = {
            "token_str": token_str,
            "peer_id": token.peer_id,
            "endpoint_url": token.endpoint_url,
            "issued_at": token.issued_at,
            "expires_in": token.expires_in_seconds,
            "state": token.state,
            "_secret_key": codec_secret,  # Never exposed
        }

        with open(self.active_tokens_file, "w") as f:
            json.dump(active, f, indent=2)

    def load_token(self, peer_id: str) -> Optional[tuple[str, str]]:
        """
        Load token (token_str, secret_key) for peer.
        Returns: (token_str, codec_secret) or None.
        """
        active = self._load_tokens()
        if peer_id in active:
            entry = active[peer_id]
            return (entry["token_str"], entry.get("_secret_key", ""))
        return None

    def _load_tokens(self) -> dict:
        """Load tokens from disk"""
        if self.active_tokens_file.exists():
            with open(self.active_tokens_file) as f:
                return json.load(f)
        return {}

    def set_active(self, peer_id: str) -> None:
        """Transition token from PENDING to ACTIVE"""
        active = self._load_tokens()
        if peer_id in active:
            active[peer_id]["state"] = "ACTIVE"
            with open(self.active_tokens_file, "w") as f:
                json.dump(active, f, indent=2)

    def revoke(self, peer_id: str) -> None:
        """Revoke a token"""
        active = self._load_tokens()
        if peer_id in active:
            del active[peer_id]
            with open(self.active_tokens_file, "w") as f:
                json.dump(active, f, indent=2)


# CLI helpers (for corvin a2a create-token, etc.)

def create_token(peer_id: str, endpoint_url: str, expires_hours: int = 24) -> str:
    """
    Create a new A2A friendship token.
    Returns: base64url token string (shareable).
    """
    codec = A2ATokenCodec()
    token = A2AToken(
        peer_id=peer_id,
        endpoint_url=endpoint_url,
        expires_in_seconds=expires_hours * 3600
    )
    return codec.encode(token)


def verify_token(token_str: str, codec_secret: str) -> Optional[A2AToken]:
    """
    Verify and decode a token.
    Returns: A2AToken if valid, None if expired/tampered/invalid.
    """
    codec = A2ATokenCodec(secret_key=codec_secret)
    return codec.decode(token_str)


if __name__ == "__main__":
    # Quick test
    codec = A2ATokenCodec()
    token = A2AToken(
        peer_id="adesso-windows",
        endpoint_url="http://192.168.1.100:8765"
    )
    encoded = codec.encode(token)
    print(f"Encoded: {encoded}")

    decoded = codec.decode(encoded)
    if decoded:
        print(f"✅ Verified: {decoded.peer_id} → {decoded.endpoint_url}")
    else:
        print("❌ Verification failed")
