#!/usr/bin/env python3
"""CorvinOS RAG CLI — Register and manage RAG providers.

Usage:
  corvin-rag register <manifest.yaml>
  corvin-rag list [--active|--all]
  corvin-rag show <provider-id>
  corvin-rag health <provider-id>
  corvin-rag unregister <provider-id> [--confirm]
  corvin-rag --version
  corvin-rag --help
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..registry.rag_registry import RAGRegistry, get_default_registry_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(__name__)

__version__ = "0.1.0"


class RagCLI:
    """CorvinOS RAG CLI."""

    def __init__(self, tenant_id: str = "_default"):
        """Initialize CLI."""
        self.tenant_id = tenant_id
        self.registry_dir = get_default_registry_dir(tenant_id)
        self.registry = RAGRegistry(self.registry_dir)

    def cmd_register(self, args: argparse.Namespace) -> int:
        """Register a new RAG provider."""
        manifest_path = Path(args.manifest)

        if not manifest_path.exists():
            logger.error(f"❌ File not found: {manifest_path}")
            return 1

        success, message = self.registry.register(manifest_path, self.tenant_id)

        if success:
            logger.info(message)
            return 0
        else:
            logger.error(f"❌ {message}")
            return 1

    def cmd_list(self, args: argparse.Namespace) -> int:
        """List registered providers."""
        status = None
        if args.active:
            status = "active"

        providers = self.registry.list_providers(status)

        if not providers:
            logger.info("No providers registered")
            return 0

        # Header
        print("\n📋 RAG Providers:")
        print(f"{'ID':<30} {'Name':<30} {'Status':<12} {'Health':<12}")
        print("-" * 84)

        for p in providers:
            print(
                f"{p.id:<30} {p.name:<30} {p.status:<12} {p.health_status:<12}"
            )

        print(f"\nTotal: {len(providers)} provider(s)\n")
        return 0

    def cmd_show(self, args: argparse.Namespace) -> int:
        """Show provider details."""
        provider_id = args.provider_id

        entry = self.registry.get_provider(provider_id)
        if not entry:
            logger.error(f"❌ Provider not found: {provider_id}")
            return 1

        manifest = self.registry.get_manifest(provider_id)
        if not manifest:
            logger.error(f"❌ Manifest not found: {provider_id}")
            return 1

        # Display entry
        print(f"\n📌 Provider: {entry.id}")
        print(f"   Name: {entry.name}")
        print(f"   Version: {entry.version}")
        print(f"   Status: {entry.status}")
        print(f"   Health: {entry.health_status}")
        print(f"   Registered: {entry.registered_at}")
        print(f"   Last Health Check: {entry.last_health_check or 'Never'}")
        print()
        print(f"   Query Stats:")
        print(f"     Total: {entry.query_stats.get('total', 0)}")
        print(f"     Today: {entry.query_stats.get('today', 0)}")
        print(f"     Avg Latency: {entry.query_stats.get('avg_latency_ms', 0)}ms")
        print()

        # Display manifest metadata
        metadata = manifest.get("metadata", {})
        spec = manifest.get("spec", {})

        print(f"   Data Type: {spec.get('classification', {}).get('data_type', 'N/A')}")
        print(f"   Endpoint: {spec.get('retrieval', {}).get('endpoint', 'N/A')}")
        print()

        return 0

    def cmd_health(self, args: argparse.Namespace) -> int:
        """Check health of a provider."""
        provider_id = args.provider_id

        entry = self.registry.get_provider(provider_id)
        if not entry:
            logger.error(f"❌ Provider not found: {provider_id}")
            return 1

        manifest = self.registry.get_manifest(provider_id)
        if not manifest:
            logger.error(f"❌ Manifest not found: {provider_id}")
            return 1

        # Run health check
        success, message = RAGRegistry._health_check(manifest)

        if success:
            logger.info(f"✅ {provider_id}: {message}")
            self.registry.update_health_status(provider_id, "healthy")
            return 0
        else:
            logger.error(f"❌ {provider_id}: {message}")
            self.registry.update_health_status(provider_id, "degraded")
            return 1

    def cmd_unregister(self, args: argparse.Namespace) -> int:
        """Unregister a RAG provider."""
        provider_id = args.provider_id

        entry = self.registry.get_provider(provider_id)
        if not entry:
            logger.error(f"❌ Provider not found: {provider_id}")
            return 1

        if not args.confirm:
            logger.warning(
                f"⚠️  This will unregister provider: {entry.id} ({entry.name})"
            )
            logger.info("Use --confirm to proceed")
            return 1

        success, message = self.registry.unregister(provider_id)

        if success:
            logger.info(message)
            return 0
        else:
            logger.error(f"❌ {message}")
            return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CorvinOS RAG Provider Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  corvin-rag register elasticsearch-docs.yaml
  corvin-rag list --active
  corvin-rag show elasticsearch-docs
  corvin-rag health elasticsearch-docs
  corvin-rag unregister elasticsearch-docs --confirm
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"corvin-rag {__version__}",
    )

    parser.add_argument(
        "--tenant",
        default="_default",
        help="Tenant ID (default: _default)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # register command
    register_parser = subparsers.add_parser("register", help="Register a new provider")
    register_parser.add_argument("manifest", help="Path to provider manifest (YAML/JSON)")

    # list command
    list_parser = subparsers.add_parser("list", help="List registered providers")
    list_parser.add_argument(
        "--active",
        action="store_true",
        help="Show only active providers",
    )

    # show command
    show_parser = subparsers.add_parser("show", help="Show provider details")
    show_parser.add_argument("provider_id", help="Provider ID")

    # health command
    health_parser = subparsers.add_parser("health", help="Check provider health")
    health_parser.add_argument("provider_id", help="Provider ID")

    # unregister command
    unregister_parser = subparsers.add_parser("unregister", help="Unregister a provider")
    unregister_parser.add_argument("provider_id", help="Provider ID")
    unregister_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm unregistration",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Create CLI instance
    cli = RagCLI(tenant_id=args.tenant)

    # Dispatch command
    try:
        if args.command == "register":
            return cli.cmd_register(args)
        elif args.command == "list":
            return cli.cmd_list(args)
        elif args.command == "show":
            return cli.cmd_show(args)
        elif args.command == "health":
            return cli.cmd_health(args)
        elif args.command == "unregister":
            return cli.cmd_unregister(args)
        else:
            parser.print_help()
            return 1
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
