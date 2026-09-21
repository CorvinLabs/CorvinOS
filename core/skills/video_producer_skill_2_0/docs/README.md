# Video Producer Skill 2.0 — LLM Synthesis

Professional video generation plugin with **LLM-driven spec generation**.

## Features

✅ **LLM-Powered Specs:** Natural language → video specification (JSON)  
✅ **Multi-Renderer Support:** Slide, SVG, Charts, Blender 3D, Screencast  
✅ **Fail-Closed Validation:** Invalid specs rejected immediately  
✅ **Immutable Specs:** Hashes for auditability and caching  
✅ **German/English Support:** Multilingual narration via OpenAI TTS  
✅ **Professional Quality:** 1920×1080 Full HD @ 30 FPS  

## Quick Start

```python
from core.skills.video_producer_skill_2_0.llm_synthesis import SpecGenerator

# Generate video spec from brief
generator = SpecGenerator()
spec = generator.generate_spec(
    brief="Create a 60-second ACS Runner demo",
    duration_sec=60,
    language="de",
    tone="professional",
)

# Spec is validated and immutable
print(f"✅ Spec: {len(spec.scenes)} scenes, {spec.video_metadata.duration_seconds}s")
```

## Architecture

### Phase 1: Spec Generation (Complete ✅)

```
llm_synthesis/
├── spec_schema.py         # Pydantic VideoSpec (immutable)
├── spec_validator.py      # Fail-closed validation
├── spec_generator.py      # LLM integration (Claude Opus)
├── spec_cache.py          # Memoization by request_hash
└── __init__.py            # Module exports
```

### Phase 2: Tool Execution (Planned)

```
renderers/
├── frame_renderer.py      # PIL frame rendering
├── svg_renderer.py        # SVG diagram rendering
├── blender_renderer.py    # Blender 3D animation
├── audio_renderer.py      # OpenAI TTS
└── composition.py         # FFmpeg video merge
```

### Phase 3: API & Marketplace (Planned)

```
routes/
├── llm_synthesis.py       # POST /v1/video/llm-generate
└── quality_feedback.py    # Quality scoring integration

extension_points.py        # Plugin override interface
```

## Documentation

- **[ADR-0955](./ADR-0955-llm-synthesis.md)** — Architecture decision (plugin-autonomous)
- **[IMPLEMENTATION-PLAN.md](./IMPLEMENTATION-PLAN.md)** — 6-week roadmap + adversarial review
- **[CONCEPT-0051](../../..​/Corvin-ADR/concepts/CONCEPT-0051-llm-driven-video-synthesis.md)** — Working method (core concept)

## Validation Strategy

### Fail-Closed Checks

✅ **JSON Schema:** Pydantic validation (structure)  
✅ **Semantic:** Narration timing ±2s, duration ±5s, file existence  
✅ **Uniqueness:** Scene IDs non-duplicated  
✅ **Completeness:** All fields populated  

Invalid spec → `SpecValidationError` (fail-closed, no silent fallback)

## Roadmap

| Phase | Timeline | Deliverable | Status |
|---|---|---|---|
| 1 | Weeks 1–2 | Spec generation + validation | ✅ **COMPLETE** |
| 2 | Weeks 3–4 | Tool execution (PIL, SVG, Blender, TTS, FFmpeg) | 🔲 Planned |
| 3 | Weeks 5–6 | HTTP API + marketplace integration | 🔲 Planned |

## Testing

```bash
# Phase 1: Spec generation
pytest tests/test_llm_synthesis_phase1.py -v

# E2E: Brief → Spec → Validation
pytest tests/e2e/test_llm_spec_generation.py -v

# Phase 2: Full pipeline (when ready)
pytest tests/e2e/test_full_pipeline_phase2.py -v
```

## Security & Compliance

- **Plugin-Autonomous:** No dependency on core CorvinOS
- **Immutable Specs:** `frozen=True` in Pydantic (spec cannot be modified after creation)
- **Auditable:** `prompt_hash` + `request_hash` for traceability
- **Fail-Closed:** Invalid specs rejected (no recovery)

## Integration with Video Producer

Video Producer users can:
1. Use high-level LLM endpoint: `POST /v1/video/llm-generate`
2. Or manually craft VideoSpec JSON (if preferred)
3. Or extend via marketplace plugins (custom prompt, model, renderers)

## Contributing

Plugin contributors should:
1. Extend via **extension points** (don't fork)
2. Validate new renderers against `spec_schema.py`
3. Add tests (unit + E2E) for new features
4. Update this README + ADR-0955 as needed

## License

Same as CorvinOS (Apache-2.0 + CLA v3.1)

---

**Status:** Phase 1 Complete, Ready for Phase 2  
**Plugin ID:** `video_producer_skill_2_0`  
**Scope:** Self-contained (plugin-autonomous)  
**Maintainer:** Video Producer team
