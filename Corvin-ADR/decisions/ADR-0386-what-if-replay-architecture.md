---
id: ADR-0386
status: PROPOSED
depends_on: [ADR-0383]
relates_to: []
paths:
  - core/learning/replay_engine.py
  - core/learning/snapshot.py
  - core/console/routes/replay.py
docs:
  - docs/v0.6-design/V0.6_IDEAS.md
---

# ADR-0386: What-If Replay Architecture

## Problem

Operators make decisions but can't easily explore counterfactuals: "What if I'd chosen option B instead?"

## Solution

**Deterministic state snapshots** at every decision point + replay engine for counterfactual exploration:

```python
@dataclass(frozen=True)
class DecisionSnapshot:
    """Immutable decision-point snapshot for replay."""
    
    turn_id: str
    decision_point_id: str
    timestamp: datetime
    
    # Decision context
    available_options: List[Option]  # choices presented to operator
    chosen_option: Option             # what operator selected
    
    # State snapshot
    context: Dict[str, Any]           # full execution context
    operator_fingerprint: OperatorFingerprint  # operator's fingerprint at this moment
    
    # Cost/quality estimates
    estimated_cost: float             # $/unit
    estimated_quality: float          # [0.0 .. 1.0]
    
    # Metadata
    operator_id: str
    tenant_id: str
    
    # Retention
    created_at: datetime
    expires_at: datetime = created_at + timedelta(days=30)
```

## Replay Mechanism

```python
async def replay(
    snapshot: DecisionSnapshot,
    counterfactual_option: Option,
) -> ReplayOutcome:
    """Execute turn with different option, return counterfactual outcome."""
    
    # 1. Validate snapshot is not expired
    assert now() < snapshot.expires_at
    
    # 2. Restore context from snapshot
    context = snapshot.context.copy()
    
    # 3. Substitute option
    context["chosen_option"] = counterfactual_option
    
    # 4. Re-execute turn (deterministic)
    outcome = await execute_turn_deterministic(context)
    
    # 5. Compare to actual
    return ReplayOutcome(
        actual_outcome=snapshot,
        counterfactual_outcome=outcome,
        cost_delta=outcome.estimated_cost - snapshot.estimated_cost,
        quality_delta=outcome.estimated_quality - snapshot.estimated_quality,
        trade_off_analysis=analyze_trade_offs(
            snapshot.estimated_cost,
            snapshot.estimated_quality,
            outcome.estimated_cost,
            outcome.estimated_quality,
        ),
    )
```

## Determinism Contract

**Invariant:** Same context + same option = exact same outcome (bit-for-bit).

**Implementation:**
- Disable all randomness (seed RNG if used)
- Disable external APIs (mock results from snapshot)
- Disable user input (use snapshotted decisions)
- Disable time-dependent operations (use snapshotted timestamp)

**Validation:**
```python
def validate_determinism(snapshot):
    """Replay twice, verify outcomes are identical."""
    outcome1 = replay(snapshot, snapshot.chosen_option)
    outcome2 = replay(snapshot, snapshot.chosen_option)
    
    assert outcome1.cost == outcome2.cost
    assert outcome1.quality == outcome2.quality
    assert outcome1.trace == outcome2.trace
```

## Snapshot Storage & Lifecycle

**Location:** `~/.corvin/tenants/{tenant_id}/learning/snapshots/{turn_id}/{decision_point_id}.json`

**Retention:** 30 days (auto-purge older)

**Size management:** Compress snapshots >1MB, limit to 10,000 per operator

**Permissions:** 0600 (operator only)

## Counterfactual Display UI

```
┌─────────────────────────────────────────────┐
│ What-If: Choose "Option B"?                 │
├─────────────────────────────────────────────┤
│                                             │
│ ACTUAL (Your Choice)                        │
│ Option A: "Use Claude API"                  │
│ ✓ Success  $0.045  87% quality              │
│                                             │
│ WHAT-IF (If You Chose B)                    │
│ Option B: "Use Local LLM"                   │
│ ? Result unknown  $0.003  72% quality       │
│                                             │
│ TRADE-OFF ANALYSIS                          │
│ • Cost savings: 93% ($0.042)                │
│ • Quality loss: 15% (-8.25 points)          │
│ • Best for: offline scenarios, cost-        │
│   sensitive tasks                           │
│ • Risk: 15% chance of quality degradation   │
│                                             │
│ [Learn from this]  [Dismiss]                │
└─────────────────────────────────────────────┘
```

## Cost & Quality Estimation

**Cost model:** Learned from actual turn costs (v0.5 baseline + operator feedback).

**Quality model:** Combination of:
- Operator's grading (ADR-0317)
- Objective metrics (code coverage, test pass rate, latency)
- Subjective assessment (did it solve the problem?)

**Accuracy target:** ±10% error on estimates

## Testing (15+ tests)

- Snapshot creation & expiration (4)
- Deterministic replay (4)
- Cost/quality estimation (3)
- Counterfactual comparison (4)

---

## GDPR Notes

Snapshots contain operator's context only (no external data). Stored locally, encrypted at rest. Expires after 30 days. No transmission to server.

## Rollback

Set `spec.features.operator_modeling_replay: false` to disable What-If (snapshots still created but not displayed).

