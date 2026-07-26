# CorvinOS Admin Control Points
## What Admin Can & Cannot Do

**Date:** 2026-07-26  
**Audience:** Operators, Enterprise Admins

---

## Matrix: Permission by Tier

| Action | Tier-0 | Tier-1 | Tier-2 | Tier-3 | Notes |
|--------|--------|--------|--------|--------|-------|
| **View status** | ✅ | ✅ | ✅ | ✅ | Dashboard always shows all plugins |
| **Read config** | ✅ | ✅ | ✅ | ✅ | See current settings |
| **Change config** | ❌ | ✅ | ✅ | ✅ | Tier-0: immutable |
| **Disable plugin** | ❌ | ❌ | ✅ | ✅ | Tier 0/1: system prevents |
| **Re-enable plugin** | N/A | N/A | ✅ | ✅ | Reload from disk |
| **Install new version** | ❌ | ❌ | ✅ | ✅ | Premium only via license |
| **Uninstall** | ❌ | ❌ | ✅ | ✅ | Delete plugin files |
| **Register extension hook** | ❌ | ✅ | ✅ | ✅ | Only allowed hooks per plugin |

---

## Tier-0 (Mandatory Compliance)

**Plugins:**
- `audit-compliance/1.0.0` — Write audit trail
- `consent-gate/1.0.0` — Enforce consent
- `flow-guard/1.0.0` — Detect PII
- `house-rules/1.0.0` — Acceptable use
- `erasure/1.0.0` — GDPR deletion

**Admin can:**
- ✅ View status (is audit writer running?)
- ✅ Read config (audit path, hash-chain status)

**Admin CANNOT:**
- ❌ Change config (immutable by design)
- ❌ Disable plugin (system prevents at registry level)
- ❌ Replace with custom version (hardcoded)

**Why:** If admin could disable audit, compliance guarantees collapse. Tripwire at boot enforces this.

**Example:** Admin tries to disable audit trail
```bash
$ corvinctl plugin disable audit-compliance/1.0.0
Error: Cannot disable tier-0 plugin (GDPR requirement)
        Admin does not have permission.
```

---

## Tier-1 (License Infrastructure)

**Plugins:**
- `a2a-orchestration/1.0.0` — Instance coordination
- `tde-routing/1.0.0` — Smart routing
- `conversation-recall/1.0.0` — User data storage
- `admin-control-plane/1.0.0` — Dashboard itself

**Admin can:**
- ✅ View status + config
- ✅ **Change config** (e.g., which regions to trust in A2A)
- ✅ **Register extension hooks** (custom routing, storage backends)

**Admin CANNOT:**
- ❌ Disable plugin (system prevents)
- ❌ Replace with fork (IP-protected)
- ❌ Remove core logic (hooks can extend, not replace)

**Why:** These are strategic. If admin could swap A2A, they get instance coordination for free (should be paid). But we DO let them extend via hooks.

---

### Tier-1 Extension Points

#### A2A Orchestration
```python
# What admin can customize:
a2a.register_hook("routing.select_target", my_routing_fn)
a2a.register_hook("envelope.pre_send", my_inspect_fn)
a2a.register_hook("attestation.custom_verify", my_extra_check_fn)

# What admin CANNOT customize:
# - Core Ed25519 signature verification (immutable)
# - Audit logging of all A2A events (compliance)
# - Denial logic (if attestation fails, DENY. Period.)
```

#### TDE Routing
```python
# What admin can customize:
tde.register_cost_model("my-internal", my_cost_fn)
tde.register_router_strategy("geo-aware", my_strategy_fn)

# What admin CANNOT customize:
# - Core token accounting (load-bearing)
# - Budget enforcement (fail-closed)
# - Fallback to native if unavailable
```

#### Conversation Recall
```python
# What admin can customize:
recall.register_storage_backend("postgres-local", pg_backend)
recall.register_storage_backend("s3-archive", s3_backend)

# What admin CANNOT customize:
# - Core encryption/decryption (data protection)
# - User identity in storage (PII scrubbing)
# - Retention policy enforcement (GDPR)
```

---

## Tier-2 (Standard Edition)

**Plugins:**
- `forge/1.0.0` — Tool generation
- `skillforge/1.0.0` — Skill generation
- `discord-bridge/1.0.0` — Discord integration
- `slack-bridge/1.0.0` — Slack integration
- `structured-logging/1.0.0` — Observability
- `basic-monitoring/1.0.0` — Health checks

**Admin can:**
- ✅ View status
- ✅ Change config
- ✅ Disable (yes, even Forge if they want)
- ✅ Re-enable
- ✅ Register hooks

**Admin CANNOT:**
- ❌ Delete permanently (would need to reinstall from package)
- ❌ Replace with fork (has to disable + install new)

**Why:** These are core UX features. Disabling breaks user experience, but technically possible. Admin won't, but system allows it.

**Example:** Admin disables Discord bridge for maintenance
```bash
$ corvinctl plugin disable discord-bridge/1.0.0
✅ discord-bridge/1.0.0 disabled
   Users will not see Discord option in UI
   
$ corvinctl plugin enable discord-bridge/1.0.0
✅ discord-bridge/1.0.0 re-enabled
```

---

## Tier-3 (Premium Plugins)

**Plugins:**
- `advanced-stт/1.0.0` — Cloud speech-to-text
- `advanced-classification/1.0.0` — ML-based data classification
- `postgres-audit-backend/1.0.0` — Custom audit storage
- `okta-auth/1.0.0` — OKTA authentication
- `splunk-integration/1.0.0` — Splunk SIEM

**Admin can:**
- ✅ View status
- ✅ Disable (if licensed + no longer needed)
- ✅ Change config

**Admin CANNOT:**
- ❌ Install without valid license key
- ❌ Use after license expires (system disables on next health check)

**Example:** Admin installs Postgres audit backend
```bash
$ corvinctl plugin install premium postgres-audit-backend/1.0.0 \
    --license-key sk_live_abc123_corvin_postgresql

✅ License validated (expires: 2027-07-26)
✅ postgres-audit-backend/1.0.0 installed

$ corvinctl plugin config postgres-audit-backend/1.0.0 \
    --db-url postgres://auditor@internal-pg.example.com:5432/corvin_audit

✅ Config updated
```

---

## Admin Dashboard Screenshots

### All Plugins View
```
PLUGINS & EXTENSIONS

🔒 TIER 0 (Mandatory Compliance)
   ✅ audit-compliance/1.0.0
      │ Status: healthy, 1,234 events/sec
      │ Config: immutable (compliance-hardened)
      │ [view details]
      │
   ✅ consent-gate/1.0.0
      │ Status: healthy, 100% gatings enforced
      │ Config: immutable (deny-by-default hardened)
      │ [view details]

🛡️  TIER 1 (License Infrastructure)
   ✅ a2a-orchestration/1.0.0
      │ Status: connected to 3 peers
      │ Config: [edit] (e.g., trusted regions)
      │ Extensions: [view hooks] (1 custom routing registered)
      │
   ✅ tde-routing/1.0.0
      │ Status: healthy, native + TDE active
      │ Config: [edit] (cost model)
      │ Extensions: [view hooks] (custom strategy registered)

⭐ TIER 2 (Standard Edition)
   ✅ forge/1.0.0
      │ Status: healthy, 50 tools created
      │ Config: [edit] (sandbox limits)
      │ [disable] [uninstall]
      │
   ✅ discord-bridge/1.0.0
      │ Status: healthy, 2 servers connected
      │ Config: [edit] (token, rate limits)
      │ [disable] [uninstall]

💳 TIER 3 (Premium)
   ✅ postgres-audit-backend/1.0.0 (licensed until 2027-07-26)
      │ Status: connected, archiving 200 events/min
      │ Config: [edit] (DB URL, retention)
      │ [disable]
      │
   ❌ okta-auth/1.0.0
      │ Status: not installed
      │ License: available ($50/month)
      │ [install with license key]
      │
   ⚠️  advanced-stт/1.0.0
      │ Status: license expired 2026-06-01
      │ Action required: renew license or plugin will disable
      │ [renew license]
```

---

## API Reference for Admins

### List Plugins with Details
```
GET /api/admin/plugins
Response:
{
  "plugins": [
    {
      "plugin_id": "audit-compliance/1.0.0",
      "tier": "tier-0",
      "status": "healthy",
      "disableable": false,
      "reason_if_not_disableable": "GDPR compliance requirement",
      "config_schema": {...},  # null for tier-0
      "hooks": []
    },
    {
      "plugin_id": "a2a-orchestration/1.0.0",
      "tier": "tier-1",
      "status": "healthy",
      "disableable": false,
      "reason_if_not_disableable": "License infrastructure (strategic IP)",
      "config_schema": {"trusted_regions": [...], ...},
      "hooks": [
        {"name": "routing.select_target", "registered_by": "custom-routing/1.0.0"},
        {"name": "attestation.custom_verify", "registered_by": null}
      ]
    },
    {
      "plugin_id": "forge/1.0.0",
      "tier": "tier-2",
      "status": "healthy",
      "disableable": true,
      "config_schema": {"sandbox_memory_mb": 512, ...},
      "hooks": []
    }
  ]
}
```

### Disable a Plugin
```
POST /api/admin/plugins/{plugin_id}/disable
Request: {}
Response:
{
  "ok": true | false,
  "message": "Plugin disabled" | "Cannot disable tier-0 plugin"
}
```

### Configure a Plugin
```
POST /api/admin/plugins/{plugin_id}/config
Request:
{
  "config": {
    "trusted_regions": ["eu", "us"],
    "attestation_timeout_ms": 5000
  }
}
Response:
{
  "ok": true,
  "message": "Config updated",
  "audit_event_id": "evt-xyz"  # Logged to audit trail
}
```

### Register Extension Hook
```
POST /api/admin/plugins/{plugin_id}/hooks
Request:
{
  "hook_name": "routing.select_target",
  "handler_plugin": "custom-routing/1.0.0"
}
Response:
{
  "ok": true,
  "message": "Hook registered"
}
```

---

## Audit Trail of Admin Actions

Every admin action is logged:
```json
{
  "timestamp": "2026-07-26T10:30:45Z",
  "event_type": "admin.plugin_disabled",
  "admin_user": "alice@company.com",
  "plugin_id": "discord-bridge/1.0.0",
  "reason": "Temporary maintenance",
  "audit_event_id": "evt-abc123"
}
```

**Compliance:** Can't be disabled. Signed, immutable.

---

## Common Scenarios

### Scenario 1: Emergency Maintenance

**Problem:** Discord bridge is failing. Need to disable it quickly.

**Solution:**
```bash
# Admin disables Discord bridge
corvinctl plugin disable discord-bridge/1.0.0

# Users won't see Discord option
# All other plugins continue working
# Audit: "admin.plugin_disabled" logged

# After maintenance, re-enable
corvinctl plugin enable discord-bridge/1.0.0
```

**What's protected:** Tier-0 compliance still runs. Audit still writes. Consent still enforced.

---

### Scenario 2: Custom Routing

**Problem:** Organization has 3 internal CorvinOS instances. Need smart routing.

**Solution:**
```python
# Admin (or engineer) creates custom plugin
class GeoAwareRoutingPlugin(CorvinPlugin):
    def on_load(self, ctx):
        a2a = ctx.registry.registry["a2a-orchestration/1.0.0"]
        a2a.register_hook("routing.select_target", self.my_routing)
    
    def my_routing(self, envelope):
        # Route based on user region
        if envelope.user_region == "eu":
            return self.eu_instance
        return self.us_instance
```

Admin installs plugin:
```bash
corvinctl plugin install custom-routing/1.0.0
```

Result: A2A uses custom routing, but core (attestation, audit) is unchanged.

---

### Scenario 3: Premium Feature Renewal

**Problem:** Postgres audit backend license expires in 30 days.

**Solution:**
```bash
# Dashboard warns admin

# Admin renews license
corvinctl plugin license-renew postgres-audit-backend/1.0.0 \
    --license-key sk_live_new_key_2027

✅ License renewed (expires 2027-07-26)
```

**Fallback:** If admin forgets, system logs warning, then disables plugin on expiry date.

---

## Summary: Permission Model

```
Admin is EMPOWERED to:
  ✅ See all plugins + their tiers
  ✅ Understand why something is mandatory
  ✅ Customize (config) Tier-1/2/3
  ✅ Extend (hooks) Tier-1/2/3
  ✅ Disable (pause) Tier-2/3
  ✅ Install premium features

Admin is PREVENTED from:
  ❌ Disabling compliance (Tier-0)
  ❌ Disabling infrastructure IP (Tier-1)
  ❌ Replacing core logic (only extension)
  ❌ Installing premium without license
  ❌ Modifying audit trail
```

This keeps operators in control while protecting the business + compliance model.

