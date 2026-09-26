---
type: plugin-readme
status: Phase 1 Week 2 (IN PROGRESS)
last_updated: 2026-09-26
---

# Video Producer Skill 2.0

**Orchestrated Video Production from PowerPoints** — High-quality, branded, production-ready videos via LLM-guided storyboarding + parallel worker orchestration.

## Quick Start

```python
from video_producer import VideoProducerOrchestrator

# Initialize orchestrator
orchestrator = VideoProducerOrchestrator("./my_project")

# Produce video (7-phase pipeline)
result = await orchestrator.orchestrate(
    asset_paths=["presentation.pptx", "logo.png"],
    instructions={"style": "corporate", "duration": "3-5 minutes"},
)

# Output
print(result)
# {
#   "status": "success",
#   "storyboard": {...},
#   "video_path": "./my_project/video.mp4",
#   "youtube_task_id": "...",
#   "message": "✅ Video ready (3min 42s, 1280x720p)"
# }
```

## Consolidation status (2026-09-26) -- know which implementation you're touching

This directory (plus two sibling trees) used to hold FIVE separate,
non-communicating video-production code paths, discovered and consolidated
over two sessions:

| Implementation | Entry point | Status |
|---|---|---|
| **PPT/slide pipeline** (this README's own Quick Start, below) | `orchestrator.py::VideoProducerOrchestrator` + `core/skills/workers/*` | **The canonical path.** `EventEmitter`/`TaskManager` construction bugs fixed across all 5 workers, video assembly now has real ffmpeg `-i` wiring + a real multi-scene filter graph (was a discarded-then-hardcoded stub), voice synthesis is real edge-tts (was silence), YouTube upload is honest local-only mode (was a hardcoded fake video id). `_generate_storyboard` (the LLM call) is still a placeholder -- the one open stub in this path. |
| **Skill Forge v2.0 MVP** | `maestro.py::VideoProducerMaestro` | **Deprecated** (`DeprecationWarning` on construction) -- Phases 1-3 only, stubbed, no unique capability over the canonical path. Kept in place, not deleted, pending a release cycle with no external callers found. |
| **Blender headless render path** | `blender_orchestrator.py::BlenderHeadlessOrchestrator`, driven via `blender_cli.py` | Real, E2E-proven, autonomously runnable (see below) -- a Blender-specific enhancement layer, not a competing pipeline. |
| **Generic topic->video generator** (`core/skills/video_producer/`, a *different* package from this one) | `maestro.py::MaestroOrchestrator` | Not part of this consolidation; reachable only from demo scripts, not any live API. Its real (non-mocked) espeak-ng TTS and Playwright screenshot logic were the model for this session's edge-tts port, but the package itself was left alone. |
| **Director Mode** (`src/director/` here + a second independent copy at `core/skills/director_mode/`) | narrative/visual/pacing optimizers | **Deleted**, both copies (2026-09-26). Zero production call sites ever existed -- only each copy's own test suite exercised it. ADR-0696 claimed `status: IMPLEMENTED`; corrected to `superseded` in the same change. |

The console's live video-producer API (`core/console/corvin_console/routes/video_producer_api.py`)
still resolves a SIXTH, separately-versioned implementation from the
Corvin-Marketplace plugin repo via `importlib` -- that one is what an
operator using the console UI actually reaches today. It was deliberately
**not** touched this session: it already solves job persistence,
async progress tracking and console reachability (models.py/storage.py/
async_runner.py) that the canonical path doesn't attempt, and replacing it
outright would have meant rebuilding that whole layer from scratch rather
than finishing already-declared work. The console's `VideoProducerPage`
panel itself was dead UI until this session (imported in `registry.tsx` but
never routed, no `NAV_GROUPS` entry) -- now wired, though unverified: this
environment has no Node.js, so the frontend change is source-correct but
**not build/runtime-proven** (see CLAUDE.md's console-frontend proof
requirements, which this explicitly could not satisfy).

If the task is "render a Blender scene into a video," use `blender_cli.py`.
For everything else (PPT ingestion, TTS, assembly, upload), use
`orchestrator.py`.

### The Blender path: real, working, autonomously runnable

```bash
python3 -m core.skills.os_skills.video_producer.blender_cli \
    --blend tests/fixtures/video_producer/simple_scene.blend \
    --output /tmp/my_video.mp4
```

fps/frame range/resolution are read from the `.blend` file itself (a real,
separate headless Blender subprocess probe) rather than guessed. The fixture
at `tests/fixtures/video_producer/simple_scene.blend` (built by
`tests/fixtures/video_producer/build_fixture_scene.py`) bakes a rotating cube
plus a 2s sine-tone narration track, so a fresh render always produces a real
h264 video stream + a real AAC audio stream, verifiable independently with
`ffprobe`. Real E2E tests (no mocks of `subprocess`/`bpy`/`ffmpeg`) live in
`tests/skills/video_producer/test_blender_orchestrator_e2e.py` and
`test_blender_cli_e2e.py`.

`scripts/phase0_video_producer_roadmap.sh`'s references to a prior
`Corvin-Videos/blender_20260922_*` artifact directory are **stale** -- that
directory does not exist on this install; the fixture + CLI above replace it
as the way to produce and verify a Blender render.

## Architecture

**7-Phase Pipeline (PPT/slide implementation only -- see table above):**
1. **Asset Ingestion** — Copy files, organize by type
2. **Deep Analysis** — Extract structure, visuals, metadata (ADR-0693)
3. **Storyboard Generation** — LLM-constrained to analysis facts
4. **Parallel Workers** — Slides + Audio + Screenshots (ADR-0694)
5. **Video Assembly** — FFmpeg orchestration (ADR-0695)
6. **YouTube Upload** — Async, non-blocking upload. **Local-only by default**:
   uploading to YouTube requires an operator-provisioned Google Cloud OAuth2
   client (own console project, consent screen, a stored token at
   `CORVIN_YOUTUBE_TOKEN_PATH`) — an agent/installer cannot provision that.
   Without it, the pipeline still produces the final MP4 + generated
   title/description/tags locally; `enqueue_upload`/`upload_video` report
   `status: "not_configured"` with a `reason`, never a fabricated video id
   (until 2026-09-26 this always returned a hardcoded fake id). See
   `core/skills/workers/youtube_uploader/youtube_api.py`'s module docstring.
7. **Feedback Collection** — Operator feedback → learning (Phase 4b)

**See Also:**
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — System overview
- [BUILD_PROCESS.md](docs/BUILD_PROCESS.md) — Design decisions (Thesis/Antithesis/Synthesis)
- [PLUGIN_DEVELOPMENT_GUIDE.md](docs/PLUGIN_DEVELOPMENT_GUIDE.md) — Template for future plugins

## Design Principles

### ✅ Quality Gates (Hardcoded, Fail-Closed)
```json
{
  "DRAFT": {"clarity_min": 70, "audio_quality_min": 60},
  "PRODUCTION": {"clarity_min": 85, "audio_quality_min": 80},
  "BROADCAST": {"clarity_min": 95, "audio_quality_min": 95}
}
```
See [ADR-0690: Quality Gates](docs/adrs/ADR-0690-quality-gates.md)

### ✅ Audit Trail (Hash-Chained)
Every phase transition logged + verified:
```json
{
  "event_type": "phase_transition",
  "phase": 4,
  "worker": "VideoAssembler",
  "hash": "sha256(...)",
  "prev_hash": "sha256(...)",  // Linked to phase 3
  "status": "success"
}
```
See [ADR-0691: Audit Trail](docs/adrs/ADR-0691-audit-trail.md)

### ✅ Design System (Locked, Immutable)
All videos use project-specific design_system.json (v1.0.0):
- Fonts, colors, logos
- Quality thresholds
- Video specs (resolution, bitrate)

See [design_system.json](design_system.json)

### ✅ Deterministic Workers
Same input → Same output (bit-for-bit):
- No random numbers (use seeds)
- No external API randomness (cache responses)
- Reproducible for audit trail verification

### ✅ Maestro + Precondition Decorators
Workers declare preconditions, Maestro enforces:
```python
@require_precondition("scene.visuals != None")
async def generate_slides(self, scene: Scene):
    # Maestro checks precondition, decides skip/retry/abort
    pass
```
See [ADR-0692: Orchestration](docs/adrs/ADR-0692-orchestration.md)

## Development

### Installation
```bash
pip install -e .
```

### Testing
```bash
# Unit tests (all phases)
pytest tests/skills/test_video_producer_*.py -v

# E2E proof (wiring verification)
pytest tests/e2e/test_video_producer_e2e_proof.py -v

# Adversarial review (stress test)
pytest tests/adversarial/test_video_producer_compliance.py -v

# All tests
pytest tests/ -k video_producer -v
```

### Code Coverage
```bash
pytest tests/ -k video_producer --cov=core/skills/os_skills/video_producer
```

**Target:** ≥85% coverage

### Running Locally
```bash
# Create test project
python -c "from video_producer.tests import setup_test_project; setup_test_project('./test_video')"

# Run orchestrator
python -c "
import asyncio
from video_producer import VideoProducerOrchestrator

async def main():
    orch = VideoProducerOrchestrator('./test_video')
    result = await orch.orchestrate(
        asset_paths=['./test_video/assets/sample.pptx'],
        instructions={'style': 'corporate'}
    )
    print(result)

asyncio.run(main())
"

# Check output
ls -la ./test_video/video.mp4
```

## Compliance

### ✅ GDPR (Art. 30 — Accountability)
- Audit trail proves every decision
- Tenant scoping (audit events have tenant_id)
- Hash-chained integrity (prevents tampering)

### ✅ EU AI Act (Art. 50 — Bot Disclosure)
- User informed: "This video was AI-generated"
- Transparency log: which model (GPT/Claude/Opus)?
- Opt-out possible: disable Video Producer plugin

### ✅ Learning Integration (ADR-0314)
- Phase 4b: Operator feedback collection
- Confidence scores updated per worker
- Next run: Maestro uses updated scores

## ADRs (Embedded in Plugin)

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-0692](docs/adrs/ADR-0692-orchestration.md) | Orchestration Architecture | ✅ ACCEPTED |
| [ADR-0693](docs/adrs/ADR-0693-asset-analyzer.md) | Asset Analyzer Worker | ✅ ACCEPTED |
| [ADR-0694](docs/adrs/ADR-0694-workers.md) | Voice + Screenshot Workers | ✅ ACCEPTED |
| [ADR-0695](docs/adrs/ADR-0695-assembly.md) | Assembler + YouTube Workers | ✅ ACCEPTED |
| [ADR-0690](docs/adrs/ADR-0690-quality-gates.md) | Quality Gates | ✅ ACCEPTED |
| [ADR-0691](docs/adrs/ADR-0691-audit-trail.md) | Audit Trail Hash-Chaining | ✅ ACCEPTED |

## Roadmap

### ✅ Phase 1 Week 1: Foundation
- Quality gates (hardcoded, fail-closed)
- Design system (locked, immutable)
- Asset ingestion + analysis

### 🔄 Phase 1 Week 2: Workers + Orchestration (IN PROGRESS)
- SlideGenerator Worker (300 LOC)
- AudioGenerator Worker (200 LOC)
- VideoAssembler Worker (500 LOC)
- Maestro Orchestrator (200 LOC)
- 56+ tests (96 total, 0 CRITICAL)

### 📋 Phase 2: Console API + Panel
- 4 REST endpoints (create, status, feedback, list)
- React console panel (Vibe integration)
- 12 API tests + 8 UI tests

### 📋 Phase 3: Parallel Workers Optimized
- Worker execution profiling
- Timeout handling
- Error recovery

### 📋 Phase 4a: YouTube Integration
- Async upload (non-blocking)
- Webhook callback for completion
- Task API integration

### 📋 Phase 4b: Learning Loops
- Feedback collection (operator)
- Confidence scoring (per worker, per model)
- Model selection optimizer

### 📋 Phase 5: Dashboard
- Live orchestration view
- Quality metrics display
- Cost breakdown

### 📋 Phase 6: Marketplace Publish
- Plugin discoverable in index v3
- Design system tunable (without code changes)
- Learning integration visible

### 📋 Phase 7: Scaling
- Multi-tenant support
- Webhook triggers
- Scheduled runs (cron)

## FAQ

**Q: How do I customize fonts/colors?**
A: Edit `design_system.json` (v1.0.0 locked). To use a different design, create new project + new design_system.json v2.0.0.

**Q: What if a worker fails?**
A: Maestro retries up to 3 times, then aborts job. Failure logged in audit trail. Operator can tune retry behavior via feedback loop (Phase 4b).

**Q: How do I add a new worker?**
A: Implement the Worker interface + add to `src/workers/`, update Orchestrator._phase_4_workers(), add tests. Must have `@require_precondition` decorator + audit logging.

**Q: Is the video output deterministic?**
A: Yes (bit-for-bit identical given same inputs + design_system.json). Required for audit trail reproducibility.

**Q: Can I use custom fonts?**
A: Yes, add to `design_system.json` assets + `fonts/` folder. Fonts are locked per design_system.json version.

**Q: What quality tiers should I use?**
A: DRAFT (quick review, 70% clarity) → PRODUCTION (standard, 85% clarity) → BROADCAST (TV/cinema, 95% clarity). See [ADR-0690](docs/adrs/ADR-0690-quality-gates.md).

## Support

- **Issues:** GitHub Issues (this repo)
- **Design Docs:** [docs/](docs/) folder
- **Compliance:** See [ARCHITECTURE.md](docs/ARCHITECTURE.md#compliance-integration)
- **Learning:** See [BUILD_PROCESS.md](docs/BUILD_PROCESS.md) (how it was designed)

## License

Apache 2.0 — See [LICENSE](../../../../../../LICENSE)

## Attribution

- **Designed:** 2026-09-12 (ADR-0692/0693/0694/0695)
- **Phase 1 Week 1:** 2026-09-12 (Foundation + Quality Gates)
- **Phase 1 Week 2:** 2026-09-13 (Workers + Orchestration, IN PROGRESS)
- **Lead:** Claude Haiku 4.5

---

**Status:** Phase 1 Week 2 (IN PROGRESS — 96+ tests, 0 CRITICAL)  
**Next Update:** 2026-09-27 (Phase 4b adversarial review)  
**Stable Version:** v1.0.0 (after Phase 1 complete)
