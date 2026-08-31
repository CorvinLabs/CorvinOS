"""Auth identity — atlr_* static token system removed.

Only the ResolvedToken dataclass and the shared audit helper remain.
OIDC/JWT authentication lives in oidc.py; wired in app.py when needed.

For local deployments the loopback binding is the security boundary.
OIDC will be enforced in the cloud deployment phase.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[2]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


@dataclass(frozen=True)
class ResolvedToken:
    """Resolved caller identity after successful authentication."""
    tenant_id: str
    label: str
    fingerprint: str


def _audit_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"


def _audit(
    event_type: str,
    *,
    tenant_id: str,
    details: dict[str, Any] | None = None,
    severity: str | None = None,
) -> None:
    """Best-effort audit write into the tenant's unified chain."""
    try:
        _security_events.write_event(
            _audit_path(tenant_id),
            event_type,
            severity=severity,
            details=dict(details or {}),
            hash_chain=True,
        )
    except Exception:
        pass
