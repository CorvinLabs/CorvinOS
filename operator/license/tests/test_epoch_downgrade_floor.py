"""R3-confirm regression: the SOB replay-epoch is floored against the monotonic
persisted instance_epoch.json, so an OLDER but validly-signed manifest (lower
nonce_epoch) cannot DOWNGRADE replay-epoch enforcement via a cache substitution."""
import pytest

from license import sob as S
import license.manifest as M
import license.instance_epoch as IE


@pytest.fixture
def patched(monkeypatch):
    def set_state(manifest_epoch, floor):
        monkeypatch.setattr(M, "load_cached_manifest",
                            lambda ch=None: {"nonce_epoch": manifest_epoch, "_fetched_at": 0})
        monkeypatch.setattr(IE, "read_instance_epoch", lambda ch: floor)
    return set_state


def test_sob_epoch_floored_against_persisted(patched):
    patched(manifest_epoch=3, floor=5)        # older signed manifest, higher floor
    assert S._current_nonce_epoch(None) == 5  # downgrade blocked


def test_sob_forward_rotation_uses_manifest(patched):
    patched(manifest_epoch=9, floor=5)        # legitimate forward rotation
    assert S._current_nonce_epoch(None) == 9


def test_manifest_get_nonce_epoch_floored(patched):
    patched(manifest_epoch=3, floor=5)
    assert M.get_nonce_epoch(None) == 5


def test_absent_manifest_uses_floor(monkeypatch):
    monkeypatch.setattr(M, "load_cached_manifest", lambda ch=None: None)
    monkeypatch.setattr(IE, "read_instance_epoch", lambda ch: 4)
    assert S._current_nonce_epoch(None) == 4
    assert M.get_nonce_epoch(None) == 4
