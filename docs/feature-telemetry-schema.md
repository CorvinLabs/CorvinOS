# CorvinOS Feature Telemetry Schema (ADR-0212)

**Status:** DRAFT (Design Phase)  
**Compliance:** GDPR Art. 6(1)(f) legitimate interest, EU AI Act Art. 50  
**Consent Gate:** `ping_enabled` (reuse existing)  
**Cadence:** ~5min (piggybacked on existing heartbeat)  
**Changelog:** Instance-level feature snapshot aggregation; closed-enum validation

---

## Core Design

Feature telemetry captures **what CorvinOS capabilities are actually used** without tracking *actions*, only *configuration + aggregate counts*.

### Payload Structure

```json
{
  "schema_version": "feature_snapshot/1",
  "instance_id": "pseudonymous_uuid",
  "timestamp": "2026-07-22T14:23:10Z",
  "version": "0.10.57",
  "platform": "linux",      // linux, windows, macos (closed enum)
  "python_minor": 11,        // 11, 12, 13, ... (int)
  
  "engines": {
    "primary": "claude",     // claude, hermes, openrouter, openlama (closed enum)
    "fallback": "hermes",    // null or closed enum
    "models": {
      "default": "opus",     // opus, sonnet, haiku, fable (closed enum)
      "fast": "haiku",
      "vision": "opus"
    }
  },
  
  "features": {
    "bridges_connected": ["discord", "telegram", "slack"],    // subset of all bridges
    "bridges_messages_7d": 142,                                // count, no per-user tracking
    "ldd_enabled": true,                                       // feature gated?
    "ldd_layers_used": 6,                                      // numeric count
    "a2a_delegations_count": 3,                                // lifetime count (coarse)
    "workflows_created_count": 1,                              // count, not content
    "workflows_run_count": 5,                                  // execution count
    "browser_automation_used": true,                           // boolean feature flag
    "compute_jobs_count": 2,                                   // ACS/Compute usage
    "forge_tools_created": 1,                                  // tool generation
    "skills_created": 0,                                       // skill forge
    "voice_sessions": 12,                                      // total voice turns
    "artifacts_created": 4,                                    // artifact usage
    "console_accessed": true,                                  // was console ever opened?
    "agent_types_used": ["general-purpose", "code-reviewer"],  // closed enum array
    "mcp_servers_connected": ["playground", "github"]          // MCP server discovery
  }
}
```

---

## Feature Matrix (Dashboard)

The **Ecosystem Feature Heatmap** displays instances × features:

```
Instance ID  | Bridges | LDD | A2A | Workflows | Browser | Compute | Created
─────────────┼─────────┼─────┼─────┼───────────┼─────────┼─────────┼────────
inst-a1b2   | 3       | ✓   | 1   | 1/5       | ✗       | 2       | 2026-01
inst-c3d4   | 1       | ✗   | 0   | 0/0       | ✓       | 0       | 2026-06
inst-e5f6   | 6       | ✓   | 5   | 3/12      | ✓       | 4       | 2026-02
─────────────┴─────────┴─────┴─────┴───────────┴─────────┴─────────┴────────
Adoption %   | 33%     | 50% | 25% | 33%       | 33%     | 25%     |
```

Colors: Green (>50% adoption), Yellow (20-50%), Gray (<20%)

---

## Collection Strategy

### Local Snapshot

File: `~/.corvin/telemetry/feature_snapshot.json`

```python
# Collected every 5 minutes by heartbeat engine
def collect_feature_snapshot(home: Path) -> dict:
    """Aggregate feature usage from local state."""
    
    # 1. Bridges connected (from settings.json in each bridge dir)
    bridges_connected = []
    for bridge_name in ["discord", "telegram", "slack", "teams", "email", "signal", "whatsapp"]:
        settings_path = home / "bridges" / bridge_name / "settings.json"
        if settings_path.exists() and settings_path.stat().st_size > 100:  # crude "configured" check
            bridges_connected.append(bridge_name)
    
    # 2. LDD enabled (from .ldd/ directory structure)
    ldd_layers = 0
    ldd_enabled = (home / ".ldd" / "ldd.json").exists()
    if ldd_enabled:
        ldd_layers = len(list((home / ".ldd").glob("*.log")))
    
    # 3. A2A delegations (count from audit log, coarse bin)
    a2a_count = _count_audit_events(home, "a2a_send", window_days=30)
    
    # 4. Workflows (from .corvin/workflows/ or console DB if available)
    workflows = _count_workflows(home)
    
    # 5. Browser automation (from .corvin/browser/ or logs)
    browser_used = (home / "browser" / "session").exists()
    
    # 6. Compute jobs (ACS calls from audit)
    compute_count = _count_audit_events(home, "acs_delegate", window_days=30)
    
    # 7. Voice sessions (from audit event type "voice_turn")
    voice_count = _count_audit_events(home, "voice_turn", window_days=30)
    
    return {
        "bridges_connected": sorted(bridges_connected),
        "ldd_enabled": ldd_enabled,
        "ldd_layers_used": ldd_layers,
        "a2a_delegations_count": a2a_count,
        "workflows_created_count": workflows["created"],
        "workflows_run_count": workflows["executed"],
        "browser_automation_used": browser_used,
        "compute_jobs_count": compute_count,
        "voice_sessions": voice_count,
        # ... etc
    }
```

### Transmission (5min heartbeat)

Append to existing ping payload:

```python
# In heartbeat.py
ping_payload["features"] = load_local_snapshot()
```

### Validation (Fail-Closed)

```python
def _assert_safe_features(snapshot: dict) -> dict:
    """Drop any non-closed-enum field. Fail-closed backstop."""
    
    ALLOWED_BRIDGES = {"discord", "telegram", "slack", "teams", "email", "signal", "whatsapp"}
    ALLOWED_ENGINES = {"claude", "hermes", "openrouter", "openlama"}
    ALLOWED_MODELS = {"opus", "sonnet", "haiku", "fable"}
    ALLOWED_AGENT_TYPES = {"general-purpose", "code-reviewer", "explore", "plan", ...}
    
    # Validate closed enums
    if "bridges_connected" in snapshot:
        bridges = snapshot.get("bridges_connected", [])
        if not all(b in ALLOWED_BRIDGES for b in bridges):
            snapshot["bridges_connected"] = [b for b in bridges if b in ALLOWED_BRIDGES]
    
    # Drop any non-numeric count
    for count_field in ["a2a_delegations_count", "workflows_run_count", ...]:
        if count_field in snapshot:
            val = snapshot[count_field]
            if not isinstance(val, int) or val < 0:
                del snapshot[count_field]
    
    # Drop any freeform string
    for field in ["feature_summary", "note", "comment"]:
        if field in snapshot:
            del snapshot[field]
    
    return snapshot
```

---

## GDPR Compliance

### Legitimate Interest Assessment

**Legal Basis:** GDPR Art. 6(1)(f) legitimate interest  
**Purpose:** Understand which CorvinOS features are actually used in production  
**Necessity:** Without telemetry, cannot prioritize bug fixes / feature work; maintainer decisions are blind  
**Proportionality:** Data is aggregated (no per-action tracking), anonymous (pseudonymous instance_id), CONTENT-FREE (closed enums only, no prompts/users/content)

### Data Minimization

✓ Only counts, not logs  
✓ Only configuration (is feature enabled), not behavior (actions taken)  
✓ Only closed enums, never freeform strings  
✓ No PII, no prompts, no user identifiers  
✓ No location beyond platform (no IP, no geo)  
✓ Dropped by fail-closed validator if schema violated  

### Consent

✓ Reuses existing `ping_enabled` opt-out gate (GDPR Art. 7 explicit opt-out)  
✓ Default-ON (maintainer decision for Corvin-Logs real data)  
✓ Disabling ping disables all telemetry (unified control)  

### Retention

7 days (same as ping storage); older snapshots auto-rotated

---

## Implementation Phases

**Phase 1 (k=1-2):** Schema + ADR + Local collection logic  
**Phase 2 (k=3):** Integrate into heartbeat.py + validation  
**Phase 3 (k=4):** Stats API endpoint (/stats/features) + backend aggregation  
**Phase 4 (k=5):** Dashboard tab "Ecosystem Feature Heatmap" + E2E test  

---

## Testing Strategy

- Unit: _assert_safe_features with invalid enums → drops them
- Unit: collect_feature_snapshot on mock home → correct counts
- Integration: snapshot written to file, read by heartbeat, appended to ping
- E2E: ping endpoint returns features payload, frontend renders heatmap

