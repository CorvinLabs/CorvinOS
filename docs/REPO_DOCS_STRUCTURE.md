# CorvinOS Repo: Complete Documentation Structure

**This document describes the COMPLETE repo documentation organization** — where each MD file belongs, how diagrams are organized, and how everything is linked together.

**Not code implementation. Pure documentation structure for `/docs/`.**

---

## Target Directory Structure

```
/home/shumway/projects/CorvinOS/docs/
│
├── README.md                                    ← MAIN HUB (central entry point)
│
├── diagrams/                                    ← All 8 SVG diagrams
│   ├── DIAGRAM_01_ACP_VISION_HIGH_LEVEL.svg
│   ├── DIAGRAM_02_LEARNING_INFRASTRUCTURE_6D.svg
│   ├── DIAGRAM_03_PLUGIN_SYSTEM_MARKETPLACE.svg
│   ├── DIAGRAM_04_AUDIT_CHAIN_GROUND_TRUTH.svg
│   ├── DIAGRAM_05_DATA_FLOW_COMPLETE_REQUEST.svg
│   ├── DIAGRAM_06_LAYER_STACK_OVERVIEW.svg
│   ├── DIAGRAM_07_9D_LEARNING_VECTOR.svg
│   └── DIAGRAM_08_META_LOOP_DAMPING.svg
│
├── architecture/                                ← Phase A: Core Architecture (5 docs)
│   ├── 05_ARCHITECTURE_OVERVIEW.md
│   ├── 06_ACP_VISION.md
│   ├── 07_LEARNING_INFRASTRUCTURE.md
│   ├── 08_PLUGIN_SYSTEM.md
│   └── 09_AUDIT_CHAIN.md
│
├── learning/                                    ← 9D Learning Vector (3 docs)
│   ├── CONCEPT_0032_9D_DESIGN.md
│   ├── ADR_0620-0623_9D_LEARNING_VECTOR.md
│   └── PHASE_1_ROADMAP_9D_TIER2.md
│
├── quality-discipline.md                        ← LDD, ADR Gate, E2E Proof
├── layer-stack-reference.md                     ← All 36+ security layers
│
├── compliance/                                  ← Compliance & Security
│   └── 10_COMPLIANCE_BASELINE.md
│
├── implementation/                              ← Phase B (stubs)
│   ├── event-schemas.md
│   ├── skill-manifest.md
│   └── plugin-manifest.md
│
└── [existing files preserved]                   ← Keep: layer-model.md, phase-*.md, etc.
    ├── layer-model.md
    ├── layer-manifest.yaml
    ├── claude-ref/                              ← Keep (20 files, on-demand load)
    │   ├── layer-*.md                           ← Phase B: consolidate to layer-stack-reference.md
    │   ├── adr-gate.md
    │   ├── concept-gate.md
    │   ├── e2e-wiring-proof-standard.md
    │   ├── ldd-mandatory.md
    │   ├── phase-1a-voice-consolidation.md
    │   ├── phase-1b-secrets-encryption.md
    │   ├── phase-1c-tenant-plugins.md
    │   └── [17 other modern docs]
    └── [other existing structure]
```

---

## MD File Organization (16 Files Total)

### **GROUP 1: Main Hub (1 file)**

**Path:** `docs/README.md`

**Content:** (Use COMPLETE_README_WITH_9D.md template)
```markdown
# CorvinOS v2.0 — Complete Documentation

Quick Navigation:
- [Architecture Overview](#1-core-architecture)
- [9D Learning Vector](#2-9d-learning-vector)
- [Quality Discipline](#3-quality-discipline)
- [Implementation Roadmap](#4-implementation-roadmap)
- [Compliance](#5-compliance)
- [Layer Stack](#6-layer-stack)

## 1. Core Architecture (Phase A)
[Links to 05-09 docs + diagrams]

## 2. 9D Learning Vector
[Links to learning/ docs + DIAGRAM_07, DIAGRAM_08]

## 3. Quality Discipline
[Link to quality-discipline.md]

## 4. Implementation Roadmap
[Links to Phase 1, 2, 3 roadmaps]

## 5. Compliance & Security
[Link to compliance/10_*.md]

## 6. Layer Stack
[Link to layer-stack-reference.md]

[Reading paths for different roles]
[FAQ + Glossary]
```

**Size:** ~500 lines
**Status:** ✅ READY (from COMPLETE_README_WITH_9D.md)

---

### **GROUP 2: Architecture Docs (5 files)**

**Path:** `docs/architecture/`

| **File** | **Title** | **Size** | **Content** | **Diagrams** |
|---|---|---|---|---|
| `05_ARCHITECTURE_OVERVIEW.md` | System Overview & Five Layers | 2000 lines | High-level mental model, 5-layer system, data flow example, glossary | DIAGRAM_01, DIAGRAM_05 |
| `06_ACP_VISION.md` | ACP: Skills 2.0 as Control Plane | 1500 lines | Why Skills, 5 phases of L-layer replacement, Skill anatomy, integration with learning | DIAGRAM_01 |
| `07_LEARNING_INFRASTRUCTURE.md` | 6D Learning Loss Vector (Core) | 1800 lines | 6 independent loops, causal coupling, backpropagation, dashboard, ADRs 0614-0616 | DIAGRAM_02 |
| `08_PLUGIN_SYSTEM.md` | Plugin System & Marketplace | 1600 lines | 5 boot layers, plugin lifecycle, marketplace integration, trust boundaries, ADRs 0243, 0511 | DIAGRAM_03 |
| `09_AUDIT_CHAIN.md` | Audit Chain as Ground Truth | 1400 lines | Hash-chain mechanics, tenant isolation, 6 audit subsystems, operator proof workflow, RFC 3161 | DIAGRAM_04 |

**Links in each file:**
- Cross-references to other architecture docs (05→06→07→08→09)
- Embedded diagrams
- References to ADRs in Corvin-ADR
- Code locations (e.g., `core/skills/os_skills/delegation_router.py`)

**Status:** ✅ READY (05 complete, 06 complete, 07-09 in batch file)

---

### **GROUP 3: 9D Learning Vector Docs (3 files)**

**Path:** `docs/learning/`

| **File** | **Title** | **Size** | **Content** | **Diagrams** |
|---|---|---|---|---|
| `CONCEPT_0032_9D_DESIGN.md` | 9D Learning Vector Design | 5000 lines | Dialektische Synthese (thesis/antithesis/synthesis), 6D→9D transition, Tier 2 + Tier 3 explanation, damping strategy | DIAGRAM_07, DIAGRAM_08 |
| `ADR_0620-0623_9D_LEARNING_VECTOR.md` | Four New ADRs | 1500 lines | ADR-0620 (Infrastructure Loops), ADR-0621 (Meta Loop), ADR-0622 (9D Loss Schema), ADR-0623 (Damping Protocol) | DIAGRAM_07 |
| `PHASE_1_ROADMAP_9D_TIER2.md` | Phase 1: 4-Week Implementation | 2000 lines | Week-by-week breakdown (L_memory, L_plugins, L_security, integration), success gates, team breakdown, risks | None (refers to DIAGRAM_07) |

**Links in each file:**
- Cross-references between docs (CONCEPT→ADRs→Roadmap)
- References to ADRs-0614-0616 (6D core, in Corvin-ADR)
- Embedded diagrams (DIAGRAM_07, DIAGRAM_08)
- Phase 2 and Phase 3 forward references

**Status:** ✅ READY (all 3 complete)

---

### **GROUP 4: Quality & Foundation (2 files)**

**Path:** `docs/`

| **File** | **Title** | **Size** | **Content** |
|---|---|---|---|
| `quality-discipline.md` | LDD, ADR Gate, E2E Proof, Concept Gate | 1200 lines | Mandatory 12-layer LDD framework, when ADR is needed (HIGH BAR), E2E wiring proof, Concept gate, ADR migration workflow |
| `layer-stack-reference.md` | All 36+ Security/Compliance Layers | 2500 lines | Organized by category (input, context, security, audit, network, enforcement), each layer's purpose, NEW v2.0 layers highlighted |

**Status:** ✅ READY (from batch and stubs)

---

### **GROUP 5: Compliance Docs (1 file)**

**Path:** `docs/compliance/`

| **File** | **Title** | **Size** | **Content** |
|---|---|---|---|
| `10_COMPLIANCE_BASELINE.md` | EU AI Act 2026 + GDPR | 1800 lines | Regulations table, load-bearing mechanisms, absolute must-NOT rules, telemetry opt-out model, data minimization |

**Status:** ✅ READY (from batch and stubs)

---

### **GROUP 6: Implementation Reference (3 files, Phase B)**

**Path:** `docs/implementation/`

| **File** | **Title** | **Size** | **Content** | **Status** |
|---|---|---|---|---|
| `event-schemas.md` | Audit + Learning Event Schemas | 900 lines | Immutable audit event schema, learning event types, JSON examples, PII-free validation | STUB (expand Phase B) |
| `skill-manifest.md` | Skill Manifest Schema | 800 lines | plugin.json for Skills, Skill.execute() contract, versioning, config tuning | STUB (expand Phase B) |
| `plugin-manifest.md` | Plugin Manifest Schema | 900 lines | plugin.json for Plugins (trusted vs untrusted), lifecycle hooks, LoM attribution | STUB (expand Phase B) |

**Status:** 🆕 STUBS (ready for Phase B expansion)

---

## Diagram Organization (8 Files)

**Path:** `docs/diagrams/`

**All diagrams are Dark Mode, High→Low detail, embedded in relevant MD files:**

| **Diagram** | **Size** | **Embedded In** | **Purpose** |
|---|---|---|---|
| `DIAGRAM_01_ACP_VISION_HIGH_LEVEL.svg` | 1400×800 | README, 05, 06 | System architecture (5 layers) |
| `DIAGRAM_02_LEARNING_INFRASTRUCTURE_6D.svg` | 1400×900 | README, 07 | 6D loss vector, 6 feedback loops |
| `DIAGRAM_03_PLUGIN_SYSTEM_MARKETPLACE.svg` | 1400×850 | README, 08 | Boot layers, plugin lifecycle, marketplace |
| `DIAGRAM_04_AUDIT_CHAIN_GROUND_TRUTH.svg` | 1400×950 | README, 09 | Hash-chain proof system, operator queries |
| `DIAGRAM_05_DATA_FLOW_COMPLETE_REQUEST.svg` | 1400×1000 | README, 05 | 9-step complete request flow |
| `DIAGRAM_06_LAYER_STACK_OVERVIEW.svg` | 1000×1200 | README, layer-stack-reference.md | All 36+ layers at a glance |
| `DIAGRAM_07_9D_LEARNING_VECTOR.svg` | 1400×900 | README, CONCEPT_0032, ADR_0620-0623 | 9D architecture (Tier 1+2+3) |
| `DIAGRAM_08_META_LOOP_DAMPING.svg` | 1400×950 | README, CONCEPT_0032, PHASE_1_ROADMAP | Meta loop mechanics + damping |

---

## Cross-Reference Map (How Docs Link)

### **README.md**
```
├── [Section 1] → architecture/05_ARCHITECTURE_OVERVIEW.md
│   └── Links to: 06, 07, 08, 09
│   └── Embeds: DIAGRAM_01, DIAGRAM_05, DIAGRAM_06
│
├── [Section 2] → learning/CONCEPT_0032_9D_DESIGN.md
│   └── Links to: ADR_0620-0623, PHASE_1_ROADMAP
│   └── Embeds: DIAGRAM_07, DIAGRAM_08
│
├── [Section 3] → quality-discipline.md
│   └── Links to: layer-stack-reference.md
│
├── [Section 4] → learning/PHASE_1_ROADMAP_9D_TIER2.md
│   └── Links to: CONCEPT_0032 (Phase 2, 3 references)
│   └── Embeds: None (refers to DIAGRAM_07)
│
├── [Section 5] → compliance/10_COMPLIANCE_BASELINE.md
│   └── References: layer-stack-reference.md (L16, L34, L35, L37, L44)
│
└── [Section 6] → layer-stack-reference.md
    └── References: quality-discipline.md (ADR Gate, E2E Proof)
```

### **architecture/05_ARCHITECTURE_OVERVIEW.md**
```
├── Embeds: DIAGRAM_01 (system overview)
├── Embeds: DIAGRAM_05 (data flow)
├── Links to: 06_ACP_VISION.md (next read)
├── References: ADR-0532-0535 (in Corvin-ADR)
├── References: core/skills/ (code locations)
└── Glossary (defined here, linked from README)
```

### **learning/CONCEPT_0032_9D_DESIGN.md**
```
├── Embeds: DIAGRAM_07 (9D architecture)
├── Embeds: DIAGRAM_08 (meta loop + damping)
├── Links to: ADR_0620-0623_9D_LEARNING_VECTOR.md
├── Links to: PHASE_1_ROADMAP_9D_TIER2.md
├── References: ADR-0614-0616 (6D core, in Corvin-ADR)
└── References: architecture/07_LEARNING_INFRASTRUCTURE.md (6D foundation)
```

### **learning/PHASE_1_ROADMAP_9D_TIER2.md**
```
├── References: CONCEPT_0032_9D_DESIGN.md
├── References: ADR_0620-0623_9D_LEARNING_VECTOR.md
├── Links to: PHASE_2_ROADMAP (future doc)
├── Links to: PHASE_3_ROADMAP (future doc)
└── Code locations: core/learning/tier2_loops/
```

---

## Migration Script (Phase A)

**How to copy everything from `outputs/` → `docs/`:**

```bash
#!/bin/bash
cd /home/shumway/projects/CorvinOS

# Step 1: Create directories
mkdir -p docs/architecture docs/learning docs/compliance docs/implementation docs/diagrams

# Step 2: Copy main README
cp outputs/COMPLETE_README_WITH_9D.md docs/README.md

# Step 3: Copy architecture docs
cp outputs/05_ARCHITECTURE_OVERVIEW.md docs/architecture/
cp outputs/06_ACP_VISION.md docs/architecture/
# Extract 07-09 from 07-10_REMAINING_DOCS.md
# (or copy as separate files if prepared)

# Step 4: Copy 9D learning docs
cp outputs/CONCEPT_0032_9D_LEARNING_VECTOR_DESIGN.md docs/learning/CONCEPT_0032_9D_DESIGN.md
cp outputs/ADR_0620-0623_9D_LEARNING_VECTOR.md docs/learning/
cp outputs/PHASE_1_ROADMAP_9D_TIER2.md docs/learning/PHASE_1_ROADMAP_9D_TIER2.md

# Step 5: Copy foundation docs
cp outputs/[quality-discipline, layer-stack-reference, etc].md docs/

# Step 6: Copy compliance
cp outputs/COMPLIANCE_BASELINE.md docs/compliance/10_COMPLIANCE_BASELINE.md

# Step 7: Copy diagrams
cp outputs/DIAGRAM_*.svg docs/diagrams/

# Step 8: Commit
git add docs/
git commit -m "docs: refactor CorvinOS documentation (Phase A + 9D Learning)

- Architecture: 5 core docs (ACP, Skills 2.0, Learning, Plugins, Audit)
- Learning: 3 9D docs (Design, ADRs, Phase 1 roadmap)
- Quality: LDD discipline, layer stack reference
- Compliance: EU AI Act 2026 + GDPR baseline
- Diagrams: 8 SVG (Dark Mode, High→Low)
- Total: 16 MD files + 8 diagrams, fully linked

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"

git push origin main
```

---

## Navigation Flow (Reading Paths)

### **Path 1: Architect (90 min)**
README → 05_ARCHITECTURE_OVERVIEW → 06_ACP_VISION → CONCEPT_0032_9D_DESIGN → PHASE_1_ROADMAP

### **Path 2: Backend Engineer (60 min)**
README → 06_ACP_VISION → 07_LEARNING_INFRASTRUCTURE → CONCEPT_0032_9D_DESIGN → PHASE_1_ROADMAP

### **Path 3: Security/Compliance (45 min)**
README → 09_AUDIT_CHAIN → 10_COMPLIANCE_BASELINE → layer-stack-reference.md (L16, L34, L35, L37, L44)

### **Path 4: DevOps (45 min)**
README → 08_PLUGIN_SYSTEM → layer-stack-reference.md (L4, L22) → quality-discipline.md

### **Path 5: New to CorvinOS (40 min)**
README → 05_ARCHITECTURE_OVERVIEW → CONCEPT_0032_9D_DESIGN → Glossary (in README)

---

## Summary: What Gets Delivered

| **Category** | **Count** | **Total Lines** | **Status** |
|---|---|---|---|
| Architecture Docs | 5 | 7,300 | ✅ READY |
| Learning Docs | 3 | 8,500 | ✅ READY |
| Foundation Docs | 2 | 3,700 | ✅ READY |
| Compliance Docs | 1 | 1,800 | ✅ READY |
| Implementation Stubs | 3 | 2,600 | 🆕 STUB (Phase B) |
| **Total** | **16 MD** | **~24,000 lines** | **~13 complete + 3 stubs** |
| **Diagrams** | 8 SVG | — | ✅ READY (Dark Mode) |

---

## Validation Checklist

Before marking docs complete:

- [ ] All 16 MD files in correct directories
- [ ] All 8 SVG diagrams in `docs/diagrams/`
- [ ] Main README links to all sections
- [ ] Cross-references verified (no 404s)
- [ ] Diagrams embedded + render in Dark Mode
- [ ] Code locations referenced correctly
- [ ] ADR references point to `/Corvin-ADR/decisions/`
- [ ] Glossary complete (in README)
- [ ] Reading paths all work
- [ ] Git commit message follows format

---

**This is the complete REPO DOCUMENTATION STRUCTURE.**

Copy template into `docs/`, commit, done.

No code implementation. Pure doc organization + linking.
