"""E2E for T-0073 (ADR-2236 D7/D11): the detached ``/task`` worker with background children.

The REAL ``bg_task_worker.py`` runs as a REAL subprocess against the REAL adapter and a
fake claude CLI (``tests/fake_claude.py`` replaying a stream captured from the real
CLI). Unlike ``test_bg_task_worker_supervised.py`` the engine is NOT replaced — that
suite's stub adapter called ``on_status`` with one argument while the real adapter calls
it with ``tool_name=``, which hid a TypeError that silenced every ``/task`` run's
progress relay. The only substitution here is the L44 classifier (it needs a live
model), done by a tiny runner that patches it before running the unmodified worker.

Asserted on files a real process wrote: the outbox the messenger daemons poll, the
progress queue, the completion record, the run record and the children file.
"""
from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests", HERE.parents[1] / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"),
                                reason="POSIX process semantics in the harness")

_RUNNER = '''
import runpy, sys
sys.path.insert(0, {here!r})
import adapter
adapter._house_rules_classifier = lambda task, rules, auth, **kw: ("", 1.0, "test-benign")
sys.argv = [{worker!r}, {spec!r}]
runpy.run_path({worker!r}, run_name="__main__")
'''


@pytest.fixture()
def box(tmp_path, monkeypatch):
    home, outbox = tmp_path / "corvin", tmp_path / "outbox"
    xdg = tmp_path / "xdg"
    (xdg / "corvin-voice").mkdir(parents=True)
    (xdg / "corvin-voice" / "profile.json").write_text("{}")
    home.mkdir()
    outbox.mkdir()
    for k, v in {"CORVIN_HOME": home, "XDG_CONFIG_HOME": xdg, "FORGE_ROOT": tmp_path / "forge",
                 "ADAPTER_INBOX": tmp_path / "inbox", "ADAPTER_OUTBOX": outbox,
                 "VOICE_AUDIT_PATH": tmp_path / "audit.jsonl"}.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_OS_ENGINE", "claude_code")
    import completion_notify as cn
    import task_progress as tp
    import task_supervisor as sup
    for m in (cn, tp, sup):
        importlib.reload(m)
    return {"tmp": tmp_path, "home": home, "outbox": outbox, "cn": cn, "tp": tp, "sup": sup}


def _prepare(box, task_id="bgt_scope"):
    box["cn"].register(task_id, channel="discord", chat_id="123456789012345678",
                       sender="uid42", label="long job")
    box["sup"].register_run(task_id, instruction="do the long thing", channel="discord",
                            chat_key="123456789012345678", sender="uid42",
                            supervise_enabled=True, progress_enabled=True)
    return {"task_id": task_id, "instruction": "do the long thing", "channel": "discord",
            "chat_key": "123456789012345678", "sender": "uid42",
            "outbox_dir": str(box["outbox"])}


def _start(box, spec, fixture, *, speedup=4.0, extra_env=None):
    spec_file = box["tmp"] / f"spec_{spec['task_id']}.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    runner = box["tmp"] / "runner.py"
    runner.write_text(_RUNNER.format(here=str(HERE), worker=str(HERE / "bg_task_worker.py"),
                                     spec=str(spec_file)), encoding="utf-8")
    env = dict(os.environ)
    env.update(kit.fake_env(box["tmp"], fixture, speedup=speedup))
    env.update({
        "ADAPTER_HEARTBEAT_INTERVAL": "0", "ADAPTER_STREAM_IDLE_TIMEOUT": "2",
        "CORVIN_BG_TASK_HEARTBEAT": "1",
        "TP_MIN_INTERVAL": "300",       # a routine update inside this window would coalesce
        "PYTHONPATH": os.pathsep.join([str(HERE), str(HERE.parents[1] / "forge")]),
        **(extra_env or {}),
    })
    return subprocess.Popen([sys.executable, str(runner)], env=env, cwd=str(HERE),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _outbox(box):
    return [(p.name, json.loads(p.read_text())) for p in sorted(box["outbox"].glob("*.json"))]


def _progress_texts(box):
    d = Path(os.environ["CORVIN_HOME"]) / "task_progress"
    out = []
    for p in sorted(d.glob("tp_*.json")) if d.exists() else []:
        try:
            out.append(json.loads(p.read_text()).get("text", ""))
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------------------

def test_interim_wakeups_reach_the_user_in_full_and_the_completion_is_one_final(box):
    spec = _prepare(box)
    p = _start(box, spec, "monitor_3lines")
    p.communicate(timeout=240)
    assert p.returncode == 0
    files = _outbox(box)
    interim = [(n, e) for n, e in files if "_-" in n]
    final = [(n, e) for n, e in files if "_-" not in n and e.get("_final") and e.get("text")]
    texts = [e["text"] for _, e in interim]
    # first answer + 3 per-line wake-ups, none folded into another
    assert len(interim) == 4, [t[:40] for t in texts]
    for needle in ("monitor laeuft", "Tick 1", "Tick 2", "Tick 3"):
        assert any(needle in t for t in texts), needle
    assert all("still running" in t for t in texts)
    assert all("_final" not in e and e["provenance"]["ai_generated"] for _, e in interim)
    assert all(e["chat_id"] == "123456789012345678" for _, e in interim)
    assert len(final) == 1 and "beendet" in final[0][1]["text"].lower()
    # a backlog is read in order: every interim file sorts before the completion
    names = [n for n, _ in files]
    assert max(names.index(n) for n, _ in interim) < names.index(final[0][0])


def test_child_transitions_are_a_status_line_even_inside_the_progress_window(box):
    """The REAL on_status contract: this is the regression guard for the TypeError."""
    spec = _prepare(box)
    p = _start(box, spec, "bash_bg_ok")
    p.communicate(timeout=240)
    assert p.returncode == 0
    texts = _progress_texts(box)
    assert texts, "no progress update at all — the on_status relay is dead again"
    # TP_MIN_INTERVAL=300 would have swallowed routine progress; a child state change is forced
    assert any("Background shell command" in t and "finished" in t for t in texts), texts


def test_the_wall_clock_does_not_cut_a_quiet_child(box):
    """A wall clock of 6 s expires while the captured 8 s child (open from ~2 s to ~10 s of the
    CLI clock) is still running. Before, it fired then and the supervisor 'resumed' by
    starting the child again. (A clock that expires BEFORE any child exists may — and does —
    still fire: it protects against a stuck turn, not against a quiet child.)"""
    spec = _prepare(box)
    p = _start(box, spec, "bash_bg_ok", speedup=1.0,
               extra_env={"CORVIN_BG_TASK_TIMEOUT": "6", "ADAPTER_STREAM_IDLE_TIMEOUT": "30"})
    out, err = p.communicate(timeout=240)
    assert p.returncode == 0, err[-500:]
    # what the user actually receives: the completion envelope in the outbox
    final = [e for n, e in _outbox(box) if "_-" not in n and e.get("_final") and e.get("text")]
    assert len(final) == 1, [n for n, _ in _outbox(box)]
    text = final[0]["text"]
    assert "timed out" not in text and "abgeschlossen" in text.lower(), text
    run = box["sup"].get_run(spec["task_id"])
    assert run is None or run.get("outcome") in (None, "completed"), run
    assert kit.spawn_count(box["tmp"]) == 1, "the prompt (and the child) was started again"


def test_a_killed_worker_leaves_the_lost_children_for_the_resume(box):
    spec = _prepare(box)
    p = _start(box, spec, "bash_bg_ok", speedup=1.0, extra_env={"ADAPTER_STREAM_IDLE_TIMEOUT": "60"})
    # the child starts at ~2 s on the captured clock; wait until the worker has noted it
    deadline = time.time() + 60
    while time.time() < deadline and not box["sup"].read_children(spec["task_id"]):
        time.sleep(0.3)
    lost = box["sup"].read_children(spec["task_id"])
    assert lost and lost[0]["kind"] == "bash", "the worker never recorded its open child"
    assert "Sleep 8" in lost[0]["description"]
    os.killpg(os.getpgid(p.pid), signal.SIGKILL) if os.getpgid(p.pid) != os.getpgid(0) else p.kill()
    p.communicate(timeout=30)
    pidfile = box["tmp"] / "fake.pid"
    if kit.pid_alive(pidfile):
        os.kill(int(pidfile.read_text()), signal.SIGKILL)       # the orphaned fake CLI
    prompt = box["sup"].continuation_prompt(box["sup"].get_run(spec["task_id"]))
    assert "BACKGROUND PROCESSES THAT WERE RUNNING" in prompt
    assert "GONE now" in prompt and "bash" in prompt and "Sleep 8" in prompt


def test_the_children_file_is_gone_after_a_clean_finish_and_after_retire(box):
    spec = _prepare(box)
    p = _start(box, spec, "bash_bg_ok")
    p.communicate(timeout=240)
    assert p.returncode == 0
    assert box["sup"].read_children(spec["task_id"]) == []
    # retire / Art. 17 purge remove it as well
    box["sup"].write_children(spec["task_id"], [{"kind": "bash", "age_s": 5, "description": "x"}])
    box["sup"]._cleanup_run_artifacts(spec["task_id"])
    assert box["sup"].read_children(spec["task_id"]) == []
