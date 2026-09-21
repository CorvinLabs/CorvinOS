# Renderer & Dispatcher Architecture

**Status:** Production-Ready (M0 Consolidation Complete)  
**Last Updated:** 2026-09-21  
**ADR Reference:** ADR-0742 (3-Tier Animation), ADR-0759 (Worker-Engine Model Routing)

---

## Table of Contents

1. [Overview](#overview)
2. [3-Tier Renderer Architecture](#3-tier-renderer-architecture)
3. [Dispatcher Implementations](#dispatcher-implementations)
4. [Unified Exception Hierarchy](#unified-exception-hierarchy)
5. [Audit Event Schema](#audit-event-schema)
6. [E2E Example: Animation Request to MP4](#e2e-example-animation-request-to-mp4)
7. [Error Handling & Recovery](#error-handling--recovery)
8. [Performance Metrics](#performance-metrics)

---

## Overview

CorvinOS consolidates multiple dispatcher implementations under a unified framework with shared patterns:

- **Dispatcher:** Routes work to appropriate handler (engine, skill, tier, etc.)
- **Renderer:** Produces output (video, animation, slide deck, etc.)
- **Audit Event:** Immutable, tenant-scoped event recorded in hash-chain
- **Exception:** Standardized error type with traceability (lom)

**Key Properties:**
- All events carry `tenant_id` (ADR-0007, GDPR Art. 5)
- All errors carry `lom` (line-of-moral-responsibility) for traceability
- All dispatchers share timeout, retry, and audit patterns

---

## 3-Tier Renderer Architecture

### Tier Structure

```
Tier 3: Premium (async, ~30-120s)
  ├─ Hand-crafted animations (Blender)
  ├─ Highest quality (0.95 score)
  ├─ Async queue (2 concurrent max)
  └─ Fallback: Tier 2

Tier 2: Rich (blocking, ~30-60s)
  ├─ Manim-powered mathematical animations
  ├─ Medium quality (0.75 score)
  ├─ Blocking render
  └─ Fallback: Tier 1

Tier 1: Quick (blocking, <10s)
  ├─ ASCII/SVG diagrams
  ├─ Low quality (0.5 score)
  ├─ Instant feedback
  └─ Fallback: None (always succeeds)
```

### TierDispatcher Routing Logic

```python
TierDispatcher.dispatch(request: AnimationRequest):
  1. Get optimizer-recommended tier (learning loop feedback)
  2. Try recommended tier
  3. If success → apply voice-sync (if narration provided) → return
  4. If failed → fallback chain:
     - Tier 3 → Tier 2 → Tier 1
     - Record outcome (success=True, fallback_used=True)
  5. If all tiers fail → return error
```

**Fallback Chain:**
- Tier 3 (Premium) → Tier 2 (Rich) → Tier 1 (Quick)
- Tier 2 (Rich) → Tier 1 (Quick)
- Tier 1 (Quick) → [None] (always succeeds)

### Result Dictionary Schema

```python
{
  "success": bool,              # True if render succeeded
  "tier": str,                  # "TIER_1_QUICK", "TIER_2_RICH", "TIER_3_PREMIUM"
  "output_path": Optional[str], # Path to MP4 (if success)
  "render_time_ms": int,        # Milliseconds to render
  "quality_score": float,       # 0.0-1.0 quality estimate
  "error": Optional[str],       # Error message (if failed)
  "fallback_required": bool,    # True if next tier should try
  "fallback_used": bool,        # True if preferred tier failed
  "voice_sync_applied": bool,   # True if narration integrated
  "keyframe_count": int,        # Number of voice-sync keyframes
}
```

### Renderer Base Class

All renderers inherit from `RendererBase`:

```python
class RendererBase(ABC):
    def __init__(self, name, version, tier, timeout_seconds):
        self.name = name
        self.version = version
        self.tier = tier
        self.timeout = timeout_seconds

    def execute(self, request) -> dict:
        """Standard execution with error handling & timeouts"""
        # Calls _render() (subclass-specific)
        # Catches exceptions
        # Tracks metrics

    @abstractmethod
    def _render(self, request) -> dict:
        """Subclass-specific rendering logic"""
        pass
```

### Implementations

**Quick Renderer (Tier 1)**
- File: `core/skills/video_producer_skill_2_0/phase5/quick_renderer.py`
- Class: `QuickRendererWorker`
- Timeout: 10s
- Quality: 0.5 (low)
- Method: SVG → PNG → MP4 with fade effect

**ThreeJS Renderer (Tier 1.5)**
- File: `core/skills/video_producer_skill_2_0/phase5/threejs_renderer.py`
- Class: `ThreeJSRenderer`
- Timeout: 10s
- Quality: 0.65 (medium-low)
- Method: Three.js + Puppeteer (headless Chrome)

**Premium Renderer (Tier 3)**
- File: `core/skills/video_producer_skill_2_0/phase5/premium_renderer.py`
- Class: `PremiumAsyncQueue`
- Timeout: 120s (async)
- Quality: 0.95 (high)
- Method: Blender or pre-rendered assets

**Tier Dispatcher**
- File: `core/skills/video_producer_skill_2_0/phase5/tier_dispatcher.py`
- Class: `TierDispatcher`
- Wires all three tiers with fallback logic & learning loop

---

## Dispatcher Implementations

### 1. RunDispatcher (Gateway)

**Location:** `core/gateway/corvin_gateway/dispatcher.py`

**Purpose:** Run lifecycle management (accepted → running → completed)

**Methods:**
- `spawn()`: Async spawn engine with budget enforcement
- State transitions: accepted → running → completed/failed/budget_exceeded

**Audit Events:**
- `engine_spawned`: When engine process starts
- `engine_completed`: When engine finishes
- `engine_failed`: When engine crashes

### 2. SkillDispatcher

**Location:** `core/task_engine/skill_dispatcher.py`

**Purpose:** Route requests to registered skills + versioning

**Methods:**
- `register_skill()`: Register skill + version
- `dispatch()`: Route to skill, catch exceptions, emit events

**Audit Events:**
- `skill_executed`: When skill runs
- `skill_failed`: When skill raises exception

### 3. TierDispatcher (Renderers)

**Location:** `core/skills/video_producer_skill_2_0/phase5/tier_dispatcher.py`

**Purpose:** 3-tier render fallback chain

**Methods:**
- `dispatch()`: Try recommended tier → fallback chain
- `_try_tier()`: Attempt single tier render
- `_get_fallback_chain()`: Deterministic chain lookup
- `_apply_voice_sync()`: Integrate narration (Phase 5)

**Audit Events:**
- `render_started`: When dispatch begins
- `render_completed`: When render succeeds
- `render_failed`: When render fails
- `tier_fallback`: When fallback to next tier occurs

### 4. FlowDispatcher

**Location:** `corvin_operator/bridges/shared/flow_dispatcher.py`

**Purpose:** Route to node matching requirements (e.g., "needs GPU")

**Methods:**
- `resolve()`: Find node satisfying requirements
- `_satisfies()`: Check if node matches requirements

### 5. CommandDispatcher (CLI)

**Location:** `corvin_operator/bridges/shared/eci/dispatcher.py`

**Purpose:** Route CLI commands to handlers

**Methods:**
- `dispatch_btw()`: Route BTW commands
- `dispatch_native()`: Route native commands

### 6. HookDispatcher

**Location:** `corvin_operator/bridges/shared/teb/hook_dispatcher.py`

**Purpose:** Event hook registration & dispatch

**Methods:**
- `register()`: Register hook callback
- `dispatch_pre()`: Fire pre-event hooks

### 7. AlertDispatcher

**Location:** `core/learning/alert_dispatcher.py`

**Purpose:** Alert routing with signature verification (security)

**Methods:**
- `process_alert()`: Verify signature → rate-limit → act
- Rate-limiting: 1 CRITICAL per 5 min per component
- Manual confirmation: Required for CRITICAL alerts

**Audit Events:**
- `alert_processed`: When alert is processed
- `alert_rejected`: When signature check fails
- `alert_rate_limited`: When rate limit triggered

### 8. WebhookDispatcher

**Location:** `core/gateway/corvin_gateway/webhooks.py`

**Purpose:** Webhook delivery & retry logic

**Methods:**
- `post()`: Send webhook with retry backoff

---

## Unified Exception Hierarchy

**File:** `core/dispatch/exceptions.py`

```python
DispatcherException (base)
├─ DispatcherTimeout        # Async operation exceeded timeout
├─ DispatcherConfigError    # Invalid configuration (fail-closed)
├─ DispatcherFailed         # Final failure (no recovery)
├─ DispatcherAuthError      # Auth/permission check failed (security)
└─ DispatcherNotFound       # Requested handler not found
```

**Properties:**
```python
@dataclass
class DispatcherException:
    message: str
    tenant_id: Optional[str]  # Tenant isolation (ADR-0007)
    lom: Optional[str]        # Line-of-moral-responsibility ("file.py:L123")
    context: Optional[dict]   # Structured error data
```

**Usage:**

```python
# Dispatcher catching exception
try:
    result = tier_dispatcher.dispatch(request)
except DispatcherTimeout as e:
    # Emit audit event
    audit_event = DispatcherAuditEvent(
        event_type=AuditEventType.RENDER_TIMEOUT,
        dispatcher_id="tier_dispatcher",
        tenant_id=e.tenant_id,
        lom=e.lom,
        status="timeout",
        error_type="DispatcherTimeout",
    )
    audit_store.write_event(audit_event)
    # Fallback or retry
```

---

## Audit Event Schema

**File:** `core/dispatch/audit_event.py`

```python
@dataclass(frozen=True)
class DispatcherAuditEvent:
    # Event identity
    event_id: str                      # UUID (12 hex chars)
    event_type: AuditEventType         # skill_executed, render_completed, etc.
    timestamp: str                     # ISO 8601 + 'Z'

    # Dispatcher identity
    dispatcher_id: str                 # "tier_dispatcher", "skill_dispatcher"
    dispatcher_version: str            # "5.1.0" (semantic)

    # Tenant isolation (MANDATORY — fail-closed if missing)
    tenant_id: str                     # MUST be present

    # Request/response hashing (integrity)
    input_hash: Optional[str]          # SHA256 of request
    output_hash: Optional[str]         # SHA256 of result

    # Execution metrics
    latency_ms: int                    # How long dispatch took
    status: str                        # "success", "failed", "timeout", etc.
    error_type: Optional[str]          # Exception class name

    # Traceability
    lom: Optional[str]                 # "tier_dispatcher.py::dispatch:L89"
    lom_hash: Optional[str]            # SHA256(lom + context) for anti-spoofing

    # Chain integrity (ADR-0232)
    prev_hash: Optional[str]           # SHA256 of previous event
    event_hash: Optional[str]          # SHA256 of this event

    # Contextual data (no PII)
    context_data: Optional[dict]       # {tier: "tier_2", quality_score: 0.85}
```

**Stored in:** `~/.corvin/tenants/{tenant_id}/global/forge/audit.jsonl`

**Chain Integrity:** Each event's `prev_hash` points to previous event's `event_hash` (forms immutable chain).

---

## E2E Example: Animation Request to MP4

### Scenario: User requests "Learning Loop" animation with narration

```python
# 1. User submits request via console
request = AnimationRequest(
    animation_id="learning-loop",
    duration_seconds=60,
    didactic_level="advanced",
    narration_audio="/tmp/narration.mp3",
    voice_sync_mapping={
        "frame_to_event": {
            30: "Click here to start",
            90: "Feedback loop closes",
        }
    }
)

# 2. TierDispatcher receives request
dispatcher = TierDispatcher(
    tier1=QuickRendererWorker(),
    tier2=ManimAnimatorWorker(),
    tier3=PremiumAsyncQueue(),
    learning_optimizer=LearningOptimizer()
)

# 3. Dispatcher consults learning loop for recommended tier
recommended_tier = learning_optimizer.optimizer_feedback_next_tier("learning-loop")
# → Returns TierLevel.TIER_2_RICH (based on past successful renders)

# 4. Try recommended tier (Tier 2: Manim)
result = dispatcher._try_tier(TierLevel.TIER_2_RICH, request)
# → Manim renders in 45 seconds → success

# 5. Apply voice-sync (Phase 5)
if request.narration_audio:
    result = dispatcher._apply_voice_sync(result, request)
    # → VoiceSyncCompositor integrates MP3 at keyframes
    # → Output: learning-loop_voice_synced.mp4

# 6. Record outcome for learning loop
outcome = RenderOutcome(
    success=True,
    tier=TierLevel.TIER_2_RICH,
    animation_id="learning-loop",
    render_time_ms=45000,
    quality_score=0.85,
    fallback_used=False
)
learning_optimizer.record_render_outcome(outcome)

# 7. Emit audit event
audit_event = DispatcherAuditEvent(
    event_type=AuditEventType.RENDER_COMPLETED,
    dispatcher_id="tier_dispatcher",
    dispatcher_version="5.2.0",
    tenant_id=tenant_id,
    status="success",
    latency_ms=45000,
    lom="tier_dispatcher.py::dispatch:L93",
    context_data={
        "tier": "TIER_2_RICH",
        "quality_score": 0.85,
        "voice_sync_applied": True,
        "keyframe_count": 2,
    }
)
audit_store.write_event(audit_event)

# 8. Return result to console
return {
    "success": True,
    "tier": "TIER_2_RICH",
    "output_path": "/path/to/learning-loop_voice_synced.mp4",
    "render_time_ms": 45000,
    "quality_score": 0.85,
    "voice_sync_applied": True,
    "keyframe_count": 2,
}
```

---

## Error Handling & Recovery

### Timeout Pattern

```python
# Quick Renderer (10s timeout)
try:
    result = _create_mp4_from_frames(png_path, duration_seconds)
except TimeoutError:
    # Return fail → TierDispatcher tries next tier
    return {
        "success": False,
        "error": "Timeout: FFmpeg exceeded 10s",
        "render_time_ms": 10001,
        "fallback_required": True,
    }

# TierDispatcher sees fallback_required=True
if not result["success"] and result.get("fallback_required"):
    next_tier = self._get_fallback_chain(current_tier)[0]
    # Try next tier
```

### Exception Pattern

```python
# Dispatcher executes skill
try:
    result = skill.execute(request)
except DispatcherConfigError as e:
    # Config error = fail-closed (no retry)
    audit_event = DispatcherAuditEvent(
        event_type=AuditEventType.DISPATCH_CONFIG_ERROR,
        status="failed",
        error_type="DispatcherConfigError",
        lom=e.lom,
        tenant_id=e.tenant_id,
    )
    audit_store.write_event(audit_event)
    raise  # Don't retry

except DispatcherTimeout as e:
    # Timeout = may retry with fallback
    audit_event = DispatcherAuditEvent(
        event_type=AuditEventType.DISPATCH_TIMEOUT,
        status="timeout",
        error_type="DispatcherTimeout",
        lom=e.lom,
        tenant_id=e.tenant_id,
    )
    audit_store.write_event(audit_event)
    # Fallback logic
```

### Fallback Recovery Chain

```
Tier 3 fails (error)
  ↓ Record outcome (success=False)
  ↓ Emit audit event (tier_fallback)
  ↓
Tier 2 (retry, blocking)
  ├─ Success → record outcome + return
  └─ Failure → fallback
      ↓
      Tier 1 (retry, always succeeds)
        ├─ Success → record outcome + return
        └─ Failure → [No fallback] ERROR
```

---

## Performance Metrics

### Renderer Metrics

```python
renderer.get_metrics()
# Returns:
{
    "total_renders": 150,
    "successful_renders": 147,
    "failed_renders": 3,
    "success_rate": 0.98,  # 98%
    "avg_render_time_ms": 5200,  # ~5.2 seconds
}
```

### Tier Distribution (from audit trail)

```python
# Query audit events by tier
audit_events = audit_store.query(
    event_type="render_completed",
    tenant_id="default",
    context_filter={"tier": "TIER_2_RICH"}
)

# Aggregate
tier_stats = {
    "TIER_1_QUICK": {
        "count": 300,
        "success_rate": 1.0,  # Always succeeds
        "avg_latency_ms": 4200,
        "quality_avg": 0.50,
    },
    "TIER_2_RICH": {
        "count": 142,
        "success_rate": 0.98,
        "avg_latency_ms": 35000,
        "quality_avg": 0.75,
    },
    "TIER_3_PREMIUM": {
        "count": 8,
        "success_rate": 0.87,
        "avg_latency_ms": 90000,
        "quality_avg": 0.92,
    },
}
```

### Learning Loop Optimization

```python
# Learning optimizer adapts tier selection over time
learning_optimizer.get_convergence_stats()
# Returns:
{
    "animation_id": "learning-loop",
    "recommended_tier": "TIER_2_RICH",
    "confidence": 0.92,  # 92% confidence in recommendation
    "success_rate_before": 0.70,
    "success_rate_after": 0.98,
    "iterations": 47,
}
```

---

## Integration Points

### Console (Web UI)
- `/app/video-producer` → calls TierDispatcher.dispatch()
- Real-time metrics in dashboard
- Voice-sync preview (Phase 5)

### Learning Loop (ADR-0314)
- RenderOutcome → optimizer feedback
- Confidence scores influence tier selection
- Dashboard shows convergence

### Audit Trail (ADR-0232)
- All events hash-chained
- Tenant isolation verified
- GDPR Art. 30 compliance

### Voice-Sync Integration (Phase 5)
- VoiceSyncCompositor orchestrates narration
- Keyframe mapping from VoiceSyncMapper
- Output: H.264 MP4 with narration-synced animation

---

## Testing

### Unit Tests
- `tests/skills/video_producer/test_*_renderer.py`
- Verify execute() error handling
- Verify timeout handling
- Verify result dict schema

### Integration Tests
- `tests/skills/video_producer/test_tier_dispatcher_e2e.py`
- Full 3-tier fallback chain
- Voice-sync integration (Phase 5)
- Learning loop feedback

### Adversarial Tests
- `tests/skills/video_producer/test_tier_dispatcher_hostile.py`
- Timeout attacks (all tiers timeout simultaneously)
- Configuration attacks (invalid tier config)
- Learning loop poisoning (fake outcomes)

---

**Document Version:** 1.0  
**Status:** Production-Ready  
**Last Reviewed:** 2026-09-21  
**Next Review:** After Phase M1 (skeleton implementation)
