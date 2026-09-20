"""
LLM Judge for quality grading in scientific benchmarking.

Uses Claude-Opus-5 to evaluate outputs against golden truth,
with category-specific grading rubrics.

Scores: 0=INCORRECT, 1=PARTIAL, 2=CORRECT
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
import anthropic

logger = logging.getLogger(__name__)


@dataclass
class GradeResult:
    """Result of a quality grade."""
    score: int  # 0=incorrect, 1=partial, 2=correct
    reasoning: str  # Human-readable explanation


class LLMJudge:
    """LLM-based quality judge for benchmark grading."""

    def __init__(self, model: str = "claude-opus-5"):
        self.model = model
        self.client = anthropic.Anthropic()
        self.grading_rubrics = self._build_rubrics()

    def grade(
        self,
        golden_output: str,
        candidate_output: str,
        task_category: str,
    ) -> int:
        """
        Grade a candidate output against golden truth.

        Args:
            golden_output: Reference/correct output
            candidate_output: Output to be graded
            task_category: Task category (code, data, writing, etc.)

        Returns:
            Score: 0=INCORRECT, 1=PARTIAL, 2=CORRECT
        """
        rubric = self.grading_rubrics.get(task_category, self.grading_rubrics["default"])

        prompt = f"""
You are an expert judge evaluating the quality of an AI assistant's output.

**Task Category:** {task_category}

**Grading Rubric:**
{rubric}

**Expected Output (Golden Truth):**
{golden_output}

**Candidate Output (to be graded):**
{candidate_output}

---

Based on the grading rubric and comparing the candidate output to the expected output, assign ONE of the following scores:

- **0 (INCORRECT):** Candidate output is fundamentally wrong, contains major errors, or fails to address the task
- **1 (PARTIAL):** Candidate output is partially correct, has some right elements but also notable gaps or errors
- **2 (CORRECT):** Candidate output is correct, complete, and meets or exceeds expectations

Respond with ONLY the score (0, 1, or 2) on a single line. Do not include explanation.
"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=10,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )

            # Extract score from response
            response_text = response.content[0].text.strip()

            # Parse score (should be 0, 1, or 2)
            try:
                score = int(response_text)
                if score not in (0, 1, 2):
                    logger.warning(f"LLM judge returned invalid score {score}, defaulting to 1")
                    return 1
                return score
            except ValueError:
                logger.warning(f"LLM judge returned non-integer '{response_text}', defaulting to 1")
                return 1

        except Exception as e:
            logger.error(f"LLM judge error: {e}")
            # Default to 1 (partial) on error to avoid completely discarding output
            return 1

    def _build_rubrics(self) -> Dict[str, str]:
        """Build category-specific grading rubrics."""
        return {
            "code": """
Code Writing Quality Rubric:
- CORRECT (2): Code executes without errors, implements the requested functionality, follows best practices
- PARTIAL (1): Code mostly works but has minor bugs, edge cases unhandled, or quality issues
- INCORRECT (0): Code fails to execute, implements wrong functionality, or has critical bugs
            """,

            "data": """
Data Analysis Quality Rubric:
- CORRECT (2): Query/analysis executes correctly, returns expected schema and correct results
- PARTIAL (1): Query mostly works but has edge cases or returns incomplete results
- INCORRECT (0): Query fails to execute or returns incorrect results
            """,

            "writing": """
Writing/Documentation Quality Rubric:
- CORRECT (2): Writing is clear, complete, factually accurate, well-organized
- PARTIAL (1): Writing covers main points but has gaps, minor inaccuracies, or organizational issues
- INCORRECT (0): Writing is incomplete, contains significant errors, or fails to address the task
            """,

            "debugging": """
Debugging/Analysis Quality Rubric:
- CORRECT (2): Correctly identifies the bug/issue, proposes a sound fix that solves the problem
- PARTIAL (1): Identifies some aspects of the issue but misses root cause or fix is incomplete
- INCORRECT (0): Fails to identify the real issue or proposes an incorrect/harmful fix
            """,

            "qa": """
Q&A/Reasoning Quality Rubric:
- CORRECT (2): Answer is factually accurate, reasoning is sound, explanation is complete
- PARTIAL (1): Answer has some correct elements but reasoning has gaps or minor inaccuracies
- INCORRECT (0): Answer is incorrect or reasoning is fundamentally flawed
            """,

            "summarization": """
Summarization/Extraction Quality Rubric:
- CORRECT (2): Summary/extraction is accurate, captures all key points, properly structured
- PARTIAL (1): Summary has most key points but misses some details or structure is imperfect
- INCORRECT (0): Summary is incomplete, misses key points, or contains significant errors
            """,

            "default": """
General Quality Rubric:
- CORRECT (2): Output fully addresses the task, is accurate, and meets expectations
- PARTIAL (1): Output partially addresses the task or has minor issues
- INCORRECT (0): Output fails to address the task or contains major errors
            """,
        }
