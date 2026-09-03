"""GDPR Art. 17 (Right to Erasure) Orchestrator — Coordinated cascade delete across modules.

**Problem (C1–C3):** Separate delete methods in decision_history, outcome_feedback, user_profile
and the ADR-0314 event files lead to incomplete erasure if any step is forgotten.

**Solution:** Single orchestrator that:
1. Deletes outcomes for the user (per-user, tenant-scoped)
2. Deletes the user profile (``UserProfileManager.delete_user_profiles``)
3. Deletes decisions for the user
4. Erases the user's ADR-0314 learning events (partition rewrite + tombstone)
5. Reports counts per store

**Atomicity:** Not a true transaction (would need centralized DB), but retry logic ensures
eventual consistency.

**GDPR Art. 17 Compliance:**
- Complete erasure (all related data deleted)
- Fail-closed (raises exception if any delete fails after retries)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GDPRErasureCoordinator:
    """Coordinate cascading deletion across decision_history, outcome_feedback,
    user_profile and the ADR-0314 event store.

    Usage:
        coord = GDPRErasureCoordinator(
            decision_store=store_history,
            outcome_store=store_outcome,
            profile_manager=manager_profile,
            event_store=event_persistence.EventStore(tenant_id),
        )
        deleted = coord.erase_user("tenant_1", "user-123")
        # Returns per-store counts, or raises if the cascade fails
    """

    def __init__(
        self,
        decision_store: object,  # DecisionHistoryStore
        outcome_store: object,  # OutcomeFeedbackStore
        profile_manager: Optional[object] = None,  # UserProfileManager
        event_store: Optional[object] = None,  # event_persistence.EventStore (ADR-0314)
    ):
        """Initialize coordinator with store references."""
        self.decision_store = decision_store
        self.outcome_store = outcome_store
        self.profile_manager = profile_manager
        self.event_store = event_store

    def erase_user(
        self, tenant_id: str, user_id: str, max_retries: int = 3
    ) -> dict[str, int]:
        """Erase all user data (GDPR Art. 17).

        Cascades across:
        1. Outcome Feedback
        2. User Profiles
        3. Decision History
        4. ADR-0314 learning events (if an event_store was supplied)

        Args:
            tenant_id: Tenant identifier
            user_id: User identifier
            max_retries: Retry count for transient failures

        Returns:
            {"decisions": N, "outcomes": N, "profiles": N, "events": N, "total": N}

        Raises:
            ValueError: If erasure fails after retries (fail-closed)
        """
        if not tenant_id or not user_id:
            raise ValueError("tenant_id and user_id required")

        for attempt in range(max_retries):
            try:
                # Step 1: Outcomes (per-user, tenant-scoped delete)
                deleted_outcomes = self.outcome_store.delete_user_outcomes(tenant_id, user_id)

                # Step 2: Profile
                deleted_profiles = 0
                if self.profile_manager is not None:
                    deleted_profiles = self.profile_manager.delete_user_profiles(user_id, tenant_id)

                # Step 3: Decisions
                deleted_decisions = self.decision_store.delete_user_decisions(tenant_id, user_id)

                # Step 4: ADR-0314 event files (partition rewrite + tombstone + audit)
                deleted_events = 0
                if self.event_store is not None:
                    deleted_events = asyncio.run(
                        self.event_store.erase_user_events(tenant_id=tenant_id, user_id=user_id)
                    )

                total = deleted_decisions + deleted_outcomes + deleted_profiles + deleted_events
                if total == 0:
                    logger.warning(
                        f"Erasure returned 0 rows for user {user_id} in tenant {tenant_id}"
                    )

                logger.info(
                    f"Erasure complete: {deleted_decisions} decisions, "
                    f"{deleted_outcomes} outcomes, {deleted_profiles} profiles, "
                    f"{deleted_events} events"
                )

                return {
                    "decisions": deleted_decisions,
                    "outcomes": deleted_outcomes,
                    "profiles": deleted_profiles,
                    "events": deleted_events,
                    "total": total,
                }

            except Exception as e:
                logger.error(f"Erasure attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(
                        f"Cascading erasure failed after {max_retries} attempts: {e}"
                    ) from e

        # Should not reach here, but if it does, fail-closed
        raise ValueError("Cascading erasure exceeded retry limit")
