# ADR-0544 AMENDMENT REQUEST — SPIKE 1 BLOCKER RESOLUTION
**Priority:** CRITICAL | **Deadline:** Sept 3 10:00 UTC | **Requestor:** Active Steering

---

## CONTEXT
Spike 1 (feature_flags.py → Skills API rewrite) cannot proceed without architectural clarity. 4 blocking questions identified during scoping. **ADR-0544 must provide definitive answers** to unblock Phase 1 Big Bang execution.

---

## 4 CRITICAL ANSWERS REQUIRED

### **ANSWER 1: FLAG-TO-SKILLS MAPPING**

**Question:** How do the 60 registered feature flags map to Skills in the new architecture?

**Options:**
- **Option 1a:** Each flag → separate Skill (60 Skills total)
  - Pros: 1:1 mapping, simple
  - Cons: 60 separate Skill manifests, versioning overhead
- **Option 1b:** Flags → condensed composite Skills (e.g., 10–15 super-Skills grouping related flags)
  - Pros: Simpler manifest structure, fewer versions to track
  - Cons: Loss of granularity, operator UX impact
- **Option 1c:** Flags remain in feature_flags.py, Skills manage OTHER concerns (context, routing, etc.)
  - Pros: Minimal refactor, parallel migration
  - Cons: Dual system (flags + Skills) adds tech debt

**Required Output:**
```yaml
flag_to_skill_mapping:
  # Example:
  "execution_context_badge": "os.observability_flags"
  "ccc_command_routing": "os.chat_flags"
  "bridge_task_supervision": "os.bridge_flags"
  # ... all 60 flags
  
skill_manifest_structure:
  # Example structure for os.chat_flags:
  - skill_id: os.chat_flags
    version: "0.1.0"
    config_params:
      - flag_id: execution_context_badge
        default: false
      - flag_id: ccc_command_routing
        default: false
```

---

### **ANSWER 2: ARCHITECTURE CHOICE (WRAPPER VS BIG BANG)**

**Question:** For Phase 1b (Weeks 1–10), do we rewrite ALL 88 call-sites immediately (Big Bang) or use a compatibility wrapper for Phase 1b?

**Options:**
- **Option 2a: Big Bang** (ADR-0544 current intent)
  - All 88 files refactored in parallel during 10-week window
  - Calls move from `feature_flags.is_enabled(flag_id)` → `skills_registry.execute("feature_flags", {"flag_id": flag_id})`
  - Pros: Clean break, no dual system, single audit trail
  - Cons: High parallelization complexity, 88–176 hours refactor work, higher risk of regressions
  
- **Option 2b: Wrapper + Phased Migration** (Phase 1b gradual, Phase 2 cleanup)
  - Spike 1: Rewrite feature_flags.py as Skill + FeatureFlagLegacyAdapter wrapper
  - Phase 1b: Wrapper transparently delegates to Skill (88 files unchanged initially)
  - Phase 2: Gradual migration of call-sites (acceptable tech debt for now)
  - Pros: Spike 1 achieves in 4–6h, Phase 1b stays on track
  - Cons: Dual system for 2–3 months, wrapper overhead

**Required Output:**
```yaml
architecture_choice: "big_bang"  # or "wrapper_phased"

if_big_bang:
  phase_1b_scope: "88 files refactored in parallel over 10 weeks"
  estimated_team_capacity: "X parallel tracks, Y files per track per week"
  risk_level: "HIGH (parallelization, regression testing)"
  
if_wrapper_phased:
  spike_1_scope: "Skill + wrapper, 88 files untouched"
  phase_1b_scope: "Gradual call-site migration, deprecation notices"
  phase_2_scope: "Final wrapper removal (Phase 2, Week 13+)"
  tech_debt_acceptance: "ACCEPTED (2–3 months)"
```

---

### **ANSWER 3: WORKER ENGINE MODE (SKILL OR LEGACY)**

**Question:** Is `worker_engine_mode()` (native/acs/tde selection) a Skill parameter or remain separate config?

**Context:**
- Currently: `worker_engine_mode(tenant_id)` → returns "native" | "acs" | "tde"
- Not a boolean flag (unlike 60+ other feature flags)
- Controls task delegation strategy system-wide
- Needs audit trail (which engine was selected per task)

**Options:**
- **Option 3a: Skill Parameter**
  - New Skill `os.worker_engine_selection` with methods `get_engine()`, `set_engine(mode)`, `describe_engines()`
  - Pros: Single audit trail, consistent Skills model
  - Cons: Slight latency overhead (Skill execution vs direct config read)
  
- **Option 3b: Legacy Config (Separate)**
  - Keep `worker_engine_mode()` in feature_flags.py or new config module
  - Skills system doesn't manage it
  - Pros: Zero overhead, backward-compatible
  - Cons: Dual audit paths (feature flags + worker engine), inconsistent model

**Required Output:**
```yaml
worker_engine_mode_handling: "skill_parameter"  # or "legacy_config"

if_skill_parameter:
  skill_id: "os.worker_engine_selection"
  public_api:
    - method: "get_engine(tenant_id) -> str"
    - method: "set_engine(mode: str, tenant_id: str) -> bool"
  audit_events:
    - "SKILL_EXECUTED (input: {}, output: {engine: native})"
    
if_legacy_config:
  location: "feature_flags.py or new module?"
  audit_strategy: "Audit in caller or in worker_engine_mode()?"
```

---

### **ANSWER 4: TIER MANAGEMENT (KEEP OR DROP)**

**Question:** Do ADR-0286/0288 tier management features (alpha/beta/stable/production promotion) continue or get dropped in Skills model?

**Context:**
- Current: `tier_of(flag_id)`, `can_promote_to(flag_id, target_tier)`, auto-promotion daemon (ADR-0288)
- Tracks maturity of each flag
- Enables gradual rollout (alpha → beta → stable → production)

**Options:**
- **Option 4a: Keep Tiers in Skills**
  - Each Skill has `release_tier` (alpha/beta/stable/production)
  - Promotion daemon moves tiers based on metrics (success rate, error rate, latency)
  - Pros: Operator observability, safety guardrail
  - Cons: Adds complexity to Skill manifest, promotion daemon must integrate
  
- **Option 4b: Drop Tiers (Feature Flags Only Legacy)**
  - Tiers removed; Skills launch at stable by default
  - Pros: Simplified architecture, no auto-promotion complexity
  - Cons: Loses operator safety control, operator can't see feature maturity

**Required Output:**
```yaml
tier_management: "keep_in_skills"  # or "drop_legacy_only"

if_keep_in_skills:
  skill_manifest_field: "release_tier: alpha | beta | stable | production"
  promotion_daemon_integration: "ADR-0288 daemon rewritten for Skills?"
  auto_promotion_criteria: "success_rate > X%, latency < Y ms"
  
if_drop_tiers:
  consequence: "No auto-promotion, operator loses maturity visibility"
  migration_path: "How to handle existing flags in alpha tier?"
```

---

## REQUIRED DELIVERABLE FORMAT

**Requested Amendment to ADR-0544:**

```markdown
## AMENDMENT 1 (Sept 2, 2026): Spike 1 Blocker Resolution

### DECISION 1: Flag-to-Skill Mapping
[Insert Answer 1 with mapping table + manifest structure]

### DECISION 2: Architecture Choice (Big Bang vs Wrapper)
[Insert Answer 2 with choice + phase allocations]

### DECISION 3: Worker Engine Mode Handling
[Insert Answer 3 with skill_id or legacy path]

### DECISION 4: Tier Management
[Insert Answer 4 with keep vs drop decision]

### IMPACT SUMMARY
- **Spike 1 Estimate (after decisions):** X–Y hours
- **Phase 1b Scope:** Z files × W hours = estimated total
- **Critical Assumptions:** [list]
- **Escalation Criteria:** [if this changes, escalate]
```

---

## ESCALATION CRITERIA

- **If answers unavailable by Sept 3 10:00 UTC:** Escalate to architecture lead + steering
- **If answers conflict with ADR-0549:** Escalate to ADR author (resolve dependency)
- **If answers require NEW ADR:** Create separate ADR (don't block this one)

---

## SUBMITTER
**Requestor:** Phase 1 Active Steering  
**Target:** ADR-0544 Author / Architecture Lead  
**Deadline:** Sept 3 10:00 UTC (HARD DEADLINE for Phase 1 Big Bang proceed decision)  
**Contact:** This file location for async updates

---

**STATUS:** Awaiting response. Spike 1 scoping blocked until answers received.
