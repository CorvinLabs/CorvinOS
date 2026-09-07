"""SkillAdapter lost-update race (F-K5).

Every console feedback request builds its OWN ``SkillAdapter`` (see
``method_discovery_api._adapter``) and each adapter used to load → mutate →
persist without any lock: two concurrent requests both read epoch N and both
wrote N+1. The fix takes an exclusive ``flock`` and RELOADS inside it, so the
epoch advances once per epoch run, whatever the interleaving.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from core.skills.os_skills.feedback_loop import ConfigHypothesis
from core.skills.os_skills.skill_adapter import SkillAdapter


def _epoch_on_disk(adapter: SkillAdapter) -> int:
    return json.loads(adapter.config_file.read_text())["optimizer"]["epoch"]


def test_two_concurrent_adapters_never_lose_an_epoch(tmp_path: Path) -> None:
    work = tmp_path / "skills"
    n_threads, n_epochs = 4, 25
    barrier = threading.Barrier(n_threads)

    def run() -> None:
        adapter = SkillAdapter("os.delegation_router", "_default", work_dir=work)
        barrier.wait()
        for _ in range(n_epochs):
            adapter.run_optimizer_epoch(None, 7, 10)

    threads = [threading.Thread(target=run) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    final = SkillAdapter("os.delegation_router", "_default", work_dir=work)
    assert _epoch_on_disk(final) == 1 + n_threads * n_epochs
    assert final.state.epoch == 1 + n_threads * n_epochs
    assert final.lock_file.exists()


def test_accepted_version_from_one_adapter_is_seen_by_another(tmp_path: Path) -> None:
    """Reload-inside-lock: a second adapter created BEFORE the first one's
    acceptance still sees (and can roll back to) the version once it acts."""
    work = tmp_path / "skills"
    a = SkillAdapter("os.delegation_router", "_default", work_dir=work)
    b = SkillAdapter("os.delegation_router", "_default", work_dir=work)
    # push `a` into the hypothesis phase with a low baseline
    for _ in range(50):
        a.run_optimizer_epoch(None, 2, 10)
    hyp = ConfigHypothesis(
        hypothesis_id="h1", skill_id="os.delegation_router", param="confidence_threshold",
        delta=-0.05, reason="test", confidence=0.9,
    )
    accepted, _why = a.run_optimizer_epoch(hyp, 9, 10)
    assert accepted
    assert a.get_version_history()[-1].version_id == "v1"
    # `b` was constructed with the stale (epoch 1) state; any mutation reloads first
    b.run_optimizer_epoch(None, 9, 10)
    assert [v.version_id for v in b.get_version_history()] == ["v1"]
    assert b.get_current_config().confidence_threshold == a.get_current_config().confidence_threshold
    assert b.rollback("v1").confidence_threshold == a.get_current_config().confidence_threshold
