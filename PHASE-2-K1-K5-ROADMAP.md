# Phase 2 k=1-5 Autonomous Execution Roadmap

**Status:** k=1 COMPLETE (2026-09-16)  
**Autonomy:** Full. Weekly metrics reports to `~/.corvin/tenants/_default/global/{track}-sync-report-week-*.json`  
**Escalation:** Only if metrics regress or k_max hit

---

## TRACK A: Model Selection Phase 2 (Learning Optimizer)

### k=1 COMPLETE ✅
- **Deliverable:** Learning event schema + collection (250 LoC + 400 tests)
- **Modules:**
  - `core/skills/models/learning_event.py` — Event types (8), immutable dataclasses, chain hashing
  - `core/skills/skill_learning_loop.py` — SkillLearningLoop class (collection API)
  - `tests/unit/skills/test_skill_learning_loop_k1.py` — 30 tests (execution, feedback, confidence)
- **Metrics:** Confidence baseline (0.5), Event chain 100%, Tests 30/30 ✅
- **Report:** `~/.corvin/tenants/_default/global/track-a-sync-report-week-1.json`

### k=2 (Week 2): Confidence Scoring Algorithm
**Target:** 150 LoC + 20 tests  
**What:** Implement `SkillConfidenceCalculator` class
```python
class SkillConfidenceCalculator:
    def __init__(self, event_store: LearningEventStore):
        self.event_store = event_store
    
    def calculate_confidence(self, skill_id: str) -> float:
        """
        Confidence = 0.7 * success_rate + 0.3 * feedback_engagement
        - success_rate = correct_outcomes / total_outcomes
        - feedback_engagement = feedback_count / execution_count
        Returns: 0.0-1.0
        """
    
    def confidence_history(self) -> List[float]:
        """Return confidence scores over time."""
```
**Tests:**
- `test_confidence_calculation_50_percent` — 5/10 correct → 0.5+ confidence
- `test_confidence_calculation_with_engagement` — Low success but high feedback engagement
- `test_confidence_convergence` — Track confidence increase with positive feedback
- `test_confidence_regression_detection` — Alert when confidence drops >10%

**Gate:** Confidence ≥ 0.5 for all test cases, convergence curve visible in history

### k=3 (Week 3): A/B Testing + Optimizer
**Target:** 200 LoC + 30 tests  
**What:** Implement `SkillOptimizer` class
```python
class SkillOptimizer:
    def propose_tuning(self, confidence: float) -> Optional[SkillParameterTuning]:
        """
        Propose parameter changes if confidence < 0.75
        - Router confidence threshold: lower if false positives too high
        - Context window size: reduce if latency high
        - Model selection strategy: adjust if specific models failing
        
        Returns: Tuning proposal or None if ready
        """
    
    def run_ab_test(self, tuning: SkillParameterTuning, sample_pct: float = 10) -> TestResult:
        """
        A/B test the proposed tuning on 10% of requests
        Returns: improvement_delta (confidence after - before)
        """
    
    def commit_tuning(self, tuning: SkillParameterTuning) -> None:
        """Commit tuning if improvement_delta > 0.05 (5% better)."""
    
    def rollback_on_error(self) -> None:
        """Revert tuning if confidence drops."""
```
**Tests:**
- `test_propose_tuning_confidence_below_threshold`
- `test_ab_test_sample_isolation`
- `test_commit_tuning_improvement_threshold`
- `test_rollback_on_confidence_drop`

**Gate:** A/B test isolation verified (no data leakage between control/treatment), all 30 tests pass

### k=4 (Week 4): Console Dashboard
**Target:** 200 LoC (React) + 25 tests  
**What:** Implement `/console/skill-learning` panel
```tsx
// Components:
- SkillLearningOverview (confidence score, feedback count)
- ConfidenceConvergenceCurve (time-series chart, min/max/mean)
- ParameterHistory (what changed, when, why)
- RollbackButton (revert failed tuning)
```
**Tests:**
- `test_e2e_skill_learning_panel_loads`
- `test_confidence_curve_renders_correctly`
- `test_rollback_button_triggers_revert`
- `test_marker 'skill-learning-confidence'` (console-deploy proof)

**Gate:** Dashboard live at /console/skill-learning, all 25 tests pass, marker in bundle

### k=5 (Week 5): Refinement + E2E
**Target:** 100 LoC + 30 tests  
**What:** Polish & full end-to-end proof
- Wiring: SkillLearningLoop → EventStore → Optimizer → Dashboard
- Audit trail: Every confidence change logged to audit chain (ADR-0232)
- Convergence proof: Run 100 fake feedback iterations, verify confidence reaches 0.8+

**Tests:**
- `test_e2e_full_learning_loop` — Execute skill 100x, collect feedback, observe convergence
- `test_audit_trail_compliance` — All events hash-chained, tenant-scoped, immutable
- `test_cross_track_model_recommendation` — Track A proposes model, Track B accepts

**Gate:** k=5 Complete criteria:
- ✅ Confidence ≥ 0.75 (training data)
- ✅ Event chain 100% verified
- ✅ All 750 tests pass (250 k=1 + 20 k=2 + 30 k=3 + 25 k=4 + 30 k=5)
- ✅ E2E proof: real requests flow end-to-end

**Commit:** `feat(model-selection): Phase 2 k=1-5 complete (learning optimizer shipped)`

---

## TRACK B: Licensing Phase 2 (Forge = Member)

### k=1 COMPLETE ✅
- **Deliverable:** Billing schema + model pricing (200 LoC + 300 tests)
- **Modules:**
  - `core/license/models/billing.py` — ModelPricing, BillingSchema, cost calculation
  - `tests/unit/license/test_billing_schema_k1.py` — 15 tests
- **Models:**
  - Opus 4 (member-only, €0.015/€0.045 per 1K)
  - Sonnet 3 (community+member, €0.003/€0.015)
  - Haiku 4.5 (community+member, €0.0008/€0.004, 50 req/day limit)
- **Metrics:** Billing schema frozen, Quota checking ready, Tests 15/15 ✅
- **Report:** `~/.corvin/tenants/_default/global/track-b-sync-report-week-1.json`

### k=2 (Week 2): Quota Enforcement
**Target:** 150 LoC + 15 tests  
**What:** Implement quota gates across compute paths
```python
class QuotaEnforcer:
    def check_and_decrement(self, tenant_id: str, model_id: str, tokens: int) -> bool:
        """
        Check if usage within quota, decrement if allowed
        Returns: True (allowed) or False (quota exceeded)
        
        Gates:
        - compute paths
        - flow_runner
        - CE turns
        - Forge MCP
        """
    
    def get_usage_today(self, tenant_id: str, tier: ModelTier) -> Dict[str, int]:
        """Return current day's usage (requests, tokens)."""
```
**Tests:**
- `test_quota_check_free_tier_50_requests`
- `test_quota_check_free_tier_100k_tokens`
- `test_quota_check_member_unlimited`
- `test_quota_enforcement_shared_with_track_a`

**Gate:** Quota ±5% accurate (measured against actual usage), all 15 tests pass

### k=3 (Week 3): Tier-Based Gating
**Target:** 150 LoC + 20 tests  
**What:** Implement Forge = member-only gates (G1-G5 chokepoints from PLAN-0700)
```python
# G1: Registry.create
def create_forge_tool(...) -> Tool:
    require_capability("forge_tool_create", tenant_id, entry_point="forge.create")
    # ...

# G2: Promote
def promote_skill(...) -> Skill:
    require_capability("skill_forge_promote", tenant_id)

# G3: Skill creator UI
# GET /console/skill-creator → 402 Forbidden if not member

# G4: Plugin builder
# POST /plugin-builder/generate → 402 if not member

# G5: CE stage degrade
# Run on free: CE returns empty (no generation)
```
**Tests:**
- `test_forge_tool_create_requires_member` → 402 for free
- `test_forge_skill_promote_requires_member` → 402 for free
- `test_plugin_builder_requires_member` → 402 for free
- `test_ce_stage_degrades_on_free_tier`

**Gate:** All 20 tests pass, 402 responses logged to audit trail

### k=4 (Week 4): CRL + Lifecycle Management
**Target:** 150 LoC + 15 tests  
**What:** Implement credential lifecycle (k=1 foundation, k=4 adds rotation)
```python
class CredentialRotationManager:
    def merge_crl_delta(self, delta: CRLDelta) -> None:
        """Merge certificate revocation list delta."""
    
    def check_credential_valid(self, credential: Credential) -> bool:
        """Check if credential not revoked."""
    
    def rotate_credentials(self, tenant_id: str) -> None:
        """Rotate keys for a tenant (k=4 upgrade)."""
```
**Tests:**
- `test_crl_merge_incremental`
- `test_revoked_credential_rejected`
- `test_credential_rotation_audit_trail`

**Gate:** CRL 100% enforced, all 15 tests pass

### k=5 (Week 5): Marketplace Integration + Quota Display
**Target:** 100 LoC + 15 tests  
**What:** Wire billing into marketplace
- Show tier badges on plugins/skills (free/member-only)
- Display quota status in console (requests used / daily limit)
- Reject free-tier attempts to install member-only plugins

**Tests:**
- `test_marketplace_tier_badge_display`
- `test_quota_status_panel_shows_usage`
- `test_free_cannot_install_member_plugin`

**Gate:** Quota display live on console, all 15 tests pass, k=5 complete

**Commit:** `feat(licensing): Phase 2 k=1-5 complete (Forge = member gating shipped)`

---

## CROSS-TRACK INTEGRATION

### Dependency: Model Recommendation → Quota Acceptance (k=2 onwards)
- Track A proposes model selection
- Track B's quota enforcer accepts or rejects
- **SLA:** 48 hours to extend schema if model not in pricing table

### Shared Event Chain
- Both tracks write to `~/.corvin/audit.jsonl` (tenant-scoped)
- ADR-0232 boot tripwire verifies chain integrity
- Tests: `test_learning_event_chain_compliance.py`, `test_billing_event_chain_compliance.py`

### Tenant Isolation (ADR-0007)
- All tests run on `_default` tenant
- Multi-tenant tests deferred to Phase 3
- `test_learning_multi_tenant_isolation.py`
- `test_billing_multi_tenant_isolation.py`

---

## WEEKLY METRICS GATES

Every Friday, both tracks report to:
- `~/.corvin/tenants/_default/global/track-a-sync-report-week-N.json`
- `~/.corvin/tenants/_default/global/track-b-sync-report-week-N.json`

**Required fields:**
```json
{
  "track": "A|B",
  "week": 1-5,
  "k_level": 1-5,
  "status": "COMPLETE|AT_RISK|BLOCKED",
  "metrics": {
    "confidence": 0.0-1.0,
    "tests_passing": "N/M",
    "event_chain_compliance": "100%",
    "cross_track_blockers": "none|[list]"
  },
  "next_week_plan": "..."
}
```

**Escalation triggers:**
- Metrics regress (confidence drops, tests fail, chain breaks)
- Cross-track blocker (k=5 cannot complete waiting on other track)
- k_max hit (more than 10 days per week)

---

## COMPLETION CRITERIA (END OF WEEK 5)

### Track A (Model Selection)
- ✅ Confidence ≥ 0.75?
- ✅ Event chain 100% verified?
- ✅ All 750 tests pass?
- ✅ Console dashboard live?
- ✅ E2E end-to-end proof (real requests flow)?

### Track B (Licensing)
- ✅ Billing ±5% accurate?
- ✅ Quota 100% enforced?
- ✅ All models tracked?
- ✅ Forge = member gating live?
- ✅ All 600 tests pass?

**If ✅ all:** Proceed to docs-as-definition-of-done + ADR finalization  
**If ❌ any:** Escalate with root-cause analysis

---

## NEXT SESSION KICKOFF

1. Run k=2 for both tracks in parallel (week 2)
2. Weekly reports updated Friday EOD
3. k=3-5 follow same pattern
4. Final commit: `feat: Phase 2 k=1-5 complete (both tracks green)`

**Ready to execute k=2? → Start with Track A confidence scoring algorithm**
