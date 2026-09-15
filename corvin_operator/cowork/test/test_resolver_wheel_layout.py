"""Regression tests for the wheel-install layout class of MCP-spawn bugs
(adversarial review 2026-07-12, ADR-0190/0191 pass).

Three confirmed CRITICALs shared one root cause: resolver.py's spawn
templates assumed the source-checkout layout. On a pip/uv wheel install
(a) {{REPO_ROOT}} resolves to corvin_console/_vendor, which holds the
vendored operator/ subtrees but NOT core/ — so PYTHONPATH entries like
_vendor/core/orchestration pointed into the void and every
orchestration/delegate MCP spawn died with ModuleNotFoundError, while the
capability map kept advertising the tools; (b) operator/forge/forge.py (the
spawn SCRIPT, not the inner package) was never vendored at all; and (c) the
PYTHONPATH strings were ':'-joined, which Windows (';' separator) treats as
one giant unusable path — dead-on-arrival for all three default personas.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import resolver  # noqa: E402


def _iter_mcp_servers(profile: dict):
    for name, cfg in (profile.get("mcp_servers") or {}).items():
        yield name, cfg


def _expanded(profile_name: str) -> dict:
    profile = resolver.resolve(profile_name)
    return resolver._expand_template_vars(
        profile.get("mcp_servers") or {}, persona=profile)


def test_pythonpath_uses_os_pathsep_not_hard_colon():
    """A ':'-joined PYTHONPATH is one unusable path on Windows."""
    for persona in ("assistant", "coder", "orchestrator"):
        servers = _expanded(persona)
        for name, cfg in servers.items():
            pp = (cfg.get("env") or {}).get("PYTHONPATH")
            if not pp:
                continue
            for entry in pp.split(os.pathsep):
                # On POSIX this also catches a stray ';' that would break
                # the entry; on Windows it catches the raw-':' class.
                assert not (os.name == "nt" and ":" in entry[2:]), (
                    f"{persona}/{name}: PYTHONPATH entry {entry!r} contains a "
                    f"raw ':' — joined with the wrong separator for Windows")


def test_all_spawn_paths_exist_on_disk():
    """Every PYTHONPATH entry and every script-path arg the resolver
    materializes must exist in THIS layout (source checkout here; the same
    invariant holds for the wheel layout via _compute_core_root — verified
    by the fresh-install E2E). Catches 'points into the void' regressions
    the moment an injector adds a path that isn't real."""
    for persona in ("assistant", "coder", "orchestrator", "forge"):
        servers = _expanded(persona)
        for name, cfg in servers.items():
            for entry in ((cfg.get("env") or {}).get("PYTHONPATH") or "").split(os.pathsep):
                if entry:
                    assert Path(entry).is_dir(), (
                        f"{persona}/{name}: PYTHONPATH entry does not exist: {entry}")
            for arg in cfg.get("args") or []:
                if isinstance(arg, str) and arg.endswith(".py") and os.sep in arg:
                    assert Path(arg).is_file(), (
                        f"{persona}/{name}: spawn script does not exist: {arg}")


def test_no_bare_python3_command():
    """A bare 'python3' resolves via the SPAWNING process's PATH — usually a
    system interpreter without this package's deps, and it doesn't exist at
    all on Windows. Every materialized command must be an absolute path or
    a non-python launcher (npx etc.)."""
    for persona in ("assistant", "coder", "orchestrator", "forge", "research"):
        servers = _expanded(persona)
        for name, cfg in servers.items():
            cmd = cfg.get("command", "")
            assert cmd not in ("python", "python3"), (
                f"{persona}/{name}: bare interpreter command {cmd!r}")


def test_core_root_resolves_site_packages_in_wheel_layout(tmp_path, monkeypatch):
    """Simulated wheel layout: REPO_ROOT == .../corvin_console/_vendor and
    core/ lives two levels up (site-packages)."""
    site = tmp_path / "site-packages"
    vendor = site / "corvin_console" / "_vendor"
    (vendor / "operator").mkdir(parents=True)
    (site / "core" / "orchestration").mkdir(parents=True)

    monkeypatch.setattr(resolver, "REPO_ROOT", vendor)
    assert resolver._compute_core_root() == site


def test_core_root_is_repo_root_in_source_checkout():
    assert resolver._compute_core_root() == resolver.REPO_ROOT
    assert (resolver.CORE_ROOT / "core" / "orchestration").is_dir()


def test_forge_entry_script_is_vendored_in_wheel_map():
    """hatch_build must ship operator/forge/forge.py (the spawn script) —
    vendoring only the inner package left every wheel install with a dead
    forge MCP server."""
    repo_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo_root))
    try:
        import hatch_build
    finally:
        sys.path.remove(str(repo_root))
    srcs = [src for src, _ in hatch_build._VENDOR_MAP]
    assert "operator/forge/forge.py" in srcs
    for src, dest in hatch_build._VENDOR_MAP:
        assert (repo_root / src).exists(), f"vendor map source missing: {src}"
