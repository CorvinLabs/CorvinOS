# TreeOfThoughts: Unified Learning System Design

**Status:** PROPOSAL (Phase 0)  
**Author:** Claude Haiku 4.5  
**Date:** 2026-08-17  
**Scope:** Complete redesign of learning infrastructure (Concepts + Metaphers + Skills + Events → unified 3-level hierarchy)

---

## Executive Summary

Current state fragments learning into 4 incompatible layers:
- **Concepts** (narrative, Operator Notes, 500+ lines)
- **Metaphers** (pattern-level, <100 lines, undocumented)
- **Skills** (behavioral, injected, auto-graded, <8KB)
- **Learning Events** (telemetry, confidence scoring, isolated from others)

**TreeOfThoughts** unifies these into a single **3-level hierarchy with unified learning DNA**:

```
Pattern (atomic, <200 lines)
  ↓ composes into
Method (workflow, 100-500 lines)
  ↓ composes into
Framework (architectural, ∞)
```

Each level:
- Has confidence (0.0-1.0, learned from production)
- Owns anti-patterns ("don't use when X")
- Receives active learning feedback
- Is reachability-proved (E2E, not unit-tested)
- Links to ADRs (architectural decisions)

---

## Design Specification

### 1. Unified Entity Model

```python
# All three levels share this DNA
@dataclass(frozen=True)
class TreeNode:
    # Identity
    id: str  # "pattern_retry_backoff" or "method_voice_synthesis"
    level: Literal["pattern", "method", "framework"]
    name: str
    
    # Learning
    confidence: float  # 0.0-1.0, mutable but immutable history
    confidence_history: list[ConfidenceEvent]  # [date, delta, reason, context]
    
    # Semantics
    body: str  # code, prose, or reference
    when: list[str]  # use cases where this applies
    anti_when: list[str]  # use cases where this MUST NOT apply
    
    # Composition (for Method/Framework)
    children: list[str]  # IDs of Patterns (for Method) or Methods (for Framework)
    
    # Proof
    e2e_tests: list[str]  # references to test files
    metrics: dict[str, float]  # latency, cost, success_rate, etc.
    
    # Documentation
    operator_notes: list[OperatorNote]  # timestamped, append-only
    adr_link: str | None  # "ADR-0351" or None
    
    # Audit
    created_at: ISO8601
    modified_at: ISO8601
    modified_by: list[str]  # who changed this
```

### 2. Learning Event (unified)

```python
@dataclass(frozen=True)
class LearningEvent:
    subject_id: str  # which Pattern/Method/Framework?
    event_type: Literal["used", "failed", "graded", "refuted", "antipattern_detected"]
    
    # The gradient
    confidence_delta: float  # -1.0 to +1.0, how much to update?
    reason: str  # "worked in production" | "failed with msg X" | "user graded low"
    
    # Context (what was happening?)
    context: dict = dataclass.field(default_factory=dict)
    # {task_id, user_id, stage, metrics: {latency, cost, tokens}, outcome}
    
    timestamp: ISO8601
    immutable: bool = True  # never edited, only appended
```

### 3. Confidence Update Algorithm

```python
def update_confidence(node: TreeNode, event: LearningEvent) -> float:
    """
    Bayesian update: blend new evidence with prior.
    
    Returns: new_confidence ∈ [0.0, 1.0]
    """
    # If antipattern was used in its anti_when context, strong penalty
    if (event.event_type == "antipattern_detected" 
        and event.reason in node.anti_when):
        event.confidence_delta = -0.3  # hard penalty
    
    # Blend: 70% prior, 30% new evidence
    # (confidence decays if not used; learning events boost it)
    alpha = 0.3  # learning rate
    new_conf = (1 - alpha) * node.confidence + alpha * clip(
        node.confidence + event.confidence_delta,
        0.0, 1.0
    )
    
    # For composite nodes (Method/Framework):
    # re-compute from children if any changed
    if node.children:
        child_confs = [get_confidence(child_id) for child_id in node.children]
        new_conf = weighted_avg(child_confs, weights="equal")
    
    return new_conf
```

### 4. Reachability Proof (E2E enforcement)

Every Pattern/Method MUST have:

```yaml
e2e_tests:
  - name: test_openai_tts_with_429_retry
    file: tests/test_voice_tts_*.py
    status: ✅ PASSING
    last_run: 2026-08-17
    
metrics:
  avg_latency_ms: 4.2
  success_rate: 0.97
  calls_in_production: 15  # how many times did this actually run?
  last_production_run: 2026-08-17T10:45:00Z
```

**Rule:** If `calls_in_production == 0` for >7 days, confidence drops by 0.1/day (decay).

### 5. Anti-Pattern Enforcement

```yaml
pattern: retry-backoff-exponential
when: ["API rate-limits (429)", "transient network errors"]
anti_when: ["user input validation", "auth failures", "cache misses"]

# If this pattern is used in an anti_when context:
penalty: 0.3  # confidence -= 0.3
alert: true   # emit warning in console & audit log
```

### 6. Hierarchical Confidence Aggregation

```
Framework("voice-synthesis-strategy")
  confidence = weighted_avg([
    confidence(Method("openai-tts")) * 0.4,
    confidence(Method("edge-tts")) * 0.3,
    confidence(Method("piper-tts")) * 0.3,
  ])

Method("openai-tts")
  confidence = weighted_avg([
    confidence(Pattern("retry-backoff")) * 0.5,
    confidence(Pattern("timeout-budget")) * 0.3,
    confidence(Pattern("fallback-chain")) * 0.2,
  ])
```

---

## Console Features

### Settings

```yaml
learning:
  # Global
  enabled: true
  track_all_events: true  # audit trail
  confidence_decay_days: 7  # unused → confidence drops
  
  # Dashboard
  dashboard:
    view: "confidence-tree"  # show 3-level hierarchy
    sort_by: "confidence"  # or "last_used", "success_rate"
    filters:
      - "confidence > 0.7"
      - "not used in 7d"
      - "antipattern violations"
  
  # Auto-suggest
  auto_suggest:
    enabled: true
    trigger: "when_confidence_drops_below_0.5"
    message: "Pattern X might be better (0.85 vs 0.62)"
    confidence_threshold: 0.75
  
  # Feedback
  feedback:
    inline_grade: true  # "👍 / 👎" after each method
    weekly_report: true  # "top patterns this week"
    confidence_forecast: true  # "will drop below X by Friday"
  
  # Anti-patterns
  anti_patterns:
    track: true
    alert_on_violation: true  # console warning
    block_if_critical: false  # or true to hard-fail
  
  # Audit
  audit:
    track_all: true
    immutable: true
    export_monthly: true  # CSV of events
```

### Dashboard Components

```
┌─ TreeOfThoughts Dashboard ──────────────────────┐
│                                                 │
│  📊 Framework: voice-synthesis-strategy         │
│     Confidence: 0.85 ████████░  (↑ 0.02 this week)
│                                                 │
│  ├─ 🔵 Method: openai-tts-with-fallback        │
│  │  Confidence: 0.82 ████████░                  │
│  │  Used: 15 times (last: 2h ago)               │
│  │  Success rate: 97%                           │
│  │                                              │
│  │  ├─ 🟢 Pattern: retry-backoff-exponential   │
│  │  │  Conf: 0.78, E2E: ✅, Prod: 15x          │
│  │  │                                           │
│  │  └─ 🟢 Pattern: timeout-budgeting           │
│  │     Conf: 0.85, E2E: ✅, Prod: 12x          │
│  │                                              │
│  ├─ 🔵 Method: edge-tts                        │
│  │  Confidence: 0.88 (fallback, always works)   │
│  │  Used: 3 times (always as fallback)          │
│  │                                              │
│  └─ 🔵 Method: piper-tts                       │
│     Confidence: 0.72 (local, rarely used)       │
│     Used: 2 times (budget exceeded)             │
│                                                 │
│  ⚠️ Antipattern Violations: 0                   │
│  📈 Forecast: confidence stays ≥0.80 for 30d   │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## Operator Notes (First-Class)

```yaml
Pattern: retry-backoff-exponential
operator_notes:
  - date: 2026-08-17
    author: shumway
    text: |
      Fixed timeout bounds to respect VOICE-10 budget.
      Original: 3s/6s/12s = 21s total (consumed 95% of budget).
      New: 1s/2s/4s = 7s total (leaves room for fallback).
      This was the root cause of "all providers failed" errors.
  
  - date: 2026-08-16
    author: claude-haiku-4-5
    text: |
      Added RateLimitError-specific handling to _try_openai().
      Manual retry loop with exponential backoff, not SDK max_retries.
      SDK retries were disabled to protect budget; manual loop respects deadline.
```

(Append-only. Never edit or delete. Critical for audit trail.)

---

## Implementation Roadmap

### Phase 1: Core Data Model & Persistence (Weeks 1-2)
- [ ] Define `TreeNode`, `LearningEvent`, `ConfidenceEvent` data classes
- [ ] Create storage layer (JSON files, date-partitioned like audit logs)
- [ ] Implement confidence update algorithm (with Bayesian blending)
- [ ] Write tests for confidence math (edge cases, aggregation)

### Phase 2: Reachability Proof & E2E Integration (Weeks 3-4)
- [ ] Refactor existing Concepts/Metaphers/Skills into Pattern/Method/Framework
- [ ] Wire E2E tests into TreeNode (scan `tests/` for `@e2e_for("pattern_id")`)
- [ ] Add metrics collection (latency, cost, success_rate)
- [ ] Track `calls_in_production` (via audit log)

### Phase 3: Active Learning Loop (Weeks 5-6)
- [ ] Auto-emit `LearningEvent` after each agent/method execution
- [ ] Implement confidence decay (unused → confidence drops)
- [ ] Build auto-suggest engine (when confidence drops)
- [ ] Anti-pattern detection (context-aware penalties)

### Phase 4: Console Dashboard (Weeks 7-8)
- [ ] TreeOfThoughts dashboard (3-level view, drill-down)
- [ ] Settings UI (confidence thresholds, feedback modes, decay)
- [ ] Inline grading ("👍 / 👎" after each method)
- [ ] Weekly reports (top patterns, declining confidence)

### Phase 5: Operator Notes & Audit (Weeks 9-10)
- [ ] First-class operator notes (append-only, versioned)
- [ ] Full audit trail export (monthly CSV)
- [ ] ADR linking (show which decisions drove pattern creation)

### Phase 6: Documentation & Migration (Weeks 11-12)
- [ ] Migrate all Concepts → Frameworks
- [ ] Migrate all Metaphers → Patterns
- [ ] Migrate all Skills → Patterns/Methods
- [ ] docs-as-definition-of-done pass

---

## Success Criteria

✅ **Clarity:** One model, three levels, unified semantics  
✅ **Learning:** Confidence updates from production, not opinions  
✅ **Proof:** Every pattern used real E2E, not unit-tested  
✅ **Safety:** Anti-patterns tracked, violations alert  
✅ **Auditability:** Immutable event log, operator notes, ADR links  
✅ **Adoption:** Existing Concepts/Skills migrate without rewrite  

---

## Risks & Open Questions

1. **Storage at scale:** With millions of LearningEvents, JSON append becomes slow. Need Parquet or DB?
2. **Confidence decay:** How fast should unused patterns decay? 0.1/day? Configurable per pattern?
3. **Hierarchical aggregation:** What if a Method's patterns have conflicting confidence? Weighted avg? Min? Consensus?
4. **Antipattern false positives:** If a pattern is *sometimes* valid in an anti_when context, how to model that?
5. **Operator burden:** Will humans maintain Operator Notes, or auto-generate them from events?

---

## Next Steps

1. **ADR-0365:** Formalize the 3-level hierarchy & learning semantics
2. **ADR-0366:** Design reachability proof & E2E integration
3. **ADR-0367:** Define console dashboard & settings
4. Then: Implementation roadmap above

