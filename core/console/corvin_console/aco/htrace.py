"""HealingTrace — ADR-0180 cross-instance healing trace aggregation.

Writes scrubbed, PII-free healing-event records to a local JSONL file
(~/.corvin/healing-traces/YYYY-MM-DD.jsonl). The allow-list schema in
htrace_allowlists.py defines exactly which fields may appear. _assert_safe_htrace()
is the last line of defence before any record is written.

Security model (open-source safe): every guarantee holds when the full source
is known to an adversary. Pseudonymisation uses HMAC (key server-side only,
not in this file). PII rejection uses an allow-list (unknown fields → drop)
plus active regex scanning.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import platform
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .htrace_allowlists import (
    NS_ALLOWLIST,
    EVENT_SEQ_ALLOWLIST,
    CONFIG_KEY_ALLOWLIST,
    HTRACE_FIELD_ALLOWLIST,
    STACK_FRAME_FIELD_ALLOWLIST,
    HEAL_OUTCOME_VALUES,
    TENANT_SHAPE_VALUES,
)

logger = logging.getLogger(__name__)

_SCHEMA = "htrace/1"
_MAX_DAY_BYTES = 10 * 1024 * 1024   # 10 MB / day
_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB total dir
_MAX_RETAIN_DAYS = 14
_MAX_STACK_DEPTH = 8
_MAX_EVENT_SEQ = 10

# ── PII scanner (same pattern class as ADR-0179) ──────────────────────────────
# Full scanner — used for free-text fields (templates, action names, etc.)
_PII = re.compile(
    r"@|"                                                   # email-like
    r"~/|\\\\|/home/|/Users/|/root/|(?:[A-Za-z]:\\)|"      # path fragments
    r"\beyJ[A-Za-z0-9_-]{6,}\.|"                           # JWT header
    r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[baprs]|AKIA|ASIA)[_-]|"  # API token prefixes
    r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b|"          # MAC address
    r"\b[0-9a-fA-F]{20,}\b|"                               # long hex (≥20) = keys/tokens
    r"\b\d{12,}\b|"                                        # long numeric ID
    r"(?:\d{1,3}\.){3}\d{1,3}",                            # IPv4
)

# Reduced scanner for fields that are expected to contain hex hashes.
# sha256 hashes (exactly 64 hex chars) and short tokens are legitimate here;
# only email, path, JWT, named-token prefixes, MAC, and IP are still flagged.
_PII_NO_LONGHEX = re.compile(
    r"@|"
    r"~/|\\\\|/home/|/Users/|/root/|(?:[A-Za-z]:\\)|"
    r"\beyJ[A-Za-z0-9_-]{6,}\.|"
    r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[baprs]|AKIA|ASIA)[_-]|"
    r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b|"
    r"\b\d{12,}\b|"
    r"(?:\d{1,3}\.){3}\d{1,3}",
)

# Fields that contain legitimate long-hex values (sha256 hashes, HMAC tokens).
# Use the reduced scanner (_PII_NO_LONGHEX) for these — the long-hex check
# would otherwise reject valid fingerprints and HMAC pseudonyms.
_HASH_FIELDS = frozenset({
    "error_fingerprint",
    "config_profile_hash",
    "instance_token",  # HMAC-SHA256(server_secret, instance_id) — 64 hex chars
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _corvin_version() -> str:
    try:
        from importlib.metadata import version
        return version("corvinOS")
    except Exception:  # noqa: BLE001
        return "unknown"


def _platform_string() -> str:
    """OS family + arch only, nothing user-specific."""
    sys_map = {"linux": "linux", "darwin": "darwin", "win32": "windows"}
    sys_name = sys_map.get(sys.platform, "unknown")
    arch_map = {
        "x86_64": "x86_64", "amd64": "x86_64",
        "aarch64": "arm64", "arm64": "arm64",
        "armv7l": "armv7l", "i386": "x86", "i686": "x86",
    }
    arch = arch_map.get(platform.machine().lower(), "unknown")
    return f"{sys_name}/{arch}"


def _python_minor() -> str:
    """Major.minor only — e.g. '3.12'."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _safe_str(s: str, maxlen: int = 120) -> str:
    return re.sub(r"[^\x20-\x7e]", "", str(s))[:maxlen]


# ── Fingerprint ───────────────────────────────────────────────────────────────

def _norm_ns(module_name: str) -> str:
    """Normalise a Python module path to its terminal component."""
    part = str(module_name).rsplit(".", 1)[-1]
    return re.sub(r"[^A-Za-z0-9_]", "_", part)[:60] or "unknown"


def _norm_fn(fn: str) -> str:
    """Strip decorator wrappers injected by functools.wraps."""
    fn = re.sub(r"^_{0,2}wrapped?_?", "", str(fn))
    fn = re.sub(r"_wrapper$", "", fn)
    return re.sub(r"[^A-Za-z0-9_]", "_", fn)[:60] or "unknown"


def make_fingerprint(exc_type: str, module_ns: str, function: str) -> str:
    """Stable, version-normalised, platform-neutral error fingerprint."""
    canonical = (
        f"{exc_type.lower().rsplit('.', 1)[-1]}|"
        f"{_norm_ns(module_ns)}|"
        f"{_norm_fn(function)}"
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Config profile hash ───────────────────────────────────────────────────────

def config_profile_hash(cfg: dict) -> str:
    """sha256 of sorted, allowlisted config KEY NAMES only (values never included)."""
    keys = sorted(k for k in cfg if k in CONFIG_KEY_ALLOWLIST)
    return hashlib.sha256("|".join(keys).encode()).hexdigest()


# ── Stack-frame normalisation ─────────────────────────────────────────────────

def _norm_frame(frame: dict) -> dict:
    raw_ns = str(frame.get("module", frame.get("ns", "")))
    fn = str(frame.get("fn", frame.get("function", frame.get("co_name", ""))))
    ln = int(frame.get("ln", frame.get("line", frame.get("lineno", 0))))
    ns = _norm_ns(raw_ns)
    if ns not in NS_ALLOWLIST:
        return {"ns": "[external]", "fn": "[redacted]", "ln": 0}
    return {"ns": ns, "fn": _norm_fn(fn), "ln": ln}


def _norm_event_sequence(events: list) -> list:
    result = []
    for ev in events:
        s = str(ev)
        result.append(s if s in EVENT_SEQ_ALLOWLIST else "[event.redacted]")
    return result


def _safe_template(raw_msg: str) -> str:
    """Convert an exception message to a PII-free structural template."""
    s = str(raw_msg)[:500]
    s = re.sub(r"(?:[A-Za-z]:[\\/]|/)[^\s'\",;]{3,}", "[path]", s)
    s = re.sub(r"'[^']{0,120}'", "'{}'", s)
    s = re.sub(r'"[^"]{0,120}"', '"{}"', s)
    s = re.sub(r"\b[0-9a-fA-F]{8,}\b", "{}", s)
    s = re.sub(r"\b\d{4,}\b", "{}", s)
    if _PII.search(s):
        return "[message.redacted]"
    return s[:200]


# ── _assert_safe_htrace — allow-list based ────────────────────────────────────

def _assert_safe_htrace(record: dict) -> None:
    """Fail-closed field + PII validator.

    Layer 1: allow-list — any field not in HTRACE_FIELD_ALLOWLIST → ValueError.
    Layer 2: active PII regex scan over all string values.

    Raises ValueError if any check fails. Never returns partial results.
    """
    unknown = set(record.keys()) - HTRACE_FIELD_ALLOWLIST
    if unknown:
        raise ValueError(f"HealingTrace: unknown fields {unknown!r}")

    def _scan_value(v, *, reduced: bool = False):
        if isinstance(v, str):
            pattern = _PII_NO_LONGHEX if reduced else _PII
            m = pattern.search(v)
            if m:
                raise ValueError(f"HealingTrace: PII near {m.group(0)[:20]!r}")
        elif isinstance(v, dict):
            for item in v.values():
                _scan_value(item, reduced=reduced)
        elif isinstance(v, (list, tuple)):
            for item in v:
                _scan_value(item, reduced=reduced)

    # Scan ALL string-typed top-level fields, not just the 9 that were
    # originally listed.  omitting corvin_version / platform / python /
    # schema / ts_day / heal_outcome / tenant_shape left those fields open
    # to PII injection via direct HealingTrace() construction + validated().
    for fld in (
        "schema", "corvin_version", "platform", "python", "ts_day",
        "error_type", "error_module_ns", "error_function", "error_template",
        "heal_action", "heal_outcome", "tenant_shape",
        "consent_act_id", "instance_token",
        "error_fingerprint", "config_profile_hash",
    ):
        if fld in record:
            _scan_value(record[fld], reduced=(fld in _HASH_FIELDS))

    # error_line must be an int — a string "192.168.1.1" would bypass _scan_value
    if "error_line" in record and not isinstance(record["error_line"], int):
        raise ValueError(
            f"HealingTrace: error_line must be int, got {type(record['error_line']).__name__}"
        )

    for frame in record.get("stack_frames", []):
        # Enforce STACK_FRAME_FIELD_ALLOWLIST — extra keys bypass PII scanner
        extra_keys = set(frame.keys()) - STACK_FRAME_FIELD_ALLOWLIST
        if extra_keys:
            raise ValueError(f"HealingTrace: unknown stack frame keys {extra_keys!r}")
        _scan_value(frame.get("fn", ""))
        ns = frame.get("ns", "")
        if ns not in NS_ALLOWLIST and ns != "[external]":
            raise ValueError(f"HealingTrace: unsafe stack frame ns {ns!r}")
        # ln must be an int — a string IP/email would bypass _scan_value
        ln = frame.get("ln", 0)
        if not isinstance(ln, int):
            raise ValueError(
                f"HealingTrace: stack frame ln must be int, got {type(ln).__name__}"
            )

    for ev in record.get("event_sequence", []):
        if ev not in EVENT_SEQ_ALLOWLIST and ev != "[event.redacted]":
            raise ValueError(f"HealingTrace: unsafe event name {ev!r}")


# ── HealingTrace dataclass ────────────────────────────────────────────────────

@dataclass
class HealingTrace:
    """One scrubbed, PII-free healing event record (ADR-0180 §1)."""
    schema: str = _SCHEMA
    corvin_version: str = field(default_factory=_corvin_version)
    platform: str = field(default_factory=_platform_string)
    python: str = field(default_factory=_python_minor)
    error_fingerprint: str = ""
    error_type: str = ""
    error_module_ns: str = ""
    error_function: str = ""
    error_line: int = 0
    error_template: str = ""
    stack_frames: list = field(default_factory=list)
    event_sequence: list = field(default_factory=list)
    heal_action: str = ""
    heal_outcome: str = "skipped"
    config_profile_hash: str = ""
    tenant_shape: str = "single"
    ts_day: str = field(default_factory=_today_utc)
    consent_act_id: str = ""
    instance_token: str = ""

    @classmethod
    def from_heal_event(
        cls,
        exc: BaseException,
        *,
        event_sequence: list[str],
        heal_action: str,
        heal_outcome: str,
        corvin_cfg: dict | None = None,
        consent_act_id: str = "",
        instance_token: str = "",
        tenant_shape: str = "single",
    ) -> "HealingTrace":
        """Build a HealingTrace from a live exception + heal context."""
        tb_frames: list[dict] = []
        tb = exc.__traceback__
        while tb is not None:
            co = tb.tb_frame.f_code
            tb_frames.append({
                # Use __name__ (dotted module path) so _norm_ns extracts the
                # terminal component correctly (e.g. 'chat_runtime').
                # co_filename is an OS path ending in '.py' — _norm_ns would
                # strip everything before the last '.' and return 'py', which
                # is absent from NS_ALLOWLIST, making every frame external.
                "module": tb.tb_frame.f_globals.get("__name__", co.co_filename),
                "fn": co.co_name,
                "ln": tb.tb_lineno,
            })
            tb = tb.tb_next

        norm_frames = [_norm_frame(f) for f in tb_frames]

        # All three attribution fields (ns, fn, ln) must come from the same
        # frame.  Previously top_fn and top_ln were taken from norm_frames[0]
        # (outermost) while top_ns was the first non-external frame, making
        # the record semantically incoherent when external library frames sit
        # at the top of the traceback.
        top_frame = next(
            (f for f in norm_frames if f["ns"] != "[external]"),
            norm_frames[0] if norm_frames else None,
        )
        top_ns = top_frame["ns"] if top_frame else "unknown"
        top_fn = top_frame["fn"] if top_frame else ""
        top_ln = top_frame["ln"] if top_frame else 0

        exc_type = type(exc).__name__
        fp = make_fingerprint(exc_type, top_ns, top_fn)
        template = _safe_template(str(exc))

        return cls(
            error_fingerprint=fp,
            error_type=exc_type,
            error_module_ns=top_ns,
            error_function=top_fn,
            error_line=top_ln,
            error_template=template,
            stack_frames=norm_frames[:_MAX_STACK_DEPTH],
            event_sequence=_norm_event_sequence(event_sequence)[-_MAX_EVENT_SEQ:],
            heal_action=_safe_str(heal_action, 80),
            heal_outcome=heal_outcome if heal_outcome in HEAL_OUTCOME_VALUES else "skipped",
            config_profile_hash=config_profile_hash(corvin_cfg or {}),
            tenant_shape=tenant_shape if tenant_shape in TENANT_SHAPE_VALUES else "single",
            consent_act_id=_safe_str(consent_act_id, 64),
            instance_token=_safe_str(instance_token, 64),
        )

    def validated(self) -> dict:
        """Serialise and validate. Raises ValueError on any violation."""
        d = asdict(self)
        _assert_safe_htrace(d)
        return d


# ── Local JSONL write ─────────────────────────────────────────────────────────

def htrace_dir(home: Path) -> Path:
    return home / "healing-traces"


def _today_file(home: Path) -> Path:
    return htrace_dir(home) / f"{_today_utc()}.jsonl"


def write_trace(
    trace: HealingTrace,
    home: Path,
    *,
    consent_active: bool = False,
) -> bool:
    """Append one validated HealingTrace to today's JSONL.

    Returns True if written, False if skipped. Never raises.
    """
    if not consent_active:
        return False
    if trace.tenant_shape == "multi":
        return False  # ADR-0180 §5.4: multi-tenant excluded

    try:
        record = trace.validated()
    except ValueError as e:
        logger.debug("htrace: record dropped by _assert_safe_htrace: %s", e)
        _inc_dropped(home)
        return False

    try:
        d = htrace_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        _enforce_caps(home)

        today = _today_file(home)
        if today.exists() and today.stat().st_size >= _MAX_DAY_BYTES:
            logger.debug("htrace: daily cap reached, record dropped")
            _inc_dropped(home)
            return False

        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with today.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True

    except Exception as e:  # noqa: BLE001
        logger.debug("htrace: write error (non-fatal): %s", e)
        return False


def _inc_dropped(home: Path) -> None:
    """Increment dropped-record counter (count only, no content)."""
    try:
        d = htrace_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        p = d / "dropped.count"
        count = int(p.read_text(encoding="utf-8").strip()) if p.exists() else 0
        p.write_text(str(count + 1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _enforce_caps(home: Path) -> None:
    """Expire files older than MAX_RETAIN_DAYS or if total dir exceeds MAX_TOTAL_BYTES.

    Scans both the top-level healing-traces/ directory and the sent/ sub-
    directory so uploaded bundles are also subject to the 14-day / 50 MB cap.
    Without this, sent/ accumulates indefinitely because the normal write/
    upload paths never touch it after the initial move.
    """
    try:
        d = htrace_dir(home)
        today_str = _today_utc()
        cutoff = time.time() - (_MAX_RETAIN_DAYS * 86400)
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        gz_files = sorted(d.glob("*.jsonl.gz"), key=lambda p: p.stat().st_mtime)
        # Include sent/ subdirectory so uploaded bundles are also capped
        sent_dir = d / "sent"
        sent_gz_files = (
            sorted(sent_dir.glob("*.jsonl.gz"), key=lambda p: p.stat().st_mtime)
            if sent_dir.is_dir()
            else []
        )
        all_files = files + gz_files + sent_gz_files
        total = sum(p.stat().st_size for p in all_files if p.exists())
        for p in all_files:
            if p.name.startswith(today_str):
                continue
            if p.stat().st_mtime < cutoff or total > _MAX_TOTAL_BYTES:
                logger.info("htrace: expiring %s (retention/cap)", p.name)
                size = p.stat().st_size
                p.unlink(missing_ok=True)
                total = max(0, total - size)
    except Exception:  # noqa: BLE001
        pass


def compress_for_upload(home: Path, date_str: str | None = None) -> Optional[Path]:
    """Compress a previous day's .jsonl → .jsonl.gz for upload.

    Returns the compressed path or None if nothing to compress.

    Security: date_str is validated to YYYY-MM-DD to prevent path traversal.
    Concurrency: the source file is renamed to a temporary path before being
    read, making it invisible to concurrent write_trace calls which target
    the original path.  Any write that races with the rename creates a fresh
    file under the original name and is preserved for the next cycle.
    Crash-safety: the gz is fsynced before the source is deleted.
    """
    if date_str is None:
        date_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise ValueError(f"htrace: invalid date_str {date_str!r} — must match YYYY-MM-DD")
    src = htrace_dir(home) / f"{date_str}.jsonl"
    if not src.exists():
        return None
    dst = src.with_suffix(".jsonl.gz")
    # Rename atomically so any concurrent write_trace creates a fresh .jsonl
    # file under the original name instead of appending to a file we're
    # about to read and delete.
    tmp = src.with_suffix(".jsonl.compressing")
    try:
        src.rename(tmp)
    except OSError as e:
        logger.warning("htrace: compress rename failed: %s", e)
        return None
    try:
        with tmp.open("rb") as fi, gzip.open(dst, "wb", compresslevel=6) as fo:
            fo.write(fi.read())
        # fsync the gz before removing the only copy of the data
        try:
            with dst.open("rb") as fsync_fh:
                os.fsync(fsync_fh.fileno())
        except OSError:
            pass
        tmp.unlink()
        logger.info("htrace: compressed %s → %s", src.name, dst.name)
        return dst
    except Exception as e:  # noqa: BLE001
        logger.warning("htrace: compress failed: %s", e)
        try:
            tmp.rename(src)  # restore so data is not lost
        except Exception:  # noqa: BLE001
            pass
        return None
