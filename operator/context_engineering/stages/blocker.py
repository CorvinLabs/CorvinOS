"""Blocker ID stage (ADR-0280) — DETERMINISTIC constraint scan over the gathered
context. `requires=("memory","graph")`. Empty is a valid result."""
from __future__ import annotations

from .base import StageTelemetry
from .registry import register_stage
from ._util import scan_blockers


class BlockerStage:
    id = "blocker_id"
    requires: tuple = ("memory", "graph")
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        blockers = scan_blockers(bundle.brief)
        bundle.brief.blockers = blockers
        tel = StageTelemetry(
            stage="blocker_id", status="ok",
            confidence_tier="high" if blockers else "low",
            sources=[{"id": b, "score": 1.0} for b in blockers])
        return bundle, tel


register_stage(BlockerStage())
