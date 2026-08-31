"""Regression: the loopback/IMDS deny shim blocks the unspecified + encoded
addresses the R2-13 fix missed.

connect() to 0.0.0.0 / :: / decimal 0 is routed to 127.0.0.1 by the kernel, so
it reaches host loopback services (Postgres/Redis/Vault) and must be blocked
alongside is_loopback. Encoded IPv4 (decimal/hex/short-dotted) must also be
caught — the kernel canonicalizes them to loopback. (Review 2026-06-17.)
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SHIM = Path(__file__).resolve().parents[1] / "forge" / "sandbox_helpers" / "sitecustomize.py"
_spec = importlib.util.spec_from_file_location("_corvin_loopback_shim_test", _SHIM)
_mod = importlib.util.module_from_spec(_spec)
# exec_module monkeypatches socket.connect IN THIS PROCESS — harmless: this test
# only exercises the address predicate, it never opens a connection.
_spec.loader.exec_module(_mod)

PASS = 0
FAIL = 0


def t(label: str, ok: bool) -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# Must be BLOCKED — loopback, link-local/IMDS, unspecified, and encoded loopback.
_BLOCKED = [
    "127.0.0.1", "::1", "169.254.169.254",
    "0.0.0.0", "::", "0",            # unspecified (R2-13 gap — review finding)
    "2130706433", "0x7f000001", "127.1",  # decimal/hex/short-dotted loopback
    "::ffff:127.0.0.1",              # v4-mapped loopback
]
# Must be ALLOWED — genuine public addresses.
_ALLOWED = ["8.8.8.8", "1.1.1.1", "93.184.216.34"]


def main() -> int:
    for h in _BLOCKED:
        t(f"blocked: {h}", _mod._is_blocked(h) is True)
    for h in _ALLOWED:
        t(f"allowed: {h}", _mod._is_blocked(h) is False)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
