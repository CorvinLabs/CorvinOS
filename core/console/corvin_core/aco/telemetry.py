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
    r"\b[0-9a-fA-F]{16,}\b|\b\d{12,}\b")                      # long hex / id
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
