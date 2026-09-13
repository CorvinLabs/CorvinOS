---
id: PLUGIN_DEVELOPMENT_GUIDE
type: reference
scope: marketplace
canonical: "null"
applies_to: [all-marketplace-plugins, skill-extensions, layer-implementations]
version: "1.0.0"
last_updated: 2026-09-13
---

# How to Build CorvinOS Plugins — Video Producer as Template

*This guide is based on the Video Producer plugin (Phase 1 Week 2 ✅). Use this as your template for building new marketplace plugins.*

---

## Table of Contents

1. [Overview](#overview)
2. [Step 1: Problem & Thesis/Antithesis/Synthesis](#step-1-problem--thesisanthitheticsynthesis)
3. [Step 2: Architecture & ADRs](#step-2-architecture--adrs)
4. [Step 3: Implementation Plan (3–4 Phases)](#step-3-implementation-plan)
5. [Step 4: Design System & Code (Parallel!)](#step-4-design-system--code-parallel)
6. [Step 5: Tests & E2E Proof](#step-5-tests--e2e-proof)
7. [Step 6: Adversarial Review](#step-6-adversarial-review)
8. [Step 7: Self-Documentation (Embedded)](#step-7-self-documentation-embedded)
9. [Compliance Checklist](#compliance-checklist)
10. [Knowledge Graph Setup](#knowledge-graph-setup)

---

## Overview

**Goal:** Build a marketplace plugin that is:
- ✅ Self-contained (everything needed lives in the plugin repo)
- ✅ Well-documented (BUILD_PROCESS.md shows how you got here)
- ✅ LDD-compliant (Dialektical reasoning before each phase)
- ✅ Adversarially reviewed (4 attack vectors, 0 CRITICAL findings)
- ✅ Properly integrated (Knowledge Graph links, ADR-0264 frontmatter)
- ✅ Teachable (future developers understand the "why")

**Timeline:** 8–12 weeks (3–4 phases, 2,000–3,000 LoC, 80+ tests per phase)

---

## Step 1: Problem & Thesis/Antithesis/Synthesis

**Before you code, define the problem and explore alternatives.**

### Template

```markdown
# Problem Statement

**What problem does this plugin solve?**
- E.g., "Video Producer: How to create high-quality marketing videos from PowerPoints?"

**Why is it important?**
- User impact: ...
- Compliance requirement: ...
- Blocker for other plugins: ...

# Design Space: Thesis/Antithesis/Synthesis

## Thesis
**Approach:** [conservative/simple approach]
- Pros: ...
- Cons: ...
- Hidden assumptions: ...

## Antithesis
**Approach:** [ambitious/flexible approach]
- Pros: ...
- Cons: ...
- Hidden assumptions: ...

## Synthesis (CHOSEN)
**Approach:** [hybrid, combining best of both]
- Design decision: ...
- Constraint enforcement: ...
- Learning hooks: ...
- Audit trail integration: ...

**Why this?**
- Balances [Pro from Thesis] + [Pro from Antithesis]
- Mitigates [Con from Thesis] by [mechanism]
- Mitigates [Con from Antithesis] by [mechanism]
```

### Video Producer Example

**Problem:** Create high-quality marketing videos from PowerPoints (narration + slides + animations).

**Thesis:** Hardcoded quality gates (DRAFT/PRODUCTION/BROADCAST tiers) → safe, predictable, but rigid.

**Antithesis:** LLM-based adaptive scoring → flexible, but fragile (hallucinations).

**Synthesis:** 5-Component Deterministic Scorer (hardcoded thresholds + feedback loops for tuning).

---

## Step 2: Architecture & ADRs

**ADRs are not optional. Write them BEFORE code.**

### When to Write an ADR

| Change | ADR Needed? | Why |
|---|---|---|
| New plugin | ✅ YES | Structural decision |
| New protocol/wire-format | ✅ YES | Interface contract |
| New compliance mechanism | ✅ YES | Regulatory binding |
| Change to audit trail | ✅ YES | Data integrity |
| New layer-level contract | ✅ YES | Cross-module coupling |
| Refactor (0 behavior change) | ❌ NO | Commit message sufficient |
| Bug fix | ❌ NO | Commit message sufficient |

### ADR-0264 Frontmatter (MANDATORY)

Every ADR starts with this metadata block:

```yaml
---
id: ADR-NNNN
type: adr
status: PROPOSED | ACCEPTED | SUPERSEDED
depends_on: [ADR-XXXX, ADR-YYYY]
related: [ADR-ZZZZ]
paths:
  - src/file.py
  - tests/test_file.py
docs:
  - docs/ARCHITECTURE.md
  - docs/BUILD_PROCESS.md
canonical: "../../Corvin-ADR/decisions/ADR-NNNN.md"
---
```

**Fields Explained:**
- `id`: Sequential numbering (ADR-0692, ADR-0693, etc.)
- `type`: Always "adr" (vs "concept", "reference")
- `status`: Proposal stage → Accepted (after adversarial review)
- `depends_on`: Other ADRs this decision depends on
- `paths`: Code files this ADR governs
- `docs`: Documentation files that implement this ADR
- `canonical`: Link to central Corvin-ADR repo (if exists there)

### Video Producer ADRs (Example Structure)

```
ADR-0692: Video Producer Orchestration
  ├─ depends_on: [ADR-0690 (Quality Gates), ADR-0691 (Audit Trail)]
  ├─ paths: [orchestrator.py, maestro.py]
  └─ docs: [BUILD_PROCESS.md, ARCHITECTURE.md]

ADR-0693: Asset Analyzer Worker
  ├─ depends_on: [ADR-0692]
  ├─ paths: [workers/asset_analyzer.py]
  └─ docs: [ARCHITECTURE.md]

ADR-0694: Voice + Screenshot Workers
  ├─ depends_on: [ADR-0692]
  ├─ paths: [workers/voice_synthesizer.py, workers/screenshot_capturer.py]
  └─ docs: [ARCHITECTURE.md]

ADR-0695: Assembler + YouTube Workers
  ├─ depends_on: [ADR-0692]
  ├─ paths: [workers/video_assembler.py, workers/youtube_uploader.py]
  └─ docs: [ARCHITECTURE.md]
```

### Embedding ADRs in Plugin Repo

**Structure:**
```
plugin/
├── docs/
│   ├── adrs/
│   │   ├── ADR-0692-orchestration.md         (with ADR-0264 frontmatter)
│   │   ├── ADR-0693-asset-analyzer.md
│   │   ├── ADR-0694-workers.md
│   │   └── ADR-0695-assembly.md
│   ├── concepts/
│   │   └── CONCEPT-0040-orchestrated-multi-skill.md
│   ├── BUILD_PROCESS.md                      (THIS file)
│   ├── ARCHITECTURE.md                       (system design overview)
│   └── PLUGIN_DEVELOPMENT_GUIDE.md           (THIS template)
└── src/
    └── ... (implementation)
```

**Key Rule:** Plugin ADRs have `canonical: "../../Corvin-ADR/decisions/..."` pointing to the central source of truth.

---

## Step 3: Implementation Plan (3–4 Phases)

**Break work into phases with explicit gates between them.**

### Template

```markdown
# Implementation Plan

## Phase 1: Foundation (Weeks 1–2, ~500 LoC)

**Goal:** Core infrastructure without integration.

**Deliverables:**
- [x] Architecture documented (ADR-XXXX)
- [x] Data types defined (schema, types.py)
- [x] Quality gates (hardcoded thresholds)
- [x] 20+ unit tests (0 CRITICAL findings)
- [ ] Console API (REST endpoints)

**Gate (Week 2 end):**
- [ ] All 20 tests pass
- [ ] Adversarial review (4 attack vectors)
- [ ] ADR-0264 compliance check
- [ ] Preconditions: define next phase blockers

## Phase 2: Integration (Weeks 3–5, ~400 LoC)

**Goal:** Wire Phase 1 into running system.

[similar structure]

## Phase 3: Production Hardening (Weeks 6–8, ~300 LoC)

**Goal:** Learning loops, monitoring, dashboards.

[similar structure]
```

### Video Producer Plan (Example)

**Phase 1 Week 1:** Quality Gates (20 tests, hardcoded thresholds)
**Phase 1 Week 2:** Workers + Orchestration (56 tests, precondition decorators)
**Phase 2:** Console API + Panel (12 API tests, 8 UI tests)
**Phase 3 + 4a:** Parallel Workers + YouTube (20 adversarial tests)
**Phase 4b:** Learning Loops (feedback integration, confidence tuning)
**Phase 5:** Dashboard (observability, cost breakdown)

---

## Step 4: Design System & Code (Parallel!)

**Design System ≠ "nice to have". It is input to code. Build both together.**

### Design System (Immutable Asset)

```json
{
  "version": "1.0.0",
  "name": "Video Producer Corporate",
  "locked": true,
  "thresholds": {
    "quality_tiers": {
      "DRAFT": {"clarity_min": 70, "audio_quality_min": 60},
      "PRODUCTION": {"clarity_min": 85, "audio_quality_min": 80},
      "BROADCAST": {"clarity_min": 95, "audio_quality_min": 95}
    }
  },
  "assets": {
    "fonts": ["Arial", "Helvetica"],
    "colors": ["#1e3a5f", "#ffffff"],
    "logos": ["logo_hd.png"]
  },
  "workers": {
    "slide_generator": {
      "dpi": 300,
      "format": "PNG",
      "max_text_chars": 100
    }
  }
}
```

**Key Rule:** Design System is versioned (v1.0.0, v2.0.0, ...) and locked. Changes require migration ADRs.

### Code Structure

```
plugin/
├── src/
│   ├── __init__.py
│   ├── types.py                 # Storyboard, Scene, QualityScore
│   ├── quality_gates.py         # Hardcoded thresholds
│   ├── orchestrator.py          # Main Maestro
│   ├── workers/
│   │   ├── asset_analyzer.py
│   │   ├── slide_generator.py
│   │   ├── voice_synthesizer.py
│   │   └── video_assembler.py
│   └── learning/
│       └── feedback_collector.py
├── design_system.json           # v1.0.0, locked
└── tests/
    ├── test_quality_gates.py
    ├── test_orchestrator.py
    └── test_workers_e2e.py
```

---

## Step 5: Tests & E2E Proof

**Unit tests prove function works. E2E tests prove system works.**

### Test Structure

```python
# tests/test_orchestrator_phase2.py

@pytest.mark.asyncio
async def test_orchestrate_full_pipeline():
    """E2E: Asset → Analysis → Storyboard → Workers → Video."""
    # 1. Setup
    project = create_test_project()
    
    # 2. Execute
    result = await orchestrator.orchestrate(
        asset_paths=[...],
        instructions={...}
    )
    
    # 3. Verify reachability (Phase 1 only)
    assert result["status"] in ["success", "partial"]
    assert "storyboard" in result
    
    # 4. Verify audit chain (Phase 2)
    audit_events = read_audit_chain()
    assert any(e["event_type"] == "orchestrator_started" for e in audit_events)
    assert any(e["event_type"] == "worker_executed" for e in audit_events)
    
    # 5. Verify output (Phase 2)
    assert Path(result["video_path"]).exists()

@pytest.mark.adversarial
async def test_orchestrate_with_corrupted_design_system():
    """Adversarial: What if design_system.json is mutated?"""
    # Fail-closed: should reject, not proceed with bad design
    project = create_test_project()
    mutate_design_system(project, remove_quality_thresholds=True)
    
    with pytest.raises(DesignSystemLocked):
        await orchestrator.orchestrate([...])
```

### E2E Wiring Proof Checklist

For each new entry point (function/endpoint/worker):

- [ ] Found ≥1 real call site (not test)
- [ ] Call site is traceable to external trigger (route, CLI, event, etc.)
- [ ] Created E2E test using real transport boundary
- [ ] Test fails without the code (red), passes with (green)
- [ ] Audit trail shows the event (if applicable)

---

## Step 6: Adversarial Review

**After each major phase, stress-test your design.**

### 4 Dialectic Challenges

| Challenge | Question | Example |
|---|---|---|
| **Assumption Attack** | "What if your hidden assumption is false?" | "Quality gates can change per run?" |
| **Failure Mode** | "What breaks if this component dies?" | "Asset analyzer crashes → what happens?" |
| **Scaling Attack** | "What breaks at 10x load?" | "100 videos/day → bottleneck?" |
| **Compliance Breach** | "How do I bypass this gate?" | "Can I upload without audit trail?" |

### Review Checklist (Per Phase)

```markdown
# Adversarial Review — Phase 1 Week 2

## Challenge 1: Assumption Attack
- [ ] Quality gates are immutable? Check: `design_system_locked=True`
- [ ] Workers are deterministic? Check: no RNG, seeds explicit
- [ ] Feedback doesn't modify gates? Check: only adjusts weights

## Challenge 2: Failure Mode
- [ ] Asset analyzer crashes → abort job? Check: maestro catches exception
- [ ] Slide generator is slow → cancel? Check: precondition timeout
- [ ] Network fails → retry? Check: exponential backoff

## Challenge 3: Scaling Attack
- [ ] 100 videos in parallel → orchestrator bottleneck? Check: async/await
- [ ] Audit chain grows 10x → corrupt? Check: hash validation daily
- [ ] YouTube API rate-limited → queue? Check: async batch upload

## Challenge 4: Compliance Breach
- [ ] Can I skip quality gates? Check: fail-closed, no bypass
- [ ] Can I tamper with audit trail? Check: hash-chained, immutable
- [ ] Can I upload without consent? Check: consent gate before upload

## Result
- [ ] 0 CRITICAL findings
- [ ] ≤3 MEDIUM findings (mitigated)
- [ ] ≥5 LOW findings (nice-to-have improvements)
```

---

## Step 7: Self-Documentation (Embedded)

**Your plugin is the documentation. Future developers read your code first, then BUILD_PROCESS.md.**

### Code Docstrings (Link to ADRs)

```python
async def orchestrate(self, asset_paths: list[str]) -> dict:
    """
    Main orchestration entry point.
    
    Seven-Phase Pipeline (ADR-0692):
    1. Asset ingestion
    2. Deep analysis (ADR-0693)
    3. Gate check (ADR-0690 Quality Gates)
    4. Storyboard generation
    5. Parallel Workers (ADR-0694)
    6. Video Assembly (ADR-0695)
    7. YouTube Upload
    
    Returns:
        {
            "status": "success" | "partial" | "blocked",
            "storyboard": Storyboard | None,
            "video_path": str | None,
            "youtube_task_id": str | None,
        }
    
    Raises:
        AnalysisGateFailedError: Quality gates not met (ADR-0690)
        AuditChainBrokenError: Hash-chain verification failed (ADR-0691)
    
    See Also:
        - BUILD_PROCESS.md: How this was designed
        - ARCHITECTURE.md: System overview
        - ADR-0692: Orchestration architecture
    """
```

### README Structure (Plugin Root)

```markdown
# Video Producer Skill

Orchestrated Video Production from PowerPoints.

## Quick Start

```python
from video_producer import VideoProducerOrchestrator

orchestrator = VideoProducerOrchestrator("./project")
result = await orchestrator.orchestrate(
    asset_paths=["presentation.pptx"],
    instructions={"style": "corporate"}
)
# → result["video_path"] = "./project/video.mp4"
```

## Architecture

See [ARCHITECTURE.md](docs/ARCHITECTURE.md) (system overview) and [BUILD_PROCESS.md](docs/BUILD_PROCESS.md) (design decisions).

## Design System

All videos use [design_system.json](design_system.json) (v1.0.0, locked). Custom branding requires a new project + new design_system.json.

## ADRs

- [ADR-0692](docs/adrs/ADR-0692-orchestration.md): Main orchestration design
- [ADR-0693](docs/adrs/ADR-0693-asset-analyzer.md): Asset analysis gates
- [ADR-0694](docs/adrs/ADR-0694-workers.md): Worker architecture
- [ADR-0695](docs/adrs/ADR-0695-assembly.md): Assembly + upload

## Testing

```bash
# Unit tests (all phases)
pytest tests/skills/test_*.py -v

# E2E proof (wiring verification)
pytest tests/e2e/test_video_producer_*.py -v

# Adversarial review (stress test)
pytest tests/adversarial/ -v
```

## Compliance

- ✅ Quality gates (hardcoded, fail-closed)
- ✅ Audit trail (hash-chained)
- ✅ Design system (versioned, locked)
- ✅ Learning hooks (feedback collection, ADR-0314)

## Knowledge Graph

- Source: `/home/shumway/projects/CorvinOS/core/skills/os_skills/video_producer/`
- Central: `/home/shumway/projects/Corvin-ADR/decisions/ADR-069{2..5}.md`
- Concept: `/home/shumway/projects/Corvin-ADR/concepts/CONCEPT-0040.md`
```

---

## Compliance Checklist

**Use this for every new plugin.**

```markdown
# Compliance Checklist

## Architecture & Design
- [ ] Problem statement + Thesis/Antithesis/Synthesis documented
- [ ] ADRs written (ADR-0264 frontmatter) BEFORE code
- [ ] Implementation plan (3–4 phases with gates)
- [ ] Design system locked (versioned, immutable)

## Code Quality
- [ ] Tests: ≥80 per phase (unit + E2E)
- [ ] Coverage: ≥85%
- [ ] 0 CRITICAL findings (adversarial review)
- [ ] E2E proof: reachability + real transport boundary

## Compliance (GDPR/EU AI Act)
- [ ] Audit trail: all decisions hash-chained
- [ ] Consent gate: before any PII operation
- [ ] House rules: no disable switch
- [ ] Design system: locked (no arbitrary changes)

## Documentation
- [ ] BUILD_PROCESS.md: every design decision explained
- [ ] ARCHITECTURE.md: system overview
- [ ] Code docstrings: every function links to ADRs
- [ ] README.md: quick start + architecture pointers
- [ ] Embedded ADRs: all docs/adrs/ files ADR-0264 compliant

## Knowledge Graph
- [ ] Every code file has docstrings linking to ADRs
- [ ] Every ADR has `paths:` linking to code
- [ ] `canonical:` fields point to central Corvin-ADR
- [ ] Cross-plugin references work (relative paths)

## Marketplace Ready
- [ ] Plugin installable via marketplace index v3
- [ ] Design system tunable (without code changes)
- [ ] Learning hooks integrated (feedback → confidence)
- [ ] Error messages user-friendly (no Python tracebacks)
```

---

## Knowledge Graph Setup

**Make your plugin discoverable + traversable.**

### In Every Code File

```python
"""Module docstring with ADR links.

Related ADRs:
    - ADR-0692: Orchestration architecture
    - ADR-0693: Asset analysis gates
    - CONCEPT-0040: Multi-skill orchestration pattern

See Also:
    - docs/BUILD_PROCESS.md: Design decisions
    - docs/ARCHITECTURE.md: System overview
"""
```

### In Every ADR

```yaml
---
id: ADR-0692
paths:
  - core/skills/os_skills/video_producer/src/orchestrator.py
  - core/skills/os_skills/video_producer/src/maestro.py
docs:
  - core/skills/os_skills/video_producer/docs/BUILD_PROCESS.md
  - core/skills/os_skills/video_producer/docs/ARCHITECTURE.md
canonical: "/home/shumway/projects/Corvin-ADR/decisions/ADR-0692.md"
---
```

### Query Examples

**"Which code implements ADR-0692?"**
```bash
grep -r "ADR-0692" core/skills/os_skills/video_producer/src/
# → orchestrator.py:12, maestro.py:45, ...
```

**"Which ADRs affect orchestrator.py?"**
```bash
grep -r "orchestrator.py" /home/shumway/projects/Corvin-ADR/decisions/ | grep "paths:"
# → ADR-0692, ADR-0698, ...
```

**"What was the design decision for this code?"**
```python
# From orchestrator.py docstring → find ADR-0692 → read BUILD_PROCESS.md
# → understand Thesis/Antithesis/Synthesis
```

---

## FAQ

### Q: How many ADRs do I need?
**A:** One per major architectural decision. Video Producer has 4 (arch + 3 worker types). Minimum is 1 (main ADR), maximum is ≤10 (too many = scope too wide).

### Q: Can I skip the Design System?
**A:** No. Design System is what makes plugins composable + upgradeable. Without it, every custom video requires code changes.

### Q: What if an ADR changes?
**A:** Never edit a live ADR. Write an amendment or superseding ADR (ADR-0XXX supersedes ADR-0YYY). Immutability is load-bearing.

### Q: How much documentation is enough?
**A:** If a new developer can read BUILD_PROCESS.md + ADRs and understand every design choice, you're done.

### Q: When do I write CONCEPT vs ADR?
**A:** ADR = one-time decision (e.g., "use maestro pattern"). CONCEPT = reusable way-of-working (e.g., "orchestrated multi-skill pattern" can apply to many plugins).

---

## Next Steps (After Completing This Guide)

1. **Migrate to Corvin-ADR:** Move ADRs to `/home/shumway/projects/Corvin-ADR/decisions/`
2. **Publish to Marketplace:** Register plugin in index v3 (ADR-0678)
3. **Seed Learning Skill:** Create `assistant.<plugin>_skill` for optimizer
4. **Monitor in Vibe:** Add dashboard panel for observability

---

**Template Version:** 1.0.0  
**Based On:** Video Producer Plugin (Phase 1 Week 2 ✅)  
**Last Updated:** 2026-09-13  
**Applies To:** All marketplace plugins (phase 1+)
