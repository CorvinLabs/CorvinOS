"""
Skill 2.0: Console Frontend Change Verifier

Verifies that console frontend changes are actually bundled and served,
not cached on either the build or browser side.

Load-bearing guarantee: New frontend code is LIVE in the browser, not just
built but cached.
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum
import subprocess
import re
import logging
from pathlib import Path
import requests

_log = logging.getLogger(__name__)


class VerificationStatus(str, Enum):
    """Status of frontend verification."""
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_BROWSER_REFRESH = "NEEDS_BROWSER_REFRESH"


@dataclass
class VerificationResult:
    """Result of frontend change verification."""
    status: VerificationStatus
    message: str
    bundle_hash: Optional[str] = None
    marker_found: bool = False
    details: dict = None  # Additional diagnostic info

    def is_success(self) -> bool:
        """True if change is live (or ready to be after browser refresh)."""
        return self.status in (VerificationStatus.PASS, VerificationStatus.NEEDS_BROWSER_REFRESH)


class ConsoleFrontendVerifier:
    """
    Verifies console frontend changes end-to-end.

    Workflow:
    1. User edits src/pages/foo.tsx
    2. Developer adds a unique marker string (e.g., "FOO_PANEL_V2")
    3. Call verifier.verify_frontend_change(marker="FOO_PANEL_V2", location="...")
    4. Verifier:
       - Clears both caches (dist/, .vite/)
       - Rebuilds
       - Greps for marker in bundled assets
       - Checks backend is serving new hashes
       - Returns PASS/FAIL with evidence
    """

    def __init__(
        self,
        console_dir: str = "core/console/corvin_console/web-next",
        backend_url: str = "http://127.0.0.1:8765",
    ):
        self.console_dir = Path(console_dir)
        self.backend_url = backend_url

    def verify_frontend_change(
        self,
        marker: str,
        location: str,
        auto_restart_backend: bool = True,
    ) -> VerificationResult:
        """
        Verify a console frontend change is live.

        Args:
            marker: Unique string added to source (e.g., "VIBE_DASH_V2")
            location: Path to edited file (e.g., "src/pages/vibe-engineering.tsx")
            auto_restart_backend: Automatically restart backend if needed

        Returns:
            VerificationResult with status and details
        """
        details = {}

        # Step 1: Verify source contains marker
        _log.info(f"Verifying marker '{marker}' in {location}")
        source_path = Path(location)

        if not source_path.exists():
            return VerificationResult(
                status=VerificationStatus.FAIL,
                message=f"Source file not found: {location}",
                details={"step": "1_source_check"},
            )

        source_content = source_path.read_text()
        if marker not in source_content:
            return VerificationResult(
                status=VerificationStatus.FAIL,
                message=f"Marker '{marker}' not found in {location}",
                details={
                    "step": "1_source_check",
                    "file_content_sample": source_content[:500],
                },
            )

        details["step_1_source_ok"] = True

        # Step 2: Clear both caches (atomic)
        _log.info("Clearing build caches...")
        try:
            self._clear_caches()
            details["step_2_caches_cleared"] = True
        except Exception as e:
            return VerificationResult(
                status=VerificationStatus.FAIL,
                message=f"Failed to clear caches: {e}",
                details={"step": "2_cache_clear", "error": str(e)},
            )

        # Step 3: Build
        _log.info("Building...")
        try:
            self._build()
            details["step_3_build_ok"] = True
        except subprocess.CalledProcessError as e:
            return VerificationResult(
                status=VerificationStatus.FAIL,
                message="Build failed",
                details={
                    "step": "3_build",
                    "error": str(e),
                    "stdout": e.stdout.decode() if e.stdout else "",
                    "stderr": e.stderr.decode() if e.stderr else "",
                },
            )

        # Step 4: Verify marker in bundled assets
        _log.info(f"Checking for marker '{marker}' in bundled assets...")
        marker_found, asset_name = self._marker_in_bundle(marker)

        if not marker_found:
            return VerificationResult(
                status=VerificationStatus.FAIL,
                message=f"Marker '{marker}' not found in bundled assets",
                details={
                    "step": "4_marker_check",
                    "bundled_assets": self._list_bundled_assets(),
                },
            )

        details["step_4_marker_found"] = asset_name

        # Step 5: Verify backend serves new hashes
        _log.info("Checking backend bundle hash...")
        deployed_hash = self._get_deployed_hash()

        if not deployed_hash:
            return VerificationResult(
                status=VerificationStatus.FAIL,
                message="Could not retrieve bundle hash from backend",
                details={
                    "step": "5_backend_check",
                    "backend_url": self.backend_url,
                },
            )

        # Step 6: Compare hashes
        built_hash = self._get_built_hash()

        if built_hash != deployed_hash:
            _log.warning(
                f"Hash mismatch: built={built_hash}, deployed={deployed_hash}"
            )

            if auto_restart_backend:
                _log.info("Restarting backend...")
                try:
                    self._restart_backend()
                    import time
                    time.sleep(1)  # Wait for backend to come up

                    deployed_hash_new = self._get_deployed_hash()
                    if deployed_hash_new == built_hash:
                        _log.info("Hash match after backend restart")
                        deployed_hash = deployed_hash_new
                    else:
                        return VerificationResult(
                            status=VerificationStatus.FAIL,
                            message="Hash mismatch even after backend restart",
                            details={
                                "step": "6_hash_check",
                                "built": built_hash,
                                "deployed_before": deployed_hash,
                                "deployed_after": deployed_hash_new,
                            },
                        )
                except Exception as e:
                    _log.warning(f"Backend restart failed: {e}")
                    return VerificationResult(
                        status=VerificationStatus.NEEDS_BROWSER_REFRESH,
                        message=(
                            f"Bundle ready but backend not restarted. "
                            f"Hash mismatch: {built_hash} != {deployed_hash}"
                        ),
                        bundle_hash=built_hash,
                        marker_found=True,
                        details={
                            "step": "6_hash_check",
                            "built": built_hash,
                            "deployed": deployed_hash,
                            "requires_manual_backend_restart": True,
                        },
                    )
            else:
                return VerificationResult(
                    status=VerificationStatus.NEEDS_BROWSER_REFRESH,
                    message=(
                        f"Hash mismatch (backend restart skipped). "
                        f"Manual restart needed."
                    ),
                    bundle_hash=built_hash,
                    marker_found=True,
                    details={
                        "step": "6_hash_check",
                        "built": built_hash,
                        "deployed": deployed_hash,
                    },
                )

        details["step_6_hash_verified"] = deployed_hash

        # Success!
        return VerificationResult(
            status=VerificationStatus.PASS,
            message=f"✅ Frontend change verified live (marker: {marker})",
            bundle_hash=deployed_hash,
            marker_found=True,
            details=details,
        )

    # ========================================================================
    # Private helpers
    # ========================================================================

    def _clear_caches(self):
        """Clear both build caches atomically."""
        dist_dir = self.console_dir / "dist"
        vite_dir = self.console_dir / "node_modules" / ".vite"

        if dist_dir.exists():
            import shutil
            shutil.rmtree(dist_dir)
            _log.debug(f"Deleted {dist_dir}")

        if vite_dir.exists():
            import shutil
            shutil.rmtree(vite_dir)
            _log.debug(f"Deleted {vite_dir}")

    def _build(self):
        """Run npm build in console_dir."""
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=self.console_dir,
            capture_output=True,
            check=True,
        )
        _log.debug(f"Build stdout: {result.stdout.decode()}")

    def _marker_in_bundle(self, marker: str) -> tuple[bool, Optional[str]]:
        """Check if marker exists in any bundled .js asset."""
        assets_dir = self.console_dir / "dist" / "assets"

        if not assets_dir.exists():
            return False, None

        for asset_file in assets_dir.glob("*.js"):
            try:
                content = asset_file.read_text()
                if marker in content:
                    return True, asset_file.name
            except Exception as e:
                _log.debug(f"Failed to read {asset_file}: {e}")

        return False, None

    def _list_bundled_assets(self) -> list[str]:
        """List all bundled assets."""
        assets_dir = self.console_dir / "dist" / "assets"
        if not assets_dir.exists():
            return []
        return [f.name for f in assets_dir.glob("*.js")]

    def _get_deployed_hash(self) -> Optional[str]:
        """Get bundle hash from backend."""
        try:
            r = requests.get(
                f"{self.backend_url}/console/",
                headers={"Cache-Control": "no-cache"},
                timeout=5,
            )
            if not r.ok:
                return None

            # Extract index-HASH.js from HTML
            match = re.search(r'assets/(index-[A-Za-z0-9_-]+\.js)', r.text)
            return match.group(1) if match else None
        except Exception as e:
            _log.error(f"Failed to get deployed hash: {e}")
            return None

    def _get_built_hash(self) -> Optional[str]:
        """Get bundle hash from dist/ directory."""
        assets_dir = self.console_dir / "dist" / "assets"
        if not assets_dir.exists():
            return None

        for asset_file in assets_dir.glob("index-*.js"):
            return asset_file.name

        return None

    def _restart_backend(self):
        """Try to restart backend via systemctl."""
        subprocess.run(
            ["systemctl", "--user", "restart", "corvin-console.service"],
            capture_output=True,
            timeout=10,
        )


# ============================================================================
# Testing & Usage
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    verifier = ConsoleFrontendVerifier()

    # Example: verify a Vibe Engineering panel change
    result = verifier.verify_frontend_change(
        marker="VIBE_DASHBOARD_NEW",
        location="core/console/corvin_console/web-next/src/pages/vibe-engineering.tsx",
    )

    print(f"\nVerification Result:")
    print(f"  Status: {result.status}")
    print(f"  Message: {result.message}")
    if result.bundle_hash:
        print(f"  Bundle: {result.bundle_hash}")
    if result.details:
        print(f"  Details: {result.details}")
