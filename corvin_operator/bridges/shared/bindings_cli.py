#!/usr/bin/env python3
"""Operator CLI for app-bound channels (ADR-2333).

    python3 bindings_cli.py list
    python3 bindings_cli.py confirm <binding_id>
    python3 bindings_cli.py resume <binding_id>
    python3 bindings_cli.py release <binding_id>

`list` prints the binding id, the owning plugin, a fingerprint of the chat (never the full chat id) and the status.
The tenant is ``CORVIN_TENANT_ID`` (default ``_default``). Every change writes its audit record first.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[3])
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

import core.channel_bindings as cb  # noqa: E402


def _fp(chat_id: str) -> str:
    return hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:8]


def main(argv: list[str]) -> int:
    tenant = os.environ.get("CORVIN_TENANT_ID") or "_default"
    if not argv or argv[0] not in ("list", "confirm", "resume", "release"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if argv[0] == "list":
        try:
            cb._read(cb.registry_path(tenant))                  # say it when the registry itself is damaged
        except cb.BindingError as exc:
            print(f"the registry is unreadable: {exc}", file=sys.stderr)
            return 3
        rows = cb.list_bindings(tenant)
        for b in rows:
            print(f"{b['binding_id']}\t{b['plugin_id']}\t{b['channel']}:{_fp(b['chat_id'])}\t{b['status']}")
        if not rows:
            print("no app-bound chats")
        return 0
    if len(argv) != 2:
        print(f"usage: {argv[0]} <binding_id>", file=sys.stderr)
        return 2
    try:
        if argv[0] in ("confirm", "resume"):
            hit = cb.confirm(tenant, argv[1]) if argv[0] == "confirm" else cb.reactivate(tenant, argv[1])
            print({"confirm": "confirmed", "resume": "resumed"}[argv[0]] if hit else f"nothing to {argv[0]} with that id")
            return 0 if hit else 1
        hits = cb.release(tenant, binding_id=argv[1], reason="released_by_operator")
        print("released" if hits else "no binding with that id")
        return 0 if hits else 1
    except cb.BindingError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
