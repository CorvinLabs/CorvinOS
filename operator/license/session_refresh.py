"""Session permit refresh client — ADR-0095 M1 / ADR-0098.

Fetches a short-lived (6 h) server-signed session permit from Corvin-Features
and writes it to ~/.config/corvin-voice/session.key (mode 0600).

Call once at adapter boot (blocking, 5 s timeout) and then periodically in a
background thread (every REFRESH_INTERVAL seconds).

Storage layout:
  ~/.config/corvin-voice/features.json  — {"token_fp", "api_key", "activated_at", "device_fp"}
  ~/.config/corvin-voice/session.key    — current session permit (CORVIN-...)
  ~/.config/corvin-voice/session.meta   — {"refreshed_at", "exp", "tier"}

Fail-open: any error during refresh leaves the existing session.key intact.
Grace period: CorvinOS validator accepts an expired session.key for up to
GRACE_PERIOD_SECONDS (6 hours) after the last successful refresh.

ADR-0098 device binding: every refresh request carries X-Corvin-Device-Fp.
The fingerprint is computed fresh from hardware identifiers at each refresh
(not read from disk) so copying features.json to a different machine does
not bypass the server-side device check.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger("corvin.license.session_refresh")

REFRESH_INTERVAL = 4 * 3600        # refresh every 4 hours
GRACE_PERIOD_SECONDS = 6 * 3600    # allow expired session for 6 hours only (ADR-0098)
_BOOT_TIMEOUT = 5                   # seconds for blocking boot-time refresh

_FEATURES_SERVER_PROD = "https://corvin-features-production.up.railway.app"


# ADR-0144 Fix B1: snapshot CORVIN_TEST_MODE and CORVIN_FEATURES_URL at module
# import time (analogous to _CORVIN_HOME_SNAPSHOT_SR / _CONFIG_DIR_SNAPSHOT_SR).
# Live os.environ reads in a long-running daemon are an in-process attack vector:
# an adversary with in-process code execution can redirect refresh traffic to an
# attacker-controlled server after boot by mutating these variables.
_TEST_MODE_SNAPSHOT: str = os.environ.get("CORVIN_TEST_MODE", "")
_FEATURES_URL_SNAPSHOT: str = os.environ.get("CORVIN_FEATURES_URL", _FEATURES_SERVER_PROD)


def _features_server() -> str:
    """Return the Corvin-Features base URL.

    In production the URL is hardcoded.  CORVIN_FEATURES_URL is accepted
    ONLY when CORVIN_TEST_MODE=1 — the env-var override used to exist
    unconditionally, which was a license-bypass attack vector (ADR-0098).
    Both values are snapshotted at import time to prevent post-boot mutation
    (ADR-0144 Fix B1).
    """
    if _TEST_MODE_SNAPSHOT == "1":
        return _FEATURES_URL_SNAPSHOT
    return _FEATURES_SERVER_PROD

_refresh_lock = threading.Lock()


# ── Hardware device fingerprint ───────────────────────────────────────────────

def _get_device_fp() -> str:
    """Compute ADR-0098 device fingerprint fresh from local hardware.

    Delegates to device_fp.compute_device_fp() — single source of truth
    shared with sob.py and validator.py (FND-LIC-07 fix).
    """
    import sys as _sys
    _lic_dir = str(Path(__file__).resolve().parent)
    if _lic_dir not in _sys.path:
        _sys.path.insert(0, _lic_dir)
    from device_fp import compute_device_fp as _cfp  # type: ignore
    return _cfp()


# ── Config / key paths ────────────────────────────────────────────────────────

# ADR-0138 M1 C2: snapshot XDG_CONFIG_HOME + CORVIN_HOME at module import time
# so long-running refresh daemon is immune to post-start env mutations.
_CONFIG_DIR_SNAPSHOT_SR: Path = (
    (Path(os.environ["XDG_CONFIG_HOME"]) / "corvin-voice")
    if os.environ.get("XDG_CONFIG_HOME")
    else (Path.home() / ".config" / "corvin-voice")
)
_CORVIN_HOME_SNAPSHOT_SR: Path = (
    Path(os.environ["CORVIN_HOME"])
    if os.environ.get("CORVIN_HOME", "").strip()
    else (Path.home() / ".corvin")
)


def _config_dir() -> Path:
    return _CONFIG_DIR_SNAPSHOT_SR


def features_json_path() -> Path:
    return _config_dir() / "features.json"


def session_key_path() -> Path:
    return _config_dir() / "session.key"


def session_meta_path() -> Path:
    return _config_dir() / "session.meta"


# ── Activation credential storage ─────────────────────────────────────────────

def save_activation(token_fp: str, api_key: str, device_fp: str | None = None) -> None:
    """Persist activation credentials so the refresh daemon can use them.

    Called by `corvin-license activate` after a successful activation.
    device_fp is stored as informational metadata; the refresh daemon
    computes it fresh from hardware on every refresh (ADR-0098).
    """
    data: dict = {
        "token_fp": token_fp,
        "api_key": api_key,
        "activated_at": int(time.time()),
    }
    if device_fp:
        data["device_fp"] = device_fp
    _write_secure(features_json_path(), json.dumps(data))
    _log.info("session_refresh: activation credentials saved")


def load_features() -> dict[str, Any] | None:
    """Return {"token_fp", "api_key", "activated_at"} or None."""
    path = features_json_path()
    if not path.exists():
        return None
    try:
        mode = path.stat().st_mode & 0o777
        # Windows: NTFS has no POSIX group/other bits, so st_mode always looks
        # permissive there regardless of real ACLs — skip the check (chmod
        # cannot narrow it either, so this would otherwise fire on every read).
        if not sys.platform.startswith("win") and mode & 0o077:
            _log.warning("features.json mode 0o%o too permissive — correcting", mode)
            path.chmod(0o600)
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.warning("session_refresh: cannot read features.json: %s", exc)
        return None


# ── HMAC helpers ──────────────────────────────────────────────────────────────

def _sign_request(body: bytes, api_key: str) -> tuple[str, str]:
    ts = str(int(time.time()))
    sig = hmac.new(
        api_key.encode(), body + b"." + ts.encode(), hashlib.sha256
    ).hexdigest()
    return ts, sig


# ── Token discovery ───────────────────────────────────────────────────────────

def _find_license_token() -> str | None:
    env = os.environ.get("CORVIN_LICENSE_KEY", "").strip()
    if env:
        return env
    corvin_home = _CORVIN_HOME_SNAPSHOT_SR
    key_file = corvin_home / "global" / "license.key"
    if key_file.exists():
        try:
            t = key_file.read_text().strip()
            if t:
                return t
        except OSError:
            pass
    return None


# ── Atomic write ──────────────────────────────────────────────────────────────

def _write_secure(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sr.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Refresh logic ─────────────────────────────────────────────────────────────

def refresh_once(*, timeout: int = 10) -> bool:
    """Attempt a single HTTP refresh. Returns True on success.

    Thread-safe (holds _refresh_lock for the duration).
    """
    with _refresh_lock:
        return _do_refresh(timeout=timeout)


def _do_refresh(*, timeout: int) -> bool:
    try:
        import urllib.request
        import urllib.error
    except ImportError:
        return False

    features = load_features()
    if features is None:
        _log.debug("session_refresh: no features.json — not yet activated")
        return False

    token_fp = features.get("token_fp", "")
    api_key = features.get("api_key", "")
    if not token_fp or not api_key:
        _log.warning("session_refresh: features.json missing token_fp or api_key")
        return False

    license_token = _find_license_token()
    if not license_token:
        _log.debug("session_refresh: no license token — skipping refresh")
        return False

    body = b""
    ts, sig = _sign_request(body, api_key)
    url = f"{_features_server()}/v1/session/refresh"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {license_token}")
    req.add_header("X-Corvin-Ts", ts)
    req.add_header("X-Corvin-Sig", sig)
    # ADR-0098: send fresh hardware fingerprint so the server can enforce
    # device binding. Computed from hardware here (not read from features.json)
    # so that copying features.json to a different machine still fails.
    device_fp = _get_device_fp()
    if device_fp:
        req.add_header("X-Corvin-Device-Fp", device_fp)
    # ADR-0098 P2: send code attestation for anomaly detection.
    # Server logs a mismatch event when this value deviates from known-good hashes.
    req.add_header("X-Corvin-Attestation", _compute_attestation())

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode()[:200]
        _log.warning("session_refresh: HTTP %d: %s", exc.code, body_text)
        return False
    except Exception as exc:
        _log.warning("session_refresh: network error: %s", exc)
        return False

    session_token = data.get("session_token", "")
    if not session_token:
        _log.warning("session_refresh: empty session_token in response")
        return False

    # ADR-0144 Fix F-A: verify Ed25519 signature BEFORE writing to disk.
    # A MITM between the adapter and the refresh endpoint could inject a
    # syntactically valid but wrongly-signed token, which would degrade to
    # Free-tier on the next reload.  Checking the signature locally ensures
    # we only persist tokens we can cryptographically trust.
    # Fail-CLOSED when the validator module is unavailable: the background
    # refresh daemon runs for hours after boot, so the "self_test catches it at
    # boot" guarantee only holds for initial startup. An in-process attacker
    # that removes validator from sys.modules post-boot would otherwise bypass
    # the verify step. Without verification we cannot confirm the token's
    # authenticity, so we refuse to write it. (ADR-0144 Fix B review-round-1.)
    _sr_verified = False
    try:
        import sys as _sr_sys
        _lic_dir = str(Path(__file__).resolve().parent.parent)
        if _lic_dir not in _sr_sys.path:
            _sr_sys.path.insert(0, _lic_dir)
        from license.validator import _verify_ed25519 as _sr_verify  # type: ignore
        if _sr_verify(session_token) is None:
            _log.warning(
                "session_refresh: received token failed local Ed25519 verification — "
                "NOT writing to disk; keeping existing session.key intact. (ADR-0144 F-A)"
            )
            return False
        _sr_verified = True
    except ImportError:
        _log.warning(
            "session_refresh: license.validator not importable — "
            "cannot verify received token; NOT writing to disk (fail-closed). "
            "Check that operator/license/ is intact. (ADR-0144)"
        )
    if not _sr_verified:
        return False

    exp = int(data.get("exp", time.time() + 6 * 3600))
    tier = str(data.get("tier", "unknown"))

    _write_secure(session_key_path(), session_token)
    _write_secure(session_meta_path(), json.dumps({
        "refreshed_at": int(time.time()),
        "exp": exp,
        "tier": tier,
    }))

    _log.info("session_refresh: refreshed — tier=%s exp=%d", tier, exp)
    return True


# ── Grace period checks ───────────────────────────────────────────────────────

def should_refresh() -> bool:
    """True if the refresh interval has elapsed or no session.meta exists."""
    meta = _load_meta()
    if meta is None:
        return True
    return (time.time() - meta.get("refreshed_at", 0)) > REFRESH_INTERVAL


def is_within_grace_period() -> bool:
    """True if last successful refresh is within GRACE_PERIOD_SECONDS."""
    meta = _load_meta()
    if meta is None:
        return False
    return (time.time() - meta.get("refreshed_at", 0)) <= GRACE_PERIOD_SECONDS


def _load_meta() -> dict[str, Any] | None:
    try:
        return json.loads(session_meta_path().read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Background daemon ─────────────────────────────────────────────────────────

_daemon_started = False


def start_background_daemon() -> None:
    """Start a daemon thread that refreshes the session permit every 4 hours.

    Safe to call multiple times — only one thread is ever started.
    Call from adapter boot AFTER load_license_from_env().
    """
    global _daemon_started
    if _daemon_started:
        return
    _daemon_started = True
    t = threading.Thread(target=_daemon_loop, daemon=True, name="corvin-session-refresh")
    t.start()
    _log.debug("session_refresh: background daemon started")


def _daemon_loop() -> None:
    # Initial delay: wait 30 s so the adapter is fully booted before the first
    # refresh attempt (avoids hammering the server during rapid restart cycles).
    time.sleep(30)
    while True:
        try:
            if should_refresh():
                ok = refresh_once()
                if not ok:
                    _log.debug("session_refresh: refresh failed (will retry next cycle)")
        except Exception as exc:
            _log.warning("session_refresh: daemon error: %s", exc)
        time.sleep(60)   # check every minute; refresh_once only runs when interval has elapsed


# ── Code attestation (ADR-0098 P2) ───────────────────────────────────────────

def _compute_attestation() -> str:
    """Return a short anomaly-detection fingerprint for critical license files.

    Computes SHA-256 over the sorted concatenation of the files listed below.
    Returns the first 16 hex chars — enough to detect tampering.

    Sent as X-Corvin-Attestation on every session refresh.  The server logs
    mismatches; it does NOT hard-block (legitimate custom builds must still work).

    ADR-0098 P2: covers all files whose modification could silently elevate a
    Free-tier installation to a paid tier without a valid token.  An attacker
    must patch ALL of them AND this function to suppress the anomaly signal.

    Files covered:
      - session_refresh.py  (this file — contains attestation logic)
      - validator.py        (Ed25519 token verification + tier resolution)
      - limits.py           (TIER_RESOURCE_LIMITS — patching could elevate free tier)
      - ../../core/license/corvin_license/tier_flags.py  (tier→flag canonical map)
      - ../../core/license/corvin_license/verifier.py    (RS256 license verification)
    """
    _here = Path(__file__).resolve()
    _core_lic = _here.parents[2] / "core" / "license" / "corvin_license"
    _files = sorted([
        _here,                               # session_refresh.py
        _here.parent / "validator.py",
        _here.parent / "limits.py",
        _core_lic / "tier_flags.py",
        _core_lic / "verifier.py",
    ])
    h = hashlib.sha256()
    for f in _files:
        try:
            h.update(f.read_bytes())
        except OSError:
            h.update(f.name.encode() + b":MISSING")
    return h.hexdigest()[:16]


# ── Boot-time key integrity check ────────────────────────────────────────────

def _audit_license_event(event_type: str, **details: Any) -> None:
    """Best-effort audit emission (mirrors validator._audit). Never raises."""
    try:
        _here = Path(__file__).resolve()
        shared = _here.parents[2] / "bridges" / "shared"
        import sys as _sys
        if str(shared) not in _sys.path:
            _sys.path.insert(0, str(shared))
        from audit import audit_event  # type: ignore[import]
        audit_event(event_type, **details)
    except Exception:
        pass


def check_public_key_integrity() -> bool:
    """Verify the embedded SESSION_SERVER_KEY_RING matches the live server.

    Fetches GET /v1/keys/session-key-ring from Corvin-Features, which returns
    the full {kid: public_key_b64} mapping the server currently trusts.
    Compares each server key against SESSION_SERVER_KEY_RING in validator.py.

    - All keys match   → True (OK)
    - Any key differs  → False + CRITICAL log + license.key_integrity_mismatch audit.
                         Does NOT block (fail-open) — cannot prevent local patching,
                         but creates an immutable audit trail at boot time.
    - Unknown server kid not in local ring → WARNING only (forward compat for rotation)
    - Network error    → True (fail-open — offline installs must still work).
    - CORVIN_TEST_MODE → True (skip — test environments use mock keys).

    ADR-0098 P3: checks the full key ring, not just the primary key, so that
    any key substitution (including substituting a future sess-v2 key) is detected.
    """
    if _TEST_MODE_SNAPSHOT == "1":  # V5: use snapshot, not live env read
        return True

    try:
        import urllib.request as _ur
        # Try the new ring endpoint first; fall back to legacy single-key endpoint.
        url = f"{_FEATURES_SERVER_PROD}/v1/keys/session-key-ring"
        req = _ur.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        try:
            with _ur.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            # Expected: {"keys": {"sess-v1": "...", "sess-v2": "..."}}
            server_ring: dict = data.get("keys", {})
            if not server_ring:
                # Legacy fallback: {"public_key_b64": "...", "kid": "sess-v1"}
                pk = str(data.get("public_key_b64", "")).strip()
                kid = str(data.get("kid", "sess-v1"))
                server_ring = {kid: pk} if pk else {}
        except Exception:
            # /v1/keys/session-key-ring not deployed yet — fall back to legacy endpoint
            url_legacy = f"{_FEATURES_SERVER_PROD}/v1/keys/session-public-key"
            req2 = _ur.Request(url_legacy, method="GET")
            req2.add_header("Accept", "application/json")
            with _ur.urlopen(req2, timeout=3) as resp2:
                data2 = json.loads(resp2.read().decode("utf-8"))
            pk = str(data2.get("public_key_b64", "")).strip()
            kid = str(data2.get("kid", "sess-v1"))
            server_ring = {kid: pk} if pk else {}
    except Exception as exc:
        _log.warning(
            "key_integrity: cannot reach features server (%s) — skipping check", exc
        )
        return True  # fail-open: offline installs still work

    if not server_ring:
        _log.warning("key_integrity: server returned empty key ring — skipping")
        return True

    # Lazy import of the local key ring.
    try:
        from .validator import SESSION_SERVER_KEY_RING as _local_ring  # type: ignore
    except ImportError:
        import sys as _sys
        _here = Path(__file__).resolve().parent
        if str(_here) not in _sys.path:
            _sys.path.insert(0, str(_here))
        from validator import SESSION_SERVER_KEY_RING as _local_ring  # type: ignore

    # Compare every key that appears in BOTH rings (constant-time per comparison).
    mismatch_kids: list[str] = []
    for kid, server_key in server_ring.items():
        if not server_key:
            continue
        local_key = _local_ring.get(kid, "")
        if not local_key:
            # Server has a kid the local ring doesn't know yet — expected during rotation.
            _log.warning(
                "key_integrity: server kid=%r not in local SESSION_SERVER_KEY_RING "
                "— update CorvinOS to receive permits signed with the new key", kid
            )
            continue
        if not hmac.compare_digest(server_key, local_key):
            mismatch_kids.append(kid)

    if mismatch_kids:
        _log.critical(
            "license: KEY INTEGRITY MISMATCH for kid(s) %s — "
            "SESSION_SERVER_KEY_RING in validator.py does not match the live "
            "Corvin-Features server. Possible tampering detected. "
            "Contact security@corvin-labs.com.",
            mismatch_kids,
        )
        _audit_license_event("license.key_integrity_mismatch")
        return False

    _log.debug("key_integrity: session-permit key ring verified OK")
    return True


# ── Boot-time synchronous refresh ────────────────────────────────────────────

def boot_refresh() -> bool:
    """Attempt a blocking refresh at boot time (5 s timeout).

    Runs check_public_key_integrity() first (non-blocking — emits audit on
    mismatch but never prevents startup). Then refreshes the session permit.
    Returns True if the session.key was successfully refreshed.
    Never raises — all errors are logged and suppressed.
    """
    check_public_key_integrity()
    try:
        if not should_refresh():
            return False   # fresh key, no need to refresh
        return refresh_once(timeout=_BOOT_TIMEOUT)
    except Exception as exc:
        _log.debug("session_refresh: boot refresh failed: %s", exc)
        return False
