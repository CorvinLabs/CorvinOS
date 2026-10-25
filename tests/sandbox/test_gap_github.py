"""GitHub Verify (S-9i, real half): ``POST /github/verify`` asks the real GitHub API.

Red on origin/main (925406772): ``routes/github.py`` ``/verify`` answered ``connected: true,
repo_exists: true, rate_limit "60/60"`` for any well-formed URL without contacting GitHub, saved
``auto_sync: true`` and started the sync worker. The cases here run a console that HAS network
(``networked_server``: the sandbox minus its dead proxies) and use public GitHub repositories
(read-only GET, no token except a deliberately invalid one). Needs outbound HTTPS to api.github.com.

A failure of the check is never a success: unknown repo, bad token and unreachable GitHub all
answer ``connected: false``, save ``verified: false`` + ``auto_sync: false`` and start no worker.
"""
from __future__ import annotations

import json
import socket
import stat

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .helpers_gap import action_records, networked_server, verify_tenant_chain  # noqa: E402

PUBLIC_REPO = "https://github.com/CorvinLabs/Corvin-Marketplace"
MISSING_REPO = "https://github.com/CorvinLabs/ltp-this-repo-does-not-exist-9z"
FAKE_TOKEN = "ghp_SBXFAKETOKEN0123456789abcdefABCDEF"


def _github_reachable() -> bool:
    try:
        socket.create_connection(("api.github.com", 443), timeout=5).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _github_reachable(), reason="api.github.com is not reachable from this host")


@pytest.fixture(scope="module")
def net(sandbox, planted, live_tripwire):
    srv = networked_server(sandbox).start()
    yield srv
    from .conftest import stop_checked
    stop_checked(srv)


def _verify(net, sess, url, token=None):
    body = {"url": url}
    if token:
        body["token"] = token
    return harness.fetch(net, sess, "/github/verify", method="POST", json_body=body)


def _config(sandbox, tenant):
    return json.loads((sandbox.tenant_home(tenant) / "github-config.json").read_text())


def _running(net, sess) -> bool:
    return harness.fetch(net, sess, "/github/worker/status").json().get("running") is True


def test_G_github_real_public_repo_is_verified_and_audited(sandbox, net, session_for):
    sess = session_for("_default")
    r = _verify(net, sess, PUBLIC_REPO)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["connected"] is True and body["verified"] is True, body
    d = body["details"]
    assert d["repo_name"] == "Corvin-Marketplace" and d["repo_exists"] is True
    assert d["rate_limit"] and d["rate_limit"] != "60/60", f"rate limit must come from GitHub headers: {d}"
    cfg = _config(sandbox, "_default")
    assert cfg["verified"] is True and cfg["auto_sync"] is True and cfg["last_verified"]
    assert "token" not in cfg
    mode = stat.S_IMODE((sandbox.tenant_home("_default") / "github-config.json").stat().st_mode)
    assert mode == 0o600, oct(mode)
    st = harness.fetch(net, sess, "/github/status").json()
    assert st["connected"] is True and st["state"] == "connected"
    # tear the worker down so nothing keeps cloning after the test
    harness.fetch(net, sess, "/github/worker/stop", method="POST")
    ok = action_records(sandbox, "_default", "github.verify")
    assert ok and ok[-1]["event_type"] == "console.action_performed"
    assert verify_tenant_chain(sandbox, "_default")["ok"]


def test_G_github_missing_repo_is_not_connected_and_starts_nothing(sandbox, net, session_for):
    sess = session_for("sbxgh1")
    r = _verify(net, sess, MISSING_REPO)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["connected"] is False and body["verified"] is False, body
    assert body["details"]["verdict"] == "not_found", body
    cfg = _config(sandbox, "sbxgh1")
    assert cfg["verified"] is False and cfg["auto_sync"] is False
    assert not _running(net, sess)
    st = harness.fetch(net, sess, "/github/status").json()
    assert st["connected"] is False and st["state"] == "saved_not_verified"
    start = harness.fetch(net, sess, "/github/worker/start", method="POST")
    assert start.status_code == 409
    assert not _running(net, sess)


def test_G_github_bad_token_is_a_failure_and_the_token_is_never_stored(sandbox, net, session_for):
    sess = session_for("sbxgh2")
    r = _verify(net, sess, PUBLIC_REPO, token=FAKE_TOKEN)
    body = r.json()
    assert body["connected"] is False and body["details"]["verdict"] == "auth_failed", body
    assert FAKE_TOKEN not in r.text
    for p in sandbox.tenant_home("sbxgh2").rglob("*"):
        if p.is_file():
            assert FAKE_TOKEN not in p.read_text(errors="replace"), p
    cfg = _config(sandbox, "sbxgh2")
    assert cfg["verified"] is False and cfg["auto_sync"] is False and "token" not in cfg
    assert cfg.get("token_hash")
    assert not _running(net, sess)


def test_G_github_failed_verify_is_audited_as_failure_without_the_token(sandbox, net, session_for):
    sess = session_for("_default")
    r = _verify(net, sess, MISSING_REPO, token=FAKE_TOKEN)
    assert r.json()["connected"] is False
    failed = action_records(sandbox, "_default", "github.verify")
    rec = failed[-1]
    assert rec["event_type"] == "console.action_failed", rec
    assert (rec["details"] or {}).get("reason") in {"not_found", "auth_failed"}, rec
    assert FAKE_TOKEN not in json.dumps(failed)
    assert verify_tenant_chain(sandbox, "_default")["ok"]
