---
id: ARCHITECTURE
type: reference
canonical: "null"
depends_on: [ADR-0692, ADR-0690, ADR-0691, CONCEPT-0040]
last_updated: 2026-09-13
---

# Video Producer Architecture Overview

*System design, layer structure, and component interactions.*

## System Architecture (7-Phase Pipeline)

```
┌─────────────────────────────────────────────────────────────┐
│ Video Producer Orchestrator (Maestro)                       │
│ └─ Coordinates all phases                                   │
│ └─ Emits audit events (hash-chained)                        │
│ └─ Integrates feedback (Phase 4b)                           │
└───────┬─────────────────────────────────────────────────────┘
        │
        ├─→ Phase 1: Asset Ingestion
        │   Input: [PowerPoint, Images, Documents]
        │   Output: Raw assets organized by type
        │   Component: AssetIngester
        │
        ├─→ Phase 2: Deep Analysis (ADR-0693)
        │   Input: Raw assets
        │   Output: AssetAnalysisResult {structure, visuals, metadata}
        │   Component: AssetAnalyzer
        │   ├─ Checks quality gates (ADR-0690)
        │   └─ Emits audit event (ADR-0691)
        │
        ├─→ Phase 3: Storyboard Generation
        │   Input: AssetAnalysisResult
        │   Output: Storyboard {scenes, timing, narration}
        │   Component: StoryboardGenerator (LLM-constrained to analysis facts)
        │
        ├─→ Phase 4: Parallel Workers (ADR-0694)
        │   Parallel execution:
        │   ├─ SlideGenerator      (300 LOC)
        │   │  Input: Scene (visuals, timing)
        │   │  Output: PNG slides (high-res)
        │   │  Tech: Pillow + fonts (deterministic)
        │   │
        │   ├─ VoiceSynthesizer    (200 LOC)
        │   │  Input: Scene (narration text, voice_params)
        │   │  Output: WAV audio (matching timing)
        │   │  Tech: TTS API (deterministic seed)
        │   │
        │   ├─ ScreenshotCapturer  (150 LOC)
        │   │  Input: Scene (UI spec, viewport)
        │   │  Output: PNG screenshot
        │   │  Tech: Playwright (deterministic)
        │   │
        │   └─ [All workers run in parallel, maestro waits for all]
        │
        ├─→ Phase 5: Video Assembly (ADR-0695)
        │   Input: Slides, Audio, Screenshots
        │   Output: MP4 video (H.264 + AAC)
        │   Component: VideoAssembler
        │   ├─ Validates codec specs
        │   ├─ Calls FFmpeg subprocess
        │   └─ Emits audit event
        │
        ├─→ Phase 6: YouTube Upload (ADR-0695)
        │   Input: MP4 video path
        │   Output: YouTube Task ID (for async monitoring)
        │   Component: YouTubeUploader
        │   ├─ Checks audit chain integrity (ADR-0691)
        │   ├─ Queues async upload (non-blocking)
        │   └─ Returns task_id for polling
        │
        └─→ Audit Trail (Hash-Chained, ADR-0691)
            Every phase transition logged + verified
            Broken chain = reject upload (fail-closed)
```

## Component Breakdown

### Maestro (Orchestrator)
- **File:** `src/orchestrator.py`
- **Responsibility:** Supervises all 7 phases
- **Key Methods:**
  - `orchestrate(asset_paths, instructions)` — Main entry point
  - `_check_analysis_gates(analysis)` — Quality gate enforcement (ADR-0690)
  - `_emit_audit_event(event_type, payload)` — Hash-chained logging
  - `_collect_feedback(video_path)` — Phase 4b integration

### Workers (Independent Components)
All workers follow the same pattern:

```python
class WorkerName:
    def __init__(self, config: dict):
        self.config = config
        # Load design_system.json thresholds
    
    @require_precondition("input != None")
    async def execute(self, input_obj) -> Output:
        # 1. Validate preconditions (checked by maestro)
        # 2. Execute deterministically
        # 3. Return output + metadata (timing, quality_score)
```

#### SlideGenerator
- **Input:** Scene (visuals, text, timing)
- **Output:** list[PNG bytes]
- **Tech:** Pillow (PIL) + custom fonts
- **Precondition:** scene.visuals != None
- **Tests:** 12+ (edge cases, font rendering, DPI)

#### VoiceSynthesizer
- **Input:** Scene (narration text, voice params)
- **Output:** WAV bytes
- **Tech:** TTS API (deterministic seed for reproducibility)
- **Precondition:** scene.narration_text != None
- **Tests:** 8+ (timing accuracy, silence handling)

#### ScreenshotCapturer
- **Input:** Scene (UI spec, viewport size)
- **Output:** PNG bytes
- **Tech:** Playwright headless browser
- **Precondition:** scene.ui_spec != None
- **Tests:** 6+ (viewport sizes, async waits)

#### VideoAssembler
- **Input:** Slides, Audio, Screenshots + timing
- **Output:** MP4 video
- **Tech:** FFmpeg subprocess (deterministic codec params)
- **Precondition:** all files exist + timing valid
- **Tests:** 16+ (codec validation, bitrate, duration)

### Quality Gates (ADR-0690)
- **File:** `src/quality_gates.py`
- **Responsibility:** Hardcoded thresholds (DRAFT/PRODUCTION/BROADCAST)
- **Rules:**
  - Gates are immutable (no runtime override)
  - Learning adjusts confidence, not gates
  - Fail-closed (default: reject if uncertain)

### Audit Trail (ADR-0691)
- **File:** `core/security/audit_chain.py` (core library)
- **Integration:** Every phase logs to hash-chained audit.jsonl
- **Event Schema:**
  ```json
  {
    "tenant_id": "_default",
    "timestamp": "2026-09-13T12:34:56Z",
    "event_type": "video_orchestrated",
    "phase": 4,
    "worker": "VideoAssembler",
    "input_hash": "sha256(...)",
    "output_hash": "sha256(...)",
    "prev_hash": "sha256(...)",  // Linked to phase 3
    "status": "success"
  }
  ```

### Design System (Locked Asset)
- **File:** `design_system.json` (v1.0.0)
- **Immutable:** True (loaded at boot, never modified)
- **Contents:**
  ```json
  {
    "version": "1.0.0",
    "quality_tiers": {
      "DRAFT": {"clarity_min": 70},
      "PRODUCTION": {"clarity_min": 85},
      "BROADCAST": {"clarity_min": 95}
    },
    "assets": {
      "fonts": ["Arial", "Helvetica"],
      "colors": ["#1e3a5f", "#ffffff"],
      "logos": ["logo_hd.png"]
    }
  }
  ```

## Data Flow

### End-to-End: Asset → Video

```
User Input
    ↓
Asset Paths ["ppt.pptx", "logo.png"]
    ↓
Phase 1: Ingest (copy to project/assets/)
    ↓
Phase 2: Analyze (AssetAnalyzer.analyze())
    ├─ Extract text, images, metadata
    ├─ Validate against design_system.json
    └─ Emit audit event "analysis_complete"
    ↓
Phase 3: Storyboard (LLM constrained to analysis facts)
    ├─ Generate scenes [Scene, Scene, ...]
    └─ Emit audit event "storyboard_generated"
    ↓
Phase 4: Workers (Parallel)
    ├─ SlideGenerator → list[PNG]      (audit: "slides_generated")
    ├─ VoiceSynthesizer → WAV          (audit: "audio_generated")
    └─ ScreenshotCapturer → PNG        (audit: "screenshot_generated")
    ↓
Phase 5: Assembly (FFmpeg)
    ├─ Concatenate slides + audio + screenshots
    └─ Emit audit event "video_assembled"
    ↓
Phase 6: Upload (YouTube)
    ├─ Verify audit chain (hash-chained)
    ├─ Queue async upload
    └─ Emit audit event "youtube_upload_queued"
    ↓
Output: {"video_path": "...", "youtube_task_id": "..."}
```

## Failure Modes & Recovery

| Failure | Detection | Recovery | Logged As |
|---------|-----------|----------|-----------|
| Analysis gate failure | Quality threshold not met | Abort (fail-closed) | analysis_gate_failed |
| Worker precondition not met | Maestro checks before calling | Skip/Retry/Abort | worker_precondition_failed |
| Worker timeout (>60s) | asyncio.timeout | Retry up to 3x, then abort | worker_timeout |
| Audit chain broken | Hash verification fails | Reject upload (fail-closed) | audit_chain_broken |
| YouTube API error | HTTP 5xx response | Queue for retry (exponential backoff) | youtube_upload_failed |
| Design system corrupted | JSON parse error | Boot fails (fail-closed) | design_system_invalid |

## Compliance Integration

### GDPR (Art. 30 — Accountability)
- ✅ Audit chain proves every decision
- ✅ Tenant scoping (audit events have tenant_id)
- ✅ Hash-chained integrity (prevents tampering)

### EU AI Act (Art. 50 — Bot Disclosure)
- ✅ User informed: "This video was AI-generated"
- ✅ Transparency log: which model (GPT/Claude/Opus)?
- ✅ Opt-out possible: operator can disable Video Producer plugin

### Learning Integration (ADR-0314)
- ✅ Phase 4b: Feedback collection
- ✅ Confidence scores updated (per worker, per model)
- ✅ Next run: Maestro uses updated scores

## Testing Architecture

```
tests/
├── skills/
│   ├── test_video_producer_phase1.py      (40 tests)
│   │   ├─ Quality gates
│   │   ├─ Design system
│   │   └─ Asset ingestion
│   │
│   ├── test_video_producer_workers.py     (48 tests)
│   │   ├─ SlideGenerator (12)
│   │   ├─ VoiceSynthesizer (8)
│   │   ├─ ScreenshotCapturer (6)
│   │   └─ VideoAssembler (16)
│   │
│   ├── test_video_producer_orchestrator.py (8 tests)
│   │   ├─ Phase transitions
│   │   ├─ Audit events
│   │   └─ Error recovery
│   │
│   └── test_video_producer_learning.py    (0+ tests Phase 4b)
│
├── console/
│   └── test_video_producer_api.py         (12+ API tests)
│
├── e2e/
│   ├── test_video_producer_e2e_proof.py   (E2E: assets → video)
│   └── test_video_producer_adversarial.py (Stress tests)
│
└── adversarial/
    └── test_video_producer_compliance.py  (Audit chain, GDPR, etc.)
```

## Performance Targets

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Phase 2 Analysis | <10s (small assets) | ~3s | ✅ OK |
| Storyboard Gen | <5s | ~2s | ✅ OK |
| Worker Parallel | <30s (4 workers) | ~25s | ✅ OK |
| Assembly (FFmpeg) | <60s (5min video) | ~45s | ✅ OK |
| Full Pipeline | <120s (end-to-end) | ~75s | ✅ OK |

## Deployment Checklist

- [ ] Phase 1 Week 1: Quality gates + design system (40 tests, 0 CRITICAL)
- [ ] Phase 1 Week 2: Workers + orchestrator (96 tests, 0 CRITICAL)
- [ ] Phase 2: Console API + panel (12 API + 8 UI tests)
- [ ] Phase 3: Parallel workers optimized (20 adversarial tests)
- [ ] Phase 4a: YouTube upload (async, non-blocking)
- [ ] Phase 4b: Learning loop (feedback integration)
- [ ] Staging: 2h canary (5% traffic)
- [ ] Production: 100% rollout (1h per tier)

## Knowledge Graph

**Central ADRs:**
- ADR-0692: Orchestration (this design)
- ADR-0693: Asset Analyzer Worker
- ADR-0694: Voice + Screenshot Workers
- ADR-0695: Assembler + YouTube Workers
- ADR-0690: Quality Gates (compliance)
- ADR-0691: Audit Trail Hash-Chaining

**Related Concepts:**
- CONCEPT-0040: Orchestrated Multi-Skill Pattern

**References:**
- BUILD_PROCESS.md: Design decisions (Thesis/Antithesis/Synthesis)
- PLUGIN_DEVELOPMENT_GUIDE.md: Template for future plugins

---

**Last Updated:** 2026-09-13  
**Status:** Phase 1 Week 2 IN PROGRESS  
**Next Review:** 2026-09-27 (Phase 4b adversarial)
