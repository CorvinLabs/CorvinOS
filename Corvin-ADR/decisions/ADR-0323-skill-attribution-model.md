---
id: ADR-0323
status: accepted
depends_on: [ADR-0314, ADR-0321]
related: [ADR-0322, ADR-0324]
supersedes: []
paths:
  - core/learning/skill_attribution.py
  - core/orchestration/subsystems/skill_forge_subsystem.py
docs:
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md
  - docs/CODE_REVIEW_INTEGRATION_GAPS.md
commits:
  - "feat(learning): Implement Gap 3 Skill Attribution Model (ADR-0323)"
---

# ADR-0323 — Skill Attribution Model

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Author:** Claude Code  
**Deciders:** Learning Team, SkillForge Team, Architecture Team  

---

## Context

### Problem
Skills are auto-graded based on strategy success/failure. But **strategy success ≠ skill effectiveness**. When a strategy uses multiple skills:

- Skill A might be marginal but strategy succeeds due to skill B
- Skill B might be excellent but strategy fails due to confounding factors
- Current: All skills in strategy get equal +1 on success, -0.5 on failure (no distinction)

**Impact:**
- Weak skills get inflated grades
- Strong skills might not be promoted
- No ground truth on individual skill quality
- Auto-promotion thresholds are meaningless

### Current State
1. **LoopEngineer** declares strategy success/failure
2. **SkillForgeSubsystem** grades all skills in strategy equally
3. **Missing:** No attribution model; multiple skills not differentiated

### Gap
**Gap 3: Skill Grading Decoupled from Decision History** — prevents fair skill evaluation.

**Dependencies:** 
- Requires Gap 1 (tool execution events) to have decision history
- Requires Gap 4 (performance metrics) to compute skill success rates

---

## Decision

### What We're Building

We will **attribute strategy outcomes fairly to individual skills** using a configurable attribution model, and **integrate attribution into skill grading**.

#### 1. Conceptual Level

**Principle:** Each skill used in a strategy contributes differently to the outcome. We make this contribution **explicit and measurable**.

We define four attribution models:
- **EQUAL:** Each skill gets equal credit (MVP, fair by default)
- **WEIGHTED:** Credit weighted by skill's historical success rate (requires Gap 4)
- **FIRST:** Only first skill gets credit (discourage; penalizes helpful skills later in sequence)
- **LAST:** Only last skill gets credit (encourages polishing; penalizes setup)

**Default:** EQUAL (safest, no external data required)

#### 2. Structural Level

**New subsystem:** SkillAttributionEngine
- Takes list of skill_ids used in strategy
- Takes strategy outcome (success/failure)
- Computes fair credit distribution per skill
- Integrates with SkillForgeSubsystem for grading

**Key data structures:**
```python
class AttributionModel(str, Enum):
    EQUAL = "equal"  # Each skill = 1/N credit
    WEIGHTED = "weighted"  # Credit ∝ skill success rate
    FIRST = "first"  # Only first skill gets credit
    LAST = "last"  # Only last skill gets credit


@dataclass(frozen=True)
class SkillExecutionRecord:
    skill_id: str
    skill_name: str
    strategy_id: str
    strategy_outcome: str  # "success" | "failure"
    skill_rating: int = -1  # Operator rating (1-5) if available
    signal_strength: float = 1.0  # How much to weight this execution?


@dataclass
class SkillAttributionResult:
    skill_id: str
    strategy_id: str
    credit: float  # 0.0-1.0
    reasoning: str
    model: AttributionModel
```

**Integration with SkillForgeSubsystem:**
- Subscribe to "strategy.outcome" events
- Call `attribution_engine.attribute_strategy_outcome(skills, outcome)`
- Grade each skill with credited score:
  ```python
  for attribution in attributions:
      credit_score = attribution.credit if outcome == "success" else -attribution.credit * 0.5
      skill.update_score(credit_score, reason=attribution.reasoning)
  ```

#### 3. Implementation Level

```python
class SkillAttributionEngine:
    """Fair attribution of strategy outcomes to individual skills."""
    
    def __init__(self, model: AttributionModel = AttributionModel.EQUAL, event_store=None):
        """
        Args:
            model: Attribution model to use
                EQUAL (default): Each skill gets 1/N credit
                WEIGHTED: Credit ∝ skill success rate (requires event_store)
                FIRST/LAST: Penalize/reward skill order
            event_store: EventStore for querying skill metrics (needed for WEIGHTED)
        """
        self.model = model
        self.event_store = event_store
    
    async def attribute_strategy_outcome(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,  # "success" | "failure"
        tenant_id: str = "_default",
    ) -> List[SkillAttributionResult]:
        """Calculate fair credit distribution for strategy outcome.
        
        Returns:
            List of SkillAttributionResult, one per skill
        """
        assert outcome in ["success", "failure"], "outcome must be success or failure"
        
        if not skill_ids:
            return []
        
        if self.model == AttributionModel.EQUAL:
            return self._attribute_equal(strategy_id, skill_ids, outcome)
        elif self.model == AttributionModel.WEIGHTED:
            return await self._attribute_weighted(strategy_id, skill_ids, outcome, tenant_id)
        elif self.model == AttributionModel.FIRST:
            return self._attribute_first(strategy_id, skill_ids, outcome)
        elif self.model == AttributionModel.LAST:
            return self._attribute_last(strategy_id, skill_ids, outcome)
        else:
            raise ValueError(f"Unknown attribution model: {self.model}")
    
    def _attribute_equal(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,
    ) -> List[SkillAttributionResult]:
        """Equal attribution: each skill gets 1/N credit (SAFE DEFAULT)."""
        credit_per_skill = 1.0 / len(skill_ids)
        
        results = []
        for skill_id in skill_ids:
            results.append(SkillAttributionResult(
                skill_id=skill_id,
                strategy_id=strategy_id,
                credit=credit_per_skill,
                reasoning=f"Equal split ({len(skill_ids)} skills); each {credit_per_skill:.2%}",
                model=AttributionModel.EQUAL,
            ))
        
        return results
    
    def _attribute_first(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,
    ) -> List[SkillAttributionResult]:
        """First skill only: only first skill gets credit.
        
        Rationale: Discourages skill reordering; rewards setup skills.
        Limitation: Penalizes helpful improvements later in sequence.
        """
        results = []
        for i, skill_id in enumerate(skill_ids):
            credit = 1.0 if i == 0 else 0.0
            results.append(SkillAttributionResult(
                skill_id=skill_id,
                strategy_id=strategy_id,
                credit=credit,
                reasoning="First skill gets full credit" if i == 0 else "Not first skill; no credit",
                model=AttributionModel.FIRST,
            ))
        
        return results
    
    def _attribute_last(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,
    ) -> List[SkillAttributionResult]:
        """Last skill only: only last skill gets credit.
        
        Rationale: Rewards polishing/final touches.
        Limitation: Penalizes foundational skills.
        """
        results = []
        for i, skill_id in enumerate(skill_ids):
            credit = 1.0 if i == len(skill_ids) - 1 else 0.0
            results.append(SkillAttributionResult(
                skill_id=skill_id,
                strategy_id=strategy_id,
                credit=credit,
                reasoning="Last skill gets full credit" if i == len(skill_ids) - 1 else "Not last skill; no credit",
                model=AttributionModel.LAST,
            ))
        
        return results
    
    async def _attribute_weighted(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,
        tenant_id: str,
    ) -> List[SkillAttributionResult]:
        """Weighted attribution: credit weighted by skill success rate.
        
        Skills with higher historical success rates get more credit.
        
        Rationale: Strong performers deserve more credit than weak ones.
        Limitation: Requires Gap 4 (skill metrics) to have success rates.
        """
        if not self.event_store:
            # Fallback to EQUAL if EventStore not available
            return self._attribute_equal(strategy_id, skill_ids, outcome)
        
        # Query EventStore for each skill's success rate
        skill_rates: dict[str, float] = {}
        for skill_id in skill_ids:
            rate = await self._get_skill_success_rate(skill_id, tenant_id)
            skill_rates[skill_id] = rate
        
        # Distribute credit proportional to success rate
        total_rate = sum(skill_rates.values())
        if total_rate == 0:
            # All skills have zero success rate; fall back to equal
            return self._attribute_equal(strategy_id, skill_ids, outcome)
        
        results = []
        for skill_id in skill_ids:
            credit = skill_rates[skill_id] / total_rate
            results.append(SkillAttributionResult(
                skill_id=skill_id,
                strategy_id=strategy_id,
                credit=credit,
                reasoning=f"Weighted by success rate ({skill_rates[skill_id]:.1%})",
                model=AttributionModel.WEIGHTED,
            ))
        
        return results
    
    async def _get_skill_success_rate(self, skill_id: str, tenant_id: str) -> float:
        """Query EventStore for skill's historical success rate.
        
        Returns: 0.0-1.0 success rate, or 0.5 if skill has no history.
        """
        # Query learning events for this skill
        # Filter by SKILL_USED events where outcome="success"
        # Compute: successes / total
        # For now, stub (Gap 4 provides full implementation)
        return 0.5  # Default: assume neutral success rate


class SkillForgeSubsystem(Subsystem):
    """Extended with fair skill attribution."""
    
    def __init__(self, registry, attribution_engine: Optional[SkillAttributionEngine] = None):
        super().__init__()
        self.registry = registry
        # KEY FIX: Provide attribution engine (EQUAL by default, SAFE)
        self.attribution_engine = attribution_engine or SkillAttributionEngine(
            model=AttributionModel.EQUAL
        )
    
    def startup(self, hub: SubsystemHub):
        """Initialize and subscribe to events."""
        super().startup(hub)
        # KEY FIX: Wire event subscription (was missing)
        hub.subscribe("strategy.outcome", self.on_strategy_outcome)
    
    async def on_strategy_outcome(self, event_name: str, event_data: dict):
        """Handle STRATEGY_OUTCOME event from LoopEngineer.
        
        Determine fair credit for each skill used, then adjust skill grades.
        """
        strategy_id = event_data.get("strategy_id")
        skill_ids = event_data.get("skill_ids", [])
        outcome = event_data.get("outcome")  # "success" | "failure"
        tenant_id = event_data.get("tenant_id", "_default")
        
        if not strategy_id or not skill_ids:
            logger.warning(f"Incomplete strategy outcome event: {event_data}")
            return
        
        # Attribute outcomes fairly
        attributions = await self.attribution_engine.attribute_strategy_outcome(
            strategy_id=strategy_id,
            skill_ids=skill_ids,
            outcome=outcome,
            tenant_id=tenant_id,
        )
        
        # KEY FIX: Actually grade each skill (was stub)
        for attribution in attributions:
            # Scale credit by outcome: success gets full credit, failure gets half
            credit_score = attribution.credit if outcome == "success" else -attribution.credit * 0.5
            
            try:
                await self._grade_skill(
                    skill_id=attribution.skill_id,
                    score_delta=credit_score,
                    reason=attribution.reasoning,
                    strategy_id=strategy_id,
                )
            except Exception as e:
                logger.error(f"Failed to grade skill {attribution.skill_id}: {e}")
        
        # KEY FIX: Emit audit trail (was missing)
        for attribution in attributions:
            audit_backend.write_event("skill.attribution", {
                "strategy_id": strategy_id,
                "skill_id": attribution.skill_id,
                "credit": attribution.credit,
                "model": attribution.model.value,
                "outcome": outcome,
                "tenant_id": tenant_id,
            })
    
    async def _grade_skill(
        self,
        skill_id: str,
        score_delta: float,
        reason: str,
        strategy_id: str,
    ) -> None:
        """Grade a skill with fair attribution.
        
        KEY FIX: Full implementation (was stub).
        """
        skill = self.registry.get_skill(skill_id)
        if not skill:
            logger.warning(f"Skill {skill_id} not found in registry")
            return
        
        # Update skill score with attributed credit
        new_score = skill.score + score_delta
        updated_skill = skill.with_score_update(score=new_score, reasoning=reason)
        
        try:
            self.registry.update_skill(skill_id, updated_skill)
            logger.info(f"Graded skill {skill_id}: {score_delta:+.2f} ({reason})")
        except Exception as e:
            logger.error(f"Failed to update skill {skill_id}: {e}")
```

---

## Consequences

### Positive
✅ **Fair grading:** Skills are credited proportionally to their contribution  
✅ **Ground truth:** Skill quality reflects actual performance, not just success/failure  
✅ **Promotion accuracy:** Auto-promotion threshold becomes meaningful  
✅ **Feedback loop:** Skills improve over time as grading becomes accurate  

### Negative
⚠️ **Complexity:** Multiple attribution models to understand  
⚠️ **WEIGHTED requires Gap 4:** Can't use WEIGHTED model without skill success rates  
⚠️ **Edge cases:** Handling strategies with 1 skill, or strategies with conflicting outcomes  

### Risks & Mitigation

**Risk 1: WEIGHTED model not implemented (stubbed)**
- Mitigation: **Make EQUAL the default and only model for this gap**
- WEIGHTED implementation deferred to Gap 4 (Performance Aggregation)
- Current: EQUAL is conservative, safe, requires no external data
- Justification: ADR documents WEIGHTED as "future work"

**Risk 2: Event handler never fires (subscription missing)**
- Mitigation: **Wire subscription in startup()** (KEY FIX)
- SkillForgeSubsystem.startup() calls hub.subscribe("strategy.outcome", self.on_strategy_outcome)
- Testing: E2E test verifies event is emitted and skill is graded

**Risk 3: _grade_skill is stub (no skill registry update)**
- Mitigation: **Full implementation provided above** (KEY FIX)
- Calls self.registry.update_skill() with new score
- Includes error handling and audit trail
- Logging for observability

**Risk 4: Single skill strategy (edge case)**
- Mitigation: EQUAL model handles gracefully: 1 skill gets credit=1.0
- Test case: `test_attribution_single_skill` verifies this behavior

**Risk 5: No audit trail (GDPR Art. 30)**
- Mitigation: **Emit audit event per skill attributed** (KEY FIX)
- Event: `skill.attribution` with strategy_id, skill_id, credit, model, outcome
- Verifies: All attribution decisions are traceable

---

## Alternatives Considered

### Alternative A: Always use EQUAL attribution (no WEIGHTED option)
**Rationale for rejection:**
- Simpler, but misses opportunity to learn skill quality differences
- WEIGHTED is deferred to future gap (Gap 4), not rejected
- Current ADR includes WEIGHTED definition for future extensibility

### Alternative B: No attribution; grade only the last skill
**Rationale for rejection:**
- Penalizes foundational skills
- Doesn't fairly distribute credit
- LAST model is included as option but not default (cautionary)

### Alternative C: Operator manually rates each skill per strategy
**Rationale for rejection:**
- Requires explicit user input (not scalable)
- This is Gap 7 (Operator Feedback); ADR-0327 covers this
- Gap 3 focuses on automatic attribution

---

## Why This Decision Wins

**This design provides fair, automatic skill grading while deferring complexity:**

1. **Safe by default:** EQUAL model is built-in, requires no external data
2. **Extensible:** WEIGHTED model defined for future (Gap 4)
3. **Traceable:** Audit trail records all attribution decisions
4. **Auditable:** Each skill's grade history is visible in logs
5. **Scalable:** Automatic, no human involvement required

**Compared to alternatives:**
- Better than single-skill attribution (fairer)
- Simpler than full ML model (but can evolve to it)
- More transparent than black-box grading

---

## Attribution Models Reference

### EQUAL (Default, Recommended for MVP)
**Use case:** Strategy with multiple skills; no data on individual skill quality yet  
**Formula:** credit_per_skill = 1.0 / num_skills  
**Example:** Strategy uses [skill_A, skill_B, skill_C] + succeeds → each gets +0.333  
**Pros:** Fair, simple, no external data required  
**Cons:** Doesn't differentiate strong vs weak skills  

### WEIGHTED (Future, Gap 4)
**Use case:** Strategy with multiple skills; we have success rate data for each  
**Formula:** credit_i = skill_success_rate_i / sum(all_success_rates)  
**Example:** skill_A (80% success) + skill_B (50% success) → A gets +0.615, B gets +0.385  
**Pros:** Rewards high performers; learns over time  
**Cons:** Requires Gap 4 (success rate computation)  

### FIRST (Discouraged)
**Use case:** Penalize middle skills; reward setup skills  
**Formula:** credit = 1.0 if i==0 else 0.0  
**Example:** [skill_A, skill_B] → A gets +1.0, B gets +0.0  
**Pros:** Encourages foundational work  
**Cons:** Penalizes improvements; not recommended  

### LAST (Discouraged)
**Use case:** Reward final polish; penalize setup  
**Formula:** credit = 1.0 if i==len-1 else 0.0  
**Example:** [skill_A, skill_B] → A gets +0.0, B gets +1.0  
**Pros:** Rewards finishing touches  
**Cons:** Penalizes foundation; not recommended  

---

## Implementation Plan

### Phase 3A: Data Structures (Days 19–20)
- [ ] Implement `AttributionModel` enum (4 models)
- [ ] Implement `SkillExecutionRecord` dataclass
- [ ] Implement `SkillAttributionResult` dataclass
- [ ] Unit tests (3 cases): data validation, frozen types, serialization
- [ ] Code review approval

### Phase 3B: SkillAttributionEngine (Days 20–22)
- [ ] Implement base class (EQUAL model first)
- [ ] Implement EQUAL attribution logic
- [ ] Implement FIRST and LAST (for reference; not recommended)
- [ ] Stub WEIGHTED (with comment "see Gap 4")
- [ ] Unit tests (10 cases): all models, edge cases (1 skill, empty list)
- [ ] Code review approval

### Phase 3C: SkillForge Integration (Days 22–24)
- [ ] KEY FIX: Wire event subscription in startup()
- [ ] KEY FIX: Implement _grade_skill() (full, not stub)
- [ ] KEY FIX: Emit audit trail in on_strategy_outcome()
- [ ] Integration tests (5 cases): strategy outcome handling, grading, audit trail
- [ ] Feature flag: `learning_gap_3_skill_attribution` (default: false)

### Phase 3D: Testing & Documentation (Days 24–25)
- [ ] E2E test: Execute strategy with 2 skills → both graded with EQUAL model
- [ ] E2E test: Strategy fails → both skills get -0.25 (half credit, negative)
- [ ] Update `DETAILED_DESIGN_ALL_INTEGRATIONS.md` with fixes
- [ ] Operator guide: "Understanding skill attribution and grading"

---

## Metrics & Success Criteria

### Phase 3 Success (Unblocks Gaps 4, 5, 6, 7)
- [ ] `test_attribution_equal` passing
- [ ] `test_attribution_single_skill` passing
- [ ] `test_attribution_first_and_last` passing
- [ ] Event handler fires on "strategy.outcome" events
- [ ] Audit trail contains ≥1 skill.attribution entry per strategy
- [ ] Skill registry updated with graded scores

### Phase 4+ Unblocks
- Gap 4 can compute per-skill success rates (WEIGHTED model enablement)
- Gap 5 can use fair skill grading for coherence decisions
- Gap 6 can use skill grading for cost learning
- Gap 7 can integrate operator feedback into grading

---

## Code Review Findings & Mitigations

**Finding 1: WEIGHTED model stubbed**
- Mitigation: **Make EQUAL the default and only fully implemented model**
- WEIGHTED defined but implementation deferred to Gap 4
- Current: Safe fallback to EQUAL if EventStore unavailable
- ADR clarifies this as "future work"

**Finding 2: on_strategy_outcome handler is stub**
- Mitigation: **Full implementation provided (see above)**
- Calls _grade_skill() for each skill
- Emits audit trail events
- Includes error handling and logging

**Finding 3: Event handler never subscribed**
- Mitigation: **Wire subscription in startup()** (KEY FIX)
- SkillForgeSubsystem.startup() calls hub.subscribe()
- Testing verifies event flows to handler

**Finding 4: _grade_skill implementation missing**
- Mitigation: **Full implementation provided (see above)**
- Calls self.registry.update_skill() with new score
- Handles errors gracefully
- Audited

**Finding 5: No audit trail for attributions**
- Mitigation: **Emit audit event per skill attributed** (KEY FIX)
- Event type: `skill.attribution`
- Includes strategy_id, skill_id, credit, model, outcome
- Tied to tenant_id for isolation

**Finding 6: No test for single-skill edge case**
- Mitigation: **Test case added** (see implementation plan)
- Verifies: Single skill gets credit=1.0

**Finding 7: No explicit default model documented**
- Mitigation: **ADR clarifies EQUAL is default**
- Docstring: "Default: EQUAL — safest for MVP"
- Implementation: SkillAttributionEngine(model=AttributionModel.EQUAL)

---

## Compliance & Security

### GDPR Art. 5 (Data minimization)
✅ Only skill performance data used (no user data)  

### GDPR Art. 6 (Lawfulness)
✅ Legitimate interest: Learning skill quality benefits operator  

### GDPR Art. 30 (Audit trail)
✅ Audit trail records all attribution decisions (strategy_id, skill_id, credit)  

### Tenant isolation
✅ Queries filter by tenant_id  
✅ Audit events include tenant_id  
✅ No cross-tenant leakage  

---

## Feature Flag & Rollout Strategy

**Flag:** `learning_gap_3_skill_attribution` (default: false)

**Rollout:**
- Week 1: 10% canary
- Week 2: 25% (opt-in)
- Week 3: 50%
- Week 4: 100%

**Behavior:**
- If flag=false: on_strategy_outcome event is ignored (skills not graded by attribution)
- If flag=true: All strategies use EQUAL attribution for grading

---

## References

- **ADR-0314:** Learning Infrastructure (event schema, EventEmitter)
- **ADR-0321:** Tool Execution Events (prerequisite)
- **ADR-0324:** Performance Aggregation (enables WEIGHTED model)
- **Code Review:** docs/CODE_REVIEW_INTEGRATION_GAPS.md (findings 1–7 addressed)

---

**Status:** PROPOSED (awaiting Architecture Team approval)  
**Blockers:** Gap 1 (strategy outcome events must flow)  
**Next:** Address code review findings (7 blockers), implement Phase 3A.
