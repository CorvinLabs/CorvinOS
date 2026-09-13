---
type: plugin-readme
status: Phase 1 Week 2 (IN PROGRESS)
last_updated: 2026-09-13
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

## Architecture

**7-Phase Pipeline:**
1. **Asset Ingestion** — Copy files, organize by type
2. **Deep Analysis** — Extract structure, visuals, metadata (ADR-0693)
3. **Storyboard Generation** — LLM-constrained to analysis facts
4. **Parallel Workers** — Slides + Audio + Screenshots (ADR-0694)
5. **Video Assembly** — FFmpeg orchestration (ADR-0695)
6. **YouTube Upload** — Async, non-blocking upload
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
