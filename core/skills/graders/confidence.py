"""Confidence Skill Grader — LLM-based evaluation (ADR-0307)."""

from __future__ import annotations

import re
from typing import Any

from core.skills.skill import Grade


class ConfidenceGrader:
    """LLM-based skill grader using Claude to evaluate skill outputs.

    Uses anthropic.Anthropic client to grade skill outputs on 0.0–1.0 scale.
    Parses Claude's response to extract numeric confidence score.
    """

    def __init__(self, api_key: str | None = None, model: str = "claude-opus-5"):
        """Initialize confidence grader.

        Args:
            api_key: Anthropic API key (optional, uses env var if not provided)
            model: Claude model ID (default: claude-opus-5)
        """
        self.model = model
        self.api_key = api_key
        self.client = None

        try:
            import anthropic

            self.client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            pass  # Client init deferred until grade() call

    async def grade(self, request: dict[str, Any]) -> Grade | None:
        """Grade skill output using Claude.

        Args:
            request: Invocation metadata with:
                - skill_name: str
                - output: str (skill output to evaluate)
                - exception: str | None (if raised)

        Returns:
            Grade(value=0.0–1.0, feedback=Claude's response) or None on failure.
        """
        # Lazy import to avoid hard dependency
        try:
            import anthropic
        except ImportError:
            return None

        if not self.client:
            try:
                self.client = anthropic.Anthropic(api_key=self.api_key)
            except Exception:
                return None

        skill_name = request.get("skill_name", "unknown")
        output = request.get("output", "")
        exception = request.get("exception")

        # Build grading prompt
        if exception:
            prompt = (
                f"The skill '{skill_name}' raised {exception}. "
                "Rate this skill's reliability on a 0.0–1.0 scale "
                "(0 = completely failed, 1 = perfect). Respond with a single float."
            )
        else:
            prompt = (
                f"Evaluate the output of skill '{skill_name}':\n\n{output}\n\n"
                "Rate this skill's output quality on a 0.0–1.0 scale "
                "(0 = useless, 1 = excellent). Respond with a single float."
            )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = response.content[0].text.strip()

            # Parse float from response
            score = self._parse_score(response_text)
            if score is not None:
                return Grade(value=score, feedback=response_text)
            else:
                # Fallback if parsing fails
                return Grade(value=0.5, feedback=f"Could not parse: {response_text}")
        except Exception as e:
            # Return neutral grade on API failure
            return Grade(value=0.5, feedback=f"Grading error: {type(e).__name__}")

    @staticmethod
    def _parse_score(response: str) -> float | None:
        """Parse numeric score from Claude's response.

        Tries to extract a float between 0.0 and 1.0 from the response.

        Returns:
            Float 0.0–1.0, or None if parsing fails.
        """
        # Try to find a float pattern
        matches = re.findall(r"0\.\d+|1\.0|1", response)
        if matches:
            try:
                score = float(matches[0])
                # Clamp to [0.0, 1.0]
                return max(0.0, min(1.0, score))
            except ValueError:
                pass
        return None
