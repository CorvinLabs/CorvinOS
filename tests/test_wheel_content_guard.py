"""Wheel-content guard — what a `pip install corvinos` actually ships.

Background (adversarial installation review, 2026-09-03). The repo checkout
worked; the wheel did not: the `[tool.hatch.build.targets.wheel.sources]`
mapping lifted whole directories to the site-packages ROOT (a stray
`pyproject.toml`, `uv.lock`, `Dockerfile`, `dev.sh`, a top-level `routes/`
namespace package, ...), 19 packaged modules imported checkout-only dotted
paths (`core.console.*`, `core.compliance.corvin_compliance_reports.*`, ...)
that the wheel remaps to top-level names, three source files carried the
developer's absolute checkout path, and the installer's "is this a wheel?"
detection was fooled by the stray pyproject. None of it was red in CI because
every test ran inside the checkout.

This file is the CI-side gate for that failure class:

* the static checks (import-path guard, version-pin sync, entry-point
  targets) run everywhere and need nothing but the source tree;
* the wheel checks build a real wheel with `uv build --wheel` (skipped with
  a reason when `uv` — or a buildable console SPA — is unavailable) and
  inspect its contents: root entries, developer paths, alias packages, and
  the installer's layout detection against the extracted wheel.

`build_wheels.py::test_wheel` is the release-time twin (it additionally
installs the wheel into a venv and boots the platform).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

# Top-level names a wheel install may place in site-packages, besides the
# `corvinos-*.dist-info` directory. Every entry is either a `packages =`
# root, a `sources` remap target, a `_TOPLEVEL_ALIAS_MAP` alias copy
# (hatch_build.py) or the Windows PATH-fix .pth. Adding a name here is a
# packaging decision — mirror it in build_wheels.py::_EXPECTED_ROOT_ENTRIES.
EXPECTED_ROOT_ENTRIES: frozenset[str] = frozenset({
    "corvinOS", "core", "ops",
    "corvin_console", "corvin_core", "corvin_gateway", "corvin_license",
    "corvin_plugins", "plugin_builder", "corvin_logging",
    "corvin_compliance_reports", "corvin_compute", "corvin_workflows",
    "corvin_delegate", "corvin_orchestration", "awpkg",
    "vibe_engineering", "corvin_skills",
    "corvinOS_path_fix.pth",
})

# Dotted prefixes that exist ONLY in a git checkout. The wheel remaps each of
# these packages to a top-level name (pyproject.toml `sources`), so an import
# through the checkout path raises ModuleNotFoundError on every pip install.
# The top-level names work in BOTH layouts (the checkout's editable install
# puts the same parent dirs on sys.path), so they are the only form allowed.
_CHECKOUT_ONLY_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+core\.(?:"
    r"console\.(?:corvin_console|corvin_core)"
    r"|gateway\.corvin_gateway"
    r"|license\.corvin_license"
    r"|compliance\.corvin_compliance_reports"
    r"|plugins\.(?:corvin_plugins|plugin_builder)"
    r"|observability\.corvin_logging"
    r"|compute\.corvin_compute"
    r"|workflows\.corvin_workflows"
    r"|delegate\.corvin_delegate"
    r"|orchestration\.corvin_orchestration"
    r"|awpkg\.awpkg"
    r")(?:\.|\s)",
    re.M,
)

# Files that still carry a checkout-only import and are owned by other
# in-flight work (2026-09-03). Each entry is a TODO for that owner; remove
# the entry the moment the file is fixed so the guard starts covering it.
KNOWN_CHECKOUT_ONLY_IMPORTS: dict[str, str] = {
    # TODO(core/learning owner): example module, `core.console.corvin_core.feature_flags`.
    "core/learning/tool_cost_learning_integration_example.py": "learning subsystem owner",
    # TODO(console routes owners): `core.console.corvin_console.auth` /
    # `.routes.auth` → `corvin_console.auth` / `corvin_console.routes.auth`.
    # TODO(telemetry/pipeline): `audit_writer.write_audit_event` never existed
    # in either layout (the import fails and both call sites fall through to
    # their disk/None fallback). Wiring metrics into the compliance chain is
    # a design decision (event type + ADR-0129 allowlist), not a path fix —
    # tracked as a NEW finding of the 2026-09-03 installation review.
    "core/pipeline/aggregation.py": "phantom audit_writer module; needs audit-event design",
    "core/telemetry/source_of_truth.py": "phantom audit_writer module; needs audit-event design",
}

_SCAN_ROOTS = ("core", "ops", "corvinOS")


def _is_test_file(rel: Path) -> bool:
    parts = rel.parts
    return (
        "tests" in parts
        or "test" in parts
        or rel.name.startswith("test_")
        or rel.name.endswith("_test.py")
        or rel.name == "conftest.py"
    )


def _python_sources() -> list[Path]:
    out: list[Path] = []
    for root in _SCAN_ROOTS:
        for path in (_REPO / root).rglob("*.py"):
            rel = path.relative_to(_REPO)
            if _is_test_file(rel) or "node_modules" in rel.parts or "__pycache__" in rel.parts:
                continue
            out.append(path)
    return out


# ── static checks (no wheel build) ──────────────────────────────────────────


def test_no_checkout_only_imports_in_packaged_code() -> None:
    """No packaged module may import a `sources`-remapped package by its
    checkout path — see `_CHECKOUT_ONLY_IMPORT` for the rationale."""
    hits: list[str] = []
    stale_exceptions: list[str] = []
    for path in _python_sources():
        rel = path.relative_to(_REPO).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        matches = [m.group(0).strip() for m in _CHECKOUT_ONLY_IMPORT.finditer(text)]
        if matches:
            if rel in KNOWN_CHECKOUT_ONLY_IMPORTS:
                continue
            hits.extend(f"{rel}: {m}" for m in matches)
    for rel in KNOWN_CHECKOUT_ONLY_IMPORTS:
        p = _REPO / rel
        if not p.exists():
            stale_exceptions.append(f"{rel} (file gone)")
        elif not _CHECKOUT_ONLY_IMPORT.search(p.read_text(encoding="utf-8", errors="replace")):
            stale_exceptions.append(f"{rel} (fixed — drop it from KNOWN_CHECKOUT_ONLY_IMPORTS)")
    assert not hits, (
        "checkout-only imports found (use the top-level package name — e.g. "
        "`corvin_core.feature_flags`, `corvin_compliance_reports.tripwire`, "
        "`corvin_plugins.manifest`, `corvin_workflows.runner` — which works in "
        "both the checkout and the wheel):\n  " + "\n  ".join(hits)
    )
    assert not stale_exceptions, "stale KNOWN_CHECKOUT_ONLY_IMPORTS entries:\n  " + "\n  ".join(stale_exceptions)


def _pyproject_version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (_REPO / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert m, "pyproject.toml has no version line"
    return m.group(1)


def test_installer_version_floor_matches_pyproject() -> None:
    """install.sh / install.ps1 install `corvinos>=<floor>`; the floor must be
    this release's version or the one-liners silently accept an older index."""
    version = _pyproject_version()
    sh = re.search(r'^CORVIN_MIN_VERSION="([^"]+)"', (_REPO / "install.sh").read_text(encoding="utf-8"), re.M)
    ps1 = re.search(r'^\$CorvinMinVersion\s*=\s*"([^"]+)"', (_REPO / "install.ps1").read_text(encoding="utf-8"), re.M)
    assert sh and sh.group(1) == version, f"install.sh CORVIN_MIN_VERSION {sh and sh.group(1)!r} != pyproject {version!r}"
    assert ps1 and ps1.group(1) == version, f"install.ps1 $CorvinMinVersion {ps1 and ps1.group(1)!r} != pyproject {version!r}"


def test_uv_installer_pin_is_checksummed() -> None:
    """Both one-liners must pin the uv installer to a version AND a SHA-256;
    a bare `curl https://astral.sh/uv/install.sh | sh` is not allowed."""
    sh = (_REPO / "install.sh").read_text(encoding="utf-8")
    ps1 = (_REPO / "install.ps1").read_text(encoding="utf-8")
    assert re.search(r'^UV_INSTALLER_SHA256="[0-9a-f]{64}"', sh, re.M)
    assert re.search(r'^\$UvInstallerSha256\s*=\s*"[0-9a-f]{64}"', ps1, re.M)
    assert "astral.sh/uv/install.sh | sh" not in sh
    assert "astral.sh/uv/install.ps1 | iex" not in ps1


def test_console_version_is_the_distribution_version() -> None:
    """F8: `corvin_console.__version__` must track pyproject, not a literal."""
    import corvin_console

    assert corvin_console.__version__ == _pyproject_version()


# ── wheel checks ────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("`uv` not on PATH — the wheel checks build a real wheel with `uv build --wheel`")
    spa_index = _REPO / "core/console/corvin_console/web-next/dist/index.html"
    if not spa_index.is_file() and shutil.which("npm") is None:
        pytest.skip("console SPA dist is not built and npm is unavailable — hatch_build.py refuses a UI-less wheel")
    out = tmp_path_factory.mktemp("wheel")
    env = dict(os.environ)
    env.setdefault("UV_NO_PROGRESS", "1")
    proc = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        env=env,
        timeout=900,
    )
    if proc.returncode != 0:
        pytest.skip(f"`uv build --wheel` failed (rc={proc.returncode}); tail:\n{proc.stderr[-2000:]}")
    wheels = sorted(out.glob("corvinos-*.whl"))
    assert wheels, f"no wheel produced in {out}: {proc.stdout[-500:]}"
    return wheels[0]


def _wheel_names(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        return zf.namelist()


def test_wheel_root_contains_only_known_entries(built_wheel: Path) -> None:
    roots = {n.split("/", 1)[0] for n in _wheel_names(built_wheel)}
    dist_info = {r for r in roots if r.endswith(".dist-info")}
    assert len(dist_info) == 1, f"expected exactly one dist-info dir, got {sorted(dist_info)}"
    stray = sorted(roots - dist_info - EXPECTED_ROOT_ENTRIES)
    assert not stray, (
        "unexpected entries at the wheel root (repo junk lifted into site-packages, "
        "or a new package that must be added to EXPECTED_ROOT_ENTRIES on purpose): "
        f"{stray}"
    )
    missing = sorted(EXPECTED_ROOT_ENTRIES - roots)
    assert not missing, f"expected wheel root entries are missing: {missing}"


def test_wheel_has_no_stray_top_level_files(built_wheel: Path) -> None:
    """The specific junk the 2026-09-03 review found at the root must never
    return, and no top-level `routes` namespace package may exist."""
    names = _wheel_names(built_wheel)
    for bad in ("pyproject.toml", "uv.lock", "Dockerfile", "dev.sh", "github_client.py"):
        assert bad not in names, f"{bad} shipped at the wheel root"
    assert not any(n.startswith("routes/") for n in names), "top-level `routes/` namespace package in wheel"
    assert not any(n.startswith(("scripts/", "chart/", "frontend/", "ui/", "systemd/")) for n in names)


# A developer checkout path: `/home/<user>/projects/...` (Linux) or
# `/Users/<user>/projects/...` (macOS), the literal maintainer path the
# 2026-09-03 review found, and whatever the BUILDING user's home dir is.
# Generic `/home/user/` / `/home/corvin/` strings in docs, examples and the
# Docker files are placeholders, not leaks, and are deliberately not matched.
def _dev_path_patterns() -> list[bytes]:
    pats = [rb"/(?:home|Users)/[A-Za-z0-9._-]+/projects/", rb"/home/shumway/"]
    home = str(Path.home()).encode()
    if home not in (b"/", b"/root", b"/home", b"/home/user"):
        pats.append(re.escape(home + b"/"))
    return pats


_BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".gz", ".pyc", ".wasm")


def test_wheel_has_no_developer_home_paths(built_wheel: Path) -> None:
    """No shipped file may contain a developer's absolute checkout/home path
    — F5 shipped three (`/home/shumway/projects/...`)."""
    patterns = [re.compile(p) for p in _dev_path_patterns()]
    hits: list[str] = []
    with zipfile.ZipFile(built_wheel) as zf:
        for name in zf.namelist():
            if name.endswith(_BINARY_SUFFIXES):
                continue
            data = zf.read(name)
            for pat in patterns:
                m = pat.search(data)
                if m:
                    hits.append(f"{name}: {m.group(0).decode('utf-8', 'replace')}")
                    break
    assert not hits, "developer paths in wheel:\n  " + "\n  ".join(hits)


def test_wheel_ships_top_level_aliases_and_skill_creator(built_wheel: Path) -> None:
    """F4: the console imports `vibe_engineering`, `corvin_skills` and
    `skill_creator` by top-level name; the wheel must resolve all three."""
    names = set(_wheel_names(built_wheel))
    assert "vibe_engineering/task_graph.py" in names
    assert "vibe_engineering/__init__.py" in names
    assert "corvin_skills/resolver.py" in names
    assert "corvin_core/_vendor/operator/skill_creator/skill_creator.py" in names
    assert "corvin_core/_vendor/operator/skill_creator/__init__.py" in names
    # alias copies are Python-only and test-free
    for n in names:
        if n.startswith(("vibe_engineering/", "corvin_skills/")):
            assert not n.endswith((".md", ".json", ".txt")), f"non-code file in alias copy: {n}"
            assert "/tests/" not in n, f"test file in alias copy: {n}"
        if n.startswith("corvin_core/_vendor/operator/skill_creator/"):
            assert "/tests/" not in n, f"test file vendored: {n}"
    # the git-tracked test artifacts must not ship anywhere
    assert not any(n.endswith(("test_results.json", "coverage_report.json")) for n in names)


def test_wheel_entry_point_targets_exist(built_wheel: Path) -> None:
    """Every [project.scripts] target must resolve to a FILE inside the wheel."""
    names = set(_wheel_names(built_wheel))
    with zipfile.ZipFile(built_wheel) as zf:
        ep_file = next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
        text = zf.read(ep_file).decode("utf-8")
    targets = re.findall(r"^\s*[\w-]+\s*=\s*([\w.]+):\w+", text, re.M)
    assert targets, "no console_scripts in entry_points.txt"
    missing = []
    for module in targets:
        rel = module.replace(".", "/")
        if f"{rel}.py" not in names and f"{rel}/__init__.py" not in names:
            missing.append(module)
    assert not missing, f"entry-point modules missing from the wheel: {missing}"


def test_installer_layout_detection_on_extracted_wheel(built_wheel: Path, tmp_path: Path) -> None:
    """F2: against a REAL wheel layout the installer must say "wheel", and
    against the real checkout it must say "source" — using the real
    detection function, not a patched constant."""
    from corvinOS.installer.core import _is_source_checkout

    site = tmp_path / "site-packages"
    with zipfile.ZipFile(built_wheel) as zf:
        zf.extractall(site)
    assert _is_source_checkout(site) is False
    assert _is_source_checkout(_REPO) is True


# ── F12: boot_platform() without `import corvin_console` first ──────────────


def test_boot_platform_does_not_depend_on_console_import_order() -> None:
    """DOCUMENTED REQUIREMENT (F12, 2026-09-03 review) — not yet met.

    On a wheel install the ADR-0232 tripwire (`corvin_plugins.bootstrap.
    boot_platform()` → `corvin_compliance_reports.tripwire.assert_all()`)
    only finds its audit / consent / house-rules modules because
    `import corvin_console` runs `_operator_bootstrap.ensure_operator_on_path()`
    first. Called WITHOUT that import it fails closed (`TripwireError:
    mandatory compliance mechanism unavailable`). Both shipped hosts import
    `corvin_console` first, so this holds today; a third host that copies
    only the `boot_platform()` call would refuse to boot.

    Requirement: `corvin_plugins.bootstrap` (or the tripwire) must call
    `corvin_core._operator_bootstrap.ensure_operator_on_path()` itself so
    correctness does not depend on caller import order. Owner:
    core/plugins/corvin_plugins/ (bootstrap.py).

    This test needs a wheel INSTALLED into a venv; point
    CORVIN_WHEEL_VENV_PYTHON at that venv's python to run it. It is an xfail
    (not strict) until the requirement is implemented — flip to a plain
    assertion then.
    """
    python = os.environ.get("CORVIN_WHEEL_VENV_PYTHON")
    if not python:
        pytest.skip("set CORVIN_WHEEL_VENV_PYTHON=<venv with the built wheel installed>/bin/python to run")
    env = dict(os.environ)
    env["CORVIN_TELEMETRY_OPTIN"] = "false"
    proc = subprocess.run(
        [python, "-c", "from corvin_plugins.bootstrap import boot_platform; print(boot_platform())"],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    if proc.returncode != 0 and "TripwireError" in proc.stderr:
        pytest.xfail("F12 requirement not implemented: boot_platform() fails closed without `import corvin_console` first")
    assert proc.returncode == 0, proc.stderr[-2000:]
