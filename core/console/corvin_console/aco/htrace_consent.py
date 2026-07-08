"""HealingTrace consent management (ADR-0180 §5).

Implements the GDPR Art. 7-compliant consent flow: timestamped ConsentAct
with the exact text SHA-256, stored in the L16 audit chain.

The YAML flag (telemetry.healing_traces: true) is the operator-level gate.
The ConsentAct is the user-level gate. BOTH must be present for uploads.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import sys
import tempfile
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# fcntl is POSIX-only (absent on Windows). Conditional import keeps this module
# importable everywhere; file locking is skipped when absent (single-instance
# Windows installs are not affected by the cross-process token-pair race).
try:
    import fcntl as _fcntl
    _HAS_FLOCK = True
except ImportError:  # pragma: no cover — Windows path
    _HAS_FLOCK = False

logger = logging.getLogger(__name__)

# Must match htrace_uploader._PING_LOCK_FILENAME — the same on-disk lock file is
# shared so ping_if_due()'s lock and a direct token provision serialize together.
_PING_LOCK_FILENAME = ".ping.lock"

# False-like string forms accepted for every telemetry opt-out flag.
_FALSE_LIKE = ("false", "no", "0", "off")

_CONSENT_VERSION = "htrace/1.1"
# Base URL for all telemetry endpoints. Overridable via CORVIN_TELEMETRY_BASE_URL.
# Default: Railway deployment (the only host with a valid DNS record for the
# public API). api.corvin-labs.com has no CNAME yet; set CORVIN_TELEMETRY_BASE_URL
# to that value once the Cloudflare record exists and Railway can be the backend.
_TELEMETRY_BASE = os.environ.get(
    "CORVIN_TELEMETRY_BASE_URL", "https://corvin-features-production.up.railway.app"
).rstrip("/")
_TOKEN_ENDPOINT = f"{_TELEMETRY_BASE}/v1/telemetry/token"
_TOKEN_TIMEOUT_S = 15
_CONSENT_TEXT_FILE = Path(__file__).parent / "consent_texts" / "htrace-1.0.txt"

# SHA-256 of the exact consent text shipped in htrace-1.0.txt, pinned at
# development time.  Any change to the text requires bumping _CONSENT_VERSION
# AND updating this constant (compute: sha256sum htrace-1.0.txt).
# Pinning prevents a tampered consent file from silently passing is_text_intact:
# _consent_text_sha256() now returns the pinned value, not the file's live hash.
_CONSENT_TEXT_SHA256_PINNED = (
    "559b776af1ab0697e01cc0888150a309d38ef6154af32bc125c88b9cc3c23a03"
)


def _consent_text() -> str:
    return _CONSENT_TEXT_FILE.read_text(encoding="utf-8")


def _consent_text_sha256() -> str:
    """Return the pinned SHA-256 of the shipped consent text.

    Using the pinned value (not a live file hash) means a tampered htrace-1.0.txt
    cannot make is_text_intact() return True — the stored act.text_sha256 would
    match the pinned value only if the user consented to the original text.
    """
    return _CONSENT_TEXT_SHA256_PINNED


# ── ConsentAct ────────────────────────────────────────────────────────────────

@dataclass
class ConsentAct:
    """GDPR Art. 7 consent record — the single source of truth for 'was consent given?'

    Stored locally in the L16 audit chain AND sent to the server for Art. 7
    accountability. The text_sha256 pins the exact text that was shown.
    """
    consent_act_id: str
    consent_version: str
    ts_utc: str
    text_sha256: str
    method: str          # "cli" | "web_ui"
    corvin_version: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, home: Path) -> Optional["ConsentAct"]:
        """Load the stored ConsentAct or None if not present / malformed."""
        p = _consent_act_path(home)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return cls(**d)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def save(self, home: Path) -> None:
        p = _consent_act_path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        p.chmod(0o600)
        # Provision the stateless HMAC upload tokens now that consent exists.
        # Fail-soft: a network error must not undo the consent that was saved.
        try:
            instance_id = load_or_create_instance_id(home)
            provision_telemetry_tokens(home, instance_id)
        except Exception:  # noqa: BLE001
            logger.debug("htrace: token provisioning skipped (non-fatal)")

    @property
    def is_current_version(self) -> bool:
        return self.consent_version == _CONSENT_VERSION

    @property
    def is_text_intact(self) -> bool:
        """True only if BOTH the live consent file AND the stored consent hash
        match the pinned original.

        - live file vs pinned   → detects tampering of the shipped consent text
          (the intent of the pinning; the previous comparison was tautological —
          it compared the pinned constant to itself and was always True).
        - stored vs pinned       → detects a tampered / stale ConsentAct record.

        Fail-closed: any read error on the consent file → False.
        """
        try:
            live = hashlib.sha256(_consent_text().encode("utf-8")).hexdigest()
        except Exception:  # noqa: BLE001
            return False
        return (
            hmac.compare_digest(live, _CONSENT_TEXT_SHA256_PINNED)
            and hmac.compare_digest(self.text_sha256, _CONSENT_TEXT_SHA256_PINNED)
        )


def _consent_act_path(home: Path) -> Path:
    return home / "aco" / "telemetry" / "htrace-consent-act.json"


def _tenant_cfg_path(home: Path) -> Path:
    """Resolve ``<corvin_home>/tenants/<tid>/global/tenant.corvin.yaml`` (ADR-0007).

    The previous ``home.parent.parent / "global"`` derivation resolved to
    ``/home/global/tenant.corvin.yaml`` — a path that never exists — which made
    the YAML opt-in gate always fail and silently killed the whole telemetry
    pipeline.  The tenant id comes from the canonical forge resolver (env-aware,
    defaults to ``_default``); the path is built relative to the supplied
    ``home`` so the same helper works under a test tmp-home.
    """
    try:
        from forge.paths import _resolve_tenant_id  # noqa: PLC0415
        tid = _resolve_tenant_id(None)
    except Exception:  # noqa: BLE001
        tid = "_default"
    return home / "tenants" / tid / "global" / "tenant.corvin.yaml"


# ── Shared HTTP hardening + config parsing helpers ────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Don't follow 3xx — a redirect could bounce an authenticated telemetry POST
    (its Authorization + instance-token headers) to an unintended host."""

    def redirect_request(self, *a, **k):  # noqa: D401
        return None


def _open_no_redirect(req: urllib.request.Request, timeout: float):
    """urlopen with a non-redirecting opener + https-only guard. Raises
    ValueError if the target URL is not ``https://`` so credentials are never
    forwarded over plaintext or across a cross-host redirect (F8)."""
    url = str(req.full_url)
    if not url.lower().startswith("https://"):
        raise ValueError("telemetry URL must be https://")
    opener = urllib.request.build_opener(_NoRedirect)
    return opener.open(req, timeout=timeout)


def _ping_lock_path(home: Path) -> Path:
    from .htrace import htrace_dir  # local import avoids a circular dependency
    return htrace_dir(home) / _PING_LOCK_FILENAME


def _read_telemetry_flag(cfg_path: Path, key: str) -> Optional[bool]:
    """Read ``spec.telemetry.<key>`` from tenant.corvin.yaml with FAIL-CLOSED
    parsing.

    Returns:
      * ``None``  — config file ABSENT (caller applies default-ON / opt-out).
      * ``False`` — flag is an explicit ``false`` / false-like string → opted OUT.
      * ``True``  — flag is truthy, the key is missing, or unset → opted IN.
      * ``False`` — config file EXISTS but is unreadable / unparseable / not a
        mapping. Default-ON applies ONLY to an ABSENT config, never a BROKEN one
        (F4): a config we cannot trust fails toward the privacy-preserving state.
    """
    if not cfg_path.exists():
        return None
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError:
        return False  # exists but unreadable → fail toward privacy (opted OUT)
    try:
        import yaml  # type: ignore[import]
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        return False  # broken YAML → fail toward privacy (opted OUT)
    if not isinstance(data, dict):
        return False
    spec = data.get("spec", data)
    if not isinstance(spec, dict):
        return False
    tele = spec.get("telemetry", {})
    if not isinstance(tele, dict):
        return False
    v = tele.get(key, True)
    if v is False:
        return False
    if isinstance(v, str) and v.strip().lower() in _FALSE_LIKE:
        return False
    return True


# ── Instance identity + stateless token provisioning (ADR-0180 M4) ────────────

def _instance_id_path(home: Path) -> Path:
    return home / "instance_id"


def _instance_token_path(home: Path) -> Path:
    return home / "aco" / "telemetry" / "htrace-token.txt"


def _telemetry_token_path(home: Path) -> Path:
    return home / "aco" / "telemetry" / ".telemetry_token"


def load_or_create_instance_id(home: Path) -> str:
    """Return the persistent UUID4 instance id, creating ~/.corvin/instance_id if absent."""
    p = _instance_id_path(home)
    try:
        existing = p.read_text(encoding="utf-8").strip()
        parsed = uuid.UUID(existing)
        if parsed.version == 4 and str(parsed) == existing.lower():
            return existing
    except (OSError, ValueError):
        pass
    new_id = str(uuid.uuid4())
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write (tempfile + os.replace): a concurrent reader never observes a
    # half-written / empty instance_id, and two racing creators converge on one
    # file rather than interleaving bytes (F12).
    _atomic_write_token(p, new_id)
    return new_id


def provision_telemetry_tokens(
    home: Path, instance_id: str, *, _outer_locked: bool = False
) -> bool:
    """Fetch and persist the healing-trace HMAC tokens; return False on network failure.

    POSTs the instance_id to the Corvin-Features token endpoint (https-only, no
    redirects — F8), then writes the returned instance_token and telemetry_token
    to disk with 0o600 permissions. Fail-soft: any network / HTTP error returns
    False without raising.

    Concurrency (F6): the two token files are ONE logical pair — if two server
    responses interleave, the on-disk instance_token can end up from response A
    while the telemetry_token is from response B, a mismatch ensure_ping_tokens()'s
    existence-only check can never detect or self-heal. Both writes therefore run
    under the shared ``.ping.lock``. ``_outer_locked=True`` is passed by
    ``ensure_ping_tokens`` — which only ever runs while ``ping_if_due`` already
    holds that same lock — so the lock is NOT re-acquired there (a second flock on
    a fresh fd for the same file would deadlock this process against itself).
    """
    payload = json.dumps({"instance_id": instance_id}).encode("utf-8")
    req = urllib.request.Request(
        _TOKEN_ENDPOINT,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    lf = None
    if not _outer_locked:
        try:
            lock_path = _ping_lock_path(home)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lf = lock_path.open("w")
            if _HAS_FLOCK:
                _fcntl.flock(lf, _fcntl.LOCK_EX)  # blocking — serialize the pair write
        except OSError:
            if lf is not None:
                with contextlib.suppress(Exception):
                    lf.close()
            lf = None  # best-effort — proceed even if the lock cannot be taken

    try:
        try:
            with _open_no_redirect(req, _TOKEN_TIMEOUT_S) as resp:
                if not (200 <= resp.getcode() < 300):
                    return False
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.debug("htrace: token endpoint unreachable (non-fatal): %s", e)
            return False

        instance_token = str(data.get("instance_token", ""))
        telemetry_token = str(data.get("telemetry_token", ""))
        if not instance_token or not telemetry_token:
            logger.debug("htrace: token endpoint returned incomplete payload")
            return False

        try:
            inst_p = _instance_token_path(home)
            tel_p = _telemetry_token_path(home)
            inst_p.parent.mkdir(parents=True, exist_ok=True)
            # Atomic, paired write: both tokens land via tempfile+os.replace at
            # 0600 from creation (not write_text-then-chmod, which has a brief
            # permissive window and no atomicity). The surrounding lock keeps the
            # pair consistent across processes.
            _atomic_write_token(inst_p, instance_token)
            _atomic_write_token(tel_p, telemetry_token)
        except OSError as e:
            logger.debug("htrace: token persistence failed (non-fatal): %s", e)
            return False
        return True
    finally:
        if lf is not None:
            with contextlib.suppress(Exception):
                if _HAS_FLOCK:
                    _fcntl.flock(lf, _fcntl.LOCK_UN)
                lf.close()


def _atomic_write_token(path: Path, value: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Healing-trace gate (default-ON, opt-out) ──────────────────────────────────

def _healing_flag_on(spec: dict) -> bool:
    """Read spec.telemetry.healing_traces with opt-out semantics: ON unless an
    explicit boolean ``false`` or a false-like string (false/no/0/off)."""
    v = spec.get("telemetry", {}).get("healing_traces", True)
    if v is False:
        return False
    if isinstance(v, str) and v.strip().lower() in ("false", "no", "0", "off"):
        return False
    return True


def healing_traces_enabled(home: Path, *, cfg: dict | None = None) -> bool:
    """Default-ON (opt-OUT). Healing traces (ADR-0180) are enabled unless the
    operator/user explicitly disables them. Maintainer decision — the aggregation
    backend (Corvin-Logs) needs real healing data by default.

    Safety: a HealingTrace carries ONLY content-free fields — allowlisted stack
    namespaces, exc_type, code paths — with ``message_template`` and any free text
    excluded, and the FAIL-CLOSED ``_assert_safe_htrace`` backstop DROPS any record
    that still carries a PII/secret shape rather than uploading it. So default-ON
    transmits only anonymous code diagnostics. Legal basis: GDPR Art. 6(1)(f)
    legitimate interest. (A recorded ConsentAct is no longer *required* — it is
    kept only as an audit artefact when a user explicitly consents; the gate here
    is the opt-out flag.)

    Opt out: set ``spec.telemetry.healing_traces: false`` in
    ``<corvin_home>/tenants/_default/global/tenant.corvin.yaml`` (false-like
    strings false/no/0/off also disable). Any other state → ON.
    """
    if cfg is not None:
        # Callers may pass the full k8s-style manifest (with spec:) or a bare dict
        return _healing_flag_on(cfg.get("spec", cfg))
    # On-disk read with FAIL-CLOSED parsing: absent config → default ON;
    # a config that EXISTS but is broken → opted OUT, never default-ON (F4).
    flag = _read_telemetry_flag(_tenant_cfg_path(home), "healing_traces")
    return True if flag is None else flag


def load_consent_act_id(home: Path) -> str:
    """Return the consent_act_id for embedding in records, or '' if none."""
    act = ConsentAct.load(home)
    return act.consent_act_id if act is not None else ""


# ── Anonymous instance-count ping gate (default-ON, opt-out) ──────────────────

def ping_enabled(home: Path) -> bool:
    """Default-ON (opt-OUT). The anonymous activity ping is enabled unless the
    operator explicitly disables it.

    Sanctioned exception to the general telemetry stance (CLAUDE.md): this is
    ANONYMOUS INSTANCE COUNTING, not user telemetry. The ping sends only a random
    uuid4 instance id + the installed version + an HMAC token — NO personal data,
    no prompts, no PII (see htrace_uploader.ping_if_due). Legal basis: GDPR
    Art. 6(1)(f) legitimate interest (counting how many installations exist).
    Distinct from the healing-trace / error-signature channels, which remain
    strictly opt-in / deny-by-default.

    To opt out — the operator/user disables it — set
    ``spec.telemetry.ping_enabled: false`` in
    ``<corvin_home>/tenants/_default/global/tenant.corvin.yaml``. Disabled by an
    explicit boolean ``false`` OR a false-like string (false/no/0/off); anything
    else — including a missing key — keeps the ping ON.

    Multi-tenant note: the ping identity (instance_id, last_ping stamp) is
    ONE per CORVIN_HOME, shared across every tenant on this install — but
    each tenant can independently set ``spec.telemetry.ping_enabled``. This
    used to only ever consult the single env-resolved tenant
    (``CORVIN_TENANT_ID``, default ``_default``), so a non-default tenant's
    explicit opt-out was silently ignored for the shared ping (adversarial
    review finding). Fail-closed fix: if ANY known tenant on this install
    has explicitly opted out, the shared ping is suppressed for all of them.
    """
    if not _tenant_ping_flag(home, None):
        return False
    try:
        for tid in _discover_tenants_under(home):
            if not _tenant_ping_flag(home, tid):
                return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _discover_tenants_under(home: Path) -> list[str]:
    """Known tenant_ids under THIS specific ``home`` (mirrors
    ``boot_healer._discover_tenants()``'s logic, but parameterized by the
    caller's own ``home`` rather than the global ``forge.paths.corvin_home()``
    — needed so this stays correct under a test tmp-home / non-default
    CORVIN_HOME, not just the process-global one)."""
    tenants_root = home / "tenants"
    if not tenants_root.is_dir():
        return []
    return [
        d.name for d in tenants_root.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ]


def _tenant_ping_flag(home: Path, tid: str | None) -> bool:
    """``ping_enabled`` for one specific tenant id (``None`` = env-resolved
    default tenant, matching the pre-existing single-tenant behaviour).

    Fail-closed parsing (F4): an ABSENT config counts by default (opt-out), but a
    config that EXISTS and is broken/unparseable is treated as opted OUT — a
    config we cannot trust must never fall through to default-ON."""
    cfg_path = _tenant_cfg_path(home) if tid is None else (
        home / "tenants" / tid / "global" / "tenant.corvin.yaml"
    )
    flag = _read_telemetry_flag(cfg_path, "ping_enabled")
    return True if flag is None else flag


def ensure_ping_tokens(home: Path) -> bool:
    """Provision HMAC tokens if not yet present; fail-soft.

    Called automatically by ping_if_due() on every fresh install so the
    operator does not need to run a separate consent step just to be counted.
    Returns True if tokens are ready (freshly provisioned or already present).
    """
    inst_p = _instance_token_path(home)
    tel_p = _telemetry_token_path(home)
    if inst_p.exists() and tel_p.exists():
        return True  # already provisioned
    instance_id = load_or_create_instance_id(home)
    # ensure_ping_tokens() is only ever called from ping_if_due(), which already
    # holds the shared .ping.lock — so provision must NOT re-acquire it here or it
    # would deadlock the process against its own lock (F6).
    return provision_telemetry_tokens(home, instance_id, _outer_locked=True)


# ── CLI consent flow ──────────────────────────────────────────────────────────

def run_consent_flow(home: Path, *, method: str = "cli") -> Optional[ConsentAct]:
    """Interactive consent prompt. Returns ConsentAct on 'yes', None on cancel.

    Shows the versioned consent text, requires explicit 'yes' input (not Enter),
    and records a ConsentAct in the L16 audit chain on acceptance.
    """
    try:
        text = _consent_text()
    except OSError:
        print("ERROR: consent text not found — cannot enable healing traces.", file=sys.stderr)
        return None

    print("\n" + "=" * 72)
    print(text)

    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return None

    if answer != "yes":
        print("Healing trace telemetry NOT enabled.")
        return None

    try:
        from importlib.metadata import version
        cv = version("corvinOS")
    except Exception:  # noqa: BLE001
        cv = "unknown"

    act = ConsentAct(
        consent_act_id=str(uuid.uuid4()),
        consent_version=_CONSENT_VERSION,
        ts_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        text_sha256=_consent_text_sha256(),
        method=method,
        corvin_version=cv,
    )
    act.save(home)
    _write_consent_audit_event(home, act)
    print(f"\nHealing trace telemetry enabled (consent_act_id: {act.consent_act_id[:8]}…).")
    return act


def run_revoke_flow(home: Path) -> None:
    """Revoke consent. Immediate effect — no further uploads."""
    p = _consent_act_path(home)
    if p.exists():
        # Capture the existing act BEFORE overwriting — the file content is
        # replaced with a minimal {revoked: True} stub that cannot be
        # deserialized back into a ConsentAct, so the act_id is unrecoverable
        # afterwards.  We need it for the audit trail (GDPR Art. 7(3), Art. 30).
        existing_act: Optional[ConsentAct] = ConsentAct.load(home)
        p.write_text(
            json.dumps({"revoked": True, "ts_utc": datetime.now(timezone.utc).isoformat()}),
            encoding="utf-8",
        )
        _write_revoke_audit_event(home, existing_act)
    print("Healing trace telemetry disabled. No further data will be sent.")
    # Run cap enforcement once now: after revocation, healing_traces_enabled()
    # returns False so neither write_trace nor run_upload_cycle will call
    # _enforce_caps again, causing JSONL files to persist past the 14-day
    # retention limit promised in the consent text (GDPR Art. 5(1)(e)).
    try:
        from .htrace import _enforce_caps  # local import avoids circular dependency
        _enforce_caps(home)
    except Exception:  # noqa: BLE001
        pass


# ── L16 audit chain integration ───────────────────────────────────────────────

def _write_consent_audit_event(home: Path, act: ConsentAct) -> None:
    try:
        from forge import audit as _audit  # type: ignore[import]
        _audit.audit_event(
            "telemetry.consent.granted",
            {
                "consent_act_id": act.consent_act_id,
                "consent_version": act.consent_version,
                "text_sha256": act.text_sha256[:16],  # partial — enough for correlation
                "method": act.method,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _write_revoke_audit_event(home: Path, act: Optional["ConsentAct"] = None) -> None:
    try:
        from forge import audit as _audit  # type: ignore[import]
        # Include the consent_act_id so GDPR Art. 30 audits can correlate the
        # revocation event with the specific ConsentAct that was active.
        # Previously the payload was {}, making the revocation event
        # uncorrelatable after the on-disk file was overwritten.
        payload: dict = {}
        if act is not None:
            payload = {
                "consent_act_id": act.consent_act_id,
                "consent_version": act.consent_version,
                "text_sha256": act.text_sha256[:16],
            }
        _audit.audit_event("telemetry.consent.revoked", payload)
    except Exception:  # noqa: BLE001
        pass
