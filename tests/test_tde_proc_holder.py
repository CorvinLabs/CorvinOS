"""Round-4 finding: TDE subprocess one-shots need a killable holder so a
cancelled ``asyncio.to_thread(run_one_shot, ...)`` caller can terminate the
underlying process instead of letting it run to its own timeout.

No LLM calls — uses ``sys.executable`` as the spawned command, never `claude`.
"""
import asyncio
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


class TestParallelBatchProcHolderTracking:
    """Round-4 follow-up: AdaptiveDelegationExecutor.execute() now tracks one
    ProcHolder per concurrently-scheduled task in a parallel batch. A client
    disconnect (execute() itself cancelled while awaiting asyncio.gather())
    must kill EVERY sibling subprocess started by that batch — delegated AND
    local — not just whichever task happened to raise first.

    Real subprocesses (sys.executable), no LLM calls.
    """

    @staticmethod
    def _plan_two_parallel_steps():
        from initial_analysis import GlobalPlan, Step

        return GlobalPlan(
            steps=[
                # write_file: tracker below is seeded for this action ->
                # gates_passed -> delegated.
                Step(step=1, action="write_file", can_parallelize=[2]),
                # delete_file: no seeded evidence, not side-effect-free ->
                # no_evidence_mutating_step -> stays local.
                Step(step=2, action="delete_file", can_parallelize=[1]),
            ],
            estimated_duration_s=10, estimated_tokens=5000,
        )

    @staticmethod
    def _seeded_tracker():
        from tde.loss_profile_tracker import LossProfileTracker

        tracker = LossProfileTracker()
        for _ in range(6):
            tracker.record_delegation_result(
                task_type="write_file", engine="tiered_delegation",
                loss_pct=1.0, measured=True,
            )
        return tracker

    def _executor(self, worker_ipc):
        from tde.adaptive_delegation_executor import AdaptiveDelegationExecutor
        from tde.l34_delegation_gate import L34DelegationGate

        return AdaptiveDelegationExecutor(
            self._plan_two_parallel_steps(), L34DelegationGate(),
            self._seeded_tracker(), worker_ipc=worker_ipc,
        )

    @pytest.mark.asyncio
    async def test_normal_parallel_batch_completes_fine(self):
        """(a) baseline: with proc_holder plumbing wired through both the
        delegated (IPC) and local paths, an uncancelled batch still runs
        both steps to completion and reports the expected delegation split.
        """
        class _QuickIPC:
            async def send_delegation(self, envelope, *, proc_holder=None):
                rc, out, _err = run_one_shot(
                    [sys.executable, "-c", "print('ok')"],
                    timeout_s=10, proc_holder=proc_holder,
                )
                return {"success": rc == 0, "output": out.strip(), "error": None}

        async def _quick_local(step, statement, *, proc_holder=None):
            rc, out, _err = await asyncio.to_thread(
                run_one_shot, [sys.executable, "-c", "print('ok')"], 10, None, proc_holder,
            )
            return out.strip()

        ex = self._executor(_QuickIPC())
        results = await ex.execute({}, None, _quick_local)

        assert len(results) == 2
        assert all(r.success for r in results)
        delegated = {r.step_num: r.was_delegated for r in results}
        assert delegated[1] is True   # write_file -> gates_passed -> delegated
        assert delegated[2] is False  # delete_file -> no evidence -> local

    @pytest.mark.asyncio
    async def test_mid_batch_cancellation_kills_all_sibling_subprocesses(self):
        """(b) core fix: cancelling execute() mid-batch kills BOTH the
        delegated subprocess AND the local sibling's subprocess in that
        batch — not just one of them."""
        captured_holders: list[ProcHolder] = []

        _SLEEP_CMD = [sys.executable, "-c", "import time; time.sleep(30)"]

        class _SlowIPC:
            async def send_delegation(self, envelope, *, proc_holder=None):
                captured_holders.append(proc_holder)
                rc, out, _err = await asyncio.to_thread(
                    run_one_shot, _SLEEP_CMD, 30, None, proc_holder,
                )
                return {"success": rc == 0, "output": out, "error": None}

        async def _slow_local(step, statement, *, proc_holder=None):
            captured_holders.append(proc_holder)
            rc, out, _err = await asyncio.to_thread(
                run_one_shot, _SLEEP_CMD, 30, None, proc_holder,
            )
            return out

        ex = self._executor(_SlowIPC())
        task = asyncio.create_task(ex.execute({}, None, _slow_local))

        # Wait until BOTH sibling subprocesses actually registered a live
        # Popen on their holder (not just scheduled) before cancelling.
        for _ in range(500):
            if len(captured_holders) == 2 and all(
                h is not None and h.popen is not None for h in captured_holders
            ):
                break
            await asyncio.sleep(0.01)
        assert len(captured_holders) == 2
        assert all(h.popen is not None for h in captured_holders), (
            "both sibling subprocesses must have started before cancellation"
        )
        live_popens = [h.popen for h in captured_holders]
        assert all(p.poll() is None for p in live_popens), "subprocesses must still be running"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Give the OS a brief moment to reap the killed processes, then
        # verify NEITHER sibling was left running its full 30s sleep.
        deadline = time.time() + 5
        while time.time() < deadline and any(p.poll() is None for p in live_popens):
            time.sleep(0.05)

        for p in live_popens:
            assert p.poll() is not None, (
                "sibling subprocess was left running past batch cancellation "
                "instead of being killed"
            )
