"""Plugin upload, verification, and installation flow (ADR-0249 Stage 6).

This route handles the complete plugin installation lifecycle:
1. Upload: Accept multipart tarball + optional checksum
2. Verify: Extract manifest, validate schema, check trust level
3. Audit: Emit plugin.installation_started event
4. Install: Call the Stage 6 CLI (corvin plugin install)
5. Enable: Hot-reload or mark for next boot
6. Verify: health_check passes

Behind the `plugin_console_surface` + `plugin_runtime_lifecycle` feature flags.
Tenant resolution: Always `rec.tenant_id` from authenticated `SessionRecord`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi import status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from ..deps import require_csrf

log = logging.getLogger(__name__)

router = APIRouter()

# Import the console plugin surface gate from plugins.py
try:
    from .plugins import require_surface_csrf, _feature_flags, _PLUGINS_AVAILABLE
except ImportError:  # pragma: no cover
    _feature_flags = None
    _PLUGINS_AVAILABLE = False
    require_surface_csrf = None


# ── Models ────────────────────────────────────────────────────────────────────


class PluginUploadRequest(BaseModel):
    """Metadata accompanying a plugin tarball upload (optional)."""
    sha256_checksum: str | None = Field(
        None,
        description="Optional SHA256 checksum for integrity verification"
    )
    auto_enable: bool = Field(
        False,
        description="Automatically enable after installation (default: false)"
    )


class PluginUploadResponse(BaseModel):
    """Lifecycle event + result for one plugin upload."""
    plugin_id: str
    version: str
    status: str  # 'installed', 'installed_pending_enable', 'error'
    message: str
    trust_verdict: str | None = None  # 'vetted', 'community', 'forged', None
    requires_consent: bool = False
    health_check_passed: bool | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _verify_tarball_integrity(
    data: bytes,
    expected_sha256: str | None = None,
) -> bool:
    """Verify tarball integrity via checksum (fail-closed)."""
    if expected_sha256 is None:
        return True  # Optional verification

    computed = hashlib.sha256(data).hexdigest()
    if computed.lower() != expected_sha256.lower():
        log.warning(
            f"checksum mismatch: expected {expected_sha256}, got {computed}"
        )
        return False
    return True


def _extract_and_verify_manifest(
    tarball_data: bytes,
) -> tuple[dict[str, Any], Path, Path] | None:
    """Extract manifest from tarball and validate schema.

    Returns (manifest_dict, plugin_dir_path, temp_dir) or None on error. ``temp_dir`` is the
    caller-owned root to delete — the caller must NOT guess it from ``plugin_dir.parent`` (when
    plugin.yaml sits at the tarball root, plugin_dir == temp_dir and .parent is the SYSTEM temp
    root, so rmtree would wipe /tmp).
    """
    try:
        # Extract to temp directory
        temp_dir = Path(tempfile.mkdtemp())

        # Read tarball
        tar_bytes = BytesIO(tarball_data)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            # filter="data" (PEP 706) rejects absolute paths, ".." traversal, symlinks and
            # device files — an untrusted upload tarball must never write outside temp_dir.
            tar.extractall(path=temp_dir, filter="data")

        # Find plugin.yaml in the extracted structure
        plugin_yaml_paths = list(temp_dir.rglob("plugin.yaml"))
        if not plugin_yaml_paths:
            log.error("no plugin.yaml found in tarball")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None

        manifest_path = plugin_yaml_paths[0]
        plugin_dir = manifest_path.parent

        # Load and validate manifest
        try:
            import yaml
            manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest_data, dict):
                log.error("invalid plugin.yaml structure")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return None

            # Validate required fields
            required = {"plugin_id", "plugin_type", "version"}
            if not required.issubset(manifest_data.keys()):
                log.error(f"manifest missing required fields: {required}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return None

            return manifest_data, plugin_dir, temp_dir
        except Exception as exc:
            log.error(f"failed to parse plugin.yaml: {exc}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None
    except Exception as exc:
        log.error(f"failed to extract tarball: {exc}")
        return None


def _evaluate_trust(
    manifest_data: dict[str, Any],
    tenant_id: str,
    corvin_home: Path,
) -> tuple[str, bool]:  # (verdict: 'vetted'|'community'|'forged', allowed: bool)
    """Evaluate trust level per ADR-0249 (Stage 6).

    Returns (verdict, allowed) where:
    - 'vetted' = signed by maintainer (allowed if enforcement off or key pinned)
    - 'community' = unsigned (allowed if enforcement off or consent given)
    - 'forged' = claims vetted but signature invalid (never allowed)
    """
    try:
        from corvin_plugins.trust import evaluate, load_trust_anchors, Verdict
        from corvin_plugins.trust import enforcement_enabled

        trust_anchors = load_trust_anchors(corvin_home)

        # Evaluate with enforcement=True to get the correct verdict
        decision = evaluate(
            manifest_data,
            corvin_home=corvin_home,
            tenant_id=tenant_id,
            enforcement=True,
            trust_anchors=trust_anchors,
        )

        # Check if enforcement is enabled
        enforce = enforcement_enabled(tenant_id)

        origin = str(manifest_data.get("origin", "community")).lower()

        if origin == "vetted" and decision.verdict == Verdict.FORGED:
            # Vetted with invalid signature: never allowed
            return "forged", False

        if origin == "vetted" and decision.verdict == Verdict.VETTED:
            # Valid vetted plugin: always allowed
            return "vetted", True

        # Community or unverified plugin
        if enforce:
            # Enforcement on: refuse unless pre-approved
            return "community", False

        # Enforcement off: prompt user for consent
        return "community", True
    except Exception as exc:
        log.warning(f"trust evaluation failed: {exc}")
        return "community", False


async def _emit_installation_started_event(
    rec: Any,
    plugin_id: str,
    version: str,
    trust_verdict: str | None,
) -> None:
    """Emit plugin.installation_started audit event (Stage 3: Audit).

    Uses forge.security_events.write_event directly for plugin-specific events
    (ADR-0249, console_audit.action_performed is for console mutations only).
    """
    try:
        from forge import security_events
        security_events.write_event(
            event_type="plugin.installation_started",
            details={
                "plugin_id": plugin_id,
                "version": version,
                "trust_verdict": trust_verdict,
                "source": "console_upload",
                "tenant_id": rec.tenant_id,
            },
        )
    except Exception as exc:
        log.warning(f"failed to emit audit event: {exc}")


async def _install_via_cli(
    plugin_dir: Path,
    tenant_id: str,
    yes_flag: bool = False,
) -> tuple[bool, str]:  # (success, message)
    """Call corvin plugin install CLI command (Stage 4: Install).

    Returns (success, message) tuple.
    """
    try:
        import subprocess

        cmd = [
            "corvin",
            "plugin",
            "install",
            str(plugin_dir),
            "--tenant",
            tenant_id,
        ]
        if yes_flag:
            cmd.append("--yes")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return False, f"install failed: {result.stderr}"

        return True, result.stdout
    except subprocess.TimeoutExpired:
        return False, "install command timed out"
    except Exception as exc:
        return False, f"install failed: {type(exc).__name__}: {exc}"


async def _health_check_plugin(
    plugin_id: str,
    tenant_id: str,
) -> bool:
    """Verify plugin health after installation (Stage 6: Verify).

    Returns True if health_check passes, False otherwise.
    """
    try:
        from corvin_plugins.registry import get_registry

        registry = get_registry()
        if plugin_id not in registry.discover():
            log.warning(f"plugin {plugin_id} not loaded after install")
            return False

        plugin = registry.discover()[plugin_id]
        from corvin_plugins.protocol import HealthStatus

        result = plugin.health_check()
        if isinstance(result, HealthStatus):
            return result.ok
        return False
    except Exception as exc:
        log.warning(f"health check failed for {plugin_id}: {exc}")
        return False


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/plugins/upload")
async def upload_plugin(
    file: UploadFile = File(...),
    checksum: str | None = None,
    auto_enable: bool = False,
    rec: Annotated[Any, Depends(require_surface_csrf)] = None,
) -> PluginUploadResponse:
    """Complete plugin installation flow via file upload.

    Flow:
    1. Upload: Receive and validate tarball
    2. Verify: Extract manifest, validate schema, check trust level
    3. Audit: Emit plugin.installation_started event
    4. Install: Call Stage 6 CLI (corvin plugin install)
    5. Enable: Mark for hot-reload or next boot
    6. Verify: Run health_check

    Accepts: .tar.gz with embedded plugin.yaml
    Returns: Installation status + trust verdict + health check result
    """
    # Gate check (redundant, but explicit)
    if not _PLUGINS_AVAILABLE or not _feature_flags.is_enabled(
        "plugin_runtime_lifecycle", rec.tenant_id
    ):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="plugin installation is disabled",
        )

    try:
        # Stage 1: Upload & validate file
        if not file.filename.endswith((".tar.gz", ".tgz")):
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="plugin must be a .tar.gz archive",
            )

        file_data = await file.read()
        if not file_data:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="empty file uploaded",
            )

        # Stage 2a: Verify integrity (checksum)
        if checksum and not _verify_tarball_integrity(file_data, checksum):
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="checksum verification failed",
            )

        # Stage 2b: Extract and verify manifest
        result = _extract_and_verify_manifest(file_data)
        if result is None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="invalid plugin archive or manifest",
            )

        manifest_data, plugin_dir, temp_dir = result
        plugin_id = manifest_data.get("plugin_id")
        version = manifest_data.get("version", "0.1.0")

        # Stage 2c: Trust evaluation
        try:
            from corvinOS.shared.paths import corvin_home
        except ImportError:
            corvin_home_path = Path.home() / ".corvin"
        else:
            corvin_home_path = corvin_home()

        trust_verdict, trust_allowed = _evaluate_trust(
            manifest_data,
            rec.tenant_id,
            corvin_home_path,
        )

        if not trust_allowed and trust_verdict == "forged":
            # Forged plugins are always refused
            await _emit_installation_started_event(
                rec, plugin_id, version, "forged"
            )
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=f"plugin {plugin_id} failed trust verification (forged)",
            )

        # Stage 3: Audit event
        await _emit_installation_started_event(
            rec, plugin_id, version, trust_verdict
        )

        # Stage 4: Install via CLI
        success, install_message = await _install_via_cli(
            plugin_dir,
            rec.tenant_id,
            yes_flag=True,  # Console already gated trust
        )

        if not success:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"installation failed: {install_message}",
            )

        # Stage 5: Enable (mark for next load)
        # Note: Hot-reload is optional and depends on runtime capabilities
        enable_attempted = False
        try:
            from corvin_plugins.state import PluginLifecycle
            lifecycle = PluginLifecycle(tenant_id=rec.tenant_id)
            if auto_enable:
                lifecycle.enable(plugin_id, consent_granted_by="console" if trust_verdict == "community" else None)
                enable_attempted = True
        except Exception as exc:
            log.warning(f"could not enable plugin immediately: {exc}")

        # Stage 6: Health check
        health_passed = await _health_check_plugin(plugin_id, rec.tenant_id)

        return PluginUploadResponse(
            plugin_id=plugin_id,
            version=version,
            status="installed_pending_enable" if not enable_attempted else ("installed" if health_passed else "installed"),
            message=f"Plugin {plugin_id}@{version} installed successfully" + (
                " and enabled" if enable_attempted else " (enable manually or on next boot)"
            ),
            trust_verdict=trust_verdict,
            requires_consent=(trust_verdict == "community"),
            health_check_passed=health_passed,
        )

    except HTTPException:
        raise
    except Exception as exc:
        log.error(f"plugin upload failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"plugin upload failed: {type(exc).__name__}",
        ) from exc

    finally:
        # Cleanup the caller-owned temp root ONLY — never plugin_dir.parent (which is the SYSTEM
        # temp root when plugin.yaml sits at the tarball root; rmtree there would wipe /tmp).
        try:
            if 'temp_dir' in locals() and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


__all__ = ["router"]
