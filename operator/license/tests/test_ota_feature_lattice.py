"""ADR-0154 M1 (KDFL) + shared-primitive E2E.

Covers the load-bearing free-tier-safety property: with no license the lattice
still derives stable keys, and a paid token produces a *different*, unforgeable
key tree. Wrong license → InvalidTag (opaque, looks like corruption).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make operator/license importable as top-level `feature_lattice`.
_LIC_DIR = Path(__file__).resolve().parents[1]
if str(_LIC_DIR) not in sys.path:
    sys.path.insert(0, str(_LIC_DIR))

import feature_lattice as fl  # type: ignore  # noqa: E402

cryptography = pytest.importorskip("cryptography")
from cryptography.exceptions import InvalidTag  # noqa: E402

PAID_TOKEN = "CORVIN-header.payload.signature-AAAA"
OTHER_TOKEN = "CORVIN-header.payload.signature-BBBB"
IID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _reset_root():
    """Each test starts from the un-wired (free) state."""
    fl.set_feature_root_key(None)
    yield
    fl.set_feature_root_key(None)


# ── Root-key wiring ───────────────────────────────────────────────────────────

def test_free_root_is_stable_and_not_paid():
    fl.set_feature_root_key(None)
    assert fl.feature_root_key() == fl.feature_root_key()
    assert fl.is_paid_root_active() is False


def test_paid_root_differs_from_free_and_per_token():
    fl.set_feature_root_key(None)
    free = fl.feature_root_key()
    fl.set_feature_root_key(PAID_TOKEN)
    paid = fl.feature_root_key()
    fl.set_feature_root_key(OTHER_TOKEN)
    other = fl.feature_root_key()
    assert len({free, paid, other}) == 3
    assert fl.is_paid_root_active() is True


def test_root_key_falls_back_to_free_before_wiring(monkeypatch):
    # Simulate a never-initialised cache: feature_root_key() must still work.
    monkeypatch.setattr(fl, "_FEATURE_ROOT_KEY", None, raising=False)
    assert len(fl.feature_root_key()) == 32


# ── M1: feature config encryption ─────────────────────────────────────────────

def test_feature_config_roundtrip_free():
    fl.set_feature_root_key(None)
    blob = fl.encrypt_feature_config("a2a_peers_max", IID, b'{"limit": 1}')
    assert fl.decrypt_feature_config("a2a_peers_max", IID, blob) == b'{"limit": 1}'


def test_feature_config_roundtrip_paid():
    fl.set_feature_root_key(PAID_TOKEN)
    blob = fl.encrypt_feature_config("a2a_peers_max", IID, b'{"limit": 25}')
    assert fl.decrypt_feature_config("a2a_peers_max", IID, blob) == b'{"limit": 25}'


def test_wrong_license_raises_invalidtag():
    # Encrypt under the paid token, then try to read with a different token.
    fl.set_feature_root_key(PAID_TOKEN)
    blob = fl.encrypt_feature_config("sso_enabled", IID, b"true")
    fl.set_feature_root_key(OTHER_TOKEN)
    with pytest.raises(InvalidTag):
        fl.decrypt_feature_config("sso_enabled", IID, blob)


def test_wrong_instance_raises_invalidtag():
    fl.set_feature_root_key(PAID_TOKEN)
    blob = fl.encrypt_feature_config("sso_enabled", IID, b"true")
    with pytest.raises(InvalidTag):
        fl.decrypt_feature_config("sso_enabled", "different-instance", blob)


def test_truncated_blob_raises_invalidtag():
    with pytest.raises(InvalidTag):
        fl.decrypt_feature_config("x", IID, b"short")


# ── File-backed store ─────────────────────────────────────────────────────────

def test_file_store_roundtrip_and_mode(tmp_path):
    fl.set_feature_root_key(PAID_TOKEN)
    p = fl.write_feature_config_file(tmp_path, "compute_units_per_day", IID, b"2000")
    assert (p.stat().st_mode & 0o777) == 0o600
    assert fl.read_feature_config_file(tmp_path, "compute_units_per_day", IID) == b"2000"


def test_file_store_absent_returns_none(tmp_path):
    assert fl.read_feature_config_file(tmp_path, "nope", IID) is None


def test_file_store_wrong_license_raises(tmp_path):
    fl.set_feature_root_key(PAID_TOKEN)
    fl.write_feature_config_file(tmp_path, "audit_export", IID, b"true")
    fl.set_feature_root_key(OTHER_TOKEN)
    with pytest.raises(InvalidTag):
        fl.read_feature_config_file(tmp_path, "audit_export", IID)


# ── M3 primitive: session_lic_proof ───────────────────────────────────────────

def test_session_proof_stable_per_root_and_length():
    fl.set_feature_root_key(None)
    p1 = fl.session_lic_proof("sid-abc")
    p2 = fl.session_lic_proof("sid-abc")
    assert p1 == p2
    assert len(p1) == 16


def test_session_proof_changes_with_license():
    fl.set_feature_root_key(None)
    free_proof = fl.session_lic_proof("sid-abc")
    fl.set_feature_root_key(PAID_TOKEN)
    paid_proof = fl.session_lic_proof("sid-abc")
    assert free_proof != paid_proof


def test_session_proof_differs_per_session():
    fl.set_feature_root_key(PAID_TOKEN)
    assert fl.session_lic_proof("sid-1") != fl.session_lic_proof("sid-2")


# ── M5 primitive: lic_constant ────────────────────────────────────────────────

def test_lic_constant_deterministic_and_license_bound():
    fl.set_feature_root_key(None)
    free_c = fl.lic_constant()
    assert free_c == fl.lic_constant()
    assert 0 <= free_c < 2**32
    fl.set_feature_root_key(PAID_TOKEN)
    assert fl.lic_constant() != free_c
