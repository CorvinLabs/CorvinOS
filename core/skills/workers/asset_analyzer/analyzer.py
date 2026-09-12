"""Asset Analyzer Worker: Deep-read asset analysis for video narration (Phase 2).

Stages:
1. Asset Ingestion (call narrated-video-producer/ingest_assets.py)
2. Deep Read (extract facts from full text)
3. Contradiction Detection (cross-source validation)
4. Asset Role Mapping
5. Gate Check (ready_for_narration)
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

# Import from video_producer
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from os_skills.video_producer.types import AssetAnalysisResult, FactualClaim, Contradiction
from os_skills.video_producer.exceptions import AssetIngestionError


class AssetAnalyzer:
    """Worker for deep-read asset analysis."""

    def __init__(self, workdir: str | Path):
        """Initialize with working directory."""
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.text_dir = self.workdir / "text"
        self.frames_dir = self.workdir / "frames"
        self.assets_json = self.workdir / "assets.json"

    async def analyze(
        self,
        asset_paths: list[str | Path],
        instructions: Optional[dict[str, Any]] = None,
    ) -> AssetAnalysisResult:
        """
        Analyze assets through 4 stages.

        Stage 1: Ingestion (call narrated-video-producer)
        Stage 2: Deep read (extract facts from full text)
        Stage 2b: Contradiction detection
        Stage 3: Asset role mapping
        Stage 4: Gate check

        Args:
            asset_paths: Files to analyze
            instructions: Optional user guidance

        Returns:
            AssetAnalysisResult with ready_for_narration flag
        """
        # Stage 1: Ingestion (call narrated-video-producer)
        try:
            assets_data = await self._stage_1_ingest(asset_paths)
        except Exception as e:
            # Return analysis indicating ingestion failed
            return AssetAnalysisResult(
                metadata={
                    "processed_at": datetime.utcnow().isoformat() + "Z",
                    "total_assets": 0,
                    "analysis_complete": False,
                },
                blockers=[f"Asset ingestion failed: {str(e)}"],
                ready_for_narration=False,
            )

        assets = assets_data.get("assets", [])

        # Stage 2: Deep read (extract facts from full text of each asset)
        facts = await self._stage_2_deep_read(assets)

        # Stage 2b: Contradiction detection
        contradictions = await self._stage_2b_detect_contradictions(facts)

        # Stage 3: Asset role mapping
        asset_roles = await self._stage_3_map_asset_roles(assets, facts)

        # Stage 4: Gate check
        blockers = []
        if len(facts) < 3:
            blockers.append(f"Extracted {len(facts)} facts, need ≥3")
        if not asset_roles:
            blockers.append("No asset roles mapped; need ≥1")

        ready = len(blockers) == 0 and len(facts) >= 3 and len(asset_roles) >= 1

        return AssetAnalysisResult(
            metadata={
                "processed_at": datetime.utcnow().isoformat() + "Z",
                "total_assets": len(assets),
                "analysis_complete": ready,
            },
            factual_claims=facts,
            asset_roles=asset_roles,
            contradictions=contradictions,
            ready_for_narration=ready,
            blockers=blockers,
        )

    async def _stage_1_ingest(self, asset_paths: list[str | Path]) -> dict:
        """Stage 1: Call narrated-video-producer/ingest_assets.py."""
        if not asset_paths:
            return {"summary": {}, "assets": []}

        # Find narrated-video-producer repo
        vp_repo = Path(__file__).resolve().parents[4] / "narrated-video-producer"
        if not vp_repo.exists():
            raise AssetIngestionError(f"narrated-video-producer not found at {vp_repo}")

        ingest_script = vp_repo / "scripts" / "ingest_assets.py"
        if not ingest_script.exists():
            raise AssetIngestionError(f"ingest_assets.py not found at {ingest_script}")

        # Call ingest_assets.py
        try:
            cmd = [
                "python3",
                str(ingest_script),
                *[str(p) for p in asset_paths],
                "--workdir", str(self.workdir),
                "--out", str(self.assets_json),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(vp_repo),
            )

            if result.returncode != 0:
                raise AssetIngestionError(f"ingest_assets.py failed: {result.stderr}")

            # Load result JSON
            with open(self.assets_json) as f:
                assets_data = json.load(f)
            return assets_data

        except subprocess.TimeoutExpired:
            raise AssetIngestionError("Asset ingestion timed out after 120s")
        except Exception as e:
            raise AssetIngestionError(f"Asset ingestion failed: {str(e)}")

    async def _stage_2_deep_read(self, assets: list[dict]) -> list[FactualClaim]:
        """Stage 2: Extract factual claims from assets via full-text read."""
        facts = []
        claim_id_counter = 1

        for asset in assets:
            # Read full text file if it exists
            if "text_file" in asset:
                try:
                    text_path = Path(asset["text_file"])
                    if text_path.exists():
                        full_text = text_path.read_text(encoding="utf-8", errors="replace")

                        # Extract facts from the text (simple sentence-based extraction)
                        # In production, this would use LLM for structured extraction
                        sentences = [s.strip() for s in full_text.split(".") if s.strip()]

                        for sentence in sentences[:10]:  # Limit to first 10 sentences per asset
                            if len(sentence) > 10:  # Only meaningful sentences
                                claim = FactualClaim(
                                    id=f"claim_{claim_id_counter:03d}",
                                    text=sentence,
                                    source_asset=asset.get("filename", asset.get("id")),
                                    source_page=None,
                                    confidence="medium",
                                )
                                facts.append(claim)
                                claim_id_counter += 1
                except Exception:
                    # Silently skip unreadable files
                    pass

        return facts

    async def _stage_2b_detect_contradictions(
        self,
        facts: list[FactualClaim],
    ) -> list[Contradiction]:
        """Stage 2b: Detect contradictions between claims."""
        contradictions = []

        # Simple contradiction detection: look for opposite keywords
        keywords_pro = {"enabled", "active", "ready", "available", "true", "yes"}
        keywords_con = {"disabled", "inactive", "beta", "unavailable", "false", "no"}

        for i, fact_a in enumerate(facts):
            for fact_b in facts[i + 1:]:
                # Simple check: if claims from different sources contain opposite keywords
                text_a_lower = fact_a.text.lower()
                text_b_lower = fact_b.text.lower()

                if fact_a.source_asset != fact_b.source_asset:
                    # Check for opposite keywords
                    has_pro_a = any(kw in text_a_lower for kw in keywords_pro)
                    has_con_a = any(kw in text_a_lower for kw in keywords_con)
                    has_pro_b = any(kw in text_b_lower for kw in keywords_pro)
                    has_con_b = any(kw in text_b_lower for kw in keywords_con)

                    if (has_pro_a and has_con_b) or (has_con_a and has_pro_b):
                        contradiction = Contradiction(
                            sources=[fact_a.source_asset, fact_b.source_asset],
                            claim_a=fact_a.text[:100],
                            claim_b=fact_b.text[:100],
                            resolution=None,
                        )
                        contradictions.append(contradiction)
                        break  # Only report one contradiction per asset pair

        return contradictions

    async def _stage_3_map_asset_roles(
        self,
        assets: list[dict],
        facts: list[FactualClaim],
    ) -> dict[str, str]:
        """Stage 3: Infer which asset plays which role in the video."""
        asset_roles = {}

        for asset in assets:
            filename = asset.get("filename", "")
            role_suggestion = asset.get("role_suggestion", "unclassified")
            kind = asset.get("kind", "unknown")

            # Use ingest_assets suggestion, augmented with heuristics
            if kind == "deck":
                role = "Slides and visual structure"
            elif kind == "pdf":
                role = "Reference documentation"
            elif kind == "document":
                role = "Detailed source content"
            elif kind == "text":
                if "script" in filename.lower() or "transcript" in filename.lower():
                    role = "Narrative script or transcript"
                else:
                    role = "Source content"
            elif kind == "video":
                role = "Screencast or demonstration"
            elif kind == "image":
                role = "Visual asset or still frame"
            else:
                role = role_suggestion or "Unclassified"

            if kind != "not_found" and kind != "unknown":
                asset_roles[filename] = role

        return asset_roles
