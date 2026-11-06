"""Only OPTIONAL background work yields to loop_health.host_saturated(): the KB projector's
cadence and starting a NEW SkillForge fork. Canary rollback (and the learning brake, which
is not wired to the signal at all) always run."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from corvin_console import kb_projection as kp
from corvin_console import loop_health as lh


def test_kb_projector_holds_its_cadence_while_saturated_and_resumes(monkeypatch):
    ticks = []
    monkeypatch.setattr(kp, "sync", lambda t: ticks.append(time.monotonic()))
    monkeypatch.setattr(kp, "periodic", lambda t: {})
    monkeypatch.setattr(kp, "TICK_S", 0.01)
    monkeypatch.setattr(kp, "SATURATED_TICK_S", 3600.0)
    stop = threading.Event()
    busy = {"v": True}
    monkeypatch.setattr(lh, "host_saturated", lambda now=None: busy["v"])
    t = threading.Thread(target=kp._loop, args=("_t", stop), daemon=True)
    t.start()
    deadline = time.monotonic() + 30
    while not ticks and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(1.0)
    assert len(ticks) == 1                       # the first tick, then held
    busy["v"] = False
    deadline = time.monotonic() + 30
    while len(ticks) < 4 and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(5)
    assert len(ticks) >= 4                       # normal cadence resumes


def _autopilot(monkeypatch, tmp_path, *, saturated):
    from corvin_operator.skill_creator import autonomous as ap

    rolled, forked = [], []

    class _Store:
        def list_states(self):
            return [{"status": "canary", "skill": "s1", "traffic_percent": 10}]

        def rollback(self, name, reg, **kw):
            rolled.append((name, kw.get("reason_code")))

    class _Canary:
        @staticmethod
        def evaluate(state):
            return {"decision": "rollback"}

    monkeypatch.setattr(ap, "autopilot_settings", lambda root: {"enabled": True})
    monkeypatch.setattr(ap, "_write_settings", lambda root, s: None)
    monkeypatch.setattr(ap, "_canary_module", lambda: _Canary)
    monkeypatch.setattr(ap, "canary_store", lambda root: _Store())
    monkeypatch.setattr(ap, "registry_for", lambda root: object())
    monkeypatch.setattr(ap, "loss_signals", lambda root: [{"skill_id": "s2"}])
    monkeypatch.setattr(ap, "forks_in_flight", lambda root: False)
    monkeypatch.setattr(lh, "host_saturated", lambda now=None: saturated)
    res = ap.tick(Path(tmp_path), fork_fn=lambda root, sid, **kw: forked.append(sid))
    return res, rolled, forked


def test_saturated_autopilot_still_rolls_back_a_failing_canary_but_defers_a_new_fork(monkeypatch, tmp_path):
    res, rolled, forked = _autopilot(monkeypatch, tmp_path, saturated=True)
    assert rolled == [("s1", "gates_failed")]
    assert forked == [] and res["fork_deferred"] == "host_saturated"


def test_unsaturated_autopilot_forks(monkeypatch, tmp_path):
    res, rolled, forked = _autopilot(monkeypatch, tmp_path, saturated=False)
    assert rolled == [("s1", "gates_failed")] and forked == ["s2"]
    assert "fork_deferred" not in res


def test_the_learning_brake_is_not_wired_to_the_signal():
    src = (Path(__file__).resolve().parents[3] / "core" / "background" / "learning_host.py").read_text(encoding="utf-8")
    assert "host_saturated" not in src
