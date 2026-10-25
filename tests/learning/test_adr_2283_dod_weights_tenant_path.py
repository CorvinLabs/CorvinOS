"""ADR-2283 decision 3: DoD weights live under the tenant, resolved through core.paths.

Until 2026-10-25 ``WeightPersistence()`` wrote ``Path.home()/.corvin/tenants/_default/...``:
every tenant shared one file and ``CORVIN_HOME`` was ignored.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.skills.os_skills.definition_of_done_verifier.weight_persistence import (
    WeightPersistence,
)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    fake_user_home = tmp_path / "user_home"
    fake_user_home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("HOME", str(fake_user_home))
    return home, fake_user_home


def test_two_tenants_get_two_files_under_corvin_home(sandbox):
    home, user_home = sandbox
    a = WeightPersistence(tenant_id="tenant_a")
    b = WeightPersistence(tenant_id="tenant_b")
    assert a.storage_path == home / "tenants" / "tenant_a" / "global" / "forge" / "dod_weights.json"
    assert b.storage_path == home / "tenants" / "tenant_b" / "global" / "forge" / "dod_weights.json"

    assert a.save_weights({"feature": {"w_audit": 0.3}}, {"feature": {"w_audit": 0.6}})
    assert b.save_weights({"feature": {"w_audit": 0.1}}, {"feature": {"w_audit": 0.2}})
    assert a.storage_path.is_file() and b.storage_path.is_file()
    assert a.storage_path != b.storage_path
    # nothing under the user's home directory
    assert not any(user_home.rglob("dod_weights.json"))
    assert a.storage_path.stat().st_mode & 0o777 == 0o600

    # a FRESH instance (a restart) reads weights AND confidence back, per tenant
    a2, b2 = WeightPersistence(tenant_id="tenant_a"), WeightPersistence(tenant_id="tenant_b")
    assert a2.load_weights() == {"feature": {"w_audit": 0.3}}
    assert a2.load_confidence() == {"feature": {"w_audit": 0.6}}
    assert b2.load_weights() == {"feature": {"w_audit": 0.1}}
    assert b2.load_confidence() == {"feature": {"w_audit": 0.2}}


def test_invalid_tenant_is_refused(sandbox):
    with pytest.raises(ValueError):
        WeightPersistence(tenant_id="../escape")


def test_default_tenant_follows_corvin_home(sandbox):
    home, _ = sandbox
    assert WeightPersistence().storage_path.is_relative_to(home)
    assert Path(WeightPersistence().storage_path).parts[-5:] == (
        "tenants", "_default", "global", "forge", "dod_weights.json")
