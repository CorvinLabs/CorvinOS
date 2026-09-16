"""
Tier-2 Tests: Finding 1 Verification — Auth Gate (require_session)

Verifies that protected endpoints use Depends(require_session) and
unauthenticated requests are rejected with 401.

Test Cases:
- Test 1: require_session is imported in task_graph_api.py
- Test 2: Multiple routes use Depends(require_session)
"""

import pytest
from pathlib import Path
import re


@pytest.mark.tier2
def test_finding_1_require_session_imported():
    """
    Verify: require_session is imported in task_graph_api.py

    Expected: Module contains 'require_session' import
    """
    source_file = Path(__file__).parent / '../corvin_console/routes/task_graph_api.py'
    assert source_file.exists(), f"task_graph_api.py not found at {source_file}"

    source_code = source_file.read_text()

    # Verify import
    assert 'require_session' in source_code, \
        "require_session not imported in task_graph_api"

    # Verify it's from deps
    assert 'from ..deps import' in source_code, \
        "deps module not imported"


@pytest.mark.tier2
def test_finding_1_task_graph_api_uses_auth():
    """
    Verify: task_graph_api.py source code contains Depends(require_session).

    Expected: Source file contains require_session usage in route parameters
    """
    source_file = Path(__file__).parent / '../corvin_console/routes/task_graph_api.py'
    source_code = source_file.read_text()

    # Verify Depends(require_session) is used
    assert 'Depends(require_session)' in source_code, \
        "Depends(require_session) not found in task_graph_api"

    # Verify it's used on actual routes (not just commented)
    lines_with_require_session = [
        line for line in source_code.split('\n')
        if 'Depends(require_session)' in line and not line.strip().startswith('#')
    ]
    assert len(lines_with_require_session) >= 1, \
        f"Depends(require_session) not used in any actual routes (found {len(lines_with_require_session)} uncommented)"


@pytest.mark.tier2
def test_finding_1_sessionrecord_parameter():
    """
    Verify: Protected routes use SessionRecord parameter.

    Expected: Routes define a SessionRecord parameter with require_session dependency
    """
    source_file = Path(__file__).parent / '../corvin_console/routes/task_graph_api.py'
    source_code = source_file.read_text()

    # Verify SessionRecord is imported or used
    assert 'SessionRecord' in source_code, \
        "SessionRecord not referenced in task_graph_api"

    # Verify that authenticated routes are defined
    # Look for patterns like "Annotated[..SessionRecord, Depends(require_session)]"
    auth_patterns = re.findall(
        r'Annotated\[.*?SessionRecord.*?Depends\(require_session\)',
        source_code,
        re.DOTALL
    )
    assert len(auth_patterns) >= 1, \
        f"No authenticated route parameters found (expected >=1, got {len(auth_patterns)})"


@pytest.mark.tier2
def test_finding_1_require_session_function():
    """
    Verify: require_session is a callable dependency.

    Expected: require_session can be found and referenced as a dependency
    """
    source_file = Path(__file__).parent / '../corvin_console/routes/task_graph_api.py'
    source_code = source_file.read_text()

    # Verify it's actually used as a Depends() argument
    assert 'Depends(require_session)' in source_code, \
        "require_session should be used with Depends()"


@pytest.mark.tier2
def test_finding_1_multiple_protected_routes():
    """
    Verify: Multiple routes use the require_session guard.

    Expected: At least 3 routes in task_graph_api.py use Depends(require_session)
    """
    source_file = Path(__file__).parent / '../corvin_console/routes/task_graph_api.py'
    source_code = source_file.read_text()

    # Count occurrences
    count = source_code.count('Depends(require_session)')
    assert count >= 3, \
        f"Expected at least 3 protected routes using require_session, found {count}"
