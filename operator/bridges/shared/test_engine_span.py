"""ADR-0171 M1 — engine-span schema + audit-floor survival.

The load-bearing property: an engine.span.start/end record's metadata fields must
SURVIVE the ADR-0129 audit-detail floor (else the span is written but empty and
the worker graph can't be rebuilt from it), while PII/content keys are still
dropped (metadata-only invariant).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent
_FORGE = _SHARED.parents[1] / "forge"
for _p in (str(_SHARED), str(_FORGE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine_span as ES  # type: ignore
from forge import security_events as SEC  # type: ignore


def _filter(details, event_type):
    filtered, dropped = SEC.filter_audit_details(details, event_type=event_type)
    return filtered, dropped


def test_new_span_id_shape():
    a, b = ES.new_span_id(), ES.new_span_id()
    assert a.startswith("spn_") and len(a) > 8 and a != b


def test_start_details_fields_survive_floor():
    d = ES.start_details(span_id="spn_1", role="worker", engine_id="hermes",
                         model_id="qwen3:8b", parent_span_id="spn_0",
                         run_id="acs-1", turn_id="t1")
    filtered, dropped = _filter(d, ES.ENGINE_SPAN_START)
    for f in ("span_id", "role", "engine_id", "model_id", "parent_span_id",
              "run_id", "turn_id", "started_at"):
        assert f in filtered, f"span field {f!r} was dropped by the audit floor"
    assert filtered["engine_id"] == "hermes" and filtered["role"] == "worker"


def test_end_details_fields_survive_floor():
    d = ES.end_details(span_id="spn_1", role="worker", engine_id="codex_cli",
                       model_id="gpt-x", run_id="acs-1", status="ok",
                       duration_ms=1234, tokens_used=567, tool_call_count=3)
    filtered, _ = _filter(d, ES.ENGINE_SPAN_END)
    for f in ("span_id", "role", "engine_id", "status", "duration_ms",
              "tokens_used", "tool_call_count"):
        assert f in filtered, f"span field {f!r} dropped"
    assert filtered["status"] == "ok" and filtered["tokens_used"] == 567


def test_metadata_only_floor_and_positive_allowlist():
    # Spans are metadata-only: a content key is denylist-dropped, AND a key not in
    # the registered positive allowlist is also dropped (ADR-0129 M2 tightening) —
    # so a span can only ever carry the known, vetted metadata fields.
    d = ES.start_details(span_id="spn_1", role="os", engine_id="claude_code")
    d["prompt"] = "secret user text"     # forbidden content → denylist drop
    d["random_unlisted_field"] = "x"     # not in positive allowlist → drop
    filtered, _dropped = _filter(d, ES.ENGINE_SPAN_START)
    assert "prompt" not in filtered, "content leaked into an engine span"
    assert "random_unlisted_field" not in filtered, "positive allowlist not enforced"
    assert filtered["span_id"] == "spn_1"  # known field still survives


def test_roles_are_constrained():
    assert ES.ROLES == frozenset({"os", "manager", "worker"})


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
