# k=2 Tier 1 Router — Algorithm & Implementation

**Status:** k=2 Loop 2 Documentation  
**Related:** ADR-0845 (k=2 core), model_selector.py (implementation)

## Algorithm: Complexity → Model Selection

### Stratification Formula

```
Input: task_id (string), complexity ∈ {simple, medium, complex}

1. Deterministic Bucket:
   bucket = hash(task_id) ∈ [0.0, 1.0]
   → Uses SHA256, normalized to [0–10000) / 10000
   → Same task_id always produces same bucket

2. Complexity-Based Ranges:
   
   SIMPLE:
   - Haiku: [0.00, 0.35) → 35%
   - Sonnet: [0.35, 1.00) → 65%
   - Opus: never
   
   MEDIUM:
   - Haiku: [0.00, 0.10) → 10%
   - Sonnet: [0.10, 0.60) → 50%
   - Opus: [0.60, 1.00) → 40%
   
   COMPLEX:
   - Haiku: never
   - Sonnet: [0.00, 0.20) → 20%
   - Opus: [0.20, 1.00) → 80%

3. Output:
   model = MODEL_NAMES[tier]
   → "claude-haiku-4-5-20251001"
   → "claude-sonnet-5"
   → "claude-opus-5-5"
```

### Why These Percentages?

**Simple Tasks (Haiku 35%):**
- Haiku cost ≈ 1/15 of Opus
- Simple tasks need fast, cheap execution
- 35% Haiku gives cost savings while maintaining quality
- 65% Sonnet ensures edge cases get mid-tier reasoning

**Medium Tasks (Sonnet 50% / Opus 40%):**
- 50/50 split enables A/B testing
- Opus handles ambiguous/complex subtasks
- Sonnet covers typical medium-complexity work
- Allows measuring Opus win rate

**Complex Tasks (Opus 80%):**
- Opus needed for reasoning-heavy tasks
- 20% Sonnet handles outliers (cost sensitivity)
- No Haiku (too weak for complex reasoning)

### Implementation: `Tier1Router.route()`

```python
# Deterministic bucket (same task_id → same bucket)
bucket = hash_to_bucket(task_id)  # [0.0, 1.0]

# Cumulative threshold logic
if complexity == "simple":
    if bucket < 0.35:
        return "claude-haiku-4-5-20251001"
    else:
        return "claude-sonnet-5"

elif complexity == "medium":
    if bucket < 0.10:
        return "claude-haiku-4-5-20251001"
    elif bucket < 0.60:
        return "claude-sonnet-5"
    else:
        return "claude-opus-5-5"

elif complexity == "complex":
    if bucket < 0.20:
        return "claude-sonnet-5"
    else:
        return "claude-opus-5-5"
```

## Consistency & Reproducibility

**Determinism:** Same `task_id` → same `bucket` → same model across runs.

**Enables A/B Testing:** 
- Fix complexity, vary task_id
- Observe model-specific performance
- Update stratification thresholds based on loss signal (k=4)

**No Randomness:** Pure hash-based selection (no RNG, no state).

## Audit Trail Integration (ADR-0297)

**ModelSelectionDecision:**
```python
@dataclass(frozen=True)
class ModelSelectionDecision:
    task_id: str              # Safe (task metadata)
    complexity: str           # Safe ("simple", "medium", "complex")
    selected_model: str       # Safe (model name)
    stratification_bucket: float  # Safe (0.0–1.0, one-way hash)
```

**No PII, No Secrets:**
- No prompts (only task metadata)
- No user data (only task_id hash)
- No API keys (none generated)
- Safe for audit_backend.emit() per ADR-0297

## Quality Gate: k=2 Success

✅ **Algorithm:** Matches implementation (Tier1Router._select_tier)  
✅ **Tests:** 30 tests cover all paths (distribution, determinism, edge cases)  
✅ **Audit-Safe:** No PII in decision or logs  
✅ **Reproducible:** Same task_id → same output  

**Next:** k=2 Loop 2 complete when this doc matches code exactly.
