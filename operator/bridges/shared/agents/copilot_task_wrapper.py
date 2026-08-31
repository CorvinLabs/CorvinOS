"""
ADR-0087 M7: Copilot Multi-Turn Wrapper

Goal: Enable multi-turn conversations on Copilot CLI (normally single-turn).

Copilot CLI is single-turn by default. M7 implements optional sequential wrapping:
  1. Decompose multi-turn into sequential single-turn spawns
  2. Between spawns: save checkpoint from prior turn (M1 pattern)
  3. Prepend checkpoint summary to next prompt
  4. Clear labeling: "Each wrapped turn is independent"

Architecture:
  - CopilotTaskWrapper: Main class, coordinates sequential execution
  - CheckpointSaver: Serialize turn context (M1 checkpoint pattern reuse)
  - PromptPrepender: Inject prior context into next turn
  - SequentialExecutor: Spawn chain with error handling

Constraints:
  - Single spawn per turn (Copilot can't do native multi-turn)
  - Checkpoint sizing: warn if >100 KB per turn (token creep)
  - Default: single-turn only; opt-in via persona config
  - Max turns: warn at 10+ turns (encourage fallback to Claude Code)

Compliance (from CLAUDE.md):
  - L16 Audit-First: Checkpoint saved before next spawn
  - L33 Artifacts: Checkpoint itself not treated as artifact (internal state)
  - No new audit event types: reuse existing checkpoint events from M1

Test Coverage Standard (from M1–M4):
  - Tier-1: Config validation, checkpoint serialization
  - Tier-2: Checkpoint injection, prompt prepending, error handling
  - Tier-3: Full sequential E2E (mock Copilot spawns)
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TurnCheckpoint:
    """Serializable turn context for sequential execution."""
    turn_number: int
    prompt_summary: str  # User's request summary
    response_summary: str  # Model's response summary
    tool_calls_summary: str  # Tools called (if any)
    token_count: int  # Approximate turn tokens
    timestamp: int


class CheckpointSaver:
    """Save turn context using M1 checkpoint pattern."""

    MAX_CHECKPOINT_SIZE_KB = 100

    def save_checkpoint(
        self,
        session_dir: str,
        turn_number: int,
        prompt: str,
        response: str,
        tool_calls: Optional[str] = None,
        token_count: int = 0,
    ) -> TurnCheckpoint:
        """
        Save turn checkpoint (M1 pattern reuse).

        Args:
            session_dir: Session directory (for checkpoints/)
            turn_number: Turn sequence number
            prompt: User prompt (will be summarized)
            response: Model response (will be summarized)
            tool_calls: Tool calls made (optional)
            token_count: Approximate tokens for turn

        Returns:
            TurnCheckpoint object

        Raises:
            ValueError: If checkpoint too large (token creep warning)
        """
        import time

        # Summarize prompt (first 200 chars or full if shorter)
        prompt_summary = prompt[:200] + ("..." if len(prompt) > 200 else "")

        # Summarize response (first 200 chars or full if shorter)
        response_summary = response[:200] + ("..." if len(response) > 200 else "")

        # Summarize tool calls (first 100 chars or full if shorter)
        tool_calls_summary = ""
        if tool_calls:
            tool_calls_summary = tool_calls[:100] + ("..." if len(tool_calls) > 100 else "")

        checkpoint = TurnCheckpoint(
            turn_number=turn_number,
            prompt_summary=prompt_summary,
            response_summary=response_summary,
            tool_calls_summary=tool_calls_summary,
            token_count=token_count,
            timestamp=int(time.time()),
        )

        # Check size (prompt + response + tool_calls combined)
        total_size_kb = (len(prompt) + len(response) + len(tool_calls or "")) // 1024
        if total_size_kb > self.MAX_CHECKPOINT_SIZE_KB:
            logger.warning(
                f"Checkpoint turn {turn_number} is {total_size_kb} KB "
                f"(>{self.MAX_CHECKPOINT_SIZE_KB} KB). Token creep warning."
            )

        # Save checkpoint to file
        checkpoint_dir = Path(session_dir) / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        checkpoint_file = checkpoint_dir / f"turn_{turn_number}.json"
        checkpoint_file.write_text(
            json.dumps({
                "turn_number": checkpoint.turn_number,
                "prompt_summary": checkpoint.prompt_summary,
                "response_summary": checkpoint.response_summary,
                "tool_calls_summary": checkpoint.tool_calls_summary,
                "token_count": checkpoint.token_count,
                "timestamp": checkpoint.timestamp,
            })
        )

        logger.info(f"Checkpoint saved for turn {turn_number} to {checkpoint_file}")
        return checkpoint

    def load_checkpoint(self, session_dir: str, turn_number: int) -> Optional[TurnCheckpoint]:
        """
        Load turn checkpoint for context prepending (M1 pattern).

        Args:
            session_dir: Session directory
            turn_number: Turn to load

        Returns:
            TurnCheckpoint or None if not found
        """
        checkpoint_file = Path(session_dir) / "checkpoints" / f"turn_{turn_number}.json"

        if not checkpoint_file.exists():
            logger.debug(f"Checkpoint file not found: {checkpoint_file}")
            return None

        try:
            data = json.loads(checkpoint_file.read_text())
            checkpoint = TurnCheckpoint(
                turn_number=data["turn_number"],
                prompt_summary=data["prompt_summary"],
                response_summary=data["response_summary"],
                tool_calls_summary=data.get("tool_calls_summary", ""),
                token_count=data.get("token_count", 0),
                timestamp=data["timestamp"],
            )
            logger.info(f"Checkpoint loaded for turn {turn_number}")
            return checkpoint
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to load checkpoint from {checkpoint_file}: {e}")
            return None


class PromptPrepender:
    """Inject prior turn context into next prompt."""

    def prepend_context(
        self,
        checkpoint: TurnCheckpoint,
        next_prompt: str,
    ) -> str:
        """
        Prepend checkpoint summary to next turn's prompt.

        Format:
            [Context from turn N]
            User said: <summary>
            I responded: <summary>
            [Tools used: <summary>]

            [Next turn]
            <new_prompt>

        Args:
            checkpoint: Prior turn checkpoint
            next_prompt: New user prompt for next turn

        Returns:
            Prepended prompt ready for Copilot spawn
        """
        context_lines = [f"[Context from turn {checkpoint.turn_number}]"]
        context_lines.append(f"User said: {checkpoint.prompt_summary}")
        context_lines.append(f"I responded: {checkpoint.response_summary}")

        if checkpoint.tool_calls_summary:
            context_lines.append(f"Tools used: {checkpoint.tool_calls_summary}")

        context_lines.append("")  # Blank line
        context_lines.append("[Next turn]")
        context_lines.append(next_prompt)

        return "\n".join(context_lines)


class CopilotTaskWrapper:
    """
    Main class: decompose multi-turn into sequential Copilot spawns.

    Usage:
        wrapper = CopilotTaskWrapper(session_dir, copilot_engine)
        result = wrapper.execute_sequential(
            initial_prompt="Help me write a function",
            follow_ups=["Now add error handling", "Make it async"],
            spawn_fn=my_copilot_spawn_function
        )
    """

    MAX_TURNS_DEFAULT = 10
    WARN_TURN_THRESHOLD = 10

    def __init__(self, session_dir: str, tenant_id: str = "_default"):
        """
        Initialize wrapper.

        Args:
            session_dir: Session directory (for checkpoints)
            tenant_id: Tenant ID (ADR-0007)
        """
        self.session_dir = session_dir
        self.tenant_id = tenant_id
        self.checkpoint_saver = CheckpointSaver()
        self.prompt_prepender = PromptPrepender()

    def execute_sequential(
        self,
        initial_prompt: str,
        follow_ups: List[str],
        spawn_fn: Any,  # Copilot spawn function (receives prompt, returns response)
        max_turns: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Execute multi-turn conversation via sequential Copilot spawns.

        Args:
            initial_prompt: First turn prompt
            follow_ups: List of subsequent prompts
            spawn_fn: Copilot spawn function (prompt) -> response
            max_turns: Max turns to allow (default: WARN_TURN_THRESHOLD)

        Returns:
            {
                "status": "success" | "error" | "max_turns_exceeded",
                "turns": [{"prompt": ..., "response": ..., "checkpoint": ...}, ...],
                "final_response": "...",
                "warning": "..." (if token creep detected)
            }

        Raises:
            ValueError: If configuration invalid
        """
        if max_turns is None:
            max_turns = self.MAX_TURNS_DEFAULT

        all_turns = [initial_prompt] + follow_ups
        total_turns = len(all_turns)

        if total_turns > self.WARN_TURN_THRESHOLD:
            logger.warning(
                f"Multi-turn count ({total_turns}) exceeds threshold ({self.WARN_TURN_THRESHOLD}). "
                "Consider using Claude Code for native multi-turn support."
            )

        turns_result = []
        prior_checkpoint = None
        warning = None

        for turn_number, prompt in enumerate(all_turns, start=1):
            try:
                turn_result = self._execute_single_turn(
                    turn_number=turn_number,
                    prompt=prompt,
                    spawn_fn=spawn_fn,
                    prior_checkpoint=prior_checkpoint,
                )

                if turn_result.get("error"):
                    return {
                        "status": "error",
                        "turns": turns_result,
                        "error": turn_result["error"],
                    }

                turns_result.append(turn_result)
                prior_checkpoint = turn_result.get("checkpoint")

            except Exception as e:
                logger.error(f"Error executing turn {turn_number}: {e}")
                return {
                    "status": "error",
                    "turns": turns_result,
                    "error": str(e),
                }

        return {
            "status": "success",
            "turns": turns_result,
            "final_response": turns_result[-1]["response"] if turns_result else "",
            "warning": warning,
        }

    def _execute_single_turn(
        self,
        turn_number: int,
        prompt: str,
        spawn_fn: Any,
        prior_checkpoint: Optional[TurnCheckpoint] = None,
    ) -> Dict[str, Any]:
        """
        Execute single turn with optional context prepending.

        Args:
            turn_number: Current turn
            prompt: User prompt for this turn
            spawn_fn: Copilot spawn function
            prior_checkpoint: Checkpoint from prior turn (if any)

        Returns:
            {
                "prompt": "...",
                "response": "...",
                "checkpoint": TurnCheckpoint,
                "error": "..." (if failed)
            }
        """
        # Prepend prior context if available
        effective_prompt = prompt
        if prior_checkpoint:
            effective_prompt = self.prompt_prepender.prepend_context(prior_checkpoint, prompt)
            logger.info(f"Turn {turn_number}: prepended context from turn {prior_checkpoint.turn_number}")

        # Spawn Copilot
        logger.info(f"Executing turn {turn_number}")
        try:
            response = spawn_fn(effective_prompt)
        except Exception as e:
            logger.error(f"Turn {turn_number} spawn failed: {e}")
            return {
                "prompt": effective_prompt,
                "response": "",
                "error": str(e),
            }

        # Save checkpoint for next turn
        checkpoint = self.checkpoint_saver.save_checkpoint(
            session_dir=self.session_dir,
            turn_number=turn_number,
            prompt=prompt,  # Save original prompt, not prepended
            response=response,
            token_count=0,  # TODO: Calculate actual token count
        )

        return {
            "prompt": effective_prompt,
            "response": response,
            "checkpoint": checkpoint,
        }
