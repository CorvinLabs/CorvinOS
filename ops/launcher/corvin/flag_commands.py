"""CLI commands for feature tier management (Phase 4, ADR-0286/0288).

Commands:
  corvin flag promote <id> <tier> [--force]  - Promote flag to tier (--force bypasses gates)
  corvin flag demote <id> <tier>             - Demote flag to tier
  corvin flag status [<id>]                  - Show flag tier and metrics
  corvin flag history <id> [--days N]        - Show promotion/demotion history
"""
import argparse
from dataclasses import asdict
from typing import Optional

from core.console.corvin_console.feature_flags import tier_of
from core.console.corvin_console.promotion_daemon import AuditEvent
from core.telemetry import get_flag_metrics


def cmd_promote(args) -> int:
    """Promote a flag to a tier. Optionally bypass gates with --force."""
    flag_id = args.id
    target_tier = args.tier
    force = args.force

    current_tier = tier_of(flag_id)
    print(f"Flag {flag_id}: {current_tier} → {target_tier}" + (" (forced)" if force else ""))

    if not force:
        # TODO: Check promotion gates from promotion_daemon
        print("(Promotion gates would be checked here in real implementation)")

    # TODO: Update registry with new tier
    # TODO: Log audit event
    print("✓ Promoted (not persisted in this Phase 4 skeleton)")
    return 0


def cmd_demote(args) -> int:
    """Demote a flag to a tier."""
    flag_id = args.id
    target_tier = args.tier

    current_tier = tier_of(flag_id)
    print(f"Flag {flag_id}: {current_tier} → {target_tier}")

    # TODO: Update registry with new tier
    # TODO: Log audit event
    print("✓ Demoted (not persisted in this Phase 4 skeleton)")
    return 0


def cmd_status(args) -> int:
    """Show flag tier and metrics."""
    if args.id:
        # Show one flag
        flag_id = args.id
        tier = tier_of(flag_id)
        metrics = get_flag_metrics(flag_id).get_24h_stats()

        print(f"\nFlag: {flag_id}")
        print(f"Tier: {tier}")
        print(f"Error Rate (24h): {metrics.get('error_rate_24h', 0.0):.2%}")
        print(f"Invocations (24h): {metrics.get('invocation_count_24h', 0)}")
        print(f"Days Since Error: {metrics.get('days_since_last_error', 'N/A')}")
        return 0
    else:
        # Show all flags (abbreviated)
        from core.console.corvin_console.feature_flags import REGISTRY

        print("\nFeature Flags by Tier:")
        tiers = {"alpha": [], "beta": [], "stable": [], "production": []}
        for flag in REGISTRY:
            tiers[flag.release_tier].append(flag.id)

        for tier, flags in tiers.items():
            print(f"\n  {tier.upper()}: {len(flags)} flags")
            for fid in flags[:3]:  # Show first 3
                print(f"    - {fid}")
            if len(flags) > 3:
                print(f"    ... and {len(flags) - 3} more")
        return 0


def cmd_history(args) -> int:
    """Show promotion/demotion history for a flag."""
    flag_id = args.id
    days = args.days

    print(f"\nHistory for {flag_id} (last {days} days):")
    print("  (Audit events would be fetched from database here)")
    print("  - 2026-08-10 10:00 UTC: auto-promoted alpha → beta (error rate 0.02%)")
    print("  - 2026-08-05 14:30 UTC: manual-promoted by shumway alpha → beta")
    return 0


def add_flag_subcommands(subparsers):
    """Add flag subcommands to argparse."""
    # promote
    promote_parser = subparsers.add_parser(
        "promote",
        help="Promote flag to tier",
    )
    promote_parser.add_argument("id", help="Flag ID")
    promote_parser.add_argument("tier", choices=["alpha", "beta", "stable", "production"])
    promote_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass automatic gates (maintainer override)",
    )
    promote_parser.set_defaults(func=cmd_promote)

    # demote
    demote_parser = subparsers.add_parser(
        "demote",
        help="Demote flag to tier",
    )
    demote_parser.add_argument("id", help="Flag ID")
    demote_parser.add_argument("tier", choices=["alpha", "beta", "stable", "production"])
    demote_parser.set_defaults(func=cmd_demote)

    # status
    status_parser = subparsers.add_parser(
        "status",
        help="Show flag status and metrics",
    )
    status_parser.add_argument("id", nargs="?", help="Flag ID (omit to show all)")
    status_parser.set_defaults(func=cmd_status)

    # history
    history_parser = subparsers.add_parser(
        "history",
        help="Show flag promotion/demotion history",
    )
    history_parser.add_argument("id", help="Flag ID")
    history_parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Days to show (default 30)",
    )
    history_parser.set_defaults(func=cmd_history)


def main(args: Optional[list] = None) -> int:
    """Main entry point for flag commands."""
    parser = argparse.ArgumentParser(
        description="Feature flag tier management (Phase 4 - ADR-0288)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    add_flag_subcommands(subparsers)

    parsed = parser.parse_args(args)
    if not parsed.command:
        parser.print_help()
        return 1

    return parsed.func(parsed)


if __name__ == "__main__":
    import sys

    sys.exit(main())
