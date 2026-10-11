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


# The Phase 2a module's own entry points (_fetch_skill_decision, resolve_worker_engine_dual_write,
# initialize_dual_write) were removed with the ADR-2092 rewrite; the gated Phase 2 path is covered
# by the tests below and tests/e2e/test_os_skills_l5_l10_wiring.py.


@pytest.mark.parametrize("phase,reason", [
    ("phase2_real", "phase2_real_unsupported"),
    ("phase2_dual_write", None),   # refused by the readiness gate on a fresh install
])
def test_phase2_env_cannot_change_served_routing(booted, monkeypatch, phase, reason):
    """Round 2: CORVIN_ACP_PHASE=phase2_* used to serve the Skill's engine ("native")
    over the operator's selection ("acs") with no recovery path. Since ADR-2092 the
    request is honoured only when every gate clears; here none does, so the bundled
    engine is served, the refusal is audited once per process (not once per turn),
    and the shadow record is still kept."""
    from core.skills.os_skills.monitoring import dual_write
    from corvin_operator.bridges.shared import delegation_policy as dp

    dual_write.reset_for_tests()
    monkeypatch.setenv("CORVIN_ACP_PHASE", phase)
    for _ in range(3):
        engine = dp.resolve_worker_engine(mode="acs", force_delegate=False, is_big_data=False,
                                          tde_available=True, quota_ok=True, tenant_id="_default",
                                          sink={})
        assert engine == "acs"
    assert _events(booted, "routing.phase2_decision") == []
    refused = _events(booted, "routing.phase2_refused")
    # Once per process PER REASON, never once per turn. The readiness gate may name two
    # reasons across three turns ("evaluating" while its cache warms, then the verdict).
    reasons = [r["reason"] for r in refused]
    assert refused and len(reasons) == len(set(reasons)) <= (1 if reason else 2), refused
    assert all(r["phase_requested"] == phase for r in refused)
    if reason:
        assert reasons == [reason]
    shadow = [r for r in _events(booted, "skill.executed")
              if "delegation_policy.py" in json.dumps(r)]
    assert len(shadow) == 3


def test_phase2_is_reachable_only_through_the_gates(booted, monkeypatch):
    """An unknown phase value degrades to shadow; a cleared request still needs the
    Skill booted in this process."""
    from core.skills.os_skills.monitoring import dual_write

    dual_write.reset_for_tests()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase3_bogus")
    assert dual_write.requested_phase() == "phase1_shadow"
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    assert dual_write.requested_phase() == "phase2_dual_write"
    assert dual_write.effective_phase(tenant_id="_default", surface="console",
                                      skill_available=False) == "shadow"


@pytest.mark.parametrize("force,big", [(True, False), (False, True)])
def test_shadow_route_passes_delegate_and_big_data_signals(booted, force, big):
    """Round 2: the shadow route dropped force_delegate / is_big_data, so the Skill
    answered "native" where the bundled rule (correctly) chose "acs" — a fabricated
    disagreement fed into the learning signal."""
    from core.skills import skill_registry_phase1 as reg
    from corvin_operator.bridges.shared import delegation_policy as dp

    seen = []
    real_execute = reg._global_registry.execute

    def spy(skill_id, payload, **kw):
        res = real_execute(skill_id, payload, **kw)
        if skill_id == "os.delegation_router":
            seen.append(res.output)
        return res

    reg._global_registry.execute = spy
    try:
        # ``sink`` makes this a recorded turn; without it the call is a pure query
        # that never executes the Skill.
        engine = dp.resolve_worker_engine(mode="native", force_delegate=force, is_big_data=big,
                                          tde_available=False, quota_ok=True, tenant_id="_default",
                                          sink={})
    finally:
        reg._global_registry.execute = real_execute
    assert engine == "acs"
    assert seen and seen[-1]["decision"] == "acs", seen
    assert seen[-1]["bundled_engine"] == "acs"


def test_context_adapter_executes_after_router_key_change(booted):
    from core.skills.skill_registry_phase1 import get_registry

    res = get_registry().execute(
        "os.context_adapter",
        {"complexity": 5, "task_type": "chat", "task_description": "x", "tenant_id": "_default"},
        timeout_ms=5000,
        lom="corvin_operator/bridges/shared/delegation_policy.py:route_and_record",
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


# --------------------------------------------------------------------------- #
# Round 2: os.workflow_optimizer (module failed to import; logged prompts)
# --------------------------------------------------------------------------- #

def test_workflow_optimizer_imports_and_chains_content_free(tmp_path, monkeypatch, caplog):
    home = tmp_path / "corvin-home"
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    from core.skills.os_skills.workflow_optimizer.skill import (
        RoutingInput, TaskComplexity, WorkflowOptimizer,
    )

    opt = WorkflowOptimizer(config_path=str(tmp_path / "cfg" / "x.json"))
    secret = "Design a distributed system for patient ABC-SECRET-123 with encryption"
    with caplog.at_level("DEBUG"):
        d = opt.route_task(RoutingInput(task_id="t1", task_content=secret, tenant_id="_default"))
    assert d.complexity == TaskComplexity.COMPLEX  # was unreachable
    assert "ABC-SECRET-123" not in caplog.text
    raw = chain.read_text()
    assert "ABC-SECRET-123" not in raw
    recs = _events(chain, "workflow_routing_decision")
    assert recs and recs[-1]["complexity"] == "complex"


def test_video_producer_audit_writer_resolves_in_a_fresh_process():
    """Round 2: in a fresh process (the Blender CLI) with only the repo root and
    corvin_operator on PYTHONPATH, ``forge`` resolved to the corvin_operator/forge
    directory as a namespace package and every CLI render failed audit-first."""
    import os
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = f"{repo}:{repo / 'corvin_operator'}"
    proc = subprocess.run(
        [sys.executable, "-c",
         "import forge; from core.skills.os_skills.video_producer.audit import _core_write_event;"
         "fn = _core_write_event(); print(fn.__module__)"],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert proc.stdout.strip() == "forge.security_events"
