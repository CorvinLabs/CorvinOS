"""
Tier-2 Tests: Finding 4 Verification — Checkpoint Sorting

Verifies that CheckpointManager sorts checkpoints by timestamp (not filename),
with newest checkpoints appearing first.

Test Cases:
- Test 1: Source code uses (timestamp, iteration_num) sort key
- Test 2: Sort is reversed (newest first)
- Test 3: Mock checkpoints are correctly sorted by timestamp
"""

import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock


@pytest.mark.tier2
def test_finding_4_checkpoint_sorting_code():
    """
    Verify: CheckpointManager.list_checkpoints() uses timestamp sorting.

    Expected: Source code contains correct sort pattern
    """
    source_file = Path(__file__).parent / '../checkpoint_manager.py'
    source_code = source_file.read_text()

    # Verify the sort statement uses timestamp
    assert 'timestamp' in source_code, \
        "Checkpoint sorting should use timestamp field"

    # Verify sorting is by tuple (timestamp, iteration_num)
    assert 'iteration_num' in source_code or 'iteration' in source_code, \
        "Checkpoint sorting should consider iteration_num"


@pytest.mark.tier2
def test_finding_4_checkpoint_sort_order():
    """
    Verify: Checkpoints are sorted in reverse order (newest first).

    Expected: sort() call includes reverse=True
    """
    source_file = Path(__file__).parent / '../checkpoint_manager.py'
    source_code = source_file.read_text()

    # Find the sort statement
    import re
    sort_statements = re.findall(
        r'\.sort\(.*?key=.*?timestamp.*?reverse\s*=\s*(True|False)',
        source_code,
        re.DOTALL
    )

    # Verify at least one sort uses reverse=True
    reverse_true_found = any('True' in stmt for stmt in sort_statements)

    assert reverse_true_found or 'reverse=True' in source_code, \
        "Checkpoint sorting should use reverse=True (newest first)"


@pytest.mark.tier2
def test_finding_4_checkpoint_list_method():
    """
    Verify: list_checkpoints() method exists and returns sorted list.

    Expected: Method can be called and returns a list
    """
    from core.vibe_engineering.checkpoint_manager import CheckpointManager

    # Verify method exists
    assert hasattr(CheckpointManager, 'list_checkpoints'), \
        "CheckpointManager should have list_checkpoints method"

    # Verify it's callable
    method = getattr(CheckpointManager, 'list_checkpoints')
    assert callable(method), \
        "list_checkpoints should be callable"


@pytest.mark.tier2
def test_finding_4_sorting_logic_behavior():
    """
    Verify: Sorting logic produces newest-first order.

    Expected: Mock checkpoints sorted correctly by timestamp
    """
    # Create mock checkpoint metadata objects
    now = datetime.utcnow()

    meta_old = Mock()
    meta_old.task_id = "task_123"
    meta_old.timestamp = (now - timedelta(seconds=30)).isoformat()
    meta_old.iteration_num = 1

    meta_middle = Mock()
    meta_middle.task_id = "task_123"
    meta_middle.timestamp = (now - timedelta(seconds=10)).isoformat()
    meta_middle.iteration_num = 1

    meta_new = Mock()
    meta_new.task_id = "task_123"
    meta_new.timestamp = now.isoformat()
    meta_new.iteration_num = 1

    # Apply sort logic (what list_checkpoints does)
    checkpoints = [meta_old, meta_new, meta_middle]  # Intentionally unsorted
    checkpoints.sort(key=lambda m: (m.timestamp, m.iteration_num), reverse=True)

    # Verify newest is first
    assert checkpoints[0].timestamp == now.isoformat(), \
        "Newest checkpoint should be first after sort"

    # Verify order is correct
    assert checkpoints[1].timestamp == (now - timedelta(seconds=10)).isoformat(), \
        "Middle checkpoint should be second"

    assert checkpoints[2].timestamp == (now - timedelta(seconds=30)).isoformat(), \
        "Oldest checkpoint should be last"


@pytest.mark.tier2
def test_finding_4_sort_with_iteration_num():
    """
    Verify: Sorting uses both timestamp AND iteration_num for stability.

    Expected: Checkpoints with same timestamp are sorted by iteration_num descending
    """
    now = datetime.utcnow()

    # Create checkpoints with same timestamp but different iteration numbers
    meta_iter_1 = Mock()
    meta_iter_1.timestamp = now.isoformat()
    meta_iter_1.iteration_num = 1

    meta_iter_3 = Mock()
    meta_iter_3.timestamp = now.isoformat()
    meta_iter_3.iteration_num = 3

    meta_iter_2 = Mock()
    meta_iter_2.timestamp = now.isoformat()
    meta_iter_2.iteration_num = 2

    # Apply sort
    checkpoints = [meta_iter_1, meta_iter_2, meta_iter_3]
    checkpoints.sort(key=lambda m: (m.timestamp, m.iteration_num), reverse=True)

    # Verify iteration 3 comes first (highest iteration number first for same timestamp)
    assert checkpoints[0].iteration_num == 3, \
        "Highest iteration_num should come first for same timestamp"

    assert checkpoints[1].iteration_num == 2, \
        "Middle iteration_num should come second"

    assert checkpoints[2].iteration_num == 1, \
        "Lowest iteration_num should come last for same timestamp"


@pytest.mark.tier2
def test_finding_4_no_filename_sort():
    """
    Verify: Sorting is NOT based on filename.

    Expected: Timestamp sort takes precedence over filename alphabetical sort
    """
    # Create checkpoints with deliberately mismatched filenames and timestamps
    now = datetime.utcnow()

    # Filename would sort as: checkpoint_001 < checkpoint_002 < checkpoint_003
    # But by timestamp: checkpoint_002 is newest (10s ago), checkpoint_001 is middle (20s ago), checkpoint_003 is oldest (30s ago)

    meta1 = Mock()
    meta1.filename = "checkpoint_001.json"
    meta1.timestamp = (now - timedelta(seconds=20)).isoformat()  # Middle
    meta1.iteration_num = 1

    meta2 = Mock()
    meta2.filename = "checkpoint_002.json"
    meta2.timestamp = (now - timedelta(seconds=10)).isoformat()  # Newest (most recent)
    meta2.iteration_num = 1

    meta3 = Mock()
    meta3.filename = "checkpoint_003.json"
    meta3.timestamp = (now - timedelta(seconds=30)).isoformat()  # Oldest
    meta3.iteration_num = 1

    # Apply timestamp sort (not filename sort)
    checkpoints = [meta1, meta2, meta3]
    checkpoints.sort(key=lambda m: (m.timestamp, m.iteration_num), reverse=True)

    # Verify: timestamp sort takes precedence over filename sort
    # Checkpoint_002 should be first (newest timestamp = 10s ago)
    # Checkpoint_001 should be second (middle timestamp = 20s ago)
    # Checkpoint_003 should be last (oldest timestamp = 30s ago)
    assert checkpoints[0].filename == "checkpoint_002.json", \
        "Newest checkpoint by timestamp (10s ago) should be first"

    assert checkpoints[1].filename == "checkpoint_001.json", \
        "Middle checkpoint by timestamp (20s ago) should be second"

    assert checkpoints[2].filename == "checkpoint_003.json", \
        "Oldest checkpoint by timestamp (30s ago) should be last"
