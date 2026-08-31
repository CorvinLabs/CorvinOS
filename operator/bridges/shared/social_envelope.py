"""Layer 39 CorvinFed — PostEnvelope sign/verify, Ed25519 keypair management."""

import json
import time
import uuid
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

VALID_POST_TYPES = frozenset(
    {"status", "reply", "boost", "follow", "unfollow", "retract", "announce"}
)
VALID_VISIBILITIES = frozenset({"public", "followers", "direct"})
MAX_CONTENT_CHARS = 2000
MAX_ATTACHMENTS = 4
MAX_ATTACHMENTS_BYTES = 512 * 1024
TIME_WINDOW_SECONDS = 300


class EnvelopeError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class PostEnvelope:
    post_id: str
    actor_id: str
    issued_at: float
    post_type: str
    visibility: str
    content: str
    content_warning: str | None
    in_reply_to: str | None
    boost_of: str | None
    tags: list[str]
    attachments: list[dict]
    is_ai: bool
    ai_model: str | None
    key_id: str
    signature: str


def generate_keypair() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_hex = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
    public_hex = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private_hex, public_hex


def _canonical_payload(envelope_dict: dict) -> bytes:
    return json.dumps(
        {k: v for k, v in envelope_dict.items() if k != "signature"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sign_envelope(envelope_dict: dict, private_key_hex: str) -> str:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    payload = _canonical_payload(envelope_dict)
    return private_key.sign(payload).hex()


def verify_envelope(envelope_dict: dict, public_key_hex: str) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        payload = _canonical_payload(envelope_dict)
        sig = bytes.fromhex(envelope_dict["signature"])
        public_key.verify(sig, payload)
        return True
    except Exception:
        return False


def build_envelope(**kwargs) -> dict:
    envelope = {
        "post_id": str(uuid.uuid4()),
        "actor_id": kwargs["actor_id"],
        "issued_at": time.time(),
        "post_type": kwargs["post_type"],
        "visibility": kwargs["visibility"],
        "content": kwargs["content"],
        "content_warning": kwargs.get("content_warning"),
        "in_reply_to": kwargs.get("in_reply_to"),
        "boost_of": kwargs.get("boost_of"),
        "tags": kwargs.get("tags", []),
        "attachments": kwargs.get("attachments", []),
        "is_ai": kwargs["is_ai"],
        "ai_model": kwargs.get("ai_model"),
        "key_id": kwargs["key_id"],
        "signature": "",
    }
    return envelope


def validate_envelope_schema(envelope_dict: dict) -> None:
    required_fields = {
        "post_id": str,
        "actor_id": str,
        "issued_at": (int, float),
        "post_type": str,
        "visibility": str,
        "content": str,
        "is_ai": bool,
        "key_id": str,
        "signature": str,
    }
    for field_name, expected_type in required_fields.items():
        if field_name not in envelope_dict:
            raise EnvelopeError(f"missing required field: {field_name}")
        if not isinstance(envelope_dict[field_name], expected_type):
            raise EnvelopeError(
                f"field {field_name!r} has wrong type: "
                f"expected {expected_type}, got {type(envelope_dict[field_name])}"
            )
    if envelope_dict["post_type"] not in VALID_POST_TYPES:
        raise EnvelopeError(f"invalid post_type: {envelope_dict['post_type']!r}")
    if envelope_dict["visibility"] not in VALID_VISIBILITIES:
        raise EnvelopeError(f"invalid visibility: {envelope_dict['visibility']!r}")
    if len(envelope_dict["content"]) > MAX_CONTENT_CHARS:
        raise EnvelopeError(
            f"content exceeds {MAX_CONTENT_CHARS} chars: {len(envelope_dict['content'])}"
        )
    attachments = envelope_dict.get("attachments", [])
    if not isinstance(attachments, list):
        raise EnvelopeError("attachments must be a list")
    if len(attachments) > MAX_ATTACHMENTS:
        raise EnvelopeError(f"too many attachments: {len(attachments)} (max {MAX_ATTACHMENTS})")
    tags = envelope_dict.get("tags", [])
    if not isinstance(tags, list):
        raise EnvelopeError("tags must be a list")
    if len(tags) > 10:
        raise EnvelopeError(f"too many tags: {len(tags)} (max 10)")
