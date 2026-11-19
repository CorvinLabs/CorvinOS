"""Review 2026-11-19 round 3, R3-NEW-3: the secret vault is per tenant.

Before: one host-global ``secrets.json``; a connection manifest of tenant B naming
``secrets: ["PGPASSWORD"]`` received tenant A's plaintext, and a manifest re-pointed
at another host received the password too.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "compute", _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_compute.fabric.datasources import connection_env as ce  # noqa: E402
from forge import secret_vault as sv  # noqa: E402


@pytest.fixture()
def vault_env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CORVIN_SECRET_VAULT", raising=False)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    return tmp_path


def _pg(host: str, secrets=("PGPASSWORD",)) -> dict:
    return {"adapter": "postgresql", "config": {"host": host, "port": 5432, "dbname": "d"},
            "secrets": list(secrets)}


def test_tenant_b_manifest_does_not_get_tenant_a_secret(vault_env):
    sv.set_secret("PGPASSWORD", "tenant-a-plaintext", tenant_id="tenant_a")
    env_a = ce.env_for_connection(_pg("db.internal"), "a", tenant_id="tenant_a")
    assert env_a["PGPASSWORD"] == "tenant-a-plaintext"
    with pytest.raises(ValueError, match="missing from vault"):
        ce.env_for_connection(_pg("attacker.example"), "b", tenant_id="tenant_b")
    assert sv.secret_names(tenant_id="tenant_b") == []
    assert sv.secret_names(tenant_id="tenant_a") == ["PGPASSWORD"]


def test_set_secret_cannot_overwrite_another_tenant(vault_env):
    sv.set_secret("PGPASSWORD", "value-a", tenant_id="tenant_a")
    sv.set_secret("PGPASSWORD", "value-b", tenant_id="tenant_b")
    assert sv.load_tenant_vault("tenant_a") == {"PGPASSWORD": "value-a"}
    assert sv.load_tenant_vault("tenant_b") == {"PGPASSWORD": "value-b"}


def test_tenant_vault_is_0600_and_under_the_gated_vault_dir(vault_env):
    path = sv.set_secret("PGPASSWORD", "value-a", tenant_id="tenant_a")
    assert path == sv.default_vault_path().parent / "tenants" / "tenant_a" / "secrets.json"
    assert path.name == "secrets.json"  # the name the L10 path gate protects below the vault dir
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_host_global_vault_is_a_fallback_for_the_process_tenant_only(vault_env):
    legacy = sv.default_vault_path()
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"PGPASSWORD": "legacy-value"}))
    os.chmod(legacy, 0o600)
    # process tenant = _default
    assert ce.env_for_connection(_pg("db.internal"), "x", tenant_id="_default")["PGPASSWORD"] \
        == "legacy-value"
    assert ce.env_for_connection(_pg("db.internal"), "x")["PGPASSWORD"] == "legacy-value"
    with pytest.raises(ValueError, match="missing from vault"):
        ce.env_for_connection(_pg("db.internal"), "x", tenant_id="other")
    assert sv.secret_names(tenant_id="other") == []
    # a tenant entry wins over the host-global one
    sv.set_secret("PGPASSWORD", "own-value", tenant_id="_default")
    assert sv.load_tenant_vault("_default")["PGPASSWORD"] == "own-value"
    assert json.loads(legacy.read_text()) == {"PGPASSWORD": "legacy-value"}


def test_secret_is_bound_to_its_first_target_host(vault_env):
    sv.set_secret("PGPASSWORD", "value-a", tenant_id="tenant_a")
    ce.env_for_connection(_pg("DB.internal."), "a", tenant_id="tenant_a")
    assert sv.secret_binding("PGPASSWORD", tenant_id="tenant_a") == "db.internal"
    with pytest.raises(ValueError, match="bound to another target"):
        ce.env_for_connection(_pg("attacker.example"), "evil", tenant_id="tenant_a")
    # same host still works; storing the value again re-binds
    assert ce.env_for_connection(_pg("db.internal"), "a", tenant_id="tenant_a")["PGPASSWORD"]
    sv.set_secret("PGPASSWORD", "value-a2", tenant_id="tenant_a", bind_to="new.internal")
    assert ce.env_for_connection(_pg("new.internal"), "a", tenant_id="tenant_a")["PGPASSWORD"] \
        == "value-a2"
    with pytest.raises(ValueError):
        ce.env_for_connection(_pg("db.internal"), "a", tenant_id="tenant_a")


@pytest.mark.parametrize("name", ["CORVIN_AUDIT_ANCHOR_KEY", "PATH", "LD_PRELOAD", "PYTHONPATH",
                                  "ANTHROPIC_API_KEY", "HOME", "GIT_SSH_COMMAND", "lower"])
def test_reserved_names_are_never_injected(vault_env, name):
    if sv.is_valid_key(name):
        sv.set_secret(name, "some-value-123", tenant_id="tenant_a")
    with pytest.raises(ValueError, match="reserved"):
        ce.env_for_connection(_pg("db.internal", secrets=[name]), "a", tenant_id="tenant_a")


def test_resolve_connection_env_uses_the_runs_tenant(vault_env):
    home = vault_env / "home"
    for tid, host in (("tenant_a", "db.internal"), ("tenant_b", "attacker.example")):
        d = home / "tenants" / tid / "datasource_connections"
        d.mkdir(parents=True)
        (d / "main.json").write_text(json.dumps(_pg(host)))
    sv.set_secret("PGPASSWORD", "tenant-a-plaintext", tenant_id="tenant_a")
    assert ce.resolve_connection_env(home, "tenant_a", ["main"])["PGPASSWORD"] == "tenant-a-plaintext"
    with pytest.raises(ValueError):
        ce.resolve_connection_env(home, "tenant_b", ["main"])


def test_invalid_tenant_id_is_refused(vault_env):
    for bad in ("../x", "", "A", "a/b"):
        with pytest.raises(sv.VaultError):
            sv.set_secret("PGPASSWORD", "v-123456", tenant_id=bad)


def test_concurrent_writers_do_not_drop_entries(vault_env):
    names = [f"KEY_{i}" for i in range(24)]
    errors: list[BaseException] = []

    def write(n: str) -> None:
        try:
            sv.set_secret(n, "value-" + n, tenant_id="tenant_a")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sv.secret_names(tenant_id="tenant_a") == sorted(names)


def test_legacy_writer_is_locked_and_keeps_flat_format(vault_env):
    threads = [threading.Thread(target=sv.set_secret, args=(f"K_{i}", f"value-{i}-xx"))
               for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(sv.load_vault()) == sorted(f"K_{i}" for i in range(16))


def test_a_manifest_naming_a_reserved_secret_is_refused_at_registration():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from corvin_compute.fabric.datasources.manifest import DSIv1PolicyError, validate_dsiv1_manifest
    from test_datasource_manifest import _valid_manifest

    assert validate_dsiv1_manifest(_valid_manifest(secrets=["PGPASSWORD"])).secrets == ["PGPASSWORD"]
    for name in ("CORVIN_AUDIT_ANCHOR_KEY", "PATH", "LD_PRELOAD", "ANTHROPIC_API_KEY"):
        with pytest.raises(DSIv1PolicyError, match="reserved"):
            validate_dsiv1_manifest(_valid_manifest(secrets=[name]))
