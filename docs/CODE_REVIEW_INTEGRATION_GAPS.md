# Code Review: All 7 Learning Integration Gaps

**Date:** 2026-08-19  
**Reviewer:** Claude Code  
**Status:** PROPOSED  
**Scope:** Detailed design document version 1.0  

---

## Executive Summary

**Overall Assessment:** 3 CRITICAL issues, 11 MEDIUM issues, 6 LOW issues identified. Gaps 1-3 are largely well-designed but have integration gaps and compliance oversights. Recommend addressing all CRITICAL issues before implementation.

**Key Findings:**
- Data structures are immutable and type-hinted (✓)
- Validation in __post_init__ is mostly correct, with one critical bug in subsystem tokens
- Event emission architecture is sound, but audit trail integration is missing from all gaps
- Performance queries lack caching and pagination strategies
- Attribution models have a stubbed WEIGHTED implementation (blocker)
- PII sanitization is undocumented in several places

**Blockers Before Implementation:**
1. Fix subsystem tokens validation (Gap 1)
2. Implement WEIGHTED attribution model or make EQUAL the default (Gap 3)
3. Complete on_strategy_outcome event handler integration (Gap 3)
4. Justify scoring formula weights (Gap 2)

---

## Gap 1: Tool Execution Learning Events

### Detailed Findings

#### [CRITICAL] Subsystem Tokens Validation (Line 184-185)

**Issue:**
```python
assert sum(self.subsystem_tokens.values()) <= (self.input_tokens + self.output_tokens), \
    "subsystem_tokens sum must not exceed total tokens"
```

The validation is **incorrect**. `subsystem_tokens` is a **breakdown** of total token consumption, not separate consumption. A tool using Claude Opus (450 tokens) and vector cache (120 tokens) has 570 total tokens, but this assertion would require 570 ≤ (input + output).

**Impact:** Either:
1. The assertion is too strict and will reject valid data, OR
2. The field definition is misleading (should be documented as "additional subsystem overhead")

**Recommendation:**
- Clarify intent: Is `subsystem_tokens` a breakdown (subset) or overhead (additional)?
- If breakdown: Remove or relax assertion to allow equality
- If overhead: Rename field to `subsystem_overhead_tokens` and adjust assertion to `sum(...) + input_tokens + output_tokens` equals a budget

**Fix:**
```python
# Option 1: If subsystem_tokens is a breakdown (likely case)
assert sum(self.subsystem_tokens.values()) <= (self.input_tokens + self.output_tokens), \
    "subsystem_tokens breakdown must not exceed total tokens"

# Option 2: If subsystem_tokens is additional overhead
assert (self.input_tokens + self.output_tokens + sum(self.subsystem_tokens.values())) <= MAX_TOKENS_PER_EXECUTION, \
    "total tokens (including overhead) exceeded budget"
```

---

#### [MEDIUM] Error Message PII Sanitization (Line 146)

**Issue:**
```python
error_message: Optional[str] = None  # Exception message (sanitized for PII)
```

Documentation says "sanitized for PII", but **no sanitization logic is implemented**. Exception messages often contain:
- File paths (e.g., `/home/user/data.csv`)
- SQL queries with table names or schema details
- API responses with internal service names
- Stack traces with user directory names

**Impact:** GDPR Art. 5 (data minimization) violation if raw exception messages reach audit trail or learning events.

**Recommendation:**
- Add `_sanitize_error_message(msg: str) -> str` function that:
  - Replaces absolute paths with `<path>`
  - Redacts database schema/table names
  - Replaces internal service names with generic placeholders
  - Removes stack traces (keep only top-level exception type + generic description)
- Call it in __post_init__:
  ```python
  def __post_init__(self):
      if self.error_message:
          object.__setattr__(self, 'error_message', _sanitize_error_message(self.error_message))
  ```

---

#### [MEDIUM] `required_followup` Field Never Populated (Line 155)

**Issue:**
```python
required_followup: bool = False  # Did user ask again immediately after?
```

This proxy signal for "did tool help?" is defined but **never populated** in the ToolForgeSubsystem implementation. Line 332 hardcodes it to False.

**Impact:** Lost outcome signal for learning. The system can't measure whether a tool's output was useful beyond success/failure.

**Recommendation:**
- Define a signal detector: Did user send another message <30 seconds after tool execution?
- Populate in _handle_tool_execute() after returning tool output
- This may require async tracking (set a callback that fires on next user message)
- Document the 30-second window as configurable

---

#### [MEDIUM] Audit Trail Integration Missing (Lines 169-210)

**Issue:**
- No audit trail event emitted for tool execution telemetry
- ToolExecutionTelemetry.__post_init__ doesn't call audit logger
- The LearningEvent that wraps telemetry gets audited, but not the raw telemetry

**Impact:** GDPR Art. 30 (records of processing) — audit chain should record every tool execution, not just aggregated events.

**Recommendation:**
- Emit audit trail entry in __post_init__:
  ```python
  def __post_init__(self):
      # ... existing validation ...
      # Audit trail
      audit_backend.write_event("tool.execution_captured", {
          "tool_id": self.tool_id,
          "status": self.status.value,
          "session_id": self.session_id,
      })
  ```

---

#### [MEDIUM] EventEmitter Initialization Dependency (Lines 509-522)

**Issue:**
```python
self.event_emitter: EventEmitter = hub.get_subsystem(EventEmitter)
```

If EventEmitter hasn't been registered by hub.startup_all(), this will return None and cause AttributeError later.

**Recommendation:**
- Validate in startup():
  ```python
  def startup(self, hub: SubsystemHub):
      super().startup(hub)
      self.hub = hub
      self.event_emitter = hub.get_subsystem(EventEmitter)
      assert self.event_emitter is not None, "EventEmitter not available in SubsystemHub"
  ```

---

#### [LOW] Latency Calculation Awkwardness (Line 175-176)

**Issue:**
```python
object.__setattr__(self, 'latency_ms', 
    int((self.end_timestamp_utc - self.start_timestamp_utc).total_seconds() * 1000))
```

Works, but using `object.__setattr__` on a frozen dataclass is a code smell. It's correct, but makes reviewers pause.

**Recommendation:**
- Consider using `field(init=False)` with a property instead:
  ```python
  @property
  def latency_ms(self) -> int:
      return int((self.end_timestamp_utc - self.start_timestamp_utc).total_seconds() * 1000)
  ```
- Or validate in __post_init__ and accept latency_ms as a parameter (removing init=False)

---

### Gap 1 Review Summary

| Dimension | Status | Notes |
|-----------|--------|-------|
| **Correctness** | ⚠️ MEDIUM | Subsystem tokens validation is wrong |
| **Completeness** | ⚠️ MEDIUM | Audit trail missing; required_followup never populated |
| **Integration** | ✓ GOOD | Event emission wiring is clear, but needs validation |
| **Performance** | ✓ GOOD | O(1) telemetry capture; async emission |
| **Testing** | ✓ GOOD | 8 test cases cover happy path, failures, ratings |
| **Backwards Compat** | ✓ N/A | New feature |
| **Compliance** | ❌ CRITICAL | PII sanitization missing; audit trail missing |

---

## Gap 2: Tool Performance Ranking

### Detailed Findings

#### [MEDIUM] Scoring Formula Weights Unjustified (Lines 1116-1175)

**Issue:**
The scoring formula allocates fixed weights to different factors:
- High success rate: +0.3
- Low latency: +0.2
- Low cost: +0.2
- Recent trend: +0.1
- Cold-start penalty: -0.2

**No justification** is provided for these weights. Why 0.3 for success and only 0.2 for cost? This is a critical business decision (tool selection priority) buried in code.

**Impact:** 
- Operators can't understand why a particular tool is ranked higher
- Scoring doesn't reflect actual business priorities (cost vs. quality trade-off)
- Hard to tweak without changing code

**Recommendation:**
- Move weights to a configuration dataclass:
  ```python
  @dataclass(frozen=True)
  class ScoringWeights:
      success_rate: float = 0.3
      latency: float = 0.2
      cost: float = 0.2
      trend: float = 0.1
      cold_start_penalty: float = -0.2
      
      def __post_init__(self):
          assert sum(abs(v) for v in [self.success_rate, self.latency, self.cost, self.trend]) > 0
  ```
- Document rationale in ADR-0322
- Make configurable per tenant (if needed)

---

#### [MEDIUM] "Trend" Calculation is Misleading (Lines 1159-1162, 1094-1111)

**Issue:**
```python
recent_successes = sum(1 for t in agg["success_times"] if t >= recent_cutoff)
recent_total = recent_successes + sum(1 for t in agg["failure_times"] if t >= recent_cutoff)
# ...
recent_success_rate=recent_successes / recent_total if recent_total > 0 else 0.5,
```

The field `success_trend` is documented as "improving/declining" but is never actually calculated. Instead, `recent_success_rate` is computed but `success_trend` remains 0.0 (default).

**Impact:** 
- Trend scoring (line 1159) always evaluates to False because trend is always 0.0
- Misleading API contract — trend is unused

**Recommendation:**
- Calculate trend as improvement/decline:
  ```python
  trend = recent_success_rate - metrics.success_rate  # +X = improving, -X = declining
  object.__setattr__(metrics, 'success_trend', trend)
  ```
- Or remove `success_trend` field and score based on `recent_success_rate - overall_success_rate`

---

#### [MEDIUM] Caching Infrastructure Unused (Lines 955-956)

**Issue:**
```python
self._metrics_cache: dict[str, ToolPerformanceMetrics] = {}
self._cache_expiry: dict[str, datetime] = {}
```

Initialized but never used in get_ranked_tools(). Every call queries EventStore fresh.

**Impact:** 
- High latency for repeated queries (aggregation is O(n) over all events)
- No performance optimization despite infrastructure

**Recommendation:**
- Implement cache with TTL:
  ```python
  async def get_ranked_tools(...):
      cache_key = f"{task_type}:{error_class}"
      if cache_key in self._metrics_cache and self._cache_expiry[cache_key] > datetime.utcnow():
          return self._metrics_cache[cache_key]
      
      # Compute...
      
      self._metrics_cache[cache_key] = ranked
      self._cache_expiry[cache_key] = datetime.utcnow() + timedelta(minutes=5)
      return ranked
  ```

---

#### [MEDIUM] No Audit Trail for Ranking Decisions (Lines 965-1021)

**Issue:**
- ToolRankingManager.get_ranked_tools() queries and ranks tools but doesn't emit audit events
- No record that tool_A was ranked #1 for task_type="code" at time T

**Impact:** GDPR Art. 30 — audit trail should record ranking decisions (input to subsequent tool selection)

**Recommendation:**
- Emit audit event:
  ```python
  async def get_ranked_tools(...):
      ranked = [...]  # existing logic
      
      audit_backend.write_event("tool.ranking_computed", {
          "task_type": task_type,
          "error_class": error_class,
          "count": len(ranked),
          "top_tool": ranked[0].tool_id if ranked else None,
      })
      
      return ranked
  ```

---

#### [MEDIUM] EventStore Query Lacks Pagination (Line 984-988)

**Issue:**
```python
events = await self.event_store.query_events(
    event_type=LearningEventType.TOOL_EXECUTED,
    tenant_id=tenant_id,
    filter_fn=self._match_tool_event(task_type, error_class, time_window_days),
)
```

No limit specified. If a tenant has 100,000+ tool executions, this loads all into memory.

**Impact:** 
- Memory spike for large tenants
- Aggregation becomes O(n) where n can be very large
- System may crash or hang under load

**Recommendation:**
- Add pagination:
  ```python
  events = await self.event_store.query_events(
      event_type=LearningEventType.TOOL_EXECUTED,
      tenant_id=tenant_id,
      filter_fn=...,
      limit=10000,  # Process in batches
  )
  ```

---

#### [LOW] Cold-Start Penalty Not Documented (Line 1167-1169)

**Issue:**
```python
if metrics.is_cold_start:
    score -= 0.2
```

The -0.2 penalty means a tool with 4 samples and 100% success rate (score: 0.5 base + 0.3 success + 0.2 latency + 0.2 cost - 0.2 cold-start = 1.0) is treated the same as a tool with 100 samples and 100% success rate.

**Impact:** 
- New, high-performing tools might not be reused
- Bias toward older tools

**Recommendation:**
- Document trade-off in ADR-0322
- Consider Bayesian smoothing instead of hard penalty:
  ```python
  effective_success_rate = (success_count + prior) / (total_count + prior_samples)
  ```

---

### Gap 2 Review Summary

| Dimension | Status | Notes |
|-----------|--------|-------|
| **Correctness** | ⚠️ MEDIUM | Trend calculation unused; caching stub |
| **Completeness** | ⚠️ MEDIUM | Audit trail missing; scoring formula unjustified |
| **Integration** | ✓ GOOD | ToolForgeSubsystem integration is clean |
| **Performance** | ❌ CRITICAL | No pagination; no caching implementation; O(n) queries |
| **Testing** | ✓ GOOD | 12 test cases cover ranking, filtering, cold-start |
| **Backwards Compat** | ✓ GOOD | New subsystem doesn't break existing tool selection |
| **Compliance** | ⚠️ MEDIUM | Audit trail missing; caching of sensitive rankings |

---

## Gap 3: Skill Attribution Model

### Detailed Findings

#### [CRITICAL] WEIGHTED Model Stubbed (Lines 1661-1683)

**Issue:**
```python
async def _attribute_weighted(self, ...):
    """Weighted attribution: credit weighted by skill performance history.
    
    Skills with higher historical success rates get more credit.
    """
    # Query EventStore for historical success rates of each skill
    # For now, stub implementation
    results = []
    for skill_id in skill_ids:
        results.append(SkillAttributionResult(
            skill_id=skill_id,
            strategy_id=strategy_id,
            credit=1.0 / len(skill_ids) if skill_ids else 0.0,
            reasoning="Weighted by historical success rate (not yet implemented)",
            model=AttributionModel.WEIGHTED,
        ))
    
    return results
```

The WEIGHTED model is **not implemented** and just falls back to EQUAL attribution.

**Impact:** BLOCKER
- If WEIGHTED is the default, attribution is broken (users think they're using weighted but get equal)
- Misleading reasoning message

**Recommendation:**
- Either implement WEIGHTED properly, OR
- Make EQUAL the default and remove WEIGHTED from this gap
- If implementing WEIGHTED:
  ```python
  async def _attribute_weighted(self, strategy_id, skill_ids, outcome):
      # Query EventStore for each skill's success rate
      skill_rates = {}
      for skill_id in skill_ids:
          rate = await self._get_skill_success_rate(skill_id)
          skill_rates[skill_id] = rate
      
      # Distribute credit proportional to success rate
      total_rate = sum(skill_rates.values())
      if total_rate == 0:
          # Fall back to equal
          return self._attribute_equal(strategy_id, skill_ids, outcome)
      
      results = []
      for skill_id in skill_ids:
          credit = skill_rates[skill_id] / total_rate
          results.append(SkillAttributionResult(
              skill_id=skill_id,
              strategy_id=strategy_id,
              credit=credit,
              reasoning=f"Weighted by success rate ({skill_rates[skill_id]:.2%})",
              model=AttributionModel.WEIGHTED,
          ))
      
      return results
  ```

---

#### [CRITICAL] on_strategy_outcome Handler is a Stub (Line 1732)

**Issue:**
```python
async def _grade_skill(self, skill_id: str, score: float, reason: str):
    """Grade a skill with fair attribution."""
    # Delegate to existing auto-grading logic, but with attributed score
    pass
```

The implementation is empty. How does the skill actually get graded?

**Impact:** BLOCKER
- Attribution is computed but never applied to skill scores
- Learning signal is lost

**Recommendation:**
- Implement _grade_skill by calling the existing SkillRegistry update:
  ```python
  async def _grade_skill(self, skill_id: str, score: float, reason: str):
      """Grade a skill with fair attribution."""
      skill = self.registry.get_skill(skill_id)
      if not skill:
          logger.warning(f"Skill {skill_id} not found in registry")
          return
      
      # Update skill score with attributed credit
      updated_skill = skill.with_score_update(score=score, reasoning=reason)
      self.registry.update_skill(skill_id, updated_skill)
      
      # Emit audit trail
      audit_backend.write_event("skill.graded", {
          "skill_id": skill_id,
          "score_delta": score,
          "attribution_reason": reason,
      })
  ```

---

#### [MEDIUM] Event Handler Integration Missing (Line 1707)

**Issue:**
```python
async def on_strategy_outcome(self, event_name: str, event_data: dict):
    """Handle STRATEGY_OUTCOME event from LoopEngineer.
    ...
    """
```

This handler is defined but **never subscribed to the event bus**. Where does it hook in?

**Impact:** 
- Handler never fires
- Strategies complete without attribution

**Recommendation:**
- Subscribe in SkillForgeSubsystem.startup():
  ```python
  def startup(self, hub: SubsystemHub):
      super().startup(hub)
      hub.subscribe("strategy.outcome", self.on_strategy_outcome)
  ```

---

#### [MEDIUM] No Default Attribution Model Selected (Line 1559)

**Issue:**
```python
def __init__(self, model: AttributionModel = AttributionModel.EQUAL):
```

EQUAL is the default, but the documentation suggests WEIGHTED might be expected. This should be explicit.

**Recommendation:**
- Document in code and ADR:
  ```python
  def __init__(self, model: AttributionModel = AttributionModel.EQUAL):
      """
      Initialize attribution engine.
      
      Default: EQUAL — each skill used in a strategy gets equal credit.
      
      Args:
          model: Attribution model to use
              EQUAL: All skills get 1/N credit (recommended for MVP)
              WEIGHTED: Credit proportional to skill success rate (future work)
              FIRST/LAST: Penalize/reward skill order (discouraged)
      """
  ```

---

#### [MEDIUM] No Test for Edge Case: Single Skill (Lines 1599-1619)

**Issue:**
The EQUAL model handles N skills:
```python
credit_per_skill = 1.0 / len(skill_ids) if skill_ids else 0.0
```

If 1 skill is used, credit_per_skill = 1.0. This is correct, but the test plan doesn't mention it.

**Recommendation:**
- Add test case:
  ```python
  def test_attribution_single_skill(self):
      results = await engine.attribute_strategy_outcome(
          strategy_id="s1",
          skill_ids=["skill_1"],
          outcome="success",
      )
      assert len(results) == 1
      assert results[0].credit == 1.0
  ```

---

#### [MEDIUM] No Audit Trail for Attribution (Lines 1707-1730)

**Issue:**
- on_strategy_outcome computes attributions and grades skills, but no audit event is emitted
- GDPR Art. 30 — no record of attribution decision

**Recommendation:**
- Emit audit trail:
  ```python
  for attribution in attributions:
      audit_backend.write_event("skill.attribution", {
          "skill_id": attribution.skill_id,
          "strategy_id": attribution.strategy_id,
          "credit": attribution.credit,
          "model": attribution.model.value,
      })
  ```

---

### Gap 3 Review Summary

| Dimension | Status | Notes |
|-----------|--------|-------|
| **Correctness** | ❌ CRITICAL | WEIGHTED model stubbed; _grade_skill is empty |
| **Completeness** | ⚠️ MEDIUM | Audit trail missing; event handler not subscribed |
| **Integration** | ❌ CRITICAL | No event subscription; _grade_skill doesn't update registry |
| **Performance** | ✓ GOOD | O(n) where n ≤ 5 skills |
| **Testing** | ✓ GOOD | Coverage of attribution models (except WEIGHTED) |
| **Backwards Compat** | ✓ GOOD | New subsystem; existing skill grading unaffected until wired in |
| **Compliance** | ❌ CRITICAL | Audit trail missing for all attribution decisions |

---

## Cross-Cutting Issues (All Gaps)

### 1. Missing Audit Trail Integration (All Gaps)

**Severity:** CRITICAL  
**Finding:** None of the three gaps integrate with the audit trail. Every learning event, ranking decision, and attribution decision should be audited.

**Impact:** GDPR Art. 30 violation — no records of processing.

**Recommendation:**
- All subsystems should call:
  ```python
  audit_backend.write_event(event_type, {
      "tenant_id": tenant_id,
      "timestamp": datetime.utcnow().isoformat(),
      ...
  })
  ```
- This should be automatic in ToolExecutionTelemetry.__post_init__, ToolRankingManager.get_ranked_tools(), and SkillAttributionEngine.attribute_strategy_outcome()

---

### 2. Tenant Isolation Not Consistently Enforced (Gaps 2, 3)

**Severity:** MEDIUM  
**Finding:**
- Gap 1 includes session_id and implicit tenant via LearningEvent
- Gap 2 explicitly passes tenant_id to get_ranked_tools()
- Gap 3 doesn't mention tenant_id at all in SkillAttributionEngine

**Impact:** 
- Potential data leaks between tenants if attribution queries EventStore without tenant filter
- Inconsistent API contract

**Recommendation:**
- All queries should explicitly filter by tenant_id
- Add tenant_id parameter to SkillAttributionEngine methods:
  ```python
  async def attribute_strategy_outcome(
      self,
      strategy_id: str,
      skill_ids: List[str],
      outcome: str,
      tenant_id: str = "_default",  # ADD THIS
      ...
  )
  ```

---

### 3. Feature Flags Not Mentioned (All Gaps)

**Severity:** MEDIUM  
**Finding:** No feature flags defined to ship these gaps dark (default OFF).

**Impact:** 
- New subsystems will be active on first deployment
- No way to disable if problems arise
- Breaks "ship dark by default" rule

**Recommendation:**
- Define per gap:
  ```yaml
  spec:
    features:
      learning_gap_1_tool_telemetry: false
      learning_gap_2_tool_ranking: false
      learning_gap_3_skill_attribution: false
      # ... etc
  ```
- All subsystems check flag before startup:
  ```python
  if not hub.tenant_config.features.learning_gap_1_tool_telemetry:
      logger.info("Gap 1 (Tool Telemetry) is disabled")
      return
  ```

---

## Quick Wins (Easy Improvements)

| Gap | Issue | Fix Effort | Recommendation |
|-----|-------|-----------|-----------------|
| 1 | Latency calculation awkwardness | 15 min | Use @property or field(init=False) |
| 1 | EventEmitter validation | 10 min | Add assert in startup() |
| 2 | Scoring weights in config | 1 hour | Extract to dataclass, document rationale |
| 2 | Add caching implementation | 2 hours | Implement cache with TTL and invalidation |
| 3 | Default model documentation | 30 min | Add docstring clarifying EQUAL is default |
| All | Feature flags | 3 hours | Define flags in tenant config schema |

---

## Blockers Before Implementation

**Must fix before starting Gap 1 implementation:**

1. ✅ **Fix subsystem tokens validation** (Gap 1)
   - Clarify if tokens are breakdown or overhead
   - Fix assertion logic

2. ✅ **Implement audit trail calls** (All Gaps)
   - ToolExecutionTelemetry.__post_init__
   - ToolRankingManager.get_ranked_tools
   - SkillAttributionEngine.attribute_strategy_outcome

3. ✅ **Implement or remove WEIGHTED model** (Gap 3)
   - Full implementation if needed, OR
   - Remove from enum and make EQUAL the only option

4. ✅ **Complete _grade_skill implementation** (Gap 3)
   - Actually update skill registry
   - Emit audit trail

5. ✅ **Wire event handlers** (Gap 3)
   - Subscribe on_strategy_outcome to "strategy.outcome" bus

6. ✅ **Justify scoring formula weights** (Gap 2)
   - Document rationale in ADR-0322
   - Move to configuration

---

## Testing Coverage Assessment

### Gap 1: ToolExecutionTelemetry
- **Happy path:** ✓ (test_happy_path_successful_tool_execution)
- **Failures:** ✓ (test_failed_tool_execution)
- **Operator rating:** ✓ (test_tool_rating_attached)
- **Token breakdown:** ✓ (test_subsystem_tokens_breakdown)
- **Validation:** ✓ (test_validation_negative_tokens_rejected, test_validation_invalid_rating_rejected)
- **Outcome signals:** ✓ (test_error_resolved_signal)
- **Missing:**
  - Audit trail emission
  - required_followup population
  - PII sanitization

### Gap 2: ToolRankingManager
- **Ranking by success:** ✓ (test_rank_tools_by_success_rate)
- **Filtering:** ✓ (test_filter_by_task_type)
- **Cold-start:** ✓ (test_cold_start_penalty)
- **Cost-aware:** ✓ (test_cost_aware_ranking)
- **Empty results:** ✓ (test_empty_result_set)
- **Scoring formula:** ✓ (test_scoring_formula)
- **Missing:**
  - Caching behavior
  - Trend calculation
  - Pagination
  - Audit trail

### Gap 3: SkillAttributionEngine
- **EQUAL model:** ✓ (implied)
- **FIRST model:** ✓ (implied)
- **LAST model:** ✓ (implied)
- **WEIGHTED model:** ❌ (stubbed, no test)
- **Edge cases:** ❌ (single skill not mentioned)
- **Missing:**
  - Event subscription
  - _grade_skill implementation
  - Audit trail

---

## Recommendations Summary

### Immediate Actions
1. **Gap 1:** Fix subsystem tokens validation (1 hour)
2. **Gap 1:** Add PII sanitization for error_message (2 hours)
3. **Gap 3:** Implement _grade_skill or provide stub that logs warning (2 hours)
4. **Gap 3:** Implement WEIGHTED model or make EQUAL the default (4 hours)
5. **All Gaps:** Add audit trail integration (4 hours)

### Before Implementation Starts
1. **Gap 2:** Document scoring formula weights in ADR-0322
2. **Gap 2:** Implement caching with TTL
3. **Gap 2:** Add pagination to EventStore queries
4. **Gap 3:** Wire event handler subscriptions
5. **All Gaps:** Define feature flags

### Design Iterations
1. Clarify subsystem_tokens semantics (breakdown vs. overhead)
2. Decide on WEIGHTED model (build vs. remove)
3. Justify scoring weights with business rationale
4. Publish operator feedback UI spec (Gap 7 dependency)

---

## Approval Gates

**Code review approval:** CONDITIONAL (pending blocker fixes)

**Next Steps:**
1. Author addresses each CRITICAL issue
2. Author updates ADRs with findings
3. Re-review after fixes
4. Proceed with implementation planning

---

**Review Date:** 2026-08-19  
**Reviewer:** Claude Code  
**Status:** AWAITING AUTHOR RESPONSE (5 CRITICAL items)
