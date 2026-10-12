"""Epic I-11 adversarial review round 2 - skills core (R2-SC-08/09/10/12)."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import pytest

from core.learning import event_emitter as em_mod
from core.learning.event_emitter import EventEmitter
from core.learning.learning_events import EventType, LearningEvent


def _ev(i: int) -> LearningEvent:
    return LearningEvent(
        event_id=f"00000000-0000-4000-8000-{i:012d}", event_type=list(EventType)[0],
        skill_id="os.x", tenant_id="_default", timestamp=datetime.now(timezone.utc).isoformat())


class _Dies:
    def write_event(self, e):
        raise SystemExit("store called sys.exit")


class _Stuck:
    def __init__(self):
        self.release = threading.Event()

    def write_event(self, e):
        self.release.wait()


class TestEmitterR2SC08:
    def test_dead_worker_refuses_and_counts_the_loss(self):
        em = EventEmitter(_Dies(), queue_size=100)
        accepted = sum(em.emit(_ev(i)) for i in range(6))
        em._worker_thread.join(timeout=5)
        assert not em._worker_thread.is_alive()
        assert not any(em.emit(_ev(10 + i)) for i in range(4))  # refused, not queued for nobody
        em_mod._flush_all([em], 1.0)
        # 6 accepted, 1 reached the store (and killed the worker), 5 never written
        assert accepted == 6 and em.lost_at_exit == 5


class TestEmitterR2SC09:
    def test_one_shared_deadline_for_all_emitters(self):
        stores = [_Stuck() for _ in range(3)]
        ems = [EventEmitter(s, queue_size=4) for s in stores]
        for em in ems:
            for i in range(3):
                em.emit(_ev(i))
        time.sleep(0.1)
        t = time.monotonic()
        em_mod._flush_all(ems, 1.0)
        took = time.monotonic() - t
        for s in stores:
            s.release.set()
        assert took < 1.8, f"exit waited {took:.2f}s for three emitters - deadline per emitter"
        assert all(em.lost_at_exit == 3 for em in ems)

    def test_exactly_one_module_exit_hook(self):
        import atexit  # noqa: F401
        src = open(em_mod.__file__).read()
        assert src.count("atexit.register(") == 1 and "atexit.register(_flush_all)" in src


class TestCreatorWalR2SC10:
    def _orch(self, tmp_path):
        from core.skills.os_skills.creator_2_0.phase_model import PhaseModelOrchestrator
        return PhaseModelOrchestrator(tenant_home=tmp_path)

    def _entries(self, orch):
        if not orch.wal.wal_file.exists():
            return []
        return [json.loads(l) for l in orch.wal.wal_file.read_text().splitlines() if l.strip()]

    def test_failure_in_intake_marks_the_placeholder_entry(self, tmp_path):
        orch = self._orch(tmp_path)
        orch.phases[0].execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        orch.execute("make a skill")
        ents = self._entries(orch)
        assert ents and all(e["skill_id"].startswith("pending_") for e in ents)
        assert any(e.get("error") and "boom" in e["error"] for e in ents)

    def test_failure_after_intake_marks_inflight_and_clears_placeholder(self, tmp_path):
        orch = self._orch(tmp_path)
        orch.phases[1].execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("late"))
        state = orch.execute("make a skill that greets people")
        ents = self._entries(orch)
        assert not any(e["skill_id"].startswith("pending_") for e in ents)
        failed = [e for e in ents if e.get("error")]
        assert failed and failed[0]["skill_id"] == state.skill_id and failed[0]["phase_num"] == 1


class TestDependencyListR2SC12:
    @pytest.mark.parametrize("raw", ["os.a", {"name": "os.a"}, 7])
    def test_non_list_is_unreadable(self, raw):
        from core.skills.skill_dag_loader import dependency_list
        with pytest.raises(TypeError):
            dependency_list(raw)

    def test_none_and_list(self):
        from core.skills.skill_dag_loader import dependency_list
        assert dependency_list(None) == []
        assert [d.name for d in dependency_list(["os.a", {"name": "os.b"}])] == ["os.a", "os.b"]

    def test_toposort_refuses_a_string_declaration(self):
        from types import SimpleNamespace
        from core.skills.skill_dag_loader import topological_sort_skills
        meta = SimpleNamespace(id="os.x", depends_on="os.a")
        reg = SimpleNamespace(_metadata_by_id={"os.x": meta}, _skills={})
        out, err = topological_sort_skills([SimpleNamespace(metadata=meta)], reg)
        assert out == [] and "Unreadable dependency declaration" in (err or "")
