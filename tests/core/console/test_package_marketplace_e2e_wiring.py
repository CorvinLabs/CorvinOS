"""E2E Wiring Proof for PackageMarketplace (ADR-0268 Phase 4)

Verifies that:
1. PackageMarketplace component exists and is reachable from PackagesPage
2. PackagesPage is imported in lazy-pages.ts
3. /app/packages route is registered in panels/registry.tsx and merged into App.tsx
4. Packages link appears in sidebar navigation (layout.tsx)
"""
import re
from pathlib import Path


# The Packages UI (pages/packages.tsx, PackageMarketplace.tsx, its lazy-page
# export, registry route and sidebar link) was deleted in dd93c32c7 together
# with the rest of the old marketplace hub; the six tests that pinned that UI
# were removed with it (2026-11-05 sweep). The mounted backend route module is
# still live and still pinned below.


def test_marketplace_routes_exist():
    """The MOUNTED packages route module must define the marketplace routes.

    ``core/console/routes/packages.py`` (the old target) was an unmounted
    parallel tree — nothing imported it — and was deleted on 2026-09-07; the
    live module is the one ``app.py`` include_router()s."""
    routes_file = (
        Path(__file__).resolve().parents[3]
        / "core" / "console" / "corvin_console" / "routes" / "packages.py"
    )
    assert routes_file.exists(), f"packages.py routes not found at {routes_file}"

    content = routes_file.read_text(encoding="utf-8")

    # Check required routes
    assert "def upload_package" in content or "@packages_bp.route" in content, "Upload route not found"
    assert "def list_packages" in content or "@packages_bp.route" in content, "List route not found"
    assert "def uninstall_package" in content, "Uninstall (DELETE) route not found"


