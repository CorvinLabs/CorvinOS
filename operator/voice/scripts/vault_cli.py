#!/usr/bin/env python3
"""vault_cli.py — CLI in front of vault.py.

Sub-commands:
    list                                  list items (no values)
    get <name>                            print the value (audited)
    set <name>=<value> [--kind K] [--tags t1,t2] [--locked] [--encrypted]
    unlock <name> [--ttl SECONDS]
    forget <name>
    audit [N]                             last N audit-log lines (default 20)

Notes:
  - `set` always reads the value from a single positional `name=value`
    argument. For multi-line / structured values pass JSON: `set foo='{"a":1}'`.
  - `get` prints the value directly to stdout. The dispatcher trims it.
  - `--locked` flips `auto_unlock` to false.
  - `--encrypted` requires a working `gpg` install.
  - The `source` argument (chat/sender) is read from the env var
    `VAULT_AUDIT_SOURCE` so the dispatcher can pass the chat id without
    cluttering the user-visible CLI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent.parent / "bridges" / "shared"
sys.path.insert(0, str(SHARED))

import vault as v  # noqa: E402


def _audit_source() -> str:
    return os.environ.get("VAULT_AUDIT_SOURCE", "cli")


def cmd_list(_args) -> int:
    items = v.list_items()
    if not items:
        print("Vault is empty.")
        print("Add an item with:  /vault set <name>=<value>")
        return 0
    print(f"{len(items)} item(s):")
    for it in items:
        flags = []
        if it["encrypted"]:
            flags.append("encrypted")
        if not it["auto_unlock"]:
            flags.append("locked")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        tag_str = f"  tags: {', '.join(it['tags'])}" if it["tags"] else ""
        print(f"  {it['name']} ({it['kind']}){flag_str}{tag_str}")
    return 0


def cmd_get(args) -> int:
    if not args.name:
        print("Usage: vault_cli.py get <name>")
        return 2
    try:
        value = v.get_item(args.name, source=_audit_source())
    except KeyError as e:
        print(f"Error: {e}")
        return 1
    except PermissionError as e:
        print(f"Locked: {e}")
        return 3
    except RuntimeError as e:
        print(f"Error: {e}")
        return 1
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)
    return 0


def cmd_set(args) -> int:
    if not args.spec or "=" not in args.spec:
        print("Usage: vault_cli.py set <name>=<value> [--kind K] [--tags a,b] [--locked] [--encrypted]")
        return 2
    name, raw = args.spec.split("=", 1)
    name = name.strip()
    raw = raw.strip()
    # Try JSON first; fall back to raw string.
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    try:
        v.set_item(
            name, value, kind=args.kind, tags=tags,
            encrypted=args.encrypted, auto_unlock=not args.locked,
        )
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}")
        return 1
    flags = []
    if args.encrypted:
        flags.append("encrypted")
    if args.locked:
        flags.append("locked")
    suffix = f" [{', '.join(flags)}]" if flags else ""
    print(f"Saved {name}{suffix}.")
    return 0


def cmd_unlock(args) -> int:
    if not args.name:
        print("Usage: vault_cli.py unlock <name> [--ttl SECONDS]")
        return 2
    try:
        exp = v.unlock(args.name, ttl=args.ttl)
    except KeyError as e:
        print(f"Error: {e}")
        return 1
    import time
    rem = max(0, int(exp - time.time()))
    print(f"Unlocked {args.name} for {rem // 60} min {rem % 60} s.")
    return 0


def cmd_forget(args) -> int:
    if not args.name:
        print("Usage: vault_cli.py forget <name>")
        return 2
    if v.forget_item(args.name):
        print(f"Removed {args.name}.")
        return 0
    print(f"No vault item named {args.name}.")
    return 1


def cmd_audit(args) -> int:
    n = args.n or 20
    log = v.read_audit(n)
    if not log:
        print("No audit log yet.")
        return 0
    for line in log:
        print(line)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vault_cli.py")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list")

    p_get = sub.add_parser("get")
    p_get.add_argument("name", nargs="?")

    p_set = sub.add_parser("set")
    p_set.add_argument("spec", nargs="?", help='"<name>=<value>" — value can be JSON')
    p_set.add_argument("--kind", default="secret")
    p_set.add_argument("--tags", default="")
    p_set.add_argument("--locked", action="store_true")
    p_set.add_argument("--encrypted", action="store_true")

    p_unlock = sub.add_parser("unlock")
    p_unlock.add_argument("name", nargs="?")
    p_unlock.add_argument("--ttl", type=int, default=v.UNLOCK_TTL)

    p_forget = sub.add_parser("forget")
    p_forget.add_argument("name", nargs="?")
    sub.add_parser("rm").add_argument("name", nargs="?")

    p_audit = sub.add_parser("audit")
    p_audit.add_argument("n", nargs="?", type=int, default=20)

    args = parser.parse_args(argv[1:])
    cmd = args.cmd or "list"
    if cmd == "list":     return cmd_list(args)
    if cmd == "get":      return cmd_get(args)
    if cmd == "set":      return cmd_set(args)
    if cmd == "unlock":   return cmd_unlock(args)
    if cmd in ("forget", "rm"):
        # argparse builds two different namespaces depending on the
        # subparser; both expose `.name`.
        return cmd_forget(args)
    if cmd == "audit":    return cmd_audit(args)
    print(f"Unknown sub-command: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
