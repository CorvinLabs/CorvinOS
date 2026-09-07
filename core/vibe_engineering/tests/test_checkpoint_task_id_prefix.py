"""R3-B4: a checkpoint lookup must not match a SIBLING task by filename prefix.

Checkpoints are stored as ``{task_id}_{checkpoint_id}_{iter}.json`` and
``list_checkpoints`` globbed ``{task_id}_*.json``. ``_`` is a legal task-id
character, so task ``build`` matched every ``build_docs_*`` file. ``load()``
verifies tenant, merkle root and signature — all of which the sibling's own
valid checkpoint passes — but never the task identity. Two live consequences,
both reproduced below through the real caller (``VibeOrchestrator``):

* ``resume_from_checkpoint("build")`` resumed *build_docs*' state, i.e. a long
  autonomous run continued from another task's context;
* ``delete_old_checkpoints("build", keep_count=1)`` deleted *build*'s only
  checkpoint while keeping *build_docs*'.
"""

from __future__ import annotations

import pytest

from core.vibe_engineering.vibe_orchestrator import VibeOrchestrator

TENANT = "_default"


def _write(manager, task_id: str, iteration: int):
    checkpoint = manager.create_checkpoint(
        task_id=task_id,
        session_id=f"sess_{task_id}",
        phase="build",
        trigger="manual",
        iteration_num=iteration,
        task_state={"task_id": task_id, "iteration": iteration},
        context_essentials={"task_id": task_id},
        learning_state={},
        open_subgoals=[],
        artifacts=[],
    )
    manager.save(checkpoint)
    return checkpoint


@pytest.fixture
def orchestrator(tmp_path):
    return VibeOrchestrator(checkpoint_dir=tmp_path, tenant_id=TENANT)


class TestPrefixGlobCannotCrossTasks:
    def test_listing_a_task_never_returns_a_sibling_prefixed_task(self, orchestrator):
        manager = orchestrator.checkpoint_manager
        _write(manager, "build", iteration=1)
        for i in (5, 6, 7):
            _write(manager, "build_docs", iteration=i)

        listed = orchestrator.list_task_checkpoints("build")
        assert [m.task_id for m in listed] == ["build"], listed
        assert len(listed) == 1

        listed_sibling = orchestrator.list_task_checkpoints("build_docs")
        assert {m.task_id for m in listed_sibling} == {"build_docs"}
        assert len(listed_sibling) == 3

    def test_resume_never_loads_a_sibling_tasks_checkpoint(self, orchestrator):
        manager = orchestrator.checkpoint_manager
        _write(manager, "build", iteration=1)
        _write(manager, "build_docs", iteration=7)

        state = orchestrator.resume_from_checkpoint("build")
        assert state is not None
        # Before the fix this resumed build_docs iteration 7.
        assert state.task_id == "build"
        assert state.iteration_num == 1
        assert state.session_id == "sess_build"

    def test_cleanup_never_deletes_a_sibling_tasks_checkpoint(self, orchestrator, tmp_path):
        manager = orchestrator.checkpoint_manager
        _write(manager, "build", iteration=1)
        for i in (5, 6, 7):
            _write(manager, "build_docs", iteration=i)

        manager.delete_old_checkpoints("build", keep_count=1)

        # build keeps its only checkpoint; build_docs is untouched.
        assert len(manager.list_checkpoints("build")) == 1
        assert len(manager.list_checkpoints("build_docs")) == 3
        assert len(list(tmp_path.glob("*.json"))) == 4

    def test_cleanup_of_the_prefixed_task_is_still_correct(self, orchestrator):
        manager = orchestrator.checkpoint_manager
        _write(manager, "build", iteration=1)
        for i in (5, 6, 7):
            _write(manager, "build_docs", iteration=i)

        manager.delete_old_checkpoints("build_docs", keep_count=1)

        remaining = manager.list_checkpoints("build_docs")
        assert [m.iteration_num for m in remaining] == [7]
        assert len(manager.list_checkpoints("build")) == 1
