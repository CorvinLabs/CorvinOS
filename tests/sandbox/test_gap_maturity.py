"""Learnings maturity radar (gap 8): does the API's key set match what the radar reads?

Reproduction attempt for the reported "radar tier key mismatch" through the real API: seed audit
events for several loops into the process tenant's chain, call ``GET /vibe/maturity/measurements``
and compare ``loop_scores`` with the 13 keys of ``LoopScores``
(``web-next/src/pages/vibe-engineering/components/maturity/types.ts``: 6 tier-1, 6 tier-2, meta).

Result recorded by these tests: the key sets are identical (no key mismatch), and seeded loops score
above zero. What DID mismatch is the label: the radar draws 12 axes + a meta loop while the visible
title said "9D" (REPORT-A). The title test is red on origin/main.
"""
from __future__ import annotations

import re

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .helpers_gap import tenant_chain  # noqa: E402

TYPES = harness.REPO_ROOT / "core/console/corvin_console/web-next/src/pages/vibe-engineering/components/maturity/types.ts"
DASH = harness.REPO_ROOT / "core/console/corvin_console/web-next/src/pages/vibe-engineering/components/MaturityDashboard.tsx"

SEED = r"""
import sys
from pathlib import Path
from corvin_console import audit
chain = Path(sys.argv[1])
chain.parent.mkdir(parents=True, exist_ok=True)
for et in ("data_flow.allowed", "flow_guard.checked", "memory.recall_hit", "recall.query",
           "skill.executed", "skill.executed", "plugin.loaded", "boot.self_test_passed"):
    audit._security_events.write_event(chain, et, details={})
"""


def _ui_keys() -> set[str]:
    body = TYPES.read_text(encoding="utf-8")
    block = body[body.index("export interface LoopScores"):]
    block = block[:block.index("}")]
    return set(re.findall(r"^\s*([a-z_]+):\s*number;", block, re.M))


@pytest.fixture(scope="module")
def measurement(sandbox, server, session_for):
    sandbox.run_child(SEED, str(tenant_chain(sandbox, "_default")))
    r = harness.fetch(server, session_for("_default"), "/vibe/maturity/measurements?window=7d")
    assert r.status_code == 200, r.text[:300]
    ms = r.json().get("measurements") or []
    assert ms, f"no measurement returned: {r.text[:300]}"
    return ms[-1]  # the same element useLiveMaturityData.ts takes


def test_G_maturity_api_loop_keys_match_the_radar_types(measurement):
    api_keys = set(measurement["loop_scores"])
    ui_keys = _ui_keys()
    assert len(ui_keys) == 13, ui_keys
    assert api_keys == ui_keys, f"API-only: {sorted(api_keys - ui_keys)} UI-only: {sorted(ui_keys - api_keys)}"


def test_G_maturity_seeded_loops_score_above_zero(measurement):
    scores = measurement["loop_scores"]
    for loop in ("data_flow", "memory", "skills"):
        assert scores[loop] > 0, f"{loop} has seeded events but scores {scores[loop]}: {scores}"


def test_G_maturity_radar_title_matches_what_it_draws():
    text = DASH.read_text(encoding="utf-8")
    titles = re.findall(r"Learning Loops —[^<\n]*", text)
    assert titles, "radar title not found"
    assert not any("9D" in t for t in titles), f"title claims 9 dimensions, radar draws 12 + meta: {titles}"
