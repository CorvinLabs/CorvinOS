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
from datetime import datetime

from .types import AssetAnalysisResult, Storyboard, Scene
from .exceptions import AnalysisGateFailedError

# Import AssetAnalyzer from workers (dynamic to avoid circular imports)
# Will be imported when needed in orchestrate() method


class VideoProducerOrchestrator:
    """Main orchestrator for video production workflow."""

    def __init__(self, project_dir: str | Path):
        """Initialize orchestrator with project directory."""
        self.project_dir = Path(project_dir)
        self.assets_dir = self.project_dir / "assets"
        self.analysis_path = self.project_dir / "analysis.json"
        self.storyboard_path = self.project_dir / "storyboard.json"
        self.project_dir.mkdir(parents=True, exist_ok=True)

    async def orchestrate(
        self,
        asset_paths: list[str | Path],
        instructions: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Main orchestration entry point.

        Phases (Phase 1–3 in this version):
        1. Asset ingestion (via narrated-video-producer)
        2. Deep analysis (via worker.asset_analyzer)
        3. Gate check (ready_for_narration)
        4. Storyboard generation (LLM-constrained to analysis facts)
        5–7. (Phases 4–7 in later phases: voice, assembly, YouTube)

        Args:
            asset_paths: Files to analyze
            instructions: Optional user guidance

        Returns:
            {
                "analysis": AssetAnalysisResult dict,
                "storyboard": Storyboard dict | None,
                "status": "success" | "blocked",
            }

        Raises:
            AnalysisGateFailedError: If analysis gates not met
        """
        try:
            # Phase 2: Deep analysis via worker.asset_analyzer
            # Import dynamically to avoid circular imports
            from core.skills.workers.asset_analyzer import AssetAnalyzer

            analyzer = AssetAnalyzer(str(self.project_dir))
            analysis = await analyzer.analyze(asset_paths, instructions)

            # Save analysis to disk
            self._save_analysis(analysis)

            # Gate check: ready_for_narration must be true
            self._check_analysis_gates(analysis)

            # Phase 3: Storyboard generation (constrained)
            storyboard = await self._generate_storyboard(analysis)

            # Save storyboard to disk
            if storyboard:
                self._save_storyboard(storyboard)

            return {
                "analysis": analysis.to_dict(),
                "storyboard": storyboard.to_dict() if storyboard else None,
                "status": "success",
            }

        except AnalysisGateFailedError:
            # Re-raise gate failures (expected workflow stop)
            raise
        except Exception as e:
            # Wrap unexpected errors
            return {
                "analysis": {
                    "metadata": {"error": str(e)},
                    "ready_for_narration": False,
                    "blockers": [str(e)],
                },
                "storyboard": None,
                "status": "blocked",
            }

    def _save_analysis(self, analysis: AssetAnalysisResult) -> None:
        """Save analysis to JSON file."""
        with open(self.analysis_path, "w") as f:
            json.dump(analysis.to_dict(), f, indent=2)

    def _save_storyboard(self, storyboard: Storyboard) -> None:
        """Save storyboard to JSON file."""
        with open(self.storyboard_path, "w") as f:
            json.dump(storyboard.to_dict(), f, indent=2)

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
        """Phase 3: Generate storyboard constrained to analysis.factual_claims.

        NOTE: This is a placeholder skeleton. Real implementation in Phase 1 k=5
        would call an LLM with the source-constrained prompt.
        """
        if not analysis.ready_for_narration:
            return None

        # Create a minimal storyboard structure (placeholder for LLM generation)
        # In production, this would:
        # 1. Call LLM with source-constrained prompt
        # 2. Validate that all narration references analysis.factual_claims
        # 3. Return structured storyboard with timing data

        scenes = []
        for i, claim in enumerate(analysis.factual_claims[:3], 1):
            scene = Scene(
                id=f"s{i:02d}",
                kind="card",
                narration=claim.text[:100],  # Truncated for now
                source_asset=claim.source_asset,
                captions=True,
            )
            scenes.append(scene)

        return Storyboard(
            metadata={
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "source_constrained": True,
                "scene_count": len(scenes),
            },
            scenes=scenes,
        )
