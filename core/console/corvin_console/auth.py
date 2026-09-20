"""Session-cookie auth bridge for the console UI.

Key properties:

1. Cookie name is ``corvin_console_sid``.
2. Sessions live under ``<corvin_home>/global/console/sessions/``
   (separate trust subtree).
3. Tier is hard-coded ``"owner"``.

For local deployments sessions are created directly (loopback = security
boundary). The ``token_fingerprint`` field is kept for backward compat
with existing session files; new sessions use an empty string.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

_log = logging.getLogger(__name__)

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[2]
_FORGE_PATH = _REPO / "corvin_operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402

# ADR-0154 M3 (SDLP): the license dir is added lazily so the console can derive
# a session license-proof. corvin_operator/ on path enables `license.feature_lattice`.
_OPERATOR_PATH = _REPO / "corvin_operator"
if str(_OPERATOR_PATH) not in sys.path:
    sys.path.insert(0, str(_OPERATOR_PATH))


def _compute_lic_proof(sid: str) -> str:
    """ADR-0154 M3 Session-Derived License Proof for *sid*.

    Returns ``""`` (fail-open) on any import/compute failure — the loopback
    owner login must NEVER brick because the license lattice is unavailable.
    On a no-license (free) install the lattice uses a stable public root, so the
    proof is stable and sessions persist; when the license changes the proof
    changes and outstanding sessions are invalidated (the OTA deterrent).
    """
    try:
        # Wire the OTA feature root key from the on-disk license FIRST, so the
        # proof reflects the actual licence tier in THIS (console) process. The
        # validator wires the root only inside its own load/reload, and the
        # console doesn't load the licence at boot — so without this the root
        # would be stale-free and the proof would (a) be an inert no-op deterrent
        # and (b) flip free→paid the first time any reload ran mid-session,
        # spuriously logging the owner out (review MEDIUM). reload_from_disk is
        # disk-only + throttled (cheap to call per session op) and resets to the
        # free root when no token is present, so free-tier stays stable. Wrapped
        # best-effort: a validator failure must never brick owner login.
        try:
            from license import validator as _lv  # type: ignore
            _lv.reload_from_disk()
        except Exception:  # noqa: BLE001
            pass  # fall through — feature_root_key() still returns a safe root
        from license.feature_lattice import session_lic_proof  # type: ignore

        return session_lic_proof(sid)
    except Exception:  # noqa: BLE001
        return ""


Tier = Literal["owner"]
COOKIE_NAME = "corvin_console_sid"

IDLE_TIMEOUT_S = 60 * 60                      # 1 hour
ABSOLUTE_TIMEOUT_S = 8 * 60 * 60              # 8 hours
PERSISTENT_TIMEOUT_S = 90 * 24 * 60 * 60      # 90 days — "remember me"

_SID_BYTES = 32       # → 43-char url-safe base64
_CSRF_BYTES = 16      # → 32-char hex

_REQUIRED_MODE = 0o600


class SessionError(Exception):
    """Base class for session-management failures."""


class SessionStoreMalformed(SessionError):
    """A specific session file is unreadable / wrong mode / corrupted."""


@dataclass(frozen=True)
class SessionRecord:
    sid: str
    sid_fingerprint: str
    tier: Tier
    tenant_id: str
    token_fingerprint: str
    csrf_secret: str
    csrf_nonce: str  # 32-char hex, one-time use per CSRF-protected operation (rotated after validation)
    csrf_nonce_issued_at: float  # Timestamp when csrf_nonce was issued
    created_at: float
    last_seen_at: float
    expires_at: float
    persistent: bool = False  # "remember me" — skips IDLE_TIMEOUT_S check
    lic_proof: str = ""       # ADR-0154 M3 SDLP — "" = pre-M3 / free-tier passthrough
    # ADR-0193 — True only for the synthetic record internal_auth.py builds for
    # the corvin-browser MCP tool's X-Corvin-Browser-Token path; False (the
    # default) for every real, cookie-authenticated SPA session. Lets a route
    # tell "an LLM-driven tool call" apart from "a human's own browser tab"
    # without a fragile string check on `sid`, e.g. to require the cross-host
    # navigation confirm for the former but not the latter (see
    # routes/browser.py::navigate).
    is_internal_tool: bool = False

    def is_alive(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        if now >= self.expires_at:
            return False
        # Persistent ("remember me") sessions skip the idle-timeout check —
        # only the absolute 90 d window matters. Non-persistent sessions
        # still die after 1 h of silence so a forgotten browser tab on a
        # shared device closes itself.
        if not self.persistent and now - self.last_seen_at >= IDLE_TIMEOUT_S:
            return False
        return True


def _console_dir() -> Path:
    return _forge_paths.corvin_home() / "global" / "console"


def _sessions_dir() -> Path:
    return _console_dir() / "sessions"


def _session_path(sid: str) -> Path:
    if not _looks_like_sid(sid):
        raise SessionError("invalid sid shape")
    return _sessions_dir() / f"{sid}.json"


def _looks_like_sid(sid: str) -> bool:
    if not isinstance(sid, str):
        return False
    if len(sid) != 43:
        return False
    return all(c.isalnum() or c in ("_", "-") for c in sid)


def _sid_fingerprint(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()[:12]


def derive_csrf_token(csrf_secret: str, sid: str) -> str:
    return hmac.new(
        csrf_secret.encode("utf-8"),
        sid.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_csrf_token(csrf_secret: str, sid: str, presented: str) -> bool:
    if not isinstance(presented, str) or len(presented) != 64:
        return False
    expected = derive_csrf_token(csrf_secret, sid)
    return hmac.compare_digest(expected, presented)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    # Use mkstemp so concurrent writers in the FastAPI threadpool get
    # distinct tmp files and never clobber each other's in-flight writes.
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}.",
        suffix=".tmp",
    )
    try:
        # POSIX-only: lock the file to 0o600. On Windows os.chmod does not
        # support a file descriptor (raises) and cannot set Unix mode bits
        # anyway — access there is governed by ACLs, so skip it.
        if sys.platform != "win32":
            os.chmod(fd, _REQUIRED_MODE)
        written = os.write(fd, encoded)
        if written != len(encoded):
            raise OSError(f"short write: {written}/{len(encoded)} bytes")
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    os.close(fd)
    _replace_atomic(tmp_path, str(path))
    if sys.platform != "win32":
        # HIGH FIX #7: Validate chmod succeeded
        try:
            os.chmod(str(path), _REQUIRED_MODE)
            # Verify permissions were actually set
            st = os.stat(str(path))
            actual_mode = st.st_mode & 0o777
            if actual_mode != _REQUIRED_MODE:
                raise OSError(
                    f"chmod failed to set mode 0o{_REQUIRED_MODE:o}: "
                    f"actual mode is 0o{actual_mode:o} (session file may be world-readable)"
                )
        except OSError as exc:
            # Don't silently ignore chmod failure — world-readable session is critical
            _log.error("Failed to set secure permissions on session file %s: %s", path, exc)
            raise


def _replace_atomic(src: str, dst: str) -> None:
    """Atomic rename with Windows retry.

    On POSIX os.replace() is a true atomic rename. On Windows it raises
    PermissionError (WinError 5) when another thread holds the destination
    open for reading — which happens constantly in FastAPI's threadpool.
    Retry with short exponential backoff; the reader releases the handle
    within a few ms.
    """
    if sys.platform != "win32":
        os.replace(src, dst)
        return
    for attempt in range(6):  # max ~310 ms total (10+20+40+80+160 ms)
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt < 5:
                time.sleep(0.01 * (2 ** attempt))
    # Final attempt — let it raise so the caller can log and handle.
    os.replace(src, dst)


def _read_record(path: Path, sid: str) -> SessionRecord:
    try:
        st = path.stat()
    except OSError as e:
        raise SessionStoreMalformed(f"stat {path}: {e}") from e
    # The 0o600 mode check is a POSIX-only guard (keep the session file
    # unreadable by other local users). On Windows os.chmod CANNOT produce Unix
    # mode 0o600 — st_mode & 0o777 is 0o666 — so enforcing it there rejected
    # EVERY freshly-written session, and whoami 401'd in an endless
    # "Opening session…" loop after a successful local-login. Windows access is
    # governed by ACLs, not Unix bits, so skip the bit-check on Windows.
    if sys.platform != "win32":
        mode = st.st_mode & 0o777
        if mode != _REQUIRED_MODE:
            raise SessionStoreMalformed(
                f"session {path} has mode 0o{mode:o}, want 0o{_REQUIRED_MODE:o}"
            )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SessionStoreMalformed(f"read {path}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SessionStoreMalformed(f"malformed json in {path}: {e}") from e
    if not isinstance(data, dict):
        raise SessionStoreMalformed(f"{path}: top-level must be object")
    try:
        # Import here to avoid circular import
        from .csrf import generate_csrf_nonce

        ts = time.time()
        return SessionRecord(
            sid=sid,
            sid_fingerprint=data["sid_fingerprint"],
            tier=data["tier"],
            tenant_id=data["tenant_id"],
            token_fingerprint=data.get("token_fingerprint", ""),
            csrf_secret=data["csrf_secret"],
            # Backward-compat: pre-nonce sessions get a fresh nonce on load
            csrf_nonce=str(data.get("csrf_nonce", generate_csrf_nonce())),
            csrf_nonce_issued_at=float(data.get("csrf_nonce_issued_at", ts)),
            created_at=float(data["created_at"]),
            last_seen_at=float(data["last_seen_at"]),
            expires_at=float(data["expires_at"]),
            # Backward-compat: pre-persistent sessions default to False.
            persistent=bool(data.get("persistent", False)),
            # Backward-compat: pre-M3 sessions have no lic_proof → "" (skip check).
            lic_proof=str(data.get("lic_proof", "")),
        )
    except (KeyError, ValueError) as e:
        raise SessionStoreMalformed(f"{path}: invalid record: {e}") from e


def _write_record(rec: SessionRecord) -> Path:
    path = _session_path(rec.sid)
    payload = {
        "sid_fingerprint":      rec.sid_fingerprint,
        "tier":                 rec.tier,
        "tenant_id":            rec.tenant_id,
        "token_fingerprint":    rec.token_fingerprint,
        "csrf_secret":          rec.csrf_secret,
        "csrf_nonce":           rec.csrf_nonce,
        "csrf_nonce_issued_at": rec.csrf_nonce_issued_at,
        "created_at":           rec.created_at,
        "last_seen_at":         rec.last_seen_at,
        "expires_at":           rec.expires_at,
        "persistent":           rec.persistent,
        "lic_proof":            rec.lic_proof,
    }
    _atomic_write(path, payload)
    return path


def create_session(
    *,
    tenant_id: str,
    token_fingerprint: str = "",
    persistent: bool = False,
    now: float | None = None,
) -> SessionRecord:
    """Mint a fresh session record and persist it.

    The console only knows the ``owner`` tier; tenant_id is required.

    When ``persistent=True`` ("remember me"), the absolute expiry is
    extended to 90 days and the per-load idle-timeout is skipped. The
    cookie max-age is set by the caller from ``ABSOLUTE_TIMEOUT_S`` vs.
    ``PERSISTENT_TIMEOUT_S`` accordingly.
    """
    if not tenant_id:
        raise SessionError("console sessions require tenant_id")

    from .csrf import generate_csrf_nonce

    sid = secrets.token_urlsafe(_SID_BYTES)
    csrf_secret = secrets.token_hex(_CSRF_BYTES)
    ts = now if now is not None else time.time()
    lifetime = PERSISTENT_TIMEOUT_S if persistent else ABSOLUTE_TIMEOUT_S
    rec = SessionRecord(
        sid=sid,
        sid_fingerprint=_sid_fingerprint(sid),
        tier="owner",
        tenant_id=tenant_id,
        token_fingerprint=token_fingerprint,
        csrf_secret=csrf_secret,
        csrf_nonce=generate_csrf_nonce(),
        csrf_nonce_issued_at=ts,
        created_at=ts,
        last_seen_at=ts,
        expires_at=ts + lifetime,
        persistent=persistent,
        lic_proof=_compute_lic_proof(sid),
    )
    _write_record(rec)

    # CRITICAL #1: Cache with tenant isolation
    from . import session_manager
    manager = session_manager.get_session_manager()
    manager.cache_put(rec, tenant_id)

    return rec


def load_session(sid: str, *, now: float | None = None, tenant_id: str | None = None) -> SessionRecord | None:
    try:
        path = _session_path(sid)
    except SessionError:
        return None
    if not path.exists():
        return None

    # Phase 2 REMEDIATION: If tenant_id not provided, read disk first to extract it
    # This allows cache lookup even when caller doesn't know tenant_id upfront
    if tenant_id is None:
        try:
            rec = _read_record(path, sid)
            tenant_id = rec.tenant_id  # Extract tenant_id from disk record
        except SessionStoreMalformed:
            return None
    else:
        # Try cache first (if tenant_id provided)
        from . import session_manager
        manager = session_manager.get_session_manager()
        cached = manager.cache_get(sid, tenant_id)
        if cached is not None and cached.is_alive(now if now is not None else time.time()):
            _log.debug("Session cache hit: sid_fp=%s tenant=%s", cached.sid_fingerprint, tenant_id)
            return cached
        try:
            rec = _read_record(path, sid)
        except SessionStoreMalformed:
            return None

    ts = now if now is not None else time.time()
    if not rec.is_alive(ts):
        try:
            path.unlink()
        except OSError:
            pass
        # Invalidate cache on session expiry
        if tenant_id is not None:
            from . import session_manager
            manager = session_manager.get_session_manager()
            manager.cache_invalidate(sid, tenant_id)
        return None

    # ADR-0154 M3 (SDLP): if this session carries a license proof, it must still
    # match the active license. A license swap/removal changes the derived proof
    # and invalidates the session (looks like a normal session expiry to the
    # user — no license vocabulary surfaces). Only enforced on a POSITIVE
    # mismatch: an empty stored proof (pre-M3 session) or an empty recomputed
    # proof (lattice unavailable) is treated as "skip" so loopback owner login
    # never bricks (free-tier-safe, fail-open on infra failure).
    if rec.lic_proof:
        expected = _compute_lic_proof(sid)
        if expected and not hmac.compare_digest(expected, rec.lic_proof):
            # Deny this request, but do NOT unlink the file. A genuine license
            # change keeps failing every request (effective denial) and the
            # record is reaped by the session TTL sweep — no data loss.
            #
            # This USED to fire routinely, not just on a real licence change:
            # reload_from_disk() reset the OTA root key to free before it had
            # resolved the on-disk token, so every request that computed its
            # proof inside that window derived it from the free root and was
            # denied. On a licensed install a single console panel load (~10
            # parallel requests, one reload every 5 s) lost 8 of them to this,
            # and the SPA's 401 handler reads that as a lost session. The root
            # key is now installed exactly once per reload, under
            # _RELOAD_LOCK, and never transits through free (2026-09-20).
            _log.warning(
                "load_session: session proof mismatch (sid_fp=%s) — denying "
                "(not deleting; may be a transient license-reload window)",
                rec.sid_fingerprint,
            )
            return None

    bumped = replace(rec, last_seen_at=ts)
    try:
        _write_record(bumped)
    except OSError as exc:
        # CRITICAL FIX #2: Don't return stale record on write failure.
        # Instead, return the in-memory bumped record for THIS request only,
        # but mark for re-validation on next request (return stale only once).
        # This prevents idle timeout bypass when disk is full/broken.
        fp = rec.sid_fingerprint
        _write_failures = getattr(load_session, "_write_failures", {})
        _write_failures[fp] = _write_failures.get(fp, 0) + 1
        load_session._write_failures = _write_failures  # type: ignore[attr-defined]
        consecutive = _write_failures[fp]

        if consecutive >= 5:
            # After 5 consecutive failures, deny the session entirely.
            # This prevents indefinite use of a session when disk is broken.
            _log.error(
                "load_session: persistent write failures (sid_fp=%s, consecutive=%d). "
                "Denying session access to enforce idle timeout. %s",
                fp, consecutive, exc,
            )
            return None  # CRITICAL: Fail-closed on repeated write failures
        else:
            # Return bumped record for THIS request, but log that write failed
            _log.warning(
                "load_session: _write_record failed (sid_fp=%s, consecutive=%d): %s. "
                "Session valid for this request, but not persisted.",
                fp, consecutive, exc,
            )
            return bumped  # Still usable for this request, but will re-check next time

    # Successful write — reset the consecutive-failure counter for this SID.
    fp = rec.sid_fingerprint
    _write_failures = getattr(load_session, "_write_failures", {})
    if fp in _write_failures:
        del _write_failures[fp]
        load_session._write_failures = _write_failures  # type: ignore[attr-defined]

    # CRITICAL #1: Cache with tenant isolation
    if tenant_id is not None:
        from . import session_manager
        manager = session_manager.get_session_manager()
        manager.cache_put(bumped, tenant_id)

    return bumped


def end_session(sid: str, tenant_id: str | None = None) -> bool:
    try:
        path = _session_path(sid)
    except SessionError:
        return False
    if not path.exists():
        return False
    try:
        path.unlink()
        # MEDIUM: Fsync after unlink for durability (ensures metadata written)
        if sys.platform != "win32":
            try:
                dirfd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
            except OSError as sync_exc:
                _log.warning("Failed to fsync session directory after unlink: %s", sync_exc)
                # Non-fatal — log but don't fail the operation
        # Invalidate cache on session end
        if tenant_id is not None:
            from . import session_manager
            manager = session_manager.get_session_manager()
            manager.cache_invalidate(sid, tenant_id)
        return True
    except OSError:
        return False


# ═════════════════════════════════════════════════════════════════════════════
# Session Token Binding — Cryptographically-Bound Session Tokens (ADR-XXXX)
# ═════════════════════════════════════════════════════════════════════════════
# CRITICAL SECURITY: Session tokens are HMAC-SHA256 bound to server state and
# cannot be forged via HTTP headers (User-Agent, X-Forwarded-For). Prevents
# operator ID spoofing attacks.


TOKEN_TTL_S = 60 * 60  # 1 hour
_SERVER_SECRET: bytes | None = None


def _get_server_secret() -> bytes:
    """Get or generate the per-boot server secret (NEVER persisted)."""
    global _SERVER_SECRET
    if _SERVER_SECRET is None:
        _SERVER_SECRET = secrets.token_bytes(32)
        _log.info("Session token binding: generated fresh server secret (this boot only)")
    return _SERVER_SECRET


class SessionToken:
    """Cryptographically-bound session token (immutable)."""

    def __init__(
        self,
        token: str,
        issued_at: float,
        expires_at: float,
        operator_id: str,
        tenant_id: str,
        session_id: str,
    ):
        self.token = token
        self.issued_at = issued_at
        self.expires_at = expires_at
        self.operator_id = operator_id
        self.tenant_id = tenant_id
        self.session_id = session_id

    def is_expired(self, now: float | None = None) -> bool:
        """Check if token has expired."""
        check_time = now if now is not None else time.time()
        return check_time >= self.expires_at


def token_to_json(token: SessionToken) -> dict[str, Any]:
    """Serialize a SessionToken for an HTTP response.

    Excludes session_id: it is the server-side session cookie value, never
    sent back to the client inside another payload (that would let a
    leaked/logged response body double as a session hijack vector).
    """
    return {
        "token": token.token,
        "issued_at": token.issued_at,
        "expires_at": token.expires_at,
        "operator_id": token.operator_id,
        "tenant_id": token.tenant_id,
    }


def generate_token(
    session_id: str,
    operator_id: str,
    tenant_id: str,
    client_nonce: str,
    fixed_fingerprint: str,
    now: float | None = None,
) -> SessionToken:
    """Generate a new cryptographically-bound session token.

    FAIL-CLOSED: If any component is missing or invalid, raise ValueError.
    """
    if not session_id or not isinstance(session_id, str):
        raise ValueError("session_id required and must be string")
    if not operator_id or not isinstance(operator_id, str):
        raise ValueError("operator_id required and must be string")
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError("tenant_id required and must be string")
    if not client_nonce or not isinstance(client_nonce, str) or len(client_nonce) < 16:
        raise ValueError("client_nonce required, must be string, min 16 chars")
    if not fixed_fingerprint or not isinstance(fixed_fingerprint, str):
        raise ValueError("fixed_fingerprint required and must be string")

    ts = now if now is not None else time.time()
    secret = _get_server_secret()

    # Construct message for HMAC
    msg = f"{session_id}|{int(ts)}|{client_nonce}|{fixed_fingerprint}|{operator_id}|{tenant_id}"

    # Generate HMAC-SHA256
    token_hex = hmac.new(
        secret,
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return SessionToken(
        token=token_hex,
        issued_at=ts,
        expires_at=ts + TOKEN_TTL_S,
        operator_id=operator_id,
        tenant_id=tenant_id,
        session_id=session_id,
    )


def validate_token(
    presented_token: str,
    session_id: str,
    operator_id: str,
    tenant_id: str,
    client_nonce: str,
    fixed_fingerprint: str,
    now: float | None = None,
    token_issued_at: float | None = None,
) -> tuple[bool, str]:
    """Validate a presented token (FAIL-CLOSED).

    token_issued_at must be the timestamp the token was actually generated
    with (e.g. stored server-side alongside the session, or returned to the
    caller at issuance). Without it, the expected token can only be
    recomputed against `now`, which makes any expiry check vacuous: an old
    token would never match a freshly-generated one regardless of age, so
    it is rejected as `token_mismatch` rather than the more informative
    `token_expired` — still fail-closed, but expiry never actually fires.

    Returns: (is_valid, reason_if_invalid)
    """
    ts = now if now is not None else time.time()
    issued_at = token_issued_at if token_issued_at is not None else ts

    # Validate input shape
    if not isinstance(presented_token, str):
        return False, "token_not_string"
    if not _looks_like_token(presented_token):
        return False, "token_invalid_format"

    # Check expiry against the token's real issuance time, not `now`.
    if ts - issued_at > TOKEN_TTL_S:
        return False, "token_expired"

    try:
        expected = generate_token(
            session_id, operator_id, tenant_id, client_nonce, fixed_fingerprint, now=issued_at
        )
    except ValueError as e:
        return False, f"token_generation_failed: {str(e)}"

    # Timing-safe comparison
    if not hmac.compare_digest(presented_token, expected.token):
        return False, "token_mismatch"

    return True, ""


def _looks_like_token(token: str) -> bool:
    """Quick shape check: tokens are 64-char hex (SHA256)."""
    if not isinstance(token, str):
        return False
    if len(token) != 64:
        return False
    try:
        int(token, 16)
        return True
    except ValueError:
        return False


def emit_audit_event(
    event_type: str,
    session_id: str,
    operator_id: str,
    tenant_id: str,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Emit an audit event for token validation (GDPR Art. 30, EU AI Act Art. 50)."""
    try:
        from . import audit as console_audit

        audit_details = {
            "session_id": session_id,
            "operator_id": operator_id,
        }
        if reason:
            audit_details["reason"] = reason
        if details:
            audit_details.update(details)

        console_audit.system_event(
            tenant_id=tenant_id,
            event=event_type,
            details=audit_details,
        )
    except Exception:  # noqa: BLE001
        # FAIL-OPEN on audit: don't let logging failure block the request
        _log.error("Failed to emit token validation audit event")
