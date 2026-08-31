"""Key-Derived Feature Lattice (KDFL) — ADR-0154 M1.

Feature configurations and resource limits can be stored *encrypted at rest*
using keys derived from the active license + instance_id via HKDF-SHA256.
Wrong license → wrong derived key → ``cryptography.exceptions.InvalidTag`` on
read, which is indistinguishable from disk corruption or config tampering.

This module also exposes the shared key-derivation primitives that the other
ADR-0154 mechanisms reuse so that there is exactly one HKDF tree in the system:

  * ``session_lic_proof()``  — M3 Session-Derived License Proof primitive
  * ``lic_constant()``       — M5 Structural Embedding constant
  * ``feature_root_key()``   — the root key all sub-keys derive from

Architecture note (forced deviation from the ADR pseudocode)
------------------------------------------------------------
ADR-0154 writes ``license_key_bytes`` as the HKDF input. The running system
keeps **no raw license-key bytes** — ``validator._ACTIVE_LICENSE`` holds only
the *verified claims dict*, and the free tier holds nothing at all. The only
license-bound key material available at runtime is the **signed JWT token
string** (which embeds the Ed25519/RSA signature that only CorvinLabs can
produce). We therefore derive the root key from the token via SHA-256 — the
exact pattern already used by ``chain_dna.derive_seed_paid()`` (ADR-0132). This
keeps the OTA lattice consistent with the existing License-Seeded Audit DNA.

Free-tier safety (load-bearing — CLAUDE.md: "Don't gate Apache-core on the
license")
----------------------------------------------------------------------------
An Apache-core install runs with **no license**. In that state the root key
falls back to a *publicly-known* constant (mirroring
``chain_dna.derive_seed_free()``). Consequences:

  * Free-tier feature configs are encrypted with a public key — i.e. not secret,
    which is correct: the free tier has no paid feature config to protect.
  * ``session_lic_proof`` / ``lic_constant`` are *stable* across the process
    lifetime, so M3 sessions stay valid and M5 path-gate decisions stay correct
    on free tier. Nothing breaks when no license is present.

When a paid license is applied/removed at runtime the root key changes, which
intentionally invalidates M3 sessions and flips M5 decisions — that is the OTA
deterrent, and it only ever engages on a *license transition*, never on a
steady-state free-tier install.

In-process boundary (ADR-0139) is NOT closed here. An attacker who understands
the full lattice can still rebind ``_FEATURE_ROOT_KEY`` via gc. OTA raises the
bar to "understand six subsystems"; it does not make in-process attack
impossible. Do not represent this module as a security boundary.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("corvin.license.lattice")

# ── Key-derivation labels (versioned; never reuse a label across purposes) ────
_ROOT_LABEL_FREE = b"corvin:ota:feature-root:free:v1"
_HKDF_SALT = b"corvin:ota:hkdf:v1"
_FEATURE_INFO_PREFIX = "corvin:feature:"
_SESSION_PROOF_LABEL = b"corvin:ota:session-proof:v1"
_LIC_CONSTANT_LABEL = b"corvin:ota:lic-constant:v1"

# ── Module-level root-key cache ───────────────────────────────────────────────
# Set once at license load/reload by validator via set_feature_root_key(). Held
# in-process only — never written to disk. None means "not yet initialised";
# feature_root_key() then transparently falls back to the public free-tier root,
# so callers are always safe even before the validator wires us up.
_FEATURE_ROOT_KEY: "bytes | None" = None
_lock = threading.Lock()


def _free_root_key() -> bytes:
    """Publicly-known root key for the no-license (Apache-core / free) tier.

    Not a secret — the free tier has no paid feature config to protect. Mirrors
    ``chain_dna.derive_seed_free()`` so the OTA lattice and the audit DNA agree
    on what "free tier" means.
    """
    return hashlib.sha256(_ROOT_LABEL_FREE).digest()


def _paid_root_key(license_jwt: str) -> bytes:
    """Root key for a paid tier — derived from the signed JWT token bytes.

    The token embeds a signature that only CorvinLabs' private key can produce,
    so the correct root key (and every sub-key below it) cannot be reproduced
    without the genuine license. Identical derivation to
    ``chain_dna.derive_seed_paid()``'s first step (``sha256(jwt)``).
    """
    return hashlib.sha256(license_jwt.encode("utf-8")).digest()


def set_feature_root_key(license_jwt: "str | None") -> None:
    """Install the active root key for the lattice. Called by validator at boot.

    ``license_jwt=None`` (no license / free tier / tamper fallback) installs the
    public free-tier root so the lattice keeps working with stable keys. A real
    token installs the paid root. Best-effort and never raises — a failure here
    must not block license activation.
    """
    global _FEATURE_ROOT_KEY
    try:
        with _lock:
            _FEATURE_ROOT_KEY = (
                _paid_root_key(license_jwt) if license_jwt else _free_root_key()
            )
    except Exception:  # noqa: BLE001
        # Never let key installation break the boot path; fall back to free root.
        with _lock:
            _FEATURE_ROOT_KEY = _free_root_key()


def feature_root_key() -> bytes:
    """Return the active root key, falling back to the public free-tier root.

    Always returns 32 bytes — callers never need to handle ``None``. This is the
    free-tier-safety guarantee: before the validator has wired us up, or on any
    no-license install, the lattice still derives stable sub-keys.
    """
    with _lock:
        rk = _FEATURE_ROOT_KEY
    return rk if rk is not None else _free_root_key()


def is_paid_root_active() -> bool:
    """True when a non-free (paid) root key is installed.

    Used by M6 shard consistency to know whether to expect encrypted paid
    feature configs. Never raises.
    """
    with _lock:
        rk = _FEATURE_ROOT_KEY
    return rk is not None and rk != _free_root_key()


# ── HKDF sub-key derivation ───────────────────────────────────────────────────

def _derive_subkey(info: bytes, length: int = 32, *, root_key: "bytes | None" = None) -> bytes:
    """HKDF-SHA256 sub-key from the active (or supplied) root key.

    One HKDF tree for the whole OTA lattice: M1 feature keys, M3 session proof
    and M5 constant all branch from here via distinct ``info`` labels.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    rk = root_key if root_key is not None else feature_root_key()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=_HKDF_SALT,
        info=info,
    ).derive(rk)


# ── M1: Key-Derived Feature Lattice (encrypted feature configs at rest) ───────

def _derive_feature_key(feature: str, instance_id: str, *, root_key: "bytes | None" = None) -> bytes:
    """32-byte AES-GCM key for one feature config, bound to feature + instance.

    The HKDF ``info`` is length-prefixed so the (feature, instance_id) pair maps
    to the key UNAMBIGUOUSLY — a plain ``feature:instance`` join lets
    ("a:b","c") and ("a","b:c") collide to the same key (review LOW). Encoding
    each field's byte-length removes the collision.
    """
    f = feature.encode("utf-8")
    i = instance_id.encode("utf-8")
    info = (f"{_FEATURE_INFO_PREFIX}{len(f)}:".encode("utf-8")
            + f + b":" + f"{len(i)}:".encode("utf-8") + i)
    return _derive_subkey(info, 32, root_key=root_key)


def encrypt_feature_config(
    feature: str,
    instance_id: str,
    plaintext: bytes,
    *,
    root_key: "bytes | None" = None,
) -> bytes:
    """Encrypt a feature config blob. Returns ``nonce(12) || ciphertext``.

    Looks like an ordinary AEAD operation to a reader without the full picture.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_feature_key(feature, instance_id, root_key=root_key)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ct


def decrypt_feature_config(
    feature: str,
    instance_id: str,
    blob: bytes,
    *,
    root_key: "bytes | None" = None,
) -> bytes:
    """Decrypt a ``nonce || ciphertext`` blob.

    Wrong license (wrong derived key) → ``cryptography.exceptions.InvalidTag``,
    which the caller cannot distinguish from disk corruption. That opacity is
    the M1 deterrent — there is no "license check failed" message.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(blob) < 13:
        from cryptography.exceptions import InvalidTag

        raise InvalidTag("feature config blob too short")
    key = _derive_feature_key(feature, instance_id, root_key=root_key)
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


# ── File-backed feature config store ──────────────────────────────────────────

def _feature_store_dir(corvin_home: Path) -> Path:
    return corvin_home / "global" / "license" / "feature_configs"


def write_feature_config_file(
    corvin_home: Path,
    feature: str,
    instance_id: str,
    plaintext: bytes,
    *,
    root_key: "bytes | None" = None,
) -> Path:
    """Encrypt + atomically write a feature config to disk (mode 0600)."""
    blob = encrypt_feature_config(feature, instance_id, plaintext, root_key=root_key)
    d = _feature_store_dir(corvin_home)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{feature}.enc"
    tmp = path.with_suffix(".enc.tmp")
    tmp.write_bytes(blob)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def read_feature_config_file(
    corvin_home: Path,
    feature: str,
    instance_id: str,
    *,
    root_key: "bytes | None" = None,
) -> "bytes | None":
    """Read + decrypt a feature config from disk.

    Returns ``None`` when the file is absent. Raises ``InvalidTag`` when present
    but undecryptable with the active license (wrong/changed license, or
    tampering).
    """
    path = _feature_store_dir(corvin_home) / f"{feature}.enc"
    if not path.exists():
        return None
    return decrypt_feature_config(feature, instance_id, path.read_bytes(), root_key=root_key)


# ── M3: Session-Derived License Proof primitive ───────────────────────────────

def session_lic_proof(session_id: str, *, root_key: "bytes | None" = None) -> str:
    """16-hex license proof bound to a session id and the active root key.

    Stable on free tier (public root) so loopback owner login keeps working;
    changes when the license changes, which invalidates outstanding sessions.
    Looks like a generic HMAC token — no license vocabulary in the call site.
    """
    subkey = _derive_subkey(_SESSION_PROOF_LABEL, 16, root_key=root_key)
    return hmac.new(subkey, session_id.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


# ── M5: Structural Embedding constant ─────────────────────────────────────────

def lic_constant(*, root_key: "bytes | None" = None) -> int:
    """4-byte unsigned int derived from the active root key.

    Mixed into the path-gate decision hash under ADR-0154 M5 (gated, default
    OFF). Wrong license → different constant → write-gate decisions flip. Reads
    like an ordinary salt/constant to anyone without the full picture.
    """
    raw = _derive_subkey(_LIC_CONSTANT_LABEL, 4, root_key=root_key)
    return int.from_bytes(raw, "big")
