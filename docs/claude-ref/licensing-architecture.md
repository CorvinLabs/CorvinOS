# Licensing Architecture Implementation Spec

**Source:** ADR-0363  
**Status:** Proposed  
**Last Updated:** 2026-08-17

---

## Overview

This document provides implementation details for the licensing architecture supporting Brain v0.2
and Forge features. It is the definitive guide for implementing the five-layer licensing gate
system.

---

## License Key Structure

```python
from dataclasses import dataclass
from typing import FrozenSet, Literal
from datetime import datetime

@dataclass(frozen=True)
class LicenseKey:
    """Immutable, cryptographically-signed license key."""
    
    # Identity
    tenant_id: str
    
    # Tier (controls feature access)
    tier: Literal["free", "standard", "professional", "enterprise"]
    
    # Validity period
    issued_at: str    # ISO 8601: "2026-08-17T00:00:00Z"
    expires_at: str   # ISO 8601: "2027-08-17T00:00:00Z"
    
    # Feature flags (per-tier customization)
    features: FrozenDict[str, bool]  # {
                                      #   "brain": True,
                                      #   "tool_forge": True,
                                      #   "skill_forge": True,
                                      #   "voice_guidance": False,
                                      # }
    
    # Quota limits (per-tier defaults)
    quotas: FrozenDict[str, int]     # {
                                      #   "brain_tasks_per_day": 100,
                                      #   "tool_forge_per_day": 50,
                                      #   "skill_forge_per_day": 20,
                                      # }
    
    # Cryptography
    public_key_id: str    # "pk_2026_08_anthropic_1"
    signature: bytes      # Ed25519(SHA256(canonical_json))
    
    @classmethod
    def default_free(cls, tenant_id: str) -> "LicenseKey":
        """Create a free-tier license (no features except builtin plugin)."""
        return cls(
            tenant_id=tenant_id,
            tier="free",
            issued_at=datetime.now().isoformat() + "Z",
            expires_at="2999-12-31T23:59:59Z",  # Never expires
            features=FrozenDict({"brain": False, "tool_forge": False, "skill_forge": False}),
            quotas=FrozenDict({"max_plugins": 1}),
            public_key_id="none",
            signature=b"",  # No signature for default free tier
        )
```

---

## License Validator (L48–L52 Gates)

### Gate L48: Brain Feature Gate

```python
class LicenseValidator:
    def __init__(
        self,
        license_store: "LicenseStore",
        pubkeys: dict[str, "Ed25519PublicKey"],
    ):
        self.store = license_store
        self.pubkeys = pubkeys  # Anthropic-signed public keys
    
    async def check_brain_enabled(self, tenant_id: str) -> None:
        """Gate L48: Check if Brain v0.2 is enabled in license.
        
        Raises:
            FeatureLocked: If Brain is disabled or license missing
            LicenseExpired: If license has expired
            LicenseInvalid: If signature verification fails
        """
        license = await self.store.get_license(tenant_id)
        
        # Verify signature (fail-closed)
        if not self._verify_signature(license):
            raise LicenseInvalid(
                "License signature verification failed",
                tenant_id=tenant_id,
                public_key_id=license.public_key_id,
            )
        
        # Check expiration (fail-closed)
        if datetime.fromisoformat(license.expires_at.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            raise LicenseExpired(
                f"License expired on {license.expires_at}",
                expires_at=license.expires_at,
                tier=license.tier,
            )
        
        # Check feature enabled
        if not license.features.get("brain", False):
            raise FeatureLocked(
                title="Brain v0.2 requires license",
                message=f"Brain v0.2 is not included in {license.tier.title()} tier.",
                feature="brain",
                current_tier=license.tier,
                required_tier="standard" if license.tier == "free" else "professional",
                upsell_url=f"https://corvin.io/pricing?feature=brain&tier={license.tier}",
            )
```

### Gate L49: Tool Forge Gate

```python
    async def check_tool_forge_enabled(self, tenant_id: str) -> None:
        """Gate L49: Check if Tool Forge is enabled + quota available.
        
        Raises:
            FeatureLocked: If Tool Forge is disabled
            QuotaExceeded: If daily quota is exhausted
            LicenseExpired: If license has expired
        """
        license = await self.store.get_license(tenant_id)
        
        # Verify signature + expiration (same as L48)
        if not self._verify_signature(license):
            raise LicenseInvalid(...)
        
        if datetime.fromisoformat(license.expires_at.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            raise LicenseExpired(...)
        
        # Check feature enabled
        if not license.features.get("tool_forge", False):
            raise FeatureLocked(
                title="Tool Forge requires license",
                message=f"Tool Forge is not included in {license.tier.title()} tier.",
                feature="tool_forge",
                current_tier=license.tier,
                required_tier="standard",
                upsell_url="https://corvin.io/pricing?feature=tool_forge",
            )
        
        # Check quota
        await self._check_quota(tenant_id, "tool_forge", 1)
```

### Gate L50: Skill Forge Gate

```python
    async def check_skill_forge_enabled(self, tenant_id: str) -> None:
        """Gate L50: Check if Skill Forge is enabled + quota available."""
        license = await self.store.get_license(tenant_id)
        
        if not self._verify_signature(license):
            raise LicenseInvalid(...)
        
        if datetime.fromisoformat(license.expires_at.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            raise LicenseExpired(...)
        
        if not license.features.get("skill_forge", False):
            raise FeatureLocked(
                title="Skill Forge requires license",
                feature="skill_forge",
                current_tier=license.tier,
                required_tier="standard",
                upsell_url="https://corvin.io/pricing?feature=skill_forge",
            )
        
        await self._check_quota(tenant_id, "skill_forge", 1)
```

### Gate L51: Plugin Limit Gate

```python
    async def check_plugin_limit(self, tenant_id: str) -> None:
        """Gate L51: Check if customer is below plugin limit."""
        license = await self.store.get_license(tenant_id)
        
        if not self._verify_signature(license):
            raise LicenseInvalid(...)
        
        max_plugins = license.quotas.get("max_plugins", 1)
        current_plugins = await self.store.count_plugins(tenant_id)
        
        if current_plugins >= max_plugins:
            upgrade_tier = {
                "free": "standard",
                "standard": "professional",
                "professional": "enterprise",
            }.get(license.tier, "enterprise")
            
            raise PluginLimitExceeded(
                title="Plugin limit reached",
                message=f"You have {current_plugins}/{max_plugins} plugins installed.",
                current_count=current_plugins,
                limit=max_plugins,
                upgrade_to_tier=upgrade_tier,
                upgrade_url=f"https://corvin.io/pricing?tier={upgrade_tier}",
            )
```

### Gate L52: Quota Meter (Post-Execution)

```python
    async def record_usage(
        self,
        tenant_id: str,
        feature: str,
        amount: int = 1,
    ) -> None:
        """Gate L52: Record quota usage after successful feature execution.
        
        Called only if Gates L48–L51 pass. Records to audit trail + Redis.
        """
        await self.usage_logger.record(tenant_id, feature, amount)
        
        # Also log to audit trail
        await audit_trail.write_event(
            event_type="license_usage",
            tenant_id=tenant_id,
            details={
                "feature": feature,
                "amount": amount,
                "timestamp": datetime.now().isoformat(),
            },
        )
```

### Signature Verification

```python
    def _verify_signature(self, license: LicenseKey) -> bool:
        """Verify license was signed by Anthropic using Ed25519.
        
        Returns:
            True if signature is valid
            False if public key not found or signature is invalid
        """
        if not license.public_key_id:
            return False  # Free tier with no signature is OK
        
        pubkey = self.pubkeys.get(license.public_key_id)
        if not pubkey:
            return False  # Unknown key ID; fail-closed
        
        # Reconstruct the canonical JSON that was signed
        canonical = self._canonical_license_json(license)
        
        try:
            pubkey.verify(license.signature, canonical.encode())
            return True
        except cryptography.exceptions.InvalidSignature:
            return False
    
    def _canonical_license_json(self, license: LicenseKey) -> str:
        """Reconstruct canonical JSON (deterministic, for signature verification)."""
        return json.dumps(
            {
                "tenant_id": license.tenant_id,
                "tier": license.tier,
                "issued_at": license.issued_at,
                "expires_at": license.expires_at,
                "features": dict(license.features),
                "quotas": dict(license.quotas),
            },
            sort_keys=True,
            separators=(",", ":"),  # No spaces
        )
```

---

## License Store (Dual Layer: PostgreSQL + Redis)

```python
class LicenseStore:
    """Hybrid storage: PostgreSQL (authoritative) + Redis (cache)."""
    
    def __init__(self, db_pool: asyncpg.Pool, redis: aioredis.Redis):
        self.db = db_pool
        self.redis = redis
    
    async def get_license(self, tenant_id: str) -> LicenseKey:
        """Get license, with Redis cache fallback to PostgreSQL.
        
        Lookup order:
            1. Redis cache (24h TTL)
            2. PostgreSQL
            3. Default free tier
        """
        # Try cache
        cache_key = f"license:{tenant_id}"
        if cached := await self.redis.get(cache_key):
            return LicenseKey.from_json(cached)
        
        # Query PostgreSQL
        row = await self.db.fetchrow(
            "SELECT * FROM licenses WHERE tenant_id = $1",
            tenant_id,
        )
        
        if row:
            license = LicenseKey.from_db_row(row)
        else:
            # No license = free tier
            license = LicenseKey.default_free(tenant_id)
        
        # Cache for 24h
        await self.redis.setex(cache_key, 86400, license.to_json())
        return license
    
    async def set_license(self, license: LicenseKey) -> None:
        """Store or update a license.
        
        Writes to PostgreSQL (authoritative) and invalidates Redis cache.
        """
        await self.db.execute(
            """
            INSERT INTO licenses (tenant_id, tier, issued_at, expires_at, features, quotas, public_key_id, signature)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (tenant_id) DO UPDATE SET
                tier = EXCLUDED.tier,
                issued_at = EXCLUDED.issued_at,
                expires_at = EXCLUDED.expires_at,
                features = EXCLUDED.features,
                quotas = EXCLUDED.quotas,
                public_key_id = EXCLUDED.public_key_id,
                signature = EXCLUDED.signature
            """,
            license.tenant_id,
            license.tier,
            license.issued_at,
            license.expires_at,
            json.dumps(dict(license.features)),
            json.dumps(dict(license.quotas)),
            license.public_key_id,
            license.signature,
        )
        
        # Invalidate cache
        await self.redis.delete(f"license:{license.tenant_id}")
    
    async def count_plugins(self, tenant_id: str) -> int:
        """Count installed plugins for a tenant."""
        result = await self.db.fetchval(
            "SELECT COUNT(*) FROM plugins WHERE tenant_id = $1",
            tenant_id,
        )
        return result or 0
```

### PostgreSQL Schema

```sql
CREATE TABLE licenses (
    id SERIAL PRIMARY KEY,
    tenant_id VARCHAR(255) UNIQUE NOT NULL,
    tier VARCHAR(50) NOT NULL CHECK (tier IN ('free', 'standard', 'professional', 'enterprise')),
    issued_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    features JSONB NOT NULL DEFAULT '{}',
    quotas JSONB NOT NULL DEFAULT '{}',
    public_key_id VARCHAR(255),
    signature BYTEA,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    INDEX idx_tenant_id (tenant_id),
    INDEX idx_expires_at (expires_at)
);

-- Trigger to update updated_at
CREATE TRIGGER update_licenses_updated_at
BEFORE UPDATE ON licenses
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
```

---

## Usage Logger (Quota Metering)

```python
class UsageLogger:
    """Track daily feature usage per tenant."""
    
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis
    
    async def record(
        self,
        tenant_id: str,
        feature: str,
        amount: int = 1,
    ) -> None:
        """Record usage of a feature.
        
        Usage key format: "{tenant_id}:{feature}:{date_iso}"
        Expires automatically at UTC midnight + 24h.
        """
        today = datetime.now().date().isoformat()
        key = f"usage:{tenant_id}:{feature}:{today}"
        
        # Increment counter
        await self.redis.incr(key, amount)
        
        # Set 24h expiry (auto-reset at UTC midnight + 24h)
        await self.redis.expire(key, 86400)
    
    async def get_daily_usage(self, tenant_id: str, feature: str) -> int:
        """Get today's usage count for a feature."""
        today = datetime.now().date().isoformat()
        key = f"usage:{tenant_id}:{feature}:{today}"
        
        count = await self.redis.get(key)
        return int(count or 0)
    
    async def get_historical_usage(
        self,
        tenant_id: str,
        feature: str,
        days: int = 30,
    ) -> dict[str, int]:
        """Get historical usage (requires Redis Streams or query PostgreSQL audit trail)."""
        # For now, query audit trail (PostgreSQL)
        rows = await db.fetch(
            """
            SELECT
                DATE(created_at) as date,
                COUNT(*) as count
            FROM audit_log
            WHERE tenant_id = $1
                AND event_type = 'license_usage'
                AND details->>'feature' = $2
                AND created_at >= NOW() - INTERVAL '$3 days'
            GROUP BY DATE(created_at)
            ORDER BY date DESC
            """,
            tenant_id,
            feature,
            days,
        )
        return {row["date"].isoformat(): row["count"] for row in rows}
```

---

## Error Types (User-Facing)

### FeatureLocked Exception

```python
class FeatureLocked(Exception):
    """Feature is not enabled in the customer's license tier."""
    
    def __init__(
        self,
        title: str,
        message: str,
        feature: str,
        current_tier: str,
        required_tier: str,
        upsell_url: str,
    ):
        self.title = title
        self.message = message
        self.feature = feature
        self.current_tier = current_tier
        self.required_tier = required_tier
        self.upsell_url = upsell_url
        
        super().__init__(f"{title}: {message}")
    
    def to_api_response(self) -> dict:
        """Serialize to JSON for API response."""
        return {
            "error": "feature_locked",
            "title": self.title,
            "message": self.message,
            "feature": self.feature,
            "current_tier": self.current_tier,
            "required_tier": self.required_tier,
            "upsell_url": self.upsell_url,
        }
```

### QuotaExceeded Exception

```python
class QuotaExceeded(Exception):
    """Daily quota for a feature has been exhausted."""
    
    def __init__(
        self,
        title: str,
        message: str,
        feature: str,
        used_today: int,
        quota: int,
        reset_time: str,  # ISO 8601
        upgrade_suggestion: str = "",
        upgrade_tier: str = "",
        upgrade_url: str = "",
    ):
        self.title = title
        self.message = message
        self.feature = feature
        self.used_today = used_today
        self.quota = quota
        self.reset_time = reset_time
        self.upgrade_suggestion = upgrade_suggestion
        self.upgrade_tier = upgrade_tier
        self.upgrade_url = upgrade_url
        
        super().__init__(f"{title}: {message}")
    
    def to_api_response(self) -> dict:
        """Serialize to JSON for API response."""
        return {
            "error": "quota_exceeded",
            "title": self.title,
            "message": self.message,
            "feature": self.feature,
            "used_today": self.used_today,
            "quota": self.quota,
            "reset_time": self.reset_time,
            "upgrade_suggestion": self.upgrade_suggestion,
            "upgrade_tier": self.upgrade_tier,
            "upgrade_url": self.upgrade_url,
        }
```

### PluginLimitExceeded Exception

```python
class PluginLimitExceeded(Exception):
    """Plugin limit for current tier has been reached."""
    
    def __init__(
        self,
        title: str,
        message: str,
        current_count: int,
        limit: int,
        upgrade_to_tier: str = "",
        upgrade_url: str = "",
    ):
        self.title = title
        self.message = message
        self.current_count = current_count
        self.limit = limit
        self.upgrade_to_tier = upgrade_to_tier
        self.upgrade_url = upgrade_url
        
        super().__init__(f"{title}: {message}")
    
    def to_api_response(self) -> dict:
        return {
            "error": "plugin_limit_exceeded",
            "title": self.title,
            "message": self.message,
            "current_count": self.current_count,
            "limit": self.limit,
            "upgrade_to_tier": self.upgrade_to_tier,
            "upgrade_url": self.upgrade_url,
        }
```

---

## Integration Points

### Brain Startup (L48 Gate)

```python
# core/brain/orchestrator.py

async def initialize_brain(tenant_id: str) -> TaskBrain:
    """Initialize Brain subsystem with license check (L48)."""
    try:
        await license_validator.check_brain_enabled(tenant_id)
    except FeatureLocked as e:
        # Feature is locked; raise to caller
        raise e
    except LicenseExpired as e:
        # License expired; raise to caller
        raise e
    
    # Brain is licensed; proceed with initialization
    return TaskBrain(
        tenant_id=tenant_id,
        health_monitor=HealthMonitor(),
        loop_engineer=LoopEngineer(),
        # ... other subsystems
    )
```

### Tool Forge Request (L49 Gate)

```python
# core/forge/tool_registry.py

async def forge_tool_from_impl(tenant_id: str, impl: str) -> ToolSpec:
    """Forge a tool with license + quota check (L49)."""
    try:
        await license_validator.check_tool_forge_enabled(tenant_id)
    except (FeatureLocked, QuotaExceeded) as e:
        raise e
    
    # Generate tool
    tool = await async_registry.forge_tool(impl)
    
    # Record usage
    await license_validator.record_usage(tenant_id, "tool_forge", 1)
    
    return tool
```

### Skill Forge Request (L50 Gate)

```python
# core/forge/skill_registry.py

async def skill_create(tenant_id: str, body_md: str) -> SkillRecord:
    """Create a skill with license + quota check (L50)."""
    try:
        await license_validator.check_skill_forge_enabled(tenant_id)
    except (FeatureLocked, QuotaExceeded) as e:
        raise e
    
    skill = await async_registry.skill_create(body_md)
    
    await license_validator.record_usage(tenant_id, "skill_forge", 1)
    
    return skill
```

### Plugin Creation (L51 Gate)

```python
# core/plugins/registry.py

def create_plugin(tenant_id: str, plugin_spec: dict) -> None:
    """Create a plugin with limit check (L51)."""
    try:
        await license_validator.check_plugin_limit(tenant_id)
    except PluginLimitExceeded as e:
        raise e
    
    plugin_registry.register(tenant_id, plugin_spec)
```

---

## CLI Commands

### Issue License

```bash
$ corvin-cli license issue \
    --tenant-id "org-acme-corp" \
    --tier "standard" \
    --expires-in "1 year" \
    --output license.json

License issued successfully.
Tenant:     org-acme-corp
Tier:       standard
Expires:    2027-08-17
Signature:  Ed25519 (pk_2026_08_anthropic_1)
Output:     license.json
```

### Install License

```bash
$ corvin-cli license install license.json

License installed successfully.
Tenant:     org-acme-corp
Tier:       standard
Expires:    2027-08-17
Features:   brain, tool_forge, skill_forge
Quotas:     100 brain_tasks/day, 50 tool_forge/day, 20 skill_forge/day
```

### View License Info

```bash
$ corvin-cli license info

Current License
Tenant:     org-acme-corp
Tier:       standard
Expires:    2027-08-17 (in 364 days)
Features:   brain, tool_forge, skill_forge
Quotas:     100 brain_tasks/day, 50 tool_forge/day, 20 skill_forge/day

Today's Usage
Brain:      0 / 100
Tool Forge: 12 / 50
Skill Forge: 3 / 20
Plugins:    2 / 5
```

---

## Testing Strategy

### Unit Tests
- Signature verification (valid, tampered, unknown key)
- Quota reset via Redis TTL
- Fail-closed behavior (missing license, expired, invalid signature)
- Canonical JSON reconstruction (deterministic)

### Integration Tests
- Full flow: issue → install → verify → access feature
- Quota exhaustion → wait 24h → quota reset
- Multi-tenant isolation (tenant A's usage doesn't affect B)

### E2E Tests
- CLI flow: `issue` → `install` → feature works
- Console: upgrade to Standard → unlock Tool Forge → forge tools until quota → see quota error
- Audit trail: all license checks appear in audit.jsonl

---

## Monitoring & Observability

### Metrics
- `license.checks_total` — count of license checks by result (granted, denied, expired)
- `license.quota_usage` — gauge of daily usage per feature per tenant
- `license.signature_verifications` — count of signature verification attempts by result

### Logging
- Log all license checks to audit trail (event_type: "license_check")
- Log quota usage (event_type: "license_usage")
- Log license renewals (event_type: "license_renewed")

### Dashboards
- Admin dashboard: quota usage per tenant, top 10 quota consumers, expiring licenses
- Support dashboard: tenant license status, quota reset times, usage trends

---

## Troubleshooting

### "License signature verification failed"
- Check that `public_key_id` matches a key in the validator's keyring
- Verify license file was not corrupted or edited
- Contact Anthropic to refresh public keys (monthly rotation)

### "Feature locked" on Standard tier
- Verify license.json was installed (`corvin-cli license info`)
- Check license has not expired
- Check feature is enabled in license JSON

### "Quota exceeded" message
- Check quota reset time in error message
- Upgrade to Professional tier for higher limits
- Contact Anthropic for overage options (v1.0)
