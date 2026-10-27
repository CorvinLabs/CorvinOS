"""LIVE token A/B benchmark with the real claude CLI (opt-in: CLAUDE_LIVE_E2E=1). Costs real tokens.

Scope: Haiku only (config/arms.live_haiku.json), the synthetic smoke corpus (15 fictional tasks: 3 per category),
2 repetitions, hard caps of 130 calls and 1.50 USD. All arms use the SAME model, so this measures the harness path
and the test-retest noise of one model. It makes no savings claim (CONCEPT-0122 section 8, ADR-2307).

Code tasks run model-written Python inside bubblewrap; the test is skipped without bwrap.

Run:  CLAUDE_LIVE_E2E=1 pytest tests/benchmarking/token_ab/test_token_ab_live.py -q -s
The run directory (manifest, raw.jsonl, summary.json, report.md) is copied to token_ab/results/live-haiku-last/ (git-ignored).
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
    os.environ.get("CLAUDE_LIVE_E2E", "") != "1"
    or (shutil.which("claude") is None and not os.path.isfile(os.path.expanduser("~/.local/bin/claude")))
    or not ab.bwrap_available(),
    reason="live benchmark needs CLAUDE_LIVE_E2E=1, the claude CLI and bubblewrap",
)

TASKS, REPS, ARMS = 15, 2, 3


def test_live_haiku_benchmark_runs_end_to_end(tmp_path):
    rc = ab.main(["run", "--runner", "claude", "--profile", "smoke", "--tasks-per-category", "3", "--reps", str(REPS),
                  "--seed", "20261027", "--arms-config", str(HERE / "config" / "arms.live_haiku.json"),
                  "--run-id", "live-haiku", "--results-dir", str(tmp_path), "--max-calls", "130", "--max-cost-usd", "1.5",
                  "--allow-code-exec"])
    run = tmp_path / "live-haiku"
    man = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert rc == 0, f"run did not complete: status={man['status']}"
    assert man["runner"] == "claude" and man["claude_version"] and man["models"]["haiku"].startswith("claude-haiku")

    recs = ab.load_raw(run)
    assert len(recs) == TASKS * REPS * ARMS, f"expected {TASKS * REPS * ARMS} runs, got {len(recs)}"
    calls = [c for r in recs for c in r["calls"]]
    errors = [c["error"] for c in calls if c["error"]]
    assert not errors, f"{len(errors)} calls failed: {errors[:5]}"
    for c in calls:
        assert c["model"].startswith("claude-haiku")
        assert c["input"] + c["output"] + c["cache_read"] + c["cache_write"] > 0
        assert isinstance(c["cost_usd"], (int, float)) and c["cost_usd"] > 0

    ab.main(["analyze", "--run-dir", str(run), "--bootstrap", "2000"])
    s = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert s["primary_metric"] == "cost_usd"
    assert s["arms"]["direct_haiku"]["success"]["mean"] > 0, "Haiku solved nothing: grader or prompt broken"
    ctl = s["comparisons"]["baseline_retest"]
    assert ctl["role"] == "retest" and "identical_policy_non_inferior" in ctl["sanity"]
    assert all(st["exploratory"] for k, st in ctl["strata"].items() if k.startswith("category:")), "3 tasks per category must stay exploratory"

    out = HERE / "results" / "live-haiku-last"
    shutil.copytree(run, out, dirs_exist_ok=True)
    spent = sum(c["cost_usd"] for c in calls)
    print(f"\nLIVE calls={len(calls)} spent_usd={spent:.4f} raw_sha256={s['raw_sha256']}")
    print((run / "report.md").read_text(encoding="utf-8"))
