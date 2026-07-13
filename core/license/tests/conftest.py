"""Shared pytest fixtures for the corvin-license test suite.

Every test runs against a sandboxed ``CORVIN_HOME`` tmpdir + a
freshly-generated RS256 keypair, so we never touch the operator's
real license tree or pubkey.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

_THIS = Path(__file__).resolve().parent
_PLUGIN = _THIS.parent
_REPO = _PLUGIN.parent.parent
_FORGE = _REPO / "operator" / "forge"

# Make the plugin + forge importable without bootstrap.
for p in (_PLUGIN, _FORGE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    """Set CORVIN_HOME to a fresh tmpdir + provision _default tenant tree."""
    home = tmp_path / "corvin"
    home.mkdir(parents=True)
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    # Strangler-fig symlink (Phase 1) — global/ points at _default/global/
    (home / "global").symlink_to(home / "tenants" / "_default" / "global")
    monkeypatch.setenv("CORVIN_HOME", str(home))
    return home


@pytest.fixture
def rs256_keypair(tmp_path):
    """Generate a fresh RS256 keypair and return (priv_pem, pub_pem, priv_path, pub_path)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path = tmp_path / "test-priv.pem"
    pub_path = tmp_path / "test-pub.pem"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    return priv_pem, pub_pem, priv_path, pub_path


@pytest.fixture
def pinned_pubkey(sandbox_home, rs256_keypair, monkeypatch):
    """Inject the test pubkey via Python-level monkeypatch (ADR-0093 M1.1).

    ``CORVIN_LICENSE_PUBKEY_PATH`` was the old mechanism; it is now
    ignored at runtime. Tests must use this fixture instead — it patches
    ``verifier.load_pubkey`` so any call to ``load_license_from_disk()``
    or the CLI's ``_verifier.load_pubkey()`` receives the test key.

    Returns the pub_pem bytes so callers can also pass ``pubkey_pem=``
    directly to ``verify_token()`` / ``load_license_from_disk()``.
    """
    from corvin_license import verifier as _verifier
    _priv, pub_pem, _privpath, _pubpath = rs256_keypair
    monkeypatch.setattr(_verifier, "load_pubkey", lambda: pub_pem)
    return pub_pem


def make_token(
    priv_pem: bytes,
    *,
    customer_id: str = "cust-01HG-XYZ",
    tier: str = "pro",
    employee_count_max: int = 250,
    seats: int = 50,
    valid_seconds: int = 365 * 24 * 3600,
    issued_offset_s: int = 0,
    issuer: str | None = None,
    feature_flags: list[str] | None = None,
    algorithm: str = "RS256",
    trial_type: str | None = None,
    trial_expires_at: int | None = None,
    trial_id: str | None = None,
    machine_fp: str | None = None,
) -> str:
    """Sign a test license JWT.

    ADR-0019 defence-in-depth: when ``feature_flags`` is omitted, the
    canonical flag set for ``tier`` is used so the produced token
    passes the verifier's Tier→Flag check. Tests that explicitly want
    to probe drift / unknown-flag paths pass an explicit list.

    ``trial_type``/``trial_expires_at``/``trial_id``/``machine_fp`` are
    optional trial claims (see verifier._validate_claims' "Optional
    trial claims" block). Omitted entirely unless a caller passes at
    least ``trial_type``.
    """
    import jwt as _pyjwt
    from corvin_license import tier_flags as _tier_flags
    now = int(time.time()) + issued_offset_s
    if feature_flags is None and tier in _tier_flags.TIER_FLAGS:
        feature_flags = sorted(_tier_flags.flags_for_tier(tier))
    payload = {
        "iss": issuer if issuer is not None else "corvin-maintainer",
        "iat": now,
        "exp": now + valid_seconds,
        "customer_id": customer_id,
        "tier": tier,
        "employee_count_max": employee_count_max,
        "seats": seats,
        "feature_flags": feature_flags or [],
    }
    if trial_type is not None:
        payload["trial_type"] = trial_type
        payload["trial_expires_at"] = (
            trial_expires_at if trial_expires_at is not None
            else now + valid_seconds - 60
        )
        payload["trial_id"] = trial_id if trial_id is not None else "t_abc123"
        if machine_fp is not None:
            payload["machine_fp"] = machine_fp
    return _pyjwt.encode(payload, priv_pem, algorithm=algorithm)


@pytest.fixture
def make_jwt(rs256_keypair):
    """Curried helper for signing tokens with the test keypair."""
    priv_pem, _pub_pem, _privpath, _pubpath = rs256_keypair

    def _factory(**kwargs):
        return make_token(priv_pem, **kwargs)

    return _factory


@pytest.fixture
def write_license_file(sandbox_home):
    """Helper: write a license.jwt into the sandboxed corvin home."""
    def _write(token: str, *, mode: int = 0o600) -> Path:
        path = sandbox_home / "tenants" / "_default" / "global" / "license"
        path.mkdir(parents=True, exist_ok=True)
        f = path / "license.jwt"
        f.write_text(token, encoding="utf-8")
        os.chmod(f, mode)
        return f
    return _write
