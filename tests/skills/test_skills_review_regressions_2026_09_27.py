"""Regression tests for the 2026-09-27 adversarial review of core/skills.

Each test fails on the pre-fix code and passes after it:

* SkillInstaller built its install path from caller-supplied ``skill_id`` /
  ``version`` unvalidated — ``../..`` wrote the ZIP outside ``install_root``
  (reachable unauthenticated through the console skill-manager upload route).
* SkillInstaller had no ``uninstall_skill`` although the console route calls it.
* ABTesting cost/quality "means" decayed toward 0 with sample size.
* Phase 2a dual-write treated ``registry.execute()``'s SkillExecutionResult as a
  dict, so the Skill was never consulted, and its audit events imported a
  module that does not exist, so nothing reached the chain.
* os.context_adapter read ``routing_decision["engine"]`` after the router's
  output key became ``decision`` — every L10 execution failed.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


def _install(installer, tmp_path: Path, skill_id: str, version: str):
    data = _zip_bytes({"impl.py": "def run(): pass"})
    zp = tmp_path / "pkg.zip"
    zp.write_bytes(data)
    return installer.install_skill(
        zp, hashlib.sha256(data).hexdigest(), {"skill_id": skill_id, "version": version}
    )


# --------------------------------------------------------------------------- #
# SkillInstaller
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "skill_id,version",
    [
        ("../../../escaped", "1.0.0"),
        ("ok-skill", "../../escaped"),
        ("a/b", "1.0.0"),
        ("..", "1.0.0"),
        ("ok-skill", "."),
        ("/abs", "1.0.0"),
    ],
)
def test_installer_refuses_path_escaping_ids(tmp_path, skill_id, version):
    from core.skills.skill_installer import SkillInstaller

    root = tmp_path / "root" / "skills_installed"
    installer = SkillInstaller(root)
    ok, msg = _install(installer, tmp_path, skill_id, version)
    assert not ok, msg
    written = [p for p in tmp_path.rglob("impl.py")]
    assert written == [], f"ZIP content written despite refusal: {written}"
    assert installer._load_registry() == {}


def test_installer_uninstall_roundtrip(tmp_path):
    from core.skills.skill_installer import SkillInstaller

    installer = SkillInstaller(tmp_path / "root")
    ok, msg = _install(installer, tmp_path, "good-skill", "1.0.0")
    assert ok, msg
    assert (tmp_path / "root" / "good-skill" / "1.0.0" / "impl.py").exists()

    ok, msg = installer.uninstall_skill("good-skill", "1.0.0")
    assert ok, msg
    assert not (tmp_path / "root" / "good-skill").exists()
    assert installer._load_registry() == {}
    assert installer.uninstall_skill("good-skill", "1.0.0")[0] is False
    assert installer.uninstall_skill("../x", "1.0.0")[0] is False


def test_installer_stale_temp_dir_not_merged(tmp_path):
    from core.skills.skill_installer import SkillInstaller

    installer = SkillInstaller(tmp_path / "root")
    stale = tmp_path / "root" / "good-skill" / ".1.0.0_tmp"
    stale.mkdir(parents=True)
    (stale / "leftover.py").write_text("x")
    ok, msg = _install(installer, tmp_path, "good-skill", "1.0.0")
    assert ok, msg
    assert not (tmp_path / "root" / "good-skill" / "1.0.0" / "leftover.py").exists()


def test_skill_manager_upload_route_cannot_escape_install_root(tmp_path, monkeypatch):
    """Through the real HTTP boundary (console skill-manager upload route)."""
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from corvin_console.routes import skill_manager

    app = fastapi.FastAPI()
    app.include_router(skill_manager.router, prefix="/v1/console/skills-manager")
    client = TestClient(app)
    resp = client.post(
        "/v1/console/skills-manager/skills/install",
        files={"file": ("a.zip", _zip_bytes({"pwned.txt": "x"}))},
        data={"skill_id": "../../../escaped", "version": "1"},
    )
    assert resp.status_code >= 400
    assert list(tmp_path.rglob("pwned.txt")) == []


# --------------------------------------------------------------------------- #
# A/B testing
# --------------------------------------------------------------------------- #

def test_ab_metrics_are_true_running_means(tmp_path):
    from core.skills.ab_testing import ABTestingFramework, ExperimentConfig

    fw = ABTestingFramework(test_dir=tmp_path, audit_emit=lambda *_a, **_k: None)
    fw.create_experiment(ExperimentConfig(
        experiment_id="exp1", skill_id="os.x", baseline_version="1.0.0",
        variant_version="1.1.0", variant_name="v", rollout_percentage=0,
    ))
    for _ in range(20):  # rollout 0% → every tenant is CONTROL
        fw.record_metric("exp1", "_default", latency_ms=10.0,
                         cost_per_token=0.5, quality_score=0.8)
    m = fw.metrics["exp1"]
    assert m.sample_size_control == 20
    assert m.quality_score_control == pytest.approx(0.8)
    assert m.cost_per_token_control == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# L5 Phase 2a dual-write + L10 context adapter (real registry, real chain)
# --------------------------------------------------------------------------- #

@pytest.fixture
def booted(tmp_path, monkeypatch):
    home = tmp_path / "corvin-home"
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    from core.skills.boot import boot_skills

    assert "os.delegation_router" in boot_skills("_default", wire_learning=False)
    from core.skills.os_skills.monitoring import dual_write

    dual_write.initialize_dual_write(storage_dir=tmp_path / "metrics")
    return chain


def _events(chain: Path, event_type: str) -> list[dict]:
    out = []
    if not chain.exists():
        return out
    for line in chain.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("event_type") == event_type:
            out.append(rec.get("details") or rec)
    return out


def test_dual_write_fetch_consults_the_skill(booted):
    from core.skills.os_skills.monitoring.dual_write import _fetch_skill_decision

    out = _fetch_skill_decision(complexity=3, task_type="chat", force_delegate=False,
                                is_big_data=False, tenant_id="_default")
    assert out["decision"] == "native"
    assert out["confidence"] > 0.0, out  # 0.0 == the error fallback


def test_phase2_routing_is_audited_to_tenant_chain(booted, monkeypatch):
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    from corvin_operator.bridges.shared.delegation_policy import resolve_worker_engine

    engine = resolve_worker_engine(mode="acs", force_delegate=False, is_big_data=False,
                                   tde_available=True, quota_ok=True, tenant_id="_default")
    # Skill advice (native @0.90) clears the 0.75 gate and is a permitted
    # de-escalation of the bundled "acs".
    assert engine == "native"
    recs = _events(booted, "l5_routing_dual_write")
    assert recs, "Phase 2a routing decision missing from the audit chain"
    rec = recs[-1]
    assert rec["decision_source"] == "skill"
    assert rec["used_engine"] == "native"
    assert rec["bundled_engine"] == "acs"
    assert rec["skill_engine"] == "native"
    assert rec["tenant_id"] == "_default"
    assert _events(booted, "l5_routing_metrics")


def test_context_adapter_executes_after_router_key_change(booted):
    from core.skills.skill_registry_phase1 import get_registry

    res = get_registry().execute(
        "os.context_adapter",
        {"complexity": 5, "task_type": "chat", "task_description": "x", "tenant_id": "_default"},
        timeout_ms=5000,
        lom="corvin_operator/bridges/shared/delegation_policy.py:_acp_shadow_route",
        tenant_id="_default",
    )
    assert res.status == "success", res.error_message
    assert res.output["base_tier"]["engine"] in ("native", "acs", "tde")


# --------------------------------------------------------------------------- #
# ADR-0952 Tier 2.9 classifier (core.skills.os_skills.model_selector)
# --------------------------------------------------------------------------- #

def test_model_selector_module_still_exports_the_classifier():
    """4036473fe replaced the module with Tier1Router only; the Tier 2.9
    resolver looks ``ModelSelector`` up by name and silently abstained."""
    import core.skills.os_skills.model_selector as ms

    assert hasattr(ms, "ModelSelector") and hasattr(ms, "ClassificationResult")
    assert ms.COMPLEXITY_LEVELS == ("simple", "medium", "complex")
    assert hasattr(ms, "Tier1Router")  # the k=2 router is kept alongside
    result = ms.ModelSelector().classify("hi", "_default")
    assert result.complexity == "simple"
    import core.models.model_selection_routing  # noqa: F401 — imported ModelSelector at module level


def test_tier29_classify_os_model_routes_again(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin-home"))
    import core.skills.os_skills.model_selector  # noqa: F401 — the warm-up import
    from corvin_operator.bridges.shared import model_selector as bridge_ms

    assert bridge_ms.classify_os_model("hi", tenant_id="_default") is not None
