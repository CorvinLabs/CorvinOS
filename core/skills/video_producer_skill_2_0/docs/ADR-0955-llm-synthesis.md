---
id: ADR-0955-VIDEO-PRODUCER
status: proposed
plugin: video_producer_skill_2_0
scope: plugin-autonomous
depends_on: []
related: [CONCEPT-0051]
commits: []
paths:
  - core/skills/video_producer_skill_2_0/llm_synthesis/
docs:
  - core/skills/video_producer_skill_2_0/docs/
---

# ADR-0955 — Video Producer LLM Synthesis Architecture

**Scope:** Video Producer Plugin (self-contained, plugin-autonomous)  
**Status:** Proposed  
**Date:** 2026-09-21

---

## Context

**Problem:** Video generation in Video Producer is manual:
- Users write rendering code by hand
- No high-level abstraction for complex videos
- Difficult to reuse patterns
- Requires deep knowledge of PIL, FFmpeg, Blender APIs

**Opportunity:** CONCEPT-0051 demonstrates LLM-generated specs + tool execution is 3x faster than manual.

**Decision:** Build LLM video synthesis **self-contained in Video Producer plugin** (not in core CorvinOS).

---

## Conceptual Level

Separate **specification** (what to render) from **execution** (how to render):

```
User Request → LLM Spec Generation → Tool Execution → MP4
```

Video spec is tool-neutral, immutable, auditable (within plugin scope).

---

## Structural Level

**Three modules within plugin:**

```
core/skills/video_producer_skill_2_0/llm_synthesis/
├── spec_schema.py         # Pydantic VideoSpec
├── spec_validator.py      # Fail-closed validation
├── spec_generator.py      # LLM → spec
└── spec_cache.py          # Memoization
```

**Execution (Phase 2):**
```
renderers/
├── frame_renderer.py      # PIL frames
├── svg_renderer.py        # SVG → PNG
├── blender_renderer.py    # Blender automation
├── audio_renderer.py      # OpenAI TTS
└── composition.py         # FFmpeg merge
```

---

## Implementation Level

### Video Spec Schema (Immutable)

```json
{
  "spec_version": "1.0",
  "generation_metadata": {
    "llm_model": "claude-opus-5",
    "prompt_hash": "...",
    "request_hash": "..."
  },
  "video_metadata": {
    "duration_seconds": 60,
    "fps": 30,
    "language": "de"
  },
  "scenes": [
    {
      "id": "intro",
      "type": "slide",
      "narration": "...",
      "narration_duration_ms": 8500
    }
  ]
}
```

### LLM Prompt Template

- Claude Opus 5 (temperature 0.3)
- Narration in user's language (German/English/etc.)
- Fail-closed validation (schema + semantic checks)

### Fail-Closed Validation

- Narration duration ±2 seconds
- Total duration ±5 seconds
- Blender files exist (if type=blender_render)
- Scene IDs unique
- Invalid spec → Exception (no silent fallback)

---

## Decision

**Adopt LLM Video Synthesis as self-contained Video Producer feature:**

✅ Plugin-autonomous (not core CorvinOS concern)  
✅ Reusable across all Video Producer users  
✅ Immutable specs (auditable within plugin scope)  
✅ Fail-closed validation  
✅ Extensible via plugin override points  

---

## Timeline

- **Phase 1 (Weeks 1–2):** Spec generation + validation (1,100 LOC)
- **Phase 2 (Weeks 3–4):** Tool execution (1,650 LOC)
- **Phase 3 (Weeks 5–6):** HTTP API + quality scoring (1,030 LOC)

**Total:** 6 weeks, 2,200 LOC, plugin-ready.

