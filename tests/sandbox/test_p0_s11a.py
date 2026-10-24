"""P-S11a — a non-default tenant's audit tail holds no instance-wide legacy record (PLAN §4 S-11a).

Red on main (3029088f8): ``core/console/corvin_console/routes/audit_tail.py:168`` names the
instance chain ``<corvin_home>/global/forge/audit.jsonl`` and ``:174`` merges it into EVERY
tenant's tail; its records carry no ``tenant_id``.
Green after the planned fix: merge it only when ``rec.tenant_id == "_default"``. The
``_default`` control below stays true after the fix.
"""
from __future__ import annotations

import json
import time

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .conftest import LEGACY_EVENT_S11A  # noqa: E402


def _append(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "S-11a open: routes/audit_tail.py:168,174 merge the instance-wide legacy chain into every "
    "tenant's tail; turns green with S-11a (merge only for rec.tenant_id == '_default'); "
    "remove this mark in the fixing commit"))
def test_P_S11a_tenant_b_tail_has_no_legacy_record(server, sandbox, session_for, sess_b):
    _append(sandbox.corvin_home / "global" / "forge" / "audit.jsonl",
            {"ts": time.time(), "event_type": LEGACY_EVENT_S11A, "severity": "INFO", "details": {}})
    _append(sandbox.tenant_home(harness.TENANT_B) / "global" / "forge" / "audit.jsonl",
            {"ts": time.time(), "event_type": "console.sbx_b_own", "severity": "INFO", "details": {}})

    default = harness.fetch(server, session_for("_default"), "/audit/tail?limit=500")
    assert default.status_code == 200, default.text[:300]
    assert LEGACY_EVENT_S11A in default.text, "control: the _default tenant must see the legacy record"

    b = harness.fetch(server, sess_b, "/audit/tail?limit=500")
    assert b.status_code == 200, b.text[:300]
    assert "console.sbx_b_own" in b.text, "control: tenant B must see its own record"
    assert LEGACY_EVENT_S11A not in b.text, "tenant B's audit tail contains the instance-wide legacy record"
