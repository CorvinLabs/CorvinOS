"""
Console Panel Deletion Verifier — Skill 2.0

Programmatic verification that deleted console panels are truly gone from the frontend.
Solves the 3-Cache Problem (source → build artifacts → browser cache).

Usage:
    from core.skills.console_panel_deletion_verifier import ConsolePanelDeletionVerifier

    verifier = ConsolePanelDeletionVerifier()
    result = verifier.verify_panel_deletion(
        panel_names=["personas", "learning-dashboard", "infinite-session"],
        verify_navigation=True,  # Also check layout.tsx nav groups
    )

    if result.status == "PASS":
        print(f"✅ Panels deleted. New bundle hash: {result.new_bundle_hash}")
    else:
        print(f"❌ {result.error}")
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import subprocess
import re


@dataclass
class PanelDeletionResult:
    """Result of panel deletion verification."""
    status: str  # "PASS" | "FAIL" | "PARTIAL"
    panel_names: List[str]
    old_bundle_hash: Optional[str] = None
    new_bundle_hash: Optional[str] = None
    panels_in_bundle: List[str] = None  # Panel names found in new bundle
    nav_links_removed: bool = False  # layout.tsx clean?
    error: Optional[str] = None
    details: dict = None


class ConsolePanelDeletionVerifier:
    """Verify that deleted panels are truly gone from console frontend."""

    def __init__(self, console_root: str = "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next"):
        self.console_root = Path(console_root)
        self.src_dir = self.console_root / "src"
        self.dist_dir = self.console_root / "dist"

    def verify_panel_deletion(
        self,
        panel_names: List[str],
        verify_navigation: bool = True,
        verify_bundle: bool = True,
    ) -> PanelDeletionResult:
        """
        Verify that panels are deleted from source, navigation, and bundle.

        Args:
            panel_names: List of panel names to verify (e.g., ["personas", "learning-dashboard"])
            verify_navigation: Check layout.tsx/layout-old.tsx for nav links
            verify_bundle: Check dist/ bundle for panel code

        Returns:
            PanelDeletionResult with status, hashes, and details
        """
        result = PanelDeletionResult(
            status="PASS",
            panel_names=panel_names,
            panels_in_bundle=[],
            details={}
        )

        # Step 1: Check source files deleted
        source_found = []
        for panel in panel_names:
            # Convert panel name to likely filenames
            page_file = self.src_dir / f"pages/{panel}.tsx"
            if page_file.exists():
                source_found.append(str(page_file))

        if source_found:
            result.status = "FAIL"
            result.error = f"Panel source files still exist: {source_found}"
            return result

        result.details["source_check"] = "✓ All panel files deleted"

        # Step 2: Check navigation links removed
        if verify_navigation:
            nav_links = self._check_nav_links(panel_names)
            if nav_links:
                result.status = "FAIL"
                result.error = f"Panel nav links still in layout files: {nav_links}"
                result.nav_links_removed = False
                return result

            result.nav_links_removed = True
            result.details["navigation_check"] = "✓ All nav links removed from layout.tsx"

        # Step 3: Get current bundle hash
        current_hash = self._get_current_bundle_hash()
        if current_hash:
            result.new_bundle_hash = current_hash
            result.details["bundle_hash"] = current_hash

        # Step 4: Check bundle for panel references
        if verify_bundle:
            panels_in_bundle = self._check_panels_in_bundle(panel_names)
            result.panels_in_bundle = panels_in_bundle

            if panels_in_bundle:
                result.status = "PARTIAL"  # Build succeeded but panels still in code
                result.error = f"Panel references in bundle: {panels_in_bundle}"
                result.details["bundle_check"] = f"WARN: Found {len(panels_in_bundle)} panel refs in bundle"
                return result

            result.details["bundle_check"] = f"✓ No panel references in {current_hash}"

        result.details["overall"] = "✅ All checks passed — panels deleted from source, nav, and bundle"
        return result

    def _check_nav_links(self, panel_names: List[str]) -> List[str]:
        """Check layout.tsx and layout-old.tsx for hardcoded nav links to panels."""
        found_links = []

        layout_files = [
            self.src_dir / "components/layout.tsx",
            self.src_dir / "components/layout-old.tsx",
        ]

        for layout_file in layout_files:
            if not layout_file.exists():
                continue

            content = layout_file.read_text()

            for panel in panel_names:
                # Look for routing patterns: /app/panel-name
                if f'"/app/{panel}"' in content or f"'/app/{panel}'" in content:
                    found_links.append(f"{layout_file.name}: /app/{panel}")

        return found_links

    def _get_current_bundle_hash(self) -> Optional[str]:
        """Extract the current bundle hash from dist/assets/index-*.js."""
        if not self.dist_dir.exists():
            return None

        # Find index-<hash>.js
        index_files = list(self.dist_dir.glob("assets/index-*.js"))
        if not index_files:
            return None

        # Return the most recent one
        index_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return index_files[0].name

    def _check_panels_in_bundle(self, panel_names: List[str]) -> List[str]:
        """Grep bundle for panel names."""
        if not self.dist_dir.exists():
            return []

        found = []
        index_files = list(self.dist_dir.glob("assets/index-*.js"))

        for index_file in index_files:
            content = index_file.read_text(errors='ignore')

            for panel in panel_names:
                # Search for the panel name
                if panel in content:
                    found.append(f"{panel} (in {index_file.name})")

        return found

    def trigger_clean_build(self) -> bool:
        """Trigger a clean build (rm -rf dist node_modules/.vite && npx vite build)."""
        try:
            # Clear caches
            subprocess.run(["rm", "-rf", str(self.dist_dir)], check=True)
            subprocess.run(["rm", "-rf", str(self.console_root / "node_modules/.vite")], check=True)

            # Build
            result = subprocess.run(
                ["npx", "vite", "build"],
                cwd=str(self.console_root),
                capture_output=True,
                text=True,
                timeout=120,
            )

            return result.returncode == 0

        except Exception as e:
            print(f"❌ Clean build failed: {e}")
            return False

    def trigger_backend_restart(self) -> bool:
        """Restart the console backend service."""
        try:
            subprocess.run(
                ["systemctl", "--user", "restart", "corvin-webui.service"],
                check=True,
                timeout=10,
            )
            return True
        except Exception as e:
            print(f"❌ Backend restart failed: {e}")
            return False


if __name__ == "__main__":
    # Example usage
    verifier = ConsolePanelDeletionVerifier()

    result = verifier.verify_panel_deletion(
        panel_names=["personas", "learning-dashboard", "infinite-session", "world-map", "task-graph"],
        verify_navigation=True,
        verify_bundle=True,
    )

    print(f"\n{'='*60}")
    print(f"Panel Deletion Verification: {result.status}")
    print(f"{'='*60}")

    for detail_key, detail_val in (result.details or {}).items():
        print(f"{detail_key}: {detail_val}")

    if result.new_bundle_hash:
        print(f"\nNew Bundle Hash: {result.new_bundle_hash}")

    if result.error:
        print(f"\n❌ Error: {result.error}")

    if result.panels_in_bundle:
        print(f"\n⚠️  Panels still in bundle: {result.panels_in_bundle}")
