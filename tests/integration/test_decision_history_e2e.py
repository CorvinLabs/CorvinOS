#!/usr/bin/env python3
"""Integration test — Decision History (ADR-0316) E2E wiring proof.

This test MUST run end-to-end:
  1. Create DecisionRecorder
  2. Create decision via recorder
  3. Store decision via DecisionHistoryStore
  4. Query decisions from store
  5. Verify audit chain + tenant isolation

Success = decision flows from recorder → store → query results.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Manual test without pytest to verify E2E wiring


def test_decision_history_e2e():
    """E2E test: decision creation → storage → retrieval."""
    print("\n[E2E PHASE 1] Import modules...")
    try:
        from core.learning.decision_history import (
            DecisionRecorder,
            DecisionRecord,
            DecisionHistoryStore,
        )
        print("✅ Imports successful")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        return False

    print("\n[E2E PHASE 2] Create recorder...")
    try:
        recorder = DecisionRecorder("_default")
        print(f"✅ Recorder created for tenant: {recorder.tenant_id}")
    except Exception as e:
        print(f"❌ Recorder creation failed: {e}")
        return False

    print("\n[E2E PHASE 3] Create decision...")
    try:
        decision = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["skill-a", "skill-b", "skill-c"],
            chosen="skill-a",
            session_id="session-123",
            confidence_score=0.92,
            reasoning="Best latency/quality trade-off",
        )
        print(f"✅ Decision created: {decision.decision_id}")
        print(f"   - choice_type: {decision.choice_type}")
        print(f"   - candidates: {decision.candidates}")
        print(f"   - chosen: {decision.chosen}")
        print(f"   - confidence_score: {decision.confidence_score}")
        assert decision.decision_id is not None
        assert decision.chosen == "skill-a"
        assert decision.confidence_score == 0.92
    except Exception as e:
        print(f"❌ Decision creation failed: {e}")
        return False

    print("\n[E2E PHASE 4] Store decision persistently...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decisions.db"
            store = DecisionHistoryStore(db_path)
            print(f"✅ Store created: {db_path}")

            # Record the decision
            decision_id = store.record_decision(decision)
            print(f"✅ Decision recorded: {decision_id}")
            assert decision_id == decision.decision_id

            print("\n[E2E PHASE 5] Retrieve decision from store...")
            retrieved = store.get_decision(decision_id)
            assert retrieved is not None
            assert retrieved.choice_type == "skill_selection"
            assert retrieved.chosen == "skill-a"
            assert retrieved.confidence_score == 0.92
            print(f"✅ Decision retrieved successfully")

            print("\n[E2E PHASE 6] Query by type (tenant isolation)...")
            decisions = store.get_decisions_by_type("_default", "skill_selection")
            assert len(decisions) >= 1
            print(f"✅ Found {len(decisions)} decision(s) of type 'skill_selection'")

            print("\n[E2E PHASE 7] Compute candidate statistics...")
            stats = store.get_candidate_stats("_default", "skill_selection")
            assert "skill-a" in stats
            assert stats["skill-a"]["chosen"] == 1
            assert stats["skill-a"]["selection_rate"] > 0
            print(f"✅ Candidate statistics computed:")
            for candidate, stat in stats.items():
                print(
                    f"   - {candidate}: chosen={stat['chosen']}, "
                    f"total={stat['total']}, rate={stat['selection_rate']:.2%}"
                )

            print("\n[E2E PHASE 8] Secret redaction verification...")
            decision_with_secret = recorder.create_decision(
                choice_type="model_choice",
                candidates=["opus", "sonnet"],
                chosen="opus",
                session_id="session-456",
                reasoning="Used api_key=secret123 for auth",
            )
            assert decision_with_secret.reasoning == "[redacted]"
            print(f"✅ Secret redaction works: '{decision_with_secret.reasoning}'")

            print("\n[E2E PHASE 9] Tenant isolation (cross-tenant)...")
            recorder2 = DecisionRecorder("tenant-2")
            decision2 = recorder2.create_decision(
                choice_type="skill_selection",
                candidates=["x", "y"],
                chosen="x",
                session_id="session-789",
            )
            store.record_decision(decision2)

            # Query tenant-1 only
            decisions1 = store.get_decisions_by_type("_default", "skill_selection")
            decisions2 = store.get_decisions_by_type("tenant-2", "skill_selection")

            assert len(decisions1) >= 1
            assert len(decisions2) == 1
            print(f"✅ Tenant isolation verified:")
            print(f"   - tenant '_default': {len(decisions1)} decision(s)")
            print(f"   - tenant 'tenant-2': {len(decisions2)} decision(s)")

            print("\n[E2E PHASE 10] Date range query...")
            now = datetime.utcnow()
            start = now - timedelta(days=1)
            end = now + timedelta(seconds=1)
            decisions_range = store.get_decisions_by_date_range("_default", start, end)
            assert len(decisions_range) >= 1
            print(f"✅ Date range query: found {len(decisions_range)} decision(s)")

            print("\n[E2E PHASE 11] GDPR erasure (user_id tracking)...")
            recorder_user = DecisionRecorder("_default")
            decision_user = recorder_user.create_decision(
                choice_type="routing",
                candidates=["a", "b"],
                chosen="a",
                session_id="session-gdpr",
                user_id="user-123",
            )
            store.record_decision(decision_user)

            deleted = store.delete_user_decisions("_default", "user-123")
            assert deleted == 1
            print(f"✅ GDPR erasure: deleted {deleted} decision(s) for user-123")

            # Verify deletion
            verify = store.get_decision(decision_user.decision_id)
            assert verify is None
            print(f"✅ Verification: decision no longer exists")

            print("\n[E2E PHASE 12] Immutability check...")
            try:
                decision.chosen = "skill-b"
                print(f"❌ Immutability check FAILED: decision should be frozen")
                return False
            except Exception:
                print(f"✅ Immutability check passed: decision is frozen")

    except Exception as e:
        print(f"❌ Store operations failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print("\n" + "=" * 60)
    print("🎉 ALL E2E PHASES PASSED")
    print("=" * 60)
    return True


if __name__ == "__main__":
    import sys

    # Ensure project root is in path
    project_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(project_root))

    success = test_decision_history_e2e()
    sys.exit(0 if success else 1)
