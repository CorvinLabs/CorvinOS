#!/usr/bin/env python3
"""
Verify marketing claims about token savings against real measured data.

This script enforces truth in advertising by comparing marketing claims
against actual measured token consumption data.

Usage:
    python3 scripts/verify_token_savings_marketing_claims.py --period 2026-09
    python3 scripts/verify_token_savings_marketing_claims.py --period 2026-09 --claim 12.0

Output:
    {
      "marketing_claim_percent": 12.0,
      "measured_savings_percent": 11.8,
      "accuracy": "ACCURATE",
      "sample_size": 1248,
      "confidence_95pct": [10.2, 13.4],
      "status": "APPROVED FOR MARKETING"
    }

Exit Codes:
    0 = Claim is accurate (within ±2% margin), approved for marketing
    1 = Claim is inaccurate, requires update to marketing materials
    2 = Data not available for period (insufficient data collected)

Compliance:
    - Consumer Protection Law: Truth in advertising (marketing claims must be factual)
    - GDPR Art. 13/14: Transparency at collection (must disclose limitations)
    - ADR-0667 License Binding: Tier-based savings must be measurable and reported
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import statistics
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TokenSavingsVerifier:
    """Verifies marketing claims against measured data."""

    MARKETING_CLAIM_PERCENT = 12.0  # Default claim: ~12% savings for Paid tier
    ACCURACY_MARGIN_PERCENT = 2.0   # Allow ±2% for "approximately"
    MIN_SAMPLE_SIZE = 100            # Minimum samples to consider claim valid

    def __init__(self, corvin_home: Optional[Path] = None):
        """Initialize verifier.

        Args:
            corvin_home: Path to ~/.corvin (default: CORVIN_HOME env var or ~/.corvin)
        """
        if corvin_home is None:
            corvin_home = Path.home() / ".corvin"
        self.corvin_home = Path(corvin_home)

    def load_savings_data(self, period: str) -> Optional[list[Dict[str, Any]]]:
        """Load token savings data for a specific period.

        Args:
            period: Month in format "2026-09"

        Returns:
            List of savings records, or None if no data found
        """
        savings_file = (
            self.corvin_home
            / "tenants" / "_default" / "global" / "savings"
            / f"monthly_rollup_{period}.json"
        )

        if not savings_file.exists():
            logger.warning(f"Savings data file not found: {savings_file}")
            return None

        try:
            with open(savings_file, "r") as f:
                data = json.load(f)
            logger.info(f"Loaded {len(data)} savings records from {savings_file}")
            return data
        except Exception as e:
            logger.error(f"Failed to load savings data: {e}")
            return None

    def calculate_savings_percent(self, data: list[Dict[str, Any]], tier: str = "paid") -> Optional[float]:
        """Calculate actual savings percentage for a tier.

        Args:
            data: List of savings records
            tier: Tier to analyze ("free", "paid", "enterprise")

        Returns:
            Savings percentage, or None if insufficient data
        """
        tier_records = [r for r in data if r.get("tier") == tier]

        if len(tier_records) < self.MIN_SAMPLE_SIZE:
            logger.warning(
                f"Insufficient data for {tier} tier: "
                f"{len(tier_records)} records (need {self.MIN_SAMPLE_SIZE})"
            )
            return None

        total_baseline = sum(r.get("baseline_tokens", 0) for r in tier_records)
        total_actual = sum(r.get("actual_tokens", 0) for r in tier_records)

        if total_baseline == 0:
            logger.error("Baseline tokens is 0, cannot calculate savings")
            return None

        savings_percent = ((total_baseline - total_actual) / total_baseline) * 100
        logger.info(
            f"{tier.upper()} tier: {len(tier_records)} records, "
            f"baseline={total_baseline}, actual={total_actual}, "
            f"savings={savings_percent:.1f}%"
        )
        return savings_percent

    def calculate_confidence_interval(
        self,
        data: list[Dict[str, Any]],
        tier: str = "paid",
        confidence: float = 0.95
    ) -> Optional[tuple[float, float]]:
        """Calculate confidence interval for savings percentage.

        Args:
            data: List of savings records
            tier: Tier to analyze
            confidence: Confidence level (0.95 = 95%)

        Returns:
            (lower_bound, upper_bound) tuple, or None if insufficient data
        """
        tier_records = [r for r in data if r.get("tier") == tier]

        if len(tier_records) < self.MIN_SAMPLE_SIZE:
            return None

        # Calculate savings percent for each record
        savings_percents = []
        for record in tier_records:
            baseline = record.get("baseline_tokens", 0)
            actual = record.get("actual_tokens", 0)
            if baseline > 0:
                savings_pct = ((baseline - actual) / baseline) * 100
                savings_percents.append(savings_pct)

        if len(savings_percents) < 10:
            return None

        # Simple 95% CI using mean ± 1.96*std (normal approximation)
        mean = statistics.mean(savings_percents)
        stdev = statistics.stdev(savings_percents)
        n = len(savings_percents)

        # Standard error
        se = stdev / (n ** 0.5)

        # 95% CI
        z_score = 1.96
        lower = mean - (z_score * se)
        upper = mean + (z_score * se)

        return (lower, upper)

    def verify_claim(
        self,
        period: str,
        claim_percent: Optional[float] = None
    ) -> Dict[str, Any]:
        """Verify a marketing claim against measured data.

        Args:
            period: Month in format "2026-09"
            claim_percent: Marketing claim percentage (default: 12.0%)

        Returns:
            Dict with verification results
        """
        if claim_percent is None:
            claim_percent = self.MARKETING_CLAIM_PERCENT

        # Load data
        data = self.load_savings_data(period)
        if data is None:
            return {
                "status": "NO_DATA",
                "message": f"Savings data not available for period {period}",
                "period": period,
                "marketing_claim_percent": claim_percent,
            }

        # Calculate actual savings
        measured_savings = self.calculate_savings_percent(data, tier="paid")
        if measured_savings is None:
            return {
                "status": "INSUFFICIENT_DATA",
                "message": f"Insufficient data for period {period} (need {self.MIN_SAMPLE_SIZE} records)",
                "period": period,
                "marketing_claim_percent": claim_percent,
                "sample_size": len([r for r in data if r.get("tier") == "paid"]),
            }

        # Check accuracy (allow ±ACCURACY_MARGIN_PERCENT)
        delta = measured_savings - claim_percent
        within_margin = abs(delta) <= self.ACCURACY_MARGIN_PERCENT

        # Calculate confidence interval
        ci = self.calculate_confidence_interval(data, tier="paid")

        result = {
            "period": period,
            "marketing_claim_percent": claim_percent,
            "measured_savings_percent": round(measured_savings, 1),
            "delta_percent": round(delta, 1),
            "within_accuracy_margin": within_margin,
            "accuracy_margin_percent": self.ACCURACY_MARGIN_PERCENT,
            "sample_size": len([r for r in data if r.get("tier") == "paid"]),
            "status": "APPROVED FOR MARKETING" if within_margin else "REQUIRES UPDATE",
            "accuracy": "ACCURATE" if within_margin else "INACCURATE",
        }

        if ci:
            result["confidence_95pct"] = [round(ci[0], 1), round(ci[1], 1)]

        return result

    def generate_recommendation(self, result: Dict[str, Any]) -> str:
        """Generate marketing recommendation based on verification result.

        Args:
            result: Verification result dict

        Returns:
            Recommendation text
        """
        if result["status"] == "NO_DATA":
            return (
                f"⚠️ No data available for period {result['period']}. "
                f"Collect at least 2+ weeks of production data before marketing."
            )

        if result["status"] == "INSUFFICIENT_DATA":
            return (
                f"⚠️ Insufficient data for period {result['period']} "
                f"({result['sample_size']} records, need {self.MIN_SAMPLE_SIZE}). "
                f"Continue collecting data."
            )

        accuracy = result["accuracy"]
        measured = result["measured_savings_percent"]
        claim = result["marketing_claim_percent"]

        if accuracy == "ACCURATE":
            return (
                f"✅ Marketing claim is accurate. "
                f"Claim: ~{claim}% | Measured: {measured}% | "
                f"Difference: {result['delta_percent']}% (within ±{self.ACCURACY_MARGIN_PERCENT}% margin)"
            )
        else:
            return (
                f"❌ Marketing claim requires update. "
                f"Claim: ~{claim}% | Measured: {measured}% | "
                f"Difference: {result['delta_percent']}% (exceeds ±{self.ACCURACY_MARGIN_PERCENT}% margin). "
                f"\n   Recommended new claim: ~{round(measured)}% savings"
            )


def main():
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Verify marketing claims against measured token savings data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Verify default claim (12.0%) for September 2026
  python3 verify_token_savings_marketing_claims.py --period 2026-09

  # Verify custom claim for August 2026
  python3 verify_token_savings_marketing_claims.py --period 2026-08 --claim 10.5

  # Check status only (dry-run)
  python3 verify_token_savings_marketing_claims.py --period 2026-09 --dry-run

Exit Codes:
  0 = Claim is accurate and approved
  1 = Claim is inaccurate or data missing
  2 = Internal error
        """
    )

    parser.add_argument(
        "--period",
        required=True,
        help="Month in format YYYY-MM (e.g., 2026-09)"
    )
    parser.add_argument(
        "--claim",
        type=float,
        default=None,
        help="Marketing claim percentage (default: 12.0)"
    )
    parser.add_argument(
        "--corvin-home",
        type=Path,
        default=None,
        help="Path to ~/.corvin (default: CORVIN_HOME or ~/.corvin)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without enforcing exit code"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (machine-readable)"
    )

    args = parser.parse_args()

    try:
        verifier = TokenSavingsVerifier(corvin_home=args.corvin_home)
        result = verifier.verify_claim(args.period, claim_percent=args.claim)

        if args.json:
            # Output as JSON
            print(json.dumps(result, indent=2))
        else:
            # Output as human-readable text
            print("\n" + "="*70)
            print("TOKEN SAVINGS CLAIM VERIFICATION")
            print("="*70)
            print(f"Period:               {result.get('period', 'N/A')}")
            print(f"Marketing Claim:      {result.get('marketing_claim_percent', 'N/A')}%")
            print(f"Measured Savings:     {result.get('measured_savings_percent', 'N/A')}%")
            print(f"Difference:           {result.get('delta_percent', 'N/A')}%")
            print(f"Sample Size:          {result.get('sample_size', 'N/A')} records")

            if "confidence_95pct" in result:
                ci = result["confidence_95pct"]
                print(f"95% Confidence:       [{ci[0]}, {ci[1]}]%")

            print(f"Accuracy:             {result.get('accuracy', 'N/A')}")
            print(f"Status:               {result.get('status', 'N/A')}")
            print("\n" + verifier.generate_recommendation(result))
            print("="*70 + "\n")

        # Determine exit code
        if args.dry_run:
            return 0

        status = result.get("status", "ERROR")
        if status == "APPROVED FOR MARKETING":
            return 0
        else:
            return 1

    except Exception as e:
        logger.error(f"Verification failed: {e}", exc_info=True)
        print(f"❌ Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
