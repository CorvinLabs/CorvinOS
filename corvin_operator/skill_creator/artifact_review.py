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
from typing import Any, Dict, List, Optional, Sequence

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
MAX_ARTIFACT_CHARS = 40_000


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


async def review(client: Any, *, kind: str, name: str, purpose: str, artifact: str,
                 dimensions: Sequence[str] = tuple(DIMENSIONS)) -> List[sc.ReviewFinding]:
    """Run one reviewer per dimension in parallel; an engine-less run returns []."""
    if client is None:
        logger.warning("%s review skipped: no engine available", kind)
        return []
    text = artifact
    if len(text) > MAX_ARTIFACT_CHARS:
        text = text[:MAX_ARTIFACT_CHARS] + "\n[artifact truncated for review]"

    async def one(dimension: str) -> List[sc.ReviewFinding]:
        prompt = _REVIEW_TEMPLATE.format(
            kind=kind, kind_title=kind.capitalize(), dimension_upper=dimension.upper(),
            what=DIMENSIONS[dimension], name=name, purpose=purpose, artifact=text,
        )
        reply = await asyncio.to_thread(ask, client, prompt, max_tokens=800)
        return sc.parse_review_findings(reply, dimension)

    results = await asyncio.gather(*(one(d) for d in dimensions), return_exceptions=True)
    findings: List[sc.ReviewFinding] = []
    for dimension, result in zip(dimensions, results):
        if isinstance(result, Exception):
            # A reviewer that could not run is not a clean dimension.
            logger.error("%s review dimension %s failed: %s", kind, dimension, result)
            findings.append(sc.ReviewFinding(
                finding_id=f"{dimension}-error", dimension=dimension,
                summary=f"reviewer failed to run ({type(result).__name__})",
                verdict=sc.ReviewVerdict.PLAUSIBLE, reasoning=str(result)[:200],
            ))
            continue
        findings.extend(result)
    return findings


def quality(findings: List[sc.ReviewFinding], *, converged: bool, dimensions: int) -> float:
    return sc.score_quality(findings, converged=converged, dimensions=dimensions)


def findings_out(findings: List[sc.ReviewFinding]) -> List[Dict[str, str]]:
    return [{"dimension": f.dimension, "summary": f.summary, "verdict": f.verdict.value}
            for f in findings]
