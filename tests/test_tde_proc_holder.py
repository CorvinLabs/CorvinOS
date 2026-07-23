"""Round-4 finding: TDE subprocess one-shots need a killable holder so a
cancelled ``asyncio.to_thread(run_one_shot, ...)`` caller can terminate the
underlying process instead of letting it run to its own timeout.

No LLM calls — uses ``sys.executable`` as the spawned command, never `claude`.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.worker_ipc import ProcHolder, run_one_shot


class TestProcHolder:
    def test_holder_is_populated_before_communicate_returns(self):
        holder = ProcHolder()
        assert holder.popen is None
        rc, stdout, _stderr = run_one_shot(
            [sys.executable, "-c", "print('hi')"], timeout_s=10, proc_holder=holder,
        )
        assert rc == 0
        assert stdout.strip() == "hi"
        # populated during the call, still referenceable afterwards
        assert holder.popen is not None

    def test_kill_terminates_a_long_running_process(self):
        holder = ProcHolder()
        errors: list[BaseException] = []
        result: dict = {}

        def _runner():
            try:
                rc, _out, _err = run_one_shot(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    timeout_s=60, proc_holder=holder,
                )
                result["rc"] = rc
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        import threading
        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        # Wait for the holder to actually have a live process, then kill it.
        for _ in range(200):
            if holder.popen is not None:
                break
            time.sleep(0.01)
        assert holder.popen is not None, "process never registered on the holder"

        start = time.time()
        holder.kill()
        t.join(timeout=10)
        elapsed = time.time() - start

        assert not t.is_alive(), "runner thread should finish quickly after kill()"
        assert elapsed < 5, f"kill() should terminate the process almost immediately, took {elapsed:.1f}s"
        # A killed process ends with a non-zero returncode.
        assert holder.popen.returncode != 0

    def test_kill_is_a_no_op_before_any_process_started(self):
        holder = ProcHolder()
        holder.kill()  # must not raise

    def test_kill_is_a_no_op_after_process_already_exited(self):
        holder = ProcHolder()
        run_one_shot([sys.executable, "-c", "pass"], timeout_s=10, proc_holder=holder)
        holder.kill()  # must not raise even though the process already exited


class TestAnalysisRunnerForwardsHolder:
    def test_run_lm_call_forwards_proc_holder(self, monkeypatch):
        from tde import analysis_runner

        captured: dict = {}

        def _fake_run_one_shot(cmd, timeout_s, cwd=None, proc_holder=None):
            captured["proc_holder"] = proc_holder
            return 0, '{"ok": true}', ""

        class _FakeWorkerIPCModule:
            run_one_shot = staticmethod(_fake_run_one_shot)

        monkeypatch.setitem(sys.modules, "tde.worker_ipc", _FakeWorkerIPCModule)
        # helper_model must resolve for _run_lm_call to reach run_one_shot
        monkeypatch.setattr(
            analysis_runner, "_bridges_shared_dir",
            lambda: Path(__file__).parent.parent / "operator" / "bridges" / "shared",
        )

        holder = object()  # any sentinel — _run_lm_call just forwards it
        out = analysis_runner._run_lm_call("prompt", 10, proc_holder=holder)
        assert out == '{"ok": true}'
        assert captured["proc_holder"] is holder
