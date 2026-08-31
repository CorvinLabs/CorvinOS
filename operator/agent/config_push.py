"""Config-push event handler for the Instance Agent.

The Management API sends structured JSON events to the agent's
POST /config-push endpoint.  The agent handles them synchronously
and returns a result dict.

Supported event types:
  secret.push       — decrypt BYOK ciphertext → write to vault
  config.reload     — signal adapter hot-reload (best-effort)
  keypair.rotate    — regenerate RSA keypair + re-register pubkey

Events are validated before processing.  Unknown event types return
{ok: false, error: "unknown_event"} without raising.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def handle(
    event: dict[str, Any],
    *,
    agent_dir: Path | None = None,
    vault_dir: Path | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Dispatch a config-push event.  Never raises; returns a result dict."""
    event_type = event.get("event") or event.get("type") or ""
    tid = tenant_id or os.environ.get("CORVIN_TENANT_ID", "_default")

    if event_type == "secret.push":
        return _handle_secret_push(event, agent_dir=agent_dir, vault_dir=vault_dir, tenant_id=tid)
    if event_type == "config.reload":
        return _handle_config_reload(tid)
    if event_type == "keypair.rotate":
        return _handle_keypair_rotate(agent_dir=agent_dir, tenant_id=tid)

    return {"ok": False, "error": "unknown_event", "event": event_type}


def _handle_secret_push(
    event: dict[str, Any],
    *,
    agent_dir: Path | None,
    vault_dir: Path | None,
    tenant_id: str,
) -> dict[str, Any]:
    from .byok import apply_byok_secret
    from .health import record_byok_rotation

    key_name = event.get("key_name", "")
    ciphertext_b64 = event.get("ciphertext", "")
    updated_by = event.get("updated_by", "management_api")

    if not key_name or not ciphertext_b64:
        return {"ok": False, "error": "missing key_name or ciphertext"}

    try:
        result = apply_byok_secret(
            key_name,
            ciphertext_b64,
            agent_dir=agent_dir,
            vault_dir=vault_dir,
            tenant_id=tenant_id,
            updated_by=updated_by,
        )
        record_byok_rotation(key_name)
        from .audit import agent_event
        agent_event(
            "secret.byok_updated",
            tenant_id=tenant_id,
            details={"key_name": key_name, "updated_by": updated_by},
        )
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "key_name": key_name}


def _handle_config_reload(tenant_id: str) -> dict[str, Any]:
    from .health import record_config_push
    record_config_push()
    from .audit import agent_event
    agent_event("bridge.config_reloaded", tenant_id=tenant_id)
    return {"ok": True, "event": "config.reload", "ts": time.time()}


def _handle_keypair_rotate(
    *,
    agent_dir: Path | None,
    tenant_id: str,
) -> dict[str, Any]:
    """Delete existing keypair files and regenerate."""
    from .keypair import generate_or_load_keypair, _agent_dir
    from .audit import agent_event

    d = agent_dir if agent_dir is not None else _agent_dir(tenant_id)
    for fname in ("byok_privkey.pem", "byok_pubkey.pem"):
        p = d / fname
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        _, pub_pem = generate_or_load_keypair(d, tenant_id=tenant_id)
        agent_event("agent.pubkey_rotated", tenant_id=tenant_id)
        return {"ok": True, "event": "keypair.rotate", "pubkey_pem": pub_pem.decode("utf-8")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
