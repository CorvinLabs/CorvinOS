"""Tests for _win_shim — cmd.exe-safe argument quoting (RCE regression).

The vulnerability: `Popen(["cmd","/c", bin, *args])` on Windows routes args
through list2cmdline, and cmd.exe re-parses the line — a user prompt like
`a" & calc & "b` breaks out of quotes and cmd runs the payload. The fix builds
the line ourselves with cmd-literal `""` quoting so cmd never leaves quoted
mode. These tests exercise the pure string builder (runnable on any OS).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _win_shim import (  # noqa: E402
    cmd_quote,
    build_cmd_c_line,
    windows_shim_command,
    no_console_window_flags,
)


def _section(t: str) -> None:
    print(f"\n=== {t} ===")


def test_plain_arg_is_quoted() -> None:
    _section("plain arg wrapped in quotes")
    assert cmd_quote("hello") == '"hello"'
    print("PASS")


def test_ampersand_stays_inside_quotes() -> None:
    _section("cmd metachars are neutralised by staying quoted")
    # The whole payload is wrapped — cmd sees one quoted string, never a & split.
    for payload in ("hello&calc.exe", "a|b", "x<y>z", "a^b", "(sub)"):
        q = cmd_quote(payload)
        assert q.startswith('"') and q.endswith('"'), q
        # No unescaped metachar sits outside the surrounding quotes.
        assert q[1:-1] == payload.replace('"', '""'), q
    print("PASS: & | < > ^ ( ) remain inside the quoted token")


def test_quote_toggle_breakout_is_closed() -> None:
    _section("the `\" & payload & \"` breakout vector")
    payload = 'a" & powershell -enc AAAA & "b'
    q = cmd_quote(payload)
    # Inner double-quotes are doubled (cmd literal-quote), NOT backslash-escaped.
    assert '\\"' not in q, f"backslash-escaped quote would toggle cmd out: {q!r}"
    assert '""' in q, q
    # cmd stays in quoted mode: the only quote-mode transitions are the first
    # and last char. Count of `"` is even, and doubling keeps parity.
    assert q.count('"') % 2 == 0, q
    print(f"PASS: breakout closed → {q!r}")


def test_trailing_backslashes_doubled() -> None:
    _section("trailing backslashes can't escape the closing quote")
    q = cmd_quote("C:\\path\\dir\\")
    assert q.endswith('\\\\"'), q  # the single trailing \ became \\ before "
    assert not q.endswith('\\"') or q.endswith('\\\\"'), q
    print(f"PASS: {q!r}")


def test_empty_arg() -> None:
    _section("empty arg is an empty quoted token")
    assert cmd_quote("") == '""'
    print("PASS")


def test_build_line_shape() -> None:
    _section("full cmd /c line")
    line = build_cmd_c_line(["C:\\n\\claude.cmd", "-p", "hi & bye"])
    assert line == 'cmd /c "C:\\n\\claude.cmd" "-p" "hi & bye"', line
    print(f"PASS: {line!r}")


def test_windows_shim_command_posix_noop() -> None:
    _section("POSIX: argv returned unchanged (list, not string)")
    # os.name is 'posix' here, so the helper must be a no-op.
    argv = ["/usr/bin/claude", "-p", "a & b"]
    out = windows_shim_command(argv)
    assert out is argv, "POSIX must return the exact list unchanged"
    print("PASS: Linux/macOS spawns are byte-for-byte identical to before")


def test_argv_roundtrips_through_CommandLineToArgvW_semantics() -> None:
    _section("target program still receives the exact argument")
    # On Windows, list2cmdline is the INVERSE parser of CommandLineToArgvW.
    # Our quoting uses "" for a literal quote, which CommandLineToArgvW also
    # decodes to a single ". Verify a value with a literal quote round-trips.
    payload = 'say "hi"'
    q = cmd_quote(payload)
    # Simulate CommandLineToArgvW on a single quoted token with "" → ":
    # strip the outer quotes, replace "" with ".
    inner = q[1:-1]
    decoded = inner.replace('""', '"')
    assert decoded == payload, f"{decoded!r} != {payload!r}"
    print(f"PASS: {q!r} decodes back to {payload!r}")


def test_no_console_window_flags_is_zero_on_posix() -> None:
    _section("no_console_window_flags: 0 on POSIX (os.name == 'posix' here)")
    assert no_console_window_flags() == 0
    print("PASS")


def test_no_console_window_flags_safe_as_popen_kwarg() -> None:
    _section("creationflags=no_console_window_flags() is accepted by Popen on POSIX")
    # Regression guard for the exact bug this fixes: every engine spawn site
    # now unconditionally passes creationflags=no_console_window_flags() —
    # if that ever stopped being a safe no-op value on POSIX, every bridge/
    # console/A2A turn on Linux/macOS would crash outright, not just misbehave.
    proc = subprocess.Popen(
        ["true"] if sys.platform != "win32" else ["cmd", "/c", "exit", "0"],
        creationflags=no_console_window_flags(),
    )
    assert proc.wait() == 0
    print("PASS")


def test_no_console_window_flags_uses_windows_constant_when_nt() -> None:
    _section("no_console_window_flags: resolves subprocess.CREATE_NO_WINDOW when os.name == 'nt'")
    import _win_shim as mod
    import unittest.mock as mock

    with mock.patch.object(mod.os, "name", "nt"), \
         mock.patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True):
        assert mod.no_console_window_flags() == 0x08000000
    print("PASS")


def main() -> int:
    tests = [
        test_plain_arg_is_quoted,
        test_ampersand_stays_inside_quotes,
        test_quote_toggle_breakout_is_closed,
        test_trailing_backslashes_doubled,
        test_empty_arg,
        test_build_line_shape,
        test_windows_shim_command_posix_noop,
        test_argv_roundtrips_through_CommandLineToArgvW_semantics,
        test_no_console_window_flags_is_zero_on_posix,
        test_no_console_window_flags_safe_as_popen_kwarg,
        test_no_console_window_flags_uses_windows_constant_when_nt,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print()
    if failed:
        print(f"{failed} test(s) failed")
        return 1
    print(f"All {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
