# CONCEPT-0012: Graph-Native Task Execution Monitoring

**Status:** PROPOSED  
**Date:** 2026-08-24  
**Type:** Architecture Pattern (Learned Experience)  
**Scope:** Project (CorvinOS Vibe Engineering)  
**Skills:** [[celery_graph_visualization]], [[task_lifecycle_monitoring]]

---

## Problem

Large engineering tasks (16+ hours, 100+ iterations) are opaque during execution. Operators need to understand:
- Why a task split at iteration 42?
- Which decision led to the error?
- Can this checkpoint recover?
- Which parts succeeded / failed?

Current approach: scattered data across SessionState, checkpoint files, logs. Reconstructing execution flow is manual and error-prone.

## Method

**Graph-Native Task Execution Model:**

1. **Unified Graph Representation:**
   - All task events (decisions, errors, checkpoints, metrics) become graph nodes
   - Dependencies (hard/soft), data flow, temporal sequence become edges
   - Single source of truth for task execution

2. **Event-Driven Graph Building:**
   - SessionLifecycleManager emits: checkpoint_saved
   - LoopEngineer emits: decision_made
   - ContextReducer emits: context_reduced
   - RecoveryEngine emits: recovery_started
   - Events → graph mutations (add nodes, infer edges)

3. **Multi-Layer Visualization:**
   - DAG view: Full dependency graph (reachability, critical path)
   - Swimlane view: Stages + iterations in parallel
   - Hierarchical view: Task → Stages → Decisions → Errors
   - Focus: allow zoom/drill-down without cognitive overload

4. **Backward Compatibility:**
   - Legacy CheckpointState → TaskGraph conversion
   - Existing checkpoint files still work
   - Gradual migration path (no forced rework)

5. **Query API:**
   - Reachability: which decisions led to this error?
   - Critical path: slowest sequence of decisions
   - Impact analysis: if we redo decision X, what else changes?

## Alternatives Considered

**A. Incremental Extension (add fields to SessionState)**
- Pros: Minimal disruption, low effort
- Cons: Graph data scattered, hard to query, inefficient reconstruction
- **Rejected:** Would require rework in Phase 4 anyway

**B. Graph-Native Redesign (unified graph structure)**
- Pros: Single source of truth, efficient queries, enables visualization
- Cons: Data model change, migration needed
- **Selected:** Right time (Phase 1-2), enables key monitoring feature

**C. External Graph Store (separate DB for graphs)**
- Pros: Unlimited scale, full analytics
- Cons: Added infrastructure, operational complexity, network latency
- **Rejected:** Overkill for single-user, single-task scope; CorvinOS keeps state local

## Evidence

- **Phase 1 ADR-0369:** Task graph visualization needed for 16-hour task monitoring
- **Memory:** Graph-native design enables Phase 3.1+ features (StatusSnapshot dashboard, learning insights)
- **Feasibility:** MVP in 10 days (Phase 1-2), plugin extension in Week 4

## Consequences

### Positive
✅ Real-time task monitoring (zoom from full DAG to single decision)
✅ Execution flow reconstruction (why did X happen?)
✅ Pattern detection (which decision types succeed most?)
✅ Recovery path validation (can we restore from this checkpoint?)

### Negative / Trade-offs
⚠️ Data model migration (old checkpoints need conversion)
⚠️ Additional memory per task (graph nodes + edges)
⚠️ Visualization latency (graph rendering with 100+ nodes)

**Mitigation:**
- Conversion is transparent (backward compat layer)
- Memory: ~50KB per 100 nodes (acceptable)
- Latency: aggressive caching + progressive rendering

### Neutral
- Depends on plugin system hookup (deferred by key-custody blockade, not blocking MVP)
- Audit trail integration (audit events already exist, graph adds query layer)

## When NOT to Use

- Small tasks (<10 iterations): overhead not justified, simple logs sufficient
- Real-time data (sub-100ms): polling may be insufficient, needs event streaming (Phase 2)
- Multi-task coordination: graph is per-task, not cross-task
- Machine learning model training: specialized graphs may be better (future phase)

## Related Concepts

- [[celery_task_graph_architecture]] — Low-level graph data structures
- [[checkpoint_idempotency_and_recovery]] — Task state persistence layer
- [[context_pipeline_v2_ml_classification]] — Context reduction feeds into graph
- [[phase3_unified_context_bridge]] — Graph bridges multiple subsystems

## Operator Notes

(None yet — first proposal)

---

**Learnings from this concept:**
1. Graph-native is early enough in Phase 1-2 to migrate (wait until Phase 4 = painful rework)
2. Visualization drives architecture (monitoring need → data model design)
3. Event-driven graph building avoids tight coupling (SessionManager doesn't know about graph internals)

**Next iteration:** Implement MVP and measure graph generation latency + memory usage; refine data model based on real task execution patterns.
