"""The 24 h background-task defaults must be what a REAL detached worker actually runs with.

The 24 h change shipped with no test that read a default anywhere: the existing worker E2E
sets ``CORVIN_BG_TASK_TIMEOUT=6`` explicitly, so a regression of the default itself (or of
the ``setdefault`` lines in ``main()``) would have stayed green. Two layers here:

* the pure rules of ``task_timeout_s`` and the agreement of the three default constants;
* a real ``bg_task_worker.py`` subprocess with none of the three variables set, whose engine
  child (the fake claude CLI behind a wrapper) reports the environment it inherited.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests", HERE.parents[1] / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bg_scope  # noqa: E402
import bg_task_worker  # noqa: E402
import task_supervisor  # noqa: E402
import bgscope_testkit as kit  # noqa: E402
from test_bg_scope_task_worker_e2e import _RUNNER, _outbox, _prepare, _start, box  # noqa: E402,F401

_VARS = ("CORVIN_BG_TASK_TIMEOUT", "CORVIN_BG_CHILD_MAX", "ADAPTER_TOOL_IDLE_TIMEOUT",
         "ADAPTER_STREAM_IDLE_TIMEOUT")


# --- the pure rules --------------------------------------------------------------------

def test_the_defaults_agree_and_sit_inside_the_child_cap_bounds(monkeypatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)
    assert bg_task_worker.task_timeout_s() == 86400.0
    assert bg_scope.child_max_s() == 86400.0
    assert bg_task_worker.DEFAULT_TASK_TIMEOUT_S == bg_scope.WORKER_CHILD_MAX_S == bg_scope.CHILD_MAX_DEFAULT_S
    assert bg_scope.WORKER_CHILD_MAX_S <= bg_scope._CHILD_MAX_BOUNDS[1], \
        "the default must not exceed the clamp ceiling it is passed through"


def test_the_supervisor_total_budget_fits_a_full_attempt_plus_a_resume(monkeypatch):
    """At 6 h a run killed by a restart after hour 7 was failed for good, not resumed."""
    monkeypatch.delenv("SUP_TOTAL_BUDGET", raising=False)
    import importlib
    importlib.reload(task_supervisor)
    assert task_supervisor.SUP_TOTAL_BUDGET >= 2 * bg_task_worker.DEFAULT_TASK_TIMEOUT_S


@pytest.mark.parametrize("raw,expected", [
    ("21600", 21600.0),       # an operator's explicit 6 h wins
    ("6", 6.0),               # tests use small values
    ("  3600  ", 3600.0),
])
def test_an_explicit_timeout_is_honoured(monkeypatch, raw, expected):
    monkeypatch.setenv("CORVIN_BG_TASK_TIMEOUT", raw)
    assert bg_task_worker.task_timeout_s() == expected


@pytest.mark.parametrize("raw,expected", [
    ("1e30", 86400.0),      # never fired, like inf
    ("604800", 86400.0),    # a week: above the 24 h ceiling every child cap already has
    ("2", 2.0),             # the smallest value the 2 s deadline loop can honour
])
def test_a_usable_timeout_is_clamped_to_at_most_24_hours(monkeypatch, raw, expected):
    monkeypatch.setenv("CORVIN_BG_TASK_TIMEOUT", raw)
    assert bg_task_worker.task_timeout_s() == expected


@pytest.mark.parametrize("raw", ["0", "-5", "nan", "inf", "-inf", "abc", "", "   ",
                                  "1e-9", "0.0001", "0.5", "1.9", "1e-400"])
def test_an_unusable_timeout_falls_back_to_the_default_instead_of_killing_or_unbounding(monkeypatch, raw):
    """0 made the deadline loop end every task two seconds in; nan/inf removed the bound."""
    monkeypatch.setenv("CORVIN_BG_TASK_TIMEOUT", raw)
    assert bg_task_worker.task_timeout_s() == 86400.0


# --- a real worker process -------------------------------------------------------------

def test_a_real_worker_gives_its_engine_child_the_24h_defaults(box, monkeypatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)
    spec = _prepare(box, "bgt_defaults")

    # The wrapper sits between the worker's engine spawn and the fake CLI and records
    # what the engine process inherited.
    launcher = kit.make_fake_claude_bin(box["tmp"] / "fake-bin")
    dump = box["tmp"] / "engine_env.txt"
    wrapper = box["tmp"] / "env-wrapper.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"env | grep -E '^(CORVIN_BG_|ADAPTER_TOOL_IDLE)' | sort > '{dump}'\n"
        f"exec '{launcher}' \"$@\"\n", encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    p = _start(box, spec, "plain_no_children", speedup=8.0,
               extra_env={"CORVIN_CLAUDE_BIN": str(wrapper)})
    out, err = p.communicate(timeout=240)
    assert p.returncode == 0, (out[-400:], err[-400:])

    assert dump.exists(), "the engine child never ran, so nothing was proven"
    inherited = dict(line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line)
    assert inherited.get("CORVIN_BG_CHILD_MAX") == "86400", inherited
    assert inherited.get("ADAPTER_TOOL_IDLE_TIMEOUT") == "86400", inherited
    # Neither was set by the test process, so both values came from the worker's main().
    assert not any(os.environ.get(v) for v in _VARS)


# --- the stream-idle default (the limit that still cut a silent worker) ----------------

class _Adapter:
    def __init__(self, settings):
        self._s = settings
        self.asked = []

    def _load_channel_settings(self, channel):
        self.asked.append(channel)
        if isinstance(self._s, Exception):
            raise self._s
        return self._s


def test_stream_idle_default_applies_only_when_nobody_chose(monkeypatch):
    monkeypatch.delenv("ADAPTER_STREAM_IDLE_TIMEOUT", raising=False)
    assert bg_task_worker.apply_stream_idle_default(_Adapter({}), "c") is True
    assert os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] == "3600"


def test_an_operators_environment_value_wins(monkeypatch):
    monkeypatch.setenv("ADAPTER_STREAM_IDLE_TIMEOUT", "120")
    assert bg_task_worker.apply_stream_idle_default(_Adapter({}), "c") is False
    assert os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] == "120"


@pytest.mark.parametrize("configured", [7200, 0, 600])
def test_a_channels_own_setting_wins_including_zero_which_disables_the_watchdog(monkeypatch, configured):
    monkeypatch.delenv("ADAPTER_STREAM_IDLE_TIMEOUT", raising=False)
    assert bg_task_worker.apply_stream_idle_default(
        _Adapter({"stream_idle_timeout_seconds": configured}), "c") is False
    assert "ADAPTER_STREAM_IDLE_TIMEOUT" not in os.environ   # the adapter reads the channel value itself


def test_unreadable_channel_settings_count_as_unset(monkeypatch):
    monkeypatch.delenv("ADAPTER_STREAM_IDLE_TIMEOUT", raising=False)
    assert bg_task_worker.apply_stream_idle_default(_Adapter(OSError("gone")), "c") is True


def _start_without_forced_idle(box, spec, fixture, *, speedup=1.0, extra_env=None):
    """Like test_bg_scope_task_worker_e2e._start, but WITHOUT its forced ADAPTER_STREAM_IDLE_TIMEOUT=2."""
    import json
    import subprocess
    spec_file = box["tmp"] / f"spec_{spec['task_id']}.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    runner = box["tmp"] / "runner.py"
    runner.write_text(_RUNNER.format(here=str(HERE), worker=str(HERE / "bg_task_worker.py"),
                                     spec=str(spec_file)), encoding="utf-8")
    env = dict(os.environ)
    env.pop("ADAPTER_STREAM_IDLE_TIMEOUT", None)
    env.update(kit.fake_env(box["tmp"], fixture, speedup=speedup))
    env.update({"ADAPTER_HEARTBEAT_INTERVAL": "0", "CORVIN_BG_TASK_HEARTBEAT": "1", "TP_MIN_INTERVAL": "300",
                "PYTHONPATH": os.pathsep.join([str(HERE), str(HERE.parents[1] / "forge")]),
                **(extra_env or {})})
    return subprocess.Popen([sys.executable, str(runner)], env=env, cwd=str(HERE),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_a_real_worker_runs_its_engine_with_a_one_hour_stream_idle_limit(box, monkeypatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)
    spec = _prepare(box, "bgt_idle_default")
    spec["channel"] = "sandbox-no-channel-settings"       # the live discord channel sets 3600 itself
    launcher = kit.make_fake_claude_bin(box["tmp"] / "fake-bin")
    dump = box["tmp"] / "engine_env_idle.txt"
    wrapper = box["tmp"] / "env-wrapper-idle.sh"
    wrapper.write_text("#!/bin/sh\n"
                       f"env | grep -E '^ADAPTER_STREAM_IDLE' | sort > '{dump}'\n"
                       f"exec '{launcher}' \"$@\"\n", encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    p = _start_without_forced_idle(box, spec, "plain_no_children", speedup=8.0,
                                   extra_env={"CORVIN_CLAUDE_BIN": str(wrapper)})
    out, err = p.communicate(timeout=240)
    assert p.returncode == 0, (out[-300:], err[-300:])
    assert dump.read_text().strip() == "ADAPTER_STREAM_IDLE_TIMEOUT=3600"


def test_a_worker_cut_off_by_the_idle_watchdog_is_a_failed_attempt_not_a_finished_task(box, monkeypatch):
    """Measured before the fix: the apology text came back as an answer, ok stayed True, the run
    state was 'done' after one attempt and the user was told '✅ long job finished'."""
    import json
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)
    evs = kit.load_fixture("plain_no_children")
    for e in evs:
        if e["type"] != "system":
            e["_t"] = e["_t"] + 9.0                          # the CLI goes silent after init
    fx = kit.write_fixture(box["tmp"] / "fx", "silent_think", evs)
    spec = _prepare(box, "bgt_silent")
    p = _start_without_forced_idle(box, spec, fx, speedup=1.0,
                                   extra_env={"ADAPTER_STREAM_IDLE_TIMEOUT": "3"})
    out, err = p.communicate(timeout=240)
    assert p.returncode == 0, (out[-300:], err[-300:])
    run = box["sup"].get_run(spec["task_id"])
    assert run["state"] != "done", run
    last = run["attempt_log"][-1]
    assert last["ok"] is False and last.get("resumable") is True, last
    assert "no events for too long" in last["summary"], last
    texts = " ".join(str(e.get("text", "")) for _, e in _outbox(box))
    assert "long job finished" not in texts, texts


def test_the_channel_settings_are_read_for_the_workers_own_channel(monkeypatch):
    monkeypatch.delenv("ADAPTER_STREAM_IDLE_TIMEOUT", raising=False)
    fake = _Adapter({"stream_idle_timeout_seconds": 7200})
    bg_task_worker.apply_stream_idle_default(fake, "telegram")
    assert fake.asked == ["telegram"]


@pytest.mark.parametrize("exc", [OSError("gone"), ValueError("bad json"), KeyError("x"), RuntimeError("boom")])
def test_any_error_reading_the_channel_settings_counts_as_unset(monkeypatch, exc):
    monkeypatch.delenv("ADAPTER_STREAM_IDLE_TIMEOUT", raising=False)
    assert bg_task_worker.apply_stream_idle_default(_Adapter(exc), "c") is True


@pytest.mark.parametrize("junk", ["", "1h", "abc", [], {}])
def test_a_channel_value_the_adapter_cannot_parse_is_not_an_operator_choice(monkeypatch, junk):
    """The adapter falls back to 300 s on these; treating them as 'chosen' kept the cut."""
    monkeypatch.delenv("ADAPTER_STREAM_IDLE_TIMEOUT", raising=False)
    assert bg_task_worker.apply_stream_idle_default(
        _Adapter({"stream_idle_timeout_seconds": junk}), "c") is True


# --- how a cut or failed turn is read from the adapter ----------------------------------

class _Outcome:
    def __init__(self, **kw):
        self.timed_out = False
        self.failed = None
        self.__dict__.update(kw)


class _A:
    _ENGINE_FAILURE_REASONS = frozenset({"engine_error", "timeout", "spawn_timeout"})

    def __init__(self, **kw):
        self._TURN_OUTCOME = _Outcome(**kw)


@pytest.mark.parametrize("kw,expected", [
    ({}, None),
    ({"timed_out": True}, "StreamIdleTimeout"),                 # claude path
    ({"failed": "timeout"}, "StreamIdleTimeout"),               # codex / opencode path
    ({"failed": "engine_error"}, "EngineError"),                # incl. the after-background-work branch
    ({"failed": "spawn_timeout"}, "EngineError"),
    ({"failed": "cancelled"}, None),                            # an operator /cancel is not a failure
    ({"failed": "l44_refused"}, None),                          # a gate refusal is an answer
])
def test_engine_cut_reason_reads_the_adapters_own_verdict(kw, expected):
    assert bg_task_worker.engine_cut_reason(_A(**kw)) == expected


def test_an_adapter_without_a_turn_outcome_is_not_called_cut():
    assert bg_task_worker.engine_cut_reason(object()) is None


# --- real worker processes: the cut must be a failed attempt, whatever shape it takes ---------

def _run_record(box, spec):
    return box["sup"].get_run(spec["task_id"])


def test_an_idle_cut_after_a_background_child_is_a_failed_attempt(box, monkeypatch):
    """Reviewer repro: the adapter's 'error after background work started' branch sets only
    failed='engine_error' (never timed_out); the run was recorded 'done' with '✅ long job finished'."""
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)
    evs = kit.load_fixture("bash_bg_ok")
    for e in evs:
        if e.get("_t", 0) >= 10.05:                  # the wake-up turn after the child ended goes silent
            e["_t"] += 9.0
    fx = kit.write_fixture(box["tmp"] / "fx", "silent_wakeup", evs)
    spec = _prepare(box, "bgt_silent_wake")
    p = _start_without_forced_idle(box, spec, fx, speedup=1.0, extra_env={"ADAPTER_STREAM_IDLE_TIMEOUT": "3"})
    out, err = p.communicate(timeout=240)
    assert p.returncode == 0, (out[-300:], err[-300:])
    run = _run_record(box, spec)
    assert run["state"] != "done", run
    last = run["attempt_log"][-1]
    assert last["ok"] is False and last.get("resumable") is True, last
    assert "the engine reported an error" in last["summary"], last
    assert "long job finished" not in " ".join(str(e.get("text", "")) for _, e in _outbox(box))


def test_a_stale_failure_flag_from_before_the_call_does_not_fail_a_healthy_run(box, monkeypatch):
    """The worker resets the adapter's turn outcome before its only call; a stale True must not leak in."""
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)
    spec = _prepare(box, "bgt_stale_flag")
    spec_file = box["tmp"] / "spec_stale.json"
    import json
    import subprocess
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    runner = box["tmp"] / "runner_stale.py"
    runner.write_text(_RUNNER.format(here=str(HERE), worker=str(HERE / "bg_task_worker.py"),
                                     spec=str(spec_file)).replace(
        "import adapter\n", "import adapter\nadapter._TURN_OUTCOME.timed_out = True\nadapter._TURN_OUTCOME.failed = 'engine_error'\n", 1),
        encoding="utf-8")
    env = dict(os.environ)
    env.update(kit.fake_env(box["tmp"], "plain_no_children", speedup=8.0))
    env.update({"ADAPTER_HEARTBEAT_INTERVAL": "0", "CORVIN_BG_TASK_HEARTBEAT": "1", "TP_MIN_INTERVAL": "300",
                "PYTHONPATH": os.pathsep.join([str(HERE), str(HERE.parents[1] / "forge")])})
    env.pop("ADAPTER_STREAM_IDLE_TIMEOUT", None)
    proc = subprocess.run([sys.executable, str(runner)], env=env, cwd=str(HERE), capture_output=True,
                          text=True, timeout=240)
    assert proc.returncode == 0, proc.stderr[-300:]
    last = _run_record(box, spec)["attempt_log"][-1]
    assert last["ok"] is True, last
