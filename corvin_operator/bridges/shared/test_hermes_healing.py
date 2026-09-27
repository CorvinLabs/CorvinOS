"""The periodic Hermes repair cycle must not run inference against Ollama.

corvin-hermes-health.timer runs ``repair_actions.run_default()`` every 5 min.
Its precondition only reads ``reachable``/``has_model``, but
``get_health_status()`` used to fire a real /api/generate as well — on a
CPU-only Ollama that pinned every core (measured 725 % CPU) and aborted after
its 5 s client timeout while Ollama kept computing.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import hermes_healing  # noqa: E402

_TAGS = json.dumps({"models": [{"name": "qwen3:8b"}]}).encode()


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def ollama_calls(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        url = req if isinstance(req, str) else req.full_url
        calls.append(url)
        return _Resp(_TAGS if url.endswith("/api/tags") else b"{}")

    monkeypatch.setattr(hermes_healing.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_health_status_does_not_generate_by_default(ollama_calls):
    status = hermes_healing.get_health_status()
    assert status["reachable"] is True and status["has_model"] is True
    assert status["inference_ok"] is None
    assert not [u for u in ollama_calls if "/api/generate" in u]


def test_explicit_probe_still_generates(ollama_calls):
    status = hermes_healing.get_health_status(probe_inference=True)
    assert status["inference_ok"] is True
    assert [u for u in ollama_calls if "/api/generate" in u]


def test_repair_cycle_entry_point_never_generates(ollama_calls, monkeypatch, tmp_path):
    """Drive the same function the systemd unit runs, with the risky gate open."""
    repair_actions = pytest.importorskip("corvin_core.aco.repair_actions")
    monkeypatch.setenv("CORVIN_ACO_L5_RISKY", "1")
    monkeypatch.delenv("CORVIN_ACO_L5_OFF", raising=False)
    # repair_actions imports hermes_healing by module name; make it this one.
    monkeypatch.setitem(sys.modules, "hermes_healing", hermes_healing)

    repair_actions.run_local_repairs(
        repair_actions.RepairContext(corvin_home=tmp_path), dry_run=True)

    assert any(u.endswith("/api/tags") for u in ollama_calls), \
        "positive control: the Hermes precondition did not run at all"
    assert not [u for u in ollama_calls if "/api/generate" in u]
