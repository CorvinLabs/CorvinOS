"""Hook Registry — preprocessing pipeline for turn processing (ADR-0268 Phase 2)."""
from __future__ import annotations

import asyncio
import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from core.concurrency.locks import RWLock

logger = logging.getLogger(__name__)


@dataclass
class PreprocessContext:
    """Mutable context passed through preprocessing pipeline."""

    turn: dict[str, Any]
    session: dict[str, Any]
    user: dict[str, Any]
    tenant_id: str = "_default"
    metadata: dict[str, Any] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        """Reject the turn with reason."""
        self.metadata["rejected"] = True
        self.metadata["rejection_reason"] = reason


@dataclass
class HookDefinition:
    """Defines a preprocessing hook."""

    id: str
    trigger: str  # "preprocessing", "on_error", "on_complete"
    priority: int = 50  # 0-1000; higher = runs first
    file: str = ""
    function: str = ""
    package_id: Optional[str] = None
    enabled: bool = True

    def __lt__(self, other: HookDefinition) -> bool:
        """Sort by priority (descending)."""
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.id < other.id


class HookRegistry:
    """
    Registry for preprocessing hooks.

    Hooks are loaded from packages and executed in priority order
    before each LLM turn. Supports fail-closed error handling and
    audit logging.
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self._hooks: dict[str, list[HookDefinition]] = {
            "preprocessing": [],
            "on_error": [],
            "on_complete": [],
            "on_artifact": [],
        }
        self._loaded_functions: dict[str, Callable] = {}
        # F-C4 Fix: RWLock for thread-safe registry access
        # Optimized for read-heavy workloads (many get_hooks, few register_hook)
        self._lock = RWLock(timeout=5.0)

    def register_hook(self, hook_def: HookDefinition) -> None:
        """Register a hook with the registry (write lock protected)."""
        trigger = hook_def.trigger
        if trigger not in self._hooks:
            raise ValueError(f"Unknown hook trigger: {trigger}")

        # F-C4: Acquire write lock for thread-safe modification
        with self._lock.write_lock():
            self._hooks[trigger].append(hook_def)
            self._hooks[trigger].sort()
            logger.info(
                f"Registered hook {hook_def.id} (trigger={trigger}, priority={hook_def.priority})"
            )

    def unregister_hook(self, hook_id: str) -> None:
        """Unregister a hook from all triggers (write lock protected)."""
        # F-C4: Acquire write lock for thread-safe modification
        with self._lock.write_lock():
            for trigger in self._hooks.values():
                trigger[:] = [h for h in trigger if h.id != hook_id]
            if hook_id in self._loaded_functions:
                del self._loaded_functions[hook_id]

    def load_hook_function(self, hook_def: HookDefinition) -> Callable:
        """
        Load a hook function from a file (read/write lock protected).

        Returns the callable or raises on import error.
        """
        # F-C4: First, try read lock for cache check
        try:
            with self._lock.read_lock():
                if hook_def.id in self._loaded_functions:
                    return self._loaded_functions[hook_def.id]
        except TimeoutError:
            logger.warning(f"Read lock timeout for hook {hook_def.id}, retrying with write lock")

        # Not in cache; acquire write lock to load + cache
        with self._lock.write_lock():
            # Double-check pattern (might have been loaded by another thread)
            if hook_def.id in self._loaded_functions:
                return self._loaded_functions[hook_def.id]

            hook_file = Path(hook_def.file)
            if not hook_file.exists():
                raise FileNotFoundError(f"Hook file not found: {hook_def.file}")

            try:
                spec = importlib.util.spec_from_file_location(f"hook_{hook_def.id}", hook_file)
                if spec is None or spec.loader is None:
                    raise ImportError(f"Could not load spec for {hook_def.file}")

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                func = getattr(module, hook_def.function, None)
                if func is None:
                    raise AttributeError(
                        f"Function {hook_def.function} not found in {hook_def.file}"
                    )

                self._loaded_functions[hook_def.id] = func
                return func

            except Exception as e:
                logger.error(f"Failed to load hook {hook_def.id}: {e}")
                raise

    async def run_pipeline(
        self, ctx: PreprocessContext, trigger: str = "preprocessing"
    ) -> PreprocessContext:
        """
        Run preprocessing pipeline for a given trigger (read lock protected).

        Executes all hooks for the trigger in priority order.
        Fails closed — hook errors don't crash the pipeline.

        Args:
            ctx: Preprocessing context (mutable)
            trigger: Hook trigger type ("preprocessing", "on_error", etc.)

        Returns:
            Modified context (ctx is modified in-place)
        """
        # F-C4: Acquire read lock to safely get hooks snapshot
        with self._lock.read_lock():
            if trigger not in self._hooks:
                return ctx

            # Copy hooks list to avoid holding lock during execution
            hooks = list(self._hooks[trigger])
            if not hooks:
                return ctx

        # Execute hooks outside lock (avoid holding lock during hook execution)
        for hook_def in hooks:
            if not hook_def.enabled:
                continue

            try:
                func = self.load_hook_function(hook_def)

                # Determine if function is async
                if asyncio.iscoroutinefunction(func):
                    await func(ctx)
                else:
                    func(ctx)

                # Check if turn was rejected by hook
                if ctx.metadata.get("rejected"):
                    logger.warning(
                        f"Turn rejected by hook {hook_def.id}: {ctx.metadata.get('rejection_reason')}"
                    )
                    break

            except Exception as e:
                logger.error(
                    f"Hook {hook_def.id} failed (trigger={trigger}): {e}",
                    exc_info=True,
                )
                # Fail closed: log error, continue with next hook
                ctx.metadata.setdefault("hook_errors", []).append(
                    {
                        "hook_id": hook_def.id,
                        "error": str(e),
                        "trigger": trigger,
                    }
                )

        return ctx

    def get_hooks(self, trigger: str) -> list[HookDefinition]:
        """Get all hooks for a trigger, sorted by priority (read lock protected)."""
        # F-C4: Acquire read lock for thread-safe read
        with self._lock.read_lock():
            return sorted(self._hooks.get(trigger, []))

    def get_hook(self, hook_id: str) -> Optional[HookDefinition]:
        """Get a specific hook definition (read lock protected)."""
        # F-C4: Acquire read lock for thread-safe read
        with self._lock.read_lock():
            for hooks in self._hooks.values():
                for hook in hooks:
                    if hook.id == hook_id:
                        return hook
            return None
