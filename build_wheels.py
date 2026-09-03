#!/usr/bin/env python3
"""
Build wheels for CorvinOS installer across all platforms.

This script creates platform-specific wheels that can be distributed via PyPI.
Run this on each platform (Linux, macOS, Windows) to build native wheels.

Usage:
    python build_wheels.py [--upload]

Environment variables:
    TWINE_USERNAME  — PyPI username
    TWINE_PASSWORD  — PyPI password (use token instead in production)
"""

import os
import shutil
import subprocess
import sys
import platform
from pathlib import Path

# web-next SPA source dir. The wheel bundles its compiled `dist/`; without a
# fresh `npm run build` the wheel ships an empty/stale SPA dist and the
# console serves only the "frontend not built" fallback.
_WEB_NEXT_DIR = Path(__file__).parent.resolve() / "core" / "console" / "corvin_console" / "web-next"


def get_platform_name() -> str:
    """Get human-readable platform name."""
    system = platform.system()
    if system == "Darwin":
        return "macOS"
    elif system == "Windows":
        return "Windows"
    else:
        return "Linux"


def run_command(cmd: list[str], description: str = "", cwd: "Path | None" = None) -> bool:
    """Run command and return success status."""
    if description:
        print(f"\n{'=' * 60}")
        print(f"▶ {description}")
        print("=" * 60)

    try:
        result = subprocess.run(cmd, check=True, cwd=str(cwd) if cwd else None)
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"✗ Command failed: {' '.join(cmd)}")
        print(f"  Error: {e}")
        return False


def build_spa() -> bool:
    """Compile the web-next console SPA into ``dist/`` before the wheel build.

    The wheel packages the pre-built SPA ``dist/`` (ADR-0037). Skipping this
    step produces a wheel whose console serves only the "frontend not built"
    fallback. Guarded: when npm/node is absent we emit a clear warning and
    continue (so a backend-only wheel can still be built on a CI box without
    Node), rather than failing the whole build.
    """
    print(f"\n{'=' * 60}")
    print("▶ Building console SPA (npm run build)")
    print("=" * 60)

    if not _WEB_NEXT_DIR.exists():
        print(f"⚠ web-next dir not found at {_WEB_NEXT_DIR} — skipping SPA build.")
        print("  Wheel will ship without a compiled console SPA.")
        return True

    npm = shutil.which("npm")
    if npm is None:
        print("⚠ npm/node not found on PATH — skipping SPA build.")
        print("  The resulting wheel will have an EMPTY console SPA dist.")
        print(f"  Install Node.js and re-run, or build manually:")
        print(f"    cd {_WEB_NEXT_DIR} && npm install && npm run build")
        return True

    # Install deps (idempotent) then build.
    if not run_command([npm, "install"], "npm install (web-next)", cwd=_WEB_NEXT_DIR):
        return False
    if not run_command([npm, "run", "build"], "npm run build (web-next)", cwd=_WEB_NEXT_DIR):
        return False

    dist = _WEB_NEXT_DIR / "dist"
    if not (dist / "index.html").exists():
        print(f"✗ SPA build did not produce {dist / 'index.html'}")
        return False
    print(f"✓ SPA built: {dist}")
    return True


def build_wheels() -> bool:
    """Build wheels for the current platform."""
    repo_root = Path(__file__).parent.absolute()
    os.chdir(repo_root)

    print(f"\n{'=' * 60}")
    print(f"CorvinOS Wheel Builder")
    print(f"{'=' * 60}")
    print(f"Platform: {get_platform_name()}")
    print(f"Python: {sys.version}")
    print(f"Repository: {repo_root}")

    # 1. Install build dependencies
    if not run_command(
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "build", "hatchling"],
        "Installing build dependencies"
    ):
        return False

    # 2. Clean previous builds
    print("\n▶ Cleaning previous builds...")
    for item in (repo_root / "dist").glob("*"):
        item.unlink()
    for item in (repo_root / "build").glob("*"):
        if item.is_file():
            item.unlink()
        else:
            import shutil
            shutil.rmtree(item)
    print("✓ Cleaned")

    # 3. Build wheel
    if not run_command(
        [sys.executable, "-m", "build", "--wheel"],
        "Building wheel"
    ):
        return False

    # 4. Verify wheel
    wheels = list((repo_root / "dist").glob("*.whl"))
    if not wheels:
        print("✗ No wheels found after build")
        return False

    for wheel in wheels:
        print(f"✓ Built: {wheel.name}")

    return True


# Names allowed at the site-packages ROOT of an installed wheel, besides the
# dist-info dir. Keep in sync with tests/test_wheel_content_guard.py
# (EXPECTED_ROOT_ENTRIES) — that test is the CI-side twin of this check.
_EXPECTED_ROOT_ENTRIES = frozenset({
    "corvinOS", "core", "ops",
    "corvin_console", "corvin_core", "corvin_gateway", "corvin_license",
    "corvin_plugins", "plugin_builder", "corvin_logging",
    "corvin_compliance_reports", "corvin_compute", "corvin_workflows",
    "corvin_delegate", "corvin_orchestration", "awpkg",
    "vibe_engineering", "corvin_skills",
    "corvinOS_path_fix.pth",
})

# Snippet run INSIDE the throwaway venv: import every console_scripts target,
# import the console host, boot the platform (ADR-0232 tripwire) under a temp
# CORVIN_HOME, and check the site-packages root. Exit code != 0 on any failure.
_WHEEL_SMOKE = r"""
import importlib, importlib.metadata as md, os, sys, sysconfig, tempfile
from pathlib import Path

expected = set(sys.argv[1].split(","))
failures = []

# 1. site-packages root contains only known names
site = Path(sysconfig.get_paths()["purelib"])
stray = sorted(
    p.name for p in site.iterdir()
    if p.name not in expected
    and not p.name.endswith(".dist-info")
    and not p.name.startswith("_")            # _virtualenv.pth, __pycache__
    and p.name not in {"pip", "setuptools", "pkg_resources", "wheel", "distutils-precedence.pth"}
    and not p.name.endswith(".pth")           # venv tooling .pth files
    and "corvinos" not in p.name.lower()      # our own metadata dirs
)
# Only OUR stray files matter: filter out third-party dependency packages by
# keeping names that came from the corvinos RECORD.
record = md.distribution("corvinos").files or []
ours = {str(f).split("/")[0] for f in record}
stray = [n for n in stray if n in ours]
if stray:
    failures.append(f"unexpected site-packages root entries from the wheel: {stray}")

# 2. every [project.scripts] target imports (module AND attribute)
for ep in md.distribution("corvinos").entry_points:
    if ep.group != "console_scripts":
        continue
    try:
        getattr(importlib.import_module(ep.module), ep.attr)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"entry point {ep.name} ({ep.value}) failed to import: {exc!r}")

# 3. console host + tripwire boot under an isolated CORVIN_HOME
with tempfile.TemporaryDirectory(prefix="corvin_wheel_smoke_") as home:
    os.environ["HOME"] = home
    os.environ["CORVIN_HOME"] = str(Path(home) / ".corvin")
    os.environ["CORVIN_TELEMETRY_OPTIN"] = "false"
    try:
        import corvin_console.standalone  # noqa: F401
        from corvin_plugins.bootstrap import boot_platform
        boot_platform()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"corvin_console.standalone / boot_platform() failed: {exc!r}")

if failures:
    print("\n".join("✗ " + f for f in failures))
    sys.exit(1)
print("✓ wheel smoke: root clean, entry points import, boot_platform() ran")
"""


def _wheel_contains_home_paths(wheel: Path) -> list[str]:
    """Return wheel members whose content mentions a `/home/<user>` path."""
    import zipfile

    hits: list[str] = []
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            if name.endswith((".png", ".woff", ".woff2", ".ttf", ".ico", ".jpg", ".gz", ".pyc")):
                continue
            if b"/home/" in zf.read(name):
                hits.append(name)
    return hits


def test_wheel() -> bool:
    """Test the built wheel in a clean virtual environment.

    Beyond "does one import work", this gate checks what the 2026-09-03
    adversarial installation review found the old one-import test could
    never catch: repo junk at the site-packages root (F1), entry points that
    only resolve in a checkout (F3), the ADR-0232 boot tripwire being
    reachable from the wheel (F12), and developer paths baked into shipped
    files (F5).
    """
    print(f"\n{'=' * 60}")
    print("Testing wheel in isolation")
    print("=" * 60)

    repo_root = Path(__file__).parent.absolute()
    wheels = list((repo_root / "dist").glob("*.whl"))

    if not wheels:
        print("✗ No wheels to test")
        return False

    wheel = wheels[0]

    home_hits = _wheel_contains_home_paths(wheel)
    if home_hits:
        print("✗ wheel members contain a /home/ path (developer path leaked):")
        for h in home_hits:
            print(f"    {h}")
        return False
    print("✓ no /home/ paths inside the wheel")

    # Create temp venv
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        venv_dir = tmpdir_path / "test_venv"

        # Create venv
        if not run_command(
            [sys.executable, "-m", "venv", str(venv_dir)],
            f"Creating test venv"
        ):
            return False

        # Get python executable in venv
        if sys.platform == "win32":
            python_exe = venv_dir / "Scripts" / "python.exe"
        else:
            python_exe = venv_dir / "bin" / "python"

        # Install wheel
        if not run_command(
            [str(python_exe), "-m", "pip", "install", str(wheel)],
            "Installing wheel in test venv"
        ):
            return False

        # Test import
        if not run_command(
            [str(python_exe), "-c", "from corvinOS.installer.core import CorvinInstaller; print('✓ Import successful')"],
            "Testing import"
        ):
            return False

        # Root cleanliness + every console_scripts target + console host +
        # boot_platform() (tripwire) under a temp CORVIN_HOME.
        if not run_command(
            [str(python_exe), "-c", _WHEEL_SMOKE, ",".join(sorted(_EXPECTED_ROOT_ENTRIES))],
            "Wheel smoke: root entries, entry points, boot_platform()"
        ):
            return False

    print("✓ Wheel test passed")
    return True


def upload_to_pypi(upload: bool = False) -> bool:
    """Upload wheels to PyPI."""
    if not upload:
        print("\n💡 To upload to PyPI:")
        print(f"  1. Install: pip install twine")
        print(f"  2. Upload: twine upload dist/*.whl")
        print(f"  3. Or run with --upload flag")
        return True

    print(f"\n{'=' * 60}")
    print("Uploading to PyPI")
    print("=" * 60)

    if not run_command(
        [sys.executable, "-m", "pip", "install", "twine"],
        "Installing twine"
    ):
        return False

    if not run_command(
        [sys.executable, "-m", "twine", "upload", "dist/*.whl"],
        "Uploading wheels to PyPI"
    ):
        return False

    print("✓ Upload complete")
    return True


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Build and test CorvinOS wheels"
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload wheels to PyPI after building"
    )
    parser.add_argument(
        "--test-only",
        action="store_true",
        help="Only test existing wheels, don't build"
    )

    args = parser.parse_args()

    try:
        if not args.test_only:
            if not build_spa():
                sys.exit(1)
            if not build_wheels():
                sys.exit(1)

        if not test_wheel():
            sys.exit(1)

        if not upload_to_pypi(args.upload):
            sys.exit(1)

        print(f"\n{'=' * 60}")
        print("✓ All done!")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n✗ Interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
