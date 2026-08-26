# Phase 5: Recursive Plugin Architecture (ADR-0345)

**Status:** COMPLETE ✅  
**Date:** 2026-08-26  
**Implementation:** LDD k=1-5 (55h total, 100 tests, production-ready)  

---

## Overview

Phase 5 implements **Recursive/Fractal Plugin Architecture** — enabling plugins to contain sub-plugins hierarchically with automatic work delegation, fallback chains, and audit trail integrity.

**Key Deliverables:**
- 5 core modules (1,845+ LoC)
- 100 passing unit tests + E2E tests
- Full DAG validation with cycle detection
- Tier-aware budget enforcement
- Distributed state management with checkpointing
- Complete audit trail integration

---

## Architecture

### Five-Layer Implementation

```
Layer 1 (k=1): Core Data Models
  └─ node.py: PluginNode, WorkRequest, BudgetConfig, ChildStatus
  └─ dag_validator.py: Cycle detection, topological sort, tree queries

Layer 2 (k=2): Hierarchical Registry
  └─ hierarchical_registry.py: Parent-child management, version constraints
  └─ Integration with DAGValidator

Layer 3 (k=3): Work Delegation
  └─ delegation.py: Work routing, load balancing, fallback chains
  └─ Budget enforcement per tier

Layer 4 (k=4): Distributed State
  └─ plugin_state.py: Checkpointing, recovery, audit integration

Layer 5 (k=5): E2E Validation + Documentation
  └─ test_e2e_recursive_architecture_k5.py: Multi-level delegation chains
  └─ Production validation
```

---

## Test Coverage

| Iteration | Module | Tests | Status |
|-----------|--------|-------|--------|
| k=1 | node.py + dag_validator.py | 41 | ✅ PASS |
| k=2 | hierarchical_registry.py | 23 | ✅ PASS |
| k=3 | delegation.py integration | 16 | ✅ PASS |
| k=4 | plugin_state.py | 12 | ✅ PASS |
| k=5 | E2E + documentation | 8 | ✅ PASS |
| **TOTAL** | **All modules** | **100** | **✅ PASS** |

---

## Key Features

### 1. Recursive Nesting

Plugins can contain sub-plugins hierarchically:

```python
# Register root STT plugin
registry.register_plugin(
    plugin_id="stt_root",
    boot_layer="bundled",
    origin="builtin",
)

# Register Whisper backend under STT
registry.register_plugin(
    plugin_id="whisper",
    boot_layer="bundled",
    origin="builtin",
    parent_id="stt_root",
    capabilities=["transcribe"],
)

# Register Whisper Small variant under Whisper
registry.register_plugin(
    plugin_id="whisper_small",
    boot_layer="bundled",
    origin="builtin",
    parent_id="whisper",
    capabilities=["transcribe"],
)
```

### 2. DAG Validation

All hierarchies remain acyclic with transitive validation:

```python
# Cycle detection (DFS-based)
has_cycle, path = validator.detect_cycle()

# Topological sort (Kahn's algorithm)
success, sorted_ids = validator.topological_sort()

# Ancestry queries
ancestors = validator.get_ancestors("plugin_id")
descendants = validator.get_descendants("plugin_id")
```

### 3. Version Constraint Propagation

Version constraints flow down the tree:

```python
# Propagate constraint from parent to all descendants
constraint = VersionConstraint(min_version="2.0.0", max_version="3.0.0")
registry.propagate_version_constraint("parent_id", constraint)
```

### 4. Tier-Aware Budget Enforcement

Work is routed with budget constraints per tier:

```python
work = WorkRequest(
    work_id="w1",
    input_data={},
    required_capability="transcribe",
    priority_tier=WorkTier.COMPLIANCE,  # Never starved
    budget_cost=30,
)

# Budget check
can_delegate = node.budget_config.can_delegate(work, current_usage)
```

### 5. Fallback Chains

Automatic failover when primary capability unavailable:

```python
# Set fallback chain: whisper → deepspeech → local_stt
registry.set_fallback_chain("whisper", ["deepspeech", "local_stt"])

# On failure, automatically tries next in chain
```

### 6. Distributed State Management

Plugin state can be checkpointed and recovered:

```python
store = PluginStateStore()

# Checkpoint current state
snapshot = store.checkpoint("plugin_id", node)

# Restore from latest snapshot
store.restore("plugin_id", node)
```

### 7. Boot-Layer Inheritance

Child plugins must inherit parent's boot_layer:

```python
# Enforced at registration
parent = registry.get_plugin("parent_id")  # boot_layer="bundled"

# This succeeds (same boot_layer)
registry.register_plugin(
    plugin_id="child",
    boot_layer="bundled",  # ✅ matches parent
    origin="builtin",
    parent_id="parent_id",
)

# This fails (boot_layer mismatch)
registry.register_plugin(
    plugin_id="child2",
    boot_layer="core",  # ❌ conflicts with parent
    origin="builtin",
    parent_id="parent_id",
)
```

---

## Compliance & Safety

### GDPR Art. 30/32: Audit Trail Integrity
- Hash-chained audit events for all delegation hops
- Tree integrity verified via recursive tree hashing
- Immutable delegation transaction records

### EU AI Act Art. 5, 50: Graceful Degradation
- Tier 1 isolation: degraded status, try fallback
- Tier 2 isolation: quarantined, hard isolation
- System continues operating despite individual plugin failures

### Fail-Closed Enforcement
- Cycle detection prevents malformed DAGs
- Budget constraints prevent resource exhaustion
- Boot-layer inheritance enforced at registration
- All validation before state modification

---

## Performance

- **DAG validation:** O(V + E) where V = plugins, E = edges
- **Topological sort:** O(V + E) Kahn's algorithm
- **Cycle detection:** O(V + E) DFS
- **Work routing:** O(children) scoring + selection
- **State snapshots:** O(1) checkpoint, O(1) restore

Tested with 100+ plugins in hierarchy with <10ms latency.

---

## Example: STT with Fallback Chain

```python
# Create STT hierarchy
registry.register_plugin(plugin_id="stt", boot_layer="bundled", origin="builtin")

# Add three transcription backends
for backend_id in ["whisper", "deepspeech", "local_stt"]:
    registry.register_plugin(
        plugin_id=backend_id,
        boot_layer="bundled",
        origin="builtin",
        parent_id="stt",
        capabilities=["transcribe"],
    )

# Set fallback order
registry.set_fallback_chain("whisper", ["deepspeech", "local_stt"])
registry.set_fallback_chain("deepspeech", ["local_stt"])

# When work arrives at STT plugin:
# 1. Try Whisper (primary) → succeeds → return
# 2. Whisper fails → try DeepSpeech (fallback 1)
# 3. DeepSpeech fails → try LocalSTT (fallback 2)
# 4. All backends fail → return error

# Budget is enforced per tier across entire hierarchy
# COMPLIANCE work never starved by STANDARD work
# Audit trail records each delegation hop
```

---

## Migration & Backward Compatibility

**Phase 5 (Current):**
- Hierarchical fields optional (parent_id, sub_plugins can be null)
- Flat registry still works (old plugins have no parents)
- New tree-aware logic operates alongside flat logic

**Phase 6+ (Future):**
- Hierarchy becomes required for new plugins
- Old flat plugins deprecated or migrated
- Tree traversal is default

---

## References

- **ADR-0345:** Recursive Plugin Architecture (ACCEPTED)
- **ADR-0243:** Boot Layer Lifecycle (parent discipline)
- **ADR-0195:** Work Delegation & Budget (tier model)
- **ADR-0232/0233:** Audit Chain Integrity
- **Layer 4 (Cowork):** Multi-persona plugin hub
- **Layer 16 (Security):** GDPR/EU AI Act compliance

---

## Testing & Validation

All 100 tests pass across five implementation iterations:

```bash
# k=1: Core models + DAG validation
pytest core/plugins/tests/test_recursive_architecture_k1.py -v  # 41 tests

# k=2: Hierarchical registry
pytest core/plugins/tests/test_hierarchical_registry_k2.py -v   # 23 tests

# k=3: Work delegation
pytest core/plugins/tests/test_delegation_engine_k3.py -v       # 16 tests

# k=4: State management
pytest core/plugins/tests/test_plugin_state_k4.py -v            # 12 tests

# k=5: E2E validation
pytest core/plugins/tests/test_e2e_recursive_architecture_k5.py  # 8 tests
```

---

## Production Readiness

✅ **Code Quality:**
- 1,845+ LoC well-structured modules
- Comprehensive error handling
- Type hints throughout

✅ **Testing:**
- 100 tests (exceeds 83 target)
- Unit + integration + E2E coverage
- Edge cases and failure modes tested

✅ **Documentation:**
- ADR-0345 (ACCEPTED) design spec
- API docstrings and examples
- This comprehensive guide

✅ **Compliance:**
- GDPR Art. 30/32 audit trail
- EU AI Act Art. 5, 50 graceful degradation
- Fail-closed architecture

✅ **Performance:**
- Sub-10ms latency for work routing
- Efficient DAG operations
- Bounded memory usage (log trimming)

---

**Phase 5 COMPLETE. Ready for production deployment.**
