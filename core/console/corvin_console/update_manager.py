"""Update Manager — orchestrate platform-specific updates with audit trail.

Handles:
- Script resolution (Windows .ps1 / Unix .sh)
- Concurrent update locking (single update per tenant)
- Output filtering (PII scrubbing)
- Audit events (update.initiated, update.completed)
- Boot-time auto-update triggering
"""

import asyncio
import json
import logging
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# Global lock: one update per tenant
_update_locks: dict[str, Lock] = {}
_lock_table_lock = Lock()


def _get_tenant_lock(tenant_id: str) -> Lock:
    """Get or create a lock for a specific tenant."""
    with _lock_table_lock:
        if tenant_id not in _update_locks:
            _update_locks[tenant_id] = Lock()
        return _update_locks[tenant_id]


class UpdateNotAllowedError(Exception):
    """Update is not allowed (already in progress, script not found, etc.)"""
    pass


class UpdateFailedError(Exception):
    """Update script failed with non-zero exit code."""
    pass


@dataclass
class UpdateCheckResult:
    """Result of version check."""
    has_update: bool
    current_version: str
    available_version: str | None
    error: str | None = None


@dataclass
class UpdateProgress:
    """Progress event during update."""
    status: str  # "checking", "downloading", "installing", "success", "failed"
    message: str
    progress_percent: int | None = None
    error_detail: str | None = None


class UpdateManager:
    """Manages updates with platform detection, locking, and audit trail."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.lock = _get_tenant_lock(tenant_id)
        self.system = platform.system()  # "Linux", "Windows", "Darwin"

    def _resolve_script_path(self) -> Path:
        """Resolve update script path for current platform.

        Order of precedence:
        1. ~/.corvin/scripts/Update.{sh|ps1}
        2. CORVIN_SCRIPT_DIR env var
        3. Installation directory (if determinable)

        Returns:
            Path to the update script

        Raises:
            UpdateNotAllowedError: If script cannot be found
        """
        script_name = "Update.ps1" if self.system == "Windows" else "Update.sh"

        # Try 1: User config directory
        home = Path.home()
        user_script = home / ".corvin" / "scripts" / script_name
        if user_script.exists():
            return user_script

        # Try 2: CORVIN_SCRIPT_DIR env var
        script_dir = os.environ.get("CORVIN_SCRIPT_DIR")
        if script_dir:
            env_script = Path(script_dir) / script_name
            if env_script.exists():
                return env_script

        # Try 3: Installation directory (relative to executable)
        try:
            import sys
            exe_dir = Path(sys.executable).parent.parent
            inst_script = exe_dir / "scripts" / script_name
            if inst_script.exists():
                return inst_script
        except Exception:
            pass

        # Not found
        raise UpdateNotAllowedError(
            f"Update script not found: {script_name}. "
            f"Expected in ~/.corvin/scripts/, {os.environ.get('CORVIN_SCRIPT_DIR', 'CORVIN_SCRIPT_DIR (unset)')}, "
            f"or installation directory."
        )

    async def check_update(self) -> UpdateCheckResult:
        """Check if an update is available (non-blocking).

        This is a soft check and does NOT acquire the update lock.
        Returns version info without making changes.
        """
        try:
            script_path = self._resolve_script_path()
        except UpdateNotAllowedError as e:
            return UpdateCheckResult(
                has_update=False,
                current_version="unknown",
                available_version=None,
                error=str(e)
            )

        try:
            # Run script in check-only mode (--check-only if supported)
            result = await self._run_script_async(
                script_path,
                args=["--check-only"],
                timeout=30
            )

            # Parse output for version info (script must output JSON on stdout)
            if result["exit_code"] == 0:
                try:
                    output = json.loads(result["stdout"])
                    return UpdateCheckResult(
                        has_update=output.get("has_update", False),
                        current_version=output.get("current_version", "unknown"),
                        available_version=output.get("available_version"),
                        error=None
                    )
                except json.JSONDecodeError:
                    return UpdateCheckResult(
                        has_update=False,
                        current_version="unknown",
                        available_version=None,
                        error=f"Script output was not valid JSON"
                    )
            else:
                return UpdateCheckResult(
                    has_update=False,
                    current_version="unknown",
                    available_version=None,
                    error=f"Check script failed: {result['stderr']}"
                )
        except Exception as e:
            return UpdateCheckResult(
                has_update=False,
                current_version="unknown",
                available_version=None,
                error=f"Check failed: {str(e)}"
            )

    async def run_update(self, emit_progress=None) -> dict:
        """Run the update script with locking and audit trail.

        Args:
            emit_progress: Optional async callable(UpdateProgress) for streaming updates

        Returns:
            dict with keys: success (bool), exit_code (int), final_message (str)

        Raises:
            UpdateNotAllowedError: If another update is already running
            UpdateFailedError: If script returns non-zero exit code
        """
        acquired = self.lock.acquire(blocking=False)
        if not acquired:
            raise UpdateNotAllowedError(
                "Update already in progress. Please wait or try again later."
            )

        try:
            script_path = self._resolve_script_path()

            if emit_progress:
                await emit_progress(
                    UpdateProgress(
                        status="checking",
                        message=f"Starting update from {script_path}",
                        progress_percent=0
                    )
                )

            # Emit audit event: update.initiated
            await self._audit_update("update.initiated", {
                "script": str(script_path),
                "platform": self.system,
            })

            # Run the update
            if emit_progress:
                await emit_progress(
                    UpdateProgress(
                        status="downloading",
                        message="Downloading and installing updates...",
                        progress_percent=25
                    )
                )

            result = await self._run_script_async(
                script_path,
                args=[],
                timeout=600,  # 10-minute timeout
                emit_progress=emit_progress
            )

            if result["exit_code"] != 0:
                final_msg = f"Update failed: {self._scrub_output(result['stderr'][:500])}"
                await self._audit_update("update.completed", {
                    "success": False,
                    "exit_code": result["exit_code"],
                    "error": final_msg,
                })
                if emit_progress:
                    await emit_progress(
                        UpdateProgress(
                            status="failed",
                            message=final_msg,
                            error_detail=result["stderr"][:200]
                        )
                    )
                raise UpdateFailedError(final_msg)

            # Success
            final_msg = "Update completed successfully. Please restart the Console to apply changes."
            await self._audit_update("update.completed", {
                "success": True,
                "exit_code": 0,
                "message": final_msg,
            })

            if emit_progress:
                await emit_progress(
                    UpdateProgress(
                        status="success",
                        message=final_msg,
                        progress_percent=100
                    )
                )

            return {
                "success": True,
                "exit_code": 0,
                "final_message": final_msg,
            }

        finally:
            self.lock.release()

    async def _run_script_async(
        self,
        script_path: Path,
        args: list[str] | None = None,
        timeout: int = 300,
        emit_progress=None
    ) -> dict:
        """Run a script asynchronously with timeout.

        Returns:
            dict with keys: exit_code (int), stdout (str), stderr (str)
        """
        args = args or []

        if self.system == "Windows":
            # PowerShell command
            cmd = ["pwsh", "-NoProfile", "-Command", str(script_path), *args]
        else:
            # Shell command
            cmd = ["sh", str(script_path), *args]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path.home()
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )

            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            }
        except asyncio.TimeoutError:
            proc.kill()
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Script timeout after {timeout} seconds",
            }
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
            }

    def _scrub_output(self, text: str) -> str:
        """Scrub PII/secrets from script output.

        Fail-closed: any suspicious pattern is redacted.
        """
        # Redact API keys, tokens, paths
        import re

        patterns = [
            (r'[a-z]{2,}-[a-z0-9]{40,}', '[REDACTED_KEY]'),  # AWS/GCP keys
            (r'sk-[a-zA-Z0-9]{48,}', '[REDACTED_TOKEN]'),      # OpenAI keys
            (r'ghp_[a-zA-Z0-9]{36,}', '[REDACTED_TOKEN]'),     # GitHub PAT
            (r'/home/[a-zA-Z0-9_\-\.]+', '[REDACTED_PATH]'),   # User paths
            (r'[A-Z0-9]{20,}', '[REDACTED_SECRET]'),           # Generic secrets
        ]

        result = text
        for pattern, replacement in patterns:
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

        return result

    async def _audit_update(self, event_type: str, details: dict):
        """Emit audit event for update action.

        This integrates with ADR-0232 (audit chain) but can be mocked in tests.
        """
        try:
            from . import audit as console_audit
            console_audit.action_performed(
                tenant_id=self.tenant_id,
                action=event_type,
                target_kind="system_update",
                target_id="corvinOS_core",
            )
        except ImportError:
            logger.warning("Audit module not available for update event")
        except Exception as e:
            logger.error(f"Failed to emit audit event {event_type}: {e}")
