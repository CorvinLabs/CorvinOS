"""ADR-0169 M1 — gate-pipeline registries + shared invariants + drift guards.

Both live pre-dispatch sequences are characterized (they differ by design — the
ClaudeCode base-engine path omits the engine-allowlist gate and orders
capabilities differently). The drift guards introspect BOTH live functions so a
reorder of EITHER without updating its registry fails CI.

Covers:
  GP/1  GATE_PIPELINES["default"] / ["claudecode"] match their canonical orders.
  GP/2  assert_pipeline_invariants() accepts both live orders.
  GP/3  Negative: egress-before-classification is rejected (I1 bites).
  GP/4  Drift guard — _run_pre_dispatch_gates matches the "default" registry.
  GP/5  Drift guard — _call_claude_streaming_via_engine matches "claudecode".
  GP/6  gate_pipeline_self_test() returns (True, "verified").
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import gate_pipeline as GP  # type: ignore


_CANON_DEFAULT = ["capabilities", "license", "engine_trust",
                  "data_classification", "egress", "house_rules"]
_CANON_CLAUDECODE = ["engine_trust", "data_classification", "egress",
                     "capabilities", "house_rules"]

_MARKERS = {
    "capabilities":        "_check_capabilities_or_fail",
    "license":             '_lic_assert_limit("engines_allowed"',
    "engine_trust":        "_check_engine_trust_or_fail",
    "data_classification": "_check_compliance_or_fail",
    "egress":              "_check_egress_or_fail",
    "house_rules":         "_check_house_rules_or_fail",
}


def test_registries_match_canonical_orders():
    assert [g.key for g in GP.GATE_PIPELINES["default"]] == _CANON_DEFAULT
    assert [g.key for g in GP.GATE_PIPELINES["claudecode"]] == _CANON_CLAUDECODE


def test_invariants_accept_both_live_orders():
    GP.assert_pipeline_invariants(GP.GATE_PIPELINES["default"])
    GP.assert_pipeline_invariants(GP.GATE_PIPELINES["claudecode"])


def test_invariant_rejects_misordered_pipeline():
    base = [g for g in GP.GATE_PIPELINES["default"]]
    dc = next(g for g in base if g.key == "data_classification")
    eg = next(g for g in base if g.key == "egress")
    rest = [g for g in base if g.key not in ("data_classification", "egress")]
    bad = tuple(rest[:2] + [eg, dc] + rest[2:])  # egress before classification
    with pytest.raises(ValueError):
        GP.assert_pipeline_invariants(bad)


def test_no_invalid_fail_mode():
    for pl in GP.GATE_PIPELINES.values():
        for g in pl:
            assert g.fail_mode in ("closed", "two-tier"), g


def _order_in_source(src: str, keys: list[str]) -> list[str]:
    positions = []
    for key in keys:
        marker = _MARKERS[key]
        idx = src.find(marker)
        assert idx >= 0, f"gate marker for {key!r} ({marker!r}) not found"
        positions.append((idx, key))
    return [k for _, k in sorted(positions)]


def _adapter():
    try:
        import adapter  # type: ignore
        return adapter
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"adapter import unavailable: {e}")


def test_default_path_matches_registry():
    src = inspect.getsource(_adapter()._run_pre_dispatch_gates)
    assert _order_in_source(src, _CANON_DEFAULT) == _CANON_DEFAULT


def test_claudecode_path_matches_registry():
    # The ClaudeCode path has NO license gate; check only the keys it contains,
    # and assert the license marker is genuinely ABSENT (by-design difference).
    src = inspect.getsource(_adapter()._call_claude_streaming_via_engine)
    assert _MARKERS["license"] not in src, "ClaudeCode path unexpectedly grew a license gate"
    assert _order_in_source(src, _CANON_CLAUDECODE) == _CANON_CLAUDECODE


def test_self_test_ok():
    ok, reason = GP.gate_pipeline_self_test()
    assert ok and reason == "verified"


def test_self_test_failed_event_is_critical():
    # GP/7 (security review 2026-06-27): a mis-ordered chain must surface as a
    # CRITICAL audit event, not the default INFO. Enshrine the severity mapping
    # so a future audit-map edit can't silently downgrade it.
    import audit  # noqa: PLC0415
    assert audit._VOICE_EVENT_SEVERITY.get("gate_pipeline.self_test_failed") == "CRITICAL"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
