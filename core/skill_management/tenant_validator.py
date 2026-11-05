"""Tenant ID Validation (TENANT-002 Fix).

Centralizes tenant_id validation to prevent path traversal and other attacks.
Used by all skill_management modules that accept tenant_id parameter.

Delegates to the canonical rule ``core.tenants.validation.validate_tenant_id``
(full match, reserved names). The private ``re.match(r"^[a-zA-Z0-9_-]+$")`` this
module carried accepted ``"acme\\n"`` (``$`` matches before a trailing newline)
and every reserved name — a third copy next to the two LRN-15 fixed
(refutation 2026-11-05, RL-4).
"""


def validate_tenant_id(tenant_id: str) -> str:
    """Validate tenant ID (GDPR Art. 5 integrity, ADR-0007 multi-tenant axis).

    Returns the validated tenant_id (pass-through if valid).

    Raises:
        ValueError: if tenant_id is not a canonical tenant id (see
            ``core.tenants.validation.validate_tenant_id``).
    """
    from core.tenants.validation import validate_tenant_id as _canonical  # noqa: PLC0415

    return _canonical(tenant_id)
