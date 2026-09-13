---
id: BUILD_PROCESS
type: reference
canonical: "../../../../Corvin-ADR/decisions/ADR-0692.md"
last_updated: 2026-09-13
phases_completed: [1-Week-1, 1-Week-2]
---

# BUILD_PROCESS — Wie wurde der Video Producer entwickelt?

*Die "Killer-Doku": Zeigt jeden Designentscheidung mit Thesis/Antithesis/Synthesis + Warum.*

## Überblick: Hero's Journey (7 Phasen)

Das Video Producer Plugin folgt einer strukturierten 7-Phasen-Pipeline (ADR-0692):

```
Phase 1: Asset Analysis        (Thesis: hardcoded gates)
Phase 2: Storyboard Gen        (Thesis: LLM-unconstrained)
Phase 3: Workers (Maestro)     (Thesis: Master-Worker)
Phase 4a: Assembly + Upload    (Thesis: Async batch)
Phase 4b: Learning Loops       (Thesis: Real feedback)
Phase 5: Console UI            (Thesis: Manual trigger)
Phase 6: Quality Dashboard     (Thesis: Metrics-only)
Phase 7: Marketplace Publish   (Thesis: Ready for scaling)
```

---

## Phase 1, Week 1: Foundation + Quality Gates (COMPLETE ✅)

**Problem:** "Wir brauchen ein Framework für hochwertige Videos — sicher, reproduzierbar, testbar."

### Thesis
- **Ansatz:** Hardcoded Quality Thresholds (DRAFT/PRODUCTION/BROADCAST tiers)
- **Vorteil:** Sicher, vorhersehbar, keine Ambiguität
- **Nachteil:** Rigid, keine Anpassung an neue Use-Cases

### Antithesis
- **Ansatz:** LLM-based Adaptive Scoring (jede Szene bewerten lassen)
- **Vorteil:** Flexibel, kontextabhängig
- **Nachteil:** Fragil, halluzinieren möglich, schwer zu debuggen

### Synthesis (GEWÄHLT)
- **Ansatz:** 5-Component Deterministic Scorer
  - Input validation (schema, ranges)
  - Hardcoded thresholds (DRAFT/PRODUCTION/BROADCAST) — compliance-critical
  - Named metrics (clarity_score, audio_quality, timing_alignment)
  - Per-scene feedback hooks (für Phase 4b learning)
  - Fail-closed gates (no bypass)

### Implementierung (Phase 1 Week 1)
- **ADR-0690:** Quality Gates (hardcoded Thresholds)
- **ADR-0691:** Task Audit Trail (Hash-Chained Events)
- **Komponenten:**
  - `quality_gates.py` (Schema-Validator + Thresholds)
  - `design_system.json` (v1.0.0, immutable assets)
  - `types.py` (Storyboard, Scene, QualityScore)
- **Tests:** 20+ unit tests (quality gates, edge cases)
- **Status:** ✅ 0 CRITICAL findings (adversarial review passed)

**Key Learning:** Quality gates MÜSSEN hardcoded sein. Compliance erfordert fail-closed Design. Kein Escape-Hatch, keine Feature-Flag zum Ausschalten.

---

## Phase 1, Week 2: Workers + Orchestration (CURRENT — IN PROGRESS)

**Problem:** "Wie orchestrieren wir 4 unabhängige Worker (Slides, Audio, Screenshots, Assembly) ohne sie fest zu koppeln?"

### Thesis
- **Ansatz:** Master-Worker Pattern (zentrale Kontrolle, Worker sind dumm)
- **Vorteil:** Einfach zu verstehen, zentrale Fehlerbehandlung
- **Nachteil:** Bottleneck, schwer zu testen einzeln, Worker können nicht ausfallen ohne alles zu brechen

### Antithesis
- **Ansatz:** Self-Managed Workers (Worker entscheiden, Maestro ist Koordinator)
- **Vorteil:** Dezentral, resilient, Worker können unabhängig versagen
- **Nachteil:** Schwer zu debuggen, wer ist verantwortlich?, Race-Conditions möglich

### Synthesis (GEWÄHLT)
- **Ansatz:** Maestro + Precondition Decorators
  - Maestro orchestriert die 7 Phasen (zentrale Kontrolle)
  - Jeder Worker hat `@require_precondition(...)` Decorator
  - Worker können lokal testen ohne Maestro
  - Feedback wirkt auf Maestro-Strategie, nicht auf Worker-Code

### Implementierung (Phase 1 Week 2 — IN PROGRESS)

**4 Core Workers:**

1. **SlideGenerator** (300 LOC)
   - Input: Scene (text, visuals, timing)
   - Output: PNG slides (high-res, branded)
   - Tech: Pillow + custom fonts (deterministic)
   - Precondition: scene.visuals != None
   - Tests: 12 (edge cases, fonts, DPI)

2. **AudioGenerator** (200 LOC)
   - Input: Scene (narration text, voice_params)
   - Output: WAV audio (matching timing)
   - Tech: Text-to-Speech API (deterministic seed)
   - Precondition: scene.narration_text != None
   - Tests: 8 (timing accuracy, silence handling)

3. **ScreenshotCapturer** (150 LOC — bereits vorhanden)
   - Input: Scene (webpage/UI spec)
   - Output: PNG screenshot
   - Tech: Playwright + headless browser
   - Precondition: scene.ui_spec != None
   - Tests: 6 (viewport sizes, async wait)

4. **VideoAssembler** (500 LOC)
   - Input: List[Scene], slides, audio, screenshots
   - Output: MP4 video (H.264, AAC)
   - Tech: FFmpeg subprocess
   - Precondition: all slides && audio && timing valid
   - Tests: 16 (codec validation, bitrate, duration)

**Orchestrator Main** (200 LOC)
- Koordiniert 7 Phasen
- Emits `VideoProducedEvent` (audit-chained)
- Learning hooks: `per_scene_feedback(scene, actual_quality)`

**Compliance (LOAD-BEARING):**
- ✅ Every Worker output logged (audit-chained)
- ✅ Preconditions are fail-closed (no bypass)
- ✅ Worker errors → Maestro decides retry/abort
- ✅ Feedback integrated into next run (ADR-0314 learning)

**Tests Structure (56+ neue = 96 total):**
```
tests/skills/
├── test_video_producer_phase1_workers.py       (12+12+8+16 = 48 new)
├── test_video_producer_orchestrator_phase2.py  (8 new)
└── test_video_producer_integration_phase2.py   (E2E, 0 CRITICAL)
```

### Dialektische Reasoning (Phase 1 Week 2)

**Thesis (Centralized Master-Worker):**
- Pro: Single orchestrator = clear flow, easy tracing
- Con: Maestro is SPOF (single point of failure)
- Con: Worker can't fail gracefully (all-or-nothing)

**Antithesis (Distributed Self-Managed):**
- Pro: Worker failure is isolated
- Pro: Horizontal scaling (add more workers)
- Con: Who coordinates state? Race conditions?
- Con: Debugging nightmare (distributed tracing required)

**Synthesis (Precondition Decorators):**
```python
@require_precondition("scene.visuals != None", worker="SlideGenerator")
async def generate_slides(self, scene: Scene) -> list[bytes]:
    # Worker is simple, no orchestration inside
    # Maestro checks precondition before calling
    # If fails, Maestro decides: skip/retry/abort
    pass
```

**Result:**
- Worker is testable in isolation (mock preconditions)
- Maestro orchestrates (but workers have agency)
- Feedback drives Maestro decisions (next run: skip slow worker?)
- Audit trail shows exactly where each phase failed

---

## Phase 2: Console API + Panel (COMPLETE ✅)

**Problem:** "Operators brauchen eine Möglichkeit, Videos zu triggern + Status zu sehen."

### Thesis
- **Ansatz:** Hardcoded REST API + Browser UI
- Vorteil: Einfach, keine Abstraktion
- Nachteil: Schwer zu erweitern

### Synthesis (GEWÄHLT)
- **Ansatz:** Skill-driven Console (Plugin-based routing)
- Vorteil: Works with Marketplace discovery
- Vorteil: Feedback loop → learning (ADR-0314)

### Implementierung
- **ADR-0696/0697:** Console UI + Model Selection
- 4 REST endpoints (create, status, feedback, list)
- Console panel (React, Vibe integration)
- **Tests:** 12 API tests + 8 UI tests

---

## Phase 3 + 4a: Orchestrated Workers + YouTube (COMPLETE ✅)

**Problem:** "Wie produzieren wir Videos ohne manuelles Stitching? Wie laden wir auf YouTube?"

### Synthesis
- **Ansatz:** Parallel Worker Orchestration (Maestro supervises)
- Slides + Audio in parallel (faster)
- FFmpeg assembly (deterministic)
- YouTube async upload (non-blocking)

### Implementierung
- **ADR-0698:** Phase 3 + 4a Orchestration
- `video_assembler.py` (FFmpeg wrapper)
- `youtube_uploader.py` (async Task API)
- **Tests:** 20+ adversarial (timeout, network, invalid video)

---

## Hidden Assumptions (Pro Future Development)

### 1. Quality Gates are Immutable
**Assumption:** DRAFT/PRODUCTION/BROADCAST tiers don't change between runs.
**Why:** Compliance requires consistency. Can't have "90% quality sometimes passes, sometimes doesn't."
**Implication:** Phase 4b learning adjusts weights, NOT gates.

### 2. Workers are Deterministic
**Assumption:** Same input → Same output (bit-for-bit)
**Why:** Audit trail requires reproducibility.
**Implication:** No random number generators in worker code. Seeds must be explicit.

### 3. Feedback is Mandatory (Phase 4b)
**Assumption:** Operator always provides feedback on final video.
**Why:** Learning loop requires labeled data.
**Implication:** Phase 4b blocks upload until feedback collected (or timeout default).

### 4. Audit Trail is Truth
**Assumption:** `audit.jsonl` hash-chain is authoritative.
**Why:** GDPR Art. 30 requires provenance.
**Implication:** If audit is corrupted, video is tainted (rejected from upload).

### 5. Design System is Locked
**Assumption:** `design_system.json` v1.0.0 is immutable for a given project.
**Why:** Reproducibility + compliance.
**Implication:** New designs require new project (or v2.0.0 migration ADR).

---

## Lessons for Future Phases

### Phase 4b: Learning Loops
- Quality feedback (1-5 scale) feeds back to confidence scorer
- Model selection (GPT-4 vs Claude vs Opus) learned based on cost + quality
- Worker parameters tuned (FFmpeg bitrate, font size, etc.)

### Phase 5: Console Dashboard
- Show live orchestration (which phase, which worker)
- Show quality metrics (clarity, audio, timing)
- Show cost breakdown (model spend, compute, storage)

### Phase 6: Marketplace Publish
- Plugin becomes discoverable (index v3, ADR-0678)
- Operators can install + tweak design_system.json
- Community plugins extend (new workers, new design systems)

### Phase 7: Scaling
- Multi-tenant support (each tenant has own design_system.json + audit chain)
- Webhook triggers (from other apps)
- Scheduled runs (cron-based video regeneration)

---

## Compliance Checklist (LOAD-BEARING)

- [ ] ✅ Phase 1 Week 1: Quality Gates (hardcoded, fail-closed)
- [ ] ✅ Phase 1 Week 2: Workers (deterministic, preconditions, audit-chained)
- [ ] ✅ Phase 2: Console API (REST, learning-integrated, GDPR-compliant)
- [ ] ✅ Phase 3 + 4a: Orchestration (parallel, async, error-recovery)
- [ ] 🔲 Phase 4b: Learning Loop (feedback collection, confidence tuning)
- [ ] 🔲 Phase 5: Dashboard (observability, cost transparency)
- [ ] 🔲 Phase 6: Marketplace (publishable, extensible)
- [ ] 🔲 Phase 7: Scaling (multi-tenant, webhooks, scheduling)

---

## Knowledge Graph Links

- **ADR-0692:** Video Producer Orchestration (main architecture)
- **ADR-0693:** Asset Analyzer Worker
- **ADR-0694:** Voice + Screenshot Workers
- **ADR-0695:** Assembler + YouTube Workers
- **ADR-0690:** Quality Gates (compliance-critical)
- **ADR-0691:** Audit Trail Hash-Chaining
- **ADR-0698:** Phase 3 + 4a Implementation
- **CONCEPT-0040:** Orchestrated Multi-Skill Pattern (Thesis/Antithesis/Synthesis)
- **ADR-0314:** Learning Infrastructure (feedback integration)

---

**Last Updated:** 2026-09-13  
**Phases Complete:** 1 Week 1, 1 Week 2 (IN PROGRESS), 2, 3, 4a  
**Next:** Phase 4b (Learning Loops, Week 3–4)
