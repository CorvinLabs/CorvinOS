"""Console routes must never block forever on a bridges/shared registry lock.

Companion to ``test_route_lock_nonblocking.py``, which covered the three
``fcntl.flock(LOCK_EX)`` calls that lived in the console package itself. The
same defect class also sat in the ``operator/bridges/shared/`` registries the
console routes import, plus the plugin registry:

  * ``engine_switch._save_store``          — PUT /settings/engine-pref/{chat_key}
  * ``a2a_invite_registry.InviteRegistry._save``
                                           — POST /remote-trigger/pair/cli-invite,
                                             DELETE /remote-trigger/pair/invites/{ikey}
  * ``a2a_friendship.config_file_lock``    — POST /remote-trigger/pair/friendship/set-url
  * ``corvin_plugins.state.registry_mutation``
                                           — POST /plugins/{id}/enable (+ every
                                             other lifecycle transition)

Each was a plain ``flock(LOCK_EX)`` with NO timeout. A wedged holder (a crashed
writer whose fd the kernel had not reaped, an NFS mount, a debugger-stopped
process) hung the operator's HTTP request FOREVER, and no ``try/except`` can
catch a hang.

They are bounded now (``LOCK_EX | LOCK_NB`` + deadline, the contract from
``core.infinite_session.event_store``) and every route maps the deadline to a
clean 503 ``lock_busy``. These tests drive the REAL router through FastAPI's
TestClient with a REAL console session and hold the lock from an INDEPENDENT
file description — exactly what a foreign process holding it looks like to
``flock`` — and additionally assert that the refusal left the guarded state
UNCHANGED (a busy lock must never become an implicit "it worked").

Run:  .venv/bin/python -m pytest core/console/tests/test_route_lock_nonblocking_registries.py
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_SHARED = _OPERATOR / "bridges" / "shared"
_PLUGINS = _REPO / "core" / "plugins"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_SHARED), str(_PLUGINS)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

TENANT = "_default"

# Keep the wedged-lock tests fast: the deadline is what is under test, not its
# default value. 5.0s is the "did it block?" ceiling in every assertion.
SHORT_DEADLINE = 0.2

#: corvin_plugins is NEVER purged (same rule as test_admin_route.py): a second
#: copy forks every enum and steals the audit fan-out sink.
_PURGED = ("corvin_console", "corvin_gateway", "forge")


def _reset_modules(restore: dict | None = None) -> None:
    for key in list(sys.modules):
        if key.startswith(_PURGED):
            del sys.modules[key]
    if restore:
        sys.modules.update(restore)


@contextmanager
def _console(tmp_path: Path):
    """Real console router + real session cookie + CSRF, under a temp CORVIN_HOME."""
    home = tmp_path / "corvin_home"
    (home / "tenants" / TENANT / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / TENANT / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / TENANT / "global" / "console" / "sessions").mkdir(parents=True)

    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH",
            "CORVIN_A2A_INVITE_REGISTRY_PATH", "REMOTE_ORIGINS_DIR",
            "REMOTE_ENDPOINTS_DIR", "REMOTE_PENDING_DIR",
            "REMOTE_PENDING_FRIENDSHIPS_DIR")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT
    # Keep the real GDPR chain out of the test run (tests/conftest.py convention).
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    os.environ["CORVIN_A2A_INVITE_REGISTRY_PATH"] = str(home / "invites.json")
    os.environ["REMOTE_ORIGINS_DIR"] = str(home / "remote_origins")
    os.environ["REMOTE_ENDPOINTS_DIR"] = str(home / "remote_endpoints")
    os.environ["REMOTE_PENDING_DIR"] = str(home / "pending_invites")
    os.environ["REMOTE_PENDING_FRIENDSHIPS_DIR"] = str(home / "pending_friendships")
    preloaded = {k: v for k, v in sys.modules.items() if k.startswith(_PURGED)}
    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console import auth as _auth
        from corvin_console.app import router

        rec = _auth.create_session(tenant_id=TENANT, token_fingerprint="lock-test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})
        yield client, home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


@contextmanager
def _held(lock_path: Path):
    """Hold ``lock_path`` from an independent file description."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


@contextmanager
def _audited(module):
    """Capture the module's console_audit calls without touching the real chain."""
    with patch.object(module, "console_audit") as mock:
        yield mock


def _failed_reasons(mock) -> list[str]:
    return [c.kwargs.get("reason") for c in mock.action_failed.call_args_list]


def _flag(client, flag_id: str, on: bool) -> None:
    resp = client.put(f"/v1/console/settings/features/{flag_id}", json={"enabled": on})
    assert resp.status_code == 200, resp.text


# ── PUT /settings/engine-pref/{chat_key} (engine_switch._save_store) ──────


def _engine_pref_path(home: Path, chat_key: str) -> Path:
    return home / "global" / "engine_pref" / f"console__{chat_key}.json"


def test_engine_pref_put_refuses_503_when_store_lock_is_wedged(tmp_path):
    with _console(tmp_path) as (client, home):
        from corvin_console.routes import engine_pref as ep
        import engine_switch as es

        store = _engine_pref_path(home, "chatlock")
        with patch.object(es, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE), _audited(ep) as audit:
            with _held(store.with_suffix(".json.lock")):
                started = time.monotonic()
                res = client.put(
                    "/v1/console/settings/engine-pref/chatlock",
                    json={"engine": "claude_code"},
                )
                elapsed = time.monotonic() - started

        assert res.status_code == 503, res.text
        assert res.json()["detail"] == "lock_busy"
        assert elapsed < 5.0, f"engine-pref PUT blocked for {elapsed:.1f}s"
        assert "lock_busy" in _failed_reasons(audit)
        audit.action_performed.assert_not_called()
        # 503 means "nothing happened, retry" — prove it.
        assert not store.exists()


def test_engine_pref_put_succeeds_once_the_store_lock_is_free(tmp_path):
    """The bounded lock must stay a real mutex — the same PUT works when free."""
    with _console(tmp_path) as (client, home):
        res = client.put(
            "/v1/console/settings/engine-pref/chatfree", json={"engine": "claude_code"}
        )
        assert res.status_code == 200, res.text
        assert res.json()["per_chat_engine"] == "claude_code"
        saved = json.loads(_engine_pref_path(home, "chatfree").read_text())
        assert saved["engine"] == "claude_code"


# ── A2A invite registry (a2a_invite_registry.InviteRegistry._save) ────────


def _invite_lock(home: Path) -> Path:
    return home / "invites.json.lock"


def _cli_invite_body() -> dict:
    return {
        "origin_id": "peer_lock",
        "url": "http://127.0.0.1:9999",
        "scope": "assistant",
        "ttl_hours": 1,
        "single_use": True,
        "max_call_ttl_s": 60,
        "label": "lock probe",
        "spawn_worker": False,
    }


def test_cli_invite_refuses_503_when_registry_lock_is_wedged(tmp_path):
    """A minted token that could not be RECORDED must not be handed out.

    The registry is what enforces single-use and revocation; answering 200
    with an unrecorded token would hand the operator a credential the
    instance cannot take back.
    """
    with _console(tmp_path) as (client, home):
        from corvin_console.routes import a2a_pair as ap
        import a2a_invite_registry as reg

        with patch.object(reg, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE), _audited(ap) as audit:
            with _held(_invite_lock(home)):
                started = time.monotonic()
                res = client.post(
                    "/v1/console/remote-trigger/pair/cli-invite", json=_cli_invite_body()
                )
                elapsed = time.monotonic() - started

        assert res.status_code == 503, res.text
        assert res.json()["detail"] == "lock_busy"
        assert elapsed < 5.0, f"cli-invite blocked for {elapsed:.1f}s"
        assert "lock_busy" in _failed_reasons(audit)
        audit.action_performed.assert_not_called()
        # The refusal recorded nothing.
        assert not (home / "invites.json").exists()


def test_cli_invite_succeeds_and_records_once_the_lock_is_free(tmp_path):
    with _console(tmp_path) as (client, home):
        res = client.post(
            "/v1/console/remote-trigger/pair/cli-invite", json=_cli_invite_body()
        )
        assert res.status_code == 200, res.text
        recorded = json.loads((home / "invites.json").read_text())
        assert len(recorded) == 1


def test_invite_revoke_refuses_503_and_leaves_the_invite_live(tmp_path):
    """A revoke that did not land must NOT answer 200.

    Reporting a revocation that is not on disk is the dangerous direction:
    the operator believes a credential is dead while it still validates.
    """
    with _console(tmp_path) as (client, home):
        from corvin_console.routes import a2a_pair as ap
        import a2a_invite_registry as reg

        created = client.post(
            "/v1/console/remote-trigger/pair/cli-invite", json=_cli_invite_body()
        )
        assert created.status_code == 200, created.text
        ikey = next(iter(json.loads((home / "invites.json").read_text())))

        with patch.object(reg, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE), _audited(ap) as audit:
            with _held(_invite_lock(home)):
                started = time.monotonic()
                res = client.delete(f"/v1/console/remote-trigger/pair/invites/{ikey}")
                elapsed = time.monotonic() - started

        assert res.status_code == 503, res.text
        assert res.json()["detail"] == "lock_busy"
        assert elapsed < 5.0, f"invite revoke blocked for {elapsed:.1f}s"
        assert "lock_busy" in _failed_reasons(audit)
        # The entry is untouched — not silently marked revoked.
        entry = json.loads((home / "invites.json").read_text())[ikey]
        assert not entry.get("revoked"), entry


# ── A2A friendship config (a2a_friendship.config_file_lock) ───────────────


def test_friendship_set_url_refuses_503_when_config_lock_is_wedged(tmp_path):
    """A contended config lock must REFUSE, not fall through unlocked.

    ``config_file_lock`` is advisory fail-soft when no lock can be OBTAINED at
    all; a BUSY lock is the opposite situation — another writer is mid
    read-modify-write on the peer URL that decides where A2A tasks get
    relayed, and proceeding unlocked is exactly the lost update the lock
    exists to prevent.
    """
    with _console(tmp_path) as (client, home):
        from corvin_console.routes import a2a_pair as ap
        import a2a_friendship as ft

        origins = home / "remote_origins"
        endpoints = home / "remote_endpoints"
        origins.mkdir(parents=True, exist_ok=True)
        endpoints.mkdir(parents=True, exist_ok=True)
        before = {"kid": "kidlock", "_friendship": True, "state": "PENDING",
                  "enabled": False, "url": ""}
        (origins / "kidlock.json").write_text(json.dumps(before))
        (endpoints / "kidlock.json").write_text(json.dumps(before))

        with patch.object(ft, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE), _audited(ap) as audit:
            with _held(origins / ft.CONFIG_LOCK_NAME):
                started = time.monotonic()
                res = client.post(
                    "/v1/console/remote-trigger/pair/friendship/set-url",
                    json={"kid": "kidlock", "peer_url": "http://127.0.0.1:8888"},
                )
                elapsed = time.monotonic() - started

        assert res.status_code == 503, res.text
        assert res.json()["detail"] == "lock_busy"
        assert elapsed < 5.0, f"friendship set-url blocked for {elapsed:.1f}s"
        assert "lock_busy" in _failed_reasons(audit)
        audit.action_performed.assert_not_called()
        # Still PENDING — the refusal changed no routing state.
        assert json.loads((origins / "kidlock.json").read_text())["state"] == "PENDING"


def test_config_file_lock_releases_the_first_dir_when_the_second_is_busy(tmp_path):
    """A partial acquire must not leak a held lock on the way out."""
    with _console(tmp_path):
        import a2a_friendship as ft

        d1 = tmp_path / "aaa"
        d2 = tmp_path / "bbb"
        d1.mkdir()
        d2.mkdir()
        with patch.object(ft, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE):
            with _held(d2 / ft.CONFIG_LOCK_NAME):
                with pytest.raises(ft.FriendshipLockBusy):
                    with ft.config_file_lock(d1, d2):
                        pass
            # d1's lock was released on the way out — it is takeable now.
            probe = open(d1 / ft.CONFIG_LOCK_NAME, "a+")
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
            finally:
                probe.close()


# ── Plugin registry (corvin_plugins.state.registry_mutation) ──────────────


_PLUGIN_RECORD = {
    "plugin_id": "acme-lockprobe",
    "version": "1.0.0",
    "display_name": "Acme Lock Probe",
    "plugin_type": "notification_backend",
    "origin": "community",
    "pii_risk": "low",
    "network_egress": "none",
    "locality": "local",
    "settings_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "settings": {},
}


def _registry_lock(home: Path) -> Path:
    from corvin_plugins.state import registry_path

    return registry_path(tenant_id=TENANT, corvin_home_path=home).with_name(".registry.lock")


def test_plugin_enable_refuses_503_when_registry_lock_is_wedged(tmp_path):
    """A wedged registry lock must refuse, and must NOT enable the plugin.

    ``enable`` passes the consent/egress gates and then persists. If a busy
    lock degraded to "proceed", the console would report a plugin enabled
    that the registry still lists as disabled — the two doors onto the same
    mechanism giving two different answers.
    """
    with _console(tmp_path) as (client, home):
        from corvin_console.routes import plugins as pl
        from corvin_plugins import state as pstate

        _flag(client, "plugin_console_surface", True)
        _flag(client, "plugin_runtime_lifecycle", True)
        assert client.post("/v1/console/plugins", json=_PLUGIN_RECORD).status_code == 200

        with patch.object(pstate, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE):
            with _held(_registry_lock(home)):
                started = time.monotonic()
                res = client.post(
                    "/v1/console/plugins/acme-lockprobe/enable",
                    json={"consent_granted": True},
                )
                elapsed = time.monotonic() - started

        assert res.status_code == 503, res.text
        assert res.json()["detail"] == "lock_busy"
        assert elapsed < 5.0, f"plugin enable blocked for {elapsed:.1f}s"
        # The busy path did NOT grant anything: still disabled on disk AND
        # over the API.
        listed = client.get("/v1/console/plugins").json()["plugins"]
        record = next(p for p in listed if p["plugin_id"] == "acme-lockprobe")
        assert record["enabled"] is False, record
        _ = pl  # imported to prove the route module is the one under test


def test_plugin_enable_succeeds_once_the_registry_lock_is_free(tmp_path):
    """The bounded registry lock must stay a real mutex."""
    with _console(tmp_path) as (client, _home):
        _flag(client, "plugin_console_surface", True)
        _flag(client, "plugin_runtime_lifecycle", True)
        assert client.post("/v1/console/plugins", json=_PLUGIN_RECORD).status_code == 200

        res = client.post(
            "/v1/console/plugins/acme-lockprobe/enable", json={"consent_granted": True}
        )
        assert res.status_code == 200, res.text
        assert res.json()["enabled"] is True
