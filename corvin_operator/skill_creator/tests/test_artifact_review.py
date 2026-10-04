"""Tests for the shared reviewer loop (round-2 refutation of the forge review).

A review in markdown ("**VERDICT:** CONFIRMED") contains the literal
substring "VERDICT:", so the old no-verdict fallback in run_reviewers never
fired for it either, even once parse_review_findings itself was fixed to
tolerate markdown. Both halves are pinned here.
"""

import sys
from pathlib import Path

_OPERATOR_DIR = Path(__file__).resolve().parents[2]
if str(_OPERATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR_DIR))

import pytest

from skill_creator import skill_creator as sc
from skill_creator.artifact_review import run_reviewers, NO_VERDICT_SUFFIX


@pytest.mark.asyncio
async def test_markdown_confirmed_verdict_is_not_swallowed():
    async def reviewer():
        return "**FINDING:** unsandboxed execution\n**VERDICT:** CONFIRMED"

    findings = await run_reviewers({"security": reviewer})
    assert len(findings) == 1
    assert findings[0].verdict == sc.ReviewVerdict.CONFIRMED
    assert not findings[0].finding_id.endswith(NO_VERDICT_SUFFIX)


@pytest.mark.asyncio
async def test_reply_with_no_structured_verdict_is_flagged_even_if_it_mentions_the_word():
    async def reviewer():
        # Mentions "verdict" in prose but has no FINDING:/VERDICT: structure at all.
        return "I could not form a verdict on this one, inconclusive."

    findings = await run_reviewers({"security": reviewer}, on_error="flag")
    assert len(findings) == 1
    assert findings[0].finding_id.endswith(NO_VERDICT_SUFFIX)
    assert findings[0].verdict == sc.ReviewVerdict.PLAUSIBLE


@pytest.mark.asyncio
async def test_clean_refutation_yields_no_findings():
    async def reviewer():
        return "VERDICT: REFUTED"

    findings = await run_reviewers({"security": reviewer}, on_error="flag")
    assert findings == []
