"""Pseudonymisation seed resolution.

Phase 12.6. The seed is the per-tenant secret that makes the
``pseudonymize`` strategy deterministic across calls AND unlinkable
across tenants. Same tenant + same value → same pseudo-token;
different tenant → different token.

Resolution order:

  1. Explicit ``seed`` arg passed to ``resolve_seed`` (test path).
  2. ``CORVIN_PSEUDO_SEED`` env var (operator-overridable per process).
  3. Vault key ``CORVIN_PSEUDO_SEED`` (default operator location).
  4. Per-tenant derived fallback: ``sha256("corvin:pseudo:" + tenant_id)``.
     Better than nothing; still deterministic; still per-tenant; but
     not a true secret (anyone reading the source can recompute it).
     Caller decides whether to accept this via ``allow_derived``.
"""
from __future__ import annotations

import hashlib
import os

# Reserved key name in the secret vault.
PSEUDO_SEED_VAULT_KEY = "CORVIN_PSEUDO_SEED"


def derived_seed(tenant_id: str) -> str:
    """Fallback seed when the operator has not configured one. Stable
    per-tenant but recoverable from source — for testing / dev only.
    """
    h = hashlib.sha256()
    h.update(b"corvin:pseudo:")
    h.update(tenant_id.encode("utf-8"))
    return h.hexdigest()


def resolve_seed(
    *,
    explicit:        str | None = None,
    tenant_id:       str = "_default",
    allow_derived:   bool = True,
    vault_loader:    object = None,    # callable returning {key: value} or None
) -> tuple[str | None, str]:
    """Resolve the active pseudonymisation seed.

    Returns ``(seed_or_None, source)`` where source is one of
    ``"explicit" | "env" | "vault" | "derived" | "none"``.

    Caller policy:
      * ``allow_derived=True`` (default) → returns the per-tenant
        derived seed when no operator seed is configured. Suitable for
        development / single-operator.
      * ``allow_derived=False`` → returns ``(None, "none")`` when no
        real secret is available. Use this when you'd rather fail-loud
        than pseudonymise with a derivable token.
    """
    if explicit:
        return explicit, "explicit"

    env = os.environ.get(PSEUDO_SEED_VAULT_KEY)
    if env:
        return env, "env"

    # Vault lookup — best-effort.
    if vault_loader is not None:
        try:
            vault = vault_loader()
            if isinstance(vault, dict):
                v = vault.get(PSEUDO_SEED_VAULT_KEY)
                if isinstance(v, str) and v:
                    return v, "vault"
        except Exception:
            # vault read failed (mode 0644 / corrupt JSON / etc.) — fall
            # through to derived. The vault module's own logging
            # surfaces the cause.
            pass

    if allow_derived:
        return derived_seed(tenant_id), "derived"

    return None, "none"


def default_vault_loader() -> dict[str, str]:
    """Convenience: read the standard secret vault and return its dict."""
    try:
        from .. import secret_vault   # type: ignore[no-redef]
        return secret_vault.load_vault()
    except Exception:
        return {}
