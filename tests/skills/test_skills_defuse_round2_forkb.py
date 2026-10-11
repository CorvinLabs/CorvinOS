"""Regression tests, adversarial review round 2 (2026-09-27): DEFUSE of the
unwired core/skills marketplace / A-B / manifest / versioning modules.

Every test here fails on the pre-fix code and passes after it.
"""
from __future__ import annotations

import dataclasses
import json
import textwrap
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# compiler.py
# --------------------------------------------------------------------------- #

# Copied from the removed tests/test_marketplace_skill_installation_e2e.py (its subject, the
# marketplace installer, was deleted); the compiler/validator tests below still need it.
def adr_manifest(name: str = "os.test_router", version: str = "1.0.0") -> dict:
    """An ADR-0533 conformant manifest (the installer now runs the validator)."""
    return {
        "name": name,
        "version": version,
        "goal": "Test routing decision for the installer E2E",
        "description": "Minimal ADR-0533 manifest used by the marketplace installer tests",
        "triggers": [{
            "name": "before_delegation", "event_type": "decision_point",
            "phase": "pre_routing", "condition": "every_turn",
            "async_allowed": False, "timeout_ms": 5000,
        }],
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "learning_signal": {
            "metrics": ["latency_actual_vs_predicted"],
            "scoring_rule": "mde < 5%",
            "feedback_sources": [{"event_type": "turn_completed", "extract": ["latency"]}],
            "sanitization": {"disallow_fields": ["prompt"], "pii_patterns": ["email"],
                             "fail_closed": True},
        },
        "boot_layer": "installed",
        "origin": "community",
        "scope": "local_development",
    }


def _write_skill(tmp_path: Path, code: str) -> Path:
    import yaml

    d = tmp_path / "skill"
    d.mkdir()
    (d / "manifest.yaml").write_text(yaml.dump(adr_manifest("os.bad_skill")))
    (d / "skill.py").write_text(textwrap.dedent(code))
    return d


def test_compiler_is_importable_as_a_package_module():
    import core.skills.compiler  # noqa: F401 — used to fail: `from skill_validator import`


@pytest.mark.parametrize("code", [
    "from corvin.skills import brain_loader\n",
    "import corvin.skills.brain_loader\n",
    "from corvin.skills.brain_loader import infer\n",
])
def test_compiler_blocks_every_sealed_import_form(tmp_path, code):
    from core.skills.compiler import SkillCompiler

    report = SkillCompiler().compile_skill(_write_skill(tmp_path, code))
    assert not report.is_valid
    assert any("Direct import forbidden" in e for e in report.errors), report.errors


def test_compiler_does_not_flag_stdlib_attribute_calls(tmp_path):
    from core.skills.compiler import SkillCompiler

    code = """
    import os
    from corvin.skills import SkillRuntime

    def execute(input_data):
        return {"p": os.path.join("a", "b")}
    """
    report = SkillCompiler().compile_skill(_write_skill(tmp_path, code))
    assert report.is_valid, report.errors


# --------------------------------------------------------------------------- #
# skill_validator.py
# --------------------------------------------------------------------------- #

def test_dict_and_file_validation_agree(tmp_path):
    """The dict path skipped checks 2-13: an unparseable scoring_rule passed."""
    import yaml
    from core.skills.skill_validator import SkillValidator

    m = adr_manifest()
    m["learning_signal"]["scoring_rule"] = "vibes are good"
    f = tmp_path / "manifest.yaml"
    f.write_text(yaml.dump(m))
    v = SkillValidator()
    by_dict, by_file = v.validate_manifest_dict(m), v.validate_manifest(f)
    assert not by_dict.is_valid and not by_file.is_valid
    assert by_dict.blockers == by_file.blockers


def test_validator_reports_all_problems_and_survives_mistyped_fields():
    from core.skills.skill_validator import SkillValidator

    r = SkillValidator().validate_manifest_dict(
        {"name": "x", "version": 1.0, "triggers": ["t"], "learning_signal": "s"}
    )
    assert not r.is_valid
    assert any("Missing required field: scope" in b for b in r.blockers)
    assert any("Invalid version format" in b for b in r.blockers)  # no early return


# --------------------------------------------------------------------------- #
# version_manager.py
# --------------------------------------------------------------------------- #

def test_latest_stable_excludes_prereleases(tmp_path):
    from core.skills.version_manager import SkillVersionManager, VersionResolver

    r = VersionResolver(SkillVersionManager(tmp_path))
    assert r.resolve_version("os.x", ["1.2.3", "2.0.0-beta", "10.0.0-rc1", "1.10.0"]) == "1.10.0"
    with pytest.raises(ValueError, match="stable"):
        r.resolve_version("os.x", ["2.0.0-beta"])


def test_unavailable_pin_fails_instead_of_returning_latest(tmp_path):
    from core.skills.version_manager import SkillVersionManager, TenantVersionPin, VersionResolver

    r = VersionResolver(SkillVersionManager(tmp_path))
    pin = TenantVersionPin({"os.x": {"version": "1.2.3"}})
    with pytest.raises(ValueError, match="not available"):
        r.resolve_version("os.x", ["1.3.0"], pin)
    beta_pin = TenantVersionPin({"os.x": {"version": "2.0.0-beta"}})
    assert r.resolve_version("os.x", ["1.3.0", "2.0.0-beta"], beta_pin) == "2.0.0-beta"


def test_dependency_range_does_not_resolve_to_a_prerelease(tmp_path):
    from core.skills.version_manager import SkillVersionManager, VersionResolver

    r = VersionResolver(SkillVersionManager(tmp_path))
    assert r.resolve_dependency_version("d", ">=1.0.0", ["1.1.0", "2.0.0-beta"]) == "1.1.0"
    assert r.resolve_dependency_version("d", ">=2.0.0-alpha", ["1.1.0", "2.0.0-beta"]) == "2.0.0-beta"


def test_version_manager_default_state_dir_honours_corvin_home(tmp_path, monkeypatch):
    from core.skills.version_manager import SkillVersionManager

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "ch"))
    assert str(SkillVersionManager().state_dir).startswith(str(tmp_path / "ch"))


# --------------------------------------------------------------------------- #
# ab_testing.py
# --------------------------------------------------------------------------- #









def _chain_records(event_type: str) -> list:
    import os

    chain = Path(os.environ["VOICE_AUDIT_PATH"])
    if not chain.exists():
        return []
    out = []
    for line in chain.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("event_type") == event_type:
            out.append(rec.get("details") or {})
    return out




# --------------------------------------------------------------------------- #
# marketplace_installer.py
# --------------------------------------------------------------------------- #



















# --------------------------------------------------------------------------- #
# marketplace_skill_integration.py
# --------------------------------------------------------------------------- #





