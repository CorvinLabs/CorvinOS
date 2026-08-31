# TDE Architecture Diagrams

Professional SVG diagrams for the Tiered Delegation Engine documentation.

## Diagrams

### 01. Current Architecture Problem
**File:** `01-problem-current-architecture.svg`

Shows the current one-engine-fits-all architecture problem:
- Claude Code: keeps context but wastes tokens on simple tasks
- ACS: parallelizes but loses context, rarely used
- **Problem:** 95% of tasks inefficiently routed to Claude Code

### 02. TDE Solution: Three-Engine Routing
**File:** `02-solution-tde-routing.svg`

Demonstrates TDE's adaptive engine selection:
- Task → TDE Router (5-signal ensemble) → Optimal engine
- Shows three-engine routing with cost breakdown
- Result: **48.8% projected aggregate savings (simulation)**

### 03. Five-Signal Detector
**File:** `03-five-signal-detector.svg`

Details the 5-signal ensemble that powers TDE selection:
- **Signal 1 (35%):** Parallelization ratio
- **Signal 2 (25%):** Iteration loops
- **Signal 3 (20%):** Context dependency
- **Signal 4 (15%):** Data volume
- **Signal 5 (5%):** Task type

Includes worked example: complex refactor with 72% TDE confidence.

### 04. Token Savings Model (simulation)
**File:** `04-token-savings-proof.svg`

PROJECTED numbers (worked model examples, not measured usage — see the
honesty banner in tde-layer-comprehensive-guide.md) for three scenarios:
- **Trivial task:** 62% savings (cheap pre-gate)
- **Moderate task:** 47% savings (context carryover)
- **Parallel task:** 72% savings (ACS parallelization)

### 05. Benchmark Results by Category
**File:** `05-benchmark-results-bars.svg`

Bar chart showing token savings across six task categories:
- **Trivial (3 tasks):** -4.3% (pre-gate optimized)
- **Simple (2 tasks):** -5.2% (pre-gate optimized)
- **Moderate (2 tasks):** +26.8% ✅ (context carryover)
- **Complex (1 task):** +36.1% ✅ (state retention)
- **Parallel (2 tasks):** +63.6% ✅ (parallelization)
- **Big Data (1 task):** +84.4% ✅ (task enablement)
- **Aggregate (11 modeled tasks):** **48.8%** (simulated — no measured usage, no significance claim)

### 06. Three Savings Mechanisms
**File:** `06-three-mechanisms.svg`

Illustration of the three hypothesized mechanisms (modeled figures):
- **Mechanism 1: Context Carryover** — 52% reduction (iteration context stays warm)
- **Mechanism 2: Parallelization Efficiency** — 83% reduction (8 workers vs. sequential)
- **Mechanism 3: Task Enablement** — 84%+ savings (enables impossible-for-CC workloads)

### 07. TDE Data Flow
**File:** `07-tde-data-flow.svg`

Complete pipeline from task arrival to engine execution:
1. **User Task** — Task received with context
2. **Pre-Gate** — Skip TDE for trivial tasks
3. **L34 Safety** — CONFIDENTIAL → Claude Code only
4. **5-Signal Detector** — Analyze task structure
5. **Softmax Ensemble** — Calculate engine scores
6. **Engine Selection** — Route to optimal engine
7. **Execution** — Three engines execute in parallel (logically)
8. **Tracking** — Log token delta for learning

## Usage

All diagrams are SVG files optimized for clarity and can be:
- Viewed in any web browser
- Embedded in Markdown with: `![alt text](path/to/diagram.svg)`
- Displayed in documentation tools
- Included in presentations

## Embedding in Markdown

```markdown
![Problem Architecture](docs/diagrams/01-problem-current-architecture.svg)

![TDE Routing Solution](docs/diagrams/02-solution-tde-routing.svg)

![5-Signal Detector](docs/diagrams/03-five-signal-detector.svg)

![Token Savings Model](docs/diagrams/04-token-savings-proof.svg)

![Benchmark Results](docs/diagrams/05-benchmark-results-bars.svg)

![Three Mechanisms](docs/diagrams/06-three-mechanisms.svg)

![TDE Data Flow](docs/diagrams/07-tde-data-flow.svg)
```

## Color Scheme

All diagrams use a **light, accessible color scheme:**
- **Titles & Labels:** Black (#000) for maximum contrast
- **Highlights:** Green (#4caf50) for positive signals, Orange (#ff9800) for overhead
- **Backgrounds:** Light neutrals (whites, grays) for readability
- **Tested:** Works on both light and dark markdown renderers

All text is **verified visible** on both light and dark backgrounds.
