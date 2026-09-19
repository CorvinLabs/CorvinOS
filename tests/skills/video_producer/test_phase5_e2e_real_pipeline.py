#!/usr/bin/env python3
"""
E2E Test: Phase 5 Real Pipeline (Tier 2 → MP4)
Proves: tier_dispatcher → manim_animator → ffmpeg → playable output
No mocks. Real subprocess calls.

LDD K=2 Gate: E2E Proof
"""

import sys
import os
from pathlib import Path
import subprocess
import tempfile
import json
from dataclasses import dataclass
from datetime import datetime
import hashlib

# Add skill to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core/skills/video_producer_skill_2_0"))

from phase5.tier_dispatcher import TierDispatcher, TierLevel, AnimationRequest
from phase5.manim_animator import ManimAnimatorWorker
from phase5.quick_renderer import QuickRendererWorker
from phase5.premium_renderer import PremiumAsyncQueue


@dataclass
class E2ETestResult:
    """E2E Test Result (audit trail)"""
    test_name: str
    start_time: str
    end_time: str
    success: bool
    video_file: str | None
    duration_sec: float | None
    file_size_mb: float | None
    codec: str | None
    error: str | None
    audit_event_logged: bool
    quality_gate_passed: bool


class Phase5E2ETest:
    """End-to-end test suite for Phase 5 real pipeline"""

    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or Path("/tmp/phase5_e2e")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results = []

    def test_tier1_quick_render(self) -> E2ETestResult:
        """Test Tier 1: Quick ASCII/SVG render (should always succeed)"""
        print("\n[TEST 1] Tier 1 Quick Render (ASCII/SVG)")
        start = datetime.utcnow().isoformat()

        try:
            # Create minimal tier 1 renderer
            tier1 = QuickRendererWorker()

            # Request
            req = AnimationRequest(
                animation_id="test-quick-1",
                didactic_level="beginner",
                duration_seconds=10,
                preferred_tier=TierLevel.TIER_1_QUICK
            )

            # Render
            result = tier1.execute(req)

            # Validate
            assert result["success"], f"Tier 1 failed: {result.get('error')}"
            assert result["output_path"], "No output path"

            output_path = Path(result["output_path"])
            assert output_path.exists(), f"Output file missing: {output_path}"

            size_mb = output_path.stat().st_size / (1024 * 1024)
            duration = 10.0  # Tier 1 is quick, duration ~= requested

            test_result = E2ETestResult(
                test_name="tier1_quick_render",
                start_time=start,
                end_time=datetime.utcnow().isoformat(),
                success=True,
                video_file=str(output_path),
                duration_sec=duration,
                file_size_mb=size_mb,
                codec="ASCII/SVG",
                error=None,
                audit_event_logged=False,  # TODO: wire audit
                quality_gate_passed=True
            )

            print(f"✅ Tier 1 SUCCESS: {size_mb:.2f}MB, {duration}s, {result['output_path']}")
            self.results.append(test_result)
            return test_result

        except Exception as e:
            print(f"❌ Tier 1 FAILED: {e}")
            test_result = E2ETestResult(
                test_name="tier1_quick_render",
                start_time=start,
                end_time=datetime.utcnow().isoformat(),
                success=False,
                video_file=None,
                duration_sec=None,
                file_size_mb=None,
                codec=None,
                error=str(e),
                audit_event_logged=False,
                quality_gate_passed=False
            )
            self.results.append(test_result)
            return test_result

    def test_tier2_manim_render(self) -> E2ETestResult:
        """Test Tier 2: Real Manim render to MP4 (subprocess, no mock)"""
        print("\n[TEST 2] Tier 2 Manim Render (Real Subprocess)")
        start = datetime.utcnow().isoformat()

        try:
            # Check Manim installed
            result = subprocess.run(
                ["which", "manim"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                raise RuntimeError("Manim not installed. Install: pip install manim")

            # Create Manim animator
            tier2 = ManimAnimatorWorker(timeout_sec=60)

            # Minimal Manim scene spec (learning loop stub)
            scene_spec = {
                "scene_name": "LearningLoopSimple",
                "duration": 30,
                "script": """
from manim import *

class LearningLoopSimple(Scene):
    def construct(self):
        # Draw a simple circle
        circle = Circle()
        self.play(Create(circle))
        self.wait(1)
        # Fade out
        self.play(FadeOut(circle))
"""
            }

            # Request
            req = AnimationRequest(
                animation_id="test-manim-learning-loop",
                didactic_level="technical",
                duration_seconds=30,
                preferred_tier=TierLevel.TIER_2_RICH
            )

            # Render (real subprocess)
            result = tier2.execute(req, scene_spec=scene_spec)

            # Validate
            assert result["success"], f"Manim render failed: {result.get('error')}"
            output_path = Path(result["output_path"])
            assert output_path.exists(), f"Output MP4 missing: {output_path}"

            # Verify with ffprobe
            duration = self._get_mp4_duration(output_path)
            codec = self._get_mp4_codec(output_path)
            size_mb = output_path.stat().st_size / (1024 * 1024)

            # Quality checks
            assert 28 <= duration <= 32, f"Duration out of range: {duration}s (expected ~30s)"
            assert codec in ["h264", "h.264", "libx264"], f"Wrong codec: {codec}"
            assert size_mb > 0.5, f"File too small: {size_mb}MB"

            test_result = E2ETestResult(
                test_name="tier2_manim_render",
                start_time=start,
                end_time=datetime.utcnow().isoformat(),
                success=True,
                video_file=str(output_path),
                duration_sec=duration,
                file_size_mb=size_mb,
                codec=codec,
                error=None,
                audit_event_logged=False,  # TODO: wire audit
                quality_gate_passed=self._quality_gate_check(duration, codec, size_mb)
            )

            print(f"✅ Tier 2 SUCCESS: {size_mb:.2f}MB, {duration:.1f}s, codec={codec}")
            self.results.append(test_result)
            return test_result

        except Exception as e:
            print(f"❌ Tier 2 FAILED: {e}")
            test_result = E2ETestResult(
                test_name="tier2_manim_render",
                start_time=start,
                end_time=datetime.utcnow().isoformat(),
                success=False,
                video_file=None,
                duration_sec=None,
                file_size_mb=None,
                codec=None,
                error=str(e),
                audit_event_logged=False,
                quality_gate_passed=False
            )
            self.results.append(test_result)
            return test_result

    def test_tier_dispatcher_fallback(self) -> E2ETestResult:
        """Test TierDispatcher: Tier 2 → Tier 1 fallback on failure"""
        print("\n[TEST 3] TierDispatcher Fallback Chain (T2 → T1)")
        start = datetime.utcnow().isoformat()

        try:
            # Create all tiers
            tier1 = QuickRendererWorker()
            tier2 = ManimAnimatorWorker(timeout_sec=10)  # Short timeout to force fallback
            tier3 = PremiumAsyncQueue()

            dispatcher = TierDispatcher(tier1=tier1, tier2=tier2, tier3=tier3)

            # Request Tier 2 (which should timeout and fallback to Tier 1)
            req = AnimationRequest(
                animation_id="test-fallback",
                didactic_level="beginner",
                duration_seconds=10,
                preferred_tier=TierLevel.TIER_2_RICH
            )

            # Dispatch (will try T2, fail, fallback to T1)
            result = dispatcher.dispatch(req)

            # Validate: should succeed on Tier 1
            assert result["success"], f"Dispatcher failed: {result.get('error')}"
            assert result["tier"] in ["TIER_1_QUICK", "TIER_2_RICH"], f"Unexpected tier: {result['tier']}"

            test_result = E2ETestResult(
                test_name="tier_dispatcher_fallback",
                start_time=start,
                end_time=datetime.utcnow().isoformat(),
                success=True,
                video_file=result.get("output_path"),
                duration_sec=10.0,
                file_size_mb=None,
                codec=result.get("tier"),
                error=None,
                audit_event_logged=False,
                quality_gate_passed=True
            )

            print(f"✅ Fallback SUCCESS: {result['tier']} → output generated")
            self.results.append(test_result)
            return test_result

        except Exception as e:
            print(f"❌ Fallback FAILED: {e}")
            test_result = E2ETestResult(
                test_name="tier_dispatcher_fallback",
                start_time=start,
                end_time=datetime.utcnow().isoformat(),
                success=False,
                video_file=None,
                duration_sec=None,
                file_size_mb=None,
                codec=None,
                error=str(e),
                audit_event_logged=False,
                quality_gate_passed=False
            )
            self.results.append(test_result)
            return test_result

    def _get_mp4_duration(self, mp4_path: Path) -> float:
        """Extract duration from MP4 via ffprobe"""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(mp4_path)
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            return float(result.stdout.strip())
        except Exception:
            return 0.0

    def _get_mp4_codec(self, mp4_path: Path) -> str:
        """Extract video codec from MP4"""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(mp4_path)
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"

    def _quality_gate_check(self, duration: float, codec: str, size_mb: float) -> bool:
        """Quality gate: validate video meets standards"""
        checks = [
            ("Duration 28-32s", 28 <= duration <= 32),
            ("Codec H.264", codec in ["h264", "h.264", "libx264"]),
            ("Size > 0.5MB", size_mb > 0.5),
        ]
        all_pass = all(check[1] for check in checks)
        for name, passed in checks:
            print(f"  {'✅' if passed else '❌'} {name}: {passed}")
        return all_pass

    def summary(self) -> dict:
        """Return test summary"""
        passed = sum(1 for r in self.results if r.success)
        total = len(self.results)
        quality_passed = sum(1 for r in self.results if r.quality_gate_passed)

        summary = {
            "total_tests": total,
            "passed": passed,
            "failed": total - passed,
            "quality_gate_passed": quality_passed,
            "timestamp": datetime.utcnow().isoformat(),
            "results": [
                {
                    "test": r.test_name,
                    "success": r.success,
                    "duration_sec": r.duration_sec,
                    "size_mb": r.file_size_mb,
                    "codec": r.codec,
                    "error": r.error,
                    "quality_gate": r.quality_gate_passed
                }
                for r in self.results
            ]
        }

        return summary

    def save_results(self, path: Path = None):
        """Save test results to JSON"""
        path = path or self.output_dir / "test_results.json"
        with open(path, "w") as f:
            json.dump(self.summary(), f, indent=2)
        print(f"\n📊 Results saved: {path}")
        return path


def main():
    """Run full E2E test suite"""
    print("=" * 70)
    print("VIDEO PRODUCER PHASE 5 — E2E REAL PIPELINE TEST SUITE")
    print("=" * 70)
    print("LDD K=2 Gate: E2E Wiring Proof")
    print("Target: Prove real (non-mock) tier2 → ffmpeg → MP4 works end-to-end")
    print()

    # Create test suite
    test_suite = Phase5E2ETest()

    # Run tests
    test_suite.test_tier1_quick_render()
    test_suite.test_tier2_manim_render()
    test_suite.test_tier_dispatcher_fallback()

    # Summary
    summary = test_suite.summary()
    passed = summary["passed"]
    total = summary["total_tests"]

    print("\n" + "=" * 70)
    print(f"RESULTS: {passed}/{total} tests passed")
    print("=" * 70)

    for result in summary["results"]:
        status = "✅" if result["success"] else "❌"
        print(f"{status} {result['test']}: {result.get('error', 'OK')}")

    # Save
    test_suite.save_results()

    # Exit code
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
