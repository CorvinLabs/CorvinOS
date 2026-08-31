"""debug_logging.py — central debug logger for Corvin Python components.

Single entry point so every module in the bridge / engine / forge / hooks
tree gets the same configuration, log file, and PII-redaction discipline.
Mirrors the Node-side `js/logger.js` so a single env-flag toggles both.

Environment variables (all optional, sane defaults):

    CORVIN_DEBUG          1/true/on  → DEBUG level, "0"/"off" → INFO
                           default: 1 (ON) — operator can flip off explicitly.
    CORVIN_LOG_LEVEL      DEBUG|INFO|WARNING|ERROR (overrides CORVIN_DEBUG)
    CORVIN_LOG_FILE       absolute path; default <corvin_home>/logs/corvin.log
    CORVIN_LOG_MAX_BYTES  rotation size (default 10 MB)
    CORVIN_LOG_BACKUPS    rotated copies kept (default 5)
    CORVIN_LOG_STDERR     1 (default) → also log to stderr; 0 → file only
    CORVIN_LOG_REDACT     1 (default) → redact secret-looking strings
    CORVIN_LOG_BODY_CAP   chars of user/content bodies kept (default 200)

PII / compliance contract (load-bearing — see CLAUDE.md §Compliance):

    - Never log full message bodies, transcripts, or secret values.
    - `redact(text)` masks bearer tokens, API keys, JWTs, common envelope
      keys (api_key, password, token, secret, authorization).
    - `body_excerpt(text)` truncates to CORVIN_LOG_BODY_CAP chars with a
      "+N more chars" marker. Use this when you do want a flavour of the
      content (e.g. first line of a prompt) without leaking it whole.

Idempotent: importing the module installs a single set of handlers on the
"corvin" root logger. Re-imports / re-configure-on-env-change calls do not
duplicate handlers. Safe to call from inside tests.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Iterable

# Sentinel that marks our handlers so we can find them on reconfigure.
_HANDLER_MARK = "_corvin_debug_logging_handler"
_ROOT_LOGGER_NAME = "corvin"
_setup_lock = threading.Lock()
_setup_done = False


def _env_truthy(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _resolve_env(name: str) -> str | None:
    """Return the value of the given environment variable, or None."""
    return os.environ.get(name)


def _resolve_log_dir() -> Path:
    """Pick the log directory. Best-effort — never raises."""
    explicit = _resolve_env("CORVIN_LOG_FILE")
    if explicit:
        return Path(explicit).expanduser().parent
    # Walk up to find <corvin_home>; fall back to ~/.corvin/logs/.
    try:
        from paths import corvin_home  # type: ignore
        home = Path(corvin_home())
    except Exception:
        try:
            from .paths import corvin_home  # type: ignore
            home = Path(corvin_home())
        except Exception:
            home = Path.home() / ".corvin"
    return home / "logs"


def _resolve_log_file() -> Path:
    explicit = _resolve_env("CORVIN_LOG_FILE")
    if explicit:
        return Path(explicit).expanduser()
    return _resolve_log_dir() / "corvin.log"


def _resolve_level() -> int:
    # Explicit level wins.
    explicit = _resolve_env("CORVIN_LOG_LEVEL")
    if explicit:
        name = explicit.strip().upper()
        lvl = logging.getLevelName(name)
        if isinstance(lvl, int):
            return lvl
    # Otherwise CORVIN_DEBUG (default OFF) flips DEBUG vs INFO.
    debug = _env_truthy(_resolve_env("CORVIN_DEBUG"), False)
    return logging.DEBUG if debug else logging.INFO


# ── Redaction ───────────────────────────────────────────────────────────

# Order matters: longer / more specific patterns first.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # eyJ... JWTs (header.body.sig). At least 16 chars on each side.
    (re.compile(r"eyJ[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{8,}"),
     "[REDACTED_JWT]"),
    # sk-..., sk-ant-..., ghp_..., ghs_..., xoxb-..., AKIA..., AIza...
    (re.compile(r"\b(sk-(?:ant-)?[A-Za-z0-9_\-]{20,})\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"), "[REDACTED_KEY]"),
    # Bearer / Authorization headers.
    # Matches `Authorization: <scheme> <token>` and `Authorization: <token>`
    # forms. The optional scheme word (Bearer/Token/Basic) is folded into
    # the redaction so we don't leak the token that follows it.
    (re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bearer|token|basic)?\s*[^\s\"',}]+"),
     "Authorization: [REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{8,}"), "Bearer [REDACTED]"),
    # key=value envelopes used in our JSON / dict reprs.
    (re.compile(r"(?i)(['\"]?(?:api[_-]?key|password|token|secret|auth)['\"]?\s*[:=]\s*['\"]?)([^'\",}\s]+)"),
     r"\1[REDACTED]"),
)


def redact(text: Any) -> str:
    """Mask secret-looking substrings. Always returns a str."""
    if text is None:
        return ""
    s = text if isinstance(text, str) else repr(text)
    if not _env_truthy(_resolve_env("CORVIN_LOG_REDACT"), True):
        return s
    for pat, repl in _SECRET_PATTERNS:
        s = pat.sub(repl, s)
    return s


def body_excerpt(text: Any, cap: int | None = None) -> str:
    """Truncate `text` to CORVIN_LOG_BODY_CAP chars (default 200) + redact.

    Used for "flavour" of user / model content. NEVER use for transcripts
    of voice messages (compliance: voice.transcribed = metadata only).
    """
    if text is None:
        return ""
    s = text if isinstance(text, str) else repr(text)
    if cap is None:
        try:
            cap = int(_resolve_env("CORVIN_LOG_BODY_CAP") or "200")
        except ValueError:
            cap = 200
    s = redact(s)
    if len(s) <= cap:
        return s
    return f"{s[:cap]}…(+{len(s) - cap} more chars)"


# ── Setup ───────────────────────────────────────────────────────────────


class _RedactingFormatter(logging.Formatter):
    """Final-line guard: scrub patterns even if a caller forgot redact()."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        msg = super().format(record)
        if _env_truthy(_resolve_env("CORVIN_LOG_REDACT"), True):
            for pat, repl in _SECRET_PATTERNS:
                msg = pat.sub(repl, msg)
        return msg


def _build_formatter() -> logging.Formatter:
    fmt = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"
    return _RedactingFormatter(fmt=fmt, datefmt=datefmt)


def _clear_marked_handlers(logger: logging.Logger) -> None:
    for h in list(logger.handlers):
        if getattr(h, _HANDLER_MARK, False):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass


def setup_root(force: bool = False) -> logging.Logger:
    """Install handlers on the `corvin` root logger. Idempotent.

    Safe to call repeatedly. Set `force=True` to rebuild handlers if env
    vars changed (used by tests).
    """
    global _setup_done
    with _setup_lock:
        root = logging.getLogger(_ROOT_LOGGER_NAME)
        if _setup_done and not force:
            return root

        _clear_marked_handlers(root)
        root.setLevel(_resolve_level())
        # Don't propagate to the global root — keeps tests + other apps clean.
        root.propagate = False
        formatter = _build_formatter()

        # stderr handler (default on)
        if _env_truthy(_resolve_env("CORVIN_LOG_STDERR"), True):
            sh = logging.StreamHandler(stream=sys.stderr)
            sh.setFormatter(formatter)
            sh.setLevel(root.level)
            setattr(sh, _HANDLER_MARK, True)
            root.addHandler(sh)

        # File handler (always on if we can write)
        try:
            log_file = _resolve_log_file()
            log_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                max_bytes = int(
                    _resolve_env("CORVIN_LOG_MAX_BYTES")
                    or str(10 * 1024 * 1024)
                )
            except ValueError:
                max_bytes = 10 * 1024 * 1024
            try:
                backups = int(
                    _resolve_env("CORVIN_LOG_BACKUPS") or "5"
                )
            except ValueError:
                backups = 5
            fh = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backups,
                encoding="utf-8",
                delay=True,
            )
            fh.setFormatter(formatter)
            fh.setLevel(root.level)
            setattr(fh, _HANDLER_MARK, True)
            root.addHandler(fh)
        except Exception as exc:  # pragma: no cover — never block on logging
            sys.stderr.write(
                f"[debug_logging] file handler init failed: {exc!r}\n"
            )

        _setup_done = True
        root.debug(
            "debug_logging armed level=%s file=%s redact=%s",
            logging.getLevelName(root.level),
            _resolve_log_file(),
            _env_truthy(
                _resolve_env("CORVIN_LOG_REDACT"), True
            ),
        )
        return root


def get_logger(tag: str) -> logging.Logger:
    """Return a child logger under the `corvin` root.

    The first call also performs the (idempotent) global setup. Callers
    that import this module top-level get a fully-configured logger
    without any extra ceremony.
    """
    setup_root()
    name = tag if tag.startswith(_ROOT_LOGGER_NAME) else f"{_ROOT_LOGGER_NAME}.{tag}"
    return logging.getLogger(name)


def is_debug_enabled() -> bool:
    """True iff DEBUG records are currently emitted by the root logger."""
    return get_logger("__probe__").isEnabledFor(logging.DEBUG)


def current_log_file() -> Path:
    """Resolved log file path. Useful for `/settings` and bridge.sh status."""
    return _resolve_log_file()


def describe() -> dict[str, Any]:
    """Snapshot of the current config — for diagnostics and tests."""
    root = setup_root()
    return {
        "level": logging.getLevelName(root.level),
        "file": str(_resolve_log_file()),
        "stderr": _env_truthy(
            _resolve_env("CORVIN_LOG_STDERR"), True
        ),
        "redact": _env_truthy(
            _resolve_env("CORVIN_LOG_REDACT"), True
        ),
        "handlers": [type(h).__name__ for h in root.handlers],
    }


# Pre-warm on import. Cheap, idempotent, and means every downstream
# `get_logger(...)` call is just a name-lookup.
setup_root()


__all__ = [
    "get_logger",
    "setup_root",
    "redact",
    "body_excerpt",
    "is_debug_enabled",
    "current_log_file",
    "describe",
]
