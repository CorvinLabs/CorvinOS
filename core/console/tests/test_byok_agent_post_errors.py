"""WA-21: _agent_post must distinguish a real 4xx from the Instance Agent
(HTTPError) from the agent actually being unreachable (URLError) — HTTPError
is a URLError subclass, so a bare `except URLError` silently mislabels every
rejected-key-shape / rejected-key-name response as "503 Instance Agent
unreachable", hiding the real reason a save failed.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from fastapi import HTTPException

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import byok as B


def test_http_error_forwards_real_status_and_detail(monkeypatch):
    body = json.dumps({"detail": "anthropic_api_key does not look like a valid key"}).encode()

    def fake_urlopen(req, timeout=None):
        raise HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(B, "urlopen", fake_urlopen)

    with pytest.raises(HTTPException) as exc_info:
        B._agent_post("/secrets/anthropic_api_key", {"ciphertext": "..."})

    assert exc_info.value.status_code == 400
    assert "does not look like a valid key" in exc_info.value.detail


def test_url_error_still_reports_agent_unreachable(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise URLError("connection refused")

    monkeypatch.setattr(B, "urlopen", fake_urlopen)

    with pytest.raises(HTTPException) as exc_info:
        B._agent_post("/secrets/anthropic_api_key", {"ciphertext": "..."})

    assert exc_info.value.status_code == 503
    assert "unreachable" in exc_info.value.detail
