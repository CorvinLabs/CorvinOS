"""Test-only client shim: send the session's own re-auth token, as the console SPA does.

Every re-auth-gated console route refuses a request without a ``re_auth_token`` (``deps.verify_reauth``).
The SPA sends the session fingerprint on those requests (``web-next/src/lib/reauth.ts``). Functional tests
that drive those routes through ``TestClient`` are about the feature, not the gate, so this shim adds the
same value the SPA would send when a test sends none.

Opt out per request with the header ``X-Sbx-No-Reauth-Shim: 1``: the request then goes out exactly as
written. Tests that prove refusal use that header, and the refusal is also proven over real HTTP in
``tests/sandbox/test_gap_reauth.py`` (no shim there at all).

The value is read from the session cookie's OWN record, never from the request body, so a test cannot
use the shim to present another session's token.
"""
from __future__ import annotations

import re
from typing import Any

# Re-auth routes, matched on the path after an optional /v1/console prefix.
_REAUTH_PATHS = [
    ("PUT", r"/setup/engines/[^/]+$"),
    ("PUT", r"/profile$"),
    ("POST", r"/profile/reset$"),
    ("PATCH", r"/chat-settings/[^/]+/[^/]+$"),
    ("POST", r"/tools/[^/]+/promote$"),
    ("POST", r"/skills/[^/]+/promote$"),
    ("PUT", r"/bridges/[^/]+/settings$"),
    ("PUT", r"/bridges/[^/]+/enabled$"),
    ("POST", r"/bridges/[^/]+/disconnect$"),
    ("PUT", r"/settings/worker-engine$"),
    ("PUT", r"/settings/features/[^/]+$"),
    ("PUT", r"/settings/[^/]+$"),
    ("PUT", r"/compute/config$"),
    ("POST", r"/compute/runs$"),
    ("POST", r"/license/key$"),
    ("DELETE", r"/byok/secrets/[^/]+$"),
]
_COMPILED = [(m, re.compile(r"^(?:/v1/console)?" + p)) for m, p in _REAUTH_PATHS]
OPT_OUT_HEADER = "X-Sbx-No-Reauth-Shim"


def is_reauth_route(method: str, url: str) -> bool:
    path = url.split("?", 1)[0]
    path = re.sub(r"^https?://[^/]+", "", path)
    return any(m == method.upper() and rx.match(path) for m, rx in _COMPILED)


def install(monkeypatch) -> None:
    """Wrap TestClient.request for the current test. Idempotent per test (monkeypatch undoes it)."""
    from starlette.testclient import TestClient

    from corvin_console import auth as session_auth

    original = TestClient.request

    def request(self, method: str, url: Any, *args: Any, **kwargs: Any):
        headers = dict(kwargs.get("headers") or {})
        opt_out = headers.pop(OPT_OUT_HEADER, None) is not None
        kwargs["headers"] = headers
        if (not opt_out and method.upper() in ("PUT", "POST", "PATCH", "DELETE")
                and is_reauth_route(method, str(url))):
            body = kwargs.get("json")
            if body is None or (isinstance(body, dict) and not body.get("re_auth_token")
                                and "re_auth_token" not in body):
                sid = self.cookies.get("corvin_console_sid")
                rec = session_auth.load_session(sid) if sid else None
                if rec is not None:
                    kwargs["json"] = {**(body or {}), "re_auth_token": rec.sid_fingerprint}
        return original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "request", request)
