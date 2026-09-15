# RAG Query Engine & Orchestrator

**Phase 3 of ADR-0089: RAG Integration System**

This module provides production-grade multi-provider RAG (Retrieval-Augmented Generation) query execution with advanced resilience, caching, and ranking.

---

## Components

### 1. RAG Query Engine (`rag_query_engine.py`)

**Purpose:** Execute queries against a single RAG provider with fault tolerance.

**Key Classes:**

- `RAGQuery` — User's retrieval request with validation
  ```python
  query = RAGQuery(
      query="What is RAG?",
      limit=5,
      timeout_ms=5000,
      metadata_filters={"source": "docs"},
      preferred_providers=["elasticsearch"],
  )
  ```

- `RAGResultItem` — Single result with normalized scoring
  ```python
  item = RAGResultItem(
      content="RAG augments LLMs with external knowledge...",
      score=0.92,  # Normalized to [0.0, 1.0]
      metadata={"source": "docs", "date": "2024-01"},
      source_url="https://example.com/doc",
  )
  ```

- `RAGProviderResult` — Results from one provider
  ```python
  result = RAGProviderResult(
      provider_id="elasticsearch-docs",
      status=QueryStatus.SUCCESS,
      items=[...],
      latency_ms=145,
  )
  ```

- `CircuitBreaker` — Fail-safe pattern
  ```
  CLOSED (normal operation)
    ↓ (after N failures)
  OPEN (rejecting requests)
    ↓ (after timeout)
  HALF_OPEN (testing recovery)
    ↓ (success → CLOSED, failure → OPEN)
  ```

- `RAGQueryEngine` — Core execution
  ```python
  engine = RAGQueryEngine(
      provider_id="elasticsearch-docs",
      manifest=manifest_dict,
      auth_tokens={"ES_TOKEN": "token123"},
  )
  result = await engine.execute(query)
  ```

**Error Handling:**

| Status | Trigger | Recovery |
|--------|---------|----------|
| `SUCCESS` | Response 200, valid JSON | Continue |
| `TIMEOUT` | `asyncio.TimeoutError` | Circuit breaker recorded |
| `AUTH_ERROR` | HTTP 401 | Circuit breaker recorded |
| `MALFORMED_RESPONSE` | Invalid JSON | Circuit breaker recorded |
| `PROVIDER_ERROR` | HTTP 5xx, network | Circuit breaker recorded |
| `UNKNOWN_ERROR` | Unexpected exception | Circuit breaker recorded |

**Circuit Breaker Behavior:**

```
CLOSED state:
  ✓ Requests execute normally
  ✓ Failures counted
  ✓ After N failures → OPEN

OPEN state:
  ✗ Requests rejected immediately (<1ms)
  ✓ Error: "Circuit breaker OPEN"
  ✓ After timeout (30s) → HALF_OPEN

HALF_OPEN state:
  ✓ Single request allowed (test recovery)
  ✓ Success → CLOSED (reset failures)
  ✓ Failure → OPEN (restart timer)
```

---

### 2. RAG Orchestrator (`rag_orchestrator.py`)

**Purpose:** Coordinate queries across multiple providers with intelligent ranking and caching.

**Key Classes:**

- `RAGCache` — TTL-based in-memory result cache
  ```python
  cache = RAGCache(cache_dir=None)  # Memory-only
  cache.set(query, items, ttl_seconds=300)
  cached = cache.get(query)  # Returns None if expired
  cache.clear()
  ```

- `RankedResult` — Result with provider weight for ranking
  ```python
  ranked = RankedResult(
      item=result_item,
      provider_id="elasticsearch-docs",
      provider_score_weight=1.0,  # Customizable per provider
  )
  final_score = ranked.final_score  # item.score * weight
  ```

- `RAGOrchestrator` — Multi-provider orchestration
  ```python
  orch = RAGOrchestrator(
      registry_dir=Path("~/.corvin/tenants/_default/global/rag"),
      auth_tokens={"ES_TOKEN": "token", "VECTOR_KEY": "key"},
      cache_ttl_seconds=300,
  )
  await orch.initialize()  # Load providers from registry
  results = await orch.query(query)  # Execute + rank + cache
  health = await orch.health_check_all()  # Provider status
  ```

**Orchestration Flow:**

```
Input: RAGQuery
  ↓
[Cache Check]
  → If valid & not expired: return cached results
  ↓
[Provider Selection]
  → Use preferred_providers OR all active providers
  → Filter out OPEN circuit breakers
  ↓
[Parallel Execution]
  → asyncio.gather() across selected providers
  → RAGQueryEngine.execute() for each
  ↓
[Ranking & Deduplication]
  → Skip failed providers
  → Deduplicate by MD5(content)
  → Sort by (score * provider_weight) descending
  ↓
[Caching]
  → Store top-N results with TTL
  ↓
Output: Ranked, deduplicated results
```

**Provider Selection:**

```python
# Preferred providers take priority
query = RAGQuery(
    query="test",
    preferred_providers=["elasticsearch-docs"],
)
# Uses only elasticsearch-docs if available & circuit closed

# Otherwise, all active providers
query = RAGQuery(query="test")
# Uses all providers with circuit state == CLOSED
```

**Deduplication Strategy:**

```
Provider 1: "Content A" (0.95), "Shared" (0.85)
Provider 2: "Content B" (0.92), "Shared" (0.88)

After dedup: "Content A" (0.95), "Shared" (0.85), "Content B" (0.92)
             (keeps first occurrence, deduplicates by MD5)
```

---

## Usage Examples

### Single Provider Query

```python
from operator.bridges.shared.rag_query_engine import (
    RAGQueryEngine,
    RAGQuery,
    QueryStatus,
)
import yaml

# Load manifest
with open("elasticsearch-docs.yaml") as f:
    manifest = yaml.safe_load(f)

# Create engine
engine = RAGQueryEngine(
    provider_id="elasticsearch-docs",
    manifest=manifest,
    auth_tokens={"ES_TOKEN": os.getenv("ELASTICSEARCH_TOKEN")},
)

# Execute query
query = RAGQuery(query="How does RAG work?", limit=5)
result = await engine.execute(query)

if result.status == QueryStatus.SUCCESS:
    for item in result.items:
        print(f"[{item.score:.2f}] {item.content}")
else:
    print(f"Error: {result.error_message}")
```

### Multi-Provider Orchestration

```python
from operator.bridges.shared.rag_orchestrator import RAGOrchestrator

# Initialize
orch = RAGOrchestrator(
    registry_dir=Path.home() / ".corvin" / "tenants" / "_default" / "global" / "rag",
    auth_tokens={"ES_TOKEN": "...", "VECTOR_KEY": "..."},
)
await orch.initialize()

# Query all providers
query = RAGQuery(
    query="Best practices for RAG systems",
    limit=10,
)
results = await orch.query(query)

# Results are ranked, deduplicated, and cached
for item in results:
    print(f"[{item.score:.2f}] {item.content}")

# Check provider health
health = await orch.health_check_all()
for pid, status in health.items():
    print(f"{pid}: {status['circuit_state']}")

# Cleanup
await orch.close()
```

---

## Configuration

### Manifest Fields Used by Phase 3

```yaml
spec:
  retrieval:
    endpoint: "https://..."              # Target URL
    method: "POST"                       # GET or POST
    timeout_ms: 5000                     # Per-request timeout
    auth:
      type: "bearer-token"               # or "api-key" or "none"
      token_env_var: "ES_TOKEN"          # Environment variable name

  resilience:
    circuit_breaker:
      failure_threshold: 5               # Open after N failures
      timeout_seconds: 30                # Recovery timeout
```

### Orchestrator Configuration

```python
orch = RAGOrchestrator(
    registry_dir=Path(...),              # Where manifests are stored
    auth_tokens={...},                   # env-var → actual token mapping
    cache_ttl_seconds=300,               # Cache expiration (5 min default)
)
```

---

## Performance

| Scenario | Latency | Notes |
|----------|---------|-------|
| **Cache hit** | ~50ms | In-memory lookup + return |
| **Single provider (fresh)** | 200–500ms | Network + transformation |
| **Multi-provider (3) parallel** | 300–800ms | Wall-clock of slowest |
| **Circuit open rejection** | <1ms | Instant fail-safe |
| **Deduplication** | O(n) | Hash comparison |

### Scaling

- **Providers:** Unlimited (parallel execution)
- **Results per provider:** Configurable (query.limit)
- **Cache memory:** Grows with unique queries (no size limit)
- **TTL:** Configurable per query

---

## Error Handling & Resilience

### Circuit Breaker Examples

```python
engine = RAGQueryEngine(...)

# Normal operation
assert engine.circuit_breaker.state == CircuitState.CLOSED

# Simulate failures
for _ in range(5):
    engine.circuit_breaker.record_failure()

# Circuit opens
assert engine.circuit_breaker.state == CircuitState.OPEN
assert engine.circuit_breaker.can_execute() is False

# After timeout, goes HALF_OPEN
# (test code manually sets last_failure_time in past for testing)
engine.circuit_breaker.last_failure_time = 0  # Way in the past
assert engine.circuit_breaker.can_execute() is True
assert engine.circuit_breaker.state == CircuitState.HALF_OPEN

# Success resets
engine.circuit_breaker.record_success()
assert engine.circuit_breaker.state == CircuitState.CLOSED
```

### Timeout Handling

```python
query = RAGQuery(query="test", timeout_ms=2000)
result = await engine.execute(query)

if result.status == QueryStatus.TIMEOUT:
    print(f"Query timed out after {result.latency_ms}ms")
```

---

## Testing

### Run Tests

```bash
# All Phase 3 tests
pytest operator/rag-integration/tests/test_rag_*.py -v

# Specific test
pytest operator/rag-integration/tests/test_rag_query_engine.py::TestCircuitBreaker -v

# With coverage
pytest operator/rag-integration/tests/ --cov=operator.bridges.shared -v
```

### Test Coverage

- ✅ 60+ tests (unit, integration, E2E)
- ✅ Circuit breaker state machine
- ✅ Timeout handling
- ✅ Multi-provider ranking
- ✅ Content deduplication
- ✅ Cache expiration
- ✅ Error categorization

---

## Security & Compliance

### Auth Token Handling

- ✅ Tokens injected from environment variables (never hardcoded)
- ✅ `token_env_var` field defines which env var to read
- ✅ Registry stores env-var name, not the token value

### Data Classification (Phase 5)

- ✅ Ready for L34 pre-query gate integration
- ✅ Manifest includes `dataClassification` field
- ✅ Can be extended with `allowed_engines` filtering

### GDPR Art. 17 Erasure (Phase 5)

- ✅ Manifest includes `erasureHandler` configuration
- ✅ Will integrate with corvin-erasure command

---

## Integration with CorvinOS

### With Phase 2 (Registry + CLI)

- Orchestrator loads manifests from `registry/manifests/`
- Health checks update `registry.json`
- Query stats aggregated in `provider_entry.query_stats`

### With Phase 1 (Manifest Validation)

- All manifests validated before Phase 2 registration
- Orchestrator respects manifest resilience config

### With Future Phases

- **Phase 4:** Expose as `/api/rag/query` endpoint
- **Phase 5:** Add L34/L36 gates before query execution
- **Phase 6:** Example manifests for popular backends

---

## Troubleshooting

### Circuit Breaker Opens Too Quickly

```python
# Reduce failure threshold
engine.circuit_breaker.failure_threshold = 10

# Or increase recovery timeout
engine.circuit_breaker.timeout_seconds = 60
```

### Slow Queries

```python
# Check latency per provider
result = await engine.execute(query)
print(f"Latency: {result.latency_ms}ms")

# Reduce timeout if network is unreliable
query = RAGQuery(query="...", timeout_ms=3000)
```

### Cache Not Being Used

```python
# Verify cache initialization
cache = orch.cache
print(cache.memory_cache)  # Should have entries after first query

# Check TTL
orch.cache_ttl_seconds = 600  # Increase to 10 min
```

---

## Future Enhancements

- Redis-backed distributed caching
- Per-provider result filtering
- Weighted scoring per provider SLA
- Request rate limiting per provider
- Result compression for large payloads

---

**For more information, see:**
- `PHASE_3_COMPLETE.md` — Detailed implementation guide
- `operator/rag-integration/README.md` — Full ADR context
- `Corvin-ADR/decisions/0089-*.md` — Formal specification
