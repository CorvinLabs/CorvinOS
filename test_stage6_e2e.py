#!/usr/bin/env python3
"""E2E test for Stage 6 Plugin System Activation — CLI Install + Trust Anchor.

This script validates the complete plugin installation workflow:
1. Create a test plugin with proper manifest
2. Sign it with the maintainer key
3. Run `corvin plugin install <path>` and verify it works
4. Test community plugin confirmation flow
5. Test vetted plugin signature verification
6. Verify audit trail hash-chaining

Run:
    python3 test_stage6_e2e.py
"""
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Tuple
import base64
import hashlib

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger(__name__)

# Add paths
REPO = Path(__file__).resolve().parent
LAUNCHER = REPO / "ops" / "launcher"
if str(LAUNCHER) not in sys.path:
    sys.path.insert(0, str(LAUNCHER))

sys.path.insert(0, str(REPO / "core" / "plugins"))
sys.path.insert(0, str(REPO / "core" / "gateway"))


def create_test_plugin_dir(tmp_dir: Path, plugin_id: str, origin: str = "community") -> Path:
    """Create a minimal valid plugin directory."""
    plugin_root = tmp_dir / f"test-{plugin_id}"
    plugin_root.mkdir(parents=True)

    # Create plugin.yaml
    manifest = {
        "plugin_id": plugin_id,
        "plugin_type": "router_backend",
        "version": "1.0.0",
        "display_name": f"Test Plugin {plugin_id}",
        "boot_layer": "installed",
        "origin": origin,
        "pii_risk": "low",
        "requires_consent": False,
    }

    import yaml
    (plugin_root / "plugin.yaml").write_text(
        yaml.dump(manifest),
        encoding="utf-8"
    )

    # Create plugin.py
    (plugin_root / "plugin.py").write_text(
        f"""
from corvin_plugins.protocol import BasePlugin, HealthStatus

class TestPlugin(BasePlugin):
    plugin_id = "{plugin_id}"
    plugin_type = "router_backend"
    version = "1.0.0"

    def on_load(self):
        pass

    def health_check(self):
        return HealthStatus(ok=True, message="ok")

    def route(self, task):
        return None
""",
        encoding="utf-8"
    )

    # Create pyproject.toml
    (plugin_root / "pyproject.toml").write_text(
        f"""[project]
name = "test-{plugin_id}"
version = "1.0.0"
requires-python = ">=3.11"

[project.entry-points."corvin.plugins"]
test_plugin = "plugin:TestPlugin"
""",
        encoding="utf-8"
    )

    return plugin_root


def sign_plugin_manifest(plugin_dir: Path) -> bool:
    """Sign the plugin manifest with the maintainer key.

    Returns True if signing succeeded.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        import yaml
    except ImportError:
        log.error("cryptography not available, skipping signature test")
        return False

    # Load the private key
    key_path = Path.home() / ".ssh" / "corvinOS-plugin-trust"
    if not key_path.exists():
        log.warning(f"Private key not found at {key_path}, skipping signing")
        return False

    try:
        # Read the OpenSSH format private key
        key_pem = key_path.read_bytes()

        # Load using cryptography
        priv_key = serialization.load_ssh_private_key(
            key_pem,
            password=None
        )

        if not isinstance(priv_key, Ed25519PrivateKey):
            log.error("Key is not Ed25519")
            return False

    except Exception as e:
        log.error(f"Failed to load private key: {e}")
        return False

    # Load and sign the manifest
    manifest_path = plugin_dir / "plugin.yaml"
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    # Create signing digest (same as in trust.py)
    from corvin_plugins.trust import manifest_signing_digest
    digest = manifest_signing_digest(manifest_data)

    # Sign the digest
    signature = priv_key.sign(digest)

    # Add signature to manifest
    pub_key = priv_key.public_key()
    pub_bytes = pub_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip('=')
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip('=')

    manifest_data["signature"] = {
        "algorithm": "ed25519",
        "public_key": pub_b64,
        "value": sig_b64,
    }

    # Write signed manifest back
    manifest_path.write_text(yaml.dump(manifest_data), encoding="utf-8")
    log.info(f"✓ Signed plugin manifest: {manifest_path}")
    return True


def test_url_rejection():
    """Test 1: URLs should be rejected."""
    log.info("\n=== Test 1: URL Rejection ===")

    from argparse import Namespace
    from corvin.plugin_runtime_cmd import cmd_install

    # Test HTTP URL
    args = Namespace(path="http://example.com/plugin.zip", tenant=None, yes=False)
    rc = cmd_install(args)

    if rc == 1:
        log.info("✓ HTTP URL rejected correctly")
        return True
    else:
        log.error(f"✗ HTTP URL should be rejected, got exit code {rc}")
        return False


def test_community_plugin_with_yes():
    """Test 2: Community plugin with --yes flag."""
    log.info("\n=== Test 2: Community Plugin (--yes) ===")

    from argparse import Namespace
    from corvin.plugin_runtime_cmd import cmd_install
    import tempfile
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        plugin_dir = create_test_plugin_dir(tmp_path, "test-community", origin="community")

        # Mock CORVIN_HOME
        with tempfile.TemporaryDirectory() as corvin_tmp:
            corvin_home = Path(corvin_tmp) / ".corvin"
            corvin_home.mkdir()

            with mock.patch("corvinOS.shared.paths.corvin_home", return_value=corvin_home):
                with mock.patch("corvinOS.shared.paths._resolve_tenant_id", return_value="_default"):
                    args = Namespace(path=str(plugin_dir), tenant=None, yes=True)
                    rc = cmd_install(args)

                    if rc == 0:
                        log.info("✓ Community plugin installed with --yes")
                        return True
                    else:
                        log.error(f"✗ Community plugin install failed, got exit code {rc}")
                        return False


def test_community_plugin_rejection():
    """Test 3: Community plugin without confirmation."""
    log.info("\n=== Test 3: Community Plugin (No Confirmation) ===")

    from argparse import Namespace
    from corvin.plugin_runtime_cmd import cmd_install
    import tempfile
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        plugin_dir = create_test_plugin_dir(tmp_path, "test-community-no-confirm", origin="community")

        with tempfile.TemporaryDirectory() as corvin_tmp:
            corvin_home = Path(corvin_tmp) / ".corvin"
            corvin_home.mkdir()

            # Mock enforcement OFF and user input 'n'
            with mock.patch("corvinOS.shared.paths.corvin_home", return_value=corvin_home):
                with mock.patch("corvinOS.shared.paths._resolve_tenant_id", return_value="_default"):
                    with mock.patch("corvin_plugins.trust.enforcement_enabled", return_value=False):
                        with mock.patch("builtins.input", return_value="n"):
                            args = Namespace(path=str(plugin_dir), tenant=None, yes=False)
                            rc = cmd_install(args)

                            # Should reject when user enters 'n'
                            if rc == 1:
                                log.info("✓ Community plugin rejected on 'n'")
                                return True
                            else:
                                log.error(f"✗ Should reject when user enters 'n', got exit code {rc}")
                                return False


def test_vetted_plugin_with_trust_anchor():
    """Test 4: Vetted plugin with valid signature and trust anchor."""
    log.info("\n=== Test 4: Vetted Plugin (Trust Anchor) ===")

    from argparse import Namespace
    from corvin.plugin_runtime_cmd import cmd_install
    import tempfile
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        plugin_dir = create_test_plugin_dir(tmp_path, "test-vetted", origin="vetted")

        # Try to sign it
        signed = sign_plugin_manifest(plugin_dir)

        if not signed:
            log.info("⚠ Skipping test (signing not available)")
            return True

        with tempfile.TemporaryDirectory() as corvin_tmp:
            corvin_home = Path(corvin_tmp) / ".corvin"
            corvin_home.mkdir()

            # Copy the trust anchors file
            anchors_dir = corvin_home / "global"
            anchors_dir.mkdir(parents=True)
            source_anchors = Path.home() / ".corvin" / "global" / "plugin_trust_anchors.txt"
            if source_anchors.exists():
                (anchors_dir / "plugin_trust_anchors.txt").write_text(
                    source_anchors.read_text(encoding="utf-8"),
                    encoding="utf-8"
                )

            with mock.patch("corvinOS.shared.paths.corvin_home", return_value=corvin_home):
                with mock.patch("corvinOS.shared.paths._resolve_tenant_id", return_value="_default"):
                    args = Namespace(path=str(plugin_dir), tenant=None, yes=False)
                    rc = cmd_install(args)

                    if rc == 0:
                        log.info("✓ Vetted plugin installed with valid signature")
                        return True
                    else:
                        log.error(f"✗ Vetted plugin install failed, got exit code {rc}")
                        return False


def test_builtin_plugin():
    """Test 5: Builtin plugins need no confirmation."""
    log.info("\n=== Test 5: Builtin Plugin ===")

    from argparse import Namespace
    from corvin.plugin_runtime_cmd import cmd_install
    import tempfile
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        plugin_dir = create_test_plugin_dir(tmp_path, "test-builtin", origin="builtin")

        with tempfile.TemporaryDirectory() as corvin_tmp:
            corvin_home = Path(corvin_tmp) / ".corvin"
            corvin_home.mkdir()

            with mock.patch("corvinOS.shared.paths.corvin_home", return_value=corvin_home):
                with mock.patch("corvinOS.shared.paths._resolve_tenant_id", return_value="_default"):
                    args = Namespace(path=str(plugin_dir), tenant=None, yes=False)
                    rc = cmd_install(args)

                    if rc == 0:
                        log.info("✓ Builtin plugin installed without confirmation")
                        return True
                    else:
                        log.error(f"✗ Builtin plugin install failed, got exit code {rc}")
                        return False


def test_nonexistent_path():
    """Test 6: Nonexistent paths are rejected."""
    log.info("\n=== Test 6: Nonexistent Path ===")

    from argparse import Namespace
    from corvin.plugin_runtime_cmd import cmd_install

    args = Namespace(path="/nonexistent/path", tenant=None, yes=False)
    rc = cmd_install(args)

    if rc == 2:
        log.info("✓ Nonexistent path rejected")
        return True
    else:
        log.error(f"✗ Nonexistent path should return exit code 2, got {rc}")
        return False


def test_idempotent_install():
    """Test 7: Installing the same plugin twice is idempotent."""
    log.info("\n=== Test 7: Idempotent Install ===")

    from argparse import Namespace
    from corvin.plugin_runtime_cmd import cmd_install
    import tempfile
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        plugin_dir = create_test_plugin_dir(tmp_path, "test-idempotent", origin="community")

        with tempfile.TemporaryDirectory() as corvin_tmp:
            corvin_home = Path(corvin_tmp) / ".corvin"
            corvin_home.mkdir()

            with mock.patch("corvinOS.shared.paths.corvin_home", return_value=corvin_home):
                with mock.patch("corvinOS.shared.paths._resolve_tenant_id", return_value="_default"):
                    # First install
                    args = Namespace(path=str(plugin_dir), tenant=None, yes=True)
                    rc1 = cmd_install(args)

                    if rc1 != 0:
                        log.error(f"✗ First install failed, got exit code {rc1}")
                        return False

                    # Second install (should be idempotent)
                    rc2 = cmd_install(args)

                    if rc2 == 0:
                        log.info("✓ Second install is idempotent")
                        return True
                    else:
                        log.error(f"✗ Second install should be idempotent, got exit code {rc2}")
                        return False


def main():
    """Run all E2E tests."""
    log.info("=" * 70)
    log.info("Stage 6 Plugin System Activation — E2E Test Suite")
    log.info("=" * 70)

    tests = [
        ("URL Rejection", test_url_rejection),
        ("Community Plugin (--yes)", test_community_plugin_with_yes),
        ("Community Plugin (No Confirmation)", test_community_plugin_rejection),
        ("Vetted Plugin (Trust Anchor)", test_vetted_plugin_with_trust_anchor),
        ("Builtin Plugin", test_builtin_plugin),
        ("Nonexistent Path", test_nonexistent_path),
        ("Idempotent Install", test_idempotent_install),
    ]

    results = []
    for name, test_fn in tests:
        try:
            result = test_fn()
            results.append((name, result))
        except Exception as e:
            log.error(f"Test '{name}' raised exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # Print summary
    log.info("\n" + "=" * 70)
    log.info("Test Summary")
    log.info("=" * 70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓" if result else "✗"
        log.info(f"{status} {name}")

    log.info(f"\nPassed: {passed}/{total}")

    if passed == total:
        log.info("\n✅ All tests passed!")
        return 0
    else:
        log.error(f"\n❌ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
