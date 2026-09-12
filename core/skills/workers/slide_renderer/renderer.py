"""Slide Renderer implementation: PowerPoint to PNG conversion + timing + feedback."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional, Any
from datetime import datetime
from dataclasses import asdict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from os_skills.video_producer.types import Storyboard, Scene
from core.learning.event_persistence import EventEmitter  # ADR-0314 feedback


class SlideRenderer:
    """Worker for rendering PowerPoint slides to PNG."""

    def __init__(self, workdir: str | Path):
        """Initialize with working directory."""
        self.workdir = Path(workdir)
        self.slides_dir = self.workdir / "slides"
        self.slides_dir.mkdir(parents=True, exist_ok=True)
        self.event_emitter = EventEmitter()  # For QualityFeedbackEvent

    async def render_slides(
        self,
        ppt_path: str | Path,
        storyboard: Storyboard,
        dpi: int = 150,
    ) -> dict[str, Any]:
        """
        Render PowerPoint slides to PNG for video.

        Stages:
        1. Load PowerPoint file (python-pptx stub)
        2. For each slide: render to PNG
        3. Extract speaker notes (narration sync)
        4. Measure rendering timing
        5. Emit QualityFeedbackEvent per slide
        6. Collect metadata (dimensions, text, notes)

        Args:
            ppt_path: Path to PowerPoint file
            storyboard: Scene list (maps slides to narration)
            dpi: Rendering DPI (default 150 for video quality)

        Returns:
            {
                "status": "success" | "partial",
                "slides_rendered": int,
                "slides_failed": int,
                "slide_files": {scene_id: png_path},
                "metadata": {scene_id: {width, height, text, notes}},
                "total_duration_ms": int,
            }
        """
        ppt_path = Path(ppt_path)

        # 0. Precondition: PPT file must exist
        if not ppt_path.exists():
            return {
                "status": "blocked",
                "slides_rendered": 0,
                "slides_failed": len(storyboard.scenes),
                "slide_files": {},
                "metadata": {},
                "error": f"PowerPoint file not found: {ppt_path}",
                "total_duration_ms": 0,
            }

        results = {
            "status": "success",
            "slides_rendered": 0,
            "slides_failed": 0,
            "slide_files": {},
            "metadata": {},
            "total_duration_ms": 0,
        }

        total_start = time.time()

        # Load PowerPoint (stub)
        try:
            prs = await self._load_ppt(ppt_path)
        except Exception as e:
            results["status"] = "blocked"
            results["slides_failed"] = len(storyboard.scenes)
            results["error"] = f"Failed to load PowerPoint: {str(e)}"
            return results

        # Render each slide (parallel)
        tasks = []
        for i, scene in enumerate(storyboard.scenes):
            tasks.append(
                self._render_slide(
                    prs=prs,
                    slide_index=i,
                    scene=scene,
                    dpi=dpi,
                    results=results,
                )
            )
        await asyncio.gather(*tasks, return_exceptions=True)

        results["total_duration_ms"] = int((time.time() - total_start) * 1000)

        return results

    async def _load_ppt(self, ppt_path: Path) -> Any:
        """Load PowerPoint file (stub)."""
        # Stub: simulate loading via python-pptx
        # Real: from pptx import Presentation; return Presentation(str(ppt_path))

        # Return stub presentation object
        return {
            "slides": [],
            "path": str(ppt_path),
            "slide_count": 3,  # Simulated
        }

    async def _render_slide(
        self,
        prs: Any,
        slide_index: int,
        scene: Scene,
        dpi: int,
        results: dict[str, Any],
    ) -> None:
        """Render a single slide to PNG."""
        try:
            # 1. Precondition: scene must have a slide reference
            # (In real workflow, scene.source_asset references slide number)
            if not scene.source_asset:
                results["slides_failed"] += 1
                return

            # 2. Render slide to PNG (stub)
            start_time = time.time()
            png_bytes = await self._render_slide_to_png(prs, slide_index, dpi)
            render_latency_ms = (time.time() - start_time) * 1000

            # 3. Extract speaker notes
            notes = await self._extract_speaker_notes(prs, slide_index)

            # 4. Save PNG file
            slide_path = self.slides_dir / f"{scene.id}.png"
            await self._write_slide_file(png_bytes, slide_path)

            # 5. Extract metadata (dimensions, text)
            metadata = await self._extract_slide_metadata(
                png_bytes,
                notes,
                render_latency_ms,
            )

            # 6. Emit QualityFeedbackEvent
            await self._emit_quality_feedback(
                scene_id=scene.id,
                slide_index=slide_index,
                render_latency_ms=render_latency_ms,
                file_size_bytes=len(png_bytes),
                text_detected=len(metadata.get("extracted_text", "")),
                confidence="medium",  # Rendering quality
            )

            # 7. Update results
            results["slide_files"][scene.id] = str(slide_path)
            results["metadata"][scene.id] = metadata
            results["slides_rendered"] += 1

        except Exception as e:
            results["slides_failed"] += 1
            print(f"Failed to render slide for scene {scene.id}: {str(e)}")

    async def _render_slide_to_png(
        self,
        prs: Any,
        slide_index: int,
        dpi: int,
    ) -> bytes:
        """
        Render slide to PNG bytes.

        Stub: generates dummy PNG.
        Production: use python-pptx + Pillow or LibreOffice UNO.
        """
        # Stub: generate 1920x1080 PNG at specified DPI
        # Real: prs.slides[slide_index].export_to_png(path, dpi=dpi)

        # Simulated PNG size: 1920x1080 at 150 DPI
        # Uncompressed: 1920 * 1080 * 3 bytes = 6,220,800 bytes
        # Compressed PNG: ~2MB typical
        simulated_size = 2_000_000
        return b'\x89PNG...' + (b'\x00' * (simulated_size - 8))

    async def _extract_speaker_notes(
        self,
        prs: Any,
        slide_index: int,
    ) -> str:
        """Extract speaker notes from slide."""
        # Stub: return empty (real: prs.slides[slide_index].notes_slide.notes_text_frame.text)
        return ""

    async def _write_slide_file(
        self,
        png_bytes: bytes,
        path: Path,
    ) -> None:
        """Write slide PNG to disk."""
        path.write_bytes(png_bytes)

    async def _extract_slide_metadata(
        self,
        png_bytes: bytes,
        notes: str,
        render_latency_ms: float,
    ) -> dict[str, Any]:
        """Extract metadata from rendered slide."""
        # Stub: return minimal metadata
        # Real: OCR text, detect shapes, extract colors, etc.
        return {
            "width": 1920,
            "height": 1080,
            "dpi": 150,
            "extracted_text": "",
            "speaker_notes": notes,
            "file_size_bytes": len(png_bytes),
            "render_latency_ms": render_latency_ms,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    async def _emit_quality_feedback(
        self,
        scene_id: str,
        slide_index: int,
        render_latency_ms: float,
        file_size_bytes: int,
        text_detected: int,
        confidence: str,
    ) -> None:
        """Emit QualityFeedbackEvent for slide rendering (ADR-0314)."""
        event_data = {
            "event_type": "slide_rendered",
            "scene_id": scene_id,
            "slide_index": slide_index,
            "render_latency_ms": render_latency_ms,
            "file_size_bytes": file_size_bytes,
            "text_detected": text_detected,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Fire-and-forget to event emitter
        try:
            await self.event_emitter.emit("slide_rendered", event_data)
        except Exception as e:
            # Emit failure doesn't block workflow (fail-closed: log, continue)
            print(f"Failed to emit slide_rendered event for {scene_id}: {str(e)}")
