#!/usr/bin/env python3
"""Per-subtask E2E for engine_policy.py + compliance_zone_classifier.py
(ADR-0004 Phase 5 skeleton).

Run: python3 operator/bridges/shared/test_engine_policy.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import engine_policy as ep  # type: ignore  # noqa: E402
import compliance_zone_classifier as czc  # type: ignore  # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


def main() -> int:
    # ── Block A — engine_policy ──────────────────────────────────────
    print("\n── engine_policy: load + validate ─────────────────────")

    p = ep.EnginePolicy.from_dict({
        "default_engine": "claude_code",
        "fallback_chain": ["claude_code", "vllm_eu_west", "ollama_local"],
        "compliance_zones": {
            "personal_data": {
                "allow_engines": ["azure_openai_eu", "vllm_eu_west"],
                "deny_engines": ["claude_code"],
                "audit_severity": "WARNING",
            },
            "code_only": {"allow_engines": ["claude_code", "codex_cli"]},
            "open_zone": {},
        },
    })
    expect(p.default_engine == "claude_code", "default_engine parsed")
    expect(p.default_chain()[0] == "claude_code",
           "default_chain first id is the default")
    expect("vllm_eu_west" in p.default_chain(),
           "fallback_chain entries propagate")
    expect(p.allow_engines_for("personal_data") ==
           ["azure_openai_eu", "vllm_eu_west"],
           "personal_data zone — allow ∩ ¬deny")
    expect(p.allow_engines_for("code_only") ==
           ["claude_code", "codex_cli"],
           "code_only zone — explicit allow only")
    expect(p.allow_engines_for("unknown_zone") == p.default_chain(),
           "unknown zone → default_chain (legacy fallback)")
    expect(p.allow_engines_for(None) == p.default_chain(),
           "None zone → default_chain")
    expect(p.allow_engines_for("open_zone") == p.default_chain(),
           "empty allow + no deny → default_chain (catch-all)")
    expect(p.severity_for("personal_data") == "WARNING",
           "severity_for picks zone-specific severity")
    expect(p.severity_for("code_only") == "INFO",
           "severity_for defaults to INFO when zone omits it")
    expect(p.severity_for(None) == "INFO",
           "severity_for None → INFO")
    expect(p.severity_for("unknown_zone") == "INFO",
           "severity_for unknown → INFO")

    # ── deny-beats-allow + ordering preserved ────────────────────────
    p2 = ep.EnginePolicy.from_dict({
        "default_engine": "claude_code",
        "compliance_zones": {
            "z": {
                "allow_engines": ["a", "b", "c", "d"],
                "deny_engines": ["b"],
            },
        },
    })
    expect(p2.allow_engines_for("z") == ["a", "c", "d"],
           "deny removes from allow, order preserved",
           f"got {p2.allow_engines_for('z')}")

    # ── from_file: missing file → None ───────────────────────────────
    expect(ep.EnginePolicy.from_file("/no/such/path") is None,
           "from_file(missing) → None gracefully")

    # ── from_file: valid file ────────────────────────────────────────
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w",
                                     delete=False) as f:
        json.dump({"default_engine": "claude_code"}, f)
        tmp = f.name
    p3 = ep.EnginePolicy.from_file(tmp)
    expect(p3 is not None and p3.default_engine == "claude_code",
           "from_file(valid) → policy")
    Path(tmp).unlink()

    # ── from_dict: malformed inputs raise ValueError ─────────────────
    print("\n── engine_policy: schema validation ────────────────────")
    bad_cases = [
        ({}, "missing default_engine"),
        ({"default_engine": ""}, "empty default_engine"),
        ({"default_engine": 42}, "non-string default_engine"),
        ({"default_engine": "x", "fallback_chain": "not a list"}, "non-list fallback_chain"),
        ({"default_engine": "x", "fallback_chain": [1, 2]}, "non-string fallback entries"),
        ({"default_engine": "x", "compliance_zones": "not a dict"}, "non-dict zones"),
        ({"default_engine": "x", "compliance_zones": {"z": "not a dict"}}, "non-dict zone body"),
        ({"default_engine": "x", "compliance_zones": {"z": {"allow_engines": "not a list"}}}, "non-list allow"),
        ({"default_engine": "x", "compliance_zones": {"z": {"audit_severity": "BANANA"}}}, "invalid severity"),
    ]
    for bad_data, label in bad_cases:
        raised = False
        try:
            ep.EnginePolicy.from_dict(bad_data)
        except ValueError:
            raised = True
        expect(raised, f"raises ValueError: {label}")

    # ── validate_against_registry warnings ───────────────────────────
    print("\n── engine_policy: registry validation ───────────────────")
    p4 = ep.EnginePolicy.from_dict({
        "default_engine": "fictional_engine",
        "fallback_chain": ["claude_code", "another_fake"],
        "compliance_zones": {
            "z": {"allow_engines": ["typo_engine"], "deny_engines": ["bogus"]},
        },
    })
    warnings = p4.validate_against_registry({"claude_code", "codex_cli"})
    expect(any("default_engine" in w for w in warnings),
           "warning for unknown default_engine")
    expect(any("another_fake" in w for w in warnings),
           "warning for unknown fallback engine")
    expect(any("typo_engine" in w for w in warnings),
           "warning for unknown allow engine")
    expect(any("bogus" in w for w in warnings),
           "warning for unknown deny engine")
    expect(p4.validate_against_registry({"fictional_engine", "claude_code", "another_fake", "typo_engine", "bogus"}) == [],
           "all-known registry → no warnings")

    expect(sorted(p4.list_zones()) == ["z"],
           "list_zones returns the declared zones")

    # ── Block B — compliance_zone_classifier ────────────────────────
    print("\n── classify_zone: explicit marker ──────────────────────")
    out = czc.classify_zone("[zone:personal_data] schreib mal eine Mail")
    expect(out["zone"] == "personal_data" and out["confidence"] == 1.0,
           "[zone:personal_data] marker triggers")
    out = czc.classify_zone("[zone:custom_zone_42] something")
    expect(out["zone"] == "custom_zone_42",
           "any zone name passes through marker")
    out = czc.classify_zone("[zone:CODE_ONLY] git status",
                            persona="inbox")
    expect(out["zone"] == "code_only",
           "marker beats persona hint (and lower-cases)")

    # ── PII regex zone ──────────────────────────────────────────────
    print("\n── classify_zone: PII regex ────────────────────────────")
    # Note: PII signal tags can overlap (an IBAN substring also matches
    # the 13-19-digit credit-card heuristic). The zone classification
    # is primary; the specific signal tag is informational and order-
    # dependent. We assert zone always, and signal-tag only where the
    # pattern is unambiguous (no possible overlap with other classes).
    pii_cases = [
        ("schreib eine Mail an test.user@example.com", "email", True),
        ("ruf +49 30 12345678 zurück",                       "phone", True),
        ("IBAN ist DE89 3704 0044 0532 0130 00",             "iban",  False),
        ("Kreditkarte 4012 8888 8888 1881 abbuchen",         "credit_card", True),
        ("SSN 123-45-6789 prüfen",                           "ssn",   True),
        ("AHV 756.1234.5678.97 hinterlegt",                  "ahv",   True),
    ]
    for text, tag, assert_signal in pii_cases:
        out = czc.classify_zone(text)
        expect(out["zone"] == "personal_data",
               f"PII '{tag}' → personal_data",
               f"got {out['zone']} signals={out['signals']}")
        if assert_signal:
            expect(any(tag in s for s in out["signals"]),
                   f"PII '{tag}' signal recorded")
        else:
            expect(any("pii:" in s for s in out["signals"]),
                   f"PII '{tag}' has at least one pii:* signal "
                   f"(specific tag may be overlap-aliased)",
                   f"got {out['signals']}")

    # ── Persona-hint zone ───────────────────────────────────────────
    print("\n── classify_zone: persona hints ────────────────────────")
    # browser + jarvis removed from bundle in f1e3246
    persona_cases = [
        ("inbox",         "personal_data"),
        ("coder",         "code_only"),
        ("forge",         "code_only"),
        ("research",      "external_facing"),
        ("homeassistant", "personal_data"),
        ("assistant",     "general"),
    ]
    for persona, expected_zone in persona_cases:
        out = czc.classify_zone("Mach was Sinnvolles", persona=persona)
        expect(out["zone"] == expected_zone,
               f"persona '{persona}' → {expected_zone}",
               f"got {out['zone']}")

    # ── Default fallback zone ───────────────────────────────────────
    out = czc.classify_zone("Mach was Sinnvolles")
    expect(out["zone"] == "general" and out["signals"] == [],
           "no signal → general / empty signals")

    # ── Empty input ─────────────────────────────────────────────────
    out = czc.classify_zone("")
    expect(out["zone"] == "general" and out["reason"] == "empty task",
           "empty task → general (default)")
    out = czc.classify_zone("   ")
    expect(out["zone"] == "general",
           "whitespace-only → general")

    # ── PII overrides persona ───────────────────────────────────────
    out = czc.classify_zone("Mail: foo@example.com", persona="coder")
    expect(out["zone"] == "personal_data",
           "PII overrides persona hint",
           f"got {out['zone']} signals={out['signals']}")

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
