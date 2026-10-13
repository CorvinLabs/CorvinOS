"""E2E tests for Update Button feature — Manual + Auto Update.

Proves:
- Manual button: GET /settings/update/check works
- Manual button: POST /settings/update/install triggers script
- Auto-update toggle: GET/PUT /settings/auto-update works
- Script resolution: finds Update.sh or Update.ps1
- Concurrent updates: second call gets 409 Conflict
- Audit events: update.initiated and update.completed logged
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from corvin_console.update_manager import UpdateManager, UpdateNotAllowedError, UpdateFailedError


class TestUpdateScriptResolution:
    """Test that update scripts are found in correct locations."""

    @pytest.mark.asyncio
    async def test_resolve_script_path_user_config(self):
        """Script in ~/.corvin/scripts/ is preferred."""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            script_dir = home / ".corvin" / "scripts"
            script_dir.mkdir(parents=True)
            script = script_dir / "Update.sh"
            script.touch()

            with patch("pathlib.Path.home", return_value=home):
                manager = UpdateManager("_default")
                result = manager._resolve_script_path()
                assert result == script

    @pytest.mark.asyncio
    async def test_resolve_script_path_env_var(self):
        """CORVIN_SCRIPT_DIR env var is fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "Update.sh"
            script_path.touch()

            with patch.dict("os.environ", {"CORVIN_SCRIPT_DIR": tmpdir}):
                with patch("pathlib.Path.home", return_value=Path("/nonexistent")):
                    manager = UpdateManager("_default")
                    result = manager._resolve_script_path()
                    assert result == script_path

    @pytest.mark.asyncio
    async def test_resolve_script_path_not_found(self):
        """Raises UpdateNotAllowedError when script not found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("pathlib.Path.home", return_value=Path(tmpdir)):
                with patch.dict("os.environ", {}, clear=True):
                    manager = UpdateManager("_default")
                    with pytest.raises(UpdateNotAllowedError, match="Update script not found"):
                        manager._resolve_script_path()


class TestUpdateCheck:
    """Test version check (non-blocking, no lock)."""

    @pytest.mark.asyncio
    async def test_check_update_available(self):
        """check_update returns has_update=true when new version available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "Update.sh"
            script.write_text('echo \'{"has_update": true, "current_version": "1.0.0", "available_version": "1.1.0"}\'')
            script.chmod(0o755)

            with patch.object(UpdateManager, "_resolve_script_path", return_value=script):
                manager = UpdateManager("_default")
                result = await manager.check_update()

                assert result.has_update is True
                assert result.current_version == "1.0.0"
                assert result.available_version == "1.1.0"
                assert result.error is None

    @pytest.mark.asyncio
    async def test_check_update_not_found(self):
        """check_update handles missing script gracefully."""
        with patch.object(UpdateManager, "_resolve_script_path", side_effect=UpdateNotAllowedError("test")):
            manager = UpdateManager("_default")
            result = await manager.check_update()

            assert result.has_update is False
            assert result.error is not None


class TestUpdateInstall:
    """Test actual update installation with locking."""

    @pytest.mark.asyncio
    async def test_install_update_success(self):
        """install_update runs script and emits audit events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "Update.sh"
            script.write_text("#!/bin/bash\nexit 0")
            script.chmod(0o755)

            with patch.object(UpdateManager, "_resolve_script_path", return_value=script):
                with patch.object(UpdateManager, "_audit_update") as mock_audit:
                    manager = UpdateManager("_default")
                    result = await manager.run_update()

                    # Audit was called
                    assert mock_audit.call_count >= 2  # initiated + completed

                    # Result is success
                    assert result["success"] is True
                    assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_install_update_failure(self):
        """install_update raises UpdateFailedError on script failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "Update.sh"
            script.write_text("#!/bin/bash\nexit 1")
            script.chmod(0o755)

            with patch.object(UpdateManager, "_resolve_script_path", return_value=script):
                manager = UpdateManager("_default")
                with pytest.raises(UpdateFailedError):
                    await manager.run_update()

    @pytest.mark.asyncio
    async def test_concurrent_update_locked(self):
        """Second update while first running gets 409 Conflict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "Update.sh"
            script.write_text("#!/bin/bash\nsleep 10\nexit 0")
            script.chmod(0o755)

            manager = UpdateManager("_default")

            # Acquire lock
            with patch.object(UpdateManager, "_resolve_script_path", return_value=script):
                with patch.object(UpdateManager, "_run_script_async", side_effect=asyncio.TimeoutError):
                    # Try to acquire while locked
                    manager.lock.acquire()
                    try:
                        with pytest.raises(UpdateNotAllowedError, match="already in progress"):
                            await manager.run_update()
                    finally:
                        manager.lock.release()


def _fake_record(tenant_id: str = "_default"):
    import dataclasses
    from corvin_console import auth as session_auth
    values: dict = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = 1_000_000.0 + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console import deps as console_deps
    from corvin_console.routes import settings as settings_route

    app = FastAPI()
    app.include_router(settings_route.router)
    rec = _fake_record()
    app.dependency_overrides[console_deps.require_session] = lambda: rec
    app.dependency_overrides[console_deps.require_csrf] = lambda: rec
    return TestClient(app)


class TestUpdateApi:
    def test_check_returns_version_info(self, client):
        from corvin_console.update_manager import UpdateCheckResult

        async def fake(self):
            return UpdateCheckResult(True, "1.0.0", "1.1.0", None)

        with patch.object(UpdateManager, "check_update", fake):
            r = client.get("/settings/update/check")
        assert r.status_code == 200
        assert r.json()["available_version"] == "1.1.0"

    def test_install_runs_manager(self, client):
        async def fake(self, emit_progress=None):
            return {"success": True, "exit_code": 0, "final_message": "ok"}

        with patch.object(UpdateManager, "run_update", fake):
            r = client.post("/settings/update/install", json={})
        assert r.status_code == 200
        assert r.json()["success"] is True

    def test_install_conflict_is_409(self, client):
        async def fake(self, emit_progress=None):
            raise UpdateNotAllowedError("Update already in progress")

        with patch.object(UpdateManager, "run_update", fake):
            r = client.post("/settings/update/install", json={})
        assert r.status_code == 409


class TestAutoUpdateDefault:
    def test_default_is_true_without_config_file(self, client, tmp_path):
        missing = tmp_path / "nope" / "config.json"
        with patch("corvin_console.routes.settings._launcher_config_path", return_value=missing):
            r = client.get("/settings/auto-update")
        assert r.status_code == 200
        assert r.json()["enabled"] is True

    def test_default_is_true_when_key_absent(self, client, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text("{}")
        with patch("corvin_console.routes.settings._launcher_config_path", return_value=cfg):
            assert client.get("/settings/auto-update").json()["enabled"] is True

    def test_explicit_false_is_respected_and_roundtrips(self, client, tmp_path):
        cfg = tmp_path / "config.json"
        with patch("corvin_console.routes.settings._launcher_config_path", return_value=cfg):
            assert client.put("/settings/auto-update", json={"enabled": False}).status_code == 200
            assert client.get("/settings/auto-update").json()["enabled"] is False
            assert client.put("/settings/auto-update", json={"enabled": True}).status_code == 200
            assert json.loads(cfg.read_text())["auto_update"] is True
