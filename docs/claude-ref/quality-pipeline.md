# Quality Pipeline: Idea-to-Implementation (Reference)

**Status:** MVP (Tier 1: filesystem + YAML)  
**Entry point:** `from core.quality.palace import IdeaPalace`  
**CLI:** `corvin-ideas` (subcommands: create, list, search, audit)  
**ADR:** ADR-0350 (architecture), CONCEPT-0008 (full spec)

---

## Overview

The Idea-to-Implementation Pipeline enforces a four-stage lineage: **Idea → Concept → ADR → Plan**. Each stage has an upstream requirement (validated by gates), and immutable artifacts preserve the full lineage tree for auditing and traceability.

**Why:** Ideas scattered across Slack/email; Concepts without rationale; ADRs with no parent Idea; Plans with no deployment steps. This pipeline makes the lineage explicit and prevents orphans.

---

## Core Concepts

### Artifacts (4 types)

| Type | Purpose | Upstream Requirement | Status Values |
|------|---------|----------------------|----------------|
| **Idea** | Raw problem statement | None (root) | `draft`, `proposed` |
| **Concept** | Reusable pattern | Must link to Idea | `draft`, `proposed`, `approved` |
| **ADR** | Architecture decision | Must link to Concept | `proposed`, `approved`, `active` |
| **Plan** | Deployment + rollback | Must link to ADR (blocking) | `approved`, `active`, `superseded` |

### Storage (MemPalace hierarchy)

```
~/.corvin/tenants/_default/idea-pipeline/
├── <wing>/                          # Project (e.g., "core", "platform")
│   ├── <room>/                      # Topic (e.g., "consensus-algorithms")
│   │   ├── ideas/
│   │   │   ├── idea-0001-foo.md     # Immutable markdown + YAML frontmatter
│   │   │   └── ...
│   │   ├── concepts/
│   │   │   └── concept-0001-bar.md
│   │   ├── adrs/
│   │   │   └── adr-0001-use-raft.md
│   │   ├── implementation-plans/
│   │   │   └── impl-0001-rollout.md
│   │   └── metadata.jsonl           # Fast lookup index (append-only)
│   └── ...
└── ...
```

**Immutability:** Once written, artifacts cannot be overwritten (FileExistsError raised). Supersede old artifacts; create new ones.

---

## CLI: `corvin-ideas`

### Create

```bash
corvin-ideas create idea "Problem statement" --room consensus --wing core
# → IDEA-0001: Problem statement

corvin-ideas create concept "Raft Algorithm" --room consensus --wing core --upstream IDEA-0001
# → CONCEPT-0001: Raft Algorithm (links to IDEA-0001)

corvin-ideas create adr "Use Raft" --room consensus --wing core --upstream CONCEPT-0001
# → ADR-0001: Use Raft (links to CONCEPT-0001)

corvin-ideas create implementation-plan "Rollout" --room consensus --wing core --upstream ADR-0001
# → IMPL-0001: Rollout (links to ADR-0001)
```

### List

```bash
corvin-ideas list                           # All artifacts, all wings/rooms
corvin-ideas list --wing core               # Filter by wing
corvin-ideas list --wing core --room consensus  # Filter by room
corvin-ideas list --type ideas              # Filter by type
```

### Search

```bash
corvin-ideas search "consensus"             # Full-text search across all artifacts
```

### Audit

```bash
corvin-ideas audit                          # Check all wings for orphans, cycles
corvin-ideas audit --wing core              # Check specific wing

# Output:
# Wing: core
#   Room: consensus
#     CONCEPT-0001 [warn]: Idea has no tags; recommend adding at least one
#     IMPL-0001 [fail]: upstream ADR not found
```

---

## Quality Gates

Four validators enforce lineage constraints:

### IdeaGate

- **Checks:** Name, status, tags (recommended), inspiration_context (recommended)
- **Verdict:** `PASS` (all required fields present) | `WARN` (missing optional) | `FAIL` (missing required)
- **Code:** `from core.quality.gates import IdeaGate; gate = IdeaGate(); result = gate.validate(idea)`

### ConceptGate

- **Checks:** Name, status, tags (required ≥1), upstream Idea (blocking)
- **Verdict:** Blocks creation if upstream missing
- **Code:** `result = ConceptGate().validate(concept, drawer_manager)`

### ADRGate

- **Checks:** Name, status, upstream Concept (blocking), tags (recommended)
- **Verdict:** Blocks if upstream missing
- **Code:** `result = ADRGate().validate(adr, drawer_manager)`

### ImplementationGate

- **Checks:** upstream ADR (blocking), ≥1 deployment step (blocking), success criteria (blocking), rollback procedure (recommended)
- **Verdict:** Blocking — Plan cannot exist without all required fields
- **Code:** `result = ImplementationGate().validate(plan, drawer_manager)`

### PipelineAudit

- **Checks:** All artifacts against their respective gates, plus:
  - Orphan detection (Concepts/ADRs/Plans without upstream)
  - Cycle detection (circular upstream/downstream)
- **Code:** `audit = PipelineAudit(); results = audit.audit_all(drawer_manager)`

---

## Bidirectional Upstream Backfill

Auto-generate missing upstream artifacts (placeholder text; AI generation Phase 5):

```python
from core.quality.backfill import UpstreamBackfill

backfill = UpstreamBackfill(palace)

# Auto-generate missing Idea for a Concept
idea_id = backfill.ensure_idea_upstream(concept.id, concept, auto_generate=True)

# Audit and backfill entire wing
result = backfill.backfill_lineage('core', auto_generate=True)
# → {'filled': 5, 'warnings': [...]}
```

**Behavior:**
- Generates placeholder artifact with `auto_generated=True` tag
- Sets bidirectional links (upstream + downstream)
- If `auto_generate=False`, reports warnings instead
- Preserve existing upstream (no regeneration)

---

## Artifact Models (Python API)

### Idea

```python
from core.quality.models import Idea, Status
from datetime import datetime

idea = Idea(
    id='IDEA-0001',
    name='Distributed Consensus Problem',
    room='consensus-algorithms',
    wing='core',
    status=Status.PROPOSED,
    created_at=datetime.now(),
    tags=['consensus', 'distributed-systems'],
    inspiration_context='Observed consensus gaps in microservices',
)
```

### Concept

```python
concept = Concept(
    id='CONCEPT-0001',
    name='Raft Algorithm',
    room='consensus-algorithms',
    wing='core',
    status=Status.DRAFT,
    created_at=datetime.now(),
    upstream='IDEA-0001',  # Required
    tags=['raft', 'consensus'],
)
```

### ADR

```python
from core.quality.models import ADR

adr = ADR(
    id='ADR-0321',
    name='Use Raft for Distributed Consensus',
    room='consensus-algorithms',
    wing='core',
    status=Status.PROPOSED,
    created_at=datetime.now(),
    upstream='CONCEPT-0001',  # Required
    tags=['raft', 'decision'],
)
```

### ImplementationPlan

```python
from core.quality.models import ImplementationPlan

plan = ImplementationPlan(
    id='IMPL-0001',
    name='Raft Rollout Plan',
    room='consensus-algorithms',
    wing='core',
    status=Status.APPROVED,
    created_at=datetime.now(),
    upstream='ADR-0321',  # Required (blocking)
    deployment_steps=[
        'Deploy consensus-service v1.0 to staging',
        'Run smoke tests',
        'Canary to 5% prod',
        'Full rollout',
    ],
    success_criteria='Raft election time < 500ms, quorum > 99.9%',
    rollback_procedure='Revert to v0.9 + 15min sync',
    rollout_sequence='canary → staged → full',
)
```

All models serialize to markdown + YAML frontmatter via `Drawer` class.

---

## SkillForge: Reusable Gate Skills

Five skills available for injection into future turns (learned-experience type, project scope):

| Skill | Purpose |
|-------|---------|
| `idea-gate` | Validate ideas (permissive; recommends tags + context) |
| `concept-gate` | Enforce Concept → Idea link |
| `adr-gate` | Enforce ADR → Concept link |
| `implementation-gate` | Blocking gate: requires ADR + steps + criteria |
| `pipeline-audit` | Full pipeline audit (orphans, cycles, lineage) |

**Access:** `from core.quality.skills import get_skill_catalog, list_skills; list_skills()`

---

## Compliance & Audit

### Immutability guarantee

Once an artifact is written to disk, it cannot be modified (enforced by FileExistsError). This makes the full lineage history immutable and auditable.

### Audit Trail

All gate results include:
- Gate name, artifact ID, verdict (PASS/WARN/FAIL)
- List of issues (blocking failures)
- List of warnings (recommend but not required)

### Consent & Opt-out

Gates are enforcement-only; no opt-out flags. ImplementationGate is blocking and cannot be bypassed (no `--force` flag, no env var override).

---

## Phases & Roadmap

**Phase 1 (MVP, NOW):**
- Filesystem + YAML metadata (done)
- CLI + SkillForge (done)
- Bidirectional backfill (placeholder generation, done)
- Immutability + orphan detection (done)

**Phase 2 (Optional, future):**
- Local embeddings + similarity search
- Duplicate detection
- SQLite storage layer

**Phase 3 (Optional, future):**
- Knowledge graph + temporal lineage
- Query support ("all Plans from this Idea?")
- Bulk-import 350+ existing Corvin-ADRs
- Semantic generation via AI

---

## See Also

- **ADR-0350:** Architectural Decision (MVP design, trade-offs, validation)
- **CONCEPT-0008:** Full Idea-to-Implementation spec (345 KB, all tiers, migration strategy)
- **ADR-0321:** Storage backend (Tier 1/2/3 architecture)
- **ADR-0322:** Quality gates (lineage enforcement, upstream validation)
- **ADR-0323:** Knowledge graph lineage (Phase 3, future work)

---

## Quality Gates console — `core/quality_gates/` (ADR-0688)

A **separate module** from `core.quality.gates` above: four validators
(`IdeaGate`, `ConceptGate`, `ADRGate`, `ImplementationPlanGate`) that judge the
markdown artifacts of the Corvin-ADR checkout and write every verdict as a
hash-chained `gate_events` row in `<tenant>/global/quality_gates.db` (DuckDB).
The console page `/console/app/quality` renders those rows and nothing else.

**What produces events.** The git hooks the ADR describes are not installed;
the only producer is the console's **Run all gates** (`POST
/v1/console/api/quality/gates/run/all`). Since 2026-09-20 that is a real job on
a daemon thread: `core/quality_gates/artifacts.py` resolves the checkout
(`CORVIN_ADR_ROOT` → `../Corvin-ADR` → `corvin_decisions/`), projects each file
of `decisions/`, `concepts/`, `implementation-plans/`, `ideas/` onto the dict
its gate checks, runs the real validator, writes the gate event and a
`kg_nodes` projection, and reports `artifacts_done / artifacts_total` on
`GET .../results/{run_id}` (the page's progress bar). Before that date the run
wrote one hard-coded `pass` per gate, and the page fetched
`/v1/console/quality/...` (no `/api`), so every call 404ed and the page showed
zeros, "Average: NaN%" and "Trend: Up" over no data.

**Artifact identity is the file stem**, never the declared frontmatter `id`:
77 live decisions declare the placeholder `ADR-0000` and seven declare
`ADR-0469`, so a verdict keyed on the declared id could not be traced to a
file. The declared id travels as `declared_id`.

**Endpoints** (router prefix `/api/quality`, all tenant-scoped):

| Route | Renders as |
|---|---|
| `GET gates/status` | per-gate `current` (the NEWEST verdict per artifact, however old — one per artifact, so a second run does not double it), plus 24 h / 7 d verdict counts aggregated in SQL (never a row limit — the old "last 1000 rows" cut the gates that ran first), `pass_percentage` **null** when a gate has no event in the window, `events_24h`, `events_total`, `as_of` (newest verdict timestamp), `source_root`, `last_run` |
| `GET gates/trend?days=` | one point per **UTC day that has events**; a day without a run is a gap, not 0 % |
| `GET gates/failures?scope=window\|current&hours=&limit=` | `window` (default): fail/warn verdicts of the last `hours`; `current`: artifacts whose newest verdict is fail/warn (`hours` ignored). Rows carry the validator's own `reason`, `findings_count`, `artifact_type` |
| `POST gates/run/all` → `GET gates/results/{id}` | the job above; a checkout that cannot be found is a `failed` run that says so |

**Page rules (ADR-0761/0763).** Tiles, the table's main columns and the
failure list show the **current state** (`current`, `scope=current`), dated by
"Newest verdict". Until 2026-09-22 they read only the 24 h window, so three
days after the last run the page showed zeros and "not run" over 2 184
recorded verdicts — nothing runs the gates on a schedule, and an unchanged
artifact does not stop failing because a day passed. The 24 h / 7 d windows
remain as columns, counted in verdicts (a second run judges every artifact
again). Below two trend points the chart is a
bar and the caption says it is not a trend. A 404 build says "not available
on this build" instead of zeros. Deploy marker: the page caption string in
`src/pages/quality.tsx` (`MARKER_QUALITY`).

**Timestamps** are the audit logger's `YYYY-MM-DDTHH:MM:SS.ffffffZ` shape
(`_iso()` in the route); the SQL windows compare strings, so every writer must
keep that shape.

**Proof.** `tests/e2e/test_quality_gates_console_e2e.py` (HTTP, temp
`CORVIN_HOME`, temp ADR root with known verdicts, chain verifies) and
`web-next/tests/unit/quality-page.test.tsx` (MSW).

