"""PLAN-0969 WP-3: the console reports a front-end render failure as a content-free signature.

``POST /aco/client-error`` is what the route error boundary calls before its one automatic
reload. It records ``console.client_error`` in the tenant's audit chain with a closed set of
fields (error code, route pattern, build hash, OS family, whether the boundary already reloaded)
and refuses anything shaped like a message or a path. Real ``uvicorn`` on the gateway mount.
"""
from __future__ import annotations

import pytest

from ._preconditions import SKIP_REASON

if SKIP_REASON is not None:
    pytest.skip(SKIP_REASON, allow_module_level=True)

from . import harness  # noqa: E402
from .helpers_gap import chain_events  # noqa: E402

_PATH = "/aco/client-error"
_GOOD = {"code": "react-185", "route": "/app/chat", "build": "DzBPfJtL",
         "os_family": "windows", "auto_reloaded": False}
_FIELDS = {"code", "route", "build", "os_family", "auto_reloaded", "tenant_id",
           "chain_dna"}  # chain_dna: added by the audit writer to every record, not report content


def test_render_failure_is_recorded_with_closed_fields(gateway, session_for, sandbox):
    sess = session_for("_default")
    before = len(chain_events(sandbox, "_default", "console.client_error"))
    r = harness.fetch(gateway, sess, _PATH, method="POST", json_body=_GOOD)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    assert r.json() == {"recorded": True}
    records = chain_events(sandbox, "_default", "console.client_error")
    assert len(records) == before + 1
    details = records[-1]["details"]
    assert set(details) <= _FIELDS, details
    assert details["code"] == "react-185" and details["os_family"] == "windows"


def test_report_needs_csrf(gateway, session_for):
    import httpx

    sess = session_for("_default")
    with gateway.client() as c:
        r = c.post(_PATH, json=_GOOD, cookies=sess.cookies())
    assert r.status_code == 403, r.status_code
    r = httpx.post(gateway.base + gateway.prefix + _PATH, json=_GOOD, timeout=10.0, trust_env=False)
    assert r.status_code in (401, 403), r.status_code


@pytest.mark.parametrize("field,value", [
    ("code", "Maximum update depth exceeded"),       # a message, not a code
    ("route", "/app/chat/YKAe0sFHktvYaIEa6iSFAA"),    # carries a chat id
    ("route", "C:\\Users\\someone"),                  # a path
    ("build", "x" * 80),
    ("os_family", "Windows NT 10.0; Win64"),          # a user agent, not the enum
])
def test_message_or_identifier_shaped_values_are_refused(gateway, session_for, sandbox, field, value):
    sess = session_for("_default")
    before = len(chain_events(sandbox, "_default", "console.client_error"))
    r = harness.fetch(gateway, sess, _PATH, method="POST", json_body={**_GOOD, field: value})
    assert r.status_code == 422, f"{field}={value!r}: {r.status_code}"
    assert len(chain_events(sandbox, "_default", "console.client_error")) == before


def test_unknown_field_is_refused(gateway, session_for):
    sess = session_for("_default")
    r = harness.fetch(gateway, sess, _PATH, method="POST",
                      json_body={**_GOOD, "message": "some text"})
    assert r.status_code == 422, r.status_code
