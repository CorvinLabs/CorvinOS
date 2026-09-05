# Skill Engine-Agnostic Architecture — Implementation Plan

**Status:** Phase A (k=3 in LDD loop)  
**Estimated:** 6 weeks, 1200 LoC, 40+ tests  
**Phases:** A (RPC API), B (Hermes), C (Copilot/OpenCode)  
**Dependencies:** ADR-0598, ADR-0599, ADR-0600, ADR-0601, ADR-0602  

---

## Phase A: Engine-Agnostic Skill-Invocation RPC API (Weeks 1–2)

### Goal
Define + implement canonical RPC contract for all engines to invoke Skills. Route Claude Code through it (behavior-identical). Unified audit.

### Deliverables

| File | LoC | Purpose | Dependencies |
|------|-----|---------|--------------|
| `core/engine/skill_invocation_models.py` | 150 | Request/Response dataclasses, validation | None |
| `core/engine/skill_invocation_service.py` | 400 | Core RPC logic (Phases 0–10) | models, manifest loader, audit backend |
| `core/skills/skill_invocation_router.py` | 100 | Routes Skill invocation (by engine) | service |
| `core/skills/audit_integration.py` | 80 | Audit event emission | audit_backend |
| **Tests** | 350 | 25+ unit + E2E tests | all above |
| **Docs** | 200 | API reference, examples | none |
| **Total** | **1280** | | |

### New Files (Detailed)

#### 1. `core/engine/skill_invocation_models.py` (~150 LoC)

```python
# Request/Response contracts
class SkillInvocationRequest:
    tenant_id: str
    skill_id: str
    skill_version: str
    input: Dict[str, Any]
    engine: WorkerEngine
    request_id: str
    user_id: Optional[str]
    context: Optional[Dict[str, Any]]
    
    def validate_schema(self, manifest: SkillManifest) -> None:
        """Validate input against manifest.input_schema."""
        # Raises ValidationError if invalid

class SkillInvocationResponse:
    output: Dict[str, Any]
    latency_ms: int
    execution_trace: List[str]
    lom: str
    audit_event_id: str
    phase_completed: int
    error: Optional[str]
```

**Tests:** 8 unit tests (validation, schema, immutability)

#### 2. `core/engine/skill_invocation_service.py` (~400 LoC)

```python
class SkillInvocationService:
    def __init__(
        self,
        skill_registry: SkillRegistry,
        manifest_loader: SkillManifestLoader,
        audit_backend: AuditBackend,
        timeout_config: TimeoutConfig,
    ):
        self.registry = skill_registry
        self.manifests = manifest_loader
        self.audit = audit_backend
        self.timeouts = timeout_config
    
    async def invoke_skill(
        self,
        request: SkillInvocationRequest,
    ) -> SkillInvocationResponse:
        """
        Main entry point. Implements Phase 0–10.
        """
        # Phase 0: Intake (validation)
        # Phase 1: Manifest load
        # Phase 2: Context load (history, tenant state)
        # Phase 3: Schema validation
        # Phase 4-6: Skill execution (SKILL.md logic)
        # Phase 7: Audit emission
        # Phase 8-9: Output validation
        # Phase 10: Return response (immutable)
        
        # Timeout + fallback per phase
        # Return immutable response
```

**Tests:** 12 E2E tests (all phases, timeouts, fallbacks)

#### 3. `core/skills/skill_invocation_router.py` (~100 LoC)

```python
class SkillInvocationRouter:
    """Route Skill invocations based on engine + skill_id."""
    
    async def route(
        self,
        request: SkillInvocationRequest,
    ) -> SkillInvocationResponse:
        """
        Dispatch to correct handler by engine.
        For Phase A: all engines → same handler (service).
        For Phase B/C: Hermes/Copilot-specific logic (if needed).
        """
        return await self.service.invoke_skill(request)
```

**Tests:** 4 unit tests (routing, engine dispatch)

#### 4. `core/skills/audit_integration.py` (~80 LoC)

```python
async def emit_skill_invocation_event(
    audit_backend: AuditBackend,
    request: SkillInvocationRequest,
) -> str:
    """Emit skill_invocation_requested event."""
    event = AuditEvent(
        tenant_id=request.tenant_id,
        event_type="skill_invocation_requested",
        actor=f"skill:{request.skill_id}",
        resource=request.request_id,
        input_hash=hash(request.input),
        # ...
    )
    return await audit_backend.write_event(event)

async def emit_skill_invocation_completed(
    audit_backend: AuditBackend,
    request: SkillInvocationRequest,
    response: SkillInvocationResponse,
) -> str:
    """Emit skill_invocation_completed event."""
    event = AuditEvent(
        tenant_id=request.tenant_id,
        event_type="skill_invocation_completed",
        output_hash=hash(response.output),
        latency_ms=response.latency_ms,
        # ...
    )
    return await audit_backend.write_event(event)
```

**Tests:** 4 unit tests (event emission, audit chain)

### Tests (25+, ~350 LoC)

#### Tier 1 (Schema / Type)
- `test_skill_invocation_request_validation.py` — 4 tests (schema, immutability)
- `test_skill_invocation_response_validation.py` — 4 tests

#### Tier 2 (Unit)
- `test_skill_invocation_service_phases.py` — 8 tests (Phase 0–10)
- `test_skill_invocation_timeouts.py` — 4 tests (timeout + fallback per phase)
- `test_skill_invocation_routing.py` — 4 tests (engine dispatch)

#### Tier 3 (Integration)
- `test_skill_invocation_audit.py` — 6 tests (events emitted, hash-chain)
- `test_skill_invocation_e2e.py` — 4 tests (full flow, Claude Code invocation)

#### Tier 4 (Adversarial)
- `test_skill_invocation_adversarial.py` — 8 tests (PII injection, timeout cascade, tenant isolation)

### Documentation (200 LoC)

- `docs/claude-ref/layer-22-skill-invocation-api.md` — Full API reference + examples
- Inline docstrings in all files

### Integration Points

**Modified files (no new behavior, just routing):**
- `core/skills/system_integration.py` — Call `skill_invocation_service` instead of hardcoded path
- No behavior change to Claude Code (audit/output identical)

### Wiring into Claude Code (Phase A only)

```python
# core/skills/system_integration.py (existing)

class SkillSystemIntegration:
    def __init__(self, skill_invocation_service: SkillInvocationService):
        self.service = skill_invocation_service
    
    async def invoke_skill(
        self,
        skill_id: str,
        input_data: Dict[str, Any],
        skill_version: str = "latest",
        tenant_id: str = "_default",
    ) -> Dict[str, Any]:
        """Old interface (backward compat). Routes through RPC."""
        request = SkillInvocationRequest(
            tenant_id=tenant_id,
            skill_id=skill_id,
            skill_version=skill_version,
            input=input_data,
            engine=WorkerEngine.CLAUDE_CODE,
            request_id=str(uuid4()),
        )
        response = await self.service.invoke_skill(request)
        return response.output
```

### Verification Gate (Phase A)

- ✅ All tests pass (Tier 1–3 green)
- ✅ Audit events hash-chained (Tier 3 test)
- ✅ Claude Code behavior identical to old path (E2E test)
- ✅ Latency overhead <50ms (performance test)

---

## Phase B: Hermes Engine Integration (Weeks 3–4)

### Goal
Wire Hermes daemon to call `SkillInvocationService`. Async feedback loop. Session persistence.

### Deliverables

| File | LoC | Purpose | Dependencies |
|------|-----|---------|--------------|
| `core/engine/hermes_integration.py` | 300 | Hermes Skill wiring | Phase A service |
| `core/engine/hermes_feedback_emitter.py` | 150 | Async feedback channel | Phase A service |
| `operator/hermes_daemon/skill_boot.py` | 100 | Session persistence | Phase A |
| **Tests** | 200 | 15+ integration tests | all above |
| **Total** | **750** | | |

### New/Modified Files

#### 1. `core/engine/hermes_integration.py` (~300 LoC)

```python
class HermesSkillIntegration:
    def __init__(self, service: SkillInvocationService):
        self.service = service
        self.event_emitter = LearningEventEmitter()
    
    async def intake_task(self, task: Task) -> None:
        """Route task via os.delegation_router Skill."""
        request = SkillInvocationRequest(...)
        response = await self.service.invoke_skill(request)
        task.routing_decision = response.output
    
    async def adapt_context(self, task: Task) -> None:
        """Adapt context via os.context_adapter Skill."""
        # Similar: route through service
    
    async def emit_outcome(self, task: Task, outcome: TaskOutcome) -> None:
        """Emit feedback for Skill-grading (non-blocking)."""
        await self.event_emitter.emit_async(...)
```

#### 2. `core/engine/hermes_feedback_emitter.py` (~150 LoC)

```python
class HermesFeedbackEmitter:
    """Emit LearningEvents from Hermes task outcomes."""
    
    async def emit_async(self, feedback: LearningEvent) -> None:
        """Fire-and-forget event emission."""
        # Queue to EventEmitter (ADR-0314)
        # Skill-grader picks it up async
        # Never blocks Hermes
```

#### 3. `operator/hermes_daemon/skill_boot.py` (~100 LoC)

```python
async def boot_skill_state(tenant_id: str) -> Dict[str, Any]:
    """Load Skill state from disk on daemon restart."""
    # Load ~/.corvin/tenants/<tenant>/skills/*/grading_stats.json
    # Pass to Skill registry
    # Hermes resumes with prior learning state
```

### Tests (200 LoC, 15+)

#### Integration
- `test_hermes_skill_routing.py` — Hermes routes via Skill, audit logged
- `test_hermes_skill_context.py` — Hermes adapts context via Skill
- `test_hermes_feedback_loop.py` — Outcome emitted, Skill grader sees it
- `test_hermes_session_persistence.py` — Daemon restarts, Skill state intact

#### Adversarial
- `test_hermes_skill_timeout.py` — Skill timeout → Hermes fallback (no hang)
- `test_hermes_feedback_missing.py` — Missing feedback → Hermes continues (graceful)

### Verification Gate (Phase B)

- ✅ Hermes routes 100 tasks via Skill
- ✅ Feedback emitted for each task
- ✅ Skill scores include Hermes runs
- ✅ Daemon restart preserves Skill state

---

## Phase C: Copilot & OpenCode (Weeks 5–6)

### Goal
Copilot CLI + OpenCode Python import. Both call `SkillInvocationService`. A/B test support.

### Deliverables

| File | LoC | Purpose | Dependencies |
|------|-----|---------|--------------|
| `operator/cli/copilot_skill_wrapper.py` | 200 | CLI wrapper | Phase A |
| `core/engine/opencode_integration.py` | 150 | OpenCode import | Phase A |
| **Tests** | 150 | 10+ E2E tests | all above |
| **Total** | **500** | | |

### Files

#### 1. `operator/cli/copilot_skill_wrapper.py` (~200 LoC)

```python
@click.group()
def copilot_skill():
    """Invoke Corvin Skills from GitHub CLI."""
    pass

@copilot_skill.command()
@click.argument("skill_id")
@click.option("--input", type=str, required=True)
@click.option("--version", type=str, default="latest")
def invoke(skill_id: str, input: str, version: str):
    """Invoke a Skill, return JSON."""
    request = SkillInvocationRequest(...)
    response = await service.invoke_skill(request)
    click.echo(json.dumps(response.output))
```

#### 2. `core/engine/opencode_integration.py` (~150 LoC)

```python
# Re-export for OpenCode import
from core.engine.skill_invocation_service import SkillInvocationService

# Type stubs for IDE autocomplete
# Example usage documentation
```

### Tests (150 LoC, 10+)

- `test_copilot_cli_invoke.py` — CLI invocation works, JSON output
- `test_opencode_import.py` — Python import works in-process
- `test_copilot_opencode_a2a_test.py` — A/B test same Skill across engines

### Verification Gate (Phase C)

- ✅ `gh corvin skill invoke` works
- ✅ OpenCode Python import works
- ✅ A/B test: same Skill v1.2 across Claude Code, Hermes, Copilot, OpenCode
- ✅ Audit events all chained (all engines)

---

## Phase D: Multi-Engine Feedback + Audit Unification (Concurrent with B/C)

### Files

| File | LoC | Purpose | Dependencies |
|------|-----|---------|--------------|
| `core/skills/feedback_integration/feedback_ingester.py` | 200 | Multi-engine feedback intake | Phase A |
| `core/skills/skill_grader.py` | 300 | Per-engine + aggregate scoring | feedback_ingester |
| `core/compliance/audit_chain/unified_chain.py` | 250 | Unified audit (all systems) | audit_backend |
| **Tests** | 250 | 20+ integration tests | all above |
| **Total** | **1000** | | |

### Timeline

- **Weeks 1–2 (Phase A):** RPC API, Claude Code wiring
- **Weeks 3–4 (Phase B + D):** Hermes + feedback ingestion + unified audit (parallel)
- **Weeks 5–6 (Phase C + D):** Copilot/OpenCode + final feedback/audit wiring

---

## Test Pyramid & Coverage

### Tier 1 (Schema, Lint, Type) — Fast, every edit
- `ruff`, `mypy`, YAML validation
- All files must pass before Tier 2

### Tier 2 (Unit) — Per-file, <1s per test
- 40+ tests, all <50 LoC each
- Mock external dependencies (audit_backend, registry)
- Goal: >90% line coverage in core logic

### Tier 3 (Integration) — Cross-module, <5s per test
- 20+ tests
- Mock external systems (Skill manifest, audit backend)
- Real `SkillInvocationRequest`/`Response` objects
- Goal: all phase gates green

### Tier 4 (E2E) — Full system, <10s per test
- 10+ tests
- Real Skill-invocation-service
- Real audit-backend (in-memory for tests)
- Real Claude Code wiring
- Hermes daemon boot (Phase B+)
- A/B test across engines (Phase C+)

### Tier 5 (Adversarial) — Attack vectors, <10s per test
- 12+ tests
- PII injection → rejected
- Tenant isolation → enforced
- Timeout cascade → handled
- Feedback tampering → detected

---

## Rollout Timeline

```
Week 1  Phase A-1: Skeleton
        Phase A-2: Core logic + tests
        Tier 1–2 green ✓

Week 2  Phase A-3: Claude Code wiring
        Phase A-4: Audit integration
        Tier 3–4 green ✓
        
Week 3  Phase B-1: Hermes daemon wiring
        Phase D-1: Feedback ingestion
        Tier 3 green ✓

Week 4  Phase B-2: Session persistence
        Phase D-2: Unified audit
        Tier 4–5 green ✓

Week 5  Phase C-1: Copilot CLI
        Phase C-2: OpenCode import
        Tier 4 green ✓

Week 6  Phase C-3: A/B test
        Phase D-3: Multi-engine grading
        Tier 5 green ✓
        All phases done
```

---

## Success Criteria (End of k=5)

### Code Quality
- ✅ 0 CRITICAL findings (adversarial review)
- ✅ 0 HIGH findings (code-review Tier 4)
- ✅ <5 MEDIUM findings (acceptable, documented)
- ✅ >85% line coverage (Tier 2 tests)
- ✅ 100% Tier 4–5 tests pass (no flakes)

### Architecture
- ✅ Single, unified audit chain (all systems)
- ✅ Multi-engine feedback integration working
- ✅ A/B test support proven (same Skill across engines)
- ✅ Session persistence for Hermes (state survives restart)

### Docs
- ✅ docs-as-definition-of-done (API reference, examples, CLI help)
- ✅ All ADRs (0598–0602) linked in frontmatter
- ✅ Implementation plan matches code

### Performance
- ✅ Skill invocation overhead <50ms (hot path caching)
- ✅ Audit events emitted <10ms (async, non-blocking)
- ✅ E2E latency <500ms for typical Skill (Phase 0–10)

---

## Risk Mitigation

| Risk | Mitigation | Owner |
|------|-----------|-------|
| Phase A overruns | Pre-implement Phase A models in parallel | shumway |
| Hermes timeout cascade | Explicit phase timeouts + fallback heuristics | shumway |
| Audit-backend bottleneck | Async queue + batching + date partitioning | shumway |
| Tenant isolation breach | Per-request validation + adversarial tests | shumway |
| Copilot feedback missing (Phase C) | Document as Phase C+1 (deferred); A/B test still works without feedback | shumway |

---

## Escalation Thresholds

If any gate fails at k=5:

1. **Audit findings >10:** Stop. Root-cause via `root-cause-by-layer`. Architectural rethink needed.
2. **Performance regression >100ms:** Stop. Profile via `loss-backprop-lens`. May need Phase A refactor.
3. **Test flakes >2%:** Stop. Investigate via `reproducibility-first`. May need test isolation fix.

---

## See Also

- ADR-0598–0602 (architecture)
- CONCEPT-0029 (full dialektical analysis)
- `/home/shumway/.claude/skills/loop-driven-engineering` (LDD discipline)
