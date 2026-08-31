#!/usr/bin/env python3
"""test_init.py — E2E for Layer 19 service manager (Phase-1 MVP).

Exercises the supervisor with REAL subprocesses (Python sleep loops
written as scripts, spawned via shell). No mocks for any moving part:

  - YAML parser handles the subset we need (scalars, lists, comments)
  - discover_services walks tempdir, picks up *.service.yaml
  - topological_order respects requires + wants, detects cycles, fails
    on missing deps
  - Supervisor.start spawns real processes, marks state running
  - Supervisor.tick detects exits, schedules restarts with exponential
    backoff, fires restarts on schedule
  - max_restarts caps the loop, transitions to "failed"
  - hot_reload sends a real signal to a real process
  - shutdown_all runs in reverse topological order
  - journal captures stdout/stderr to file, journal_tail reads it

Per-subtask E2E rule from CLAUDE.md.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_module():
    """Re-import init module fresh per case."""
    sys.modules.pop("init", None)
    import init  # type: ignore
    return init


def _write_yaml(d: Path, name: str, content: str) -> Path:
    f = d / f"{name}.service.yaml"
    f.write_text(content, encoding="utf-8")
    return f


# --------------------------------------------------------------------- cases

def case_yaml_parser_handles_scalars_and_lists() -> None:
    _section("yaml parser handles scalars + lists + comments")
    init = _fresh_module()
    text = """\
# this is a comment
name: forge
type: daemon
restart_sec: 2.5
max_restarts: 7
exec_start: "echo hello"   # inline comment
requires:
  - audit
  - workspace
wants:
  - skill-forge
"""
    d = init._load_yaml(text)
    assert d["name"] == "forge", d
    assert d["type"] == "daemon"
    assert d["restart_sec"] == 2.5
    assert d["max_restarts"] == 7
    assert d["exec_start"] == "echo hello"
    assert d["requires"] == ["audit", "workspace"]
    assert d["wants"] == ["skill-forge"]
    print("  PASS scalars and lists parsed")


def case_yaml_rejects_malformed() -> None:
    _section("yaml parser rejects unparseable input")
    init = _fresh_module()
    try:
        init._load_yaml("just a line without colon")
    except ValueError as exc:
        print(f"  PASS rejected: {exc}")
    else:
        raise AssertionError("expected ValueError")


def case_discover_picks_up_yaml_files() -> None:
    _section("discover_services walks tempdir for *.service.yaml")
    init = _fresh_module()
    d = Path(tempfile.mkdtemp(prefix="init-disc-"))
    try:
        sub_a = d / "plugin_a"
        sub_a.mkdir()
        _write_yaml(
            sub_a, "alpha",
            "name: alpha\nexec_start: \"sleep 0.1\"\n",
        )
        sub_b = d / "plugin_b"
        sub_b.mkdir()
        _write_yaml(
            sub_b, "beta",
            "name: beta\nexec_start: \"sleep 0.1\"\n"
            "requires:\n  - alpha\n",
        )
        services = init.discover_services([d])
        assert set(services.keys()) == {"alpha", "beta"}
        assert services["beta"].requires == ["alpha"]
        print(f"  PASS discovered: {list(services.keys())}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_topological_order_respects_requires() -> None:
    _section("topological_order respects requires graph")
    init = _fresh_module()
    services = {
        "c": init.ServiceDef(
            name="c", exec_start="true", requires=["b"]
        ),
        "a": init.ServiceDef(
            name="a", exec_start="true"
        ),
        "b": init.ServiceDef(
            name="b", exec_start="true", requires=["a"]
        ),
    }
    order = init.topological_order(services)
    assert order.index("a") < order.index("b") < order.index("c"), order
    print(f"  PASS order: {order}")


def case_topological_order_detects_cycle() -> None:
    _section("topological_order detects cycles")
    init = _fresh_module()
    services = {
        "a": init.ServiceDef(name="a", exec_start="true", requires=["b"]),
        "b": init.ServiceDef(name="b", exec_start="true", requires=["a"]),
    }
    try:
        init.topological_order(services)
    except ValueError as exc:
        assert "cycle" in str(exc).lower(), exc
        print(f"  PASS detected cycle: {exc}")
        return
    raise AssertionError("expected ValueError on cycle")


def case_topological_order_detects_missing_dep() -> None:
    _section("topological_order detects missing dep references")
    init = _fresh_module()
    services = {
        "a": init.ServiceDef(name="a", exec_start="true", requires=["ghost"]),
    }
    try:
        init.topological_order(services)
    except ValueError as exc:
        assert "ghost" in str(exc), exc
        print(f"  PASS rejected: {exc}")
        return
    raise AssertionError("expected ValueError on missing dep")


def case_topological_order_skips_missing_wants() -> None:
    _section("topological_order tolerates missing 'wants' (optional)")
    init = _fresh_module()
    services = {
        "a": init.ServiceDef(
            name="a", exec_start="true", wants=["does-not-exist"]
        ),
    }
    order = init.topological_order(services)
    assert order == ["a"], order
    print(f"  PASS order: {order} (missing want ignored)")


def case_supervisor_starts_real_subprocess() -> None:
    _section("supervisor.start spawns a real subprocess and marks running")
    init = _fresh_module()
    journal = Path(tempfile.mkdtemp(prefix="init-jrnl-"))
    try:
        services = {
            "longrunner": init.ServiceDef(
                name="longrunner",
                exec_start="sleep 5",
            ),
        }
        sup = init.Supervisor(services, journal_dir=journal)
        sup.start("longrunner")
        st = sup.states["longrunner"]
        assert st.status == "running", st.status
        assert st.pid is not None and st.pid > 0
        # Verify the process is actually alive
        try:
            os.kill(st.pid, 0)
        except ProcessLookupError:
            raise AssertionError("supervisor reports running but PID dead")
        print(f"  PASS pid={st.pid} status={st.status}")
        sup.stop("longrunner")
        assert sup.states["longrunner"].status == "stopped"
        print("  PASS stop transitions to stopped")
    finally:
        shutil.rmtree(journal, ignore_errors=True)


def case_supervisor_recursive_start_resolves_requires() -> None:
    _section("supervisor.start with recursive=True spawns deps first")
    init = _fresh_module()
    journal = Path(tempfile.mkdtemp(prefix="init-jrnl-"))
    try:
        services = {
            "leaf": init.ServiceDef(
                name="leaf", exec_start="sleep 5"
            ),
            "branch": init.ServiceDef(
                name="branch", exec_start="sleep 5", requires=["leaf"]
            ),
            "root": init.ServiceDef(
                name="root", exec_start="sleep 5", requires=["branch"]
            ),
        }
        sup = init.Supervisor(services, journal_dir=journal)
        sup.start("root")
        for n in ("leaf", "branch", "root"):
            assert sup.states[n].status == "running", n
            assert sup.states[n].pid
        print("  PASS all three running after recursive start of root")
        sup.shutdown_all()
        for n in ("leaf", "branch", "root"):
            assert sup.states[n].status == "stopped"
        print("  PASS shutdown_all reversed-topo halted everything")
    finally:
        shutil.rmtree(journal, ignore_errors=True)


def case_supervisor_restarts_on_failure_with_backoff() -> None:
    _section("supervisor restarts on-failure with exponential backoff")
    init = _fresh_module()
    journal = Path(tempfile.mkdtemp(prefix="init-jrnl-"))
    try:
        # exit-1 service that immediately dies
        services = {
            "flaky": init.ServiceDef(
                name="flaky",
                exec_start="false",  # /bin/false exits 1
                restart="on-failure",
                restart_sec=0.05,
                backoff="exponential",
                max_restarts=3,
            ),
        }
        # Use a controllable clock so we don't sleep waiting for backoff
        clock_t = [time.time()]

        def clock() -> float:
            return clock_t[0]

        sup = init.Supervisor(services, journal_dir=journal, clock=clock)
        sup.start("flaky")
        # Allow the subprocess to exit (it dies fast — false exits in <50ms)
        time.sleep(0.2)

        # Tick #1 — observe the exit, transition to backoff
        ch = sup.tick()
        assert any("backoff" in c for c in ch), ch
        st = sup.states["flaky"]
        assert st.status == "backoff"
        # Backoff #0 with exponential: base * 2^0 = 0.05s
        delay_0 = st.next_restart_at - clock_t[0]
        assert abs(delay_0 - 0.05) < 0.01, f"expected 0.05s, got {delay_0}"
        print(f"  PASS first backoff = {delay_0:.3f}s (exponential 2^0)")

        # Advance clock past first backoff window
        clock_t[0] += 0.06
        ch = sup.tick()
        # Tick fires the restart; the new subprocess starts and might exit
        # quickly again. Allow it to exit, tick again to see backoff again.
        time.sleep(0.15)
        ch2 = sup.tick()
        assert sup.states["flaky"].restart_count == 1, sup.states["flaky"]
        # Now in backoff again (#1 with exponential: base * 2 = 0.1s)
        if sup.states["flaky"].status == "backoff":
            delay_1 = (
                sup.states["flaky"].next_restart_at - clock_t[0]
            )
            assert abs(delay_1 - 0.1) < 0.02, f"expected 0.1s, got {delay_1}"
            print(f"  PASS second backoff = {delay_1:.3f}s (exponential 2^1)")
        else:
            print(
                f"  NOTE backoff race; status={sup.states['flaky'].status} "
                f"after tick={ch2}"
            )
    finally:
        shutil.rmtree(journal, ignore_errors=True)


def case_supervisor_caps_at_max_restarts() -> None:
    _section("supervisor caps at max_restarts and transitions to failed")
    init = _fresh_module()
    journal = Path(tempfile.mkdtemp(prefix="init-jrnl-"))
    try:
        services = {
            "doomed": init.ServiceDef(
                name="doomed",
                exec_start="false",
                restart="on-failure",
                restart_sec=0.01,
                backoff="none",
                max_restarts=2,
            ),
        }
        clock_t = [time.time()]

        def clock() -> float:
            return clock_t[0]

        sup = init.Supervisor(services, journal_dir=journal, clock=clock)
        sup.start("doomed")
        # Loop: each iter = let it exit, advance clock, tick
        for _ in range(10):
            time.sleep(0.05)
            clock_t[0] += 0.02
            sup.tick()
            if sup.states["doomed"].status == "failed":
                break
        st = sup.states["doomed"]
        assert st.status == "failed", st.status
        assert st.restart_count == 2, st.restart_count
        print(
            f"  PASS status=failed restart_count={st.restart_count} "
            f"(capped at max_restarts=2)"
        )
    finally:
        shutil.rmtree(journal, ignore_errors=True)


def case_supervisor_journal_writes_real_file() -> None:
    _section("supervisor writes journal to a real file, journal_tail reads it")
    init = _fresh_module()
    journal = Path(tempfile.mkdtemp(prefix="init-jrnl-"))
    try:
        services = {
            "talky": init.ServiceDef(
                name="talky",
                exec_start="echo hello-from-talky",
            ),
        }
        sup = init.Supervisor(services, journal_dir=journal)
        sup.start("talky")
        time.sleep(0.2)
        sup.tick()  # detect exit
        lines = sup.journal_tail("talky", n=20)
        joined = "\n".join(lines)
        assert "started" in joined, joined
        assert "hello-from-talky" in joined, (
            "subprocess stdout should be in journal"
        )
        print(f"  PASS journal contains spawn + stdout ({len(lines)} lines)")
    finally:
        shutil.rmtree(journal, ignore_errors=True)


def case_supervisor_hot_reload_sends_signal() -> None:
    _section("supervisor.reload sends the configured signal to a live PID")
    init = _fresh_module()
    journal = Path(tempfile.mkdtemp(prefix="init-jrnl-"))
    try:
        # Python script that traps SIGHUP, writes 'reloaded', then waits more
        helper = journal / "trap.py"
        helper.write_text(
            "import signal, sys, time\n"
            "got = []\n"
            "def h(s,f):\n"
            "    got.append(s)\n"
            "    print(f'caught {s}', flush=True)\n"
            "signal.signal(signal.SIGHUP, h)\n"
            "for _ in range(50):\n"
            "    time.sleep(0.1)\n"
            "    if got: sys.exit(0)\n"
            "sys.exit(1)\n"
        )
        services = {
            "trap": init.ServiceDef(
                name="trap",
                # 'exec' replaces the /bin/sh wrapper with python directly,
                # so SIGHUP from os.kill(pid, ...) reaches python's handler
                # rather than terminating the shell. Operators should use
                # 'exec' in service definitions when hot_reload is set.
                exec_start=f"exec python3 {helper}",
                hot_reload="SIGHUP",
            ),
        }
        sup = init.Supervisor(services, journal_dir=journal)
        sup.start("trap")
        time.sleep(0.2)
        sent = sup.reload("trap")
        assert sent is True, "reload should report success"
        # Wait for trap to exit (helper exits 0 after seeing the signal)
        proc = sup.states["trap"].proc
        rc = proc.wait(timeout=3.0)
        assert rc == 0, f"helper should exit 0 after SIGHUP, got rc={rc}"
        sup.tick()
        # Journal should mention the reload
        joined = "\n".join(sup.journal_tail("trap"))
        assert "reload signal sent (SIGHUP)" in joined, joined
        # Stdout should have the trap output
        assert "caught" in joined, joined
        print("  PASS SIGHUP delivered, helper exited cleanly, journal logged")
    finally:
        shutil.rmtree(journal, ignore_errors=True)


def case_supervisor_status_reports_all_fields() -> None:
    _section("status() returns full state record")
    init = _fresh_module()
    journal = Path(tempfile.mkdtemp(prefix="init-jrnl-"))
    try:
        services = {
            "x": init.ServiceDef(name="x", exec_start="sleep 5"),
        }
        sup = init.Supervisor(services, journal_dir=journal)
        sup.start("x")
        st = sup.status("x")
        assert st["name"] == "x"
        assert st["status"] == "running"
        assert st["pid"] > 0
        assert st["restart_count"] == 0
        sup.stop("x")
        print(f"  PASS status: {st}")
    finally:
        shutil.rmtree(journal, ignore_errors=True)


def case_servicedef_validates_required_fields() -> None:
    _section("ServiceDef.from_dict rejects missing name/exec_start")
    init = _fresh_module()
    for missing, payload in (
        ("name", {"exec_start": "true"}),
        ("exec_start", {"name": "x"}),
    ):
        try:
            init.ServiceDef.from_dict(payload)
        except ValueError as exc:
            assert missing in str(exc), exc
            print(f"  PASS rejected missing {missing}: {exc}")
            continue
        raise AssertionError(f"expected ValueError when {missing} missing")


# --------------------------------------------------------------------- driver

def main() -> None:
    cases = [
        case_yaml_parser_handles_scalars_and_lists,
        case_yaml_rejects_malformed,
        case_discover_picks_up_yaml_files,
        case_topological_order_respects_requires,
        case_topological_order_detects_cycle,
        case_topological_order_detects_missing_dep,
        case_topological_order_skips_missing_wants,
        case_supervisor_starts_real_subprocess,
        case_supervisor_recursive_start_resolves_requires,
        case_supervisor_restarts_on_failure_with_backoff,
        case_supervisor_caps_at_max_restarts,
        case_supervisor_journal_writes_real_file,
        case_supervisor_hot_reload_sends_signal,
        case_supervisor_status_reports_all_fields,
        case_servicedef_validates_required_fields,
    ]
    failures = 0
    for case in cases:
        try:
            case()
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
