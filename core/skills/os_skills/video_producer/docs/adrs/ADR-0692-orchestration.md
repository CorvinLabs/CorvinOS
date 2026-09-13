---
id: ADR-0692
type: adr
status: ACCEPTED
depends_on: [ADR-0690, ADR-0691]
related: [ADR-0693, ADR-0694, ADR-0695, CONCEPT-0040]
paths:
  - core/skills/os_skills/video_producer/src/orchestrator.py
  - core/skills/os_skills/video_producer/src/maestro.py
  - core/skills/os_skills/video_producer/types.py
docs:
  - core/skills/os_skills/video_producer/docs/BUILD_PROCESS.md
  - core/skills/os_skills/video_producer/docs/ARCHITECTURE.md
canonical: "/home/shumway/projects/Corvin-ADR/decisions/ADR-0692.md"
amendments: []
---

# ADR-0692: Video Producer Orchestration Architecture

## Summary

The Video Producer Skill orchestrates a seven-phase pipeline (asset analysis → storyboard → workers → assembly → upload) using a **Maestro + Precondition Decorator** pattern. The Maestro supervises each phase while Workers remain simple and independently testable.

## Problem

How do we orchestrate 4+ independent workers (slide generator, voice synthesizer, screenshot capturer, video assembler) without:
1. Tightly coupling workers to the main flow
2. Creating a bottleneck at the orchestrator
3. Making workers hard to test in isolation
4. Losing visibility into which phase failed

## Design Rationale

### Thesis: Master-Worker (Centralized Control)
- **Pro:** Single orchestrator = clear flow, easy tracing
- **Con:** Maestro is SPOF (single point of failure)
- **Con:** Workers can't fail gracefully (all-or-nothing)

### Antithesis: Self-Managed Workers (Distributed)
- **Pro:** Worker failure is isolated
- **Pro:** Horizontal scaling
- **Con:** Distributed tracing is hard
- **Con:** Race conditions

### Synthesis: Maestro + Precondition Decorators
```
┌─────────────────────────────────────────────┐
│  Maestro (Orchestrator)                     │
│  Supervises 7 phases                        │
│  Decides: retry/skip/abort                  │
│  Emits audit events                         │
└────┬────────────────────────────────────────┘
     │
     ├─→ Phase 1-2: Asset → Analysis → Storyboard
     │
     ├─→ Phase 3: Parallel Workers
     │   ├─→ SlideGenerator       @require_precondition(...)
     │   ├─→ VoiceSynthesizer     @require_precondition(...)
     │   ├─→ ScreenshotCapturer   @require_precondition(...)
     │   └─→ VideoAssembler       @require_precondition(...)
     │
     ├─→ Phase 4: YouTube Upload
     │
     └─→ Audit Trail (hash-chained)
```

## Seven-Phase Pipeline

| Phase | Owner | Input | Output | Precondition | Audit Event |
|-------|-------|-------|--------|--------------|------------|
| 1 | AssetAnalyzer | Files | Analysis | files != empty | asset_analysis_complete |
| 2 | StoryboardGen | Analysis | Storyboard | analysis.ready_for_storyboard | storyboard_generated |
| 3 | Workers (Parallel) | Storyboard | Slides+Audio | storyboard.scenes != empty | worker_executed |
| 4 | VideoAssembler | Slides+Audio | MP4 | all slides && audio exist | video_assembled |
| 5 | YouTubeUploader | MP4 | Task ID | video_path valid && audit_chain intact | youtube_upload_queued |
| 6 | FeedbackCollector | Video | Feedback | (Phase 4b only) | feedback_received |
| 7 | Learning Optimizer | Feedback | Updated Weights | (Phase 4b only) | optimizer_updated |

## Key Design Decisions

### 1. Precondition Decorators
Workers declare preconditions (not assertions):

```python
@require_precondition("scene.visuals != None", worker="SlideGenerator")
async def generate_slides(self, scene: Scene) -> list[bytes]:
    # Worker doesn't check preconditions (Maestro does)
    # Worker just implements the core logic
    pass
```

**Why:**
- Worker is testable in isolation (mock preconditions)
- Maestro decides if precondition failure = skip/retry/abort
- Feedback can tune precondition thresholds (Phase 4b)

### 2. Deterministic Workers
All workers must produce byte-for-byte identical output given same input.

**Why:**
- Audit trail requires reproducibility
- Enables local testing + CI/CD validation
- Quality gates can rely on consistent output

**How:**
- No random numbers (use seeds)
- No external API randomness (cache responses)
- Serialize all non-determinism (timestamp, user ID) into inputs

### 3. Audit Trail (Hash-Chained)
Every phase transition is logged:

```json
{
  "timestamp": "2026-09-13T12:34:56Z",
  "event_type": "phase_transition",
  "phase": 3,
  "worker": "SlideGenerator",
  "input_hash": "sha256(...)",
  "output_hash": "sha256(...)",
  "prev_hash": "sha256(...)",  // Chained to phase 2
  "tenant_id": "_default",
  "status": "success"
}
```

**Why:**
- GDPR Art. 30: Provenance proof
- Enables root-cause debugging (which phase failed?)
- Detects tampering (broken hash chain = corrupted data)

### 4. Feedback Integration (Phase 4b)
Maestro adjusts strategy based on operator feedback:

```python
# Phase 4b: Collect feedback
feedback = await collect_feedback(video_path)
# E.g., {"quality": 4.5, "narration": "too fast", "model": "prefer-opus"}

# Next run: Maestro uses feedback to decide
# - Skip slow worker? (model_selection skill)
# - Adjust voice speed?
# - Use different LLM?
```

**Why:**
- Closes learning loop (ADR-0314)
- Operator teaches system (no code changes needed)
- Confidence scores converge over time

## Implementation Notes

### Maestro Class
```python
class VideoProducerOrchestrator:
    async def orchestrate(
        self,
        asset_paths: list[str],
        instructions: dict = None,
        skip_phases: list[int] = None  # For testing
    ) -> dict:
        """7-phase orchestration."""
        # Phase 1: Asset ingestion
        assets = await self._phase_1_ingest(asset_paths)
        
        # Phase 2: Analysis
        analysis = await self._phase_2_analyze(assets)
        self._check_analysis_gates(analysis)  # ADR-0690
        self._emit_audit_event("analysis_complete")  # ADR-0691
        
        # Phase 3: Storyboard
        storyboard = await self._phase_3_storyboard(analysis)
        
        # Phase 4: Parallel workers
        if 4 not in skip_phases:
            results = await self._phase_4_parallel_workers(storyboard)
        
        # ... continue phases
```

### Worker Decorator
```python
from functools import wraps

def require_precondition(condition_check: str, worker: str):
    """Declare preconditions for a worker."""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            # Maestro checks precondition before calling
            # If fails, Maestro decides retry/skip/abort
            return await func(self, *args, **kwargs)
        wrapper._precondition = condition_check
        wrapper._worker_name = worker
        return wrapper
    return decorator
```

## Constraints (Load-Bearing)

1. **Quality Gates are Immutable** (ADR-0690)
   - DRAFT/PRODUCTION/BROADCAST tiers don't change mid-pipeline
   - Learning adjusts weights, not gates

2. **Audit Trail is Authoritative** (ADR-0691)
   - Every phase transition is logged
   - Hash-chain integrity verified before upload
   - Broken chain = reject upload (fail-closed)

3. **Workers are Deterministic**
   - Same input → same output (bit-for-bit)
   - Required for reproducible quality checks

4. **Feedback is Integrated**
   - Phase 4b: Maestro reads feedback
   - Next run: Maestro adjusts strategy (model selection, voice speed, etc.)

5. **Design System is Locked** (ADR-XXXX TBD)
   - Per-project design system (colors, fonts, thresholds)
   - Immutable for a given version
   - New designs require version bump (v1.0.0 → v2.0.0)

## Error Handling

```python
# Maestro decides what to do on phase failure
async def _orchestrate_phase(self, phase: int, worker_fn):
    try:
        result = await worker_fn()
        return result
    except PreconditionNotMet as e:
        # Feedback might say "skip this worker"
        self.log_skipped_phase(phase, reason=str(e))
        return None  # Skip to next phase
    except WorkerError as e:
        # Retry with exponential backoff
        for attempt in range(3):
            try:
                result = await worker_fn()
                return result
            except Exception:
                if attempt == 2:
                    # Final attempt failed → abort entire job
                    self.emit_audit_event("orchestration_failed", reason=str(e))
                    raise OrchestrationFailed(f"Phase {phase} failed after 3 retries")
```

## Testing Strategy

| Level | Scope | Example |
|-------|-------|---------|
| **Unit** | Single worker in isolation | `test_slide_generator.py` (no Maestro) |
| **Integration** | Worker + Maestro (mock other workers) | `test_orchestrator_phase3.py` |
| **E2E** | Full pipeline (real workers + real output) | `test_orchestrator_e2e.py` (assets → MP4) |
| **Adversarial** | Stress test (corrupted inputs, failures) | `test_orchestrator_adversarial.py` |

## Related Work

- **CONCEPT-0040:** Orchestrated Multi-Skill Pattern (reusable for other plugins)
- **ADR-0693:** Asset Analyzer Worker (Phase 2)
- **ADR-0694:** Voice + Screenshot Workers (Phase 3)
- **ADR-0695:** Assembler + YouTube Workers (Phase 4)
- **ADR-0690:** Quality Gates (compliance enforcement)
- **ADR-0691:** Audit Trail Hash-Chaining (provenance)
- **ADR-0314:** Learning Infrastructure (feedback integration)

## Future Work

- **Phase 4b:** Learning loop (feedback → confidence)
- **Phase 5:** Console dashboard (live orchestration view)
- **Phase 6:** Marketplace publish (installable plugin)
- **Phase 7:** Scaling (multi-tenant, webhooks, cron)

---

**Status:** ACCEPTED  
**Implemented:** 2026-09-13 (Phase 1 Week 2)  
**Next Review:** 2026-09-27 (Phase 4b adversarial review)
