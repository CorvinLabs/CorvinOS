# CONCEPT-0011: Context Engineering Optimization Initiative

**Status:** PROPOSED (G1–G5 delivered, awaiting integration gate)  
**Date:** 2026-08-19 · **Author:** Claude (Haiku 4.5) for shumway  
**Scope:** CEL cost-quality tradeoff automation · Learning feedback loop closure · Cross-device state sync  
**ADRs:** Depends on ADR-0275, ADR-0278, ADR-0282–0285, ADR-0314; implemented via ADR-0368–0371  
**Related Concepts:** CONCEPT-0001 (self-learning archive), CONCEPT-0010 (unified context bridge)  
**Skills:** `assistant.corvinOS_context_optimization` (learned-experience)

---

## 1. Executive Summary

Three coordinated optimizations reduce context-engineering latency and cost while closing the learning flywheel:

1. **TokenBudget** — each retrieval channel (memory, skills, ADRs, tools) carries a token allowance; retrievals exceeding it degrade gracefully to the next-best source.
2. **ConfidenceGate** — stages report confidence tiers; retrievals below a threshold are deduplicated or culled before the prompt is built, saving tokens.
3. **PreviewBound** — the Glass Box (operator tracing surface) limits artifact preview count per turn; overflow is linkable but not inlined, reducing console bloat.

**Why together?** None works alone. TokenBudget picks the *volume* of context; ConfidenceGate picks the *quality*; PreviewBound picks what the operator *sees* and therefore what they act on. The three work at different layers (cost, quality, observability) but feed a single decision: *what context actually matters for this turn?*

**Invariant:** every optimization degrades gracefully. A broken budget → use unbudgeted fallback. A failed confidence assessment → treat as neutral. A preview overflow → show counts, not blank. Audit trail remains hash-chained and tamper-evident across all paths.

---

## 2. Architecture Overview

### 2.1 Three-Layer Optimization Stack

```
┌──────────────────────────────────────────────────────────────┐
│         INPUT: Memory + ADRs + Skills + Tools                │
│  (8 CEL stages, running in parallel with requires-DAG)       │
└────────────────────┬─────────────────────────────────────────┘
                     │
        ┌────────────▼────────────┐
        │  RETRIEVAL → RANKING    │
        │  (each stage outputs)   │
        └────────────┬────────────┘
                     │
    ┌────────────────┼────────────────┐
    │                │                │
    ▼                ▼                ▼
┌─────────┐  ┌────────────┐  ┌──────────────┐
│ TokenBud│  │Confidence  │  │ PreviewBound │
│  GET    │  │   GATE     │  │  (UI only)   │
│  (cost) │  │ (quality)  │  │  (display)   │
└────┬────┘  └─────┬──────┘  └──────┬───────┘
     │             │                │
     └──────────────┼────────────────┘
                    │
        ┌───────────▼──────────┐
        │   DEDUP & BUILD      │
        │   (ordered brief)    │
        └───────────┬──────────┘
                    │
        ┌───────────▼──────────┐
        │   LLM SYNTHESIS      │
        │  (optional stage)    │
        └───────────┬──────────┘
                    │
        ┌───────────▼──────────┐
        │   BIND TOOLS/SKILLS  │
        │   FINAL PROMPT       │
        └──────────────────────┘
                    │
                    ▼
        ┌──────────────────────┐
        │   WORKER ENGINE      │
        │   (consumer)         │
        └──────────────────────┘
```

### 2.2 Interaction Pattern

**TokenBudget** runs *first* (at retrieval time):
- Each retrieval channel declares a cost (memory match = 500 tokens, ADR snippet = 300, skill body = 1200).
- The `budget_remaining` for the turn (default 4000 for context, configurable) is decremented as matches are fetched.
- When budget is exhausted, the stage returns `degrade_reason: "token_budget"` without fetching more.
- Caller has a pre-cached fallback (the top-1 memory match is always fetched, never deferred).

**ConfidenceGate** runs *second* (during dedup):
- After all stages report their results, the dedup phase queries the **grade store** for each source's `confidence_tier`.
- Tiers are: `unknown` (0.0), `low` (0.3), `medium` (0.6), `high` (0.9), `very_high` (1.0).
- If a stage's mean score is < 0.5, its retrievals are marked `low_confidence` and are either culled or deduped down to the top-1.
- If a stage never ran (disabled or failed), its confidence tier is `unknown` and defaults to neutral (included, but with disclosure).

**PreviewBound** runs *last* (at UI render time):
- The Glass Box limits preview chips to 20 per turn (configurable).
- If a turn forged >20 artifacts, the console shows a count chip ("3 more skills") instead of inlining each one.
- Clicking the count navigates to a full artifacts page (still reachable, never hidden from the operator).
- This is *operator-facing only*; the worker never sees this limit.

**Data flow convergence:**
```
Turn starts
  │
  ├─ Retrieval stage runs (TokenBudget applied, cost tracked)
  │
  ├─ All stages complete, results ranked
  │
  ├─ Dedup phase:
  │   ├─ Query grade store for confidence tier per source
  │   ├─ Apply ConfidenceGate (cull if tier < threshold)
  │   └─ Merge and order results
  │
  ├─ Build brief text (sections: memory, ADRs, skills, approach, blockers)
  │
  ├─ LLM synthesis (optional POST-gate stage, its own quota)
  │
  ├─ Bind forged tools/skills
  │
  ├─ Emit `cel.decision` audit record (hash-chained)
  │   └─ Store `brief_sha256`, `brief_bytes`, `stages[]`, `top_score`, `degraded`
  │
  └─ Render to console
      ├─ Glass Box (final_prompt, stages, forged artifacts)
      │   ├─ PreviewBound limits preview count
      │   └─ Overflow → count chip (full list reachable via `/forged/{turn}`)
      │
      └─ Learning Ledger
          ├─ Show grade tier for each stage
          └─ Operator can add feedback (→ grade_stage)
```

---

## 3. Component Design

### 3.1 TokenBudget

**Interface:**
```python
class TokenBudget:
    """Per-turn, per-channel cost constraint."""
    
    def __init__(self, total_allowed: int = 4000, 
                 channel_costs: dict[str, int] = None):
        self.total = total_allowed
        self.remaining = total_allowed
        self.costs = channel_costs or {
            "memory": 500,           # per match
            "adrs": 300,             # per snippet
            "skills": 1200,          # per body
            "tools": 800,            # per tool
            "approach": 100,         # per line
            "blockers": 150,         # per item
        }
        self.log: list[dict] = []   # audit trail
    
    def can_afford(self, channel: str, count: int) -> bool:
        """Check if we have budget for N more items from this channel."""
        cost = self.costs.get(channel, 0) * count
        return cost <= self.remaining
    
    def charge(self, channel: str, count: int, description: str = "") -> bool:
        """Deduct cost; return True if successful, False if over-budget."""
        cost = self.costs.get(channel, 0) * count
        if cost > self.remaining:
            return False
        self.remaining -= cost
        self.log.append({
            "ts": now(),
            "channel": channel,
            "count": count,
            "cost": cost,
            "remaining": self.remaining,
            "reason": description,
        })
        return True
    
    def status(self) -> dict:
        """Current state for audit."""
        return {
            "total": self.total,
            "remaining": self.remaining,
            "spent": self.total - self.remaining,
            "exhausted": self.remaining <= 0,
            "log": self.log,
        }
```

**Usage in retrieval stages:**
```python
# In memory stage:
budget = TokenBudget(total_allowed=4000)
matches = []
for candidate in ranked_candidates:
    if budget.can_afford("memory", 1):
        if budget.charge("memory", 1, f"matched on '{query}'"):
            matches.append(candidate)
    else:
        return ContextStageResult(
            matches=matches,
            degraded=True,
            degrade_reason="token_budget_exhausted",
            status="ok"  # stage itself ran fine; cost constraint kicked in
        )
```

**Operator config** (in `tenant.corvin.yaml`):
```yaml
spec:
  context_engineering:
    token_budget:
      total_per_turn: 4000
      channel_costs:
        memory: 500
        adrs: 300
        skills: 1200
```

### 3.2 ConfidenceGate

**Interface:**
```python
class ConfidenceGate:
    """Quality filter based on stage grades."""
    
    def __init__(self, min_tier: float = 0.5, grade_store=None):
        """
        min_tier: confidence scores below this are culled.
        grade_store: reads from ce_stage_grades.json (or similar).
        """
        self.min_tier = min_tier
        self.store = grade_store
    
    def evaluate_source(self, source_id: str, stage_id: str) -> dict:
        """Get confidence tier for a (stage, source) pair.
        
        Returns:
          {
            "tier_name": "high" | "medium" | "low" | "unknown",
            "score": 0.0–1.0,
            "n_grades": int,
            "action": "keep" | "dedupe" | "cull"
          }
        """
        if not self.store:
            return {"tier_name": "unknown", "score": 0.0, "n_grades": 0, "action": "keep"}
        
        grades = self.store.get_grades(stage_id)  # [score, score, ...]
        if not grades:
            return {"tier_name": "unknown", "score": 0.0, "n_grades": 0, "action": "keep"}
        
        mean = sum(grades) / len(grades)
        tier_name = self._tier_name(mean)
        
        # Cull if below minimum; dedupe if marginal; keep if solid.
        if mean < 0.3:
            action = "cull"
        elif mean < self.min_tier:
            action = "dedupe"  # keep top-1, discard rest
        else:
            action = "keep"
        
        return {
            "tier_name": tier_name,
            "score": mean,
            "n_grades": len(grades),
            "action": action,
        }
    
    def _tier_name(self, score: float) -> str:
        """Map score to a human-readable tier."""
        if score >= 0.9:
            return "very_high"
        elif score >= 0.6:
            return "high"
        elif score >= 0.3:
            return "medium"
        elif score > 0.0:
            return "low"
        else:
            return "unknown"
    
    def apply(self, results: dict[str, list]) -> dict[str, list]:
        """
        results: {"memory": [match1, match2, ...], "adrs": [...], ...}
        
        Applies action (cull/dedupe) to each channel.
        Returns filtered results.
        """
        filtered = {}
        for channel, items in results.items():
            eval_result = self.evaluate_source(channel, channel)
            action = eval_result["action"]
            
            if action == "cull":
                filtered[channel] = []
            elif action == "dedupe":
                filtered[channel] = items[:1] if items else []
            else:  # "keep"
                filtered[channel] = items
        
        return filtered
```

**Integration point** (in dedup phase):
```python
# After all stages report results:
gate = ConfidenceGate(
    min_tier=0.5,
    grade_store=CELGradeStore(tenant_id)
)
# Apply: {"memory": [m1, m2], "adrs": [a1], "skills": [...]} 
#     -> cull low-confidence, dedupe medium, keep high
filtered = gate.apply(all_stage_results)
```

**Audit record** (in `cel.decision`):
```json
{
  "event_type": "cel.decision",
  "details": {
    "turn_id": "turn-42",
    "stages": [
      {"id": "memory", "status": "ok", "confidence_tier": "high", "score": 0.87, "count": 2},
      {"id": "adrs", "status": "ok", "confidence_tier": "medium", "score": 0.56, "count": 1},
      {"id": "skills", "status": "ok", "confidence_tier": "low", "score": 0.31, "count": 0}
    ],
    "top_score": 0.87,
    "degraded": false
  }
}
```

### 3.3 PreviewBound

**Interface:**
```python
class PreviewBound:
    """Limits artifact preview chip count in the Glass Box UI."""
    
    def __init__(self, max_previews: int = 20):
        self.max_previews = max_previews
    
    def apply(self, artifacts: list[Artifact]) -> dict:
        """
        artifacts: [tool1, skill1, tool2, ...]
        
        Returns:
          {
            "previewed": [...up to max_previews...],
            "overflow_count": int,
            "overflow_link": "/vibe-engineering/forged/{turn_id}"
          }
        """
        if len(artifacts) <= self.max_previews:
            return {
                "previewed": artifacts,
                "overflow_count": 0,
                "overflow_link": None,
            }
        
        return {
            "previewed": artifacts[:self.max_previews],
            "overflow_count": len(artifacts) - self.max_previews,
            "overflow_link": f"/vibe-engineering/forged/{turn_id}",
        }
```

**Console component:**
```tsx
// pages/vibe-engineering.tsx: GlassBoxArtifacts
export function GlassBoxArtifacts({ turn_id, artifacts }) {
  const bound = new PreviewBound(20);
  const { previewed, overflow_count, overflow_link } = bound.apply(artifacts);
  
  return (
    <div>
      {previewed.map(art => <ArtifactChip key={art.name} artifact={art} />)}
      {overflow_count > 0 && (
        <ChipCount 
          label={`${overflow_count} more`}
          link={overflow_link}
        />
      )}
    </div>
  );
}
```

---

## 4. Integration Points

### 4.1 Wiring into Vibe (Context Engineering Pipeline View)

**Routes affected:**
- `GET /vibe-engineering/traces` — reads `cel.decision` audit records; includes `degraded`, `top_score`, `stages[]` per record.
- `GET /vibe-engineering/explain/{brief_sha256}` — brief text and metadata (unchanged).
- `GET /vibe-engineering/prompt/{turn_id}` — `final_prompt`, sections, CEL block (unchanged).
- `GET /vibe-engineering/forged/{turn_id}` — full tool/skill list (not preview-bounded).
- `GET /vibe-engineering/grades` — new endpoint; confidence tier per stage.
- `POST /vibe-engineering/grades/{stage_id}` — new endpoint; operator adds a grade.

**Console UI:**
- `pages/vibe-engineering.tsx` — Glass Box with CEL-Block vs. Prompt split, sections legend.
- `pages/vibe-overview.tsx` — aggregate stats (turn count, avg confidence tier, degradation rate).
- `pages/learning-ledger.tsx` — three panes: CEL-Grades (confidence), TreeOfThoughts (patterns), ULO (objectives).

### 4.2 Wiring into Brain (ExecutionContext + Learning Flywheel)

**New fields in ContextBundle:**
```python
class ContextBundle:
    brief: Brief
    memory_context: MemoryContext
    related_decisions: list[ADR]
    recommended_skills: list[Skill]
    
    # NEW: optimization signals
    token_budget: TokenBudget  # tracks cost
    confidence_gate: ConfidenceGate  # filters by quality
    preview_bound: PreviewBound  # UI preview limit
    
    # NEW: feedback loop
    turn_outcome: bool  # success/failure signal
    stage_grades: dict[str, float]  # per-stage confidence from operator
```

**Learning flywheel:**
```
1. Turn runs → CEL stages produce results
2. TokenBudget constrains retrieval volume
3. ConfidenceGate filters by stage grades (from previous turns)
4. Brief is built, synthesised, prompt is final
5. AUDIT RECORD written (cel.decision, hash-chained)
6. WORKER runs, task completes
7. Outcome signal → record_turn_outcome(turn_id, stage_ids, success=bool)
8. Operator grades stage: POST /vibe-engineering/grades/memory (score=0.9)
9. NEXT turn: ConfidenceGate reads updated grades → memory is now tier="high"
10. Flywheel tightens: high-confidence stages get more retrieval budget
```

### 4.3 Audit Trail Integration

**No weakening of compliance baseline.** Every optimization feeds the hash-chained log:

```json
{
  "event_type": "cel.decision",
  "ts": "2026-08-19T14:32:00Z",
  "tenant_id": "_default",
  "details": {
    "turn_id": "turn-42",
    "session_id": "chat:123",
    "stages": [
      {
        "id": "memory",
        "status": "ok",
        "confidence_tier": "high",
        "score": 0.87,
        "n_grades": 5,
        "count": 2,
        "token_cost": 1000,
        "sources": [
          {"id": "mem_001", "title": "Q1 OKRs", "score": 0.95}
        ]
      }
    ],
    "budget_status": {
      "total": 4000,
      "remaining": 2000,
      "degraded": false
    },
    "top_score": 0.87,
    "brief_sha256": "abc123...",
    "brief_bytes": 4521,
    "synthesised": false
  },
  "hash": "sha256(prev_hash + json(details))",
  "prev_hash": "xyz789..."
}
```

---

## 5. Data Flow: Memory → Render

```
TURN START
  │
  ├─ Initialize TokenBudget(total=4000, channel_costs={...})
  ├─ Initialize ConfidenceGate(min_tier=0.5, grade_store=GradeStore)
  ├─ Initialize PreviewBound(max_previews=20)
  │
  ├─ RUN CEL STAGES (in parallel, DAG-ordered)
  │  │
  │  ├─ Memory stage:
  │  │   ├─ Rank candidates by relevance
  │  │   ├─ Iterate: if budget.can_afford("memory", 1):
  │  │   │           budget.charge("memory", 1)
  │  │   │           add to matches
  │  │   │    else: degrade_reason="token_budget"
  │  │   └─ Report {matches: [m1, m2], status: "ok", degraded: bool}
  │  │
  │  ├─ ADRs stage:
  │  │   └─ Similar: check budget, charge, report
  │  │
  │  ├─ Skills stage:
  │  │   └─ Similar: check budget, charge, report
  │  │
  │  └─ [other stages...]
  │
  ├─ COLLECT ALL RESULTS
  │  │
  │  ├─ Apply ConfidenceGate:
  │  │  │
  │  │  ├─ For each stage:
  │  │  │   ├─ grades = grade_store.get_grades(stage_id)  # [0.8, 0.9, 0.7]
  │  │  │   ├─ mean = 0.8
  │  │  │   ├─ tier_name = "high" (mean ≥ 0.6)
  │  │  │   ├─ action = "keep" (mean ≥ min_tier)
  │  │  │   └─ filter[stage] = results[stage]  # unmodified
  │  │  │
  │  │  └─ For low-confidence stage (mean=0.3):
  │  │      ├─ tier_name = "medium"
  │  │      ├─ action = "dedupe"  (mean < min_tier)
  │  │      └─ filter[stage] = results[stage][:1]  # top-1 only
  │  │
  │  └─ return filtered results
  │
  ├─ DEDUP & BUILD BRIEF
  │  │
  │  ├─ Merge results into sections (memory, adrs, skills, approach, blockers)
  │  ├─ Format as markdown text: "## Memory\nRelevant past: ...\n\n## ADRs\n..."
  │  ├─ SHA256-hash the brief text → brief_sha256
  │  └─ Store sidecar: ~/sessions/<sid>/cel-briefs/<turn-id>.assembly.json
  │
  ├─ LLM SYNTHESIS (optional, POST-gate)
  │  │
  │  ├─ If stage enabled and egress allowed:
  │  │   ├─ claude -p <brief + task>  →  {brief: "...", needs: {tools: [...], skills: [...]}}
  │  │   └─ bundle.synthesised_prompt = new brief
  │  │
  │  └─ Else: skip, use deterministic brief
  │
  ├─ BIND TOOLS & SKILLS (ToolForge + SkillForge)
  │  │
  │  ├─ For each tool in needs: forge_tool(name, description, schema)
  │  └─ For each skill in needs: forge_skill(name, body)
  │
  ├─ BUILD FINAL PROMPT
  │  │
  │  ├─ CEL block: "## CONTEXT\n<brief>\n\n## RELEVANT DECISIONS\n..."
  │  ├─ System prompt: "You are CorvinOS..."
  │  ├─ final_prompt = sys_prompt + "\n\n" + cel_block
  │  └─ Persist to assembly sidecar
  │
  ├─ EMIT AUDIT RECORD
  │  │
  │  ├─ build cel.decision event
  │  ├─ Hash with previous hash
  │  ├─ Write to audit.jsonl (atomic, fail-closed)
  │  └─ return {hash, prev_hash, details}
  │
  ├─ RENDER TO CONSOLE (Vibe)
  │  │
  │  ├─ Glass Box:
  │  │  ├─ Show final_prompt in full
  │  │  ├─ Overlay CEL block (different color)
  │  │  ├─ Show sections legend: "✔ Memory (2 matches, tier=high)"
  │  │  └─ Apply PreviewBound to artifacts:
  │  │      ├─ if forged_tools + forged_skills ≤ 20: show all chips
  │  │      └─ else: show first 20 + count chip (→ /forged/{turn})
  │  │
  │  └─ Learning Ledger:
  │      ├─ Show grade tier for each stage
  │      ├─ Offer 👎/😐/👍 buttons → POST /grades/{stage}
  │      └─ Update audits with operator-grading event
  │
  └─ WORKER PROCESSES
      │
      └─ (consumer sees final_prompt, knows nothing of optimizations)
```

---

## 6. Invariants (Never Break These)

### 6.1 Audit Trail Integrity
- **Hash chain is immutable.** Every record contains `hash` and `prev_hash`; a mutation to an earlier record breaks all downstream hashes.
- **No PII in audit details.** The `cel.decision` record can be shown to third parties (with permission); it must never contain user data, prompt text, or model outputs.
- **Content-free signatures only.** Stage confidence tiers, token costs, and degrade reasons are allowed; the brief text lives in a separate sidecar (`brief_sha256` as key, not value).

### 6.2 Graceful Degradation
- **Budget exhausted?** → Use the pre-cached fallback (always the top-1 from each stage). Never block the turn.
- **Confidence gate unavailable?** → Treat all stages as neutral (action="keep"). Process continues.
- **Grade store broken?** → Confidence tier = `unknown`. Continue without filtering.
- **LLM synthesis timeout?** → Use the deterministic brief. Worker still gets a valid prompt.
- **Preview bound overflowed?** → Show counts and links, never blank artifacts.

### 6.3 Feature Flags Always Default-Off
- `token_budget_enabled` → default false
- `confidence_gate_enabled` → default false
- `cross_device_sync` → default false
- Operator enables in Settings → Features; no env var, no hardcoded override.

### 6.4 E2E Wiring Proof
- **TokenBudget** must have ≥1 production caller in a retrieval stage (e.g., memory stage's `can_afford` check).
- **ConfidenceGate** must have ≥1 production caller in the dedup phase (verified via E2E: real turn → audit record shows `confidence_tier` filled).
- **PreviewBound** must have ≥1 production caller in the Glass Box UI (verified via E2E: turn with >20 artifacts → count chip renders).

### 6.5 Learning Flywheel Closure
- `record_turn_outcome()` must have ≥1 production caller (the chat_runtime turn completion hook).
- Outcome grades must contribute to the grade store (non-promoting, but recorded).
- A later turn's ConfidenceGate must read the updated grades.

---

## 7. Phase Dependencies

The optimization initiative unfolds in five coordinated phases (G1–G5 from the glass-box plan):

| Phase | Goal | Prerequisite | Ship Status |
|-------|------|---|---|
| **G1** | Glass Box (prompt transparency) | None | ✅ DONE (ADR-0368) |
| **G2** | Vibe Overview (observability layer) | G1 working | ✅ DONE |
| **G3** | Learning Ledger + Grade UI | G1, G2 | ✅ DONE (ADR-0371) |
| **G4** | Outcome-Feedback Wiring | G3 (grade store exists) | ✅ DONE (ADR-0371) |
| **G5** | Tenant Sync (cross-device state) | G3, G4 (grades are synced) | ✅ DONE (ADR-0369) |

**G1–G5 are delivery sequence, not technical dependencies.** But:
- **G3 must ship before G4** (grade store must exist for outcome → grade to be useful).
- **TokenBudget, ConfidenceGate, PreviewBound are orthogonal** and can ship in any order. They don't call each other.
- **Learning flywheel closure (G4) depends on grades (G3)** to provide the `confidence_tier` that ConfidenceGate reads.

---

## 8. Known Limitations & Trade-Offs

### 8.1 ConfidenceGate Uses Historical Grades, Not Real-Time Quality
**Limitation:** a stage's mean score is based on operator feedback from *past* turns. A newly-enabled stage (0 grades) gets tier=`unknown` and action=`keep` (conservative, no filtering).

**Trade-off:** this is intentional. A stage we've never graded is treated as neutral; we only cull/dedupe stages that have *proven* low quality. The alternative (rely on the stage's own internal confidence signal) would couple the gate to the stage implementation.

**Mitigation:** G3 + G4 let the operator quickly grade a stage up or down; the flywheel tightens over 10–20 turns.

### 8.2 PreviewBound is Console-Only, Not API
**Limitation:** the full artifact list is always available at `/vibe-engineering/forged/{turn}`, but the chat console preview is capped at 20.

**By design:** the worker never sees this limit. The cap is an operator-comfort UX choice, not a cost or security boundary. A hypothetical API consumer can request the full list.

### 8.3 TokenBudget Uses Heuristic Costs
**Limitation:** `{"memory": 500, "adrs": 300, "skills": 1200}` are operator-configurable but not measured in real-time.

**Trade-off:** measuring real tokens for each stage would require running token-counting on every candidate before deciding whether to fetch it — that's slower than a heuristic. The heuristic is conservative (skills cost more because they're longer), and the audit log records actual token spend post-decision.

**Mitigation:** operator can tune costs in `tenant.corvin.yaml` based on observed token logs.

### 8.4 PII Backstop in Sync is Best-Effort, Not Fail-Closed
**Limitation:** `_assert_no_raw_pii` scans for common PII shapes (email, phone, credit card) but can't catch all forms of GDPR-relevant data (a medical condition mention, a person's address).

**Mitigation:** GPG encryption is *mandatory* (not optional) for sync payloads. The backstop reduces risk; encryption is the hard guarantee.

---

## 9. Alternatives Considered

### Why TokenBudget Alone Isn't Enough
Limiting tokens controls *cost* but not *quality*. A cheap, low-confidence memory match burns tokens on noise.

### Why ConfidenceGate Alone Isn't Enough
Filtering by quality controls signal but not *volume*. A high-confidence ADR that costs 5000 tokens can still overflow the budget.

### Why PreviewBound Isn't About Cost
The preview cap is operator-facing only and affects UI, not API or worker. It's a UX choice, not a cost control. (Cost is handled by TokenBudget and ConfidenceGate.)

### Why Not Combine Them Into One "Context Optimizer"?
Tempting, but they run at different layers:
- **TokenBudget** is **pull-side** (retrieval stage asks "can I afford to fetch?").
- **ConfidenceGate** is **merge-side** (dedup asks "should I include this?").
- **PreviewBound** is **push-side** (UI asks "how many chips should I show?").

Each has different caller, different failure mode, different config. Keeping them separate makes each one testable and swappable independently.

---

## 10. Success Metrics

| Metric | Target | Measurement |
|--------|--------|---|
| **Latency** | <10ms per turn (optimization overhead) | `audit.jsonl` timestamp deltas for CEL phases |
| **Cost savings** | >15% token reduction vs. baseline (via ConfidenceGate + TokenBudget) | `token_budget_status.spent` vs. `baseline_tokens` |
| **Confidence accuracy** | stage grades have >0.8 correlation with operator manual rating | post-launch user study |
| **Artifact preview quality** | operator clicks "more" <5% of the time (PreviewBound sizing) | usage log sampling |
| **Learning loop closure** | >80% of turns with outcome feedback → grade within 10 turns | `cel.decision.degraded` rate for "low confidence" stages trending ↓ |
| **E2E wiring proof** | 100% of new entry points have ≥1 real call-site + 1 passing E2E test | test coverage report |

---

## 11. Implementation Checklist

- [ ] **TokenBudget** wired into all 8 CEL stages (memory, graph, skill, adrs, toolforge, skillforge, synthesis, binding)
- [ ] **ConfidenceGate** integrated into dedup phase (calls `grade_store.get_grades(stage_id)`)
- [ ] **PreviewBound** applied in Glass Box UI (`GlassBoxArtifacts` component)
- [ ] **Audit record** includes `budget_status`, `confidence_tier`, and `preview_overflow_count`
- [ ] **Learning Ledger** UI shows grade tier per stage (+ operator grading surface)
- [ ] **Outcome feedback** wired: `chat_runtime` calls `record_turn_outcome(tenant_id, stage_ids, success)`
- [ ] **Tenant Sync** reads/writes grades, memory, skills, learning-events (with typ-specific merge logic)
- [ ] **E2E tests** verify: turn with token budget → degrade reason in audit; low-confidence stage → culled; >20 artifacts → preview count chip
- [ ] **Operator docs** updated: how to tune TokenBudget costs, interpret ConfidenceGate tiers, use Learning Ledger to grade stages
- [ ] **Feature flags** default-off and toggle-able in Settings → Features

---

## 12. Operator Notes

(none at present; append dated entries here only)

---

**Related ADRs:** ADR-0368 (Glass Box), ADR-0369 (Tenant Sync), ADR-0370 (Vibe Overview), ADR-0371 (Learning Ledger + Outcome Wiring)  
**Related Concepts:** CONCEPT-0001 (self-learning archive), CONCEPT-0010 (unified context bridge)  
**Skill:** `assistant.corvinOS_context_optimization` (learned-experience, auto-injected during CEL optimization tasks)
