"""Shared types for Video Producer Skill 2.0."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime


@dataclass(frozen=True)
class FactualClaim:
    """A single sourced factual claim from asset analysis."""
    id: str
    text: str
    source_asset: str
    source_page: Optional[str] = None
    confidence: str = "medium"  # high, medium, low
    contradictions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Contradiction:
    """A contradiction between two factual claims."""
    sources: list[str]
    claim_a: str
    claim_b: str
    resolution: Optional[str] = None


@dataclass
class AssetAnalysisResult:
    """Complete analysis output from asset_analyzer worker."""
    metadata: dict[str, Any]
    audience: Optional[str] = None
    purpose: Optional[str] = None
    factual_claims: list[FactualClaim] = field(default_factory=list)
    asset_roles: dict[str, str] = field(default_factory=dict)
    terminology: dict[str, str] = field(default_factory=dict)
    contradictions: list[Contradiction] = field(default_factory=list)
    ready_for_narration: bool = False
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "metadata": self.metadata,
            "audience": self.audience,
            "purpose": self.purpose,
            "factual_claims": [
                {
                    "id": c.id,
                    "text": c.text,
                    "source_asset": c.source_asset,
                    "source_page": c.source_page,
                    "confidence": c.confidence,
                    "contradictions": c.contradictions,
                }
                for c in self.factual_claims
            ],
            "asset_roles": self.asset_roles,
            "terminology": self.terminology,
            "contradictions": [
                {
                    "sources": c.sources,
                    "claim_a": c.claim_a,
                    "claim_b": c.claim_b,
                    "resolution": c.resolution,
                }
                for c in self.contradictions
            ],
            "ready_for_narration": self.ready_for_narration,
            "blockers": self.blockers,
        }


@dataclass
class Scene:
    """A single scene in the video storyboard."""
    id: str
    kind: str  # card, screencast, slide
    duration_seconds: Optional[float] = None
    narration: Optional[str] = None
    source_asset: Optional[str] = None
    captions: bool = True


@dataclass
class Storyboard:
    """Complete storyboard for video production."""
    metadata: dict[str, Any]
    scenes: list[Scene] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "metadata": self.metadata,
            "scenes": [
                {
                    "id": s.id,
                    "kind": s.kind,
                    "duration_seconds": s.duration_seconds,
                    "narration": s.narration,
                    "source_asset": s.source_asset,
                    "captions": s.captions,
                }
                for s in self.scenes
            ],
        }
