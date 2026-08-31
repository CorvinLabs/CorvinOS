"""Preprocessing hooks integration for chat_runtime (ADR-0268 Phase 2.5)."""
from __future__ import annotations

import logging
from typing import Any

from core.preprocessing import HookRegistry, PreprocessContext

logger = logging.getLogger(__name__)

# Global registry instance (one per process)
_HOOK_REGISTRY: dict[str, HookRegistry] = {}


def get_hook_registry(tenant_id: str = "_default") -> HookRegistry:
    """Get or create HookRegistry for a tenant."""
    if tenant_id not in _HOOK_REGISTRY:
        _HOOK_REGISTRY[tenant_id] = HookRegistry(tenant_id=tenant_id)
    return _HOOK_REGISTRY[tenant_id]


async def run_preprocessing_hooks(
    prompt: str,
    session: dict[str, Any],
    user: dict[str, Any],
    tenant_id: str = "_default",
) -> tuple[str, dict[str, Any]]:
    """
    Run preprocessing hooks before a turn.

    Returns:
        (modified_prompt, context_metadata)

    Raises:
        ValueError if turn is rejected by a hook
    """
    registry = get_hook_registry(tenant_id)

    # Build preprocessing context
    ctx = PreprocessContext(
        turn={"prompt": prompt},
        session=session,
        user=user,
        tenant_id=tenant_id,
    )

    # Run preprocessing pipeline
    ctx = await registry.run_pipeline(ctx, trigger="preprocessing")

    # Check if turn was rejected
    if ctx.metadata.get("rejected"):
        reason = ctx.metadata.get("rejection_reason", "Rejected by preprocessing hook")
        logger.warning(f"Turn rejected during preprocessing: {reason}")
        raise ValueError(f"Turn rejected: {reason}")

    # Extract modified prompt (hooks can modify it)
    modified_prompt = ctx.turn.get("prompt", prompt)

    # Return prompt and metadata (errors, warnings, etc.)
    return modified_prompt, ctx.metadata


def register_hook_from_package(
    package_id: str,
    hook_definitions: list[dict[str, Any]],
    tenant_id: str = "_default",
) -> None:
    """
    Register hooks from a loaded package.

    Called by PackageManager after a package is loaded.

    Args:
        package_id: ID of the package that provided these hooks
        hook_definitions: List of hook definitions from package manifest
        tenant_id: Tenant ID
    """
    registry = get_hook_registry(tenant_id)

    for hook_def in hook_definitions:
        # Skip if already registered
        if registry.get_hook(hook_def.get("id")):
            logger.debug(f"Hook {hook_def.get('id')} already registered, skipping")
            continue

        from core.preprocessing import HookDefinition

        hook = HookDefinition(
            id=hook_def.get("id"),
            trigger=hook_def.get("trigger", "preprocessing"),
            priority=hook_def.get("priority", 50),
            file=hook_def.get("file"),
            function=hook_def.get("function"),
            package_id=package_id,
            enabled=hook_def.get("enabled", True),
        )

        try:
            registry.register_hook(hook)
            logger.info(f"Registered hook {hook.id} from package {package_id}")
        except Exception as e:
            logger.error(f"Failed to register hook {hook.id}: {e}")


def unregister_hooks_from_package(
    package_id: str,
    tenant_id: str = "_default",
) -> None:
    """
    Unregister all hooks from a package.

    Called by PackageManager when a package is unloaded.

    Args:
        package_id: ID of the package to remove hooks from
        tenant_id: Tenant ID
    """
    registry = get_hook_registry(tenant_id)

    # Find all hooks from this package
    hooks_to_remove = []
    for hook_list in registry._hooks.values():
        for hook in hook_list:
            if hook.package_id == package_id:
                hooks_to_remove.append(hook.id)

    for hook_id in hooks_to_remove:
        registry.unregister_hook(hook_id)
        logger.info(f"Unregistered hook {hook_id} from package {package_id}")
