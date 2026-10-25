"""P-S9i — an unverified GitHub config never syncs, never claims "connected" (PLAN §4 S-9i).

Red on main (3029088f8), all in ``core/console/corvin_console/routes/``:
  github.py:129   ``/verify`` saves ``auto_sync: True``
  github.py:145   ``/verify`` starts the worker → github_sync.py:44 ``start`` → :124
                  ``sync_skills_to_github()`` → github_git_wrapper.py:56-60 ``git`` subprocess
  github.py:150   ``/verify`` answers ``connected: True`` (``rate_limit "60/60"`` :158) unchecked
  github.py:177   ``/status`` says ``connected: True`` whenever a config file exists
  github.py:247   ``/worker/start`` starts the worker for an unverified config
  github.py:286   ``/auto-sync {enabled:true}`` starts it too
  github_sync.py:248-250  ``resume_auto_sync`` restarts every ``auto_sync`` config at boot
                  (console lifespan ``app.py:873-879``; gateway ``corvin_gateway/app.py:510``)
Green after the planned fix: a ``verified`` flag (false until S-9); the check inside
``GitHubSyncWorker.start()`` and its loop refuses every caller while unverified; ``/verify``
answers "saved, not verified" with ``auto_sync: False``; ``/auto-sync`` refuses ``enabled: true``;
an audited migration resets ``auto_sync`` on existing unverified configs; ``/status``
reports "configured · unverified".

Each case uses its own fresh tenant so a worker started by one case cannot satisfy another.
"""
from __future__ import annotations

import json
import time

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .conftest import GATEWAY_RESUME_TENANT, GATEWAY_RESUME_URL, stop_checked, write_github_config  # noqa: E402
from .harness import Server  # noqa: E402


def _url(tenant: str) -> str:
    return f"https://github.com/sbx-owner/{tenant}-repo"


def _verify(server, sess):
    return harness.fetch(server, sess, "/github/verify", method="POST",
                         json_body={"url": _url(sess.tenant), "token": "ghp_sandbox_fake_token"})


def _worker_running(server, sess) -> bool:
    r = harness.fetch(server, sess, "/github/worker/status")
    assert r.status_code == 200, r.text[:300]
    return r.json().get("running") is True


def _git_calls(sandbox, tenant: str, url: str, wait: float = 5.0) -> list[str]:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        calls = sandbox.git_calls_for(url + " ", f"github-sync-{tenant}")
        if calls:
            return calls
        time.sleep(0.25)
    return sandbox.git_calls_for(url + " ", f"github-sync-{tenant}")


def test_P_S9i_a_verify_runs_no_git(server, sandbox, session_for):
    sess = session_for("sbx9a")
    assert _verify(server, sess).status_code == 200
    calls = _git_calls(sandbox, sess.tenant, _url(sess.tenant))
    assert calls == [], f"git subprocess for the saved config: {calls[:3]}"


def test_P_S9i_b_verify_starts_no_worker(server, session_for):
    sess = session_for("sbx9b")
    assert _verify(server, sess).status_code == 200
    assert not _worker_running(server, sess), "sync worker running after /verify"


def _refused(r) -> bool:
    """The plan only says "refuses": any 4xx, or a 2xx body that says so."""
    if 400 <= r.status_code < 500:
        return True
    try:
        body = r.json()
    except ValueError:
        return False
    return isinstance(body, dict) and (body.get("refused") is True or body.get("ok") is False
                                       or body.get("success") is False)


def test_P_S9i_c_verify_is_not_reported_connected(server, sandbox, session_for):
    sess = session_for("sbx9c")
    r = _verify(server, sess)
    assert r.status_code < 500, r.text[:300]
    if r.status_code == 200:
        assert r.json().get("connected") is not True, f"/verify claims connected: {r.text[:300]}"
    saved = json.loads((sandbox.tenant_home(sess.tenant) / "github-config.json").read_text())
    assert saved.get("verified") is False, f"saved config must carry verified=false: {saved}"


def test_P_S9i_d_worker_start_refused_while_unverified(server, sandbox, session_for):
    sess = session_for("sbx9d")
    write_github_config(sandbox, sess.tenant, url=_url(sess.tenant), auto_sync=False)
    r = harness.fetch(server, sess, "/github/worker/start", method="POST")
    assert r.status_code < 500, r.text[:300]
    assert _refused(r), f"/worker/start started an unverified config: {r.status_code} {r.text[:300]}"
    assert not _worker_running(server, sess)


def test_P_S9i_e_auto_sync_enable_refused_while_unverified(server, sandbox, session_for):
    sess = session_for("sbx9e")
    write_github_config(sandbox, sess.tenant, url=_url(sess.tenant), auto_sync=False)
    r = harness.fetch(server, sess, "/github/auto-sync", method="POST", json_body={"enabled": True})
    assert _refused(r), f"/auto-sync enabled an unverified config: {r.status_code} {r.text[:300]}"
    assert not _worker_running(server, sess)


def test_P_S9i_f_status_not_connected_while_unverified(server, sandbox, session_for):
    sess = session_for("sbx9f")
    write_github_config(sandbox, sess.tenant, url=_url(sess.tenant), auto_sync=False)
    r = harness.fetch(server, sess, "/github/status")
    assert r.status_code == 200, r.text[:300]
    assert r.json().get("connected") is not True, f"/status claims connected: {r.text[:300]}"


RESTART_TENANT = "sbxc"
RESTART_URL = "https://github.com/x/y"


@pytest.fixture(scope="module")
def restarted_console(sandbox, server, live_tripwire):
    """A second console process started over a pre-existing ``auto_sync: true`` config."""
    write_github_config(sandbox, RESTART_TENANT, url=RESTART_URL, auto_sync=True)
    srv = Server(sandbox, Server.CONSOLE, "console-restart").start()
    yield srv
    stop_checked(srv)


def test_P_S9i_g_console_restart_resumes_no_worker(restarted_console, sandbox, session_for):
    sess = session_for(RESTART_TENANT)
    assert not _worker_running(restarted_console, sess), \
        "console boot resumed the sync worker for an unverified auto_sync config"
    calls = _git_calls(sandbox, RESTART_TENANT, RESTART_URL, wait=2.0)
    assert calls == [], f"git subprocess after console restart: {calls[:3]}"


def test_P_S9i_h_gateway_boot_resumes_no_worker(gateway, sandbox, session_for):
    sess = session_for(GATEWAY_RESUME_TENANT)
    assert not _worker_running(gateway, sess), \
        "gateway boot (corvin_gateway/app.py:510) resumed the sync worker for an unverified config"
    calls = _git_calls(sandbox, GATEWAY_RESUME_TENANT, GATEWAY_RESUME_URL, wait=2.0)
    assert calls == [], f"git subprocess after gateway boot: {calls[:3]}"
