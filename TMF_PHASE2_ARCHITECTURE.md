# Token Measurement Framework — Phase 2 Architecture

**Diagram-based reference for Phase 2 design.**

---

## Data Flow: End-to-End (Phase 2 Complete)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          WorkerEngine.run()                             │
│  (wherever LLM invocation happens — core/compute, core/gateway, etc)    │
│                                                                         │
│  1. START:  TokenInstrumentationHooks.on_worker_engine_start()          │
│             └─→ Create TokenCounter(turn_id, engine, tier)             │
│                                                                         │
│  2. SUBSYSTEMS: (throughout turn)                                       │
│             TokenInstrumentationHooks.on_subsystem_executed()           │
│             └─→ Record overhead (confidence, cache, skills)            │
│                                                                         │
│  3. LLM RESPONSE:                                                       │
│             response = await llm_api.complete(prompt)                   │
│             TokenInstrumentationHooks.on_llm_response()                 │
│             └─→ Record input_tokens, output_tokens from response        │
│                                                                         │
│  4. END:    TokenInstrumentationHooks.on_worker_engine_end()            │
│             └─→ Finalize counter, calculate latency                    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                  ↓
                        ┌─────────────────────┐
                        │   TokenCounter      │
                        │  (data payload)     │
                        │ ─────────────────── │
                        │ turn_id             │
                        │ input_tokens: 1000  │
                        │ output_tokens: 500  │
                        │ total_tokens: 1500  │
                        │ baseline_tokens:2000│
                        │ savings_tokens: 500 │
                        │ latency_ms: 350     │
                        │ task_type: "code"   │
                        │ subsystem_tokens: {…}│
                        └─────────────────────┘
                                  ↓
                    ┌──────────────────────────────┐
                    │  counter.to_event()          │
                    │  Creates LearningEvent       │
                    │  event_type: TOKEN_METRICS   │
                    │  payload: {token_metrics: …} │
                    └──────────────────────────────┘
                                  ↓
            ┌─────────────────────┴──────────────────────┐
            │                                            │
       ┌────▼─────┐                            ┌────────▼────────┐
       │ EventEmitter                          │ TokenMetricsStore
       │ (audit chain)                         │                 │
       │ ──────────                            │ K=2: DB-backed  │
       │ emit(event)                           └────────┬────────┘
       │   ↓                                            │
       │ write_audit_event()                           │
       │   ↓                                            │
       │ audit.jsonl                                    │
       │ (immutable, hash-chained)                      │
       └─────────────────────────────────────┬──────────┘
                                              │
                            ┌─────────────────▼──────────────────┐
                            │   EventStore (Phase 1)             │
                            │                                    │
                            │   Writes to disk (JSONL):          │
                            │   ~/.corvin/tenants/_default/      │
                            │   global/learning/events/          │
                            │   2024-01-15.jsonl                 │
                            └────────────────────────────────────┘
                                              │
                            ┌─────────────────▼──────────────────┐
                            │   TokenMetricsDB (Phase 2 NEW)     │
                            │                                    │
                            │   INSERT token_metrics (…)         │
                            │                                    │
                            │   ↓                                │
                            │   SQLite / PostgreSQL              │
                            │                                    │
                            │   ~/.corvin/tenants/_default/      │
                            │   global/metrics.db                │
                            │                                    │
                            │   Table: token_metrics             │
                            │   ├─ event_id (PK)                │
                            │   ├─ tenant_id (FK)               │
                            │   ├─ session_id                    │
                            │   ├─ turn_id (UNIQUE)              │
                            │   ├─ input_tokens                  │
                            │   ├─ output_tokens                 │
                            │   ├─ total_tokens                  │
                            │   ├─ engine                        │
                            │   ├─ baseline_tokens               │
                            │   ├─ savings_percent               │
                            │   ├─ task_type                     │
                            │   ├─ timestamp_utc (IDX)           │
                            │   └─ [+ 10 more fields]            │
                            │                                    │
                            │   Indexes:                         │
                            │   ├─ session_id, timestamp_utc     │
                            │   ├─ tenant_id, timestamp_utc      │
                            │   └─ task_type                     │
                            └────────────────────────────────────┘
```

---

## Console to DB Query Path (K=3 + K=4)

```
┌──────────────────────────────────────────────────────────────┐
│                  Browser / Console UI                        │
│                                                              │
│              VibeMetrics Dashboard Component                 │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 1. User navigates to /console/vibe-metrics            │  │
│  │                                                       │  │
│  │ 2. Dashboard calls useLiveMetrics(sessionId)          │  │
│  │    └─→ useEffect → fetch("/v1/console/metrics/…")    │  │
│  │                                                       │  │
│  │ 3. UI renders:                                        │  │
│  │    ├─ TokenChart (line graph of tokens/turn)         │  │
│  │    ├─ SavingsCard (% savings vs baseline)            │  │
│  │    ├─ SubsystemBreakdown (pie chart)                 │  │
│  │    └─ TaskTypeTable (by task_type aggregates)        │  │
│  │                                                       │  │
│  │ 4. Polling: fetch every 5 seconds                    │  │
│  └───────────────────────────────────────────────────────┘  │
│                          ↓                                   │
└──────────────────────────────────────────────────────────────┘
             HTTP GET /v1/console/metrics/session/s1/summary
             ├─ Header: Authorization: Bearer {token}
             ├─ Header: Accept: application/json
             └─ Auth context → SessionRecord (user_id, tenant_id)
                                  ↓
        ┌─────────────────────────────────────────────┐
        │  FastAPI Route Handler                      │
        │  (core/console/routes/metrics.py)           │
        │                                             │
        │  get_session_summary(session_id, rec)       │
        │  ├─ Verify: rec.session_id == session_id    │
        │  ├─ Check: rec.role in ["owner", "admin"]   │
        │  └─ Call: metrics_store.summary(…)          │
        │                                             │
        │  Returns: MetricsSummary {                  │
        │    turn_count: 42,                          │
        │    total_tokens: 63000,                     │
        │    savings_percent: 18.5,                   │
        │    subsystems: {…},                         │
        │    by_task_type: {…}                        │
        │  }                                          │
        └──────────────────┬──────────────────────────┘
                           ↓
        ┌─────────────────────────────────────────────┐
        │  TokenMetricsStore (K=2 Upgraded)           │
        │  (core/learning/token_metrics_store.py)     │
        │                                             │
        │  async summary(session_id, tenant_id)       │
        │  ├─ Query DB backend:                       │
        │  │  await self.db.summary(…)                │
        │  └─ Return aggregates                       │
        └──────────────────┬──────────────────────────┘
                           ↓
        ┌─────────────────────────────────────────────┐
        │  TokenMetricsDB Backend (K=2 NEW)           │
        │  (core/learning/token_metrics_db.py)        │
        │                                             │
        │  SqliteMetricsDB / PostgresMetricsDB        │
        │                                             │
        │  async summary(session_id, tenant_id)       │
        │  ├─ SELECT SUM(total_tokens), …             │
        │  │   FROM token_metrics                     │
        │  │   WHERE session_id = ? AND tenant_id = ? │
        │  │   GROUP BY task_type                     │
        │  └─ Return results                          │
        └──────────────────┬──────────────────────────┘
                           ↓
        ┌─────────────────────────────────────────────┐
        │  SQLite / PostgreSQL Database               │
        │  (metrics.db or remote postgres)            │
        │                                             │
        │  token_metrics table (11 columns)           │
        │  ├─ Filtered by: tenant_id + session_id     │
        │  └─ Indexes: (session_id, timestamp_utc)    │
        └─────────────────────────────────────────────┘
                           ↑
                    [Query Results]
                           │
             ┌─────────────▼──────────────┐
             │  HTTP 200 OK               │
             │  Content-Type: application/json
             │                            │
             │  {                         │
             │    "turn_count": 42,       │
             │    "total_tokens": 63000,  │
             │    "savings_percent": 18.5,│
             │    "subsystems": {...},    │
             │    "by_task_type": {...}   │
             │  }                         │
             └─────────────┬──────────────┘
                           │
        ┌──────────────────▼──────────────────┐
        │  Browser receives JSON response    │
        │                                    │
        │  useLiveMetrics hook:              │
        │  ├─ setMetrics(data)               │
        │  └─ Component re-renders           │
        │                                    │
        │  Charts/Cards update with values   │
        └────────────────────────────────────┘
```

---

## File Dependency Graph

```
Phase 1 (Existing)
├── event_schema.py (LearningEvent, TokenMetricsPayload)
├── event_emitter.py (EventEmitter.emit → audit chain)
├── event_persistence.py (EventStore disk I/O)
├── token_instrumentation.py (TokenCounter, hooks)
├── token_baseline.py (BaselineMetrics, ComparisonEngine)
└── token_metrics_aggregator.py (aggregation pipeline)

     ↓ (K=1 integration)

WorkerEngine
(core/compute/worker.py or similar)
├── Calls: TokenInstrumentationHooks.on_worker_engine_start()
├── Calls: TokenInstrumentationHooks.on_llm_response()
├── Calls: TokenInstrumentationHooks.on_subsystem_executed()
└── Calls: TokenInstrumentationHooks.on_worker_engine_end()

     ↓ (K=2 DB backend)

token_metrics_store.py (UPGRADED)
├── Now uses: TokenMetricsDB backend
├── Calls: db.insert_token_metrics(event)
└── Provides: query_by_session(), query_by_timespan(), summary()

     ↓ (K=2 new DB layer)

token_metrics_db.py (NEW)
├── Abstract: TokenMetricsDB
├── Impl: SqliteMetricsDB
│   └─ Uses: sqlite3 library
├── Impl: PostgresMetricsDB (future)
│   └─ Uses: asyncpg or psycopg2
└── Provides: INSERT, SELECT, aggregation queries

token_metrics_db_factory.py (NEW)
├── Creates: SqliteMetricsDB or PostgresMetricsDB
├── Logic: env var → config → default
└── Used in: app.py bootstrap

     ↓ (K=3 console panel)

web-next/src/pages/vibe-metrics.tsx (NEW)
└─→ web-next/src/components/VibeMetrics/ (NEW)
    ├── Dashboard.tsx (main container)
    ├── useLiveMetrics.ts (data fetching hook)
    ├── TokenChart.tsx (Recharts line chart)
    ├── SavingsCard.tsx (KPI card)
    ├── SubsystemBreakdown.tsx (pie chart)
    └── TaskTypeTable.tsx (table component)

web-next/src/panels/registry.tsx (MODIFIED)
└─→ Register: rc("vibe-metrics", "Token Metrics", VibeMetricsPage)

web-next/src/lazy-pages/index.ts (MODIFIED)
└─→ Export: VibeMetricsPage

     ↓ (K=4 API)

routes/metrics.py (NEW)
├── GET /metrics/session/{id}/summary
├── GET /metrics/session/{id}/turns
├── GET /metrics/session/{id}/by-task-type
├── GET /metrics/session/{id}/by-subsystem
└── GET /metrics/stats (global, admin-only)

deps.py (MODIFIED)
└─→ get_metrics_store() dependency

app.py (MODIFIED)
├── Initialize DB on startup
├── Inject metrics_store into app.state
└── Register metrics routes

```

---

## Deployment Diagram (Single Instance)

```
┌─────────────────────────────────────────────────────────────────┐
│                    CorvinOS Instance                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  ~/.corvin/tenants/_default/                            │  │
│  │                                                          │  │
│  │  ├─ global/                                             │  │
│  │  │  ├─ forge/audit.jsonl          ← Phase 1 audit chain │  │
│  │  │  ├─ learning/events/           ← Phase 1 disk backup │  │
│  │  │  │  ├─ 2024-01-15.jsonl                             │  │
│  │  │  │  └─ 2024-01-16.jsonl                             │  │
│  │  │  └─ metrics.db                 ← K=2 DB (NEW)       │  │
│  │  │                                                      │  │
│  │  ├─ sessions/                                           │  │
│  │  │  └─ s1/context.json   ← current_session_id          │  │
│  │  │                                                      │  │
│  │  └─ voice/                                              │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Console Web Server (FastAPI)                           │  │
│  │                                                          │  │
│  │  ┌─ lifespan():                                         │  │
│  │  │  ├─ db = create_metrics_db()  ← K=2 init            │  │
│  │  │  ├─ app.state.metrics_store = TokenMetricsStore()   │  │
│  │  │  └─ app.include_router(metrics_route)               │  │
│  │  │                                                     │  │
│  │  ├─ routes/metrics.py            ← K=4 API routes      │  │
│  │  │  ├─ GET /metrics/session/{id}/summary              │  │
│  │  │  ├─ GET /metrics/session/{id}/turns                │  │
│  │  │  └─ GET /metrics/stats                              │  │
│  │  │                                                     │  │
│  │  └─ SPA: /console/                                    │  │
│  │     ├─ panels/vibe-metrics        ← K=3 panel         │  │
│  │     └─ components/VibeMetrics/    ← K=3 components    │  │
│  │                                                         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  WorkerEngine (K=1 instrumented)                        │  │
│  │                                                          │  │
│  │  async def run(self, turn_input):                       │  │
│  │    counter = TokenInstrumentationHooks.on_worker_…()    │  │
│  │    response = await llm_api.complete(prompt)           │  │
│  │    TokenInstrumentationHooks.on_llm_response(…)         │  │
│  │    TokenInstrumentationHooks.on_worker_engine_end(…)    │  │
│  │    await metrics_store.write_token_metrics(…)           │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
          ↓ ↓ ↓
  [Each turn: metrics flow through both audit chain AND DB]
```

---

## Database Schema (SQLite)

```sql
CREATE TABLE token_metrics (
    -- IDs
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT UNIQUE NOT NULL,
    user_id TEXT,
    instance_id TEXT NOT NULL,
    skill_name TEXT,
    
    -- Token counts (core)
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    
    -- Baseline comparison
    baseline_tokens INTEGER,
    savings_tokens INTEGER,
    savings_percent REAL,
    
    -- Engine/Model info
    engine TEXT NOT NULL,      -- "claude", "gpt", etc.
    engine_tier TEXT,          -- "cloud", "local", "mocked"
    model_id TEXT,             -- "claude-3-opus-20240229"
    
    -- Task metadata
    task_type TEXT,            -- "code", "writing", "analysis"
    task_domain TEXT,          -- "web", "backend", "data"
    task_complexity TEXT,      -- "simple", "moderate", "complex"
    
    -- Outcome
    outcome_quality TEXT,      -- "good", "bad", "partial"
    required_followup BOOLEAN DEFAULT FALSE,
    
    -- Performance
    latency_ms INTEGER,
    iterations_count INTEGER DEFAULT 1,
    
    -- Subsystems (JSON blob)
    subsystem_tokens JSON,     -- {"confidence": 200, "cache": 50, "skills": 300}
    
    -- Timestamps
    timestamp_utc TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Audit
    audit_id TEXT,             -- reference to audit chain
    _persistence_fallback TEXT  -- error message if audit write failed
);

-- Indexes (for fast queries)
CREATE INDEX idx_session_timestamp
    ON token_metrics(session_id, timestamp_utc DESC);

CREATE INDEX idx_tenant_timestamp
    ON token_metrics(tenant_id, timestamp_utc DESC);

CREATE INDEX idx_task_type
    ON token_metrics(task_type);
```

---

## K-wise Completion Criteria

### K=1: WorkerEngine Integration
```
✓ WorkerEngine located and identified
✓ 4 hook calls inserted (start, llm_response, subsystem, end)
✓ TokenCounter flows through entire turn lifecycle
✓ Metrics persist to audit chain + disk (EventStore)
✓ E2E: Real turn → tokens recorded → verify in storage
✓ All Phase 1 tests still pass
```

### K=2: DB Backend
```
✓ TokenMetricsDB base class with async interface
✓ SqliteMetricsDB implementation (11 columns + 3 indexes)
✓ DB factory (env/config/default precedence)
✓ TokenMetricsStore upgraded (DB-backed queries)
✓ App bootstrap initializes DB on startup
✓ Audit chain still fires for every write
✓ DB persistence verified (close/reopen test)
✓ All Phase 1 + K=1 tests still pass
```

### K=3: Console Panel
```
✓ React components created (Dashboard + 6 children)
✓ useLiveMetrics hook fetches from API
✓ Panel registered in registry.tsx
✓ Export added to lazy-pages/index.ts
✓ Dark mode styling applied
✓ Real-time polling works (5s interval)
✓ Charts render (Recharts integration)
✓ Session/tenant isolation via auth headers
✓ E2E tests confirm rendering + polling
✓ All previous tests still pass
```

### K=4: API Endpoints
```
✓ /v1/console/metrics/session/{id}/summary       [GET]
✓ /v1/console/metrics/session/{id}/turns         [GET]
✓ /v1/console/metrics/session/{id}/by-task-type  [GET]
✓ /v1/console/metrics/session/{id}/by-subsystem  [GET]
✓ /v1/console/metrics/stats                      [GET]  (admin-only)
✓ Auth checks: SessionRecord ownership verified
✓ Tenant isolation: all queries WHERE tenant_id = ?
✓ DI: get_metrics_store() injected
✓ Routes registered in app.py
✓ API tests pass (auth, isolation, data correctness)
✓ Console panel can fetch and display data
✓ All previous tests still pass
```

---

## Environment Variables (K=2+)

| Var | Purpose | Default | Example |
|-----|---------|---------|---------|
| `CORVIN_METRICS_DB_URI` | Metrics DB backend | (not set; uses default) | `sqlite:///~/.corvin/metrics.db` |
| | | | `postgresql://user:pass@localhost/corvin_metrics` |
| `CORVIN_TENANT_ID` | Tenant for metrics (existing) | `_default` | `_default` |

---

## Migration / Upgrade Path

### Phase 1 → Phase 2 (Zero Downtime)

```
1. Deploy K=1: WorkerEngine hooks inserted
   ├─ Existing metrics still flow to disk (EventStore)
   ├─ New: Also flow to in-memory cache (Phase 1 TokenMetricsStore)
   └─ No DB writes yet

2. Deploy K=2: DB backend initialized
   ├─ First run: create metrics.db schema
   ├─ Backfill: (optional) import historical events from JSONL
   ├─ New turns: write to both audit chain AND DB
   └─ Queries: use DB if available, fallback to cache

3. Deploy K=3: Console panel enabled
   ├─ New panel registered in nav
   ├─ Polling API endpoints (not yet live)
   └─ No breaking changes to existing routes

4. Deploy K=4: API endpoints live
   ├─ Panel starts fetching real data
   ├─ Auth checks enforce tenant isolation
   └─ All K=1-K=3 features now work end-to-end
```

**Rollback:** Disable `vibe-metrics` panel in registry.tsx; continue storing to both audit + DB (safe).

---

## Testing Strategy

### K=1 Tests
```
├─ Unit: TokenCounter lifecycle (create → finalize)
├─ Unit: TokenInstrumentationHooks all 4 methods
├─ Integration: WorkerEngine → metrics_store.write()
└─ E2E: Real turn in console → metrics appear in storage
```

### K=2 Tests
```
├─ Unit: SqliteMetricsDB insert/query/aggregate
├─ Unit: DB factory (env/config/default)
├─ Integration: TokenMetricsStore + DB backend
├─ Persistence: Close/reopen DB, verify data
└─ Audit Chain: Verify write_audit_event still fires
```

### K=3 Tests
```
├─ Component: Dashboard renders without errors
├─ Component: useLiveMetrics hook fetches data
├─ E2E: Navigate to panel, wait for load, verify charts
├─ E2E: Polling interval fires (verify in network tab)
└─ Theme: Dark mode colors applied
```

### K=4 Tests
```
├─ Unit: All 5 API routes return correct JSON
├─ Unit: Auth checks block unauthorized access
├─ Unit: Tenant isolation (WHERE clause) verified
├─ Integration: Panel ↔ API ↔ DB end-to-end
└─ Load: 1000s of metrics returned in <100ms
```

---

## Success Metrics (Phase 2 Done)

| Metric | Target | Verification |
|--------|--------|--------------|
| E2E latency (DB query) | <100ms | `pytest -k test_metrics_query_performance` |
| Turn throughput | >100 turns/sec | Load test with mock WorkerEngine |
| Panel responsiveness | <1s load | Chrome DevTools network tab |
| Data accuracy | 100% match | Unit tests for aggregation logic |
| Test coverage | ≥90% | `pytest --cov core/learning` |
| Docs completeness | ✅ | Phase 2 completion report + API reference |

---

## Troubleshooting Matrix

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| "metrics_store not injected" | K=1: DI missing in app.py | Add to lifespan() startup |
| "DB file not found" | K=2: Wrong path or no perms | Check `~/.corvin/tenants/_default/global/` |
| "Table doesn't exist" | K=2: Schema not created | Call `_init_schema()` in SqliteMetricsDB.__init__() |
| "Panel doesn't render" | K=3: Not registered in registry | Add `rc("vibe-metrics", …)` to PANELS |
| "API returns 404" | K=4: Routes not included | Check `app.include_router()` in app.py |
| "Auth error 403" | K=4: SessionRecord mismatch | Verify `rec.session_id == session_id` |
| "Panel shows no data" | K=4: Polling not working | Check network tab for `/v1/console/metrics/…` requests |

---

## Next: Phase 3 (Future)

Phase 3 will extend Phase 2 with:

```
K=5: Advanced Baselines
     ├─ Segment by engine, model, user persona
     └─ ML-driven baseline prediction

K=6: Closed-Loop Learning
     ├─ User feedback (rate quality)
     └─ Confidence scoring refinement

K=7: Cost Attribution
     ├─ Map tokens → cost per model
     └─ Subsystem cost breakdown

K=8: Anomaly Detection
     ├─ 2σ outlier alerts
     └─ Token spike investigation
```

All built on Phase 2's DB + API foundation.
