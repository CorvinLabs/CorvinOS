# Phase 2: ACP Skills Optimization Roadmap

**Status:** Ready to start (Phase 1 complete 2026-09-03)  
**Duration:** 6–8 weeks  
**Token Cost:** ~40–50K estimated  

---

## Phase 2 Scope (8 subtasks, 360h estimated)

### 2a. Learning Optimizer (ADR-0314.2) [120h]
- Feedback ingestion from confidence scores
- Config tuning loop (routing thresholds, context weights)
- A/B testing framework
- Canary deployment (10% traffic split)

### 2b. Manifest Schema Validation (ADR-0533) [90h]
- Full manifest.yaml schema
- Version matching (semver)
- Dependency resolution (DAG)
- Skill compiler (manifest → runtime)

### 2c. More OS-Skills Phase 2 (ADRs TBD) [180h]
- os.workflow_optimizer (parallel vs. serial DAG shapes)
- os.security_orchestrator (learns attack patterns)
- os.flow_guard (learns safe data shapes, L34)
- Dashboard observability Skill

### 2d. Console Integration (Vibe Dashboard) [45h]
- Skill execution telemetry panel
- Learning curve visualization
- Manual override UI
- Confidence score heatmap

### 2e. Production Rollout Plan [45h]
- Canary deployment (5% → 10% → 50% → 100%)
- Monitoring + alerting
- Rollback procedure
- SLO definition (P99 latency, error rate)

### 2f. Documentation + Training [30h]
- Skill authoring guide (for community)
- LDD methodology for Skills
- Troubleshooting guide
- Video walkthrough

### 2g. Ecosystem (Marketplace Integration) [30h]
- Community Skill submission process
- Vetting checklist
- License/CLA enforcement
- Plugin builder v2 (scaffolding)

### 2h. Performance Optimization [30h]
- Cache routing decisions (5min TTL)
- Lazy-load Skills (on-demand vs. boot)
- Parallel Skill execution
- Batch feedback ingestion

---

## Blockers Before Phase 2

- [ ] ADR-0532–0535, 0555 status → ACCEPTED (update commits field)
- [ ] Phase 1 commit merged + tested in staging
- [ ] Compliance audit sign-off (COMPLIANCE_AUDIT_PHASE1.md)
- [ ] Memory updated with Phase 2 context

---

## Deliverables (Phase 2)

| Item | Type | Owner |
|---|---|---|
| Learning optimizer | Core | Claude |
| Manifest validation | Core | Claude |
| 3 new OS-Skills | Features | Claude |
| Vibe Dashboard panel | Frontend | (External) |
| Production rollout | Ops | Shumway |
| Skill authoring guide | Docs | Claude |
| Marketplace integration | Infrastructure | (External) |

---

## ADRs to Accept/Update

- ADR-0532 → ACCEPTED (commit: 6550563b)
- ADR-0533 → ACCEPTED (commit: 6550563b)
- ADR-0534 → ACCEPTED (commit: 6550563b)
- ADR-0535 → ACCEPTED (commit: 6550563b)
- ADR-0555 → ACCEPTED (commit: 6550563b)
- ADR-0537 → NEW (LoM cryptographic binding)

---

## Start Date

**Recommended:** 2026-09-10 (1 week buffer for Phase 1 stabilization)

**First Sprint (Week 1–2):** Learning optimizer + manifest validation (2a + 2b)  
**Sprint 2 (Week 3–4):** OS-Skills (2c)  
**Sprint 3 (Week 5–6):** Dashboard + rollout plan (2d + 2e)  
**Sprint 4 (Week 7–8):** Docs + ecosystem (2f + 2g + 2h)

---

**Phase 1 → Phase 2 transition complete. Ready for optimization.**
