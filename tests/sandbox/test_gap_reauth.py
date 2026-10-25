"""Every re-auth-gated console route refuses an absent or empty re-auth token.

Red on origin/main (925406772): ``deps.verify_reauth`` returned True for an absent/empty token
("CSRF upstream is the possession gate"), so all 14 legacy call sites accepted a mutation with no
re-auth at all. Green: ``verify_reauth`` refuses it everywhere (no opt-out parameter); the console
SPA sends the session fingerprint (``web-next/src/lib/reauth.ts``).

Call sites (route -> deps.verify_reauth):
  routes/setup.py:900          PUT  /setup/engines/{engine_id}
  routes/profile.py:270        PUT  /profile
  routes/profile.py:381        POST /profile/reset
  routes/chat_settings.py:472  PATCH /chat-settings/{channel}/{chat_key}
  routes/promote.py:73,122     POST /tools|skills/{name}/promote   (behind forge.create -> 402 on the
                               sandbox's free tier; proven in tests/license/test_reauth_promote_e2e.py)
  routes/bridges.py:686,749,1018 PUT settings / PUT enabled / POST disconnect
  routes/settings.py:624,818,948 PUT worker-engine / PUT /settings/{label} / PUT features/{flag}
  routes/compute.py:938,1105   PUT /compute/config / POST /compute/runs
  routes/license.py, routes/byok.py (DELETE) -- already strict on this branch, included here.

Positive controls (real fingerprint -> anything but 401) only where the side effect stays inside the
sandbox; bridges and compute runs are negative-only (a positive call would start daemons / workers).
"""
from __future__ import annotations

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402

ROUTES = [
    # (method, path, body, positive_control)
    ("PUT", "/setup/engines/anthropic", {"value": "sk-ant-sbx-reauth-probe-000000000000"}, True),
    ("PUT", "/profile", {"identity": {"name": "Sbx"}}, True),
    ("POST", "/profile/reset", {}, True),
    ("PATCH", "/chat-settings/discord/sbxreauth", {"ldd_enabled": True}, True),
    ("PUT", "/bridges/discord/settings", {"settings": {}}, False),
    ("PUT", "/bridges/discord/enabled", {"enabled": False}, False),
    ("POST", "/bridges/discord/disconnect", {"mode": "disconnect"}, False),
    ("PUT", "/settings/worker-engine", {"mode": "native"}, True),
    ("PUT", "/settings/branding.yaml", {"body": "name: sbx\n"}, True),
    ("PUT", "/settings/features/sbx_probe_flag", {"enabled": True}, False),
    ("PUT", "/compute/config", {"enabled": False}, True),
    ("POST", "/compute/runs", {"tool_name": "sbx_probe"}, False),
    ("POST", "/license/key", {"key": "CORVIN-sbx-reauth-probe"}, False),
    ("DELETE", "/byok/secrets/custom_sbxreauth", {}, False),
]


def _ids():
    return [f"{m} {p}" for m, p, _, _ in ROUTES]


@pytest.mark.parametrize("method,path,body,_pos", ROUTES, ids=_ids())
@pytest.mark.parametrize("token", [None, ""], ids=["absent", "empty"])
def test_G_reauth_route_refuses_a_missing_token(server, session_for, method, path, body, _pos, token):
    sess = session_for("_default")
    payload = dict(body)
    if token is not None:
        payload["re_auth_token"] = token
    r = harness.fetch(server, sess, path, method=method, json_body=payload)
    # settings.py worker-engine / features answer 403 {"reason": "reauth-failed"}; the rest 401
    refused = r.status_code == 401 or (r.status_code == 403 and "reauth" in r.text)
    assert refused, f"{method} {path} with {token!r} token: {r.status_code} {r.text[:200]}"


@pytest.mark.parametrize("method,path,body,pos", [r for r in ROUTES if r[3]], ids=[i for i, r in zip(_ids(), ROUTES) if r[3]])
def test_G_reauth_route_accepts_the_session_fingerprint(server, session_for, method, path, body, pos):
    sess = session_for("_default")
    fp = harness.fetch(server, sess, "/auth/whoami").json()["fingerprint"]
    r = harness.fetch(server, sess, path, method=method, json_body={**body, "re_auth_token": fp})
    assert r.status_code != 401, f"{method} {path} refused the real fingerprint: {r.text[:200]}"
    assert r.status_code < 500, f"{method} {path}: {r.status_code} {r.text[:200]}"
