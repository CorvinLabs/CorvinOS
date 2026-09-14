"""Tier Dispatcher — Route animation requests to best tier

Orchestrates 3-tier fallback chain:
  Try Tier 3 (Premium) → Tier 2 (Manim) → Tier 1 (Quick)
"""

from enum import Enum
from dataclasses import dataclass
import time


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


class TierDispatcher:
    """Route animation requests to appropriate tier with fallback"""

    def __init__(self, tier1, tier2, tier3):
        self.tier1 = tier1  # QuickRendererWorker
        self.tier2 = tier2  # ManimAnimatorWorker
        self.tier3 = tier3  # PremiumAsyncQueue

        self.tier_metrics = {
            TierLevel.TIER_1_QUICK: {"success_count": 0, "fail_count": 0},
            TierLevel.TIER_2_RICH: {"success_count": 0, "fail_count": 0},
            TierLevel.TIER_3_PREMIUM: {"success_count": 0, "fail_count": 0},
        }

    def dispatch(self, request: AnimationRequest) -> dict:
        """Dispatch request to appropriate tier with fallback"""

        # Try preferred tier first
        result = self._try_tier(request.preferred_tier, request)

        if result["success"]:
            print(f"✅ {request.preferred_tier.name}: {request.animation_id}")
            return result

        # Fallback to next tier
        fallback_tiers = self._get_fallback_chain(request.preferred_tier)

        for tier in fallback_tiers:
            print(f"⚠️  Falling back to {tier.name}...")
            result = self._try_tier(tier, request)

            if result["success"]:
                print(f"✅ {tier.name}: {request.animation_id}")
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

    def get_metrics(self) -> dict:
        """Get tier performance metrics"""
        return {
            "tier_1_quick": self.tier_metrics[TierLevel.TIER_1_QUICK],
            "tier_2_rich": self.tier_metrics[TierLevel.TIER_2_RICH],
            "tier_3_premium": self.tier_metrics[TierLevel.TIER_3_PREMIUM],
            "timestamp": str(time.time())
        }
