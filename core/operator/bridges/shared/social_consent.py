"""Layer 39 CorvinFed — participation consent flow, /social-join, /social-leave."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .audit import audit_event  # type: ignore[import-not-found]
    from . import instance_identity  # type: ignore[import-not-found]
    from . import social_actor  # type: ignore[import-not-found]
    from .social_actor import social_dir  # type: ignore[import-not-found]
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from audit import audit_event  # type: ignore[import-not-found]
    import instance_identity  # type: ignore[import-not-found]
    import social_actor  # type: ignore[import-not-found]
    from social_actor import social_dir  # type: ignore[import-not-found]


class ConsentRequired(Exception):
    """Raised when a social federation operation requires prior consent."""

    def __init__(self) -> None:
        super().__init__("Social federation not enabled. Run /social-join first.")


# ── Consent file path ─────────────────────────────────────────────────────────

def _consent_path(tenant_id: str | None = None) -> Path:
    return social_dir(tenant_id) / "consent.json"


# ── Atomic write helper ───────────────────────────────────────────────────────

def _write_consent(path: Path, payload: dict[str, Any]) -> None:
    """Write consent.json atomically with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _load_consent(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


# ── Public API ────────────────────────────────────────────────────────────────

def is_consented(tenant_id: str | None = None) -> bool:
    """Return True if consent.json exists and ``consented == True``."""
    data = _load_consent(_consent_path(tenant_id))
    if data is None:
        return False
    return bool(data.get("consented", False))


def require_consent(tenant_id: str | None = None) -> None:
    """Raise ``ConsentRequired`` if the tenant has not consented to social federation."""
    if not is_consented(tenant_id):
        raise ConsentRequired()


def join(
    display_name: str,
    host: str,
    compliance_zone: str = "eu",
    tenant_id: str | None = None,
    channel: str = "",
    chat_key: str = "",
) -> dict:
    """Execute the /social-join flow.

    1. Returns early with ``status: already_joined`` if already consented.
    2. Writes ``social.participation_consented`` to the L16 audit chain BEFORE
       any state mutation.
    3. Generates keypair and actor document.
    4. Writes consent.json atomically (mode 0600).
    5. Returns ``{"status": "joined", "actor_id": ..., "actor_doc": ...}``.

    On failure after the audit event: writes ``social.join_failed`` (WARNING)
    and re-raises the exception.
    """
    # 1. Already joined?
    path = _consent_path(tenant_id)
    existing = _load_consent(path)
    if existing and existing.get("consented"):
        return {
            "status": "already_joined",
            "actor_id": existing.get("actor_id"),
            "consented_at": existing.get("consented_at"),
        }

    # 2. Audit BEFORE any state mutation
    instance_id = instance_identity.get_instance_id()
    audit_event(
        "social.participation_consented",
        channel=channel,
        chat_key=chat_key,
        severity="INFO",
        details={"actor_id": instance_id},
    )

    try:
        # 3. Generate keypair
        social_actor.generate_keypair_and_save(tenant_id)

        # 4. Generate actor document
        actor_doc = social_actor.generate_actor_document(
            display_name=display_name,
            host=host,
            compliance_zone=compliance_zone,
            tenant_id=tenant_id,
        )

        actor_id = actor_doc.get("instance_id", instance_id)

        # 5. Write consent.json atomically (mode 0600)
        consent_payload: dict[str, Any] = {
            "consented": True,
            "consented_at": time.time(),
            "actor_id": actor_id,
        }
        _write_consent(path, consent_payload)

        # 6. Return result
        return {
            "status": "joined",
            "actor_id": actor_id,
            "actor_doc": actor_doc,
        }

    except Exception:
        # 7. Failure path — write warning audit, re-raise
        audit_event(
            "social.join_failed",
            channel=channel,
            chat_key=chat_key,
            severity="WARNING",
            details={"actor_id": instance_id},
        )
        raise


def leave(
    tenant_id: str | None = None,
    channel: str = "",
    chat_key: str = "",
) -> dict:
    """Execute the /social-leave flow.

    1. Returns ``{"status": "not_joined"}`` if not currently consented.
    2. Writes ``social.participation_revoked`` (WARNING) BEFORE any deletion.
    3. Deletes consent.json, keypair.json, and actor.json.
    4. Does NOT delete posts.db or registry.db (handled by L36 ErasureHandler).
    5. Returns ``{"status": "left"}``.
    """
    # 1. Not joined?
    if not is_consented(tenant_id):
        return {"status": "not_joined"}

    # 2. Audit BEFORE any deletion
    audit_event(
        "social.participation_revoked",
        channel=channel,
        chat_key=chat_key,
        severity="WARNING",
    )

    # 3. Delete state files
    _unlink_if_exists(_consent_path(tenant_id))
    _unlink_if_exists(social_actor.keypair_path(tenant_id))
    _unlink_if_exists(social_actor.actor_doc_path(tenant_id))

    # 4. Return
    return {"status": "left"}


def get_status(tenant_id: str | None = None) -> dict:
    """Return a summary of the current social federation consent state."""
    data = _load_consent(_consent_path(tenant_id))
    if data is None or not data.get("consented"):
        return {
            "is_enabled": False,
            "consented_at": None,
            "actor_id": None,
        }
    return {
        "is_enabled": True,
        "consented_at": data.get("consented_at"),
        "actor_id": data.get("actor_id"),
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


__all__ = [
    "ConsentRequired",
    "get_status",
    "is_consented",
    "join",
    "leave",
    "require_consent",
]
