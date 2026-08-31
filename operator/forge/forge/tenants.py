"""Corvin tenant resolver — fifth scope above (task, session, project, user).

Introduced by ADR-0007 Phase 1.1.

The tenant axis sits ABOVE the existing four-scope model. A single-operator
deployment is implicitly tenant ``_default``; no operator action required.
Multi-tenant deployments inject ``CORVIN_TENANT_ID`` at the gateway layer
so every downstream resolver sees its tenant transparently.

Resolution order in :func:`current_tenant`:
  1. Explicit ``tenant_id`` arg if passed.
  2. ``CORVIN_TENANT_ID`` env var.
  3. :func:`default_tenant_id` → ``_default``.

Tenant-id charset rules (:func:`validate_tenant_id`):
  - ``[a-z0-9][a-z0-9_-]{0,62}`` — DNS-label-like, lower-case only.
  - One leading underscore allowed for reserved internal IDs
    (``_default``, future ``_system``).
  - Path-traversal sequences (``.``, ``..``, ``/``, ``\\``) rejected.
  - Uppercase, unicode, whitespace rejected.

The contract is intentionally narrow: Phase 1.1 ships the identity
contract only; path construction lives in Phase 1.2's ``paths.py``
extension. Keeping the two concerns split avoids the cascade churn
the survey flagged.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_TENANT_ID = "_default"

_TENANT_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,62}$")


class InvalidTenantID(ValueError):
    """Raised when a tenant-id violates the charset / shape contract."""


def default_tenant_id() -> str:
    """Return the implicit single-operator tenant id."""
    return DEFAULT_TENANT_ID


def validate_tenant_id(tenant_id: str) -> str:
    """Return ``tenant_id`` if valid; raise :class:`InvalidTenantID` otherwise.

    Reserved internal IDs (single leading underscore) are accepted; an
    explicit double-underscore prefix is rejected to keep room for future
    reserved namespaces.
    """
    if not isinstance(tenant_id, str):
        raise InvalidTenantID(f"tenant_id must be str, got {type(tenant_id).__name__}")
    if not tenant_id:
        raise InvalidTenantID("tenant_id must not be empty")
    if tenant_id.startswith("__"):
        raise InvalidTenantID(f"tenant_id {tenant_id!r} starts with '__' (reserved)")
    if not _TENANT_ID_RE.match(tenant_id):
        raise InvalidTenantID(
            f"tenant_id {tenant_id!r} fails charset rule "
            f"[a-z0-9_][a-z0-9_-]{{0,62}} (lower-case, DNS-label-like)"
        )
    return tenant_id


def current_tenant(tenant_id: str | None = None) -> str:
    """Resolve the active tenant id.

    Precedence: explicit arg → ``CORVIN_TENANT_ID`` env → ``_default``.

    The result is always a validated tenant-id. An invalid env-var value
    raises :class:`InvalidTenantID` rather than silently falling through
    to the default — the operator's intent must be unambiguous.
    """
    if tenant_id is not None:
        return validate_tenant_id(tenant_id)
    env = os.environ.get("CORVIN_TENANT_ID")
    if env:
        return validate_tenant_id(env)
    return DEFAULT_TENANT_ID


def tenant_home(tenant_id: str | None = None, *, corvin_home_path: Path | None = None) -> Path:
    """Return ``<corvin_home>/tenants/<tid>/`` for the active tenant.

    ``corvin_home_path`` is injected only by tests that don't want to
    import :func:`forge.paths.corvin_home`. Production callers pass
    nothing; the resolver delegates to :func:`forge.paths.corvin_home`.

    Phase 1.1 contract: this function exists and returns the path. It
    does NOT create the directory. Phase 1.4 (migration helper) is the
    only code path that creates tenant dirs on disk.
    """
    resolved = current_tenant(tenant_id)
    if corvin_home_path is None:
        # Lazy import — keeps the module testable without pulling the full
        # paths.py stack into every consumer's import graph.
        from .paths import corvin_home as _corvin_home

        corvin_home_path = _corvin_home()
    return Path(corvin_home_path) / "tenants" / resolved
