"""Audit event helpers for the Instance Agent.

Wraps the bridge-layer audit_event() so the agent emits into the same
hash-chained audit.jsonl as the rest of the system.

Agent-specific event types and their severities:
  agent.registered         INFO    — successful Management API registration
  agent.heartbeat_missed   CRITICAL — agent not reachable for > 5 min
  agent.pubkey_rotated     INFO    — new RSA keypair generated
  vault.secret_rotated     INFO    — BYOK secret written to L16 vault
  secret.byok_updated      INFO    — ciphertext received from Management API

All emits are best-effort; failures are silently ignored so the agent
never crashes due to an audit write error.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_AGENT_EVENT_SEVERITY: dict[str, str] = {
    "agent.registered":       "INFO",
    "agent.heartbeat_missed": "CRITICAL",
    "agent.pubkey_rotated":   "INFO",
    "vault.secret_rotated":   "INFO",
    "secret.byok_updated":    "INFO",
}


def agent_event(
    event_type: str,
    *,
    tenant_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Emit an agent audit event into the hash-chained audit.jsonl.

    Silently no-ops when the bridge audit module is not importable
    (standalone agent without the full operator stack).
    """
    tid = tenant_id or os.environ.get("CORVIN_TENANT_ID", "_default")

    # Make bridge shared/ importable.
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "operator").is_dir():
            shared = parent / "operator" / "bridges" / "shared"
            if str(shared) not in sys.path:
                sys.path.insert(0, str(shared))
            break

    try:
        from audit import audit_event  # type: ignore
        severity = _AGENT_EVENT_SEVERITY.get(event_type, "INFO")
        audit_event(
            event_type,
            channel="agent",
            chat_key=tid,
            user="",
            persona="agent",
            details=details or {},
            severity=severity,
        )
    except Exception:  # noqa: BLE001
        pass
