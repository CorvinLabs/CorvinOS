---
id: ADR-0402
status: ACCEPTED
depends_on: [ADR-0363, ADR-0401]
relates_to: [ADR-0362, ADR-0348]
paths:
  - core/vibe_engineering/task_orchestrator.py
  - core/vibe_engineering/task_registry.py
  - core/vibe_engineering/orchestration_events.py
docs:
  - docs/architecture/task-orchestration-dag.md
---

# ADR-0402: Autonomous Task-Orchestration Engine

## Problem

Multiphase tasks require manual session management. Corvin cannot autonomously continue work across token budget limits.

## Solution

Three-subsystem architecture:
1. **TaskRegistry:** Persistent JSONL store of tasks + phase metadata (tenant-isolated, ACID-safe)
2. **TaskOrchestrator:** Stateless DAG executor (topological sort, concurrent phases, retry logic)
3. **OrchestrationEventBridge:** Routes events to NotificationRouter + VoiceCoordinator

## Implementation

- `TaskMetadata` + `PhaseMetadata` (frozen, immutable snapshots)
- `TaskRegistryPersistence` (append-only JSONL, async lock-serialized writes)
- `TaskOrchestrator` (DAG solver, asyncio.gather for parallelism, exponential backoff retry)
- Event emission on phase.completed, phase.failed, task.completed

## Trade-offs

✅ Stateless executor scales, FIFO append log avoids conflicts
❌ Phase ordering guarantees depend on Registry consistency

## Verification

Week 3-4: 15+ E2E tests verify DAG execution, concurrent phases, retry logic, cross-session resume
