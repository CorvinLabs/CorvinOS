"""Licensing 1.0.0 (Phase 2.5, T3.1) — Plugin monetization + tier gating

Tier-A (Free): 10 skills, community plugins
Tier-B (Pro): 50 skills, priority support
Tier-C (Enterprise): unlimited, SLA
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, List
from datetime import datetime, timedelta

class TierType(Enum):
    TIER_A = "tier_a"  # Free
    TIER_B = "tier_b"  # Pro
    TIER_C = "tier_c"  # Enterprise

@dataclass
class License:
    plugin_id: str
    tier: TierType
    issued_at: str
    expires_at: str
    active: bool = True
    
    def is_valid(self) -> bool:
        now = datetime.utcnow()
        expires = datetime.fromisoformat(self.expires_at.replace('Z', '+00:00'))
        return self.active and now < expires

class LicensingEngine:
    """Enforce plugin licensing tiers."""
    
    def __init__(self):
        self.licenses: Dict[str, License] = {}
        self.tier_limits = {
            TierType.TIER_A: 10,
            TierType.TIER_B: 50,
            TierType.TIER_C: float('inf')
        }
    
    async def issue_license(self, plugin_id: str, tier: TierType, days: int = 365) -> License:
        """Issue plugin license."""
        now = datetime.utcnow()
        expires = (now + timedelta(days=days)).isoformat() + "Z"
        
        license = License(
            plugin_id=plugin_id,
            tier=tier,
            issued_at=now.isoformat() + "Z",
            expires_at=expires
        )
        self.licenses[plugin_id] = license
        return license
    
    async def check_license(self, plugin_id: str) -> bool:
        """Verify plugin license is valid."""
        license = self.licenses.get(plugin_id)
        return license and license.is_valid() if license else False
    
    async def enforce_tier_limit(self, plugin_id: str, tier: TierType, skill_count: int) -> bool:
        """Enforce skill limit for tier."""
        limit = self.tier_limits[tier]
        return skill_count <= limit
    
    async def revoke_license(self, plugin_id: str) -> None:
        """Revoke plugin license."""
        if plugin_id in self.licenses:
            self.licenses[plugin_id].active = False
