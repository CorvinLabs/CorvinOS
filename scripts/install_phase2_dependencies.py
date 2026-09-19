#!/usr/bin/env python3
"""
Phase 2 Dependency Installer

Resolves the Phase 2 blocker by installing required dependencies without pip.
Uses setuptools.setup() + subprocess (setup.py) as fallback for pip-free environments.

Status: EMERGENCY FIX for pip-unavailable systems (e.g., containerized, restricted envs).
Recommended: Use `pip install -e .` instead if pip is available.
"""

import subprocess
import sys
import os
from pathlib import Path

def check_module(module_name: str) -> bool:
    """Check if a module is importable."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False

def install_via_setup_py(repo_path: str) -> bool:
    """Attempt to install via setup.py + setuptools (pip-free fallback)."""
    setup_py = Path(repo_path) / "setup.py"

    if not setup_py.exists():
        print(f"❌ setup.py not found at {setup_py}")
        return False

    print(f"📦 Attempting setup.py install from {repo_path}...")
    try:
        result = subprocess.run(
            [sys.executable, str(setup_py), "develop"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode == 0:
            print("✅ setup.py install succeeded")
            return True
        else:
            print(f"❌ setup.py install failed:\n{result.stderr}")
            return False
    except Exception as e:
        print(f"❌ setup.py install error: {e}")
        return False

def install_via_manual_sys_path() -> bool:
    """Manual sys.path configuration (last resort)."""
    print("🔧 Configuring sys.path manually (.pth file approach)...")

    repo_root = Path(__file__).parent.parent

    # Try user site-packages first (doesn't need root)
    import site
    user_site = site.USER_SITE
    if user_site:
        site_packages = Path(user_site)
    else:
        site_packages = Path(sys.exec_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"

    pth_file = site_packages / "corvinOS_dev.pth"

    # Create site-packages if missing (user site-packages should be writable)
    try:
        site_packages.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"❌ Permission denied creating {site_packages}")
        return False

    # Write .pth file
    pth_content = f"{repo_root}\n{repo_root / 'core' / 'console'}"
    try:
        pth_file.write_text(pth_content)
        print(f"✅ .pth file created: {pth_file}")
        return True
    except PermissionError:
        print(f"❌ Permission denied writing to {pth_file}")
        return False

def check_critical_dependencies() -> dict:
    """Check if critical Phase 2 dependencies are available."""
    critical = {
        "fastapi": False,
        "pydantic": False,
        "anthropic": False,
        "numpy": False,
        "scikit-learn": False,
        "pytest": False,
    }

    for module in critical:
        critical[module] = check_module(module)

    return critical

def main():
    print("=" * 60)
    print("Phase 2 Blocker: Dependency Installation")
    print("=" * 60)
    print()

    repo_path = Path(__file__).parent.parent

    # Step 1: Check current state
    print("📊 Checking current dependency state...")
    deps = check_critical_dependencies()
    missing = [k for k, v in deps.items() if not v]

    if not missing:
        print("✅ All critical dependencies already available!")
        return 0

    print(f"❌ Missing {len(missing)} dependencies: {', '.join(missing)}")
    print()

    # Step 2: Try setup.py approach
    print("🔄 Attempt 1: setup.py install...")
    if install_via_setup_py(str(repo_path)):
        # Verify
        deps_after = check_critical_dependencies()
        missing_after = [k for k, v in deps_after.items() if not v]
        if not missing_after:
            print(f"✅ Phase 2 Blocker RESOLVED! All dependencies installed.")
            print()
            print("🎉 Next Steps:")
            print("   1. Run Phase 2 tests: pytest tests/ -q")
            print("   2. Start console: python3 -m corvin_console")
            print("   3. Verify marketplace: curl http://localhost:8765/v1/console/marketplace/index")
            return 0
        else:
            print(f"⚠️  setup.py install partial: still missing {missing_after}")

    # Step 3: Fallback to manual sys.path
    print()
    print("🔄 Attempt 2: Manual sys.path configuration...")
    if install_via_manual_sys_path():
        print("✅ sys.path configured (partial fix)")
        print()
        print("⚠️  IMPORTANT: Dependency installation still required.")
        print("   To complete the fix, install dependencies:")
        print("   $ pip install -e . ")
        print("   or (if pip unavailable)")
        print("   $ python3 setup.py develop")
        print()
        return 1

    # Step 4: Failure
    print()
    print("❌ Phase 2 Blocker FIX FAILED")
    print()
    print("📋 Manual Installation Steps:")
    print("   1. Install pip (if not available):")
    print("      $ curl https://bootstrap.pypa.io/get-pip.py | python3")
    print("   2. Install CorvinOS in development mode:")
    print("      $ pip install -e /home/shumway/projects/CorvinOS")
    print("   3. Verify installation:")
    print("      $ python3 -c 'from core.console.corvin_console import app'")
    print()
    return 2

if __name__ == "__main__":
    sys.exit(main())
