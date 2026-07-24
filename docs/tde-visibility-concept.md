# TDE-Visibility & Audit-Graph Concept

**Ziel:** Der TDE-Layer wird im Chat genauso prominent wie ACS angezeigt; der Nutzer sieht in Echtzeit, dass TDE aktiv ist und kann die Delegation als Graph visualisieren.

---

## 1. Chat-Sichtbarkeit: TDE-Progress-Notifications

### Current State (ACS)
```
⚙ Delegation an ACS-Worker gestartet (run acs-1234567-abc)…
✓ Worker A abgeschlossen  
✓ Worker B abgeschlossen  
✓ Worker C abgeschlossen
```

### Proposed TDE-Progress-Notifications

**1.1: Turn-Start Notification**
```
🔀 TDE (Tiered Delegation Engine) gestartet (run tde-1234567-xyz)…
   Analysiere Task auf Eskalationsebenen
```

**1.2: Step-by-Step Notifications (LIVE)**
```
  → Level-1 (Claude): Versuche Lösung auf Layer 1
    ↳ Ergebnis: [SOLVED | ESCALATE]
    
  → Level-2 (Sonnet): Für komplexere Reasoning
    ↳ Ergebnis: [SOLVED | ESCALATE]
    
  → Level-3 (Opus): Für XL Reasoning/Multi-Modal
    ↳ Ergebnis: [SOLVED | ESCALATE]
```

**1.3: Completion Notification**
```
✓ TDE abgeschlossen (3 Ebenen, 42 Tokens sparsam gegenüber Full-Opus)
```

### Implementation Details
- **Location:** Inline im Chat-Stream, wie ACS
- **Event-Type:** `tde.step` Events + `tde.completed`
- **User-Action:** Klick auf TDE-Badge öffnet Graph-Tab im Audit-Panel

---

## 2. Audit-Panel: TDE-Graph-Tab (neben ACS & OS-Turn)

### Current Tab-Structure
```
Chat Audit Panel
├── ACS-Graph        (Compute-Graph mit Worker-DAG)
├── OS-Turn Audit    (GDPR Art. 12 — EU AI Act)
└── [NEW] TDE-Graph  ← HIER
```

### TDE-Graph Visual Design

#### 2.1: Graph-Topology (Decision-Tree mit Annotations)

```
┌─────────────────────────────────────────────────────────┐
│ TDE Run tde-1234567-xyz — "Löse komplexe Code-Frage"  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│         ┌──────────────────────────────────┐           │
│         │ Input Task (42 Tokens)           │           │
│         │ ⓘ User: "Schreib ein Parser..."  │           │
│         └────────────┬─────────────────────┘           │
│                      │                                  │
│         ┌────────────▼──────────────┐                  │
│         │ Step 1: Layer-1 Routing   │                  │
│         │ (Haiku-Level Heuristic)   │                  │
│         │ Cost: 3K tokens          │                  │
│         └────────────┬──────────────┘                  │
│                      │                                  │
│         ┌────────────▼──────────────────────┐          │
│         │ Attempt on Layer 1 (Claude)       │          │
│         │ ✓ SOLVED (175 Tokens)             │          │
│         │ Confidence: 0.92 → STOP           │          │
│         └────────────┬──────────────────────┘          │
│                      │                                  │
│         ┌────────────▼──────────────┐                  │
│         │ Final Output (175 Tokens)  │                  │
│         │ Total Cost: 178 Tokens     │                  │
│         │ Savings vs Full-Opus: 68%  │                  │
│         └────────────────────────────┘                  │
│                                                         │
│  ▸ Details  ▸ Transcript  ▸ Cost-Breakdown            │
└─────────────────────────────────────────────────────────┘
```

#### 2.2: Compact View (für mehrere Steps hintereinander)

```
Step 1: Layer-1  →  [Solved 0.92]  ✓  175T
Step 2: Layer-2  →  [Escalate]     →
        Layer-2  →  [Solved 0.88]  ✓  420T
Total: 2 steps, 595T, 42% vs Full-Opus
```

#### 2.3: Expanded Node Details (on-Click)

```
┌─ Step 1: Layer-1 (Claude-3.5-Sonnet) ─────────────────┐
│                                                        │
│ Input Tokens:     3,142                               │
│ Output Tokens:    1,235                               │
│ Model:            claude-3.5-sonnet                   │
│ Thinking:         disabled (fast path)                │
│ Cost (est.):      ~$0.018                             │
│                                                        │
│ Confidence Score: 0.92 (SOLVED)                       │
│ Reasoning:        "Task was within scope of Layer-1   │
│                   capabilities (basic reasoning,      │
│                   <5min wall-time)"                   │
│                                                        │
│ ▸ Full Transcript                                      │
│ ▸ Latency Breakdown                                    │
└────────────────────────────────────────────────────────┘
```

---

## 3. Engine-Badge Semantics

### Current (ACS)
```
Engine: Agentic Compute Shell
Status: ✓ Completed
Workers: 4 | Duration: 12s
```

### Proposed (TDE)

```
Engine: Tiered Delegation Engine (TDE)
Status: ✓ Completed
Layers Used: 2 (Claude → Sonnet) | Duration: 8s
Token Savings: 68% vs Full-Opus
```

### Inline Chat Badge (clickable)
```
🔀 TDE (tde-1234567xyz)    ← opens graph in audit panel
```

---

## 4. L34 (Data Residency) Integration

TDE Graph shows:
- **Node**: `L34: EU-Residency-Opt-In`
- **Status**: ✓ Enforced
- **Effect**: "All layers routed to EU-only deployments"

```
Step 1: Layer-1 (Claude)  → EU-DE ✓
Step 2: Layer-2 (Sonnet)  → EU-NL ✓
```

---

## 5. Audit-Trail Entries

TDE contributes to `audit.jsonl`:
```json
{
  "ts": "2026-07-24T10:15:30Z",
  "event": "tde.step_started",
  "layer": 1,
  "model": "claude-3.5-sonnet",
  "input_tokens": 3142,
  "confidence_threshold": 0.85
}
```

```json
{
  "ts": "2026-07-24T10:15:38Z",
  "event": "tde.step_completed",
  "layer": 1,
  "status": "SOLVED",
  "confidence": 0.92,
  "output_tokens": 1235,
  "cost_usd": 0.018
}
```

---

## 6. Comparison Matrix: ACS vs OS vs TDE

| Aspect | ACS | OS-Turn | TDE |
|--------|-----|---------|-----|
| **Visibility** | Inline + Graph | Inline + Audit Log | Inline + Graph ← NEW |
| **Graph Type** | Worker DAG | Audit Trail (linear) | Decision Tree |
| **Node Info** | Worker ID, Status, Output | Event timestamp, action | Layer, Model, Confidence |
| **User Action** | Click run_id → Graph | Click turn → Log | Click engine → Graph |
| **Cost Breakdown** | Per-worker | Per-turn | Per-layer + Savings % |

---

## 7. Implementation Roadmap

### Phase 1: Chat Notifications
- **Files:** `chat_runtime.py` (add `tde.step` events)
- **UI:** `chat.tsx` (render TDE progress inline, similar to ACS)
- **Duration:** ~2h

### Phase 2: Audit-Graph Integration
- **Files:** `TdeAuditGraphPanel.tsx` (enhance existing)
- **UI:** Add to chat audit-tab bar
- **Duration:** ~3h

### Phase 3: L34 Overlay
- **Files:** `ComputeGraphView.tsx` (add L34 residency layer)
- **Duration:** ~1h

### Phase 4: Audit Logging
- **Files:** `audit.py` (TDE event schema)
- **Duration:** ~1h

---

## 8. Visual Mockup: Audit-Tab Bar

```
Chat Audit Panel
┌─────────────────────────────────────────────────────────┐
│  [ACS Graphs]  [OS-Turn Audit]  [TDE Graph]  [Details]  │
│                                  ↑ NEW                   │
└─────────────────────────────────────────────────────────┘
```

**On click "TDE Graph":**
- Shows latest TDE run from this chat (auto-detected)
- Falls back to manual run-id input (for older turns)
- Expandable node details on hover/click

---

## 9. Key Design Principles

✅ **Consistency with ACS:** Same visual language, same interaction pattern
✅ **Real-time Feedback:** Step-by-step notifications in chat
✅ **Transparency:** Full token accounting + confidence scores
✅ **Audit-Compliance:** Every step logged to `audit.jsonl`
✅ **User Control:** Manual run-id override for deep-dive analysis
✅ **Performance-Aware:** Token savings prominently shown

---

## Summary

**Before (current state):**
- ACS delegation visible ✓
- TDE runs silently in background ✗
- No audit-graph for TDE ✗

**After (proposed):**
- ACS + TDE + OS-Turn all have **unified visibility** ✓
- Real-time step-by-step progress in chat ✓
- Full decision-tree audit-graph in dedicated tab ✓
- L34 residency overlay visible ✓
- Token savings & confidence scores transparent ✓
