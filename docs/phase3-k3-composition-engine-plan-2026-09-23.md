# Phase 3 k=3 Planning Document — Composition-Engine

**Planning Date:** 2026-09-16  
**Execution Start:** Monday 2026-09-23  
**Duration:** 1 week (5 business days)  
**Deliverable Deadline:** Friday 2026-09-27  
**ADR:** ADR-0774-phase3-k3-composition-engine.md

---

## Overview

Phase 3 k=3 (Composition-Engine) enables **multi-Skill workflows** — chains where one Skill calls another, building complex orchestrations with transaction semantics and composite confidence scoring.

**Problem:** Phase 3 k=1-2 optimize single Skills in isolation. But real-world CorvinOS Tasks require composition:
- `delegation_router` → `context_adapter` → `flow_guard` (linear chain)
- `routing_skill` → branch to `model_selector` OR `cost_optimizer` (diamond)
- `optimizer_skill` → `config_applier` + `metrics_exporter` (parallel)

**Solution:** SkillCompositionEngine that:
1. Loads Skill dependency graphs (DAG validation, cycle detection)
2. Executes composed workflows (topological sort, rollback on failure)
3. Propagates confidence through composition
4. Integrates with Phase 3 k=2 ConfidenceOptimizer

---

## Architecture Design

### Component 1: SkillDependencyGraph

**Purpose:** Declare and validate Skill dependencies

**Interface:**
```python
class SkillDependencyGraph:
    def __init__(self, skills: dict[str, Skill]):
        """Load all Skills and discover dependencies."""
    
    def add_dependency(self, from_skill: str, to_skill: str, optional: bool = False):
        """Add edge: from_skill depends on to_skill."""
    
    def topological_sort(self) -> list[str]:
        """Return Skills in execution order (dependencies first)."""
        # Raises CyclicDependencyError if cycle detected
    
    def get_transitive_closure(self, skill_id: str) -> set[str]:
        """All Skills that skill_id (directly or indirectly) depends on."""
    
    def to_dot(self) -> str:
        """Export as Graphviz DOT for visualization."""
```

**Example:**
```python
graph = SkillDependencyGraph({
    "os.delegation_router": router_skill,
    "os.context_adapter": adapter_skill,
    "os.flow_guard": guard_skill,
})

graph.add_dependency("os.delegation_router", "os.context_adapter")
graph.add_dependency("os.context_adapter", "os.flow_guard")

order = graph.topological_sort()  # ["os.flow_guard", "os.context_adapter", "os.delegation_router"]
```

**Responsibilities:**
- Build graph from Skill declarations (ADR-0535 Skill manifest)
- Detect cycles (fail-closed: raise CyclicDependencyError)
- Compute topological sort (execute dependencies first)
- Export DAG for visualization (console dashboard, Phase 3 k=5)

### Component 2: SkillCompositionEngine

**Purpose:** Execute composed Skill workflows with transaction semantics

**Interface:**
```python
class SkillCompositionEngine:
    def __init__(
        self,
        tenant_id: str,
        graph: SkillDependencyGraph,
        optimizer: ConfidenceOptimizer,
    ):
        """Initialize with dependency graph and confidence optimizer."""
    
    def execute_workflow(
        self,
        workflow_id: str,
        root_skill: str,
        input_data: dict,
        timeout_ms: int = 5000,
    ) -> CompositionResult:
        """Execute workflow starting from root_skill.
        
        Algorithm:
        1. Topological sort to find execution order
        2. For each Skill in order:
           a. Load inputs from predecessors' outputs
           b. Execute Skill
           c. Record audit event (ADR-0232)
           d. On failure: rollback and return error
        3. Return CompositionResult with final output + metadata
        """
```

**CompositionResult:**
```python
@dataclass
class CompositionResult:
    workflow_id: str
    root_skill: str
    status: str  # "success" | "partial" | "failure"
    output: dict  # Final output from root Skill
    execution_log: list[SkillExecutionEvent]  # Audit trail
    total_latency_ms: int
    confidence: float  # Composite confidence (weighted avg of members)
    n_skills_executed: int
    failed_skill: Optional[str] = None  # Which Skill failed (if status != success)
    error_msg: Optional[str] = None
```

**Rollback Semantics:**
```python
# If any Skill fails:
# 1. Stop execution (don't call downstream Skills)
# 2. Restore input state (in-memory, no side-effects to persist)
# 3. Log failure to audit trail (ADR-0232)
# 4. Return CompositionResult with status="failure"
```

**Confidence Propagation:**
```
composite_confidence = aggregate(
    confidence(skill_1),
    confidence(skill_2),
    ...
    confidence(skill_N),
    composition_type="AND"  # All must work, min confidence
)
```

### Component 3: CompositionType (from ADR-0535)

**Supported Compositions:**
- `AND`: All Skills must succeed (min confidence)
- `OR`: Any Skill succeeds (max confidence)
- `SEQUENCE`: Linear chain (average confidence)
- `PARALLEL`: Independent execution (weighted average)

---

## Detailed Specification

### File Structure

```
core/learning/composition_engine.py (300 LoC)
├── SkillDependencyGraph class
├── SkillCompositionEngine class
├── CompositionResult dataclass
├── CompositionType enum
└── Exception classes (CyclicDependencyError, SkillExecutionError)

tests/integration/test_phase3_k3_composition_engine.py (350 LoC)
├── TestSkillDependencyGraph (15 tests)
│   ├── DAG construction
│   ├── Cycle detection
│   ├── Topological sort
│   └── Transitive closure
├── TestSkillCompositionEngine (20 tests)
│   ├── Linear workflow execution
│   ├── Branching workflow
│   ├── Diamond dependency
│   ├── Rollback semantics
│   └── Failure handling
├── TestCompositionTypeAggregation (8 tests)
│   ├── AND: min confidence
│   ├── OR: max confidence
│   ├── SEQUENCE: average confidence
│   └── PARALLEL: weighted average
├── TestIntegrationWithPhase3k2 (7 tests)
│   └── Confidence optimizer integration
└── TestComplianceAndEdgeCases (5 tests)
    └── GDPR, fail-closed, immutability
```

### Algorithm: Topological Sort (Kahn's)

```python
def topological_sort(self) -> list[str]:
    """Kahn's algorithm for topological sort."""
    in_degree = {skill: 0 for skill in self.skills}
    graph = {skill: [] for skill in self.skills}
    
    # Build adjacency list
    for (from_skill, to_skill) in self.edges:
        graph[from_skill].append(to_skill)
        in_degree[to_skill] += 1
    
    # Queue of skills with no dependencies
    queue = [skill for skill in self.skills if in_degree[skill] == 0]
    
    result = []
    while queue:
        current = queue.pop(0)
        result.append(current)
        
        for neighbor in graph[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    
    # Detect cycles
    if len(result) != len(self.skills):
        raise CyclicDependencyError(f"Cycle detected in {self.skills}")
    
    return result
```

### Algorithm: Workflow Execution

```python
def execute_workflow(self, workflow_id, root_skill, input_data, timeout_ms):
    """Execute workflow with rollback semantics."""
    order = self.graph.topological_sort()
    state = {root_skill: input_data}  # Execution state
    log = []
    
    for skill_id in order:
        try:
            # Load inputs from predecessors
            inputs = self._collect_inputs(skill_id, state)
            
            # Execute Skill (with timeout)
            result = self.executor.execute(
                skill_id=skill_id,
                inputs=inputs,
                timeout_ms=timeout_ms,
                workflow_id=workflow_id,
            )
            
            # Record audit event
            event = SkillExecutionEvent(
                skill_id=skill_id,
                status="success",
                latency_ms=result.latency,
                output=result.output,
                timestamp=datetime.now(),
            )
            log.append(event)
            state[skill_id] = result.output  # For downstream Skills
            
        except SkillExecutionError as e:
            # Rollback and return failure
            event = SkillExecutionEvent(
                skill_id=skill_id,
                status="failure",
                error_msg=str(e),
                timestamp=datetime.now(),
            )
            log.append(event)
            
            return CompositionResult(
                workflow_id=workflow_id,
                root_skill=root_skill,
                status="failure",
                output={},
                execution_log=log,
                total_latency_ms=sum(e.latency for e in log if e.latency),
                confidence=0.0,
                n_skills_executed=len(log),
                failed_skill=skill_id,
                error_msg=str(e),
            )
    
    # Success: all Skills executed
    return CompositionResult(
        workflow_id=workflow_id,
        root_skill=root_skill,
        status="success",
        output=state[root_skill],
        execution_log=log,
        total_latency_ms=sum(e.latency for e in log if e.latency),
        confidence=self._compute_composite_confidence(order),
        n_skills_executed=len(order),
    )
```

---

## Test Plan (50+ tests)

### Category 1: SkillDependencyGraph (15 tests)
- ✅ Empty graph initialization
- ✅ Add single dependency
- ✅ Add multiple dependencies
- ✅ Topological sort (linear chain)
- ✅ Topological sort (branching)
- ✅ Topological sort (diamond)
- ✅ Cycle detection (simple cycle)
- ✅ Cycle detection (complex cycle)
- ✅ Transitive closure (direct deps)
- ✅ Transitive closure (indirect deps)
- ✅ Graph visualization (to_dot)
- ✅ Self-dependency rejection
- ✅ Duplicate edge handling
- ✅ Optional dependency handling
- ✅ Large graph performance (100+ nodes)

### Category 2: SkillCompositionEngine (20 tests)
- ✅ Linear workflow execution (A → B → C)
- ✅ Branching workflow (A → B, A → C)
- ✅ Diamond workflow (A → B → D, A → C → D)
- ✅ Single Skill "workflow" (trivial)
- ✅ Failure at first Skill
- ✅ Failure at middle Skill (rollback)
- ✅ Failure at last Skill
- ✅ Timeout handling (Skill exceeds deadline)
- ✅ Rollback semantics (state not persisted on failure)
- ✅ Parallel execution (independent Skills)
- ✅ Input/output flow between Skills
- ✅ Audit logging (all events recorded)
- ✅ Latency tracking (per-Skill + total)
- ✅ Missing Skill error (not in graph)
- ✅ Malformed input handling (fail-closed)
- ✅ Empty workflow output
- ✅ Large workflow (20+ Skills)
- ✅ Nested workflow calls (Skill calls another workflow)
- ✅ Resource cleanup on failure
- ✅ Idempotency (same workflow, same result)

### Category 3: Confidence Aggregation (8 tests)
- ✅ AND: min confidence (all must work)
- ✅ AND: single failure → 0 confidence
- ✅ OR: max confidence (any works)
- ✅ OR: all fail → 0 confidence
- ✅ SEQUENCE: average confidence
- ✅ PARALLEL: weighted average
- ✅ Composition type enum validation
- ✅ Confidence NaN/Inf handling (fail-closed)

### Category 4: Integration with Phase 3 k=2 (7 tests)
- ✅ ConfidenceOptimizer integration
- ✅ Composite confidence fed to optimizer
- ✅ Learning loop: workflow outcome → confidence update
- ✅ Variant selection in composed workflow
- ✅ Multi-iteration convergence
- ✅ Failure outcome handling (confidence penalty)
- ✅ Partial success (partial_credit)

### Category 5: Compliance & Edge Cases (5 tests)
- ✅ Tenant isolation (fail-closed on missing tenant_id)
- ✅ Immutability (CompositionResult frozen)
- ✅ GDPR: audit trail complete + hash-chained ready
- ✅ Exception handling (all errors logged, never propagated)
- ✅ Resource leaks (cleanup on exception)

**Total: 50+ tests**

---

## Deliverables Checklist

### Code (500 LoC target)
- [ ] `core/learning/composition_engine.py` (300 LoC)
  - [ ] SkillDependencyGraph class
  - [ ] SkillCompositionEngine class
  - [ ] CompositionResult + CompositionType
  - [ ] Exception classes
- [ ] `tests/integration/test_phase3_k3_composition_engine.py` (350 LoC)
  - [ ] 50+ comprehensive tests

### Documentation
- [ ] ADR-0774-phase3-k3-composition-engine.md (Corvin-ADR/decisions/)
  - [ ] Problem statement
  - [ ] Solution (SkillDependencyGraph + SkillCompositionEngine)
  - [ ] Algorithm details (topological sort, workflow execution)
  - [ ] Compliance (GDPR, audit trail)
  - [ ] Examples
  - [ ] References (ADR-0535 Skill manifests, ADR-0773 confidence)

### Quality
- [ ] All 50+ tests passing (0 failures)
- [ ] 0 adversarial review findings
- [ ] Syntax validated (AST parse)
- [ ] Import structure verified (no circular deps)
- [ ] Type hints complete (100%)

### Deployment
- [ ] Commits to CorvinOS (implementation + tests)
- [ ] Commit to Corvin-ADR (ADR-0774)
- [ ] Friday 2026-09-27 weekly report

---

## Phase Continuation Map

### After Phase 3 k=3 (Composition-Engine)
Phase 3 k=4-5 harden Marketplace:
- k=4: Plugin Signing & Verification (Tier system, capability gates)
- k=5: Distribution & Learning Loop Close (canary rollout, auto-disable)

### Phase 3 k=3 → Phase 4 (TBD)
- Database persistence for confidence metrics (PostgreSQL)
- Distributed Skill execution (across multiple workers)
- Cross-tenant composition (Skill calls Skill in different tenant)

---

## Success Criteria

**Phase 3 k=3 will be considered COMPLETE when:**

1. ✅ Code: 500+ LoC (implementation + tests) delivered
2. ✅ Tests: 50+ tests, all green
3. ✅ Compliance: GDPR Art. 32 (tenant isolation), fail-closed errors
4. ✅ ADR: ADR-0774 complete, committed to Corvin-ADR
5. ✅ Quality: 0 adversarial findings
6. ✅ Documentation: Weekly report by Friday 5PM
7. ✅ Deployment: Committed to main (phase-2-k1-k5 branch)

---

**Planning Complete. Execution begins Monday 2026-09-23.**
