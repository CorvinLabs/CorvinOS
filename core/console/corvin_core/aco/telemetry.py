"""ACO — default-ON (opt-out) error telemetry channel (ADR-0179 / ADR-0180).

How a FOREIGN user's machine — which can NOT fix its own code (no maintainer
capability) — still gets bugs fixed: it ships *scrubbed error signatures* to the
maintainer, who synthesizes a diagnosis, proves+fixes it, releases a new PyPI
version, and the fix returns to every machine via ``pip``.

Hard privacy guarantees (GDPR / CLAUDE.md compliance baseline):
  * **Default-ON, opt-OUT (maintainer decision).** This channel ships by default
    so Corvin-Logs gets real crash data across installs; it is disabled by an
    explicit opt-out (``CORVIN_TELEMETRY_OPTIN=false``, a ``consent.json`` with
    ``{"opted_in": false}``, or ``spec.telemetry.error_traces: false``) — see
    ``consent_granted``. Legal basis: GDPR Art. 6(1)(f) legitimate interest. The
    load-bearing safety invariant is NOT consent-gating but that everything sent
    stays strictly CONTENT-FREE (below); an opt-out always wins.
  * **Signatures only, never content.** The payload is the output of
    ``error_signature`` — exception type, repo-relative frames, and a scrubbed
    message TEMPLATE. Never a prompt, transcript, name, email, token, path, or
    raw log line. ``_assert_safe`` re-checks every report before it is written.
  * **Pseudonymous.** A report is tagged with a caller-chosen pseudonym, never a
    raw user/instance identifier.
  * **Egress-bounded.** Submission targets exactly one configured intake URL
    (must be on the L35 allowlist); the HTTP call is injectable for testing.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from .error_signature import parse_tracebacks, scrub

_SCHEMA = "aco.telemetry/1"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _tele_root(home: Path) -> Path:
    return Path(home) / "aco" / "telemetry"


def consent_path(home: Path) -> Path:
    return _tele_root(home) / "consent.json"


def consent_granted(home: str | Path) -> bool:
    """Default-ON (opt-OUT). Healing/error telemetry is enabled unless explicitly
    disabled. Maintainer decision — the aggregation backend (Corvin-Logs) needs
    real crash data by default to fix bugs across installs.

    Safety: this channel ships ONLY scrubbed, CONTENT-FREE error signatures. The
    payload is projected by ``_content_free`` (code-level fields: signature hash,
    exc_type, repo file, func, allowlisted stack namespaces — never prompts or
    user data) and re-checked by the FAIL-CLOSED ``_assert_safe`` backstop, which
    DROPS any record carrying a PII/secret shape rather than sending it. So
    default-ON transmits only anonymous code diagnostics. Legal basis: GDPR
    Art. 6(1)(f) legitimate interest.

    Opt out: env ``CORVIN_TELEMETRY_OPTIN=false`` OR a consent file with
    ``{"opted_in": false}`` OR ``spec.telemetry.error_traces: false``. An explicit
    opt-out ALWAYS wins — including over a legacy ``CORVIN_TELEMETRY_OPTIN=1``
    env opt-in (F3). Any other state (incl. no file) → ON.
    """
    home_p = Path(home)
    env = os.environ.get("CORVIN_TELEMETRY_OPTIN", "").strip().lower()

    # 1) Explicit opt-out artifacts take precedence over EVERYTHING — including a
    #    stale/legacy CORVIN_TELEMETRY_OPTIN=1 env opt-in (F3). An opt-out the user
    #    or operator set must never be silently overridden by an env var.
    if _yaml_error_optout(home_p):  # spec.telemetry.error_traces: false (Settings toggle)
        return False
    if _consent_file_opted_in(home_p) is False:  # consent.json {"opted_in": false}
        return False

    # 2) Env var: opt-out or opt-in (opt-in is only reachable here because no
    #    explicit artifact opted out above).
    if env in _FALSE:
        return False
    if env in _TRUE:
        return True

    # 3) Default-ON (opt-out): nothing said otherwise.
    return True


def _consent_file_opted_in(home: Path) -> Optional[bool]:
    """Read consent.json's ``opted_in`` flag.

    Returns ``None`` if no consent file exists, ``True`` if opted in (or the flag
    is absent but the file parses), ``False`` if the flag is an explicit false OR
    the file exists but cannot be parsed (fail toward opt-OUT — a broken consent
    artifact must not resume transmission)."""
    p = consent_path(home)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("opted_in") is not False
    except (OSError, json.JSONDecodeError):
        return False  # exists but broken → treat as opted OUT (fail-closed)


def _yaml_error_optout(home: Path) -> bool:
    """True when spec.telemetry.error_traces is an explicit false / false-like in
    tenant.corvin.yaml — the console Settings opt-out for the error channel.

    A config that EXISTS but is unreadable/unparseable is treated as an opt-out
    (fail toward privacy): default-ON applies only to an ABSENT config, never a
    BROKEN one (F4). Delegates to the shared fail-closed parser."""
    try:
        # Shared resolver + fail-closed parser (ADR-0007 / F4).
        from .htrace_consent import _tenant_cfg_path, _read_telemetry_flag
        flag = _read_telemetry_flag(_tenant_cfg_path(home), "error_traces")
    except Exception:  # noqa: BLE001
        return False
    return flag is False


def grant_consent(home: str | Path, *, pseudonym: str = "anon") -> Path:
    """Record explicit opt-in. ``pseudonym`` is a non-PII label the user controls."""
    p = consent_path(Path(home))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"opted_in": True, "pseudonym": _safe_pseudonym(pseudonym)}),
                 encoding="utf-8")
    return p


def revoke_consent(home: str | Path) -> None:
    p = consent_path(Path(home))
    try:
        p.write_text(json.dumps({"opted_in": False}), encoding="utf-8")
    except OSError:
        pass


def _safe_pseudonym(name: str) -> str:
    # never let a raw email/id become the pseudonym
    return scrub(re.sub(r"[^A-Za-z0-9_.-]", "", str(name)))[:40] or "anon"


def _pseudonym(home: Path) -> str:
    try:
        return _safe_pseudonym(json.loads(consent_path(home).read_text(
            encoding="utf-8")).get("pseudonym", "anon"))
    except (OSError, json.JSONDecodeError):
        return "anon"


def _corvin_version() -> str:
    try:
        from importlib.metadata import version
        return version("corvinOS")
    except Exception:  # noqa: BLE001
        return "unknown"


# ── safety re-check before anything is written/sent ─────────────────────────────
_LEAK = re.compile(
    r"@|~/|\\\\|/home/|/Users/|/root/|[A-Za-z]:\\|"           # emails, home/UNC/drive paths
    r"\beyJ[A-Za-z0-9_-]{6,}\.|"                              # JWT
    r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[baprs]|AKIA|ASIA)[_-]|"   # token prefixes
    r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b|"              # MAC
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b|"                           # IPv4 dotted quad
    r"\b[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){2,}\b|::\b|"    # IPv6 (incl. compressed)
    r"\b[0-9a-fA-F]{16,}\b|\b\d{12,}\b|"                      # long hex / id
    r"(?<![\w.:/-])(?:\+\d{1,3}|0\d{1,4})(?:[\s./()-]{0,3}\d){6,13}(?![\w-])|"  # intl/trunk phone
    r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}\s?[A-Z0-9]{1,4}\b")  # IBAN
# Free text: four or more whitespace-separated word tokens is prose (a prompt,
# a transcript, a user sentence), never a code-level signature. Fail-closed.
_FREE_TEXT = re.compile(r"(?:[A-Za-z][A-Za-z'’,.!?-]*\s+){3,}[A-Za-z]")
# NOTE: this is a FAIL-CLOSED backstop — a match DROPS the record (never sends),
# so over-matching (e.g. an IPv6-shaped hash) is safe: worst case a benign record
# is withheld, never a leak. Airtight enough to make the default-ON telemetry
# (opt-out) transmit only content-free crash diagnostics.


_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _scan_values(obj: Any, out: list[str]) -> None:
    """Collect every free-text string — both dict KEYS and values, recursively.
    The ``signature`` field is allowed to be a 16-hex hash (validated, not blindly
    trusted): a non-hex 'signature' is scanned like anything else, so the backstop
    can never be bypassed by stuffing content into that field."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))                       # scan KEYS too
            if k == "signature" and isinstance(v, str) and _HEX16.match(v):
                continue                             # genuine hash → safe
            _scan_values(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _scan_values(v, out)
    elif isinstance(obj, str):
        out.append(obj)


def _assert_safe(report: dict) -> None:
    """Defensive: re-scan every key + value for any PII/secret shape that slipped
    through. Raises if found — fail closed, never transmit a leak."""
    vals: list[str] = []
    _scan_values(report, vals)
    for v in vals:
        m = _LEAK.search(v)
        if m:
            raise ValueError(f"telemetry report failed safety re-check near {m.group(0)!r}")
        if _FREE_TEXT.search(v):
            raise ValueError("telemetry report failed safety re-check: free text (>=4 words)")


def _content_free(sig) -> dict:
    """Project a signature to a CONTENT-FREE telemetry record: the message
    template — the one free-text field that could carry PII/secrets — is NOT
    transmitted. Only the structural hash, the exception class name, and
    repo-relative frames (no user paths by construction) leave the machine."""
    return {"signature": sig.signature, "exc_type": sig.exc_type,
            "top_repo_file": sig.top_repo_file, "func": sig.func, "frames": sig.frames}


def collect_local(home: str | Path) -> Optional[dict]:
    """Build a content-free telemetry report from local logs, or None if no
    consent / nothing to report. Only LOCALIZED tracebacks (a repo frame) are
    included — bare ``ERROR`` log lines (which routinely contain user text) are
    never transmitted. Carries no message text at all."""
    home = Path(home)
    if not consent_granted(home):
        return None
    counts: dict[str, dict] = {}
    logs = home / "logs"
    for p in sorted(logs.glob("corvin.log*")) if logs.is_dir() else []:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for sig in parse_tracebacks(text):       # tracebacks only, never bare ERROR lines
            if not sig.localized:                # un-localizable → not transmittable
                continue
            slot = counts.setdefault(sig.signature, {"d": _content_free(sig), "count": 0})
            slot["count"] += 1
    if not counts:
        return None
    sigs = [{**slot["d"], "count": slot["count"]} for slot in counts.values()]
    report = {"schema": _SCHEMA, "instance": _pseudonym(home),
              "corvin_version": _corvin_version(), "signatures": sigs}
    _assert_safe(report)
    return report


def write_outbox(home: str | Path, report: dict, *, stamp: str) -> Optional[Path]:
    """Persist a report to the outbox (awaiting submission). No-op without consent
    (defense-in-depth: never stage a report for sending on a non-opted-in machine,
    regardless of caller). ``stamp`` is passed in by the caller."""
    home = Path(home)
    if not consent_granted(home):
        return None
    _assert_safe(report)
    out = _tele_root(home) / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    fp = out / f"report-{stamp}.json"
    fp.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return fp


def _default_http() -> Callable[[str, dict], tuple[bool, str]]:
    class _NoRedirect(__import__("urllib.request", fromlist=["HTTPRedirectHandler"]).HTTPRedirectHandler):
        # Don't follow 3xx — a redirect could bounce telemetry to an unintended
        # host. Submission targets exactly the one configured intake URL.
        def redirect_request(self, *a, **k):  # noqa: D401
            return None

    def _post(url: str, payload: dict) -> tuple[bool, str]:
        import urllib.request
        if not url.lower().startswith("https://"):
            return (False, "intake URL must be https://")
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=15) as resp:  # noqa: S310 — fixed https intake URL, no redirects
                return (200 <= resp.status < 300, f"http {resp.status}")
        except Exception as exc:  # noqa: BLE001
            return (False, str(exc)[:120])
    return _post


def submit(home: str | Path, *, url: Optional[str] = None,
           http: Optional[Callable[[str, dict], tuple[bool, str]]] = None) -> dict:
    """Submit every queued outbox report to the maintainer intake URL. No-op
    without consent or without a URL. On success the report moves to ``sent/``."""
    home = Path(home)
    if not consent_granted(home):
        return {"sent": 0, "reason": "no consent"}
    url = url or os.environ.get("CORVIN_TELEMETRY_URL", "").strip()
    if not url:
        return {"sent": 0, "reason": "no intake URL configured"}
    http = http or _default_http()
    outbox = _tele_root(home) / "outbox"
    sent_dir = _tele_root(home) / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    sent, failed = 0, 0
    for fp in sorted(outbox.glob("report-*.json")) if outbox.is_dir() else []:
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
            _assert_safe(payload)            # never transmit an unsafe report
        except (OSError, json.JSONDecodeError, ValueError):
            failed += 1
            continue
        ok, _detail = http(url, payload)
        if ok:
            fp.rename(sent_dir / fp.name)
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed, "url": url}


# ── maintainer side ─────────────────────────────────────────────────────────────

def ingest_inbox(inbox_dir: str | Path) -> list[dict]:
    """Maintainer side: read received telemetry reports from a directory and
    return a flat list of signature dicts (with counts + instance) ready to feed
    ``diagnosis_synth.synthesize(telemetry_sigs=...)``. The transport that FILLS
    the inbox (HTTP intake server or rsync) is operator infra; this is the
    consumer."""
    inbox = Path(inbox_dir)
    out: list[dict] = []
    for fp in sorted(inbox.glob("*.json")) if inbox.is_dir() else []:
        try:
            rep = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if rep.get("schema") != _SCHEMA:
            continue
        inst = rep.get("instance", "anon")
        for s in rep.get("signatures", []):
            if isinstance(s, dict) and s.get("signature"):
                out.append({**s, "instance": inst})
    return out


# ── batched submission to the maintainer intake (2026-09-20) ───────────────────
#
# ``submit`` posts one HTTP request per outbox file and only when
# ``CORVIN_TELEMETRY_URL`` is set — nothing in production ever set it or called
# it, so 10 164 reports (one per boot since 2026-07-26, all the same
# signature) piled up on the maintainer host. ``submit_batch`` folds the whole
# outbox into ONE content-free payload — the signatures with summed counts,
# how many reports were merged and the span they cover — and posts it to the
# Corvin-Features intake ``/v1/telemetry/error-signatures`` with the same HMAC
# token pair as the healing traces. What was merged is kept as one file under
# ``sent/`` and the consumed reports are removed; ``error_reports_state.json``
# records the outcome for the console.

_BATCH_MAX_SIGNATURES = 500
_SUBMIT_INTERVAL_S = 3600
_LAST_SUBMIT_FILENAME = ".last_error_submit"
_STATE_FILENAME = "error_reports_state.json"


def intake_url() -> str:
    """``CORVIN_TELEMETRY_URL`` if set, else the Corvin-Features intake."""
    url = os.environ.get("CORVIN_TELEMETRY_URL", "").strip()
    if url:
        return url
    from .htrace_uploader import _TELEMETRY_BASE  # noqa: PLC0415 — sibling module, no cycle at import time

    return f"{_TELEMETRY_BASE}/v1/telemetry/error-signatures"


def state_path(home: str | Path) -> Path:
    return _tele_root(Path(home)) / _STATE_FILENAME


def read_state(home: str | Path) -> dict:
    try:
        data = json.loads(state_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(home: Path, **fields: Any) -> None:
    try:
        st = read_state(home)
        st.update(fields)
        p = state_path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, sort_keys=True), encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001 — state must never break the send
        pass


def _stamp_iso(stamp: int) -> str:
    import datetime as _dt  # noqa: PLC0415

    return _dt.datetime.fromtimestamp(stamp, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_batch(home: str | Path) -> tuple[Optional[dict], list[Path]]:
    """Fold every outbox report into one payload. ``(payload, consumed_files)``;
    ``(None, [])`` when the outbox is empty. Every merged report is re-checked
    with ``_assert_safe`` and a report that fails it is skipped, not merged."""
    home = Path(home)
    outbox = _tele_root(home) / "outbox"
    files = sorted(outbox.glob("report-*.json")) if outbox.is_dir() else []
    merged: dict[str, dict] = {}
    consumed: list[Path] = []
    stamps: list[int] = []
    version = _corvin_version()
    for fp in files:
        try:
            rep = json.loads(fp.read_text(encoding="utf-8"))
            _assert_safe(rep)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        for sig in rep.get("signatures") or []:
            if not isinstance(sig, dict) or not sig.get("signature"):
                continue
            slot = merged.setdefault(str(sig["signature"]), {
                "signature": str(sig["signature"]), "exc_type": str(sig.get("exc_type", "")),
                "top_repo_file": str(sig.get("top_repo_file", "")), "func": str(sig.get("func", "")),
                "frames": list(sig.get("frames") or [])[:16], "count": 0,
            })
            slot["count"] += int(sig.get("count") or 1)
        if rep.get("corvin_version") and rep["corvin_version"] != "unknown":
            version = str(rep["corvin_version"])
        consumed.append(fp)
        try:
            stamps.append(int(fp.stem.split("-", 1)[1]))
        except (IndexError, ValueError):
            pass
    if not merged:
        return None, []
    top = sorted(merged.values(), key=lambda s: -s["count"])[:_BATCH_MAX_SIGNATURES]
    payload: dict[str, Any] = {
        "schema": _SCHEMA, "instance": _pseudonym(home), "corvin_version": version,
        "signatures": top, "reports_merged": len(consumed),
    }
    if stamps:
        payload["span"] = {"from": _stamp_iso(min(stamps)), "to": _stamp_iso(max(stamps))}
    _assert_safe(payload)
    return payload, consumed


def _auth_http() -> Callable[[str, dict], tuple[bool, str]]:
    """HTTPS POST with the instance's HMAC token pair (the healing-trace
    credentials), redirects refused, 15 s timeout. ``(ok, "http <status>")``."""

    def _post(url: str, payload: dict) -> tuple[bool, str]:
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        from .htrace_consent import _open_no_redirect, load_or_create_instance_id  # noqa: PLC0415
        from .htrace_uploader import _load_instance_token, _load_telemetry_token, _telemetry_user_agent  # noqa: PLC0415

        home = _home_for_tokens()
        if home is None:
            return (False, "no corvin home")
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_load_telemetry_token(home)}",
            "X-HTTrace-Instance-Token": _load_instance_token(home),
            "X-HTrace-Instance-Id": load_or_create_instance_id(home),
            "User-Agent": _telemetry_user_agent("ErrorSignatures"),
        })
        try:
            with _open_no_redirect(req, 15) as resp:
                status = int(resp.getcode())
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
        except Exception as exc:  # noqa: BLE001
            return (False, type(exc).__name__)
        return (200 <= status < 300, f"http {status}")

    return _post


def _home_for_tokens() -> Optional[Path]:
    try:
        from forge import paths as _p  # type: ignore[import]  # noqa: PLC0415

        return Path(_p.corvin_home())
    except Exception:  # noqa: BLE001
        return None


def submit_batch(home: str | Path, *, url: Optional[str] = None,
                 http: Optional[Callable[[str, dict], tuple[bool, str]]] = None) -> dict:
    """Fold the outbox into one payload and post it. On success the merged
    payload is kept as ``sent/batch-<stamp>.json`` and the consumed reports
    are deleted; on failure nothing moves. Never raises."""
    home = Path(home)
    now = _stamp_iso(int(__import__("time").time()))
    if not consent_granted(home):
        return {"sent": 0, "reason": "no consent"}
    payload, consumed = build_batch(home)
    if payload is None:
        _write_state(home, last_attempt=now, last_detail="outbox empty", outbox_empty=True)
        return {"sent": 0, "reason": "outbox empty"}
    url = url or intake_url()
    http = http or _auth_http()
    ok, detail = http(url, payload)
    _write_state(home, last_attempt=now, last_detail=detail[:64], url=url,
                 attempts=int(read_state(home).get("attempts") or 0) + 1)
    if not ok:
        _write_state(home, consecutive_failures=int(read_state(home).get("consecutive_failures") or 0) + 1)
        return {"sent": 0, "failed": len(consumed), "reason": detail, "url": url}
    sent_dir = _tele_root(home) / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(__import__("time").time())
    (sent_dir / f"batch-{stamp}.json").write_text(json.dumps(
        {"sent_at": now, "url": url, "reports_merged": len(consumed), "payload": payload}, ensure_ascii=False,
    ), encoding="utf-8")
    for fp in consumed:
        try:
            fp.unlink()
        except OSError:
            pass
    _write_state(home, last_success=now, consecutive_failures=0,
                 batches=int(read_state(home).get("batches") or 0) + 1,
                 reports_sent=int(read_state(home).get("reports_sent") or 0) + len(consumed),
                 last_batch={"reports_merged": len(consumed), "signatures": len(payload["signatures"])})
    return {"sent": len(consumed), "signatures": len(payload["signatures"]), "url": url}


def submit_if_due(home: str | Path) -> bool:
    """Hourly, from the ACO ``htrace.uploader`` fiber. Returns True when a
    batch was posted (or nothing was due)."""
    home = Path(home)
    try:
        if not consent_granted(home):
            return False
        stamp = _tele_root(home) / _LAST_SUBMIT_FILENAME
        try:
            if stamp.exists():
                import time as _t  # noqa: PLC0415

                age = _t.time() - stamp.stat().st_mtime
                if 0 <= age < _SUBMIT_INTERVAL_S:
                    return True
        except OSError:
            pass
        result = submit_batch(home)
        if result.get("sent") or result.get("reason") == "outbox empty":
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(str(int(__import__("time").time())), encoding="utf-8")
            return True
        return False
    except Exception:  # noqa: BLE001 — fail-soft, like the ping
        return False

