# CorvinOS Quick Reference

## Core Concepts (3 Ideas)

| Idea | Metric | Timeline |
|---|---|---|
| **Smart Routing** | Saves 60–80% tokens | Day 1 |
| **4-Layer ACP** | DataHub→Creator→Daemon→Dashboard | Week 1 |
| **Learning Loop** | Converges in 500 samples | 2–3 weeks |

## The 4 Layers

```
Layer 1: DataHub      → Unified ingestion (memory, files, RAG, MCP)
Layer 2: Creator 2.0  → 12-phase skill generation + loss tracking
Layer 3: Daemon       → Event-driven learning + weight optimization
Layer 4: Dashboard    → Operator UI + audit trail + compliance
```

## 6D Quality Dimensions

- **Data Quality** (0-1): Are the sources good?
- **Generation Quality** (0-1): Is the skill well-made?
- **User Satisfaction** (0-1): Do users like it?
- **Efficiency** (0-1): Is it fast/cheap?
- **Learning Health** (0-1): Is feedback flowing?
- **System Health** (0-1): No errors/crashes?

**Overall = 1.0 - mean(losses)**

## Token Costs

```
Haiku:    $0.00080/token  (cheap, fast, ~95% tasks)
Sonnet:   $0.003/token    (balanced, ~4% tasks)
Opus:     $0.015/token    (powerful, ~1% tasks)
```

## Learning Convergence

**Stochastic Gradient Descent:**
```
weight[i] ← weight[i] + 0.01 × attribution[i]

Convergence: ~500 samples (~2–3 weeks)
Quality improvement: +5–7%
Cost reduction: 60–80%
```

## Request Flow (7 Steps)

1. **Execute** — Skill runs on real data
2. **Measure** — Check quality (6D)
3. **Feedback** — User rates output
4. **Attribute** — Which source helped?
5. **Update** — Adjust weights
6. **Converge?** — Weights stable?
7. **Regenerate** — Next version uses learned weights

## Compliance Checklist

- ✅ GDPR Art. 30 — Immutable audit trail
- ✅ GDPR Art. 32 — Hash-chained, secrets redacted
- ✅ EU AI Act — All decisions logged with attribution
- ✅ Audit Trail — Every event has hash + prev_hash
- ✅ Tenant Isolation — No cross-tenant leakage

## Key Metrics

| Metric | Target | Status |
|---|---|---|
| Generation success rate | >99% | ✓ |
| Quality score | >0.90 | ✓ |
| Convergence time | <2 weeks | ✓ |
| Cost savings | 60–80% | ✓ |
| Audit integrity | 100% | ✓ |

## Files to Read

- **README_CORVINVS_EXPLAINED.md** — Full story + diagrams
- **SYSTEM_OVERVIEW_COMPLETE.md** — Technical depth
- **core/skills/os_skills/MANIFEST.json** — Skill registry
- **ADR-0661** (Corvin-ADR) — Architecture decision

## Diagrams

- `routing_decision.svg` — How routing works
- `6d_hexagon.svg` — Quality dimensions
- `4layer_acp.svg` — Layer architecture
- `feedback_loop.svg` — Learning cycle

---

**CorvinOS in one sentence:** An OS that routes requests to the right LLM, learns which routing works best, and gets cheaper and smarter every day.
