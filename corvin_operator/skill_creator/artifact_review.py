"""Adversarial review + engine helpers shared by the tool and plugin forges (ADR-2217 D2).

The Skill-Creator's phase-4 review runs three reviewers on three dimensions
and scores per dimension (``score_quality``). Tools and plugins are reviewed
the same way — same verdict vocabulary, same parser, same score — over their
own artifact text and with a SECURITY dimension instead of "simplification",
because their artifact is executable code.

The engine is always resolved through ``skill_creator.resolve_llm_client`` at
call time (module attribute lookup), so one engine serves every kind and the
console E2E can substitute a scripted engine in one place.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from . import llm_client
from . import skill_creator as sc

logger = logging.getLogger(__name__)

#: Reviewer dimension -> what that reviewer hunts for.
DIMENSIONS: Dict[str, str] = {
    "correctness": "wrong, missing or impossible behaviour; code or docs that cannot do what the purpose says",
    "security": ("anything that reads or writes outside its inputs, reaches the network or the shell, "
                 "leaks secrets or personal data, or could be abused by a hostile input"),
    "scope_creep": "behaviour that does more, less or different than the stated purpose",
}

_REVIEW_TEMPLATE = """You are a {kind} reviewer focused on {dimension_upper}.
Look for: {what}.

{kind_title}: {name}
Purpose: {purpose}

--- ARTIFACT ---
{artifact}
--- END ARTIFACT ---

For each finding, output:
FINDING: <one sentence>
VERDICT: CONFIRMED / PLAUSIBLE / REFUTED

If no findings, output: VERDICT: REFUTED"""

#: Upper bound on the artifact text one reviewer sees; a review of a truncated
#: artifact says so instead of silently judging half of it.
MAX_ARTIFACT_CHARS = 120_000


def resolve_engine() -> Any:
    """The configured engine client, or None when no engine is reachable."""
    return sc.resolve_llm_client()


def engine_id(client: Any) -> str:
    return sc.engine_id_of(client)


def ask(client: Any, prompt: str, *, max_tokens: int = 4000, system: Optional[str] = None) -> str:
    """One blocking single-turn completion; returns the reply text."""
    kwargs: Dict[str, Any] = {
        "model": getattr(client, "model", None) or llm_client.DEFAULT_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    return response.content[0].text


def ask_json(client: Any, prompt: str, *, max_tokens: int = 8000) -> Dict[str, Any]:
    """Ask for one JSON object; raises ValueError when the reply holds none."""
    reply = ask(client, prompt, max_tokens=max_tokens)
    blob = sc._extract_json_object(reply)
    if not blob:
        raise ValueError("engine reply contained no JSON object")
    data = json.loads(blob)
    if not isinstance(data, dict):
        raise ValueError("engine reply JSON is not an object")
    return data


#: Synthetic finding ids that mark a dimension whose review did not happen.
#: A gate that must be fail-closed treats them as blocking.
ERROR_SUFFIX = "-error"
NO_VERDICT_SUFFIX = "-noverdict"
TRUNCATED_ID = "artifact-truncated"


async def run_reviewers(fns: Dict[str, Callable[[], Awaitable[str]]], *,
                        on_error: str = "flag") -> List[sc.ReviewFinding]:
    """Run one reviewer per dimension in parallel and parse their verdicts.

    The ONE reviewer loop of every Forge kind — the Skill-Creator's phase 4
    calls it too. ``on_error="flag"`` turns a reviewer that crashed, or replied
    without any ``VERDICT:``, into a PLAUSIBLE finding with a synthetic id
    (``<dim>-error`` / ``<dim>-noverdict``) so a fail-closed gate can see that
    the dimension was never reviewed; ``"drop"`` keeps the Skill-Creator's
    historical behaviour of skipping it.
    """
    dims = list(fns)
    results = await asyncio.gather(*(fns[d]() for d in dims), return_exceptions=True)
    findings: List[sc.ReviewFinding] = []
    for dim, result in zip(dims, results):
        if isinstance(result, BaseException):
            logger.error("review dimension %s failed: %s", dim, result)
            if on_error == "flag":
                findings.append(sc.ReviewFinding(
                    finding_id=dim + ERROR_SUFFIX, dimension=dim,
                    summary=f"reviewer failed to run ({type(result).__name__})",
                    verdict=sc.ReviewVerdict.PLAUSIBLE, reasoning=str(result)[:200]))
            continue
        parsed = sc.parse_review_findings(result, dim)
        # A clean refutation ("VERDICT: REFUTED", no FINDING: line at all) is
        # the documented happy path and legitimately parses to zero findings —
        # flagging it would make every clean review look suspicious. What must
        # be flagged fail-closed is everything else that failed to structure:
        # no verdict keyword at all, OR a non-REFUTED verdict (CONFIRMED/
        # PLAUSIBLE) that the parser still couldn't turn into a finding. The
        # substring check used to be "VERDICT:" (no markdown-tolerance), so a
        # markdown-decorated "**VERDICT:** CONFIRMED" slipped past it too —
        # this reads the verdict itself, not just whether the word is present.
        if not parsed and on_error == "flag":
            decorated = re.sub(r"[*_`#]+", "", result or "").upper()
            cleanly_refuted = bool(re.search(r"VERDICT:\s*REFUTED", decorated))
            if not cleanly_refuted:
                findings.append(sc.ReviewFinding(
                    finding_id=dim + NO_VERDICT_SUFFIX, dimension=dim,
                    summary="reviewer replied without a verdict",
                    verdict=sc.ReviewVerdict.PLAUSIBLE, reasoning=(result or "")[:200]))
        findings.extend(parsed)
    return findings


async def review(client: Any, *, kind: str, name: str, purpose: str, artifact: str,
                 dimensions: Sequence[str] = tuple(DIMENSIONS)) -> List[sc.ReviewFinding]:
    """Correctness / security / scope review of a tool or plugin; engine-less → []."""
    if client is None:
        logger.warning("%s review skipped: no engine available", kind)
        return []
    text = artifact
    truncated = len(text) > MAX_ARTIFACT_CHARS
    if truncated:
        text = text[:MAX_ARTIFACT_CHARS] + "\n[artifact truncated for review]"

    def fn(dimension: str) -> Callable[[], Awaitable[str]]:
        prompt = _REVIEW_TEMPLATE.format(
            kind=kind, kind_title=kind.capitalize(), dimension_upper=dimension.upper(),
            what=DIMENSIONS[dimension], name=name, purpose=purpose, artifact=text,
        )
        return lambda: asyncio.to_thread(ask, client, prompt, max_tokens=800)

    findings = await run_reviewers({d: fn(d) for d in dimensions}, on_error="flag")
    if truncated:
        findings.append(sc.ReviewFinding(
            finding_id=TRUNCATED_ID, dimension="security",
            summary=f"artifact exceeds {MAX_ARTIFACT_CHARS} characters; its tail was not reviewed",
            verdict=sc.ReviewVerdict.PLAUSIBLE, reasoning=""))
    return findings


def unreviewed(findings: List[sc.ReviewFinding], dimension: str) -> List[sc.ReviewFinding]:
    """Synthetic findings saying *dimension* was not (fully) reviewed."""
    return [f for f in findings if f.dimension == dimension and (
        f.finding_id.endswith((ERROR_SUFFIX, NO_VERDICT_SUFFIX)) or f.finding_id == TRUNCATED_ID)]


def quality(findings: List[sc.ReviewFinding], *, converged: bool, dimensions: int) -> float:
    return sc.score_quality(findings, converged=converged, dimensions=dimensions)


def findings_out(findings: List[sc.ReviewFinding]) -> List[Dict[str, str]]:
    return [{"dimension": f.dimension, "summary": f.summary, "verdict": f.verdict.value}
            for f in findings]
