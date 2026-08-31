"""Tests for Gap 2: Signature verification."""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile

from core.plugins.marketplace import (
    PluginMarketplace,
    PluginMetadata,
    PluginOrigin,
    BootLayer,
    PluginCategory,
    verify_ed25519_signature,
    SignatureVerificationError,
)


class TestSignatureVerification:
    """Test ED25519 signature verification (Gap 2)."""

    @patch("core.plugins.marketplace.CRYPTO_AVAILABLE", False)
    def test_crypto_not_available(self):
        """Raise error when cryptography library not installed."""
        with pytest.raises(SignatureVerificationError, match="cryptography library not installed"):
            verify_ed25519_signature(
                Path("/tmp/test.whl"),
                "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----",
                b"signature",
            )

    @pytest.mark.skipif(
        not pytest.importorskip("cryptography", minversion=None),
        reason="cryptography not installed",
    )
    def test_invalid_public_key_format(self):
        """Raise error for invalid PEM format."""
        with pytest.raises(SignatureVerificationError):
            verify_ed25519_signature(
                Path("/tmp/test.whl"),
                "not a valid pem key",
                b"signature",
            )

    @pytest.mark.skipif(
        not pytest.importorskip("cryptography", minversion=None),
        reason="cryptography not installed",
    )
    def test_missing_package_file(self):
        """Raise error when package file doesn't exist."""
        with pytest.raises(SignatureVerificationError):
            verify_ed25519_signature(
                Path("/nonexistent/test.whl"),
                "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----",
                b"signature",
            )


class TestMarketplaceInstallWithVerification:
    """Test PluginMarketplace.install_plugin_with_verification()."""

    def test_install_builtin_no_verification(self):
        """BUILTIN plugins don't require signature verification."""
        marketplace = PluginMarketplace()

        # Add a builtin plugin
        plugin = PluginMetadata(
            plugin_id="builtin-auth",
            name="Built-in Auth",
            version="1.0.0",
            category=PluginCategory.AUTHENTICATION,
            boot_layer=BootLayer.CORE,
            origin=PluginOrigin.BUILTIN,  # No verification needed
            author_id="corvin-team",
            author_email="team@corvin.io",
            license="Apache-2.0",
            description="Built-in auth provider",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        with tempfile.TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "plugin.whl"
            package_path.write_text("dummy package")

            # Should succeed without signature
            result = marketplace.install_plugin_with_verification(
                "builtin-auth",
                package_path,
                signature_path=None,
            )
            assert result is True

    def test_install_community_no_verification(self):
        """COMMUNITY plugins don't require signature verification."""
        marketplace = PluginMarketplace()

        plugin = PluginMetadata(
            plugin_id="community-plugin",
            name="Community Plugin",
            version="1.0.0",
            category=PluginCategory.TOOLING,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,  # No verification needed
            author_id="community-user",
            author_email="user@example.com",
            license="MIT",
            description="Community plugin",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        with tempfile.TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "plugin.whl"
            package_path.write_text("dummy package")

            # Should succeed without signature
            result = marketplace.install_plugin_with_verification(
                "community-plugin",
                package_path,
                signature_path=None,
            )
            assert result is True

    def test_install_vetted_missing_public_key(self):
        """VETTED plugins with missing public_key should fail."""
        marketplace = PluginMarketplace()

        plugin = PluginMetadata(
            plugin_id="vetted-plugin",
            name="Vetted Plugin",
            version="2.0.0",
            category=PluginCategory.SECURITY,
            boot_layer=BootLayer.BUNDLED,
            origin=PluginOrigin.VETTED,
            author_id="vetted-author",
            author_email="author@corvin.io",
            license="Apache-2.0",
            description="Vetted plugin",
            long_description="",
            public_key=None,  # Missing!
        )
        marketplace.register_plugin(plugin)

        with tempfile.TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "plugin.whl"
            package_path.write_text("dummy package")

            # Should fail for missing public key
            with pytest.raises(SignatureVerificationError, match="missing public_key"):
                marketplace.install_plugin_with_verification(
                    "vetted-plugin",
                    package_path,
                    signature_path=None,
                )

    def test_install_vetted_missing_signature_file(self):
        """VETTED plugins with missing signature file should fail."""
        marketplace = PluginMarketplace()

        plugin = PluginMetadata(
            plugin_id="vetted-plugin",
            name="Vetted Plugin",
            version="2.0.0",
            category=PluginCategory.SECURITY,
            boot_layer=BootLayer.BUNDLED,
            origin=PluginOrigin.VETTED,
            author_id="vetted-author",
            author_email="author@corvin.io",
            license="Apache-2.0",
            description="Vetted plugin",
            long_description="",
            public_key="-----BEGIN PUBLIC KEY-----\ntest-key\n-----END PUBLIC KEY-----",
        )
        marketplace.register_plugin(plugin)

        with tempfile.TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "plugin.whl"
            package_path.write_text("dummy package")

            # Should fail for missing signature file
            with pytest.raises(SignatureVerificationError, match="Signature file not found"):
                marketplace.install_plugin_with_verification(
                    "vetted-plugin",
                    package_path,
                    signature_path=None,
                )

    def test_install_skip_verification_flag(self):
        """skip_verification flag bypasses signature check (debug mode)."""
        marketplace = PluginMarketplace()

        plugin = PluginMetadata(
            plugin_id="vetted-plugin",
            name="Vetted Plugin",
            version="2.0.0",
            category=PluginCategory.SECURITY,
            boot_layer=BootLayer.BUNDLED,
            origin=PluginOrigin.VETTED,
            author_id="vetted-author",
            author_email="author@corvin.io",
            license="Apache-2.0",
            description="Vetted plugin",
            long_description="",
            public_key="-----BEGIN PUBLIC KEY-----\ntest-key\n-----END PUBLIC KEY-----",
        )
        marketplace.register_plugin(plugin)

        with tempfile.TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "plugin.whl"
            package_path.write_text("dummy package")

            # Should succeed with skip flag (even without signature file)
            result = marketplace.install_plugin_with_verification(
                "vetted-plugin",
                package_path,
                signature_path=None,
                skip_verification=True,  # DEBUG MODE
            )
            assert result is True

    def test_install_plugin_not_found(self):
        """Raise error if plugin not found in marketplace."""
        marketplace = PluginMarketplace()

        with tempfile.TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "plugin.whl"
            package_path.write_text("dummy package")

            with pytest.raises(ValueError, match="Plugin not found"):
                marketplace.install_plugin_with_verification(
                    "nonexistent-plugin",
                    package_path,
                    signature_path=None,
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
