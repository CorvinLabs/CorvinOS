# Vibe Engineering Console Redesign — Unified UX Concept

**Status: SUPERSEDED (2026-09-05).** The five-entry NavGroup this document
describes shipped on 2026-08-27 (ADR-0431) and is gone. The group is now ONE
entry, the **Learning Dashboard** (route id still `vibe-engineering`): Brain
Monitor, Context Intelligence, Learning Hub and Session Explorer were removed
as duplicates (routes, `PANELS`, `NAV_GROUPS`, page files and the backend
capability manifest), and the audit-graph tabs that the panel carried in
between (Graph View · Inspector · Timeline, ADR-0564) were removed with them on
operator instruction. Every view section below describes a page that no longer
exists; keep them for the UX rationale, not as a map of the console. See
`docs/components/vibe-dashboard.md`.

Deltas from this document that were deliberate while the five-entry group was
live:

| This document | Shipped |
|---|---|
| "Brain Monitor" = 10 Brain v0.2 subsystems | CEL pipeline stages — the units that actually execute per turn. The 10 Brain subsystems have no mounted introspection endpoint, so a page over them could only have shown invented numbers. |
| Dashboard hosts the secondary views as tabs | Secondary views are their own routes (`/app/brain-monitor`, `/app/context-intelligence`, `/app/learning-hub`, `/app/session-explorer`); the Dashboard links to them. This document's own "NOT tabs" rule, applied one level deeper. |
| "Session Explorer (Task history)" | Turn history from the durable Decision Record (`/traces`), with brief / assembly / forged drill-down — the surface the retired Context Pipeline page owned. |

Known gap, NOT fixed by ADR-0431: the Dashboard's Learning Hub column and
`GET /vibe-engineering/state` still emit hardcoded placeholders (literal
"decision X" / "Skill Y", 42/0.87/18/94%, `talent.score = 50 + events*2`).
Brain Monitor and Session Explorer read only real endpoints.

**Objective:** Single coherent dashboard showing Brain, Context, Memory, Graph, Learning  
**Principle:** Operator perspective — "What is the agent doing RIGHT NOW and WHY?"  
**Structure:** One unified NavGroup, four interconnected view layers

---

## NAVIGATION STRUCTURE (NavGroup: "Vibe Engineering")

```
Vibe Engineering
├── Dashboard (Primary View)
├── Brain Monitor (Worker subsystems)
├── Context Intelligence (Pipeline layers)
├── Learning Hub (Feedback loops)
└── Session Explorer (Task history)
```

**NOT tabs under existing Dashboard** — **standalone nav entry** (like "Talent" today).

---

## PRIMARY VIEW: Dashboard (Unified Operator Console)

**Layout:** 3-column grid (responsive: 1-col mobile, 2-col tablet, 3-col desktop)

### **Column 1: Brain Status (Left)**
**What:** Real-time worker subsystems + decision-making  
**Shows:**
- **Active Task** (title, current phase, elapsed time)
- **Worker Status**: CostController, SafetyValidator, LoopEngineer, Orchestrator (icons + state)
- **Decision Queue** (what decision is the brain making right now? confidence score)
- **Last 5 Decisions** (decision type → outcome, scrollable list)

**Visual Style:**
- Cards with state indicators (🟢 running, 🟡 thinking, 🟠 blocked)
- Confidence bars (0-100%)
- Click on any decision → drill-down to rationale + audit trail

---

### **Column 2: Context Intelligence (Center)**
**What:** Original Context + Pipeline layers (Option B, live)  
**Shows:**
- **ORIGINAL CONTEXT** (immutable, locked icon)
  - Task description (first 100 chars)
  - User intent (first 100 chars)
  - Integrity hash (green ✓ if valid)
  - Click to expand → full text + edit timestamp
  
- **PIPELINE CONTEXT** (live, with quality tier breakdown)
  - TIER_1 (high-confidence additions): count badge
  - TIER_2 (medium-confidence): count badge
  - TIER_3 (low-confidence/filtered): count badge
  - **Entropy Score** (contradiction risk gauge, 0-100%)
    - Green (<30%): safe
    - Yellow (30-60%): caution
    - Red (>60%): alert
  - Recent additions (last 3, with source + confidence)

- **Quality Gate Policy** (selector: TIER_1 | TIER_2 | TIER_3)
  - Changes instantly which tiers appear in prompt
  - Audit logged

**Visual Style:**
- Two distinct sections (Original = locked/neutral, Pipeline = flowing/colored)
- Entropy as a circular gauge (like a fuel gauge)
- Click "Show Prompt" → modal with full rendered prompt

---

### **Column 3: Learning & Feedback (Right)**
**What:** Feedback loops, talent development, skill grading  
**Shows:**
- **Talent Score** (overall 0-100%, not mocked data)
  - Breakdown: context relevance, decision quality, outcome accuracy
  - Trend sparkline (last 24h)
  
- **Recent Feedback** (last 3 events, user + system)
  - "User approved decision X" (green ✓)
  - "Skill Y improved to grade 0.7" (⭐)
  - "Entropy detected in context" (⚠️)
  
- **Active Skills** (top 3 by recent use)
  - Name, origin (builtin/skill-forge/community)
  - Grade (0-1.0)
  - Usage count (last 24h)
  - Click → full skill details

- **Learning Rate** (iterations to mastery)
  - Tasks completed: 42
  - Avg decision confidence: 0.87
  - Feedback loops closed: 18

**Visual Style:**
- Sparklines for trends
- Skill pills with color coding (green = trusted, yellow = probation, red = untested)
- Click any skill → open Learning Hub view

---

## SECONDARY VIEW: Brain Monitor (Subsystem Detail)

**What:** Deep dive into 9+ Brain subsystems  
**Shows:**
- **8 Core Subsystems (v0.2):**
  - HealthMonitor (latency, error rates)
  - ContextBridge (original + pipeline sync)
  - LoopEngineer (iteration count, loss trend)
  - Orchestrator (decision queue, priority)
  - LearningEngine (feedback processed, grade updates)
  - CostController (budget spent vs allocated)
  - SafetyValidator (safety gates passed)
  - StrategyAdvisor (recommendations issued)

- **2 New Subsystems (v2):**
  - ToolForgeSubsystem (tools created, used, retired)
  - SkillForgeSubsystem (skills graded, promoted, demoted)

- **For each subsystem:**
  - Status (🟢/🟡/🟠/🔴)
  - Key metric (latency, queue size, grade, budget %)
  - Last action (timestamp, outcome)
  - Click → full subsystem trace (JSON audit trail, searchable)

**Visual Style:**
- Parallel timeline (each subsystem = vertical track)
- Colors consistent with decision types (e.g., cost decision = blue, safety decision = orange)
- Hoverable tooltips (latency, error count)

---

## TERTIARY VIEW: Context Intelligence (Full Pipeline)

**What:** Focused deep-dive on context layers + entropy  
**Shows:**

### **Layer A: Original Context (Immutable)**
- Task description (full text)
- User intent (full text)
- Session ID + Tenant ID
- Created timestamp
- Hash verification status (✓ valid | ✗ corrupted)
- Read-only (locked icon)

### **Layer B: Pipeline Context (Additive)**
- **All additions in chronological order** (scrollable table)
  - Text (truncated, click to expand)
  - Tier (TIER_1/2/3 badge)
  - Source (memory/graph/skill/user/feedback)
  - Confidence (0-100%)
  - Timestamp
  - Reasoning (if provided)

- **Entropy History** (line chart)
  - X-axis: iteration count
  - Y-axis: entropy score (0-1.0)
  - Shows when entropy exceeded threshold (red markers)

- **Quality Gate Policy** (selector + preview)
  - Show what prompt will include under each policy

- **Contradiction Detector** (alerts table)
  - Iteration when detected
  - What contradicted what
  - Was it rejected or accepted? (if accepted, reason)
  - Remediation (if any)

**Visual Style:**
- Scrollable table with sorting/filtering
- Entropy chart with threshold lines
- Red alert badges for rejected/flagged additions
- Click any addition → full details modal

---

## QUATERNARY VIEW: Learning Hub (Feedback + Grading)

**What:** Skill grades, feedback loops, auto-promotion  
**Shows:**

### **Section 1: Skill Catalog**
- All skills (builtin, skill-forge, community)
- Per-skill:
  - Grade (0-1.0, with color gradient)
  - Origin (badge)
  - Usage count (last 24h, 7d, 30d)
  - Auto-promotion status (eligible? when?)
  - Manual override (lock grade if needed)
- Search + filter (by grade, origin, usage)

### **Section 2: Feedback Queue**
- Pending user feedback (auto-collected from chat)
- Skill X "was this good?" (thumbs up/down)
- Skill Y "improved?" (free-form text)
- Batch approve/reject

### **Section 3: Learning Metrics**
- Total skills: 42
- Avg grade: 0.72
- Promoted this week: 3
- Demoted this week: 0
- Probation (0.3-0.5): 8 skills
- Trend sparkline (grade distribution over time)

### **Section 4: Auto-Promotion Log**
- History of skills that got promoted (grade 0.5 → 0.7+)
- Why (metric that triggered)
- When
- User approval status (pending/approved/rejected)

**Visual Style:**
- Skill pills with gradient background (green=trusted, yellow=probation, red=new)
- Feedback as chat-like messages (operator reply inline)
- Sparklines showing grade trends

---

## TERTIARY VIEW: Session Explorer (Task History)

**What:** Historical view of all tasks (past sessions)  
**Shows:**
- **Task List** (scrollable, searchable)
  - Task ID + title
  - Start time, end time, duration
  - Final status (completed/failed/abandoned)
  - Brain score (how well did the brain perform? 0-100%)
  - Context score (how clean was context? 0-100%)
  - Click → full session replay

- **Session Details Modal** (on click)
  - Timeline of all decisions (with timestamps + outcomes)
  - Context evolution (Original → Pipeline mutations over time)
  - Entropy peaks (where did contradictions happen?)
  - Skill usage (which skills ran, grades at time of run)
  - Token budget (spent vs allocated)
  - Feedback collected (user approvals/corrections)

**Visual Style:**
- Timeline view (decisions as dots, colored by type)
- Context layers animation (show how pipeline grew)
- Entropy overlay (when entropy spiked)

---

## INTERACTION PATTERNS

### **Global Interactions**
1. **Click any component → drill-down modal** (preserves nav state)
2. **Breadcrumb navigation** (Vibe Eng > Context Intelligence > TIER_1 additions)
3. **Keyboard shortcuts:**
   - `G` → jump to Brain Monitor
   - `C` → jump to Context Intelligence
   - `L` → jump to Learning Hub
   - `S` → jump to Session Explorer
   - `?` → help (shows all shortcuts)

4. **Real-time updates**
   - Refresh every 2-5 seconds (configurable)
   - New decisions appear live (no manual refresh)
   - Entropy gauge updates in real-time

5. **Export/Download**
   - Export session as JSON (audit trail)
   - Export context as markdown (debugging)
   - CSV export of skill grades

---

## RESPONSIVE DESIGN

### **Desktop (>1200px)**
- 3-column Dashboard (all visible at once)
- Secondary views in modals

### **Tablet (768-1199px)**
- 2-column Dashboard (Col1+Col2, Col3 below)
- Secondary views in full-width modals

### **Mobile (<768px)**
- 1-column Dashboard (stack all vertically)
- Collapsible sections
- Secondary views in drawer/slide-over

---

## COLOR SCHEME & ACCESSIBILITY

**Brand Colors:**
- Primary (Blue): Decisions, Brain status
- Success (Green): Valid context, trusted skills, safe entropy
- Warning (Yellow): Probation skills, caution entropy, medium confidence
- Danger (Red): Safety violations, corrupted context, low confidence
- Neutral (Gray): Filtered content, historical events, disabled

**Accessibility:**
- All status icons have text labels (not just colors)
- Contrast ratio ≥ 4.5:1 for text
- Dark mode support (auto + manual toggle)
- Font sizes: 12px (labels), 14px (body), 16px (headings)

---

## WHAT GETS REMOVED / REIMPLEMENTED

### **Keep (Reimplemented in New Unified View):**
- Context Pipeline (moved to Column 2)
- Token Metrics (moved to Brain Monitor subsystem detail)
- Your Talent (moved to Column 3)
- Task Graph (moved to Session Explorer)
- TreeOfThoughts (removed; functionality in Decision Queue + Session Explorer)

### **Remove:**
- ❌ TreeOfThoughts (rarely used; functionality in Brain Monitor + Session Explorer)
- ❌ Cross-Device Learning (moved to Learning Hub as "Feedback Queue")
- ❌ Separate "Overview" (merged into primary Dashboard)

### **NEW:**
- ✅ Brain Monitor (unified subsystem view)
- ✅ Context Intelligence (deep-dive on layers)
- ✅ Learning Hub (skill grading + feedback)
- ✅ Session Explorer (task history + replay)

---

## IMPLEMENTATION ROADMAP

### **Phase 1: Dashboard + Navigation (Week 1)**
- New NavGroup structure
- Column 1: Brain Status (static mockup first)
- Column 2: Context Intelligence (wire to Option B live data)
- Column 3: Learning Hub (wire to learning events)

### **Phase 2: Secondary Views (Week 2)**
- Brain Monitor (subsystem detail)
- Context Intelligence (full pipeline drill-down)
- Learning Hub (skill catalog + feedback queue)
- Session Explorer (task history)

### **Phase 3: Interactions & Polish (Week 3)**
- Keyboard shortcuts
- Real-time updates (WebSocket or polling)
- Export/Download
- Dark mode
- Responsive design
- Accessibility audit

### **Phase 4: E2E Testing + Deployment (Week 4)**
- Playwright tests (all views, interactions)
- Performance optimization (lazy-load secondary views)
- Canary rollout (10% → 50% → 100%)

---

## SUCCESS CRITERIA

**Operator Perspective:**
- ✅ "I can see what the brain is thinking right now" (Brain Monitor)
- ✅ "I understand what context the agent has" (Context Intelligence)
- ✅ "I know which skills are trustworthy" (Learning Hub)
- ✅ "I can replay past sessions to debug" (Session Explorer)

**Technical:**
- ✅ Live data (not mocked, except during dev)
- ✅ <500ms time-to-interact for all views
- ✅ Responsive (mobile, tablet, desktop)
- ✅ Accessible (WCAG 2.1 AA minimum)
- ✅ Real-time updates (2-5s refresh cadence)

---

**READY FOR IMPLEMENTATION IN NEXT SESSION**

This unified concept replaces all 7 existing panels with 1 coherent narrative:
- **Dashboard**: What is the brain doing RIGHT NOW?
- **Brain Monitor**: How does each subsystem work?
- **Context Intelligence**: What does the agent know + why?
- **Learning Hub**: Which skills can we trust?
- **Session Explorer**: What did we learn from past tasks?

All interconnected, all driven by real data, all focused on operator understanding.
