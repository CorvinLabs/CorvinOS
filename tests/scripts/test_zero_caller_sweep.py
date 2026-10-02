"""scripts/zero_caller_sweep.py — positive and negative controls (ADR-2102).

A sweep that reports nothing proves nothing unless it is shown to (a) reach
modules that are known to be live and (b) flag ones known to be dead. Both are
pinned here against the real tree, plus a synthetic tree for the mechanics.
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import zero_caller_sweep as z  # noqa: E402


@functools.lru_cache(maxsize=1)
def _real():
    """(index, reachable set, roots, sweep report) for the real tree — built
    once per session; each build parses ~2500 files."""
    idx = z.Index(z.production_files())
    live = z.reachable(idx)
    return idx, live, z.roots(idx), z.sweep()


def test_known_live_modules_are_reachable():
    idx, live, _, _ = _real()
    for rel in ("corvin_operator/bridges/shared/session_ledger.py",
                "corvin_operator/context_engineering/stages/l10_adapter.py",
                "core/skills/os_skills/delegation_router.py",
                "corvin_operator/bridges/shared/erasure_handlers.py"):
        assert REPO / rel in live, f"positive control unreachable: {rel}"


def test_known_dead_context_bridges_are_flagged():
    dead = {(d["file"], d["class"]) for d in _real()[3]["dead"]}
    assert ("core/orchestration/subsystems/context_bridge.py", "ContextBridge") in dead
    # phase1's ContextBridge is imported (package __init__ re-export, pulled in
    # by a mounted console route) but never instantiated — reachable by import,
    # so NOT flagged; "imported" is not "used" (review R2-C16).
    assert ("core/skills/os_skills/phase1/context_bridge.py", "ContextBridge") not in dead
    # review R1-C10: the skill_registry_phase1 `Skill` base and unscheduled stages
    assert ("core/skills/workflow_optimizer.py", "WorkflowOptimizerSkill") in dead
    assert any(d["file"].endswith("adr_reranking_stage.py") for d in _real()[3]["dead"])


def test_plugin_contract_is_the_real_lifecycle_shape_on_the_real_tree():
    """Review R5-2: the old "Plugin"/"BasePlugin" contracts matched nothing that
    exists — the four plugin.json plugins hit them only through an import of a
    nonexistent ``corvin_plugins.BasePlugin``. The contract is now the shape the
    loader accepts (``plugin_id`` + ``on_load``); positive control: the
    copy-and-edit templates have exactly that shape and are never imported."""
    assert "Plugin" not in z.CONTRACTS and "BasePlugin" not in z.CONTRACTS
    dead = {(d["file"], d["class"]): d["contract"] for d in _real()[3]["dead"]}
    assert dead[("core/plugins/templates/router_backend_plugin.py", "MyRouterPlugin")] == "CorvinPlugin"
    assert ("core/plugins/buildin/ai/vibe_engineering/src/vibe_routing.py", "VibeRouter") not in dead


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
    idx, live, _, _ = _real()
    assert "corvin_operator/bridges" in idx.active_roots
    assert REPO / "corvin_operator/bridges/bridge_manager.py" in live


_SUB = "from core.base import Subsystem\nclass {}(Subsystem): pass\n"


def test_stdlib_and_third_party_names_never_link_into_the_repo(tmp_path, monkeypatch):
    """Review R5-1: ``import types`` reached ``video_producer/types.py`` through
    a sys.path root only a DEAD file inserted, ``import random`` reached
    ``strategies/random.py`` by unique suffix, ``import queue`` a ``core/queue``
    package through a measured root. A stdlib / declared third-party name links
    into the repo only from a non-package script directory or the repo root,
    and sys.path roots count only when a REACHABLE file inserts them."""
    ins = ("import sys\nfrom pathlib import Path\n"
           "sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '{}'))\n")
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\n",
        "core/app/__init__.py": "",
        "core/app/main.py": ("import types, random, queue, yaml, packaging\n"
                             "import core.pkg.mod\nimport core.live.boot\nimport helper\nimport livehelper\n"),
        # a dead file's insert must not make `vp/` a root for anyone
        "core/dead/boot.py": ins.format("vp"),
        "core/vp/types.py": _SUB.format("StdTypes"),
        "core/vp/helper.py": _SUB.format("DeadRootHelper"),
        "core/other/helper.py": "x = 1\n",            # bare `helper` ambiguous by suffix
        # unique suffix, stdlib name
        "core/compute/strategies/random.py": _SUB.format("StdRandom"),
        # a REACHABLE file inserts `core`: `queue` is still the stdlib, but a
        # plain name found there links (fixpoint over reachable files)
        "core/live/__init__.py": "",
        "core/live/boot.py": ins.format("."),
        "core/queue/__init__.py": _SUB.format("StdQueue"),
        "core/livehelper.py": _SUB.format("LiveHelper"),
        "core/zz/livehelper.py": "x = 1\n",           # bare `livehelper` ambiguous by suffix
        "core/yaml/__init__.py": _SUB.format("ThirdPartyYaml"),
        "core/packaging.py": _SUB.format("ThirdPartyPackaging"),
        # inside a package an absolute `import random` is the stdlib, not a sibling
        "core/pkg/__init__.py": "",
        "core/pkg/mod.py": "import random\n",
        "core/pkg/random.py": _SUB.format("SiblingRandom"),
    })
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["PyYAML >= 6"]\n\n'
        '[project.scripts]\nx = "core.app.main:run"\n')
    _sandbox(monkeypatch, tmp_path)
    assert {c for _, c in _dead()} == {"StdTypes", "DeadRootHelper", "StdRandom", "StdQueue",
                                       "ThirdPartyYaml", "ThirdPartyPackaging", "SiblingRandom"}


def test_stdlib_namesake_from_a_script_directory_still_links(tmp_path, monkeypatch):
    """The one place Python does look first: a script's own (non-package)
    directory is ``sys.path[0]``."""
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\n",
        "core/main.py": "import core.tools_dir.run\n",
        "core/tools_dir/run.py": "import platform\n",          # no __init__.py: a script dir
        "core/tools_dir/platform.py": _SUB.format("ScriptDirPlatform"),
    })
    _sandbox(monkeypatch, tmp_path)
    assert _dead() == set()


def test_plugin_contract_is_the_lifecycle_shape(tmp_path, monkeypatch):
    """Review R5-2: an unwired plugin.json plugin with a plain class carrying
    ``plugin_id`` + ``on_load`` — the shape ``bootstrap._load_builtin_class``
    accepts — was not flagged, because the contract was a ``Plugin`` base class
    that does not exist."""
    shape = "class {}:\n    plugin_id = 'x'\n    def on_load(self, ctx): pass\n"
    _tree(tmp_path, {
        "core/main.py": "import core.pbase\n",
        "core/plugins/buildin/ai/probe_plugin/plugin.json": '{"entry_point": "src/probe.py:ProbePlugin"}',
        "core/plugins/buildin/ai/probe_plugin/src/probe.py": shape.format("ProbePlugin"),
        "core/plugins/buildin/ai/live_plugin/plugin.yaml": "plugin_id: live\n",
        "core/plugins/buildin/ai/live_plugin/provider.py": shape.format("LivePlugin"),
        "core/pbase.py": ("class PBase:\n    def __init__(self): self.plugin_id = 'b'\n"
                          "    async def on_load(self, ctx): pass\n"),
        "core/child.py": "from core.pbase import PBase\nclass ChildPlugin(PBase): pass\n",
        "core/half.py": "class OnlyOnLoad:\n    def on_load(self, ctx): pass\n",
        "core/proto.py": ("from typing import Protocol\nclass ShapeProto(Protocol):\n"
                          "    plugin_id: str\n    def on_load(self, ctx): ...\n"),
    })
    _sandbox(monkeypatch, tmp_path)
    impls = {d["class"]: d["contract"] for d in z.implementations(z.Index(z.production_files()))}
    assert impls == {"ProbePlugin": "CorvinPlugin", "LivePlugin": "CorvinPlugin",
                     "PBase": "CorvinPlugin", "ChildPlugin": "CorvinPlugin"}
    assert _dead() == {("core/plugins/buildin/ai/probe_plugin/src/probe.py", "ProbePlugin"),
                       ("core/child.py", "ChildPlugin")}


def test_missed_roots_are_roots(tmp_path, monkeypatch):
    """Review R5-3: ``-m pkg`` runs pkg/__main__.py; tools/ targets of a
    tools/systemd unit; an ExecStart continued on the next line; shell
    launchers outside corvin_operator/ and ops/; ``-m`` inside a shell script.
    Comments, echo text and file lists in a shell script are not launches."""
    _tree(tmp_path, {
        "core/base.py": "class Subsystem: pass\n",
        "core/main.py": "x = 1\n",
        "core/svc/__init__.py": "",
        "core/svc/__main__.py": "from .runner import R\n",
        "core/svc/runner.py": _SUB.format("R"),
        "ops/svc.service": "[Service]\nExecStart=/usr/bin/python3 -m core.svc --flag\n",
        "tools/loop.py": _SUB.format("ToolLoop"),
        "tools/systemd/loop.service": "[Service]\nExecStart=%h/CorvinOS/.venv/bin/python tools/loop.py -x\n",
        "core/cont/verify.py": _SUB.format("Continued"),
        "ops/systemd/cont.service": ("[Service]\nExecStart=/usr/bin/docker exec c /opt/venv/bin/python \\\n"
                                     "    /opt/repo/core/cont/verify.py --all\n"),
        "core/sh/script.py": _SUB.format("ShellPath"),
        "core/shm/__init__.py": "",
        "core/shm/mod.py": _SUB.format("ShellDashM"),
        "core/shc/commented.py": _SUB.format("Commented"),
        "core/she/echoed.py": _SUB.format("Echoed"),
        "core/shl/listed.py": _SUB.format("Listed"),
        "deploy/run.sh": ('#!/bin/bash\n# core/shc/commented.py is the old entry point\n'
                          'echo "run: python3 core/she/echoed.py"\nFILES=(\n  "core/shl/listed.py"\n)\n'
                          'python3 core/sh/script.py\n"$PY" -u -m core.shm.mod \\\n  --x\n'),
    })
    _sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(z, "SCAN_DIRS", ("core", "tools"))
    assert {c for _, c in _dead()} == {"Commented", "Echoed", "Listed"}


def test_real_tree_missed_roots():
    """Review R5-3 on the real tree."""
    _, live, roots, _ = _real()
    assert REPO / "core/compute/corvin_compute/__main__.py" in roots   # corvin-compute@.service
    assert REPO / "tools/loop_a_pipeline.py" in roots                  # tools/systemd/corvin-loop-a.service
    assert REPO / "corvin_operator/voice/scripts/voice_audit.py" in roots   # continued ExecStart


def test_real_tree_stdlib_namesakes_are_not_linked():
    """Review R5-1 on the real tree: these were reachable only as stdlib
    namesakes (``types``, ``queue``, ``platform``)."""
    idx, live, _, _ = _real()
    for rel in ("core/skills/os_skills/video_producer/types.py", "core/queue/__init__.py",
                "core/platform/__init__.py"):
        assert REPO / rel not in live, rel
    src = REPO / "corvin_operator/bridges/shared/adapter.py"
    assert idx.resolve("types", src) == [] and idx.resolve("queue", src) == []


def test_stdlib_namesake_beside_a_script_launched_from_a_package_dir_links():
    """Review R6-1: ``bridge.sh`` runs ``cd shared && python adapter.py``;
    ``shared/`` is a package, yet ``import profile`` there loads
    ``shared/profile.py`` (sys.path[0] is the script's directory)."""
    idx, _live, _roots, _rep = _real()
    shared = z.REPO / "corvin_operator" / "bridges" / "shared"
    assert shared in idx.script_dirs
    assert idx.resolve("profile", shared / "adapter.py") == [shared / "profile.py"]


def test_only_the_loader_s_plugin_files_are_roots(tmp_path, monkeypatch):
    """Review R7-1: a plugin.yaml outside the builtin root, or a plugin.py
    shadowed by a provider.py, is never loaded — and must not count as live."""
    shape = "class {}:\n    plugin_id = 'x'\n    def on_load(self, ctx): pass\n"
    _tree(tmp_path, {
        "core/main.py": "x = 1\n",
        "core/zzplug/plugin.yaml": "plugin_id: z\n",
        "core/zzplug/plugin.py": shape.format("OutsidePlugin"),
        "core/plugins/buildin/ai/zz/plugin.yaml": "plugin_id: zz\n",
        "core/plugins/buildin/ai/zz/provider.py": shape.format("LoadedPlugin"),
        "core/plugins/buildin/ai/zz/plugin.py": shape.format("ShadowedPlugin"),
    })
    _sandbox(monkeypatch, tmp_path)
    assert {c for _f, c in _dead()} == {"OutsidePlugin", "ShadowedPlugin"}


def test_a_startup_module_name_never_links_to_a_script_sibling():
    """Review R7-3: ``abc``/``os`` are imported before any script runs, so a
    sibling ``abc.py`` beside a path-launched script is never what loads."""
    assert {"abc", "os", "codecs", "io"} <= z._STARTUP_MODULES
    idx, _live, _roots, _rep = _real()
    shared = z.REPO / "corvin_operator" / "bridges" / "shared"
    assert idx.resolve("abc", shared / "adapter.py") == []
