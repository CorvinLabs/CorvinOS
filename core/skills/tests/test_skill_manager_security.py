"""SkillManager security contract (adversarial review D-02, D-07c, D-16).

* install_skill refuses a manifest name that escapes the tenant skills dir
* SkillExecutor.execute ENFORCES timeout_ms (thread join)
* manifest sanitization.disallow_fields applied before run_state persistence
* execute_skill resolves by the manifest trigger, never a hard-coded id
* registry.yaml RMW is locked (concurrent registrations all survive)
* every execution chains a metadata-only skill.executed event
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from core.skills.skill_manager import SkillExecutor, SkillManager

MANIFEST_TMPL = """
name: {name}
version: "{version}"
goal: "test"
triggers:
  - name: {trigger}
    event_type: decision_point
learning_signal:
  sanitization:
    disallow_fields: [prompt, api_key]
"""


def _bundle(root: Path, name: str, version: str = "1.0.0", trigger: str = "before_delegation_decision") -> Path:
    d = root / "bundles" / f"{name.replace('/', '_')}_{version}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(MANIFEST_TMPL.format(name=name, version=version, trigger=trigger))
    (d / "SKILL.md").write_text("# skill\n")
    return d


def _chain_events(home: Path, tenant: str = "_default") -> list[dict]:
    chain = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


@pytest.fixture
def home(tmp_path):
    return tmp_path / "corvin_home"


class TestInstallTraversal:

    @pytest.mark.parametrize("name", ["../../../escaped", "../x", "a/b", "/abs", "..", ".hidden", "Upper"])
    def test_escaping_or_invalid_name_is_refused(self, home, name):
        mgr = SkillManager(home, "_default")
        result = mgr.install_skill(_bundle(home, name))
        assert result["success"] is False, result
        assert "rejected" in result["error"] or "escapes" in result["error"]
        # nothing created above the tenant skills dir, nothing registered
        assert not (home / "escaped_v1.0.0").exists()
        assert not (home / "tenants" / "escaped_v1.0.0").exists()
        assert not list(home.glob("**/escaped_v1.0.0"))
        assert mgr.registry.list_skills() == {}

    @pytest.mark.parametrize("version", ["../1", "1/2", "..", "-1"])
    def test_escaping_version_is_refused(self, home, version):
        mgr = SkillManager(home, "_default")
        result = mgr.install_skill(_bundle(home, "ok.skill", version=version))
        assert result["success"] is False, result
        assert mgr.registry.list_skills() == {}

    def test_valid_install_lands_inside_tenant_dir_and_is_audited(self, home):
        mgr = SkillManager(home, "_default")
        result = mgr.install_skill(_bundle(home, "os.router", "2.1.0"))
        assert result["success"] is True, result
        dest = mgr.skills_dir / "os.router_v2.1.0"
        assert dest.is_dir()
        assert mgr.registry.get_skill_path("os.router") == dest.resolve()
        entry = mgr.registry.get_entry("os.router")
        assert entry["triggers"] == ["before_delegation_decision"]
        assert mgr.list_active_skills() == ["os.router"]
        assert any(e["event_type"] == "skill.installed" and e["tool"] == "os.router"
                   for e in _chain_events(home))


class TestExecutorContract:

    def test_timeout_is_enforced(self, home, monkeypatch):
        mgr = SkillManager(home, "_default")
        assert mgr.install_skill(_bundle(home, "slow.skill"))["success"]
        path = mgr.registry.get_skill_path("slow.skill")
        ex = SkillExecutor(path, tenant_id="_default", corvin_home=home)

        release = threading.Event()

        def _hang(inputs):
            release.wait(5)
            return {"decision": "native", "confidence": 1.0, "reasoning": "late"}

        monkeypatch.setattr(ex, "_make_decision", _hang)
        started = time.monotonic()
        result = ex.execute({"task_shape": "x"}, timeout_ms=200)
        elapsed = time.monotonic() - started
        release.set()
        assert result.success is False
        assert result.errors and "timeout" in result.errors[0]
        assert elapsed < 2.0, "execute() must return at the timeout, not when the skill finishes"
        ev = [e for e in _chain_events(home) if e["event_type"] == "skill.executed"
              and e["details"].get("run_id") == result.run_id]
        assert ev and ev[0]["details"]["status"] == "timeout"

    def test_disallowed_fields_never_reach_run_state(self, home):
        mgr = SkillManager(home, "_default")
        assert mgr.install_skill(_bundle(home, "san.skill"))["success"]
        result = mgr.execute_skill(
            "before_delegation_decision",
            {"task_shape": "big_data", "prompt": "TOP SECRET", "api_key": "sk-123", "keep": 1},
        )
        assert result.success, result.errors
        state_file = mgr.skills_dir / "san.skill_v1.0.0" / "runs" / result.run_id / "run_state.json"
        state = json.loads(state_file.read_text())
        assert state["inputs"] == {"task_shape": "big_data", "keep": 1}
        raw = state_file.read_text()
        assert "TOP SECRET" not in raw and "sk-123" not in raw
        for e in _chain_events(home):
            assert "TOP SECRET" not in json.dumps(e)
            assert "sk-123" not in json.dumps(e)

    def test_trigger_lookup_is_not_hardcoded(self, home):
        mgr = SkillManager(home, "_default")
        assert mgr.install_skill(_bundle(home, "other.skill", trigger="on_custom_event"))["success"]
        # no skill declares the delegation trigger → not routed anywhere
        miss = mgr.execute_skill("before_delegation_decision", {})
        assert miss.success is False and "before_delegation_decision" in miss.errors[0]
        hit = mgr.execute_skill("on_custom_event", {"task_shape": "small_code"})
        assert hit.success is True
        state_file = mgr.skills_dir / "other.skill_v1.0.0" / "runs" / hit.run_id / "run_state.json"
        assert json.loads(state_file.read_text())["trigger"] == "on_custom_event"

    def test_disabled_skill_is_not_triggered(self, home):
        mgr = SkillManager(home, "_default")
        assert mgr.install_skill(_bundle(home, "off.skill"))["success"]
        mgr.registry.register_skill("off.skill", "1.0.0",
                                    str(mgr.skills_dir / "off.skill_v1.0.0"), enabled=False,
                                    triggers=["before_delegation_decision"])
        assert mgr.list_active_skills() == []
        assert mgr.execute_skill("before_delegation_decision", {}).success is False

    def test_audit_event_is_metadata_only_and_chained(self, home):
        mgr = SkillManager(home, "_default")
        assert mgr.install_skill(_bundle(home, "aud.skill"))["success"]
        result = mgr.execute_skill("before_delegation_decision", {"task_shape": "big_data"})
        events = [e for e in _chain_events(home) if e["event_type"] == "skill.executed"]
        assert len(events) == 1
        d = events[0]["details"]
        assert set(d) >= {"skill_id", "skill_version", "status", "latency_ms", "run_id", "tenant_id"}
        assert d["run_id"] == result.run_id
        assert "inputs" not in d and "output" not in d and "big_data" not in json.dumps(d)
        import corvin_core._bootstrap  # noqa: F401 — forge on sys.path
        from forge.security_events import verify_chain  # type: ignore[import-not-found]
        ok, problems = verify_chain(home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl")
        assert ok, problems


class TestRegistryLock:

    def test_concurrent_registrations_all_survive(self, home):
        mgr = SkillManager(home, "_default")
        n = 25

        def _reg(i):
            mgr.registry.register_skill(f"s{i}", "1", str(mgr.skills_dir / f"s{i}_v1"))

        threads = [threading.Thread(target=_reg, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(mgr.registry.list_skills()) == n
