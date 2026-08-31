"""CMP-04 (ADR-0146): record_trial_iteration must not lose increments under
concurrent compute_run submissions (TOCTOU soft-overrun of the trial cap).

The fix serializes the load-modify-save under an exclusive flock, so N concurrent
processes each recording one iteration must yield exactly N — never fewer.
"""
from __future__ import annotations

import multiprocessing as mp
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute import license_gate as LG


def _record_once(home_str: str) -> None:
    # Module-level so it is picklable for spawn/fork workers.
    LG.record_trial_iteration(Path(home_str), strategy="grid")


def test_concurrent_record_trial_iteration_loses_no_increment():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        n = 40
        ctx = mp.get_context("fork")  # Linux deploy target
        with ctx.Pool(processes=8) as pool:
            pool.map(_record_once, [str(home)] * n)

        state = LG._load_trial_state(home)
        assert state.iterations_used == n, (
            f"Expected exactly {n} iterations recorded under concurrency, "
            f"got {state.iterations_used} — the flock failed to serialize the "
            "read-modify-write (lost increments = trial-cap overrun)."
        )


def test_record_trial_iteration_single_increment():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        LG.record_trial_iteration(home, strategy="grid")
        LG.record_trial_iteration(home, strategy="bayesian")
        state = LG._load_trial_state(home)
        assert state.iterations_used == 1
        assert state.bayesian_iterations_used == 1
