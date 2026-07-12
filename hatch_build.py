"""Custom Hatch build hook — vendor pruned operator subtrees into the wheel.

Why a build hook instead of plain ``force-include``?
----------------------------------------------------
The console's 56 modules reach runtime deps in ``operator/`` via repo-relative
``sys.path`` injection. In a wheel install that path resolves into site-packages
where no ``operator/`` exists → ``ModuleNotFoundError: No module named 'forge'``.

We fix that by vendoring the needed operator subtrees into
``corvin_console/_vendor/operator/<same-relative-layout>`` so the
``_operator_bootstrap.py`` module can prepend them onto ``sys.path``.

A plain ``[tool.hatch.build.targets.wheel.force-include]`` entry would ship the
whole subtree INCLUDING test suites, and Hatch's ``exclude`` patterns do NOT
apply to force-included files. This hook instead stages a *pruned* copy (tests,
``__pycache__`` and ``*.pyc`` stripped) into a temp dir and force-includes that,
so the wheel carries only runtime code.

Source-tree mode is untouched: the hook only runs at wheel-build time and writes
into a temp staging dir; the live checkout never gains a ``_vendor/`` dir, so
``_operator_bootstrap.ensure_operator_on_path()`` stays a no-op there.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# (source subtree relative to repo root, destination relative to wheel root).
# Mirror layout EXACTLY so the bootstrap's relative paths resolve. ``forge`` is
# the inner package at operator/forge/forge so ``from forge import paths`` works
# once ``_vendor/operator/forge`` is on sys.path.
#
# operator/bridges is included so that bridge_manager.py and the per-channel
# daemon.js entry points are available from a pure pip install (ADR-0130).
# node_modules/, auth/, systemd/, and settings.json are excluded — see
# _BRIDGE_RUNTIME_SKIP and _BRIDGE_WHEEL_SKIP below.
_VENDOR_MAP: tuple[tuple[str, str], ...] = (
    ("operator/forge/forge", "corvin_console/_vendor/operator/forge/forge"),
    # The forge CLI/MCP entry SCRIPT (not the inner package): resolver.py
    # spawns the forge MCP server as `<python> {{REPO_ROOT}}/operator/forge/
    # forge.py mcp ...`. Vendoring only the inner package left every wheel
    # install with a dead forge MCP server ("can't open file ..._vendor/
    # operator/forge/forge.py") — which also killed ADR-0190 M2/M3
    # (compute_submit/compute_gate/datasource_connect) on fresh installs.
    ("operator/forge/forge.py", "corvin_console/_vendor/operator/forge/forge.py"),
    ("operator/bridges", "corvin_console/_vendor/operator/bridges"),
    ("operator/license", "corvin_console/_vendor/operator/license"),
    ("operator/agent", "corvin_console/_vendor/operator/agent"),
    ("operator/voice/scripts", "corvin_console/_vendor/operator/voice/scripts"),
    # ADR-0141 / L10: the path-gate hook file. The mandatory CAP_PATH_GATE
    # capability is registered by FILE PRESENCE at
    # _repo_root()/operator/voice/hooks/path_gate.py (security_capabilities.
    # _register_path_gate_by_presence). Without vendoring voice/hooks the file is
    # absent in a wheel install → CAP_PATH_GATE unregistered → "mandatory security
    # layer missing" fail-closed block of every request on a fresh install.
    ("operator/voice/hooks", "corvin_console/_vendor/operator/voice/hooks"),
    ("operator/mcp_manager", "corvin_console/_vendor/operator/mcp_manager"),
    ("operator/skill-forge", "corvin_console/_vendor/operator/skill-forge"),
    ("operator/cowork", "corvin_console/_vendor/operator/cowork"),
    # ADR-0141: the RS256-signed layer-integrity manifest. Without this the
    # vendored layer_integrity.py resolves MANIFEST_REL_PATH to a missing file,
    # leaving every wheel install permanently in the pre-rollout (T1-disabled)
    # state. layer_integrity._repo_root() = parents[3] = _vendor, so the
    # manifest must land at _vendor/operator/security/layer-manifest.json.
    ("operator/security", "corvin_console/_vendor/operator/security"),
    # ADR-0143: the SHA-anchored L44 acceptable-use policy. Without this the
    # vendored house_rules.py resolves repo_policy_path() to a missing file and
    # the gate fail-closes — blocking EVERY chat/workflow/assistant OS-turn on a
    # fresh pip install. house_rules._repo_root() = parents[3] = _vendor, so the
    # policy must land at _vendor/operator/policy/house_rules.yaml.
    ("operator/policy", "corvin_console/_vendor/operator/policy"),
    # Config-template resources resolved at runtime by vendored modules via
    # parents[3] (= _vendor on a wheel install): engine_models.py reads
    # engine_model_registry.yaml (Engine Control Center + per-persona model
    # dropdown), and the EU_PRODUCTION presets are referenced by the egress/
    # compliance paths. Without this the model dropdowns come up empty on a
    # fresh pip install.
    ("operator/bundle/config-templates", "corvin_console/_vendor/operator/bundle/config-templates"),
)


# Dev-only files that must never ship in a wheel regardless of their location.
# sob_issuer.py is explicitly a test/dev SOB signer — any wheel recipient
# could call SobIssuer().register_local() to self-sign a member-tier SOB.
_DEV_ONLY_FILES: frozenset[str] = frozenset({
    "sob_issuer.py",  # dev-only license forge — see ADR-0111
})

# Runtime directory names that must never ship in the wheel, regardless of depth.
#
# These grow during normal operation and may contain user data, credentials, or
# large session archives. Excluding them keeps the wheel to source files only.
#
#   node_modules  — npm deps, installed at runtime by bridge_manager.py (can be
#                   hundreds of MB for discord.js / Baileys)
#   auth          — WhatsApp Baileys session JSON (real credentials)
#   systemd       — Linux service unit templates (irrelevant from pip install)
#   processed     — consumed-message archive (grows to millions of files)
#   outbox        — pending-send queue (runtime state)
#   inbox         — received-message queue (runtime state)
#   agents        — per-agent session state directories
#   teb           — Tool Execution Broker runtime state
#   eci           — Engine Command Interface runtime state
#   attachments   — email bridge downloaded attachments (user data)
#   console       — bridge-local console session outbox (not the web console)
# NOTE: matched by bare component name at ANY depth (see _is_test_path), so an
# entry here must NOT collide with a SOURCE package name that has to ship.
# `agents` (shared/agents — the WorkerEngine implementations), `teb`
# (shared/teb — Tool Execution Broker / L10 path-gate broker for non-CC
# engines, ADR-0069) and `eci` (shared/eci — Engine Command Interface, imported
# as `from eci.dispatcher import ...` in adapter.py) are SOURCE packages, not
# runtime state — excluding them shipped a wheel that could load NO engine
# (regression fixed in 0.1.1). The runtime per-agent/teb/eci STATE lives under
# CORVIN_HOME, never inside the packaged source tree, so it is not at risk here.
_BRIDGE_RUNTIME_SKIP: frozenset[str] = frozenset({
    "node_modules",
    "auth",
    "systemd",
    "processed",
    "outbox",
    "inbox",
    "attachments",
    "console",
})

# Individual filenames we never want in the wheel even if not test files.
# settings.json may contain real bot tokens in a developer checkout; only
# the .example file ships as a template. Likewise, auth-backup dirs that
# use the pattern auth.bak.TIMESTAMP must be excluded.
_BRIDGE_WHEEL_SKIP_NAMES: frozenset[str] = frozenset({
    "settings.json",   # user credentials — only settings.json.example ships
    ".claude",         # Claude Code project files — not needed at runtime
})


def _is_runtime_path(name: str) -> bool:
    """True for filenames/dirnames that are runtime state or credentials."""
    # WhatsApp auth backup dirs: auth.bak.YYYYMMDD-HHMMSS
    if name.startswith("auth.bak"):
        return True
    return False


def _is_test_path(rel: Path) -> bool:
    """True for test files / test dirs / dev-only files we never want in the wheel."""
    parts = rel.parts
    # .pytest_cache/.ldd are CI/dev-only state dirs that exist in real
    # checkouts today (operator/bridges/.pytest_cache, .ldd/heartbeat) but
    # used a different literal string than "tests"/"test"/"__pycache__", so
    # they were never matched here and could ship inside a force-included
    # vendored subtree's wheel copy (adversarial review finding).
    if any(p in ("tests", "test", "__pycache__", ".pytest_cache", ".ldd") for p in parts):
        return True
    if any(p in _BRIDGE_RUNTIME_SKIP for p in parts):
        return True
    if any(_is_runtime_path(p) for p in parts):
        return True
    name = rel.name
    if name in _DEV_ONLY_FILES or name in _BRIDGE_WHEEL_SKIP_NAMES:
        return True
    # self_test.py is PRODUCTION code (the L16/ADR-0141 boot self-test + a
    # mandatory Tier-3 security capability), NOT a test file — but it ends in
    # "_test.py" so the Go-style suffix rule below wrongly pruned it from the
    # wheel, leaving CAP_SELF_TEST unregistered → "mandatory security layer
    # missing" fail-closed block of every request on a fresh install.
    if name != "self_test.py" and (name.startswith("test_") or name.endswith("_test.py")):
        return True
    if name.startswith("test_") and name.endswith((".js", ".sh")):
        return True
    if name in ("conftest.py",) or name.endswith(("_fixture.py", ".snap")):
        return True
    if name.endswith(".pyc"):
        return True
    return False


class VendorOperatorHook(BuildHookInterface):
    PLUGIN_NAME = "vendor-operator"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: D401
        root = Path(self.root)

        dist_dir = root / "core/console/corvin_console/web-next/dist"
        spa_index = dist_dir / "index.html"

        # Hatchling's editable install also uses WheelBuilder (target_name=="wheel")
        # but passes version=="editable". Treat editable the same as sdist/dev so
        # `pip install -e` never hits the hard-fail SPA check.
        is_real_wheel = self.target_name == "wheel" and version != "editable"

        if is_real_wheel:
            # The wheel MUST ship a REAL, built SPA. A prior version created an
            # EMPTY placeholder dist/ here "to satisfy force-include validation",
            # which is exactly how 0.9.0 shipped a UI-less wheel: `corvin-serve`
            # then served a 404 console and the user could never reach setup
            # ("Einrichtung geht nicht"). A pure `pip install corvinos` has no npm
            # to populate it later, so the SPA must be present at packaging time.
            # Build it if missing; HARD-FAIL the wheel build if it cannot be
            # produced — never ship a UI-less wheel again.
            if not spa_index.is_file():
                self._build_spa(root / "core/console/corvin_console/web-next")
            if not spa_index.is_file():
                raise RuntimeError(
                    "console SPA dist is missing and could not be built — "
                    "refusing to build a UI-less wheel. Install Node.js and run "
                    "`npm ci && npm run build` in "
                    "core/console/corvin_console/web-next/ before packaging."
                )
        else:
            # Editable / sdist dev builds: a placeholder keeps `pip install -e`
            # working before the frontend is built (the dev runs vite separately).
            dist_dir.mkdir(parents=True, exist_ok=True)

        # Vendor-staging is only meaningful for real wheel builds.
        if not is_real_wheel:
            return

        self._stage = Path(tempfile.mkdtemp(prefix="corvin_vendor_"))

        force_include = build_data.setdefault("force_include", {})
        for src_rel, dest_rel in _VENDOR_MAP:
            src = root / src_rel
            staged = self._stage / dest_rel
            if src.is_file():
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, staged)
            elif src.is_dir():
                shutil.copytree(
                    src,
                    staged,
                    ignore=self._ignore,
                    dirs_exist_ok=True,
                )
            else:
                continue
            force_include[str(staged)] = dest_rel

    @staticmethod
    def _build_spa(web_next: Path) -> None:
        """Build the web-next SPA so the wheel ships a real UI.

        Best-effort: if npm is unavailable or the build fails, leaves dist/
        unbuilt and lets ``initialize`` hard-fail with actionable guidance. Runs
        only at wheel-build time (release machine), never on the end user's
        `pip install` of the published wheel.
        """
        import subprocess

        if not web_next.is_dir():
            return
        npm = shutil.which("npm")
        if npm is None:
            return  # caller raises with install-Node guidance
        try:
            if not (web_next / "node_modules").is_dir():
                subprocess.run([npm, "ci"], cwd=str(web_next), check=True)
            subprocess.run([npm, "run", "build"], cwd=str(web_next), check=True)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"console SPA build failed: {exc}") from exc

    @staticmethod
    def _ignore(directory: str, names: list[str]) -> set[str]:
        skip: set[str] = set()
        base = Path(directory)
        for n in names:
            if _is_test_path(Path(n)) or _is_test_path(base / n) or _is_runtime_path(n):
                skip.add(n)
        return skip

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        stage = getattr(self, "_stage", None)
        if stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
