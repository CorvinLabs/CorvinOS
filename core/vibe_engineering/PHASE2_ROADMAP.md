# Vibe Engineering Dashboard Phase 2 — Real Data Wiring

**Status:** Planned  
**Dependencies:** ADR-0314 (Learning Events), ADR-0399 (Context Pipeline v2)  
**Timeline:** 2–3 weeks (after ContextBus event schema stabilization)

---

## Scope

Replace mock data in `/vibe-engineering/state` and `/vibe-engineering/config` with **live data from Corvin Brain**:
- Active task state from ExecutionContext
- Worker subsystem health from HealthMonitor (real latency, error counts)
- Decision queue from DecisionBus (real decisions with confidence scores)
- Context layers from ContextBus (real Original Context + Pipeline Context tiers)
- Talent metrics from LearningEngine (real scores, sparkline history)

Estimated effort: **~40 LoC backend** + **~20 LoC frontend refactor** + **tests**.

---

## Implementation Steps

### Step 1: ContextBus Event Aggregator

**File:** `core/context_pipeline/vibe_state_aggregator.py`

```python
class VibeStateAggregator:
    """Real-time aggregation of Brain state from ContextBus + other sources."""
    
    def __init__(self, context_bus, health_monitor, learning_engine):
        self.context_bus = context_bus
        self.health_monitor = health_monitor
        self.learning_engine = learning_engine
        self._state_cache = {}
    
    def get_current_state(self) -> dict:
        """Return live Brain state (brain status, context, talent metrics)."""
        return {
            "active_task": self._get_active_task(),
            "workers": self._get_worker_status(),
            "decision_queue": self._get_decisions(limit=1),
            "recent_decisions": self._get_decisions(limit=5),
            "original_context": self._get_original_context(),
            "pipeline_context": self._get_pipeline_context(),
            "talent": self._get_talent_metrics(),
        }
    
    def _get_active_task(self) -> dict:
        """From ExecutionContext: current task + elapsed time."""
        # TODO: wire ExecutionContext
        pass
    
    def _get_worker_status(self) -> list[dict]:
        """From HealthMonitor: CostController, SafetyValidator, LoopEngineer, Orchestrator."""
        # TODO: map HealthMonitor subsystem states to status enum
        pass
    
    def _get_decisions(self, limit: int) -> list[dict]:
        """From DecisionBus: recent decisions with confidence."""
        # TODO: read decision log (last N)
        pass
    
    def _get_original_context(self) -> dict:
        """From ContextBus / audit log: immutable original context."""
        # TODO: read from Layer A (audit.jsonl)
        pass
    
    def _get_pipeline_context(self) -> dict:
        """From ContextBus: tier breakdown, entropy, recent additions."""
        # TODO: aggregate TIER_1/2/3 counts + entropy score
        pass
    
    def _get_talent_metrics(self) -> dict:
        """From LearningEngine: talent score, component breakdown, sparkline."""
        # TODO: call learning_engine.get_talent_metrics()
        pass
```

**Why:** Single source of truth for live state, decoupled from HTTP routing.

### Step 2: Backend Route Refactor

**File:** `core/console/corvin_console/routes/vibe_engineering.py`

Replace the `get_vibe_dashboard_state()` mock handler:

```python
@router.get("/state")
async def get_vibe_dashboard_state(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Live Brain state (real data from ContextBus + HealthMonitor + LearningEngine)."""
    try:
        aggregator = VibeStateAggregator(
            context_bus=get_context_bus(rec.tenant_id),  # TODO: inject
            health_monitor=get_health_monitor(rec.tenant_id),
            learning_engine=get_learning_engine(rec.tenant_id),
        )
        return aggregator.get_current_state()
    except Exception as exc:
        # Graceful degradation: return last known state + error flag
        return {"error": str(exc), "status": "unavailable"}
```

**Why:** Swaps mock data source without changing frontend contract.

### Step 3: Frontend Polling Resilience

**File:** `core/console/corvin_console/web-next/src/pages/vibe-engineering/hooks/useVibeData.ts`

Add fallback for when backend is unavailable:

```typescript
export function useVibeData(pollIntervalMs = 5000): VibeData {
  const [data, setData] = useState<VibeData>({ /* ... */ });

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [stateRes, configRes] = await Promise.all([
          fetch('/vibe-engineering/state'),
          fetch('/vibe-engineering/config'),
        ]);

        if (!stateRes.ok) {
          // Graceful degradation: keep showing last known state + warning
          setData(prev => ({
            ...prev,
            error: `API error: ${stateRes.status}`,
          }));
          return;
        }

        const state = await stateRes.json();
        if (state.error) {
          // Backend returned error flag (e.g., ContextBus unavailable)
          setData(prev => ({
            ...prev,
            error: state.error,
          }));
          return;
        }

        setData({ /* populate from state */ });
      } catch (err) {
        setData(prev => ({
          ...prev,
          error: err instanceof Error ? err.message : 'Unknown error',
        }));
      }
    };

    fetchData();
    const interval = setInterval(fetchData, pollIntervalMs);
    return () => clearInterval(interval);
  }, [pollIntervalMs]);

  return data;
}
```

**Why:** Prevents UI crashes when Brain is overloaded or context pipeline is unavailable.

### Step 4: LDD k=1–3 Validation (Phase 2)

**k=1: Mock → Real Data**
- Verify VibeStateAggregator builds without errors (integration test)
- Verify `/vibe-engineering/state` endpoint returns valid JSON shape

**k=2: Live Data in Dashboard**
- Start Console
- Open Dashboard
- Verify 3 columns show REAL values (not mock)
- Check that data updates every 5 seconds

**k=3: Error Resilience**
- Simulate ContextBus failure (inject exception in aggregator)
- Verify Dashboard shows graceful error, doesn't crash
- Verify "last known state" is preserved for ~30 seconds

---

## Phase 2 Alternatives Considered

### A1: WebSocket Subscriptions
**Rejected for Phase 2.** REST polling sufficient for single operator. WebSocket adds complexity (connection state, reconnection logic). Revisit for multi-operator rollout.

### A2: GraphQL API
**Rejected.** Overkill. Simple JSON REST is faster to implement and iterate.

### A3: Direct ContextBus access in Frontend
**Rejected.** Frontend must never reach internal Brain state directly. Backend aggregator enforces isolation.

---

## Phase 2 Open Items

- [ ] Where does ExecutionContext live? (integration point TBD)
- [ ] What is the ContextBus event schema? (awaiting ADR-0314 finalization)
- [ ] LearningEngine API? (do we call `get_talent_metrics()` or subscribe to events?)
- [ ] Error handling: should `/state` return HTTP 503 or 200 + error flag? (chose 200 + error flag for graceful degradation)

---

## Rollout Plan

**Week 1:** Implement VibeStateAggregator + wire ContextBus  
**Week 2:** Backend route refactor + frontend resilience  
**Week 3:** LDD validation + operator acceptance testing  

Once stable → keep as Phase 2 stable, plan Phase 3 (feedback loop, quality gate writeable).

---

## Success Criteria

- [ ] Dashboard shows LIVE worker status (not static mock)
- [ ] Decision queue updates in real time (new decisions appear without page reload)
- [ ] Entropy gauge reflects actual context pipeline state
- [ ] Talent score changes as brain learns
- [ ] No errors when brain is under heavy load
- [ ] Graceful degradation if ContextBus unavailable

---

## Related Decisions

- **ADR-0400:** Phase 1 (mock data MVP)
- **ADR-0314:** Learning infrastructure (event schema Phase 2 depends on)
- **ADR-0399:** Context pipeline v2 (data source for real state)

**Next ADR:** ADR-0401 will document Phase 2 real-data architecture once implemented.
