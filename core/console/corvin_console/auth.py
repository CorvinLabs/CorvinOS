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
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402

# ADR-0154 M3 (SDLP): the license dir is added lazily so the console can derive
# a session license-proof. operator/ on path enables `license.feature_lattice`.
_OPERATOR_PATH = _REPO / "operator"
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
    created_at: float
    last_seen_at: float
    expires_at: float
    persistent: bool = False  # "remember me" — skips IDLE_TIMEOUT_S check
    lic_proof: str = ""       # ADR-0154 M3 SDLP — "" = pre-M3 / free-tier passthrough

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
        os.chmod(str(path), _REQUIRED_MODE)


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
        return SessionRecord(
            sid=sid,
            sid_fingerprint=data["sid_fingerprint"],
            tier=data["tier"],
            tenant_id=data["tenant_id"],
            token_fingerprint=data.get("token_fingerprint", ""),
            csrf_secret=data["csrf_secret"],
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
        "sid_fingerprint":   rec.sid_fingerprint,
        "tier":              rec.tier,
        "tenant_id":         rec.tenant_id,
        "token_fingerprint": rec.token_fingerprint,
        "csrf_secret":       rec.csrf_secret,
        "created_at":        rec.created_at,
        "last_seen_at":      rec.last_seen_at,
        "expires_at":        rec.expires_at,
        "persistent":        rec.persistent,
        "lic_proof":         rec.lic_proof,
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
        created_at=ts,
        last_seen_at=ts,
        expires_at=ts + lifetime,
        persistent=persistent,
        lic_proof=_compute_lic_proof(sid),
    )
    _write_record(rec)
    return rec


def load_session(sid: str, *, now: float | None = None) -> SessionRecord | None:
    try:
        path = _session_path(sid)
    except SessionError:
        return None
    if not path.exists():
        return None
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
            # Deny this request, but do NOT unlink the file. The proof can
            # mismatch transiently while a license reload re-installs the root
            # key (the brief window in reload_from_disk between resetting to the
            # free root and setting the paid root); deleting here would log the
            # owner out irreversibly mid-/license/apply. A genuine license change
            # keeps failing every request (effective denial) and the record is
            # reaped by the session TTL sweep — no data loss on a transient race.
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
        # The session record could not be persisted.  Return the stale record
        # so the request can still proceed, but log the failure so operators
        # can detect a disk / permissions problem before it causes premature
        # idle-timeout expiry on repeated failures.
        fp = rec.sid_fingerprint
        _write_failures = getattr(load_session, "_write_failures", {})
        _write_failures[fp] = _write_failures.get(fp, 0) + 1
        load_session._write_failures = _write_failures  # type: ignore[attr-defined]
        consecutive = _write_failures[fp]
        if consecutive >= 3:
            _log.error(
                "load_session: persistent _write_record failure "
                "(sid_fp=%s, consecutive=%d): %s",
                fp, consecutive, exc,
            )
        else:
            _log.warning(
                "load_session: _write_record failed, returning stale last_seen_at "
                "(sid_fp=%s, consecutive=%d): %s",
                fp, consecutive, exc,
            )
        return rec
    # Successful write — reset the consecutive-failure counter for this SID.
    fp = rec.sid_fingerprint
    _write_failures = getattr(load_session, "_write_failures", {})
    if fp in _write_failures:
        del _write_failures[fp]
        load_session._write_failures = _write_failures  # type: ignore[attr-defined]
    return bumped


def end_session(sid: str) -> bool:
    try:
        path = _session_path(sid)
    except SessionError:
        return False
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False
