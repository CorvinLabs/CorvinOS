---
id: ADR-0324
status: proposed
depends_on: [ADR-0314, ADR-0321, ADR-0323]
related: [ADR-0322, ADR-0325, ADR-0326]
supersedes: []
paths:
  - core/learning/performance_aggregator.py
  - core/learning/confidence_intervals.py
docs:
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md
commits: []
---

# ADR-0324 — Performance Aggregation Pipeline

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Author:** Claude Code  

---

## Context

### Problem
Gap 2 (tool ranking) and Gap 3 (skill attribution) both need aggregated metrics:
- Tool success rates, latency percentiles, cost distributions
- Skill success rates, outcome histories

Currently, these metrics are computed on-demand (expensive O(n) queries). We need:
1. Efficient aggregation (batch queries, caching)
2. Temporal windows (7-day, 30-day, all-time)
3. Confidence intervals (Bayesian smoothing for cold-start)
4. Trending (improving vs declining)

### Gap
**Gap 4: Tool/Skill Success Rates Unknown** — provides foundation for fair skill grading (Gap 3), enables WEIGHTED attribution (Gap 3), unblocks metrics-driven optimization.

---

## Decision

### Conceptual Level
**Principle:** Performance aggregation is a **background pipeline**, not a critical-path operation. We batch compute metrics periodically (hourly) and cache results. On-demand queries use cache; only recompute if stale.

### Structural Level
**New components:**
1. **PerformanceAggregator:** Queries EventStore in batches, computes metrics for tools/skills
2. **ConfidenceIntervalCalculator:** Bayesian smoothing (Beta-Binomial) for success rates
3. **AggregationScheduler:** Runs aggregation hourly; notifies subsystems of updates
4. **MetricsCache:** Stores aggregated metrics with TTL

**Aggregation granularity:**
- Per tool (by tool_id)
- Per skill (by skill_id)
- Per (tool_id, task_type)
- Per (skill_id, task_type)

**Temporal windows:**
- 7-day (default, for Gap 2/3)
- 30-day (for trending)
- All-time (for comparison)

**Confidence intervals:**
- 95% Bayesian interval using Beta-Binomial conjugate prior
- Justification: Handles cold-start (small sample counts)

### Implementation Level

```python
@dataclass(frozen=True)
class ConfidenceInterval:
    """Bayesian confidence interval for success rate."""
    lower: float  # 2.5th percentile
    mean: float   # Point estimate
    upper: float  # 97.5th percentile
    samples: int  # Sample count
    prior_successes: int = 2  # Beta prior (regularization)
    prior_failures: int = 2


class ConfidenceIntervalCalculator:
    """Computes Bayesian confidence intervals for success rates."""
    
    @staticmethod
    def compute_interval(
        successes: int,
        failures: int,
        prior_successes: int = 2,
        prior_failures: int = 2,
        credible_level: float = 0.95,
    ) -> ConfidenceInterval:
        """Compute 95% credible interval using Beta-Binomial.
        
        Prior: Beta(prior_successes, prior_failures) [default: Beta(2,2) ~ uniform]
        Posterior: Beta(successes + prior_successes, failures + prior_failures)
        """
        from scipy.stats import beta
        
        total = successes + failures
        
        # Posterior distribution
        a = successes + prior_successes
        b = failures + prior_failures
        
        # Point estimate (posterior mean)
        mean_rate = a / (a + b)
        
        # Credible interval
        alpha = 1 - credible_level
        lower = beta.ppf(alpha / 2, a, b)
        upper = beta.ppf(1 - alpha / 2, a, b)
        
        return ConfidenceInterval(
            lower=lower,
            mean=mean_rate,
            upper=upper,
            samples=total,
            prior_successes=prior_successes,
            prior_failures=prior_failures,
        )


class PerformanceAggregator:
    """Aggregate tool/skill metrics from learning event stream."""
    
    def __init__(self, event_store: EventStore, cache_ttl_minutes: int = 60):
        self.event_store = event_store
        self.cache = {}
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.last_aggregation = {}  # track last aggregation time per key
    
    async def aggregate_tool_metrics(
        self,
        tool_id: Optional[str] = None,
        task_type: Optional[str] = None,
        time_window_days: int = 7,
        tenant_id: str = "_default",
    ) -> dict[str, ToolPerformanceMetrics]:
        """Aggregate metrics for tools.
        
        Returns:
            Dict of {tool_id: ToolPerformanceMetrics}
        """
        # Query TOOL_EXECUTED events
        events = await self.event_store.query_events(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id=tenant_id,
            filter_fn=lambda e: self._match_tool_filter(e, tool_id, task_type, time_window_days),
            limit=100000,
        )
        
        # Aggregate by tool
        metrics_by_tool = {}
        for event in events:
            payload = event["payload"]
            tid = payload["tool_id"]
            
            if tid not in metrics_by_tool:
                metrics_by_tool[tid] = {
                    "successes": 0,
                    "failures": 0,
                    "latencies": [],
                    "costs": [],
                }
            
            is_success = payload["status"] == "success"
            metrics_by_tool[tid]["successes"] += 1 if is_success else 0
            metrics_by_tool[tid]["failures"] += 0 if is_success else 1
            metrics_by_tool[tid]["latencies"].append(payload.get("latency_ms", 0))
            metrics_by_tool[tid]["costs"].append(payload.get("estimated_cost_cents", 0))
        
        # Convert to ToolPerformanceMetrics
        results = {}
        for tool_id, agg in metrics_by_tool.items():
            ci = ConfidenceIntervalCalculator.compute_interval(
                agg["successes"],
                agg["failures"],
            )
            
            results[tool_id] = ToolPerformanceMetrics(
                tool_id=tool_id,
                tool_name=payload.get("tool_name", tool_id),
                success_count=agg["successes"],
                failure_count=agg["failures"],
                confidence_lower=ci.lower,
                confidence_upper=ci.upper,
                confidence_samples=ci.samples,
                median_latency_ms=self._percentile(agg["latencies"], 50),
                p95_latency_ms=self._percentile(agg["latencies"], 95),
                p99_latency_ms=self._percentile(agg["latencies"], 99),
                median_cost_cents=self._percentile(agg["costs"], 50),
            )
        
        return results
    
    async def aggregate_skill_metrics(
        self,
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
        time_window_days: int = 7,
        tenant_id: str = "_default",
    ) -> dict[str, SkillPerformanceMetrics]:
        """Aggregate metrics for skills."""
        # Similar to tool metrics, but queries SKILL_USED events
        # Computes success rate from strategy outcomes where skill participated
        pass
    
    def _percentile(self, data: list[int], p: int) -> int:
        """Compute percentile of data."""
        if not data:
            return 0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        return sorted_data[min(idx, len(sorted_data) - 1)]
    
    def _match_tool_filter(self, event, tool_id, task_type, time_window_days):
        """Match event against filter criteria."""
        payload = event["payload"]
        
        if tool_id and payload.get("tool_id") != tool_id:
            return False
        
        if task_type and payload.get("task_type") != task_type:
            return False
        
        event_time = datetime.fromisoformat(event["timestamp"])
        cutoff = datetime.utcnow() - timedelta(days=time_window_days)
        
        return event_time >= cutoff


class AggregationScheduler:
    """Background scheduler for periodic aggregation."""
    
    def __init__(self, aggregator: PerformanceAggregator, interval_minutes: int = 60):
        self.aggregator = aggregator
        self.interval = timedelta(minutes=interval_minutes)
    
    async def start(self, hub: SubsystemHub):
        """Start background aggregation loop."""
        while True:
            try:
                await self.run_aggregation(hub)
            except Exception as e:
                logger.error(f"Aggregation error: {e}")
            
            await asyncio.sleep(self.interval.total_seconds())
    
    async def run_aggregation(self, hub: SubsystemHub):
        """Run one round of aggregation."""
        tenant_id = hub.tenant_id
        
        # Aggregate tool metrics (7-day, 30-day)
        for window in [7, 30]:
            metrics = await self.aggregator.aggregate_tool_metrics(
                time_window_days=window,
                tenant_id=tenant_id,
            )
            
            # Publish update event
            await hub.event_emitter.emit(LearningEvent(
                event_type=LearningEventType.PERFORMANCE_METRICS_COMPUTED,
                tenant_id=tenant_id,
                payload={
                    "entity_type": "tool",
                    "time_window_days": window,
                    "count": len(metrics),
                },
            ))
        
        # Aggregate skill metrics (7-day, 30-day)
        for window in [7, 30]:
            metrics = await self.aggregator.aggregate_skill_metrics(
                time_window_days=window,
                tenant_id=tenant_id,
            )
            
            await hub.event_emitter.emit(LearningEvent(
                event_type=LearningEventType.PERFORMANCE_METRICS_COMPUTED,
                tenant_id=tenant_id,
                payload={
                    "entity_type": "skill",
                    "time_window_days": window,
                    "count": len(metrics),
                },
            ))
        
        logger.info(f"Aggregation complete for tenant {tenant_id}")
```

---

## Consequences

### Positive
✅ **Efficient queries:** Metrics cached; no O(n) aggregation on hot path  
✅ **Confidence intervals:** Handles cold-start (few samples) gracefully  
✅ **Trending:** Temporal windows enable improvement/decline detection  
✅ **Enables WEIGHTED model:** Gap 3 can use skill success rates  

### Negative
⚠️ **Stale data:** Cached metrics are up to 1 hour old (eventual consistency)  
⚠️ **Background complexity:** Scheduler adds operational overhead  

### Risks & Mitigation

**Risk 1: Aggregation query is expensive (O(n) over all events)**
- Mitigation: Run in background (hourly), not on critical path
- Monitoring: Alert if aggregation takes > 30 minutes

**Risk 2: Stale data (cache 1 hour old)**
- Mitigation: Document as eventual consistency model
- Justification: 1-hour staleness acceptable for learning

**Risk 3: Cold-start bias (tools with few samples penalized)**
- Mitigation: Bayesian smoothing with prior; lower bound > 0
- Example: 1 success out of 1 trial → credible interval [0.1, 0.8] (not [1.0, 1.0])

---

## Alternatives Considered

### Alternative A: On-demand aggregation (no caching)
**Rationale for rejection:**
- Expensive (O(n) per query)
- Blocks tool selection and ranking
- Doesn't scale

### Alternative B: Pre-aggregate at event write time
**Rationale for rejection:**
- Complex bookkeeping (update aggregates on every event)
- Race conditions (concurrent event writes)
- Doesn't support retrospective filtering

---

## Implementation Plan

### Phase 2A (Parallel with Gap 2/3): Performance Aggregation (Days 26–32)
- [ ] Implement `ConfidenceIntervalCalculator` (Bayesian Beta-Binomial)
- [ ] Implement `PerformanceAggregator` (batch queries, aggregation)
- [ ] Implement `AggregationScheduler` (hourly background job)
- [ ] Unit tests (15+ cases): intervals, aggregation, edge cases
- [ ] Integration: Wire scheduler into SubsystemHub
- [ ] Feature flag: `learning_gap_4_aggregation` (default: false)

---

## Metrics & Success Criteria

### Phase 4 Success
- [ ] Aggregation completes in < 30 minutes for 100K events
- [ ] Cache hit rate > 95% (metrics reused, not recomputed)
- [ ] Confidence intervals computed for all tools/skills
- [ ] PERFORMANCE_METRICS_COMPUTED events emitted hourly

---

## References

- ADR-0314: Learning Infrastructure
- ADR-0321: Tool Execution Events
- ADR-0322: Tool Ranking (uses aggregated metrics)
- ADR-0323: Skill Attribution (uses aggregated metrics for WEIGHTED model)

---

**Status:** PROPOSED  
**Next:** Implement after Gap 1–3 stabilize
