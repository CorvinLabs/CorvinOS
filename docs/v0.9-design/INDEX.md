# CorvinOS v0.9 Design Index

**Release:** Real-Time Dashboard  
**Timeline:** 4 weeks (2026-12-22)  
**Status:** Design Phase

---

## Quick Navigation

| Document | Purpose | Status |
|---|---|---|
| **[V0.9_IDEAS.md](V0.9_IDEAS.md)** | Vision & 5 core ideas | ✓ Complete |
| **[V0.9_IMPLEMENTATION_PLAN.md](V0.9_IMPLEMENTATION_PLAN.md)** | Detailed impl (4 phases, 114+ tests) | ✓ Complete |
| **[ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md)** | Diagrams: health, stream, cost, interrupt | ✓ Complete |

---

## Key Features

### 1. Live Subsystem Monitor
- Real-time health for 13 subsystems
- Latency histogram (p50, p95, p99)
- Throughput + error rate
- Status: GREEN/YELLOW/RED

### 2. Decision Stream (WebSocket)
- Real-time events: decision, cost, confidence, plugin, engine_switch, interrupt
- <500ms latency
- Event sequence guarantees
- Subscribe from past event N

### 3. Interrupt Protocol
- Pause: freeze turn, save snapshot
- Resume: continue from checkpoint
- Redirect: switch engine (native→ACS→TDE)
- <100ms pause latency

### 4. Cost Burn Visualization
- Budget + spent
- Burn rate ($/hr)
- Projections
- Breakdown by engine

### 5. Operator Annotation
- 👍 / 👎 feedback
- Feedback category
- Learning signal integration
- >50% adoption target

---

## Success Criteria

- [ ] Dashboard load: <2s
- [ ] Stream latency: <500ms p99
- [ ] Interrupt success: 100%
- [ ] Pause latency: <100ms p99
- [ ] Annotations: >50% adoption
- [ ] Tests: 114+ green
- [ ] Uptime: 99.9%

---

## ADRs & Concepts

| Item | Status |
|---|---|
| ADR-0396 (Health monitoring) | ⏳ Pending |
| ADR-0397 (WebSocket streaming) | ⏳ Pending |
| ADR-0398 (Interrupt protocol) | ⏳ Pending |
| ADR-0399 (Cost visualization) | ⏳ Pending |
| CONCEPT-0030 (Real-time methodology) | ⏳ Pending |
| CONCEPT-0031 (Annotation feedback loop) | ⏳ Pending |

---

## Dependency Chain

```
v0.8 (Offline Mode, complete)
  ↓
v0.9 (Real-Time Dashboard) ← YOU ARE HERE
  ├─→ ADR-0396-399
  └─→ CONCEPT-0030-31
      ↓
  v1.0 (Production Release)
```

---

**Maintained by:** Claude Code  
**Last Updated:** 2026-08-18
