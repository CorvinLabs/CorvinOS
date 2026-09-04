"""L4: Context Isolation — Copy-on-Write semantics for skill execution.

Ensures skills can't leak state or mutate shared task context.
ADR-0571: Context Isolation Layer
"""

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional, Dict
from datetime import datetime


@dataclass(frozen=True)
class StateDelta:
    """Represents a state change made by a skill."""
    skill_id: str
    path: str  # JSONPath of what changed (e.g., "context.user_prefs.theme")
    old_value: Any
    new_value: Any
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """Serialize delta for audit logging."""
        return {
            "skill_id": self.skill_id,
            "path": self.path,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class IsolatedTaskContext:
    """Copy-on-Write wrapper for task context.

    When a skill executes, it gets an isolated copy of the task context.
    Any mutations stay on the copy until explicitly merged back.

    Invariant: Original context is never modified by skill execution.
    """

    # Original task context (immutable reference)
    _original_context: Dict[str, Any]

    # Working copy (skill sees this, mutations here)
    _context_copy: Dict[str, Any]

    # Skill metadata
    _skill_id: str
    _task_id: str
    _tenant_id: str

    # Mutation tracking
    _mutations: Dict[str, StateDelta] = field(default_factory=dict)

    # Isolation markers
    _source_task_id: str = ""
    _is_isolated: bool = True

    # Original context hash at creation time (for detecting external modifications)
    _original_context_hash_at_creation: str = ""

    @classmethod
    def create_isolated(
        cls,
        original_context: Dict[str, Any],
        skill_id: str,
        task_id: str,
        tenant_id: str,
    ) -> "IsolatedTaskContext":
        """Create an isolated copy for skill execution.

        Args:
            original_context: The original task context
            skill_id: Skill ID that will execute on this copy
            task_id: Task ID
            tenant_id: Tenant ID (multi-tenant isolation)

        Returns:
            IsolatedTaskContext with deep copy of context

        Raises:
            ValueError: If tenant_id is empty (multi-tenant safety)
        """
        if not tenant_id:
            raise ValueError("tenant_id cannot be empty (multi-tenant safety)")

        # Capture original context hash at creation time
        original_hash = hashlib.sha256(
            json.dumps(original_context, sort_keys=True, default=str).encode()
        ).hexdigest()

        isolated = cls(
            _original_context=original_context,
            _context_copy=copy.deepcopy(original_context),
            _skill_id=skill_id,
            _task_id=task_id,
            _tenant_id=tenant_id,
            _mutations={},
            _source_task_id=task_id,
            _is_isolated=True,
            _original_context_hash_at_creation=original_hash,
        )
        return isolated

    def get(self, path: str, default: Any = None) -> Any:
        """Get a value from the isolated context.

        Args:
            path: JSONPath to value (e.g., "user.name" or "context.prefs.theme")
            default: Default if path not found

        Returns:
            Value from the isolated copy (not the original)
        """
        parts = path.split(".")
        current = self._context_copy

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default

        return current

    def set(self, path: str, value: Any) -> None:
        """Set a value in the isolated context.

        Mutation is tracked for later merging.

        Args:
            path: JSONPath to set (e.g., "user.name")
            value: New value

        Raises:
            ValueError: If skill tries to set read-only fields (fail-closed)
        """
        if not self._is_isolated:
            raise RuntimeError("Cannot mutate non-isolated context")

        # Read-only fields (fail-closed)
        readonly_prefixes = ("_tenant_id", "_task_id", "_skill_id")
        if any(path.startswith(p) for p in readonly_prefixes):
            raise ValueError(f"Field '{path}' is read-only; skill cannot mutate")

        # Get old value for delta
        old_value = self.get(path)

        # Set new value
        parts = path.split(".")
        current = self._context_copy

        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]

        current[parts[-1]] = value

        # Track mutation
        delta_key = f"{self._skill_id}:{path}"
        self._mutations[delta_key] = StateDelta(
            skill_id=self._skill_id,
            path=path,
            old_value=old_value,
            new_value=value,
        )

    def get_mutations(self) -> Dict[str, StateDelta]:
        """Get all mutations made by the skill.

        Returns:
            Dict of {path: StateDelta}
        """
        return self._mutations.copy()

    def get_context_hash(self) -> str:
        """Compute hash of current context state for audit logging.

        Returns:
            SHA256 hash of context_copy (for audit chain)
        """
        context_json = json.dumps(self._context_copy, sort_keys=True, default=str)
        return hashlib.sha256(context_json.encode()).hexdigest()

    def assert_isolation_intact(self) -> bool:
        """Verify isolation is intact (original context unchanged).

        Returns:
            True if original context matches state at creation time

        Raises:
            RuntimeError: If isolation was violated (fail-closed)
        """
        # Compute current hash of original context
        current_hash = hashlib.sha256(
            json.dumps(self._original_context, sort_keys=True, default=str).encode()
        ).hexdigest()

        # Compare against hash captured at creation time
        if self._original_context_hash_at_creation != current_hash:
            raise RuntimeError(
                "CRITICAL: Original context was modified during skill execution "
                "(isolation violation)"
            )

        return True


class ContextMutationValidator:
    """Validates that skill mutations respect postconditions."""

    @staticmethod
    def validate_deltas(
        deltas: Dict[str, StateDelta],
        postconditions: list,
        context_after: Dict[str, Any],
    ) -> tuple[bool, str]:
        """Validate mutations against skill's postconditions.

        Args:
            deltas: Mutations made by skill
            postconditions: List of Predicate objects (from SkillContract)
            context_after: Context state after skill execution

        Returns:
            (valid, reason)
        """
        if not postconditions:
            # No postconditions = accept all mutations
            return True, "No postconditions to validate"

        # Evaluate each postcondition against the final context
        for predicate in postconditions:
            try:
                # Postconditions have a `condition` callable and a `name`
                if hasattr(predicate, "evaluate"):
                    # Predicate.evaluate() method (from SkillContract.Predicate)
                    if not predicate.evaluate(context_after):
                        return False, f"Postcondition '{predicate.name}' failed"
                elif callable(predicate.condition):
                    # Fallback: call condition directly
                    if not predicate.condition(context_after):
                        return False, f"Postcondition '{predicate.name}' failed"
            except Exception as e:
                # Predicate evaluation error (treat as failure, fail-closed)
                return False, f"Postcondition '{predicate.name}' evaluation error: {str(e)}"

        return True, "All postconditions validated"


class ContextMerger:
    """Merges validated deltas back into original context."""

    @staticmethod
    def merge_deltas(
        original_context: Dict[str, Any],
        deltas: Dict[str, StateDelta],
        skill_id: str,
        tenant_id: str,
    ) -> tuple[Dict[str, Any], list[StateDelta]]:
        """Apply validated deltas to original context.

        Args:
            original_context: Original task context
            deltas: Deltas to merge
            skill_id: Skill that created deltas (audit)
            tenant_id: Tenant ID (audit)

        Returns:
            (merged_context, applied_deltas)

        Raises:
            ValueError: If delta validation fails
        """
        merged = copy.deepcopy(original_context)
        applied = []

        for delta_key, delta in deltas.items():
            # Only apply deltas from this skill
            if delta.skill_id != skill_id:
                continue

            # Apply delta
            parts = delta.path.split(".")
            current = merged

            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]

            current[parts[-1]] = delta.new_value
            applied.append(delta)

        return merged, applied

    @staticmethod
    def compute_merge_hash(
        merged_context: Dict[str, Any],
        tenant_id: str,
    ) -> str:
        """Compute hash of merged context (for audit chain linking).

        Args:
            merged_context: Merged context
            tenant_id: Tenant ID (included in hash)

        Returns:
            SHA256 hash
        """
        context_json = json.dumps(
            {"tenant_id": tenant_id, "context": merged_context},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(context_json.encode()).hexdigest()
