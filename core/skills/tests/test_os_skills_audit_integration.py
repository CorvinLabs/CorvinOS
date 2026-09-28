"""``core.skills.os_skills.audit_integration`` writes into THE tenant chain.

Regression (adversarial review round 5, 2026-09-28): the module imported the
nonexistent ``core.compliance.audit_backend``; the ImportError was caught and
logged, so every ``skill_executed`` / ``skill_feedback`` /
``skill_config_updated`` record — including the console DoD-verifier route's —
was dropped. These tests read the records back from the tenant chain and
verify the chain.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.skills.os_skills import audit_integration as ai

_FORGE_DIR = Path(__file__).resolve().parents[3] / "corvin_operator" / "forge"


def _chain(home: Path, tenant: str = "_default") -> Path:
    return home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"


def _records(path: Path, event_type: str) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("event_type") == event_type:
                out.append(rec)
    return out


def _verify(path: Path):
    if str(_FORGE_DIR) not in sys.path:
        sys.path.insert(0, str(_FORGE_DIR))
    from forge.security_events import verify_chain  # type: ignore
    return verify_chain(path)


@pytest.fixture
def home(monkeypatch, tmp_path: Path) -> Path:
    h = tmp_path / "corvin-home"
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    return h


def test_skill_executed_is_chained_metadata_only(home: Path):
    ok = ai.emit_skill_executed_event(
        skill_id="os.definition_of_done_verifier",
        tenant_id="_default",
        input_data={"prompt": "secret user text alice@example.com"},
        output_data={"score": 0.9},
        latency_ms=12.7,
        line_of_moral_responsibility="core/x.py:run:1",
        error="ValueError: /home/alice/private.txt not found",
    )
    assert ok is True
    chain = _chain(home)
    recs = _records(chain, "skill_executed")
    assert len(recs) == 1, chain
    d = recs[0]["details"]
    assert d["skill_id"] == "os.definition_of_done_verifier"
    assert len(d["input_hash"]) == 64 and len(d["output_hash"]) == 64
    assert d["error_type"] == "ValueError" and d["status"] == "error"
    assert d["latency_ms"] == 12
    raw = chain.read_text()
    assert "alice" not in raw and "secret user text" not in raw
    ok_chain, problems = _verify(chain)
    assert ok_chain, problems


def test_feedback_and_config_update_are_chained(home: Path):
    assert ai.emit_skill_feedback_event(
        skill_id="os.delegation_router", tenant_id="_default",
        feedback_type="outcome", signal="the user said: call me at +49 170 1234567",
    ) is True
    assert ai.emit_skill_config_updated_event(
        skill_id="os.delegation_router", tenant_id="_default",
        param_name="confidence_threshold", old_value=0.7, new_value=0.65,
        reason="optimizer",
    ) is True
    chain = _chain(home)
    fb = _records(chain, "skill_feedback")
    cu = _records(chain, "skill_config_updated")
    assert len(fb) == 1 and len(cu) == 1
    assert fb[0]["details"]["feedback_type"] == "outcome"
    assert "signal" not in fb[0]["details"]
    assert len(fb[0]["details"]["signal_sha256"]) == 64
    assert "1234567" not in chain.read_text()
    c = cu[0]["details"]
    assert (c["param_name"], c["old_value"], c["new_value"], c["reason_code"]) == (
        "confidence_threshold", 0.7, 0.65, "optimizer")
    ok_chain, problems = _verify(chain)
    assert ok_chain, problems


def test_missing_tenant_is_refused_not_written(home: Path):
    assert ai.emit_skill_executed_event(
        skill_id="os.x", tenant_id="", input_data=1, output_data=2,
        latency_ms=1, line_of_moral_responsibility="x:1",
    ) is False
    assert not _chain(home).exists()


def test_context_manager_records_the_exception_type(home: Path):
    with pytest.raises(RuntimeError):
        with ai.SkillExecutionAuditor("os.x", "_default", {"a": 1}, "x.py:1"):
            raise RuntimeError("boom with user text")
    recs = _records(_chain(home), "skill_executed")
    assert len(recs) == 1
    assert recs[0]["details"]["error_type"] == "RuntimeError"
    assert "boom" not in _chain(home).read_text()
