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
- Task → TDE Detector (5-signal ensemble) → Optimal engine
- Shows three-engine routing with cost breakdown
- Result: **48.8% aggregate token savings**

### 03. Five-Signal Detector
**File:** `03-five-signal-detector.svg`

Details the 5-signal ensemble that powers TDE selection:
- **Signal 1 (35%):** Parallelization ratio
- **Signal 2 (25%):** Iteration loops
- **Signal 3 (20%):** Context dependency
- **Signal 4 (15%):** Data volume
- **Signal 5 (5%):** Task type

Includes worked example: complex refactor with 72% TDE confidence.

### 04. Token Savings Proof
**File:** `04-token-savings-proof.svg`

Real numbers showing token savings in three scenarios:
- **Trivial task:** 62% savings (cheap pre-gate)
- **Moderate task:** 47% savings (context carryover)
- **Parallel task:** 72% savings (ACS parallelization)

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

![Token Savings Proof](docs/diagrams/04-token-savings-proof.svg)
```
