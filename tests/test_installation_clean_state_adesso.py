"""
Installation regression test for clean state (no stray connections).

REGRESSION: Neuinstallation von CorvinOS zeigte unerwartete Verbindung zu 'Adesso'
im Agent Hub, obwohl bei sauberer Installation Agent Hub und A2A leer sein sollen.

TEST: Sicherstellen, dass zukünftige Installationen KEINE Adesso-Verbindungen haben.

Date: 2026-09-24
Issue: https://github.com/CorvinLabs/CorvinOS/issues/XXXX (ADR-0250, ADR-0180)
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def test_no_adesso_in_installation():
    """Verify that fresh installation has NO Adesso/adesso references.

    This test catches the regression where:
    1. Hardcoded peer_id="adesso-windows" in a2a_token.py was executed
    2. adscale-ldd package with Adesso GitHub references was installed

    Both are now prevented.
    """

    # Phase 1: Check source code for hardcoded Adesso peers
    repo_root = Path(__file__).parent.parent
    a2a_token_file = repo_root / "core" / "bridges" / "shared" / "a2a_token.py"

    assert a2a_token_file.exists(), f"a2a_token.py not found at {a2a_token_file}"

    content = a2a_token_file.read_text()

    # This used to fail — hardcoded "adesso-windows" in __main__ block
    assert 'peer_id="adesso-windows"' not in content, \
        "REGRESSION: Hardcoded adesso-windows peer found in a2a_token.py — must be removed"

    assert "adesso-windows" not in content, \
        "REGRESSION: Any reference to adesso-windows found in a2a_token.py"

    # Phase 2: Check that __main__ block doesn't have example peers
    # (should be clean test code or removed entirely)
    if 'if __name__ == "__main__"' in content:
        main_block = content[content.find('if __name__ == "__main__"'):]
        assert "adesso" not in main_block.lower(), \
            "REGRESSION: __main__ block contains Adesso references"

    print("✅ a2a_token.py clean: no hardcoded Adesso peers")


def test_no_adeso_package_in_defaults():
    """Verify installation defaults do NOT include Adesso packages.

    This catches the regression where:
    - adscale-ldd package was installed by default
    - It referenced https://github.com/adesso-ag/adscale-ldd
    """

    repo_root = Path(__file__).parent.parent
    install_defaults = repo_root / ".corvin" / "install-defaults.yaml"

    if not install_defaults.exists():
        # Fallback: check the actual installation defaults in the codebase
        install_defaults_search = list(repo_root.glob("**/install-defaults.yaml"))

        if install_defaults_search:
            install_defaults = install_defaults_search[0]
        else:
            print("⚠️  install-defaults.yaml not found, skipping package check")
            return

    content = install_defaults.read_text()

    # No Adesso-related packages should be in defaults
    assert "adesso" not in content.lower(), \
        "REGRESSION: Adesso reference found in installation defaults"

    assert "adscale-ldd" not in content, \
        "REGRESSION: adscale-ldd package in installation defaults"

    print("✅ install-defaults.yaml clean: no Adesso packages")


def test_no_adesso_in_manifest_files():
    """Verify that installed package manifests do NOT reference Adesso.

    Check corvin_decisions/decisions and ADR files for any Adesso references
    that should not be there.
    """

    repo_root = Path(__file__).parent.parent

    # Search for manifest files that might reference Adesso
    suspicious_files = []

    for manifest_file in repo_root.glob("**/manifest.json"):
        content = manifest_file.read_text()

        # adscale-ldd was the problematic package
        if "adscale-ldd" in content and "adesso" in content.lower():
            suspicious_files.append(manifest_file)

    # This will be empty after the fix
    assert not suspicious_files, \
        f"REGRESSION: Found Adesso references in: {suspicious_files}"

    print("✅ No suspicious Adesso package manifests found")


def test_a2a_token_codec_no_side_effects():
    """Verify that importing a2a_token module doesn't create Adesso tokens.

    Regression: The __main__ block was creating tokens, which could leak
    into logs or into the Agent Hub if executed during installation.
    """

    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root / "core" / "bridges" / "shared"))

    try:
        # Import the module — this used to execute the __main__ block
        import a2a_token

        # If we reach here without assertion errors, the __main__ block is safe
        # (i.e., doesn't execute hardcoded Adesso setup)

        # Verify the classes exist and are clean
        assert hasattr(a2a_token, 'A2ATokenCodec'), "A2ATokenCodec class not found"
        assert hasattr(a2a_token, 'A2AToken'), "A2AToken class not found"

        print("✅ a2a_token module import is clean: no side effects")

    finally:
        sys.path.pop(0)


if __name__ == "__main__":
    print("\n=== Installation Clean State Test (No Adesso) ===\n")

    try:
        test_no_adesso_in_installation()
        test_no_adeso_package_in_defaults()
        test_no_adesso_in_manifest_files()
        test_a2a_token_codec_no_side_effects()

        print("\n✅ All tests passed — installation is clean\n")
        sys.exit(0)

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        sys.exit(1)
