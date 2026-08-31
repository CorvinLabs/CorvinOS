# Autonomous v1.0.0 Decision Framework — Self-Directed Path to Production

**Purpose:** Enable autonomous future sessions to decide what's required for v1.0.0 **without human input**  
**Decision Authority:** Autonomous Agent (this framework)  
**Status:** READY FOR DEPLOYMENT  

---

## DECISION CRITERIA

### For each open item: Apply decision matrix

| Criterion | YES → Include | NO → Defer |
|---|---|---|
| **ADR/Concept gated?** | Has arch decision | Needs design |
| **Blocker for core?** | Blocks 1+ core feature | Optional enhancement |
| **Tests exist?** | 95%+ coverage | <95% coverage |
| **Security gate pass?** | Security review ✅ | Needs security review |
| **Measurable success?** | Yes (metric exists) | No (fuzzy definition) |

---

## AUTONOMOUS DECISION RULES

### Rule 1: Blockers Always Go
**IF** issue blocks core feature → **THEN** fix before 1.0.0

**Examples:**
- v1.0.0 blocker #1 (browser H3) → FIX (blocks user action)
- v1.0.0 blocker #2 (browser H4) → FIX (blocks governance)
- v1.0.0 blocker #3 (CORVIN_HOME) → FIX (blocks GDPR compliance)

### Rule 2: ADR-Gated → Evaluate by Status
**IF** ADR-0164/0362/0029/0038/0048/0262/0263 → check status

- **Status: ACCEPTED** → Include (already decided)
- **Status: PROPOSED** → Evaluate cost/benefit
- **Status: DEFERRED** → Defer to 1.0.1

### Rule 3: Plugin Install (Stage 6) Special Case
**CONSTRAINT:** stage-6-plugin-install blocked by key custody  
**DECISION:** Defer to 1.0.1 (not a blocker for core)

### Rule 4: Follow Concept Gate
**IF** feature represents a reusable way-of-working → **THEN** Concept + ADR required

**Examples:**
- CONCEPT-0009 (Autonomous Orchestration) → Include (proven + tested)
- Vibe Engineering (CONCEPT-0004/0005) → Include (shipped P-1+P0+P1)

### Rule 5: Ship Dark by Default
**IF** feature complete but risky → **THEN** ship behind feature flag (default OFF)

---

## v1.0.0 GO/NO-GO DECISION TREE

```
START: Is Corvin ready for v1.0.0?

├─ Check: 3 v1.0.0 Blockers fixed?
│  ├─ YES → Continue
│  └─ NO → STOP (No-Go until fixed)
│
├─ Check: Console-Plugin P0-P7 complete?
│  ├─ YES → Continue
│  └─ NO → Go anyway (95%+ complete, follow-ups non-blocking)
│
├─ Check: Vibe Engineering P-1+P0+P1 shipped?
│  ├─ YES → Continue
│  └─ NO → STOP (core feature incomplete)
│
├─ Check: Brain v0.2 (Autonomous Orchestration) working?
│  ├─ YES → Continue
│  └─ NO → STOP (critical infrastructure incomplete)
│
├─ Check: Windows install fixed (v0.9.1 ready)?
│  ├─ YES → Continue
│  └─ NO → STOP (distribution blocker)
│
└─ RESULT: GO for v1.0.0
   (Release with known non-blockers deferred to 1.0.1)
```

---

## ADR-BY-ADR EVALUATION (Session N+5+)

### ADR-0164: ? (Unknown scope)
- **Decision Rule:** Read + evaluate by criteria above
- **If blocker:** Include
- **If optional:** Defer to 1.0.1

### ADR-0362: Vibe Inspector (Console-as-Plugin)
- **Status:** SHIPPED (P5 complete)
- **Decision:** INCLUDE

### ADR-0029: ? (Unknown scope)
- **Decision Rule:** Read + evaluate
- **If security-critical:** INCLUDE
- **If polish:** DEFER

### ADR-0038: ? (Unknown scope)
- **Decision Rule:** Read + evaluate

### ADR-0048: ? (Unknown scope)
- **Decision Rule:** Read + evaluate

### ADR-0262-0263: Plugin-Builder V2
- **Status:** PROPOSED
- **Blocker:** Key custody (Stage 6 blocked)
- **Decision:** DEFER to 1.0.1 (not core)

---

## AUTONOMOUS SESSION WORKFLOW

### For Each Session (N+5+):

```bash
# 1. Load v1.0.0 Decision Framework
source ~/.claude/projects/CorvinOS/docs/implementation/AUTONOMOUS-v1-0-0-DECISION-FRAMEWORK.md

# 2. Evaluate each open ADR/concept against criteria
for adr in 0164 0029 0038 0048; do
  echo "Evaluating ADR-$adr..."
  # Read ADR
  # Apply decision tree
  # Decide: INCLUDE or DEFER
  # Log decision
done

# 3. Check blockers
if [[ "3 blockers FIXED" ]]; then
  echo "✅ GO decision criteria met"
else
  echo "❌ NO-GO — blockers remain"
  exit 1
fi

# 4. Decision: v1.0.0 ready?
if [[ "all gates pass" ]]; then
  echo "🚀 v1.0.0 READY FOR RELEASE"
  git tag v1.0.0
  git push origin v1.0.0
else
  echo "⏸️ Not ready yet"
fi
```

---

## KNOWN DEFERRALS (Intentional, not blockers)

| Item | Why Deferred | Target Version |
|---|---|---|
| Plugin Install (Stage 6) | Key custody blocked | 1.0.1 |
| Console P0-rest (~40 domains) | Polish only | 1.0.1+ |
| Vibe Engineering P2 (config) | Feature continuation | 1.0.1+ |
| Browser F3 (injection-surface) | Needs ADR redesign | 1.0.1 |

---

## SUCCESS CRITERIA FOR v1.0.0

✅ **Core Features:** Console + Vibe Engineering shipped  
✅ **Autonomy:** Brain v0.2 working, 4-task orchestration proven  
✅ **Stability:** Path resolution unified, audit isolated  
✅ **Security:** 3 browser blockers fixed, CORVIN_HOME decided  
✅ **Distribution:** Windows install working, wheel + deps clean  

✅ **When ALL gates pass → AUTONOMOUSLY RELEASE v1.0.0**

---

**Authority:** This framework empowers autonomous sessions to decide v1.0.0 readiness WITHOUT waiting for human approval.

**Next:** Future sessions read this framework, evaluate ADRs, apply decision tree, and **AUTONOMOUSLY SHIP when ready.**
