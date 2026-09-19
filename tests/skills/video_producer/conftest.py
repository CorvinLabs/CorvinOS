"""Pytest fixtures for Video Producer Skill 2.0 tests"""

import pytest
import sys
from pathlib import Path

# Add video_producer to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core/skills/video_producer"))

try:
    from maestro import MaestroOrchestrator, VideoJob, VideoJobPhase
    from worker_manager import VideoProductionPipeline
except ImportError:
    # Fallback for missing modules
    MaestroOrchestrator = None
    VideoJob = None
    VideoJobPhase = None
    VideoProductionPipeline = None


@pytest.fixture
def maestro():
    """Create fresh Maestro Orchestrator instance"""
    if MaestroOrchestrator is None:
        pytest.skip("MaestroOrchestrator module not available")
    return MaestroOrchestrator()


@pytest.fixture
def pipeline():
    """Create fresh VideoProductionPipeline instance"""
    if VideoProductionPipeline is None:
        pytest.skip("VideoProductionPipeline module not available")
    return VideoProductionPipeline()


@pytest.fixture
def sample_job(maestro):
    """Create sample video job"""
    job_id = maestro.create_job(
        topic="What is CorvinOS?",
        duration=60,
        audience="beginners",
        narration=[
            "CorvinOS is an open-source operating system.",
            "It provides security and compliance features.",
            "It supports multi-tenancy.",
        ],
    )
    return maestro.get_job(job_id)


@pytest.fixture
def sample_narration():
    """Sample narration for testing"""
    return [
        "CorvinOS is an open-source operating system.",
        "It provides security and compliance features.",
        "It supports multi-tenancy and GDPR compliance.",
        "The system is designed for enterprise use.",
    ]
