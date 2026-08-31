# Vibe Engineering ADR Status Report (2026-08-25)

## Übersicht

**Anforderung des Nutzers:**
- ADR-0275 ❌ **existiert nicht**
- ADR-0353 ❌ **existiert nicht**
- ADR-0354 ❌ **existiert nicht**
- ADR-0355 ❌ **existiert nicht**
- ADR-0370 ✅ **existiert** — analysiert

**Tatsächlich vorhandene Vibe Engineering ADRs:**
- ADR-0365: Real-Time Telemetry Dashboard
- ADR-0369: Phase 3.1 Status Reporting System
- ADR-0370: Adaptive Strategy Ladder
- ADR-0371: Adaptive Strategy Production Wiring
- ADR-0387: Feature Whitelist & Settings API Integration
- ADR-0391: Adaptive Context Routing & Dynamic Budgeting

---

## ADR-0365: Real-Time Telemetry Dashboard (corvin-labs.com/stats)

**Status:** ACCEPTED (2026-08-18)  
**Scope:** Live observability into cluster-wide metrics

### Anforderungen & Implementierungsstatus:

- [x] **Datensammlung (TokenCounter + TokenMetricsStore)** — ✅ VOLLSTÄNDIG
  - TokenCounter instrumentiert jeden Turn
  - TokenMetricsStore persistiert in SQLite + EventStore
  - Hash-chained in Audit Trail

- [x] **Aggregation (VibeMetricsAPI)** — ✅ VOLLSTÄNDIG
  - Cluster-level Summaries über InstanceRegistry
  - Tenant-Isolation auf Query-Layer
  - Per-Instance Breakdown vorhanden

- [x] **Visualisierung (Dashboard)** — 🟡 **TEILWEISE**
  - stats.html: HTML-Dashboard (vorhanden)
  - React VibeEngineeringDashboard: Teilweise funktional
  - Cloudflare Pages Deployment: **AUSSTEHEND** (WIP)

- [x] **Compliance (GDPR/EU AI Act)** — ✅ VOLLSTÄNDIG
  - Hash-chained audit trail (Art. 30)
  - Tenant-Isolation erzwungen
  - No PII in Dashboard (pseudonyme Instanz-IDs)

### Kritische Lücken (Amendment 2026-08-18):

**5 separate Defekte wurden gefixt:**

1. ✅ **Token Measurement Hook Initialization** — Ist nur in `corvin_console.standalone` aufgerufen worden, nicht in `corvin-service`
   - **Fix:** `get_token_hook()` auto-initializes on first use (beide Hosts covered)

2. ✅ **Endpoint Path Mismatch** — Konsole fetched `/v1/console/api/metrics/session/{id}`, existiert nirgends
   - **Fix:** Neuer Endpoint `GET /v1/console/vibe-engineering/token-metrics/{session_id}` (tenant-scoped)

3. ✅ **Hook Implementation Bugs** — `TokenMeasurementHook.end_turn()` hatte 4 separate Fehler
   - `counter.total_tokens()` falsch aufgerufen (int statt callable)
   - `subsystem_breakdown` → `subsystem_tokens` (falscher Feldname)
   - `store.insert_token_metrics()` → `write_token_metrics()` (API mismatch)
   - `emitter.emit()` erwartete LearningEvent, erhielt dict
   - **Fix:** Alle 4 Fehler korrigiert

4. ✅ **Async/Sync Mismatch** — `EventEmitter.emit()` await missing, DB schrieb nicht
   - **Fix:** `_dispatch()` Helper läuft Coroutine in sync+async Contexten

5. ✅ **Datenbank Integrität** — `instance_id` in Payload (sollte in Event), NULL-Violation
   - **Fix:** `event.instance_id` verwenden, IntegrityError-Handling verengt

### Messwerte (SLOs):
- ✅ Polling Latenz: <100ms
- ✅ Webhook Retry Success: >95%
- ✅ Monitor CPU: <1% pro 100 Tasks
- ✅ Memory: <10MB pro 1000 Snapshots

### Noch zu tun:
- ❌ Cloudflare Worker Proxy für `/api/metrics/stats` (WIP)
- ❌ Real Instance Data (warte auf InstanceRegistry-Verbindung)
- ❌ Redis Cache Optimization (v0.3)

---

## ADR-0369: Phase 3.1 Status Reporting System

**Status:** ACCEPTED (2026-08-24)  
**Scope:** Einheitliches Task-Status-Reporting über Discord, Console, CLI, Chat

### Anforderungen & Implementierungsstatus:

- [x] **StatusSnapshot (immutable dataclass)** — ✅ VOLLSTÄNDIG
  - Single source of truth für Task-Status
  - Format-Methoden: `to_discord_embed()`, `to_console_tile()`, `to_cli_summary()`, `to_chat_line()`
  - No side effects

- [x] **StatusPublisher (async fan-out hub)** — ✅ VOLLSTÄNDIG
  - O(1) Latest-Snapshot Lookup via `_latest_by_task` Index
  - Bounded History pro Task (max 100)
  - Exception Handling isoliert Bridge-Fehler

- [x] **BackgroundMonitor (polling + Discord webhooks)** — ✅ VOLLSTÄNDIG
  - Milestone Detection: Progress (5 iter), State Changes, User Input, Errors
  - Discord Webhook POST mit exponential backoff (1s, 2s, 4s; max 3 attempts)
  - Non-blocking Async

- [x] **TaskCLI Integration** — ✅ VOLLSTÄNDIG
  - `list_tasks()`, `resume()`, `status()`, `monitor()`, `auto_resume_last_unfinished()`
  - Checkpoint Persistence: `~/.corvin/vibe/checkpoints/`

- [x] **VibeEngine Integration** — ✅ VOLLSTÄNDIG
  - Status Publishing nach jedem Iteration
  - Error Recovery & Escalation Snapshots

### Test Coverage:
- ✅ 10 Tests: StatusSnapshot (serialize, format methods, lifecycle)
- ✅ 12 Tests: BackgroundMonitor (milestone detection, retry, cleanup)
- ✅ 14 Tests: TaskCLI (list, resume, status, monitor)
- ✅ 9 Tests: E2E Integration (polling loop, concurrent tasks)
- **Total: 45 Tests, alle green**

### Deployment:
- [x] Tier 1 (internal) — Development/Staging
- [x] Tier 2 (beta) — 10% Production
- [x] Tier 3 (GA) — 100% Rollout (no kill-switch needed)

### Offene Fragen (deferred):
- **Phase 3.2:** WebSocket Bridge für sub-second updates?
- **Phase 3.3:** Configurable Retention (heute: 100 pro Task)?
- **Phase 3.4:** Cross-Tenant Isolation (später: tenant_id Filter)?

---

## ADR-0370: Adaptive Strategy Ladder — Fingerprint-Gated Ranking

**Status:** PROPOSED (2026-08-19)  
**Scope:** Operator-aware Strategy Selection via OperatorFingerprint

### Anforderungen & Implementierungsstatus:

- [ ] **Confidence-Gated Ranking (Algorithm)** — 🟡 **DESIGN COMPLETE, IMPLEMENTATION PARTIAL**
  - Weighted Score: success_rate(50%) + operator_preference(30%) + cost_efficiency(20%)
  - Confidence Gate: ≥0.7 für Adaptive, sonst Empirical Fallback
  - **Status:** Code-Review gefunden: keine Production Call Site (ADR-0371 behebt das)

- [ ] **Operator Preference Score** — 🟡 **DESIGNED, NOT TESTED IN PRODUCTION**
  - Task-type Expertise (40% of preference) — designed
  - Speed Alignment (30%) — designed
  - Risk Alignment (30%) — designed
  - **Status:** Unit Tests vorhanden, E2E nicht verifikabel ohne Call Site

- [ ] **StrategyAdvisor Integration** — ❌ **UNREACHABLE FROM PRODUCTION**
  - `StrategyAdvisor.get_strategy()` Method existiert, aber:
  - **Keine Production Call Site** außer Tests
  - Code-Review (k=1 LDD): E2E Wiring Violation
  - **Fix:** ADR-0371 wiring in `LoopEngineer._apply_strategy()`

- [ ] **Feature Flag** — 🟡 **REGISTERED, NOT TESTED**
  - `FEATURE_ADAPTIVE_STRATEGIES` (default: true)
  - Confidence Threshold: 0.7 (hardcoded)
  - Fallback: Empirical-only ranking vorhanden

### Testing:
- ✅ 42 Unit Tests (strategy scoring, preference logic, confidence gate)
- ✅ 20+ Integration Tests (StrategyAdvisor + fingerprint)
- ❌ 5 E2E Scenarios: **BLOCKED** — no call site

### Kritische Lücken:
- ❌ **E2E Wiring:** Methode existiert, aber ist von Production nirgends aufrufbar
  - **Blockier:** ADR-0371 muss vor Deployment angewendet werden
- ❌ **Docstring-Code Inconsistency:** 3 Docstrings sagen `> 0.7`, Code nutzt `>= 0.7`
  - **Blockier:** ADR-0371 Amendment k=1 behebt das
- ⚠️ **In-Flight Fingerprint Updates:** Fingerprint ist static zur Selection-Zeit
  - **Deferred:** v0.3

### Rollout:
- **Week 1:** Code Review + Integration Testing
- **Week 2:** Canary (10% neue Tasks)
- **Week 3:** Loss Measurement
- **Week 4:** Full Rollout oder v0.3 Pivot

---

## ADR-0371: Adaptive Strategy Production Wiring & Docstring Fix

**Status:** PROPOSED → ACCEPTED (k=1-4 LDD iterations) (2026-08-19)  
**Scope:** Wiring von ADR-0370 in Production + Docstring-Fixes

### Anforderungen & Implementierungsstatus:

- [x] **E2E Wiring Proof** — ✅ **k=1 LDD: FIXED**
  - Call Site: `LoopEngineer._apply_strategy()` (error recovery path)
  - StrategyAdvisor injiziert via Hub während Startup
  - Fallback zu static ladder wenn StrategyAdvisor unavailable
  - **Status:** Production-erreichbar, getestet

- [x] **Docstring-Code Alignment** — ✅ **k=1 LDD: FIXED**
  - Alle 3 Docstrings: `> 0.7` → `>= 0.7`
  - Module, Class, Method Level
  - Matched now ADR-0370 Spec

- [x] **Constant Extraction** — ✅ **k=4 LDD: DONE**
  - `STRATEGY_BASE_COST_CENTS`, `STRATEGY_COST_INCREMENT_CENTS`
  - `STRATEGY_BASE_LATENCY_MS`, `STRATEGY_LATENCY_INCREMENT_MS`
  - `STRATEGY_DEFAULT_SUCCESS_RATE = 0.5`

- [x] **Empirical Data Wiring** — ✅ **k=3 LDD: FIXED**
  - Added `StrategyAdvisor.build_strategy_options()` mit REAL empirical rates
  - Hardcoded Formulas durch echte `strategy_scores` ersetzt
  - Silent Exception Handling in Fingerprint Retrieval behoben

### Three-Level Analysis:
1. **Conceptual:** Adaptive selection bei Errors ist load-bearing für Phase 2
2. **Structural:** StrategyAdvisor Dependency in LoopEngineer injiziert, Fallback erhalten
3. **Implementation:** Alle 3 Schritte (Klassifikation, Budget, Wiring) vorhanden

### Risiken & Mitigationen:
| Risk | Mitigation |
|---|---|
| StrategyAdvisor Injection Fehler | Logs auf INFO; Fallback; kein System-Fehler |
| Fingerprint unavailable mid-task | hasattr/getattr; None zu get_strategy() |
| Building StrategyOption Latenz | Frozen Dataclass (<1ms); negligible |
| Audit Trail Bloat | Kleine Records; append-only; kein Perf-Impact |

### Test Status (ADR-0371):
- ✅ Wiring Verification: get_strategy() has production call site
- ✅ Docstring Match: alle 3 Stellen `>= 0.7`
- ✅ Hub Injection: StrategyAdvisor erfolgreich in LoopEngineer startup
- ✅ _apply_strategy(): ruft get_strategy() auf, logged Mode
- ✅ Fallback Test: static ladder, wenn StrategyAdvisor unavailable
- ✅ Audit Trail: decision_type + value + reasoning recorded

---

## ADR-0387: Feature Whitelist & Settings API Integration Fix

**Status:** ACCEPTED (2026-08-18)  
**Scope:** Settings API & Feature Flag Resolution Alignment

### Anforderungen & Implementierungsstatus:

- [x] **Conceptual Alignment** — ✅ **FIXED**
  - Einheitliche Resolution Strategy: `feature_flags.is_enabled()`
  - Settings API + Feature Flags Module lesen jetzt gleich

- [x] **Settings API Fix** — ✅ **FIXED**
  - Ersetzt direkten Overlay-Read mit `is_enabled()` Call
  - Respektiert jetzt Whitelist Strategy

- [x] **Whitelist Audit** — ✅ **FIXED**
  - Entfernt Non-Existent Features: `tree_of_thoughts`, `learning_objectives`, `token_metrics`
  - Nur Features in REGISTRY sind now in Whitelist

- [x] **Tenant Config Sync** — ✅ **FIXED**
  - Beide `.corvin/tenants/_default/global/tenant.corvin.yaml` Kopien synced
  - `features.json` Overlay nur Whitelisted Features

- [x] **Amendment: Overlay Override** — ✅ **k=0 ACCEPTED**
  - Explicit operator decision in `features.json` beats whitelist (beide Richtungen)
  - Whitelist ist jetzt Fallback, nicht Ceiling
  - 36 von 41 Flags konnten nicht vom Console toggled werden — **BEHOBEN**

- [x] **Amendment: FastAPI Dependency Fix** — ✅ **k=0 ACCEPTED**
  - `verify_reauth` wurde als Depends() wired — machte `rec` zu second body field
  - Jetzt inline aufgerufen, Result tatsächlich checked

- [x] **Amendment: Route Order Fix** — ✅ **k=0 ACCEPTED**
  - `PUT /settings/worker-engine` war unreachable (nach Wildcard `PUT /settings/{label}`)
  - Moved above wildcard mit Comment zur Warnung

### Verification Tests:
- ✅ Whitelisted Features Enabled: vibe_engineering, outcome_feedback_loop, etc.
- ✅ Non-Whitelisted Disabled: browser_automation, acs_context_sync, admin_control_plane
- ✅ Settings API Returns Correct State: exactly 5 whitelisted features enabled

### Lücken:
- ⚠️ Shadow Implementation in `gateway/corvin_gateway/console_api.py` 
  - Ist dead demo code (imported by nothing)
  - Aber: Editing sieht aus wie fixing, changes nothing
  - **Nicht geändert, aber dokumentiert**

---

## ADR-0391: Adaptive Context Routing & Dynamic Budget Allocation

**Status:** PROPOSED (2026-08-19)  
**Scope:** Phase 3 Optimization — Task-aware Context Budget

### Anforderungen & Implementierungsstatus:

- [ ] **Task Complexity Classifier** — 🟡 **DESIGNED, NOT IMPLEMENTED**
  - Heuristic Classifier: SIMPLE | MODERATE | COMPLEX
  - Keyword Detection (rename, refactor, architect, etc.)
  - Confidence Scoring (keyword density)
  - **Status:** 99 LoC designed, Fallback zu MODERATE bei Parse-Error

- [ ] **Adaptive Budget Allocation** — 🟡 **DESIGNED, NOT IMPLEMENTED**
  - Complexity-aware Token Distribution
  - SIMPLE: Memory 60%, Synthesis 40% (skip Graph + Skills)
  - MODERATE: Memory 35%, Graph 15%, Skills 15%, Synthesis 35%
  - COMPLEX: Memory 30%, Graph 20%, Skills 20%, Synthesis 30%
  - **Status:** Design complete, Rebalancing Logic (±10% cap) defined

- [ ] **Performance Metrics Collection** — 🟡 **DESIGNED, NOT IMPLEMENTED**
  - PerformanceTracker mit rolling windows (default 10 metrics)
  - Utilization, Confidence, Quality, Latency pro Stage
  - Drift Detection (Δ ≥ 15% → rebalance signal)
  - **Status:** Design complete, Integration mit Pipeline pending

- [ ] **Feature Flag** — 🟡 **REGISTERED, NOT TESTED**
  - `adaptive_context_routing` (default: OFF, ship-dark)
  - Target Release: 0.13.x
  - Fallback zu Phase 2 uniform budgeting wenn OFF

### Testing (Planned):
- ⏳ 12 Unit Tests: TaskClassifier, AdaptiveBudget, PerformanceTracker
- ⏳ E2E Coverage: Real Task Classification + Rebalancing

### Expected Impact:
- **Target:** 40–50% zusätzliche Context Reduction (Phase 1+2+3 combined)
- **Latency:** Simple Tasks 200–300ms schneller
- **Token Efficiency:** 300–500 tokens/turn reduction
- **Quality:** ±5–10% Confidence Improvement

### Constraints:
- ⚠️ Heuristic Classification ist nicht exact (false negatives akzeptabel)
- ⚠️ Rebalancing Delay: 1–2 Turns Lag (window size default 10)
- ⚠️ Keine ML-Model Training; Keywords sind locked

### Deployment Plan:
1. **Phase 3.0 (v0.13-alpha):** Modules + Feature Flag
2. **Phase 3.1 (v0.13-beta):** 10% Canary Rollout
3. **Phase 3.2 (v0.13-stable):** 50% Rollout, Keyword Adjustments
4. **Phase 3.3 (v0.14):** Learned Classifiers (optional)

### Kritische Lücken:
- ❌ **Implementation:** Code nicht geschrieben
- ❌ **Integration:** Pipeline-Wiring ausstehend
- ❌ **Testing:** Alle Tests noch zu implementieren
- ❌ **Validation:** Production Measurement nicht durchgeführt

---

## ZUSAMMENFASSUNG — Noch zu implementieren:

### 🔴 BLOCKIERER (muss sofort behoben werden):

1. **ADR-0370 E2E Wiring**
   - Heute: `StrategyAdvisor.get_strategy()` existiert, aber ist nicht aufrufbar
   - Aktion: **ADR-0371 anwenden** (bereits designed + k=1-4 LDD accepted)
   - Timeline: Sofort (before any canary)

2. **ADR-0391 Implementation**
   - Heute: Nur Design, kein Code
   - Aktion: TaskClassifier + AdaptiveBudget + PerformanceTracker implementieren
   - Timeline: Phase 3.1 (v0.13-beta)

### 🟡 PARTIELLE LÜCKEN (können parallel behoben werden):

3. **ADR-0365 Cloudflare Pages Deployment**
   - Heute: WIP, Cloudflare Worker Proxy nicht setup
   - Aktion: Wrangler CLI Setup + Worker Proxy
   - Timeline: v0.2-rc1+ (parallel möglich)

4. **ADR-0369 Cross-Tenant Isolation**
   - Heute: Keine Tenant-Filter in StatusPublisher
   - Aktion: `_tenant_id` Filter hinzufügen (deferred für Phase 3.4)
   - Timeline: Phase 3.4 (kein Blocker heute)

5. **ADR-0391 Production Integration**
   - Heute: Design complete, Modules nicht wired in Context Pipeline
   - Aktion: Classification + Allocation in Build Context Pipeline integrieren
   - Timeline: v0.13-beta

### 📋 FOLLOW-UP AUFGABEN (nach Deployment):

- Week 1–4: Canary Rollout (10→50% users)
- Week 5: Measurement Phase
  - ADR-0370 Adaptive vs. Static Vergleich
  - ADR-0391 Context Reduction Metriken
  - ADR-0365 Telemetry Accuracy
- Week 6: Go/No-Go Decision (full rollout oder pivot)

### 🎯 PRIORISIERUNG:

**P0 (vor RC release):**
- Apply ADR-0371 wiring (unlock ADR-0370)

**P1 (v0.13 sprint):**
- Implement ADR-0391 modules
- Cloudflare Pages integration

**P2 (post-GA):**
- ADR-0369 Cross-tenant isolation
- ADR-0391 Learned classifiers
- ADR-0365 Redis Cache optimization

---

**Report Generated:** 2026-08-25  
**Last ADR Review:** ADR-0391 (2026-08-19)  
**Status:** Vibe Engineering v0.2-rc1 shipped + gates complete; v0.3 (Phase 3.1) in progress
