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

def _write_skill(tmp_path: Path, code: str) -> Path:
    from tests.test_marketplace_skill_installation_e2e import adr_manifest
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
    from tests.test_marketplace_skill_installation_e2e import adr_manifest

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

def test_welch_p_value_is_real():
    from core.skills.ab_testing import welch_t_test

    a = [10.0, 11.0, 9.5, 10.5, 10.2, 9.8]
    b = [12.0, 12.5, 11.8, 12.2, 13.0, 11.9]
    p, d = welch_t_test(a, b)
    scipy_stats = pytest.importorskip("scipy.stats")
    ref = scipy_stats.ttest_ind(a, b, equal_var=False).pvalue
    assert p == pytest.approx(ref, rel=1e-6)
    assert d > 0
    # identical groups → no evidence of a difference
    assert welch_t_test(a, list(a))[0] == pytest.approx(1.0)


def test_welch_p_value_without_scipy_matches_known_value():
    """t-distribution tail: t=2.0, df=10 → two-sided p = 0.07338803 (tables)."""
    from core.skills.ab_testing import _betainc_reg

    df, t = 10.0, 2.0
    assert _betainc_reg(df / 2, 0.5, df / (df + t * t)) == pytest.approx(0.0733880, abs=1e-6)


def test_insufficient_samples_are_not_reported_as_p_one(tmp_path):
    from core.skills.ab_testing import ABTestingFramework, ExperimentConfig, StatisticalTest

    assert StatisticalTest.t_test([1.0], [2.0, 3.0])[0] is None
    fw = ABTestingFramework(test_dir=tmp_path, audit_emit=lambda *_a, **_k: None)
    fw.create_experiment(ExperimentConfig(
        experiment_id="e", skill_id="os.x", baseline_version="1.0.0",
        variant_version="1.1.0", variant_name="v", rollout_percentage=0,
    ))
    for _ in range(5):
        fw.record_metric("e", "_default", latency_ms=10.0, cost_per_token=0.1, quality_score=0.5)
    res = fw.analyze_experiment("e")
    assert res.winner == "inconclusive"
    assert res.metrics.pvalue is None and not res.metrics.is_significant
    assert res.recommendation.startswith("insufficient_stats")


def test_rollback_policy_survives_reload(tmp_path):
    from core.skills.ab_testing import ABTestingFramework, ExperimentConfig

    fw = ABTestingFramework(test_dir=tmp_path, audit_emit=lambda *_a, **_k: None)
    fw.create_experiment(ExperimentConfig(
        experiment_id="e", skill_id="os.x", baseline_version="1.0.0", variant_version="1.1.0",
        variant_name="v", rollback_on_regression=False, max_regression_pct=0.5,
    ))
    again = ABTestingFramework(test_dir=tmp_path, audit_emit=lambda *_a, **_k: None)
    assert again.experiments["e"].rollback_on_regression is False
    assert again.experiments["e"].max_regression_pct == 0.5


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


def test_ab_default_emitter_writes_the_hash_chain(tmp_path):
    from core.skills.ab_testing import ABTestingFramework, ExperimentConfig

    fw = ABTestingFramework(test_dir=tmp_path)  # default emitter
    fw.create_experiment(ExperimentConfig(
        experiment_id="exp_chain", skill_id="os.x", baseline_version="1.0.0",
        variant_version="1.1.0", variant_name="v",
    ))
    recs = _chain_records("ab_experiment_created")
    assert recs and recs[-1]["experiment_id"] == "exp_chain"
    assert recs[-1]["variant_version"] == "1.1.0"


# --------------------------------------------------------------------------- #
# marketplace_installer.py
# --------------------------------------------------------------------------- #

@pytest.fixture
def installer(tmp_path):
    from core.skills.marketplace_installer import MarketplaceSkillInstaller

    (tmp_path / "mp").mkdir()
    return MarketplaceSkillInstaller(marketplace_root=tmp_path / "mp",
                                     skills_install_dir=tmp_path / "install")


def _pkg(installer, src: Path, **kw):
    from core.skills.marketplace_installer import SkillPackage
    from tests.test_marketplace_skill_installation_e2e import adr_manifest

    src.mkdir(parents=True, exist_ok=True)
    (src / "impl.py").write_text("x = 1\n")
    base = dict(skill_id="os.good", version="1.0.0", source_url=f"local://{src}",
                checksum_sha256=installer._compute_checksum(src), manifest=adr_manifest("os.good"))
    base.update(kw)
    return SkillPackage(**base)


def test_installer_default_emitter_writes_the_hash_chain(installer, tmp_path):
    rec = installer.install_skill(_pkg(installer, tmp_path / "src"))
    started = [r for r in _chain_records("skill_install_initiated") if r.get("install_id") == rec.install_id]
    done = [r for r in _chain_records("skill_installed") if r.get("install_id") == rec.install_id]
    assert started and done
    assert done[0]["skill_id"] == "os.good" and done[0]["checksum_sha256"]


@pytest.mark.parametrize("bad_id", ["../../escaped", "a/b", "..", "/abs"])
def test_installer_refuses_path_escaping_skill_id(installer, tmp_path, bad_id):
    from core.skills.marketplace_installer import SkillVerificationError

    pkg = _pkg(installer, tmp_path / "src", skill_id=bad_id)
    with pytest.raises(SkillVerificationError):
        installer.install_skill(pkg)
    assert not list(tmp_path.rglob("escaped*"))


def test_discovery_skips_plugin_json_with_traversal_id(installer, tmp_path):
    from tests.test_marketplace_skill_installation_e2e import adr_manifest
    import yaml

    d = tmp_path / "mp" / "buildin" / "cat" / "evil"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({"id": "../../../escaped", "version": "1.0.0"}))
    (d / "manifest.yaml").write_text(yaml.dump(adr_manifest()))
    assert installer.discover_skills() == []


def test_installer_verifies_the_checksum(installer, tmp_path):
    from core.skills.marketplace_installer import SkillVerificationError

    pkg = dataclasses.replace(_pkg(installer, tmp_path / "src"), checksum_sha256="0" * 64)
    with pytest.raises(SkillVerificationError, match="checksum"):
        installer.install_skill(pkg)
    assert installer.installations == {}


def test_installer_refuses_missing_source_and_unimplemented_scheme(installer, tmp_path):
    from core.skills.marketplace_installer import SkillVerificationError

    pkg = _pkg(installer, tmp_path / "src")
    with pytest.raises(SkillVerificationError):
        installer.install_skill(dataclasses.replace(pkg, source_url=f"local://{tmp_path}/nope"))
    with pytest.raises(SkillVerificationError, match="not_implemented"):
        installer.install_skill(dataclasses.replace(pkg, source_url="https://example.com/x.zip"))
    assert installer.installations == {}


def test_installer_runs_the_adr0533_validator(installer, tmp_path):
    from core.skills.marketplace_installer import SkillVerificationError

    pkg = _pkg(installer, tmp_path / "src")
    bad = dict(pkg.manifest, learning_signal={"sanitization": {"fail_closed": False}})
    with pytest.raises(SkillVerificationError, match="ADR-0533"):
        installer.install_skill(dataclasses.replace(pkg, manifest=bad))


def test_rolled_back_install_cannot_be_promoted(installer, tmp_path):
    from core.skills.marketplace_installer import DeploymentStage, SkillInstallError

    rec = installer.install_skill(_pkg(installer, tmp_path / "src"))
    installer.rollback_skill(rec.install_id)
    with pytest.raises(SkillInstallError, match="Invalid promotion"):
        installer.promote_canary(rec.install_id, DeploymentStage.PROMOTED)
    rec2 = installer.install_skill(_pkg(installer, tmp_path / "src2", version="1.0.0"))
    with pytest.raises(SkillInstallError):
        installer.promote_canary(rec2.install_id, DeploymentStage.CANARY_10)  # backwards


# --------------------------------------------------------------------------- #
# marketplace_skill_integration.py
# --------------------------------------------------------------------------- #

def test_integration_refuses_to_install_without_a_registry(installer, tmp_path):
    from core.skills.ab_testing import ABTestingFramework
    from core.skills.marketplace_skill_integration import MarketplaceSkillIntegration

    integ = MarketplaceSkillIntegration(
        installer, ABTestingFramework(test_dir=tmp_path / "ab", audit_emit=lambda *_: None))
    with pytest.raises(NotImplementedError):
        integ.install_and_register(_pkg(installer, tmp_path / "src"))
    assert installer.installations == {}  # refused BEFORE installing


def test_canary_routing_is_deterministic_and_at_the_stage_share(installer, tmp_path):
    """CANARY_10 must route ~10% of tenants to the variant. The old code ANDed
    the 10% experiment cohort with a second 10% gate on Python's per-process
    randomised ``hash()`` → ~1%, and a different set after every restart."""
    from core.skills.ab_testing import ABTestingFramework, ExperimentConfig
    from core.skills.marketplace_skill_integration import MarketplaceSkillIntegration

    rec = installer.install_skill(_pkg(installer, tmp_path / "src"))
    ab = ABTestingFramework(test_dir=tmp_path / "ab", audit_emit=lambda *_: None)
    ab.create_experiment(ExperimentConfig(
        experiment_id=f"{rec.skill_id}_{rec.version}_canary", skill_id=rec.skill_id,
        baseline_version="0.9.0", variant_version=rec.version, variant_name="v"))
    integ = MarketplaceSkillIntegration(installer, ab, registry_callback=lambda *_: None)
    got = [integ.get_installed_skill_version(rec.skill_id, f"t{i}") for i in range(4000)]
    share = got.count("1.0.0") / len(got)
    assert 0.08 < share < 0.12, share
    assert got == [integ.get_installed_skill_version(rec.skill_id, f"t{i}") for i in range(4000)]


def test_promotion_without_experiment_does_not_promote(installer, tmp_path):
    from core.skills.ab_testing import ABTestingFramework
    from core.skills.marketplace_installer import DeploymentStage
    from core.skills.marketplace_skill_integration import MarketplaceSkillIntegration

    rec = installer.install_skill(_pkg(installer, tmp_path / "src"))
    integ = MarketplaceSkillIntegration(
        installer, ABTestingFramework(test_dir=tmp_path / "ab", audit_emit=lambda *_: None),
        registry_callback=lambda *_: None)
    out = integ.promote_canary_to_rollout(rec.install_id, 100)
    assert out.deployment_stage == DeploymentStage.CANARY_10
