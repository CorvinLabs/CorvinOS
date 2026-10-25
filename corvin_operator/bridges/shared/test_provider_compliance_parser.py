#!/usr/bin/env python3
"""Parser rules for spec.data_classification.provider_compliance (T-0228, ADR-2268 Decision 2).

Declared facts about a provider used by a rerouted claude_code turn. Stricter than
engine_compliance: ``unknown`` locality and ``network_egress: none`` are rejected, a platform
provider needs its region, and a malformed entry drops only that provider. It never switches
the matrix guard off.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import data_classification as dc  # type: ignore  # noqa: E402


def _cfg(*entries) -> dict:
    return {"spec": {"data_classification": {"provider_compliance": list(entries)}}}


def _ok(**kw) -> dict:
    base = {"provider": "acme_llm", "locality": "eu_cloud", "network_egress": "external",
            "endpoint_host": "llm.acme.example"}
    base.update(kw)
    return base


def test_a_valid_entry_is_parsed():
    out, bad = dc.parse_provider_compliance(_cfg(_ok()))
    assert bad == []
    assert out["acme_llm"].locality == "eu_cloud"
    assert out["acme_llm"].network_egress == "external"
    assert out["acme_llm"].endpoint_host == "llm.acme.example"


def test_unknown_locality_is_rejected_and_only_that_provider_drops():
    out, bad = dc.parse_provider_compliance(_cfg(_ok(locality="unknown"), _ok(provider="other", locality="local")))
    assert "acme_llm" not in out
    assert out["other"].locality == "local"
    assert [b["provider"] for b in bad] == ["acme_llm"]


def test_network_egress_none_is_rejected_for_a_provider():
    out, bad = dc.parse_provider_compliance(_cfg(_ok(network_egress="none")))
    assert out == {} and len(bad) == 1


def test_a_missing_locality_is_rejected_not_defaulted():
    entry = _ok()
    del entry["locality"]
    out, bad = dc.parse_provider_compliance(_cfg(entry))
    assert out == {} and len(bad) == 1


def test_an_endpoint_host_is_required():
    entry = _ok()
    del entry["endpoint_host"]
    out, bad = dc.parse_provider_compliance(_cfg(entry))
    assert out == {} and len(bad) == 1


def test_a_platform_provider_needs_region_and_models():
    # The pure parser cannot read the provider registry, so the entry declares platform=true.
    # Checking that claim against load_providers() belongs to the use site (validate_provider).
    base = _ok(provider="bedrock", locality="eu_cloud", platform=True)
    out, bad = dc.parse_provider_compliance(_cfg(base))
    assert out == {}, "no region, no models: not eligible"
    complete = dict(base, region="eu-central-1", models=["anthropic.claude-sonnet-5"])
    out, bad = dc.parse_provider_compliance(_cfg(complete))
    assert bad == [] and out["bedrock"].region == "eu-central-1"
    assert out["bedrock"].models == ("anthropic.claude-sonnet-5",)


def test_malformed_shapes_drop_the_entry_never_the_section():
    cases = [_ok(provider=""), _ok(provider=None), _ok(provider=["x"]), "not-a-dict", None]
    out, bad = dc.parse_provider_compliance(_cfg(*cases))
    assert out == {}
    assert len(bad) == len(cases)


def test_a_non_list_section_is_nothing_not_an_error():
    assert dc.parse_provider_compliance({"spec": {"data_classification": {"provider_compliance": "x"}}}) == ({}, [])
    assert dc.parse_provider_compliance({}) == ({}, [])
    assert dc.parse_provider_compliance(None) == ({}, [])


def test_a_duplicate_provider_is_rejected_in_both_entries():
    out, bad = dc.parse_provider_compliance(_cfg(_ok(), _ok(locality="local")))
    assert "acme_llm" not in out
    assert len(bad) == 2


def test_the_parser_never_touches_the_matrix_guard():
    """A broken provider entry must not change the engine matrix the guard enforces."""
    cfg = {"spec": {"data_classification": {"matrix": {"CONFIDENTIAL": ["local"]},
                                             "provider_compliance": [_ok(locality="unknown")]}}}
    guard = dc.DataFlowGuard.from_tenant_config(cfg)
    assert guard.matrix[dc.DataClassification.CONFIDENTIAL] == frozenset({"local"})
