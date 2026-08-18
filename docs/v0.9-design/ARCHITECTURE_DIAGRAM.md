# CorvinOS v0.9 Architecture Diagrams

**Release:** Real-Time Dashboard v0.9  
**Status:** Design Phase  
**Purpose:** Visual architecture documentation for live subsystem monitoring, decision streaming, and cost visualization.

---

## 1. Dashboard Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│              CONSOLE (Browser)                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Dashboard Component (React)                                  │   │
│  │ ├─ Tab 1: Subsystem Health (polling, 1Hz)                   │   │
│  │ ├─ Tab 2: Decision Stream (WebSocket, real-time)            │   │
│  │ ├─ Tab 3: Cost Tracker (polling, 100ms)                     │   │
│  │ ├─ Tab 4: Operator Annotations (polling)                    │   │
│  │ └─ Tab 5: Interrupt Controls (buttons)                      │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
└─────────────────────────────┼───────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
   WebSocket            HTTP Polling         HTTP Polling
   (1 conn)          (subsystem health)      (cost, annotations)
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────────────────────────────────────────────────────────┐
│           CONSOLE BACKEND API (corvin_console)                   │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ GET /api/dashboard/health                                       │
│  Returns: {subsystems: [{name, status, latency_p50/p95/p99}]}  │
│  Rate: 1 Hz (1000ms)                                            │
│                                                                  │
│ WebSocket /ws/decision-stream                                    │
│  Publishes events: {type, data, timestamp}                      │
│  Events: decision_made, cost_incurred, confidence_updated       │
│  Latency: <500ms p99                                            │
│                                                                  │
│ GET /api/dashboard/cost                                         │
│  Returns: {budget, spent, burn_rate, projected_end}            │
│  Rate: 100ms (10 Hz for smooth animation)                       │
│                                                                  │
│ POST /api/dashboard/interrupt/{action}                          │
│  Actions: pause, resume, redirect                               │
│  Latency: <100ms                                                │
│                                                                  │
│ POST /api/dashboard/annotation/{turn_id}                        │
│  Payload: {rating: 1-5, feedback: "..."}                        │
│                                                                  │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│              BRAIN (Orchestration Layer)                         │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ HealthMonitor subsystem:                                         │
│  ├─ Polls all 13 subsystems every 1s                             │
│  ├─ Collects: (latency, throughput, error_rate, status)         │
│  ├─ Caches in CircularBuffer (last 60 samples)                   │
│  ├─ Exposes: get_health() → List[SubsystemHealth]               │
│  └─ Metrics: p50, p95, p99 latency + error count                │
│                                                                  │
│ DecisionBus (Event stream):                                      │
│  ├─ Every turn publishes decision_made event                     │
│  ├─ Publishes cost_incurred after turn                           │
│  ├─ Publishes confidence_update from learning                    │
│  ├─ Subscribers: Console WebSocket handler, Audit trail         │
│  ├─ Queue size: 10K events (circular, old events drop)          │
│  └─ Latency: <100ms from event to subscriber notification       │
│                                                                  │
│ CostController subsystem:                                        │
│  ├─ Accumulates costs per turn                                   │
│  ├─ Tracks: budget, spent, spend_rate                            │
│  ├─ Calculates: days_remaining, projected_total                  │
│  ├─ Exposes: get_cost_status() → CostStatus                      │
│  └─ Updates: every turn completion                               │
│                                                                  │
│ ExecutionContext:                                                │
│  ├─ Carries execution_paused flag                                │
│  ├─ Allows redirect: engine = native/acs/tde                     │
│  ├─ Snapshot on pause, restored on resume                        │
│  └─ Consistent across Brain, plugins, models                     │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Subsystem Health Dashboard

```
┌──────────────────────────────────────────────────────────────────┐
│                 SUBSYSTEM HEALTH DASHBOARD                        │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Live Status (Updates every 1 second)                      │ │
│  │                                                             │ │
│  │ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │ │
│  │ │ ✓ Health     │  │ ◐ Context    │  │ ✓ Loop       │     │ │
│  │ │  Monitor     │  │  Bridge      │  │  Engineer    │     │ │
│  │ │              │  │              │  │              │     │ │
│  │ │ Status: GREEN│  │ Status: YELLOW│  │ Status: GREEN│     │ │
│  │ │ p50: 2ms     │  │ p50: 5ms     │  │ p50: 15ms    │     │ │
│  │ │ p95: 4ms     │  │ p95: 12ms    │  │ p95: 25ms    │     │ │
│  │ │ p99: 6ms     │  │ p99: 18ms    │  │ p99: 35ms    │     │ │
│  │ │ Throughput:  │  │ Throughput:  │  │ Throughput:  │     │ │
│  │ │  150 Hz      │  │  140 Hz      │  │  120 Hz      │     │ │
│  │ │ Error rate:  │  │ Error rate:  │  │ Error rate:  │     │ │
│  │ │  0.1%        │  │  0.5%        │  │  0.0%        │     │ │
│  │ └──────────────┘  └──────────────┘  └──────────────┘     │ │
│  │                                                             │ │
│  │ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │ │
│  │ │ ✓ Orchestr.  │  │ ✓ Learning   │  │ ✓ Cost       │     │ │
│  │ │              │  │  Engine      │  │  Controller  │     │ │
│  │ │              │  │              │  │              │     │ │
│  │ │ Status: GREEN│  │ Status: GREEN│  │ Status: GREEN│     │ │
│  │ │ p50: 8ms     │  │ p50: 12ms    │  │ p50: 1ms     │     │ │
│  │ │ p95: 18ms    │  │ p95: 22ms    │  │ p95: 2ms     │     │ │
│  │ │ p99: 28ms    │  │ p99: 35ms    │  │ p99: 3ms     │     │ │
│  │ │ Throughput:  │  │ Throughput:  │  │ Throughput:  │     │ │
│  │ │  130 Hz      │  │  100 Hz      │  │  500 Hz      │     │ │
│  │ │ Error rate:  │  │ Error rate:  │  │ Error rate:  │     │ │
│  │ │  0.0%        │  │  0.2%        │  │  0.0%        │     │ │
│  │ └──────────────┘  └──────────────┘  └──────────────┘     │ │
│  │                                                             │ │
│  │ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │ │
│  │ │ ✓ Tool Forge │  │ ✓ Skill      │  │ ◐ Safety     │     │ │
│  │ │              │  │  Forge       │  │  Validator   │     │ │
│  │ │              │  │              │  │              │     │ │
│  │ │ Status: GREEN│  │ Status: GREEN│  │ Status: YELLOW│     │ │
│  │ │ p50: 20ms    │  │ p50: 18ms    │  │ p50: 8ms     │     │ │
│  │ │ p95: 45ms    │  │ p95: 40ms    │  │ p95: 15ms    │     │ │
│  │ │ p99: 65ms    │  │ p99: 60ms    │  │ p99: 22ms    │     │ │
│  │ │ Throughput:  │  │ Throughput:  │  │ Throughput:  │     │ │
│  │ │  80 Hz       │  │  85 Hz       │  │  110 Hz      │     │ │
│  │ │ Error rate:  │  │ Error rate:  │  │ Error rate:  │     │ │
│  │ │  0.0%        │  │  0.1%        │  │  1.2%        │     │ │
│  │ └──────────────┘  └──────────────┘  └──────────────┘     │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Color Legend:                                                   │
│  ✓ GREEN:  p99 < 100ms, error < 0.5%, throughput > 100 Hz      │
│  ◐ YELLOW: p99 < 200ms, error < 2.0%, throughput > 50 Hz       │
│  ✗ RED:    p99 > 200ms OR error > 2.0% OR throughput < 50 Hz   │
│                                                                  │
│  Dashboard load time: <2 seconds                                 │
│  Update latency: <500ms (real-time)                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Decision Stream (WebSocket)

```
┌──────────────────────────────────────────────────────────────────┐
│              DECISION STREAM (WebSocket /ws/decision)            │
│                                                                  │
│  Event Types (Real-time, <500ms latency):                        │
│                                                                  │
│  1. decision_made                                                │
│     {                                                            │
│       "type": "decision_made",                                   │
│       "decision_id": "dec-xyz",                                  │
│       "turn_id": "turn-123",                                     │
│       "operator_chose": "Option A",                              │
│       "confidence": 0.87,                                        │
│       "timestamp": 1692374400.234                                │
│     }                                                            │
│                                                                  │
│  2. cost_incurred                                                │
│     {                                                            │
│       "type": "cost_incurred",                                   │
│       "turn_id": "turn-123",                                     │
│       "cost_usd": 0.0045,                                        │
│       "model": "claude-opus",                                    │
│       "input_tokens": 1250,                                      │
│       "output_tokens": 380,                                      │
│       "timestamp": 1692374401.034                                │
│     }                                                            │
│                                                                  │
│  3. confidence_updated                                           │
│     {                                                            │
│       "type": "confidence_updated",                              │
│       "decision_id": "dec-xyz",                                  │
│       "old_confidence": 0.81,                                    │
│       "new_confidence": 0.87,                                    │
│       "reason": "Outcome was success, confidence increased",     │
│       "timestamp": 1692374402.500                                │
│     }                                                            │
│                                                                  │
│  4. plugin_invoked                                               │
│     {                                                            │
│       "type": "plugin_invoked",                                  │
│       "turn_id": "turn-123",                                     │
│       "plugin_id": "auth-recommender",                           │
│       "plugin_name": "Auth Best Practices",                      │
│       "result": "Suggested OAuth2 flow",                         │
│       "timestamp": 1692374401.500                                │
│     }                                                            │
│                                                                  │
│  5. engine_switched                                              │
│     {                                                            │
│       "type": "engine_switched",                                 │
│       "turn_id": "turn-123",                                     │
│       "from_engine": "native",                                   │
│       "to_engine": "acs",                                        │
│       "reason": "Big data task detected",                        │
│       "timestamp": 1692374401.800                                │
│     }                                                            │
│                                                                  │
│  6. interrupt_action                                             │
│     {                                                            │
│       "type": "interrupt_action",                                │
│       "turn_id": "turn-123",                                     │
│       "action": "pause",  # pause / resume / redirect           │
│       "timestamp": 1692374402.000                                │
│     }                                                            │
│                                                                  │
│  Stream Properties:                                              │
│  ├─ Real-time: <500ms latency from event to console            │
│  ├─ Ordered: Sequence number ensures correct order             │
│  ├─ Durable: Events queued (max 10K, circular buffer)          │
│  ├─ Resumable: New client can request past N events            │
│  └─ Reliable: TCP ensures delivery, no event loss              │
│                                                                  │
│  Consumer (Console Dashboard):                                   │
│  ├─ Maintains list of recent events (last 100)                 │
│  ├─ Renders timeline in reverse chronological order            │
│  ├─ Updates metrics in real-time as events arrive              │
│  └─ Allows annotation on any event (👍 / 👎)                   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Cost Burn Visualization

```
┌──────────────────────────────────────────────────────────────────┐
│                 COST TRACKER DASHBOARD                           │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Budget & Spending Overview                                │ │
│  │                                                             │ │
│  │ Budget:      $100.00                                       │ │
│  │ Spent:       $42.50 (42.5%)                                │ │
│  │ Burn rate:   $12.50/hour                                   │ │
│  │ Projected:   $300 (exceeds budget!)  ⚠️ WARNING            │ │
│  │ Days left:   0.7 days (16.8 hours)                         │ │
│  │                                                             │ │
│  │ ┌────────────────────────────────────────────────────────┐ │ │
│  │ │ Budget Visualization (Progress Bar)                   │ │ │ │
│  │ │                                                         │ │ │
│  │ │ [████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░]        │ │ │
│  │ │  0%            42.5%                            100%   │ │ │
│  │ │                                                         │ │ │
│  │ │ Status: On pace to exceed budget by 200% 🔴 RED       │ │ │
│  │ └────────────────────────────────────────────────────────┘ │ │
│  │                                                             │ │
│  │ Hourly Burn Rate (Last 24 hours):                          │ │
│  │                                                             │ │
│  │ Cost/hr                                                     │ │
│  │   $15 │                                                     │ │
│  │   $14 │                                                     │ │
│  │   $13 │        ╭╮        ╭╮                                │ │
│  │   $12 │ ╭──────╯╰────────╯╰──────                          │ │
│  │   $11 │╭╯                      ╰╮                          │ │
│  │   $10 ├─────────────────────────────────                  │ │
│  │    $9 │                                                     │ │
│  │      └──────────────────────────────────►                 │ │
│  │       0h    4h    8h   12h   16h   20h   24h              │ │
│  │                                                             │ │
│  │ Projection (based on current burn):                         │ │
│  │  ├─ Continue at $12.50/hr: Total $300 (budget: $100)       │ │
│  │  ├─ Recommended action: Reduce to $4.17/hr to stay in budget│ │
│  │  └─ Or: Increase budget to $300                            │ │
│  │                                                             │ │
│  │ [View breakdown by engine] [Adjust budget] [History]       │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Breakdown by Engine:                                            │
│  ├─ Native (Claude):    $30.00 (70.6%)                          │
│  ├─ ACS (Opus):         $10.00 (23.5%)                          │
│  ├─ TDE (Multi-model):   $2.50 (5.9%)                           │
│  └─ Total:              $42.50 (100%)                           │
│                                                                  │
│  Update latency: <100ms (smooth animation)                       │
│  Polling: 100ms (10 Hz for real-time feel)                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. Interrupt Protocol State Machine

```
┌─────────────────────────────────────────────────────────────────────┐
│             TURN EXECUTION STATE MACHINE                            │
│              (with interrupt control points)                        │
│                                                                     │
│  NORMAL FLOW (no interrupts):                                       │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│  │ INIT     │──▶│ RUNNING  │──▶│ COMPLETE │──▶│ STORED   │        │
│  │          │   │          │   │          │   │          │        │
│  │ (0ms)    │   │ (0-100ms)│   │ (100ms)  │   │ (101ms)  │        │
│  └──────────┘   └──┬───────┘   └──────────┘   └──────────┘        │
│                    │                                               │
│                    ├─ Control point: Can PAUSE here                │
│                    ├─ Control point: Can REDIRECT here             │
│                    └─ (Every 10ms check for interrupts)            │
│                                                                     │
│  INTERRUPTED FLOW (pause):                                         │
│  ┌──────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐        │
│  │ INIT     │──▶│ RUNNING  │──▶│ PAUSED  │──▶│ RESUMED  │        │
│  │          │   │          │   │         │   │ (continue)        │
│  │          │   │          │ X │         │   │          │        │
│  └──────────┘   └──────────┘   └────┬────┘   └──────────┘        │
│                                      │                             │
│                                      ├─ Snapshot captured          │
│                                      ├─ Turn execution frozen      │
│                                      ├─ Operator can inspect       │
│                                      ├─ Then RESUME or DISCARD     │
│                                      └─ Latency: <100ms to pause   │
│                                                                     │
│  REDIRECTED FLOW (engine switch):                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌──────────┐   │
│  │ INIT     │──▶│ RUNNING  │──▶│ REDIRECTED   │──▶│ COMPLETE │   │
│  │ (native) │   │ (native) │   │ (switch to   │   │ (new     │   │
│  │          │   │          │ X │  ACS/TDE)    │   │  engine) │   │
│  └──────────┘   └──────────┘   └──────────────┘   └──────────┘   │
│                                                                     │
│                                  Re-execute with:                 │
│                                  ├─ Same ExecutionContext         │
│                                  ├─ Same user input               │
│                                  ├─ Different model/engine        │
│                                  ├─ Timestamp reset               │
│                                  └─ <500ms total redirect time    │
│                                                                     │
│  API Endpoints:                                                    │
│  ├─ POST /api/interrupt/pause                                     │
│  │  Effect: ExecutionContext.paused = true                       │
│  │  Response: Current snapshot                                    │
│  │  Latency: <100ms                                               │
│  │                                                                 │
│  ├─ POST /api/interrupt/resume                                    │
│  │  Effect: ExecutionContext.paused = false, continue execution  │
│  │  Response: Final result                                        │
│  │  Latency: <50ms                                                │
│  │                                                                 │
│  └─ POST /api/interrupt/redirect                                  │
│     Payload: {new_engine: "acs" | "tde"}                         │
│     Effect: ExecutionContext.engine = new_engine                  │
│     Response: New result (using new engine)                       │
│     Latency: <500ms (includes re-execution)                       │
│                                                                     │
│  Success Criteria:                                                 │
│  ├─ 100% interrupt success (no crashes)                           │
│  ├─ <100ms pause latency p99                                      │
│  ├─ <500ms redirect latency p99                                   │
│  ├─ No data loss on interrupt                                     │
│  └─ Snapshot accurately captures state                            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Annotation Feedback Loop

```
┌─────────────────────────────────────────────────────────────────────┐
│          OPERATOR ANNOTATION & FEEDBACK LOOP                        │
│                                                                     │
│  Console UI: Decision Stream View                                  │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ Turn #123: "Implement OAuth2 flow"                        │   │
│  │ ├─ Model: Claude Opus                                      │   │
│  │ ├─ Cost: $0.0045                                           │   │
│  │ ├─ Decision: Chose "Option A" (confidence 0.87)           │   │
│  │ ├─ Outcome: ✓ Success                                      │   │
│  │ │                                                          │   │
│  │ │ [👍 Good]  [👎 Bad]  [💬 Feedback]                       │   │
│  │ │                                                          │   │
│  │ │ (If user clicks 👎 or 💬):                              │   │
│  │ │ ┌──────────────────────────────────────────────────┐   │   │
│  │ │ │ Why was this decision not helpful?              │   │   │
│  │ │ │ ┌──────────────────────────────────────────────┐ │   │   │
│  │ │ │ │ Took too long to generate                    │ │   │   │
│  │ │ │ │ Suggested overly complex solution            │ │   │   │
│  │ │ │ │ Didn't follow requirements                   │ │   │   │
│  │ │ │ │ Other (please specify)                       │ │   │   │
│  │ │ │ └──────────────────────────────────────────────┘ │   │   │
│  │ │ │                                                  │   │   │
│  │ │ │ Additional feedback (optional):                 │   │   │
│  │ │ │ ┌──────────────────────────────────────────────┐ │   │   │
│  │ │ │ │ The solution didn't match the use case...    │ │   │   │
│  │ │ │ │                                              │ │   │   │
│  │ │ │ └──────────────────────────────────────────────┘ │   │   │
│  │ │ │                                                  │   │   │
│  │ │ │ [Submit]  [Cancel]                               │   │   │
│  │ │ └──────────────────────────────────────────────────┘   │   │
│  │                                                          │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Backend Processing:                                               │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ 1. POST /api/dashboard/annotation/{turn_id}               │   │
│  │    Payload: {rating: -1, feedback: "..."}                 │   │
│  │                                                             │   │
│  │ 2. LearningEngine.record_feedback() called:               │   │
│  │    - Create OperatorFeedback event                        │   │
│  │    - Store in decision_feedback table                     │   │
│  │    - Hash-chain link to decision audit                    │   │
│  │    - Emit learning signal                                 │   │
│  │                                                             │   │
│  │ 3. Update confidence scores:                              │   │
│  │    - If rating < 0: Decrease model confidence for this    │   │
│  │      decision type                                         │   │
│  │    - If rating > 0: Increase confidence                   │   │
│  │    - Confidence saturates at 1.0 (no over-learning)       │   │
│  │                                                             │   │
│  │ 4. Learning signal:                                       │   │
│  │    - Feedback category → skill adjustment                 │   │
│  │    - E.g. "overly complex" → Reduce complexity preference │   │
│  │    - Feedback stored + encrypted                          │   │
│  │    - PII scrubbed (never store actual feedback text)      │   │
│  │                                                             │   │
│  │ 5. Emit event to decision stream:                         │   │
│  │    {type: "feedback_received", decision_id, rating}       │   │
│  │                                                             │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Success Metrics:                                                  │
│  ├─ >50% annotation adoption (operators rate >50% of decisions)   │
│  ├─ Annotations reduce error rate (feedback used for learning)    │
│  ├─ Zero PII leaks (scrubbed before storage)                      │
│  ├─ Audit trail complete (all annotations hash-chained)           │
│  └─ Right to erasure works (can delete own annotations)           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## References

- **ADRs:** 0396–0399 (dashboard architecture, real-time streaming)
- **Concepts:** 0030–0031 (real-time monitoring methodology)
- **Depends on:** v0.6 (learning signals), v0.8 (offline support)
- **GDPR:** Art. 5 (transparency), Art. 32 (data security), Art. 17 (erasure)

---

**Maintained by:** Claude Code  
**Last Updated:** 2026-08-18  
**Next Review:** v0.9 Week 1 (WebSocket infrastructure complete)
