"""Promote routes (``routes/promote.py:73,122``) refuse an absent or empty re-auth token.

Both routes sit behind the ``forge.create`` capability gate (402 on the free tier), so the HTTP
sandbox cannot reach their re-auth check; this test runs them on the member tier through the real
console router (TestClient, real session cookie + CSRF). Red on origin/main: an empty token passed
``verify_reauth`` and the request reached the promote logic (404 for the unknown artefact).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import console, member_tier  # noqa: E402


@pytest.fixture
def con(tmp_path):
    with console(tmp_path) as c:
        yield c


@pytest.mark.parametrize("kind", ["tools", "skills"])
@pytest.mark.parametrize("token", [None, ""], ids=["absent", "empty"])
def test_G_promote_refuses_a_missing_reauth_token(con, kind, token):
    body = {"to": "user"}
    if token is not None:
        body["re_auth_token"] = token
    with member_tier():
        r = con.client.post(f"/v1/console/{kind}/sbx_probe/promote", json=body, headers=con.h)
    assert r.status_code == 401, f"{kind}: {r.status_code} {r.text[:200]}"


@pytest.mark.parametrize("kind", ["tools", "skills"])
def test_G_promote_with_the_fingerprint_passes_reauth(con, kind):
    fp = con.client.get("/v1/console/auth/whoami").json()["fingerprint"]
    with member_tier():
        r = con.client.post(f"/v1/console/{kind}/sbx_probe/promote",
                            json={"to": "user", "re_auth_token": fp}, headers=con.h)
    assert r.status_code != 401, r.text[:200]
