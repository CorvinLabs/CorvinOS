"""scripts/zero_caller_sweep.py — positive and negative controls (ADR-2102).

A sweep that reports nothing proves nothing unless it is shown to (a) reach
modules that are known to be live and (b) flag ones known to be dead. Both are
pinned here against the real tree, plus a synthetic tree for the mechanics.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import zero_caller_sweep as z  # noqa: E402


def test_known_live_modules_are_reachable():
    idx = z.Index(z.production_files())
    live = z.reachable(idx)
    for rel in ("corvin_operator/bridges/shared/session_ledger.py",
                "corvin_operator/context_engineering/stages/l10_adapter.py",
                "core/skills/os_skills/delegation_router.py",
                "corvin_operator/bridges/shared/erasure_handlers.py"):
        assert REPO / rel in live, f"positive control unreachable: {rel}"


def test_known_dead_context_bridges_are_flagged():
    dead = {(d["file"], d["class"]) for d in z.sweep()["dead"]}
    assert ("core/orchestration/subsystems/context_bridge.py", "ContextBridge") in dead
    # phase1's ContextBridge is imported (package __init__ re-export, pulled in
    # by a mounted console route) but never instantiated — reachable by import,
    # so NOT flagged; "imported" is not "used" (review R2-C16).
    assert ("core/skills/os_skills/phase1/context_bridge.py", "ContextBridge") not in dead
    # review R1-C10: the skill_registry_phase1 `Skill` base and unscheduled stages
    assert ("core/skills/workflow_optimizer.py", "WorkflowOptimizerSkill") in dead
    assert any(d["file"].endswith("adr_reranking_stage.py") for d in z.sweep()["dead"])


def test_plugin_json_entry_points_are_not_roots():
    """Review R2-C15: the plugin bootstrap loads plugin.yaml → provider.py /
    plugin.py; a plugin.json entry_point is never loaded, so those plugins
    (which also import a nonexistent ``corvin_plugins.BasePlugin``) are dead."""
    dead = {d["file"] for d in z.sweep()["dead"]}
    assert "core/plugins/buildin/ai/vibe_engineering/src/vibe_routing.py" in dead


def test_the_gate_passes_on_the_committed_baseline():
    assert z.main(["--check"]) == 0


def test_mechanics_on_a_synthetic_tree(tmp_path, monkeypatch):
    (tmp_path / "core" / "pkg").mkdir(parents=True)
    (tmp_path / "core" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "core" / "pkg" / "base.py").write_text("class Subsystem: pass\n")
    (tmp_path / "core" / "pkg" / "wired.py").write_text(
        "from .base import Subsystem\nclass Wired(Subsystem): pass\n")
    (tmp_path / "core" / "pkg" / "by_string.py").write_text(
        "from .base import Subsystem\nclass ByString(Subsystem): pass\n")
    (tmp_path / "core" / "pkg" / "dead.py").write_text(
        "from .base import Subsystem\nclass Dead(Subsystem): pass\n")
    (tmp_path / "core" / "main.py").write_text(
        "from core.pkg.wired import Wired\nREG = {'x': 'core.pkg.by_string:ByString'}\n")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nx = "core.main:run"\n')
    monkeypatch.setattr(z, "REPO", tmp_path)
    monkeypatch.setattr(z, "EXTRA_ROOTS", ())
    monkeypatch.setattr(z, "SCAN_DIRS", ("core",))
    dead = {d["class"] for d in z.sweep()["dead"]}
    assert dead == {"Dead"}


def _tree(tmp_path, files: dict[str, str]):
    for rel, body in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nx = "core.main:run"\n')


def _sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(z, "REPO", tmp_path)
    monkeypatch.setattr(z, "EXTRA_ROOTS", ())
    monkeypatch.setattr(z, "SCAN_DIRS", ("core",))


def test_same_named_module_elsewhere_is_not_kept_alive(tmp_path, monkeypatch):
    """Review R3-6: a live bare `import ledger` made every ledger.py reachable."""
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\n",
        "core/main.py": "import ledger\n",
        "core/ledger.py": "x = 1\n",
        "core/zzprobe/ledger.py": "from core.base import Subsystem\nclass P(Subsystem): pass\n",
    })
    _sandbox(monkeypatch, tmp_path)
    assert {d["class"] for d in z.sweep()["dead"]} == {"P"}


def test_indirect_subclass_is_an_implementation(tmp_path, monkeypatch):
    """Review R3-18: class B(A) with A(Subsystem) implements the contract."""
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\nclass Bridge(Subsystem): pass\n",
        "core/main.py": "from core.base import Subsystem\n",
        "core/zz.py": "from core.base import Bridge\nclass ProbeB(Bridge): pass\n",
    })
    _sandbox(monkeypatch, tmp_path)
    assert "ProbeB" in {d["class"] for d in z.sweep()["dead"]}


def test_check_fails_on_new_dead_and_on_stale_baseline(tmp_path, monkeypatch):
    """Review R3-19/20: --check must exit 1 on a new dead implementation AND on a
    baseline line that no longer applies."""
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\n",
        "core/main.py": "from core.base import Subsystem\n",
        "core/zz.py": "from core.base import Subsystem\nclass Dead(Subsystem): pass\n",
    })
    _sandbox(monkeypatch, tmp_path)
    base = tmp_path / "baseline.json"
    monkeypatch.setattr(z, "BASELINE", base)
    base.write_text("[]")
    assert z.main(["--check"]) == 1                       # new dead
    base.write_text('["core/zz.py::Dead"]')
    assert z.main(["--check"]) == 0                       # baselined
    base.write_text('["core/zz.py::Dead", "core/gone.py::Old"]')
    assert z.main(["--check"]) == 1                       # stale line


def _dead():
    return {(d["file"], d["class"]) for d in z.sweep()["dead"]}


_INSERT_SHARED = ("import sys\nfrom pathlib import Path\n"
                  "sys.path.insert(0, str(Path(__file__).resolve().parent / 'shared'))\n")


def test_bare_import_does_not_reach_a_package_root_namesake(tmp_path, monkeypatch):
    """Review R4-16: ``import audit`` (resolved via the file's own sys.path
    insert) kept a dead ``core/experimental/audit/__init__.py`` alive, because
    the parent of every top-level package counted as a sys.path root."""
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\n",
        "core/main.py": _INSERT_SHARED + "import audit\n",
        "core/shared/audit.py": "from core.base import Subsystem\nclass Live(Subsystem): pass\n",
        "core/experimental/audit/__init__.py":
            "from core.base import Subsystem\nclass DeadAudit(Subsystem): pass\n",
    })
    _sandbox(monkeypatch, tmp_path)
    assert _dead() == {("core/experimental/audit/__init__.py", "DeadAudit")}


def test_bare_import_ambiguous_across_sys_path_roots_links_nowhere(tmp_path, monkeypatch):
    """Review R4-16: when a bare name exists in two real sys.path roots and the
    importer inserts neither, neither is linked (never all of them)."""
    ins = ("import sys\nfrom pathlib import Path\n"
           "sys.path.insert(0, str(Path(__file__).resolve().parent))\n")
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\n",
        "core/main.py": "import util\n",
        "core/one/boot.py": ins, "core/two/boot.py": ins,
        "core/one/util.py": "from core.base import Subsystem\nclass A(Subsystem): pass\n",
        "core/two/util.py": "from core.base import Subsystem\nclass B(Subsystem): pass\n",
    })
    _sandbox(monkeypatch, tmp_path)
    idx = z.Index(z.production_files())
    assert {"core/one", "core/two"} <= set(idx.path_roots)
    assert _dead() == {("core/one/util.py", "A"), ("core/two/util.py", "B")}


def test_file_path_mentions_resolve_as_written_or_not_at_all(tmp_path, monkeypatch):
    """Review R4-17: a shell/unit/string mention of ``audit.py`` linked the first
    three same-named files. Now: as written (script dir, repo root), else the
    most specific UNIQUE path suffix, else nothing."""
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\n",
        "core/main.py": "LOG = 'logs/audit.py'\n",                 # ambiguous string
        "core/a/audit.py": "x = 1\n",
        "core/b/audit.py": "x = 1\n",
        "core/aaa_probe/audit.py":
            "from core.base import Subsystem\nclass ProbeAudit(Subsystem): pass\n",
        "core/tools/runner.py": "from core.base import Subsystem\nclass Run(Subsystem): pass\n",
        "core/tools/svc.py": "from core.base import Subsystem\nclass Svc(Subsystem): pass\n",
        "ops/demo.sh": 'python3 "$ROOT/../tools/runner.py"\n# bridge audit.py writes the log\n',
        "ops/demo.service": "[Service]\nExecStart=/usr/bin/python3 %h/CorvinOS/core/tools/svc.py\n",
    })
    _sandbox(monkeypatch, tmp_path)
    assert _dead() == {("core/aaa_probe/audit.py", "ProbeAudit")}


def test_subclass_closure_is_keyed_by_class_identity_not_name(tmp_path, monkeypatch):
    """Review R4-18: a subclass of a plain ``HealthMonitor`` was reported as a
    Brain Hub subsystem because another ``HealthMonitor`` subclasses Subsystem."""
    _tree(tmp_path, {
        "core/main.py": "x = 1\n",
        "core/hub/__init__.py": "from .base import Subsystem\n",
        "core/hub/base.py": "class Subsystem: pass\n",
        "core/hub/health.py": "from . import Subsystem\nclass HealthMonitor(Subsystem): pass\n",
        "core/obs/health.py": "class HealthMonitor: pass\n",
        "core/zz_plain.py": "from core.obs.health import HealthMonitor\nclass MyMon(HealthMonitor): pass\n",
        "core/zz_real.py": "from core.hub.health import HealthMonitor\nclass RealMon(HealthMonitor): pass\n",
    })
    _sandbox(monkeypatch, tmp_path)
    impls = {d["class"] for d in z.implementations(z.Index(z.production_files()))}
    assert "MyMon" not in impls
    assert {"HealthMonitor", "RealMon"} <= impls


def test_contract_base_under_an_alias_is_seen(tmp_path, monkeypatch):
    """Review R4-19: ``Subsystem as _Base`` / ``mod.Subsystem`` hid the base."""
    _tree(tmp_path, {
        "core/main.py": "x = 1\n",
        "core/base.py": "class Subsystem: pass\n",
        "core/alias.py": "from core.base import Subsystem as _Base\nclass AliasDead(_Base): pass\n",
        "core/attr.py": "import core.base as cb\nclass AttrDead(cb.Subsystem): pass\n",
    })
    _sandbox(monkeypatch, tmp_path)
    assert _dead() == {("core/alias.py", "AliasDead"), ("core/attr.py", "AttrDead")}


def test_path_roots_are_measured_from_sys_path_calls(tmp_path, monkeypatch):
    """Review R4-20: PATH_ROOTS was a hand list that missed the console's
    ``for cand in (_REPO / …,): sys.path.insert(0, str(cand))`` and
    ``parents[N] / "license"`` inserts, so ``bridge_manager`` read as dead."""
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\n",
        "core/main.py": "import core.console.routes\n",
        "core/console/__init__.py": "",
        "core/console/routes.py": (
            "import os, sys\nfrom pathlib import Path as _P\n"
            "_THIS_DIR = _P(__file__).resolve().parent\n_REPO = _THIS_DIR.parents[1]\n"
            "def _lic():\n    return _P(__file__).resolve().parents[1] / 'license'\n"
            "for cand in (_REPO / 'core' / 'bridges', _THIS_DIR / 'nope'):\n"
            "    sys.path.insert(0, str(cand))\n"
            "sys.path.insert(0, str(_lic()))\n"
            "sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'gw'))\n"
            "import bridge_manager\nfrom validator import check\nimport gwmod\n"),
        "core/bridges/bridge_manager.py": "from core.base import Subsystem\nclass BM(Subsystem): pass\n",
        "core/old/bridge_manager.py": "x = 1\n",       # makes the bare name ambiguous by suffix
        "core/license/validator.py": "from core.base import Subsystem\nclass V(Subsystem): pass\n",
        "core/gw/gwmod.py": "from core.base import Subsystem\nclass G(Subsystem): pass\n",
    })
    _sandbox(monkeypatch, tmp_path)
    idx = z.Index(z.production_files())
    assert {"core/bridges", "core/license", "core/gw"} <= set(idx.path_roots)
    assert "core/console/nope" not in idx.path_roots       # not a directory
    assert _dead() == set()


def test_docstring_does_not_overclaim_dead_means_no_path():
    """Review R4-21: "dead" means no STATICALLY resolvable path, not proof."""
    doc = z.__doc__
    assert "a module reported dead has no import path" not in doc
    assert "not a proof" in doc


def test_real_tree_bridge_manager_is_reachable():
    """Review R4-20 on the real tree: the console routes put
    corvin_operator/bridges on sys.path before ``import bridge_manager``."""
    idx = z.Index(z.production_files())
    assert "corvin_operator/bridges" in idx.path_roots
    assert REPO / "corvin_operator/bridges/bridge_manager.py" in z.reachable(idx)
