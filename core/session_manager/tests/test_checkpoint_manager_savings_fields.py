"""CheckpointManager persists the ADR-0668 token-savings fields of a lifecycle Checkpoint.

Regression: 252aa3705 made ``save_checkpoint``/``load_checkpoint`` read and write
``baseline_tokens``/``actual_tokens``/``savings_tokens``/``savings_pct`` but never
declared them on ``lifecycle_manager.Checkpoint``. Every save raised AttributeError
(caught, ``return False``), so ``SessionAutoStarter`` refused every auto-split
(tests/e2e/test_adr0472_d1_vibe_gates.py).
"""

import asyncio
import tempfile
import time

from core.session_manager.checkpoint_manager import CheckpointManager
from core.session_manager.lifecycle_manager import Checkpoint


def _checkpoint(**extra) -> Checkpoint:
    return Checkpoint(
        session_id="sess-savings", goal="g", goal_hash="h", timestamp=time.time(),
        context_reduction_pct=0.5, context_tokens_used=100, audit_trail_hash="a", **extra,
    )


def test_defaults_are_zero():
    cp = _checkpoint()
    assert (cp.baseline_tokens, cp.actual_tokens, cp.savings_tokens, cp.savings_pct) == (0, 0, 0, 0.0)


def test_save_and_load_round_trip_the_savings_fields():
    mgr = CheckpointManager(checkpoint_dir=tempfile.mkdtemp(), auto_cleanup=False)
    cp = _checkpoint(baseline_tokens=1000, actual_tokens=400, savings_tokens=600, savings_pct=60.0)
    cp.checkpoint_hash = cp.compute_hash()  # as SessionLifecycleManager.create_checkpoint does

    async def run():
        assert await mgr.save_checkpoint(cp) is True
        return await mgr.load_checkpoint("sess-savings")

    loaded = asyncio.run(run())
    assert loaded is not None
    assert (loaded.baseline_tokens, loaded.actual_tokens, loaded.savings_tokens, loaded.savings_pct) == (
        1000, 400, 600, 60.0)
