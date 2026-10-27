"""LIVE pilot of the Opus/Sonnet/Haiku comparison (opt-in: CLAUDE_LIVE_PILOT=1). Spends real money.

Scope: 10 synthetic tasks (2 per category of the smoke corpus), 1 repetition, arms from config/arms.pilot.json (direct Opus
as the baseline, direct Sonnet, direct Haiku). Hard caps: 40 calls and 0.90 USD (the approved pilot budget is 1.00 USD; two
probe calls already cost 0.066). Measured on 2026-10-27: one ~6.1k-token call costs 0.0440 USD on Opus 5.5, 0.0220 on
Sonnet 5.5 and about 0.0015 on Haiku 5.5, about 15x and 30x Haiku, NOT the 2x and 4x of the rate-card factors an earlier
estimate used. The pilot reads the price per run of each tier. With 10 tasks it cannot establish non-inferiority.

Run:  CLAUDE_LIVE_PILOT=1 pytest tests/benchmarking/token_ab/test_token_ab_pilot_live.py -q -s
The run directory is copied to token_ab/results/pilot-last/ (git-ignored).
"""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ab_bench as ab  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_PILOT", "") != "1"
    or (shutil.which("claude") is None and not os.path.isfile(os.path.expanduser("~/.local/bin/claude")))
    or not ab.bwrap_available(),
    reason="the pilot spends money: needs CLAUDE_LIVE_PILOT=1, the claude CLI and bubblewrap",
)

TASKS, REPS, ARMS = 10, 1, 3


def test_pilot_opus_sonnet_haiku(tmp_path):
    rc = ab.main(["run", "--runner", "claude", "--profile", "smoke", "--tasks-per-category", "2", "--reps", str(REPS),
                  "--seed", "20261027", "--arms-config", str(HERE / "config" / "arms.pilot.json"),
                  "--run-id", "pilot", "--results-dir", str(tmp_path), "--max-calls", "40", "--max-cost-usd", "0.9",
                  "--allow-code-exec"])
    run = tmp_path / "pilot"
    man = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    out = HERE / "results" / "pilot-last"
    shutil.copytree(run, out, dirs_exist_ok=True)  # keep the evidence even when an assertion below fails
    assert rc == 0, f"pilot did not complete: status={man['status']}"
    recs = ab.load_raw(run)
    calls = [c for r in recs for c in r["calls"]]
    errors = [(c["tier"], c["error"]) for c in calls if c["error"]]
    assert not errors, f"{len(errors)} calls failed: {errors[:5]}"
    assert len(recs) == TASKS * REPS * ARMS
    for c in calls:
        assert c["input"] + c["output"] + c["cache_read"] + c["cache_write"] > 0
        assert isinstance(c["cost_usd"], (int, float)) and c["cost_usd"] > 0
    ab.main(["analyze", "--run-dir", str(run), "--bootstrap", "5000"])
    shutil.copytree(run, out, dirs_exist_ok=True)
    print(f"\nPILOT calls={len(calls)} spent_usd={sum(c['cost_usd'] for c in calls):.4f}")
    print((run / "report.md").read_text(encoding="utf-8"))


def test_text_rules_opus_sonnet_haiku(tmp_path):
    """Opus, Sonnet and Haiku on the 12 text_rules tasks of profile `hard` (the category Haiku failed on, 8 of 12, in the
    2026-10-27 probe). Spends real money: hard cap 1.50 USD set by the operator; the estimate of about 1 USD is an
    extrapolation from measured per-call prices of the pilot, NOT a measurement on this corpus."""
    rc = ab.main(["run", "--runner", "claude", "--profile", "hard", "--categories", "text_rules", "--reps", "1",
                  "--seed", "20261027", "--arms-config", str(HERE / "config" / "arms.text_rules.json"),
                  "--run-id", "text-rules", "--results-dir", str(tmp_path), "--max-calls", "40", "--max-cost-usd", "1.5",
                  "--allow-code-exec"])
    run = tmp_path / "text-rules"
    out = HERE / "results" / "text-rules-last"
    shutil.copytree(run, out, dirs_exist_ok=True)  # keep the evidence even when an assertion below fails
    man = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert rc == 0, f"run did not complete: status={man['status']}"
    recs = ab.load_raw(run)
    assert len(recs) == 36
    errors = [(c["tier"], c["error"]) for r in recs for c in r["calls"] if c["error"]]
    assert not errors, f"{len(errors)} calls failed: {errors[:5]}"
    ab.main(["analyze", "--run-dir", str(run), "--bootstrap", "5000"])
    shutil.copytree(run, out, dirs_exist_ok=True)
    per_arm = {}
    for r in recs:
        per_arm.setdefault(r["arm"], []).append(r["correct"])
    spent = sum(c["cost_usd"] for r in recs for c in r["calls"])
    print(f"\nTEXT-RULES spent_usd={spent:.4f} success=" + str({k: f"{sum(v)}/{len(v)}" for k, v in sorted(per_arm.items())}))
    print((run / "report.md").read_text(encoding="utf-8"))
