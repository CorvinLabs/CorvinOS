"""CLI tests for corvin-license subcommands.

ADR-0703 §2.4: Argparse interface, subcommand dispatch, audit trail.
"""
from __future__ import annotations

import pytest
import tempfile
import json
from pathlib import Path
from io import StringIO
import sys

from core.operator.license.cli import (
    main,
    cmd_activate_request,
    cmd_activate_redeem,
    cmd_status,
    cmd_deactivate,
    cmd_bind_offline_request,
    cmd_crl_import,
    cmd_credential_import,
)


class TestActivateCommand:
    """activate --request | activate <code>"""

    def test_activate_request_flag(self, capsys):
        """activate --request returns success."""
        ret = main(["activate", "--request"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Activation code:" in captured.out

    def test_activate_with_code(self, capsys):
        """activate <code> redeems the code."""
        ret = main(["activate", "test-code-123"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Redeeming code:" in captured.out

    def test_activate_no_args_fails(self, capsys):
        """activate with no args/flags returns error."""
        ret = main(["activate"])
        assert ret == 1


class TestStatusCommand:
    """status — show license tier, seat, expiry"""

    def test_status_no_license(self, capsys):
        """status when free returns FREE tier."""
        ret = main(["status"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Status: FREE" in captured.out or "free" in captured.out.lower()

    def test_status_shows_tier_and_fields(self, capsys):
        """status output includes all required fields."""
        ret = main(["status"])
        assert ret == 0
        captured = capsys.readouterr()
        output = captured.out.lower()
        assert "seat" in output
        assert "expir" in output
        assert "offline" in output
        assert "device" in output


class TestDeactivateCommand:
    """deactivate — revert to free"""

    def test_deactivate_returns_success(self, capsys):
        """deactivate returns 0."""
        ret = main(["deactivate"])
        assert ret == 0


class TestBindCommand:
    """bind --offline-request"""

    def test_bind_offline_request_flag_required(self):
        """bind without --offline-request fails."""
        ret = main(["bind"])
        assert ret == 1

    def test_bind_offline_request_success(self, capsys):
        """bind --offline-request returns success."""
        ret = main(["bind", "--offline-request"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Device fingerprint:" in captured.out or "fingerprint" in captured.out.lower()


class TestCRLCommand:
    """crl import <file>"""

    def test_crl_import_nonexistent_file(self, capsys):
        """crl import nonexistent file returns error."""
        ret = main(["crl", "import", "/nonexistent/crl.json"])
        assert ret == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower()

    def test_crl_import_valid_file(self, capsys):
        """crl import valid delta file returns success."""
        with tempfile.TemporaryDirectory() as tmpdir:
            crl_path = Path(tmpdir) / "crl.json"
            crl_data = {
                "pages": [
                    {"issued_at": 1234567890, "revoked_serials": ["s1", "s2"], "serial": "root"}
                ]
            }
            crl_path.write_text(json.dumps(crl_data), encoding="utf-8")

            ret = main(["crl", "import", str(crl_path)])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Imported" in captured.out


class TestCredentialCommand:
    """credential import <file>"""

    def test_credential_import_nonexistent_file(self, capsys):
        """credential import nonexistent file returns error."""
        ret = main(["credential", "import", "/nonexistent/cred.jwt"])
        assert ret == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower()

    def test_credential_import_jwt(self, capsys):
        """credential import JWT file returns success."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_path = Path(tmpdir) / "cred.jwt"
            # Minimal valid JWT-like token (eyJ... prefix)
            jwt_token = "eyJhbGciOiJFZDI1NTE5IiwidHlwIjoiSldUIn0.test.signature"
            cred_path.write_text(jwt_token, encoding="utf-8")

            ret = main(["credential", "import", str(cred_path)])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Imported" in captured.out


class TestFeaturesURLOverride:
    """--features-url flag for staging"""

    def test_features_url_default(self, capsys):
        """--features-url defaults to production."""
        ret = main(["--features-url", "https://license.corvin-labs.com", "status"])
        assert ret == 0

    def test_features_url_staging(self, capsys):
        """--features-url can override to staging."""
        ret = main([
            "--features-url", "https://staging-license.corvin-labs.com",
            "status"
        ])
        assert ret == 0


class TestArgparse:
    """Argument parsing and dispatch"""

    def test_main_no_args_prints_help(self, capsys):
        """main() with no args prints help."""
        ret = main([])
        assert ret == 0
        captured = capsys.readouterr()
        assert "corvin-license" in captured.out.lower() or "usage" in captured.out.lower()

    def test_unknown_command_fails(self):
        """Unknown subcommand fails."""
        ret = main(["unknown-cmd"])
        assert ret != 0

    def test_log_level_argument(self, capsys):
        """--log-level sets logging level."""
        ret = main(["--log-level", "DEBUG", "status"])
        assert ret == 0


class TestCLIIntegration:
    """End-to-end CLI flows"""

    def test_full_flow_activate_status_deactivate(self, capsys):
        """Typical flow: request → status → deactivate."""
        # Request
        ret = main(["activate", "--request"])
        assert ret == 0

        # Status
        ret = main(["status"])
        assert ret == 0

        # Deactivate
        ret = main(["deactivate"])
        assert ret == 0
