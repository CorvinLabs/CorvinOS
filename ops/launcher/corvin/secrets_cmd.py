"""`corvin secrets set|get|delete|list|migrate` — manage encrypted secrets.

These commands manage encrypted secrets in the current tenant's secrets.enc.

    corvin secrets set KEY VALUE         set a secret value
    corvin secrets get KEY               get a secret value
    corvin secrets delete KEY            delete a secret
    corvin secrets list                  list all secret keys
    corvin secrets migrate               migrate from legacy .env
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Imported lazily — only when a secrets command is actually invoked.


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# ── corvin secrets set ───────────────────────────────────────────────────────

def cmd_set(args: argparse.Namespace) -> int:
    """Set a secret value."""
    try:
        from operator.bridges.shared.provider_keys import SecretsStore
    except ImportError as exc:
        _err(f"provider_keys module not available: {exc}")
        return 2

    tenant_id = getattr(args, "tenant", None)

    try:
        store = SecretsStore(tenant_id=tenant_id)
        store.save_secret(args.key, args.value)
        print(f"✓ Set secret '{args.key}' in tenant {store.tenant_id}")
        return 0
    except ValueError as exc:
        _err(str(exc))
        return 1
    except Exception as exc:
        _err(f"failed to set secret: {exc}")
        return 2


# ── corvin secrets get ───────────────────────────────────────────────────────

def cmd_get(args: argparse.Namespace) -> int:
    """Get a secret value."""
    try:
        from operator.bridges.shared.provider_keys import SecretsStore
    except ImportError as exc:
        _err(f"provider_keys module not available: {exc}")
        return 2

    tenant_id = getattr(args, "tenant", None)

    try:
        store = SecretsStore(tenant_id=tenant_id)
        value = store.load_secret(args.key)
        if value is None:
            _err(f"secret '{args.key}' not found")
            return 1
        print(value)
        return 0
    except Exception as exc:
        _err(f"failed to get secret: {exc}")
        return 2


# ── corvin secrets delete ────────────────────────────────────────────────────

def cmd_delete(args: argparse.Namespace) -> int:
    """Delete a secret."""
    try:
        from operator.bridges.shared.provider_keys import SecretsStore
    except ImportError as exc:
        _err(f"provider_keys module not available: {exc}")
        return 2

    tenant_id = getattr(args, "tenant", None)

    try:
        store = SecretsStore(tenant_id=tenant_id)
        deleted = store.delete_secret(args.key)
        if deleted:
            print(f"✓ Deleted secret '{args.key}'")
            return 0
        else:
            _err(f"secret '{args.key}' not found")
            return 1
    except ValueError as exc:
        _err(str(exc))
        return 1
    except Exception as exc:
        _err(f"failed to delete secret: {exc}")
        return 2


# ── corvin secrets list ──────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> int:
    """List all secret keys (not values)."""
    try:
        from operator.bridges.shared.provider_keys import SecretsStore
    except ImportError as exc:
        _err(f"provider_keys module not available: {exc}")
        return 2

    tenant_id = getattr(args, "tenant", None)

    try:
        store = SecretsStore(tenant_id=tenant_id)
        keys = store.list_secrets()

        if not keys:
            print(f"No secrets found in tenant {store.tenant_id}")
            return 0

        print(f"Secrets in tenant {store.tenant_id}:\n")
        for key in keys:
            print(f"  {key}")
        print()
        return 0
    except Exception as exc:
        _err(f"failed to list secrets: {exc}")
        return 2


# ── corvin secrets migrate ───────────────────────────────────────────────────

def cmd_migrate(args: argparse.Namespace) -> int:
    """Migrate secrets from legacy .env to encrypted secrets.enc."""
    try:
        from operator.bridges.shared.provider_keys import SecretsStore
    except ImportError as exc:
        _err(f"provider_keys module not available: {exc}")
        return 2

    tenant_id = getattr(args, "tenant", None)

    # Try default .env location
    env_file = (Path.home() / ".corvin" / ".env")

    try:
        store = SecretsStore(tenant_id=tenant_id)
        result = store.migrate_from_env(env_file)
        if result:
            print(
                f"✓ Migrated {len(result)} secrets from .env "
                f"to tenant {store.tenant_id}"
            )
            return 0
        else:
            _warn(f"no secrets found in {env_file}")
            return 0
    except ValueError as exc:
        _err(str(exc))
        return 1
    except Exception as exc:
        _err(f"migration failed: {exc}")
        return 2


# ── Parser wiring ────────────────────────────────────────────────────────────

def add_parser(main_sub: Any) -> None:
    """Attach secrets subcommands to the main parser.

    Creates a 'secrets' command group with set/get/delete/list/migrate subcommands.
    """
    # Main secrets command
    secrets = main_sub.add_parser(
        "secrets",
        help="Manage encrypted secrets (Phase 1b)",
    )
    sub = secrets.add_subparsers(dest="secrets_cmd", metavar="subcommand")

    # set subcommand
    set_parser = sub.add_parser(
        "set",
        help="Set a secret value",
    )
    set_parser.add_argument("key", metavar="KEY", help="Secret key name")
    set_parser.add_argument("value", metavar="VALUE", help="Secret value")
    set_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    set_parser.set_defaults(secrets_cmd="set", func=cmd_set)

    # get subcommand
    get_parser = sub.add_parser(
        "get",
        help="Get a secret value",
    )
    get_parser.add_argument("key", metavar="KEY", help="Secret key name")
    get_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    get_parser.set_defaults(secrets_cmd="get", func=cmd_get)

    # delete subcommand
    delete_parser = sub.add_parser(
        "delete",
        help="Delete a secret",
    )
    delete_parser.add_argument("key", metavar="KEY", help="Secret key name")
    delete_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    delete_parser.set_defaults(secrets_cmd="delete", func=cmd_delete)

    # list subcommand
    list_parser = sub.add_parser(
        "list",
        help="List all secret keys",
    )
    list_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    list_parser.set_defaults(secrets_cmd="list", func=cmd_list)

    # migrate subcommand
    migrate_parser = sub.add_parser(
        "migrate",
        help="Migrate secrets from .env to encrypted store",
    )
    migrate_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    migrate_parser.set_defaults(secrets_cmd="migrate", func=cmd_migrate)

    # Set default function to show help if no subcommand
    secrets.set_defaults(func=_dispatch_secrets)


def _dispatch_secrets(args: argparse.Namespace) -> int:
    """Dispatch a secrets command."""
    func = getattr(args, "func", None)
    if func is not None and func != _dispatch_secrets:
        return func(args)
    # No subcommand provided, show error
    print(
        "usage: corvin secrets {set|get|delete|list|migrate}",
        file=sys.stderr,
    )
    return 2


__all__ = [
    "add_parser",
    "cmd_set",
    "cmd_get",
    "cmd_delete",
    "cmd_list",
    "cmd_migrate",
]
