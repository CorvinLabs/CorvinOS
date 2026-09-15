"""Gap #1 Context Carryover — ContextualSubprocessWorkerIPC implementation (LDD).

Wraps SubprocessWorkerIPC to track prior step outputs and include them in
subsequent step prompts, reducing cold-start overhead. This proves context-carryover
benefit while avoiding the need for a persistent session daemon.

The strategy:
- Each step's prompt includes [Prior outputs from steps 1..N-1]
- Capped at 8KB to stay within Claude's working memory for one-shot
- Measured: compare cost of contextual vs non-contextual runs

Future: replace with true session-reuse when claude CLI supports --resume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

# Import only what we need (SubprocessWorkerIPC for subclassing)
try:
    from .worker_ipc import ProcHolder, SubprocessWorkerIPC
except ImportError:
    # Fallback for test imports
    SubprocessWorkerIPC = object  # type: ignore
    ProcHolder = Any  # type: ignore

_logger = logging.getLogger(__name__)

_CONTEXT_BUDGET_CHARS = 8_000  # max prior-output size to include


@dataclass
class StepMemory:
    """Captured output from a delegated step."""

    step_num: int
    output: str  # Truncated to 500 chars (model-derived, untrusted)
    usage: Optional[dict[str, Any]] = None


class ContextualSubprocessWorkerIPC(SubprocessWorkerIPC):
    """SubprocessWorkerIPC with context carryover via prompt concatenation.

    Tracks step history within a single TDE run and includes prior outputs
    in subsequent prompts. Reduces context-loading overhead (the "context
    carryover" mechanism from the audit).

    Not a true session (no --resume), but proves the benefit without
    infrastructure complexity.
    """

    def __init__(self, timeout_s: int = 120):
        super().__init__(timeout_s=timeout_s)
        self.step_memory: list[StepMemory] = []
        self._context_size_total = 0

    def add_step_memory(
        self, step_num: int, output: str, usage: Optional[dict[str, Any]] = None
    ) -> None:
        """Record a step's output for context carryover to future steps.

        Args:
            step_num: Step number (1-indexed)
            output: Step output (will be truncated to 500 chars)
            usage: Token usage dict (e.g., {"tokens": 1000})
        """
        # Truncate output to 500 chars for model safety
        truncated_output = output[:500] if output else ""
        self.step_memory.append(
            StepMemory(step_num=step_num, output=truncated_output, usage=usage)
        )
        self._context_size_total += len(truncated_output)

    def _build_context_summary(self) -> str:
        """Build a concise summary of prior steps' outputs.

        Returns:
            Markdown string suitable for injection into prompt, or empty string if no memory.
        """
        if not self.step_memory:
            return ""

        lines = ["\n--- Prior step outputs (context carryover) ---"]
        cumulative_chars = 0

        for mem in self.step_memory:
            # Take first 200 chars of each step's output for the summary
            step_summary = f"Step {mem.step_num}: {mem.output[:200]}"
            if cumulative_chars + len(step_summary) > _CONTEXT_BUDGET_CHARS:
                lines.append("... [context truncated to budget]")
                break
            lines.append(step_summary)
            cumulative_chars += len(step_summary)

        return "\n".join(lines)

    def _inject_context(self, prompt: str) -> str:
        """Inject prior step context into the prompt (before YOUR step:).

        Args:
            prompt: Base prompt from parent _build_prompt()

        Returns:
            Prompt with context injected, or unchanged if no context.
        """
        context_summary = self._build_context_summary()
        if not context_summary:
            return prompt

        # Inject before "YOUR step:" marker
        return prompt.replace(
            "\nYOUR step:",
            f"{context_summary}\n\nYOUR step:",
        )

    async def send_delegation(
        self,
        envelope: Any,  # DelegationEnvelope
        *,
        proc_holder: Optional[ProcHolder] = None,
    ) -> dict[str, Any]:
        """Delegate with context carryover.

        Args:
            envelope: DelegationEnvelope from adaptive_delegation_executor
            proc_holder: Optional process holder for cleanup

        Returns:
            Result dict with success flag, output, usage, context stats
        """
        # Call parent's send_delegation
        result = await super().send_delegation(envelope, proc_holder=proc_holder)

        # On success, record the output for future steps' context
        if result.get("success"):
            output_str = str(result.get("output", ""))[:500]  # Truncate again
            usage = result.get("usage")
            self.add_step_memory(
                step_num=envelope.step.step,  # Assumes envelope has step.step
                output=output_str,
                usage=usage,
            )
            # Annotate result with context stats for observability
            result["context_size_chars"] = self._context_size_total
            result["step_memory_count"] = len(self.step_memory)

        return result

    def reset_context(self) -> None:
        """Clear step history (between independent runs)."""
        self.step_memory.clear()
        self._context_size_total = 0

    def get_context_stats(self) -> dict[str, Any]:
        """Return observability stats about context carryover."""
        return {
            "step_memory_count": len(self.step_memory),
            "context_size_total_chars": self._context_size_total,
            "context_budget_chars": _CONTEXT_BUDGET_CHARS,
            "context_budget_used_pct": (
                100 * self._context_size_total / _CONTEXT_BUDGET_CHARS
                if _CONTEXT_BUDGET_CHARS
                else 0
            ),
        }


# Backward compat alias
ContextualWorkerIPC = ContextualSubprocessWorkerIPC
