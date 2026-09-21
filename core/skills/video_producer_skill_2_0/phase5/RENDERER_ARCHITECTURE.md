# CEL Renderer Architecture — Consolidation Guide

**Last Updated**: 2026-09-21  
**Consolidation Status**: ✅ PHASE 1 COMPLETE (Zero Behavior Change)  
**Maintainer**: CorvinOS Rendering Layer

---

## System Overview

The CEL (Concept Explanation Layout) renderer system provides a 3-tier fallback chain for generating animated videos from concepts:

```
                    Animation Request
                           |
                    TierDispatcher
                           |
                +----------+----------+
                |          |          |
            Tier 3      Tier 2      Tier 1
           Premium     Manim        Quick
           (Async)    (Blocking)   (Blocking)
            ~60s       <60s         <10s
                |          |          |
                +----------+----------+
                           |
                    Voice Sync (Optional)
                           |
                    Output MP4
```

---

## Component Architecture

### 1. Renderer Base Class (`renderer_base.py`)
**Purpose**: Reference interface for standardized renderer behavior  
**Status**: Clean, stable  
**Key Classes**:
- `RendererRequest`: Standard request dataclass with animation_id, duration_seconds
- `RenderResult`: Standardized result dataclass
- `RendererBase`: Abstract base class (ABC) defining common interface

**Interface**:
```python
class RendererBase(ABC):
    def __init__(name, version, tier, timeout_seconds)
    def execute(request) -> dict  # Main rendering method
    def _render(request) -> dict  # Subclass override point
    def get_metrics() -> dict      # Performance tracking
```

**Note**: Subclass implementations do NOT inherit from `RendererBase` (see architecture notes below).

---

### 2. Tier 1: Quick Renderer (`quick_renderer.py`)
**Purpose**: Ultra-fast ASCII/SVG rendering (<10 seconds)  
**Status**: Stable, no changes needed  
**Implementation**:
- **Class**: `QuickRendererWorker`
- **Speed**: ~5 seconds target
- **Quality**: Low (ASCII/SVG)
- **Fallback**: Always succeeds (last-resort tier)

**Method Chain**:
1. `_create_svg_diagram()` - Generate SVG for animation_id
2. `_svg_to_png()` - Convert SVG → PNG (cairosvg/ImageMagick/ffmpeg)
3. `_create_mp4_from_frames()` - PNG + fade effect → MP4

**Result Keys**:
```python
{
    "success": True/False,
    "output_path": str,
    "duration_seconds": int,
    "render_time_ms": int,
    "error": Optional[str]
}
```

---

### 3. Tier 2: Manim Animator (external)
**Purpose**: High-quality mathematical animations (<60 seconds)  
**Status**: External (not in this directory)  
**Implementation**:
- Manim library for professional animations
- Supports complex mathematical concepts
- Typically 30-60 seconds render time

**Note**: Tier 2 implementation assumed to be in a separate module.

---

### 4. Tier 1.5: Three.js Renderer (`threejs_renderer.py`)
**Purpose**: GPU-accelerated 3D rendering via Puppeteer (<10 seconds)  
**Status**: Stable with placeholders documented  
**Implementation**:
- **Class**: `ThreeJSRenderer`
- **Speed**: ~5-10 seconds target
- **Quality**: High (3D, GPU-accelerated)
- **Dependencies**: Puppeteer, Node.js, chromium

**Method Chain**:
1. `_check_puppeteer_available()` - Verify Puppeteer is installed
2. `_generate_threejs_scene()` - Generate Three.js HTML scene
3. `_render_via_puppeteer()` - Puppeteer → Chromium → MP4
4. `_generate_puppeteer_script()` - Generate Node.js Puppeteer script

**Scene Types** (with M1 TODOs):
- `_scene_maestro_architecture()` - ✅ Implemented (Maestro + 5 orbiting workers)
- `_scene_learning_loop()` - 🔲 TODO (M1): 5-step cycle animation
- `_scene_audit_chain()` - 🔲 TODO (M1): Hash-linked nodes
- `_scene_skill_system()` - 🔲 TODO (M1): Hierarchical skills
- `_scene_context_flow()` - 🔲 TODO (M1): Data flow visualization

**Result Keys**:
```python
{
    "success": True/False,
    "output_path": str,
    "render_time_ms": int,
    "tier": "TIER_1_5_THREE_JS",
    "duration_seconds": int,
    "error": Optional[str],
    "fallback_required": Optional[bool]
}
```

---

### 5. Tier 3: Premium Async Queue (`premium_renderer.py`)
**Purpose**: Hand-crafted/Blender-generated premium videos (async)  
**Status**: Stable, no changes needed  
**Implementation**:
- **Classes**: 
  - `PremiumRenderJob`: Async job dataclass (job_id, status, timestamps)
  - `PremiumAsyncQueue`: Queue manager for concurrent renders
- **Concurrency**: Max 2 concurrent renders (configurable)
- **Status States**: queued → rendering → complete/failed

**Job Lifecycle**:
1. `submit_job()` - Queue async render job, return job_id
2. `process_queue()` - Async coroutine processing queued jobs
3. `_render_premium()` - Blender simulation (or load pre-rendered)
4. `get_job_status()` - Poll job status
5. `get_queue_stats()` - Get queue metrics

**Result Keys**:
```python
{
    "job_id": str,
    "status": "queued|rendering|complete|failed",
    "created_at": ISO8601_str,
    "started_at": ISO8601_str,
    "completed_at": ISO8601_str,
    "output_path": Optional[str],
    "error": Optional[str]
}
```

---

### 6. Tier Dispatcher (`tier_dispatcher.py`)
**Purpose**: Orchestrate 3-tier fallback chain with voice-sync integration  
**Status**: Stable, no changes needed  
**Implementation**:
- **Class**: `TierDispatcher`
- **Enums**: `TierLevel` (TIER_1_QUICK, TIER_2_RICH, TIER_3_PREMIUM)
- **Learning**: Integrated with `LearningOptimizer` (ADR-0314)

**Dispatch Flow**:
1. `dispatch(request)` - Main entry point
2. Get optimizer-recommended tier (based on past performance)
3. Try recommended tier first
4. If failed: fallback chain (Tier 3 → 2 → 1)
5. Optional: Apply voice-sync if narration_audio provided
6. Record learning outcome for optimizer

**Fallback Chain**:
```
Tier 3 (Premium) fails
  ↓
Try Tier 2 (Manim)
  ↓ (if Tier 2 fails)
Try Tier 1 (Quick) - always succeeds
```

**Voice-Sync Integration** (Phase 5):
- If `request.narration_audio` + `request.voice_sync_mapping` provided
- Compositor integrates narration timing with animation
- Returns voice-synced MP4 output

**Learning Integration** (Blocker 6, ADR-0314):
- Emits `SkillExecutedEvent` per render outcome
- Calculates confidence scores per tier
- Feeds back into tier selection (optimizer_feedback_next_tier)

---

## Result Dictionary Schema

All renderers return a dict with standardized keys:

### Common Keys (All Tiers)
```python
{
    "success": bool,                    # True if render succeeded
    "tier": str,                        # Tier identifier
    "output_path": Optional[str],       # Path to output MP4 (if success)
    "render_time_ms": int,              # Milliseconds to render
    "error": Optional[str],             # Error message (if failed)
}
```

### Tier-Specific Keys
- **Tier 1 (Quick)**: Standard only
- **Tier 1.5 (Three.js)**: Standard + `fallback_required` (bool), `quality_score` (0.0-1.0)
- **Tier 2 (Manim)**: Standard + `frame_dir` (optional), `quality_score`
- **Tier 3 (Premium)**: Job-based async result
  - `job_id` (str): Async job identifier
  - `status` (str): "queued", "rendering", "complete", "failed"
  - Timestamps: `created_at`, `started_at`, `completed_at`

---

## Architecture Notes

### Why Renderers Don't Inherit from RendererBase

**Design Decision**: `RendererBase` is a **reference interface**, not an enforced contract.

**Rationale**:
1. Different tiers have fundamentally different execution models:
   - Quick/Three.js: Synchronous, blocking
   - Premium: Asynchronous, job-based
   - Manim: External subprocess
2. Each tier optimizes for different constraints (speed, quality, resources)
3. Forcing all into one base class would add unnecessary abstraction
4. Tier-specific behavior is more important than base-class uniformity

**The `RendererBase` exists for**:
- Reference implementation (how to structure a renderer)
- Documentation (interface contract)
- Future unification (if async framework is adopted universally)

### Module Dependencies

**Internal**:
- `video_paths.py`: Output directory resolution

**External**:
- `subprocess`: FFmpeg, ImageMagick, Puppeteer, Blender
- `asyncio`: Premium tier async queue
- `tempfile`: Temporary file handling for rendering

**Optional**:
- `cairosvg`: SVG → PNG conversion (fallback to ImageMagick)
- `puppeteer`: Three.js rendering (fallback to slow path)
- `blender`: Premium tier rendering (simulation via FFmpeg)

---

## Consolidation Status

### Phase 1: Cleanup ✅ COMPLETE
- [x] Removed unused import: `shutil` from `threejs_renderer.py`
- [x] Documented placeholder scene methods with TODO (M1) markers
- [x] Added comprehensive architecture documentation

### Phase 2: Standardization (Deferred to M1)
- [ ] Unify result dict schemas (low priority, works today)
- [ ] Add comprehensive type hints (works today, nice-to-have)
- [ ] Implement pending Three.js scenes (M1 skeleton)

### Phase 3: Verification ✅ COMPLETE
- [x] All tests passing (no regression)
- [x] Zero behavior change confirmed
- [x] Code is clean and maintainable

---

## Testing

### Current Test Coverage
- Asset library: ✅ 3 test files (63 tests)
- Blocker 3 E2E: ✅ 1 test file
- Voice sync: ✅ 1 test file
- Learning feedback loop: ✅ 2 test files (40+ tests)

### Test Execution
```bash
cd /home/shumway/projects/CorvinOS
python -m pytest core/skills/video_producer_skill_2_0/phase5/test_*.py -v
```

**Status**: All existing tests continue to pass. Zero behavior change.

---

## Known Issues & Placeholders

### M1 Implementation Items
1. Three.js scene implementations (_scene_learning_loop, etc.) - currently return placeholder
2. Manim animator integration (assumed external)
3. Blender integration (simulated via FFmpeg)
4. Puppeteer timeout handling (30-second hard limit, could be tuned)

### Future Improvements (Post-M1)
- [ ] Implement dedicated Three.js scenes (4 pending)
- [ ] Add comprehensive error recovery
- [ ] Implement cache for rendered scenes
- [ ] Add quality scoring per tier
- [ ] Implement priority queue for Premium tier
- [ ] Add metrics dashboard integration

---

## Quick Start (for developers)

### Adding a New Renderer

1. **Choose tier** (1, 1.5, 2, or 3)
2. **Create class** implementing `execute(request) -> dict`
3. **Return standardized dict** with success, tier, output_path, render_time_ms
4. **Register with dispatcher** in `TierDispatcher.__init__()`
5. **Test end-to-end** with `dispatch()` call
6. **Update this document** with architecture notes

### Running Renderers Standalone

```python
# Quick renderer
from quick_renderer import QuickRendererWorker
qr = QuickRendererWorker(timeout_seconds=10)
result = qr.execute(request)

# Three.js renderer
from threejs_renderer import ThreeJSRenderer
tr = ThreeJSRenderer(timeout_seconds=10)
result = tr.execute(request)

# Via dispatcher (recommended)
from tier_dispatcher import TierDispatcher
dispatcher = TierDispatcher(tier1, tier2, tier3)
result = dispatcher.dispatch(animation_request)
```

---

## References

- **ADR-0741**: 3-Tier Animation Architecture (Phase 5.3)
- **ADR-0742**: Phase 5 Voice-Sync Integration
- **ADR-0314**: Learning Infrastructure (Blocker 6)
- **Blocker 6**: Learning Loop Integration
- **Blocker 7** (pending): M1 Skeleton Consolidation

---

**Status**: ✅ Consolidation Phase 1 complete. Ready for M0 closeout and M1 skeleton implementation.
