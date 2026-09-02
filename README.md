# CorvinOS: An Agentic Operating System

[![CI/CD](https://img.shields.io/badge/ci%2Fcd-passing-brightgreen)](https://github.com/CorvinLabs/CorvinOS)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)

## What is CorvinOS?

**CorvinOS** is a self-learning, agentic operating system that replaces hardcoded system layers with **Skills** — composable, auditable, self-optimizing programs. Every system function—from request routing to data governance—becomes a versioned Skill that learns from user feedback and converges to optimal behavior.

### Core Vision

```
Traditional OS:  Code → Features → Hardcoded behavior
CorvinOS:        Code → Skills → Audit → Feedback → Optimization → Better behavior
```

Every subsystem is:
- **Composable:** Skills call other Skills; dependencies are declared and verified
- **Auditable:** Every decision logged immutably with cryptographic proof (hash-chaining)
- **Self-Learning:** Feedback loops optimize configuration; convergence tracked in real-time
- **Compliant:** GDPR Art. 30/32, EU AI Act Art. 50 built-in; tenant isolation enforced

---

## 📚 Documentation Index

Start here for guided learning:

- **[Skills System](docs/skills-system.md)** — What Skills are, lifecycle, composition model
- **[ACP Vision](docs/acp-vision.md)** — Agentic Control Plane: replacing L-Layers with Skills
- **[Learning Loop](docs/learning-loop.md)** — Feedback, optimization, convergence metrics
- **[Audit Trail](docs/audit-trail.md)** — Immutable event logging, hash-chain proof, compliance
- **[Deployment Guide](docs/deployment-guide.md)** — Staged rollout, canary, zero-downtime
- **[Skills API Reference](docs/skills-api-reference.md)** — Registry, execution, composition
- **[Composable Programs](docs/composable-programs.md)** — Writing Skills that call other Skills
- **[Big Bang Migration](docs/big-bang-migration.md)** — From feature flags to Skills (ADR-0544+)
- **[Complete Index](docs/INDEX.md)** — All topics, quick links

---

## Architecture Overview

```svg
<svg viewBox="0 0 800 500" xmlns="http://www.w3.org/2000/svg">
  <!-- Background -->
  <rect width="800" height="500" fill="#F9FAFB"/>
  
  <!-- Title -->
  <text x="400" y="30" font-size="24" font-weight="bold" text-anchor="middle" fill="#1F2937">
    CorvinOS Architecture: Skills-Based Control Plane
  </text>
  
  <!-- Meta-Skills Layer (immutable, fail-closed) -->
  <rect x="50" y="60" width="700" height="70" rx="4" fill="#FEE2E2" stroke="#EF4444" stroke-width="2"/>
  <text x="400" y="90" font-size="12" font-weight="bold" text-anchor="middle" fill="#DC2626">
    Meta-Skills Layer (Immutable, Non-Disableable)
  </text>
  <text x="400" y="110" font-size="11" text-anchor="middle" fill="#7F1D1D">
    Audit Chain • Consent Gates • House Rules • Boot Tripwire • Disclosure
  </text>
  
  <!-- OS-Skills Layer (self-learning) -->
  <rect x="50" y="150" width="700" height="70" rx="4" fill="#DBEAFE" stroke="#3B82F6" stroke-width="2"/>
  <text x="400" y="180" font-size="12" font-weight="bold" text-anchor="middle" fill="#1E40AF">
    OS-Skills Layer (Self-Learning, Versioned)
  </text>
  <text x="400" y="200" font-size="11" text-anchor="middle" fill="#0C2340">
    Routing • Context Adapter • Workflow Optimizer • Security Orchestrator • Flow Guard
  </text>
  
  <!-- Plugin Layer (extensible) -->
  <rect x="50" y="240" width="700" height="70" rx="4" fill="#DCFCE7" stroke="#10B981" stroke-width="2"/>
  <text x="400" y="270" font-size="12" font-weight="bold" text-anchor="middle" fill="#065F46">
    Plugin Layer (Extensible, Marketplace)
  </text>
  <text x="400" y="290" font-size="11" text-anchor="middle" fill="#0D3B2E">
    Memory • Data Processing • Observability • Integration • Community Plugins
  </text>
  
  <!-- Audit Backbone -->
  <line x1="30" y1="330" x2="770" y2="330" stroke="#6B7280" stroke-width="2" stroke-dasharray="5,5"/>
  <text x="400" y="355" font-size="11" font-weight="bold" text-anchor="middle" fill="#6B7280">
    Audit Backbone: Immutable Event Log + Hash-Chain Proof
  </text>
  
  <!-- Learning Loop -->
  <rect x="50" y="380" width="700" height="80" rx="4" fill="#FEF3C7" stroke="#F59E0B" stroke-width="2" opacity="0.6"/>
  <text x="400" y="405" font-size="11" font-weight="bold" text-anchor="middle" fill="#92400E">
    Learning Loop: Execution → Feedback → Optimization → Convergence
  </text>
  <text x="400" y="425" font-size="10" text-anchor="middle" fill="#5B4B08">
    Confidence scores improve over time • Optimizer tunes parameters • Non-convergence alerts
  </text>
  <text x="400" y="445" font-size="10" text-anchor="middle" fill="#5B4B08">
    All learning events audit-logged and tenant-scoped (GDPR Art. 5, 6, 32)
  </text>
</svg>
```

---

## Key Features

### 🎯 Composable Skills
Skills call other Skills. Dependencies are declared and verified. Write complex behavior by composing simple, reusable units:

```python
@skill
def content_router(request: dict) -> dict:
    # Compose three Skills
    classified = call_skill("classify_content", request)
    adapted = call_skill("adapt_context", classified)
    routed = call_skill("select_engine", adapted)
    return routed
```

### 📊 Self-Learning
Every Skill executes, gets feedback, and optimizes. Convergence is tracked and visualized:

- **Confidence Score:** P(correct decision) over time (target: 95%+)
- **Optimizer Loop:** Weekly parameter tuning based on feedback
- **Non-Convergence Alerts:** Flag Skills stuck below 80% confidence for 2 weeks

### 🔐 Immutable Audit Trail
Every Skill decision is logged immutably with cryptographic proof:

- **Hash-Chain:** Event N links to Event N-1; tampering detected instantly
- **LoM Binding:** Proves which line of code made the decision
- **Tenant Isolation:** No cross-tenant data leakage (GDPR Art. 5)
- **Compliance Queries:** Operator can audit any task end-to-end

### 🚀 Zero-Downtime Deployment
Roll out new Skills without taking the system offline:

- **Staged Rollout:** 10% → 50% → 100% traffic over days
- **Instant Rollback:** Per-Skill rollback if canary fails
- **A/B Equivalence Testing:** Prove old == new before traffic shift

### 🏭 Plugin Marketplace
Community-contributed Plugins extend core functionality:

- 5 builtin plugins (Memory, Security, Data, Observability, Integration)
- 20+ community plugins available
- Plugin lifecycle management (register, execute, disable, unload)
- Full audit trail for plugin actions

---

## Quick Start

### Installation

```bash
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
python -m pip install -e .
```

### Run CorvinOS

```bash
corvin-console serve --port 8765
```

Visit http://localhost:8765 to access the web UI.

### Execute Your First Skill

```bash
corvin skill execute --skill-id os.delegation_router \
  --input '{"request": "summarize this article"}'
```

View execution in audit trail:

```bash
corvin audit show-task <task_id>
```

---

## Core Concepts

### Skill
A **Skill** is a Python program + metadata that:
- Declares a clear purpose (`skill_id`, `version`)
- Implements `execute(input: dict) -> dict`
- Declares dependencies (other Skills it calls)
- Receives feedback and optimizes configuration
- Logs every decision immutably

**Example:** `os.delegation_router` takes a user request, classifies its complexity, and routes to the appropriate LLM (Haiku, Sonnet, Opus).

### Agentic Control Plane (ACP)
Replaces static L-Layers with **Skills**. Each core subsystem (routing, context, security, data flow) is now a versioned Skill that:
- Receives traffic and makes decisions
- Gets user feedback
- Tunes parameters
- Converges to optimal behavior

### Learning Loop
Feedback → Optimization → Convergence:

```
Week 1: Skill routes 1000 requests; confidence = 60%
        User feedback: 15% wrong; optimizer lowers threshold
Week 2: Confidence = 72%; 12% wrong → lower threshold more
Week 3: Confidence = 85%; 8% wrong → fine-tune threshold
Week 4: Confidence = 92%; 5% wrong → converged
```

### Audit Trail
Immutable log of every Skill decision:

```json
{
  "tenant_id": "_default",
  "timestamp": "2026-09-02T14:30:45.123Z",
  "event_type": "skill_executed",
  "skill_id": "os.delegation_router",
  "input": {...},
  "output": {"route_to": "opus"},
  "latency_ms": 42,
  "lom": "core/skills/os_skills/router.py:L237",
  "hash": "sha256(...)",
  "prev_hash": "sha256(...)"  // Chain to previous event
}
```

---

## Skills System

The Skills System is the heart of CorvinOS. See [Skills System](docs/skills-system.md) for:

- **Skill Lifecycle:** registration → execution → feedback → optimization
- **Skill Metadata:** id, version, origin, boot_layer, dependencies
- **Skill Interface:** execute method, audit integration, composition
- **Examples:** routing, context, workflow, security, data flow

---

## ACP Vision

The **Agentic Control Plane** transforms CorvinOS from a hardcoded system into a **self-learning OS**. See [ACP Vision](docs/acp-vision.md) for:

- **From L-Layers to Skills:** why we replaced 36 hardcoded layers with versioned Skills
- **L-Layer Mapping:** how each layer becomes a Skill
- **Load-Bearing Boundaries:** 4 constraints that keep the system safe
- **Phase 1–3 Roadmap:** implementation timeline

---

## Learning Loop

Every Skill improves through feedback. See [Learning Loop](docs/learning-loop.md) for:

- **Feedback Types:** outcome (yes/no/maybe), preference (style), confidence (P), metric (latency)
- **Optimizer:** reads feedback, tunes configuration, measures convergence
- **Real Examples:** routing learns complexity threshold, vibe learns user style preferences
- **Convergence Tracking:** confidence score over time, non-convergence alerts

---

## Audit Trail

Every decision is logged immutably. See [Audit Trail](docs/audit-trail.md) for:

- **Why It Matters:** GDPR Art. 30, EU AI Act Article 50
- **Hash-Chain Proof:** immutable, tamper-evident
- **Tenant Isolation:** no cross-tenant leakage
- **LoM Binding:** proves which code made the decision
- **Operator Queries:** audit any task end-to-end

---

## Deployment

Rolling out Skills changes safely. See [Deployment Guide](docs/deployment-guide.md) for:

- **Staged Rollout:** 10% → 50% → 100% over 7 days
- **Canary Metrics:** latency, errors, confidence score
- **Instant Rollback:** per-Skill if canary fails
- **Zero-Downtime:** new code loads in parallel
- **A/B Equivalence Testing:** prove old == new before traffic shift

---

## Skills API

Comprehensive API reference. See [Skills API Reference](docs/skills-api-reference.md) for:

- **Registry API:** `register()`, `execute()`, `list_all()`, `get()`, `is_enabled()`
- **Skill Interface:** `execute(input)`, metadata fields
- **Composition:** `call_skill()`, dependency resolution, error handling
- **Learning API:** `get_confidence()`, `get_feedback_history()`, `get_config()`

---

## Composable Programs

Write complex behavior by composing Skills. See [Composable Programs](docs/composable-programs.md) for:

- **Skills as Imports:** Python-like composition model
- **Dependency Declaration:** explicit, verified
- **Error Handling:** what happens if one Skill fails?
- **Circular Dependency Detection:** prevent infinite loops
- **Examples:** context_adapter composing routing + vibe learning

---

## Big Bang Migration

We replaced Feature Flags with Skills. See [Big Bang Migration](docs/big-bang-migration.md) for:

- **Why Feature Flags Were Wrong:** no versioning, no learning, hard to test
- **Big Bang Decision:** all-at-once migration with safety gates (ADR-0544)
- **What Changed:** every feature flag → Skill
- **Compliance Gates:** audit trail verified before each stage
- **Rollout Timeline:** Weeks 11–13 with 3 safety gates

---

## Contributing

We welcome community contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for:

- **CLA:** Contributor License Agreement (v3.1)
- **Submitting a Plugin:** registration, testing, review
- **Skill Extension:** custom Skills for your domain
- **Code Style:** Python, testing, documentation standards

---

## Compliance

CorvinOS is built on compliance as a core feature:

- **GDPR (Art. 5, 6, 30, 32):** tenant isolation, audit trail, consent, right to erasure
- **EU AI Act (Art. 50):** AI transparency (disclosure card), opt-out (`/pass`)
- **Immutable Proof System:** every action logged, hash-chained, cryptographically verifiable

For details, see `docs/claude-ref/compliance-baseline.md`.

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.

CLA signatories tracked in [CLA-SIGNATORIES.md](CLA-SIGNATORIES.md).

---

## Getting Help

- **Questions?** Open a [GitHub Issue](https://github.com/CorvinLabs/CorvinOS/issues)
- **Contribute?** See [CONTRIBUTING.md](CONTRIBUTING.md)
- **Report a Bug?** Use the bug template in GitHub Issues
- **Security Issue?** Email security@corvinlabs.com

---

**CorvinOS:** Building an agentic operating system where every subsystem is versioned, auditable, and self-learning.

Made with ❤️ by the Corvin Team. Licensed under Apache 2.0.
