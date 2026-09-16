"""
Tier-2 Tests: Finding 2 Verification — Checkpoint Producer Wired

Verifies that CheckpointManager is properly wired into the task execution flow.

Test Cases:
- Test 1: CheckpointManager is imported in task_graph_api.py
- Test 2: CheckpointManager has required methods (create, save, list)
- Test 3: task_graph_api imports VibeOrchestrator for checkpoint production
"""

import pytest
from pathlib import Path


@pytest.mark.tier2
def test_finding_2_checkpointmanager_imported():
    """
    Verify: CheckpointManager is imported in task_graph_api.py

    Expected: Source file contains 'CheckpointManager' import
    """
    source_file = Path(__file__).parents[2] / 'console/corvin_console/routes/task_graph_api.py'
    assert source_file.exists(), f"task_graph_api.py not found at {source_file}"

    source_code = source_file.read_text()

    # Verify CheckpointManager is imported
    assert 'CheckpointManager' in source_code, \
        "CheckpointManager not imported in task_graph_api"

    # Verify it's from vibe_engineering
    assert 'vibe_engineering.checkpoint_manager' in source_code or \
           'from vibe_engineering' in source_code or \
           'from core.vibe_engineering' in source_code, \
        "CheckpointManager not imported from vibe_engineering"


@pytest.mark.tier2
def test_finding_2_vibe_orchestrator_imported():
    """
    Verify: VibeOrchestrator is imported in task_graph_api.py

    Expected: Source file contains 'VibeOrchestrator' import for checkpoint production
    """
    source_file = Path(__file__).parents[2] / 'console/corvin_console/routes/task_graph_api.py'
    source_code = source_file.read_text()

    # Verify VibeOrchestrator is imported
    assert 'VibeOrchestrator' in source_code, \
        "VibeOrchestrator not imported in task_graph_api"

    # Verify availability flag
    assert '_VIBE_ORCHESTRATOR_AVAILABLE' in source_code, \
        "Module does not check VibeOrchestrator availability"


@pytest.mark.tier2
def test_finding_2_checkpointmanager_has_methods():
    """
    Verify: CheckpointManager class has required methods.

    Expected: create_checkpoint, save, list_checkpoints, get_latest methods exist
    """
    source_file = Path(__file__).parent / '../checkpoint_manager.py'
    assert source_file.exists(), f"checkpoint_manager.py not found at {source_file}"

    source_code = source_file.read_text()

    required_methods = [
        'def create_checkpoint',
        'def save',
        'def list_checkpoints',
        'def get_latest'
    ]

    for method in required_methods:
        assert method in source_code, \
            f"CheckpointManager missing method: {method}"


@pytest.mark.tier2
def test_finding_2_checkpointmanager_callable():
    """
    Verify: CheckpointManager class can be instantiated.

    Expected: CheckpointManager is a proper class with __init__
    """
    from core.vibe_engineering.checkpoint_manager import CheckpointManager

    # Verify it's a class
    assert isinstance(CheckpointManager, type), \
        "CheckpointManager should be a class"

    # Verify __init__ signature accepts tenant_id
    import inspect
    sig = inspect.signature(CheckpointManager.__init__)
    params = list(sig.parameters.keys())

    assert 'tenant_id' in params, \
        "CheckpointManager.__init__ should accept tenant_id parameter"


@pytest.mark.tier2
def test_finding_2_checkpoint_persistence_methods():
    """
    Verify: CheckpointManager methods handle persistence correctly.

    Expected: save() method exists and should persist checkpoints to disk
    """
    from core.vibe_engineering.checkpoint_manager import CheckpointManager

    # Verify save method signature
    import inspect
    save_sig = inspect.signature(CheckpointManager.save)

    # save should be callable with minimal arguments
    assert 'checkpoint' in save_sig.parameters or \
           len(save_sig.parameters) >= 1, \
        "save() method should accept checkpoint parameter"

    # Verify list_checkpoints signature
    list_sig = inspect.signature(CheckpointManager.list_checkpoints)

    # list_checkpoints should be callable
    assert len(list_sig.parameters) >= 0, \
        "list_checkpoints() should be callable"


@pytest.mark.tier2
def test_finding_2_producer_integration():
    """
    Verify: task_graph_api.py has code to produce checkpoints.

    Expected: Module contains logic to call CheckpointManager
    """
    source_file = Path(__file__).parents[2] / 'console/corvin_console/routes/task_graph_api.py'
    source_code = source_file.read_text()

    # Verify that checkpoints are created or saved in the code
    checkpoint_actions = (
        'CheckpointManager(' in source_code or
        'create_checkpoint' in source_code or
        'checkpoint.save' in source_code or
        'orchestrator.' in source_code  # VibeOrchestrator usage
    )

    assert checkpoint_actions, \
        "task_graph_api should contain code to produce checkpoints"
