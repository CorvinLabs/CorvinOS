"""Phase 2: LLM-based full skill generation (with fallback)."""

from .manifest import SkillManifest, SkillType, SkillScope
from .skeleton import SkeletonGenerator
import json
from typing import Optional, Tuple

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


class LLMGenerator:
    """Generate full skill body via Claude (Phase 2, with fallback)."""

    def __init__(self, api_key: Optional[str] = None):
        self.has_anthropic = HAS_ANTHROPIC
        if HAS_ANTHROPIC:
            self.client = anthropic.Anthropic(api_key=api_key)
        else:
            self.client = None
        self.model = "claude-opus-5"
        self.fallback = SkeletonGenerator()

    def generate(self, manifest: SkillManifest, max_retries: int = 2) -> Tuple[SkillManifest, bool]:
        """Generate full skill body. Returns (manifest, is_llm_generated)."""

        if not self.has_anthropic:
            # Fallback to skeleton if anthropic not available
            return self.fallback.generate(manifest.name, manifest.title, manifest.description, manifest.scope), False

        # Try LLM first
        for attempt in range(max_retries):
            try:
                full_body = self._call_llm(manifest)
                manifest.body_md = full_body
                manifest.generator_phase = "full"
                return manifest, True
            except Exception as e:
                if attempt == max_retries - 1:
                    # Fallback to skeleton
                    return self.fallback.generate(manifest.name, manifest.title, manifest.description, manifest.scope), False

        # Unreachable, but satisfy type checker
        return manifest, False

    def _call_llm(self, manifest: SkillManifest) -> str:
        """Call Claude to expand skeleton into full skill."""

        prompt = f"""Expand this skill skeleton into a complete, production-ready skill:

**Type:** {manifest.skill_type.value}
**Title:** {manifest.title}
**Description:** {manifest.description}

**Skeleton:**
{manifest.body_md}

Requirements:
1. Keep the structure but expand each section with concrete content
2. For learned-experience: Add 2–3 real-world examples and case studies
3. For reasoning: Add nuanced arguments, edge cases, trade-offs
4. For reference: Complete the API table, add error codes, examples
5. For automation: Add pseudocode or algorithm steps, input/output specs
6. Total: 1500–2500 words
7. Markdown formatting only (no HTML, no code blocks >80 chars unless necessary)
8. No meta-commentary, no "TODO" placeholders
9. Fail-closed: if uncertain, be conservative and concrete

Return ONLY the expanded markdown, no preamble."""

        message = self.client.messages.create(
            model=self.model,
            max_tokens=3000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return message.content[0].text


def enhance_with_llm(manifest: SkillManifest, use_llm: bool = True) -> Tuple[SkillManifest, bool]:
    """Enhance manifest: LLM if enabled and available, fallback to skeleton."""
    if not use_llm:
        skeleton_gen = SkeletonGenerator(manifest.skill_type)
        return skeleton_gen.generate(manifest.name, manifest.title, manifest.description, manifest.scope), False

    llm_gen = LLMGenerator()
    return llm_gen.generate(manifest)
