"""Tier Dispatcher — Route animation requests to best tier

Orchestrates 3-tier fallback chain:
  Try Tier 3 (Premium) → Tier 2 (Manim) → Tier 1 (Quick)

With Phase 5 Voice-Sync Integration (ADR-0742):
  - Each tier render produces frame sequence
  - Voice-Sync Compositor integrates narration timing
  - Final output: H.264 MP4 with narration-synced animation
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
import time
from pathlib import Path

from voice_sync_mapper import VoiceSyncMapper, VoiceSyncMapping
from voice_sync_compositor import VoiceSyncCompositor, VoiceSyncConfig


class TierLevel(Enum):
    TIER_3_PREMIUM = 3  # Hand-crafted, async
    TIER_2_RICH = 2      # Manim, <60s
    TIER_1_QUICK = 1     # ASCII/SVG, <10s


@dataclass
class AnimationRequest:
    animation_id: str
    didactic_level: str
    duration_seconds: int
    preferred_tier: TierLevel = TierLevel.TIER_2_RICH
    narration_audio: Optional[str] = None  # Optional MP3 narration
    voice_sync_mapping: Optional[dict] = None  # Optional voice-sync data


class TierDispatcher:
    """Route animation requests to appropriate tier with fallback

    Phase 5 Enhancement (ADR-0742):
    - Each tier produces frame sequence
    - If narration_audio + voice_sync_mapping provided, composits final video
    - Otherwise returns frame directory path
    """

    def __init__(self, tier1, tier2, tier3):
        self.tier1 = tier1  # QuickRendererWorker
        self.tier2 = tier2  # ManimAnimatorWorker
        self.tier3 = tier3  # PremiumAsyncQueue

        self.voice_sync_compositor = VoiceSyncCompositor()
        self.voice_sync_mapper = VoiceSyncMapper()

        self.tier_metrics = {
            TierLevel.TIER_1_QUICK: {"success_count": 0, "fail_count": 0},
            TierLevel.TIER_2_RICH: {"success_count": 0, "fail_count": 0},
            TierLevel.TIER_3_PREMIUM: {"success_count": 0, "fail_count": 0},
        }

    def dispatch(self, request: AnimationRequest) -> dict:
        """Dispatch request to appropriate tier with fallback

        Phase 5 (Voice-Sync): If narration_audio provided, integrates via
        VoiceSyncCompositor for narration-synced MP4 output.
        """

        # Try preferred tier first
        result = self._try_tier(request.preferred_tier, request)

        if result["success"]:
            print(f"✅ {request.preferred_tier.name}: {request.animation_id}")

            # Phase 5: Apply voice-sync if narration provided
            if request.narration_audio and request.voice_sync_mapping:
                result = self._apply_voice_sync(result, request)

            return result

        # Fallback to next tier
        fallback_tiers = self._get_fallback_chain(request.preferred_tier)

        for tier in fallback_tiers:
            print(f"⚠️  Falling back to {tier.name}...")
            result = self._try_tier(tier, request)

            if result["success"]:
                print(f"✅ {tier.name}: {request.animation_id}")

                # Phase 5: Apply voice-sync if narration provided
                if request.narration_audio and request.voice_sync_mapping:
                    result = self._apply_voice_sync(result, request)

                return result

        # All tiers failed
        return {
            "success": False,
            "error": "All tiers failed",
            "tier": None,
            "output_path": None
        }

    def _try_tier(self, tier: TierLevel, request: AnimationRequest) -> dict:
        """Try to render with specific tier"""

        try:
            if tier == TierLevel.TIER_3_PREMIUM:
                # Submit async job
                job_id = self.tier3.submit_job(request.animation_id)
                self.tier_metrics[tier]["success_count"] += 1
                return {
                    "success": True,
                    "tier": tier.name,
                    "job_id": job_id,
                    "status": "queued"
                }

            elif tier == TierLevel.TIER_2_RICH:
                # Call Manim animator (blocking)
                start = time.time()
                result = self.tier2.execute(request)
                elapsed_ms = int((time.time() - start) * 1000)

                if result.get("success") and elapsed_ms < 60000:
                    self.tier_metrics[tier]["success_count"] += 1
                    return {
                        "success": True,
                        "tier": tier.name,
                        "output_path": result.get("output_path"),
                        "render_time_ms": elapsed_ms
                    }
                else:
                    self.tier_metrics[tier]["fail_count"] += 1
                    return {"success": False, "error": "Timeout or render error"}

            elif tier == TierLevel.TIER_1_QUICK:
                # Call quick renderer (blocking, always succeeds)
                result = self.tier1.execute(request)
                if result.get("success"):
                    self.tier_metrics[tier]["success_count"] += 1
                    return {
                        "success": True,
                        "tier": tier.name,
                        "output_path": result.get("output_path"),
                        "render_time_ms": result.get("render_time_ms", 5000)
                    }

        except Exception as e:
            self.tier_metrics[tier]["fail_count"] += 1
            return {"success": False, "error": str(e)}

        return {"success": False, "error": "Unknown error"}

    def _get_fallback_chain(self, preferred_tier: TierLevel) -> list:
        """Get fallback chain (deterministic)"""

        chains = {
            TierLevel.TIER_3_PREMIUM: [TierLevel.TIER_2_RICH, TierLevel.TIER_1_QUICK],
            TierLevel.TIER_2_RICH: [TierLevel.TIER_1_QUICK],
            TierLevel.TIER_1_QUICK: [],  # No fallback (always succeeds)
        }

        return chains.get(preferred_tier, [])

    def _apply_voice_sync(self, tier_result: dict, request: AnimationRequest) -> dict:
        """Apply voice-sync composition to tier output (Phase 5)

        Args:
            tier_result: Result from tier rendering (contains frame_dir or output_path)
            request: AnimationRequest with narration_audio and voice_sync_mapping

        Returns:
            Updated result with voice-synced MP4 output_path
        """

        try:
            frame_dir = tier_result.get("frame_dir") or tier_result.get("output_path")

            # Check if frame directory exists
            frame_path = Path(frame_dir)
            if not frame_path.exists():
                print(f"⚠️  Frame directory not found: {frame_dir}")
                return tier_result  # Return tier result as-is

            # Prepare output MP4 path
            output_mp4 = frame_path.parent / f"{request.animation_id}_voice_synced.mp4"

            print(f"🎬 Applying voice-sync: {output_mp4}")

            # Composite with voice-sync
            sync_result = self.voice_sync_compositor.composite_with_voice_sync(
                frame_dir=str(frame_path),
                narration_audio=request.narration_audio,
                voice_sync_mapping=request.voice_sync_mapping,
                output_path=str(output_mp4)
            )

            if sync_result.get("success"):
                # Update metrics
                self.tier_metrics[request.preferred_tier]["success_count"] += 1

                # Return updated result with MP4 path
                return {
                    **tier_result,
                    "output_path": str(output_mp4),
                    "voice_sync_applied": True,
                    "sync_result": sync_result,
                    "keyframe_count": len(request.voice_sync_mapping.get("frame_to_event", {}))
                }
            else:
                print(f"⚠️  Voice-sync failed: {sync_result.get('error')}")
                self.tier_metrics[request.preferred_tier]["fail_count"] += 1

                # Return tier result (frames only, no sync)
                return {
                    **tier_result,
                    "voice_sync_failed": True,
                    "voice_sync_error": sync_result.get("error")
                }

        except Exception as e:
            print(f"❌ Voice-sync error: {e}")
            self.tier_metrics[request.preferred_tier]["fail_count"] += 1
            return {
                **tier_result,
                "voice_sync_failed": True,
                "voice_sync_error": str(e)
            }

    def get_metrics(self) -> dict:
        """Get tier performance metrics"""
        return {
            "tier_1_quick": self.tier_metrics[TierLevel.TIER_1_QUICK],
            "tier_2_rich": self.tier_metrics[TierLevel.TIER_2_RICH],
            "tier_3_premium": self.tier_metrics[TierLevel.TIER_3_PREMIUM],
            "timestamp": str(time.time())
        }
