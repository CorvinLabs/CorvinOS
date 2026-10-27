"""pair/accept is a public server-to-server route: the redeemer has no session cookie.

Real console app, no cookie. Until this fix the route carried the host-session dependency and the
redeemer got 401, so online code pairing could never complete. Authentication is the one-time
accept key of the pending invite, so an unknown invite must answer with the invite lookup
(404/410), never 401.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_a2a_pairing_gate_and_pin_routes import BODIES, _dirs  # noqa: E402
from test_admin_route import _sandbox  # noqa: E402

_PREFIX = "/v1/console/remote-trigger/pair/"


@pytest.mark.licence_tier("member")
def test_cookieless_accept_reaches_the_invite_lookup(tmp_path):
    _dirs(tmp_path)
    _method, url, body = BODIES["accept"]
    with _sandbox(tmp_path) as (client, _csrf, _home, _):
        client.cookies.clear()
        r = client.post(url, json=body)
        assert r.status_code in (404, 410), (r.status_code, r.text)


def test_no_public_a2a_pair_route_depends_on_a_session():
    from corvin_console.deps import PUBLIC_ROUTES
    from corvin_console.routes import a2a_pair

    checked = 0
    for route in a2a_pair.router.routes:
        full = "/v1/console" + route.path
        if full not in PUBLIC_ROUTES:
            continue
        checked += 1
        calls = [d.call for d in route.dependant.dependencies]
        assert a2a_pair._require_host_session not in calls, full
    assert checked >= 1, "positive control: at least one public a2a_pair route must be inspected"
