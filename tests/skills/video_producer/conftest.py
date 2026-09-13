"""Pytest fixtures for Video Producer Skill 2.0 tests"""

import pytest
from video_producer.maestro import MaestroOrchestrator, VideoJob, VideoJobPhase
from video_producer.worker_manager import VideoProductionPipeline


@pytest.fixture
def maestro():
    """Create fresh Maestro Orchestrator instance"""
    return MaestroOrchestrator()


@pytest.fixture
def pipeline():
    """Create fresh VideoProductionPipeline instance"""
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
