"""Data-residency validation for DataSource adapters (ADR-0026 Section D).

Fail-closed rule: an unknown region is treated as outside every zone.
validate_residency() is AUDIT-FIRST — it emits the audit event before raising.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Default region → zone map (at least 20 entries)
# ---------------------------------------------------------------------------

_DEFAULT_REGION_ZONE_MAP: dict[str, str] = {
    # AWS EU
    "eu-central-1": "eu",
    "eu-central-2": "eu",
    "eu-west-1": "eu",
    "eu-west-2": "eu",
    "eu-west-3": "eu",
    "eu-north-1": "eu",
    "eu-south-1": "eu",
    "eu-south-2": "eu",
    # AWS US
    "us-east-1": "us",
    "us-east-2": "us",
    "us-west-1": "us",
    "us-west-2": "us",
    "us-gov-east-1": "us",
    "us-gov-west-1": "us",
    # AWS APAC
    "ap-southeast-1": "apac",
    "ap-southeast-2": "apac",
    "ap-northeast-1": "apac",
    "ap-northeast-2": "apac",
    "ap-south-1": "apac",
    "ap-east-1": "apac",
    # GCP EU
    "europe-west1": "eu",
    "europe-west2": "eu",
    "europe-west3": "eu",
    "europe-west4": "eu",
    "europe-north1": "eu",
    "europe-central2": "eu",
    # GCP US
    "us-central1": "us",
    "us-east1": "us",
    "us-east4": "us",
    "us-west1": "us",
    "us-west2": "us",
    # GCP APAC
    "asia-southeast1": "apac",
    "asia-northeast1": "apac",
    "asia-south1": "apac",
    # Azure EU
    "northeurope": "eu",
    "westeurope": "eu",
    "germanywestcentral": "eu",
    "swedencentral": "eu",
    "switzerlandnorth": "eu",
    # Azure US
    "eastus": "us",
    "eastus2": "us",
    "westus": "us",
    "westus2": "us",
    "centralus": "us",
    # Azure APAC
    "southeastasia": "apac",
    "japaneast": "apac",
    "australiaeast": "apac",
}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

class DataResidencyViolation(Exception):
    """Raised when a datasource's region is outside the tenant's allowed zone."""


def _region_in_zone(
    region: str,
    zone: str,
    zone_map: Optional[dict[str, str]] = None,
) -> bool:
    """Return True iff region maps to zone.

    FAIL-CLOSED: an unknown region always returns False.
    """
    mapping = zone_map if zone_map is not None else _DEFAULT_REGION_ZONE_MAP
    actual_zone = mapping.get(region)
    if actual_zone is None:
        return False  # unknown region → outside every zone
    return actual_zone == zone


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------

def validate_residency(
    manifest: Any,
    tenant_config: Optional[dict],
    audit_fn: Callable[[str, dict], None],
) -> None:
    """Check that the datasource region is within the tenant's allowed zone.

    Args:
        manifest: A ConnectionManifest (or any object with .name and .source.region).
        tenant_config: Dict with optional keys:
            - data_residency: str zone name  (None → skip check)
            - datasource_residency_strict: bool (default True)
        audit_fn: Callable(event_name, details) — called BEFORE raise.

    Raises:
        DataResidencyViolation: when strict=True and region is outside zone.
    """
    if tenant_config is None:
        return

    tenant_zone: Optional[str] = tenant_config.get("data_residency")
    if not tenant_zone:
        return  # no zone requirement → pass-through

    strict: bool = tenant_config.get("datasource_residency_strict", True)
    region: str = manifest.source.region

    if not _region_in_zone(region, tenant_zone):
        # AUDIT-FIRST before raise
        audit_fn(
            "datasource.residency_violation",
            {
                "datasource_name": manifest.name,
                "declared_region": region,
                "tenant_zone": tenant_zone,
            },
        )
        msg = (
            f"DataSource '{manifest.name}' region '{region}' is outside "
            f"tenant zone '{tenant_zone}'"
        )
        if strict:
            raise DataResidencyViolation(msg)
        # non-strict: warning-only (caller may log; we just return)


__all__ = [
    "_DEFAULT_REGION_ZONE_MAP",
    "_region_in_zone",
    "DataResidencyViolation",
    "validate_residency",
]
