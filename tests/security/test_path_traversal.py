"""Path traversal vulnerability tests (OWASP A01:2021).

Comprehensive tests for input_validator.py and autonomous_forge_routes.py
path traversal prevention. All tests must PASS to ensure security.

Test coverage:
  - Valid skill_id formats
  - Valid version formats (semver)
  - Invalid skill_id with traversal escapes (../../, ..\\, etc.)
  - Invalid version with traversal escapes
  - Length boundary validation
  - Empty/None input handling
  - Route-level validation (endpoint security)
"""
from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from core.console.corvin_console.validation.input_validator import (
    validate_skill_id,
    validate_version,
    validate_filename,
    validate_path_component,
)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: validate_skill_id
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateSkillId:
    """Unit tests for validate_skill_id function."""

    def test_valid_skill_id_with_dots(self):
        """Valid: 'os.delegation_router'."""
        assert validate_skill_id("os.delegation_router") is True

    def test_valid_skill_id_simple(self):
        """Valid: 'my_skill'."""
        assert validate_skill_id("my_skill") is True

    def test_valid_skill_id_with_dashes(self):
        """Valid: 'skill-name'."""
        assert validate_skill_id("skill-name") is True

    def test_valid_skill_id_alphanumeric_uppercase(self):
        """Valid: 'SKILL_123'."""
        assert validate_skill_id("SKILL_123") is True

    def test_valid_skill_id_mixed_case(self):
        """Valid: 'MySkill_v2.0'."""
        assert validate_skill_id("MySkill_v2.0") is True

    def test_invalid_skill_id_forward_slash(self):
        """Invalid: '../../etc/passwd' (contains /)."""
        assert validate_skill_id("../../etc/passwd") is False

    def test_invalid_skill_id_backslash(self):
        """Invalid: '..\\windows\\system' (contains \\)."""
        assert validate_skill_id("..\\windows\\system") is False

    def test_invalid_skill_id_path_traversal_middle(self):
        """Invalid: 'skill/../dangerous' (contains /)."""
        assert validate_skill_id("skill/../dangerous") is False

    def test_invalid_skill_id_tilde_home(self):
        """Invalid: '~/.corvin' (contains ~)."""
        assert validate_skill_id("~/.corvin") is False

    def test_invalid_skill_id_absolute_path(self):
        """Invalid: '/etc/passwd' (starts with /)."""
        assert validate_skill_id("/etc/passwd") is False

    def test_invalid_skill_id_empty(self):
        """Invalid: '' (empty string)."""
        assert validate_skill_id("") is False

    def test_invalid_skill_id_none(self):
        """Invalid: None."""
        assert validate_skill_id(None) is False

    def test_invalid_skill_id_too_long(self):
        """Invalid: 'x' * 129 (exceeds 128 char limit)."""
        assert validate_skill_id("x" * 129) is False

    def test_invalid_skill_id_special_chars(self):
        """Invalid: 'skill@name' (contains @)."""
        assert validate_skill_id("skill@name") is False

    def test_invalid_skill_id_space(self):
        """Invalid: 'skill name' (contains space)."""
        assert validate_skill_id("skill name") is False

    def test_invalid_skill_id_asterisk(self):
        """Invalid: 'skill*' (contains *)."""
        assert validate_skill_id("skill*") is False

    def test_invalid_skill_id_question_mark(self):
        """Invalid: 'skill?' (contains ?)."""
        assert validate_skill_id("skill?") is False

    def test_valid_skill_id_boundary_1_char(self):
        """Valid: 'x' (1 char, minimum)."""
        assert validate_skill_id("x") is True

    def test_valid_skill_id_boundary_128_char(self):
        """Valid: 'x' * 128 (exactly 128 chars, maximum)."""
        assert validate_skill_id("x" * 128) is True

    def test_invalid_skill_id_non_string(self):
        """Invalid: 123 (not a string)."""
        assert validate_skill_id(123) is False  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Tests: validate_version
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateVersion:
    """Unit tests for validate_version function."""

    def test_valid_version_basic(self):
        """Valid: '1.2.3' (standard semver)."""
        assert validate_version("1.2.3") is True

    def test_valid_version_zeros(self):
        """Valid: '0.0.1' (all zeros allowed)."""
        assert validate_version("0.0.1") is True

    def test_valid_version_large_numbers(self):
        """Valid: '99.99.99' (large numbers)."""
        assert validate_version("99.99.99") is True

    def test_invalid_version_missing_patch(self):
        """Invalid: '1.2' (missing patch version)."""
        assert validate_version("1.2") is False

    def test_invalid_version_too_many_parts(self):
        """Invalid: '1.2.3.4' (too many parts)."""
        assert validate_version("1.2.3.4") is False

    def test_invalid_version_non_numeric_patch(self):
        """Invalid: '1.2.a' (patch is non-numeric)."""
        assert validate_version("1.2.a") is False

    def test_invalid_version_forward_slash(self):
        """Invalid: '../../1.2.3' (contains /)."""
        assert validate_version("../../1.2.3") is False

    def test_invalid_version_backslash(self):
        """Invalid: '..\\1.2.3' (contains \\)."""
        assert validate_version("..\\1.2.3") is False

    def test_invalid_version_v_prefix(self):
        """Invalid: 'v1.2.3' (contains 'v' prefix)."""
        assert validate_version("v1.2.3") is False

    def test_invalid_version_empty(self):
        """Invalid: '' (empty string)."""
        assert validate_version("") is False

    def test_invalid_version_none(self):
        """Invalid: None."""
        assert validate_version(None) is False

    def test_invalid_version_too_long(self):
        """Invalid: 'x' * 129 (exceeds 128 char limit)."""
        assert validate_version("x" * 129) is False

    def test_invalid_version_tilde(self):
        """Invalid: '~1.2.3' (contains ~)."""
        assert validate_version("~1.2.3") is False

    def test_invalid_version_asterisk(self):
        """Invalid: '*.2.3' (contains *)."""
        assert validate_version("*.2.3") is False

    def test_invalid_version_non_string(self):
        """Invalid: 123 (not a string)."""
        assert validate_version(123) is False  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Tests: validate_filename
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateFilename:
    """Unit tests for validate_filename function."""

    def test_valid_filename_basic(self):
        """Valid: 'manifest.json'."""
        assert validate_filename("manifest.json") is True

    def test_valid_filename_with_version(self):
        """Valid: 'skill_v1.2.3.zip'."""
        assert validate_filename("skill_v1.2.3.zip") is True

    def test_valid_filename_readme(self):
        """Valid: 'README.md'."""
        assert validate_filename("README.md") is True

    def test_valid_filename_tar_gz(self):
        """Valid: 'config-backup.tar.gz'."""
        assert validate_filename("config-backup.tar.gz") is True

    def test_invalid_filename_forward_slash(self):
        """Invalid: '../../etc/passwd' (contains /)."""
        assert validate_filename("../../etc/passwd") is False

    def test_invalid_filename_backslash(self):
        """Invalid: '..\\windows\\file' (contains \\)."""
        assert validate_filename("..\\windows\\file") is False

    def test_invalid_filename_tilde(self):
        """Invalid: 'file~backup' (contains ~)."""
        assert validate_filename("file~backup") is False

    def test_invalid_filename_asterisk(self):
        """Invalid: 'file*' (contains *)."""
        assert validate_filename("file*") is False

    def test_invalid_filename_question_mark(self):
        """Invalid: 'file?' (contains ?)."""
        assert validate_filename("file?") is False

    def test_invalid_filename_empty(self):
        """Invalid: '' (empty string)."""
        assert validate_filename("") is False

    def test_invalid_filename_none(self):
        """Invalid: None."""
        assert validate_filename(None) is False

    def test_invalid_filename_too_long(self):
        """Invalid: 'x' * 256 (exceeds 255 char limit)."""
        assert validate_filename("x" * 256) is False


# ─────────────────────────────────────────────────────────────────────────────
# Tests: validate_path_component
# ─────────────────────────────────────────────────────────────────────────────


class TestValidatePathComponent:
    """Unit tests for validate_path_component function."""

    def test_valid_component_basic(self):
        """Valid: 'component'."""
        assert validate_path_component("component") is True

    def test_valid_component_with_dashes(self):
        """Valid: 'my-component'."""
        assert validate_path_component("my-component") is True

    def test_invalid_component_forward_slash(self):
        """Invalid: '../../etc' (contains /)."""
        assert validate_path_component("../../etc") is False

    def test_invalid_component_empty(self):
        """Invalid: '' (empty)."""
        assert validate_path_component("") is False

    def test_valid_component_custom_max_len(self):
        """Valid: 'x' * 50 (within custom max_len=50)."""
        assert validate_path_component("x" * 50, max_len=50) is True

    def test_invalid_component_exceeds_custom_max_len(self):
        """Invalid: 'x' * 51 (exceeds custom max_len=50)."""
        assert validate_path_component("x" * 51, max_len=50) is False


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Route-level security (autonomous_forge_routes.py)
# ─────────────────────────────────────────────────────────────────────────────


class TestAutonomousForgeRouteSecurity:
    """Integration tests for autonomous_forge_routes.py endpoint security."""

    @pytest.fixture
    def mock_session_rec(self):
        """Mock SessionRecord for test routes."""
        rec = MagicMock()
        rec.tenant_id = "_default"
        rec.sid_fingerprint = "test-operator-id"
        return rec

    def test_get_manifest_valid_skill_version(self, mock_session_rec):
        """GET /manifest/{skill_id}/{version} with valid inputs → 200."""
        from core.console.corvin_console.routes.autonomous_forge_routes import get_manifest

        result = get_manifest("os.delegation_router", "1.2.3", mock_session_rec)
        assert result is not None
        assert result.skill_json["id"] == "os.delegation_router"
        assert result.skill_json["version"] == "1.2.3"

    def test_get_manifest_invalid_skill_id_path_traversal(self, mock_session_rec):
        """GET /manifest/{skill_id}/{version} with ../../ escape → 400."""
        from fastapi import HTTPException
        from core.console.corvin_console.routes.autonomous_forge_routes import get_manifest

        with pytest.raises(HTTPException) as exc_info:
            get_manifest("../../etc/passwd", "1.2.3", mock_session_rec)

        assert exc_info.value.status_code == 400
        assert "invalid" in exc_info.value.detail.lower()

    def test_get_manifest_invalid_version_path_traversal(self, mock_session_rec):
        """GET /manifest/{skill_id}/{version} with ../../ escape → 400."""
        from fastapi import HTTPException
        from core.console.corvin_console.routes.autonomous_forge_routes import get_manifest

        with pytest.raises(HTTPException) as exc_info:
            get_manifest("os.delegation_router", "../../1.2.3", mock_session_rec)

        assert exc_info.value.status_code == 400
        assert "invalid" in exc_info.value.detail.lower()

    def test_get_manifest_invalid_skill_id_backslash(self, mock_session_rec):
        """GET /manifest/{skill_id}/{version} with ..\\windows escape → 400."""
        from fastapi import HTTPException
        from core.console.corvin_console.routes.autonomous_forge_routes import get_manifest

        with pytest.raises(HTTPException) as exc_info:
            get_manifest("..\\windows\\system", "1.2.3", mock_session_rec)

        assert exc_info.value.status_code == 400

    def test_get_manifest_invalid_version_backslash(self, mock_session_rec):
        """GET /manifest/{skill_id}/{version} with ..\\windows escape → 400."""
        from fastapi import HTTPException
        from core.console.corvin_console.routes.autonomous_forge_routes import get_manifest

        with pytest.raises(HTTPException) as exc_info:
            get_manifest("os.router", "..\\1.2.3", mock_session_rec)

        assert exc_info.value.status_code == 400

    def test_get_manifest_invalid_version_format(self, mock_session_rec):
        """GET /manifest/{skill_id}/{version} with invalid semver → 400."""
        from fastapi import HTTPException
        from core.console.corvin_console.routes.autonomous_forge_routes import get_manifest

        with pytest.raises(HTTPException) as exc_info:
            get_manifest("os.router", "1.2", mock_session_rec)

        assert exc_info.value.status_code == 400
        assert "semantic" in exc_info.value.detail.lower()

    @patch("core.console.corvin_console.routes.autonomous_forge_routes._validate_and_rotate_csrf")
    @patch("core.console.corvin_console.routes.autonomous_forge_routes.console_audit")
    def test_approve_skill_valid_inputs(self, mock_audit, mock_csrf, mock_session_rec):
        """POST /approve with valid skill_id, version → 200.

        CSRF/nonce validation (Fix #2/#6) is orthogonal to path-traversal
        validation (Fix #4/#5) under test here, so the CSRF gate is mocked
        to 'valid'. The route also calls dataclasses.replace(rec, ...) on
        success (nonce rotation), which requires a real dataclass instance —
        a bare MagicMock raises TypeError there — so this test uses a real
        SessionRecord instead of the generic mock_session_rec fixture.
        """
        import time as _time
        from core.console.corvin_console.auth import SessionRecord
        from core.console.corvin_console.routes.autonomous_forge_routes import approve_skill
        from core.console.corvin_console.api_schemas.autonomous_forge import ApproveRequest

        mock_csrf.return_value = (True, None, "n" * 32)

        now = _time.time()
        real_rec = SessionRecord(
            sid="sid-test",
            sid_fingerprint="test-operator-id",
            tier="free",
            tenant_id="_default",
            token_fingerprint="tok-test",
            csrf_secret="secret-test",
            csrf_nonce="c" * 32,
            csrf_nonce_issued_at=now,
            created_at=now,
            last_seen_at=now,
            expires_at=now + 3600,
        )

        body = ApproveRequest(
            skill_id="os.delegation_router",
            version="1.2.3",
            operator_id="test-operator-id",
            session_token="a" * 64,
            client_nonce="b" * 32,
        )

        result = approve_skill(body, real_rec)
        assert result.status == "approved"
        assert result.rolled_out_at is not None

    @patch("core.console.corvin_console.routes.autonomous_forge_routes._validate_and_rotate_csrf")
    @patch("core.console.corvin_console.routes.autonomous_forge_routes.console_audit")
    def test_approve_skill_invalid_skill_id(self, mock_audit, mock_csrf, mock_session_rec):
        """POST /approve with skill_id="../../etc" → 400."""
        from fastapi import HTTPException
        from core.console.corvin_console.routes.autonomous_forge_routes import approve_skill
        from core.console.corvin_console.api_schemas.autonomous_forge import ApproveRequest

        mock_csrf.return_value = (True, None, "n" * 32)

        body = ApproveRequest(
            skill_id="../../etc/passwd",
            version="1.2.3",
            operator_id="test-operator-id",
            session_token="a" * 64,
            client_nonce="b" * 32,
        )

        with pytest.raises(HTTPException) as exc_info:
            approve_skill(body, mock_session_rec)

        assert exc_info.value.status_code == 400
        assert "invalid" in exc_info.value.detail.lower()

    @patch("core.console.corvin_console.routes.autonomous_forge_routes._validate_and_rotate_csrf")
    @patch("core.console.corvin_console.routes.autonomous_forge_routes.console_audit")
    def test_approve_skill_invalid_version(self, mock_audit, mock_csrf, mock_session_rec):
        """POST /approve with version="../../" → 400."""
        from fastapi import HTTPException
        from core.console.corvin_console.routes.autonomous_forge_routes import approve_skill
        from core.console.corvin_console.api_schemas.autonomous_forge import ApproveRequest

        mock_csrf.return_value = (True, None, "n" * 32)

        body = ApproveRequest(
            skill_id="os.router",
            version="../../",
            operator_id="test-operator-id",
            session_token="a" * 64,
            client_nonce="b" * 32,
        )

        with pytest.raises(HTTPException) as exc_info:
            approve_skill(body, mock_session_rec)

        assert exc_info.value.status_code == 400

    @patch("core.console.corvin_console.routes.autonomous_forge_routes.console_audit")
    def test_defer_skill_invalid_skill_id(self, mock_audit, mock_session_rec):
        """POST /defer with invalid skill_id → 400."""
        from fastapi import HTTPException
        from core.console.corvin_console.routes.autonomous_forge_routes import defer_skill
        from core.console.corvin_console.api_schemas.autonomous_forge import DeferRequest

        body = DeferRequest(
            skill_id="/etc/passwd",
            version="1.2.3",
            operator_id="test-operator-id",
            reason="Testing path traversal",
            session_token="a" * 64,
            client_nonce="b" * 20,
        )

        with pytest.raises(HTTPException) as exc_info:
            defer_skill(body, mock_session_rec)

        assert exc_info.value.status_code == 400

    @patch("core.console.corvin_console.routes.autonomous_forge_routes.console_audit")
    def test_rollback_skill_invalid_skill_id(self, mock_audit, mock_session_rec):
        """POST /rollback with invalid skill_id → 400."""
        from fastapi import HTTPException
        from core.console.corvin_console.routes.autonomous_forge_routes import rollback_skill
        from core.console.corvin_console.api_schemas.autonomous_forge import RollbackRequest

        body = RollbackRequest(
            skill_id="../../windows/system",
            operator_id="test-operator-id",
            reason="Testing path traversal",
            session_token="a" * 64,
            client_nonce="b" * 20,
        )

        with pytest.raises(HTTPException) as exc_info:
            rollback_skill(body, mock_session_rec)

        assert exc_info.value.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
