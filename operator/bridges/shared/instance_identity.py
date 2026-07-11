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
    import jwt as _jwt
    _JWT_OK = True
except ImportError:
    _JWT_OK = False

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
_IBC_BIND_URL = "https://api.corvin-labs.com/v1/instance/bind"


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


def bind_instance(sest_token: str, license_fp: str) -> dict:
    """Call the Corvin Labs IBC bind endpoint and store the returned IBC JWT.

    URL override for testing: CORVIN_IBC_BIND_URL env var.

    Returns the decoded IBC payload dict on success.
    Raises IBCError on any failure.
    """
    if not _JWT_OK:
        raise IBCError("pyjwt not installed — cannot parse IBC JWT")

    instance_id = get_instance_id()
    pubkey_b64 = get_instance_pubkey_b64()

    bind_url = os.environ.get("CORVIN_IBC_BIND_URL", _IBC_BIND_URL)

    body = {
        "instance_id": instance_id,
        "instance_pubkey": pubkey_b64,
        "license_fingerprint": license_fp,
        "sest_token": sest_token,
    }
    import json as _json
    body_bytes = _json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        bind_url,
        data=body_bytes,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read()
    except urllib.error.HTTPError as exc:
        raise IBCError(f"IBC bind request failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise IBCError(f"IBC bind request failed: {exc.reason}") from exc

    try:
        resp_data = _json.loads(resp_body)
    except ValueError as exc:
        raise IBCError(f"IBC bind response was not valid JSON: {exc}") from exc
    if not isinstance(resp_data, dict):
        raise IBCError("IBC bind response was not a JSON object")
    ibc_jwt = resp_data.get("ibc")
    if not ibc_jwt:
        raise IBCError("IBC bind response missing 'ibc' field")

    # Verify the IBC RS256 signature against the embedded a2a_network_pubkey.pem
    _verify_ibc_signature(ibc_jwt)

    # Decode and validate
    decoded = _jwt.decode(ibc_jwt, options={"verify_signature": False})
    if decoded.get("sub") != instance_id:
        raise IBCError(
            f"IBC sub mismatch: expected {instance_id!r}, got {decoded.get('sub')!r}"
        )
    # R1 finding: also verify the IBC binds OUR public key. Without this, a
    # signature-valid IBC issued for a different key (issuer bug, or a swapped
    # response) would be silently persisted — a latent identity mismatch (A2A
    # attestation would later present a cert whose bound key we don't hold).
    # The IBC carries an `instance_pubkey` claim (ADR-0145); it must equal ours.
    if decoded.get("instance_pubkey") != pubkey_b64:
        raise IBCError(
            "IBC instance_pubkey does not bind this instance's public key"
        )

    # Store
    cert_path = instance_cert_path()
    with _lock:
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cert_path.with_suffix(".tmp")
        tmp.write_text(ibc_jwt, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, cert_path)

    _audit_ibc(
        "instance.ibc_issued", "INFO",
        {"ibc_jti": (decoded.get("jti", "") or "")[:16]},
    )
    return decoded


def _verify_ibc_signature(ibc_jwt: str) -> None:
    """Verify IBC RS256 signature against a2a_network_pubkey.pem.

    Raises IBCError if signature is invalid.
    """
    if not _JWT_OK:
        raise IBCError("pyjwt not installed")

    # Find the pubkey relative to this module
    here = Path(__file__).resolve()
    pubkey_pem = None
    for parent in here.parents:
        candidate = parent / "operator" / "license" / "a2a_network_pubkey.pem"
        if candidate.exists():
            pubkey_pem = candidate.read_text()
            break
        candidate2 = parent / "license" / "a2a_network_pubkey.pem"
        if candidate2.exists():
            pubkey_pem = candidate2.read_text()
            break

    if pubkey_pem is None:
        # Fallback: env override for tests
        env_key = os.environ.get("CORVIN_IBC_PUBKEY_PEM")
        if env_key:
            pubkey_pem = env_key
        else:
            raise IBCError("Cannot locate a2a_network_pubkey.pem — IBC signature unverifiable")

    try:
        _jwt.decode(
            ibc_jwt,
            pubkey_pem,
            algorithms=["RS256"],
            options={"verify_exp": True},
        )
    except _jwt.ExpiredSignatureError as exc:
        raise IBCError("IBC has expired") from exc
    except _jwt.InvalidTokenError as exc:
        raise IBCError(f"IBC signature invalid: {exc}") from exc


def get_ibc() -> dict | None:
    """Load and validate the current IBC. Returns None if absent or expired."""
    cert_path = instance_cert_path()
    if not cert_path.exists():
        return None
    if not _JWT_OK:
        return None
    try:
        ibc_jwt = cert_path.read_text(encoding="utf-8").strip()
        # Quick expiry check without signature verification (signature was
        # already verified at bind time; we trust the local file's mode 0600)
        decoded = _jwt.decode(
            ibc_jwt,
            options={"verify_signature": False, "verify_exp": True},
        )
        return decoded
    except _jwt.ExpiredSignatureError:
        _audit_ibc("instance.ibc_expired", "WARNING", {})
        return None
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
        # Quick expiry guard
        if _JWT_OK:
            _jwt.decode(ibc, options={"verify_signature": False, "verify_exp": True})
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


_HARDWARE_BIND_URL = "https://api.corvin-labs.com/v1/instance/bind-hardware"


def bind_hardware() -> dict:
    """Tether the current IBC to this machine's hardware fingerprint (M3).

    Requires an existing IBC (call ``bind_instance`` first). Signs the
    freshly computed hardware fingerprint with the instance Ed25519 key
    and POSTs it alongside the current IBC JTI; the server re-issues the
    IBC with a ``hardware_fp`` claim added, which this function verifies
    and stores exactly like ``bind_instance`` does.

    This is an explicit, operator-initiated, opt-in step — never run
    automatically at boot or on every spawn (ADR-0145 "what not to do").

    Raises IBCError on any failure (no IBC yet, empty fingerprint,
    network error, signature mismatch).
    """
    if not _JWT_OK:
        raise IBCError("pyjwt not installed — cannot parse IBC JWT")

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

    body = {
        "instance_id": instance_id,
        "ibc_jti": jti,
        "hardware_fp": hardware_fp,
        "hardware_fp_sig": sig,
    }
    import json as _json
    body_bytes = _json.dumps(body).encode("utf-8")

    bind_url = os.environ.get("CORVIN_HARDWARE_BIND_URL", _HARDWARE_BIND_URL)
    req = urllib.request.Request(
        bind_url,
        data=body_bytes,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read()
    except urllib.error.HTTPError as exc:
        raise IBCError(f"Hardware bind request failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise IBCError(f"Hardware bind request failed: {exc.reason}") from exc

    try:
        resp_data = _json.loads(resp_body)
    except ValueError as exc:
        raise IBCError(f"Hardware bind response was not valid JSON: {exc}") from exc
    if not isinstance(resp_data, dict):
        raise IBCError("Hardware bind response was not a JSON object")
    ibc_jwt = resp_data.get("ibc")
    if not ibc_jwt:
        raise IBCError("Hardware bind response missing 'ibc' field")

    _verify_ibc_signature(ibc_jwt)
    decoded = _jwt.decode(ibc_jwt, options={"verify_signature": False})
    if decoded.get("sub") != instance_id:
        raise IBCError(
            f"IBC sub mismatch after hardware bind: expected {instance_id!r}, "
            f"got {decoded.get('sub')!r}"
        )
    if decoded.get("hardware_fp") != hardware_fp:
        raise IBCError("Re-issued IBC does not carry the hardware fingerprint we sent")

    cert_path = instance_cert_path()
    with _lock:
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cert_path.with_suffix(".tmp")
        tmp.write_text(ibc_jwt, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, cert_path)

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

_CRL_URL = "https://api.corvin-labs.com/v1/instance/revoked"
_CRL_CACHE_FILE = "ibc_crl_cache.json"
_CRL_TTL_SECONDS = 24 * 3600
_CRL_GRACE_SECONDS = 7 * 24 * 3600  # serve a stale cache up to 7 days offline


def _crl_cache_path() -> Path:
    env = os.environ.get("CORVIN_CRL_CACHE_PATH")
    if env:
        return Path(env).expanduser()
    return corvin_home() / _GLOBAL_DIR / _CRL_CACHE_FILE


def _fetch_crl_remote() -> list[str]:
    url = os.environ.get("CORVIN_CRL_URL", _CRL_URL)
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
