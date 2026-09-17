"""
Licensing System 1.0.0 — Tier A/B/C Enforcement (ADR-0700-0704)
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class LicenseQuota:
    tier: str
    max_plugins: int
    max_mcp_tools: int
    max_skills: int
    max_concurrent_workflows: int
    max_api_calls_per_hour: int
    storage_gb: int


@dataclass
class PluginLicense:
    plugin_id: str
    name: str
    tier: str
    author: str
    version: str
    issued_at: datetime
    expires_at: Optional[datetime] = None
    signature: Optional[str] = None
    
    def is_valid(self) -> bool:
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True


@dataclass
class TenantLicenseStatus:
    tenant_id: str
    license_tier: str
    quota: LicenseQuota
    plugins: Dict[str, PluginLicense] = field(default_factory=dict)
    usage: Dict[str, int] = field(default_factory=dict)
    last_verified: datetime = field(default_factory=datetime.utcnow)
    
    def check_quota(self, metric: str, delta: int = 1) -> bool:
        if self.license_tier == "A":
            return True
        limits = {
            'plugins': self.quota.max_plugins,
            'mcp_tools': self.quota.max_mcp_tools,
            'skills': self.quota.max_skills,
            'concurrent_workflows': self.quota.max_concurrent_workflows,
        }
        if metric not in limits:
            return True
        current = self.usage.get(metric, 0)
        return (current + delta) <= limits[metric]


class LicenseValidator:
    TIER_QUOTAS = {
        "A": LicenseQuota("A", 100, 50, 50, 10, 10000, 100),
        "B": LicenseQuota("B", 50, 25, 25, 5, 1000, 10),
        "C": LicenseQuota("C", 1000, 500, 500, 100, 100000, 1000),
    }
    
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._status: Optional[TenantLicenseStatus] = None
    
    def load_license(self) -> TenantLicenseStatus:
        if self._status:
            return self._status
        self._status = TenantLicenseStatus(
            tenant_id=self.tenant_id,
            license_tier="B",
            quota=self.TIER_QUOTAS["B"],
        )
        return self._status
    
    def validate_plugin_load(self, plugin_id: str) -> bool:
        status = self.load_license()
        if not status.check_quota('plugins', 1):
            raise LicenseError(f'Plugin quota exceeded')
        return True


class LicenseError(Exception):
    pass
