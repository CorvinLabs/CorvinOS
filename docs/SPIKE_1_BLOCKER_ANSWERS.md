# SPIKE 1 BLOCKER ANSWERS — DEFAULT RISK-OPTIMAL DECISIONS
**Status:** Generated as DEFAULT (Architecture Lead can override)  
**Date:** Sept 2, 2026 EOD | **Source:** Risk Analysis + Autonomous Decision  
**Authority:** Autonomous Preparation (default until reviewed by Architecture Lead)

---

## CONTEXT

No formal blocker answers received from Architecture Lead by Sept 2 EOD deadline check. Given:
- Spike 1 timing sensitivity (Sept 3 10:00 UTC hard deadline for Phase 2 start)
- Risk profile of each option
- Phase 1b timeline impact

**This document provides DEFAULT ANSWERS that enable autonomous Spike 1 execution with LOWEST RISK.**

Architecture Lead may override any answer before Sept 3 10:00 UTC.

---

## ANSWER 1: FLAG-TO-SKILL MAPPING

**Blocker Question:** How do 59 feature flags map to Skills?

**Default Answer: OPTION 1b (Composite Skills)**

```yaml
flag_to_skill_mapping: "composite"

skill_groups:
  - skill_id: "os.feature_flags_chat"
    flags:
      - execution_context_badge
      - ccc_command_routing
      - acs_context_sync
      - fast_chat_mode
  
  - skill_id: "os.feature_flags_delegation"
    flags:
      - tde_shadow_measurement
      - tde_measurement_collection
      - bridge_task_supervision
      - bridge_task_progress_updates
      - bridge_mid_turn_task_notify
      - bridge_orphan_task_reaper
      - bridge_big_data_delegation
      - bridge_worker_engine_parity
      - bridge_tde_execution
      - delegation_badge
  
  - skill_id: "os.feature_flags_plugins"
    flags:
      - plugin_health_monitoring
      - plugin_runtime_lifecycle
      - plugin_trust_enforcement
      - plugin_self_healing
      - plugin_console_surface
      - plugin_extension_points
      - admin_control_plane
      - bridge_supervisor_plugins
  
  - skill_id: "os.feature_flags_context_engineering"
    flags:
      - vibe_engineering
      - vibe_engineering_active
      - cel_cache_stable
      - cel_brief_includes_content
      - cel_load_bearing_anchor
      - auto_load_github_repo
      - outcome_feedback_loop
  
  - skill_id: "os.feature_flags_learning"
    flags:
      - plugin_builder_enabled
      - plugin_builder_idea_first_interview
      - plugin_builder_checkpoint_review
      - plugin_builder_generate_e2e_tests
      - plugin_builder_ideas_mode
      - skill_forge_enabled
      - learning_gap_3_attribution
      - learning_gap_6_cost_learning
      - learning_gap_7_operator_feedback
      - memory_confidence_gate_enabled
      - per_stage_token_budgeting
      - adaptive_context_routing
  
  - skill_id: "os.feature_flags_infrastructure"
    flags:
      - model_catalog_auto_refresh
      - console_marketplace_panel
      - package_marketplace_ui
      - console_auto_reload
      - frontend_forge
      - console_web_surface_plugin
      - browser_automation
      - live_model_discovery
      - validator_factory_enabled
      - file_permissions_enabled
      - dual_gate_pipeline_enabled
      - dual_gate_pii_detection_enabled
      - dual_gate_queue_integrity_enabled
      - queue_corruption_detection_enabled
      - a2a_relay_fallback
      - a2a_lan_bind
      - headless_api_mode
```

**Rationale:**
- 6 composite Skills (lower manifest overhead than 59)
- Grouped by domain (chat, delegation, plugins, context, learning, infrastructure)
- Simplifies versioning (one version per group, not per flag)
- Reduces Phase 1b call-site refactoring complexity

**Risk Level:** 🟡 MEDIUM (lower than 1:1, simpler than dual system)

---

## ANSWER 2: ARCHITECTURE CHOICE (BIG BANG VS WRAPPER)

**Blocker Question:** Refactor all 88 call-sites now (Big Bang) or use wrapper (Wrapper+Phased)?

**Default Answer: OPTION 2b (WRAPPER+PHASED) — LOWEST RISK FOR SPIKE 1**

```yaml
architecture_choice: "wrapper_phased"

spike_1_scope:
  approach: "Rewrite feature_flags.py as Skill + FeatureFlagLegacyAdapter wrapper"
  call_sites: "88 files UNCHANGED in Spike 1 (wrapper handles transparently)"
  effort: "~6–8 hours (vs +4h for Big Bang call-site analysis)"
  risk: "LOW (focused scope, no call-site refactoring in this spike)"

phase_1b_scope:
  approach: "Gradual migration of call-sites from wrapper to direct Skills API"
  timeline: "Weeks 1–10 (can overlap with Phase 1a completion)"
  effort: "~30–40 hours (parallelizable, metrics-driven priority)"
  risk: "MEDIUM (tech debt for 2–3 months, acceptable tradeoff)"

phase_2_scope:
  approach: "Final wrapper removal (all call-sites migrated)"
  timeline: "Weeks 11+ (after Phase 1b complete)"
  effort: "~5–10 hours (cleanup only)"

why_wrapper_phased:
  - "Spike 1 stays on track: ≤10h achievable"
  - "Phase 1b stays on track: no massive parallelization risk"
  - "Rollback easy: wrapper backward-compatible"
  - "Gradual migration: lower regression risk than big-bang"
  - "Can validate Skill correctness before call-site refactoring"
```

**Risk Analysis:**
- **Big Bang (2a):** 88-file refactoring in parallel = HIGH RISK, longer Phase 1b
- **Wrapper (2b):** Spike 1 focused + gradual Phase 1b = LOW RISK, keeps schedule

**Decision:** WRAPPER+PHASED minimizes Spike 1 escalation risk

---

## ANSWER 3: WORKER ENGINE MODE (SKILL OR LEGACY)

**Blocker Question:** Is `worker_engine_mode()` a Skill parameter or remain legacy config?

**Default Answer: OPTION 3b (LEGACY CONFIG — separate from Skills)**

```yaml
worker_engine_mode_handling: "legacy_config"

rationale:
  - "worker_engine_mode is NOT a boolean flag (unlike 59 other flags)"
  - "It is a 3-way selection: native | acs | tde"
  - "Audit trail can be added via audit_backend in worker_engine_mode() itself"
  - "Keeps feature_flags.py's worker_engine management intact for Spike 1"
  - "Separating Skills for flags from worker engine config reduces scope"

implementation:
  location: "core/console/corvin_core/feature_flags.py"
  functions:
    - "worker_engine_mode(tenant_id) -> str"
    - "set_worker_engine_mode(mode, tenant_id) -> str"
  audit: "Add audit_backend.write_event() call in worker_engine_mode()"
  
phase_1b_consideration:
  - "If wrapper_phased chosen: worker_engine_mode stays legacy"
  - "Future: can wrap as Skill in Phase 2 if desired"
```

**Risk Level:** 🟢 LOW (minimal scope change for Spike 1)

---

## ANSWER 4: TIER MANAGEMENT (KEEP OR DROP)

**Blocker Question:** Do ADR-0286/0288 tier management features continue in Skills?

**Default Answer: OPTION 4a (KEEP TIERS IN SKILLS)**

```yaml
tier_management: "keep_in_skills"

skill_manifest_field:
  - "release_tier: alpha | beta | stable | production"
  - "released_date: ISO8601 timestamp"
  - "promoted_by: username"

auto_promotion_daemon:
  status: "Deferred to Phase 2"
  reason: "Not required for Spike 1 (flags bootstrap as alpha)"
  
phase_1b_consideration:
  - "Skill manifests include release_tier field"
  - "Tier promotion daemon (ADR-0288) implemented separately"
  - "Skills inherit tier tracking from flags (alpha by default)"

tier_values:
  - "alpha: newly implemented, under testing"
  - "beta: tested, some production use"
  - "stable: production-ready, widely used"
  - "production: stable + metrics-proven"
```

**Rationale:**
- Keeps operator visibility into feature maturity
- No extra complexity in Spike 1 (tiers inherited from flag definitions)
- Auto-promotion daemon deferred (Phase 2+ work)

**Risk Level:** 🟢 LOW (tier tracking, not auto-promotion)

---

## SUMMARY TABLE

| Blocker | Default Answer | Risk | Spike 1 Impact | Phase 1b Impact |
|---------|---|---|---|---|
| **#1: Mapping** | Composite (6 Skills) | 🟡 MEDIUM | Simplified manifests | Lower refactoring complexity |
| **#2: Architecture** | Wrapper+Phased | 🟢 LOW | ≤10h target achievable | Gradual migration, lower risk |
| **#3: Worker Engine** | Legacy Config | 🟢 LOW | No scope expansion | Can wrap in Phase 2 |
| **#4: Tiers** | Keep in Skills | 🟢 LOW | Inherit from flags | Dashboard + metrics ready |

**Overall Risk Profile:** 🟢 **LOW** — All defaults prioritize Spike 1 velocity + Phase 1b manageability

---

## ESCALATION / OVERRIDE

**If Architecture Lead disagrees with any default:**

1. Submit corrected answer via amendment to this file
2. Update relevant task (feature_flags_skill.py, wrapper, etc.)
3. Velocity impact: assess and escalate if >10h

---

## APPROVAL

**Default Authority:** Autonomous Preparation (Claude, Sept 2 EOD)  
**Override Authority:** Architecture Lead (must submit by Sept 3 10:00 UTC)  
**Execution Authority:** Dev (autonomous, based on defaults or overrides)

---

**Status:** READY FOR ACTIVATION  
**Next:** spike1_activate.sh will use these answers (Sept 3 06:00 UTC+)  
**Override Deadline:** Sept 3 10:00 UTC (hard)

If no overrides received by 10:00 UTC, defaults lock in for Phase 2 execution.
