#!/usr/bin/env python3
"""profile_cli.py — CLI in front of profile.py for the in-chat `/profile`
commands. Sub-commands:

    show
    get <key>
    set <key>=<value>
    rm <key>            (alias: unset)
    reset
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent.parent / "bridges" / "shared"
sys.path.insert(0, str(SHARED))

import i18n as _i18n    # noqa: E402
import profile as prof  # noqa: E402


def cmd_show(_args: list[str]) -> int:
    print(prof.humanize())
    return 0


def cmd_get(args: list[str]) -> int:
    if not args:
        print("Usage: profile_cli.py get <key>")
        return 2
    v = prof.get(args[0])
    if v is None:
        print(f"(no value set for {args[0]})")
    else:
        print(f"{args[0]}: {v}")
    return 0


def cmd_set(args: list[str]) -> int:
    if not args:
        print("Usage: profile_cli.py set <key>=<value>")
        return 2
    body = " ".join(args)
    if "=" not in body:
        print("Error: expected `key=value`. Example: set name=Silvio")
        return 2
    key, raw = body.split("=", 1)
    key = key.strip()
    if not key:
        print("Error: empty key.")
        return 2
    val = prof.parse_value(raw)
    if key == "display_language" and val is not None:
        # Route through the SAME BCP-47 validator `/lang set` uses
        # (i18n.normalise) instead of storing raw input verbatim.
        # Confirmed live bug (2026-07-12): a bare "zh" (not the canonical
        # "zh-Hans") stored here silently broke every downstream i18n.t()
        # lookup (welcome greeting, voice-summary language pin), which
        # fall through their own fallback chain to English -- neither the
        # configured language nor the user's actual one. See
        # docs/troubleshooting.md #34. Use `/lang set <code>` for a
        # friendlier native-name confirmation; this generic command now
        # just refuses an unrecognisable code instead of accepting it.
        normalised = _i18n.normalise(str(val))
        if not normalised:
            print(f"Error: {val!r} is not a recognised language code. "
                  f"Try `/lang set <code>` (e.g. de, en, fr).")
            return 2
        val = normalised
    prof.set_value(key, val)
    if val is None:
        print(f"Removed {key}.")
    else:
        print(f"Set {key} = {val}")
    return 0


def cmd_rm(args: list[str]) -> int:
    if not args:
        print("Usage: profile_cli.py rm <key>")
        return 2
    prof.set_value(args[0], None)
    print(f"Removed {args[0]}.")
    return 0


def cmd_reset(_args: list[str]) -> int:
    prof.reset()
    print("Profile cleared.")
    return 0


_DISPATCH = {
    "show": cmd_show,
    "get": cmd_get,
    "set": cmd_set,
    "rm": cmd_rm,
    "unset": cmd_rm,
    "reset": cmd_reset,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        # Bare invocation: behave like `show`.
        return cmd_show([])
    sub = argv[1].lower()
    rest = argv[2:]
    fn = _DISPATCH.get(sub)
    if fn is None:
        print(f"Unknown sub-command: {sub}")
        print("Try: show | get <key> | set <key>=<value> | rm <key> | reset")
        return 2
    return fn(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
