# Layer 22 — Task Engine M2: Background Task Execution (ADR-0081, ADR-0082)

> **Messenger notify-on-completion:** the worker's terminal branches call
> `_notify_task_done()` → `completion_notify.mark_done()`, so a task whose
> producer registered a messenger origin gets a delivered Discord/WhatsApp/…
> notification when it finishes. See
> [background-completion-notify.md](background-completion-notify.md).


**Status (0.9.0):** Backend modules present (`task_queue.py`, `task_worker_pool.py`,
`task_pubsub.py`, `routes/tasks.py`) and unit-tested, but **NOT wired into the
console**: `routes/tasks.py` is never registered in `app.py`, the
`TaskWorkerPool` daemon is never started, and the frontend never calls the
tenant-global task API. The shipping console chat path uses the M1
session-scoped `TaskManager` (per-turn event log + streamed tokens) only.
Wiring M2 into the console is deferred — see the "Wiring status" note below.

## Overview

Task Engine M2 decouples task execution from WebSocket sessions, enabling:
- **Independent execution:** Tasks run to completion even if user switches chats
- **Cross-session visibility:** All running tasks visible across chat windows
- **Offline resilience:** Page reload recovers task state from local cache
- **Real-time progress:** WebSocket pub/sub for instant cross-session updates

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND (Browser)                                              │
├─────────────────────────────────────────────────────────────────┤
│  useTaskProgress()          (WebSocket pub/sub)                 │
│  useTaskPolling()           (HTTP fallback)                     │
│  useTaskIDBSync()           (IndexedDB write-on-event)         │
│  TaskStatusBarM2            (Real-time task visibility)         │
│  task-recovery.ts           (Page-reload hydration)             │
└─────────────────────────────────────────────────────────────────┘
                            ↕ (HTTP + WebSocket)
┌─────────────────────────────────────────────────────────────────┐
│ BACKEND (FastAPI + Worker Daemon)                               │
├─────────────────────────────────────────────────────────────────┤
│  routes/tasks.py            (4 tenant-global endpoints)         │
│  TaskQueue                  (Persistent append-only log)        │
│  TaskWorkerPool             (Background subprocess executor)     │
│  TaskPubSub                 (In-memory per-tenant broadcast)    │
│  TaskManager                (M1 compat: session-scoped events)  │
└─────────────────────────────────────────────────────────────────┘
```

## Backend Components

### TaskQueue (task_queue.py)

**Persistent append-only queue at `tenants/<tid>/global/tasks_<tid>.jsonl`**

```python
queue = TaskQueue(tenant_global_dir)

# Create task
task_id = queue.enqueue(
    tenant_id="_default",
    chat_key="web:session_1",
    instruction="Hello, world!",
    ttl_seconds=3600,
)

# Dequeue (FIFO, marks as RUNNING)
task = queue.dequeue("_default")  # Next PENDING task, skips expired

# Update status
queue.update_status(task_id, TaskStatus.COMPLETED, exit_code=0)

# List tasks
tasks = queue.list_tenant_tasks("_default", status=TaskStatus.RUNNING)
```

**Key Features:**
- FIFO + TTL expiry enforcement
- Multi-tenant isolation (per-tenant log file)
- Idempotent log replay (state derived from events)
- No locks needed (append-only, atomic writes via fsync)

### TaskWorkerPool (task_worker_pool.py)

**Independent background worker daemon**

```python
pool = TaskWorkerPool(queue, taskmanager_factory=..., max_workers=5)
await pool.run()  # Main loop: poll queue, spawn workers
```

**Lifecycle:**
1. Poll `task_queue.dequeue()` every 100ms
2. Spawn subprocess: `claude -p --output-format stream-json`
3. Record events to **both**:
   - TaskManager (session-scoped, for M1 SSE compatibility)
   - TaskPubSub (broadcast to all subscribers)
4. Background cleanup: mark expired tasks as FAILED every 60s

**Configurable:**
- `CORVIN_TASK_MAX_WORKERS` (default 5)
- `poll_interval_ms` (default 100)

### TaskPubSub (task_pubsub.py)

**In-memory pub/sub for real-time task progress**

```python
pubsub = get_pubsub()

# Subscribe to all tenant task events
async for event in pubsub.subscribe("_default"):
    print(f"Task {event['task_id']}: {event['event']}")

# Publish event (called by worker pool)
await pubsub.publish("_default", task_id, {"event": "progress", "pct": 50})
```

**Properties:**
- Per-tenant isolated channels
- asyncio.Queue based (bounded, backpressure handling)
- Auto-cleanup slow subscribers (logging only)
- Event schema: `{task_id, event, seq, timestamp, ...}`

### HTTP Endpoints (routes/tasks.py)

**Tenant-global task API (separate from session-scoped chat.py endpoints)**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/console/tasks` | Create task |
| GET | `/v1/console/tasks/{task_id}` | Get task (cross-session) |
| POST | `/v1/console/tasks/{task_id}/abort` | Cancel task |
| WS | `/v1/console/tasks/progress` | Subscribe to all task events |

**Example: Create task**
```
POST /v1/console/tasks
{
  "chat_key": "web:session_1",
  "instruction": "...",
  "ttl_seconds": 3600
}

Response:
{
  "ok": true,
  "task_id": "uuid"
}
```

**Example: Get task status**
```
GET /v1/console/tasks/<task_id>

Response:
{
  "task_id": "uuid",
  "chat_key": "web:session_1",
  "status": "running",
  "created_at": 1717317600,
  "started_at": 1717317601,
  "progress_pct": 45
}
```

## Frontend Components

### useTaskProgress()

**Real-time cross-session task visibility via WebSocket**

```typescript
const { tasks, isConnected } = useTaskProgress();

// tasks = [
//   {task_id: "...", chat_key: "...", status: "running", progress_pct: 45},
//   ...
// ]
```

### useTaskPolling()

**HTTP polling fallback (when WebSocket unavailable)**

```typescript
const { events, isPolling, error } = useTaskPolling({
  taskId: "...",
  enabled: sseFailedOrUnavailable,
  pollIntervalMs: 5000,
});

// Adaptive backoff: 5s → 30s if no new events
// Etag caching for efficiency
```

### useTaskIDBSync()

**IndexedDB write-on-event with batching**

```typescript
const { cachedEvents, writeEvent } = useTaskIDBSync({
  taskId: "...",
  onEvent: (event) => {
    setEvents(prev => [...prev, event]);
  },
});

// Intercept SSE/pub/sub events
writeEvent(event);  // Batches + flushes to IDB every 500ms
```

### task-recovery.ts

**Page-reload recovery**

```typescript
const { tasks, recovered, failed } = await recoverTaskState();
// Hydrates from IDB, verifies via HTTP, re-subscribes to pub/sub
```

## Frontend Components (ADR-0082)

### task-db.ts — IndexedDB Schema

**Database:** `corvin_tasks` v2  
**ObjectStore:** `tasks` (keyPath: `task_id`)

```typescript
interface Task {
  task_id: string;           // UUID
  chat_key: string;          // e.g., "web:session_1"
  persona: string;           // Active persona name
  instruction: string;       // User input / prompt
  status: "pending" | "running" | "completed" | "failed";
  created_at: number;        // Timestamp (ms)
  started_at: number | null;
  completed_at: number | null;
  progress_pct: number;      // 0–100
  latest_line: string;       // Last output line
  result: string;            // Full output
  error: string | null;
  last_synced_at: number;    // Last backend sync
  synced: boolean;           // Is synced with backend?
  etag: string | null;       // For polling Etag caching
}
```

**Indexes:**
- `chat_key` (non-unique) — query tasks for current chat
- `status` (non-unique) — filter by state
- `created_at` (non-unique) — sort by creation time
- `last_synced_at` (non-unique) — cleanup old tasks

**CRUD Operations:**
```typescript
saveTask(task)               // Create/update (put)
getTask(task_id)             // Read single
getAllTasksForChat(chat_key) // Read by chat
deleteTask(task_id)          // Delete
cleanupOldTasks(ttlMs)       // Delete > ttlMs old
```

### task-polling.ts — HTTP Fallback

**Purpose:** Fallback when WebSocket unavailable (browser offline, network issue)

**Polling interval:** 3 seconds (configurable)  
**Etag caching:** Only fetch if backend changed (HTTP 304)

```typescript
class TaskPoller {
  start(): void;     // Begin polling loop
  stop(): void;      // Stop polling
  private pollOnce() // Single HTTP GET + update IDB
}
```

**Flow:**
1. `GET /v1/console/chat/{chat_id}/tasks?etag={lastEtag}`
2. If 304: Skip (no change)
3. If 200: Parse tasks, save to IDB, call `onTaskUpdate()`
4. Repeat every 3s

### task-lifecycle.ts — Cleanup + Export/Import

**Phase 3: Automatic cleanup**
```typescript
startTaskCleanupSchedule(ttlMs = 30 days)  // Run daily
// Deletes tasks > ttlMs old from IDB
```

**Phase 4: Export/Import**
```typescript
exportTask(taskId)          // → JSON string
exportAllTasks()            // → JSONL (one per line)
importTaskFromJSON(json)    // ← Single task
importTasksFromJSONL(jsonl) // ← Multiple tasks
```

### use-task-persistence.ts — React Hook

**Loads persisted tasks when chat switches**

```typescript
const { tasks, isLoading } = useTaskPersistence(chatKey);
// Runs on mount, re-runs when chatKey changes
// Fetches from IDB, falls back to HTTP
```

### TaskPanel Component

**Displays persisted tasks in chat sidebar**
- Shows task ID (first 8 chars)
- Instruction preview (first 50 chars)
- Status badge (pending/running/completed/failed)
- Progress bar (0–100%)
- Sync status (✓ Synced / ✧ Local)
- Export button (aria-label: "Export task as JSON")
- Delete button (aria-label: "Delete task from IndexedDB")

**Props:**
```typescript
interface TaskPanelProps {
  tasks: Task[];
  onDeleteTask?: (taskId: string) => void;
  onExportTask?: (taskId: string) => void;
  isLoading?: boolean;
}
```

## Deployment

### Systemd Service

**File:** `ops/systemd/corvin-task-worker.service`

```ini
[Unit]
Description=Corvin Task Worker Pool (tenant %i)
After=default.target

[Service]
ExecStart=/path/to/.venv/bin/python -m corvin_console.task_worker_pool
Environment=CORVIN_TASK_MAX_WORKERS=5
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

**Enable:**
```bash
systemctl --user enable corvin-task-worker@_default.service
systemctl --user start corvin-task-worker@_default.service
```

## Monitoring

**Queue depth:**
```bash
wc -l ~/.corvin/tenants/_default/global/tasks__default.jsonl
```

**Worker daemon health:**
```bash
systemctl --user status corvin-task-worker@_default
journalctl --user -u corvin-task-worker@_default -f
```

**Pub/sub subscribers:**
```python
pubsub.subscriber_count("_default")  # Active WebSocket connections
```

## Backward Compatibility

- **M1 endpoints unchanged:** `GET /v1/console/chat/sessions/{sid}/tasks`, `GET /v1/console/chat/sessions/{sid}/tasks/{id}/events` (SSE)
- **M2 endpoints additive:** New tenant-global scope
- **TaskManager M1 compat:** Worker pool writes to session-scoped `workdir/tasks/` for SSE

## Edge Cases & Limits

| Limit | Value | Notes |
|-------|-------|-------|
| Max concurrent workers | 5 (configurable) | Per tenant |
| Max task TTL | 1 hour (default) | Enforced on dequeue |
| Task queue backlog | Unbounded | Append-only JSONL |
| Pub/sub queue size | 1000 events | Per tenant, backpressure drops slow subscribers |
| Task result cap | 64 MB | Same as Forge output |

### Stuck-task recovery (two complementary mechanisms)

A task killed mid-run (process SIGKILL / `bridge.sh restart`) never writes a
terminal event and is stuck on `running` — which also counts against the
per-chat `max_concurrent` quota and eventually starves the chat of new tasks.
Two layers clean this up:

- **M2 worker pool** (`task_worker_pool.py`): background cleanup marks expired
  tasks `FAILED` every 60s while the worker daemon runs. (Not active in 0.9.0 —
  the pool is not wired; see the Status note above.)
- **M1 adapter TaskManager** (`task_manager.py`, ADR-0080): the bridge has no
  long-running worker daemon, so it reaps at **boot** instead.
  `TaskManager.reap_stale_running()` finalizes a `running`/`pending` orphan as
  `FAILED` (`error="orphaned_on_restart"`, append-only `task.failed` event), but
  **only when its recorded engine pid is no longer alive**
  (`_task_pid_alive` / `_last_started_pid`). The earlier "at boot nothing can
  legitimately be running" assumption was false — an engine subprocess can
  outlive a restart (reparented, still streaming), and a task can itself boot
  `adapter.main()` (the E2E security suite does). **Load-bearing producer
  contract:** every code path that spawns an engine MUST record the real
  subprocess pid in its `task.started` event, or the reaper will read no pid,
  treat the live turn as an orphan, and falsely finalize it. The console chat
  path (`chat_runtime.py`) records `proc.pid` for the direct `claude`
  subprocess; the delegation path runs inline and records no engine pid (a
  console restart there is a genuine orphan). The adapter `main()` runs the
  reaper once per boot across all tenants/chats (logged as `task-reaper: …`).

## Testing

**Unit tests:** `test_task_queue.py` (13 tests, all passing)

**E2E tests:** `test_task_engine_m2_e2e.py`
- Multi-tenant task isolation
- Pub/sub broadcast across subscribers
- Task lifecycle (create → running → completed)

**Browser E2E:** vitest + playwright (TBD)
- Cross-chat task visibility
- Page-reload recovery
- Polling fallback activation

## Related ADRs

- **ADR-0081:** Task-Engine M2 Backend (this page)
- **ADR-0082:** Frontend Persistence Layer (IndexedDB + polling + recovery)

## Migration Path (M1 → M2)

1. Deploy `task_queue.py` + `task_worker_pool.py` + systemd service
2. Start worker daemon: `systemctl --user start corvin-task-worker@_default`
3. Deploy frontend hooks: `use-task-polling.ts`, `use-task-progress.ts`, `task-recovery.ts`
4. Integrate routes/tasks.py (no removal of M1 endpoints)
5. Migrate UI to TaskStatusBarM2 (parallel with old status bar during transition)
6. Monitor: queue depth, worker health, pub/sub subscriber count
7. Sunset M1 after 2-week observation window

