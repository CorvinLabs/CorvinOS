"""Data models for DataHub Unified (Phase 1+2)."""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from enum import Enum
import json


class DataSourceType(str, Enum):
    """Supported data sources."""
    CSV = "csv"
    JSON = "json"
    SQL = "sql"
    API = "api"
    PARQUET = "parquet"


class CreationType(str, Enum):
    """What to create from data."""
    SKILL = "skill"
    TOOL = "tool"
    DATASET = "dataset"
    PIPELINE = "pipeline"


@dataclass
class DataIngestion:
    """Stage 1: Ingest data from source."""
    source_type: DataSourceType
    source_path: str
    sample_rows: int = 100
    infer_schema: bool = True

    def validate(self) -> bool:
        """Validate ingestion config."""
        return bool(self.source_path and self.sample_rows > 0)


@dataclass
class CreationRequest:
    """Stage 2-7: Request to create artifact from data."""
    data: DataIngestion
    creation_type: CreationType
    name: str
    description: str

    # Constraints
    complexity: str = "medium"  # low, medium, high
    fail_closed: bool = True

    def to_dict(self) -> dict:
        return {
            "data": {
                "source_type": self.data.source_type.value,
                "source_path": self.data.source_path,
                "sample_rows": self.data.sample_rows,
            },
            "creation_type": self.creation_type.value,
            "name": self.name,
            "description": self.description,
            "complexity": self.complexity,
            "fail_closed": self.fail_closed,
        }


@dataclass
class CreationResult:
    """Stage 8: Result of creation."""
    success: bool
    artifact_name: str
    artifact_type: CreationType
    artifact_body: str

    # Metadata
    generated_at: str = ""
    test_count: int = 0
    validation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "artifact_name": self.artifact_name,
            "artifact_type": self.artifact_type.value,
            "artifact_body_length": len(self.artifact_body),
            "test_count": self.test_count,
            "validation_errors": self.validation_errors,
        }
