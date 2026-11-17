"""Operator CLI for the personal knowledge base (ADR-2326 item 9, route (a)).

    python -m core.personal_kb.cli erase --tenant _default --handle <handle>
    python -m core.personal_kb.cli erase --tenant _default --default
    python -m core.personal_kb.cli count --tenant _default

``erase`` runs the same steps as the owner's own erasure (crypto-shredding, audited start and end)
and passes no handle or uid to the orchestrator. It needs no key. ``count`` prints only the number
of personal spaces; nothing in this CLI reads or prints content, handles of others, or uids.
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="personal_kb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("erase", help="erase one personal space (Art. 17, operator)")
    e.add_argument("--tenant", default="_default")
    which = e.add_mutually_exclusive_group(required=True)
    which.add_argument("--handle")
    which.add_argument("--default", action="store_true", help="the tenant's default space (it has no usable handle)")
    c = sub.add_parser("count", help="number of personal spaces in a tenant")
    c.add_argument("--tenant", default="_default")
    a = ap.parse_args(argv)
    from . import erasure, owner

    if a.cmd == "count":
        print(len(owner.list_owner_ids(a.tenant)))
        return 0
    oid = owner.find_default(a.tenant) if a.default else owner.find_by_handle(a.tenant, a.handle.strip().lower())
    if oid is None:
        print("no such personal space", file=sys.stderr)
        return 2
    n = erasure.erase_owner(a.tenant, oid)
    print(f"erased ({n} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
