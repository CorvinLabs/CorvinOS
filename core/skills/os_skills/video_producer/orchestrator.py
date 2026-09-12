"""Maestro Skill for orchestrating Video Producer workflow.

Coordinates:
1. Asset ingestion and analysis
2. Storyboard generation (constrained to analysis facts)
3. Worker dispatch (voice, screenshots, assembly)
4. Feedback collection and learning
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional, Any

from .types import AssetAnalysisResult, Storyboard
from .exceptions import AnalysisGateFailedError


class VideoProducerOrchestrator:
    """Main orchestrator for video production workflow."""

    def __init__(self, project_dir: str | Path):
        """Initialize orchestrator with project directory."""
        self.project_dir = Path(project_dir)
        self.assets_dir = self.project_dir / "assets"
        self.analysis_path = self.project_dir / "analysis.json"
        self.storyboard_path = self.project_dir / "storyboard.json"

    async def orchestrate(
        self,
        asset_paths: list[str | Path],
        instructions: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Main orchestration entry point.

        Phases:
        1. Asset ingestion
        2. Deep analysis (via worker.asset_analyzer)
        3. Gate check (ready_for_narration)
        4. Storyboard generation
        5. (Phases 4–7 in later phases)

        Args:
            asset_paths: Files to analyze
            instructions: Optional user guidance

        Returns:
            {
                "analysis": AssetAnalysisResult dict,
                "storyboard": Storyboard dict,
                "status": "success" | "blocked",
            }

        Raises:
            AnalysisGateFailedError: If analysis gates not met
        """
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Phase 1: Asset ingestion
        assets = await self._ingest_assets(asset_paths)

        # Phase 2: Deep analysis
        analysis = await self._deep_analyze_assets(assets, instructions)

        # Gate check: ready_for_narration
        self._check_analysis_gates(analysis)

        # Phase 3: Storyboard generation (constrained)
        storyboard = await self._generate_storyboard(analysis)

        return {
            "analysis": analysis.to_dict(),
            "storyboard": storyboard.to_dict() if storyboard else None,
            "status": "success",
        }

    async def _ingest_assets(self, asset_paths: list[str | Path]) -> dict[str, Any]:
        """Phase 1: Call narrated-video-producer/ingest_assets.py."""
        # TODO: Call narrated-video-producer ingest_assets
        return {
            "assets": [],
            "workdir": str(self.project_dir),
        }

    async def _deep_analyze_assets(
        self,
        assets: dict[str, Any],
        instructions: Optional[dict[str, Any]],
    ) -> AssetAnalysisResult:
        """Phase 2: Call worker.asset_analyzer."""
        # TODO: Dispatch to worker.asset_analyzer
        return AssetAnalysisResult(
            metadata={
                "processed_at": "2026-09-12T00:00:00Z",
                "total_assets": 0,
                "analysis_complete": False,
            }
        )

    def _check_analysis_gates(self, analysis: AssetAnalysisResult) -> None:
        """Gate check: ensure ready_for_narration is true."""
        if not analysis.ready_for_narration:
            blockers_str = "\n".join([f"  • {b}" for b in analysis.blockers])
            raise AnalysisGateFailedError(
                f"Analysis gates not met:\n"
                f"- Facts extracted: {len(analysis.factual_claims)} (need ≥3)\n"
                f"- Asset roles mapped: {len(analysis.asset_roles)} (need ≥1)\n"
                f"- Blockers:\n{blockers_str}"
            )

    async def _generate_storyboard(
        self,
        analysis: AssetAnalysisResult,
    ) -> Optional[Storyboard]:
        """Phase 3: Generate LLM storyboard constrained to analysis.factual_claims."""
        # TODO: Call LLM with source-constrained prompt
        return None
