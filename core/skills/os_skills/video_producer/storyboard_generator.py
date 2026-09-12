"""Storyboard Generator: LLM-based constrained storyboard creation.

Constraints:
- Narration ONLY references facts from analysis.factual_claims
- No invention of new claims
- Asset roles guide scene composition
"""

from __future__ import annotations

from typing import Optional, Any

from .types import AssetAnalysisResult, Storyboard, Scene


class StoryboardGenerator:
    """Generate source-constrained storyboard from analysis."""

    def __init__(self):
        """Initialize generator."""
        pass

    async def generate(
        self,
        analysis: AssetAnalysisResult,
    ) -> Optional[Storyboard]:
        """
        Generate storyboard constrained to analysis.factual_claims.

        Args:
            analysis: AssetAnalysisResult from asset_analyzer

        Returns:
            Storyboard with scenes, or None if generation fails
        """
        # TODO: Call LLM with source-constrained prompt
        # Prompt must explicitly state:
        # - Use ONLY claims from analysis.factual_claims
        # - Reference asset_roles for each scene
        # - No invention
        return None

    def _build_source_constraint_prompt(
        self,
        analysis: AssetAnalysisResult,
    ) -> str:
        """Build LLM prompt that enforces source constraint."""
        claims_json = "\n".join([
            f"- {claim.text} (from {claim.source_asset}, confidence: {claim.confidence})"
            for claim in analysis.factual_claims
        ])

        roles_json = "\n".join([
            f"- {asset}: {role}"
            for asset, role in analysis.asset_roles.items()
        ])

        return f"""Based on these factual claims:
{claims_json}

And these asset roles:
{roles_json}

Generate a video storyboard where:
1. Every narration sentence references ONLY the claims above
2. Scenes are ordered to tell a coherent story
3. Each scene references the appropriate asset
4. Do NOT invent new claims or facts
5. Do NOT add information not in the claims list

Format: JSON with scenes array, each having:
{{
  "id": "s01",
  "kind": "card",  // card, screencast, slide
  "narration": "...",  // sourced from claims only
  "source_asset": "...",  // from asset_roles
  "duration_seconds": null  // will be measured
}}
"""
