# ADR-0400: Graph-Native Task Execution Model

**Status:** PROPOSED  
**Date:** 2026-08-24  
**Depends On:** [ADR-0369 (Task Monitoring), ADR-0314 (Learning Infrastructure), ADR-0348 (Event Bus)]  
**Related To:** [CONCEPT-0012 (Graph-Native Monitoring)]  

**Paths:**
- `core/vibe_engineering/task_graph.py`
- `core/vibe_engineering/graph_events.py`
- `core/vibe_engineering/checkpoint_to_graph.py`
- `core/console/corvin_console/routes/task_graph_api.py`
- `core/console/corvin_console/web-next/src/components/TaskGraphViewer.tsx`
- `core/console/corvin_console/web-next/src/components/TaskGraphNodeDetail.tsx`
- `core/console/corvin_console/web-next/src/lib/taskGraphViz.ts`
- `core/console/corvin_console/web-next/src/hooks/useTaskGraph.ts`
- `core/console/corvin_console/web-next/src/pages/task-graph.tsx`
- `core/console/corvin_console/web-next/src/styles/TaskGraphViewer.css`

**Docs:**
- `docs/claude-ref/task-graph-architecture.md`
- `docs/implementation/task-graph-mvp-plan.md`

---

## Context

### Problem Statement

Large engineering tasks (16+ hours, 100+ iterations) have opaque execution patterns:

1. **Distributed State**: Task events scattered across SessionState, checkpoint files, event logs
2. **Manual Reconstruction**: Operators manually trace decisions → errors → recovery paths
3. **Missing Traceability**: "Why did the task split at iteration 42?" requires reading 50 log lines
4. **Limited Visualization**: Existing panels (Token Metrics, TreeOfThoughts) are outdated and don't show execution flow
5. **Scalability**: No visualization scales to 100+ nodes (decisions, errors, checkpoints)

### Business Driver

- **ADR-0369** identified need for unified task status reporting
- **Phase 3.1** shipped StatusSnapshot (bridge-agnostic status)
- **Phase 4** learning system needs decision-outcome traceability
- **Operator monitoring** must be efficient for 16-hour unattended tasks

### Architectural Context

CorvinOS already has:
- ✅ EventBus (Phase 0, ADR-0348) for async pub/sub
- ✅ Checkpoint system with recovery paths (Phase 1-2, ADR-0369)
- ✅ Context reduction (91% compression) with Tier classification (Phase 1-3)
- ✅ Decision tracking (strategies_tried, success_rate) in SessionState
- ⚠️ No unified execution graph (scattered across multiple systems)

---

## Decision

**Adopt Graph-Native Task Execution Model (Option B)**

Redesign Context Pipeline and checkpoint system to use unified directed acyclic graph (DAG) representing:

1. **Nodes**: Task stages, decisions, errors, checkpoints, context snapshots, metrics, subgoals
2. **Edges**: Dependencies (hard/soft), data flow, temporal sequence, recovery paths
3. **Persistence**: TaskGraph serialized alongside CheckpointState (backward compat)
4. **Events**: SessionLifecycleManager → LoopEngineer → ContextReducer emit graph events via EventBus
5. **Visualization**: Multi-layer DAG/Swimlane/Hierarchical views in Console

### Rationale

**Why Option B (Graph-Native) over Option A (Incremental):**

| Criterion | Option A | Option B |
|-----------|----------|----------|
| **Scatter** | Data in 5 places (State, Checkpoint, Logs, Cache, Metrics) | Single TaskGraph structure |
| **Query Complexity** | Manual traversal (logs + code) | Efficient graph queries (BFS, reachability) |
| **Visualization** | Impossible for 100+ nodes | Scales with progressive rendering |
| **Extensibility** | Phase 4 learning needs rework | Ready for Phase 4 from start |
| **Effort** | 2 weeks effort NOW | 10 days effort NOW, saves 3 weeks in Phase 4 |
| **Risk** | Low risk, high regret if scaled poorly | Higher upfront risk, zero regret at scale |

**Timeline Justification**: Phase 1-2 is the right time to redesign (before Phase 3-4 locks in patterns).

---

## Architecture

### Core Data Model

```python
@dataclass(frozen=True)
class TaskGraph:
    """Unified execution graph for a task."""
    task_id: str
    created_at: str
    
    # All nodes (decisions, errors, checkpoints, etc.)
    nodes: Dict[str, Node]
    
    # All edges (dependencies, data flow, temporal)
    edges: List[Edge]
    
    # Fast lookups
    nodes_by_type: Dict[str, List[str]]
    iterations: Dict[int, str]  # iteration_num → checkpoint_id

@dataclass(frozen=True)
class Node:
    """Any event in the execution graph."""
    id: str
    type: str  # "decision", "error", "checkpoint", "context", "metric", "subgoal"
    timestamp: str
    data: Dict[str, Any]  # Type-specific fields

@dataclass(frozen=True)
class Edge:
    """Relationship between nodes."""
    from_id: str
    to_id: str
    edge_type: str  # "hard_dependency", "soft_dependency", "data_flow", "temporal"
    label: str
    metadata: Dict[str, Any]
```

### Event Emission (Backward Compatible)

```python
# SessionLifecycleManager
def evaluate_triggers(state: SessionState) -> TriggerEvaluation:
    result = ...  # existing logic
    event_bus.emit("checkpoint_saved", {
        checkpoint_id, task_id, trigger, iteration_num
    })
    return result

# LoopEngineer
def on_decision_made(decision: Dict):
    event_bus.emit("decision_made", {
        decision_text, iteration, alternatives, outcome
    })

# ContextReducer
def reduce(goal, constraints, ...):
    reduced = ...  # existing logic
    event_bus.emit("context_reduced", {
        reduction_pct, tier_counts, ml_confidence
    })
    return reduced
```

### Graph Building (Converter Pattern)

```python
# At checkpoint save time
graph_builder = GraphBuilder()
graph_builder.add_node(Node(...))  # from event
graph_builder.infer_edges()  # hard/soft/temporal
graph = graph_builder.build()

# Serialize with checkpoint
checkpoint_state.graph = graph.to_json()

# On resume: restore graph from checkpoint
graph = TaskGraph.from_json(checkpoint_state.graph)
```

### Visualization Layer

- **Frontend**: React component + D3.js for DAG rendering
- **API**: `/api/tasks/{task_id}/graph` returns TaskGraph JSON
- **Query**: `/api/tasks/{task_id}/graph/query?type=reachability&node=X`

---

## Consequences

### Positive
✅ **Unified Execution Model**: All task events in one queryable structure  
✅ **Scalability**: Handles 100+ nodes without performance degradation  
✅ **Auditability**: Full trace of why → what → outcome  
✅ **Extensibility**: Plugin system can hook into graph events (Phase 4)  
✅ **Recovery Validation**: Can verify checkpoint is complete before resume  

### Trade-offs
⚠️ **Data Model Migration**: CheckpointState format changes (converter handles compat)  
⚠️ **Memory Overhead**: ~50KB per 100 nodes (acceptable for single-user)  
⚠️ **Visualization Latency**: Graph rendering must be async (avoid UI blocking)  

**Mitigations:**
- Backward compat converter (old checkpoints still work)
- Aggressive graph caching (compute once, serialize)
- Progressive rendering (show DAG, then drill-down details)

### Operational
- **Audit Trail**: Graph mutations logged via EventBus (audit chain)
- **Debugging**: Full execution trace available via `/graph` API
- **Monitoring**: Operators can validate task health via graph queries

---

## Implementation

### Phase 1: Core (Week 1)
- [ ] TaskGraph + Node + Edge dataclasses
- [ ] GraphBuilder with edge inference
- [ ] Event handlers (emit from SessionManager, LoopEngineer, ContextReducer)
- [ ] Serialization (to/from JSON)
- [ ] Unit tests: 30 tests

### Phase 2: Visualization (Week 2)
- [ ] DAG renderer (D3.js)
- [ ] Zoom + pan + drill-down
- [ ] API endpoints (`/graph`, `/graph/query`)
- [ ] E2E test: full task lifecycle → visualization
- [ ] Feature flag: `task_graph_visualization` (default: OFF)

### Phase 3: Polish (Week 3)
- [ ] Swimlane view
- [ ] Hierarchical view
- [ ] Export to SVG/DOT/Timeline
- [ ] Anomaly highlighting (errors, slow nodes)
- [ ] Performance profiling (latency, memory)

### Phase 4: Plugin Hookup (Week 4, after key-custody)
- [ ] Plugin system integration (learning plugin, monitoring plugin)
- [ ] Graph mutation API for plugins
- [ ] Audit trail for plugin-added edges

**MVP Scope:** Phase 1-2 (graph + basic visualization)

---

## Validation

### Assumptions
1. Graph generation latency < 100ms (per 100 nodes) — **verified in Phase 2**
2. Memory overhead acceptable (~50KB per 100 nodes) — **verified in Phase 2**
3. Operators can understand DAG visualization — **validated via usability testing**
4. Event emission doesn't degrade SessionLifecycleManager — **verified in Phase 1 integration**

### Risks
- **Risk**: Large tasks (200+ iterations) → graph too complex to visualize
  - **Mitigation**: Aggregate nodes by iteration range, allow histogram view
- **Risk**: Event emission overhead slows down task execution
  - **Mitigation**: Async event emission, non-blocking EventBus
- **Risk**: Backward compat converter bugs → corrupt checkpoints
  - **Mitigation**: Unit tests for converter (roundtrip), gradual rollout

---

## Compliance

✅ **Audit Trail** (GDPR Art. 30, 32): Graph mutations logged via EventBus  
✅ **Determinism** (ADR-0264): Same task → same graph (reproducible)  
✅ **Transparency** (EU AI Act Art. 50): Operator can inspect full execution flow  
✅ **Traceability** (ADR-0301 Learning): Decisions linked to outcomes  

---

## Questions for Review

1. **Event Latency**: Should EventBus be async or sync?
   - Current: Sync (simpler, ok for single-user)
   - Alternative: Async (future-proof for distributed)

2. **Graph Persistence**: Store full graph with every checkpoint or only on splits?
   - Current: Every checkpoint (redundant but queryable)
   - Alternative: Only on splits (storage-optimized)

3. **Plugin Extension**: Should graph allow plugin-added node types?
   - Current: Plugin system deferred (MVP has no new types)
   - Alternative: Design plugin extension points now (future-proof)

**Recommendation**: Sync EventBus for MVP, full graph on every checkpoint, plugin extension points (documented, not implemented).

---

**Author**: Claude Haiku 4.5  
**Reviewed By**: [pending]  
**Approved By**: [pending]  
**Implementation Start**: 2026-08-24 (Week 1)

---

## Amendment Log

(None yet — initial proposal)
