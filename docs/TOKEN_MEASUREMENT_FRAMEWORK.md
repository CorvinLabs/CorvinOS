# Vibe Engineering Token Measurement Framework (TMF)

**Version:** 1.0-design  
**Status:** Design Phase (Pre-Implementation)  
**Goal:** Measure and compare token efficiency: Native Engine (stateless) vs Vibe Engineering (stateful with Learning)

---

## Executive Summary

**The Challenge:**
- Native Claude Code: Each session starts fresh → all context re-derived
- Vibe Engineering: Stateful, learns patterns, caches decisions
- Question: How much do we actually save? On what tasks? At what cost?

**The Solution:**
A multi-layer measurement framework that:
1. **Measures** token consumption per turn, per subsystem, per task-type
2. **Compares** Native vs Vibe across 50+ task types
3. **Traces** token flow from input through completion
4. **Dashboards** live metrics in Console (VibeMetrics panel)
5. **Exportable** for per-user, per-tenant, per-instance analysis

---

## Architecture: Three Measurement Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1: Instrumentation (Where tokens go)                    │
├─────────────────────────────────────────────────────────────────┤
│  • Every LLM call: record input_tokens + output_tokens          │
│  • Every subsystem: track own overhead (ExecutionContext, etc)  │
│  • Every cache hit/miss: log what was (not) re-computed         │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌─────────────────────────▼────────────────────────────────────────┐
│  LAYER 2: Aggregation (Rollup & comparison)                    │
├─────────────────────────────────────────────────────────────────┤
│  • Per-turn metrics (total, by subsystem)                       │
│  • Baseline: Native (no caching, no learning)                   │
│  • Vibe: Same task with stateful learning                       │
│  • Delta: (Native - Vibe) / Native = savings %                  │
│  • Confidence: only report if N samples > threshold             │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌─────────────────────────▼────────────────────────────────────────┐
│  LAYER 3: Dashboarding (VibeMetrics Console panel)              │
├─────────────────────────────────────────────────────────────────┤
│  • Real-time stats (current session)                            │
│  • Historical trends (7d, 30d, 90d)                             │
│  • Task-type breakdown (code, research, analysis, etc)          │
│  • Subsystem attribution (Confidence, Cache, Skill, etc)        │
│  • Exportable: JSON, CSV for external analysis                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Instrumentation

### 1.1 Turn-Level Instrumentation

Every turn recorded in `LearningEvent` (ADR-0314):

```python
@dataclass(frozen=True)
class TokenMetrics(LearningEvent):
    """Immutable token consumption record."""
    
    turn_id: str                          # "turn_12345"
    timestamp: datetime
    tenant_id: str
    session_id: str
    user_id: str
    
    # Input/Output tokens
    input_tokens: int                     # tokens in user prompt
    output_tokens: int                    # tokens in LLM response
    total_tokens: int = computed          # input + output
    
    # Subsystem attribution (where did tokens go?)
    subsystem_tokens: dict[str, int] = {
        "llm_call": 1850,           # actual model invocation
        "context_load": 400,        # loading ExecutionContext
        "skill_inject": 320,        # skill library overhead
        "cache_miss_penalty": 200,  # had to re-parse
        "other": 0
    }
    
    # Inference source (which LLM?)
    engine: str                           # "claude-opus-5"
    engine_tier: str                      # "cloud" | "local" | "tiered"
    
    # Comparison baseline
    baseline_tokens: Optional[int]        # what Native would spend
    savings_vs_baseline: Optional[int]    # baseline - total_tokens
    savings_percent: Optional[float]      # (baseline - total) / baseline
    
    # Cache/Learning attribution
    confidence_exit_at_iteration: Optional[int]    # stopped early? 1=yes
    decision_history_cache_hit: bool      # used past decision?
    skill_injected_grades: dict[str, float] = {
        "assistant.skill_x": 0.7,         # grades of skills used
        "assistant.skill_y": 0.9,
    }
    
    # Task metadata (for slicing/grouping)
    task_type: str                        # "code", "research", "analysis", "chat"
    task_complexity: str                  # "trivial", "simple", "moderate", "complex"
    task_domain: str                      # "backend", "frontend", "data", "general"
    
    # Outcome (was this turn successful?)
    outcome_quality: str                  # "excellent" | "good" | "acceptable" | "poor"
    required_followup: bool               # did user need to ask again?
    user_satisfaction: Optional[int]      # 1-5 rating (if given)
    
    # Audit trail
    committed_to_audit_chain: bool        # was this written to hash chain?
```

### 1.2 Subsystem Instrumentation Points

```
ExecutionContext:
  - Load time (seconds) + token equivalent (estimated)
  - Size (KB) → token equivalent
  
SkillForge (Skill Injection):
  - Which skills injected + sizes
  - Which skills actually used in response
  - Skill-overhead: X tokens per 100 chars in response
  
Confidence Scoring (ADR-0315):
  - Confidence score at each iteration
  - Did it trigger early exit?
  - Iteration N: cost without score vs with score
  
Decision History (ADR-0316):
  - Cache lookup latency
  - Hit/miss
  - Re-derive cost (if miss)
  
CIES Concept Caching (Future):
  - Concept lookup time
  - Serialized size (tokens)
  - vs full parse size (tokens)
  
Vibe Engine:
  - Intent detection overhead (tokens)
  - User-pattern match overhead (tokens)
```

---

## Layer 2: Aggregation & Comparison

### 2.1 Baseline: Native Engine (Stateless)

Define a "Pure Native" baseline - same task, no learning, no cache:

```python
class TokenComparison:
    """Compare Vibe vs Native on same task."""
    
    def measure_baseline(task_prompt: str) -> int:
        """
        Simulate Native Engine:
        - No ExecutionContext (fresh start)
        - No Skill injection (only embedded in model)
        - No cache (full re-derivation)
        - No confidence scoring (iterate until confident)
        """
        # Run task with stateless Claude Code
        native_engine = ClaudeCodeEngine(
            learning_enabled=False,
            cache_enabled=False,
            skill_injection=False,
            confidence_scoring=False
        )
        result = native_engine.run(task_prompt)
        return result.total_tokens
    
    def measure_vibe(task_prompt: str) -> int:
        """Measure Vibe Engineering (all subsystems on)."""
        vibe_engine = VibeEngine(learning=True)
        result = vibe_engine.run(task_prompt)
        return result.total_tokens
    
    def compare(task_prompt: str) -> TokenComparison:
        native = measure_baseline(task_prompt)
        vibe = measure_vibe(task_prompt)
        
        return TokenComparison(
            task_id=task_prompt.hash(),
            native_tokens=native,
            vibe_tokens=vibe,
            savings_tokens=native - vibe,
            savings_percent=(native - vibe) / native,
            confidence=self._calculate_confidence(native, vibe),
            
            # Subsystem contribution
            subsystem_breakdown={
                "confidence_exit": vibe.metrics.confidence_savings,
                "skill_grading": vibe.metrics.skill_savings,
                "cache_hit": vibe.metrics.cache_savings,
                "vibe_intent": vibe.metrics.vibe_savings,
                "learning_overhead": -vibe.metrics.learning_cost,  # negative!
            }
        )
```

### 2.2 Confidence Calculation

Only report savings if statistically significant:

```python
def _calculate_confidence(native: int, vibe: int) -> float:
    """
    Confidence that savings are real, not noise.
    
    Factors:
    - Variance across runs (sample std dev)
    - Effect size (|native - vibe| / native)
    - Sample count (n runs of same task)
    """
    if not self.has_n_samples(task_type, n=10):
        return 0.0  # Not enough data
    
    effect_size = abs(native - vibe) / max(native, 100)
    variance = self.variance_across_runs(task_type)
    
    # Effect size > 1 std dev = 68% confidence (1σ)
    # Effect size > 2 std dev = 95% confidence (2σ)
    
    z_score = effect_size / variance
    confidence = self.normal_cdf(z_score)  # 0.0-1.0
    
    # Only show if confidence > 68%
    return confidence if confidence > 0.68 else 0.0
```

### 2.3 Aggregation Buckets

```python
class TokenMetricsAggregator:
    """Aggregate across multiple turns."""
    
    def by_task_type(self, timespan: str = "7d"):
        """Sum tokens by task type (code, research, etc)."""
        return {
            "code": {
                "turns": 42,
                "native_total": 185_000,
                "vibe_total": 118_000,
                "savings_percent": 36.2,
                "confidence": 0.94,
                "subsystem_breakdown": {...}
            },
            "research": {...},
            "analysis": {...},
        }
    
    def by_subsystem(self, timespan: str = "7d"):
        """Which subsystems saved the most tokens?"""
        return {
            "confidence_exit": {
                "times_triggered": 127,
                "avg_tokens_saved_per_trigger": 2400,
                "total_saved": 304_800,
            },
            "skill_grading": {
                "skills_filtered": 340,  # not injected due to low grade
                "avg_tokens_saved": 120,
                "total_saved": 40_800,
            },
            "cache_hit": {
                "hits": 45,
                "misses": 128,
                "hit_rate": 0.26,
                "avg_tokens_saved_per_hit": 800,
                "total_saved": 36_000,
            },
        }
    
    def by_domain(self, timespan: str = "7d"):
        """Where do we save most? Backend > Frontend > General."""
        return {
            "backend": {"turns": 120, "savings_percent": 42.1},
            "frontend": {"turns": 85, "savings_percent": 28.3},
            "data": {"turns": 45, "savings_percent": 35.7},
            "general": {"turns": 200, "savings_percent": 18.2},
        }
    
    def by_complexity(self, timespan: str = "7d"):
        """Simple tasks save less (less room to optimize)."""
        return {
            "trivial": {"turns": 100, "savings_percent": 5.2},
            "simple": {"turns": 150, "savings_percent": 18.5},
            "moderate": {"turns": 130, "savings_percent": 38.9},
            "complex": {"turns": 70, "savings_percent": 51.2},
        }
```

---

## Layer 3: Dashboard (Console / VibeMetrics)

### 3.1 Dashboard Layout

```
┌────────────────────────────────────────────────────────────────────┐
│  CORVINVIBE | VibeMetrics | 🚀                                    │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  📊 SUMMARY (Today)                                               │
│  ┌────────────┬──────────┬──────────┬──────────────────────────┐  │
│  │ Turns      │ Tokens   │ Baseline │ Savings                │  │
│  │ 42         │ 287.5k   │ 456.2k   │ 168.7k (36.9%)  ✅    │  │
│  └────────────┴──────────┴──────────┴──────────────────────────┘  │
│                                                                    │
│  💡 SUBSYSTEM ATTRIBUTION (Where did we save?)                   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Confidence Exit        ████████░░░░░  42% (71.2k tokens)│    │
│  │ Cache Hit              ██████░░░░░░░░  18% (30.5k tokens)│   │
│  │ Skill Grading Filter   ███░░░░░░░░░░░  12% (20.3k tokens)│   │
│  │ Vibe Intent Detection  ██░░░░░░░░░░░░  8%  (13.5k tokens)│   │
│  │ Learning Overhead      ░░░░░░░░░░░░░░ -3%  (-5.1k tokens)│   │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                    │
│  📈 TRENDS (Last 7 Days)                                          │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │   Mon  Tue  Wed  Thu  Fri  Sat  Sun                      │    │
│  │    ↗   ↗    ↘   ↗    ↗    ↗    ↗   (Savings % by day)   │    │
│  │  28%  31%  26%  35%  37%  34%  39%                       │    │
│  │                                                            │    │
│  │   Average: 32.9% ✓ High confidence (σ=0.12)            │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                    │
│  🎯 BY TASK TYPE                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Code (42 turns):          36.2% saved ✅ High confidence │    │
│  │ Research (28 turns):      41.8% saved ✅ High confidence │    │
│  │ Analysis (18 turns):      28.5% saved ⚠️  Moderate      │    │
│  │ Chat (8 turns):           12.1% saved ⚠️  Low           │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                    │
│  🏗️ BY DOMAIN                                                     │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Backend:     45.2% saved (32 turns) ⭐⭐⭐              │    │
│  │ Frontend:    28.7% saved (24 turns) ⭐⭐                │    │
│  │ Data:        38.1% saved (18 turns) ⭐⭐⭐              │    │
│  │ General:     18.3% saved (12 turns) ⭐                  │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                    │
│  🔍 DETAILS TAB                                                   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ [Timeseries] [Subsystem] [Tasks] [Export] [Settings]   │    │
│  │                                                            │    │
│  │ Task ID              Tokens   Baseline  Savings  Grade   │    │
│  │ turn_12845           1.2k     1.8k      33%      ✓✓✓    │    │
│  │ turn_12846           2.1k     2.9k      28%      ✓✓     │    │
│  │ turn_12847           890      1.1k      19%      ✓      │    │
│  │ (scroll for more...)                                      │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                    │
│ 🔧 SETTINGS  [Baseline: Native] [Confidence: 68%] [Timespan: 7d] │
│ 💾 EXPORT    [JSON] [CSV] [Grafana] [Webhook]                     │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 3.2 Dashboard Data Flow

```python
class VibeMetricsPanel:
    """Live metrics panel in CorvinOS Console."""
    
    def __init__(self, session_id: str):
        self.session = session_id
        self.store = TokenMetricsStore()  # EventStore backend
    
    def summary_stats(self, timespan: str = "1d") -> dict:
        """Current session summary."""
        events = self.store.query_metrics(
            session_id=self.session,
            timespan=timespan,
            only_successful=True  # filter out failed turns
        )
        
        return {
            "turn_count": len(events),
            "total_tokens": sum(e.total_tokens for e in events),
            "baseline_tokens": sum(e.baseline_tokens for e in events),
            "savings_tokens": sum(e.savings_vs_baseline for e in events),
            "savings_percent": savings_tokens / baseline_tokens,
            
            # Confidence (only show if > 0.68)
            "confidence": self._calculate_confidence(events),
            "confidence_level": self._confidence_label(confidence),
            
            # Per-subsystem breakdown
            "subsystem_breakdown": self._aggregate_subsystems(events),
        }
    
    def trends(self, timespan: str = "7d", bucket_size: str = "1d"):
        """Historical trend line."""
        buckets = self.store.time_bucket_metrics(
            session_id=self.session,
            timespan=timespan,
            bucket_size=bucket_size
        )
        
        return [
            {
                "date": bucket.start_time,
                "turns": bucket.count,
                "savings_percent": bucket.avg_savings_percent,
                "confidence": bucket.confidence,
                "volatility": bucket.std_dev
            }
            for bucket in buckets
        ]
    
    def by_task_type(self, timespan: str = "7d"):
        """Slice by task type."""
        return self.store.aggregate_by(
            session_id=self.session,
            groupby="task_type",
            timespan=timespan,
            include_confidence=True
        )
    
    def by_domain(self, timespan: str = "7d"):
        """Slice by domain."""
        return self.store.aggregate_by(
            session_id=self.session,
            groupby="task_domain",
            timespan=timespan,
            include_confidence=True
        )
    
    def export(self, fmt: str = "json", timespan: str = "7d"):
        """Export for external analysis."""
        events = self.store.query_metrics(
            session_id=self.session,
            timespan=timespan
        )
        
        if fmt == "json":
            return {
                "metadata": {
                    "session_id": self.session,
                    "exported_at": now(),
                    "timespan": timespan,
                    "event_count": len(events)
                },
                "events": [e.to_dict() for e in events],
                "aggregates": self.summary_stats(timespan)
            }
        elif fmt == "csv":
            return self._to_csv(events)
        elif fmt == "grafana":
            return self._to_grafana_json(events)
```

---

## Measurement Roadmap

### Phase 1: Foundation (Week 1-2)
- [ ] Extend `TokenMetrics` dataclass (ADR-0314 integration)
- [ ] Instrument all LLM calls (`WorkerEngine.run()`)
- [ ] Measure subsystem overhead (ExecutionContext, SkillForge)
- [ ] Store in EventStore (GDPR compliant)

### Phase 2: Baseline & Aggregation (Week 3-4)
- [ ] Define "Native Engine" baseline (stateless simulation)
- [ ] Compare same tasks Vibe vs Native
- [ ] Build aggregation logic (by_task_type, by_domain, by_subsystem)
- [ ] Implement confidence calculation

### Phase 3: Dashboard (Week 5-6)
- [ ] UI Panel in Console (React component)
- [ ] Real-time summary widget
- [ ] Trend charts (7d, 30d, 90d)
- [ ] Export API (JSON, CSV, Grafana)

### Phase 4: Analysis & Insights (Week 7-8)
- [ ] Per-user savings breakdown
- [ ] Per-tenant cost attribution
- [ ] Anomaly detection (task type suddenly expensive?)
- [ ] Recommendations engine ("your backend tasks save 45%, try analysis tasks too")

---

## Expandability

### Add a New Subsystem to Measurement

```python
# Step 1: Define event in TokenMetrics.subsystem_tokens
subsystem_tokens: dict[str, int] = {
    "new_subsystem": 0  # add here
}

# Step 2: Instrument the subsystem
class NewSubsystem:
    def run(self):
        start_tokens = self.token_counter.current()
        result = self._execute()
        end_tokens = self.token_counter.current()
        
        # Report to TokenMetrics
        TokenMetricsStore.record_subsystem_usage(
            subsystem="new_subsystem",
            tokens_used=end_tokens - start_tokens,
            turn_id=current_turn_id()
        )
        return result

# Step 3: Add to dashboard
# → Automatically appears in "BY SUBSYSTEM" breakdown
# → No dashboard code changes needed (generic aggregation)
```

### Add a New Aggregation Dimension

```python
# Step 1: Add field to TokenMetrics
class TokenMetrics:
    user_experience_level: str  # "beginner" | "intermediate" | "expert"

# Step 2: Aggregate it
aggregator.by("user_experience_level", timespan="30d")

# Step 3: Dashboard automatically shows
# "BY EXPERIENCE LEVEL" tab in details section
```

---

## Success Criteria

✅ **Launched when:**
- Token measurements available in real-time for every turn
- Baseline (Native) vs Vibe comparison running on 100+ tasks
- Confidence scoring filters out noise (only report sig. results)
- Dashboard shows live summary + 7d trends
- Export works (engineers can do deep analysis)
- Subsystems correctly attributed (Confidence, Cache, Skills, Vibe)

✅ **Credible when:**
- 3+ subsystems show consistent savings (not one-offs)
- High-confidence (>0.95) results on 2+ task types
- External validation: real users see faster responses

---

## Next Steps

1. **Design Review:** Does this framework answer your questions?
2. **Implementation Pick:** Phase 1 (Instrumentation) or Phase 3 (Dashboard UI)?
3. **Metric Validation:** Which task types to measure first?

---

**This framework is:**
- ✅ Expandable (add subsystems, dimensions, exports)
- ✅ Rigorous (confidence-based, not hand-wavy)
- ✅ Transparent (exportable, auditable)
- ✅ Actionable (shows which subsystem saves most)
- ✅ GDPR-compliant (EventStore, pseudonymized)

