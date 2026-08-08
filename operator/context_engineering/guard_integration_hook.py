"""
CR-6 Guard Integration Hook

This module shows how console and agent code should wire the DangerZoneGuard
into their suggestion/recommendation flows.

The guard is designed to be injected at the point where suggested contexts,
skills, or ADRs are about to be recommended to the user.

Usage Pattern:
  1. Load profiles at session start
  2. Create guard instance
  3. Before suggesting any context, call guard.should_use_context()
  4. Log guard decisions to audit trail
"""

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from critical_fixes_roundk2 import ExtensibleDangerZoneGuard

logger = logging.getLogger(__name__)

# MEDIUM FIX M1 + ITERATION 5 FIX I5-2: Module-level LRU cache to avoid unbounded growth
# Max 10 profiles (typical deployment has 1-3 tenants, 10 is safe upper bound)
_PROFILE_CACHE_MAX_SIZE = 10
_profile_cache: OrderedDict[str, tuple] = OrderedDict()  # (tenant_id, profile_path) -> (data, mtime)
_cache_lock = threading.Lock()


class ContextSuggestionGate:
    """CR-6: Gate that checks suggested contexts against danger zones before surfacing."""

    def __init__(self, profile_dir: Path, tenant_id: str = "_default"):
        """
        Initialize gate with loaded profiles.

        Args:
            profile_dir: Path to Tier 3 profiles (from aggregator)
            tenant_id: Tenant identifier
        """
        self.profile_dir = profile_dir
        self.tenant_id = tenant_id
        self.guard: Optional[ExtensibleDangerZoneGuard] = None
        self._load_guard()

    def _load_guard(self) -> None:
        """Load latest profiles and initialize guard (with cache)."""
        try:
            baseline_link = self.profile_dir / "tenant-baseline.json"

            # CRITICAL FIX H5: Validate symlink target exists before opening
            if baseline_link.is_symlink():
                # Symlink exists; now check if target is valid
                try:
                    import os
                    target_path = os.readlink(str(baseline_link))  # Use os.readlink for Python 3.8+ compat
                    target = baseline_link.parent / target_path  # Resolve relative to symlink location
                    target_exists = target.exists()
                    if not target_exists:
                        logger.warning(f"CR-6: Baseline symlink is dangling: {baseline_link} → {target_path}")
                except (OSError, FileNotFoundError):
                    # Symlink read failed or is broken
                    logger.warning(f"CR-6: Failed to read symlink {baseline_link}")
                    target_exists = False
            else:
                target_exists = baseline_link.exists()

            if target_exists:
                # MEDIUM FIX M1 + CODE REVIEW I6-4: Fix TOCTOU race in cache check
                # MUST read mtime INSIDE lock to prevent file modification race
                cache_key = (self.tenant_id, str(baseline_link.resolve()))

                with _cache_lock:
                    # Read mtime INSIDE lock to detect concurrent modifications
                    current_mtime = baseline_link.stat().st_mtime

                    if cache_key in _profile_cache:
                        cached_data, cached_mtime = _profile_cache[cache_key]
                        if cached_mtime == current_mtime:
                            # Fast path: mtime unchanged, cache is valid
                            # Move to end (LRU)
                            _profile_cache.move_to_end(cache_key)
                            self.guard = ExtensibleDangerZoneGuard({"tenant-baseline": cached_data})
                            logger.debug(f"CR-6: Guard loaded from cache (mtime unchanged)")
                            return

                # Cache miss or stale (mtime changed); read content OUTSIDE lock
                import json
                with open(baseline_link, "r") as f:
                    file_content = f.read()

                # Update cache with mtime (not hash; faster)
                profile_data = json.loads(file_content)
                with _cache_lock:
                    # Check mtime again (file could have changed during read)
                    final_mtime = baseline_link.stat().st_mtime

                    # ITERATION 5 FIX I5-2: Implement LRU eviction
                    if cache_key not in _profile_cache:
                        # Check if cache is full
                        if len(_profile_cache) >= _PROFILE_CACHE_MAX_SIZE:
                            # Evict oldest (first) entry
                            oldest_key = next(iter(_profile_cache))
                            del _profile_cache[oldest_key]
                            logger.debug(f"CR-6: Evicted oldest cache entry to make room")

                    _profile_cache[cache_key] = (profile_data, final_mtime)
                    _profile_cache.move_to_end(cache_key)  # Mark as most recent

                self.guard = ExtensibleDangerZoneGuard({"tenant-baseline": profile_data})
                logger.info(f"CR-6: Guard loaded from disk with {len(self.guard.patterns)} patterns")
            else:
                logger.warning("CR-6: No baseline profile found; guard disabled")
        except Exception as e:
            logger.error(f"CR-6: Failed to load guard: {e}")

    def filter_suggestions(
        self,
        suggested_contexts: List[str],
        user_id: str,
        task_conditions: Dict,
    ) -> Tuple[List[str], List[str]]:
        """
        CR-6: Filter suggested contexts through danger zone guard.

        Args:
            suggested_contexts: List of context IDs to suggest (e.g., ADRs, skills)
            user_id: User ID for per-user profile checks
            task_conditions: Task metadata (urgency, task_type, deadline, etc.)

        Returns:
            (approved_contexts, blocked_contexts_with_reasons)
        """
        if not self.guard:
            logger.debug("CR-6: Guard not initialized; passing all suggestions")
            return suggested_contexts, []

        approved = []
        blocked = []

        for context_id in suggested_contexts:
            allowed, reason = self.guard.should_use_context(
                context_id,
                task_conditions,
                user_id=user_id,
            )

            if allowed:
                approved.append(context_id)
                logger.debug(f"CR-6: Approved {context_id}")
            else:
                blocked.append((context_id, reason))
                logger.info(f"CR-6: Blocked {context_id}: {reason}")

        return approved, blocked

    def get_blocked_audit_log(self) -> List[Dict]:
        """Get audit trail of all blocked contexts this session."""
        if self.guard:
            return self.guard.get_audit_log()
        return []


# ============================================================================
# Console Integration Hook (Example)
# ============================================================================

def console_suggest_contexts_with_guard(
    suggested_contexts: List[str],
    user_id: str,
    task_conditions: Dict,
    profile_dir: Path,
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """
    CONSOLE INTEGRATION EXAMPLE

    This is how console code should call the guard before suggesting contexts.

    Called at the point where console is about to display skill recommendations,
    ADR suggestions, or memory context to the user.

    Args:
        suggested_contexts: Skills/ADRs/contexts to suggest
        user_id: User making the request
        task_conditions: Task metadata (urgency, type, deadline, etc.)
        profile_dir: Profile directory (from config)

    Returns:
        (approved_contexts, blocked_with_reasons)
    """
    gate = ContextSuggestionGate(profile_dir)
    approved, blocked = gate.filter_suggestions(
        suggested_contexts,
        user_id,
        task_conditions,
    )

    if blocked:
        logger.warning(f"Console: {len(blocked)} contexts blocked by guard")
        for ctx_id, reason in blocked:
            logger.debug(f"  — {ctx_id}: {reason}")

    return approved, blocked


# ============================================================================
# Agent Integration Hook (Example)
# ============================================================================

def agent_filter_context_pool_with_guard(
    context_pool: Dict[str, List[str]],
    user_id: str,
    task_conditions: Dict,
    profile_dir: Path,
) -> Dict[str, List[str]]:
    """
    AGENT INTEGRATION EXAMPLE

    This is how agent code should filter its context pool before querying.

    The agent maintains multiple pools (ADRs, skills, concepts, memory).
    Before using any pool, filter through the guard.

    Args:
        context_pool: {
            "adrs": ["ADR-0269", "ADR-0270", ...],
            "skills": ["skill-e2e-wiring", "skill-testing", ...],
            "memory": ["memory-phase3", ...],
        }
        user_id: User making the request
        task_conditions: Task metadata
        profile_dir: Profile directory

    Returns:
        Filtered context_pool (same structure)
    """
    gate = ContextSuggestionGate(profile_dir)
    filtered = {}

    for pool_name, contexts in context_pool.items():
        approved, blocked = gate.filter_suggestions(contexts, user_id, task_conditions)
        filtered[pool_name] = approved

        if blocked:
            logger.info(f"Agent: {pool_name} pool — {len(blocked)} contexts blocked by guard")

    return filtered


# ============================================================================
# Programmatic Usage Markers
# ============================================================================

# Key integration points for developers:
#
# [CONSOLE] operator/console/chat_handler.py:
#   - Before calling task_engine.execute_task(), filter suggested contexts:
#     approved, blocked = console_suggest_contexts_with_guard(...)
#     # Only include approved contexts in the turn
#
# [AGENT] operator/context_engineering/task_engine.py:
#   - Before building context_pool, filter through guard:
#     filtered_pool = agent_filter_context_pool_with_guard(...)
#     # Use filtered_pool instead of raw pool
#
# [AUDIT] Both should log blocked decisions:
#   - gate.get_blocked_audit_log() → audit trail
#   - Ensures GDPR compliance (Art. 30: record processing decisions)
#
