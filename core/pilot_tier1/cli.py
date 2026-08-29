#!/usr/bin/env python3
"""Tier 1 Pilot CLI — Plugin installation & management via task engine."""

import argparse
import sys
from pathlib import Path
from typing import Optional

from tier0_bootstrap import BootstrapManager, Config
from tier1_session import SessionManager, SessionStatus
from tier2_task_engine import TaskPayload, TaskType, TaskEngine
from tier3_brain_core import BrainCore, MetricsCollector


def get_manager(tenant_id: str = "_default") -> tuple:
    """Initialize all tiers and return manager instances.

    Returns:
        (bootstrap_manager, session_manager, task_engine, brain_core)
    """
    config = Config(tenant_id=tenant_id)
    bootstrap = BootstrapManager(config)
    bootstrap.boot()

    db_conn = bootstrap.get_database_connection()
    sessions = SessionManager(db_conn, bootstrap.audit)
    tasks = TaskEngine(db_conn, bootstrap.audit)
    brain = BrainCore(tasks, sessions, bootstrap.audit)
    brain.set_metrics_collector(MetricsCollector(db_conn))

    return bootstrap, sessions, tasks, brain


def cmd_install(args):
    """Install plugin: `pilot install <plugin-id>`"""
    print(f"📦 Installing plugin: {args.plugin_id}")

    _, sessions, _, brain = get_manager(args.tenant)

    # Register mock handler (for pilot)
    def install_handler(payload):
        print(f"  ✓ Installing {payload.plugin_id} v{payload.version}")
        return {
            "success": True,
            "plugin_id": payload.plugin_id,
            "action": "installed",
        }

    brain.task_engine.executor.register_handler(TaskType.PLUGIN_INSTALL, install_handler)

    # Create session and task
    session_id = sessions.begin_session(
        details={"action": "install", "plugin": args.plugin_id}
    )
    sessions.activate_session(session_id)

    payload = TaskPayload(
        task_type=TaskType.PLUGIN_INSTALL,
        plugin_id=args.plugin_id,
        version=args.version or "latest",
    )

    # Execute
    result = brain.submit_and_wait(session_id, payload, timeout_seconds=30.0)

    # Report
    if result.get("success"):
        print(f"✅ {args.plugin_id} installed successfully")
        return 0
    else:
        print(f"❌ Installation failed: {result.get('error', 'unknown error')}")
        return 1


def cmd_enable(args):
    """Enable plugin: `pilot enable <plugin-id>`"""
    print(f"🟢 Enabling plugin: {args.plugin_id}")

    _, sessions, _, brain = get_manager(args.tenant)

    def enable_handler(payload):
        print(f"  ✓ Enabling {payload.plugin_id}")
        return {"success": True, "plugin_id": payload.plugin_id, "action": "enabled"}

    brain.task_engine.executor.register_handler(TaskType.PLUGIN_ENABLE, enable_handler)

    session_id = sessions.begin_session(
        details={"action": "enable", "plugin": args.plugin_id}
    )
    sessions.activate_session(session_id)

    payload = TaskPayload(
        task_type=TaskType.PLUGIN_ENABLE,
        plugin_id=args.plugin_id,
    )

    result = brain.submit_and_wait(session_id, payload, timeout_seconds=30.0)

    if result.get("success"):
        print(f"✅ {args.plugin_id} enabled")
        return 0
    else:
        print(f"❌ Enable failed: {result.get('error', 'unknown error')}")
        return 1


def cmd_disable(args):
    """Disable plugin: `pilot disable <plugin-id>`"""
    print(f"🔴 Disabling plugin: {args.plugin_id}")

    _, sessions, _, brain = get_manager(args.tenant)

    def disable_handler(payload):
        print(f"  ✓ Disabling {payload.plugin_id}")
        return {"success": True, "plugin_id": payload.plugin_id, "action": "disabled"}

    brain.task_engine.executor.register_handler(TaskType.PLUGIN_DISABLE, disable_handler)

    session_id = sessions.begin_session(
        details={"action": "disable", "plugin": args.plugin_id}
    )
    sessions.activate_session(session_id)

    payload = TaskPayload(
        task_type=TaskType.PLUGIN_DISABLE,
        plugin_id=args.plugin_id,
    )

    result = brain.submit_and_wait(session_id, payload, timeout_seconds=30.0)

    if result.get("success"):
        print(f"✅ {args.plugin_id} disabled")
        return 0
    else:
        print(f"❌ Disable failed: {result.get('error', 'unknown error')}")
        return 1


def cmd_uninstall(args):
    """Uninstall plugin: `pilot uninstall <plugin-id>`"""
    print(f"🗑️  Uninstalling plugin: {args.plugin_id}")

    _, sessions, _, brain = get_manager(args.tenant)

    def uninstall_handler(payload):
        print(f"  ✓ Uninstalling {payload.plugin_id}")
        return {"success": True, "plugin_id": payload.plugin_id, "action": "uninstalled"}

    brain.task_engine.executor.register_handler(TaskType.PLUGIN_UNINSTALL, uninstall_handler)

    session_id = sessions.begin_session(
        details={"action": "uninstall", "plugin": args.plugin_id}
    )
    sessions.activate_session(session_id)

    payload = TaskPayload(
        task_type=TaskType.PLUGIN_UNINSTALL,
        plugin_id=args.plugin_id,
    )

    result = brain.submit_and_wait(session_id, payload, timeout_seconds=30.0)

    if result.get("success"):
        print(f"✅ {args.plugin_id} uninstalled")
        return 0
    else:
        print(f"❌ Uninstall failed: {result.get('error', 'unknown error')}")
        return 1


def cmd_status(args):
    """Show plugin/session status: `pilot status [--session <id>]`"""
    _, sessions, _, _ = get_manager(args.tenant)

    if args.session:
        session = sessions.load_session(args.session)
        if not session:
            print(f"❌ Session {args.session} not found")
            return 1

        print(f"Session: {session['session_id']}")
        print(f"  Status: {session['status']}")
        print(f"  Created: {session['created_at']}")
        print(f"  Updated: {session['updated_at']}")
        return 0
    else:
        sessions_list = sessions.list_sessions()
        if not sessions_list:
            print("No sessions yet")
            return 0

        print("Sessions:")
        for sess in sessions_list[:10]:  # Show last 10
            print(f"  {sess['session_id'][:16]}... {sess['status']} ({sess['created_at']})")

        if len(sessions_list) > 10:
            print(f"  ... and {len(sessions_list) - 10} more")

        return 0


def cmd_health(args):
    """Health check: `pilot health`"""
    print("🏥 Health Check")

    try:
        bootstrap, sessions, _, _ = get_manager(args.tenant)

        # Verify bootstrap
        if not bootstrap.is_initialized:
            print("❌ Bootstrap not initialized")
            return 1

        print("✅ Bootstrap: OK")

        # Verify audit chain
        if not bootstrap.audit.verify_chain():
            print("❌ Audit chain corrupted")
            return 1

        print("✅ Audit chain: OK")

        # Verify database
        sessions_list = sessions.list_sessions()
        print(f"✅ Database: OK ({len(sessions_list)} sessions)")

        return 0

    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Tier 1 Pilot — Plugin Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pilot install slack-notifier
  pilot enable slack-notifier
  pilot disable slack-notifier
  pilot uninstall slack-notifier
  pilot status
  pilot health

For help with a specific command:
  pilot install --help
        """,
    )

    parser.add_argument(
        "--tenant",
        default="_default",
        help="Tenant ID (default: _default)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # install
    install_parser = subparsers.add_parser("install", help="Install plugin")
    install_parser.add_argument("plugin_id", help="Plugin ID (e.g., slack-notifier)")
    install_parser.add_argument("--version", help="Plugin version (default: latest)")
    install_parser.set_defaults(func=cmd_install)

    # enable
    enable_parser = subparsers.add_parser("enable", help="Enable plugin")
    enable_parser.add_argument("plugin_id", help="Plugin ID")
    enable_parser.set_defaults(func=cmd_enable)

    # disable
    disable_parser = subparsers.add_parser("disable", help="Disable plugin")
    disable_parser.add_argument("plugin_id", help="Plugin ID")
    disable_parser.set_defaults(func=cmd_disable)

    # uninstall
    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall plugin")
    uninstall_parser.add_argument("plugin_id", help="Plugin ID")
    uninstall_parser.set_defaults(func=cmd_uninstall)

    # status
    status_parser = subparsers.add_parser("status", help="Show status")
    status_parser.add_argument("--session", help="Show specific session")
    status_parser.set_defaults(func=cmd_status)

    # health
    health_parser = subparsers.add_parser("health", help="Health check")
    health_parser.set_defaults(func=cmd_health)

    # Parse and execute
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    if hasattr(args, "func"):
        return args.func(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
