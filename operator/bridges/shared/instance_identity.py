"""Layer 38 — Instance Identity.

Each Corvin deployment carries a stable, locally-generated UUID that
identifies it across A2A exchanges. The identity is persisted at
``<corvin_home>/global/instance_id.json`` (mode 0600) on first read; all
subsequent reads return the same value.

Why this lives separately from the OriginRegistry:

* OriginRegistry says *who is allowed to call us* (incoming auth).
* InstanceIdentity says *who we are when we reply or initiate* (outgoing
  attestation). The cloud-side caller can pin the instance_id alongside
  the recv_key to detect a swapped/cloned receiver.

Threat model: identity is a non-secret attestation. The recv_key remains
the cryptographic root of trust — a leaked or guessed instance_id alone
yields nothing without the matching key.

Public API:
    get_instance_id() -> str
    instance_id_path() -> Path
    instance_id_metadata() -> dict   # {instance_id, created_at, label}
    set_label(label: str) -> dict    # update human-readable label only

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac as _hmac
import json
import os
import stat
import sys
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives import serialization as _serialization
    from cryptography.hazmat.backends import default_backend as _default_backend
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

try:
    from .paths import corvin_home  # type: ignore[import-not-found]
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from paths import corvin_home  # type: ignore[import-not-found]

# Best-effort audit hook — imported lazily so unit tests without the forge
# plugin can still use instance_identity.  CI lint: MUST NOT import anthropic.
def _audit_ibc(event_type: str, severity: str, details: dict) -> None:
    """Emit to the L16 audit chain if the SecurityEventsPlugin is available."""
    try:
        try:
            from .forge.security_events import SecurityEventsPlugin as _SEP  # type: ignore
        except ImportError:
            from forge.security_events import SecurityEventsPlugin as _SEP  # type: ignore
        _SEP().write_event(event_type, severity, details)
    except Exception:  # noqa: BLE001
        pass  # best-effort — never block IBC operations


_INSTANCE_ID_FILE = "instance_id.json"
_GLOBAL_DIR = "global"
# RLock, not Lock: instance_id_metadata(create_if_missing=False) can, while
# holding this lock, call _emit_missing_audit() -> forge.security_events.write_event()
# -> get_instance_id() -> instance_id_metadata() again on the SAME thread (per-event
# instance_id attestation, ADR-0153 M3). A plain Lock self-deadlocks on that re-entry —
# the fail-closed missing-identity path would hang forever instead of raising.
_lock = threading.RLock()

_INSTANCE_KEY_FILE = "instance_key.pem"
_INSTANCE_PUBKEY_FILE = "instance_pubkey.pem"
_IBC_FILE = "instance_cert.jwt"

# The live Corvin-Features server (Paddle/license/A2A-permit backend, ADR-0093
# M3). Earlier drafts of this module pointed at "api.corvin-labs.com", a
# domain that was never actually provisioned — every IBC call silently failed
# in production. Fixed to the real, already-live domain used everywhere else
# in operator/license/ (session_refresh.py, validator.py).
_FEATURES_SERVER_PROD = "https://corvin-features-production.up.railway.app"

# ADR-0144 Fix B1 pattern (see session_refresh.py): snapshot at import time so
# a post-boot env mutation from in-process code cannot redirect IBC traffic to
# an attacker-controlled server. CORVIN_FEATURES_URL is honoured ONLY under
# CORVIN_TEST_MODE=1.
_TEST_MODE_SNAPSHOT: str = os.environ.get("CORVIN_TEST_MODE", "")
_FEATURES_URL_SNAPSHOT: str = os.environ.get("CORVIN_FEATURES_URL", _FEATURES_SERVER_PROD)


def _features_server() -> str:
    if _TEST_MODE_SNAPSHOT == "1":
        return _FEATURES_URL_SNAPSHOT
    return _FEATURES_SERVER_PROD


# IBC signature trust anchor. The server reuses its existing Ed25519
# session-signing keypair (kid "ibc-vN") instead of a separate RS256 trust
# anchor — see ADR-0145 "Relationship to ADR-0153" / deviation note. This is
# the SAME DER-b64 public key as SESSION_SERVER_KEY_RING["sess-v1"] in
# operator/license/validator.py; kept as a local literal (public key, not a
# secret) so this module has no import dependency on operator/license/.
# Rotation checklist: whenever validator.py's SESSION_SERVER_KEY_RING gains a
# new kid, add the same entry here too (see validator.py's rotation comment).
_IBC_TRUST_KEY_RING: dict[str, str] = {
    "sess-v1": "MCowBQYDK2VwAyEAGd/9rorTQ+kWfYsablfa4eD6RYl1MKhANivIRjozCK4=",
}


class IBCError(RuntimeError):
    """Raised when IBC operations fail (missing key, invalid cert, network error)."""


def instance_id_path() -> Path:
    """Resolved path to the instance_id.json file.

    Honours the ``CORVIN_INSTANCE_ID_PATH`` env override (used by tests
    and multi-instance E2E setups). Otherwise: ``<corvin_home>/global/
    instance_id.json``.
    """
    env = os.environ.get("CORVIN_INSTANCE_ID_PATH")
    if env:
        return Path(env).expanduser()
    return corvin_home() / _GLOBAL_DIR / _INSTANCE_ID_FILE


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _validate_mode_strict(path: Path) -> None:
    """Raise on world-readable identity file. Identity is non-secret but
    the file may live next to keys later; cheap to enforce now.

    No-op on Windows: NTFS has no POSIX group/other bits, so os.stat().st_mode
    always reports a permissive-looking value there regardless of the file's
    real ACLs, and the os.chmod(0o600) self-heal below cannot narrow it either
    (Windows os.chmod only toggles the read-only attribute) — the check would
    otherwise "self-heal" every single read forever without ever succeeding.
    """
    if sys.platform.startswith("win"):
        return
    file_stat = path.stat()
    if file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        # Self-heal: tighten the mode rather than crash. The contents
        # are not secret, so a one-time fixup is safe and avoids a
        # boot loop on a freshly-cloned repo.
        # Log to stderr so operators notice the violation — a world-readable
        # instance_id.json may have been exfiltrated (ADR-0099 iter-2
        # finding MED-IDENTITY-01).
        print(
            f"[instance_identity] WARNING: {path.name} has group/other "
            f"permissions (mode {oct(file_stat.st_mode & 0o777)}) — "
            "tightening to 0600. Verify A2A audit logs for unexpected "
            "activity from peers that should not know this instance_id.",
            file=sys.stderr, flush=True,
        )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    _validate_mode_strict(path)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    iid = data.get("instance_id")
    if not isinstance(iid, str) or not iid:
        return None
    return data


def _generate(label: str | None = None) -> dict[str, Any]:
    return {
        "instance_id": str(uuid.uuid4()),
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds",
        ),
        "label": label or os.environ.get("CORVIN_INSTANCE_LABEL") or "",
    }


def instance_id_metadata(*, create_if_missing: bool = True) -> dict[str, Any]:
    """Return the full identity record.

    ADR-0052 F10: ``create_if_missing=False`` makes the call fail-closed
    (raises ``InstanceIdentityMissing``) when the file does not exist. Use
    this at runtime after first boot so a deleted file is detected rather
    than silently replaced with a new identity (which would break all
    cloud-side endpoint pins).

    The default ``create_if_missing=True`` preserves the legacy behaviour
    for first-boot and tests.

    Thread-safe: concurrent first-call races are serialised through a
    module-level lock; only one UUID is generated.
    """
    path = instance_id_path()
    with _lock:
        data = _load(path)
        if data is not None:
            return data
        if not create_if_missing:
            _emit_missing_audit(path)
            raise InstanceIdentityMissing(
                f"instance_id.json missing at {path} — cannot attest identity. "
                "Run 'corvin-instance-id show' to create it, or investigate why "
                "the file was deleted."
            )
        data = _generate()
        _atomic_write(path, data)
        return data


def get_instance_id() -> str:
    """Return the local Corvin instance UUID. Stable across restarts."""
    return instance_id_metadata()["instance_id"]


class InstanceIdentityMissing(RuntimeError):
    """Raised when instance_id.json is absent and create_if_missing=False.

    ADR-0052 F10 — fail-closed on missing identity at runtime. The operator
    must run 'corvin-instance-id show' to regenerate, then update all
    cloud-side endpoint pins.
    """


def _emit_missing_audit(path: Path) -> None:
    """Best-effort CRITICAL audit emit when the identity file is missing."""
    try:
        _forge_pkg = path.parents[2] / "operator" / "forge"
        if not _forge_pkg.is_dir():
            here = Path(__file__).resolve()
            for parent in here.parents:
                if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                    _forge_pkg = parent / "operator" / "forge"
                    break
        if str(_forge_pkg) not in sys.path:
            sys.path.insert(0, str(_forge_pkg))
        from forge.security_events import write_event as _we  # type: ignore
        audit_path = path.parent / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        _we(audit_path, "instance_identity.missing",
            severity="CRITICAL",
            details={"expected_path": str(path)[:300]})
    except Exception:  # noqa: BLE001
        pass


def rotate(*, label: str | None = None) -> dict[str, Any]:
    """ADR-0052 F10 — Formal instance identity rotation ceremony.

    Generates a new UUID4, writes it atomically, emits
    ``instance_identity.rotated`` (WARNING) to the audit chain, and
    returns the new record.

    THIS IS A BREAKING OPERATION: all cloud-side endpoints that pin the
    old instance_id will reject responses until they are updated with the
    new value. The function prints a reminder to stdout.

    Callers: ``corvin-instance-id rotate`` CLI only.
    NOT for use by bridge/adapter code — rotation must always be
    operator-initiated.
    """
    path = instance_id_path()
    with _lock:
        old_data = _load(path)
        old_id = (old_data or {}).get("instance_id", "")
        new_data = _generate(label=label)
        _atomic_write(path, new_data)
        _emit_rotation_audit(path, old_id, new_data["instance_id"])
        return new_data


def _emit_rotation_audit(
    id_path: Path, old_id: str, new_id: str
) -> None:
    """Best-effort WARNING audit emit for rotation event."""
    try:
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                forge_pkg = parent / "operator" / "forge"
                if str(forge_pkg) not in sys.path:
                    sys.path.insert(0, str(forge_pkg))
                break
        from forge.security_events import write_event as _we  # type: ignore
        audit_path = id_path.parent / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        _we(audit_path, "instance_identity.rotated",
            severity="WARNING",
            details={
                "old_id_prefix": old_id[:8] if old_id else "",
                "new_id_prefix": new_id[:8],
            })
    except Exception:  # noqa: BLE001
        pass


def set_label(label: str) -> dict[str, Any]:
    """Update the human-readable label on the identity record.

    The instance_id itself is immutable; only the label is mutable. A
    common workflow is to assign a label after first boot
    (``corvin-instance-id label "fsn1-prod"``) so operators can spot the
    instance in audit logs without leaking infra detail in the UUID.
    """
    if not isinstance(label, str):
        raise TypeError("label must be a str")
    if len(label) > 64:
        raise ValueError("label too long (max 64 chars)")
    # Disallow control chars; keep operator-readable strings clean.
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in label):
        raise ValueError("label contains control characters")
    path = instance_id_path()
    with _lock:
        data = _load(path)
        if data is None:
            data = _generate(label=label)
        else:
            data["label"] = label
        _atomic_write(path, data)
        return data


def instance_key_path() -> Path:
    env = os.environ.get("CORVIN_INSTANCE_KEY_PATH")
    if env:
        return Path(env).expanduser()
    return corvin_home() / _GLOBAL_DIR / _INSTANCE_KEY_FILE


def instance_cert_path() -> Path:
    env = os.environ.get("CORVIN_INSTANCE_CERT_PATH")
    if env:
        return Path(env).expanduser()
    return corvin_home() / _GLOBAL_DIR / _IBC_FILE


def ensure_instance_key() -> Path:
    """Generate Ed25519 keypair if absent. Returns path to private key (mode 0600).

    Idempotent — safe to call on every boot.
    CI lint: MUST NOT import anthropic.
    """
    if not _CRYPTO_OK:
        raise IBCError(
            "cryptography package not installed — cannot generate Ed25519 keypair. "
            "Run: pip install cryptography"
        )
    key_path = instance_key_path()
    pubkey_path = key_path.parent / _INSTANCE_PUBKEY_FILE
    with _lock:
        if key_path.exists():
            # R2 finding: validate (and self-heal) the private-key file mode on
            # every access — perms can drift after creation (backup restore,
            # manual cp, permissive umask). A group/other-readable Ed25519 key
            # lets a co-located UID forge instance_attestation signatures with no
            # detection. Re-assert 0600 + WARN so drift is corrected at first use
            # (boot self-test adds the CRITICAL gate). Mirrors the strict-mode
            # treatment of instance_seed.key / actor_keypair.json.
            # No-op on Windows — see _validate_mode_strict for why st_mode/chmod
            # cannot express POSIX group/other bits on NTFS.
            if not sys.platform.startswith("win"):
                try:
                    _mode = key_path.stat().st_mode
                    if _mode & 0o077:
                        os.chmod(key_path, 0o600)
                        import logging as _lg
                        _lg.getLogger("corvin.instance").warning(
                            "instance_key.pem mode 0o%03o too permissive — reset to 0600",
                            _mode & 0o777,
                        )
                except OSError:
                    pass
            return key_path
        key_path.parent.mkdir(parents=True, exist_ok=True)
        privkey = Ed25519PrivateKey.generate()
        priv_pem = privkey.private_bytes(
            encoding=_serialization.Encoding.PEM,
            format=_serialization.PrivateFormat.PKCS8,
            encryption_algorithm=_serialization.NoEncryption(),
        )
        pub_pem = privkey.public_key().public_bytes(
            encoding=_serialization.Encoding.PEM,
            format=_serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        # Write private key atomically with mode 0600 from creation (no TOCTOU window).
        # Using os.open with O_CREAT|O_WRONLY ensures mode is set before any data is
        # written, eliminating the window between write_bytes() and os.chmod().
        tmp = key_path.with_suffix(".tmp")
        _fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(_fd, priv_pem)
        finally:
            os.close(_fd)
        os.replace(tmp, key_path)
        pubkey_path.write_bytes(pub_pem)
        os.chmod(pubkey_path, 0o644)
        _audit_ibc("instance.key_rotated", "WARNING", {})
    return key_path


def get_instance_pubkey_b64() -> str:
    """Return base64url-encoded Ed25519 public key (raw 32 bytes).

    Suitable for inclusion in IBC bind requests and JWT fields.
    """
    if not _CRYPTO_OK:
        raise IBCError("cryptography package not installed")
    key_path = ensure_instance_key()
    with _lock:
        priv_pem = key_path.read_bytes()
    privkey = _serialization.load_pem_private_key(priv_pem, password=None)
    raw = privkey.public_key().public_bytes(
        encoding=_serialization.Encoding.Raw,
        format=_serialization.PublicFormat.Raw,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign_payload(payload: bytes) -> str:
    """Sign payload with the instance Ed25519 private key.

    Returns base64url-encoded signature (no padding). The payload
    must be deterministic and include all envelope fields that must
    be bound (task_id, origin_id, issued_at, nonce, instruction hash).
    """
    if not _CRYPTO_OK:
        raise IBCError("cryptography package not installed")
    key_path = ensure_instance_key()
    with _lock:
        priv_pem = key_path.read_bytes()
    privkey = _serialization.load_pem_private_key(priv_pem, password=None)
    sig = privkey.sign(payload)
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def verify_instance_sig(sig_b64: str, payload: bytes, pubkey_b64: str) -> bool:
    """Verify an Ed25519 signature against a base64url-encoded public key.

    Returns False on any verification failure (never raises).
    Used by the A2A receiver for instance_attestation verification.
    """
    if not _CRYPTO_OK:
        return False
    try:
        # Re-pad base64url
        sig = base64.urlsafe_b64decode(sig_b64 + "==")
        raw_pub = base64.urlsafe_b64decode(pubkey_b64 + "==")
        pubkey = Ed25519PublicKey.from_public_bytes(raw_pub)
        pubkey.verify(sig, payload)
        return True
    except Exception:  # noqa: BLE001
        return False


def build_canonical_payload(
    task_id: str,
    origin_id: str,
    issued_at: int,
    nonce: str,
    instruction: str,
) -> bytes:
    """Build the deterministic payload for Ed25519 signing/verification.

    canonical = SHA-256(task_id:origin_id:issued_at:nonce:SHA-256(instruction_utf8))
    """
    instruction_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
    inner = f"{task_id}:{origin_id}:{issued_at}:{nonce}:{instruction_hash}"
    return hashlib.sha256(inner.encode("utf-8")).digest()


def _b64url_pad_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * ((4 - len(s) % 4) % 4))


def _decode_corvin_claims_unverified(token: str) -> dict:
    """Decode a CORVIN-<header>.<payload>.<sig> token's payload WITHOUT
    verifying the signature. Raises ValueError on malformed input."""
    if not isinstance(token, str) or not token.startswith("CORVIN-"):
        raise ValueError("not a CORVIN-format token")
    parts = token[len("CORVIN-"):].split(".")
    if len(parts) != 3:
        raise ValueError("malformed CORVIN token: expected 3 segments")
    payload = json.loads(_b64url_pad_decode(parts[1]))
    if not isinstance(payload, dict):
        raise ValueError("CORVIN token payload is not a JSON object")
    return payload


def _verify_ibc_signature(ibc_token: str) -> dict:
    """Verify a CORVIN-format IBC's Ed25519 signature and expiry.

    Returns the decoded claims dict on success. Raises IBCError on any
    failure (bad format, bad signature, expired, unknown kid).
    """
    if not _CRYPTO_OK:
        raise IBCError("cryptography package not installed")
    if not isinstance(ibc_token, str) or not ibc_token.startswith("CORVIN-"):
        raise IBCError("IBC token format invalid: expected CORVIN-<header>.<payload>.<sig>")
    parts = ibc_token[len("CORVIN-"):].split(".")
    if len(parts) != 3:
        raise IBCError("IBC token format invalid: expected 3 segments after CORVIN-")
    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64url_pad_decode(header_b64))
        kid = str(header.get("kid", ""))
    except Exception as exc:  # noqa: BLE001
        raise IBCError(f"IBC header malformed: {exc}") from exc

    # A real IBC is always signed with an ``ibc-`` kid (server:
    # issue_ibc_jwt → _ibc_kid_for(CURRENT_SESSION_KID)). The sess-/lic-/ibc-
    # token classes share ONE Ed25519 keypair, so only the ``ibc-`` kid may be
    # resolved from the trust ring — accepting a bare ``sess-``/``lic-`` kid
    # would let another token class signed by the same key verify as an IBC
    # (defense-in-depth; the test-mode env override below is unaffected).
    if kid.startswith("ibc-"):
        lookup_kid = "sess-" + kid[len("ibc-"):]
        pubkey_der_b64 = _IBC_TRUST_KEY_RING.get(lookup_kid)
    else:
        pubkey_der_b64 = None
    if not pubkey_der_b64:
        env_key = os.environ.get("CORVIN_IBC_PUBKEY_DER_B64")
        if env_key and _TEST_MODE_SNAPSHOT == "1":
            pubkey_der_b64 = env_key
        else:
            raise IBCError(f"IBC signed with unknown/non-IBC kid={kid!r} — cannot verify")

    try:
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        pub = load_der_public_key(base64.b64decode(pubkey_der_b64))
        sig = _b64url_pad_decode(sig_b64)
        signing_input = f"{header_b64}.{payload_b64}".encode()
        pub.verify(sig, signing_input)
    except Exception as exc:  # noqa: BLE001
        raise IBCError(f"IBC signature invalid: {exc}") from exc

    try:
        claims = json.loads(_b64url_pad_decode(payload_b64))
    except Exception as exc:  # noqa: BLE001
        raise IBCError(f"IBC payload malformed: {exc}") from exc

    # Pin the token class and issuer (parity with the server's
    # auth._verify_corvin_token, which pins both). Without this a same-key
    # token of another class that happened to carry a sub+instance_pubkey
    # shape could be replayed as an IBC.
    if claims.get("type") != "instance_binding":
        raise IBCError(f"IBC has wrong type={claims.get('type')!r} — expected instance_binding")
    if claims.get("iss") != "corvinlabs.io":
        raise IBCError(f"IBC has wrong iss={claims.get('iss')!r} — expected corvinlabs.io")

    exp = claims.get("exp")
    if exp is not None and exp < _dt.datetime.now(_dt.timezone.utc).timestamp():
        raise IBCError("IBC has expired")

    return claims


def _authenticated_features_request(path: str, body: dict) -> dict:
    """POST to Corvin-Features with the standard license + HMAC auth headers.

    Reuses the SAME activation credentials (~/.config/corvin-voice/features.json,
    written by 'corvin-license activate') and helper functions as
    operator/license/session_refresh.py, rather than re-implementing the HMAC
    scheme a second time. Raises IBCError on any failure.
    """
    try:
        here = Path(__file__).resolve()
        lic_dir = None
        for parent in here.parents:
            candidate = parent / "operator" / "license"
            if candidate.is_dir():
                lic_dir = candidate
                break
        if lic_dir is None:
            raise ImportError("operator/license directory not found")
        if str(lic_dir) not in sys.path:
            sys.path.insert(0, str(lic_dir))
        import session_refresh as _sr  # type: ignore[import-not-found]
    except ImportError as exc:
        raise IBCError(f"Cannot import session_refresh: {exc}") from exc

    features = _sr.load_features()
    if features is None:
        raise IBCError(
            "No activated license found — run 'corvin-license activate <key>' first."
        )
    api_key = features.get("api_key", "")
    if not api_key:
        raise IBCError("features.json missing api_key — re-run license activation.")
    license_token = _sr._find_license_token()
    if not license_token:
        raise IBCError(
            "No license token found — run 'corvin-license activate <key>' first."
        )

    body_bytes = json.dumps(body).encode("utf-8")
    ts, sig = _sr._sign_request(body_bytes, api_key)
    try:
        device_fp = _sr._get_device_fp()
    except Exception:  # noqa: BLE001
        device_fp = ""

    url = f"{_features_server()}{path}"
    req = urllib.request.Request(url, data=body_bytes, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {license_token}")
    req.add_header("X-Corvin-Ts", ts)
    req.add_header("X-Corvin-Sig", sig)
    if device_fp:
        req.add_header("X-Corvin-Device-Fp", device_fp)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise IBCError(f"Corvin-Features request failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise IBCError(f"Corvin-Features request failed: {exc.reason}") from exc

    try:
        resp_data = json.loads(resp_body)
    except ValueError as exc:
        raise IBCError(f"Corvin-Features response was not valid JSON: {exc}") from exc
    if not isinstance(resp_data, dict):
        raise IBCError("Corvin-Features response was not a JSON object")
    return resp_data


def _store_cert(ibc_token: str) -> None:
    cert_path = instance_cert_path()
    with _lock:
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cert_path.with_suffix(".tmp")
        tmp.write_text(ibc_token, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, cert_path)


def bind_instance() -> dict:
    """Bind this instance to the caller's activated Corvin Labs license.

    Requires the installation to already be activated ('corvin-license
    activate <key>' — see operator/license/session_refresh.py). Authenticates
    to Corvin-Features with the same license token + HMAC api_key as every
    other authenticated endpoint; no separate SesT concept.

    Returns the decoded IBC claims dict on success. Raises IBCError on any
    failure.
    """
    instance_id = get_instance_id()
    pubkey_b64 = get_instance_pubkey_b64()

    resp_data = _authenticated_features_request(
        "/v1/instance/bind",
        {"instance_id": instance_id, "instance_pubkey": pubkey_b64},
    )
    ibc_token = resp_data.get("ibc")
    if not ibc_token:
        raise IBCError("IBC bind response missing 'ibc' field")

    decoded = _verify_ibc_signature(ibc_token)
    if decoded.get("sub") != instance_id:
        raise IBCError(
            f"IBC sub mismatch: expected {instance_id!r}, got {decoded.get('sub')!r}"
        )
    # R1 finding (carried over from the original design): also verify the IBC
    # binds OUR public key. Without this, a signature-valid IBC issued for a
    # different key (issuer bug, or a swapped response) would be silently
    # persisted — a latent identity mismatch.
    if decoded.get("instance_pubkey") != pubkey_b64:
        raise IBCError("IBC instance_pubkey does not bind this instance's public key")

    _store_cert(ibc_token)
    _audit_ibc(
        "instance.ibc_issued", "INFO",
        {"ibc_jti": (decoded.get("jti", "") or "")[:16]},
    )
    return decoded


def get_ibc() -> dict | None:
    """Load and validate the current IBC. Returns None if absent or expired.

    Local read only — the signature was already verified at bind time and
    the file is trusted at mode 0600; this only checks expiry.
    """
    cert_path = instance_cert_path()
    if not cert_path.exists():
        return None
    try:
        ibc_token = cert_path.read_text(encoding="utf-8").strip()
        claims = _decode_corvin_claims_unverified(ibc_token)
        exp = claims.get("exp")
        if exp is not None and exp < _dt.datetime.now(_dt.timezone.utc).timestamp():
            _audit_ibc("instance.ibc_expired", "WARNING", {})
            return None
        return claims
    except Exception:  # noqa: BLE001
        return None


def get_ibc_jwt() -> str | None:
    """Return the raw IBC JWT string for embedding in A2A envelopes.

    Returns None if the IBC's JTI is confirmed revoked. Uses
    ``revocation_status_cached()`` — a *cache-only* CRL read — rather than
    ``is_ibc_revoked()``, which is allowed to make a live network fetch when
    its 24h cache has expired. This function is the single chokepoint every
    outbound instance_attestation goes through, so it must never block a
    send on a Corvin Labs round-trip; the CRL cache is instead refreshed out
    of band (``corvin-id check-revocation`` or an operator-scheduled job).
    A confirmed-revoked cache entry still actually stops the send from
    presenting the revoked cert, per ADR-0145 ("revoked IBC must block, not
    just warn").
    """
    cert_path = instance_cert_path()
    if not cert_path.exists():
        return None
    try:
        ibc = cert_path.read_text(encoding="utf-8").strip()
        claims = _decode_corvin_claims_unverified(ibc)
        exp = claims.get("exp")
        if exp is not None and exp < _dt.datetime.now(_dt.timezone.utc).timestamp():
            return None
        if revocation_status_cached() == "revoked":
            return None
        return ibc
    except Exception:  # noqa: BLE001
        return None


def _read_tpm_pcr0() -> str:
    """Best-effort read of TPM PCR[0] (SHA-256 bank), Linux only.

    Tries the kernel sysfs exposure first (no subprocess, no extra
    package required); falls back to ``tpm2_pcrread`` if the tools are
    installed. Returns "" on any failure — a machine without a TPM (or
    without operator-granted /dev/tpm0 access) must not break hardware
    fingerprinting, only make it slightly less specific.
    """
    if not sys.platform.startswith("linux"):
        return ""
    sysfs_candidates = (
        "/sys/class/tpm/tpm0/pcr-sha256/0",
        "/sys/class/tpm/tpm0/pcrs",  # older kernels: multi-line "PCR-00: xx xx ..."
    )
    for candidate in sysfs_candidates:
        try:
            text = Path(candidate).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        if candidate.endswith("pcrs"):
            for line in text.splitlines():
                if line.startswith("PCR-00:"):
                    return line.split(":", 1)[1].strip().replace(" ", "")[:64]
            continue
        return text.replace(" ", "")[:64]
    try:
        import subprocess
        out = subprocess.run(
            ["tpm2_pcrread", "sha256:0"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                line = line.strip()
                if line.startswith("0 "):
                    return line.split(":", 1)[-1].strip().replace(" ", "")[:64]
    except Exception:  # noqa: BLE001
        pass
    return ""


def compute_hardware_fp() -> str:
    """Compute a stable hardware fingerprint (M3 feature, best-effort).

    Returns SHA-256 hex of: cpu_brand:mac:disk_serial:tpm_pcr0
    Falls back to empty string on any failure (non-fatal, opt-in feature).

    The TPM PCR[0] component (firmware/bootloader measurement) is
    included when available so the fingerprint also detects a VM clone
    or disk image restored onto different physical firmware, not just a
    changed NIC/disk. Its absence (no TPM, no access) degrades the
    fingerprint's specificity but never breaks it — this must stay
    best-effort per ADR-0145 (hardware binding is opt-in, not a boot gate).
    """
    import platform
    parts = []

    # CPU brand
    try:
        cpu = platform.processor() or ""
        parts.append(cpu[:64])
    except Exception:  # noqa: BLE001
        parts.append("")

    # Stable MAC address (lowest non-loopback)
    try:
        import uuid as _uuid_mod
        mac_int = _uuid_mod.getnode()
        parts.append(f"{mac_int:012x}")
    except Exception:  # noqa: BLE001
        parts.append("")

    # First disk serial (Linux only, best-effort)
    disk_serial = ""
    try:
        import glob as _glob
        for block_dev in sorted(_glob.glob("/sys/block/*/device/serial")):
            serial = Path(block_dev).read_text().strip()
            if serial:
                disk_serial = serial[:32]
                break
    except Exception:  # noqa: BLE001
        pass
    parts.append(disk_serial)

    # TPM PCR[0] — best-effort, "" on any unavailability
    parts.append(_read_tpm_pcr0())

    raw = ":".join(parts)
    if not any(parts):
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def bind_hardware() -> dict:
    """Tether the current IBC to this machine's hardware fingerprint (M3).

    Requires an existing IBC (call ``bind_instance`` first) and an activated
    license (same auth as ``bind_instance``). Signs the freshly computed
    hardware fingerprint with the instance Ed25519 key and POSTs it; the
    server re-issues the IBC with a ``hardware_fp`` claim added, which this
    function verifies and stores exactly like ``bind_instance`` does.

    This is an explicit, operator-initiated, opt-in step — never run
    automatically at boot or on every spawn (ADR-0145 "what not to do").

    Raises IBCError on any failure (no IBC yet, empty fingerprint,
    network error, signature mismatch).
    """
    current = get_ibc()
    if current is None:
        raise IBCError(
            "No valid IBC found — run 'corvin-id init' (license bind) before "
            "'corvin-id bind-hardware'."
        )

    hardware_fp = compute_hardware_fp()
    if not hardware_fp:
        raise IBCError(
            "Could not compute a hardware fingerprint on this machine "
            "(no CPU/MAC/disk signal available) — hardware binding unsupported here."
        )

    instance_id = get_instance_id()
    jti = current.get("jti", "")
    sig = sign_payload(hardware_fp.encode("utf-8"))

    resp_data = _authenticated_features_request(
        "/v1/instance/bind-hardware",
        {
            "instance_id": instance_id,
            "ibc_jti": jti,
            "hardware_fp": hardware_fp,
            "hardware_fp_sig": sig,
        },
    )
    ibc_token = resp_data.get("ibc")
    if not ibc_token:
        raise IBCError("Hardware bind response missing 'ibc' field")

    decoded = _verify_ibc_signature(ibc_token)
    if decoded.get("sub") != instance_id:
        raise IBCError(
            f"IBC sub mismatch after hardware bind: expected {instance_id!r}, "
            f"got {decoded.get('sub')!r}"
        )
    if decoded.get("hardware_fp") != hardware_fp:
        raise IBCError("Re-issued IBC does not carry the hardware fingerprint we sent")

    _store_cert(ibc_token)
    _audit_ibc(
        "instance.hardware_bound", "INFO",
        {"ibc_jti": (decoded.get("jti", "") or "")[:16]},
    )
    return decoded


def check_hardware_binding() -> dict:
    """Compare the currently computed hardware fingerprint against the IBC claim.

    Explicit, operator-invoked check only (``corvin-id check-hardware`` or
    equivalent) — NOT run automatically on every spawn, per ADR-0145: a
    laptop's MAC can legitimately change (new dock, disabled NIC) and a
    boot-time hard-fail would be a denial-of-service against the operator's
    own machine. Callers decide what to do with the result.

    Returns {"bound": bool, "matches": bool | None, "current_fp": str,
    "claimed_fp": str | None}. ``matches`` is None when the IBC has no
    hardware_fp claim at all (hardware binding never performed).
    """
    ibc = get_ibc()
    current_fp = compute_hardware_fp()
    if ibc is None:
        return {"bound": False, "matches": None, "current_fp": current_fp, "claimed_fp": None}
    claimed_fp = ibc.get("hardware_fp")
    if not claimed_fp:
        return {"bound": False, "matches": None, "current_fp": current_fp, "claimed_fp": None}
    matches = bool(current_fp) and current_fp == claimed_fp
    if not matches:
        _audit_ibc("instance.ibc_hardware_mismatch", "WARNING", {"reason": "fp_mismatch"})
    return {"bound": True, "matches": matches, "current_fp": current_fp, "claimed_fp": claimed_fp}


# ── M3: Certificate Revocation List (CRL) ──────────────────────────────────────

_CRL_CACHE_FILE = "ibc_crl_cache.json"
_CRL_TTL_SECONDS = 24 * 3600
_CRL_GRACE_SECONDS = 7 * 24 * 3600  # serve a stale cache up to 7 days offline


def _crl_cache_path() -> Path:
    env = os.environ.get("CORVIN_CRL_CACHE_PATH")
    if env:
        return Path(env).expanduser()
    return corvin_home() / _GLOBAL_DIR / _CRL_CACHE_FILE


def _fetch_crl_remote() -> list[str]:
    url = f"{_features_server()}/v1/instance/revoked"
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read()
    import json as _json
    data = _json.loads(body)
    revoked = data.get("revoked_jti", [])
    if not isinstance(revoked, list):
        raise IBCError("CRL response malformed: 'revoked_jti' is not a list")
    return [str(x) for x in revoked]


def fetch_revocation_list(*, force_refresh: bool = False) -> list[str]:
    """Fetch the revoked-JTI list, with a 24h local cache and offline grace.

    Never raises on network failure: falls back to the last cached list
    (even if past its 24h TTL, up to a 7-day grace window) so a temporary
    outage does not strand an operator without an answer. Returns an empty
    list only when there is no cache at all AND the network is unreachable
    — in that case revocation status is simply "unknown", never "revoked"
    (fail-open on the network dimension; fail-closed only on an actually
    confirmed revocation, per ADR-0145).
    """
    cache_path = _crl_cache_path()
    now = _dt.datetime.now(_dt.timezone.utc).timestamp()

    cached: dict[str, Any] | None = None
    if cache_path.exists():
        try:
            import json as _json
            cached = _json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cached = None

    if not force_refresh and cached is not None:
        fetched_at = cached.get("fetched_at", 0)
        if now - fetched_at < _CRL_TTL_SECONDS:
            return list(cached.get("revoked_jti", []))

    try:
        revoked = _fetch_crl_remote()
    except Exception:  # noqa: BLE001
        if cached is not None and now - cached.get("fetched_at", 0) < _CRL_GRACE_SECONDS:
            return list(cached.get("revoked_jti", []))
        return []

    payload = {"fetched_at": now, "revoked_jti": revoked}
    try:
        _atomic_write(cache_path, payload)
    except OSError:
        pass  # cache write is best-effort; the fetch itself still succeeded
    return revoked


def is_ibc_revoked(*, force_refresh: bool = False) -> bool:
    """True only if the current IBC's JTI is confirmed present on the CRL.

    Any ambiguity (no IBC, no JTI, CRL unreachable with no cache) resolves
    to False — this function answers "is this instance confirmed revoked",
    not "can we prove it isn't". Emits ``instance.ibc_revoked`` (CRITICAL)
    when a revocation is confirmed.
    """
    ibc = get_ibc()
    if ibc is None:
        return False
    jti = ibc.get("jti", "")
    if not jti:
        return False
    revoked_list = fetch_revocation_list(force_refresh=force_refresh)
    if jti in revoked_list:
        _audit_ibc("instance.ibc_revoked", "CRITICAL", {"ibc_jti": jti[:16], "reason": "crl_match"})
        return True
    return False


def revocation_status_cached() -> str:
    """Cache-only revocation read — never makes a network call.

    For UI surfaces (e.g. the console Dashboard) that must render fast and
    must not block a page load on network I/O. Returns one of
    ``"revoked"``, ``"clean"``, or ``"unknown"`` (no IBC, no cache yet, or
    cache older than the 7-day grace window).
    """
    ibc = get_ibc()
    if ibc is None:
        return "unknown"
    jti = ibc.get("jti", "")
    if not jti:
        return "unknown"
    cache_path = _crl_cache_path()
    if not cache_path.exists():
        return "unknown"
    try:
        import json as _json
        cached = _json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "unknown"
    now = _dt.datetime.now(_dt.timezone.utc).timestamp()
    if now - cached.get("fetched_at", 0) > _CRL_GRACE_SECONDS:
        return "unknown"
    return "revoked" if jti in cached.get("revoked_jti", []) else "clean"


__all__ = [
    "IBCError",
    "InstanceIdentityMissing",
    "bind_hardware",
    "bind_instance",
    "build_canonical_payload",
    "check_hardware_binding",
    "compute_hardware_fp",
    "ensure_instance_key",
    "fetch_revocation_list",
    "get_ibc",
    "get_ibc_jwt",
    "get_instance_id",
    "get_instance_pubkey_b64",
    "instance_cert_path",
    "instance_id_metadata",
    "instance_id_path",
    "instance_key_path",
    "is_ibc_revoked",
    "revocation_status_cached",
    "rotate",
    "set_label",
    "sign_payload",
    "verify_instance_sig",
]
