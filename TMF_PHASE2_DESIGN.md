# Token Measurement Framework (TMF) — Phase 2 Design

**Status:** Design Phase  
**Built On:** Phase 1 (COMPLETE) — Instrumentation, Storage, Baseline, Aggregation, Tests  
**Target:** Production measurement + live dashboard + database backend  
**Timeline:** K=1 through K=4 (one week per K)

---

## Executive Summary

Phase 1 built the measurement infrastructure. Phase 2 wires it into the runtime, makes it observable via a React dashboard, and replaces the in-memory cache with persistent database storage. Four milestones:

| K | Focus | Deliverable | Dependencies |
|---|-------|-------------|--------------|
| **K=1** | **WorkerEngine Wiring** | Token hooks inserted into every turn; counter propagated via context | Phase 1 complete, ExecutionContext accessible |
| **K=2** | **EventStore ↔ DB** | SQLite/PostgreSQL backend; query interface; audit chain integration | K=1 metrics flowing, EventStore API stable |
| **K=3** | **Console Panel** | React component; real-time updates; session/tenant isolation; dark mode | K=2 storage stable, console API patterns known |
| **K=4** | **API Endpoints** | `/api/metrics/*` routes; aggregation; live WebSocket feed (optional) | K=3 panel complete, auth/session context live |

---

## Phase 1 Recap — What's Already Built

### Components Ready

| File | Purpose | Status |
|------|---------|--------|
| `core/learning/token_instrumentation.py` | Hook API (on_worker_engine_start, on_llm_response, on_subsystem_executed, on_worker_engine_end) | ✅ Production-ready |
| `core/learning/token_baseline.py` | BaselineMetrics, ComparisonEngine, ComparisonResult | ✅ Production-ready |
| `core/learning/token_metrics_store.py` | In-memory TokenMetricsStore (cache-backed) | ✅ Ready for K=2 upgrade |
| `core/learning/token_metrics_aggregator.py` | Dashboard data generator (summary, by_task_type, by_subsystem) | ✅ Production-ready |
| `core/learning/event_schema.py` | LearningEvent, TokenMetricsPayload, LearningEventType | ✅ Immutable schema |
| `core/learning/event_persistence.py` | EventStore (disk I/O + audit chain) | ✅ Ready for K=2 |
| Tests | 200+ unit + E2E tests, all green | ✅ 100% coverage of Phase 1 |

### Hook API (Already Defined)

```python
# In WorkerEngine.run() (wherever it is):
# 1. START: token_counter = TokenInstrumentationHooks.on_worker_engine_start(turn_id, engine, tier)
# 2. SUBSYSTEMS: TokenInstrumentationHooks.on_subsystem_executed(counter, subsystem_name, tokens)
# 3. LLM RESPONSE: TokenInstrumentationHooks.on_llm_response(counter, input_tokens, output_tokens)
# 4. END: TokenInstrumentationHooks.on_worker_engine_end(counter, outcome_quality, required_followup)
```

No changes needed to the hook API. Task: find WorkerEngine and insert calls.

---

## K=1: WorkerEngine Integration

### Goal
Instrument the runtime so every turn measures tokens automatically.

### Files to Modify

#### 1. **Find WorkerEngine** (Discovery Task)
   - **Search target:** `core/compute/`, `core/chat/`, `core/orchestration/`, `core/gateway/`
   - **Grep for:** `class.*Engine.*run()`, `async def run(`, `execute_turn`, `process_turn`
   - **Expected:** Main LLM invocation point where responses come back with token counts
   - **Action:** Identify exact file and line numbers

#### 2. **WorkerEngine.run()** → **MODIFY**
   - **File location:** (TBD after discovery)
   - **Changes:**
     ```python
     # Line ~N: START OF RUN()
     from core.learning.token_instrumentation import (
         TokenInstrumentationHooks, set_current_token_counter
     )
     from core.learning.token_metrics_store import TokenMetricsStore
     from core.learning.event_emitter import EventEmitter
     
     async def run(self, turn_input):
         # --- NEW: K=1 Instrumentation Start ---
         token_counter = TokenInstrumentationHooks.on_worker_engine_start(
             turn_id=turn_input.turn_id,
             engine=self.engine_name,  # or similar
             engine_tier=self.tier,     # "cloud", "local", etc.
         )
         set_current_token_counter(token_counter)
         
         # Existing turn logic...
         try:
             # ... setup, context loading, skill injection, etc. ...
             
             # Where LLM response comes in (usually after `await llm_call(...)`)
             response = await self.llm_api.complete(prompt)
             
             # --- NEW: Record LLM tokens ---
             TokenInstrumentationHooks.on_llm_response(
                 token_counter,
                 input_tokens=response.usage.input_tokens,
                 output_tokens=response.usage.output_tokens,
             )
             
             # Existing: extract response, apply skills, etc.
             result = self._process_response(response)
             
         finally:
             # --- NEW: Finalize and persist ---
             TokenInstrumentationHooks.on_worker_engine_end(
                 token_counter,
                 outcome_quality=self._assess_quality(result),  # Or "good"/"bad"
                 required_followup=result.requires_followup,    # Or False
             )
             # Persist to EventStore (if injected)
             try:
                 await self.metrics_store.write_token_metrics(
                     token_counter,
                     tenant_id=turn_input.tenant_id,
                     instance_id=turn_input.instance_id,
                     session_id=turn_input.session_id,
                     user_id=turn_input.user_id,
                 )
             except Exception as e:
                 self.logger.warning(f"Failed to persist metrics: {e}")
     ```

#### 3. **ExecutionContext** → **MODIFY (Optional for Phase 2, Load-Bearing for Phase 3)**
   - **File:** `core/orchestration/execution_context.py` (or similar)
   - **Purpose:** Make token counter accessible to subsystems (confidence, cache, skill_injection)
   - **Changes:**
     ```python
     class ExecutionContext:
         def __init__(self, ...):
             ...
             self.token_counter = None  # Set by WorkerEngine before subsystems run
     
         def record_subsystem_usage(self, subsystem: str, tokens: int):
             """Called by subsystems (confidence, cache, skills) to record overhead."""
             if self.token_counter:
                 TokenInstrumentationHooks.on_subsystem_executed(
                     self.token_counter, subsystem, tokens
                 )
     ```
   - **Action:** Add context-passthrough so subsystems can call `context.record_subsystem_usage(...)`

#### 4. **Dependency Injection** → **CREATE or MODIFY**
   - **File:** `core/console/corvin_console/app.py` (or similar app bootstrap)
   - **Purpose:** Inject EventEmitter + TokenMetricsStore into WorkerEngine
   - **Changes:**
     ```python
     # In app startup:
     from core.learning.event_emitter import EventEmitter
     from core.learning.token_metrics_store import TokenMetricsStore
     
     event_emitter = EventEmitter(tenant_home=current_tenant().home)
     metrics_store = TokenMetricsStore(event_emitter)
     
     # Pass metrics_store to WorkerEngine constructor or via context
     worker_engine = WorkerEngine(..., metrics_store=metrics_store)
     ```

### K=1 Testing

**Test file:** `tests/unit/test_token_instrumentation_k1_live.py` (NEW)

```python
# Pseudo-code for K=1 E2E test
async def test_worker_engine_records_tokens():
    """Verify WorkerEngine starts/ends metrics correctly."""
    engine = WorkerEngine(metrics_store=mock_store)
    
    result = await engine.run(TurnInput(
        turn_id="t1",
        prompt="Hello",
        tenant_id="default",
    ))
    
    # Check: metrics_store.write_token_metrics was called
    assert mock_store.write_token_metrics.call_count == 1
    event = mock_store.write_token_metrics.call_args[0][0]  # TokenCounter
    assert event.turn_id == "t1"
    assert event.input_tokens > 0
    assert event.output_tokens > 0
```

**Live check:** Run a real turn in the console UI, verify metrics appear in storage.

### K=1 Deliverable

- ✅ WorkerEngine instrumented with 4 hook calls
- ✅ Token counter flows through entire turn
- ✅ Metrics persist to EventStore (in-memory cache + disk)
- ✅ Unit + E2E tests confirm flow

---

## K=2: EventStore ↔ Database Backend

### Goal
Replace in-memory cache with persistent SQLite/PostgreSQL, maintain audit chain integrity.

### Architecture

**Current (Phase 1):** EventStore writes to disk (JSONL), cache is in-memory dict  
**Target (Phase 2):** SQLite/PostgreSQL for queries, audit chain still disk-based

```
WorkerEngine
    ↓
TokenCounter.to_event()
    ↓
EventEmitter.emit()  ← writes to audit chain (unchanged)
    ↓
TokenMetricsStore.write_token_metrics()
    ↓
DB Backend (NEW)
    ├─ INSERT into metrics table
    ├─ Maintain index on (session_id, timestamp)
    └─ Return event_id
```

### Files to Create

#### 1. **`core/learning/token_metrics_db.py`** — NEW
   - **Purpose:** DB abstraction layer (SQLite/PostgreSQL agnostic)
   - **Class:** `TokenMetricsDB`
   - **Methods:**
     ```python
     class TokenMetricsDB:
         def __init__(self, db_uri: str):
             """Initialize DB connection.
             
             Args:
                 db_uri: "sqlite:///~/.corvin/metrics.db" or
                        "postgresql://user:pass@localhost/metrics"
             """
             self.db_uri = db_uri
             self.connection_pool = None  # or sqlalchemy.create_engine
         
         async def insert_token_metrics(self, event: LearningEvent) -> str:
             """Write token metrics event to DB. Returns event_id."""
             
         async def query_by_session(self, session_id: str, limit: int = 1000):
             """Fetch all metrics for a session."""
             
         async def query_by_timespan(self, tenant_id: str, start: datetime, end: datetime):
             """Fetch metrics in date range."""
             
         async def aggregate_by_task_type(self, session_id: str) -> dict:
             """Aggregate metrics by task type (for dashboard)."""
             
         async def close(self):
             """Close DB connection."""
     ```
   - **DB Schema (SQLite):**
     ```sql
     CREATE TABLE token_metrics (
         event_id TEXT PRIMARY KEY,
         tenant_id TEXT NOT NULL,
         session_id TEXT NOT NULL,
         turn_id TEXT UNIQUE NOT NULL,
         user_id TEXT,
         instance_id TEXT NOT NULL,
         skill_name TEXT,
         
         -- Token counts
         input_tokens INTEGER NOT NULL,
         output_tokens INTEGER NOT NULL,
         total_tokens INTEGER NOT NULL,
         baseline_tokens INTEGER,
         savings_tokens INTEGER,
         savings_percent REAL,
         
         -- Engine/Model info
         engine TEXT NOT NULL,
         engine_tier TEXT,
         model_id TEXT,
         
         -- Metadata
         task_type TEXT,
         task_domain TEXT,
         task_complexity TEXT,
         outcome_quality TEXT,
         required_followup BOOLEAN DEFAULT FALSE,
         latency_ms INTEGER,
         iterations_count INTEGER DEFAULT 1,
         
         -- Subsystems (JSON)
         subsystem_tokens JSON,
         
         -- Timing
         timestamp_utc TIMESTAMP NOT NULL,
         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
         
         -- Audit
         audit_id TEXT,
         _persistence_fallback TEXT
     );
     
     CREATE INDEX idx_session_timestamp ON token_metrics(session_id, timestamp_utc DESC);
     CREATE INDEX idx_tenant_timestamp ON token_metrics(tenant_id, timestamp_utc DESC);
     CREATE INDEX idx_task_type ON token_metrics(task_type);
     ```

#### 2. **`core/learning/token_metrics_db_factory.py`** — NEW
   - **Purpose:** Determine DB backend at runtime (SQLite default, PostgreSQL option)
   - **Class:** `create_metrics_db(tenant_id, config) -> TokenMetricsDB`
   - **Logic:**
     ```python
     def create_metrics_db(tenant_id: str, config: dict = None) -> TokenMetricsDB:
         """Factory for DB backend.
         
         Precedence:
         1. env var CORVIN_METRICS_DB_URI
         2. config["metrics_db_uri"]
         3. default: ~/.corvin/tenants/{tenant_id}/global/metrics.db
         """
         uri = (
             os.getenv("CORVIN_METRICS_DB_URI") or
             (config or {}).get("metrics_db_uri") or
             f"sqlite:///{tenant_home(tenant_id)}/global/metrics.db"
         )
         
         if "postgresql" in uri:
             return PostgresMetricsDB(uri)
         else:
             return SqliteMetricsDB(uri)
     ```

### Files to Modify

#### 1. **`core/learning/token_metrics_store.py`** → **MODIFY**
   - **Purpose:** Upgrade cache to DB-backed queries
   - **Changes:**
     ```python
     class TokenMetricsStore:
         def __init__(self, event_emitter: EventEmitter, db: TokenMetricsDB):
             self.event_emitter = event_emitter
             self.db = db
             # In-memory cache still exists but acts as write-through cache only
             self._cache: dict[str, LearningEvent] = {}
         
         async def write_token_metrics(self, counter, ...) -> str:
             # Write to EventEmitter (audit chain) unchanged
             event = counter.to_event(...)
             self.event_emitter.emit(event)
             
             # NEW: Write to DB backend
             await self.db.insert_token_metrics(event)
             
             # Cache for fast access
             self._cache[event.event_id] = event
             return event.event_id
         
         async def query_by_session(self, session_id: str, limit: int = 1000):
             # Try cache first (fast path for current session)
             cached = [e for e in self._cache.values() if e.session_id == session_id]
             if cached:
                 return cached
             # Fall back to DB (for historical sessions)
             return await self.db.query_by_session(session_id, limit)
     ```

#### 2. **`core/console/corvin_console/app.py`** → **MODIFY**
   - **Purpose:** Initialize DB backend on app startup
   - **Changes:**
     ```python
     from core.learning.token_metrics_db_factory import create_metrics_db
     from core.learning.token_metrics_store import TokenMetricsStore
     from core.learning.event_emitter import EventEmitter
     
     async def lifespan(app: FastAPI):
         # Startup
         db = create_metrics_db(tenant_id="default")
         event_emitter = EventEmitter(tenant_home=...)
         metrics_store = TokenMetricsStore(event_emitter, db)
         
         # Store in app state for dependency injection
         app.state.metrics_store = metrics_store
         
         yield
         
         # Cleanup
         await db.close()
     
     app = FastAPI(lifespan=lifespan)
     ```

### K=2 Testing

**Test file:** `tests/unit/test_token_metrics_db_k2.py` (NEW)

```python
# Pseudo-code
async def test_sqlite_backend_insert_and_query():
    db = SqliteMetricsDB("sqlite:///:memory:")
    event = LearningEvent(...)
    
    event_id = await db.insert_token_metrics(event)
    retrieved = await db.query_by_turn(event.payload["token_metrics"]["turn_id"])
    
    assert retrieved.event_id == event_id
    assert retrieved.payload["token_metrics"]["input_tokens"] == 1000

async def test_db_persists_across_restarts():
    """SQLite on disk survives app restart."""
    db = SqliteMetricsDB("sqlite:///test_metrics.db")
    # Insert events...
    await db.close()
    
    # Reopen
    db2 = SqliteMetricsDB("sqlite:///test_metrics.db")
    events = await db2.query_by_session("s1")
    assert len(events) > 0  # Data persisted
```

### K=2 Deliverable

- ✅ SQLite backend (default) or PostgreSQL (optional)
- ✅ Query interface matching TokenMetricsStore API
- ✅ Audit chain integration (write_audit_event still fires)
- ✅ Tenant isolation (all queries filtered by tenant_id)
- ✅ DB tests + migration path doc

---

## K=3: Console Panel (React VibeMetrics Component)

### Goal
Build a real-time dashboard showing token metrics for the current session.

### File Structure

```
core/console/corvin_console/web-next/src/
├── pages/
│   └── vibe-metrics.tsx  (NEW — loader for lazy component)
├── components/
│   └── VibeMetrics/      (NEW — panel components)
│       ├── Dashboard.tsx      (main container)
│       ├── TokenChart.tsx     (line graph of tokens/turn)
│       ├── SavingsCard.tsx    (savings % and delta)
│       ├── SubsystemBreakdown.tsx  (pie chart of subsystem overhead)
│       ├── TaskTypeTable.tsx  (table: task_type, turns, total_tokens, savings)
│       └── useLiveMetrics.ts  (hook for real-time updates via WebSocket or polling)
└── lazy-pages/
    └── index.ts          (MODIFY — add VibeMetricsPage export)
```

### Files to Create

#### 1. **`core/console/corvin_console/web-next/src/pages/vibe-metrics.tsx`** — NEW
   - **Purpose:** Entry point for vibe-metrics page (lazy-loaded)
   - **Code:**
     ```tsx
     import { lazy, Suspense } from "react";
     import { Loader2 } from "lucide-react";
     
     const VibeMetricsDashboard = lazy(() =>
       import("@/components/VibeMetrics/Dashboard")
     );
     
     export default function VibeMetricsPage() {
       return (
         <Suspense fallback={<div className="flex justify-center py-12"><Loader2 className="animate-spin" /></div>}>
           <VibeMetricsDashboard />
         </Suspense>
       );
     }
     ```

#### 2. **`core/console/corvin_console/web-next/src/components/VibeMetrics/Dashboard.tsx`** — NEW
   - **Purpose:** Main metrics dashboard
   - **Features:**
     - Session selector (dropdown or auto-detect current)
     - Real-time updates (via `/api/metrics/session/{id}` polling or WebSocket)
     - Four-card layout: Total Tokens, Savings %, Avg Latency, Turn Count
     - Charts below: Token trend line, Subsystem pie, Task type bar
   - **Pseudocode:**
     ```tsx
     export default function Dashboard() {
       const [session, setSession] = useState(useSessionId());
       const { metrics, isLoading } = useLiveMetrics(session);
       
       if (isLoading) return <Loader2 className="animate-spin" />;
       if (!metrics) return <div>No metrics yet</div>;
       
       return (
         <div className="p-8 bg-gradient-to-br from-slate-900 to-slate-800 min-h-screen text-white">
           <h1>Token Metrics — {session}</h1>
           
           {/* Stats row */}
           <div className="grid grid-cols-4 gap-4">
             <StatCard title="Total Tokens" value={metrics.total_tokens} />
             <StatCard title="Savings" value={`${metrics.savings_percent}%`} />
             <StatCard title="Avg Latency" value={`${metrics.avg_latency}ms`} />
             <StatCard title="Turns" value={metrics.turn_count} />
           </div>
           
           {/* Charts row */}
           <div className="grid grid-cols-2 gap-6 mt-8">
             <TokenChart data={metrics.turns} />
             <SubsystemBreakdown data={metrics.subsystems} />
           </div>
           
           {/* Table */}
           <TaskTypeTable data={metrics.by_task_type} />
         </div>
       );
     }
     ```

#### 3. **`core/console/corvin_console/web-next/src/components/VibeMetrics/TokenChart.tsx`** — NEW
   - **Purpose:** Line chart of token consumption over time
   - **Library:** Recharts (already used in console)
   - **Props:** `{ data: Turn[] }` where `Turn = { turn_id, timestamp, total_tokens, input_tokens, output_tokens }`
   - **Features:**
     - X-axis: turn number or time
     - Y-axis: total tokens (stacked: input + output)
     - Hover: show input/output split
     - Legend: input vs output
     - Dark mode: slate/blue colors
   - **Pseudocode:**
     ```tsx
     import { LineChart, Line, XAxis, YAxis, Tooltip, Legend } from "recharts";
     
     export function TokenChart({ data }: { data: Turn[] }) {
       return (
         <div className="bg-slate-700 p-4 rounded">
           <h3>Tokens per Turn</h3>
           <LineChart width={500} height={300} data={data}>
             <XAxis dataKey="turn_id" />
             <YAxis />
             <Tooltip />
             <Legend />
             <Line type="monotone" dataKey="input_tokens" stroke="#3b82f6" />
             <Line type="monotone" dataKey="output_tokens" stroke="#8b5cf6" />
           </LineChart>
         </div>
       );
     }
     ```

#### 4. **`core/console/corvin_console/web-next/src/components/VibeMetrics/useLiveMetrics.ts`** — NEW
   - **Purpose:** React hook for fetching + polling metrics
   - **Features:**
     - Auto-poll `/api/metrics/session/{id}/summary` every 5 seconds
     - Tenant/session isolation via auth header
     - Error handling (graceful fallback)
   - **Pseudocode:**
     ```tsx
     export function useLiveMetrics(sessionId: string) {
       const [metrics, setMetrics] = useState(null);
       const [isLoading, setIsLoading] = useState(true);
       
       useEffect(() => {
         const poll = async () => {
           try {
             const resp = await fetch(`/v1/console/metrics/session/${sessionId}/summary`);
             const data = await resp.json();
             setMetrics(data);
           } catch (e) {
             console.error("Metrics fetch failed:", e);
           } finally {
             setIsLoading(false);
           }
         };
         
         poll();
         const interval = setInterval(poll, 5000);  // Poll every 5s
         return () => clearInterval(interval);
       }, [sessionId]);
       
       return { metrics, isLoading };
     }
     ```

#### 5. **Other Components** (similar pattern)
   - `SavingsCard.tsx` — KPI card showing savings % + trend
   - `SubsystemBreakdown.tsx` — Pie chart of subsystem contributions
   - `TaskTypeTable.tsx` — Table view of task_type aggregates

### Files to Modify

#### 1. **`core/console/corvin_console/web-next/src/panels/registry.tsx`** → **MODIFY**
   - **Purpose:** Register vibe-metrics panel in the console nav
   - **Changes:**
     ```tsx
     import { VibeMetricsPage } from "@/lazy-pages";  // Add import
     
     export const PANELS: ConsolePanel[] = [
       // ... existing panels ...
       rc("vibe-metrics", "Token Metrics", VibeMetricsPage),  // Add this line
     ];
     ```

#### 2. **`core/console/corvin_console/web-next/src/lazy-pages/index.ts`** → **MODIFY**
   - **Purpose:** Export VibeMetricsPage
   - **Changes:**
     ```ts
     export { default as VibeMetricsPage } from "../pages/vibe-metrics";
     ```

### K=3 Testing

**Test file:** `tests/e2e/console-vibe-metrics.spec.ts` (NEW, Playwright)

```typescript
test("VibeMetrics panel loads and displays session data", async ({ page }) => {
  // Navigate to metrics panel
  await page.goto("/console/vibe-metrics");
  
  // Wait for session selector to load
  await page.waitForSelector("text=Token Metrics");
  
  // Verify stat cards rendered
  await expect(page.locator("text=Total Tokens")).toBeVisible();
  await expect(page.locator("text=Savings")).toBeVisible();
  
  // Verify chart loaded (Recharts container)
  await expect(page.locator("[role='presentation']")).toBeVisible();
});

test("VibeMetrics updates when new metrics arrive", async ({ page }) => {
  await page.goto("/console/vibe-metrics");
  
  const totalBefore = await page.locator("text=Total Tokens").textContent();
  
  // Simulate 2 new turns (or wait for them)
  await page.waitForTimeout(10000);  // Wait 10s for polling
  
  const totalAfter = await page.locator("text=Total Tokens").textContent();
  // Should update (implementation depends on mock data)
});
```

### K=3 Deliverable

- ✅ React components (Dashboard, Charts, Tables)
- ✅ Console panel registration
- ✅ Live polling from `/api/metrics/*` endpoints
- ✅ Dark mode (matches console theme)
- ✅ Tenant/session isolation (via auth headers)
- ✅ E2E tests confirm rendering + data flow

---

## K=4: API Endpoints

### Goal
Expose metrics via REST API for the console panel + external tools.

### Files to Create

#### 1. **`core/console/corvin_console/routes/metrics.py`** — NEW
   - **Purpose:** REST API for metrics queries
   - **Routes:**
     ```
     GET /v1/console/metrics/session/{session_id}/summary
     GET /v1/console/metrics/session/{session_id}/turns
     GET /v1/console/metrics/session/{session_id}/by-task-type
     GET /v1/console/metrics/session/{session_id}/by-subsystem
     GET /v1/console/metrics/stats
     GET /v1/console/metrics/export  (CSV download)
     ```
   - **Code:**
     ```python
     from fastapi import APIRouter, Depends, HTTPException, Query
     from pydantic import BaseModel
     from typing import Optional
     from datetime import datetime
     
     from .. import auth as session_auth
     from ..deps import require_session
     from core.learning.token_metrics_store import TokenMetricsStore
     
     router = APIRouter(prefix="/metrics")
     
     class MetricsSummary(BaseModel):
         turn_count: int
         total_tokens: int
         baseline_tokens: int
         savings_tokens: int
         savings_percent: float
         avg_tokens_per_turn: float
         subsystems: dict
         by_task_type: dict
     
     @router.get("/session/{session_id}/summary")
     async def get_session_summary(
         session_id: str,
         rec: Annotated[SessionRecord, Depends(require_session)],
         metrics_store: TokenMetricsStore = Depends(get_metrics_store),
     ) -> MetricsSummary:
         """Fetch summary stats for a session.
         
         Auth: user must own the session
         Isolation: tenant_id from SessionRecord
         """
         # Verify ownership
         if rec.session_id != session_id:
             raise HTTPException(status_code=403, detail="Access denied")
         
         # Query from DB (filtered by tenant_id, session_id)
         summary = await metrics_store.summary(session_id)
         return MetricsSummary(**summary)
     
     @router.get("/session/{session_id}/turns")
     async def get_session_turns(
         session_id: str,
         rec: Annotated[SessionRecord, Depends(require_session)],
         limit: int = Query(100, ge=1, le=1000),
     ) -> list:
         """Fetch individual turn metrics."""
         if rec.session_id != session_id:
             raise HTTPException(status_code=403)
         
         turns = await metrics_store.query_by_session(session_id, limit)
         return [t.to_dict() for t in turns]
     
     @router.get("/stats")
     async def get_global_stats(
         rec: Annotated[SessionRecord, Depends(require_session)],
         since: Optional[datetime] = Query(None),
     ) -> dict:
         """Fetch stats across all sessions for this tenant."""
         # Only owner/admin can query global stats
         if rec.role not in ["owner", "admin"]:
             raise HTTPException(status_code=403)
         
         # Query: all metrics for tenant_id in past 30 days
         end = datetime.utcnow()
         start = since or (end - timedelta(days=30))
         
         events = await metrics_store.query_by_timespan(
             tenant_id=rec.tenant_id, start=start, end=end
         )
         
         # Aggregate across all sessions
         return {
             "turn_count": len(events),
             "total_tokens": sum(e.payload["token_metrics"]["total_tokens"] for e in events),
             # ... etc
         }
     ```

#### 2. **Dependency Injection** → **MODIFY `core/console/corvin_console/deps.py`**
   - **Purpose:** Provide metrics_store to routes via DI
   - **Changes:**
     ```python
     from fastapi import Request
     from core.learning.token_metrics_store import TokenMetricsStore
     
     async def get_metrics_store(request: Request) -> TokenMetricsStore:
         """Inject metrics store from app state."""
         return request.app.state.metrics_store
     ```

#### 3. **Route Registration** → **MODIFY `core/console/corvin_console/app.py`**
   - **Purpose:** Include metrics router
   - **Changes:**
     ```python
     from .routes import metrics as metrics_route
     
     # In app setup:
     app.include_router(metrics_route.router, prefix="/v1/console", tags=["metrics"])
     ```

### Optional: WebSocket Live Feed (K=4+)

If real-time updates are critical (vs. polling), add WebSocket endpoint:

```python
from fastapi import WebSocket

@router.websocket("/ws/session/{session_id}/live")
async def metrics_live(
    websocket: WebSocket,
    session_id: str,
    rec: SessionRecord,  # Auth still applies
):
    """Stream metrics updates via WebSocket."""
    await websocket.accept()
    
    # Subscribe to metrics events
    async for event in metrics_store.watch_session(session_id):
        await websocket.send_json({
            "event_type": "metric_update",
            "data": event.to_dict(),
        })
```

This requires an event subscription system (more complex; defer if polling is sufficient).

### K=4 Testing

**Test file:** `tests/unit/test_metrics_api_k4.py` (NEW)

```python
@pytest.mark.asyncio
async def test_get_session_summary_requires_auth():
    """Unauthenticated request returns 401."""
    client = AsyncClient(app, base_url="http://test")
    response = await client.get("/v1/console/metrics/session/s1/summary")
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_get_session_summary_forbidden_for_other_user():
    """User cannot query another user's session."""
    # Setup: user1 owns session s1, user2 tries to access
    token = create_auth_token("user2")
    client = AsyncClient(app, base_url="http://test", headers={"Authorization": f"Bearer {token}"})
    
    response = await client.get("/v1/console/metrics/session/s1/summary")
    assert response.status_code == 403

@pytest.mark.asyncio
async def test_get_session_summary_success():
    """Authorized user retrieves summary."""
    # Write some metrics first
    store = app.state.metrics_store
    counter = TokenCounter(turn_id="t1", engine="claude")
    counter.record_llm_call(1000, 500)
    counter.baseline_tokens = 2000
    counter.finalize()
    await store.write_token_metrics(counter, tenant_id="default", ...)
    
    # Query
    token = create_auth_token("user1")
    client = AsyncClient(app, headers={"Authorization": f"Bearer {token}"})
    response = await client.get("/v1/console/metrics/session/s1/summary")
    
    assert response.status_code == 200
    data = response.json()
    assert data["turn_count"] == 1
    assert data["total_tokens"] == 1500
    assert data["savings_tokens"] == 500
```

### K=4 Deliverable

- ✅ Five REST endpoints (`/v1/console/metrics/*`)
- ✅ Auth integration (SessionRecord + tenant isolation)
- ✅ Summary + detail views
- ✅ CSV export (optional)
- ✅ API tests (auth, isolation, data correctness)

---

## Phase 2 Summary: Files to Create/Modify

### Create (New Files)

| File | Purpose | LOC Est. | K |
|------|---------|----------|---|
| `core/learning/token_metrics_db.py` | DB abstraction layer | 400 | K=2 |
| `core/learning/token_metrics_db_factory.py` | DB backend factory | 80 | K=2 |
| `core/console/corvin_console/web-next/src/pages/vibe-metrics.tsx` | Metrics page entry | 20 | K=3 |
| `core/console/corvin_console/web-next/src/components/VibeMetrics/Dashboard.tsx` | Main dashboard | 150 | K=3 |
| `core/console/corvin_console/web-next/src/components/VibeMetrics/TokenChart.tsx` | Token line chart | 80 | K=3 |
| `core/console/corvin_console/web-next/src/components/VibeMetrics/SavingsCard.tsx` | Savings KPI card | 60 | K=3 |
| `core/console/corvin_console/web-next/src/components/VibeMetrics/SubsystemBreakdown.tsx` | Subsystem pie chart | 80 | K=3 |
| `core/console/corvin_console/web-next/src/components/VibeMetrics/TaskTypeTable.tsx` | Task type table | 100 | K=3 |
| `core/console/corvin_console/web-next/src/components/VibeMetrics/useLiveMetrics.ts` | Metrics polling hook | 60 | K=3 |
| `core/console/corvin_console/routes/metrics.py` | Metrics REST API | 300 | K=4 |
| `tests/unit/test_token_instrumentation_k1_live.py` | K=1 E2E tests | 150 | K=1 |
| `tests/unit/test_token_metrics_db_k2.py` | K=2 DB tests | 200 | K=2 |
| `tests/e2e/console-vibe-metrics.spec.ts` | K=3 component tests | 120 | K=3 |
| `tests/unit/test_metrics_api_k4.py` | K=4 API tests | 180 | K=4 |

**Total New:** ~2,000 LOC (tests included)

### Modify (Existing Files)

| File | Purpose | Changes |
|------|---------|---------|
| (TBD) `core/compute/worker.py` or similar | **K=1:** Insert 4 token hooks | ~30 lines |
| (TBD) `core/orchestration/execution_context.py` | **K=1:** Add token counter pass-through | ~20 lines |
| `core/console/corvin_console/app.py` | **K=2/K=4:** Initialize DB, register metrics routes | ~40 lines |
| `core/learning/token_metrics_store.py` | **K=2:** Upgrade to DB-backed | ~50 lines |
| `core/console/corvin_console/web-next/src/panels/registry.tsx` | **K=3:** Register metrics panel | ~3 lines |
| `core/console/corvin_console/web-next/src/lazy-pages/index.ts` | **K=3:** Export VibeMetricsPage | ~1 line |
| `core/console/corvin_console/deps.py` | **K=4:** Add get_metrics_store DI | ~8 lines |

**Total Modified:** ~150 lines

---

## Integration Points (Exact Locations)

### K=1: Find WorkerEngine

**Search Strategy:**
1. `grep -r "class.*Engine.*run" core/ --include="*.py"`
2. Check `core/compute/`, `core/orchestration/`, `core/chat/`, `core/gateway/`
3. Look for: `async def run(self, turn_input)` returning `TurnResult` with token counts
4. Expected: 1-2 files, ~50–200 lines per hook site

**Likely candidates:**
- `core/compute/corvin_compute/worker.py` (from earlier search)
- `core/chat/` subfolder (if exists)
- `core/orchestration/` subfolder (if exists)

### K=2: DB Schema Deployment

**SQLite Default Path:** `~/.corvin/tenants/_default/global/metrics.db`  
**Init on first write:** If DB doesn't exist, create schema in `TokenMetricsDB.__init__()`  
**Migration:** No Alembic/Alembic needed for Phase 2 (direct schema creation)

### K=3: Console Theme Matching

**Dark Mode:** Verify `useDarkMode()` hook or theme context exists  
**Color Palette:** Check `tailwind.config.js` for existing color vars  
**Font/Spacing:** Reuse existing `DashboardPage` or `SettingsPage` patterns

### K=4: Auth Context

**SessionRecord:** Verify structure in `core/console/corvin_console/auth.py` or `deps.py`  
**Tenant Isolation:** Confirm `rec.tenant_id` is accessible in routes  
**Owner Check:** Look for role-based gating pattern in existing routes (e.g., `license.py`)

---

## High-Level Checklist

### K=1: WorkerEngine Wiring

- [ ] Discover WorkerEngine location (grep)
- [ ] Add 4 hook calls to WorkerEngine.run()
- [ ] Inject TokenMetricsStore into WorkerEngine constructor
- [ ] Test hook calls fire on every turn
- [ ] E2E: run console chat, verify metrics in storage

### K=2: EventStore ↔ DB

- [ ] Create `token_metrics_db.py` with TokenMetricsDB base class
- [ ] Create SQLiteMetricsDB and PostgresMetricsDB subclasses
- [ ] Create DB schema (11-column table + 3 indexes)
- [ ] Create `token_metrics_db_factory.py`
- [ ] Update `token_metrics_store.py` to use DB backend
- [ ] Update `app.py` to initialize DB on startup
- [ ] Test SQLite read/write and persistence across restarts
- [ ] Test PostgreSQL backend (if available)
- [ ] Verify audit chain still fires for every write

### K=3: Console Panel

- [ ] Create React components (Dashboard, TokenChart, SavingsCard, SubsystemBreakdown, TaskTypeTable)
- [ ] Create `useLiveMetrics.ts` hook
- [ ] Register panel in registry.tsx
- [ ] Add VibeMetricsPage to lazy-pages exports
- [ ] Style for dark mode + responsive layout
- [ ] E2E test: panel loads, stats render, polling works

### K=4: API Endpoints

- [ ] Create `/v1/console/metrics/` routes file
- [ ] Implement 5 GET endpoints + optional WebSocket
- [ ] Add auth checks (SessionRecord, tenant isolation)
- [ ] Add DI for metrics_store
- [ ] Register router in app.py
- [ ] API tests for auth + data correctness
- [ ] Integration test: panel → API → DB → response

---

## Phase 2 → Phase 3 Roadmap

Once K=4 is done, Phase 3 will build on this foundation:

| Phase 3 Milestone | Focus | Deps |
|---|---|---|
| **K=5** | **Baselines at Scale** | Segment baseline by engine, model, user persona |
| **K=6** | **Closed-Loop Learning** | Feedback loop (user rates quality) → confidence scoring |
| **K=7** | **Cost Attribution** | Map tokens → cost; show per-subsystem cost breakdown |
| **K=8** | **Anomaly Detection** | Alert on 2σ outliers (sudden token spike) |

---

## FAQ / Common Pitfalls

### Q: Where is WorkerEngine?
**A:** Search `core/compute/`, `core/orchestration/`, `core/chat/`. If not found, check `core/gateway/` for the main LLM invocation point.

### Q: Should I use SQLAlchemy?
**A:** Not required for Phase 2. Direct SQL (with parameterized queries) is simpler and sufficient. Add SQLAlchemy in Phase 3+ if needed for complex joins.

### Q: How do I test metrics without running real LLM calls?
**A:** Mock the LLM response in tests:
```python
mock_response = Mock()
mock_response.usage.input_tokens = 1000
mock_response.usage.output_tokens = 500
engine.llm_api.complete = AsyncMock(return_value=mock_response)
```

### Q: Can I skip WebSocket for K=4?
**A:** Yes. Polling `/api/metrics/session/{id}/summary` every 5s is sufficient for MVP. WebSocket is a future optimization.

### Q: How do I handle tenant isolation?
**A:** Every DB query **must** filter by `tenant_id`:
```python
# Bad: await db.query_by_session(session_id)
# Good: await db.query_by_session(session_id, tenant_id=rec.tenant_id)
```
All WHERE clauses should include `WHERE ... AND tenant_id = ?`.

### Q: What if metrics write fails (DB down)?
**A:** Fall back to EventStore (disk) silently:
```python
try:
    await db.insert_token_metrics(event)
except Exception as e:
    logger.warning(f"DB write failed, falling back to disk: {e}")
    # metrics_store._cache still has it; disk JSONL still has it
```

---

## Success Criteria

**Phase 2 Complete when:**
- ✅ Every turn measure tokens (K=1)
- ✅ Metrics persist to SQLite/PostgreSQL (K=2)
- ✅ Console panel displays live data (K=3)
- ✅ REST API exposes metrics (K=4)
- ✅ Tests: 200+ new tests, all green
- ✅ Docs: Phase 2 completion report + API reference
- ✅ ADR: ADR-0319 (Phase 2 architecture) if design choice recorded

---

## Next: Phase 2 Kick-Off

1. **Week 1 (K=1):** Discover WorkerEngine, instrument it, run first live turn
2. **Week 2 (K=2):** Build DB layer, verify persistence, test schema
3. **Week 3 (K=3):** Build console panel, hook up polling, test rendering
4. **Week 4 (K=4):** Implement API endpoints, auth checks, final integration tests

**Target Ship Date:** 4 weeks  
**Success Gate:** All Phase 2 tests green + Phase 1 tests still pass
