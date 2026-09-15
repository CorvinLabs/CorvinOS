"""Layer 39 CorvinFed — ActorDocument generation, keypair management, storage paths."""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .paths import tenant_global_dir  # type: ignore[import-not-found]
    from .audit import audit_event  # type: ignore[import-not-found]
    from . import instance_identity  # type: ignore[import-not-found]
    from . import social_envelope  # type: ignore[import-not-found]
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from paths import tenant_global_dir  # type: ignore[import-not-found]
    from audit import audit_event  # type: ignore[import-not-found]
    import instance_identity  # type: ignore[import-not-found]
    import social_envelope  # type: ignore[import-not-found]


class ActorError(Exception):
    """Raised when actor document or keypair operations fail."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ActorDocument:
    actor_version: str
    instance_id: str
    display_name: str
    summary: str
    inbox_url: str
    outbox_url: str
    public_key: dict
    is_ai: bool
    ai_model: str
    ai_operator: str
    corvin_version: str
    compliance_zone: str
    created_at: float
    signature: str


# ── Path helpers ──────────────────────────────────────────────────────────────

def social_dir(tenant_id: str | None = None) -> Path:
    """Return the social storage directory for the given tenant."""
    return tenant_global_dir(tenant_id) / "social"


def keypair_path(tenant_id: str | None = None) -> Path:
    """Return path to actor_keypair.json."""
    return social_dir(tenant_id) / "actor_keypair.json"


def actor_doc_path(tenant_id: str | None = None) -> Path:
    """Return path to actor.json."""
    return social_dir(tenant_id) / "actor.json"


# ── Keypair management ────────────────────────────────────────────────────────

def generate_keypair_and_save(tenant_id: str | None = None) -> tuple[str, str]:
    """Generate an Ed25519 keypair, save it to keypair.json (mode 0600).

    Returns (private_hex, public_hex).
    Also writes a ``social.keypair_created`` INFO audit event.
    """
    private_hex, public_hex = social_envelope.generate_keypair()

    path = keypair_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "private_key_hex": private_hex,
        "public_key_hex": public_hex,
        "created_at": time.time(),
    }
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

    audit_event(
        "social.keypair_created",
        severity="INFO",
        details={"public_key_hex_prefix": public_hex[:16]},
    )
    return private_hex, public_hex


def load_keypair(tenant_id: str | None = None) -> tuple[str, str]:
    """Load keypair from keypair.json. Raises ``ActorError`` if missing."""
    path = keypair_path(tenant_id)
    if not path.exists():
        raise ActorError(f"keypair file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ActorError(f"failed to load keypair: {exc}") from exc

    priv = data.get("private_key_hex", "")
    pub = data.get("public_key_hex", "")
    if not priv or not pub:
        raise ActorError("keypair file missing private_key_hex or public_key_hex")
    return priv, pub


def check_keypair_mode(tenant_id: str | None = None) -> bool:
    """Return True if keypair.json exists and has mode 0600.

    Returns False if the file is world-readable or group-readable,
    and writes a ``social.keypair_world_readable`` CRITICAL audit event.
    Returns True if the file does not exist (not an error; it just means
    the actor has not been initialised yet).
    """
    path = keypair_path(tenant_id)
    if not path.exists():
        return True
    file_stat = path.stat()
    if file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        audit_event(
            "social.keypair_world_readable",
            severity="CRITICAL",
            details={"keypair_path_prefix": str(path)[:200]},
        )
        return False
    return True


# ── ActorDocument generation and loading ─────────────────────────────────────

def generate_actor_document(
    display_name: str,
    host: str,
    compliance_zone: str = "eu",
    ai_model: str = "claude-sonnet",
    ai_operator: str = "corvin",
    corvin_version: str = "1.0.0",
    tenant_id: str | None = None,
) -> dict:
    """Generate a self-signed ActorDocument and save it to actor.json (mode 0644).

    ``is_ai`` is ALWAYS True for Corvin actors — this is non-negotiable and
    there is no parameter to override it (EU AI Act Art. 50 compliance).

    Returns the document dict (including the signature field).
    """
    # Validate and sanitize display_name
    display_name = display_name[:64].strip()
    if not display_name:
        raise ActorError("display_name must not be empty")

    private_hex, public_hex = load_keypair(tenant_id)
    instance_id = instance_identity.get_instance_id()

    key_id = f"https://{host}/v1/social/actor#key"

    doc: dict[str, Any] = {
        "actor_version": "1",
        "instance_id": instance_id,
        "display_name": display_name,
        "summary": f"Corvin AI node operated by {ai_operator}"[:500],
        "inbox_url": f"https://{host}/v1/social/inbox",
        "outbox_url": f"https://{host}/v1/social/outbox",
        "public_key": {
            "type": "Ed25519",
            "key_id": key_id,
            "public_key_hex": public_hex,
        },
        "is_ai": True,  # ALWAYS True — hard-coded; no override allowed
        "ai_model": ai_model,
        "ai_operator": ai_operator,
        "corvin_version": corvin_version,
        "compliance_zone": compliance_zone,
        "created_at": time.time(),
    }

    # Self-sign over all fields except "signature"
    signature = social_envelope.sign_envelope(doc, private_hex)
    doc["signature"] = signature

    # Persist
    path = actor_doc_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(doc, fh, sort_keys=True, indent=2)
        fh.write("\n")
    # actor.json is public (mode 0644) — it contains no secrets
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)

    return doc


def load_actor_document(tenant_id: str | None = None) -> dict:
    """Load actor.json from disk. Raises ``ActorError`` if missing.

    Does NOT verify the signature — caller's responsibility.
    """
    path = actor_doc_path(tenant_id)
    if not path.exists():
        raise ActorError(f"actor document not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ActorError(f"failed to load actor document: {exc}") from exc
    return data


def is_actor_enabled(tenant_id: str | None = None) -> bool:
    """Return True if both keypair.json and actor.json exist."""
    return keypair_path(tenant_id).exists() and actor_doc_path(tenant_id).exists()


__all__ = [
    "ActorDocument",
    "ActorError",
    "actor_doc_path",
    "check_keypair_mode",
    "generate_actor_document",
    "generate_keypair_and_save",
    "is_actor_enabled",
    "keypair_path",
    "load_actor_document",
    "load_keypair",
    "social_dir",
]
