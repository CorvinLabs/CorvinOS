"""Shared, fail-closed RAG-provider license gate (ADR-0094 / ADR-0144 CON-01).

Both ``POST /custom-provider/create`` and ``POST /hub/import`` write a
``{provider_id}.yaml`` file into the SAME registry directory
(``tenant_global_dir(tid)/rag``) — the directory whose ``*.yaml`` count is what
``rag_providers_max`` limits and what the RAG orchestrator loads. The original
gate lived inline only in ``custom_provider.py``; ``rag_hub.import_provider``
wrote into the same dir with NO check, so a free-tier user could register
unlimited providers via the import path (CON-01, A_WORKS_ON_UNMODIFIED).

This module is the single enforcement point so the two write paths can never
drift again. It mirrors the ADR-0144 F-01 fail-closed import fallback: a missing
``license`` package degrades to the FREE_TIER cap (``rag_providers_max`` = 1),
never to "no limit".
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from fastapi import HTTPException

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_OPERATOR = _REPO / "operator"
_FORGE = _OPERATOR / "forge"
for _p in (_FORGE, _OPERATOR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from forge import paths as _forge_paths  # noqa: E402

# Fail-closed FREE_TIER fallback for the limits this gate reads. If BOTH the
# validator AND license.limits are unimportable we must NOT degrade to a bare
# ``{}.get`` — that returns None for every feature, and None is the "unlimited"
# sentinel, so an unimportable license package would FAIL OPEN to unlimited RAG
# providers (contradicting _compute_license_gate.enforce_compute_quota). Hard-code
# the FREE_TIER caps inline so the gate stays fail-closed even with no package.
_RAG_FREE_TIER_FALLBACK: dict = {"rag_providers_max": 1}

try:
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
except ImportError:
    try:
        from license.limits import FREE_TIER as _FREE_TIER  # type: ignore[import]
        _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]
    except ImportError:
        # Innermost fallback: license package entirely absent. Resolve via the
        # hard-coded FREE_TIER caps (fail-closed), never to None=unlimited.
        _lic_get_limit = _RAG_FREE_TIER_FALLBACK.get  # type: ignore[assignment]

# Provider ids become a filename (``{provider_id}.yaml``). Constrain to a safe,
# non-traversing charset so a crafted id cannot escape the registry dir.
_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def sanitize_provider_id(provider_id: str) -> str:
    """Return provider_id if it is a safe filename stem, else raise HTTP 400.

    Rejects path separators, ``..``, leading dots, and anything outside
    ``[a-z0-9._-]`` — closing the path-traversal vector where provider_id flows
    unsanitized into ``destination_dir / f"{provider_id}.yaml"``.
    """
    pid = (provider_id or "").strip()
    if not _PROVIDER_ID_RE.match(pid):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_provider_id",
                "msg": (
                    "provider_id must match ^[a-z0-9][a-z0-9._-]{0,63}$ "
                    "(lowercase, no path separators or '..')."
                ),
            },
        )
    return pid


def enforce_rag_providers_max(
    tenant_id: str,
    sid_fingerprint: str,
    requested_id: str = "pending",
    *,
    audit_action: str = "rag.provider_create",
) -> None:
    """Raise HTTP 402 when the tenant already holds ``rag_providers_max`` providers.

    Re-checks per call (each successful write adds a file). ``None`` limit means
    unlimited (paid tier) → no-op. Fail-closed: an absent license module resolves
    via FREE_TIER (limit 1), never to "no limit".
    """
    rag_max = _lic_get_limit("rag_providers_max")
    if rag_max is None:
        return  # unlimited (paid tier)
    registry = _forge_paths.tenant_global_dir(tenant_id) / "rag"
    existing = len(list(registry.glob("*.yaml"))) if registry.exists() else 0
    if existing >= rag_max:
        try:
            from .. import audit as _ca  # noqa: PLC0415

            _ca.action_failed(
                tenant_id=tenant_id,
                sid_fingerprint=sid_fingerprint,
                action=audit_action,
                target_kind="rag_provider",
                target_id=requested_id,
                reason="quota_exceeded",
            )
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(
            status_code=402,
            detail={
                "error": "license_limit",
                "feature": "rag_providers_max",
                "existing": existing,
                "msg": (
                    f"Free tier allows at most {rag_max} RAG provider(s) "
                    f"({existing} registered). "
                    "Upgrade to Member plan for unlimited providers."
                ),
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        )
