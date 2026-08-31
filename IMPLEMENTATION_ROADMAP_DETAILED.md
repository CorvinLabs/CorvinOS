# 🔧 VIBE ENGINEERING & CORE IMPLEMENTATION ROADMAP
*Vollständiger Plan für alle 11 offenen/teilweise implementierten ADRs*

**Generiert:** 2026-08-25  
**Status:** v0.2-rc1 shipped + critical blockers identified  
**Next milestone:** Week 1 (P0 blocker fix)

---

## EXECUTIVE SUMMARY

### Implementierungsstatus
- ✅ **Complete:** 3 ADRs (0369, 0371, 0387)
- 🟡 **Partial:** 4 ADRs (0237, 0311, 0365, 0391)
- ❌ **Not Implemented:** 4 ADRs (0010, 0235, 0236)
- 🔴 **Blocked:** 1 ADR (0370 — E2E wiring violation)

### Gesamtaufwand
- **P0 (vor Canary):** 1-2 Tage (blocker only)
- **P1 (v0.13 sprint):** 2-3 Wochen (features)
- **P2 (documentation):** 3-5 Tage (debt)
- **P3 (optional):** 1-2 Wochen (future phases)

**Total:** ~4-5 Wochen für vollständige Implementierung

### Kritischer Pfad
```
ADR-0370 (blocked)
    ↓ (blocked by)
ADR-0371 (wiring fix) — 1-2h, MUST DO FIRST
    ↓ (unblocks)
ADR-0370 (now reachable) — then canary can proceed
    ↓ (parallel work)
ADR-0391 (implementation) — 1-2 weeks
ADR-0365 (Cloudflare) — 4-6 hours
```

---

## PHASE 1: KRITISCHE GRUNDLAGEN (Woche 1)
### 🔴 MUSS ERLEDIGT SEIN VOR CANARY ROLLOUT

### ADR-0371: Adaptive Strategy Production Wiring
**Status:** ✅ Complete (aber möglicherweise nicht angewendet)  
**Aufwand:** 1-2 Stunden

#### Was ist der Status?
- ✅ ADR-Dokument vollständig
- ✅ Code implementiert (k=1-4 LDD iterations)
- ✅ Tests grün (45+ tests)
- ⚠️ **Problem:** Ist diese Änderung bereits im `main` branch?

#### Was genau muss getan werden?
1. **Verifizieren:** Git checkout `main`, prüfen ob LoopEngineer._apply_strategy() bereits:
   ```python
   strategy = self.strategy_advisor.get_strategy(
       operator_fingerprint=self.fingerprint,
       current_error=error
   )
   ```
   
2. **Falls nicht:** Apply the three fixes described in ADR-0371:
   - E2E Wiring: Call to `StrategyAdvisor.get_strategy()` in error recovery
   - Docstring Fixes: `> 0.7` → `>= 0.7` (alle 3 Stellen)
   - Constants: Extract `STRATEGY_BASE_COST_CENTS`, etc.

3. **Verifizieren:** 
   ```bash
   grep -n "get_strategy()" core/orchestration/loop_engineer.py
   grep -n ">= 0.7" core/learning/strategy_advisor.py
   ```

4. **Tests laufen lassen:**
   ```bash
   pytest tests/test_adaptive_strategy_wiring.py -v
   pytest tests/test_adaptive_strategy_integration.py -v
   ```

#### Abhängigkeiten
- Blockiert ADR-0370 derzeit (macht es reachable)
- Keine Abhängigkeiten auf andere ADRs

#### Success Criteria
- ✅ `StrategyAdvisor.get_strategy()` hat Production call site
- ✅ Code/Docstring aligned (>= 0.7)
- ✅ Hub injection wired in bootstrap
- ✅ Fallback zu static ladder funktioniert

#### Risiken
- **Risk:** Falsche Fingerprint Handling → Fallback zu static
  - **Mitigation:** Logs auf INFO level, kein System-Fehler
- **Risk:** Audit Trail Bloat
  - **Mitigation:** Kleine Records, append-only, negligible perf impact

---

### ADR-0370: Adaptive Strategy Ladder
**Status:** 🔴 Blocked (unreachable from production)  
**Aufwand:** 0h (nach ADR-0371 fix)

#### Was ist der Status?
- ✅ Code vollständig (`core/learning/adaptive_strategy.py`, `strategy_advisor.py`)
- ✅ Tests: 42 unit + 20 integration + 5 E2E = 67 tests
- ❌ **Problem:** `StrategyAdvisor.get_strategy()` wird NIRGENDS von Production aufgerufen
- ⚠️ Docstring-Code Inconsistency: 3 Docstrings sagen `> 0.7`, Code nutzt `>= 0.7`

#### Was genau muss getan werden?
Nach ADR-0371 wiring ist diese ADR automatisch reachable.

#### Success Criteria
- ✅ Hat Production call site (via ADR-0371)
- ✅ Feature flag funktioniert
- ✅ Fallback zu Empirical ranking bei `confidence < 0.7`
- ✅ In-flight fingerprint updates deferred zu v0.3 ✅

---

## PHASE 2: ABHÄNGIGKEITEN & FEATURES (Woche 2-3)

### ADR-0391: Adaptive Context Routing & Dynamic Budgeting
**Status:** ❌ Not Implemented (Design complete)  
**Aufwand:** 8-10 Tage (full implementation)  
**Priorität:** P1 (v0.13 sprint)

#### Was ist der Status?
- ✅ ADR-Dokument vollständig
- 🟡 Design-Phase: 99 LoC TaskClassifier designed, aber **NICHT implementiert**
- 🟡 Design-Phase: Budget Allocation formulas defined, aber **NICHT implementiert**
- 🟡 Design-Phase: PerformanceTracker designed, aber **NICHT implementiert**
- ❌ Keine Tests
- ❌ Nicht wired in Context Pipeline
- ✅ Feature flag registriert: `adaptive_context_routing` (default: OFF)

#### Was genau muss getan werden?

**Subtask 1: TaskClassifier implementieren (2 Tage)**
- **Was:** Heuristic-based classifier: SIMPLE | MODERATE | COMPLEX
- **Code-Path:** `core/context_engineering/task_classifier.py`
- **Klassifizierung:**
  - SIMPLE: "list", "show", "what is", "count" keywords
  - MODERATE: "rename", "refactor", "move", "create", "update"
  - COMPLEX: "architect", "design", "audit", "multi-step", "research"
- **Implementation:**
  ```python
  class TaskClassifier:
      def classify(task_prompt: str) -> TaskComplexity:
          # keyword density analysis
          # confidence scoring
          # fallback to MODERATE on parse error
  ```
- **Tests:** 6 unit tests (keyword detection, confidence, fallback)

**Subtask 2: AdaptiveBudgetAllocator implementieren (2 Tage)**
- **Was:** Token-Budget Reallokation basierend auf Task Complexity
- **Code-Path:** `core/context_engineering/adaptive_budget.py`
- **Allocation schema:**
  ```
  SIMPLE (5-10% of tasks):
    Memory: 60%, Synthesis: 40%
    (skip Graph + Skills: save tokens)
  
  MODERATE (75-80% of tasks):
    Memory: 35%, Graph: 15%, Skills: 15%, Synthesis: 35%
    (balanced)
  
  COMPLEX (10-15% of tasks):
    Memory: 30%, Graph: 20%, Skills: 20%, Synthesis: 30%
    (more graph reasoning, more skill context)
  ```
- **Rebalancing:** ±10% cap per iteration
- **Tests:** 4 unit tests (allocation math, rebalancing, edge cases)

**Subtask 3: PerformanceTracker implementieren (2 Tage)**
- **Was:** Rolling-window metrics collection + drift detection
- **Code-Path:** `core/context_engineering/performance_tracker.py`
- **Tracking:**
  ```python
  class PerformanceMetric:
      stage: str  # "memory", "graph", "skills", "synthesis"
      utilization: float  # 0-1
      confidence: float   # 0-1
      quality: float      # 0-1
      latency_ms: int
      timestamp: datetime
  ```
- **Drift Detection:** Δ ≥ 15% triggers rebalance signal
- **Window:** Default 10 metrics (rolling)
- **Tests:** 3 unit tests (tracking, drift detection, window mgmt)

**Subtask 4: Pipeline Integration (2 Tage)**
- **Was:** Wire classifier + budget + tracker in Build Context flow
- **Code-Path:** `core/context_engineering/context_builder.py` (update)
- **Flow:**
  ```
  build_context(task_prompt)
    → classify(task_prompt)  # → SIMPLE/MODERATE/COMPLEX
    → get_performance_metrics()
    → allocate_budget(complexity)
    → build_memory_stage()
    → build_graph_stage()
    → build_skills_stage()
    → build_synthesis_stage()
  ```
- **Fallback:** Wenn flag OFF → uniform budgeting (Phase 2 behavior)
- **Tests:** 3 E2E tests (full flow, fallback, metrics collection)

#### Expected Impact
- 40–50% zusätzliche Context Reduction (Phase 1+2+3 combined)
- Simple Tasks 200–300ms schneller
- 300–500 tokens/turn reduction

#### Dependencies
- Depends on: ADR-0388 (per-stage token budgeting — assumed complete)
- Blocks: v0.14 (learned classifiers)

#### Success Criteria
- ✅ TaskClassifier works on 100 test prompts (>90% accuracy on complexity bins)
- ✅ Budget allocation preserves total token count (±5%)
- ✅ PerformanceTracker detects 15% drift in latency
- ✅ Fallback to uniform budget works when flag OFF
- ✅ 12 unit tests + 3 E2E tests passing
- ✅ Feature flag toggleable from Settings UI

#### Risiken
- **Risk:** Heuristic Classification falsches Bild (false negatives acceptable)
  - **Mitigation:** Fallback zu MODERATE, kein System-Fehler
- **Risk:** Rebalancing Delay 1-2 turns
  - **Mitigation:** Akzeptabel — Fenster-basiert, nicht real-time
- **Risk:** Keyword List wird schnell veraltet
  - **Mitigation:** Kein ML-Training heute — Keywords sind locked, v0.14 für Learned Classifiers

---

### ADR-0365: Real-Time Telemetry Dashboard
**Status:** 🟡 Partial (Cloudflare deployment pending)  
**Aufwand:** 4-6 Stunden  
**Priorität:** P1 (parallel mit ADR-0391)

#### Was ist der Status?
- ✅ Token measurement hook implementiert + wired
- ✅ TokenMetricsStore (SQLite + EventStore)
- ✅ VibeMetricsAPI (cluster aggregation)
- ✅ Dashboard code (stats.html + React component)
- ❌ Cloudflare Pages deployment: **WIP**
- ❌ Real Instance Data: **awaiting InstanceRegistry connection**

#### Was genau muss getan werden?

**Subtask 1: Cloudflare Worker Proxy (2h)**
- **Was:** Proxy `/api/metrics/stats` zu Cloudflare Pages
- **Code-Path:** `corvin-telemetry/worker.js` (new)
- **Setup:**
  ```bash
  npm install -g wrangler
  cd corvin-telemetry/
  wrangler init
  wrangler publish
  ```
- **Worker:**
  ```javascript
  export default {
    fetch: (request) => {
      // proxy to corvin-labs.com/stats
      // add CORS headers
      // cache for 30s
    }
  }
  ```
- **Tests:** Curl test gegen deployed URL

**Subtask 2: InstanceRegistry Integration (2h)**
- **Was:** Verbinde TokenMetricsStore mit InstanceRegistry
- **Code-Path:** `core/learning/token_metrics_aggregator.py` (update)
- **Connection:**
  - Bei `get_cluster_metrics()`: Alle Instanzen vom Registry lesen
  - Per-Instance Breakdown berechnen
  - Propagieren zu stats.html
- **Tests:** Prüfe `curl http://localhost:8765/v1/metrics/cluster`

**Subtask 3: Documentation + Verification (2h)**
- **Was:** Dashboard at `corvin-labs.com/stats` functional
- **Verification:**
  ```bash
  # 1. Local dev
  npm run dev -w web-next
  open http://localhost:5173/stats
  
  # 2. Staging
  npm run build -w web-next
  curl -s https://corvin-labs.staging/stats | grep -o "assets/.*\.js"
  
  # 3. Production (post-deploy)
  curl -s https://corvin-labs.com/stats | grep -o "assets/.*\.js"
  ```

#### Success Criteria
- ✅ Dashboard erreichbar unter `corvin-labs.com/stats`
- ✅ Real cluster metrics rendered (nicht placeholder)
- ✅ Per-Instance breakdown sichtbar
- ✅ Metrics refresh alle 30s
- ✅ Compliance: Keine PII sichtbar (nur pseudonyme instance_ids)

#### Dependencies
- Depends on: InstanceRegistry (waiting for connection)
- No blocking deps on other ADRs

---

## PHASE 3: FEHLENDE DOKUMENTATION (Woche 3-4)

### ADR-0237: Extensible Core Plugins & Replacement Pattern
**Status:** 🟡 Code Complete, ADR Document Missing  
**Aufwand:** 2-3 Stunden  
**Priorität:** P2 (documentation)

#### Was ist der Status?
- ✅ Code vollständig implementiert
  - Extension-point bus: `core/plugins/corvin_plugins/extension_points.py`
  - Plugin registry + replacement: `core/plugins/corvin_plugins/registry.py`
  - Hook system: Fully functional
- ❌ **Problem:** ADR-Dokument existiert NICHT, aber Code referenziert "ADR-0237"
- ✅ Tests: 20+ integration tests vorhanden

#### Was genau muss getan werden?
1. **Formal ADR-Dokument schreiben** in `/home/shumway/projects/Corvin-ADR/decisions/0237-extensible-core-plugins.md`
2. **Folgende Sections dokumentieren:**
   - Decision: Hook vs. Replacement approaches
   - Eight existing provider registries (mit je einer Zeile Beschreibung)
   - Proposed new extension points (backlog)
   - Immutable vs. Extensible boundaries (critical)
   - Failure direction per extension point
   - Testing requirements

3. **ADR-0264 frontmatter hinzufügen:**
   ```yaml
   id: ADR-0237
   status: accepted
   depends_on: [ADR-0236, ADR-0243]
   paths:
     - core/plugins/corvin_plugins/extension_points.py
     - core/plugins/corvin_plugins/registry.py
     - core/plugins/corvin_plugins/manifest.py
   docs:
     - docs/claude-ref/layer-plugins.md
   ```

#### Success Criteria
- ✅ ADR-Dokument vollständig (≥1500 words)
- ✅ Frontmatter mit korrekten paths/docs
- ✅ Immutable vs. Extensible Tabelle clear
- ✅ Eight registries documented
- ✅ Proposed extension points as backlog listed

---

### ADR-0010, ADR-0235, ADR-0236: Missing Documentation
**Status:** ❌ Nicht implementiert  
**Aufwand:** 1-2 Wochen (design + impl)  
**Priorität:** P2-P3 (deferred)

#### ADR-0010: Operator Observability Surface
**Was:** Audit Sinks + Tenant Branding  
**Gaps:** Zero implementation, no tests, no feature flag

**Roadmap:**
1. **Phase 1 (1 week):** Audit Sinks
   - Sink registry + jsonl_tail sink (100 LOC)
   - Syslog + Webhook sinks (150-200 LOC each)
   - Dead-letter + replay CLI (100 LOC)
   - Tests: 15 unit + 5 E2E

2. **Phase 2 (4 days):** Tenant Branding
   - branding.yaml loader (150 LOC)
   - Layer 19 disclosure integration (80 LOC)
   - TTS + bridge reply integration (100 LOC)
   - Tests: 10 unit + 3 E2E

3. **Phase 3 (2 days):** GCS/S3 sinks (deferred)

#### ADR-0235: Plugin Classification System
**Was:** `support_class` Taxonomy (product/infrastructure/community)  
**Gaps:** Zero implementation, no tests

**Roadmap:**
1. **Phase 1 (2 days):** Data model
   - Define `support_class` enum
   - Update PluginManifest
   - Classify all existing plugins

2. **Phase 2 (2 days):** Registry + UI
   - Registry filtering by support_class
   - Console UI "Support Info" tab
   - Tests: 8 unit + 2 E2E

3. **Phase 3 (2 days):** Maintenance plan + SLA docs

#### ADR-0236: Minimal Core Specification
**Was:** Target state of core (2,400 LOC)  
**Gaps:** Zero implementation, purely architectural

**Roadmap:**
1. **Design (3 days):** Finalize extraction plan
   - What moves from `operator/bridges/shared` → `core/compliance`
   - Module dependencies
   - Migration gate design

2. **Implementation (2-3 weeks):** Actual extraction
   - Phase 1: Audit writer (no-downtime extract)
   - Phase 2: Consent gate
   - Phase 3: House-rules gate
   - Phase 4: Erasure orchestrator

3. **Validation (1 week):** Hash chain integrity verification

---

## PHASE 4: OPTIONAL IMPROVEMENTS (Woche 5+)

### ADR-0311: Rate Limiter — Fix Path Mismatch
**Status:** 🟡 Partial (implementation exists, but tests missing)  
**Aufwand:** 1-2 Tage  
**Priorität:** P3 (optional, ship-dark friendly)

#### Was ist der Status?
- ✅ Code implementiert: `core/context_engineering/rate_limiter.py`
- ❌ **Problem 1:** Pfad-Mismatch (ADR sagt `core/skills/rate_limiter.py`, liegt aber in `context_engineering`)
- ❌ **Problem 2:** Tests nicht gefunden (ADR sagt `tests/unit/test_rate_limiter.py`, existiert aber nicht)
- ❌ **Problem 3:** Integration in main flow nicht verifiziert

#### Was genau muss getan werden?

**Option A: Pfad korrigieren (ideal)**
1. Umbenennen: `core/context_engineering/rate_limiter.py` → `core/skills/rate_limiter.py`
2. Update imports überall
3. Verifiziere Pfade in git log

**Option B: ADR korrigieren (schneller)**
1. ADR-Amendment: `paths:` Feld korrigieren
2. ADR-Amendment: Tests Status klarstellen

**Recommended: Option A** (macht Code+ADR konsistent)

#### Tests schreiben (1 day)
- `test_rate_limiter.py` (12 tests): Token bucket, limits, headers
- Token bucket behavior (fill rate, consumption)
- Per-skill limits (10/min default)
- Per-user limits (single user isolation)
- Global limits (system-wide cap)
- Retry-after header generation
- Tenant isolation

#### Success Criteria
- ✅ Code + ADR pfad konsistent
- ✅ 12 unit tests mit >90% coverage
- ✅ Funktion ist aufrufbar von Production (oder deferred zu v0.3)
- ✅ Graceful degradation (429 response)

---

## 📊 DETAILLIERTER TIMELINE

```
WOCHE 1 (8h)
├─ ADR-0371: Verify + Apply wiring (1-2h) — CRITICAL PATH
├─ ADR-0370: Verify now reachable (0.5h) — auto-fixed by 0371
├─ ADR-0365: Cloudflare setup (4-6h) — parallel work
└─ Commit + Merge to main (0.5h)

WOCHE 2-3 (40-50h)
├─ ADR-0391: Implementation (32-40h)
│  ├─ TaskClassifier (8h)
│  ├─ AdaptiveBudgetAllocator (8h)
│  ├─ PerformanceTracker (6h)
│  └─ Pipeline integration (8h)
└─ ADR-0365: InstanceRegistry integration (4-6h) — parallel

WOCHE 4 (16h)
├─ ADR-0237: Write ADR document (2-3h)
├─ ADR-0311: Fix + write tests (8h)
└─ Code review + testing (4-5h)

WOCHE 5+ (deferred)
├─ ADR-0010: Audit Sinks + Branding (1-2 weeks)
├─ ADR-0235: Support Classes (1 week)
├─ ADR-0236: Core extraction (3-4 weeks)
└─ Continuous measurement + feedback

PARALLEL STREAMS
└─ Canary Rollout (Week 1-4, 10% → 50% → 100%)
└─ Production Measurement (Week 2-5)
└─ Learning Loop (operator feedback)
```

---

## 🎯 PRIORISIERUNG & GO/NO-GO GATES

### Week 1: RC Release Gate
**Status:** Ready if:
- ✅ ADR-0371 wiring verified + applied
- ✅ ADR-0370 reachable from production
- ✅ No E2E wiring violations
- ✅ 45+ tests passing for Phase 3.1
- ✅ Compliance audit OK (tripwire, hash-chain)

**Decision:** Go/No-Go für Canary (10% users)

### Week 5: Measurement Gate
**Status:** Ready to expand if:
- ✅ ADR-0370 Adaptive vs. Static Vergleich zeigt Gewinn (>5%)
- ✅ ADR-0391 Context Reduction messbar (>100 tokens/turn)
- ✅ ADR-0365 Telemetry Accuracy >90%
- ✅ No new critical issues in canary

**Decision:** Go/No-Go für 50% rollout oder pivot v0.3

### Week 6: GA Gate
**Status:** Ready to full if:
- ✅ 50% cohort stable für 3+ days
- ✅ No regressions in upstream metrics
- ✅ Operator feedback positive

**Decision:** 100% rollout oder rollback + v0.3 redesign

---

## 🚨 DEPENDENCY MATRIX

```
0371 (wiring)
  ↓
0370 (adaptive strategy) — unblocked after 0371
  ↓
0369 (status reporting) ✅ complete, independent
  ↓
0391 (context routing) — independent, parallel
  ↓
0365 (telemetry) — independent, parallel
  ↓
0387 (feature flags) ✅ complete, independent
  ↓
0237 (ext plugins) ✅ code complete, needs doc
  ↓
0311 (rate limiter) 🟡 partial, tests needed
  ↓
0010/0235/0236 — deferred to Phase 2
```

---

## 📋 KONKRETE NÄCHSTE SCHRITTE

### Today (2026-08-25)
1. **Verify ADR-0371 application status:**
   ```bash
   git log --oneline main | grep -i "strategy.*wiring\|0371"
   grep -n "get_strategy()" core/orchestration/loop_engineer.py
   ```

2. **Falls ADR-0371 NICHT angewendet:**
   ```bash
   git checkout -b fix/adr-0371-wiring
   # Apply three changes from ADR-0371
   # Write commit message with "ADR-0371" + "Co-Authored-By"
   # Submit PR
   ```

3. **Falls ADR-0371 BEREITS angewendet:**
   ```bash
   pytest tests/test_adaptive_strategy_wiring.py -v
   pytest tests/test_adaptive_strategy_integration.py -v
   # Verify all passing
   ```

### This week (Aug 25-29)
1. Merge ADR-0371 wiring (if not done)
2. Start ADR-0391 implementation (TaskClassifier)
3. Setup Cloudflare Pages (ADR-0365)
4. Code review + testing

### Next week (Sept 1-5)
1. Complete ADR-0391 implementation
2. Finalize ADR-0365 metrics integration
3. Write ADR-0237 document
4. Run full test suite + canary preparation

---

## ✅ SUCCESS CRITERIA (OVERALL)

**v0.2-rc1 Ready (before canary):**
- ✅ ADR-0371 wiring applied (makes 0370 reachable)
- ✅ ADR-0369 complete (Phase 3.1 status reporting)
- ✅ ADR-0387 complete (feature whitelist)
- ✅ All unit tests passing (200+)
- ✅ E2E tests passing (74+)
- ✅ No compliance violations (audit chain verified)
- ✅ Release notes + ADR closure

**v0.2-rc1 Canary (Week 1-4, 10% users):**
- ✅ ADR-0370 metrics: Adaptive vs. static showing gains
- ✅ ADR-0391 design finalized (TaskClassifier ready for Week 2)
- ✅ ADR-0365 telemetry flowing (InstanceRegistry connected)
- ✅ No production incidents (pagerduty empty)

**v0.2-GA (Week 6, 100% rollout):**
- ✅ ADR-0391 implementation complete + tested
- ✅ ADR-0365 Cloudflare deployment live
- ✅ ADR-0237 document written
- ✅ ADR-0311 tests written + verified
- ✅ Operator feedback loop closed (satisfaction >80%)

---

## 📞 DECISION GATES & OWNER

| Gate | Owner | Date | Criterion |
|---|---|---|---|
| RC Release | shumway + AI | 2026-08-29 | ADR-0371 applied, tests green |
| Canary Go | shumway | 2026-09-05 | 10% cohort stable |
| Expand to 50% | shumway | 2026-09-12 | Metrics positive |
| GA (100%) | shumway | 2026-09-19 | No regressions, feedback good |
| v0.3 Planning | shumway | 2026-09-26 | Post-GA retrospective |

---

**Generated:** 2026-08-25  
**Status:** Awaiting ADR-0371 verification (P0 blocker)  
**Next Review:** 2026-08-29 (RC gate)

