"""Context Coherence Manager — Persist and resume tool/strategy inheritance (ADR-0369).

Manages persistence and resumption of ToolCoherence across session boundaries.
Enables learned tool preferences, strategy decisions, and cost estimates to carry
forward from parent sessions to new sessions.

Integration points:
- ContextInitializer: Load parent coherence on resume
- ToolForgeSubsystem: Record tool executions for learning
- LoopEngineer: Use inherited strategies for error recovery
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime

from core.orchestration.context_coherence import ToolCoherence

logger = logging.getLogger(__name__)


class ContextCoherenceError(Exception):
    """Base exception for coherence operations."""
    pass


class CoherenceNotFoundError(ContextCoherenceError):
    """Raised when coherence cannot be loaded."""
    pass


class ContextCoherenceManager:
    """Manages ToolCoherence persistence and inheritance.

    Responsibilities:
    - Save coherence state to disk (JSON per task)
    - Load parent coherence for new sessions
    - Validate coherence age (max 24 hours)
    - Track coherence chain (for audit)
    """

    def __init__(self, corvin_home: Optional[str] = None):
        """Initialize ContextCoherenceManager.

        Args:
            corvin_home: Path to CORVIN_HOME (for config)
        """
        import os

        if corvin_home is None:
            corvin_home = os.environ.get("CORVIN_HOME")
            if not corvin_home:
                raise ValueError(
                    "corvin_home not provided and CORVIN_HOME environment variable not set"
                )

        self.corvin_home = Path(corvin_home)
        self._coherence_base = (
            self.corvin_home / "tenants" / "_default" / "coherence"
        )
        self._coherence_base.mkdir(parents=True, exist_ok=True)

    def save_coherence(
        self,
        task_id: str,
        coherence: ToolCoherence,
        session_id: str,
    ) -> str:
        """Save coherence state for a task.

        Persists ToolCoherence to disk for use by future sessions.
        Latest coherence saved to {task_id}/latest.json.

        Args:
            task_id: Unique task identifier
            coherence: ToolCoherence instance to persist
            session_id: Current session ID

        Returns:
            Coherence ID (timestamp-based)

        Raises:
            ContextCoherenceError: If persistence fails
        """
        try:
            coherence_id = datetime.utcnow().isoformat()
            task_coherence_dir = self._coherence_base / task_id
            task_coherence_dir.mkdir(parents=True, exist_ok=True)

            # Save to latest.json
            latest_path = task_coherence_dir / "latest.json"
            coherence_dict = coherence.to_dict()
            coherence_dict["coherence_id"] = coherence_id
            coherence_dict["session_id"] = session_id

            with open(latest_path, "w") as f:
                json.dump(coherence_dict, f, indent=2)

            # Append to history.jsonl
            history_path = task_coherence_dir / "history.jsonl"
            with open(history_path, "a") as f:
                f.write(json.dumps(coherence_dict) + "\n")

            logger.info(
                f"Saved coherence '{coherence_id}' for task '{task_id}' "
                f"from session '{session_id}'"
            )
            return coherence_id

        except Exception as e:
            logger.error(f"Failed to save coherence for task '{task_id}': {e}")
            raise ContextCoherenceError(f"Failed to save coherence: {e}") from e

    def load_coherence(
        self,
        task_id: str,
        coherence_id: Optional[str] = None,
    ) -> ToolCoherence:
        """Load coherence for a task.

        If coherence_id is None, loads the latest coherence.
        Validates age (max 24 hours).

        Args:
            task_id: Unique task identifier
            coherence_id: Optional coherence ID to load specific version

        Returns:
            ToolCoherence instance

        Raises:
            CoherenceNotFoundError: If coherence not found
            ContextCoherenceError: If validation fails
        """
        try:
            task_coherence_dir = self._coherence_base / task_id

            if not task_coherence_dir.exists():
                raise CoherenceNotFoundError(
                    f"No coherence found for task '{task_id}'"
                )

            # Load latest if no ID specified
            if coherence_id is None:
                latest_path = task_coherence_dir / "latest.json"
                if not latest_path.exists():
                    raise CoherenceNotFoundError(
                        f"No latest coherence for task '{task_id}'"
                    )

                with open(latest_path) as f:
                    data = json.load(f)
            else:
                # Load specific coherence from history
                history_path = task_coherence_dir / "history.jsonl"
                if not history_path.exists():
                    raise CoherenceNotFoundError(
                        f"Coherence history not found for task '{task_id}'"
                    )

                data = None
                with open(history_path) as f:
                    for line in f:
                        entry = json.loads(line)
                        if entry.get("coherence_id") == coherence_id:
                            data = entry
                            break

                if data is None:
                    raise CoherenceNotFoundError(
                        f"Coherence '{coherence_id}' not found for task '{task_id}'"
                    )

            # Validate age (max 24 hours)
            created_at_str = data.get("created_at")
            if created_at_str:
                created_at = datetime.fromisoformat(created_at_str)
                age_hours = (datetime.utcnow() - created_at).total_seconds() / 3600
                if age_hours > 24:
                    logger.warning(
                        f"Coherence for task '{task_id}' is {age_hours:.1f}h old "
                        f"(max: 24h); may not be reliable"
                    )

            coherence = ToolCoherence.from_dict(data)
            logger.info(f"Loaded coherence for task '{task_id}'")
            return coherence

        except CoherenceNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to load coherence for task '{task_id}': {e}")
            raise ContextCoherenceError(f"Failed to load coherence: {e}") from e

    def get_coherence_metadata(self, task_id: str) -> Dict[str, Any]:
        """Get metadata for coherence of a task.

        Args:
            task_id: Unique task identifier

        Returns:
            Dict with coherence metadata (age, tool_count, strategy_count)
        """
        try:
            coherence = self.load_coherence(task_id)
            created_at = datetime.fromisoformat(
                json.loads(coherence.__class__.__name__)  # Hack to get created_at
            )
        except Exception:
            return {}

        return {
            "task_id": task_id,
            "tools_known_good_count": len(coherence.tools_known_good),
            "tools_known_bad_count": len(coherence.tools_known_bad),
            "strategies_learned_count": len(coherence.learned_strategies),
            "cost_samples": len(coherence.cost_deltas),
            "coherence_chain_length": len(coherence.coherence_chain),
        }

    def inherit_coherence(
        self,
        current_coherence: ToolCoherence,
        parent_coherence: ToolCoherence,
        strategy: str = "blend",
    ) -> ToolCoherence:
        """Merge parent coherence into current coherence.

        Blends parent and current preferences, with current overriding
        parent on conflicts.

        Args:
            current_coherence: Current session's ToolCoherence
            parent_coherence: Parent session's ToolCoherence
            strategy: Merge strategy ("blend" = current wins on conflict)

        Returns:
            Merged ToolCoherence with parent chain updated
        """
        try:
            # Inherit known good tools (current wins on conflict)
            inherited_good = {
                **parent_coherence.tools_known_good,
                **current_coherence.tools_known_good,
            }

            # Inherit known bad tools (current wins on conflict)
            inherited_bad = {
                **parent_coherence.tools_known_bad,
                **current_coherence.tools_known_bad,
            }

            # Inherit success rates per error (merge, current overrides)
            inherited_rates = {}
            for error_class, tools in parent_coherence.success_rates_per_error.items():
                inherited_rates[error_class] = dict(tools)

            for error_class, tools in current_coherence.success_rates_per_error.items():
                if error_class not in inherited_rates:
                    inherited_rates[error_class] = {}
                inherited_rates[error_class].update(tools)

            # Inherit learned strategies (current wins)
            inherited_strategies = {
                **parent_coherence.learned_strategies,
                **current_coherence.learned_strategies,
            }

            # Inherit learned preferences (current wins)
            inherited_preferences = {
                **parent_coherence.learned_preferences,
                **current_coherence.learned_preferences,
            }

            # Build new coherence chain
            parent_chain = parent_coherence.coherence_chain.copy()
            if parent_coherence.parent_coherence_id:
                parent_chain.append(parent_coherence.parent_coherence_id)

            # Create merged coherence
            merged = ToolCoherence(
                parent_session_id=parent_coherence.parent_session_id,
                parent_coherence_id=parent_coherence.parent_coherence_id,
                coherence_chain=parent_chain,
                tools_known_good=inherited_good,
                tools_known_bad=inherited_bad,
                success_rates_per_error=inherited_rates,
                learned_strategies=inherited_strategies,
                learned_preferences=inherited_preferences,
                cost_deltas=parent_coherence.cost_deltas.copy()
                + current_coherence.cost_deltas.copy(),
                cost_corrections=parent_coherence.cost_corrections.copy()
                + current_coherence.cost_corrections.copy(),
                tenant_id=current_coherence.tenant_id,
            )

            logger.info(
                f"Inherited coherence: {len(inherited_good)} good tools, "
                f"{len(inherited_bad)} bad tools, {len(inherited_strategies)} strategies"
            )
            return merged

        except Exception as e:
            logger.error(f"Failed to inherit coherence: {e}")
            raise ContextCoherenceError(f"Failed to inherit coherence: {e}") from e
