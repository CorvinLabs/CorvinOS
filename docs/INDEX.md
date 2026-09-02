# Documentation Index: CorvinOS Skills System

Complete navigation guide to all CorvinOS documentation.

---

## 📖 Getting Started

Start here if you're new to CorvinOS:

1. **[README.md](../README.md)** — Overview of CorvinOS
   - What is CorvinOS (agentic operating system)
   - Quick start (install, run, execute)
   - Core concepts (Skill, ACP, Learning Loop, Audit Trail)
   - Key features

2. **[Skills System](skills-system.md)** — What Skills are and how to write them
   - Skill definition and lifecycle
   - Creating custom Skills
   - Skill metadata and interface
   - Examples (simple, with dependencies, with learning)

3. **[ACP Vision](acp-vision.md)** — Why Skills matter
   - Replacing L-Layers with Skills
   - Self-learning system architecture
   - 4 load-bearing boundaries
   - Phase 1–3 roadmap

---

## 🎯 Core Concepts

Deep dive into essential ideas:

### [Learning Loop](learning-loop.md)
How Skills improve through feedback:
- S-curve convergence (3 phases)
- Feedback types (outcome, preference, confidence, metric)
- The Optimizer (weekly parameter tuning)
- Real examples (routing learns complexity threshold)
- Convergence tracking + non-convergence alerts

### [Audit Trail](audit-trail.md)
Immutable event logging for compliance:
- Why audit matters (GDPR Art. 30/32, EU AI Act)
- Audit event schema (all required fields)
- Hash-chaining proof (tamper-evident)
- Tenant isolation (no cross-tenant leakage)
- LoM binding (proving code identity)
- Boot tripwire (startup verification)

### [Skills API Reference](skills-api-reference.md)
Complete API documentation:
- Registry API (`register_skill`, `execute_skill`, `get_skill`, `list_all_skills`)
- Skill class interface (`execute()`, `call_skill()`, `get_config()`, `get_confidence()`)
- Composition API (dependencies, dependency resolution)
- Learning API (`get_feedback_schema()`, `submit_feedback()`)
- CLI commands (`corvin skill`)
- Error handling (exception hierarchy)

---

## 🔧 Advanced Topics

Build advanced Skill systems:

### [Composable Programs](composable-programs.md)
Write Skills that call other Skills:
- Skills as imports (Python-like composition)
- Composition patterns (linear chain, fan-out, conditional, recursive)
- Dependency management (declaration, resolution, validation)
- Error handling (failure propagation, fallback, graceful degradation)
- Real examples (document pipeline, ensemble classifier)
- Testing composition (unit, E2E, invariants)

### [Deployment Guide](deployment-guide.md)
Rolling out Skill changes safely:
- Staged rollout strategy (10% → 50% → 100% over 7 days)
- Canary monitoring (latency, errors, confidence)
- Automatic gates (hard stops if metrics exceed tolerance)
- Instant rollback (per-Skill, < 1 second)
- Zero-downtime architecture
- A/B equivalence testing
- Post-deploy verification
- Compliance gates (audit chain, consent, dependency validation)

---

## 📚 Reference

Comprehensive references and guides:

### [Big Bang Migration](big-bang-migration.md)
How CorvinOS replaced feature flags with Skills:
- Why feature flags were wrong (no versioning, no learning)
- The Big Bang decision (ADR-0544)
- Migration timeline (Weeks 11–13)
  - Week 11: Foundation (write Skills, test, prepare)
  - Week 12: Canary (10% traffic, safety gates)
  - Week 13: Ramp + GA (50% → 100%)
- Compliance gates (audit chain verification)
- Rollback plan (available for 2 weeks)
- Lessons learned

---

## 🏗️ Architecture Documents

System design and architecture:

### [Architecture Overview (README)](../README.md#architecture-overview)
Visual overview of CorvinOS architecture:
- Meta-Skills Layer (immutable, fail-closed)
- OS-Skills Layer (versioned, self-learning)
- Plugin Layer (extensible, community)
- Audit Backbone (immutable event log)
- Learning Loop (feedback → optimization)

---

## 📋 Quick Reference Tables

### Feedback Types

| Type | Question | Values | Impact |
|---|---|---|---|
| **Outcome** | Was I correct? | yes, no, maybe | Adjust parameters |
| **Preference** | What's your style? | concise, detailed, formal | Tune config |
| **Confidence** | How sure am I? | 0.0–1.0 | Calibrate scoring |
| **Metric** | What's the cost? | latency, cost, errors | Optimize thresholds |

### Skill Metadata

| Field | Type | Required | Purpose |
|---|---|---|---|
| **skill_id** | string | ✅ YES | Unique identifier |
| **version** | string | ✅ YES | Semantic version |
| **origin** | string | ✅ YES | builtin \| vetted \| community |
| **boot_layer** | string | ✅ YES | meta \| core \| bundled \| installed |
| **depends_on** | string[] | ❌ OPTIONAL | Skills this one calls |
| **description** | string | ❌ OPTIONAL | Brief description |
| **author** | string | ❌ OPTIONAL | Author email |
| **tags** | string[] | ❌ OPTIONAL | Search tags |

### Event Types

| Event | When Fired | Contains | Audit |
|---|---|---|---|
| **skill_loaded** | Skill registered | skill_id, version, dependencies | Prove code is running |
| **skill_executed** | Skill.execute() completes | input, output, latency, confidence | Prove what it did |
| **skill_feedback** | User provides feedback | feedback_type, signal | Track learning |
| **skill_config_updated** | Optimizer tunes parameters | param_delta, confidence_before/after | Prove improvement |
| **consent_granted** | User grants consent | consent_type, scope, ttl | GDPR Art. 6, 7 |
| **consent_checked** | System checks consent | consent_type, decision | Prove respect |
| **house_rule_denied** | Gate blocks an action | rule_id, reason | Unambiguous denial |
| **audit_chain_verified** | Boot tripwire verifies | chain_height, verification_result | Prove chain valid |

### Deployment Stages

| Stage | Traffic | Duration | Success Criteria |
|---|---|---|---|
| **Canary** | 10% | 24h | Latency +5%, error <0.2%, confidence >85% |
| **Ramp** | 50% | 48h | Same as canary |
| **GA** | 100% | 72h+ | All metrics stable, confidence >95% |

---

## 🔍 Finding What You Need

**By Question:**

- **"What is CorvinOS?"** → [README](../README.md)
- **"How do I write a Skill?"** → [Skills System](skills-system.md)
- **"How do I call another Skill?"** → [Composable Programs](composable-programs.md)
- **"How do I deploy safely?"** → [Deployment Guide](deployment-guide.md)
- **"How are decisions audited?"** → [Audit Trail](audit-trail.md)
- **"How do Skills improve?"** → [Learning Loop](learning-loop.md)
- **"Why Skills instead of flags?"** → [Big Bang Migration](big-bang-migration.md)
- **"What's the API?"** → [Skills API Reference](skills-api-reference.md)
- **"Why Agentic Control Plane?"** → [ACP Vision](acp-vision.md)

**By Role:**

- **Skill Author** → [Skills System](skills-system.md) → [Composable Programs](composable-programs.md)
- **Operator/Deployer** → [Deployment Guide](deployment-guide.md) → [Audit Trail](audit-trail.md)
- **Compliance Officer** → [Audit Trail](audit-trail.md) → [Big Bang Migration](big-bang-migration.md)
- **System Architect** → [ACP Vision](acp-vision.md) → [Architecture Overview](../README.md#architecture-overview)
- **Developer/Engineer** → [Skills API Reference](skills-api-reference.md) → [Composable Programs](composable-programs.md)

---

## 🔗 Cross-References

### Skills System
- Depends on: [Skills API Reference](skills-api-reference.md), [Audit Trail](audit-trail.md)
- Referenced by: [ACP Vision](acp-vision.md), [Composable Programs](composable-programs.md), [Learning Loop](learning-loop.md)

### ACP Vision
- Depends on: [Skills System](skills-system.md), [Learning Loop](learning-loop.md)
- Referenced by: [README](../README.md), [Big Bang Migration](big-bang-migration.md)

### Learning Loop
- Depends on: [Skills System](skills-system.md), [Audit Trail](audit-trail.md)
- Referenced by: [ACP Vision](acp-vision.md), [Deployment Guide](deployment-guide.md)

### Audit Trail
- Depends on: [Skills System](skills-system.md)
- Referenced by: [Learning Loop](learning-loop.md), [Deployment Guide](deployment-guide.md), [Composable Programs](composable-programs.md)

### Deployment Guide
- Depends on: [Skills System](skills-system.md), [Learning Loop](learning-loop.md), [Audit Trail](audit-trail.md)
- Referenced by: [README](../README.md)

### Composable Programs
- Depends on: [Skills System](skills-system.md), [Skills API Reference](skills-api-reference.md), [Audit Trail](audit-trail.md)
- Referenced by: [Skills API Reference](skills-api-reference.md)

### Skills API Reference
- Depends on: [Skills System](skills-system.md)
- Referenced by: [Composable Programs](composable-programs.md)

### Big Bang Migration
- Depends on: [Skills System](skills-system.md), [ACP Vision](acp-vision.md), [Audit Trail](audit-trail.md)
- Referenced by: [README](../README.md)

---

## 📊 Documentation Statistics

| Document | Type | Sections | Diagrams | Code Examples |
|---|---|---|---|---|
| README.md | Overview | 15 | 1 | 3 |
| Skills System | Guide | 12 | 1 | 15 |
| ACP Vision | Guide | 8 | 2 | 5 |
| Learning Loop | Guide | 8 | 4 | 8 |
| Audit Trail | Reference | 10 | 2 | 10 |
| Deployment Guide | Guide | 9 | 2 | 8 |
| Skills API Reference | Reference | 12 | 0 | 25 |
| Big Bang Migration | Guide | 10 | 1 | 6 |
| Composable Programs | Guide | 9 | 0 | 12 |
| **TOTAL** | — | **93** | **13** | **92** |

---

## 🎓 Learning Paths

### Path 1: New to CorvinOS (2–3 hours)
1. [README](../README.md) — 10 min
2. [Skills System](skills-system.md) — 30 min
3. [ACP Vision](acp-vision.md) — 20 min
4. [Learning Loop](learning-loop.md) — 20 min
5. [Skills API Reference](skills-api-reference.md) — 30 min

### Path 2: Skill Author (4–5 hours)
1. [Skills System](skills-system.md) — 45 min
2. [Skills API Reference](skills-api-reference.md) — 45 min
3. [Composable Programs](composable-programs.md) — 45 min
4. [Audit Trail](audit-trail.md) — 30 min
5. [Deployment Guide](deployment-guide.md) — 45 min

### Path 3: Operator/Deployer (3–4 hours)
1. [Deployment Guide](deployment-guide.md) — 45 min
2. [Audit Trail](audit-trail.md) — 45 min
3. [Learning Loop](learning-loop.md) — 45 min
4. [Skills API Reference](skills-api-reference.md) (CLI section) — 30 min

### Path 4: Architect/Strategist (6–8 hours)
1. [ACP Vision](acp-vision.md) — 45 min
2. [Big Bang Migration](big-bang-migration.md) — 45 min
3. [Skills System](skills-system.md) — 45 min
4. [Audit Trail](audit-trail.md) — 45 min
5. [Composable Programs](composable-programs.md) — 45 min
6. [Deployment Guide](deployment-guide.md) — 45 min

---

## 🔗 External References

### ADRs (Architectural Decision Records)

- **[ADR-0314](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Learning Infrastructure (Event Schema)
- **[ADR-0316](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Decision History
- **[ADR-0317](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Outcome Feedback
- **[ADR-0532](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — OS-Skills Architecture
- **[ADR-0533](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Manifest Schema
- **[ADR-0534](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Feedback Integration
- **[ADR-0535](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Composition & Dependencies
- **[ADR-0544](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Big Bang Migration

### Standards & Regulations

- **[GDPR Art. 5, 6, 30, 32](https://gdpr-info.eu/)** — Data Protection
- **[EU AI Act Art. 50](https://digital-strategy.ec.europa.eu/en/policies/european-approach-artificial-intelligence)** — AI Transparency

---

## 📝 How to Contribute

To update this documentation:

1. Edit the relevant `.md` file
2. Update cross-references if structure changes
3. Run tests to verify all links work
4. Commit with message: `docs: update <filename> [skip-adr-check]`

---

## 📞 Getting Help

- **Questions?** Open an [Issue](https://github.com/CorvinLabs/CorvinOS/issues)
- **Found a typo?** Submit a PR
- **Want to contribute?** See [CONTRIBUTING.md](../CONTRIBUTING.md)

---

**Last Updated:** September 2, 2026  
**Documentation Version:** 1.0  
**CorvinOS Version:** 2.0.0 (Skills 2.0)

---

**Welcome to CorvinOS: An Agentic Operating System. Every subsystem is a Skill. Every decision is audited. Every behavior improves.**
