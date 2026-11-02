"""PLAN-0969 WP-2: the platform probe report, over real HTTP on the production mount.

``GET /aco/platform/probe`` runs every probe on the host that serves it and returns the report a
Windows operator can paste back: per probe name / ok / stage / exception type, plus a closed
vocabulary host description. It must answer 200 with every probe green on this host, be refused
without a session, write its audit action, and carry no path or message.
"""
from __future__ import annotations

import pytest

from ._preconditions import SKIP_REASON

if SKIP_REASON is not None:
    pytest.skip(SKIP_REASON, allow_module_level=True)

from . import harness  # noqa: E402
from .helpers_gap import action_records  # noqa: E402

_PATH = "/aco/platform/probe"


def test_probe_report_answers_with_every_probe_green(gateway, session_for):
    sess = session_for("_default")
    resp = harness.fetch(gateway, sess, _PATH)
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert [p["name"] for p in body["probes"]] == [
        "session_memory", "recall_store", "run_log", "voice_summary_store"]
    assert body["ok"] is True, body
    assert all(p["ok"] and p["error_type"] == "" for p in body["probes"]), body
    host = body["host"]
    assert host["platform"] and host["fts5_compiled"] is True
    assert set(host) == {"platform", "python", "sqlite", "fts5_compiled",
                         "has_o_nofollow", "has_o_binary", "has_real_fcntl"}


def test_probe_report_carries_no_path(gateway, session_for, sandbox):
    sess = session_for("_default")
    text = harness.fetch(gateway, sess, _PATH).text
    assert str(sandbox.root) not in text and "/home/" not in text and "\\" not in text


def test_probe_report_is_audited(gateway, session_for, sandbox):
    sess = session_for("_default")
    before = len(action_records(sandbox, "_default", "aco.platform_probe"))
    assert harness.fetch(gateway, sess, _PATH).status_code == 200
    assert len(action_records(sandbox, "_default", "aco.platform_probe")) == before + 1


def test_probe_report_needs_a_session(gateway):
    import httpx

    r = httpx.get(gateway.base + gateway.prefix + _PATH, timeout=10.0, trust_env=False)
    assert r.status_code in (401, 403), r.status_code


def test_probe_leaves_no_scratch_behind(gateway, session_for, sandbox):
    sess = session_for("_default")
    assert harness.fetch(gateway, sess, _PATH).status_code == 200
    assert not (sandbox.corvin_home / ".platform_probe").exists()
    assert list(sandbox.corvin_home.rglob("platform-probe*")) == []
