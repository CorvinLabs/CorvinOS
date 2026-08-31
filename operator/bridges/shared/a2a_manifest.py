"""A2A Network Membership Manifest — ADR-0103 M3.

Fetches, signature-verifies, caches, and exposes the Corvin Labs network
manifest.  All legitimate CorvinOS instances pick up revocation list updates
on every adapter restart.

Primary:  https://corvin-labs.com/a2a/manifest.json
Mirror:   https://github.com/CorvinLabs/CorvinOS/releases/latest/download/a2a-manifest.json
Cache:    <corvin_home>/global/a2a_manifest.json  (mode 0600)
TTL:      7 days maximum; ≥3 days triggers ``a2a.manifest_stale`` WARNING.
Signature: RS256 over canonical JSON (without the ``"signature"`` field itself)
           verified against ``operator/license/a2a_network_pubkey.pem``.

Offline behaviour
-----------------
If neither URL is reachable the last cached manifest is returned when it is
< 7 days old.  When the cache itself is stale AND the network is down, this
module returns an empty (permissive) manifest so the adapter can still boot —
unless ``a2a_manifest_required: true`` is set in the tenant config, in which
case A2A reception is disabled.

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_PRIMARY_URL = "https://corvin-labs.com/a2a/manifest.json"
_MIRROR_URL = (
    "https://github.com/CorvinLabs/CorvinOS"
    "/releases/latest/download/a2a-manifest.json"
)

_PUBKEY_PATH = Path(__file__).resolve().parents[2] / "license" / "a2a_network_pubkey.pem"

_STALE_WARN_DAYS: float = 3.0
_STALE_HARD_DAYS: float = 7.0
_FETCH_TIMEOUT_S: int = 8
_MAX_MANIFEST_BYTES: int = 64 * 1024  # 64 KB

# ── Empty (permissive) manifest returned when nothing is available ─────────

_EMPTY_MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "issued_at": 0,
    "min_protocol_version": "3.0",
    "current_protocol_version": "4.0",
    "revoked_instance_ids": [],
    "revoked_sest_fps": [],
    "revoked_pairing_ids": [],
    "attestation_mandatory_after": 9_999_999_999,  # effectively never
}


# ── Signature verification ─────────────────────────────────────────────────

def _load_pubkey() -> Any:
    """Load the embedded RS256 public key.  Returns None when unavailable."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key  # type: ignore
        pem = _PUBKEY_PATH.read_bytes()
        return load_pem_public_key(pem)
    except Exception:
        return None


def _verify_manifest_signature(manifest_dict: dict) -> bool:
    """Return True iff the RS256 signature in manifest_dict is valid.

    Signs the canonical JSON of all fields except ``"signature"`` using the
    embedded ``a2a_network_pubkey.pem`` as trust anchor.  Returns False (not
    raises) on any failure so callers can choose how to react.
    """
    sig_b64 = manifest_dict.get("signature")
    if not sig_b64 or not isinstance(sig_b64, str):
        return False

    payload = {k: v for k, v in manifest_dict.items() if k != "signature"}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    msg_hash = hashlib.sha256(canonical).digest()

    try:
        import base64
        sig_bytes = base64.urlsafe_b64decode(sig_b64 + "==")
    except Exception:
        return False

    pubkey = _load_pubkey()
    if pubkey is None:
        return False

    try:
        from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore
        from cryptography.hazmat.primitives import hashes  # type: ignore
        from cryptography.hazmat.primitives.asymmetric.utils import Prehashed  # type: ignore
        from cryptography.exceptions import InvalidSignature  # type: ignore

        pubkey.verify(
            sig_bytes,
            msg_hash,
            padding.PKCS1v15(),
            Prehashed(hashes.SHA256()),
        )
        return True
    except Exception:
        return False


# ── Cache ──────────────────────────────────────────────────────────────────

def _cache_path() -> Path:
    home_env = os.environ.get("CORVIN_HOME", "")
    corvin_home = Path(home_env).expanduser() if home_env else Path.home() / ".corvin"
    return corvin_home / "global" / "a2a_manifest.json"


def _load_cache() -> dict | None:
    """Return parsed cached manifest, or None when missing / unreadable."""
    p = _cache_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def _save_cache(manifest: dict) -> None:
    """Atomically write manifest to cache file at mode 0600."""
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, sort_keys=True, indent=2), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(p)
    except Exception:
        pass


# ── HTTP fetch ─────────────────────────────────────────────────────────────

def _fetch_url(url: str) -> dict | None:
    """Fetch + parse a manifest JSON from url.  Returns None on any failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "corvin-a2a-manifest/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
            body = resp.read(_MAX_MANIFEST_BYTES)
        return json.loads(body)
    except Exception:
        return None


def _fetch_manifest() -> dict | None:
    """Try primary URL, then mirror.  Returns raw dict or None."""
    m = _fetch_url(_PRIMARY_URL)
    if m is not None:
        return m
    return _fetch_url(_MIRROR_URL)


# ── Manifest age helpers ───────────────────────────────────────────────────

def _manifest_age_days(manifest: dict) -> float:
    issued_at = float(manifest.get("issued_at", 0) or 0)
    if issued_at <= 0:
        return float("inf")
    return (time.time() - issued_at) / 86400.0


# ── Audit helper ───────────────────────────────────────────────────────────

def _emit_audit(event_type: str, severity: str, details: dict) -> None:
    """Best-effort audit emit — never raises."""
    try:
        _forge_parent = Path(__file__).resolve().parents[2] / "forge"
        import sys as _sys
        if str(_forge_parent) not in _sys.path:
            _sys.path.insert(0, str(_forge_parent))
        from forge import security_events as _se  # type: ignore[import-not-found]
        from audit import audit_path as _ap  # type: ignore[import-not-found]
        _ap_val = _ap()
        _ap_val.parent.mkdir(parents=True, exist_ok=True)
        _se.write_event(_ap_val, event_type, severity=severity,
                        tool="", run_id="", details=details)
    except Exception:
        pass


# ── Public API ─────────────────────────────────────────────────────────────

class A2AManifest:
    """Immutable view of a loaded (or empty) network manifest."""

    def __init__(self, data: dict, *, from_cache: bool = False,
                 sig_verified: bool = False, age_days: float = 0.0) -> None:
        self._data = data
        self.from_cache = from_cache
        self.sig_verified = sig_verified
        self.age_days = age_days

    @property
    def revoked_instance_ids(self) -> set[str]:
        return set(self._data.get("revoked_instance_ids") or [])

    @property
    def revoked_sest_fps(self) -> set[str]:
        return set(self._data.get("revoked_sest_fps") or [])

    @property
    def revoked_pairing_ids(self) -> set[str]:
        return set(self._data.get("revoked_pairing_ids") or [])

    @property
    def attestation_mandatory_after(self) -> float:
        v = self._data.get("attestation_mandatory_after", 9_999_999_999)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 9_999_999_999.0

    @property
    def min_protocol_version(self) -> str:
        return str(self._data.get("min_protocol_version") or "3.0")

    @property
    def is_stale(self) -> bool:
        return self.age_days >= _STALE_WARN_DAYS

    @property
    def is_hard_stale(self) -> bool:
        return self.age_days >= _STALE_HARD_DAYS

    @property
    def is_empty(self) -> bool:
        return self._data.get("issued_at", 0) == 0


_LOADED_MANIFEST: A2AManifest | None = None
_MANIFEST_LOCK = threading.Lock()


def load_manifest(*, force_refresh: bool = False) -> A2AManifest:
    """Return the current A2A network manifest.

    Call order:
    1. Return in-process cache if already loaded and not force_refresh.
    2. Fetch from primary / mirror URL.
    3. Verify RS256 signature — reject if invalid.
    4. Cache on disk at mode 0600.
    5. Fall back to disk cache when network unavailable.
    6. Fall back to empty (permissive) manifest when everything fails.

    Audit events emitted:
      a2a.manifest_fetched — on successful fresh fetch
      a2a.manifest_stale   — when manifest age ≥ 3 days
    """
    global _LOADED_MANIFEST
    with _MANIFEST_LOCK:
        if _LOADED_MANIFEST is not None and not force_refresh:
            return _LOADED_MANIFEST
        return _load_manifest_unlocked()


def _load_manifest_unlocked() -> "A2AManifest":
    """Inner load logic — must be called with _MANIFEST_LOCK held."""
    global _LOADED_MANIFEST

    # Try live fetch
    raw = _fetch_manifest()
    if raw is not None:
        sig_ok = _verify_manifest_signature(raw)
        if sig_ok:
            age = _manifest_age_days(raw)
            if age < _STALE_HARD_DAYS:
                _save_cache(raw)
                m = A2AManifest(raw, from_cache=False, sig_verified=True, age_days=age)
                _LOADED_MANIFEST = m
                _emit_audit("a2a.manifest_fetched", "INFO", {
                    "age_days": round(age, 2),
                    "revoked_count": (
                        len(raw.get("revoked_instance_ids") or [])
                        + len(raw.get("revoked_sest_fps") or [])
                        + len(raw.get("revoked_pairing_ids") or [])
                    ),
                })
                return m

    # Try cache — but only if it is not hard-stale (> 7 days).
    # Per the module contract: a hard-stale cache is treated as absent and
    # the empty (permissive) manifest is returned so the adapter can boot
    # without enforcing a potentially outdated revocation list.
    cached = _load_cache()
    if cached is not None:
        age = _manifest_age_days(cached)
        if age < _STALE_HARD_DAYS:
            sig_ok = _verify_manifest_signature(cached)
            if sig_ok:
                m = A2AManifest(cached, from_cache=True, sig_verified=True, age_days=age)
                _LOADED_MANIFEST = m
                if m.is_stale:
                    _emit_audit("a2a.manifest_stale", "WARNING", {
                        "age_days": round(age, 2),
                        "sig_verified": True,
                    })
                return m
            # FND-04: the cached manifest's signature does NOT verify — a local
            # tamperer could have emptied revoked_sest_fps/revoked_pairing_ids/
            # revoked_instance_ids to un-revoke a known fork. Do NOT trust this
            # cache; treat it as absent and fall through to the empty manifest
            # (the live signed fetch remains the only authoritative source of
            # revocation). The cache is never an oracle for revocation state.
            _emit_audit("a2a.manifest_cache_sig_invalid", "WARNING", {
                "age_days": round(age, 2),
            })
        # Hard-stale OR signature-invalid cache: fall through to empty manifest.

    # No usable manifest — return empty (permissive)
    m = A2AManifest(_EMPTY_MANIFEST, from_cache=False,
                    sig_verified=False, age_days=float("inf"))
    _LOADED_MANIFEST = m
    _emit_audit("a2a.manifest_stale", "WARNING", {
        "age_days": None,
        "sig_verified": False,
        "reason": "no_manifest_available",
    })
    return m


def clear_cached() -> None:
    """Discard the in-process cache so the next call re-fetches."""
    global _LOADED_MANIFEST
    with _MANIFEST_LOCK:
        _LOADED_MANIFEST = None


def pubkey_present() -> bool:
    """Return True iff the embedded A2A network public key exists and is parseable."""
    return _load_pubkey() is not None
