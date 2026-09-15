#!/usr/bin/env python3
"""lang_cli.py — `/lang` slash-command backend.

Sub-commands:

    set <code>      validate <code> as BCP-47, store as
                    profile.display_language; emit a success line.
    show            print current display_language + native name.
    clear           remove the display_language entry.
    list            print every BCP-47 code the native-name registry
                    knows about (one per line).

The actual storage path is `profile.set_value("display_language", code)`,
i.e. the same Tier-1 profile that `profile_cli.py` writes to. This script
exists so the slash-command UI gets a small, self-contained validator
that doesn't conflate i18n with profile-management.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SHARED = Path(__file__).resolve().parent.parent.parent / "bridges" / "shared"
sys.path.insert(0, str(SHARED))

import i18n        # type: ignore
import profile     # type: ignore


def _emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0 if payload.get("ok") else 1


def cmd_set(raw: str) -> int:
    code = i18n.normalise(raw)
    if not code:
        return _emit({"ok": False, "reason": "unknown", "raw": raw})
    name = i18n.native_name(code)
    profile.set_value("display_language", code)
    return _emit({"ok": True, "code": code, "name": name})


def cmd_show() -> int:
    raw = profile.load().get("display_language") or ""
    code = i18n.normalise(raw)
    if not code:
        # Default falls back to English. The caller decides whether to
        # auto-detect from bridge metadata when the profile is empty.
        return _emit({
            "ok": True, "set": False,
            "code": "en", "name": i18n.native_name("en"),
        })
    return _emit({
        "ok": True, "set": True,
        "code": code, "name": i18n.native_name(code),
    })


def cmd_clear() -> int:
    profile.set_value("display_language", None)
    return _emit({"ok": True})


def cmd_list() -> int:
    items = [{"code": c, "name": i18n.native_name(c)}
             for c in i18n.known_codes()]
    return _emit({"ok": True, "codes": items})


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sset = sub.add_parser("set"); sset.add_argument("code")
    sub.add_parser("show")
    sub.add_parser("clear")
    sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "set":   return cmd_set(args.code)
    if args.cmd == "show":  return cmd_show()
    if args.cmd == "clear": return cmd_clear()
    if args.cmd == "list":  return cmd_list()
    return 2


if __name__ == "__main__":
    sys.exit(main())
