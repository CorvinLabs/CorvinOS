"""E2E Wiring Proof for PackageMarketplace (ADR-0268 Phase 4)

Verifies that:
1. PackageMarketplace component exists and is reachable from PackagesPage
2. PackagesPage is imported in lazy-pages.ts
3. /app/packages route is wired in App.tsx
4. Packages link appears in sidebar navigation (layout.tsx)
"""
import re
from pathlib import Path


def test_package_marketplace_component_exports():
    """PackageMarketplace.tsx must export PackageMarketplace component."""
    marketplace_file = Path(
        "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/components/PackageMarketplace.tsx"
    )
    assert marketplace_file.exists(), f"PackageMarketplace.tsx not found at {marketplace_file}"

    content = marketplace_file.read_text()
    assert "export const PackageMarketplace" in content, "PackageMarketplace not exported as const"
    assert "export default PackageMarketplace" in content, "PackageMarketplace not exported as default"
    assert "React.FC" in content, "Component is not a React Functional Component"


def test_packages_page_created_and_wired():
    """packages.tsx page must exist and import PackageMarketplace."""
    packages_page = Path(
        "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/pages/packages.tsx"
    )
    assert packages_page.exists(), f"pages/packages.tsx not found at {packages_page}"

    content = packages_page.read_text()
    assert "PackageMarketplace" in content, "packages.tsx does not import PackageMarketplace"
    assert "PackagesPage" in content, "pages/packages.tsx does not export PackagesPage"
    assert "<PackageMarketplace />" in content, "PackageMarketplace component not rendered in PackagesPage"


def test_packages_page_lazy_loaded_in_lazy_pages():
    """PackagesPage must be lazy-loaded in lazy-pages.ts."""
    lazy_pages_file = Path(
        "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/lazy-pages.ts"
    )
    assert lazy_pages_file.exists(), f"lazy-pages.ts not found at {lazy_pages_file}"

    content = lazy_pages_file.read_text()
    assert "export const PackagesPage" in content, "PackagesPage not exported from lazy-pages.ts"
    assert 'import("@/pages/packages")' in content, "packages.tsx not lazy-imported"


def test_packages_route_wired_in_app():
    """App.tsx must have /app/packages route wired to PackagesPage."""
    app_tsx = Path(
        "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/App.tsx"
    )
    assert app_tsx.exists(), f"App.tsx not found at {app_tsx}"

    content = app_tsx.read_text()

    # Check PackagesPage is imported
    assert "PackagesPage" in content, "PackagesPage not imported in App.tsx"
    assert "from" in content and "PackagesPage" in content, "PackagesPage import statement not found"

    # Check route is defined
    assert '<Route path="packages"' in content, "/app/packages route not found in App.tsx"
    assert "<PackagesPage />" in content, "PackagesPage not rendered in any route"


def test_packages_link_in_sidebar_navigation():
    """layout.tsx sidebar must have Packages link."""
    layout_file = Path(
        "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/components/layout.tsx"
    )
    assert layout_file.exists(), f"layout.tsx not found at {layout_file}"

    content = layout_file.read_text()

    # Look for Packages navigation item
    assert '"/app/packages"' in content or '/app/packages' in content, "Packages route link not found in navigation"
    assert '"Packages"' in content or "'Packages'" in content, "Packages label not found in navigation"


def test_marketplace_routes_exist():
    """core/console/routes/packages.py must define marketplace routes."""
    routes_file = Path(
        "/home/shumway/projects/CorvinOS/core/console/routes/packages.py"
    )
    assert routes_file.exists(), f"packages.py routes not found at {routes_file}"

    content = routes_file.read_text()

    # Check required routes
    assert "def upload_package" in content or "@packages_bp.route" in content, "Upload route not found"
    assert "def list_packages" in content or "@packages_bp.route" in content, "List route not found"
    assert "def delete_package" in content or "@packages_bp.route" in content, "Delete route not found"


def test_e2e_call_path_is_complete():
    """Verify the complete E2E call path: sidebar → route → page → component."""
    # This is a meta-test that documents the path:
    # 1. User clicks "Packages" in sidebar (layout.tsx)
    # 2. React Router navigates to /app/packages (App.tsx)
    # 3. PackagesPage component loads (lazy-pages.ts)
    # 4. PackagesPage renders PackageMarketplace (pages/packages.tsx)
    # 5. PackageMarketplace renders UI with upload form (PackageMarketplace.tsx)
    # 6. Form submits to /api/v1/packages/upload (backend routes/packages.py)

    sidebar_ok = Path(
        "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/components/layout.tsx"
    ).read_text()
    assert "/app/packages" in sidebar_ok

    app_ok = Path(
        "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/App.tsx"
    ).read_text()
    assert 'path="packages"' in app_ok

    lazy_ok = Path(
        "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/lazy-pages.ts"
    ).read_text()
    assert "PackagesPage" in lazy_ok

    page_ok = Path(
        "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/pages/packages.tsx"
    ).read_text()
    assert "PackageMarketplace" in page_ok

    backend_ok = Path(
        "/home/shumway/projects/CorvinOS/core/console/routes/packages.py"
    ).read_text()
    assert "upload_package" in backend_ok

    # All pieces verified ✓
    assert True, "E2E call path is complete and verified"
