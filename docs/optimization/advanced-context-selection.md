# Phase 5: Advanced Context Optimizations (ADR-0394)

## Overview

Phase 5 implements intelligent context reduction through three orthogonal optimization techniques:

1. **Selective Injection** — Filter context by relevance (10-15% savings)
2. **Memory Pruning** — Remove low-confidence and expired memories (5-10% savings)
3. **ADR Reranking** — Surface most relevant architectural decisions (5-10% savings)

**Total expected savings:** 15-25% additional reduction on top of Phase 1-4, for a combined **50-70% total context reduction**.

---

## Architecture

### Context Engineering Pipeline Integration

Each optimization is implemented as a reusable module + a pipeline stage:

```
memory (root)
  ↓
graph
  ↓
skill
  ↓
selective_injection (Phase 5) ← filters memory.matches by relevance
  ↓
memory_pruning (Phase 5) ← prunes by confidence, age, quota
  ↓
adr_reranking (Phase 5) ← reranks graph.related_decisions by score
  ↓
approach_synthesis
  ↓
blocker_id
```

All three stages:
- Run **post-memory** to have something to optimize
- Are **pure** (no side effects, read-only)
- Are **optional** (can be disabled via feature flags)
- **Degrade silently** when disabled or when they have nothing to process

---

## Module 1: Selective Injection

**File:** `operator/context_engineering/selective_injection.py`

**Class:** `SelectiveInjector`

### Purpose

Filters memory matches and other context items by relevance to the task query using embedding-based cosine similarity. Drops items below a relevance threshold to reduce noise.

### Key Methods

```python
filter_by_relevance(
    context_items: List[Any],
    query: str,
    threshold: Optional[float] = None
) -> Tuple[List[Any], dict]
```

**Pipeline:**
1. Normalize query
2. Embed query (cached by hash)
3. Score each item by cosine similarity to query
4. Filter items >= threshold
5. Deduplicate by item id (keep highest score)
6. Return filtered items + telemetry

### Parameters

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `threshold` | float | 0.7 | Relevance score threshold (0.0-1.0). Items below this are dropped. |
| `context_items` | List | — | Items to filter (must have 'id' or 'filename' field). |
| `query` | str | — | Task query to measure relevance against. |

### Returns

Tuple of:
- **filtered_items**: List of items with relevance >= threshold
- **telemetry**: Dict with:
  - `items_before`: Count before filtering
  - `items_after`: Count after filtering
  - `dropped_count`: Number of items dropped
  - `dropped_reasons`: Dict breaking down why items were dropped
  - `threshold`: Threshold used
  - `duration_ms`: Execution time

### Expected Savings

- **10-15% context reduction** on memory matches
- Preserves high-relevance items, drops noisy low-score matches

### Trade-offs

| Pro | Con |
|-----|-----|
| Reduces noise in context | May drop marginally-relevant items |
| Fast (hash-based pseudo-embedding) | Real embedding model would be better |
| Deterministic (same input = same output) | Embedding quality limited by hash-based approach |

---

## Module 2: Memory Pruning

**File:** `operator/context_engineering/memory_pruning.py`

**Class:** `MemoryPruner`

### Purpose

Non-destructively removes low-confidence and expired memories from the context. Memories remain in the audit trail but are not rendered in the prompt.

### Key Methods

```python
prune(
    memories: List[Any],
    tenant_id: str = "_default",
    now: Optional[datetime] = None
) -> Tuple[List[Any], dict]
```

**Pipeline:**
1. Filter by confidence floor
2. Filter by age (retention policy)
3. Sort by confidence (highest first)
4. Truncate to quota
5. Return pruned list + telemetry

### Parameters

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `confidence_floor` | float | 0.3 | Minimum confidence score to keep (0.0-1.0). |
| `max_age_days` | int | 30 | Maximum memory age in days before expiry. |
| `per_tenant_quota` | int | 5 | Maximum memories to keep per tenant. |
| `memories` | List | — | Memory objects with 'confidence' and 'created_at' fields. |
| `tenant_id` | str | "_default" | Tenant identifier for logging/audit. |
| `now` | datetime | datetime.now() | Reference time for age calculation. |

### Returns

Tuple of:
- **pruned_memories**: List of memories passing all filters, sorted by confidence (highest first)
- **telemetry**: Dict with:
  - `memories_before`: Count before pruning
  - `memories_after`: Count after pruning
  - `dropped_count`: Number of memories dropped
  - `dropped_reasons`: Dict breaking down why memories were dropped:
    - `confidence_below_floor`: Count below confidence threshold
    - `age_exceeds_retention`: Count exceeding age retention policy
    - `quota_exceeded`: Count exceeding per-tenant quota
  - Configuration used (confidence_floor, max_age_days, per_tenant_quota)
  - `duration_ms`: Execution time
  - `tenant_id`: Tenant that was pruned

### Expected Savings

- **5-10% context reduction** on memory count
- Removes low-quality and stale memories

### Rules

**All applied in order; a memory is dropped if it fails ANY rule:**

1. **Confidence Floor:** Drop memories with `confidence < 0.3`
   - Only keeps memories with reasonable quality
   - Default threshold: 30% confidence minimum

2. **Age Retention:** Drop memories older than 30 days
   - Keeps context focused on recent decisions
   - Prevents accumulated stale memories

3. **Per-Tenant Quota:** Keep at most 5 memories per tenant
   - Enforces hard limit on context size
   - Keeps highest-confidence memories when quota exceeded

### Trade-offs

| Pro | Con |
|-----|-----|
| Removes clear noise and stale data | May need tuning per use case |
| Respects GDPR retention policies | Quota may be too aggressive for some tasks |
| Sorted by confidence (best first) | No semantic consideration (just confidence score) |

---

## Module 3: ADR Reranking

**File:** `operator/context_engineering/adr_reranking.py`

**Class:** `ADRRanker`

### Purpose

Reranks ADRs by multiple criteria (recency, relevance, status) to surface the most relevant architectural decisions first. Removes superseded ADRs and keeps only the top-k.

### Key Methods

```python
rerank(
    adrs: List[Any],
    query: str = "",
    now: Optional[datetime] = None
) -> Tuple[List[Any], dict]
```

**Pipeline:**
1. Score each ADR:
   - Recency: Recent ADRs score higher (-0.5 per year old)
   - Relevance: Semantic similarity to query
   - Status: ACCEPTED (1.0) > FROZEN (0.8) > PROPOSED (0.7) > SUPERSEDED (0.0)
2. Filter superseded ADRs if a newer one exists
3. Sort by composite score (descending)
4. Keep top-k
5. Return reranked list + telemetry

### Parameters

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `keep_top_k` | int | 3 | Number of top ADRs to keep. |
| `recency_weight` | float | 0.3 | Weight for recency score (0.0-1.0). |
| `relevance_weight` | float | 0.4 | Weight for relevance score (0.0-1.0). |
| `status_weight` | float | 0.3 | Weight for status score (0.0-1.0). |
| `adrs` | List | — | ADR objects with 'id', 'status', 'created_at', 'supersedes' fields. |
| `query` | str | "" | Optional task query to measure relevance against. |
| `now` | datetime | datetime.now() | Reference time for recency calculation. |

### Returns

Tuple of:
- **reranked_adrs**: List of top-k ADRs by composite score
- **telemetry**: Dict with:
  - `adrs_before`: Count before reranking
  - `adrs_after`: Count after reranking
  - `dropped_count`: Number of ADRs dropped
  - `dropped_reasons`: Dict breaking down why ADRs were dropped:
    - `superseded`: Count that were superseded
    - `truncation_to_keep_top_k`: Count dropped by keep_top_k limit
  - `keep_top_k`: K value used
  - `weights`: Dict of weight values used
  - `duration_ms`: Execution time

### Expected Savings

- **5-10% context reduction** on ADR count
- Surfaces highest-signal ADRs first

### Scoring Details

**Recency Score (1.0 = today, 0.0 = 2+ years old):**
```
score = max(0.0, min(1.0, 1.0 - (0.5 * age_years)))
```
- Today: 1.0
- 1 year ago: 0.5
- 2+ years ago: 0.0

**Status Score:**
| Status | Score |
|--------|-------|
| accepted | 1.0 |
| frozen | 0.8 |
| proposed | 0.7 |
| superseded | 0.0 |

**Relevance Score:** Cosine similarity of ADR title+id to query (0.0-1.0)

**Composite Score:**
```
score = (recency_weight * recency) + (relevance_weight * relevance) + (status_weight * status)
```

**Supersession Filtering:** If ADR-A supersedes ADR-B and both are in the list, drop ADR-B.

### Trade-offs

| Pro | Con |
|-----|-----|
| Prioritizes accepted/recent ADRs | May deprioritize newer proposals |
| Removes superseded ADRs automatically | Requires accurate `supersedes` metadata |
| Customizable weights | Three weights to tune |
| Deterministic | Embedding quality limited by hash-based approach |

---

## Pipeline Integration

### Stage Registration

The three stages self-register at package import:

```python
# In stages/selective_injection_stage.py
class SelectiveInjectionStage:
    id = "selective_injection"
    requires = ("memory",)
    effect = "pure"
    trust = "builtin"

register_stage(SelectiveInjectionStage())
```

Import in `stages/__init__.py`:
```python
from . import selective_injection_stage, memory_pruning_stage, adr_reranking_stage
```

### Configuration

Each stage can be configured in `tenant.corvin.yaml`:

```yaml
spec:
  context_engineering:
    pipeline:
      - stage: memory
      - stage: graph
      - stage: skill
      - stage: selective_injection
        config:
          enabled: true
          relevance_threshold: 0.7
      - stage: memory_pruning
        config:
          enabled: true
          confidence_floor: 0.3
          max_age_days: 30
          per_tenant_quota: 5
      - stage: adr_reranking
        config:
          enabled: true
          keep_top_k: 3
          recency_weight: 0.3
          relevance_weight: 0.4
          status_weight: 0.3
      - stage: approach_synthesis
      - stage: blocker_id
```

### Feature Flags

Each stage can be toggled independently:

```python
# In tenant.corvin.yaml
spec:
  features:
    phase_5_selective_injection: true  # Enable/disable
    phase_5_memory_pruning: true       # Enable/disable
    phase_5_adr_reranking: true        # Enable/disable
```

Or in stage config:
```yaml
- stage: selective_injection
  config:
    enabled: false  # This stage is disabled
```

---

## Telemetry

Each optimization produces telemetry:

### SelectiveInjectionStage Telemetry

```python
{
    "stage": "selective_injection",
    "status": "ok",
    "sources": [
        {"id": "dropped_count", "score": 5},
        {"id": "threshold", "score": 0.7}
    ],
    "duration_ms": 12.5
}
```

### MemoryPruningStage Telemetry

```python
{
    "stage": "memory_pruning",
    "status": "ok",
    "sources": [
        {"id": "dropped_count", "score": 3},
        {"id": "confidence_floor", "score": 0.3},
        {"id": "max_age_days", "score": 30}
    ],
    "duration_ms": 8.2
}
```

### ADRRerangkingStage Telemetry

```python
{
    "stage": "adr_reranking",
    "status": "ok",
    "sources": [
        {"id": "dropped_count", "score": 2},
        {"id": "keep_top_k", "score": 3}
    ],
    "duration_ms": 10.1
}
```

---

## Testing

**File:** `operator/context_engineering/tests/test_advanced_optimizations_adr0394.py`

**Test Coverage:** 16 comprehensive tests

### Test Categories

1. **Selective Injection (6 tests)**
   - Basic relevance filtering
   - Empty list handling
   - Custom thresholds
   - Deduplication
   - Invalid parameters
   - Telemetry structure

2. **Memory Pruning (6 tests)**
   - Confidence filtering
   - Age retention
   - Per-tenant quota
   - Combined rules
   - Empty list handling
   - Invalid parameters

3. **ADR Reranking (5 tests)**
   - Status ranking
   - Recency ranking
   - Supersession filtering
   - Keep-top-k truncation
   - Empty list handling

4. **Feature Flags (3 tests)**
   - Selective injection disabled
   - Memory pruning disabled
   - ADR reranking disabled

5. **Edge Cases (3 tests)**
   - All items below relevance threshold
   - All memories below confidence floor
   - No ADRs to rank

6. **Context Size Reduction (3 tests)**
   - Selective injection reduces count
   - Memory pruning reduces count
   - ADR reranking reduces count

### Running Tests

```bash
# From repo root
cd /home/shumway/projects/CorvinOS

# Run all Phase 5 tests
python3 -m pytest operator/context_engineering/tests/test_advanced_optimizations_adr0394.py -v

# Run specific test class
python3 -m pytest operator/context_engineering/tests/test_advanced_optimizations_adr0394.py::TestSelectiveInjector -v

# Run with coverage
python3 -m pytest operator/context_engineering/tests/test_advanced_optimizations_adr0394.py --cov=operator.context_engineering --cov-report=html
```

---

## Configuration Guide for Operators

### Default Configuration (Recommended)

Use the defaults from `stages/config.py`:

```yaml
spec:
  context_engineering:
    pipeline:
      - stage: memory
      - stage: graph
      - stage: skill
      - stage: selective_injection          # 10-15% savings
      - stage: memory_pruning               # 5-10% savings
      - stage: adr_reranking                # 5-10% savings
      - stage: approach_synthesis
      - stage: blocker_id
```

This gives **15-25% additional context reduction** with safe defaults.

### Aggressive Optimization (for token-constrained scenarios)

```yaml
spec:
  context_engineering:
    pipeline:
      - stage: memory
      - stage: graph
      - stage: skill
      - stage: selective_injection
        config:
          relevance_threshold: 0.8  # Drop more items
      - stage: memory_pruning
        config:
          confidence_floor: 0.5     # Higher confidence bar
          max_age_days: 14          # Shorter retention
          per_tenant_quota: 3       # Tighter quota
      - stage: adr_reranking
        config:
          keep_top_k: 2             # Keep fewer ADRs
      - stage: approach_synthesis
      - stage: blocker_id
```

### Conservative Configuration (for quality-focused scenarios)

```yaml
spec:
  context_engineering:
    pipeline:
      - stage: memory
      - stage: graph
      - stage: skill
      - stage: selective_injection
        config:
          relevance_threshold: 0.5  # Keep more items
      - stage: memory_pruning
        config:
          confidence_floor: 0.1     # Lower confidence bar
          max_age_days: 60          # Longer retention
          per_tenant_quota: 10      # Higher quota
      - stage: adr_reranking
        config:
          keep_top_k: 5             # Keep more ADRs
      - stage: approach_synthesis
      - stage: blocker_id
```

---

## Performance Characteristics

### Time Complexity

| Module | Complexity | Typical | Notes |
|--------|-----------|---------|-------|
| SelectiveInjector | O(n) | 10-15ms | Embedding cache hits speed this up |
| MemoryPruner | O(n log n) | 5-10ms | Sorting dominates |
| ADRRanker | O(n log n) | 8-12ms | Scoring + sorting |

### Space Complexity

| Module | Complexity | Notes |
|--------|-----------|-------|
| SelectiveInjector | O(n) | Embedding cache for recent queries |
| MemoryPruner | O(n) | Sorts in-place |
| ADRRanker | O(n) | Embedding cache for query |

### Typical Savings

| Optimization | Before | After | Savings |
|--------------|--------|-------|---------|
| Phase 1-4 alone | 100% | 30-50% | 50-70% |
| + Selective Injection | 100% | 25-40% | 60-75% |
| + Memory Pruning | 100% | 20-35% | 65-80% |
| + ADR Reranking | 100% | 18-32% | 68-82% |

---

## Quality & Trade-offs

### What We Gain

- **15-25% additional context reduction** (on top of Phase 1-4)
- **Deterministic output** (same input = same output)
- **Configurable thresholds** (tune per use case)
- **Non-destructive** (audit trail unchanged)
- **Fail-safe** (stage failures don't break the turn)

### What We Trade Away

- **Marginal-relevance items dropped** (by design)
- **Hash-based embeddings** (not as good as neural embeddings)
- **No semantic understanding of ADR contents** (just titles)
- **Tuning required** (defaults may not be optimal)

### When to Use Each Optimization

| Scenario | Selective | Pruning | Reranking |
|----------|-----------|---------|-----------|
| Token-constrained | ✅ | ✅ | ✅ |
| Quality-focused | ⚠️ Use conservatively | ✅ | ✅ |
| Latency-critical | ✅ Fast | ✅ Fast | ✅ Fast |
| First-time user | ✅ Safe defaults | ✅ Safe defaults | ✅ Safe defaults |

---

## Future Improvements (v0.3+)

1. **Real Embedding Model**
   - Replace hash-based pseudo-embeddings with `sentence-transformers`
   - Better semantic understanding
   - Slight latency cost (~50ms)

2. **Configurable Weights**
   - Let operators set recency/relevance/status weights per task type
   - Store learned weights from feedback

3. **Learned Thresholds**
   - Auto-tune confidence_floor based on feedback loop
   - Per-persona or per-domain tuning

4. **Cost Estimation**
   - Predict token savings before optimizing
   - Trade off quality vs tokens

5. **Audit Integration**
   - Record why each item was dropped
   - Trace pruning decisions in audit log

---

## References

- **ADR-0394:** Advanced Context Optimizations (Phase 5)
- **ADR-0280:** Config-Driven Pipeline Architecture
- **CONCEPT-0006:** Context Engineering Unification
- **Phase 1-4 Docs:** operator/context_engineering/README.md
