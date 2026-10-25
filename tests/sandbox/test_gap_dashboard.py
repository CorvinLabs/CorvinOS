"""Learnings anomalies card (gap 8): the client path must not double the ``/v1/console`` prefix.

``lib/api/client.ts`` prefixes every ``api()`` path with ``/v1/console``. The anomalies card passed the
full path, so the browser requested ``/v1/console/v1/console/api/learning/daemon`` (404). Red on
origin/main (925406772): the source literal starts with the prefix and the production host (gateway,
``/v1/console`` mount) answers 404 for the doubled URL. Green: the literal is relative and the real
URL answers 200.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402

SRC = harness.REPO_ROOT / "core" / "console" / "corvin_console" / "web-next" / "src" / "components" / "learning-anomalies.tsx"


def test_G_dashboard_anomalies_card_does_not_double_the_console_prefix():
    text = SRC.read_text(encoding="utf-8")
    literals = re.findall(r'api<[^>]*>\(\s*"([^"]+)"', text)
    assert literals, "the anomalies card no longer calls api() with a literal path"
    assert not any(p.startswith("/v1/console") for p in literals), f"doubled prefix in: {literals}"


def test_G_dashboard_anomalies_endpoint_answers_on_the_production_mount(gateway, session_for):
    sess = session_for("_default")
    ok = harness.fetch(gateway, sess, "/api/learning/daemon")
    assert ok.status_code == 200, f"learning daemon endpoint: {ok.status_code} {ok.text[:200]}"
    doubled = harness.fetch(gateway, sess, "/v1/console/api/learning/daemon")
    assert doubled.status_code == 404, f"the doubled path must not exist: {doubled.status_code}"
