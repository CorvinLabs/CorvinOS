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


def test_wheel() -> bool:
    """Test the built wheel in a clean virtual environment."""
    print(f"\n{'=' * 60}")
    print("Testing wheel in isolation")
    print("=" * 60)

    repo_root = Path(__file__).parent.absolute()
    wheels = list((repo_root / "dist").glob("*.whl"))

    if not wheels:
        print("✗ No wheels to test")
        return False

    wheel = wheels[0]

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
