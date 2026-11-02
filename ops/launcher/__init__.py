"""Corvin launcher package — console-script entry points shipped in the wheel."""
import sys as _sys

# Windows without Python UTF-8 mode (a user running `corvinos-serve > log.txt`,
# a task wrapper that does not set PYTHONUTF8): a redirected stdout/stderr is
# encoded in the ANSI code page (cp1252), and the first emoji / non-Latin-1
# character any entry point prints raises UnicodeEncodeError and kills it.
# Write UTF-8 instead and never raise on output. A real console is unaffected
# (it is UTF-16 console I/O already); Linux/macOS are UTF-8 already: no-op.
if _sys.platform == "win32":
    for _stream in (_sys.stdout, _sys.stderr):
        try:
            if _stream is not None and (_stream.encoding or "").replace("-", "").lower() != "utf8":
                _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:  # noqa: BLE001 — a non-TextIOWrapper stream: leave it
            pass
