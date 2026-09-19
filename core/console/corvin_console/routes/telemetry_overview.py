"""Telemetry transparency — what this install collects and what it sends.

One read-only endpoint, ``GET /v1/console/telemetry/channels``, that reports
every outbound telemetry channel of CorvinOS FROM THAT CHANNEL'S OWN STATE:
the stamp files, outbox and sent directories the senders write, and the
opt-out flags the senders themselves read. Nothing here is a second log, and
nothing is estimated — a channel that leaves no trace reports "unknown".

Channels (compliance-baseline.md § Mechanisms):

* ``ping``            daily instance-count ping           ADR-0180 / ADR-0186
* ``heartbeat``       5-minute presence heartbeat         ADR-0186 (+ ADR-0212 features)
* ``healing_traces``  nightly healing-trace bundle        ADR-0180
* ``error_reports``   content-free error signatures       ADR-0179
* ``geo``             what geography leaves the machine   ADR-0205 / 0206 / 0208
* ``otlp_export``     OTEL exporter (ADR-0680/0681)       — no production caller
* ``stability``       feature-stability digest (ADR-0288) — no production caller

The payloads shown are what the sender would build NOW from the same inputs
(version, platform, engine id, feature snapshot, field allowlists); tokens
are reported as present/absent only and never returned.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from .. import _bootstrap
from .. import auth as session_auth
from ..deps import require_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telemetry", tags=["telemetry"])
_forge_paths = _bootstrap.forge_paths

#: How many of the newest outbox reports the signature summary reads.
_OUTBOX_SAMPLE = 500
#: Fields an error report carries per signature (aco/telemetry.py::_content_free).
_ERROR_REPORT_FIELDS = ["signature", "exc_type", "top_repo_file", "func", "frames", "count"]
_NEVER_TRANSMITTED = [
    "prompts, transcripts or chat text",
    "exception messages or log lines (only the exception class and repo frame)",
    "file contents, paths outside the repository, user names",
    "raw IP addresses (geography is resolved at the Cloudflare edge from the connection)",
    "tenant ids, session ids, audit records",
]


def _home() -> Path:
    return Path(_forge_paths.corvin_home())


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mtime(p: Path) -> Optional[float]:
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def _host(url: str) -> str:
    try:
        from urllib.parse import urlsplit

        return urlsplit(url).netloc
    except Exception:  # noqa: BLE001
        return url


def _cfg_flag(home: Path, key: str) -> Optional[bool]:
    """``spec.telemetry.<key>`` as the senders read it (fail-closed reader)."""
    try:
        from corvin_core.aco.htrace_consent import _read_telemetry_flag, _tenant_cfg_path

        return _read_telemetry_flag(_tenant_cfg_path(home), key)
    except Exception:  # noqa: BLE001
        return None


# ── channels ────────────────────────────────────────────────────────────────

def _ping_channel(home: Path) -> Dict[str, Any]:
    from corvin_core.aco import htrace_consent as hc
    from corvin_core.aco import htrace_uploader as hu

    enabled = hc.ping_enabled(home)
    stamp = hu._last_ping_path(home)
    last = _mtime(stamp)
    tele_dir = home / "aco" / "telemetry"
    body = {
        "corvin_version": hu._corvin_version(),
        "platform": sys.platform if sys.platform in hu._PING_ALLOWED_PLATFORMS else "other",
        "python_minor": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "active_engine": hu._detect_active_engine(home),
    }
    next_due = (last + hu._PING_INTERVAL_S) if last else None
    return {
        "id": "ping",
        "title": "Daily instance ping",
        "purpose": "Counts that this installation exists and is alive — one ping per 24 h.",
        "enabled": enabled,
        "opt_out": "spec.telemetry.ping_enabled: false in tenant.corvin.yaml",
        "legal_basis": "GDPR Art. 6(1)(f) legitimate interest",
        "cadence": "once per 24 h (checked hourly by the htrace.uploader fiber)",
        "endpoint": hu._PING_URL_DEFAULT,
        "endpoint_host": _host(hu._PING_URL_DEFAULT),
        "transport": "HTTPS POST, redirects refused, 8 s timeout",
        "identity": {
            "instance_id": hc.load_or_create_instance_id(home),
            "instance_token_present": hc._instance_token_path(home).exists(),
            "telemetry_token_present": hc._telemetry_token_path(home).exists(),
        },
        "headers": ["Authorization: Bearer <telemetry token>", "X-HTTrace-Instance-Token", "X-HTrace-Instance-Id", "User-Agent"],
        "payload": body,
        "payload_fields": sorted(hu._PING_BODY_ALLOWED_KEYS),
        "last_sent": _iso(last) if last else None,
        "next_due": _iso(next_due) if next_due else None,
        "status": ("disabled" if not enabled else "sent" if last else "never"),
        "state_file": str(stamp.relative_to(home)) if stamp.exists() else None,
        "artifacts": {"telemetry_dir": str(tele_dir.relative_to(home))},
    }


def _heartbeat_channel(home: Path) -> Dict[str, Any]:
    from corvin_core.aco import heartbeat as hb
    from corvin_core.aco import htrace_consent as hc

    enabled = hc.ping_enabled(home)  # same gate as the ping, by design (ADR-0186)
    st = hb.read_state(home)
    snapshot: Dict[str, Any] = {}
    snap_path = home / "telemetry" / "feature_snapshot.json"
    try:
        data = json.loads(snap_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            snapshot = data
    except (OSError, ValueError):
        pass
    tier = hc.effective_geo_tier(home)
    last_success = st.get("last_success")
    status = "disabled" if not enabled else ("sent" if last_success else ("failing" if st.get("attempts") else "unknown"))
    return {
        "id": "heartbeat",
        "title": "Presence heartbeat",
        "purpose": "Tells the maintainer stats that this instance is online; carries the feature snapshot.",
        "enabled": enabled,
        "opt_out": "spec.telemetry.ping_enabled: false (the same flag as the ping)",
        "legal_basis": "GDPR Art. 6(1)(f) legitimate interest",
        "cadence": f"every {hb._INTERVAL // 60} min ± {hb._JITTER} s, from the console host process",
        "endpoint": hb._HEARTBEAT_URL,
        "endpoint_host": _host(hb._HEARTBEAT_URL),
        "transport": f"HTTPS POST, redirects refused, {hb._HEARTBEAT_TIMEOUT_S} s timeout",
        "headers": ["Authorization: Bearer <telemetry token>", "X-HTTrace-Instance-Token", "X-HTrace-Instance-Id", "User-Agent"]
        + ([f"X-HTrace-Geo-Tier: {tier}"] if tier >= 2 else []),
        "payload": {"features": snapshot} if snapshot else {},
        "payload_fields": ["features.<name>"] ,
        "feature_fields": sorted(_known_features()),
        "thread_running_in_this_process": bool(getattr(hb, "_started", False)),
        "last_attempt": st.get("last_attempt"),
        "last_success": last_success,
        "last_detail": st.get("last_detail"),
        "attempts": int(st.get("attempts") or 0),
        "successes": int(st.get("successes") or 0),
        "consecutive_failures": int(st.get("consecutive_failures") or 0),
        "status": status,
        "state_file": str(hb.state_path(home).relative_to(home)) if hb.state_path(home).exists() else None,
        "note": None if st else "No outcome recorded yet — the state file is written from the first attempt after this build.",
    }


def _known_features() -> List[str]:
    try:
        from corvin_core.aco.feature_snapshot import _KNOWN_FEATURES

        return list(_KNOWN_FEATURES)
    except Exception:  # noqa: BLE001
        return []


def _healing_channel(home: Path) -> Dict[str, Any]:
    from corvin_core.aco import htrace, htrace_consent as hc, htrace_uploader as hu

    enabled = hc.healing_traces_enabled(home)
    d = htrace.htrace_dir(home)
    sent_dir = d / "sent"
    pending: List[Dict[str, Any]] = []
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            if p.suffix == ".jsonl":
                try:
                    n = sum(1 for line in p.open("r", encoding="utf-8") if line.strip())
                except OSError:
                    n = 0
                pending.append({"file": p.name, "records": n, "bytes": p.stat().st_size, "compressed": False})
            elif p.name.endswith(".jsonl.gz"):
                pending.append({"file": p.name, "records": None, "bytes": p.stat().st_size, "compressed": True})
    sent: List[Dict[str, Any]] = []
    if sent_dir.is_dir():
        for p in sorted(sent_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file():
                sent.append({"file": p.name, "bytes": p.stat().st_size, "sent_at": _iso(p.stat().st_mtime)})
    last_upload = None
    try:
        raw = (d / hu._LAST_UPLOAD_FILENAME).read_text(encoding="utf-8").strip()
        day, count = raw.split(",")
        last_upload = {"bundle_day": day, "bundles": int(count)}
    except (OSError, ValueError):
        pass
    sample = None
    today = d / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    src = today if today.exists() else next((d / e["file"] for e in pending if not e["compressed"]), None)
    if src is not None:
        try:
            lines = [ln for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                sample = json.loads(lines[-1])
                sample.pop("instance_token", None)  # pseudonym, not for display
        except (OSError, ValueError):
            sample = None
    allowlist = sorted(getattr(htrace, "HTRACE_FIELD_ALLOWLIST", ()))
    return {
        "id": "healing_traces",
        "title": "Healing traces",
        "purpose": "What the self-healing layer did (action, outcome, code-level signature) — one nightly bundle.",
        "enabled": enabled,
        "opt_out": "spec.telemetry.healing_traces: false in tenant.corvin.yaml",
        "legal_basis": "GDPR Art. 6(1)(f) legitimate interest",
        "cadence": f"nightly, at most {hu._MAX_BUNDLES_PER_DAY} bundles per day, {hu._MAX_BUNDLE_BYTES // (1024 * 1024)} MB max each",
        "endpoint": hu._UPLOAD_URL_DEFAULT,
        "endpoint_host": _host(hu._UPLOAD_URL_DEFAULT),
        "transport": f"HTTPS POST gzip bundle, redirects refused, {hu._UPLOAD_TIMEOUT_S} s timeout",
        "payload_fields": allowlist,
        "sample_record": sample,
        "local_dir": str(d.relative_to(home)),
        "retention_days": htrace._MAX_RETAIN_DAYS,
        "pending": pending,
        "pending_records": sum(int(e["records"] or 0) for e in pending),
        "sent_bundles": len(sent),
        "sent": sent[:14],
        "last_upload": last_upload,
        "last_sent": sent[0]["sent_at"] if sent else None,
        "status": ("disabled" if not enabled else "sent" if sent else "never"),
    }


def _error_reports_channel(home: Path) -> Dict[str, Any]:
    from corvin_core.aco import telemetry as tel

    enabled = tel.consent_granted(home)
    root = tel._tele_root(home)
    outbox = root / "outbox"
    sent_dir = root / "sent"
    names: List[str] = []
    total_bytes = 0
    if outbox.is_dir():
        with os.scandir(outbox) as it:
            for e in it:
                if e.is_file() and e.name.startswith("report-") and e.name.endswith(".json"):
                    names.append(e.name)
                    try:
                        total_bytes += e.stat().st_size
                    except OSError:
                        pass
    names.sort()
    stamps = []
    for n in names:
        try:
            stamps.append(int(n[7:-5]))
        except ValueError:
            continue
    top: Counter = Counter()
    sampled = 0
    for n in names[-_OUTBOX_SAMPLE:]:
        try:
            rep = json.loads((outbox / n).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sampled += 1
        for sig in rep.get("signatures") or []:
            if isinstance(sig, dict):
                top[(str(sig.get("exc_type")), str(sig.get("top_repo_file")), str(sig.get("func")))] += int(sig.get("count") or 1)
    sent_count = sum(1 for p in sent_dir.glob("report-*.json")) if sent_dir.is_dir() else 0
    intake = os.environ.get("CORVIN_TELEMETRY_URL", "").strip()
    if not enabled:
        status = "disabled"
    elif sent_count:
        status = "sent"
    elif names:
        status = "collected_never_sent"
    else:
        status = "never"
    return {
        "id": "error_reports",
        "title": "Error signatures",
        "purpose": "Which exceptions occur where in the code (class + repo frame), counted — never the message.",
        "enabled": enabled,
        "opt_out": "CORVIN_TELEMETRY_OPTIN=false, or spec.telemetry.error_traces: false, or consent.json opted_in: false",
        "legal_basis": "GDPR Art. 6(1)(f) legitimate interest",
        "cadence": "written by the boot healer at every boot; submitted only by an explicit submit() call",
        "endpoint": intake or None,
        "endpoint_host": _host(intake) if intake else None,
        "intake_configured": bool(intake),
        "transport": "HTTPS POST JSON, redirects refused, 15 s timeout",
        "payload_fields": _ERROR_REPORT_FIELDS,
        "outbox": {
            "reports": len(names),
            "bytes": total_bytes,
            "oldest": _iso(min(stamps)) if stamps else None,
            "newest": _iso(max(stamps)) if stamps else None,
            "dir": str(outbox.relative_to(home)),
        },
        "top_signatures": [
            {"exc_type": k[0], "top_repo_file": k[1], "func": k[2], "count": v} for k, v in top.most_common(10)
        ],
        "signatures_sampled_from_newest": sampled,
        "sent_reports": sent_count,
        "status": status,
        "note": (
            None if status != "collected_never_sent" else
            "Reports accumulate in the outbox but nothing submits them: no intake URL is configured "
            "(CORVIN_TELEMETRY_URL) and no scheduler on this build calls submit()."
        ),
    }


def _geo_channel(home: Path) -> Dict[str, Any]:
    from corvin_core.aco import htrace_consent as hc

    configured = hc.geo_tracking_tier(home)
    consent = hc.geo_tracking_consent_given(home)
    effective = hc.effective_geo_tier(home)
    names = {1: "country", 2: "region", 3: "city (10 km grid)"}
    return {
        "id": "geo",
        "title": "Geography",
        "purpose": "Where installs are, for the public stats map — resolved at the Cloudflare edge, never from a raw IP in the payload.",
        "enabled": True,
        "opt_out": "spec.telemetry.geo_tracking_tier: 1 (country only) or CORVIN_GEO_OPT_OUT=1",
        "legal_basis": "GDPR Art. 6(1)(f) legitimate interest",
        "configured_tier": configured,
        "consent_flag": consent,
        "effective_tier": effective,
        "effective_tier_name": names.get(effective, str(effective)),
        "what_leaves": (
            "Nothing extra: the edge sees the connection's country like any HTTPS request."
            if effective < 2 else
            f"The header X-HTrace-Geo-Tier: {effective} on ping and heartbeat, so the edge attaches {names[effective]}."
        ),
        "status": "sent",
        "carried_by": ["ping", "heartbeat"],
    }


def _otlp_channel() -> Dict[str, Any]:
    try:
        from core.observability.otel_exporter import exporter as ex

        sdk = bool(getattr(ex, "OTEL_AVAILABLE", False))
    except Exception:  # noqa: BLE001
        sdk = False
    return {
        "id": "otlp_export",
        "title": "OTLP export",
        "purpose": "OpenTelemetry metrics export (heartbeat gauge, geo resource attributes).",
        "enabled": False,
        "sdk_installed": sdk,
        "wired": False,
        "status": "not_wired",
        "note": "OTELExporter has no production caller on this build; nothing is exported over OTLP.",
    }


def _stability_channel() -> Dict[str, Any]:
    running = False
    try:
        from core.telemetry import telemetry_daemon as td

        d = td.get_daemon()
        running = bool(d and getattr(d, "enabled", False) and getattr(d, "_task", None))
    except Exception:  # noqa: BLE001
        pass
    return {
        "id": "stability",
        "title": "Feature-stability digest",
        "purpose": "Hourly digest of feature-flag stability.",
        "enabled": running,
        "wired": running,
        "status": "sent" if running else "not_wired",
        "note": None if running else "The stability daemon is never initialised by the console host; nothing is sent.",
    }


@router.get("/channels", summary="Every outbound telemetry channel: what it collects, what it sends, when it last did")
async def get_channels(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    home = _home()
    t0 = time.monotonic()
    channels: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}
    for name, fn in (
        ("ping", lambda: _ping_channel(home)),
        ("heartbeat", lambda: _heartbeat_channel(home)),
        ("healing_traces", lambda: _healing_channel(home)),
        ("error_reports", lambda: _error_reports_channel(home)),
        ("geo", lambda: _geo_channel(home)),
        ("otlp_export", _otlp_channel),
        ("stability", _stability_channel),
    ):
        try:
            channels.append(fn())
        except Exception as exc:  # noqa: BLE001 — one broken channel must not hide the others
            logger.warning("telemetry channel %s unavailable: %s", name, type(exc).__name__)
            errors[name] = type(exc).__name__
            channels.append({"id": name, "title": name, "status": "unavailable", "enabled": None, "note": f"could not be read: {type(exc).__name__}"})
    return {
        "tenant_id": rec.tenant_id,
        "corvin_home": str(home),
        "channels": channels,
        "never_transmitted": _NEVER_TRANSMITTED,
        "errors": errors,
        "took_ms": round((time.monotonic() - t0) * 1000),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
