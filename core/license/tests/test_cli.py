"""Per-subtask E2E for the CLI — install + show + revoke round-trip."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from corvin_license import cli, verifier


def test_cli_keygen_creates_valid_keypair(tmp_path, monkeypatch):
    priv_path = tmp_path / "p.pem"
    pub_path = tmp_path / "P.pem"
    import sys
    monkeypatch.setattr(sys, "argv", ["corvin-license", "keygen",
                                       str(priv_path), str(pub_path)])
    code = cli.main()
    assert code == 0
    assert priv_path.exists()
    assert pub_path.exists()
    assert priv_path.stat().st_mode & 0o777 == 0o600
    # Smoke check: signing with the priv should verify with the pub.
    # ADR-0019: tier 'pro' canonical flag set must be present.
    import jwt as _pyjwt
    token = _pyjwt.encode(
        {"iss": "corvin-maintainer", "iat": int(time.time()),
         "exp": int(time.time()) + 1000, "customer_id": "x",
         "tier": "pro", "employee_count_max": 10, "seats": 5,
         "feature_flags": ["compliance_reports_premium", "compute"]},
        priv_path.read_bytes(),
        algorithm="RS256",
    )
    lic = verifier.verify_token(token, pubkey_pem=pub_path.read_bytes())
    assert lic.tier == "pro"


def test_cli_issue_signs_a_valid_token(tmp_path, rs256_keypair):
    """Issue with explicit --flags matching the tier's canonical set.

    ADR-0019: --flags must exactly equal the tier's canonical flag set.
    The previous test passed "sso_wizard,worm_archive" with
    --tier business, which is now (correctly) rejected because
    business doesn't grant worm_archive. We pass the canonical set
    instead.
    """
    _priv, pub_pem, priv_path, _pub_path = rs256_keypair
    out_path = tmp_path / "license.jwt"
    import sys
    sys.argv = [
        "corvin-license", "issue", str(priv_path),
        "--customer-id", "test-cust-01",
        "--tier", "business",
        "--employee-count-max", "500",
        "--seats", "100",
        "--days", "30",
        "--flags", "compliance_reports_premium,sso_wizard,support_integration,compute",
        "--out", str(out_path),
    ]
    code = cli.main()
    assert code == 0
    assert out_path.exists()
    token = out_path.read_text().strip()
    lic = verifier.verify_token(token, pubkey_pem=pub_pem)
    assert lic.customer_id == "test-cust-01"
    assert lic.tier == "business"
    assert "sso_wizard" in lic.feature_flags
    # All canonical business flags must be present.
    assert set(lic.feature_flags) == {
        "compliance_reports_premium", "sso_wizard", "support_integration", "compute",
    }


def test_cli_issue_signs_valid_token_personal_tier(tmp_path, rs256_keypair):
    """ADR-0092: personal tier (€9/month single-device) must be issuable via CLI."""
    _priv, pub_pem, priv_path, _pub_path = rs256_keypair
    out_path = tmp_path / "personal_license.jwt"
    import sys
    sys.argv = [
        "corvin-license", "issue", str(priv_path),
        "--customer-id", "personal-cust-01",
        "--tier", "personal",
        "--employee-count-max", "1",
        "--seats", "1",
        "--days", "30",
        "--out", str(out_path),
    ]
    code = cli.main()
    assert code == 0
    assert out_path.exists()
    token = out_path.read_text().strip()
    lic = verifier.verify_token(token, pubkey_pem=pub_pem)
    assert lic.customer_id == "personal-cust-01"
    assert lic.tier == "personal"
    # All canonical personal flags must be present (same feature set as pro).
    assert set(lic.feature_flags) == {
        "compliance_reports_premium", "compute",
    }


def test_cli_issue_signs_valid_token_member_tier(tmp_path, rs256_keypair):
    """ADR-0097/ADR-0098: member tier (€10/month flat-rate) must be issuable via CLI."""
    _priv, pub_pem, priv_path, _pub_path = rs256_keypair
    out_path = tmp_path / "member_license.jwt"
    import sys
    sys.argv = [
        "corvin-license", "issue", str(priv_path),
        "--customer-id", "member-cust-01",
        "--tier", "member",
        "--employee-count-max", "1",
        "--seats", "1",
        "--days", "30",
        "--out", str(out_path),
    ]
    code = cli.main()
    assert code == 0
    assert out_path.exists()
    token = out_path.read_text().strip()
    lic = verifier.verify_token(token, pubkey_pem=pub_pem)
    assert lic.customer_id == "member-cust-01"
    assert lic.tier == "member"
    # All canonical member flags must be present.
    assert set(lic.feature_flags) == {
        "compliance_reports_premium", "worm_archive", "sla_dashboard",
        "compute", "compute_fabric",
    }


def test_cli_issue_defaults_flags_from_tier(tmp_path, rs256_keypair):
    """ADR-0019: omitting --flags fills in the canonical set for the tier."""
    _priv, pub_pem, priv_path, _pub_path = rs256_keypair
    out_path = tmp_path / "license.jwt"
    import sys
    sys.argv = [
        "corvin-license", "issue", str(priv_path),
        "--customer-id", "default-flags-cust",
        "--tier", "enterprise",
        "--employee-count-max", "1000",
        "--seats", "200",
        "--days", "30",
        # no --flags
        "--out", str(out_path),
    ]
    assert cli.main() == 0
    token = out_path.read_text().strip()
    lic = verifier.verify_token(token, pubkey_pem=pub_pem)
    # All nine enterprise flags must ride along.
    assert set(lic.feature_flags) == {
        "compliance_reports_premium", "cross_tenant_search", "sso_wizard",
        "worm_archive", "sla_dashboard", "support_integration",
        "white_label_ui", "compute", "compute_fabric",
    }


def test_cli_issue_rejects_off_tier_flags(tmp_path, rs256_keypair, capsys):
    """ADR-0019: --flags that mismatch the tier are refused with exit 4."""
    _priv, _pub_pem, priv_path, _pub_path = rs256_keypair
    out_path = tmp_path / "license.jwt"
    import sys
    sys.argv = [
        "corvin-license", "issue", str(priv_path),
        "--customer-id", "off-tier-cust",
        "--tier", "pro",
        "--employee-count-max", "100",
        "--seats", "20",
        "--days", "30",
        # pro tier does NOT grant worm_archive.
        "--flags", "compliance_reports_premium,worm_archive",
        "--out", str(out_path),
    ]
    code = cli.main()
    assert code == 4
    captured = capsys.readouterr()
    assert "tier-flag" in captured.err
    # JWT file must NOT have been written.
    assert not out_path.exists()


def test_cli_install_and_show_and_revoke_round_trip(
    sandbox_home, rs256_keypair, pinned_pubkey, tmp_path, monkeypatch, capsys,
):
    _priv, _pub_pem, priv_path, _pub_path = rs256_keypair
    issued_token_path = tmp_path / "issued.jwt"

    # 1. Issue
    import sys
    sys.argv = [
        "corvin-license", "issue", str(priv_path),
        "--customer-id", "round-trip-cust",
        "--tier", "enterprise",
        "--employee-count-max", "5000",
        "--seats", "500",
        "--days", "365",
        "--out", str(issued_token_path),
    ]
    assert cli.main() == 0

    # 2. Install
    sys.argv = ["corvin-license", "install", str(issued_token_path)]
    assert cli.main() == 0
    capsys.readouterr()  # clear the "installed license for..." line

    # license.jwt must now exist with mode 0o600
    installed = verifier.license_file_path()
    assert installed.exists()
    assert installed.stat().st_mode & 0o777 == 0o600

    # 3. Show — should report the license as active
    sys.argv = ["corvin-license", "show"]
    assert cli.main() == 0
    captured = capsys.readouterr()
    status = json.loads(captured.out)
    assert status["tier"] == "enterprise"
    assert status["mode"] == "licensed-active"

    # 4. Revoke — must use a valid reason code (enforced by argparse choices +
    # the audit reason-code allowlist; an invalid free-text reason now aborts).
    sys.argv = ["corvin-license", "revoke", "--reason", "renewal"]
    assert cli.main() == 0
    assert not installed.exists()

    # 5. Show after revoke — back to free tier
    capsys.readouterr()  # clear buffer
    sys.argv = ["corvin-license", "show"]
    assert cli.main() == 0
    captured = capsys.readouterr()
    status = json.loads(captured.out)
    assert status["tier"] == "free"


def test_cli_install_refuses_bad_signature(
    sandbox_home, rs256_keypair, pinned_pubkey, tmp_path,
):
    """Operator can't sneak in a license signed with a different key."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    other_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_priv_pem = other_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    other_priv_path = tmp_path / "evil-priv.pem"
    other_priv_path.write_bytes(other_priv_pem)

    bad_token_path = tmp_path / "bad.jwt"
    import sys
    sys.argv = [
        "corvin-license", "issue", str(other_priv_path),
        "--customer-id", "evil-cust",
        "--tier", "enterprise",
        "--employee-count-max", "999999",
        "--seats", "99999",
        "--out", str(bad_token_path),
    ]
    assert cli.main() == 0  # signing with the wrong key still works

    sys.argv = ["corvin-license", "install", str(bad_token_path)]
    code = cli.main()
    assert code != 0  # install must refuse
    # license.jwt must NOT have been written
    assert not verifier.license_file_path().exists()
