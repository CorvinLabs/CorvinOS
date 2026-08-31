# CEL Phase 4: Ganzheitliches Adaptive Context Management

**Status:** Concept Design Complete (5 Docs)  
**Date:** 2026-08-07  
**Location:** `~/.claude/projects/-home-shumway-projects-CorvinOS/memory/cel-phase4-*.md`

## Overview

Phase 4 extends CEL (Phases 1-3) with **adaptive context management** — making context selection intelligent, learning-driven, and user-aware.

## 4 Core Concepts

### 1. **Uncertainty Quantification (Phase 4a)**
File: `cel-phase4-concept-uncertainty.md`

**Problem:** Context has no confidence attached. "Here's an ADR" doesn't say if it's proven or speculative.

**Solution:** Multidimensional confidence scoring.
```
Confidence = (Relevance × 0.5) + (Reliability × 0.3) + (Freshness × 0.2)

Tiers: HIGH (>0.85) / MEDIUM (0.65-0.85) / LOW (0.40-0.65) / UNCERTAIN (<0.40)
Warnings: "ADR superseded", "File 60 days old", "Skill 30% success rate"
```

**Impact:** Agent can weight advice by confidence, not treat all equal.

### 2. **Outcome Feedback Loop (Phase 4b)**
File: `cel-phase4-concept-outcome-feedback.md`

**Problem:** CEL provides context → Agent decides → Outcome (lost). No learning.

**Solution:** Closed-loop measurement.
```
Task → CEL tracks context usage → Outcome → Feedback → Confidence update

Example: "ADR-0269 was used, helpful=YES" → confidence += 0.05
Example: "Memory-file was ignored, reason=outdated" → confidence -= 0.05
```

**Impact:** Context becomes progressively better. Learning from real outcomes.

### 3. **Style & Preference Priming (Phase 4c)**
File: `cel-phase4-concept-preferences.md`

**Problem:** Same context for all agents. User X is pragmatic, User Y is rigorous.

**Solution:** UserProfile persists preferences.
```
UserProfile:
  decision_style: "pragmatic" | "rigorous" | "balanced"
  language: "de" | "en"
  detail_level: "summary" | "balanced" | "deep"
  care_about: ["performance", "testing"]
  avoid: ["manual-processes"]
  
→ Context filtered, ranked, formatted per user
```

**Impact:** Each user gets personalized context shaped to their decision style.

### 4. **Attention Budget (Phase 4d)**
File: `cel-phase4-concept-attention-budget.md`

**Problem:** Agent has limited attention. CEL dumps 20 items; agent can only process 5.

**Solution:** Attention budget framework.
```
max_memory = 3
max_adr = 5
max_skills = 2

Budget adjusted by: task_complexity + urgency + user_style

Tiering: CRITICAL (must show) / IMPORTANT (show if budget) / NICE-TO-HAVE (hide by default)
```

**Impact:** Context becomes **curated, not comprehensive**. Signal > noise.

## Ganzheitliche Integration

File: `cel-phase4-integration-ganzheitlich.md`

All 4 phases work together as a **flywheel**:

```
User Profile (pragmatic, German, fast)
    ↓
Attention Budget (max 3 memory, 3 ADRs, 2 skills due to pragmatism)
    ↓
Context Retrieval (find all memory/ADRs/skills)
    ↓
Uncertainty Scoring (attach confidence to each)
    ↓
Filtering & Ranking (keep top N by confidence, tier by priority)
    ↓
Personalized Output (German, bullet points, pragmatic tone)
    ↓
Agent Decision + Tracking
    ↓
Execution → Outcome
    ↓
Feedback Collection (what was used? helpful?)
    ↓
Learning Update (adjust confidence scores)
    ↓
[Next task: better scores, better recommendations]
```

### The Flywheel Effect

```
Better Preferences (4c)
  → Better Attention Budget (4d)
  → More Relevant Context (4a)
  → Higher Confidence (4a)
  → Better Outcomes
  → Better Feedback (4b)
  → Better Scoring
  → Better Preferences
  → [Strengthens each cycle]
```

Over 10 tasks:
- Task 1: 70% success (cold start)
- Task 5: 85% success (learning kicks in)
- Task 10: 92% success (flywheel spinning)

## Implementation Roadmap

| Week | Phase | Goal |
|---|---|---|
| Week 1 | 4a + 4d | Uncertainty scoring + attention budgets |
| Week 2 | 4c | User preferences integration |
| Week 3 | 4b | Feedback loop + learning |
| Week 4 | Measurement | Calibration checks, dashboards |

## Key Metrics

- **Calibration:** If we say "HIGH confidence (0.85%)", do 85% succeed?
- **Adoption:** Does agent use recommended context? (Target: 90%)
- **Speed:** Faster decisions? (Target: 30% faster)
- **Quality:** Higher success rate? (Target: 85%+)
- **Learning:** How fast do confidence scores stabilize? (Target: 5-10 cycles)

## Related

- CEL Phase 1: Memory Lookup ✅ Live
- CEL Phase 2: Graph Traversal + Skill Injection ✅ Live
- CEL Phase 3: ADR-based Decision Discovery ✅ Live
- CEL Phase 4: Adaptive Context (4 concepts) → Ready for implementation

## Notes for Implementation

1. **Start with 4a + 4d** (foundation: uncertainty + attention)
2. **Then 4c** (personalization unlocks value)
3. **Finally 4b** (feedback closes the loop)
4. **Measure continuously** (calibration is critical)

Each phase adds value independently but together create exponential improvement.

---

**Full design docs:** See memory files at `~/.claude/projects/-home-shumway-projects-CorvinOS/memory/cel-phase4-*.md`
