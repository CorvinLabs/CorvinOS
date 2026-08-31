"""Regression test: ensure no orphaned blueprints/routes in console.

An orphaned route is one that is defined but never registered in the Flask/FastAPI app.
This creates maintenance confusion and dead code.
"""

import ast
import re
from pathlib import Path


def test_no_orphaned_blueprints():
    """Verify no Flask blueprints exist that are not registered in app.py."""
    routes_dir = Path(__file__).parent.parent / "routes"
    assert routes_dir.exists(), "routes directory not found"

    # Read app.py to find all imported route modules
    app_file = routes_dir.parent / "app.py"
    app_content = app_file.read_text()

    # Extract imported module names from "from .routes import (...)"
    import_pattern = r"from \.routes import \(([\s\S]*?)\)"
    match = re.search(import_pattern, app_content)
    assert match, "Could not find routes import block in app.py"

    imported_text = match.group(1)
    imported_modules = set()
    for line in imported_text.split("\n"):
        # Handle "module as alias" pattern
        if "import" not in line:  # Skip comment lines
            parts = line.split("as")
            module_name = parts[0].strip().split(",")[-1].strip()
            if module_name and not module_name.startswith("#"):
                imported_modules.add(module_name)

    # Scan routes directory for Python files that define blueprints
    for route_file in routes_dir.glob("*.py"):
        if route_file.name.startswith("_") or route_file.name == "test_":
            continue  # Skip private/test files

        module_name = route_file.stem
        content = route_file.read_text()

        # Check if file defines a Flask blueprint
        if "Blueprint(" in content or "@bp.route" in content or "bp = Blueprint" in content:
            if module_name not in imported_modules:
                raise AssertionError(
                    f"Orphaned blueprint found: {module_name}. "
                    f"It defines a Flask blueprint but is not imported in app.py. "
                    f"Either register it (add to app.py imports) or delete it ({route_file})."
                )


def test_no_vibe_plugins_api_artifact():
    """Verify vibe_plugins_api.py was deleted (it was orphaned with async Flask routes)."""
    routes_dir = Path(__file__).parent.parent / "routes"
    vibe_plugins_file = routes_dir / "vibe_plugins_api.py"

    assert not vibe_plugins_file.exists(), (
        f"vibe_plugins_api.py should be deleted: it was an orphaned Flask blueprint "
        f"with async route handlers (not supported by Flask). If you need this API, "
        f"rewrite it as a FastAPI route module using async def functions."
    )


def test_no_fastapi_routes_reference_vibe_plugins():
    """Verify no FastAPI routes import or reference vibe_plugins_api."""
    routes_dir = Path(__file__).parent.parent / "routes"
    app_file = routes_dir.parent / "app.py"

    content = app_file.read_text()
    assert "vibe_plugins_api" not in content, (
        "app.py should not reference vibe_plugins_api (it's orphaned). "
        "If you need vibe plugin API routes, create a new FastAPI route module."
    )
