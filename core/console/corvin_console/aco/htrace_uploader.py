"""HealingTrace batch uploader — NerveFiber (ADR-0180 §2 + §3).

Fires once per day (opportunistic: at boot if a day was missed, plus scheduled
nightly window). Compresses yesterday's .jsonl, validates every record through
_assert_safe_htrace, then POSTs the bundle to the configured endpoint.

Primary target: POST /v1/telemetry/healing-traces (Corvin-Features proxy, M4).
Transparency mirror: github.com/CorvinLabs/CorvinLogs (via CORVINLOGS_GITHUB_TOKEN).

Gating is DEFAULT-ON / opt-OUT (maintainer decision, ADR-0180): healing traces
upload unless disabled via ``spec.telemetry.healing_traces: false``
(``healing_traces_enabled``). A recorded ConsentAct is NO LONGER required — it is
embedded only as an audit-correlation id when a user explicitly consented. The
load-bearing safety invariant is not consent but that every uploaded record is
CONTENT-FREE (``_assert_safe_htrace`` fail-closed drops any PII/secret shape).

Upload is silently skipped when: the opt-out flag is set, there is no bundle for
the day, or a lock is already held. On network failure: leave file, retry on next
trigger (14-day cap).
"""
from __future__ import annotations

import contextlib as _contextlib
import gzip
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# fcntl is POSIX-only (unavailable on Windows).  Conditional import keeps the
# module importable on all platforms; file locking is silently skipped when absent
# (Windows concurrent-upload race is a known limitation — single-instance installs
# are not affected).
try:
    import fcntl as _fcntl
    _HAS_FLOCK = True
except ImportError:
    _HAS_FLOCK = False

from .htrace import (
    _assert_safe_htrace,
    _inc_dropped,
    _enforce_caps,
    compress_for_upload,
    htrace_dir,
    _today_utc,
)
from .htrace_consent import (
    healing_traces_enabled,
    load_consent_act_id,
    load_or_create_instance_id,
    ping_enabled,
    ensure_ping_tokens,
    _tenant_cfg_path,
    _open_no_redirect,
)
from .nerve import NerveFiber, NerveSignal, SEVERITY_OK, SEVERITY_LOW, SEVERITY_MEDIUM

logger = logging.getLogger(__name__)

# Base URL for all telemetry endpoints. Overridable via CORVIN_TELEMETRY_BASE_URL.
# Default: Railway deployment (the only host with a valid DNS record for the
# public API). A per-tenant spec.telemetry.upload_url still wins over this default
# (see _upload_url()).
_TELEMETRY_BASE = os.environ.get(
    "CORVIN_TELEMETRY_BASE_URL", "https://corvin-features-production.up.railway.app"
).rstrip("/")
_UPLOAD_URL_DEFAULT = f"{_TELEMETRY_BASE}/v1/telemetry/healing-traces"
_PING_URL_DEFAULT = f"{_TELEMETRY_BASE}/v1/telemetry/ping"
_UPLOAD_TIMEOUT_S = 30
_PING_TIMEOUT_S = 8
_PING_INTERVAL_S = 24 * 3600  # once per 24h
_MAX_BUNDLE_BYTES = 5 * 1024 * 1024  # 5 MB compressed
_MAX_BUNDLES_PER_DAY = 3
_LOCK_FILENAME = ".upload.lock"
_PING_LOCK_FILENAME = ".ping.lock"
# Recheck interval for the recurring ping loop (start_ping_thread). ping_if_due()
# self-throttles to once per _PING_INTERVAL_S (24h) via its own stamp file, so
# rechecking hourly is cheap and just makes sure a long-running process doesn't
# miss its daily window (e.g. after a missed check due to a transient error).
_PING_LOOP_INTERVAL_S = 3600
_LAST_UPLOAD_FILENAME = ".last_upload"
_LAST_PING_FILENAME = "last_ping"
_CORVINLOGS_REPO = "CorvinLabs/CorvinLogs"

# ── Ping-body fail-closed backstop ────────────────────────────────────────────
# CLAUDE.md invariant for the anonymous instance-count ping: "random uuid4 +
# version + coarse environment enums, no PII" (maintainer decision 2026-07-10,
# ADR — the earlier "version only" set starved the public stats page of every
# distribution chart). The uuid4 (instance_id) and the HMAC pseudonym
# (instance_token) travel in request HEADERS; the JSON body may carry ONLY the
# four allowlisted keys below, and every value must match its closed enum /
# pattern — free-form strings never leave the process. This backstop mirrors
# telemetry._assert_safe / htrace._assert_safe_htrace: it is fail-closed, so a
# body that over-ships is DROPPED (ValueError → ping_if_due returns False, no
# network call) rather than transmitted.
_PING_BODY_ALLOWED_KEYS = frozenset({
    # Core (existing)
    "corvin_version", "platform", "python_minor", "active_engine",
    # Phase 1 (new, 2026-07-17)
    "voice_languages_enabled",  # hex bitmask of 20 languages
    "voice_usage_rate",  # never | occasionally | frequently | always
    "stt_engine",  # system | openai | byok | unconfigured
    "install_type",  # fresh_install | upgrade | docker | manual | installer_msi | installer_ps1
    "auto_update_enabled",  # true | false | null
    "container_runtime",  # native | docker | vm | wsl | unknown
    "code_review_used",  # true | false | null
    "browser_automation_used",  # true | false | null
    # Phase 2 Geolocation (new, 2026-07-18) — closed enums only, no IPs/coordinates
    "country_code",  # ISO 3166-1 alpha-2 (e.g., "DE", "US", "XX")
    "continent",  # Africa | Americas | Asia | Europe | Oceania | Unknown
    "timezone_offset",  # integer seconds offset from UTC (e.g., 3600 for +01:00)
})
_PING_ALLOWED_PLATFORMS = frozenset({"linux", "win32", "darwin", "other"})
_RE_PING_VERSION = re.compile(r"^[0-9A-Za-z.\-+]{1,32}\Z")
_RE_PING_PY_MINOR = re.compile(r"^\d{1,2}\.\d{1,3}\Z")
_RE_VOICE_LANGS = re.compile(r"^(?:0x)?[0-9a-fA-F]{1,16}\Z")  # hex bitmask with optional 0x prefix

# Phase 1 Enum Validators
_VOICE_USAGE_RATES = frozenset({"never", "occasionally", "frequently", "always"})
_STT_ENGINES = frozenset({"system", "openai", "byok", "unconfigured"})
_INSTALL_TYPES = frozenset({"fresh_install", "upgrade", "docker", "manual", "installer_msi", "installer_ps1"})
_CONTAINER_RUNTIMES = frozenset({"native", "docker", "vm", "wsl", "unknown"})

# Phase 2 Geolocation (2026-07-18) — closed enums for GDPR safety
# ISO 3166-1 alpha-2 country codes (allowlist to prevent injection)
_ALLOWED_COUNTRY_CODES = frozenset({
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS", "BT", "BV", "BW", "BY", "BZ",
    "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN", "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ",
    "DE", "DJ", "DK", "DM", "DO", "DZ",
    "EC", "EE", "EG", "EH", "ER", "ES", "ET",
    "FI", "FJ", "FK", "FM", "FO", "FR",
    "GA", "GB", "GD", "GE", "GF", "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS", "GT", "GU", "GW", "GY",
    "HK", "HM", "HN", "HR", "HT", "HU",
    "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR", "IS", "IT",
    "JE", "JM", "JO", "JP",
    "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW", "KY", "KZ",
    "LA", "LB", "LC", "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY",
    "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK", "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ",
    "NA", "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP", "NR", "NU", "NZ",
    "OM",
    "PA", "PE", "PF", "PG", "PH", "PK", "PL", "PM", "PN", "PR", "PS", "PT", "PW", "PY",
    "QA",
    "RE", "RO", "RS", "RU", "RW",
    "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS", "ST", "SV", "SX", "SY", "SZ",
    "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO", "TR", "TT", "TV", "TW", "TZ",
    "UA", "UG", "UM", "US", "UY", "UZ",
    "VA", "VC", "VE", "VG", "VI", "VN", "VU",
    "WF", "WS",
    "YE", "YT",
    "ZA", "ZM", "ZW", "XX"  # XX = unknown/VPN/proxy
})

# Continents: closed set, no free-form strings
_ALLOWED_CONTINENTS = frozenset({
    "Africa", "Americas", "Asia", "Europe", "Oceania", "Unknown"
})

# Timezone offset ranges: validated as integer, ±14 hours = ±50400 seconds
_TIMEZONE_OFFSET_MIN = -12 * 3600  # -43200
_TIMEZONE_OFFSET_MAX = 14 * 3600   # 50400


def _assert_ping_safe(body: dict) -> None:
    """Raise if the ping body carries any non-allowlisted key or a value
    outside its closed enum/pattern. Fail-closed. Phase 1 (2026-07-17):
    extended with 8 new optional fields (voice, install, runtime, features)."""
    extra = set(body.keys()) - _PING_BODY_ALLOWED_KEYS
    if extra:
        raise ValueError(
            f"ping body carries non-allowlisted keys {sorted(extra)!r}"
        )
    checks = {
        "corvin_version": lambda v: isinstance(v, str) and bool(_RE_PING_VERSION.match(v)),
        "platform": lambda v: isinstance(v, str) and v in _PING_ALLOWED_PLATFORMS,
        "python_minor": lambda v: isinstance(v, str) and bool(_RE_PING_PY_MINOR.match(v)),
        "active_engine": lambda v: isinstance(v, str) and (v in _ALLOWED_ENGINES or v == "unknown"),
        # Phase 1 (new)
        "voice_languages_enabled": lambda v: isinstance(v, str) and bool(_RE_VOICE_LANGS.match(v)),
        "voice_usage_rate": lambda v: isinstance(v, str) and v in _VOICE_USAGE_RATES,
        "stt_engine": lambda v: isinstance(v, str) and v in _STT_ENGINES,
        "install_type": lambda v: isinstance(v, str) and v in _INSTALL_TYPES,
        "auto_update_enabled": lambda v: v is None or isinstance(v, bool),
        "container_runtime": lambda v: isinstance(v, str) and v in _CONTAINER_RUNTIMES,
        "code_review_used": lambda v: v is None or isinstance(v, bool),
        "browser_automation_used": lambda v: v is None or isinstance(v, bool),
        # Phase 2 Geolocation (new, 2026-07-18)
        "country_code": lambda v: isinstance(v, str) and v in _ALLOWED_COUNTRY_CODES,
        "continent": lambda v: isinstance(v, str) and v in _ALLOWED_CONTINENTS,
        "timezone_offset": lambda v: isinstance(v, int) and _TIMEZONE_OFFSET_MIN <= v <= _TIMEZONE_OFFSET_MAX,
    }
    for key, val in body.items():
        if key not in checks or not checks[key](val):
            raise ValueError(f"ping body key {key!r} carries a non-allowlisted value")


def _home() -> Optional[Path]:
    try:
        from forge import paths as _p  # type: ignore[import]
        return _p.corvin_home()
    except Exception:  # noqa: BLE001
        return None


def _load_instance_token(home: Path) -> str:
    """Load the pre-computed instance_token issued at license time."""
    try:
        p = home / "aco" / "telemetry" / "htrace-token.txt"
        return p.read_text(encoding="utf-8").strip()[:64]
    except OSError:
        return ""


def _load_telemetry_token(home: Path) -> str:
    """Load the scoped telemetry Bearer token (scope=healing_traces, 90d TTL).

    Enforces 0o600 permissions on the token file.  ConsentAct.save() does the
    same for htrace-consent-act.json, but .telemetry_token is provisioned
    externally and may be written with a permissive umask.  A world-readable
    token would allow any local user to post forged bundles.
    """
    try:
        p = home / "aco" / "telemetry" / ".telemetry_token"
        if not p.exists():
            return ""
        try:
            if p.stat().st_mode & 0o777 != 0o600:
                p.chmod(0o600)
        except OSError:
            pass  # best-effort; proceed to read
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _load_corvinlogs_token() -> str:
    """Fine-grained GitHub PAT with contents:write on CorvinLabs/CorvinLogs."""
    return os.environ.get("CORVINLOGS_GITHUB_TOKEN", "")


def _last_ping_path(home: Path) -> Path:
    return home / "aco" / "telemetry" / _LAST_PING_FILENAME


_ALLOWED_ENGINES = frozenset(
    {"claude_code", "hermes", "opencode", "codex_cli", "copilot"}
)


def _active_engine_path(home: Path) -> Path:
    """State file holding the bridge-resolved OS engine (one per CORVIN_HOME)."""
    return home / "aco" / "telemetry" / "active_engine"


def record_active_engine(home: Path, engine: str) -> None:
    """Persist the bridge-resolved OS engine so the out-of-process activity ping
    can attribute this install to its REAL engine instead of "unknown".

    The bridge adapter is the only component that runs the full engine ladder
    (CORVIN_OS_ENGINE env → hardened claude-CLI probe → hermes). That result
    lives only in the bridge process's memory; the ping fires from a SEPARATE
    process (corvin-serve) whose environment never inherits it, so the ping
    previously fell through to "unknown" for almost every install. Writing the
    resolved engine to a shared file bridges that process boundary.

    Allow-list validated (no attacker-controlled / free-form value is stored),
    atomic (temp file + os.replace so the concurrent ping never reads a torn
    write), write-only-if-changed (this runs on the per-turn hot path; after the
    first write it is a cheap no-op), and fail-soft (never raises).
    """
    try:
        eng = (engine or "").strip().lower()
        if eng not in _ALLOWED_ENGINES:
            return  # never persist an unknown / spoofed value
        p = _active_engine_path(home)
        try:
            if p.exists() and p.read_text(encoding="utf-8").strip() == eng:
                return  # unchanged — skip the write entirely
        except OSError:
            pass
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(eng, encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            with _contextlib.suppress(OSError):
                tmp.unlink()
    except Exception:  # noqa: BLE001
        pass


def _collect_voice_languages(home: Path) -> str:
    """Collect enabled voice languages as hex bitmask.

    Maps 20 languages: de, en, es, fr, it, pt, nl, pl, ru, ja, zh, ko, ar, tr, sv, da, no, fi, el, cs.
    Returns hex string (e.g., "0x0FFFFF80") or "0" if unconfigured.
    """
    try:
        # Languages in order: de(0), en(1), es(2), fr(3), it(4), pt(5), nl(6), pl(7), ru(8), ja(9),
        #                     zh(10), ko(11), ar(12), tr(13), sv(14), da(15), no(16), fi(17), el(18), cs(19)
        lang_map = {
            "de": 0, "en": 1, "es": 2, "fr": 3, "it": 4, "pt": 5, "nl": 6, "pl": 7, "ru": 8, "ja": 9,
            "zh": 10, "ko": 11, "ar": 12, "tr": 13, "sv": 14, "da": 15, "no": 16, "fi": 17, "el": 18, "cs": 19,
        }
        bitmask = 0
        voice_config = home / ".config" / "corvin-voice" / "profile.json"
        if voice_config.exists():
            import json as _j
            try:
                data = _j.loads(voice_config.read_text(encoding="utf-8"))
                display_language = data.get("display_language", "")
                if display_language in lang_map:
                    bitmask |= (1 << lang_map[display_language])
            except Exception:
                pass
        return format(bitmask, "x") if bitmask else "0"
    except Exception:  # noqa: BLE001
        return "0"


def _collect_voice_usage_rate(home: Path) -> str:
    """Estimate voice usage rate from audit log (heuristic).

    Returns: never | occasionally | frequently | always
    """
    try:
        audit = home / "audit.jsonl"
        if not audit.exists():
            return "never"
        # Heuristic: count voice events in the last 7 days
        import time as _t
        lines = audit.read_text(encoding="utf-8", errors="replace").strip().split("\n")
        voice_events = 0
        total_events = 0
        threshold = _t.time() - (7 * 86400)  # 7 days ago
        for line in lines[-500:]:  # scan last 500 events
            if not line.strip():
                continue
            try:
                import json as _j
                rec = _j.loads(line)
                total_events += 1
                ts = rec.get("timestamp", 0)
                if ts > threshold and "voice" in str(rec).lower():
                    voice_events += 1
            except Exception:
                pass
        if total_events == 0:
            return "never"
        rate = voice_events / max(1, total_events)
        if rate > 0.5:
            return "frequently"
        elif rate > 0.2:
            return "occasionally"
        return "never"
    except Exception:  # noqa: BLE001
        return "never"


def _collect_stt_engine(home: Path) -> str:
    """Detect configured STT engine.

    Returns: system | openai | byok | unconfigured
    """
    try:
        voice_config = home / ".config" / "corvin-voice" / "stt.yaml"
        if voice_config.exists():
            text = voice_config.read_text(encoding="utf-8", errors="replace")
            if "openai" in text.lower():
                return "openai"
            if "byok" in text.lower() or "api_key" in text.lower():
                return "byok"
            return "system"
        return "unconfigured"
    except Exception:  # noqa: BLE001
        return "unconfigured"


def _collect_install_type(home: Path) -> str:
    """Detect installation type.

    Returns: fresh_install | upgrade | docker | manual | installer_msi | installer_ps1
    """
    try:
        install_marker = home / "aco" / "install.marker"
        if install_marker.exists():
            marker = install_marker.read_text(encoding="utf-8", errors="replace").strip()
            if marker in ("docker", "manual", "installer_msi", "installer_ps1"):
                return marker
            if "upgrade" in marker.lower():
                return "upgrade"
        return "fresh_install"
    except Exception:  # noqa: BLE001
        return "unknown"


def _collect_container_runtime() -> str:
    """Detect if running in container/VM.

    Returns: native | docker | vm | wsl | unknown
    """
    try:
        # Check for /.dockerenv (Docker marker)
        if Path("/.dockerenv").exists():
            return "docker"
        # Check for WSL marker (Windows Subsystem for Linux)
        if Path("/proc/version").exists():
            proc_version = Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
            if "microsoft" in proc_version or "wsl" in proc_version:
                return "wsl"
        # Check for VM via dmidecode or systemd (best-effort)
        try:
            import subprocess as _sp
            result = _sp.run(["systemd-detect-virt"], capture_output=True, text=True, timeout=2)
            vm_type = result.stdout.strip().lower()
            if vm_type and vm_type not in ("none", "unknown"):
                return "vm"
        except Exception:
            pass
        return "native"
    except Exception:  # noqa: BLE001
        return "unknown"


def _collect_country_code(home: Path) -> str:
    """Detect country code from system timezone.

    Uses tzdata to map the local timezone to an ISO 3166-1 alpha-2 country code.
    Returns 2-character uppercase code or "XX" if unknown/VPN.
    Fail-soft: never raises.
    """
    try:
        import time as _t
        # Try to detect timezone from environment or system
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore[import]

        # Get local timezone name
        tz_name = None
        try:
            import os as _os
            if "TZ" in _os.environ:
                tz_name = _os.environ["TZ"]
            else:
                # Try to read from /etc/timezone (Linux)
                try:
                    tz_path = Path("/etc/timezone")
                    if tz_path.exists():
                        tz_name = tz_path.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
        except Exception:
            pass

        # Fallback: try to infer from offset
        if not tz_name:
            # Map common timezone offsets to regions (heuristic)
            # This is a best-effort fallback when timezone name is unavailable
            offset_map = {
                -28800: "US",  # PST (-8h)
                -25200: "US",  # MST (-7h)
                -21600: "US",  # CST (-6h)
                -18000: "US",  # EST (-5h)
                -3600: "GB",   # GMT
                0: "GB",       # UTC
                3600: "DE",    # CET (+1h)
                7200: "EE",    # EET (+2h)
                19800: "IN",   # IST (+5:30h)
                28800: "CN",   # CST (+8h)
                32400: "JP",   # JST (+9h)
            }
            offset_sec = int(_t.timezone if _t.daylight == 0 else _t.altzone)
            # Negative offset means east of UTC
            if -offset_sec in offset_map:
                return offset_map[-offset_sec]
            return "XX"

        # Map common timezone -> country code (simplified; real system would use tzdata)
        tz_to_country = {
            "Europe/Berlin": "DE", "Europe/Paris": "FR", "Europe/London": "GB",
            "Europe/Madrid": "ES", "Europe/Rome": "IT", "Europe/Amsterdam": "NL",
            "Europe/Brussels": "BE", "Europe/Vienna": "AT", "Europe/Prague": "CZ",
            "Europe/Warsaw": "PL", "Europe/Moscow": "RU", "Europe/Istanbul": "TR",
            "America/New_York": "US", "America/Chicago": "US", "America/Denver": "US",
            "America/Los_Angeles": "US", "America/Toronto": "CA", "America/Mexico_City": "MX",
            "America/Sao_Paulo": "BR", "America/Buenos_Aires": "AR", "America/Santiago": "CL",
            "Asia/Tokyo": "JP", "Asia/Shanghai": "CN", "Asia/Hong_Kong": "HK",
            "Asia/Singapore": "SG", "Asia/Bangkok": "TH", "Asia/Seoul": "KR",
            "Asia/Mumbai": "IN", "Asia/Kolkata": "IN", "Asia/Dubai": "AE",
            "Africa/Cairo": "EG", "Africa/Johannesburg": "ZA", "Africa/Lagos": "NG",
            "Australia/Sydney": "AU", "Australia/Melbourne": "AU",
            "Pacific/Auckland": "NZ", "UTC": "XX",
        }
        if tz_name in tz_to_country:
            return tz_to_country[tz_name]

        # Extract country code from timezone string (e.g., Europe/Berlin -> DE)
        if "/" in tz_name:
            try:
                region = tz_name.split("/")[1]  # e.g., "Berlin"
                # Simple heuristic: if it's a city name, try to resolve it
                # This is best-effort and may return XX (unknown)
                if len(region) == 2 and region.isupper():
                    if region in _ALLOWED_COUNTRY_CODES:
                        return region
            except (IndexError, ValueError):
                pass

        return "XX"  # Unknown/VPN
    except Exception:  # noqa: BLE001
        return "XX"


def _collect_continent(home: Path) -> str:
    """Map country code to continent.

    Returns: Africa | Americas | Asia | Europe | Oceania | Unknown
    Uses simple country_code -> continent mapping.
    Fail-soft: never raises.
    """
    try:
        cc = _collect_country_code(home)

        # Country-to-continent mapping (simplified)
        continent_map = {
            # Africa
            "DZ": "Africa", "AO": "Africa", "BJ": "Africa", "BW": "Africa", "BF": "Africa",
            "BI": "Africa", "CM": "Africa", "CV": "Africa", "CF": "Africa", "TD": "Africa",
            "KM": "Africa", "CG": "Africa", "CD": "Africa", "CI": "Africa", "DJ": "Africa",
            "EG": "Africa", "GQ": "Africa", "ER": "Africa", "ET": "Africa", "GA": "Africa",
            "GM": "Africa", "GH": "Africa", "GN": "Africa", "GW": "Africa", "KE": "Africa",
            "LS": "Africa", "LR": "Africa", "LY": "Africa", "MG": "Africa", "MW": "Africa",
            "ML": "Africa", "MR": "Africa", "MU": "Africa", "MA": "Africa", "MZ": "Africa",
            "NA": "Africa", "NE": "Africa", "NG": "Africa", "RE": "Africa", "RW": "Africa",
            "SH": "Africa", "ST": "Africa", "SN": "Africa", "SC": "Africa", "SL": "Africa",
            "SO": "Africa", "ZA": "Africa", "SS": "Africa", "SD": "Africa", "SZ": "Africa",
            "TZ": "Africa", "TG": "Africa", "TN": "Africa", "UG": "Africa", "ZM": "Africa",
            "ZW": "Africa", "EH": "Africa",
            # Americas
            "AG": "Americas", "AR": "Americas", "BS": "Americas", "BB": "Americas", "BZ": "Americas",
            "CA": "Americas", "CR": "Americas", "CU": "Americas", "DM": "Americas", "DO": "Americas",
            "SV": "Americas", "GD": "Americas", "GT": "Americas", "GY": "Americas", "HT": "Americas",
            "HN": "Americas", "JM": "Americas", "MX": "Americas", "NI": "Americas", "PA": "Americas",
            "PY": "Americas", "PE": "Americas", "KN": "Americas", "LC": "Americas", "VC": "Americas",
            "SR": "Americas", "TT": "Americas", "US": "Americas", "UY": "Americas", "VE": "Americas",
            "BM": "Americas", "GL": "Americas", "AI": "Americas", "AW": "Americas", "BQ": "Americas",
            "CW": "Americas", "SX": "Americas", "FK": "Americas", "GF": "Americas", "GP": "Americas",
            "MQ": "Americas", "PM": "Americas", "PR": "Americas", "VG": "Americas", "VI": "Americas",
            "BO": "Americas", "BR": "Americas", "CL": "Americas", "CO": "Americas", "EC": "Americas",
            # Asia
            "AF": "Asia", "AM": "Asia", "AZ": "Asia", "BH": "Asia", "BD": "Asia", "BT": "Asia",
            "BN": "Asia", "KH": "Asia", "CN": "Asia", "GE": "Asia", "HK": "Asia", "IN": "Asia",
            "ID": "Asia", "IR": "Asia", "IQ": "Asia", "IL": "Asia", "JP": "Asia", "JO": "Asia",
            "KZ": "Asia", "KP": "Asia", "KR": "Asia", "KW": "Asia", "KG": "Asia", "LA": "Asia",
            "LB": "Asia", "MO": "Asia", "MY": "Asia", "MV": "Asia", "MN": "Asia", "MM": "Asia",
            "NP": "Asia", "OM": "Asia", "PK": "Asia", "PS": "Asia", "PH": "Asia", "QA": "Asia",
            "SA": "Asia", "SG": "Asia", "LK": "Asia", "SY": "Asia", "TW": "Asia", "TJ": "Asia",
            "TH": "Asia", "TL": "Asia", "TR": "Asia", "TM": "Asia", "AE": "Asia", "UZ": "Asia",
            "VN": "Asia", "YE": "Asia", "IO": "Asia", "CC": "Asia",
            # Europe
            "AX": "Europe", "AL": "Europe", "AD": "Europe", "AT": "Europe", "BY": "Europe",
            "BE": "Europe", "BA": "Europe", "BG": "Europe", "HR": "Europe", "CY": "Europe",
            "CZ": "Europe", "DK": "Europe", "DE": "Europe", "EE": "Europe", "FO": "Europe",
            "FI": "Europe", "FR": "Europe", "GB": "Europe", "GR": "Europe", "HU": "Europe",
            "IS": "Europe", "IE": "Europe", "IT": "Europe", "XK": "Europe", "LV": "Europe",
            "LI": "Europe", "LT": "Europe", "LU": "Europe", "MT": "Europe", "MD": "Europe",
            "MC": "Europe", "ME": "Europe", "NL": "Europe", "NO": "Europe", "PL": "Europe",
            "PT": "Europe", "RO": "Europe", "RU": "Europe", "SM": "Europe", "RS": "Europe",
            "SK": "Europe", "SI": "Europe", "ES": "Europe", "SE": "Europe", "CH": "Europe",
            "UA": "Europe", "VA": "Europe", "IM": "Europe", "JE": "Europe", "GG": "Europe",
            "SJ": "Europe",
            # Oceania
            "AU": "Oceania", "FJ": "Oceania", "KI": "Oceania", "MH": "Oceania",
            "FM": "Oceania", "NR": "Oceania", "NZ": "Oceania", "PW": "Oceania",
            "PG": "Oceania", "WS": "Oceania", "SB": "Oceania", "TO": "Oceania",
            "TV": "Oceania", "VU": "Oceania", "NU": "Oceania", "NC": "Oceania",
            "PF": "Oceania", "WF": "Oceania",
            # Special/Unknown
            "XX": "Unknown",
        }
        return continent_map.get(cc, "Unknown")
    except Exception:  # noqa: BLE001
        return "Unknown"


def _collect_timezone_offset(home: Path) -> int:
    """Collect local UTC offset in seconds.

    Returns: integer seconds offset from UTC (e.g., 3600 for +01:00, -18000 for -05:00)
    Fail-soft: never raises, defaults to 0 (UTC) on error.
    """
    try:
        import time as _t
        import calendar as _cal

        # Try to get current UTC offset
        now = datetime.now()

        # Get the local time's UTC offset
        # On most systems, -time.timezone or -time.altzone gives the offset
        if _t.daylight:
            # DST is in effect or might be
            offset = -_t.altzone if _t.daylight and _t.daylight != 0 else -_t.timezone
        else:
            offset = -_t.timezone

        # Clamp to valid range
        if not (_TIMEZONE_OFFSET_MIN <= offset <= _TIMEZONE_OFFSET_MAX):
            return 0  # Fallback to UTC if sanity check fails

        return offset
    except Exception:  # noqa: BLE001
        return 0  # UTC fallback


def _detect_active_engine(home: Path) -> str:
    """Return the configured OS engine id, anonymised to a known set.

    Resolution order (first hit wins):
      1. CORVIN_WORKER_ENGINE / CORVIN_OS_ENGINE env vars — present only when the
         ping happens to run inside the bridge process itself.
      2. The active_engine state file written by the bridge adapter
         (record_active_engine) — the cross-process bridge that makes the real
         engine visible to the out-of-process corvin-serve ping.
      3. The tenant YAML engine key, for installs that pin it explicitly but have
         never launched a bridge OS-turn.
    Unknown / unreadable → "unknown". Fail-soft: never raises.
    """
    try:
        for env_key in ("CORVIN_WORKER_ENGINE", "CORVIN_OS_ENGINE"):
            val = os.environ.get(env_key, "").strip().lower()
            if val in _ALLOWED_ENGINES:
                return val

        # State file written by the bridge adapter (see record_active_engine).
        try:
            state_p = _active_engine_path(home)
            if state_p.exists():
                val = state_p.read_text(encoding="utf-8").strip().lower()
                if val in _ALLOWED_ENGINES:
                    return val
        except OSError:
            pass

        # Fallback: tenant YAML. The canonical keys are ``default_engine``
        # (persona/tenant default, ADR-0159) and ``default_worker_engine``
        # (console selection); the legacy ``worker_engine`` is matched too.
        # The previous ``worker_engine`` regex missed ``default_engine`` — the
        # key the tenant template actually ships — so this fallback never fired.
        cfg_path = _tenant_cfg_path(home)
        if cfg_path.exists():
            import re as _re
            text = cfg_path.read_text(encoding="utf-8", errors="replace")
            m = _re.search(
                r"(?:default_worker_engine|default_engine|worker_engine)\s*:\s*"
                r"[\"']?([A-Za-z0-9_]+)",
                text,
            )
            if m:
                val = m.group(1).strip().lower()
                return val if val in _ALLOWED_ENGINES else "unknown"
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def ping_if_due(home: Path) -> bool:
    """Send a daily activity ping to api.corvin-labs.com/v1/telemetry/ping.

    Returns True if the ping was sent (or already sent today), False on error.
    Fail-soft: never raises, never blocks startup.
    Gate: ping_enabled(home) — opt-out, default ON. No ConsentAct required;
          the ping sends only a pseudonymous token + version + coarse
          allowlisted environment enums (platform / python minor / engine id),
          no personal data.
    Rate: once per 24h, tracked by ~/.corvin/aco/telemetry/last_ping.

    On the very first call (no tokens provisioned yet) ensure_ping_tokens()
    provisions them automatically so a fresh install is counted immediately.
    """
    try:
        # Opt-out gate — true by default, disabled only by explicit config flag.
        if not ping_enabled(home):
            return False

        # Lock the check-then-provision-then-send-then-stamp sequence below
        # (same pattern as run_upload_cycle's _LOCK_FILENAME) — without it,
        # two processes sharing one CORVIN_HOME (e.g. a bridge daemon and the
        # web console both booting around the same time) could both pass the
        # "already pinged today?" check before either writes the stamp,
        # sending two ping events for what the server should count as one
        # instance-day (adversarial review finding). _HAS_FLOCK is False on
        # Windows — single-instance installs there are unaffected either way.
        #
        # ensure_ping_tokens() is deliberately called INSIDE this locked
        # section (it used to run before the lock was acquired) — two
        # processes racing to provision tokens for the first time could
        # otherwise both call provision_telemetry_tokens() concurrently and
        # interleave two different token-endpoint responses into a
        # mismatched instance/telemetry-token pair that never self-heals
        # (adversarial review finding).
        lock_path = htrace_dir(home) / _PING_LOCK_FILENAME
        lf = None
        try:
            htrace_dir(home).mkdir(parents=True, exist_ok=True)
            lf = lock_path.open("w")
            if _HAS_FLOCK:
                _fcntl.flock(lf, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            logger.debug("htrace: ping already in progress elsewhere (lock held)")
            if lf is not None:
                with _contextlib.suppress(Exception):
                    lf.close()
            return True  # not an error — another process is handling it

        try:
            # Auto-provision tokens on first boot; fail-soft if unreachable.
            if not ensure_ping_tokens(home):
                logger.debug("htrace: ping skipped — token provisioning not yet complete")
                return False

            # Rate-limit: skip if a ping was already sent within the last 24h.
            stamp = _last_ping_path(home)
            try:
                if stamp.exists():
                    age = time.time() - stamp.stat().st_mtime
                    # A backward clock jump (NTP correction, VM/container clock
                    # skew on boot) makes `age` negative — always < interval,
                    # which previously suppressed the ping indefinitely
                    # (adversarial review finding). Only an actually-elapsed,
                    # non-negative age within the window counts as "already
                    # sent"; a negative age falls through and pings for real.
                    if 0 <= age < _PING_INTERVAL_S:
                        return True  # already done today
            except OSError:
                pass  # unreadable stamp — proceed to ping

            telemetry_token = _load_telemetry_token(home)
            instance_token = _load_instance_token(home)
            instance_id = load_or_create_instance_id(home)

            try:
                from importlib.metadata import version as _pkg_version
                corvin_version = _pkg_version("corvinos")
            except Exception:  # noqa: BLE001
                try:
                    # Dev-mode fallback: read __version__ from the installed console package.
                    from corvin_console import __version__ as _cv  # type: ignore[attr-defined]
                    corvin_version = _cv
                except Exception:  # noqa: BLE001
                    corvin_version = "unknown"

            # Body carries the version plus coarse environment enums (platform / python_minor / active_engine)
            # plus Phase 1 feature flags (voice, install, container, features).
            # The uuid4 (instance_id) and the HMAC pseudonym (instance_token) go in the headers below.
            # All values are closed enums (never free-form); _assert_ping_safe is a fail-closed
            # backstop that validates keys AND values (see its definition).
            # Phase 1 (2026-07-17): added 8 fields for language distribution, install type, runtime,
            # feature adoption. All GDPR-safe (closed enums, no PII, no behavioral fingerprinting).
            # Phase 2 (2026-07-18): added 3 geolocation fields (country_code, continent, timezone_offset)
            # for live-world-map stats. No IP addresses, no fine-grained coordinates, only closed enums.
            raw_platform = sys.platform
            ping_body = {
                "corvin_version": corvin_version,
                "platform": raw_platform
                if raw_platform in _PING_ALLOWED_PLATFORMS
                else "other",
                "python_minor": f"{sys.version_info[0]}.{sys.version_info[1]}",
                "active_engine": _detect_active_engine(home),
                # Phase 1 (new, 2026-07-17)
                "voice_languages_enabled": _collect_voice_languages(home),
                "voice_usage_rate": _collect_voice_usage_rate(home),
                "stt_engine": _collect_stt_engine(home),
                "install_type": _collect_install_type(home),
                "auto_update_enabled": None,  # TODO: detect from config
                "container_runtime": _collect_container_runtime(),
                "code_review_used": None,  # TODO: detect from audit
                "browser_automation_used": None,  # TODO: detect from audit
                # Phase 2 Geolocation (new, 2026-07-18) — closed enums, GDPR-safe
                "country_code": _collect_country_code(home),
                "continent": _collect_continent(home),
                "timezone_offset": _collect_timezone_offset(home),
            }
            _assert_ping_safe(ping_body)
            payload = json.dumps(ping_body).encode("utf-8")
            req = urllib.request.Request(
                _PING_URL_DEFAULT,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {telemetry_token}",
                    "X-HTTrace-Instance-Token": instance_token,
                    "X-HTrace-Instance-Id": instance_id,
                },
            )
            # No-redirect + https-only opener (F8): the ping carries the Bearer
            # telemetry token + instance token/id in headers. A plain urlopen
            # would follow a 302 to an attacker host WITH those credentials
            # intact, and would POST them in plaintext over an http:// base URL.
            # Route through the same hardened opener the token endpoint uses.
            with _open_no_redirect(req, _PING_TIMEOUT_S) as resp:
                status = resp.getcode()
                if not (200 <= status < 300):
                    logger.debug("htrace: ping returned %d", status)
                    return False

            # Record successful ping (touch the stamp with the current timestamp).
            try:
                stamp.parent.mkdir(parents=True, exist_ok=True)
                stamp.write_text(str(int(time.time())), encoding="utf-8")
            except OSError:
                pass
            return True
        finally:
            with _contextlib.suppress(Exception):
                if lf is not None:
                    if _HAS_FLOCK:
                        _fcntl.flock(lf, _fcntl.LOCK_UN)
                    lf.close()
                # Do NOT unlink the lock file: another process may be blocked on
                # this inode's flock (provision_telemetry_tokens). Unlinking it
                # lets the next opener create a fresh inode whose lock is
                # uncontended, defeating the shared lock and re-opening the
                # mismatched-token-pair race. Leave the file; the lock is the
                # inode, not the record.
    except Exception as e:  # noqa: BLE001
        logger.debug("htrace: ping failed (non-fatal): %s", e)
        return False


def ping_loop(home: Path) -> None:
    """Run forever: call ping_if_due() every hour.

    ping_if_due() self-throttles to once per 24h via its own stamp file, so
    this just makes sure a long-running process keeps checking. Without this
    loop, a process that never restarts (corvin-serve, the primary pip/uv
    install path) sent the daily ping exactly ONCE at boot and then never
    again for the rest of its uptime — silently dropping out of
    active_7d/active_30d after the first day despite staying up and in active
    use (adversarial review finding: _fire_startup_ping was a one-shot call
    because corvin_console.standalone has no FastAPI lifespan to run the
    recurring boot-healer cycle that normally re-invokes ping_if_due every
    5 minutes for the gateway/systemd deployment path).
    """
    while True:
        try:
            ping_if_due(home)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(_PING_LOOP_INTERVAL_S)


_ping_thread_started = False
_ping_thread_lock = threading.Lock()


def start_ping_thread(home: Path) -> None:
    """Start the recurring ping-check daemon thread (idempotent per process).

    Unlike start_heartbeat_thread(), this does NOT gate on ping_enabled(home)
    at start time — ping_if_due() already re-checks ping_enabled() on every
    single call, so an opt-out set mid-process-lifetime takes effect on the
    very next hourly check instead of requiring a restart.
    """
    global _ping_thread_started
    with _ping_thread_lock:
        if _ping_thread_started:
            return
        t = threading.Thread(
            target=ping_loop, args=(home,), daemon=True, name="corvin-ping",
        )
        t.start()
        _ping_thread_started = True
        logger.debug("htrace: recurring ping thread started")


def _upload_url(home: Path) -> str:
    try:
        import yaml  # type: ignore[import]
        cfg_path = _tenant_cfg_path(home)
        if cfg_path.exists():
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            # tenant.corvin.yaml is a k8s-style manifest — settings live under
            # spec:. Reading data.get("telemetry") at the top level was always
            # None, so a per-tenant upload_url override was silently ignored.
            spec = data.get("spec", data)
            if isinstance(spec, dict):
                tele = spec.get("telemetry", {})
                if isinstance(tele, dict):
                    return tele.get("upload_url", _UPLOAD_URL_DEFAULT)
    except Exception:  # noqa: BLE001
        pass
    return _UPLOAD_URL_DEFAULT


def _already_uploaded_today(home: Path) -> bool:
    try:
        p = htrace_dir(home) / _LAST_UPLOAD_FILENAME
        if not p.exists():
            return False
        last = p.read_text(encoding="utf-8").strip()
        today = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        parts = last.split(",")  # "<date>,<count>"
        if parts[0] == today and int(parts[1]) >= _MAX_BUNDLES_PER_DAY:
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _record_upload(home: Path) -> None:
    try:
        p = htrace_dir(home) / _LAST_UPLOAD_FILENAME
        today = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        existing = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        parts = existing.split(",")
        if parts[0] == today:
            count = int(parts[1]) + 1
        else:
            count = 1
        p.write_text(f"{today},{count}", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _check_bundle_ok(gz_path: Path) -> tuple[bool, int]:
    """Validate AND FILTER a .jsonl.gz bundle in place. Returns (ok, valid_count).

    The fail-closed drop-semantics MUST hold at the send boundary, not only the
    write boundary: the CLAUDE.md telemetry invariant is that a record carrying
    a PII/secret shape is DROPPED *rather than sent*. Transmission itself is the
    GDPR event, and the bundle is additionally mirrored to a PUBLIC repo — so
    relying on the server-side validator to reject bad records after they have
    already left the machine is not sufficient.

    This therefore REWRITES the gz to contain only records that pass
    ``_assert_safe_htrace``; any failing record is dropped from what gets sent.
    Returns ``ok=True`` only if at least one safe record remains and the bundle
    is within the size cap.

    stat() is inside the try block so that a FileNotFoundError (file deleted
    between compress_for_upload and _check_bundle_ok) is caught and returns
    (False, 0) instead of propagating to the outer except in run_upload_cycle
    and incorrectly returning 'error'.
    """
    try:
        size = gz_path.stat().st_size
        if size > _MAX_BUNDLE_BYTES:
            logger.warning("htrace: bundle too large (%d bytes) — skipping", size)
            return False, 0
        safe_lines: list[str] = []
        dropped = 0
        with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    _assert_safe_htrace(record)
                    safe_lines.append(json.dumps(record, ensure_ascii=False))
                except (json.JSONDecodeError, ValueError) as e:
                    dropped += 1
                    logger.debug("htrace: unsafe record DROPPED before send: %s", e)
        if dropped:
            logger.warning("htrace: dropped %d unsafe record(s) from bundle before upload",
                           dropped)
        if not safe_lines:
            return False, 0
        # Rewrite the bundle atomically with only the passing records, so the
        # posted (and mirrored) bytes contain nothing that failed the validator.
        if dropped:
            tmp = gz_path.with_suffix(gz_path.suffix + ".filtered")
            with gzip.open(tmp, "wt", encoding="utf-8") as out:
                out.write("\n".join(safe_lines) + "\n")
            tmp.replace(gz_path)
        return True, len(safe_lines)
    except Exception as e:  # noqa: BLE001
        logger.warning("htrace: bundle validation error: %s", e)
        return False, 0


def _post_bundle(
    gz_path: Path,
    *,
    upload_url: str,
    bearer_token: str,
    instance_token: str,
    instance_id: str,
    consent_act_id: str,
) -> bool:
    """POST the bundle. Returns True on 2xx, False on error."""
    if not upload_url.lower().startswith("https://"):
        logger.warning("htrace: upload_url must be https:// — skipping")
        return False
    try:
        data = gz_path.read_bytes()
        req = urllib.request.Request(
            upload_url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/x-ndjson+gzip",
                "Authorization": f"Bearer {bearer_token}",
                "X-HTTrace-Schema": "htrace/1",
                "X-HTTrace-Instance-Token": instance_token,
                "X-HTrace-Instance-Id": instance_id,
                "X-HTTrace-Consent-Act-Id": consent_act_id,
            },
        )
        # No-redirect + https-only (F8): the bundle POST carries a Bearer token +
        # instance token/id + consent_act_id. A 302 must never re-send them to a
        # different host. (The https prefix is already checked above.)
        with _open_no_redirect(req, _UPLOAD_TIMEOUT_S) as resp:
            status = resp.getcode()
            if 200 <= status < 300:
                logger.info("htrace: bundle uploaded (%s → %d)", gz_path.name, status)
                return True
            logger.warning("htrace: upload returned %d — will retry", status)
            return False
    except Exception as e:  # noqa: BLE001
        logger.info("htrace: upload failed (will retry tomorrow): %s", e)
        return False


def _push_to_corvinlogs(gz_path: Path, *, instance_token: str) -> bool:
    """Transparency mirror: push bundle to CorvinLabs/CorvinLogs via GitHub API.

    Uses CORVINLOGS_GITHUB_TOKEN env var (fine-grained PAT, contents:write).
    Silent no-op when token is absent.
    """
    token = _load_corvinlogs_token()
    if not token:
        return False
    try:
        import base64

        data = gz_path.read_bytes()
        if len(data) > _MAX_BUNDLE_BYTES:
            return False

        # File path in repo: traces/htrace-v1/<date>/<token_prefix>_<date>.jsonl.gz
        stem = gz_path.stem.replace(".jsonl", "")  # YYYY-MM-DD
        token_prefix = instance_token[:12] if instance_token else "unknown"
        repo_path = f"traces/htrace-v1/{stem}/{token_prefix}_{stem}.jsonl.gz"

        payload = json.dumps({
            "message": f"chore: add healing trace bundle {stem}",
            "content": base64.b64encode(data).decode("ascii"),
            "branch": "main",
        }).encode("utf-8")

        api_url = f"https://api.github.com/repos/{_CORVINLOGS_REPO}/contents/{repo_path}"
        req = urllib.request.Request(
            api_url,
            data=payload,
            method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "CorvinOS-HealingTrace/1",
            },
        )
        # No-redirect + https-only (F8): this PUT carries a GitHub PAT with
        # contents:write. A redirect must never forward that credential to
        # another host. api.github.com is https, so the guard also asserts it.
        with _open_no_redirect(req, _UPLOAD_TIMEOUT_S) as resp:
            status = resp.getcode()
            ok = 200 <= status < 300
            if ok:
                logger.info("htrace: pushed to CorvinLogs (%s)", repo_path)
            return ok
    except Exception as e:  # noqa: BLE001
        logger.debug("htrace: CorvinLogs mirror failed (non-fatal): %s", e)
        return False


def _write_upload_audit_event(outcome: str, count: int) -> None:
    try:
        from forge import audit as _audit  # type: ignore[import]
        event = "htrace.upload.sent" if outcome == "sent" else f"htrace.upload.{outcome}"
        _audit.audit_event(event, {"record_count": count})
    except Exception:  # noqa: BLE001
        pass


def run_upload_cycle(home: Path) -> tuple[str, int]:
    """Run one upload cycle. Returns (outcome, record_count).

    outcome: "sent" | "skipped" | "error" | "no_bundle" | "already_done"
    Acquires a file lock to prevent concurrent runs.
    """
    lock_file = htrace_dir(home) / _LOCK_FILENAME
    # Open the lock file before entering the main try block so we can close
    # it in a dedicated except branch if flock raises.  Without this the FD
    # leaks on every concurrent invocation because the finally block of the
    # inner try is never entered when flock raises before it is reached.
    # Opt-out gate (default-ON): the healing-traces channel is disabled only by an
    # explicit spec.telemetry.healing_traces: false. scan() also checks this, but
    # run_upload_cycle() is a public function — direct callers (e.g. maintainer_cli
    # upload) must still honour the opt-out. (Safety rides on CONTENT-FREE records,
    # not on a consent gate — see the module docstring.)
    if not healing_traces_enabled(home):
        logger.debug("htrace: upload skipped — opt-out gate active")
        return "skipped", 0

    lf = None
    try:
        htrace_dir(home).mkdir(parents=True, exist_ok=True)
        lf = lock_file.open("w")
        if _HAS_FLOCK:
            _fcntl.flock(lf, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        logger.debug("htrace: upload already running (lock held)")
        if lf is not None:
            try:
                lf.close()
            except Exception:  # noqa: BLE001
                pass
        return "skipped", 0

    try:
        if _already_uploaded_today(home):
            return "already_done", 0

        _enforce_caps(home)

        # Compress yesterday's file
        gz = compress_for_upload(home)
        if gz is None:
            # Try the day before (startup catch-up for missed days)
            for delta in range(2, 8):
                date_str = (datetime.now(timezone.utc) - timedelta(days=delta)).strftime("%Y-%m-%d")
                gz = compress_for_upload(home, date_str=date_str)
                if gz is not None:
                    break

        if gz is None:
            _write_upload_audit_event("skipped", 0)
            return "no_bundle", 0

        ok, count = _check_bundle_ok(gz)
        if not ok or count == 0:
            # Move the invalid/empty bundle to sent/ so it is still subject to
            # the 14-day cap.  Ensure sent/ exists first — on a fresh install
            # (or after the directory was removed) it doesn't exist yet, and
            # Path.replace() would raise FileNotFoundError which is then
            # swallowed by the outer except, leaving the gz as an orphan.
            skip_dir = htrace_dir(home) / "sent"
            skip_dir.mkdir(exist_ok=True)
            gz.replace(skip_dir / gz.name)
            _write_upload_audit_event("skipped", 0)
            return "skipped", 0

        bearer = _load_telemetry_token(home)
        inst_token = _load_instance_token(home)
        instance_id = load_or_create_instance_id(home)
        consent_act_id = load_consent_act_id(home)
        url = _upload_url(home)

        # Create sent/ before posting so a disk-full / permission error on
        # mkdir doesn't cause a duplicate POST on the next cycle.  If mkdir
        # fails here we abort cleanly before touching the network.
        sent_dir = htrace_dir(home) / "sent"
        sent_dir.mkdir(exist_ok=True)

        sent = False
        if bearer:
            sent = _post_bundle(
                gz,
                upload_url=url,
                bearer_token=bearer,
                instance_token=inst_token,
                instance_id=instance_id,
                consent_act_id=consent_act_id,
            )

        if sent:
            # Only push to CorvinLogs after primary upload confirmed — prevents
            # data appearing on the public mirror without auth/audit trail.
            cl_token = _load_corvinlogs_token()
            if cl_token:
                mirror_ok = _push_to_corvinlogs(gz, instance_token=inst_token)
                if not mirror_ok:
                    logger.warning("htrace: CorvinLogs mirror failed — bundle kept locally")
                    try:
                        from forge import audit as _audit  # type: ignore[import]
                        _audit.audit_event("htrace.mirror.failed", {"bundle": gz.name})
                    except Exception:  # noqa: BLE001
                        pass
            gz.replace(sent_dir / gz.name)
            _record_upload(home)
            _write_upload_audit_event("sent", count)
            return "sent", count
        else:
            # Leave file for retry (up to 14-day cap)
            _write_upload_audit_event("error", 0)
            return "error", 0

    except Exception as e:  # noqa: BLE001
        logger.warning("htrace: upload cycle error: %s", e)
        _write_upload_audit_event("error", 0)
        return "error", 0
    finally:
        try:
            if lf is not None:
                if _HAS_FLOCK:
                    _fcntl.flock(lf, _fcntl.LOCK_UN)
                lf.close()
            lock_file.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


# ── NerveFiber ────────────────────────────────────────────────────────────────

class HealingTraceUploaderFiber(NerveFiber):
    """Daily healing-trace batch uploader (ADR-0180 M2+M3).

    Fires at boot if yesterday's bundle wasn't sent, and opportunistically
    when the scheduled nightly window passes (checked by last-upload stamp).
    Silent no-op when the opt-out flag disables the channel.
    """
    fiber_id = "htrace.uploader"
    fiber_version = "1.0.0"
    fiber_description = "ADR-0180: täglicher Healing-Trace-Upload (default-ON, opt-out; content-free)"

    def scan(self) -> list[NerveSignal]:
        home = _home()
        if home is None:
            return []

        # Opt-out activity ping — runs regardless of healing_traces consent.
        # ping_enabled() defaults True; no ConsentAct required (pseudonymous
        # token + version only, GDPR Art. 6(1)(f)). Must run BEFORE the
        # healing_traces gate so fresh installs without a ConsentAct are
        # still counted in the active-instance stats.
        ping_if_due(home)

        if not healing_traces_enabled(home):
            return []

        if _already_uploaded_today(home):
            return []

        outcome, count = run_upload_cycle(home)

        if outcome == "sent":
            return [NerveSignal(
                fiber_id=self.fiber_id,
                signal_type="htrace.upload.sent",
                severity=SEVERITY_OK,
                message=f"Healing-Traces hochgeladen ({count} Records)",
                data={"record_count": count},
                audit=True,
            )]
        elif outcome == "no_bundle":
            return []
        elif outcome == "error":
            return [NerveSignal(
                fiber_id=self.fiber_id,
                signal_type="htrace.upload.error",
                severity=SEVERITY_LOW,
                message="Healing-Trace-Upload fehlgeschlagen (wird morgen wiederholt)",
                data={},
                repair_hint="CORVINLOGS_GITHUB_TOKEN prüfen oder Netzwerk",
                audit=True,
            )]
        return []
