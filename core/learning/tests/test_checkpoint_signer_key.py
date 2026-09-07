"""The checkpoint signing key must be SECRET (round-4 review, F2).

``CheckpointSigner.get_tenant_key()`` returned
``sha256(b"checkpoint.signer:" + tenant_id)`` — a pure function of a public
string — while the module docstring claimed "attacker cannot forge without
tenant key". Anyone could recompute it, sign an arbitrary meta-loop state and
have ``DivergenceWatchdog.restore_checkpoint`` accept it as authentic. The
forged state in the original reproduction pinned α at the top of the watchdog's
bound and damping at the bottom: the maximum-learning-rate / minimum-damping
corner.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import stat
from pathlib import Path

import pytest

from core.learning.checkpoint_signer import (
    KEY_FILENAME,
    CheckpointKeyUnavailable,
    CheckpointSignatureError,
    CheckpointSigner,
)
from core.learning.watchdog import DivergenceWatchdog

TENANT = "_default"
GOOD_STATE = {"α_core": 0.01, "α_infra": 0.01, "damping_core": 0.95, "damping_infra": 0.95}
#: max learning rate / min damping — what an attacker would pin.
FORGED_STATE = {"α_core": 0.3, "α_infra": 0.3, "damping_core": 0.8, "damping_infra": 0.8}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "corvin_home"
    root.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(root))
    return root


def _legacy_public_signature(state: dict, tenant_id: str = TENANT) -> tuple[str, str]:
    """Recompute the PRE-FIX signature — exactly what an attacker could do."""
    merkle = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
    public_key = hashlib.sha256(f"checkpoint.signer:{tenant_id}".encode()).digest()
    return merkle, hmac.new(public_key, merkle.encode(), hashlib.sha256).hexdigest()


def test_the_key_is_random_secret_material_on_disk(home: Path):
    signer = CheckpointSigner(TENANT)
    key = signer.get_tenant_key()

    assert len(key) == 64, "256 bits, hex-encoded"
    path = signer.key_path
    assert path == home / "tenants" / TENANT / "keys" / KEY_FILENAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert key != hashlib.sha256(f"checkpoint.signer:{TENANT}".encode()).digest()


def test_the_key_is_stable_across_signer_instances(home: Path):
    assert CheckpointSigner(TENANT).get_tenant_key() == CheckpointSigner(TENANT).get_tenant_key()


def test_two_tenants_get_different_keys(home: Path):
    assert CheckpointSigner("alpha").get_tenant_key() != CheckpointSigner("beta").get_tenant_key()


def test_a_legitimately_signed_checkpoint_still_restores(home: Path):
    """The counter-test: the fix must not break the real rollback path."""
    watchdog = DivergenceWatchdog(TENANT)
    checkpoint_id = watchdog.save_checkpoint(dict(GOOD_STATE))
    assert watchdog.restore_checkpoint(checkpoint_id) == GOOD_STATE


def test_a_checkpoint_signed_with_the_OLD_PUBLIC_derivation_is_rejected(home: Path):
    """The reproduction, inverted: the forgery must now fail closed."""
    watchdog = DivergenceWatchdog(TENANT)
    watchdog.save_checkpoint(dict(GOOD_STATE))  # creates the real key

    merkle, signature = _legacy_public_signature(FORGED_STATE)
    watchdog.signed_checkpoints["forged"] = {
        "id": "forged",
        "state": dict(FORGED_STATE),
        "merkle_root": merkle,
        "signature": signature,
    }

    with pytest.raises(CheckpointSignatureError):
        watchdog.restore_checkpoint("forged")


def test_tampering_with_the_state_still_fails_closed(home: Path):
    watchdog = DivergenceWatchdog(TENANT)
    checkpoint_id = watchdog.save_checkpoint(dict(GOOD_STATE))
    watchdog.signed_checkpoints[checkpoint_id]["state"]["α_core"] = 0.3

    with pytest.raises(CheckpointSignatureError):
        watchdog.restore_checkpoint(checkpoint_id)


def test_an_empty_key_file_fails_closed_rather_than_signing(home: Path):
    signer = CheckpointSigner(TENANT)
    signer.key_path.parent.mkdir(parents=True, exist_ok=True)
    signer.key_path.write_text("")

    with pytest.raises(CheckpointKeyUnavailable):
        signer.get_tenant_key()


def test_the_persisted_copy_carries_a_signature_over_the_real_key(home: Path, tmp_path: Path):
    """On-disk checkpoints are forensic; they must not be signable by outsiders."""
    watchdog = DivergenceWatchdog(TENANT)
    directory = tmp_path / "meta_checkpoints"
    checkpoint_id = watchdog.save_checkpoint(dict(GOOD_STATE), directory=directory)

    payload = json.loads((directory / f"{checkpoint_id}.json").read_text())
    _, public_sig = _legacy_public_signature(payload["state"])
    assert payload["signature"] != public_sig
