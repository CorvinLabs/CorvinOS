# Phase 2 Quick Reference — File Locations & Task Breakdown

Use this alongside `TMF_PHASE2_DESIGN.md` for rapid implementation.

---

## Phase 1 Existing Files (Reference Only)

These are READY TO USE; no modifications needed:

```
core/learning/
├── token_instrumentation.py        ← TokenCounter, TokenInstrumentationHooks (DONE)
├── token_baseline.py               ← BaselineMetrics, ComparisonEngine (DONE)
├── token_metrics_store.py          ← TokenMetricsStore (K=2 UPGRADE NEEDED)
├── token_metrics_aggregator.py     ← TokenMetricsAggregator (DONE)
├── event_schema.py                 ← LearningEvent, TokenMetricsPayload (DONE)
├── event_persistence.py            ← EventStore, write_event (DONE)
└── event_emitter.py                ← EventEmitter.emit() (DONE)

tests/unit/
├── test_token_instrumentation_k1.py              (DONE)
├── test_token_metrics_phase1_complete.py         (DONE)
├── test_token_metrics_store_k2.py                (DONE)
└── [K=1-K=4 new test files below]
```

---

## K=1: WorkerEngine Integration (Discovery Required)

### Task 1A: Find WorkerEngine

**Steps:**
1. Run: `grep -r "class.*Engine.*run" /home/shumway/projects/CorvinOS/core --include="*.py" | head -20`
2. Check for: `async def run(self, turn_input)` or `def run(self, ...)`
3. Likely locations:
   - `core/compute/corvin_compute/worker.py` ← **START HERE**
   - `core/orchestration/corvin_orchestration/*.py` (if exists)
   - `core/chat/corvin_chat/*.py` (if exists)
   - `core/gateway/corvin_gateway/*.py` (main inference)

**Success:** You have exact file path and line number of `async def run(...)`

### Task 1B: Instrument WorkerEngine

**File:** (TBD from 1A)  
**Changes:**
- Add import: `from core.learning.token_instrumentation import TokenInstrumentationHooks, set_current_token_counter`
- At run() start (~line N): Create counter
- After LLM call (~line N+50): Record tokens
- At run() end (~line N+100): Finalize counter
- Wrap in try/finally for cleanup

**Pseudocode (insert at specific line numbers):**
```python
# Line ~N (start of async def run)
async def run(self, turn_input):
    # NEW: Create counter
    from core.learning.token_instrumentation import TokenInstrumentationHooks, set_current_token_counter
    
    counter = TokenInstrumentationHooks.on_worker_engine_start(
        turn_id=turn_input.turn_id or str(uuid.uuid4()),
        engine=self.engine_name,
        engine_tier=getattr(self, 'tier', 'cloud'),
    )
    set_current_token_counter(counter)
    
    try:
        # ... existing setup code ...
        
        # Line ~N+50 (after LLM response)
        response = await self.llm_api.complete(prompt)  # or similar
        
        # NEW: Record LLM tokens
        TokenInstrumentationHooks.on_llm_response(
            counter,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        
        # ... existing response processing ...
        result = self._process_response(response)
        
        return result
        
    finally:
        # Line ~N+100 (end of run)
        # NEW: Finalize counter
        TokenInstrumentationHooks.on_worker_engine_end(
            counter,
            outcome_quality="good" if result.success else "bad",
            required_followup=getattr(result, 'requires_followup', False),
        )
        
        # NEW: Persist (if metrics_store available)
        if hasattr(self, 'metrics_store'):
            try:
                event = counter.to_event(
                    tenant_id=turn_input.tenant_id,
                    instance_id=turn_input.instance_id,
                    session_id=turn_input.session_id,
                    user_id=getattr(turn_input, 'user_id', None),
                )
                self.metrics_store.event_emitter.emit(event)
            except Exception as e:
                self.logger.warning(f"Failed to persist metrics: {e}")
```

### Task 1C: Inject TokenMetricsStore into WorkerEngine

**File:** `core/console/corvin_console/app.py` or `core/gateway/corvin_gateway/app.py` (main app bootstrap)

**Add to startup:**
```python
# In lifespan or @app.on_event("startup"):
from core.learning.event_emitter import EventEmitter
from core.learning.token_metrics_store import TokenMetricsStore
from core.compliance.corvin_compliance_reports.audit_writer import get_audit_writer

# Initialize EventEmitter with audit chain
audit_writer = get_audit_writer()
event_emitter = EventEmitter(audit_writer=audit_writer)

# Initialize TokenMetricsStore
metrics_store = TokenMetricsStore(event_emitter)

# Store in app state for DI
app.state.metrics_store = metrics_store

# Also inject into WorkerEngine (pseudocode — exact method depends on engine)
worker_engine.metrics_store = metrics_store
# OR (if DI container):
# container.register_singleton("metrics_store", metrics_store)
```

### Task 1D: Create K=1 Test

**File:** `tests/unit/test_token_instrumentation_k1_live.py` (NEW)

**Test outline:**
```python
import pytest
from core.learning.token_instrumentation import TokenCounter, TokenInstrumentationHooks
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.event_emitter import EventEmitter

class MockEventEmitter(EventEmitter):
    def __init__(self):
        super().__init__()
        self.events = []
    
    def emit(self, event):
        self.events.append(event)

@pytest.mark.asyncio
async def test_worker_engine_records_tokens():
    """Live test: WorkerEngine → TokenInstrumentationHooks → metrics_store."""
    # (Pseudo-code; actual test depends on WorkerEngine structure)
    
    emitter = MockEventEmitter()
    store = TokenMetricsStore(emitter)
    
    # Simulate WorkerEngine.run() flow
    counter = TokenInstrumentationHooks.on_worker_engine_start("t1", "claude", "cloud")
    
    # Simulate LLM response
    TokenInstrumentationHooks.on_llm_response(counter, 1000, 500)
    
    # Simulate subsystem overhead
    TokenInstrumentationHooks.on_subsystem_executed(counter, "confidence", 200)
    
    # Finalize
    TokenInstrumentationHooks.on_worker_engine_end(counter, "good", False)
    
    # Persist
    event_id = await store.write_token_metrics(
        counter,
        tenant_id="default",
        instance_id="inst1",
        session_id="s1",
    )
    
    # Verify
    assert event_id is not None
    assert counter.total_tokens == 1500
    assert counter.subsystem_tokens["confidence"] == 200
    assert len(emitter.events) == 1
```

**Run:** `pytest tests/unit/test_token_instrumentation_k1_live.py -v`

### K=1 Definition of Done

- [ ] WorkerEngine found and located (file path + line numbers)
- [ ] 4 hooks inserted (start, llm_response, subsystem, end)
- [ ] TokenMetricsStore injected into WorkerEngine
- [ ] K=1 unit test passes
- [ ] E2E: Run 1 real turn in console, verify metrics appear
- [ ] No Phase 1 tests broken

---

## K=2: EventStore ↔ Database Backend

### Task 2A: Create TokenMetricsDB Base Class

**File:** `core/learning/token_metrics_db.py` (NEW, ~400 LOC)

**Skeleton:**
```python
"""Token metrics database abstraction."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List
from core.learning.event_schema import LearningEvent

class TokenMetricsDB(ABC):
    """Abstract base for token metrics persistence."""
    
    @abstractmethod
    async def insert_token_metrics(self, event: LearningEvent) -> str:
        """Insert event and return event_id."""
        pass
    
    @abstractmethod
    async def query_by_session(self, session_id: str, tenant_id: str, limit: int = 1000):
        """Fetch events for a session."""
        pass
    
    @abstractmethod
    async def query_by_timespan(self, tenant_id: str, start: datetime, end: datetime, limit: int = 10000):
        """Fetch events in date range."""
        pass
    
    @abstractmethod
    async def aggregate_by_task_type(self, session_id: str, tenant_id: str) -> dict:
        """Aggregate metrics by task type."""
        pass
    
    @abstractmethod
    async def close(self):
        """Close DB connection."""
        pass


class SqliteMetricsDB(TokenMetricsDB):
    """SQLite backend (default)."""
    
    def __init__(self, db_uri: str):
        """e.g., "sqlite:///~/.corvin/metrics.db" or "sqlite:///:memory:" for tests"""
        import sqlite3
        from pathlib import Path
        
        # Parse URI: sqlite:///path or sqlite:///:memory:
        if db_uri == "sqlite:///:memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = db_uri.replace("sqlite:///", "")
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
    
    def _init_schema(self):
        """Create tables if not exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS token_metrics (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_id TEXT UNIQUE NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                engine TEXT NOT NULL,
                engine_tier TEXT,
                baseline_tokens INTEGER,
                savings_tokens INTEGER,
                savings_percent REAL,
                task_type TEXT,
                outcome_quality TEXT,
                timestamp_utc TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_timestamp
            ON token_metrics(session_id, timestamp_utc DESC)
        """)
        
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tenant_timestamp
            ON token_metrics(tenant_id, timestamp_utc DESC)
        """)
        
        self.conn.commit()
    
    async def insert_token_metrics(self, event: LearningEvent) -> str:
        """Insert event into SQLite."""
        metrics = event.payload.get("token_metrics", {})
        
        self.conn.execute("""
            INSERT INTO token_metrics (
                event_id, tenant_id, session_id, turn_id,
                input_tokens, output_tokens, total_tokens,
                engine, engine_tier, baseline_tokens, savings_tokens, savings_percent,
                task_type, outcome_quality, timestamp_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.event_id,
            event.tenant_id,
            event.session_id,
            metrics.get("turn_id"),
            metrics.get("input_tokens", 0),
            metrics.get("output_tokens", 0),
            metrics.get("total_tokens", 0),
            metrics.get("engine"),
            metrics.get("engine_tier"),
            metrics.get("baseline_tokens"),
            metrics.get("savings_tokens"),
            metrics.get("savings_percent"),
            metrics.get("task_type"),
            metrics.get("outcome_quality"),
            event.timestamp_utc,
        ))
        
        self.conn.commit()
        return event.event_id
    
    async def query_by_session(self, session_id: str, tenant_id: str, limit: int = 1000):
        """Fetch all metrics for a session."""
        rows = self.conn.execute("""
            SELECT * FROM token_metrics
            WHERE session_id = ? AND tenant_id = ?
            ORDER BY timestamp_utc DESC
            LIMIT ?
        """, (session_id, tenant_id, limit)).fetchall()
        
        # Reconstruct LearningEvent objects (or return raw dicts)
        return [dict(row) for row in rows]
    
    # ... implement other abstract methods similarly ...
    
    async def close(self):
        """Close connection."""
        self.conn.close()


class PostgresMetricsDB(TokenMetricsDB):
    """PostgreSQL backend (optional, future)."""
    
    def __init__(self, db_uri: str):
        # Use asyncpg or psycopg2 here
        raise NotImplementedError("PostgreSQL backend in Phase 2.5")
    
    async def insert_token_metrics(self, event: LearningEvent) -> str:
        raise NotImplementedError()
    
    # ... etc
```

### Task 2B: Create DB Factory

**File:** `core/learning/token_metrics_db_factory.py` (NEW, ~80 LOC)

```python
"""Factory for token metrics DB backend."""

import os
from pathlib import Path
from typing import Optional
from core.learning.token_metrics_db import SqliteMetricsDB, TokenMetricsDB

def create_metrics_db(
    tenant_id: str = "default",
    config: Optional[dict] = None,
) -> TokenMetricsDB:
    """Create appropriate DB backend based on config/env.
    
    Precedence:
    1. env CORVIN_METRICS_DB_URI
    2. config["metrics_db_uri"]
    3. default: ~/.corvin/tenants/{tenant_id}/global/metrics.db
    """
    # 1. Check env
    db_uri = os.getenv("CORVIN_METRICS_DB_URI")
    
    # 2. Check config
    if not db_uri and config:
        db_uri = config.get("metrics_db_uri")
    
    # 3. Default
    if not db_uri:
        from core.corvin_core import tenant_home
        tenant = tenant_home(tenant_id)
        db_path = tenant / "global" / "metrics.db"
        db_uri = f"sqlite:///{db_path}"
    
    # 4. Instantiate backend
    if "postgresql" in db_uri or "postgres" in db_uri:
        # from core.learning.token_metrics_db import PostgresMetricsDB
        # return PostgresMetricsDB(db_uri)
        raise NotImplementedError("PostgreSQL in Phase 2.5")
    else:
        return SqliteMetricsDB(db_uri)
```

### Task 2C: Upgrade TokenMetricsStore

**File:** `core/learning/token_metrics_store.py` → MODIFY

**Changes (around line 15):**
```python
class TokenMetricsStore:
    """Persistence layer for token measurements.
    
    Now DB-backed with in-memory cache for write-through.
    """
    
    def __init__(self, event_emitter: EventEmitter, db: Optional[TokenMetricsDB] = None):
        """Initialize store.
        
        Args:
            event_emitter: EventEmitter for audit chain
            db: TokenMetricsDB backend (if None, cache-only for tests)
        """
        self.event_emitter = event_emitter
        self.db = db
        self._cache: dict[str, LearningEvent] = {}  # Write-through cache
    
    async def write_token_metrics(
        self,
        counter,
        tenant_id: str,
        instance_id: str,
        session_id: str,
        user_id: Optional[str] = None,
        skill_name: Optional[str] = None,
    ) -> str:
        """Write to EventEmitter + DB backend."""
        event = counter.to_event(
            tenant_id=tenant_id,
            instance_id=instance_id,
            session_id=session_id,
            user_id=user_id,
            skill_name=skill_name,
        )
        
        # 1. Write to audit chain
        self.event_emitter.emit(event)
        
        # 2. Write to DB backend (if available)
        if self.db:
            try:
                event_id = await self.db.insert_token_metrics(event)
            except Exception as e:
                # Log but don't fail
                import logging
                logging.warning(f"DB write failed: {e}")
                event_id = event.event_id
        else:
            event_id = event.event_id
        
        # 3. Cache for fast access
        self._cache[event.event_id] = event
        
        return event_id
    
    async def query_by_session(self, session_id: str, tenant_id: str, limit: int = 1000):
        """Fetch from DB or cache."""
        if self.db:
            return await self.db.query_by_session(session_id, tenant_id, limit)
        else:
            # Fallback to cache
            return [e for e in self._cache.values() if e.session_id == session_id][:limit]
    
    async def summary(self, session_id: str, tenant_id: str) -> dict:
        """Get summary stats (from DB aggregation)."""
        if self.db:
            return await self.db.summary(session_id, tenant_id)
        else:
            # Fallback to in-memory aggregation
            events = await self.query_by_session(session_id, tenant_id)
            # ... aggregate in Python ...
```

### Task 2D: Update App Bootstrap

**File:** `core/console/corvin_console/app.py` → MODIFY (around line 63)

```python
from core.learning.token_metrics_db_factory import create_metrics_db
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.event_emitter import EventEmitter

# Add to lifespan or @app.on_event("startup"):
async def lifespan(app: FastAPI):
    # Startup
    try:
        # Get audit writer
        from core.compliance.corvin_compliance_reports.audit_writer import get_audit_writer
        audit_writer = get_audit_writer()
    except:
        audit_writer = None
    
    # Create EventEmitter
    from pathlib import Path
    from core.corvin_core import tenant_home
    tenant = tenant_home()
    event_emitter = EventEmitter(tenant_home=tenant, audit_writer=audit_writer)
    
    # Create DB backend
    db = create_metrics_db(tenant_id="default")
    
    # Create metrics store
    metrics_store = TokenMetricsStore(event_emitter, db)
    
    # Store in app state
    app.state.metrics_store = metrics_store
    
    yield
    
    # Cleanup
    await db.close()

app = FastAPI(lifespan=lifespan)
```

### Task 2E: Create K=2 Tests

**File:** `tests/unit/test_token_metrics_db_k2.py` (NEW, ~200 LOC)

```python
"""Test TokenMetricsDB SQLite backend."""

import pytest
from datetime import datetime
from core.learning.token_metrics_db import SqliteMetricsDB
from core.learning.event_schema import LearningEvent, LearningEventType, TokenMetricsPayload

@pytest.fixture
def db():
    """In-memory SQLite for tests."""
    db = SqliteMetricsDB("sqlite:///:memory:")
    yield db
    # Cleanup happens automatically

@pytest.mark.asyncio
async def test_insert_and_query(db):
    """Test insert and retrieval."""
    # Create event
    event = LearningEvent(
        event_type=LearningEventType.TOKEN_METRICS,
        tenant_id="default",
        instance_id="inst1",
        session_id="s1",
        timestamp_utc=datetime.utcnow(),
        payload={
            "token_metrics": {
                "turn_id": "t1",
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tokens": 1500,
                "engine": "claude",
                "engine_tier": "cloud",
                "baseline_tokens": 2000,
                "savings_tokens": 500,
                "savings_percent": 25.0,
                "task_type": "code",
            }
        },
    )
    
    # Insert
    event_id = await db.insert_token_metrics(event)
    assert event_id == event.event_id
    
    # Query
    results = await db.query_by_session("s1", "default")
    assert len(results) == 1
    assert results[0]["turn_id"] == "t1"
    assert results[0]["total_tokens"] == 1500

@pytest.mark.asyncio
async def test_persistence():
    """Test data survives close/reopen."""
    db1 = SqliteMetricsDB("sqlite:///test_metrics_k2.db")
    
    event = LearningEvent(...)  # As above
    await db1.insert_token_metrics(event)
    await db1.close()
    
    # Reopen
    db2 = SqliteMetricsDB("sqlite:///test_metrics_k2.db")
    results = await db2.query_by_session("s1", "default")
    assert len(results) == 1
    await db2.close()
    
    # Cleanup
    import os
    os.remove("test_metrics_k2.db")
```

### K=2 Definition of Done

- [ ] TokenMetricsDB base class + SqliteMetricsDB implemented
- [ ] DB schema created (11 columns + 3 indexes)
- [ ] DB factory implemented (env/config/default precedence)
- [ ] TokenMetricsStore upgraded to DB-backed
- [ ] App bootstrap initializes DB on startup
- [ ] K=2 tests pass (insert, query, persistence)
- [ ] No Phase 1 tests broken
- [ ] Audit chain still fires for every write (verify in test)

---

## K=3: Console Panel (React)

### Task 3A: Create Pages & Components

**Files to create:**

1. `core/console/corvin_console/web-next/src/pages/vibe-metrics.tsx` (NEW, 20 LOC)
2. `core/console/corvin_console/web-next/src/components/VibeMetrics/Dashboard.tsx` (NEW, 150 LOC)
3. `core/console/corvin_console/web-next/src/components/VibeMetrics/TokenChart.tsx` (NEW, 80 LOC)
4. `core/console/corvin_console/web-next/src/components/VibeMetrics/SavingsCard.tsx` (NEW, 60 LOC)
5. `core/console/corvin_console/web-next/src/components/VibeMetrics/SubsystemBreakdown.tsx` (NEW, 80 LOC)
6. `core/console/corvin_console/web-next/src/components/VibeMetrics/TaskTypeTable.tsx` (NEW, 100 LOC)
7. `core/console/corvin_console/web-next/src/components/VibeMetrics/useLiveMetrics.ts` (NEW, 60 LOC)

**Skeleton for Dashboard.tsx:**

```tsx
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useLiveMetrics } from "./useLiveMetrics";
import TokenChart from "./TokenChart";
import SavingsCard from "./SavingsCard";
import SubsystemBreakdown from "./SubsystemBreakdown";
import TaskTypeTable from "./TaskTypeTable";

export default function Dashboard() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const { metrics, isLoading, error } = useLiveMetrics(sessionId);
  
  // Auto-detect current session from URL or state
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setSessionId(params.get("session") || localStorage.getItem("current_session") || "");
  }, []);
  
  if (isLoading) return <Loader2 className="animate-spin" />;
  if (error) return <div className="text-red-500">Error: {error}</div>;
  if (!metrics) return <div>No metrics yet</div>;
  
  return (
    <div className="p-8 bg-gradient-to-br from-slate-900 to-slate-800 min-h-screen text-white">
      <h1 className="text-3xl font-bold mb-8">Token Metrics</h1>
      
      {/* Session selector (optional) */}
      <div className="mb-8">
        <input 
          type="text"
          value={sessionId || ""}
          onChange={(e) => setSessionId(e.target.value)}
          placeholder="Session ID"
          className="px-4 py-2 rounded bg-slate-700 text-white"
        />
      </div>
      
      {/* Stats row */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <SavingsCard value={metrics.savings_percent} label="Savings %" />
        <StatCard value={metrics.total_tokens} label="Total Tokens" />
        <StatCard value={metrics.turn_count} label="Turns" />
        <StatCard value={metrics.avg_tokens_per_turn} label="Avg/Turn" />
      </div>
      
      {/* Charts */}
      <div className="grid grid-cols-2 gap-6 mb-8">
        <TokenChart data={metrics.turns} />
        <SubsystemBreakdown data={metrics.subsystems} />
      </div>
      
      {/* Table */}
      <TaskTypeTable data={metrics.by_task_type} />
    </div>
  );
}

function StatCard({ value, label }: { value: number | string; label: string }) {
  return (
    <div className="bg-slate-700 p-4 rounded shadow-lg">
      <div className="text-sm text-slate-400">{label}</div>
      <div className="text-2xl font-bold">{value}</div>
    </div>
  );
}
```

**Skeleton for useLiveMetrics.ts:**

```ts
import { useEffect, useState } from "react";

export function useLiveMetrics(sessionId: string | null) {
  const [metrics, setMetrics] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  useEffect(() => {
    if (!sessionId) {
      setIsLoading(false);
      return;
    }
    
    const fetch_metrics = async () => {
      try {
        const resp = await fetch(`/v1/console/metrics/session/${sessionId}/summary`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        setMetrics(data);
        setError(null);
      } catch (e) {
        setError(String(e));
      } finally {
        setIsLoading(false);
      }
    };
    
    fetch_metrics();
    const interval = setInterval(fetch_metrics, 5000);  // Poll every 5s
    return () => clearInterval(interval);
  }, [sessionId]);
  
  return { metrics, isLoading, error };
}
```

### Task 3B: Register Panel

**File:** `core/console/corvin_console/web-next/src/panels/registry.tsx` → MODIFY (line ~33)

```tsx
import { VibeMetricsPage } from "@/lazy-pages";  // Add import

export const PANELS: ConsolePanel[] = [
  // ... existing panels ...
  rc("vibe-metrics", "Token Metrics", VibeMetricsPage),  // Add before the closing ]
];
```

### Task 3C: Export from Lazy Pages

**File:** `core/console/corvin_console/web-next/src/lazy-pages/index.ts` → MODIFY

```ts
export { default as VibeMetricsPage } from "../pages/vibe-metrics";
```

### Task 3D: Create K=3 Tests

**File:** `tests/e2e/console-vibe-metrics.spec.ts` (NEW, ~120 LOC, Playwright)

```typescript
import { test, expect } from "@playwright/test";

test.describe("VibeMetrics Panel", () => {
  test.beforeEach(async ({ page }) => {
    // Login / setup
    await page.goto("/console");
  });
  
  test("panel loads with session data", async ({ page }) => {
    await page.goto("/console/vibe-metrics");
    
    // Wait for heading
    await expect(page.locator("text=Token Metrics")).toBeVisible({ timeout: 5000 });
    
    // Check stat cards
    await expect(page.locator("text=Total Tokens")).toBeVisible();
    await expect(page.locator("text=Savings")).toBeVisible();
  });
  
  test("real-time updates via polling", async ({ page }) => {
    await page.goto("/console/vibe-metrics");
    
    const tokenCount = page.locator("[data-testid=total-tokens]");
    const initialText = await tokenCount.textContent();
    
    // Wait for poll (5s)
    await page.waitForTimeout(6000);
    
    // Value should update (if new metrics arrived)
    // Note: may not change if no new turns ran
  });
  
  test("chart renders", async ({ page }) => {
    await page.goto("/console/vibe-metrics");
    
    // Recharts uses SVG with role="presentation"
    await expect(page.locator("[role='presentation']")).toBeVisible();
  });
});
```

### K=3 Definition of Done

- [ ] React components created (Dashboard + 6 children)
- [ ] Panel registered in registry.tsx
- [ ] Export added to lazy-pages/index.ts
- [ ] Styling: dark mode, responsive layout
- [ ] useLiveMetrics hook implemented
- [ ] Real-time polling works (verify in browser)
- [ ] K=3 E2E tests pass
- [ ] No Phase 1 tests broken

---

## K=4: API Endpoints

### Task 4A: Create Metrics Routes

**File:** `core/console/corvin_console/routes/metrics.py` (NEW, ~300 LOC)

```python
"""REST API for token metrics."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Annotated, Optional
from datetime import datetime, timedelta

from .. import auth as session_auth
from ..deps import require_session
from core.learning.token_metrics_store import TokenMetricsStore

router = APIRouter(prefix="/metrics")

class MetricsSummary(BaseModel):
    """Summary stats for a session."""
    turn_count: int
    total_tokens: int
    baseline_tokens: int
    savings_tokens: int
    savings_percent: float
    avg_tokens_per_turn: float
    subsystems: dict
    by_task_type: dict

class TurnMetrics(BaseModel):
    """Individual turn metrics."""
    turn_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    engine: str
    timestamp_utc: str
    task_type: Optional[str] = None
    outcome_quality: Optional[str] = None

# DI: inject metrics_store from app state
async def get_metrics_store(request) -> TokenMetricsStore:
    return request.app.state.metrics_store

@router.get("/session/{session_id}/summary")
async def get_session_summary(
    session_id: str,
    rec: Annotated[SessionRecord, Depends(require_session)],
    metrics_store: Annotated[TokenMetricsStore, Depends(get_metrics_store)],
) -> MetricsSummary:
    """Fetch summary stats for a session."""
    # Verify ownership
    if rec.session_id != session_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Query (tenant-isolated)
    summary = await metrics_store.summary(session_id, rec.tenant_id)
    return MetricsSummary(**summary)

@router.get("/session/{session_id}/turns")
async def get_session_turns(
    session_id: str,
    rec: Annotated[SessionRecord, Depends(require_session)],
    limit: int = Query(100, ge=1, le=1000),
    metrics_store: Annotated[TokenMetricsStore, Depends(get_metrics_store)],
) -> list[TurnMetrics]:
    """Fetch individual turn metrics."""
    if rec.session_id != session_id:
        raise HTTPException(status_code=403)
    
    events = await metrics_store.query_by_session(session_id, rec.tenant_id, limit)
    
    turns = []
    for event in events:
        metrics = event.payload.get("token_metrics", {})
        turns.append(TurnMetrics(
            turn_id=metrics.get("turn_id", ""),
            input_tokens=metrics.get("input_tokens", 0),
            output_tokens=metrics.get("output_tokens", 0),
            total_tokens=metrics.get("total_tokens", 0),
            engine=metrics.get("engine", ""),
            timestamp_utc=event.timestamp_utc.isoformat(),
            task_type=metrics.get("task_type"),
            outcome_quality=metrics.get("outcome_quality"),
        ))
    
    return turns

@router.get("/stats")
async def get_global_stats(
    rec: Annotated[SessionRecord, Depends(require_session)],
    since: Optional[datetime] = Query(None),
    metrics_store: Annotated[TokenMetricsStore, Depends(get_metrics_store)],
) -> dict:
    """Fetch aggregate stats across all sessions (admin/owner only)."""
    if rec.role not in ["owner", "admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Query: last 30 days (or custom range)
    end = datetime.utcnow()
    start = since or (end - timedelta(days=30))
    
    events = await metrics_store.query_by_timespan(rec.tenant_id, start, end)
    
    # Aggregate
    total_tokens = sum(e.payload["token_metrics"].get("total_tokens", 0) for e in events)
    turn_count = len(events)
    
    return {
        "turn_count": turn_count,
        "total_tokens": total_tokens,
        "avg_tokens_per_turn": total_tokens / turn_count if turn_count > 0 else 0,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
    }
```

### Task 4B: Add DI Helper

**File:** `core/console/corvin_console/deps.py` → MODIFY

```python
async def get_metrics_store(request: Request) -> TokenMetricsStore:
    """Inject metrics store from app state."""
    return request.app.state.metrics_store
```

### Task 4C: Register Routes

**File:** `core/console/corvin_console/app.py` → MODIFY (around line 65)

```python
from .routes import metrics as metrics_route

# Add to app setup (after FastAPI instantiation):
app.include_router(metrics_route.router, prefix="/v1/console", tags=["metrics"])
```

### Task 4D: Create K=4 Tests

**File:** `tests/unit/test_metrics_api_k4.py` (NEW, ~180 LOC)

```python
"""Test metrics REST API."""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime
from core.console.corvin_console.app import app
from core.learning.token_instrumentation import TokenCounter

client = TestClient(app)

@pytest.fixture
def auth_headers():
    """Valid auth headers (mocked)."""
    return {"Authorization": "Bearer mock_token"}

def test_get_session_summary_requires_auth():
    """Unauthenticated request returns 401."""
    response = client.get("/v1/console/metrics/session/s1/summary")
    assert response.status_code in [401, 403]

def test_get_session_summary_forbids_other_user(auth_headers):
    """User cannot access other user's session."""
    # This test requires mocking SessionRecord auth
    # Pseudocode:
    response = client.get(
        "/v1/console/metrics/session/s1/summary",
        headers=auth_headers,
    )
    # Should return 403 if user doesn't own s1
    # (requires auth mock implementation)

@pytest.mark.asyncio
async def test_get_session_summary_success():
    """Authorized user retrieves summary."""
    # Setup: write metrics
    store = app.state.metrics_store
    
    counter = TokenCounter(turn_id="t1", engine="claude")
    counter.record_llm_call(1000, 500)
    counter.baseline_tokens = 2000
    counter.finalize()
    
    await store.write_token_metrics(
        counter,
        tenant_id="default",
        instance_id="inst1",
        session_id="s1",
    )
    
    # Query (with mock auth)
    # response = client.get("/v1/console/metrics/session/s1/summary", headers=auth_headers)
    # assert response.status_code == 200
    # data = response.json()
    # assert data["turn_count"] == 1
    # assert data["total_tokens"] == 1500
```

### K=4 Definition of Done

- [ ] Metrics routes file created (~300 LOC)
- [ ] 5 GET endpoints implemented (summary, turns, stats, by-task-type, by-subsystem)
- [ ] Auth checks in place (SessionRecord ownership verification)
- [ ] Tenant isolation (all queries filtered by tenant_id)
- [ ] DI for metrics_store working
- [ ] Routes registered in app.py
- [ ] K=4 API tests pass
- [ ] Console panel can fetch data via `/api/metrics/session/{id}/summary`
- [ ] No Phase 1 tests broken

---

## Dependency Tree

```
K=1 (WorkerEngine)
  → K=2 (DB Backend)
    → K=3 (Console Panel)
      → K=4 (API Endpoints)
```

Each K depends on previous K being complete.

---

## Quick Start Command List

```bash
# K=1: Find WorkerEngine
grep -r "async def run" /home/shumway/projects/CorvinOS/core --include="*.py" | grep -i engine

# K=1: Run test
pytest tests/unit/test_token_instrumentation_k1_live.py -v

# K=2: Run test
pytest tests/unit/test_token_metrics_db_k2.py -v

# K=3: Run E2E test
npm run test:e2e -- console-vibe-metrics.spec.ts

# K=4: Run API test
pytest tests/unit/test_metrics_api_k4.py -v

# Integration: Run console, check panel
npm run dev  # In web-next directory
# Then open http://localhost:5173/console/vibe-metrics
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `TokenMetricsStore not injected into WorkerEngine` | Add to app bootstrap (Task 1C) |
| `DB file not created` | Check `~/.corvin/tenants/_default/global/` has write perms |
| `Console panel doesn't load` | Check panel registered in registry.tsx + export in lazy-pages |
| `API returns 403 Forbidden` | Verify auth headers + SessionRecord ownership logic |
| `Metrics not appearing in panel` | Check useLiveMetrics polling interval + network tab for 200 responses |

---

## File Locations (Master List)

### K=1
- (TBD) WorkerEngine file
- `core/console/corvin_console/app.py` (modify for DI)
- `tests/unit/test_token_instrumentation_k1_live.py` (new)

### K=2
- `core/learning/token_metrics_db.py` (new)
- `core/learning/token_metrics_db_factory.py` (new)
- `core/learning/token_metrics_store.py` (modify)
- `core/console/corvin_console/app.py` (modify for DB init)
- `tests/unit/test_token_metrics_db_k2.py` (new)

### K=3
- `core/console/corvin_console/web-next/src/pages/vibe-metrics.tsx` (new)
- `core/console/corvin_console/web-next/src/components/VibeMetrics/{Dashboard,TokenChart,SavingsCard,SubsystemBreakdown,TaskTypeTable,useLiveMetrics}.{tsx,ts}` (new)
- `core/console/corvin_console/web-next/src/panels/registry.tsx` (modify)
- `core/console/corvin_console/web-next/src/lazy-pages/index.ts` (modify)
- `tests/e2e/console-vibe-metrics.spec.ts` (new)

### K=4
- `core/console/corvin_console/routes/metrics.py` (new)
- `core/console/corvin_console/deps.py` (modify)
- `core/console/corvin_console/app.py` (modify for routes)
- `tests/unit/test_metrics_api_k4.py` (new)

**Total: ~25 files (15 new, 10 modified)**
